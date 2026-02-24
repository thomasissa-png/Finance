"""Tests for the learning module."""

import json
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

from backend.app.learning import (
    _compute_decay_weight,
    _get_decay_half_life,
    compute_learning_adjustments,
    compute_performance,
    DECAY_HALF_LIFE_DAYS_HIGH,
    DECAY_HALF_LIFE_DAYS_LOW,
    load_trades,
    save_trade,
    TRADES_THRESHOLD_FOR_FAST_DECAY,
    update_trade_result,
)
from backend.app.models import (
    Direction,
    ScanType,
    TradeRecommendation,
    TradeResult,
)


def _make_trade(**overrides) -> TradeRecommendation:
    defaults = dict(
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
    )
    defaults.update(overrides)
    return TradeRecommendation(**defaults)


def _with_temp_trades(trades_data):
    """Patch TRADES_FILE to a temp file with given data."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(trades_data, tmp, default=str)
    tmp.close()
    return patch("backend.app.learning.TRADES_FILE", Path(tmp.name))


def test_load_empty():
    with _with_temp_trades([]):
        trades = load_trades()
    assert trades == []


def test_save_and_load():
    trade = _make_trade()
    with _with_temp_trades([]) as mock_path:
        save_trade(trade)
        trades = load_trades()
    assert len(trades) == 1
    assert trades[0].ticker == "MC.PA"


def test_compute_performance_empty():
    with _with_temp_trades([]):
        stats = compute_performance()
    assert stats.total_trades == 0
    assert stats.win_rate == 0.0


def test_compute_performance_with_trades():
    t1 = _make_trade(result=TradeResult.TP_HIT, pnl_pct=1.2)
    t2 = _make_trade(result=TradeResult.SL_HIT, pnl_pct=-0.8, ticker="TTE.PA", category="actions_europe")
    t3 = _make_trade(result=TradeResult.TP_HIT, pnl_pct=1.5)
    raw = [t.model_dump(mode="json") for t in [t1, t2, t3]]
    with _with_temp_trades(raw):
        stats = compute_performance()
    assert stats.total_trades == 3
    assert stats.wins == 2
    assert stats.losses == 1
    assert stats.win_rate == round(2 / 3 * 100, 1)


def test_learning_adjustments_not_enough_data():
    t1 = _make_trade(result=TradeResult.TP_HIT, pnl_pct=1.0)
    raw = [t1.model_dump(mode="json")]
    with _with_temp_trades(raw):
        adj = compute_learning_adjustments()
    assert adj == {}  # Need >= 5 closed trades


def test_learning_adjustments_enough_data():
    trades = []
    for i in range(6):
        pnl = 1.0 if i < 5 else -0.5
        result = TradeResult.TP_HIT if pnl > 0 else TradeResult.SL_HIT
        trades.append(_make_trade(result=result, pnl_pct=pnl))
    raw = [t.model_dump(mode="json") for t in trades]
    with _with_temp_trades(raw):
        adj = compute_learning_adjustments()
    assert "MC.PA" in adj
    assert adj["MC.PA"] > 1.0


# ── New tests for v2.0 learning features ────────────────────────


def test_adaptive_decay_half_life_low():
    """With few trades, half-life should be 45 days (#30)."""
    assert _get_decay_half_life(10) == DECAY_HALF_LIFE_DAYS_LOW
    assert _get_decay_half_life(199) == DECAY_HALF_LIFE_DAYS_LOW


def test_adaptive_decay_half_life_high():
    """With many trades, half-life should be 30 days (#30)."""
    assert _get_decay_half_life(200) == DECAY_HALF_LIFE_DAYS_HIGH
    assert _get_decay_half_life(500) == DECAY_HALF_LIFE_DAYS_HIGH


def test_decay_weight_recent_trade():
    """A trade from just now should have weight close to 1.0."""
    now = datetime.now(timezone.utc)
    w = _compute_decay_weight(now, 30.0)
    assert 0.99 <= w <= 1.0


def test_decay_weight_old_trade():
    """A trade from 60 days ago should have lower weight with 30-day half-life."""
    old = datetime.now(timezone.utc) - timedelta(days=60)
    w = _compute_decay_weight(old, 30.0)
    assert 0.2 <= w <= 0.3


def test_decay_weight_half_life():
    """At exactly one half-life, weight should be ~0.5."""
    one_hl = datetime.now(timezone.utc) - timedelta(days=30)
    w = _compute_decay_weight(one_hl, 30.0)
    assert 0.45 <= w <= 0.55


def test_learning_adjustments_bounded():
    """Adjustments should be bounded to [0.5, 1.5]."""
    trades = []
    for i in range(10):
        trades.append(_make_trade(result=TradeResult.TP_HIT, pnl_pct=5.0))
    raw = [t.model_dump(mode="json") for t in trades]
    with _with_temp_trades(raw):
        adj = compute_learning_adjustments()
    for ticker, mult in adj.items():
        assert 0.5 <= mult <= 1.5


def test_learning_adjustments_losing_ticker():
    """A consistently losing ticker should get a lower multiplier."""
    trades = []
    for i in range(6):
        trades.append(_make_trade(result=TradeResult.SL_HIT, pnl_pct=-1.0))
    raw = [t.model_dump(mode="json") for t in trades]
    with _with_temp_trades(raw):
        adj = compute_learning_adjustments()
    if "MC.PA" in adj:
        assert adj["MC.PA"] < 1.0


def test_performance_by_category():
    """Performance should break down by category."""
    t1 = _make_trade(result=TradeResult.TP_HIT, pnl_pct=1.2, category="forex")
    t2 = _make_trade(result=TradeResult.SL_HIT, pnl_pct=-0.8, category="indices")
    raw = [t.model_dump(mode="json") for t in [t1, t2]]
    with _with_temp_trades(raw):
        stats = compute_performance()
    assert "forex" in stats.by_category
    assert "indices" in stats.by_category
    assert stats.by_category["forex"]["wins"] == 1
    assert stats.by_category["indices"]["wins"] == 0


def test_performance_by_scan_type():
    """Performance should break down by scan type."""
    t1 = _make_trade(scan_type=ScanType.EUROPE, result=TradeResult.TP_HIT, pnl_pct=1.0)
    t2 = _make_trade(scan_type=ScanType.US, result=TradeResult.SL_HIT, pnl_pct=-0.5)
    raw = [t.model_dump(mode="json") for t in [t1, t2]]
    with _with_temp_trades(raw):
        stats = compute_performance()
    assert "europe" in stats.by_scan_type
    assert "us" in stats.by_scan_type
