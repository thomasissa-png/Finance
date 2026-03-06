"""Selects the best trade from scored news and calibrates entry/TP/SL."""

import logging
from datetime import datetime, timezone, timedelta

from .market_data import fetch_history

from .config import (
    ASSET_BY_TICKER,
    BASE_POSITION_SIZE_PCT,
    CORRELATION_GROUPS,
    KELLY_FRACTION,
    MAX_POSITION_SIZE_PCT,
    MAX_TRADES_PER_DAY,
    MIN_POSITION_SIZE_PCT,
    MIN_RISK_REWARD,
    MIN_SCORE_THRESHOLD,
    NEWS_CATEGORY_MULTIPLIERS,
    TARGET_PERCENT,
    assets_for_session,
)

# ── Pre-move thresholds by asset category ──────────────────────────
# Percentage of expected move that constitutes "already priced"
# Forex: tight (0.3% overnight is normal), Commodities: loose (2-3% moves are common)
PRE_MOVE_THRESHOLDS: dict[str, float] = {
    "forex": 0.6,       # 60% of expected move — forex has small ATR, overnight gap is normal
    "commodities": 0.85, # 85% — commodities can move 3%+ on real signals, don't reject too early
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

    Loads trades and checks which tickers have PENDING or recently-closed trades.
    This prevents the system from proposing the same trade day after day on
    persistent news (e.g., wheat drought story running for a week).
    """
    try:
        from .learning import load_trades
        cutoff = datetime.now(timezone.utc) - timedelta(days=cooldown_days)
        trades = load_trades()
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
        from .learning import load_trades
        from .models import TradeResult
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        trades = load_trades()
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
    """Count how many trades were already taken today (for daily cap)."""
    try:
        from .learning import load_trades
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        trades = load_trades()
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


def _get_price_and_range(ticker: str, days: int = 20) -> tuple[float | None, float, float | None, float | None]:
    """Fetch current price, true ATR (#11), previous close, and volume info.

    Returns (current_price, true_atr_pct, prev_close, avg_volume_ratio).
    avg_volume_ratio = today's volume / 20d avg volume (None if unavailable).
    """
    try:
        data = fetch_history(ticker, period_days=days + 5, interval="1day")
        if data is None or data.empty:
            return None, 1.5, None, None
        current_price = float(data["Close"].iloc[-1])
        if len(data) < 5:
            return current_price, 1.5, None, None

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

        # Use last `days` true ranges
        recent_tr = true_ranges[-days:] if len(true_ranges) >= days else true_ranges
        avg_true_range = sum(recent_tr) / len(recent_tr) if recent_tr else 0
        true_atr_pct = (avg_true_range / current_price * 100) if current_price > 0 else 1.5

        # Previous close for "news déjà pricée" detection (#4)
        prev_close = float(closes[-2]) if len(closes) >= 2 else None

        # (#10) Volume ratio — None if no volume data (don't block the trade)
        volume_ratio = None
        if "Volume" in data.columns:
            volumes = data["Volume"].values
            avg_vol = float(volumes[-days:].mean()) if len(volumes) >= days else float(volumes.mean())
            if avg_vol > 0 and volumes[-1] > 0:
                volume_ratio = float(volumes[-1]) / avg_vol

        return current_price, round(true_atr_pct, 4), prev_close, volume_ratio
    except Exception as exc:
        logger.warning("Price/range fetch failed for %s: %s", ticker, exc)
        return None, 1.5, None, None


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


def _compute_dynamic_correlation(ticker1: str, ticker2: str, lookback: int = 20) -> float | None:
    """Compute 20-day rolling correlation between two tickers (G. dynamic correlation).

    Returns correlation coefficient (-1 to 1), or None if data unavailable.
    """
    try:
        data1 = fetch_history(ticker1, period_days=lookback + 5, interval="1day")
        data2 = fetch_history(ticker2, period_days=lookback + 5, interval="1day")
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
        return round(corr, 3) if corr == corr else None  # NaN check
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
        in_static_group = False
        for group_tickers in CORRELATION_GROUPS.values():
            if ticker in group_tickers and existing in group_tickers:
                return True
            # P3-6: Track if either is in the same static group (skip dynamic then)
            if ticker in group_tickers or existing in group_tickers:
                if ticker in group_tickers and existing in group_tickers:
                    in_static_group = True

        # P3-6: Only run dynamic check if not already covered by static groups
        # Dynamic correlation is slow (2 yfinance calls per pair) — avoid when unnecessary
        if not in_static_group:
            dyn_corr = _compute_dynamic_correlation(ticker, existing)
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
) -> tuple[float, float, float, float, float]:
    """Calculate target, stop, percentages and risk/reward.

    Target is score-weighted: higher score → bigger target as fraction of ATR.
    Stop is a fixed fraction of ATR, independent of target.
    News category multipliers (#7) adjust target/stop based on event type.

    Returns (target_price, stop_price, target_pct, stop_pct, risk_reward).
    """
    # Target: score-weighted fraction of ATR, scaled by volatility regime
    # Low-vol assets (forex, large indices): wider factor to overcome slippage
    # High-vol assets (NG, small-cap): tighter to stay realistic for day trading
    if avg_range < 1.0:
        score_factor = 0.35 + (score / 100) * 0.55
        stop_fraction = 0.5
    elif avg_range > 5.0:
        score_factor = 0.15 + (score / 100) * 0.30
        stop_fraction = 0.3
    else:
        score_factor = 0.25 + (score / 100) * 0.45
        stop_fraction = 0.4
    target_move_pct = max(TARGET_PERCENT, avg_range * score_factor)

    # Stop: fraction of ATR adapted to volatility, independent of target
    # Floor at TARGET_PERCENT * 0.7 = 0.35% — adapte levier (0.35% x 10x = 3.5% perte max)
    stop_move_pct = max(TARGET_PERCENT * 0.7, avg_range * stop_fraction)

    # (#7) Apply news category multipliers
    cat_mults = NEWS_CATEGORY_MULTIPLIERS.get(news_category, {"target_mult": 1.0, "stop_mult": 1.0})
    target_move_pct *= cat_mults["target_mult"]
    stop_move_pct *= cat_mults["stop_mult"]

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
    """Build a serializable log of all scored news for journal tracing."""
    log = []
    for sn in scored_news:
        log.append({
            "title": sn.news.title,
            "source": sn.news.source,
            "direction": sn.direction.value,
            "total_score": sn.total_score,
            "surprise": sn.surprise,
            "freshness": sn.freshness,
            "directional_clarity": sn.directional_clarity,
            "transmission_delay": sn.transmission_delay,
            "market_awareness": sn.market_awareness,
            "news_category": sn.news_category,
            "category_score_mult": sn.category_score_mult,
            "impacted_tickers": sn.impacted_tickers,
            "reasoning": sn.reasoning,
        })
    return log


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
    now = datetime.now(timezone.utc)

    # v3.4: Unpack the structured learning data (backward-compatible with flat dict)
    if isinstance(learning_adjustments, dict) and "adjustments" in learning_adjustments:
        ticker_adj = learning_adjustments.get("adjustments", {})
        session_adj = learning_adjustments.get("session_adj", {})
        newscat_adj = learning_adjustments.get("newscat_adj", {})
        regime_adj = learning_adjustments.get("regime_adj", {})
        learning_state_for_log = learning_adjustments
    elif isinstance(learning_adjustments, dict):
        # Backward compat: old flat format {ticker: multiplier}
        ticker_adj = learning_adjustments
        session_adj = {}
        newscat_adj = {}
        regime_adj = {}
        learning_state_for_log = learning_adjustments
    else:
        ticker_adj = {}
        session_adj = {}
        newscat_adj = {}
        regime_adj = {}
        learning_state_for_log = None

    # v3.4 #2: Session multiplier from CURRENT scan type (not last trade)
    current_session_mult = session_adj.get(scan_type.value, 1.0)

    # v3.4 #1: Regime multiplier from CURRENT market context
    current_regime = market_context.get("regime", "normal") if market_context else "normal"
    current_regime_mult = regime_adj.get(current_regime, 1.0)

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

    # ── Daily cap check ──────────────────────────────────────────
    today_count = _count_today_trades()
    daily_slots_remaining = max(0, MAX_TRADES_PER_DAY - today_count)
    if daily_slots_remaining == 0:
        logger.info("Daily cap reached (%d trades today) — no more trades", today_count)
        return ScanResult(
            scan_type=scan_type,
            timestamp=now,
            has_trade=False,
            reason_no_trade=f"Cap journalier atteint ({MAX_TRADES_PER_DAY} trades aujourd'hui)",
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

        # Find the best matching ticker from our universe AND eligible for this session
        best_ticker = None
        for t in sn.impacted_tickers:
            if t in ASSET_BY_TICKER and t in eligible_tickers:
                best_ticker = t
                break

        if not best_ticker:
            rejection_log.append({
                "title": sn.news.title, "ticker": sn.impacted_tickers[:3],
                "reason": f"Aucun ticker eligible pour session {scan_type.value}",
                "score": sn.total_score,
            })
            continue

        # v3.4: Contextual learning multiplier = base(ticker*cat) * session * newscat * regime
        base_mult = ticker_adj.get(best_ticker, 1.0)
        nc_mult = newscat_adj.get(sn.news_category, 1.0)  # v3.4 #4: direct from current news
        multiplier = base_mult * current_session_mult * nc_mult * current_regime_mult
        multiplier = max(0.5, min(1.5, multiplier))  # Clamp

        adjusted_score = sn.total_score * multiplier

        # v3.6 (I): Multi-source convergence boost
        convergence_boost = 1.0
        if hasattr(sn, 'convergence_count') and sn.convergence_count >= 2:
            convergence_boost = min(1.5, 1.0 + sn.convergence_count * 0.15)
            adjusted_score *= convergence_boost
            logger.info("Convergence boost for '%s': %d sources → %.2fx",
                        sn.news.title[:60], sn.convergence_count, convergence_boost)

        candidates.append((sn, adjusted_score))

    candidates.sort(key=lambda x: x[1], reverse=True)

    if not candidates:
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
    for sn, _ in candidates:
        for t in sn.impacted_tickers:
            if t in ASSET_BY_TICKER and t in eligible_tickers:
                candidate_tickers.add(t)
                break

    price_cache: dict[str, tuple] = {}
    # max_workers capped at 3: Replit kills process on too many concurrent threads.
    with ThreadPoolExecutor(max_workers=min(3, len(candidate_tickers) or 1)) as executor:
        futures = {executor.submit(_get_price_and_range, t): t for t in candidate_tickers}
        for future in futures:
            ticker_key = futures[future]
            try:
                price_cache[ticker_key] = future.result(timeout=15)
            except Exception as exc:
                logger.debug("Price pre-fetch failed for %s: %s", ticker_key, exc)
                price_cache[ticker_key] = (None, 1.5, None, None)

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

    for rank, (best_news, best_score) in enumerate(candidates):
        # Recompute convergence boost for this candidate (needed for TradeRecommendation)
        _cand_convergence_boost = 1.0
        if hasattr(best_news, 'convergence_count') and best_news.convergence_count >= 2:
            _cand_convergence_boost = min(1.5, 1.0 + best_news.convergence_count * 0.15)
        # Daily cap reached during iteration
        if len(selected_trades) >= daily_slots_remaining:
            decision_parts.append(f"Cap journalier atteint apres {len(selected_trades)} trades ce scan")
            break

        ticker = next(t for t in best_news.impacted_tickers if t in ASSET_BY_TICKER and t in eligible_tickers)
        asset = ASSET_BY_TICKER[ticker]
        raw_score = best_news.total_score

        # v3.4: Recompute full contextual multiplier for this candidate
        base_mult = ticker_adj.get(ticker, 1.0)
        nc_mult = newscat_adj.get(best_news.news_category, 1.0)
        multiplier = base_mult * current_session_mult * nc_mult * current_regime_mult
        multiplier = max(0.5, min(1.5, multiplier))

        # (#22) Correlation check — cross-scan + intra-scan
        all_existing = cross_scan_tickers + scan_selected_tickers
        if _check_correlation(ticker, all_existing):
            reason = f"Correle avec trade existant {all_existing}"
            logger.info("Skipping %s: correlated with existing trades %s", ticker, all_existing)
            rejection_log.append({
                "title": best_news.news.title, "ticker": [ticker],
                "reason": reason, "score": raw_score, "adjusted_score": best_score,
            })
            continue

        # Cross-day dedup: skip tickers already traded recently
        # P1-4: Use category-adaptive cooldown (weather/supply_chain = 1 day)
        # P2-6: Allow re-entry if trade was stopped and signal persists
        cat_recently_traded = _get_recently_traded_tickers_by_category(best_news.news_category)
        effective_cooldown = CATEGORY_COOLDOWN_DAYS.get(best_news.news_category, RECENT_TRADE_COOLDOWN_DAYS)
        if ticker in cat_recently_traded and not _check_reentry_eligible(ticker, best_news.news_category):
            reason = f"Deja trade dans les {effective_cooldown} derniers jours (categorie: {best_news.news_category})"
            logger.info("Skipping %s: %s", ticker, reason)
            rejection_log.append({
                "title": best_news.news.title, "ticker": [ticker],
                "reason": reason, "score": raw_score, "adjusted_score": best_score,
            })
            continue

        price, avg_range, prev_close, volume_ratio = price_cache.get(ticker, (None, 1.5, None, None))
        if price is None:
            rejection_log.append({
                "title": best_news.news.title, "ticker": [ticker],
                "reason": "Prix indisponible (yfinance)", "score": raw_score,
            })
            continue

        # (#4) News déjà pricée detection — direction-aware, category-adaptive
        # v3.7: Instead of rejecting partially-priced moves, REDUCE the target.
        # "Si une tendance est déjà commencée, 1% de gain est une super victoire" (levier 5-10x).
        # Only reject if >95% of expected move is already priced (truly exhausted).
        if avg_range < 1.0:
            _pre_factor = 0.35 + (best_news.total_score / 100) * 0.55
        elif avg_range > 5.0:
            _pre_factor = 0.15 + (best_news.total_score / 100) * 0.30
        else:
            _pre_factor = 0.25 + (best_news.total_score / 100) * 0.45
        target_move_expected = avg_range * _pre_factor
        pre_move_pct = _detect_pre_move(price, prev_close, target_move_expected)
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

        if rr < MIN_RISK_REWARD:
            reason = f"R/R {rr:.2f} < seuil {MIN_RISK_REWARD}"
            logger.info("Skipping %s: %s", ticker, reason)
            rejection_log.append({
                "title": best_news.news.title, "ticker": [ticker],
                "reason": reason, "score": raw_score, "risk_reward": rr,
            })
            continue

        confidence = min(100, int(best_score))

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
        volume_confirmed = None
        if volume_ratio is not None:
            volume_confirmed = volume_ratio > 1.2
            if volume_confirmed:
                logger.info("Volume confirmed for %s: %.1fx average", ticker, volume_ratio)
            else:
                logger.info("Volume below average for %s: %.1fx (trade still valid)", ticker, volume_ratio)

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
            f"newscat={nc_mult:.3f}, regime={current_regime_mult:.3f}), "
            f"score ajuste={best_score:.1f} | "
            f"News: '{best_news.news.title[:80]}' | "
            f"Categorie: {best_news.news_category}, edge={best_news.transmission_delay}/{best_news.market_awareness}"
        )

        recommendation = TradeRecommendation(
            scan_type=scan_type,
            timestamp=now,
            ticker=ticker,
            asset_name=asset.name,
            category=asset.category,
            direction=best_news.direction,
            news_headline=best_news.news.title,
            news_category=best_news.news_category,
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
            transmission_delay=best_news.transmission_delay,
            market_awareness=best_news.market_awareness,
            edge_score=round(
                (best_news.transmission_delay / 100)
                * (1 - best_news.market_awareness / 100), 4
            ),
            chain_reactions=[cr.model_dump() for cr in best_news.chain_reactions] if best_news.chain_reactions else None,
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
            convergence_count=getattr(best_news, 'convergence_count', 0),
            convergence_boost=_cand_convergence_boost if _cand_convergence_boost > 1.0 else None,
            # v3.6: Price at publication
            price_at_publication=price_at_pub,
            price_at_scan=round(price, 4),
            publication_move_pct=pub_move_pct,
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
