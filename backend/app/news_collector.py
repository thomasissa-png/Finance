"""Collects news from Yahoo Finance and RSS feeds."""

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import feedparser
import requests
import yfinance as yf  # Keep yfinance for news — Twelve Data has no news endpoint

from .config import (ASSETS, DEFAULT_SOURCE_WEIGHT, EARLY_SIGNAL_FEEDS,
                      NEWS_MAX_AGE_HOURS, RSS_FEEDS, SOURCE_WEIGHTS,
                      STRUCTURED_SOURCE_MAX_AGE_HOURS, STRUCTURED_SOURCES)
from .data_apis import collect_structured_data
from .models import NewsItem

logger = logging.getLogger(__name__)

# ── P1 Audit Scoring: keyword→ticker mapping for RSS feeds ──────────
# RSS feeds (Phase 1 & 3) don't provide related_tickers. This mapping
# infers tickers from headline keywords so that convergence detection
# in news_scorer._count_convergence() works even before Claude scoring.
# Only high-confidence mappings — Claude handles the rest.
_RSS_KEYWORD_TICKERS: list[tuple[list[str], list[str]]] = [
    # Energy
    (["crude oil", "wti", "petroleum", "oil stocks", "oil inventory"], ["CL=F", "BZ=F"]),
    (["brent"], ["BZ=F", "CL=F"]),
    (["natural gas", "lng", "ttf", "gas storage", "gas stocks"], ["NG=F"]),
    (["opec", "opec+", "oil output", "oil production cut"], ["CL=F", "BZ=F"]),
    (["refinery", "refining"], ["CL=F"]),
    # Metals
    (["gold", "bullion", "xau"], ["GC=F"]),
    (["silver", "xag"], ["SI=F"]),
    (["copper"], ["HG=F"]),
    (["platinum", "palladium", "pgm"], ["PL=F", "PA=F"]),
    # Agriculture
    (["corn", "maize", "mais", "milho"], ["ZC=F"]),
    (["wheat", "ble"], ["ZW=F"]),
    (["soybean", "soja", "soy"], ["ZS=F"]),
    (["coffee", "cafe", "café", "arabica", "robusta"], ["KC=F"]),
    (["sugar", "sucre", "sucrose"], ["SB=F"]),
    (["cocoa", "cacao"], ["CC=F"]),
    (["cotton", "coton"], ["CT=F"]),
    (["orange juice", "citrus", "oj futures"], ["OJ=F"]),
    # Livestock
    (["cattle", "beef", "betail", "bétail", "live cattle"], ["LE=F"]),
    (["hog", "pork", "swine", "porc"], ["HE=F"]),
    (["avian flu", "bird flu", "avian influenza"], ["HE=F", "LE=F"]),
    (["african swine fever", "asf outbreak"], ["HE=F"]),
    # Forex / Macro
    (["euro zone", "eurozone", "ecb", "lagarde"], ["EURUSD=X"]),
    (["bank of japan", "boj", "yen"], ["USDJPY=X"]),
    (["yuan", "renminbi", "pboc", "cnh"], ["USDCNH=X"]),
    # Indices
    (["s&p 500", "s&p500", "wall street"], ["^GSPC"]),
    (["nasdaq"], ["^IXIC"]),
    (["cac 40", "cac40", "euronext paris"], ["^FCHI"]),
    (["dax", "german stocks"], ["^GDAXI"]),
    (["ftse", "london stock"], ["^FTSE"]),
    (["nikkei"], ["^N225"]),
    # Shipping / Supply chain
    (["panama canal"], ["CL=F", "ZC=F", "ZS=F"]),
    (["suez canal", "red sea"], ["CL=F", "BZ=F"]),
    (["baltic dry", "freight rate", "shipping rate"], ["HG=F", "ZW=F", "ZC=F"]),
    # Geopolitical
    (["uranium", "nuclear"], ["URA"]),
    # French stocks (from early-signal feeds like ECB, BoE)
    (["lvmh"], ["MC.PA"]),
    (["totalenergies", "total energies"], ["TTE.PA"]),
    (["hermes", "hermès"], ["RMS.PA"]),
]


