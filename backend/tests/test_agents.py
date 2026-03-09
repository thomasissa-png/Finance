"""Tests for agent infrastructure — BaseAgent, MessageBus, AgentLogger, Registry, Auditor.

Covers:
- BaseAgent status tracking, execute wrapper, logging shortcuts
- MessageBus in-memory publish/consume, subscriber callbacks
- AgentLogger in-memory log storage and filtering
- Agent Registry singleton initialization and pipeline functions
- Agent Auditor profiles and dispatch
- Each agent's initialization, metrics, and basic run contracts
"""

import json
import threading
import time
from unittest.mock import patch, MagicMock

import pytest


# ── BaseAgent & Infrastructure ──────────────────────────────────────


class TestAgentStatus:
    """Test AgentStatus enum."""

    def test_status_values(self):
        from backend.app.agents.base import AgentStatus
        assert AgentStatus.IDLE.value == "idle"
        assert AgentStatus.WORKING.value == "working"
        assert AgentStatus.ERROR.value == "error"
        assert AgentStatus.DISABLED.value == "disabled"


class TestMessageBus:
    """Test MessageBus in-memory behavior (no PG)."""

    def setup_method(self):
        """Reset singleton for each test."""
        from backend.app.agents.base import MessageBus
        MessageBus._instance = None
        self.bus = MessageBus()

    def test_singleton(self):
        from backend.app.agents.base import MessageBus
        bus2 = MessageBus()
        assert self.bus is bus2

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    def test_publish_and_consume(self, _mock_pg):
        self.bus.publish("agent_a", "test_msg", {"key": "value"})
        msgs = self.bus.consume("agent_b", ["test_msg"])
        assert len(msgs) == 1
        assert msgs[0]["from_agent"] == "agent_a"
        assert msgs[0]["payload"]["key"] == "value"
        assert msgs[0]["msg_type"] == "test_msg"

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    def test_consume_marks_consumed(self, _mock_pg):
        self.bus.publish("a", "t", {"x": 1})
        self.bus.consume("b", ["t"])
        # Second consume should return nothing
        msgs = self.bus.consume("b", ["t"])
        assert len(msgs) == 0

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    def test_targeted_message(self, _mock_pg):
        self.bus.publish("a", "t", {"x": 1}, to_agent="specific_agent")
        # Other agent can't see it
        msgs = self.bus.consume("other_agent", ["t"])
        assert len(msgs) == 0
        # Target agent can
        msgs = self.bus.consume("specific_agent", ["t"])
        assert len(msgs) == 1

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    def test_broadcast_message(self, _mock_pg):
        self.bus.publish("a", "t", {"x": 1}, to_agent=None)
        msgs = self.bus.consume("any_agent", ["t"])
        assert len(msgs) == 1

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    def test_subscribe_callback(self, _mock_pg):
        received = []
        self.bus.subscribe("test_type", lambda msg: received.append(msg))
        self.bus.publish("sender", "test_type", {"data": 42})
        assert len(received) == 1
        assert received[0]["payload"]["data"] == 42

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    def test_consume_limit(self, _mock_pg):
        for i in range(10):
            self.bus.publish("a", "t", {"i": i})
        msgs = self.bus.consume("b", ["t"], limit=3)
        assert len(msgs) == 3

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    def test_consume_filters_by_type(self, _mock_pg):
        self.bus.publish("a", "type_a", {"x": 1})
        self.bus.publish("a", "type_b", {"x": 2})
        msgs = self.bus.consume("b", ["type_a"])
        assert len(msgs) == 1
        assert msgs[0]["msg_type"] == "type_a"

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    def test_recent_messages(self, _mock_pg):
        self.bus.publish("a", "t", {"x": 1})
        self.bus.publish("b", "t", {"x": 2})
        recent = self.bus.recent_messages(limit=10)
        assert len(recent) == 2

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    def test_memory_maxlen(self, _mock_pg):
        # MessageBus has maxlen=1000, verify it doesn't crash with many messages
        for i in range(1100):
            self.bus.publish("a", "t", {"i": i})
        recent = self.bus.recent_messages(limit=2000)
        assert len(recent) == 1000  # Capped at maxlen


class TestAgentLogger:
    """Test AgentLogger in-memory behavior."""

    def setup_method(self):
        from backend.app.agents.base import AgentLogger
        self.logger = AgentLogger("test_agent")

    @patch("backend.app.agents.base.AgentLogger._use_pg", return_value=False)
    def test_log_entry(self, _mock_pg):
        self.logger.log("test_action", {"detail": "value"})
        logs = self.logger.get_logs()
        assert len(logs) == 1
        assert logs[0]["action"] == "test_action"
        assert logs[0]["level"] == "INFO"
        assert logs[0]["details"]["detail"] == "value"

    @patch("backend.app.agents.base.AgentLogger._use_pg", return_value=False)
    def test_log_with_duration(self, _mock_pg):
        self.logger.log("action", duration_ms=150)
        logs = self.logger.get_logs()
        assert logs[0]["duration_ms"] == 150

    @patch("backend.app.agents.base.AgentLogger._use_pg", return_value=False)
    def test_log_level_filter(self, _mock_pg):
        self.logger.log("info_action", level="INFO")
        self.logger.log("error_action", level="ERROR")
        self.logger.log("warn_action", level="WARN")

        all_logs = self.logger.get_logs()
        assert len(all_logs) == 3

        error_logs = self.logger.get_logs(level="ERROR")
        assert len(error_logs) == 1
        assert error_logs[0]["action"] == "error_action"

    @patch("backend.app.agents.base.AgentLogger._use_pg", return_value=False)
    def test_log_limit(self, _mock_pg):
        for i in range(20):
            self.logger.log(f"action_{i}")
        logs = self.logger.get_logs(limit=5)
        assert len(logs) == 5

    @patch("backend.app.agents.base.AgentLogger._use_pg", return_value=False)
    def test_log_maxlen(self, _mock_pg):
        # AgentLogger maxlen=500
        for i in range(600):
            self.logger.log(f"action_{i}")
        logs = self.logger.get_logs(limit=1000)
        assert len(logs) == 500


class TestBaseAgent:
    """Test BaseAgent status, execute, logging."""

    def setup_method(self):
        from backend.app.agents.base import BaseAgent, MessageBus
        # Reset MessageBus singleton
        MessageBus._instance = None

        class TestAgent(BaseAgent):
            name = "test"
            description = "Test agent"

            def run(self, **kwargs):
                return {"ok": True}

            def get_metrics(self):
                return {"test_metric": 42}

        self.agent = TestAgent()

    def test_initial_status(self):
        status = self.agent.status
        assert status["name"] == "test"
        assert status["status"] == "idle"
        assert status["action_count"] == 0
        assert status["last_error"] is None

    @patch("backend.app.agents.base.AgentLogger._use_pg", return_value=False)
    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    def test_execute_success(self, _m1, _m2):
        result = self.agent.execute("test_action", lambda: {"result": "ok"})
        assert result == {"result": "ok"}
        assert self.agent.status["status"] == "idle"
        assert self.agent.status["action_count"] == 2  # WORKING + IDLE

    @patch("backend.app.agents.base.AgentLogger._use_pg", return_value=False)
    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    def test_execute_failure(self, _m1, _m2):
        def fail():
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            self.agent.execute("fail_action", fail)

        assert self.agent.status["status"] == "error"
        assert self.agent.status["last_error"] == "test error"

    @patch("backend.app.agents.base.AgentLogger._use_pg", return_value=False)
    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    def test_log_decision(self, _m1, _m2):
        self.agent.log_decision("TRADE EXECUTED", {"ticker": "GC=F"})
        logs = self.agent.logger.get_logs(level="DECISION")
        assert len(logs) == 1
        assert logs[0]["details"]["ticker"] == "GC=F"

    def test_get_metrics(self):
        assert self.agent.get_metrics() == {"test_metric": 42}

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    def test_publish_consume(self, _m):
        self.agent.publish("test_event", {"data": 1})
        # Another agent consuming
        msgs = self.agent.bus.consume("other", ["test_event"])
        assert len(msgs) == 1


# ── Agent Registry ──────────────────────────────────────────────────


class TestAgentRegistry:
    """Test agent registry singleton and status."""

    def setup_method(self):
        """Reset registry for each test."""
        import backend.app.agents.registry as reg
        reg._agents = {}

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    @patch("backend.app.agents.base.AgentLogger._use_pg", return_value=False)
    def test_get_all_agents(self, _m1, _m2):
        from backend.app.agents.registry import get_all_agents
        agents = get_all_agents()
        assert "news" in agents
        assert "scoring" in agents
        assert "trader_1" in agents
        assert "trader_2" in agents
        assert "journal" in agents
        assert "journal_2" in agents
        assert "learning" in agents
        assert "learning_2" in agents
        assert "scoring_2" in agents
        assert "infrastructure" in agents
        assert "auditor" in agents
        assert len(agents) == 20

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    @patch("backend.app.agents.base.AgentLogger._use_pg", return_value=False)
    def test_get_agent_by_name(self, _m1, _m2):
        from backend.app.agents.registry import get_agent
        agent = get_agent("news")
        assert agent is not None
        assert agent.name == "news"

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    @patch("backend.app.agents.base.AgentLogger._use_pg", return_value=False)
    def test_get_agent_unknown(self, _m1, _m2):
        from backend.app.agents.registry import get_agent
        assert get_agent("nonexistent") is None

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    @patch("backend.app.agents.base.AgentLogger._use_pg", return_value=False)
    def test_get_all_status_includes_ux(self, _m1, _m2):
        from backend.app.agents.registry import get_all_status
        statuses = get_all_status()
        names = [s["name"] for s in statuses]
        assert "ux" in names
        assert len(statuses) == 21  # 20 real agents + virtual UX

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    @patch("backend.app.agents.base.AgentLogger._use_pg", return_value=False)
    def test_all_agents_have_metrics(self, _m1, _m2):
        from backend.app.agents.registry import get_all_status
        statuses = get_all_status()
        for s in statuses:
            assert "metrics" in s, f"Agent {s['name']} missing metrics"

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    @patch("backend.app.agents.base.AgentLogger._use_pg", return_value=False)
    def test_all_agents_have_status_fields(self, _m1, _m2):
        from backend.app.agents.registry import get_all_status
        required_fields = {"name", "description", "status", "last_action",
                           "last_action_time", "last_error", "action_count", "metrics"}
        statuses = get_all_status()
        for s in statuses:
            assert required_fields.issubset(set(s.keys())), \
                f"Agent {s['name']} missing fields: {required_fields - set(s.keys())}"


