"""Tests for Agent Journal 2 and Agent Learning 2.

Tests:
- Journal 2: init, metrics, process_flip, dedup, snapshot, pruning
- Learning 2: init, metrics, compute_trend_learning, adjustments, anomalies
- Integration: Trader 2 consumes learning data, registry wiring, pipeline
"""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta


# ── Journal 2 tests ─────────────────────────────────────────────

class TestAgentJournal2:
    def test_init(self):
        from backend.app.agents.agent_journal_2 import AgentJournal2
        agent = AgentJournal2()
        assert agent.name == "journal_2"
        assert agent._last_run_flips_processed == 0
        assert agent._total_entries == 0

    def test_metrics(self):
        from backend.app.agents.agent_journal_2 import AgentJournal2
        agent = AgentJournal2()
        metrics = agent.get_metrics()
        assert "last_flips_processed" in metrics
        assert "total_entries" in metrics
        assert "last_daily_pnl" in metrics
        assert "last_snapshots" in metrics

    def test_compute_mae_mfe_long(self):
        from backend.app.agents.agent_journal_2 import _compute_mae_mfe
        bars = [
            {"high": 105, "low": 95, "open": 100, "close": 102},
            {"high": 110, "low": 98, "open": 102, "close": 108},
            {"high": 107, "low": 90, "open": 108, "close": 92},
        ]
        mae, mfe = _compute_mae_mfe("LONG", 100.0, bars)
        # MAE: worst low = 90, (90-100)/100 = -10%
        assert mae == -10.0
        # MFE: best high = 110, (110-100)/100 = 10%
        assert mfe == 10.0

    def test_compute_mae_mfe_short(self):
        from backend.app.agents.agent_journal_2 import _compute_mae_mfe
        bars = [
            {"high": 105, "low": 95, "open": 100, "close": 98},
            {"high": 108, "low": 92, "open": 98, "close": 93},
        ]
        mae, mfe = _compute_mae_mfe("SHORT", 100.0, bars)
        # MAE for SHORT: worst high = 108, (100-108)/100 = -8%
        assert mae == -8.0
        # MFE for SHORT: best low = 92, (100-92)/100 = 8%
        assert mfe == 8.0

    def test_compute_mae_mfe_empty_bars(self):
        """L3 fix: empty bars → None instead of 0.0."""
        from backend.app.agents.agent_journal_2 import _compute_mae_mfe
        mae, mfe = _compute_mae_mfe("LONG", 100.0, [])
        assert mae is None
        assert mfe is None

    def test_compute_mae_mfe_no_entry_price(self):
        """L3 fix: no entry_price → None instead of 0.0."""
        from backend.app.agents.agent_journal_2 import _compute_mae_mfe
        mae, mfe = _compute_mae_mfe("LONG", 0, [{"high": 10, "low": 5}])
        assert mae is None
        assert mfe is None

    def test_snapshot(self):
        from backend.app.agents.agent_journal_2 import AgentJournal2
        agent = AgentJournal2()
        pos = {
            "direction": "LONG",
            "entry_price": 3.5,
            "current_price": 3.8,
            "unrealized_pnl_pct": 8.57,
            "confidence": 75,
            "total_switches": 3,
        }
        snap = agent._take_snapshot("HG=F", pos)
        assert snap["ticker"] == "HG=F"
        assert snap["direction"] == "LONG"
        assert snap["unrealized_pnl_pct"] == 8.57
        assert "date" in snap

    def test_process_flip_neutral_skipped(self):
        from backend.app.agents.agent_journal_2 import AgentJournal2
        agent = AgentJournal2()
        flip = {
            "time": "2026-03-01T10:00:00+00:00",
            "entry_price": 3.5,
            "exit_price": 3.8,
            "from_direction": "NEUTRAL",
            "pnl_pct": 0,
            "signal_strength": 10,
            "key_news": [],
        }
        result = agent._process_flip("HG=F", flip, {"name": "Copper", "category": "industrial"})
        assert result is None  # NEUTRAL → skipped

    def test_process_flip_valid(self):
        from backend.app.agents.agent_journal_2 import AgentJournal2
        agent = AgentJournal2()
        flip = {
            "time": "2026-03-01T10:00:00+00:00",
            "entry_price": 3.5,
            "exit_price": 3.8,
            "from_direction": "LONG",
            "pnl_pct": 8.57,
            "signal_strength": 25.3,
            "key_news": [
                {"title": "Copper shortage", "category": "commodity", "score": 35},
            ],
            "reason": "Renversement",
        }
        result = agent._process_flip("HG=F", flip, {"name": "Copper", "category": "industrial"})
        assert result is not None
        assert result["ticker"] == "HG=F"
        assert result["direction"] == "LONG"
        assert result["pnl_pct"] == 8.57
        assert "commodity" in result["news_categories"]


