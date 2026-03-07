"""Daily journal: auto-closes trades at 22:00 CET, generates journal entries.

v4.1 journal audit — 27 improvements:
- A1: File locking on _save_journal()
- A2: Guard division by zero in _compute_pnl()
- A3: Explicit chronological sort of bars
- A4: exit_time = actual TP/SL hit time (not journal run time)
- B1: 15min bars for finer TP/SL resolution
- B2: +midpoint correction on actual_pricing_hours
- B3: Smart fallback for actual_pricing_hours (remaining window)
- B4: Use last post-entry bar close for EXPIRED
- B5: Slippage tracking (entry_price vs next bar open)
- E1: Structured journal run metrics
- E2: Alert on price fetch failure rate
- E4: Bar coverage tracking
- F1: Max Adverse Excursion (MAE) tracking
- F4: Price anomaly detection
- G1: DST-safe bar date filtering (Paris timezone)
- G3: Global timeout for journal run
"""

import fcntl
import json
import logging
import math
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .database import is_pg_enabled
from .market_data import fetch_history_range
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

# G3: Global timeout for journal run (10 minutes max)
# Fix 2: Raised from 300s to 600s — 300s was too short when many trades
# need price fetches, causing remaining trades to stay PENDING permanently.
JOURNAL_GLOBAL_TIMEOUT_SECONDS = 600

# F4: Price anomaly threshold — flag if exit/entry ratio exceeds this
PRICE_ANOMALY_THRESHOLD = 0.20  # 20% change = likely split or data error


def _ensure_journal_file() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not JOURNAL_FILE.exists():
        JOURNAL_FILE.write_text("[]")