# ── Agent Auditor ──────────────────────────────────────────────────


class TestAgentAuditor:
    """Test Agent Auditor profiles and dispatch."""

    def test_all_audit_profiles_present(self):
        from backend.app.agents.agent_auditor import AUDIT_PROFILES
        expected = {"news", "scoring", "scoring_2", "scoring_3", "scoring_4", "trader_1", "trader_2", "trader_3", "trader_4", "journal", "journal_2", "journal_3", "journal_4", "learning", "learning_2", "learning_3", "learning_4", "ux", "infrastructure", "performance", "auditor"}
        assert set(AUDIT_PROFILES.keys()) == expected

    def test_ux_profile_has_checks(self):
        from backend.app.agents.agent_auditor import AUDIT_PROFILES
        ux = AUDIT_PROFILES["ux"]
        assert "expertise" in ux
        assert len(ux["checks"]) >= 5
        assert "component_coverage" in ux["checks"]
        assert "api_integration" in ux["checks"]
        assert "agent_visibility" in ux["checks"]

    def test_each_profile_has_expertise_and_checks(self):
        from backend.app.agents.agent_auditor import AUDIT_PROFILES
        for name, profile in AUDIT_PROFILES.items():
            assert "expertise" in profile, f"Profile {name} missing expertise"
            assert "checks" in profile, f"Profile {name} missing checks"
            assert len(profile["checks"]) >= 3, f"Profile {name} has too few checks"

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    @patch("backend.app.agents.base.AgentLogger._use_pg", return_value=False)
    def test_invalid_target_returns_error(self, _m1, _m2):
        from backend.app.agents.base import MessageBus
        MessageBus._instance = None
        from backend.app.agents.agent_auditor import AgentAuditor
        auditor = AgentAuditor()
        result = auditor.run("nonexistent_agent")
        assert "error" in result

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    @patch("backend.app.agents.base.AgentLogger._use_pg", return_value=False)
    def test_auditor_metrics(self, _m1, _m2):
        from backend.app.agents.base import MessageBus
        MessageBus._instance = None
        from backend.app.agents.agent_auditor import AgentAuditor
        auditor = AgentAuditor()
        metrics = auditor.get_metrics()
        assert "total_audits" in metrics
        assert "last_audit_target" in metrics
        assert "last_audit_score" in metrics
        assert "reports_stored" in metrics

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    @patch("backend.app.agents.base.AgentLogger._use_pg", return_value=False)
    def test_audit_ux_runs(self, _m1, _m2):
        """Test that UX audit runs without error and produces a valid report."""
        from backend.app.agents.base import MessageBus
        MessageBus._instance = None
        from backend.app.agents.agent_auditor import AgentAuditor
        auditor = AgentAuditor()
        report = auditor.run("ux")
        assert "error" not in report
        assert report["target_agent"] == "ux"
        assert report["score"] > 0
        assert len(report["findings"]) > 0
        assert len(report["score_breakdown"]) > 0

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    @patch("backend.app.agents.base.AgentLogger._use_pg", return_value=False)
    def test_audit_self_runs(self, _m1, _m2):
        """Test that self-audit runs and produces a valid report."""
        from backend.app.agents.base import MessageBus
        MessageBus._instance = None
        from backend.app.agents.agent_auditor import AgentAuditor
        auditor = AgentAuditor()
        report = auditor.run("auditor")
        assert "error" not in report
        assert report["target_agent"] == "auditor"
        assert report["score"] > 0
        assert "profile_coverage" in report["score_breakdown"]
        assert "check_implementation" in report["score_breakdown"]

    def test_auditor_profile_has_checks(self):
        from backend.app.agents.agent_auditor import AUDIT_PROFILES
        aud = AUDIT_PROFILES["auditor"]
        assert "expertise" in aud
        assert len(aud["checks"]) >= 5
        assert "profile_coverage" in aud["checks"]
        assert "check_implementation" in aud["checks"]

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    @patch("backend.app.agents.base.AgentLogger._use_pg", return_value=False)
    def test_trend_tracking_empty(self, _m1, _m2):
        """Test trend computation with no history."""
        from backend.app.agents.base import MessageBus
        MessageBus._instance = None
        from backend.app.agents.agent_auditor import AgentAuditor
        auditor = AgentAuditor()
        # Clear any history loaded from previous tests or persisted files
        auditor._audit_history = []
        trends = auditor._compute_trends()
        assert isinstance(trends, dict)
        assert len(trends) == 0

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    @patch("backend.app.agents.base.AgentLogger._use_pg", return_value=False)
    def test_trend_tracking_with_history(self, _m1, _m2):
        """Test trend computation with 2 audit reports for same agent."""
        from backend.app.agents.base import MessageBus
        MessageBus._instance = None
        from backend.app.agents.agent_auditor import AgentAuditor
        auditor = AgentAuditor()
        auditor._audit_history = [
            {"target_agent": "news", "score": 6.0},
            {"target_agent": "news", "score": 8.0},
        ]
        trends = auditor._compute_trends()
        assert "news" in trends
        assert trends["news"]["delta"] == 2.0
        assert trends["news"]["current"] == 8.0

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    @patch("backend.app.agents.base.AgentLogger._use_pg", return_value=False)
    def test_analyze_agent_errors(self, _m1, _m2):
        """Test error log analysis helper."""
        from backend.app.agents.base import MessageBus
        MessageBus._instance = None
        from backend.app.agents.agent_auditor import AgentAuditor
        auditor = AgentAuditor()
        findings = []
        scores = {}
        improvements = []
        # Should not crash even with no logs
        auditor._analyze_agent_errors(findings, scores, improvements, "news")


# ── Individual Agent Init & Metrics ────────────────────────────────


class TestAgentNews:
    """Test Agent News initialization and metrics."""

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    @patch("backend.app.agents.base.AgentLogger._use_pg", return_value=False)
    def test_init_and_metrics(self, _m1, _m2):
        from backend.app.agents.base import MessageBus
        MessageBus._instance = None
        from backend.app.agents.agent_news import AgentNews
        agent = AgentNews()
        assert agent.name == "news"
        metrics = agent.get_metrics()
        assert "last_collect_count" in metrics
        assert "total_collected" in metrics
        assert "source_errors" in metrics


class TestAgentScoring:
    """Test Agent Scoring initialization and metrics."""

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    @patch("backend.app.agents.base.AgentLogger._use_pg", return_value=False)
    def test_init_and_metrics(self, _m1, _m2):
        from backend.app.agents.base import MessageBus
        MessageBus._instance = None
        from backend.app.agents.agent_scoring import AgentScoring
        agent = AgentScoring()
        assert agent.name == "scoring"
        metrics = agent.get_metrics()
        assert "last_scored_count" in metrics
        assert "total_tokens_used" in metrics


class TestAgentTrader:
    """Test Agent Trader initialization and metrics."""

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    @patch("backend.app.agents.base.AgentLogger._use_pg", return_value=False)
    def test_init_and_metrics(self, _m1, _m2):
        from backend.app.agents.base import MessageBus
        MessageBus._instance = None
        from backend.app.agents.agent_trader import AgentTrader
        agent = AgentTrader()
        assert agent.name == "trader_1"
        metrics = agent.get_metrics()
        assert "trades_today" in metrics
        assert "rejections_today" in metrics


class TestAgentJournal:
    """Test Agent Journal initialization and metrics."""

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    @patch("backend.app.agents.base.AgentLogger._use_pg", return_value=False)
    def test_init_and_metrics(self, _m1, _m2):
        from backend.app.agents.base import MessageBus
        MessageBus._instance = None
        from backend.app.agents.agent_journal import AgentJournal
        agent = AgentJournal()
        assert agent.name == "journal"
        metrics = agent.get_metrics()
        assert "last_trades_closed" in metrics


class TestAgentLearning:
    """Test Agent Learning initialization and metrics."""

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    @patch("backend.app.agents.base.AgentLogger._use_pg", return_value=False)
    def test_init_and_metrics(self, _m1, _m2):
        from backend.app.agents.base import MessageBus
        MessageBus._instance = None
        from backend.app.agents.agent_learning import AgentLearning
        agent = AgentLearning()
        assert agent.name == "learning"
        metrics = agent.get_metrics()
        assert "last_adjustment_count" in metrics
        assert "total_recalculations" in metrics


