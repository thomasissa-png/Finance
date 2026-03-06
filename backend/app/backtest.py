"""Backtesting engine (#31): replay historical scans to evaluate parameters."""

import logging
import statistics
from datetime import datetime, timezone

from .config import ASSET_BY_TICKER, MIN_RISK_REWARD, MIN_SCORE_THRESHOLD, assets_for_session
from .journal import _determine_result
from .market_data import fetch_history_range
from .models import Direction, ScanType, TradeRecommendation, TradeResult
from .trade_selector import _calibrate_trade

logger = logging.getLogger(__name__)


def _fetch_historical_prices(ticker: str, start: str, end: str) -> list[dict]:
    """Fetch historical OHLCV data for a ticker.

    Uses Twelve Data (primary) with yfinance fallback via market_data module.
    """
    try:
        data = fetch_history_range(ticker, start=start, end=end, interval="1day")
        if data is None or data.empty:
            return []
        rows = []
        for dt, row in data.iterrows():
            rows.append({
                "date": dt.strftime("%Y-%m-%d"),
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

    # v3.6 (J): Advanced backtesting metrics
    pnl_series = [t["pnl_pct"] for t in results["trades"] if t["pnl_pct"] is not None]

    # Sharpe ratio (annualized, assuming ~250 trading days)
    if len(pnl_series) >= 2:
        import statistics
        mean_pnl = statistics.mean(pnl_series)
        std_pnl = statistics.stdev(pnl_series)
        if std_pnl > 0:
            # Approximate annualization: sqrt(trades_per_year)
            trades_per_year = min(250, len(pnl_series) * 4)  # rough estimate
            results["sharpe_ratio"] = round(mean_pnl / std_pnl * (trades_per_year ** 0.5), 2)
        else:
            results["sharpe_ratio"] = 0.0
    else:
        results["sharpe_ratio"] = 0.0

    # Max drawdown
    if pnl_series:
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for pnl in pnl_series:
            cumulative += pnl
            peak = max(peak, cumulative)
            dd = peak - cumulative
            max_dd = max(max_dd, dd)
        results["max_drawdown_pct"] = round(max_dd, 4)
    else:
        results["max_drawdown_pct"] = 0.0

    # Profit factor (gross wins / gross losses)
    gross_wins = sum(p for p in pnl_series if p > 0)
    gross_losses = abs(sum(p for p in pnl_series if p < 0))
    results["profit_factor"] = round(gross_wins / gross_losses, 2) if gross_losses > 0 else float("inf") if gross_wins > 0 else 0.0

    # Average win / average loss
    wins_list = [p for p in pnl_series if p > 0]
    losses_list = [p for p in pnl_series if p < 0]
    results["avg_win_pct"] = round(statistics.mean(wins_list), 4) if wins_list else 0.0
    results["avg_loss_pct"] = round(statistics.mean(losses_list), 4) if losses_list else 0.0

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
