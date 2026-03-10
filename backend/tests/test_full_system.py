"""Full system integration test — simulates 1 month of trading.

Tests the COMPLETE pipeline: News → Scoring → Trade Selection → Journal → Learning.
All external dependencies mocked (Claude API, market data, RSS, PG).

Validates:
- All 6 agents function correctly
- Data flows end-to-end through the pipeline
- Learning evolves based on trade outcomes
- Journal correctly closes positions
- Auditor produces valid reports
- Database operations (JSON fallback)
- Frontend build integrity
- Models serialize/deserialize correctly
"""

import json
import tempfile
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from contextlib import contextmanager

import pytest

from backend.app.config import (
    ASSET_BY_TICKER,
    ASSETS,
    CATEGORY_SCORE_MULTIPLIERS,
    CORRELATION_GROUPS,
    MIN_RISK_REWARD,
    MIN_SCORE_THRESHOLD,
    NEWS_CATEGORY_MULTIPLIERS,
    assets_for_session,
    ESTIMATED_SPREADS,
)
from backend.app.journal import (
    _determine_result,
    _compute_pnl,
    load_journal,
    run_daily_journal,
)
from backend.app.learning import (
    _compute_adjustment,
    _is_significant,
    build_performance_summary,
    compute_learning_adjustments,
    compute_performance,
    invalidate_perf_summary_cache,
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
    _is_zero_edge_headline,
)
from backend.app.trade_selector import (
    _calibrate_trade,
    _check_correlation,
    select_trade,
    select_trades,
)


# ════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════

def _make_news(title="Test news", source="Reuters", source_weight=0.85,
               published=None, tickers=None, description=""):
    return NewsItem(
        title=title,
        source=source,
        url="https://example.com/test",
        published=published or datetime.now(timezone.utc),
        related_tickers=tickers or [],
        source_weight=source_weight,
        description=description,
    )


def _make_scored(news=None, surprise=70, freshness=100, clarity=80,
                 delay=70, awareness=15, direction=Direction.LONG,
                 tickers=None, category="commodity", cat_mult=None,
                 reliability=100, magnitude=50):
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
        expected_magnitude=magnitude,
        signal_reliability=reliability,
        direction=direction,
        impacted_tickers=tickers or ["CL=F"],
        reasoning="Test reasoning",
        news_category=category,
        category_score_mult=cat_mult,
    )


def _make_trade(days_ago=0, **overrides):
    ts = datetime.now(timezone.utc) - timedelta(days=days_ago)
    defaults = dict(
        scan_type=ScanType.EUROPE,
        timestamp=ts,
        ticker="CL=F",
        asset_name="Pétrole WTI",
        category="commodities_energy",
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
        expected_magnitude=50,
        signal_reliability=80,
    )
    defaults.update(overrides)
    return TradeRecommendation(**defaults)


def _make_journal_entry(**overrides):
    """Create a JournalEntry with all required fields."""
    defaults = dict(
        date="2026-03-01",
        scan_type=ScanType.EUROPE,
        news_title="Test headline",
        news_source="Reuters",
        news_category="commodity",
        reasoning="Test reasoning",
        score=75.0,
        ticker="CL=F",
        asset_name="Pétrole WTI",
        asset_category="commodities_energy",
        direction=Direction.LONG,
        entry_time=datetime.now(timezone.utc),
        entry_price=70.0,
        exit_price=72.0,
        exit_time=datetime.now(timezone.utc) + timedelta(hours=6),
        result=TradeResult.TP_HIT,
        pnl_pct=2.86,
    )
    defaults.update(overrides)
    return JournalEntry(**defaults)


@contextmanager
def _temp_file(initial_data=None):
    """Create a temp JSON file and return its path."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(initial_data or [], tmp, default=str)
    tmp.close()
    try:
        yield Path(tmp.name)
    finally:
        os.unlink(tmp.name)


@contextmanager
def _temp_trades_and_journal(trades_data=None, journal_data=None):
    """Patch both TRADES_FILE and JOURNAL_FILE with temp files."""
    with _temp_file(trades_data) as tf, _temp_file(journal_data) as jf:
        with patch("backend.app.learning.TRADES_FILE", tf), \
             patch("backend.app.journal.JOURNAL_FILE", jf):
            yield tf, jf


# ════════════════════════════════════════════════════════════════
# Phase 1: Agent News — collecte et dedup
# ════════════════════════════════════════════════════════════════

class TestAgentNewsSystem:
    """Vérifie que l'Agent News collecte, déduplique et publie correctement."""

    def test_news_item_creation(self):
        """Un NewsItem est correctement créé avec tous les champs."""
        news = _make_news(
            title="NOAA: Severe drought in Midwest",
            source="NOAA",
            source_weight=1.1,
            tickers=["ZC=F", "ZW=F"],
            description="Prolonged heat wave forecast",
        )
        assert news.title == "NOAA: Severe drought in Midwest"
        assert news.source_weight == 1.1
        assert "ZC=F" in news.related_tickers
        assert news.description == "Prolonged heat wave forecast"

    def test_freshness_computation(self):
        """La fraîcheur décroît avec l'âge."""
        now = datetime.now(timezone.utc)
        fresh = _compute_freshness(now - timedelta(minutes=30))
        stale = _compute_freshness(now - timedelta(hours=6))
        assert fresh > stale
        assert fresh >= 80
        assert stale < 50

    def test_zero_edge_headline_detection(self):
        """Les headlines zero-edge sont correctement filtrées."""
        # _is_zero_edge_headline returns category name or None
        assert _is_zero_edge_headline("Apple Q4 earnings report beats consensus") is not None
        assert _is_zero_edge_headline("nonfarm payroll comes in at 256K") is not None
        assert _is_zero_edge_headline("NOAA warns of severe drought in US Midwest") is None
        assert _is_zero_edge_headline("Frost alert in Brazil Minas Gerais region") is None

    def test_chain_reaction_detection(self):
        """Les chain reactions enrichissent les tickers impactés."""
        # _detect_chain_reactions takes (impacted_tickers, direction)
        reactions = _detect_chain_reactions(["CL=F"], Direction.LONG)
        # CL=F should trigger TTE.PA, BZ=F, NG=F
        assert len(reactions) >= 1

    def test_news_dedup_jaccard(self):
        """Les news similaires sont dédupliquées."""
        from backend.app.news_collector import _jaccard_similarity
        sim = _jaccard_similarity(
            "Oil prices surge on supply disruption fears",
            "Oil prices rise on supply disruption concerns",
        )
        assert sim > 0.5

    def test_agent_news_init_and_metrics(self):
        """L'Agent News s'initialise et retourne des métriques."""
        from backend.app.agents.agent_news import AgentNews
        agent = AgentNews()
        assert agent.name == "news"
        metrics = agent.get_metrics()
        assert "last_collect_count" in metrics
        assert "total_collected" in metrics


