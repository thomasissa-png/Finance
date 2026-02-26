"""Scan history: persists all scored events from each scan for audit trail.

After each scan, the full ScanResult (all scored news, rejections, decision)
is appended to data/scan_history.json. This lets us review what signals were
detected but not traded — essential for post-hoc analysis.

Retention: entries older than MAX_HISTORY_DAYS are pruned on each write
to prevent unbounded growth.
"""

import fcntl
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .models import ScanHistoryEntry

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
SCAN_HISTORY_FILE = DATA_DIR / "scan_history.json"

# Keep 30 days of scan history (~120 scans at 4/day)
MAX_HISTORY_DAYS = 30


def _ensure_file() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not SCAN_HISTORY_FILE.exists():
        SCAN_HISTORY_FILE.write_text("[]")


def load_scan_history() -> list[ScanHistoryEntry]:
    """Load all scan history entries from disk."""
    _ensure_file()
    try:
        with open(SCAN_HISTORY_FILE, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                raw = json.load(f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        return [ScanHistoryEntry(**e) for e in raw]
    except (json.JSONDecodeError, Exception) as exc:
        logger.error("Failed to load scan history: %s", exc)
        return []


def save_scan_history(entries: list[ScanHistoryEntry]) -> None:
    """Save scan history to disk with exclusive lock."""
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


def append_scan_result(scan_result: dict) -> None:
    """Append a scan result to the history file.

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
            all_scored_news=scan_result.get("all_scored_news") or [],
            rejection_log=scan_result.get("rejection_log") or [],
            decision_summary=scan_result.get("decision_summary"),
            learning_state=scan_result.get("learning_state"),
            market_context=scan_result.get("market_context"),
        )

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

    except Exception as exc:
        logger.warning("Failed to save scan history: %s", exc)
