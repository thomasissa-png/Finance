"""Tests for Agent Scoring 2 — Trend-specific re-scoring for Équipe 2.

Tests:
1. Core functions (persistence mult, trend score, score_for_trend)
2. Category multipliers and structural keywords
3. AgentScoring2 class (init, run, metrics)
4. Pipeline integration (registry, accumulation)
5. Filtering (NEUTRAL skipped, non-trend tickers skipped, min threshold)
"""

import time
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from backend.app.agents.agent_scoring_2 import (
    AgentScoring2,
    TREND_CATEGORY_MULTS,
    STRUCTURAL_KEYWORDS,
    TREND_TICKERS,
    _compute_persistence_mult,
    _compute_trend_score,
    score_for_trend,
)
from backend.app.models import (
    Direction,
    NewsItem,
    ScoredNews,
    ScanType,
    ChainReaction,
)


def _make_scored_news(ticker, direction="LONG", surprise=80,
                      category="commodity", reliability=80, clarity=80,
                      magnitude=60, title="Test commodity news",
                      source_weight=1.1, chain_ticker=None,
                      chain_direction="LONG"):
    """Helper to create a ScoredNews for trend scoring tests."""
    news = NewsItem(
        title=title,
        source="test",
        url="https://test.com",
        published=datetime.now(timezone.utc),
        source_weight=source_weight,
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
        surprise=surprise,
        freshness=90,
        directional_clarity=clarity,
        transmission_delay=80,
        market_awareness=10,
        expected_magnitude=magnitude,
        signal_reliability=reliability,
        direction=Direction(direction),
        impacted_tickers=impacted,
        reasoning="Test reasoning",
        news_category=category,
        category_score_mult=1.5,
        chain_reactions=chains,
    )


# ── Persistence multiplier ──────────────────────────────────────────


class TestPersistenceMult:
    def test_structural_keyword_drought(self):
        mult = _compute_persistence_mult("Severe drought hits Midwest", "")
        assert mult == 1.5

    def test_structural_keyword_embargo(self):
        mult = _compute_persistence_mult("Russia embargo on wheat exports", "")
        assert mult == 1.5

    def test_structural_keyword_frost(self):
        mult = _compute_persistence_mult("Frost damages coffee crops in Brazil", "")
        assert mult == 1.5

    def test_temporary_event_low_mult(self):
        # "rumor" keyword has mult 0.7, but best_mult starts at 1.0
        # so if "production" also matches (via "production cut"=1.3), it wins
        # Use a title with ONLY a low-persistence keyword
        mult = _compute_persistence_mult("Pure rumor nothing else", "")
        # best_mult = max(1.0, 0.7) = 1.0 — low keywords don't reduce below 1.0
        assert mult == 1.0

    def test_no_keyword_returns_1(self):
        mult = _compute_persistence_mult("Generic unrelated headline", "")
        assert mult == 1.0

    def test_best_keyword_wins(self):
        """If multiple keywords match, the highest mult wins."""
        mult = _compute_persistence_mult("Drought and rumor about embargo", "")
        # drought=1.5, rumor=0.7, embargo=1.5 → best = 1.5
        assert mult == 1.5

    def test_description_also_checked(self):
        mult = _compute_persistence_mult("Generic headline", "severe drought in region")
        assert mult == 1.5


# ── Trend score formula ─────────────────────────────────────────────


