"""Scores news headlines using Claude API with tool_use for structured output."""

import json
import logging
import os
import time
from datetime import datetime, timezone

import anthropic
import yfinance as yf

from .config import (
    ASSETS,
    CATEGORY_SCORE_MULTIPLIERS,
    CHAIN_REACTIONS,
    NEWS_CATEGORIES,
    NEWS_FRESHNESS_PEAK_HOURS,
    NEWS_MAX_AGE_HOURS,
)
from .economic_calendar import get_events_context
from .learning import build_performance_summary
from .models import ChainReaction, Direction, NewsItem, ScanType, ScoredNews

logger = logging.getLogger(__name__)

TICKER_LIST = ", ".join(f"{a.ticker} ({a.name})" for a in ASSETS)
CATEGORY_LIST = ", ".join(NEWS_CATEGORIES)

SYSTEM_PROMPT = f"""Tu es un speculateur expert en news trading depuis 20 ans, specialise dans la detection
de DISLOCATIONS NON ENCORE PRICEES par le marche. Ton edge, c'est d'identifier les news qui ne sont
PAS ENCORE integrees dans les cours — les signaux en avance de phase.

Univers de 49 actifs surveilles :
{TICKER_LIST}

Pour chaque news, tu dois evaluer :

1. **surprise** (0-100) : A quel point cette information est inattendue par le marche.
   - 0 = totalement anticipe / consensus / sans impact
   - 50 = moderement surprenant
   - 100 = choc total, cygne noir

2. **directional_clarity** (0-100) : A quel point la direction de l'impact est claire.
   - 0 = ambigu, impact dans les deux sens possibles
   - 100 = direction absolument evidente

3. **transmission_delay** (0-100) : CRITIQUE — Combien de TEMPS avant que le marche price
   pleinement cette information ?
   - 0 = deja price (earnings post-publication, decision de taux attendue, NFP conforme)
   - 20 = les algos HFT ont deja reagi en millisecondes (CPI, NFP, FOMC decision)
   - 50 = quelques acteurs ont vu, le gros du marche pas encore (discours secondaire BCE)
   - 80 = information specialisee, seuls les experts du secteur ont compris l'impact
         (rapport USDA sur les stocks de ble, alerte secheresse NOAA sur le Midwest)
   - 100 = personne n'a encore fait le lien avec les actifs concernes
         (gel au Bresil repere par bulletin meteo local → impact cafe/sucre dans 6-12h)

4. **market_awareness** (0-100) : Quel % des participants a DEJA VU cette information ?
   - 0 = personne (bulletin meteo local, rapport technique USDA)
   - 30 = les specialistes du secteur
   - 50 = les desk institutionnels
   - 80 = tous les terminaux Bloomberg
   - 100 = tout le monde (headline CNN/BBC, trending sur Twitter)

5. **direction** : "LONG", "SHORT", ou "NEUTRAL"

6. **impacted_tickers** : tickers directement impactes de notre univers

7. **news_category** : categorie parmi : {CATEGORY_LIST}

8. **reasoning** : explication en 1-2 phrases — INCLURE l'estimation du delai de pricing

REGLES CRUCIALES — PHILOSOPHIE DU SYSTEME :

- Notre edge est sur les signaux EN AVANCE DE PHASE. On cherche la news que le marche
  n'a PAS ENCORE pricee, pas celle que tout le monde commente.

- EARNINGS / RESULTATS D'ENTREPRISE : TOUJOURS mettre transmission_delay ≤ 10 et
  market_awareness ≥ 90. Ces infos sont pricees en pre-market/after-hours par les algos.
  On n'a ZERO edge dessus sauf profit warning inattendu.

- DECISIONS DE TAUX / NFP / CPI : transmission_delay = 0-5, market_awareness = 100.
  Les algos reagissent en microsecondes. Ne jamais surestimer notre avantage.

- SIGNAUX PHYSIQUES (meteo, shipping, stocks commodities) : Souvent
  transmission_delay 60-100 car les traders de commodities physiques sont lents
  a repercuter sur les futures.

- GEOPOLITIQUE : Evaluer honnêtement — une declaration officielle = deja vue.
  Un mouvement militaire capte par OSINT = potentiellement en avance de phase.

- EFFETS DE SECOND ORDRE : Si une news impacte un actif A, pense aux impacts
  indirects sur B et C (ex: gel bresilien → cafe + sucre car memes planteurs).
  Mets les tickers de second ordre dans impacted_tickers aussi.

- Tiens compte du CONTEXTE DE MARCHE fourni (VIX, tendances) pour ta calibration."""

