"""Agent Scoring 4 — Meta-scorer combining signals from Teams 1, 2, 3 (Équipe 4).

Responsabilités :
- Reads outputs from Scoring 1 (news edge), Scoring 2 (trend accumulation),
  Scoring 3 (technical indicators)
- Combines them into unified meta-scores per ticker
- Configurable weighting per source (news, trend, tech)
- Confluence detection: when multiple teams agree on direction → signal boost
- Publishes "meta_scored" on bus → Trader 4 consomme
- Only activates when upstream teams have enough data (graceful degradation)

Différences avec les autres scorers :
- Scoring 1 : évalue l'edge intraday (transmission_delay, market_awareness)
- Scoring 2 : évalue la pertinence tendancielle (impact structurel, durée)
- Scoring 4 : combine les 3 perspectives en un meta-score unifié
- NE rappelle PAS Claude — pur aggregation/weighting des signaux upstream

Expertise incarnée :
- 15+ ans quantitative portfolio management
- Multi-factor model combining fundamentals, technicals, and sentiment
- Confluence detection across independent signal sources
"""

import logging
import time
from datetime import datetime, timezone

from .base import BaseAgent, AgentStatus

logger = logging.getLogger(__name__)

# ── Configurable source weights ───────────────────────────────────
# These determine how much each team's signal contributes to the meta-score.
# Sum does NOT need to equal 1.0 — they are relative weights.
NEWS_WEIGHT = 0.35       # Team 1: News edge scoring
TREND_WEIGHT = 0.25      # Team 2: Trend accumulation
TECH_WEIGHT = 0.40       # Team 3: Technical indicators

# Minimum data thresholds — scoring 4 only activates when upstream
# teams have produced enough data to be meaningful
MIN_DATA_THRESHOLD = 3   # Min items from each source to activate
MIN_CONFLUENCE_BOOST = 1.2   # 2/3 teams agree → 20% boost
MAX_CONFLUENCE_BOOST = 1.5   # 3/3 teams agree → 50% boost

# Minimum meta-score to include in output
MIN_META_SCORE = 5.0


def _normalize_direction(direction: str) -> str:
    """Normalize direction string to LONG/SHORT/NEUTRAL."""
    d = direction.upper().strip()
    if d in ("LONG", "SHORT", "NEUTRAL"):
        return d
    return "NEUTRAL"


def _compute_confluence(directions: list[str]) -> tuple[str, int, float]:
    """Determine confluence direction, level, and boost from team signals.

    Args:
        directions: list of direction strings from each team (up to 3)

    Returns:
        (consensus_direction, confluence_level, boost_multiplier)
        - confluence_level: 0 = no agreement, 2 = two agree, 3 = all agree
        - boost_multiplier: 1.0 (no confluence) to MAX_CONFLUENCE_BOOST
    """
    active = [d for d in directions if d != "NEUTRAL"]
    if not active:
        return "NEUTRAL", 0, 1.0

    long_count = sum(1 for d in active if d == "LONG")
    short_count = sum(1 for d in active if d == "SHORT")

    if long_count > short_count:
        consensus = "LONG"
        agree_count = long_count
    elif short_count > long_count:
        consensus = "SHORT"
        agree_count = short_count
    else:
        # Tie — no clear confluence
        consensus = active[0]  # Take first signal
        agree_count = 1

    total_sources = len(active)
    if total_sources >= 3 and agree_count >= 3:
        return consensus, 3, MAX_CONFLUENCE_BOOST
    elif total_sources >= 2 and agree_count >= 2:
        return consensus, 2, MIN_CONFLUENCE_BOOST
    else:
        return consensus, 1, 1.0


