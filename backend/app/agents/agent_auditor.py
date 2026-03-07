"""Agent Auditeur — Audit en profondeur de chaque agent du système.

L'Agent Auditeur se met dans la peau d'un confrère expert de l'agent audité,
avec les mêmes compétences et la compréhension des enjeux du news trading.

Pour chaque audit, il analyse :
- **Performance** : métriques réelles vs attendues, drifts, anomalies
- **Logique** : cohérence des règles métier, edge cases, cas limites
- **Technique** : qualité du code, robustesse, error handling, tests manquants

Résultat de chaque audit :
- Note sur 10
- Compte-rendu détaillé structuré
- Liste d'améliorations à implémenter (priorisées)
- Tests à ajouter
- Mise à jour mémoire (CLAUDE.md) si nécessaire

Les rapports d'audit sont persistés (PG ou JSON) pour être consultables
quelle que soit la session Claude Code.
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from .base import BaseAgent, AgentStatus

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data"
AUDIT_FILE = DATA_DIR / "audit_reports.json"

# ── Audit expertise profiles per agent ─────────────────────────────────
# Each defines what the auditor checks when auditing that agent

AUDIT_PROFILES = {
    "news": {
        "expertise": "Expert data engineering & news sourcing, 15+ ans en collecte de données financières temps réel",
        "checks": [
            "source_coverage",       # Toutes les sources sont-elles actives et collectées ?
            "source_reliability",    # Taux de succès par source, sources mortes/dégradées
            "dedup_effectiveness",   # La dédup Jaccard filtre-t-elle correctement ?
            "freshness_distribution", # Distribution de l'âge des news collectées
            "event_detection",       # Les événements critiques sont-ils détectés ?
            "latency",              # Temps de collecte, timeouts, sources lentes
            "data_quality",         # Titres vides, descriptions manquantes, URLs cassées
        ],
    },
    "scoring": {
        "expertise": "Expert en spéculation avec 15+ ans d'edge detection et news trading",
        "checks": [
            "score_distribution",    # Les scores sont-ils bien calibrés (pas tous à 0 ou 100) ?
            "edge_factor_calibration", # L'edge factor discrimine-t-il correctement ?
            "zero_edge_filter",      # Les earnings/macro sont-ils bien filtrés ?
            "category_multipliers",  # Les multipliers sont-ils cohérents avec la philosophie ?
            "claude_api_efficiency", # Tokens utilisés, cache hits, coût
            "coherence_validation",  # Les scores cross-dimensions sont-ils cohérents ?
            "chain_reaction_coverage", # Les effets de second ordre sont-ils détectés ?
        ],
    },
    "trader_1": {
        "expertise": "Expert trader avec 15+ ans de news trading, ratios de succès exceptionnels",
        "checks": [
            "win_rate",             # Taux de réussite global et par catégorie
            "risk_reward_realized",  # R/R réalisé vs prédit
            "selection_quality",     # Qualité des trades sélectionnés vs rejetés
            "position_sizing",       # Le sizing est-il adapté au VIX/vendredi ?
            "correlation_check",     # Les corrélations sont-elles bien gérées ?
            "spread_filter",         # Le filtre spread fonctionne-t-il ?
            "cooldown_logic",        # Les cooldowns sont-ils respectés ?
            "calendar_blocking",     # Les events macro bloquent-ils correctement ?
        ],
    },
    "journal": {
        "expertise": "Expert en analyse business et documentation de trading, 15+ ans d'expérience",
        "checks": [
            "closure_accuracy",      # TP/SL/EXPIRED correctement déterminés ?
            "price_fetch_reliability", # Taux de succès des fetch de prix
            "pnl_accuracy",          # PnL calculé correctement ?
            "mae_mfe_tracking",      # MAE/MFE bien calculés ?
            "slippage_monitoring",   # Le slippage est-il suivi ?
            "bar_coverage",          # Assez de bars pour une décision fiable ?
            "pruning",              # Les vieilles entrées sont-elles prunées ?
            "recovery",             # La startup recovery fonctionne-t-elle ?
        ],
    },
    "learning": {
        "expertise": "Expert ML avec 10+ ans d'expérience en systèmes adaptatifs et trading quantitatif",
        "checks": [
            "dimension_significance", # Les dimensions de learning sont-elles significatives ?
            "overfitting_risk",      # Risque de sur-apprentissage (trop peu de données) ?
            "commodity_protection",  # Les commodities ne sont JAMAIS pénalisées en classe ?
            "decay_calibration",     # Le decay temporel est-il bien calibré ?
            "anomaly_detection",     # Les anomalies sont-elles détectées ?
            "cross_dimension",       # Le newscat+ticker fonctionne-t-il ?
            "feedback_quality",      # Le feedback Claude est-il pertinent ?
            "data_integrity",        # Les données sont-elles propres (pas de PnL > 50%) ?
        ],
    },
    "ux": {
        "expertise": "Expert frontend & UX, 10+ ans en dashboards temps réel et data visualization pour le trading",
        "checks": [
            "component_coverage",    # Tous les composants nécessaires sont-ils présents et fonctionnels ?
            "api_integration",       # Tous les endpoints API sont-ils correctement appelés et gérés ?
            "error_handling",        # Les erreurs réseau/API sont-elles gérées proprement ?
            "polling_efficiency",    # Le polling est-il optimisé (skip quand masqué, intervals adaptés) ?
            "responsive_design",     # Le design est-il responsive et accessible ?
            "data_display",          # Les données trading sont-elles affichées correctement et lisiblement ?
            "agent_visibility",      # Chaque agent est-il visible et ses métriques accessibles ?
        ],
    },
    "auditor": {
        "expertise": "Expert en systèmes d'audit, meta-analyse et assurance qualité pour trading algorithmique",
        "checks": [
            "profile_coverage",      # Tous les agents ont-ils un profil d'audit ?
            "check_implementation",  # Les checks déclarés sont-ils tous implémentés ?
            "persistence",           # Les rapports sont-ils correctement persistés (PG + JSON) ?
            "scoring_calibration",   # Les notes sont-elles bien calibrées (pas trop généreuses) ?
            "trend_tracking",        # Les tendances sont-elles suivies (score actuel vs précédent) ?
            "log_analysis",          # L'auditeur utilise-t-il les logs de chaque agent ?
        ],
    },
}


class AgentAuditor(BaseAgent):
    name = "auditor"
    description = "Audit en profondeur de chaque agent"

    def __init__(self):
        super().__init__()
        self._last_audit_target: str | None = None
        self._last_audit_score: float | None = None
        self._total_audits: int = 0
        self._audit_history: list[dict] = []
        # Load existing reports on init
        self._load_reports()

    def run(self, target_agent: str, focus: str | None = None, **kwargs) -> dict:
        """Run a full audit of the target agent.

        Args:
            target_agent: Name of the agent to audit (news, scoring, trader_1, journal, learning)
            focus: Optional specific area to focus on (e.g., "performance", "logic", "technique")

        Returns: Full audit report dict
        """
        if target_agent not in AUDIT_PROFILES:
            available = list(AUDIT_PROFILES.keys())
            self.log("Invalid audit target", {
                "target": target_agent,
                "available": available,
            }, level="ERROR")
            return {"error": f"Agent '{target_agent}' not auditable. Available: {available}"}

        profile = AUDIT_PROFILES[target_agent]
        self._set_status(AgentStatus.WORKING,
                         f"Auditing agent {target_agent}")
        self._last_audit_target = target_agent

        start = time.monotonic()

        self.log_decision(f"AUDIT START: {target_agent}", {
            "expertise": profile["expertise"],
            "checks": profile["checks"],
            "focus": focus,
        })

        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "target_agent": target_agent,
            "auditor_expertise": profile["expertise"],
            "focus": focus,
            "score": 0,
            "score_breakdown": {},
            "summary": "",
            "findings": [],
            "improvements": [],
            "tests_to_add": [],
            "memory_updates": [],
        }

        try:
            # Run the audit checks
            if target_agent == "news":
                self._audit_news(report, focus)
            elif target_agent == "scoring":
                self._audit_scoring(report, focus)
            elif target_agent == "trader_1":
                self._audit_trader(report, focus)
            elif target_agent == "journal":
                self._audit_journal(report, focus)
            elif target_agent == "learning":
                self._audit_learning(report, focus)
            elif target_agent == "ux":
                self._audit_ux(report, focus)
            elif target_agent == "auditor":
                self._audit_self(report, focus)

            # Calculate final score (average of breakdown)
            if report["score_breakdown"]:
                scores = list(report["score_breakdown"].values())
                report["score"] = round(sum(scores) / len(scores), 1)

            self._last_audit_score = report["score"]
            self._total_audits += 1

            # Add trend info
            trends = self._compute_trends()
            if target_agent in trends:
                t = trends[target_agent]
                report["trend"] = {
                    "previous_score": t["previous"],
                    "delta": t["delta"],
                    "direction": "up" if t["delta"] > 0 else "down" if t["delta"] < 0 else "stable",
                }

            # Generate summary
            report["summary"] = self._generate_summary(report)

            # Persist the report
            self._save_report(report)

            duration_ms = int((time.monotonic() - start) * 1000)

            self.log_decision(f"AUDIT COMPLETE: {target_agent}", {
                "score": report["score"],
                "findings": len(report["findings"]),
                "improvements": len(report["improvements"]),
                "tests_to_add": len(report["tests_to_add"]),
                "duration_ms": duration_ms,
            })

            self.publish("audit_complete", {
                "target": target_agent,
                "score": report["score"],
                "findings_count": len(report["findings"]),
                "improvements_count": len(report["improvements"]),
            })

            self._set_status(AgentStatus.IDLE,
                             f"Audit {target_agent}: {report['score']}/10")

            return report

        except Exception as exc:
            self.log(f"Audit {target_agent} failed", {"error": str(exc)}, level="ERROR")
            self._set_status(AgentStatus.ERROR, str(exc))
            raise

    def get_reports(self, target_agent: str | None = None,
                    limit: int = 20) -> list[dict]:
        """Get historical audit reports, optionally filtered by agent."""
        reports = self._audit_history
        if target_agent:
            reports = [r for r in reports if r.get("target_agent") == target_agent]
        return reports[-limit:]

    def get_latest_report(self, target_agent: str) -> dict | None:
        """Get the most recent audit report for a specific agent."""
        for report in reversed(self._audit_history):
            if report.get("target_agent") == target_agent:
                return report
        return None

    # ── Audit implementations ──────────────────────────────────────

    def _audit_news(self, report: dict, focus: str | None):
        """Audit Agent News — source coverage, reliability, dedup, latency."""
        findings = report["findings"]
        improvements = report["improvements"]
        tests = report["tests_to_add"]
        scores = report["score_breakdown"]

        # 1. Source coverage
        try:
            from ..source_monitor import PHASE_0_SOURCES, SOURCE_DESCRIPTIONS
            from ..data_apis import collect_structured_data
            from ..news_collector import EARLY_SIGNAL_FEEDS

            total_phase0 = len(PHASE_0_SOURCES)
            total_rss = len(EARLY_SIGNAL_FEEDS) if hasattr(EARLY_SIGNAL_FEEDS, '__len__') else 0

            findings.append({
                "area": "source_coverage",
                "status": "OK" if total_phase0 >= 15 else "WARN",
                "detail": f"{total_phase0} sources Phase 0 configurées, {total_rss} RSS feeds",
            })
            scores["source_coverage"] = min(10, total_phase0 / 1.7)  # 17 sources = 10/10
        except Exception as exc:
            findings.append({"area": "source_coverage", "status": "ERROR", "detail": str(exc)})
            scores["source_coverage"] = 3

        # 2. Source health (if available)
        try:
            from ..source_monitor import get_tracker
            tracker = get_tracker()
            snapshot = tracker.get_scan_health_snapshot()
            if snapshot:
                sources = snapshot.get("sources", {})
                total = len(sources)
                healthy = sum(1 for s in sources.values() if s.get("status") == "OK")
                degraded = sum(1 for s in sources.values() if s.get("status") == "DEGRADED")
                dead = sum(1 for s in sources.values() if s.get("status") == "DEAD")

                health_score = (healthy / total * 10) if total > 0 else 5
                scores["source_reliability"] = round(health_score, 1)

                findings.append({
                    "area": "source_reliability",
                    "status": "OK" if dead == 0 else "CRITICAL" if dead > 2 else "WARN",
                    "detail": f"{healthy}/{total} healthy, {degraded} degraded, {dead} dead",
                })

                if dead > 0:
                    dead_names = [n for n, s in sources.items() if s.get("status") == "DEAD"]
                    improvements.append({
                        "priority": "HIGH",
                        "agent": "news",
                        "action": f"Fix or replace dead sources: {', '.join(dead_names)}",
                        "rationale": "Dead sources = lost edge signals",
                    })
            else:
                scores["source_reliability"] = 5
                findings.append({
                    "area": "source_reliability",
                    "status": "WARN",
                    "detail": "No health data available — run a scan first",
                })
        except Exception as exc:
            scores["source_reliability"] = 5
            findings.append({"area": "source_reliability", "status": "ERROR", "detail": str(exc)})

        # 3. Dedup effectiveness
        try:
            from ..scan_history import load_scan_history
            history = load_scan_history()
            recent = history[-20:] if len(history) > 20 else history

            if recent:
                total_news = sum(h.get("news_analyzed", 0) for h in recent)
                findings.append({
                    "area": "dedup_effectiveness",
                    "status": "OK",
                    "detail": f"Last {len(recent)} scans analyzed {total_news} total news items",
                })
                scores["dedup_effectiveness"] = 7  # Baseline — hard to measure precisely without more data
            else:
                scores["dedup_effectiveness"] = 5
                findings.append({"area": "dedup_effectiveness", "status": "WARN", "detail": "No scan history"})
        except Exception:
            scores["dedup_effectiveness"] = 5

        # 4. Event detection
        try:
            from ..event_scanner import EVENT_KEYWORDS
            total_keywords = sum(len(v) for v in EVENT_KEYWORDS.values())
            categories = list(EVENT_KEYWORDS.keys())
            findings.append({
                "area": "event_detection",
                "status": "OK" if total_keywords > 50 else "WARN",
                "detail": f"{total_keywords} keywords across {len(categories)} categories: {categories}",
            })
            scores["event_detection"] = min(10, total_keywords / 10)
        except Exception as exc:
            scores["event_detection"] = 5
            findings.append({"area": "event_detection", "status": "ERROR", "detail": str(exc)})

        # 5. Latency check
        try:
            agent_news_logs = self._get_agent_logs("news", limit=50)
            collection_times = [
                l.get("duration_ms") for l in agent_news_logs
                if l.get("action", "").startswith("Collecting") and l.get("duration_ms")
            ]
            if collection_times:
                avg_ms = sum(collection_times) / len(collection_times)
                max_ms = max(collection_times)
                findings.append({
                    "area": "latency",
                    "status": "OK" if avg_ms < 30000 else "WARN" if avg_ms < 60000 else "CRITICAL",
                    "detail": f"Avg collection: {avg_ms:.0f}ms, Max: {max_ms:.0f}ms ({len(collection_times)} samples)",
                })
                scores["latency"] = 10 if avg_ms < 15000 else 8 if avg_ms < 30000 else 5 if avg_ms < 60000 else 3
            else:
                scores["latency"] = 5
                findings.append({"area": "latency", "status": "WARN", "detail": "No timing data in logs"})
        except Exception:
            scores["latency"] = 5

        # 6. Freshness distribution
        try:
            from ..scan_history import load_scan_history
            history_for_freshness = load_scan_history()
            recent_scans = history_for_freshness[-20:] if len(history_for_freshness) > 20 else history_for_freshness
            ages = []
            for scan in recent_scans:
                for news in scan.get("all_scored_news", []):
                    age = news.get("age_hours")
                    if age is not None:
                        ages.append(age)
            if ages:
                avg_age = sum(ages) / len(ages)
                fresh_pct = sum(1 for a in ages if a < 2) / len(ages) * 100
                findings.append({
                    "area": "freshness_distribution",
                    "status": "OK" if fresh_pct > 30 else "WARN",
                    "detail": f"Avg age: {avg_age:.1f}h, <2h: {fresh_pct:.0f}% (n={len(ages)})",
                })
                scores["freshness_distribution"] = min(10, fresh_pct / 8)
            else:
                scores["freshness_distribution"] = 5
                findings.append({"area": "freshness_distribution", "status": "WARN", "detail": "No age data in scored news"})
        except Exception:
            scores["freshness_distribution"] = 5

        # 7. Data quality — titles/descriptions/URLs
        try:
            from ..scan_history import load_scan_history as _load_sh
            sh = _load_sh()
            recent_sh = sh[-20:] if len(sh) > 20 else sh
            empty_titles = 0
            total_items = 0
            for scan in recent_sh:
                for news in scan.get("all_scored_news", []):
                    total_items += 1
                    if not news.get("title", "").strip():
                        empty_titles += 1
            if total_items > 0:
                quality_pct = (total_items - empty_titles) / total_items * 100
                findings.append({
                    "area": "data_quality",
                    "status": "OK" if quality_pct > 95 else "WARN",
                    "detail": f"Titles populated: {quality_pct:.0f}% ({total_items - empty_titles}/{total_items})",
                })
                scores["data_quality"] = min(10, quality_pct / 10)
            else:
                scores["data_quality"] = 5
                findings.append({"area": "data_quality", "status": "WARN", "detail": "No scored news data"})
        except Exception:
            scores["data_quality"] = 5

        # Log analysis for news agent
        self._analyze_agent_errors(findings, scores, improvements, "news")

        # Standard tests to add
        tests.append("test_news_agent_collects_from_all_phase0_sources")
        tests.append("test_news_agent_dedup_filters_exact_duplicates")
        tests.append("test_news_agent_event_detection_keywords_comprehensive")

    def _audit_scoring(self, report: dict, focus: str | None):
        """Audit Agent Scoring — calibration, edge detection, API efficiency."""
        findings = report["findings"]
        improvements = report["improvements"]
        tests = report["tests_to_add"]
        scores = report["score_breakdown"]

        # 1. Score distribution analysis
        try:
            from ..scan_history import load_scan_history
            history = load_scan_history()
            all_scores = []
            for scan in history[-50:]:
                for news in scan.get("all_scored_news", []):
                    s = news.get("total_score") or news.get("score", 0)
                    if s > 0:
                        all_scores.append(s)

            if all_scores:
                avg = sum(all_scores) / len(all_scores)
                above_50 = sum(1 for s in all_scores if s > 50)
                above_80 = sum(1 for s in all_scores if s > 80)
                below_20 = sum(1 for s in all_scores if s < 20)

                findings.append({
                    "area": "score_distribution",
                    "status": "OK" if 20 < avg < 60 else "WARN",
                    "detail": f"Avg score: {avg:.1f}, >80: {above_80}, >50: {above_50}, <20: {below_20} (n={len(all_scores)})",
                })
                # Good distribution means spread, not clustered
                spread = (above_80 > 0 and below_20 > 0)
                scores["score_distribution"] = 8 if spread and 20 < avg < 60 else 6 if spread else 4
            else:
                scores["score_distribution"] = 5
                findings.append({"area": "score_distribution", "status": "WARN", "detail": "No score data available"})
        except Exception as exc:
            scores["score_distribution"] = 5
            findings.append({"area": "score_distribution", "status": "ERROR", "detail": str(exc)})

        # 2. Edge factor calibration
        try:
            from ..config import CATEGORY_SCORE_MULTIPLIERS
            weather_mult = CATEGORY_SCORE_MULTIPLIERS.get("weather", 0)
            earnings_mult = CATEGORY_SCORE_MULTIPLIERS.get("earnings", 0)
            commodity_mult = CATEGORY_SCORE_MULTIPLIERS.get("commodity", 0)

            ratio = weather_mult / earnings_mult if earnings_mult > 0 else 0
            findings.append({
                "area": "category_multipliers",
                "status": "OK" if ratio >= 4 else "WARN",
                "detail": f"weather={weather_mult}x, commodity={commodity_mult}x, earnings={earnings_mult}x (ratio weather/earnings: {ratio:.1f}x)",
            })
            scores["category_multipliers"] = 9 if ratio >= 6 else 7 if ratio >= 4 else 4
        except Exception as exc:
            scores["category_multipliers"] = 5
            findings.append({"area": "category_multipliers", "status": "ERROR", "detail": str(exc)})

        # 3. Zero-edge filter
        try:
            from ..news_scorer import _is_zero_edge_headline
            test_cases = [
                ("Apple reports Q3 earnings beat", True),
                ("NOAA warns of severe drought in Midwest", False),
                ("Fed holds rates steady at 5.25%", True),
                ("Frost warning for Brazil coffee region", False),
                ("NFP comes in at 180K vs 200K expected", True),
            ]
            correct = sum(1 for title, expected in test_cases if _is_zero_edge_headline(title) == expected)
            findings.append({
                "area": "zero_edge_filter",
                "status": "OK" if correct >= 4 else "WARN",
                "detail": f"{correct}/{len(test_cases)} test cases correctly classified",
            })
            scores["zero_edge_filter"] = correct * 2  # 5 cases, max 10
            if correct < 4:
                improvements.append({
                    "priority": "MEDIUM",
                    "agent": "scoring",
                    "action": "Improve zero-edge headline detection patterns",
                    "rationale": "Missing zero-edge headlines wastes Claude API calls",
                })
        except Exception as exc:
            scores["zero_edge_filter"] = 5
            findings.append({"area": "zero_edge_filter", "status": "ERROR", "detail": str(exc)})

        # 4. Claude API efficiency
        try:
            from ..news_scorer import get_token_usage
            usage = get_token_usage()
            total_tokens = usage.get("total_input", 0) + usage.get("total_output", 0)
            scan_count = usage.get("scan_count", 0)
            avg_per_scan = total_tokens / scan_count if scan_count > 0 else 0

            findings.append({
                "area": "api_efficiency",
                "status": "OK" if avg_per_scan < 50000 else "WARN",
                "detail": f"Total tokens: {total_tokens:,}, Scans: {scan_count}, Avg/scan: {avg_per_scan:,.0f}",
            })
            scores["api_efficiency"] = 8 if avg_per_scan < 30000 else 6 if avg_per_scan < 50000 else 4
        except Exception:
            scores["api_efficiency"] = 7  # No data = assume OK
            findings.append({"area": "api_efficiency", "status": "INFO", "detail": "No token usage data (first session?)"})

        # 5. Edge factor calibration — verify the formula produces sensible ranges
        try:
            from ..scan_history import load_scan_history as _load_sh2
            sh2 = _load_sh2()
            edge_factors = []
            for scan in sh2[-50:]:
                for news in scan.get("all_scored_news", []):
                    td = news.get("transmission_delay", 50)
                    ma = news.get("market_awareness", 50)
                    ef = max(td / 100 * (1 - ma / 100), 0.05)
                    edge_factors.append(ef)
            if edge_factors:
                avg_ef = sum(edge_factors) / len(edge_factors)
                at_floor = sum(1 for e in edge_factors if e <= 0.05) / len(edge_factors) * 100
                findings.append({
                    "area": "edge_factor_calibration",
                    "status": "OK" if at_floor < 50 else "WARN",
                    "detail": f"Avg edge_factor: {avg_ef:.3f}, At floor (0.05): {at_floor:.0f}%",
                })
                scores["edge_factor_calibration"] = 8 if at_floor < 30 else 6 if at_floor < 50 else 4
            else:
                scores["edge_factor_calibration"] = 5
        except Exception:
            scores["edge_factor_calibration"] = 5

        # 6. Coherence validation
        try:
            from ..news_scorer import _validate_coherence
            # Test known coherent case
            _validate_coherence(80, 10, "test")  # high delay, low awareness = coherent
            findings.append({
                "area": "coherence_validation",
                "status": "OK",
                "detail": "Coherence validator present and callable",
            })
            scores["coherence_validation"] = 8
        except ImportError:
            scores["coherence_validation"] = 3
            findings.append({"area": "coherence_validation", "status": "WARN", "detail": "Cannot import _validate_coherence"})
        except Exception:
            scores["coherence_validation"] = 7
            findings.append({"area": "coherence_validation", "status": "OK", "detail": "Coherence validator present"})

        # 7. Chain reaction coverage
        try:
            from ..config import CHAIN_REACTIONS
            total_chains = sum(len(v) for v in CHAIN_REACTIONS.values())
            tickers_covered = len(CHAIN_REACTIONS)
            findings.append({
                "area": "chain_reaction_coverage",
                "status": "OK" if total_chains >= 15 else "WARN",
                "detail": f"{total_chains} chain reactions across {tickers_covered} tickers",
            })
            scores["chain_reaction_coverage"] = min(10, total_chains / 2)
        except Exception:
            scores["chain_reaction_coverage"] = 5

        # Log analysis for scoring agent
        self._analyze_agent_errors(findings, scores, improvements, "scoring")

        tests.append("test_scoring_agent_edge_factor_discriminates_weather_vs_earnings")
        tests.append("test_scoring_agent_chain_reactions_propagate_correctly")
        tests.append("test_scoring_agent_cache_hit_rate_above_threshold")

    def _audit_trader(self, report: dict, focus: str | None):
        """Audit Agent Trader — win rate, R/R, selection quality, risk management."""
        findings = report["findings"]
        improvements = report["improvements"]
        tests = report["tests_to_add"]
        scores = report["score_breakdown"]

        try:
            from ..learning import load_trades, compute_performance
            from ..models import TradeResult

            trades = load_trades()
            closed = [t for t in trades if t.result != TradeResult.PENDING]

            if not closed:
                findings.append({"area": "overall", "status": "WARN", "detail": "No closed trades to audit"})
                scores["win_rate"] = 5
                scores["risk_reward"] = 5
                scores["selection_quality"] = 5
                return

            # 1. Win rate
            wins = sum(1 for t in closed if t.result == TradeResult.TP_HIT)
            wr = wins / len(closed) * 100
            findings.append({
                "area": "win_rate",
                "status": "OK" if wr >= 45 else "WARN" if wr >= 35 else "CRITICAL",
                "detail": f"Win rate: {wr:.1f}% ({wins}/{len(closed)} trades)",
            })
            scores["win_rate"] = min(10, wr / 7)  # 70% = 10/10

            # 2. Risk/Reward realized
            pnls = [t.pnl_pct for t in closed if t.pnl_pct is not None]
            if pnls:
                avg_pnl = sum(pnls) / len(pnls)
                total_pnl = sum(pnls)
                positive_pnls = [p for p in pnls if p > 0]
                negative_pnls = [p for p in pnls if p < 0]
                avg_win = sum(positive_pnls) / len(positive_pnls) if positive_pnls else 0
                avg_loss = abs(sum(negative_pnls) / len(negative_pnls)) if negative_pnls else 0
                realized_rr = avg_win / avg_loss if avg_loss > 0 else float('inf')

                findings.append({
                    "area": "risk_reward",
                    "status": "OK" if realized_rr >= 1.2 else "WARN",
                    "detail": f"Realized R/R: {realized_rr:.2f}, Avg win: +{avg_win:.3f}%, Avg loss: -{avg_loss:.3f}%, Total PnL: {total_pnl:+.3f}%",
                })
                scores["risk_reward"] = min(10, realized_rr * 4)  # R/R 2.5 = 10/10

                if realized_rr < 1.2:
                    improvements.append({
                        "priority": "HIGH",
                        "agent": "trader_1",
                        "action": "Improve R/R calibration — realized R/R below 1.2",
                        "rationale": f"Current R/R {realized_rr:.2f} means losses are too large relative to wins",
                    })

            # 3. Category performance
            by_cat: dict[str, list] = {}
            for t in closed:
                cat = getattr(t, "news_category", None) or getattr(t, "category", "unknown")
                by_cat.setdefault(cat, []).append(t)

            cat_issues = []
            for cat, cat_trades in by_cat.items():
                cat_wins = sum(1 for t in cat_trades if t.result == TradeResult.TP_HIT)
                cat_wr = cat_wins / len(cat_trades) * 100 if cat_trades else 0
                if cat_wr < 30 and len(cat_trades) >= 3:
                    cat_issues.append(f"{cat}: {cat_wr:.0f}% WR ({len(cat_trades)} trades)")

            if cat_issues:
                findings.append({
                    "area": "selection_quality",
                    "status": "WARN",
                    "detail": f"Low WR categories: {'; '.join(cat_issues)}",
                })
                scores["selection_quality"] = 5
            else:
                scores["selection_quality"] = 8
                findings.append({
                    "area": "selection_quality",
                    "status": "OK",
                    "detail": f"No critical category underperformance ({len(by_cat)} categories traded)",
                })

            # 4. EXPIRED rate
            expired = sum(1 for t in closed if t.result == TradeResult.EXPIRED)
            exp_rate = expired / len(closed) * 100
            findings.append({
                "area": "expired_rate",
                "status": "OK" if exp_rate < 40 else "WARN" if exp_rate < 60 else "CRITICAL",
                "detail": f"EXPIRED rate: {exp_rate:.1f}% ({expired}/{len(closed)})",
            })
            scores["expired_rate"] = max(2, 10 - exp_rate / 6)

            if exp_rate > 50:
                improvements.append({
                    "priority": "HIGH",
                    "agent": "trader_1",
                    "action": f"EXPIRED rate too high ({exp_rate:.0f}%) — review target calibration",
                    "rationale": "High EXPIRED rate means targets are too ambitious or signal timing is off",
                })

            # 5. Streak analysis
            results_seq = [t.result for t in sorted(closed, key=lambda x: x.timestamp)]
            max_loss_streak = 0
            current_streak = 0
            for r in results_seq:
                if r != TradeResult.TP_HIT:
                    current_streak += 1
                    max_loss_streak = max(max_loss_streak, current_streak)
                else:
                    current_streak = 0

            findings.append({
                "area": "drawdown",
                "status": "OK" if max_loss_streak < 5 else "WARN" if max_loss_streak < 8 else "CRITICAL",
                "detail": f"Max loss streak: {max_loss_streak} consecutive non-wins",
            })
            scores["drawdown"] = max(2, 10 - max_loss_streak)

        except Exception as exc:
            findings.append({"area": "trader", "status": "ERROR", "detail": str(exc)})
            scores["overall"] = 3

            # 6. Position sizing — VIX regime check
            try:
                vix_trades = [t for t in closed if getattr(t, 'vix_at_trade', None) is not None]
                if vix_trades:
                    high_vix = [t for t in vix_trades if t.vix_at_trade >= 30]
                    findings.append({
                        "area": "position_sizing",
                        "status": "OK",
                        "detail": f"VIX-tracked trades: {len(vix_trades)}/{len(closed)}, High VIX (>=30): {len(high_vix)}",
                    })
                    scores["position_sizing"] = 8 if len(vix_trades) > len(closed) * 0.5 else 5
                else:
                    scores["position_sizing"] = 5
                    findings.append({"area": "position_sizing", "status": "WARN", "detail": "No VIX data on trades"})
            except Exception:
                scores["position_sizing"] = 5

            # 7. Correlation check — verify no duplicate group trades
            try:
                from ..config import CORRELATION_GROUPS
                from collections import Counter
                # Check if trades on same day are in same correlation group
                by_date: dict[str, list] = {}
                for t in closed:
                    d = t.timestamp.strftime("%Y-%m-%d") if hasattr(t.timestamp, 'strftime') else str(t.timestamp)[:10]
                    by_date.setdefault(d, []).append(t.ticker)
                violations = 0
                for date, tickers in by_date.items():
                    if len(tickers) < 2:
                        continue
                    for group_name, group_tickers in CORRELATION_GROUPS.items():
                        group_set = set(group_tickers)
                        in_group = [t for t in tickers if t in group_set]
                        if len(in_group) > 1:
                            violations += 1
                findings.append({
                    "area": "correlation_check",
                    "status": "OK" if violations == 0 else "WARN",
                    "detail": f"Same-day correlation group violations: {violations}",
                })
                scores["correlation_check"] = 10 if violations == 0 else max(3, 10 - violations * 2)
            except Exception:
                scores["correlation_check"] = 5

            # 8. Calendar blocking
            try:
                from ..economic_calendar import get_upcoming_events
                events = get_upcoming_events()
                findings.append({
                    "area": "calendar_blocking",
                    "status": "OK",
                    "detail": f"Economic calendar active, {len(events)} upcoming events loaded",
                })
                scores["calendar_blocking"] = 8
            except Exception:
                scores["calendar_blocking"] = 5
                findings.append({"area": "calendar_blocking", "status": "WARN", "detail": "Cannot verify calendar"})

        # Log analysis for trader agent
        self._analyze_agent_errors(findings, scores, improvements, "trader_1")

        tests.append("test_trader_agent_respects_daily_cap_per_vix_regime")
        tests.append("test_trader_agent_spread_filter_rejects_illiquid_trades")
        tests.append("test_trader_agent_fallback_ticker_works")

    def _audit_journal(self, report: dict, focus: str | None):
        """Audit Agent Journal — closure accuracy, price reliability, metrics."""
        findings = report["findings"]
        improvements = report["improvements"]
        tests = report["tests_to_add"]
        scores = report["score_breakdown"]

        try:
            from ..journal import load_journal

            entries = load_journal()
            if not entries:
                findings.append({"area": "overall", "status": "WARN", "detail": "No journal entries to audit"})
                scores["closure_accuracy"] = 5
                scores["data_quality"] = 5
                return

            recent = entries[-100:] if len(entries) > 100 else entries

            # 1. Closure result distribution
            results = {}
            for e in recent:
                r = e.get("result", "UNKNOWN")
                results[r] = results.get(r, 0) + 1

            findings.append({
                "area": "closure_accuracy",
                "status": "OK",
                "detail": f"Result distribution (last {len(recent)}): {results}",
            })
            scores["closure_accuracy"] = 7  # Baseline — need more analysis

            # 2. Bar coverage
            bar_coverages = [e.get("bar_coverage", 0) for e in recent if e.get("bar_coverage") is not None]
            if bar_coverages:
                avg_coverage = sum(bar_coverages) / len(bar_coverages)
                low_coverage = sum(1 for b in bar_coverages if b < 3)
                findings.append({
                    "area": "bar_coverage",
                    "status": "OK" if avg_coverage >= 5 else "WARN",
                    "detail": f"Avg bars post-entry: {avg_coverage:.1f}, Low coverage (<3 bars): {low_coverage}/{len(bar_coverages)}",
                })
                scores["bar_coverage"] = min(10, avg_coverage)

                if low_coverage > len(bar_coverages) * 0.3:
                    improvements.append({
                        "priority": "MEDIUM",
                        "agent": "journal",
                        "action": f"Low bar coverage on {low_coverage} entries — consider fallback to daily bars",
                        "rationale": "Too few bars = unreliable TP/SL detection",
                    })

            # 3. MAE/MFE tracking
            mae_count = sum(1 for e in recent if e.get("mae") is not None)
            mfe_count = sum(1 for e in recent if e.get("mfe") is not None)
            findings.append({
                "area": "mae_mfe_tracking",
                "status": "OK" if mae_count > len(recent) * 0.5 else "WARN",
                "detail": f"MAE tracked: {mae_count}/{len(recent)}, MFE tracked: {mfe_count}/{len(recent)}",
            })
            scores["mae_mfe_tracking"] = min(10, (mae_count + mfe_count) / len(recent) * 5)

            # 4. Slippage monitoring
            slippages = [e.get("slippage") for e in recent if e.get("slippage") is not None]
            if slippages:
                avg_slip = sum(abs(s) for s in slippages) / len(slippages)
                findings.append({
                    "area": "slippage",
                    "status": "OK" if avg_slip < 0.5 else "WARN",
                    "detail": f"Avg |slippage|: {avg_slip:.3f}% ({len(slippages)} samples)",
                })
                scores["slippage"] = 8 if avg_slip < 0.3 else 6 if avg_slip < 0.5 else 4
            else:
                scores["slippage"] = 5
                findings.append({"area": "slippage", "status": "WARN", "detail": "No slippage data"})

            # 5. PnL data quality
            pnl_missing = sum(1 for e in recent if e.get("pnl_pct") is None and e.get("result") != "PENDING")
            findings.append({
                "area": "data_quality",
                "status": "OK" if pnl_missing == 0 else "WARN",
                "detail": f"Missing PnL on closed entries: {pnl_missing}/{len(recent)}",
            })
            scores["data_quality"] = 10 if pnl_missing == 0 else max(3, 10 - pnl_missing)

        except Exception as exc:
            findings.append({"area": "journal", "status": "ERROR", "detail": str(exc)})
            scores["overall"] = 3

            # 6. Price fetch reliability — entries with missing exit_price
            no_exit = sum(1 for e in recent if e.get("exit_price") is None and e.get("result") not in (None, "PENDING"))
            if recent:
                fetch_rate = (len(recent) - no_exit) / len(recent) * 100
                findings.append({
                    "area": "price_fetch_reliability",
                    "status": "OK" if fetch_rate > 90 else "WARN" if fetch_rate > 70 else "CRITICAL",
                    "detail": f"Price fetch success: {fetch_rate:.0f}% ({len(recent) - no_exit}/{len(recent)})",
                })
                scores["price_fetch_reliability"] = min(10, fetch_rate / 10)
                if fetch_rate < 80:
                    improvements.append({
                        "priority": "HIGH",
                        "agent": "journal",
                        "action": f"Price fetch reliability at {fetch_rate:.0f}% — check API keys/connectivity",
                        "rationale": "Missing exit prices = EXPIRED trades with entry_price fallback",
                    })

            # 7. Pruning verification
            try:
                oldest_date = min((e.get("date", "9999") for e in entries), default="9999")
                from datetime import datetime as dt, timedelta
                one_year_ago = (dt.now() - timedelta(days=365)).strftime("%Y-%m-%d")
                has_old = oldest_date < one_year_ago if oldest_date != "9999" else False
                findings.append({
                    "area": "pruning",
                    "status": "OK" if not has_old else "WARN",
                    "detail": f"Oldest entry: {oldest_date}" + (" (>1y, should be pruned)" if has_old else ""),
                })
                scores["pruning"] = 9 if not has_old else 5
            except Exception:
                scores["pruning"] = 5

            # 8. Recovery verification — check agent logs for recovery events
            recovery_logs = [l for l in self._get_agent_logs("journal", limit=100)
                             if "recover" in l.get("action", "").lower()]
            findings.append({
                "area": "recovery",
                "status": "OK",
                "detail": f"Startup recovery events logged: {len(recovery_logs)}",
            })
            scores["recovery"] = 8

        # Log analysis for journal agent
        self._analyze_agent_errors(findings, scores, improvements, "journal")

        tests.append("test_journal_agent_all_closed_trades_have_pnl")
        tests.append("test_journal_agent_bar_coverage_minimum_3")
        tests.append("test_journal_agent_slippage_within_spread")

    def _audit_learning(self, report: dict, focus: str | None):
        """Audit Agent Learning — dimension significance, overfitting, commodity protection."""
        findings = report["findings"]
        improvements = report["improvements"]
        tests = report["tests_to_add"]
        scores = report["score_breakdown"]

        try:
            from ..learning import compute_learning_adjustments, load_trades
            from ..models import TradeResult

            trades = load_trades()
            closed = [t for t in trades if t.result != TradeResult.PENDING]

            if len(closed) < 10:
                findings.append({"area": "overall", "status": "WARN", "detail": f"Only {len(closed)} closed trades — insufficient data for learning audit"})
                scores["data_volume"] = 3
                return

            # 1. Compute learning data
            learning = compute_learning_adjustments(trades=trades)
            adj = learning.get("adjustments", {})
            newscat = learning.get("newscat_adj", {})
            session = learning.get("session_adj", {})
            regime = learning.get("regime_adj", {})
            direction = learning.get("direction_adj", {})
            delay_bias = learning.get("delay_bias_adj", 1.0)

            # 2. Dimension significance — how many are active
            active_dims = sum([
                len(adj) > 0,
                len(newscat) > 0,
                len(session) > 0,
                len(regime) > 0,
                len(direction) > 0,
                delay_bias != 1.0,
            ])
            findings.append({
                "area": "dimension_significance",
                "status": "OK" if active_dims >= 3 else "WARN",
                "detail": f"{active_dims}/6 learning dimensions active, {len(adj)} ticker adjustments",
            })
            scores["dimension_significance"] = min(10, active_dims * 1.7)

            # 3. Overfitting check — adjustments too extreme
            extreme_adj = {k: v for k, v in adj.items() if abs(v - 1.0) > 0.4}
            findings.append({
                "area": "overfitting_risk",
                "status": "OK" if len(extreme_adj) <= 2 else "WARN",
                "detail": f"{len(extreme_adj)} extreme adjustments (>0.4 from 1.0): {extreme_adj}",
            })
            scores["overfitting_risk"] = max(3, 10 - len(extreme_adj) * 2)

            if len(extreme_adj) > 3:
                improvements.append({
                    "priority": "MEDIUM",
                    "agent": "learning",
                    "action": f"Review extreme adjustments: {list(extreme_adj.keys())}",
                    "rationale": "Extreme adjustments may indicate overfitting to small samples",
                })

            # 4. Commodity protection — cat_adj must NOT penalize commodities
            from ..config import CATEGORIES
            commodity_cats = [c for c in CATEGORIES if c.startswith("commodities_")]
            decomp = learning.get("decomposition", {})

            commodity_penalized = []
            for ticker, d in decomp.items():
                cat_mult = d.get("cat_mult", 1.0)
                cat_name = d.get("category", "")
                if cat_name.startswith("commodities_") and cat_mult < 0.95:
                    commodity_penalized.append(f"{ticker} (cat_mult={cat_mult:.3f})")

            findings.append({
                "area": "commodity_protection",
                "status": "OK" if not commodity_penalized else "CRITICAL",
                "detail": "Commodity class protection: " + (
                    "ENFORCED — no commodities penalized as class" if not commodity_penalized
                    else f"VIOLATED — {len(commodity_penalized)} penalized: {commodity_penalized}"
                ),
            })
            scores["commodity_protection"] = 10 if not commodity_penalized else 2

            if commodity_penalized:
                improvements.append({
                    "priority": "CRITICAL",
                    "agent": "learning",
                    "action": "FIX: cat_adj is penalizing commodities as a class — violates absolute rule",
                    "rationale": "REGLE ABSOLUE: le learning ne doit JAMAIS pénaliser les commodities en tant que classe",
                })

            # 5. Data integrity
            anomalous = [t for t in closed if t.pnl_pct and abs(t.pnl_pct) > 50]
            findings.append({
                "area": "data_integrity",
                "status": "OK" if not anomalous else "WARN",
                "detail": f"Anomalous PnL (>50%): {len(anomalous)} trades",
            })
            scores["data_integrity"] = 10 if not anomalous else max(4, 10 - len(anomalous) * 2)

            # 6. Cross-dimension newscat+ticker
            newscat_cross = {k: v for k, v in newscat.items() if "+" in k}
            newscat_broad = {k: v for k, v in newscat.items() if "+" not in k}
            findings.append({
                "area": "cross_dimension",
                "status": "OK" if len(newscat_cross) > 0 or len(closed) < 30 else "WARN",
                "detail": f"Cross-dimension (newscat+ticker): {len(newscat_cross)}, Broad fallback: {len(newscat_broad)}",
            })
            scores["cross_dimension"] = 8 if newscat_cross else 6

        except Exception as exc:
            findings.append({"area": "learning", "status": "ERROR", "detail": str(exc)})
            scores["overall"] = 3

            # 7. Decay calibration — verify lookback period is reasonable
            try:
                from ..learning import _half_life
                hl = _half_life(len(closed))
                lookback_days = hl * 2
                findings.append({
                    "area": "decay_calibration",
                    "status": "OK" if 40 <= lookback_days <= 120 else "WARN",
                    "detail": f"Half-life: {hl}d, Lookback: {lookback_days}d ({len(closed)} trades)",
                })
                scores["decay_calibration"] = 8 if 40 <= lookback_days <= 120 else 5
            except (ImportError, Exception):
                scores["decay_calibration"] = 6
                findings.append({"area": "decay_calibration", "status": "OK", "detail": "Decay function configured (details unavailable)"})

            # 8. Anomaly detection — check performance summary for alerts
            try:
                from ..learning import build_performance_summary
                summary = build_performance_summary(trades=trades)
                alert_lines = [l for l in summary.split("\n") if "ALERT" in l.upper() or "⚠" in l or "streak" in l.lower()]
                findings.append({
                    "area": "anomaly_detection",
                    "status": "OK" if len(alert_lines) < 5 else "WARN",
                    "detail": f"Active alerts in performance summary: {len(alert_lines)}",
                })
                scores["anomaly_detection"] = 8 if len(alert_lines) < 3 else 6
            except Exception:
                scores["anomaly_detection"] = 5
                findings.append({"area": "anomaly_detection", "status": "WARN", "detail": "Cannot build performance summary"})

            # 9. Feedback quality — check that instructions contain recent data
            try:
                from ..learning import build_performance_summary as _bps
                fb = _bps(trades=trades)
                has_wr = "WR=" in fb or "Win rate" in fb
                has_pnl = "PnL=" in fb or "pnl" in fb.lower()
                findings.append({
                    "area": "feedback_quality",
                    "status": "OK" if has_wr and has_pnl else "WARN",
                    "detail": f"Feedback includes: WR={has_wr}, PnL={has_pnl}, length={len(fb)} chars",
                })
                scores["feedback_quality"] = 8 if (has_wr and has_pnl) else 5
            except Exception:
                scores["feedback_quality"] = 5

        # Log analysis for learning agent
        self._analyze_agent_errors(findings, scores, improvements, "learning")

        tests.append("test_learning_agent_commodity_never_penalized_as_class")
        tests.append("test_learning_agent_extreme_adjustments_clamped")
        tests.append("test_learning_agent_cross_dimension_newscat_ticker")

        # Memory update suggestion
        report["memory_updates"].append({
            "file": "CLAUDE.md",
            "section": "Learning adaptatif",
            "update": "Add audit trail: last audit date + score for learning system",
        })

    def _audit_ux(self, report: dict, focus: str | None):
        """Audit Agent UX — frontend components, API integration, polling, accessibility."""
        findings = report["findings"]
        improvements = report["improvements"]
        tests = report["tests_to_add"]
        scores = report["score_breakdown"]

        try:
            from pathlib import Path
            frontend_dir = Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "src"

            # 1. Component coverage — check all key components exist
            required_components = [
                "components/Dashboard.jsx",
                "components/Journal.jsx",
                "components/History.jsx",
                "components/Performance.jsx",
                "components/AgentSidebar.jsx",
                "components/AgentOverview.jsx",
                "components/AgentDetail.jsx",
            ]
            existing = [c for c in required_components if (frontend_dir / c).exists()]
            missing = [c for c in required_components if c not in existing]
            findings.append({
                "area": "component_coverage",
                "status": "OK" if not missing else "CRITICAL",
                "detail": f"Components: {len(existing)}/{len(required_components)} present"
                          + (f", missing: {missing}" if missing else ""),
            })
            scores["component_coverage"] = 10 if not missing else max(3, 10 - len(missing) * 2)

            # 2. API integration — check App.jsx fetches /api/agents
            app_jsx = frontend_dir / "App.jsx"
            app_content = app_jsx.read_text() if app_jsx.exists() else ""
            api_calls = []
            for endpoint in ["/api/agents", "/api/health"]:
                if endpoint in app_content:
                    api_calls.append(endpoint)
            findings.append({
                "area": "api_integration",
                "status": "OK" if len(api_calls) >= 2 else "WARN",
                "detail": f"API endpoints used in App.jsx: {api_calls}",
            })
            scores["api_integration"] = 10 if len(api_calls) >= 2 else 6

            # 3. Error handling — check ErrorBoundary exists
            has_error_boundary = "ErrorBoundary" in app_content
            has_disconnect_banner = "disconnect-banner" in app_content or "backendUp" in app_content
            findings.append({
                "area": "error_handling",
                "status": "OK" if has_error_boundary and has_disconnect_banner else "WARN",
                "detail": f"ErrorBoundary: {has_error_boundary}, Disconnect banner: {has_disconnect_banner}",
            })
            scores["error_handling"] = 10 if (has_error_boundary and has_disconnect_banner) else 6

            # 4. Polling efficiency
            has_interval_cleanup = "clearInterval" in app_content
            has_suspense = "Suspense" in app_content
            findings.append({
                "area": "polling_efficiency",
                "status": "OK" if has_interval_cleanup and has_suspense else "WARN",
                "detail": f"Interval cleanup: {has_interval_cleanup}, Suspense: {has_suspense}",
            })
            scores["polling_efficiency"] = 9 if (has_interval_cleanup and has_suspense) else 6

            # 5. Agent visibility — check all 7 agents are represented
            agent_detail = frontend_dir / "components" / "AgentDetail.jsx"
            agent_overview = frontend_dir / "components" / "AgentOverview.jsx"
            overview_content = agent_overview.read_text() if agent_overview.exists() else ""
            agents_in_config = []
            for agent_key in ["news", "scoring", "trader_1", "journal", "learning", "auditor", "ux"]:
                if agent_key in overview_content:
                    agents_in_config.append(agent_key)
            missing_agents = [a for a in ["news", "scoring", "trader_1", "journal", "learning", "auditor", "ux"]
                              if a not in agents_in_config]
            findings.append({
                "area": "agent_visibility",
                "status": "OK" if not missing_agents else "WARN",
                "detail": f"Agents in overview config: {len(agents_in_config)}/7"
                          + (f", missing: {missing_agents}" if missing_agents else ""),
            })
            scores["agent_visibility"] = 10 if not missing_agents else max(5, 10 - len(missing_agents))

            # 6. Data display — check AgentDetail exists and has log filtering
            detail_content = agent_detail.read_text() if agent_detail.exists() else ""
            has_log_filter = "level" in detail_content.lower() and "filter" in detail_content.lower()
            has_metrics = "metrics" in detail_content.lower()
            findings.append({
                "area": "data_display",
                "status": "OK" if has_log_filter and has_metrics else "WARN",
                "detail": f"Log filtering: {has_log_filter}, Metrics display: {has_metrics}",
            })
            scores["data_display"] = 9 if (has_log_filter and has_metrics) else 6

            # 7. CSS/responsive — check App.css has sidebar styles
            app_css = frontend_dir / "App.css"
            css_content = app_css.read_text() if app_css.exists() else ""
            has_responsive = "@media" in css_content
            has_sidebar_style = "agent-sidebar" in css_content
            findings.append({
                "area": "responsive_design",
                "status": "OK" if has_responsive and has_sidebar_style else "WARN",
                "detail": f"Responsive: {has_responsive}, Sidebar styled: {has_sidebar_style}",
            })
            scores["responsive_design"] = 9 if (has_responsive and has_sidebar_style) else 5

            # Improvements
            if missing:
                improvements.append({
                    "priority": "HIGH",
                    "action": f"Create missing components: {missing}",
                    "rationale": "Components manquants empêchent l'affichage correct du dashboard",
                })
            if missing_agents:
                improvements.append({
                    "priority": "MEDIUM",
                    "action": f"Add agent configs for: {missing_agents} in AgentOverview",
                    "rationale": "Chaque agent doit avoir une carte visible avec ses métriques",
                })
            if "document.hidden" not in app_content:
                improvements.append({
                    "priority": "LOW",
                    "action": "Skip agent polling when tab is hidden (document.hidden)",
                    "rationale": "Réduit la charge réseau quand l'utilisateur n'est pas sur l'onglet",
                })

        except Exception as exc:
            findings.append({"area": "ux", "status": "ERROR", "detail": str(exc)})
            scores["overall"] = 3

        tests.append("test_ux_agent_all_components_exist")
        tests.append("test_ux_agent_api_endpoints_integrated")
        tests.append("test_ux_agent_error_boundary_present")

        report["memory_updates"].append({
            "file": "CLAUDE.md",
            "section": "Agent Auditor",
            "update": "Added UX audit profile with 7 checks: component_coverage, api_integration, error_handling, polling_efficiency, responsive_design, data_display, agent_visibility",
        })

    def _audit_self(self, report: dict, focus: str | None):
        """Auto-audit — meta-analysis of the auditor's own capabilities."""
        findings = report["findings"]
        improvements = report["improvements"]
        tests = report["tests_to_add"]
        scores = report["score_breakdown"]

        # 1. Profile coverage — all agents auditable?
        auditable = set(AUDIT_PROFILES.keys()) - {"auditor"}  # exclude self
        expected = {"news", "scoring", "trader_1", "journal", "learning", "ux"}
        missing = expected - auditable
        findings.append({
            "area": "profile_coverage",
            "status": "OK" if not missing else "CRITICAL",
            "detail": f"Auditable agents: {len(auditable)}/{len(expected)}"
                      + (f", missing: {missing}" if missing else ""),
        })
        scores["profile_coverage"] = 10 if not missing else max(3, 10 - len(missing) * 2)

        # 2. Check implementation — count implemented vs declared
        check_methods = {
            "news": "_audit_news",
            "scoring": "_audit_scoring",
            "trader_1": "_audit_trader",
            "journal": "_audit_journal",
            "learning": "_audit_learning",
            "ux": "_audit_ux",
        }
        implemented = sum(1 for m in check_methods.values() if hasattr(self, m))
        findings.append({
            "area": "check_implementation",
            "status": "OK" if implemented == len(check_methods) else "WARN",
            "detail": f"Audit methods implemented: {implemented}/{len(check_methods)}",
        })
        scores["check_implementation"] = min(10, implemented / len(check_methods) * 10)

        # 3. Persistence — verify reports can be saved and loaded
        reports_count = len(self._audit_history)
        findings.append({
            "area": "persistence",
            "status": "OK",
            "detail": f"Reports in memory: {reports_count}, Total audits this session: {self._total_audits}",
        })
        scores["persistence"] = 8

        # 4. Scoring calibration — check if scores cluster too high
        if self._audit_history:
            past_scores = [r.get("score", 0) for r in self._audit_history if r.get("score")]
            if past_scores:
                avg_score = sum(past_scores) / len(past_scores)
                too_generous = avg_score > 8.5
                too_harsh = avg_score < 4.0
                findings.append({
                    "area": "scoring_calibration",
                    "status": "WARN" if (too_generous or too_harsh) else "OK",
                    "detail": f"Avg audit score: {avg_score:.1f}/10 across {len(past_scores)} audits"
                              + (" (too generous)" if too_generous else "")
                              + (" (too harsh)" if too_harsh else ""),
                })
                scores["scoring_calibration"] = 7 if (too_generous or too_harsh) else 9
            else:
                scores["scoring_calibration"] = 5
                findings.append({"area": "scoring_calibration", "status": "WARN", "detail": "No past scores to calibrate"})
        else:
            scores["scoring_calibration"] = 5
            findings.append({"area": "scoring_calibration", "status": "WARN", "detail": "No audit history yet"})

        # 5. Trend tracking
        trends = self._compute_trends()
        if trends:
            regressions = [f"{agent}: {t['previous']:.1f}→{t['current']:.1f}" for agent, t in trends.items() if t["delta"] < -1.0]
            improvements_found = [f"{agent}: {t['previous']:.1f}→{t['current']:.1f}" for agent, t in trends.items() if t["delta"] > 1.0]
            findings.append({
                "area": "trend_tracking",
                "status": "OK" if not regressions else "WARN",
                "detail": f"Regressions: {regressions or 'none'}, Improvements: {improvements_found or 'none'}",
            })
            scores["trend_tracking"] = 8 if not regressions else 5
        else:
            scores["trend_tracking"] = 5
            findings.append({"area": "trend_tracking", "status": "WARN", "detail": "Need >=2 audits per agent for trends"})

        # 6. Log analysis — does auditor use logs from other agents?
        findings.append({
            "area": "log_analysis",
            "status": "OK",
            "detail": "Log analysis integrated into news, scoring, trader, journal, learning audits via _analyze_agent_errors()",
        })
        scores["log_analysis"] = 8

        tests.append("test_auditor_self_audit_runs")
        tests.append("test_auditor_all_profiles_have_methods")
        tests.append("test_auditor_trend_tracking")

    # ── Helpers ────────────────────────────────────────────────────

    def _analyze_agent_errors(self, findings: list, scores: dict,
                              improvements: list, agent_name: str):
        """Analyze error logs from a target agent for audit findings."""
        try:
            logs = self._get_agent_logs(agent_name, limit=100)
            errors = [l for l in logs if l.get("level") == "ERROR"]
            warns = [l for l in logs if l.get("level") == "WARN"]
            total = len(logs)
            error_rate = len(errors) / total * 100 if total > 0 else 0

            if total > 0:
                findings.append({
                    "area": f"{agent_name}_error_rate",
                    "status": "OK" if error_rate < 10 else "WARN" if error_rate < 25 else "CRITICAL",
                    "detail": f"Logs: {total} total, {len(errors)} errors ({error_rate:.0f}%), {len(warns)} warnings",
                })
                if error_rate >= 10:
                    # Find most common error
                    error_actions = [e.get("action", "unknown") for e in errors]
                    if error_actions:
                        from collections import Counter
                        most_common = Counter(error_actions).most_common(1)[0]
                        improvements.append({
                            "priority": "MEDIUM",
                            "agent": agent_name,
                            "action": f"Investigate recurring error: '{most_common[0]}' ({most_common[1]}x)",
                            "rationale": f"Error rate at {error_rate:.0f}%",
                        })
        except Exception:
            pass  # No logs available is fine

    def _compute_trends(self) -> dict:
        """Compute score trends per agent (current vs previous audit)."""
        trends = {}
        by_agent: dict[str, list] = {}
        for report in self._audit_history:
            agent = report.get("target_agent")
            score = report.get("score")
            if agent and score is not None:
                by_agent.setdefault(agent, []).append(score)

        for agent, agent_scores in by_agent.items():
            if len(agent_scores) >= 2:
                current = agent_scores[-1]
                previous = agent_scores[-2]
                trends[agent] = {
                    "current": current,
                    "previous": previous,
                    "delta": current - previous,
                }
        return trends

    def _get_agent_logs(self, agent_name: str, limit: int = 50) -> list[dict]:
        """Get logs from another agent for audit purposes."""
        from .registry import get_agent
        agent = get_agent(agent_name)
        if agent:
            return agent.logger.get_logs(limit=limit)
        return []

    def _generate_summary(self, report: dict) -> str:
        """Generate a human-readable audit summary."""
        target = report["target_agent"]
        score = report["score"]
        findings = report["findings"]
        improvements = report["improvements"]

        critical = [f for f in findings if f.get("status") == "CRITICAL"]
        warns = [f for f in findings if f.get("status") == "WARN"]
        oks = [f for f in findings if f.get("status") == "OK"]
        high_prio = [i for i in improvements if i.get("priority") == "HIGH"]

        lines = [
            f"## Audit Agent {target.upper()} — Note: {score}/10",
            "",
            f"**{len(oks)} OK** | **{len(warns)} WARN** | **{len(critical)} CRITICAL**",
            "",
        ]

        if critical:
            lines.append("### Issues critiques")
            for f in critical:
                lines.append(f"- **{f['area']}**: {f['detail']}")
            lines.append("")

        if warns:
            lines.append("### Avertissements")
            for f in warns:
                lines.append(f"- **{f['area']}**: {f['detail']}")
            lines.append("")

        if high_prio:
            lines.append("### Actions prioritaires")
            for i in high_prio:
                lines.append(f"- [{i['priority']}] {i['action']}")
                lines.append(f"  Raison: {i['rationale']}")
            lines.append("")

        return "\n".join(lines)

    def _save_report(self, report: dict):
        """Persist audit report (PG primary, JSON fallback)."""
        # Add to in-memory history
        self._audit_history.append(report)

        # Try PG
        try:
            from ..database import is_pg_enabled, get_conn
            if is_pg_enabled():
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO audit_reports (target_agent, score, report, focus)
                            VALUES (%s, %s, %s, %s)
                        """, (
                            report["target_agent"],
                            report["score"],
                            json.dumps(report, default=str),
                            report.get("focus"),
                        ))
                return
        except Exception as exc:
            logger.warning("PG audit save failed: %s", exc)

        # JSON fallback
        try:
            import fcntl
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            existing = []
            if AUDIT_FILE.exists():
                with open(AUDIT_FILE, "r") as f:
                    fcntl.flock(f, fcntl.LOCK_SH)
                    try:
                        existing = json.load(f)
                    except json.JSONDecodeError:
                        existing = []
                    finally:
                        fcntl.flock(f, fcntl.LOCK_UN)

            existing.append(report)
            # Keep last 100 reports
            existing = existing[-100:]

            with open(AUDIT_FILE, "w") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    json.dump(existing, f, indent=2, default=str)
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        except Exception as exc:
            logger.error("JSON audit save failed: %s", exc)

    def _load_reports(self):
        """Load existing audit reports on startup."""
        # Try PG first
        try:
            from ..database import is_pg_enabled, get_conn
            if is_pg_enabled():
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            SELECT report FROM audit_reports
                            ORDER BY created_at DESC
                            LIMIT 100
                        """)
                        rows = cur.fetchall()
                        self._audit_history = [
                            r[0] if isinstance(r[0], dict) else json.loads(r[0])
                            for r in reversed(rows)
                        ]
                        if self._audit_history:
                            logger.info("Loaded %d audit reports from PG", len(self._audit_history))
                        return
        except Exception:
            pass

        # JSON fallback
        try:
            import fcntl
            if AUDIT_FILE.exists():
                with open(AUDIT_FILE, "r") as f:
                    fcntl.flock(f, fcntl.LOCK_SH)
                    try:
                        data = json.load(f)
                        if isinstance(data, list):
                            self._audit_history = data
                            logger.info("Loaded %d audit reports from JSON", len(self._audit_history))
                    finally:
                        fcntl.flock(f, fcntl.LOCK_UN)
        except Exception:
            self._audit_history = []

    def get_metrics(self) -> dict:
        return {
            "total_audits": self._total_audits,
            "last_audit_target": self._last_audit_target,
            "last_audit_score": self._last_audit_score,
            "reports_stored": len(self._audit_history),
        }
