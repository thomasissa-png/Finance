"""Selects the best trade from scored news and calibrates entry/TP/SL."""

import logging
from datetime import datetime, timezone

import yfinance as yf

from .config import (
    ASSET_BY_TICKER,
    CORRELATION_GROUPS,
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
        data = yf.Ticker(ticker).history(period=f"{days + 5}d")
        if data.empty:
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


def _check_correlation(ticker: str, existing_trade_tickers: list[str] | str | None) -> bool:
    """Check if a ticker is correlated with ANY existing trade (#22).

    Accepts either a single ticker (backward compat) or a list of all pending tickers.
    Returns True if correlated (should avoid).
    """
    if not existing_trade_tickers:
        return False
    # Normalize to list
    if isinstance(existing_trade_tickers, str):
        tickers_to_check = [existing_trade_tickers]
    else:
        tickers_to_check = existing_trade_tickers

    for existing in tickers_to_check:
        for group_tickers in CORRELATION_GROUPS.values():
            if ticker in group_tickers and existing in group_tickers:
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
    learning_adjustments: dict[str, float] | None = None,
    existing_trade_ticker: str | None = None,
    market_context: dict | None = None,
) -> ScanResult:
    """Pick the single best trade from scored news.

    v3: Enriched with full decision trace for journal learning.

    Args:
        scored_news: News scored by the LLM, sorted by total_score desc.
        scan_type: europe or us scan.
        learning_adjustments: Optional dict of ticker -> multiplier from learning module.
        existing_trade_ticker: Ticker from the other scan (for correlation check #22).
        market_context: Market context from news_scorer (#5).

    Returns:
        ScanResult with either a trade recommendation or a pass.
    """
    now = datetime.now(timezone.utc)
    adjustments = learning_adjustments or {}

    # Build the full scored news log for journal (all Claude reasoning)
    all_scored_log = _build_scored_news_log(scored_news) if scored_news else None
    rejection_log: list[dict] = []

    if not scored_news:
        return ScanResult(
            scan_type=scan_type,
            timestamp=now,
            has_trade=False,
            reason_no_trade="Aucune news collectee",
            news_analyzed=0,
            market_context=market_context,
            learning_state=adjustments if adjustments else None,
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
            reason_no_trade=f"Evenement macro imminent: {event_conflict.name} — zero edge, trade bloque",
            news_analyzed=len(scored_news),
            market_context=market_context,
            all_scored_news=all_scored_log,
            learning_state=adjustments if adjustments else None,
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

        # Apply learning multiplier
        multiplier = adjustments.get(best_ticker, 1.0)
        adjusted_score = sn.total_score * multiplier
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
            learning_state=adjustments if adjustments else None,
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
    with ThreadPoolExecutor(max_workers=min(5, len(candidate_tickers) or 1)) as executor:
        futures = {executor.submit(_get_price_and_range, t): t for t in candidate_tickers}
        for future in futures:
            ticker_key = futures[future]
            try:
                price_cache[ticker_key] = future.result(timeout=15)
            except Exception as exc:
                logger.debug("Price pre-fetch failed for %s: %s", ticker_key, exc)
                price_cache[ticker_key] = (None, 1.5, None, None)

    # Try candidates until we find one with a valid price and R/R
    for rank, (best_news, best_score) in enumerate(candidates):
        ticker = next(t for t in best_news.impacted_tickers if t in ASSET_BY_TICKER and t in eligible_tickers)
        asset = ASSET_BY_TICKER[ticker]
        raw_score = best_news.total_score
        multiplier = adjustments.get(ticker, 1.0)

        # (#22) Correlation check
        if _check_correlation(ticker, existing_trade_ticker):
            reason = f"Correle avec trade existant {existing_trade_ticker}"
            logger.info("Skipping %s: correlated with existing trade %s", ticker, existing_trade_ticker)
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
        # Use same volatility-regime factors as _calibrate_trade
        # Pre-move threshold adapts to asset category (forex tighter, commodities looser)
        if avg_range < 1.0:
            _pre_factor = 0.35 + (best_news.total_score / 100) * 0.55
        elif avg_range > 5.0:
            _pre_factor = 0.15 + (best_news.total_score / 100) * 0.30
        else:
            _pre_factor = 0.25 + (best_news.total_score / 100) * 0.45
        target_move_expected = avg_range * _pre_factor
        pre_move_pct = _detect_pre_move(price, prev_close, target_move_expected)
        # Category-adaptive pre-move threshold (default 0.8 = 80% of expected move)
        pre_move_ratio = PRE_MOVE_THRESHOLDS.get(asset.category, 0.8)
        if pre_move_pct is not None:
            if best_news.direction == Direction.LONG and pre_move_pct > target_move_expected * pre_move_ratio:
                reason = f"LONG deja price: pre-move +{pre_move_pct:.2f}% > seuil {target_move_expected * pre_move_ratio:.2f}% ({asset.category})"
                logger.info("Skipping %s: %s", ticker, reason)
                rejection_log.append({
                    "title": best_news.news.title, "ticker": [ticker],
                    "reason": reason, "score": raw_score, "pre_move_pct": pre_move_pct,
                })
                continue
            elif best_news.direction == Direction.SHORT and pre_move_pct < -target_move_expected * pre_move_ratio:
                reason = f"SHORT deja price: pre-move {pre_move_pct:.2f}% < seuil -{target_move_expected * pre_move_ratio:.2f}% ({asset.category})"
                logger.info("Skipping %s: %s", ticker, reason)
                rejection_log.append({
                    "title": best_news.news.title, "ticker": [ticker],
                    "reason": reason, "score": raw_score, "pre_move_pct": pre_move_pct,
                })
                continue

        # (#13) Gap buffer for morning scans
        gap_buffer_applied = False
        if prev_close is not None:
            gap_pct = abs((price - prev_close) / prev_close * 100) if prev_close > 0 else 0
            if gap_pct > 0.5:
                gap_buffer_applied = True
                logger.info("Gap buffer applied for %s: gap=%.2f%%", ticker, gap_pct)

        target_price, stop_price, target_pct, stop_pct, rr = _calibrate_trade(
            best_news.direction, price, avg_range, best_news.total_score,
            news_category=best_news.news_category,
        )

        if rr < MIN_RISK_REWARD:
            reason = f"R/R {rr:.2f} < seuil {MIN_RISK_REWARD}"
            logger.info("Skipping %s: %s", ticker, reason)
            rejection_log.append({
                "title": best_news.news.title, "ticker": [ticker],
                "reason": reason, "score": raw_score, "risk_reward": rr,
            })
            continue

        confidence = min(100, int(best_score))

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

        # Build decision summary
        decision_parts.append(
            f"SELECTIONNE #{rank+1}: {ticker} {best_news.direction.value} | "
            f"Score brut Claude={raw_score:.1f}, learning_mult={multiplier:.3f}, "
            f"score ajuste={best_score:.1f} | "
            f"News: '{best_news.news.title[:80]}' | "
            f"Categorie: {best_news.news_category}, edge={best_news.transmission_delay}/{best_news.market_awareness}"
        )
        if len(candidates) > 1:
            runner_up = candidates[1] if rank == 0 else candidates[0]
            decision_parts.append(
                f"Alternative proche: score={runner_up[1]:.1f} '{runner_up[0].news.title[:60]}'"
            )

        # P1-#13: Extract VIX and regime from market_context
        vix_val = market_context.get("vix") if market_context else None
        regime_val = market_context.get("regime") if market_context else None

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
        )

        return ScanResult(
            scan_type=scan_type,
            timestamp=now,
            has_trade=True,
            recommendation=recommendation,
            news_analyzed=len(scored_news),
            market_context=market_context,
            all_scored_news=all_scored_log,
            rejection_log=rejection_log if rejection_log else None,
            decision_summary=" | ".join(decision_parts),
            learning_state=adjustments if adjustments else None,
        )

    # All candidates failed price/R/R checks
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
        learning_state=adjustments if adjustments else None,
    )
