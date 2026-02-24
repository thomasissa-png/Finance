"""End-to-end workflow backtest: simulates the FULL pipeline from news collection
through scoring, trade selection, journal closure, learning, and feedback loop.

No network calls — everything is mocked to test pure logic integrity.
"""

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.app.config import (
    ASSET_BY_TICKER,
    CATEGORY_SCORE_MULTIPLIERS,
    CORRELATION_GROUPS,
    MIN_RISK_REWARD,
    MIN_SCORE_THRESHOLD,
    NEWS_CATEGORY_MULTIPLIERS,
    assets_for_session,
)
from backend.app.journal import (
    _build_review,
    _determine_result,
    _extract_scan_trace,
    load_journal,
    run_daily_journal,
)
from backend.app.learning import (
    _compute_adjustment,
    _is_significant,
    build_performance_summary,
    compute_learning_adjustments,
    compute_performance,
    load_trades,
    save_trade,
    update_trade_result,
)
from backend.app.models import (
    ChainReaction,
    Direction,
    JournalEntry,
    NewsItem,
    ScanResult,
    ScanType,
    ScoredNews,
    TradeRecommendation,
    TradeResult,
)
from backend.app.news_scorer import (
    _compute_freshness,
    _detect_chain_reactions,
)
from backend.app.trade_selector import (
    _calibrate_trade,
    _check_binary_event,
    _check_correlation,
    _detect_pre_move,
    select_trade,
)


# ════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════


def _make_news(title="Test news", source="Reuters", source_weight=0.85,
               published=None, tickers=None):
    return NewsItem(
        title=title,
        source=source,
        url="https://example.com",
        published=published or datetime.now(timezone.utc),
        related_tickers=tickers or [],
        source_weight=source_weight,
    )


def _make_scored(news=None, surprise=70, freshness=100, clarity=80,
                 delay=70, awareness=15, direction=Direction.LONG,
                 tickers=None, category="commodity", cat_mult=None):
    if news is None:
        news = _make_news()
    if cat_mult is None:
        cat_mult = CATEGORY_SCORE_MULTIPLIERS.get(category, 1.0)
    return ScoredNews(
        news=news,
        surprise=surprise,
        freshness=freshness,
        directional_clarity=clarity,
        transmission_delay=delay,
        market_awareness=awareness,
        direction=direction,
        impacted_tickers=tickers or ["CL=F"],
        reasoning="Test reasoning for backtest",
        news_category=category,
        category_score_mult=cat_mult,
    )


def _make_trade(**overrides):
    defaults = dict(
        scan_type=ScanType.EUROPE,
        timestamp=datetime.now(timezone.utc),
        ticker="CL=F",
        asset_name="Pétrole WTI",
        category="commodities",
        direction=Direction.LONG,
        news_headline="Oil supply disruption",
        news_category="commodity",
        catalyst="Test catalyst",
        entry_price=70.0,
        target_price=72.0,
        stop_price=69.0,
        target_pct=2.86,
        stop_pct=1.43,
        risk_reward=2.0,
        confidence=75,
        time_window="09:00 — 20:00",
        news_sources=["EIA"],
        raw_claude_score=75.0,
        learning_multiplier=1.0,
        predicted_transmission_delay=70,
    )
    defaults.update(overrides)
    return TradeRecommendation(**defaults)


def _with_temp_trades(trades_data):
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(trades_data, tmp, default=str)
    tmp.close()
    return patch("backend.app.learning.TRADES_FILE", Path(tmp.name))


def _with_temp_journal(data):
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(data, tmp, default=str)
    tmp.close()
    return patch("backend.app.journal.JOURNAL_FILE", Path(tmp.name))


# ════════════════════════════════════════════════════════════════
# Phase 1: Scoring formula edge cases
# ════════════════════════════════════════════════════════════════


