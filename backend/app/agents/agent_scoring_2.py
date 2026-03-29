"""Agent Scoring 2 — Scoring dédié au trend following (Équipe 2).

v8.0: Dedicated Claude API call (replaces heuristic re-scoring of Scoring 1).
- Own trend-specific Claude prompt focused on structural commodity impact
- NO edge_factor (transmission_delay/market_awareness) in scoring formula
  → trends don't need speed, they need structural impact assessment
- Filters for 4 trend tickers (HG=F, CC=F, KC=F, ZW=F) before calling Claude
- Publishes trend_scored on bus → Trader 2 consumes

Previous versions (v7.5 and earlier) consumed Scoring 1's output and applied
heuristic re-weighting. v8.0 is a full rewrite with its own Claude call,
producing higher quality scoring tailored to trend following.

Key differences vs Scoring 1:
- Scoring 1: evaluates intraday edge (transmission_delay × market_awareness)
- Scoring 2: evaluates structural trend impact (persistence, magnitude, supply/demand)
- Scoring 1 prompt: "speculateur expert en news trading"
- Scoring 2 prompt: "expert en tendances commodities physiques"
- Scoring 2 tool schema: 9 dimensions (no transmission_delay/market_awareness)
- Scoring 2 formula: surprise × clarity × magnitude × reliability × persistence × category × source_weight

Expertise incarnée :
- 15+ ans spéculation tendance commodities
- Comprend que l'impact structurel (sécheresse, gel, embargo) > timing d'edge
- Sait que les signaux s'accumulent dans une tendance
"""

import hashlib
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone

from .base import BaseAgent, AgentStatus
from ..config import CHAIN_REACTIONS  # S2-P4: Top-level import (was inside loop)

logger = logging.getLogger(__name__)

# ── Trend-specific category multipliers ──────────────────────────
# Used AFTER Claude scoring to weight the trend_score by category
TREND_CATEGORY_MULTS = {
    "weather":            2.0,   # Max impact on commodity supply (drought, frost, hurricane)
    "supply_chain":       1.8,   # Port closures, embargoes, shipping disruptions
    "commodity":          1.6,   # Direct commodity signals (inventories, production)
    "geopolitical":       1.3,   # Sanctions, conflicts, trade wars
    "regulatory":         1.2,   # Export bans, tariffs
    "sector":             0.8,   # Less relevant for commodity trends
    "other":              0.6,   # Low relevance
    "central_bank_subtle": 0.4,  # Minimal trend impact
    "macro":              0.3,   # Already priced, low trend value
    "earnings":           0.1,   # Zero relevance for commodity trends
    "m_a":                0.1,   # Zero relevance
}

# Priority order for tie-breaking top_category
_CATEGORY_PRIORITY = [
    "weather", "supply_chain", "commodity", "geopolitical",
    "regulatory", "sector", "other", "central_bank_subtle",
    "macro", "earnings", "m_a",
]

# ── Structural impact keywords ───────────────────────────────────
# News containing these words implies lasting supply/demand change
STRUCTURAL_KEYWORDS = {
    # High persistence (supply disruption)
    "drought": 1.5, "sécheresse": 1.5, "frost": 1.5, "gel": 1.5,
    "freeze": 1.4, "hurricane": 1.4, "typhoon": 1.3,
    "embargo": 1.5, "ban": 1.4, "export ban": 1.6,
    "sanctions": 1.4, "blockade": 1.5, "strike": 1.3,
    "shortage": 1.4, "deficit": 1.3, "crop failure": 1.6,
    "disease": 1.3, "blight": 1.4, "pest": 1.3,
    "el niño": 1.3, "la niña": 1.3, "monsoon": 1.2,
    # Accent-free variants (ASCII text from RSS/API often lacks accents)
    "el nino": 1.3, "la nina": 1.3, "secheresse": 1.5,
    # Copper-specific keywords
    "smelter": 1.4, "concentrate": 1.3, "treatment charges": 1.3,
    "mine closure": 1.5, "mine shutdown": 1.5, "tc/rc": 1.3,
    # Cocoa-specific keywords
    "swollen shoot": 1.5, "black pod": 1.4, "harmattan": 1.3,
    "main crop": 1.1, "mid-crop": 1.1, "grindings": 1.3,
    "certified stocks": 1.4, "warehouse stocks": 1.4,
    # Coffee-specific keywords
    "robusta": 1.2, "arabica": 1.2, "coffee rust": 1.4,
    "leaf rust": 1.4, "ferrugem": 1.4,
    "roya": 1.4,
    "black frost": 1.6, "geada negra": 1.6,
    "safrinha": 1.2,
    "conab": 1.3,
    # Wheat-specific keywords
    "wheat rust": 1.4, "stem rust": 1.4, "karnal bunt": 1.3,
    "vomitoxin": 1.3, "fusarium": 1.3,
    "export pace": 1.2, "delivery notice": 1.3,
    # Medium persistence
    "inventory": 1.2, "stockpile": 1.2, "reserves": 1.2,
    "production cut": 1.3, "opec": 1.2, "output": 1.1,
    "demand": 1.1, "import": 1.1, "export": 1.1,
    # Low persistence (temporary events)
    "forecast": 0.9, "outlook": 0.9, "estimate": 0.9,
    "rumor": 0.7, "speculation": 0.7, "talks": 0.8,
}

