"""FastAPI application — backend API for the news trading tool."""

import csv
import fcntl
import io
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .agents.registry import (
    get_all_status as get_agents_status,
    get_agent,
    invalidate_learning_cache,
    run_learning_update,
    run_learning_2_update,
    run_daily_journal as agents_run_journal,
    run_daily_journal_2 as agents_run_journal_2,
    run_event_check as agents_run_event_check,
    run_position_monitor as agents_run_position_monitor,
    run_position_monitor_3 as agents_run_position_monitor_3,
    run_position_monitor_4 as agents_run_position_monitor_4,
    run_scan_pipeline,
    run_weekly_review as agents_run_weekly_review,
)
from .backtest import run_backtest, run_parameter_sweep
from .config import SCAN_KEY_TO_TYPE, TRIGGER_COOLDOWN_SECONDS
from .database import is_pg_enabled, init_db, get_conn
from .economic_calendar import get_upcoming_events
from .journal import load_journal, run_daily_journal
from .learning import (
    compute_learning_adjustments,
    compute_performance,
    load_trades,
    update_trade_result,
)
from .models import ScanType, TradeResult
from .position_monitor import monitor_positions
from .scan_history import load_scan_history
from .scheduler import run_scan, run_event_check

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── (#34) Persist scan cache to disk ──────────────────────────
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
SCANS_CACHE_FILE = DATA_DIR / "last_scans.json"

_last_scans: dict[str, dict] = {}
_scans_lock = threading.Lock()

# (#35) Rate limiting for triggers
_last_trigger_times: dict[str, float] = {}
_trigger_lock = threading.Lock()

# M2: Prevent overlapping scans for the same scan_key
_running_scans: set = set()
_running_scans_lock = threading.Lock()

# O2: Mutex between recovery thread and journal scheduler
_journal_lock = threading.Lock()


def _load_scans_cache() -> dict[str, dict]:
    """Load last scan results (#34). Uses PostgreSQL when available.

    Fix 1: Validates that cached scans are from today (Paris time).
    Stale cache from a previous day is discarded to avoid showing yesterday's data.
    """
    raw = {}
    if is_pg_enabled():
        try:
            from .database import pg_load_last_scans
            raw = pg_load_last_scans()
        except Exception as exc:
            logger.warning("Failed to load scans cache from PostgreSQL: %s", exc)
            return {}
    else:
        try:
            if SCANS_CACHE_FILE.exists():
                # C2: Shared lock for consistent reads
                with open(SCANS_CACHE_FILE, "r") as f:
                    fcntl.flock(f, fcntl.LOCK_SH)
                    try:
                        raw = json.load(f)
                    finally:
                        fcntl.flock(f, fcntl.LOCK_UN)
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning("Failed to load scans cache: %s", exc)
            return {}

    # Fix 1: Discard stale cache from a previous trading day
    if raw:
        today_paris = datetime.now(PARIS_TZ).strftime("%Y-%m-%d")
        raw = _filter_todays_scans(raw, today_paris)
    return raw


def _filter_todays_scans(scans: dict, today: str) -> dict:
    """Fix 1: Keep only scans from today (Paris time). Discard yesterday's stale data.

    Checks the 'timestamp' field in each scan result. If no timestamp is found,
    the scan is kept (backward compat). If the timestamp is from a previous day,
    the scan is discarded and a log message is emitted.
    """
    filtered = {}
    for key, scan_data in scans.items():
        if not isinstance(scan_data, dict):
            continue
        ts = scan_data.get("timestamp")
        if ts:
            try:
                # Parse timestamp and convert to Paris date
                scan_dt = datetime.fromisoformat(str(ts))
                scan_date = scan_dt.astimezone(PARIS_TZ).strftime("%Y-%m-%d")
                if scan_date < today:
                    logger.info("Fix 1: Discarding stale scan '%s' from %s (today=%s)", key, scan_date, today)
                    continue
            except (ValueError, TypeError):
                pass  # Keep scans with unparseable timestamps
        filtered[key] = scan_data
    if len(filtered) < len(scans):
        logger.info("Fix 1: Discarded %d stale scan(s) from cache", len(scans) - len(filtered))
    return filtered


def _save_scans_cache(scans: dict[str, dict]) -> None:
    """Save scan results (#34). Uses PostgreSQL when available."""
    if is_pg_enabled():
        try:
            from .database import pg_save_all_last_scans
            pg_save_all_last_scans(scans)
            return
        except Exception as exc:
            logger.warning("Failed to save scans cache to PostgreSQL: %s", exc)
            return
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        # C2: File locking to prevent corruption from concurrent writes
        data = json.dumps(scans, indent=2, default=str)
        with open(SCANS_CACHE_FILE, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.write(data)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except Exception as exc:
        logger.warning("Failed to save scans cache: %s", exc)


bg_scheduler = BackgroundScheduler(timezone="Europe/Paris")

PARIS_TZ = ZoneInfo("Europe/Paris")


def _recover_pending_trades_on_startup() -> None:
    """Close ALL PENDING trades on startup — not just old ones.

    Fix 2: Previously only recovered trades from *previous* days (trade_date < today),
    which missed trades from today if the app crashed and restarted during trading hours.
    Now recovers ALL PENDING trades regardless of date, since the journal will correctly
    handle them (fetch real prices, compute TP/SL/EXPIRED).

    On Replit, the app can be killed at any time. If the 22:00 journal was missed,
    or the app crashed mid-day, trades stay PENDING forever without this recovery.
    """
    def _worker():
        try:
            trades = load_trades()
            all_pending = [t for t in trades if t.result == TradeResult.PENDING]

            if not all_pending:
                logger.info("Startup recovery: no PENDING trades found")
                return

            # Log details about what we're recovering
            now_paris = datetime.now(PARIS_TZ)
            today = now_paris.strftime("%Y-%m-%d")
            old_count = sum(
                1 for t in all_pending
                if t.timestamp.astimezone(PARIS_TZ).strftime("%Y-%m-%d") < today
            )
            today_count = len(all_pending) - old_count

            logger.info(
                "Startup recovery: found %d PENDING trade(s) (%d from previous days, %d from today) — running journal",
                len(all_pending), old_count, today_count,
            )
            if not _journal_lock.acquire(blocking=False):
                logger.warning("Startup recovery: journal lock held — skipping (journal already running)")
                return
            try:
                new_entries = run_daily_journal()
                logger.info(
                    "Startup recovery: journal created %d entries", len(new_entries),
                )
                # Invalidate learning cache so next scan uses fresh data
                try:
                    run_learning_update()
                    logger.info("Startup recovery: learning update completed")
                except Exception as exc:
                    logger.warning("Startup recovery: learning update failed: %s", exc)
                # Clear stale scan cache after recovery
                with _scans_lock:
                    global _last_scans
                    _last_scans = {}
                    _save_scans_cache(_last_scans)
                logger.info("Startup recovery: scan cache cleared")
            finally:
                _journal_lock.release()
        except Exception as exc:
            logger.error("Startup recovery failed: %s", exc)

    thread = threading.Thread(target=_worker, daemon=True, name="startup-recovery")
    thread.start()

    # V1: Also recover Teams 3/4 positions (check expired/stopped-out positions)
    def _worker_3_4():
        try:
            t3 = get_agent("trader_3")
            if t3 and hasattr(t3, "run_position_monitor"):
                result = t3.run_position_monitor()
                if result and result.get("closed", 0) > 0:
                    logger.info("Startup recovery Team 3: closed %d position(s)", result["closed"])
            t4 = get_agent("trader_4")
            if t4 and hasattr(t4, "run_position_monitor"):
                result = t4.run_position_monitor()
                if result and result.get("closed", 0) > 0:
                    logger.info("Startup recovery Team 4: closed %d position(s)", result["closed"])
        except Exception as exc:
            logger.error("Startup recovery Teams 3/4 failed: %s", exc)

    thread2 = threading.Thread(target=_worker_3_4, daemon=True, name="startup-recovery-3-4")
    thread2.start()


# ── Self-ping keepalive ──────────────────────────────────────
# Replit autoscale kills apps with no inbound traffic.
# This thread pings /api/health every 4 minutes to keep the process alive
# so that APScheduler jobs (scans at 07:50, 11:15, 14:50, 17:00) actually fire.
KEEPALIVE_INTERVAL = 240  # 4 minutes
_keepalive_stop = threading.Event()


def _keepalive_loop() -> None:
    """Ping our own health endpoint to prevent Replit from killing the app."""
    # Wait a bit for the server to be ready
    _keepalive_stop.wait(10)
    port = os.environ.get("PORT", "8000")
    url = f"http://127.0.0.1:{port}/api/health"
    while not _keepalive_stop.is_set():
        try:
            resp = requests.get(url, timeout=10)
            logger.debug("Keepalive ping: %d", resp.status_code)
        except Exception as exc:
            logger.debug("Keepalive ping failed: %s", exc)
        _keepalive_stop.wait(KEEPALIVE_INTERVAL)


def _get_existing_trade_tickers(exclude_key: str) -> list[str]:
    """Collect trade tickers from all other active scans for portfolio correlation.

    v3.5: Collects tickers from all recommendations (multi-trade per scan).
    """
    tickers = []
    with _scans_lock:
        for key, scan_data in _last_scans.items():
            if key == exclude_key:
                continue
            if not scan_data.get("has_trade"):
                continue
            # v3.5: collect from recommendations list first
            recs = scan_data.get("recommendations", [])
            if recs:
                for rec in recs:
                    ticker = rec.get("ticker") if isinstance(rec, dict) else None
                    if ticker:
                        tickers.append(ticker)
            elif scan_data.get("recommendation"):
                # Backward compat: single recommendation
                ticker = scan_data["recommendation"].get("ticker")
                if ticker:
                    tickers.append(ticker)
    return tickers


def _run_scheduled_scan(scan_key: str) -> None:
    """Run a scheduled scan in a background daemon thread.

    APScheduler's BackgroundScheduler runs jobs on its own threadpool.
    If a scan takes 2-3 minutes (collect → Claude API → select), it blocks
    that thread. On Replit this means the event loop can't serve HTTP
    (including health checks), so Replit kills the process.

    Solution: spawn a daemon thread and return immediately. The scheduler
    thread stays free, health checks keep responding, Replit stays happy.
    """
    # Night guard: never run scans outside trading hours (7h-20h CET)
    # Protects against APScheduler misfire edge cases on Replit restarts
    now_paris = datetime.now(PARIS_TZ)
    if now_paris.hour < 7 or now_paris.hour >= 20:
        logger.warning("Scheduled scan '%s' blocked — outside trading hours (%02d:%02d CET)",
                        scan_key, now_paris.hour, now_paris.minute)
        return
    if now_paris.weekday() >= 5:  # Saturday=5, Sunday=6
        logger.warning("Scheduled scan '%s' blocked — weekend", scan_key)
        return

    def _scan_worker():
        with _running_scans_lock:
            if scan_key in _running_scans:
                logger.warning("Scheduled scan '%s' already running — skipping", scan_key)
                return
            _running_scans.add(scan_key)
        try:
            scan_type_str = SCAN_KEY_TO_TYPE.get(scan_key, scan_key)
            scan_type = ScanType(scan_type_str)
            existing = _get_existing_trade_tickers(scan_key)
            result = run_scan(scan_type, existing_trade_ticker=existing or None)
            with _scans_lock:
                _last_scans[scan_key] = result
                _save_scans_cache(_last_scans)
        except Exception as exc:
            logger.error("Scheduled scan '%s' failed: %s", scan_key, exc)
        finally:
            with _running_scans_lock:
                _running_scans.discard(scan_key)

    thread = threading.Thread(target=_scan_worker, daemon=True, name=f"scan-{scan_key}")
    thread.start()
    logger.info("Scheduled scan '%s' dispatched to background thread", scan_key)


def _run_europe_scan() -> None:
    _run_scheduled_scan("europe")


def _run_mid_session_scan() -> None:
    _run_scheduled_scan("mid_session")


def _run_us_scan() -> None:
    _run_scheduled_scan("us")


def _run_us_session_scan() -> None:
    _run_scheduled_scan("us_session")


def _run_event_check() -> None:
    """Run event-driven scan check in background thread."""
    def _event_worker():
        event_key = "__event_check__"
        with _running_scans_lock:
            if event_key in _running_scans:
                logger.warning("Event check already running — skipping")
                return
            _running_scans.add(event_key)
        try:
            result = run_event_check()
            if result and result.get("has_trade"):
                scan_key = result.get("scan_type", "europe")
                with _scans_lock:
                    _last_scans[scan_key] = result
                    _save_scans_cache(_last_scans)
                logger.info("Event-driven scan produced trade(s) for %s", scan_key)
        except Exception as exc:
            logger.error("Event check failed: %s", exc)
        finally:
            with _running_scans_lock:
                _running_scans.discard(event_key)

    thread = threading.Thread(target=_event_worker, daemon=True, name="event-check")
    thread.start()


def _run_position_monitor() -> None:
    """Run position monitor via Agent Trader in background thread."""
    def _monitor_worker():
        try:
            result = agents_run_position_monitor()
            if result and result.get("actions_taken"):
                logger.info("Position monitor took %d action(s)",
                            len(result["actions_taken"]))
        except Exception as exc:
            logger.error("Position monitor failed: %s", exc)

    thread = threading.Thread(target=_monitor_worker, daemon=True, name="position-monitor")
    thread.start()


def _run_position_monitor_3(force_close_all: bool = False) -> None:
    """V1: Monitor Team 3 positions (TP/SL/trailing) between scans.

    Args:
        force_close_all: v3.0 — pass True at 19:50 CET to force-close all.
    """
    def _worker():
        try:
            result = agents_run_position_monitor_3(force_close_all=force_close_all)
            if result and result.get("closed", 0) > 0:
                logger.info("Team 3 position monitor closed %d position(s)%s",
                           result["closed"],
                           " (EOD force-close)" if force_close_all else "")
        except Exception as exc:
            logger.error("Team 3 position monitor failed: %s", exc)

    thread = threading.Thread(target=_worker, daemon=True, name="position-monitor-3")
    thread.start()


def _run_position_monitor_3_eod() -> None:
    """v3.0: Force-close all Team 3 positions at 19:50 CET."""
    _run_position_monitor_3(force_close_all=True)


def _run_position_monitor_4() -> None:
    """V1: Monitor Team 4 positions (TP/SL/trailing) between scans."""
    def _worker():
        try:
            result = agents_run_position_monitor_4()
            if result and result.get("closed", 0) > 0:
                logger.info("Team 4 position monitor closed %d position(s)", result["closed"])
        except Exception as exc:
            logger.error("Team 4 position monitor failed: %s", exc)

    thread = threading.Thread(target=_worker, daemon=True, name="position-monitor-4")
    thread.start()


def _run_post_eia_scan() -> None:
    """Run conditional post-EIA scan on Wednesdays at 16:45 CET.

    Uses scan_key "post_eia" (not "us_session") to avoid conflicting with the
    regular 17:00 us_session scan. Both map to ScanType.US for asset selection.
    """
    _run_scheduled_scan("post_eia")
    logger.info("Post-EIA conditional scan triggered (Wednesday 16:45 CET)")


def _run_daily_journal() -> None:
    """Run daily journal via Agent Journal in background thread."""
    def _journal_worker():
        global _last_scans
        if not _journal_lock.acquire(blocking=False):
            logger.warning("Daily journal: journal lock held — skipping (journal already running)")
            return
        try:
            agents_run_journal()
            # Reset trader daily counters for next trading day
            try:
                from .agents.registry import get_agent
                trader = get_agent("trader_1")
                if trader:
                    trader.reset_daily_counters()
                # P5: Also reset Trader 2 daily counters
                trader_2 = get_agent("trader_2")
                if trader_2:
                    trader_2.reset_daily_counters()
            except Exception as exc:
                logger.warning("Failed to reset daily counters: %s", exc)
            # Run Learning 1 update (after Journal 1)
            try:
                run_learning_update()
            except Exception as exc:
                logger.warning("Learning update after journal failed: %s", exc)
            # Run Journal 2 (trend positions) + Learning 2 update
            try:
                agents_run_journal_2()
                run_learning_2_update()
            except Exception as exc:
                logger.warning("Journal 2 / Learning 2 update failed: %s", exc)
            # Run Journal 3 (technical) + Learning 3 update
            try:
                from .agents.registry import (run_daily_journal_3,
                                              run_learning_3_update)
                run_daily_journal_3()
                run_learning_3_update()
            except Exception as exc:
                logger.warning("Journal 3 / Learning 3 update failed: %s", exc)
            # Run Journal 4 (meta) + Learning 4 update
            try:
                from .agents.registry import (run_daily_journal_4,
                                              run_learning_4_update)
                run_daily_journal_4()
                run_learning_4_update()
            except Exception as exc:
                logger.warning("Journal 4 / Learning 4 update failed: %s", exc)
            # I4 (v7.6): Single centralized cache invalidation — replaces per-team invalidations
            try:
                from .agents.registry import invalidate_all_learning_caches
                invalidate_all_learning_caches()
            except Exception as exc:
                logger.warning("Centralized learning cache invalidation failed: %s", exc)
            # Reset Trader 3 & 4 daily counters
            try:
                trader_3 = get_agent("trader_3")
                if trader_3 and hasattr(trader_3, "reset_daily_counters"):
                    trader_3.reset_daily_counters()
                trader_4 = get_agent("trader_4")
                if trader_4 and hasattr(trader_4, "reset_daily_counters"):
                    trader_4.reset_daily_counters()
            except Exception:
                pass
            # Clear scan cache after journal
            with _scans_lock:
                _last_scans = {}
                _save_scans_cache(_last_scans)
            logger.info("Scan cache cleared after daily journal")
        except Exception as exc:
            logger.error("Daily journal failed: %s", exc)
        finally:
            _journal_lock.release()

    thread = threading.Thread(target=_journal_worker, daemon=True, name="daily-journal")
    thread.start()


def _run_weekly_source_review() -> None:
    """Run weekly source health review via Agent News — Sunday 20:00 CET."""
    def _review_worker():
        try:
            agents_run_weekly_review()
        except Exception as exc:
            logger.error("Weekly source health review failed: %s", exc)

    thread = threading.Thread(target=_review_worker, daemon=True, name="weekly-source-review")
    thread.start()


def _run_performance_snapshot() -> None:
    """Run hourly performance snapshot — lightweight KPI collection."""
    def _worker():
        try:
            from .agents.registry import run_performance_snapshot
            run_performance_snapshot()
        except Exception as exc:
            logger.error("Performance snapshot failed: %s", exc)

    thread = threading.Thread(target=_worker, daemon=True, name="performance-snapshot")
    thread.start()


def _run_performance_daily() -> None:
    """Run daily performance report at 22h30 — after journal, full trade analysis."""
    def _worker():
        try:
            from .agents.registry import run_performance_daily
            run_performance_daily()
        except Exception as exc:
            logger.error("Performance daily report failed: %s", exc)

    thread = threading.Thread(target=_worker, daemon=True, name="performance-daily")
    thread.start()


def _run_team3_weekly_config() -> None:
    """Generate weekly strategy config for Team 3 — Sunday 20:30 CET.

    Runs after the weekly source review (20:00) and before performance weekly (21:30).
    Validates strategies for the coming week based on A/B results.
    """
    def _worker():
        try:
            from .agents.registry import generate_weekly_config_3
            config = generate_weekly_config_3()
            logger.info("Team 3 weekly config generated: %d enabled, %d validated",
                        len(config.get("enabled_strategies", [])),
                        len(config.get("validated_strategies", [])))
        except Exception as exc:
            logger.error("Team 3 weekly config generation failed: %s", exc)

    thread = threading.Thread(target=_worker, daemon=True, name="team3-weekly-config")
    thread.start()


def _run_team4_weekly_config() -> None:
    """Generate weekly strategy config for Team 4 — Sunday 20:45 CET.

    Runs after Team 3 weekly config (20:30) and before performance weekly (21:30).
    Validates team combinations, weights, and config for the coming week.
    """
    def _worker():
        try:
            from .agents.registry import generate_weekly_config_4
            config = generate_weekly_config_4()
            logger.info("Team 4 weekly config generated: v%d, %d validated combos",
                        config.get("config_version", 0),
                        len(config.get("validated_combos", [])))
        except Exception as exc:
            logger.error("Team 4 weekly config generation failed: %s", exc)

    thread = threading.Thread(target=_worker, daemon=True, name="team4-weekly-config")
    thread.start()


def _run_performance_weekly() -> None:
    """Run weekly performance trends — Sunday 21h30."""
    def _worker():
        try:
            from .agents.registry import run_performance_weekly
            run_performance_weekly()
        except Exception as exc:
            logger.error("Performance weekly trends failed: %s", exc)

    thread = threading.Thread(target=_worker, daemon=True, name="performance-weekly")
    thread.start()


def _run_infra_health_check() -> None:
    """Run infrastructure health check every 15 min (weekdays only)."""
    def _worker():
        try:
            from .agents.registry import run_infra_health_check
            run_infra_health_check()
        except Exception as exc:
            logger.error("Infra health check failed: %s", exc)

    thread = threading.Thread(target=_worker, daemon=True, name="infra-health-check")
    thread.start()


def _run_infra_maintenance() -> None:
    """Run infrastructure maintenance at 23h — VACUUM, pruning, stats."""
    def _worker():
        try:
            from .agents.registry import run_infra_maintenance
            run_infra_maintenance()
        except Exception as exc:
            logger.error("Infra maintenance failed: %s", exc)

    thread = threading.Thread(target=_worker, daemon=True, name="infra-maintenance")
    thread.start()


def _run_infra_report() -> None:
    """Run weekly infrastructure report — Sunday 21h."""
    def _worker():
        try:
            from .agents.registry import run_infra_report
            run_infra_report()
        except Exception as exc:
            logger.error("Infra report failed: %s", exc)

    thread = threading.Thread(target=_worker, daemon=True, name="infra-report")
    thread.start()


_CLEANUP_SENTINEL = Path(os.getenv("DATA_DIR", "data")) / ".cleanup_done_2026_03_09c"

_ALL_PG_TABLES = [
    "trades", "journal_entries", "scan_history", "last_scans",
    "price_archive",
    "trend_positions", "trend_journal_entries",
    "tech_positions", "tech_journal_entries",
    "meta_positions", "meta_journal_entries",
    "agent_messages", "agent_logs", "audit_reports",
    "performance_data", "source_health",
]

_ALL_JSON_FILES = [
    "trades.json", "journal.json", "scan_history.json", "last_scans.json",
    "trend_positions.json", "trend_journal.json",
    "tech_positions.json", "tech_journal.json",
    "meta_positions.json", "meta_journal.json",
    "audit_reports.json", "source_health.json",
]

_LEARNING_CONFIG_FILES = [
    "learning3_config_history.json", "learning4_config_history.json",
    "learning3_weekly_config.json", "learning4_weekly_config.json",
]


def _truncate_pg_tables() -> dict:
    """Truncate all PG tables. Returns per-table results dict."""
    results = {}
    if not is_pg_enabled():
        return results
    for table in _ALL_PG_TABLES:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    # Check table exists first — TRUNCATE does NOT support IF EXISTS in PG
                    cur.execute(
                        "SELECT 1 FROM information_schema.tables "
                        "WHERE table_schema = 'public' AND table_name = %s",
                        (table,),
                    )
                    if cur.fetchone():
                        cur.execute(f"TRUNCATE TABLE {table} CASCADE")
                        results[table] = "truncated"
                    else:
                        results[table] = "not_found"
                conn.commit()
            logger.info("  PG table %s: %s", table, results[table])
        except Exception as exc:
            results[table] = f"error: {exc}"
            logger.warning("  PG table %s: error: %s", table, exc)
    return results


def _reset_json_files() -> tuple[dict, bool]:
    """Reset all JSON files to empty. Returns (results_dict, all_ok)."""
    data_dir = Path(os.getenv("DATA_DIR", "data"))
    results = {}
    all_ok = True
    for fname in _ALL_JSON_FILES:
        fpath = data_dir / fname
        try:
            # Positions files are dicts, not arrays — reset to correct format
            if fname in ("last_scans.json", "trend_positions.json", "meta_positions.json"):
                empty = "{}"
            elif fname == "tech_positions.json":
                empty = '{"active": [], "closed": []}'
            else:
                empty = "[]"
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(empty)
            results[fname] = "reset"
            logger.info("  JSON file %s: reset", fname)
        except Exception as exc:
            all_ok = False
            results[fname] = f"error: {exc}"
            logger.error("  JSON file %s: error: %s", fname, exc)
    return results, all_ok


def _delete_learning_configs() -> dict:
    """Delete learning weekly config files. Returns results dict."""
    data_dir = Path(os.getenv("DATA_DIR", "data"))
    results = {}
    for fname in _LEARNING_CONFIG_FILES:
        fpath = data_dir / fname
        try:
            if fpath.exists():
                fpath.unlink()
                results[fname] = "deleted"
                logger.info("  Learning config %s: deleted", fname)
            else:
                results[fname] = "not_found"
        except Exception as exc:
            results[fname] = f"error: {exc}"
            logger.error("  Learning config %s: error: %s", fname, exc)
    return results


def _clear_agent_memory() -> dict:
    """Clear in-memory caches and state of all agents."""
    results = {}
    try:
        all_agents = get_all_agents()
    except Exception:
        return {"error": "could not get agents"}

    for name, agent in all_agents.items():
        cleared = []
        try:
            # Learning agents: invalidate caches
            if hasattr(agent, "_cached_adjustments"):
                agent._cached_adjustments = None
                agent._cache_valid = False
                cleared.append("cached_adjustments")
            if hasattr(agent, "_last_anomalies") and isinstance(getattr(agent, "_last_anomalies", None), list):
                agent._last_anomalies = []
                cleared.append("anomalies")
            if hasattr(agent, "_total_recalculations"):
                agent._total_recalculations = 0
                cleared.append("recalculations")
            if hasattr(agent, "_last_run_time"):
                agent._last_run_time = None
                cleared.append("last_run_time")

            # Trader agents: reset counters and cached data
            for attr in ["_trades_today", "_trades_total", "_rejections_today",
                         "_evaluations_today", "_position_changes_total",
                         "_trades_opened_total", "_trades_closed_total",
                         "_position_opens_total", "_position_closes_total",
                         "_pending_positions"]:
                if hasattr(agent, attr):
                    setattr(agent, attr, 0)
                    cleared.append(attr)
            for attr in ["_last_trade_ticker", "_last_trade_direction",
                         "_last_change_ticker", "_last_change_direction",
                         "_last_action_ticker", "_last_action_direction"]:
                if hasattr(agent, attr):
                    setattr(agent, attr, None)
                    cleared.append(attr)
            # Trader cached learning / scoring / weekly config
            if hasattr(agent, "_current_learning"):
                agent._current_learning = {}
                cleared.append("current_learning")
            if hasattr(agent, "_current_trend_scoring"):
                agent._current_trend_scoring = {}
                cleared.append("current_trend_scoring")
            if hasattr(agent, "_weekly_config"):
                agent._weekly_config = None
                cleared.append("weekly_config")

            # Journal agents: reset run stats
            for attr in ["_last_run_trades_closed", "_last_run_tp", "_last_run_sl",
                         "_last_run_expired", "_total_entries"]:
                if hasattr(agent, attr):
                    setattr(agent, attr, 0)
                    cleared.append(attr)
            if hasattr(agent, "_last_pnl_sum"):
                agent._last_pnl_sum = 0.0
                cleared.append("last_pnl_sum")

            # Scoring agents: reset counters and cached results
            for attr in ["_last_scored_count", "_last_zero_edge_filtered",
                         "_total_scored", "_total_tokens_used", "_cache_hits"]:
                if hasattr(agent, attr):
                    setattr(agent, attr, 0)
                    cleared.append(attr)
            if hasattr(agent, "_last_result"):
                agent._last_result = None
                cleared.append("last_result")

            # News agent
            for attr in ["_last_collect_count", "_last_source_errors",
                         "_total_collected", "_total_filtered_dedup"]:
                if hasattr(agent, attr):
                    setattr(agent, attr, 0)
                    cleared.append(attr)

            # Performance agent: clear history
            if hasattr(agent, "_snapshots") and isinstance(getattr(agent, "_snapshots", None), list):
                agent._snapshots = []
                cleared.append("snapshots")
            if hasattr(agent, "_daily_reports") and isinstance(getattr(agent, "_daily_reports", None), list):
                agent._daily_reports = []
                cleared.append("daily_reports")
            if hasattr(agent, "_last_report"):
                agent._last_report = None
                cleared.append("last_report")
            for attr in ["_total_snapshots", "_total_reports"]:
                if hasattr(agent, attr):
                    setattr(agent, attr, 0)
                    cleared.append(attr)

            if cleared:
                results[name] = cleared
        except Exception as exc:
            results[name] = f"error: {exc}"

    # Clear MessageBus in-memory messages
    try:
        from .agents.base import MessageBus
        bus = MessageBus()
        if hasattr(bus, "_memory_messages"):
            bus._memory_messages.clear()
            results["message_bus"] = ["memory_messages"]
    except Exception as exc:
        results["message_bus"] = f"error: {exc}"

    logger.info("  Cleared agent memory: %s", results)
    return results


def _one_time_cleanup() -> None:
    """One-time database cleanup for fresh start after v8.3 bugfix sprint.

    Clears all trades, journals, scan history, positions, learning data
    from both PG and JSON. Uses a sentinel file so it only runs once.
    """
    if _CLEANUP_SENTINEL.exists():
        logger.info("One-time cleanup: sentinel found (%s), skipping", _CLEANUP_SENTINEL.name)
        return

    logger.info("=" * 60)
    logger.info("ONE-TIME CLEANUP — Resetting all trading data for fresh start")
    logger.info("=" * 60)

    # 1. PG tables
    _truncate_pg_tables()

    # 2. JSON files
    _, json_ok = _reset_json_files()

    # 3. Learning configs
    _delete_learning_configs()

    # 4. Agent in-memory state
    _clear_agent_memory()

    # 5. Write sentinel ONLY if JSON cleanup succeeded
    if json_ok:
        _CLEANUP_SENTINEL.parent.mkdir(parents=True, exist_ok=True)
        _CLEANUP_SENTINEL.write_text(f"Cleanup performed at {datetime.now(timezone.utc).isoformat()}\n")
        logger.info("=" * 60)
        logger.info("ONE-TIME CLEANUP COMPLETE — Fresh start ready")
        logger.info("=" * 60)
    else:
        logger.warning("=" * 60)
        logger.warning("ONE-TIME CLEANUP INCOMPLETE — json_ok=%s — will retry on next startup", json_ok)
        logger.warning("=" * 60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _last_scans
    # Initialize PostgreSQL tables if DATABASE_URL is set
    init_db()
    # One-time cleanup for fresh start (runs only once, uses sentinel file)
    _one_time_cleanup()
    # (#34) Load cached scans on startup
    with _scans_lock:
        _last_scans = _load_scans_cache()
        if _last_scans:
            logger.info("Restored %d cached scan results", len(_last_scans))

    # Schedule scans: 4 scans/day, weekdays only (markets closed on weekends)
    # misfire_grace_time=60 (1 min) — short grace period prevents crash loops:
    # if the app crashes mid-scan, a 10 min window would re-trigger the scan
    # immediately on restart, causing another crash. 60s is enough for normal
    # cold-start delays without re-triggering after a crash.
    # misfire_grace_time=300 (5 min) — Replit may sleep the container and wake it
    # after the scheduled time. A 5-minute window ensures the scan still runs after
    # a brief sleep, without risk of crash loops (night guard blocks >20h CET anyway).
    bg_scheduler.add_job(_run_europe_scan, CronTrigger(hour=7, minute=50, day_of_week="mon-fri", timezone="Europe/Paris"), id="europe_scan", misfire_grace_time=300)
    bg_scheduler.add_job(_run_mid_session_scan, CronTrigger(hour=11, minute=15, day_of_week="mon-fri", timezone="Europe/Paris"), id="mid_session_scan", misfire_grace_time=300)
    bg_scheduler.add_job(_run_us_scan, CronTrigger(hour=14, minute=50, day_of_week="mon-fri", timezone="Europe/Paris"), id="us_scan", misfire_grace_time=300)
    bg_scheduler.add_job(_run_us_session_scan, CronTrigger(hour=17, minute=0, day_of_week="mon-fri", timezone="Europe/Paris"), id="us_session_scan", misfire_grace_time=300)
    # Daily journal at 22:00 CET — auto-close trades + generate journal, weekdays only
    # misfire_grace_time=3600 (1h) — journal is pure data processing (no external API calls),
    # so crash loops are not a concern. A long grace period ensures the journal runs even
    # after prolonged downtime (e.g., Replit kills the app from 21:00 to 23:30).
    bg_scheduler.add_job(_run_daily_journal, CronTrigger(hour=22, minute=0, day_of_week="mon-fri", timezone="Europe/Paris"), id="daily_journal", misfire_grace_time=3600)
    # P2-5: Event-driven scanner: every 10 min during trading hours (was 15min)
    # Faster detection = less edge lost waiting for next check
    bg_scheduler.add_job(_run_event_check, CronTrigger(minute="*/10", hour="7-19", day_of_week="mon-fri", timezone="Europe/Paris"), id="event_check", misfire_grace_time=60)
    # Position monitor: every 30 min during trading hours — trailing stop + time stop
    # Reduced from 15min to ease PG pool pressure (3 monitors × 4/hr = 12 conn/hr was too much)
    bg_scheduler.add_job(_run_position_monitor, CronTrigger(minute="7,37", hour="7-19", day_of_week="mon-fri", timezone="Europe/Paris"), id="position_monitor", misfire_grace_time=60)
    # V1: Position monitors for Teams 3 and 4 (TP/SL/trailing between scans)
    # Staggered by 3min to avoid resource contention
    # v3.0: Monitor every 15min (was 30min) for intraday positions (holdings 1-5h)
    bg_scheduler.add_job(_run_position_monitor_3, CronTrigger(minute="5,20,35,50", hour="7-19", day_of_week="mon-fri", timezone="Europe/Paris"), id="position_monitor_3", misfire_grace_time=60)
    # v3.0: Force-close all Team 3 positions at 19:50 CET (hard deadline safety net)
    bg_scheduler.add_job(_run_position_monitor_3_eod, CronTrigger(hour=19, minute=50, day_of_week="mon-fri", timezone="Europe/Paris"), id="position_monitor_3_eod", misfire_grace_time=300)
    bg_scheduler.add_job(_run_position_monitor_4, CronTrigger(minute="13,43", hour="7-19", day_of_week="mon-fri", timezone="Europe/Paris"), id="position_monitor_4", misfire_grace_time=60)
    # Conditional post-EIA scan: Wednesday 16:45 CET (EIA petroleum report at 16:30)
    bg_scheduler.add_job(_run_post_eia_scan, CronTrigger(hour=16, minute=45, day_of_week="wed", timezone="Europe/Paris"), id="post_eia_scan", misfire_grace_time=60)
    # v5.2: Weekly source health review — Sunday 20:00 CET (before Monday trading)
    # misfire_grace_time=3600: safe to run late, pure data analysis
    bg_scheduler.add_job(_run_weekly_source_review, CronTrigger(hour=20, minute=0, day_of_week="sun", timezone="Europe/Paris"), id="weekly_source_review", misfire_grace_time=3600)
    # v2.0: Team 3 weekly strategy config — Sunday 20:30 CET (after source review, before Monday)
    bg_scheduler.add_job(_run_team3_weekly_config, CronTrigger(hour=20, minute=30, day_of_week="sun", timezone="Europe/Paris"), id="team3_weekly_config", misfire_grace_time=3600)
    # v2.0: Team 4 weekly strategy config — Sunday 20:45 CET (after Team 3, before Monday)
    bg_scheduler.add_job(_run_team4_weekly_config, CronTrigger(hour=20, minute=45, day_of_week="sun", timezone="Europe/Paris"), id="team4_weekly_config", misfire_grace_time=3600)
    # v7.5: Infrastructure health check every 15 min, maintenance daily at 23h, report weekly Sun 21h
    bg_scheduler.add_job(_run_infra_health_check, CronTrigger(minute="*/15", hour="7-22", day_of_week="mon-fri", timezone="Europe/Paris"), id="infra_health_check", misfire_grace_time=60)
    bg_scheduler.add_job(_run_infra_maintenance, CronTrigger(hour=23, minute=0, day_of_week="mon-fri", timezone="Europe/Paris"), id="infra_maintenance", misfire_grace_time=3600)
    bg_scheduler.add_job(_run_infra_report, CronTrigger(hour=21, minute=0, day_of_week="sun", timezone="Europe/Paris"), id="infra_report", misfire_grace_time=3600)
    # v8.0: Performance agent — hourly snapshot, daily report 22h30, weekly trends Sun 21h30
    bg_scheduler.add_job(_run_performance_snapshot, CronTrigger(minute=0, hour="7-22", day_of_week="mon-fri", timezone="Europe/Paris"), id="performance_snapshot", misfire_grace_time=300)
    bg_scheduler.add_job(_run_performance_daily, CronTrigger(hour=22, minute=30, day_of_week="mon-fri", timezone="Europe/Paris"), id="performance_daily", misfire_grace_time=3600)
    bg_scheduler.add_job(_run_performance_weekly, CronTrigger(hour=21, minute=30, day_of_week="sun", timezone="Europe/Paris"), id="performance_weekly", misfire_grace_time=3600)
    bg_scheduler.start()
    logger.info("Scheduler started — scans at 07:50, 11:15, 14:50, 17:00, event check q10min, position monitor q15min, post-EIA Wed 16:45, journal at 22:00 CET (weekdays), source review Sun 20:00")

    # Startup recovery: close any old PENDING trades that were missed by the 22:00 journal
    # (e.g., app was down overnight, Replit killed the process before journal ran)
    _recover_pending_trades_on_startup()

    # Seed price references for anomaly detection (background, non-blocking)
    def _seed_prices():
        try:
            from .market_data import seed_price_references
            seed_price_references()
        except Exception as exc:
            logger.warning("Price reference seeding failed: %s", exc)
    threading.Thread(target=_seed_prices, daemon=True, name="price-seed").start()

    # Start keepalive thread to prevent Replit autoscale from killing the app
    keepalive_thread = threading.Thread(target=_keepalive_loop, daemon=True, name="keepalive")
    keepalive_thread.start()
    logger.info("Keepalive thread started (ping every %ds)", KEEPALIVE_INTERVAL)

    yield

    _keepalive_stop.set()
    bg_scheduler.shutdown()
    # Close PostgreSQL connection pool if active
    try:
        from .database import close_pool
        close_pool()
    except Exception:
        pass


app = FastAPI(title="OneShot News Trading", version="2.0.0", lifespan=lifespan)

# (#36) CORS restriction — localhost + Replit
ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:3000",
]
# Add Replit domains if REPL_SLUG is set
repl_slug = os.environ.get("REPL_SLUG")
repl_owner = os.environ.get("REPL_OWNER")
if repl_slug and repl_owner:
    ALLOWED_ORIGINS.append(f"https://{repl_slug}.{repl_owner}.repl.co")
    ALLOWED_ORIGINS.append(f"https://{repl_slug}-{repl_owner}.repl.co")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS if repl_slug else ["*"],  # Keep * for local dev
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Root endpoint — serves frontend in prod, health check in dev ──
FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


@app.get("/")
def root():
    """Root endpoint — doubles as Replit Autoscale health check (returns 200).

    In production: serves index.html (frontend build exists).
    In dev: returns JSON status (Vite dev server handles the frontend).
    Either way, Cloud Run gets the 200 it needs.
    """
    index = FRONTEND_DIST / "index.html"
    if index.is_file():
        return FileResponse(index)
    return {"status": "ok"}


# ── API Endpoints ────────────────────────────────────────────────


@app.get("/api/scan/latest")
def get_latest_scans():
    """Get the most recent scan results (europe + us).

    Fix 1: Filters out stale scans from previous days at read time,
    so the dashboard never shows yesterday's data even if the cache wasn't cleared.
    """
    global _last_scans
    today_paris = datetime.now(PARIS_TZ).strftime("%Y-%m-%d")
    with _scans_lock:
        filtered = _filter_todays_scans(_last_scans, today_paris)
        if len(filtered) < len(_last_scans):
            # Auto-clean the cache in memory too
            _last_scans = filtered
        return dict(filtered)


@app.get("/api/scan/latest/{scan_type}")
def get_latest_scan(scan_type: str):
    """Get the latest result for a specific scan type."""
    with _scans_lock:
        return _last_scans.get(scan_type, {"has_trade": False, "reason_no_trade": "Aucun scan effectué"})


def _run_triggered_scan(scan_type: str, st: ScanType, existing_ticker: list[str] | str | None) -> None:
    """Run a manually-triggered scan in a background thread.

    Results are stored in _last_scans and persisted to disk,
    available via GET /api/scan/latest/{scan_type}.
    """
    try:
        result = run_scan(st, existing_trade_ticker=existing_ticker)
        with _scans_lock:
            _last_scans[scan_type] = result
            _save_scans_cache(_last_scans)
        logger.info("Background scan %s completed (trade=%s)", scan_type, result.get("has_trade"))
    except Exception as exc:
        logger.error("Background scan %s failed: %s", scan_type, exc)
        with _scans_lock:
            _last_scans[scan_type] = {
                "scan_type": scan_type,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "has_trade": False,
                "reason_no_trade": f"Scan échoué : {exc}",
                "news_analyzed": 0,
            }
            _save_scans_cache(_last_scans)


@app.post("/api/scan/trigger/{scan_type}")
def trigger_scan(scan_type: str):
    """Manually trigger a scan (with rate-limiting #35).

    The scan runs in a background thread and returns immediately.
    Poll GET /api/scan/latest/{scan_type} for results.
    """
    # Weekend guard — markets closed
    now_paris = datetime.now(PARIS_TZ)
    if now_paris.weekday() >= 5:  # 5=Saturday, 6=Sunday
        raise HTTPException(
            status_code=400,
            detail="Marchés fermés le week-end. Scan disponible du lundi au vendredi.",
        )

    # (#35) Rate limiting
    now = time.time()
    with _trigger_lock:
        last_trigger = _last_trigger_times.get(scan_type, 0)
        if now - last_trigger < TRIGGER_COOLDOWN_SECONDS:
            remaining = int(TRIGGER_COOLDOWN_SECONDS - (now - last_trigger))
            raise HTTPException(
                status_code=429,
                detail=f"Cooldown actif. Reessayez dans {remaining}s.",
            )
        _last_trigger_times[scan_type] = now

    # Map scan key to ScanType (supports europe, mid_session, us, us_session)
    if scan_type not in SCAN_KEY_TO_TYPE:
        raise HTTPException(status_code=400, detail=f"Type de scan invalide: {scan_type}. Valeurs acceptees: {', '.join(SCAN_KEY_TO_TYPE.keys())}")
    st = ScanType(SCAN_KEY_TO_TYPE[scan_type])

    # Portfolio-level correlation: collect all existing trade tickers from other scans
    existing_tickers = _get_existing_trade_tickers(scan_type)
    existing_ticker = existing_tickers or None

    # Run scan in background thread — return immediately to avoid HTTP timeout
    thread = threading.Thread(
        target=_run_triggered_scan,
        args=(scan_type, st, existing_ticker),
        daemon=True,
    )
    thread.start()

    return {"status": "scan_started", "scan_type": scan_type, "message": "Scan lance en arriere-plan. Consultez GET /api/scan/latest pour les resultats (v3.5: multi-trade)."}


@app.get("/api/trades")
def get_trades():
    """Get all historical trades."""
    try:
        trades = load_trades()
        return [t.model_dump(mode="json") for t in trades]
    except Exception as exc:
        logger.error("GET /api/trades failed: %s", exc)
        return []


@app.get("/api/trades/pending")
def get_pending_trades():
    """Get trades awaiting resolution."""
    try:
        trades = load_trades()
        return [t.model_dump(mode="json") for t in trades if t.result == TradeResult.PENDING]
    except Exception as exc:
        logger.error("GET /api/trades/pending failed: %s", exc)
        return []


@app.post("/api/trades/close")
def close_trade(ticker: str, timestamp: str, result: str, exit_price: float):
    """Manually close a trade with its outcome."""
    ts = datetime.fromisoformat(timestamp)
    tr = TradeResult(result)
    update_trade_result(ts, ticker, tr, exit_price)
    return {"status": "ok"}


@app.get("/api/performance")
def get_performance():
    """Get aggregated performance statistics."""
    return compute_performance().model_dump()


@app.get("/api/learning")
def get_learning():
    """Get current learning adjustments per ticker."""
    return compute_learning_adjustments()


@app.get("/api/newscat-performance/{team}")
def get_newscat_performance(team: int = 1):
    """Get per-newscat combo performance stats (zone+intensity+source+ticker).

    Returns aggregated WR/PnL/count for each newscat combination,
    plus the individual trades for drill-down.
    Team 1 = day trading, Team 2 = trend following.
    """
    combos: dict[str, dict] = {}

    if team == 1:
        trades = load_trades()
        closed = [t for t in trades
                  if t.result != TradeResult.PENDING and t.pnl_pct is not None]
        for t in closed:
            nc = t.news_category or "other"
            zone = getattr(t, "news_zone", "") or ""
            mag = getattr(t, "expected_magnitude", None)
            intensity = ""
            if mag is not None:
                if mag <= 33:
                    intensity = "low"
                elif mag >= 67:
                    intensity = "high"
            sources = getattr(t, "news_sources", []) or []
            source_str = sources[0] if sources else "unknown"

            # Build combo key: category+zone+intensity+ticker
            parts = [nc]
            if zone:
                parts.append(zone)
            if intensity:
                parts.append(intensity)
            parts.append(t.ticker)
            combo_key = "+".join(parts)

            if combo_key not in combos:
                combos[combo_key] = {
                    "key": combo_key, "category": nc, "zone": zone,
                    "intensity": intensity, "ticker": t.ticker,
                    "wins": 0, "losses": 0, "expired": 0,
                    "pnls": [], "sources": {}, "trades": [],
                }
            c = combos[combo_key]
            if t.result == TradeResult.TP_HIT:
                c["wins"] += 1
            elif t.result == TradeResult.SL_HIT:
                c["losses"] += 1
            else:
                c["expired"] += 1
            c["pnls"].append(t.pnl_pct)
            c["sources"][source_str] = c["sources"].get(source_str, 0) + 1
            c["trades"].append({
                "timestamp": t.timestamp.isoformat(),
                "ticker": t.ticker,
                "direction": t.direction.value if hasattr(t.direction, "value") else str(t.direction),
                "result": t.result.value if hasattr(t.result, "value") else str(t.result),
                "pnl_pct": t.pnl_pct,
                "news_headline": t.news_headline,
                "source": source_str,
                "zone": zone,
                "intensity": intensity,
                "magnitude": mag,
            })

    elif team == 2:
        agent = get_agent("trader_2")
        if agent:
            positions = agent._load_positions()
            for ticker, pos in positions.items():
                for h in pos.get("history", []):
                    pnl = h.get("pnl_pct", 0)
                    if pnl is None:
                        continue
                    zones = h.get("news_zones", [])
                    zone = zones[0] if zones else ""
                    intensity_val = h.get("intensity", "")
                    cats = []
                    for n in h.get("key_news", []):
                        cat = n.get("category", "other")
                        if cat not in cats:
                            cats.append(cat)
                    primary_cat = cats[0] if cats else "other"
                    source_str = "trend_signal"
                    for n in h.get("key_news", []):
                        if n.get("source"):
                            source_str = n["source"]
                            break

                    parts = [primary_cat]
                    if zone:
                        parts.append(zone)
                    if intensity_val:
                        parts.append(intensity_val)
                    parts.append(ticker)
                    combo_key = "+".join(parts)

                    if combo_key not in combos:
                        combos[combo_key] = {
                            "key": combo_key, "category": primary_cat, "zone": zone,
                            "intensity": intensity_val, "ticker": ticker,
                            "wins": 0, "losses": 0, "expired": 0,
                            "pnls": [], "sources": {}, "trades": [],
                        }
                    c = combos[combo_key]
                    if pnl > 0:
                        c["wins"] += 1
                    else:
                        c["losses"] += 1
                    c["pnls"].append(pnl)
                    c["sources"][source_str] = c["sources"].get(source_str, 0) + 1
                    c["trades"].append({
                        "timestamp": h.get("time", ""),
                        "ticker": ticker,
                        "direction": h.get("to_direction", ""),
                        "result": "WIN" if pnl > 0 else "LOSS",
                        "pnl_pct": pnl,
                        "news_headline": h.get("reason", ""),
                        "source": source_str,
                        "zone": zone,
                        "intensity": intensity_val,
                        "magnitude": None,
                    })
    else:
        raise HTTPException(400, f"Team {team} not supported for newscat performance")

    # Compute stats
    result = []
    for c in combos.values():
        total = c["wins"] + c["losses"] + c["expired"]
        wr = c["wins"] / total * 100 if total > 0 else 0
        avg_pnl = sum(c["pnls"]) / len(c["pnls"]) if c["pnls"] else 0
        result.append({
            "key": c["key"],
            "category": c["category"],
            "zone": c["zone"],
            "intensity": c["intensity"],
            "ticker": c["ticker"],
            "total": total,
            "wins": c["wins"],
            "losses": c["losses"],
            "expired": c["expired"],
            "win_rate": round(wr, 1),
            "avg_pnl": round(avg_pnl, 3),
            "total_pnl": round(sum(c["pnls"]), 3),
            "sources": c["sources"],
            "trades": sorted(c["trades"], key=lambda x: x["timestamp"], reverse=True),
        })

    # Sort by total trades desc
    result.sort(key=lambda x: x["total"], reverse=True)
    return result


@app.get("/api/journal")
def get_journal():
    """Get all journal entries."""
    entries = load_journal()
    logger.info("GET /api/journal: returning %d entries (PG=%s)", len(entries), is_pg_enabled())
    return [e.model_dump(mode="json") for e in entries]


@app.get("/api/journal/debug")
def get_journal_debug():
    """Diagnostic endpoint: raw journal state from PG/JSON for debugging."""
    from .journal import JOURNAL_FILE
    import json as _json

    diag = {"pg_enabled": is_pg_enabled(), "pg_raw_count": 0, "pg_parse_errors": [],
            "json_raw_count": 0, "json_parse_errors": [], "final_count": 0,
            "pending_trades": 0, "all_trades": 0}

    all_trades = load_trades()
    diag["all_trades"] = len(all_trades)
    diag["pending_trades"] = sum(1 for t in all_trades if t.result == TradeResult.PENDING)
    diag["pending_details"] = [
        {"ticker": t.ticker, "date": t.timestamp.isoformat(), "scan_type": t.scan_type.value}
        for t in all_trades if t.result == TradeResult.PENDING
    ]

    if is_pg_enabled():
        try:
            from .database import pg_load_journal
            raw = pg_load_journal()
            diag["pg_raw_count"] = len(raw)
            from .models import JournalEntry as JE
            for i, e in enumerate(raw):
                try:
                    JE(**e)
                except Exception as exc:
                    diag["pg_parse_errors"].append({"index": i, "ticker": e.get("ticker"), "error": str(exc)})
        except Exception as exc:
            diag["pg_error"] = str(exc)

    try:
        if JOURNAL_FILE.exists():
            raw = _json.loads(JOURNAL_FILE.read_text())
            diag["json_raw_count"] = len(raw)
            from .models import JournalEntry as JE
            for i, e in enumerate(raw):
                try:
                    JE(**e)
                except Exception as exc:
                    diag["json_parse_errors"].append({"index": i, "ticker": e.get("ticker") if isinstance(e, dict) else None, "error": str(exc)})
    except Exception as exc:
        diag["json_error"] = str(exc)

    entries = load_journal()
    diag["final_count"] = len(entries)
    return diag


@app.get("/api/journal/{date}")
def get_journal_by_date(date: str):
    """Get journal entries for a specific date (YYYY-MM-DD)."""
    entries = load_journal()
    return [e.model_dump(mode="json") for e in entries if e.date == date]


@app.post("/api/journal/trigger")
def trigger_journal():
    """Manually trigger the daily journal (for testing).

    O3: Runs in a background thread to avoid HTTP timeout.
    Returns immediately with diagnostic info about pending trades.
    Poll GET /api/journal for results.
    """
    # P1.1: Weekend guard — markets closed, no trades to close
    now_paris = datetime.now(PARIS_TZ)
    if now_paris.weekday() >= 5:
        raise HTTPException(
            status_code=400,
            detail="Marchés fermés le week-end. Journal disponible du lundi au vendredi.",
        )

    # Pre-check: how many trades exist and how many are PENDING
    from .models import TradeResult
    all_trades = load_trades()
    pending = [t for t in all_trades if t.result == TradeResult.PENDING]
    logger.info(
        "Journal trigger: %d total trades, %d PENDING",
        len(all_trades), len(pending),
    )

    def _journal_trigger_worker():
        global _last_scans
        if not _journal_lock.acquire(blocking=False):
            logger.warning("Journal trigger: journal lock held — skipping (journal already running)")
            return
        try:
            result = run_daily_journal()
            logger.info("Journal trigger: completed with %d entries", len(result) if result else 0)
            # Clear scan cache — trades are closed, dashboard should reset
            with _scans_lock:
                _last_scans = {}
                _save_scans_cache(_last_scans)
            logger.info("Scan cache cleared after manual journal trigger")
        except Exception as exc:
            logger.error("Journal trigger failed: %s", exc)
        finally:
            _journal_lock.release()

    thread = threading.Thread(target=_journal_trigger_worker, daemon=True, name="journal-trigger")
    thread.start()

    return {
        "status": "journal_started",
        "diagnostic": {
            "total_trades": len(all_trades),
            "pending_trades": len(pending),
            "pending_tickers": [t.ticker for t in pending],
            "message": (
                f"Journal lance en arriere-plan pour {len(pending)} trade(s) PENDING. Consultez GET /api/journal pour les resultats."
                if pending
                else "Aucun trade PENDING detecte, journal lance en arriere-plan."
            ),
        },
    }


# ── Scan history endpoints ────────────────────────────────────────


@app.get("/api/scan-history")
def get_scan_history(limit: int = 50):
    """Get recent scan history (all scored events + rejections).

    Returns the last `limit` scans, most recent first.
    Each entry includes all_scored_news and rejection_log for audit trail.
    """
    entries = load_scan_history()
    # Most recent first, capped
    entries.reverse()
    return [e.model_dump(mode="json") for e in entries[:limit]]


@app.get("/api/scan-history/{date}")
def get_scan_history_by_date(date: str):
    """Get scan history for a specific date (YYYY-MM-DD)."""
    entries = load_scan_history()
    return [
        e.model_dump(mode="json") for e in entries
        if e.timestamp.strftime("%Y-%m-%d") == date
    ]


# ── (#38) CSV Export endpoints ────────────────────────────────────


@app.get("/api/export/trades")
def export_trades_csv():
    """Export all trades as CSV (#38)."""
    trades = load_trades()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "timestamp", "scan_type", "ticker", "asset_name", "category",
        "direction", "news_headline", "news_category", "entry_price",
        "target_price", "stop_price", "target_pct", "stop_pct",
        "risk_reward", "confidence", "result", "exit_price", "pnl_pct",
        "binary_event_warning", "volume_confirmed", "pre_move_pct",
    ])
    for t in trades:
        writer.writerow([
            t.timestamp.isoformat(), t.scan_type.value, t.ticker, t.asset_name,
            t.category, t.direction.value, t.news_headline, t.news_category,
            t.entry_price, t.target_price, t.stop_price, t.target_pct, t.stop_pct,
            t.risk_reward, t.confidence, t.result.value, t.exit_price, t.pnl_pct,
            t.binary_event_warning, t.volume_confirmed, t.pre_move_pct,
        ])
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=trades.csv"},
    )


