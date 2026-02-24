"""Tests for configuration module."""

from backend.app.config import ASSETS, ASSET_BY_TICKER, CATEGORIES, assets_for_session


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
    # Must include Euronext stocks
    assert "MC.PA" in eu
    # Must include EUR indices
    assert "^FCHI" in eu
    assert "^GDAXI" in eu
    # Must NOT include USD indices
    assert "^GSPC" not in eu
    assert "^DJI" not in eu
    # Must include metals, forex, commodities
    assert "GC=F" in eu
    assert "EURUSD=X" in eu
    assert "CL=F" in eu


def test_assets_for_session_us():
    us = assets_for_session("us")
    # Must NOT include Euronext stocks
    assert "MC.PA" not in us
    # Must include USD indices
    assert "^GSPC" in us
    assert "^DJI" in us
    # Must NOT include EUR indices
    assert "^FCHI" not in us
    # Must include metals, forex, commodities
    assert "GC=F" in us
    assert "EURUSD=X" in us
    assert "CL=F" in us