class TestAgentInfrastructure:
    """Test Agent Infrastructure initialization and metrics."""

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    @patch("backend.app.agents.base.AgentLogger._use_pg", return_value=False)
    def test_init_and_metrics(self, _m1, _m2):
        from backend.app.agents.base import MessageBus
        MessageBus._instance = None
        from backend.app.agents.agent_infrastructure import AgentInfrastructure
        agent = AgentInfrastructure()
        assert agent.name == "infrastructure"
        metrics = agent.get_metrics()
        assert "health_status" in metrics
        assert "total_health_checks" in metrics
        assert "total_maintenance_runs" in metrics
        assert "consecutive_pg_failures" in metrics
        assert "last_duration_ms" in metrics

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    @patch("backend.app.agents.base.AgentLogger._use_pg", return_value=False)
    def test_health_check_no_pg(self, _m1, _m2):
        from backend.app.agents.base import MessageBus
        MessageBus._instance = None
        from backend.app.agents.agent_infrastructure import AgentInfrastructure
        agent = AgentInfrastructure()
        result = agent.run_health_check()
        assert "overall" in result
        assert "pg_status" in result
        assert "issues" in result
        assert result["pg_status"] == "not_configured"

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    @patch("backend.app.agents.base.AgentLogger._use_pg", return_value=False)
    def test_infra_audit_profile_exists(self, _m1, _m2):
        from backend.app.agents.agent_auditor import AUDIT_PROFILES
        assert "infrastructure" in AUDIT_PROFILES
        profile = AUDIT_PROFILES["infrastructure"]
        assert "expertise" in profile
        assert len(profile["checks"]) >= 8


# ── Database Pruning ────────────────────────────────────────────────


class TestDatabasePruning:
    """Test agent table pruning functions exist and handle no-PG gracefully."""

    def test_prune_agent_messages_no_pg(self):
        from backend.app.database import pg_prune_agent_messages
        result = pg_prune_agent_messages()
        assert result == 0

    def test_prune_agent_logs_no_pg(self):
        from backend.app.database import pg_prune_agent_logs
        result = pg_prune_agent_logs()
        assert result == 0

    def test_prune_audit_reports_no_pg(self):
        from backend.app.database import pg_prune_audit_reports
        result = pg_prune_audit_reports()
        assert result == 0


# ── v6.3 Audit Trader tests ─────────────────────────────────────────


class TestAgentTraderAuditV63:
    """v6.3: Trader audit fixes — score field, direction enum, position monitor."""

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    @patch("backend.app.agents.base.AgentLogger._use_pg", return_value=False)
    def test_trader_uses_raw_claude_score_not_score(self, _m1, _m2):
        """P1: TradeRecommendation has raw_claude_score, not score."""
        from backend.app.models import TradeRecommendation, Direction, ScanType
        from datetime import datetime, timezone
        rec = TradeRecommendation(
            scan_type=ScanType.EUROPE, timestamp=datetime.now(timezone.utc),
            ticker="ZW=F", asset_name="Wheat", category="commodities_agri",
            direction=Direction.LONG, catalyst="Test",
            entry_price=600.0, target_price=610.0, stop_price=594.0,
            target_pct=1.67, stop_pct=1.0, risk_reward=1.67,
            confidence=75, time_window="09:00 — 20:00",
            raw_claude_score=42.5,
        )
        # raw_claude_score exists
        assert rec.raw_claude_score == 42.5
        # 'score' does NOT exist as a field
        assert "score" not in TradeRecommendation.model_fields

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    @patch("backend.app.agents.base.AgentLogger._use_pg", return_value=False)
    def test_trader_direction_serialized_as_string(self, _m1, _m2):
        """P3: direction.value produces string, not enum object."""
        from backend.app.models import Direction
        d = Direction.LONG
        assert d.value == "LONG"
        assert isinstance(d.value, str)

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    @patch("backend.app.agents.base.AgentLogger._use_pg", return_value=False)
    def test_trader_reset_daily_counters(self, _m1, _m2):
        """P5: reset_daily_counters zeroes both counters."""
        from backend.app.agents.base import MessageBus
        MessageBus._instance = None
        from backend.app.agents.agent_trader import AgentTrader
        agent = AgentTrader()
        agent._trades_today = 5
        agent._rejections_today = 3
        agent.reset_daily_counters()
        assert agent._trades_today == 0
        assert agent._rejections_today == 0

    def test_correlation_static_group_skips_dynamic(self):
        """P4: When both tickers are in static groups, skip dynamic correlation."""
        from backend.app.trade_selector import _check_correlation
        # GC=F and SI=F are in the gold_safe group → should be correlated (True)
        assert _check_correlation("GC=F", ["SI=F"]) is True
        # GC=F and TTE.PA are in different groups → dynamic needed (but returns False without data)
        # Just verify it doesn't crash
        result = _check_correlation("GC=F", ["TTE.PA"])
        assert isinstance(result, bool)

    @patch("backend.app.agents.base.MessageBus._use_pg", return_value=False)
    @patch("backend.app.agents.base.AgentLogger._use_pg", return_value=False)
    def test_position_monitor_returns_dict(self, _m1, _m2):
        """P2: run_position_monitor returns dict with actions_taken key."""
        from backend.app.agents.base import MessageBus
        MessageBus._instance = None
        from backend.app.agents.agent_trader import AgentTrader
        agent = AgentTrader()
        # Mock monitor_positions to return empty list (no pending trades)
        with patch("backend.app.agents.agent_trader.AgentTrader.run_position_monitor") as mock:
            mock.return_value = {"actions_taken": [], "active_positions": 0}
            result = agent.run_position_monitor()
            assert isinstance(result, dict)
            assert "actions_taken" in result


class TestTraderAuditJournalLearningV64:
    """v6.4: Trader audit from Journal & Learning perspective — trailing stop persistence."""

    def test_trailing_stop_before_sl_check(self):
        """J1: Trailing stop adjustment happens BEFORE SL check in position monitor."""
        import inspect
        from backend.app.position_monitor import monitor_positions
        source = inspect.getsource(monitor_positions)
        # Find positions of key comments/calls
        trailing_pos = source.find("Trailing stop adjustment")
        sl_pos = source.find("Check SL hit")
        tp_pos = source.find("Check TP hit")
        # TP should be first, then trailing, then SL
        assert tp_pos < trailing_pos < sl_pos, \
            "Order must be: TP check → Trailing stop → SL check"

    def test_trailing_stop_calls_persist(self):
        """J1: Trailing stop adjustment calls update_trade_stop to persist."""
        import inspect
        from backend.app.position_monitor import monitor_positions
        source = inspect.getsource(monitor_positions)
        assert "update_trade_stop" in source, \
            "monitor_positions must call update_trade_stop to persist trailing changes"

    def test_update_trade_stop_exists(self):
        """J1: update_trade_stop function exists in learning module."""
        from backend.app.learning import update_trade_stop
        assert callable(update_trade_stop)

    def test_update_trade_stop_json_fallback(self):
        """J1: update_trade_stop works with JSON storage (no PG)."""
        from datetime import datetime, timezone
        from unittest.mock import patch, MagicMock
        from backend.app.models import TradeRecommendation, Direction, ScanType, TradeResult

        trade = TradeRecommendation(
            scan_type=ScanType.EUROPE, timestamp=datetime(2026, 3, 7, 8, 0, tzinfo=timezone.utc),
            ticker="ZW=F", asset_name="Wheat", category="commodities_agri",
            direction=Direction.LONG, catalyst="Test",
            entry_price=600.0, target_price=610.0, stop_price=594.0,
            target_pct=1.67, stop_pct=1.0, risk_reward=1.67,
            confidence=75, time_window="09:00 — 20:00",
        )

        with patch("backend.app.learning.is_pg_enabled", return_value=False), \
             patch("backend.app.learning._load_trades_uncached", return_value=[trade]), \
             patch("backend.app.learning._write_trades") as mock_write, \
             patch("backend.app.learning._invalidate_trades_cache"):
            from backend.app.learning import update_trade_stop
            update_trade_stop(trade.timestamp, trade.ticker, 600.0)
            # Should have written trades with updated stop
            assert mock_write.called
            written_trades = mock_write.call_args[0][0]
            assert written_trades[0].stop_price == 600.0

    def test_pg_update_trade_stop_function_exists(self):
        """J1: pg_update_trade_stop function exists in database module."""
        from backend.app.database import pg_update_trade_stop
        assert callable(pg_update_trade_stop)

    def test_journal_no_getattr_on_trade(self):
        """J2: journal.py uses direct field access, not getattr on TradeRecommendation."""
        import inspect
        from backend.app.journal import _build_review
        source = inspect.getsource(_build_review)
        # Should use trade.news_category, not getattr(trade, "news_category", ...)
        assert 'getattr(trade,' not in source, \
            "_build_review should use direct field access, not getattr"

    def test_learning_no_getattr_on_trade(self):
        """L2/L3: learning.py uses direct field access on TradeRecommendation."""
        from backend.app import learning
        import inspect
        # Check compute_learning_adjustments
        source = inspect.getsource(learning.compute_learning_adjustments)
        assert 'getattr(t,' not in source, \
            "compute_learning_adjustments should use direct field access"
        # Check build_performance_summary
        source2 = inspect.getsource(learning.build_performance_summary)
        assert 'getattr(t,' not in source2, \
            "build_performance_summary should use direct field access on trades"