@app.get("/api/export/journal")
def export_journal_csv():
    """Export all journal entries as CSV (#38)."""
    entries = load_journal()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "date", "scan_type", "news_title", "news_source", "news_category",
        "score", "ticker", "asset_name", "asset_category", "direction",
        "entry_time", "entry_price", "exit_time", "exit_price",
        "day_high", "day_low", "result", "pnl_pct", "review",
        "binary_event_warning",
    ])
    for e in entries:
        writer.writerow([
            e.date, e.scan_type.value, e.news_title, e.news_source,
            e.news_category, e.score, e.ticker, e.asset_name, e.asset_category,
            e.direction.value, e.entry_time.isoformat() if e.entry_time else "",
            e.entry_price, e.exit_time.isoformat() if e.exit_time else "",
            e.exit_price, e.day_high, e.day_low, e.result.value, e.pnl_pct,
            e.review, e.binary_event_warning,
        ])
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=journal.csv"},
    )


# ── Economic calendar endpoints ───────────────────────────────────


@app.get("/api/positions/monitor")
def api_position_monitor():
    """Manually trigger position monitor (for testing)."""
    actions = monitor_positions()
    return {"actions": actions, "count": len(actions)}


@app.get("/api/calendar")
def get_calendar(days: int = 7):
    """Get upcoming economic events."""
    events = get_upcoming_events(window_days=days)
    return [
        {
            "name": e.name,
            "date": e.date.isoformat(),
            "time_cet": e.time_cet.strftime("%H:%M"),
            "impact": e.impact,
            "currency": e.currency,
            "blocks_trade": e.blocks_trade,
        }
        for e in events
    ]