# Tickers tracked by Trader 2
# NOTE: Must stay in sync with TREND_TICKERS in agent_trader_2.py
TREND_TICKERS = {"HG=F", "CC=F", "KC=F", "ZW=F"}

# Ticker display names for Claude prompt
TREND_TICKER_INFO = {
    "HG=F": "Copper Futures",
    "CC=F": "Cocoa Futures",
    "KC=F": "Coffee Futures",
    "ZW=F": "Wheat Futures",
}

# Minimum trend score threshold
MIN_TREND_SCORE = 8.0

# H1: Max items per Claude batch (consistent with Scoring 1's SCORING_BATCH_SIZE)
TREND_SCORING_BATCH_SIZE = 40

# Categories relevant to commodity trends (pre-filter for Claude)
RELEVANT_CATEGORIES_KEYWORDS = [
    "drought", "frost", "freeze", "hurricane", "typhoon", "flood",
    "embargo", "ban", "sanctions", "blockade", "strike", "shortage",
    "crop", "harvest", "yield", "production", "inventory", "stockpile",
    "copper", "cocoa", "coffee", "wheat", "grain", "cereal",
    "mine", "smelter", "port", "shipping", "freight",
    "weather", "storm", "rain", "heat", "cold", "snow",
    "usda", "noaa", "conab", "eia", "opec",
    "export", "import", "tariff", "trade war", "quota",
    "disease", "blight", "pest", "rust", "fungus",
    "el nino", "la nina", "monsoon", "geada",
    "warehouse", "delivery", "stocks", "reserves",
    "brazil", "india", "china", "ukraine", "russia", "argentina",
    "ivory coast", "ghana", "cote d'ivoire", "midwest",
    "commodity", "commodities", "futures", "supply", "demand",
]

# News categories to always include (even without keyword match)
ALWAYS_RELEVANT_CATEGORIES = {
    "weather", "supply_chain", "commodity", "commodities_energy",
    "commodities_agri", "commodities_soft", "commodities_industrial",
}


# Precompile word-boundary patterns for structural keywords
_STRUCTURAL_PATTERNS: dict[str, tuple[re.Pattern, float]] = {}
for _kw, _mult in STRUCTURAL_KEYWORDS.items():
    _STRUCTURAL_PATTERNS[_kw] = (re.compile(r'\b' + re.escape(_kw) + r'\b', re.IGNORECASE), _mult)


def _compute_persistence_mult(title: str, description: str = "") -> float:
    """Evaluate how structurally persistent a news signal is."""
    text = title + " " + (description or "")
    best_mult = 1.0
    for keyword, (pattern, mult) in _STRUCTURAL_PATTERNS.items():
        if pattern.search(text):
            best_mult = max(best_mult, mult)
    return best_mult


# ── Claude API for Trend Scoring ─────────────────────────────────

# Reuse Scoring 1's singleton client
from ..news_scorer import _get_client, _get_model

# Trend-specific categories (subset relevant for commodity trends)
TREND_NEWS_CATEGORIES = [
    "weather", "supply_chain", "commodity", "geopolitical",
    "regulatory", "sector", "other",
]

TREND_TICKER_LIST = ", ".join(
    f"{ticker} ({name})" for ticker, name in TREND_TICKER_INFO.items()
)

