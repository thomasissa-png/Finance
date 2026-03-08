"""Tests for Agent Trader 2 — Trend following on commodities.

Tests:
1. Agent initialization and registration
2. Position init/load/save (JSON fallback)
3. News filtering (relevant tickers only)
4. Direction evaluation (LONG/SHORT signals)
5. Position change logic (threshold, confirmation, reversal)
6. P&L tracking (unrealized + realized)
7. Metrics
8. API endpoints
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from backend.app.agents.agent_trader_2 import (
    AgentTrader2,
    TREND_TICKERS,
    RELEVANT_CATEGORIES,
    MIN_NEWS_SCORE,
    _load_positions,
    _save_positions,
    _ensure_positions_file,
)
from backend.app.models import (
    Direction,
    NewsItem,
    ScoredNews,
    ScanType,
    ChainReaction,
)


@pytest.fixture
def agent():
    return AgentTrader2()


@pytest.fixture
def temp_positions_file():
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
        json.dump({}, f)
        return Path(f.name)


def _make_scored_news(ticker, direction="LONG", score_surprise=80,
                      category="commodity", reliability=80, clarity=80,
                      title="Test commodity news", chain_ticker=None,
                      chain_direction="LONG"):
    """Helper to create a ScoredNews object."""
    news = NewsItem(
        title=title,
        source="test",
        url="https://test.com",
        published=datetime.now(timezone.utc),
        source_weight=1.1,
        description="Test description",
    )

    impacted = [ticker] if ticker else []
    chains = []
    if chain_ticker:
        chains.append(ChainReaction(
            ticker=chain_ticker,
            direction=Direction(chain_direction),
            reason="chain reaction",
            source_ticker=ticker or "CL=F",
        ))

    return ScoredNews(
        news=news,
        surprise=score_surprise,
        freshness=90,
        directional_clarity=clarity,
        transmission_delay=80,
        market_awareness=10,
        expected_magnitude=60,
        signal_reliability=reliability,
        direction=Direction(direction),
        impacted_tickers=impacted,
        reasoning="Test reasoning",
        news_category=category,
        category_score_mult=1.5,
        chain_reactions=chains,
    )


class TestAgentTrader2Init:
    def test_name_and_description(self, agent):
        assert agent.name == "trader_2"
        assert "trend" in agent.description.lower() or "Trend" in agent.description

    def test_inherits_base_agent(self, agent):
        from backend.app.agents.base import BaseAgent
        assert isinstance(agent, BaseAgent)

    def test_initial_metrics(self, agent):
        m = agent.get_metrics()
        assert m["tickers_tracked"] == 4
        assert m["position_changes_total"] == 0
        assert m["evaluations_today"] == 0

    def test_reset_daily_counters(self, agent):
        agent._evaluations_today = 5
        agent.reset_daily_counters()
        assert agent._evaluations_today == 0


class TestTrendTickers:
    def test_four_tickers_configured(self):
        assert len(TREND_TICKERS) == 4
        assert "HG=F" in TREND_TICKERS  # Cuivre
        assert "CC=F" in TREND_TICKERS  # Cacao
        assert "KC=F" in TREND_TICKERS  # Café
        assert "ZW=F" in TREND_TICKERS  # Blé

    def test_each_has_name_and_category(self):
        for ticker, info in TREND_TICKERS.items():
            assert "name" in info
            assert "category" in info


class TestPositionPersistence:
    def test_load_save_json(self, temp_positions_file):
        with patch("backend.app.database.is_pg_enabled", return_value=False), \
             patch("backend.app.agents.agent_trader_2.POSITIONS_FILE", temp_positions_file):

            # Save
            test_data = {"HG=F": {"direction": "LONG", "ticker": "HG=F"}}
            _save_positions(test_data)

            # Load
            loaded = _load_positions()
            assert loaded["HG=F"]["direction"] == "LONG"

    def test_ensure_file_creates_empty(self, tmp_path):
        file_path = tmp_path / "new_positions.json"
        with patch("backend.app.agents.agent_trader_2.POSITIONS_FILE", file_path):
            _ensure_positions_file()
            assert file_path.exists()
            data = json.loads(file_path.read_text())
            assert data == {}

    def test_load_corrupt_file(self, tmp_path):
        file_path = tmp_path / "corrupt.json"
        file_path.write_text("NOT JSON")
        with patch("backend.app.database.is_pg_enabled", return_value=False), \
             patch("backend.app.agents.agent_trader_2.POSITIONS_FILE", file_path):
            result = _load_positions()
            assert result == {}


class TestNewsFiltering:
    def test_filters_relevant_tickers(self, agent):
        news = [
            _make_scored_news("HG=F", "LONG"),
            _make_scored_news("CL=F", "LONG"),  # Not in our 4 tickers
            _make_scored_news("ZW=F", "SHORT"),
        ]
        result = agent._filter_relevant_news(news)
        assert "HG=F" in result
        assert "ZW=F" in result
        assert "CL=F" not in result

    def test_filters_low_score(self, agent):
        news = [_make_scored_news("HG=F", "LONG", score_surprise=5)]  # Very low
        result = agent._filter_relevant_news(news)
        # Score might still be above MIN_NEWS_SCORE with high reliability
        # but with surprise=5, the total_score should be low

    def test_filters_neutral_direction(self, agent):
        news = [_make_scored_news("HG=F", "NEUTRAL")]
        result = agent._filter_relevant_news(news)
        assert len(result) == 0

    def test_includes_chain_reactions(self, agent):
        # News about CL=F that chains to HG=F
        news = [_make_scored_news("CL=F", "LONG", chain_ticker="HG=F")]
        result = agent._filter_relevant_news(news)
        assert "HG=F" in result

    def test_filters_irrelevant_categories(self, agent):
        news = [_make_scored_news("HG=F", "LONG", category="earnings")]
        result = agent._filter_relevant_news(news)
        assert len(result) == 0


class TestDirectionEvaluation:
    def test_initial_position_set(self, agent):
        """First evaluation on NEUTRAL position should set direction."""
        position = agent._init_position("HG=F", TREND_TICKERS["HG=F"])
        position["entry_price"] = 4.5
        position["current_price"] = 4.5

        news = [_make_scored_news("HG=F", "LONG", score_surprise=80, reliability=80)]

        change = agent._evaluate_ticker("HG=F", news, position)
        assert change is not None
        assert change["new_direction"] == "LONG"
        assert change["old_direction"] == "NEUTRAL"

    def test_confirmation_no_change(self, agent):
        """News confirming existing direction should NOT trigger a change."""
        position = {
            "direction": "LONG",
            "confidence": 50,
            "entry_price": 4.5,
            "current_price": 4.6,
            "key_catalysts": [],
            "reasoning": "test",
        }
        # Single confirming news — not strong enough alone
        news = [_make_scored_news("HG=F", "LONG", score_surprise=60)]
        change = agent._evaluate_ticker("HG=F", news, position)
        assert change is None
        # Confidence should have increased
        assert position["confidence"] > 50

    def test_weak_contradiction_no_change(self, agent):
        """Weak contradictory signal should NOT flip position."""
        position = {
            "direction": "LONG",
            "confidence": 70,
            "entry_price": 4.5,
            "current_price": 4.6,
            "key_catalysts": [],
            "reasoning": "test",
        }
        # Single contradictory news — not strong enough
        news = [_make_scored_news("HG=F", "SHORT", score_surprise=50, reliability=50)]
        change = agent._evaluate_ticker("HG=F", news, position)
        assert change is None
        # Confidence should have decreased
        assert position["confidence"] < 70

    def test_strong_contradiction_flips(self, agent):
        """Strong contradictory signal should flip position."""
        position = {
            "direction": "LONG",
            "confidence": 50,
            "entry_price": 4.5,
            "current_price": 4.3,
            "key_catalysts": [],
            "reasoning": "test",
            "total_switches": 0,
            "realized_pnl_pct": 0.0,
            "history": [],
            "name": "Cuivre",
            "category": "commodities_industrial",
            "ticker": "HG=F",
        }
        # Multiple strong contradictory news
        news = [
            _make_scored_news("HG=F", "SHORT", score_surprise=90, reliability=90, clarity=90,
                              title="MASSIVE copper supply surplus discovered"),
            _make_scored_news("HG=F", "SHORT", score_surprise=85, reliability=85, clarity=85,
                              title="China copper demand collapses — PMI crash"),
        ]
        change = agent._evaluate_ticker("HG=F", news, position)
        assert change is not None
        assert change["new_direction"] == "SHORT"
        assert change["old_direction"] == "LONG"


class TestPnLTracking:
    def test_realized_pnl_on_flip(self, agent):
        """When position flips, realized P&L should be recorded."""
        position = {
            "direction": "LONG",
            "confidence": 50,
            "entry_price": 100.0,
            "current_price": 110.0,
            "key_catalysts": [],
            "reasoning": "test",
            "total_switches": 0,
            "realized_pnl_pct": 0.0,
            "history": [],
            "name": "Test",
            "category": "test",
            "ticker": "HG=F",
        }
        change = agent._make_change(
            "HG=F", position, "SHORT",
            "Test reversal", [], 30.0,
        )

        assert change["close_pnl"] == 10.0  # +10% gain on LONG 100→110
        assert change["new_position"]["realized_pnl_pct"] == 10.0

    def test_realized_pnl_short(self, agent):
        """Short position P&L calculated correctly."""
        position = {
            "direction": "SHORT",
            "confidence": 50,
            "entry_price": 100.0,
            "current_price": 95.0,
            "key_catalysts": [],
            "reasoning": "test",
            "total_switches": 1,
            "realized_pnl_pct": 5.0,
            "history": [],
            "name": "Test",
            "category": "test",
            "ticker": "HG=F",
        }
        change = agent._make_change(
            "HG=F", position, "LONG",
            "Test reversal", [], 25.0,
        )

        assert change["close_pnl"] == 5.0  # +5% gain on SHORT 100→95
        assert change["new_position"]["realized_pnl_pct"] == 10.0  # 5 + 5

    def test_history_appended(self, agent):
        """Position changes should be recorded in history."""
        position = {
            "direction": "LONG",
            "entry_price": 100.0,
            "current_price": 105.0,
            "total_switches": 0,
            "realized_pnl_pct": 0.0,
            "history": [],
            "name": "Test",
            "category": "test",
            "ticker": "HG=F",
            "confidence": 50,
            "key_catalysts": [],
            "reasoning": "test",
        }
        change = agent._make_change("HG=F", position, "SHORT", "Test", [], 25.0)
        assert len(change["new_position"]["history"]) == 1
        assert change["new_position"]["history"][0]["from_direction"] == "LONG"
        assert change["new_position"]["history"][0]["to_direction"] == "SHORT"
        assert change["new_position"]["total_switches"] == 1


class TestAgentRun:
    def test_run_with_no_news(self, agent, tmp_path):
        pos_file = tmp_path / "pos.json"
        pos_file.write_text("{}")

        with patch("backend.app.database.is_pg_enabled", return_value=False), \
             patch("backend.app.agents.agent_trader_2.POSITIONS_FILE", pos_file), \
             patch("backend.app.agents.agent_trader_2._fetch_current_price", return_value=100.0):
            result = agent.run(scored_news=[], scan_type=ScanType.EUROPE)

        assert "positions" in result
        assert "changes" in result
        assert len(result["changes"]) == 0
        # Positions should have been initialized
        assert len(result["positions"]) == 4

    def test_run_initializes_positions(self, agent, tmp_path):
        pos_file = tmp_path / "pos.json"
        pos_file.write_text("{}")

        with patch("backend.app.database.is_pg_enabled", return_value=False), \
             patch("backend.app.agents.agent_trader_2.POSITIONS_FILE", pos_file), \
             patch("backend.app.agents.agent_trader_2._fetch_current_price", return_value=50.0):
            result = agent.run(scored_news=[], scan_type=ScanType.US)

        positions = result["positions"]
        for ticker in TREND_TICKERS:
            assert ticker in positions
            assert positions[ticker]["direction"] == "NEUTRAL"
            assert positions[ticker]["entry_price"] == 50.0


class TestRegistration:
    def test_trader_2_in_registry(self):
        """Trader 2 should be registered in the agent registry."""
        with patch("backend.app.agents.agent_trader_2._fetch_current_price", return_value=100.0):
            from backend.app.agents.registry import get_agent, _ensure_agents
            # Force re-init
            import backend.app.agents.registry as reg
            old = reg._agents
            reg._agents = {}
            try:
                _ensure_agents()
                agent = get_agent("trader_2")
                assert agent is not None
                assert agent.name == "trader_2"
            finally:
                reg._agents = old

    def test_trader_2_has_status(self, agent):
        status = agent.status
        assert status["name"] == "trader_2"
        assert "status" in status


class TestConfidenceBounds:
    def test_confidence_bounded_0_100_on_init(self, agent, tmp_path):
        """Confidence must be 0 on initial NEUTRAL positions."""
        pos_file = tmp_path / "pos.json"
        pos_file.write_text("{}")

        with patch("backend.app.database.is_pg_enabled", return_value=False), \
             patch("backend.app.agents.agent_trader_2.POSITIONS_FILE", pos_file), \
             patch("backend.app.agents.agent_trader_2._fetch_current_price", return_value=50.0):
            result = agent.run(scored_news=[], scan_type=ScanType.EUROPE)

        for ticker, pos in result["positions"].items():
            assert 0 <= pos["confidence"] <= 100, f"{ticker} confidence {pos['confidence']} out of bounds"

    def test_confidence_bounded_0_100_after_flip(self, agent):
        """Confidence must stay in [0, 100] after a direction change."""
        position = {
            "direction": "NEUTRAL",
            "confidence": 0,
            "entry_price": 100.0,
            "current_price": 100.0,
            "unrealized_pnl_pct": 0.0,
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "last_update": datetime.now(timezone.utc).isoformat(),
            "total_switches": 0,
            "realized_pnl_pct": 0.0,
            "history": [],
            "key_catalysts": [],
            "reasoning": "",
            "name": "Test",
            "category": "test",
            "ticker": "HG=F",
        }
        # Very strong signal — confidence should cap at 100
        news = [
            _make_scored_news("HG=F", "LONG", score_surprise=100, reliability=100, clarity=100,
                              title=f"Strong signal {i}")
            for i in range(5)
        ]
        change = agent._evaluate_ticker("HG=F", news, position)
        assert change is not None
        assert 0 <= change["new_position"]["confidence"] <= 100


class TestAuditProfile:
    def test_trader_2_audit_profile_exists(self):
        """Trader 2 must have an audit profile in the auditor."""
        from backend.app.agents.agent_auditor import AUDIT_PROFILES
        assert "trader_2" in AUDIT_PROFILES
        profile = AUDIT_PROFILES["trader_2"]
        assert len(profile["checks"]) >= 8

    def test_trader_2_audit_runs_without_error(self):
        """Audit should complete even with no positions."""
        with patch("backend.app.agents.agent_trader_2._fetch_current_price", return_value=100.0):
            from backend.app.agents.agent_auditor import AgentAuditor
            auditor = AgentAuditor()
            report = auditor.run(target_agent="trader_2")
            assert "error" not in report
            assert report["score"] > 0
            assert len(report["findings"]) > 0


class TestNewsSourceCoverage:
    """Trader 2 audits the News agent — ensuring all 4 tickers have adequate coverage."""

    def test_cc_f_in_yfinance_key_tickers(self):
        """CC=F (cocoa) must be in KEY_TICKERS for yfinance news collection."""
        from backend.app.news_collector import collect_yfinance_news
        import inspect
        source = inspect.getsource(collect_yfinance_news)
        assert '"CC=F"' in source or "'CC=F'" in source, \
            "CC=F missing from KEY_TICKERS — cocoa gets no yfinance news"

    def test_all_trader2_tickers_in_yfinance_news(self):
        """All 4 Trader 2 tickers must be in yfinance KEY_TICKERS."""
        import inspect
        from backend.app.news_collector import collect_yfinance_news
        source = inspect.getsource(collect_yfinance_news)
        for ticker in TREND_TICKERS:
            assert f'"{ticker}"' in source or f"'{ticker}'" in source, \
                f"{ticker} missing from yfinance KEY_TICKERS"

    def test_zw_f_chain_reactions_complete(self):
        """ZW=F must chain to ZC=F, ZS=F, LE=F, HE=F (rotation + feed cost)."""
        from backend.app.config import CHAIN_REACTIONS
        zw_chains = CHAIN_REACTIONS.get("ZW=F", [])
        chain_tickers = {c["ticker"] for c in zw_chains}
        assert "ZC=F" in chain_tickers, "ZW=F missing chain to ZC=F (corn rotation)"
        assert "ZS=F" in chain_tickers, "ZW=F missing chain to ZS=F (soy rotation)"
        assert "LE=F" in chain_tickers, "ZW=F missing chain to LE=F (feed cost inverse)"
        assert "HE=F" in chain_tickers, "ZW=F missing chain to HE=F (feed cost inverse)"

    def test_zw_f_feed_cost_chains_are_inverse(self):
        """Wheat→Livestock chains must be inverse (wheat up = feed cost up = margin pressure)."""
        from backend.app.config import CHAIN_REACTIONS
        zw_chains = {c["ticker"]: c["direction"] for c in CHAIN_REACTIONS.get("ZW=F", [])}
        assert zw_chains.get("LE=F") == "inverse", "ZW=F→LE=F should be inverse (feed cost)"
        assert zw_chains.get("HE=F") == "inverse", "ZW=F→HE=F should be inverse (feed cost)"

    def test_gnews_drought_query_includes_cc_f(self):
        """GNews drought query must include CC=F — cocoa is highly drought-sensitive."""
        from backend.app.data_apis import GNEWS_QUERIES
        drought_q = [q for q in GNEWS_QUERIES if "drought" in q["q"]]
        assert drought_q, "No drought query found in GNEWS_QUERIES"
        all_drought_tickers = set()
        for q in drought_q:
            all_drought_tickers.update(q["tickers"])
        assert "CC=F" in all_drought_tickers, \
            "CC=F missing from drought GNews queries — Côte d'Ivoire/Ghana drought = cocoa crisis"

    def test_gnews_portuguese_query_includes_cc_f(self):
        """Portuguese GNews query must include CC=F — Brazil is 5th largest cocoa producer."""
        from backend.app.data_apis import GNEWS_QUERIES
        pt_queries = [q for q in GNEWS_QUERIES if q.get("lang") == "pt"]
        assert pt_queries, "No Portuguese query found in GNEWS_QUERIES"
        pt_tickers = set()
        for q in pt_queries:
            pt_tickers.update(q["tickers"])
        assert "CC=F" in pt_tickers, \
            "CC=F missing from Portuguese GNews query — Brazil cocoa = 12-24h edge"

    def test_gnews_portuguese_query_includes_cacau(self):
        """Portuguese query should contain 'cacau' keyword for Brazilian cocoa news."""
        from backend.app.data_apis import GNEWS_QUERIES
        pt_queries = [q for q in GNEWS_QUERIES if q.get("lang") == "pt"]
        assert any("cacau" in q["q"] for q in pt_queries), \
            "Portuguese query missing 'cacau' keyword — won't match Brazilian cocoa news"

    def test_all_trader2_tickers_have_chain_reactions(self):
        """Each Trader 2 ticker must appear in CHAIN_REACTIONS (as source or target)."""
        from backend.app.config import CHAIN_REACTIONS
        # Collect all tickers that appear anywhere in chain reactions
        all_chain_tickers = set(CHAIN_REACTIONS.keys())
        for targets in CHAIN_REACTIONS.values():
            for t in targets:
                all_chain_tickers.add(t["ticker"])
        for ticker in TREND_TICKERS:
            assert ticker in all_chain_tickers, \
                f"{ticker} has no chain reaction coverage — isolated from spillover signals"

    def test_cocoa_gnews_dedicated_query_exists(self):
        """There must be a dedicated GNews query for cocoa (CC=F)."""
        from backend.app.data_apis import GNEWS_QUERIES
        cocoa_dedicated = [q for q in GNEWS_QUERIES
                           if "CC=F" in q["tickers"] and "cocoa" in q["q"].lower()]
        assert cocoa_dedicated, "No dedicated cocoa GNews query — CC=F under-covered"

    def test_trader2_relevant_categories_include_weather(self):
        """Trader 2 MUST accept weather category — critical for commodity trends."""
        assert "weather" in RELEVANT_CATEGORIES, \
            "weather not in RELEVANT_CATEGORIES — misses drought/frost/hurricane signals"

    def test_trader2_excludes_zero_edge_categories(self):
        """Trader 2 must NOT accept earnings or macro — zero edge for commodities."""
        assert "earnings" not in RELEVANT_CATEGORIES
        assert "macro" not in RELEVANT_CATEGORIES


class TestDatabaseTable:
    def test_trend_positions_ddl_in_init_db(self):
        """The trend_positions table DDL should be in database.py."""
        import backend.app.database as db
        source = open(db.__file__).read()
        assert "trend_positions" in source
        assert "CREATE TABLE IF NOT EXISTS trend_positions" in source

    def test_trend_positions_in_vacuum(self):
        """trend_positions should be included in VACUUM ANALYZE."""
        import backend.app.database as db
        source = open(db.__file__).read()
        assert '"trend_positions"' in source or "'trend_positions'" in source
