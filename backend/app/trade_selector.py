"""Selects the best trade from scored news and calibrates entry/TP/SL."""

import logging
import time
from datetime import datetime, timezone, timedelta

from .market_data import fetch_history, validate_price

from .config import (
    ASSET_BY_TICKER,
    BASE_POSITION_SIZE_PCT,
    CORRELATION_GROUPS,
    DEFAULT_SPREAD,
    ESTIMATED_SPREADS,
    KELLY_FRACTION,
    MAX_POSITION_SIZE_PCT,
    MAX_TRADES_PER_DAY,
    MIN_POSITION_SIZE_PCT,
    MIN_RISK_REWARD,
    MIN_SCORE_THRESHOLD,
    NEWS_CATEGORY_MULTIPLIERS,
    TARGET_PERCENT,
    assets_for_session,
    is_market_open,
)

# ── Pre-move thresholds by asset category ──────────────────────────
# Percentage of expected move that constitutes "already priced"
# Forex: tight (0.3% overnight is normal), Commodities: loose (2-3% moves are common)
PRE_MOVE_THRESHOLDS: dict[str, float] = {
    "forex": 0.6,       # 60% of expected move — forex has small ATR, overnight gap is normal
    "commodities_energy": 0.85,     # 85% — energy can move 3%+ on real signals
    "commodities_agri": 0.85,       # 85% — grains can gap hard on USDA/weather
    "commodities_soft": 0.85,       # 85% — tropical softs very volatile
    "commodities_industrial": 0.8,  # 80% — copper tracks China/macro
    "commodities_livestock": 0.85,  # 85% — livestock gaps on disease outbreaks
    "metaux": 0.8,       # 80% — metals are volatile but less than soft commodities
    "actions_europe": 0.8,  # 80% — stocks gap on earnings etc
    "indices": 0.75,     # 75% — indices reflect broad sentiment, pre-move is more informative
}
from .economic_calendar import check_event_conflict, get_events_context
from .models import (
    Direction,
    ScanResult,
    ScanType,
    ScoredNews,
    TradeRecommendation,
)

logger = logging.getLogger(__name__)

# T1-P5: Per-scan trade cache to avoid repeated load_trades() calls
# Reset at the start of each select_trade() call
# v8.4 fix P3-E1: Thread-safe cache via threading.local() instead of module-level global.
# Concurrent scans (event scanner + scheduled) could corrupt a shared global cache.
import threading as _ts_threading
_ts_local = _ts_threading.local()


def _get_trades_cached() -> list:
    """Return cached trades list (loaded once per select_trade call).

    v8.4 fix P3-E1: Uses thread-local storage to prevent concurrent scan corruption.
    """
    cached = getattr(_ts_local, "cached_trades", None)
    if cached is not None:
        return cached
    try:
        from .learning import load_trades
        _ts_local.cached_trades = load_trades()
    except Exception as exc:
        logger.warning("Failed to load trades: %s", exc)
        _ts_local.cached_trades = []
    return _ts_local.cached_trades


# Cross-day dedup: don't trade the same ticker within this many days
RECENT_TRADE_COOLDOWN_DAYS = 3

# P1-4: Adaptive cooldown — persistent physical signals (weather, supply_chain)
# get shorter cooldowns because the signal genuinely persists across days.
# A drought worsening daily = valid re-entry, not a duplicate.
CATEGORY_COOLDOWN_DAYS: dict[str, int] = {
    "weather": 1,        # Weather events persist — re-entry after 1 day
    "supply_chain": 1,   # Supply disruptions persist
    "commodity": 2,      # Physical commodity signals — moderate persistence
}


def _get_recently_traded_tickers(cooldown_days: int = RECENT_TRADE_COOLDOWN_DAYS) -> set[str]:
    """Return tickers traded in the last N days (to avoid repeating the same trade).

    T1-P5: Uses cached trades (loaded once per select_trade call).
    """
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=cooldown_days)
        trades = _get_trades_cached()
        return {
            t.ticker
            for t in trades
            if t.timestamp >= cutoff
        }
    except Exception as exc:
        logger.warning("Failed to load recent trades for cross-day dedup: %s", exc)
        return set()


def _get_recently_traded_tickers_by_category(news_category: str) -> set[str]:
    """Return tickers traded recently, with category-adaptive cooldown (P1-4).

    Weather/supply_chain signals persist across days — use shorter cooldown
    to allow re-entry on genuinely persistent signals.
    """
    cooldown = CATEGORY_COOLDOWN_DAYS.get(news_category, RECENT_TRADE_COOLDOWN_DAYS)
    return _get_recently_traded_tickers(cooldown_days=cooldown)


def _check_reentry_eligible(ticker: str, news_category: str) -> bool:
    """P2-6: Check if a stopped trade is eligible for re-entry.

    If a trade was stopped out today but the fundamental signal is still active
    (weather, supply_chain categories), allow re-entry with the next scan.
    This prevents missing multi-day moves after a false stop-out.
    """
    if news_category not in ("weather", "supply_chain", "commodity"):
        return False
    try:
        from .models import TradeResult
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        trades = _get_trades_cached()
        for t in trades:
            if t.ticker != ticker:
                continue
            if t.timestamp.strftime("%Y-%m-%d") != today:
                continue
            # Only allow re-entry if the trade was stopped (not TP hit — signal exhausted)
            if t.result == TradeResult.SL_HIT:
                logger.info("P2-6: Re-entry eligible for %s (stopped today, signal category: %s)",
                            ticker, news_category)
                return True
    except Exception:
        pass
    return False


def _count_today_trades() -> int:
    """Count how many trades were already taken today (for daily cap).

    T1-P15: Uses cached trades (loaded once per select_trade call).
    """
    try:
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        trades = _get_trades_cached()
        return sum(1 for t in trades if t.timestamp.strftime("%Y-%m-%d") == today)
    except Exception as exc:
        logger.warning("Failed to count today's trades: %s", exc)
        return 0


TIME_WINDOWS = {
    ScanType.EUROPE: "09:00 — 20:00",
    ScanType.US: "15:30 — 20:00",
}

# (#24) Binary event keywords — only truly binary decisions
# Note: the calendar system already blocks trades near these events.
# This is a secondary safety net for news that slips through.
BINARY_EVENT_KEYWORDS = [
    "fomc", "nfp", "non-farm", "payrolls",
    "rate decision", "taux directeur",
    "ecb rate", "boe rate", "fed rate",
]


