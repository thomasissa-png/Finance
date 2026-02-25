"""Collects news from Yahoo Finance and RSS feeds."""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import feedparser
import requests
import yfinance as yf

from .config import ASSETS, DEFAULT_SOURCE_WEIGHT, EARLY_SIGNAL_FEEDS, NEWS_MAX_AGE_HOURS, RSS_FEEDS, SOURCE_WEIGHTS
from .data_apis import collect_structured_data
from .models import NewsItem

logger = logging.getLogger(__name__)

# Network timeout for RSS feed fetches (seconds).
# feedparser.parse(url) uses urllib with NO timeout internally,
# so we fetch via requests first, then parse the content.
RSS_FETCH_TIMEOUT = 10


def _get_source_weight(source: str) -> float:
    """Return reliability weight for a news source (#6)."""
    for key, weight in SOURCE_WEIGHTS.items():
        if key.lower() in source.lower():
            return weight
    return DEFAULT_SOURCE_WEIGHT


def _fetch_news_for_asset(asset) -> list[NewsItem]:
    """Fetch news for a single asset — designed to run in a thread."""
    items: list[NewsItem] = []
    try:
        ticker = yf.Ticker(asset.ticker)
        news = ticker.news or []
        for article in news:
            title = article.get("title", "")
            if not title:
                continue

            published = None
            pub_ts = article.get("providerPublishTime")
            if pub_ts:
                published = datetime.fromtimestamp(pub_ts, tz=timezone.utc)

            source = article.get("publisher", "Yahoo Finance")
            items.append(NewsItem(
                title=title,
                source=source,
                url=article.get("link", ""),
                published=published,
                related_tickers=[asset.ticker],
                source_weight=_get_source_weight(source),
            ))
    except Exception as exc:
        logger.warning("yfinance news error for %s: %s", asset.ticker, exc)
    return items


def collect_yfinance_news() -> list[NewsItem]:
    """Fetch news for all tracked assets via yfinance in parallel."""
    items: list[NewsItem] = []

    executor = ThreadPoolExecutor(max_workers=10)
    futures = {executor.submit(_fetch_news_for_asset, asset): asset for asset in ASSETS}
    try:
        for future in as_completed(futures, timeout=30):
            try:
                items.extend(future.result(timeout=10))
            except Exception as exc:
                asset = futures[future]
                logger.debug("yfinance news timeout/error for %s: %s", asset.ticker, exc)
    except TimeoutError:
        logger.warning("yfinance collection timed out, some assets skipped")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    return items


def _fetch_rss_feed(feed_url: str) -> list[NewsItem]:
    """Fetch news from a single RSS feed — designed to run in a thread.

    Uses requests.get() with a timeout instead of feedparser.parse(url)
    to avoid indefinite blocking on unresponsive servers.
    """
    items: list[NewsItem] = []
    try:
        resp = requests.get(feed_url, timeout=RSS_FETCH_TIMEOUT)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        feed_title = feed.feed.get("title", feed_url)
        for entry in feed.entries[:20]:
            title = entry.get("title", "")
            if not title:
                continue

            published = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published = datetime(
                    *entry.published_parsed[:6], tzinfo=timezone.utc
                )

            items.append(NewsItem(
                title=title,
                source=feed_title,
                url=entry.get("link", ""),
                published=published,
                source_weight=_get_source_weight(feed_title),
            ))
    except Exception as exc:
        logger.warning("RSS error for %s: %s", feed_url, exc)
    return items


def collect_rss_news() -> list[NewsItem]:
    """Fetch news from configured RSS feeds in parallel."""
    items: list[NewsItem] = []

    executor = ThreadPoolExecutor(max_workers=5)
    futures = {executor.submit(_fetch_rss_feed, url): url for url in RSS_FEEDS}
    try:
        for future in as_completed(futures, timeout=20):
            try:
                items.extend(future.result(timeout=10))
            except Exception as exc:
                url = futures[future]
                logger.debug("RSS feed timeout/error for %s: %s", url, exc)
    except TimeoutError:
        logger.warning("RSS collection timed out, some feeds skipped")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    return items


