"""Tests for source_monitor.py — Source Health Monitoring Agent.

v5.2: Validates tracking, daily reports, weekly reviews, source discovery.
"""

import pytest
from unittest.mock import patch
from datetime import datetime, timezone


class TestSourceHealthTracker:
    """Test the core SourceHealthTracker class."""

    def _make_tracker(self):
        """Create a fresh tracker (not the singleton)."""
        from backend.app.source_monitor import SourceHealthTracker
        tracker = SourceHealthTracker()
        # Clear any loaded history
        tracker._daily_summaries = []
        tracker._today_records = []
        tracker._today_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return tracker

    def test_record_success(self):
        tracker = self._make_tracker()
        tracker.record_success("eia", "phase0", items_count=5, latency_ms=120.5)
        assert len(tracker._today_records) == 1
        assert tracker._today_records[0].success is True
        assert tracker._today_records[0].items_count == 5
        assert tracker._today_records[0].latency_ms == 120.5
        assert tracker._today_records[0].source == "eia"

    def test_record_failure(self):
        tracker = self._make_tracker()
        tracker.record_failure("gnews", "phase0", ValueError("API key invalid"), latency_ms=50)
        assert len(tracker._today_records) == 1
        rec = tracker._today_records[0]
        assert rec.success is False
        assert rec.error_type == "ValueError"
        assert "API key invalid" in rec.error_msg

    def test_record_skipped(self):
        tracker = self._make_tracker()
        tracker.record_skipped("eia", "phase0", "EIA_API_KEY not set")
        assert len(tracker._today_records) == 1
        rec = tracker._today_records[0]
        assert rec.success is True
        assert rec.error_type == "SKIPPED"

    def test_scan_health_snapshot(self):
        tracker = self._make_tracker()
        tracker.record_success("weather", "phase0", 3, 200)
        tracker.record_success("eia", "phase0", 5, 150)
        tracker.record_failure("gnews", "phase0", "Timeout", 5000)

        snapshot = tracker.get_scan_health_snapshot()
        assert snapshot["total_sources"] == 3
        assert snapshot["ok"] == 2
        assert snapshot["failed"] == 1
        assert snapshot["total_items"] == 8
        assert len(snapshot["failures"]) == 1
        assert snapshot["failures"][0]["source"] == "gnews"
        assert snapshot["critical_failures"] == 1  # gnews is Phase 0

    def test_scan_snapshot_resets(self):
        """After getting snapshot, current scan records are cleared."""
        tracker = self._make_tracker()
        tracker.record_success("weather", "phase0", 3, 200)
        tracker.get_scan_health_snapshot()
        # Should be empty now
        snapshot2 = tracker.get_scan_health_snapshot()
        assert snapshot2 == {}

    def test_daily_report(self):
        tracker = self._make_tracker()
        # Simulate multiple scans in a day
        tracker.record_success("weather", "phase0", 3, 200)
        tracker.record_success("eia", "phase0", 5, 150)
        tracker.record_failure("gnews", "phase0", "Rate limit", 100)
        tracker.get_scan_health_snapshot()  # flush current scan

        tracker.record_success("weather", "phase0", 2, 180)
        tracker.record_success("gnews", "phase0", 10, 300)  # recovered
        tracker.get_scan_health_snapshot()

        report = tracker.get_daily_report()
        assert report["date"] == datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert "weather" in report["sources"]
        assert report["sources"]["weather"]["status"] == "OK"
        assert report["sources"]["weather"]["calls_ok"] == 2
        assert report["sources"]["gnews"]["status"] == "DEGRADED"
        assert report["sources"]["gnews"]["calls_ok"] == 1
        assert report["sources"]["gnews"]["calls_failed"] == 1

    def test_daily_report_dead_source(self):
        tracker = self._make_tracker()
        tracker.record_failure("agsi", "phase0", "Connection refused", 50)
        tracker.record_failure("agsi", "phase0", "Connection refused", 60)

        report = tracker.get_daily_report()
        assert report["sources"]["agsi"]["status"] == "DEAD"

    def test_weekly_review_no_data(self):
        tracker = self._make_tracker()
        review = tracker.get_weekly_review()
        assert review["days_with_data"] == 0

    def test_weekly_review_with_data(self):
        tracker = self._make_tracker()
        # Manually inject daily summaries
        tracker._daily_summaries = [
            {
                "date": "2026-03-06",
                "sources": {
                    "weather": {"calls_ok": 4, "calls_failed": 0, "total_items": 12,
                                "status": "OK", "avg_latency_ms": 200},
                    "gnews": {"calls_ok": 0, "calls_failed": 4, "total_items": 0,
                              "status": "DEAD", "avg_latency_ms": 100,
                              "errors": ["Rate limit exceeded"]},
                },
            },
            {
                "date": "2026-03-07",
                "sources": {
                    "weather": {"calls_ok": 4, "calls_failed": 0, "total_items": 10,
                                "status": "OK", "avg_latency_ms": 180},
                    "gnews": {"calls_ok": 0, "calls_failed": 4, "total_items": 0,
                              "status": "DEAD", "avg_latency_ms": 90,
                              "errors": ["Rate limit exceeded"]},
                },
            },
        ]

        review = tracker.get_weekly_review()
        assert review["days_with_data"] == 2
        assert len(review["dead_sources"]) == 1
        assert review["dead_sources"][0]["source"] == "gnews"
        assert len(review["stable_sources"]) == 1
        assert review["stable_sources"][0]["source"] == "weather"
        assert len(review["recommendations"]) > 0