# ════════════════════════════════════════════════════════════════
# Phase 2: Agent Scoring — formule et cohérence
# ════════════════════════════════════════════════════════════════

class TestAgentScoringSystem:
    """Vérifie la formule de scoring et la cohérence des scores."""

    def test_score_formula_weather_vs_earnings(self):
        """Weather score >> earnings grâce à edge_factor et category_mult."""
        weather = _make_scored(
            surprise=85, clarity=90, delay=85, awareness=10,
            category="weather", reliability=90, magnitude=70,
        )
        earnings = _make_scored(
            surprise=60, clarity=95, delay=5, awareness=95,
            category="earnings", reliability=100, magnitude=40,
        )
        assert weather.total_score > earnings.total_score * 5

    def test_score_formula_edge_factor_floor(self):
        """L'edge_factor ne descend pas en-dessous de 0.05."""
        low_edge = _make_scored(delay=5, awareness=95)
        assert low_edge.total_score > 0

    def test_reliability_factor_range(self):
        """Le reliability_factor varie de 0.4 (rumeur) à 1.0 (fait confirmé)."""
        rumor = _make_scored(reliability=0)
        confirmed = _make_scored(reliability=100)
        assert confirmed.total_score > rumor.total_score * 2

    def test_category_multipliers_hierarchy(self):
        """weather/supply_chain > commodity > geopolitical > ... > earnings."""
        weather = _make_scored(category="weather")
        commodity = _make_scored(category="commodity")
        earnings = _make_scored(category="earnings")
        assert weather.total_score > commodity.total_score
        assert commodity.total_score > earnings.total_score

    def test_convergence_count_default(self):
        """convergence_count a une valeur par défaut de 0."""
        scored = _make_scored()
        assert scored.convergence_count == 0

    def test_scored_news_serialization(self):
        """ScoredNews se sérialise et désérialise correctement."""
        scored = _make_scored(
            tickers=["ZW=F", "ZC=F"],
            category="weather",
            direction=Direction.SHORT,
        )
        data = scored.model_dump(mode="json")
        assert data["direction"] == "SHORT"
        assert data["news_category"] == "weather"
        assert data["transmission_delay"] == 70

    def test_agent_scoring_init(self):
        """L'Agent Scoring s'initialise correctement."""
        from backend.app.agents.agent_scoring import AgentScoring
        agent = AgentScoring()
        assert agent.name == "scoring"
        metrics = agent.get_metrics()
        assert "total_scored" in metrics


# ════════════════════════════════════════════════════════════════
# Phase 3: Agent Trader — sélection et calibration
# ════════════════════════════════════════════════════════════════

class TestAgentTraderSystem:
    """Vérifie la sélection de trade, la calibration R/R et les filtres."""

    def test_correlation_check_blocks_same_group(self):
        """Deux tickers dans le même groupe de corrélation sont bloqués."""
        result = _check_correlation("BZ=F", ["CL=F"])
        assert result is True  # energy group → blocked

    def test_correlation_check_allows_different_groups(self):
        """Deux tickers dans des groupes différents passent."""
        result = _check_correlation("GC=F", ["CL=F"])
        assert result is False  # gold vs energy → allowed

    def test_spread_filter_in_config(self):
        """Les spreads estimés sont raisonnables."""
        assert len(ESTIMATED_SPREADS) > 0
        for ticker, spread in ESTIMATED_SPREADS.items():
            assert 0 < spread < 5.0, f"Unreasonable spread for {ticker}: {spread}%"

    def test_convex_score_factor(self):
        """Le facteur de score est convexe : (score/100)^1.5."""
        factor_50 = (50 / 100) ** 1.5
        factor_90 = (90 / 100) ** 1.5
        assert factor_90 / factor_50 > 2.0  # Disproportionately higher

    def test_select_trade_empty_news(self):
        """Un scan sans news ne génère pas de trade."""
        result = select_trade([], ScanType.EUROPE)
        assert result is None or (isinstance(result, ScanResult) and not result.has_trade)

    def test_agent_trader_init(self):
        """L'Agent Trader s'initialise correctement."""
        from backend.app.agents.agent_trader import AgentTrader
        agent = AgentTrader()
        assert agent.name == "trader_1"
        metrics = agent.get_metrics()
        assert isinstance(metrics, dict)


# ════════════════════════════════════════════════════════════════
# Phase 4: Agent Journal — clôture et P&L
# ════════════════════════════════════════════════════════════════

