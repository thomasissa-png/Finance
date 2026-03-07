"""Source Health Monitor — tracks reliability of all news/data sources.

Instruments every source call (structured APIs, RSS feeds, yfinance, mainstream)
to record success/failure/latency/item count. Provides:

1. Per-scan health snapshot: attached to scan_history for visibility
2. Daily report: aggregated success rates + errors (logged at 22h with journal)
3. Weekly review: trend analysis, dead source detection, recommendations
   (created as a journal entry every Monday)

ZERO additional API cost — only instruments existing calls, no extra requests.

Source priority (for edge relevance):
- CRITICAL: Phase 0 structured APIs (weather, EIA, USDA, COT, etc.) — our edge
- IMPORTANT: Phase 1 early-signal RSS (NOAA, USDA, shipping, OSINT)
- USEFUL: Phase 2 yfinance news
- LOW: Phase 3 mainstream RSS (BBC, CNBC)
"""

import json
import logging
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
SOURCE_HEALTH_FILE = DATA_DIR / "source_health.json"

# ── Source Classification ─────────────────────────────────────────────
# Maps source names to their edge priority tier

PHASE_0_SOURCES = {
    "weather", "eia", "gnews", "usda", "wasde", "cot", "options",
    "eonet", "agsi", "fedwatch", "shfe", "woah", "ndvi", "freight",
    "lme_proxy", "chokepoint", "dark_pool",
}

PHASE_1_LABEL = "early_signal"
PHASE_2_LABEL = "yfinance"
PHASE_3_LABEL = "rss_mainstream"

SOURCE_PRIORITY = {}
for s in PHASE_0_SOURCES:
    SOURCE_PRIORITY[s] = "CRITICAL"
# Phase 1/2/3 are tracked per-feed inside their respective phases

# ── Per-source human-readable descriptions (for reports) ──────────────

SOURCE_DESCRIPTIONS: dict[str, str] = {
    "weather": "Open-Meteo weather alerts (18 agricultural zones)",
    "eia": "EIA US energy stocks (crude, gas, distillates, refinery)",
    "gnews": "GNews targeted search (28 commodity/weather queries)",
    "usda": "USDA NASS crop data (corn, soybeans, wheat progress)",
    "wasde": "USDA WASDE supply/demand estimates",
    "cot": "CFTC COT positioning (commercial vs speculator)",
    "options": "Options unusual activity (put/call ratio, IV skew)",
    "eonet": "NASA EONET natural events (storms, fires, quakes)",
    "agsi": "GIE AGSI European gas storage",
    "fedwatch": "CME FedWatch implied rates",
    "shfe": "SHFE/LME inventory proxy (metal ETF volumes)",
    "woah": "WOAH animal disease alerts (ASF, HPAI, FMD)",
    "ndvi": "NASA POWER satellite NDVI (vegetation stress)",
    "freight": "Freight index proxy (Baltic Dry via BDRY)",
    "lme_proxy": "LME inventory proxy (CPER, JJN, PALL, PPLT)",
    "chokepoint": "Chokepoint monitoring (tanker stocks STNG, FRO)",
    "dark_pool": "Dark pool signals (ETF volume/price divergence)",
    PHASE_1_LABEL: "Early-signal RSS feeds (25 feeds: meteo, OSINT, shipping)",
    PHASE_2_LABEL: "Yahoo Finance news (15 key tickers)",
    PHASE_3_LABEL: "Mainstream RSS (BBC, CNBC, Investing.com)",
}

# Env vars required for each source (empty = no key needed)
SOURCE_ENV_VARS: dict[str, str] = {
    "eia": "EIA_API_KEY",
    "gnews": "GNEWS_API_KEY",
    "usda": "USDA_API_KEY",
    "wasde": "USDA_API_KEY",
    "agsi": "GIE_AGSI_API_KEY",
}


# ═══════════════════════════════════════════════════════════════════════
# Source Health Tracker — singleton, thread-safe
# ═══════════════════════════════════════════════════════════════════════

