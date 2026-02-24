"""Daily journal: auto-closes trades at 22:00 CET, generates journal entries."""

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yfinance as yf

from .learning import load_trades, update_trade_result, compute_learning_adjustments
from .models import Direction, JournalEntry, TradeRecommendation, TradeResult

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
JOURNAL_FILE = DATA_DIR / "journal.json"

CET = timezone(timedelta(hours=1))


def _ensure_journal_file() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not JOURNAL_FILE.exists():
        JOURNAL_FILE.write_text("[]")


def load_journal() -> list[JournalEntry]:
    """Load all journal entries from disk."""
    _ensure_journal_file()
    try:
        raw = json.loads(JOURNAL_FILE.read_text())
        return [JournalEntry(**e) for e in raw]
    except (json.JSONDecodeError, Exception) as exc:
        logger.error("Failed to load journal: %s", exc)
        return []


def _save_journal(entries: list[JournalEntry]) -> None:
    _ensure_journal_file()
    JOURNAL_FILE.write_text(
        json.dumps([e.model_dump(mode="json") for e in entries], indent=2, default=str)
    )


def _fetch_day_prices(ticker: str) -> tuple[float | None, float | None, float | None]:
    """Fetch today's high, low, and closing price for a ticker.

    Returns (day_high, day_low, close).
    """
    try:
        data = yf.Ticker(ticker).history(period="1d")
        if data.empty:
            return None, None, None
        row = data.iloc[-1]
        return float(row["High"]), float(row["Low"]), float(row["Close"])
    except Exception as exc:
        logger.warning("Day price fetch failed for %s: %s", ticker, exc)
        return None, None, None


def _determine_result(
    trade: TradeRecommendation,
    day_high: float | None,
    day_low: float | None,
    close: float | None,
) -> tuple[TradeResult, float | None, float | None]:
    """Determine trade outcome based on day price action.

    Returns (result, exit_price, pnl_pct).
    """
    if day_high is None or day_low is None or close is None:
        return TradeResult.EXPIRED, close, None

    if trade.direction == Direction.LONG:
        # Check if target was hit (day high reached target)
        if day_high >= trade.target_price:
            exit_price = trade.target_price
            pnl = round((exit_price - trade.entry_price) / trade.entry_price * 100, 4)
            return TradeResult.TP_HIT, exit_price, pnl
        # Check if stop was hit (day low breached stop)
        if day_low <= trade.stop_price:
            exit_price = trade.stop_price
            pnl = round((exit_price - trade.entry_price) / trade.entry_price * 100, 4)
            return TradeResult.SL_HIT, exit_price, pnl
        # Neither hit — expired at close
        pnl = round((close - trade.entry_price) / trade.entry_price * 100, 4)
        return TradeResult.EXPIRED, close, pnl
    else:
        # SHORT
        if day_low <= trade.target_price:
            exit_price = trade.target_price
            pnl = round((trade.entry_price - exit_price) / trade.entry_price * 100, 4)
            return TradeResult.TP_HIT, exit_price, pnl
        if day_high >= trade.stop_price:
            exit_price = trade.stop_price
            pnl = round((trade.entry_price - exit_price) / trade.entry_price * 100, 4)
            return TradeResult.SL_HIT, exit_price, pnl
        pnl = round((trade.entry_price - close) / trade.entry_price * 100, 4)
        return TradeResult.EXPIRED, close, pnl


def _build_review(trade: TradeRecommendation, result: TradeResult, pnl_pct: float | None) -> str:
    """Generate a short post-trade review line."""
    if result == TradeResult.TP_HIT:
        return f"Objectif atteint. Gain de {pnl_pct:+.2f}%. La these etait correcte."
    elif result == TradeResult.SL_HIT:
        return f"Stop touche. Perte de {pnl_pct:+.2f}%. Le marche n'a pas suivi la news."
    elif pnl_pct is not None and pnl_pct > 0:
        return f"Expire en gain ({pnl_pct:+.2f}%). Mouvement insuffisant pour le TP."
    elif pnl_pct is not None and pnl_pct < 0:
        return f"Expire en perte ({pnl_pct:+.2f}%). Mouvement contraire mais SL non touche."
    return "Expire sans mouvement significatif."


def run_daily_journal() -> list[dict]:
    """Main job: close all pending trades, generate journal entries for today.

    Called at 22:00 CET by the scheduler.
    Returns the list of new journal entries as dicts.
    """
    today = datetime.now(CET).strftime("%Y-%m-%d")
    logger.info("=== Daily journal for %s ===", today)

    trades = load_trades()
    pending = [t for t in trades if t.result == TradeResult.PENDING]

    if not pending:
        logger.info("No pending trades to close")
        return []

    new_entries: list[JournalEntry] = []

    for trade in pending:
        # Check if trade is from today
        trade_date = trade.timestamp.astimezone(CET).strftime("%Y-%m-%d")
        if trade_date != today:
            # Old trade still pending — force close it too
            logger.info("Force-closing old pending trade: %s from %s", trade.ticker, trade_date)

        day_high, day_low, close = _fetch_day_prices(trade.ticker)
        result, exit_price, pnl_pct = _determine_result(trade, day_high, day_low, close)

        # Update the trade in the learning system
        if exit_price is not None:
            update_trade_result(trade.timestamp, trade.ticker, result, exit_price)

        now = datetime.now(timezone.utc)
        review = _build_review(trade, result, pnl_pct)

        entry = JournalEntry(
            date=trade_date,
            scan_type=trade.scan_type,
            news_title=trade.catalyst[:200],
            news_source=trade.news_sources[0] if trade.news_sources else "—",
            reasoning=trade.catalyst,
            score=trade.confidence,
            ticker=trade.ticker,
            asset_name=trade.asset_name,
            direction=trade.direction,
            entry_time=trade.timestamp,
            entry_price=trade.entry_price,
            exit_time=now,
            exit_price=exit_price,
            day_high=day_high,
            day_low=day_low,
            result=result,
            pnl_pct=pnl_pct,
            review=review,
        )
        new_entries.append(entry)
        logger.info(
            "Journal: %s %s %s → %s (PnL: %s%%)",
            trade.direction.value, trade.ticker, trade.asset_name,
            result.value, pnl_pct,
        )

    # Append to journal file
    existing = load_journal()
    existing.extend(new_entries)
    _save_journal(existing)

    # Log learning update
    adjustments = compute_learning_adjustments()
    if adjustments:
        logger.info("Learning adjustments updated: %d tickers", len(adjustments))

    return [e.model_dump(mode="json") for e in new_entries]
