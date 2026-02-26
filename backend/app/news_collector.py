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

# Max news items to send to Claude per scan.
# 50 items ~= 5,200 input tokens → ~30s processing → well within 90s timeout.
# Phase 0/1 premium sources naturally rank higher and always make the cut.
MAX_NEWS_PER_SCAN = 50

# Network timeout for RSS feed fetches (seconds).
# feedparser.parse(url) uses urllib with NO timeout internally,
# so we fetch via requests first, then parse the content.
# Raised from 15→20s: Replit cold starts have slow DNS/network, 15s was too tight.
RSS_FETCH_TIMEOUT = 20

# Browser User-Agent — many feeds (CNBC, gCaptain, BoE) return 403
# when they see the default "python-requests/x.y.z" User-Agent.
_RSS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


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

    # max_workers=3: Replit kills process on too many concurrent threads.
    # 49 assets / 3 workers = sequential batches. Slower but stable on Replit.
    executor = ThreadPoolExecutor(max_workers=3)
    futures = {executor.submit(_fetch_news_for_asset, asset): asset for asset in ASSETS}
    completed_count = 0
    try:
        for future in as_completed(futures, timeout=60):
            try:
                items.extend(future.result(timeout=15))
                completed_count += 1
            except Exception as exc:
                asset = futures[future]
                logger.debug("yfinance news timeout/error for %s: %s", asset.ticker, exc)
    except TimeoutError:
        logger.warning("yfinance collection timed out after 60s (%d/%d assets completed)",
                       completed_count, len(ASSETS))
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
        resp = requests.get(feed_url, timeout=RSS_FETCH_TIMEOUT, headers=_RSS_HEADERS)
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

            # Extract description/summary for Claude context
            desc = entry.get("summary", "") or entry.get("description", "")
            # Strip HTML tags and truncate
            if desc:
                import re
                desc = re.sub(r"<[^>]+>", " ", desc).strip()
                desc = " ".join(desc.split())  # normalize whitespace
                if len(desc) > 200:
                    desc = desc[:197] + "..."

            items.append(NewsItem(
                title=title,
                source=feed_title,
                url=entry.get("link", ""),
                published=published,
                source_weight=_get_source_weight(feed_title),
                description=desc,
            ))
    except Exception as exc:
        logger.warning("RSS error for %s: %s", feed_url, exc)
    return items


def collect_rss_news() -> list[NewsItem]:
    """Fetch news from configured RSS feeds in parallel."""
    items: list[NewsItem] = []

    executor = ThreadPoolExecutor(max_workers=3)
    futures = {executor.submit(_fetch_rss_feed, url): url for url in RSS_FEEDS}
    completed_count = 0
    try:
        for future in as_completed(futures, timeout=45):
            try:
                result = future.result(timeout=20)
                items.extend(result)
                completed_count += 1
            except Exception as exc:
                url = futures[future]
                logger.warning("RSS feed timeout/error for %s: %s", url, exc)
    except TimeoutError:
        logger.warning("RSS collection timed out after 45s (%d/%d feeds completed)",
                       completed_count, len(RSS_FEEDS))
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    if not items and RSS_FEEDS:
        logger.warning("RSS collection returned 0 items from %d feeds", len(RSS_FEEDS))

    return items


def collect_early_signal_news() -> list[NewsItem]:
    """Fetch news from early-signal feeds (meteo, OSINT, agri, shipping).

    These are Phase 1 sources — raw data before mainstream interpretation.
    Failures are silently logged (these feeds are best-effort, many may 404).
    """
    items: list[NewsItem] = []

    executor = ThreadPoolExecutor(max_workers=3)
    futures = {executor.submit(_fetch_rss_feed, url): url for url in EARLY_SIGNAL_FEEDS}
    completed_count = 0
    try:
        for future in as_completed(futures, timeout=45):
            try:
                result = future.result(timeout=20)
                if result:
                    items.extend(result)
                completed_count += 1
            except Exception as exc:
                url = futures[future]
                logger.warning("Early-signal feed unavailable %s: %s", url, exc)
    except TimeoutError:
        logger.warning("Early-signal collection timed out after 45s (%d/%d feeds completed)",
                       completed_count, len(EARLY_SIGNAL_FEEDS))
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    if items:
        logger.info("Collected %d early-signal news items", len(items))
    elif EARLY_SIGNAL_FEEDS:
        logger.warning("Early-signal collection returned 0 items from %d feeds", len(EARLY_SIGNAL_FEEDS))
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


def _dedup_by_similarity(items: list[NewsItem], threshold: float = 0.65) -> list[NewsItem]:
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


def _pre_filter_for_scoring(items: list[NewsItem], max_items: int = MAX_NEWS_PER_SCAN) -> list[NewsItem]:
    """Heuristic pre-filter to cap items before sending to Claude API.

    Without this, 100+ items cause API timeouts (~3 min per attempt).
    Prioritizes by source weight (Phase 0/1 always make the cut),
    freshness, and context richness.
    """
    if len(items) <= max_items:
        return items

    now = datetime.now(timezone.utc)

    def _priority(item: NewsItem) -> float:
        # Source weight dominates: Phase 0/1 (>1.0) always rank above Phase 2/3
        score = item.source_weight * 100

        # Freshness boost
        if item.published:
            age_h = (now - item.published).total_seconds() / 3600
            if age_h <= 2:
                score += 30
            elif age_h <= 4:
                score += 15
        else:
            score += 10  # Unknown age — don't penalize

        # Description = more context for Claude scoring
        if item.description:
            score += 10

        # Related tickers = more actionable
        if item.related_tickers:
            score += 5

        return score

    sorted_items = sorted(items, key=_priority, reverse=True)
    filtered = sorted_items[:max_items]

    logger.info(
        "Pre-filtered %d -> %d items for Claude scoring (dropped %d low-priority)",
        len(items), len(filtered), len(items) - len(filtered),
    )
    return filtered


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

    structured_news = _safe_get(future_structured, "structured")
    early_news = _safe_get(future_early, "early-signal")
    yf_news = _safe_get(future_yf, "yfinance")
    rss_news = _safe_get(future_rss, "rss")

    # Log per-source results for diagnostics (helps debug "0 news" issues)
    logger.info("News sources: structured=%d, early-signal=%d, yfinance=%d, rss=%d",
                len(structured_news), len(early_news), len(yf_news), len(rss_news))

    executor.shutdown(wait=False, cancel_futures=True)

    all_items = structured_news + early_news + yf_news + rss_news

    # (#1) Pre-filter old news before sending to Claude
    all_items = _filter_old_news(all_items)

    # (#2) Smart dedup by Jaccard similarity
    unique = _dedup_by_similarity(all_items)

    # Cap items to avoid Claude API timeout on large batches (111+ items → timeout)
    unique = _pre_filter_for_scoring(unique)

    logger.info(
        "Collected %d news for scoring (%d structured, %d early-signal, %d yfinance, %d rss, after pre-filter & dedup)",
        len(unique), len(structured_news), len(early_news), len(yf_news), len(rss_news),
    )
    return unique