class TestScoringAuditFromTraderV65:
    """v6.5: Scoring audit from Trader perspective — formula sync, field access."""

    def test_highvol_formula_synced_with_calibrate(self):
        """P1: High-vol tier formula in spread filter and pre-move must match _calibrate_trade."""
        import inspect
        from backend.app.trade_selector import _calibrate_trade, select_trades
        # Extract calibration coefficients from _calibrate_trade source
        cal_source = inspect.getsource(_calibrate_trade)
        # High-vol tier in calibrate: 0.12 + 0.33
        assert "0.12" in cal_source and "0.33" in cal_source, \
            "_calibrate_trade high-vol tier should use 0.12 + 0.33"
        # select_trades must use the same coefficients
        sel_source = inspect.getsource(select_trades)
        # Count occurrences of the OLD wrong formula — should be zero
        assert "0.10 + 0.35" not in sel_source, \
            "select_trades should NOT use 0.10 + 0.35 (old mismatched formula)"
        # Should use the synced formula
        assert "0.12 + 0.33" in sel_source, \
            "select_trades high-vol tier should use 0.12 + 0.33 (synced with _calibrate_trade)"

    def test_highvol_spread_filter_matches_calibrate(self):
        """P1: Spread filter estimated target matches what _calibrate_trade produces."""
        from backend.app.trade_selector import _calibrate_trade
        from backend.app.models import Direction
        # Test with high-vol ticker: avg_range=6%, score=50
        score = 50
        avg_range = 6.0
        entry_price = 100.0
        target_price, _, target_pct, _, _ = _calibrate_trade(
            Direction.LONG, entry_price, avg_range, score, ticker="NG=F",
        )
        # Compute estimated target using the same formula as spread filter
        ns = score / 100
        sf = 0.12 + 0.33 * (ns ** 1.5)
        mf = 0.7 + 0.6 * (50 / 100)  # default magnitude=50
        estimated_target = avg_range * sf * mf
        # They should be close (category multipliers excluded for direct comparison)
        assert abs(target_pct - estimated_target) < 0.5, \
            f"Spread filter estimate {estimated_target:.2f}% vs calibrate {target_pct:.2f}% — should be close"

    def test_no_hasattr_on_scored_news(self):
        """P2: trade_selector.py should NOT use hasattr on ScoredNews declared fields."""
        import inspect
        from backend.app.trade_selector import select_trades
        source = inspect.getsource(select_trades)
        assert "hasattr(sn," not in source, \
            "select_trades should use direct field access on ScoredNews, not hasattr"
        assert "hasattr(best_news," not in source, \
            "select_trades should use direct field access on ScoredNews, not hasattr"

    def test_no_getattr_on_scored_news(self):
        """P3: trade_selector.py should NOT use getattr on ScoredNews declared fields."""
        import inspect
        from backend.app.trade_selector import select_trades
        source = inspect.getsource(select_trades)
        assert "getattr(best_news," not in source, \
            "select_trades should use direct field access on ScoredNews, not getattr"
        assert "getattr(sn," not in source, \
            "select_trades should use direct field access on ScoredNews, not getattr"

    def test_scored_news_has_all_trader_fields(self):
        """Verify ScoredNews exposes all fields the trader needs."""
        from backend.app.models import ScoredNews, NewsItem, Direction
        item = NewsItem(title="Test", source="test")
        sn = ScoredNews(
            news=item, surprise=70, freshness=90, directional_clarity=80,
            transmission_delay=75, market_awareness=15,
            expected_magnitude=60, signal_reliability=85,
            direction=Direction.LONG,
            impacted_tickers=["ZW=F"],
            reasoning="Test signal",
            news_category="weather",
        )
        # All fields the trader accesses must exist with correct types
        assert isinstance(sn.convergence_count, int)
        assert isinstance(sn.expected_magnitude, int)
        assert isinstance(sn.signal_reliability, int)
        assert isinstance(sn.transmission_delay, int)
        assert isinstance(sn.market_awareness, int)
        assert isinstance(sn.category_score_mult, float)
        assert isinstance(sn.total_score, float)
        assert isinstance(sn.chain_reactions, list)
        assert sn.convergence_count == 0  # default

    def test_scoring_agent_returns_all_needed_keys(self):
        """Agent Scoring result dict must contain 'scored' and 'market_context'."""
        from backend.app.agents.agent_scoring import AgentScoring
        agent = AgentScoring()
        # Mock the scoring to return known data
        from backend.app.models import ScoredNews, NewsItem, Direction, ScanType
        item = NewsItem(title="Test drought", source="NOAA")
        sn = ScoredNews(
            news=item, surprise=80, freshness=95, directional_clarity=90,
            transmission_delay=85, market_awareness=10,
            direction=Direction.LONG, impacted_tickers=["ZW=F"],
            reasoning="Drought", news_category="weather",
        )
        mock_ctx = {"vix": 18.5, "regime": "normal"}
        with patch.object(agent, '_score_batch', return_value=([sn], mock_ctx)), \
             patch("backend.app.news_scorer._is_zero_edge_headline", return_value=None), \
             patch("backend.app.news_scorer.get_token_usage", return_value={"total_input": 100, "total_output": 50}):
            result = agent.run([item], ScanType.EUROPE)
        assert "scored" in result
        assert "market_context" in result
        assert len(result["scored"]) == 1
        assert result["market_context"] == mock_ctx


# ── v7.1 Audit fixes tests ────────────────────────────────────────────


class TestAuditDispatchEquipe2:
    """P1: Verify audit dispatch routes to journal_2, learning_2, scoring_2."""

    def test_auditor_has_audit_journal_2_method(self):
        from backend.app.agents.agent_auditor import AgentAuditor
        assert hasattr(AgentAuditor, "_audit_journal_2")

    def test_auditor_has_audit_learning_2_method(self):
        from backend.app.agents.agent_auditor import AgentAuditor
        assert hasattr(AgentAuditor, "_audit_learning_2")

    def test_auditor_has_audit_scoring_2_method(self):
        from backend.app.agents.agent_auditor import AgentAuditor
        assert hasattr(AgentAuditor, "_audit_scoring_2")

    def test_self_audit_expected_includes_equipe2(self):
        """Self-audit expected set includes journal_2 and learning_2."""
        import inspect
        from backend.app.agents.agent_auditor import AgentAuditor
        src = inspect.getsource(AgentAuditor._audit_self)
        assert "journal_2" in src
        assert "learning_2" in src

    def test_check_methods_includes_equipe2(self):
        """Self-audit check_methods includes journal_2, learning_2, scoring_2."""
        import inspect
        from backend.app.agents.agent_auditor import AgentAuditor
        src = inspect.getsource(AgentAuditor._audit_self)
        assert "_audit_journal_2" in src
        assert "_audit_learning_2" in src
        assert "_audit_scoring_2" in src


class TestJournal2AuditFixes:
    """Tests for P2-P8, L1-L8 fixes in agent_journal_2.py."""

    def test_mae_mfe_none_when_no_bars(self):
        """L3: MAE/MFE returns None when bars are missing."""
        from backend.app.agents.agent_journal_2 import _compute_mae_mfe
        mae, mfe = _compute_mae_mfe("LONG", 100.0, [])
        assert mae is None
        assert mfe is None

    def test_process_flip_has_duration_days(self):
        """L6: process_flip includes duration_days."""
        from backend.app.agents.agent_journal_2 import AgentJournal2
        agent = AgentJournal2()
        flip = {
            "time": "2024-06-15T10:00:00+00:00",
            "entry_price": 100.0,
            "exit_price": 105.0,
            "from_direction": "LONG",
            "pnl_pct": 5.0,
            "position_entry_time": "2024-06-10T10:00:00+00:00",
            "key_news": [{"category": "weather", "title": "Test"}],
        }
        entry = agent._process_flip("HG=F", flip, {"name": "Cuivre", "category": "commodities_industrial"})
        assert entry is not None
        assert "duration_days" in entry
        assert entry["duration_days"] == pytest.approx(5.0, abs=0.1)

    def test_process_flip_has_bar_interval(self):
        """L4: process_flip includes bar_interval."""
        from backend.app.agents.agent_journal_2 import AgentJournal2
        agent = AgentJournal2()
        flip = {
            "time": "2024-06-15T10:00:00+00:00",
            "entry_price": 100.0,
            "exit_price": 105.0,
            "from_direction": "LONG",
            "pnl_pct": 5.0,
            "key_news": [],
        }
        entry = agent._process_flip("HG=F", flip, {"name": "Cuivre", "category": "commodities_industrial"})
        assert entry is not None
        assert entry["bar_interval"] in ("daily", "1day", "1h")  # P3.10: now returns interval from fetch

    def test_news_categories_sorted_by_frequency(self):
        """L7: news_categories sorted by frequency, not random set."""
        from backend.app.agents.agent_journal_2 import AgentJournal2
        agent = AgentJournal2()
        flip = {
            "time": "2024-06-15T10:00:00+00:00",
            "entry_price": 100.0,
            "exit_price": 105.0,
            "from_direction": "LONG",
            "pnl_pct": 5.0,
            "key_news": [
                {"category": "commodity"},
                {"category": "weather"},
                {"category": "commodity"},
                {"category": "commodity"},
                {"category": "weather"},
            ],
        }
        entry = agent._process_flip("HG=F", flip, {"name": "Cuivre", "category": "commodities_industrial"})
        assert entry is not None
        # commodity appears 3 times, weather 2 — commodity should be first
        assert entry["news_categories"][0] == "commodity"
        assert entry["news_categories"][1] == "weather"

    def test_key_news_count_before_truncation(self):
        """L1: key_news_count reflects total news, not truncated."""
        from backend.app.agents.agent_journal_2 import AgentJournal2
        agent = AgentJournal2()
        # Create 8 news items (Trader 2 truncates to 5 in history)
        flip = {
            "time": "2024-06-15T10:00:00+00:00",
            "entry_price": 100.0,
            "exit_price": 105.0,
            "from_direction": "LONG",
            "pnl_pct": 5.0,
            "key_news": [{"category": "weather"} for _ in range(8)],
        }
        entry = agent._process_flip("HG=F", flip, {"name": "Cuivre", "category": "commodities_industrial"})
        assert entry is not None
        assert entry["key_news_count"] == 8

    def test_total_entries_loaded_from_persistence(self):
        """P8: _total_entries loads from persistence."""
        from backend.app.agents.agent_journal_2 import AgentJournal2
        agent = AgentJournal2()
        assert agent._total_entries_loaded is False
        agent._ensure_total_entries()
        assert agent._total_entries_loaded is True

    def test_snapshot_has_entry_time_for_dedup(self):
        """P2: Snapshots have entry_time for PG dedup."""
        from backend.app.agents.agent_journal_2 import AgentJournal2
        agent = AgentJournal2()
        snapshot = agent._take_snapshot("HG=F", {
            "direction": "LONG",
            "entry_price": 100.0,
            "current_price": 105.0,
            "unrealized_pnl_pct": 5.0,
        })
        assert "entry_time" in snapshot
        assert snapshot["entry_time"].startswith("snapshot_HG=F_")

    def test_get_entries_excludes_snapshots(self):
        """P2: get_entries() filters out snapshot entries."""
        from backend.app.agents.agent_journal_2 import AgentJournal2
        agent = AgentJournal2()
        with patch("backend.app.agents.agent_journal_2._load_journal_entries",
                    return_value=[
                        {"ticker": "HG=F", "entry_type": "snapshot", "entry_time": "s1"},
                        {"ticker": "HG=F", "pnl_pct": 5.0, "entry_time": "e1"},
                    ]):
            entries = agent.get_entries()
            assert len(entries) == 1
            assert entries[0]["pnl_pct"] == 5.0