class TestTrendScore:
    def test_basic_score_positive(self):
        sn = _make_scored_news("HG=F", surprise=80, clarity=80,
                               magnitude=60, reliability=80)
        score = _compute_trend_score(sn, 1.6, 1.0)
        assert score > 0

    def test_higher_magnitude_higher_score(self):
        sn_low = _make_scored_news("HG=F", magnitude=20)
        sn_high = _make_scored_news("HG=F", magnitude=90)
        score_low = _compute_trend_score(sn_low, 1.0, 1.0)
        score_high = _compute_trend_score(sn_high, 1.0, 1.0)
        assert score_high > score_low

    def test_category_mult_scales_score(self):
        sn = _make_scored_news("HG=F")
        score_weather = _compute_trend_score(sn, 2.0, 1.0)
        score_macro = _compute_trend_score(sn, 0.3, 1.0)
        assert score_weather > score_macro
        assert abs(score_weather / score_macro - 2.0/0.3) < 0.1

    def test_persistence_mult_scales_score(self):
        sn = _make_scored_news("HG=F")
        score_base = _compute_trend_score(sn, 1.0, 1.0)
        score_persist = _compute_trend_score(sn, 1.0, 1.5)
        assert score_persist == pytest.approx(score_base * 1.5, rel=0.01)

    def test_source_weight_matters(self):
        sn_high = _make_scored_news("HG=F", source_weight=1.2)
        sn_low = _make_scored_news("HG=F", source_weight=0.7)
        score_high = _compute_trend_score(sn_high, 1.0, 1.0)
        score_low = _compute_trend_score(sn_low, 1.0, 1.0)
        assert score_high > score_low


# ── score_for_trend() ────────────────────────────────────────────────


class TestScoreForTrend:
    def test_empty_list(self):
        result = score_for_trend([])
        assert result["trend_scored"] == []
        assert result["stats"]["total_items"] == 0
        assert result["stats"]["relevant_items"] == 0

    def test_neutral_skipped(self):
        sn = _make_scored_news("HG=F", direction="NEUTRAL")
        result = score_for_trend([sn])
        assert result["stats"]["relevant_items"] == 0

    def test_non_trend_ticker_skipped(self):
        sn = _make_scored_news("TTE.PA", direction="LONG")
        result = score_for_trend([sn])
        assert result["stats"]["relevant_items"] == 0

    def test_trend_ticker_included(self):
        sn = _make_scored_news("HG=F", direction="LONG", surprise=90,
                               category="weather", title="Drought copper mine")
        result = score_for_trend([sn])
        assert result["stats"]["relevant_items"] >= 1
        assert "HG=F" in result["by_ticker"]

    def test_accumulation_long(self):
        sn = _make_scored_news("CC=F", direction="LONG", surprise=90,
                               category="weather", title="Drought in Ivory Coast")
        result = score_for_trend([sn])
        acc = result["accumulation"].get("CC=F", {})
        assert acc.get("long", 0) > 0
        assert acc.get("short", 0) == 0

    def test_accumulation_short(self):
        sn = _make_scored_news("ZW=F", direction="SHORT", surprise=90,
                               category="commodity", title="Wheat surplus record harvest")
        result = score_for_trend([sn])
        acc = result["accumulation"].get("ZW=F", {})
        assert acc.get("short", 0) > 0

    def test_chain_reaction_ticker(self):
        """A news impacting CL=F with chain to HG=F should appear in HG=F."""
        sn = _make_scored_news("CL=F", direction="LONG", surprise=90,
                               category="supply_chain", title="Embargo oil",
                               chain_ticker="HG=F", chain_direction="SHORT")
        result = score_for_trend([sn])
        # CL=F is not a trend ticker, but HG=F is via chain reaction
        assert len(result["by_ticker"].get("HG=F", [])) >= 1

    def test_min_threshold_filter(self):
        """Very low score should be filtered (< 5.0)."""
        sn = _make_scored_news("HG=F", direction="LONG", surprise=5,
                               clarity=10, magnitude=5, reliability=10,
                               category="earnings", source_weight=0.5,
                               title="Generic low relevance")
        result = score_for_trend([sn])
        assert result["stats"]["relevant_items"] == 0

    def test_stats_top_category(self):
        items = [
            _make_scored_news("HG=F", category="weather", surprise=90,
                              title="Drought copper region"),
            _make_scored_news("CC=F", category="weather", surprise=85,
                              title="Frost cocoa Ivory Coast"),
            _make_scored_news("KC=F", category="commodity", surprise=90,
                              title="Coffee surplus Brazil"),
        ]
        result = score_for_trend(items)
        if result["stats"]["relevant_items"] >= 2:
            assert result["stats"]["top_category"] in ("weather", "commodity")

    def test_all_trend_tickers_initialized(self):
        result = score_for_trend([_make_scored_news("HG=F", surprise=90,
                                                     title="Drought mine")])
        for ticker in TREND_TICKERS:
            assert ticker in result["accumulation"]
            assert ticker in result["by_ticker"]


