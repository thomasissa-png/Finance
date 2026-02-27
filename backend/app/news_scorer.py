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

Univers de 42 actifs surveilles :
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

- M&A (FUSIONS/ACQUISITIONS) : DISTINGUER RUMEUR vs CONFIRMATION.
  - Rumeur non confirmee / "talks" / "in discussions" → transmission_delay 40-60
    (le marche n'a pas encore price car incertain)
  - Deal confirme / "announces acquisition" / "agrees to buy" → transmission_delay ≤ 10,
    market_awareness ≥ 85 (deja price en pre-market par les algos)

- CENTRAL BANK : DISTINGUER DECISION vs DISCOURS.
  - Decision de taux (rate decision) = deja price par algos HFT → transmission_delay ≤ 5
  - Discours president (Fed Chair, ECB President) = signal fort → transmission_delay 20-40
  - Discours secondaire (regional Fed, membre ECB non-president) → transmission_delay 40-60

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

    Fully sequential — no ThreadPoolExecutor. Replit kills the process when
    too many threads exist, even with low max_workers, because of zombie threads
    from prior collection phases (shutdown(wait=False) doesn't join them).
    Sequential is ~20s slower but guarantees 0 extra threads.
    Each call is wrapped in try/except — market context is nice-to-have, not critical.
    """
    context = {"vix": None, "regime": "normal", "indices": {}, "trends": {}}

    # VIX
    try:
        data = yf.Ticker("^VIX").history(period="2d")
        if not data.empty:
            vix_val = round(float(data["Close"].iloc[-1]), 1)
            context["vix"] = vix_val
            if vix_val >= 30:
                context["regime"] = "stress"
            elif vix_val >= 20:
                context["regime"] = "elevated"
            elif vix_val <= 13:
                context["regime"] = "calm"
    except Exception as exc:
        logger.warning("Failed to fetch VIX: %s", exc)

    # Index changes
    for ticker, name in [("^GSPC", "S&P500"), ("^FCHI", "CAC40"), ("^DJI", "DowJones")]:
        try:
            data = yf.Ticker(ticker).history(period="2d")
            if len(data) >= 2:
                prev = float(data["Close"].iloc[-2])
                curr = float(data["Close"].iloc[-1])
                context["indices"][name] = round((curr - prev) / prev * 100, 2)
        except Exception as exc:
            logger.debug("Failed to fetch index %s: %s", name, exc)

    # Trends
    for ticker, name in [("^GSPC", "S&P500"), ("^FCHI", "CAC40"), ("GC=F", "Or"), ("CL=F", "WTI"), ("EURUSD=X", "EURUSD")]:
        try:
            data = yf.Ticker(ticker).history(period="25d")
            if len(data) >= 20:
                close_now = float(data["Close"].iloc[-1])
                close_5d = float(data["Close"].iloc[-5])
                close_20d = float(data["Close"].iloc[0])
                trend_5d = "haussier" if close_now > close_5d * 1.005 else ("baissier" if close_now < close_5d * 0.995 else "neutre")
                trend_20d = "haussier" if close_now > close_20d * 1.01 else ("baissier" if close_now < close_20d * 0.99 else "neutre")
                context["trends"][name] = {"5d": trend_5d, "20d": trend_20d}
        except Exception as exc:
            logger.debug("Failed to fetch trend %s: %s", name, exc)

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
                max_tokens=16384,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
                tools=[SCORING_TOOL],
                tool_choice={"type": "tool", "name": "submit_news_scores"},
                timeout=90.0,  # 90s timeout — 50 items ~30s processing, margin for API queueing
            )

            # Detect truncation — if max_tokens was hit, scores are likely incomplete
            if response.stop_reason == "max_tokens":
                logger.warning("Claude response truncated (max_tokens hit, attempt %d) — retrying", attempt + 1)
                if attempt < max_retries:
                    time.sleep(2 ** attempt)
                    continue

            # (#9) Extract structured tool_use response
            for block in response.content:
                if block.type == "tool_use" and block.name == "submit_news_scores":
                    scores = block.input.get("scores", [])
                    if not scores:
                        logger.warning("Claude returned tool_use with empty scores array (attempt %d, stop_reason=%s)",
                                       attempt + 1, response.stop_reason)
                    return scores

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
            # Re-raise after all retries so callers can surface the error
            raise

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
        elif news_cat == "central_bank_subtle":
            # Stratify: rate decision vs president speech vs secondary official
            title_lower = item.title.lower()
            reasoning_lower = entry.get("reasoning", "").lower()
            combined = title_lower + " " + reasoning_lower
            _is_rate_decision = any(kw in combined for kw in [
                "rate decision", "taux directeur", "interest rate",
                "rate unchanged", "rate hike", "rate cut",
                "holds rates", "raises rates", "cuts rates",
            ])
            _is_major_speaker = any(kw in combined for kw in [
                "fed chair", "powell", "lagarde", "ecb president",
                "boe governor", "bailey", "president de la bce",
            ])
            if _is_rate_decision:
                # Rate decisions = priced by HFT in microseconds
                if transmission_delay > 10:
                    logger.info("Central bank rate-decision hard-cap: forcing transmission_delay %d -> 5 for '%s'",
                                transmission_delay, item.title[:60])
                    transmission_delay = 5
                    market_awareness = max(market_awareness, 95)
            elif _is_major_speaker and transmission_delay > 40:
                # Major speaker (Powell, Lagarde) — widely followed
                logger.info("Central bank major-speech hard-cap: forcing transmission_delay %d -> 25 for '%s'",
                            transmission_delay, item.title[:60])
                transmission_delay = 25
                market_awareness = max(market_awareness, 70)
            elif transmission_delay > 60:
                # Secondary official — less attention, more edge
                logger.info("Central bank secondary hard-cap: forcing transmission_delay %d -> 45 for '%s'",
                            transmission_delay, item.title[:60])
                transmission_delay = 45
                market_awareness = max(market_awareness, 50)
        elif news_cat == "m_a":
            # Distinguish M&A rumor (high edge) vs confirmed deal (zero edge)
            title_lower = item.title.lower()
            reasoning_lower = entry.get("reasoning", "").lower()
            combined = title_lower + " " + reasoning_lower
            _is_confirmed = any(kw in combined for kw in [
                "confirms", "confirmed", "announces acquisition", "agrees to buy",
                "agrees to acquire", "completed acquisition", "merger approved",
                "deal closed", "takeover complete", "annonce l'acquisition",
            ])
            if _is_confirmed:
                # Confirmed M&A = already priced in pre-market (treat like earnings)
                if transmission_delay > 10:
                    logger.info("M&A confirmed hard-cap: forcing transmission_delay %d -> 5 for '%s'",
                                transmission_delay, item.title[:60])
                    transmission_delay = 5
                    market_awareness = max(market_awareness, 90)
            elif transmission_delay > 50:
                # M&A rumor — cap less aggressively (rumors still have edge)
                logger.info("M&A rumor hard-cap: forcing transmission_delay %d -> 40 for '%s'",
                            transmission_delay, item.title[:60])
                transmission_delay = 40
                market_awareness = max(market_awareness, 50)

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

    if scored and len(scored) < len(batch_items):
        logger.warning("Claude scored only %d/%d items in batch (possible truncation or index mismatch)",
                       len(scored), len(batch_items))

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