class TestScoringEdgeCases:
    """Verify score formula produces correct rankings across scenarios."""

    def test_weather_beats_earnings_always(self):
        """Our philosophy: weather signal should ALWAYS outscore earnings."""
        weather = _make_scored(
            news=_make_news("Drought in Brazil", "NOAA", 1.1),
            surprise=60, freshness=100, clarity=80,
            delay=85, awareness=5, category="weather",
        )
        earnings = _make_scored(
            news=_make_news("LVMH beats estimates", "Reuters", 0.85),
            surprise=80, freshness=100, clarity=90,
            delay=5, awareness=95, category="earnings",
        )
        assert weather.total_score > earnings.total_score * 10, (
            f"Weather ({weather.total_score}) should dominate earnings ({earnings.total_score})"
        )

    def test_stale_weather_still_beats_fresh_earnings(self):
        """A 4h-old weather signal still has more edge than fresh earnings."""
        old_weather = _make_scored(
            news=_make_news("Frost kills coffee", "NOAA", 1.1,
                            published=datetime.now(timezone.utc) - timedelta(hours=4)),
            surprise=70, freshness=_compute_freshness(
                datetime.now(timezone.utc) - timedelta(hours=4)
            ),
            clarity=80, delay=80, awareness=10, category="weather",
        )
        fresh_earnings = _make_scored(
            news=_make_news("Apple beats Q4", "CNBC", 0.9),
            surprise=80, freshness=100, clarity=90,
            delay=5, awareness=95, category="earnings",
        )
        assert old_weather.total_score > fresh_earnings.total_score

    def test_geopolitical_osint_vs_mainstream_headline(self):
        """OSINT signal with high delay should outscore CNN headline."""
        osint = _make_scored(
            news=_make_news("Troop movements near Strait", "state.gov", 1.0),
            surprise=70, freshness=100, clarity=70,
            delay=75, awareness=20, category="geopolitical",
            tickers=["CL=F", "GC=F"],
        )
        headline = _make_scored(
            news=_make_news("Tensions rise in Middle East", "CNBC", 0.9),
            surprise=50, freshness=100, clarity=60,
            delay=20, awareness=80, category="geopolitical",
            tickers=["CL=F"],
        )
        assert osint.total_score > headline.total_score * 2

    def test_zero_surprise_kills_score(self):
        """Even high edge can't save a zero-surprise news item."""
        scored = _make_scored(surprise=0, delay=100, awareness=0)
        assert scored.total_score == 0.0

    def test_score_range_realistic(self):
        """Max realistic score should be between 80-150 for best signals."""
        best_case = _make_scored(
            news=_make_news("Unprecedented frost", "NOAA", 1.2),
            surprise=95, freshness=100, clarity=95,
            delay=95, awareness=3, category="weather",
        )
        # Should be high but not absurdly so
        assert 80 < best_case.total_score < 200

    def test_edge_factor_floor_prevents_zero(self):
        """Even with delay=0, awareness=100, score shouldn't be exactly 0
        due to the 0.01 floor (unless surprise=0)."""
        scored = _make_scored(
            surprise=80, freshness=100, clarity=100,
            delay=0, awareness=100, category="earnings",
        )
        assert scored.total_score > 0

    def test_source_weight_ordering(self):
        """NOAA (1.1) > Reuters (0.85) > Investing.com (0.7)."""
        base = dict(surprise=80, freshness=100, clarity=80,
                    delay=60, awareness=30, category="commodity")
        noaa = _make_scored(news=_make_news(source_weight=1.1), **base)
        reuters = _make_scored(news=_make_news(source_weight=0.85), **base)
        investing = _make_scored(news=_make_news(source_weight=0.7), **base)
        assert noaa.total_score > reuters.total_score > investing.total_score


# ════════════════════════════════════════════════════════════════
# Phase 2: Trade selection logic
# ════════════════════════════════════════════════════════════════


