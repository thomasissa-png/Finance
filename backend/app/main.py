"""FastAPI application — backend API for the news trading tool."""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .learning import (
    compute_learning_adjustments,
    compute_performance,
    load_trades,
    update_trade_result,
)
from .models import ScanType, TradeResult
from .scheduler import CET, run_scan

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Store last scan results in memory for fast API access ────────
_last_scans: dict[str, dict] = {}

bg_scheduler = BackgroundScheduler(timezone="Europe/Paris")


def _run_europe_scan() -> None:
    result = run_scan(ScanType.EUROPE)
    _last_scans["europe"] = result


def _run_us_scan() -> None:
    result = run_scan(ScanType.US)
    _last_scans["us"] = result


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schedule scans: 07:50 and 14:30 CET
    bg_scheduler.add_job(_run_europe_scan, CronTrigger(hour=7, minute=50, timezone="Europe/Paris"), id="europe_scan")
    bg_scheduler.add_job(_run_us_scan, CronTrigger(hour=14, minute=30, timezone="Europe/Paris"), id="us_scan")
    bg_scheduler.start()
    logger.info("Scheduler started — scans at 07:50 and 14:30 CET")
    yield
    bg_scheduler.shutdown()


app = FastAPI(title="OneShot News Trading", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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


@app.post("/api/scan/trigger/{scan_type}")
def trigger_scan(scan_type: str):
    """Manually trigger a scan (for testing or on-demand use)."""
    st = ScanType(scan_type)
    result = run_scan(st)
    _last_scans[scan_type] = result
    return result


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


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "scheduled_jobs": [j.id for j in bg_scheduler.get_jobs()],
    }
