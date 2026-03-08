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
    # Medium persistence
    "inventory": 1.2, "stockpile": 1.2, "reserves": 1.2,
    "production cut": 1.3, "opec": 1.2, "output": 1.1,
    "demand": 1.1, "import": 1.1, "export": 1.1,
    # Low persistence (temporary events)
    "forecast": 0.9, "outlook": 0.9, "estimate": 0.9,
    "rumor": 0.7, "speculation": 0.7, "talks": 0.8,
}

# Tickers tracked by Trader 2
TREND_TICKERS = {"HG=F", "CC=F", "KC=F", "ZW=F"}


def _compute_persistence_mult(title: str, description: str = "") -> float:
    """Evaluate how structurally persistent a news signal is.

    Structural events (drought, embargo) → higher multiplier.
    Temporary/speculative events → lower multiplier.
    """
    text = (title + " " + (description or "")).lower()
    best_mult = 1.0
    for keyword, mult in STRUCTURAL_KEYWORDS.items():
        if keyword in text:
            best_mult = max(best_mult, mult)
    return best_mult


def _compute_trend_score(scored_news, category_mult: float,
                         persistence_mult: float) -> float:
    """Compute trend-weighted score from a ScoredNews object.

    Trend formula (different from intraday):
    trend_score = surprise * (clarity/100) * magnitude_factor
                  * reliability_factor * category_mult * persistence_mult
                  * source_weight

    Key differences vs intraday:
    - NO transmission_delay/market_awareness in formula
      (trends don't need edge speed — structural impact matters)
    - magnitude_factor BOOSTED (0.5 + 0.5 * mag/100, floor 0.5)
    - persistence_mult from structural keywords
    - Different category multipliers
    """
    surprise = scored_news.surprise
    clarity = scored_news.directional_clarity / 100
    magnitude = scored_news.expected_magnitude
    reliability = scored_news.signal_reliability

    # Magnitude factor — trends care MORE about amplitude
    magnitude_factor = 0.5 + 0.5 * (magnitude / 100)

    # Reliability factor — same floor as intraday
    reliability_factor = 0.4 + 0.6 * (reliability / 100)

    # Source weight from original news
    source_weight = scored_news.news.source_weight

    score = (surprise * clarity * magnitude_factor * reliability_factor
             * category_mult * persistence_mult * source_weight)

    return round(score, 1)


def score_for_trend(scored_news_list: list) -> dict:
    """Re-score news for trend relevance on the 4 commodity tickers.

    Args:
        scored_news_list: list[ScoredNews] from Scoring 1

    Returns dict with:
        - trend_scored: list[dict] — all trend-relevant scored items
        - by_ticker: dict[ticker, list[dict]] — grouped by impacted ticker
        - accumulation: dict[ticker, {long: float, short: float}] — directional signal sum
        - stats: {total_items, relevant_items, avg_trend_score}
    """
    result = {
        "trend_scored": [],
        "by_ticker": {},
        "accumulation": {},
        "stats": {
            "total_items": len(scored_news_list),
            "relevant_items": 0,
            "avg_trend_score": 0.0,
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

    for sn in scored_news_list:
        # Skip NEUTRAL
        if sn.direction.value == "NEUTRAL":
            continue

        # Check if any trend ticker is impacted
        impacted_trend = set()
        for ticker in sn.impacted_tickers:
            if ticker in TREND_TICKERS:
                impacted_trend.add(ticker)

        # Also check chain reactions
        chain_directions = {}
        for cr in (sn.chain_reactions or []):
            if cr.ticker in TREND_TICKERS:
                impacted_trend.add(cr.ticker)
                chain_directions[cr.ticker] = cr.direction.value

        if not impacted_trend:
            continue

        # Compute trend-specific score
        cat_mult = TREND_CATEGORY_MULTS.get(sn.news_category, 0.6)
        description = getattr(sn.news, "description", "") or ""
        persistence_mult = _compute_persistence_mult(sn.news.title, description)
        trend_score = _compute_trend_score(sn, cat_mult, persistence_mult)

        if trend_score < 5.0:  # Minimum threshold for trend relevance
            continue

        item = {
            "title": sn.news.title[:200],
            "source": sn.news.source,
            "direction": sn.direction.value,
            "news_category": sn.news_category,
            "original_score": round(sn.total_score, 1),
            "trend_score": trend_score,
            "category_mult": cat_mult,
            "persistence_mult": persistence_mult,
            "surprise": sn.surprise,
            "magnitude": sn.expected_magnitude,
            "reliability": sn.signal_reliability,
            "clarity": sn.directional_clarity,
            "impacted_tickers": list(impacted_trend),
        }

        result["trend_scored"].append(item)
        trend_scores.append(trend_score)

        # Group by ticker and accumulate
        for ticker in impacted_trend:
            result["by_ticker"][ticker].append(item)

            # Determine direction for this ticker
            if ticker in sn.impacted_tickers:
                direction = sn.direction.value
            elif ticker in chain_directions:
                direction = chain_directions[ticker]
            else:
                direction = sn.direction.value

            # Accumulate weighted signal
            weight = trend_score * (sn.signal_reliability / 100)
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
    # Top contributing category
    cat_counts: dict[str, int] = {}
    for item in result["trend_scored"]:
        cat = item["news_category"]
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    if cat_counts:
        result["stats"]["top_category"] = max(cat_counts, key=cat_counts.get)

    return result


class AgentScoring2(BaseAgent):
    """Agent Scoring 2 — Re-scoring trend-spécifique pour Équipe 2.

    Ne rappelle PAS Claude — prend la sortie de Scoring 1 et applique
    des multiplicateurs différents optimisés pour le trend following.
    """

    name = "scoring_2"
    description = "Scoring tendance — re-pondération pour commodities"

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

            # Step 3: Publish
            self.publish("trend_scored", {
                "relevant_items": relevant,
                "avg_trend_score": avg_score,
                "accumulation": {
                    t: {"net": round(v["long"] - v["short"], 1)}
                    for t, v in trend_data.get("accumulation", {}).items()
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            duration_ms = int((time.monotonic() - start) * 1000)
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