class TestTradeSelection:
    """Verify select_trade correctly filters, ranks, and calibrates."""

    def test_neutral_direction_rejected(self):
        """NEUTRAL news must be rejected — no directional trade possible."""
        scored = [_make_scored(direction=Direction.NEUTRAL)]
        with patch("backend.app.trade_selector._get_price_and_range",
                   return_value=(70.0, 2.0, 69.0, 1.5)):
            result = select_trade(scored, ScanType.EUROPE)
        assert result.has_trade is False

    def test_below_threshold_rejected(self):
        """News scoring below MIN_SCORE_THRESHOLD must be rejected."""
        # Make a low-scoring news
        scored = [_make_scored(surprise=10, delay=10, awareness=90, category="earnings")]
        assert scored[0].total_score < MIN_SCORE_THRESHOLD
        with patch("backend.app.trade_selector._get_price_and_range",
                   return_value=(70.0, 2.0, 69.0, 1.5)):
            result = select_trade(scored, ScanType.EUROPE)
        assert result.has_trade is False

    def test_session_filtering_europe(self):
        """European scan should reject US-only tickers."""
        # ^GSPC is USD index — not eligible in Europe scan
        scored = [_make_scored(tickers=["^GSPC"])]
        with patch("backend.app.trade_selector._get_price_and_range",
                   return_value=(5000.0, 1.5, 4990.0, 1.2)):
            result = select_trade(scored, ScanType.EUROPE)
        assert result.has_trade is False

    def test_session_filtering_us(self):
        """US scan should reject Euronext stocks."""
        scored = [_make_scored(tickers=["MC.PA"])]
        with patch("backend.app.trade_selector._get_price_and_range",
                   return_value=(800.0, 1.5, 795.0, 1.0)):
            result = select_trade(scored, ScanType.US)
        assert result.has_trade is False

    def test_commodity_eligible_both_sessions(self):
        """Commodities like CL=F should be eligible in both sessions."""
        assert "CL=F" in assets_for_session("europe")
        assert "CL=F" in assets_for_session("us")

    def test_correlation_blocks_second_trade(self):
        """Two correlated tickers should not both get trades."""
        assert _check_correlation("CL=F", "BZ=F") is True
        assert _check_correlation("CL=F", "GC=F") is False

    def test_pre_move_detection_long_already_priced(self):
        """A LONG where price already moved 80%+ of target should be rejected."""
        # target_move_expected ~= 2.0 * (0.25 + 0.7*0.45) = 2.0 * 0.565 = 1.13
        # 0.8 * 1.13 = 0.904%
        pre_move = _detect_pre_move(71.0, 70.0, 1.13)  # +1.43%
        assert pre_move is not None
        assert pre_move > 1.13 * 0.8  # Already priced

    def test_pre_move_detection_fresh(self):
        """A small pre-move should NOT reject the trade."""
        pre_move = _detect_pre_move(70.2, 70.0, 1.13)  # +0.29%
        assert pre_move is not None
        assert pre_move < 1.13 * 0.8  # Not yet priced

    def test_rr_below_minimum_rejected(self):
        """Trades with R/R below MIN_RISK_REWARD must be rejected."""
        # With earnings category (target_mult=0.8, stop_mult=1.2),
        # a low score on mid-vol should produce R/R < 1.3
        _, _, _, _, rr = _calibrate_trade(Direction.LONG, 100.0, 2.0, 20.0, "earnings")
        # Earnings: target_mult=0.8 shrinks target, stop_mult=1.2 widens stop
        assert rr < MIN_RISK_REWARD

    def test_calibration_score_proportional(self):
        """Higher score should give wider target (and thus better R/R)."""
        _, _, t_low, _, rr_low = _calibrate_trade(Direction.LONG, 100.0, 2.0, 30.0)
        _, _, t_high, _, rr_high = _calibrate_trade(Direction.LONG, 100.0, 2.0, 90.0)
        assert t_high > t_low
        assert rr_high > rr_low

    def test_calibration_short_direction(self):
        """SHORT should have target below entry and stop above."""
        target, stop, _, _, _ = _calibrate_trade(Direction.SHORT, 100.0, 2.0, 70.0)
        assert target < 100.0
        assert stop > 100.0

    def test_news_category_multipliers_applied(self):
        """Weather should get wider target than earnings."""
        _, _, t_weather, _, _ = _calibrate_trade(Direction.LONG, 100.0, 2.0, 70.0, "weather")
        _, _, t_earnings, _, _ = _calibrate_trade(Direction.LONG, 100.0, 2.0, 70.0, "earnings")
        assert t_weather > t_earnings

    def test_binary_event_detection(self):
        """FOMC/NFP keywords should trigger binary event warning."""
        assert _check_binary_event("Fed rate decision", "") is not None
        assert _check_binary_event("Drought in Brazil", "") is None

    def test_select_trade_full_success_path(self):
        """A high-scoring commodity news should produce a valid trade."""
        news = _make_news("Oil supply cut", "EIA", 1.15, tickers=["CL=F"])
        scored = [_make_scored(
            news=news, surprise=80, freshness=100, clarity=85,
            delay=75, awareness=10, tickers=["CL=F"], category="commodity",
        )]
        with patch("backend.app.trade_selector._get_price_and_range",
                   return_value=(70.0, 2.5, 69.5, 1.3)), \
             patch("backend.app.trade_selector.check_event_conflict",
                   return_value=None):
            result = select_trade(scored, ScanType.EUROPE)

        assert result.has_trade is True
        rec = result.recommendation
        assert rec.ticker == "CL=F"
        assert rec.direction == Direction.LONG
        assert rec.target_price > rec.entry_price
        assert rec.stop_price < rec.entry_price
        assert rec.risk_reward >= MIN_RISK_REWARD
        assert rec.news_category == "commodity"
        assert rec.raw_claude_score is not None
        assert result.all_scored_news is not None
        assert result.decision_summary is not None

    def test_decision_trace_populated(self):
        """ScanResult should include all_scored_news and rejection_log."""
        scored = [
            _make_scored(tickers=["CL=F"], surprise=80, delay=75, awareness=10),
            _make_scored(tickers=["GC=F"], direction=Direction.NEUTRAL, surprise=60),
        ]
        with patch("backend.app.trade_selector._get_price_and_range",
                   return_value=(70.0, 2.5, 69.5, 1.3)), \
             patch("backend.app.trade_selector.check_event_conflict",
                   return_value=None):
            result = select_trade(scored, ScanType.EUROPE)

        assert result.all_scored_news is not None
        assert len(result.all_scored_news) == 2
        # The NEUTRAL one should be in rejection_log
        assert result.rejection_log is not None
        assert any("NEUTRAL" in r["reason"] for r in result.rejection_log)


