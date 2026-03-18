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

Audit fixes v7.2 (Auditeur + Journal 2 perspectives):
- P1: getattr → direct access on _current_learning/_current_trend_scoring
- P2: Global timeout 120s on run(), per-fetch timeout 15s
- P3: Parallel price fetch via ThreadPoolExecutor
- P4: Log errors in _fetch_current_price (no more silent pass)
- P7: Clarified use_trend_scoring flow (direct var init in key_news)
- P8/J3: Guard entry_price > 0 in _make_change and unrealized P&L
- P9: History pruning temporal (1 year) instead of hard cap 50
- J2: Store trend_scoring snapshot in flip history_entry
- J6: Add news published timestamp in key_news
- J7: Track high/low watermarks per position for MAE/MFE fallback
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

# P2: Timeout constants
GLOBAL_TIMEOUT_S = 120
PER_FETCH_TIMEOUT_S = 15

# P9: History pruning — keep 1 year instead of hard cap 50
HISTORY_MAX_AGE_DAYS = 365

# v8.0: Time-decay — reduce confidence when no news confirm the position
# After CONFIDENCE_DECAY_START_DAYS without confirming news, confidence drops
# by CONFIDENCE_DECAY_PER_DAY each day. Below CONFIDENCE_CLOSE_THRESHOLD → close.
CONFIDENCE_DECAY_START_DAYS = 3  # Grace period before decay starts
CONFIDENCE_DECAY_PER_DAY = 12   # Points of confidence lost per day (7 days ≈ kills a 100-conf position)
CONFIDENCE_CLOSE_THRESHOLD = 15  # Close position when confidence drops below this

# v8.0: Momentum reversal — if the last N flips on a ticker are all losses,
# reduce the signal threshold to make it easier to flip direction.
# This prevents staying stuck in a losing direction.
LOSING_STREAK_FLIP_COUNT = 3    # How many consecutive losing flips to trigger
LOSING_STREAK_THRESHOLD_MULT = 0.5  # Halve the flip threshold on losing streak

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
            # Validate: positions must be a dict, not a list (corrupted reset)
            if not isinstance(data, dict):
                return {}
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
    # T2-P1: Atomic write — write to temp then rename (avoids truncate-before-lock race)
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
            if not isinstance(data, dict):
                return {}
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
                    data_json = json.dumps(data, default=str)  # T2-P12: serialize once
                    cur.execute("""
                        INSERT INTO trend_positions (ticker, data, updated_at)
                        VALUES (%s, %s, NOW())
                        ON CONFLICT (ticker) DO UPDATE SET data = %s, updated_at = NOW()
                    """, (ticker, data_json, data_json))
    except Exception as exc:
        logger.warning("PG save trend_positions failed: %s — fallback JSON", exc)
        # v8.4 fix C1-E2: Call JSON-only save directly to avoid infinite recursion.
        # Previously called _save_positions() which re-enters _pg_save_positions on PG path.
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


def _fetch_current_price(ticker: str, bypass_cache: bool = False) -> float | None:
    """Fetch the latest price for a ticker.

    P4 fix: log errors instead of silent pass.
    Returns (price, source) tuple is internal — callers get price only.
    """
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