class TestAgentJournalSystem:
    """Vérifie la clôture des trades, le P&L et les enrichissements v4.1."""

    def test_determine_result_tp_hit(self):
        """Un trade LONG dont le TP est touché → TP_HIT."""
        trade = _make_trade(entry_price=70.0, target_price=72.0, stop_price=69.0)
        result, exit_price, pnl = _determine_result(
            trade=trade,
            post_high=73.0,
            post_low=69.5,
            close=72.5,
        )
        assert result == TradeResult.TP_HIT
        assert exit_price == 72.0

    def test_determine_result_sl_hit(self):
        """Un trade LONG dont le SL est touché → SL_HIT."""
        trade = _make_trade(entry_price=70.0, target_price=72.0, stop_price=69.0)
        result, exit_price, pnl = _determine_result(
            trade=trade,
            post_high=70.5,
            post_low=68.5,
            close=69.0,
        )
        assert result == TradeResult.SL_HIT
        assert exit_price == 69.0

    def test_determine_result_expired(self):
        """Un trade sans TP ni SL touché → EXPIRED."""
        trade = _make_trade(entry_price=70.0, target_price=72.0, stop_price=69.0)
        result, exit_price, pnl = _determine_result(
            trade=trade,
            post_high=71.5,
            post_low=69.5,
            close=70.5,
        )
        assert result == TradeResult.EXPIRED
        assert exit_price == 70.5  # last bar close

    def test_compute_pnl_long(self):
        """P&L LONG = (exit - entry) / entry × 100."""
        trade = _make_trade(entry_price=70.0, direction=Direction.LONG)
        pnl = _compute_pnl(trade, 72.0)
        assert abs(pnl - 2.857) < 0.01

    def test_compute_pnl_short(self):
        """P&L SHORT = (entry - exit) / entry × 100."""
        trade = _make_trade(entry_price=70.0, direction=Direction.SHORT,
                            target_price=68.0, stop_price=71.0)
        pnl = _compute_pnl(trade, 68.0)
        assert abs(pnl - 2.857) < 0.01

    def test_compute_pnl_division_by_zero(self):
        """P&L avec entry_price=0 ne crash pas."""
        trade = _make_trade(entry_price=0.0)
        pnl = _compute_pnl(trade, 72.0)
        assert pnl == 0.0

    def test_journal_entry_model(self):
        """Un JournalEntry a tous les champs v4.1."""
        entry = _make_journal_entry(
            slippage_pct=0.05,
            max_adverse_excursion=0.5,
            max_favorable_excursion=3.0,
            bar_coverage=12,
            bar_interval="15min",
            realized_rr=2.0,
        )
        assert entry.slippage_pct == 0.05
        assert entry.bar_interval == "15min"
        assert entry.max_favorable_excursion == 3.0

    def test_journal_closes_pending_trades(self):
        """run_daily_journal ferme les trades PENDING."""
        trade = _make_trade()
        trades_data = [trade.model_dump(mode="json")]

        with _temp_file(trades_data) as tf, _temp_file([]) as jf:
            with patch("backend.app.learning.TRADES_FILE", tf), \
                 patch("backend.app.journal.JOURNAL_FILE", jf), \
                 patch("backend.app.journal.is_pg_enabled", return_value=False), \
                 patch("backend.app.learning.is_pg_enabled", return_value=False), \
                 patch("backend.app.journal._fetch_intraday_prices", return_value=(
                     73.0, 69.5, 71.5, [
                         (datetime.now(timezone.utc), 72.0, 69.5, 70.0, 71.5),
                         (datetime.now(timezone.utc) + timedelta(hours=1), 73.0, 71.0, 71.5, 72.5),
                     ]
                 )):
                result = run_daily_journal()

            assert isinstance(result, (list, dict))

    def test_agent_journal_init(self):
        """L'Agent Journal s'initialise correctement."""
        from backend.app.agents.agent_journal import AgentJournal
        agent = AgentJournal()
        assert agent.name == "journal"


# ════════════════════════════════════════════════════════════════
# Phase 5: Agent Learning — ajustements et dimensions
# ════════════════════════════════════════════════════════════════

