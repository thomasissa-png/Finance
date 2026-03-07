"""Scores news headlines using Claude API with tool_use for structured output.

v4.3 audit changes:
- A1/F1: Model updated to Haiku (configurable via CLAUDE_MODEL env var)
- A2: temperature=0 for reproducible scoring
- A3: Singleton Anthropic client (reuse HTTP connections)
- A4: Dynamic max_tokens based on batch size
- B1: System prompt restructured with XML sections
- B2: Few-shot examples added for calibration
- B3/E1: Performance summary moved to system prompt (stable between scans)
- B5: Ticker list moved to user message (saves system prompt tokens)
- B6: Prompt version hash logged for tracking
- C1: perceived_age_hours added to tool schema (optional)
- C2: maxLength on reasoning field
- D1: Pre-filter earnings/macro before sending to Claude
- D2: Cross-dimension coherence validation post-scoring
- D3: confirmed_event field added to tool schema
- F2: Score cache by headline hash (TTL 4h)
- F3: Anthropic prompt caching (cache_control on system prompt)
- F4: Token usage logging per scan
"""

import hashlib
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone, timedelta

import anthropic

from .market_data import fetch_history, fetch_history_batch

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

# ── v3.6 (N): Cross-day signal accumulation ─────────────────────
# Physical signals (weather, supply_chain) that persist across days should
# accumulate rather than be deduped. A drought worsening over 3 days = stronger signal.
_signal_accumulator: dict[str, dict] = {}  # key: (category, ticker_set_key) -> {count, first_seen, last_seen}
ACCUMULATION_CATEGORIES = {"weather", "supply_chain", "commodity"}
ACCUMULATION_BOOST_PER_DAY = 0.1  # +10% per additional day the signal persists
ACCUMULATION_MAX_BOOST = 1.5      # Cap at 50% boost


def _get_signal_accumulation_boost(news_category: str, impacted_tickers: list[str]) -> float:
    """Check if this signal has been seen on previous days and compute boost.

    Returns a multiplier >= 1.0 (1.0 = no boost, up to ACCUMULATION_MAX_BOOST).
    """
    if news_category not in ACCUMULATION_CATEGORIES:
        return 1.0
    if not impacted_tickers:
        return 1.0

    key = f"{news_category}:{','.join(sorted(impacted_tickers[:3]))}"
    now = datetime.now(timezone.utc)

    if key in _signal_accumulator:
        entry = _signal_accumulator[key]
        days_active = (now - entry["first_seen"]).total_seconds() / 86400
        entry["count"] += 1
        entry["last_seen"] = now
        boost = min(ACCUMULATION_MAX_BOOST, 1.0 + days_active * ACCUMULATION_BOOST_PER_DAY)
        if boost > 1.0:
            logger.info("Signal accumulation: %s active for %.1f days → %.2fx boost",
                        key, days_active, boost)
        return boost
    else:
        _signal_accumulator[key] = {"count": 1, "first_seen": now, "last_seen": now}
        # Prune old entries (> 7 days)
        cutoff = now - timedelta(days=7)
        stale = [k for k, v in _signal_accumulator.items() if v["last_seen"] < cutoff]
        for k in stale:
            del _signal_accumulator[k]
        return 1.0


# ── v4.3 A3: Singleton Anthropic client ──────────────────────────
_anthropic_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    """Return a singleton Anthropic client, reusing HTTP connections."""
    global _anthropic_client
    if _anthropic_client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")
        _anthropic_client = anthropic.Anthropic(api_key=api_key)
    return _anthropic_client


# ── v4.3 A1/F1: Configurable model ──────────────────────────────
# Default to Haiku for cost efficiency (10x cheaper, sufficient for structured scoring).
# Override with CLAUDE_MODEL env var for Sonnet if needed.
DEFAULT_MODEL = "claude-haiku-4-5-20251001"


def _get_model() -> str:
    return os.environ.get("CLAUDE_MODEL", DEFAULT_MODEL)


# ── v4.3 F2: Score cache (v5.0: thread-safe, stores post-hard-cap values) ──
# Cache scored headlines by hash(title+description) to avoid re-scoring
# the same news across consecutive scans. TTL 4 hours.
# v5.0 C1: Cache stores POST-hard-cap values to avoid bypassing safety caps.
_score_cache: dict[str, tuple[dict, float]] = {}  # hash -> (score_entry, timestamp)
_score_cache_lock = threading.Lock()
SCORE_CACHE_TTL_SECONDS = 4 * 3600  # 4 hours


def _get_cache_key(title: str, description: str | None) -> str:
    """Compute a cache key for a headline."""
    raw = f"{title}|{description or ''}"
    return hashlib.md5(raw.encode()).hexdigest()


def _get_cached_score(key: str) -> dict | None:
    """Return cached score if exists and not expired."""
    with _score_cache_lock:
        if key in _score_cache:
            entry, ts = _score_cache[key]
            if time.time() - ts < SCORE_CACHE_TTL_SECONDS:
                return entry
            del _score_cache[key]
    return None


