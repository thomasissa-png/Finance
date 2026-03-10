"""Position monitor: trailing stop + time stop for intraday trade management.

Runs every 15 minutes during trading hours. For each PENDING trade:
1. Trailing stop: move SL to breakeven at +35% TP, lock 50% of move at +60% TP
2. Time stop: close flat if move < 25% of TP target after 3h, close everything after 5h
3. Fetch live prices via Twelve Data (primary) / yfinance (fallback)

This module is the "position management" layer that was missing — without it,
trades rely entirely on end-of-day journal closure, missing intraday opportunities
to lock in profits or cut losers early.
"""

import logging
import math
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from .learning import load_trades, update_trade_result, update_trade_stop
from .market_data import fetch_history, validate_price
from .models import Direction, TradeRecommendation, TradeResult

logger = logging.getLogger(__name__)

PARIS_TZ = ZoneInfo("Europe/Paris")

# ── Trailing stop thresholds (v3.7 — adapte levier 5-10x) ────────
# At +35% of TP move: move SL to breakeven (entry price)
# Baisse de 50% → 35% : avec levier 5x, +0.35% = +1.75% deja securise
TRAILING_BREAKEVEN_THRESHOLD = 0.35
# At +60% of TP move: move SL to lock in 50% of current move
# Baisse de 75% → 60% : verrouille les gains plus tot
TRAILING_LOCK_THRESHOLD = 0.60
TRAILING_LOCK_FRACTION = 0.50

# ── Time stop thresholds ──────────────────────────────────────────
# After 3h: close if move < 25% of expected TP move (trade going nowhere)
TIME_STOP_HOURS_SOFT = 3.0
TIME_STOP_MIN_PROGRESS = 0.25
# After 5h: close everything regardless (intraday discipline)
TIME_STOP_HOURS_HARD = 5.0


def _get_current_price(ticker: str) -> float | None:
    """Fetch the most recent price for a ticker.

    v8.4 fix P17-E1: Try fetch_price() first for live/recent quotes,
    fall back to daily bars. Previous version only used daily bars which
    return previous day's close during trading hours, causing stale
    trailing stop decisions.
    """
    # Try live quote first via market_data module
    try:
        from .market_data import fetch_price
        price = fetch_price(ticker)
        if price is not None and math.isfinite(price):
            is_valid, reason = validate_price(ticker, price)
            if is_valid:
                return price
            else:
                logger.warning("Position monitor: rejected price for %s — %s", ticker, reason)
    except Exception as exc:
        logger.debug("fetch_price failed for %s: %s — falling back to daily bars", ticker, exc)
    # Fallback to daily bars
    try:
        data = fetch_history(ticker, period_days=2, interval="1day")
        if data is not None and not data.empty:
            price = float(data["Close"].iloc[-1])
            if not math.isnan(price) and not math.isinf(price):
                return price
    except Exception as exc:
        logger.debug("Price fetch failed for %s: %s", ticker, exc)
    return None


def _compute_move_progress(trade: TradeRecommendation, current_price: float) -> float:
    """Compute how far price has moved toward TP as a fraction (0.0 to 1.0+).

    Returns negative values if price moved against the trade.
    """
    if trade.direction == Direction.LONG:
        total_move = trade.target_price - trade.entry_price
        current_move = current_price - trade.entry_price
    else:
        total_move = trade.entry_price - trade.target_price
        current_move = trade.entry_price - current_price

    if total_move == 0:
        return 0.0
    return current_move / total_move