class AgentTrader2(BaseAgent):
    """Agent Trader 2 — Trend follower sur commodities.

    Gère des positions de tendance (LONG/SHORT) sur 4 actifs.
    Change de direction quand les news de fond l'exigent.
    """

    name = "trader_2"
    description = "Trend trading — spéculateur commodities long terme"
    version = "8.1"  # v8.1: Fix fresh start detection — only reinit on truly empty DB, not on NEUTRAL positions from DB

    def __init__(self):
        super().__init__()
        self._position_changes_total: int = 0
        self._last_change_ticker: str | None = None
        self._last_change_direction: str | None = None
        self._evaluations_today: int = 0
        # P1: Initialize in __init__ so direct access never fails
        self._current_learning: dict = {}
        self._current_trend_scoring: dict = {}

    def run(self, scored_news=None, scan_type=None, learning_data=None,
            trend_scoring=None, **kwargs) -> dict:
        """Évalue les news scorées et met à jour les positions de tendance.

        Contrairement à Trader 1, cet agent ne passe pas d'ordres.
        Il maintient une vue de position (LONG/SHORT) sur chaque actif
        et change de direction quand les news de fond le justifient.

        Audit fixes v7.2:
        - P2: Global timeout 120s
        - P3: Parallel price fetch via ThreadPoolExecutor
        - J3: Guard entry_price > 0 before P&L calculation
        - J7: Track high/low watermarks per position

        Args:
            scored_news: list[ScoredNews] from Agent Scoring
            scan_type: ScanType enum
            learning_data: dict from Agent Learning 2 (optional)
            trend_scoring: dict from Agent Scoring 2 (optional)

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

            # Force initial LONG positions on TRUE fresh start only:
            # A fresh start is when _load_positions() returned {} (empty DB)
            # and all positions were just created by _init_position() above.
            # Do NOT reinitialize when positions loaded from DB happen to be NEUTRAL
            # (that erases accumulated history/confidence).
            freshly_created = all(
                positions[t].get("direction") == "NEUTRAL"
                and not positions[t].get("changes")  # No flip history = truly new
                and not positions[t].get("entry_time")  # No entry time = never traded
                for t in TREND_TICKERS
            )
            if freshly_created:
                self.log("Fresh start detected — initializing all positions to LONG", {
                    "tickers": list(TREND_TICKERS.keys()),
                }, level="DECISION")
                for ticker in TREND_TICKERS:
                    pos = positions[ticker]
                    price = pos.get("entry_price") or _fetch_current_price(ticker)
                    # Validate fresh start price
                    if price is not None:
                        is_valid, reason = validate_price(ticker, price)
                        if not is_valid:
                            self.log("Fresh start price rejected for %s: %s" % (ticker, reason),
                                     level="WARN")
                            price = None
                    pos["direction"] = "LONG"
                    pos["entry_price"] = price
                    pos["current_price"] = price
                    pos["entry_time"] = datetime.now(timezone.utc).isoformat()
                    pos["last_evaluation"] = datetime.now(timezone.utc).isoformat()
                    pos["reasoning"] = "Position initiale LONG — départ fresh start"
                    pos["confidence"] = 50

            # Filter relevant news for our 4 tickers
            all_scored = scored_news or []
            relevant_news = self._filter_relevant_news(all_scored)

            # Diagnostic: log why news were filtered out
            total_relevant = sum(len(v) for v in relevant_news.values())
            if total_relevant == 0 and all_scored:
                # Count filter stages for diagnosis (v7.5: no total_score filter)
                wrong_cat = sum(1 for sn in all_scored
                                if sn.news_category not in RELEVANT_CATEGORIES)
                neutral = sum(1 for sn in all_scored
                              if sn.news_category in RELEVANT_CATEGORIES
                              and sn.direction.value == "NEUTRAL")
                no_ticker = len(all_scored) - wrong_cat - neutral
                self.log("No relevant news for trend tickers", {
                    "total_scored": len(all_scored),
                    "filtered_wrong_category": wrong_cat,
                    "filtered_neutral": neutral,
                    "filtered_no_trend_ticker": no_ticker,
                    "trend_tickers": list(TREND_TICKERS.keys()),
                }, level="INFO")
            else:
                self.log("Relevant news found", {
                    "total_scored": len(all_scored),
                    "relevant_count": total_relevant,
                    "by_ticker": {k: len(v) for k, v in relevant_news.items()},
                })

            changes = []
            for ticker in TREND_TICKERS:
                # P2: Check global timeout
                elapsed = time.monotonic() - start
                if elapsed > GLOBAL_TIMEOUT_S:
                    self.log("Global timeout reached during evaluation",
                             {"elapsed_s": round(elapsed, 1)}, level="WARN")
                    break

                # Market hours check — only flip positions when the market is open
                from ..config import is_market_open
                if not is_market_open(ticker):
                    logger.debug("Trader 2: %s market closed, skipping", ticker)
                    continue

                ticker_news = relevant_news.get(ticker, [])

                # v8.0: Time-decay — if no news for this ticker, decay confidence
                if not ticker_news:
                    decay_change = self._apply_confidence_decay(ticker, positions[ticker])
                    if decay_change:
                        changes.append(decay_change)
                        positions[ticker] = decay_change["new_position"]
                        self._position_changes_total += 1
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

            # P3: Update prices in parallel for performance tracking
            self._update_prices_parallel(positions)

            # Save
            _save_positions(positions)

            # Build result BEFORE publish/log — an exception in publish/log
            # must never prevent the result from being returned to the pipeline.
            duration_ms = int((time.monotonic() - start) * 1000)
            result = {
                "positions": positions,
                "changes": changes,
                "news_evaluated": sum(len(v) for v in relevant_news.values()),
                "duration_ms": duration_ms,
            }

            # Publish + Log (best-effort, never blocks result)
            try:
                self._evaluations_today += 1
                self.publish("trend_update", {
                    "changes": changes,
                    "positions_count": len(positions),
                    "scan_type": scan_type.value if scan_type else None,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

                if changes:
                    for c in changes:
                        top_headline = ""
                        if c.get("key_news"):
                            top_headline = c["key_news"][0].get("title", "") if c["key_news"] else ""
                        self.log_decision(f"POSITION CHANGED — {TREND_TICKERS.get(c['ticker'], {}).get('name', c['ticker'])}", {
                            "ticker": c["ticker"],
                            "from": c["old_direction"],
                            "to": c["new_direction"],
                            "reason": c["reason"],
                            "close_pnl": c.get("close_pnl"),
                            "key_news": [
                                f"{n.get('title', '?')} [{n.get('category', '')}{'/' + n.get('zone') if n.get('zone') else ''}] (score:{n.get('score', '?')})"
                                for n in c.get("key_news", [])[:3]
                            ],
                        })
                else:
                    self.log("Trend evaluation complete — no changes", {
                        "news_evaluated": sum(len(v) for v in relevant_news.values()),
                        "tickers_with_news": len(relevant_news),
                    })
            except Exception as log_exc:
                logger.warning("Trader 2 publish/log failed (positions saved OK): %s", log_exc)

            action = (f"Changed: {', '.join(c['ticker'] for c in changes)}"
                      if changes else "No trend changes")
            self._set_status(AgentStatus.IDLE, action)

            return result

        except Exception as exc:
            self.log("Trend evaluation failed", {"error": str(exc)}, level="ERROR")
            self._set_status(AgentStatus.ERROR, str(exc))
            raise

    def _update_prices_parallel(self, positions: dict) -> None:
        """P3: Fetch current prices for all 4 tickers in parallel.

        J3: Guard entry_price > 0 before P&L calculation.
        J7: Track high/low watermarks for MAE/MFE fallback.
        """
        tickers = list(TREND_TICKERS.keys())
        prices: dict[str, float | None] = {}

        # T2-P18: Use shutdown(wait=False, cancel_futures=True) pattern
        executor = ThreadPoolExecutor(max_workers=4)
        try:
            futures = {
                executor.submit(_fetch_current_price, t): t
                for t in tickers
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
            for t in tickers:
                if t not in prices:
                    prices[t] = _fetch_current_price(t)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        now_iso = datetime.now(timezone.utc).isoformat()
        for ticker, price in prices.items():
            if price is None or ticker not in positions:
                continue
            # Validate price before storing — reject anomalous values
            is_valid, reason = validate_price(ticker, price)
            if not is_valid:
                logger.warning("T2 price update: rejected for %s — %s", ticker, reason)
                continue
            pos = positions[ticker]
            pos["current_price"] = price
            pos["last_price_update"] = now_iso

            # J7: Update high/low watermarks for MAE/MFE fallback
            high_wm = pos.get("high_watermark")
            low_wm = pos.get("low_watermark")
            pos["high_watermark"] = max(price, high_wm) if high_wm is not None else price
            pos["low_watermark"] = min(price, low_wm) if low_wm is not None else price

            # J3: Guard entry_price > 0 before P&L calculation
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
        # Validate price before storing — reject anomalous values
        if price is not None:
            is_valid, reason = validate_price(ticker, price)
            if not is_valid:
                logger.error("T2 init: rejected price for %s — %s", ticker, reason)
                price = None
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
            # v7.5: Do NOT filter by total_score here — Scoring 1's total_score
            # includes edge_factor (transmission_delay * market_awareness) designed
            # for intraday edge, which penalises signals still valid for trend
            # following. Scoring 2's MIN_TREND_SCORE is the proper quality gate.
            if sn.news_category not in RELEVANT_CATEGORIES:
                continue
            if sn.direction.value == "NEUTRAL":
                continue

            # Check if any of our tickers are impacted (direct OR chain, but not both)
            matched_tickers = set()
            for ticker in TREND_TICKERS:
                if ticker in sn.impacted_tickers:
                    matched_tickers.add(ticker)

            # Also check chain reactions (only if not already matched directly)
            for cr in (sn.chain_reactions or []):
                if cr.ticker in TREND_TICKERS:
                    matched_tickers.add(cr.ticker)

            for ticker in matched_tickers:
                result.setdefault(ticker, []).append(sn)

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

        # P1: Direct access (set in run() before _evaluate_ticker is called)
        learning = self._current_learning
        ticker_adj = learning.get("ticker_adj", {})
        newscat_adj = learning.get("newscat_adj", {})
        newscat_ticker_adj = learning.get("newscat_ticker_adj", {})
        direction_adj = learning.get("direction_adj", {})
        signal_cal = learning.get("signal_calibration", {})

        # P1: Direct access — Scoring 2 accumulation
        trend_scoring = self._current_trend_scoring
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
            long_score = trend_accumulation.get("long", 0)
            short_score = trend_accumulation.get("short", 0)
            # Apply per-ticker learning
            ticker_mult = ticker_adj.get(ticker, 1.0)
            long_score *= ticker_mult
            short_score *= ticker_mult
            # T2: Apply newscat_adj even when using Scoring 2 accumulations
            # Compute weighted newscat multiplier from contributing news
            newscat_mults = []
            for sn in news_list:
                # v7.7: zone+intensity lookup
                _intensity = ""
                if sn.expected_magnitude is not None:
                    if sn.expected_magnitude <= 33:
                        _intensity = "low"
                    elif sn.expected_magnitude >= 67:
                        _intensity = "high"
                found = False
                if sn.news_zone and _intensity:
                    zit_key = f"{sn.news_category}+{sn.news_zone}+{_intensity}+{ticker}"
                    if zit_key in newscat_ticker_adj:
                        newscat_mults.append(newscat_ticker_adj[zit_key])
                        found = True
                if not found and sn.news_zone:
                    zone_key = f"{sn.news_category}+{sn.news_zone}+{ticker}"
                    if zone_key in newscat_ticker_adj:
                        newscat_mults.append(newscat_ticker_adj[zone_key])
                        found = True
                if not found and _intensity:
                    int_key = f"{sn.news_category}+{_intensity}+{ticker}"
                    if int_key in newscat_ticker_adj:
                        newscat_mults.append(newscat_ticker_adj[int_key])
                        found = True
                if not found:
                    cross_key = f"{sn.news_category}+{ticker}"
                    if cross_key in newscat_ticker_adj:
                        newscat_mults.append(newscat_ticker_adj[cross_key])
                    elif sn.news_category in newscat_adj:
                        newscat_mults.append(newscat_adj[sn.news_category])
            if newscat_mults:
                avg_newscat = sum(newscat_mults) / len(newscat_mults)
                long_score *= avg_newscat
                short_score *= avg_newscat

        for sn in news_list:
            # P7: Determine direct impact once, used by both paths
            is_direct = ticker in sn.impacted_tickers
            chain_dir = None
            for cr in (sn.chain_reactions or []):
                if cr.ticker == ticker:
                    chain_dir = cr.direction.value if hasattr(cr.direction, "value") else str(cr.direction)

            if not use_trend_scoring:
                # Fallback: compute signal from Scoring 1 raw scores.
                # v7.9: Dampen Scoring 1 scores — they include edge_factor
                # (transmission_delay × market_awareness) designed for intraday,
                # which inflates weights and causes oscillation when used for trend.
                # Apply 0.4× damping so accumulated signals from Scoring 1
                # don't trivially exceed the flip threshold of 20.
                score = sn.total_score
                weight = score * (sn.signal_reliability / 100) * (sn.directional_clarity / 100) * 0.4

                # Apply learning 2 adjustments
                weight *= ticker_adj.get(ticker, 1.0)
                # v7.7: zone+intensity lookup
                _fb_intensity = ""
                if sn.expected_magnitude is not None:
                    if sn.expected_magnitude <= 33:
                        _fb_intensity = "low"
                    elif sn.expected_magnitude >= 67:
                        _fb_intensity = "high"
                nc_applied = False
                if sn.news_zone and _fb_intensity:
                    zit_key = f"{sn.news_category}+{sn.news_zone}+{_fb_intensity}+{ticker}"
                    if zit_key in newscat_ticker_adj:
                        weight *= newscat_ticker_adj[zit_key]
                        nc_applied = True
                if not nc_applied and sn.news_zone:
                    zone_key = f"{sn.news_category}+{sn.news_zone}+{ticker}"
                    if zone_key in newscat_ticker_adj:
                        weight *= newscat_ticker_adj[zone_key]
                        nc_applied = True
                if not nc_applied and _fb_intensity:
                    int_key = f"{sn.news_category}+{_fb_intensity}+{ticker}"
                    if int_key in newscat_ticker_adj:
                        weight *= newscat_ticker_adj[int_key]
                        nc_applied = True
                if not nc_applied:
                    cross_key = f"{sn.news_category}+{ticker}"
                    if cross_key in newscat_ticker_adj:
                        weight *= newscat_ticker_adj[cross_key]
                    else:
                        weight *= newscat_adj.get(sn.news_category, 1.0)

                if is_direct:
                    if sn.direction.value == "LONG":
                        long_score += weight
                    elif sn.direction.value == "SHORT":
                        short_score += weight
                elif chain_dir:
                    if chain_dir == "LONG":
                        long_score += weight * 0.7
                    elif chain_dir == "SHORT":
                        short_score += weight * 0.7

            # Always collect key_news for logging
            # J6: Include news published timestamp for freshness analysis
            published_iso = None
            if sn.news.published:
                try:
                    published_iso = sn.news.published.isoformat()
                except (AttributeError, TypeError):
                    pass

            key_news.append({
                "title": sn.news.title[:120],
                "score": round(sn.total_score, 1),
                "direction": sn.direction.value,
                "category": sn.news_category,
                "zone": sn.news_zone or "",
                "magnitude": sn.expected_magnitude,
                "reliability": sn.signal_reliability,
                "direct": is_direct,
                "published": published_iso,
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
        # v7.9: Hysteresis — positions held longer need stronger signals to flip.
        # Prevents churning when signals oscillate near the threshold.
        # Scale: 1.0× at 0h, up to 1.5× after 24h, capped at 1.5×
        # v8.0: Losing streak multiplier — if last N flips all losses, lower threshold
        base_threshold = 20.0 * signal_cal.get("threshold_adj", 1.0)
        entry_time_str = current_position.get("entry_time", "")
        hysteresis_mult = 1.0
        if entry_time_str:
            try:
                entry_dt = datetime.fromisoformat(entry_time_str)
                hours_held = (datetime.now(timezone.utc) - entry_dt).total_seconds() / 3600
                # Linear ramp: +0.5× over 24 hours, capped at 1.5×
                hysteresis_mult = min(1.5, 1.0 + 0.5 * min(hours_held, 24) / 24)
            except (ValueError, TypeError):
                pass
        # v8.0: Losing streak → lower threshold to exit bad direction faster
        streak_mult = self._check_losing_streak(current_position.get("ticker", ""), current_position)
        change_threshold = base_threshold * hysteresis_mult * streak_mult
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

    def _apply_confidence_decay(self, ticker: str, position: dict) -> dict | None:
        """v8.0: Decay confidence when no news confirm the position.

        If no news for this ticker in the current scan, check how long since
        the last evaluation. After CONFIDENCE_DECAY_START_DAYS, reduce
        confidence by CONFIDENCE_DECAY_PER_DAY per day elapsed.

        If confidence drops below CONFIDENCE_CLOSE_THRESHOLD, close the
        position back to NEUTRAL — the signal has gone stale.

        Returns a change dict (position closed) or None (just decayed).
        """
        direction = position.get("direction", "NEUTRAL")
        if direction == "NEUTRAL":
            return None

        confidence = position.get("confidence", 50)
        last_eval = position.get("last_evaluation")
        if not last_eval:
            return None

        try:
            last_eval_dt = datetime.fromisoformat(last_eval)
        except (ValueError, TypeError):
            return None

        days_silent = (datetime.now(timezone.utc) - last_eval_dt).total_seconds() / 86400

        if days_silent < CONFIDENCE_DECAY_START_DAYS:
            return None  # Grace period

        # Decay: points lost = rate × (days beyond grace period)
        decay_days = days_silent - CONFIDENCE_DECAY_START_DAYS
        decay_amount = int(CONFIDENCE_DECAY_PER_DAY * decay_days)
        new_confidence = max(0, confidence - decay_amount)
        position["confidence"] = new_confidence
        position["reasoning"] = (
            f"Confiance en déclin — aucune news depuis {days_silent:.1f}j "
            f"(confiance {confidence} → {new_confidence})"
        )

        if new_confidence < CONFIDENCE_CLOSE_THRESHOLD:
            # Close the position — signal is stale
            self.log_decision(f"TIME DECAY CLOSE — {ticker}", {
                "ticker": ticker,
                "direction": direction,
                "days_silent": round(days_silent, 1),
                "confidence_was": confidence,
                "confidence_now": new_confidence,
                "threshold": CONFIDENCE_CLOSE_THRESHOLD,
            })
            return self._make_change(
                ticker, position, "NEUTRAL",
                f"Fermeture time-decay — aucune news depuis {days_silent:.1f}j, "
                f"confiance {new_confidence} < seuil {CONFIDENCE_CLOSE_THRESHOLD}",
                [], 0.0,
            )

        logger.info("T2 confidence decay: %s %s → %d (silent %.1f days)",
                     ticker, direction, new_confidence, days_silent)
        return None

    def _check_losing_streak(self, ticker: str, position: dict) -> float:
        """v8.0: Check if ticker has a losing streak → lower flip threshold.

        If the last LOSING_STREAK_FLIP_COUNT flips were all losses (pnl < 0),
        return a multiplier < 1.0 that reduces the change_threshold,
        making it easier to flip direction.

        Returns multiplier (1.0 = normal, LOSING_STREAK_THRESHOLD_MULT on streak).
        """
        history = position.get("history", [])
        if len(history) < LOSING_STREAK_FLIP_COUNT:
            return 1.0

        # Check last N flips (most recent first)
        recent = history[:LOSING_STREAK_FLIP_COUNT]
        all_losses = all(
            h.get("pnl_pct", 0) < 0
            for h in recent
            if h.get("from_direction", "NEUTRAL") != "NEUTRAL"
        )

        # Need at least LOSING_STREAK_FLIP_COUNT actual non-NEUTRAL flips
        actual_flips = [h for h in recent if h.get("from_direction", "NEUTRAL") != "NEUTRAL"]
        if len(actual_flips) < LOSING_STREAK_FLIP_COUNT:
            return 1.0

        if all_losses:
            logger.warning("T2 losing streak on %s: last %d flips all negative → "
                           "lowering flip threshold (×%.1f)",
                           ticker, LOSING_STREAK_FLIP_COUNT, LOSING_STREAK_THRESHOLD_MULT)
            return LOSING_STREAK_THRESHOLD_MULT

        return 1.0

    def _make_change(self, ticker: str, position: dict, new_dir: str,
                     reason: str, key_news: list, strength: float) -> dict | None:
        """Create a position change record and update position state.

        Audit fixes v7.2:
        - P8: Guard entry_price > 0 before P&L calculation
        - P9: Temporal pruning (1 year) instead of hard cap 50
        - J2: Store trend_scoring snapshot in flip history_entry
        - J7: Reset high/low watermarks on new position
        """
        old_dir = position.get("direction", "NEUTRAL")
        old_entry = position.get("entry_price")
        current_price = position.get("current_price") or _fetch_current_price(ticker)

        # Guard: cannot open a position without a valid price
        if not current_price:
            logger.warning("Cannot change position for %s — no price available", ticker)
            return None

        # Validate price before using as new entry_price
        is_valid, reason = validate_price(ticker, current_price)
        if not is_valid:
            logger.error("T2 flip: rejected price for %s — %s", ticker, reason)
            return None

        # P8: Calculate realized P&L with entry_price > 0 guard
        close_pnl = 0.0
        if old_dir != "NEUTRAL" and old_entry and old_entry > 0 and current_price:
            if old_dir == "LONG":
                close_pnl = round((current_price - old_entry) / old_entry * 100, 2)
            else:
                close_pnl = round((old_entry - current_price) / old_entry * 100, 2)

        # J2: Capture trend scoring snapshot at flip time (not after)
        trend_scoring_snapshot = None
        trend_scoring = self._current_trend_scoring
        if trend_scoring:
            ticker_acc = trend_scoring.get("accumulation", {}).get(ticker, {})
            if ticker_acc:
                trend_scoring_snapshot = {
                    "long": round(ticker_acc.get("long", 0), 1),
                    "short": round(ticker_acc.get("short", 0), 1),
                    "net": round(ticker_acc.get("long", 0) - ticker_acc.get("short", 0), 1),
                }

        # v7.6: Extract news zones from key news for zone-aware learning
        news_zones = [n.get("zone", "") for n in key_news if n.get("zone")]
        # v7.7: Compute average magnitude for intensity tier
        magnitudes = [n.get("magnitude") for n in key_news if n.get("magnitude") is not None]
        avg_magnitude = round(sum(magnitudes) / len(magnitudes)) if magnitudes else None
        intensity = ""
        if avg_magnitude is not None:
            if avg_magnitude <= 33:
                intensity = "low"
            elif avg_magnitude >= 67:
                intensity = "high"

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
            # Store position entry_time for Journal 2 MAE/MFE
            "position_entry_time": position.get("entry_time"),
            # v7.6: Primary zone for zone-aware learning
            "news_zones": news_zones[:3],
            # v7.7: Intensity tier for magnitude-aware learning
            "intensity": intensity,
        }
        # J2: Include trend scoring snapshot if available
        if trend_scoring_snapshot:
            history_entry["trend_scoring"] = trend_scoring_snapshot
        # V1: Stamp flip with agent versions
        history_entry["agent_versions"] = self._get_agent_versions()

        # P9: Temporal pruning — keep entries from last HISTORY_MAX_AGE_DAYS
        history = position.get("history", [])
        history.insert(0, history_entry)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=HISTORY_MAX_AGE_DAYS)).isoformat()
        history = [h for h in history if (h.get("time", "") > cutoff or h.get("time", "") == "")]
        # Safety cap at 100 (should never be reached with 1-year pruning)
        history = history[:100]

        now_iso = datetime.now(timezone.utc).isoformat()

        # Build new position
        new_position = {
            "ticker": ticker,
            "name": position.get("name", TREND_TICKERS.get(ticker, {}).get("name", ticker)),
            "category": position.get("category", TREND_TICKERS.get(ticker, {}).get("category", "")),
            "direction": new_dir,
            "entry_price": current_price,
            "current_price": current_price,
            "unrealized_pnl_pct": 0.0,
            "entry_time": now_iso,
            "last_evaluation": now_iso,
            "last_price_update": now_iso,
            "reasoning": reason,
            "key_catalysts": key_news[:5],
            "confidence": min(100, int(strength * 2)),
            "history": history,
            "total_switches": position.get("total_switches", 0) + 1,
            "realized_pnl_pct": round(
                position.get("realized_pnl_pct", 0.0) + close_pnl, 2),
            # J7: Reset watermarks for new position
            "high_watermark": current_price,
            "low_watermark": current_price,
            # V1: Stamp with agent versions for version-aware learning/performance
            "agent_versions": self._get_agent_versions(),
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

    def _get_agent_versions(self) -> dict:
        """V1: Get current agent versions for position stamping."""
        try:
            from .registry import get_all_agents
            agents = get_all_agents()
            return {name: getattr(a, "version", "?") for name, a in agents.items()}
        except Exception:
            return {"trader_2": self.version}

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