def _infer_tickers_from_title(title: str) -> list[str]:
    """Infer related tickers from RSS headline keywords.

    Returns a list of tickers that likely relate to this headline.
    Only high-confidence keyword matches — Claude handles nuanced cases.
    """
    title_lower = title.lower()
    tickers: list[str] = []
    for keywords, ticker_list in _RSS_KEYWORD_TICKERS:
        if any(kw in title_lower for kw in keywords):
            for t in ticker_list:
                if t not in tickers:
                    tickers.append(t)
    return tickers

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


def collect_yfinance_news() -> list[NewsItem]:
    """Fetch news for key tracked assets via yfinance — fully sequential.

    Replit OOM-kills the process when ThreadPoolExecutor is used here:
    after Phase 0 (structured) + Phase 1 (early-signal) have already consumed
    memory, even 3-worker yfinance pool for 49 assets is too much.

    Mitigations:
    1. Fully sequential — zero threads, one yfinance call at a time.
    2. Reduced to ~15 key tickers (commodities, energy, indices, gold)
       instead of all 49. Phase 2 is lower priority; Phase 0/1 already
       provide the edge signals. The 15 tickers cover the core universe.
    3. Hard timeout of 45s total — bail early if it's taking too long.
    """
    import time
    import gc

    # Key tickers covering core commodity/energy/index universe.
    # Euronext and minor forex are well-covered by Phase 0/1 RSS feeds.
    KEY_TICKERS = [
        "CL=F", "BZ=F", "NG=F", "GC=F",     # energy + gold
        "ZC=F", "ZW=F", "ZS=F", "KC=F", "CC=F",  # agriculture + soft
        "^GSPC", "^FCHI", "^DJI",             # major indices
        "EURUSD=X", "USDJPY=X",              # key forex
        "HG=F", "SI=F",                       # metals
    ]

    items: list[NewsItem] = []
    start = time.monotonic()
    timeout_secs = 75  # Increased from 45s — sequential 15 tickers need more time
    completed = 0

    # Build a lookup for asset metadata
    asset_by_ticker = {a.ticker: a for a in ASSETS}

    for ticker in KEY_TICKERS:
        if time.monotonic() - start > timeout_secs:
            logger.warning("yfinance news collection timed out after %ds (%d/%d tickers)",
                           timeout_secs, completed, len(KEY_TICKERS))
            break
        asset = asset_by_ticker.get(ticker)
        if not asset:
            continue
        try:
            t = yf.Ticker(ticker)
            news = t.news or []
            for article in news:
                title = article.get("title", "")
                if not title:
                    continue
                published = None
                pub_ts = article.get("providerPublishTime")
                if pub_ts:
                    published = datetime.fromtimestamp(pub_ts, tz=timezone.utc)
                source = article.get("publisher", "Yahoo Finance")
                # N10: Extract description from yfinance article for Claude context
                desc = ""
                if isinstance(article.get("relatedTickers"), list):
                    pass  # yfinance doesn't provide article body
                # Try to get summary/description from article
                for desc_key in ("summary", "description", "text"):
                    raw_desc = article.get(desc_key, "")
                    if raw_desc:
                        desc = raw_desc.strip()
                        if len(desc) > 200:
                            desc = desc[:197] + "..."
                        break
                items.append(NewsItem(
                    title=title,
                    source=source,
                    url=article.get("link", ""),
                    published=published,
                    related_tickers=[ticker],
                    source_weight=_get_source_weight(source),
                    description=desc,
                ))
            completed += 1
        except Exception as exc:
            logger.warning("yfinance news error for %s: %s", ticker, exc)

        # Free yfinance internal caches between tickers to reduce memory pressure
        gc.collect()

    logger.info("yfinance news: %d items from %d/%d tickers in %.1fs",
                len(items), completed, len(KEY_TICKERS), time.monotonic() - start)
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
                desc = re.sub(r"<[^>]+>", " ", desc).strip()
                desc = " ".join(desc.split())  # normalize whitespace
                if len(desc) > 200:
                    desc = desc[:197] + "..."

            # P1 Audit Scoring: infer tickers from headline keywords
            inferred_tickers = _infer_tickers_from_title(title)

            items.append(NewsItem(
                title=title,
                source=feed_title,
                url=entry.get("link", ""),
                published=published,
                related_tickers=inferred_tickers,
                source_weight=_get_source_weight(feed_title),
                description=desc,
            ))
    except Exception as exc:
        logger.warning("RSS error for %s: %s", feed_url, exc)
    return items