class SourceCallRecord:
    """Record of a single source call."""
    __slots__ = ("source", "phase", "timestamp", "success", "items_count",
                 "latency_ms", "error_type", "error_msg")

    def __init__(self, source: str, phase: str, success: bool,
                 items_count: int = 0, latency_ms: float = 0,
                 error_type: str = "", error_msg: str = ""):
        self.source = source
        self.phase = phase
        self.timestamp = datetime.now(timezone.utc)
        self.success = success
        self.items_count = items_count
        self.latency_ms = round(latency_ms, 1)
        self.error_type = error_type
        self.error_msg = error_msg[:300]  # Truncate long error messages

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "source": self.source,
            "phase": self.phase,
            "timestamp": self.timestamp.isoformat(),
            "success": self.success,
            "items_count": self.items_count,
            "latency_ms": self.latency_ms,
        }
        if not self.success:
            d["error_type"] = self.error_type
            d["error_msg"] = self.error_msg
        return d


class SourceHealthTracker:
    """Thread-safe tracker for all source call health data.

    Singleton — use get_tracker() to access.
    Records every source call during scans and provides daily/weekly reports.
    """

    def __init__(self):
        self._lock = threading.Lock()
        # Current scan's records (reset per scan)
        self._current_scan: list[SourceCallRecord] = []
        # All records for today (for daily report)
        self._today_records: list[SourceCallRecord] = []
        self._today_date: str = ""
        # Historical daily summaries (for weekly analysis)
        self._daily_summaries: list[dict] = []
        self._load_history()

    def _load_history(self) -> None:
        """Load historical daily summaries from disk."""
        if SOURCE_HEALTH_FILE.exists():
            try:
                data = json.loads(SOURCE_HEALTH_FILE.read_text())
                self._daily_summaries = data.get("daily_summaries", [])
                # Prune summaries older than 30 days
                cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
                self._daily_summaries = [
                    s for s in self._daily_summaries if s.get("date", "") >= cutoff[:10]
                ]
            except Exception as exc:
                logger.warning("Failed to load source health history: %s", exc)

    def _save_history(self) -> None:
        """Persist daily summaries to disk."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            data = {"daily_summaries": self._daily_summaries}
            SOURCE_HEALTH_FILE.write_text(json.dumps(data, indent=2, default=str))
        except Exception as exc:
            logger.warning("Failed to save source health history: %s", exc)

    def _check_day_rollover(self) -> None:
        """Reset today's records if the date has changed."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._today_date:
            # Save yesterday's summary before rolling over
            if self._today_date and self._today_records:
                summary = self._build_daily_summary(self._today_date, self._today_records)
                self._daily_summaries.append(summary)
                # Keep max 30 days
                if len(self._daily_summaries) > 30:
                    self._daily_summaries = self._daily_summaries[-30:]
                self._save_history()
            self._today_date = today
            self._today_records = []

    # ── Recording ─────────────────────────────────────────────────────

    def record_success(self, source: str, phase: str, items_count: int,
                       latency_ms: float) -> None:
        """Record a successful source call."""
        record = SourceCallRecord(
            source=source, phase=phase, success=True,
            items_count=items_count, latency_ms=latency_ms,
        )
        with self._lock:
            self._check_day_rollover()
            self._current_scan.append(record)
            self._today_records.append(record)

    def record_failure(self, source: str, phase: str,
                       error: Exception | str, latency_ms: float = 0) -> None:
        """Record a failed source call."""
        if isinstance(error, Exception):
            error_type = type(error).__name__
            error_msg = str(error)
        else:
            error_type = "Error"
            error_msg = error
        record = SourceCallRecord(
            source=source, phase=phase, success=False,
            error_type=error_type, error_msg=error_msg, latency_ms=latency_ms,
        )
        with self._lock:
            self._check_day_rollover()
            self._current_scan.append(record)
            self._today_records.append(record)

    def record_skipped(self, source: str, phase: str, reason: str) -> None:
        """Record a source that was intentionally skipped (missing API key, etc)."""
        record = SourceCallRecord(
            source=source, phase=phase, success=True,
            items_count=0, error_type="SKIPPED", error_msg=reason,
        )
        with self._lock:
            self._check_day_rollover()
            self._current_scan.append(record)
            self._today_records.append(record)

    # ── Per-scan snapshot ─────────────────────────────────────────────

    def get_scan_health_snapshot(self) -> dict:
        """Get health snapshot for the current scan and reset.

        Returns a dict suitable for embedding in scan_history entries.
        """
        with self._lock:
            records = list(self._current_scan)
            self._current_scan = []

        if not records:
            return {}

        ok = [r for r in records if r.success]
        fail = [r for r in records if not r.success]

        snapshot: dict[str, Any] = {
            "total_sources": len(records),
            "ok": len(ok),
            "failed": len(fail),
            "total_items": sum(r.items_count for r in ok),
            "avg_latency_ms": round(
                sum(r.latency_ms for r in records) / len(records), 1
            ) if records else 0,
        }

        if fail:
            snapshot["failures"] = [
                {
                    "source": r.source,
                    "phase": r.phase,
                    "error_type": r.error_type,
                    "error": r.error_msg,
                    "priority": SOURCE_PRIORITY.get(r.source, "LOW"),
                }
                for r in fail
            ]

        # Flag CRITICAL failures (Phase 0 sources)
        critical_failures = [r for r in fail if r.source in PHASE_0_SOURCES]
        if critical_failures:
            snapshot["critical_failures"] = len(critical_failures)
            snapshot["critical_failed_sources"] = [r.source for r in critical_failures]

        return snapshot

    # ── Daily report ──────────────────────────────────────────────────

    def get_daily_report(self) -> dict:
        """Build daily health report from today's records.

        Called at journal time (22h) to produce the daily summary.
        """
        with self._lock:
            self._check_day_rollover()
            records = list(self._today_records)
            today = self._today_date

        return self._build_daily_summary(today, records)

    def _build_daily_summary(self, date: str, records: list[SourceCallRecord]) -> dict:
        """Aggregate records into a daily summary per source."""
        if not records:
            return {"date": date, "sources": {}, "scan_count": 0}

        # Group records by source
        by_source: dict[str, list[SourceCallRecord]] = defaultdict(list)
        for r in records:
            by_source[r.source].append(r)

        sources_summary: dict[str, dict] = {}
        for source, recs in sorted(by_source.items()):
            ok = [r for r in recs if r.success and r.error_type != "SKIPPED"]
            skipped = [r for r in recs if r.error_type == "SKIPPED"]
            fail = [r for r in recs if not r.success]
            total_calls = len(ok) + len(fail)

            status = "OK"
            if total_calls == 0 and skipped:
                status = "SKIPPED"
            elif fail and not ok:
                status = "DEAD"
            elif fail:
                status = "DEGRADED"

            entry: dict[str, Any] = {
                "status": status,
                "calls_ok": len(ok),
                "calls_failed": len(fail),
                "total_items": sum(r.items_count for r in ok),
                "avg_latency_ms": round(
                    sum(r.latency_ms for r in (ok + fail)) / max(1, len(ok) + len(fail)), 1
                ),
                "priority": SOURCE_PRIORITY.get(source, "LOW"),
                "description": SOURCE_DESCRIPTIONS.get(source, source),
            }

            if fail:
                # Deduplicate error messages
                errors = list({r.error_msg for r in fail})
                entry["errors"] = errors[:5]  # Max 5 unique errors
                entry["last_error_type"] = fail[-1].error_type

            if skipped:
                entry["skip_reason"] = skipped[0].error_msg

            sources_summary[source] = entry

        # Count distinct scans (approximate: group records by 5-minute windows)
        scan_timestamps = set()
        for r in records:
            # Round to 5-minute window
            ts = r.timestamp.replace(second=0, microsecond=0)
            ts = ts.replace(minute=(ts.minute // 5) * 5)
            scan_timestamps.add(ts)

        total_ok = sum(1 for r in records if r.success and r.error_type != "SKIPPED")
        total_fail = sum(1 for r in records if not r.success)

        return {
            "date": date,
            "scan_count": len(scan_timestamps),
            "total_calls_ok": total_ok,
            "total_calls_failed": total_fail,
            "success_rate": round(total_ok / max(1, total_ok + total_fail) * 100, 1),
            "sources": sources_summary,
        }

    def save_daily_report(self) -> dict:
        """Build, store, and return the daily report. Called at journal time."""
        report = self.get_daily_report()
        with self._lock:
            # Store in history
            if report.get("sources"):
                # Remove existing entry for same date
                self._daily_summaries = [
                    s for s in self._daily_summaries if s.get("date") != report["date"]
                ]
                self._daily_summaries.append(report)
                if len(self._daily_summaries) > 30:
                    self._daily_summaries = self._daily_summaries[-30:]
                self._save_history()

        # PG persistence
        try:
            from .database import is_pg_enabled
            if is_pg_enabled():
                _pg_save_daily_report(report)
        except Exception as exc:
            logger.warning("Failed to save source health to PG: %s", exc)

        return report

    # ── Weekly review ─────────────────────────────────────────────────

    def get_weekly_review(self) -> dict:
        """Analyze last 7 days of source health for trends.

        Returns a structured review with:
        - dead_sources: failed 100% over last 7 days
        - degraded_sources: success rate < 70% over last 7 days
        - recovered_sources: were degraded/dead, now OK
        - stable_sources: consistently working
        - recommendations: actionable suggestions
        """
        with self._lock:
            summaries = list(self._daily_summaries)

        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
        week_summaries = [s for s in summaries if s.get("date", "") >= cutoff]

        if not week_summaries:
            return {
                "period": f"{cutoff} → now",
                "days_with_data": 0,
                "message": "Pas assez de données pour une analyse hebdomadaire",
            }

        # Aggregate per-source stats across the week
        source_stats: dict[str, dict] = defaultdict(lambda: {
            "total_ok": 0, "total_fail": 0, "total_items": 0,
            "days_seen": 0, "days_dead": 0, "errors": set(),
            "latencies": [],
        })

        all_sources_seen: set[str] = set()

        for summary in week_summaries:
            for source, data in summary.get("sources", {}).items():
                all_sources_seen.add(source)
                stats = source_stats[source]
                stats["total_ok"] += data.get("calls_ok", 0)
                stats["total_fail"] += data.get("calls_failed", 0)
                stats["total_items"] += data.get("total_items", 0)
                stats["days_seen"] += 1
                if data.get("status") == "DEAD":
                    stats["days_dead"] += 1
                for err in data.get("errors", []):
                    stats["errors"].add(err)
                if data.get("avg_latency_ms", 0) > 0:
                    stats["latencies"].append(data["avg_latency_ms"])

        dead_sources: list[dict] = []
        degraded_sources: list[dict] = []
        slow_sources: list[dict] = []
        stable_sources: list[dict] = []
        low_yield_sources: list[dict] = []

        for source, stats in sorted(source_stats.items()):
            total = stats["total_ok"] + stats["total_fail"]
            success_rate = stats["total_ok"] / max(1, total) * 100
            avg_items_per_call = stats["total_items"] / max(1, stats["total_ok"])
            avg_latency = (sum(stats["latencies"]) / len(stats["latencies"])
                           if stats["latencies"] else 0)
            priority = SOURCE_PRIORITY.get(source, "LOW")

            entry = {
                "source": source,
                "priority": priority,
                "description": SOURCE_DESCRIPTIONS.get(source, source),
                "success_rate": round(success_rate, 1),
                "total_calls": total,
                "total_items": stats["total_items"],
                "avg_items_per_call": round(avg_items_per_call, 1),
                "days_seen": stats["days_seen"],
                "avg_latency_ms": round(avg_latency, 0),
            }

            if stats["errors"]:
                entry["errors"] = list(stats["errors"])[:3]

            if stats["total_ok"] == 0 and stats["total_fail"] > 0:
                dead_sources.append(entry)
            elif success_rate < 70:
                degraded_sources.append(entry)
            elif avg_latency > 10000:  # > 10s average
                slow_sources.append(entry)
            elif stats["total_ok"] > 0 and avg_items_per_call < 0.1:
                low_yield_sources.append(entry)
            else:
                stable_sources.append(entry)

        # Build recommendations
        recommendations = self._build_recommendations(
            dead_sources, degraded_sources, slow_sources, low_yield_sources
        )

        return {
            "period": f"{cutoff} → {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
            "days_with_data": len(week_summaries),
            "total_sources_tracked": len(all_sources_seen),
            "dead_sources": dead_sources,
            "degraded_sources": degraded_sources,
            "slow_sources": slow_sources,
            "low_yield_sources": low_yield_sources,
            "stable_sources": stable_sources,
            "recommendations": recommendations,
        }

    def _build_recommendations(self, dead: list, degraded: list,
                               slow: list, low_yield: list) -> list[str]:
        """Generate actionable recommendations based on source health trends."""
        recs: list[str] = []

        for src in dead:
            name = src["source"]
            desc = src.get("description", name)
            priority = src["priority"]
            errors = src.get("errors", ["unknown error"])

            if priority == "CRITICAL":
                recs.append(
                    f"[URGENT] {name} ({desc}) est MORT depuis {src['days_seen']} jours. "
                    f"Source CRITIQUE pour notre edge. Erreur: {errors[0][:100]}. "
                    f"ACTION: vérifier l'API, la clé, ou chercher une alternative."
                )
            else:
                recs.append(
                    f"[INFO] {name} ({desc}) est mort. Erreur: {errors[0][:100]}. "
                    f"Priorité {priority} — envisager suppression si non réparable."
                )

        for src in degraded:
            name = src["source"]
            rate = src["success_rate"]
            recs.append(
                f"[WARNING] {name} dégradé — {rate}% de succès cette semaine. "
                f"Vérifier si l'API a changé ou si le rate limit est atteint."
            )

        for src in slow:
            name = src["source"]
            lat = src["avg_latency_ms"]
            recs.append(
                f"[PERF] {name} très lent ({lat:.0f}ms en moyenne). "
                f"Risque de timeout pendant les scans — peut bloquer d'autres sources."
            )

        for src in low_yield:
            name = src["source"]
            avg = src["avg_items_per_call"]
            priority = src["priority"]
            if priority == "CRITICAL":
                recs.append(
                    f"[CHECK] {name} retourne très peu de données ({avg:.1f} items/appel). "
                    f"Source CRITIQUE — vérifier si le format de réponse a changé."
                )

        # Source discovery — suggest new sources based on gaps
        discovery = _get_source_discovery_suggestions()
        if discovery:
            recs.append("")  # separator
            for suggestion in discovery:
                recs.append(suggestion)

        if not recs or recs == [""]:
            recs = ["Toutes les sources fonctionnent correctement. Aucune action requise."]

        return recs


# ═══════════════════════════════════════════════════════════════════════
# Source Discovery — identify potential new data sources
# ═══════════════════════════════════════════════════════════════════════

# Known free APIs relevant to our edge-first commodity/weather/supply-chain focus.
# Each entry: (name, description, url, category, reason_useful)
# Updated periodically — sources here are NOT yet integrated.
POTENTIAL_SOURCES: list[tuple[str, str, str, str, str]] = [
    (
        "NOAA CPC",
        "Climate Prediction Center — seasonal forecasts, drought outlooks",
        "https://www.cpc.ncep.noaa.gov/",
        "weather",
        "Complète Open-Meteo avec des prévisions saisonnières (El Niño, La Niña) impactant agri",
    ),
    (
        "ICE Futures",
        "Intercontinental Exchange — open interest, volume data",
        "https://www.theice.com/marketdata",
        "positioning",
        "Alternative au CFTC COT pour les soft commodities (café, cacao, sucre, coton)",
    ),
    (
        "IGC Grain Market Report",
        "International Grains Council — monthly supply/demand",
        "https://www.igc.int/",
        "agri",
        "Données mondiales céréales complémentaires au WASDE (focus non-US)",
    ),
    (
        "NOAA GHCN",
        "Global Historical Climatology Network — données température/précipitations",
        "https://www.ncei.noaa.gov/products/land-based-station/global-historical-climatology-network-daily",
        "weather",
        "Données observées (pas prévisions) — valide les alertes Open-Meteo avec des mesures réelles",
    ),
    (
        "USDA FAS GATS",
        "Foreign Agricultural Service — export/import data",
        "https://apps.fas.usda.gov/gats/",
        "agri",
        "Flux commerciaux agricoles US — détecte les export bans avant Reuters",
    ),
    (
        "Brazil CONAB",
        "Companhia Nacional de Abastecimento — crop estimates Brazil",
        "https://www.conab.gov.br/",
        "agri",
        "Estimations safra brésilienne (soja, maïs, café, sucre) — publie avant le WASDE",
    ),
    (
        "India DGCIS",
        "Directorate General of Commercial Intelligence — India trade data",
        "https://www.dgciskol.gov.in/",
        "trade",
        "Détection précoce des export bans indiens (riz, blé, sucre, oignons)",
    ),
    (
        "AIS Marine Traffic",
        "Vessel tracking — chokepoint transit monitoring",
        "https://www.marinetraffic.com/",
        "supply_chain",
        "Données AIS pour détecter les congestions (Suez, Panama, Hormuz) avant les médias",
    ),
    (
        "ECMWF Open Data",
        "European weather forecasts — medium-range (15 days)",
        "https://www.ecmwf.int/en/forecasts/datasets/open-data",
        "weather",
        "Modèle météo européen (meilleur que GFS pour Europe/Afrique) — gratuit depuis 2022",
    ),
    (
        "ENTSO-E Transparency",
        "European electricity generation/demand — real-time",
        "https://transparency.entsoe.eu/",
        "energy",
        "Production électrique EU en temps réel — proxy pour demande gaz/charbon",
    ),
]


def _get_source_discovery_suggestions() -> list[str]:
    """Generate suggestions for new data sources to investigate.

    Checks which potential sources are NOT yet integrated and suggests
    the most relevant ones based on our edge-first commodity focus.
    """
    import os

    suggestions: list[str] = []

    # Check for dead sources that could be replaced
    # (This is handled separately in _build_recommendations)

    # Suggest new sources from our curated list
    # Prioritize by category relevance to our edge
    priority_categories = ["weather", "agri", "supply_chain", "energy", "positioning"]

    relevant = [s for s in POTENTIAL_SOURCES if s[3] in priority_categories]
    other = [s for s in POTENTIAL_SOURCES if s[3] not in priority_categories]

    # Pick top 3 most relevant suggestions (rotate weekly by date)
    from datetime import datetime, timezone
    week_number = datetime.now(timezone.utc).isocalendar()[1]
    start_idx = (week_number * 2) % max(1, len(relevant))

    selected = (relevant[start_idx:] + relevant[:start_idx])[:3]
    if len(selected) < 3 and other:
        selected.extend(other[:3 - len(selected)])

    if selected:
        suggestions.append("[DISCOVERY] Sources potentielles à investiguer cette semaine :")
        for name, desc, url, cat, reason in selected:
            suggestions.append(
                f"  → {name} ({cat}) — {reason}. URL: {url}"
            )

    return suggestions
_tracker_lock = threading.Lock()


def get_tracker() -> SourceHealthTracker:
    """Get the global source health tracker (singleton)."""
    global _tracker
    if _tracker is None:
        with _tracker_lock:
            if _tracker is None:
                _tracker = SourceHealthTracker()
    return _tracker


# ═══════════════════════════════════════════════════════════════════════
# PostgreSQL persistence for source health
# ═══════════════════════════════════════════════════════════════════════

_CREATE_SOURCE_HEALTH = """
CREATE TABLE IF NOT EXISTS source_health (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL,
    report JSONB NOT NULL,
    report_type VARCHAR(10) DEFAULT 'daily',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(date, report_type)
)
"""


def init_source_health_table() -> None:
    """Create the source_health table if it doesn't exist."""
    from .database import is_pg_enabled, get_conn
    if not is_pg_enabled():
        return
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_CREATE_SOURCE_HEALTH)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_source_health_date
                    ON source_health(date)
                """)
        logger.info("source_health table initialized")
    except Exception as exc:
        logger.warning("Failed to create source_health table: %s", exc)


def _pg_save_daily_report(report: dict) -> None:
    """Save daily report to PostgreSQL."""
    from .database import get_conn
    date_str = report.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO source_health (date, report, report_type)
                VALUES (%s, %s, 'daily')
                ON CONFLICT (date, report_type) DO UPDATE SET report = EXCLUDED.report
            """, (date_str, json.dumps(report, default=str)))


