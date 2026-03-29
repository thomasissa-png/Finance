"""Tests for Agent Scoring 3 — Technical Indicators (Team 3).

Tests:
1. Combo strategy detectors (v2.1)
2. Strategy registration (all strategies in STRATEGIES + detector_map + regime prefs)
3. Scoring function integration
4. Version tracking
"""

import pytest

from backend.app.agents.agent_scoring_3 import (
    AgentScoring3,
    STRATEGIES,
    STRATEGY_REGIME_PREFERENCE,
    MIN_SETUP_SCORE,
    _detect_rsi_macd_combo,
    _detect_bollinger_stoch_combo,
    _detect_ma_rsi_macd_combo,
    _detect_rsi_bollinger_combo,
    _detect_macd_ma_combo,
    _detect_rsi_reversal,
    _detect_macd_crossover,
    _detect_bollinger_squeeze,
    _detect_ma_trend,
    _detect_momentum_divergence,
    _detect_stochastic_reversal,
    score_technical_setups,
)


# ── Helpers ───────────────────────────────────────────────────────

def _make_indicators(**overrides):
    """Build a base indicators dict with sensible defaults."""
    base = {
        "last_close": 100.0,
        "prev_close": 99.0,
        "rsi_14": 50.0,
        "rsi_21": 50.0,
        "rsi_prev": 48.0,
        "adx": 20.0,
        "macd": {
            "macd": 0.5, "signal": 0.3, "histogram": 0.2,
            "prev_macd": 0.2, "prev_signal": 0.3, "prev_histogram": 0.1,
        },
        "bollinger": {
            "upper": 105.0, "middle": 100.0, "lower": 95.0,
            "bandwidth": 5.0, "pct_b": 0.50,
        },
        "stochastic": {"k": 50.0, "d": 50.0},
        "sma_20": 99.0,
        "sma_50": 97.0,
        "sma_200": 95.0,
        "ema_20": 99.5,
        "atr": 2.0,
        "volume_ratio": 1.0,
        "_params": {},
    }
    base.update(overrides)
    return base


# ── Strategy Registration ─────────────────────────────────────────

SINGLE_STRATEGIES = [
    "rsi_reversal", "macd_crossover", "bollinger_squeeze",
    "ma_trend", "momentum_divergence", "stochastic_reversal",
]

COMBO_STRATEGIES = [
    "rsi_macd_combo", "bollinger_stoch_combo", "ma_rsi_macd_combo",
    "rsi_bollinger_combo", "macd_ma_combo",
]


class TestStrategyRegistration:
    """Ensure all strategies are properly registered."""

    def test_all_singles_in_strategies(self):
        for s in SINGLE_STRATEGIES:
            assert s in STRATEGIES, f"{s} missing from STRATEGIES dict"

    def test_all_combos_in_strategies(self):
        for s in COMBO_STRATEGIES:
            assert s in STRATEGIES, f"{s} missing from STRATEGIES dict"

    def test_combos_have_higher_weight(self):
        for s in COMBO_STRATEGIES:
            weight = STRATEGIES[s]["weight"]
            assert weight >= 1.0, f"{s} weight {weight} should be >= 1.0 for combos"

    def test_all_in_regime_preference(self):
        for s in SINGLE_STRATEGIES + COMBO_STRATEGIES:
            assert s in STRATEGY_REGIME_PREFERENCE, f"{s} missing from STRATEGY_REGIME_PREFERENCE"

    def test_total_strategy_count(self):
        assert len(STRATEGIES) == 11, f"Expected 11 strategies (6 single + 5 combo), got {len(STRATEGIES)}"

    def test_combo_names_contain_combo(self):
        for s in COMBO_STRATEGIES:
            assert "combo" in s, f"Combo strategy {s} should have 'combo' in name"

    def test_single_names_no_combo(self):
        for s in SINGLE_STRATEGIES:
            assert "combo" not in s, f"Single strategy {s} should not have 'combo' in name"


# ── RSI + MACD Combo ──────────────────────────────────────────────

