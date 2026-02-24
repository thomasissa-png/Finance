"""Selects the best trade from scored news and calibrates entry/TP/SL."""

import logging
from datetime import datetime, timezone

import yfinance as yf

from .config import ASSET_BY_TICKER, MIN_RISK_REWARD, MIN_SCORE_THRESHOLD, TARGET_PERCENT
from .models import (
    Direction,
    ScanResult,
    ScanType,
    ScoredNews,
    TradeRecommendation,
)

logger = logging.getLogger(__name__)

TIME_WINDOWS = {
    ScanType.EUROPE: "09:00 — 13:00",
    ScanType.US: "15:30 — 19:30",
}


def _get_current_price(ticker: str) -> float | None:
    """Fetch the latest available price for a ticker."""
    try:
        data = yf.Ticker(ticker).history(period="2d")
        if data.empty:
            return None
        return float(data["Close"].iloc[-1])
    except Exception as exc:
        logger.warning("Price fetch failed for %s: %s", ticker, exc)
        return None


def _get_avg_daily_range(ticker: str, days: int = 20) -> float:
    """Average daily high-low range in % over recent days."""
    try:
        data = yf.Ticker(ticker).history(period=f"{days + 5}d")
        if len(data) < 5:
            return 1.5  # Default assumption
        ranges = ((data["High"] - data["Low"]) / data["Close"] * 100).tail(days)
        return float(ranges.mean())
    except Exception:
        return 1.5


def _calibrate_trade(
    ticker: str,
    direction: Direction,
    entry_price: float,
    avg_range: float,
) -> tuple[float, float, float, float, float]:
    """Calculate target, stop, percentages and risk/reward.

    Returns (target_price, stop_price, target_pct, stop_pct, risk_reward).
    """
    # Target: at least TARGET_PERCENT, but adapted to the asset's typical range
    target_move_pct = max(TARGET_PERCENT, avg_range * 0.5)
    # Stop: tighter than target to get R/R >= 1
    stop_move_pct = target_move_pct * 0.85

    if direction == Direction.LONG:
        target_price = entry_price * (1 + target_move_pct / 100)
        stop_price = entry_price * (1 - stop_move_pct / 100)
    else:
        target_price = entry_price * (1 - target_move_pct / 100)
        stop_price = entry_price * (1 + stop_move_pct / 100)

    target_pct = target_move_pct
    stop_pct = stop_move_pct
    risk_reward = round(target_move_pct / stop_move_pct, 2) if stop_move_pct > 0 else 0

    return (
        round(target_price, 4),
        round(stop_price, 4),
        round(target_pct, 2),
        round(stop_pct, 2),
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

    # Apply learning adjustments to scores
    adjustments = learning_adjustments or {}
    candidates: list[tuple[ScoredNews, float]] = []

    for sn in scored_news:
        if sn.direction == Direction.NEUTRAL:
            continue
        if sn.total_score < MIN_SCORE_THRESHOLD:
            continue

        # Find the best matching ticker from our universe
        best_ticker = None
        for t in sn.impacted_tickers:
            if t in ASSET_BY_TICKER:
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

    # Take the top candidate
    best_news, best_score = candidates[0]
    ticker = next(t for t in best_news.impacted_tickers if t in ASSET_BY_TICKER)
    asset = ASSET_BY_TICKER[ticker]

    # Get current price
    price = _get_current_price(ticker)
    if price is None:
        # Try next candidate
        for sn, sc in candidates[1:]:
            t = next((t for t in sn.impacted_tickers if t in ASSET_BY_TICKER), None)
            if t:
                price = _get_current_price(t)
                if price is not None:
                    ticker = t
                    asset = ASSET_BY_TICKER[t]
                    best_news = sn
                    best_score = sc
                    break

    if price is None:
        return ScanResult(
            scan_type=scan_type,
            timestamp=now,
            has_trade=False,
            reason_no_trade="Impossible de récupérer le prix des actifs candidats",
            news_analyzed=len(scored_news),
        )

    avg_range = _get_avg_daily_range(ticker)
    target_price, stop_price, target_pct, stop_pct, rr = _calibrate_trade(
        ticker, best_news.direction, price, avg_range
    )

    if rr < MIN_RISK_REWARD:
        return ScanResult(
            scan_type=scan_type,
            timestamp=now,
            has_trade=False,
            reason_no_trade=f"Ratio R/R insuffisant ({rr})",
            news_analyzed=len(scored_news),
        )

    confidence = min(100, int(best_score))

    recommendation = TradeRecommendation(
        scan_type=scan_type,
        timestamp=now,
        ticker=ticker,
        asset_name=asset.name,
        category=asset.category,
        direction=best_news.direction,
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