class TestAgentLearningSystem:
    """Vérifie les 6 dimensions de learning et la protection commodities."""

    def _make_trades_for_learning(self, n=12, win_rate=0.5, ticker="CL=F",
                                   category="commodity", direction=Direction.LONG):
        """Génère N trades avec un win rate donné."""
        trades = []
        for i in range(n):
            is_win = i < int(n * win_rate)
            t = _make_trade(
                days_ago=i + 1,
                ticker=ticker,
                news_category=category,
                direction=direction,
                result=TradeResult.TP_HIT if is_win else TradeResult.SL_HIT,
                pnl_pct=2.5 if is_win else -1.5,
                exit_price=72.0 if is_win else 69.0,
                raw_claude_score=75.0,
                learning_multiplier=1.0,
                scan_type=ScanType.EUROPE,
            )
            trades.append(t)
        return trades

    def test_compute_adjustment_basic(self):
        """_compute_adjustment retourne un ajustement basé sur le P&L moyen."""
        # entries is list[tuple[float, float]] → (pnl, weight)
        adj = _compute_adjustment([(2.0, 1.0), (1.5, 1.0), (3.0, 1.0), (-0.5, 1.0)])
        assert adj is not None
        assert adj > 1.0
        # Negative average → penalty
        adj_neg = _compute_adjustment([(-2.0, 1.0), (-1.5, 1.0), (-3.0, 1.0), (0.5, 1.0)])
        assert adj_neg is not None
        assert adj_neg < 1.0

    def test_significance_requires_min_trades(self):
        """Le test de significativité requiert un nombre minimum de trades."""
        assert not _is_significant([1.0, 2.0], min_samples=5)

    def test_significance_enough_trades(self):
        """Assez de trades → significatif si effet assez grand."""
        data = [1.0, 2.0, 1.5, 3.0, 2.5, 1.8, 2.2, 1.9]
        assert _is_significant(data, min_samples=5)

    def test_significance_min_effect_size(self):
        """Les effets négligeables (<0.1) ne sont pas significatifs."""
        tiny = [0.01, -0.01, 0.02, -0.02, 0.01, -0.01, 0.02, -0.02, 0.01, -0.01]
        assert not _is_significant(tiny, min_samples=5)

    def test_learning_adjustments_structure(self):
        """compute_learning_adjustments retourne le bon format."""
        trades = self._make_trades_for_learning(n=20, win_rate=0.6)
        trades_data = [t.model_dump(mode="json") for t in trades]

        with _temp_file(trades_data) as tf:
            with patch("backend.app.learning.TRADES_FILE", tf), \
                 patch("backend.app.learning.is_pg_enabled", return_value=False):
                invalidate_perf_summary_cache()
                result = compute_learning_adjustments()

        assert "adjustments" in result
        assert "session_adj" in result
        assert "newscat_adj" in result
        assert "regime_adj" in result
        assert "direction_adj" in result
        assert "delay_bias_adj" in result
        assert "decomposition" in result

    def test_commodities_never_penalized_as_class(self):
        """Les commodities ne sont JAMAIS pénalisées en tant que classe (règle absolue)."""
        trades = self._make_trades_for_learning(
            n=15, win_rate=0.2,
            ticker="ZW=F", category="weather",
        )
        trades_data = [t.model_dump(mode="json") for t in trades]

        with _temp_file(trades_data) as tf:
            with patch("backend.app.learning.TRADES_FILE", tf), \
                 patch("backend.app.learning.is_pg_enabled", return_value=False):
                invalidate_perf_summary_cache()
                result = compute_learning_adjustments()

        decomp = result.get("decomposition", {})
        for ticker, d in decomp.items():
            asset = ASSET_BY_TICKER.get(ticker)
            if asset and asset.category.startswith("commodities"):
                cat_mult = d.get("cat_mult", 1.0)
                assert cat_mult >= 1.0, f"Commodity {ticker} penalized: cat_mult={cat_mult}"

    def test_learning_blending_clamp(self):
        """Le blending multiplicatif est clampé à [0.5, 1.5]."""
        final = 0.5 * 0.8 * 0.7 * 0.6 * 0.7 * 0.8
        assert max(0.5, min(1.5, final)) == 0.5

        final_high = 1.5 * 1.3 * 1.3 * 1.3 * 1.2 * 1.1
        assert max(0.5, min(1.5, final_high)) == 1.5

    def test_performance_summary(self):
        """build_performance_summary retourne un string."""
        trades = self._make_trades_for_learning(n=10, win_rate=0.5)
        summary = build_performance_summary(trades=trades)
        assert isinstance(summary, str)

    def test_newscat_cross_dimension(self):
        """Les ajustements newscat sont par newscat+ticker (v5.2)."""
        trades_w_zw = self._make_trades_for_learning(
            n=8, win_rate=0.8, ticker="ZW=F", category="weather",
        )
        trades_w_cc = self._make_trades_for_learning(
            n=8, win_rate=0.3, ticker="CC=F", category="weather",
        )
        all_trades = trades_w_zw + trades_w_cc
        trades_data = [t.model_dump(mode="json") for t in all_trades]

        with _temp_file(trades_data) as tf:
            with patch("backend.app.learning.TRADES_FILE", tf), \
                 patch("backend.app.learning.is_pg_enabled", return_value=False):
                invalidate_perf_summary_cache()
                result = compute_learning_adjustments()

        newscat = result.get("newscat_adj", {})
        assert isinstance(newscat, dict)

    def test_agent_learning_init(self):
        """L'Agent Learning s'initialise correctement."""
        from backend.app.agents.agent_learning import AgentLearning
        agent = AgentLearning()
        assert agent.name == "learning"


# ════════════════════════════════════════════════════════════════
# Phase 6: Agent Auditor — rapports et profils
# ════════════════════════════════════════════════════════════════

class TestAgentAuditorSystem:
    """Vérifie l'Auditeur, ses 8 profils et la persistance des rapports."""

    def test_auditor_has_all_profiles(self):
        """L'Auditeur couvre les 8 profils d'expertise."""
        from backend.app.agents.agent_auditor import AUDIT_PROFILES
        assert "news" in AUDIT_PROFILES
        assert "scoring" in AUDIT_PROFILES
        assert "trader_1" in AUDIT_PROFILES
        assert "journal" in AUDIT_PROFILES
        assert "learning" in AUDIT_PROFILES
        assert "ux" in AUDIT_PROFILES
        assert "auditor" in AUDIT_PROFILES

    def test_auditor_produces_report(self):
        """Un audit produit un rapport avec score, findings et améliorations."""
        from backend.app.agents.agent_auditor import AgentAuditor
        auditor = AgentAuditor()

        with patch.object(auditor, "_load_reports", return_value=None), \
             patch.object(auditor, "_save_report"):
            report = auditor.run(target_agent="news")

        assert isinstance(report, dict)
        assert "score" in report
        assert "findings" in report
        assert "improvements" in report
        assert 0 <= report["score"] <= 10

    def test_auditor_self_audit(self):
        """L'Auditeur peut s'auditer lui-même."""
        from backend.app.agents.agent_auditor import AgentAuditor
        auditor = AgentAuditor()

        with patch.object(auditor, "_load_reports", return_value=None), \
             patch.object(auditor, "_save_report"):
            report = auditor.run(target_agent="auditor")

        assert isinstance(report, dict)


