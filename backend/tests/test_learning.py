"""Tests for the learning module."""

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from backend.app.learning import (
    compute_learning_adjustments,
    compute_performance,
    load_trades,
    save_trade,
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
    # MC.PA should have a positive adjustment (5/6 win rate)
    assert "MC.PA" in adj
    assert adj["MC.PA"] > 1.0
