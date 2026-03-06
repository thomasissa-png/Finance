"""Tests for data models."""

from datetime import datetime, timezone

from backend.app.models import (
    ChainReaction,
    Direction,
    NewsItem,
    ScanResult,
    ScanType,
    ScoredNews,
    TradeRecommendation,
    TradeResult,
)


def test_scored_news_edge_weighted_score():
    """Score with high edge (high delay, low awareness) should be high."""
    news = NewsItem(title="Test", source="Reuters", source_weight=1.0)
    scored = ScoredNews(
        news=news,
        surprise=80,
        freshness=100,
        directional_clarity=100,
        transmission_delay=80,   # Not yet priced
        market_awareness=10,     # Almost nobody has seen
        signal_reliability=100,  # Confirmed fact
        direction=Direction.LONG,
        category_score_mult=1.0,
    )
    # edge_factor = 0.8 * 0.9 = 0.72
    # reliability_factor = 0.4 + 0.6 * 1.0 = 1.0
    # score = 80 * 1.0 * 0.72 * 1.0 * 1.0 * 1.0 = 57.6
    assert scored.total_score == 57.6


def test_scored_news_zero_edge_earnings():
    """Earnings (already priced) should score near zero."""
    news = NewsItem(title="LVMH beats estimates", source="Reuters", source_weight=1.0)
    scored = ScoredNews(
        news=news,
        surprise=80,
        freshness=100,
        directional_clarity=100,
        transmission_delay=5,    # Already priced by algos
        market_awareness=95,     # Everyone saw it
        signal_reliability=100,  # Confirmed (published earnings)
        direction=Direction.LONG,
        category_score_mult=0.2,  # Earnings penalty
    )
    # edge_factor = max(0.05 * 0.05, 0.05) = 0.05
    # reliability_factor = 1.0
    # score = 80 * 1.0 * 0.05 * 1.0 * 1.0 * 0.2 = 0.8
    assert scored.total_score == 0.8


def test_scored_news_weather_commodity():
    """Weather signal for commodity should score highest."""
    news = NewsItem(title="Drought hits Brazil", source="NOAA", source_weight=1.1)
    scored = ScoredNews(
        news=news,
        surprise=75,
        freshness=100,
        directional_clarity=90,
        transmission_delay=85,
        market_awareness=5,
        signal_reliability=90,  # Observed drought = high reliability
        direction=Direction.LONG,
        category_score_mult=1.8,  # Weather category boost
    )
    # edge_factor = 0.85 * 0.95 = 0.8075
    # reliability_factor = 0.4 + 0.6 * 0.9 = 0.94
    # score = 75 * 0.9 * 0.8075 * 0.94 * 1.1 * 1.8 = ~90.76
    assert scored.total_score > 85


def test_scored_news_stale_cap():
    """Freshness is NOT in the formula — stale news scores same as fresh (Claude adjusts via surprise/delay)."""
    news = NewsItem(title="Test", source="Reuters", source_weight=1.0)
    scored = ScoredNews(
        news=news,
        surprise=100,
        freshness=20,
        directional_clarity=100,
        transmission_delay=80,
        market_awareness=10,
        signal_reliability=100,
        direction=Direction.LONG,
    )
    # edge_factor = 0.8 * 0.9 = 0.72, reliability_factor = 1.0
    # score = 100 * 1.0 * 0.72 * 1.0 * 1.0 * 1.0 = 72.0
    assert scored.total_score == 72.0


def test_scored_news_source_weight_applied():
    """Source weight should scale score."""
    high_weight = NewsItem(title="Test", source="Reuters", source_weight=1.0)
    low_weight = NewsItem(title="Test", source="Blog", source_weight=0.7)

    kwargs = dict(surprise=80, freshness=100, directional_clarity=100,
                  transmission_delay=50, market_awareness=50,
                  direction=Direction.LONG, category_score_mult=1.0)
    scored_high = ScoredNews(news=high_weight, **kwargs)
    scored_low = ScoredNews(news=low_weight, **kwargs)

    assert scored_high.total_score > scored_low.total_score


def test_scored_news_total_score_zero():
    news = NewsItem(title="Nothing", source="test")
    scored = ScoredNews(
        news=news,
        surprise=0,
        freshness=0,
        directional_clarity=0,
    )
    assert scored.total_score == 0.0


def test_scored_news_category_mult_effect():
    """Category multiplier should scale the final score."""
    news = NewsItem(title="Test", source="Reuters", source_weight=1.0)
    base_kwargs = dict(surprise=80, freshness=100, directional_clarity=100,
                       transmission_delay=50, market_awareness=30,
                       direction=Direction.LONG)

    commodity = ScoredNews(news=news, category_score_mult=1.5, **base_kwargs)
    earnings = ScoredNews(news=news, category_score_mult=0.2, **base_kwargs)

    assert commodity.total_score > earnings.total_score * 5


