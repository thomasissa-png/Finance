"""Scan history: persists all scored events from each scan for audit trail.

After each scan, the full ScanResult (all scored news, rejections, decision)
is appended to data/scan_history.json. This lets us review what signals were
detected but not traded — essential for post-hoc analysis.

Also provides cross-scan deduplication: get_recently_scored_titles() returns
headlines already scored in recent scans, so run_scan() can skip them before
calling Claude (avoids re-scoring the same news every 3 hours).

Retention: entries older than MAX_HISTORY_DAYS are pruned on each write
to prevent unbounded growth.

v4.0: PostgreSQL persistence (when DATABASE_URL is set, falls back to JSON files).
"""

import fcntl
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .database import is_pg_enabled
from .models import ScanHistoryEntry

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
SCAN_HISTORY_FILE = DATA_DIR / "scan_history.json"

# Keep 30 days of scan history (~120 scans at 4/day)
MAX_HISTORY_DAYS = 30

# ── In-memory cache for cross-scan dedup ─────────────────────────
# Populated from scan_history.json on first access, then updated after each scan.
# Avoids re-reading disk on every scan while staying in sync.
_scored_titles_cache: list[str] = []
_cache_last_updated: float = 0.0


def _ensure_file() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not SCAN_HISTORY_FILE.exists():
        SCAN_HISTORY_FILE.write_text("[]")


def _parse_scan_entries(raw: list, source: str) -> list[ScanHistoryEntry]:
    """Parse raw dicts into ScanHistoryEntry, skipping invalid entries."""
    entries: list[ScanHistoryEntry] = []
    for i, e in enumerate(raw):
        try:
            entries.append(ScanHistoryEntry(**e))
        except Exception as exc:
            logger.warning(
                "Skipping invalid scan history entry #%d from %s: %s",
                i, source, exc,
            )
    return entries


def load_scan_history() -> list[ScanHistoryEntry]:
    """Load all scan history entries.

    Resilient: skips individual entries that fail to parse.
    Falls back to JSON if PG returns empty (un-migrated data).
    """
    if is_pg_enabled():
        try:
            from .database import pg_load_scan_history
            raw = pg_load_scan_history()
            entries = _parse_scan_entries(raw, "PG")
            if entries:
                return entries
            if not raw:
                logger.info("PG scan_history table is empty, trying JSON fallback")
        except Exception as exc:
            logger.error("Failed to load scan history from PostgreSQL: %s", exc)

    # JSON fallback (or primary path when PG is disabled)
    _ensure_file()
    try:
        with open(SCAN_HISTORY_FILE, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                raw = json.load(f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        entries = _parse_scan_entries(raw, "JSON")
        # Auto-migrate JSON → PG if PG is enabled but was empty
        if entries and is_pg_enabled():
            logger.info(
                "Auto-migrating %d scan history entries from JSON to PostgreSQL",
                len(entries),
            )
            try:
                from .database import pg_save_scan_history_entry
                for e in entries:
                    pg_save_scan_history_entry(e.model_dump(mode="json"))
                logger.info("Auto-migration of scan history complete")
            except Exception as exc:
                logger.error("Auto-migration of scan history failed: %s", exc)
        return entries
    except (json.JSONDecodeError, Exception) as exc:
        logger.error("Failed to load scan history: %s", exc)
        return []


def save_scan_history(entries: list[ScanHistoryEntry]) -> None:
    """Save scan history (JSON path only — PG uses direct insert in append_scan_result)."""
    _ensure_file()
    with open(SCAN_HISTORY_FILE, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            json.dump(
                [e.model_dump(mode="json") for e in entries],
                f, indent=2, default=str,
            )
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def get_recently_scored_titles(max_age_hours: float = 8.0) -> list[str]:
    """Return titles of news already scored in recent scans.

    Used by run_scan() for cross-scan dedup: if a headline was already
    sent to Claude in a recent scan, skip it to avoid wasting API calls
    and prevent duplicate trades on the same signal.

    Uses in-memory cache refreshed after each scan to avoid disk I/O.
    Falls back to loading from disk if cache is empty (first call after restart).
    """
    global _scored_titles_cache, _cache_last_updated

    # On first call or if cache is stale (>1h), reload from disk
    import time
    now = time.time()
    if not _scored_titles_cache or (now - _cache_last_updated) > 3600:
        _refresh_titles_cache(max_age_hours)

    return _scored_titles_cache


def _refresh_titles_cache(max_age_hours: float = 8.0) -> None:
    """Rebuild the in-memory titles cache from scan history."""
    global _scored_titles_cache, _cache_last_updated
    import time

    if is_pg_enabled():
        try:
            from .database import pg_get_recently_scored_titles
            _scored_titles_cache = pg_get_recently_scored_titles(max_age_hours)
            _cache_last_updated = time.time()
            logger.debug("Cross-scan dedup cache (PG): %d titles from last %.0fh",
                         len(_scored_titles_cache), max_age_hours)
            return
        except Exception as exc:
            logger.warning("Failed to refresh titles cache from PG: %s", exc)
            _scored_titles_cache = []
            _cache_last_updated = time.time()
            return

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    entries = load_scan_history()
    titles = []
    for entry in entries:
        ts = entry.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < cutoff:
            continue
        for news in entry.all_scored_news:
            title = news.get("title", "")
            if title:
                titles.append(title)
    _scored_titles_cache = titles
    _cache_last_updated = time.time()
    logger.debug("Cross-scan dedup cache: %d titles from last %.0fh", len(titles), max_age_hours)


def append_scan_result(scan_result: dict) -> None:
    """Append a scan result to the history.

    Called after each scan completes (from scheduler.run_scan).
    Automatically prunes entries older than MAX_HISTORY_DAYS.

    Args:
        scan_result: The ScanResult dict returned by run_scan().
    """
    try:
        entry = ScanHistoryEntry(
            timestamp=scan_result.get("timestamp", datetime.now(timezone.utc).isoformat()),
            scan_type=scan_result.get("scan_type", "europe"),
            has_trade=scan_result.get("has_trade", False),
            news_analyzed=scan_result.get("news_analyzed", 0),
            reason_no_trade=scan_result.get("reason_no_trade", ""),
            recommendation=scan_result.get("recommendation"),
            recommendations=scan_result.get("recommendations") or [],
            all_scored_news=scan_result.get("all_scored_news") or [],
            rejection_log=scan_result.get("rejection_log") or [],
            decision_summary=scan_result.get("decision_summary"),
            learning_state=scan_result.get("learning_state"),
            market_context=scan_result.get("market_context"),
        )

        if is_pg_enabled():
            from .database import pg_save_scan_history_entry
            pg_save_scan_history_entry(entry.model_dump(mode="json"))
        else:
            entries = load_scan_history()
            entries.append(entry)

            # Prune old entries
            cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_HISTORY_DAYS)
            before = len(entries)
            entries = [e for e in entries if e.timestamp.replace(tzinfo=timezone.utc) > cutoff]
            if len(entries) < before:
                logger.info("Pruned %d old scan history entries (>%dd)",
                            before - len(entries), MAX_HISTORY_DAYS)

            save_scan_history(entries)

        logger.info("Scan history saved: %s scan, %d scored news, trade=%s",
                     entry.scan_type.value, len(entry.all_scored_news), entry.has_trade)

        # Refresh cross-scan dedup cache with newly saved data
        _refresh_titles_cache()

    except Exception as exc:
        logger.warning("Failed to save scan history: %s", exc)
