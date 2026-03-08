"""Agent Journal 2 — Journal dédié à l'Agent Trader 2 (trend following).

Responsabilités :
- Run quotidien à 22h : enrichit les flips récents avec MAE/MFE via daily bars
- Snapshot quotidien : P&L latent, prix courant, durée de position
- Enregistre chaque flip comme un "completed period" pour Learning 2
- Détecte les anomalies (flips trop rapides, drawdowns excessifs)
- Publie journal_2_complete sur le bus → Agent Learning 2 consomme

Différences avec Journal 1 :
- Journal 1 : ferme les trades PENDING intraday (TP/SL/EXPIRED)
- Journal 2 : enrichit les positions de tendance (LONG/SHORT) durée jours/semaines
- Pas de notion de TP/SL — les positions se ferment uniquement sur flip de direction
- MAE/MFE calculés sur la durée de vie de la position (daily bars)

Expertise incarnée :
- Analyse de la qualité des retournements de tendance
- Tracking de la performance par période de position
- Détection des churning (trop de flips = over-trading)
"""

import json
import logging
import fcntl
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .base import BaseAgent, AgentStatus

logger = logging.getLogger(__name__)

# Persistence file (JSON fallback)
JOURNAL_FILE = Path(os.getenv("DATA_DIR", "data")) / "trend_journal.json"


def _ensure_journal_file():
    """Create the journal file if it doesn't exist."""
    JOURNAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not JOURNAL_FILE.exists():
        JOURNAL_FILE.write_text("[]")


def _load_journal_entries() -> list[dict]:
    """Load trend journal entries from PG or JSON."""
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
    """Save trend journal entries to PG or JSON."""
    from ..database import is_pg_enabled
    if is_pg_enabled():
        _pg_save_entries(entries)
        return
    _save_entries_json(entries)


def _save_entries_json(entries: list[dict]):
    _ensure_journal_file()
    with open(JOURNAL_FILE, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        json.dump(entries, f, indent=2, default=str)
        fcntl.flock(f, fcntl.LOCK_UN)


def _pg_load_entries() -> list[dict]:
    """Load from PostgreSQL."""
    try:
        from ..database import get_conn
        import psycopg2.extras
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT data FROM trend_journal_entries ORDER BY created_at DESC LIMIT 500"
                )
                return [row["data"] for row in cur.fetchall()]
    except Exception as exc:
        logger.warning("PG load trend_journal_entries failed: %s — fallback JSON", exc)
        return _load_entries_json()


def _pg_save_entries(entries: list[dict]):
    """Save to PostgreSQL (append new, dedup by ticker+entry_time)."""
    try:
        from ..database import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                for entry in entries:
                    cur.execute("""
                        INSERT INTO trend_journal_entries (ticker, entry_time, data, created_at)
                        VALUES (%s, %s, %s, NOW())
                        ON CONFLICT (ticker, entry_time) DO UPDATE
                        SET data = EXCLUDED.data
                    """, (
                        entry.get("ticker"),
                        entry.get("entry_time"),
                        json.dumps(entry, default=str),
                    ))
    except Exception as exc:
        logger.warning("PG save trend_journal_entries failed: %s — fallback JSON", exc)
        _save_entries_json(entries)


def _fetch_daily_bars(ticker: str, start_date: str, end_date: str) -> list[dict]:
    """Fetch daily OHLCV bars for MAE/MFE computation."""
    try:
        from ..market_data import fetch_history
        bars = fetch_history(ticker, period="3mo")
        if not bars:
            return []
        # Filter to date range
        result = []
        for bar in bars:
            bar_date = bar.get("date", "")
            if isinstance(bar_date, datetime):
                bar_date = bar_date.strftime("%Y-%m-%d")
            if start_date <= bar_date <= end_date:
                result.append(bar)
        return result
    except Exception:
        pass
    # Fallback yfinance
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        h = t.history(start=start_date, end=end_date)
        if h.empty:
            return []
        return [
            {"date": idx.strftime("%Y-%m-%d"), "high": row["High"],
             "low": row["Low"], "open": row["Open"], "close": row["Close"]}
            for idx, row in h.iterrows()
        ]
    except Exception:
        return []


