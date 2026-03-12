"""Agent Trader 4 — Meta/Ensemble trader combining signals from Teams 1, 2, 3 (Équipe 4).

Stratégie :
- Consumes meta-scored signals from Scoring 4 (confluence of news, trend, tech)
- Confluence trades: when news + technical + trend agree → high conviction
- Position sizing based on confluence level (3/3 = full, 2/3 = half)
- Holding period: 0-3 days (72h max)
- TP/SL with trailing stop for risk management
- Correlation check to avoid conflicting positions

Architecture :
- Consumes meta-scores from Agent Scoring 4
- Manages persistent positions (PG + JSON fallback)
- Tracks per-team accuracy to learn which combinations work best
- Requires minimum history from teams 1-3 before activating
- Weekly config from Learning 4 (weights, validated combos, etc.)

Différences avec les autres traders :
- Trader 1 : day trading intraday, news-only, TP/SL
- Trader 2 : trend following, commodity-only, flip on reversal
- Trader 3 : technical indicators, multi-strategy, A/B testing
- Trader 4 : multi-signal ensemble, any asset, confluence-driven, 0-3j holding

Expertise incarnée :
- 15+ ans combining fundamental + technical analysis
- Portfolio-level risk management across signal sources
- Understanding that confluence of independent signals reduces false positives
"""

import json
import logging
import os
import time
import fcntl
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta, date
from pathlib import Path

from .base import BaseAgent, AgentStatus
from ..market_data import validate_price

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────

# Minimum confluence level to open a position
MIN_CONFLUENCE_LEVEL = 2  # At least 2/3 teams must agree

# Minimum meta-score threshold
MIN_META_SCORE = 15.0

# Position sizing by confluence level
SIZING_BY_CONFLUENCE = {
    3: 1.0,    # Full size — all teams agree
    2: 0.5,    # Half size — 2/3 agree
    1: 0.0,    # No position — insufficient confluence
    0: 0.0,
}

# Maximum concurrent positions
MAX_POSITIONS = 6

# Timeout constants
GLOBAL_TIMEOUT_S = 120
PER_FETCH_TIMEOUT_S = 15

# History pruning — keep 1 year
HISTORY_MAX_AGE_DAYS = 365

# P7 fix: Max hold hours aligned at 72h (0-3 days as per spec)
MAX_HOLD_HOURS = 72

# P3 fix: TP/SL configuration
# TP/SL are percentages relative to entry price
TP_PCT_BY_CONFLUENCE = {
    3: 3.0,    # 3% target for full confluence
    2: 2.0,    # 2% target for partial confluence
}
SL_PCT = 1.5       # 1.5% stop-loss (applies to all)
TRAILING_ACTIVATION_PCT = 1.0   # Activate trailing after 1% profit
TRAILING_DISTANCE_PCT = 0.7     # Trail 0.7% behind peak

# Activation date — Team 4 only starts trading after this date
# Configurable via TEAM4_ACTIVATION_DATE env var (format: YYYY-MM-DD)
_activation_str = os.environ.get("TEAM4_ACTIVATION_DATE", "2026-04-15")
try:
    ACTIVATION_DATE = date.fromisoformat(_activation_str)
except ValueError:
    ACTIVATION_DATE = date(2026, 4, 15)

# P5 fix: Correlation groups (shared with Team 1 & 3)
META_CORRELATION_GROUPS = {
    "energy": ["TTE.PA", "CL=F", "BZ=F", "NG=F"],
    "gold_safe": ["GC=F", "SI=F", "USDCHF=X"],
    "risk_on_eu": ["^FCHI", "^GDAXI", "^FTSE"],
    "risk_on_us": ["^GSPC", "^DJI", "^IXIC", "^RUT"],
    "jpy_carry": ["USDJPY=X", "EURJPY=X", "^N225"],
    "luxury": ["MC.PA", "RMS.PA", "OR.PA"],
    "agri": ["ZC=F", "ZW=F", "ZS=F"],
    "tropical_soft": ["KC=F", "SB=F", "CC=F", "OJ=F"],
    "livestock": ["LE=F", "HE=F"],
    "pgm": ["PL=F", "PA=F"],
    "china_proxy": ["USDCNH=X", "HG=F", "AUDUSD=X"],
}

