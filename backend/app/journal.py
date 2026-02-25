"""Daily journal: auto-closes trades at 22:00 CET, generates journal entries."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yfinance as yf

from .learning import load_trades, update_trade_result, compute_learning_adjustments
from .models import Direction, JournalEntry, TradeRecommendation, TradeResult

# Module-level function for invalidating learning cache
# Defined here so tests can patch it at backend.app.journal.invalidate_learning_cache
def invalidate_learning_cache():
    """Invalidate learning and performance summary caches — delegates to scheduler module."""
    from .scheduler import invalidate_learning_cache as _invalidate
    from .learning import invalidate_perf_summary_cache
    _invalidate()
    invalidate_perf_summary_cache()

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
JOURNAL_FILE = DATA_DIR / "journal.json"

PARIS_TZ = ZoneInfo("Europe/Paris")


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


def _fetch_day_prices(ticker: str, date: str | None = None) -> tuple[float | None, float | None, float | None]:
    """Fetch day's high, low, and closing price for a ticker.

    If date is provided (YYYY-MM-DD), fetches historical prices for that date.
    Otherwise fetches today's prices.

    Returns (day_high, day_low, close).
    """
    try:
        if date:
            data = yf.Ticker(ticker).history(start=date, period="2d")
        else:
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

    TP is checked first (user preference: focus on TP).
    Returns (result, exit_price, pnl_pct).
    """
    if day_high is None or day_low is None or close is None:
        return TradeResult.EXPIRED, close, None

    if trade.direction == Direction.LONG:
        # Check TP first (priority)
        if day_high >= trade.target_price:
            exit_price = trade.target_price
            pnl = round((exit_price - trade.entry_price) / trade.entry_price * 100, 4)
            return TradeResult.TP_HIT, exit_price, pnl
        if day_low <= trade.stop_price:
            exit_price = trade.stop_price
            pnl = round((exit_price - trade.entry_price) / trade.entry_price * 100, 4)
            return TradeResult.SL_HIT, exit_price, pnl
        pnl = round((close - trade.entry_price) / trade.entry_price * 100, 4)
        return TradeResult.EXPIRED, close, pnl
    else:
        # SHORT — TP first
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
    parts = []

    if result == TradeResult.TP_HIT:
        parts.append(f"Objectif atteint. Gain de {pnl_pct:+.2f}%. La these etait correcte.")
    elif result == TradeResult.SL_HIT:
        parts.append(f"Stop touche. Perte de {pnl_pct:+.2f}%. Le marche n'a pas suivi la news.")
    elif pnl_pct is not None and pnl_pct > 0:
        parts.append(f"Expire en gain ({pnl_pct:+.2f}%). Mouvement insuffisant pour le TP.")
    elif pnl_pct is not None and pnl_pct < 0:
        parts.append(f"Expire en perte ({pnl_pct:+.2f}%). Mouvement contraire mais SL non touche.")
    else:
        parts.append("Expire sans mouvement significatif.")

    # (#24) Add binary event warning if present
    if trade.binary_event_warning:
        parts.append(f" [{trade.binary_event_warning}]")

    # Add volume info
    if trade.volume_confirmed is True:
        parts.append(" Volume confirme.")
    elif trade.volume_confirmed is False:
        parts.append(" Volume faible.")

    return "".join(parts)


def _extract_scan_trace(scan_data: dict, scan_type_value: str, ticker: str | None = None) -> dict:
    """Safely extract scan-level decision trace fields for a JournalEntry.

    With 4 scan keys (europe, mid_session, us, us_session), a trade's scan_type
    alone is ambiguous (e.g. "europe" could be 07:50 or 11:15 scan). We first
    try to find the scan entry that produced this specific trade (matching ticker),
    then fall back to the scan_type_value key.

    Returns a dict of kwargs safe to unpack into JournalEntry().
    Handles corrupt/missing/wrong-type cache data gracefully.
    """
    def _extract(entry: dict) -> dict:
        return {
            "all_scored_news": entry.get("all_scored_news"),
            "rejection_log": entry.get("rejection_log"),
            "decision_summary": entry.get("decision_summary"),
            "learning_state": entry.get("learning_state"),
        }

    # First pass: find the scan entry that matches this trade's ticker
    if ticker:
        for _key, entry in scan_data.items():
            if not isinstance(entry, dict):
                continue
            rec = entry.get("recommendation")
            if isinstance(rec, dict) and rec.get("ticker") == ticker:
                return _extract(entry)

    # Fallback: use scan_type_value directly
    entry = scan_data.get(scan_type_value)
    if not isinstance(entry, dict):
        return {}
    return _extract(entry)


def _load_scan_decision_data() -> dict[str, dict]:
    """Load cached scan results to extract decision trace for journal entries.

    Returns a dict keyed by scan type ("europe"/"us"), with safe fallback
    to empty dict if the file is missing, corrupt, or has unexpected format.
    """
    scans_file = DATA_DIR / "last_scans.json"
    try:
        if scans_file.exists():
            data = json.loads(scans_file.read_text())
            if isinstance(data, dict):
                return data
            logger.warning("Scan cache has unexpected type %s, ignoring", type(data).__name__)
    except (json.JSONDecodeError, Exception) as exc:
        logger.warning("Failed to load scans cache for journal: %s", exc)
    return {}