def _set_cached_score(key: str, score_entry: dict) -> None:
    """Store a score in the cache."""
    with _score_cache_lock:
        _score_cache[key] = (score_entry, time.time())
        # Prune old entries
        now = time.time()
        stale = [k for k, (_, ts) in _score_cache.items()
                 if now - ts > SCORE_CACHE_TTL_SECONDS]
        for k in stale:
            del _score_cache[k]


# ── v4.3 F4: Token usage tracking ───────────────────────────────
_scan_token_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "scans": 0}


def get_token_usage() -> dict:
    """Return cumulative token usage stats."""
    return dict(_scan_token_usage)


# ── v4.3 B6: Prompt version hash ────────────────────────────────
TICKER_LIST = ", ".join(f"{a.ticker} ({a.name})" for a in ASSETS)
CATEGORY_LIST = ", ".join(NEWS_CATEGORIES)

# v4.3 B1: Restructured system prompt with XML sections for clarity
SYSTEM_PROMPT = f"""Tu es un speculateur expert en news trading depuis 20 ans, specialise dans la detection
de DISLOCATIONS NON ENCORE PRICEES par le marche.

<role>
Ton edge : identifier les news PAS ENCORE integrees dans les cours — les signaux en avance de phase.
On cherche la news que le marche n'a PAS ENCORE pricee, pas celle que tout le monde commente.
</role>

<scoring_dimensions>
Pour chaque news, evalue ces 11 dimensions :

1. surprise (0-100) : A quel point cette information est inattendue.
   0=anticipe/consensus | 50=moderement surprenant | 100=choc total/cygne noir

2. directional_clarity (0-100) : Clarte de la direction d'impact.
   0=ambigu, deux sens possibles | 100=direction absolument evidente

3. transmission_delay (0-100) : CRITIQUE — Temps avant que le marche price pleinement.
   0=deja price (earnings, NFP conforme)
   20=algos HFT ont reagi (CPI, FOMC decision)
   50=quelques acteurs ont vu, pas le gros du marche
   80=info specialisee, seuls les experts comprennent (rapport USDA, alerte NOAA)
   100=personne n'a fait le lien (gel Bresil bulletin local → cafe dans 6-12h)

4. market_awareness (0-100) : % des participants qui ont DEJA VU l'info.
   0=personne (bulletin meteo local) | 30=specialistes | 50=desk institutionnels
   80=terminaux Bloomberg | 100=tout le monde (CNN/BBC/Twitter trending)

5. expected_magnitude (0-100) : Amplitude du move attendu.
   0-10=bruit (<0.3%) | 20-40=modere (0.3-1%) | 50-70=notable (1-3%) | 80-100=choc (>3%)

6. signal_reliability (0-100) : Niveau de confirmation du signal.
   0-20=rumeur/prevision 10j | 30-50=presse "sources proches" | 60-80=donnees officielles
   90-100=fait observe/mesure (gel constate, pipeline explose, stock draw publie)

7. direction : LONG / SHORT / NEUTRAL

8. impacted_tickers : tickers directement impactes de notre univers

9. news_category : {CATEGORY_LIST}

10. reasoning : explication en 1-2 phrases incluant l'estimation du delai de pricing

11. confirmed_event (true/false) : Est-ce un fait confirme (true) ou une rumeur/prevision (false) ?
</scoring_dimensions>

<hard_rules>
REGLES IMPERATIVES — ne jamais devier :

EARNINGS : transmission_delay ≤ 10, market_awareness ≥ 90. Price en pre-market par algos. Zero edge.

DECISIONS DE TAUX / NFP / CPI : transmission_delay 0-5, market_awareness 100. Algos en microsecondes.

SIGNAUX PHYSIQUES (meteo, shipping, stocks commodities) : transmission_delay 60-100.
Les traders de commodities physiques sont lents a repercuter sur les futures.

GEOPOLITIQUE : declaration officielle = deja vue. Mouvement militaire OSINT = en avance de phase.

M&A : Rumeur ("talks","in discussions") → delay 40-60, confirmed_event=false.
      Confirme ("announces","agrees to buy") → delay ≤ 10, awareness ≥ 85, confirmed_event=true.

CENTRAL BANK : Decision taux → delay ≤ 5, confirmed_event=true.
              Discours president (Powell/Lagarde) → delay 20-40.
              Discours secondaire (regional Fed) → delay 40-60.

EFFETS DE SECOND ORDRE : gel bresilien → cafe + sucre (memes planteurs). Max 2 tickers impactes.

COHERENCE : Si surprise est elevee (>70), transmission_delay devrait etre >40.
            Si market_awareness est >80, transmission_delay devrait etre <30.

SURPRISE vs MAGNITUDE — ce sont deux dimensions DIFFERENTES :
- surprise = "personne ne s'y attendait" (0=consensus, 100=cygne noir)
- expected_magnitude = "l'impact sur les prix sera grand" (0=bruit, 100=choc)
Exemples : rapport EIA hebdomadaire avec draw RECORD = LOW surprise (40-50, EIA publie chaque mercredi) mais HIGH magnitude (70-80, draw historique). Rumeur gel Bresil non confirmee = HIGH surprise (80) mais MEDIUM magnitude (50, pas encore confirme).

TIMING DE PRICING par categorie :
- weather/supply_chain : pricing PROGRESSIF 6-24h (these se confirme graduellement) → delay 70-100
- commodity (data officielle EIA/USDA) : pricing en 1-3h (algo + traders physiques) → delay 50-75
- geopolitical : spike initial 15-30min puis reversion possible si non confirme → delay 40-70
- sector : liens indirects prices en 2-6h → delay 50-80

TICKERS : Ne liste que les 1-2 tickers les PLUS DIRECTEMENT impactes. Ne pas diluer le signal.
</hard_rules>

<examples>
EXEMPLES DE SCORING (format attendu) :

Exemple 1 — Signal physique fort :
Headline: "NOAA: Severe drought warning for US Midwest corn belt, soil moisture at 10-year low"
→ surprise=75, directional_clarity=90, transmission_delay=85, market_awareness=15,
  expected_magnitude=65, signal_reliability=95, direction=LONG,
  impacted_tickers=["ZC=F","ZS=F"], news_category=weather, confirmed_event=true,
  reasoning="Secheresse severe confirmee par NOAA pendant silking mais. Marche futures pas encore reagi — pricing dans 6-12h."

Exemple 2 — Earnings zero-edge :
Headline: "Apple reports Q4 earnings beat, revenue up 8% YoY"
→ surprise=30, directional_clarity=70, transmission_delay=5, market_awareness=95,
  expected_magnitude=40, signal_reliability=100, direction=LONG,
  impacted_tickers=[], news_category=earnings, confirmed_event=true,
  reasoning="Earnings deja pricees en after-hours par algos HFT. Zero edge pour nous."

Exemple 3 — Geopolitique early signal :
Headline: "Maritime tracking shows 15 Iranian tankers changing course away from Strait of Hormuz"
→ surprise=80, directional_clarity=85, transmission_delay=90, market_awareness=10,
  expected_magnitude=70, signal_reliability=60, direction=LONG,
  impacted_tickers=["CL=F","BZ=F"], news_category=geopolitical, confirmed_event=false,
  reasoning="Signal OSINT rare, mainstream media n'a pas encore repris. Impact petrole dans 12-24h si confirme."

Exemple 4 — Signal mid-range tradeable (NE PAS rejeter) :
Headline: "EIA: crude oil inventories draw -5.2M barrels vs consensus -3.0M"
→ surprise=45, directional_clarity=85, transmission_delay=65, market_awareness=40,
  expected_magnitude=55, signal_reliability=95, direction=LONG,
  impacted_tickers=["CL=F"], news_category=commodity, confirmed_event=true,
  reasoning="Draw surprise 2.2M au-dessus du consensus. EIA publie a 16:30 CET, traders physiques reagissent dans 1-3h. Edge sur le second ordre."

Exemple 5 — Signal faible a rejeter :
Headline: "CNBC: Analysts expect Fed to hold rates at next meeting"
→ surprise=10, directional_clarity=30, transmission_delay=5, market_awareness=95,
  expected_magnitude=15, signal_reliability=60, direction=NEUTRAL,
  impacted_tickers=[], news_category=macro, confirmed_event=false,
  reasoning="Anticipation consensus deja pricee dans les futures. Aucun edge."
</examples>

Tiens compte du CONTEXTE DE MARCHE fourni (VIX, tendances) pour ta calibration."""