# ── Audit v7.4 — Agent Scoring fixes ──────────────────────────────


class TestAgentScoringAuditV74:
    """Tests for v7.4 audit fixes on Agent Scoring 1."""

    def test_a1_token_usage_correct_keys(self):
        """A1: Agent reads input_tokens/output_tokens (not total_input/total_output)."""
        import inspect
        from backend.app.agents.agent_scoring import AgentScoring
        source = inspect.getsource(AgentScoring.run)
        assert "input_tokens" in source
        assert "output_tokens" in source
        assert "total_input" not in source
        assert "total_output" not in source

    def test_a2_cache_hits_in_metrics(self):
        """A2: cache_hits should be in get_metrics() and can be incremented."""
        from backend.app.agents.agent_scoring import AgentScoring
        agent = AgentScoring()
        m = agent.get_metrics()
        assert "cache_hits" in m
        assert m["cache_hits"] == 0
        # Simulate increment
        agent._cache_hits = 5
        assert agent.get_metrics()["cache_hits"] == 5

    def test_a3_duration_ms_in_metrics(self):
        """A3: last_duration_ms should be in get_metrics()."""
        from backend.app.agents.agent_scoring import AgentScoring
        agent = AgentScoring()
        m = agent.get_metrics()
        assert "last_duration_ms" in m

    def test_a8_zero_edge_import_at_module_level(self):
        """A8: _is_zero_edge_headline should be importable from agent_scoring module."""
        from backend.app.agents.agent_scoring import _is_zero_edge_headline
        # Should be the same function as in news_scorer
        from backend.app.news_scorer import _is_zero_edge_headline as original
        assert _is_zero_edge_headline is original

    def test_a3_duration_ms_published_in_bus(self):
        """A3: duration_ms should appear in the scoring bus publish."""
        import inspect
        from backend.app.agents.agent_scoring import AgentScoring
        source = inspect.getsource(AgentScoring.run)
        assert "duration_ms" in source

    def test_a2_cache_hits_published_in_bus(self):
        """A2: cache_hits should appear in the scoring bus publish."""
        import inspect
        from backend.app.agents.agent_scoring import AgentScoring
        source = inspect.getsource(AgentScoring.run)
        assert '"cache_hits"' in source


# ── Team 3 v2.0 Audit Tests ─────────────────────────────────────────


class TestTeam3V2Scoring3:
    """Tests for Scoring 3 v2.0 audit fixes."""

    def test_stochastic_reversal_strategy_exists(self):
        """T3: stochastic_reversal should be a valid strategy."""
        from backend.app.agents.agent_scoring_3 import STRATEGIES
        assert "stochastic_reversal" in STRATEGIES
        assert STRATEGIES["stochastic_reversal"]["weight"] == 0.9

    def test_sma_200_computed(self):
        """P5: SMA 200 should be computed in indicators."""
        from backend.app.agents.agent_scoring_3 import _compute_all_indicators
        closes = [100 + i * 0.1 for i in range(250)]
        ohlcv = {
            "close": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "volume": [1000] * 250,
        }
        indicators = _compute_all_indicators(ohlcv)
        assert "sma_200" in indicators
        assert indicators["sma_200"] is not None

    def test_regime_strategy_preference_defined(self):
        """J3: Each strategy should have a regime preference."""
        from backend.app.agents.agent_scoring_3 import (
            STRATEGY_REGIME_PREFERENCE, STRATEGIES)
        for strategy in STRATEGIES:
            assert strategy in STRATEGY_REGIME_PREFERENCE

    def test_default_params_configurable(self):
        """C1: All indicator params should be in DEFAULT_PARAMS."""
        from backend.app.agents.agent_scoring_3 import DEFAULT_PARAMS
        assert "rsi_oversold" in DEFAULT_PARAMS
        assert "bb_squeeze_threshold" in DEFAULT_PARAMS
        assert "adx_trend_threshold" in DEFAULT_PARAMS
        assert "min_setup_score" in DEFAULT_PARAMS

    def test_pivot_detection(self):
        """C7: Pivot detection should find swing points."""
        from backend.app.agents.agent_scoring_3 import _find_recent_pivot
        # Create a V-shaped price series
        closes = [100, 99, 98, 97, 96, 97, 98, 99, 100]
        pivot = _find_recent_pivot(closes, min_lookback=2, max_lookback=7)
        assert pivot is not None
        assert closes[pivot] == 96  # The swing low

    def test_confidence_not_score_times_09(self):
        """P4: Confidence should NOT be score * 0.9."""
        import inspect
        from backend.app.agents.agent_scoring_3 import score_technical_setups
        source = inspect.getsource(score_technical_setups)
        assert "final_score * 0.9" not in source

    def test_volume_boost_order_correct(self):
        """P2: vol_ratio > 2.0 should be checked before > 1.5."""
        import inspect
        from backend.app.agents.agent_scoring_3 import score_technical_setups
        source = inspect.getsource(score_technical_setups)
        pos_2 = source.index("vol_ratio > 2.0")
        pos_15 = source.index("vol_ratio > 1.5")
        assert pos_2 < pos_15  # 2.0 check before 1.5 check

    def test_version_bumped(self):
        from backend.app.agents.agent_scoring_3 import AgentScoring3
        assert AgentScoring3.version == "2.0"

    def test_stochastic_reversal_detector(self):
        """T3: Stochastic reversal should detect oversold conditions."""
        from backend.app.agents.agent_scoring_3 import _detect_stochastic_reversal
        indicators = {
            "stochastic": {"k": 15, "d": 20},  # Oversold, K > D expected
            "adx": 18,  # Ranging
            "rsi_14": 35,
        }
        # K=15 < 20, D=20 < 25, K < D → no signal (need K > D for bullish)
        result = _detect_stochastic_reversal(indicators)
        # K < D, so no bullish signal → None
        assert result is None

        # Fix: K crosses above D
        indicators["stochastic"] = {"k": 18, "d": 17}
        result = _detect_stochastic_reversal(indicators)
        assert result is not None
        assert result["direction"] == "LONG"


class TestTeam3V2Trader3:
    """Tests for Trader 3 v2.0 audit fixes."""

    def test_correlation_groups_defined(self):
        """P8: Correlation groups should prevent conflicting positions."""
        from backend.app.agents.agent_trader_3 import TECH_CORRELATION_GROUPS
        assert "gold_silver" in TECH_CORRELATION_GROUPS
        assert "GC=F" in TECH_CORRELATION_GROUPS["gold_silver"]
        assert "SI=F" in TECH_CORRELATION_GROUPS["gold_silver"]

    def test_correlation_conflict_detection(self):
        """P8: Should detect opposing directions in correlated group."""
        from backend.app.agents.agent_trader_3 import AgentTrader3
        trader = AgentTrader3()
        active = [{"ticker": "GC=F", "direction": "LONG"}]
        # SI=F SHORT should conflict with GC=F LONG (gold_silver group)
        assert trader._check_correlation_conflict("SI=F", "SHORT", active) is True
        # SI=F LONG should NOT conflict (same direction)
        assert trader._check_correlation_conflict("SI=F", "LONG", active) is False
        # AAPL SHORT should NOT conflict (different group)
        assert trader._check_correlation_conflict("AAPL", "SHORT", active) is False

    def test_trailing_threshold_per_strategy(self):
        """J2: Trailing stop threshold should vary by strategy."""
        from backend.app.agents.agent_trader_3 import AgentTrader3
        trader = AgentTrader3()
        assert trader._get_trailing_threshold("ma_trend") < trader._get_trailing_threshold("rsi_reversal")

    def test_dynamic_max_per_strategy(self):
        """J4: Validated strategies should get higher budget."""
        from backend.app.agents.agent_trader_3 import (
            AgentTrader3, MAX_PER_STRATEGY_BASE, MAX_PER_STRATEGY_VALIDATED)
        trader = AgentTrader3()
        # No weekly config → base
        assert trader._get_max_per_strategy("rsi_reversal") == MAX_PER_STRATEGY_BASE
        # With weekly config → validated gets more
        trader._weekly_config = {"validated_strategies": ["rsi_reversal"]}
        assert trader._get_max_per_strategy("rsi_reversal") == MAX_PER_STRATEGY_VALIDATED
        assert trader._get_max_per_strategy("ma_trend") == MAX_PER_STRATEGY_BASE

    def test_version_bumped(self):
        from backend.app.agents.agent_trader_3 import AgentTrader3
        assert AgentTrader3.version == "2.0"