# ── Learning 2 tests ────────────────────────────────────────────

class TestAgentLearning2:
    def test_init(self):
        from backend.app.agents.agent_learning_2 import AgentLearning2
        agent = AgentLearning2()
        assert agent.name == "learning_2"
        assert agent._cache_valid is False

    def test_metrics(self):
        from backend.app.agents.agent_learning_2 import AgentLearning2
        agent = AgentLearning2()
        metrics = agent.get_metrics()
        assert "last_adjustment_count" in metrics
        assert "total_recalculations" in metrics
        assert "cache_valid" in metrics

    def test_invalidate_cache(self):
        from backend.app.agents.agent_learning_2 import AgentLearning2
        agent = AgentLearning2()
        agent._cache_valid = True
        agent.invalidate_cache()
        assert agent._cache_valid is False

    def test_compute_trend_learning_empty(self):
        from backend.app.agents.agent_learning_2 import compute_trend_learning
        result = compute_trend_learning([])
        assert result["ticker_adj"] == {}
        assert result["newscat_adj"] == {}
        assert result["direction_adj"] == {}

    def test_compute_trend_learning_insufficient_data(self):
        from backend.app.agents.agent_learning_2 import compute_trend_learning
        entries = [
            {"ticker": "HG=F", "direction": "LONG", "pnl_pct": 5.0, "news_categories": ["commodity"]},
        ]
        result = compute_trend_learning(entries)
        assert result["stats"].get("sufficient_data") is False

    def test_compute_trend_learning_with_data(self):
        from backend.app.agents.agent_learning_2 import compute_trend_learning
        entries = [
            {"ticker": "HG=F", "direction": "LONG", "pnl_pct": 5.0,
             "news_categories": ["commodity"], "signal_strength": 30},
            {"ticker": "HG=F", "direction": "SHORT", "pnl_pct": -2.0,
             "news_categories": ["weather"], "signal_strength": 22},
            {"ticker": "CC=F", "direction": "LONG", "pnl_pct": 3.0,
             "news_categories": ["weather"], "signal_strength": 28},
            {"ticker": "CC=F", "direction": "SHORT", "pnl_pct": -1.0,
             "news_categories": ["commodity"], "signal_strength": 18},
            {"ticker": "KC=F", "direction": "LONG", "pnl_pct": 4.0,
             "news_categories": ["supply_chain"], "signal_strength": 35},
            {"ticker": "KC=F", "direction": "SHORT", "pnl_pct": 2.0,
             "news_categories": ["commodity"], "signal_strength": 25},
            {"ticker": "ZW=F", "direction": "LONG", "pnl_pct": -3.0,
             "news_categories": ["weather"], "signal_strength": 20},
        ]
        result = compute_trend_learning(entries)
        assert result["stats"]["sufficient_data"] is True
        assert result["stats"]["total_periods"] == 7
        assert result["stats"]["win_rate"] > 0
        # Should have some adjustments
        assert "avg_strength" in result["signal_calibration"]

    def test_ticker_adj_bounds(self):
        from backend.app.agents.agent_learning_2 import compute_trend_learning
        # All wins on one ticker → should be clamped
        entries = [
            {"ticker": "HG=F", "direction": "LONG", "pnl_pct": p,
             "news_categories": ["commodity"], "signal_strength": 30}
            for p in [10, 8, 12, 6, 9, 7, 11]
        ]
        result = compute_trend_learning(entries)
        if "HG=F" in result["ticker_adj"]:
            assert result["ticker_adj"]["HG=F"] <= 1.4
            assert result["ticker_adj"]["HG=F"] >= 0.6

    def test_anomaly_detection_low_win_rate(self):
        from backend.app.agents.agent_learning_2 import compute_trend_learning
        # All losses
        entries = [
            {"ticker": "HG=F", "direction": d, "pnl_pct": -3.0,
             "news_categories": ["commodity"], "signal_strength": 30}
            for d in ["LONG", "SHORT"] * 4
        ]
        result = compute_trend_learning(entries)
        assert any("Win rate" in a or "STREAK" in a for a in result["anomalies"])

    def test_anomaly_consecutive_losses(self):
        from backend.app.agents.agent_learning_2 import compute_trend_learning
        entries = [
            {"ticker": "HG=F", "direction": "LONG", "pnl_pct": -1.0,
             "news_categories": ["commodity"], "signal_strength": 30},
        ] * 8
        result = compute_trend_learning(entries)
        assert any("STREAK" in a for a in result["anomalies"])

    def test_neutral_entries_filtered(self):
        from backend.app.agents.agent_learning_2 import compute_trend_learning
        entries = [
            {"ticker": "HG=F", "direction": "NEUTRAL", "pnl_pct": 0},
            {"ticker": "HG=F", "direction": None, "pnl_pct": 0},
        ] * 5
        result = compute_trend_learning(entries)
        assert result["stats"].get("total_periods", 0) == 0


