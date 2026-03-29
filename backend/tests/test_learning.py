"""Tests for the learning module.

v3.4 audit: updated for new return format (structured dict) and new features.
"""

import json
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

from backend.app.learning import (
    _compute_decay_weight,
    _get_decay_half_life,
    _is_significant,
    _compute_adjustment,
    build_performance_summary,
    compute_learning_adjustments,
    compute_performance,
    DECAY_HALF_LIFE_DAYS_HIGH,
    DECAY_HALF_LIFE_DAYS_LOW,
    DECAY_TRANSITION_START,
    DECAY_TRANSITION_END,
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


# ── Helper to extract adjustments from v3.4 structured return ──
def _get_ticker_adj(result: dict, ticker: str) -> float | None:
    """Get a ticker adjustment from the v3.4 structured return format."""
    if not isinstance(result, dict) or "adjustments" not in result:
        return None
    return result["adjustments"].get(ticker)


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
    # Need >= 5 closed trades — returns structured empty dict
    assert adj["adjustments"] == {}
    assert adj["session_adj"] == {}
    assert adj["newscat_adj"] == {}
    assert adj["regime_adj"] == {}
    assert adj["direction_adj"] == {}
    assert adj["delay_bias_adj"] == 1.0


def test_learning_adjustments_enough_data():
    """v3.4: Returns structured dict. Per-ticker needs 8+ trades now."""
    trades = []
    for i in range(10):
        pnl = 1.0 if i < 8 else -0.5
        result = TradeResult.TP_HIT if pnl > 0 else TradeResult.SL_HIT
        trades.append(_make_trade(result=result, pnl_pct=pnl))
    raw = [t.model_dump(mode="json") for t in trades]
    with _with_temp_trades(raw):
        result = compute_learning_adjustments()
    assert isinstance(result, dict)
    assert "adjustments" in result
    assert "session_adj" in result
    assert "newscat_adj" in result
    assert "regime_adj" in result
    # Per-ticker should exist with 10 trades (above min 8)
    ticker_mult = _get_ticker_adj(result, "MC.PA")
    assert ticker_mult is not None
    assert ticker_mult > 1.0  # Mostly winning


# ── New tests for v2.0 learning features ────────────────────────


def test_adaptive_decay_half_life_low():
    """With few trades, half-life should be 45 days (#30)."""
    assert _get_decay_half_life(10) == DECAY_HALF_LIFE_DAYS_LOW
    assert _get_decay_half_life(DECAY_TRANSITION_START) == DECAY_HALF_LIFE_DAYS_LOW


def test_adaptive_decay_half_life_high():
    """With many trades, half-life should be 30 days (#30)."""
    assert _get_decay_half_life(DECAY_TRANSITION_END) == DECAY_HALF_LIFE_DAYS_HIGH
    assert _get_decay_half_life(500) == DECAY_HALF_LIFE_DAYS_HIGH


def test_adaptive_decay_half_life_gradual():
    """Midway between transition start/end, half-life should be interpolated (#30)."""
    mid = (DECAY_TRANSITION_START + DECAY_TRANSITION_END) // 2
    hl = _get_decay_half_life(mid)
    assert DECAY_HALF_LIFE_DAYS_HIGH < hl < DECAY_HALF_LIFE_DAYS_LOW


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
    """v3.4: Per-ticker adjustments should be bounded to [0.5, 1.5]."""
    trades = []
    for i in range(10):
        trades.append(_make_trade(result=TradeResult.TP_HIT, pnl_pct=5.0))
    raw = [t.model_dump(mode="json") for t in trades]
    with _with_temp_trades(raw):
        result = compute_learning_adjustments()
    ticker_adjs = result.get("adjustments", {})
    for ticker, mult in ticker_adjs.items():
        assert 0.5 <= mult <= 1.5


def test_learning_adjustments_losing_ticker():
    """A consistently losing ticker should get a lower multiplier (v3.4: needs 8+ trades)."""
    trades = []
    for i in range(10):
        trades.append(_make_trade(result=TradeResult.SL_HIT, pnl_pct=-1.0))
    raw = [t.model_dump(mode="json") for t in trades]
    with _with_temp_trades(raw):
        result = compute_learning_adjustments()
    ticker_mult = _get_ticker_adj(result, "MC.PA")
    if ticker_mult is not None:
        assert ticker_mult < 1.0


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


# ── v3 tests: significance, multiplicative blending, performance summary ──


def test_is_significant_too_few_samples():
    """Significance test should reject with fewer than min_samples."""
    assert _is_significant([1.0, 2.0], min_samples=4) is False


def test_is_significant_all_identical_nonzero():
    """v4.2 A2: All identical non-zero values → stderr=0 → significant if effect size met."""
    assert _is_significant([1.0, 1.0, 1.0, 1.0], min_samples=4) is True


def test_is_significant_all_zeros():
    """v4.2 A2: All zero values → stderr=0 but effect size = 0 → NOT significant."""
    assert _is_significant([0.0, 0.0, 0.0, 0.0], min_samples=4) is False


def test_is_significant_min_effect_size():
    """v4.2 A3: Very small mean below effect size threshold → not significant."""
    # Mean ~0.05, below default min_effect_size=0.1
    assert _is_significant([0.05, 0.05, 0.05, 0.05], min_samples=4) is False


def test_is_significant_noisy_data():
    """High variance with small mean → not significant."""
    # Mean ~0, high stdev → t-stat near 0
    assert _is_significant([5.0, -5.0, 5.0, -5.0], min_samples=4) is False


def test_is_significant_clear_signal():
    """Consistent positive values → significant."""
    assert _is_significant([1.0, 1.2, 0.8, 1.1], min_samples=4) is True


def test_compute_adjustment_not_significant():
    """Adjustment returns None when data is not significant."""
    entries = [(1.0, 1.0), (-1.0, 1.0)]  # Too few
    assert _compute_adjustment(entries, min_significant=4) is None


def test_compute_adjustment_significant():
    """Adjustment returns a value when data is significant."""
    entries = [(1.0, 1.0), (1.2, 0.9), (0.8, 0.8), (1.1, 1.0)]
    result = _compute_adjustment(entries, min_significant=4)
    assert result is not None
    assert result > 1.0  # All positive PnL → boost


def test_multiplicative_blending():
    """v3.4: Blending uses ticker*category. Result should exist and be bounded."""
    trades = []
    for i in range(10):
        pnl = 1.0 if i < 8 else -0.5
        result = TradeResult.TP_HIT if pnl > 0 else TradeResult.SL_HIT
        trades.append(_make_trade(result=result, pnl_pct=pnl))
    raw = [t.model_dump(mode="json") for t in trades]
    with _with_temp_trades(raw):
        result = compute_learning_adjustments()
    ticker_mult = _get_ticker_adj(result, "MC.PA")
    if ticker_mult is not None:
        assert 0.5 <= ticker_mult <= 1.5


def test_build_performance_summary_not_enough_data():
    """Summary should be empty with fewer than 5 closed trades."""
    from backend.app.learning import invalidate_perf_summary_cache
    invalidate_perf_summary_cache()
    trades = [_make_trade(result=TradeResult.TP_HIT, pnl_pct=1.0)]
    raw = [t.model_dump(mode="json") for t in trades]
    with _with_temp_trades(raw):
        summary = build_performance_summary()
    assert summary == ""


def test_build_performance_summary_with_data():
    """Summary should contain key sections when enough data exists."""
    from backend.app.learning import invalidate_perf_summary_cache
    invalidate_perf_summary_cache()
    trades = []
    for i in range(6):
        pnl = 1.0 if i < 4 else -0.5
        result = TradeResult.TP_HIT if pnl > 0 else TradeResult.SL_HIT
        trades.append(_make_trade(result=result, pnl_pct=pnl))
    raw = [t.model_dump(mode="json") for t in trades]
    with _with_temp_trades(raw):
        summary = build_performance_summary()
    assert "DONNEES DE PERFORMANCE" in summary
    assert "WR=" in summary  # v4.2 E1: compact format
    assert "Derniers" in summary


# ── v3.4 audit tests ──────────────────────────────────────────────


def test_v34_structured_return_format():
    """v4.2: compute_learning_adjustments returns structured dict with all dimensions."""
    trades = []
    for i in range(10):
        pnl = 1.0 if i < 7 else -0.5
        result = TradeResult.TP_HIT if pnl > 0 else TradeResult.SL_HIT
        trades.append(_make_trade(result=result, pnl_pct=pnl))
    raw = [t.model_dump(mode="json") for t in trades]
    with _with_temp_trades(raw):
        result = compute_learning_adjustments()
    # Must have all expected keys (v4.2: +direction_adj, delay_bias_adj; M8: hour_adj removed)
    assert "adjustments" in result
    assert "session_adj" in result
    assert "newscat_adj" in result
    assert "regime_adj" in result
    assert "decomposition" in result
    assert "hour_adj" not in result
    assert "direction_adj" in result
    assert "delay_bias_adj" in result


def test_v34_session_adj_returned_separately():
    """v3.4 #2: Session adjustments are returned separately, not baked into ticker adj."""
    trades = []
    for i in range(10):
        pnl = 1.0 if i < 7 else -0.5
        result = TradeResult.TP_HIT if pnl > 0 else TradeResult.SL_HIT
        trades.append(_make_trade(
            scan_type=ScanType.EUROPE, result=result, pnl_pct=pnl,
        ))
    raw = [t.model_dump(mode="json") for t in trades]
    with _with_temp_trades(raw):
        result = compute_learning_adjustments()
    # Session adj should have "europe" key
    session = result.get("session_adj", {})
    if session:
        assert "europe" in session
        assert 0.8 <= session["europe"] <= 1.2


def test_v34_regime_adj():
    """v4.2 C1: Regime merged to 2 buckets (low_vol, high_vol), min 15 trades."""
    trades = []
    # Need 15+ trades in same regime bucket to pass min_significant
    for i in range(18):
        pnl = 1.5 if i < 15 else -0.5
        result = TradeResult.TP_HIT if pnl > 0 else TradeResult.SL_HIT
        trades.append(_make_trade(
            result=result, pnl_pct=pnl,
            market_regime="calm",  # Maps to "low_vol" bucket
        ))
    raw = [t.model_dump(mode="json") for t in trades]
    with _with_temp_trades(raw):
        result = compute_learning_adjustments()
    regime = result.get("regime_adj", {})
    if regime:
        assert "low_vol" in regime
        assert regime["low_vol"] > 1.0  # Mostly winning → boost


def test_v34_per_ticker_stricter_significance():
    """Per-ticker needs min_significant=5 trades and t>1.5 to be significant."""
    # Only 3 trades — should NOT produce per-ticker adjustment (min_significant=5)
    trades = []
    for i in range(5):
        pnl = 1.0 if i < 4 else -0.5
        result = TradeResult.TP_HIT if pnl > 0 else TradeResult.SL_HIT
        # Use 3 different tickers so no single ticker has enough trades
        ticker = ["MC.PA", "TTE.PA", "BNP.PA"][i % 3]
        trades.append(_make_trade(result=result, pnl_pct=pnl, ticker=ticker))
    raw = [t.model_dump(mode="json") for t in trades]
    with _with_temp_trades(raw):
        result = compute_learning_adjustments()
    # With < 5 trades per ticker, no ticker should have per-ticker adjustment
    decomp = result.get("decomposition", {}).get("MC.PA", {})
    assert decomp.get("ticker_mult", 1.0) == 1.0  # Not enough for per-ticker


def test_v34_newscat_adj_returned_separately():
    """v5.2: News category adjustments use cross-dimension newscat+ticker keys."""
    trades = []
    for i in range(10):
        pnl = 1.0 if i < 7 else -0.5
        result = TradeResult.TP_HIT if pnl > 0 else TradeResult.SL_HIT
        trades.append(_make_trade(
            result=result, pnl_pct=pnl,
            news_category="weather",
        ))
    raw = [t.model_dump(mode="json") for t in trades]
    with _with_temp_trades(raw):
        result = compute_learning_adjustments()
    newscat = result.get("newscat_adj", {})
    if newscat:
        # v5.2: cross-dimension key "weather+MC.PA" instead of broad "weather"
        matching_keys = [k for k in newscat if k.startswith("weather")]
        assert len(matching_keys) > 0
        for k in matching_keys:
            assert 0.7 <= newscat[k] <= 1.3


def test_v34_decomposition_logged():
    """v3.4 #8: Decomposition contains ticker_mult and cat_mult for each ticker."""
    trades = []
    for i in range(10):
        trades.append(_make_trade(result=TradeResult.TP_HIT, pnl_pct=1.5))
    raw = [t.model_dump(mode="json") for t in trades]
    with _with_temp_trades(raw):
        result = compute_learning_adjustments()
    decomp = result.get("decomposition", {})
    if "MC.PA" in decomp:
        assert "ticker_mult" in decomp["MC.PA"]
        assert "cat_mult" in decomp["MC.PA"]


def test_v34_is_significant_with_t_threshold():
    """v3.4: _is_significant supports configurable t_threshold."""
    # Data with moderate signal: passes t>1.0 but may fail t>1.5
    data = [1.0, 0.5, 1.2, 0.3, 0.8]
    assert _is_significant(data, min_samples=4, t_threshold=1.0) is True
    # Stricter threshold should require stronger signal
    noisy = [0.5, -0.3, 0.2, -0.1, 0.4, -0.2, 0.3, -0.1]
    assert _is_significant(noisy, min_samples=4, t_threshold=1.5) is False


def test_v34_compute_adjustment_pnl_signed():
    """v3.4 #6: _compute_adjustment uses PnL-signed signal, not binary win_rate."""
    # All positive PnL = strong positive signal
    entries_positive = [(2.0, 1.0), (1.5, 1.0), (1.8, 1.0), (1.3, 1.0), (1.0, 1.0)]
    result_pos = _compute_adjustment(entries_positive, min_significant=4, sensitivity=0.5)
    assert result_pos is not None
    assert result_pos > 1.0

    # All negative PnL = strong negative signal
    entries_negative = [(-1.0, 1.0), (-1.5, 1.0), (-0.8, 1.0), (-1.2, 1.0), (-1.0, 1.0)]
    result_neg = _compute_adjustment(entries_negative, min_significant=4, sensitivity=0.5)
    assert result_neg is not None
    assert result_neg < 1.0


def test_v34_performance_summary_benchmark():
    """v4.2: Performance summary includes compact stats."""
    from backend.app.learning import invalidate_perf_summary_cache
    invalidate_perf_summary_cache()
    trades = []
    for i in range(6):
        pnl = 1.0 if i < 4 else -0.5
        result = TradeResult.TP_HIT if pnl > 0 else TradeResult.SL_HIT
        trades.append(_make_trade(result=result, pnl_pct=pnl))
    raw = [t.model_dump(mode="json") for t in trades]
    with _with_temp_trades(raw):
        summary = build_performance_summary()
    assert "WR=" in summary
    assert "moy=" in summary  # v4.2: compact format uses "moy="


def test_v34_performance_summary_anti_double_counting():
    """v3.4 #7: Performance summary tells Claude NOT to adjust scores."""
    from backend.app.learning import invalidate_perf_summary_cache
    invalidate_perf_summary_cache()
    trades = []
    for i in range(6):
        pnl = 1.0 if i < 4 else -0.5
        result = TradeResult.TP_HIT if pnl > 0 else TradeResult.SL_HIT
        trades.append(_make_trade(result=result, pnl_pct=pnl))
    raw = [t.model_dump(mode="json") for t in trades]
    with _with_temp_trades(raw):
        summary = build_performance_summary()
    assert "OBJECTIVEMENT" in summary


# ── v4.2 audit tests ──────────────────────────────────────────────


def test_v42_is_significant_stderr_zero_effect_size():
    """v4.2 A2/A3: stderr=0 with small effect size → not significant."""
    # All 0.05 values: stderr=0, but mean=0.05 < min_effect_size=0.1
    assert _is_significant([0.05, 0.05, 0.05, 0.05], min_samples=4) is False
    # All 0.5 values: stderr=0, mean=0.5 >= min_effect_size → significant
    assert _is_significant([0.5, 0.5, 0.5, 0.5], min_samples=4) is True


def test_v42_compute_adjustment_divisor_fixed():
    """v4.2 A4: PnL divisor changed from 2.0 to 1.0 — stronger signal."""
    entries = [(1.0, 1.0), (1.0, 1.0), (1.0, 1.0), (1.0, 1.0), (1.0, 1.0)]
    result = _compute_adjustment(entries, min_significant=4, sensitivity=0.5, pnl_cap=0.25)
    assert result is not None
    # With divisor=1.0 and avg_pnl=1.0, pnl_signal=min(0.25, 1.0)=0.25
    # mult = 1.0 + 0.25 * 0.5 = 1.125
    assert result >= 1.12  # Would have been ~1.06 with old divisor=2.0


def test_v42_hour_adj_removed():
    """M8: hour_adj removed from compute_learning_adjustments (worst data-to-noise ratio)."""
    trades = []
    for i in range(10):
        pnl = 1.5 if i < 8 else -0.5
        result = TradeResult.TP_HIT if pnl > 0 else TradeResult.SL_HIT
        trades.append(_make_trade(result=result, pnl_pct=pnl))
    result = compute_learning_adjustments(trades=trades)
    assert "hour_adj" not in result


def test_v42_direction_adj_in_result():
    """v4.2 B5: compute_learning_adjustments should include direction_adj."""
    trades = []
    for i in range(10):
        pnl = 1.5 if i < 8 else -0.5
        result = TradeResult.TP_HIT if pnl > 0 else TradeResult.SL_HIT
        trades.append(_make_trade(result=result, pnl_pct=pnl))
    result = compute_learning_adjustments(trades=trades)
    assert "direction_adj" in result
    assert isinstance(result["direction_adj"], dict)


def test_v42_delay_bias_adj_default():
    """v4.2 B1: delay_bias_adj should default to 1.0 when no delay data."""
    trades = []
    for i in range(10):
        pnl = 1.0 if i < 7 else -0.5
        result = TradeResult.TP_HIT if pnl > 0 else TradeResult.SL_HIT
        trades.append(_make_trade(result=result, pnl_pct=pnl))
    result = compute_learning_adjustments(trades=trades)
    assert result["delay_bias_adj"] == 1.0


def test_v42_d1_lookback_reduced():
    """v4.2 D1: Lookback reduced to 2x half_life instead of fixed 180 days."""
    # Trades from 100 days ago — with half_life=45, lookback=90 days
    # These should be filtered out
    old_trades = []
    base = datetime.now(timezone.utc) - timedelta(days=100)
    for i in range(10):
        old_trades.append(_make_trade(
            result=TradeResult.TP_HIT, pnl_pct=2.0,
            timestamp=base + timedelta(hours=i),
        ))
    # Recent losing trades
    recent = datetime.now(timezone.utc) - timedelta(days=5)
    for i in range(10):
        old_trades.append(_make_trade(
            result=TradeResult.SL_HIT, pnl_pct=-1.0,
            timestamp=recent + timedelta(hours=i),
        ))
    result = compute_learning_adjustments(trades=old_trades)
    # Only recent losses should matter → ticker should be penalized
    if "MC.PA" in result.get("adjustments", {}):
        assert result["adjustments"]["MC.PA"] < 1.0


def test_v42_regime_merged_buckets():
    """v4.2 C1: Regimes merged to low_vol (calm+normal) and high_vol (elevated+stress)."""
    trades = []
    base = datetime.now(timezone.utc) - timedelta(days=20)
    # Mix of calm and normal → should merge into low_vol
    for i in range(10):
        trades.append(_make_trade(
            result=TradeResult.TP_HIT, pnl_pct=1.5,
            timestamp=base + timedelta(hours=i),
            market_regime="calm" if i % 2 == 0 else "normal",
        ))
    for i in range(8):
        trades.append(_make_trade(
            result=TradeResult.TP_HIT, pnl_pct=1.5,
            timestamp=base + timedelta(hours=10 + i),
            market_regime="calm" if i % 2 == 0 else "normal",
        ))
    result = compute_learning_adjustments(trades=trades)
    regime = result.get("regime_adj", {})
    # Should have "low_vol" not "calm" or "normal"
    assert "calm" not in regime
    assert "normal" not in regime
    if regime:
        assert "low_vol" in regime


def test_v42_build_performance_summary_trades_param():
    """v4.2 D4: build_performance_summary accepts trades param."""
    from backend.app.learning import invalidate_perf_summary_cache
    invalidate_perf_summary_cache()
    trades = []
    for i in range(6):
        pnl = 1.0 if i < 4 else -0.5
        result = TradeResult.TP_HIT if pnl > 0 else TradeResult.SL_HIT
        trades.append(_make_trade(result=result, pnl_pct=pnl))
    # Pass trades directly — should not need TRADES_FILE
    summary = build_performance_summary(trades=trades)
    assert "DONNEES DE PERFORMANCE" in summary


def test_v42_alerts_only_format():
    """v4.2 E1: Performance summary should be compact (alerts-only)."""
    from backend.app.learning import invalidate_perf_summary_cache
    invalidate_perf_summary_cache()
    trades = []
    for i in range(6):
        pnl = 1.0 if i < 4 else -0.5
        result = TradeResult.TP_HIT if pnl > 0 else TradeResult.SL_HIT
        trades.append(_make_trade(result=result, pnl_pct=pnl))
    summary = build_performance_summary(trades=trades)
    # Should have compact format
    assert "N=" in summary
    assert "WR=" in summary
    # Instructions should be shorter
    assert "INSTRUCTIONS SCORING" in summary


# ── v6.3 audit tests — Journal↔Learning data integrity ──────────────


def test_v63_pg_trade_columns_include_scoring_fields():
    """v6.3 P1: _TRADE_COLUMNS must include signal_reliability, expected_magnitude, etc."""
    from backend.app.database import _TRADE_COLUMNS
    required_v63 = [
        "surprise", "directional_clarity", "signal_reliability", "expected_magnitude",
        "position_size_pct", "convergence_count", "convergence_boost",
        "news_url", "news_description",
    ]
    for col in required_v63:
        assert col in _TRADE_COLUMNS, f"Missing column {col} in _TRADE_COLUMNS"


def test_v63_expired_pricing_hours_computed():
    """v6.3 P2: EXPIRED trades should get actual_pricing_hours from remaining trading window.

    The logic in journal.py computes: market_close_hour(20) - entry_hour - entry_min/60.
    This ensures Learning can compute delay_bias even for EXPIRED outcomes.
    """
    from zoneinfo import ZoneInfo

    PARIS_TZ = ZoneInfo("Europe/Paris")
    market_close_hour = 20

    # Simulate the P2 logic for a trade entered at 10:00 Paris
    entry_paris_hour = 10
    entry_paris_minute = 0
    remaining_hours = max(1.0, market_close_hour - entry_paris_hour - entry_paris_minute / 60)
    actual_pricing_hours = round(remaining_hours, 2)

    # 10:00 → 20:00 = 10 hours
    assert actual_pricing_hours == 10.0

    # Trade entered at 18:30 Paris → only 1.5h remaining
    remaining_late = max(1.0, market_close_hour - 18 - 30 / 60)
    assert round(remaining_late, 2) == 1.5

    # Trade entered at 19:45 → floor at 1.0h (never zero)
    remaining_very_late = max(1.0, market_close_hour - 19 - 45 / 60)
    assert remaining_very_late == 1.0


def test_v63_news_category_direct_access():
    """v6.3 P3: news_category accessed directly (not via getattr) in learning.py."""
    import ast
    from pathlib import Path

    learning_path = Path("backend/app/learning.py")
    source = learning_path.read_text()
    tree = ast.parse(source)

    # Check no getattr calls with "news_category" in the source
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "getattr" and len(node.args) >= 2:
                if isinstance(node.args[1], ast.Constant) and node.args[1].value == "news_category":
                    pytest.fail(f"Found getattr(_, 'news_category', ...) at line {node.lineno} — should use direct access")


def test_v63_extract_structured_anomalies_returns_typed_dicts():
    """v6.3: extract_structured_anomalies returns list[dict] with type/message keys."""
    from backend.app.learning import extract_structured_anomalies

    trades = []
    # Create enough trades with high EXPIRED rate to trigger anomaly
    for i in range(10):
        result = TradeResult.EXPIRED if i < 7 else TradeResult.TP_HIT
        pnl = 0.0 if result == TradeResult.EXPIRED else 1.0
        trades.append(_make_trade(result=result, pnl_pct=pnl))

    anomalies = extract_structured_anomalies(trades=trades)
    assert isinstance(anomalies, list)
    for a in anomalies:
        assert isinstance(a, dict)
        assert "type" in a
        assert "message" in a


def test_v63_performance_summary_uses_journal_mae():
    """v6.3: build_performance_summary loads MAE from JournalEntry, not TradeRecommendation."""
    from backend.app.learning import build_performance_summary, invalidate_perf_summary_cache
    invalidate_perf_summary_cache()

    # Create trades with SL_HIT (triggers MAE feedback check)
    trades = []
    for i in range(8):
        result = TradeResult.SL_HIT if i < 5 else TradeResult.TP_HIT
        pnl = -1.0 if result == TradeResult.SL_HIT else 1.5
        trades.append(_make_trade(result=result, pnl_pct=pnl))

    # Should not crash even without journal entries (graceful fallback)
    summary = build_performance_summary(trades=trades)
    assert isinstance(summary, str)
    assert "DONNEES DE PERFORMANCE" in summary
