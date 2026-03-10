"""Agent Journal 4 — Journal dédié à l'Agent Trader 4 (Meta/Ensemble) (Équipe 4).

Responsabilités :
- Run quotidien à 22h : after Journal 1, 2, 3
- Closes expired meta positions (open > max_hold_hours without signal renewal)
- Records: which teams contributed to each signal, confluence level, per-team accuracy
- Tracks which team combinations produce best results (1+2, 1+3, 2+3, 1+2+3)
- MAE/MFE per trade, Sharpe ratio per combo
- Weekly summary for Learning 4 validation cycle
- Publishes "journal_4_complete" on bus → Agent Learning 4 consomme

Différences avec les autres journaux :
- Journal 1 : ferme les trades PENDING intraday (TP/SL/EXPIRED)
- Journal 2 : enrichit les positions de tendance (flips, daily bars)
- Journal 3 : journal technique, A/B testing par stratégie, Sharpe ratio
- Journal 4 : tracks multi-team confluence accuracy, team combination performance, weekly summary

Expertise incarnée :
- Multi-factor attribution analysis
- Understanding which signal combinations are most reliable
- Performance decomposition by contributing source
"""

import json
import logging
import math
import fcntl
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

# Max hold hours before force-close (no signal renewal)
MAX_HOLD_HOURS = 72

# Persistence file (JSON fallback)
JOURNAL_FILE = Path(os.getenv("DATA_DIR", "data")) / "meta_journal.json"


def _ensure_journal_file():
    """Create the journal file if it doesn't exist."""
    JOURNAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not JOURNAL_FILE.exists():
        JOURNAL_FILE.write_text("[]")


def _load_journal_entries() -> list[dict]:
    """Load meta journal entries from PG or JSON."""
    try:
        from ..database import is_pg_enabled
        if is_pg_enabled():
            return _pg_load_entries()
    except Exception:
        pass
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
    """Save meta journal entries to PG or JSON."""
    try:
        from ..database import is_pg_enabled
        if is_pg_enabled():
            _pg_save_entries(entries)
            return
    except Exception:
        pass
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
                    "SELECT data FROM meta_journal_entries ORDER BY created_at DESC LIMIT 500"
                )
                return [row["data"] for row in cur.fetchall()]
    except Exception as exc:
        logger.warning("PG load meta_journal_entries failed: %s — fallback JSON", exc)
        return _load_entries_json()


def _pg_save_entries(entries: list[dict]):
    """Save to PostgreSQL (append new only, dedup by ticker+entry_time)."""
    try:
        from ..database import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                for entry in entries:
                    cur.execute("""
                        INSERT INTO meta_journal_entries (ticker, entry_time, data, created_at)
                        VALUES (%s, %s, %s, NOW())
                        ON CONFLICT (ticker, entry_time) DO NOTHING
                    """, (
                        entry.get("ticker"),
                        entry.get("entry_time"),
                        json.dumps(entry, default=str),
                    ))
    except Exception as exc:
        logger.warning("PG save meta_journal_entries failed: %s — fallback JSON", exc)
        _save_entries_json(entries)


def _fetch_daily_bars(ticker: str, start_date: str, end_date: str) -> list[dict]:
    """Fetch daily OHLCV bars for MAE/MFE computation."""
    try:
        from ..market_data import fetch_history
        df = fetch_history(ticker, period_days=90)
        if df is None or df.empty:
            logger.warning("fetch_history returned empty for %s (%s->%s)",
                           ticker, start_date, end_date)
            return []
        result = []
        for idx, row in df.iterrows():
            bar_date = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
            if start_date <= bar_date <= end_date:
                result.append({
                    "date": bar_date, "high": row["High"], "low": row["Low"],
                    "open": row["Open"], "close": row["Close"],
                })
        return result
    except Exception as exc:
        logger.warning("Twelve Data fetch failed for %s: %s — trying yfinance",
                       ticker, exc)
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        h = t.history(start=start_date, end=end_date)
        if h.empty:
            logger.warning("yfinance returned empty for %s (%s->%s)",
                           ticker, start_date, end_date)
            return []
        return [
            {"date": idx.strftime("%Y-%m-%d"), "high": row["High"],
             "low": row["Low"], "open": row["Open"], "close": row["Close"]}
            for idx, row in h.iterrows()
        ]
    except Exception as exc:
        logger.warning("yfinance fetch also failed for %s: %s", ticker, exc)
        return []


