"""Performance tracking and self-learning via historical trade analysis."""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from .models import PerformanceStats, TradeRecommendation, TradeResult

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
TRADES_FILE = DATA_DIR / "trades.json"


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not TRADES_FILE.exists():
        TRADES_FILE.write_text("[]")


def load_trades() -> list[TradeRecommendation]:
    """Load all historical trades from disk."""
    _ensure_data_dir()
    try:
        raw = json.loads(TRADES_FILE.read_text())
        return [TradeRecommendation(**t) for t in raw]
    except (json.JSONDecodeError, Exception) as exc:
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
    _ensure_data_dir()
    TRADES_FILE.write_text(
        json.dumps([t.model_dump(mode="json") for t in trades], indent=2, default=str)
    )


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

    Simple approach: if a ticker historically performs well on news trades,
    boost its score. If it performs poorly, penalize it.

    Returns dict of ticker -> multiplier (default 1.0, range 0.5-1.5).
    """
    trades = load_trades()
    closed = [t for t in trades if t.result != TradeResult.PENDING]

    if len(closed) < 5:
        return {}  # Not enough data to learn from

    ticker_stats: dict[str, list[float]] = {}
    for t in closed:
        if t.pnl_pct is not None:
            ticker_stats.setdefault(t.ticker, []).append(t.pnl_pct)

    adjustments: dict[str, float] = {}
    for ticker, pnls in ticker_stats.items():
        if len(pnls) < 2:
            continue
        avg = sum(pnls) / len(pnls)
        win_rate = sum(1 for p in pnls if p > 0) / len(pnls)

        # Multiplier: base 1.0, adjusted by win rate and avg pnl
        # Win rate 70%+ → boost, <40% → penalize
        multiplier = 1.0 + (win_rate - 0.5) * 0.5 + min(0.25, max(-0.25, avg / 10))
        adjustments[ticker] = round(max(0.5, min(1.5, multiplier)), 3)

    logger.info("Learning adjustments for %d tickers", len(adjustments))
    return adjustments
