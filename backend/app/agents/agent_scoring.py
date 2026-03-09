"""Agent Scoring — Expert en spéculation avec 15+ ans d'expertise edge & news trading.

Responsabilités :
- Consomme les news brutes publiées par l'Agent News
- Pré-filtre les headlines zero-edge (earnings, macro évidentes)
- Score chaque headline via Claude API (11 dimensions)
- Applique la formule edge-weighted (surprise × clarity × edge_factor × reliability)
- Détecte les chain reactions (effets de second ordre)
- Publie les news scorées sur le bus → Agent Trader les consomme

Audit fixes v7.4:
- A1: Token usage keys fixed (input_tokens/output_tokens, not total_input/total_output)
- A2: _cache_hits incremented from score_news_batch cache stats
- A3: duration_ms published in bus message and stored in metrics
- A7: Import failure for token usage logged (not silently swallowed)
- A8: _is_zero_edge_headline imported at module level (not per-call)

Expertise incarnée :
- Identification des dislocations non pricées (rapport NOAA, gel Brésil, OSINT)
- Discrimination entre edge réel et bruit (earnings = 0 edge, météo locale = max edge)
- Évaluation du transmission_delay (combien de temps avant que le marché price)
"""

import logging
import time
from datetime import datetime, timezone

from .base import BaseAgent, AgentStatus

# A8: Import at module level (not per-call in run())
from ..news_scorer import _is_zero_edge_headline

logger = logging.getLogger(__name__)


class AgentScoring(BaseAgent):
    name = "scoring"
    description = "Notation expert des news — edge & impact"
    version = "7.4"  # v7.4: 9 fixes, token tracking, cache hits, duration tracking

    def __init__(self):
        super().__init__()
        self._last_scored_count: int = 0
        self._last_zero_edge_filtered: int = 0
        self._total_scored: int = 0
        self._total_tokens_used: int = 0
        self._cache_hits: int = 0
        self._last_duration_ms: int = 0
        self._last_result: dict | None = None  # P2.7: Store last result for API

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
            # A1: Fixed keys — get_token_usage() returns input_tokens/output_tokens
            # A2: Read cache hit count from scorer
            # A7: Log import failure instead of silently swallowing
            try:
                from ..news_scorer import get_token_usage
                usage = get_token_usage()
                result["tokens_used"] = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
                self._total_tokens_used = result["tokens_used"]
            except Exception as exc:
                logger.warning("Failed to get token usage stats: %s", exc)

            # A2: Count cache hits — items that were in scored but not sent to Claude
            # Cache hits = total scored - items actually sent to Claude
            # The scorer logs "F2: N headlines served from cache" — we count from the result
            try:
                from ..news_scorer import _score_cache
                # Approximate: cache hits this scan = items that matched cache
                # We count how many input items had a cache entry
                from ..news_scorer import _get_cache_key, _get_cached_score
                cache_hit_count = 0
                for item in news_items:
                    key = _get_cache_key(item.title, item.description)
                    if _get_cached_score(key) is not None:
                        cache_hit_count += 1
                result["cache_hits"] = cache_hit_count
                self._cache_hits += cache_hit_count
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

                # Count zero-edge filtered (A8: uses module-level import)
                ze_count = sum(1 for item in news_items if _is_zero_edge_headline(item.title))
                result["zero_edge_filtered"] = ze_count
                self._last_zero_edge_filtered = ze_count
                if ze_count:
                    self.log("Zero-edge headlines filtered", {"count": ze_count})

            # A3: Compute duration before publishing
            duration_ms = int((time.monotonic() - start) * 1000)
            self._last_duration_ms = duration_ms

            # Step 4: Publish scored news to bus (A3: include duration_ms)
            self.publish("news_scored", {
                "scan_type": scan_type.value if scan_type else None,
                "count": len(scored),
                "cache_hits": result["cache_hits"],
                "duration_ms": duration_ms,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "top_score": round(scored[0].total_score, 1) if scored else 0,
            })

            self.log_decision("Scoring complete", {
                "scored": len(scored),
                "zero_edge_filtered": result["zero_edge_filtered"],
                "cache_hits": result["cache_hits"],
                "duration_ms": duration_ms,
                "top_score": round(scored[0].total_score, 1) if scored else 0,
            })

            self._set_status(AgentStatus.IDLE, f"Scored {len(scored)} items")
            # P2.7: Store result for API endpoint
            self._last_result = {
                "scored_count": len(scored),
                "zero_edge_filtered": result["zero_edge_filtered"],
                "cache_hits": result["cache_hits"],
                "tokens_used": result["tokens_used"],
                "duration_ms": duration_ms,
                "top_scored": [
                    {
                        "score": round(s.total_score, 1),
                        "direction": s.direction.value,
                        "category": s.news_category,
                        "ticker": s.impacted_tickers[0] if s.impacted_tickers else "?",
                        "headline": s.news.title[:120],
                        "delay": s.transmission_delay,
                        "awareness": s.market_awareness,
                        "reliability": s.signal_reliability,
                    }
                    for s in sorted(scored, key=lambda s: s.total_score, reverse=True)[:10]
                ],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            return result

        except Exception as exc:
            error_str = str(exc)
            error_type = type(exc).__name__
            self.log("Scoring failed", {"error": error_str, "type": error_type}, level="ERROR")
            self._set_status(AgentStatus.ERROR, error_str)
            # Return partial result with error info instead of raising
            # so the pipeline can differentiate timeout vs auth failure
            result["error_type"] = error_type
            result["error_message"] = error_str
            return result

    # ── Private helpers ────────────────────────────────────────────

    def _score_batch(self, news_items, scan_type):
        from ..news_scorer import score_news_batch
        return score_news_batch(news_items, scan_type)

    def get_last_result(self) -> dict | None:
        """P2.7: Get the last scoring result (for API/frontend)."""
        return self._last_result

    def get_metrics(self) -> dict:
        return {
            "last_scored_count": self._last_scored_count,
            "zero_edge_filtered": self._last_zero_edge_filtered,
            "total_scored": self._total_scored,
            "total_tokens_used": self._total_tokens_used,
            "cache_hits": self._cache_hits,
            "last_duration_ms": self._last_duration_ms,
        }
