"""Agent Learning 2 — Learning dédié à l'Agent Trader 2 (trend following).

Responsabilités :
- Consomme les completed periods de Journal 2
- Calcule 4 dimensions de learning adaptées au trend following :
  1. Per-ticker : quels tickers trend bien vs choppy ?
  2. Per-newscat : quelles catégories de news produisent de bons flips ?
  3. Per-direction : LONG vs SHORT accuracy
  4. Signal strength calibration : le seuil de flip est-il bien calibré ?
- Publie les ajustements sur le bus → Trader 2 les consomme
- Détecte les anomalies (churning, drawdowns, flips perdants)

Différences avec Learning 1 :
- Learning 1 : apprend des trades intraday (TP/SL/EXPIRED) sur 41 actifs
- Learning 2 : apprend des positions de tendance (flips) sur 4 commodities
- Dimensions différentes : pas de session_adj (24/7 relevant), pas de delay_bias
- Focus sur la qualité des retournements, pas sur le timing d'entrée

Expertise incarnée :
- 10+ ans ML : significance testing sur petits échantillons
- Calibration de seuils adaptatifs (flip threshold)
- Protection contre le churning (trop de flips = signal noise)
"""

import logging
import time
from datetime import datetime, timezone

from .base import BaseAgent, AgentStatus

logger = logging.getLogger(__name__)

# Minimum completed periods needed for significance
MIN_PERIODS_TICKER = 4    # Per-ticker (only 4 tickers, need small sample)
MIN_PERIODS_NEWSCAT = 3   # Per-newscat
MIN_PERIODS_DIRECTION = 4  # Per-direction
MIN_PERIODS_GLOBAL = 6    # For global metrics

# Adjustment bounds
ADJ_MIN = 0.6
ADJ_MAX = 1.4


def _clamp(value: float, lo: float = ADJ_MIN, hi: float = ADJ_MAX) -> float:
    return max(lo, min(hi, value))