def collect_early_signal_news() -> list[NewsItem]:
    """Fetch news from early-signal feeds (meteo, OSINT, agri, shipping).

    These are Phase 1 sources — raw data before mainstream interpretation.
    Failures are silently logged (these feeds are best-effort, many may 404).
    """
    items: list[NewsItem] = []

    executor = ThreadPoolExecutor(max_workers=8)
    futures = {executor.submit(_fetch_rss_feed, url): url for url in EARLY_SIGNAL_FEEDS}
    try:
        for future in as_completed(futures, timeout=20):
            try:
                result = future.result(timeout=10)
                if result:
                    items.extend(result)
            except Exception as exc:
                url = futures[future]
                logger.debug("Early-signal feed unavailable %s: %s", url, exc)
    except TimeoutError:
        logger.warning("Early-signal collection timed out, some feeds skipped")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    if items:
        logger.info("Collected %d early-signal news items", len(items))
    return items


def _jaccard_similarity(a: str, b: str) -> float:
    """Jaccard similarity on word sets (#2)."""
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)


def _filter_old_news(items: list[NewsItem]) -> list[NewsItem]:
    """Pre-filter news older than max age BEFORE sending to Claude (#1)."""
    now = datetime.now(timezone.utc)
    filtered = []
    for item in items:
        if item.published is not None:
            age_hours = (now - item.published).total_seconds() / 3600
            if age_hours > NEWS_MAX_AGE_HOURS:
                continue
        filtered.append(item)
    if len(items) != len(filtered):
        logger.info("Pre-filtered %d old news (>%dh)", len(items) - len(filtered), NEWS_MAX_AGE_HOURS)
    return filtered


def _dedup_by_similarity(items: list[NewsItem], threshold: float = 0.75) -> list[NewsItem]:
    """Deduplicate news by Jaccard similarity (#2).

    Keeps the first (highest source weight) version of similar headlines.
    """
    # Sort by source_weight desc so we keep higher-quality sources
    sorted_items = sorted(items, key=lambda x: x.source_weight, reverse=True)
    unique: list[NewsItem] = []
    for item in sorted_items:
        is_dup = False
        for kept in unique:
            if _jaccard_similarity(item.title, kept.title) >= threshold:
                is_dup = True
                # Merge tickers from duplicate into kept
                for t in item.related_tickers:
                    if t not in kept.related_tickers:
                        kept.related_tickers.append(t)
                break
        if not is_dup:
            unique.append(item)
    if len(items) != len(unique):
        logger.info("Deduped %d similar news (Jaccard >= %.1f)", len(items) - len(unique), threshold)
    return unique


def collect_all_news() -> list[NewsItem]:
    """Aggregate news from all sources, pre-filtered and deduplicated.

    Sources (by priority):
    - Phase 0: Structured data APIs (EIA, Open-Meteo, USDA, GNews, COT, Options)
    - Phase 1: Early-signal RSS feeds (NOAA, USDA, EIA, gCaptain, etc.)
    - Phase 2: Yahoo Finance (per-ticker news)
    - Phase 3: Mainstream RSS (Reuters, CNBC, Investing.com)

    All 4 sources are fetched in parallel to reduce total collection time.
    Uses shutdown(wait=False) to avoid blocking if a source hangs.
    """
    executor = ThreadPoolExecutor(max_workers=4)
    future_yf = executor.submit(collect_yfinance_news)
    future_rss = executor.submit(collect_rss_news)
    future_early = executor.submit(collect_early_signal_news)
    future_structured = executor.submit(collect_structured_data)

    def _safe_get(future, name):
        try:
            return future.result(timeout=90)
        except Exception as exc:
            logger.warning("News source '%s' failed/timed out: %s", name, exc)
            return []

    yf_news = _safe_get(future_yf, "yfinance")
    rss_news = _safe_get(future_rss, "rss")
    early_news = _safe_get(future_early, "early-signal")
    structured_news = _safe_get(future_structured, "structured")

    executor.shutdown(wait=False, cancel_futures=True)

    all_items = structured_news + early_news + yf_news + rss_news

    # (#1) Pre-filter old news before sending to Claude
    all_items = _filter_old_news(all_items)

    # (#2) Smart dedup by Jaccard similarity
    unique = _dedup_by_similarity(all_items)

    logger.info(
        "Collected %d unique news (%d structured, %d early-signal, %d yfinance, %d rss, after pre-filter & dedup)",
        len(unique), len(structured_news), len(early_news), len(yf_news), len(rss_news),
    )
    return unique