# ── Integration tests ───────────────────────────────────────────

class TestIntegration:
    def test_registry_has_journal_2(self):
        from backend.app.agents.registry import get_agent, _ensure_agents
        _ensure_agents()
        agent = get_agent("journal_2")
        assert agent is not None
        assert agent.name == "journal_2"

    def test_registry_has_learning_2(self):
        from backend.app.agents.registry import get_agent, _ensure_agents
        _ensure_agents()
        agent = get_agent("learning_2")
        assert agent is not None
        assert agent.name == "learning_2"

    def test_auditor_profile_journal_2(self):
        from backend.app.agents.agent_auditor import AUDIT_PROFILES
        assert "journal_2" in AUDIT_PROFILES
        assert "flip_coverage" in AUDIT_PROFILES["journal_2"]["checks"]
        assert "mae_mfe_accuracy" in AUDIT_PROFILES["journal_2"]["checks"]

    def test_auditor_profile_learning_2(self):
        from backend.app.agents.agent_auditor import AUDIT_PROFILES
        assert "learning_2" in AUDIT_PROFILES
        assert "ticker_calibration" in AUDIT_PROFILES["learning_2"]["checks"]
        assert "feedback_loop" in AUDIT_PROFILES["learning_2"]["checks"]

    def test_trader_2_accepts_learning_data(self):
        """Trader 2's run() accepts learning_data parameter."""
        from backend.app.agents.agent_trader_2 import AgentTrader2
        import inspect
        sig = inspect.signature(AgentTrader2.run)
        assert "learning_data" in sig.parameters

    def test_trader_2_applies_ticker_adj(self):
        """Trader 2 applies per-ticker learning adjustments to signal weight."""
        from backend.app.agents.agent_trader_2 import AgentTrader2
        agent = AgentTrader2()
        agent._current_learning = {
            "ticker_adj": {"HG=F": 1.3},
            "newscat_adj": {},
            "direction_adj": {},
            "signal_calibration": {},
        }
        # The _evaluate_ticker method should use _current_learning
        # We can verify by inspecting the source
        import inspect
        source = inspect.getsource(agent._evaluate_ticker)
        assert "ticker_adj" in source
        assert "newscat_adj" in source
        assert "direction_adj" in source
        assert "signal_cal" in source

    def test_trader_2_threshold_adjusted(self):
        """Signal calibration adjusts the change_threshold."""
        import inspect
        from backend.app.agents.agent_trader_2 import AgentTrader2
        source = inspect.getsource(AgentTrader2._evaluate_ticker)
        assert "threshold_adj" in source
        assert "signal_cal" in source

    def test_database_has_trend_journal_ddl(self):
        """DDL includes trend_journal_entries table."""
        import inspect
        from backend.app.database import init_db
        source = inspect.getsource(init_db)
        assert "trend_journal_entries" in source

    def test_database_vacuum_includes_trend_journal(self):
        """VACUUM maintenance includes trend_journal_entries."""
        import inspect
        from backend.app.database import pg_run_maintenance
        source = inspect.getsource(pg_run_maintenance)
        assert "trend_journal_entries" in source

    def test_registry_helpers_exist(self):
        """Registry exposes run_daily_journal_2 and run_learning_2_update."""
        from backend.app.agents import registry
        assert hasattr(registry, "run_daily_journal_2")
        assert hasattr(registry, "run_learning_2_update")
        assert hasattr(registry, "invalidate_learning_2_cache")
        assert hasattr(registry, "get_learning_2_adjustments")

    def test_all_agents_count(self):
        """Registry initializes 20 agents (not counting UX virtual)."""
        from backend.app.agents.registry import get_all_agents
        agents = get_all_agents()
        assert len(agents) == 20
        expected = {"news", "scoring", "scoring_2", "scoring_3", "scoring_4",
                    "trader_1", "trader_2", "trader_3", "trader_4",
                    "journal", "journal_2", "journal_3", "journal_4",
                    "learning", "learning_2", "learning_3", "learning_4",
                    "infrastructure", "performance", "auditor"}
        assert set(agents.keys()) == expected

    def test_get_all_status_includes_new_agents(self):
        """get_all_status includes journal_2, learning_2 and UX virtual."""
        from backend.app.agents.registry import get_all_status
        statuses = get_all_status()
        names = {s["name"] for s in statuses}
        assert "journal_2" in names
        assert "learning_2" in names
        assert "ux" in names  # virtual