def _compute_mae_mfe(direction: str, entry_price: float,
                     bars: list[dict]) -> tuple[float | None, float | None]:
    """Compute MAE/MFE from daily bars for a position period.

    Returns (mae_pct, mfe_pct) where mae is negative, mfe is positive.
    Returns (None, None) when bars are missing.
    """
    if not bars or not entry_price:
        return None, None

    worst = 0.0
    best = 0.0

    for bar in bars:
        high = bar.get("high", entry_price)
        low = bar.get("low", entry_price)

        if direction == "LONG":
            adverse = (low - entry_price) / entry_price * 100
            favorable = (high - entry_price) / entry_price * 100
        else:  # SHORT
            adverse = (entry_price - high) / entry_price * 100
            favorable = (entry_price - low) / entry_price * 100

        worst = min(worst, adverse)
        best = max(best, favorable)

    return round(worst, 2), round(best, 2)


def _extract_team_contributions(source_details: dict) -> list[str]:
    """Extract which teams contributed to a signal from source_details.

    Returns list of team names (e.g. ["news", "trend", "tech"]).
    """
    teams = []
    for key in ("news", "trend", "tech"):
        if key in source_details:
            teams.append(key)
    return sorted(teams)


def _team_combination_key(teams: list[str]) -> str:
    """Create a canonical key for a team combination (e.g. 'news+tech+trend')."""
    return "+".join(sorted(teams))


def _classify_duration(duration_hours: float) -> str:
    """L3: Classify trade duration into categories for learning."""
    if duration_hours <= 8:
        return "intraday"
    elif duration_hours <= 24:
        return "overnight"
    else:
        return "multi_day"


def _compute_sharpe(pnls: list[float]) -> float | None:
    """Compute annualized Sharpe ratio from a list of PnL percentages."""
    if len(pnls) < 3:
        return None
    mean = sum(pnls) / len(pnls)
    variance = sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1)
    if variance == 0:
        return None
    std = math.sqrt(variance)
    # Annualize: assume ~250 trading days
    return round(mean / std * math.sqrt(250), 2)


