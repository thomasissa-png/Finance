"""Agent Learning 3 — Learning for Agent Trader 3 (Technical Indicators Trading).

Responsibilities:
- Consumes closed trade entries from Journal 3
- Calculates 5 learning dimensions adapted for technical trading:
  1. Per-strategy: which indicator combos work best (A/B comparison)
  2. Per-ticker: which assets respond well to technical signals
  3. Per-timeframe: which timeframes are most reliable
  4. Per-market-regime: trending vs ranging (ADX-based)
  5. AB-test: compares strategy variants, identifies winners
- Publishes adjustments on bus -> Trader 3 consumes
- Detects anomalies (overtrading, strategy degradation, regime shifts)

Differences with Learning 1/2:
- Learning 1: learns from intraday trades (TP/SL/EXPIRED), 6 dimensions, news-oriented
- Learning 2: learns from trend flips on 4 commodities, 4 dimensions
- Learning 3: learns from multi-strategy technical trades, 5 dimensions,
  includes A/B testing framework for strategy comparison
"""

import logging
import math
import time
from datetime import datetime, timezone, timedelta

from .base import BaseAgent, AgentStatus

logger = logging.getLogger(__name__)

# Minimum closed trades for significance
MIN_TRADES_STRATEGY = 5   # Per-strategy (need enough per variant)
MIN_TRADES_TICKER = 6     # Per-ticker
MIN_TRADES_TIMEFRAME = 5  # Per-timeframe
MIN_TRADES_REGIME = 8     # Per-regime (trending/ranging)
MIN_TRADES_AB = 10        # For A/B comparison (per variant)
MIN_TRADES_GLOBAL = 10    # For global metrics

# Adjustment bounds
ADJ_MIN = 0.6
ADJ_MAX = 1.4

# Temporal decay half-life in days
DECAY_HALF_LIFE_DAYS = 30  # Shorter than trend (30 vs 45) — technical signals evolve faster

# ADX threshold for regime classification
ADX_TRENDING_THRESHOLD = 25.0


def _clamp(value: float, lo: float = ADJ_MIN, hi: float = ADJ_MAX) -> float:
    return max(lo, min(hi, value))


def _compute_decay_weight(entry: dict, now: datetime) -> float:
    """Compute exponential decay weight based on entry age."""
    close_time = entry.get("close_time") or entry.get("entry_time", "")
    if not close_time:
        return 1.0
    try:
        entry_dt = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
        age_days = (now - entry_dt).total_seconds() / 86400
        if age_days < 0:
            return 1.0
        return math.exp(-0.693 * age_days / DECAY_HALF_LIFE_DAYS)
    except (ValueError, AttributeError, TypeError):
        return 1.0


def _is_significant(pnls: list[float], min_effect_size: float = 0.1) -> bool:
    """Pseudo t-test for significance on small samples."""
    n = len(pnls)
    if n < 2:
        return False
    mean = sum(pnls) / n
    if abs(mean) < min_effect_size:
        return False
    variance = sum((p - mean) ** 2 for p in pnls) / (n - 1)
    if variance == 0:
        return abs(mean) >= min_effect_size
    stderr = math.sqrt(variance / n)
    if stderr == 0:
        return abs(mean) >= min_effect_size
    t_stat = abs(mean / stderr)
    return t_stat > 1.5