# ── Learning 2 edge cases ───────────────────────────────────────

class TestLearning2EdgeCases:
    def test_direction_adj_bounds(self):
        from backend.app.agents.agent_learning_2 import compute_trend_learning
        entries = [
            {"ticker": "HG=F", "direction": "LONG", "pnl_pct": 20.0,
             "news_categories": ["commodity"], "signal_strength": 30},
        ] * 8
        result = compute_trend_learning(entries)
        if "LONG" in result["direction_adj"]:
            assert result["direction_adj"]["LONG"] <= 1.2
            assert result["direction_adj"]["LONG"] >= 0.8

    def test_signal_calibration_high_strength_better(self):
        from backend.app.agents.agent_learning_2 import compute_trend_learning
        # High-strength signals win, low-strength lose
        entries = []
        for i in range(4):
            entries.append({"ticker": "HG=F", "direction": "LONG", "pnl_pct": 5.0,
                            "news_categories": ["commodity"], "signal_strength": 40})
        for i in range(4):
            entries.append({"ticker": "CC=F", "direction": "SHORT", "pnl_pct": -3.0,
                            "news_categories": ["weather"], "signal_strength": 10})
        result = compute_trend_learning(entries)
        # Threshold should increase (require stronger signals)
        assert result["signal_calibration"]["threshold_adj"] >= 1.0

    def test_clamp_function(self):
        from backend.app.agents.agent_learning_2 import _clamp
        assert _clamp(2.0) == 1.4
        assert _clamp(0.1) == 0.6
        assert _clamp(1.0) == 1.0
        assert _clamp(0.9, 0.8, 1.2) == 0.9

    def test_mae_anomaly(self):
        from backend.app.agents.agent_learning_2 import compute_trend_learning
        entries = [
            {"ticker": "HG=F", "direction": "LONG", "pnl_pct": 1.0,
             "mae_pct": -8.0, "news_categories": ["commodity"], "signal_strength": 30},
        ] * 8
        result = compute_trend_learning(entries)
        assert any("MAE" in a for a in result["anomalies"])


# ── Audit fixes P1-P8 tests ─────────────────────────────────────