# v4.3 B6: Prompt version hash for tracking
PROMPT_VERSION = hashlib.md5(SYSTEM_PROMPT.encode()).hexdigest()[:8]

# (#9) Tool definition for structured output — with edge-detection fields
# v4.3: Added confirmed_event (D3), perceived_age_hours (C1), maxLength on reasoning (C2)
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
                        "expected_magnitude": {
                            "type": "integer", "minimum": 0, "maximum": 100,
                            "description": "Expected price move amplitude: 0=noise, 50=notable 1-3%, 100=major shock >3%",
                        },
                        "signal_reliability": {
                            "type": "integer", "minimum": 0, "maximum": 100,
                            "description": "How confirmed is this signal? 0=rumor/speculation, 50=press report, 100=confirmed fact/measurement",
                        },
                        "direction": {"type": "string", "enum": ["LONG", "SHORT", "NEUTRAL"]},
                        "impacted_tickers": {"type": "array", "items": {"type": "string"}, "maxItems": 2, "description": "Les 1-2 tickers les PLUS DIRECTEMENT impactes"},
                        "news_category": {"type": "string", "enum": NEWS_CATEGORIES},
                        "reasoning": {
                            "type": "string",
                            "maxLength": 300,  # v4.3 C2: Keep reasoning concise
                        },
                        # v4.3 D3: Confirmed event flag — replaces fragile keyword matching
                        "confirmed_event": {
                            "type": "boolean",
                            "description": "Is this a confirmed fact (true) or rumor/forecast/speculation (false)?",
                        },
                        # v4.3 C1: Optional perceived age field for coherence diagnostics
                        "perceived_age_hours": {
                            "type": "number",
                            "description": "How old do you estimate this news is, in hours? (optional diagnostic)",
                        },
                    },
                    "required": ["index", "surprise", "directional_clarity",
                                 "transmission_delay", "market_awareness",
                                 "expected_magnitude", "signal_reliability",
                                 "direction", "impacted_tickers", "news_category",
                                 "reasoning", "confirmed_event"],
                },
            },
        },
        "required": ["scores"],
    },
}


