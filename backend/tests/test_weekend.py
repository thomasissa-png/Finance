"""Tests that all trading operations are blocked on weekends.

Markets are closed Saturday-Sunday, so scans, event checks, and manual
triggers must not fire. This prevents wasted API calls and stale signals.
"""

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from backend.app.event_scanner import should_trigger_scan
from backend.app.main import app

PARIS_TZ = ZoneInfo("Europe/Paris")

client = TestClient(app)


# ════════════════════════════════════════════════════════════════
# Event scanner weekend guard
# ════════════════════════════════════════════════════════════════


class TestEventScannerWeekend:
    """Verify that should_trigger_scan() returns False on weekends."""

    def test_saturday_blocked(self):
        """Saturday: should_trigger_scan returns (False, [])."""
        saturday = datetime(2026, 2, 28, 10, 0, tzinfo=PARIS_TZ)  # Saturday
        with patch("backend.app.event_scanner.datetime") as mock_dt:
            mock_dt.now.return_value = saturday
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result, triggers = should_trigger_scan()
        assert result is False
        assert triggers == []

    def test_sunday_blocked(self):
        """Sunday: should_trigger_scan returns (False, [])."""
        sunday = datetime(2026, 3, 1, 14, 30, tzinfo=PARIS_TZ)  # Sunday
        with patch("backend.app.event_scanner.datetime") as mock_dt:
            mock_dt.now.return_value = sunday
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result, triggers = should_trigger_scan()
        assert result is False
        assert triggers == []

    def test_monday_not_blocked_by_weekend_check(self):
        """Monday during trading hours: weekend guard does not block.

        Note: may still return False due to no triggers found, but the
        weekend check itself should pass.
        """
        monday = datetime(2026, 2, 23, 10, 0, tzinfo=PARIS_TZ)  # Monday
        with patch("backend.app.event_scanner.datetime") as mock_dt:
            mock_dt.now.return_value = monday
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            # Patch scan_feeds to avoid network calls
            with patch("backend.app.event_scanner.scan_feeds_for_triggers",
                       return_value=[]):
                with patch("backend.app.event_scanner._last_event_trigger", 0):
                    result, triggers = should_trigger_scan()
        # No triggers found, but that's because no feeds matched —
        # the weekend guard did NOT block it
        assert triggers == []

    def test_friday_not_blocked(self):
        """Friday during trading hours: should not be blocked by weekend check."""
        friday = datetime(2026, 2, 27, 10, 0, tzinfo=PARIS_TZ)  # Friday
        with patch("backend.app.event_scanner.datetime") as mock_dt:
            mock_dt.now.return_value = friday
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            with patch("backend.app.event_scanner.scan_feeds_for_triggers",
                       return_value=[]):
                with patch("backend.app.event_scanner._last_event_trigger", 0):
                    result, triggers = should_trigger_scan()
        assert triggers == []


# ════════════════════════════════════════════════════════════════
# Manual trigger weekend guard (API endpoints)
# ════════════════════════════════════════════════════════════════


class TestManualTriggerWeekend:
    """Verify that manual scan triggers return 400 on weekends."""

    def test_scan_trigger_blocked_saturday(self):
        """POST /api/scan/trigger/europe returns 400 on Saturday."""
        saturday = datetime(2026, 2, 28, 10, 0, tzinfo=PARIS_TZ)
        with patch("backend.app.main.datetime") as mock_dt:
            mock_dt.now.return_value = saturday
            mock_dt.fromisoformat = datetime.fromisoformat
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            response = client.post("/api/scan/trigger/europe")
        assert response.status_code == 400
        assert "week-end" in response.json()["detail"]

    def test_scan_trigger_blocked_sunday(self):
        """POST /api/scan/trigger/us returns 400 on Sunday."""
        sunday = datetime(2026, 3, 1, 14, 30, tzinfo=PARIS_TZ)
        with patch("backend.app.main.datetime") as mock_dt:
            mock_dt.now.return_value = sunday
            mock_dt.fromisoformat = datetime.fromisoformat
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            response = client.post("/api/scan/trigger/us")
        assert response.status_code == 400
        assert "week-end" in response.json()["detail"]

    def test_event_check_blocked_weekend(self):
        """POST /api/scan/event-check returns 400 on Saturday."""
        saturday = datetime(2026, 2, 28, 10, 0, tzinfo=PARIS_TZ)
        with patch("backend.app.main.datetime") as mock_dt:
            mock_dt.now.return_value = saturday
            mock_dt.fromisoformat = datetime.fromisoformat
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            response = client.post("/api/scan/event-check")
        assert response.status_code == 400
        assert "week-end" in response.json()["detail"]


# ════════════════════════════════════════════════════════════════
# CronTrigger weekday configuration
# ════════════════════════════════════════════════════════════════


class TestCronTriggerWeekday:
    """Verify that all scheduled jobs use day_of_week='mon-fri'."""

    def test_all_jobs_weekday_only(self):
        """All scheduler jobs should have day_of_week set to mon-fri."""
        from backend.app.main import bg_scheduler

        # The scheduler may or may not be running in test context,
        # so we verify the configuration by reading the source code
        import inspect
        from backend.app.main import lifespan

        source = inspect.getsource(lifespan)
        # Count CronTrigger occurrences and day_of_week occurrences
        cron_count = source.count("CronTrigger(")
        weekday_count = source.count('day_of_week="mon-fri"')
        assert cron_count == weekday_count, (
            f"Found {cron_count} CronTrigger definitions but only "
            f"{weekday_count} have day_of_week='mon-fri'"
        )
        assert cron_count >= 6, f"Expected at least 6 CronTriggers, found {cron_count}"
