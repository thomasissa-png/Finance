"""Agent Journal 3 — Journal for Agent Trader 3 (Technical Indicators Trading).

Responsibilities:
- Runs daily at 22h (after Journal 1 & 2)
- Closes expired positions (max holding 3 days)
- Records entry/exit with strategy_id, indicators at entry/exit
- MAE/MFE tracking per trade
- Groups results by strategy for A/B analysis
- Publishes "journal_3_complete" on bus -> Learning 3 consumes

Differences with Journal 1/2:
- Journal 1: closes PENDING intraday trades (TP/SL/EXPIRED)
- Journal 2: enriches trend positions (LONG/SHORT over days/weeks)
- Journal 3: closes expired tech positions, records indicator state at exit,
  groups by strategy for A/B comparison
"""

import json
import logging
import fcntl
import math
import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .base import BaseAgent, AgentStatus

logger = logging.getLogger(__name__)

# Timeout constants
GLOBAL_TIMEOUT_S = 120
PER_TICKER_TIMEOUT_S = 30

# Persistence file (JSON fallback)
JOURNAL_FILE = Path(os.getenv("DATA_DIR", "data")) / "tech_journal.json"


def _ensure_journal_file():
    """Create the journal file if it doesn't exist."""
    JOURNAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not JOURNAL_FILE.exists():
        JOURNAL_FILE.write_text("[]")


def _load_journal_entries() -> list[dict]:
    """Load tech journal entries from PG or JSON."""
    from ..database import is_pg_enabled
    if is_pg_enabled():
        return _pg_load_entries()
    return _load_entries_json()


def _load_entries_json() -> list[dict]:
    _ensure_journal_file()
    try:
        with open(JOURNAL_FILE, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            data = json.load(f)
            fcntl.flock(f, fcntl.LOCK_UN)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def _save_journal_entries(entries: list[dict]):
    """Save tech journal entries to PG or JSON."""
    from ..database import is_pg_enabled
    if is_pg_enabled():
        _pg_save_entries(entries)
        return
    _save_entries_json(entries)


def _save_entries_json(entries: list[dict]):
    """Save entries atomically via temp-file + os.replace().

    v8.4 fix: Previous version truncated file before lock acquisition.
    """
    import tempfile
    _ensure_journal_file()
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(JOURNAL_FILE.parent), suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(entries, f, indent=2, default=str)
        os.replace(tmp_path, str(JOURNAL_FILE))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _pg_load_entries() -> list[dict]:
    """Load from PostgreSQL."""
    try:
        from ..database import get_conn
        import psycopg2.extras
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT data FROM tech_journal_entries ORDER BY created_at DESC LIMIT 500"
                )
                return [row["data"] for row in cur.fetchall()]
    except Exception as exc:
        logger.warning("PG load tech_journal_entries failed: %s — fallback JSON", exc)
        return _load_entries_json()


def _pg_save_entries(entries: list[dict]):
    """Save to PostgreSQL (append new only, dedup by ticker+strategy+entry_time).

    BUG FIX: PG schema has UNIQUE(ticker, strategy, entry_time) but INSERT
    was using ON CONFLICT(ticker, entry_time) — mismatched constraint.
    """
    try:
        from ..database import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                for entry in entries:
                    cur.execute("""
                        INSERT INTO tech_journal_entries
                            (ticker, strategy, entry_time, data, created_at)
                        VALUES (%s, %s, %s, %s, NOW())
                        ON CONFLICT (ticker, strategy, entry_time) DO NOTHING
                    """, (
                        entry.get("ticker"),
                        entry.get("strategy", ""),
                        entry.get("entry_time"),
                        json.dumps(entry, default=str),
                    ))
    except Exception as exc:
        logger.warning("PG save tech_journal_entries failed: %s — fallback JSON", exc)
        _save_entries_json(entries)


def _fetch_current_price(ticker: str) -> float | None:
    """Fetch the latest price for a ticker."""
    try:
        from ..market_data import fetch_price
        price = fetch_price(ticker)
        if price is not None:
            return price
        logger.warning("fetch_price returned None for %s — trying yfinance", ticker)
    except Exception as exc:
        logger.warning("Twelve Data fetch failed for %s: %s — trying yfinance", ticker, exc)
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        h = t.history(period="1d")
        if not h.empty:
            return float(h["Close"].iloc[-1])
        logger.warning("yfinance returned empty for %s", ticker)
    except Exception as exc:
        logger.warning("yfinance fetch also failed for %s: %s", ticker, exc)
    return None