def _get_price_and_range(ticker: str, days: int = 20) -> tuple[float | None, float, float | None, float | None, float | None]:
    """Fetch current price, true ATR (#11), previous close, volume info, and today's open.

    Returns (current_price, true_atr_pct, prev_close, avg_volume_ratio, today_open).
    avg_volume_ratio = today's volume / 20d avg volume (None if unavailable).
    today_open = today's opening price for intraday pre-move detection (v4.0 B5).
    """
    try:
        data = fetch_history(ticker, period_days=days + 5, interval="1day")
        if data is None or data.empty:
            return None, 1.5, None, None, None
        current_price = float(data["Close"].iloc[-1])
        if len(data) < 5:
            return current_price, 1.5, None, None, None

        # (#11) True ATR (Wilder): max(H-L, |H-prev_close|, |L-prev_close|)
        highs = data["High"].values
        lows = data["Low"].values
        closes = data["Close"].values
        true_ranges = []
        for i in range(1, len(data)):
            hl = highs[i] - lows[i]
            hpc = abs(highs[i] - closes[i - 1])
            lpc = abs(lows[i] - closes[i - 1])
            true_ranges.append(max(hl, hpc, lpc))

        # v4.0 C1: Use 5-day ATR (recent volatility) instead of 20-day for more reactive calibration
        recent_atr_days = min(5, len(true_ranges))
        recent_tr = true_ranges[-recent_atr_days:] if true_ranges else []
        avg_true_range = sum(recent_tr) / len(recent_tr) if recent_tr else 0
        true_atr_pct = (avg_true_range / current_price * 100) if current_price > 0 else 1.5

        # Previous close for "news déjà pricée" detection (#4)
        prev_close = float(closes[-2]) if len(closes) >= 2 else None

        # v4.0 B5: Today's open for intraday pre-move detection
        today_open = float(data["Open"].iloc[-1]) if "Open" in data.columns else None

        # (#10) Volume ratio — None if no volume data (don't block the trade)
        volume_ratio = None
        if "Volume" in data.columns:
            volumes = data["Volume"].values
            avg_vol = float(volumes[-days:].mean()) if len(volumes) >= days else float(volumes.mean())
            today_vol = float(volumes[-1])
            # v5.1: Minimum volume floor — ignore if avg < 100 (forex/indices return 0)
            if avg_vol >= 100 and today_vol > 0:
                volume_ratio = today_vol / avg_vol
            elif avg_vol < 100 and today_vol >= 0:
                logger.debug("Volume data sparse/unavailable for %s (avg=%.0f) — skipping volume confirmation", ticker, avg_vol)

        return current_price, round(true_atr_pct, 4), prev_close, volume_ratio, today_open
    except Exception as exc:
        logger.warning("Price/range fetch failed for %s: %s", ticker, exc)
        return None, 1.5, None, None, None


def _detect_pre_move(current_price: float, prev_close: float | None, target_move_expected: float) -> float | None:
    """Detect if news is already priced in (#4).

    Returns pre_move_pct if detectable, None otherwise.
    """
    if prev_close is None or prev_close == 0:
        return None
    pre_move = (current_price - prev_close) / prev_close * 100
    return round(pre_move, 4)


def _check_binary_event(news_title: str, reasoning: str) -> str | None:
    """Check if the news relates to a binary event (#24)."""
    combined = (news_title + " " + reasoning).lower()
    for keyword in BINARY_EVENT_KEYWORDS:
        if keyword in combined:
            return f"Evenement binaire detecte: '{keyword}' — risque de volatilite extreme"
    return None


# v4.0 D3: Cache dynamic correlation results (TTL 1h) to avoid redundant yfinance calls
import threading as _corr_threading
_corr_cache: dict[str, tuple[float | None, float]] = {}  # key -> (correlation, timestamp)
_corr_cache_lock = _corr_threading.Lock()  # T1-P9: Thread safety for concurrent scans
_CORR_CACHE_TTL = 3600  # 1 hour
_CORR_CACHE_MAX_SIZE = 1000  # v8.4 fix P13-E1: evict oldest entries to prevent unbounded growth


def _compute_dynamic_correlation(ticker1: str, ticker2: str, lookback: int = 20) -> float | None:
    """Compute 20-day rolling correlation between two tickers (G. dynamic correlation).

    v4.0 D3: Results are cached for 1h to avoid redundant API calls.
    v6.5 P9: fetch_history calls wrapped with 10s timeout each to prevent
    silent hangs that block the entire scan pipeline.
    Returns correlation coefficient (-1 to 1), or None if data unavailable.
    """
    _time = time  # T1-P17: Use module-level import
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
    cache_key = f"{min(ticker1, ticker2)}:{max(ticker1, ticker2)}"
    with _corr_cache_lock:
        cached = _corr_cache.get(cache_key)
        if cached is not None:
            corr_val, cached_at = cached
            if _time.time() - cached_at < _CORR_CACHE_TTL:
                return corr_val

    try:
        # v6.5 P9: Use ThreadPoolExecutor with timeout to prevent indefinite blocking
        # on fetch_history calls (root cause of 11:33 CET pipeline hang)
        executor = ThreadPoolExecutor(max_workers=2)
        try:
            f1 = executor.submit(fetch_history, ticker1, period_days=lookback + 5, interval="1day")
            f2 = executor.submit(fetch_history, ticker2, period_days=lookback + 5, interval="1day")
            data1 = f1.result(timeout=10)
            data2 = f2.result(timeout=10)
        except (TimeoutError, FuturesTimeoutError):
            logger.warning("Dynamic correlation TIMEOUT for %s vs %s (10s)", ticker1, ticker2)
            with _corr_cache_lock:
                _corr_cache[cache_key] = (None, _time.time())
            return None
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        if data1 is None or data2 is None or len(data1) < lookback or len(data2) < lookback:
            return None
        returns1 = data1["Close"].pct_change().dropna().tail(lookback)
        returns2 = data2["Close"].pct_change().dropna().tail(lookback)
        if len(returns1) < 10 or len(returns2) < 10:
            return None
        # Align by date
        common = returns1.index.intersection(returns2.index)
        if len(common) < 10:
            return None
        corr = returns1.loc[common].corr(returns2.loc[common])
        result = round(corr, 3) if corr == corr else None  # NaN check
        with _corr_cache_lock:
            _corr_cache[cache_key] = (result, _time.time())
            # v8.4 fix P13-E1: evict oldest entries if cache exceeds max size
            if len(_corr_cache) > _CORR_CACHE_MAX_SIZE:
                oldest_key = min(_corr_cache, key=lambda k: _corr_cache[k][1])
                del _corr_cache[oldest_key]
        return result
    except Exception:
        return None


DYNAMIC_CORRELATION_THRESHOLD = 0.7  # Block if rolling correlation > 0.7


def _check_correlation(ticker: str, existing_trade_tickers: list[str] | str | None) -> bool:
    """Check if a ticker is correlated with ANY existing trade (#22).

    Accepts either a single ticker (backward compat) or a list of all pending tickers.
    Returns True if correlated (should avoid). Also blocks exact same ticker
    even if not in any correlation group.
    """
    if not existing_trade_tickers:
        return False
    # Normalize to list
    if isinstance(existing_trade_tickers, str):
        tickers_to_check = [existing_trade_tickers]
    else:
        tickers_to_check = existing_trade_tickers

    # Direct same-ticker check (even if not in any correlation group)
    if ticker in tickers_to_check:
        return True

    for existing in tickers_to_check:
        # Static correlation group check (fast)
        ticker_in_any_group = False
        existing_in_any_group = False
        for group_tickers in CORRELATION_GROUPS.values():
            if ticker in group_tickers and existing in group_tickers:
                return True  # Same static group → correlated
            if ticker in group_tickers:
                ticker_in_any_group = True
            if existing in group_tickers:
                existing_in_any_group = True

        # P3-6: Only run dynamic check if BOTH tickers are not already covered by static groups.
        # If both appear in static groups (even different ones), the groups already capture
        # their correlation profile — no need for expensive yfinance rolling correlation.
        if not (ticker_in_any_group and existing_in_any_group):
            _corr_time = time  # T1-P17: Use module-level import
            _corr_start = _corr_time.monotonic()
            dyn_corr = _compute_dynamic_correlation(ticker, existing)
            _corr_elapsed = _corr_time.monotonic() - _corr_start
            if _corr_elapsed > 5.0:
                logger.warning("Dynamic correlation slow: %s vs %s took %.1fs",
                               ticker, existing, _corr_elapsed)
            if dyn_corr is not None and abs(dyn_corr) > DYNAMIC_CORRELATION_THRESHOLD:
                logger.info("Dynamic correlation block: %s vs %s = %.3f (threshold: %.1f)",
                            ticker, existing, dyn_corr, DYNAMIC_CORRELATION_THRESHOLD)
                return True
    return False


