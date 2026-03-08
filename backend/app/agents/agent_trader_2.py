"""Agent Trader 2 — Spéculateur tendance, 15+ ans sur les commodities.

Stratégie :
- Trend following sur 4 actifs : cuivre (HG=F), cacao (CC=F), café (KC=F), blé (ZW=F)
- Toujours en position (LONG ou SHORT) sur chaque actif — jamais flat
- Change de position quand la tendance de fond s'inverse
- Se base principalement sur les NEWS pour les changements de tendance
- Utilise les indicateurs techniques (volumes, moyennes mobiles) comme confirmation
- N'est PAS un trader d'indicateurs — les news ont le rôle clé

Architecture :
- Consomme les news scorées de l'Agent Scoring (comme Trader 1)
- Mais ne trade PAS au sens intraday — il gère des POSITIONS de tendance
- Chaque actif a une position persistante (LONG ou SHORT) avec un raisonnement
- À chaque scan, évalue si les news justifient un changement de direction
- Track la performance (P&L virtuel) de chaque position

Différences avec Trader 1 :
- Trader 1 : day trading, entry/exit intraday, TP/SL
- Trader 2 : trend following, position indéfinie, changement de direction
"""

import json
import logging
import os
import time
import fcntl
from datetime import datetime, timezone
from pathlib import Path

from .base import BaseAgent, AgentStatus

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────

# Les 4 actifs suivis par Trader 2
TREND_TICKERS = {
    "HG=F": {"name": "Cuivre (Copper)", "category": "commodities_industrial"},
    "CC=F": {"name": "Cacao", "category": "commodities_soft"},
    "KC=F": {"name": "Café (Coffee)", "category": "commodities_soft"},
    "ZW=F": {"name": "Blé (Wheat)", "category": "commodities_agri"},
}

# News categories pertinentes pour les commodities
RELEVANT_CATEGORIES = {
    "commodity", "weather", "supply_chain", "geopolitical",
    "regulatory", "sector", "other",
}

# Seuil minimum de score pour considérer une news
MIN_NEWS_SCORE = 15.0

# Persistence file (JSON fallback)
POSITIONS_FILE = Path(os.getenv("DATA_DIR", "data")) / "trend_positions.json"


def _ensure_positions_file():
    """Create the positions file if it doesn't exist."""
    POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not POSITIONS_FILE.exists():
        POSITIONS_FILE.write_text("{}")