# ── (#31) Backtest endpoints ─────────────────────────────────────


@app.get("/api/backtest")
def api_backtest(min_score: int | None = None, min_rr: float | None = None):
    """Run backtest with optional parameter overrides (#31)."""
    trades = load_trades()
    params = {}
    if min_score is not None:
        params["min_score"] = min_score
    if min_rr is not None:
        params["min_rr"] = min_rr
    return run_backtest(trades, params if params else None)


@app.get("/api/backtest/sweep")
def api_backtest_sweep():
    """Run parameter sweep to find optimal settings (#31)."""
    trades = load_trades()
    return run_parameter_sweep(trades)


# ── (L4) Database monitoring & maintenance endpoints ─────────────


@app.get("/api/db/stats")
def get_db_stats():
    """L4: Get database table statistics (row counts, sizes, oldest/newest)."""
    from .database import pg_table_stats
    return pg_table_stats()


@app.post("/api/db/maintenance")
def run_db_maintenance():
    """M4: Run VACUUM ANALYZE on all PG tables."""
    from .database import pg_run_maintenance
    return pg_run_maintenance()


@app.post("/api/db/backup")
def run_db_backup():
    """L5: Dump all PG tables to JSON backup files."""
    from .database import pg_backup_to_json
    return pg_backup_to_json()