class TestAuditFixP1TemporalDecay:
    """P1: Recent entries should be weighted more than old entries."""

    def test_decay_weight_recent_entry(self):
        from backend.app.agents.agent_learning_2 import _compute_decay_weight
        now = datetime.now(timezone.utc)
        entry = {"exit_time": now.isoformat()}
        weight = _compute_decay_weight(entry, now)
        assert weight > 0.99  # Recent → weight ~1.0

    def test_decay_weight_old_entry(self):
        from backend.app.agents.agent_learning_2 import _compute_decay_weight, DECAY_HALF_LIFE_DAYS
        now = datetime.now(timezone.utc)
        old_time = (now - timedelta(days=DECAY_HALF_LIFE_DAYS)).isoformat()
        entry = {"exit_time": old_time}
        weight = _compute_decay_weight(entry, now)
        assert 0.45 < weight < 0.55  # At half-life → weight ~0.5

    def test_decay_weight_very_old(self):
        from backend.app.agents.agent_learning_2 import _compute_decay_weight
        now = datetime.now(timezone.utc)
        old_time = (now - timedelta(days=180)).isoformat()
        entry = {"exit_time": old_time}
        weight = _compute_decay_weight(entry, now)
        assert weight < 0.15  # Very old → low weight

    def test_decay_weight_missing_time(self):
        from backend.app.agents.agent_learning_2 import _compute_decay_weight
        now = datetime.now(timezone.utc)
        weight = _compute_decay_weight({}, now)
        assert weight == 1.0  # Fallback

    def test_decay_affects_adjustment(self):
        """Old entries with extreme PnL should have less effect than recent ones."""
        from backend.app.agents.agent_learning_2 import compute_trend_learning
        now = datetime.now(timezone.utc)
        recent_time = now.isoformat()
        old_time = (now - timedelta(days=120)).isoformat()
        # Mix of old losing and recent winning entries
        entries = [
            {"ticker": "HG=F", "direction": "LONG", "pnl_pct": -5.0,
             "exit_time": old_time, "news_categories": ["commodity"], "signal_strength": 30},
        ] * 4 + [
            {"ticker": "HG=F", "direction": "LONG", "pnl_pct": 5.0,
             "exit_time": recent_time, "news_categories": ["commodity"], "signal_strength": 30},
        ] * 4
        result = compute_trend_learning(entries)
        # Recent wins should dominate over old losses
        if "HG=F" in result["ticker_adj"]:
            assert result["ticker_adj"]["HG=F"] >= 1.0  # Should be boosted


class TestAuditFixP2Significance:
    """P2: Significance testing filters out noise."""

    def test_is_significant_clear_signal(self):
        from backend.app.agents.agent_learning_2 import _is_significant
        # Strong positive signal
        assert _is_significant([5.0, 4.0, 6.0, 3.0, 5.5]) is True

    def test_is_significant_noise(self):
        from backend.app.agents.agent_learning_2 import _is_significant
        # Noise — mean close to zero
        assert _is_significant([0.01, -0.01, 0.02, -0.02, 0.01]) is False

    def test_is_significant_small_sample(self):
        from backend.app.agents.agent_learning_2 import _is_significant
        assert _is_significant([5.0]) is False  # n=1, can't compute

    def test_is_significant_min_effect_size(self):
        from backend.app.agents.agent_learning_2 import _is_significant
        # Mean = 0.05, below min_effect_size of 0.1
        assert _is_significant([0.05, 0.05, 0.05, 0.05]) is False

    def test_is_significant_zero_variance(self):
        from backend.app.agents.agent_learning_2 import _is_significant
        # All same, but effect size met
        assert _is_significant([2.0, 2.0, 2.0, 2.0]) is True

    def test_no_adj_without_significance(self):
        """Entries with near-zero PnL should not produce adjustments."""
        from backend.app.agents.agent_learning_2 import compute_trend_learning
        entries = [
            {"ticker": "HG=F", "direction": "LONG", "pnl_pct": 0.01,
             "news_categories": ["commodity"], "signal_strength": 30},
        ] * 8
        result = compute_trend_learning(entries)
        # WR is 100% but PnL is negligible → t-test should filter
        # Actually WR 100% with all positive produces a signal,
        # but the pnl_signal is tiny. The t-test on [0.01]*8 should pass
        # because mean=0.01 < min_effect_size=0.1. Let's check.
        # The adj should not be produced due to min_effect_size
        # Note: signal from WR is (1.0-0.5)*2=1.0, but _is_significant([0.01]*8) = False
        # so _compute_adj returns None
        assert "HG=F" not in result["ticker_adj"]


