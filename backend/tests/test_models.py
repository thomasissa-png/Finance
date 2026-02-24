"""Tests for data models."""

from datetime import datetime, timezone

from backend.app.models import (
    Direction,
    NewsItem,
    ScanResult,
    ScanType,
    ScoredNews,
    TradeRecommendation,
    TradeResult,
)


def test_scored_news_total_score_multiplicative():
    """Score is now multiplicative (#3): surprise * freshness/100 * clarity/100."""
    news = NewsItem(title="Test", source="Reuters", source_weight=1.0)
    scored = ScoredNews(
        news=news,
        surprise=80,
        freshness=100,
        directional_clarity=100,
        direction=Direction.LONG,
    )
    # 80 * (100/100) * (100/100) * 1.0 = 80
    assert scored.total_score == 80.0


def test_scored_news_total_score_freshness_penalty():
    """Stale news should dramatically reduce score (#3)."""
    news = NewsItem(title="Test", source="Reuters", source_weight=1.0)
    scored = ScoredNews(
        news=news,
        surprise=80,
        freshness=50,
        directional_clarity=100,
        direction=Direction.LONG,
    )
    # 80 * (50/100) * (100/100) * 1.0 = 40
    assert scored.total_score == 40.0


def test_scored_news_stale_cap():
    """When freshness < 30, score is capped at 20 (#3)."""
    news = NewsItem(title="Test", source="Reuters", source_weight=1.0)
    scored = ScoredNews(
        news=news,
        surprise=100,
        freshness=20,
        directional_clarity=100,
        direction=Direction.LONG,
    )
    # 100 * (20/100) * (100/100) = 20, but freshness < 30 → capped at 20
    assert scored.total_score <= 20.0


def test_scored_news_source_weight_applied():
    """Source weight should reduce score for less reliable sources (#6)."""
    high_weight = NewsItem(title="Test", source="Reuters", source_weight=1.0)
    low_weight = NewsItem(title="Test", source="Blog", source_weight=0.7)

    scored_high = ScoredNews(news=high_weight, surprise=80, freshness=100, directional_clarity=100, direction=Direction.LONG)
    scored_low = ScoredNews(news=low_weight, surprise=80, freshness=100, directional_clarity=100, direction=Direction.LONG)

    assert scored_high.total_score > scored_low.total_score
    assert scored_low.total_score == 80.0 * 0.7  # 56


def test_scored_news_total_score_zero():
    news = NewsItem(title="Nothing", source="test")
    scored = ScoredNews(
        news=news,
        surprise=0,
        freshness=0,
        directional_clarity=0,
    )
    assert scored.total_score == 0.0


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
    assert trade.pnl_pct is None
    assert trade.schema_version == 2  # (#42)
    assert trade.pre_move_pct is None  # (#4)
    assert trade.binary_event_warning is None  # (#24)
    assert trade.volume_confirmed is None  # (#10)


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
    assert result.market_context is None  # (#5)


def test_news_item_defaults():
    item = NewsItem(title="Breaking", source="Reuters")
    assert item.url == ""
    assert item.published is None
    assert item.related_tickers == []
    assert item.source_weight == 0.75  # (#6) default weight
