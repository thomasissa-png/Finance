"""Agent Learning — Expert ML, optimisation continue des paramètres.

Responsabilités :
- Réagit après chaque journal run (consomme journal_complete)
- Recalcule les 6 dimensions de learning :
  1. ticker*cat (base blend)
  2. session (europe/us)
  3. newscat+ticker (cross-dimension granulaire v5.2)
  4. regime VIX (low_vol/high_vol)
  5. direction (LONG/SHORT)
  6. delay_bias (précision prédictions transmission_delay)
- Met à jour les multipliers pour Agent Trader et Agent Scoring
- Génère le performance summary (format alerts-only)
- Identifie patterns, anomalies, drifts
- Publie les ajustements sur le bus

Expertise incarnée :
- 10+ ans ML : significance testing, decay temporel, anti-overfitting
- Apprentissage granulaire (newscat+ticker) sans sur-réagir au bruit
- Protection des commodities (jamais pénaliser la classe)
- Détection des anomalies (streaks, drawdown, skewness)
"""

import time
from datetime import datetime, timezone

from .base import BaseAgent, AgentStatus


class AgentLearning(BaseAgent):
    name = "learning"
    description = "Machine learning & optimisation continue"

    def __init__(self):
        super().__init__()
        self._last_adjustment_count: int = 0
        self._last_anomalies: list = []
        self._learning_dimensions: int = 6
        self._total_recalculations: int = 0
        self._cached_adjustments: dict | None = None
        self._cache_valid: bool = False

    def run(self, **kwargs) -> dict:
        """Recalculate all learning dimensions and publish adjustments.

        Called after each journal run.
        Returns dict with learning results.
        """
        self._set_status(AgentStatus.WORKING, "Recalculating learning dimensions")

        start = time.monotonic()
        result = {
            "adjustments": {},
            "anomalies": [],
            "performance_summary": None,
            "dimensions_updated": 0,
        }

        try:
            # Step 1: Compute learning adjustments (all 6 dimensions)
            self.log("Computing learning adjustments (6 dimensions)")

            learning_data = self.execute(
                "Computing 6 learning dimensions",
                self._compute_adjustments,
            )

            self._cached_adjustments = learning_data
            self._cache_valid = True
            self._total_recalculations += 1

            # Step 2: Log each dimension
            if isinstance(learning_data, dict):
                adj = learning_data.get("adjustments", {})
                session_adj = learning_data.get("session_adj", {})
                newscat_adj = learning_data.get("newscat_adj", {})
                regime_adj = learning_data.get("regime_adj", {})
                direction_adj = learning_data.get("direction_adj", {})
                delay_bias = learning_data.get("delay_bias_adj", 1.0)

                self._last_adjustment_count = len(adj)
                dims_updated = sum([
                    len(adj) > 0,
                    len(session_adj) > 0,
                    len(newscat_adj) > 0,
                    len(regime_adj) > 0,
                    len(direction_adj) > 0,
                    delay_bias != 1.0,
                ])
                result["dimensions_updated"] = dims_updated
                result["adjustments"] = learning_data

                self.log("Learning dimensions computed", {
                    "ticker_adjustments": len(adj),
                    "session_adj": session_adj,
                    "newscat_adj_count": len(newscat_adj),
                    "regime_adj": regime_adj,
                    "direction_adj": direction_adj,
                    "delay_bias": round(delay_bias, 3),
                    "dimensions_active": dims_updated,
                })

                # Log significant adjustments (far from 1.0)
                significant = {k: round(v, 3) for k, v in adj.items()
                               if abs(v - 1.0) > 0.1}
                if significant:
                    self.log("Significant ticker adjustments", {
                        "adjustments": significant,
                    })

            # Step 3: Compute performance summary + structured anomalies
            try:
                perf = self.execute(
                    "Building performance summary",
                    self._build_performance_summary,
                )
                result["performance_summary"] = perf

                # P5: Detect anomalies via structured extraction (not text parsing)
                anomalies = self.execute(
                    "Extracting anomalies",
                    self._extract_structured_anomalies,
                )
                anomaly_messages = [a.get("message", str(a)) for a in anomalies]
                result["anomalies"] = anomaly_messages
                self._last_anomalies = anomaly_messages

                if anomalies:
                    self.log("Anomalies detected", {
                        "count": len(anomalies),
                        "anomalies": anomalies[:10],
                    }, level="WARN")

            except Exception as exc:
                self.log("Performance summary failed", {"error": str(exc)}, level="WARN")

            # Step 4: Publish adjustments
            self.publish("learning_updated", {
                "adjustment_count": self._last_adjustment_count,
                "dimensions_active": result["dimensions_updated"],
                "anomaly_count": len(result["anomalies"]),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            self.log_decision("Learning update complete", {
                "adjustments": self._last_adjustment_count,
                "dimensions": result["dimensions_updated"],
                "anomalies": len(result["anomalies"]),
            })

            duration_ms = int((time.monotonic() - start) * 1000)
            self._set_status(AgentStatus.IDLE,
                             f"{self._last_adjustment_count} adjustments, {len(result['anomalies'])} anomalies")

            return result

        except Exception as exc:
            self.log("Learning computation failed", {"error": str(exc)}, level="ERROR")
            self._set_status(AgentStatus.ERROR, str(exc))
            raise

    def get_adjustments(self) -> dict:
        """Get cached learning adjustments (used by Agent Trader).

        Recalculates if cache is invalid (after journal).
        """
        if not self._cache_valid or self._cached_adjustments is None:
            self._cached_adjustments = self._compute_adjustments()
            self._cache_valid = True
        return self._cached_adjustments

    def invalidate_cache(self):
        """Invalidate the learning cache and perf summary cache (called after journal)."""
        self._cache_valid = False
        # P3+P7: Also invalidate the performance summary cache so next scan gets fresh data
        try:
            from ..learning import invalidate_perf_summary_cache
            invalidate_perf_summary_cache()
        except Exception:
            pass
        self.log("Learning cache invalidated (incl. perf summary)")

    # ── Private helpers ────────────────────────────────────────────

    def _compute_adjustments(self):
        from ..learning import compute_learning_adjustments
        return compute_learning_adjustments()

    def _build_performance_summary(self):
        from ..learning import build_performance_summary
        return build_performance_summary()

    def _extract_structured_anomalies(self) -> list[dict]:
        """P5: Extract structured anomalies from trade data."""
        from ..learning import extract_structured_anomalies
        return extract_structured_anomalies()

    def _extract_anomalies(self, perf_summary) -> list[str]:
        """Extract anomaly strings from the performance summary."""
        if not perf_summary:
            return []

        anomalies = []
        summary = perf_summary if isinstance(perf_summary, str) else str(perf_summary)

        # Parse alerts-only format for known anomaly patterns
        alert_keywords = [
            "EXPIRED rate",
            "streak",
            "drawdown",
            "skewness",
            "MAE",
            "reliability",
            "slippage",
            "magnitude",
            "direction accuracy",
        ]
        for line in summary.split("\n"):
            line_lower = line.lower()
            if any(kw.lower() in line_lower for kw in alert_keywords):
                anomalies.append(line.strip())

        return anomalies

    def get_metrics(self) -> dict:
        return {
            "last_adjustment_count": self._last_adjustment_count,
            "anomalies": self._last_anomalies[:5],
            "dimensions": self._learning_dimensions,
            "total_recalculations": self._total_recalculations,
            "cache_valid": self._cache_valid,
        }
