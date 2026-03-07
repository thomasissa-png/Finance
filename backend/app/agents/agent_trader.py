"""Agent Trader 1 — Expert en spéculation, 15+ ans de news trading.

Responsabilités :
- Consomme les news scorées publiées par l'Agent Scoring
- Applique les learning multipliers (reçus de l'Agent Learning)
- Vérifie le calendrier économique, corrélations, cooldowns, régime VIX
- Calibre le R/R (ATR convexe, spread filter, magnitude scaling)
- Prend la décision finale d'investir ou non
- Documente le raisonnement complet de chaque décision
- Monitore les positions ouvertes (trailing stop, time stop)

Expertise incarnée :
- Ratios de succès brillants grâce à une sélection ultra-rigoureuse
- Ne trade que les dislocations — jamais les consensus
- Gestion du risque asymétrique (stops plus larges sur SHORT)
- Position sizing dynamique (VIX regime, vendredi, conviction)

Architecture multi-trader :
- Ce fichier est agent_trader.py (Trader 1)
- Le registry supporte N traders : agent_trader_2.py, agent_trader_3.py...
- Chaque trader a sa propre logique de sélection mais partage le bus
"""

import time
from datetime import datetime, timezone

from .base import BaseAgent, AgentStatus


class AgentTrader(BaseAgent):
    name = "trader_1"
    description = "Décision d'investissement — news trading expert"

    def __init__(self):
        super().__init__()
        self._trades_today: int = 0
        self._trades_total: int = 0
        self._rejections_today: int = 0
        self._last_trade_ticker: str | None = None
        self._last_trade_direction: str | None = None
        self._pending_positions: int = 0

    def run(self, scored_news, scan_type, learning_data,
            existing_trade_ticker=None, market_context=None, **kwargs) -> dict:
        """Evaluate scored news and decide whether to trade.

        Args:
            scored_news: List of ScoredNews from Agent Scoring
            scan_type: ScanType enum
            learning_data: Learning adjustments from Agent Learning
            existing_trade_ticker: Tickers already traded today
            market_context: VIX, indices, etc.

        Returns dict with trade decision.
        """
        self._set_status(AgentStatus.WORKING,
                         f"Evaluating {len(scored_news)} candidates")

        start = time.monotonic()

        try:
            # Log candidates
            self.log("Evaluating trade candidates", {
                "candidates": len(scored_news),
                "scan_type": scan_type.value if scan_type else None,
                "existing_tickers": existing_trade_ticker,
                "learning_dims": len(learning_data.get("adjustments", {})) if isinstance(learning_data, dict) else 0,
            })

            # Step 1: Select trade
            result = self.execute(
                "Selecting best trade",
                self._select_trade,
                scored_news, scan_type, learning_data,
                existing_trade_ticker, market_context,
            )

            result_dict = result.model_dump(mode="json")

            # Step 2: Log the decision
            if result.has_trade and result.recommendations:
                for rec in result.recommendations:
                    self._trades_today += 1
                    self._trades_total += 1
                    self._last_trade_ticker = rec.ticker
                    self._last_trade_direction = rec.direction

                    self.log_decision("TRADE SELECTED", {
                        "ticker": rec.ticker,
                        "asset": rec.asset_name,
                        "direction": rec.direction,
                        "entry_price": rec.entry_price,
                        "target_pct": round(rec.target_pct, 3),
                        "stop_pct": round(rec.stop_pct, 3),
                        "risk_reward": round(rec.risk_reward, 2),
                        "confidence": rec.confidence,
                        "score": round(rec.score, 1),
                        "news_headline": rec.news_headline[:100] if rec.news_headline else None,
                        "news_category": rec.news_category,
                        "learning_multiplier": round(rec.learning_multiplier, 3) if rec.learning_multiplier else None,
                    })

                    # Save trade
                    self._save_trade(rec)

                self.publish("trade_executed", {
                    "count": len(result.recommendations),
                    "trades": [
                        {
                            "ticker": r.ticker,
                            "direction": r.direction,
                            "score": round(r.score, 1),
                        }
                        for r in result.recommendations
                    ],
                    "scan_type": scan_type.value if scan_type else None,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

            else:
                self._rejections_today += 1
                reason = result.reason_no_trade or "unknown"

                self.log("No trade selected", {
                    "reason": reason,
                    "candidates_evaluated": len(scored_news),
                    "rejection_log": result_dict.get("rejection_log", [])[:5],
                })

                self.publish("no_trade", {
                    "reason": reason,
                    "scan_type": scan_type.value if scan_type else None,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

            duration_ms = int((time.monotonic() - start) * 1000)
            action = (f"Trade: {self._last_trade_direction} {self._last_trade_ticker}"
                      if result.has_trade else f"No trade: {result.reason_no_trade}")
            self._set_status(AgentStatus.IDLE, action)

            return result_dict

        except Exception as exc:
            self.log("Trade selection failed", {"error": str(exc)}, level="ERROR")
            self._set_status(AgentStatus.ERROR, str(exc))
            raise

    def run_position_monitor(self) -> dict:
        """Monitor open positions — trailing stops, time stops."""
        self._set_status(AgentStatus.WORKING, "Monitoring positions")
        try:
            from ..position_monitor import monitor_positions
            result = monitor_positions()
            active = result.get("active_positions", 0) if result else 0
            self._pending_positions = active

            if result and result.get("actions_taken"):
                self.log_decision("Position monitor actions", {
                    "actions": result["actions_taken"],
                    "active_positions": active,
                })

            self._set_status(AgentStatus.IDLE, f"{active} active positions")
            return result or {}
        except Exception as exc:
            self.log("Position monitor failed", {"error": str(exc)}, level="ERROR")
            self._set_status(AgentStatus.ERROR, str(exc))
            return {}

    def reset_daily_counters(self):
        """Reset daily counters (called by scheduler at midnight)."""
        self._trades_today = 0
        self._rejections_today = 0

    # ── Private helpers ────────────────────────────────────────────

    def _select_trade(self, scored, scan_type, learning_data,
                      existing_trade_ticker, market_context):
        from ..trade_selector import select_trade
        return select_trade(
            scored, scan_type, learning_data,
            existing_trade_ticker=existing_trade_ticker,
            market_context=market_context,
        )

    def _save_trade(self, rec):
        from ..learning import save_trade
        save_trade(rec)

    def get_metrics(self) -> dict:
        return {
            "trades_today": self._trades_today,
            "trades_total": self._trades_total,
            "rejections_today": self._rejections_today,
            "last_trade_ticker": self._last_trade_ticker,
            "last_trade_direction": self._last_trade_direction,
            "pending_positions": self._pending_positions,
        }