# ── v5.1: Price archive & news replay backtest ──────────────────


@app.get("/api/prices/current")
def get_current_prices(tickers: str = ""):
    """Fetch current prices for a comma-separated list of tickers.

    Used by frontend to show live P&L on open positions.
    Example: /api/prices/current?tickers=GC=F,CL=F,EURUSD=X
    """
    if not tickers:
        return {}
    from .market_data import fetch_price
    from concurrent.futures import ThreadPoolExecutor, as_completed

    ticker_list = [t.strip() for t in tickers.split(",") if t.strip()]
    if len(ticker_list) > 50:
        raise HTTPException(400, "Max 50 tickers per request")

    results = {}
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch_price, t): t for t in ticker_list}
        for future in as_completed(futures, timeout=30):
            ticker = futures[future]
            try:
                price = future.result()
                if price is not None:
                    results[ticker] = price
            except Exception:
                pass
    return results


@app.get("/api/market-data/td-urls")
def get_td_market_urls():
    """Return Twelve Data market page URLs for all known tickers."""
    from .market_data import get_all_td_market_urls
    return get_all_td_market_urls()


@app.get("/api/price-archive/stats")
def get_price_archive_stats():
    """v5.1: Get price archive statistics."""
    if not is_pg_enabled():
        return {"status": "pg_not_enabled"}
    from .database import pg_price_archive_stats
    return pg_price_archive_stats()


