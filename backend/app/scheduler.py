"""Scheduler: orchestrates agents for scans, event checks, and learning.

v6.0: Delegates to the agent registry instead of calling modules directly.
The agents handle their own logging, status tracking, and bus communication.
This module remains the entry point for APScheduler jobs.
"""

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .agents.registry import (
    get_learning_adjustments,
    invalidate_learning_cache,
    run_event_check,
    run_scan_pipeline,
)
from .learning import load_trades, save_trade
from .models import ScanType, TradeResult

logger = logging.getLogger(__name__)

PARIS_TZ = ZoneInfo("Europe/Paris")


def _get_pending_trade_tickers() -> list[str]:
    """Get tickers from today's PENDING trades for correlation checking."""
    try:
        now = datetime.now(PARIS_TZ)
        today = now.strftime("%Y-%m-%d")
        trades = load_trades()
        tickers = []
        for t in trades:
            if t.result == TradeResult.PENDING and t.timestamp.strftime("%Y-%m-%d") == today:
                tickers.append(t.ticker)
        return tickers
    except Exception as exc:
        logger.warning("Failed to load pending trade tickers: %s", exc)
        return []


def run_scan(scan_type: ScanType, max_retries: int = 1,
             existing_trade_ticker: list[str] | str | None = None) -> dict:
    """Execute a full scan pipeline via agents: News → Scoring → Trader.

    v6.0: Delegates to run_scan_pipeline() which chains the agents.
    Retries on non-API errors.
    """
    for attempt in range(max_retries + 1):
        try:
            logger.info("=== Starting %s scan (attempt %d/%d) ===",
                        scan_type.value, attempt + 1, max_retries + 1)

            result = run_scan_pipeline(scan_type, existing_trade_ticker)
            return result

        except Exception as exc:
            logger.error("Scan %s failed (attempt %d/%d): %s",
                         scan_type.value, attempt + 1, max_retries + 1, exc)
            if attempt < max_retries:
                logger.info("Retrying immediately (attempt %d)...", attempt + 2)
            else:
                logger.error("All %d attempts failed for %s scan",
                             max_retries + 1, scan_type.value)
                return {
                    "scan_type": scan_type.value,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "has_trade": False,
                    "reason_no_trade": f"Scan échoué après {max_retries + 1} tentatives: {exc}",
                    "news_analyzed": 0,
                }

    return {
        "scan_type": scan_type.value,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "has_trade": False,
        "reason_no_trade": "Échec inattendu",
        "news_analyzed": 0,
    }
