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
import time
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

# P1 (v7.8): Staggered initialization — agents are grouped into waves
# with a short sleep between each wave. This prevents 21 agents from
# all hitting PG simultaneously at startup, which exhausts the connection pool.
_INIT_WAVE_DELAY_S = 0.3  # seconds between waves

_AGENT_WAVES = [
    # Wave 1: Shared agents (News + Scoring) — needed by all teams
    [
        ("news", AgentNews),
        ("scoring", AgentScoring),
    ],
    # Wave 2: Équipe 1 — Intraday
    [
        ("trader_1", AgentTrader),
        ("journal", AgentJournal),
        ("learning", AgentLearning),
    ],
    # Wave 3: Équipe 2 — Trend
    [
        ("scoring_2", AgentScoring2),
        ("trader_2", AgentTrader2),
        ("journal_2", AgentJournal2),
        ("learning_2", AgentLearning2),
    ],
    # Wave 4: Équipe 3 — Technical Indicators
    [
        ("scoring_3", AgentScoring3),
        ("trader_3", AgentTrader3),
        ("journal_3", AgentJournal3),
        ("learning_3", AgentLearning3),
    ],
    # Wave 5: Équipe 4 — Meta/Ensemble
    [
        ("scoring_4", AgentScoring4),
        ("trader_4", AgentTrader4),
        ("journal_4", AgentJournal4),
        ("learning_4", AgentLearning4),
    ],
    # Wave 6: Infrastructure agents (non-trading, can wait)
    [
        ("infrastructure", AgentInfrastructure),
        ("performance", AgentPerformance),
        ("auditor", AgentAuditor),
    ],
]


