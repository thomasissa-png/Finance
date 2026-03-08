"""Agent Performance — Mesure et suivi des KPIs de tous les agents.

Responsabilités :
- Collecte les KPIs clés de chaque agent (win rate, P&L, latence, fiabilité)
- Calcule les tendances temporelles (amélioration/dégradation)
- Identifie les agents qui performent vs ceux qui sous-performent
- Produit un tableau de bord synthétique avec historique

KPIs par agent :
- **Trader 1** : win_rate, pnl_total, avg_pnl, expired_rate, best/worst ticker, R/R réalisé
- **Trader 2** : realized_pnl, unrealized_pnl, flip_accuracy, positions_coverage
- **Scoring**  : avg_score, zero_edge_filter_rate, cache_hit_rate, tokens_per_scan
- **News**     : items_per_scan, source_error_rate, dedup_rate, collection_speed
- **Journal 1**: closure_rate, price_fetch_success, mae_avg, bar_coverage
- **Journal 2**: flip_coverage, snapshot_quality, mae_mfe_enrichment
- **Learning 1**: adjustment_count, anomaly_rate, cache_freshness
- **Learning 2**: calibration_stability, churning_detection
- **Infra**    : pg_uptime, maintenance_regularity

Schedule :
- Snapshot toutes les heures (léger, métriques agents uniquement)
- Rapport quotidien à 22h30 (après journal, données complètes)
- Tendances hebdomadaires dimanche 21h30 (évolution des KPIs)
"""

import logging
import time
from datetime import datetime, timezone
from typing import Any

from .base import BaseAgent, AgentStatus

logger = logging.getLogger(__name__)


