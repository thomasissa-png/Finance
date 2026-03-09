"""Agent Scoring 2 — Scoring dédié au trend following (Équipe 2).

Responsabilités :
- Prend les news scorées de Scoring 1 (pas de 2e appel Claude — économie API)
- Re-pondère avec des multiplicateurs trend-spécifiques :
  1. Catégories commodity/weather/supply_chain boostées (impact structurel)
  2. Signal persistence : mots-clés structurels vs événements ponctuels
  3. Magnitude privilegiée vs transmission_delay (trends don't need edge speed)
  4. Accumulation : nombre de news dans la même direction pour un ticker
- Filtre pour les 4 tickers trend (HG=F, CC=F, KC=F, ZW=F)
- Publie trend_scored sur le bus → Trader 2 consomme

Différences avec Scoring 1 :
- Scoring 1 : évalue l'edge intraday (transmission_delay, market_awareness)
- Scoring 2 : évalue la pertinence tendancielle (impact structurel, durée, accumulation)
- NE rappelle PAS Claude — pur re-weighting de Scoring 1
- Catégorie multipliers différents (weather=2.0 vs 1.6 en intraday)

Audit fixes v7.3 (Auditeur + Trader 2 perspectives):
- P1: getattr → direct access on sn.news.description
- P5: Accent-free variants in STRUCTURAL_KEYWORDS (el nino, la nina)
- P6: Convergence count as factor in trend formula
- P7: duration_ms used in bus publish (was dead variable)
- P9: Comment syncing TREND_TICKERS with agent_trader_2.py
- P10: Deterministic top_category on tie (priority order)
- P11: total_rescorings already in get_metrics (confirmed)
- P12: max_trend_score added to stats
- T3: Freshness weight in accumulation (recent news weighted more)
- T4: Description included in trend_scored items
- T5: Chain discount 0.7 for chain-reaction tickers in accumulation
- T7: Min trend score raised to 8.0 with named constant

Expertise incarnée :
- 15+ ans spéculation tendance commodities
- Comprend que l'impact structurel (sécheresse, gel, embargo) > timing d'edge
- Sait que les signaux s'accumulent dans une tendance
"""

import logging
import time
from datetime import datetime, timezone

from .base import BaseAgent, AgentStatus

logger = logging.getLogger(__name__)

# ── Trend-specific category multipliers ──────────────────────────
# Different from Scoring 1 — optimized for structural commodity impact
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

# P10: Priority order for tie-breaking top_category
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
    # P5: Accent-free variants (ASCII text from RSS/API often lacks accents)
    "el nino": 1.3, "la nina": 1.3, "secheresse": 1.5,
    # Team 2 audit: copper-specific keywords
    "smelter": 1.4, "concentrate": 1.3, "treatment charges": 1.3,
    "mine closure": 1.5, "mine shutdown": 1.5,
    # Team 2 audit: cocoa-specific keywords
    "swollen shoot": 1.5, "black pod": 1.4, "harmattan": 1.3,
    "main crop": 1.1, "mid-crop": 1.1, "grindings": 1.3,
    "certified stocks": 1.4, "warehouse stocks": 1.4,
    # Team 2 audit: coffee-specific keywords
    "robusta": 1.2, "arabica": 1.2, "coffee rust": 1.4,
    "leaf rust": 1.4, "roya": 1.4,  # Spanish name for coffee leaf rust
    "safrinha": 1.2,  # Brazil second crop
    # Team 2 audit: wheat-specific keywords
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

# T7: Minimum trend score threshold (raised from 5.0)
MIN_TREND_SCORE = 8.0

# T5: Chain reaction discount (consistent with Trader 2 fallback 0.7 factor)
CHAIN_DISCOUNT = 0.7


import re

# Precompile word-boundary patterns for structural keywords to avoid substring
# false positives (e.g. "ban" must not match "banana", "demand" not "commandeered")
_STRUCTURAL_PATTERNS: dict[str, tuple[re.Pattern, float]] = {}
for _kw, _mult in STRUCTURAL_KEYWORDS.items():
    _STRUCTURAL_PATTERNS[_kw] = (re.compile(r'\b' + re.escape(_kw) + r'\b', re.IGNORECASE), _mult)


def _compute_persistence_mult(title: str, description: str = "") -> float:
    """Evaluate how structurally persistent a news signal is.

    Structural events (drought, embargo) → higher multiplier.
    Temporary/speculative events → lower multiplier.
    Uses word-boundary matching to avoid false positives.
    """
    text = title + " " + (description or "")
    best_mult = 1.0
    for keyword, (pattern, mult) in _STRUCTURAL_PATTERNS.items():
        if pattern.search(text):
            best_mult = max(best_mult, mult)
    return best_mult


