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
        assert "journal" in agents
        assert "learning" in agents
        assert "auditor" in agents
        assert len(agents) == 6

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
        assert len(statuses) == 7  # 6 real agents + virtual UX

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
        expected = {"news", "scoring", "trader_1", "journal", "learning", "ux"}
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
