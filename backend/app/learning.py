"""Performance tracking and self-learning via historical trade analysis."""

import fcntl
import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path

from .models import PerformanceStats, TradeRecommendation, TradeResult

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
TRADES_FILE = DATA_DIR / "trades.json"

# Temporal decay: half-life in days. A trade from 30 days ago has weight 0.5.
DECAY_HALF_LIFE_DAYS = 30.0


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
) -> None:
    """Update a pending trade with its outcome."""
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

            _write_trades(trades)
            logger.info("Updated trade %s %s: %s (PnL: %s%%)", ticker, timestamp, result, trade.pnl_pct)
            return

    logger.warning("Trade not found for update: %s %s", ticker, timestamp)


def _write_trades(trades: list[TradeRecommendation]) -> None:
    _write_json_locked(
        TRADES_FILE,
        [t.model_dump(mode="json") for t in trades],
    )


def _compute_decay_weight(trade_timestamp: datetime) -> float:
    """Exponential decay weight based on trade age. Recent trades weigh more."""
    now = datetime.now(timezone.utc)
    age_days = (now - trade_timestamp).total_seconds() / 86400
    return math.exp(-0.693 * age_days / DECAY_HALF_LIFE_DAYS)  # ln(2) ~ 0.693


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


def compute_learning_adjustments() -> dict[str, float]:
    """Compute per-ticker score multipliers based on historical performance.

    Uses temporal decay: recent trades weigh more than old ones.
    Also computes per-category adjustments and blends them.

    Returns dict of ticker -> multiplier (default 1.0, range 0.5-1.5).
    """
    trades = load_trades()
    closed = [t for t in trades if t.result != TradeResult.PENDING]

    if len(closed) < 5:
        return {}  # Not enough data to learn from

    # ── Per-ticker adjustments (with decay) ──────────────────────
    ticker_weighted: dict[str, list[tuple[float, float]]] = {}  # ticker -> [(pnl, weight)]
    for t in closed:
        if t.pnl_pct is not None:
            w = _compute_decay_weight(t.timestamp)
            ticker_weighted.setdefault(t.ticker, []).append((t.pnl_pct, w))

    ticker_adj: dict[str, float] = {}
    for ticker, entries in ticker_weighted.items():
        if len(entries) < 2:
            continue
        total_w = sum(w for _, w in entries)
        if total_w == 0:
            continue
        avg = sum(p * w for p, w in entries) / total_w
        win_rate = sum(w for p, w in entries if p > 0) / total_w
        mult = 1.0 + (win_rate - 0.5) * 0.5 + min(0.25, max(-0.25, avg / 10))
        ticker_adj[ticker] = round(max(0.5, min(1.5, mult)), 3)

    # ── Per-category adjustments (with decay) ────────────────────
    cat_weighted: dict[str, list[tuple[float, float]]] = {}  # category -> [(pnl, weight)]
    for t in closed:
        if t.pnl_pct is not None:
            w = _compute_decay_weight(t.timestamp)
            cat_weighted.setdefault(t.category, []).append((t.pnl_pct, w))

    cat_adj: dict[str, float] = {}
    for cat, entries in cat_weighted.items():
        if len(entries) < 3:
            continue
        total_w = sum(w for _, w in entries)
        if total_w == 0:
            continue
        avg = sum(p * w for p, w in entries) / total_w
        win_rate = sum(w for p, w in entries if p > 0) / total_w
        mult = 1.0 + (win_rate - 0.5) * 0.3 + min(0.15, max(-0.15, avg / 10))
        cat_adj[cat] = round(max(0.7, min(1.3, mult)), 3)

    # ── Blend: ticker adjustment * category adjustment ───────────
    from .config import ASSET_BY_TICKER
    adjustments: dict[str, float] = {}
    all_tickers = set(ticker_adj.keys())
    # Also include tickers that have category stats but not enough per-ticker stats
    for t in closed:
        if t.ticker not in ticker_adj and t.category in cat_adj:
            all_tickers.add(t.ticker)

    for ticker in all_tickers:
        t_mult = ticker_adj.get(ticker, 1.0)
        asset = ASSET_BY_TICKER.get(ticker)
        c_mult = cat_adj.get(asset.category, 1.0) if asset else 1.0
        blended = round(max(0.5, min(1.5, t_mult * c_mult)), 3)
        adjustments[ticker] = blended

    logger.info("Learning adjustments for %d tickers (%d categories)", len(adjustments), len(cat_adj))
    return adjustments
