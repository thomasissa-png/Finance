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
    invalidate_learning_2_cache,
    run_learning_update,
    run_learning_2_update,
    run_daily_journal as agents_run_journal,
    run_daily_journal_2 as agents_run_journal_2,
    run_event_check as agents_run_event_check,
    run_position_monitor as agents_run_position_monitor,
    run_scan_pipeline,
    run_weekly_review as agents_run_weekly_review,
)
from .backtest import run_backtest, run_parameter_sweep
from .config import SCAN_KEY_TO_TYPE, TRIGGER_COOLDOWN_SECONDS
from .database import is_pg_enabled, init_db
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


def _run_post_eia_scan() -> None:
    """Run conditional post-EIA scan on Wednesdays at 16:45 CET."""
    _run_scheduled_scan("us_session")
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
            except Exception:
                pass
            # Invalidate learning cache and run full learning update
            invalidate_learning_cache()
            try:
                run_learning_update()
            except Exception as exc:
                logger.warning("Learning update after journal failed: %s", exc)
            # Run Journal 2 (trend positions) + Learning 2 update
            try:
                agents_run_journal_2()
                invalidate_learning_2_cache()
                run_learning_2_update()
            except Exception as exc:
                logger.warning("Journal 2 / Learning 2 update failed: %s", exc)
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _last_scans
    # Initialize PostgreSQL tables if DATABASE_URL is set
    init_db()
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
    bg_scheduler.add_job(_run_europe_scan, CronTrigger(hour=7, minute=50, day_of_week="mon-fri", timezone="Europe/Paris"), id="europe_scan", misfire_grace_time=60)
    bg_scheduler.add_job(_run_mid_session_scan, CronTrigger(hour=11, minute=15, day_of_week="mon-fri", timezone="Europe/Paris"), id="mid_session_scan", misfire_grace_time=60)
    bg_scheduler.add_job(_run_us_scan, CronTrigger(hour=14, minute=50, day_of_week="mon-fri", timezone="Europe/Paris"), id="us_scan", misfire_grace_time=60)
    bg_scheduler.add_job(_run_us_session_scan, CronTrigger(hour=17, minute=0, day_of_week="mon-fri", timezone="Europe/Paris"), id="us_session_scan", misfire_grace_time=60)
    # Daily journal at 22:00 CET — auto-close trades + generate journal, weekdays only
    # misfire_grace_time=3600 (1h) — journal is pure data processing (no external API calls),
    # so crash loops are not a concern. A long grace period ensures the journal runs even
    # after prolonged downtime (e.g., Replit kills the app from 21:00 to 23:30).
    bg_scheduler.add_job(_run_daily_journal, CronTrigger(hour=22, minute=0, day_of_week="mon-fri", timezone="Europe/Paris"), id="daily_journal", misfire_grace_time=3600)
    # P2-5: Event-driven scanner: every 10 min during trading hours (was 15min)
    # Faster detection = less edge lost waiting for next check
    bg_scheduler.add_job(_run_event_check, CronTrigger(minute="*/10", hour="7-19", day_of_week="mon-fri", timezone="Europe/Paris"), id="event_check", misfire_grace_time=60)
    # Position monitor: every 15 min during trading hours — trailing stop + time stop
    bg_scheduler.add_job(_run_position_monitor, CronTrigger(minute="7,22,37,52", hour="7-19", day_of_week="mon-fri", timezone="Europe/Paris"), id="position_monitor", misfire_grace_time=60)
    # Conditional post-EIA scan: Wednesday 16:45 CET (EIA petroleum report at 16:30)
    bg_scheduler.add_job(_run_post_eia_scan, CronTrigger(hour=16, minute=45, day_of_week="wed", timezone="Europe/Paris"), id="post_eia_scan", misfire_grace_time=60)
    # v5.2: Weekly source health review — Sunday 20:00 CET (before Monday trading)
    # misfire_grace_time=3600: safe to run late, pure data analysis
    bg_scheduler.add_job(_run_weekly_source_review, CronTrigger(hour=20, minute=0, day_of_week="sun", timezone="Europe/Paris"), id="weekly_source_review", misfire_grace_time=3600)
    bg_scheduler.start()
    logger.info("Scheduler started — scans at 07:50, 11:15, 14:50, 17:00, event check q10min, position monitor q15min, post-EIA Wed 16:45, journal at 22:00 CET (weekdays), source review Sun 20:00")

    # Startup recovery: close any old PENDING trades that were missed by the 22:00 journal
    # (e.g., app was down overnight, Replit killed the process before journal ran)
    _recover_pending_trades_on_startup()

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
    trades = load_trades()
    return [t.model_dump(mode="json") for t in trades]


@app.get("/api/trades/pending")
def get_pending_trades():
    """Get trades awaiting resolution."""
    trades = load_trades()
    return [t.model_dump(mode="json") for t in trades if t.result == TradeResult.PENDING]


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


@app.get("/api/agents/{agent_name}/logs")
def agent_logs(agent_name: str, limit: int = 50, level: str | None = None):
    """Get structured logs for a specific agent."""
    agent = get_agent(agent_name)
    if not agent:
        # UX agent is virtual — no logs
        if agent_name == "ux":
            return []
        raise HTTPException(404, f"Agent '{agent_name}' not found")
    return agent.logger.get_logs(limit=limit, level=level)


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
    """Manually trigger Journal 2 run."""
    agent = get_agent("journal_2")
    if not agent:
        raise HTTPException(500, "Journal 2 agent not available")
    return agent.run()


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