@app.post("/api/price-archive/fill")
def fill_price_archive(days: int = 30):
    """v5.1: Backfill price archive for all tickers.

    Fetches daily OHLCV for the last `days` trading days for all 41 tickers.
    Safe to call repeatedly — uses ON CONFLICT DO NOTHING for dedup.
    """
    if not is_pg_enabled():
        raise HTTPException(400, "PostgreSQL not configured")
    if days > 365:
        raise HTTPException(400, "Maximum 365 days")

    from .config import ASSETS
    from .database import pg_save_price_archive
    from .market_data import fetch_history

    rows = []
    errors = []
    for asset in ASSETS:
        try:
            df = fetch_history(asset.ticker, period_days=days + 5, interval="1day")
            if df is None or df.empty:
                errors.append(asset.ticker)
                continue
            for dt, row in df.iterrows():
                rows.append({
                    "ticker": asset.ticker,
                    "date": dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt)[:10],
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "volume": float(row.get("Volume", 0)),
                    "source": "twelve_data",
                })
        except Exception as exc:
            errors.append(f"{asset.ticker}: {exc}")

    inserted = pg_save_price_archive(rows) if rows else 0
    return {
        "total_rows_fetched": len(rows),
        "inserted": inserted,
        "tickers": len(ASSETS),
        "errors": errors[:10],  # Limit error list
    }


