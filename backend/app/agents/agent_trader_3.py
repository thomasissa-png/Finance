"""Agent Trader 3 — Technical Indicators Trading (Team 3).

Strategy:
- Trades on technical indicator signals (NOT news-based)
- 5 strategy combinations tested via A/B tracking:
  1. rsi_reversal: RSI oversold/overbought with confirmation
  2. macd_crossover: MACD signal cross with trend filter
  3. bollinger_squeeze: Low volatility breakout
  4. ma_trend: Moving average alignment
  5. momentum_divergence: Price/indicator divergence
- Holding period: hours to 3 days max
- Can hold multiple simultaneous positions (no 1-trade-per-scan limit)
- Position management: entry, target, stop, trailing stop
- Tracks realized P&L per strategy for A/B comparison

Differences with Trader 1/2:
- Trader 1: news-based day trading, 0-1 trade per scan, TP/SL intraday
- Trader 2: news-based trend following on 4 commodities, positions last weeks
- Trader 3: indicator-based, multi-position, multi-strategy, A/B testing

Architecture:
- Consumes scored setups from Agent Scoring 3
- Consumes Learning 3 adjustments (per strategy, per ticker, per timeframe)
- Manages positions with entry/target/stop/trailing
- Persistence: PG table "tech_positions" + JSON fallback
"""

import json
import logging
import os
import time
import fcntl
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .base import BaseAgent, AgentStatus

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────

# Maximum simultaneous positions
MAX_POSITIONS = 10

# Max holding period in days
MAX_HOLDING_DAYS = 3

# Minimum score to take a trade
MIN_TRADE_SCORE = 45.0

# Maximum positions per ticker
MAX_PER_TICKER = 2

# Maximum positions per strategy
MAX_PER_STRATEGY = 4

# Timeout constants
GLOBAL_TIMEOUT_S = 120
PER_FETCH_TIMEOUT_S = 15

# History pruning
HISTORY_MAX_AGE_DAYS = 365

# Persistence file (JSON fallback)
POSITIONS_FILE = Path(os.getenv("DATA_DIR", "data")) / "tech_positions.json"


def _ensure_positions_file():
    """Create the positions file if it doesn't exist."""
    POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not POSITIONS_FILE.exists():
        POSITIONS_FILE.write_text('{"active": [], "closed": []}')


