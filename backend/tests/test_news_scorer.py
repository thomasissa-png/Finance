"""Tests for the news scorer module."""

from datetime import datetime, timedelta, timezone

from backend.app.news_scorer import _compute_freshness


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