def compute_trend_learning(entries: list[dict]) -> dict:
    """Compute learning adjustments from completed trend periods.

    Args:
        entries: List of journal 2 entries (completed flips)

    Returns dict with:
        - ticker_adj: {ticker: multiplier}
        - newscat_adj: {newscat: multiplier}
        - direction_adj: {LONG: mult, SHORT: mult}
        - signal_calibration: {threshold_adj: float, avg_strength: float}
        - anomalies: [str]
        - stats: {global metrics}
    """
    result = {
        "ticker_adj": {},
        "newscat_adj": {},
        "direction_adj": {},
        "signal_calibration": {"threshold_adj": 1.0, "avg_strength": 0.0},
        "anomalies": [],
        "stats": {},
    }

    if not entries:
        return result

    # Filter out NEUTRAL entries (initial positions)
    valid = [e for e in entries if e.get("direction") not in ("NEUTRAL", None)]
    if len(valid) < MIN_PERIODS_GLOBAL:
        result["stats"] = {"total_periods": len(valid), "sufficient_data": False}
        return result

    # ── Global stats ──
    total_pnl = sum(e.get("pnl_pct", 0) for e in valid)
    wins = [e for e in valid if (e.get("pnl_pct") or 0) > 0]
    losses = [e for e in valid if (e.get("pnl_pct") or 0) <= 0]
    win_rate = len(wins) / len(valid) * 100 if valid else 0
    avg_pnl = total_pnl / len(valid) if valid else 0
    avg_mae = sum(e.get("mae_pct", 0) for e in valid) / len(valid) if valid else 0
    avg_mfe = sum(e.get("mfe_pct", 0) for e in valid) / len(valid) if valid else 0

    result["stats"] = {
        "total_periods": len(valid),
        "sufficient_data": True,
        "win_rate": round(win_rate, 1),
        "avg_pnl_pct": round(avg_pnl, 2),
        "total_pnl_pct": round(total_pnl, 2),
        "avg_mae_pct": round(avg_mae, 2),
        "avg_mfe_pct": round(avg_mfe, 2),
        "wins": len(wins),
        "losses": len(losses),
    }

    # ── 1. Per-ticker adjustments ──
    ticker_groups: dict[str, list[float]] = {}
    for e in valid:
        ticker = e.get("ticker", "")
        pnl = e.get("pnl_pct", 0)
        ticker_groups.setdefault(ticker, []).append(pnl)

    for ticker, pnls in ticker_groups.items():
        if len(pnls) >= MIN_PERIODS_TICKER:
            ticker_avg = sum(pnls) / len(pnls)
            ticker_wr = sum(1 for p in pnls if p > 0) / len(pnls)
            # Adjustment: 1.0 + capped signal based on win rate and avg P&L
            # Win rate > 50% AND positive avg → boost
            # Win rate < 40% AND negative avg → penalize
            signal = (ticker_wr - 0.5) * 2  # -1 to +1
            pnl_signal = max(-0.3, min(0.3, ticker_avg / 5.0))
            adj = 1.0 + (signal * 0.15) + (pnl_signal * 0.5)
            result["ticker_adj"][ticker] = round(_clamp(adj), 3)

    # ── 2. Per-newscat adjustments ──
    newscat_groups: dict[str, list[float]] = {}
    for e in valid:
        for cat in e.get("news_categories", ["other"]):
            pnl = e.get("pnl_pct", 0)
            newscat_groups.setdefault(cat, []).append(pnl)

    for cat, pnls in newscat_groups.items():
        if len(pnls) >= MIN_PERIODS_NEWSCAT:
            cat_avg = sum(pnls) / len(pnls)
            cat_wr = sum(1 for p in pnls if p > 0) / len(pnls)
            signal = (cat_wr - 0.5) * 2
            pnl_signal = max(-0.3, min(0.3, cat_avg / 5.0))
            adj = 1.0 + (signal * 0.15) + (pnl_signal * 0.5)
            result["newscat_adj"][cat] = round(_clamp(adj), 3)

    # ── 3. Per-direction adjustments ──
    dir_groups: dict[str, list[float]] = {}
    for e in valid:
        direction = e.get("direction", "")
        pnl = e.get("pnl_pct", 0)
        dir_groups.setdefault(direction, []).append(pnl)

    for direction, pnls in dir_groups.items():
        if len(pnls) >= MIN_PERIODS_DIRECTION:
            dir_avg = sum(pnls) / len(pnls)
            dir_wr = sum(1 for p in pnls if p > 0) / len(pnls)
            signal = (dir_wr - 0.5) * 2
            pnl_signal = max(-0.2, min(0.2, dir_avg / 5.0))
            adj = 1.0 + (signal * 0.1) + (pnl_signal * 0.5)
            result["direction_adj"][direction] = round(_clamp(adj, 0.8, 1.2), 3)

    # ── 4. Signal strength calibration ──
    strengths = [e.get("signal_strength", 0) for e in valid if e.get("signal_strength")]
    if strengths:
        avg_strength = sum(strengths) / len(strengths)
        result["signal_calibration"]["avg_strength"] = round(avg_strength, 1)

        # Check if high-strength flips perform better
        median_strength = sorted(strengths)[len(strengths) // 2]
        high_strength = [e for e in valid if (e.get("signal_strength") or 0) >= median_strength]
        low_strength = [e for e in valid if (e.get("signal_strength") or 0) < median_strength]

        if high_strength and low_strength:
            high_wr = sum(1 for e in high_strength if (e.get("pnl_pct") or 0) > 0) / len(high_strength)
            low_wr = sum(1 for e in low_strength if (e.get("pnl_pct") or 0) > 0) / len(low_strength)

            if high_wr > low_wr + 0.1:
                # Strong signals are better → slightly raise threshold
                result["signal_calibration"]["threshold_adj"] = 1.05
            elif low_wr > high_wr + 0.1:
                # Weak signals are just as good → lower threshold
                result["signal_calibration"]["threshold_adj"] = 0.95

    # ── 5. Anomaly detection ──
    anomalies = result["anomalies"]

    # Churning: too many flips on a ticker in recent period
    for ticker, pnls in ticker_groups.items():
        if len(pnls) >= 8 and sum(1 for p in pnls if p > 0) / len(pnls) < 0.3:
            anomalies.append(
                f"CHURNING {ticker}: {len(pnls)} flips, WR={sum(1 for p in pnls if p > 0)/len(pnls)*100:.0f}%"
            )

    # Consecutive losses
    consecutive_losses = 0
    max_consecutive = 0
    for e in valid:
        if (e.get("pnl_pct") or 0) <= 0:
            consecutive_losses += 1
            max_consecutive = max(max_consecutive, consecutive_losses)
        else:
            consecutive_losses = 0
    if max_consecutive >= 3:
        anomalies.append(f"STREAK: {max_consecutive} pertes consécutives")

    # MAE alert: if average MAE is much worse than average loss
    if avg_mae < -5:
        anomalies.append(f"MAE élevé: {avg_mae:.1f}% — positions subissent des drawdowns importants")

    # Win rate alert
    if win_rate < 35 and len(valid) >= MIN_PERIODS_GLOBAL:
        anomalies.append(f"Win rate faible: {win_rate:.0f}% sur {len(valid)} périodes")

    return result


class AgentLearning2(BaseAgent):
    """Agent Learning 2 — Learning dédié au trend following (Trader 2).

    Apprend des completed periods pour ajuster les signaux de Trader 2.
    """

    name = "learning_2"
    description = "Learning & optimisation — trend commodities"

    def __init__(self):
        super().__init__()
        self._last_adjustment_count: int = 0
        self._last_anomalies: list = []
        self._total_recalculations: int = 0
        self._cached_adjustments: dict | None = None
        self._cache_valid: bool = False

    def run(self, **kwargs) -> dict:
        """Recalculate trend learning dimensions and publish adjustments.

        Called after Journal 2 runs.
        Returns dict with learning results.
        """
        self._set_status(AgentStatus.WORKING, "Recalculating trend learning")

        start = time.monotonic()
        result = {
            "adjustments": {},
            "anomalies": [],
            "stats": {},
            "dimensions_updated": 0,
        }

        try:
            # Step 1: Load journal 2 entries
            self.log("Loading trend journal entries for learning")
            from .agent_journal_2 import _load_journal_entries
            entries = _load_journal_entries()

            # Step 2: Compute adjustments
            learning_data = self.execute(
                "Computing trend learning (4 dimensions)",
                compute_trend_learning,
                entries,
            )

            self._cached_adjustments = learning_data
            self._cache_valid = True
            self._total_recalculations += 1

            # Step 3: Process results
            ticker_adj = learning_data.get("ticker_adj", {})
            newscat_adj = learning_data.get("newscat_adj", {})
            direction_adj = learning_data.get("direction_adj", {})
            signal_cal = learning_data.get("signal_calibration", {})
            anomalies = learning_data.get("anomalies", [])
            stats = learning_data.get("stats", {})

            self._last_adjustment_count = (
                len(ticker_adj) + len(newscat_adj) + len(direction_adj)
            )
            self._last_anomalies = anomalies

            dims_updated = sum([
                len(ticker_adj) > 0,
                len(newscat_adj) > 0,
                len(direction_adj) > 0,
                signal_cal.get("threshold_adj", 1.0) != 1.0,
            ])

            result["adjustments"] = learning_data
            result["anomalies"] = anomalies
            result["stats"] = stats
            result["dimensions_updated"] = dims_updated

            # Step 4: Log
            self.log("Trend learning computed", {
                "ticker_adj": ticker_adj,
                "newscat_adj": newscat_adj,
                "direction_adj": direction_adj,
                "signal_calibration": signal_cal,
                "stats": stats,
                "dimensions_active": dims_updated,
            })

            if anomalies:
                self.log("Anomalies detected", {
                    "count": len(anomalies),
                    "anomalies": anomalies,
                }, level="WARN")

            # Log significant adjustments
            significant = {}
            for k, v in ticker_adj.items():
                if abs(v - 1.0) > 0.1:
                    significant[k] = v
            for k, v in newscat_adj.items():
                if abs(v - 1.0) > 0.1:
                    significant[f"cat:{k}"] = v
            if significant:
                self.log_decision("Significant trend adjustments", significant)

            # Step 5: Publish
            self.publish("learning_2_updated", {
                "adjustment_count": self._last_adjustment_count,
                "dimensions_active": dims_updated,
                "anomaly_count": len(anomalies),
                "total_periods": stats.get("total_periods", 0),
                "win_rate": stats.get("win_rate", 0),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            self._set_status(
                AgentStatus.IDLE,
                f"{self._last_adjustment_count} adjustments, {len(anomalies)} anomalies"
            )

            return result

        except Exception as exc:
            self.log("Trend learning failed", {"error": str(exc)}, level="ERROR")
            self._set_status(AgentStatus.ERROR, str(exc))
            raise

    def get_adjustments(self) -> dict:
        """Get cached trend learning adjustments (used by Trader 2).

        Recalculates if cache is invalid.
        """
        if not self._cache_valid or self._cached_adjustments is None:
            from .agent_journal_2 import _load_journal_entries
            entries = _load_journal_entries()
            self._cached_adjustments = compute_trend_learning(entries)
            self._cache_valid = True
        return self._cached_adjustments

    def invalidate_cache(self):
        """Invalidate the learning cache (called after journal 2)."""
        self._cache_valid = False
        self.log("Trend learning cache invalidated")

    def get_metrics(self) -> dict:
        return {
            "last_adjustment_count": self._last_adjustment_count,
            "anomalies": self._last_anomalies[:5],
            "total_recalculations": self._total_recalculations,
            "cache_valid": self._cache_valid,
        }