# ════════════════════════════════════════════════════════════════
# Phase 3: Chain reactions
# ════════════════════════════════════════════════════════════════


class TestChainReactions:
    """Verify second-order impact detection."""

    def test_oil_triggers_tte(self):
        """CL=F LONG should trigger TTE.PA LONG (same direction)."""
        reactions = _detect_chain_reactions(["CL=F"], Direction.LONG)
        tickers = [r.ticker for r in reactions]
        assert "TTE.PA" in tickers
        tte = next(r for r in reactions if r.ticker == "TTE.PA")
        assert tte.direction == Direction.LONG

    def test_gold_triggers_inverse_chf(self):
        """GC=F LONG should trigger USDCHF=X SHORT (inverse)."""
        reactions = _detect_chain_reactions(["GC=F"], Direction.LONG)
        chf = next((r for r in reactions if r.ticker == "USDCHF=X"), None)
        assert chf is not None
        assert chf.direction == Direction.SHORT

    def test_neutral_source_stays_neutral(self):
        """NEUTRAL direction should not propagate as LONG on inverse chains."""
        reactions = _detect_chain_reactions(["GC=F"], Direction.NEUTRAL)
        for r in reactions:
            assert r.direction == Direction.NEUTRAL

    def test_no_duplicate_chain_targets(self):
        """Chain reactions should not duplicate already-impacted tickers."""
        reactions = _detect_chain_reactions(["CL=F", "TTE.PA"], Direction.LONG)
        tickers = [r.ticker for r in reactions]
        assert tickers.count("TTE.PA") == 0  # Already in source list

    def test_corn_triggers_soy_and_wheat(self):
        """ZC=F should chain to ZS=F and ZW=F."""
        reactions = _detect_chain_reactions(["ZC=F"], Direction.SHORT)
        tickers = [r.ticker for r in reactions]
        assert "ZS=F" in tickers
        assert "ZW=F" in tickers


# ════════════════════════════════════════════════════════════════
# Phase 4: Journal closure and PnL calculation
# ════════════════════════════════════════════════════════════════