def _calibrate_trade(
    direction: Direction,
    entry_price: float,
    avg_range: float,
    score: float,
    news_category: str = "other",
    ticker: str = "",
    expected_magnitude: int = 50,
) -> tuple[float, float, float, float, float]:
    """Calculate target, stop, percentages and risk/reward.

    Target is score-weighted: higher score → bigger target as fraction of ATR.
    Stop is a fixed fraction of ATR, independent of target.
    News category multipliers (#7) adjust target/stop based on event type.
    v4.0: expected_magnitude from Claude scales the target (B1).
    v4.0: C4 convex score factor — exponential instead of linear.
    v4.0: C3 stop floor adapts to estimated spread.

    Returns (target_price, stop_price, target_pct, stop_pct, risk_reward).
    """
    # v4.0 C4: Convex score factor — exponential response gives bigger edge to high scores
    # Old: linear 0.25 + (score/100) * 0.45 → ratio high/low = 1.8x
    # New: exponential → ratio high/low = ~3x (rewards conviction, punishes mediocrity)
    normalized_score = score / 100
    if avg_range < 1.0:
        score_factor = 0.30 + 0.60 * (normalized_score ** 1.5)
        stop_fraction = 0.5
    elif avg_range > 5.0:
        score_factor = 0.12 + 0.33 * (normalized_score ** 1.5)
        stop_fraction = 0.3
    else:
        score_factor = 0.20 + 0.50 * (normalized_score ** 1.5)
        stop_fraction = 0.4

    # v4.0 B1: Scale target by expected_magnitude from Claude
    # Magnitude 50 = neutral (1.0x), magnitude 100 = 1.3x, magnitude 0 = 0.7x
    magnitude_factor = 0.7 + 0.6 * (expected_magnitude / 100)
    score_factor *= magnitude_factor

    target_move_pct = max(TARGET_PERCENT, avg_range * score_factor)

    # Stop: fraction of ATR adapted to volatility, independent of target
    # v4.0 C3: Stop floor = max(old floor, 2x estimated spread) — prevents getting stopped by bid-ask noise
    spread = ESTIMATED_SPREADS.get(ticker, DEFAULT_SPREAD)
    stop_floor = max(TARGET_PERCENT * 0.7, spread * 2.0)
    stop_move_pct = max(stop_floor, avg_range * stop_fraction)

    # (#7) Apply news category multipliers
    cat_mults = NEWS_CATEGORY_MULTIPLIERS.get(news_category, {"target_mult": 1.0, "stop_mult": 1.0})
    target_move_pct *= cat_mults["target_mult"]
    stop_move_pct *= cat_mults["stop_mult"]

    # v4.0 C2: Asymmetric SHORT stop — commodities/metals can spike 10%+ in 1h,
    # so SHORT stops need to be wider to absorb adverse spikes
    if direction == Direction.SHORT:
        stop_move_pct *= 1.2  # 20% wider stop for shorts

    if direction == Direction.LONG:
        target_price = entry_price * (1 + target_move_pct / 100)
        stop_price = entry_price * (1 - stop_move_pct / 100)
    else:
        target_price = entry_price * (1 - target_move_pct / 100)
        stop_price = entry_price * (1 + stop_move_pct / 100)

    risk_reward = round(target_move_pct / stop_move_pct, 2) if stop_move_pct > 0 else 0

    return (
        round(target_price, 4),
        round(stop_price, 4),
        round(target_move_pct, 2),
        round(stop_move_pct, 2),
        risk_reward,
    )


def _compute_position_size(
    score: float, confidence: int, rr: float,
    market_context: dict | None = None, day_of_week: int | None = None,
) -> float:
    """Compute position size as % of capital using Kelly-inspired formula.

    Higher score + higher R/R = larger position. Capped at MAX_POSITION_SIZE_PCT.
    Uses quarter-Kelly for safety.

    P2-7: Dynamic VIX-based regime sizing — reduce in stress, increase in calm.
    P3-7: Friday risk management — reduce position Friday afternoon.

    Formula: base_size * (score/100) * kelly_fraction * min(rr/1.5, 2.0)
    """
    score_factor = score / 100.0
    rr_factor = min(rr / 1.5, 2.0)  # R/R boost, capped at 2x
    size = BASE_POSITION_SIZE_PCT * score_factor * KELLY_FRACTION * rr_factor
    # Additional confidence scaling
    size *= (confidence / 100.0)

    # P2-7: Volatility regime adjustment
    if market_context:
        regime = market_context.get("regime", "normal")
        vix = market_context.get("vix")
        if regime == "stress" or (vix and vix >= 30):
            size *= 0.5   # Halve position in high-vol regime
            logger.info("Position size reduced 50%% — stress regime (VIX=%.1f)", vix or 0)
        elif regime == "elevated" or (vix and vix >= 20):
            size *= 0.75  # Reduce 25% in elevated regime
        elif regime == "calm" or (vix and vix <= 13):
            size *= 1.2   # Boost 20% in calm regime (trend more reliable)

    # P3-7: Friday risk management — reduce position size Friday
    # Weekend gap risk: markets can gap 1-3% on Monday open
    if day_of_week == 4:  # Friday
        from zoneinfo import ZoneInfo
        now_paris = datetime.now(ZoneInfo("Europe/Paris"))
        if now_paris.hour >= 14:  # Friday afternoon: even more aggressive reduction
            size *= 0.6
            logger.info("Position size reduced 40%% — Friday afternoon (weekend gap risk)")
        else:
            size *= 0.8
            logger.info("Position size reduced 20%% — Friday (weekend gap risk)")

    return round(max(MIN_POSITION_SIZE_PCT, min(MAX_POSITION_SIZE_PCT, size)), 2)