def compute_meta_scores(
    news_data: dict | None = None,
    trend_data: dict | None = None,
    tech_data: dict | None = None,
    weights: dict | None = None,
) -> dict:
    """Combine signals from Teams 1, 2, 3 into unified meta-scores per ticker.

    Args:
        news_data: Scoring 1 output — dict with scored news items
            Expected keys: "scored_news" (list of dicts with ticker, score, direction)
        trend_data: Scoring 2 output — dict with trend accumulation
            Expected keys: "accumulation" (dict[ticker, {long, short}]),
                          "trend_scored" (list of dicts)
        tech_data: Scoring 3 output — dict with technical signals
            Expected keys: "signals" (dict[ticker, {score, direction}])
        weights: Optional override for source weights

    Returns dict with:
        - meta_scored: list[dict] — all meta-scored items per ticker
        - by_ticker: dict[ticker, dict] — meta-score details per ticker
        - confluence_summary: dict — overall confluence statistics
        - stats: {total_tickers, avg_meta_score, max_confluence_level, sources_active}
    """
    w_news = (weights or {}).get("news", NEWS_WEIGHT)
    w_trend = (weights or {}).get("trend", TREND_WEIGHT)
    w_tech = (weights or {}).get("tech", TECH_WEIGHT)

    result = {
        "meta_scored": [],
        "by_ticker": {},
        "confluence_summary": {
            "level_3_count": 0,
            "level_2_count": 0,
            "level_1_count": 0,
            "level_0_count": 0,
        },
        "stats": {
            "total_tickers": 0,
            "avg_meta_score": 0.0,
            "max_meta_score": 0.0,
            "max_confluence_level": 0,
            "sources_active": 0,
        },
    }

    # ── Collect per-ticker signals from each team ──
    # Each signal: {score: float, direction: str, source: str, details: dict}
    ticker_signals: dict[str, dict[str, dict]] = {}

    sources_active = 0

    # Team 1: News edge signals
    news_items = []
    if news_data:
        news_items = news_data.get("scored_news", [])
        if not news_items and isinstance(news_data, list):
            news_items = news_data

    if len(news_items) >= MIN_DATA_THRESHOLD:
        sources_active += 1
        for item in news_items:
            # Support both ScoredNews-like dicts and flat dicts
            tickers = item.get("impacted_tickers", [])
            if isinstance(tickers, str):
                tickers = [tickers]
            direction = _normalize_direction(
                item.get("direction", "NEUTRAL")
                if isinstance(item.get("direction"), str)
                else getattr(item.get("direction", "NEUTRAL"), "value", "NEUTRAL")
            )
            score = item.get("total_score", 0) or item.get("score", 0) or 0

            for ticker in tickers:
                ticker_signals.setdefault(ticker, {})
                # Keep highest news score per ticker
                existing = ticker_signals[ticker].get("news")
                if existing is None or score > existing["score"]:
                    ticker_signals[ticker]["news"] = {
                        "score": score,
                        "direction": direction,
                        "source": "news",
                        "details": {
                            "category": item.get("news_category", ""),
                            "reliability": item.get("signal_reliability", 0),
                        },
                    }

    # Team 2: Trend accumulation signals
    if trend_data and isinstance(trend_data, dict):
        accumulation = trend_data.get("accumulation", {})
        trend_items = trend_data.get("trend_scored", [])
        if len(trend_items) >= MIN_DATA_THRESHOLD or len(accumulation) >= 1:
            sources_active += 1
            for ticker, acc in accumulation.items():
                long_val = acc.get("long", 0)
                short_val = acc.get("short", 0)
                net = long_val - short_val
                if abs(net) < 1.0:
                    continue
                direction = "LONG" if net > 0 else "SHORT"
                # Normalize to 0-100 scale (accumulation can be large)
                score = min(100, abs(net))
                ticker_signals.setdefault(ticker, {})
                ticker_signals[ticker]["trend"] = {
                    "score": score,
                    "direction": direction,
                    "source": "trend",
                    "details": {
                        "long_acc": round(long_val, 1),
                        "short_acc": round(short_val, 1),
                        "net": round(net, 1),
                    },
                }

    # Team 3: Technical signals
    if tech_data and isinstance(tech_data, dict):
        tech_signals = tech_data.get("signals", {})
        if len(tech_signals) >= MIN_DATA_THRESHOLD:
            sources_active += 1
            for ticker, sig in tech_signals.items():
                score = sig.get("score", 0)
                direction = _normalize_direction(sig.get("direction", "NEUTRAL"))
                if score < 1.0 or direction == "NEUTRAL":
                    continue
                ticker_signals.setdefault(ticker, {})
                ticker_signals[ticker]["tech"] = {
                    "score": score,
                    "direction": direction,
                    "source": "tech",
                    "details": sig.get("details", {}),
                }

    result["stats"]["sources_active"] = sources_active

    if not ticker_signals:
        return result

    # ── Compute meta-score per ticker ──
    meta_scores = []
    max_confluence = 0

    for ticker, signals in ticker_signals.items():
        # Gather directions from each source present
        directions = []
        weighted_score = 0.0
        total_weight = 0.0
        source_details = {}

        if "news" in signals:
            s = signals["news"]
            directions.append(s["direction"])
            weighted_score += s["score"] * w_news
            total_weight += w_news
            source_details["news"] = {
                "score": round(s["score"], 1),
                "direction": s["direction"],
                **s["details"],
            }

        if "trend" in signals:
            s = signals["trend"]
            directions.append(s["direction"])
            weighted_score += s["score"] * w_trend
            total_weight += w_trend
            source_details["trend"] = {
                "score": round(s["score"], 1),
                "direction": s["direction"],
                **s["details"],
            }

        if "tech" in signals:
            s = signals["tech"]
            directions.append(s["direction"])
            weighted_score += s["score"] * w_tech
            total_weight += w_tech
            source_details["tech"] = {
                "score": round(s["score"], 1),
                "direction": s["direction"],
                **s["details"],
            }

        if total_weight == 0:
            continue

        # Normalize weighted score
        base_meta_score = weighted_score / total_weight

        # Compute confluence
        consensus_dir, confluence_level, confluence_boost = _compute_confluence(directions)
        meta_score = round(base_meta_score * confluence_boost, 1)
        max_confluence = max(max_confluence, confluence_level)

        # Track confluence levels
        level_key = f"level_{confluence_level}_count"
        result["confluence_summary"][level_key] = (
            result["confluence_summary"].get(level_key, 0) + 1
        )

        if meta_score < MIN_META_SCORE:
            continue

        item = {
            "ticker": ticker,
            "meta_score": meta_score,
            "base_score": round(base_meta_score, 1),
            "direction": consensus_dir,
            "confluence_level": confluence_level,
            "confluence_boost": confluence_boost,
            "sources_contributing": len(signals),
            "source_details": source_details,
        }

        result["meta_scored"].append(item)
        result["by_ticker"][ticker] = item
        meta_scores.append(meta_score)

    # Sort by meta_score descending
    result["meta_scored"].sort(key=lambda x: x["meta_score"], reverse=True)

    # Stats
    result["stats"]["total_tickers"] = len(meta_scores)
    if meta_scores:
        result["stats"]["avg_meta_score"] = round(
            sum(meta_scores) / len(meta_scores), 1
        )
        result["stats"]["max_meta_score"] = round(max(meta_scores), 1)
    result["stats"]["max_confluence_level"] = max_confluence

    return result