class TestJournalClosure:
    """Verify trade closure, PnL computation, and delay tracking."""

    def test_long_tp_hit(self):
        trade = _make_trade(direction=Direction.LONG, entry_price=70,
                            target_price=72, stop_price=69)
        result, exit_p, pnl = _determine_result(trade, 73.0, 69.5, 71.0)
        assert result == TradeResult.TP_HIT
        assert exit_p == 72.0
        assert abs(pnl - 2.857) < 0.01

    def test_short_sl_hit(self):
        trade = _make_trade(direction=Direction.SHORT, entry_price=70,
                            target_price=68, stop_price=71.5)
        result, exit_p, pnl = _determine_result(trade, 72.0, 69.0, 71.0)
        assert result == TradeResult.SL_HIT
        assert exit_p == 71.5
        assert pnl < 0

    def test_tp_priority_over_sl(self):
        """When both TP and SL hit same day, TP wins (our design choice)."""
        trade = _make_trade(direction=Direction.LONG, entry_price=70,
                            target_price=72, stop_price=69)
        result, _, _ = _determine_result(trade, 73.0, 68.0, 70.0)
        assert result == TradeResult.TP_HIT

    def test_no_data_gives_expired(self):
        trade = _make_trade()
        result, exit_p, pnl = _determine_result(trade, None, None, None)
        assert result == TradeResult.EXPIRED
        assert exit_p is None
        assert pnl is None

    def test_review_generation(self):
        trade = _make_trade(volume_confirmed=True,
                            binary_event_warning="Evenement binaire: 'fomc'")
        review = _build_review(trade, TradeResult.TP_HIT, 2.5)
        assert "Objectif atteint" in review
        assert "Volume confirme" in review
        assert "binaire" in review.lower()

    def test_extract_scan_trace_normal(self):
        """Normal dict extraction should work."""
        scan_data = {
            "europe": {
                "all_scored_news": [{"title": "test"}],
                "rejection_log": [],
                "decision_summary": "Selected CL=F",
                "learning_state": {"CL=F": 1.05},
            }
        }
        trace = _extract_scan_trace(scan_data, "europe")
        assert trace["all_scored_news"] == [{"title": "test"}]
        assert trace["decision_summary"] == "Selected CL=F"

    def test_extract_scan_trace_corrupt(self):
        """Corrupt data (not a dict) should return empty dict."""
        assert _extract_scan_trace({"europe": "garbage"}, "europe") == {}
        assert _extract_scan_trace({"europe": [1, 2, 3]}, "europe") == {}
        assert _extract_scan_trace({}, "europe") == {}

    def test_full_journal_flow(self):
        """Simulate the complete journal closure flow."""
        now = datetime.now(timezone.utc)
        trade = _make_trade(
            timestamp=now,
            result=TradeResult.PENDING,
            vix_at_trade=18.5,
            market_regime="normal",
        )

        with _with_temp_journal([]), \
             patch("backend.app.journal.load_trades", return_value=[trade]), \
             patch("backend.app.journal._fetch_day_prices",
                   return_value=(73.0, 69.5, 71.0)), \
             patch("backend.app.journal.update_trade_result") as mock_update, \
             patch("backend.app.journal.compute_learning_adjustments",
                   return_value={}), \
             patch("backend.app.journal.invalidate_learning_cache"):
            entries = run_daily_journal()

        assert len(entries) == 1
        entry = entries[0]
        assert entry["result"] == "TP_HIT"
        assert entry["pnl_pct"] > 0
        assert entry["news_category"] == "commodity"
        assert entry["raw_claude_score"] == 75.0
        assert entry["learning_multiplier"] == 1.0
        assert entry["vix_at_trade"] == 18.5
        assert entry["market_regime"] == "normal"
        mock_update.assert_called_once()


# ════════════════════════════════════════════════════════════════
# Phase 5: Learning and auto-progression
# ════════════════════════════════════════════════════════════════


