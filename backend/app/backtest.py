"""Backtesting engine (#31): replay historical scans to evaluate parameters.

v5.1: Added run_news_replay_backtest() for re-scoring historical headlines
with current parameters and comparing against actual trade outcomes.
"""

import logging
import statistics
from datetime import datetime, timezone, timedelta

from .config import (
    ASSET_BY_TICKER, CATEGORY_SCORE_MULTIPLIERS, MIN_RISK_REWARD,
    MIN_SCORE_THRESHOLD, assets_for_session,
)
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


def _rescore_headline(news_entry: dict) -> dict:
    """v5.1: Re-score a historical headline using current formula parameters.

    Takes a dict from all_scored_news and recomputes total_score using
    the CURRENT formula (edge_factor, reliability_factor, category_mult).
    This does NOT call Claude — it uses the stored Claude dimensions
    (surprise, delay, awareness, etc.) and applies the current formula.

    Returns dict with original + recalculated fields.
    """
    surprise = news_entry.get("surprise", 0)
    clarity = news_entry.get("directional_clarity", 0)
    delay = news_entry.get("transmission_delay", 50)
    awareness = news_entry.get("market_awareness", 50)
    reliability = news_entry.get("signal_reliability", 50)
    magnitude = news_entry.get("expected_magnitude", 50)
    source_weight = news_entry.get("source_weight", 0.75)
    news_cat = news_entry.get("news_category", "other")

    # Current formula
    edge_factor = max(delay / 100 * (1 - awareness / 100), 0.05)
    reliability_factor = 0.4 + 0.6 * (reliability / 100)
    cat_mult = CATEGORY_SCORE_MULTIPLIERS.get(news_cat, 0.7)

    recalc_score = round(
        surprise * (clarity / 100) * edge_factor * reliability_factor * source_weight * cat_mult,
        2,
    )

    return {
        "title": news_entry.get("title", ""),
        "source": news_entry.get("source", ""),
        "news_category": news_cat,
        "direction": news_entry.get("direction", "NEUTRAL"),
        "impacted_tickers": news_entry.get("impacted_tickers", []),
        "original_score": news_entry.get("total_score", 0),
        "recalculated_score": recalc_score,
        "score_diff": round(recalc_score - news_entry.get("total_score", 0), 2),
        # Stored Claude dimensions (NOT re-evaluated — these are facts)
        "surprise": surprise,
        "directional_clarity": clarity,
        "transmission_delay": delay,
        "market_awareness": awareness,
        "signal_reliability": reliability,
        "expected_magnitude": magnitude,
    }