def _compute_trend_score(scored_news, category_mult: float,
                         persistence_mult: float,
                         convergence_count: int = 0) -> float:
    """Compute trend-weighted score from a ScoredNews object.

    Trend formula (different from intraday):
    trend_score = surprise * (clarity/100) * magnitude_factor
                  * reliability_factor * category_mult * persistence_mult
                  * convergence_boost * source_weight

    Key differences vs intraday:
    - NO transmission_delay/market_awareness in formula
      (trends don't need edge speed — structural impact matters)
    - magnitude_factor BOOSTED (0.5 + 0.5 * mag/100, floor 0.5)
    - persistence_mult from structural keywords
    - Different category multipliers
    - P6: convergence_boost from multiple independent sources
    """
    surprise = scored_news.surprise
    clarity = scored_news.directional_clarity / 100
    magnitude = scored_news.expected_magnitude
    reliability = scored_news.signal_reliability

    # Magnitude factor — trends care MORE about amplitude
    magnitude_factor = 0.5 + 0.5 * (magnitude / 100)

    # Reliability factor — same floor as intraday
    reliability_factor = 0.4 + 0.6 * (reliability / 100)

    # P6: Convergence boost — multiple sources confirming = stronger signal
    # Max +30% boost (3 sources), diminishing returns
    convergence_boost = 1.0 + min(convergence_count, 3) * 0.1

    # Source weight from original news
    source_weight = scored_news.news.source_weight

    score = (surprise * clarity * magnitude_factor * reliability_factor
             * category_mult * persistence_mult * convergence_boost
             * source_weight)

    return round(score, 1)


def score_for_trend(scored_news_list: list) -> dict:
    """Re-score news for trend relevance on the 4 commodity tickers.

    Args:
        scored_news_list: list[ScoredNews] from Scoring 1

    Returns dict with:
        - trend_scored: list[dict] — all trend-relevant scored items
        - by_ticker: dict[ticker, list[dict]] — grouped by impacted ticker
        - accumulation: dict[ticker, {long: float, short: float}] — directional signal sum
        - stats: {total_items, relevant_items, avg_trend_score, max_trend_score, top_category}
    """
    result = {
        "trend_scored": [],
        "by_ticker": {},
        "accumulation": {},
        "stats": {
            "total_items": len(scored_news_list),
            "relevant_items": 0,
            "avg_trend_score": 0.0,
            "max_trend_score": 0.0,
            "top_category": "",
        },
    }

    if not scored_news_list:
        return result

    # Initialize accumulation
    for ticker in TREND_TICKERS:
        result["accumulation"][ticker] = {"long": 0.0, "short": 0.0}
        result["by_ticker"][ticker] = []

    trend_scores = []
    now = datetime.now(timezone.utc)

    for sn in scored_news_list:
        # Skip NEUTRAL
        if sn.direction.value == "NEUTRAL":
            continue

        # Check if any trend ticker is impacted (direct vs chain)
        direct_trend = set()
        chain_trend = set()
        chain_directions = {}

        for ticker in sn.impacted_tickers:
            if ticker in TREND_TICKERS:
                direct_trend.add(ticker)

        # Also check chain reactions
        for cr in (sn.chain_reactions or []):
            if cr.ticker in TREND_TICKERS:
                chain_trend.add(cr.ticker)
                chain_directions[cr.ticker] = cr.direction.value

        impacted_trend = direct_trend | chain_trend
        if not impacted_trend:
            continue

        # Compute trend-specific score
        cat_mult = TREND_CATEGORY_MULTS.get(sn.news_category, 0.6)
        # P1: Direct access — description is a declared field on NewsItem with default ""
        description = sn.news.description or ""
        persistence_mult = _compute_persistence_mult(sn.news.title, description)
        # P6: Pass convergence_count to the formula
        trend_score = _compute_trend_score(
            sn, cat_mult, persistence_mult,
            convergence_count=sn.convergence_count,
        )

        # T7: Named constant for min threshold
        if trend_score < MIN_TREND_SCORE:
            continue

        # T4: Include description for Journal 2 / frontend context
        item = {
            "title": sn.news.title[:200],
            "description": (sn.news.description or "")[:200],
            "source": sn.news.source,
            "direction": sn.direction.value,
            "news_category": sn.news_category,
            "original_score": round(sn.total_score, 1),
            "trend_score": trend_score,
            "category_mult": cat_mult,
            "persistence_mult": persistence_mult,
            "convergence_count": sn.convergence_count,
            "surprise": sn.surprise,
            "magnitude": sn.expected_magnitude,
            "reliability": sn.signal_reliability,
            "clarity": sn.directional_clarity,
            "impacted_tickers": list(impacted_trend),
        }

        result["trend_scored"].append(item)
        trend_scores.append(trend_score)

        # T3: Compute freshness weight — recent news weighted more in accumulation
        # News < 2h = weight 1.0, news 8h = weight 0.5, older = 0.3 floor
        freshness_weight = 1.0
        if sn.news.published:
            try:
                age_hours = (now - sn.news.published).total_seconds() / 3600
                freshness_weight = max(0.3, 1.0 - age_hours / 16.0)
            except (TypeError, AttributeError):
                pass

        # Group by ticker and accumulate
        for ticker in impacted_trend:
            result["by_ticker"][ticker].append(item)

            # Determine direction for this ticker
            if ticker in direct_trend:
                direction = sn.direction.value
            elif ticker in chain_directions:
                direction = chain_directions[ticker]
            else:
                direction = sn.direction.value

            # T5: Chain discount — chain reaction tickers get 0.7 weight
            # (consistent with Trader 2 fallback factor)
            chain_factor = CHAIN_DISCOUNT if (ticker in chain_trend and ticker not in direct_trend) else 1.0

            # Accumulate weighted signal (T3: with freshness, T5: with chain discount)
            weight = trend_score * (sn.signal_reliability / 100) * freshness_weight * chain_factor
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
        # P12: Max trend score for distribution analysis
        result["stats"]["max_trend_score"] = round(max(trend_scores), 1)

    # P10: Top contributing category — deterministic on tie (priority order)
    cat_counts: dict[str, int] = {}
    for item in result["trend_scored"]:
        cat = item["news_category"]
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    if cat_counts:
        max_count = max(cat_counts.values())
        # Among tied categories, pick the one with highest priority
        for cat in _CATEGORY_PRIORITY:
            if cat_counts.get(cat, 0) == max_count:
                result["stats"]["top_category"] = cat
                break
        else:
            # Fallback if category not in priority list
            result["stats"]["top_category"] = max(cat_counts, key=cat_counts.get)

    return result


