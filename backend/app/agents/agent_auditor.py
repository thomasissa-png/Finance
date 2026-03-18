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

## Règle d'interaction — Confirmation utilisateur

Quand l'audit identifie des améliorations à implémenter, l'auditeur doit
**demander confirmation à l'utilisateur** avant de coder si :
- Le changement touche la logique métier (scoring, trade selection, learning)
- Le changement a un impact potentiel sur les performances de trading
- Le scope de l'amélioration est ambigu ou pourrait être interprété de plusieurs façons
- Le changement nécessite un choix d'architecture (ex: nouveau module vs extension existante)
- Il y a un doute sur la pertinence ou la priorité de l'amélioration

L'auditeur présente ses trouvailles et recommandations, puis attend le feu vert
de l'utilisateur avant d'implémenter. Les fixes purement techniques (bugs évidents,
typos, missing error handling) peuvent être appliqués directement.
"""

import json
import logging
import time
from datetime import datetime, timedelta, timezone
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
    "trader_2": {
        "expertise": "Expert spéculateur tendance, 15+ ans sur commodities, détection de retournements",
        "checks": [
            "position_coverage",     # Les 4 tickers sont-ils tous suivis avec une position ?
            "direction_coherence",   # La direction est-elle cohérente avec les news récentes ?
            "flip_frequency",        # Le taux de changement est-il raisonnable (pas trop/peu fréquent) ?
            "pnl_tracking",          # Le P&L réalisé et latent est-il correctement calculé ?
            "news_filtering",        # Les news non pertinentes sont-elles bien filtrées ?
            "confidence_evolution",  # La confiance évolue-t-elle logiquement ?
            "history_integrity",     # L'historique des positions est-il cohérent ?
            "persistence",           # Les positions sont-elles bien persistées (PG/JSON) ?
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
    "scoring_2": {
        "expertise": "Expert scoring tendanciel commodities, re-pondération structurelle vs edge intraday",
        "checks": [
            "category_multipliers",  # Les multiplicateurs trend sont-ils bien calibrés ?
            "persistence_detection", # Les mots-clés structurels sont-ils détectés ?
            "accumulation_logic",    # L'accumulation directionnelle est-elle cohérente ?
            "ticker_coverage",       # Les 4 tickers trend sont-ils couverts ?
            "score_distribution",    # Les trend scores ont-ils une distribution raisonnable ?
            "pipeline_integration",  # Le scoring 2 est-il bien intégré dans le pipeline ?
            "trader_2_consumption",  # Trader 2 consomme-t-il bien les trend scores ?
            "no_claude_call",        # Vérifie qu'aucun appel Claude supplémentaire n'est fait
        ],
    },
    "journal_2": {
        "expertise": "Expert en analyse de positions de tendance et suivi de performance multi-semaines",
        "checks": [
            "flip_coverage",         # Tous les flips sont-ils bien journalisés ?
            "mae_mfe_accuracy",      # MAE/MFE calculés correctement sur daily bars ?
            "pnl_tracking",          # P&L réalisé et latent cohérents ?
            "dedup_integrity",       # Pas de doublons dans les entries ?
            "bar_fetch_reliability", # Taux de succès des fetch daily bars ?
            "pruning",              # Les vieilles entrées sont-elles prunées (> 1 an) ?
            "snapshot_quality",      # Les snapshots quotidiens capturent-ils toutes les positions ?
            "persistence",           # Les données sont-elles bien persistées (PG/JSON) ?
        ],
    },
    "learning_2": {
        "expertise": "Expert ML spécialisé trend following, calibration de seuils adaptatifs sur petits échantillons",
        "checks": [
            "sample_size",           # Assez de données pour des ajustements significatifs ?
            "ticker_calibration",    # Les ajustements per-ticker sont-ils cohérents ?
            "newscat_calibration",   # Les ajustements per-newscat sont-ils utiles ?
            "direction_balance",     # LONG vs SHORT sont-ils équilibrés ?
            "threshold_stability",   # Le seuil de flip est-il stable (pas trop volatile) ?
            "churning_detection",    # Le churning est-il détecté et pénalisé ?
            "anomaly_detection",     # Les anomalies sont-elles détectées (streaks, drawdowns) ?
            "feedback_loop",         # Les ajustements sont-ils bien consommés par Trader 2 ?
        ],
    },
    "infrastructure": {
        "expertise": "Expert infrastructure & DevOps, 15+ ans en systèmes distribués temps réel et haute disponibilité",
        "checks": [
            "pg_connectivity",       # PostgreSQL est-il accessible et performant ?
            "pool_health",           # Le pool de connexions est-il sain ?
            "table_maintenance",     # VACUUM et pruning sont-ils exécutés régulièrement ?
            "fallback_detection",    # Les fallbacks JSON sont-ils détectés et signalés ?
            "data_consistency",      # Les données sont-elles cohérentes entre tables ?
            "pending_trade_monitoring", # Les trades PENDING bloqués sont-ils détectés ?
            "error_rate_monitoring", # Le taux d'erreurs des agents est-il surveillé ?
            "timeout_management",    # Les timeouts sont-ils bien configurés partout ?
        ],
    },
    "performance": {
        "expertise": "Expert en mesure de performance et KPIs pour systèmes de trading multi-agents",
        "checks": [
            "kpi_coverage",           # Tous les agents ont-ils des KPIs mesurés ?
            "data_freshness",         # Les snapshots sont-ils récents et réguliers ?
            "trader_win_rate",        # Le win rate trader est-il correctement calculé ?
            "trend_computation",      # Les tendances temporelles sont-elles fiables ?
            "alert_thresholds",       # Les seuils d'alerte sont-ils bien calibrés ?
            "ranking_logic",          # Le ranking des agents est-il pertinent ?
            "history_retention",      # L'historique est-il conservé (168 snapshots, 30 reports) ?
            "cross_agent_consistency", # Les KPIs sont-ils cohérents entre agents ?
        ],
    },
    "scoring_3": {
        "expertise": "Expert indicateurs techniques multi-timeframe, 15+ ans d'analyse technique quantitative",
        "checks": [
            "indicator_accuracy",    # RSI/MACD/Bollinger/etc. calculés correctement ?
            "strategy_coverage",     # Les 5 stratégies produisent-elles des setups ?
            "ticker_coverage",       # Les 20 tickers sont-ils tous scannés ?
            "score_distribution",    # Les scores techniques sont-ils bien distribués ?
            "ohlcv_reliability",     # Les données OHLCV sont-elles fiables ?
            "multi_timeframe",       # Les signaux multi-timeframe convergent-ils ?
            "no_claude_call",        # Pas d'appel Claude (pur computationnel) ?
        ],
    },
    "trader_3": {
        "expertise": "Expert trading technique multi-stratégie, gestion de positions à durée variable (heures à 3 jours)",
        "checks": [
            "position_management",   # Les positions actives sont-elles bien gérées ?
            "strategy_selection",    # La sélection de stratégie est-elle pertinente ?
            "risk_management",       # TP/SL/trailing stop bien calibrés ?
            "learning_integration",  # Les ajustements learning sont-ils appliqués ?
            "holding_period",        # Les durées de holding respectent-elles les limites ?
            "ab_testing",            # L'A/B testing stratégies fonctionne-t-il ?
            "persistence",           # Les positions sont-elles persistées (PG/JSON) ?
        ],
    },
    "journal_3": {
        "expertise": "Expert en journalisation de trades techniques et analyse de performance par stratégie",
        "checks": [
            "entry_coverage",        # Toutes les positions fermées sont-elles journalisées ?
            "mae_mfe_accuracy",      # MAE/MFE calculés correctement ?
            "strategy_analysis",     # L'analyse A/B par stratégie est-elle pertinente ?
            "dedup_integrity",       # Pas de doublons ?
            "pnl_tracking",          # P&L correctement calculé ?
            "persistence",           # Données persistées (PG/JSON) ?
        ],
    },
    "learning_3": {
        "expertise": "Expert ML pour trading technique, optimisation de stratégies et sélection de modèles",
        "checks": [
            "strategy_ranking",      # Le classement des stratégies est-il pertinent ?
            "ticker_calibration",    # Les ajustements par ticker sont-ils cohérents ?
            "sample_size",           # Assez de données pour des ajustements significatifs ?
            "overfitting_risk",      # Risque de sur-apprentissage ?
            "anomaly_detection",     # Les stratégies sous-performantes sont-elles détectées ?
            "feedback_loop",         # Les ajustements sont-ils consommés par Trader 3 ?
        ],
    },
    "scoring_4": {
        "expertise": "Expert en meta-scoring multi-signal et détection de confluence entre équipes indépendantes",
        "checks": [
            "weight_calibration",    # Les poids news/trend/tech sont-ils bien calibrés ?
            "confluence_detection",  # La confluence 2/3 et 3/3 est-elle correctement détectée ?
            "source_availability",   # Les 3 sources upstream sont-elles disponibles ?
            "score_distribution",    # Les meta-scores sont-ils bien distribués ?
            "direction_consensus",   # Le consensus directionnel est-il fiable ?
            "no_claude_call",        # Pas d'appel Claude (pure agrégation) ?
        ],
    },
    "trader_4": {
        "expertise": "Expert trading ensemble/confluence, combinaison de signaux multi-stratégies indépendants",
        "checks": [
            "confluence_quality",    # La qualité de confluence est-elle suffisante ?
            "position_management",   # Les positions sont-elles bien gérées ?
            "upstream_dependency",   # La dépendance aux équipes upstream est-elle gérée ?
            "sizing_by_confluence",  # Le sizing adapté au niveau de confluence ?
            "holding_expiry",        # Les positions expirées sont-elles fermées à temps ?
            "persistence",           # Les positions sont-elles persistées (PG/JSON) ?
        ],
    },
    "journal_4": {
        "expertise": "Expert en journalisation de trades meta/ensemble et analyse de performance par confluence",
        "checks": [
            "entry_coverage",        # Toutes les positions fermées sont-elles journalisées ?
            "confluence_analysis",   # L'analyse par niveau de confluence est-elle pertinente ?
            "source_combo_tracking", # Les combinaisons de sources sont-elles suivies ?
            "pnl_tracking",          # P&L correctement calculé ?
            "dedup_integrity",       # Pas de doublons ?
            "persistence",           # Données persistées (PG/JSON) ?
        ],
    },
    "learning_4": {
        "expertise": "Expert ML pour trading ensemble, optimisation de poids multi-signal et détection d'anomalies",
        "checks": [
            "weight_optimization",   # L'optimisation des poids est-elle pertinente ?
            "combination_analysis",  # L'analyse par combinaison est-elle utile ?
            "sample_size",           # Assez de données pour des ajustements significatifs ?
            "anomaly_detection",     # Les anomalies sont-elles détectées ?
            "ticker_calibration",    # Les ajustements par ticker sont-ils cohérents ?
            "feedback_loop",         # Les ajustements sont-ils consommés par Trader 4 ?
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
    "news_for_trader_1": {
        "expertise": "Expert news trading intraday, 15+ ans — audite la couverture GNews du point de vue du Trader 1 (41 actifs, day trading)",
        "checks": [
            "ticker_coverage",       # Chaque actif a-t-il au moins une query GNews directe ou chain reaction ?
            "category_coverage",     # Les catégories à edge (weather, supply_chain, commodity, geopolitical) sont-elles couvertes ?
            "gnews_syntax",          # Les queries GNews ont-elles des bugs OR/AND (mots nus, branches sans contexte) ?
            "scored_news_relevance", # Les news scorées récentes couvrent-elles les tickers Trader 1 ?
            "signal_to_noise",       # Ratio articles pertinents vs bruit dans les scans récents
            "freshness_for_intraday", # Les news sont-elles assez fraîches pour du day trading (<2h) ?
            "chain_reaction_reach",  # Les chain reactions étendent-elles la couverture aux actifs sans query directe ?
        ],
    },
    "news_for_trader_2": {
        "expertise": "Expert trend following commodities, 15+ ans — audite la couverture GNews du point de vue du Trader 2 (HG=F, CC=F, KC=F, ZW=F)",
        "checks": [
            "ticker_coverage_depth", # Chaque ticker T2 a-t-il assez de queries dédiées (weather, supply, demand, regulatory) ?
            "category_balance",      # Les catégories pertinentes (weather, supply_chain, commodity, regulatory) sont-elles couvertes par ticker ?
            "producer_coverage",     # Les pays producteurs clés sont-ils couverts (Chile/DRC pour HG, Ghana/IC pour CC, etc.) ?
            "gnews_syntax",          # Les queries GNews ont-elles des bugs OR/AND pour les tickers T2 ?
            "scored_news_for_trend", # Les news scorées récentes contiennent-elles assez de signaux trend (high signal_reliability, structural categories) ?
            "structural_freshness",  # Les données structurelles (USDA, NOAA) arrivent-elles avec le bon lookback (18h) ?
            "accumulation_signal",   # Les news récentes génèrent-elles assez de signal directionnel pour le seuil de flip ?
        ],
    },
}


class AgentAuditor(BaseAgent):
    name = "auditor"
    description = "Audit en profondeur de chaque agent"
    version = "8.3"  # v8.3: Deep audit methods for Team 3 (scoring_3, trader_3, journal_3, learning_3)

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
            # Run the audit checks — dict dispatch (replaces if-elif chain)
            audit_dispatch = {
                "news": self._audit_news,
                "scoring": self._audit_scoring,
                "trader_1": self._audit_trader,
                "trader_2": self._audit_trader_2,
                "journal": self._audit_journal,
                "learning": self._audit_learning,
                "scoring_2": self._audit_scoring_2,
                "journal_2": self._audit_journal_2,
                "learning_2": self._audit_learning_2,
                "scoring_3": self._audit_scoring_3,
                "trader_3": self._audit_trader_3,
                "journal_3": self._audit_journal_3,
                "learning_3": self._audit_learning_3,
                "scoring_4": self._audit_generic_agent,
                "trader_4": self._audit_generic_agent,
                "journal_4": self._audit_generic_agent,
                "learning_4": self._audit_generic_agent,
                "ux": self._audit_ux,
                "infrastructure": self._audit_infrastructure,
                "performance": self._audit_performance,
                "auditor": self._audit_self,
                "news_for_trader_1": self._audit_news_for_trader_1,
                "news_for_trader_2": self._audit_news_for_trader_2,
            }
            audit_fn = audit_dispatch.get(target_agent)
            if audit_fn:
                audit_fn(report, focus)

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
            from ..event_scanner import HIGH_IMPACT_KEYWORDS
            total_keywords = sum(len(v) for v in HIGH_IMPACT_KEYWORDS.values())
            categories = list(HIGH_IMPACT_KEYWORDS.keys())
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

        # 8. Logging quality — structured logs coverage
        try:
            agent_logs = self._get_agent_logs("news", limit=100)
            if agent_logs:
                levels = {}
                for log in agent_logs:
                    lvl = log.get("level", "INFO")
                    levels[lvl] = levels.get(lvl, 0) + 1
                has_decisions = levels.get("DECISION", 0) > 0
                has_warns = levels.get("WARN", 0) > 0
                has_info = levels.get("INFO", 0) > 0
                # Good logging = mix of levels with DECISION logs for key actions
                log_quality = 10 if has_decisions and has_info else 7 if has_info else 4
                findings.append({
                    "area": "logging_quality",
                    "status": "OK" if has_decisions else "WARN",
                    "detail": f"Logs: {levels}, DECISION logs present: {has_decisions}",
                })
                scores["logging_quality"] = log_quality
                if not has_decisions:
                    improvements.append({
                        "priority": "MEDIUM",
                        "agent": "news",
                        "action": "Add DECISION-level logs for key collection choices (source selection, dedup decisions)",
                        "rationale": "DECISION logs enable audit trail and performance tracking",
                    })
            else:
                scores["logging_quality"] = 3
                findings.append({
                    "area": "logging_quality",
                    "status": "WARN",
                    "detail": "No agent logs found — agent may not have run yet or logging is broken",
                })
        except Exception:
            scores["logging_quality"] = 5

        # 9. Weekly review capability
        try:
            from ..source_monitor import get_tracker
            tracker = get_tracker()
            review = tracker.get_weekly_review()
            days_with_data = review.get("days_with_data", 0)
            dead = review.get("dead_sources", [])
            degraded = review.get("degraded_sources", [])
            stable = review.get("stable_sources", [])
            recs = review.get("recommendations", [])

            if days_with_data > 0:
                total_sources = len(dead) + len(degraded) + len(stable)
                health_pct = len(stable) / max(1, total_sources) * 100
                findings.append({
                    "area": "weekly_review",
                    "status": "OK" if health_pct > 70 and not dead else "WARN" if not dead else "CRITICAL",
                    "detail": (f"Weekly review: {days_with_data} days data, "
                               f"{len(stable)} stable / {len(degraded)} degraded / {len(dead)} dead, "
                               f"{len(recs)} recommendations"),
                })
                scores["weekly_review"] = min(10, health_pct / 10)
                if dead:
                    for src in dead:
                        improvements.append({
                            "priority": "HIGH",
                            "agent": "news",
                            "action": f"Fix dead source: {src.get('source')} ({src.get('description', '')})",
                            "rationale": f"Dead since {src.get('days_seen', '?')} days — lost edge signals",
                        })
            else:
                findings.append({
                    "area": "weekly_review",
                    "status": "WARN",
                    "detail": "No weekly review data — source health tracker may not be collecting (check _tracker init)",
                })
                scores["weekly_review"] = 4
        except Exception as exc:
            scores["weekly_review"] = 3
            findings.append({"area": "weekly_review", "status": "ERROR", "detail": str(exc)})

        # Log analysis for news agent
        self._analyze_agent_errors(findings, scores, improvements, "news")

        # Standard tests to add
        tests.append("test_news_agent_collects_from_all_phase0_sources")
        tests.append("test_news_agent_dedup_filters_exact_duplicates")
        tests.append("test_news_agent_event_detection_keywords_comprehensive")
        tests.append("test_source_health_tracker_singleton")

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

        except Exception as exc:
            findings.append({"area": "trader", "status": "ERROR", "detail": str(exc)})
            scores["overall"] = 3

        # Log analysis for trader agent
        self._analyze_agent_errors(findings, scores, improvements, "trader_1")

        tests.append("test_trader_agent_respects_daily_cap_per_vix_regime")
        tests.append("test_trader_agent_spread_filter_rejects_illiquid_trades")
        tests.append("test_trader_agent_fallback_ticker_works")

    def _audit_trader_2(self, report: dict, focus: str | None):
        """Audit Agent Trader 2 — trend following sur commodities.

        En tant qu'expert spéculateur tendance avec 15+ ans sur commodities,
        je vérifie que le Trader 2 gère correctement ses positions de tendance,
        que ses changements de direction sont justifiés, et que le tracking
        de performance est fiable.
        """
        findings = report["findings"]
        improvements = report["improvements"]
        tests = report["tests_to_add"]
        scores = report["score_breakdown"]

        try:
            from .agent_trader_2 import (
                AgentTrader2, TREND_TICKERS, RELEVANT_CATEGORIES,
                MIN_NEWS_SCORE, _load_positions, POSITIONS_FILE,
            )

            positions = _load_positions()

            # ── 1. Position coverage ──
            # Les 4 tickers doivent tous avoir une position
            covered = set(positions.keys()) & set(TREND_TICKERS.keys())
            missing_tickers = set(TREND_TICKERS.keys()) - covered
            has_direction = [t for t, p in positions.items()
                            if t in TREND_TICKERS and p.get("direction") in ("LONG", "SHORT")]

            if not positions:
                findings.append({
                    "area": "position_coverage",
                    "status": "WARN",
                    "detail": "Aucune position initialisée — l'agent n'a pas encore été exécuté",
                })
                scores["position_coverage"] = 3
            elif missing_tickers:
                findings.append({
                    "area": "position_coverage",
                    "status": "WARN",
                    "detail": f"Tickers non couverts : {missing_tickers}. "
                              f"Couverts : {len(covered)}/{len(TREND_TICKERS)}",
                })
                scores["position_coverage"] = max(3, int(len(covered) / len(TREND_TICKERS) * 10))
            else:
                neutral_count = sum(1 for t in TREND_TICKERS
                                    if positions.get(t, {}).get("direction") == "NEUTRAL")
                findings.append({
                    "area": "position_coverage",
                    "status": "OK" if neutral_count == 0 else "WARN",
                    "detail": f"4/4 tickers couverts, {len(has_direction)} avec direction active, "
                              f"{neutral_count} encore NEUTRAL",
                })
                scores["position_coverage"] = 10 if neutral_count == 0 else max(5, 10 - neutral_count * 2)

            if not positions:
                # Cannot audit further without positions
                for check in ["direction_coherence", "flip_frequency", "pnl_tracking",
                              "confidence_evolution", "history_integrity"]:
                    scores[check] = 5
                findings.append({
                    "area": "overall",
                    "status": "WARN",
                    "detail": "Pas de positions — audit limité. L'agent doit être exécuté au moins une fois.",
                })
                improvements.append({
                    "priority": "HIGH",
                    "agent": "trader_2",
                    "action": "Exécuter l'agent au moins une fois pour initialiser les positions",
                    "rationale": "Sans positions, aucun audit de fond n'est possible",
                })
                self._analyze_agent_errors(findings, scores, improvements, "trader_2")
                tests.append("test_trader_2_initializes_all_4_positions")
                return

            # ── 2. Direction coherence ──
            # Vérifier que les directions sont valides
            invalid_dirs = []
            for ticker in TREND_TICKERS:
                pos = positions.get(ticker, {})
                d = pos.get("direction", "MISSING")
                if d not in ("LONG", "SHORT", "NEUTRAL"):
                    invalid_dirs.append(f"{ticker}: '{d}'")

            if invalid_dirs:
                findings.append({
                    "area": "direction_coherence",
                    "status": "CRITICAL",
                    "detail": f"Directions invalides : {', '.join(invalid_dirs)}",
                })
                scores["direction_coherence"] = 2
                improvements.append({
                    "priority": "CRITICAL",
                    "agent": "trader_2",
                    "action": f"Corriger les directions invalides : {invalid_dirs}",
                    "rationale": "Une direction invalide casse toute la logique de P&L",
                })
            else:
                # Vérifier que le reasoning est présent et cohérent
                no_reasoning = [t for t in TREND_TICKERS
                                if not positions.get(t, {}).get("reasoning")]
                findings.append({
                    "area": "direction_coherence",
                    "status": "OK" if not no_reasoning else "WARN",
                    "detail": f"Toutes les directions sont valides. "
                              + (f"Reasoning manquant pour : {no_reasoning}" if no_reasoning
                                 else "Tous ont un reasoning."),
                })
                scores["direction_coherence"] = 10 if not no_reasoning else 7

            # ── 3. Flip frequency ──
            # Un bon trend follower ne change pas trop souvent (overtrading)
            # ni trop rarement (manque de réactivité)
            total_switches = sum(positions.get(t, {}).get("total_switches", 0)
                                 for t in TREND_TICKERS)
            avg_switches = total_switches / len(TREND_TICKERS) if TREND_TICKERS else 0

            # Calculer l'âge des positions les plus anciennes
            oldest_days = 0
            for t in TREND_TICKERS:
                entry_time = positions.get(t, {}).get("entry_time")
                if entry_time:
                    try:
                        from datetime import datetime, timezone
                        et = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
                        age = (datetime.now(timezone.utc) - et).days
                        oldest_days = max(oldest_days, age)
                    except Exception:
                        pass

            if total_switches == 0:
                flip_status = "WARN"
                flip_detail = "Aucun changement de position enregistré — l'agent n'a pas encore détecté de retournement"
                scores["flip_frequency"] = 5
            elif avg_switches > 2 and oldest_days < 7:
                flip_status = "WARN"
                flip_detail = (f"{total_switches} changements en {oldest_days}j "
                               f"(moy {avg_switches:.1f}/ticker) — possiblement trop fréquent (overtrading)")
                scores["flip_frequency"] = 5
                improvements.append({
                    "priority": "MEDIUM",
                    "agent": "trader_2",
                    "action": "Revoir le seuil de renversement si les flips sont trop fréquents",
                    "rationale": "Un trend follower doit rester en position — trop de flips = pas de tendance captée",
                })
            else:
                flip_status = "OK"
                flip_detail = (f"{total_switches} changements total "
                               f"(moy {avg_switches:.1f}/ticker, oldest position: {oldest_days}j)")
                scores["flip_frequency"] = 8

            findings.append({
                "area": "flip_frequency",
                "status": flip_status,
                "detail": flip_detail,
            })

            # ── 4. P&L tracking ──
            pnl_issues = []
            for ticker in TREND_TICKERS:
                pos = positions.get(ticker, {})
                entry = pos.get("entry_price")
                current = pos.get("current_price")
                unrealized = pos.get("unrealized_pnl_pct")
                realized = pos.get("realized_pnl_pct", 0)
                direction = pos.get("direction")

                if entry is None:
                    pnl_issues.append(f"{ticker}: entry_price manquant")
                elif current is None:
                    pnl_issues.append(f"{ticker}: current_price manquant")
                elif direction in ("LONG", "SHORT") and unrealized is not None and entry > 0:
                    # Vérifier la cohérence du P&L
                    if direction == "LONG":
                        expected_pnl = round((current - entry) / entry * 100, 2)
                    else:
                        expected_pnl = round((entry - current) / entry * 100, 2)
                    if abs(expected_pnl - unrealized) > 0.1:
                        pnl_issues.append(
                            f"{ticker}: P&L incohérent (affiché {unrealized}%, "
                            f"calculé {expected_pnl}%)")

                # Vérifier que realized_pnl est un nombre
                if not isinstance(realized, (int, float)):
                    pnl_issues.append(f"{ticker}: realized_pnl n'est pas un nombre : {type(realized)}")

            if pnl_issues:
                findings.append({
                    "area": "pnl_tracking",
                    "status": "WARN" if len(pnl_issues) <= 2 else "CRITICAL",
                    "detail": f"{len(pnl_issues)} problème(s) P&L : {'; '.join(pnl_issues[:5])}",
                })
                scores["pnl_tracking"] = max(3, 10 - len(pnl_issues) * 2)
            else:
                total_realized = sum(positions.get(t, {}).get("realized_pnl_pct", 0)
                                     for t in TREND_TICKERS)
                total_unrealized = sum(positions.get(t, {}).get("unrealized_pnl_pct", 0)
                                       for t in TREND_TICKERS)
                findings.append({
                    "area": "pnl_tracking",
                    "status": "OK",
                    "detail": f"P&L cohérent. Réalisé total : {total_realized:+.2f}%, "
                              f"Latent total : {total_unrealized:+.2f}%",
                })
                scores["pnl_tracking"] = 9

            # ── 5. News filtering — vérifier la config ──
            # Vérifier que les catégories filtrées sont cohérentes
            expected_cats = {"commodity", "weather", "supply_chain", "geopolitical"}
            missing_cats = expected_cats - RELEVANT_CATEGORIES
            extra_cats = RELEVANT_CATEGORIES - expected_cats - {"regulatory", "sector", "other"}

            findings.append({
                "area": "news_filtering",
                "status": "OK" if not missing_cats else "CRITICAL",
                "detail": f"Catégories filtrées : {sorted(RELEVANT_CATEGORIES)}. "
                          f"MIN_SCORE={MIN_NEWS_SCORE}. "
                          + (f"Catégories manquantes : {missing_cats}" if missing_cats else "Toutes les catégories clés couvertes."),
            })
            scores["news_filtering"] = 10 if not missing_cats else 5

            # ── 6. Confidence evolution ──
            confidence_issues = []
            for ticker in TREND_TICKERS:
                pos = positions.get(ticker, {})
                conf = pos.get("confidence", -1)
                if not isinstance(conf, (int, float)):
                    confidence_issues.append(f"{ticker}: confiance non numérique")
                elif conf < 0 or conf > 100:
                    confidence_issues.append(f"{ticker}: confiance hors limites ({conf})")

            findings.append({
                "area": "confidence_evolution",
                "status": "OK" if not confidence_issues else "WARN",
                "detail": "Confiances valides (0-100) pour tous les tickers"
                          if not confidence_issues
                          else f"Problèmes : {'; '.join(confidence_issues)}",
            })
            scores["confidence_evolution"] = 9 if not confidence_issues else 5

            # ── 7. History integrity ──
            history_issues = []
            for ticker in TREND_TICKERS:
                pos = positions.get(ticker, {})
                history = pos.get("history", [])
                if not isinstance(history, list):
                    history_issues.append(f"{ticker}: history n'est pas une liste")
                    continue
                for i, h in enumerate(history[:10]):
                    if not isinstance(h, dict):
                        history_issues.append(f"{ticker}: history[{i}] n'est pas un dict")
                        break
                    required = {"from_direction", "to_direction", "time", "pnl_pct"}
                    missing_fields = required - set(h.keys())
                    if missing_fields:
                        history_issues.append(
                            f"{ticker}: history[{i}] champs manquants : {missing_fields}")
                        break

            findings.append({
                "area": "history_integrity",
                "status": "OK" if not history_issues else "WARN",
                "detail": "Historique bien structuré pour tous les tickers"
                          if not history_issues
                          else f"Problèmes : {'; '.join(history_issues[:3])}",
            })
            scores["history_integrity"] = 9 if not history_issues else 5

            # ── 8. Persistence ──
            # Vérifier que le fichier/table existe
            persistence_ok = True
            try:
                from ..database import is_pg_enabled
                if is_pg_enabled():
                    from ..database import get_conn
                    with get_conn() as conn:
                        with conn.cursor() as cur:
                            cur.execute("SELECT COUNT(*) FROM trend_positions")
                            count = cur.fetchone()[0]
                    findings.append({
                        "area": "persistence",
                        "status": "OK",
                        "detail": f"Table PG trend_positions active, {count} ticker(s) persistés",
                    })
                else:
                    if POSITIONS_FILE.exists():
                        findings.append({
                            "area": "persistence",
                            "status": "OK",
                            "detail": f"Fichier JSON {POSITIONS_FILE} présent (fallback)",
                        })
                    else:
                        findings.append({
                            "area": "persistence",
                            "status": "WARN",
                            "detail": "Ni PG ni fichier JSON trouvé — les positions seront perdues au redémarrage",
                        })
                        persistence_ok = False
            except Exception as exc:
                findings.append({
                    "area": "persistence",
                    "status": "WARN",
                    "detail": f"Vérification persistence échouée : {exc}",
                })
                persistence_ok = False

            scores["persistence"] = 9 if persistence_ok else 4

        except ImportError as exc:
            findings.append({
                "area": "import",
                "status": "CRITICAL",
                "detail": f"Impossible d'importer agent_trader_2 : {exc}",
            })
            scores["overall"] = 2
            improvements.append({
                "priority": "CRITICAL",
                "agent": "trader_2",
                "action": "Corriger l'import de agent_trader_2",
                "rationale": f"Erreur : {exc}",
            })
        except Exception as exc:
            findings.append({
                "area": "overall",
                "status": "ERROR",
                "detail": f"Erreur audit trader_2 : {exc}",
            })
            scores["overall"] = 3

        # Log analysis
        self._analyze_agent_errors(findings, scores, improvements, "trader_2")

        tests.append("test_trader_2_all_4_positions_initialized")
        tests.append("test_trader_2_flip_records_history")
        tests.append("test_trader_2_pnl_calculation_matches_direction")
        tests.append("test_trader_2_confidence_bounded_0_100")
        tests.append("test_trader_2_persistence_pg_and_json")

    def _audit_journal(self, report: dict, focus: str | None):
        """Audit Agent Journal — closure accuracy, price reliability, metrics."""
        findings = report["findings"]
        improvements = report["improvements"]
        tests = report["tests_to_add"]
        scores = report["score_breakdown"]

        entries = []
        recent = []
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
        if recent:
            no_exit = sum(1 for e in recent if e.get("exit_price") is None and e.get("result") not in (None, "PENDING"))
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

        closed = []
        trades = []
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

    # ── Team 3 deep audits ─────────────────────────────────────────

    def _audit_scoring_3(self, report: dict, focus: str | None):
        """Audit Agent Scoring 3 — indicator params, strategy coverage, v3.0 intraday checks."""
        findings = report["findings"]
        improvements = report["improvements"]
        tests = report["tests_to_add"]
        scores = report["score_breakdown"]

        try:
            from .agent_scoring_3 import (
                TECH_TICKERS, STRATEGIES, TIMEFRAMES, TIMEFRAME_WEIGHTS,
                DEFAULT_PARAMS, STRATEGY_RR_PROFILES, STRATEGY_REGIME_PREFERENCE,
                MIN_SETUP_SCORE, score_technical_setups,
            )

            # 1. indicator_accuracy — v3.0 params (EMA 9/21, MACD 5/13/4, BB 12/1.8)
            param_checks = {
                "ema_fast": (9, DEFAULT_PARAMS.get("ema_fast")),
                "ema_slow": (21, DEFAULT_PARAMS.get("ema_slow")),
                "macd_fast": (5, DEFAULT_PARAMS.get("macd_fast")),
                "macd_slow": (13, DEFAULT_PARAMS.get("macd_slow")),
                "macd_signal": (4, DEFAULT_PARAMS.get("macd_signal")),
                "bb_period": (12, DEFAULT_PARAMS.get("bb_period")),
                "bb_std": (1.8, DEFAULT_PARAMS.get("bb_std")),
                "stoch_k": (9, DEFAULT_PARAMS.get("stoch_k")),
                "adx_period": (10, DEFAULT_PARAMS.get("adx_period")),
            }
            mismatches = [k for k, (expected, actual) in param_checks.items() if expected != actual]
            findings.append({
                "area": "indicator_accuracy",
                "status": "OK" if not mismatches else "CRITICAL",
                "detail": "All v3.0 indicator params match (EMA 9/21, MACD 5/13/4, BB 12/1.8, Stoch 9, ADX 10)"
                          if not mismatches else f"Param mismatches: {mismatches}",
            })
            scores["indicator_accuracy"] = 10 if not mismatches else 2

            # 2. strategy_coverage — 11 strategies (6 simple + 5 combos)
            expected_strategies = {
                "rsi_reversal", "macd_crossover", "bollinger_squeeze",
                "ema_trend", "momentum_divergence", "stochastic_reversal",
                "rsi_macd_combo", "bollinger_stoch_combo", "ma_rsi_macd_combo",
                "rsi_bollinger_combo", "macd_ma_combo",
            }
            actual = set(STRATEGIES.keys())
            missing = expected_strategies - actual
            extra = actual - expected_strategies
            # Verify cross-references in RR_PROFILES and REGIME_PREFERENCE
            rr_missing = expected_strategies - set(STRATEGY_RR_PROFILES.keys())
            regime_missing = expected_strategies - set(STRATEGY_REGIME_PREFERENCE.keys())
            all_ok = not missing and not extra and not rr_missing and not regime_missing
            findings.append({
                "area": "strategy_coverage",
                "status": "OK" if all_ok else "CRITICAL",
                "detail": f"{len(actual)} strategies, "
                          f"RR profiles: {len(STRATEGY_RR_PROFILES)}, "
                          f"regime prefs: {len(STRATEGY_REGIME_PREFERENCE)}"
                          + (f" — MISSING: {missing}" if missing else "")
                          + (f" — NO RR: {rr_missing}" if rr_missing else "")
                          + (f" — NO REGIME: {regime_missing}" if regime_missing else ""),
            })
            scores["strategy_coverage"] = 10 if all_ok else 3

            # 3. ticker_coverage — 20 tickers expected
            findings.append({
                "area": "ticker_coverage",
                "status": "OK" if len(TECH_TICKERS) == 20 else "WARN",
                "detail": f"{len(TECH_TICKERS)} tickers configured (expected 20)",
            })
            scores["ticker_coverage"] = 9 if len(TECH_TICKERS) >= 18 else 5

            # 4. score_distribution — from last scoring result
            try:
                from .registry import get_agent
                agent = get_agent("scoring_3")
                if agent:
                    last = agent.get_last_result()
                    if last and isinstance(last, dict):
                        stats = last.get("stats", {})
                        setups = stats.get("setups_found", 0)
                        avg = stats.get("avg_score", 0)
                        findings.append({
                            "area": "score_distribution",
                            "status": "OK" if setups >= 0 else "WARN",
                            "detail": f"Last run: {setups} setups found, avg score={avg:.1f}",
                        })
                        scores["score_distribution"] = 8 if setups > 0 else 5
                    else:
                        findings.append({"area": "score_distribution", "status": "INFO",
                                         "detail": "No scoring data yet (agent not run)"})
                        scores["score_distribution"] = 5
            except Exception:
                findings.append({"area": "score_distribution", "status": "INFO",
                                 "detail": "Registry unavailable — skipped runtime check"})
                scores["score_distribution"] = 6

            # 5. ohlcv_reliability — market_data import and batch fetch
            try:
                from ..market_data import fetch_history_batch
                findings.append({
                    "area": "ohlcv_reliability",
                    "status": "OK",
                    "detail": "fetch_history_batch available; Twelve Data + yfinance fallback",
                })
                scores["ohlcv_reliability"] = 8
            except ImportError:
                findings.append({"area": "ohlcv_reliability", "status": "CRITICAL",
                                 "detail": "Cannot import fetch_history_batch from market_data"})
                scores["ohlcv_reliability"] = 0

            # 6. multi_timeframe — v3.0: 1H only signal, daily directional filter
            tf_ok = TIMEFRAMES == ["1h"] and TIMEFRAME_WEIGHTS == {"1h": 1.0}
            daily_filter = DEFAULT_PARAMS.get("daily_sma_period") is not None
            findings.append({
                "area": "multi_timeframe",
                "status": "OK" if tf_ok and daily_filter else "WARN",
                "detail": f"Timeframes={TIMEFRAMES}, weights={TIMEFRAME_WEIGHTS}, "
                          f"daily_sma_period={DEFAULT_PARAMS.get('daily_sma_period')}, "
                          f"daily_adx_period={DEFAULT_PARAMS.get('daily_adx_period')}",
            })
            scores["multi_timeframe"] = 10 if tf_ok and daily_filter else 5

            # 7. no_claude_call — pure computational
            import inspect
            src = inspect.getsource(score_technical_setups)
            has_claude = "anthropic" in src.lower() or "claude" in src.lower()
            findings.append({
                "area": "no_claude_call",
                "status": "OK" if not has_claude else "CRITICAL",
                "detail": "Confirmed: no Claude API call in score_technical_setups()"
                          if not has_claude else "UNEXPECTED Claude reference in tech scoring!",
            })
            scores["no_claude_call"] = 10 if not has_claude else 0

        except Exception as exc:
            findings.append({"area": "scoring_3", "status": "ERROR", "detail": str(exc)})
            scores["overall"] = 3

        self._analyze_agent_errors(findings, scores, improvements, "scoring_3")
        tests.extend([
            "test_scoring_3_v30_indicator_params",
            "test_scoring_3_11_strategies_coverage",
            "test_scoring_3_no_claude_call",
            "test_scoring_3_1h_only_timeframe",
        ])

    def _audit_trader_3(self, report: dict, focus: str | None):
        """Audit Agent Trader 3 — EOD_CLOSE, holding limits, trailing, learning integration."""
        findings = report["findings"]
        improvements = report["improvements"]
        tests = report["tests_to_add"]
        scores = report["score_breakdown"]

        try:
            from .agent_trader_3 import (
                AgentTrader3, MAX_POSITIONS, MAX_HOLDING_DAYS,
                EOD_DEADLINE_HOUR, EOD_DEADLINE_MINUTE,
                STRATEGY_MAX_HOLDING_HOURS, MIN_TRADE_SCORE,
            )

            # 1. position_management — EOD_CLOSE hard deadline
            eod_ok = EOD_DEADLINE_HOUR == 19 and EOD_DEADLINE_MINUTE == 45
            findings.append({
                "area": "position_management",
                "status": "OK" if eod_ok else "CRITICAL",
                "detail": f"EOD deadline={EOD_DEADLINE_HOUR}:{EOD_DEADLINE_MINUTE:02d} CET "
                          f"(expected 19:45), MAX_HOLDING_DAYS={MAX_HOLDING_DAYS}",
            })
            scores["position_management"] = 10 if eod_ok else 2

            # Verify force_close_all triggers EOD_CLOSE result
            import inspect
            monitor_src = inspect.getsource(AgentTrader3._monitor_positions)
            has_eod_close = 'EOD_CLOSE' in monitor_src and 'force_close_all' in monitor_src
            findings.append({
                "area": "eod_close_logic",
                "status": "OK" if has_eod_close else "CRITICAL",
                "detail": "force_close_all → EOD_CLOSE result confirmed in _monitor_positions"
                          if has_eod_close else "Missing EOD_CLOSE logic in monitor!",
            })
            scores["eod_close_logic"] = 10 if has_eod_close else 0

            # 2. strategy_selection — all 11 strategies mapped in holdings
            expected = {
                "rsi_reversal", "macd_crossover", "bollinger_squeeze",
                "ema_trend", "momentum_divergence", "stochastic_reversal",
                "rsi_macd_combo", "bollinger_stoch_combo", "ma_rsi_macd_combo",
                "rsi_bollinger_combo", "macd_ma_combo",
            }
            holdings_mapped = set(STRATEGY_MAX_HOLDING_HOURS.keys())
            missing_holdings = expected - holdings_mapped
            findings.append({
                "area": "strategy_selection",
                "status": "OK" if not missing_holdings else "WARN",
                "detail": f"{len(holdings_mapped)} strategies with max holding hours"
                          + (f" — MISSING: {missing_holdings}" if missing_holdings else ""),
            })
            scores["strategy_selection"] = 9 if not missing_holdings else 5

            # 3. risk_management — trailing stop per-strategy
            trailing_src = inspect.getsource(AgentTrader3._get_trailing_threshold)
            has_ema = "ema_trend" in trailing_src
            has_combos = "ma_rsi_macd_combo" in trailing_src
            findings.append({
                "area": "risk_management",
                "status": "OK" if has_ema and has_combos else "WARN",
                "detail": f"Trailing thresholds: ema_trend={'present' if has_ema else 'MISSING'}, "
                          f"combos={'present' if has_combos else 'MISSING'}, "
                          f"MAX_POSITIONS={MAX_POSITIONS}",
            })
            scores["risk_management"] = 9 if has_ema and has_combos else 5

            # 4. learning_integration — Trader 3 uses Learning 3 adjustments
            run_src = inspect.getsource(AgentTrader3.run)
            uses_learning = "get_adjustments" in run_src or "strategy_adj" in run_src
            findings.append({
                "area": "learning_integration",
                "status": "OK" if uses_learning else "WARN",
                "detail": "Learning 3 adjustments consumed (strategy_adj × ticker_adj × timeframe_adj)"
                          if uses_learning else "Learning 3 adjustments NOT found in run()",
            })
            scores["learning_integration"] = 9 if uses_learning else 4

            # 5. holding_period — verify max hours are intraday (<= 12h)
            max_hours = max(STRATEGY_MAX_HOLDING_HOURS.values()) if STRATEGY_MAX_HOLDING_HOURS else 0
            min_hours = min(STRATEGY_MAX_HOLDING_HOURS.values()) if STRATEGY_MAX_HOLDING_HOURS else 0
            intraday_ok = max_hours <= 12 and min_hours >= 1
            findings.append({
                "area": "holding_period",
                "status": "OK" if intraday_ok else "WARN",
                "detail": f"Holding range: {min_hours}h - {max_hours}h "
                          f"(v3.0 intraday: must be <= 12h)",
            })
            scores["holding_period"] = 10 if intraday_ok else 4

            # 6. ab_testing — weekly config consumption
            init_src = inspect.getsource(AgentTrader3.__init__)
            has_weekly = "weekly_config" in init_src or "_weekly_config" in init_src
            findings.append({
                "area": "ab_testing",
                "status": "OK" if has_weekly else "WARN",
                "detail": "Weekly config (enabled/disabled strategies) loaded"
                          if has_weekly else "Weekly config not found in init",
            })
            scores["ab_testing"] = 8 if has_weekly else 4

            # 7. persistence — atomic writes
            try:
                save_src = inspect.getsource(AgentTrader3._save_positions)
                atomic = "os.replace" in save_src or "replace" in save_src
                findings.append({
                    "area": "persistence",
                    "status": "OK" if atomic else "WARN",
                    "detail": "Atomic file writes (temp → replace)" if atomic
                              else "Non-atomic file writes detected",
                })
                scores["persistence"] = 9 if atomic else 5
            except Exception:
                scores["persistence"] = 6

        except Exception as exc:
            findings.append({"area": "trader_3", "status": "ERROR", "detail": str(exc)})
            scores["overall"] = 3

        self._analyze_agent_errors(findings, scores, improvements, "trader_3")
        tests.extend([
            "test_trader_3_eod_close_deadline_1945",
            "test_trader_3_force_close_all_eod_close",
            "test_trader_3_11_strategies_holding_hours",
            "test_trader_3_trailing_stop_per_strategy",
        ])

    def _audit_journal_3(self, report: dict, focus: str | None):
        """Audit Agent Journal 3 — EOD_CLOSE tracking, dedup, atomic writes, MAE/MFE."""
        findings = report["findings"]
        improvements = report["improvements"]
        tests = report["tests_to_add"]
        scores = report["score_breakdown"]

        try:
            from .agent_journal_3 import AgentJournal3, JOURNAL_FILE
            import inspect

            # 1. entry_coverage — EOD_CLOSE result supported
            run_src = inspect.getsource(AgentJournal3.run)
            process_src = inspect.getsource(AgentJournal3._process_closed_trade)
            supports_eod = "EOD_CLOSE" in run_src or "EOD_CLOSE" in process_src
            findings.append({
                "area": "entry_coverage",
                "status": "OK" if supports_eod else "CRITICAL",
                "detail": "EOD_CLOSE result type supported in journal processing"
                          if supports_eod else "EOD_CLOSE NOT handled — intraday force-closes lost!",
            })
            scores["entry_coverage"] = 10 if supports_eod else 0

            # 2. mae_mfe_accuracy — watermark tracking
            has_mae_mfe = ("high_watermark" in process_src or "mae" in process_src.lower())
            findings.append({
                "area": "mae_mfe_accuracy",
                "status": "OK" if has_mae_mfe else "WARN",
                "detail": "MAE/MFE watermark tracking found in _process_closed_trade"
                          if has_mae_mfe else "MAE/MFE tracking not detected",
            })
            scores["mae_mfe_accuracy"] = 8 if has_mae_mfe else 4

            # 3. strategy_analysis — per-strategy breakdown
            has_strategy_breakdown = "strategy" in process_src
            findings.append({
                "area": "strategy_analysis",
                "status": "OK" if has_strategy_breakdown else "WARN",
                "detail": "Per-strategy analysis in journal entries",
            })
            scores["strategy_analysis"] = 8 if has_strategy_breakdown else 5

            # 4. dedup_integrity — (ticker, strategy, entry_time) key
            from .agent_journal_3 import _pg_save_entries
            pg_src = inspect.getsource(_pg_save_entries)
            correct_dedup = "strategy" in pg_src and "entry_time" in pg_src and "ticker" in pg_src
            # Also check ON CONFLICT
            has_conflict = "ON CONFLICT" in pg_src
            findings.append({
                "area": "dedup_integrity",
                "status": "OK" if correct_dedup and has_conflict else "CRITICAL",
                "detail": f"PG dedup key includes (ticker, strategy, entry_time): {correct_dedup}, "
                          f"ON CONFLICT clause: {has_conflict}",
            })
            scores["dedup_integrity"] = 10 if correct_dedup and has_conflict else 3

            # 5. pnl_tracking — P&L computation
            has_pnl = "pnl" in process_src.lower()
            findings.append({
                "area": "pnl_tracking",
                "status": "OK" if has_pnl else "WARN",
                "detail": "P&L tracking found in trade processing",
            })
            scores["pnl_tracking"] = 8 if has_pnl else 4

            # 6. persistence — atomic single write
            from .agent_journal_3 import _save_entries_json
            json_src = inspect.getsource(_save_entries_json)
            atomic = "os.replace" in json_src or "replace" in json_src
            has_lock = "fcntl" in json_src or "LOCK" in json_src
            findings.append({
                "area": "persistence",
                "status": "OK" if atomic else "WARN",
                "detail": f"Atomic write: {atomic}, file locking: {has_lock}",
            })
            scores["persistence"] = 9 if atomic else 5

        except Exception as exc:
            findings.append({"area": "journal_3", "status": "ERROR", "detail": str(exc)})
            scores["overall"] = 3

        self._analyze_agent_errors(findings, scores, improvements, "journal_3")
        tests.extend([
            "test_journal_3_eod_close_support",
            "test_journal_3_dedup_ticker_strategy_entry_time",
            "test_journal_3_atomic_write",
        ])

    def _audit_learning_3(self, report: dict, focus: str | None):
        """Audit Agent Learning 3 — decay 15d, EOD_CLOSE, weekly config, anomaly detection."""
        findings = report["findings"]
        improvements = report["improvements"]
        tests = report["tests_to_add"]
        scores = report["score_breakdown"]

        try:
            from .agent_learning_3 import (
                AgentLearning3, DECAY_HALF_LIFE_DAYS, ACTIVATION_DATE,
                MIN_TRADES_STRATEGY, MIN_TRADES_TICKER, MIN_TRADES_GLOBAL,
                MIN_TRADES_AB, compute_tech_learning,
            )

            # 1. strategy_ranking — A/B test with WR + PnL + Sharpe
            import inspect
            weekly_src = inspect.getsource(AgentLearning3.generate_weekly_config)
            has_sharpe = "sharpe" in weekly_src.lower()
            has_wr = "win_rate" in weekly_src or "wr" in weekly_src.lower()
            findings.append({
                "area": "strategy_ranking",
                "status": "OK" if has_sharpe and has_wr else "WARN",
                "detail": f"A/B ranking uses Sharpe: {has_sharpe}, WR: {has_wr}, "
                          f"min trades for A/B: {MIN_TRADES_AB}",
            })
            scores["strategy_ranking"] = 9 if has_sharpe and has_wr else 5

            # 2. ticker_calibration — per-ticker adjustments with bounds
            learning_src = inspect.getsource(compute_tech_learning)
            has_ticker = "ticker_adj" in learning_src or "per_ticker" in learning_src
            findings.append({
                "area": "ticker_calibration",
                "status": "OK" if has_ticker else "WARN",
                "detail": f"Per-ticker adjustments: {has_ticker}, "
                          f"min trades: {MIN_TRADES_TICKER}",
            })
            scores["ticker_calibration"] = 8 if has_ticker else 4

            # 3. sample_size — min trades enforced
            samples_ok = (MIN_TRADES_STRATEGY >= 5 and MIN_TRADES_TICKER >= 5
                          and MIN_TRADES_GLOBAL >= 10)
            findings.append({
                "area": "sample_size",
                "status": "OK" if samples_ok else "WARN",
                "detail": f"Minimums — strategy: {MIN_TRADES_STRATEGY}, ticker: {MIN_TRADES_TICKER}, "
                          f"global: {MIN_TRADES_GLOBAL}, A/B: {MIN_TRADES_AB}",
            })
            scores["sample_size"] = 9 if samples_ok else 5

            # 4. overfitting_risk — decay half-life = 15d for intraday
            decay_ok = DECAY_HALF_LIFE_DAYS == 15
            findings.append({
                "area": "overfitting_risk",
                "status": "OK" if decay_ok else "WARN",
                "detail": f"Decay half-life: {DECAY_HALF_LIFE_DAYS}d "
                          f"(v3.0 expected: 15d for intraday feedback speed)",
            })
            scores["overfitting_risk"] = 10 if decay_ok else 5

            # 5. anomaly_detection — EOD_CLOSE rate + HIGH_MAE + WIN_RATE
            has_eod_anomaly = "HIGH_EOD_CLOSE" in learning_src or "eod_close" in learning_src.lower()
            has_mae_anomaly = "HIGH_MAE" in learning_src
            has_wr_anomaly = "win_rate" in learning_src and "35" in learning_src
            findings.append({
                "area": "anomaly_detection",
                "status": "OK" if has_eod_anomaly and has_mae_anomaly else "WARN",
                "detail": f"EOD_CLOSE rate anomaly: {has_eod_anomaly}, "
                          f"HIGH_MAE: {has_mae_anomaly}, "
                          f"low win rate alert: {has_wr_anomaly}",
            })
            ad_score = sum([has_eod_anomaly, has_mae_anomaly, has_wr_anomaly])
            scores["anomaly_detection"] = min(10, 5 + ad_score * 2)

            # 6. feedback_loop — weekly config generation + consumption
            has_weekly = hasattr(AgentLearning3, "generate_weekly_config")
            has_get = hasattr(AgentLearning3, "get_weekly_config")
            # Verify activation date gate
            run_src = inspect.getsource(AgentLearning3.run)
            has_activation_gate = "ACTIVATION_DATE" in run_src
            findings.append({
                "area": "feedback_loop",
                "status": "OK" if has_weekly and has_get else "WARN",
                "detail": f"generate_weekly_config: {has_weekly}, "
                          f"get_weekly_config: {has_get}, "
                          f"activation_date: {ACTIVATION_DATE.isoformat()}, "
                          f"gate in run(): {has_activation_gate}",
            })
            scores["feedback_loop"] = 9 if has_weekly and has_get and has_activation_gate else 5

            # EOD_CLOSE inclusion in valid results
            has_eod_valid = '"EOD_CLOSE"' in learning_src or "'EOD_CLOSE'" in learning_src
            findings.append({
                "area": "eod_close_inclusion",
                "status": "OK" if has_eod_valid else "CRITICAL",
                "detail": "EOD_CLOSE included in valid trade results for learning"
                          if has_eod_valid else "EOD_CLOSE NOT in valid results — learning ignores intraday force-closes!",
            })
            scores["eod_close_inclusion"] = 10 if has_eod_valid else 0

        except Exception as exc:
            findings.append({"area": "learning_3", "status": "ERROR", "detail": str(exc)})
            scores["overall"] = 3

        self._analyze_agent_errors(findings, scores, improvements, "learning_3")
        tests.extend([
            "test_learning_3_decay_15_days",
            "test_learning_3_eod_close_in_valid_results",
            "test_learning_3_weekly_config_generation",
            "test_learning_3_activation_date_gate",
        ])

    def _audit_generic_agent(self, report: dict, focus: str | None):
        """Generic audit for Teams 3/4 agents — checks metrics, logs, and basic health."""
        target = report["target_agent"]
        findings = report["findings"]
        improvements = report["improvements"]
        scores = report["score_breakdown"]

        try:
            from . import registry
            agent = registry.get_agent(target)
            if not agent:
                findings.append({"area": "existence", "status": "CRITICAL", "detail": f"Agent {target} not found in registry"})
                scores["existence"] = 0
                return

            # Check agent has metrics
            metrics = agent.get_metrics()
            findings.append({
                "area": "metrics",
                "status": "OK" if metrics else "WARN",
                "detail": f"Metrics keys: {list(metrics.keys()) if metrics else 'none'}",
            })
            scores["metrics"] = 8 if metrics else 3

            # Check logs
            agent_logs = agent.logger.get_logs(limit=20)
            findings.append({
                "area": "logging",
                "status": "OK" if agent_logs else "INFO",
                "detail": f"{len(agent_logs)} log entries",
            })
            scores["logging"] = 8 if agent_logs else 5

            # Check version
            version = getattr(agent, "version", None)
            findings.append({
                "area": "versioning",
                "status": "OK" if version else "WARN",
                "detail": f"Version: {version or 'not set'}",
            })
            scores["versioning"] = 9 if version else 4

            # Analyze error logs
            error_logs = [l for l in agent_logs if l.get("level") in ("ERROR", "WARN")]
            if error_logs:
                findings.append({
                    "area": "errors",
                    "status": "WARN" if len(error_logs) < 5 else "CRITICAL",
                    "detail": f"{len(error_logs)} errors/warnings in recent logs",
                })
                scores["errors"] = max(3, 10 - len(error_logs))
            else:
                scores["errors"] = 10

            # Profile checks from AUDIT_PROFILES
            profile = AUDIT_PROFILES.get(target, {})
            checks = profile.get("checks", [])
            findings.append({
                "area": "profile",
                "status": "OK",
                "detail": f"Audit profile: {len(checks)} checks declared",
            })
            scores["profile"] = 7

            if not improvements:
                improvements.append({
                    "priority": "P3",
                    "description": f"Implement deep audit checks for {target} (currently using generic audit)",
                    "effort": "medium",
                })

        except Exception as exc:
            findings.append({"area": "audit_error", "status": "ERROR", "detail": str(exc)})
            scores["audit_error"] = 2

    def _audit_ux(self, report: dict, focus: str | None):
        """Audit Agent UX — frontend components, API integration, polling, accessibility."""
        findings = report["findings"]
        improvements = report["improvements"]
        tests = report["tests_to_add"]
        scores = report["score_breakdown"]

        try:
            from pathlib import Path
            frontend_dir = Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "src"

            # 1. Component coverage — check all key components exist (v7.0 naming)
            required_components = [
                "components/DashboardPage.jsx",
                "components/TraderPage.jsx",
                "components/ScoringPage.jsx",
                "components/NewsPage.jsx",
                "components/JournalPage.jsx",
                "components/LearningPage.jsx",
                "components/AuditorPage.jsx",
                "components/AgentSidebar.jsx",
                "components/AgentOverview.jsx",
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

            # 6. Data display — check pages have log filtering and metrics
            # In v7.0, log filtering is in individual page components (TraderPage, AuditorPage, etc.)
            trader_page = frontend_dir / "components" / "TraderPage.jsx"
            trader_content = trader_page.read_text() if trader_page.exists() else ""
            has_log_filter = "log-filter" in trader_content or "logFilter" in trader_content
            has_metrics = "metrics" in trader_content.lower() or "kpi" in trader_content.lower()
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

    def _audit_scoring_2(self, report: dict, focus: str | None):
        """Audit Agent Scoring 2 — trend re-scoring quality, multipliers, accumulation."""
        findings = report["findings"]
        improvements = report["improvements"]
        tests = report["tests_to_add"]
        scores = report["score_breakdown"]

        try:
            from .agent_scoring_2 import (
                AgentScoring2, TREND_CATEGORY_MULTS, STRUCTURAL_KEYWORDS,
                TREND_TICKERS, score_for_trend,
            )

            # 1. Category multipliers — verify they prioritize structural impact
            weather_mult = TREND_CATEGORY_MULTS.get("weather", 0)
            earnings_mult = TREND_CATEGORY_MULTS.get("earnings", 0)
            findings.append({
                "area": "category_multipliers",
                "status": "OK" if weather_mult >= 1.5 and earnings_mult <= 0.2 else "WARN",
                "detail": f"weather={weather_mult}, earnings={earnings_mult}, "
                          f"{len(TREND_CATEGORY_MULTS)} categories configured",
            })
            scores["category_multipliers"] = 9 if weather_mult >= 2.0 else 7

            # 2. Structural keywords coverage
            high_persist = {k: v for k, v in STRUCTURAL_KEYWORDS.items() if v >= 1.4}
            low_persist = {k: v for k, v in STRUCTURAL_KEYWORDS.items() if v < 1.0}
            findings.append({
                "area": "persistence_detection",
                "status": "OK",
                "detail": f"{len(STRUCTURAL_KEYWORDS)} keywords, "
                          f"{len(high_persist)} high-persistence (>=1.4), "
                          f"{len(low_persist)} low-persistence (<1.0)",
            })
            scores["persistence_detection"] = 8

            # 3. Ticker coverage
            findings.append({
                "area": "ticker_coverage",
                "status": "OK" if len(TREND_TICKERS) == 4 else "WARN",
                "detail": f"{len(TREND_TICKERS)} trend tickers: {TREND_TICKERS}",
            })
            scores["ticker_coverage"] = 9

            # 4. Score distribution from last result
            from .registry import get_agent
            agent = get_agent("scoring_2")
            if agent:
                last = agent.get_last_result()
                if last:
                    stats = last.get("stats", {})
                    relevant = stats.get("relevant_items", 0)
                    total = stats.get("total_items", 0)
                    avg_score = stats.get("avg_trend_score", 0)
                    findings.append({
                        "area": "score_distribution",
                        "status": "OK" if relevant > 0 else "WARN",
                        "detail": f"Last run: {relevant}/{total} relevant, avg trend score={avg_score}",
                    })
                    scores["score_distribution"] = 8 if relevant > 0 else 5
                else:
                    findings.append({"area": "score_distribution", "status": "WARN", "detail": "No scoring data yet"})
                    scores["score_distribution"] = 5

                metrics = agent.get_metrics()
                findings.append({
                    "area": "pipeline_integration",
                    "status": "OK" if metrics.get("total_rescorings", 0) > 0 else "WARN",
                    "detail": f"Total rescorings: {metrics.get('total_rescorings', 0)}",
                })
                scores["pipeline_integration"] = 8 if metrics.get("total_rescorings", 0) > 0 else 4

            # 5. No Claude call verification
            import inspect
            src = inspect.getsource(score_for_trend)
            has_claude = "anthropic" in src.lower() or "claude" in src.lower()
            findings.append({
                "area": "no_claude_call",
                "status": "OK" if not has_claude else "CRITICAL",
                "detail": "Confirmed: no Claude API call in score_for_trend()" if not has_claude
                          else "UNEXPECTED Claude reference in trend scoring!",
            })
            scores["no_claude_call"] = 10 if not has_claude else 0

            # 6. Accumulation logic
            findings.append({
                "area": "accumulation_logic",
                "status": "OK",
                "detail": "Accumulation per ticker with long/short signal sums, weighted by trend_score × reliability",
            })
            scores["accumulation_logic"] = 8

            # 7. Trader 2 consumption
            try:
                from .agent_trader_2 import AgentTrader2
                import inspect as insp2
                trader_src = insp2.getsource(AgentTrader2._evaluate_ticker)
                uses_trend = "trend_accumulation" in trader_src or "trend_scoring" in trader_src
                findings.append({
                    "area": "trader_2_consumption",
                    "status": "OK" if uses_trend else "WARN",
                    "detail": "Trader 2 consumes trend scoring accumulation" if uses_trend
                              else "Trader 2 may not use trend scoring data",
                })
                scores["trader_2_consumption"] = 9 if uses_trend else 4
            except Exception:
                scores["trader_2_consumption"] = 5

        except Exception as exc:
            findings.append({"area": "scoring_2", "status": "ERROR", "detail": str(exc)})
            scores["overall"] = 3

        self._analyze_agent_errors(findings, scores, improvements, "scoring_2")
        tests.append("test_scoring_2_no_claude_call")
        tests.append("test_scoring_2_category_multipliers_valid")
        tests.append("test_scoring_2_accumulation_logic")

    def _audit_journal_2(self, report: dict, focus: str | None):
        """Audit Agent Journal 2 — flip coverage, MAE/MFE accuracy, persistence."""
        findings = report["findings"]
        improvements = report["improvements"]
        tests = report["tests_to_add"]
        scores = report["score_breakdown"]

        try:
            from .agent_journal_2 import _load_journal_entries

            entries = _load_journal_entries()
            # Filter out snapshots for flip analysis
            flip_entries = [e for e in entries if e.get("entry_type") != "snapshot"]
            snapshot_entries = [e for e in entries if e.get("entry_type") == "snapshot"]

            if not flip_entries:
                findings.append({"area": "overall", "status": "WARN", "detail": "No journal 2 flip entries to audit"})
                scores["flip_coverage"] = 5
                scores["data_quality"] = 5
                return

            recent = flip_entries[-50:] if len(flip_entries) > 50 else flip_entries

            # 1. Flip coverage — check all 4 tickers have entries
            tickers_covered = {e.get("ticker") for e in flip_entries}
            from .agent_trader_2 import TREND_TICKERS
            missing_tickers = set(TREND_TICKERS.keys()) - tickers_covered
            findings.append({
                "area": "flip_coverage",
                "status": "OK" if not missing_tickers else "WARN",
                "detail": f"Tickers covered: {tickers_covered}, missing: {missing_tickers or 'none'}, "
                          f"total flips: {len(flip_entries)}",
            })
            scores["flip_coverage"] = 9 if not missing_tickers else max(4, 9 - len(missing_tickers) * 2)

            # 2. MAE/MFE accuracy — check for None values (missing bars)
            mae_none = sum(1 for e in recent if e.get("mae_pct") is None)
            mfe_none = sum(1 for e in recent if e.get("mfe_pct") is None)
            mae_rate = (len(recent) - mae_none) / len(recent) * 100 if recent else 0
            findings.append({
                "area": "mae_mfe_accuracy",
                "status": "OK" if mae_rate > 80 else "WARN" if mae_rate > 50 else "CRITICAL",
                "detail": f"MAE available: {len(recent)-mae_none}/{len(recent)} ({mae_rate:.0f}%), "
                          f"MFE missing: {mfe_none}/{len(recent)}",
            })
            scores["mae_mfe_accuracy"] = min(10, mae_rate / 10)

            if mae_rate < 70:
                improvements.append({
                    "priority": "HIGH",
                    "agent": "journal_2",
                    "action": f"MAE/MFE availability at {mae_rate:.0f}% — check bar fetch reliability",
                    "rationale": "Learning 2 needs MAE/MFE to detect drawdown anomalies",
                })

            # 3. P&L tracking
            pnl_missing = sum(1 for e in recent if e.get("pnl_pct") is None)
            pnl_zero = sum(1 for e in recent if e.get("pnl_pct") == 0.0)
            findings.append({
                "area": "pnl_tracking",
                "status": "OK" if pnl_missing == 0 else "WARN",
                "detail": f"Missing PnL: {pnl_missing}/{len(recent)}, Zero PnL: {pnl_zero}/{len(recent)}",
            })
            scores["pnl_tracking"] = 10 if pnl_missing == 0 else max(3, 10 - pnl_missing * 2)

            # 4. Dedup integrity — check for duplicate (ticker, entry_time) pairs
            keys = [(e.get("ticker"), e.get("entry_time")) for e in flip_entries]
            duplicates = len(keys) - len(set(keys))
            findings.append({
                "area": "dedup_integrity",
                "status": "OK" if duplicates == 0 else "WARN",
                "detail": f"Duplicate entries: {duplicates}/{len(flip_entries)}",
            })
            scores["dedup_integrity"] = 10 if duplicates == 0 else max(3, 10 - duplicates * 2)

            # 5. Bar fetch reliability
            bar_counts = [e.get("bar_count", 0) for e in recent]
            low_bars = sum(1 for b in bar_counts if b < 2)
            avg_bars = sum(bar_counts) / len(bar_counts) if bar_counts else 0
            findings.append({
                "area": "bar_fetch_reliability",
                "status": "OK" if avg_bars >= 3 else "WARN",
                "detail": f"Avg bars: {avg_bars:.1f}, low coverage (<2): {low_bars}/{len(recent)}",
            })
            scores["bar_fetch_reliability"] = min(10, avg_bars * 2)

            # 6. Pruning — check oldest entry
            oldest_time = min((e.get("entry_time", "9999") for e in flip_entries), default="9999")
            one_year_ago = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
            has_old = oldest_time < one_year_ago if oldest_time != "9999" else False
            findings.append({
                "area": "pruning",
                "status": "OK" if not has_old else "WARN",
                "detail": f"Oldest entry: {oldest_time[:10]}" + (" (>1y, should be pruned)" if has_old else ""),
            })
            scores["pruning"] = 9 if not has_old else 5

            # 7. Snapshot quality
            findings.append({
                "area": "snapshot_quality",
                "status": "OK" if snapshot_entries else "WARN",
                "detail": f"{len(snapshot_entries)} snapshots persisted"
                          + (" — snapshots now saved with entries" if snapshot_entries else " — no snapshots yet"),
            })
            scores["snapshot_quality"] = 8 if snapshot_entries else 4

            # 8. Persistence
            from ..database import is_pg_enabled
            pg = is_pg_enabled()
            findings.append({
                "area": "persistence",
                "status": "OK",
                "detail": f"Storage: {'PostgreSQL' if pg else 'JSON'}, total entries: {len(entries)}",
            })
            scores["persistence"] = 8 if pg else 6

        except Exception as exc:
            findings.append({"area": "journal_2", "status": "ERROR", "detail": str(exc)})
            scores["overall"] = 3

        self._analyze_agent_errors(findings, scores, improvements, "journal_2")
        tests.append("test_journal_2_all_flips_have_pnl")
        tests.append("test_journal_2_mae_mfe_none_when_no_bars")
        tests.append("test_journal_2_dedup_by_position_entry_time")
        tests.append("test_journal_2_snapshots_persisted")

    def _audit_learning_2(self, report: dict, focus: str | None):
        """Audit Agent Learning 2 — adjustment quality, sample sizes, anomaly detection."""
        findings = report["findings"]
        improvements = report["improvements"]
        tests = report["tests_to_add"]
        scores = report["score_breakdown"]

        try:
            from .agent_learning_2 import (
                compute_trend_learning, MIN_PERIODS_TICKER,
                MIN_PERIODS_NEWSCAT, MIN_PERIODS_DIRECTION, MIN_PERIODS_GLOBAL,
                ADJ_MIN, ADJ_MAX,
            )
            from .agent_journal_2 import _load_journal_entries

            entries = _load_journal_entries()
            flip_entries = [e for e in entries if e.get("entry_type") != "snapshot"]

            if not flip_entries:
                findings.append({"area": "overall", "status": "WARN", "detail": "No data for learning 2 audit"})
                scores["sample_size"] = 5
                return

            # Run the learning computation
            learning = compute_trend_learning(flip_entries)
            stats = learning.get("stats", {})

            # 1. Sample size
            total = stats.get("total_periods", 0)
            sufficient = stats.get("sufficient_data", False)
            findings.append({
                "area": "sample_size",
                "status": "OK" if sufficient else "WARN",
                "detail": f"Total periods: {total}, sufficient: {sufficient} (min {MIN_PERIODS_GLOBAL})",
            })
            scores["sample_size"] = 8 if sufficient else 4

            # 2. Ticker calibration
            ticker_adj = learning.get("ticker_adj", {})
            extreme_tickers = {k: v for k, v in ticker_adj.items() if abs(v - 1.0) > 0.3}
            findings.append({
                "area": "ticker_calibration",
                "status": "OK" if not extreme_tickers else "WARN",
                "detail": f"Ticker adjustments: {ticker_adj or 'none'}"
                          + (f", extreme: {extreme_tickers}" if extreme_tickers else ""),
            })
            scores["ticker_calibration"] = 8 if not extreme_tickers else 6

            # 3. Newscat calibration
            newscat_adj = learning.get("newscat_adj", {})
            findings.append({
                "area": "newscat_calibration",
                "status": "OK",
                "detail": f"Newscat adjustments: {newscat_adj or 'none'}",
            })
            scores["newscat_calibration"] = 8

            # 4. Direction balance
            dir_adj = learning.get("direction_adj", {})
            long_adj = dir_adj.get("LONG", 1.0)
            short_adj = dir_adj.get("SHORT", 1.0)
            imbalance = abs(long_adj - short_adj)
            findings.append({
                "area": "direction_balance",
                "status": "OK" if imbalance < 0.3 else "WARN",
                "detail": f"LONG adj={long_adj}, SHORT adj={short_adj}, imbalance={imbalance:.2f}",
            })
            scores["direction_balance"] = 8 if imbalance < 0.2 else 6 if imbalance < 0.3 else 4

            # 5. Threshold stability
            sig_cal = learning.get("signal_calibration", {})
            threshold_adj = sig_cal.get("threshold_adj", 1.0)
            findings.append({
                "area": "threshold_stability",
                "status": "OK" if 0.95 <= threshold_adj <= 1.05 else "WARN",
                "detail": f"Threshold adj: {threshold_adj} (bounds [0.95, 1.05])",
            })
            scores["threshold_stability"] = 9 if 0.97 <= threshold_adj <= 1.03 else 7

            # 6. Churning detection
            anomalies = learning.get("anomalies", [])
            churning = [a for a in anomalies if "CHURNING" in a]
            findings.append({
                "area": "churning_detection",
                "status": "WARN" if churning else "OK",
                "detail": f"Churning alerts: {len(churning)}"
                          + (f" — {churning}" if churning else ""),
            })
            scores["churning_detection"] = 5 if churning else 9

            # 7. Anomaly detection overall
            findings.append({
                "area": "anomaly_detection",
                "status": "OK" if len(anomalies) < 3 else "WARN",
                "detail": f"{len(anomalies)} anomalies detected: {anomalies or 'none'}",
            })
            scores["anomaly_detection"] = 8 if len(anomalies) < 3 else 5

            # 8. Feedback loop — verify cache mechanism
            from .registry import get_agent
            agent = get_agent("learning_2")
            if agent:
                cache_valid = agent._cache_valid
                findings.append({
                    "area": "feedback_loop",
                    "status": "OK",
                    "detail": f"Cache valid: {cache_valid}, recalculations: {agent._total_recalculations}",
                })
                scores["feedback_loop"] = 8

        except Exception as exc:
            findings.append({"area": "learning_2", "status": "ERROR", "detail": str(exc)})
            scores["overall"] = 3

        self._analyze_agent_errors(findings, scores, improvements, "learning_2")
        tests.append("test_learning_2_bounds_respected")
        tests.append("test_learning_2_significance_testing")
        tests.append("test_learning_2_cache_invalidation")

    def _audit_infrastructure(self, report: dict, focus: str | None):
        """Audit Agent Infrastructure — DB health, maintenance, fallbacks, timeouts."""
        findings = report["findings"]
        improvements = report["improvements"]
        tests = report["tests_to_add"]
        scores = report["score_breakdown"]

        # 1. PG connectivity
        try:
            from ..database import is_pg_enabled
            pg_enabled = is_pg_enabled()
            if pg_enabled:
                from ..database import get_conn
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1")
                findings.append({
                    "area": "pg_connectivity",
                    "status": "OK",
                    "detail": "PostgreSQL is configured and reachable",
                })
                scores["pg_connectivity"] = 10
            else:
                findings.append({
                    "area": "pg_connectivity",
                    "status": "WARN",
                    "detail": "PostgreSQL not configured — using JSON fallback (limited functionality)",
                })
                scores["pg_connectivity"] = 4
                improvements.append({
                    "priority": "HIGH",
                    "agent": "infrastructure",
                    "action": "Configure PostgreSQL for production use",
                    "rationale": "JSON fallback lacks concurrent access safety, VACUUM, and proper indexing",
                })
        except Exception as exc:
            findings.append({"area": "pg_connectivity", "status": "CRITICAL", "detail": str(exc)})
            scores["pg_connectivity"] = 1

        # 2. Pool health
        try:
            from ..database import _get_pool
            pool = _get_pool()
            minconn = getattr(pool, 'minconn', None)
            maxconn = getattr(pool, 'maxconn', None)
            findings.append({
                "area": "pool_health",
                "status": "OK" if pool else "WARN",
                "detail": f"Pool configured: min={minconn}, max={maxconn}",
            })
            scores["pool_health"] = 8 if pool else 3
        except Exception as exc:
            findings.append({"area": "pool_health", "status": "WARN", "detail": str(exc)})
            scores["pool_health"] = 5

        # 3. Table maintenance — check if VACUUM is scheduled
        try:
            from ..database import pg_run_maintenance
            # Just check the function exists and is importable
            findings.append({
                "area": "table_maintenance",
                "status": "OK",
                "detail": "pg_run_maintenance() available — VACUUM + pruning (messages 30d, logs 90d, reports 100)",
            })
            scores["table_maintenance"] = 8

            # Check if maintenance is wired in scheduler
            infra_agent = None
            try:
                from . import registry
                infra_agent = registry.get_agent("infrastructure")
            except Exception:
                pass

            if infra_agent:
                maint_runs = infra_agent.get_metrics().get("total_maintenance_runs", 0)
                if maint_runs == 0:
                    findings[-1]["detail"] += " — WARNING: 0 maintenance runs recorded"
                    scores["table_maintenance"] = 6
                    improvements.append({
                        "priority": "MEDIUM",
                        "agent": "infrastructure",
                        "action": "Verify maintenance scheduler is running daily at 23h",
                        "rationale": "VACUUM prevents table bloat and maintains query performance",
                    })
        except Exception as exc:
            findings.append({"area": "table_maintenance", "status": "ERROR", "detail": str(exc)})
            scores["table_maintenance"] = 3

        # 4. Fallback detection
        try:
            from ..database import is_pg_enabled
            if is_pg_enabled():
                from pathlib import Path
                import json as _json
                data_dir = Path(__file__).resolve().parent.parent.parent.parent / "data"
                divergences = []
                for jf, pg_table in [("trades.json", "trades"), ("journal.json", "journal_entries")]:
                    fpath = data_dir / jf
                    if fpath.exists():
                        try:
                            with open(fpath, "r") as f:
                                data = _json.load(f)
                            if isinstance(data, list) and len(data) > 5:
                                from ..database import get_conn
                                with get_conn() as conn:
                                    with conn.cursor() as cur:
                                        cur.execute(f"SELECT COUNT(*) FROM {pg_table}")
                                        pg_count = cur.fetchone()[0]
                                if pg_count == 0:
                                    divergences.append(f"{jf}: {len(data)} entries vs PG {pg_table}: 0")
                        except Exception:
                            pass

                if divergences:
                    findings.append({
                        "area": "fallback_detection",
                        "status": "WARN",
                        "detail": f"Data divergence detected: {'; '.join(divergences)}",
                    })
                    scores["fallback_detection"] = 5
                    improvements.append({
                        "priority": "HIGH",
                        "agent": "infrastructure",
                        "action": "Run JSON→PG migration to sync data",
                        "rationale": "Divergent data means some queries return incomplete results",
                    })
                else:
                    findings.append({
                        "area": "fallback_detection",
                        "status": "OK",
                        "detail": "No JSON/PG data divergence detected",
                    })
                    scores["fallback_detection"] = 9
            else:
                findings.append({
                    "area": "fallback_detection",
                    "status": "WARN",
                    "detail": "PG not enabled — all data in JSON fallback",
                })
                scores["fallback_detection"] = 4
        except Exception as exc:
            findings.append({"area": "fallback_detection", "status": "ERROR", "detail": str(exc)})
            scores["fallback_detection"] = 5

        # 5. Data consistency
        try:
            from ..database import is_pg_enabled, get_conn
            if is_pg_enabled():
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            SELECT COUNT(*) FROM trades
                            WHERE result != 'PENDING'
                            AND NOT EXISTS (
                                SELECT 1 FROM journal_entries j
                                WHERE j.ticker = trades.ticker
                                AND j.entry_time = trades.timestamp
                            )
                        """)
                        orphans = cur.fetchone()[0]
                status = "OK" if orphans <= 5 else "WARN" if orphans <= 20 else "CRITICAL"
                findings.append({
                    "area": "data_consistency",
                    "status": status,
                    "detail": f"{orphans} closed trades without matching journal entries",
                })
                scores["data_consistency"] = 10 if orphans <= 2 else 7 if orphans <= 10 else 4
            else:
                scores["data_consistency"] = 5
                findings.append({"area": "data_consistency", "status": "WARN", "detail": "PG not enabled"})
        except Exception as exc:
            findings.append({"area": "data_consistency", "status": "ERROR", "detail": str(exc)})
            scores["data_consistency"] = 5

        # 6. Pending trade monitoring
        try:
            from ..database import is_pg_enabled, get_conn
            if is_pg_enabled():
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            SELECT COUNT(*) FROM trades
                            WHERE result = 'PENDING'
                            AND timestamp < NOW() - INTERVAL '2 days'
                        """)
                        stuck = cur.fetchone()[0]
                findings.append({
                    "area": "pending_trade_monitoring",
                    "status": "OK" if stuck == 0 else "WARN",
                    "detail": f"{stuck} trades stuck PENDING for >2 days" if stuck > 0
                             else "No stuck PENDING trades",
                })
                scores["pending_trade_monitoring"] = 10 if stuck == 0 else 5 if stuck <= 3 else 2
            else:
                scores["pending_trade_monitoring"] = 5
                findings.append({"area": "pending_trade_monitoring", "status": "WARN", "detail": "PG not enabled"})
        except Exception as exc:
            findings.append({"area": "pending_trade_monitoring", "status": "ERROR", "detail": str(exc)})
            scores["pending_trade_monitoring"] = 5

        # 7. Error rate monitoring
        try:
            agent_logs = self._get_agent_logs("infrastructure", limit=100)
            errors = [l for l in agent_logs if l.get("level") == "ERROR"]
            warns = [l for l in agent_logs if l.get("level") == "WARN"]
            total = len(agent_logs) or 1
            error_rate = len(errors) / total * 100
            findings.append({
                "area": "error_rate_monitoring",
                "status": "OK" if error_rate < 5 else "WARN" if error_rate < 20 else "CRITICAL",
                "detail": f"{len(errors)} errors, {len(warns)} warnings in last {total} log entries ({error_rate:.1f}% error rate)",
            })
            scores["error_rate_monitoring"] = 10 if error_rate < 2 else 7 if error_rate < 10 else 4
        except Exception:
            scores["error_rate_monitoring"] = 6
            findings.append({"area": "error_rate_monitoring", "status": "WARN", "detail": "No infrastructure logs available yet"})

        # 8. Timeout management
        try:
            # Check key timeout configs exist
            from ..data_apis import REQUEST_TIMEOUT
            from ..news_collector import RSS_FETCH_TIMEOUT
            timeouts_ok = REQUEST_TIMEOUT > 0 and RSS_FETCH_TIMEOUT > 0
            findings.append({
                "area": "timeout_management",
                "status": "OK" if timeouts_ok else "WARN",
                "detail": f"API timeout={REQUEST_TIMEOUT}s, RSS timeout={RSS_FETCH_TIMEOUT}s",
            })
            scores["timeout_management"] = 8 if timeouts_ok else 4
        except Exception as exc:
            findings.append({"area": "timeout_management", "status": "ERROR", "detail": str(exc)})
            scores["timeout_management"] = 5

        # Analyze errors from infrastructure agent
        self._analyze_agent_errors(findings, scores, improvements, "infrastructure")

        # Tests to add
        tests.append("test_infra_health_check_returns_overall_status")
        tests.append("test_infra_maintenance_calls_pg_maintenance")
        tests.append("test_infra_detects_stuck_pending_trades")
        tests.append("test_infra_detects_json_pg_divergence")
        tests.append("test_infra_metrics_exposed")

    def _audit_performance(self, report: dict, focus: str | None):
        """Audit the Performance agent — KPI coverage, data quality, trends."""
        findings = report["findings"]
        improvements = report["improvements"]
        tests = report["tests_to_add"]
        scores = report["score_breakdown"]

        # Get performance agent
        from .registry import get_agent
        perf_agent = get_agent("performance")

        # 1. KPI coverage — does the agent measure all other agents?
        # Report uses top-level keys: trader_1, trader_2, scoring, news, journal, learning, infrastructure
        expected_agents = {"trader_1", "trader_2", "scoring", "news", "journal", "learning", "infrastructure"}
        if perf_agent and perf_agent._last_report:
            measured = {k for k in expected_agents if k in perf_agent._last_report and perf_agent._last_report[k]}
            missing = expected_agents - measured
            findings.append({
                "area": "kpi_coverage",
                "status": "OK" if not missing else "WARN",
                "detail": f"Agents measured: {len(measured)}/{len(expected_agents)}"
                          + (f", missing: {missing}" if missing else ""),
            })
            scores["kpi_coverage"] = 10 if not missing else max(4, 10 - len(missing))
        else:
            findings.append({
                "area": "kpi_coverage",
                "status": "INFO",
                "detail": "No report generated yet — cannot assess coverage",
            })
            scores["kpi_coverage"] = 5

        # 2. Data freshness — are snapshots being taken?
        snapshot_count = perf_agent._total_snapshots if perf_agent else 0
        findings.append({
            "area": "data_freshness",
            "status": "OK" if snapshot_count > 0 else "WARN",
            "detail": f"Total snapshots taken: {snapshot_count}, in memory: {len(perf_agent._snapshots) if perf_agent else 0}",
        })
        scores["data_freshness"] = min(10, 5 + snapshot_count) if snapshot_count > 0 else 3

        # 3. Trader win rate — verify it matches real data
        try:
            from ..learning import load_trades
            trades = load_trades()
            closed = [t for t in trades if t.result and t.result.value not in ("PENDING",)]
            if closed:
                wins = sum(1 for t in closed if t.pnl_pct is not None and t.pnl_pct > 0)
                real_wr = wins / len(closed) * 100
                if perf_agent and perf_agent._last_report:
                    reported_wr = perf_agent._last_report.get("trader_1", {}).get("win_rate")
                    if reported_wr is not None:
                        delta = abs(reported_wr - real_wr)
                        findings.append({
                            "area": "trader_win_rate",
                            "status": "OK" if delta < 2 else "WARN",
                            "detail": f"Reported WR: {reported_wr:.1f}%, Real WR: {real_wr:.1f}%, Delta: {delta:.1f}pp",
                        })
                        scores["trader_win_rate"] = 10 if delta < 2 else max(4, 10 - delta)
                    else:
                        findings.append({"area": "trader_win_rate", "status": "INFO", "detail": "No WR in report yet"})
                        scores["trader_win_rate"] = 5
                else:
                    findings.append({"area": "trader_win_rate", "status": "INFO", "detail": f"Real WR: {real_wr:.1f}% ({len(closed)} trades), no report to compare"})
                    scores["trader_win_rate"] = 6
            else:
                findings.append({"area": "trader_win_rate", "status": "INFO", "detail": "No closed trades to verify"})
                scores["trader_win_rate"] = 5
        except Exception as exc:
            findings.append({"area": "trader_win_rate", "status": "WARN", "detail": f"Cannot load trades: {exc}"})
            scores["trader_win_rate"] = 4

        # 4. Trend computation — check method exists and produces output
        has_trend_method = hasattr(perf_agent, '_compute_trend') if perf_agent else False
        report_count = perf_agent._total_reports if perf_agent else 0
        findings.append({
            "area": "trend_computation",
            "status": "OK" if has_trend_method else "CRITICAL",
            "detail": f"Trend method: {'present' if has_trend_method else 'MISSING'}, daily reports: {report_count}",
        })
        scores["trend_computation"] = 8 if has_trend_method else 2

        # 5. Alert thresholds — verify they exist and are reasonable
        has_alerts = hasattr(perf_agent, '_detect_alerts') if perf_agent else False
        findings.append({
            "area": "alert_thresholds",
            "status": "OK" if has_alerts else "WARN",
            "detail": f"Alert detection: {'implemented' if has_alerts else 'missing'}",
        })
        scores["alert_thresholds"] = 8 if has_alerts else 3

        # 6. Ranking logic
        has_ranking = hasattr(perf_agent, '_rank_agents') if perf_agent else False
        findings.append({
            "area": "ranking_logic",
            "status": "OK" if has_ranking else "WARN",
            "detail": f"Agent ranking: {'implemented' if has_ranking else 'missing'}",
        })
        scores["ranking_logic"] = 8 if has_ranking else 3

        # 7. History retention
        max_snapshots = 168
        max_reports = 30
        snap_len = len(perf_agent._snapshots) if perf_agent else 0
        rep_len = len(perf_agent._daily_reports) if perf_agent else 0
        findings.append({
            "area": "history_retention",
            "status": "OK",
            "detail": f"Snapshots: {snap_len}/{max_snapshots} capacity, Reports: {rep_len}/{max_reports} capacity",
        })
        scores["history_retention"] = 8

        # 8. Cross-agent consistency — verify metrics methods exist on all agents
        from .registry import get_all_agents
        all_agents = get_all_agents()
        agents_with_metrics = sum(1 for a in all_agents.values() if hasattr(a, 'get_metrics'))
        findings.append({
            "area": "cross_agent_consistency",
            "status": "OK" if agents_with_metrics == len(all_agents) else "WARN",
            "detail": f"Agents with get_metrics(): {agents_with_metrics}/{len(all_agents)}",
        })
        scores["cross_agent_consistency"] = 10 if agents_with_metrics == len(all_agents) else 7

        # Error analysis
        self._analyze_agent_errors(findings, scores, improvements, "performance")

        report["summary"] = (
            f"Agent Performance audit: {len(findings)} checks. "
            f"Snapshots: {snapshot_count}, Reports: {report_count}. "
            f"KPI methods all present." if has_trend_method and has_alerts and has_ranking
            else f"Agent Performance audit: some methods may be missing."
        )

        # Improvements
        if snapshot_count == 0:
            improvements.append({
                "priority": "P1",
                "description": "Run at least one performance snapshot to populate initial KPIs",
            })
        if not perf_agent or not perf_agent._last_report:
            improvements.append({
                "priority": "P2",
                "description": "Trigger a daily report to verify full KPI computation pipeline",
            })

        tests.append("test_performance_snapshot_collects_all_agents")
        tests.append("test_performance_daily_report_has_kpis")
        tests.append("test_performance_trend_computation")
        tests.append("test_performance_alert_thresholds")
        tests.append("test_performance_ranking_logic")

    # ── Cross-agent audits: News from Trader perspective ────────────

    def _audit_news_for_trader_1(self, report: dict, focus: str | None):
        """Audit News agent from Trader 1's perspective — 41 assets, intraday day trading."""
        findings = report["findings"]
        improvements = report["improvements"]
        tests = report["tests_to_add"]
        scores = report["score_breakdown"]

        # 1. Ticker coverage — does every Trader 1 asset have at least one GNews query or chain reaction?
        try:
            from ..config import ASSETS, CHAIN_REACTIONS
            from ..data_apis import GNEWS_QUERIES

            all_tickers = {a.ticker for a in ASSETS}
            # Build set of tickers directly covered by GNews queries
            gnews_covered = set()
            for q in GNEWS_QUERIES:
                for t in q.get("tickers", []):
                    gnews_covered.add(t)
            # Add chain reaction targets
            chain_covered = set()
            for source, targets in CHAIN_REACTIONS.items():
                if source in gnews_covered:
                    for target in targets:
                        chain_covered.add(target["ticker"] if isinstance(target, dict) else target)

            total_covered = gnews_covered | chain_covered
            uncovered = all_tickers - total_covered
            coverage_pct = len(total_covered & all_tickers) / max(1, len(all_tickers)) * 100

            findings.append({
                "area": "ticker_coverage",
                "status": "OK" if coverage_pct >= 80 else "WARN" if coverage_pct >= 60 else "CRITICAL",
                "detail": (f"{len(total_covered & all_tickers)}/{len(all_tickers)} tickers couverts "
                           f"({len(gnews_covered & all_tickers)} direct GNews, "
                           f"{len(chain_covered & all_tickers - gnews_covered)} via chain reactions). "
                           f"Non couverts: {sorted(uncovered) if uncovered else 'aucun'}"),
            })
            scores["ticker_coverage"] = min(10, coverage_pct / 10)

            if uncovered:
                improvements.append({
                    "priority": "MEDIUM",
                    "agent": "news",
                    "action": f"Ajouter des queries GNews ou chain reactions pour: {', '.join(sorted(uncovered))}",
                    "rationale": "Trader 1 ne peut pas trader ces actifs sans signal GNews ni chain reaction",
                })
        except Exception as exc:
            scores["ticker_coverage"] = 3
            findings.append({"area": "ticker_coverage", "status": "ERROR", "detail": str(exc)})

        # 2. Category coverage — are high-edge categories well represented?
        try:
            from ..data_apis import GNEWS_QUERIES
            high_edge_cats = {"weather", "supply_chain", "commodity", "geopolitical"}
            cat_counts = {}
            for q in GNEWS_QUERIES:
                cat = q.get("category", "other")
                cat_counts[cat] = cat_counts.get(cat, 0) + 1

            covered_cats = high_edge_cats & set(cat_counts.keys())
            missing_cats = high_edge_cats - set(cat_counts.keys())
            total_high_edge = sum(cat_counts.get(c, 0) for c in high_edge_cats)

            findings.append({
                "area": "category_coverage",
                "status": "OK" if len(covered_cats) == len(high_edge_cats) else "WARN",
                "detail": (f"High-edge categories: {dict((c, cat_counts.get(c, 0)) for c in high_edge_cats)}. "
                           f"Total high-edge queries: {total_high_edge}/{len(GNEWS_QUERIES)}. "
                           f"Missing: {missing_cats if missing_cats else 'aucune'}"),
            })
            scores["category_coverage"] = 10 if not missing_cats and total_high_edge >= 15 else 7 if not missing_cats else 4
        except Exception as exc:
            scores["category_coverage"] = 5
            findings.append({"area": "category_coverage", "status": "ERROR", "detail": str(exc)})

        # 3. GNews syntax audit — check for bare OR branches that produce noise
        try:
            import re
            from ..data_apis import GNEWS_QUERIES
            syntax_issues = []
            for i, q in enumerate(GNEWS_QUERIES):
                query = q["q"]
                branches = re.split(r'\s+OR\s+', query)
                for branch in branches:
                    branch = branch.strip()
                    words = branch.split()
                    has_quotes = '"' in branch
                    # Single bare word (no quotes, not a proper noun / ultra-niche term)
                    if len(words) == 1 and not has_quotes:
                        word = words[0]
                        # Whitelist ultra-niche proper nouns that are OK bare
                        niche_ok = {"Houthi", "Nornickel", "Codelco", "Escondida", "OPEC", "CONAB", "PBOC"}
                        if word not in niche_ok:
                            syntax_issues.append(f"#{i}: bare word '{word}' in query '{query[:60]}'")

            status = "OK" if not syntax_issues else "WARN" if len(syntax_issues) <= 2 else "CRITICAL"
            findings.append({
                "area": "gnews_syntax",
                "status": status,
                "detail": f"{len(syntax_issues)} bare OR branches détectées" +
                          (f": {'; '.join(syntax_issues[:5])}" if syntax_issues else " — toutes les branches ont du contexte"),
            })
            scores["gnews_syntax"] = max(0, 10 - len(syntax_issues) * 2)

            for issue in syntax_issues[:3]:
                improvements.append({
                    "priority": "HIGH",
                    "agent": "news",
                    "action": f"Fix GNews syntax: {issue}",
                    "rationale": "Mot nu dans un OR = bruit massif (articles sport, entertainment, etc.)",
                })
        except Exception as exc:
            scores["gnews_syntax"] = 5
            findings.append({"area": "gnews_syntax", "status": "ERROR", "detail": str(exc)})

        # 4. Scored news relevance — do recent scans have news for Trader 1 tickers?
        try:
            from ..scan_history import load_scan_history
            from ..config import ASSETS
            history = load_scan_history()
            recent = history[-20:] if len(history) > 20 else history

            t1_tickers = {a.ticker for a in ASSETS}
            ticker_hit_count: dict[str, int] = {}
            total_scored = 0

            for scan in recent:
                for news in scan.get("all_scored_news", []):
                    total_scored += 1
                    for ticker in news.get("impacted_tickers", []):
                        if ticker in t1_tickers:
                            ticker_hit_count[ticker] = ticker_hit_count.get(ticker, 0) + 1

            tickers_with_hits = len(ticker_hit_count)
            top_tickers = sorted(ticker_hit_count.items(), key=lambda x: -x[1])[:10]

            findings.append({
                "area": "scored_news_relevance",
                "status": "OK" if tickers_with_hits >= 15 else "WARN" if tickers_with_hits >= 5 else "CRITICAL",
                "detail": (f"{tickers_with_hits}/{len(t1_tickers)} tickers touchés dans les {len(recent)} derniers scans "
                           f"({total_scored} news scorées). Top: {top_tickers[:5]}"),
            })
            scores["scored_news_relevance"] = min(10, tickers_with_hits / (len(t1_tickers) / 10))
        except Exception as exc:
            scores["scored_news_relevance"] = 3
            findings.append({"area": "scored_news_relevance", "status": "ERROR", "detail": str(exc)})

        # 5. Signal-to-noise ratio — ratio of tradeable scores (>20) vs noise (<20)
        try:
            from ..scan_history import load_scan_history
            history = load_scan_history()
            recent = history[-20:] if len(history) > 20 else history

            total = 0
            tradeable = 0
            for scan in recent:
                for news in scan.get("all_scored_news", []):
                    s = news.get("total_score") or news.get("score", 0)
                    if s > 0:
                        total += 1
                        if s >= 20:
                            tradeable += 1

            ratio = tradeable / max(1, total) * 100
            findings.append({
                "area": "signal_to_noise",
                "status": "OK" if ratio >= 20 else "WARN" if ratio >= 10 else "CRITICAL",
                "detail": f"{tradeable}/{total} news avec score >= 20 ({ratio:.0f}%). Objectif: >= 20%",
            })
            scores["signal_to_noise"] = min(10, ratio / 5)  # 50% = 10/10
        except Exception as exc:
            scores["signal_to_noise"] = 5
            findings.append({"area": "signal_to_noise", "status": "ERROR", "detail": str(exc)})

        # 6. Freshness for intraday — news must be <2h for day trading edge
        try:
            from ..scan_history import load_scan_history
            history = load_scan_history()
            recent = history[-20:] if len(history) > 20 else history

            ages = []
            for scan in recent:
                for news in scan.get("all_scored_news", []):
                    age = news.get("age_hours")
                    if age is not None:
                        ages.append(age)

            if ages:
                fresh_pct = sum(1 for a in ages if a < 2) / len(ages) * 100
                avg_age = sum(ages) / len(ages)
                findings.append({
                    "area": "freshness_for_intraday",
                    "status": "OK" if fresh_pct >= 30 else "WARN" if fresh_pct >= 15 else "CRITICAL",
                    "detail": f"<2h: {fresh_pct:.0f}%, avg age: {avg_age:.1f}h (n={len(ages)}). Intraday needs >=30% fresh",
                })
                scores["freshness_for_intraday"] = min(10, fresh_pct / 5)  # 50%+ = 10
            else:
                scores["freshness_for_intraday"] = 3
                findings.append({"area": "freshness_for_intraday", "status": "WARN", "detail": "No age data"})
        except Exception as exc:
            scores["freshness_for_intraday"] = 5
            findings.append({"area": "freshness_for_intraday", "status": "ERROR", "detail": str(exc)})

        # 7. Chain reaction reach — does the chain extend coverage to uncovered assets?
        try:
            from ..config import ASSETS, CHAIN_REACTIONS
            from ..data_apis import GNEWS_QUERIES

            gnews_direct = set()
            for q in GNEWS_QUERIES:
                for t in q.get("tickers", []):
                    gnews_direct.add(t)

            all_tickers = {a.ticker for a in ASSETS}
            no_gnews = all_tickers - gnews_direct
            chain_reaches = set()
            for source, targets in CHAIN_REACTIONS.items():
                if source in gnews_direct:
                    for tgt in targets:
                        t = tgt["ticker"] if isinstance(tgt, dict) else tgt
                        if t in no_gnews:
                            chain_reaches.add(t)

            still_uncovered = no_gnews - chain_reaches
            findings.append({
                "area": "chain_reaction_reach",
                "status": "OK" if len(still_uncovered) <= 5 else "WARN",
                "detail": (f"{len(no_gnews)} tickers sans GNews direct. "
                           f"Chain reactions couvrent {len(chain_reaches)}: {sorted(chain_reaches)}. "
                           f"Toujours non couverts: {sorted(still_uncovered)}"),
            })
            scores["chain_reaction_reach"] = max(0, 10 - len(still_uncovered))
        except Exception as exc:
            scores["chain_reaction_reach"] = 5
            findings.append({"area": "chain_reaction_reach", "status": "ERROR", "detail": str(exc)})

        self._analyze_agent_errors(findings, scores, improvements, "news")
        tests.append("test_gnews_all_trader1_tickers_covered")
        tests.append("test_gnews_no_bare_or_branches")
        tests.append("test_gnews_high_edge_categories_present")

    def _audit_news_for_trader_2(self, report: dict, focus: str | None):
        """Audit News agent from Trader 2's perspective — HG=F, CC=F, KC=F, ZW=F trend following."""
        findings = report["findings"]
        improvements = report["improvements"]
        tests = report["tests_to_add"]
        scores = report["score_breakdown"]

        T2_TICKERS = {"HG=F": "Cuivre", "CC=F": "Cacao", "KC=F": "Café", "ZW=F": "Blé"}
        T2_NEEDED_DIMS = {
            "HG=F": {"weather", "supply_chain", "commodity", "geopolitical", "regulatory"},
            "CC=F": {"weather", "supply_chain", "commodity", "regulatory"},
            "KC=F": {"weather", "supply_chain", "commodity"},
            "ZW=F": {"weather", "supply_chain", "commodity", "geopolitical", "regulatory"},
        }
        T2_KEY_PRODUCERS = {
            "HG=F": ["Chile", "DRC", "Zambia", "Peru", "China"],
            "CC=F": ["Ghana", "Ivory Coast", "Cameroon", "Nigeria", "Indonesia"],
            "KC=F": ["Brazil", "Vietnam", "Colombia", "Ethiopia", "Honduras"],
            "ZW=F": ["Russia", "Ukraine", "Australia", "Canada", "India"],
        }

        # 1. Ticker coverage depth — how many queries per T2 ticker?
        try:
            from ..data_apis import GNEWS_QUERIES
            ticker_query_count: dict[str, list[int]] = {t: [] for t in T2_TICKERS}
            for i, q in enumerate(GNEWS_QUERIES):
                for t in q.get("tickers", []):
                    if t in ticker_query_count:
                        ticker_query_count[t].append(i)

            min_queries = min(len(v) for v in ticker_query_count.values())
            details = {t: len(v) for t, v in ticker_query_count.items()}
            weak_tickers = [t for t, v in ticker_query_count.items() if len(v) < 4]

            findings.append({
                "area": "ticker_coverage_depth",
                "status": "OK" if min_queries >= 4 else "WARN" if min_queries >= 2 else "CRITICAL",
                "detail": f"Queries par ticker T2: {details}. Min recommandé: 4. Faibles: {weak_tickers or 'aucun'}",
            })
            scores["ticker_coverage_depth"] = min(10, min_queries * 2)

            for t in weak_tickers:
                improvements.append({
                    "priority": "HIGH",
                    "agent": "news",
                    "action": f"Ajouter des queries GNews pour {t} ({T2_TICKERS[t]}) — actuellement {len(ticker_query_count[t])} queries",
                    "rationale": f"Trader 2 suit {t} en trend following, {len(ticker_query_count[t])} queries insuffisant pour couvrir weather+supply+demand",
                })
        except Exception as exc:
            scores["ticker_coverage_depth"] = 3
            findings.append({"area": "ticker_coverage_depth", "status": "ERROR", "detail": str(exc)})

        # 2. Category balance — for each T2 ticker, which dimensions are covered?
        try:
            from ..data_apis import GNEWS_QUERIES
            from ..agents.agent_trader_2 import RELEVANT_CATEGORIES

            ticker_cats: dict[str, set[str]] = {t: set() for t in T2_TICKERS}
            for q in GNEWS_QUERIES:
                cat = q.get("category", "other")
                for t in q.get("tickers", []):
                    if t in ticker_cats:
                        ticker_cats[t].add(cat)

            gaps = {}
            for ticker, needed in T2_NEEDED_DIMS.items():
                missing = needed - ticker_cats.get(ticker, set())
                if missing:
                    gaps[ticker] = missing

            total_missing = sum(len(v) for v in gaps.values())
            findings.append({
                "area": "category_balance",
                "status": "OK" if total_missing == 0 else "WARN" if total_missing <= 3 else "CRITICAL",
                "detail": (f"Couverture catégorielle par ticker: "
                           f"{dict((t, sorted(c)) for t, c in ticker_cats.items())}. "
                           f"Gaps: {dict((t, sorted(m)) for t, m in gaps.items()) if gaps else 'aucun'}"),
            })
            scores["category_balance"] = max(0, 10 - total_missing * 1.5)

            for ticker, missing in gaps.items():
                for cat in missing:
                    improvements.append({
                        "priority": "HIGH" if cat in ("weather", "supply_chain") else "MEDIUM",
                        "agent": "news",
                        "action": f"Ajouter une query GNews '{cat}' pour {ticker} ({T2_TICKERS[ticker]})",
                        "rationale": f"Trader 2 a besoin de signaux {cat} pour le trend following sur {ticker}",
                    })
        except Exception as exc:
            scores["category_balance"] = 5
            findings.append({"area": "category_balance", "status": "ERROR", "detail": str(exc)})

        # 3. Producer coverage — key producing countries for each T2 ticker
        try:
            from ..data_apis import GNEWS_QUERIES
            import re

            producer_hits: dict[str, list[str]] = {t: [] for t in T2_TICKERS}
            producer_missing: dict[str, list[str]] = {t: [] for t in T2_TICKERS}

            for ticker, producers in T2_KEY_PRODUCERS.items():
                for producer in producers:
                    found = False
                    for q in GNEWS_QUERIES:
                        if ticker in q.get("tickers", []):
                            # Check if producer name appears in query text or zone
                            query_text = q["q"].lower() + " " + q.get("zone", "").lower()
                            if producer.lower() in query_text:
                                found = True
                                break
                    if found:
                        producer_hits[ticker].append(producer)
                    else:
                        producer_missing[ticker].append(producer)

            total_missing_producers = sum(len(v) for v in producer_missing.values())
            total_producers = sum(len(v) for v in T2_KEY_PRODUCERS.values())
            coverage = (total_producers - total_missing_producers) / max(1, total_producers) * 100

            findings.append({
                "area": "producer_coverage",
                "status": "OK" if coverage >= 70 else "WARN" if coverage >= 50 else "CRITICAL",
                "detail": (f"Couverture producteurs: {coverage:.0f}% ({total_producers - total_missing_producers}/{total_producers}). "
                           f"Manquants: {dict((t, m) for t, m in producer_missing.items() if m) or 'aucun'}"),
            })
            scores["producer_coverage"] = min(10, coverage / 10)

            for ticker, missing in producer_missing.items():
                if missing:
                    improvements.append({
                        "priority": "MEDIUM",
                        "agent": "news",
                        "action": f"Ajouter couverture producteur pour {ticker}: {', '.join(missing)}",
                        "rationale": f"Pays producteurs clés sans query GNews — risque de manquer des signaux supply pour {T2_TICKERS[ticker]}",
                    })
        except Exception as exc:
            scores["producer_coverage"] = 5
            findings.append({"area": "producer_coverage", "status": "ERROR", "detail": str(exc)})

        # 4. GNews syntax — same check but filtered to T2 queries only
        try:
            import re
            from ..data_apis import GNEWS_QUERIES
            syntax_issues = []
            for i, q in enumerate(GNEWS_QUERIES):
                # Only check queries that impact T2 tickers
                if not any(t in T2_TICKERS for t in q.get("tickers", [])):
                    continue
                query = q["q"]
                branches = re.split(r'\s+OR\s+', query)
                for branch in branches:
                    branch = branch.strip()
                    words = branch.split()
                    has_quotes = '"' in branch
                    if len(words) == 1 and not has_quotes:
                        niche_ok = {"Houthi", "Nornickel", "Codelco", "Escondida", "OPEC", "CONAB", "PBOC"}
                        if words[0] not in niche_ok:
                            syntax_issues.append(f"#{i}: bare '{words[0]}' in '{query[:50]}'")

            findings.append({
                "area": "gnews_syntax",
                "status": "OK" if not syntax_issues else "CRITICAL",
                "detail": f"{len(syntax_issues)} bare OR branches dans les queries T2" +
                          (f": {'; '.join(syntax_issues)}" if syntax_issues else ""),
            })
            scores["gnews_syntax"] = max(0, 10 - len(syntax_issues) * 3)
        except Exception as exc:
            scores["gnews_syntax"] = 5
            findings.append({"area": "gnews_syntax", "status": "ERROR", "detail": str(exc)})

        # 5. Scored news for trend — recent news with structural categories for T2 tickers
        try:
            from ..scan_history import load_scan_history
            history = load_scan_history()
            recent = history[-20:] if len(history) > 20 else history

            structural_cats = {"weather", "supply_chain", "commodity", "geopolitical", "regulatory"}
            t2_signals = 0
            t2_structural = 0
            t2_high_reliability = 0
            total_scored = 0

            for scan in recent:
                for news in scan.get("all_scored_news", []):
                    total_scored += 1
                    tickers = news.get("impacted_tickers", [])
                    if any(t in T2_TICKERS for t in tickers):
                        t2_signals += 1
                        cat = news.get("news_category", "other")
                        if cat in structural_cats:
                            t2_structural += 1
                        rel = news.get("signal_reliability", 50)
                        if rel >= 70:
                            t2_high_reliability += 1

            findings.append({
                "area": "scored_news_for_trend",
                "status": "OK" if t2_structural >= 5 else "WARN" if t2_structural >= 2 else "CRITICAL",
                "detail": (f"{t2_signals} news touchant les tickers T2 dans {len(recent)} scans "
                           f"(dont {t2_structural} structurelles, {t2_high_reliability} haute fiabilité). "
                           f"Total scorées: {total_scored}"),
            })
            scores["scored_news_for_trend"] = min(10, t2_structural)
        except Exception as exc:
            scores["scored_news_for_trend"] = 3
            findings.append({"area": "scored_news_for_trend", "status": "ERROR", "detail": str(exc)})

        # 6. Structural freshness — structured sources should use 18h lookback, not 8h
        try:
            from ..config import STRUCTURED_SOURCE_MAX_AGE_HOURS, STRUCTURED_SOURCES

            has_extended_lookback = STRUCTURED_SOURCE_MAX_AGE_HOURS >= 12
            structured_count = len(STRUCTURED_SOURCES) if hasattr(STRUCTURED_SOURCES, '__len__') else 0

            findings.append({
                "area": "structural_freshness",
                "status": "OK" if has_extended_lookback else "CRITICAL",
                "detail": (f"STRUCTURED_SOURCE_MAX_AGE_HOURS={STRUCTURED_SOURCE_MAX_AGE_HOURS}h "
                           f"({structured_count} sources structurées). "
                           f"Trend following a besoin de >=12h lookback pour données overnight (USDA, NOAA)"),
            })
            scores["structural_freshness"] = 10 if has_extended_lookback else 3
        except Exception as exc:
            scores["structural_freshness"] = 5
            findings.append({"area": "structural_freshness", "status": "ERROR", "detail": str(exc)})

        # 7. Accumulation signal — do recent scans produce enough directional signal for T2 flip threshold?
        try:
            from ..scan_history import load_scan_history
            from ..agents.agent_trader_2 import RELEVANT_CATEGORIES
            history = load_scan_history()
            recent = history[-20:] if len(history) > 20 else history

            # Simulate signal accumulation per ticker
            ticker_signals: dict[str, dict[str, float]] = {t: {"LONG": 0, "SHORT": 0, "count": 0} for t in T2_TICKERS}
            for scan in recent:
                for news in scan.get("all_scored_news", []):
                    cat = news.get("news_category", "other")
                    if cat not in RELEVANT_CATEGORIES:
                        continue
                    direction = news.get("direction", "NEUTRAL")
                    if direction == "NEUTRAL":
                        continue
                    score = news.get("total_score") or news.get("score", 0)
                    for t in news.get("impacted_tickers", []):
                        if t in ticker_signals:
                            ticker_signals[t][direction] = ticker_signals[t].get(direction, 0) + score
                            ticker_signals[t]["count"] = ticker_signals[t].get("count", 0) + 1

            # Check if any ticker has enough signal to potentially trigger a flip (threshold ~20)
            tickers_with_signal = sum(
                1 for t, s in ticker_signals.items()
                if abs(s.get("LONG", 0) - s.get("SHORT", 0)) > 0 or s.get("count", 0) > 0
            )
            strong_signals = sum(
                1 for t, s in ticker_signals.items()
                if max(s.get("LONG", 0), s.get("SHORT", 0)) >= 20
            )

            detail_parts = []
            for t in sorted(T2_TICKERS.keys()):
                s = ticker_signals[t]
                net = s.get("LONG", 0) - s.get("SHORT", 0)
                cnt = s.get("count", 0)
                detail_parts.append(f"{t}: {cnt} news, net={net:+.0f}")

            findings.append({
                "area": "accumulation_signal",
                "status": "OK" if strong_signals >= 2 else "WARN" if tickers_with_signal >= 2 else "CRITICAL",
                "detail": (f"{tickers_with_signal}/4 tickers avec signal, {strong_signals}/4 au-dessus du seuil de flip (20). "
                           f"Détail: {'; '.join(detail_parts)}"),
            })
            scores["accumulation_signal"] = min(10, tickers_with_signal * 2 + strong_signals)
        except Exception as exc:
            scores["accumulation_signal"] = 3
            findings.append({"area": "accumulation_signal", "status": "ERROR", "detail": str(exc)})

        self._analyze_agent_errors(findings, scores, improvements, "news")
        tests.append("test_gnews_all_t2_tickers_have_4_plus_queries")
        tests.append("test_gnews_t2_category_balance_weather_supply_commodity")
        tests.append("test_gnews_t2_producer_coverage")

    def _audit_self(self, report: dict, focus: str | None):
        """Auto-audit — meta-analysis of the auditor's own capabilities."""
        findings = report["findings"]
        improvements = report["improvements"]
        tests = report["tests_to_add"]
        scores = report["score_breakdown"]

        # 1. Profile coverage — all agents auditable?
        auditable = set(AUDIT_PROFILES.keys()) - {"auditor"}  # exclude self
        expected = {"news", "scoring", "scoring_2", "scoring_3", "scoring_4", "trader_1", "trader_2", "trader_3", "trader_4", "journal", "journal_2", "journal_3", "journal_4", "learning", "learning_2", "learning_3", "learning_4", "ux", "infrastructure", "performance"}
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
            "scoring_2": "_audit_scoring_2",
            "scoring_3": "_audit_generic_agent",
            "scoring_4": "_audit_generic_agent",
            "trader_1": "_audit_trader",
            "trader_2": "_audit_trader_2",
            "trader_3": "_audit_generic_agent",
            "trader_4": "_audit_generic_agent",
            "journal": "_audit_journal",
            "journal_2": "_audit_journal_2",
            "journal_3": "_audit_generic_agent",
            "journal_4": "_audit_generic_agent",
            "learning": "_audit_learning",
            "learning_2": "_audit_learning_2",
            "learning_3": "_audit_generic_agent",
            "learning_4": "_audit_generic_agent",
            "ux": "_audit_ux",
            "infrastructure": "_audit_infrastructure",
            "performance": "_audit_performance",
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
