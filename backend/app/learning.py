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
"""

import fcntl
import json
import logging
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path

from .database import is_pg_enabled
from .models import PerformanceStats, TradeRecommendation, TradeResult

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
TRADES_FILE = DATA_DIR / "trades.json"

# (#30) Adaptive decay: gradual transition from 45 to 30 days
DECAY_HALF_LIFE_DAYS_LOW = 45.0   # When few trades
DECAY_HALF_LIFE_DAYS_HIGH = 30.0  # When many trades
DECAY_TRANSITION_START = 50       # Start transitioning at 50 closed trades
DECAY_TRANSITION_END = 200        # Fully transitioned at 200 closed trades


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


def load_trades() -> list[TradeRecommendation]:
    """Load all historical trades."""
    if is_pg_enabled():
        try:
            from .database import pg_load_trades
            raw = pg_load_trades()
            return [TradeRecommendation(**t) for t in raw]
        except Exception as exc:
            logger.error("Failed to load trades from PostgreSQL: %s", exc)
            return []
    try:
        raw = _read_json_locked(TRADES_FILE)
        return [TradeRecommendation(**t) for t in raw]
    except Exception as exc:
        logger.error("Failed to load trades: %s", exc)
        return []


def save_trade(trade: TradeRecommendation) -> None:
    """Append a new trade to the history."""
    if is_pg_enabled():
        from .database import pg_save_trade
        pg_save_trade(trade.model_dump(mode="json"))
        logger.info("Saved trade: %s %s %s", trade.direction, trade.ticker, trade.catalyst[:50])
        return
    trades = load_trades()
    trades.append(trade)
    _write_trades(trades)
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
            _signal_learning_cache_invalidation()
        else:
            logger.warning("Trade not found for update: %s %s", ticker, timestamp)
        return

    trades = load_trades()
    for trade in trades:
        if trade.ticker == ticker and trade.timestamp == timestamp and trade.result == TradeResult.PENDING:
            trade.result = result
            trade.exit_price = exit_price
            trade.closed_at = datetime.now(timezone.utc)

            if trade.direction.value == "LONG":
                trade.pnl_pct = round((exit_price - trade.entry_price) / trade.entry_price * 100, 4)
            else:
                trade.pnl_pct = round((trade.entry_price - exit_price) / trade.entry_price * 100, 4)

            # P1-#6: Store transmission delay accuracy on the trade
            if actual_pricing_time_hours is not None:
                trade.actual_pricing_time_hours = actual_pricing_time_hours
            if delay_accuracy is not None:
                trade.delay_accuracy = delay_accuracy

            _write_trades(trades)
            logger.info("Updated trade %s %s: %s (PnL: %s%%)", ticker, timestamp, result, trade.pnl_pct)
            # Signal cache invalidation — imported lazily to avoid circular imports
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
                    t_threshold: float = 1.0) -> bool:
    """P0-#12: Check if we have enough data for a statistically meaningful adjustment.

    Requires:
    1. At least min_samples data points
    2. Standard error small enough that the mean is distinguishable from zero
       (pseudo t-test: |mean/stderr| > t_threshold)

    v3.4: t_threshold is configurable per dimension.
    Per-ticker uses 1.5 (stricter — small samples, high noise).
    Per-category/session/newscat uses 1.0 (larger pool, less noise).
    """
    if len(pnl_values) < min_samples:
        return False
    if len(pnl_values) < 2:
        return False
    try:
        mean = statistics.mean(pnl_values)
        stdev = statistics.stdev(pnl_values)
        stderr = stdev / math.sqrt(len(pnl_values))
        if stderr == 0:
            return True  # All identical values — signal is clear
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
        return None
    total_w = sum(w for _, w in entries)
    if total_w == 0:
        return None

    # P0-#12: Significance check on raw PnL values
    pnl_values = [p for p, _ in entries]
    if not _is_significant(pnl_values, min_samples=min_significant,
                           t_threshold=t_threshold):
        return None

    # v3.4 #6: PnL-signed signal — decay-weighted average PnL
    avg_pnl = sum(p * w for p, w in entries) / total_w
    # Normalize: divide by a reference scale (1% PnL = strong signal)
    pnl_signal = min(pnl_cap, max(-pnl_cap, avg_pnl / 2.0))
    mult = 1.0 + pnl_signal * sensitivity
    lo, hi = bounds
    return round(max(lo, min(hi, mult)), 3)


def compute_learning_adjustments() -> dict:
    """Compute per-ticker score multipliers based on historical performance.

    Uses temporal decay: recent trades weigh more than old ones.
    Also computes per-category, per-session (#28), per-news_category (#29),
    and per-regime (#1 v3.4) adjustments.

    v3 changes:
    - P0-#2: Multiplicative blending instead of additive (signals compound, not average)
    - P0-#12: Significance test — adjustments only applied when statistically meaningful

    v3.4 audit changes:
    - #1: Regime-conditional learning (VIX calm/normal/elevated/stress)
    - #2: Session/hour adj returned separately — applied by current scan, not last trade
    - #3: Per-ticker significance raised (t>1.5, min 8 trades)
    - #4: News category uses direct newscat_adj instead of averaging history
    - #8: Per-dimension decomposition logged for diagnostics

    Returns dict with:
    - "adjustments": dict[ticker, multiplier] (default 1.0, range 0.5-1.5)
    - "session_adj": dict[scan_type, multiplier] — applied by caller based on current scan
    - "newscat_adj": dict[news_category, multiplier] — applied by caller based on current news
    - "regime_adj": dict[regime, multiplier] — applied by caller based on current VIX regime
    - "decomposition": dict[ticker, {ticker_mult, cat_mult}] — for diagnostics (#8)
    """
    trades = load_trades()
    closed = [t for t in trades if t.result != TradeResult.PENDING]

    if len(closed) < 5:
        return {}  # Not enough data to learn from

    # (#30) Adaptive decay
    half_life = _get_decay_half_life(len(closed))

    # ── Per-ticker adjustments (v3.4 #3: stricter — t>1.5, min 8) ────
    ticker_weighted: dict[str, list[tuple[float, float]]] = {}
    for t in closed:
        if t.pnl_pct is not None:
            w = _compute_decay_weight(t.timestamp, half_life)
            ticker_weighted.setdefault(t.ticker, []).append((t.pnl_pct, w))

    ticker_adj: dict[str, float] = {}
    for ticker, entries in ticker_weighted.items():
        adj = _compute_adjustment(entries, sensitivity=0.5, pnl_cap=0.25,
                                  bounds=(0.5, 1.5), min_significant=8,
                                  t_threshold=1.5)
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
                                  bounds=(0.7, 1.3), min_significant=5)
        if adj is not None:
            cat_adj[cat] = adj

    # ── (#28) Per-session adjustments (v3.4 #2: returned separately) ──
    hour_weighted: dict[str, list[tuple[float, float]]] = {}
    for t in closed:
        if t.pnl_pct is not None:
            w = _compute_decay_weight(t.timestamp, half_life)
            hour_weighted.setdefault(t.scan_type.value, []).append((t.pnl_pct, w))

    session_adj: dict[str, float] = {}
    for scan_type, entries in hour_weighted.items():
        adj = _compute_adjustment(entries, sensitivity=0.2, pnl_cap=0.1,
                                  bounds=(0.8, 1.2), min_significant=5)
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
                                  bounds=(0.7, 1.3), min_significant=5)
        if adj is not None:
            newscat_adj[ncat] = adj

    # ── v3.4 #1: Per-regime adjustments (VIX-conditional learning) ──
    regime_weighted: dict[str, list[tuple[float, float]]] = {}
    for t in closed:
        if t.pnl_pct is not None:
            regime = getattr(t, "market_regime", None) or "normal"
            w = _compute_decay_weight(t.timestamp, half_life)
            regime_weighted.setdefault(regime, []).append((t.pnl_pct, w))

    regime_adj: dict[str, float] = {}
    for regime, entries in regime_weighted.items():
        adj = _compute_adjustment(entries, sensitivity=0.3, pnl_cap=0.15,
                                  bounds=(0.7, 1.3), min_significant=5)
        if adj is not None:
            regime_adj[regime] = adj

    # ── Multiplicative blend: ticker * category only ──────────────
    # v3.4 #2/#4: session, newscat, and regime are returned separately
    # and applied contextually by the caller (based on CURRENT scan/news/regime).
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
        # Session, newscat, and regime applied contextually by select_trade().
        blended = t_mult * c_mult
        adjustments[ticker] = round(max(0.5, min(1.5, blended)), 3)

        # v3.4 #8: Store decomposition for diagnostics
        decomposition[ticker] = {"ticker_mult": t_mult, "cat_mult": c_mult}

    # v3.4 #8: Log per-dimension decomposition
    logger.info("Learning v3.4: %d tickers, %d categories, %d sessions, %d news_cats, "
                "%d regimes, half_life=%.0fd",
                len(adjustments), len(cat_adj), len(session_adj),
                len(newscat_adj), len(regime_adj), half_life)
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

    return {
        "adjustments": adjustments,
        "session_adj": session_adj,
        "newscat_adj": newscat_adj,
        "regime_adj": regime_adj,
        "decomposition": decomposition,
    }


# ── Cached performance summary (invalidated with learning cache) ────
_cached_perf_summary: str | None = None


def invalidate_perf_summary_cache() -> None:
    """Invalidate the performance summary cache. Called after daily journal."""
    global _cached_perf_summary
    _cached_perf_summary = None


def build_performance_summary(max_recent: int = 15) -> str:
    """P1-#1: Build a concise performance summary to inject into Claude's scoring prompt.

    This creates the feedback loop: Claude sees its past performance so it can
    calibrate better. Cached to avoid re-reading trades.json on every scan.

    Returns empty string if not enough data.
    """
    global _cached_perf_summary
    if _cached_perf_summary is not None:
        return _cached_perf_summary

    try:
        trades = load_trades()
    except Exception as exc:
        logger.warning("build_performance_summary: failed to load trades: %s", exc)
        return ""
    closed = [t for t in trades if t.result != TradeResult.PENDING and t.pnl_pct is not None]

    if len(closed) < 5:
        return ""

    wins = [t for t in closed if t.result == TradeResult.TP_HIT]
    losses = [t for t in closed if t.result == TradeResult.SL_HIT]
    expired = [t for t in closed if t.result == TradeResult.EXPIRED]
    pnls = [t.pnl_pct for t in closed]
    win_rate = len(wins) / len(closed) * 100 if closed else 0

    # v3.4 #11: Benchmark — compare win rate to random baseline (50%)
    avg_pnl = sum(pnls) / len(pnls) if pnls else 0
    parts = [
        f"\n--- HISTORIQUE DE PERFORMANCE (feedback loop) ---",
        f"Trades clotures: {len(closed)} | Win rate: {win_rate:.0f}% (baseline: 50%) | "
        f"PnL total: {sum(pnls):+.1f}% | PnL moyen: {avg_pnl:+.2f}%",
    ]

    # v3.4 #7: Anti-double-counting note — tell Claude NOT to adjust scores
    # based on this history, because the learning system already does it.
    parts.append("NOTE: N'ajuste PAS tes scores surprise/transmission_delay en fonction "
                 "de cet historique — le systeme de learning applique deja des multiplicateurs "
                 "automatiques. Ton role est de scorer OBJECTIVEMENT chaque news.")

    # Performance by news category
    by_newscat: dict[str, dict] = {}
    for t in closed:
        nc = getattr(t, "news_category", "other")
        if nc not in by_newscat:
            by_newscat[nc] = {"wins": 0, "total": 0, "pnl": 0.0}
        by_newscat[nc]["total"] += 1
        if t.result == TradeResult.TP_HIT:
            by_newscat[nc]["wins"] += 1
        by_newscat[nc]["pnl"] += t.pnl_pct

    if by_newscat:
        cat_lines = []
        for nc, stats in sorted(by_newscat.items(), key=lambda x: x[1]["pnl"], reverse=True):
            if stats["total"] >= 3:
                wr = stats["wins"] / stats["total"] * 100
                cat_lines.append(f"  {nc}: {stats['total']} trades, WR={wr:.0f}% (vs 50%), PnL={stats['pnl']:+.1f}%")
        if cat_lines:
            parts.append("Par categorie de news (min 3 trades):")
            parts.extend(cat_lines)

    # Recent trades (last N)
    recent = sorted(closed, key=lambda t: t.timestamp, reverse=True)[:max_recent]
    if recent:
        parts.append(f"Derniers {len(recent)} trades:")
        for t in recent:
            nc = getattr(t, "news_category", "?")
            parts.append(
                f"  {t.ticker} ({nc}) {t.direction.value} → {t.result.value} {t.pnl_pct:+.2f}%"
            )

    # Biases detected — transmission_delay accuracy
    delay_errors = []
    for t in closed:
        predicted = getattr(t, "predicted_transmission_delay", None)
        actual_h = getattr(t, "actual_pricing_time_hours", None)
        if predicted is not None and actual_h is not None:
            # Convert actual hours to 0-100 scale (6h = 100)
            actual_score = min(100, actual_h / 6 * 100)
            delay_errors.append(predicted - actual_score)

    if len(delay_errors) >= 5:
        avg_error = statistics.mean(delay_errors)
        if abs(avg_error) > 10:
            if avg_error > 0:
                parts.append(f"BIAIS DETECTE: Tu SURESTIMES le transmission_delay de {avg_error:+.0f} points en moyenne. "
                             "Les marches pricent PLUS VITE que tu ne le penses. Corrige a la baisse.")
            else:
                parts.append(f"BIAIS DETECTE: Tu SOUS-ESTIMES le transmission_delay de {avg_error:+.0f} points en moyenne. "
                             "Les marches pricent PLUS LENTEMENT que tu ne le penses. Corrige a la hausse.")

    parts.append("--- FIN HISTORIQUE ---")
    result = "\n".join(parts)
    _cached_perf_summary = result
    return result
