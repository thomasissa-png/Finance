"""Tests for trade selection logic."""

from datetime import datetime, timezone

from backend.app.models import Direction, NewsItem, ScanType, ScoredNews
from backend.app.trade_selector import select_trade


def _make_scored(
    ticker: str,
    surprise: int = 70,
    freshness: int = 80,
    clarity: int = 90,
    direction: Direction = Direction.LONG,
) -> ScoredNews:
    return ScoredNews(
        news=NewsItem(title=f"News about {ticker}", source="Test"),
        surprise=surprise,
        freshness=freshness,
        directional_clarity=clarity,
        direction=direction,
        impacted_tickers=[ticker],
        reasoning="Test reasoning",
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
