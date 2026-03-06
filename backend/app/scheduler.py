"""Scheduler: triggers scans at 07:50 and 14:30 CET + event-driven scans every 30 min."""

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .event_scanner import determine_scan_type, should_trigger_scan
from .learning import compute_learning_adjustments, load_trades, save_trade
from .models import ScanType, TradeResult
from .news_collector import collect_all_news, _jaccard_similarity
from .news_scorer import score_news_batch
from .scan_history import append_scan_result, get_recently_scored_titles
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


def get_learning_adjustments() -> dict:
    """Get learning adjustments with caching (#26).

    v3.4: Returns the full learning dict with adjustments, session_adj,
    newscat_adj, regime_adj, and decomposition.
    Cache is invalidated after 22h journal — trades added during the day
    are NOT reflected until then (acceptable for 4 scans/day).
    """
    global _cached_adjustments, _cache_valid
    if not _cache_valid:
        _cached_adjustments = compute_learning_adjustments()
        _cache_valid = True
        adj = _cached_adjustments.get("adjustments", {}) if isinstance(_cached_adjustments, dict) else _cached_adjustments
        logger.info("Learning cache refreshed: %d adjustments", len(adj))
    return _cached_adjustments


def _get_pending_trade_tickers() -> list[str]:
    """Get tickers from today's PENDING trades for correlation checking.

    Event-driven scans need to know what's already been traded today
    to avoid duplicate/correlated positions.
    """
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

    # Load existing trade tickers to avoid duplicate/correlated positions
    existing_tickers = _get_pending_trade_tickers()
    if existing_tickers:
        logger.info("Event-driven scan: existing pending tickers today: %s", existing_tickers)

    result = run_scan(scan_type, existing_trade_ticker=existing_tickers or None)

    if result.get("has_trade"):
        logger.info("Event-driven scan produced a trade!")
    else:
        logger.info("Event-driven scan: no trade (reason: %s)", result.get("reason_no_trade", "unknown"))

    return result


def run_scan(scan_type: ScanType, max_retries: int = 1, existing_trade_ticker: list[str] | str | None = None) -> dict:
    """Execute a full scan pipeline: collect → score → select → save.

    Retries up to max_retries times for non-API errors (file I/O, etc.).
    API errors are handled inside the scorer with their own retry logic —
    the scorer returns [] on API failure so the scan completes gracefully.
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
                    "reason_no_trade": "Aucune news collectée",
                    "news_analyzed": 0,
                }

            # Step 1b: Cross-scan dedup — skip headlines already scored in recent scans
            recently_scored = get_recently_scored_titles()
            if recently_scored:
                before_dedup = len(news_items)
                filtered = []
                for item in news_items:
                    is_dup = False
                    for scored_title in recently_scored:
                        if _jaccard_similarity(item.title, scored_title) >= 0.65:
                            is_dup = True
                            break
                    if not is_dup:
                        filtered.append(item)
                news_items = filtered
                if before_dedup != len(news_items):
                    logger.info("Cross-scan dedup: filtered %d already-scored headlines (%d → %d)",
                                before_dedup - len(news_items), before_dedup, len(news_items))

            if not news_items:
                return {
                    "scan_type": scan_type.value,
                    "has_trade": False,
                    "reason_no_trade": "Toutes les news ont déjà été scorées dans les scans précédents",
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

            if not scored:
                reason = "API Claude hors service — les news n'ont pas pu etre scorees. Reessai au prochain scan."
                logger.warning(reason)
                result_dict = {
                    "scan_type": scan_type.value,
                    "has_trade": False,
                    "reason_no_trade": reason,
                    "news_analyzed": len(news_items),
                    "all_scored_news": [],
                }
                append_scan_result(result_dict)
                return result_dict

            # Log scored results with scores for traceability
            if scored:
                logger.info("--- Scored news (%d) ---", len(scored))
                for s in scored:  # already sorted by total_score desc
                    logger.info("  score=%-6.1f dir=%-7s cat=%-15s delay=%-3d aware=%-3d | %s",
                                s.total_score, s.direction.value,
                                s.news_category,
                                s.transmission_delay,
                                s.market_awareness,
                                s.news.title[:100])

            # Step 3: Get learning adjustments (#26 — cached)
            learning_data = get_learning_adjustments()

            # Step 4: Select trade (with correlation check #22)
            result = select_trade(
                scored, scan_type, learning_data,
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

            result_dict = result.model_dump(mode="json")

            # Persist full scan result to history (all scored news + rejections)
            append_scan_result(result_dict)

            return result_dict

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
                    "reason_no_trade": f"Scan échoué après {max_retries + 1} tentatives: {exc}",
                    "news_analyzed": 0,
                }

    return {
        "scan_type": scan_type.value,
        "has_trade": False,
        "reason_no_trade": "Échec inattendu",
        "news_analyzed": 0,
    }