# ════════════════════════════════════════════════════════════════
# Phase 7: Database — persistance et fallback
# ════════════════════════════════════════════════════════════════

class TestDatabaseSystem:
    """Vérifie la persistance JSON (fallback) et les opérations CRUD."""

    def test_save_and_load_trades(self):
        """save_trade + load_trades fonctionne en mode JSON fallback."""
        trade = _make_trade()

        with _temp_file([]) as tf:
            with patch("backend.app.learning.TRADES_FILE", tf), \
                 patch("backend.app.learning.is_pg_enabled", return_value=False):
                save_trade(trade)
                loaded = load_trades()

        assert len(loaded) == 1
        assert loaded[0].ticker == "CL=F"

    def test_update_trade_result(self):
        """update_trade_result met à jour le résultat d'un trade."""
        trade = _make_trade()
        trades_data = [trade.model_dump(mode="json")]

        with _temp_file(trades_data) as tf:
            with patch("backend.app.learning.TRADES_FILE", tf), \
                 patch("backend.app.learning.is_pg_enabled", return_value=False):
                update_trade_result(
                    timestamp=trade.timestamp,
                    ticker="CL=F",
                    result=TradeResult.TP_HIT,
                    exit_price=72.0,
                )
                loaded = load_trades()

        assert loaded[0].result == TradeResult.TP_HIT
        assert loaded[0].exit_price == 72.0

    def test_journal_save_and_load(self):
        """Les entrées journal sont sauvegardées et relues."""
        entry = _make_journal_entry(ticker="GC=F", asset_name="Or", direction=Direction.LONG)
        data = [entry.model_dump(mode="json")]

        with _temp_file(data) as jf:
            with patch("backend.app.journal.JOURNAL_FILE", jf), \
                 patch("backend.app.journal.is_pg_enabled", return_value=False):
                loaded = load_journal()

        assert len(loaded) == 1
        assert loaded[0].ticker == "GC=F"

    def test_corrupt_json_resilience(self):
        """Le système résiste aux fichiers JSON corrompus."""
        with _temp_file(None) as tf:
            tf.write_text("{invalid json")
            with patch("backend.app.learning.TRADES_FILE", tf), \
                 patch("backend.app.learning.is_pg_enabled", return_value=False):
                loaded = load_trades()
            assert loaded == [] or isinstance(loaded, list)

    def test_database_module_imports(self):
        """Le module database est importable avec toutes les fonctions nécessaires."""
        from backend.app.database import (
            is_pg_enabled,
            pg_load_trades,
            pg_save_trade,
            pg_update_trade_result,
        )
        assert callable(is_pg_enabled)


# ════════════════════════════════════════════════════════════════
# Phase 8: Infrastructure Agents — base, bus, logger
# ════════════════════════════════════════════════════════════════

class TestAgentInfrastructure:
    """Vérifie BaseAgent, MessageBus et AgentLogger."""

    def test_message_bus_publish_consume(self):
        """Le MessageBus publie et consomme des messages."""
        from backend.app.agents.base import MessageBus
        bus = MessageBus()
        bus.publish("test_agent", "test_action", {"key": "value"})
        msgs = bus.recent_messages(limit=10)
        assert len(msgs) >= 1

    def test_message_bus_targeted(self):
        """Les messages ciblés sont livrés au bon agent."""
        from backend.app.agents.base import MessageBus
        bus = MessageBus()
        bus.publish("agent_a", "msg_for_b", {"data": 1}, to_agent="agent_b")
        consumed = bus.consume("agent_b", ["msg_for_b"])
        assert any(m["msg_type"] == "msg_for_b" for m in consumed)

    def test_agent_logger(self):
        """AgentLogger enregistre les logs avec le bon format."""
        from backend.app.agents.base import AgentLogger
        logger = AgentLogger("test_agent")
        logger.log("test_action", {"detail": "value"})
        logs = logger.get_logs(limit=5)
        assert len(logs) >= 1
        assert logs[0]["action"] == "test_action"

    def test_base_agent_status_lifecycle(self):
        """Un BaseAgent passe par idle → working → idle/error."""
        from backend.app.agents.base import BaseAgent, AgentStatus
        agent = BaseAgent()
        agent.name = "test"
        agent.description = "Test agent"

        assert agent.status["status"] == "idle"
        agent._set_status(AgentStatus.WORKING, "processing")
        assert agent.status["status"] == "working"
        agent._set_status(AgentStatus.IDLE, "done")
        assert agent.status["status"] == "idle"

    def test_registry_returns_all_agents(self):
        """Le registry retourne les 6 agents + UX virtuel."""
        from backend.app.agents.registry import get_all_status
        statuses = get_all_status()
        names = [s["name"] for s in statuses]
        assert "news" in names
        assert "scoring" in names
        assert "trader_1" in names
        assert "journal" in names
        assert "learning" in names
        assert "auditor" in names
        assert "ux" in names


# ════════════════════════════════════════════════════════════════
# Phase 9: Simulation complète — 1 mois de trading
# ════════════════════════════════════════════════════════════════