@app.get("/api/backtest/replay")
def api_backtest_replay(days: int = 90, min_score: float | None = None):
    """v5.1: Replay historical news backtest.

    Re-scores historical scan_history headlines using current scoring formula
    and compares against actual outcomes.
    """
    from .backtest import run_news_replay_backtest
    params = {}
    if min_score is not None:
        params["min_score"] = min_score
    return run_news_replay_backtest(days=days, override_params=params)


# ── Agent API Endpoints (v6.0) ───────────────────────────────────


@app.get("/api/agents")
def list_agents():
    """Get status + metrics for all 6 agents."""
    return get_agents_status()


@app.get("/api/agents/logs/all")
def all_agent_logs(limit: int = 15, level: str = "WARN,ERROR", since_hours: float = 48):
    """Get consolidated logs across ALL agents in a single request.

    Replaces 21 individual /api/agents/{name}/logs calls from the frontend
    notification polling (was ~42 requests/min, now ~2 requests/min).
    """
    from .agents.registry import get_all_agents
    all_logs = []
    for name, agent in get_all_agents().items():
        try:
            logs = agent.logger.get_logs(limit=limit, level=level, since_hours=since_hours)
            for log in (logs if isinstance(logs, list) else []):
                log["agent"] = name
                all_logs.append(log)
        except Exception:
            pass
    all_logs.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return all_logs[:50]


@app.get("/api/agents/{agent_name}/logs")
def agent_logs(agent_name: str, limit: int = 50, level: str | None = None, since_hours: float | None = None):
    """Get structured logs for a specific agent.

    Args:
        level: Filter by level(s), comma-separated (e.g. "WARN,ERROR").
        since_hours: Only return logs from the last N hours.
    """
    agent = get_agent(agent_name)
    if not agent:
        # UX agent is virtual — no logs
        if agent_name == "ux":
            return []
        raise HTTPException(404, f"Agent '{agent_name}' not found")
    return agent.logger.get_logs(limit=limit, level=level, since_hours=since_hours)


@app.get("/api/agents/{agent_name}/status")
def agent_status(agent_name: str):
    """Get detailed status for a specific agent."""
    agent = get_agent(agent_name)
    if not agent:
        if agent_name == "ux":
            return {
                "name": "ux",
                "description": "Frontend & expérience utilisateur",
                "status": "idle",
                "metrics": {},
            }
        raise HTTPException(404, f"Agent '{agent_name}' not found")
    status = agent.status
    status["metrics"] = agent.get_metrics()
    return status


@app.get("/api/agents/messages")
def agent_messages(limit: int = 50):
    """Get recent agent bus messages (debug)."""
    from .agents.base import MessageBus
    bus = MessageBus()
    return bus.recent_messages(limit=limit)


@app.post("/api/agents/auditor/audit/{target_agent}")
def trigger_audit(target_agent: str, focus: str | None = None):
    """Trigger an audit of a specific agent."""
    auditor = get_agent("auditor")
    if not auditor:
        raise HTTPException(500, "Auditor agent not initialized")
    try:
        report = auditor.run(target_agent=target_agent, focus=focus)
        return report
    except Exception as exc:
        raise HTTPException(500, str(exc))


@app.get("/api/agents/auditor/reports")
def get_audit_reports(target_agent: str | None = None, limit: int = 20):
    """Get historical audit reports."""
    auditor = get_agent("auditor")
    if not auditor:
        return []
    return auditor.get_reports(target_agent=target_agent, limit=limit)


@app.get("/api/agents/auditor/reports/latest/{target_agent}")
def get_latest_audit(target_agent: str):
    """Get the most recent audit report for an agent."""
    auditor = get_agent("auditor")
    if not auditor:
        raise HTTPException(404, "No audit reports")
    report = auditor.get_latest_report(target_agent)
    if not report:
        raise HTTPException(404, f"No audit report for agent '{target_agent}'")
    return report


# ── Agent Infrastructure API ─────────────────────────────────────


@app.get("/api/infrastructure/health")
def get_infra_health():
    """Run and return infrastructure health check."""
    from .agents.registry import run_infra_health_check
    return run_infra_health_check()


@app.post("/api/infrastructure/maintenance")
def trigger_infra_maintenance():
    """Trigger infrastructure maintenance (VACUUM, pruning)."""
    from .agents.registry import run_infra_maintenance
    return run_infra_maintenance()


@app.get("/api/infrastructure/report")
def get_infra_report():
    """Get full infrastructure report."""
    from .agents.registry import run_infra_report
    return run_infra_report()


@app.post("/api/infrastructure/reset")
def reset_all_data(password: str = ""):
    """Reset ALL trading data for a fresh start.

    Requires RESET_PASSWORD secret to be set and provided.
    Truncates all PG tables, resets JSON files, clears learning configs,
    clears agent in-memory state, removes cleanup sentinel.
    """
    expected = os.environ.get("RESET_PASSWORD", "")
    if not expected:
        raise HTTPException(403, "RESET_PASSWORD secret not configured on server")
    if password != expected:
        raise HTTPException(403, "Mot de passe incorrect")
    results: dict = {}

    # 1. PG tables
    results["pg_tables"] = _truncate_pg_tables()

    # 2. JSON files
    json_results, _ = _reset_json_files()
    results["json_files"] = json_results

    # 3. Learning configs
    results["learning_configs"] = _delete_learning_configs()

    # 4. Clear agent in-memory state (caches, counters, positions)
    results["agent_memory"] = _clear_agent_memory()

    # 5. Clear in-memory scan cache
    global _last_scans
    with _scans_lock:
        _last_scans = {}
    results["scan_cache"] = "cleared"

    # 6. Remove cleanup sentinel so next deploy won't skip
    if _CLEANUP_SENTINEL.exists():
        _CLEANUP_SENTINEL.unlink()
        results["sentinel"] = "removed"

    logger.info("MANUAL RESET via API — results: %s", results)
    return {"status": "ok", "results": results}


# ── Per-team reset definitions ───────────────────────────────────

_TEAM_RESET_MAP: dict[int, dict] = {
    1: {
        "label": "Équipe 1 — Day Trading Intraday",
        "pg_tables": ["trades", "journal_entries", "scan_history", "last_scans"],
        "json_files": {
            "trades.json": "[]",
            "journal.json": "[]",
            "scan_history.json": "[]",
            "last_scans.json": "{}",
        },
        "learning_configs": [],
        "agents": ["trader_1", "journal", "learning"],
    },
    2: {
        "label": "Équipe 2 — Trend Following",
        "pg_tables": ["trend_positions", "trend_journal_entries"],
        "json_files": {
            "trend_positions.json": "{}",
            "trend_journal.json": "[]",
        },
        "learning_configs": [],
        "agents": ["scoring_2", "trader_2", "journal_2", "learning_2"],
    },
    3: {
        "label": "Équipe 3 — Technical Indicators",
        "pg_tables": ["tech_positions", "tech_journal_entries"],
        "json_files": {
            "tech_positions.json": '{"active": [], "closed": []}',
            "tech_journal.json": "[]",
        },
        "learning_configs": ["learning3_weekly_config.json", "learning3_config_history.json"],
        "agents": ["scoring_3", "trader_3", "journal_3", "learning_3"],
    },
    4: {
        "label": "Équipe 4 — Meta/Ensemble",
        "pg_tables": ["meta_positions", "meta_journal_entries"],
        "json_files": {
            "meta_positions.json": "{}",
            "meta_journal.json": "[]",
        },
        "learning_configs": ["learning4_weekly_config.json", "learning4_config_history.json"],
        "agents": ["scoring_4", "trader_4", "journal_4", "learning_4"],
    },
}