def test_chain_reaction_model():
    cr = ChainReaction(
        ticker="SI=F",
        direction=Direction.LONG,
        reason="Argent suit l'or",
        source_ticker="GC=F",
    )
    assert cr.ticker == "SI=F"
    assert cr.source_ticker == "GC=F"


def test_trade_recommendation_defaults():
    trade = TradeRecommendation(
        scan_type=ScanType.EUROPE,
        timestamp=datetime.now(timezone.utc),
        ticker="MC.PA",
        asset_name="LVMH",
        category="actions_europe",
        direction=Direction.LONG,
        catalyst="Test catalyst",
        entry_price=800.0,
        target_price=808.0,
        stop_price=793.2,
        target_pct=1.0,
        stop_pct=0.85,
        risk_reward=1.18,
        confidence=75,
        time_window="09:00 — 20:00",
    )
    assert trade.result == TradeResult.PENDING
    assert trade.exit_price is None
    assert trade.schema_version == 3
    assert trade.pre_move_pct is None
    assert trade.binary_event_warning is None
    assert trade.volume_confirmed is None
    assert trade.transmission_delay is None
    assert trade.market_awareness is None
    assert trade.edge_score is None
    assert trade.chain_reactions is None
    # v3 fields
    assert trade.raw_claude_score is None
    assert trade.learning_multiplier == 1.0
    assert trade.vix_at_trade is None
    assert trade.market_regime is None
    assert trade.day_of_week is None
    assert trade.predicted_transmission_delay is None


def test_scan_result_no_trade():
    result = ScanResult(
        scan_type=ScanType.US,
        timestamp=datetime.now(timezone.utc),
        has_trade=False,
        reason_no_trade="No actionable news",
        news_analyzed=42,
    )
    assert result.recommendation is None
    assert result.news_analyzed == 42
    assert result.market_context is None


def test_news_item_defaults():
    item = NewsItem(title="Breaking", source="Reuters")
    assert item.url == ""
    assert item.published is None
    assert item.related_tickers == []
    assert item.source_weight == 0.75


# ── v4.0 tests: reliability and magnitude ────────────────────


def test_scored_news_reliability_factor():
    """signal_reliability should scale the score — speculative rumor vs confirmed fact."""
    news = NewsItem(title="Test", source="Reuters", source_weight=1.0)
    base_kwargs = dict(surprise=80, freshness=100, directional_clarity=100,
                       transmission_delay=80, market_awareness=10,
                       direction=Direction.LONG, category_score_mult=1.0)

    # Confirmed fact: reliability_factor = 0.4 + 0.6*1.0 = 1.0
    confirmed = ScoredNews(news=news, signal_reliability=100, **base_kwargs)
    # Rumor: reliability_factor = 0.4 + 0.6*0.0 = 0.4
    rumor = ScoredNews(news=news, signal_reliability=0, **base_kwargs)

    assert confirmed.total_score > rumor.total_score
    # Confirmed should be ~2.5x higher than rumor (1.0/0.4)
    assert confirmed.total_score / rumor.total_score > 2.0


def test_scored_news_reliability_floor():
    """Even a pure rumor (reliability=0) retains 40% of value."""
    news = NewsItem(title="Test", source="Reuters", source_weight=1.0)
    confirmed = ScoredNews(news=news, surprise=80, freshness=100, directional_clarity=100,
                           transmission_delay=80, market_awareness=10,
                           signal_reliability=100, direction=Direction.LONG, category_score_mult=1.0)
    rumor = ScoredNews(news=news, surprise=80, freshness=100, directional_clarity=100,
                       transmission_delay=80, market_awareness=10,
                       signal_reliability=0, direction=Direction.LONG, category_score_mult=1.0)
    # Rumor score should be 40% of confirmed
    assert abs(rumor.total_score / confirmed.total_score - 0.4) < 0.01


def test_scored_news_magnitude_defaults():
    """Default expected_magnitude and signal_reliability should be 50."""
    news = NewsItem(title="Test", source="Reuters")
    scored = ScoredNews(news=news, surprise=50, freshness=50, directional_clarity=50,
                        direction=Direction.LONG)
    assert scored.expected_magnitude == 50
    assert scored.signal_reliability == 50


def test_trade_recommendation_has_magnitude_reliability():
    """TradeRecommendation should store expected_magnitude and signal_reliability."""
    trade = TradeRecommendation(
        scan_type=ScanType.EUROPE,
        timestamp=datetime.now(timezone.utc),
        ticker="MC.PA",
        asset_name="LVMH",
        category="actions_europe",
        direction=Direction.LONG,
        catalyst="Test",
        entry_price=800.0,
        target_price=808.0,
        stop_price=793.2,
        target_pct=1.0,
        stop_pct=0.85,
        risk_reward=1.18,
        confidence=75,
        time_window="09:00 — 20:00",
        expected_magnitude=70,
        signal_reliability=85,
    )
    assert trade.expected_magnitude == 70
    assert trade.signal_reliability == 85
