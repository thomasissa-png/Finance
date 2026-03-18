"""Agent Learning 4 — Learning dédié à l'Agent Trader 4 (Meta/Ensemble) (Équipe 4).

Responsabilités :
- Consumes completed trades from Journal 4
- Calculates 5 dimensions of learning adapted for meta/ensemble approach:
  1. Per-team-combination: which team combos produce best results
  2. Per-ticker: which assets benefit most from multi-signal approach
  3. Per-confluence-level: how reliable are 2/3 vs 3/3 confluences
  4. Per-duration-category: intraday vs overnight vs multi_day
  5. Weight optimization: adjusts news_weight, trend_weight, tech_weight
- Weekly config generation (Sunday): freezes validated strategy for next week
- AB testing: compares current vs previous config performance
- Publishes "learning_4_updated" on bus → Trader 4 and Scoring 4 consume

Différences avec les autres learnings :
- Learning 1 : apprend des trades intraday (6 dimensions, ticker/session/newscat/regime/dir/delay)
- Learning 2 : apprend des positions de tendance (4 dimensions, ticker/newscat/dir/calibration)
- Learning 3 : apprend des positions techniques (3 dimensions, strategy/ticker/timeframe), weekly config
- Learning 4 : apprend des combinaisons multi-signal (5 dimensions, combo/ticker/confluence/duration/weights),
  weekly config, AB testing

Expertise incarnée :
- 10+ ans ML : multi-factor model optimization
- Ensemble method weighting (similar to boosting/stacking)
- Cross-validation across independent signal sources
"""

import json
import logging
import math
import os
import time
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

from .base import BaseAgent, AgentStatus

logger = logging.getLogger(__name__)

# Activation date — Learning 4 only starts after this date
# Configurable via LEARNING4_ACTIVATION_DATE env var (format: YYYY-MM-DD)
_l4_activation_str = os.environ.get("LEARNING4_ACTIVATION_DATE", "2026-04-01")
try:
    ACTIVATION_DATE = date.fromisoformat(_l4_activation_str)
except ValueError:
    ACTIVATION_DATE = date(2026, 4, 1)

# Minimum completed trades before learning activates.
# Lowered from 30 to 15: Team 4 activates 2026-03-16 — early data is sparse
# and we want learning to kick in after ~2 weeks instead of ~1 month.
MIN_HISTORY_TRADES = 15

# Per-dimension minimum samples
MIN_SAMPLES_COMBO = 5       # Per team combination
MIN_SAMPLES_TICKER = 5      # Per ticker
MIN_SAMPLES_CONFLUENCE = 8  # Per confluence level
MIN_SAMPLES_DURATION = 5    # Per duration category
MIN_SAMPLES_WEIGHTS = 15    # For weight optimization

# Adjustment bounds
ADJ_MIN = 0.6
ADJ_MAX = 1.4
ADJ_MIN_NARROW = 0.8
ADJ_MAX_NARROW = 1.2

# Temporal decay — half-life in days
DECAY_HALF_LIFE_DAYS = 45

# Weekly config persistence file
_WEEKLY_CONFIG_FILE = Path(os.getenv("DATA_DIR", "data")) / "learning4_weekly_config.json"


