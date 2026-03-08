"""Agent Registry — Central management of all trading agents.

Provides:
- Singleton instances of all agents
- Orchestration: run_scan() chains News → Scoring → Trader
- Status overview for the frontend
- Agent-specific API helpers
"""

import logging
import threading
from typing import Any

from .agent_auditor import AgentAuditor
from .agent_news import AgentNews
from .agent_scoring import AgentScoring
from .agent_scoring_2 import AgentScoring2
from .agent_trader import AgentTrader
from .agent_trader_2 import AgentTrader2
from .agent_journal import AgentJournal
from .agent_journal_2 import AgentJournal2
from .agent_learning import AgentLearning
from .agent_learning_2 import AgentLearning2

logger = logging.getLogger(__name__)

# ── Singleton agents ───────────────────────────────────────────────────

_agents: dict[str, Any] = {}
_init_lock = threading.Lock()


def _ensure_agents():
    """Lazily initialize all agents (singleton)."""
    global _agents
    if _agents:
        return
    with _init_lock:
        if _agents:
            return
        _agents = {
            "news": AgentNews(),
            "scoring": AgentScoring(),
            "scoring_2": AgentScoring2(),
            "trader_1": AgentTrader(),
            "trader_2": AgentTrader2(),
            "journal": AgentJournal(),
            "journal_2": AgentJournal2(),
            "learning": AgentLearning(),
            "learning_2": AgentLearning2(),
            "auditor": AgentAuditor(),
        }
        logger.info("Agent registry initialized: %s", list(_agents.keys()))


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
    """Execute the full scan pipeline: News → Scoring → Trader.

    This chains the 3 agents sequentially (each depends on previous output)
    while each agent logs its own activity independently.

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

    # Step 4: Agent Trader — Decide
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
            # Step 5a: Scoring 2 — re-weight scored news for trend relevance
            trend_scoring = None
            if agent_scoring2:
                trend_scoring = agent_scoring2.run(
                    scored_news=scored, scan_type=scan_type)

            # Step 5b: Trader 2 — trend evaluation with learning adjustments
            learning2_data = (agent_learning2.get_adjustments()
                              if agent_learning2 else {})
            agent_trader2.run(scored_news=scored, scan_type=scan_type,
                              learning_data=learning2_data,
                              trend_scoring=trend_scoring)
    except Exception as exc:
        logger.warning("Équipe 2 trend evaluation failed: %s", exc)

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
    """Run full learning update after journal — computes adjustments, detects anomalies, publishes."""
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

# ── Helpers ────────────────────────────────────────────────────────────

def _append_scan_result(result_dict: dict, news_result: dict):
    """Persist scan result to history."""
    try:
        from ..scan_history import append_scan_result
        append_scan_result(result_dict)
    except Exception as exc:
        logger.error("Failed to append scan result: %s", exc)