def _compute_trailing_stop(
    trade: TradeRecommendation, current_price: float, progress: float,
) -> float | None:
    """Compute new trailing stop level based on progress toward TP.

    Returns the new stop price if it should be tightened, None otherwise.
    Only tightens — never loosens the stop.
    """
    if progress >= TRAILING_LOCK_THRESHOLD:
        # Lock in 50% of current move
        if trade.direction == Direction.LONG:
            current_move = current_price - trade.entry_price
            new_stop = trade.entry_price + current_move * TRAILING_LOCK_FRACTION
            # Only tighten (new stop must be above current stop)
            if new_stop > trade.stop_price:
                return round(new_stop, 4)
        else:
            current_move = trade.entry_price - current_price
            new_stop = trade.entry_price - current_move * TRAILING_LOCK_FRACTION
            if new_stop < trade.stop_price:
                return round(new_stop, 4)

    elif progress >= TRAILING_BREAKEVEN_THRESHOLD:
        # Move stop to breakeven
        if trade.direction == Direction.LONG:
            if trade.entry_price > trade.stop_price:
                return trade.entry_price
        else:
            if trade.entry_price < trade.stop_price:
                return trade.entry_price

    return None


def _check_time_stop(
    trade: TradeRecommendation, progress: float, now: datetime,
) -> tuple[bool, str]:
    """Check if a time-based stop should close the trade.

    Returns (should_close, reason).
    """
    trade_age_hours = (now - trade.timestamp).total_seconds() / 3600

    # Hard time stop: close everything after 5h
    if trade_age_hours >= TIME_STOP_HOURS_HARD:
        return True, f"Time stop dur: {trade_age_hours:.1f}h > {TIME_STOP_HOURS_HARD}h"

    # Soft time stop: close if not making enough progress after 3h
    if trade_age_hours >= TIME_STOP_HOURS_SOFT and progress < TIME_STOP_MIN_PROGRESS:
        return True, (
            f"Time stop souple: {trade_age_hours:.1f}h avec seulement "
            f"{progress*100:.0f}% de progression (seuil: {TIME_STOP_MIN_PROGRESS*100:.0f}%)"
        )

    return False, ""


def _check_stop_hit(trade: TradeRecommendation, current_price: float) -> bool:
    """Check if current price has hit the stop loss."""
    if trade.direction == Direction.LONG:
        return current_price <= trade.stop_price
    else:
        return current_price >= trade.stop_price


def _check_tp_hit(trade: TradeRecommendation, current_price: float) -> bool:
    """Check if current price has hit the take profit."""
    if trade.direction == Direction.LONG:
        return current_price >= trade.target_price
    else:
        return current_price <= trade.target_price