# Persistence file (JSON fallback)
POSITIONS_FILE = Path(os.getenv("DATA_DIR", "data")) / "meta_positions.json"


def _ensure_positions_file():
    """Create the positions file if it doesn't exist."""
    POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not POSITIONS_FILE.exists():
        POSITIONS_FILE.write_text("{}")


def _load_positions() -> dict:
    """Load meta positions from PG or JSON."""
    try:
        from ..database import is_pg_enabled
        if is_pg_enabled():
            return _pg_load_positions()
    except Exception:
        pass
    return _load_positions_json_only()


def _save_positions(positions: dict):
    """Save meta positions to PG or JSON."""
    try:
        from ..database import is_pg_enabled
        if is_pg_enabled():
            _pg_save_positions(positions)
            return
    except Exception:
        pass
    _save_positions_json(positions)


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


def _save_positions_json(positions: dict):
    """JSON-only save (fallback) — atomic via temp-file + os.replace().

    v8.4 fix: Previous version truncated file before lock acquisition.
    """
    import tempfile
    _ensure_positions_file()
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(POSITIONS_FILE.parent), suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(positions, f, indent=2, default=str)
        os.replace(tmp_path, str(POSITIONS_FILE))
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
                cur.execute("SELECT ticker, data FROM meta_positions")
                rows = cur.fetchall()
                return {row["ticker"]: row["data"] for row in rows}
    except Exception as exc:
        logger.warning("PG load meta_positions failed: %s — fallback JSON", exc)
        return _load_positions_json_only()


def _pg_save_positions(positions: dict):
    """Save positions to PostgreSQL (upsert per ticker)."""
    try:
        from ..database import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                for ticker, data in positions.items():
                    cur.execute("""
                        INSERT INTO meta_positions (ticker, data, updated_at)
                        VALUES (%s, %s, NOW())
                        ON CONFLICT (ticker) DO UPDATE SET data = %s, updated_at = NOW()
                    """, (ticker, json.dumps(data, default=str),
                          json.dumps(data, default=str)))
    except Exception as exc:
        logger.warning("PG save meta_positions failed: %s — fallback JSON", exc)
        _save_positions_json(positions)


def _fetch_current_price(ticker: str, bypass_cache: bool = False) -> float | None:
    """Fetch the latest price for a ticker."""
    try:
        from ..market_data import fetch_price
        price = fetch_price(ticker, bypass_cache=bypass_cache)
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


def _check_correlation_conflict(ticker: str, direction: str,
                                 positions: dict) -> bool:
    """P5: Check if opening this position conflicts with existing correlated positions.

    Returns True if conflict detected (should NOT open).
    """
    for group_name, group_tickers in META_CORRELATION_GROUPS.items():
        if ticker not in group_tickers:
            continue
        # Check if any open position in the same group has opposite direction
        for other_ticker in group_tickers:
            if other_ticker == ticker:
                continue
            other_pos = positions.get(other_ticker)
            if not other_pos or other_pos.get("status") != "OPEN":
                continue
            other_dir = other_pos.get("direction")
            if other_dir and other_dir != direction:
                logger.info("Correlation conflict: %s %s vs %s %s (group=%s)",
                            ticker, direction, other_ticker, other_dir, group_name)
                return True
    return False


def _get_agent_versions() -> dict:
    """P6: Get current versions of all Team 4 agents."""
    versions = {}
    try:
        from .agent_scoring_4 import AgentScoring4
        versions["scoring_4"] = AgentScoring4.version
    except Exception:
        pass
    try:
        versions["trader_4"] = AgentTrader4.version
    except Exception:
        pass
    try:
        from .agent_journal_4 import AgentJournal4
        versions["journal_4"] = AgentJournal4.version
    except Exception:
        pass
    try:
        from .agent_learning_4 import AgentLearning4
        versions["learning_4"] = AgentLearning4.version
    except Exception:
        pass
    return versions


