"""Tests for the news scorer module (#40 — expanded)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from backend.app.news_scorer import (
    _build_context_string,
    _compute_freshness,
    _fetch_market_context,
    SCORING_TOOL,
)
from backend.app.models import ScanType


def test_freshness_very_recent():
    now = datetime.now(timezone.utc)
    assert _compute_freshness(now) == 100


def test_freshness_one_hour_ago():
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    assert _compute_freshness(one_hour_ago) == 100  # Within peak window


def test_freshness_four_hours_ago():
    four_hours = datetime.now(timezone.utc) - timedelta(hours=4)
    score = _compute_freshness(four_hours)
    # Between peak (2h) and max (6h): should be between 0 and 100
    assert 0 < score < 100


def test_freshness_too_old():
    old = datetime.now(timezone.utc) - timedelta(hours=7)
    assert _compute_freshness(old) == 0


def test_freshness_none():
    assert _compute_freshness(None) == 50  # Unknown = neutral


def test_freshness_exactly_at_peak():
    at_peak = datetime.now(timezone.utc) - timedelta(hours=2)
    score = _compute_freshness(at_peak)
    assert score >= 99  # Allow tiny timing tolerance


def test_freshness_exactly_at_max():
    at_max = datetime.now(timezone.utc) - timedelta(hours=6)
    assert _compute_freshness(at_max) == 0


def test_freshness_future_time():
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    assert _compute_freshness(future) == 100


def test_freshness_midpoint():
    """At 4h (midpoint between 2h peak and 6h max), score should be ~50."""
    midpoint = datetime.now(timezone.utc) - timedelta(hours=4)
    score = _compute_freshness(midpoint)
    assert 45 <= score <= 55  # Allow small tolerance


def test_freshness_linear_decay():
    """Score should decrease linearly between peak and max age."""
    score_3h = _compute_freshness(datetime.now(timezone.utc) - timedelta(hours=3))
    score_4h = _compute_freshness(datetime.now(timezone.utc) - timedelta(hours=4))
    score_5h = _compute_freshness(datetime.now(timezone.utc) - timedelta(hours=5))
    assert score_3h > score_4h > score_5h


def test_scoring_tool_schema():
    """Verify the tool_use schema includes edge-detection fields."""
    assert SCORING_TOOL["name"] == "submit_news_scores"
    schema = SCORING_TOOL["input_schema"]
    assert schema["type"] == "object"
    assert "scores" in schema["properties"]
    items_schema = schema["properties"]["scores"]["items"]
    assert "surprise" in items_schema["properties"]
    assert "directional_clarity" in items_schema["properties"]
    assert "transmission_delay" in items_schema["properties"]
    assert "market_awareness" in items_schema["properties"]
    assert "direction" in items_schema["properties"]
    assert items_schema["properties"]["direction"]["enum"] == ["LONG", "SHORT", "NEUTRAL"]


def test_build_context_string_europe():
    ctx = {"vix": 18.5, "regime": "normal", "indices": {"CAC40": 0.5}, "trends": {"CAC40": {"5d": "haussier", "20d": "neutre"}}}
    result = _build_context_string(ctx, ScanType.EUROPE)
    assert "EUROPE" in result
    assert "VIX: 18.5" in result
    assert "normal" in result
    assert "CAC40" in result
    assert "haussier" in result


def test_build_context_string_us():
    ctx = {"vix": 25.0, "regime": "elevated", "indices": {}, "trends": {}}
    result = _build_context_string(ctx, ScanType.US)
    assert "US" in result
    assert "VIX: 25.0" in result
    assert "elevated" in result


def test_build_context_string_no_data():
    ctx = {"vix": None, "regime": "normal", "indices": {}, "trends": {}}
    result = _build_context_string(ctx, ScanType.EUROPE)
    assert "EUROPE" in result
    assert "VIX" not in result  # No VIX data → not included


def test_scoring_tool_news_categories():
    """Verify tool schema includes all news categories including new ones."""
    items_schema = SCORING_TOOL["input_schema"]["properties"]["scores"]["items"]
    news_cat_enum = items_schema["properties"]["news_category"]["enum"]
    assert "earnings" in news_cat_enum
    assert "macro" in news_cat_enum
    assert "geopolitical" in news_cat_enum
    assert "weather" in news_cat_enum
    assert "supply_chain" in news_cat_enum
    assert "central_bank_subtle" in news_cat_enum
    assert "other" in news_cat_enum
