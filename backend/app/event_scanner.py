"""Event-driven scanner: monitors feeds every 30 min and triggers scans on high-impact keywords.

In addition to the 4 scheduled scans (07:50, 11:15, 14:50, 17:00), this module
runs a lightweight check every 30 minutes. If it detects a high-potential signal
in early-signal feeds, it triggers a full scan immediately (respecting cooldown).
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from zoneinfo import ZoneInfo

import feedparser
import requests

from .config import EARLY_SIGNAL_FEEDS, TRIGGER_COOLDOWN_SECONDS

logger = logging.getLogger(__name__)

PARIS_TZ = ZoneInfo("Europe/Paris")

# Browser User-Agent — some feeds block python-requests default UA
_RSS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

# Category-specific cooldowns (seconds) — geopolitical escalates fast, weather is slower
CATEGORY_COOLDOWNS: dict[str, int] = {
    "geopolitical": 60,     # 1 min — wars/strikes escalate in seconds
    "supply_chain": 120,    # 2 min — port closures, pipeline attacks develop fast
    "commodity": 300,       # 5 min — commodity data is periodic
    "weather": 300,         # 5 min — weather evolves slowly
}

# ── High-impact keywords that trigger immediate scans ─────────────
# Organized by category with associated weight
HIGH_IMPACT_KEYWORDS: dict[str, list[str]] = {
    "weather": [
        "drought", "frost", "freeze", "hurricane", "typhoon", "cyclone",
        "flood", "flooding", "heatwave", "heat wave", "wildfire",
        "secheresse", "gel", "inondation", "canicule", "ouragan",
        "blizzard", "tornado", "severe weather",
        # NHC / tropical storm specifics
        "tropical storm", "tropical depression", "hurricane warning",
        "storm surge", "category 4", "category 5",
        # Agricultural weather
        "crop damage", "crop destruction", "el nino", "la nina",
        "monsoon failure", "record heat", "record cold",
        # Volcanic / seismic
        "volcanic eruption", "eruption", "earthquake",
    ],
    "supply_chain": [
        "port closed", "port congestion", "canal blocked", "shipping disruption",
        "pipeline explosion", "pipeline attack", "refinery fire", "refinery shut",
        "mine collapse", "mine strike", "production halt", "supply disruption",
        "export ban", "export restriction", "embargo",
        "suez", "panama canal", "strait of hormuz", "bab el-mandeb",
        # Shipping / freight
        "container shortage", "freight rate surge", "baltic dry",
        "vessel grounding", "ship collision", "maritime accident",
        "lng terminal", "lng tanker", "gas pipeline",
        # Storage / inventory
        "storage capacity", "tank farm", "strategic reserve",
    ],
    "geopolitical": [
        "military strike", "missile attack", "air strike", "invasion",
        "sanctions imposed", "new sanctions", "nuclear test", "nuclear threat",
        "coup", "regime change", "border clash", "war declaration",
        "ceasefire", "peace deal", "troops deployed", "naval blockade",
        "drone attack", "terrorist attack",
        # Defense / military
        "military buildup", "military deployment", "carrier strike group",
        "no-fly zone", "military exercise", "arms deal",
    ],
    "commodity": [
        "opec cut", "opec+ cut", "production cut", "output reduction",
        "crop failure", "harvest failure", "stockpile draw", "inventory draw",
        "record low stocks", "supply shortage", "shortage",
        "record export", "export surge", "demand surge",
        # Gas / LNG
        "gas storage", "ttf price", "lng shortage", "gas injection",
        # Agricultural / palm oil / coffee
        "palm oil export", "coffee frost", "sugar crop",
        "soybean import", "wheat export ban", "corn harvest delay",
        # China demand
        "china import", "china commodity", "china stockpile",
    ],
}

# Keywords that warrant IMMEDIATE scan (confirmed high-impact events)
# vs keywords that are informational (should trigger but with lower urgency)
HIGH_PRIORITY_KEYWORDS: set[str] = {
    # Confirmed events with clear directional impact
    "hurricane warning", "category 4", "category 5", "storm surge",
    "pipeline explosion", "port closed", "canal blocked", "refinery fire",
    "military strike", "missile attack", "air strike", "invasion",
    "sanctions imposed", "nuclear test", "naval blockade",
    "crop failure", "harvest failure", "production halt", "export ban",
    "drought", "frost", "freeze", "wildfire",
    "opec cut", "opec+ cut",
}

# Flatten for quick lookup
ALL_KEYWORDS: list[tuple[str, str]] = []
for category, keywords in HIGH_IMPACT_KEYWORDS.items():
    for kw in keywords:
        ALL_KEYWORDS.append((kw.lower(), category))

# Track last trigger time per category to respect category-specific cooldowns
_last_event_trigger: float = 0.0
_last_category_trigger: dict[str, float] = {}

# Track seen headlines to avoid re-triggering on the same news
_seen_headlines: set[str] = set()
_MAX_SEEN = 500  # Prevent unbounded growth


def _check_headline_for_triggers(title: str) -> list[tuple[str, str]]:
    """Check if a headline contains high-impact keywords.

    Returns list of (keyword, category) matches.
    """
    title_lower = title.lower()
    matches = []
    for keyword, category in ALL_KEYWORDS:
        if keyword in title_lower:
            matches.append((keyword, category))
    return matches


def _fetch_feed_triggers(feed_url: str, seen: set[str]) -> list[dict]:
    """Fetch a single feed and extract trigger matches — designed to run in a thread."""
    results: list[dict] = []
    try:
        resp = requests.get(feed_url, timeout=15, headers=_RSS_HEADERS)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        for entry in feed.entries[:10]:
            title = entry.get("title", "")
            if not title or title in seen:
                continue

            matches = _check_headline_for_triggers(title)
            if matches:
                keywords = [m[0] for m in matches]
                categories = list(set(m[1] for m in matches))
                results.append({
                    "title": title,
                    "source": feed.feed.get("title", feed_url),
                    "url": entry.get("link", ""),
                    "keywords": keywords,
                    "categories": categories,
                })
    except Exception as exc:
        logger.debug("Event scan feed error %s: %s", feed_url, exc)
    return results


def scan_feeds_for_triggers() -> list[dict]:
    """Quick scan of early-signal feeds for high-impact headlines.

    This is a lightweight check — only parses RSS titles,
    doesn't call Claude or fetch prices. Feeds are fetched in parallel.

    Returns list of trigger events: [{title, source, keywords, categories}]
    """
    global _seen_headlines

    triggers: list[dict] = []

    # Prune seen headlines if too many
    if len(_seen_headlines) > _MAX_SEEN:
        _seen_headlines = set(list(_seen_headlines)[-_MAX_SEEN // 2:])

    # Snapshot seen headlines for thread-safe read (writes happen after)
    seen_snapshot = set(_seen_headlines)

    executor = ThreadPoolExecutor(max_workers=8)
    futures = {executor.submit(_fetch_feed_triggers, url, seen_snapshot): url for url in EARLY_SIGNAL_FEEDS}
    try:
        for future in as_completed(futures, timeout=30):
            try:
                results = future.result(timeout=5)
                for r in results:
                    if r["title"] not in _seen_headlines:
                        _seen_headlines.add(r["title"])
                        triggers.append(r)
            except Exception as exc:
                logger.debug("Event scan future error: %s", exc)
    except TimeoutError:
        logger.warning("Event scan timed out, some feeds skipped")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    return triggers


def should_trigger_scan() -> tuple[bool, list[dict]]:
    """Determine if we should trigger an immediate scan.

    Checks:
    1. Are we within trading hours? (07:00-20:00 CET)
    2. Are there high-impact signals in the feeds?
    3. Has enough time passed since last trigger? (category-specific cooldown)

    Uses category-specific cooldowns: geopolitical=60s, supply_chain=120s, others=300s.
    High-priority keywords (confirmed events) can bypass the general cooldown.

    Returns (should_trigger, trigger_events).
    """
    global _last_event_trigger, _last_category_trigger

    now = datetime.now(PARIS_TZ)

    # No scans on weekends — markets closed
    if now.weekday() >= 5:  # 5=Saturday, 6=Sunday
        return False, []

    # Only trigger during trading hours (07:00-19:30 CET)
    if now.hour < 7 or (now.hour >= 19 and now.minute >= 30):
        return False, []

    # Hard minimum cooldown: 30s between any triggers (prevent spam)
    elapsed_global = time.time() - _last_event_trigger
    if elapsed_global < 30:
        return False, []

    triggers = scan_feeds_for_triggers()

    if not triggers:
        return False, []

    # Filter triggers by category-specific cooldowns
    current_time = time.time()
    filtered_triggers = []
    for t in triggers:
        trigger_categories = t.get("categories", [])
        trigger_keywords = [kw.lower() for kw in t.get("keywords", [])]

        # High-priority keywords bypass category cooldown (only respect 30s global minimum)
        is_high_priority = any(kw in HIGH_PRIORITY_KEYWORDS for kw in trigger_keywords)

        # Check category cooldown
        should_include = is_high_priority  # High priority always passes
        if not should_include:
            for cat in trigger_categories:
                cat_cooldown = CATEGORY_COOLDOWNS.get(cat, TRIGGER_COOLDOWN_SECONDS)
                last_cat_trigger = _last_category_trigger.get(cat, 0.0)
                if current_time - last_cat_trigger >= cat_cooldown:
                    should_include = True
                    break

        if should_include:
            filtered_triggers.append(t)

    if not filtered_triggers:
        return False, []

    # Log what we found
    for t in filtered_triggers:
        priority = "HIGH-PRIORITY" if any(kw.lower() in HIGH_PRIORITY_KEYWORDS for kw in t.get("keywords", [])) else "SIGNAL"
        logger.info(
            "%s: '%s' (source: %s, keywords: %s, categories: %s)",
            priority, t["title"], t["source"], t["keywords"], t["categories"],
        )

    # Update cooldown timestamps
    _last_event_trigger = current_time
    for t in filtered_triggers:
        for cat in t.get("categories", []):
            _last_category_trigger[cat] = current_time

    return True, filtered_triggers


def determine_scan_type() -> str:
    """Determine which scan type to run based on current time.

    Before 14:00 CET → europe scan (European markets open)
    After 14:00 CET → us scan (US markets about to open/open)
    """
    now = datetime.now(PARIS_TZ)
    if now.hour < 14:
        return "europe"
    return "us"
