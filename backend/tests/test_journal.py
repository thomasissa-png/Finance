"""Tests for the journal module."""

import json
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

from backend.app.journal import (
    _build_review,
    _determine_result,
    load_journal,
    run_daily_journal,
)
from backend.app.models import (
    Direction,
    JournalEntry,
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
        news_headline="LVMH beats earnings estimates",
        news_category="earnings",
        catalyst="Test catalyst news",
        entry_price=800.0,
        target_price=808.0,
        stop_price=793.2,
        target_pct=1.0,
        stop_pct=0.85,
        risk_reward=1.18,
        confidence=75,
        time_window="09:00 — 20:00",
        news_sources=["Reuters"],
    )
    defaults.update(overrides)
    return TradeRecommendation(**defaults)


def _with_temp_file(data, target_attr):
    """Patch a file path to a temp file with given data."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(data, tmp, default=str)
    tmp.close()
    return patch(target_attr, Path(tmp.name))


# ── _determine_result tests ────────────────────────────────────


def test_determine_result_long_tp_hit():
    trade = _make_trade(direction=Direction.LONG, entry_price=100, target_price=105, stop_price=97)
    result, exit_price, pnl = _determine_result(trade, day_high=106, day_low=99, close=104)
    assert result == TradeResult.TP_HIT
    assert exit_price == 105
    assert pnl == 5.0


def test_determine_result_long_sl_hit():
    trade = _make_trade(direction=Direction.LONG, entry_price=100, target_price=105, stop_price=97)
    result, exit_price, pnl = _determine_result(trade, day_high=101, day_low=96, close=97)
    assert result == TradeResult.SL_HIT
    assert exit_price == 97
    assert pnl == -3.0


def test_determine_result_long_expired():
    trade = _make_trade(direction=Direction.LONG, entry_price=100, target_price=105, stop_price=97)
    result, exit_price, pnl = _determine_result(trade, day_high=103, day_low=99, close=102)
    assert result == TradeResult.EXPIRED
    assert exit_price == 102
    assert pnl == 2.0


def test_determine_result_long_tp_priority():
    """When both TP and SL could be hit in the same day, TP takes priority."""
    trade = _make_trade(direction=Direction.LONG, entry_price=100, target_price=105, stop_price=97)
    result, exit_price, pnl = _determine_result(trade, day_high=106, day_low=96, close=101)
    assert result == TradeResult.TP_HIT  # TP checked first


def test_determine_result_short_tp_hit():
    trade = _make_trade(direction=Direction.SHORT, entry_price=100, target_price=95, stop_price=103)
    result, exit_price, pnl = _determine_result(trade, day_high=101, day_low=94, close=96)
    assert result == TradeResult.TP_HIT
    assert exit_price == 95
    assert pnl == 5.0


def test_determine_result_short_sl_hit():
    trade = _make_trade(direction=Direction.SHORT, entry_price=100, target_price=95, stop_price=103)
    result, exit_price, pnl = _determine_result(trade, day_high=104, day_low=99, close=103)
    assert result == TradeResult.SL_HIT
    assert exit_price == 103
    assert pnl == -3.0


def test_determine_result_short_expired():
    trade = _make_trade(direction=Direction.SHORT, entry_price=100, target_price=95, stop_price=103)
    result, exit_price, pnl = _determine_result(trade, day_high=102, day_low=97, close=98)
    assert result == TradeResult.EXPIRED
    assert exit_price == 98
    assert pnl == 2.0


def test_determine_result_no_data():
    trade = _make_trade()
    result, exit_price, pnl = _determine_result(trade, None, None, None)
    assert result == TradeResult.EXPIRED
    assert exit_price is None
    assert pnl is None


# ── _build_review tests ────────────────────────────────────────


def test_review_tp():
    trade = _make_trade()
    review = _build_review(trade, TradeResult.TP_HIT, 1.5)
    assert "Objectif atteint" in review
    assert "+1.50%" in review


def test_review_sl():
    trade = _make_trade()
    review = _build_review(trade, TradeResult.SL_HIT, -0.8)
    assert "Stop touche" in review
    assert "-0.80%" in review


def test_review_expired_positive():
    trade = _make_trade()
    review = _build_review(trade, TradeResult.EXPIRED, 0.3)
    assert "Expire en gain" in review


def test_review_expired_negative():
    trade = _make_trade()
    review = _build_review(trade, TradeResult.EXPIRED, -0.2)
    assert "Expire en perte" in review


def test_review_expired_none():
    trade = _make_trade()
    review = _build_review(trade, TradeResult.EXPIRED, None)
    assert "sans mouvement" in review


# ── load_journal tests ─────────────────────────────────────────


def test_load_journal_empty():
    with _with_temp_file([], "backend.app.journal.JOURNAL_FILE"):
        entries = load_journal()
    assert entries == []


def test_load_journal_with_entry():
    entry = JournalEntry(
        date="2025-01-15",
        scan_type=ScanType.EUROPE,
        news_title="LVMH beats earnings",
        news_source="Reuters",
        news_category="earnings",
        reasoning="Test reasoning",
        score=75,
        ticker="MC.PA",
        asset_name="LVMH",
        asset_category="actions_europe",
        direction=Direction.LONG,
        entry_time=datetime.now(timezone.utc),
        entry_price=800.0,
        result=TradeResult.TP_HIT,
        pnl_pct=1.2,
        review="Good trade",
    )
    raw = [entry.model_dump(mode="json")]
    with _with_temp_file(raw, "backend.app.journal.JOURNAL_FILE"):
        entries = load_journal()
    assert len(entries) == 1
    assert entries[0].ticker == "MC.PA"
    assert entries[0].pnl_pct == 1.2
    assert entries[0].news_category == "earnings"
    assert entries[0].asset_category == "actions_europe"


# ── run_daily_journal integration test ─────────────────────────


def test_run_daily_journal_no_pending():
    """No pending trades → empty journal output."""
    with _with_temp_file([], "backend.app.journal.JOURNAL_FILE"), \
         patch("backend.app.journal.load_trades", return_value=[]), \
         patch("backend.app.journal.compute_learning_adjustments", return_value={}):
        result = run_daily_journal()
    assert result == []


def test_run_daily_journal_with_pending_trade():
    """One pending trade → generates one journal entry."""
    now = datetime.now(timezone.utc)
    trade = _make_trade(timestamp=now, result=TradeResult.PENDING)

    with _with_temp_file([], "backend.app.journal.JOURNAL_FILE"), \
         patch("backend.app.journal.load_trades", return_value=[trade]), \
         patch("backend.app.journal._fetch_day_prices", return_value=(810.0, 795.0, 805.0)), \
         patch("backend.app.journal.update_trade_result") as mock_update, \
         patch("backend.app.journal.compute_learning_adjustments", return_value={}):
        result = run_daily_journal()

    assert len(result) == 1
    entry = result[0]
    assert entry["ticker"] == "MC.PA"
    assert entry["result"] == "TP_HIT"  # day_high 810 > target 808
    assert entry["day_high"] == 810.0
    assert entry["day_low"] == 795.0
    assert entry["news_title"] == "LVMH beats earnings estimates"
    assert entry["news_category"] == "earnings"
    assert entry["asset_category"] == "actions_europe"
    mock_update.assert_called_once()


def test_run_daily_journal_dedup():
    """Duplicate trades should not create duplicate journal entries."""
    now = datetime.now(timezone.utc)
    trade = _make_trade(timestamp=now, result=TradeResult.PENDING)

    # Simulate existing journal entry for same trade
    existing_entry = JournalEntry(
        date=now.strftime("%Y-%m-%d"),
        scan_type=ScanType.EUROPE,
        news_title="LVMH beats earnings estimates",
        news_source="Reuters",
        reasoning="Test",
        score=75,
        ticker="MC.PA",
        asset_name="LVMH",
        direction=Direction.LONG,
        entry_time=now,
        entry_price=800.0,
        result=TradeResult.TP_HIT,
    )
    existing_raw = [existing_entry.model_dump(mode="json")]

    with _with_temp_file(existing_raw, "backend.app.journal.JOURNAL_FILE"), \
         patch("backend.app.journal.load_trades", return_value=[trade]), \
         patch("backend.app.journal.compute_learning_adjustments", return_value={}):
        result = run_daily_journal()

    assert result == []  # Skipped because already in journal