class AgentTrader4(BaseAgent):
    """Agent Trader 4 — Meta/Ensemble trader using confluence signals.

    Combines signals from Teams 1, 2, 3 via Scoring 4 meta-scores.
    Opens positions when multiple independent signals agree (confluence).
    TP/SL with trailing stop, correlation check, 0-3 day holding.
    """

    name = "trader_4"
    description = "Meta trading — confluence-driven ensemble positions"
    version = "2.1"  # v2.1: trailing stop persistence fix, stale stop_price fix

    def __init__(self):
        super().__init__()
        self._position_opens_total: int = 0
        self._position_closes_total: int = 0
        self._evaluations_today: int = 0
        self._last_action_ticker: str | None = None
        self._last_action_direction: str | None = None
        self._upstream_ready: bool = False

    def run(self, meta_scored=None, learning_data=None,
            weekly_config=None, **kwargs) -> dict:
        """Evaluate meta-scored signals and manage ensemble positions.

        Args:
            meta_scored: dict from Agent Scoring 4 (meta-scores with confluence)
            learning_data: dict from Agent Learning 4 (optional adjustments)
            weekly_config: dict from Learning 4 weekly config (optional)

        Returns: dict with positions state and any changes made.
        """
        # P8: Check activation date
        if date.today() < ACTIVATION_DATE:
            self._set_status(AgentStatus.IDLE,
                             f"Activation {ACTIVATION_DATE.isoformat()}")
            return {"positions": {}, "changes": [],
                    "reason": "not_yet_activated"}

        meta_items = (meta_scored or {}).get("meta_scored", [])
        self._set_status(
            AgentStatus.WORKING,
            f"Evaluating {len(meta_items)} meta-scored signals"
        )

        start = time.monotonic()
        learning = learning_data or {}

        try:
            # Check if upstream teams have enough history
            if not self._check_upstream_readiness(meta_scored):
                self.log("Upstream teams insufficient data — skipping", {
                    "sources_active": (meta_scored or {}).get("stats", {}).get("sources_active", 0),
                    "min_required": 2,
                }, level="WARN")
                self._set_status(AgentStatus.IDLE, "Waiting for upstream data")
                return {"positions": {}, "changes": [], "reason": "insufficient_upstream_data"}

            # Load current positions
            positions = _load_positions()

            changes = []

            # Step 1: Check existing positions for TP/SL/trailing
            for ticker in list(positions.keys()):
                elapsed = time.monotonic() - start
                if elapsed > GLOBAL_TIMEOUT_S:
                    break
                pos = positions[ticker]
                if pos.get("status") != "OPEN":
                    continue
                tp_sl_change = self._check_tp_sl_trailing(ticker, pos, positions)
                if tp_sl_change:
                    changes.append(tp_sl_change)
                    positions[ticker] = tp_sl_change["new_position"]
                    self._position_closes_total += 1

            # Step 2: Evaluate new signals for potential entries
            for item in meta_items:
                elapsed = time.monotonic() - start
                if elapsed > GLOBAL_TIMEOUT_S:
                    self.log("Global timeout reached during evaluation",
                             {"elapsed_s": round(elapsed, 1)}, level="WARN")
                    break

                ticker = item.get("ticker", "")
                if not ticker:
                    continue

                change = self.execute(
                    f"Evaluating {ticker}",
                    self._evaluate_signal,
                    ticker, item, positions, learning, weekly_config,
                )

                if change:
                    changes.append(change)
                    positions[ticker] = change["new_position"]
                    if change["action"] == "OPEN":
                        self._position_opens_total += 1
                    elif change["action"] == "CLOSE":
                        self._position_closes_total += 1
                    self._last_action_ticker = ticker
                    self._last_action_direction = change.get("direction")

            # Step 3: Check existing positions for exits (confluence lost / expiry)
            for ticker in list(positions.keys()):
                elapsed = time.monotonic() - start
                if elapsed > GLOBAL_TIMEOUT_S:
                    break

                pos = positions[ticker]
                if pos.get("status") != "OPEN":
                    continue

                # Check if this ticker still has confluence
                ticker_in_signals = any(
                    i["ticker"] == ticker for i in meta_items
                )
                if not ticker_in_signals:
                    # No signal — check if position is old enough to close
                    entry_time = pos.get("entry_time", "")
                    try:
                        entry_dt = datetime.fromisoformat(
                            entry_time.replace("Z", "+00:00"))
                        age_hours = (
                            datetime.now(timezone.utc) - entry_dt
                        ).total_seconds() / 3600
                        # P7: Close positions older than MAX_HOLD_HOURS
                        if age_hours > MAX_HOLD_HOURS:
                            close_change = self._close_position(
                                ticker, positions[ticker],
                                f"Signal expired — no renewal for {age_hours:.0f}h > {MAX_HOLD_HOURS}h max",
                                "EXPIRED"
                            )
                            if close_change:
                                changes.append(close_change)
                                positions[ticker] = close_change["new_position"]
                                self._position_closes_total += 1
                    except (ValueError, AttributeError, TypeError):
                        pass

            # Step 4: Update prices for open positions
            self._update_prices(positions)

            # Save
            _save_positions(positions)

            # Build result BEFORE publish/log — an exception in publish/log
            # must never prevent the result from being returned to the pipeline.
            duration_ms = int((time.monotonic() - start) * 1000)
            result = {
                "positions": positions,
                "changes": changes,
                "signals_evaluated": len(meta_items),
                "duration_ms": duration_ms,
            }

            # Publish + Log (best-effort, never blocks result)
            try:
                self._evaluations_today += 1
                self.publish("meta_trade_update", {
                    "changes": [
                        {"ticker": c["ticker"], "action": c["action"],
                         "direction": c.get("direction")}
                        for c in changes
                    ],
                    "open_positions": sum(
                        1 for p in positions.values() if p.get("status") == "OPEN"
                    ),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

                for c in changes:
                    self.log_decision(f"META {c['action']} {c['ticker']}", {
                        "ticker": c["ticker"],
                        "action": c["action"],
                        "direction": c.get("direction"),
                        "confluence_level": c.get("confluence_level"),
                        "meta_score": c.get("meta_score"),
                        "reason": c.get("reason", ""),
                        "pnl_pct": c.get("close_pnl"),
                        "close_type": c.get("close_type"),
                    })

                if not changes:
                    self.log("Meta evaluation complete — no changes", {
                        "signals_evaluated": len(meta_items),
                        "open_positions": sum(
                            1 for p in positions.values() if p.get("status") == "OPEN"
                        ),
                    })
            except Exception as log_exc:
                logger.warning("Trader 4 publish/log failed (positions saved OK): %s", log_exc)

            self._set_status(
                AgentStatus.IDLE,
                f"{len(changes)} changes, "
                f"{sum(1 for p in positions.values() if p.get('status') == 'OPEN')} open"
            )

            return result

        except Exception as exc:
            self.log("Meta evaluation failed", {"error": str(exc)}, level="ERROR")
            self._set_status(AgentStatus.ERROR, str(exc))
            raise

    def run_position_monitor(self) -> dict:
        """V1: Standalone position monitor — check TP/SL/trailing between scans.

        Called by scheduler every 15 min. Only checks existing positions,
        does NOT evaluate new signals (no Scoring 4 data needed).
        """
        positions = _load_positions()
        open_positions = {
            t: p for t, p in positions.items() if p.get("status") == "OPEN"
        }
        if not open_positions:
            return {"active": 0, "closed": 0}

        # Fetch prices in parallel
        tickers = list(open_positions.keys())
        prices: dict[str, float | None] = {}
        try:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=min(len(tickers), 5)) as executor:
                futures = {
                    executor.submit(_fetch_current_price, t): t
                    for t in tickers
                }
                for future in as_completed(futures, timeout=30):
                    ticker = futures[future]
                    try:
                        prices[ticker] = future.result(timeout=15)
                    except Exception:
                        prices[ticker] = None
        except Exception:
            for t in tickers:
                if t not in prices:
                    prices[t] = _fetch_current_price(t)

        changes = []
        for ticker, pos in open_positions.items():
            price = prices.get(ticker)
            if price:
                is_valid, reason = validate_price(ticker, price)
                if is_valid:
                    pos["current_price"] = price
                else:
                    logger.warning("T4 monitor: rejected price for %s — %s", ticker, reason)
            change = self._check_tp_sl_trailing(ticker, pos, positions)
            if change:
                changes.append(change)
                positions[ticker] = change["new_position"]
                self._position_closes_total += 1

        # Always save — trailing stop updates modify stop_price/peak_price on still-open positions
        _save_positions(positions)

        if changes:
            for c in changes:
                self.log_decision("POSITION CLOSED (monitor)", {
                    "ticker": c.get("ticker"),
                    "close_type": c.get("close_type"),
                    "pnl_pct": c.get("pnl_pct"),
                })

        return {"active": len(open_positions) - len(changes), "closed": len(changes)}

    def _check_upstream_readiness(self, meta_scored: dict | None) -> bool:
        """Check if upstream teams have enough data to make decisions."""
        if not meta_scored:
            return False
        sources_active = meta_scored.get("stats", {}).get("sources_active", 0)
        # Need at least 2 sources active
        self._upstream_ready = sources_active >= 2
        return self._upstream_ready

    def _check_tp_sl_trailing(self, ticker: str, pos: dict,
                               positions: dict) -> dict | None:
        """P3: Check TP, trailing stop, then SL for an open position.

        Order: TP → Trailing → SL (trailing evaluated BEFORE SL).
        """
        entry_price = pos.get("entry_price")
        current_price = pos.get("current_price")
        direction = pos.get("direction", "NEUTRAL")
        confluence_level = pos.get("confluence_level", 2)

        if not entry_price or not current_price or direction == "NEUTRAL":
            return None

        # Calculate current P&L
        if direction == "LONG":
            pnl_pct = (current_price - entry_price) / entry_price * 100
        else:
            pnl_pct = (entry_price - current_price) / entry_price * 100

        tp_pct = TP_PCT_BY_CONFLUENCE.get(confluence_level, 2.0)
        stop_price = pos.get("stop_price")
        peak_price = pos.get("peak_price", current_price)

        # 1. TP check
        if pnl_pct >= tp_pct:
            return self._close_position(
                ticker, pos,
                f"TP_HIT: +{pnl_pct:.2f}% >= {tp_pct}% target",
                "TP_HIT"
            )

        # 2. Trailing stop update
        if pnl_pct >= TRAILING_ACTIVATION_PCT:
            if direction == "LONG":
                new_peak = max(peak_price or current_price, current_price)
                new_stop = new_peak * (1 - TRAILING_DISTANCE_PCT / 100)
            else:
                new_peak = min(peak_price or current_price, current_price)
                new_stop = new_peak * (1 + TRAILING_DISTANCE_PCT / 100)

            pos["peak_price"] = new_peak
            if stop_price is None or (direction == "LONG" and new_stop > stop_price) \
                    or (direction == "SHORT" and new_stop < stop_price):
                pos["stop_price"] = round(new_stop, 4)

        # 3. SL check (uses CURRENT stop_price after trailing update, not stale capture)
        effective_stop = pos.get("stop_price")
        if effective_stop is None:
            # Default SL
            if direction == "LONG":
                effective_stop = entry_price * (1 - SL_PCT / 100)
            else:
                effective_stop = entry_price * (1 + SL_PCT / 100)

        if direction == "LONG" and current_price <= effective_stop:
            return self._close_position(
                ticker, pos,
                f"SL_HIT: {pnl_pct:.2f}% (stop={effective_stop:.4f})",
                "SL_HIT"
            )
        elif direction == "SHORT" and current_price >= effective_stop:
            return self._close_position(
                ticker, pos,
                f"SL_HIT: {pnl_pct:.2f}% (stop={effective_stop:.4f})",
                "SL_HIT"
            )

        return None

    def _evaluate_signal(self, ticker: str, signal: dict,
                         positions: dict, learning: dict,
                         weekly_config: dict | None = None) -> dict | None:
        """Evaluate a meta-scored signal for position action.

        Returns change dict or None if no action.
        """
        confluence_level = signal.get("confluence_level", 0)
        meta_score = signal.get("meta_score", 0)
        direction = signal.get("direction", "NEUTRAL")

        if direction == "NEUTRAL":
            return None

        if confluence_level < MIN_CONFLUENCE_LEVEL:
            return None

        if meta_score < MIN_META_SCORE:
            return None

        # Apply learning adjustments if available
        confluence_adj = learning.get("confluence_adj", {})
        ticker_adj = learning.get("ticker_adj", {})
        combo_adj = learning.get("combo_adj", {})

        adjusted_score = meta_score * ticker_adj.get(ticker, 1.0)
        level_adj = confluence_adj.get(str(confluence_level), 1.0)
        adjusted_score *= level_adj

        # Apply combo adjustment
        source_details = signal.get("source_details", {})
        teams = sorted(source_details.keys())
        combo_key = "+".join(teams)
        c_adj = combo_adj.get(combo_key, 1.0)
        adjusted_score *= c_adj

        # Check existing position
        existing = positions.get(ticker)
        if existing and existing.get("status") == "OPEN":
            existing_dir = existing.get("direction")
            if existing_dir == direction:
                # Same direction — reinforce confidence, extend hold
                existing["confidence"] = min(100,
                    existing.get("confidence", 50) + int(adjusted_score / 10))
                existing["last_evaluation"] = datetime.now(timezone.utc).isoformat()
                existing["last_meta_score"] = round(adjusted_score, 1)
                existing["signal_renewal_count"] = existing.get("signal_renewal_count", 0) + 1
                return None
            else:
                # Opposite direction — close existing
                close_change = self._close_position(
                    ticker, existing,
                    f"Confluence reversal: {existing_dir} → {direction}",
                    "REVERSAL"
                )
                if close_change:
                    return close_change
                return None

        # Market hours check
        from ..config import is_market_open
        if not is_market_open(ticker):
            return None

        # P5: Check correlation conflict
        if _check_correlation_conflict(ticker, direction, positions):
            return None

        # Check max positions
        open_count = sum(
            1 for p in positions.values() if p.get("status") == "OPEN"
        )
        if open_count >= MAX_POSITIONS:
            return None

        # Open new position — bypass cache for fresh price at entry
        price = _fetch_current_price(ticker, bypass_cache=True)
        position_size = SIZING_BY_CONFLUENCE.get(confluence_level, 0.0)

        if position_size <= 0:
            return None

        # Validate entry price before storing
        if price is not None:
            is_valid, reason = validate_price(ticker, price)
            if not is_valid:
                logger.error("T4: rejected entry price for %s — %s", ticker, reason)
                return None
        else:
            logger.warning("T4: no price available for %s — skipping", ticker)
            return None

        now_iso = datetime.now(timezone.utc).isoformat()

        # P3: Calculate TP/SL prices
        tp_pct = TP_PCT_BY_CONFLUENCE.get(confluence_level, 2.0)
        if direction == "LONG":
            tp_price = price * (1 + tp_pct / 100) if price else None
            sl_price = price * (1 - SL_PCT / 100) if price else None
        else:
            tp_price = price * (1 - tp_pct / 100) if price else None
            sl_price = price * (1 + SL_PCT / 100) if price else None

        new_position = {
            "ticker": ticker,
            "status": "OPEN",
            "direction": direction,
            "entry_price": price,
            "current_price": price,
            "unrealized_pnl_pct": 0.0,
            "entry_time": now_iso,
            "last_evaluation": now_iso,
            "last_meta_score": round(adjusted_score, 1),
            "confluence_level": confluence_level,
            "position_size": position_size,
            "confidence": min(100, int(adjusted_score * 2)),
            "source_details": signal.get("source_details", {}),
            "reasoning": (
                f"Confluence {confluence_level}/3 — {direction} — "
                f"meta-score {adjusted_score:.1f}, size {position_size:.0%}"
            ),
            "history": positions.get(ticker, {}).get("history", []),
            "total_trades": positions.get(ticker, {}).get("total_trades", 0) + 1,
            "realized_pnl_pct": positions.get(ticker, {}).get("realized_pnl_pct", 0.0),
            # P3: TP/SL
            "tp_price": round(tp_price, 4) if tp_price else None,
            "sl_price": round(sl_price, 4) if sl_price else None,
            "stop_price": round(sl_price, 4) if sl_price else None,
            "peak_price": price,
            "tp_pct": tp_pct,
            "sl_pct": SL_PCT,
            # P6: Agent versions
            "agent_versions": _get_agent_versions(),
            "signal_renewal_count": 0,
        }

        return {
            "ticker": ticker,
            "action": "OPEN",
            "direction": direction,
            "confluence_level": confluence_level,
            "meta_score": round(adjusted_score, 1),
            "position_size": position_size,
            "reason": new_position["reasoning"],
            "close_type": None,
            "new_position": new_position,
        }

    def _close_position(self, ticker: str, position: dict,
                        reason: str, close_type: str = "SIGNAL") -> dict | None:
        """Close an existing position and record P&L."""
        entry_price = position.get("entry_price")
        current_price = position.get("current_price") or _fetch_current_price(ticker)
        # Validate exit price (use val_reason to avoid shadowing the close reason parameter)
        if current_price is not None:
            is_valid, val_reason = validate_price(ticker, current_price)
            if not is_valid:
                logger.warning("T4 close: rejected exit price for %s — %s, using entry_price", ticker, val_reason)
                current_price = entry_price
        direction = position.get("direction", "NEUTRAL")

        close_pnl = 0.0
        if entry_price and entry_price > 0 and current_price:
            if direction == "LONG":
                close_pnl = round((current_price - entry_price) / entry_price * 100, 2)
            elif direction == "SHORT":
                close_pnl = round((entry_price - current_price) / entry_price * 100, 2)

        now_iso = datetime.now(timezone.utc).isoformat()

        # Record in history
        history_entry = {
            "time": now_iso,
            "direction": direction,
            "entry_price": entry_price,
            "exit_price": current_price,
            "pnl_pct": close_pnl,
            "confluence_level": position.get("confluence_level"),
            "meta_score": position.get("last_meta_score"),
            "source_details": position.get("source_details", {}),
            "reason": reason,
            "close_type": close_type,
            "entry_time": position.get("entry_time"),
            "agent_versions": position.get("agent_versions", {}),
            "tp_pct": position.get("tp_pct"),
            "sl_pct": position.get("sl_pct"),
            "signal_renewal_count": position.get("signal_renewal_count", 0),
        }

        history = position.get("history", [])
        history.insert(0, history_entry)
        # Temporal pruning
        cutoff = (datetime.now(timezone.utc) - timedelta(days=HISTORY_MAX_AGE_DAYS)).isoformat()
        history = [h for h in history if (h.get("time", "") > cutoff or h.get("time", "") == "")]
        history = history[:100]

        new_position = {
            "ticker": ticker,
            "status": "CLOSED",
            "direction": "NEUTRAL",
            "entry_price": None,
            "current_price": current_price,
            "unrealized_pnl_pct": 0.0,
            "entry_time": None,
            "last_evaluation": now_iso,
            "last_meta_score": 0,
            "confluence_level": 0,
            "position_size": 0.0,
            "confidence": 0,
            "source_details": {},
            "reasoning": reason,
            "history": history,
            "total_trades": position.get("total_trades", 0),
            "realized_pnl_pct": round(
                position.get("realized_pnl_pct", 0.0) + close_pnl, 2
            ),
            "tp_price": None,
            "sl_price": None,
            "stop_price": None,
            "peak_price": None,
        }

        return {
            "ticker": ticker,
            "action": "CLOSE",
            "direction": direction,
            "close_pnl": close_pnl,
            "confluence_level": position.get("confluence_level"),
            "meta_score": position.get("last_meta_score"),
            "reason": reason,
            "close_type": close_type,
            "new_position": new_position,
        }

    def _update_prices(self, positions: dict) -> None:
        """Fetch current prices for all open positions in parallel."""
        open_tickers = [
            t for t, p in positions.items() if p.get("status") == "OPEN"
        ]
        if not open_tickers:
            return

        prices: dict[str, float | None] = {}

        try:
            with ThreadPoolExecutor(max_workers=min(4, len(open_tickers))) as executor:
                futures = {
                    executor.submit(_fetch_current_price, t): t
                    for t in open_tickers
                }
                for future in as_completed(futures, timeout=PER_FETCH_TIMEOUT_S * 2):
                    ticker = futures[future]
                    try:
                        prices[ticker] = future.result(timeout=PER_FETCH_TIMEOUT_S)
                    except Exception as exc:
                        logger.warning("Price fetch timeout/error for %s: %s", ticker, exc)
                        prices[ticker] = None
        except Exception as exc:
            logger.warning("Parallel price fetch failed: %s — trying sequential", exc)
            for t in open_tickers:
                if t not in prices:
                    prices[t] = _fetch_current_price(t)

        now_iso = datetime.now(timezone.utc).isoformat()
        for ticker, price in prices.items():
            if not price or ticker not in positions:
                continue
            # Validate price before updating
            is_valid, reason = validate_price(ticker, price)
            if not is_valid:
                logger.warning("T4 price update: rejected for %s — %s", ticker, reason)
                continue
            pos = positions[ticker]
            pos["current_price"] = price
            pos["last_price_update"] = now_iso

            entry = pos.get("entry_price")
            direction = pos.get("direction")
            if entry and entry > 0 and direction in ("LONG", "SHORT"):
                if direction == "LONG":
                    pos["unrealized_pnl_pct"] = round(
                        (price - entry) / entry * 100, 2)
                else:
                    pos["unrealized_pnl_pct"] = round(
                        (entry - price) / entry * 100, 2)

    def get_positions(self) -> dict:
        """Get current meta positions (for API/frontend)."""
        return _load_positions()

    def get_position_history(self, ticker: str) -> list:
        """Get position history for a ticker."""
        positions = _load_positions()
        pos = positions.get(ticker, {})
        return pos.get("history", [])

    def get_trade_history(self) -> list:
        """Get all closed trade history across all tickers (for API/frontend)."""
        positions = _load_positions()
        all_history = []
        for ticker, pos in positions.items():
            if not isinstance(pos, dict):
                continue
            for h in pos.get("history", []):
                entry = dict(h)
                entry["ticker"] = ticker
                # Add contributing_teams from source_details
                sd = entry.get("source_details", {})
                if sd and "contributing_teams" not in entry:
                    entry["contributing_teams"] = list(sd.keys())
                all_history.append(entry)
        # Sort by time descending
        all_history.sort(key=lambda h: h.get("time", ""), reverse=True)
        return all_history

    def get_metrics(self) -> dict:
        """Metrics for frontend overview."""
        positions = _load_positions()

        open_count = sum(1 for p in positions.values() if p.get("status") == "OPEN")
        open_long = sum(1 for p in positions.values()
                        if p.get("status") == "OPEN" and p.get("direction") == "LONG")
        open_short = sum(1 for p in positions.values()
                         if p.get("status") == "OPEN" and p.get("direction") == "SHORT")
        total_unrealized = sum(
            p.get("unrealized_pnl_pct", 0)
            for p in positions.values() if p.get("status") == "OPEN"
        )
        total_realized = sum(p.get("realized_pnl_pct", 0) for p in positions.values())

        return {
            "open_positions": open_count,
            "positions_long": open_long,
            "positions_short": open_short,
            "total_unrealized_pnl": round(total_unrealized, 2),
            "total_realized_pnl": round(total_realized, 2),
            "position_opens_total": self._position_opens_total,
            "position_closes_total": self._position_closes_total,
            "evaluations_today": self._evaluations_today,
            "upstream_ready": self._upstream_ready,
            "last_action_ticker": self._last_action_ticker,
            "last_action_direction": self._last_action_direction,
            "activation_date": ACTIVATION_DATE.isoformat(),
            "is_active": date.today() >= ACTIVATION_DATE,
        }

    def reset_daily_counters(self):
        """Reset daily counters (called by scheduler at midnight)."""
        self._evaluations_today = 0