class TestRsiMacdCombo:
    """Test _detect_rsi_macd_combo."""

    def test_bullish_rsi_oversold_macd_cross(self):
        """RSI < 35 + MACD bullish crossover → LONG."""
        indicators = _make_indicators(
            rsi_14=25.0,
            macd={
                "macd": 0.5, "signal": 0.3, "histogram": 0.2,
                "prev_macd": 0.2, "prev_signal": 0.3, "prev_histogram": -0.1,
            },
        )
        result = _detect_rsi_macd_combo(indicators)
        assert result is not None
        assert result["direction"] == "LONG"
        assert result["score"] >= 60
        assert result["strategy"] == "rsi_macd_combo"

    def test_bearish_rsi_overbought_macd_cross(self):
        """RSI > 65 + MACD bearish crossover → SHORT."""
        indicators = _make_indicators(
            rsi_14=80.0,
            macd={
                "macd": -0.5, "signal": -0.3, "histogram": -0.2,
                "prev_macd": -0.2, "prev_signal": -0.3, "prev_histogram": 0.1,
            },
        )
        result = _detect_rsi_macd_combo(indicators)
        assert result is not None
        assert result["direction"] == "SHORT"
        assert result["score"] >= 60

    def test_no_signal_rsi_normal(self):
        """RSI in normal range → no signal."""
        indicators = _make_indicators(rsi_14=50.0)
        assert _detect_rsi_macd_combo(indicators) is None

    def test_no_signal_rsi_extreme_macd_wrong(self):
        """RSI oversold but MACD not confirming → no signal."""
        indicators = _make_indicators(
            rsi_14=25.0,
            macd={
                "macd": -0.5, "signal": -0.3, "histogram": -0.2,
                "prev_macd": -0.2, "prev_signal": -0.3, "prev_histogram": -0.1,
            },
        )
        assert _detect_rsi_macd_combo(indicators) is None

    def test_missing_indicators(self):
        """Missing RSI or MACD → None."""
        assert _detect_rsi_macd_combo({"rsi_14": 25.0}) is None
        assert _detect_rsi_macd_combo({"macd": {"macd": 0.5, "signal": 0.3, "histogram": 0.2, "prev_macd": 0.2, "prev_signal": 0.3, "prev_histogram": 0.1}}) is None


# ── Bollinger + Stochastic Combo ──────────────────────────────────

class TestBollingerStochCombo:
    """Test _detect_bollinger_stoch_combo."""

    def test_bullish_lower_bb_stoch_oversold(self):
        """Price at lower BB + stochastic oversold → LONG."""
        indicators = _make_indicators(
            bollinger={"upper": 105, "middle": 100, "lower": 95, "bandwidth": 5.0, "pct_b": 0.05},
            stochastic={"k": 15.0, "d": 18.0},
        )
        result = _detect_bollinger_stoch_combo(indicators)
        assert result is not None
        assert result["direction"] == "LONG"
        assert result["strategy"] == "bollinger_stoch_combo"

    def test_bearish_upper_bb_stoch_overbought(self):
        """Price at upper BB + stochastic overbought → SHORT."""
        indicators = _make_indicators(
            bollinger={"upper": 105, "middle": 100, "lower": 95, "bandwidth": 5.0, "pct_b": 0.95},
            stochastic={"k": 85.0, "d": 82.0},
        )
        result = _detect_bollinger_stoch_combo(indicators)
        assert result is not None
        assert result["direction"] == "SHORT"

    def test_no_signal_mid_bb_stoch_neutral(self):
        """Price in middle BB + stoch neutral → no signal."""
        indicators = _make_indicators(
            bollinger={"upper": 105, "middle": 100, "lower": 95, "bandwidth": 5.0, "pct_b": 0.50},
            stochastic={"k": 50.0, "d": 50.0},
        )
        assert _detect_bollinger_stoch_combo(indicators) is None


# ── MA + RSI + MACD Triple Combo ──────────────────────────────────

class TestMaRsiMacdCombo:
    """Test _detect_ma_rsi_macd_combo — highest conviction."""

    def test_bullish_triple_confluence(self):
        """All three agree LONG → strong signal."""
        indicators = _make_indicators(
            last_close=102.0,
            sma_20=101.0,
            sma_50=99.0,
            ema_20=101.5,
            rsi_14=58.0,
            macd={
                "macd": 0.5, "signal": 0.3, "histogram": 0.2,
                "prev_macd": 0.2, "prev_signal": 0.3, "prev_histogram": 0.1,
            },
            adx=30.0,
        )
        result = _detect_ma_rsi_macd_combo(indicators)
        assert result is not None
        assert result["direction"] == "LONG"
        assert result["score"] >= 65
        assert result["strategy"] == "ma_rsi_macd_combo"

    def test_bearish_triple_confluence(self):
        """All three agree SHORT → strong signal."""
        indicators = _make_indicators(
            last_close=96.0,
            sma_20=98.0,
            sma_50=100.0,
            ema_20=97.0,
            rsi_14=42.0,
            macd={
                "macd": -0.5, "signal": -0.3, "histogram": -0.2,
                "prev_macd": -0.2, "prev_signal": -0.3, "prev_histogram": -0.1,
            },
            adx=30.0,
        )
        result = _detect_ma_rsi_macd_combo(indicators)
        assert result is not None
        assert result["direction"] == "SHORT"
        assert result["score"] >= 65

    def test_no_signal_partial_alignment(self):
        """MA bullish but RSI < 50 → no signal (partial confluence)."""
        indicators = _make_indicators(
            last_close=102.0,
            sma_20=101.0,
            sma_50=99.0,
            rsi_14=45.0,  # RSI disagrees
            macd={"macd": 0.5, "signal": 0.3, "histogram": 0.2,
                  "prev_macd": 0.2, "prev_signal": 0.3, "prev_histogram": 0.1},
        )
        assert _detect_ma_rsi_macd_combo(indicators) is None

    def test_triple_combo_higher_score_than_singles(self):
        """Triple combo should score higher than individual single strategies."""
        indicators = _make_indicators(
            last_close=102.0,
            sma_20=101.0,
            sma_50=99.0,
            ema_20=101.5,
            rsi_14=58.0,
            macd={
                "macd": 0.5, "signal": 0.3, "histogram": 0.2,
                "prev_macd": 0.2, "prev_signal": 0.3, "prev_histogram": 0.15,
            },
            adx=30.0,
        )
        combo = _detect_ma_rsi_macd_combo(indicators)
        ma_single = _detect_ma_trend(indicators)

        assert combo is not None
        if ma_single is not None:
            assert combo["score"] >= ma_single["score"], \
                "Triple combo should score >= single MA trend"