def _ensure_agents():
    """Lazily initialize all agents (singleton) in staggered waves.

    P1 (v7.8): Agents are initialized in 6 waves with a short delay between
    each wave. This prevents 21 agents from simultaneously exhausting the
    PG connection pool at startup (Replit pool_max=30, 21 agents = thundering herd).

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
        result = {}
        total_agents = sum(len(wave) for wave in _AGENT_WAVES)
        for wave_idx, wave in enumerate(_AGENT_WAVES):
            for name, cls in wave:
                try:
                    result[name] = cls()
                except Exception as exc:
                    logger.error("Failed to initialize agent '%s': %s", name, exc)
            # Sleep between waves to let PG connections return to pool
            if wave_idx < len(_AGENT_WAVES) - 1:
                time.sleep(_INIT_WAVE_DELAY_S)
        _agents = result
        logger.info("Agent registry initialized: %d/%d agents (6 waves) — %s",
                     len(_agents), total_agents, list(_agents.keys()))


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

    # v7.7: Detect API failure — don't return early, let Teams 2-4 run
    # (Team 3 is fully independent of news scoring, Team 2 has its own Scoring 2)
    scoring_api_failed = False
    if not scored:
        # Differentiate between API failure types
        error_type = scoring_result.get("error_type", "")
        error_msg = scoring_result.get("error_message", "")
        if "Timeout" in error_type or "timeout" in error_msg.lower():
            reason = f"Timeout API Claude — réessai au prochain scan ({error_type})"
        elif "Authentication" in error_type or "authentication" in error_msg.lower():
            reason = "API Claude hors service — clé API invalide ou crédits épuisés"
            error_type = "AuthenticationError"
        elif "RateLimit" in error_type or "rate" in error_msg.lower():
            reason = "API Claude — limite de requêtes atteinte"
            error_type = "RateLimitError"
        elif error_type:
            reason = f"Erreur API Claude ({error_type}): {error_msg[:200]}"
        else:
            reason = "Aucune news scorée — toutes filtrées ou cache vide"

        if error_type:
            scoring_api_failed = True
            logger.error("Scoring API failure: %s — Teams 2-4 will still execute", reason)

        result_dict = {
            "scan_type": scan_type.value,
            "has_trade": False,
            "reason_no_trade": reason,
            "news_analyzed": len(news_items),
            "all_scored_news": [],
        }
        if error_type:
            result_dict["api_error"] = error_type
    else:
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
        # v6.5 P9: Wrap Trader 1 in try/except so Teams 2-4 always execute.
        try:
            result_dict = agent_trader.run(
                scored, scan_type, learning_data,
                existing_trade_ticker=existing_trade_ticker,
                market_context=market_ctx,
            )
        except Exception as exc:
            logger.error("Équipe 1 Trader failed: %s — pipeline continues to Teams 2-4", exc)
            result_dict = {
                "scan_type": scan_type.value,
                "has_trade": False,
                "reason_no_trade": f"Trader 1 error: {str(exc)[:200]}",
                "news_analyzed": len(news_items),
                "all_scored_news": [],
            }

    # v8.5: Capture team results for dashboard multi-team display
    team_results = {}

    # Step 5: Équipe 2 — Scoring 2 (dedicated Claude) + Trader 2 (non-blocking)
    # v8.0: Scoring 2 now makes its own Claude API call with raw news_items
    # (no longer consumes Scoring 1 output)
    try:
        agent_scoring2 = _agents.get("scoring_2")
        agent_trader2 = _agents.get("trader_2")
        agent_learning2 = _agents.get("learning_2")
        if agent_trader2:
            trend_scoring = None
            if agent_scoring2 and news_items:
                trend_scoring = agent_scoring2.run(
                    news_items=news_items, scan_type=scan_type)

            learning2_data = (agent_learning2.get_adjustments()
                              if agent_learning2 else {})
            t2_result = agent_trader2.run(
                scored_news=scored, scan_type=scan_type,
                learning_data=learning2_data,
                trend_scoring=trend_scoring)
            changes_2 = t2_result.get("changes", []) if t2_result else []
            team_results["team_2"] = {
                "has_activity": len(changes_2) > 0,
                "changes": [
                    {"ticker": c.get("ticker"), "old_direction": c.get("old_direction"),
                     "new_direction": c.get("new_direction"), "reason": c.get("reason", "")[:120]}
                    for c in changes_2
                ],
                "news_evaluated": t2_result.get("news_evaluated", 0) if t2_result else 0,
            }
    except Exception as exc:
        logger.warning("Équipe 2 trend evaluation failed: %s", exc)
        team_results["team_2"] = {"has_activity": False, "error": str(exc)[:100]}

    # Step 6: Équipe 3 — Scoring 3 + Trader 3 (non-blocking)
    try:
        agent_scoring3 = _agents.get("scoring_3")
        agent_trader3 = _agents.get("trader_3")
        agent_learning3 = _agents.get("learning_3")
        if agent_scoring3 and agent_trader3:
            # Get weekly config and learning adjustments
            weekly_config = (agent_learning3.get_weekly_config()
                             if agent_learning3 else None)
            learning3_data = (agent_learning3.get_adjustments()
                              if agent_learning3 else {})
            # Pass weekly_config to Scoring 3 (strategy filtering, params)
            tech_scoring = agent_scoring3.run(
                scan_type=scan_type,
                weekly_config=weekly_config)
            # Pass weekly_config and learning to Trader 3
            t3_result = agent_trader3.run(
                tech_scoring=tech_scoring,
                scan_type=scan_type,
                learning_data=learning3_data,
                weekly_config=weekly_config)
            new_opened_3 = t3_result.get("new_opened", []) if t3_result else []
            newly_closed_3 = t3_result.get("newly_closed", []) if t3_result else []
            team_results["team_3"] = {
                "has_activity": len(new_opened_3) > 0 or len(newly_closed_3) > 0,
                "new_positions": [
                    {"ticker": p.get("ticker"), "direction": p.get("direction"),
                     "strategy": p.get("strategy"), "score": p.get("score")}
                    for p in new_opened_3
                ],
                "closed_positions": [
                    {"ticker": p.get("ticker"), "direction": p.get("direction"),
                     "strategy": p.get("strategy"), "result": p.get("result"),
                     "pnl_pct": p.get("pnl_pct")}
                    for p in newly_closed_3
                ],
                "active_count": len(t3_result.get("active", [])) if t3_result else 0,
            }
    except Exception as exc:
        logger.warning("Équipe 3 technical evaluation failed: %s", exc)
        team_results["team_3"] = {"has_activity": False, "error": str(exc)[:100]}

    # v8.6: Always read live Team 3 position count — catches cases where
    # positions were saved to disk but run() raised after save, or where
    # team_results["team_3"] was never set (agent init failed).
    try:
        from .agent_trader_3 import _load_positions as _t3_load
        t3_live = _t3_load()
        t3_active = t3_live.get("active", [])
        if "team_3" not in team_results:
            team_results["team_3"] = {"has_activity": False}
        team_results["team_3"]["active_count"] = len(t3_active)
        # If positions exist but has_activity is False, flag it
        if len(t3_active) > 0 and not team_results["team_3"].get("has_activity"):
            team_results["team_3"]["has_activity"] = True
    except Exception:
        pass  # Best effort — don't break pipeline for dashboard data

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

            # Get weekly config and learning adjustments from Learning 4
            weekly_config_4 = (agent_learning4.get_weekly_config()
                               if agent_learning4 else None)
            learning4_data = (agent_learning4.get_adjustments()
                              if agent_learning4 else {})

            # Pass weekly_config to Scoring 4 (weight optimization)
            meta_scoring = agent_scoring4.run(
                news_data=scoring_result,
                trend_data=scoring2_result,
                tech_data=scoring3_result,
                scan_type=scan_type,
                weekly_config=weekly_config_4,
            )
            # Pass weekly_config and learning to Trader 4
            t4_result = agent_trader4.run(
                meta_scoring=meta_scoring,
                scan_type=scan_type,
                learning_data=learning4_data,
                weekly_config=weekly_config_4)
            changes_4 = t4_result.get("changes", []) if t4_result else []
            team_results["team_4"] = {
                "has_activity": len(changes_4) > 0,
                "changes": [
                    {"ticker": c.get("ticker"), "action": c.get("action"),
                     "direction": c.get("direction")}
                    for c in changes_4
                ],
                "open_positions": sum(
                    1 for p in (t4_result.get("positions", {}) if t4_result else {}).values()
                    if isinstance(p, dict) and p.get("status") == "OPEN"
                ),
            }
    except Exception as exc:
        logger.warning("Équipe 4 meta evaluation failed: %s", exc)
        team_results["team_4"] = {"has_activity": False, "error": str(exc)[:100]}

    # v8.6: Always read live Team 4 position count (same pattern as Team 3)
    try:
        from .agent_trader_4 import _load_positions as _t4_load
        t4_live = _t4_load()
        t4_open = sum(1 for p in t4_live.values()
                      if isinstance(p, dict) and p.get("status") == "OPEN")
        if "team_4" not in team_results:
            team_results["team_4"] = {"has_activity": False}
        team_results["team_4"]["open_positions"] = t4_open
        if t4_open > 0 and not team_results["team_4"].get("has_activity"):
            team_results["team_4"]["has_activity"] = True
    except Exception:
        pass

    # v7.7: Flag scoring API failure in result for frontend/dashboard visibility
    if scoring_api_failed:
        result_dict["scoring_api_failed"] = True

    # Attach team results to scan output
    result_dict["team_results"] = team_results

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
    """Monitor positions via Agent Trader 1."""
    _ensure_agents()
    return _agents["trader_1"].run_position_monitor()


def run_position_monitor_3(force_close_all: bool = False) -> dict:
    """V1: Monitor positions via Agent Trader 3 (TP/SL/trailing).

    Args:
        force_close_all: v3.0 — force-close all positions (EOD deadline).
    """
    _ensure_agents()
    return _agents["trader_3"].run_position_monitor(force_close_all=force_close_all)


def run_position_monitor_4() -> dict:
    """V1: Monitor positions via Agent Trader 4 (TP/SL/trailing)."""
    _ensure_agents()
    return _agents["trader_4"].run_position_monitor()


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


def generate_weekly_config_3() -> dict:
    """Generate weekly strategy config for Team 3 (called Sunday)."""
    _ensure_agents()
    return _agents["learning_3"].generate_weekly_config()


def get_weekly_config_3() -> dict | None:
    """Get current weekly config for Team 3."""
    _ensure_agents()
    return _agents["learning_3"].get_weekly_config()


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


def generate_weekly_config_4() -> dict:
    """Generate weekly strategy config for Team 4 (called Sunday)."""
    _ensure_agents()
    return _agents["learning_4"].generate_weekly_config()


def get_weekly_config_4() -> dict | None:
    """Get current weekly config for Team 4."""
    _ensure_agents()
    return _agents["learning_4"].get_weekly_config()


# ── P3.9: Centralized learning cache invalidation ─────────────────────


def invalidate_all_learning_caches():
    """P3.9: Invalidate ALL learning caches in one call.

    Called after all journal runs complete. Publishes a bus message
    so any future consumer can react to the event.
    """
    _ensure_agents()
    for key in ("learning", "learning_2", "learning_3", "learning_4"):
        try:
            _agents[key].invalidate_cache()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Failed to invalidate %s cache: %s", key, exc)
    # Publish bus message for any downstream consumers
    try:
        from datetime import datetime, timezone
        _agents["learning"].publish("journal_all_complete", {
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        pass


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