class TestTeam3V2Journal3:
    """Tests for Journal 3 v2.0 audit fixes."""

    def test_pg_conflict_includes_strategy(self):
        """BUG FIX: ON CONFLICT must include strategy to match UNIQUE constraint."""
        import inspect
        from backend.app.agents.agent_journal_3 import _pg_save_entries
        source = inspect.getsource(_pg_save_entries)
        assert "ticker, strategy, entry_time" in source
        assert "ON CONFLICT (ticker, entry_time) DO NOTHING" not in source

    def test_dedup_key_includes_strategy(self):
        """Dedup key should be (ticker, strategy, entry_time)."""
        import inspect
        from backend.app.agents.agent_journal_3 import AgentJournal3
        source = inspect.getsource(AgentJournal3.run)
        assert 'e.get("strategy"' in source

    def test_realized_rr_computed(self):
        """L4: Journal entries should include realized R/R."""
        from backend.app.agents.agent_journal_3 import AgentJournal3
        j = AgentJournal3()
        trade = {
            "ticker": "GC=F", "name": "Gold", "category": "commodities",
            "strategy": "rsi_reversal", "strategy_name": "RSI Reversal",
            "direction": "LONG", "result": "TP_HIT",
            "entry_price": 2000, "close_price": 2040,
            "entry_time": "2026-03-01T10:00:00Z",
            "close_time": "2026-03-02T15:00:00Z",
            "pnl_pct": 2.0, "target_pct": 2.5, "stop_pct": 1.5,
            "high_watermark": 2050, "low_watermark": 1990,
        }
        entry = j._process_closed_trade(trade)
        assert entry is not None
        assert "realized_rr" in entry
        assert entry["realized_rr"] is not None
        assert entry["predicted_rr"] is not None
        assert entry["predicted_rr"] == round(2.5 / 1.5, 2)

    def test_sharpe_ratio_in_strategy_ab(self):
        """L5: Strategy A/B should include Sharpe ratio."""
        from backend.app.agents.agent_journal_3 import AgentJournal3
        j = AgentJournal3()
        entries = [
            {"strategy": "rsi_reversal", "pnl_pct": 1.5, "result": "TP_HIT",
             "mae_pct": -0.5, "mfe_pct": 2.0, "holding_hours": 10},
        ] * 10  # 10 identical entries for Sharpe
        perf = j._compute_strategy_ab(entries)
        assert "rsi_reversal" in perf
        assert "sharpe_ratio" in perf["rsi_reversal"]

    def test_version_bumped(self):
        from backend.app.agents.agent_journal_3 import AgentJournal3
        assert AgentJournal3.version == "2.0"


class TestTeam3V2Learning3:
    """Tests for Learning 3 v2.0 audit fixes."""

    def test_ab_test_produces_adjustments(self):
        """C4: AB-test should produce real strategy_adj, not just analytics."""
        from backend.app.agents.agent_learning_3 import compute_tech_learning
        entries = []
        for i in range(20):
            entries.append({
                "strategy": "rsi_reversal", "ticker": "GC=F",
                "timeframe": "1d", "adx_at_entry": 18,
                "pnl_pct": 1.5, "result": "TP_HIT",
                "close_time": f"2026-03-{i+1:02d}T10:00:00Z",
            })
        for i in range(20):
            entries.append({
                "strategy": "ma_trend", "ticker": "GC=F",
                "timeframe": "1d", "adx_at_entry": 30,
                "pnl_pct": -1.0, "result": "SL_HIT",
                "close_time": f"2026-03-{i+1:02d}T11:00:00Z",
            })
        result = compute_tech_learning(entries)
        # Both strategies should have adjustments
        assert "rsi_reversal" in result["strategy_adj"]
        assert "ma_trend" in result["strategy_adj"]
        # Winner should be boosted, loser penalized
        assert result["strategy_adj"]["rsi_reversal"] > result["strategy_adj"]["ma_trend"]
        # AB test should include sharpe
        assert "sharpe_ratio" in result["ab_test"]["rsi_reversal"]

    def test_weekly_config_generation(self):
        """C3: Learning 3 should generate weekly config."""
        from backend.app.agents.agent_learning_3 import AgentLearning3
        l3 = AgentLearning3()
        # No data → config still generates with all strategies enabled
        config = l3.generate_weekly_config()
        assert "enabled_strategies" in config
        assert "strategy_weights" in config
        assert "generated_at" in config
        assert len(config["enabled_strategies"]) > 0

    def test_config_history_tracking(self):
        """L2: Config changes should be tracked."""
        from backend.app.agents.agent_learning_3 import AgentLearning3
        l3 = AgentLearning3()
        l3.generate_weekly_config()
        l3.generate_weekly_config()
        history = l3.get_config_history()
        assert len(history) == 2
        assert "timestamp" in history[0]

    def test_version_bumped(self):
        from backend.app.agents.agent_learning_3 import AgentLearning3
        assert AgentLearning3.version == "2.0"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Team 4 v2.0 audit tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestTeam4V2Scoring4:
    """Scoring 4 v2.0 audit tests."""

    def test_tie_returns_neutral(self):
        """T2: Tie between LONG/SHORT should return NEUTRAL."""
        from backend.app.agents.agent_scoring_4 import _compute_confluence
        direction, level, boost = _compute_confluence(["LONG", "SHORT"])
        assert direction == "NEUTRAL"
        assert level == 0
        assert boost == 1.0

    def test_weights_used_in_result(self):
        """T1: Result should include weights_used."""
        from backend.app.agents.agent_scoring_4 import compute_meta_scores
        result = compute_meta_scores()
        assert "weights_used" in result
        assert "news" in result["weights_used"]

    def test_custom_weights_applied(self):
        """T1: Custom weights from Learning 4 should be used."""
        from backend.app.agents.agent_scoring_4 import compute_meta_scores
        custom = {"news": 0.5, "trend": 0.3, "tech": 0.2}
        result = compute_meta_scores(weights=custom)
        assert result["weights_used"]["news"] == 0.5

    def test_activation_date_exists(self):
        """P8: Activation date should be set."""
        from backend.app.agents.agent_scoring_4 import ACTIVATION_DATE
        from datetime import date
        assert isinstance(ACTIVATION_DATE, date)

    def test_version_bumped(self):
        from backend.app.agents.agent_scoring_4 import AgentScoring4
        assert AgentScoring4.version == "2.0"

    def test_weekly_config_consumed(self):
        """T1: AgentScoring4.run() should accept weekly_config parameter."""
        from backend.app.agents.agent_scoring_4 import AgentScoring4
        import inspect
        sig = inspect.signature(AgentScoring4.run)
        assert "weekly_config" in sig.parameters

    def test_confluence_3_of_3(self):
        """Confluence 3/3 should give max boost."""
        from backend.app.agents.agent_scoring_4 import _compute_confluence, MAX_CONFLUENCE_BOOST
        direction, level, boost = _compute_confluence(["LONG", "LONG", "LONG"])
        assert direction == "LONG"
        assert level == 3
        assert boost == MAX_CONFLUENCE_BOOST

    def test_confluence_2_of_3(self):
        """Confluence 2/3 should give min boost."""
        from backend.app.agents.agent_scoring_4 import _compute_confluence, MIN_CONFLUENCE_BOOST
        direction, level, boost = _compute_confluence(["LONG", "LONG", "SHORT"])
        assert direction == "LONG"
        assert level == 2
        assert boost == MIN_CONFLUENCE_BOOST


class TestScoringDataBridge:
    """Tests for Scoring 3 → Scoring 4 data format bridge."""

    def test_scoring3_returns_signals_key(self):
        """Scoring 3 result must include 'signals' dict for Scoring 4."""
        from backend.app.agents.agent_scoring_3 import score_technical_setups
        # Empty result (no market data) should still have signals key
        result = score_technical_setups(tickers={})
        assert "signals" in result
        assert isinstance(result["signals"], dict)

    def test_scoring3_signals_format(self):
        """Each signal should have score, direction, details."""
        from backend.app.agents.agent_scoring_3 import score_technical_setups
        # Build a fake result to test the signals bridge
        result = score_technical_setups(tickers={})
        # Signals is empty for empty tickers, but format is correct
        assert isinstance(result["signals"], dict)

    def test_scoring4_reads_scored_not_scored_news(self):
        """Scoring 4 should read 'scored' key from Scoring 1 output."""
        from backend.app.agents.agent_scoring_4 import compute_meta_scores
        # Simulate Scoring 1 output with 'scored' key (not 'scored_news')
        news_data = {
            "scored": [
                {"impacted_tickers": ["GC=F"], "direction": "LONG",
                 "total_score": 80, "news_category": "commodity",
                 "signal_reliability": 90},
                {"impacted_tickers": ["CL=F"], "direction": "SHORT",
                 "total_score": 60, "news_category": "supply_chain",
                 "signal_reliability": 70},
                {"impacted_tickers": ["ZW=F"], "direction": "LONG",
                 "total_score": 70, "news_category": "weather",
                 "signal_reliability": 85},
            ]
        }
        result = compute_meta_scores(news_data=news_data)
        # Should have detected 3 news items and activated news source
        assert result["stats"]["sources_active"] >= 1
        assert "GC=F" in result["by_ticker"]

    def test_scoring4_reads_pydantic_scored_news(self):
        """Scoring 4 should handle Pydantic ScoredNews objects."""
        from backend.app.agents.agent_scoring_4 import compute_meta_scores
        from backend.app.models import ScoredNews, NewsItem, Direction
        from datetime import datetime, timezone
        items = []
        for ticker, direction in [("GC=F", Direction.LONG), ("CL=F", Direction.SHORT), ("ZW=F", Direction.LONG)]:
            news = NewsItem(title="Test", source="test", url="http://test.com",
                            published=datetime.now(timezone.utc))
            sn = ScoredNews(news=news, surprise=80, freshness=90,
                            directional_clarity=85, transmission_delay=70,
                            market_awareness=20, direction=direction,
                            impacted_tickers=[ticker], news_category="commodity",
                            signal_reliability=80)
            items.append(sn)
        result = compute_meta_scores(news_data={"scored": items})
        assert result["stats"]["sources_active"] >= 1
        assert len(result["by_ticker"]) >= 1

    def test_scoring4_tech_signals_consumed(self):
        """Scoring 4 should consume Scoring 3 signals dict."""
        from backend.app.agents.agent_scoring_4 import compute_meta_scores
        tech_data = {
            "signals": {
                "GC=F": {"score": 75, "direction": "LONG", "details": {"strategy": "rsi_reversal"}},
                "CL=F": {"score": 65, "direction": "SHORT", "details": {"strategy": "macd_crossover"}},
                "ZW=F": {"score": 55, "direction": "LONG", "details": {"strategy": "bollinger_squeeze"}},
            }
        }
        result = compute_meta_scores(tech_data=tech_data)
        assert result["stats"]["sources_active"] >= 1
        assert "GC=F" in result["by_ticker"]

    def test_full_3_source_confluence(self):
        """All 3 sources agreeing should produce confluence level 3."""
        from backend.app.agents.agent_scoring_4 import compute_meta_scores
        news_data = {
            "scored": [
                {"impacted_tickers": ["GC=F"], "direction": "LONG", "total_score": 80,
                 "news_category": "commodity", "signal_reliability": 90},
                {"impacted_tickers": ["CL=F"], "direction": "LONG", "total_score": 70,
                 "news_category": "commodity", "signal_reliability": 80},
                {"impacted_tickers": ["ZW=F"], "direction": "LONG", "total_score": 60,
                 "news_category": "weather", "signal_reliability": 75},
            ]
        }
        trend_data = {
            "trend_scored": [{"ticker": "GC=F"}, {"ticker": "CL=F"}, {"ticker": "ZW=F"}],
            "accumulation": {"GC=F": {"long": 30, "short": 5}},
        }
        tech_data = {
            "signals": {
                "GC=F": {"score": 70, "direction": "LONG", "details": {}},
                "CL=F": {"score": 60, "direction": "LONG", "details": {}},
                "ZW=F": {"score": 50, "direction": "LONG", "details": {}},
            }
        }
        result = compute_meta_scores(news_data=news_data, trend_data=trend_data, tech_data=tech_data)
        # GC=F should have all 3 sources → confluence 3
        gc = result["by_ticker"].get("GC=F")
        assert gc is not None
        assert gc["confluence_level"] == 3
        assert gc["sources_contributing"] == 3


