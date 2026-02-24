"""Tests for trade selection logic."""

from datetime import datetime, timezone

from backend.app.models import Direction, NewsItem, ScanType, ScoredNews
from backend.app.trade_selector import select_trade, _calibrate_trade


def _make_scored(
    ticker: str,
    surprise: int = 70,
    freshness: int = 80,
    clarity: int = 90,
    direction: Direction = Direction.LONG,
    news_category: str = "macro",
) -> ScoredNews:
    return ScoredNews(
        news=NewsItem(title=f"News about {ticker}", source="Test"),
        surprise=surprise,
        freshness=freshness,
        directional_clarity=clarity,
        direction=direction,
        impacted_tickers=[ticker],
        reasoning="Test reasoning",
        news_category=news_category,
    )


def test_select_trade_empty_news():
    result = select_trade([], ScanType.EUROPE)
    assert not result.has_trade
    assert result.news_analyzed == 0


def test_select_trade_all_neutral():
    scored = [_make_scored("MC.PA", direction=Direction.NEUTRAL)]
    result = select_trade(scored, ScanType.EUROPE)
    assert not result.has_trade
    assert "direction claire" in result.reason_no_trade or "score suffisant" in result.reason_no_trade


def test_select_trade_below_threshold():
    scored = [_make_scored("MC.PA", surprise=10, freshness=10, clarity=10)]
    result = select_trade(scored, ScanType.US)
    assert not result.has_trade


def test_select_trade_unknown_ticker():
    scored = [_make_scored("FAKE.XX", surprise=90, freshness=90, clarity=90)]
    result = select_trade(scored, ScanType.EUROPE)
    assert not result.has_trade


def test_select_trade_with_learning_boost():
    scored = [
        _make_scored("MC.PA", surprise=60, freshness=60, clarity=60),
        _make_scored("TTE.PA", surprise=55, freshness=55, clarity=55),
    ]
    # Boost TTE.PA via learning
    adjustments = {"TTE.PA": 1.5, "MC.PA": 0.5}
    result = select_trade(scored, ScanType.EUROPE, adjustments)
    # Result depends on price fetch availability; just verify structure
    assert result.scan_type == ScanType.EUROPE
    assert result.news_analyzed == 2


def test_select_trade_session_filter_europe_rejects_us_index():
    """US-only indices (S&P 500) should be rejected in Europe scan."""
    scored = [_make_scored("^GSPC", surprise=90, freshness=90, clarity=90)]
    result = select_trade(scored, ScanType.EUROPE)
    assert not result.has_trade  # ^GSPC is USD index, not eligible in Europe


def test_select_trade_session_filter_us_rejects_euronext():
    """Euronext stocks should be rejected in US scan."""
    scored = [_make_scored("MC.PA", surprise=90, freshness=90, clarity=90)]
    result = select_trade(scored, ScanType.US)
    assert not result.has_trade  # MC.PA is actions_europe, not eligible in US


def test_select_trade_forex_available_both_sessions():
    """Forex assets should be available in both sessions."""
    scored = [_make_scored("EURUSD=X", surprise=70, freshness=80, clarity=90)]
    result_eu = select_trade(scored, ScanType.EUROPE)
    result_us = select_trade(scored, ScanType.US)
    # Both should pass the session filter (may fail on price fetch)
    assert result_eu.news_analyzed == 1
    assert result_us.news_analyzed == 1


def test_calibrate_trade_rr_varies_with_score():
    """R/R should vary based on score, not be constant."""
    # Low score
    _, _, t_pct_low, s_pct_low, rr_low = _calibrate_trade(Direction.LONG, 100.0, 2.0, 40)
    # High score
    _, _, t_pct_high, s_pct_high, rr_high = _calibrate_trade(Direction.LONG, 100.0, 2.0, 90)

    # Stop should be same (fixed ATR fraction)
    assert s_pct_low == s_pct_high
    # Target should be higher with higher score
    assert t_pct_high > t_pct_low
    # R/R should be higher with higher score
    assert rr_high > rr_low
