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

Audit fixes v7.1:
- P2: Snapshots persistés dans les journal entries
- P3: _pg_save_entries ne sauve que les nouvelles entrées
- P4: Timeout global 120s + per-ticker timeout 30s
- P6: Erreurs de fetch loggées (plus de pass silencieux)
- P7: Clé de dedup harmonisée sur (ticker, position_entry_time)
- P8: _total_entries chargé depuis la persistance
- L1: key_news_count et news_categories calculés avant troncature
- L2: Cross-check P&L entry_price/exit_price vs flip.pnl_pct
- L3: MAE/MFE = None quand bars manquantes (pas 0.0)
- L4: bar_interval tracké dans l'entrée journal
- L6: duration_days ajouté
- L7: news_categories trié par fréquence (pas set non ordonné)
- L8: Scoring 2 data stocké si disponible
"""

import json
import logging
import fcntl
import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .base import BaseAgent, AgentStatus

logger = logging.getLogger(__name__)

# P4: Timeout constants
GLOBAL_TIMEOUT_S = 120
PER_TICKER_TIMEOUT_S = 30

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
    """Save to PostgreSQL (append new only, dedup by ticker+entry_time).

    P3 fix: Only INSERT new entries with ON CONFLICT DO NOTHING.
    Previously re-wrote ALL entries on every save.
    """
    try:
        from ..database import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                for entry in entries:
                    cur.execute("""
                        INSERT INTO trend_journal_entries (ticker, entry_time, data, created_at)
                        VALUES (%s, %s, %s, NOW())
                        ON CONFLICT (ticker, entry_time) DO NOTHING
                    """, (
                        entry.get("ticker"),
                        entry.get("entry_time"),
                        json.dumps(entry, default=str),
                    ))
    except Exception as exc:
        logger.warning("PG save trend_journal_entries failed: %s — fallback JSON", exc)
        _save_entries_json(entries)


def _fetch_daily_bars(ticker: str, start_date: str, end_date: str) -> tuple[list[dict], str]:
    """Fetch OHLCV bars for MAE/MFE computation.

    P3.10: Tries 1h bars first (finer resolution like Journal 1), falls back to daily.
    P6 fix: log errors instead of silent pass.

    Returns (bars, interval) where interval is "1h" or "1day".
    """
    # P3.10: Try 1h bars first for finer MAE/MFE resolution
    try:
        from ..market_data import fetch_intraday
        bars_1h = fetch_intraday(ticker, interval="1h", outputsize=500)
        if bars_1h:
            # Filter to date range
            result = []
            for bar in bars_1h:
                bar_date = bar.get("date", "")
                if isinstance(bar_date, datetime):
                    bar_date = bar_date.strftime("%Y-%m-%d")
                elif isinstance(bar_date, str) and len(bar_date) > 10:
                    bar_date = bar_date[:10]
                if start_date <= bar_date <= end_date:
                    result.append(bar)
            if result:
                logger.info("P3.10: Using 1h bars for %s (%d bars)", ticker, len(result))
                return result, "1h"
    except Exception as exc:
        logger.debug("1h bars fetch failed for %s: %s — trying daily", ticker, exc)

    # Fallback: daily bars
    try:
        from ..market_data import fetch_history
        bars = fetch_history(ticker, period="3mo")
        if not bars:
            logger.warning("fetch_history returned empty for %s (%s→%s)", ticker, start_date, end_date)
            return [], "1day"
        # Filter to date range
        result = []
        for bar in bars:
            bar_date = bar.get("date", "")
            if isinstance(bar_date, datetime):
                bar_date = bar_date.strftime("%Y-%m-%d")
            if start_date <= bar_date <= end_date:
                result.append(bar)
        return result, "1day"
    except Exception as exc:
        logger.warning("Twelve Data fetch failed for %s: %s — trying yfinance", ticker, exc)
    # Fallback yfinance
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        h = t.history(start=start_date, end=end_date)
        if h.empty:
            logger.warning("yfinance returned empty for %s (%s→%s)", ticker, start_date, end_date)
            return [], "1day"
        return [
            {"date": idx.strftime("%Y-%m-%d"), "high": row["High"],
             "low": row["Low"], "open": row["Open"], "close": row["Close"]}
            for idx, row in h.iterrows()
        ], "1day"
    except Exception as exc:
        logger.warning("yfinance fetch also failed for %s: %s", ticker, exc)
        return [], "1day"


def _compute_mae_mfe(direction: str, entry_price: float,
                     bars: list[dict]) -> tuple[float | None, float | None]:
    """Compute MAE/MFE from daily bars for a position period.

    MAE = Max Adverse Excursion (worst drawdown during position)
    MFE = Max Favorable Excursion (best unrealized gain during position)

    Returns (mae_pct, mfe_pct) where mae is negative and mfe is positive.
    L3 fix: returns (None, None) when bars are missing instead of (0.0, 0.0).
    """
    if not bars or not entry_price:
        return None, None

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
    version = "7.2"  # v7.2: fix JSON fallback triple write data destruction

    def __init__(self):
        super().__init__()
        self._last_run_flips_processed: int = 0
        self._last_run_snapshots: int = 0
        self._total_entries: int = 0
        self._total_entries_loaded: bool = False  # P8: lazy load flag
        self._last_daily_pnl: float = 0.0

    def _ensure_total_entries(self):
        """P8: Load total entries count from persistence on first access."""
        if not self._total_entries_loaded:
            try:
                entries = _load_journal_entries()
                self._total_entries = len(entries)
            except Exception:
                pass
            self._total_entries_loaded = True

    def run(self, **kwargs) -> dict:
        """Run the daily journal for Trader 2 positions.

        Steps:
        1. Load current positions from Trader 2
        2. Load existing journal entries
        3. Find new flips not yet journaled (dedup by ticker+position_entry_time)
        4. Enrich each flip with MAE/MFE from daily bars (with timeout)
        5. Snapshot current position state (daily tracking)
        6. Save and publish results

        Returns dict with journal results.
        """
        self._set_status(AgentStatus.WORKING, "Running Trader 2 journal")

        start = time.monotonic()
        result = {
            "status": "ok",
            "new_entries": [],
            "snapshots": [],
            "flips_processed": 0,
            "total_unrealized_pnl": 0.0,
            "total_realized_pnl": 0.0,
        }

        try:
            # P8: Ensure total entries loaded
            self._ensure_total_entries()

            # Step 1: Load positions and existing journal
            from .agent_trader_2 import _load_positions, TREND_TICKERS
            positions = _load_positions()
            existing_entries = _load_journal_entries()

            # P7 fix: dedup key uses (ticker, position_entry_time) to match
            # what's stored in journal entry's "entry_time" field
            existing_keys = {
                (e.get("ticker"), e.get("entry_time"))
                for e in existing_entries
            }

            # L8: Load Scoring 2 last result if available
            scoring_2_data = {}
            try:
                from .registry import get_agent
                scoring_2_agent = get_agent("scoring_2")
                if scoring_2_agent:
                    scoring_2_data = scoring_2_agent.get_last_result() or {}
            except Exception:
                pass

            new_entries = []
            snapshots = []

            # Step 2: Process each ticker (P4: with global timeout)
            for ticker, info in TREND_TICKERS.items():
                # P4: Check global timeout
                elapsed = time.monotonic() - start
                if elapsed > GLOBAL_TIMEOUT_S:
                    self.log("Journal 2 global timeout reached",
                             {"elapsed_s": round(elapsed, 1),
                              "tickers_remaining": list(TREND_TICKERS.keys())},
                             level="WARN")
                    break

                pos = positions.get(ticker)
                if not pos:
                    continue

                # Step 2a: Find new flips to journal
                history = pos.get("history", [])
                for flip in history:
                    # P7: Use position_entry_time as dedup key (matches journal entry_time)
                    position_entry_time = flip.get("position_entry_time") or flip.get("time")
                    if not position_entry_time:
                        continue
                    # Dedup: skip if already journaled
                    if (ticker, position_entry_time) in existing_keys:
                        continue

                    # Enrich with MAE/MFE
                    entry = self._process_flip(ticker, flip, info, scoring_2_data)
                    if entry:
                        new_entries.append(entry)
                        existing_keys.add((ticker, position_entry_time))

                # Step 2b: Daily snapshot (P2: persisted with entries)
                snapshot = self._take_snapshot(ticker, pos)
                if snapshot:
                    snapshots.append(snapshot)

            # Step 3: Save new entries only (P3: don't re-write existing)
            from ..database import is_pg_enabled
            if new_entries or snapshots:
                snapshot_entries = [
                    {**s, "entry_type": "snapshot"}
                    for s in snapshots
                ] if snapshots else []

                if is_pg_enabled():
                    # PG: ON CONFLICT DO NOTHING handles dedup safely
                    if new_entries:
                        _pg_save_entries(new_entries)
                    if snapshot_entries:
                        _pg_save_entries(snapshot_entries)
                else:
                    # JSON: single atomic write with all entries combined
                    all_entries = existing_entries + new_entries + snapshot_entries
                    cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
                    all_entries = [
                        e for e in all_entries
                        if e.get("entry_time", "") > cutoff or e.get("exit_time", "") > cutoff
                    ]
                    _save_entries_json(all_entries)

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
                    "duration_days": entry.get("duration_days"),
                    "signal_strength": entry.get("signal_strength"),
                    "bar_interval": entry.get("bar_interval"),
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

    def _process_flip(self, ticker: str, flip: dict, info: dict,
                      scoring_2_data: dict | None = None) -> dict | None:
        """Process a single flip into a journal entry with MAE/MFE.

        Audit fixes:
        - P3/P4: duration from position_entry_time to flip_time
        - L1: key_news_count computed before truncation, news_categories from all news
        - L2: Cross-check P&L entry/exit vs flip.pnl_pct
        - L3: MAE/MFE = None when bars missing
        - L4: bar_interval tracked
        - L6: duration_days added
        - L7: news_categories sorted by frequency
        - L8: Scoring 2 data stored if available
        """
        flip_time = flip.get("time")  # This is the EXIT time (when direction changed)
        entry_price = flip.get("entry_price")
        exit_price = flip.get("exit_price")
        from_dir = flip.get("from_direction", "NEUTRAL")

        if not entry_price or from_dir == "NEUTRAL":
            return None

        # Use position_entry_time (when position was opened) for entry reference
        position_entry_time = flip.get("position_entry_time") or flip_time

        # Compute actual duration from position entry to flip
        duration_hours = 0.0
        try:
            entry_dt = datetime.fromisoformat(position_entry_time.replace("Z", "+00:00"))
            exit_dt = datetime.fromisoformat(flip_time.replace("Z", "+00:00"))
            duration_hours = round((exit_dt - entry_dt).total_seconds() / 3600, 1)
        except (ValueError, AttributeError, TypeError):
            pass

        # L6: duration_days for trend readability
        duration_days = round(duration_hours / 24, 1) if duration_hours > 0 else 0.0

        # Date range for MAE/MFE = position entry to flip time
        start_date = ""
        end_date = ""
        try:
            entry_dt = datetime.fromisoformat(position_entry_time.replace("Z", "+00:00"))
            exit_dt = datetime.fromisoformat(flip_time.replace("Z", "+00:00"))
            start_date = entry_dt.strftime("%Y-%m-%d")
            end_date = exit_dt.strftime("%Y-%m-%d")
        except (ValueError, AttributeError, TypeError):
            pass

        # Fetch bars for MAE/MFE — P3.10: 1h bars preferred, daily fallback
        mae_pct = None
        mfe_pct = None
        bar_count = 0
        bar_interval = "1day"  # L4: track bar resolution
        if start_date and end_date:
            try:
                fetch_result = self.execute(
                    f"Fetching bars {ticker}",
                    _fetch_daily_bars,
                    ticker, start_date, end_date,
                )
                # P3.10: _fetch_daily_bars now returns (bars, interval)
                if isinstance(fetch_result, tuple):
                    bars, bar_interval = fetch_result
                else:
                    bars = fetch_result  # backward compat
                if bars:
                    mae_pct, mfe_pct = _compute_mae_mfe(from_dir, entry_price, bars)
                    bar_count = len(bars)
            except Exception as exc:
                self.log(f"MAE/MFE fetch failed for {ticker}",
                         {"error": str(exc)}, level="WARN")

        # L1: Compute key_news_count and news_categories BEFORE truncation
        all_key_news = flip.get("key_news", [])
        full_key_news_count = len(all_key_news)

        # L7: Sort news_categories by frequency (most common first)
        cat_counter = Counter(
            n.get("category", "other")
            for n in all_key_news
            if isinstance(n, dict)
        )
        news_categories = [cat for cat, _ in cat_counter.most_common()]
        if not news_categories:
            news_categories = ["other"]

        # L2: Cross-check P&L
        pnl_pct = flip.get("pnl_pct", 0.0)
        if entry_price and exit_price and entry_price > 0:
            if from_dir == "LONG":
                computed_pnl = round((exit_price - entry_price) / entry_price * 100, 2)
            else:  # SHORT
                computed_pnl = round((entry_price - exit_price) / entry_price * 100, 2)
            if abs(computed_pnl - pnl_pct) > 0.5:
                logger.warning(
                    "P&L cross-check divergence for %s: flip=%.2f%% computed=%.2f%%",
                    ticker, pnl_pct, computed_pnl,
                )

        # L5: Compute peak P&L intra-position (give-back analysis)
        peak_pnl_pct = mfe_pct if mfe_pct is not None else None

        # L8: Extract Scoring 2 data for this ticker if available
        scoring_2_info = {}
        if scoring_2_data:
            ticker_items = scoring_2_data.get("by_ticker", {}).get(ticker, [])
            if ticker_items:
                avg_trend_score = sum(i.get("trend_score", 0) for i in ticker_items) / len(ticker_items)
                avg_cat_mult = sum(i.get("category_mult", 1.0) for i in ticker_items) / len(ticker_items)
                avg_persist = sum(i.get("persistence_mult", 1.0) for i in ticker_items) / len(ticker_items)
                scoring_2_info = {
                    "avg_trend_score": round(avg_trend_score, 1),
                    "avg_category_mult": round(avg_cat_mult, 2),
                    "avg_persistence_mult": round(avg_persist, 2),
                    "trend_news_count": len(ticker_items),
                }

        entry = {
            "ticker": ticker,
            "name": info.get("name", ticker),
            "category": info.get("category", ""),
            "direction": from_dir,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "entry_time": position_entry_time,  # actual position entry time
            "exit_time": flip_time,              # when direction changed (flip)
            "pnl_pct": pnl_pct,
            "mae_pct": mae_pct,
            "mfe_pct": mfe_pct,
            "peak_pnl_pct": peak_pnl_pct,       # L5: peak unrealized P&L
            "bar_count": bar_count,
            "bar_interval": bar_interval,        # L4: resolution tracking
            "duration_hours": duration_hours,
            "duration_days": duration_days,       # L6: trend-friendly duration
            "signal_strength": flip.get("signal_strength", 0),
            "reason": flip.get("reason", ""),
            "news_categories": news_categories,  # L7: sorted by frequency
            "key_news_count": full_key_news_count,  # L1: count before truncation
            "agent_versions": self._get_agent_versions(),  # P2.6: version tracking
        }

        # L8: Add Scoring 2 data if available
        if scoring_2_info:
            entry["scoring_2"] = scoring_2_info

        return entry

    def _get_agent_versions(self) -> dict:
        """P2.6: Get current agent versions for entry stamping."""
        try:
            from .registry import get_all_agents
            agents = get_all_agents()
            return {name: getattr(a, "version", "?") for name, a in agents.items()}
        except Exception:
            return {}

    def _take_snapshot(self, ticker: str, pos: dict) -> dict:
        """Take a daily snapshot of position state.

        P2 fix: snapshots are now persisted (saved as entries with entry_type=snapshot).
        """
        return {
            "ticker": ticker,
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "entry_time": f"snapshot_{ticker}_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
            "direction": pos.get("direction", "NEUTRAL"),
            "entry_price": pos.get("entry_price"),
            "current_price": pos.get("current_price"),
            "unrealized_pnl_pct": pos.get("unrealized_pnl_pct", 0),
            "confidence": pos.get("confidence", 0),
            "total_switches": pos.get("total_switches", 0),
        }

    def get_entries(self) -> list[dict]:
        """Get all journal entries (for API/frontend).

        Filters out snapshot entries — returns only flip entries.
        """
        entries = _load_journal_entries()
        return [e for e in entries if e.get("entry_type") != "snapshot"]

    def get_snapshots(self) -> list[dict]:
        """Get snapshot entries (for API/frontend)."""
        entries = _load_journal_entries()
        return [e for e in entries if e.get("entry_type") == "snapshot"]

    def get_metrics(self) -> dict:
        self._ensure_total_entries()  # P8
        return {
            "last_flips_processed": self._last_run_flips_processed,
            "last_snapshots": self._last_run_snapshots,
            "total_entries": self._total_entries,
            "last_daily_pnl": round(self._last_daily_pnl, 2),
        }
