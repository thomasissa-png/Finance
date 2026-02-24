"""Tests for configuration module."""

from backend.app.config import ASSETS, ASSET_BY_TICKER, CATEGORIES


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