def _build_scored_news_log(scored_news: list[ScoredNews]) -> list[dict]:
    """Build a serializable log of all scored news for journal tracing.

    v5.1: Now stores description, url, published, source_weight for backtest replay.
    These fields enable re-scoring historical headlines with updated parameters.
    """
    log = []
    for sn in scored_news:
        entry = {
            "title": sn.news.title,
            "source": sn.news.source,
            "direction": sn.direction.value,
            "total_score": sn.total_score,
            "surprise": sn.surprise,
            "freshness": sn.freshness,
            "directional_clarity": sn.directional_clarity,
            "transmission_delay": sn.transmission_delay,
            "market_awareness": sn.market_awareness,
            "expected_magnitude": sn.expected_magnitude,
            "signal_reliability": sn.signal_reliability,
            "news_category": sn.news_category,
            "category_score_mult": sn.category_score_mult,
            "impacted_tickers": sn.impacted_tickers,
            "reasoning": sn.reasoning,
            # v5.1: Backtest-critical fields — enable historical replay
            "description": sn.news.description or "",
            "url": sn.news.url or "",
            "published": sn.news.published.isoformat() if sn.news.published else None,
            "source_weight": sn.news.source_weight,
        }
        log.append(entry)
    return log


def _get_agent_versions() -> dict:
    """Collect current agent versions for trade stamping."""
    try:
        from .agents.registry import get_all_agents
        agents = get_all_agents()
        return {name: agent.version for name, agent in agents.items()}
    except Exception as exc:
        logger.warning("Failed to collect agent versions: %s", exc)
        return {}


def select_trade(
    scored_news: list[ScoredNews],
    scan_type: ScanType,
    learning_adjustments: dict | None = None,
    existing_trade_ticker: list[str] | str | None = None,
    market_context: dict | None = None,
) -> ScanResult:
    """Backward-compatible wrapper — calls select_trades() internally."""
    return select_trades(
        scored_news, scan_type, learning_adjustments,
        existing_trade_ticker=existing_trade_ticker,
        market_context=market_context,
    )