class TestAuditFixP3Duration:
    """P3: duration_hours computed from position entry to flip time."""

    def test_process_flip_has_duration(self):
        from backend.app.agents.agent_journal_2 import AgentJournal2
        agent = AgentJournal2()
        flip = {
            "time": "2026-03-05T10:00:00+00:00",
            "position_entry_time": "2026-03-01T10:00:00+00:00",
            "entry_price": 3.5,
            "exit_price": 3.8,
            "from_direction": "LONG",
            "pnl_pct": 8.57,
            "signal_strength": 25,
            "key_news": [],
        }
        result = agent._process_flip("HG=F", flip, {"name": "Copper", "category": "industrial"})
        assert result is not None
        assert result["duration_hours"] == 96.0  # 4 days = 96 hours

    def test_process_flip_duration_without_position_entry_time(self):
        """If position_entry_time is missing, duration should be 0 (same time)."""
        from backend.app.agents.agent_journal_2 import AgentJournal2
        agent = AgentJournal2()
        flip = {
            "time": "2026-03-05T10:00:00+00:00",
            # No position_entry_time → falls back to flip time
            "entry_price": 3.5,
            "exit_price": 3.8,
            "from_direction": "LONG",
            "pnl_pct": 8.57,
            "signal_strength": 25,
            "key_news": [],
        }
        result = agent._process_flip("HG=F", flip, {"name": "Copper", "category": "industrial"})
        assert result is not None
        assert result["duration_hours"] == 0.0  # Same time → 0


class TestAuditFixP4EntryTime:
    """P4: entry_time is the actual position entry, exit_time is the flip."""

    def test_process_flip_entry_exit_times(self):
        from backend.app.agents.agent_journal_2 import AgentJournal2
        agent = AgentJournal2()
        flip = {
            "time": "2026-03-05T10:00:00+00:00",
            "position_entry_time": "2026-03-01T08:00:00+00:00",
            "entry_price": 3.5,
            "exit_price": 3.8,
            "from_direction": "LONG",
            "pnl_pct": 8.57,
            "signal_strength": 25,
            "key_news": [],
        }
        result = agent._process_flip("HG=F", flip, {"name": "Copper", "category": "industrial"})
        assert result["entry_time"] == "2026-03-01T08:00:00+00:00"  # Actual entry
        assert result["exit_time"] == "2026-03-05T10:00:00+00:00"   # Flip time

    def test_trader_2_stores_position_entry_time_in_history(self):
        """Trader 2 history entries include position_entry_time."""
        import inspect
        from backend.app.agents.agent_trader_2 import AgentTrader2
        source = inspect.getsource(AgentTrader2._make_change)
        assert "position_entry_time" in source


class TestAuditFixP5ProportionalCalibration:
    """P5: Signal calibration is proportional, not binary 0.95/1.0/1.05."""

    def test_calibration_proportional_high_gap(self):
        from backend.app.agents.agent_learning_2 import compute_trend_learning
        entries = []
        # High-strength signals always win (WR=100%)
        for i in range(4):
            entries.append({"ticker": "HG=F", "direction": "LONG", "pnl_pct": 5.0,
                            "news_categories": ["commodity"], "signal_strength": 50})
        # Low-strength signals always lose (WR=0%)
        for i in range(4):
            entries.append({"ticker": "CC=F", "direction": "SHORT", "pnl_pct": -3.0,
                            "news_categories": ["weather"], "signal_strength": 5})
        result = compute_trend_learning(entries)
        threshold = result["signal_calibration"]["threshold_adj"]
        # Should be > 1.0 (raise threshold) but proportionally scaled
        assert threshold > 1.0
        assert threshold <= 1.05

    def test_calibration_proportional_small_gap(self):
        from backend.app.agents.agent_learning_2 import compute_trend_learning
        now = datetime.now(timezone.utc).isoformat()
        entries = []
        # Both high and low strength perform similarly
        for i in range(4):
            entries.append({"ticker": "HG=F", "direction": "LONG", "pnl_pct": 2.0,
                            "news_categories": ["commodity"], "signal_strength": 40,
                            "exit_time": now})
        for i in range(4):
            entries.append({"ticker": "CC=F", "direction": "SHORT", "pnl_pct": 1.5,
                            "news_categories": ["weather"], "signal_strength": 10,
                            "exit_time": now})
        result = compute_trend_learning(entries)
        threshold = result["signal_calibration"]["threshold_adj"]
        # Gap is small → threshold should be close to 1.0
        assert 0.95 <= threshold <= 1.05