def _load_positions() -> dict:
    """Load trend positions from PG or JSON."""
    from ..database import is_pg_enabled

    if is_pg_enabled():
        return _pg_load_positions()

    _ensure_positions_file()
    try:
        with open(POSITIONS_FILE, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            data = json.load(f)
            fcntl.flock(f, fcntl.LOCK_UN)
            return data
    except (json.JSONDecodeError, FileNotFoundError):
        return {}


def _save_positions(positions: dict):
    """Save trend positions to PG or JSON."""
    from ..database import is_pg_enabled

    if is_pg_enabled():
        _pg_save_positions(positions)
        return

    _ensure_positions_file()
    with open(POSITIONS_FILE, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        json.dump(positions, f, indent=2, default=str)
        fcntl.flock(f, fcntl.LOCK_UN)


def _pg_load_positions() -> dict:
    """Load positions from PostgreSQL."""
    try:
        from ..database import get_conn
        import psycopg2.extras
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT ticker, data FROM trend_positions")
                rows = cur.fetchall()
                return {row["ticker"]: row["data"] for row in rows}
    except Exception as exc:
        logger.warning("PG load trend_positions failed: %s — fallback JSON", exc)
        return _load_positions_json_only()


def _load_positions_json_only() -> dict:
    """JSON-only load (fallback)."""
    _ensure_positions_file()
    try:
        with open(POSITIONS_FILE, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            data = json.load(f)
            fcntl.flock(f, fcntl.LOCK_UN)
            return data
    except (json.JSONDecodeError, FileNotFoundError):
        return {}


def _pg_save_positions(positions: dict):
    """Save positions to PostgreSQL (upsert per ticker)."""
    try:
        from ..database import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                for ticker, data in positions.items():
                    cur.execute("""
                        INSERT INTO trend_positions (ticker, data, updated_at)
                        VALUES (%s, %s, NOW())
                        ON CONFLICT (ticker) DO UPDATE SET data = %s, updated_at = NOW()
                    """, (ticker, json.dumps(data, default=str),
                          json.dumps(data, default=str)))
    except Exception as exc:
        logger.warning("PG save trend_positions failed: %s — fallback JSON", exc)
        _ensure_positions_file()
        with open(POSITIONS_FILE, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            json.dump(positions, f, indent=2, default=str)
            fcntl.flock(f, fcntl.LOCK_UN)


def _fetch_current_price(ticker: str) -> float | None:
    """Fetch the latest price for a ticker."""
    try:
        from ..market_data import fetch_price
        return fetch_price(ticker)
    except Exception:
        try:
            import yfinance as yf
            t = yf.Ticker(ticker)
            h = t.history(period="1d")
            if not h.empty:
                return float(h["Close"].iloc[-1])
        except Exception:
            pass
    return None


class AgentTrader2(BaseAgent):
    """Agent Trader 2 — Trend follower sur commodities.

    Gère des positions de tendance (LONG/SHORT) sur 4 actifs.
    Change de direction quand les news de fond l'exigent.
    """

    name = "trader_2"
    description = "Trend trading — spéculateur commodities long terme"

    def __init__(self):
        super().__init__()
        self._position_changes_total: int = 0
        self._last_change_ticker: str | None = None
        self._last_change_direction: str | None = None
        self._evaluations_today: int = 0

    def run(self, scored_news=None, scan_type=None, learning_data=None,
            trend_scoring=None, **kwargs) -> dict:
        """Évalue les news scorées et met à jour les positions de tendance.

        Contrairement à Trader 1, cet agent ne passe pas d'ordres.
        Il maintient une vue de position (LONG/SHORT) sur chaque actif
        et change de direction quand les news de fond le justifient.

        Args:
            scored_news: list[ScoredNews] from Agent Scoring
            scan_type: ScanType enum
            learning_data: dict from Agent Learning 2 (optional)

        Returns: dict with positions state and any changes made
        """
        self._set_status(AgentStatus.WORKING,
                         f"Evaluating trend signals ({len(scored_news or [])} news)")

        start = time.monotonic()
        self._current_learning = learning_data or {}
        self._current_trend_scoring = trend_scoring or {}

        try:
            # Load current positions
            positions = _load_positions()

            # Initialize positions for tickers not yet tracked
            for ticker, info in TREND_TICKERS.items():
                if ticker not in positions:
                    positions[ticker] = self._init_position(ticker, info)

            # Filter relevant news for our 4 tickers
            relevant_news = self._filter_relevant_news(scored_news or [])

            changes = []
            for ticker in TREND_TICKERS:
                ticker_news = relevant_news.get(ticker, [])
                if not ticker_news:
                    continue

                change = self.execute(
                    f"Evaluating {ticker}",
                    self._evaluate_ticker,
                    ticker, ticker_news, positions[ticker],
                )

                if change:
                    changes.append(change)
                    positions[ticker] = change["new_position"]
                    self._position_changes_total += 1
                    self._last_change_ticker = ticker
                    self._last_change_direction = change["new_direction"]

            # Update prices for performance tracking
            for ticker in TREND_TICKERS:
                price = _fetch_current_price(ticker)
                if price:
                    positions[ticker]["current_price"] = price
                    positions[ticker]["last_price_update"] = datetime.now(timezone.utc).isoformat()
                    # Update P&L
                    entry = positions[ticker].get("entry_price")
                    direction = positions[ticker].get("direction")
                    if entry and direction:
                        if direction == "LONG":
                            positions[ticker]["unrealized_pnl_pct"] = round(
                                (price - entry) / entry * 100, 2)
                        else:
                            positions[ticker]["unrealized_pnl_pct"] = round(
                                (entry - price) / entry * 100, 2)

            # Save
            _save_positions(positions)

            # Publish to bus
            self._evaluations_today += 1
            self.publish("trend_update", {
                "changes": changes,
                "positions_count": len(positions),
                "scan_type": scan_type.value if scan_type else None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            # Log changes
            if changes:
                for c in changes:
                    self.log_decision("POSITION CHANGED", {
                        "ticker": c["ticker"],
                        "from": c["old_direction"],
                        "to": c["new_direction"],
                        "reason": c["reason"],
                        "key_news": c.get("key_news", [])[:3],
                    })
            else:
                self.log("Trend evaluation complete — no changes", {
                    "news_evaluated": sum(len(v) for v in relevant_news.values()),
                    "tickers_with_news": len(relevant_news),
                })

            duration_ms = int((time.monotonic() - start) * 1000)
            action = (f"Changed: {', '.join(c['ticker'] for c in changes)}"
                      if changes else "No trend changes")
            self._set_status(AgentStatus.IDLE, action)

            return {
                "positions": positions,
                "changes": changes,
                "news_evaluated": sum(len(v) for v in relevant_news.values()),
                "duration_ms": duration_ms,
            }

        except Exception as exc:
            self.log("Trend evaluation failed", {"error": str(exc)}, level="ERROR")
            self._set_status(AgentStatus.ERROR, str(exc))
            raise

    def get_positions(self) -> dict:
        """Get current trend positions (for API/frontend)."""
        return _load_positions()

    def get_position_history(self, ticker: str) -> list:
        """Get position change history for a ticker."""
        positions = _load_positions()
        pos = positions.get(ticker, {})
        return pos.get("history", [])

    # ── Private helpers ──────────────────────────────────────────

    def _init_position(self, ticker: str, info: dict) -> dict:
        """Initialize a new position entry (starts NEUTRAL until first evaluation)."""
        price = _fetch_current_price(ticker)
        return {
            "ticker": ticker,
            "name": info["name"],
            "category": info["category"],
            "direction": "NEUTRAL",  # Will be set on first meaningful evaluation
            "entry_price": price,
            "current_price": price,
            "unrealized_pnl_pct": 0.0,
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "last_evaluation": None,
            "last_price_update": datetime.now(timezone.utc).isoformat(),
            "reasoning": "Position initiale — en attente du premier signal de tendance",
            "key_catalysts": [],
            "confidence": 0,
            "history": [],
            "total_switches": 0,
            "realized_pnl_pct": 0.0,  # Cumulative P&L of closed positions
        }

    def _filter_relevant_news(self, scored_news: list) -> dict:
        """Filter scored news relevant to our 4 tickers.

        Returns dict keyed by ticker with list of relevant ScoredNews.
        """
        result: dict[str, list] = {}

        for sn in scored_news:
            # Skip low-score or irrelevant categories
            if sn.total_score < MIN_NEWS_SCORE:
                continue
            if sn.news_category not in RELEVANT_CATEGORIES:
                continue
            if sn.direction.value == "NEUTRAL":
                continue

            # Check if any of our tickers are impacted
            for ticker in TREND_TICKERS:
                if ticker in sn.impacted_tickers:
                    result.setdefault(ticker, []).append(sn)

            # Also check chain reactions
            for cr in (sn.chain_reactions or []):
                if cr.ticker in TREND_TICKERS:
                    result.setdefault(cr.ticker, []).append(sn)

        return result

    def _evaluate_ticker(self, ticker: str, news_list: list,
                         current_position: dict) -> dict | None:
        """Evaluate whether the trend for a ticker should change.

        Logic:
        1. Score the directional signal from all relevant news
        2. Apply Learning 2 adjustments (per-ticker, per-newscat, per-direction)
        3. Check if signal contradicts current position direction
        4. If strong enough contradiction → change direction
        5. If confirmation → reinforce confidence, update reasoning

        Returns change dict or None if no change.
        """
        current_dir = current_position.get("direction", "NEUTRAL")

        # Load learning adjustments
        learning = getattr(self, "_current_learning", {})
        ticker_adj = learning.get("ticker_adj", {})
        newscat_adj = learning.get("newscat_adj", {})
        newscat_ticker_adj = learning.get("newscat_ticker_adj", {})  # P7: cross-dimension
        direction_adj = learning.get("direction_adj", {})
        signal_cal = learning.get("signal_calibration", {})

        # Use Scoring 2 accumulation if available (pre-computed trend scores)
        trend_scoring = getattr(self, "_current_trend_scoring", {})
        trend_accumulation = trend_scoring.get("accumulation", {}).get(ticker, {})
        use_trend_scoring = bool(trend_accumulation and
                                  (trend_accumulation.get("long", 0) > 0 or
                                   trend_accumulation.get("short", 0) > 0))

        # Aggregate directional signal from news
        long_score = 0.0
        short_score = 0.0
        key_news = []

        if use_trend_scoring:
            # Scoring 2 already computed trend-weighted accumulation
            # Apply learning adjustments on top
            long_score = trend_accumulation["long"]
            short_score = trend_accumulation["short"]
            # Apply per-ticker learning
            ticker_mult = ticker_adj.get(ticker, 1.0)
            long_score *= ticker_mult
            short_score *= ticker_mult

        for sn in news_list:
            if not use_trend_scoring:
                # Fallback: compute signal from Scoring 1 raw scores
                score = sn.total_score
                weight = score * (sn.signal_reliability / 100) * (sn.directional_clarity / 100)

                # Apply learning 2 adjustments
                weight *= ticker_adj.get(ticker, 1.0)
                cross_key = f"{sn.news_category}+{ticker}"
                if cross_key in newscat_ticker_adj:
                    weight *= newscat_ticker_adj[cross_key]
                else:
                    weight *= newscat_adj.get(sn.news_category, 1.0)

                # Check direct impact
                direct = ticker in sn.impacted_tickers
                chain_dir = None
                for cr in (sn.chain_reactions or []):
                    if cr.ticker == ticker:
                        chain_dir = cr.direction.value

                if direct:
                    if sn.direction.value == "LONG":
                        long_score += weight
                    elif sn.direction.value == "SHORT":
                        short_score += weight
                elif chain_dir:
                    if chain_dir == "LONG":
                        long_score += weight * 0.7
                    elif chain_dir == "SHORT":
                        short_score += weight * 0.7
            else:
                direct = ticker in sn.impacted_tickers

            # Always collect key_news for logging
            key_news.append({
                "title": sn.news.title[:120],
                "score": round(sn.total_score, 1),
                "direction": sn.direction.value,
                "category": sn.news_category,
                "reliability": sn.signal_reliability,
                "direct": direct if not use_trend_scoring or ticker in sn.impacted_tickers else False,
            })

        # Apply direction adjustment to scores
        long_score *= direction_adj.get("LONG", 1.0)
        short_score *= direction_adj.get("SHORT", 1.0)

        # Determine suggested direction
        net_signal = long_score - short_score
        signal_strength = abs(net_signal)
        suggested_dir = "LONG" if net_signal > 0 else "SHORT" if net_signal < 0 else current_dir

        now = datetime.now(timezone.utc).isoformat()
        current_position["last_evaluation"] = now

        # Decision: should we change?
        if current_dir == "NEUTRAL":
            # First position — take it if any signal
            if signal_strength > 5:
                return self._make_change(
                    ticker, current_position, suggested_dir,
                    f"Position initiale {suggested_dir} — signal net {net_signal:.1f}",
                    key_news, signal_strength,
                )
            return None

        if suggested_dir == current_dir:
            # Confirmation — just update confidence and reasoning
            if signal_strength > 10:
                current_position["confidence"] = min(100,
                    current_position.get("confidence", 50) + int(signal_strength / 5))
                best = max(key_news, key=lambda n: n["score"]) if key_news else None
                if best:
                    catalysts = current_position.get("key_catalysts", [])
                    catalysts.insert(0, {
                        "time": now,
                        "title": best["title"],
                        "score": best["score"],
                        "category": best["category"],
                    })
                    current_position["key_catalysts"] = catalysts[:10]
                current_position["reasoning"] = (
                    f"Tendance {current_dir} confirmée — signal net {net_signal:.1f} "
                    f"({len(news_list)} news)"
                )
            return None

        # Contradiction — should we flip?
        # Need stronger signal to change than to confirm
        # Base threshold adjusted by Learning 2 signal calibration
        change_threshold = 20.0 * signal_cal.get("threshold_adj", 1.0)
        if signal_strength >= change_threshold:
            return self._make_change(
                ticker, current_position, suggested_dir,
                f"Renversement {current_dir} → {suggested_dir} — "
                f"signal net {net_signal:.1f} (seuil {change_threshold})",
                key_news, signal_strength,
            )

        # Contradiction but not strong enough
        # Reduce confidence
        current_position["confidence"] = max(0,
            current_position.get("confidence", 50) - int(signal_strength / 3))
        current_position["reasoning"] = (
            f"Signal contraire détecté ({suggested_dir}, force {signal_strength:.1f}) "
            f"mais insuffisant pour renverser (seuil {change_threshold}). "
            f"Maintien {current_dir} avec confiance réduite."
        )
        return None

    def _make_change(self, ticker: str, position: dict, new_dir: str,
                     reason: str, key_news: list, strength: float) -> dict:
        """Create a position change record and update position state."""
        old_dir = position.get("direction", "NEUTRAL")
        old_entry = position.get("entry_price")
        current_price = position.get("current_price") or _fetch_current_price(ticker)

        # Calculate realized P&L of closing position
        close_pnl = 0.0
        if old_dir != "NEUTRAL" and old_entry and current_price:
            if old_dir == "LONG":
                close_pnl = round((current_price - old_entry) / old_entry * 100, 2)
            else:
                close_pnl = round((old_entry - current_price) / old_entry * 100, 2)

        # Record in history
        history_entry = {
            "time": datetime.now(timezone.utc).isoformat(),
            "from_direction": old_dir,
            "to_direction": new_dir,
            "reason": reason,
            "entry_price": old_entry,
            "exit_price": current_price,
            "pnl_pct": close_pnl,
            "signal_strength": round(strength, 1),
            "key_news": key_news[:5],
            # Store position entry_time for Journal 2 MAE/MFE (P4 audit fix)
            "position_entry_time": position.get("entry_time"),
        }

        history = position.get("history", [])
        history.insert(0, history_entry)
        # Keep last 50 changes
        history = history[:50]

        # Build new position
        new_position = {
            "ticker": ticker,
            "name": position.get("name", TREND_TICKERS.get(ticker, {}).get("name", ticker)),
            "category": position.get("category", TREND_TICKERS.get(ticker, {}).get("category", "")),
            "direction": new_dir,
            "entry_price": current_price,
            "current_price": current_price,
            "unrealized_pnl_pct": 0.0,
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "last_evaluation": datetime.now(timezone.utc).isoformat(),
            "last_price_update": datetime.now(timezone.utc).isoformat(),
            "reasoning": reason,
            "key_catalysts": key_news[:5],
            "confidence": min(100, int(strength * 2)),
            "history": history,
            "total_switches": position.get("total_switches", 0) + 1,
            "realized_pnl_pct": round(
                position.get("realized_pnl_pct", 0.0) + close_pnl, 2),
        }

        return {
            "ticker": ticker,
            "old_direction": old_dir,
            "new_direction": new_dir,
            "reason": reason,
            "close_pnl": close_pnl,
            "key_news": key_news[:3],
            "new_position": new_position,
        }

    def get_metrics(self) -> dict:
        """Metrics for frontend overview."""
        positions = _load_positions()

        active_long = sum(1 for p in positions.values() if p.get("direction") == "LONG")
        active_short = sum(1 for p in positions.values() if p.get("direction") == "SHORT")
        total_unrealized = sum(p.get("unrealized_pnl_pct", 0) for p in positions.values())
        total_realized = sum(p.get("realized_pnl_pct", 0) for p in positions.values())

        return {
            "tickers_tracked": len(TREND_TICKERS),
            "positions_long": active_long,
            "positions_short": active_short,
            "total_unrealized_pnl": round(total_unrealized, 2),
            "total_realized_pnl": round(total_realized, 2),
            "position_changes_total": self._position_changes_total,
            "evaluations_today": self._evaluations_today,
            "last_change_ticker": self._last_change_ticker,
            "last_change_direction": self._last_change_direction,
        }

    def reset_daily_counters(self):
        """Reset daily counters (called by scheduler at midnight)."""
        self._evaluations_today = 0
