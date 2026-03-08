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
import math
import time
from datetime import datetime, timezone, timedelta

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

# P1: Temporal decay — half-life in days
DECAY_HALF_LIFE_DAYS = 45


def _clamp(value: float, lo: float = ADJ_MIN, hi: float = ADJ_MAX) -> float:
    return max(lo, min(hi, value))


def _compute_decay_weight(entry: dict, now: datetime) -> float:
    """P1: Compute exponential decay weight based on entry age.

    Recent entries weight ~1.0, entries at half_life weight ~0.5.
    """
    entry_time = entry.get("exit_time") or entry.get("entry_time", "")
    if not entry_time:
        return 1.0
    try:
        entry_dt = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
        age_days = (now - entry_dt).total_seconds() / 86400
        if age_days < 0:
            return 1.0
        return math.exp(-0.693 * age_days / DECAY_HALF_LIFE_DAYS)  # ln(2) ≈ 0.693
    except (ValueError, AttributeError, TypeError):
        return 1.0


def _is_significant(pnls: list[float], min_effect_size: float = 0.1) -> bool:
    """P2: Pseudo t-test for significance on small samples.

    Returns True if the mean PnL is significantly different from zero.
    """
    n = len(pnls)
    if n < 2:
        return False
    mean = sum(pnls) / n
    # Check minimum effect size
    if abs(mean) < min_effect_size:
        return False
    # Compute sample std dev
    variance = sum((p - mean) ** 2 for p in pnls) / (n - 1)
    if variance == 0:
        return abs(mean) >= min_effect_size
    stderr = math.sqrt(variance / n)
    if stderr == 0:
        return abs(mean) >= min_effect_size
    t_stat = abs(mean / stderr)
    # Use relaxed threshold for small samples (t > 1.5)
    return t_stat > 1.5


