"""Scheduler: triggers scans at 07:50 and 14:30 CET automatically."""

import logging
import time
from zoneinfo import ZoneInfo

from .learning import compute_learning_adjustments, save_trade
from .models import ScanType
from .news_collector import collect_all_news
from .news_scorer import score_news_batch
from .trade_selector import select_trade

logger = logging.getLogger(__name__)

PARIS_TZ = ZoneInfo("Europe/Paris")

# (#26) Cached learning adjustments — recalculated only after 22h journal
_cached_adjustments: dict[str, float] = {}
_cache_valid: bool = False


def invalidate_learning_cache() -> None:
    """Invalidate the learning cache (#26). Called after daily journal."""
    global _cache_valid
    _cache_valid = False
    logger.info("Learning cache invalidated")


def get_learning_adjustments() -> dict[str, float]:
    """Get learning adjustments with caching (#26)."""
    global _cached_adjustments, _cache_valid
    if not _cache_valid:
        _cached_adjustments = compute_learning_adjustments()
        _cache_valid = True
        logger.info("Learning cache refreshed: %d adjustments", len(_cached_adjustments))
    return _cached_adjustments


def run_scan(scan_type: ScanType, max_retries: int = 3, existing_trade_ticker: str | None = None) -> dict:
    """Execute a full scan pipeline: collect → score → select → save.

    Retries up to max_retries times with exponential backoff (#37).
    Returns the ScanResult as a dict.
    """
    for attempt in range(max_retries + 1):
        try:
            logger.info("=== Starting %s scan (attempt %d/%d) ===",
                        scan_type.value, attempt + 1, max_retries + 1)

            # Step 1: Collect news
            news_items = collect_all_news()
            logger.info("Collected %d news items", len(news_items))

            if not news_items:
                return {
                    "scan_type": scan_type.value,
                    "has_trade": False,
                    "reason_no_trade": "Aucune news collectee",
                    "news_analyzed": 0,
                }

            # Step 2: Score via Claude (now returns market_context too)
            scored, market_ctx = score_news_batch(news_items, scan_type)
            logger.info("Scored %d news items", len(scored))

            # Step 3: Get learning adjustments (#26 — cached)
            adjustments = get_learning_adjustments()

            # Step 4: Select trade (with correlation check #22)
            result = select_trade(
                scored, scan_type, adjustments,
                existing_trade_ticker=existing_trade_ticker,
                market_context=market_ctx,
            )

            # Step 5: Save if trade found
            if result.has_trade and result.recommendation:
                save_trade(result.recommendation)
                logger.info(
                    "Trade selected: %s %s %s (confidence: %d%%, volume: %s, binary: %s)",
                    result.recommendation.direction,
                    result.recommendation.ticker,
                    result.recommendation.asset_name,
                    result.recommendation.confidence,
                    result.recommendation.volume_confirmed,
                    result.recommendation.binary_event_warning or "none",
                )
            else:
                logger.info("No trade: %s", result.reason_no_trade)

            return result.model_dump(mode="json")

        except Exception as exc:
            logger.error("Scan %s failed (attempt %d/%d): %s",
                         scan_type.value, attempt + 1, max_retries + 1, exc)
            if attempt < max_retries:
                wait = 2 ** (attempt + 1)
                logger.info("Retrying in %ds...", wait)
                time.sleep(wait)
            else:
                logger.error("All %d attempts failed for %s scan", max_retries + 1, scan_type.value)
                return {
                    "scan_type": scan_type.value,
                    "has_trade": False,
                    "reason_no_trade": f"Scan echoue apres {max_retries + 1} tentatives: {exc}",
                    "news_analyzed": 0,
                }

    return {
        "scan_type": scan_type.value,
        "has_trade": False,
        "reason_no_trade": "Echec inattendu",
        "news_analyzed": 0,
    }