# ── Trend-specific system prompt ─────────────────────────────────
TREND_SYSTEM_PROMPT = f"""Tu es un expert en tendances sur commodities physiques depuis 20 ans.
Tu analyses les signaux structurels qui impactent l'offre et la demande de 4 commodities :
{TREND_TICKER_LIST}

<role>
Ton expertise : identifier les facteurs STRUCTURELS qui changent l'equilibre offre/demande.
On ne cherche PAS le timing de marche (ca c'est le trading intraday).
On cherche les FORCES DE FOND qui poussent un prix dans une direction pendant des jours/semaines.

Exemples de signaux forts :
- Secheresse au Midwest US → ble affecte pendant toute la saison
- Gel au Bresil → recolte de cafe detruite, impact 6-12 mois
- Mine de cuivre fermee → deficit d'offre pendant des mois
- Embargo sur exportations de ble → prix mondiaux affectes durablement
- Maladie du cacaoyer en Cote d'Ivoire → production en baisse structurelle

Exemples de signaux FAIBLES (bruit) :
- Earnings d'une entreprise → zero impact sur les commodities physiques
- Decision de taux de la Fed → impact indirect et deja price
- Rumeur M&A → non pertinent pour les tendances commodities
</role>

<scoring_dimensions>
Pour chaque news, evalue ces 9 dimensions :

1. structural_impact (0-100) : A quel point cet evenement change l'equilibre offre/demande ?
   0=aucun impact structurel | 30=impact mineur/temporaire | 50=impact modere |
   70=impact significatif sur la production/demande | 100=choc structurel majeur (recolte detruite, mine fermee)

2. persistence (0-100) : Combien de temps l'impact va-t-il durer ?
   0=heures (bruit de marche) | 20=jours (evenement ponctuel) | 50=semaines |
   70=mois (saison affectee) | 100=structurel multi-annee (changement permanent)

3. magnitude (0-100) : Amplitude du mouvement prix attendu ?
   0-10=bruit (<0.5%) | 20-40=modere (0.5-2%) | 50-70=notable (2-5%) | 80-100=choc (>5%)

4. reliability (0-100) : Niveau de confirmation du signal ?
   0-20=rumeur/prevision lointaine | 30-50=presse "sources proches" |
   60-80=donnees officielles (USDA, NOAA) | 90-100=fait observe/mesure (gel constate, mine fermee)

5. directional_clarity (0-100) : Clarte de la direction d'impact ?
   0=ambigu | 50=probable mais incertain | 100=direction evidente

6. direction : LONG / SHORT / NEUTRAL
   LONG = prix va monter (deficit offre, demande accrue, disruption supply)
   SHORT = prix va baisser (surplus, demande faible, bonne recolte)
   NEUTRAL = pas de direction claire ou non pertinent

7. impacted_tickers : tickers directement impactes parmi [{', '.join(TREND_TICKERS)}]
   Ne liste QUE les tickers de notre univers directement impactes.

8. news_category : {', '.join(TREND_NEWS_CATEGORIES)}

9. reasoning : explication en 1-2 phrases de l'impact structurel attendu
</scoring_dimensions>

<hard_rules>
REGLES IMPERATIVES :

PERTINENCE : Ne score que les news qui impactent DIRECTEMENT les 4 commodities.
Si la news n'a aucun lien avec cuivre, cacao, cafe ou ble → direction=NEUTRAL, structural_impact=0.

EARNINGS/MACRO/M&A : Toujours structural_impact=0, direction=NEUTRAL.
Ces categories n'ont aucune pertinence pour les tendances commodities physiques.

METEO : Les previsions a 10+ jours sont PEU fiables (reliability < 30).
Un gel CONSTATE (bulletin local) = reliability 90+. Une prevision a 7j = reliability 50.

GEOPOLITIQUE : Sanctions confirmees = persistence elevee. Menaces verbales = persistence faible.

SUPPLY CHAIN : Impact selon la duree. Port ferme 2 jours = faible. Embargo = elevee.

CUIVRE (HG=F) : Sensible a la demande chinoise, mines sud-americaines/africaines, smelters.
CACAO (CC=F) : Sensible a la meteo en Cote d'Ivoire/Ghana, maladies (swollen shoot, black pod).
CAFE (KC=F) : Sensible au gel au Bresil, pluies en Colombie, maladies (rouille/ferrugem).
BLE (ZW=F) : Sensible a la meteo Midwest US/Ukraine/Inde, export bans, maladies (rouille).

EFFETS DE SECOND ORDRE : Un embargo sur les engrais → impact indirect sur ble/mais.
Un gel bresilien impacte cafe ET sucre (memes planteurs). Mais max 2 tickers impactes.
</hard_rules>"""

TREND_PROMPT_VERSION = hashlib.md5(TREND_SYSTEM_PROMPT.encode()).hexdigest()[:8]

