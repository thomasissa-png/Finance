"""Performance tracking and self-learning via historical trade analysis."""

import fcntl
import json
import logging
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path

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
    """Load all historical trades from disk."""
    try:
        raw = _read_json_locked(TRADES_FILE)
        return [TradeRecommendation(**t) for t in raw]
    except Exception as exc:
        logger.error("Failed to load trades: %s", exc)
        return []


def save_trade(trade: TradeRecommendation) -> None:
    """Append a new trade to the history."""
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


def _is_significant(pnl_values: list[float], min_samples: int = 4) -> bool:
    """P0-#12: Check if we have enough data for a statistically meaningful adjustment.

    Requires:
    1. At least min_samples data points
    2. Standard error small enough that the mean is distinguishable from zero
       (pseudo t-test: |mean| > stderr, i.e. t > 1.0 — lenient threshold for trading)
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
        return abs(mean / stderr) > 1.0
    except (statistics.StatisticsError, ZeroDivisionError):
        return False


def _compute_adjustment(entries: list[tuple[float, float]], sensitivity: float = 0.5,
                        pnl_cap: float = 0.25, bounds: tuple[float, float] = (0.5, 1.5),
                        min_significant: int = 4) -> float | None:
    """Compute a single decay-weighted adjustment with significance check.

    Returns None if not significant, else a multiplier in [bounds].
    """
    if len(entries) < 2:
        return None
    total_w = sum(w for _, w in entries)
    if total_w == 0:
        return None

    # P0-#12: Significance check on raw PnL values
    pnl_values = [p for p, _ in entries]
    if not _is_significant(pnl_values, min_samples=min_significant):
        return None

    avg = sum(p * w for p, w in entries) / total_w
    win_rate = sum(w for p, w in entries if p > 0) / total_w
    mult = 1.0 + (win_rate - 0.5) * sensitivity + min(pnl_cap, max(-pnl_cap, avg / 10))
    lo, hi = bounds
    return round(max(lo, min(hi, mult)), 3)


def compute_learning_adjustments() -> dict[str, float]:
    """Compute per-ticker score multipliers based on historical performance.

    Uses temporal decay: recent trades weigh more than old ones.
    Also computes per-category, per-hour (#28), and per-news_category (#29) adjustments.

    v3 changes:
    - P0-#2: Multiplicative blending instead of additive (signals compound, not average)
    - P0-#12: Significance test — adjustments only applied when statistically meaningful

    Returns dict of ticker -> multiplier (default 1.0, range 0.5-1.5).
    """
    trades = load_trades()
    closed = [t for t in trades if t.result != TradeResult.PENDING]

    if len(closed) < 5:
        return {}  # Not enough data to learn from

    # (#30) Adaptive decay
    half_life = _get_decay_half_life(len(closed))

    # ── Per-ticker adjustments (with decay + significance) ────────
    ticker_weighted: dict[str, list[tuple[float, float]]] = {}
    for t in closed:
        if t.pnl_pct is not None:
            w = _compute_decay_weight(t.timestamp, half_life)
            ticker_weighted.setdefault(t.ticker, []).append((t.pnl_pct, w))

    ticker_adj: dict[str, float] = {}
    for ticker, entries in ticker_weighted.items():
        adj = _compute_adjustment(entries, sensitivity=0.5, pnl_cap=0.25,
                                  bounds=(0.5, 1.5), min_significant=4)
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

    # ── (#28) Per-hour adjustments ────────────────────────────────
    hour_weighted: dict[str, list[tuple[float, float]]] = {}
    for t in closed:
        if t.pnl_pct is not None:
            w = _compute_decay_weight(t.timestamp, half_life)
            hour_weighted.setdefault(t.scan_type.value, []).append((t.pnl_pct, w))

    hour_adj: dict[str, float] = {}
    for scan_type, entries in hour_weighted.items():
        adj = _compute_adjustment(entries, sensitivity=0.2, pnl_cap=0.1,
                                  bounds=(0.8, 1.2), min_significant=5)
        if adj is not None:
            hour_adj[scan_type] = adj

    # ── (#29) Per-news_category adjustments ────────────────────────
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

    # ── P0-#2: MULTIPLICATIVE blend ───────────────────────────────
    # Each dimension is an independent signal. Multiplicative blending means
    # a bad ticker (0.6) on a bad category (0.8) gives 0.48 — real punishment.
    # Additive would give 0.8 — diluted.
    from .config import ASSET_BY_TICKER
    adjustments: dict[str, float] = {}
    all_tickers = set(ticker_adj.keys())
    for t in closed:
        if t.ticker not in ticker_adj and t.category in cat_adj:
            all_tickers.add(t.ticker)

    for ticker in all_tickers:
        t_mult = ticker_adj.get(ticker, 1.0)
        asset = ASSET_BY_TICKER.get(ticker)
        c_mult = cat_adj.get(asset.category, 1.0) if asset else 1.0

        # (#29) News category blend — average across all news categories seen for this ticker
        nc_mults = []
        for t in closed:
            if t.ticker == ticker and hasattr(t, "news_category") and t.news_category in newscat_adj:
                nc_mults.append(newscat_adj[t.news_category])
        nc_mult = sum(nc_mults) / len(nc_mults) if nc_mults else 1.0

        # (#28) Hour blend — use scan type of most recent trade for this ticker
        h_mult = 1.0
        recent_scan = None
        for t in reversed(closed):
            if t.ticker == ticker:
                recent_scan = t.scan_type.value
                break
        if recent_scan and recent_scan in hour_adj:
            h_mult = hour_adj[recent_scan]

        # P0-#2: Multiplicative blend — signals compound instead of averaging
        blended = t_mult * c_mult * nc_mult * h_mult
        adjustments[ticker] = round(max(0.5, min(1.5, blended)), 3)

    logger.info("Learning adjustments for %d tickers (%d categories, %d news_cats, %d hours, half_life=%.0fd)",
                len(adjustments), len(cat_adj), len(newscat_adj), len(hour_adj), half_life)
    return adjustments


def build_performance_summary(max_recent: int = 15) -> str:
    """P1-#1: Build a concise performance summary to inject into Claude's scoring prompt.

    This creates the feedback loop: Claude sees its past performance so it can
    calibrate better. Focuses on:
    - Overall win rate and PnL
    - Best/worst performing news categories
    - Recent trade outcomes (last N)
    - Known biases to correct

    Returns empty string if not enough data.
    """
    trades = load_trades()
    closed = [t for t in trades if t.result != TradeResult.PENDING and t.pnl_pct is not None]

    if len(closed) < 5:
        return ""

    wins = [t for t in closed if t.result == TradeResult.TP_HIT]
    losses = [t for t in closed if t.result == TradeResult.SL_HIT]
    expired = [t for t in closed if t.result == TradeResult.EXPIRED]
    pnls = [t.pnl_pct for t in closed]
    win_rate = len(wins) / len(closed) * 100

    parts = [
        f"\n--- HISTORIQUE DE PERFORMANCE (feedback loop) ---",
        f"Trades clotures: {len(closed)} | Win rate: {win_rate:.0f}% | PnL total: {sum(pnls):+.1f}%",
    ]

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
                cat_lines.append(f"  {nc}: {stats['total']} trades, WR={wr:.0f}%, PnL={stats['pnl']:+.1f}%")
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
    return "\n".join(parts)
