"""Agent News — Collecte, curation et fiabilité des sources de données.

Responsabilités :
- Collecte les news de toutes les sources (RSS, APIs structurées, GNews, yfinance)
- Déduplique les headlines (Jaccard cross-scan)
- Évalue la santé des sources (latence, taux d'erreur, sources mortes)
- Publie les news brutes sur le message bus → Agent Scoring les consomme
- Revue hebdomadaire dimanche 20h (santé sources + recommandations)

Audit fixes v7.5:
- N1: duration_ms published in bus message and stored in metrics
- N2: Removed dead code (serialized list built but never used)
- N3: hasattr guard removed — NewsItem always has .source field
- N4: last_duration_ms exposed in get_metrics()
"""

import time
from datetime import datetime, timezone

from .base import BaseAgent, AgentStatus


class AgentNews(BaseAgent):
    name = "news"
    description = "Collecte & curation des sources de données"

    def __init__(self):
        super().__init__()
        self._last_collect_count: int = 0
        self._last_source_errors: int = 0
        self._total_collected: int = 0
        self._total_filtered_dedup: int = 0
        self._last_duration_ms: int = 0

    def run(self, scan_type=None, **kwargs) -> dict:
        """Collect news from all sources, dedup, publish to bus.

        Returns dict with collection results.
        """
        self._set_status(AgentStatus.WORKING, "Collecting news from all sources")

        start = time.monotonic()
        result = {
            "news_items": [],
            "raw_count": 0,
            "after_dedup": 0,
            "source_health": None,
            "errors": [],
        }

        try:
            # Step 1: Collect from all sources
            news_items = self.execute(
                "Collecting news from all sources",
                self._collect_all_news,
            )
            result["raw_count"] = len(news_items)

            self.log("News collected", {
                "count": len(news_items),
                "sources": self._count_by_source(news_items),
            })

            if not news_items:
                self.log("No news collected", level="WARN")
                self._set_status(AgentStatus.IDLE, "No news found")
                result["news_items"] = []
                return result

            # Step 2: Cross-scan dedup
            before_dedup = len(news_items)
            news_items = self.execute(
                "Deduplicating headlines",
                self._dedup_news,
                news_items,
            )
            filtered = before_dedup - len(news_items)
            result["after_dedup"] = len(news_items)

            if filtered > 0:
                self._total_filtered_dedup += filtered
                self.log("Cross-scan dedup", {
                    "before": before_dedup,
                    "after": len(news_items),
                    "filtered": filtered,
                })

            # Step 3: Source health snapshot
            try:
                source_health = self._get_source_health()
                result["source_health"] = source_health
                errors = sum(
                    1 for s in (source_health or {}).get("sources", {}).values()
                    if s.get("status") in ("DEAD", "DEGRADED")
                )
                self._last_source_errors = errors
                if errors > 0:
                    self.log("Source health issues", {
                        "degraded_or_dead": errors,
                    }, level="WARN")
            except Exception as exc:
                self.log("Source health check failed", {"error": str(exc)}, level="WARN")

            # Step 4: Publish to bus for Agent Scoring
            self._last_collect_count = len(news_items)
            self._total_collected += len(news_items)
            result["news_items"] = news_items

            # N1: Compute duration before publishing (consistent with Scoring agents)
            duration_ms = int((time.monotonic() - start) * 1000)
            self._last_duration_ms = duration_ms

            # N2: Removed dead serialized code — bus message carries count, not items
            self.publish("news_collected", {
                "scan_type": scan_type.value if scan_type else None,
                "count": len(news_items),
                "duration_ms": duration_ms,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            self.log_decision("News collection complete", {
                "total": len(news_items),
                "dedup_filtered": filtered,
                "sources_ok": not bool(self._last_source_errors),
                "duration_ms": duration_ms,
            })

            self._set_status(AgentStatus.IDLE, f"Collected {len(news_items)} news")
            return result

        except Exception as exc:
            self.log("Collection failed", {"error": str(exc)}, level="ERROR")
            self._set_status(AgentStatus.ERROR, str(exc))
            raise

    def run_weekly_review(self) -> dict | None:
        """Run the weekly source health review (Sunday 20h)."""
        self._set_status(AgentStatus.WORKING, "Weekly source health review")
        try:
            from ..source_monitor import create_weekly_review_journal_entry
            review = create_weekly_review_journal_entry()
            if review:
                self.log_decision("Weekly source review", {
                    "severity": review.get("severity"),
                    "dead": review.get("dead_count", 0),
                    "degraded": review.get("degraded_count", 0),
                    "recommendations": review.get("recommendations", []),
                })
                self.publish("weekly_review", review)
            else:
                self.log("Weekly review: no data available")
            self._set_status(AgentStatus.IDLE, "Weekly review done")
            return review
        except Exception as exc:
            self.log("Weekly review failed", {"error": str(exc)}, level="ERROR")
            self._set_status(AgentStatus.ERROR, str(exc))
            raise

    def run_event_check(self) -> dict | None:
        """Check for high-impact event signals (every 10 min)."""
        try:
            from ..event_scanner import should_trigger_scan, determine_scan_type
            should_trigger, triggers = should_trigger_scan()
            if not should_trigger:
                return None

            self.log_decision("Event signal detected", {
                "trigger_count": len(triggers),
                "triggers": [t[:100] for t in triggers[:5]],
                "scan_type": determine_scan_type(),
            })

            self.publish("event_detected", {
                "trigger_count": len(triggers),
                "scan_type": determine_scan_type(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            return {
                "should_trigger": True,
                "triggers": triggers,
                "scan_type": determine_scan_type(),
            }
        except Exception as exc:
            self.log("Event check failed", {"error": str(exc)}, level="ERROR")
            return None

    # ── Private helpers ────────────────────────────────────────────

    def _collect_all_news(self):
        from ..news_collector import collect_all_news
        return collect_all_news()

    def _dedup_news(self, news_items):
        from ..news_collector import _jaccard_similarity
        from ..scan_history import get_recently_scored_titles

        recently_scored = get_recently_scored_titles()
        if not recently_scored:
            return news_items

        filtered = []
        for item in news_items:
            is_dup = any(
                _jaccard_similarity(item.title, scored) >= 0.65
                for scored in recently_scored
            )
            if not is_dup:
                filtered.append(item)
        return filtered

    def _get_source_health(self):
        from ..source_monitor import get_tracker
        return get_tracker().get_scan_health_snapshot()

    def _count_by_source(self, news_items) -> dict:
        # N3: Direct access — NewsItem.source always exists (has default)
        counts: dict[str, int] = {}
        for item in news_items:
            counts[item.source] = counts.get(item.source, 0) + 1
        return counts

    def get_metrics(self) -> dict:
        return {
            "last_collect_count": self._last_collect_count,
            "source_errors": self._last_source_errors,
            "total_collected": self._total_collected,
            "total_filtered_dedup": self._total_filtered_dedup,
            "last_duration_ms": self._last_duration_ms,
        }
