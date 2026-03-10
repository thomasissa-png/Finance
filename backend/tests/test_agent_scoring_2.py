"""Tests for Agent Scoring 2 — Dedicated Claude scoring for Équipe 2 (v8.0).

Tests:
1. Core functions (persistence mult, pre-filter, score formula)
2. Category multipliers and structural keywords
3. AgentScoring2 class (init, run, metrics)
4. Claude integration (mock API call, tool schema, prompt)
5. Pipeline integration (registry, accumulation)
6. Cache (trend score cache)
7. Backward compatibility (scored_news kwarg)
"""

import time
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest

from backend.app.agents.agent_scoring_2 import (
    AgentScoring2,
    TREND_CATEGORY_MULTS,
    STRUCTURAL_KEYWORDS,
    TREND_TICKERS,
    TREND_TICKER_INFO,
    MIN_TREND_SCORE,
    CHAIN_DISCOUNT,
    _CATEGORY_PRIORITY,
    TREND_SYSTEM_PROMPT,
    TREND_SCORING_TOOL,
    TREND_NEWS_CATEGORIES,
    RELEVANT_CATEGORIES_KEYWORDS,
    _compute_persistence_mult,
    _is_trend_relevant,
    score_news_for_trend,
    get_trend_token_usage,
    _trend_score_cache,
    _get_trend_cache_key,
    _get_cached_trend_score,
    _set_cached_trend_score,
)
from backend.app.models import (
    Direction,
    NewsItem,
    ScoredNews,
    ScanType,
    ChainReaction,
)


def _make_news_item(title="Test commodity news", description="Test description",
                    source="test", source_weight=1.1, published=None,
                    news_zone="", related_tickers=None):
    """Helper to create a NewsItem for trend scoring tests."""
    return NewsItem(
        title=title,
        source=source,
        url="https://test.com",
        published=published or datetime.now(timezone.utc),
        source_weight=source_weight,
        description=description,
        news_zone=news_zone,
        related_tickers=related_tickers or [],
    )


def _make_scored_news(ticker, direction="LONG", surprise=80,
                      category="commodity", reliability=80, clarity=80,
                      magnitude=60, title="Test commodity news",
                      source_weight=1.1, published=None,
                      convergence_count=0, description="Test description"):
    """Helper to create ScoredNews (for backward compat tests)."""
    news = _make_news_item(title=title, description=description,
                           source_weight=source_weight, published=published)
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
        impacted_tickers=[ticker] if ticker else [],
        reasoning="Test reasoning",
        news_category=category,
        category_score_mult=1.5,
        convergence_count=convergence_count,
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
        mult = _compute_persistence_mult("Pure rumor nothing else", "")
        assert mult == 1.0

    def test_no_keyword_returns_1(self):
        mult = _compute_persistence_mult("Generic unrelated headline", "")
        assert mult == 1.0

    def test_best_keyword_wins(self):
        mult = _compute_persistence_mult("Drought and rumor about embargo", "")
        assert mult == 1.5

    def test_description_also_checked(self):
        mult = _compute_persistence_mult("Generic headline", "severe drought in region")
        assert mult == 1.5


# ── Pre-filter relevance ─────────────────────────────────────────────


class TestTrendRelevance:
    def test_commodity_category_always_relevant(self):
        assert _is_trend_relevant("Random", "", "commodity")

    def test_weather_category_always_relevant(self):
        assert _is_trend_relevant("Random", "", "weather")

    def test_supply_chain_category_always_relevant(self):
        assert _is_trend_relevant("Random", "", "supply_chain")

    def test_keyword_in_title(self):
        assert _is_trend_relevant("Drought hits Midwest wheat", "")

    def test_keyword_in_description(self):
        assert _is_trend_relevant("Report", "copper mine closure")

    def test_irrelevant_news_filtered(self):
        assert not _is_trend_relevant("Apple earnings beat estimates", "", "earnings")

    def test_keyword_case_insensitive(self):
        assert _is_trend_relevant("DROUGHT hits region", "")

    def test_ticker_keyword_matches(self):
        assert _is_trend_relevant("Coffee prices surge", "")

    def test_country_keyword_matches(self):
        assert _is_trend_relevant("Brazil crop outlook", "")


# ── Claude prompt and tool schema ─────────────────────────────────────


