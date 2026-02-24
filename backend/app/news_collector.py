"""Collects news from Yahoo Finance and RSS feeds."""

import logging
from datetime import datetime, timezone

import feedparser
import yfinance as yf

from .config import ASSETS, RSS_FEEDS
from .models import NewsItem

logger = logging.getLogger(__name__)


def collect_yfinance_news() -> list[NewsItem]:
    """Fetch news for all tracked assets via yfinance."""
    items: list[NewsItem] = []
    seen_titles: set[str] = set()

    for asset in ASSETS:
        try:
            ticker = yf.Ticker(asset.ticker)
            news = ticker.news or []
            for article in news:
                title = article.get("title", "")
                if not title or title in seen_titles:
                    continue
                seen_titles.add(title)

                published = None
                pub_ts = article.get("providerPublishTime")
                if pub_ts:
                    published = datetime.fromtimestamp(pub_ts, tz=timezone.utc)

                items.append(NewsItem(
                    title=title,
                    source=article.get("publisher", "Yahoo Finance"),
                    url=article.get("link", ""),
                    published=published,
                    related_tickers=[asset.ticker],
                ))
        except Exception as exc:
            logger.warning("yfinance news error for %s: %s", asset.ticker, exc)

    return items


def collect_rss_news() -> list[NewsItem]:
    """Fetch news from configured RSS feeds."""
    items: list[NewsItem] = []
    seen_titles: set[str] = set()

    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:20]:
                title = entry.get("title", "")
                if not title or title in seen_titles:
                    continue
                seen_titles.add(title)

                published = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    published = datetime(
                        *entry.published_parsed[:6], tzinfo=timezone.utc
                    )

                items.append(NewsItem(
                    title=title,
                    source=feed.feed.get("title", feed_url),
                    url=entry.get("link", ""),
                    published=published,
                ))
        except Exception as exc:
            logger.warning("RSS error for %s: %s", feed_url, exc)

    return items


def collect_all_news() -> list[NewsItem]:
    """Aggregate news from all sources, deduplicated."""
    yf_news = collect_yfinance_news()
    rss_news = collect_rss_news()

    all_items = yf_news + rss_news
    seen: set[str] = set()
    unique: list[NewsItem] = []
    for item in all_items:
        key = item.title.lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(item)

    logger.info(
        "Collected %d unique news items (%d yfinance, %d rss)",
        len(unique), len(yf_news), len(rss_news),
    )
    return unique
