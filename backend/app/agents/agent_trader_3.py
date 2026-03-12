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
from ..market_data import validate_price

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

# Maximum positions per strategy (J4: base value, dynamic via weekly_config)
MAX_PER_STRATEGY_BASE = 4
MAX_PER_STRATEGY_VALIDATED = 6  # J4: validated strategies get more budget

# Timeout constants
GLOBAL_TIMEOUT_S = 120
PER_FETCH_TIMEOUT_S = 15

# History pruning
HISTORY_MAX_AGE_DAYS = 365

# P8: Correlation groups — prevent conflicting positions
TECH_CORRELATION_GROUPS = {
    "gold_silver": ["GC=F", "SI=F"],
    "energy": ["CL=F", "BZ=F"],
    "agri": ["ZC=F", "ZW=F"],
    "risk_eu": ["^FCHI", "^GDAXI"],
    "risk_us": ["^GSPC"],
    "aud_copper": ["AUDUSD=X", "HG=F"],  # Correlated via China
}

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

    # T3-P3: Atomic write — write to temp then rename (avoids truncate-before-lock race)
    _ensure_positions_file()
    import tempfile
    tmp_fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(POSITIONS_FILE), suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(positions, f, indent=2, default=str)
        os.replace(tmp_path, POSITIONS_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


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
        data_json = json.dumps(positions, default=str)
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO tech_positions (ticker, strategy, data, updated_at)
                    VALUES ('_state_', '', %s, NOW())
                    ON CONFLICT (ticker) DO UPDATE SET data = %s, updated_at = NOW()
                """, (data_json, data_json))
    except Exception as exc:
        logger.warning("PG save tech_positions failed: %s — fallback JSON", exc)
        # v8.4 fix C1-E3: Call JSON-only write directly to avoid infinite recursion.
        import tempfile
        _ensure_positions_file()
        tmp_fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(POSITIONS_FILE), suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w") as f:
                json.dump(positions, f, indent=2, default=str)
            os.replace(tmp_path, POSITIONS_FILE)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


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
    version = "2.3"  # v2.3: Progressive trailing stop (3 paliers), strategy-family R/R profiles

    def __init__(self):
        super().__init__()
        self._trades_opened_total: int = 0
        self._trades_closed_total: int = 0
        self._evaluations_today: int = 0
        self._current_learning: dict = {}
        self._weekly_config: dict | None = None  # J5: weekly strategy config
        # T3-P5: Load persisted weekly config on init (survives restart)
        self._load_weekly_config_from_disk()

    def run(self, tech_scoring=None, learning_data=None,
            weekly_config=None, **kwargs) -> dict:
        """Evaluate scored technical setups and manage positions.

        Args:
            tech_scoring: dict from Agent Scoring 3 (setups)
            learning_data: dict from Agent Learning 3 (adjustments)
            weekly_config: dict from Learning 3 (validated strategies, budgets)

        Returns: dict with positions state and changes.
        """
        if weekly_config is not None:
            self._weekly_config = weekly_config
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

            # Build result BEFORE publish/log — an exception in publish/log
            # must never prevent the result from being returned to the pipeline.
            duration_ms = int((time.monotonic() - start) * 1000)
            result = {
                "active": all_active,
                "new_opened": new_positions,
                "newly_closed": newly_closed,
                "duration_ms": duration_ms,
            }

            # Step 4: Publish + Log (best-effort, never blocks result)
            try:
                self._evaluations_today += 1
                self.publish("tech_update", {
                    "active_count": len(all_active),
                    "new_opened": len(new_positions),
                    "newly_closed": len(newly_closed),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

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
            except Exception as log_exc:
                logger.warning("Trader 3 publish/log failed (positions saved OK): %s", log_exc)

            self._set_status(
                AgentStatus.IDLE,
                f"{len(all_active)} active, +{len(new_positions)} opened, "
                f"-{len(newly_closed)} closed"
            )

            return result

        except Exception as exc:
            self.log("Tech trader failed", {"error": str(exc)}, level="ERROR")
            self._set_status(AgentStatus.ERROR, str(exc))
            raise

    def run_position_monitor(self) -> dict:
        """V1: Standalone position monitor — check TP/SL/trailing/expiry between scans.

        Called by scheduler every 15 min. Only monitors existing positions,
        does NOT evaluate new setups (no Scoring 3 data needed).

        CRITICAL fix: Always save after monitoring — trailing stop updates
        modify effective_stop in memory and must be persisted even when no
        position is closed. Without this, trailing stops revert to their
        original value at the next load cycle.
        """
        state = _load_positions()
        active = state.get("active", [])
        if not active:
            return {"active": 0, "closed": 0}

        still_active, newly_closed = self._monitor_positions(active)

        if newly_closed:
            self._trades_closed_total += len(newly_closed)
            for pos in newly_closed:
                self.log_decision("POSITION CLOSED (monitor)", {
                    "ticker": pos["ticker"],
                    "strategy": pos["strategy"],
                    "result": pos.get("result"),
                    "pnl_pct": pos.get("pnl_pct"),
                })

        # Always save — trailing stop updates modify effective_stop on still_active
        closed_history = state.get("closed", [])
        closed_history.extend(newly_closed)
        state = {"active": still_active, "closed": closed_history}
        _save_positions(state)

        return {"active": len(still_active), "closed": len(newly_closed)}

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

            # Validate price before updating position
            is_valid, reason = validate_price(ticker, price)
            if not is_valid:
                logger.warning("T3 monitor: rejected price for %s — %s", ticker, reason)
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
                # T3-P7: Default to large value to force expiry (not 0 which blocks it)
                holding_hours = MAX_HOLDING_DAYS * 24 + 1

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

            # J2/v2.3: Progressive trailing stop per-strategy — BEFORE SL check
            # so trailing-adjusted stop is used in SL evaluation.
            # v2.3: 3 paliers instead of single breakeven jump:
            #   Palier 1: PnL > activation_pct of target → stop = breakeven (-0.05%)
            #   Palier 2: PnL > 50% of target → stop = +25% of target
            #   Palier 3: PnL > 75% of target → stop = +50% of target
            strategy = pos.get("strategy", "")
            trailing_pct = self._get_trailing_threshold(strategy)
            if target_pct > 0 and pnl_pct > 0:
                progress = pnl_pct / target_pct  # How far towards target (0.0-1.0+)
                new_stop = None
                if progress >= 0.75:
                    # Palier 3: lock in 50% of target
                    new_stop = target_pct * 0.50
                    pos["trailing_active"] = True
                    pos["trailing_level"] = 3
                elif progress >= 0.50:
                    # Palier 2: lock in 25% of target
                    new_stop = target_pct * 0.25
                    pos["trailing_active"] = True
                    pos["trailing_level"] = 2
                elif progress >= trailing_pct:
                    # Palier 1: breakeven (original activation threshold)
                    new_stop = -0.05  # Near-breakeven with tiny buffer
                    pos["trailing_active"] = True
                    pos["trailing_level"] = 1

                if new_stop is not None:
                    # Only tighten, never loosen
                    pos["effective_stop"] = max(
                        pos.get("effective_stop", -stop_pct),
                        new_stop
                    )

            # Check stop hit (uses potentially trailed effective_stop)
            effective_stop = pos.get("effective_stop", -stop_pct)
            if stop_pct > 0 and pnl_pct <= effective_stop:
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

            still_active.append(pos)

        return still_active, newly_closed

    def _get_trailing_threshold(self, strategy: str) -> float:
        """J2: Get trailing stop activation threshold per strategy.

        Returns the fraction of target_pct at which trailing stop activates.
        Momentum strategies trail tighter, reversal strategies wider.
        """
        # Weekly config can override
        if self._weekly_config:
            overrides = self._weekly_config.get("trailing_thresholds", {})
            if strategy in overrides:
                return overrides[strategy]

        defaults = {
            "rsi_reversal": 0.60,       # Reversal: let it run a bit more
            "macd_crossover": 0.40,     # Trend: trail early
            "bollinger_squeeze": 0.45,  # Breakout: moderate
            "ma_trend": 0.35,           # Strong trend: trail tight
            "momentum_divergence": 0.50,
            "stochastic_reversal": 0.55,
            # Combo strategies — tighter trailing (higher conviction)
            "rsi_macd_combo": 0.45,
            "bollinger_stoch_combo": 0.50,
            "ma_rsi_macd_combo": 0.35,  # Triple confluence: trail tight
            "rsi_bollinger_combo": 0.55,
            "macd_ma_combo": 0.40,
        }
        return defaults.get(strategy, 0.50)

    def _check_correlation_conflict(self, ticker: str, direction: str,
                                     active_positions: list[dict]) -> bool:
        """P8: Check if opening ticker+direction would conflict with existing positions.

        Returns True if there's a conflict (should SKIP this setup).
        """
        # Find which group this ticker belongs to
        ticker_group = None
        for group_name, group_tickers in TECH_CORRELATION_GROUPS.items():
            if ticker in group_tickers:
                ticker_group = group_name
                break

        if ticker_group is None:
            return False  # No group, no conflict

        # T3-P2: Check existing active positions for same-group conflicts
        # Block ANY position on a different ticker in the same group (same OR opposing direction)
        # Same direction = double exposure risk, opposing = hedge that shouldn't exist here
        group_tickers = TECH_CORRELATION_GROUPS[ticker_group]
        for pos in active_positions:
            pos_ticker = pos.get("ticker", "")
            if pos_ticker in group_tickers and pos_ticker != ticker:
                return True  # Same group, different ticker = correlation conflict

        return False

    def _get_max_per_strategy(self, strategy: str) -> int:
        """J4: Dynamic MAX_PER_STRATEGY based on validation status."""
        if self._weekly_config:
            validated = self._weekly_config.get("validated_strategies", [])
            if strategy in validated:
                return MAX_PER_STRATEGY_VALIDATED
            budgets = self._weekly_config.get("strategy_budgets", {})
            if strategy in budgets:
                return budgets[strategy]
        return MAX_PER_STRATEGY_BASE

    def _get_agent_versions(self) -> dict:
        """P6: Get current agent versions for position stamping."""
        try:
            from .registry import get_all_agents
            agents = get_all_agents()
            return {name: getattr(a, "version", "?") for name, a in agents.items()}
        except Exception:
            return {"trader_3": self.version}

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

        # P6: Get agent versions for stamping
        agent_versions = self._get_agent_versions()

        # T3-P1: Build disabled set from weekly config
        disabled_strategies = set()
        if self._weekly_config:
            disabled_strategies = set(self._weekly_config.get("disabled_strategies", []))

        for setup in setups:
            if len(current_active) + len(new_positions) >= MAX_POSITIONS:
                break

            ticker = setup.get("ticker", "")
            strategy = setup.get("strategy", "")
            direction = setup.get("direction", "")
            score = setup.get("score", 0)
            timeframe = setup.get("timeframe", "1d")

            # T3-P1: Skip strategies that Learning 3 explicitly disabled
            if strategy in disabled_strategies:
                continue

            # Market hours check: don't trade when the market is closed
            from ..config import is_market_open
            if not is_market_open(ticker):
                logger.debug("Skipping %s — market not open", ticker)
                continue

            # Skip if already have same ticker+direction position
            if (ticker, direction) in active_ticker_dirs:
                continue

            # Position limits
            if ticker_counts.get(ticker, 0) >= MAX_PER_TICKER:
                continue
            # J4: Dynamic MAX_PER_STRATEGY
            max_for_strategy = self._get_max_per_strategy(strategy)
            if strategy_counts.get(strategy, 0) >= max_for_strategy:
                continue

            # P8: Correlation conflict check
            all_active = list(current_active) + new_positions
            if self._check_correlation_conflict(ticker, direction, all_active):
                continue

            # Apply learning multipliers
            adj_score = score
            adj_score *= strategy_adj.get(strategy, 1.0)
            adj_score *= ticker_adj.get(ticker, 1.0)
            adj_score *= timeframe_adj.get(timeframe, 1.0)

            if adj_score < MIN_TRADE_SCORE:
                continue

            # T3-P6: Skip setups with invalid entry_price
            entry_price = setup.get("entry_price", 0)
            if not entry_price or entry_price <= 0:
                logger.warning("T3-P6: Skipping %s/%s — invalid entry_price: %s",
                               ticker, strategy, entry_price)
                continue

            # Fetch live entry price (fallback to Scoring 3's last_close)
            # v9.0: bypass_cache=True to avoid stale prices from previous session
            scoring_price = setup.get("entry_price", 0)
            try:
                from ..market_data import fetch_price
                live_price = fetch_price(ticker, bypass_cache=True)
                if live_price and live_price > 0:
                    # Validate price against reference
                    is_valid, reason = validate_price(ticker, live_price)
                    if is_valid:
                        entry_price = live_price
                    else:
                        logger.error("T3: rejected live price for %s — %s", ticker, reason)
                        # Validate scoring fallback too
                        if scoring_price and scoring_price > 0:
                            is_valid_s, reason_s = validate_price(ticker, scoring_price)
                            if is_valid_s:
                                entry_price = scoring_price
                            else:
                                logger.error("T3: scoring price also invalid for %s — %s, skipping", ticker, reason_s)
                                continue
                        else:
                            continue
                else:
                    # Validate scoring fallback
                    if scoring_price and scoring_price > 0:
                        is_valid_s, reason_s = validate_price(ticker, scoring_price)
                        if is_valid_s:
                            entry_price = scoring_price
                        else:
                            logger.error("T3: scoring price invalid for %s — %s, skipping", ticker, reason_s)
                            continue
                    else:
                        continue
            except Exception:
                entry_price = scoring_price

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
                "entry_price": entry_price,
                "current_price": entry_price,
                "target_pct": setup.get("target_pct", 0),
                "stop_pct": setup.get("stop_pct", 0),
                "effective_stop": -setup.get("stop_pct", 0),
                "trailing_active": False,
                "entry_time": now_iso,
                "timeframe": timeframe,
                "regime_at_entry": setup.get("regime"),
                "regime_match": setup.get("regime_match", True),
                "signals_at_entry": setup.get("signals", {}),
                "atr_at_entry": setup.get("atr"),
                "adx_at_entry": setup.get("adx"),
                "volume_ratio_at_entry": setup.get("volume_ratio", 1.0),
                "unrealized_pnl_pct": 0.0,
                "high_watermark": entry_price,
                "low_watermark": entry_price,
                # P6: Version stamping
                "agent_versions": agent_versions,
                # J1: Strategy version for param tracking
                "strategy_version": (
                    self._weekly_config.get("strategy_versions", {}).get(strategy, "1.0")
                    if self._weekly_config else "1.0"
                ),
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

    def get_strategy_performance(self) -> list[dict]:
        """Compute A/B performance per strategy from closed trades.

        Returns a list of strategy performance dicts, sorted by trade count desc.
        Each entry includes aggregated stats and the underlying trades.
        """
        from .agent_scoring_3 import STRATEGIES

        state = _load_positions()
        closed = state.get("closed", [])

        perf: dict[str, dict] = {}
        for trade in closed:
            strategy = trade.get("strategy", "unknown")
            if strategy not in perf:
                strat_info = STRATEGIES.get(strategy, {})
                perf[strategy] = {
                    "strategy": strategy,
                    "strategy_name": strat_info.get("name", strategy),
                    "is_combo": "combo" in strategy,
                    "trades_count": 0, "wins": 0, "losses": 0,
                    "expired": 0, "total_pnl": 0.0, "total_holding": 0.0,
                    "pnl_list": [], "trades": [],
                }
            p = perf[strategy]
            p["trades_count"] += 1
            pnl = trade.get("pnl_pct", 0)
            p["total_pnl"] += pnl
            p["pnl_list"].append(pnl)
            result = trade.get("result", "")
            if result == "TP_HIT":
                p["wins"] += 1
            elif result == "SL_HIT":
                p["losses"] += 1
            elif result == "EXPIRED":
                p["expired"] += 1
            p["total_holding"] += trade.get("holding_hours", 0)
            # Compact trade for frontend drill-down
            p["trades"].append({
                "ticker": trade.get("ticker", ""),
                "direction": trade.get("direction", ""),
                "result": result,
                "pnl_pct": round(pnl, 2),
                "entry_time": trade.get("entry_time", ""),
                "close_time": trade.get("close_time", ""),
                "holding_hours": round(trade.get("holding_hours", 0), 1),
                "score": trade.get("score", 0),
                "regime": trade.get("regime_at_entry", ""),
            })

        # Compute stats and convert to list
        result_list = []
        for strategy, p in perf.items():
            n = p["trades_count"]
            p["win_rate"] = round(p["wins"] / n * 100, 1) if n > 0 else 0
            p["avg_pnl"] = round(p["total_pnl"] / n, 2) if n > 0 else 0
            p["avg_holding_hours"] = round(p["total_holding"] / n, 1) if n > 0 else 0
            p["total_pnl"] = round(p["total_pnl"], 2)
            # Sharpe ratio
            if len(p["pnl_list"]) >= 3:
                import statistics
                mean_pnl = statistics.mean(p["pnl_list"])
                std_pnl = statistics.stdev(p["pnl_list"])
                p["sharpe"] = round(mean_pnl / std_pnl, 2) if std_pnl > 0 else 0
            else:
                p["sharpe"] = None
            # Learning adjustment
            adj = self._current_learning.get("strategy_adj", {})
            p["learning_adj"] = adj.get(strategy, 1.0)
            # Status from weekly config
            if self._weekly_config:
                if strategy in self._weekly_config.get("validated_strategies", []):
                    p["status"] = "validated"
                elif strategy in self._weekly_config.get("disabled_strategies", []):
                    p["status"] = "disabled"
                else:
                    p["status"] = "active"
            else:
                p["status"] = "active"
            # Clean up internal fields
            del p["total_holding"]
            del p["pnl_list"]
            # Sort trades by entry_time desc
            p["trades"].sort(key=lambda t: t.get("entry_time", ""), reverse=True)
            result_list.append(p)

        # Sort by trades count desc
        result_list.sort(key=lambda s: s["trades_count"], reverse=True)
        return result_list

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

    def _load_weekly_config_from_disk(self):
        """T3-P5: Load persisted weekly config from disk (survives restart).

        Uses same path resolution as Learning 3 (DATA_DIR env or "data/").
        """
        config_path = Path(os.getenv("DATA_DIR", "data")) / "learning3_weekly_config.json"
        try:
            if config_path.exists():
                with open(config_path, "r") as f:
                    self._weekly_config = json.load(f)
                logger.info("T3-P5: Loaded weekly config from disk (%s)",
                           self._weekly_config.get("generated_at", "?"))
        except Exception as exc:
            logger.debug("Could not load weekly config from disk: %s", exc)

    def reset_daily_counters(self):
        """Reset daily counters (called by scheduler at midnight)."""
        self._evaluations_today = 0