def _load_persisted_weekly_config() -> dict | None:
    """Load weekly config from disk (survives restarts)."""
    try:
        if _WEEKLY_CONFIG_FILE.exists():
            return json.loads(_WEEKLY_CONFIG_FILE.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load learning4 weekly config: %s", exc)
    return None


def _persist_weekly_config(config: dict):
    """Persist weekly config to disk."""
    try:
        _WEEKLY_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        _WEEKLY_CONFIG_FILE.write_text(json.dumps(config, indent=2, default=str))
    except OSError as exc:
        logger.warning("Failed to persist learning4 weekly config: %s", exc)


_CONFIG_HISTORY_FILE = Path(os.getenv("DATA_DIR", "data")) / "learning4_config_history.json"


def _load_config_history() -> list[dict]:
    """Load config history from disk."""
    try:
        if _CONFIG_HISTORY_FILE.exists():
            return json.loads(_CONFIG_HISTORY_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _persist_config_history(history: list[dict]):
    """Persist config history to disk."""
    try:
        _CONFIG_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CONFIG_HISTORY_FILE.write_text(json.dumps(history[-10:], indent=2, default=str))
    except OSError as exc:
        logger.warning("Failed to persist learning4 config history: %s", exc)


# Default source weights (same as Scoring 4)
DEFAULT_WEIGHTS = {
    "news": 0.35,
    "trend": 0.25,
    "tech": 0.40,
}

# Weight adjustment bounds (relative to defaults)
WEIGHT_ADJ_MIN = 0.5   # Minimum 50% of default weight
WEIGHT_ADJ_MAX = 2.0   # Maximum 200% of default weight

# Target win rate for Team 4
TARGET_WIN_RATE = 80.0

# AB testing: ranking formula weights (same as Team 3)
AB_WEIGHT_WR = 0.30
AB_WEIGHT_PNL = 0.40
AB_WEIGHT_SHARPE = 0.30


def _conservative_fallback(dimension: str, key: str = "") -> float:
    """Return a conservative fallback multiplier when a dimension has too few samples.

    Biases toward caution rather than neutrality so unproven configs don't
    get full credit.  Applied only to keys that appeared in the data at least
    once — completely unseen keys receive no entry at all.

    Args:
        dimension: One of "combo", "ticker", "confluence", "duration".
        key: The specific key within the dimension (e.g. "2" for confluence
             level 2, "multi_day" for duration).

    Returns:
        float multiplier in (0.0, 1.0] — always ≤ 1.0 (conservative direction).
    """
    if dimension == "combo":
        return 0.95   # Slightly cautious on unproven team combinations
    if dimension == "ticker":
        return 1.0    # Neutral — no prior information on this asset
    if dimension == "confluence":
        # Favour full confluence (3/3) when we have no data to distinguish
        return 0.9 if key == "2" else 1.0
    if dimension == "duration":
        # Longer holds carry more overnight/gap risk without track record
        return 0.95 if key == "multi_day" else 1.0
    return 1.0


def _clamp(value: float, lo: float = ADJ_MIN, hi: float = ADJ_MAX) -> float:
    return max(lo, min(hi, value))


def _compute_decay_weight(entry: dict, now: datetime) -> float:
    """Compute exponential decay weight based on entry age.

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


def _compute_sharpe(pnls: list[float]) -> float | None:
    """Compute annualized Sharpe ratio."""
    if len(pnls) < 3:
        return None
    mean = sum(pnls) / len(pnls)
    variance = sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1)
    if variance == 0:
        return None
    std = math.sqrt(variance)
    return round(mean / std * math.sqrt(250), 2)


def _filter_by_current_versions(entries: list[dict]) -> list[dict]:
    """V1: Keep only entries produced by current scorer_4+trader_4 versions.

    Entries without agent_versions (pre-versioning) are kept — time decay
    will naturally down-weight them.
    """
    try:
        from .registry import get_agent
        scorer4 = get_agent("scoring_4")
        trader4 = get_agent("trader_4")
        if not scorer4 or not trader4:
            return entries
        cur_scorer = scorer4.version
        cur_trader = trader4.version
        filtered = []
        skipped = 0
        for e in entries:
            av = e.get("agent_versions")
            if av is None:
                filtered.append(e)
                continue
            if av.get("scoring_4") == cur_scorer and av.get("trader_4") == cur_trader:
                filtered.append(e)
            else:
                skipped += 1
        if skipped:
            logger.info("V1: Version filter removed %d meta entries (keeping %d)", skipped, len(filtered))
        return filtered
    except Exception:
        return entries


def compute_meta_learning(entries: list[dict]) -> dict:
    """Compute learning adjustments from completed meta/ensemble trades.

    5 dimensions:
    1. Per-team-combination: which combos work best
    2. Per-ticker: which assets benefit from multi-signal
    3. Per-confluence-level: 2/3 vs 3/3 reliability
    4. Per-duration-category: intraday vs overnight vs multi_day
    5. Weight optimization: adjust source weights

    Args:
        entries: List of journal 4 entries (completed meta trades)

    Returns dict with:
        - combo_adj: {combo_key: multiplier}
        - ticker_adj: {ticker: multiplier}
        - confluence_adj: {level: multiplier}
        - duration_adj: {category: multiplier}
        - weight_optimization: {news: adj, trend: adj, tech: adj}
        - anomalies: [str]
        - stats: {global metrics}
    """
    result = {
        "combo_adj": {},
        "ticker_adj": {},
        "confluence_adj": {},
        "duration_adj": {},
        "weight_optimization": {},
        "source_contribution": {},
        "anomalies": [],
        "fallback_applied": [],
        "stats": {},
    }

    if not entries:
        result["stats"] = {"total_trades": 0, "sufficient_data": False}
        return result

    # Filter valid entries (not NEUTRAL, has pnl)
    valid = [e for e in entries
             if e.get("direction") not in ("NEUTRAL", None)
             and e.get("pnl_pct") is not None]

    # Sort chronologically
    valid.sort(key=lambda e: e.get("exit_time") or e.get("entry_time", ""))

    if len(valid) < MIN_HISTORY_TRADES:
        result["stats"] = {
            "total_trades": len(valid),
            "sufficient_data": False,
            "trades_needed": MIN_HISTORY_TRADES - len(valid),
        }
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
    mae_values = [e.get("mae_pct") for e in valid if e.get("mae_pct") is not None]
    mfe_values = [e.get("mfe_pct") for e in valid if e.get("mfe_pct") is not None]
    avg_mae = sum(mae_values) / len(mae_values) if mae_values else 0
    avg_mfe = sum(mfe_values) / len(mfe_values) if mfe_values else 0
    all_pnls = [e.get("pnl_pct", 0) for e in valid]
    sharpe = _compute_sharpe(all_pnls)

    result["stats"] = {
        "total_trades": len(valid),
        "sufficient_data": True,
        "win_rate": round(win_rate, 1),
        "avg_pnl_pct": round(avg_pnl, 2),
        "total_pnl_pct": round(total_pnl, 2),
        "avg_mae_pct": round(avg_mae, 2),
        "avg_mfe_pct": round(avg_mfe, 2),
        "wins": len(wins),
        "losses": len(losses),
        "sharpe": sharpe,
        "target_wr": TARGET_WIN_RATE,
        "wr_gap": round(TARGET_WIN_RATE - win_rate, 1),
    }

    # ── 1. Per-team-combination adjustments ──
    combo_groups: dict[str, list[tuple[float, float]]] = {}
    for e, w in zip(valid, weights):
        combo = e.get("team_combination", "")
        if not combo:
            continue
        pnl = e.get("pnl_pct", 0)
        combo_groups.setdefault(combo, []).append((pnl, w))

    for combo, pairs in combo_groups.items():
        if len(pairs) >= MIN_SAMPLES_COMBO:
            adj = _compute_adj(pairs)
            if adj is not None:
                result["combo_adj"][combo] = adj

    # Conservative fallback for combos seen in data but below min_samples
    for combo in combo_groups:
        if combo not in result["combo_adj"]:
            result["combo_adj"][combo] = _conservative_fallback("combo", combo)
            result["fallback_applied"].append(f"combo:{combo}")

    # ── 2. Per-ticker adjustments ──
    ticker_groups: dict[str, list[tuple[float, float]]] = {}
    for e, w in zip(valid, weights):
        ticker = e.get("ticker", "")
        pnl = e.get("pnl_pct", 0)
        ticker_groups.setdefault(ticker, []).append((pnl, w))

    for ticker, pairs in ticker_groups.items():
        if len(pairs) >= MIN_SAMPLES_TICKER:
            adj = _compute_adj(pairs)
            if adj is not None:
                result["ticker_adj"][ticker] = adj

    # Conservative fallback for tickers seen in data but below min_samples
    for ticker in ticker_groups:
        if ticker not in result["ticker_adj"]:
            result["ticker_adj"][ticker] = _conservative_fallback("ticker", ticker)
            result["fallback_applied"].append(f"ticker:{ticker}")

    # ── 3. Per-confluence-level adjustments ──
    confluence_groups: dict[str, list[tuple[float, float]]] = {}
    for e, w in zip(valid, weights):
        level = str(e.get("confluence_level", 0))
        pnl = e.get("pnl_pct", 0)
        confluence_groups.setdefault(level, []).append((pnl, w))

    for level, pairs in confluence_groups.items():
        if len(pairs) >= MIN_SAMPLES_CONFLUENCE:
            adj = _compute_adj(
                pairs, sensitivity=0.1, pnl_cap=0.2,
                lo=ADJ_MIN_NARROW, hi=ADJ_MAX_NARROW
            )
            if adj is not None:
                result["confluence_adj"][level] = adj

    # Conservative fallback for confluence levels seen in data but below min_samples
    for level in confluence_groups:
        if level not in result["confluence_adj"]:
            result["confluence_adj"][level] = _conservative_fallback("confluence", level)
            result["fallback_applied"].append(f"confluence:{level}")

    # ── 4. Per-duration-category adjustments ──
    duration_groups: dict[str, list[tuple[float, float]]] = {}
    for e, w in zip(valid, weights):
        dur = e.get("duration_category", "unknown")
        pnl = e.get("pnl_pct", 0)
        duration_groups.setdefault(dur, []).append((pnl, w))

    for dur, pairs in duration_groups.items():
        if len(pairs) >= MIN_SAMPLES_DURATION:
            adj = _compute_adj(
                pairs, sensitivity=0.12, pnl_cap=0.25,
                lo=ADJ_MIN_NARROW, hi=ADJ_MAX_NARROW
            )
            if adj is not None:
                result["duration_adj"][dur] = adj

    # Conservative fallback for duration categories seen in data but below min_samples
    for dur in duration_groups:
        if dur not in result["duration_adj"]:
            result["duration_adj"][dur] = _conservative_fallback("duration", dur)
            result["fallback_applied"].append(f"duration:{dur}")

    # ── 5. Weight optimization ──
    if len(valid) >= MIN_SAMPLES_WEIGHTS:
        source_performance: dict[str, list[float]] = {
            "news": [], "trend": [], "tech": [],
        }

        for entry in valid:
            source_details = entry.get("source_details", {})
            pnl = entry.get("pnl_pct", 0)
            for source_key in ("news", "trend", "tech"):
                if source_key in source_details:
                    source_performance[source_key].append(pnl)

        weight_adj = {}
        for source_key, pnls in source_performance.items():
            if len(pnls) < 5:
                continue
            wr = sum(1 for p in pnls if p > 0) / len(pnls)

            # Compute relative performance vs global
            rel_wr = wr - (win_rate / 100)
            raw_mult = 1.0 + rel_wr * 3.0
            clamped = max(WEIGHT_ADJ_MIN, min(WEIGHT_ADJ_MAX, raw_mult))

            default_w = DEFAULT_WEIGHTS.get(source_key, 0.33)
            adjusted_w = round(default_w * clamped, 3)
            weight_adj[source_key] = adjusted_w

        if weight_adj:
            # Normalize so weights sum to 1.0
            total_w = sum(weight_adj.values())
            if total_w > 0:
                result["weight_optimization"] = {
                    k: round(v / total_w, 3) for k, v in weight_adj.items()
                }

    # ── 5b. Source contribution analysis ──
    # For each entry, identify which source (news/trend/tech) was the primary
    # contributor (highest absolute score), then compute per-source win rates.
    source_primary_data: dict[str, dict] = {}  # source → {wins, total, pnl_sum}
    for entry in valid:
        source_details = entry.get("source_details", {})
        if not source_details:
            continue
        # Find source with highest absolute score
        best_source = None
        best_score = -1.0
        for src, score_val in source_details.items():
            if src not in ("news", "trend", "tech"):
                continue
            try:
                abs_score = abs(float(score_val))
            except (TypeError, ValueError):
                continue
            if abs_score > best_score:
                best_score = abs_score
                best_source = src
        if best_source is None:
            continue
        pnl = entry.get("pnl_pct", 0) or 0.0
        if best_source not in source_primary_data:
            source_primary_data[best_source] = {"wins": 0, "total": 0, "pnl_sum": 0.0}
        source_primary_data[best_source]["total"] += 1
        source_primary_data[best_source]["pnl_sum"] += pnl
        if pnl > 0:
            source_primary_data[best_source]["wins"] += 1

    for src, data in source_primary_data.items():
        if data["total"] >= 3:
            wr = data["wins"] / data["total"]
            avg_score_when_primary = data["pnl_sum"] / data["total"]
            result["source_contribution"][src] = {
                "primary_count": data["total"],
                "primary_wr": round(wr, 3),
                "avg_score_when_primary": round(avg_score_when_primary, 3),
            }

    # ── 6. Anomaly detection ──
    anomalies = result["anomalies"]

    # Low win rate overall
    if win_rate < 35 and len(valid) >= MIN_HISTORY_TRADES:
        anomalies.append(
            f"LOW_WIN_RATE: {win_rate:.0f}% on {len(valid)} trades"
        )

    # Confluence level 2 worse than level 3
    l2_pairs = confluence_groups.get("2", [])
    l3_pairs = confluence_groups.get("3", [])
    if len(l2_pairs) >= 5 and len(l3_pairs) >= 5:
        l2_wr = sum(1 for p, _ in l2_pairs if p > 0) / len(l2_pairs)
        l3_wr = sum(1 for p, _ in l3_pairs if p > 0) / len(l3_pairs)
        if l2_wr > l3_wr + 0.1:
            anomalies.append(
                f"CONFLUENCE_PARADOX: L2 WR={l2_wr*100:.0f}% > L3 WR={l3_wr*100:.0f}% "
                f"— full confluence not better than partial"
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
    if max_consecutive >= 4:
        anomalies.append(
            f"STREAK: {max_consecutive} consecutive losses"
        )

    # MAE alert
    if avg_mae < -5:
        anomalies.append(
            f"HIGH_MAE: {avg_mae:.1f}% — positions suffering large drawdowns"
        )

    # Team combo with very low performance
    for combo, pairs in combo_groups.items():
        if len(pairs) >= 5:
            wr = sum(1 for p, _ in pairs if p > 0) / len(pairs)
            if wr < 0.25:
                anomalies.append(
                    f"WEAK_COMBO: {combo} WR={wr*100:.0f}% on {len(pairs)} trades"
                )

    # Far from target WR
    if win_rate < TARGET_WIN_RATE - 20 and len(valid) >= MIN_HISTORY_TRADES:
        anomalies.append(
            f"BELOW_TARGET: WR={win_rate:.0f}% vs target {TARGET_WIN_RATE:.0f}% "
            f"(gap={TARGET_WIN_RATE - win_rate:.0f}pp)"
        )

    return result


class AgentLearning4(BaseAgent):
    """Agent Learning 4 — Learning dédié au Meta/Ensemble (Trader 4).

    Learns from completed meta trades to optimize team combination weights,
    per-ticker performance, confluence reliability, and source weight allocation.
    Generates weekly config (Sunday) with validated strategy for next week.
    """

    name = "learning_4"
    description = "Learning & optimisation — meta/ensemble trading"
    version = "2.2"  # v2.2: bootstrap 30→15, conservative fallbacks, source contribution, activation date

    def __init__(self):
        super().__init__()
        self._last_adjustment_count: int = 0
        self._last_anomalies: list = []
        self._total_recalculations: int = 0
        self._cached_adjustments: dict | None = None
        self._cache_valid: bool = False
        self._cache_lock = __import__("threading").Lock()  # v8.4 fix: thread-safe cache
        self._last_run_time = None  # P3.13: stale cache monitoring
        # Weekly config (persisted to survive restarts)
        self._weekly_config: dict | None = _load_persisted_weekly_config()
        self._config_history: list[dict] = _load_config_history()

    def run(self, **kwargs) -> dict:
        """Recalculate meta learning dimensions and publish adjustments.

        Called after Journal 4 runs.
        Returns dict with learning results.
        """
        # Check activation date
        if date.today() < ACTIVATION_DATE:
            self._set_status(AgentStatus.IDLE,
                             f"Activation {ACTIVATION_DATE.isoformat()}")
            self.log("Learning 4 not yet activated", {
                "activation_date": ACTIVATION_DATE.isoformat(),
                "today": date.today().isoformat(),
            })
            return {
                "adjustments": {}, "anomalies": [],
                "stats": {}, "dimensions_updated": 0,
                "reason": "not_yet_activated",
            }

        self._set_status(AgentStatus.WORKING, "Recalculating meta learning")

        start = time.monotonic()
        result = {
            "adjustments": {},
            "anomalies": [],
            "stats": {},
            "dimensions_updated": 0,
        }

        try:
            # Step 1: Load journal 4 entries + version filter
            self.log("Loading meta journal entries for learning")
            from .agent_journal_4 import _load_journal_entries
            entries = _filter_by_current_versions(_load_journal_entries())

            # Step 2: Compute adjustments
            learning_data = self.execute(
                "Computing meta learning (5 dimensions)",
                compute_meta_learning,
                entries,
            )

            with self._cache_lock:
                self._cached_adjustments = learning_data
                self._last_run_time = datetime.now(timezone.utc)  # P3.13
                self._cache_valid = True
                self._total_recalculations += 1

            # Step 3: Process results
            combo_adj = learning_data.get("combo_adj", {})
            ticker_adj = learning_data.get("ticker_adj", {})
            confluence_adj = learning_data.get("confluence_adj", {})
            duration_adj = learning_data.get("duration_adj", {})
            weight_opt = learning_data.get("weight_optimization", {})
            anomalies = learning_data.get("anomalies", [])
            stats = learning_data.get("stats", {})

            self._last_adjustment_count = (
                len(combo_adj) + len(ticker_adj)
                + len(confluence_adj) + len(duration_adj)
                + (1 if weight_opt else 0)
            )
            self._last_anomalies = anomalies

            dims_updated = sum([
                len(combo_adj) > 0,
                len(ticker_adj) > 0,
                len(confluence_adj) > 0,
                len(duration_adj) > 0,
                len(weight_opt) > 0,
            ])

            result["adjustments"] = learning_data
            result["anomalies"] = anomalies
            result["stats"] = stats
            result["dimensions_updated"] = dims_updated

            # Step 4: Log
            self.log("Meta learning computed", {
                "combo_adj": combo_adj,
                "ticker_adj": ticker_adj,
                "confluence_adj": confluence_adj,
                "duration_adj": duration_adj,
                "weight_optimization": weight_opt,
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
            for k, v in combo_adj.items():
                if abs(v - 1.0) > 0.1:
                    significant[f"combo:{k}"] = v
            for k, v in ticker_adj.items():
                if abs(v - 1.0) > 0.1:
                    significant[f"ticker:{k}"] = v
            for k, v in confluence_adj.items():
                if abs(v - 1.0) > 0.05:
                    significant[f"confl:{k}"] = v
            for k, v in duration_adj.items():
                if abs(v - 1.0) > 0.05:
                    significant[f"duration:{k}"] = v
            if significant:
                self.log_decision("Significant meta adjustments", significant)

            if weight_opt:
                self.log_decision("Weight optimization", weight_opt)

            # Step 5: Publish
            self.publish("learning_4_updated", {
                "adjustment_count": self._last_adjustment_count,
                "dimensions_active": dims_updated,
                "anomaly_count": len(anomalies),
                "total_trades": stats.get("total_trades", 0),
                "win_rate": stats.get("win_rate", 0),
                "weight_optimization": weight_opt,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            self._set_status(
                AgentStatus.IDLE,
                f"{self._last_adjustment_count} adjustments, "
                f"{len(anomalies)} anomalies"
            )

            return result

        except Exception as exc:
            self.log("Meta learning failed", {"error": str(exc)}, level="ERROR")
            self._set_status(AgentStatus.ERROR, str(exc))
            raise

    def generate_weekly_config(self) -> dict:
        """P1: Generate weekly config for Team 4 (called Sunday evening).

        Freezes the current learning state into a config that Scoring 4
        and Trader 4 will use for the entire next week.

        Includes AB testing comparison with previous config.
        """
        self._set_status(AgentStatus.WORKING, "Generating weekly config")

        try:
            # Get current learning adjustments
            adjustments = self.get_adjustments()
            stats = adjustments.get("stats", {})
            anomalies = adjustments.get("anomalies", [])

            # Get weekly summary from Journal 4
            weekly_summary = {}
            try:
                from .agent_journal_4 import AgentJournal4
                # Use a fresh instance to call the method
                from . import registry
                j4 = registry.get_agent("journal_4")
                if j4:
                    weekly_summary = j4.compute_weekly_summary()
            except Exception as exc:
                logger.warning("Could not get weekly summary: %s", exc)

            # Determine validated combos (win_rate > 50% with enough data)
            combo_adj = adjustments.get("combo_adj", {})
            validated_combos = []
            disabled_combos = []
            for combo, adj in combo_adj.items():
                if adj >= 1.0:
                    validated_combos.append(combo)
                elif adj < 0.8:
                    disabled_combos.append(combo)

            # Get optimized weights
            weight_opt = adjustments.get("weight_optimization", {})
            weights = weight_opt if weight_opt else dict(DEFAULT_WEIGHTS)

            # AB testing comparison with previous config
            ab_result = None
            if self._weekly_config:
                ab_result = self._compare_configs(
                    self._weekly_config, adjustments, weekly_summary
                )

            # Build config
            config = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "valid_from": datetime.now(timezone.utc).isoformat(),
                "weights": weights,
                "validated_combos": validated_combos,
                "disabled_combos": disabled_combos,
                "combo_adj": combo_adj,
                "ticker_adj": adjustments.get("ticker_adj", {}),
                "confluence_adj": adjustments.get("confluence_adj", {}),
                "duration_adj": adjustments.get("duration_adj", {}),
                "stats": stats,
                "weekly_summary": weekly_summary,
                "anomalies": anomalies,
                "ab_test": ab_result,
                "config_version": len(self._config_history) + 1,
            }

            # Archive previous config
            if self._weekly_config:
                self._config_history.append(self._weekly_config)
                # Keep last 10 configs
                self._config_history = self._config_history[-10:]
                _persist_config_history(self._config_history)

            self._weekly_config = config
            _persist_weekly_config(config)

            self.log_decision("Weekly config generated", {
                "config_version": config["config_version"],
                "validated_combos": validated_combos,
                "disabled_combos": disabled_combos,
                "weights": weights,
                "win_rate": stats.get("win_rate", 0),
                "target_wr": TARGET_WIN_RATE,
                "ab_test": ab_result,
            })

            self.publish("learning_4_weekly_config", {
                "config_version": config["config_version"],
                "validated_combos": validated_combos,
                "weights": weights,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            self._set_status(AgentStatus.IDLE,
                             f"Config v{config['config_version']} generated")

            return config

        except Exception as exc:
            self.log("Weekly config generation failed",
                     {"error": str(exc)}, level="ERROR")
            self._set_status(AgentStatus.ERROR, str(exc))
            raise

    def _compare_configs(self, previous_config: dict,
                         current_adjustments: dict,
                         weekly_summary: dict) -> dict:
        """P2: AB test — compare previous config vs current learning state."""
        prev_stats = previous_config.get("stats", {})
        curr_stats = current_adjustments.get("stats", {})
        prev_weekly = previous_config.get("weekly_summary", {})

        prev_wr = prev_weekly.get("win_rate", prev_stats.get("win_rate", 0))
        curr_wr = weekly_summary.get("win_rate", curr_stats.get("win_rate", 0))

        prev_pnl = prev_weekly.get("avg_pnl", prev_stats.get("avg_pnl_pct", 0))
        curr_pnl = weekly_summary.get("avg_pnl", curr_stats.get("avg_pnl_pct", 0))

        prev_sharpe = prev_weekly.get("sharpe") or prev_stats.get("sharpe")
        curr_sharpe = weekly_summary.get("sharpe") or curr_stats.get("sharpe")

        # Compute AB scores
        def _score(wr, pnl, sharpe_val):
            s = 0.0
            s += (wr or 0) * AB_WEIGHT_WR
            s += (pnl or 0) * AB_WEIGHT_PNL * 10  # Scale PnL to comparable range
            s += (sharpe_val or 0) * AB_WEIGHT_SHARPE
            return round(s, 2)

        prev_score = _score(prev_wr, prev_pnl, prev_sharpe)
        curr_score = _score(curr_wr, curr_pnl, curr_sharpe)

        return {
            "previous": {
                "win_rate": prev_wr,
                "avg_pnl": prev_pnl,
                "sharpe": prev_sharpe,
                "score": prev_score,
                "config_version": previous_config.get("config_version", 0),
            },
            "current": {
                "win_rate": curr_wr,
                "avg_pnl": curr_pnl,
                "sharpe": curr_sharpe,
                "score": curr_score,
            },
            "improvement": round(curr_score - prev_score, 2),
            "winner": "current" if curr_score >= prev_score else "previous",
        }

    def get_weekly_config(self) -> dict | None:
        """Get current weekly config (for Scoring 4 and Trader 4)."""
        return self._weekly_config

    def get_config_history(self) -> list[dict]:
        """Get history of weekly configs."""
        return self._config_history

    def get_adjustments(self) -> dict:
        """Get cached meta learning adjustments (used by Trader 4 and Scoring 4).

        Recalculates if cache is invalid.
        v8.4 fix: Thread-safe cache access via lock.
        """
        if date.today() < ACTIVATION_DATE:
            return {}
        with self._cache_lock:
            if not self._cache_valid or self._cached_adjustments is None:
                from .agent_journal_4 import _load_journal_entries
                entries = _filter_by_current_versions(_load_journal_entries())
                self._cached_adjustments = compute_meta_learning(entries)
                self._cache_valid = True
            return self._cached_adjustments

    def invalidate_cache(self):
        """Invalidate the learning cache (called after journal 4).

        v8.4 fix: Thread-safe cache invalidation via lock.
        """
        with self._cache_lock:
            self._cache_valid = False
        self.log("Meta learning cache invalidated")

    def get_metrics(self) -> dict:
        return {
            "last_adjustment_count": self._last_adjustment_count,
            "anomalies": self._last_anomalies[:5],
            "total_recalculations": self._total_recalculations,
            "cache_valid": self._cache_valid,
            "has_weekly_config": self._weekly_config is not None,
            "config_version": (self._weekly_config or {}).get("config_version", 0),
            "target_win_rate": TARGET_WIN_RATE,
            "activation_date": ACTIVATION_DATE.isoformat(),
            "is_active": date.today() >= ACTIVATION_DATE,
        }