# ── Category multipliers ────────────────────────────────────────────


class TestCategoryMultipliers:
    def test_weather_is_highest(self):
        assert TREND_CATEGORY_MULTS["weather"] == 2.0
        for cat, mult in TREND_CATEGORY_MULTS.items():
            assert mult <= TREND_CATEGORY_MULTS["weather"]

    def test_earnings_lowest(self):
        assert TREND_CATEGORY_MULTS["earnings"] == 0.1

    def test_supply_chain_high(self):
        assert TREND_CATEGORY_MULTS["supply_chain"] == 1.8

    def test_commodity_above_1(self):
        assert TREND_CATEGORY_MULTS["commodity"] > 1.0


# ── Structural keywords ─────────────────────────────────────────────


class TestStructuralKeywords:
    def test_drought_high_persistence(self):
        assert STRUCTURAL_KEYWORDS["drought"] >= 1.4

    def test_rumor_low_persistence(self):
        assert STRUCTURAL_KEYWORDS["rumor"] < 1.0

    def test_export_ban_highest(self):
        assert STRUCTURAL_KEYWORDS["export ban"] == 1.6


# ── Trend tickers ───────────────────────────────────────────────────


class TestTrendTickers:
    def test_four_tickers(self):
        assert len(TREND_TICKERS) == 4

    def test_expected_tickers(self):
        assert "HG=F" in TREND_TICKERS
        assert "CC=F" in TREND_TICKERS
        assert "KC=F" in TREND_TICKERS
        assert "ZW=F" in TREND_TICKERS


# ── AgentScoring2 class ─────────────────────────────────────────────


class TestAgentScoring2:
    def test_init(self):
        agent = AgentScoring2()
        assert agent.name == "scoring_2"
        assert "trend" in agent.description.lower() or "tendance" in agent.description.lower()

    def test_metrics_initial(self):
        agent = AgentScoring2()
        m = agent.get_metrics()
        assert m["last_relevant_count"] == 0
        assert m["total_rescorings"] == 0

    def test_get_last_result_initial(self):
        agent = AgentScoring2()
        assert agent.get_last_result() is None

    def test_run_with_empty_list(self):
        agent = AgentScoring2()
        result = agent.run(scored_news=[], scan_type=ScanType.EUROPE)
        assert result["stats"]["total_items"] == 0
        assert result["stats"]["relevant_items"] == 0
        assert agent.get_last_result() is not None
        assert agent.get_metrics()["total_rescorings"] == 1

    def test_run_with_trend_news(self):
        agent = AgentScoring2()
        news_list = [
            _make_scored_news("HG=F", direction="LONG", surprise=90,
                              category="weather", title="Drought copper mine"),
        ]
        result = agent.run(scored_news=news_list, scan_type=ScanType.EUROPE)
        assert result["stats"]["total_items"] == 1
        assert result["stats"]["relevant_items"] >= 1
        assert agent.get_metrics()["last_relevant_count"] >= 1

    def test_run_updates_accumulation_metrics(self):
        agent = AgentScoring2()
        news_list = [
            _make_scored_news("CC=F", direction="SHORT", surprise=85,
                              category="commodity", title="Cocoa surplus export ban"),
        ]
        result = agent.run(scored_news=news_list, scan_type=ScanType.US)
        metrics = agent.get_metrics()
        assert "accumulation" in metrics


# ── Registry integration ────────────────────────────────────────────


class TestRegistryIntegration:
    def test_scoring_2_in_registry(self):
        from backend.app.agents.registry import get_agent
        agent = get_agent("scoring_2")
        assert agent is not None
        assert isinstance(agent, AgentScoring2)

    def test_scoring_2_has_status(self):
        from backend.app.agents.registry import get_all_status
        statuses = get_all_status()
        names = [s["name"] for s in statuses]
        assert "scoring_2" in names