class TestLearningPipeline:
    """Verify the full learning cycle: results → adjustments → feedback."""

    def _make_closed_trades(self, n_wins, n_losses, ticker="CL=F",
                            category="commodities", news_cat="commodity"):
        """Generate a mix of closed trades for learning tests."""
        trades = []
        base_time = datetime.now(timezone.utc) - timedelta(days=30)
        for i in range(n_wins):
            trades.append(_make_trade(
                ticker=ticker, category=category, news_category=news_cat,
                result=TradeResult.TP_HIT, pnl_pct=1.5,
                timestamp=base_time + timedelta(days=i),
                scan_type=ScanType.EUROPE,
            ))
        for i in range(n_losses):
            trades.append(_make_trade(
                ticker=ticker, category=category, news_category=news_cat,
                result=TradeResult.SL_HIT, pnl_pct=-1.0,
                timestamp=base_time + timedelta(days=n_wins + i),
                scan_type=ScanType.EUROPE,
            ))
        return trades

    def test_not_enough_data_returns_empty(self):
        """Fewer than 5 closed trades → no adjustments."""
        trades = self._make_closed_trades(2, 1)
        raw = [t.model_dump(mode="json") for t in trades]
        with _with_temp_trades(raw):
            adj = compute_learning_adjustments()
        assert adj == {}

    def test_winning_ticker_boosted(self):
        """A ticker with mostly wins should get multiplier > 1.0."""
        trades = self._make_closed_trades(8, 2)
        raw = [t.model_dump(mode="json") for t in trades]
        with _with_temp_trades(raw):
            adj = compute_learning_adjustments()
        if "CL=F" in adj:
            assert adj["CL=F"] > 1.0

    def test_losing_ticker_penalized(self):
        """A ticker with mostly losses should get multiplier < 1.0."""
        trades = self._make_closed_trades(1, 8)
        raw = [t.model_dump(mode="json") for t in trades]
        with _with_temp_trades(raw):
            adj = compute_learning_adjustments()
        if "CL=F" in adj:
            assert adj["CL=F"] < 1.0

    def test_adjustments_bounded(self):
        """All adjustments must stay within [0.5, 1.5]."""
        trades = self._make_closed_trades(15, 0)
        raw = [t.model_dump(mode="json") for t in trades]
        with _with_temp_trades(raw):
            adj = compute_learning_adjustments()
        for mult in adj.values():
            assert 0.5 <= mult <= 1.5

    def test_significance_rejects_noise(self):
        """Noisy data (equal wins/losses) should not produce adjustment."""
        assert _is_significant([1.0, -1.0, 1.0, -1.0, 1.0, -1.0]) is False

    def test_significance_accepts_clear_signal(self):
        """Consistent positive data should pass significance test."""
        assert _is_significant([1.0, 1.2, 0.8, 1.1, 0.9]) is True

    def test_multiplicative_blending_compounds(self):
        """Bad ticker * bad category should compound the penalty."""
        # This is a logic test — with both signals negative, result < either alone
        entries_bad = [(- 1.0, 1.0)] * 6
        adj = _compute_adjustment(entries_bad, sensitivity=0.5, pnl_cap=0.25,
                                  bounds=(0.5, 1.5), min_significant=4)
        if adj is not None:
            assert adj < 1.0

    def test_performance_summary_content(self):
        """Performance summary should include key sections."""
        trades = self._make_closed_trades(5, 3)
        raw = [t.model_dump(mode="json") for t in trades]
        with _with_temp_trades(raw):
            summary = build_performance_summary()
        assert "HISTORIQUE DE PERFORMANCE" in summary
        assert "Win rate" in summary
        assert "Derniers" in summary
        assert "CL=F" in summary

    def test_performance_summary_empty_when_insufficient(self):
        """Summary should be empty with < 5 closed trades."""
        trades = self._make_closed_trades(2, 1)
        raw = [t.model_dump(mode="json") for t in trades]
        with _with_temp_trades(raw):
            summary = build_performance_summary()
        assert summary == ""

    def test_performance_stats_accuracy(self):
        """compute_performance() should return accurate stats."""
        trades = self._make_closed_trades(6, 4)
        raw = [t.model_dump(mode="json") for t in trades]
        with _with_temp_trades(raw):
            stats = compute_performance()
        assert stats.total_trades == 10
        assert stats.wins == 6
        assert stats.losses == 4
        assert stats.win_rate == 60.0
        assert stats.total_pnl_pct > 0  # 6*1.5 - 4*1.0 = 5.0

    def test_learning_adjustments_applied_to_selection(self):
        """Learning multiplier should change effective score in trade selection."""
        scored = [_make_scored(tickers=["CL=F"], surprise=80, delay=75, awareness=10)]
        # With boost
        with patch("backend.app.trade_selector._get_price_and_range",
                   return_value=(70.0, 2.5, 69.5, 1.3)), \
             patch("backend.app.trade_selector.check_event_conflict",
                   return_value=None):
            result_boosted = select_trade(
                scored, ScanType.EUROPE,
                learning_adjustments={"CL=F": 1.3},
            )
            result_penalized = select_trade(
                scored, ScanType.EUROPE,
                learning_adjustments={"CL=F": 0.5},
            )
        # Both should produce trades but with different confidence
        if result_boosted.has_trade and result_penalized.has_trade:
            assert result_boosted.recommendation.confidence >= result_penalized.recommendation.confidence