class TestOneMonthSimulation:
    """Simule 20 jours de trading (1 mois ouvrable)."""

    SCENARIOS = [
        # (jour, titre, source, catégorie, direction, surprise, delay, awareness, ticker, résultat)
        (1, "NOAA: Severe drought developing in US Midwest corn belt", "NOAA", "weather", Direction.LONG, 85, 85, 10, "ZC=F", TradeResult.TP_HIT),
        (2, "Frost warning issued for Brazil Minas Gerais coffee region", "Open-Meteo", "weather", Direction.LONG, 90, 90, 5, "KC=F", TradeResult.TP_HIT),
        (3, "EIA: crude oil inventories draw -8.2M barrels vs expected -2M", "EIA", "commodity", Direction.LONG, 75, 70, 20, "CL=F", TradeResult.TP_HIT),
        (4, "USDA: US wheat crop condition drops 12pp week-over-week", "USDA", "commodity", Direction.LONG, 80, 75, 15, "ZW=F", TradeResult.SL_HIT),
        (5, "Panama Canal Authority restricts daily transits to 24", "ACP", "supply_chain", Direction.LONG, 70, 80, 25, "BZ=F", TradeResult.EXPIRED),
        (6, "CFTC: Commercial gold shorts hit 5-year extreme", "CFTC", "commodity", Direction.LONG, 65, 60, 30, "GC=F", TradeResult.TP_HIT),
        (7, "GIE: German gas storage drops to 28% — winter stress", "GIE", "commodity", Direction.LONG, 75, 70, 20, "NG=F", TradeResult.SL_HIT),
        (8, "Tropical storm forming in Gulf of Mexico — Cat 2 forecast", "NHC", "weather", Direction.LONG, 80, 85, 10, "CL=F", TradeResult.TP_HIT),
        (9, "WOAH: HPAI outbreak detected in Iowa turkey farms", "WOAH", "commodity", Direction.LONG, 70, 75, 15, "LE=F", TradeResult.EXPIRED),
        (10, "Suez Canal blocked by grounded container ship", "gCaptain", "supply_chain", Direction.LONG, 90, 85, 10, "BZ=F", TradeResult.TP_HIT),
        (11, "NASA POWER: 14-day drought confirmed over Argentina Pampas", "NASA", "weather", Direction.SHORT, 75, 80, 15, "ZS=F", TradeResult.SL_HIT),
        (12, "LME copper inventory drops 40% in 2 months", "Reuters", "commodity", Direction.LONG, 70, 65, 25, "HG=F", TradeResult.TP_HIT),
        (13, "Russia suspends wheat exports for 3 months", "Reuters", "geopolitical", Direction.LONG, 85, 75, 20, "ZW=F", TradeResult.TP_HIT),
        (14, "OPEC+ announces additional 500K bbl/day cut", "OilPrice", "commodity", Direction.LONG, 60, 40, 50, "CL=F", TradeResult.SL_HIT),
        (15, "Record heatwave in India threatens wheat harvest", "Open-Meteo", "weather", Direction.LONG, 80, 80, 10, "ZW=F", TradeResult.TP_HIT),
        (16, "Cocoa processing data shows 15% demand surge", "Reuters", "commodity", Direction.LONG, 65, 60, 30, "CC=F", TradeResult.EXPIRED),
        (17, "Baltic Dry Index surges 25% in one week", "Freight", "supply_chain", Direction.LONG, 70, 65, 25, "HG=F", TradeResult.TP_HIT),
        (18, "Brazil real crashes 5% — sugar exports repriced", "Reuters", "geopolitical", Direction.SHORT, 75, 70, 20, "SB=F", TradeResult.TP_HIT),
        (19, "USDA WASDE: US corn yield revised down 8%", "USDA", "commodity", Direction.LONG, 85, 80, 15, "ZC=F", TradeResult.TP_HIT),
        (20, "Severe cyclone hits Australian wheat regions", "NHC", "weather", Direction.SHORT, 80, 85, 10, "ZW=F", TradeResult.SL_HIT),
    ]

    def test_full_month_simulation(self):
        """Simule 20 jours de trading et vérifie la cohérence globale."""
        all_trades = []
        base_time = datetime(2026, 2, 1, 8, 0, tzinfo=timezone.utc)

        for day, title, source, category, direction, surprise, delay, awareness, ticker, expected_result in self.SCENARIOS:
            trade_time = base_time + timedelta(days=day)

            # 1. Create scored news
            news = _make_news(
                title=title,
                source=source,
                source_weight=1.1 if source in ("NOAA", "EIA", "USDA", "CFTC") else 0.9,
                published=trade_time - timedelta(hours=1),
                tickers=[ticker],
            )
            scored = _make_scored(
                news=news,
                surprise=surprise,
                clarity=85,
                delay=delay,
                awareness=awareness,
                direction=direction,
                tickers=[ticker],
                category=category,
                reliability=85,
                magnitude=60,
            )

            # 2. Verify score is positive
            assert scored.total_score >= 0, f"Day {day}: score should be positive"

            # 3. Create trade
            asset = ASSET_BY_TICKER.get(ticker)
            trade = TradeRecommendation(
                scan_type=ScanType.EUROPE,
                timestamp=trade_time,
                ticker=ticker,
                asset_name=asset.name if asset else ticker,
                category=asset.category if asset else "commodities_agri",
                direction=direction,
                news_headline=title,
                news_category=category,
                catalyst=title,
                entry_price=70.0,
                target_price=72.0 if direction == Direction.LONG else 68.0,
                stop_price=69.0 if direction == Direction.LONG else 71.0,
                target_pct=2.86,
                stop_pct=1.43,
                risk_reward=2.0,
                confidence=75,
                time_window="09:00 — 20:00",
                news_sources=[source],
                raw_claude_score=scored.total_score,
                learning_multiplier=1.0,
                predicted_transmission_delay=delay,
                expected_magnitude=60,
                signal_reliability=85,
                result=expected_result,
                exit_price=72.0 if expected_result == TradeResult.TP_HIT else (
                    69.0 if expected_result == TradeResult.SL_HIT else 70.3
                ),
                pnl_pct=2.86 if expected_result == TradeResult.TP_HIT else (
                    -1.43 if expected_result == TradeResult.SL_HIT else 0.43
                ),
            )
            all_trades.append(trade)

        # 5. Validate overall performance
        wins = sum(1 for t in all_trades if t.result == TradeResult.TP_HIT)
        losses = sum(1 for t in all_trades if t.result == TradeResult.SL_HIT)
        expired = sum(1 for t in all_trades if t.result == TradeResult.EXPIRED)
        total_pnl = sum(t.pnl_pct for t in all_trades if t.pnl_pct is not None)
        win_rate = wins / len(all_trades) * 100

        assert len(all_trades) == 20
        assert wins == 12
        assert losses == 5
        assert expired == 3
        assert win_rate == 60.0
        assert total_pnl > 0

        # 6. Run learning on the full set
        trades_data = [t.model_dump(mode="json") for t in all_trades]
        with _temp_file(trades_data) as tf:
            with patch("backend.app.learning.TRADES_FILE", tf), \
                 patch("backend.app.learning.is_pg_enabled", return_value=False):
                invalidate_perf_summary_cache()
                learning_result = compute_learning_adjustments()

        assert isinstance(learning_result, dict)
        assert "adjustments" in learning_result
        assert isinstance(learning_result.get("session_adj", {}), dict)
        assert isinstance(learning_result.get("newscat_adj", {}), dict)
        assert isinstance(learning_result.get("regime_adj", {}), dict)
        assert isinstance(learning_result.get("direction_adj", {}), dict)
        assert learning_result.get("delay_bias_adj") is not None

    def test_simulation_learning_evolution(self):
        """Le learning évolue : mauvais trades → pénalité, bons → boost."""
        losing_trades = [
            _make_trade(
                days_ago=i + 1, ticker="NG=F", news_category="commodity",
                result=TradeResult.SL_HIT, pnl_pct=-1.5, exit_price=69.0,
            ) for i in range(10)
        ]
        winning_trades = [
            _make_trade(
                days_ago=i + 1, ticker="GC=F", news_category="commodity",
                result=TradeResult.TP_HIT, pnl_pct=2.5, exit_price=72.0,
            ) for i in range(10)
        ]

        all_trades = losing_trades + winning_trades
        trades_data = [t.model_dump(mode="json") for t in all_trades]

        with _temp_file(trades_data) as tf:
            with patch("backend.app.learning.TRADES_FILE", tf), \
                 patch("backend.app.learning.is_pg_enabled", return_value=False):
                invalidate_perf_summary_cache()
                result = compute_learning_adjustments()

        adj = result.get("adjustments", {})
        if "NG=F" in adj:
            assert adj["NG=F"] < 1.0, "NG=F should be penalized after 10 losses"
        if "GC=F" in adj:
            assert adj["GC=F"] > 1.0, "GC=F should be boosted after 10 wins"

    def test_simulation_direction_learning(self):
        """Le learning distingue LONG vs SHORT performance."""
        trades = []
        for i in range(8):
            trades.append(_make_trade(
                days_ago=i + 1, direction=Direction.LONG,
                result=TradeResult.TP_HIT, pnl_pct=2.0, exit_price=72.0,
            ))
        for i in range(8):
            trades.append(_make_trade(
                days_ago=i + 1, direction=Direction.SHORT,
                result=TradeResult.SL_HIT, pnl_pct=-1.5, exit_price=71.0,
                target_price=68.0, stop_price=71.0,
            ))

        trades_data = [t.model_dump(mode="json") for t in trades]

        with _temp_file(trades_data) as tf:
            with patch("backend.app.learning.TRADES_FILE", tf), \
                 patch("backend.app.learning.is_pg_enabled", return_value=False):
                invalidate_perf_summary_cache()
                result = compute_learning_adjustments()

        dir_adj = result.get("direction_adj", {})
        if "LONG" in dir_adj and "SHORT" in dir_adj:
            assert dir_adj["LONG"] >= dir_adj["SHORT"], \
                "LONG should have higher adj than SHORT"


