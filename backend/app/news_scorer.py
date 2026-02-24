"""Scores news headlines using Claude API (Option B — LLM-assisted)."""

import json
import logging
import os
import time
from datetime import datetime, timezone

import anthropic

from .config import ASSETS, NEWS_CATEGORIES, NEWS_FRESHNESS_PEAK_HOURS, NEWS_MAX_AGE_HOURS
from .models import Direction, NewsItem, ScanType, ScoredNews

logger = logging.getLogger(__name__)

TICKER_LIST = ", ".join(f"{a.ticker} ({a.name})" for a in ASSETS)
CATEGORY_LIST = ", ".join(NEWS_CATEGORIES)

SYSTEM_PROMPT = f"""Tu es un analyste expert en news trading. Tu évalues l'impact de
nouvelles financières sur des actifs spécifiques pour du trading intraday.

Univers de 49 actifs surveillés :
{TICKER_LIST}

Pour chaque news, tu dois évaluer :
1. **surprise** (0-100) : À quel point cette information est inattendue par le marché.
   - 0 = totalement anticipé / sans impact
   - 50 = modérément surprenant
   - 100 = choc total, cygne noir
2. **directional_clarity** (0-100) : À quel point la direction de l'impact est claire.
   - 0 = ambigu, impact dans les deux sens possibles
   - 100 = direction absolument évidente (hausse OU baisse)
3. **direction** : "LONG" (haussier pour l'actif), "SHORT" (baissier), ou "NEUTRAL"
4. **impacted_tickers** : liste des tickers de notre univers les plus directement impactés
5. **news_category** : catégorie de la news parmi : {CATEGORY_LIST}
6. **reasoning** : explication en 1-2 phrases de ton analyse

IMPORTANT :
- Évalue uniquement en fonction de l'impact COURT TERME (intraday, quelques heures)
- Une news attendue (ex: hausse de taux déjà pricée) = surprise faible même si importante
- Privilégie les actifs avec l'impact le plus DIRECT (pas indirect ou vague)
- Si la news ne concerne clairement aucun de nos actifs, mets surprise=0"""


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


def _call_claude_with_retry(client, headlines, session_context, max_retries=2):
    """Call Claude API with retry and exponential backoff.

    Returns parsed JSON scores list, or empty list on failure.
    """
    user_message = f"""Contexte : {session_context}

Voici {len(headlines)} headlines récentes. Analyse chacune et renvoie un JSON array.

Headlines :
{chr(10).join(headlines)}

Réponds UNIQUEMENT avec un JSON array (pas de texte autour). Chaque élément :
{{"index": 1, "surprise": 0-100, "directional_clarity": 0-100, "direction": "LONG"|"SHORT"|"NEUTRAL", "impacted_tickers": ["TICKER1"], "news_category": "macro", "reasoning": "..."}}"""

    for attempt in range(max_retries + 1):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )

            raw_text = response.content[0].text.strip()
            start = raw_text.index("[")
            end = raw_text.rindex("]") + 1
            return json.loads(raw_text[start:end])

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
) -> list[ScoredNews]:
    """Send a batch of news headlines to Claude for scoring."""
    if not news_items:
        return []

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not set — cannot score news")
        return []

    client = anthropic.Anthropic(api_key=api_key)

    # Build the headlines payload
    headlines = []
    for i, item in enumerate(news_items):
        age_str = ""
        if item.published:
            age_h = (datetime.now(timezone.utc) - item.published).total_seconds() / 3600
            age_str = f" [il y a {age_h:.1f}h]"
        tickers_str = f" (lié à: {', '.join(item.related_tickers)})" if item.related_tickers else ""
        headlines.append(f"{i+1}. {item.title}{tickers_str}{age_str}")

    session_context = (
        "Scan EUROPE 07h50 — focus ouverture européenne, recap session asiatique"
        if scan_type == ScanType.EUROPE
        else "Scan US 14h30 — focus ouverture américaine, bilan session européenne"
    )

    scores = _call_claude_with_retry(client, headlines, session_context)

    if not scores:
        return []

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
    logger.info("Scored %d/%d news items", len(scored), len(news_items))
    return scored
