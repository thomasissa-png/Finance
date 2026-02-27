"""FastAPI application — backend API for the news trading tool."""

import csv
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
from .scan_history import load_scan_history
from .scheduler import run_scan

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

# (#35) Rate limiting for triggers
_last_trigger_times: dict[str, float] = {}


def _load_scans_cache() -> dict[str, dict]:
    """Load last scan results (#34). Uses PostgreSQL when available."""
    if is_pg_enabled():
        try:
            from .database import pg_load_last_scans
            return pg_load_last_scans()
        except Exception as exc:
            logger.warning("Failed to load scans cache from PostgreSQL: %s", exc)
            return {}
    try:
        if SCANS_CACHE_FILE.exists():
            return json.loads(SCANS_CACHE_FILE.read_text())
    except (json.JSONDecodeError, Exception) as exc:
        logger.warning("Failed to load scans cache: %s", exc)
    return {}


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
        SCANS_CACHE_FILE.write_text(json.dumps(scans, indent=2, default=str))
    except Exception as exc:
        logger.warning("Failed to save scans cache: %s", exc)


bg_scheduler = BackgroundScheduler(timezone="Europe/Paris")

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
    """Collect trade tickers from all other active scans for portfolio correlation."""
    tickers = []
    for key, scan_data in _last_scans.items():
        if key == exclude_key:
            continue
        if scan_data.get("has_trade") and scan_data.get("recommendation"):
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
        try:
            scan_type_str = SCAN_KEY_TO_TYPE.get(scan_key, scan_key)
            scan_type = ScanType(scan_type_str)
            existing = _get_existing_trade_tickers(scan_key)
            result = run_scan(scan_type, existing_trade_ticker=existing or None)
            _last_scans[scan_key] = result
            _save_scans_cache(_last_scans)
        except Exception as exc:
            logger.error("Scheduled scan '%s' failed: %s", scan_key, exc)

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


def _run_daily_journal() -> None:
    """Run daily journal in background thread (same reason as scans)."""
    def _journal_worker():
        try:
            run_daily_journal()
        except Exception as exc:
            logger.error("Daily journal failed: %s", exc)

    thread = threading.Thread(target=_journal_worker, daemon=True, name="daily-journal")
    thread.start()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _last_scans
    # Initialize PostgreSQL tables if DATABASE_URL is set
    init_db()
    # (#34) Load cached scans on startup
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
    bg_scheduler.add_job(_run_daily_journal, CronTrigger(hour=22, minute=0, day_of_week="mon-fri", timezone="Europe/Paris"), id="daily_journal", misfire_grace_time=60)
    bg_scheduler.start()
    logger.info("Scheduler started — scans at 07:50, 11:15, 14:50, 17:00, journal at 22:00 CET (weekdays only)")

    # Start keepalive thread to prevent Replit autoscale from killing the app
    keepalive_thread = threading.Thread(target=_keepalive_loop, daemon=True, name="keepalive")
    keepalive_thread.start()
    logger.info("Keepalive thread started (ping every %ds)", KEEPALIVE_INTERVAL)

    yield

    _keepalive_stop.set()
    bg_scheduler.shutdown()


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
    """Get the most recent scan results (europe + us)."""
    return _last_scans


@app.get("/api/scan/latest/{scan_type}")
def get_latest_scan(scan_type: str):
    """Get the latest result for a specific scan type."""
    return _last_scans.get(scan_type, {"has_trade": False, "reason_no_trade": "Aucun scan effectué"})


PARIS_TZ = ZoneInfo("Europe/Paris")


def _run_triggered_scan(scan_type: str, st: ScanType, existing_ticker: list[str] | str | None) -> None:
    """Run a manually-triggered scan in a background thread.

    Results are stored in _last_scans and persisted to disk,
    available via GET /api/scan/latest/{scan_type}.
    """
    try:
        result = run_scan(st, existing_trade_ticker=existing_ticker)
        _last_scans[scan_type] = result
        _save_scans_cache(_last_scans)
        logger.info("Background scan %s completed (trade=%s)", scan_type, result.get("has_trade"))
    except Exception as exc:
        logger.error("Background scan %s failed: %s", scan_type, exc)
        _last_scans[scan_type] = {
            "scan_type": scan_type,
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

    return {"status": "scan_started", "scan_type": scan_type, "message": "Scan lance en arriere-plan. Consultez GET /api/scan/latest pour les resultats."}


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
    return [e.model_dump(mode="json") for e in entries]


@app.get("/api/journal/{date}")
def get_journal_by_date(date: str):
    """Get journal entries for a specific date (YYYY-MM-DD)."""
    entries = load_journal()
    return [e.model_dump(mode="json") for e in entries if e.date == date]


@app.post("/api/journal/trigger")
def trigger_journal():
    """Manually trigger the daily journal (for testing)."""
    return run_daily_journal()


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
    status["dependencies"]["eia_key"] = "configured" if os.environ.get("EIA_API_KEY") else "not_set (optional)"
    status["dependencies"]["gnews_key"] = "configured" if os.environ.get("GNEWS_API_KEY") else "not_set (optional)"
    status["dependencies"]["usda_key"] = "configured" if os.environ.get("USDA_API_KEY") else "not_set (optional)"

    # Data storage checks
    if is_pg_enabled():
        from .database import check_connection
        status["dependencies"]["database"] = "ok" if check_connection() else "error"
        status["persistence"] = "postgresql"
    else:
        trades_file = DATA_DIR / "trades.json"
        journal_file = DATA_DIR / "journal.json"
        status["dependencies"]["trades_file"] = "ok" if trades_file.exists() else "missing"
        status["dependencies"]["journal_file"] = "ok" if journal_file.exists() else "missing"
        status["persistence"] = "json_files"

    # Degraded only if the critical API key is missing
    if status["dependencies"].get("anthropic_key") == "missing":
        status["status"] = "degraded"
    if status["dependencies"].get("database") == "error":
        status["status"] = "degraded"

    return status


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