# ── D1: Pre-filter categories with zero edge ────────────────────
# These categories are ALWAYS zero-edge — skip Claude entirely.
ZERO_EDGE_KEYWORDS = {
    "earnings": [
        "earnings report", "quarterly results", "revenue beat", "eps beat",
        "profit rises", "profit falls", "quarterly profit", "annual results",
        "reports q1", "reports q2", "reports q3", "reports q4",
        "fiscal year results", "earnings surprise",
    ],
    "macro": [
        "nonfarm payroll", "jobs report", "cpi data", "inflation data",
        "gdp growth", "unemployment rate", "retail sales data",
        "consumer confidence index",
    ],
}


def _is_zero_edge_headline(title: str) -> str | None:
    """Return category if headline is clearly zero-edge, else None.

    v4.3 D1: Pre-filter obvious earnings/macro headlines before Claude.
    Saves tokens by not sending headlines we'll hard-cap anyway.
    """
    title_lower = title.lower()
    for category, keywords in ZERO_EDGE_KEYWORDS.items():
        for kw in keywords:
            if kw in title_lower:
                return category
    return None


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

    Uses Twelve Data batch API when available (1 HTTP request for all tickers),
    falls back to yfinance per-ticker. Market context is nice-to-have, not critical.
    """
    context = {"vix": None, "regime": "normal", "indices": {}, "trends": {}}

    # Batch fetch: VIX + indices (2d history) in one call
    short_tickers = ["^VIX", "^GSPC", "^FCHI", "^DJI"]
    short_data = fetch_history_batch(short_tickers, period_days=5, interval="1day")

    # VIX
    vix_df = short_data.get("^VIX")
    if vix_df is not None and not vix_df.empty:
        try:
            vix_val = round(float(vix_df["Close"].iloc[-1]), 1)
            context["vix"] = vix_val
            if vix_val >= 30:
                context["regime"] = "stress"
            elif vix_val >= 20:
                context["regime"] = "elevated"
            elif vix_val <= 13:
                context["regime"] = "calm"
        except Exception as exc:
            logger.warning("Failed to parse VIX: %s", exc)

    # Index changes
    for ticker, name in [("^GSPC", "S&P500"), ("^FCHI", "CAC40"), ("^DJI", "DowJones")]:
        df = short_data.get(ticker)
        if df is not None and len(df) >= 2:
            try:
                prev = float(df["Close"].iloc[-2])
                curr = float(df["Close"].iloc[-1])
                context["indices"][name] = round((curr - prev) / prev * 100, 2)
            except Exception as exc:
                logger.debug("Failed to parse index %s: %s", name, exc)

    # Trends (need 25d of data) — batch fetch
    trend_tickers = ["^GSPC", "^FCHI", "GC=F", "CL=F", "EURUSD=X"]
    trend_data = fetch_history_batch(trend_tickers, period_days=25, interval="1day")

    for ticker, name in [("^GSPC", "S&P500"), ("^FCHI", "CAC40"), ("GC=F", "Or"), ("CL=F", "WTI"), ("EURUSD=X", "EURUSD")]:
        df = trend_data.get(ticker)
        if df is not None and len(df) >= 20:
            try:
                close_now = float(df["Close"].iloc[-1])
                close_5d = float(df["Close"].iloc[-5])
                close_20d = float(df["Close"].iloc[0])
                trend_5d = "haussier" if close_now > close_5d * 1.005 else ("baissier" if close_now < close_5d * 0.995 else "neutre")
                trend_20d = "haussier" if close_now > close_20d * 1.01 else ("baissier" if close_now < close_20d * 0.99 else "neutre")
                context["trends"][name] = {"5d": trend_5d, "20d": trend_20d}
            except Exception as exc:
                logger.debug("Failed to parse trend %s: %s", name, exc)

    return context


def _build_context_string(market_ctx: dict, scan_type: ScanType) -> str:
    """Build the context string for Claude user message (#5, #8).

    v4.3 B3: Performance summary moved to system prompt — only market data here.
    """
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

    return " | ".join(parts)


def _build_system_messages() -> list[dict]:
    """Build the system prompt with prompt caching (F3) and performance summary (B3/E1).

    v4.3 F3: Uses Anthropic's cache_control to cache the static system prompt.
    The performance summary is appended as a non-cached block (changes per journal).
    """
    # Static system prompt — cached across calls (saves ~50% input tokens)
    messages = [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},  # v4.3 F3: prompt caching
        }
    ]

    # Performance summary — dynamic, not cached (changes after each journal run)
    perf_summary = build_performance_summary()
    if perf_summary:
        messages.append({
            "type": "text",
            "text": perf_summary,
        })

    return messages


def _call_claude_with_retry(client, headlines, session_context, max_retries=3):
    """Call Claude API with tool_use (#9) for structured output.

    v4.3 changes:
    - A1/F1: Uses configurable model (default Haiku)
    - A2: temperature=0 for reproducible scoring
    - A4: Dynamic max_tokens based on batch size
    - B3/E1: System prompt as structured blocks with cache_control
    - B5: Ticker list in user message instead of system prompt
    - F3: Prompt caching via cache_control
    - F4: Token usage logging

    Returns parsed scores list, or empty list on failure.
    Never raises — API errors are logged and return [] so the scan
    completes gracefully (no trade) instead of crashing.
    """
    # v4.3 B5: Ticker list in user message (saves system prompt tokens)
    user_message = f"""Contexte : {session_context}

Univers de {len(ASSETS)} actifs surveilles :
{TICKER_LIST}

Voici {len(headlines)} headlines recentes. Analyse chacune et utilise l'outil submit_news_scores pour soumettre tes scores.

Headlines :
{chr(10).join(headlines)}"""

    # v4.3 A4: Dynamic max_tokens — 300 tokens per item is generous
    dynamic_max_tokens = max(4096, min(16384, len(headlines) * 350))

    # v4.3 B3/E1/F3: Structured system prompt with caching
    system_messages = _build_system_messages()

    model = _get_model()

    for attempt in range(max_retries + 1):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=dynamic_max_tokens,
                temperature=0,  # v4.3 A2: Reproducible scoring
                system=system_messages,  # v4.3 F3: Cached system prompt
                messages=[{"role": "user", "content": user_message}],
                tools=[SCORING_TOOL],
                tool_choice={"type": "tool", "name": "submit_news_scores"},
                timeout=120.0,  # 120s timeout — generous margin for API congestion
            )

            # Detect truncation — if max_tokens was hit, scores are likely incomplete
            if response.stop_reason == "max_tokens":
                logger.warning("Claude response truncated (max_tokens hit, attempt %d) — retrying", attempt + 1)
                if attempt < max_retries:
                    time.sleep(2 ** attempt)
                    continue

            # v5.0 N4: Track token usage only on final successful response (not retries)
            if hasattr(response, "usage"):
                _scan_token_usage["input_tokens"] += getattr(response.usage, "input_tokens", 0)
                _scan_token_usage["output_tokens"] += getattr(response.usage, "output_tokens", 0)
                # Log cache performance if available
                cache_read = getattr(response.usage, "cache_read_input_tokens", 0)
                cache_creation = getattr(response.usage, "cache_creation_input_tokens", 0)
                if cache_read > 0 or cache_creation > 0:
                    logger.info("Prompt cache: read=%d, creation=%d tokens",
                                cache_read, cache_creation)

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
        except anthropic.APITimeoutError as exc:
            # Specific handling for timeouts — use longer backoff since API is likely congested
            wait = min(4 * (2 ** attempt), 30)  # 4s, 8s, 16s, 30s
            logger.error("Claude API timeout (attempt %d/%d, wait %ds): %s",
                         attempt + 1, max_retries + 1, wait, exc)
            if attempt < max_retries:
                time.sleep(wait)
                continue
            logger.error("Claude API timeout after %d attempts — scan will proceed without scores", max_retries + 1)
            return []
        except anthropic.APIError as exc:
            wait = 2 ** attempt  # 1s, 2s, 4s, 8s
            logger.error("Claude API error (attempt %d/%d, wait %ds): %s",
                         attempt + 1, max_retries + 1, wait, exc)
            if attempt < max_retries:
                time.sleep(wait)
                continue
            logger.error("Claude API error after %d attempts — scan will proceed without scores", max_retries + 1)
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

    v4.3 changes:
    - A3: Uses singleton client
    - D1: Pre-filters zero-edge headlines (earnings/macro)
    - F2: Uses score cache to avoid re-scoring same headlines
    - B6: Logs prompt version hash

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

    # v4.3 A3: Singleton client
    client = _get_client()

    # v4.3 B6: Log prompt version
    logger.info("Scoring with model=%s, prompt_version=%s", _get_model(), PROMPT_VERSION)

    # (#5) Fetch market context
    market_ctx = _fetch_market_context()
    session_context = _build_context_string(market_ctx, scan_type)

    # v4.3 D1: Pre-filter zero-edge headlines + F2: Check cache
    items_to_score: list[NewsItem] = []
    pre_filtered_scored: list[ScoredNews] = []
    cached_scored: list[ScoredNews] = []

    for item in news_items:
        # D1: Skip obvious zero-edge headlines
        zero_cat = _is_zero_edge_headline(item.title)
        if zero_cat:
            logger.debug("D1 pre-filter: skipping '%s' (detected as %s)", item.title[:60], zero_cat)
            # Create a minimal ScoredNews with zero-edge scores
            freshness = _compute_freshness(item.published)
            cat_mult = CATEGORY_SCORE_MULTIPLIERS.get(zero_cat, 0.2)
            pre_filtered_scored.append(ScoredNews(
                news=item, surprise=20, freshness=freshness,
                directional_clarity=50, transmission_delay=5,
                market_awareness=95, expected_magnitude=30,
                signal_reliability=90, direction=Direction.NEUTRAL,
                impacted_tickers=item.related_tickers or [],
                reasoning=f"Pre-filtered: {zero_cat} headline — zero edge",
                news_category=zero_cat, category_score_mult=cat_mult,
            ))
            continue

        # F2: Check score cache
        cache_key = _get_cache_key(item.title, item.description)
        cached = _get_cached_score(cache_key)
        if cached is not None:
            logger.debug("F2 cache hit: '%s'", item.title[:60])
            freshness = _compute_freshness(item.published)
            try:
                direction = Direction(cached.get("direction", "NEUTRAL"))
            except ValueError:
                direction = Direction.NEUTRAL
            news_cat = cached.get("news_category", "other")
            cat_mult = CATEGORY_SCORE_MULTIPLIERS.get(news_cat, 0.7)
            # v5.0 O4: Restore accumulation boost from cached entry
            cached_accum = cached.get("_accumulation_boost", 1.0)
            if cached_accum > 1.0:
                cat_mult *= cached_accum
            impacted = cached.get("impacted_tickers", [])
            chain_reactions = _detect_chain_reactions(impacted, direction)
            for cr in chain_reactions:
                if cr.ticker not in impacted:
                    impacted.append(cr.ticker)
            cached_scored.append(ScoredNews(
                news=item,
                surprise=cached.get("surprise", 0),
                freshness=freshness,
                directional_clarity=cached.get("directional_clarity", 0),
                # v5.0 C1: These are now post-hard-cap values from cache
                transmission_delay=cached.get("transmission_delay", 50),
                market_awareness=cached.get("market_awareness", 50),
                expected_magnitude=cached.get("expected_magnitude", 50),
                signal_reliability=cached.get("signal_reliability", 50),
                direction=direction,
                impacted_tickers=impacted,
                reasoning=cached.get("reasoning", ""),
                news_category=news_cat,
                category_score_mult=cat_mult,
                chain_reactions=chain_reactions,
            ))
            continue

        items_to_score.append(item)

    if pre_filtered_scored:
        logger.info("D1: Pre-filtered %d zero-edge headlines (not sent to Claude)",
                     len(pre_filtered_scored))
    if cached_scored:
        logger.info("F2: %d headlines served from cache (not sent to Claude)",
                     len(cached_scored))

    # Score remaining items via Claude
    claude_scored: list[ScoredNews] = []
    if items_to_score:
        # Split into batches if needed
        if len(items_to_score) > SCORING_BATCH_SIZE:
            logger.info("Splitting %d items into batches of %d for Claude scoring",
                         len(items_to_score), SCORING_BATCH_SIZE)

        for batch_start in range(0, len(items_to_score), SCORING_BATCH_SIZE):
            batch_items = items_to_score[batch_start:batch_start + SCORING_BATCH_SIZE]
            batch_scored = _score_batch(client, batch_items, session_context, batch_start)
            claude_scored.extend(batch_scored)

        # v5.0 N5: Increment scan counter once per score_news_batch call (not per batch)
        _scan_token_usage["scans"] += 1

    # Combine all sources
    all_scored = pre_filtered_scored + cached_scored + claude_scored
    all_scored.sort(key=lambda s: s.total_score, reverse=True)

    logger.info("Scored %d/%d news items (claude=%d, cached=%d, prefiltered=%d, VIX=%s, regime=%s)",
                len(all_scored), len(news_items),
                len(claude_scored), len(cached_scored), len(pre_filtered_scored),
                market_ctx.get("vix", "N/A"), market_ctx.get("regime", "N/A"))

    # F4: Log cumulative token usage
    usage = get_token_usage()
    if usage["scans"] > 0:
        logger.info("Token usage (cumulative): input=%d, output=%d, scans=%d",
                     usage["input_tokens"], usage["output_tokens"], usage["scans"])

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
    # v5.0 O6: Sanitize titles/descriptions to mitigate prompt injection from RSS feeds
    headlines = []
    for i, item in enumerate(batch_items):
        age_str = ""
        if item.published:
            age_h = (datetime.now(timezone.utc) - item.published).total_seconds() / 3600
            age_str = f" [il y a {age_h:.1f}h]"
        tickers_str = f" (lie a: {', '.join(item.related_tickers)})" if item.related_tickers else ""
        # Sanitize: strip HTML tags, truncate, remove control chars
        safe_title = re.sub(r'<[^>]+>', '', item.title or "")[:200]
        safe_desc = ""
        if item.description:
            safe_desc = f" | {re.sub(r'<[^>]+>', '', item.description)[:300]}"
        headlines.append(f"{i+1}. {safe_title}{safe_desc}{tickers_str}{age_str}")

    scores = _call_claude_with_retry(client, headlines, session_context)

    if not scores:
        return []

    # v4.0: Build direction map for convergence validation (first pass)
    # v5.0 O5: Also build ticker map with chain reactions for symmetric convergence
    scored_directions: dict[int, Direction] = {}
    scored_tickers: dict[int, list[str]] = {}
    for entry in scores:
        idx = entry.get("index", 0) - 1
        if 0 <= idx < len(batch_items):
            try:
                d = Direction(entry.get("direction", "NEUTRAL"))
                scored_directions[idx] = d
            except ValueError:
                d = Direction.NEUTRAL
                scored_directions[idx] = d
            tickers = list(entry.get("impacted_tickers", []))
            cr = _detect_chain_reactions(tickers, d)
            for c in cr:
                if c.ticker not in tickers:
                    tickers.append(c.ticker)
            scored_tickers[idx] = tickers

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
        # v4.0: Magnitude and reliability dimensions
        expected_magnitude = max(0, min(100, entry.get("expected_magnitude", 50)))
        signal_reliability = max(0, min(100, entry.get("signal_reliability", 50)))

        # v4.3 D3: Use confirmed_event flag when available (replaces fragile keyword matching)
        confirmed_event = entry.get("confirmed_event")

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
                if transmission_delay > 10:
                    logger.info("Central bank rate-decision hard-cap: forcing transmission_delay %d -> 5 for '%s'",
                                transmission_delay, item.title[:60])
                    transmission_delay = 5
                    market_awareness = max(market_awareness, 95)
            elif _is_major_speaker:
                if transmission_delay > 25:
                    logger.info("Central bank major-speech hard-cap: forcing transmission_delay %d -> 25 for '%s'",
                                transmission_delay, item.title[:60])
                    transmission_delay = 25
                market_awareness = max(market_awareness, 70)
            else:
                if transmission_delay > 45:
                    logger.info("Central bank secondary hard-cap: forcing transmission_delay %d -> 45 for '%s'",
                                transmission_delay, item.title[:60])
                    transmission_delay = 45
                market_awareness = max(market_awareness, 50)
        elif news_cat == "m_a":
            # v4.3 D3: Use confirmed_event flag if available, fallback to keywords
            if confirmed_event is not None:
                if confirmed_event:
                    if transmission_delay > 10:
                        logger.info("M&A confirmed (via flag): forcing transmission_delay %d -> 5 for '%s'",
                                    transmission_delay, item.title[:60])
                        transmission_delay = 5
                        market_awareness = max(market_awareness, 90)
                else:
                    if transmission_delay > 40:
                        logger.info("M&A rumor (via flag): capping transmission_delay %d -> 40 for '%s'",
                                    transmission_delay, item.title[:60])
                        transmission_delay = 40
                    market_awareness = max(market_awareness, 50)
            else:
                # Fallback: keyword-based detection
                title_lower = item.title.lower()
                reasoning_lower = entry.get("reasoning", "").lower()
                combined = title_lower + " " + reasoning_lower
                _is_confirmed = any(kw in combined for kw in [
                    "confirms", "confirmed", "announces acquisition", "agrees to buy",
                    "agrees to acquire", "completed acquisition", "merger approved",
                    "deal closed", "takeover complete", "annonce l'acquisition",
                ])
                if _is_confirmed:
                    if transmission_delay > 10:
                        logger.info("M&A confirmed hard-cap: forcing transmission_delay %d -> 5 for '%s'",
                                    transmission_delay, item.title[:60])
                        transmission_delay = 5
                        market_awareness = max(market_awareness, 90)
                else:
                    if transmission_delay > 40:
                        logger.info("M&A rumor hard-cap: forcing transmission_delay %d -> 40 for '%s'",
                                    transmission_delay, item.title[:60])
                        transmission_delay = 40
                    market_awareness = max(market_awareness, 50)

        # v4.3 D2 / v5.1: Cross-dimension coherence validation — now FIXES contradictions
        transmission_delay, market_awareness = _validate_coherence(
            entry, transmission_delay, market_awareness,
            entry.get("surprise", 0), item.title,
        )

        # Apply category score multiplier (edge priority)
        cat_mult = CATEGORY_SCORE_MULTIPLIERS.get(news_cat, 0.7)

        # Detect chain reactions for impacted tickers
        impacted = entry.get("impacted_tickers", [])

        # v3.6 (N): Cross-day signal accumulation boost for persistent physical signals
        accum_boost = _get_signal_accumulation_boost(news_cat, impacted)
        if accum_boost > 1.0:
            cat_mult *= accum_boost
        chain_reactions = _detect_chain_reactions(impacted, direction)
        # Add second-order tickers to impacted list
        for cr in chain_reactions:
            if cr.ticker not in impacted:
                impacted.append(cr.ticker)

        # v3.6: Count independent sources confirming same signal (convergence)
        # v4.0: Pass scored directions to validate direction consistency
        # v5.0 O5: Pass scored_tickers for symmetric convergence detection
        convergence_count = _count_convergence(item, impacted, direction, batch_items, scored_directions, scored_tickers)

        scored.append(ScoredNews(
            news=item,
            surprise=max(0, min(100, entry.get("surprise", 0))),
            freshness=freshness,
            directional_clarity=max(0, min(100, entry.get("directional_clarity", 0))),
            transmission_delay=transmission_delay,
            market_awareness=market_awareness,
            expected_magnitude=expected_magnitude,
            signal_reliability=signal_reliability,
            direction=direction,
            impacted_tickers=impacted,
            reasoning=entry.get("reasoning", ""),
            news_category=news_cat,
            category_score_mult=cat_mult,
            chain_reactions=chain_reactions,
            convergence_count=convergence_count,
        ))

        # v5.0 C1: Cache POST-hard-cap values (not raw Claude output)
        # This ensures cached scores respect hard-caps on earnings/macro/etc.
        post_hardcap_entry = dict(entry)
        post_hardcap_entry["transmission_delay"] = transmission_delay
        post_hardcap_entry["market_awareness"] = market_awareness
        post_hardcap_entry["_accumulation_boost"] = accum_boost
        if confirmed_event is not None:
            post_hardcap_entry["confirmed_event"] = confirmed_event
        cache_key = _get_cache_key(item.title, item.description)
        _set_cached_score(cache_key, post_hardcap_entry)

    if scored and len(scored) < len(batch_items):
        logger.warning("Claude scored only %d/%d items in batch (possible truncation or index mismatch)",
                       len(scored), len(batch_items))

    return scored


def _validate_coherence(entry: dict, transmission_delay: int, market_awareness: int,
                        surprise: int, title: str) -> tuple[int, int]:
    """v4.3 D2: Validate cross-dimension coherence and FIX contradictions.

    v5.1: Now returns corrected (transmission_delay, market_awareness) instead of
    just logging warnings. Prevents Claude's contradictory dimensions from producing
    garbage scores that would corrupt the learning system.

    Catches cases where Claude returns contradictory dimensions:
    - High surprise + low delay = surprise that's already priced? Unlikely.
    - Low awareness + low delay = nobody saw it but it's already priced? Contradictory.
    - High awareness + high delay = everyone saw it but it's not priced? Contradictory.
    """
    fixed_delay = transmission_delay
    fixed_awareness = market_awareness

    if surprise > 70 and transmission_delay < 20:
        logger.warning("D2 coherence FIX: surprise=%d but delay=%d for '%s' — "
                       "raising delay to 40 (surprising news can't be priced)",
                       surprise, transmission_delay, title[:60])
        fixed_delay = max(fixed_delay, 40)
    if market_awareness < 20 and transmission_delay < 20:
        logger.warning("D2 coherence FIX: awareness=%d but delay=%d for '%s' — "
                       "raising delay to 50 (unknown info can't be priced)",
                       market_awareness, transmission_delay, title[:60])
        fixed_delay = max(fixed_delay, 50)
    if market_awareness > 80 and transmission_delay > 60:
        logger.warning("D2 coherence FIX: awareness=%d but delay=%d for '%s' — "
                       "capping delay to 30 (widely known info is priced)",
                       market_awareness, transmission_delay, title[:60])
        fixed_delay = min(fixed_delay, 30)

    return fixed_delay, fixed_awareness


def _count_convergence(
    item: NewsItem,
    impacted_tickers: list[str],
    direction: Direction,
    all_items: list[NewsItem],
    all_scored_directions: dict[int, Direction] | None = None,
    all_scored_tickers: dict[int, list[str]] | None = None,
) -> int:
    """Count independent sources confirming the same signal (I. convergence detection).

    Two items converge if they:
    1. Come from different sources
    2. Impact at least one common ticker (including chain reactions)
    3. Have the same directional implication (not opposing directions)

    v5.0 O5: Uses scored impacted_tickers (with chain reactions) for both
    current and other items, fixing the asymmetry where only the current item
    had chain reaction tickers.

    Returns count of additional confirming sources (0 = no convergence).
    """
    count = 0
    item_source = item.source.lower()
    item_tickers = set(impacted_tickers)

    for idx, other in enumerate(all_items):
        if other is item:
            continue
        if other.source.lower() == item_source:
            continue  # Same source = not independent
        # v5.0 O5: Use scored impacted_tickers (with chain reactions) when available
        if all_scored_tickers and idx in all_scored_tickers:
            other_tickers = set(all_scored_tickers[idx])
        else:
            other_tickers = set(other.related_tickers)
        if item_tickers & other_tickers:
            # v4.0: Verify direction consistency — opposing directions = divergence, not convergence
            if all_scored_directions and idx in all_scored_directions:
                other_dir = all_scored_directions[idx]
                if other_dir != Direction.NEUTRAL and direction != Direction.NEUTRAL:
                    if other_dir != direction:
                        continue  # Opposing direction = not convergence
            count += 1

    return count


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