@app.post("/api/infrastructure/reset-team/{team_id}")
def reset_team_data(team_id: int, password: str = ""):
    """Reset trading data for a SINGLE team (1-4).

    Only touches PG tables, JSON files, learning configs, and agent memory
    that belong to the specified team. All other teams remain untouched.
    """
    expected = os.environ.get("RESET_PASSWORD", "")
    if not expected:
        raise HTTPException(403, "RESET_PASSWORD secret not configured on server")
    if password != expected:
        raise HTTPException(403, "Mot de passe incorrect")
    if team_id not in _TEAM_RESET_MAP:
        raise HTTPException(400, f"Équipe invalide : {team_id}. Valeurs acceptées : 1, 2, 3, 4")

    team = _TEAM_RESET_MAP[team_id]
    results: dict = {"team": team_id, "label": team["label"]}

    # 1. PG tables
    pg_results = {}
    if is_pg_enabled():
        for table in team["pg_tables"]:
            try:
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT 1 FROM information_schema.tables "
                            "WHERE table_schema = 'public' AND table_name = %s",
                            (table,),
                        )
                        if cur.fetchone():
                            cur.execute(f"TRUNCATE TABLE {table} CASCADE")
                            pg_results[table] = "truncated"
                        else:
                            pg_results[table] = "not_found"
                    conn.commit()
            except Exception as exc:
                pg_results[table] = f"error: {exc}"
    results["pg_tables"] = pg_results

    # 2. JSON files
    data_dir = Path(os.getenv("DATA_DIR", "data"))
    json_results = {}
    for fname, empty_content in team["json_files"].items():
        fpath = data_dir / fname
        try:
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(empty_content)
            json_results[fname] = "reset"
        except Exception as exc:
            json_results[fname] = f"error: {exc}"
    results["json_files"] = json_results

    # 3. Learning configs
    config_results = {}
    for fname in team["learning_configs"]:
        fpath = data_dir / fname
        try:
            if fpath.exists():
                fpath.unlink()
                config_results[fname] = "deleted"
            else:
                config_results[fname] = "not_found"
        except Exception as exc:
            config_results[fname] = f"error: {exc}"
    if config_results:
        results["learning_configs"] = config_results

    # 4. Clear in-memory state for team agents only
    agent_results = {}
    try:
        all_agents = get_all_agents()
        for agent_name in team["agents"]:
            agent = all_agents.get(agent_name)
            if not agent:
                agent_results[agent_name] = "not_found"
                continue
            cleared = []
            # Learning caches
            if hasattr(agent, "_cached_adjustments"):
                agent._cached_adjustments = None
                agent._cache_valid = False
                cleared.append("cached_adjustments")
            if hasattr(agent, "_last_anomalies") and isinstance(getattr(agent, "_last_anomalies", None), list):
                agent._last_anomalies = []
                cleared.append("anomalies")
            if hasattr(agent, "_total_recalculations"):
                agent._total_recalculations = 0
                cleared.append("recalculations")
            if hasattr(agent, "_last_run_time"):
                agent._last_run_time = None
                cleared.append("last_run_time")
            # Trader counters
            for attr in ["_trades_today", "_trades_total", "_rejections_today",
                         "_evaluations_today", "_position_changes_total",
                         "_trades_opened_total", "_trades_closed_total",
                         "_position_opens_total", "_position_closes_total",
                         "_pending_positions"]:
                if hasattr(agent, attr):
                    setattr(agent, attr, 0)
                    cleared.append(attr)
            for attr in ["_last_trade_ticker", "_last_trade_direction",
                         "_last_change_ticker", "_last_change_direction",
                         "_last_action_ticker", "_last_action_direction"]:
                if hasattr(agent, attr):
                    setattr(agent, attr, None)
                    cleared.append(attr)
            if hasattr(agent, "_current_learning"):
                agent._current_learning = {}
                cleared.append("current_learning")
            if hasattr(agent, "_current_trend_scoring"):
                agent._current_trend_scoring = {}
                cleared.append("current_trend_scoring")
            if hasattr(agent, "_weekly_config"):
                agent._weekly_config = None
                cleared.append("weekly_config")
            # Journal stats
            for attr in ["_last_run_trades_closed", "_last_run_tp", "_last_run_sl",
                         "_last_run_expired", "_total_entries"]:
                if hasattr(agent, attr):
                    setattr(agent, attr, 0)
                    cleared.append(attr)
            if hasattr(agent, "_last_pnl_sum"):
                agent._last_pnl_sum = 0.0
                cleared.append("last_pnl_sum")
            # Scoring counters
            for attr in ["_last_scored_count", "_last_zero_edge_filtered",
                         "_total_scored", "_total_tokens_used", "_cache_hits"]:
                if hasattr(agent, attr):
                    setattr(agent, attr, 0)
                    cleared.append(attr)
            if hasattr(agent, "_last_result"):
                agent._last_result = None
                cleared.append("last_result")
            agent_results[agent_name] = cleared if cleared else "no_state"
    except Exception as exc:
        agent_results["error"] = str(exc)
    results["agent_memory"] = agent_results

    # 5. Clear scan cache for team 1 only (shared scan cache)
    if team_id == 1:
        global _last_scans
        with _scans_lock:
            _last_scans = {}
        results["scan_cache"] = "cleared"

    logger.info("TEAM %d RESET via API — results: %s", team_id, results)
    return {"status": "ok", "results": results}


# ── Agent Performance API ────────────────────────────────────────


@app.get("/api/performance/snapshot")
def get_performance_snapshot():
    """Get latest performance snapshot (lightweight KPIs)."""
    agent = get_agent("performance")
    if not agent:
        return {"error": "Performance agent not available"}
    return agent.get_metrics()


@app.get("/api/performance/report")
def get_performance_report():
    """Get latest daily performance report."""
    agent = get_agent("performance")
    if not agent:
        return {"error": "Performance agent not available"}
    return agent._last_report or {"message": "No report yet"}


@app.get("/api/performance/history")
def get_performance_history():
    """Get performance snapshots history."""
    agent = get_agent("performance")
    if not agent:
        return {"snapshots": [], "daily_reports": []}
    return {
        "snapshots": agent._snapshots,
        "daily_reports": agent._daily_reports,
    }


@app.post("/api/performance/trigger/{action}")
def trigger_performance(action: str):
    """Trigger a performance action (snapshot, daily_report, weekly_trends)."""
    agent = get_agent("performance")
    if not agent:
        raise HTTPException(status_code=404, detail="Performance agent not available")
    if action not in ("snapshot", "daily_report", "weekly_trends"):
        raise HTTPException(status_code=400, detail=f"Invalid action: {action}")
    return agent.run(action=action)


# ── Agent Trader 2 — Trend Positions API ────────────────────────


@app.get("/api/trader2/positions")
def get_trend_positions():
    """Get current trend positions for Trader 2."""
    agent = get_agent("trader_2")
    if not agent:
        return {}
    return agent.get_positions()


@app.get("/api/trader2/positions/{ticker}/history")
def get_trend_position_history(ticker: str):
    """Get position change history for a ticker."""
    agent = get_agent("trader_2")
    if not agent:
        return []
    return agent.get_position_history(ticker)


# ── Agent Scoring 1 — Scoring API ────────────────────────────────


@app.get("/api/scoring/result")
def get_scoring_result():
    """P2.7: Get last scoring result (parity with Teams 2/3/4)."""
    agent = get_agent("scoring")
    if not agent:
        return {}
    return agent.get_last_result() or {}


# ── Agent Scoring 2 — Trend Scoring API ──────────────────────────


@app.get("/api/scoring2/result")
def get_trend_scoring_result():
    """Get last trend scoring result (re-weighted news for Trader 2)."""
    agent = get_agent("scoring_2")
    if not agent:
        return {}
    return agent.get_last_result() or {}


# ── Agent Journal 2 — Trend Journal API ─────────────────────────


@app.get("/api/journal2/entries")
def get_trend_journal_entries():
    """Get trend journal entries (completed position periods)."""
    agent = get_agent("journal_2")
    if not agent:
        return []
    return agent.get_entries()


@app.post("/api/journal2/trigger")
def trigger_journal_2():
    """Manually trigger Journal 2 run.

    P5 fix: run in background thread to avoid HTTP timeout.
    """
    # P1.1: Weekend guard
    now_paris = datetime.now(PARIS_TZ)
    if now_paris.weekday() >= 5:
        raise HTTPException(
            status_code=400,
            detail="Marchés fermés le week-end. Journal 2 disponible du lundi au vendredi.",
        )

    agent = get_agent("journal_2")
    if not agent:
        raise HTTPException(500, "Journal 2 agent not available")
    import threading
    threading.Thread(target=agent.run, daemon=True).start()
    return {"status": "triggered", "message": "Journal 2 run started in background"}


# ── Agent Learning 2 — Trend Learning API ───────────────────────


@app.get("/api/learning2/adjustments")
def get_trend_learning_adjustments():
    """Get trend learning adjustments for Trader 2."""
    agent = get_agent("learning_2")
    if not agent:
        return {}
    return agent.get_adjustments()


@app.post("/api/learning2/trigger")
def trigger_learning_2():
    """Manually trigger Learning 2 recalculation."""
    agent = get_agent("learning_2")
    if not agent:
        raise HTTPException(500, "Learning 2 agent not available")
    return agent.run()


# ── Équipe 3 — Technical Indicators API ─────────────────────────


@app.get("/api/scoring3/result")
def get_tech_scoring_result():
    """Get last technical scoring result."""
    agent = get_agent("scoring_3")
    if not agent:
        return {}
    return agent.get_last_result() or {}


@app.get("/api/trader3/positions")
def get_tech_positions():
    """Get current technical positions for Trader 3."""
    agent = get_agent("trader_3")
    if not agent:
        return {"active": [], "closed": []}
    return agent.get_positions()


@app.get("/api/trader3/history")
def get_tech_trade_history():
    """Get closed trade history for Trader 3."""
    agent = get_agent("trader_3")
    if not agent:
        return []
    return agent.get_closed_positions(limit=200)


@app.get("/api/trader3/strategies")
def get_tech_strategies():
    """Get strategy A/B test results for Trader 3."""
    agent = get_agent("trader_3")
    if not agent:
        return {}
    return agent.get_strategy_performance()


@app.get("/api/journal3/entries")
def get_tech_journal_entries():
    """Get technical journal entries."""
    agent = get_agent("journal_3")
    if not agent:
        return []
    return agent.get_entries()


@app.post("/api/journal3/trigger")
def trigger_journal_3():
    """Manually trigger Journal 3 run."""
    # P1.1: Weekend guard
    now_paris = datetime.now(PARIS_TZ)
    if now_paris.weekday() >= 5:
        raise HTTPException(
            status_code=400,
            detail="Marchés fermés le week-end. Journal 3 disponible du lundi au vendredi.",
        )

    agent = get_agent("journal_3")
    if not agent:
        raise HTTPException(500, "Journal 3 agent not available")
    import threading
    threading.Thread(target=agent.run, daemon=True).start()
    return {"status": "triggered", "message": "Journal 3 run started in background"}


@app.get("/api/learning3/adjustments")
def get_tech_learning_adjustments():
    """Get technical learning adjustments for Trader 3."""
    agent = get_agent("learning_3")
    if not agent:
        return {}
    return agent.get_adjustments()


@app.post("/api/learning3/trigger")
def trigger_learning_3():
    """Manually trigger Learning 3 recalculation."""
    agent = get_agent("learning_3")
    if not agent:
        raise HTTPException(500, "Learning 3 agent not available")
    return agent.run()


@app.post("/api/learning3/weekly-config")
def generate_learning3_weekly_config():
    """Generate weekly strategy config for Team 3."""
    agent = get_agent("learning_3")
    if not agent:
        raise HTTPException(500, "Learning 3 agent not available")
    return agent.generate_weekly_config()


@app.get("/api/learning3/weekly-config")
def get_learning3_weekly_config():
    """Get current weekly config for Team 3."""
    agent = get_agent("learning_3")
    if not agent:
        return {}
    return agent.get_weekly_config() or {}


@app.get("/api/journal3/weekly-summary")
def get_journal3_weekly_summary():
    """Get weekly aggregated summary from Journal 3."""
    agent = get_agent("journal_3")
    if not agent:
        return {}
    return agent.compute_weekly_summary()


# ── Équipe 4 — Meta/Ensemble API ────────────────────────────────


@app.get("/api/scoring4/result")
def get_meta_scoring_result():
    """Get last meta scoring result."""
    agent = get_agent("scoring_4")
    if not agent:
        return {}
    return agent.get_last_result() or {}