def pg_save_weekly_review(review: dict) -> None:
    """Save weekly review to PostgreSQL."""
    from .database import is_pg_enabled, get_conn
    if not is_pg_enabled():
        return
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO source_health (date, report, report_type)
                    VALUES (%s, %s, 'weekly')
                    ON CONFLICT (date, report_type) DO UPDATE SET report = EXCLUDED.report
                """, (date_str, json.dumps(review, default=str)))
    except Exception as exc:
        logger.warning("Failed to save weekly review to PG: %s", exc)


def pg_load_source_health(days: int = 7,
                          report_type: str | None = None) -> list[dict]:
    """Load recent source health reports from PostgreSQL."""
    from .database import is_pg_enabled, get_conn
    import psycopg2.extras
    if not is_pg_enabled():
        return []
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if report_type:
                    cur.execute("""
                        SELECT date, report, report_type FROM source_health
                        WHERE date >= CURRENT_DATE - %s AND report_type = %s
                        ORDER BY date DESC
                    """, (days, report_type))
                else:
                    cur.execute("""
                        SELECT date, report, report_type FROM source_health
                        WHERE date >= CURRENT_DATE - %s
                        ORDER BY date DESC
                    """, (days,))
                rows = cur.fetchall()
                return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("Failed to load source health from PG: %s", exc)
        return []


# ═══════════════════════════════════════════════════════════════════════
# Weekly review as journal entry
# ═══════════════════════════════════════════════════════════════════════

def create_weekly_review_journal_entry() -> dict | None:
    """Create a journal entry for the weekly source health review.

    Called every Monday at journal time. Returns a JournalEntry-compatible dict
    or None if no data available.
    """
    tracker = get_tracker()
    review = tracker.get_weekly_review()

    if review.get("days_with_data", 0) == 0:
        logger.info("Weekly source review: no data available, skipping journal entry")
        return None

    # Build human-readable review text
    lines: list[str] = []
    lines.append(f"=== REVUE HEBDOMADAIRE DES SOURCES ({review['period']}) ===")
    lines.append(f"Sources trackées: {review.get('total_sources_tracked', 0)} | "
                 f"Jours avec données: {review['days_with_data']}")
    lines.append("")

    dead = review.get("dead_sources", [])
    degraded = review.get("degraded_sources", [])
    slow = review.get("slow_sources", [])

    if dead:
        lines.append(f"--- SOURCES MORTES ({len(dead)}) ---")
        for s in dead:
            lines.append(f"  [MORT] {s['source']} — {s['description']}")
            for err in s.get("errors", [])[:2]:
                lines.append(f"         Erreur: {err[:120]}")
        lines.append("")

    if degraded:
        lines.append(f"--- SOURCES DÉGRADÉES ({len(degraded)}) ---")
        for s in degraded:
            lines.append(f"  [DÉGRADÉ] {s['source']} — {s['success_rate']}% succès — {s['description']}")
        lines.append("")

    if slow:
        lines.append(f"--- SOURCES LENTES ({len(slow)}) ---")
        for s in slow:
            lines.append(f"  [LENT] {s['source']} — {s['avg_latency_ms']:.0f}ms moy")
        lines.append("")

    stable = review.get("stable_sources", [])
    if stable:
        lines.append(f"--- SOURCES STABLES ({len(stable)}) ---")
        for s in stable:
            lines.append(f"  [OK] {s['source']} — {s['success_rate']}% | "
                         f"{s['total_items']} items | {s['avg_latency_ms']:.0f}ms")
        lines.append("")

    recs = review.get("recommendations", [])
    if recs:
        lines.append("--- RECOMMANDATIONS ---")
        for r in recs:
            lines.append(f"  • {r}")

    review_text = "\n".join(lines)

    # Determine severity
    has_critical_dead = any(s["priority"] == "CRITICAL" for s in dead)
    has_critical_degraded = any(s["priority"] == "CRITICAL" for s in degraded)

    if has_critical_dead:
        severity = "CRITICAL"
    elif has_critical_degraded or dead:
        severity = "WARNING"
    else:
        severity = "OK"

    # Save weekly review to PG
    pg_save_weekly_review(review)

    return {
        "type": "source_health_review",
        "severity": severity,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "review_text": review_text,
        "review_data": review,
        "dead_count": len(dead),
        "degraded_count": len(degraded),
        "stable_count": len(stable),
        "recommendations": recs,
    }