class AgentPerformance(BaseAgent):
    name = "performance"
    description = "Mesure & suivi des KPIs de tous les agents"

    def __init__(self):
        super().__init__()
        self._snapshots: list[dict] = []  # Historical KPI snapshots (max 168 = 1 week hourly)
        self._daily_reports: list[dict] = []  # Daily performance reports (max 30)
        self._last_report: dict | None = None
        self._total_snapshots: int = 0
        self._total_reports: int = 0
        self._last_duration_ms: int = 0

    def run(self, action: str = "snapshot", **kwargs) -> dict:
        """Run performance action.

        Actions:
        - snapshot: Quick KPI snapshot from agent metrics (hourly)
        - daily_report: Full performance report with trade data (22h30)
        - weekly_trends: KPI evolution over last 7 days (dimanche)
        """
        if action == "snapshot":
            return self.run_snapshot()
        elif action == "daily_report":
            return self.run_daily_report()
        elif action == "weekly_trends":
            return self.run_weekly_trends()
        else:
            self.log("Unknown action", {"action": action}, level="WARN")
            return {"error": f"Unknown action: {action}"}

    # ── Snapshot (léger, toutes les heures) ──────────────────────────

    def run_snapshot(self) -> dict:
        """Quick KPI snapshot from agent metrics — no DB queries."""
        self._set_status(AgentStatus.WORKING, "KPI snapshot")
        start = time.monotonic()

        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agents": {},
        }

        try:
            from . import registry
            all_agents = registry.get_all_agents()

            for name, agent in all_agents.items():
                if name == "performance":
                    continue  # Don't monitor ourselves
                try:
                    status = agent.status
                    metrics = agent.get_metrics()
                    snapshot["agents"][name] = {
                        "status": status.get("status"),
                        "action_count": status.get("action_count", 0),
                        "last_error": status.get("last_error"),
                        "metrics": metrics,
                    }
                except Exception as exc:
                    snapshot["agents"][name] = {"error": str(exc)}

            # Store snapshot (keep last 168 = 1 week hourly)
            self._snapshots.append(snapshot)
            if len(self._snapshots) > 168:
                self._snapshots = self._snapshots[-168:]

            self._total_snapshots += 1
            duration_ms = int((time.monotonic() - start) * 1000)
            self._last_duration_ms = duration_ms

            self.log("KPI snapshot", {
                "agents_captured": len(snapshot["agents"]),
            }, duration_ms=duration_ms)

            self._set_status(AgentStatus.IDLE, "Snapshot done")
            return snapshot

        except Exception as exc:
            self.log("Snapshot failed", {"error": str(exc)}, level="ERROR")
            self._set_status(AgentStatus.ERROR, str(exc))
            raise

    # ── Daily Report (complet, 22h30) ────────────────────────────────

    def run_daily_report(self) -> dict:
        """Full daily performance report — queries trades and journal data."""
        self._set_status(AgentStatus.WORKING, "Daily performance report")
        start = time.monotonic()

        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "trader_1": {},
            "trader_2": {},
            "scoring": {},
            "news": {},
            "journal": {},
            "learning": {},
            "infrastructure": {},
            "top_performers": [],
            "underperformers": [],
            "alerts": [],
        }

        try:
            # Trader 1 KPIs (main)
            report["trader_1"] = self.execute(
                "Computing Trader 1 KPIs",
                self._compute_trader_1_kpis,
            )

            # Trader 2 KPIs (trend)
            report["trader_2"] = self.execute(
                "Computing Trader 2 KPIs",
                self._compute_trader_2_kpis,
            )

            # Scoring KPIs
            report["scoring"] = self.execute(
                "Computing Scoring KPIs",
                self._compute_scoring_kpis,
            )

            # News KPIs
            report["news"] = self.execute(
                "Computing News KPIs",
                self._compute_news_kpis,
            )

            # Journal KPIs
            report["journal"] = self.execute(
                "Computing Journal KPIs",
                self._compute_journal_kpis,
            )

            # Learning KPIs
            report["learning"] = self.execute(
                "Computing Learning KPIs",
                self._compute_learning_kpis,
            )

            # Infrastructure KPIs
            report["infrastructure"] = self.execute(
                "Computing Infrastructure KPIs",
                self._compute_infra_kpis,
            )

            # Ranking & alerts
            self._rank_agents(report)
            self._detect_alerts(report)

            # Store report
            self._daily_reports.append(report)
            if len(self._daily_reports) > 30:
                self._daily_reports = self._daily_reports[-30:]
            self._last_report = report
            self._total_reports += 1

            duration_ms = int((time.monotonic() - start) * 1000)
            self._last_duration_ms = duration_ms

            self.log_decision("Daily performance report", {
                "top_performers": report["top_performers"],
                "underperformers": report["underperformers"],
                "alerts_count": len(report["alerts"]),
                "duration_ms": duration_ms,
            })

            self.publish("performance_report", {
                "date": report["date"],
                "alerts_count": len(report["alerts"]),
                "top_performers": report["top_performers"],
                "timestamp": report["timestamp"],
            })

            self._set_status(AgentStatus.IDLE, "Report done")
            return report

        except Exception as exc:
            self.log("Daily report failed", {"error": str(exc)}, level="ERROR")
            self._set_status(AgentStatus.ERROR, str(exc))
            raise

    # ── Weekly Trends (dimanche 21h30) ───────────────────────────────

    def run_weekly_trends(self) -> dict:
        """Compute KPI evolution over last 7 days from stored daily reports."""
        self._set_status(AgentStatus.WORKING, "Weekly trends")
        start = time.monotonic()

        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "period_days": 0,
            "trends": {},
        }

        try:
            reports = self._daily_reports[-7:]
            result["period_days"] = len(reports)

            if len(reports) < 2:
                result["message"] = "Pas assez de données pour calculer les tendances (min 2 jours)"
                self._set_status(AgentStatus.IDLE, "Not enough data")
                return result

            # Track key metrics across days
            trends: dict[str, dict] = {}

            # Trader 1 trends
            t1_wrs = [r["trader_1"].get("win_rate") for r in reports if r.get("trader_1", {}).get("win_rate") is not None]
            t1_pnls = [r["trader_1"].get("total_pnl") for r in reports if r.get("trader_1", {}).get("total_pnl") is not None]
            if t1_wrs:
                trends["trader_1_win_rate"] = self._compute_trend(t1_wrs)
            if t1_pnls:
                trends["trader_1_pnl"] = self._compute_trend(t1_pnls)

            # Trader 2 trends
            t2_pnls = [r["trader_2"].get("total_realized_pnl") for r in reports if r.get("trader_2", {}).get("total_realized_pnl") is not None]
            if t2_pnls:
                trends["trader_2_realized_pnl"] = self._compute_trend(t2_pnls)

            # Scoring trends
            score_avgs = [r["scoring"].get("avg_score") for r in reports if r.get("scoring", {}).get("avg_score") is not None]
            if score_avgs:
                trends["scoring_avg_score"] = self._compute_trend(score_avgs)

            # News trends
            news_counts = [r["news"].get("avg_items_per_scan") for r in reports if r.get("news", {}).get("avg_items_per_scan") is not None]
            if news_counts:
                trends["news_items_per_scan"] = self._compute_trend(news_counts)

            result["trends"] = trends

            duration_ms = int((time.monotonic() - start) * 1000)
            self._last_duration_ms = duration_ms

            self.log_decision("Weekly trends", {
                "period_days": len(reports),
                "trends_computed": len(trends),
                "duration_ms": duration_ms,
            })

            self.publish("performance_trends", {
                "period_days": len(reports),
                "trends_count": len(trends),
                "timestamp": result["timestamp"],
            })

            self._set_status(AgentStatus.IDLE, "Trends done")
            return result

        except Exception as exc:
            self.log("Weekly trends failed", {"error": str(exc)}, level="ERROR")
            self._set_status(AgentStatus.ERROR, str(exc))
            raise

    # ── Private: KPI computations ────────────────────────────────────

    def _compute_trader_1_kpis(self) -> dict:
        """Compute Trader 1 KPIs from trade data."""
        kpis: dict[str, Any] = {
            "win_rate": None,
            "total_pnl": None,
            "avg_pnl": None,
            "total_trades": 0,
            "expired_rate": None,
            "avg_rr_realized": None,
            "best_ticker": None,
            "worst_ticker": None,
            "direction_accuracy": None,
            "by_category": {},
        }

        try:
            from ..learning import _load_trades
            from ..models import TradeResult
            trades = _load_trades()
            closed = [t for t in trades
                      if t.result != TradeResult.PENDING
                      and t.pnl_pct is not None
                      and abs(t.pnl_pct) <= 50]  # Filter anomalies

            if not closed:
                return kpis

            kpis["total_trades"] = len(closed)

            # Win rate
            wins = [t for t in closed if t.result == TradeResult.TP_HIT]
            kpis["win_rate"] = round(len(wins) / len(closed) * 100, 1)

            # P&L
            pnls = [t.pnl_pct for t in closed]
            kpis["total_pnl"] = round(sum(pnls), 2)
            kpis["avg_pnl"] = round(sum(pnls) / len(pnls), 3)

            # Expired rate
            expired = [t for t in closed if t.result == TradeResult.EXPIRED]
            kpis["expired_rate"] = round(len(expired) / len(closed) * 100, 1)

            # R/R realized
            rrs = [t.pnl_pct / abs(t.stop_pct) for t in closed
                   if t.stop_pct and t.stop_pct != 0]
            if rrs:
                kpis["avg_rr_realized"] = round(sum(rrs) / len(rrs), 2)

            # Best/worst ticker
            by_ticker: dict[str, list[float]] = {}
            for t in closed:
                by_ticker.setdefault(t.ticker, []).append(t.pnl_pct)
            if by_ticker:
                ticker_pnl = {k: sum(v) for k, v in by_ticker.items() if len(v) >= 3}
                if ticker_pnl:
                    kpis["best_ticker"] = max(ticker_pnl, key=ticker_pnl.get)
                    kpis["worst_ticker"] = min(ticker_pnl, key=ticker_pnl.get)

            # Direction accuracy
            correct_dir = sum(1 for t in closed if t.pnl_pct > 0)
            kpis["direction_accuracy"] = round(correct_dir / len(closed) * 100, 1)

            # By category
            by_cat: dict[str, dict] = {}
            for t in closed:
                cat = t.news_category or "other"
                if cat not in by_cat:
                    by_cat[cat] = {"trades": 0, "wins": 0, "pnl": 0.0}
                by_cat[cat]["trades"] += 1
                if t.result == TradeResult.TP_HIT:
                    by_cat[cat]["wins"] += 1
                by_cat[cat]["pnl"] += t.pnl_pct
            for cat, stats in by_cat.items():
                stats["win_rate"] = round(stats["wins"] / stats["trades"] * 100, 1) if stats["trades"] else 0
                stats["pnl"] = round(stats["pnl"], 2)
            kpis["by_category"] = by_cat

        except Exception as exc:
            logger.debug("Trader 1 KPI computation error: %s", exc)

        return kpis

    def _compute_trader_2_kpis(self) -> dict:
        """Compute Trader 2 (trend) KPIs from positions."""
        kpis: dict[str, Any] = {
            "total_realized_pnl": None,
            "total_unrealized_pnl": None,
            "positions_coverage": 0,
            "flip_count": 0,
            "by_ticker": {},
        }

        try:
            from . import registry
            trader2 = registry.get_agent("trader_2")
            if trader2:
                metrics = trader2.get_metrics()
                kpis["total_realized_pnl"] = metrics.get("total_realized_pnl")
                kpis["total_unrealized_pnl"] = metrics.get("total_unrealized_pnl")
                kpis["positions_coverage"] = (metrics.get("positions_long", 0)
                                              + metrics.get("positions_short", 0))
                kpis["flip_count"] = metrics.get("position_changes_total", 0)

                # Per-ticker positions
                tickers = metrics.get("tickers_tracked", [])
                for ticker in tickers:
                    kpis["by_ticker"][ticker] = {
                        "tracked": True,
                    }
        except Exception as exc:
            logger.debug("Trader 2 KPI computation error: %s", exc)

        return kpis

    def _compute_scoring_kpis(self) -> dict:
        """Compute Scoring KPIs from agent metrics."""
        kpis: dict[str, Any] = {
            "total_scored": 0,
            "avg_score": None,
            "zero_edge_filtered": 0,
            "cache_hit_rate": None,
            "tokens_total": 0,
        }

        try:
            from . import registry
            scoring = registry.get_agent("scoring")
            if scoring:
                metrics = scoring.get_metrics()
                kpis["total_scored"] = metrics.get("total_scored", 0)
                kpis["zero_edge_filtered"] = metrics.get("zero_edge_filtered", 0)
                kpis["tokens_total"] = metrics.get("total_tokens_used", 0)

                total = metrics.get("total_scored", 0)
                cache = metrics.get("cache_hits", 0)
                if total + cache > 0:
                    kpis["cache_hit_rate"] = round(cache / (total + cache) * 100, 1)

            # Avg score from scan history
            try:
                from ..scan_history import load_scan_history
                history = load_scan_history()
                recent = history[-20:] if len(history) > 20 else history
                all_scores = []
                for scan in recent:
                    for sn in scan.get("all_scored_news", []):
                        score = sn.get("total_score")
                        if score is not None:
                            all_scores.append(score)
                if all_scores:
                    kpis["avg_score"] = round(sum(all_scores) / len(all_scores), 1)
            except Exception:
                pass

        except Exception as exc:
            logger.debug("Scoring KPI computation error: %s", exc)

        return kpis

    def _compute_news_kpis(self) -> dict:
        """Compute News collection KPIs."""
        kpis: dict[str, Any] = {
            "total_collected": 0,
            "avg_items_per_scan": None,
            "dedup_filtered": 0,
            "source_errors": 0,
            "avg_collection_ms": None,
        }

        try:
            from . import registry
            news = registry.get_agent("news")
            if news:
                metrics = news.get_metrics()
                kpis["total_collected"] = metrics.get("total_collected", 0)
                kpis["dedup_filtered"] = metrics.get("total_filtered_dedup", 0)
                kpis["source_errors"] = metrics.get("source_errors", 0)

            # Avg items from scan history
            try:
                from ..scan_history import load_scan_history
                history = load_scan_history()
                recent = history[-20:] if len(history) > 20 else history
                item_counts = [h.get("news_analyzed", 0) for h in recent if h.get("news_analyzed")]
                if item_counts:
                    kpis["avg_items_per_scan"] = round(sum(item_counts) / len(item_counts), 1)
            except Exception:
                pass

            # Avg collection speed from logs
            try:
                logs = self._get_agent_logs_safe("news", limit=50)
                durations = [l.get("duration_ms") for l in logs
                             if l.get("action", "").startswith("Collecting") and l.get("duration_ms")]
                if durations:
                    kpis["avg_collection_ms"] = round(sum(durations) / len(durations))
            except Exception:
                pass

        except Exception as exc:
            logger.debug("News KPI computation error: %s", exc)

        return kpis

    def _compute_journal_kpis(self) -> dict:
        """Compute Journal KPIs."""
        kpis: dict[str, Any] = {
            "total_entries": 0,
            "tp_count": 0,
            "sl_count": 0,
            "expired_count": 0,
            "avg_mae": None,
            "avg_mfe": None,
            "price_fetch_success_rate": None,
        }

        try:
            from . import registry
            journal = registry.get_agent("journal")
            if journal:
                metrics = journal.get_metrics()
                kpis["total_entries"] = metrics.get("total_entries", 0)
                kpis["tp_count"] = metrics.get("last_tp", 0)
                kpis["sl_count"] = metrics.get("last_sl", 0)
                kpis["expired_count"] = metrics.get("last_expired", 0)

            # MAE/MFE from journal entries
            try:
                from ..journal import _load_journal
                entries = _load_journal()
                recent = entries[-50:] if len(entries) > 50 else entries
                maes = [e.mae for e in recent if hasattr(e, 'mae') and e.mae is not None]
                mfes = [e.mfe for e in recent if hasattr(e, 'mfe') and e.mfe is not None]
                if maes:
                    kpis["avg_mae"] = round(sum(maes) / len(maes), 3)
                if mfes:
                    kpis["avg_mfe"] = round(sum(mfes) / len(mfes), 3)

                # Price fetch success rate
                with_price = sum(1 for e in recent if e.exit_price is not None and e.exit_price != e.entry_price)
                if recent:
                    kpis["price_fetch_success_rate"] = round(with_price / len(recent) * 100, 1)
            except Exception:
                pass

        except Exception as exc:
            logger.debug("Journal KPI computation error: %s", exc)

        return kpis

    def _compute_learning_kpis(self) -> dict:
        """Compute Learning KPIs."""
        kpis: dict[str, Any] = {
            "adjustment_count": 0,
            "anomaly_count": 0,
            "cache_valid": False,
            "total_recalculations": 0,
        }

        try:
            from . import registry
            learning = registry.get_agent("learning")
            if learning:
                metrics = learning.get_metrics()
                kpis["adjustment_count"] = metrics.get("last_adjustment_count", 0)
                kpis["cache_valid"] = metrics.get("cache_valid", False)
                kpis["total_recalculations"] = metrics.get("total_recalculations", 0)
                anomalies = metrics.get("anomalies", [])
                kpis["anomaly_count"] = len(anomalies) if isinstance(anomalies, list) else 0

            # Learning 2
            learning2 = registry.get_agent("learning_2")
            if learning2:
                m2 = learning2.get_metrics()
                kpis["learning_2_adjustments"] = m2.get("last_adjustment_count", 0)
                kpis["learning_2_anomalies"] = len(m2.get("anomalies", []))

        except Exception as exc:
            logger.debug("Learning KPI computation error: %s", exc)

        return kpis

    def _compute_infra_kpis(self) -> dict:
        """Compute Infrastructure KPIs."""
        kpis: dict[str, Any] = {
            "health_status": "unknown",
            "pg_failures": 0,
            "maintenance_runs": 0,
        }

        try:
            from . import registry
            infra = registry.get_agent("infrastructure")
            if infra:
                metrics = infra.get_metrics()
                kpis["health_status"] = metrics.get("health_status", "unknown")
                kpis["pg_failures"] = metrics.get("consecutive_pg_failures", 0)
                kpis["maintenance_runs"] = metrics.get("total_maintenance_runs", 0)
        except Exception as exc:
            logger.debug("Infra KPI computation error: %s", exc)

        return kpis

    # ── Private: Ranking & Alerts ────────────────────────────────────

    def _rank_agents(self, report: dict) -> None:
        """Identify top performers and underperformers."""
        scores: list[tuple[str, float, str]] = []  # (agent, score, reason)

        # Trader 1 scoring
        t1 = report.get("trader_1", {})
        if t1.get("win_rate") is not None:
            wr = t1["win_rate"]
            if wr >= 55:
                scores.append(("trader_1", wr, f"Win rate {wr}%"))
            elif wr < 40:
                scores.append(("trader_1", wr - 100, f"Win rate faible {wr}%"))

        # Trader 2 scoring
        t2 = report.get("trader_2", {})
        if t2.get("total_realized_pnl") is not None:
            pnl = t2["total_realized_pnl"]
            if pnl > 0:
                scores.append(("trader_2", pnl, f"P&L réalisé +{pnl:.1f}%"))
            elif pnl < -5:
                scores.append(("trader_2", pnl - 100, f"P&L réalisé {pnl:.1f}%"))

        # Scoring efficiency
        sc = report.get("scoring", {})
        if sc.get("cache_hit_rate") is not None:
            chr_ = sc["cache_hit_rate"]
            if chr_ >= 30:
                scores.append(("scoring", chr_, f"Cache hit rate {chr_}%"))

        # News reliability
        n = report.get("news", {})
        if n.get("source_errors", 0) == 0 and n.get("total_collected", 0) > 0:
            scores.append(("news", 80, "0 erreurs source"))
        elif n.get("source_errors", 0) > 3:
            scores.append(("news", -50, f"{n['source_errors']} erreurs source"))

        # Infrastructure
        infra = report.get("infrastructure", {})
        if infra.get("health_status") == "healthy":
            scores.append(("infrastructure", 70, "Système sain"))
        elif infra.get("health_status") == "critical":
            scores.append(("infrastructure", -100, "Infrastructure critique"))

        # Sort and pick top/bottom
        scores.sort(key=lambda x: x[1], reverse=True)
        report["top_performers"] = [
            {"agent": s[0], "reason": s[2]} for s in scores[:3] if s[1] > 0
        ]
        report["underperformers"] = [
            {"agent": s[0], "reason": s[2]} for s in scores if s[1] < 0
        ]

    def _detect_alerts(self, report: dict) -> None:
        """Detect performance alerts that need attention."""
        alerts = report["alerts"]

        # Trader 1 alerts
        t1 = report.get("trader_1", {})
        if t1.get("win_rate") is not None and t1["win_rate"] < 35:
            alerts.append({
                "severity": "CRITICAL",
                "agent": "trader_1",
                "message": f"Win rate critique: {t1['win_rate']}% — revoir scoring/calibration",
            })
        if t1.get("expired_rate") is not None and t1["expired_rate"] > 50:
            alerts.append({
                "severity": "WARN",
                "agent": "trader_1",
                "message": f"EXPIRED rate élevé: {t1['expired_rate']}% — targets trop ambitieux",
            })

        # Journal alerts
        j = report.get("journal", {})
        if j.get("price_fetch_success_rate") is not None and j["price_fetch_success_rate"] < 70:
            alerts.append({
                "severity": "WARN",
                "agent": "journal",
                "message": f"Prix fetch success: {j['price_fetch_success_rate']}% — problème API market data",
            })

        # Learning alerts
        l = report.get("learning", {})
        if l.get("anomaly_count", 0) > 3:
            alerts.append({
                "severity": "WARN",
                "agent": "learning",
                "message": f"{l['anomaly_count']} anomalies détectées — vérifier calibration",
            })

        # Infrastructure alerts
        infra = report.get("infrastructure", {})
        if infra.get("pg_failures", 0) > 0:
            alerts.append({
                "severity": "CRITICAL" if infra["pg_failures"] >= 3 else "WARN",
                "agent": "infrastructure",
                "message": f"{infra['pg_failures']} échecs PG consécutifs",
            })

    # ── Private: Trend computation ───────────────────────────────────

    def _compute_trend(self, values: list) -> dict:
        """Compute trend from a list of values over time."""
        if len(values) < 2:
            return {"direction": "stable", "values": values}

        first_half = values[:len(values) // 2]
        second_half = values[len(values) // 2:]

        avg_first = sum(first_half) / len(first_half)
        avg_second = sum(second_half) / len(second_half)

        delta = avg_second - avg_first
        pct_change = (delta / abs(avg_first) * 100) if avg_first != 0 else 0

        if pct_change > 5:
            direction = "improving"
        elif pct_change < -5:
            direction = "declining"
        else:
            direction = "stable"

        return {
            "direction": direction,
            "current": round(values[-1], 2) if values else None,
            "avg_first_half": round(avg_first, 2),
            "avg_second_half": round(avg_second, 2),
            "delta": round(delta, 2),
            "pct_change": round(pct_change, 1),
        }

    # ── Private: Helpers ─────────────────────────────────────────────

    def _get_agent_logs_safe(self, agent_name: str, limit: int = 50) -> list[dict]:
        """Get agent logs without crashing if unavailable."""
        try:
            from . import registry
            agent = registry.get_agent(agent_name)
            if agent:
                return agent.logger.get_logs(limit=limit)
        except Exception:
            pass
        return []

    # ── Metrics ──────────────────────────────────────────────────────

    def get_metrics(self) -> dict:
        return {
            "total_snapshots": self._total_snapshots,
            "total_reports": self._total_reports,
            "last_duration_ms": self._last_duration_ms,
            "snapshots_stored": len(self._snapshots),
            "daily_reports_stored": len(self._daily_reports),
            "last_report_date": self._last_report.get("date") if self._last_report else None,
        }

    def get_latest_report(self) -> dict | None:
        """Get the most recent daily performance report."""
        return self._last_report

    def get_snapshots(self, limit: int = 24) -> list[dict]:
        """Get recent KPI snapshots."""
        return self._snapshots[-limit:]