def _load_positions() -> dict:
    """Load tech positions from PG or JSON."""
    from ..database import is_pg_enabled

    if is_pg_enabled():
        return _pg_load_positions()

    _ensure_positions_file()
    try:
        with open(POSITIONS_FILE, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            data = json.load(f)
            fcntl.flock(f, fcntl.LOCK_UN)
            if not isinstance(data, dict):
                return {"active": [], "closed": []}
            return data
    except (json.JSONDecodeError, FileNotFoundError):
        return {"active": [], "closed": []}


def _save_positions(positions: dict):
    """Save tech positions to PG or JSON."""
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
                cur.execute("SELECT data FROM tech_positions WHERE ticker = '_state_'")
                row = cur.fetchone()
                if row:
                    return row["data"] if isinstance(row["data"], dict) else json.loads(row["data"])
                return {"active": [], "closed": []}
    except Exception as exc:
        logger.warning("PG load tech_positions failed: %s — fallback JSON", exc)
        return _load_positions_json_only()


def _load_positions_json_only() -> dict:
    """JSON-only load (fallback)."""
    _ensure_positions_file()
    try:
        with open(POSITIONS_FILE, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            data = json.load(f)
            fcntl.flock(f, fcntl.LOCK_UN)
            if not isinstance(data, dict):
                return {"active": [], "closed": []}
            return data
    except (json.JSONDecodeError, FileNotFoundError):
        return {"active": [], "closed": []}


def _pg_save_positions(positions: dict):
    """Save positions to PostgreSQL (single row with full state)."""
    try:
        from ..database import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO tech_positions (ticker, data, updated_at)
                    VALUES ('_state_', %s, NOW())
                    ON CONFLICT (ticker) DO UPDATE SET data = %s, updated_at = NOW()
                """, (json.dumps(positions, default=str),
                      json.dumps(positions, default=str)))
    except Exception as exc:
        logger.warning("PG save tech_positions failed: %s — fallback JSON", exc)
        _ensure_positions_file()
        with open(POSITIONS_FILE, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            json.dump(positions, f, indent=2, default=str)
            fcntl.flock(f, fcntl.LOCK_UN)


def _fetch_current_price(ticker: str) -> float | None:
    """Fetch the latest price for a ticker."""
    try:
        from ..market_data import fetch_price
        price = fetch_price(ticker)
        if price is not None:
            return price
        logger.warning("fetch_price returned None for %s — trying yfinance", ticker)
    except Exception as exc:
        logger.warning("Twelve Data fetch failed for %s: %s — trying yfinance", ticker, exc)
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        h = t.history(period="1d")
        if not h.empty:
            return float(h["Close"].iloc[-1])
        logger.warning("yfinance returned empty for %s", ticker)
    except Exception as exc:
        logger.warning("yfinance fetch also failed for %s: %s", ticker, exc)
    return None


class AgentTrader3(BaseAgent):
    """Agent Trader 3 — Technical Indicators Trader.

    Manages multiple simultaneous positions based on technical setups.
    Tracks A/B performance per strategy for optimization.
    """

    name = "trader_3"
    description = "Technical trading — multi-strategy, A/B testing"
    version = "1.0"

    def __init__(self):
        super().__init__()
        self._trades_opened_total: int = 0
        self._trades_closed_total: int = 0
        self._evaluations_today: int = 0
        self._current_learning: dict = {}

    def run(self, tech_scoring=None, learning_data=None, **kwargs) -> dict:
        """Evaluate scored technical setups and manage positions.

        Args:
            tech_scoring: dict from Agent Scoring 3 (setups)
            learning_data: dict from Agent Learning 3 (adjustments)

        Returns: dict with positions state and changes.
        """
        self._set_status(AgentStatus.WORKING, "Evaluating technical setups")

        start = time.monotonic()
        self._current_learning = learning_data or {}

        try:
            # Load current state
            state = _load_positions()
            active = state.get("active", [])
            closed = state.get("closed", [])

            # Step 1: Monitor existing positions (check stops, targets, expiry)
            still_active, newly_closed = self._monitor_positions(active)
            closed.extend(newly_closed)
            self._trades_closed_total += len(newly_closed)

            # Step 2: Evaluate new setups from Scoring 3
            new_positions = []
            if tech_scoring:
                setups = tech_scoring.get("setups", [])
                new_positions = self._evaluate_setups(setups, still_active)
                self._trades_opened_total += len(new_positions)

            # Combine active positions
            all_active = still_active + new_positions

            # Step 3: Prune old closed positions
            cutoff = (datetime.now(timezone.utc) - timedelta(days=HISTORY_MAX_AGE_DAYS)).isoformat()
            closed = [c for c in closed if c.get("close_time", "") > cutoff]
            # Safety cap
            closed = closed[-500:]

            # Save
            state = {"active": all_active, "closed": closed}
            _save_positions(state)

            # Step 4: Publish
            self._evaluations_today += 1
            self.publish("tech_update", {
                "active_count": len(all_active),
                "new_opened": len(new_positions),
                "newly_closed": len(newly_closed),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            # Log
            if new_positions:
                for pos in new_positions:
                    self.log_decision("POSITION OPENED", {
                        "ticker": pos["ticker"],
                        "strategy": pos["strategy"],
                        "direction": pos["direction"],
                        "entry_price": pos["entry_price"],
                        "score": pos["score"],
                        "target_pct": pos["target_pct"],
                        "stop_pct": pos["stop_pct"],
                    })

            if newly_closed:
                for pos in newly_closed:
                    self.log_decision("POSITION CLOSED", {
                        "ticker": pos["ticker"],
                        "strategy": pos["strategy"],
                        "direction": pos["direction"],
                        "result": pos.get("result"),
                        "pnl_pct": pos.get("pnl_pct"),
                        "holding_hours": pos.get("holding_hours"),
                    })

            self.log("Tech trader evaluation complete", {
                "active": len(all_active),
                "new_opened": len(new_positions),
                "newly_closed": len(newly_closed),
                "setups_evaluated": len(tech_scoring.get("setups", [])) if tech_scoring else 0,
            })

            duration_ms = int((time.monotonic() - start) * 1000)
            self._set_status(
                AgentStatus.IDLE,
                f"{len(all_active)} active, +{len(new_positions)} opened, "
                f"-{len(newly_closed)} closed"
            )

            return {
                "active": all_active,
                "new_opened": new_positions,
                "newly_closed": newly_closed,
                "duration_ms": duration_ms,
            }

        except Exception as exc:
            self.log("Tech trader failed", {"error": str(exc)}, level="ERROR")
            self._set_status(AgentStatus.ERROR, str(exc))
            raise

    def _monitor_positions(self, active: list[dict]) -> tuple[list[dict], list[dict]]:
        """Monitor active positions: check TP, SL, expiry, trailing stop.

        Returns (still_active, newly_closed).
        """
        still_active = []
        newly_closed = []
        now = datetime.now(timezone.utc)

        # Fetch prices in parallel
        tickers = list({p["ticker"] for p in active})
        prices: dict[str, float | None] = {}

        if tickers:
            try:
                with ThreadPoolExecutor(max_workers=min(len(tickers), 5)) as executor:
                    futures = {
                        executor.submit(_fetch_current_price, t): t
                        for t in tickers
                    }
                    for future in as_completed(futures, timeout=PER_FETCH_TIMEOUT_S * 2):
                        ticker = futures[future]
                        try:
                            prices[ticker] = future.result(timeout=PER_FETCH_TIMEOUT_S)
                        except Exception:
                            prices[ticker] = None
            except Exception:
                for t in tickers:
                    if t not in prices:
                        prices[t] = _fetch_current_price(t)

        for pos in active:
            ticker = pos["ticker"]
            price = prices.get(ticker)
            entry_price = pos.get("entry_price", 0)
            direction = pos.get("direction", "LONG")

            if not price or not entry_price or entry_price <= 0:
                still_active.append(pos)
                continue

            # Update current price and watermarks
            pos["current_price"] = price
            pos["high_watermark"] = max(price, pos.get("high_watermark", price))
            pos["low_watermark"] = min(price, pos.get("low_watermark", price))

            # Compute unrealized P&L
            if direction == "LONG":
                pnl_pct = (price - entry_price) / entry_price * 100
            else:
                pnl_pct = (entry_price - price) / entry_price * 100
            pos["unrealized_pnl_pct"] = round(pnl_pct, 2)

            # Check expiry (max holding days)
            entry_time = pos.get("entry_time", "")
            try:
                entry_dt = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
                holding_hours = (now - entry_dt).total_seconds() / 3600
            except (ValueError, AttributeError, TypeError):
                holding_hours = 0

            # Check target hit
            target_pct = pos.get("target_pct", 0)
            stop_pct = pos.get("stop_pct", 0)

            if target_pct > 0 and pnl_pct >= target_pct:
                pos["result"] = "TP_HIT"
                pos["pnl_pct"] = round(pnl_pct, 2)
                pos["close_time"] = now.isoformat()
                pos["close_price"] = price
                pos["holding_hours"] = round(holding_hours, 1)
                newly_closed.append(pos)
                continue

            # Check stop hit
            if stop_pct > 0 and pnl_pct <= -stop_pct:
                pos["result"] = "SL_HIT"
                pos["pnl_pct"] = round(pnl_pct, 2)
                pos["close_time"] = now.isoformat()
                pos["close_price"] = price
                pos["holding_hours"] = round(holding_hours, 1)
                newly_closed.append(pos)
                continue

            # Check max holding period
            if holding_hours > MAX_HOLDING_DAYS * 24:
                pos["result"] = "EXPIRED"
                pos["pnl_pct"] = round(pnl_pct, 2)
                pos["close_time"] = now.isoformat()
                pos["close_price"] = price
                pos["holding_hours"] = round(holding_hours, 1)
                newly_closed.append(pos)
                continue

            # Trailing stop: if P&L > 50% of target, tighten stop to breakeven
            if target_pct > 0 and pnl_pct > target_pct * 0.5:
                pos["trailing_active"] = True
                # Move stop to breakeven + small buffer
                pos["effective_stop"] = max(
                    pos.get("effective_stop", -stop_pct),
                    -0.1  # Breakeven with 0.1% buffer
                )

            still_active.append(pos)

        return still_active, newly_closed

    def _evaluate_setups(self, setups: list[dict],
                         current_active: list[dict]) -> list[dict]:
        """Evaluate scored setups and open new positions.

        Applies Learning 3 adjustments and position limits.
        """
        new_positions = []

        if len(current_active) >= MAX_POSITIONS:
            return new_positions

        # Count positions per ticker and strategy
        ticker_counts: dict[str, int] = {}
        strategy_counts: dict[str, int] = {}
        active_ticker_dirs: set[tuple[str, str]] = set()

        for pos in current_active:
            t = pos.get("ticker", "")
            s = pos.get("strategy", "")
            d = pos.get("direction", "")
            ticker_counts[t] = ticker_counts.get(t, 0) + 1
            strategy_counts[s] = strategy_counts.get(s, 0) + 1
            active_ticker_dirs.add((t, d))

        # Apply learning adjustments
        learning = self._current_learning
        strategy_adj = learning.get("strategy_adj", {})
        ticker_adj = learning.get("ticker_adj", {})
        timeframe_adj = learning.get("timeframe_adj", {})

        for setup in setups:
            if len(current_active) + len(new_positions) >= MAX_POSITIONS:
                break

            ticker = setup.get("ticker", "")
            strategy = setup.get("strategy", "")
            direction = setup.get("direction", "")
            score = setup.get("score", 0)
            timeframe = setup.get("timeframe", "1d")

            # Skip if already have same ticker+direction position
            if (ticker, direction) in active_ticker_dirs:
                continue

            # Position limits
            if ticker_counts.get(ticker, 0) >= MAX_PER_TICKER:
                continue
            if strategy_counts.get(strategy, 0) >= MAX_PER_STRATEGY:
                continue

            # Apply learning multipliers
            adj_score = score
            adj_score *= strategy_adj.get(strategy, 1.0)
            adj_score *= ticker_adj.get(ticker, 1.0)
            adj_score *= timeframe_adj.get(timeframe, 1.0)

            if adj_score < MIN_TRADE_SCORE:
                continue

            # Create position
            now_iso = datetime.now(timezone.utc).isoformat()
            position = {
                "ticker": ticker,
                "name": setup.get("name", ticker),
                "category": setup.get("category", ""),
                "strategy": strategy,
                "strategy_name": setup.get("strategy_name", strategy),
                "direction": direction,
                "score": round(adj_score, 1),
                "raw_score": round(score, 1),
                "learning_multiplier": round(
                    strategy_adj.get(strategy, 1.0)
                    * ticker_adj.get(ticker, 1.0)
                    * timeframe_adj.get(timeframe, 1.0), 3
                ),
                "confidence": setup.get("confidence", 0),
                "entry_price": setup.get("entry_price", 0),
                "current_price": setup.get("entry_price", 0),
                "target_pct": setup.get("target_pct", 0),
                "stop_pct": setup.get("stop_pct", 0),
                "effective_stop": -setup.get("stop_pct", 0),
                "trailing_active": False,
                "entry_time": now_iso,
                "timeframe": timeframe,
                "signals_at_entry": setup.get("signals", {}),
                "atr_at_entry": setup.get("atr"),
                "adx_at_entry": setup.get("adx"),
                "volume_ratio_at_entry": setup.get("volume_ratio", 1.0),
                "unrealized_pnl_pct": 0.0,
                "high_watermark": setup.get("entry_price", 0),
                "low_watermark": setup.get("entry_price", 0),
            }

            new_positions.append(position)
            active_ticker_dirs.add((ticker, direction))
            ticker_counts[ticker] = ticker_counts.get(ticker, 0) + 1
            strategy_counts[strategy] = strategy_counts.get(strategy, 0) + 1

        return new_positions

    def get_positions(self) -> dict:
        """Get current state (for API/frontend)."""
        return _load_positions()

    def get_active_positions(self) -> list[dict]:
        """Get active positions only."""
        state = _load_positions()
        return state.get("active", [])

    def get_closed_positions(self, limit: int = 50) -> list[dict]:
        """Get recently closed positions."""
        state = _load_positions()
        closed = state.get("closed", [])
        return closed[-limit:]

    def get_strategy_performance(self) -> dict:
        """Compute A/B performance per strategy from closed trades."""
        state = _load_positions()
        closed = state.get("closed", [])

        perf: dict[str, dict] = {}
        for trade in closed:
            strategy = trade.get("strategy", "unknown")
            if strategy not in perf:
                perf[strategy] = {
                    "trades": 0, "wins": 0, "total_pnl": 0.0,
                    "avg_holding_hours": 0.0, "total_holding": 0.0,
                }
            p = perf[strategy]
            p["trades"] += 1
            pnl = trade.get("pnl_pct", 0)
            p["total_pnl"] += pnl
            if pnl > 0:
                p["wins"] += 1
            p["total_holding"] += trade.get("holding_hours", 0)

        # Compute averages
        for strategy, p in perf.items():
            n = p["trades"]
            p["win_rate"] = round(p["wins"] / n * 100, 1) if n > 0 else 0
            p["avg_pnl"] = round(p["total_pnl"] / n, 2) if n > 0 else 0
            p["avg_holding_hours"] = round(p["total_holding"] / n, 1) if n > 0 else 0
            p["total_pnl"] = round(p["total_pnl"], 2)
            del p["total_holding"]

        return perf

    def get_metrics(self) -> dict:
        """Metrics for frontend overview."""
        state = _load_positions()
        active = state.get("active", [])
        closed = state.get("closed", [])

        total_unrealized = sum(p.get("unrealized_pnl_pct", 0) for p in active)
        total_realized = sum(p.get("pnl_pct", 0) for p in closed)

        # Strategy breakdown
        strategy_counts = {}
        for p in active:
            s = p.get("strategy", "")
            strategy_counts[s] = strategy_counts.get(s, 0) + 1

        return {
            "active_positions": len(active),
            "closed_positions": len(closed),
            "total_unrealized_pnl": round(total_unrealized, 2),
            "total_realized_pnl": round(total_realized, 2),
            "trades_opened_total": self._trades_opened_total,
            "trades_closed_total": self._trades_closed_total,
            "evaluations_today": self._evaluations_today,
            "strategy_counts": strategy_counts,
            "max_positions": MAX_POSITIONS,
        }

    def reset_daily_counters(self):
        """Reset daily counters (called by scheduler at midnight)."""
        self._evaluations_today = 0