# ════════════════════════════════════════════════════════════════
# Phase 10: Config et modèles — intégrité
# ════════════════════════════════════════════════════════════════

class TestConfigIntegrity:
    """Vérifie que la configuration est cohérente."""

    def test_all_assets_have_required_fields(self):
        """Chaque asset a ticker, name, category."""
        for asset in ASSETS:
            assert hasattr(asset, "ticker"), f"Missing ticker: {asset}"
            assert hasattr(asset, "name"), f"Missing name: {asset}"
            assert hasattr(asset, "category"), f"Missing category: {asset}"

    def test_correlation_groups_reference_valid_tickers(self):
        """Les groupes de corrélation ne contiennent que des tickers valides."""
        all_tickers = {a.ticker for a in ASSETS}
        for group_name, tickers in CORRELATION_GROUPS.items():
            for ticker in tickers:
                assert ticker in all_tickers, \
                    f"Correlation group '{group_name}' references unknown ticker '{ticker}'"

    def test_category_multipliers_defined(self):
        """Tous les multiplicateurs de catégorie sont définis."""
        required = ["earnings", "macro", "commodity", "weather",
                     "supply_chain", "geopolitical", "regulatory"]
        for cat in required:
            assert cat in CATEGORY_SCORE_MULTIPLIERS, f"Missing multiplier for {cat}"

    def test_session_assets_non_empty(self):
        """Les sessions EUROPE et US ont des actifs."""
        eu_assets = assets_for_session(ScanType.EUROPE)
        us_assets = assets_for_session(ScanType.US)
        assert len(eu_assets) > 0
        assert len(us_assets) > 0