class AgentScoring2(BaseAgent):
    """Agent Scoring 2 — Re-scoring trend-spécifique pour Équipe 2.

    Ne rappelle PAS Claude — prend la sortie de Scoring 1 et applique
    des multiplicateurs différents optimisés pour le trend following.
    """

    name = "scoring_2"
    description = "Scoring tendance — re-pondération pour commodities"
    version = "7.5"  # v7.5: +25 structural keywords (copper, cocoa, coffee, wheat specifics)

    def __init__(self):
        super().__init__()
        self._last_relevant_count: int = 0
        self._last_avg_score: float = 0.0
        self._total_rescorings: int = 0
        self._last_accumulation: dict = {}
        self._last_result: dict | None = None

    def run(self, scored_news=None, scan_type=None, **kwargs) -> dict:
        """Re-score news for trend relevance.

        Args:
            scored_news: list[ScoredNews] from Agent Scoring 1
            scan_type: ScanType enum (for context)

        Returns dict with trend-scored results.
        """
        self._set_status(AgentStatus.WORKING,
                         f"Re-scoring {len(scored_news or [])} news for trend")

        start = time.monotonic()

        try:
            # Step 1: Compute trend scores
            trend_data = self.execute(
                "Computing trend scores",
                score_for_trend,
                scored_news or [],
            )

            self._last_result = trend_data
            self._total_rescorings += 1
            self._last_relevant_count = trend_data["stats"]["relevant_items"]
            self._last_avg_score = trend_data["stats"]["avg_trend_score"]
            self._last_accumulation = trend_data.get("accumulation", {})

            # Step 2: Log results
            relevant = trend_data["stats"]["relevant_items"]
            total = trend_data["stats"]["total_items"]
            avg_score = trend_data["stats"]["avg_trend_score"]

            self.log(f"Trend scoring: {relevant}/{total} relevant", {
                "relevant_items": relevant,
                "avg_trend_score": avg_score,
                "max_trend_score": trend_data["stats"].get("max_trend_score", 0),
                "top_category": trend_data["stats"].get("top_category", ""),
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

            # Step 3: Publish (P7: include duration_ms)
            duration_ms = int((time.monotonic() - start) * 1000)
            self.publish("trend_scored", {
                "relevant_items": relevant,
                "avg_trend_score": avg_score,
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
        return {
            "last_relevant_count": self._last_relevant_count,
            "last_avg_score": self._last_avg_score,
            "total_rescorings": self._total_rescorings,
            "accumulation": {
                t: {"net": round(v["long"] - v["short"], 1)}
                for t, v in self._last_accumulation.items()
            },
        }
