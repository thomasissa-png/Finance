"""Tests for configuration module."""

from backend.app.config import (
    ASSETS,
    ASSET_BY_TICKER,
    CATEGORIES,
    CORRELATION_GROUPS,
    MIN_RISK_REWARD,
    MIN_SCORE_THRESHOLD,
    NEWS_CATEGORY_MULTIPLIERS,
    SCHEMA_VERSION,
    SOURCE_WEIGHTS,
    TRIGGER_COOLDOWN_SECONDS,
    assets_for_session,
)


def test_49_assets():
    assert len(ASSETS) == 49


def test_categories_coverage():
    cats = {a.category for a in ASSETS}
    assert cats == set(CATEGORIES.keys())


def test_category_counts():
    counts = {}
    for a in ASSETS:
        counts[a.category] = counts.get(a.category, 0) + 1
    assert counts["actions_europe"] == 15
    assert counts["metaux"] == 4
    assert counts["forex"] == 9
    assert counts["commodities"] == 9
    assert counts["indices"] == 12


def test_asset_by_ticker_lookup():
    assert ASSET_BY_TICKER["MC.PA"].name == "LVMH"
    assert ASSET_BY_TICKER["GC=F"].name == "Or"
    assert ASSET_BY_TICKER["EURUSD=X"].name == "EUR/USD"
    assert ASSET_BY_TICKER["CL=F"].name == "Pétrole WTI"
    assert ASSET_BY_TICKER["^FCHI"].name == "CAC 40"


def test_no_duplicate_tickers():
    tickers = [a.ticker for a in ASSETS]
    assert len(tickers) == len(set(tickers))


def test_assets_for_session_europe():
    eu = assets_for_session("europe")
    assert "MC.PA" in eu
    assert "^FCHI" in eu
    assert "^GDAXI" in eu
    assert "^GSPC" not in eu
    assert "^DJI" not in eu
    assert "GC=F" in eu
    assert "EURUSD=X" in eu
    assert "CL=F" in eu


def test_assets_for_session_us():
    us = assets_for_session("us")
    assert "MC.PA" not in us
    assert "^GSPC" in us
    assert "^DJI" in us
    assert "^FCHI" not in us
    assert "GC=F" in us
    assert "EURUSD=X" in us
    assert "CL=F" in us


# ── New tests for v2.0 config values ────────────────────────────


def test_min_score_threshold():
    """Score threshold should be 55 (#19)."""
    assert MIN_SCORE_THRESHOLD == 55


def test_min_risk_reward():
    """R/R minimum should be 1.3 (#20)."""
    assert MIN_RISK_REWARD == 1.3


def test_source_weights_known_sources():
    """Source weights should be defined for major sources (#6)."""
    assert SOURCE_WEIGHTS["reuters"] == 1.0
    assert SOURCE_WEIGHTS["Reuters"] == 1.0
    assert SOURCE_WEIGHTS["CNBC"] == 0.9
    assert SOURCE_WEIGHTS["Investing.com"] == 0.7
    assert SOURCE_WEIGHTS["Yahoo Finance"] == 0.8


def test_news_category_multipliers():
    """Category multipliers should cover all main categories (#7)."""
    assert "earnings" in NEWS_CATEGORY_MULTIPLIERS
    assert "macro" in NEWS_CATEGORY_MULTIPLIERS
    assert "geopolitical" in NEWS_CATEGORY_MULTIPLIERS
    assert NEWS_CATEGORY_MULTIPLIERS["earnings"]["target_mult"] > 1.0
    assert NEWS_CATEGORY_MULTIPLIERS["geopolitical"]["stop_mult"] > 1.0


def test_correlation_groups():
    """Correlation groups should be defined (#22)."""
    assert "energy" in CORRELATION_GROUPS
    assert "TTE.PA" in CORRELATION_GROUPS["energy"]
    assert "luxury" in CORRELATION_GROUPS
    assert "MC.PA" in CORRELATION_GROUPS["luxury"]


def test_trigger_cooldown():
    """Cooldown should be 300s (#35)."""
    assert TRIGGER_COOLDOWN_SECONDS == 300


def test_schema_version():
    """Schema version should be 2 (#42)."""
    assert SCHEMA_VERSION == 2