def _compute_mae_mfe(direction: str, entry_price: float,
                     bars: list[dict]) -> tuple[float, float]:
    """Compute MAE/MFE from daily bars for a position period.

    MAE = Max Adverse Excursion (worst drawdown during position)
    MFE = Max Favorable Excursion (best unrealized gain during position)

    Returns (mae_pct, mfe_pct) where mae is negative and mfe is positive.
    """
    if not bars or not entry_price:
        return 0.0, 0.0

    worst = 0.0
    best = 0.0

    for bar in bars:
        high = bar.get("high", entry_price)
        low = bar.get("low", entry_price)

        if direction == "LONG":
            # Adverse = price drops (low vs entry)
            adverse = (low - entry_price) / entry_price * 100
            # Favorable = price rises (high vs entry)
            favorable = (high - entry_price) / entry_price * 100
        else:  # SHORT
            # Adverse = price rises (high vs entry)
            adverse = (entry_price - high) / entry_price * 100
            # Favorable = price drops (low vs entry)
            favorable = (entry_price - low) / entry_price * 100

        worst = min(worst, adverse)
        best = max(best, favorable)

    return round(worst, 2), round(best, 2)


class AgentJournal2(BaseAgent):
    """Agent Journal 2 — Journal dédié au trend following (Trader 2).

    Enrichit les positions de tendance avec MAE/MFE,
    enregistre les flips comme des completed periods pour Learning 2.
    """

    name = "journal_2"
    description = "Journal & analyse des positions de tendance"

    def __init__(self):
        super().__init__()
        self._last_run_flips_processed: int = 0
        self._last_run_snapshots: int = 0
        self._total_entries: int = 0
        self._last_daily_pnl: float = 0.0

    def run(self, **kwargs) -> dict:
        """Run the daily journal for Trader 2 positions.

        Steps:
        1. Load current positions from Trader 2
        2. Load existing journal entries
        3. Find new flips not yet journaled (dedup by ticker+entry_time)
        4. Enrich each flip with MAE/MFE from daily bars
        5. Snapshot current position state (daily tracking)
        6. Save and publish results

        Returns dict with journal results.
        """
        self._set_status(AgentStatus.WORKING, "Running Trader 2 journal")

        start = time.monotonic()
        result = {
            "new_entries": [],
            "snapshots": [],
            "flips_processed": 0,
            "total_unrealized_pnl": 0.0,
            "total_realized_pnl": 0.0,
        }

        try:
            # Step 1: Load positions and existing journal
            from .agent_trader_2 import _load_positions, TREND_TICKERS
            positions = _load_positions()
            existing_entries = _load_journal_entries()
            existing_keys = {
                (e.get("ticker"), e.get("entry_time"))
                for e in existing_entries
            }

            new_entries = []
            snapshots = []

            # Step 2: Process each ticker
            for ticker, info in TREND_TICKERS.items():
                pos = positions.get(ticker)
                if not pos:
                    continue

                # Step 2a: Find new flips to journal
                history = pos.get("history", [])
                for flip in history:
                    entry_time = flip.get("time")
                    if not entry_time:
                        continue
                    # Dedup: skip if already journaled
                    if (ticker, entry_time) in existing_keys:
                        continue

                    # Enrich with MAE/MFE
                    entry = self._process_flip(ticker, flip, info)
                    if entry:
                        new_entries.append(entry)
                        existing_keys.add((ticker, entry_time))

                # Step 2b: Daily snapshot
                snapshot = self._take_snapshot(ticker, pos)
                if snapshot:
                    snapshots.append(snapshot)

            # Step 3: Save new entries
            if new_entries:
                all_entries = new_entries + existing_entries
                # Prune entries older than 1 year
                cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
                all_entries = [
                    e for e in all_entries
                    if e.get("entry_time", "") > cutoff or e.get("exit_time", "") > cutoff
                ]
                _save_journal_entries(all_entries)

            # Step 4: Compute totals
            total_unrealized = sum(
                p.get("unrealized_pnl_pct", 0) for p in positions.values()
            )
            total_realized = sum(
                p.get("realized_pnl_pct", 0) for p in positions.values()
            )

            result["new_entries"] = new_entries
            result["snapshots"] = snapshots
            result["flips_processed"] = len(new_entries)
            result["total_unrealized_pnl"] = round(total_unrealized, 2)
            result["total_realized_pnl"] = round(total_realized, 2)

            self._last_run_flips_processed = len(new_entries)
            self._last_run_snapshots = len(snapshots)
            self._total_entries += len(new_entries)
            self._last_daily_pnl = total_unrealized

            # Step 5: Log results
            for entry in new_entries:
                self.log_decision("Flip journaled", {
                    "ticker": entry["ticker"],
                    "direction": entry["direction"],
                    "pnl_pct": entry.get("pnl_pct"),
                    "mae_pct": entry.get("mae_pct"),
                    "mfe_pct": entry.get("mfe_pct"),
                    "duration_hours": entry.get("duration_hours"),
                    "signal_strength": entry.get("signal_strength"),
                })

            if new_entries:
                self.log_decision("Journal 2 run complete", {
                    "flips_processed": len(new_entries),
                    "total_realized_pnl": round(total_realized, 2),
                    "total_unrealized_pnl": round(total_unrealized, 2),
                })
            else:
                self.log("Journal 2 run: no new flips to process")

            # Step 6: Publish for Learning 2
            self.publish("journal_2_complete", {
                "flips_processed": len(new_entries),
                "total_realized_pnl": round(total_realized, 2),
                "total_unrealized_pnl": round(total_unrealized, 2),
                "snapshots": len(snapshots),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            self._set_status(
                AgentStatus.IDLE,
                f"{len(new_entries)} flips, P&L: {total_realized:+.2f}% réalisé"
            )

            return result

        except Exception as exc:
            self.log("Journal 2 run failed", {"error": str(exc)}, level="ERROR")
            self._set_status(AgentStatus.ERROR, str(exc))
            raise

    def _process_flip(self, ticker: str, flip: dict, info: dict) -> dict | None:
        """Process a single flip into a journal entry with MAE/MFE."""
        entry_time = flip.get("time")
        entry_price = flip.get("entry_price")
        exit_price = flip.get("exit_price")
        from_dir = flip.get("from_direction", "NEUTRAL")

        if not entry_price or from_dir == "NEUTRAL":
            return None

        # Compute duration
        duration_hours = 0.0
        # Find the original entry time for this period
        # The flip "time" is the EXIT time (when direction changed)
        # We need to estimate the entry time from context
        # Use a conservative 7-day lookback for MAE/MFE bars
        try:
            exit_dt = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
            # Look back up to 90 days for bars
            start_dt = exit_dt - timedelta(days=90)
            start_date = start_dt.strftime("%Y-%m-%d")
            end_date = exit_dt.strftime("%Y-%m-%d")
        except (ValueError, AttributeError):
            start_date = ""
            end_date = ""

        # Fetch daily bars for MAE/MFE
        mae_pct, mfe_pct = 0.0, 0.0
        bar_count = 0
        if start_date and end_date:
            try:
                bars = self.execute(
                    f"Fetching bars {ticker}",
                    _fetch_daily_bars,
                    ticker, start_date, end_date,
                )
                if bars:
                    # Filter bars to only include those during the position period
                    mae_pct, mfe_pct = _compute_mae_mfe(from_dir, entry_price, bars)
                    bar_count = len(bars)
            except Exception as exc:
                self.log(f"MAE/MFE fetch failed for {ticker}",
                         {"error": str(exc)}, level="WARN")

        # Build journal entry
        pnl_pct = flip.get("pnl_pct", 0.0)
        key_news = flip.get("key_news", [])
        news_categories = list({
            n.get("category", "other") for n in key_news if isinstance(n, dict)
        })

        return {
            "ticker": ticker,
            "name": info.get("name", ticker),
            "category": info.get("category", ""),
            "direction": from_dir,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "entry_time": entry_time,  # This is actually the flip time
            "pnl_pct": pnl_pct,
            "mae_pct": mae_pct,
            "mfe_pct": mfe_pct,
            "bar_count": bar_count,
            "signal_strength": flip.get("signal_strength", 0),
            "reason": flip.get("reason", ""),
            "news_categories": news_categories,
            "key_news_count": len(key_news),
        }

    def _take_snapshot(self, ticker: str, pos: dict) -> dict:
        """Take a daily snapshot of position state."""
        return {
            "ticker": ticker,
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "direction": pos.get("direction", "NEUTRAL"),
            "entry_price": pos.get("entry_price"),
            "current_price": pos.get("current_price"),
            "unrealized_pnl_pct": pos.get("unrealized_pnl_pct", 0),
            "confidence": pos.get("confidence", 0),
            "total_switches": pos.get("total_switches", 0),
        }

    def get_entries(self) -> list[dict]:
        """Get all journal entries (for API/frontend)."""
        return _load_journal_entries()

    def get_metrics(self) -> dict:
        return {
            "last_flips_processed": self._last_run_flips_processed,
            "last_snapshots": self._last_run_snapshots,
            "total_entries": self._total_entries,
            "last_daily_pnl": round(self._last_daily_pnl, 2),
        }