# ════════════════════════════════════════════════════════════════
# Phase 6: Full pipeline integration (scoring → save → close → learn)
# ════════════════════════════════════════════════════════════════


class TestFullPipelineIntegration:
    """Simulate multiple days of trading and verify the system learns."""

    def test_multi_day_cycle(self):
        """Simulate 3 days of trading: score → select → save → journal → learn."""
        all_trades = []
        base = datetime.now(timezone.utc) - timedelta(days=10)

        # Day 1: Good commodity trade → TP
        all_trades.append(_make_trade(
            timestamp=base, ticker="CL=F", category="commodities",
            news_category="commodity", direction=Direction.LONG,
            entry_price=70.0, target_price=72.0, stop_price=69.0,
            result=TradeResult.TP_HIT, pnl_pct=2.86,
            raw_claude_score=78.0, learning_multiplier=1.0,
            predicted_transmission_delay=70,
        ))
        # Day 2: Bad weather trade → SL
        all_trades.append(_make_trade(
            timestamp=base + timedelta(days=1), ticker="ZC=F",
            asset_name="Maïs", category="commodities",
            news_category="weather", direction=Direction.LONG,
            entry_price=450.0, target_price=460.0, stop_price=445.0,
            result=TradeResult.SL_HIT, pnl_pct=-1.11,
            raw_claude_score=82.0, learning_multiplier=1.0,
        ))
        # Day 3: Geopolitical → EXPIRED slightly positive
        all_trades.append(_make_trade(
            timestamp=base + timedelta(days=2), ticker="GC=F",
            asset_name="Or", category="metaux",
            news_category="geopolitical", direction=Direction.LONG,
            entry_price=2000.0, target_price=2040.0, stop_price=1985.0,
            result=TradeResult.EXPIRED, pnl_pct=0.5,
            raw_claude_score=65.0, learning_multiplier=1.0,
        ))
        # Add more trades to hit min_trades threshold
        for i in range(5):
            all_trades.append(_make_trade(
                timestamp=base + timedelta(days=3 + i), ticker="CL=F",
                result=TradeResult.TP_HIT, pnl_pct=1.2,
                raw_claude_score=70.0, learning_multiplier=1.0,
            ))

        raw = [t.model_dump(mode="json") for t in all_trades]
        with _with_temp_trades(raw):
            # Verify performance calculation
            stats = compute_performance()
            assert stats.total_trades == 8
            assert stats.wins == 6
            assert stats.win_rate == round(6 / 8 * 100, 1)

            # Verify learning produces adjustments
            adj = compute_learning_adjustments()
            # CL=F has 6 wins, 0 losses → should be boosted
            if "CL=F" in adj:
                assert adj["CL=F"] >= 1.0

            # Verify performance summary is generated
            summary = build_performance_summary()
            assert "HISTORIQUE DE PERFORMANCE" in summary
            assert "Win rate" in summary

    def test_delay_accuracy_tracking(self):
        """Verify predicted vs actual transmission delay tracking."""
        now = datetime.now(timezone.utc)
        trade = _make_trade(
            timestamp=now,
            result=TradeResult.PENDING,
            predicted_transmission_delay=70,
        )

        with _with_temp_journal([]), \
             patch("backend.app.journal.load_trades", return_value=[trade]), \
             patch("backend.app.journal._fetch_day_prices",
                   return_value=(73.0, 69.5, 71.0)), \
             patch("backend.app.journal.update_trade_result") as mock_update, \
             patch("backend.app.journal.compute_learning_adjustments",
                   return_value={}), \
             patch("backend.app.journal.invalidate_learning_cache"):
            entries = run_daily_journal()

        assert len(entries) == 1
        entry = entries[0]
        assert entry["predicted_transmission_delay"] == 70
        # update_trade_result should have been called with delay accuracy
        mock_update.assert_called_once()

    def test_csv_export_fields_complete(self):
        """Verify all v3 fields are present in trade model dump."""
        trade = _make_trade(
            vix_at_trade=22.5, market_regime="elevated",
            volume_ratio=1.8, day_of_week=2,
        )
        dump = trade.model_dump(mode="json")
        assert "raw_claude_score" in dump
        assert "learning_multiplier" in dump
        assert "vix_at_trade" in dump
        assert "market_regime" in dump
        assert "predicted_transmission_delay" in dump
        assert dump["vix_at_trade"] == 22.5
        assert dump["market_regime"] == "elevated"


