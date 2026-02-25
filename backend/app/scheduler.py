"""Scheduler: triggers scans at 07:50 and 14:30 CET + event-driven scans every 30 min."""

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .event_scanner import determine_scan_type, should_trigger_scan
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


def run_event_check() -> dict | None:
    """Check for high-impact signals and trigger a scan if needed.

    Called every 30 minutes by the scheduler. Only triggers a full scan
    if high-impact keywords are detected in early-signal feeds.
    """
    should_trigger, triggers = should_trigger_scan()
    if not should_trigger:
        return None

    logger.info(
        "EVENT-DRIVEN SCAN: %d high-impact signals detected, triggering scan",
        len(triggers),
    )

    scan_type_str = determine_scan_type()
    scan_type = ScanType(scan_type_str)
    result = run_scan(scan_type)

    if result.get("has_trade"):
        logger.info("Event-driven scan produced a trade!")
    else:
        logger.info("Event-driven scan: no trade (reason: %s)", result.get("reason_no_trade", "unknown"))

    return result


def run_scan(scan_type: ScanType, max_retries: int = 2, existing_trade_ticker: str | None = None) -> dict:
    """Execute a full scan pipeline: collect → score → select → save.

    Retries up to max_retries times. No sleep between retries to avoid blocking
    the scheduler thread (which would cause other scheduled jobs to be missed).
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

            # Log collected headlines for traceability
            logger.info("--- Headlines collected (%d) ---", len(news_items))
            for i, item in enumerate(news_items, 1):
                age = ""
                if item.published:
                    age_h = (datetime.now(timezone.utc) - item.published).total_seconds() / 3600
                    age = f" [{age_h:.1f}h ago]"
                logger.info("  [%d] %s (src: %s, weight: %.2f)%s",
                            i, item.title, item.source, item.source_weight, age)

            # Step 2: Score via Claude (now returns market_context too)
            scored, market_ctx = score_news_batch(news_items, scan_type)
            logger.info("Scored %d news items", len(scored))

            # Log scored results with scores for traceability
            if scored:
                logger.info("--- Scored news ---")
                for s in sorted(scored, key=lambda x: x.get("score", 0), reverse=True):
                    logger.info("  score=%-6.1f dir=%-7s cat=%-15s delay=%-3s aware=%-3s | %s",
                                s.get("score", 0), s.get("direction", "?"),
                                s.get("news_category", "?"),
                                s.get("transmission_delay", "?"),
                                s.get("market_awareness", "?"),
                                s.get("headline", "?")[:100])

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
                logger.info("Retrying immediately (attempt %d)...", attempt + 2)
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
