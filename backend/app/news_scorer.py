"""Scores news headlines using Claude API with tool_use for structured output."""

import json
import logging
import os
import time
from datetime import datetime, timezone

import anthropic
import yfinance as yf

from .config import ASSETS, NEWS_CATEGORIES, NEWS_FRESHNESS_PEAK_HOURS, NEWS_MAX_AGE_HOURS
from .models import Direction, NewsItem, ScanType, ScoredNews

logger = logging.getLogger(__name__)

TICKER_LIST = ", ".join(f"{a.ticker} ({a.name})" for a in ASSETS)
CATEGORY_LIST = ", ".join(NEWS_CATEGORIES)

SYSTEM_PROMPT = f"""Tu es un analyste expert en news trading avec 20 ans d'experience. Tu evalues l'impact de
nouvelles financieres sur des actifs specifiques pour du trading intraday.

Univers de 49 actifs surveilles :
{TICKER_LIST}

Pour chaque news, tu dois evaluer :
1. **surprise** (0-100) : A quel point cette information est inattendue par le marche.
   - 0 = totalement anticipe / sans impact
   - 50 = moderement surprenant
   - 100 = choc total, cygne noir
2. **directional_clarity** (0-100) : A quel point la direction de l'impact est claire.
   - 0 = ambigu, impact dans les deux sens possibles
   - 100 = direction absolument evidente (hausse OU baisse)
3. **direction** : "LONG" (haussier pour l'actif), "SHORT" (baissier), ou "NEUTRAL"
4. **impacted_tickers** : liste des tickers de notre univers les plus directement impactes
5. **news_category** : categorie de la news parmi : {CATEGORY_LIST}
6. **reasoning** : explication en 1-2 phrases de ton analyse

IMPORTANT :
- Evalue uniquement en fonction de l'impact COURT TERME (intraday, quelques heures)
- Une news attendue (ex: hausse de taux deja pricee) = surprise faible meme si importante
- Privilegie les actifs avec l'impact le plus DIRECT (pas indirect ou vague)
- Si la news ne concerne clairement aucun de nos actifs, mets surprise=0
- Tiens compte du CONTEXTE DE MARCHE fourni pour ponderer ton analyse (VIX, tendances)"""

# (#9) Tool definition for structured output
SCORING_TOOL = {
    "name": "submit_news_scores",
    "description": "Submit the analysis scores for each news headline",
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
                        "direction": {"type": "string", "enum": ["LONG", "SHORT", "NEUTRAL"]},
                        "impacted_tickers": {"type": "array", "items": {"type": "string"}},
                        "news_category": {"type": "string", "enum": NEWS_CATEGORIES},
                        "reasoning": {"type": "string"},
                    },
                    "required": ["index", "surprise", "directional_clarity", "direction",
                                 "impacted_tickers", "news_category", "reasoning"],
                },
            },
        },
        "required": ["scores"],
    },
}


def _compute_freshness(published: datetime | None) -> int:
    """Score freshness: 100 if < peak hours, 0 if > max age, linear between."""
    if published is None:
        return 50  # Unknown age — neutral score

    now = datetime.now(timezone.utc)
    age_hours = (now - published).total_seconds() / 3600

    if age_hours <= 0:
        return 100
    if age_hours <= NEWS_FRESHNESS_PEAK_HOURS:
        return 100
    if age_hours >= NEWS_MAX_AGE_HOURS:
        return 0

    # Linear decay between peak and max
    remaining = NEWS_MAX_AGE_HOURS - NEWS_FRESHNESS_PEAK_HOURS
    return int(100 * (1 - (age_hours - NEWS_FRESHNESS_PEAK_HOURS) / remaining))


def _fetch_market_context() -> dict:
    """Fetch current market context: VIX, major index changes, regime (#5, #8)."""
    context = {"vix": None, "regime": "normal", "indices": {}, "trends": {}}
    try:
        # VIX
        vix_data = yf.Ticker("^VIX").history(period="2d")
        if not vix_data.empty:
            context["vix"] = round(float(vix_data["Close"].iloc[-1]), 1)
            if context["vix"] >= 30:
                context["regime"] = "stress"
            elif context["vix"] >= 20:
                context["regime"] = "elevated"
            elif context["vix"] <= 13:
                context["regime"] = "calm"

        # Major index daily changes
        for ticker, name in [("^GSPC", "S&P500"), ("^FCHI", "CAC40"), ("^DJI", "DowJones")]:
            try:
                data = yf.Ticker(ticker).history(period="2d")
                if len(data) >= 2:
                    prev = float(data["Close"].iloc[-2])
                    curr = float(data["Close"].iloc[-1])
                    context["indices"][name] = round((curr - prev) / prev * 100, 2)
            except Exception:
                pass

        # (#8) Multi-timeframe trends (5d and 20d) for major assets
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
            except Exception:
                pass

    except Exception as exc:
        logger.warning("Failed to fetch market context: %s", exc)

    return context


def _build_context_string(market_ctx: dict, scan_type: ScanType) -> str:
    """Build the context string for Claude prompt (#5, #8)."""
    parts = []

    if scan_type == ScanType.EUROPE:
        parts.append("Scan EUROPE 07h50 — focus ouverture europeenne, recap session asiatique")
    else:
        parts.append("Scan US 14h30 — focus ouverture americaine, bilan session europeenne")

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


def score_news_batch(
    news_items: list[NewsItem],
    scan_type: ScanType,
) -> tuple[list[ScoredNews], dict]:
    """Send a batch of news headlines to Claude for scoring.

    Returns (scored_news, market_context).
    """
    if not news_items:
        return [], {}

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not set — cannot score news")
        return [], {}

    client = anthropic.Anthropic(api_key=api_key)

    # (#5) Fetch market context
    market_ctx = _fetch_market_context()

    # Build the headlines payload
    headlines = []
    for i, item in enumerate(news_items):
        age_str = ""
        if item.published:
            age_h = (datetime.now(timezone.utc) - item.published).total_seconds() / 3600
            age_str = f" [il y a {age_h:.1f}h]"
        tickers_str = f" (lie a: {', '.join(item.related_tickers)})" if item.related_tickers else ""
        headlines.append(f"{i+1}. {item.title}{tickers_str}{age_str}")

    session_context = _build_context_string(market_ctx, scan_type)

    scores = _call_claude_with_retry(client, headlines, session_context)

    if not scores:
        return [], market_ctx

    # Map scores back to ScoredNews objects
    scored: list[ScoredNews] = []
    for entry in scores:
        idx = entry.get("index", 0) - 1
        if idx < 0 or idx >= len(news_items):
            continue

        item = news_items[idx]
        freshness = _compute_freshness(item.published)

        try:
            direction = Direction(entry.get("direction", "NEUTRAL"))
        except ValueError:
            direction = Direction.NEUTRAL

        news_cat = entry.get("news_category", "other")
        if news_cat not in NEWS_CATEGORIES:
            news_cat = "other"

        scored.append(ScoredNews(
            news=item,
            surprise=max(0, min(100, entry.get("surprise", 0))),
            freshness=freshness,
            directional_clarity=max(0, min(100, entry.get("directional_clarity", 0))),
            direction=direction,
            impacted_tickers=entry.get("impacted_tickers", []),
            reasoning=entry.get("reasoning", ""),
            news_category=news_cat,
        ))

    scored.sort(key=lambda s: s.total_score, reverse=True)
    logger.info("Scored %d/%d news items (VIX=%s, regime=%s)",
                len(scored), len(news_items),
                market_ctx.get("vix", "N/A"), market_ctx.get("regime", "N/A"))
    return scored, market_ctx
