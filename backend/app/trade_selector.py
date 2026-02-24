"""Selects the best trade from scored news and calibrates entry/TP/SL."""

import logging
from datetime import datetime, timezone

import yfinance as yf

from .config import ASSET_BY_TICKER, MIN_RISK_REWARD, MIN_SCORE_THRESHOLD, TARGET_PERCENT, assets_for_session
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


def _get_price_and_range(ticker: str, days: int = 20) -> tuple[float | None, float]:
    """Fetch current price and average daily range in one call.

    Returns (current_price, avg_daily_range_pct).
    """
    try:
        data = yf.Ticker(ticker).history(period=f"{days + 5}d")
        if data.empty:
            return None, 1.5
        current_price = float(data["Close"].iloc[-1])
        if len(data) < 5:
            return current_price, 1.5
        ranges = ((data["High"] - data["Low"]) / data["Close"] * 100).tail(days)
        return current_price, float(ranges.mean())
    except Exception as exc:
        logger.warning("Price/range fetch failed for %s: %s", ticker, exc)
        return None, 1.5


def _calibrate_trade(
    direction: Direction,
    entry_price: float,
    avg_range: float,
    score: float,
) -> tuple[float, float, float, float, float]:
    """Calculate target, stop, percentages and risk/reward.

    Target is score-weighted: higher score → bigger target as fraction of ATR.
    Stop is a fixed fraction of ATR, independent of target.

    Returns (target_price, stop_price, target_pct, stop_pct, risk_reward).
    """
    # Target: score-weighted fraction of ATR
    # At score=40 (min): avg_range * 0.35  |  At score=100: avg_range * 0.7
    score_factor = 0.25 + (score / 100) * 0.45
    target_move_pct = max(TARGET_PERCENT, avg_range * score_factor)

    # Stop: fixed 40% of ATR, independent of target
    stop_move_pct = max(TARGET_PERCENT * 0.7, avg_range * 0.4)

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


def select_trade(
    scored_news: list[ScoredNews],
    scan_type: ScanType,
    learning_adjustments: dict[str, float] | None = None,
) -> ScanResult:
    """Pick the single best trade from scored news.

    Args:
        scored_news: News scored by the LLM, sorted by total_score desc.
        scan_type: europe or us scan.
        learning_adjustments: Optional dict of ticker -> multiplier from learning module.

    Returns:
        ScanResult with either a trade recommendation or a pass.
    """
    now = datetime.now(timezone.utc)

    if not scored_news:
        return ScanResult(
            scan_type=scan_type,
            timestamp=now,
            has_trade=False,
            reason_no_trade="Aucune news collectée",
            news_analyzed=0,
        )

    # Session-eligible tickers
    eligible_tickers = assets_for_session(scan_type.value)

    # Apply learning adjustments to scores
    adjustments = learning_adjustments or {}
    candidates: list[tuple[ScoredNews, float]] = []

    for sn in scored_news:
        if sn.direction == Direction.NEUTRAL:
            continue
        if sn.total_score < MIN_SCORE_THRESHOLD:
            continue

        # Find the best matching ticker from our universe AND eligible for this session
        best_ticker = None
        for t in sn.impacted_tickers:
            if t in ASSET_BY_TICKER and t in eligible_tickers:
                best_ticker = t
                break

        if not best_ticker:
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
        )

    # Try candidates until we find one with a valid price and R/R
    for best_news, best_score in candidates:
        ticker = next(t for t in best_news.impacted_tickers if t in ASSET_BY_TICKER and t in eligible_tickers)
        asset = ASSET_BY_TICKER[ticker]

        price, avg_range = _get_price_and_range(ticker)
        if price is None:
            continue

        target_price, stop_price, target_pct, stop_pct, rr = _calibrate_trade(
            best_news.direction, price, avg_range, best_news.total_score
        )

        if rr < MIN_RISK_REWARD:
            logger.info("Skipping %s: R/R %.2f < %.1f", ticker, rr, MIN_RISK_REWARD)
            continue

        confidence = min(100, int(best_score))

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
        )

        return ScanResult(
            scan_type=scan_type,
            timestamp=now,
            has_trade=True,
            recommendation=recommendation,
            news_analyzed=len(scored_news),
        )

    # All candidates failed price/R/R checks
    return ScanResult(
        scan_type=scan_type,
        timestamp=now,
        has_trade=False,
        reason_no_trade="Impossible de calibrer un trade avec un R/R suffisant",
        news_analyzed=len(scored_news),
    )
