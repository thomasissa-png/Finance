"""Agent Registry — Central management of all trading agents.

Provides:
- Singleton instances of all agents
- Orchestration: run_scan_pipeline chains News → Scoring → Teams
- Status overview for the frontend
- Agent-specific API helpers

4 Teams:
- Shared: News, Scoring (feed all teams)
- Équipe 1: Intraday day trading (Scoring → Trader 1, Journal 1, Learning 1)
- Équipe 2: Trend following commodities (Scoring 2 → Trader 2, Journal 2, Learning 2)
- Équipe 3: Technical indicators (Scoring 3 → Trader 3, Journal 3, Learning 3)
- Équipe 4: Meta/ensemble (Scoring 4 → Trader 4, Journal 4, Learning 4)
"""

import logging
import threading
from typing import Any

from .agent_auditor import AgentAuditor
from .agent_infrastructure import AgentInfrastructure
from .agent_performance import AgentPerformance
from .agent_news import AgentNews
from .agent_scoring import AgentScoring
from .agent_scoring_2 import AgentScoring2
from .agent_scoring_3 import AgentScoring3
from .agent_scoring_4 import AgentScoring4
from .agent_trader import AgentTrader
from .agent_trader_2 import AgentTrader2
from .agent_trader_3 import AgentTrader3
from .agent_trader_4 import AgentTrader4
from .agent_journal import AgentJournal
from .agent_journal_2 import AgentJournal2
from .agent_journal_3 import AgentJournal3
from .agent_journal_4 import AgentJournal4
from .agent_learning import AgentLearning
from .agent_learning_2 import AgentLearning2
from .agent_learning_3 import AgentLearning3
from .agent_learning_4 import AgentLearning4

logger = logging.getLogger(__name__)

# ── Singleton agents ───────────────────────────────────────────────────

_agents: dict[str, Any] = {}
_init_lock = threading.Lock()


def _ensure_agents():
    """Lazily initialize all agents (singleton).

    Each agent is initialized independently — if one fails, the others
    still start. This prevents a single broken agent from killing the
    entire system.
    """
    global _agents
    if _agents:
        return
    with _init_lock:
        if _agents:
            return
        agent_classes = [
            # Shared
            ("news", AgentNews),
            ("scoring", AgentScoring),
            # Équipe 1 — Intraday
            ("trader_1", AgentTrader),
            ("journal", AgentJournal),
            ("learning", AgentLearning),
            # Équipe 2 — Trend
            ("scoring_2", AgentScoring2),
            ("trader_2", AgentTrader2),
            ("journal_2", AgentJournal2),
            ("learning_2", AgentLearning2),
            # Équipe 3 — Technical Indicators
            ("scoring_3", AgentScoring3),
            ("trader_3", AgentTrader3),
            ("journal_3", AgentJournal3),
            ("learning_3", AgentLearning3),
            # Équipe 4 — Meta/Ensemble
            ("scoring_4", AgentScoring4),
            ("trader_4", AgentTrader4),
            ("journal_4", AgentJournal4),
            ("learning_4", AgentLearning4),
            # Infrastructure
            ("infrastructure", AgentInfrastructure),
            ("performance", AgentPerformance),
            ("auditor", AgentAuditor),
        ]
        result = {}
        for name, cls in agent_classes:
            try:
                result[name] = cls()
            except Exception as exc:
                logger.error("Failed to initialize agent '%s': %s", name, exc)
        _agents = result
        logger.info("Agent registry initialized: %d/%d agents — %s",
                     len(_agents), len(agent_classes), list(_agents.keys()))


def get_agent(name: str):
    """Get an agent by name."""
    _ensure_agents()
    return _agents.get(name)


def get_all_agents() -> dict:
    """Get all agents."""
    _ensure_agents()
    return _agents


def get_all_status() -> list[dict]:
    """Get status + metrics for all agents (frontend overview)."""
    _ensure_agents()
    result = []
    for name, agent in _agents.items():
        status = agent.status
        status["metrics"] = agent.get_metrics()
        result.append(status)
    # Add UX agent (virtual — represents the frontend)
    result.append({
        "name": "ux",
        "description": "Frontend & expérience utilisateur",
        "version": "8.0",
        "status": "idle",
        "last_action": None,
        "last_action_time": None,
        "last_error": None,
        "action_count": 0,
        "metrics": {},
    })
    return result


# ── Orchestration ──────────────────────────────────────────────────────