def _compute_mae_mfe(direction: str, entry_price: float,
                     high_watermark: float, low_watermark: float) -> tuple[float | None, float | None]:
    """Compute MAE/MFE from watermarks tracked during position lifetime.

    MAE = Max Adverse Excursion (worst drawdown)
    MFE = Max Favorable Excursion (best unrealized gain)

    Returns (mae_pct, mfe_pct) where mae is negative and mfe is positive.
    """
    if not entry_price or entry_price <= 0:
        return None, None

    if direction == "LONG":
        mae = (low_watermark - entry_price) / entry_price * 100
        mfe = (high_watermark - entry_price) / entry_price * 100
    else:  # SHORT
        mae = (entry_price - high_watermark) / entry_price * 100
        mfe = (entry_price - low_watermark) / entry_price * 100

    return round(mae, 2), round(mfe, 2)


class AgentJournal3(BaseAgent):
    """Agent Journal 3 — Journal for technical trading (Trader 3).

    Records closed positions with full indicator context,
    groups by strategy for A/B analysis, computes MAE/MFE.
    """

    name = "journal_3"
    description = "Journal & A/B analysis — technical trading strategies"
    version = "2.1"  # v2.1: fix JSON fallback double write

    def __init__(self):
        super().__init__()
        self._last_run_entries_processed: int = 0
        self._last_run_expired_closed: int = 0
        self._total_entries: int = 0
        self._total_entries_loaded: bool = False

    def _ensure_total_entries(self):
        """Load total entries count from persistence on first access."""
        if not self._total_entries_loaded:
            try:
                entries = _load_journal_entries()
                self._total_entries = len(entries)
            except Exception:
                pass
            self._total_entries_loaded = True

    def run(self, **kwargs) -> dict:
        """Run the daily journal for Trader 3 positions.

        Steps:
        1. Load active/closed positions from Trader 3
        2. Force-close any positions exceeding max holding period
        3. Process all newly closed positions into journal entries
        4. Compute A/B strategy performance
        5. Save and publish results

        Returns dict with journal results.
        """
        self._set_status(AgentStatus.WORKING, "Running Trader 3 journal")

        start = time.monotonic()
        result = {
            "status": "ok",
            "new_entries": [],
            "expired_closed": 0,
            "strategy_performance": {},
            "total_realized_pnl": 0.0,
        }

        try:
            self._ensure_total_entries()

            # Step 1: Load positions
            from .agent_trader_3 import _load_positions, _save_positions, _fetch_current_price
            state = _load_positions()
            active = state.get("active", [])
            closed = state.get("closed", [])

            # Step 2: Force-close expired positions
            now = datetime.now(timezone.utc)
            still_active = []
            force_closed = []

            # Fetch prices for active positions that need closing
            tickers_to_fetch = set()
            for pos in active:
                entry_time = pos.get("entry_time", "")
                try:
                    entry_dt = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
                    holding_hours = (now - entry_dt).total_seconds() / 3600
                    if holding_hours > 3 * 24:  # MAX_HOLDING_DAYS
                        tickers_to_fetch.add(pos["ticker"])
                except (ValueError, AttributeError, TypeError):
                    pass

            # Fetch missing prices
            prices: dict[str, float | None] = {}
            if tickers_to_fetch:
                try:
                    with ThreadPoolExecutor(max_workers=min(len(tickers_to_fetch), 5)) as executor:
                        futures = {
                            executor.submit(_fetch_current_price, t): t
                            for t in tickers_to_fetch
                        }
                        for future in as_completed(futures, timeout=PER_TICKER_TIMEOUT_S * 2):
                            ticker = futures[future]
                            try:
                                prices[ticker] = future.result(timeout=PER_TICKER_TIMEOUT_S)
                            except Exception:
                                prices[ticker] = None
                except Exception:
                    for t in tickers_to_fetch:
                        if t not in prices:
                            prices[t] = _fetch_current_price(t)

            for pos in active:
                entry_time = pos.get("entry_time", "")
                try:
                    entry_dt = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
                    holding_hours = (now - entry_dt).total_seconds() / 3600
                except (ValueError, AttributeError, TypeError):
                    holding_hours = 0
                    still_active.append(pos)
                    continue

                if holding_hours > 3 * 24:
                    # Force close expired position — validate fetched price
                    raw_price = prices.get(pos["ticker"])
                    if raw_price is not None:
                        from ..market_data import validate_price
                        is_valid, val_reason = validate_price(pos["ticker"], raw_price)
                        if not is_valid:
                            logger.warning("J3: rejected force-close price for %s — %s",
                                           pos["ticker"], val_reason)
                            raw_price = None
                    price = raw_price or pos.get("current_price") or pos.get("entry_price")
                    entry_price = pos.get("entry_price", 0)
                    direction = pos.get("direction", "LONG")

                    pnl_pct = 0.0
                    if price and entry_price and entry_price > 0:
                        if direction == "LONG":
                            pnl_pct = (price - entry_price) / entry_price * 100
                        else:
                            pnl_pct = (entry_price - price) / entry_price * 100

                    pos["result"] = "EXPIRED"
                    pos["pnl_pct"] = round(pnl_pct, 2)
                    pos["close_time"] = now.isoformat()
                    pos["close_price"] = price
                    pos["holding_hours"] = round(holding_hours, 1)
                    force_closed.append(pos)
                else:
                    still_active.append(pos)

            closed.extend(force_closed)

            # Step 3: Create journal entries for all closed positions not yet journaled
            # Dedup key = (ticker, strategy, entry_time) to match PG UNIQUE constraint
            existing_entries = _load_journal_entries()
            existing_keys = {
                (e.get("ticker"), e.get("strategy", ""), e.get("entry_time"))
                for e in existing_entries
            }

            new_entries = []
            for trade in closed:
                entry_time = trade.get("entry_time", "")
                ticker = trade.get("ticker", "")
                strategy = trade.get("strategy", "")
                if (ticker, strategy, entry_time) in existing_keys:
                    continue

                entry = self._process_closed_trade(trade)
                if entry:
                    new_entries.append(entry)
                    existing_keys.add((ticker, strategy, entry_time))

            # Step 4: Save journal entries
            if new_entries:
                from ..database import is_pg_enabled
                if is_pg_enabled():
                    # PG: ON CONFLICT DO NOTHING handles dedup safely
                    _pg_save_entries(new_entries)
                else:
                    # JSON: single atomic write with all entries combined
                    all_entries = existing_entries + new_entries
                    cutoff = (now - timedelta(days=365)).isoformat()
                    all_entries = [
                        e for e in all_entries
                        if e.get("entry_time", "") > cutoff or e.get("close_time", "") > cutoff
                    ]
                    _save_entries_json(all_entries)

            # Save updated positions (removed expired from active)
            state = {"active": still_active, "closed": closed[-500:]}
            _save_positions(state)

            # Step 5: Compute A/B strategy performance
            all_journal = (existing_entries or []) + new_entries
            strategy_perf = self._compute_strategy_ab(all_journal)

            # Compute totals
            total_realized = sum(e.get("pnl_pct", 0) for e in all_journal)

            result["new_entries"] = new_entries
            result["expired_closed"] = len(force_closed)
            result["strategy_performance"] = strategy_perf
            result["total_realized_pnl"] = round(total_realized, 2)

            self._last_run_entries_processed = len(new_entries)
            self._last_run_expired_closed = len(force_closed)
            self._total_entries += len(new_entries)

            # Step 6: Log results
            for entry in new_entries:
                self.log_decision("Trade journaled", {
                    "ticker": entry["ticker"],
                    "strategy": entry["strategy"],
                    "direction": entry["direction"],
                    "result": entry.get("result"),
                    "pnl_pct": entry.get("pnl_pct"),
                    "mae_pct": entry.get("mae_pct"),
                    "mfe_pct": entry.get("mfe_pct"),
                    "holding_hours": entry.get("holding_hours"),
                })

            if force_closed:
                self.log(f"Force-closed {len(force_closed)} expired positions", {
                    "tickers": [p["ticker"] for p in force_closed],
                }, level="WARN")

            self.log("Journal 3 run complete", {
                "new_entries": len(new_entries),
                "expired_closed": len(force_closed),
                "total_realized_pnl": round(total_realized, 2),
                "strategies": list(strategy_perf.keys()),
            })

            # Step 7: Publish for Learning 3
            self.publish("journal_3_complete", {
                "entries_processed": len(new_entries),
                "expired_closed": len(force_closed),
                "total_realized_pnl": round(total_realized, 2),
                "strategy_performance": strategy_perf,
                "timestamp": now.isoformat(),
            })

            self._set_status(
                AgentStatus.IDLE,
                f"{len(new_entries)} entries, {len(force_closed)} expired"
            )

            return result

        except Exception as exc:
            self.log("Journal 3 run failed", {"error": str(exc)}, level="ERROR")
            self._set_status(AgentStatus.ERROR, str(exc))
            raise

    def _process_closed_trade(self, trade: dict) -> dict | None:
        """Process a closed trade into a journal entry with MAE/MFE and indicator context."""
        ticker = trade.get("ticker")
        entry_price = trade.get("entry_price")
        direction = trade.get("direction", "LONG")

        if not ticker or not entry_price:
            return None

        # Compute MAE/MFE from watermarks
        high_wm = trade.get("high_watermark", entry_price)
        low_wm = trade.get("low_watermark", entry_price)
        mae_pct, mfe_pct = _compute_mae_mfe(direction, entry_price, high_wm, low_wm)

        # Compute holding duration
        entry_time = trade.get("entry_time", "")
        close_time = trade.get("close_time", "")
        holding_hours = trade.get("holding_hours", 0)
        if not holding_hours and entry_time and close_time:
            try:
                entry_dt = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
                close_dt = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
                holding_hours = round((close_dt - entry_dt).total_seconds() / 3600, 1)
            except (ValueError, AttributeError, TypeError):
                pass

        # L4: Compute realized R/R ratio
        pnl = trade.get("pnl_pct", 0)
        stop_pct_val = trade.get("stop_pct", 0)
        target_pct_val = trade.get("target_pct", 0)
        realized_rr = None
        if stop_pct_val > 0:
            realized_rr = round(pnl / stop_pct_val, 2)
        predicted_rr = None
        if stop_pct_val > 0 and target_pct_val > 0:
            predicted_rr = round(target_pct_val / stop_pct_val, 2)

        entry = {
            "ticker": ticker,
            "name": trade.get("name", ticker),
            "category": trade.get("category", ""),
            "strategy": trade.get("strategy", ""),
            "strategy_name": trade.get("strategy_name", ""),
            "direction": direction,
            "result": trade.get("result", "UNKNOWN"),
            "entry_price": entry_price,
            "close_price": trade.get("close_price"),
            "entry_time": entry_time,
            "close_time": close_time,
            "pnl_pct": pnl,
            "mae_pct": mae_pct,
            "mfe_pct": mfe_pct,
            "holding_hours": holding_hours,
            "holding_days": round(holding_hours / 24, 1) if holding_hours else 0,
            "score_at_entry": trade.get("score", 0),
            "raw_score": trade.get("raw_score", 0),
            "learning_multiplier": trade.get("learning_multiplier", 1.0),
            "timeframe": trade.get("timeframe", "1d"),
            "signals_at_entry": trade.get("signals_at_entry", {}),
            "atr_at_entry": trade.get("atr_at_entry"),
            "adx_at_entry": trade.get("adx_at_entry"),
            "volume_ratio_at_entry": trade.get("volume_ratio_at_entry", 1.0),
            "target_pct": target_pct_val,
            "stop_pct": stop_pct_val,
            "trailing_active": trade.get("trailing_active", False),
            # L4: R/R réalisé par stratégie
            "realized_rr": realized_rr,
            "predicted_rr": predicted_rr,
            # Regime context
            "regime_at_entry": trade.get("regime_at_entry"),
            "regime_match": trade.get("regime_match", True),
            # Version tracking
            "agent_versions": trade.get("agent_versions", {}),
            "strategy_version": trade.get("strategy_version", "1.0"),
        }

        return entry

    def _compute_strategy_ab(self, entries: list[dict]) -> dict:
        """Compute A/B strategy performance from journal entries.

        L4: Includes realized R/R per strategy.
        """
        perf: dict[str, dict] = {}

        for entry in entries:
            strategy = entry.get("strategy", "unknown")
            if strategy not in perf:
                perf[strategy] = {
                    "trades": 0, "wins": 0, "total_pnl": 0.0,
                    "tp_hits": 0, "sl_hits": 0, "expired": 0,
                    "mae_sum": 0.0, "mfe_sum": 0.0,
                    "mae_count": 0, "mfe_count": 0,
                    "total_holding": 0.0,
                    "rr_sum": 0.0, "rr_count": 0,
                    "regime_match_wins": 0, "regime_match_total": 0,
                    "pnl_list": [],  # For Sharpe computation
                }

            p = perf[strategy]
            p["trades"] += 1
            pnl = entry.get("pnl_pct", 0)
            p["total_pnl"] += pnl
            p["pnl_list"].append(pnl)
            if pnl > 0:
                p["wins"] += 1

            result = entry.get("result", "")
            if result == "TP_HIT":
                p["tp_hits"] += 1
            elif result == "SL_HIT":
                p["sl_hits"] += 1
            elif result == "EXPIRED":
                p["expired"] += 1

            mae = entry.get("mae_pct")
            if mae is not None:
                p["mae_sum"] += mae
                p["mae_count"] += 1

            mfe = entry.get("mfe_pct")
            if mfe is not None:
                p["mfe_sum"] += mfe
                p["mfe_count"] += 1

            p["total_holding"] += entry.get("holding_hours", 0)

            # L4: R/R réalisé tracking
            rr = entry.get("realized_rr")
            if rr is not None:
                p["rr_sum"] += rr
                p["rr_count"] += 1

            # Regime match tracking
            if entry.get("regime_match") is not None:
                p["regime_match_total"] += 1
                if entry.get("regime_match") and pnl > 0:
                    p["regime_match_wins"] += 1

        # Compute averages and clean up internal fields
        for strategy, p in perf.items():
            n = p["trades"]
            p["win_rate"] = round(p["wins"] / n * 100, 1) if n > 0 else 0
            p["avg_pnl"] = round(p["total_pnl"] / n, 2) if n > 0 else 0
            p["total_pnl"] = round(p["total_pnl"], 2)
            p["avg_mae"] = round(p["mae_sum"] / p["mae_count"], 2) if p["mae_count"] > 0 else None
            p["avg_mfe"] = round(p["mfe_sum"] / p["mfe_count"], 2) if p["mfe_count"] > 0 else None
            p["avg_holding_hours"] = round(p["total_holding"] / n, 1) if n > 0 else 0

            # L4: Average realized R/R
            p["avg_realized_rr"] = (
                round(p["rr_sum"] / p["rr_count"], 2) if p["rr_count"] > 0 else None
            )

            # L5: Sharpe ratio per strategy (annualized, assuming daily returns)
            pnl_list = p["pnl_list"]
            if len(pnl_list) >= 5:
                mean_pnl = sum(pnl_list) / len(pnl_list)
                variance = sum((x - mean_pnl) ** 2 for x in pnl_list) / (len(pnl_list) - 1)
                std_pnl = math.sqrt(variance) if variance > 0 else 0
                p["sharpe_ratio"] = round(
                    mean_pnl / std_pnl * math.sqrt(252) if std_pnl > 0 else 0, 2
                )
            else:
                p["sharpe_ratio"] = None

            # Regime match win rate
            p["regime_match_wr"] = (
                round(p["regime_match_wins"] / p["regime_match_total"] * 100, 1)
                if p["regime_match_total"] > 0 else None
            )

            # Clean up internal accumulators
            for key in ["mae_sum", "mfe_sum", "mae_count", "mfe_count",
                        "total_holding", "rr_sum", "rr_count", "pnl_list",
                        "regime_match_wins", "regime_match_total"]:
                p.pop(key, None)

        return perf

    def compute_weekly_summary(self) -> dict:
        """L1/C5: Compute weekly aggregated summary for Learning 3.

        Groups entries by week and computes aggregate stats per strategy per week.
        Returns dict with weekly stats for the validation cycle.
        """
        entries = _load_journal_entries()
        now = datetime.now(timezone.utc)
        one_week_ago = (now - timedelta(days=7)).isoformat()

        # Filter to this week's entries
        this_week = [
            e for e in entries
            if (e.get("close_time") or e.get("entry_time", "")) >= one_week_ago
        ]

        weekly_perf = self._compute_strategy_ab(this_week)

        return {
            "period_start": one_week_ago,
            "period_end": now.isoformat(),
            "entries_count": len(this_week),
            "strategy_performance": weekly_perf,
            "total_pnl": round(sum(e.get("pnl_pct", 0) for e in this_week), 2),
            "win_rate": round(
                sum(1 for e in this_week if (e.get("pnl_pct") or 0) > 0)
                / len(this_week) * 100 if this_week else 0, 1
            ),
        }

    def get_entries(self) -> list[dict]:
        """Get all journal entries (for API/frontend)."""
        return _load_journal_entries()

    def get_strategy_ab_report(self) -> dict:
        """Get A/B strategy comparison report."""
        entries = _load_journal_entries()
        return self._compute_strategy_ab(entries)

    def get_metrics(self) -> dict:
        self._ensure_total_entries()
        return {
            "last_entries_processed": self._last_run_entries_processed,
            "last_expired_closed": self._last_run_expired_closed,
            "total_entries": self._total_entries,
        }