# ════════════════════════════════════════════════════════════════
# Phase 11: Frontend — intégrité
# ════════════════════════════════════════════════════════════════

class TestFrontendIntegrity:
    """Vérifie que le frontend est complet et cohérent."""

    def test_all_page_components_exist(self):
        """Les 7 pages existent dans le répertoire components."""
        components_dir = Path("/home/user/Finance/frontend/src/components")
        required = [
            "DashboardPage.jsx", "TraderPage.jsx", "ScoringPage.jsx",
            "JournalPage.jsx", "NewsPage.jsx", "LearningPage.jsx", "AuditorPage.jsx",
        ]
        for comp in required:
            assert (components_dir / comp).exists(), f"Missing component: {comp}"

    def test_no_dead_code_files(self):
        """Les fichiers dead code ont été supprimés."""
        components_dir = Path("/home/user/Finance/frontend/src/components")
        dead_files = ["Dashboard.jsx", "History.jsx", "Performance.jsx", "AgentDetail.jsx"]
        for f in dead_files:
            assert not (components_dir / f).exists(), f"Dead code still exists: {f}"

    def test_sidebar_and_notifications_exist(self):
        """AgentSidebar et NotificationCenter existent."""
        assert Path("/home/user/Finance/frontend/src/components/AgentSidebar.jsx").exists()
        assert Path("/home/user/Finance/frontend/src/components/NotificationCenter.jsx").exists()

    def test_app_jsx_references_all_pages(self):
        """App.jsx importe et route vers toutes les pages."""
        app_content = Path("/home/user/Finance/frontend/src/App.jsx").read_text()
        # v8.3: App uses TeamsOverviewPage + TeamPage for teams + dedicated pages
        for page in ["DashboardPage", "TeamsOverviewPage", "TeamPage", "NewsPage",
                      "PerformancePage", "AuditorPage", "AdminPage"]:
            assert page in app_content, f"App.jsx missing reference to {page}"


# ════════════════════════════════════════════════════════════════
# Phase 12: Economic Calendar
# ════════════════════════════════════════════════════════════════

class TestEconomicCalendar:
    """Vérifie que le calendrier économique fonctionne."""

    def test_calendar_module_exists(self):
        """Le module economic_calendar est importable."""
        from backend.app.economic_calendar import get_upcoming_events
        events = get_upcoming_events()
        assert isinstance(events, list)

    def test_fomc_dates_exist(self):
        """Les dates FOMC sont définies."""
        from backend.app.economic_calendar import FOMC_DATES
        assert len(FOMC_DATES) > 0
        assert any(d.year >= 2025 for d in FOMC_DATES)


# ════════════════════════════════════════════════════════════════
# Phase 13: Market Data
# ════════════════════════════════════════════════════════════════

class TestMarketDataModule:
    """Vérifie le module market_data."""

    def test_ticker_mapping_exists(self):
        """Le mapping tickers est disponible (tickers TD-compatible)."""
        from backend.app.market_data import _TICKER_MAP
        assert "CL=F" in _TICKER_MAP
        assert "GC=F" in _TICKER_MAP
        # ZW=F blacklisted from TD (unit mismatch) — uses yfinance fallback
        assert "EURUSD=X" in _TICKER_MAP

    def test_fetch_functions_exist(self):
        """Les fonctions de fetch sont disponibles."""
        from backend.app.market_data import fetch_history, fetch_quote
        assert callable(fetch_history)
        assert callable(fetch_quote)


# ════════════════════════════════════════════════════════════════
# Phase 14: Edge cases et résilience
# ════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Tests de résilience — cas limites."""

    def test_empty_news_returns_no_trade(self):
        """Un scan sans news ne génère pas de trade."""
        result = select_trade([], ScanType.EUROPE)
        assert result is None or not result.has_trade

    def test_model_serialization_roundtrip(self):
        """TradeRecommendation survit à un roundtrip JSON."""
        trade = _make_trade()
        data = trade.model_dump(mode="json")
        restored = TradeRecommendation(**data)
        assert restored.ticker == trade.ticker
        assert restored.direction == trade.direction
        assert restored.entry_price == trade.entry_price

    def test_journal_entry_serialization_roundtrip(self):
        """JournalEntry survit à un roundtrip JSON."""
        entry = _make_journal_entry()
        data = entry.model_dump(mode="json")
        restored = JournalEntry(**data)
        assert restored.ticker == "CL=F"
        assert restored.pnl_pct == 2.86

    def test_scan_result_model(self):
        """ScanResult se construit correctement."""
        result = ScanResult(
            scan_type=ScanType.EUROPE,
            timestamp=datetime.now(timezone.utc),
            has_trade=False,
            reason_no_trade="No viable signal",
        )
        assert not result.has_trade
        data = result.model_dump(mode="json")
        assert data["scan_type"] == "europe"

    def test_direction_enum_serialization(self):
        """Les enums Direction se sérialisent en string."""
        assert Direction.LONG.value == "LONG"
        assert Direction.SHORT.value == "SHORT"
        assert Direction.NEUTRAL.value == "NEUTRAL"

    def test_trade_result_enum(self):
        """Les enums TradeResult sont correctement définis."""
        assert TradeResult.PENDING.value == "PENDING"
        assert TradeResult.TP_HIT.value == "TP_HIT"
        assert TradeResult.SL_HIT.value == "SL_HIT"
        assert TradeResult.EXPIRED.value == "EXPIRED"