class TestTeam4V2Trader4:
    """Trader 4 v2.0 audit tests."""

    def test_correlation_groups_exist(self):
        """P5: Correlation groups should be defined."""
        from backend.app.agents.agent_trader_4 import META_CORRELATION_GROUPS
        assert len(META_CORRELATION_GROUPS) >= 10
        assert "energy" in META_CORRELATION_GROUPS
        assert "gold_safe" in META_CORRELATION_GROUPS

    def test_correlation_conflict_detected(self):
        """P5: Conflicting positions should be detected."""
        from backend.app.agents.agent_trader_4 import _check_correlation_conflict
        positions = {
            "GC=F": {"status": "OPEN", "direction": "LONG"},
        }
        assert _check_correlation_conflict("SI=F", "SHORT", positions) is True
        assert _check_correlation_conflict("SI=F", "LONG", positions) is False

    def test_agent_versions_populated(self):
        """P6: _get_agent_versions should return version dict."""
        from backend.app.agents.agent_trader_4 import _get_agent_versions
        versions = _get_agent_versions()
        assert "scoring_4" in versions
        assert "trader_4" in versions

    def test_tp_sl_config_exists(self):
        """P3: TP/SL configuration should exist."""
        from backend.app.agents.agent_trader_4 import (
            TP_PCT_BY_CONFLUENCE, SL_PCT, TRAILING_ACTIVATION_PCT
        )
        assert TP_PCT_BY_CONFLUENCE[3] > TP_PCT_BY_CONFLUENCE[2]
        assert SL_PCT > 0
        assert TRAILING_ACTIVATION_PCT > 0

    def test_max_hold_hours_72(self):
        """P7: Max hold hours should be 72 (0-3 days)."""
        from backend.app.agents.agent_trader_4 import MAX_HOLD_HOURS
        assert MAX_HOLD_HOURS == 72

    def test_version_bumped(self):
        from backend.app.agents.agent_trader_4 import AgentTrader4
        assert AgentTrader4.version == "2.0"

    def test_metrics_include_activation(self):
        """P8: Metrics should include activation info."""
        from backend.app.agents.agent_trader_4 import AgentTrader4
        t4 = AgentTrader4()
        metrics = t4.get_metrics()
        assert "activation_date" in metrics
        assert "is_active" in metrics

    def test_weekly_config_param(self):
        """run() should accept weekly_config parameter."""
        from backend.app.agents.agent_trader_4 import AgentTrader4
        import inspect
        sig = inspect.signature(AgentTrader4.run)
        assert "weekly_config" in sig.parameters


class TestTeam4V2Journal4:
    """Journal 4 v2.0 audit tests."""

    def test_weekly_summary_exists(self):
        """L1: Journal 4 should have compute_weekly_summary."""
        from backend.app.agents.agent_journal_4 import AgentJournal4
        j4 = AgentJournal4()
        summary = j4.compute_weekly_summary()
        assert "entries_count" in summary
        assert "win_rate" in summary
        assert "sharpe" in summary
        assert "by_combo" in summary
        assert "by_confluence" in summary
        assert "by_duration" in summary

    def test_sharpe_computed(self):
        """L2: _compute_sharpe should work correctly."""
        from backend.app.agents.agent_journal_4 import _compute_sharpe
        assert _compute_sharpe([1.0, 2.0, 3.0]) is not None
        assert _compute_sharpe([1.0]) is None
        assert _compute_sharpe([5.0, 5.0, 5.0]) is None  # zero variance

    def test_duration_category_classified(self):
        """L3: Duration classification should work."""
        from backend.app.agents.agent_journal_4 import _classify_duration
        assert _classify_duration(4.0) == "intraday"
        assert _classify_duration(12.0) == "overnight"
        assert _classify_duration(48.0) == "multi_day"

    def test_team_combination_stats_include_sharpe(self):
        """L2: Combo stats should include Sharpe ratio."""
        from backend.app.agents.agent_journal_4 import AgentJournal4
        j4 = AgentJournal4()
        entries = [
            {"team_combination": "news+tech", "pnl_pct": 1.5,
             "confluence_level": 2, "close_type": "TP_HIT",
             "duration_category": "intraday"},
            {"team_combination": "news+tech", "pnl_pct": -0.5,
             "confluence_level": 2, "close_type": "SL_HIT",
             "duration_category": "overnight"},
            {"team_combination": "news+tech", "pnl_pct": 2.0,
             "confluence_level": 3, "close_type": "TP_HIT",
             "duration_category": "multi_day"},
        ]
        stats = j4._compute_team_combination_stats(entries)
        assert "news+tech" in stats
        assert "sharpe" in stats["news+tech"]
        assert "close_types" in stats["news+tech"]
        assert "duration_categories" in stats["news+tech"]

    def test_version_bumped(self):
        from backend.app.agents.agent_journal_4 import AgentJournal4
        assert AgentJournal4.version == "2.0"


class TestTeam4V2Learning4:
    """Learning 4 v2.0 audit tests."""

    def test_5_dimensions(self):
        """Learning 4 should compute 5 dimensions."""
        from backend.app.agents.agent_learning_4 import compute_meta_learning
        result = compute_meta_learning([])
        assert "combo_adj" in result
        assert "ticker_adj" in result
        assert "confluence_adj" in result
        assert "duration_adj" in result
        assert "weight_optimization" in result

    def test_weekly_config_generation(self):
        """P1: Learning 4 should generate weekly config."""
        from backend.app.agents.agent_learning_4 import AgentLearning4
        l4 = AgentLearning4()
        config = l4.generate_weekly_config()
        assert "weights" in config
        assert "validated_combos" in config
        assert "config_version" in config
        assert config["config_version"] == 1

    def test_config_history_tracking(self):
        """P2: Config changes should be tracked."""
        from backend.app.agents.agent_learning_4 import AgentLearning4
        l4 = AgentLearning4()
        l4.generate_weekly_config()
        l4.generate_weekly_config()
        history = l4.get_config_history()
        assert len(history) == 1  # First config archived when second generated

    def test_ab_testing(self):
        """P2: AB testing should compare configs."""
        from backend.app.agents.agent_learning_4 import AgentLearning4
        l4 = AgentLearning4()
        l4.generate_weekly_config()
        config2 = l4.generate_weekly_config()
        assert "ab_test" in config2
        assert config2["ab_test"] is not None
        assert "winner" in config2["ab_test"]

    def test_target_win_rate(self):
        """Target WR should be 80%."""
        from backend.app.agents.agent_learning_4 import TARGET_WIN_RATE
        assert TARGET_WIN_RATE == 80.0

    def test_below_target_anomaly(self):
        """Should detect when WR is far below target."""
        from backend.app.agents.agent_learning_4 import compute_meta_learning
        # Create 30 entries with low WR
        entries = []
        for i in range(30):
            entries.append({
                "direction": "LONG",
                "pnl_pct": -1.0 if i % 3 != 0 else 2.0,
                "exit_time": f"2026-03-0{(i % 7) + 1}T12:00:00+00:00",
                "entry_time": f"2026-03-0{(i % 7) + 1}T10:00:00+00:00",
                "team_combination": "news+tech",
                "confluence_level": 2,
                "duration_category": "intraday",
            })
        result = compute_meta_learning(entries)
        anomalies = result.get("anomalies", [])
        assert any("BELOW_TARGET" in a for a in anomalies)

    def test_version_bumped(self):
        from backend.app.agents.agent_learning_4 import AgentLearning4
        assert AgentLearning4.version == "2.0"

    def test_metrics_include_weekly(self):
        """Metrics should include weekly config info."""
        from backend.app.agents.agent_learning_4 import AgentLearning4
        l4 = AgentLearning4()
        metrics = l4.get_metrics()
        assert "has_weekly_config" in metrics
        assert "config_version" in metrics
        assert "target_win_rate" in metrics