# ── Trend-specific tool schema ───────────────────────────────────
TREND_SCORING_TOOL = {
    "name": "submit_trend_scores",
    "description": "Submit structural trend analysis scores for each news headline impacting commodities",
    "input_schema": {
        "type": "object",
        "properties": {
            "scores": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer", "description": "1-based index of the headline"},
                        "structural_impact": {
                            "type": "integer", "minimum": 0, "maximum": 100,
                            "description": "How much does this change supply/demand balance? 0=none, 100=major structural shift",
                        },
                        "persistence": {
                            "type": "integer", "minimum": 0, "maximum": 100,
                            "description": "How long will the impact last? 0=hours, 50=weeks, 100=permanent",
                        },
                        "magnitude": {
                            "type": "integer", "minimum": 0, "maximum": 100,
                            "description": "Expected price move: 0=noise, 50=2-5%, 100=>5%",
                        },
                        "reliability": {
                            "type": "integer", "minimum": 0, "maximum": 100,
                            "description": "How confirmed? 0=rumor, 50=press, 100=measured fact",
                        },
                        "directional_clarity": {
                            "type": "integer", "minimum": 0, "maximum": 100,
                            "description": "How clear is the price direction? 0=ambiguous, 100=obvious",
                        },
                        "direction": {"type": "string", "enum": ["LONG", "SHORT", "NEUTRAL"]},
                        "impacted_tickers": {
                            "type": "array", "items": {"type": "string"},
                            "maxItems": 2,
                            "description": f"Tickers impacted from [{', '.join(TREND_TICKERS)}]",
                        },
                        "news_category": {
                            "type": "string",
                            "enum": TREND_NEWS_CATEGORIES,
                        },
                        "reasoning": {
                            "type": "string",
                            "maxLength": 300,
                        },
                    },
                    "required": [
                        "index", "structural_impact", "persistence", "magnitude",
                        "reliability", "directional_clarity", "direction",
                        "impacted_tickers", "news_category", "reasoning",
                    ],
                },
            },
        },
        "required": ["scores"],
    },
}


# ── Score cache for trend scoring ────────────────────────────────
_trend_score_cache: dict[str, tuple[dict, float]] = {}
_trend_cache_lock = threading.Lock()
TREND_CACHE_TTL = 4 * 3600  # 4 hours


def _get_trend_cache_key(title: str, description: str | None) -> str:
    raw = f"trend|{title}|{description or ''}"
    return hashlib.md5(raw.encode()).hexdigest()


def _get_cached_trend_score(key: str) -> dict | None:
    with _trend_cache_lock:
        if key in _trend_score_cache:
            entry, ts = _trend_score_cache[key]
            if time.time() - ts < TREND_CACHE_TTL:
                return entry
            del _trend_score_cache[key]
    return None


TREND_CACHE_MAX_SIZE = 500  # S2-P2: LRU cap (consistent with Scoring 1)


def _set_cached_trend_score(key: str, entry: dict) -> None:
    with _trend_cache_lock:
        _trend_score_cache[key] = (entry, time.time())
        now = time.time()
        stale = [k for k, (_, ts) in _trend_score_cache.items()
                 if now - ts > TREND_CACHE_TTL]
        for k in stale:
            del _trend_score_cache[k]
        # S2-P2: LRU eviction if cache exceeds max size
        if len(_trend_score_cache) > TREND_CACHE_MAX_SIZE:
            sorted_keys = sorted(_trend_score_cache.keys(),
                                 key=lambda k: _trend_score_cache[k][1])
            for k in sorted_keys[:len(_trend_score_cache) - TREND_CACHE_MAX_SIZE]:
                del _trend_score_cache[k]


# ── Token tracking for Scoring 2 ────────────────────────────────
_trend_token_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "scans": 0}
_trend_token_lock = threading.Lock()  # S2-P1: Thread safety (consistent with Scoring 1)


def get_trend_token_usage() -> dict:
    """Return cumulative trend scoring token usage."""
    with _trend_token_lock:
        return dict(_trend_token_usage)


# ── Pre-filter: is the news relevant to commodity trends? ────────

def _is_trend_relevant(title: str, description: str = "",
                       news_category: str = "") -> bool:
    """Check if a news item is potentially relevant to commodity trends.

    Returns True if the news should be sent to Claude for trend scoring.
    This is a cheap pre-filter to avoid wasting API tokens on irrelevant news.
    C1: Now uses news_category to reject earnings/macro/m_a (zero trend relevance).
    """
    # C1: Reject categories with zero trend relevance (even if keywords match)
    ZERO_TREND_CATEGORIES = {"earnings", "macro", "m_a", "central_bank_subtle"}
    if news_category in ZERO_TREND_CATEGORIES:
        return False

    # Always include known commodity categories
    if news_category in ALWAYS_RELEVANT_CATEGORIES:
        return True

    # Check keywords in title + description
    text = (title + " " + (description or "")).lower()
    for kw in RELEVANT_CATEGORIES_KEYWORDS:
        if kw in text:
            return True

    return False


