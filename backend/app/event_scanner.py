"""Event-driven scanner: monitors feeds every 10 min and triggers scans on high-impact keywords.

In addition to the 4 scheduled scans (07:50, 11:15, 14:50, 17:00), this module
runs a lightweight check every 10 minutes. If it detects a high-potential signal
in early-signal feeds, it triggers a full scan immediately (respecting cooldown).
"""

import logging
import re
import threading
import time
from collections import OrderedDict
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
        # T5: Copper/mining supply chain — critical for trend ticker HG=F
        "copper shortage", "copper supply", "smelter fire", "smelter outage",
        "mine closure", "mining strike", "copper mine",
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
        # T5: Copper/cocoa/coffee commodity signals
        "copper demand", "copper deficit", "cocoa shortage", "coffee frost",
        "coffee leaf rust",
        # Livestock disease — major supply shocks (swine fever, avian flu, BSE)
        "african swine fever", "avian flu", "bird flu", "avian influenza",
        "foot-and-mouth", "foot and mouth", "bse", "mad cow",
        "screwworm", "herd liquidation", "livestock disease",
        "cattle disease", "swine fever", "hog disease",
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
    # Livestock disease outbreaks — massive supply shocks
    "african swine fever", "avian flu", "bird flu", "foot-and-mouth",
    "bse", "mad cow", "herd liquidation",
    # T5: Copper/mining confirmed events
    "mine collapse", "smelter fire", "copper shortage",
}

# Flatten for quick lookup
ALL_KEYWORDS: list[tuple[str, str]] = []
for category, keywords in HIGH_IMPACT_KEYWORDS.items():
    for kw in keywords:
        ALL_KEYWORDS.append((kw.lower(), category))

# N6: Thread lock for all global state — event scanner can be called
# from scheduler thread + manual trigger concurrently
_scanner_lock = threading.Lock()

# Track last trigger time per category to respect category-specific cooldowns
_last_event_trigger: float = 0.0
_last_category_trigger: dict[str, float] = {}

# Track seen headlines to avoid re-triggering on the same news (OrderedDict preserves insertion order)
_seen_headlines: OrderedDict = OrderedDict()
_MAX_SEEN = 500  # Prevent unbounded growth

# Per-keyword cooldown for HIGH_PRIORITY keywords (min 10 min between same keyword triggers)
_last_keyword_trigger: dict[str, float] = {}
_KEYWORD_COOLDOWN = 600  # 10 minutes in seconds


def _check_headline_for_triggers(title: str) -> list[tuple[str, str]]:
    """Check if a headline contains high-impact keywords.

    Returns list of (keyword, category) matches.
    """
    title_lower = title.lower()
    matches = []
    for keyword, category in ALL_KEYWORDS:
        if re.search(r'\b' + re.escape(keyword) + r'\b', title_lower):
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

    # N6: Lock for pruning + snapshot of seen headlines
    with _scanner_lock:
        # Prune seen headlines if too many (remove oldest entries)
        while len(_seen_headlines) > _MAX_SEEN:
            _seen_headlines.popitem(last=False)

        # Snapshot seen headlines for thread-safe read (writes happen after)
        seen_snapshot = set(_seen_headlines)

    # Workers increased to 5 (from 3) — 25 feeds / 5 workers = 5 rounds max
    # Global timeout increased to 60s (from 30s) — allows more feeds to complete
    # N6: Collect all results first, then update _seen_headlines under lock
    all_results: list[dict] = []
    executor = ThreadPoolExecutor(max_workers=5)
    futures = {executor.submit(_fetch_feed_triggers, url, seen_snapshot): url for url in EARLY_SIGNAL_FEEDS}
    completed_count = 0
    try:
        for future in as_completed(futures, timeout=60):
            url = futures[future]
            try:
                results = future.result(timeout=20)
                completed_count += 1
                all_results.extend(results)
            except TimeoutError:
                logger.warning("Event scan: feed timeout (20s) for %s", url)
            except Exception as exc:
                logger.warning("Event scan: feed error for %s: %s", url, exc)
    except TimeoutError:
        logger.warning("Event scan: global timeout (60s), %d/%d feeds completed",
                       completed_count, len(futures))
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    # N6: Update _seen_headlines under lock after thread pool completes
    if all_results:
        with _scanner_lock:
            for r in all_results:
                if r["title"] not in _seen_headlines:
                    _seen_headlines[r["title"]] = None
                    triggers.append(r)

    return triggers