class AgentJournal4(BaseAgent):
    """Agent Journal 4 — Journal dédié au Meta/Ensemble trading (Trader 4).

    Enrichit les positions meta avec MAE/MFE, tracks team combination
    performance, closes expired positions, provides weekly summary.
    """

    name = "journal_4"
    description = "Journal & analyse des positions meta/ensemble"
    version = "2.1"  # v2.1: fix JSON fallback double write

    def __init__(self):
        super().__init__()
        self._last_run_entries_processed: int = 0
        self._last_run_force_closed: int = 0
        self._total_entries: int = 0
        self._total_entries_loaded: bool = False
        self._last_daily_pnl: float = 0.0

    def _ensure_total_entries(self):
        """Lazy load total entries count from persistence on first access."""
        if not self._total_entries_loaded:
            try:
                entries = _load_journal_entries()
                self._total_entries = len(entries)
            except Exception:
                pass
            self._total_entries_loaded = True

    def run(self, **kwargs) -> dict:
        """Run the daily journal for Trader 4 positions.

        Steps:
        1. Load current meta positions from Trader 4
        2. Load existing journal entries for dedup
        3. Force-close expired positions (open > MAX_HOLD_HOURS without renewal)
        4. Process closed positions into journal entries with MAE/MFE
        5. Track team combination performance
        6. Save and publish results

        Returns dict with journal results.
        """
        self._set_status(AgentStatus.WORKING, "Running meta journal")

        start = time.monotonic()
        result = {
            "status": "ok",
            "new_entries": [],
            "force_closed": [],
            "team_combination_stats": {},
            "total_unrealized_pnl": 0.0,
            "total_realized_pnl": 0.0,
        }

        try:
            self._ensure_total_entries()

            # Step 1: Load positions and existing journal
            from .agent_trader_4 import _load_positions, _save_positions
            positions = _load_positions()
            existing_entries = _load_journal_entries()

            existing_keys = {
                (e.get("ticker"), e.get("entry_time"))
                for e in existing_entries
            }

            new_entries = []
            force_closed = []

            # Step 2: Force-close expired positions
            now = datetime.now(timezone.utc)
            for ticker, pos in list(positions.items()):
                elapsed = time.monotonic() - start
                if elapsed > GLOBAL_TIMEOUT_S:
                    self.log("Journal 4 global timeout reached",
                             {"elapsed_s": round(elapsed, 1)}, level="WARN")
                    break

                if pos.get("status") != "OPEN":
                    continue

                entry_time = pos.get("entry_time", "")
                try:
                    entry_dt = datetime.fromisoformat(
                        entry_time.replace("Z", "+00:00"))
                    age_hours = (now - entry_dt).total_seconds() / 3600
                except (ValueError, AttributeError, TypeError):
                    age_hours = 0

                if age_hours > MAX_HOLD_HOURS:
                    # Force close
                    close_result = self._force_close_position(
                        ticker, pos,
                        f"Expired — open {age_hours:.0f}h > {MAX_HOLD_HOURS}h max"
                    )
                    if close_result:
                        force_closed.append(close_result["entry"])
                        positions[ticker] = close_result["updated_position"]

            # Step 3: Process history entries into journal (new flips/closes)
            for ticker, pos in positions.items():
                elapsed = time.monotonic() - start
                if elapsed > GLOBAL_TIMEOUT_S:
                    break

                history = pos.get("history", [])
                for hist_entry in history:
                    entry_time = hist_entry.get("entry_time") or hist_entry.get("time")
                    if not entry_time:
                        continue
                    if (ticker, entry_time) in existing_keys:
                        continue

                    journal_entry = self._process_history_entry(ticker, hist_entry)
                    if journal_entry:
                        new_entries.append(journal_entry)
                        existing_keys.add((ticker, entry_time))

            # Step 4: Save force-closed entries + new entries
            all_new = force_closed + new_entries
            if all_new:
                try:
                    from ..database import is_pg_enabled
                    if is_pg_enabled():
                        # PG: ON CONFLICT DO NOTHING handles dedup safely
                        _pg_save_entries(all_new)
                    else:
                        # JSON: single atomic write with all entries combined
                        all_entries = existing_entries + all_new
                        cutoff = (now - timedelta(days=365)).isoformat()
                        all_entries = [
                            e for e in all_entries
                            if e.get("entry_time", "") > cutoff
                            or e.get("exit_time", "") > cutoff
                        ]
                        _save_entries_json(all_entries)
                except Exception:
                    # Last resort fallback
                    _save_entries_json(existing_entries + all_new)

            # Save updated positions (force-closed ones)
            if force_closed:
                _save_positions(positions)

            # Step 5: Compute team combination stats
            all_journal = all_new + existing_entries
            team_combo_stats = self._compute_team_combination_stats(all_journal)

            # Step 6: Compute totals
            total_unrealized = sum(
                p.get("unrealized_pnl_pct", 0)
                for p in positions.values() if p.get("status") == "OPEN"
            )
            total_realized = sum(
                p.get("realized_pnl_pct", 0) for p in positions.values()
            )

            result["new_entries"] = all_new
            result["force_closed"] = force_closed
            result["team_combination_stats"] = team_combo_stats
            result["total_unrealized_pnl"] = round(total_unrealized, 2)
            result["total_realized_pnl"] = round(total_realized, 2)

            self._last_run_entries_processed = len(all_new)
            self._last_run_force_closed = len(force_closed)
            self._total_entries += len(all_new)
            self._last_daily_pnl = total_unrealized

            # Step 7: Log
            for entry in force_closed:
                self.log_decision("META force-closed", {
                    "ticker": entry["ticker"],
                    "direction": entry.get("direction"),
                    "pnl_pct": entry.get("pnl_pct"),
                    "age_hours": entry.get("duration_hours"),
                    "reason": entry.get("reason"),
                })

            for entry in new_entries:
                self.log_decision("META trade journaled", {
                    "ticker": entry["ticker"],
                    "direction": entry.get("direction"),
                    "pnl_pct": entry.get("pnl_pct"),
                    "mae_pct": entry.get("mae_pct"),
                    "mfe_pct": entry.get("mfe_pct"),
                    "confluence_level": entry.get("confluence_level"),
                    "team_combination": entry.get("team_combination"),
                    "close_type": entry.get("close_type"),
                    "duration_category": entry.get("duration_category"),
                })

            self.log("Journal 4 run complete", {
                "new_entries": len(all_new),
                "force_closed": len(force_closed),
                "total_realized_pnl": round(total_realized, 2),
                "total_unrealized_pnl": round(total_unrealized, 2),
                "team_combos": team_combo_stats,
            })

            # Step 8: Publish for Learning 4
            self.publish("journal_4_complete", {
                "entries_processed": len(all_new),
                "force_closed": len(force_closed),
                "total_realized_pnl": round(total_realized, 2),
                "total_unrealized_pnl": round(total_unrealized, 2),
                "team_combination_stats": team_combo_stats,
                "timestamp": now.isoformat(),
            })

            self._set_status(
                AgentStatus.IDLE,
                f"{len(all_new)} entries, {len(force_closed)} force-closed"
            )

            return result

        except Exception as exc:
            self.log("Journal 4 run failed", {"error": str(exc)}, level="ERROR")
            self._set_status(AgentStatus.ERROR, str(exc))
            raise

    def _force_close_position(self, ticker: str, position: dict,
                              reason: str) -> dict | None:
        """Force-close an expired position and create a journal entry."""
        entry_price = position.get("entry_price")
        direction = position.get("direction", "NEUTRAL")

        if not entry_price or direction == "NEUTRAL":
            return None

        # Fetch current price
        from .agent_trader_4 import _fetch_current_price
        current_price = position.get("current_price") or _fetch_current_price(ticker)

        close_pnl = 0.0
        if entry_price > 0 and current_price:
            if direction == "LONG":
                close_pnl = round((current_price - entry_price) / entry_price * 100, 2)
            else:
                close_pnl = round((entry_price - current_price) / entry_price * 100, 2)

        entry_time = position.get("entry_time", "")
        now_iso = datetime.now(timezone.utc).isoformat()

        # Compute duration
        duration_hours = 0.0
        try:
            entry_dt = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
            duration_hours = round(
                (datetime.now(timezone.utc) - entry_dt).total_seconds() / 3600, 1
            )
        except (ValueError, AttributeError, TypeError):
            pass

        # MAE/MFE from daily bars
        mae_pct = None
        mfe_pct = None
        bar_count = 0
        if entry_time:
            try:
                start_date = entry_time[:10]
                end_date = now_iso[:10]
                bars = _fetch_daily_bars(ticker, start_date, end_date)
                if bars:
                    mae_pct, mfe_pct = _compute_mae_mfe(direction, entry_price, bars)
                    bar_count = len(bars)
            except Exception as exc:
                logger.warning("MAE/MFE fetch failed for %s: %s", ticker, exc)

        teams = _extract_team_contributions(position.get("source_details", {}))

        journal_entry = {
            "ticker": ticker,
            "direction": direction,
            "entry_price": entry_price,
            "exit_price": current_price,
            "entry_time": entry_time,
            "exit_time": now_iso,
            "pnl_pct": close_pnl,
            "mae_pct": mae_pct,
            "mfe_pct": mfe_pct,
            "bar_count": bar_count,
            "duration_hours": duration_hours,
            "duration_days": round(duration_hours / 24, 1) if duration_hours > 0 else 0.0,
            "duration_category": _classify_duration(duration_hours),
            "confluence_level": position.get("confluence_level", 0),
            "team_combination": _team_combination_key(teams),
            "teams_contributing": teams,
            "meta_score": position.get("last_meta_score", 0),
            "reason": reason,
            "close_type": "EXPIRED",
            "source_details": position.get("source_details", {}),
            "agent_versions": position.get("agent_versions", {}),
            "tp_pct": position.get("tp_pct"),
            "sl_pct": position.get("sl_pct"),
            "signal_renewal_count": position.get("signal_renewal_count", 0),
        }

        # Update position to CLOSED
        updated_position = {
            **position,
            "status": "CLOSED",
            "direction": "NEUTRAL",
            "unrealized_pnl_pct": 0.0,
            "realized_pnl_pct": round(
                position.get("realized_pnl_pct", 0.0) + close_pnl, 2
            ),
            "reasoning": reason,
        }

        # Record in history
        history = updated_position.get("history", [])
        history.insert(0, {
            "time": now_iso,
            "direction": direction,
            "entry_price": entry_price,
            "exit_price": current_price,
            "pnl_pct": close_pnl,
            "reason": reason,
            "close_type": "EXPIRED",
            "entry_time": entry_time,
            "confluence_level": position.get("confluence_level"),
        })
        updated_position["history"] = history[:100]

        return {
            "entry": journal_entry,
            "updated_position": updated_position,
        }

    def _process_history_entry(self, ticker: str,
                               hist_entry: dict) -> dict | None:
        """Process a single history entry into a journal entry with MAE/MFE."""
        entry_time = hist_entry.get("entry_time") or hist_entry.get("time")
        exit_time = hist_entry.get("time")
        entry_price = hist_entry.get("entry_price")
        exit_price = hist_entry.get("exit_price")
        direction = hist_entry.get("direction", "NEUTRAL")

        if not entry_price or direction == "NEUTRAL":
            return None

        # Duration
        duration_hours = 0.0
        try:
            entry_dt = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
            exit_dt = datetime.fromisoformat(exit_time.replace("Z", "+00:00"))
            duration_hours = round((exit_dt - entry_dt).total_seconds() / 3600, 1)
        except (ValueError, AttributeError, TypeError):
            pass

        # MAE/MFE
        mae_pct = None
        mfe_pct = None
        bar_count = 0
        if entry_time and exit_time:
            try:
                start_date = entry_time[:10]
                end_date = exit_time[:10]
                bars = _fetch_daily_bars(ticker, start_date, end_date)
                if bars:
                    mae_pct, mfe_pct = _compute_mae_mfe(direction, entry_price, bars)
                    bar_count = len(bars)
            except Exception as exc:
                logger.warning("MAE/MFE fetch failed for %s: %s", ticker, exc)

        teams = _extract_team_contributions(hist_entry.get("source_details", {}))
        close_type = hist_entry.get("close_type", "SIGNAL")

        return {
            "ticker": ticker,
            "direction": direction,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "entry_time": entry_time,
            "exit_time": exit_time,
            "pnl_pct": hist_entry.get("pnl_pct", 0.0),
            "mae_pct": mae_pct,
            "mfe_pct": mfe_pct,
            "bar_count": bar_count,
            "duration_hours": duration_hours,
            "duration_days": round(duration_hours / 24, 1) if duration_hours > 0 else 0.0,
            "duration_category": _classify_duration(duration_hours),
            "confluence_level": hist_entry.get("confluence_level", 0),
            "team_combination": _team_combination_key(teams),
            "teams_contributing": teams,
            "meta_score": hist_entry.get("meta_score", 0),
            "reason": hist_entry.get("reason", ""),
            "close_type": close_type,
            "source_details": hist_entry.get("source_details", {}),
            "agent_versions": hist_entry.get("agent_versions", {}),
            "tp_pct": hist_entry.get("tp_pct"),
            "sl_pct": hist_entry.get("sl_pct"),
            "signal_renewal_count": hist_entry.get("signal_renewal_count", 0),
        }

    def _compute_team_combination_stats(self, entries: list[dict]) -> dict:
        """Compute performance stats by team combination.

        Tracks which team combos (news+trend, news+tech, etc.) produce best results.
        L2: Includes Sharpe ratio per combination.
        """
        combo_stats: dict[str, dict] = {}

        for entry in entries:
            combo = entry.get("team_combination", "")
            if not combo:
                continue

            if combo not in combo_stats:
                combo_stats[combo] = {
                    "count": 0,
                    "wins": 0,
                    "total_pnl": 0.0,
                    "avg_confluence": 0.0,
                    "confluence_sum": 0,
                    "pnls": [],
                    "close_types": Counter(),
                    "duration_categories": Counter(),
                }

            stats = combo_stats[combo]
            stats["count"] += 1
            pnl = entry.get("pnl_pct", 0)
            stats["total_pnl"] += pnl
            stats["pnls"].append(pnl)
            if pnl > 0:
                stats["wins"] += 1
            stats["confluence_sum"] += entry.get("confluence_level", 0)
            close_type = entry.get("close_type", "SIGNAL")
            stats["close_types"][close_type] += 1
            dur_cat = entry.get("duration_category", "unknown")
            stats["duration_categories"][dur_cat] += 1

        # Finalize
        for combo, stats in combo_stats.items():
            n = stats["count"]
            if n > 0:
                stats["win_rate"] = round(stats["wins"] / n * 100, 1)
                stats["avg_pnl"] = round(stats["total_pnl"] / n, 2)
                stats["avg_confluence"] = round(stats["confluence_sum"] / n, 1)
                # L2: Sharpe ratio
                stats["sharpe"] = _compute_sharpe(stats["pnls"])
            else:
                stats["win_rate"] = 0.0
                stats["avg_pnl"] = 0.0
                stats["sharpe"] = None
            # Clean up internal fields
            del stats["confluence_sum"]
            stats["close_types"] = dict(stats["close_types"])
            stats["duration_categories"] = dict(stats["duration_categories"])
            del stats["pnls"]

        return combo_stats

    def compute_weekly_summary(self) -> dict:
        """L1: Compute weekly summary for Learning 4 validation cycle.

        Returns performance metrics for the past 7 days, segmented by
        team combination, confluence level, and duration category.
        """
        entries = _load_journal_entries()
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=7)).isoformat()

        weekly = [
            e for e in entries
            if (e.get("exit_time", "") or e.get("entry_time", "")) > cutoff
        ]

        if not weekly:
            return {
                "entries_count": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "avg_pnl": 0.0,
                "sharpe": None,
                "by_combo": {},
                "by_confluence": {},
                "by_duration": {},
                "by_close_type": {},
            }

        pnls = [e.get("pnl_pct", 0) for e in weekly]
        wins = sum(1 for p in pnls if p > 0)
        total_pnl = sum(pnls)

        # By combo
        by_combo = {}
        for e in weekly:
            combo = e.get("team_combination", "unknown")
            by_combo.setdefault(combo, {"count": 0, "wins": 0, "pnls": []})
            by_combo[combo]["count"] += 1
            p = e.get("pnl_pct", 0)
            by_combo[combo]["pnls"].append(p)
            if p > 0:
                by_combo[combo]["wins"] += 1
        for combo, stats in by_combo.items():
            n = stats["count"]
            stats["win_rate"] = round(stats["wins"] / n * 100, 1) if n else 0
            stats["avg_pnl"] = round(sum(stats["pnls"]) / n, 2) if n else 0
            stats["sharpe"] = _compute_sharpe(stats["pnls"])
            del stats["pnls"]

        # By confluence level
        by_confluence = {}
        for e in weekly:
            level = str(e.get("confluence_level", 0))
            by_confluence.setdefault(level, {"count": 0, "wins": 0, "total_pnl": 0})
            by_confluence[level]["count"] += 1
            p = e.get("pnl_pct", 0)
            by_confluence[level]["total_pnl"] += p
            if p > 0:
                by_confluence[level]["wins"] += 1
        for level, stats in by_confluence.items():
            n = stats["count"]
            stats["win_rate"] = round(stats["wins"] / n * 100, 1) if n else 0
            stats["avg_pnl"] = round(stats["total_pnl"] / n, 2) if n else 0

        # By duration category
        by_duration = {}
        for e in weekly:
            dur = e.get("duration_category", "unknown")
            by_duration.setdefault(dur, {"count": 0, "wins": 0, "total_pnl": 0})
            by_duration[dur]["count"] += 1
            p = e.get("pnl_pct", 0)
            by_duration[dur]["total_pnl"] += p
            if p > 0:
                by_duration[dur]["wins"] += 1
        for dur, stats in by_duration.items():
            n = stats["count"]
            stats["win_rate"] = round(stats["wins"] / n * 100, 1) if n else 0
            stats["avg_pnl"] = round(stats["total_pnl"] / n, 2) if n else 0

        # By close type
        by_close_type = Counter(e.get("close_type", "SIGNAL") for e in weekly)

        return {
            "entries_count": len(weekly),
            "win_rate": round(wins / len(weekly) * 100, 1),
            "total_pnl": round(total_pnl, 2),
            "avg_pnl": round(total_pnl / len(weekly), 2),
            "sharpe": _compute_sharpe(pnls),
            "by_combo": by_combo,
            "by_confluence": by_confluence,
            "by_duration": by_duration,
            "by_close_type": dict(by_close_type),
        }

    def get_entries(self) -> list[dict]:
        """Get all journal entries (for API/frontend)."""
        return _load_journal_entries()

    def get_metrics(self) -> dict:
        self._ensure_total_entries()
        return {
            "last_entries_processed": self._last_run_entries_processed,
            "last_force_closed": self._last_run_force_closed,
            "total_entries": self._total_entries,
            "last_daily_pnl": round(self._last_daily_pnl, 2),
        }
