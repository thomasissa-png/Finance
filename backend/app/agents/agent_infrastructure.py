"""Agent Infrastructure — Surveillance et maintien de l'infrastructure système.

Responsabilités :
- Vérifie la santé de PostgreSQL (connexion, taille tables, rows, performances)
- Exécute la maintenance périodique (VACUUM, pruning agent tables, log rotation)
- Surveille les fallbacks JSON (détection divergence PG/JSON)
- Contrôle les timeouts et performances des modules critiques
- Vérifie la cohérence des données entre les différentes tables
- Détecte les problèmes avant qu'ils n'impactent le trading

Schedule :
- Health check léger toutes les 15 min (connexion PG, pool, pending trades)
- Maintenance quotidienne à 23h (après journal 22h) : VACUUM, pruning, stats
- Rapport hebdomadaire dimanche 21h : tendances taille tables, erreurs récurrentes
"""

import logging
import time
from datetime import datetime, timezone

from .base import BaseAgent, AgentStatus

logger = logging.getLogger(__name__)


class AgentInfrastructure(BaseAgent):
    name = "infrastructure"
    description = "Surveillance & maintenance infrastructure système"

    def __init__(self):
        super().__init__()
        self._last_health_status: str = "unknown"
        self._last_maintenance_result: dict | None = None
        self._total_health_checks: int = 0
        self._total_maintenance_runs: int = 0
        self._last_pg_stats: dict | None = None
        self._consecutive_pg_failures: int = 0
        self._last_duration_ms: int = 0

    def run(self, action: str = "health_check", **kwargs) -> dict:
        """Run infrastructure action.

        Actions:
        - health_check: Quick health check (PG, pool, pending trades)
        - maintenance: Full maintenance (VACUUM, pruning, stats)
        - full_report: Comprehensive infrastructure report
        """
        if action == "health_check":
            return self.run_health_check()
        elif action == "maintenance":
            return self.run_maintenance()
        elif action == "full_report":
            return self.run_full_report()
        else:
            self.log("Unknown action", {"action": action}, level="WARN")
            return {"error": f"Unknown action: {action}"}

    # ── Health Check (léger, toutes les 15 min) ──────────────────────

    def run_health_check(self) -> dict:
        """Quick health check — PG connectivity, pool, pending trades, fallbacks."""
        self._set_status(AgentStatus.WORKING, "Health check")
        start = time.monotonic()

        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pg_status": "unknown",
            "pool_status": "unknown",
            "pending_trades": 0,
            "fallback_active": False,
            "issues": [],
        }

        try:
            # 1. PostgreSQL connectivity
            pg_ok = self._check_pg_connectivity(result)

            # 2. Pool health
            if pg_ok:
                self._check_pool_health(result)

            # 3. Pending trades check
            self._check_pending_trades(result)

            # 4. JSON fallback detection
            self._check_fallback_status(result)

            # 5. Table sizes (lightweight — only counts)
            if pg_ok:
                self._check_table_health(result)

            # Overall status
            if result["issues"]:
                critical = [i for i in result["issues"] if i.get("severity") == "CRITICAL"]
                if critical:
                    self._last_health_status = "critical"
                    result["overall"] = "CRITICAL"
                else:
                    self._last_health_status = "degraded"
                    result["overall"] = "DEGRADED"
            else:
                self._last_health_status = "healthy"
                result["overall"] = "OK"
                self._consecutive_pg_failures = 0

            self._total_health_checks += 1
            duration_ms = int((time.monotonic() - start) * 1000)
            self._last_duration_ms = duration_ms

            self.log("Health check complete", {
                "overall": result["overall"],
                "pg": result["pg_status"],
                "issues": len(result["issues"]),
            }, duration_ms=duration_ms)

            if result["issues"]:
                self.log("Infrastructure issues detected", {
                    "issues": result["issues"],
                }, level="WARN")

            self.publish("infra_health_check", {
                "overall": result["overall"],
                "pg_status": result["pg_status"],
                "issues_count": len(result["issues"]),
                "timestamp": result["timestamp"],
            })

            self._set_status(AgentStatus.IDLE, f"Health: {result['overall']}")
            return result

        except Exception as exc:
            self.log("Health check failed", {"error": str(exc)}, level="ERROR")
            self._set_status(AgentStatus.ERROR, str(exc))
            raise

    # ── Maintenance (quotidienne, 23h) ───────────────────────────────

    def run_maintenance(self) -> dict:
        """Full maintenance — VACUUM, pruning, stats collection."""
        self._set_status(AgentStatus.WORKING, "Running maintenance")
        start = time.monotonic()

        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "vacuum": {},
            "pruning": {},
            "stats": {},
            "issues": [],
        }

        try:
            # 1. Run PG maintenance (VACUUM ANALYZE + pruning)
            result["vacuum"] = self.execute(
                "VACUUM ANALYZE + pruning",
                self._run_pg_maintenance,
            )

            # 2. Collect table stats
            result["stats"] = self.execute(
                "Collecting table stats",
                self._collect_table_stats,
            )
            self._last_pg_stats = result["stats"]

            # 3. Check for bloated tables
            self._check_table_bloat(result)

            # 4. Verify data consistency
            self._check_data_consistency(result)

            self._total_maintenance_runs += 1
            self._last_maintenance_result = result
            duration_ms = int((time.monotonic() - start) * 1000)
            self._last_duration_ms = duration_ms

            self.log_decision("Maintenance complete", {
                "vacuum_tables": len(result["vacuum"]),
                "issues": len(result["issues"]),
                "duration_ms": duration_ms,
            })

            self.publish("infra_maintenance", {
                "issues_count": len(result["issues"]),
                "duration_ms": duration_ms,
                "timestamp": result["timestamp"],
            })

            self._set_status(AgentStatus.IDLE, "Maintenance done")
            return result

        except Exception as exc:
            self.log("Maintenance failed", {"error": str(exc)}, level="ERROR")
            self._set_status(AgentStatus.ERROR, str(exc))
            raise

    # ── Full Report (hebdomadaire) ───────────────────────────────────

    def run_full_report(self) -> dict:
        """Comprehensive infrastructure report — trends, sizes, recommendations."""
        self._set_status(AgentStatus.WORKING, "Building infrastructure report")
        start = time.monotonic()

        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pg_enabled": False,
            "tables": {},
            "pool": {},
            "agent_logs_summary": {},
            "recommendations": [],
        }

        try:
            from ..database import is_pg_enabled
            result["pg_enabled"] = is_pg_enabled()

            if result["pg_enabled"]:
                # Table stats with sizes
                result["tables"] = self.execute(
                    "Collecting detailed table stats",
                    self._collect_table_stats,
                )

                # Agent error analysis
                result["agent_logs_summary"] = self.execute(
                    "Analyzing agent error logs",
                    self._analyze_agent_errors_summary,
                )

                # Pool stats
                result["pool"] = self._get_pool_info()

            # Build recommendations
            self._build_recommendations(result)

            duration_ms = int((time.monotonic() - start) * 1000)
            self._last_duration_ms = duration_ms

            self.log_decision("Infrastructure report", {
                "pg_enabled": result["pg_enabled"],
                "tables": len(result.get("tables", {})),
                "recommendations": len(result["recommendations"]),
                "duration_ms": duration_ms,
            })

            self.publish("infra_report", {
                "pg_enabled": result["pg_enabled"],
                "recommendations_count": len(result["recommendations"]),
                "timestamp": result["timestamp"],
            })

            self._set_status(AgentStatus.IDLE, "Report done")
            return result

        except Exception as exc:
            self.log("Full report failed", {"error": str(exc)}, level="ERROR")
            self._set_status(AgentStatus.ERROR, str(exc))
            raise

    # ── Private: PG checks ───────────────────────────────────────────

    def _check_pg_connectivity(self, result: dict) -> bool:
        """Check PostgreSQL is reachable."""
        try:
            from ..database import is_pg_enabled, get_conn
            if not is_pg_enabled():
                result["pg_status"] = "not_configured"
                result["fallback_active"] = True
                result["issues"].append({
                    "area": "database",
                    "severity": "WARN",
                    "detail": "PostgreSQL not configured — using JSON fallback",
                })
                return False

            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
            result["pg_status"] = "connected"
            self._consecutive_pg_failures = 0
            return True

        except Exception as exc:
            self._consecutive_pg_failures += 1
            result["pg_status"] = "error"
            severity = "CRITICAL" if self._consecutive_pg_failures >= 3 else "WARN"
            result["issues"].append({
                "area": "database",
                "severity": severity,
                "detail": f"PG connection failed ({self._consecutive_pg_failures} consecutive): {exc}",
            })
            return False

    def _check_pool_health(self, result: dict) -> None:
        """Check connection pool status."""
        pool_info = self._get_pool_info()
        result["pool_status"] = pool_info.get("status", "unknown")
        if pool_info.get("status") == "error":
            result["issues"].append({
                "area": "pool",
                "severity": "WARN",
                "detail": f"Pool health check failed: {pool_info.get('error')}",
            })

    def _get_pool_info(self) -> dict:
        """Get connection pool information."""
        try:
            from ..database import _get_pool
            pool = _get_pool()
            # psycopg2 pool doesn't expose much, but we can check basic info
            return {
                "status": "ok",
                "minconn": getattr(pool, 'minconn', 'N/A'),
                "maxconn": getattr(pool, 'maxconn', 'N/A'),
            }
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    def _check_pending_trades(self, result: dict) -> None:
        """Check for stuck PENDING trades."""
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
                        cur.execute("SELECT COUNT(*) FROM trades WHERE result = 'PENDING'")
                        total_pending = cur.fetchone()[0]
                        result["pending_trades"] = total_pending
                        if stuck > 0:
                            result["issues"].append({
                                "area": "pending_trades",
                                "severity": "WARN",
                                "detail": f"{stuck} trades stuck PENDING for >2 days (total pending: {total_pending})",
                            })
            else:
                # JSON fallback
                try:
                    from ..learning import _load_trades
                    trades = _load_trades()
                    pending = [t for t in trades if t.result == "PENDING"]
                    result["pending_trades"] = len(pending)
                except Exception:
                    pass
        except Exception as exc:
            self.log("Pending trades check failed", {"error": str(exc)}, level="WARN")

    def _check_fallback_status(self, result: dict) -> None:
        """Detect if JSON fallback is being used instead of PG."""
        try:
            from ..database import is_pg_enabled
            if not is_pg_enabled():
                result["fallback_active"] = True
                return

            # Check if JSON files have data while PG might be empty
            from pathlib import Path
            data_dir = Path(__file__).resolve().parent.parent.parent.parent / "data"
            json_files = {
                "trades.json": "trades",
                "journal.json": "journal_entries",
                "scan_history.json": "scan_history",
            }
            for json_file, pg_table in json_files.items():
                fpath = data_dir / json_file
                if fpath.exists():
                    try:
                        import json
                        with open(fpath, "r") as f:
                            data = json.load(f)
                        if isinstance(data, list) and len(data) > 0:
                            # JSON has data — check if PG also has data
                            with get_conn() as conn:
                                with conn.cursor() as cur:
                                    cur.execute(f"SELECT COUNT(*) FROM {pg_table}")
                                    pg_count = cur.fetchone()[0]
                            if pg_count == 0 and len(data) > 5:
                                result["issues"].append({
                                    "area": "data_sync",
                                    "severity": "WARN",
                                    "detail": f"{json_file} has {len(data)} entries but PG {pg_table} is empty — migration needed",
                                })
                    except Exception:
                        pass
        except Exception:
            pass

    def _check_table_health(self, result: dict) -> None:
        """Quick table row count check."""
        try:
            from ..database import get_conn
            with get_conn() as conn:
                with conn.cursor() as cur:
                    for table in ["agent_messages", "agent_logs"]:
                        cur.execute(f"SELECT COUNT(*) FROM {table}")
                        count = cur.fetchone()[0]
                        if table == "agent_messages" and count > 50000:
                            result["issues"].append({
                                "area": "table_bloat",
                                "severity": "WARN",
                                "detail": f"{table} has {count} rows — pruning may be needed",
                            })
                        elif table == "agent_logs" and count > 100000:
                            result["issues"].append({
                                "area": "table_bloat",
                                "severity": "WARN",
                                "detail": f"{table} has {count} rows — pruning may be needed",
                            })
        except Exception:
            pass

    # ── Private: Maintenance ─────────────────────────────────────────

    def _run_pg_maintenance(self) -> dict:
        """Run PostgreSQL VACUUM ANALYZE + agent table pruning."""
        from ..database import pg_run_maintenance
        return pg_run_maintenance()

    def _collect_table_stats(self) -> dict:
        """Collect table statistics."""
        from ..database import pg_table_stats
        return pg_table_stats()

    def _check_table_bloat(self, result: dict) -> None:
        """Check for tables that have grown too large."""
        stats = result.get("stats", {})
        if isinstance(stats, dict) and "error" not in stats:
            for table, info in stats.items():
                if isinstance(info, dict) and info.get("size_mb", 0) > 500:
                    result["issues"].append({
                        "area": "table_bloat",
                        "severity": "WARN",
                        "detail": f"Table {table} is {info['size_mb']:.1f}MB — consider archiving old data",
                    })

    def _check_data_consistency(self, result: dict) -> None:
        """Check cross-table data consistency."""
        try:
            from ..database import is_pg_enabled, get_conn
            if not is_pg_enabled():
                return

            with get_conn() as conn:
                with conn.cursor() as cur:
                    # Check: trades with result but no journal entry
                    cur.execute("""
                        SELECT COUNT(*) FROM trades t
                        WHERE t.result != 'PENDING'
                        AND NOT EXISTS (
                            SELECT 1 FROM journal_entries j
                            WHERE j.ticker = t.ticker AND j.entry_time = t.timestamp
                        )
                    """)
                    orphan_trades = cur.fetchone()[0]
                    if orphan_trades > 10:
                        result["issues"].append({
                            "area": "data_consistency",
                            "severity": "WARN",
                            "detail": f"{orphan_trades} closed trades without journal entries",
                        })

        except Exception as exc:
            self.log("Data consistency check failed", {"error": str(exc)}, level="WARN")

    # ── Private: Analysis ────────────────────────────────────────────

    def _analyze_agent_errors_summary(self) -> dict:
        """Analyze ERROR/WARN logs across all agents."""
        try:
            from ..database import is_pg_enabled, get_conn
            if not is_pg_enabled():
                return {}

            with get_conn() as conn:
                with conn.cursor() as cur:
                    # Errors per agent in last 24h
                    cur.execute("""
                        SELECT agent_name, level, COUNT(*) as cnt
                        FROM agent_logs
                        WHERE level IN ('ERROR', 'WARN')
                        AND timestamp > NOW() - INTERVAL '24 hours'
                        GROUP BY agent_name, level
                        ORDER BY cnt DESC
                    """)
                    rows = cur.fetchall()
                    summary = {}
                    for row in rows:
                        agent = row[0]
                        if agent not in summary:
                            summary[agent] = {"errors": 0, "warnings": 0}
                        if row[1] == "ERROR":
                            summary[agent]["errors"] = row[2]
                        else:
                            summary[agent]["warnings"] = row[2]
                    return summary
        except Exception:
            return {}

    def _build_recommendations(self, result: dict) -> None:
        """Build actionable recommendations based on infrastructure state."""
        recs = result["recommendations"]

        # PG not configured
        if not result.get("pg_enabled"):
            recs.append({
                "priority": "HIGH",
                "area": "database",
                "action": "Configure PostgreSQL — JSON fallback has no VACUUM, no concurrent access safety",
            })

        # Table bloat
        tables = result.get("tables", {})
        if isinstance(tables, dict):
            for table, info in tables.items():
                if isinstance(info, dict):
                    rows = info.get("rows", 0)
                    if table == "agent_logs" and rows > 50000:
                        recs.append({
                            "priority": "MEDIUM",
                            "area": "pruning",
                            "action": f"agent_logs has {rows} rows — consider reducing max_age_days",
                        })
                    if table == "agent_messages" and rows > 20000:
                        recs.append({
                            "priority": "MEDIUM",
                            "area": "pruning",
                            "action": f"agent_messages has {rows} rows — consider reducing max_age_days",
                        })

        # Agent errors
        errors_summary = result.get("agent_logs_summary", {})
        for agent, counts in errors_summary.items():
            if counts.get("errors", 0) > 10:
                recs.append({
                    "priority": "HIGH",
                    "area": "agent_health",
                    "action": f"Agent '{agent}' has {counts['errors']} errors in last 24h — investigate",
                })

    # ── Metrics ──────────────────────────────────────────────────────

    def get_metrics(self) -> dict:
        return {
            "health_status": self._last_health_status,
            "total_health_checks": self._total_health_checks,
            "total_maintenance_runs": self._total_maintenance_runs,
            "consecutive_pg_failures": self._consecutive_pg_failures,
            "last_duration_ms": self._last_duration_ms,
            "last_pg_stats": self._last_pg_stats,
        }