def compute_tech_learning(entries: list[dict]) -> dict:
    """Compute learning adjustments from closed technical trades.

    5 dimensions:
    1. Per-strategy: adjustment per strategy_id
    2. Per-ticker: adjustment per ticker
    3. Per-timeframe: adjustment per timeframe
    4. Per-market-regime: trending vs ranging (ADX at entry)
    5. AB-test: comparative analysis of strategies

    Args:
        entries: List of journal 3 entries (closed trades)

    Returns dict with:
        - strategy_adj: {strategy: multiplier}
        - ticker_adj: {ticker: multiplier}
        - timeframe_adj: {timeframe: multiplier}
        - regime_adj: {trending: mult, ranging: mult}
        - ab_test: {strategy: {trades, win_rate, avg_pnl, rank}}
        - anomalies: [str]
        - stats: {global metrics}
    """
    result = {
        "strategy_adj": {},
        "ticker_adj": {},
        "timeframe_adj": {},
        "regime_adj": {},
        "ab_test": {},
        "anomalies": [],
        "stats": {},
    }

    if not entries:
        return result

    # Filter valid entries (with result)
    valid = [
        e for e in entries
        if e.get("result") in ("TP_HIT", "SL_HIT", "EXPIRED")
    ]

    # Sort chronologically
    valid.sort(key=lambda e: e.get("close_time") or e.get("entry_time", ""))

    if len(valid) < MIN_TRADES_GLOBAL:
        result["stats"] = {"total_trades": len(valid), "sufficient_data": False}
        return result

    # Compute decay weights
    now = datetime.now(timezone.utc)
    weights = [_compute_decay_weight(e, now) for e in valid]

    # ── Global stats ──
    total_pnl = sum(e.get("pnl_pct", 0) for e in valid)
    wins = [e for e in valid if (e.get("pnl_pct") or 0) > 0]
    losses = [e for e in valid if (e.get("pnl_pct") or 0) <= 0]
    win_rate = len(wins) / len(valid) * 100 if valid else 0
    avg_pnl = total_pnl / len(valid) if valid else 0

    tp_hits = sum(1 for e in valid if e.get("result") == "TP_HIT")
    sl_hits = sum(1 for e in valid if e.get("result") == "SL_HIT")
    expired = sum(1 for e in valid if e.get("result") == "EXPIRED")

    mae_values = [e.get("mae_pct") for e in valid if e.get("mae_pct") is not None]
    mfe_values = [e.get("mfe_pct") for e in valid if e.get("mfe_pct") is not None]
    avg_mae = sum(mae_values) / len(mae_values) if mae_values else 0
    avg_mfe = sum(mfe_values) / len(mfe_values) if mfe_values else 0

    result["stats"] = {
        "total_trades": len(valid),
        "sufficient_data": True,
        "win_rate": round(win_rate, 1),
        "avg_pnl_pct": round(avg_pnl, 2),
        "total_pnl_pct": round(total_pnl, 2),
        "tp_hits": tp_hits,
        "sl_hits": sl_hits,
        "expired": expired,
        "avg_mae_pct": round(avg_mae, 2),
        "avg_mfe_pct": round(avg_mfe, 2),
        "wins": len(wins),
        "losses": len(losses),
    }

    # ── Helper: weighted adjustment computation ──
    def _compute_adj(pnl_weight_pairs: list[tuple[float, float]],
                     sensitivity: float = 0.15,
                     pnl_cap: float = 0.3,
                     lo: float = ADJ_MIN, hi: float = ADJ_MAX) -> float | None:
        """Compute adjustment from weighted (pnl, weight) pairs."""
        if not pnl_weight_pairs:
            return None
        total_w = sum(w for _, w in pnl_weight_pairs)
        if total_w == 0:
            return None

        w_wins = sum(w for p, w in pnl_weight_pairs if p > 0)
        w_wr = w_wins / total_w
        w_avg_pnl = sum(p * w for p, w in pnl_weight_pairs) / total_w

        raw_pnls = [p for p, _ in pnl_weight_pairs]
        if not _is_significant(raw_pnls):
            return None

        signal = (w_wr - 0.5) * 2  # -1 to +1
        pnl_signal = max(-pnl_cap, min(pnl_cap, w_avg_pnl / 5.0))
        adj = 1.0 + (signal * sensitivity) + (pnl_signal * 0.5)
        return round(_clamp(adj, lo, hi), 3)

    # ── 1. Per-strategy adjustments ──
    strategy_groups: dict[str, list[tuple[float, float]]] = {}
    for e, w in zip(valid, weights):
        strategy = e.get("strategy", "unknown")
        pnl = e.get("pnl_pct", 0)
        strategy_groups.setdefault(strategy, []).append((pnl, w))

    for strategy, pairs in strategy_groups.items():
        if len(pairs) >= MIN_TRADES_STRATEGY:
            adj = _compute_adj(pairs)
            if adj is not None:
                result["strategy_adj"][strategy] = adj

    # ── 2. Per-ticker adjustments ──
    ticker_groups: dict[str, list[tuple[float, float]]] = {}
    for e, w in zip(valid, weights):
        ticker = e.get("ticker", "")
        pnl = e.get("pnl_pct", 0)
        ticker_groups.setdefault(ticker, []).append((pnl, w))

    for ticker, pairs in ticker_groups.items():
        if len(pairs) >= MIN_TRADES_TICKER:
            adj = _compute_adj(pairs)
            if adj is not None:
                result["ticker_adj"][ticker] = adj

    # ── 3. Per-timeframe adjustments ──
    tf_groups: dict[str, list[tuple[float, float]]] = {}
    for e, w in zip(valid, weights):
        tf = e.get("timeframe", "1d")
        pnl = e.get("pnl_pct", 0)
        tf_groups.setdefault(tf, []).append((pnl, w))

    for tf, pairs in tf_groups.items():
        if len(pairs) >= MIN_TRADES_TIMEFRAME:
            adj = _compute_adj(pairs)
            if adj is not None:
                result["timeframe_adj"][tf] = adj

    # ── 4. Per-market-regime adjustments (ADX-based) ──
    regime_groups: dict[str, list[tuple[float, float]]] = {}
    for e, w in zip(valid, weights):
        adx = e.get("adx_at_entry")
        pnl = e.get("pnl_pct", 0)
        if adx is not None:
            regime = "trending" if adx >= ADX_TRENDING_THRESHOLD else "ranging"
        else:
            regime = "unknown"
        regime_groups.setdefault(regime, []).append((pnl, w))

    for regime, pairs in regime_groups.items():
        if regime == "unknown":
            continue
        if len(pairs) >= MIN_TRADES_REGIME:
            adj = _compute_adj(pairs, sensitivity=0.1, pnl_cap=0.2, lo=0.8, hi=1.2)
            if adj is not None:
                result["regime_adj"][regime] = adj

    # ── 5. A/B test — strategy comparison ──
    ab_test: dict[str, dict] = {}
    for strategy, pairs in strategy_groups.items():
        n = len(pairs)
        if n < 3:
            continue
        wins = sum(1 for p, _ in pairs if p > 0)
        total_pnl_strat = sum(p for p, _ in pairs)
        ab_test[strategy] = {
            "trades": n,
            "wins": wins,
            "win_rate": round(wins / n * 100, 1),
            "avg_pnl": round(total_pnl_strat / n, 2),
            "total_pnl": round(total_pnl_strat, 2),
        }

    # Rank strategies by score (win_rate * 0.4 + avg_pnl_normalized * 0.6)
    if ab_test:
        max_avg = max(abs(s["avg_pnl"]) for s in ab_test.values()) or 1
        for strategy, stats in ab_test.items():
            score = stats["win_rate"] * 0.4 + (stats["avg_pnl"] / max_avg * 50 + 50) * 0.6
            stats["ab_score"] = round(score, 1)

        # Assign ranks
        ranked = sorted(ab_test.items(), key=lambda x: x[1]["ab_score"], reverse=True)
        for rank, (strategy, stats) in enumerate(ranked, 1):
            stats["rank"] = rank

        # Identify winner and loser
        if len(ranked) >= 2:
            winner = ranked[0]
            loser = ranked[-1]
            if (winner[1]["trades"] >= MIN_TRADES_AB
                    and loser[1]["trades"] >= MIN_TRADES_AB):
                if winner[1]["win_rate"] - loser[1]["win_rate"] > 15:
                    result["anomalies"].append(
                        f"AB_WINNER: {winner[0]} (WR={winner[1]['win_rate']}%) "
                        f"vs {loser[0]} (WR={loser[1]['win_rate']}%)"
                    )

    result["ab_test"] = ab_test

    # ── 6. Anomaly detection ──
    anomalies = result["anomalies"]

    # Strategy degradation: any strategy with WR < 30% and enough trades
    for strategy, pairs in strategy_groups.items():
        n = len(pairs)
        if n >= MIN_TRADES_STRATEGY:
            wr = sum(1 for p, _ in pairs if p > 0) / n
            if wr < 0.3:
                anomalies.append(
                    f"STRATEGY_DEGRADED {strategy}: {n} trades, WR={wr*100:.0f}%"
                )

    # Overtrading: too many trades per day (if we can compute from timestamps)
    if len(valid) >= 20:
        # Count trades per day
        day_counts: dict[str, int] = {}
        for e in valid:
            ct = e.get("close_time") or e.get("entry_time", "")
            if ct:
                day = ct[:10]
                day_counts[day] = day_counts.get(day, 0) + 1
        if day_counts:
            avg_per_day = sum(day_counts.values()) / len(day_counts)
            if avg_per_day > 5:
                anomalies.append(
                    f"OVERTRADING: avg {avg_per_day:.1f} trades/day over {len(day_counts)} days"
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
    if max_consecutive >= 5:
        anomalies.append(f"STREAK: {max_consecutive} consecutive losses")

    # High expired rate
    if len(valid) >= MIN_TRADES_GLOBAL:
        expired_rate = expired / len(valid) * 100
        if expired_rate > 40:
            anomalies.append(
                f"HIGH_EXPIRED: {expired_rate:.0f}% of trades expired "
                f"— holding period or targets may need calibration"
            )

    # MAE alert
    if avg_mae < -3:
        anomalies.append(
            f"HIGH_MAE: avg {avg_mae:.1f}% — stops may be too wide"
        )

    # Win rate alert
    if win_rate < 35 and len(valid) >= MIN_TRADES_GLOBAL:
        anomalies.append(
            f"LOW_WIN_RATE: {win_rate:.0f}% on {len(valid)} trades"
        )

    return result


class AgentLearning3(BaseAgent):
    """Agent Learning 3 — Learning for technical trading (Trader 3).

    Learns from closed trades to adjust per-strategy, per-ticker,
    per-timeframe, and per-regime multipliers. Includes A/B testing
    framework for strategy comparison.
    """

    name = "learning_3"
    description = "Learning & A/B testing — technical trading strategies"
    version = "1.0"

    def __init__(self):
        super().__init__()
        self._last_adjustment_count: int = 0
        self._last_anomalies: list = []
        self._total_recalculations: int = 0
        self._cached_adjustments: dict | None = None
        self._cache_valid: bool = False

    def run(self, **kwargs) -> dict:
        """Recalculate technical learning dimensions and publish adjustments.

        Called after Journal 3 runs.
        Returns dict with learning results.
        """
        self._set_status(AgentStatus.WORKING, "Recalculating tech learning")

        start = time.monotonic()
        result = {
            "adjustments": {},
            "anomalies": [],
            "stats": {},
            "ab_test": {},
            "dimensions_updated": 0,
        }

        try:
            # Step 1: Load journal 3 entries
            self.log("Loading tech journal entries for learning")
            from .agent_journal_3 import _load_journal_entries
            entries = _load_journal_entries()

            # Step 2: Compute adjustments
            learning_data = self.execute(
                "Computing tech learning (5 dimensions)",
                compute_tech_learning,
                entries,
            )

            self._cached_adjustments = learning_data
            self._cache_valid = True
            self._total_recalculations += 1

            # Step 3: Process results
            strategy_adj = learning_data.get("strategy_adj", {})
            ticker_adj = learning_data.get("ticker_adj", {})
            timeframe_adj = learning_data.get("timeframe_adj", {})
            regime_adj = learning_data.get("regime_adj", {})
            ab_test = learning_data.get("ab_test", {})
            anomalies = learning_data.get("anomalies", [])
            stats = learning_data.get("stats", {})

            self._last_adjustment_count = (
                len(strategy_adj) + len(ticker_adj)
                + len(timeframe_adj) + len(regime_adj)
            )
            self._last_anomalies = anomalies

            dims_updated = sum([
                len(strategy_adj) > 0,
                len(ticker_adj) > 0,
                len(timeframe_adj) > 0,
                len(regime_adj) > 0,
                len(ab_test) > 0,
            ])

            result["adjustments"] = learning_data
            result["anomalies"] = anomalies
            result["stats"] = stats
            result["ab_test"] = ab_test
            result["dimensions_updated"] = dims_updated

            # Step 4: Log
            self.log("Tech learning computed", {
                "strategy_adj": strategy_adj,
                "ticker_adj": ticker_adj,
                "timeframe_adj": timeframe_adj,
                "regime_adj": regime_adj,
                "stats": stats,
                "dimensions_active": dims_updated,
            })

            if ab_test:
                self.log("A/B test results", {
                    "strategies": {
                        k: {"wr": v["win_rate"], "pnl": v["avg_pnl"], "rank": v.get("rank")}
                        for k, v in ab_test.items()
                    },
                })

            if anomalies:
                self.log("Anomalies detected", {
                    "count": len(anomalies),
                    "anomalies": anomalies,
                }, level="WARN")

            # Log significant adjustments
            significant = {}
            for k, v in strategy_adj.items():
                if abs(v - 1.0) > 0.1:
                    significant[f"strat:{k}"] = v
            for k, v in ticker_adj.items():
                if abs(v - 1.0) > 0.1:
                    significant[f"ticker:{k}"] = v
            if significant:
                self.log_decision("Significant tech adjustments", significant)

            # Step 5: Publish
            self.publish("learning_3_updated", {
                "adjustment_count": self._last_adjustment_count,
                "dimensions_active": dims_updated,
                "anomaly_count": len(anomalies),
                "total_trades": stats.get("total_trades", 0),
                "win_rate": stats.get("win_rate", 0),
                "ab_winner": (
                    min(ab_test.items(), key=lambda x: x[1].get("rank", 99))[0]
                    if ab_test else None
                ),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            self._set_status(
                AgentStatus.IDLE,
                f"{self._last_adjustment_count} adjustments, {len(anomalies)} anomalies"
            )

            return result

        except Exception as exc:
            self.log("Tech learning failed", {"error": str(exc)}, level="ERROR")
            self._set_status(AgentStatus.ERROR, str(exc))
            raise

    def get_adjustments(self) -> dict:
        """Get cached tech learning adjustments (used by Trader 3).

        Recalculates if cache is invalid.
        """
        if not self._cache_valid or self._cached_adjustments is None:
            from .agent_journal_3 import _load_journal_entries
            entries = _load_journal_entries()
            self._cached_adjustments = compute_tech_learning(entries)
            self._cache_valid = True
        return self._cached_adjustments

    def invalidate_cache(self):
        """Invalidate the learning cache (called after journal 3)."""
        self._cache_valid = False
        self.log("Tech learning cache invalidated")

    def get_ab_report(self) -> dict:
        """Get the latest A/B test comparison report."""
        adj = self.get_adjustments()
        return adj.get("ab_test", {})

    def get_metrics(self) -> dict:
        return {
            "last_adjustment_count": self._last_adjustment_count,
            "anomalies": self._last_anomalies[:5],
            "total_recalculations": self._total_recalculations,
            "cache_valid": self._cache_valid,
        }
