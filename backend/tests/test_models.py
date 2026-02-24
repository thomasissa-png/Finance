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


def test_scored_news_total_score():
    news = NewsItem(title="Test", source="test")
    scored = ScoredNews(
        news=news,
        surprise=80,
        freshness=60,
        directional_clarity=100,
        direction=Direction.LONG,
    )
    # 80*0.4 + 60*0.3 + 100*0.3 = 32 + 18 + 30 = 80
    assert scored.total_score == 80.0


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
        time_window="09:00 — 13:00",
    )
    assert trade.result == TradeResult.PENDING
    assert trade.exit_price is None
    assert trade.pnl_pct is None


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


def test_news_item_defaults():
    item = NewsItem(title="Breaking", source="Reuters")
    assert item.url == ""
    assert item.published is None
    assert item.related_tickers == []