def run_scan_pipeline(scan_type, existing_trade_ticker=None) -> dict:
    """Execute the full scan pipeline: News → Scoring → Teams.

    Chains agents sequentially within each team, teams run in sequence:
    1. News → Scoring (shared)
    2. Équipe 1: Learning 1 cache → Trader 1
    3. Équipe 2: Scoring 2 → Learning 2 cache → Trader 2
    4. Équipe 3: Scoring 3 → Learning 3 cache → Trader 3
    5. Équipe 4: Scoring 4 → Learning 4 cache → Trader 4

    Args:
        scan_type: ScanType enum (EUROPE / US)
        existing_trade_ticker: Tickers already traded today

    Returns: Full scan result dict
    """
    _ensure_agents()

    agent_news = _agents["news"]
    agent_scoring = _agents["scoring"]
    agent_trader = _agents["trader_1"]
    agent_learning = _agents["learning"]

    from datetime import datetime, timezone

    # Step 1: Agent News — Collect
    news_result = agent_news.run(scan_type=scan_type)
    news_items = news_result.get("news_items", [])

    if not news_items:
        return {
            "scan_type": scan_type.value,
            "has_trade": False,
            "reason_no_trade": "Aucune news collectée",
            "news_analyzed": 0,
        }

    # Log headlines for traceability
    logger.info("--- Headlines collected (%d) ---", len(news_items))
    for i, item in enumerate(news_items[:20], 1):
        age = ""
        if item.published:
            age_h = (datetime.now(timezone.utc) - item.published).total_seconds() / 3600
            age = f" [{age_h:.1f}h ago]"
        logger.info("  [%d] %s (src: %s, weight: %.2f)%s",
                     i, item.title, item.source, item.source_weight, age)

    # Step 2: Agent Scoring — Score
    scoring_result = agent_scoring.run(news_items, scan_type)
    scored = scoring_result.get("scored", [])
    market_ctx = scoring_result.get("market_context")

    if not scored:
        reason = "API Claude hors service — les news n'ont pas pu être scorées"
        result_dict = {
            "scan_type": scan_type.value,
            "has_trade": False,
            "reason_no_trade": reason,
            "news_analyzed": len(news_items),
            "all_scored_news": [],
        }
        _append_scan_result(result_dict, news_result)
        return result_dict

    # Log scored results
    logger.info("--- Scored news (%d) ---", len(scored))
    for s in scored:
        logger.info("  score=%-6.1f dir=%-7s cat=%-15s delay=%-3d aware=%-3d | %s",
                     s.total_score, s.direction.value,
                     s.news_category, s.transmission_delay,
                     s.market_awareness, s.news.title[:100])

    # Step 3: Agent Learning — Get adjustments
    learning_data = agent_learning.get_adjustments()

    # Step 4: Agent Trader 1 — Decide (Équipe 1)
    result_dict = agent_trader.run(
        scored, scan_type, learning_data,
        existing_trade_ticker=existing_trade_ticker,
        market_context=market_ctx,
    )

    # Step 5: Équipe 2 — Scoring 2 + Trader 2 (non-blocking)
    try:
        agent_scoring2 = _agents.get("scoring_2")
        agent_trader2 = _agents.get("trader_2")
        agent_learning2 = _agents.get("learning_2")
        if agent_trader2 and scored:
            trend_scoring = None
            if agent_scoring2:
                trend_scoring = agent_scoring2.run(
                    scored_news=scored, scan_type=scan_type)

            learning2_data = (agent_learning2.get_adjustments()
                              if agent_learning2 else {})
            agent_trader2.run(scored_news=scored, scan_type=scan_type,
                              learning_data=learning2_data,
                              trend_scoring=trend_scoring)
    except Exception as exc:
        logger.warning("Équipe 2 trend evaluation failed: %s", exc)

    # Step 6: Équipe 3 — Scoring 3 + Trader 3 (non-blocking)
    try:
        agent_scoring3 = _agents.get("scoring_3")
        agent_trader3 = _agents.get("trader_3")
        agent_learning3 = _agents.get("learning_3")
        if agent_scoring3 and agent_trader3:
            tech_scoring = agent_scoring3.run(scan_type=scan_type)
            learning3_data = (agent_learning3.get_adjustments()
                              if agent_learning3 else {})
            agent_trader3.run(tech_scoring=tech_scoring,
                              scan_type=scan_type,
                              learning_data=learning3_data)
    except Exception as exc:
        logger.warning("Équipe 3 technical evaluation failed: %s", exc)

    # Step 7: Équipe 4 — Scoring 4 + Trader 4 (non-blocking, needs data from teams 1-3)
    try:
        agent_scoring4 = _agents.get("scoring_4")
        agent_trader4 = _agents.get("trader_4")
        agent_learning4 = _agents.get("learning_4")
        if agent_scoring4 and agent_trader4:
            # Collect outputs from all scoring agents
            scoring1_output = scoring_result
            scoring2_output = _agents.get("scoring_2", None)
            scoring2_result = (scoring2_output.get_last_result()
                               if scoring2_output else None)
            scoring3_output = _agents.get("scoring_3", None)
            scoring3_result = (scoring3_output.get_last_result()
                               if scoring3_output else None)

            meta_scoring = agent_scoring4.run(
                scoring1_output=scoring_result,
                scoring2_output=scoring2_result,
                scoring3_output=scoring3_output.get_last_result() if scoring3_output else None,
                scan_type=scan_type,
            )
            learning4_data = (agent_learning4.get_adjustments()
                              if agent_learning4 else {})
            agent_trader4.run(meta_scoring=meta_scoring,
                              scan_type=scan_type,
                              learning_data=learning4_data)
    except Exception as exc:
        logger.warning("Équipe 4 meta evaluation failed: %s", exc)

    # Attach source health
    source_health = news_result.get("source_health")
    if source_health:
        result_dict["source_health"] = source_health

    # Persist to scan history
    _append_scan_result(result_dict, news_result)

    return result_dict