# (#9) Tool definition for structured output — with edge-detection fields
SCORING_TOOL = {
    "name": "submit_news_scores",
    "description": "Submit the analysis scores for each news headline, including edge-detection metrics",
    "input_schema": {
        "type": "object",
        "properties": {
            "scores": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer", "description": "1-based index of the headline"},
                        "surprise": {"type": "integer", "minimum": 0, "maximum": 100},
                        "directional_clarity": {"type": "integer", "minimum": 0, "maximum": 100},
                        "transmission_delay": {
                            "type": "integer", "minimum": 0, "maximum": 100,
                            "description": "How long before the market fully prices this? 0=already priced, 100=nobody made the link yet",
                        },
                        "market_awareness": {
                            "type": "integer", "minimum": 0, "maximum": 100,
                            "description": "What % of market participants have already seen this? 0=nobody, 100=everyone",
                        },
                        "direction": {"type": "string", "enum": ["LONG", "SHORT", "NEUTRAL"]},
                        "impacted_tickers": {"type": "array", "items": {"type": "string"}},
                        "news_category": {"type": "string", "enum": NEWS_CATEGORIES},
                        "reasoning": {"type": "string"},
                    },
                    "required": ["index", "surprise", "directional_clarity",
                                 "transmission_delay", "market_awareness",
                                 "direction", "impacted_tickers", "news_category", "reasoning"],
                },
            },
        },
        "required": ["scores"],
    },
}


def _compute_freshness(published: datetime | None) -> int:
    """Score freshness: 100 if < peak hours, floor of 5 for old news (not 0).

    Old news that happens to have high transmission_delay (physical signals)
    should NOT be crushed to 0 — the edge_factor handles that separately.
    Floor of 5 preserves residual value for slow-moving dislocations.
    """
    if published is None:
        return 50  # Unknown age — neutral score

    now = datetime.now(timezone.utc)
    age_hours = (now - published).total_seconds() / 3600

    if age_hours <= 0:
        return 100
    if age_hours <= NEWS_FRESHNESS_PEAK_HOURS:
        return 100
    if age_hours >= NEWS_MAX_AGE_HOURS:
        return 5  # Floor: slow signals retain residual value

    # Linear decay between peak and max, floor at 5
    remaining = NEWS_MAX_AGE_HOURS - NEWS_FRESHNESS_PEAK_HOURS
    raw = 100 * (1 - (age_hours - NEWS_FRESHNESS_PEAK_HOURS) / remaining)
    return max(5, int(raw))