def monitor_positions() -> list[dict]:
    """Main monitoring loop — check all PENDING trades and manage positions.

    Called every 15 minutes during trading hours by the scheduler.
    Returns list of actions taken for logging/API.
    """
    now = datetime.now(timezone.utc)
    now_paris = now.astimezone(PARIS_TZ)

    # Only monitor during trading hours (07:00 - 20:00 CET, weekdays)
    if now_paris.weekday() >= 5:
        return []
    if now_paris.hour < 7 or now_paris.hour >= 20:
        return []

    trades = load_trades()
    pending = [t for t in trades if t.result == TradeResult.PENDING]

    if not pending:
        return []

    actions: list[dict] = []

    # Fetch prices in parallel for all pending trades
    from concurrent.futures import ThreadPoolExecutor, as_completed
    price_cache: dict[str, float | None] = {}
    tickers = list(set(t.ticker for t in pending))

    executor = ThreadPoolExecutor(max_workers=min(3, len(tickers)))
    futures = {executor.submit(_get_current_price, t): t for t in tickers}
    try:
        for future in as_completed(futures, timeout=45):
            ticker = futures[future]
            try:
                price_cache[ticker] = future.result(timeout=15)
            except TimeoutError:
                logger.warning("Position monitor: price fetch TIMEOUT for %s", ticker)
                price_cache[ticker] = None
            except Exception as exc:
                logger.warning("Position monitor: price fetch failed for %s: %s", ticker, exc)
                price_cache[ticker] = None
    except TimeoutError:
        # Some futures didn't complete within global timeout — fill missing
        for future in futures:
            t = futures[future]
            if t not in price_cache:
                logger.warning("Position monitor: global timeout, skipping %s", t)
                price_cache[t] = None
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    for trade in pending:
        current_price = price_cache.get(trade.ticker)
        if current_price is None:
            logger.debug("Position monitor: no price for %s, skipping", trade.ticker)
            continue

        progress = _compute_move_progress(trade, current_price)

        # 1. Check TP hit
        if _check_tp_hit(trade, current_price):
            pnl = _compute_pnl(trade, trade.target_price)
            update_trade_result(trade.timestamp, trade.ticker, TradeResult.TP_HIT, trade.target_price)
            action = {
                "ticker": trade.ticker, "action": "TP_HIT",
                "price": current_price, "pnl_pct": pnl,
                "reason": "Take profit atteint",
            }
            actions.append(action)
            logger.info("POSITION MONITOR: %s TP HIT at %.4f (PnL: %+.2f%%)",
                        trade.ticker, current_price, pnl)
            continue

        # 2. Trailing stop adjustment — BEFORE SL check so tightened stop is used
        # v6.4 J1: Persist trailing stop to PG/JSON so Journal and Learning use correct stop
        new_stop = _compute_trailing_stop(trade, current_price, progress)
        if new_stop is not None:
            old_stop = trade.stop_price
            trade.stop_price = new_stop
            # Persist the new stop so it survives across monitor cycles and Journal at 22h
            update_trade_stop(trade.timestamp, trade.ticker, new_stop)
            action = {
                "ticker": trade.ticker, "action": "TRAILING_STOP",
                "old_stop": old_stop, "new_stop": new_stop,
                "progress": round(progress * 100, 1),
                "reason": f"Trailing stop: {old_stop:.4f} → {new_stop:.4f} ({progress*100:.0f}% progression)",
            }
            actions.append(action)
            logger.info("POSITION MONITOR: %s trailing stop %.4f → %.4f (progress: %.0f%%)",
                        trade.ticker, old_stop, new_stop, progress * 100)

        # 3. Check SL hit (uses potentially tightened stop from step 2)
        if _check_stop_hit(trade, current_price):
            pnl = _compute_pnl(trade, trade.stop_price)
            update_trade_result(trade.timestamp, trade.ticker, TradeResult.SL_HIT, trade.stop_price)
            action = {
                "ticker": trade.ticker, "action": "SL_HIT",
                "price": current_price, "pnl_pct": pnl,
                "reason": "Stop loss touche",
            }
            actions.append(action)
            logger.info("POSITION MONITOR: %s SL HIT at %.4f (PnL: %+.2f%%)",
                        trade.ticker, current_price, pnl)
            continue

        # 4. Time stop check
        should_close, reason = _check_time_stop(trade, progress, now)
        if should_close:
            pnl = _compute_pnl(trade, current_price)
            result = TradeResult.EXPIRED
            update_trade_result(trade.timestamp, trade.ticker, result, current_price)
            action = {
                "ticker": trade.ticker, "action": "TIME_STOP",
                "price": current_price, "pnl_pct": pnl,
                "reason": reason,
            }
            actions.append(action)
            logger.info("POSITION MONITOR: %s TIME STOP at %.4f (PnL: %+.2f%%) — %s",
                        trade.ticker, current_price, pnl, reason)

    if actions:
        logger.info("Position monitor: %d action(s) taken", len(actions))

    return actions


def _compute_pnl(trade: TradeRecommendation, exit_price: float) -> float:
    """Compute PnL percentage.

    T1-P2: Guard against division by zero (consistent with journal.py A2).
    """
    if not trade.entry_price or trade.entry_price == 0:
        return 0.0
    if trade.direction == Direction.LONG:
        return round((exit_price - trade.entry_price) / trade.entry_price * 100, 4)
    return round((trade.entry_price - exit_price) / trade.entry_price * 100, 4)