def run_daily_journal() -> dict:
    """Execute the daily journal via Agent Journal."""
    _ensure_agents()
    return _agents["journal"].run()


def run_journal_recovery():
    """Startup recovery — close old PENDING trades."""
    _ensure_agents()
    return _agents["journal"].recover_pending()


def run_event_check() -> dict | None:
    """Check for high-impact signals via Agent News."""
    _ensure_agents()
    return _agents["news"].run_event_check()


def run_weekly_review() -> dict | None:
    """Weekly source review via Agent News."""
    _ensure_agents()
    return _agents["news"].run_weekly_review()


def run_position_monitor() -> dict:
    """Monitor positions via Agent Trader."""
    _ensure_agents()
    return _agents["trader_1"].run_position_monitor()


def run_learning_update() -> dict:
    """Run full learning update after journal."""
    _ensure_agents()
    return _agents["learning"].run()


def invalidate_learning_cache():
    """Invalidate learning cache after journal."""
    _ensure_agents()
    _agents["learning"].invalidate_cache()


def get_learning_adjustments() -> dict:
    """Get cached learning adjustments."""
    _ensure_agents()
    return _agents["learning"].get_adjustments()


# ── Équipe 2 helpers ──────────────────────────────────────────────────

def run_daily_journal_2() -> dict:
    """Execute the daily journal for Trader 2 via Agent Journal 2."""
    _ensure_agents()
    return _agents["journal_2"].run()


def run_learning_2_update() -> dict:
    """Run trend learning update after journal 2."""
    _ensure_agents()
    return _agents["learning_2"].run()


def invalidate_learning_2_cache():
    """Invalidate trend learning cache after journal 2."""
    _ensure_agents()
    _agents["learning_2"].invalidate_cache()


def get_learning_2_adjustments() -> dict:
    """Get cached trend learning adjustments."""
    _ensure_agents()
    return _agents["learning_2"].get_adjustments()


# ── Équipe 3 helpers ──────────────────────────────────────────────────

def run_daily_journal_3() -> dict:
    """Execute the daily journal for Trader 3 via Agent Journal 3."""
    _ensure_agents()
    return _agents["journal_3"].run()


def run_learning_3_update() -> dict:
    """Run tech learning update after journal 3."""
    _ensure_agents()
    return _agents["learning_3"].run()


def invalidate_learning_3_cache():
    """Invalidate tech learning cache after journal 3."""
    _ensure_agents()
    _agents["learning_3"].invalidate_cache()


def get_learning_3_adjustments() -> dict:
    """Get cached tech learning adjustments."""
    _ensure_agents()
    return _agents["learning_3"].get_adjustments()


# ── Équipe 4 helpers ──────────────────────────────────────────────────

def run_daily_journal_4() -> dict:
    """Execute the daily journal for Trader 4 via Agent Journal 4."""
    _ensure_agents()
    return _agents["journal_4"].run()


def run_learning_4_update() -> dict:
    """Run meta learning update after journal 4."""
    _ensure_agents()
    return _agents["learning_4"].run()


def invalidate_learning_4_cache():
    """Invalidate meta learning cache after journal 4."""
    _ensure_agents()
    _agents["learning_4"].invalidate_cache()


def get_learning_4_adjustments() -> dict:
    """Get cached meta learning adjustments."""
    _ensure_agents()
    return _agents["learning_4"].get_adjustments()


# ── Infrastructure helpers ─────────────────────────────────────────────

def run_infra_health_check() -> dict:
    """Run infrastructure health check."""
    _ensure_agents()
    return _agents["infrastructure"].run(action="health_check")


def run_infra_maintenance() -> dict:
    """Run infrastructure maintenance (VACUUM, pruning, stats)."""
    _ensure_agents()
    return _agents["infrastructure"].run(action="maintenance")


def run_infra_report() -> dict:
    """Run full infrastructure report."""
    _ensure_agents()
    return _agents["infrastructure"].run(action="full_report")


def run_performance_snapshot() -> dict:
    """Run hourly performance snapshot."""
    _ensure_agents()
    return _agents["performance"].run(action="snapshot")


def run_performance_daily() -> dict:
    """Run daily performance report (after journal)."""
    _ensure_agents()
    return _agents["performance"].run(action="daily_report")


def run_performance_weekly() -> dict:
    """Run weekly performance trends."""
    _ensure_agents()
    return _agents["performance"].run(action="weekly_trends")


# ── Helpers ────────────────────────────────────────────────────────────

def _append_scan_result(result_dict: dict, news_result: dict):
    """Persist scan result to history."""
    try:
        from ..scan_history import append_scan_result
        append_scan_result(result_dict)
    except Exception as exc:
        logger.error("Failed to append scan result: %s", exc)