def should_trigger_scan() -> tuple[bool, list[dict]]:
    """Determine if we should trigger an immediate scan.

    Checks:
    1. Are we within trading hours? (07:00-19:30 CET)
    2. Are there high-impact signals in the feeds?
    3. Has enough time passed since last trigger? (category-specific cooldown)

    Uses category-specific cooldowns: geopolitical=60s, supply_chain=120s, others=300s.
    High-priority keywords (confirmed events) can bypass the general cooldown.

    Returns (should_trigger, trigger_events).
    """
    global _last_event_trigger, _last_category_trigger, _last_keyword_trigger

    now = datetime.now(PARIS_TZ)

    # No scans on weekends — markets closed
    if now.weekday() >= 5:  # 5=Saturday, 6=Sunday
        return False, []

    # N7: Fixed doc — code correctly uses 19:30, not 20:00
    # Only trigger during trading hours (07:00-19:30 CET)
    if now.hour < 7 or now.hour > 19 or (now.hour == 19 and now.minute >= 30):
        return False, []

    # N6: Lock for reading/writing cooldown timestamps
    with _scanner_lock:
        # Hard minimum cooldown: 30s between any triggers (prevent spam)
        elapsed_global = time.time() - _last_event_trigger
        if elapsed_global < 30:
            return False, []

    triggers = scan_feeds_for_triggers()

    if not triggers:
        return False, []

    # N6: Lock for reading/writing cooldown timestamps (filter + update)
    current_time = time.time()
    filtered_triggers = []
    with _scanner_lock:
        for t in triggers:
            trigger_categories = t.get("categories", [])
            trigger_keywords = [kw.lower() for kw in t.get("keywords", [])]

            # High-priority keywords bypass category cooldown (only respect 30s global minimum)
            # But enforce per-keyword cooldown of 10 min to avoid repeated triggers on same keyword
            high_priority_keywords = [kw for kw in trigger_keywords if kw in HIGH_PRIORITY_KEYWORDS]
            is_high_priority = False
            if high_priority_keywords:
                # Check per-keyword cooldown: at least one HIGH_PRIORITY keyword must not be on cooldown
                for kw in high_priority_keywords:
                    last_kw_time = _last_keyword_trigger.get(kw, 0.0)
                    if current_time - last_kw_time >= _KEYWORD_COOLDOWN:
                        is_high_priority = True
                        break

            # Check category cooldown
            should_include = is_high_priority  # High priority passes if keyword not on cooldown
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

        # Update cooldown timestamps (inside lock)
        _last_event_trigger = current_time
        for t in filtered_triggers:
            for cat in t.get("categories", []):
                _last_category_trigger[cat] = current_time
            # Track per-keyword cooldown for HIGH_PRIORITY keywords
            for kw in t.get("keywords", []):
                kw_lower = kw.lower()
                if kw_lower in HIGH_PRIORITY_KEYWORDS:
                    _last_keyword_trigger[kw_lower] = current_time

    # Log what we found (outside lock — logging can be slow)
    for t in filtered_triggers:
        priority = "HIGH-PRIORITY" if any(kw.lower() in HIGH_PRIORITY_KEYWORDS for kw in t.get("keywords", [])) else "SIGNAL"
        logger.info(
            "%s: '%s' (source: %s, keywords: %s, categories: %s)",
            priority, t["title"], t["source"], t["keywords"], t["categories"],
        )

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