def run_daily_journal() -> list[dict]:
    """Main job: close all pending trades, generate journal entries for today.

    Called at 22:00 CET by the scheduler.
    Returns the list of new journal entries as dicts.
    """
    today = datetime.now(PARIS_TZ).strftime("%Y-%m-%d")
    logger.info("=== Daily journal for %s ===", today)

    trades = load_trades()
    pending = [t for t in trades if t.result == TradeResult.PENDING]

    if not pending:
        logger.info("No pending trades to close")
        return []

    # Dedup: check existing journal entries to avoid duplicates
    existing = load_journal()
    existing_keys = {(e.ticker, e.entry_time.isoformat() if e.entry_time else "") for e in existing}

    # Load scan-level decision data for enriching journal entries
    scan_data = _load_scan_decision_data()

    new_entries: list[JournalEntry] = []

    # (I7) Pre-fetch all day prices in parallel to speed up journal closure
    from concurrent.futures import ThreadPoolExecutor, as_completed

    price_tasks: dict[tuple[str, str | None], TradeRecommendation] = {}
    for trade in pending:
        trade_date = trade.timestamp.astimezone(PARIS_TZ).strftime("%Y-%m-%d")
        trade_key = (trade.ticker, trade.timestamp.isoformat())
        if trade_key in existing_keys:
            continue
        fetch_date = trade_date if trade_date != today else None
        price_tasks[(trade.ticker, fetch_date)] = trade

    price_cache: dict[tuple[str, str | None], tuple[float | None, float | None, float | None]] = {}
    if price_tasks:
        with ThreadPoolExecutor(max_workers=min(5, len(price_tasks))) as executor:
            futures = {
                executor.submit(_fetch_day_prices, ticker, date): (ticker, date)
                for ticker, date in price_tasks
            }
            for future in as_completed(futures):
                key = futures[future]
                try:
                    price_cache[key] = future.result(timeout=15)
                except Exception as exc:
                    logger.warning("Parallel price fetch failed for %s: %s", key[0], exc)
                    price_cache[key] = (None, None, None)

    for trade in pending:
        trade_date = trade.timestamp.astimezone(PARIS_TZ).strftime("%Y-%m-%d")

        # Skip if already in journal (dedup)
        trade_key = (trade.ticker, trade.timestamp.isoformat())
        if trade_key in existing_keys:
            logger.info("Skipping duplicate journal entry for %s %s", trade.ticker, trade_date)
            continue

        if trade_date != today:
            logger.info("Force-closing old pending trade: %s from %s", trade.ticker, trade_date)

        # Use pre-fetched prices from parallel cache
        fetch_date = trade_date if trade_date != today else None
        day_high, day_low, close = price_cache.get((trade.ticker, fetch_date), (None, None, None))
        result, exit_price, pnl_pct = _determine_result(trade, day_high, day_low, close)

        # P1-#6: Compute actual pricing time and delay accuracy
        actual_pricing_hours = None
        delay_accuracy = None
        if result in (TradeResult.TP_HIT, TradeResult.SL_HIT) and trade.closed_at:
            actual_pricing_hours = round(
                (trade.closed_at - trade.timestamp).total_seconds() / 3600, 2
            )
        elif result in (TradeResult.TP_HIT, TradeResult.SL_HIT):
            # Estimate: assume hit happened during trading day (~8h window for day trading)
            actual_pricing_hours = 8.0  # conservative default

        if trade.predicted_transmission_delay is not None and actual_pricing_hours is not None:
            # Convert actual hours to 0-100 scale (6h = 100)
            actual_delay_score = min(100, actual_pricing_hours / 6 * 100)
            delay_accuracy = round(trade.predicted_transmission_delay - actual_delay_score, 1)

        # Update the trade in the learning system (with P1-#6 delay accuracy)
        if exit_price is not None:
            update_trade_result(
                trade.timestamp, trade.ticker, result, exit_price,
                actual_pricing_time_hours=actual_pricing_hours,
                delay_accuracy=delay_accuracy,
            )

        now = datetime.now(timezone.utc)
        review = _build_review(trade, result, pnl_pct)

        entry = JournalEntry(
            date=trade_date,
            scan_type=trade.scan_type,
            news_title=trade.news_headline or trade.catalyst[:200],
            news_source=trade.news_sources[0] if trade.news_sources else "—",
            news_category=trade.news_category,
            reasoning=trade.catalyst,
            score=trade.confidence,
            ticker=trade.ticker,
            asset_name=trade.asset_name,
            asset_category=trade.category,
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
            binary_event_warning=trade.binary_event_warning,
            # v3: Score decomposition
            raw_claude_score=trade.raw_claude_score,
            learning_multiplier=trade.learning_multiplier,
            # v3: Contextual features
            vix_at_trade=trade.vix_at_trade,
            market_regime=trade.market_regime,
            # v3: Transmission delay tracking (P1-#6)
            predicted_transmission_delay=trade.predicted_transmission_delay,
            actual_pricing_time_hours=actual_pricing_hours,
            delay_accuracy=delay_accuracy,
            # v3: Scan-level decision trace (from cached scan results)
            **_extract_scan_trace(scan_data, trade.scan_type.value, trade.ticker),
        )
        new_entries.append(entry)
        logger.info(
            "Journal: %s %s %s → %s (PnL: %s%%, delay_accuracy: %s)",
            trade.direction.value, trade.ticker, trade.asset_name,
            result.value, pnl_pct, delay_accuracy,
        )

    # Append to journal file
    if new_entries:
        existing.extend(new_entries)
        _save_journal(existing)

    # (#26) Invalidate learning cache after journal
    invalidate_learning_cache()

    # Log learning update
    adjustments = compute_learning_adjustments()
    if adjustments:
        logger.info("Learning adjustments updated: %d tickers", len(adjustments))

    return [e.model_dump(mode="json") for e in new_entries]