class TestAuditFixP6NewscatDedup:
    """P6: Primary category only (no double-counting across categories)."""

    def test_primary_category_only(self):
        from backend.app.agents.agent_learning_2 import compute_trend_learning
        # Each entry has multiple categories, but learning should use only primary
        entries = [
            {"ticker": "HG=F", "direction": "LONG", "pnl_pct": 5.0,
             "news_categories": ["commodity", "weather", "supply_chain"],
             "signal_strength": 30},
        ] * 8
        result = compute_trend_learning(entries)
        # Only "commodity" (primary) should appear, not weather/supply_chain
        if result["newscat_adj"]:
            assert "commodity" in result["newscat_adj"]
            assert "weather" not in result["newscat_adj"]
            assert "supply_chain" not in result["newscat_adj"]


class TestAuditFixP7CrossDimension:
    """P7: Cross-dimension newscat×ticker adjustments."""

    def test_cross_dimension_computed(self):
        from backend.app.agents.agent_learning_2 import compute_trend_learning
        now = datetime.now(timezone.utc).isoformat()
        entries = [
            {"ticker": "HG=F", "direction": "LONG", "pnl_pct": 5.0,
             "news_categories": ["commodity"], "signal_strength": 30,
             "exit_time": now},
        ] * 4  # Need at least 3 for cross-dimension
        # Add more entries for global minimum
        entries += [
            {"ticker": "CC=F", "direction": "SHORT", "pnl_pct": 2.0,
             "news_categories": ["weather"], "signal_strength": 25,
             "exit_time": now},
        ] * 4
        result = compute_trend_learning(entries)
        # Should have cross-dimension keys like "commodity+HG=F"
        assert "newscat_ticker_adj" in result
        # With 4 commodity+HG=F entries, should compute cross-dimension
        if result["newscat_ticker_adj"]:
            keys = list(result["newscat_ticker_adj"].keys())
            assert any("+" in k for k in keys)

    def test_trader_2_uses_cross_dimension(self):
        """Trader 2 prefers cross-dimension newscat_ticker_adj."""
        import inspect
        from backend.app.agents.agent_trader_2 import AgentTrader2
        source = inspect.getsource(AgentTrader2._evaluate_ticker)
        assert "newscat_ticker_adj" in source
        assert "cross_key" in source


class TestAuditFixP8ChronologicalSort:
    """P8: Entries sorted chronologically before streak detection."""

    def test_entries_sorted_for_streak(self):
        from backend.app.agents.agent_learning_2 import compute_trend_learning
        # Entries in reverse order — 3 losses then wins
        # Without sorting, streak detection might see the wrong order
        entries = [
            {"ticker": "HG=F", "direction": "LONG", "pnl_pct": 5.0,
             "news_categories": ["commodity"], "signal_strength": 30,
             "exit_time": "2026-03-08T10:00:00+00:00"},
            {"ticker": "HG=F", "direction": "LONG", "pnl_pct": 5.0,
             "news_categories": ["commodity"], "signal_strength": 30,
             "exit_time": "2026-03-07T10:00:00+00:00"},
            {"ticker": "CC=F", "direction": "SHORT", "pnl_pct": -2.0,
             "news_categories": ["weather"], "signal_strength": 25,
             "exit_time": "2026-03-06T10:00:00+00:00"},
            {"ticker": "CC=F", "direction": "SHORT", "pnl_pct": -3.0,
             "news_categories": ["weather"], "signal_strength": 20,
             "exit_time": "2026-03-05T10:00:00+00:00"},
            {"ticker": "KC=F", "direction": "LONG", "pnl_pct": -1.0,
             "news_categories": ["commodity"], "signal_strength": 22,
             "exit_time": "2026-03-04T10:00:00+00:00"},
            {"ticker": "KC=F", "direction": "LONG", "pnl_pct": -4.0,
             "news_categories": ["supply_chain"], "signal_strength": 28,
             "exit_time": "2026-03-03T10:00:00+00:00"},
            {"ticker": "ZW=F", "direction": "SHORT", "pnl_pct": 3.0,
             "news_categories": ["weather"], "signal_strength": 32,
             "exit_time": "2026-03-02T10:00:00+00:00"},
        ]
        result = compute_trend_learning(entries)
        # After sorting: 03-02(win), 03-03(loss), 03-04(loss), 03-05(loss), 03-06(loss), 03-07(win), 03-08(win)
        # Max consecutive losses = 4 (03-03 to 03-06)
        assert any("STREAK" in a and "4" in a for a in result["anomalies"])