@app.get("/api/trader4/positions")
def get_meta_positions():
    """Get current meta positions for Trader 4."""
    agent = get_agent("trader_4")
    if not agent:
        return {}
    return agent.get_positions()


@app.get("/api/trader4/history")
def get_meta_trade_history():
    """Get meta trade history."""
    agent = get_agent("trader_4")
    if not agent:
        return []
    return agent.get_trade_history()


@app.get("/api/journal4/entries")
def get_meta_journal_entries():
    """Get meta journal entries."""
    agent = get_agent("journal_4")
    if not agent:
        return []
    return agent.get_entries()


@app.post("/api/journal4/trigger")
def trigger_journal_4():
    """Manually trigger Journal 4 run."""
    # P1.1: Weekend guard
    now_paris = datetime.now(PARIS_TZ)
    if now_paris.weekday() >= 5:
        raise HTTPException(
            status_code=400,
            detail="Marchés fermés le week-end. Journal 4 disponible du lundi au vendredi.",
        )

    agent = get_agent("journal_4")
    if not agent:
        raise HTTPException(500, "Journal 4 agent not available")
    import threading
    threading.Thread(target=agent.run, daemon=True).start()
    return {"status": "triggered", "message": "Journal 4 run started in background"}


@app.get("/api/learning4/adjustments")
def get_meta_learning_adjustments():
    """Get meta learning adjustments for Trader 4."""
    agent = get_agent("learning_4")
    if not agent:
        return {}
    return agent.get_adjustments()


@app.post("/api/learning4/trigger")
def trigger_learning_4():
    """Manually trigger Learning 4 recalculation."""
    agent = get_agent("learning_4")
    if not agent:
        raise HTTPException(500, "Learning 4 agent not available")
    return agent.run()


@app.post("/api/learning4/weekly-config")
def generate_learning_4_weekly_config():
    """Manually trigger Learning 4 weekly config generation."""
    agent = get_agent("learning_4")
    if not agent:
        raise HTTPException(500, "Learning 4 agent not available")
    return agent.generate_weekly_config()


@app.get("/api/learning4/weekly-config")
def get_learning_4_weekly_config():
    """Get current Learning 4 weekly config."""
    agent = get_agent("learning_4")
    if not agent:
        raise HTTPException(500, "Learning 4 agent not available")
    config = agent.get_weekly_config()
    return config or {"status": "no_config_yet"}


@app.get("/api/journal4/weekly-summary")
def get_journal_4_weekly_summary():
    """Get Journal 4 weekly performance summary."""
    agent = get_agent("journal_4")
    if not agent:
        raise HTTPException(500, "Journal 4 agent not available")
    return agent.compute_weekly_summary()


# ── (#41) Enhanced health check ──────────────────────────────────


@app.get("/api/health")
def health():
    """Lightweight health check — MUST always return 200 quickly.

    NEVER call external services (yfinance, APIs) here. Replit pings this
    endpoint to decide whether to keep the process alive. If it blocks or
    returns 500, Replit kills the app → misfire_grace_time re-triggers a scan
    → scan blocks the event loop → health fails again → infinite crash loop.
    """
    status = {
        "status": "ok",
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "scheduled_jobs": [j.id for j in bg_scheduler.get_jobs()],
        "dependencies": {},
    }

    # API key checks — instant, no I/O
    status["dependencies"]["anthropic_key"] = "configured" if os.environ.get("ANTHROPIC_API_KEY") else "missing"
    status["dependencies"]["twelve_data_key"] = "configured" if os.environ.get("TWELVE_DATA_API_KEY") else "not_set (optional)"
    status["dependencies"]["eia_key"] = "configured" if os.environ.get("EIA_API_KEY") else "not_set (optional)"
    status["dependencies"]["gnews_key"] = "configured" if os.environ.get("GNEWS_API_KEY") else "not_set (optional)"
    status["dependencies"]["usda_key"] = "configured" if os.environ.get("USDA_API_KEY") else "not_set (optional)"

    # Market data provider status (instant — no I/O, just internal state)
    try:
        from .market_data import get_provider_status
        status["market_data"] = get_provider_status()
    except Exception:
        status["market_data"] = {"primary": "yfinance"}

    # Data storage — report mode without I/O
    status["persistence"] = "postgresql" if is_pg_enabled() else "json_files"

    # Degraded only if the critical API key is missing
    if status["dependencies"].get("anthropic_key") == "missing":
        status["status"] = "degraded"

    return status


# ── Source Health Monitoring ──────────────────────────────────────

@app.get("/api/source-health")
def source_health(days: int = 7):
    """Get source health reports (daily + weekly).

    v5.2: Returns recent daily reports and weekly reviews.
    Query param: days (default 7) — how many days of history.
    """
    try:
        from .source_monitor import get_tracker, pg_load_source_health
        tracker = get_tracker()

        # Get current daily report (in-memory)
        current = tracker.get_daily_report()

        # Get historical from PG if available
        historical = pg_load_source_health(days=days)

        return {
            "current_day": current,
            "historical": historical,
        }
    except Exception as exc:
        logger.error("Source health endpoint error: %s", exc)
        return {"current_day": {}, "historical": [], "error": str(exc)}


@app.get("/api/source-health/weekly")
def source_health_weekly():
    """Get the latest weekly source health review.

    v5.2: Analyzes last 7 days of source health + suggests new sources.
    """
    try:
        from .source_monitor import get_tracker
        tracker = get_tracker()
        review = tracker.get_weekly_review()
        return review
    except Exception as exc:
        logger.error("Weekly source health endpoint error: %s", exc)
        return {"error": str(exc)}


# ── Admin: Price corrections ────────────────────────────────────────


@app.post("/api/admin/fix-entry-price")
def fix_entry_price(body: dict):
    """Fix a bad entry_price for any team's position.

    Body: {"team": 1|2|3|4, "ticker": "HG=F", "correct_price": 5.93}

    This will:
    1. Update the entry_price in the position/trade data
    2. Recalculate P&L based on the corrected price
    3. Save to persistence (PG + JSON fallback)
    """
    team = body.get("team")
    ticker = body.get("ticker")
    correct_price = body.get("correct_price")

    if not team or not ticker or correct_price is None:
        raise HTTPException(400, "Required: team (1-4), ticker, correct_price")
    if correct_price <= 0:
        raise HTTPException(400, "correct_price must be positive")

    result = {"team": team, "ticker": ticker, "new_entry_price": correct_price}

    if team == 1:
        # Team 1: fix in trades table (PENDING trades only)
        from .learning import load_trades, save_trades
        trades = load_trades()
        fixed = 0
        for t in trades:
            if t.ticker == ticker and t.result == "PENDING":
                old_price = t.entry_price
                t.entry_price = correct_price
                # Recalculate TP/SL based on original ratios
                if old_price and old_price > 0:
                    tp_ratio = t.target_price / old_price if t.target_price else None
                    sl_ratio = t.stop_price / old_price if t.stop_price else None
                    if tp_ratio:
                        t.target_price = round(correct_price * tp_ratio, 4)
                    if sl_ratio:
                        t.stop_price = round(correct_price * sl_ratio, 4)
                fixed += 1
                logger.info("Fixed T1 entry_price for %s: %.4f → %.4f", ticker, old_price, correct_price)
        if fixed:
            save_trades(trades)
        result["fixed_count"] = fixed

    elif team == 2:
        # Team 2: fix in trend_positions
        agent = get_agent("trader_2")
        if agent:
            positions = agent.get_positions()
            if ticker in positions:
                pos = positions[ticker]
                old_price = pos.get("entry_price")
                pos["entry_price"] = correct_price
                # Recalculate unrealized P&L
                cur = pos.get("current_price")
                direction = pos.get("direction")
                if cur and cur > 0 and direction in ("LONG", "SHORT"):
                    if direction == "LONG":
                        pos["unrealized_pnl_pct"] = round((cur - correct_price) / correct_price * 100, 2)
                    else:
                        pos["unrealized_pnl_pct"] = round((correct_price - cur) / correct_price * 100, 2)
                # Save via agent's internal method
                from .agents.agent_trader_2 import _save_positions
                _save_positions(positions)
                result["old_price"] = old_price
                result["direction"] = direction
                result["new_pnl_pct"] = pos.get("unrealized_pnl_pct")
                logger.info("Fixed T2 entry_price for %s: %s → %.4f", ticker, old_price, correct_price)
            else:
                raise HTTPException(404, f"No T2 position for {ticker}")
        else:
            raise HTTPException(500, "Trader 2 agent not available")

    elif team == 3:
        # Team 3: fix in tech_positions
        agent = get_agent("trader_3")
        if agent:
            state = agent.get_positions()
            active = state.get("active", [])
            fixed = 0
            for pos in active:
                if pos.get("ticker") == ticker:
                    old_price = pos.get("entry_price")
                    pos["entry_price"] = correct_price
                    pos["current_price"] = pos.get("current_price", correct_price)
                    cur = pos.get("current_price")
                    direction = pos.get("direction", "LONG")
                    if cur and cur > 0:
                        if direction == "LONG":
                            pos["unrealized_pnl_pct"] = round((cur - correct_price) / correct_price * 100, 2)
                        else:
                            pos["unrealized_pnl_pct"] = round((correct_price - cur) / correct_price * 100, 2)
                    pos["high_watermark"] = max(correct_price, pos.get("high_watermark", correct_price))
                    pos["low_watermark"] = min(correct_price, pos.get("low_watermark", correct_price))
                    fixed += 1
                    logger.info("Fixed T3 entry_price for %s: %s → %.4f", ticker, old_price, correct_price)
            if fixed:
                from .agents.agent_trader_3 import _save_positions
                _save_positions(state)
            result["fixed_count"] = fixed

    elif team == 4:
        # Team 4: fix in meta_positions
        agent = get_agent("trader_4")
        if agent:
            positions = agent.get_positions()
            if ticker in positions:
                pos = positions[ticker]
                old_price = pos.get("entry_price")
                pos["entry_price"] = correct_price
                cur = pos.get("current_price")
                direction = pos.get("direction")
                if cur and cur > 0 and direction in ("LONG", "SHORT"):
                    if direction == "LONG":
                        pos["unrealized_pnl_pct"] = round((cur - correct_price) / correct_price * 100, 2)
                    else:
                        pos["unrealized_pnl_pct"] = round((correct_price - cur) / correct_price * 100, 2)
                from .agents.agent_trader_4 import _save_positions
                _save_positions(positions)
                result["old_price"] = old_price
                logger.info("Fixed T4 entry_price for %s: %s → %.4f", ticker, old_price, correct_price)
            else:
                raise HTTPException(404, f"No T4 position for {ticker}")
        else:
            raise HTTPException(500, "Trader 4 agent not available")
    else:
        raise HTTPException(400, "team must be 1, 2, 3, or 4")

    # Update price reference to prevent future anomalies
    from .market_data import _update_price_reference
    _update_price_reference(ticker, correct_price)

    return result


@app.get("/api/admin/price-references")
def get_price_references():
    """Get current price reference cache (for debugging price anomalies)."""
    from .market_data import _price_reference, _price_ref_lock
    with _price_ref_lock:
        return dict(_price_reference)


# ── Serve frontend build (production only) ────────────────────────
# In production (Replit Autoscale), there's no Vite dev server.
# The backend serves the built React app from frontend/dist/.
# This MUST be after all /api routes so they take precedence.

if FRONTEND_DIST.is_dir():
    # Serve static assets (JS, CSS, images)
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="static-assets")

    @app.get("/{path:path}")
    def serve_spa(path: str):
        """Serve frontend SPA — any non-API route returns index.html."""
        file_path = FRONTEND_DIST / path
        if file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIST / "index.html")