class TestTrendPrompt:
    def test_prompt_mentions_4_tickers(self):
        for ticker in TREND_TICKERS:
            assert ticker in TREND_SYSTEM_PROMPT

    def test_prompt_mentions_structural_impact(self):
        assert "structurel" in TREND_SYSTEM_PROMPT.lower() or "structural" in TREND_SYSTEM_PROMPT.lower()

    def test_prompt_no_transmission_delay(self):
        """Trend prompt should NOT mention transmission_delay as a scoring dimension."""
        assert "transmission_delay" not in TREND_SYSTEM_PROMPT

    def test_prompt_no_market_awareness(self):
        """Trend prompt should NOT mention market_awareness as a scoring dimension."""
        assert "market_awareness" not in TREND_SYSTEM_PROMPT

    def test_tool_schema_has_structural_impact(self):
        props = TREND_SCORING_TOOL["input_schema"]["properties"]["scores"]["items"]["properties"]
        assert "structural_impact" in props

    def test_tool_schema_has_persistence(self):
        props = TREND_SCORING_TOOL["input_schema"]["properties"]["scores"]["items"]["properties"]
        assert "persistence" in props

    def test_tool_schema_no_transmission_delay(self):
        props = TREND_SCORING_TOOL["input_schema"]["properties"]["scores"]["items"]["properties"]
        assert "transmission_delay" not in props

    def test_tool_schema_no_market_awareness(self):
        props = TREND_SCORING_TOOL["input_schema"]["properties"]["scores"]["items"]["properties"]
        assert "market_awareness" not in props

    def test_tool_name_is_submit_trend_scores(self):
        assert TREND_SCORING_TOOL["name"] == "submit_trend_scores"

    def test_trend_news_categories_subset(self):
        """Trend categories should be a subset of commodity-relevant categories."""
        assert "weather" in TREND_NEWS_CATEGORIES
        assert "supply_chain" in TREND_NEWS_CATEGORIES
        assert "commodity" in TREND_NEWS_CATEGORIES
        # earnings/macro/m_a should NOT be in trend categories
        assert "earnings" not in TREND_NEWS_CATEGORIES
        assert "macro" not in TREND_NEWS_CATEGORIES
        assert "m_a" not in TREND_NEWS_CATEGORIES


# ── score_news_for_trend() ────────────────────────────────────────────


