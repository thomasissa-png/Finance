"""Tests for event-driven scanner module."""

from datetime import datetime
from unittest.mock import patch, MagicMock
from zoneinfo import ZoneInfo

from backend.app.event_scanner import (
    ALL_KEYWORDS,
    HIGH_IMPACT_KEYWORDS,
    _check_headline_for_triggers,
    determine_scan_type,
    scan_feeds_for_triggers,
    should_trigger_scan,
)

PARIS_TZ = ZoneInfo("Europe/Paris")


# ── Keyword configuration tests ──────────────────────────────────────


def test_high_impact_keywords_categories():
    """Should have keywords for weather, supply_chain, geopolitical, commodity."""
    assert "weather" in HIGH_IMPACT_KEYWORDS
    assert "supply_chain" in HIGH_IMPACT_KEYWORDS
    assert "geopolitical" in HIGH_IMPACT_KEYWORDS
    assert "commodity" in HIGH_IMPACT_KEYWORDS


def test_high_impact_keywords_count():
    """Should have substantial keyword coverage."""
    total = sum(len(kws) for kws in HIGH_IMPACT_KEYWORDS.values())
    assert total >= 30


def test_all_keywords_flattened():
    """ALL_KEYWORDS should be a flat list of (keyword, category) tuples."""
    assert len(ALL_KEYWORDS) >= 30
    for kw, cat in ALL_KEYWORDS:
        assert isinstance(kw, str)
        assert cat in HIGH_IMPACT_KEYWORDS


# ── Headline trigger detection ───────────────────────────────────────


def test_check_headline_drought():
    """Should detect drought keyword."""
    matches = _check_headline_for_triggers("Severe drought expands across Midwest corn belt")
    assert len(matches) >= 1
    categories = [m[1] for m in matches]
    assert "weather" in categories


def test_check_headline_pipeline_explosion():
    """Should detect supply chain disruption."""
    matches = _check_headline_for_triggers("Major pipeline explosion in Texas disrupts oil flow")
    assert len(matches) >= 1
    categories = [m[1] for m in matches]
    assert "supply_chain" in categories


def test_check_headline_military_strike():
    """Should detect geopolitical event."""
    matches = _check_headline_for_triggers("Military strike reported near Strait of Hormuz")
    assert len(matches) >= 1
    categories = [m[1] for m in matches]
    assert "geopolitical" in categories or "supply_chain" in categories


def test_check_headline_opec_cut():
    """Should detect OPEC production cut."""
    matches = _check_headline_for_triggers("OPEC+ announces surprise production cut of 1M bpd")
    assert len(matches) >= 1
    categories = [m[1] for m in matches]
    assert "commodity" in categories


def test_check_headline_normal_news():
    """Normal earnings news should NOT trigger."""
    matches = _check_headline_for_triggers("Apple reports Q4 earnings above expectations")
    assert len(matches) == 0


def test_check_headline_case_insensitive():
    """Keyword matching should be case-insensitive."""
    matches = _check_headline_for_triggers("HURRICANE WARNING issued for Gulf of Mexico")
    assert len(matches) >= 1


def test_check_headline_multiple_keywords():
    """Headlines with multiple keywords should return multiple matches."""
    matches = _check_headline_for_triggers("Flood and drought devastate crop harvest, shortage expected")
    assert len(matches) >= 2


# ── Scan type determination ──────────────────────────────────────────


def test_determine_scan_type_morning():
    """Before 14:00 CET should return europe."""
    with patch("backend.app.event_scanner.datetime") as mock_dt:
        mock_now = MagicMock()
        mock_now.hour = 10
        mock_dt.now.return_value = mock_now
        result = determine_scan_type()
    assert result == "europe"


def test_determine_scan_type_afternoon():
    """After 14:00 CET should return us."""
    with patch("backend.app.event_scanner.datetime") as mock_dt:
        mock_now = MagicMock()
        mock_now.hour = 15
        mock_dt.now.return_value = mock_now
        result = determine_scan_type()
    assert result == "us"


# ── Should trigger scan ──────────────────────────────────────────────


def test_should_trigger_outside_hours():
    """Should not trigger outside trading hours."""
    with patch("backend.app.event_scanner.datetime") as mock_dt:
        mock_now = MagicMock()
        mock_now.hour = 3  # 3 AM
        mock_dt.now.return_value = mock_now
        result, triggers = should_trigger_scan()
    assert result is False
    assert triggers == []


def test_scan_feeds_returns_list():
    """scan_feeds_for_triggers should always return a list."""
    # Mock feedparser to return empty feeds
    with patch("backend.app.event_scanner.feedparser") as mock_fp:
        mock_feed = MagicMock()
        mock_feed.entries = []
        mock_feed.feed = {"title": "Test"}
        mock_fp.parse.return_value = mock_feed
        result = scan_feeds_for_triggers()
    assert isinstance(result, list)