def _fetch_market_context() -> dict:
    """Fetch current market context: VIX, major index changes, regime (#5, #8).

    All yfinance calls are parallelized to minimize latency.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    context = {"vix": None, "regime": "normal", "indices": {}, "trends": {}}

    def _fetch_vix():
        data = yf.Ticker("^VIX").history(period="2d")
        if not data.empty:
            return round(float(data["Close"].iloc[-1]), 1)
        return None

    def _fetch_index_change(ticker):
        data = yf.Ticker(ticker).history(period="2d")
        if len(data) >= 2:
            prev = float(data["Close"].iloc[-2])
            curr = float(data["Close"].iloc[-1])
            return round((curr - prev) / prev * 100, 2)
        return None

    def _fetch_trend(ticker):
        data = yf.Ticker(ticker).history(period="25d")
        if len(data) >= 20:
            close_now = float(data["Close"].iloc[-1])
            close_5d = float(data["Close"].iloc[-5])
            close_20d = float(data["Close"].iloc[0])
            trend_5d = "haussier" if close_now > close_5d * 1.005 else ("baissier" if close_now < close_5d * 0.995 else "neutre")
            trend_20d = "haussier" if close_now > close_20d * 1.01 else ("baissier" if close_now < close_20d * 0.99 else "neutre")
            return {"5d": trend_5d, "20d": trend_20d}
        return None

    try:
        indices = [("^GSPC", "S&P500"), ("^FCHI", "CAC40"), ("^DJI", "DowJones")]
        trends = [("^GSPC", "S&P500"), ("^FCHI", "CAC40"), ("GC=F", "Or"), ("CL=F", "WTI"), ("EURUSD=X", "EURUSD")]

        with ThreadPoolExecutor(max_workers=9) as executor:
            vix_future = executor.submit(_fetch_vix)
            idx_futures = {executor.submit(_fetch_index_change, t): name for t, name in indices}
            trend_futures = {executor.submit(_fetch_trend, t): name for t, name in trends}

            # VIX
            try:
                vix_val = vix_future.result(timeout=15)
                if vix_val is not None:
                    context["vix"] = vix_val
                    if vix_val >= 30:
                        context["regime"] = "stress"
                    elif vix_val >= 20:
                        context["regime"] = "elevated"
                    elif vix_val <= 13:
                        context["regime"] = "calm"
            except Exception:
                pass

            # Indices
            for future in as_completed(idx_futures, timeout=15):
                name = idx_futures[future]
                try:
                    val = future.result(timeout=1)
                    if val is not None:
                        context["indices"][name] = val
                except Exception:
                    pass

            # Trends
            for future in as_completed(trend_futures, timeout=15):
                name = trend_futures[future]
                try:
                    val = future.result(timeout=1)
                    if val is not None:
                        context["trends"][name] = val
                except Exception:
                    pass

    except Exception as exc:
        logger.warning("Failed to fetch market context: %s", exc)

    return context


def _build_context_string(market_ctx: dict, scan_type: ScanType) -> str:
    """Build the context string for Claude prompt (#5, #8)."""
    parts = []

    if scan_type == ScanType.EUROPE:
        parts.append("Scan EUROPE — focus marche europeen, recap session asiatique")
    else:
        parts.append("Scan US — focus marche americain, bilan session europeenne")

    # VIX & regime
    if market_ctx.get("vix"):
        parts.append(f"VIX: {market_ctx['vix']} (regime: {market_ctx['regime']})")

    # Index changes
    idx_parts = []
    for name, chg in market_ctx.get("indices", {}).items():
        idx_parts.append(f"{name}: {chg:+.2f}%")
    if idx_parts:
        parts.append("Indices du jour: " + ", ".join(idx_parts))

    # Trends (#8)
    trend_parts = []
    for name, trends in market_ctx.get("trends", {}).items():
        trend_parts.append(f"{name}: 5j={trends['5d']}, 20j={trends['20d']}")
    if trend_parts:
        parts.append("Tendances: " + ", ".join(trend_parts))

    # Economic calendar context
    cal_ctx = get_events_context()
    if cal_ctx:
        parts.append(cal_ctx)

    # P1-#1: Performance feedback loop — Claude sees its past results
    perf_summary = build_performance_summary()
    if perf_summary:
        parts.append(perf_summary)

    return " | ".join(parts)


def _call_claude_with_retry(client, headlines, session_context, max_retries=2):
    """Call Claude API with tool_use (#9) for structured output.

    Returns parsed scores list, or empty list on failure.
    """
    user_message = f"""Contexte : {session_context}

Voici {len(headlines)} headlines recentes. Analyse chacune et utilise l'outil submit_news_scores pour soumettre tes scores.

Headlines :
{chr(10).join(headlines)}"""

    for attempt in range(max_retries + 1):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
                tools=[SCORING_TOOL],
                tool_choice={"type": "tool", "name": "submit_news_scores"},
                timeout=90.0,  # 90s timeout — 50 items ~30s processing, margin for API queueing
            )

            # (#9) Extract structured tool_use response
            for block in response.content:
                if block.type == "tool_use" and block.name == "submit_news_scores":
                    return block.input.get("scores", [])

            # Fallback: try text-based JSON parsing if tool_use somehow not used
            for block in response.content:
                if hasattr(block, "text"):
                    raw_text = block.text.strip()
                    start = raw_text.index("[")
                    end = raw_text.rindex("]") + 1
                    return json.loads(raw_text[start:end])

            logger.error("Claude response had no tool_use or parseable JSON (attempt %d)", attempt + 1)
            if attempt < max_retries:
                time.sleep(2 ** attempt)
                continue
            return []

        except (json.JSONDecodeError, ValueError) as exc:
            logger.error("Claude scoring parse error (attempt %d): %s", attempt + 1, exc)
            if attempt < max_retries:
                time.sleep(2 ** attempt)
                continue
            return []
        except anthropic.APIError as exc:
            logger.error("Claude API error (attempt %d): %s", attempt + 1, exc)
            if attempt < max_retries:
                time.sleep(2 ** attempt)
                continue
            return []

    return []