def collect_rss_news() -> list[NewsItem]:
    """Fetch news from configured RSS feeds in parallel."""
    items: list[NewsItem] = []

    executor = ThreadPoolExecutor(max_workers=5)
    futures = {executor.submit(_fetch_rss_feed, url): url for url in RSS_FEEDS}
    completed_count = 0
    try:
        for future in as_completed(futures, timeout=60):
            url = futures[future]
            try:
                result = future.result(timeout=25)
                items.extend(result)
                completed_count += 1
            except TimeoutError:
                logger.warning("RSS feed TIMEOUT (25s) for %s", url)
            except Exception as exc:
                logger.warning("RSS feed error for %s: %s", url, exc)
    except TimeoutError:
        logger.warning("RSS collection global TIMEOUT (60s), %d/%d feeds completed",
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

    # Workers increased to 5 (from 3), global timeout 90s (from 45s)
    # 25 feeds / 5 workers = 5 rounds max, plenty of time for DNS cold-starts
    executor = ThreadPoolExecutor(max_workers=5)
    futures = {executor.submit(_fetch_rss_feed, url): url for url in EARLY_SIGNAL_FEEDS}
    completed_count = 0
    try:
        for future in as_completed(futures, timeout=90):
            url = futures[future]
            try:
                result = future.result(timeout=25)
                if result:
                    items.extend(result)
                completed_count += 1
            except TimeoutError:
                logger.warning("Early-signal feed TIMEOUT (25s) for %s", url)
            except Exception as exc:
                logger.warning("Early-signal feed error for %s: %s", url, exc)
    except TimeoutError:
        logger.warning("Early-signal collection global TIMEOUT (90s), %d/%d feeds completed",
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
    """Pre-filter news older than max age BEFORE sending to Claude (#1).

    v7.7: Structured data sources (EIA, USDA, NOAA, etc.) use an extended
    window (18h) because they publish at fixed schedules — a USDA report at
    22:00 UTC is still relevant at the 07:50 CET scan the next morning.
    """
    now = datetime.now(timezone.utc)
    filtered = []
    for item in items:
        if item.published is not None:
            age_hours = (now - item.published).total_seconds() / 3600
            max_age = (STRUCTURED_SOURCE_MAX_AGE_HOURS
                       if item.source in STRUCTURED_SOURCES
                       else NEWS_MAX_AGE_HOURS)
            if age_hours > max_age:
                continue
        filtered.append(item)
    if len(items) != len(filtered):
        logger.info("Pre-filtered %d old news (>max age)", len(items) - len(filtered))
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

    Sources are fetched SEQUENTIALLY to avoid nested ThreadPoolExecutor on Replit.
    Each source internally uses max_workers=3, and we wait for full cleanup
    before starting the next source. This guarantees at most 3 active threads
    at any point, preventing Replit from killing the process.
    """
    import time as _time

    # v5.2: Source health tracking
    try:
        from .source_monitor import get_tracker
        tracker = get_tracker()
    except Exception:
        tracker = None

    def _safe_collect(fn, name, phase):
        t0 = _time.monotonic()
        try:
            result = fn()
            latency = (_time.monotonic() - t0) * 1000
            if tracker:
                tracker.record_success(name, phase, len(result), latency)
            return result
        except Exception as exc:
            latency = (_time.monotonic() - t0) * 1000
            logger.warning("News source '%s' failed: %s", name, exc)
            if tracker:
                tracker.record_failure(name, phase, exc, latency)
            return []

    # Sequential: each source completes (including thread cleanup) before next starts
    # Phase 0 sources are individually tracked in data_apis.collect_structured_data()
    structured_news = _safe_collect(collect_structured_data, "structured_data", "phase0")
    early_news = _safe_collect(collect_early_signal_news, "early_signal", "phase1")
    # yfinance news disabled 2026-03-10: returns 0 items in 43s (sequential fetch, empty API responses)
    # yfinance is still used for price data (market_data.py fallback), just not for news collection
    yf_news: list = []
    rss_news = _safe_collect(collect_rss_news, "rss_mainstream", "phase3")

    # Log per-source results for diagnostics (helps debug "0 news" issues)
    logger.info("News sources: structured=%d, early-signal=%d, yfinance=%d, rss=%d",
                len(structured_news), len(early_news), len(yf_news), len(rss_news))

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