class TestSourceCallRecord:
    """Test SourceCallRecord serialization."""

    def test_to_dict_success(self):
        from backend.app.source_monitor import SourceCallRecord
        rec = SourceCallRecord("eia", "phase0", True, items_count=5, latency_ms=120)
        d = rec.to_dict()
        assert d["source"] == "eia"
        assert d["success"] is True
        assert d["items_count"] == 5
        assert "error_type" not in d  # Not included for successes

    def test_to_dict_failure(self):
        from backend.app.source_monitor import SourceCallRecord
        rec = SourceCallRecord("gnews", "phase0", False,
                               error_type="Timeout", error_msg="Request timed out after 15s")
        d = rec.to_dict()
        assert d["success"] is False
        assert d["error_type"] == "Timeout"
        assert "15s" in d["error_msg"]

    def test_error_msg_truncation(self):
        from backend.app.source_monitor import SourceCallRecord
        long_msg = "x" * 500
        rec = SourceCallRecord("test", "phase0", False, error_msg=long_msg)
        assert len(rec.error_msg) == 300


class TestSourceDiscovery:
    """Test the source discovery suggestions."""

    def test_discovery_returns_suggestions(self):
        from backend.app.source_monitor import _get_source_discovery_suggestions
        suggestions = _get_source_discovery_suggestions()
        assert len(suggestions) > 0
        # Should have a header + at least 1 source suggestion
        assert "[DISCOVERY]" in suggestions[0]

    def test_discovery_suggests_edge_sources(self):
        from backend.app.source_monitor import _get_source_discovery_suggestions
        suggestions = _get_source_discovery_suggestions()
        all_text = " ".join(suggestions)
        # Should suggest sources relevant to our edge (weather, agri, supply_chain)
        edge_categories = ["weather", "agri", "supply_chain", "energy", "positioning"]
        has_edge = any(cat in all_text for cat in edge_categories)
        assert has_edge, f"Discovery should suggest edge-relevant sources, got: {all_text[:200]}"


class TestSourcePriority:
    """Test source classification."""

    def test_phase0_all_critical(self):
        from backend.app.source_monitor import PHASE_0_SOURCES, SOURCE_PRIORITY
        for source in PHASE_0_SOURCES:
            assert SOURCE_PRIORITY.get(source) == "CRITICAL", \
                f"Phase 0 source {source} should be CRITICAL"

    def test_phase0_has_17_sources(self):
        from backend.app.source_monitor import PHASE_0_SOURCES
        assert len(PHASE_0_SOURCES) == 17


class TestScanHistorySourceHealth:
    """Test that source_health field is accepted in ScanHistoryEntry."""

    def test_scan_history_entry_accepts_source_health(self):
        from backend.app.models import ScanHistoryEntry
        entry = ScanHistoryEntry(
            timestamp=datetime.now(timezone.utc),
            scan_type="europe",
            has_trade=False,
            source_health={
                "total_sources": 17,
                "ok": 15,
                "failed": 2,
                "failures": [{"source": "gnews", "error": "Timeout"}],
            },
        )
        assert entry.source_health is not None
        assert entry.source_health["ok"] == 15

    def test_scan_history_entry_none_source_health(self):
        """Backward compat — source_health is optional."""
        from backend.app.models import ScanHistoryEntry
        entry = ScanHistoryEntry(
            timestamp=datetime.now(timezone.utc),
            scan_type="europe",
            has_trade=False,
        )
        assert entry.source_health is None


class TestWeeklyJournalEntry:
    """Test weekly review journal entry creation."""

    def test_creates_entry_with_data(self):
        from backend.app.source_monitor import SourceHealthTracker, create_weekly_review_journal_entry

        # Patch the singleton
        tracker = SourceHealthTracker()
        tracker._daily_summaries = [
            {
                "date": "2026-03-06",
                "sources": {
                    "weather": {"calls_ok": 4, "calls_failed": 0, "total_items": 12,
                                "status": "OK", "avg_latency_ms": 200},
                },
            },
        ]

        with patch("backend.app.source_monitor.get_tracker", return_value=tracker), \
             patch("backend.app.source_monitor.pg_save_weekly_review"):
            entry = create_weekly_review_journal_entry()

        assert entry is not None
        assert entry["type"] == "source_health_review"
        assert "REVUE HEBDOMADAIRE" in entry["review_text"]
        assert entry["stable_count"] >= 1

    def test_skips_without_data(self):
        from backend.app.source_monitor import SourceHealthTracker, create_weekly_review_journal_entry

        tracker = SourceHealthTracker()
        tracker._daily_summaries = []

        with patch("backend.app.source_monitor.get_tracker", return_value=tracker):
            entry = create_weekly_review_journal_entry()

        assert entry is None
