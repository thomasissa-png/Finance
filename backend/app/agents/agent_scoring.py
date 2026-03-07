"""Agent Scoring — Expert en spéculation avec 15+ ans d'expertise edge & news trading.

Responsabilités :
- Consomme les news brutes publiées par l'Agent News
- Pré-filtre les headlines zero-edge (earnings, macro évidentes)
- Score chaque headline via Claude API (11 dimensions)
- Applique la formule edge-weighted (surprise × clarity × edge_factor × reliability)
- Détecte les chain reactions (effets de second ordre)
- Publie les news scorées sur le bus → Agent Trader les consomme

Expertise incarnée :
- Identification des dislocations non pricées (rapport NOAA, gel Brésil, OSINT)
- Discrimination entre edge réel et bruit (earnings = 0 edge, météo locale = max edge)
- Évaluation du transmission_delay (combien de temps avant que le marché price)
"""

import time
from datetime import datetime, timezone

from .base import BaseAgent, AgentStatus


class AgentScoring(BaseAgent):
    name = "scoring"
    description = "Notation expert des news — edge & impact"

    def __init__(self):
        super().__init__()
        self._last_scored_count: int = 0
        self._last_zero_edge_filtered: int = 0
        self._total_scored: int = 0
        self._total_tokens_used: int = 0
        self._cache_hits: int = 0

    def run(self, news_items, scan_type, **kwargs) -> dict:
        """Score a batch of news items.

        Args:
            news_items: List of NewsItem objects from Agent News
            scan_type: ScanType enum (EUROPE / US)

        Returns dict with scored results and market context.
        """
        self._set_status(AgentStatus.WORKING, f"Scoring {len(news_items)} news items")

        start = time.monotonic()
        result = {
            "scored": [],
            "market_context": None,
            "zero_edge_filtered": 0,
            "cache_hits": 0,
            "tokens_used": 0,
        }

        try:
            # Log incoming items
            self.log("Scoring batch received", {
                "count": len(news_items),
                "scan_type": scan_type.value if scan_type else None,
            })

            # Step 1: Score via Claude API
            # The scorer handles pre-filtering, batching, caching internally
            scored, market_ctx = self.execute(
                "Scoring news via Claude API",
                self._score_batch,
                news_items, scan_type,
            )

            result["scored"] = scored
            result["market_context"] = market_ctx
            self._last_scored_count = len(scored)
            self._total_scored += len(scored)

            # Step 2: Get token usage stats
            try:
                from ..news_scorer import get_token_usage
                usage = get_token_usage()
                result["tokens_used"] = usage.get("total_input", 0) + usage.get("total_output", 0)
                self._total_tokens_used = result["tokens_used"]
            except Exception:
                pass

            # Step 3: Log detailed scoring results
            if scored:
                top_scores = sorted(scored, key=lambda s: s.total_score, reverse=True)[:5]
                self.log("Top scored news", {
                    "total_scored": len(scored),
                    "top_5": [
                        {
                            "score": round(s.total_score, 1),
                            "direction": s.direction.value,
                            "category": s.news_category,
                            "ticker": s.impacted_tickers[0] if s.impacted_tickers else "?",
                            "headline": s.news.title[:80],
                            "delay": s.transmission_delay,
                            "awareness": s.market_awareness,
                            "reliability": s.signal_reliability,
                        }
                        for s in top_scores
                    ],
                })

                # Count zero-edge filtered
                from ..news_scorer import _is_zero_edge_headline
                ze_count = sum(1 for item in news_items if _is_zero_edge_headline(item.title))
                result["zero_edge_filtered"] = ze_count
                self._last_zero_edge_filtered = ze_count
                if ze_count:
                    self.log("Zero-edge headlines filtered", {"count": ze_count})

            # Step 4: Publish scored news to bus
            self.publish("news_scored", {
                "scan_type": scan_type.value if scan_type else None,
                "count": len(scored),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "top_score": round(scored[0].total_score, 1) if scored else 0,
            })

            self.log_decision("Scoring complete", {
                "scored": len(scored),
                "zero_edge_filtered": result["zero_edge_filtered"],
                "top_score": round(scored[0].total_score, 1) if scored else 0,
            })

            duration_ms = int((time.monotonic() - start) * 1000)
            self._set_status(AgentStatus.IDLE, f"Scored {len(scored)} items")
            return result

        except Exception as exc:
            self.log("Scoring failed", {"error": str(exc)}, level="ERROR")
            self._set_status(AgentStatus.ERROR, str(exc))
            raise

    # ── Private helpers ────────────────────────────────────────────

    def _score_batch(self, news_items, scan_type):
        from ..news_scorer import score_news_batch
        return score_news_batch(news_items, scan_type)

    def get_metrics(self) -> dict:
        return {
            "last_scored_count": self._last_scored_count,
            "zero_edge_filtered": self._last_zero_edge_filtered,
            "total_scored": self._total_scored,
            "total_tokens_used": self._total_tokens_used,
            "cache_hits": self._cache_hits,
        }