def load_journal() -> list[JournalEntry]:
    """Load all journal entries.

    Resilient: skips individual entries that fail to parse instead of
    returning an empty list.  If PostgreSQL returns zero entries but the
    JSON file has data, falls back to JSON (handles un-migrated data).
    """
    entries: list[JournalEntry] = []

    if is_pg_enabled():
        try:
            from .database import pg_load_journal
            raw = pg_load_journal()
            for i, e in enumerate(raw):
                try:
                    entries.append(JournalEntry(**e))
                except Exception as exc:
                    logger.warning(
                        "Skipping invalid journal entry #%d from PG (ticker=%s): %s",
                        i, e.get("ticker", "?"), exc,
                    )
            if entries:
                return entries
            # PG returned 0 valid entries — fall through to JSON as fallback
            if raw:
                logger.warning(
                    "PG has %d raw rows but 0 valid entries — check data integrity",
                    len(raw),
                )
            else:
                logger.info("PG journal_entries table is empty, trying JSON fallback")
        except Exception as exc:
            logger.error("Failed to load journal from PostgreSQL: %s", exc)

    # JSON fallback (or primary path when PG is disabled)
    _ensure_journal_file()
    try:
        # C3: Shared lock for consistent reads (matches _save_journal's LOCK_EX)
        with open(JOURNAL_FILE, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                raw = json.load(f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        for i, e in enumerate(raw):
            try:
                entries.append(JournalEntry(**e))
            except Exception as exc:
                logger.warning(
                    "Skipping invalid journal entry #%d from JSON (ticker=%s): %s",
                    i, e.get("ticker", "?") if isinstance(e, dict) else "?", exc,
                )
        # M6: Auto-migrate JSON → PG if PG is enabled but was empty (only once)
        # ON CONFLICT DO NOTHING protects against concurrent migrations
        if entries and is_pg_enabled():
            from .database import was_migration_attempted, mark_migration_attempted
            if not was_migration_attempted("journal"):
                mark_migration_attempted("journal")
                logger.info(
                    "Auto-migrating %d journal entries from JSON to PostgreSQL", len(entries),
                )
                try:
                    from .database import pg_save_journal_entries
                    pg_save_journal_entries(
                        [e.model_dump(mode="json") for e in entries]
                    )
                    logger.info("Auto-migration of journal entries complete (duplicates ignored via ON CONFLICT)")
                except Exception as exc:
                    logger.error("Auto-migration of journal entries failed: %s", exc)
    except (json.JSONDecodeError, Exception) as exc:
        logger.error("Failed to load journal from JSON: %s", exc)

    return entries


def _save_journal(entries: list[JournalEntry]) -> None:
    """Save journal entries with file locking (A1 fix)."""
    _ensure_journal_file()
    data = json.dumps([e.model_dump(mode="json") for e in entries], indent=2, default=str)
    with open(JOURNAL_FILE, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(data)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _fetch_intraday_prices(
    ticker: str, trade_date: str,
) -> tuple[float | None, float | None, float | None, list | None]:
    """Fetch intraday bars for a specific trading day.

    B1: Tries 15min bars first for finer TP/SL resolution, falls back to 1h then daily.
    G1: Uses Paris timezone for date filtering (DST-safe).

    Returns (full_day_high, full_day_low, session_close, bars).
    bars = list of (timestamp, bar_high, bar_low, bar_open, bar_close) for chronological TP/SL.
    Returns (None, None, None, None) if data unavailable.
    """
    from datetime import date as date_type

    target = date_type.fromisoformat(trade_date)
    # end is exclusive — +2 days to handle timezone offsets safely
    end = target + timedelta(days=2)

    try:
        # B1: Try 15min bars first for finer resolution
        data = None
        bar_interval = "15min"
        try:
            data = fetch_history_range(ticker, start=target, end=end, interval="15min")
        except Exception:
            pass

        if data is None or data.empty:
            # Fallback to 1h bars
            bar_interval = "1h"
            data = fetch_history_range(ticker, start=target, end=end, interval="1h")

        if data is None or data.empty:
            # Fallback: daily bar
            bar_interval = "1day"
            daily = fetch_history_range(
                ticker, start=target, end=target + timedelta(days=1), interval="1day",
            )
            if daily is None or daily.empty:
                return None, None, None, None
            row = daily.iloc[-1]
            return float(row["High"]), float(row["Low"]), float(row["Close"]), None

        # G1: DST-safe date filtering — convert index to Paris timezone then filter
        filtered_rows = []
        for idx in data.index:
            try:
                if hasattr(idx, 'tz_localize') and idx.tzinfo is None:
                    idx_paris = idx.tz_localize("UTC").astimezone(PARIS_TZ)
                elif hasattr(idx, 'astimezone'):
                    idx_paris = idx.astimezone(PARIS_TZ)
                else:
                    idx_paris = idx
                if idx_paris.strftime("%Y-%m-%d") == trade_date:
                    filtered_rows.append(idx)
            except Exception:
                # Fallback: string comparison on original index
                if hasattr(idx, 'strftime') and idx.strftime("%Y-%m-%d") == trade_date:
                    filtered_rows.append(idx)

        if not filtered_rows:
            return None, None, None, None

        data = data.loc[filtered_rows]
        full_high = float(data["High"].max())
        full_low = float(data["Low"].min())
        session_close = float(data["Close"].iloc[-1])

        # A3: Build bars with explicit sort + include Open/Close for slippage/MAE
        bars = []
        for idx, row in data.iterrows():
            bars.append((
                idx,
                float(row["High"]),
                float(row["Low"]),
                float(row.get("Open", row["High"])),   # Open for slippage
                float(row.get("Close", row["Low"])),    # Close for EXPIRED
            ))

        # A3: Explicit chronological sort
        bars.sort(key=lambda b: b[0])

        return full_high, full_low, session_close, bars
    except Exception as exc:
        logger.warning("Intraday price fetch failed for %s (%s): %s", ticker, trade_date, exc)
        return None, None, None, None


def _filter_post_entry(
    bars: list | None, entry_time: datetime,
) -> list | None:
    """Filter intraday bars to keep only those at or after the trade entry time.

    This ensures we don't count price extremes that happened BEFORE the trade
    was recommended — a high at 06:00 is irrelevant for a trade entered at 07:50.
    """
    if not bars:
        return None
    # Normalize entry_time to UTC for consistent comparison
    if entry_time.tzinfo is None:
        entry_utc = entry_time.replace(tzinfo=timezone.utc)
    else:
        entry_utc = entry_time.astimezone(timezone.utc)

    filtered = []
    for bar in bars:
        ts = bar[0]
        # Normalize bar timestamp to UTC
        if hasattr(ts, 'tzinfo') and ts.tzinfo is not None:
            ts_utc = ts.astimezone(timezone.utc)
        else:
            ts_utc = ts  # Assume UTC if naive
        if ts_utc >= entry_utc:
            filtered.append(bar)
    return filtered if filtered else None


def _compute_pnl(
    trade: TradeRecommendation, exit_price: float,
) -> float:
    """Compute PnL percentage for a trade. A2: Guard against division by zero."""
    if trade.entry_price == 0:
        logger.warning("entry_price is 0 for %s — cannot compute PnL", trade.ticker)
        return 0.0
    if trade.direction == Direction.LONG:
        return round((exit_price - trade.entry_price) / trade.entry_price * 100, 4)
    return round((trade.entry_price - exit_price) / trade.entry_price * 100, 4)


def _find_hit_time(
    trade: TradeRecommendation,
    result: TradeResult,
    bars: list,
) -> datetime | None:
    """Find the timestamp of the bar where TP or SL was hit.

    Walks bars chronologically and returns the timestamp of the first bar
    that triggered the given result. Returns None if not found.
    """
    for bar in bars:
        ts, bar_high, bar_low = bar[0], bar[1], bar[2]
        if result == TradeResult.TP_HIT:
            if trade.direction == Direction.LONG and bar_high >= trade.target_price:
                return ts
            if trade.direction == Direction.SHORT and bar_low <= trade.target_price:
                return ts
        elif result == TradeResult.SL_HIT:
            if trade.direction == Direction.LONG and bar_low <= trade.stop_price:
                return ts
            if trade.direction == Direction.SHORT and bar_high >= trade.stop_price:
                return ts
    return None


def _compute_mae_mfe(
    trade: TradeRecommendation, post_entry_bars: list | None,
    exit_bar_ts=None,
) -> tuple[float | None, float | None]:
    """F1: Compute Max Adverse Excursion and Max Favorable Excursion.

    MAE = worst unrealized loss during the trade (before outcome)
    MFE = best unrealized gain during the trade (before outcome)
    Both expressed as % of entry price.

    M6: If exit_bar_ts is provided (timestamp of the bar where TP/SL was hit),
    stop iterating after that bar — bars after the exit are irrelevant.
    """
    if not post_entry_bars or trade.entry_price == 0:
        return None, None

    mae = 0.0  # Worst drawdown
    mfe = 0.0  # Best unrealized gain

    for bar in post_entry_bars:
        bar_ts, bar_high, bar_low = bar[0], bar[1], bar[2]
        if trade.direction == Direction.LONG:
            # Worst case: bar_low below entry
            adverse = (bar_low - trade.entry_price) / trade.entry_price * 100
            favorable = (bar_high - trade.entry_price) / trade.entry_price * 100
        else:
            # SHORT: adverse = price going up, favorable = price going down
            adverse = (trade.entry_price - bar_high) / trade.entry_price * 100
            favorable = (trade.entry_price - bar_low) / trade.entry_price * 100

        mae = min(mae, adverse)
        mfe = max(mfe, favorable)

        # M6: Stop after the exit bar — post-exit price action is irrelevant
        if exit_bar_ts is not None and bar_ts >= exit_bar_ts:
            break

    return round(mae, 4), round(mfe, 4)


def _compute_slippage(
    trade: TradeRecommendation, post_entry_bars: list | None,
) -> float | None:
    """B5: Estimate slippage as difference between entry_price and first post-entry bar open.

    Positive slippage = price moved against us before we could enter.
    """
    if not post_entry_bars or trade.entry_price == 0:
        return None

    first_bar = post_entry_bars[0]
    if len(first_bar) < 4:
        return None  # No Open data available

    first_open = first_bar[3]
    if trade.direction == Direction.LONG:
        # LONG (buying): paid more than recommended = unfavorable
        slip = (first_open - trade.entry_price) / trade.entry_price * 100
    else:
        # SHORT (selling): price moved up = sold at worse level = unfavorable
        slip = (first_open - trade.entry_price) / trade.entry_price * 100
    # Positive = unfavorable (price moved against), Negative = favorable (price moved in our favor)
    return round(slip, 4)


def _check_price_anomaly(
    trade: TradeRecommendation, exit_price: float | None,
) -> bool:
    """F4: Detect potential price anomalies (splits, data errors).

    Returns True if anomaly detected.
    """
    if exit_price is None or trade.entry_price == 0:
        return False
    ratio = abs(exit_price - trade.entry_price) / trade.entry_price
    if ratio > PRICE_ANOMALY_THRESHOLD:
        logger.warning(
            "PRICE ANOMALY: %s moved %.1f%% (entry=%.4f, exit=%.4f) — possible split or data error",
            trade.ticker, ratio * 100, trade.entry_price, exit_price,
        )
        return True
    return False


def _determine_result(
    trade: TradeRecommendation,
    post_high: float | None,
    post_low: float | None,
    close: float | None,
    post_entry_bars: list | None = None,
) -> tuple[TradeResult, float | None, float | None]:
    """Determine trade outcome from POST-ENTRY price action.

    post_high/post_low are computed from bars AFTER the trade was recommended.
    When both TP and SL are reachable within the post-entry range, uses
    intraday bars to check chronologically which was hit FIRST.
    Falls back to SL (conservative) when intraday data is unavailable.

    B4: For EXPIRED, uses last post-entry bar close instead of full session_close.

    Returns (result, exit_price, pnl_pct).
    """
    if post_high is None or post_low is None or close is None:
        return TradeResult.EXPIRED, close, None

    # Guard against NaN/Inf from yfinance data corruption
    if any(math.isnan(v) or math.isinf(v) for v in (post_high, post_low, close)):
        logger.warning("NaN/Inf price detected for %s — treating as EXPIRED", trade.ticker)
        return TradeResult.EXPIRED, None, None

    # Check if TP/SL are reachable from POST-ENTRY high/low
    if trade.direction == Direction.LONG:
        tp_reachable = post_high >= trade.target_price
        sl_reachable = post_low <= trade.stop_price
    else:
        tp_reachable = post_low <= trade.target_price
        sl_reachable = post_high >= trade.stop_price

    # ── Case 1: Only TP reachable ──
    if tp_reachable and not sl_reachable:
        pnl = _compute_pnl(trade, trade.target_price)
        return TradeResult.TP_HIT, trade.target_price, pnl

    # ── Case 2: Only SL reachable ──
    if sl_reachable and not tp_reachable:
        pnl = _compute_pnl(trade, trade.stop_price)
        return TradeResult.SL_HIT, trade.stop_price, pnl

    # ── Case 3: BOTH reachable — need chronological resolution ──
    if tp_reachable and sl_reachable:
        if post_entry_bars:
            # Walk bars chronologically to find which was hit first
            for bar in post_entry_bars:
                bar_high, bar_low = bar[1], bar[2]
                if trade.direction == Direction.LONG:
                    tp_hit = bar_high >= trade.target_price
                    sl_hit = bar_low <= trade.stop_price
                else:
                    tp_hit = bar_low <= trade.target_price
                    sl_hit = bar_high >= trade.stop_price

                if tp_hit and not sl_hit:
                    pnl = _compute_pnl(trade, trade.target_price)
                    return TradeResult.TP_HIT, trade.target_price, pnl
                if sl_hit and not tp_hit:
                    pnl = _compute_pnl(trade, trade.stop_price)
                    return TradeResult.SL_HIT, trade.stop_price, pnl
                if tp_hit and sl_hit:
                    # Both hit in same bar — can't determine order,
                    # be conservative: assume SL was hit first
                    logger.info(
                        "TP+SL both hit in same bar for %s — conservative SL",
                        trade.ticker,
                    )
                    pnl = _compute_pnl(trade, trade.stop_price)
                    return TradeResult.SL_HIT, trade.stop_price, pnl

        # No intraday resolution — conservative: SL first
        logger.info(
            "TP+SL both reachable for %s but no intraday data — conservative SL",
            trade.ticker,
        )
        pnl = _compute_pnl(trade, trade.stop_price)
        return TradeResult.SL_HIT, trade.stop_price, pnl

    # ── Case 4: Neither hit — EXPIRED at session close ──
    # B4: Use last post-entry bar close for more accurate EXPIRED PnL
    expired_close = close
    if post_entry_bars and len(post_entry_bars[-1]) >= 5:
        expired_close = post_entry_bars[-1][4]  # Close of last post-entry bar
    pnl = _compute_pnl(trade, expired_close)
    return TradeResult.EXPIRED, expired_close, pnl


def _build_review(trade: TradeRecommendation, result: TradeResult, pnl_pct: float | None,
                   slippage: float | None = None, mae: float | None = None,
                   mfe: float | None = None, actual_pricing_hours: float | None = None) -> str:
    """Generate a learning-oriented post-trade review.

    Fix 6: Now includes actionable learning insights beyond just the outcome.
    Analyzes why the trade worked/failed based on execution metrics.
    """
    parts = []

    # --- Outcome ---
    if result == TradeResult.TP_HIT:
        parts.append(f"TP atteint ({pnl_pct:+.2f}%).")
    elif result == TradeResult.SL_HIT:
        parts.append(f"SL touche ({pnl_pct:+.2f}%).")
    elif pnl_pct is not None and pnl_pct > 0:
        parts.append(f"Expire en gain ({pnl_pct:+.2f}%).")
    elif pnl_pct is not None and pnl_pct < 0:
        parts.append(f"Expire en perte ({pnl_pct:+.2f}%).")
    else:
        parts.append("Expire flat.")

    # --- Learning insights ---
    insights = []

    # Transmission delay analysis
    if actual_pricing_hours is not None and trade.predicted_transmission_delay is not None:
        predicted_hours = trade.predicted_transmission_delay / 100.0 * 6.0  # baseline 6h
        if actual_pricing_hours < predicted_hours * 0.5:
            insights.append("Le marche a price plus vite que prevu — signal deja partiellement connu.")
        elif actual_pricing_hours > predicted_hours * 1.5 and result == TradeResult.TP_HIT:
            insights.append("Pricing plus lent que prevu — edge reel detecte avant le consensus.")

    # Slippage analysis
    if slippage is not None:
        if abs(slippage) > 0.5:
            insights.append(f"Slippage eleve ({slippage:+.2f}%) — execution degradee, verifier liquidite.")
        elif abs(slippage) < 0.05:
            insights.append("Execution propre, slippage negligeable.")

    # MAE/MFE analysis (stop tightness)
    if mae is not None and mfe is not None and result == TradeResult.SL_HIT:
        if mfe > abs(pnl_pct or 0) * 0.5:
            insights.append(f"Le trade etait en gain (MFE {mfe:+.2f}%) avant le SL — stop trop serre ou timing de sortie a revoir.")
        else:
            insights.append("Direction incorrecte des le depart — signal a recalibrer.")
    elif mae is not None and result == TradeResult.TP_HIT:
        if mae > abs(pnl_pct or 0) * 0.8:
            insights.append(f"Drawdown important (MAE {mae:.2f}%) avant TP — trade volatile, risque de SL.")

    # Category-specific insight
    news_cat = getattr(trade, "news_category", "other")
    if result == TradeResult.SL_HIT and news_cat in ("earnings", "macro"):
        insights.append("Categorie zero-edge (earnings/macro) — le learning penalisera ce type de signal.")
    elif result == TradeResult.TP_HIT and news_cat in ("weather", "supply_chain", "commodity"):
        insights.append(f"Categorie a fort edge ({news_cat}) confirmee — le learning boostera ce type.")

    # Volume confirmation
    if trade.volume_confirmed is True and result == TradeResult.TP_HIT:
        insights.append("Volume confirme = signal fiable.")
    elif trade.volume_confirmed is False and result == TradeResult.SL_HIT:
        insights.append("Volume faible — prudence sur les signaux sans confirmation volume.")

    # Learning multiplier feedback
    mult = getattr(trade, "learning_multiplier", None)
    if mult is not None:
        if mult < 0.8 and result == TradeResult.TP_HIT:
            insights.append(f"Learning penalisait ce trade ({mult:.2f}x) mais il a gagne — possible sous-estimation.")
        elif mult > 1.2 and result == TradeResult.SL_HIT:
            insights.append(f"Learning boostait ce trade ({mult:.2f}x) mais il a perdu — possible sur-estimation.")

    # EXPIRED specific
    if result == TradeResult.EXPIRED:
        if pnl_pct is not None and abs(pnl_pct) < 0.1:
            insights.append("Aucun mouvement — le signal n'a pas eu d'impact mesurable.")
        elif pnl_pct is not None and pnl_pct > 0:
            insights.append("Mouvement dans le bon sens mais TP trop ambitieux — reduire le target.")
        elif pnl_pct is not None and pnl_pct < -0.5:
            insights.append("Mouvement contraire sans toucher le SL — direction a recalibrer.")

    if insights:
        parts.append(" " + " ".join(insights))
    else:
        # Fallback: at least say something about what happened
        if result == TradeResult.TP_HIT:
            parts.append(" These correcte, signal bien calibre.")
        elif result == TradeResult.SL_HIT:
            parts.append(" Le marche n'a pas suivi — direction ou timing incorrect.")

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
    if is_pg_enabled():
        try:
            from .database import pg_load_last_scans
            return pg_load_last_scans()
        except Exception as exc:
            logger.warning("Failed to load scans cache from PostgreSQL: %s", exc)
            return {}
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


def _compute_bar_interval(post_bars: list | None) -> str | None:
    """Detect bar interval from timestamps of consecutive bars."""
    if not post_bars or len(post_bars) < 2:
        return None
    ts0 = post_bars[0][0]
    ts1 = post_bars[1][0]
    try:
        if hasattr(ts0, 'timestamp') and hasattr(ts1, 'timestamp'):
            delta_min = abs((ts1.timestamp() - ts0.timestamp())) / 60
        else:
            delta_min = 60  # Assume 1h if can't compute
        if delta_min <= 20:
            return "15min"
        elif delta_min <= 65:
            return "1h"
        else:
            return "1day"
    except Exception:
        return None


def prune_old_journal_entries(max_age_days: int = 365) -> int:
    """D3: Remove journal entries older than max_age_days.

    Returns the number of entries pruned.
    Only operates on JSON storage — PG pruning should use SQL DELETE.
    """
    if is_pg_enabled():
        try:
            from .database import pg_prune_journal
            return pg_prune_journal(max_age_days)
        except (ImportError, AttributeError):
            logger.debug("pg_prune_journal not available, skipping PG pruning")
            return 0

    entries = load_journal()
    if not entries:
        return 0

    cutoff = datetime.now(PARIS_TZ) - timedelta(days=max_age_days)
    cutoff_str = cutoff.strftime("%Y-%m-%d")

    kept = [e for e in entries if e.date >= cutoff_str]
    pruned = len(entries) - len(kept)

    if pruned > 0:
        _save_journal(kept)
        logger.info("D3: Pruned %d old journal entries (>%d days)", pruned, max_age_days)

    return pruned


def _archive_daily_prices(trades: list[TradeRecommendation]) -> None:
    """v5.1: Archive daily OHLCV data for traded tickers into price_archive table.

    Called after journal to build historical price database for backtesting.
    Archives the last 5 trading days for each ticker to fill any gaps.
    Only runs when PG is enabled.
    """
    if not is_pg_enabled():
        return
    if not trades:
        return

    try:
        from .database import pg_save_price_archive
        from .market_data import fetch_history

        # Get unique tickers from trades
        tickers = list({t.ticker for t in trades})

        rows = []
        for ticker in tickers:
            try:
                df = fetch_history(ticker, period_days=7, interval="1day")
                if df is None or df.empty:
                    continue
                for dt, row in df.iterrows():
                    rows.append({
                        "ticker": ticker,
                        "date": dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt)[:10],
                        "open": float(row["Open"]),
                        "high": float(row["High"]),
                        "low": float(row["Low"]),
                        "close": float(row["Close"]),
                        "volume": float(row.get("Volume", 0)),
                        "source": "twelve_data",
                    })
            except Exception as exc:
                logger.debug("Price archive: failed to fetch %s: %s", ticker, exc)

        if rows:
            inserted = pg_save_price_archive(rows)
            logger.info("v5.1: Archived %d price rows for %d tickers", inserted, len(tickers))
    except Exception as exc:
        logger.warning("v5.1: Price archiving failed (non-critical): %s", exc)


def run_daily_journal() -> list[dict]:
    """Main job: close all pending trades, generate journal entries for today.

    Called at 22:00 CET by the scheduler.
    Returns the list of new journal entries as dicts.
    """
    journal_start = time.monotonic()
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

    # (I7) Pre-fetch intraday prices in parallel to speed up journal closure
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Collect unique (ticker, trade_date) pairs for parallel fetching
    price_tasks: dict[tuple[str, str], TradeRecommendation] = {}
    for trade in pending:
        trade_date = trade.timestamp.astimezone(PARIS_TZ).strftime("%Y-%m-%d")
        trade_key = (trade.ticker, trade.timestamp.isoformat())
        if trade_key in existing_keys:
            continue
        cache_key = (trade.ticker, trade_date)
        if cache_key not in price_tasks:
            price_tasks[cache_key] = trade

    # E1: Track metrics
    price_fetch_successes = 0
    price_fetch_failures = 0

    # Fetch intraday bars (15min preferred, 1h fallback) for accurate post-entry price tracking
    price_cache: dict[tuple[str, str], tuple] = {}
    if price_tasks:
        # max_workers capped at 3: Replit kills process on too many concurrent threads.
        # G3: Per-future timeout of 30s
        # O7: Explicit shutdown(wait=False, cancel_futures=True) to avoid blocking
        executor = ThreadPoolExecutor(max_workers=min(3, len(price_tasks)))
        try:
            futures = {
                executor.submit(_fetch_intraday_prices, ticker, trade_date): (ticker, trade_date)
                for ticker, trade_date in price_tasks
            }
            for future in as_completed(futures):
                key = futures[future]
                try:
                    result = future.result(timeout=30)
                    price_cache[key] = result
                    if result[0] is not None:
                        price_fetch_successes += 1
                    else:
                        price_fetch_failures += 1
                except Exception as exc:
                    logger.warning("Parallel price fetch failed for %s: %s", key[0], exc)
                    price_cache[key] = (None, None, None, None)
                    price_fetch_failures += 1
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    # E2: Alert if price fetch failure rate is high
    total_fetches = price_fetch_successes + price_fetch_failures
    if total_fetches > 0 and price_fetch_failures / total_fetches > 0.5:
        logger.error(
            "ALERT: Price fetch failure rate %.0f%% (%d/%d) — possible API outage",
            price_fetch_failures / total_fetches * 100, price_fetch_failures, total_fetches,
        )

    for trade in pending:
        # G3: Check global timeout
        elapsed = time.monotonic() - journal_start
        if elapsed > JOURNAL_GLOBAL_TIMEOUT_SECONDS:
            logger.error(
                "Journal global timeout reached (%.0fs > %ds) — %d trades remaining",
                elapsed, JOURNAL_GLOBAL_TIMEOUT_SECONDS, len(pending) - len(new_entries),
            )
            break

        trade_date = trade.timestamp.astimezone(PARIS_TZ).strftime("%Y-%m-%d")

        # Skip if already in journal (dedup)
        trade_key = (trade.ticker, trade.timestamp.isoformat())
        if trade_key in existing_keys:
            logger.info("Skipping duplicate journal entry for %s %s", trade.ticker, trade_date)
            continue

        if trade_date != today:
            logger.info("Force-closing old pending trade: %s from %s", trade.ticker, trade_date)

        # Get intraday data and filter to ONLY post-entry bars
        cache_key = (trade.ticker, trade_date)
        _full_high, _full_low, session_close, all_bars = price_cache.get(
            cache_key, (None, None, None, None),
        )
        post_bars = _filter_post_entry(all_bars, trade.timestamp)

        # Compute high/low from POST-ENTRY bars only (ignore price action before trade)
        if post_bars:
            day_high = max(h for _, h, *_ in post_bars)
            day_low = min(l for _, _, l, *_ in post_bars)
        else:
            # No post-entry bars — fall back to full day data
            day_high, day_low = _full_high, _full_low

        close = session_close

        # E4: Track bar coverage
        n_bars = len(post_bars) if post_bars else 0
        bar_interval = _compute_bar_interval(post_bars)

        logger.info(
            "Price check %s (%s): entry=%.4f, post_high=%s, post_low=%s, close=%s, bars=%d (%s)",
            trade.ticker, trade_date, trade.entry_price,
            f"{day_high:.4f}" if day_high else "N/A",
            f"{day_low:.4f}" if day_low else "N/A",
            f"{close:.4f}" if close else "N/A",
            n_bars, bar_interval or "none",
        )

        result, exit_price, pnl_pct = _determine_result(
            trade, day_high, day_low, close, post_bars,
        )

        # M6: Find hit time BEFORE MAE/MFE so we can stop at the exit bar
        exit_bar_ts = None
        if result in (TradeResult.TP_HIT, TradeResult.SL_HIT) and post_bars:
            exit_bar_ts_raw = _find_hit_time(trade, result, post_bars)
            if exit_bar_ts_raw is not None:
                exit_bar_ts = exit_bar_ts_raw

        # F4: Price anomaly detection
        _check_price_anomaly(trade, exit_price)

        # B5: Slippage tracking
        slippage = _compute_slippage(trade, post_bars)

        # F1: Max Adverse Excursion and Max Favorable Excursion
        # M6: Pass exit_bar_ts to stop iteration at the hit bar
        mae, mfe = _compute_mae_mfe(trade, post_bars, exit_bar_ts=exit_bar_ts)

        # Compute realized R/R (F3 — actual risk vs reward)
        realized_rr = None
        if pnl_pct is not None and trade.stop_pct > 0:
            realized_rr = round(abs(pnl_pct) / trade.stop_pct, 2) if pnl_pct > 0 else round(-abs(pnl_pct) / trade.stop_pct, 2)

        # P1-#6: Compute actual pricing time and delay accuracy
        actual_pricing_hours = None
        delay_accuracy = None
        # M6: Reuse exit_bar_ts from earlier (already computed for MAE/MFE)
        hit_time = exit_bar_ts
        if result in (TradeResult.TP_HIT, TradeResult.SL_HIT) and hit_time is not None:
            raw_hours = (hit_time - trade.timestamp).total_seconds() / 3600
            # B2: Add midpoint correction — bar timestamp is start of bar
            if bar_interval == "15min":
                raw_hours += 0.125  # +7.5min midpoint
            elif bar_interval == "1h":
                raw_hours += 0.5    # +30min midpoint
            actual_pricing_hours = round(max(0, raw_hours), 2)

        if result in (TradeResult.TP_HIT, TradeResult.SL_HIT) and actual_pricing_hours is None:
            # B3: Smart fallback — estimate remaining trading window from entry time
            entry_paris = trade.timestamp.astimezone(PARIS_TZ)
            market_close_hour = 20  # 20:00 CET
            remaining_hours = max(1.0, market_close_hour - entry_paris.hour - entry_paris.minute / 60)
            actual_pricing_hours = round(remaining_hours, 2)

        if trade.predicted_transmission_delay is not None and actual_pricing_hours is not None:
            from .learning import TRANSMISSION_DELAY_BASELINE_HOURS
            actual_delay_score = min(100, actual_pricing_hours / TRANSMISSION_DELAY_BASELINE_HOURS * 100)
            delay_accuracy = round(trade.predicted_transmission_delay - actual_delay_score, 1)

        # Update the trade in the learning system (with P1-#6 delay accuracy)
        # Always update result even when exit_price is None (price fetch failed).
        # This prevents trades from staying PENDING forever — they get marked EXPIRED
        # so subsequent journal runs don't try to reprocess them.
        update_trade_result(
            trade.timestamp, trade.ticker, result,
            exit_price if exit_price is not None else trade.entry_price,
            actual_pricing_time_hours=actual_pricing_hours,
            delay_accuracy=delay_accuracy,
        )

        # A4: exit_time = actual hit time for TP/SL, journal run time for EXPIRED
        if hit_time is not None and result in (TradeResult.TP_HIT, TradeResult.SL_HIT):
            exit_time_value = hit_time if hasattr(hit_time, 'tzinfo') and hit_time.tzinfo else hit_time
        else:
            exit_time_value = datetime.now(timezone.utc)

        review = _build_review(
            trade, result, pnl_pct,
            slippage=slippage, mae=mae, mfe=mfe,
            actual_pricing_hours=actual_pricing_hours,
        )

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
            exit_time=exit_time_value,
            exit_price=exit_price,
            day_high=day_high,
            day_low=day_low,
            result=result,
            pnl_pct=pnl_pct,
            review=review,
            binary_event_warning=trade.binary_event_warning,
            # v5.1: Full news & trade context
            news_url=getattr(trade, "news_url", "") or "",
            news_description=getattr(trade, "news_description", "") or "",
            target_price=trade.target_price,
            stop_price=trade.stop_price,
            target_pct=trade.target_pct,
            stop_pct=trade.stop_pct,
            risk_reward=trade.risk_reward,
            edge_score=trade.edge_score,
            surprise=trade.surprise,
            directional_clarity=trade.directional_clarity,
            market_awareness=trade.market_awareness,
            signal_reliability=trade.signal_reliability,
            expected_magnitude=trade.expected_magnitude,
            volume_confirmed=trade.volume_confirmed,
            volume_ratio=trade.volume_ratio,
            position_size_pct=trade.position_size_pct,
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
            # v4.1: Journal audit improvements
            slippage_pct=slippage,
            max_adverse_excursion=mae,
            max_favorable_excursion=mfe,
            bar_coverage=n_bars,
            bar_interval=bar_interval,
            realized_rr=realized_rr,
            # v3: Scan-level decision trace (from cached scan results)
            **_extract_scan_trace(scan_data, trade.scan_type.value, trade.ticker),
        )
        new_entries.append(entry)
        logger.info(
            "Journal: %s %s %s → %s (PnL: %s%%, MAE: %s%%, slippage: %s%%, bars: %d/%s)",
            trade.direction.value, trade.ticker, trade.asset_name,
            result.value, pnl_pct, mae, slippage, n_bars, bar_interval or "none",
        )

    # Append to journal
    if new_entries:
        if is_pg_enabled():
            from .database import pg_save_journal_entries
            pg_save_journal_entries(
                [e.model_dump(mode="json") for e in new_entries]
            )
        else:
            existing.extend(new_entries)
            _save_journal(existing)

    # E3: Cross-check PnL between journal entry and trade update
    for entry in new_entries:
        if entry.pnl_pct is not None:
            # Recompute from scratch to validate
            trade_match = next(
                (t for t in pending
                 if t.ticker == entry.ticker
                 and t.timestamp.isoformat() == entry.entry_time.isoformat()),
                None,
            )
            if trade_match and entry.exit_price is not None:
                check_pnl = _compute_pnl(trade_match, entry.exit_price)
                if abs(check_pnl - entry.pnl_pct) > 0.01:
                    logger.error(
                        "E3 PnL MISMATCH: %s journal=%.4f%% vs recomputed=%.4f%%",
                        entry.ticker, entry.pnl_pct, check_pnl,
                    )

    # D3: Prune old journal entries (>1 year) — runs after each journal
    try:
        prune_old_journal_entries(max_age_days=365)
    except Exception as exc:
        logger.warning("D3: Journal pruning failed: %s", exc)

    # (#26) Invalidate learning cache after journal
    invalidate_learning_cache()

    # M4: Run VACUUM ANALYZE after journal (daily maintenance)
    if is_pg_enabled():
        try:
            from .database import pg_run_maintenance
            pg_run_maintenance()
        except Exception as exc:
            logger.warning("M4: Post-journal maintenance failed: %s", exc)

    # v5.1: Archive daily OHLCV for traded tickers (backtest data)
    _archive_daily_prices(pending)

    # v5.2: Save daily source health report
    try:
        from .source_monitor import get_tracker, create_weekly_review_journal_entry
        tracker = get_tracker()
        daily_report = tracker.save_daily_report()
        if daily_report.get("total_calls_failed", 0) > 0:
            failed_sources = [
                name for name, data in daily_report.get("sources", {}).items()
                if data.get("status") in ("DEAD", "DEGRADED")
            ]
            if failed_sources:
                logger.warning("Source health: %d sources had issues today: %s",
                               len(failed_sources), ", ".join(failed_sources))

        # Weekly review every Monday
        today_dt = datetime.now(PARIS_TZ)
        if today_dt.weekday() == 0:  # Monday
            review_entry = create_weekly_review_journal_entry()
            if review_entry:
                logger.info("Weekly source health review created (severity: %s, dead: %d, degraded: %d)",
                            review_entry.get("severity"), review_entry.get("dead_count", 0),
                            review_entry.get("degraded_count", 0))
    except Exception as exc:
        logger.warning("Source health reporting failed: %s", exc)

    # E1: Structured metrics log
    elapsed = time.monotonic() - journal_start
    logger.info(
        "=== Journal complete: %d entries, %.1fs, price_fetches=%d/%d ok ===",
        len(new_entries), elapsed, price_fetch_successes, total_fetches,
    )

    return [e.model_dump(mode="json") for e in new_entries]