class AgentScoring4(BaseAgent):
    """Agent Scoring 4 — Meta-scorer combining signals from Teams 1, 2, 3.

    Aggregates independent scoring perspectives into unified meta-scores
    with confluence detection. Does NOT call Claude — pure aggregation.
    """

    name = "scoring_4"
    description = "Meta-scoring — combines news, trend, and technical signals"
    version = "1.0"

    def __init__(self):
        super().__init__()
        self._last_ticker_count: int = 0
        self._last_avg_score: float = 0.0
        self._last_max_confluence: int = 0
        self._total_computations: int = 0
        self._last_sources_active: int = 0
        self._last_result: dict | None = None

    def run(self, news_data=None, trend_data=None, tech_data=None,
            weights=None, **kwargs) -> dict:
        """Combine upstream signals into meta-scores.

        Args:
            news_data: Scoring 1 output (scored news list or dict)
            trend_data: Scoring 2 output (trend accumulation dict)
            tech_data: Scoring 3 output (technical signals dict)
            weights: Optional weight overrides {news, trend, tech}

        Returns dict with meta-scored results.
        """
        source_counts = {
            "news": len(news_data) if isinstance(news_data, list) else len((news_data or {}).get("scored_news", [])),
            "trend": len((trend_data or {}).get("trend_scored", [])),
            "tech": len((tech_data or {}).get("signals", {})),
        }
        self._set_status(
            AgentStatus.WORKING,
            f"Computing meta-scores (news={source_counts['news']}, "
            f"trend={source_counts['trend']}, tech={source_counts['tech']})"
        )

        start = time.monotonic()

        try:
            # Step 1: Compute meta-scores
            meta_data = self.execute(
                "Computing meta-scores (3 sources)",
                compute_meta_scores,
                news_data, trend_data, tech_data, weights,
            )

            self._last_result = meta_data
            self._total_computations += 1
            self._last_ticker_count = meta_data["stats"]["total_tickers"]
            self._last_avg_score = meta_data["stats"]["avg_meta_score"]
            self._last_max_confluence = meta_data["stats"]["max_confluence_level"]
            self._last_sources_active = meta_data["stats"]["sources_active"]

            # Step 2: Log results
            stats = meta_data["stats"]
            confluence = meta_data["confluence_summary"]

            self.log(f"Meta-scoring: {stats['total_tickers']} tickers scored", {
                "total_tickers": stats["total_tickers"],
                "avg_meta_score": stats["avg_meta_score"],
                "max_meta_score": stats.get("max_meta_score", 0),
                "max_confluence_level": stats["max_confluence_level"],
                "sources_active": stats["sources_active"],
                "confluence_3": confluence.get("level_3_count", 0),
                "confluence_2": confluence.get("level_2_count", 0),
            })

            # Log high-confluence signals
            for item in meta_data["meta_scored"]:
                if item["confluence_level"] >= 2:
                    self.log_decision(f"Confluence signal {item['ticker']}", {
                        "ticker": item["ticker"],
                        "meta_score": item["meta_score"],
                        "direction": item["direction"],
                        "confluence_level": item["confluence_level"],
                        "sources": item["sources_contributing"],
                        "source_details": item["source_details"],
                    })

            # Step 3: Publish
            duration_ms = int((time.monotonic() - start) * 1000)
            self.publish("meta_scored", {
                "total_tickers": stats["total_tickers"],
                "avg_meta_score": stats["avg_meta_score"],
                "max_confluence_level": stats["max_confluence_level"],
                "sources_active": stats["sources_active"],
                "top_signals": [
                    {"ticker": i["ticker"], "score": i["meta_score"],
                     "dir": i["direction"], "confl": i["confluence_level"]}
                    for i in meta_data["meta_scored"][:5]
                ],
                "duration_ms": duration_ms,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            self._set_status(
                AgentStatus.IDLE,
                f"{stats['total_tickers']} tickers, "
                f"max confluence {stats['max_confluence_level']}/3"
            )

            return meta_data

        except Exception as exc:
            self.log("Meta-scoring failed", {"error": str(exc)}, level="ERROR")
            self._set_status(AgentStatus.ERROR, str(exc))
            raise

    def get_last_result(self) -> dict | None:
        """Get the last meta-scoring result (for API/frontend)."""
        return self._last_result

    def get_metrics(self) -> dict:
        return {
            "last_ticker_count": self._last_ticker_count,
            "last_avg_score": self._last_avg_score,
            "last_max_confluence": self._last_max_confluence,
            "last_sources_active": self._last_sources_active,
            "total_computations": self._total_computations,
        }