# ── RSI + Bollinger Combo ─────────────────────────────────────────

class TestRsiBollingerCombo:
    def test_bullish_rsi_oversold_lower_bb(self):
        indicators = _make_indicators(
            rsi_14=28.0,
            bollinger={"upper": 105, "middle": 100, "lower": 95, "bandwidth": 6.0, "pct_b": 0.05},
            adx=18.0,
        )
        result = _detect_rsi_bollinger_combo(indicators)
        assert result is not None
        assert result["direction"] == "LONG"
        assert result["strategy"] == "rsi_bollinger_combo"

    def test_no_signal_rsi_ok_bb_extreme(self):
        """RSI normal even if BB extreme → no combo."""
        indicators = _make_indicators(
            rsi_14=50.0,
            bollinger={"upper": 105, "middle": 100, "lower": 95, "bandwidth": 6.0, "pct_b": 0.02},
        )
        assert _detect_rsi_bollinger_combo(indicators) is None


# ── MACD + MA Combo ───────────────────────────────────────────────

class TestMacdMaCombo:
    def test_bullish_macd_cross_ma_aligned(self):
        indicators = _make_indicators(
            last_close=102.0,
            sma_20=101.0,
            sma_50=99.0,
            macd={
                "macd": 0.5, "signal": 0.3, "histogram": 0.2,
                "prev_macd": 0.2, "prev_signal": 0.3, "prev_histogram": 0.1,
            },
            adx=30.0,
        )
        result = _detect_macd_ma_combo(indicators)
        assert result is not None
        assert result["direction"] == "LONG"
        assert result["strategy"] == "macd_ma_combo"

    def test_no_signal_macd_cross_ma_reversed(self):
        """MACD bullish but MA bearish → no signal."""
        indicators = _make_indicators(
            last_close=96.0,
            sma_20=98.0,
            sma_50=100.0,
            macd={
                "macd": 0.5, "signal": 0.3, "histogram": 0.2,
                "prev_macd": 0.2, "prev_signal": 0.3, "prev_histogram": 0.1,
            },
        )
        assert _detect_macd_ma_combo(indicators) is None


# ── Version ───────────────────────────────────────────────────────

class TestVersion:
    def test_version_bumped(self):
        assert AgentScoring3.version == "2.3"


# ── Detector Map in score_technical_setups ────────────────────────

class TestDetectorMapRegistration:
    """Verify all combo detectors are wired into score_technical_setups."""

    def test_all_strategies_have_detectors(self):
        """Every strategy in STRATEGIES should have a matching detector function."""
        from backend.app.agents import agent_scoring_3 as module
        for strategy in STRATEGIES:
            fn_name = f"_detect_{strategy}"
            assert hasattr(module, fn_name), \
                f"Detector function {fn_name} not found for strategy {strategy}"

    def test_combo_count(self):
        combos = [s for s in STRATEGIES if "combo" in s]
        assert len(combos) == 5, f"Expected 5 combo strategies, got {len(combos)}"


# ── Trader 3 Integration ─────────────────────────────────────────

class TestTrader3Combos:
    """Verify Trader 3 handles combo strategies."""

    def test_trailing_thresholds_include_combos(self):
        from backend.app.agents.agent_trader_3 import AgentTrader3
        trader = AgentTrader3()
        for combo in COMBO_STRATEGIES:
            threshold = trader._get_trailing_threshold(combo)
            assert 0.0 < threshold < 1.0, \
                f"Trailing threshold for {combo} = {threshold}, should be in (0, 1)"

    def test_strategy_performance_returns_list(self):
        """get_strategy_performance should return a list."""
        from backend.app.agents.agent_trader_3 import AgentTrader3
        from unittest.mock import patch
        trader = AgentTrader3()
        with patch("backend.app.agents.agent_trader_3._load_positions",
                    return_value={"active": [], "closed": []}):
            result = trader.get_strategy_performance()
        assert isinstance(result, list)
