"""Backtesting engine (#31): replay historical scans to evaluate parameters."""

import logging
from datetime import datetime, timezone

import yfinance as yf

from .config import ASSET_BY_TICKER, MIN_RISK_REWARD, MIN_SCORE_THRESHOLD, assets_for_session
from .journal import _determine_result
from .models import Direction, ScanType, TradeRecommendation, TradeResult
from .trade_selector import _calibrate_trade

logger = logging.getLogger(__name__)


def _fetch_historical_prices(ticker: str, start: str, end: str) -> list[dict]:
    """Fetch historical OHLCV data for a ticker."""
    try:
        data = yf.Ticker(ticker).history(start=start, end=end)
        if data.empty:
            return []
        rows = []
        for date, row in data.iterrows():
            rows.append({
                "date": date.strftime("%Y-%m-%d"),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": float(row.get("Volume", 0)),
            })
        return rows
    except Exception as exc:
        logger.warning("Historical data fetch failed for %s: %s", ticker, exc)
        return []


def run_backtest(
    trades: list[TradeRecommendation],
    override_params: dict | None = None,
) -> dict:
    """Run backtest on historical trades with optional parameter overrides.

    Args:
        trades: List of historical trades to replay.
        override_params: Optional dict of parameter overrides:
            - min_score: Override MIN_SCORE_THRESHOLD
            - min_rr: Override MIN_RISK_REWARD

    Returns dict with backtest results.
    """
    params = {
        "min_score": MIN_SCORE_THRESHOLD,
        "min_rr": MIN_RISK_REWARD,
    }
    if override_params:
        params.update(override_params)

    results = {
        "params": params,
        "total_trades": 0,
        "filtered_trades": 0,
        "wins": 0,
        "losses": 0,
        "expired": 0,
        "total_pnl": 0.0,
        "avg_pnl": 0.0,
        "win_rate": 0.0,
        "trades": [],
    }

    closed = [t for t in trades if t.result != TradeResult.PENDING]

    for trade in closed:
        # Apply filter with override params
        if trade.confidence < params["min_score"]:
            results["filtered_trades"] += 1
            continue
        if trade.risk_reward < params["min_rr"]:
            results["filtered_trades"] += 1
            continue

        results["total_trades"] += 1

        if trade.result == TradeResult.TP_HIT:
            results["wins"] += 1
        elif trade.result == TradeResult.SL_HIT:
            results["losses"] += 1
        else:
            results["expired"] += 1

        if trade.pnl_pct is not None:
            results["total_pnl"] += trade.pnl_pct

        results["trades"].append({
            "date": trade.timestamp.isoformat(),
            "ticker": trade.ticker,
            "direction": trade.direction.value,
            "confidence": trade.confidence,
            "rr": trade.risk_reward,
            "result": trade.result.value,
            "pnl_pct": trade.pnl_pct,
        })

    if results["total_trades"] > 0:
        results["avg_pnl"] = round(results["total_pnl"] / results["total_trades"], 4)
        results["win_rate"] = round(results["wins"] / results["total_trades"] * 100, 1)
    results["total_pnl"] = round(results["total_pnl"], 4)

    return results


def run_parameter_sweep(trades: list[TradeRecommendation]) -> list[dict]:
    """Run backtest across multiple parameter combinations to find optimal settings.

    Tests combinations of min_score (40-70) and min_rr (1.0-2.0).
    Returns list of backtest results sorted by total_pnl desc.
    """
    sweep_results = []

    for min_score in range(40, 75, 5):
        for min_rr_x10 in range(10, 21, 2):  # 1.0, 1.2, 1.4, 1.6, 1.8, 2.0
            min_rr = min_rr_x10 / 10
            result = run_backtest(trades, {"min_score": min_score, "min_rr": min_rr})
            # Don't include individual trades in sweep (too much data)
            result.pop("trades", None)
            sweep_results.append(result)

    sweep_results.sort(key=lambda r: r["total_pnl"], reverse=True)
    return sweep_results