class TestScoreNewsForTrend:
    def test_empty_list(self):
        result = score_news_for_trend([])
        assert result["trend_scored"] == []
        assert result["stats"]["total_items"] == 0
        assert result["stats"]["relevant_items"] == 0

    def test_irrelevant_news_filtered_before_claude(self):
        """News without commodity keywords should be pre-filtered (no Claude call)."""
        items = [
            _make_news_item(title="Apple Q4 earnings beat", description="Revenue up 8%"),
        ]
        with patch("backend.app.agents.agent_scoring_2._call_claude_for_trend") as mock_claude:
            mock_claude.return_value = []
            result = score_news_for_trend(items)
            # Irrelevant news should not be sent to Claude
            mock_claude.assert_not_called()

    def test_relevant_news_sent_to_claude(self):
        """News with commodity keywords should be sent to Claude."""
        items = [
            _make_news_item(title="Severe drought hits Midwest wheat belt",
                            description="USDA warns of crop damage"),
        ]
        with patch("backend.app.agents.agent_scoring_2._call_claude_for_trend") as mock_claude:
            mock_claude.return_value = [{
                "index": 1,
                "structural_impact": 80,
                "persistence": 70,
                "magnitude": 65,
                "reliability": 90,
                "directional_clarity": 85,
                "direction": "LONG",
                "impacted_tickers": ["ZW=F"],
                "news_category": "weather",
                "reasoning": "Drought during critical growth period",
            }]
            result = score_news_for_trend(items)
            mock_claude.assert_called_once()
            assert result["stats"]["relevant_items"] == 1
            assert result["accumulation"]["ZW=F"]["long"] > 0

    def test_neutral_scores_ignored(self):
        items = [
            _make_news_item(title="Copper market update",
                            description="No change in copper production"),
        ]
        with patch("backend.app.agents.agent_scoring_2._call_claude_for_trend") as mock_claude:
            mock_claude.return_value = [{
                "index": 1,
                "structural_impact": 0,
                "persistence": 0,
                "magnitude": 10,
                "reliability": 50,
                "directional_clarity": 20,
                "direction": "NEUTRAL",
                "impacted_tickers": [],
                "news_category": "commodity",
                "reasoning": "No significant impact",
            }]
            result = score_news_for_trend(items)
            assert result["stats"]["relevant_items"] == 0

    def test_non_trend_tickers_filtered(self):
        """If Claude returns non-trend tickers, they should be filtered out."""
        items = [
            _make_news_item(title="Oil embargo impacts supply chain",
                            description="OPEC sanctions"),
        ]
        with patch("backend.app.agents.agent_scoring_2._call_claude_for_trend") as mock_claude:
            mock_claude.return_value = [{
                "index": 1,
                "structural_impact": 70,
                "persistence": 60,
                "magnitude": 55,
                "reliability": 80,
                "directional_clarity": 75,
                "direction": "LONG",
                "impacted_tickers": ["CL=F", "BZ=F"],  # Not trend tickers
                "news_category": "supply_chain",
                "reasoning": "Oil supply disruption",
            }]
            result = score_news_for_trend(items)
            # CL=F and BZ=F are not in TREND_TICKERS → filtered
            assert result["stats"]["relevant_items"] == 0

    def test_all_tickers_initialized(self):
        result = score_news_for_trend([])
        for ticker in TREND_TICKERS:
            assert ticker in result["accumulation"]
            assert ticker in result["by_ticker"]

    def test_trend_score_formula(self):
        """Verify the trend score formula includes persistence_factor."""
        items = [
            _make_news_item(title="Drought copper mine closure",
                            description="Major copper mine shut down"),
        ]
        with patch("backend.app.agents.agent_scoring_2._call_claude_for_trend") as mock_claude:
            mock_claude.return_value = [{
                "index": 1,
                "structural_impact": 90,
                "persistence": 80,  # High persistence
                "magnitude": 70,
                "reliability": 90,
                "directional_clarity": 85,
                "direction": "LONG",
                "impacted_tickers": ["HG=F"],
                "news_category": "commodity",
                "reasoning": "Mine closure reduces copper supply",
            }]
            result = score_news_for_trend(items)
            assert result["stats"]["relevant_items"] == 1
            scored = result["trend_scored"][0]
            # Score should be > 0 and include persistence factor
            assert scored["trend_score"] > MIN_TREND_SCORE
            assert scored["structural_impact"] == 90
            assert scored["persistence"] == 80

    def test_accumulation_direction(self):
        """LONG news accumulates in 'long', SHORT in 'short'."""
        items = [
            _make_news_item(title="Wheat export ban by Russia",
                            description="Export restrictions on wheat"),
        ]
        with patch("backend.app.agents.agent_scoring_2._call_claude_for_trend") as mock_claude:
            mock_claude.return_value = [{
                "index": 1,
                "structural_impact": 80,
                "persistence": 70,
                "magnitude": 65,
                "reliability": 85,
                "directional_clarity": 90,
                "direction": "LONG",
                "impacted_tickers": ["ZW=F"],
                "news_category": "supply_chain",
                "reasoning": "Export ban reduces global supply",
            }]
            result = score_news_for_trend(items)
            assert result["accumulation"]["ZW=F"]["long"] > 0
            assert result["accumulation"]["ZW=F"]["short"] == 0


# ── Cache ─────────────────────────────────────────────────────────────


class TestTrendScoreCache:
    def setup_method(self):
        """Clear cache before each test."""
        _trend_score_cache.clear()

    def test_cache_set_and_get(self):
        key = _get_trend_cache_key("Test title", "Test desc")
        entry = {"direction": "LONG", "trend_score": 50.0, "impacted_tickers": ["HG=F"]}
        _set_cached_trend_score(key, entry)
        cached = _get_cached_trend_score(key)
        assert cached is not None
        assert cached["direction"] == "LONG"

    def test_cache_miss(self):
        key = _get_trend_cache_key("Non-existent", "")
        assert _get_cached_trend_score(key) is None

    def test_cache_key_includes_trend_prefix(self):
        key = _get_trend_cache_key("Title", "Desc")
        # Key is an MD5 hash, but the input includes "trend|" prefix
        # to avoid collision with Scoring 1 cache
        from hashlib import md5
        expected = md5(b"trend|Title|Desc").hexdigest()
        assert key == expected

    def test_cached_items_reused(self):
        """Cached items should bypass Claude call."""
        items = [
            _make_news_item(title="Drought copper mine", description="desc"),
        ]
        # Pre-populate cache
        cache_key = _get_trend_cache_key("Drought copper mine", "desc")
        _set_cached_trend_score(cache_key, {
            "direction": "LONG",
            "trend_score": 50.0,
            "impacted_tickers": ["HG=F"],
            "reliability": 80,
            "news_category": "weather",
        })

        with patch("backend.app.agents.agent_scoring_2._call_claude_for_trend") as mock_claude:
            mock_claude.return_value = []
            result = score_news_for_trend(items)
            # Claude should NOT be called (item was in cache)
            mock_claude.assert_not_called()
            assert result["stats"]["relevant_items"] == 1


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

    def test_accent_variants(self):
        assert "el nino" in STRUCTURAL_KEYWORDS
        assert STRUCTURAL_KEYWORDS["el nino"] == STRUCTURAL_KEYWORDS["el niño"]
        assert "la nina" in STRUCTURAL_KEYWORDS
        assert STRUCTURAL_KEYWORDS["la nina"] == STRUCTURAL_KEYWORDS["la niña"]
        assert "secheresse" in STRUCTURAL_KEYWORDS