def compute_trend_learning(entries: list[dict]) -> dict:
    """Compute learning adjustments from completed trend periods.

    Audit fixes applied:
    - P1: Temporal decay (recent entries weighted more)
    - P2: Significance testing (pseudo t-test)
    - P5: Proportional signal calibration (not binary)
    - P6: Newscat deduplicated per entry (primary category only)
    - P7: Cross-dimension ticker×newscat
    - P8: Entries sorted chronologically before streak detection

    Args:
        entries: List of journal 2 entries (completed flips)

    Returns dict with:
        - ticker_adj: {ticker: multiplier}
        - newscat_adj: {newscat: multiplier}
        - newscat_ticker_adj: {newscat+ticker: multiplier}  (P7)
        - direction_adj: {LONG: mult, SHORT: mult}
        - signal_calibration: {threshold_adj: float, avg_strength: float}
        - anomalies: [str]
        - stats: {global metrics}
    """
    result = {
        "ticker_adj": {},
        "newscat_adj": {},
        "newscat_ticker_adj": {},
        "direction_adj": {},
        "signal_calibration": {"threshold_adj": 1.0, "avg_strength": 0.0},
        "anomalies": [],
        "stats": {},
    }

    if not entries:
        return result

    # Filter out NEUTRAL entries (initial positions)
    valid = [e for e in entries if e.get("direction") not in ("NEUTRAL", None)]

    # P8: Sort chronologically by exit_time (or entry_time fallback)
    valid.sort(key=lambda e: e.get("exit_time") or e.get("entry_time", ""))

    if len(valid) < MIN_PERIODS_GLOBAL:
        result["stats"] = {"total_periods": len(valid), "sufficient_data": False}
        return result

    # P1: Compute decay weights
    now = datetime.now(timezone.utc)
    weights = [_compute_decay_weight(e, now) for e in valid]

    # ── Global stats ──
    total_pnl = sum(e.get("pnl_pct", 0) for e in valid)
    wins = [e for e in valid if (e.get("pnl_pct") or 0) > 0]
    losses = [e for e in valid if (e.get("pnl_pct") or 0) <= 0]
    win_rate = len(wins) / len(valid) * 100 if valid else 0
    avg_pnl = total_pnl / len(valid) if valid else 0
    # L3: Filter None MAE/MFE — only average entries with actual bar data
    mae_values = [e.get("mae_pct") for e in valid if e.get("mae_pct") is not None]
    mfe_values = [e.get("mfe_pct") for e in valid if e.get("mfe_pct") is not None]
    avg_mae = sum(mae_values) / len(mae_values) if mae_values else 0
    avg_mfe = sum(mfe_values) / len(mfe_values) if mfe_values else 0

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

    # ── Helper: weighted adjustment computation ──
    def _compute_adj(pnl_weight_pairs: list[tuple[float, float]],
                     sensitivity: float = 0.15,
                     pnl_cap: float = 0.3,
                     lo: float = ADJ_MIN, hi: float = ADJ_MAX) -> float | None:
        """Compute adjustment from weighted (pnl, weight) pairs.

        P1: Uses decay weights. P2: Checks significance.
        Returns None if not significant.
        """
        if not pnl_weight_pairs:
            return None
        total_w = sum(w for _, w in pnl_weight_pairs)
        if total_w == 0:
            return None
        # Weighted win rate
        w_wins = sum(w for p, w in pnl_weight_pairs if p > 0)
        w_wr = w_wins / total_w
        # Weighted avg PnL
        w_avg_pnl = sum(p * w for p, w in pnl_weight_pairs) / total_w

        # P2: Check significance on raw pnls
        raw_pnls = [p for p, _ in pnl_weight_pairs]
        if not _is_significant(raw_pnls):
            return None

        signal = (w_wr - 0.5) * 2  # -1 to +1
        pnl_signal = max(-pnl_cap, min(pnl_cap, w_avg_pnl / 5.0))
        adj = 1.0 + (signal * sensitivity) + (pnl_signal * 0.5)
        return round(_clamp(adj, lo, hi), 3)

    # ── 1. Per-ticker adjustments ──
    ticker_groups: dict[str, list[tuple[float, float]]] = {}
    for e, w in zip(valid, weights):
        ticker = e.get("ticker", "")
        pnl = e.get("pnl_pct", 0)
        ticker_groups.setdefault(ticker, []).append((pnl, w))

    for ticker, pairs in ticker_groups.items():
        if len(pairs) >= MIN_PERIODS_TICKER:
            adj = _compute_adj(pairs)
            if adj is not None:
                result["ticker_adj"][ticker] = adj

    # ── 2. Per-newscat adjustments (P6: primary category only) ──
    newscat_groups: dict[str, list[tuple[float, float]]] = {}
    for e, w in zip(valid, weights):
        # P6: Use only the first/primary category to avoid double-counting
        cats = e.get("news_categories", ["other"])
        primary_cat = cats[0] if cats else "other"
        pnl = e.get("pnl_pct", 0)
        newscat_groups.setdefault(primary_cat, []).append((pnl, w))

    for cat, pairs in newscat_groups.items():
        if len(pairs) >= MIN_PERIODS_NEWSCAT:
            adj = _compute_adj(pairs)
            if adj is not None:
                result["newscat_adj"][cat] = adj

    # ── 2b. P7: Cross-dimension ticker×newscat ──
    cross_groups: dict[str, list[tuple[float, float]]] = {}
    for e, w in zip(valid, weights):
        ticker = e.get("ticker", "")
        cats = e.get("news_categories", ["other"])
        primary_cat = cats[0] if cats else "other"
        pnl = e.get("pnl_pct", 0)
        key = f"{primary_cat}+{ticker}"
        cross_groups.setdefault(key, []).append((pnl, w))

    for key, pairs in cross_groups.items():
        # Need at least 3 samples for cross-dimension
        if len(pairs) >= 3:
            adj = _compute_adj(pairs)
            if adj is not None:
                result["newscat_ticker_adj"][key] = adj

    # ── 3. Per-direction adjustments ──
    dir_groups: dict[str, list[tuple[float, float]]] = {}
    for e, w in zip(valid, weights):
        direction = e.get("direction", "")
        pnl = e.get("pnl_pct", 0)
        dir_groups.setdefault(direction, []).append((pnl, w))

    for direction, pairs in dir_groups.items():
        if len(pairs) >= MIN_PERIODS_DIRECTION:
            adj = _compute_adj(pairs, sensitivity=0.1, pnl_cap=0.2, lo=0.8, hi=1.2)
            if adj is not None:
                result["direction_adj"][direction] = adj

    # ── 4. Signal strength calibration (P5: proportional) ──
    strengths = [e.get("signal_strength", 0) for e in valid if e.get("signal_strength")]
    if strengths:
        avg_strength = sum(strengths) / len(strengths)
        result["signal_calibration"]["avg_strength"] = round(avg_strength, 1)

        # P5: Proportional calibration based on WR gap between high/low strength
        median_strength = sorted(strengths)[len(strengths) // 2]
        high_strength = [e for e in valid if (e.get("signal_strength") or 0) >= median_strength]
        low_strength = [e for e in valid if (e.get("signal_strength") or 0) < median_strength]

        if len(high_strength) >= 3 and len(low_strength) >= 3:
            high_wr = sum(1 for e in high_strength if (e.get("pnl_pct") or 0) > 0) / len(high_strength)
            low_wr = sum(1 for e in low_strength if (e.get("pnl_pct") or 0) > 0) / len(low_strength)

            # P5: Proportional — scale adjustment by WR gap magnitude
            wr_gap = high_wr - low_wr  # positive = high signals better
            # Map gap to threshold_adj: gap of 0.3 → 1.05, gap of -0.3 → 0.95
            # Clamp to [0.95, 1.05]
            raw_adj = 1.0 + (wr_gap / 0.3) * 0.05
            result["signal_calibration"]["threshold_adj"] = round(
                max(0.95, min(1.05, raw_adj)), 3
            )

    # ── 5. Anomaly detection (P8: entries already sorted chronologically) ──
    anomalies = result["anomalies"]

    # Churning: too many flips on a ticker with low WR
    for ticker, pairs in ticker_groups.items():
        n = len(pairs)
        if n >= 8:
            wr = sum(1 for p, _ in pairs if p > 0) / n
            if wr < 0.3:
                anomalies.append(
                    f"CHURNING {ticker}: {n} flips, WR={wr*100:.0f}%"
                )

    # P8: Consecutive losses (entries now sorted chronologically)
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

    # MAE alert
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
    version = "7.1"  # v7.1: 4 dims trend, churning detection, threshold calibration

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