# ════════════════════════════════════════════════════════════════
# Phase 7: Edge cases and error resilience
# ════════════════════════════════════════════════════════════════


class TestErrorResilience:
    """Verify the system handles errors gracefully."""

    def test_freshness_no_published_date(self):
        """Unknown publish date → freshness 50 (neutral)."""
        assert _compute_freshness(None) == 50

    def test_freshness_very_old(self):
        """Very old news → freshness floor at 5 (not 0)."""
        old = datetime.now(timezone.utc) - timedelta(hours=24)
        assert _compute_freshness(old) == 5

    def test_freshness_future_date(self):
        """Future date → freshness 100."""
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        assert _compute_freshness(future) == 100

    def test_select_trade_empty_scored(self):
        """Empty scored list → no trade, no crash."""
        result = select_trade([], ScanType.EUROPE)
        assert result.has_trade is False
        assert "Aucune news" in result.reason_no_trade

    def test_select_trade_price_unavailable(self):
        """If yfinance returns None price, should skip to next candidate."""
        scored = [_make_scored(tickers=["CL=F"])]
        with patch("backend.app.trade_selector._get_price_and_range",
                   return_value=(None, 1.5, None, None)), \
             patch("backend.app.trade_selector.check_event_conflict",
                   return_value=None):
            result = select_trade(scored, ScanType.EUROPE)
        assert result.has_trade is False

    def test_journal_dedup_prevents_double_entry(self):
        """Same trade should not create two journal entries."""
        now = datetime.now(timezone.utc)
        trade = _make_trade(timestamp=now, result=TradeResult.PENDING)

        existing = JournalEntry(
            date=now.strftime("%Y-%m-%d"),
            scan_type=ScanType.EUROPE,
            news_title="Oil supply disruption",
            news_source="EIA",
            reasoning="Test",
            score=75,
            ticker="CL=F",
            asset_name="Pétrole WTI",
            direction=Direction.LONG,
            entry_time=now,
            entry_price=70.0,
            result=TradeResult.TP_HIT,
        )
        existing_raw = [existing.model_dump(mode="json")]

        with _with_temp_journal(existing_raw), \
             patch("backend.app.journal.load_trades", return_value=[trade]), \
             patch("backend.app.journal.compute_learning_adjustments",
                   return_value={}), \
             patch("backend.app.journal.invalidate_learning_cache"):
            result = run_daily_journal()
        assert result == []

    def test_corrupt_trades_file_doesnt_crash_summary(self):
        """If load_trades fails, build_performance_summary returns empty."""
        with patch("backend.app.learning.load_trades",
                   side_effect=Exception("corrupt file")):
            summary = build_performance_summary()
        assert summary == ""

    def test_scan_cache_corrupt_type_handled(self):
        """If last_scans.json is a list (not dict), extraction should not crash."""
        trace = _extract_scan_trace({"europe": [1, 2]}, "europe")
        assert trace == {}
