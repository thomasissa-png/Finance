"""Agent Journal — Documentation et analyse business de chaque trade.

Responsabilités :
- Documente immédiatement le contexte d'entrée de chaque trade
- À 22h CET : ferme toutes les positions PENDING
- Récupère les prix réels (15min/1h bars via Twelve Data/yfinance)
- Calcule P&L, MAE/MFE, slippage, realized R/R
- Analyse la qualité de chaque décision
- Publie les résultats sur le bus → Agent Learning les consomme
- Prune automatiquement les entries > 1 an

Expertise incarnée :
- Analyse business rigoureuse de chaque trade
- Identification des patterns de succès/échec
- Documentation exhaustive pour le machine learning
- Détection des anomalies de prix (splits, erreurs data)
"""

import time
from datetime import datetime, timezone

from .base import BaseAgent, AgentStatus


class AgentJournal(BaseAgent):
    name = "journal"
    description = "Documentation & analyse de chaque trade"
    version = "4.1"  # v4.1: MAE/MFE, slippage, 15min bars, pruning, global timeout

    def __init__(self):
        super().__init__()
        self._last_run_trades_closed: int = 0
        self._last_run_tp: int = 0
        self._last_run_sl: int = 0
        self._last_run_expired: int = 0
        self._total_entries: int = 0
        self._last_pnl_sum: float = 0.0

    def run(self, **kwargs) -> dict:
        """Run the daily journal — close all PENDING trades, compute P&L.

        Called at 22h CET by the scheduler, or manually via trigger.
        Returns dict with journal results.
        """
        self._set_status(AgentStatus.WORKING, "Running daily journal")

        start = time.monotonic()
        result = {
            "entries": [],
            "diagnostic": {},
            "trades_closed": 0,
            "tp_hit": 0,
            "sl_hit": 0,
            "expired": 0,
            "total_pnl": 0.0,
        }

        try:
            # Step 1: Run the journal
            self.log("Starting daily journal run")

            journal_result = self.execute(
                "Closing pending trades & computing P&L",
                self._run_journal,
            )

            # P1 fix: run_daily_journal() returns list[dict], not {"entries": [...]}
            if isinstance(journal_result, list):
                entries = journal_result
                diagnostic = {}
            else:
                entries = journal_result.get("entries", [])
                diagnostic = journal_result.get("diagnostic", {})

            result["entries"] = entries
            result["diagnostic"] = diagnostic
            result["trades_closed"] = len(entries)
            self._last_run_trades_closed = len(entries)

            # Step 2: Analyze results
            tp_count = sum(1 for e in entries if e.get("result") == "TP_HIT")
            sl_count = sum(1 for e in entries if e.get("result") == "SL_HIT")
            exp_count = sum(1 for e in entries if e.get("result") == "EXPIRED")
            total_pnl = sum(e.get("pnl_pct", 0) or 0 for e in entries)

            result["tp_hit"] = tp_count
            result["sl_hit"] = sl_count
            result["expired"] = exp_count
            result["total_pnl"] = round(total_pnl, 4)

            self._last_run_tp = tp_count
            self._last_run_sl = sl_count
            self._last_run_expired = exp_count
            self._last_pnl_sum = total_pnl
            self._total_entries += len(entries)

            # Step 3: Log each closed trade
            for entry in entries:
                self.log_decision("Trade closed", {
                    "ticker": entry.get("ticker"),
                    "direction": entry.get("direction"),
                    "result": entry.get("result"),
                    "pnl_pct": entry.get("pnl_pct"),
                    "entry_price": entry.get("entry_price"),
                    "exit_price": entry.get("exit_price"),
                    "mae": entry.get("mae"),
                    "mfe": entry.get("mfe"),
                    "slippage": entry.get("slippage"),
                    "bar_coverage": entry.get("bar_coverage"),
                    "realized_rr": entry.get("realized_rr"),
                })

            # Step 4: Summary log
            if entries:
                self.log_decision("Journal run complete", {
                    "trades_closed": len(entries),
                    "tp_hit": tp_count,
                    "sl_hit": sl_count,
                    "expired": exp_count,
                    "total_pnl_pct": round(total_pnl, 4),
                    "win_rate": round(tp_count / len(entries) * 100, 1) if entries else 0,
                })
            else:
                self.log("Journal run: no trades to close")

            # Step 5: Publish results for Agent Learning
            self.publish("journal_complete", {
                "trades_closed": len(entries),
                "tp_hit": tp_count,
                "sl_hit": sl_count,
                "expired": exp_count,
                "total_pnl_pct": round(total_pnl, 4),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            duration_ms = int((time.monotonic() - start) * 1000)
            self._set_status(AgentStatus.IDLE,
                             f"Closed {len(entries)} trades (PnL: {total_pnl:+.2f}%)")

            return result

        except Exception as exc:
            self.log("Journal run failed", {"error": str(exc)}, level="ERROR")
            self._set_status(AgentStatus.ERROR, str(exc))
            raise

    def recover_pending(self) -> dict:
        """Startup recovery — close old PENDING trades from previous days."""
        self._set_status(AgentStatus.WORKING, "Recovering pending trades")
        try:
            self.log("Startup recovery: checking for old PENDING trades")
            result = self._run_journal()
            # P1 fix: run_daily_journal() returns list[dict]
            entries = result if isinstance(result, list) else result.get("entries", [])
            if entries:
                self.log_decision("Startup recovery complete", {
                    "recovered": len(entries),
                })
            self._set_status(AgentStatus.IDLE, f"Recovered {len(entries)} trades")
            return {"entries": entries, "diagnostic": {}}
        except Exception as exc:
            self.log("Startup recovery failed", {"error": str(exc)}, level="ERROR")
            self._set_status(AgentStatus.ERROR, str(exc))
            return {"entries": [], "diagnostic": {}}

    # ── Private helpers ────────────────────────────────────────────

    def _run_journal(self):
        from ..journal import run_daily_journal
        return run_daily_journal()

    def get_metrics(self) -> dict:
        return {
            "last_trades_closed": self._last_run_trades_closed,
            "last_tp": self._last_run_tp,
            "last_sl": self._last_run_sl,
            "last_expired": self._last_run_expired,
            "last_pnl_sum": round(self._last_pnl_sum, 4),
            "total_entries": self._total_entries,
        }