# Max items per Claude API call — keeps processing under 30s per batch.
# Pre-filter in news_collector caps at 50, but this is a safety net
# in case items are injected from other sources (event triggers, etc.)
SCORING_BATCH_SIZE = 50


def score_news_batch(
    news_items: list[NewsItem],
    scan_type: ScanType,
) -> tuple[list[ScoredNews], dict]:
    """Send news headlines to Claude for scoring, with automatic batching.

    If more than SCORING_BATCH_SIZE items, splits into multiple API calls
    to avoid timeout. Each batch is scored independently.

    Returns (scored_news, market_context).
    """
    if not news_items:
        return [], {}

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not set — cannot score news")
        return [], {}

    # Warn about missing optional keys that significantly improve coverage
    if not os.environ.get("GNEWS_API_KEY"):
        logger.warning("GNEWS_API_KEY non configuree — 8 recherches ciblees (drought, oil, sanctions...) desactivees. "
                        "Source early-signal majeure manquante. Inscription gratuite: https://gnews.io/")
    if not os.environ.get("EIA_API_KEY"):
        logger.warning("EIA_API_KEY non configuree — donnees stocks petrole/gaz desactivees. "
                        "Inscription gratuite: https://www.eia.gov/opendata/register.php")

    client = anthropic.Anthropic(api_key=api_key)

    # (#5) Fetch market context
    market_ctx = _fetch_market_context()
    session_context = _build_context_string(market_ctx, scan_type)

    # Split into batches if needed (safety net — pre-filter should already cap at 50)
    if len(news_items) > SCORING_BATCH_SIZE:
        logger.info("Splitting %d items into batches of %d for Claude scoring",
                     len(news_items), SCORING_BATCH_SIZE)

    all_scored: list[ScoredNews] = []
    for batch_start in range(0, len(news_items), SCORING_BATCH_SIZE):
        batch_items = news_items[batch_start:batch_start + SCORING_BATCH_SIZE]
        batch_scored = _score_batch(client, batch_items, session_context, batch_start)
        all_scored.extend(batch_scored)

    all_scored.sort(key=lambda s: s.total_score, reverse=True)
    logger.info("Scored %d/%d news items (VIX=%s, regime=%s)",
                len(all_scored), len(news_items),
                market_ctx.get("vix", "N/A"), market_ctx.get("regime", "N/A"))
    return all_scored, market_ctx