# ── Claude API call for trend scoring ────────────────────────────

def _call_claude_for_trend(headlines: list[str], max_retries: int = 2) -> list[dict]:
    """Call Claude API with trend-specific prompt and tool schema.

    Similar to Scoring 1's _call_claude_with_retry but with:
    - Trend-specific system prompt (structural impact, not edge detection)
    - Trend-specific tool schema (9 dimensions, not 11)
    - Smaller batches (trend-relevant news is pre-filtered, typically <20 items)

    Returns parsed scores list, or empty list on failure.
    """
    try:
        import anthropic
    except ImportError:
        logger.error("anthropic package not available — cannot score for trend")
        return []

    try:
        client = _get_client()
    except ValueError as exc:
        logger.error("Cannot create Claude client: %s", exc)
        return []

    model = _get_model()

    user_message = f"""Voici {len(headlines)} headlines récentes. Analyse chacune pour son impact structurel
sur les 4 commodities suivies : {TREND_TICKER_LIST}

Pour chaque headline, utilise l'outil submit_trend_scores pour soumettre ton analyse.
Si une news n'a AUCUN lien avec ces commodities, donne structural_impact=0 et direction=NEUTRAL.

Headlines :
{chr(10).join(headlines)}"""

    dynamic_max_tokens = max(2048, min(8192, len(headlines) * 300))

    system_messages = [
        {
            "type": "text",
            "text": TREND_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]

    for attempt in range(max_retries + 1):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=dynamic_max_tokens,
                temperature=0,
                system=system_messages,
                messages=[{"role": "user", "content": user_message}],
                tools=[TREND_SCORING_TOOL],
                tool_choice={"type": "tool", "name": "submit_trend_scores"},
                timeout=45.0,
            )

            # Handle truncation — H3: raise cap to 16384 (was 8192, same retry just re-truncated)
            if response.stop_reason == "max_tokens":
                dynamic_max_tokens = min(16384, int(dynamic_max_tokens * 1.5))
                logger.warning("Trend scoring truncated (attempt %d, raising max_tokens to %d)",
                               attempt + 1, dynamic_max_tokens)
                if attempt < max_retries:
                    time.sleep(2 ** attempt)
                    continue

            # Track token usage (S2-P1: thread-safe)
            if hasattr(response, "usage"):
                with _trend_token_lock:
                    _trend_token_usage["input_tokens"] += getattr(response.usage, "input_tokens", 0)
                    _trend_token_usage["output_tokens"] += getattr(response.usage, "output_tokens", 0)

            # Extract tool_use response
            for block in response.content:
                if block.type == "tool_use" and block.name == "submit_trend_scores":
                    scores = block.input.get("scores", [])
                    return scores

            # Fallback: try JSON parsing
            for block in response.content:
                if hasattr(block, "text") and block.text:
                    raw = block.text.strip()
                    try:
                        start = raw.index("[")
                        end = raw.rindex("]") + 1
                        parsed = json.loads(raw[start:end])
                        # S2-P8: Validate format (consistent with Scoring 1 C5)
                        if (isinstance(parsed, list) and parsed
                                and isinstance(parsed[0], dict)
                                and "index" in parsed[0]
                                and isinstance(parsed[0]["index"], int)):
                            return parsed
                        logger.warning("Trend fallback JSON parse: array not in expected format")
                    except (ValueError, json.JSONDecodeError):
                        continue

            logger.error("Trend scoring: no tool_use or parseable JSON (attempt %d)", attempt + 1)
            if attempt < max_retries:
                time.sleep(2 ** attempt)
                continue
            return []

        except Exception as exc:
            logger.error("Trend scoring Claude error (attempt %d): %s", attempt + 1, exc)
            if attempt < max_retries:
                time.sleep(2 ** attempt)
                continue
            return []

    return []


# ── Main scoring function ────────────────────────────────────────

