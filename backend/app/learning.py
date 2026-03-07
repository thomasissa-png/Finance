"""Performance tracking and self-learning via historical trade analysis.

v3.4 audit changes:
- #1: Regime-conditional learning (VIX calm/normal/elevated/stress)
- #2: Session mult uses current scan type, not last trade's scan type
- #3: Per-ticker significance raised (t>1.5, min 8 trades)
- #4: News category blend uses direct newscat_adj, not historical average
- #5: learning_helped tracking (adjusted vs raw score correlation)
- #6: PnL-signed signal replaces binary win_rate in _compute_adjustment
- #8: Per-dimension multiplier decomposition logged
- #11: Benchmark in performance summary

v4.0: PostgreSQL persistence (when DATABASE_URL is set, falls back to JSON files).

v4.2 audit changes:
- A2: Fix stderr=0 bug — check minimum effect size instead of blind True
- A3: Minimum effect size threshold in _is_significant
- A4: PnL divisor 2.0 → 1.0 (was halving sensitivity)
- A5: Average multipliers across all eligible tickers (in trade_selector.py)
- B1: delay_bias_adj — learning adjustment from transmission_delay accuracy
- B2: magnitude_accuracy tracking in performance summary
- B3: Slippage vs estimated spread feedback
- B4: hour_adj — REMOVED (M8: worst data-to-noise ratio)
- B5: direction_adj — direction accuracy as learning adjustment
- C1: Regime min trades raised to 15, or merged into 2 buckets
- C2: Confidence-scaled bounds in adjustments
- C3: Decay multiplier between journal runs (via cache invalidation)
- C4: Convergence source dedup (in news_scorer — documented here)
- D1: Lookback reduced to 2x half-life (max ~90 days instead of 180)
- D3: Detect partial PG migration
- D4: Pass trades param to build_performance_summary
- E1: Restructured prompt to alerts-only (no detailed stats unless anomalous)
- E2: MAE feedback in performance summary
- E3: signal_reliability precision tracking
"""

import fcntl
import json
import logging
import math
import statistics
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .database import is_pg_enabled
from .models import PerformanceStats, TradeRecommendation, TradeResult

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
TRADES_FILE = DATA_DIR / "trades.json"

# H2: In-memory cache for load_trades() — avoids repeated PG/JSON reads
_trades_cache: list[TradeRecommendation] | None = None
_trades_cache_time: float = 0.0
_trades_cache_source: str = ""  # "pg" or path string — invalidate if source changes
_trades_cache_lock = threading.Lock()
_TRADES_CACHE_TTL = 60.0  # seconds

# (#30) Adaptive decay: gradual transition from 45 to 30 days
DECAY_HALF_LIFE_DAYS_LOW = 45.0   # When few trades
DECAY_HALF_LIFE_DAYS_HIGH = 30.0  # When many trades
DECAY_TRANSITION_START = 50       # Start transitioning at 50 closed trades
DECAY_TRANSITION_END = 200        # Fully transitioned at 200 closed trades

# Baseline hours for converting actual_pricing_time_hours to 0-100 delay score
TRANSMISSION_DELAY_BASELINE_HOURS = 6.0  # 6h of pricing time = delay score 100


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not TRADES_FILE.exists():
        TRADES_FILE.write_text("[]")


