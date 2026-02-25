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

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .backtest import run_backtest, run_parameter_sweep
from .config import SCAN_KEY_TO_TYPE, TRIGGER_COOLDOWN_SECONDS
from .economic_calendar import get_upcoming_events
from .journal import load_journal, run_daily_journal
from .learning import (
    compute_learning_adjustments,
    compute_performance,
    load_trades,
    update_trade_result,
)
from .models import ScanType, TradeResult
from .scheduler import run_event_check, run_scan

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

# Health check yfinance cache (5 min TTL)
_health_yf_cache: str = "unchecked"
_health_yf_ts: float = 0.0


def _load_scans_cache() -> dict[str, dict]:
    """Load last scan results from disk (#34)."""
    try:
        if SCANS_CACHE_FILE.exists():
            return json.loads(SCANS_CACHE_FILE.read_text())
    except (json.JSONDecodeError, Exception) as exc:
        logger.warning("Failed to load scans cache: %s", exc)
    return {}


def _save_scans_cache(scans: dict[str, dict]) -> None:
    """Save scan results to disk (#34)."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        SCANS_CACHE_FILE.write_text(json.dumps(scans, indent=2, default=str))
    except Exception as exc:
        logger.warning("Failed to save scans cache: %s", exc)


bg_scheduler = BackgroundScheduler(timezone="Europe/Paris")


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
    """Run a scheduled scan and store results under scan_key."""
    scan_type_str = SCAN_KEY_TO_TYPE.get(scan_key, scan_key)
    scan_type = ScanType(scan_type_str)
    existing = _get_existing_trade_tickers(scan_key)
    result = run_scan(scan_type, existing_trade_ticker=existing or None)
    _last_scans[scan_key] = result
    _save_scans_cache(_last_scans)


def _run_europe_scan() -> None:
    _run_scheduled_scan("europe")


def _run_mid_session_scan() -> None:
    _run_scheduled_scan("mid_session")


def _run_us_scan() -> None:
    _run_scheduled_scan("us")


def _run_us_session_scan() -> None:
    _run_scheduled_scan("us_session")


def _run_daily_journal() -> None:
    run_daily_journal()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _last_scans
    # (#34) Load cached scans on startup
    _last_scans = _load_scans_cache()
    if _last_scans:
        logger.info("Restored %d cached scan results", len(_last_scans))

    # Schedule scans: 4 scans/day, weekdays only (markets closed on weekends)
    # misfire_grace_time=600 (10 min) — if app starts late (e.g. Replit cold start),
    # APScheduler still fires the missed scan instead of silently skipping it.
    bg_scheduler.add_job(_run_europe_scan, CronTrigger(hour=7, minute=50, day_of_week="mon-fri", timezone="Europe/Paris"), id="europe_scan", misfire_grace_time=600)
    bg_scheduler.add_job(_run_mid_session_scan, CronTrigger(hour=11, minute=15, day_of_week="mon-fri", timezone="Europe/Paris"), id="mid_session_scan", misfire_grace_time=600)
    bg_scheduler.add_job(_run_us_scan, CronTrigger(hour=14, minute=50, day_of_week="mon-fri", timezone="Europe/Paris"), id="us_scan", misfire_grace_time=600)
    bg_scheduler.add_job(_run_us_session_scan, CronTrigger(hour=17, minute=0, day_of_week="mon-fri", timezone="Europe/Paris"), id="us_session_scan", misfire_grace_time=600)
    # Event-driven scan: check every 30 min for high-impact signals, weekdays only
    bg_scheduler.add_job(
        run_event_check,
        CronTrigger(minute="*/30", day_of_week="mon-fri", timezone="Europe/Paris"),
        id="event_check",
        misfire_grace_time=600,
    )
    # Daily journal at 22:00 CET — auto-close trades + generate journal, weekdays only
    bg_scheduler.add_job(_run_daily_journal, CronTrigger(hour=22, minute=0, day_of_week="mon-fri", timezone="Europe/Paris"), id="daily_journal", misfire_grace_time=600)
    bg_scheduler.start()
    logger.info("Scheduler started — scans at 07:50, 11:15, 14:50, 17:00, event check every 30min, journal at 22:00 CET (weekdays only)")
    yield
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


@app.post("/api/scan/event-check")
def trigger_event_check():
    """Manually trigger an event-driven scan check."""
    # Weekend guard
    if datetime.now(PARIS_TZ).weekday() >= 5:
        raise HTTPException(
            status_code=400,
            detail="Marchés fermés le week-end.",
        )
    result = run_event_check()
    if result is None:
        return {"triggered": False, "reason": "No high-impact signals detected"}
    return {"triggered": True, "scan_result": result}


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
    """Enhanced health check: checks yfinance and API key status (#41)."""
    status = {
        "status": "ok",
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "scheduled_jobs": [j.id for j in bg_scheduler.get_jobs()],
        "dependencies": {},
    }

    # Check yfinance (cached 5 min to avoid blocking on every health check)
    global _health_yf_cache, _health_yf_ts
    now_ts = time.time()
    if now_ts - _health_yf_ts > 300:  # 5 min TTL
        try:
            import yfinance as yf
            data = yf.Ticker("^GSPC").history(period="1d")
            _health_yf_cache = "ok" if not data.empty else "no_data"
        except Exception as exc:
            _health_yf_cache = f"error: {exc}"
        _health_yf_ts = now_ts
    status["dependencies"]["yfinance"] = _health_yf_cache

    # Check Anthropic API key
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    status["dependencies"]["anthropic_key"] = "configured" if api_key else "missing"

    # Check optional API keys for structured data
    status["dependencies"]["eia_key"] = "configured" if os.environ.get("EIA_API_KEY") else "not_set (optional)"
    status["dependencies"]["gnews_key"] = "configured" if os.environ.get("GNEWS_API_KEY") else "not_set (optional)"
    status["dependencies"]["usda_key"] = "configured" if os.environ.get("USDA_API_KEY") else "not_set (optional)"

    # Check data files
    trades_file = DATA_DIR / "trades.json"
    journal_file = DATA_DIR / "journal.json"
    status["dependencies"]["trades_file"] = "ok" if trades_file.exists() else "missing"
    status["dependencies"]["journal_file"] = "ok" if journal_file.exists() else "missing"

    # Overall status
    if any("error" in str(v) or v == "missing" for v in status["dependencies"].values()
           if v != "missing"):  # trades/journal files missing is OK on first run
        if status["dependencies"].get("anthropic_key") == "missing":
            status["status"] = "degraded"

    return status