# ── Trend tickers ───────────────────────────────────────────────────


class TestTrendTickers:
    def test_four_tickers(self):
        assert len(TREND_TICKERS) == 4

    def test_expected_tickers(self):
        assert "HG=F" in TREND_TICKERS
        assert "CC=F" in TREND_TICKERS
        assert "KC=F" in TREND_TICKERS
        assert "ZW=F" in TREND_TICKERS

    def test_ticker_info_matches(self):
        assert set(TREND_TICKER_INFO.keys()) == TREND_TICKERS


# ── AgentScoring2 class ─────────────────────────────────────────────


class TestAgentScoring2:
    def test_init(self):
        agent = AgentScoring2()
        assert agent.name == "scoring_2"
        assert agent.version == "8.0"

    def test_description_mentions_claude(self):
        agent = AgentScoring2()
        assert "claude" in agent.description.lower() or "dédié" in agent.description.lower()

    def test_metrics_initial(self):
        agent = AgentScoring2()
        m = agent.get_metrics()
        assert m["last_relevant_count"] == 0
        assert m["total_rescorings"] == 0
        assert "token_usage" in m

    def test_get_last_result_initial(self):
        agent = AgentScoring2()
        assert agent.get_last_result() is None

    def test_run_with_empty_list(self):
        agent = AgentScoring2()
        result = agent.run(news_items=[], scan_type=ScanType.EUROPE)
        assert result["stats"]["total_items"] == 0
        assert result["stats"]["relevant_items"] == 0
        assert agent.get_last_result() is not None
        assert agent.get_metrics()["total_rescorings"] == 1

    def test_run_backward_compat_scored_news(self):
        """v8.0 backward compat: scored_news kwarg should extract NewsItems."""
        agent = AgentScoring2()
        sn = _make_scored_news("HG=F", title="Apple earnings", category="earnings")
        result = agent.run(scored_news=[sn], scan_type=ScanType.EUROPE)
        # Should work without crashing, even if irrelevant
        assert result["stats"]["total_items"] == 1

    def test_run_with_relevant_news(self):
        agent = AgentScoring2()
        items = [
            _make_news_item(title="Severe drought hits copper mine region",
                            description="Major copper producer affected"),
        ]
        with patch("backend.app.agents.agent_scoring_2._call_claude_for_trend") as mock_claude:
            mock_claude.return_value = [{
                "index": 1,
                "structural_impact": 85,
                "persistence": 75,
                "magnitude": 70,
                "reliability": 90,
                "directional_clarity": 80,
                "direction": "LONG",
                "impacted_tickers": ["HG=F"],
                "news_category": "weather",
                "reasoning": "Drought reduces copper production",
            }]
            result = agent.run(news_items=items, scan_type=ScanType.EUROPE)
            assert result["stats"]["relevant_items"] == 1
            assert agent.get_metrics()["last_relevant_count"] == 1


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


# ── Token tracking ──────────────────────────────────────────────────


class TestTokenTracking:
    def test_initial_token_usage(self):
        usage = get_trend_token_usage()
        assert "input_tokens" in usage
        assert "output_tokens" in usage
        assert "scans" in usage

    def test_metrics_include_token_usage(self):
        agent = AgentScoring2()
        m = agent.get_metrics()
        assert "token_usage" in m
        assert "input_tokens" in m["token_usage"]
