"""Scheduler: triggers scans at 07:50 and 14:30 CET automatically."""

import logging
from datetime import datetime, timezone, timedelta

from .learning import compute_learning_adjustments, save_trade
from .models import ScanType
from .news_collector import collect_all_news
from .news_scorer import score_news_batch
from .trade_selector import select_trade

logger = logging.getLogger(__name__)

# CET = UTC+1, CEST = UTC+2. We use a simple offset; in production
# consider pytz/zoneinfo for proper DST handling.
CET = timezone(timedelta(hours=1))


def run_scan(scan_type: ScanType) -> dict:
    """Execute a full scan pipeline: collect → score → select → save.

    Returns the ScanResult as a dict.
    """
    logger.info("=== Starting %s scan ===", scan_type.value)

    # Step 1: Collect news
    news_items = collect_all_news()
    logger.info("Collected %d news items", len(news_items))

    if not news_items:
        return {
            "scan_type": scan_type.value,
            "has_trade": False,
            "reason_no_trade": "Aucune news collectée",
            "news_analyzed": 0,
        }

    # Step 2: Score via Claude
    scored = score_news_batch(news_items, scan_type)
    logger.info("Scored %d news items", len(scored))

    # Step 3: Get learning adjustments
    adjustments = compute_learning_adjustments()

    # Step 4: Select trade
    result = select_trade(scored, scan_type, adjustments)

    # Step 5: Save if trade found
    if result.has_trade and result.recommendation:
        save_trade(result.recommendation)
        logger.info(
            "Trade selected: %s %s %s (confidence: %d%%)",
            result.recommendation.direction,
            result.recommendation.ticker,
            result.recommendation.asset_name,
            result.recommendation.confidence,
        )
    else:
        logger.info("No trade: %s", result.reason_no_trade)

    return result.model_dump(mode="json")