def score_news_for_trend(news_items: list) -> dict:
    """Score news items for trend relevance using dedicated Claude API call.

    Args:
        news_items: list[NewsItem] — raw news items (NOT pre-scored by Scoring 1)

    Returns dict with:
        - trend_scored: list[dict] — all trend-relevant scored items
        - by_ticker: dict[ticker, list[dict]] — grouped by impacted ticker
        - accumulation: dict[ticker, {long: float, short: float}] — directional signal sum
        - stats: {total_items, relevant_items, avg_trend_score, max_trend_score, top_category}
        - claude_scored_count: int — items actually sent to Claude
    """
    result = {
        "trend_scored": [],
        "by_ticker": {},
        "accumulation": {},
        "stats": {
            "total_items": len(news_items),
            "relevant_items": 0,
            "avg_trend_score": 0.0,
            "max_trend_score": 0.0,
            "top_category": "",
        },
        "claude_scored_count": 0,
    }

    # Initialize accumulation for all trend tickers
    for ticker in TREND_TICKERS:
        result["accumulation"][ticker] = {"long": 0.0, "short": 0.0}
        result["by_ticker"][ticker] = []

    if not news_items:
        return result

    # Step 1: Pre-filter for trend relevance + check cache
    items_to_score: list = []  # (index, NewsItem)
    cached_entries: list[dict] = []

    for item in news_items:
        # Pre-filter: skip obviously irrelevant news
        # C1: Pass news_category to filter earnings/macro before Claude
        item_category = getattr(item, "news_category", "")
        if not _is_trend_relevant(item.title, item.description, item_category):
            continue

        # Check cache
        cache_key = _get_trend_cache_key(item.title, item.description)
        cached = _get_cached_trend_score(cache_key)
        if cached is not None:
            cached_entries.append(cached)
            continue

        items_to_score.append(item)

    if cached_entries:
        logger.info("Trend scoring: %d items from cache", len(cached_entries))

    # Step 2: Call Claude for uncached items
    claude_scores: list[dict] = []
    if items_to_score:
        # Build headlines
        now = datetime.now(timezone.utc)
        headlines = []
        for i, item in enumerate(items_to_score):
            age_str = ""
            if item.published:
                age_h = (now - item.published).total_seconds() / 3600
                age_str = f" [il y a {age_h:.1f}h]"
            safe_title = re.sub(r'<[^>]+>', '', item.title or "")[:200]
            safe_desc = ""
            if item.description:
                safe_desc = f" | {re.sub(r'<[^>]+>', '', item.description)[:300]}"
            headlines.append(f"{i+1}. {safe_title}{safe_desc}{age_str}")

        logger.info("Trend scoring: sending %d items to Claude (prompt_version=%s)",
                     len(headlines), TREND_PROMPT_VERSION)

        # H1: Batch items to avoid truncation on large sets
        raw_scores = []
        for batch_start in range(0, len(headlines), TREND_SCORING_BATCH_SIZE):
            batch = headlines[batch_start:batch_start + TREND_SCORING_BATCH_SIZE]
            batch_scores = _call_claude_for_trend(batch)
            # Adjust indices for items in subsequent batches
            for entry in batch_scores:
                entry["index"] = entry.get("index", 0) + batch_start
            raw_scores.extend(batch_scores)

        result["claude_scored_count"] = len(items_to_score)
        with _trend_token_lock:
            _trend_token_usage["scans"] += 1

        # Map scores back to items
        for entry in raw_scores:
            idx = entry.get("index", 0) - 1
            if idx < 0 or idx >= len(items_to_score):
                continue

            item = items_to_score[idx]

            # Skip NEUTRAL or zero-impact
            direction = entry.get("direction", "NEUTRAL")
            structural_impact = max(0, min(100, entry.get("structural_impact", 0)))
            if direction == "NEUTRAL" or structural_impact == 0:
                # Cache the zero result to avoid re-scoring
                # H4: Preserve news_zone even for NEUTRAL (Learning 2 zone-aware lookup)
                cache_key = _get_trend_cache_key(item.title, item.description)
                _set_cached_trend_score(cache_key, {
                    "direction": "NEUTRAL", "structural_impact": 0,
                    "impacted_tickers": [], "trend_score": 0,
                    "news_zone": getattr(item, "news_zone", ""),
                })
                continue

            persistence = max(0, min(100, entry.get("persistence", 50)))
            magnitude = max(0, min(100, entry.get("magnitude", 50)))
            reliability = max(0, min(100, entry.get("reliability", 50)))
            clarity = max(0, min(100, entry.get("directional_clarity", 50)))
            impacted = entry.get("impacted_tickers", [])
            news_category = entry.get("news_category", "other")
            reasoning = entry.get("reasoning", "")

            # Filter tickers to only our TREND_TICKERS
            impacted = [t for t in impacted if t in TREND_TICKERS]

            # M3: Check chain reactions for trend tickers
            # e.g. a coffee frost (KC=F) may also impact CC=F (tropical soft group)
            if not impacted:
                # Before giving up, check if any impacted ticker chains to a trend ticker
                raw_impacted = entry.get("impacted_tickers", [])
                for src_ticker in raw_impacted:
                    for chain in CHAIN_REACTIONS.get(src_ticker, []):
                        if chain["ticker"] in TREND_TICKERS and chain["ticker"] not in impacted:
                            impacted.append(chain["ticker"])
                            logger.info("M3: Chain reaction %s→%s for trend scoring",
                                       src_ticker, chain["ticker"])

            if not impacted:
                continue

            # Compute trend score
            cat_mult = TREND_CATEGORY_MULTS.get(news_category, 0.6)
            description = item.description or ""
            persistence_mult = _compute_persistence_mult(item.title, description)

            # Trend formula:
            # trend_score = structural_impact * (clarity/100) * magnitude_factor
            #               * reliability_factor * persistence_factor
            #               * category_mult * persistence_mult * source_weight
            magnitude_factor = 0.5 + 0.5 * (magnitude / 100)
            reliability_factor = 0.4 + 0.6 * (reliability / 100)
            persistence_factor = 0.5 + 0.5 * (persistence / 100)  # New: persistence from Claude

            trend_score = round(
                structural_impact * (clarity / 100) * magnitude_factor
                * reliability_factor * persistence_factor
                * cat_mult * persistence_mult * item.source_weight,
                1
            )

            scored_item = {
                "title": item.title[:200],
                "description": (item.description or "")[:200],
                "source": item.source,
                "direction": direction,
                "news_category": news_category,
                "trend_score": trend_score,
                "structural_impact": structural_impact,
                "persistence": persistence,
                "magnitude": magnitude,
                "reliability": reliability,
                "clarity": clarity,
                "category_mult": cat_mult,
                "persistence_mult": persistence_mult,
                "reasoning": reasoning,
                "impacted_tickers": impacted,
                "news_zone": item.news_zone,
            }

            # Cache for future scans
            cache_key = _get_trend_cache_key(item.title, item.description)
            _set_cached_trend_score(cache_key, scored_item)

            claude_scores.append(scored_item)

    # Step 3: Merge cached + claude scores, then filter and accumulate
    all_scored_items = cached_entries + claude_scores
    now = datetime.now(timezone.utc)
    trend_scores = []

    for scored_item in all_scored_items:
        # Skip NEUTRAL/zero from cache
        if scored_item.get("direction") == "NEUTRAL" or scored_item.get("trend_score", 0) == 0:
            continue

        trend_score = scored_item.get("trend_score", 0)
        if trend_score < MIN_TREND_SCORE:
            continue

        impacted = scored_item.get("impacted_tickers", [])
        direction = scored_item.get("direction", "NEUTRAL")
        reliability = scored_item.get("reliability", 50)

        result["trend_scored"].append(scored_item)
        trend_scores.append(trend_score)

        # S2-P6: Freshness weight is always 1.0 for trend scoring
        # (structural signals don't decay, and we don't have publish time on cached entries)
        for ticker in impacted:
            if ticker not in TREND_TICKERS:
                continue
            result["by_ticker"][ticker].append(scored_item)

            weight = trend_score * (reliability / 100)
            if direction == "LONG":
                result["accumulation"][ticker]["long"] += weight
            elif direction == "SHORT":
                result["accumulation"][ticker]["short"] += weight

    # Stats
    result["stats"]["relevant_items"] = len(result["trend_scored"])
    if trend_scores:
        result["stats"]["avg_trend_score"] = round(
            sum(trend_scores) / len(trend_scores), 1
        )
        result["stats"]["max_trend_score"] = round(max(trend_scores), 1)

    # Top contributing category (deterministic on tie)
    cat_counts: dict[str, int] = {}
    for item in result["trend_scored"]:
        cat = item.get("news_category", "other")
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    if cat_counts:
        max_count = max(cat_counts.values())
        for cat in _CATEGORY_PRIORITY:
            if cat_counts.get(cat, 0) == max_count:
                result["stats"]["top_category"] = cat
                break
        else:
            result["stats"]["top_category"] = max(cat_counts, key=cat_counts.get)

    return result