def select_trades(
    scored_news: list[ScoredNews],
    scan_type: ScanType,
    learning_adjustments: dict | None = None,
    existing_trade_ticker: list[str] | str | None = None,
    market_context: dict | None = None,
) -> ScanResult:
    """Select all valid trades from scored news (v3.5 — multi-trade).

    No per-scan cap: all candidates that pass filtering are selected.
    Daily cap (MAX_TRADES_PER_DAY) limits total exposure across all scans.
    Intra-scan correlation prevents selecting correlated tickers in the same scan.

    v3.4: learning_adjustments is now a dict with sub-keys:
      - "adjustments": per-ticker base multipliers
      - "session_adj": per-scan-type multipliers (applied by current scan)
      - "newscat_adj": per-news-category multipliers (applied by current news)
      - "regime_adj": per-VIX-regime multipliers (applied by current market)
      Also backward-compatible with old format (flat dict of ticker -> float).

    Args:
        scored_news: News scored by the LLM, sorted by total_score desc.
        scan_type: europe or us scan.
        learning_adjustments: Learning data from compute_learning_adjustments().
        existing_trade_ticker: Tickers from other scans (for correlation check #22).
        market_context: Market context from news_scorer (#5).

    Returns:
        ScanResult with recommendations list (0..N trades).
    """
    # T1-P5: Reset per-scan trade cache (avoids loading trades N times per scan)
    # v8.4 fix P3-E1: Thread-local instead of global — safe for concurrent scans
    _ts_local.cached_trades = None

    now = datetime.now(timezone.utc)

    # v3.4: Unpack the structured learning data (backward-compatible with flat dict)
    if isinstance(learning_adjustments, dict) and "adjustments" in learning_adjustments:
        ticker_adj = learning_adjustments.get("adjustments", {})
        session_adj = learning_adjustments.get("session_adj", {})
        newscat_adj = learning_adjustments.get("newscat_adj", {})
        regime_adj = learning_adjustments.get("regime_adj", {})
        direction_adj = learning_adjustments.get("direction_adj", {})
        delay_bias_adj = learning_adjustments.get("delay_bias_adj", 1.0)
        learning_state_for_log = learning_adjustments
    elif isinstance(learning_adjustments, dict):
        # Backward compat: old flat format {ticker: multiplier}
        ticker_adj = learning_adjustments
        session_adj = {}
        newscat_adj = {}
        regime_adj = {}
        direction_adj = {}
        delay_bias_adj = 1.0
        learning_state_for_log = learning_adjustments
    else:
        ticker_adj = {}
        session_adj = {}
        newscat_adj = {}
        regime_adj = {}
        direction_adj = {}
        delay_bias_adj = 1.0
        learning_state_for_log = None

    # v3.4 #2: Session multiplier from CURRENT scan type (not last trade)
    current_session_mult = session_adj.get(scan_type.value, 1.0)

    # v3.4 #1 + v4.2 C1: Regime multiplier — use merged buckets
    current_regime = market_context.get("regime", "normal") if market_context else "normal"
    # C1: Map 4-regime to 2-bucket for lookup
    if current_regime in ("calm", "normal"):
        regime_lookup = "low_vol"
    else:
        regime_lookup = "high_vol"
    current_regime_mult = regime_adj.get(regime_lookup, 1.0)

    # Build the full scored news log for journal (all Claude reasoning)
    all_scored_log = _build_scored_news_log(scored_news) if scored_news else None
    rejection_log: list[dict] = []

    if not scored_news:
        return ScanResult(
            scan_type=scan_type,
            timestamp=now,
            has_trade=False,
            reason_no_trade="Aucune news collectée",
            news_analyzed=0,
            market_context=market_context,
            learning_state=learning_state_for_log,
        )

    # ── Calendar check: block trades near major macro events ──────
    event_conflict = check_event_conflict()
    if event_conflict:
        logger.warning(
            "TRADE BLOCKED: %s imminent — no edge on macro events",
            event_conflict.name,
        )
        return ScanResult(
            scan_type=scan_type,
            timestamp=now,
            has_trade=False,
            reason_no_trade=f"Événement macro imminent : {event_conflict.name} — zéro edge, trade bloqué",
            news_analyzed=len(scored_news),
            market_context=market_context,
            all_scored_news=all_scored_log,
            learning_state=learning_state_for_log,
        )

    # ── Daily cap check — v4.0 D4: adaptive to VIX regime ──────
    # Stress regime = fewer trades (higher risk per trade), calm = more trades
    effective_daily_cap = MAX_TRADES_PER_DAY  # default 6
    if current_regime == "stress":
        effective_daily_cap = max(2, MAX_TRADES_PER_DAY // 3)  # 2 trades max
    elif current_regime == "elevated":
        effective_daily_cap = max(3, MAX_TRADES_PER_DAY * 2 // 3)  # 4 trades max
    today_count = _count_today_trades()
    daily_slots_remaining = max(0, effective_daily_cap - today_count)
    if daily_slots_remaining == 0:
        logger.info("Daily cap reached (%d trades today, cap=%d for regime=%s) — no more trades",
                     today_count, effective_daily_cap, current_regime)
        return ScanResult(
            scan_type=scan_type,
            timestamp=now,
            has_trade=False,
            reason_no_trade=f"Cap journalier atteint ({effective_daily_cap} trades, regime={current_regime})",
            news_analyzed=len(scored_news),
            market_context=market_context,
            all_scored_news=all_scored_log,
            learning_state=learning_state_for_log,
        )

    # Session-eligible tickers
    eligible_tickers = assets_for_session(scan_type.value)

    # Apply learning adjustments to scores
    candidates: list[tuple[ScoredNews, float]] = []

    for sn in scored_news:
        if sn.direction == Direction.NEUTRAL:
            rejection_log.append({
                "title": sn.news.title, "ticker": sn.impacted_tickers[:1],
                "reason": "Direction NEUTRAL", "score": sn.total_score,
            })
            continue
        if sn.total_score < MIN_SCORE_THRESHOLD:
            rejection_log.append({
                "title": sn.news.title, "ticker": sn.impacted_tickers[:1],
                "reason": f"Score {sn.total_score:.1f} < seuil {MIN_SCORE_THRESHOLD}",
                "score": sn.total_score,
            })
            continue

        # v4.0 D1: Find ALL eligible tickers (not just first) — allows fallback if primary blocked
        eligible_for_news = [t for t in sn.impacted_tickers if t in ASSET_BY_TICKER and t in eligible_tickers]

        if not eligible_for_news:
            rejection_log.append({
                "title": sn.news.title, "ticker": sn.impacted_tickers[:3],
                "reason": f"Aucun ticker eligible pour session {scan_type.value}",
                "score": sn.total_score,
            })
            continue

        # v4.2 A5: Average multipliers across ALL eligible tickers (not just first)
        ticker_mults = [ticker_adj.get(t, 1.0) for t in eligible_for_news]
        base_mult = sum(ticker_mults) / len(ticker_mults)
        # v7.7: Cross-dimension lookup with zone + intensity tiers
        # Priority: zone+intensity+ticker > zone+ticker > intensity+ticker > ticker > broad > 1.0
        nc_mult = 1.0
        # Compute intensity tier from expected_magnitude
        _intensity = ""
        if sn.expected_magnitude is not None:
            if sn.expected_magnitude <= 33:
                _intensity = "low"
            elif sn.expected_magnitude >= 67:
                _intensity = "high"
        for _t in eligible_for_news:
            # 1. Zone + intensity + ticker (most specific)
            if sn.news_zone and _intensity:
                zit_key = f"{sn.news_category}+{sn.news_zone}+{_intensity}+{_t}"
                if zit_key in newscat_adj:
                    nc_mult = newscat_adj[zit_key]
                    break
            # 2. Zone + ticker
            if sn.news_zone:
                zone_key = f"{sn.news_category}+{sn.news_zone}+{_t}"
                if zone_key in newscat_adj:
                    nc_mult = newscat_adj[zone_key]
                    break
            # 3. Intensity + ticker (no zone)
            if _intensity:
                int_key = f"{sn.news_category}+{_intensity}+{_t}"
                if int_key in newscat_adj:
                    nc_mult = newscat_adj[int_key]
                    break
            # 4. Ticker only (v5.2)
            combo_key = f"{sn.news_category}+{_t}"
            if combo_key in newscat_adj:
                nc_mult = newscat_adj[combo_key]
                break
        if nc_mult == 1.0:
            nc_mult = newscat_adj.get(sn.news_category, 1.0)
        dir_mult = direction_adj.get(sn.direction.value, 1.0)  # v4.2 B5
        # v4.2: Full contextual multiplier with all dimensions
        multiplier = (base_mult * current_session_mult * nc_mult
                      * current_regime_mult
                      * dir_mult * delay_bias_adj)
        multiplier = max(0.5, min(1.5, multiplier))  # Clamp

        adjusted_score = sn.total_score * multiplier

        # NOTE: Freshness is NOT penalized here — Claude already sees the age
        # ("[il y a Xh]") and adjusts transmission_delay accordingly.
        # Adding a decay here would double-penalize physical signals (weather 8h,
        # supply_chain 6h) that have high transmission_delay by design.

        # v3.6 (I): Multi-source convergence boost
        convergence_boost = 1.0
        if sn.convergence_count >= 2:
            convergence_boost = min(1.5, 1.0 + sn.convergence_count * 0.15)
            adjusted_score *= convergence_boost
            logger.info("Convergence boost for '%s': %d sources → %.2fx",
                        sn.news.title[:60], sn.convergence_count, convergence_boost)

        candidates.append((sn, adjusted_score, eligible_for_news, multiplier))

        # Log learning decomposition for diagnostics (Bug #1: explains score reduction)
        if multiplier < 0.8 or multiplier > 1.2:
            logger.info(
                "Learning impact on '%s': claude_score=%.1f × mult=%.3f "
                "(base=%.2f sess=%.2f nc=%.2f regime=%.2f dir=%.2f delay=%.2f) → adjusted=%.1f",
                sn.news.title[:50], sn.total_score, multiplier,
                base_mult, current_session_mult, nc_mult,
                current_regime_mult, dir_mult, delay_bias_adj,
                adjusted_score,
            )

    candidates.sort(key=lambda x: x[1], reverse=True)

    # Log score filtering summary — diagnose why scans produce no trades
    score_rejections = [r for r in rejection_log if "< seuil" in r.get("reason", "")]
    neutral_rejections = [r for r in rejection_log if r.get("reason") == "Direction NEUTRAL"]
    if score_rejections:
        top_rejected = max(score_rejections, key=lambda r: r.get("score", 0))
        logger.info(
            "Score filtering: %d/%d rejected (score < %d), %d NEUTRAL. "
            "Top rejected score: %.1f ('%s')",
            len(score_rejections), len(scored_news), MIN_SCORE_THRESHOLD,
            len(neutral_rejections), top_rejected["score"],
            top_rejected["title"][:60],
        )

    if not candidates:
        logger.warning(
            "No candidates after filtering %d scored news: %d low score, "
            "%d NEUTRAL, %d no eligible ticker — skipping trade selection",
            len(scored_news), len(score_rejections), len(neutral_rejections),
            len(rejection_log) - len(score_rejections) - len(neutral_rejections),
        )
        return ScanResult(
            scan_type=scan_type,
            timestamp=now,
            has_trade=False,
            reason_no_trade="Aucune news avec un score suffisant ou une direction claire",
            news_analyzed=len(scored_news),
            market_context=market_context,
            all_scored_news=all_scored_log,
            rejection_log=rejection_log if rejection_log else None,
            learning_state=learning_state_for_log,
        )

    # Build decision summary — track why we chose the winner
    decision_parts = [f"{len(candidates)} candidats apres filtrage initial sur {len(scored_news)} news"]

    # Pre-fetch prices for all candidate tickers in parallel to avoid sequential yfinance calls
    from concurrent.futures import ThreadPoolExecutor
    candidate_tickers = set()
    for sn, _, elig_tickers, _m in candidates:
        for t in elig_tickers:
            candidate_tickers.add(t)

    price_cache: dict[str, tuple] = {}
    # max_workers capped at 3: Replit kills process on too many concurrent threads.
    # v6.5: Explicit shutdown(wait=False) to avoid blocking scheduler if a future hangs
    executor = ThreadPoolExecutor(max_workers=min(3, len(candidate_tickers) or 1))
    try:
        futures = {executor.submit(_get_price_and_range, t): t for t in candidate_tickers}
        for future in futures:
            ticker_key = futures[future]
            try:
                price_cache[ticker_key] = future.result(timeout=15)
            except TimeoutError:
                logger.warning("Price pre-fetch TIMEOUT for %s (15s)", ticker_key)
                price_cache[ticker_key] = (None, 1.5, None, None, None)
            except Exception as exc:
                logger.warning("Price pre-fetch failed for %s: %s", ticker_key, exc)
                price_cache[ticker_key] = (None, 1.5, None, None, None)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    # Cross-day dedup: load tickers traded in the last N days
    recently_traded = _get_recently_traded_tickers()
    if recently_traded:
        logger.info("Cross-day dedup: %d tickers traded recently: %s",
                     len(recently_traded), ", ".join(sorted(recently_traded)))

    # ── v3.5: Multi-trade selection ──────────────────────────────
    # Iterate all candidates and select all valid ones (no per-scan cap).
    # Intra-scan correlation prevents picking correlated tickers.
    selected_trades: list[TradeRecommendation] = []
    # Track tickers selected in THIS scan for intra-scan correlation
    scan_selected_tickers: list[str] = []
    # Combine existing cross-scan tickers with intra-scan ones for correlation checks
    if isinstance(existing_trade_ticker, str):
        cross_scan_tickers = [existing_trade_ticker]
    elif existing_trade_ticker:
        cross_scan_tickers = list(existing_trade_ticker)
    else:
        cross_scan_tickers = []

    # P1-#13: Extract VIX and regime from market_context (same for all trades in scan)
    vix_val = market_context.get("vix") if market_context else None
    regime_val = market_context.get("regime") if market_context else None

    for rank, (best_news, best_score, elig_tickers, ranking_multiplier) in enumerate(candidates):
        # Recompute convergence boost for this candidate (needed for TradeRecommendation)
        _cand_convergence_boost = 1.0
        if best_news.convergence_count >= 2:
            _cand_convergence_boost = min(1.5, 1.0 + best_news.convergence_count * 0.15)
        # Daily cap reached during iteration
        if len(selected_trades) >= daily_slots_remaining:
            decision_parts.append(f"Cap journalier atteint apres {len(selected_trades)} trades ce scan")
            break

        # v4.0 D1: Try all eligible tickers in order — if first is blocked, try fallback
        raw_score = best_news.total_score
        all_existing = cross_scan_tickers + scan_selected_tickers
        cat_recently_traded = _get_recently_traded_tickers_by_category(best_news.news_category)
        effective_cooldown = CATEGORY_COOLDOWN_DAYS.get(best_news.news_category, RECENT_TRADE_COOLDOWN_DAYS)

        ticker = None
        asset = None
        price = None
        avg_range = 1.5
        prev_close = None
        volume_ratio = None
        today_open = None
        base_mult = 1.0
        multiplier = 1.0

        for candidate_ticker in elig_tickers:
            # Market hours check — only place orders when market is open and liquid
            if not is_market_open(candidate_ticker):
                logger.debug("D1 fallback: %s market closed, trying next", candidate_ticker)
                rejection_log.append({
                    "title": best_news.news.title, "ticker": candidate_ticker,
                    "reason": f"Market closed for {candidate_ticker}",
                    "score": raw_score,
                })
                continue
            # (#22) Correlation check
            if _check_correlation(candidate_ticker, all_existing):
                logger.debug("D1 fallback: %s correlated, trying next", candidate_ticker)
                rejection_log.append({
                    "title": best_news.news.title, "ticker": candidate_ticker,
                    "reason": f"Correlated with existing position",
                    "score": raw_score, "adjusted_score": best_score,
                })
                continue
            # Cross-day dedup
            if candidate_ticker in cat_recently_traded and not _check_reentry_eligible(candidate_ticker, best_news.news_category):
                logger.debug("D1 fallback: %s recently traded, trying next", candidate_ticker)
                rejection_log.append({
                    "title": best_news.news.title, "ticker": candidate_ticker,
                    "reason": f"Recently traded (cooldown {effective_cooldown}d)",
                    "score": raw_score, "adjusted_score": best_score,
                })
                continue
            # Price check
            _p, _ar, _pc, _vr, _to = price_cache.get(candidate_ticker, (None, 1.5, None, None, None))
            if _p is None:
                logger.debug("D1 fallback: %s no price, trying next", candidate_ticker)
                rejection_log.append({
                    "title": best_news.news.title, "ticker": candidate_ticker,
                    "reason": "Price unavailable",
                    "score": raw_score, "adjusted_score": best_score,
                })
                continue
            # Found valid ticker
            ticker = candidate_ticker
            asset = ASSET_BY_TICKER[ticker]
            price, avg_range, prev_close, volume_ratio, today_open = _p, _ar, _pc, _vr, _to
            break

        if ticker is None:
            rejection_log.append({
                "title": best_news.news.title, "ticker": elig_tickers,
                "reason": f"Tous les tickers bloques (correlation/cooldown/prix)",
                "score": raw_score, "adjusted_score": best_score,
            })
            continue

        # v4.3 M5: Use the SAME multiplier that was used during ranking
        # (averaged across all eligible tickers) instead of recomputing for single ticker.
        # This ensures ranking score and recorded multiplier are consistent.
        multiplier = ranking_multiplier

        # v4.0 B4: Spread filter — reject if spread eats >40% of target
        spread_pct = ESTIMATED_SPREADS.get(ticker, DEFAULT_SPREAD)
        if avg_range > 0 and spread_pct > 0:
            # v5.2: Use volatility-tier-adapted formula (same as pre-move and calibration)
            _ns = best_news.total_score / 100
            _mf = 0.7 + 0.6 * (best_news.expected_magnitude / 100)
            if avg_range < 1.0:
                _sf = 0.30 + 0.60 * (_ns ** 1.5)  # Low-vol tier
            elif avg_range > 5.0:
                _sf = 0.12 + 0.33 * (_ns ** 1.5)  # High-vol tier — synced with _calibrate_trade
            else:
                _sf = 0.20 + 0.50 * (_ns ** 1.5)  # Normal tier
            estimated_target = avg_range * _sf * _mf
            if spread_pct / estimated_target > 0.4:
                reason = f"Spread {spread_pct:.2f}% trop large vs target estime {estimated_target:.2f}% ({ticker})"
                logger.info("Skipping %s: %s", ticker, reason)
                rejection_log.append({
                    "title": best_news.news.title, "ticker": [ticker],
                    "reason": reason, "score": raw_score,
                })
                continue

        # (#4) News déjà pricée detection — direction-aware, category-adaptive
        # v3.7: Instead of rejecting partially-priced moves, REDUCE the target.
        # v4.0 B5: Use today_open (intraday reference) instead of prev_close when available.
        # A scan at 17h comparing to yesterday's close misses the intraday move since 9h.
        # v4.3 M4: Convex formula matching calibration tiers
        _ns_pre = best_news.total_score / 100
        _mf_pre = 0.7 + 0.6 * (best_news.expected_magnitude / 100)
        if avg_range < 1.0:
            # Low-vol tier
            _sf_pre = 0.30 + 0.60 * (_ns_pre ** 1.5)
        elif avg_range > 5.0:
            # High-vol tier — synced with _calibrate_trade
            _sf_pre = 0.12 + 0.33 * (_ns_pre ** 1.5)
        else:
            # Normal tier
            _sf_pre = 0.20 + 0.50 * (_ns_pre ** 1.5)
        target_move_expected = avg_range * _sf_pre * _mf_pre
        # v4.0 B5: Prefer today_open for intraday pre-move — captures moves since market open
        pre_move_ref = today_open if today_open is not None else prev_close
        pre_move_pct = _detect_pre_move(price, pre_move_ref, target_move_expected)
        pre_move_ratio = PRE_MOVE_THRESHOLDS.get(asset.category, 0.8)
        pre_move_reduction = 1.0  # Factor to reduce target if trend already started
        if pre_move_pct is not None:
            directional_pre_move = pre_move_pct if best_news.direction == Direction.LONG else -pre_move_pct
            if directional_pre_move > 0 and target_move_expected > 0:
                consumed_ratio = directional_pre_move / target_move_expected
                if consumed_ratio > 0.95:
                    # >95% consumed: truly exhausted, reject
                    reason = f"Move epuise: pre-move {pre_move_pct:+.2f}% = {consumed_ratio*100:.0f}% du move attendu ({asset.category})"
                    logger.info("Skipping %s: %s", ticker, reason)
                    rejection_log.append({
                        "title": best_news.news.title, "ticker": [ticker],
                        "reason": reason, "score": raw_score, "pre_move_pct": pre_move_pct,
                    })
                    continue
                elif consumed_ratio > pre_move_ratio:
                    # Partially priced: reduce target to remaining move + continuation potential
                    # e.g., 70% consumed → reduce target to 50% (remaining 30% + 20% continuation)
                    remaining = 1.0 - consumed_ratio
                    pre_move_reduction = max(0.3, remaining + 0.2)  # Floor at 30% of original target
                    logger.info(
                        "Pre-move reduction for %s: %+.2f%% consumed (%.0f%%) → target reduced to %.0f%%",
                        ticker, pre_move_pct, consumed_ratio * 100, pre_move_reduction * 100,
                    )

        # v3.6 (D): Price at publication tracking
        price_at_pub = None
        pub_move_pct = None
        if best_news.news.published and prev_close is not None:
            # Estimate: use prev_close as proxy for price at publication time
            # (exact intraday price at arbitrary timestamp would require tick data)
            pub_age_hours = (now - best_news.news.published).total_seconds() / 3600
            if pub_age_hours < 24:
                price_at_pub = prev_close
                if prev_close > 0:
                    pub_move_pct = round((price - prev_close) / prev_close * 100, 4)

        # (#13) Gap buffer for morning scans + (M) overnight gap detection
        gap_buffer_applied = False
        if prev_close is not None:
            gap_pct = abs((price - prev_close) / prev_close * 100) if prev_close > 0 else 0
            if gap_pct > 0.5:
                gap_buffer_applied = True
                logger.info("Gap buffer applied for %s: gap=%.2f%%", ticker, gap_pct)
            # M: Overnight gap detection — large gaps for EU stocks suggest news already priced
            if asset.category == "actions_europe" and gap_pct > 2.0:
                reason = f"Overnight gap trop large pour {ticker}: {gap_pct:.2f}% (seuil: 2.0%)"
                logger.info("Skipping %s: %s", ticker, reason)
                rejection_log.append({
                    "title": best_news.news.title, "ticker": [ticker],
                    "reason": reason, "score": raw_score, "gap_pct": gap_pct,
                })
                continue

        target_price, stop_price, target_pct, stop_pct, rr = _calibrate_trade(
            best_news.direction, price, avg_range, best_news.total_score,
            news_category=best_news.news_category,
            ticker=ticker,
            expected_magnitude=best_news.expected_magnitude,
        )

        # v3.7: Apply pre-move reduction to target (trend already started → smaller target)
        if pre_move_reduction < 1.0:
            target_pct = round(target_pct * pre_move_reduction, 2)
            target_pct = max(TARGET_PERCENT, target_pct)  # Never below floor
            if best_news.direction == Direction.LONG:
                target_price = round(price * (1 + target_pct / 100), 4)
            else:
                target_price = round(price * (1 - target_pct / 100), 4)
            rr = round(target_pct / stop_pct, 2) if stop_pct > 0 else 0
            logger.info("Pre-move adjusted target for %s: %.2f%% (reduction %.0f%%), R/R=%.2f",
                        ticker, target_pct, pre_move_reduction * 100, rr)

        # v5.2 D2: Adaptive R/R minimum by category — demand higher R/R for low-edge categories
        _min_rr = MIN_RISK_REWARD  # 1.2 base
        if best_news.news_category in ("earnings", "macro", "regulatory", "central_bank_subtle", "other"):
            _min_rr = 1.5  # Low-edge categories need higher R/R to be worth the risk
        elif best_news.news_category in ("m_a", "sector"):
            _min_rr = 1.3  # Moderate edge
        # commodity, weather, supply_chain, geopolitical stay at 1.2

        if rr < _min_rr:
            reason = f"R/R {rr:.2f} < seuil adaptatif {_min_rr} ({best_news.news_category})"
            logger.info("Skipping %s: %s", ticker, reason)
            rejection_log.append({
                "title": best_news.news.title, "ticker": [ticker],
                "reason": reason, "score": raw_score, "risk_reward": rr,
            })
            continue

        # v4.0 B3: Confidence decoupled from score — based on reliability * clarity
        # A high-score speculative rumor has low confidence, a confirmed USDA report has high confidence
        confidence = min(100, int(best_news.signal_reliability * best_news.directional_clarity / 100))

        # v3.6: Position sizing (with IV filter K — reduce size if vol elevated)
        # P2-7: pass market_context for VIX regime sizing, P3-7: pass day_of_week for Friday
        position_size = _compute_position_size(
            best_score, confidence, rr,
            market_context=market_context, day_of_week=now.weekday(),
        )
        if avg_range > 3.0:  # K: IV proxy — high vol = reduce position
            iv_reduction = min(0.5, (avg_range - 3.0) / 10.0)
            position_size *= (1.0 - iv_reduction)
            position_size = round(max(MIN_POSITION_SIZE_PCT, position_size), 2)

        # (#24) Binary event check
        binary_warning = _check_binary_event(best_news.news.title, best_news.reasoning)

        # (#10) Volume confirmation (non-blocking)
        # v5.1: Threshold raised to 1.5x (industry standard for meaningful confirmation)
        volume_confirmed = None
        if volume_ratio is not None:
            volume_confirmed = volume_ratio > 1.5
            if volume_confirmed:
                logger.info("Volume confirmed for %s: %.1fx average", ticker, volume_ratio)
                # v5.1: Boost position size slightly when volume confirms (max +15%)
                vol_boost = min(1.15, 1.0 + (volume_ratio - 1.5) * 0.1)
                position_size *= vol_boost
                position_size = round(min(MAX_POSITION_SIZE_PCT, position_size), 2)
            else:
                logger.info("Volume below average for %s: %.1fx (trade still valid)", ticker, volume_ratio)
                # v5.1: Reduce position size when volume doesn't confirm (-10%)
                position_size *= 0.9
                position_size = round(max(MIN_POSITION_SIZE_PCT, position_size), 2)

        # (#12) Entry price latency warning
        if prev_close is not None:
            entry_vs_prev = abs((price - prev_close) / prev_close * 100) if prev_close > 0 else 0
            if entry_vs_prev > 2.0:
                logger.warning("Entry price for %s may have significant latency: %.2f%% from prev close", ticker, entry_vs_prev)

        # v3.4 #5: Track learning_helped — did learning boost or penalize this trade?
        learning_helped = None
        if multiplier != 1.0:
            learning_helped = multiplier > 1.0  # True = learning boosted this trade

        # v3.5: Build decision summary for each selected trade
        decision_parts.append(
            f"SELECTIONNE #{len(selected_trades)+1}: {ticker} {best_news.direction.value} | "
            f"Score brut Claude={raw_score:.1f}, learning_mult={multiplier:.3f} "
            f"(base={base_mult:.3f}, session={current_session_mult:.3f}, "
            f"newscat={nc_mult:.3f}, regime={current_regime_mult:.3f}, "
            f"dir={dir_mult:.3f}, delay_bias={delay_bias_adj:.3f}), "
            f"score ajuste={best_score:.1f} | "
            f"News: '{best_news.news.title[:80]}' | "
            f"Categorie: {best_news.news_category}, edge={best_news.transmission_delay}/{best_news.market_awareness}"
        )

        # Validate entry price against reference to catch API anomalies
        price_valid, price_reason = validate_price(ticker, price)
        if not price_valid:
            logger.error("REJECTED trade %s: entry price failed validation — %s", ticker, price_reason)
            rejection_log.append({
                "title": best_news.news.title, "ticker": ticker,
                "reason": f"Price validation failed: {price_reason}",
                "score": raw_score, "adjusted_score": best_score,
            })
            continue

        recommendation = TradeRecommendation(
            scan_type=scan_type,
            timestamp=now,
            ticker=ticker,
            asset_name=asset.name,
            category=asset.category,
            direction=best_news.direction,
            news_headline=best_news.news.title,
            news_url=best_news.news.url or "",
            news_description=best_news.news.description or "",
            news_category=best_news.news_category,
            news_zone=best_news.news_zone,
            catalyst=best_news.reasoning,
            entry_price=round(price, 4),
            target_price=target_price,
            stop_price=stop_price,
            target_pct=target_pct,
            stop_pct=stop_pct,
            risk_reward=rr,
            confidence=confidence,
            time_window=TIME_WINDOWS[scan_type],
            news_sources=[best_news.news.source],
            pre_move_pct=pre_move_pct,
            gap_buffer_applied=gap_buffer_applied,
            binary_event_warning=binary_warning,
            volume_confirmed=volume_confirmed,
            # v5.1: All Claude scoring dimensions for complete journal view
            surprise=best_news.surprise,
            directional_clarity=best_news.directional_clarity,
            transmission_delay=best_news.transmission_delay,
            market_awareness=best_news.market_awareness,
            edge_score=round(
                (best_news.transmission_delay / 100)
                * (1 - best_news.market_awareness / 100), 4
            ),
            chain_reactions=[cr.model_dump() for cr in best_news.chain_reactions] if best_news.chain_reactions else None,
            # v4.0: Magnitude and reliability from Claude
            expected_magnitude=best_news.expected_magnitude,
            signal_reliability=best_news.signal_reliability,
            # P0-#4: Raw score decomposition
            raw_claude_score=round(raw_score, 2),
            learning_multiplier=round(multiplier, 3),
            # P1-#13: Contextual features
            vix_at_trade=vix_val,
            market_regime=regime_val,
            day_of_week=now.weekday(),
            volume_ratio=round(volume_ratio, 2) if volume_ratio is not None else None,
            # P1-#6: Store predicted transmission delay for later validation
            predicted_transmission_delay=best_news.transmission_delay,
            # v3.6: Position sizing
            position_size_pct=position_size,
            # v3.6: Multi-source convergence
            convergence_count=best_news.convergence_count,
            convergence_boost=_cand_convergence_boost if _cand_convergence_boost > 1.0 else None,
            # v3.6: Price at publication
            price_at_publication=price_at_pub,
            price_at_scan=round(price, 4),
            publication_move_pct=pub_move_pct,
            # v8.2: Agent version tracking
            agent_versions=_get_agent_versions(),
        )

        selected_trades.append(recommendation)
        scan_selected_tickers.append(ticker)
        logger.info(
            "Trade #%d selected: %s %s %s (score=%.1f, confidence=%d%%)",
            len(selected_trades), recommendation.direction.value,
            ticker, asset.name, best_score, confidence,
        )

    # ── Build final ScanResult ──────────────────────────────────
    if selected_trades:
        decision_parts.append(f"TOTAL: {len(selected_trades)} trade(s) selectionne(s) ce scan")
        return ScanResult(
            scan_type=scan_type,
            timestamp=now,
            has_trade=True,
            recommendation=selected_trades[0],  # Backward compat: first trade
            recommendations=selected_trades,     # v3.5: all trades
            news_analyzed=len(scored_news),
            market_context=market_context,
            all_scored_news=all_scored_log,
            rejection_log=rejection_log if rejection_log else None,
            decision_summary=" | ".join(decision_parts),
            learning_state=learning_state_for_log,
        )

    # v9.0: Trace top-scored candidates that were rejected — helps diagnose why
    # strong signals (e.g., 68.7) produce no trades
    if candidates and rejection_log:
        # Find the highest raw score among ALL scored_news (not just candidates)
        top_raw = max(scored_news, key=lambda s: s.total_score) if scored_news else None
        if top_raw and top_raw.total_score >= MIN_SCORE_THRESHOLD:
            # Check if this top signal appears in rejection_log
            top_rejections = [r for r in rejection_log
                              if r.get("score", 0) >= top_raw.total_score - 0.1]
            if top_rejections:
                for rej in top_rejections:
                    logger.warning(
                        "TOP SIGNAL REJECTED: score=%.1f, ticker=%s, reason='%s' — "
                        "headline='%s'",
                        rej.get("score", 0), rej.get("ticker"),
                        rej.get("reason", "unknown"),
                        rej.get("title", "")[:80],
                    )
            else:
                # Top signal was a candidate but rejected at price/R/R/spread stage
                logger.warning(
                    "TOP SIGNAL (score=%.1f, dir=%s) not in rejection_log — "
                    "check if rejected at calibration stage. Ticker(s): %s, "
                    "headline: '%s'",
                    top_raw.total_score, top_raw.direction.value,
                    top_raw.impacted_tickers[:3],
                    top_raw.news.title[:80],
                )

    # All candidates failed checks
    decision_parts.append("AUCUN candidat n'a passe les checks prix/R/R")
    return ScanResult(
        scan_type=scan_type,
        timestamp=now,
        has_trade=False,
        reason_no_trade="Impossible de calibrer un trade avec un R/R suffisant",
        news_analyzed=len(scored_news),
        market_context=market_context,
        all_scored_news=all_scored_log,
        rejection_log=rejection_log if rejection_log else None,
        decision_summary=" | ".join(decision_parts),
        learning_state=learning_state_for_log,
    )
