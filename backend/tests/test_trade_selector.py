"""Tests for trade selection logic."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from backend.app.models import Direction, NewsItem, ScanType, ScoredNews, TradeRecommendation, TradeResult
from backend.app.trade_selector import (
    RECENT_TRADE_COOLDOWN_DAYS,
    _calibrate_trade,
    _check_binary_event,
    _check_correlation,
    _detect_pre_move,
    _get_recently_traded_tickers,
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
    _, _, t_pct_weather, s_pct_weather, _ = _calibrate_trade(Direction.LONG, 100.0, 2.0, 70, news_category="weather")

    # Weather should have bigger targets than earnings (our edge is better)
    assert t_pct_weather > t_pct_earn
    # Earnings should have wider stops (less conviction)
    assert s_pct_earn > s_pct_weather


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
    assert _check_binary_event("ECB rate decision", "") is not None
    assert _check_binary_event("fed rate hike", "") is not None


def test_check_correlation_no_existing():
    assert _check_correlation("MC.PA", None) is False


def test_check_correlation_same_group():
    """Tickers in same group should be correlated (#22)."""
    assert _check_correlation("MC.PA", "RMS.PA") is True
    assert _check_correlation("TTE.PA", "CL=F") is True


def test_check_correlation_different_groups():
    assert _check_correlation("MC.PA", "CL=F") is False
    assert _check_correlation("GC=F", "MC.PA") is False


# ── Cross-day dedup tests ──────────────────────────────────────

def _make_trade(ticker: str, days_ago: int = 0) -> TradeRecommendation:
    """Create a minimal TradeRecommendation for dedup testing."""
    return TradeRecommendation(
        scan_type=ScanType.EUROPE,
        timestamp=datetime.now(timezone.utc) - timedelta(days=days_ago),
        ticker=ticker,
        asset_name=ticker,
        category="test",
        direction=Direction.LONG,
        catalyst="Test",
        entry_price=100.0,
        target_price=102.0,
        stop_price=99.0,
        target_pct=2.0,
        stop_pct=1.0,
        risk_reward=2.0,
        confidence=70,
        time_window="09:00 — 20:00",
        result=TradeResult.PENDING,
    )


@patch("backend.app.learning.load_trades")
def test_get_recently_traded_tickers_returns_recent(mock_load):
    """Tickers traded within cooldown window should be returned."""
    mock_load.return_value = [
        _make_trade("ZW=F", days_ago=1),  # wheat yesterday
        _make_trade("MC.PA", days_ago=0),  # today
    ]
    result = _get_recently_traded_tickers(cooldown_days=3)
    assert "ZW=F" in result
    assert "MC.PA" in result


@patch("backend.app.learning.load_trades")
def test_get_recently_traded_tickers_excludes_old(mock_load):
    """Tickers traded before cooldown window should NOT be returned."""
    mock_load.return_value = [
        _make_trade("ZW=F", days_ago=5),  # 5 days ago — outside 3-day window
        _make_trade("MC.PA", days_ago=1),  # yesterday — within window
    ]
    result = _get_recently_traded_tickers(cooldown_days=3)
    assert "ZW=F" not in result
    assert "MC.PA" in result


@patch("backend.app.learning.load_trades")
def test_get_recently_traded_tickers_empty_on_error(mock_load):
    """Should return empty set if load_trades fails."""
    mock_load.side_effect = Exception("DB error")
    result = _get_recently_traded_tickers()
    assert result == set()


# ── v4.0 tests: new calibration and filtering features ────────


def test_calibrate_trade_convex_score_factor():
    """v4.0 C4: High scores should get disproportionately bigger targets (convex)."""
    _, _, t_low, _, _ = _calibrate_trade(Direction.LONG, 100.0, 2.0, 20)
    _, _, t_med, _, _ = _calibrate_trade(Direction.LONG, 100.0, 2.0, 50)
    _, _, t_high, _, _ = _calibrate_trade(Direction.LONG, 100.0, 2.0, 90)
    # The ratio high/low should be >2x (convex response)
    assert t_high / t_low > 2.0
    # And the gap between medium and high should be larger than low and medium
    assert (t_high - t_med) > (t_med - t_low)


def test_calibrate_trade_short_asymmetric_stop():
    """v4.0 C2: SHORT trades should have wider stops than LONG."""
    _, _, _, stop_long, _ = _calibrate_trade(Direction.LONG, 100.0, 2.0, 70)
    _, _, _, stop_short, _ = _calibrate_trade(Direction.SHORT, 100.0, 2.0, 70)
    # SHORT stop should be 20% wider
    assert abs(stop_short / stop_long - 1.2) < 0.01


def test_calibrate_trade_magnitude_scales_target():
    """v4.0 B1: Higher expected_magnitude should produce bigger targets."""
    _, _, t_low_mag, _, _ = _calibrate_trade(Direction.LONG, 100.0, 2.0, 70,
                                              expected_magnitude=10)
    _, _, t_high_mag, _, _ = _calibrate_trade(Direction.LONG, 100.0, 2.0, 70,
                                               expected_magnitude=90)
    assert t_high_mag > t_low_mag


def test_calibrate_trade_spread_stop_floor():
    """v4.0 C3: Stop floor should be at least 2x estimated spread for wide-spread assets."""
    from backend.app.config import ESTIMATED_SPREADS
    # OJ=F has spread 0.20% → stop floor should be at least 0.40%
    _, _, _, stop_pct, _ = _calibrate_trade(Direction.LONG, 100.0, 0.5, 30,
                                             ticker="OJ=F")
    assert stop_pct >= 0.40  # 2x estimated spread


@patch("backend.app.trade_selector._get_recently_traded_tickers_by_category")
@patch("backend.app.trade_selector._get_recently_traded_tickers")
def test_select_trade_rejects_recently_traded_ticker(mock_recent, mock_recent_cat):
    """A ticker traded recently should be rejected by select_trade."""
    mock_recent.return_value = {"MC.PA"}
    mock_recent_cat.return_value = {"MC.PA"}
    scored = [_make_scored("MC.PA", surprise=90, freshness=90, clarity=90,
                           news_category="commodity")]
    # Need high transmission_delay and low market_awareness for edge score
    scored[0].transmission_delay = 80
    scored[0].market_awareness = 10
    result = select_trade(scored, ScanType.EUROPE)
    assert not result.has_trade
    # With D1 fallback, rejection message is "Tous les tickers bloques" when all fallbacks fail
    assert any("bloques" in r.get("reason", "") or "Deja trade" in r.get("reason", "")
               for r in result.rejection_log)
