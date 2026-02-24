"""Tests for trade selection logic."""

from datetime import datetime, timezone

from backend.app.models import Direction, NewsItem, ScanType, ScoredNews
from backend.app.trade_selector import (
    _calibrate_trade,
    _check_binary_event,
    _check_correlation,
    _detect_pre_move,
    select_trade,
)


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
    adjustments = {"TTE.PA": 1.5, "MC.PA": 0.5}
    result = select_trade(scored, ScanType.EUROPE, adjustments)
    assert result.scan_type == ScanType.EUROPE
    assert result.news_analyzed == 2


def test_select_trade_session_filter_europe_rejects_us_index():
    """US-only indices (S&P 500) should be rejected in Europe scan."""
    scored = [_make_scored("^GSPC", surprise=90, freshness=90, clarity=90)]
    result = select_trade(scored, ScanType.EUROPE)
    assert not result.has_trade


def test_select_trade_session_filter_us_rejects_euronext():
    """Euronext stocks should be rejected in US scan."""
    scored = [_make_scored("MC.PA", surprise=90, freshness=90, clarity=90)]
    result = select_trade(scored, ScanType.US)
    assert not result.has_trade


def test_select_trade_forex_available_both_sessions():
    """Forex assets should be available in both sessions."""
    scored = [_make_scored("EURUSD=X", surprise=70, freshness=80, clarity=90)]
    result_eu = select_trade(scored, ScanType.EUROPE)
    result_us = select_trade(scored, ScanType.US)
    assert result_eu.news_analyzed == 1
    assert result_us.news_analyzed == 1


def test_select_trade_with_market_context():
    """Market context should be passed through to ScanResult."""
    scored = [_make_scored("MC.PA", surprise=10, freshness=10, clarity=10)]
    ctx = {"vix": 18.5, "regime": "normal"}
    result = select_trade(scored, ScanType.EUROPE, market_context=ctx)
    assert result.market_context == ctx


def test_select_trade_with_existing_trade():
    """existing_trade_ticker param should be accepted."""
    scored = [_make_scored("MC.PA", surprise=90, freshness=90, clarity=90)]
    result = select_trade(scored, ScanType.EUROPE, existing_trade_ticker="TTE.PA")
    assert result.news_analyzed == 1


def test_calibrate_trade_rr_varies_with_score():
    """R/R should vary based on score, not be constant."""
    _, _, t_pct_low, s_pct_low, rr_low = _calibrate_trade(Direction.LONG, 100.0, 2.0, 40)
    _, _, t_pct_high, s_pct_high, rr_high = _calibrate_trade(Direction.LONG, 100.0, 2.0, 90)

    assert s_pct_low == s_pct_high
    assert t_pct_high > t_pct_low
    assert rr_high > rr_low


def test_calibrate_trade_with_news_category():
    """News category should affect target/stop (#7)."""
    _, _, t_pct_earn, s_pct_earn, _ = _calibrate_trade(Direction.LONG, 100.0, 2.0, 70, news_category="earnings")
    _, _, t_pct_geo, s_pct_geo, _ = _calibrate_trade(Direction.LONG, 100.0, 2.0, 70, news_category="geopolitical")

    assert t_pct_earn > t_pct_geo
    assert s_pct_geo > s_pct_earn


def test_calibrate_trade_short_direction():
    """Short trades should have inverted target/stop prices."""
    target_long, stop_long, _, _, _ = _calibrate_trade(Direction.LONG, 100.0, 2.0, 70)
    target_short, stop_short, _, _, _ = _calibrate_trade(Direction.SHORT, 100.0, 2.0, 70)

    assert target_long > 100.0
    assert stop_long < 100.0
    assert target_short < 100.0
    assert stop_short > 100.0


# ── New tests for v2.0 functions ────────────────────────────────


def test_detect_pre_move_no_prev_close():
    assert _detect_pre_move(100.0, None, 1.5) is None


def test_detect_pre_move_calculation():
    result = _detect_pre_move(102.0, 100.0, 2.0)
    assert result == 2.0


def test_detect_pre_move_negative():
    result = _detect_pre_move(98.0, 100.0, 2.0)
    assert result == -2.0


def test_check_binary_event_found():
    """Binary event keywords should be detected (#24)."""
    warning = _check_binary_event("Fed rate decision tomorrow", "Expected to raise rates")
    assert warning is not None
    assert "binaire" in warning.lower()


def test_check_binary_event_not_found():
    warning = _check_binary_event("LVMH beats expectations", "Strong earnings growth")
    assert warning is None


def test_check_binary_event_various_keywords():
    assert _check_binary_event("FOMC meeting", "") is not None
    assert _check_binary_event("NFP data", "") is not None
    assert _check_binary_event("OPEC meeting", "") is not None
    assert _check_binary_event("ECB rate decision", "") is not None


def test_check_correlation_no_existing():
    assert _check_correlation("MC.PA", None) is False


def test_check_correlation_same_group():
    """Tickers in same group should be correlated (#22)."""
    assert _check_correlation("MC.PA", "RMS.PA") is True
    assert _check_correlation("TTE.PA", "CL=F") is True


def test_check_correlation_different_groups():
    assert _check_correlation("MC.PA", "CL=F") is False
    assert _check_correlation("GC=F", "MC.PA") is False