def run_news_replay_backtest(days: int = 90, override_params: dict | None = None) -> dict:
    """v5.1: Replay historical scan_history news using current scoring parameters.

    Does NOT call Claude API — re-uses stored Claude dimensions (surprise, delay,
    awareness, etc.) and re-applies the CURRENT formula. This answers the question:
    "If we had used today's parameters on historical news, what would have changed?"

    Compares:
    1. Original scores (at time of scan) vs recalculated scores (current formula)
    2. Which trades would have been selected vs actually selected
    3. PnL of actual trades vs hypothetical trades

    Args:
        days: How many days of history to replay (default 90).
        override_params: Optional min_score override.

    Returns comprehensive backtest report.
    """
    from .database import is_pg_enabled
    from .scan_history import load_scan_history
    from .learning import load_trades

    min_score = (override_params or {}).get("min_score", MIN_SCORE_THRESHOLD)

    # Load scan history
    all_scans = load_scan_history()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    recent_scans = [s for s in all_scans
                    if s.timestamp.replace(tzinfo=timezone.utc) > cutoff]

    if not recent_scans:
        return {"error": "No scan history available", "scans_available": len(all_scans)}

    # Load actual trades for comparison
    trades = load_trades()
    closed = [t for t in trades if t.result != TradeResult.PENDING]
    trade_by_key = {}
    for t in closed:
        key = (t.ticker, t.timestamp.strftime("%Y-%m-%d"))
        trade_by_key[key] = t

    # Replay each scan
    total_scans = 0
    scans_with_news = 0
    total_news_rescored = 0
    score_diffs = []
    category_analysis: dict[str, dict] = {}
    would_have_traded: list[dict] = []
    missed_signals: list[dict] = []
    false_positives: list[dict] = []

    for scan in recent_scans:
        total_scans += 1
        if not scan.all_scored_news:
            continue
        scans_with_news += 1
        scan_date = scan.timestamp.strftime("%Y-%m-%d")

        # Re-score all headlines with current formula
        rescored = []
        for news in scan.all_scored_news:
            # Validate: skip entries where Claude dimensions are missing/zero
            # This prevents "invented" data from contaminating the backtest
            if not news.get("title"):
                continue
            if news.get("surprise", 0) == 0 and news.get("directional_clarity", 0) == 0:
                continue  # Skip placeholder/empty entries

            rescored_entry = _rescore_headline(news)
            rescored.append(rescored_entry)
            total_news_rescored += 1

            diff = rescored_entry["score_diff"]
            score_diffs.append(diff)

            # Category analysis
            cat = rescored_entry["news_category"]
            if cat not in category_analysis:
                category_analysis[cat] = {
                    "count": 0, "avg_original": 0.0, "avg_recalc": 0.0,
                    "avg_diff": 0.0, "max_original": 0.0, "max_recalc": 0.0,
                }
            ca = category_analysis[cat]
            ca["count"] += 1
            ca["avg_original"] += rescored_entry["original_score"]
            ca["avg_recalc"] += rescored_entry["recalculated_score"]
            ca["avg_diff"] += diff
            ca["max_original"] = max(ca["max_original"], rescored_entry["original_score"])
            ca["max_recalc"] = max(ca["max_recalc"], rescored_entry["recalculated_score"])

        # Find top signal with current formula
        if rescored:
            top_current = max(rescored, key=lambda x: x["recalculated_score"])
            top_original = max(rescored, key=lambda x: x["original_score"])

            # Would this scan have produced a trade with current params?
            if top_current["recalculated_score"] >= min_score and top_current["direction"] != "NEUTRAL":
                tickers = top_current.get("impacted_tickers", [])
                if tickers:
                    # Check if there was actually a trade for this ticker/date
                    actual_trade = None
                    for tk in tickers:
                        actual_trade = trade_by_key.get((tk, scan_date))
                        if actual_trade:
                            break

                    entry = {
                        "date": scan_date,
                        "scan_type": scan.scan_type.value,
                        "title": top_current["title"][:100],
                        "ticker": tickers[0] if tickers else "?",
                        "direction": top_current["direction"],
                        "recalculated_score": top_current["recalculated_score"],
                        "original_score": top_current["original_score"],
                        "news_category": top_current["news_category"],
                    }

                    if actual_trade:
                        entry["actual_result"] = actual_trade.result.value
                        entry["actual_pnl"] = actual_trade.pnl_pct
                        would_have_traded.append(entry)
                    else:
                        missed_signals.append(entry)

            # Was the original scan's top pick worse than current recalc?
            if (scan.has_trade and top_original["original_score"] >= min_score
                    and top_current["recalculated_score"] < min_score * 0.8):
                # This trade would NOT have been taken with current formula
                false_positives.append({
                    "date": scan_date,
                    "title": top_original["title"][:100],
                    "original_score": top_original["original_score"],
                    "recalculated_score": top_original.get("recalculated_score", 0),
                    "news_category": top_original["news_category"],
                })

    # Finalize category analysis
    for cat, ca in category_analysis.items():
        n = ca["count"]
        if n > 0:
            ca["avg_original"] = round(ca["avg_original"] / n, 2)
            ca["avg_recalc"] = round(ca["avg_recalc"] / n, 2)
            ca["avg_diff"] = round(ca["avg_diff"] / n, 2)

    # Score drift statistics
    score_drift = {}
    if score_diffs:
        score_drift = {
            "mean_diff": round(statistics.mean(score_diffs), 2),
            "median_diff": round(statistics.median(score_diffs), 2),
            "stdev_diff": round(statistics.stdev(score_diffs), 2) if len(score_diffs) >= 2 else 0,
            "max_increase": round(max(score_diffs), 2),
            "max_decrease": round(min(score_diffs), 2),
        }

    # PnL analysis of would-have-traded signals
    pnl_of_confirmed = [t["actual_pnl"] for t in would_have_traded
                        if t.get("actual_pnl") is not None]
    confirmed_stats = {}
    if pnl_of_confirmed:
        confirmed_stats = {
            "total_trades": len(pnl_of_confirmed),
            "total_pnl": round(sum(pnl_of_confirmed), 2),
            "avg_pnl": round(statistics.mean(pnl_of_confirmed), 2),
            "win_rate": round(sum(1 for p in pnl_of_confirmed if p > 0) / len(pnl_of_confirmed) * 100, 1),
        }

    return {
        "period_days": days,
        "min_score_used": min_score,
        "total_scans": total_scans,
        "scans_with_news": scans_with_news,
        "total_news_rescored": total_news_rescored,
        "score_drift": score_drift,
        "category_analysis": category_analysis,
        "would_have_traded": would_have_traded[:20],  # Limit response size
        "would_have_traded_pnl": confirmed_stats,
        "missed_signals": missed_signals[:10],
        "false_positives": false_positives[:10],
    }
