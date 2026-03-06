"""Tests for the news scorer module (#40 — expanded)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from backend.app.news_scorer import (
    _build_context_string,
    _compute_freshness,
    _fetch_market_context,
    SCORING_TOOL,
)
from backend.app.models import ScanType


def test_freshness_very_recent():
    now = datetime.now(timezone.utc)
    assert _compute_freshness(now) == 100


def test_freshness_one_hour_ago():
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    assert _compute_freshness(one_hour_ago) == 100  # Within peak window


def test_freshness_four_hours_ago():
    four_hours = datetime.now(timezone.utc) - timedelta(hours=4)
    score = _compute_freshness(four_hours)
    # Between peak (2h) and max (8h): should be between 0 and 100
    assert 0 < score < 100


def test_freshness_too_old():
    old = datetime.now(timezone.utc) - timedelta(hours=9)
    assert _compute_freshness(old) == 5  # Floor at 5 for residual slow-transmission value (max age = 8h)


def test_freshness_none():
    assert _compute_freshness(None) == 50  # Unknown = neutral


def test_freshness_exactly_at_peak():
    at_peak = datetime.now(timezone.utc) - timedelta(hours=2)
    score = _compute_freshness(at_peak)
    assert score >= 99  # Allow tiny timing tolerance


def test_freshness_exactly_at_max():
    at_max = datetime.now(timezone.utc) - timedelta(hours=8)
    assert _compute_freshness(at_max) == 5  # Floor at 5 for residual slow-transmission value (max age = 8h)


def test_freshness_future_time():
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    assert _compute_freshness(future) == 100


def test_freshness_midpoint():
    """At 5h (midpoint between 2h peak and 8h max), score should be ~50."""
    midpoint = datetime.now(timezone.utc) - timedelta(hours=5)
    score = _compute_freshness(midpoint)
    assert 45 <= score <= 55  # Allow small tolerance


def test_freshness_linear_decay():
    """Score should decrease linearly between peak and max age."""
    score_3h = _compute_freshness(datetime.now(timezone.utc) - timedelta(hours=3))
    score_4h = _compute_freshness(datetime.now(timezone.utc) - timedelta(hours=4))
    score_5h = _compute_freshness(datetime.now(timezone.utc) - timedelta(hours=5))
    assert score_3h > score_4h > score_5h


def test_scoring_tool_schema():
    """Verify the tool_use schema includes edge-detection fields."""
    assert SCORING_TOOL["name"] == "submit_news_scores"
    schema = SCORING_TOOL["input_schema"]
    assert schema["type"] == "object"
    assert "scores" in schema["properties"]
    items_schema = schema["properties"]["scores"]["items"]
    assert "surprise" in items_schema["properties"]
    assert "directional_clarity" in items_schema["properties"]
    assert "transmission_delay" in items_schema["properties"]
    assert "market_awareness" in items_schema["properties"]
    assert "direction" in items_schema["properties"]
    assert items_schema["properties"]["direction"]["enum"] == ["LONG", "SHORT", "NEUTRAL"]


def test_build_context_string_europe():
    ctx = {"vix": 18.5, "regime": "normal", "indices": {"CAC40": 0.5}, "trends": {"CAC40": {"5d": "haussier", "20d": "neutre"}}}
    result = _build_context_string(ctx, ScanType.EUROPE)
    assert "EUROPE" in result
    assert "VIX: 18.5" in result
    assert "normal" in result
    assert "CAC40" in result
    assert "haussier" in result


def test_build_context_string_us():
    ctx = {"vix": 25.0, "regime": "elevated", "indices": {}, "trends": {}}
    result = _build_context_string(ctx, ScanType.US)
    assert "US" in result
    assert "VIX: 25.0" in result
    assert "elevated" in result


def test_build_context_string_no_data():
    ctx = {"vix": None, "regime": "normal", "indices": {}, "trends": {}}
    result = _build_context_string(ctx, ScanType.EUROPE)
    assert "EUROPE" in result
    assert "VIX" not in result  # No VIX data → not included


def test_scoring_tool_news_categories():
    """Verify tool schema includes all news categories including new ones."""
    items_schema = SCORING_TOOL["input_schema"]["properties"]["scores"]["items"]
    news_cat_enum = items_schema["properties"]["news_category"]["enum"]
    assert "earnings" in news_cat_enum
    assert "macro" in news_cat_enum
    assert "geopolitical" in news_cat_enum
    assert "weather" in news_cat_enum
    assert "supply_chain" in news_cat_enum
    assert "central_bank_subtle" in news_cat_enum
    assert "other" in news_cat_enum


# ── v4.0 tests: new scoring dimensions ─────────────────────


def test_scoring_tool_has_magnitude_and_reliability():
    """v4.0: Tool schema should include expected_magnitude and signal_reliability."""
    items_schema = SCORING_TOOL["input_schema"]["properties"]["scores"]["items"]
    assert "expected_magnitude" in items_schema["properties"]
    assert "signal_reliability" in items_schema["properties"]
    # Check they're required
    assert "expected_magnitude" in items_schema["required"]
    assert "signal_reliability" in items_schema["required"]
    # Check bounds
    assert items_schema["properties"]["expected_magnitude"]["minimum"] == 0
    assert items_schema["properties"]["expected_magnitude"]["maximum"] == 100
    assert items_schema["properties"]["signal_reliability"]["minimum"] == 0
    assert items_schema["properties"]["signal_reliability"]["maximum"] == 100


def test_dynamic_asset_count_in_user_message():
    """v4.3 B5: Asset count moved to user message, uses dynamic len(ASSETS)."""
    from backend.app.news_scorer import TICKER_LIST
    from backend.app.config import ASSETS
    # TICKER_LIST is built dynamically from ASSETS
    assert len(TICKER_LIST) > 0
    # Should contain at least some known tickers
    assert "CL=F" in TICKER_LIST or "GC=F" in TICKER_LIST
    # Asset count should match config
    assert TICKER_LIST.count("(") == len(ASSETS)  # Each asset has (name)


def test_system_prompt_has_magnitude_instructions():
    """v4.0 B1: System prompt should include expected_magnitude scoring guidance."""
    from backend.app.news_scorer import SYSTEM_PROMPT
    assert "expected_magnitude" in SYSTEM_PROMPT
    assert "signal_reliability" in SYSTEM_PROMPT


def test_convergence_requires_direction_param():
    """v4.0 A2: _count_convergence should accept scored_directions for direction validation."""
    from backend.app.news_scorer import _count_convergence
    from backend.app.models import Direction, NewsItem
    import inspect
    sig = inspect.signature(_count_convergence)
    assert "all_scored_directions" in sig.parameters


# ── v4.3 tests: Claude audit improvements ─────────────────────


def test_prompt_version_hash():
    """v4.3: PROMPT_VERSION should be a short hash of SYSTEM_PROMPT."""
    from backend.app.news_scorer import PROMPT_VERSION, SYSTEM_PROMPT
    import hashlib
    expected = hashlib.md5(SYSTEM_PROMPT.encode()).hexdigest()[:8]
    assert PROMPT_VERSION == expected
    assert len(PROMPT_VERSION) == 8


def test_default_model_is_haiku():
    """v4.3 A1/F1: Default model should be Haiku for cost efficiency."""
    from backend.app.news_scorer import DEFAULT_MODEL
    assert "haiku" in DEFAULT_MODEL.lower()


def test_get_model_default():
    """v4.3 A1: _get_model() returns DEFAULT_MODEL when no env var set."""
    from backend.app.news_scorer import _get_model, DEFAULT_MODEL
    import os
    # Remove env var if set
    old = os.environ.pop("CLAUDE_MODEL", None)
    try:
        assert _get_model() == DEFAULT_MODEL
    finally:
        if old is not None:
            os.environ["CLAUDE_MODEL"] = old


def test_get_model_env_override():
    """v4.3 A1: _get_model() respects CLAUDE_MODEL env var."""
    from backend.app.news_scorer import _get_model
    import os
    old = os.environ.get("CLAUDE_MODEL")
    os.environ["CLAUDE_MODEL"] = "claude-sonnet-4-5-20250929"
    try:
        assert _get_model() == "claude-sonnet-4-5-20250929"
    finally:
        if old is None:
            del os.environ["CLAUDE_MODEL"]
        else:
            os.environ["CLAUDE_MODEL"] = old


def test_scoring_tool_has_confirmed_event():
    """v4.3 D3: Tool schema should have confirmed_event boolean field."""
    items_schema = SCORING_TOOL["input_schema"]["properties"]["scores"]["items"]
    assert "confirmed_event" in items_schema["properties"]
    assert items_schema["properties"]["confirmed_event"]["type"] == "boolean"
    assert "confirmed_event" in items_schema["required"]


def test_scoring_tool_has_perceived_age():
    """v4.3 C1: Tool schema should have optional perceived_age_hours field."""
    items_schema = SCORING_TOOL["input_schema"]["properties"]["scores"]["items"]
    assert "perceived_age_hours" in items_schema["properties"]
    assert items_schema["properties"]["perceived_age_hours"]["type"] == "number"
    # perceived_age_hours should NOT be required (optional diagnostic)
    assert "perceived_age_hours" not in items_schema["required"]


def test_scoring_tool_reasoning_max_length():
    """v4.3 C2: reasoning field should have maxLength constraint."""
    items_schema = SCORING_TOOL["input_schema"]["properties"]["scores"]["items"]
    assert "maxLength" in items_schema["properties"]["reasoning"]
    assert items_schema["properties"]["reasoning"]["maxLength"] == 300


def test_zero_edge_headline_detection():
    """v4.3 D1: Pre-filter zero-edge headlines before Claude API."""
    from backend.app.news_scorer import _is_zero_edge_headline
    # Earnings → should be detected
    assert _is_zero_edge_headline("Apple reports Q4 earnings report beating estimates") == "earnings"
    assert _is_zero_edge_headline("Tesla quarterly results show profit rises") == "earnings"
    # Macro → should be detected
    assert _is_zero_edge_headline("US nonfarm payroll data exceeds expectations") == "macro"
    assert _is_zero_edge_headline("CPI data shows inflation at 3.2%") == "macro"
    # Weather/commodity → should NOT be filtered
    assert _is_zero_edge_headline("Frost warning in Brazil coffee regions") is None
    assert _is_zero_edge_headline("Oil prices surge on supply disruption") is None


def test_validate_coherence_logs_warnings():
    """v4.3 D2: Coherence validation should detect contradictions."""
    from backend.app.news_scorer import _validate_coherence
    import logging
    # This should log a warning: high surprise + low delay
    with patch("backend.app.news_scorer.logger") as mock_logger:
        _validate_coherence({}, transmission_delay=10, market_awareness=50,
                          surprise=85, title="Test headline")
        mock_logger.warning.assert_called()
    # Low awareness + low delay
    with patch("backend.app.news_scorer.logger") as mock_logger:
        _validate_coherence({}, transmission_delay=10, market_awareness=10,
                          surprise=30, title="Test headline")
        mock_logger.warning.assert_called()
    # High awareness + high delay
    with patch("backend.app.news_scorer.logger") as mock_logger:
        _validate_coherence({}, transmission_delay=70, market_awareness=90,
                          surprise=30, title="Test headline")
        mock_logger.warning.assert_called()


def test_validate_coherence_no_warning_for_consistent():
    """v4.3 D2: No warnings for consistent dimensions."""
    from backend.app.news_scorer import _validate_coherence
    # Consistent: high surprise, high delay, low awareness
    with patch("backend.app.news_scorer.logger") as mock_logger:
        _validate_coherence({}, transmission_delay=80, market_awareness=10,
                          surprise=85, title="Consistent headline")
        mock_logger.warning.assert_not_called()


def test_score_cache_operations():
    """v4.3 B6: Score cache set/get/TTL operations."""
    from backend.app.news_scorer import (
        _get_cache_key, _get_cached_score, _set_cached_score, _score_cache,
    )
    key = _get_cache_key("Test headline", "Test description")
    assert isinstance(key, str)
    assert len(key) > 0
    # Set and get
    test_score = {"surprise": 80, "score": 50.0}
    _set_cached_score(key, test_score)
    cached = _get_cached_score(key)
    assert cached == test_score
    # Clean up
    _score_cache.pop(key, None)


def test_get_token_usage():
    """v4.3 F4: Token usage tracking function."""
    from backend.app.news_scorer import get_token_usage
    usage = get_token_usage()
    assert isinstance(usage, dict)
    assert "input_tokens" in usage
    assert "output_tokens" in usage
    assert "scans" in usage


def test_system_prompt_xml_structure():
    """v4.3 B3: System prompt should use XML-structured sections."""
    from backend.app.news_scorer import SYSTEM_PROMPT
    assert "<role>" in SYSTEM_PROMPT
    assert "</role>" in SYSTEM_PROMPT
    assert "<scoring_dimensions>" in SYSTEM_PROMPT
    assert "</scoring_dimensions>" in SYSTEM_PROMPT
    assert "<hard_rules>" in SYSTEM_PROMPT
    assert "</hard_rules>" in SYSTEM_PROMPT
    assert "<examples>" in SYSTEM_PROMPT
    assert "</examples>" in SYSTEM_PROMPT


def test_system_prompt_has_few_shot_examples():
    """v4.3 B5: System prompt should contain few-shot examples."""
    from backend.app.news_scorer import SYSTEM_PROMPT
    # Should have weather, earnings, and geopolitical examples
    assert "NOAA" in SYSTEM_PROMPT or "secheresse" in SYSTEM_PROMPT
    assert "earnings" in SYSTEM_PROMPT.lower()
    assert "Iranian tankers" in SYSTEM_PROMPT or "geopolitical" in SYSTEM_PROMPT.lower()


def test_build_system_messages_has_cache_control():
    """v4.3 F3: System messages should include cache_control for prompt caching."""
    from backend.app.news_scorer import _build_system_messages
    messages = _build_system_messages()
    assert isinstance(messages, list)
    assert len(messages) >= 1
    # At least one block should have cache_control
    has_cache = any("cache_control" in m for m in messages)
    assert has_cache, "System messages should have cache_control for prompt caching"
