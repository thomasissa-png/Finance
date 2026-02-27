"""Tests for configuration module."""

from backend.app.config import (
    ASSETS,
    ASSET_BY_TICKER,
    CATEGORIES,
    CATEGORY_SCORE_MULTIPLIERS,
    CHAIN_REACTIONS,
    CORRELATION_GROUPS,
    EARLY_SIGNAL_FEEDS,
    MIN_RISK_REWARD,
    MIN_SCORE_THRESHOLD,
    NEWS_CATEGORY_MULTIPLIERS,
    SCHEMA_VERSION,
    SOURCE_WEIGHTS,
    TRIGGER_COOLDOWN_SECONDS,
    assets_for_session,
)


def test_42_assets():
    """42 assets after pruning zero-edge assets without dedicated sources."""
    assert len(ASSETS) == 42


def test_categories_coverage():
    cats = {a.category for a in ASSETS}
    assert cats == set(CATEGORIES.keys())


def test_category_counts():
    counts = {}
    for a in ASSETS:
        counts[a.category] = counts.get(a.category, 0) + 1
    assert counts["actions_europe"] == 10  # Was 15, removed 5 without dedicated sources
    assert counts["metaux"] == 4  # PL=F and PA=F kept (now covered by GNews mine strike query)
    assert counts["forex"] == 6  # Was 9, removed USDCAD, NZDUSD, EURGBP
    assert counts["commodities"] == 14
    assert counts["indices"] == 8  # Was 12, removed IBEX, FTSEMIB, HSI, AXJO


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
    """Score threshold should be 20 (lowered from 25 — capture mid-range signals)."""
    assert MIN_SCORE_THRESHOLD == 20


def test_min_risk_reward():
    """R/R minimum should be 1.2 (lowered from 1.3 — more realistic for intraday)."""
    assert MIN_RISK_REWARD == 1.2


def test_source_weights_known_sources():
    """Source weights should be defined for major sources (#6)."""
    assert SOURCE_WEIGHTS["reuters"] == 0.85
    assert SOURCE_WEIGHTS["Reuters"] == 0.85
    assert SOURCE_WEIGHTS["CNBC"] == 0.9
    assert SOURCE_WEIGHTS["Investing.com"] == 0.7
    assert SOURCE_WEIGHTS["Yahoo Finance"] == 0.8


def test_news_category_multipliers():
    """Category multipliers should cover all main categories (#7)."""
    assert "earnings" in NEWS_CATEGORY_MULTIPLIERS
    assert "macro" in NEWS_CATEGORY_MULTIPLIERS
    assert "geopolitical" in NEWS_CATEGORY_MULTIPLIERS
    assert "weather" in NEWS_CATEGORY_MULTIPLIERS
    assert "supply_chain" in NEWS_CATEGORY_MULTIPLIERS
    # Earnings now have small targets (low conviction), wide stops
    assert NEWS_CATEGORY_MULTIPLIERS["earnings"]["target_mult"] < 1.0
    assert NEWS_CATEGORY_MULTIPLIERS["earnings"]["stop_mult"] > 1.0
    # Weather has large targets (strong conviction)
    assert NEWS_CATEGORY_MULTIPLIERS["weather"]["target_mult"] > 1.2
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
    """Schema version should be 3 (v3: ML learning improvements)."""
    assert SCHEMA_VERSION == 3


# ── Edge-priority scoring tests ─────────────────────────────────


def test_category_score_multipliers_penalize_earnings():
    """Earnings should be heavily penalized."""
    assert CATEGORY_SCORE_MULTIPLIERS["earnings"] <= 0.3
    assert CATEGORY_SCORE_MULTIPLIERS["macro"] <= 0.4


def test_category_score_multipliers_boost_weather():
    """Weather / commodity should be boosted."""
    assert CATEGORY_SCORE_MULTIPLIERS["weather"] >= 1.5
    assert CATEGORY_SCORE_MULTIPLIERS["commodity"] >= 1.3
    assert CATEGORY_SCORE_MULTIPLIERS["supply_chain"] >= 1.3


def test_category_score_multipliers_geopolitical():
    """Geopolitical should have a decent multiplier."""
    assert CATEGORY_SCORE_MULTIPLIERS["geopolitical"] >= 1.0


def test_early_signal_feeds_exist():
    """Early-signal feeds should be configured."""
    assert len(EARLY_SIGNAL_FEEDS) >= 10
    # Should include weather, USDA, geopolitical sources
    feeds_joined = " ".join(EARLY_SIGNAL_FEEDS)
    assert "drought" in feeds_joined or "weather" in feeds_joined
    assert "usda" in feeds_joined.lower() or "fao" in feeds_joined.lower()
    assert "eia.gov" in feeds_joined


def test_source_weights_early_signal_premium():
    """Early-signal sources should have premium weights."""
    assert SOURCE_WEIGHTS.get("USDA", 0) >= 1.0
    assert SOURCE_WEIGHTS.get("NOAA", 0) >= 1.0
    assert SOURCE_WEIGHTS.get("eia.gov", 0) >= 1.0


def test_chain_reactions_defined():
    """Chain reactions should be defined for key commodities."""
    assert "CL=F" in CHAIN_REACTIONS  # Oil
    assert "GC=F" in CHAIN_REACTIONS  # Gold
    assert "KC=F" in CHAIN_REACTIONS  # Coffee
    assert "ZC=F" in CHAIN_REACTIONS  # Corn
    # Oil should trigger TTE.PA
    oil_chains = [c["ticker"] for c in CHAIN_REACTIONS["CL=F"]]
    assert "TTE.PA" in oil_chains
    # Gold should trigger silver
    gold_chains = [c["ticker"] for c in CHAIN_REACTIONS["GC=F"]]
    assert "SI=F" in gold_chains