class AgentScoring2(BaseAgent):
    """Agent Scoring 2 — Dedicated Claude scoring for trend following (Équipe 2).

    v8.0: Makes its own Claude API call with a trend-specific prompt.
    Previous versions consumed Scoring 1 output and applied heuristic re-weighting.
    """

    name = "scoring_2"
    description = "Scoring tendance — Claude dédié pour commodities"
    version = "8.2"  # v8.2: Thread-safe tokens, LRU cache cap, dead code cleanup, fallback validation

    def __init__(self):
        super().__init__()
        self._last_relevant_count: int = 0
        self._last_avg_score: float = 0.0
        self._total_rescorings: int = 0
        self._last_accumulation: dict = {}
        self._last_result: dict | None = None

    def run(self, news_items=None, scan_type=None, **kwargs) -> dict:
        """Score news for trend relevance using dedicated Claude call.

        Args:
            news_items: list[NewsItem] — raw news items from Agent News
            scan_type: ScanType enum (for context)

        Returns dict with trend-scored results.
        """
        # Backward compatibility: accept scored_news kwarg (old pipeline)
        if news_items is None:
            news_items = kwargs.get("scored_news")
            if news_items is not None:
                logger.warning("Scoring 2: received scored_news (old API) — "
                               "extracting raw NewsItems for dedicated scoring")
                # Extract NewsItem from ScoredNews objects
                raw_items = []
                for sn in news_items:
                    if hasattr(sn, "news"):
                        raw_items.append(sn.news)
                    else:
                        raw_items.append(sn)
                news_items = raw_items

        self._set_status(AgentStatus.WORKING,
                         f"Scoring {len(news_items or [])} news for trend (Claude)")

        start = time.monotonic()

        try:
            trend_data = self.execute(
                "Scoring news for trend via Claude",
                score_news_for_trend,
                news_items or [],
            )

            self._last_result = trend_data
            self._total_rescorings += 1
            self._last_relevant_count = trend_data["stats"]["relevant_items"]
            self._last_avg_score = trend_data["stats"]["avg_trend_score"]
            self._last_accumulation = trend_data.get("accumulation", {})

            # Log results
            relevant = trend_data["stats"]["relevant_items"]
            total = trend_data["stats"]["total_items"]
            avg_score = trend_data["stats"]["avg_trend_score"]
            claude_count = trend_data.get("claude_scored_count", 0)

            self.log(f"Trend scoring: {relevant}/{total} relevant (claude={claude_count})", {
                "relevant_items": relevant,
                "avg_trend_score": avg_score,
                "max_trend_score": trend_data["stats"].get("max_trend_score", 0),
                "top_category": trend_data["stats"].get("top_category", ""),
                "claude_scored_count": claude_count,
                "accumulation": {
                    t: {"long": round(v["long"], 1), "short": round(v["short"], 1)}
                    for t, v in trend_data.get("accumulation", {}).items()
                    if v["long"] > 0 or v["short"] > 0
                },
            })

            # Log significant signals
            for ticker, acc in trend_data.get("accumulation", {}).items():
                net = acc["long"] - acc["short"]
                if abs(net) > 10:
                    self.log_decision(f"Strong trend signal {ticker}", {
                        "ticker": ticker,
                        "net_signal": round(net, 1),
                        "long": round(acc["long"], 1),
                        "short": round(acc["short"], 1),
                        "direction": "LONG" if net > 0 else "SHORT",
                        "news_count": len(trend_data["by_ticker"].get(ticker, [])),
                    })

            # Publish
            duration_ms = int((time.monotonic() - start) * 1000)
            self.publish("trend_scored", {
                "relevant_items": relevant,
                "avg_trend_score": avg_score,
                "claude_scored_count": claude_count,
                "accumulation": {
                    t: {"net": round(v["long"] - v["short"], 1)}
                    for t, v in trend_data.get("accumulation", {}).items()
                },
                "duration_ms": duration_ms,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            self._set_status(
                AgentStatus.IDLE,
                f"{relevant} trend-relevant, avg score {avg_score}"
            )

            return trend_data

        except Exception as exc:
            self.log("Trend scoring failed", {"error": str(exc)}, level="ERROR")
            self._set_status(AgentStatus.ERROR, str(exc))
            raise

    def get_last_result(self) -> dict | None:
        """Get the last trend scoring result (for API/frontend)."""
        return self._last_result

    def get_metrics(self) -> dict:
        token_usage = get_trend_token_usage()
        return {
            "last_relevant_count": self._last_relevant_count,
            "last_avg_score": self._last_avg_score,
            "total_rescorings": self._total_rescorings,
            "accumulation": {
                t: {"net": round(v["long"] - v["short"], 1)}
                for t, v in self._last_accumulation.items()
            },
            "token_usage": token_usage,
        }