def _score_batch(
    client: anthropic.Anthropic,
    batch_items: list[NewsItem],
    session_context: str,
    index_offset: int = 0,
) -> list[ScoredNews]:
    """Score a single batch of news items via Claude API.

    index_offset is the position of this batch within the full list
    (used to map scores back to the correct NewsItem).
    """
    # Build the headlines payload — include description when available for more context
    headlines = []
    for i, item in enumerate(batch_items):
        age_str = ""
        if item.published:
            age_h = (datetime.now(timezone.utc) - item.published).total_seconds() / 3600
            age_str = f" [il y a {age_h:.1f}h]"
        tickers_str = f" (lie a: {', '.join(item.related_tickers)})" if item.related_tickers else ""
        desc_str = f" | {item.description}" if item.description else ""
        headlines.append(f"{i+1}. {item.title}{desc_str}{tickers_str}{age_str}")

    scores = _call_claude_with_retry(client, headlines, session_context)

    if not scores:
        return []

    # Map scores back to ScoredNews objects
    scored: list[ScoredNews] = []
    for entry in scores:
        idx = entry.get("index", 0) - 1
        if idx < 0 or idx >= len(batch_items):
            continue

        item = batch_items[idx]
        freshness = _compute_freshness(item.published)

        try:
            direction = Direction(entry.get("direction", "NEUTRAL"))
        except ValueError:
            direction = Direction.NEUTRAL

        news_cat = entry.get("news_category", "other")
        if news_cat not in NEWS_CATEGORIES:
            news_cat = "other"

        # New edge-detection fields
        transmission_delay = max(0, min(100, entry.get("transmission_delay", 50)))
        market_awareness = max(0, min(100, entry.get("market_awareness", 50)))

        # Hard rejection: categories with unrealistic transmission_delay
        # These are priced quickly by algos — Claude sometimes overestimates delay
        if news_cat == "earnings" and transmission_delay > 15:
            logger.info("Earnings hard-cap: forcing transmission_delay %d -> 5 for '%s'",
                        transmission_delay, item.title[:60])
            transmission_delay = 5
            market_awareness = max(market_awareness, 90)
        elif news_cat == "macro" and transmission_delay > 20:
            logger.info("Macro hard-cap: forcing transmission_delay %d -> 10 for '%s'",
                        transmission_delay, item.title[:60])
            transmission_delay = 10
            market_awareness = max(market_awareness, 85)
        elif news_cat == "central_bank_subtle" and transmission_delay > 60:
            logger.info("Central bank hard-cap: forcing transmission_delay %d -> 40 for '%s'",
                        transmission_delay, item.title[:60])
            transmission_delay = 40
            market_awareness = max(market_awareness, 60)
        elif news_cat == "m_a" and transmission_delay > 50:
            logger.info("M&A hard-cap: forcing transmission_delay %d -> 30 for '%s'",
                        transmission_delay, item.title[:60])
            transmission_delay = 30
            market_awareness = max(market_awareness, 70)

        # Apply category score multiplier (edge priority)
        cat_mult = CATEGORY_SCORE_MULTIPLIERS.get(news_cat, 0.7)

        # Detect chain reactions for impacted tickers
        impacted = entry.get("impacted_tickers", [])
        chain_reactions = _detect_chain_reactions(impacted, direction)
        # Add second-order tickers to impacted list
        for cr in chain_reactions:
            if cr.ticker not in impacted:
                impacted.append(cr.ticker)

        scored.append(ScoredNews(
            news=item,
            surprise=max(0, min(100, entry.get("surprise", 0))),
            freshness=freshness,
            directional_clarity=max(0, min(100, entry.get("directional_clarity", 0))),
            transmission_delay=transmission_delay,
            market_awareness=market_awareness,
            direction=direction,
            impacted_tickers=impacted,
            reasoning=entry.get("reasoning", ""),
            news_category=news_cat,
            category_score_mult=cat_mult,
            chain_reactions=chain_reactions,
        ))

    return scored


def _detect_chain_reactions(impacted_tickers: list[str], direction: Direction) -> list[ChainReaction]:
    """Detect second-order impacts from chain reaction map.

    If news impacts CL=F (oil up), automatically flag TTE.PA (same direction),
    BZ=F (same direction), etc.
    """
    reactions: list[ChainReaction] = []
    seen = set(impacted_tickers)

    for ticker in impacted_tickers:
        chains = CHAIN_REACTIONS.get(ticker, [])
        for chain in chains:
            target = chain["ticker"]
            if target in seen:
                continue
            seen.add(target)

            # Determine direction for the chain reaction
            # NEUTRAL source → chain stays NEUTRAL (inverse of NEUTRAL is still NEUTRAL)
            if direction == Direction.NEUTRAL:
                cr_direction = Direction.NEUTRAL
            elif chain["direction"] == "same":
                cr_direction = direction
            elif chain["direction"] == "inverse":
                cr_direction = Direction.SHORT if direction == Direction.LONG else Direction.LONG
            else:
                cr_direction = Direction.NEUTRAL

            reactions.append(ChainReaction(
                ticker=target,
                direction=cr_direction,
                reason=chain["reason"],
                source_ticker=ticker,
            ))

    if reactions:
        logger.info("Chain reactions detected: %s",
                     ", ".join(f"{cr.source_ticker}->{cr.ticker}" for cr in reactions))
    return reactions