def _read_json_locked(path: Path) -> list:
    """Read JSON file with shared lock."""
    _ensure_data_dir()
    try:
        with open(path, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        logger.error("Failed to read %s: %s", path, exc)
        return []


def _write_json_locked(path: Path, data: list) -> None:
    """Write JSON file with exclusive lock."""
    _ensure_data_dir()
    with open(path, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            json.dump(data, f, indent=2, default=str)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _parse_trades(raw: list, source: str) -> list[TradeRecommendation]:
    """Parse raw dicts into TradeRecommendation, skipping invalid entries."""
    trades: list[TradeRecommendation] = []
    for i, t in enumerate(raw):
        try:
            trades.append(TradeRecommendation(**t))
        except Exception as exc:
            logger.warning(
                "Skipping invalid trade #%d from %s (ticker=%s): %s",
                i, source, t.get("ticker", "?") if isinstance(t, dict) else "?", exc,
            )
    return trades


def _invalidate_trades_cache() -> None:
    """H2: Invalidate the in-memory trades cache (called after writes)."""
    global _trades_cache, _trades_cache_time, _trades_cache_source
    with _trades_cache_lock:
        _trades_cache = None
        _trades_cache_time = 0.0
        _trades_cache_source = ""


def load_trades() -> list[TradeRecommendation]:
    """Load all historical trades.

    H2: Returns cached result if within TTL (60s), avoiding repeated PG/JSON reads.
    Cache auto-invalidates if the data source changes (PG vs JSON file path).
    M6: Guards against repeated auto-migration attempts.
    Resilient: skips individual entries that fail to parse.
    Falls back to JSON if PG returns empty (un-migrated data).
    """
    # H2: Determine current source for cache validity check
    current_source = "pg" if is_pg_enabled() else str(TRADES_FILE)

    # H2: Check cache first
    global _trades_cache, _trades_cache_time, _trades_cache_source
    with _trades_cache_lock:
        if (_trades_cache is not None
                and _trades_cache_source == current_source
                and (time.monotonic() - _trades_cache_time) < _TRADES_CACHE_TTL):
            return list(_trades_cache)  # Return copy to prevent mutation

    trades = _load_trades_uncached()

    # H2: Update cache
    with _trades_cache_lock:
        _trades_cache = list(trades)
        _trades_cache_time = time.monotonic()
        _trades_cache_source = current_source

    return trades


def _load_trades_uncached() -> list[TradeRecommendation]:
    """Internal: load trades without cache (for cache population)."""
    if is_pg_enabled():
        try:
            from .database import pg_load_trades
            raw = pg_load_trades()
            trades = _parse_trades(raw, "PG")
            if trades:
                return trades
            if not raw:
                logger.info("PG trades table is empty, trying JSON fallback")
        except Exception as exc:
            logger.error("Failed to load trades from PostgreSQL: %s", exc)

    # JSON fallback (or primary path when PG is disabled)
    try:
        raw = _read_json_locked(TRADES_FILE)
        trades = _parse_trades(raw, "JSON")
        # M6: Auto-migrate JSON → PG if PG is enabled but was empty (only once)
        if trades and is_pg_enabled():
            from .database import was_migration_attempted, mark_migration_attempted
            if not was_migration_attempted("trades"):
                mark_migration_attempted("trades")
                logger.info("Auto-migrating %d trades from JSON to PostgreSQL", len(trades))
                try:
                    from .database import pg_save_trade
                    for t in trades:
                        pg_save_trade(t.model_dump(mode="json"))
                    logger.info("Auto-migration of trades complete")
                except Exception as exc:
                    logger.error("Auto-migration of trades failed: %s", exc)
        return trades
    except Exception as exc:
        logger.error("Failed to load trades: %s", exc)
        return []


def save_trade(trade: TradeRecommendation) -> None:
    """Append a new trade to the history."""
    if is_pg_enabled():
        from .database import pg_save_trade
        pg_save_trade(trade.model_dump(mode="json"))
        logger.info("Saved trade: %s %s %s", trade.direction, trade.ticker, trade.catalyst[:50])
        _invalidate_trades_cache()  # H2
        return
    trades = _load_trades_uncached()  # bypass cache to get fresh data for append
    trades.append(trade)
    _write_trades(trades)
    _invalidate_trades_cache()  # H2
    logger.info("Saved trade: %s %s %s", trade.direction, trade.ticker, trade.catalyst[:50])


def update_trade_result(
    timestamp: datetime,
    ticker: str,
    result: TradeResult,
    exit_price: float,
    actual_pricing_time_hours: float | None = None,
    delay_accuracy: float | None = None,
) -> None:
    """Update a pending trade with its outcome.

    L3: Uses shared compute_pnl() for consistent PnL calculation.
    H2: Invalidates trades cache after update.
    Also signals that the learning cache should be invalidated,
    so the next scan picks up the updated performance data.
    """
    if is_pg_enabled():
        from .database import pg_update_trade_result
        updated, pnl = pg_update_trade_result(
            ticker, timestamp, result.value, exit_price,
            datetime.now(timezone.utc),
            actual_pricing_time_hours, delay_accuracy,
        )
        if updated:
            logger.info("Updated trade %s %s: %s (PnL: %s%%)", ticker, timestamp, result, pnl)
            _invalidate_trades_cache()  # H2
            _signal_learning_cache_invalidation()
        else:
            logger.warning("Trade not found for update: %s %s", ticker, timestamp)
        return

    trades = _load_trades_uncached()  # bypass cache for fresh data
    for trade in trades:
        if trade.ticker == ticker and trade.timestamp == timestamp and trade.result == TradeResult.PENDING:
            trade.result = result
            trade.exit_price = exit_price
            trade.closed_at = datetime.now(timezone.utc)

            # L3: Use shared PnL calculation
            from .database import compute_pnl
            pnl = compute_pnl(trade.direction.value, trade.entry_price, exit_price)
            trade.pnl_pct = pnl if pnl is not None else 0.0

            # P1-#6: Store transmission delay accuracy on the trade
            if actual_pricing_time_hours is not None:
                trade.actual_pricing_time_hours = actual_pricing_time_hours
            if delay_accuracy is not None:
                trade.delay_accuracy = delay_accuracy

            _write_trades(trades)
            _invalidate_trades_cache()  # H2
            logger.info("Updated trade %s %s: %s (PnL: %s%%)", ticker, timestamp, result, trade.pnl_pct)
            _signal_learning_cache_invalidation()
            return

    logger.warning("Trade not found for update: %s %s", ticker, timestamp)


def _signal_learning_cache_invalidation() -> None:
    """Invalidate the scheduler's learning cache after a trade closes."""
    try:
        from .scheduler import invalidate_learning_cache
        invalidate_learning_cache()
    except ImportError:
        pass  # Module not loaded yet (e.g., during testing)


def _write_trades(trades: list[TradeRecommendation]) -> None:
    _write_json_locked(
        TRADES_FILE,
        [t.model_dump(mode="json") for t in trades],
    )


def _get_decay_half_life(n_closed: int) -> float:
    """Get adaptive decay half-life (#30).

    Gradual transition from 45d to 30d between 50 and 200 closed trades.
    Avoids abrupt behavior change at a single threshold.
    """
    if n_closed <= DECAY_TRANSITION_START:
        return DECAY_HALF_LIFE_DAYS_LOW
    if n_closed >= DECAY_TRANSITION_END:
        return DECAY_HALF_LIFE_DAYS_HIGH
    # Linear interpolation between start and end
    progress = (n_closed - DECAY_TRANSITION_START) / (DECAY_TRANSITION_END - DECAY_TRANSITION_START)
    return DECAY_HALF_LIFE_DAYS_LOW + progress * (DECAY_HALF_LIFE_DAYS_HIGH - DECAY_HALF_LIFE_DAYS_LOW)


def _compute_decay_weight(trade_timestamp: datetime, half_life: float) -> float:
    """Exponential decay weight based on trade age. Recent trades weigh more."""
    now = datetime.now(timezone.utc)
    age_days = (now - trade_timestamp).total_seconds() / 86400
    return math.exp(-0.693 * age_days / half_life)  # ln(2) ~ 0.693


def compute_performance() -> PerformanceStats:
    """Calculate aggregated performance statistics."""
    trades = load_trades()
    closed = [t for t in trades if t.result != TradeResult.PENDING]
    pending = [t for t in trades if t.result == TradeResult.PENDING]

    if not trades:
        return PerformanceStats()

    wins = [t for t in closed if t.result == TradeResult.TP_HIT]
    losses = [t for t in closed if t.result == TradeResult.SL_HIT]
    expired = [t for t in closed if t.result == TradeResult.EXPIRED]

    pnls = [t.pnl_pct for t in closed if t.pnl_pct is not None]

    # Stats by category
    by_category: dict[str, dict] = {}
    for t in closed:
        cat = t.category
        if cat not in by_category:
            by_category[cat] = {"total": 0, "wins": 0, "pnl_sum": 0.0}
        by_category[cat]["total"] += 1
        if t.result == TradeResult.TP_HIT:
            by_category[cat]["wins"] += 1
        if t.pnl_pct is not None:
            by_category[cat]["pnl_sum"] += t.pnl_pct
    for cat in by_category:
        total = by_category[cat]["total"]
        by_category[cat]["win_rate"] = round(by_category[cat]["wins"] / total * 100, 1) if total else 0

    # Stats by scan type
    by_scan: dict[str, dict] = {}
    for t in closed:
        st = t.scan_type.value
        if st not in by_scan:
            by_scan[st] = {"total": 0, "wins": 0, "pnl_sum": 0.0}
        by_scan[st]["total"] += 1
        if t.result == TradeResult.TP_HIT:
            by_scan[st]["wins"] += 1
        if t.pnl_pct is not None:
            by_scan[st]["pnl_sum"] += t.pnl_pct
    for st in by_scan:
        total = by_scan[st]["total"]
        by_scan[st]["win_rate"] = round(by_scan[st]["wins"] / total * 100, 1) if total else 0

    return PerformanceStats(
        total_trades=len(trades),
        wins=len(wins),
        losses=len(losses),
        expired=len(expired),
        pending=len(pending),
        win_rate=round(len(wins) / len(closed) * 100, 1) if closed else 0,
        avg_pnl_pct=round(sum(pnls) / len(pnls), 4) if pnls else 0,
        total_pnl_pct=round(sum(pnls), 4) if pnls else 0,
        best_trade_pnl=max(pnls) if pnls else 0,
        worst_trade_pnl=min(pnls) if pnls else 0,
        avg_confidence=round(sum(t.confidence for t in trades) / len(trades), 1),
        by_category=by_category,
        by_scan_type=by_scan,
    )


def _is_significant(pnl_values: list[float], min_samples: int = 4,
                    t_threshold: float = 1.0,
                    min_effect_size: float = 0.1,
                    weighted_mean: float | None = None,
                    weighted_stderr: float | None = None) -> bool:
    """P0-#12: Check if we have enough data for a statistically meaningful adjustment.

    Requires:
    1. At least min_samples data points
    2. Standard error small enough that the mean is distinguishable from zero
       (pseudo t-test: |mean/stderr| > t_threshold)
    3. v4.2 A3: Minimum effect size — |mean| must be at least min_effect_size
       to avoid adjusting on negligible signals

    v3.4/M8: t_threshold is configurable per dimension.
    Per-ticker uses 2.0 (stricter — small samples, high noise).
    Per-category/session/newscat/regime uses 1.5 (larger pool, less noise).

    v4.2 A2: When stderr=0 (all identical values), check minimum effect size
    instead of blindly returning True. All-zero PnL values are not a signal.

    C2: If weighted_mean and weighted_stderr are provided, use those for the
    significance test instead of computing unweighted statistics from pnl_values.
    This ensures the significance test matches the decay-weighted values actually
    used for the adjustment computation.
    """
    if len(pnl_values) < min_samples:
        return False
    if len(pnl_values) < 2:
        return False
    try:
        # C2: Use pre-computed decay-weighted mean/stderr if provided
        if weighted_mean is not None and weighted_stderr is not None:
            mean = weighted_mean
            stderr = weighted_stderr
        else:
            mean = statistics.mean(pnl_values)
            stdev = statistics.stdev(pnl_values)
            stderr = stdev / math.sqrt(len(pnl_values))

        # A3: Minimum effect size — ignore negligible average PnL
        if abs(mean) < min_effect_size:
            return False
        if stderr == 0:
            # A2: All identical non-zero values — check effect size instead of blind True
            return abs(mean) >= min_effect_size
        return abs(mean / stderr) > t_threshold
    except (statistics.StatisticsError, ZeroDivisionError):
        return False


def _compute_adjustment(entries: list[tuple[float, float]], sensitivity: float = 0.5,
                        pnl_cap: float = 0.25, bounds: tuple[float, float] = (0.5, 1.5),
                        min_significant: int = 4, t_threshold: float = 1.0) -> float | None:
    """Compute a single decay-weighted adjustment with significance check.

    v3.4 #6: Uses PnL-signed signal instead of binary win_rate.
    The old formula used `win_rate - 0.5` which treats an EXPIRED at +0.8%
    the same as a SL_HIT at -2%. Now we use decay-weighted average PnL directly,
    normalized and capped, as the primary signal.

    Returns None if not significant, else a multiplier in [bounds].
    """
    if len(entries) < 2:
        logger.debug("_compute_adjustment: not enough entries (%d < 2)", len(entries))
        return None
    total_w = sum(w for _, w in entries)
    if total_w == 0:
        logger.warning("_compute_adjustment: zero total weight (possible data issue)")
        return None

    # v3.4 #6: PnL-signed signal — decay-weighted average PnL
    avg_pnl = sum(p * w for p, w in entries) / total_w

    # C2: Compute decay-weighted stderr for significance test on weighted values
    # Weighted variance: sum(w_i * (x_i - avg)^2) / total_w
    weighted_var = sum(w * (p - avg_pnl) ** 2 for p, w in entries) / total_w
    weighted_stderr = math.sqrt(weighted_var / len(entries)) if weighted_var > 0 else 0.0

    # P0-#12: Significance check on decay-weighted values (C2: uses weighted mean/stderr)
    pnl_values = [p for p, _ in entries]
    if not _is_significant(pnl_values, min_samples=min_significant,
                           t_threshold=t_threshold,
                           weighted_mean=avg_pnl,
                           weighted_stderr=weighted_stderr):
        logger.debug("_compute_adjustment: not significant (n=%d, t_thresh=%.1f)",
                      len(pnl_values), t_threshold)
        return None
    # v4.2 A4: Normalize by 1.0 (1% PnL = full-strength signal)
    # Previously divided by 2.0 which halved the sensitivity — a 1% avg PnL
    # only produced a 0.125 adjustment instead of 0.25
    pnl_signal = min(pnl_cap, max(-pnl_cap, avg_pnl / 1.0))
    mult = 1.0 + pnl_signal * sensitivity
    lo, hi = bounds
    return round(max(lo, min(hi, mult)), 3)


def compute_learning_adjustments(trades: list[TradeRecommendation] | None = None) -> dict:
    """Compute per-ticker score multipliers based on historical performance.

    Uses temporal decay: recent trades weigh more than old ones.
    Also computes per-category, per-session (#28), per-news_category (#29),
    per-regime (#1 v3.4), delay_bias (B1 v4.2),
    and direction (B5 v4.2) adjustments.

    Returns dict with:
    - "adjustments": dict[ticker, multiplier] (default 1.0, range 0.5-1.5)
    - "session_adj": dict[scan_type, multiplier] — applied by caller based on current scan
    - "newscat_adj": dict[news_category, multiplier] — applied by caller based on current news
    - "regime_adj": dict[regime, multiplier] — applied by caller based on current VIX regime
    - "direction_adj": dict[direction, multiplier] — v4.2 B5: LONG vs SHORT accuracy
    - "delay_bias_adj": float — v4.2 B1: adjustment from delay prediction accuracy
    - "decomposition": dict[ticker, {ticker_mult, cat_mult}] — for diagnostics (#8)

    v4.2 changes:
    - D1: Lookback reduced to 2x half_life (was 180 days fixed)
    - B1: delay_bias_adj from transmission_delay accuracy data
    - B5: direction_adj LONG/SHORT accuracy
    - C1: Regime min trades raised to 15, merged to 2 buckets (calm+normal, elevated+stress)
    - C2: Significance test on decay-weighted values (weighted mean/stderr)
    - C3: Half-life computed after lookback filter
    - M8: hour_adj removed (worst data-to-noise ratio), t-stat thresholds raised
    """
    if trades is None:
        trades = load_trades()
    closed = [t for t in trades if t.result != TradeResult.PENDING]

    # C3: Initial generous lookback filter (180 days), then compute half_life from filtered set
    initial_cutoff = datetime.now(timezone.utc) - timedelta(days=180)
    recent_closed = []
    for t in closed:
        ts = t.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts > initial_cutoff:
            recent_closed.append(t)
    if len(recent_closed) < len(closed):
        logger.info("C3: Initial filter %d → %d trades (last 180 days)",
                     len(closed), len(recent_closed))
    closed = recent_closed

    # (#30) Adaptive decay — computed AFTER lookback filter (C3)
    half_life = _get_decay_half_life(len(closed))

    # D1: Tighten lookback to 2x half_life (computed from filtered set)
    lookback_days = int(half_life * 2)
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    tighter_closed = []
    for t in closed:
        ts = t.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts > cutoff:
            tighter_closed.append(t)
    if len(tighter_closed) < len(closed):
        logger.info("D1: Filtered %d → %d trades (last %d days = 2x half_life)",
                     len(closed), len(tighter_closed), lookback_days)
    closed = tighter_closed

    empty_result = {
        "adjustments": {},
        "session_adj": {},
        "newscat_adj": {},
        "regime_adj": {},
        "direction_adj": {},
        "delay_bias_adj": 1.0,
        "decomposition": {},
    }

    if len(closed) < 5:
        logger.info("Not enough closed trades for learning: %d < 5", len(closed))
        return empty_result

    # ── Per-ticker adjustments (M8: stricter — t>2.0, min 8) ────
    ticker_weighted: dict[str, list[tuple[float, float]]] = {}
    for t in closed:
        if t.pnl_pct is not None:
            w = _compute_decay_weight(t.timestamp, half_life)
            ticker_weighted.setdefault(t.ticker, []).append((t.pnl_pct, w))

    ticker_adj: dict[str, float] = {}
    for ticker, entries in ticker_weighted.items():
        adj = _compute_adjustment(entries, sensitivity=0.5, pnl_cap=0.25,
                                  bounds=(0.5, 1.5), min_significant=8,
                                  t_threshold=2.0)
        if adj is not None:
            ticker_adj[ticker] = adj

    # ── Per-category adjustments (with decay + significance) ──────
    cat_weighted: dict[str, list[tuple[float, float]]] = {}
    for t in closed:
        if t.pnl_pct is not None:
            w = _compute_decay_weight(t.timestamp, half_life)
            cat_weighted.setdefault(t.category, []).append((t.pnl_pct, w))

    cat_adj: dict[str, float] = {}
    for cat, entries in cat_weighted.items():
        adj = _compute_adjustment(entries, sensitivity=0.3, pnl_cap=0.15,
                                  bounds=(0.7, 1.3), min_significant=5,
                                  t_threshold=1.5)
        if adj is not None:
            cat_adj[cat] = adj

    # ── (#28) Per-session adjustments (v3.4 #2: returned separately) ──
    session_weighted: dict[str, list[tuple[float, float]]] = {}
    for t in closed:
        if t.pnl_pct is not None:
            w = _compute_decay_weight(t.timestamp, half_life)
            session_weighted.setdefault(t.scan_type.value, []).append((t.pnl_pct, w))

    session_adj: dict[str, float] = {}
    for scan_type, entries in session_weighted.items():
        adj = _compute_adjustment(entries, sensitivity=0.2, pnl_cap=0.1,
                                  bounds=(0.8, 1.2), min_significant=5,
                                  t_threshold=1.5)
        if adj is not None:
            session_adj[scan_type] = adj

    # ── (#29) Per-news_category adjustments (v3.4 #4: returned separately) ──
    newscat_weighted: dict[str, list[tuple[float, float]]] = {}
    for t in closed:
        if t.pnl_pct is not None and hasattr(t, "news_category"):
            w = _compute_decay_weight(t.timestamp, half_life)
            newscat_weighted.setdefault(t.news_category, []).append((t.pnl_pct, w))

    newscat_adj: dict[str, float] = {}
    for ncat, entries in newscat_weighted.items():
        adj = _compute_adjustment(entries, sensitivity=0.3, pnl_cap=0.15,
                                  bounds=(0.7, 1.3), min_significant=5,
                                  t_threshold=1.5)
        if adj is not None:
            newscat_adj[ncat] = adj

    # ── v3.4 #1 + v4.2 C1: Per-regime adjustments ──
    # C1: Merge to 2 buckets (calm+normal, elevated+stress) and raise min to 15
    regime_weighted: dict[str, list[tuple[float, float]]] = {}
    for t in closed:
        if t.pnl_pct is not None:
            raw_regime = getattr(t, "market_regime", None) or "normal"
            # C1: Merge into 2 buckets for larger sample sizes
            if raw_regime in ("calm", "normal"):
                merged_regime = "low_vol"
            else:  # elevated, stress
                merged_regime = "high_vol"
            w = _compute_decay_weight(t.timestamp, half_life)
            regime_weighted.setdefault(merged_regime, []).append((t.pnl_pct, w))

    regime_adj: dict[str, float] = {}
    for regime, entries in regime_weighted.items():
        # C1: min 15 trades for regime (was 5 — too few leads to overfitting)
        # M8: t_threshold raised to 1.5
        adj = _compute_adjustment(entries, sensitivity=0.3, pnl_cap=0.15,
                                  bounds=(0.7, 1.3), min_significant=15,
                                  t_threshold=1.5)
        if adj is not None:
            regime_adj[regime] = adj

    # ── v4.2 B5: Direction accuracy adjustment ──
    dir_weighted: dict[str, list[tuple[float, float]]] = {}
    for t in closed:
        if t.pnl_pct is not None:
            w = _compute_decay_weight(t.timestamp, half_life)
            dir_weighted.setdefault(t.direction.value, []).append((t.pnl_pct, w))

    direction_adj: dict[str, float] = {}
    for d, entries in dir_weighted.items():
        adj = _compute_adjustment(entries, sensitivity=0.3, pnl_cap=0.15,
                                  bounds=(0.8, 1.2), min_significant=8)
        if adj is not None:
            direction_adj[d] = adj

    # ── v4.2 B1: Delay bias adjustment ──
    # If we systematically over/underestimate transmission_delay, adjust
    delay_bias_adj = 1.0
    delay_errors = []
    for t in closed:
        predicted = getattr(t, "predicted_transmission_delay", None)
        actual_h = getattr(t, "actual_pricing_time_hours", None)
        if predicted is not None and actual_h is not None and t.pnl_pct is not None:
            actual_score = min(100, actual_h / TRANSMISSION_DELAY_BASELINE_HOURS * 100)
            error = predicted - actual_score  # positive = overestimate delay
            delay_errors.append((error, _compute_decay_weight(t.timestamp, half_life)))

    if len(delay_errors) >= 10:
        total_w = sum(w for _, w in delay_errors)
        if total_w > 0:
            avg_error = sum(e * w for e, w in delay_errors) / total_w
            # If we overestimate delay (avg_error > 0), we enter trades too aggressively → penalize
            # If we underestimate delay (avg_error < 0), we miss edge → boost slightly
            if abs(avg_error) > 10:
                bias_signal = min(0.15, max(-0.15, -avg_error / 100))
                delay_bias_adj = round(1.0 + bias_signal, 3)
                logger.info("B1: delay_bias_adj=%.3f (avg_error=%.1f)", delay_bias_adj, avg_error)

    # ── Multiplicative blend: ticker * category only ──────────────
    # v3.4 #2/#4: session, newscat, regime, direction returned separately
    from .config import ASSET_BY_TICKER
    adjustments: dict[str, float] = {}
    decomposition: dict[str, dict] = {}
    all_tickers = set(ticker_adj.keys())
    for t in closed:
        if t.ticker not in ticker_adj and t.category in cat_adj:
            all_tickers.add(t.ticker)

    for ticker in all_tickers:
        t_mult = ticker_adj.get(ticker, 1.0)
        asset = ASSET_BY_TICKER.get(ticker)
        c_mult = cat_adj.get(asset.category, 1.0) if asset else 1.0

        # v3.4: Only ticker * category in the base blend.
        # Session, newscat, regime, hour, direction applied contextually by select_trade().
        blended = t_mult * c_mult
        adjustments[ticker] = round(max(0.5, min(1.5, blended)), 3)

        # v3.4 #8: Store decomposition for diagnostics
        decomposition[ticker] = {"ticker_mult": t_mult, "cat_mult": c_mult}

    # Log per-dimension decomposition
    logger.info("Learning v4.2: %d tickers, %d categories, %d sessions, %d news_cats, "
                "%d regimes, %d directions, delay_bias=%.3f, half_life=%.0fd",
                len(adjustments), len(cat_adj), len(session_adj),
                len(newscat_adj), len(regime_adj),
                len(direction_adj), delay_bias_adj, half_life)
    if decomposition:
        for ticker, dec in sorted(decomposition.items()):
            if dec["ticker_mult"] != 1.0 or dec["cat_mult"] != 1.0:
                logger.info("  %s: ticker=%.3f, cat=%.3f → base=%.3f",
                            ticker, dec["ticker_mult"], dec["cat_mult"],
                            adjustments.get(ticker, 1.0))
    if session_adj:
        logger.info("  Session adj: %s", session_adj)
    if newscat_adj:
        logger.info("  NewsCategory adj: %s", newscat_adj)
    if regime_adj:
        logger.info("  Regime adj: %s", regime_adj)
    if direction_adj:
        logger.info("  Direction adj: %s", direction_adj)

    return {
        "adjustments": adjustments,
        "session_adj": session_adj,
        "newscat_adj": newscat_adj,
        "regime_adj": regime_adj,
        "direction_adj": direction_adj,
        "delay_bias_adj": delay_bias_adj,
        "decomposition": decomposition,
    }


# ── Cached performance summary (invalidated with learning cache) ────
_cached_perf_summary: str | None = None


def invalidate_perf_summary_cache() -> None:
    """Invalidate the performance summary cache. Called after daily journal."""
    global _cached_perf_summary
    _cached_perf_summary = None


def build_performance_summary(max_recent: int = 15,
                              trades: list[TradeRecommendation] | None = None) -> str:
    """Build a concise performance summary to inject into Claude's scoring prompt.

    v4.2 restructure (E1): alerts-only format — only include data sections that show
    anomalies or actionable insights. Removes verbose breakdowns that add noise.
    v4.2 D4: accepts optional trades parameter to avoid redundant reload.
    v4.2 E2: MAE feedback
    v4.2 E3: signal_reliability precision tracking
    v4.2 B2: magnitude_accuracy tracking
    v4.2 B3: slippage vs estimated spread feedback
    v4.2 D3: partial PG migration detection

    Returns empty string if not enough data.
    """
    global _cached_perf_summary
    if _cached_perf_summary is not None:
        return _cached_perf_summary

    try:
        if trades is None:
            trades = load_trades()
    except Exception as exc:
        logger.warning("build_performance_summary: failed to load trades: %s", exc)
        return ""

    # D3: Detect partial PG migration — warn if both sources have data
    if is_pg_enabled():
        try:
            json_raw = _read_json_locked(TRADES_FILE)
            if json_raw and len(json_raw) > 0:
                pg_count = len([t for t in trades if t.result != TradeResult.PENDING])
                json_count = len([t for t in _parse_trades(json_raw, "D3-check")
                                  if t.result != TradeResult.PENDING])
                if json_count > 0 and pg_count > 0 and abs(pg_count - json_count) > 5:
                    logger.warning("D3: Partial PG migration detected — PG has %d closed, "
                                   "JSON has %d closed. Run migration script.", pg_count, json_count)
        except Exception:
            pass  # Non-critical check

    closed = [t for t in trades if t.result != TradeResult.PENDING and t.pnl_pct is not None]

    if len(closed) < 5:
        return ""

    wins = [t for t in closed if t.result == TradeResult.TP_HIT]
    losses = [t for t in closed if t.result == TradeResult.SL_HIT]
    expired = [t for t in closed if t.result == TradeResult.EXPIRED]
    pnls = [t.pnl_pct for t in closed]
    win_rate = len(wins) / len(closed) * 100 if closed else 0
    avg_pnl = sum(pnls) / len(pnls) if pnls else 0

    # E1: Compact header — always shown
    parts = [
        f"\n--- DONNEES DE PERFORMANCE ---",
        f"N={len(closed)} | WR={win_rate:.0f}% | PnL={sum(pnls):+.1f}% | moy={avg_pnl:+.2f}%",
    ]

    # E1: Alerts-only — only include sections with notable deviations

    # F2: EXPIRED rate monitoring — only if anomalous
    if len(closed) >= 10:
        expired_rate = len(expired) / len(closed) * 100
        if expired_rate > 60:
            parts.append(f"ALERTE CALIBRATION: {expired_rate:.0f}% EXPIRED — "
                         "reduis expected_magnitude.")
        elif expired_rate > 40:
            parts.append(f"EXPIRED eleve: {expired_rate:.0f}%")

    # F3: Realized R/R vs predicted — only if significant gap
    rr_diffs = []
    for t in closed:
        if t.pnl_pct is not None and t.risk_reward > 0:
            if t.result == TradeResult.TP_HIT:
                realized = abs(t.pnl_pct) / t.stop_pct if t.stop_pct > 0 else 0
            elif t.result == TradeResult.SL_HIT:
                realized = -1.0
            else:
                realized = t.pnl_pct / t.stop_pct if t.stop_pct > 0 else 0
            rr_diffs.append(realized - t.risk_reward)
    if len(rr_diffs) >= 5:
        avg_rr_diff = statistics.mean(rr_diffs)
        if avg_rr_diff < -0.5:
            parts.append(f"R/R gap: {avg_rr_diff:+.2f} — stops trop serres?")

    # C4: Drawdown — only if severe
    sorted_closed = sorted(closed, key=lambda t: t.timestamp)
    if len(sorted_closed) >= 5:
        max_losing_streak = 0
        current_streak = 0
        for t in sorted_closed:
            if t.pnl_pct is not None and t.pnl_pct < 0:
                current_streak += 1
                max_losing_streak = max(max_losing_streak, current_streak)
            else:
                current_streak = 0

        cumulative = 0.0
        peak = 0.0
        max_drawdown = 0.0
        for t in sorted_closed:
            if t.pnl_pct is not None:
                cumulative += t.pnl_pct
                peak = max(peak, cumulative)
                dd = peak - cumulative
                max_drawdown = max(max_drawdown, dd)

        if max_losing_streak >= 3:
            parts.append(f"Drawdown: {max_losing_streak} pertes consec., DD max={max_drawdown:.1f}%")

    # C5: PnL skewness — only if anomalous
    if len(pnls) >= 10:
        try:
            n = len(pnls)
            mean_pnl = statistics.mean(pnls)
            std_pnl = statistics.stdev(pnls)
            if std_pnl > 0:
                skew = sum((p - mean_pnl) ** 3 for p in pnls) / (n * std_pnl ** 3)
                if skew < -0.5:
                    parts.append(f"ALERTE: skewness={skew:.2f} (profil defavorable)")
        except Exception:
            pass

    # C1: Streak tracking — only active streaks
    ticker_streaks: dict[str, int] = {}
    for t in sorted_closed:
        tk = t.ticker
        if t.pnl_pct is not None and t.pnl_pct < 0:
            ticker_streaks[tk] = ticker_streaks.get(tk, 0) + 1
        else:
            ticker_streaks[tk] = 0
    bad_streaks = [(tk, s) for tk, s in ticker_streaks.items() if s >= 3]
    if bad_streaks:
        streak_strs = [f"{tk}({s})" for tk, s in sorted(bad_streaks, key=lambda x: -x[1])]
        parts.append(f"Streaks negatifs: {', '.join(streak_strs)}")

    # E1: News category performance — only show outliers (WR < 35% or > 70%)
    by_newscat: dict[str, dict] = {}
    for t in closed:
        nc = getattr(t, "news_category", "other")
        if nc not in by_newscat:
            by_newscat[nc] = {"wins": 0, "total": 0, "pnl": 0.0}
        by_newscat[nc]["total"] += 1
        if t.result == TradeResult.TP_HIT:
            by_newscat[nc]["wins"] += 1
        by_newscat[nc]["pnl"] += t.pnl_pct

    outlier_cats = []
    for nc, stats in sorted(by_newscat.items(), key=lambda x: x[1]["pnl"], reverse=True):
        if stats["total"] >= 3:
            wr = stats["wins"] / stats["total"] * 100
            if wr < 35 or wr > 70:
                outlier_cats.append(f"  {nc}: WR={wr:.0f}%, PnL={stats['pnl']:+.1f}% (n={stats['total']})")
    if outlier_cats:
        parts.append("Outliers par newscat:")
        parts.extend(outlier_cats)

    # Direction accuracy — only if anomalous
    dir_by_cat: dict[str, dict] = {}
    for t in closed:
        if t.pnl_pct is not None and t.pnl_pct != 0:
            nc = getattr(t, "news_category", "other")
            if nc not in dir_by_cat:
                dir_by_cat[nc] = {"correct": 0, "total": 0}
            dir_by_cat[nc]["total"] += 1
            if t.pnl_pct > 0:
                dir_by_cat[nc]["correct"] += 1

    direction_correct = sum(d["correct"] for d in dir_by_cat.values())
    direction_total = sum(d["total"] for d in dir_by_cat.values())
    if direction_total >= 10:
        dir_accuracy = direction_correct / direction_total * 100
        if dir_accuracy < 45:
            parts.append(f"BIAIS DIRECTION: {dir_accuracy:.0f}% — augmente directional_clarity seuil.")
        elif dir_accuracy > 65:
            parts.append(f"Direction solide: {dir_accuracy:.0f}%")

    # Transmission delay bias — only if significant
    delay_errors = []
    for t in closed:
        predicted = getattr(t, "predicted_transmission_delay", None)
        actual_h = getattr(t, "actual_pricing_time_hours", None)
        if predicted is not None and actual_h is not None:
            actual_score = min(100, actual_h / TRANSMISSION_DELAY_BASELINE_HOURS * 100)
            delay_errors.append(predicted - actual_score)

    if len(delay_errors) >= 5:
        avg_error = statistics.mean(delay_errors)
        if abs(avg_error) > 10:
            direction_word = "SURESTIMES" if avg_error > 0 else "SOUS-ESTIMES"
            parts.append(f"BIAIS DELAY: {direction_word} de {abs(avg_error):.0f}pts")

    # E2: MAE feedback — average max adverse excursion
    mae_values = [getattr(t, "mae", None) for t in closed if getattr(t, "mae", None) is not None]
    if len(mae_values) >= 5:
        avg_mae = statistics.mean(mae_values)
        # Only alert if MAE suggests stops are too tight
        sl_hit_count = len(losses)
        if sl_hit_count > 0 and len(closed) > 0:
            sl_rate = sl_hit_count / len(closed) * 100
            if sl_rate > 40 and avg_mae > 0:
                parts.append(f"MAE moy={avg_mae:.2f}% avec SL_HIT={sl_rate:.0f}% — stops possiblement trop serres")

    # E3: signal_reliability precision tracking
    reliability_results: dict[str, dict] = {"high": {"wins": 0, "total": 0},
                                             "low": {"wins": 0, "total": 0}}
    for t in closed:
        rel = getattr(t, "signal_reliability", None)
        if rel is not None:
            bucket = "high" if rel >= 70 else "low"
            reliability_results[bucket]["total"] += 1
            if t.result == TradeResult.TP_HIT:
                reliability_results[bucket]["wins"] += 1

    high_r = reliability_results["high"]
    low_r = reliability_results["low"]
    if high_r["total"] >= 5 and low_r["total"] >= 5:
        high_wr = high_r["wins"] / high_r["total"] * 100
        low_wr = low_r["wins"] / low_r["total"] * 100
        if low_wr > high_wr + 10:
            parts.append(f"ALERTE RELIABILITY: low-rel WR={low_wr:.0f}% > high-rel WR={high_wr:.0f}% — "
                         "signal_reliability mal calibre")

    # B2: magnitude_accuracy tracking
    mag_errors = []
    for t in closed:
        mag = getattr(t, "expected_magnitude", None)
        if mag is not None and t.pnl_pct is not None:
            actual_mag = abs(t.pnl_pct)
            # Normalize: expected_magnitude 50 ≈ 1-3% move → use 2% as mid reference
            expected_pct = mag / 50 * 2.0
            if expected_pct > 0:
                mag_errors.append(actual_mag - expected_pct)
    if len(mag_errors) >= 10:
        avg_mag_err = statistics.mean(mag_errors)
        if avg_mag_err < -1.0:
            parts.append(f"BIAIS MAGNITUDE: surestimee de {abs(avg_mag_err):.1f}pp — reduis expected_magnitude")
        elif avg_mag_err > 1.0:
            parts.append(f"Magnitude sous-estimee de {avg_mag_err:.1f}pp — augmente expected_magnitude")

    # B3: Slippage vs estimated spread feedback
    slippage_values = [getattr(t, "slippage", None) for t in closed
                       if getattr(t, "slippage", None) is not None]
    if len(slippage_values) >= 5:
        avg_slippage = statistics.mean([abs(s) for s in slippage_values])
        from .config import ESTIMATED_SPREADS, DEFAULT_SPREAD
        avg_spread = statistics.mean([ESTIMATED_SPREADS.get(t.ticker, DEFAULT_SPREAD) for t in closed])
        if avg_slippage > avg_spread * 2:
            parts.append(f"SLIPPAGE: {avg_slippage:.3f}% vs spread estime {avg_spread:.3f}% — "
                         "execution degradee ou spreads sous-estimes")

    # Recent trades (compact — last 10)
    recent = sorted(closed, key=lambda t: t.timestamp, reverse=True)[:min(max_recent, 10)]
    if recent:
        parts.append(f"Derniers {len(recent)}:")
        for t in recent:
            nc = getattr(t, "news_category", "?")
            parts.append(
                f"  {t.ticker}({nc}) {t.direction.value}→{t.result.value} {t.pnl_pct:+.2f}%"
            )

    parts.append("--- FIN DONNEES ---")

    # Instructions section (always shown)
    parts.extend([
        "--- INSTRUCTIONS SCORING ---",
        "Le learning applique AUTOMATIQUEMENT des multiplicateurs. Score OBJECTIVEMENT "
        "chaque news selon surprise, delay, awareness, magnitude, reliability. "
        "NE COMPENSE PAS l'historique.",
        "--- FIN INSTRUCTIONS ---",
    ])

    result = "\n".join(parts)
    _cached_perf_summary = result
    return result