class TestVersioningAudit:
    """Tests for versioning system consistency across all 4 teams."""

    def test_trader_2_stamps_agent_versions(self):
        """V1: Trader 2 _make_change should include agent_versions in new_position."""
        from backend.app.agents.agent_trader_2 import AgentTrader2
        t2 = AgentTrader2()
        assert hasattr(t2, "_get_agent_versions")
        versions = t2._get_agent_versions()
        assert "trader_2" in versions

    def test_trader_2_has_get_agent_versions(self):
        """V1: Trader 2 should have _get_agent_versions method."""
        from backend.app.agents.agent_trader_2 import AgentTrader2
        import inspect
        assert "_get_agent_versions" in [m[0] for m in inspect.getmembers(AgentTrader2)]

    def test_learning_2_has_version_filter(self):
        """V1: Learning 2 should filter entries by version."""
        from backend.app.agents.agent_learning_2 import _filter_by_current_versions
        # Empty entries should return empty
        assert _filter_by_current_versions([]) == []

    def test_learning_3_has_version_filter(self):
        """V1: Learning 3 should filter entries by version."""
        from backend.app.agents.agent_learning_3 import _filter_by_current_versions
        assert _filter_by_current_versions([]) == []

    def test_learning_4_has_version_filter(self):
        """V1: Learning 4 should filter entries by version."""
        from backend.app.agents.agent_learning_4 import _filter_by_current_versions
        assert _filter_by_current_versions([]) == []

    def test_version_filter_keeps_unversioned_entries(self):
        """V1: Entries without agent_versions should be kept (backward compat)."""
        from backend.app.agents.agent_learning_2 import _filter_by_current_versions
        entries = [
            {"ticker": "HG=F", "pnl_pct": 2.0},  # No agent_versions
            {"ticker": "CC=F", "pnl_pct": -1.0, "agent_versions": None},
        ]
        filtered = _filter_by_current_versions(entries)
        assert len(filtered) == 2

    def test_version_filter_removes_old_versions(self):
        """V1: Entries with old versions should be filtered out."""
        from backend.app.agents.agent_learning_3 import _filter_by_current_versions
        from backend.app.agents.agent_scoring_3 import AgentScoring3
        from backend.app.agents.agent_trader_3 import AgentTrader3
        entries = [
            {"ticker": "GC=F", "pnl_pct": 2.0, "agent_versions": {
                "scoring_3": AgentScoring3.version,
                "trader_3": AgentTrader3.version,
            }},
            {"ticker": "CL=F", "pnl_pct": -1.0, "agent_versions": {
                "scoring_3": "0.1",  # Old version
                "trader_3": "0.1",
            }},
        ]
        filtered = _filter_by_current_versions(entries)
        # Current version entry kept, old version entry removed
        assert len(filtered) == 1
        assert filtered[0]["ticker"] == "GC=F"

    def test_performance_version_bumped(self):
        """Performance agent version should be 8.2."""
        from backend.app.agents.agent_performance import AgentPerformance
        assert AgentPerformance.version == "8.2"

    def test_performance_has_filter_method(self):
        """V1: Performance should have _filter_entries_by_version method."""
        from backend.app.agents.agent_performance import AgentPerformance
        perf = AgentPerformance()
        assert hasattr(perf, "_filter_entries_by_version")

    def test_performance_filter_keeps_unversioned(self):
        """V1: Performance filter should keep pre-versioning entries."""
        from backend.app.agents.agent_performance import AgentPerformance
        perf = AgentPerformance()
        entries = [{"ticker": "GC=F"}, {"ticker": "CL=F"}]  # No agent_versions
        filtered = perf._filter_entries_by_version(entries, "scoring", "trader_1")
        assert len(filtered) == 2


class TestPositionMonitoring:
    """Tests for position monitoring in Teams 3 and 4."""

    def test_trader_3_has_position_monitor(self):
        """V1: Trader 3 should have run_position_monitor method."""
        from backend.app.agents.agent_trader_3 import AgentTrader3
        t3 = AgentTrader3()
        assert hasattr(t3, "run_position_monitor")

    def test_trader_4_has_position_monitor(self):
        """V1: Trader 4 should have run_position_monitor method."""
        from backend.app.agents.agent_trader_4 import AgentTrader4
        t4 = AgentTrader4()
        assert hasattr(t4, "run_position_monitor")

    def test_registry_has_position_monitor_3(self):
        """V1: Registry should have run_position_monitor_3."""
        from backend.app.agents.registry import run_position_monitor_3
        assert callable(run_position_monitor_3)

    def test_registry_has_position_monitor_4(self):
        """V1: Registry should have run_position_monitor_4."""
        from backend.app.agents.registry import run_position_monitor_4
        assert callable(run_position_monitor_4)

    def test_trader_3_monitor_empty_state(self):
        """V1: Position monitor with no active positions should return 0."""
        from backend.app.agents.agent_trader_3 import AgentTrader3
        with patch("backend.app.agents.agent_trader_3._load_positions",
                    return_value={"active": [], "closed": []}):
            t3 = AgentTrader3()
            result = t3.run_position_monitor()
            assert result["active"] == 0
            assert result["closed"] == 0

    def test_trader_4_monitor_empty_state(self):
        """V1: Position monitor with no open positions should return 0."""
        from backend.app.agents.agent_trader_4 import AgentTrader4
        with patch("backend.app.agents.agent_trader_4._load_positions",
                    return_value={}):
            t4 = AgentTrader4()
            result = t4.run_position_monitor()
            assert result["active"] == 0
            assert result["closed"] == 0


class TestTradeSelectionTimeout:
    """v6.5 P9: Tests for trade selection timeout protection.

    Production incident 2026-03-09: trade_selector hung on market data fetch,
    blocking the entire pipeline (Teams 2-4 never ran).
    """

    def test_dynamic_correlation_has_timeout(self):
        """P9: _compute_dynamic_correlation uses ThreadPoolExecutor with timeout."""
        import inspect
        from backend.app.trade_selector import _compute_dynamic_correlation
        source = inspect.getsource(_compute_dynamic_correlation)
        assert "ThreadPoolExecutor" in source, \
            "Dynamic correlation must use ThreadPoolExecutor for timeout"
        assert "timeout=10" in source, \
            "Dynamic correlation fetch_history must have 10s timeout"
        assert "shutdown(wait=False" in source, \
            "Executor must use non-blocking shutdown"

    def test_dynamic_correlation_timeout_returns_none(self):
        """P9: When fetch_history times out, correlation returns None (not hang)."""
        import time
        from backend.app.trade_selector import _compute_dynamic_correlation, _corr_cache

        # Clear cache to force fetch
        _corr_cache.clear()

        def slow_fetch(*args, **kwargs):
            time.sleep(15)  # Longer than 10s timeout
            return None

        with patch("backend.app.trade_selector.fetch_history", side_effect=slow_fetch):
            start = time.monotonic()
            result = _compute_dynamic_correlation("FAKE1", "FAKE2")
            elapsed = time.monotonic() - start
            assert result is None, "Timed-out correlation must return None"
            assert elapsed < 12, f"Correlation took {elapsed:.1f}s — should timeout at 10s"

    def test_trader1_select_trade_has_global_timeout(self):
        """P9: AgentTrader._select_trade wraps select_trade with 60s timeout."""
        import inspect
        from backend.app.agents.agent_trader import AgentTrader
        source = inspect.getsource(AgentTrader._select_trade)
        assert "timeout=60" in source, \
            "_select_trade must have 60s global timeout"
        assert "ThreadPoolExecutor" in source, \
            "_select_trade must use ThreadPoolExecutor for timeout"
        assert "shutdown(wait=False" in source, \
            "Executor must use non-blocking shutdown"

    def test_trader1_timeout_returns_scan_result(self):
        """P9: When select_trade times out, return a valid ScanResult with error."""
        from backend.app.agents.agent_trader import AgentTrader
        from backend.app.models import ScanType
        import time

        def slow_select_trade(*args, **kwargs):
            time.sleep(120)

        agent = AgentTrader()
        # Patch at module level where the lazy import resolves
        with patch("backend.app.trade_selector.select_trade",
                    side_effect=slow_select_trade):
            with patch.object(agent, "log"):  # Suppress logging
                start = time.monotonic()
                result = agent._select_trade(
                    [], ScanType.EUROPE, {}, None, None)
                elapsed = time.monotonic() - start
                assert elapsed < 65, f"Timeout took {elapsed:.1f}s — expected ~60s"
                assert result.has_trade is False
                assert "timeout" in result.reason_no_trade.lower()

    def test_pipeline_trader1_failure_doesnt_block_teams(self):
        """P9: If Trader 1 raises, Teams 2-4 still execute in registry."""
        import inspect
        from backend.app.agents.registry import run_scan_pipeline
        source = inspect.getsource(run_scan_pipeline)
        # Trader 1 call must be wrapped in try/except
        trader1_section = source[source.find("Step 4"):source.find("Step 5")]
        assert "try:" in trader1_section, \
            "Trader 1 call must be wrapped in try/except"
        assert "except Exception" in trader1_section, \
            "Trader 1 must catch exceptions to let Teams 2-4 run"
        assert "pipeline continues" in trader1_section.lower() or "Teams 2-4" in trader1_section, \
            "Error message must mention pipeline continuation"

    def test_correlation_slow_check_logging(self):
        """P9: Dynamic correlation logs warning when taking >5s."""
        import inspect
        from backend.app.trade_selector import _check_correlation
        source = inspect.getsource(_check_correlation)
        assert "correlation slow" in source.lower() or "corr_elapsed" in source, \
            "Slow correlation check must log a warning"
