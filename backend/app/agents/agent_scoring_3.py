"""Agent Scoring 3 — Technical Indicators Scoring (Team 3).

Responsibilities:
- Computes technical indicator scores for trading signals
- NOT based on news — Team 3 trades purely on technical indicators
- Multi-timeframe analysis: 15min, 1h, 4h, daily
- Indicators: RSI (14, 21), MACD (12,26,9), Bollinger Bands (20,2),
  SMA/EMA crossovers (20/50, 50/200), Stochastic, ADX, Volume analysis
- For each ticker in TECH_TICKERS (20 liquid assets)
- Produces scored trade setups with direction, strategy, score, targets
- Publishes "tech_scored" on bus -> Trader 3 consumes

Differences with Scoring 1/2:
- Scoring 1: evaluates news edge (transmission_delay, market_awareness)
- Scoring 2: evaluates trend relevance for commodity news
- Scoring 3: evaluates technical setups (indicators, price action)
- NO Claude API calls — pure computational analysis
- Multi-timeframe confluence scoring
"""

import logging
import math
import time
from datetime import datetime, timezone

from .base import BaseAgent, AgentStatus

logger = logging.getLogger(__name__)

# ── Technical Tickers Universe ────────────────────────────────────
# 20 liquid assets across forex, indices, commodities, and equities
TECH_TICKERS = {
    # Forex (4)
    "EURUSD=X": {"name": "EUR/USD", "category": "forex", "atr_mult": 1.0},
    "GBPUSD=X": {"name": "GBP/USD", "category": "forex", "atr_mult": 1.0},
    "USDJPY=X": {"name": "USD/JPY", "category": "forex", "atr_mult": 1.0},
    "AUDUSD=X": {"name": "AUD/USD", "category": "forex", "atr_mult": 1.0},
    # Indices (3)
    "^GSPC":    {"name": "S&P 500", "category": "indices", "atr_mult": 0.8},
    "^FCHI":    {"name": "CAC 40", "category": "indices", "atr_mult": 0.8},
    "^GDAXI":   {"name": "DAX", "category": "indices", "atr_mult": 0.8},
    # Commodities (5)
    "GC=F":     {"name": "Gold", "category": "commodities", "atr_mult": 1.2},
    "CL=F":     {"name": "WTI Crude", "category": "commodities", "atr_mult": 1.3},
    "BZ=F":     {"name": "Brent Crude", "category": "commodities", "atr_mult": 1.3},
    "HG=F":     {"name": "Copper", "category": "commodities", "atr_mult": 1.2},
    "SI=F":     {"name": "Silver", "category": "commodities", "atr_mult": 1.3},
    # Agriculture (2)
    "ZC=F":     {"name": "Corn", "category": "commodities", "atr_mult": 1.2},
    "ZW=F":     {"name": "Wheat", "category": "commodities", "atr_mult": 1.2},
    # Equities (6)
    "AAPL":     {"name": "Apple", "category": "equities", "atr_mult": 1.0},
    "MSFT":     {"name": "Microsoft", "category": "equities", "atr_mult": 1.0},
    "TSLA":     {"name": "Tesla", "category": "equities", "atr_mult": 1.5},
    "AMZN":     {"name": "Amazon", "category": "equities", "atr_mult": 1.0},
    "BNP.PA":   {"name": "BNP Paribas", "category": "equities", "atr_mult": 1.0},
    "TTE.PA":   {"name": "TotalEnergies", "category": "equities", "atr_mult": 1.0},
}

# ── Timeframes ────────────────────────────────────────────────────
TIMEFRAMES = ["15m", "1h", "4h", "1d"]
TIMEFRAME_WEIGHTS = {"15m": 0.15, "1h": 0.25, "4h": 0.30, "1d": 0.30}

# ── Strategy definitions ──────────────────────────────────────────
STRATEGIES = {
    "rsi_reversal": {
        "name": "RSI Reversal",
        "description": "RSI oversold/overbought with confirmation",
        "weight": 1.0,
    },
    "macd_crossover": {
        "name": "MACD Crossover",
        "description": "MACD signal line cross with trend filter",
        "weight": 1.0,
    },
    "bollinger_squeeze": {
        "name": "Bollinger Squeeze",
        "description": "Low volatility breakout from Bollinger Band compression",
        "weight": 0.9,
    },
    "ma_trend": {
        "name": "MA Trend",
        "description": "Moving average alignment (20/50/200)",
        "weight": 0.8,
    },
    "momentum_divergence": {
        "name": "Momentum Divergence",
        "description": "Price/indicator divergence (RSI or MACD)",
        "weight": 1.1,
    },
}

# ── Indicator parameters ──────────────────────────────────────────
RSI_PERIODS = [14, 21]
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
BB_PERIOD = 20
BB_STD = 2.0
SMA_FAST = 20
SMA_MID = 50
SMA_SLOW = 200
STOCH_K = 14
STOCH_D = 3
ADX_PERIOD = 14
ADX_TREND_THRESHOLD = 25  # ADX > 25 = trending market

# Minimum score to emit a setup
MIN_SETUP_SCORE = 30.0

# Per-fetch timeout
PER_FETCH_TIMEOUT_S = 15


# ── Indicator computation helpers ─────────────────────────────────

def _compute_sma(closes: list[float], period: int) -> list[float]:
    """Simple Moving Average."""
    if len(closes) < period:
        return []
    result = []
    for i in range(period - 1, len(closes)):
        result.append(sum(closes[i - period + 1:i + 1]) / period)
    return result


def _compute_ema(closes: list[float], period: int) -> list[float]:
    """Exponential Moving Average."""
    if len(closes) < period:
        return []
    k = 2.0 / (period + 1)
    ema = [sum(closes[:period]) / period]
    for i in range(period, len(closes)):
        ema.append(closes[i] * k + ema[-1] * (1 - k))
    return ema


def _compute_rsi(closes: list[float], period: int = 14) -> float | None:
    """Relative Strength Index (last value)."""
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def _compute_macd(closes: list[float]) -> dict | None:
    """MACD line, signal line, histogram (last values)."""
    ema_fast = _compute_ema(closes, MACD_FAST)
    ema_slow = _compute_ema(closes, MACD_SLOW)
    if not ema_fast or not ema_slow:
        return None

    # Align lengths
    diff = len(ema_fast) - len(ema_slow)
    if diff > 0:
        ema_fast = ema_fast[diff:]

    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    if len(macd_line) < MACD_SIGNAL:
        return None

    signal = _compute_ema(macd_line, MACD_SIGNAL)
    if not signal:
        return None

    # Align
    macd_trimmed = macd_line[len(macd_line) - len(signal):]
    histogram = [m - s for m, s in zip(macd_trimmed, signal)]

    return {
        "macd": macd_trimmed[-1] if macd_trimmed else 0,
        "signal": signal[-1] if signal else 0,
        "histogram": histogram[-1] if histogram else 0,
        "prev_histogram": histogram[-2] if len(histogram) >= 2 else 0,
        "prev_macd": macd_trimmed[-2] if len(macd_trimmed) >= 2 else 0,
        "prev_signal": signal[-2] if len(signal) >= 2 else 0,
    }


def _compute_bollinger(closes: list[float]) -> dict | None:
    """Bollinger Bands (last values)."""
    if len(closes) < BB_PERIOD:
        return None
    window = closes[-BB_PERIOD:]
    sma = sum(window) / BB_PERIOD
    std = math.sqrt(sum((c - sma) ** 2 for c in window) / BB_PERIOD)

    upper = sma + BB_STD * std
    lower = sma - BB_STD * std
    bandwidth = (upper - lower) / sma * 100 if sma > 0 else 0

    return {
        "upper": upper,
        "middle": sma,
        "lower": lower,
        "bandwidth": round(bandwidth, 4),
        "pct_b": round((closes[-1] - lower) / (upper - lower), 4) if (upper - lower) > 0 else 0.5,
    }


def _compute_stochastic(highs: list[float], lows: list[float],
                         closes: list[float]) -> dict | None:
    """Stochastic Oscillator %K and %D."""
    if len(closes) < STOCH_K:
        return None

    k_values = []
    for i in range(STOCH_K - 1, len(closes)):
        window_highs = highs[i - STOCH_K + 1:i + 1]
        window_lows = lows[i - STOCH_K + 1:i + 1]
        highest = max(window_highs)
        lowest = min(window_lows)
        if highest == lowest:
            k_values.append(50.0)
        else:
            k_values.append((closes[i] - lowest) / (highest - lowest) * 100)

    if len(k_values) < STOCH_D:
        return None

    d_values = _compute_sma(k_values, STOCH_D)

    return {
        "k": round(k_values[-1], 2) if k_values else 50.0,
        "d": round(d_values[-1], 2) if d_values else 50.0,
    }


def _compute_adx(highs: list[float], lows: list[float],
                  closes: list[float], period: int = ADX_PERIOD) -> float | None:
    """Average Directional Index (simplified)."""
    if len(closes) < period * 2:
        return None

    plus_dm = []
    minus_dm = []
    tr_list = []

    for i in range(1, len(closes)):
        high_diff = highs[i] - highs[i - 1]
        low_diff = lows[i - 1] - lows[i]

        plus_dm.append(max(high_diff, 0) if high_diff > low_diff else 0)
        minus_dm.append(max(low_diff, 0) if low_diff > high_diff else 0)

        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        tr_list.append(tr)

    if len(tr_list) < period:
        return None

    # Smoothed averages
    atr = sum(tr_list[:period]) / period
    plus_di_smooth = sum(plus_dm[:period]) / period
    minus_di_smooth = sum(minus_dm[:period]) / period

    dx_values = []
    for i in range(period, len(tr_list)):
        atr = (atr * (period - 1) + tr_list[i]) / period
        plus_di_smooth = (plus_di_smooth * (period - 1) + plus_dm[i]) / period
        minus_di_smooth = (minus_di_smooth * (period - 1) + minus_dm[i]) / period

        if atr == 0:
            continue
        plus_di = (plus_di_smooth / atr) * 100
        minus_di = (minus_di_smooth / atr) * 100
        di_sum = plus_di + minus_di
        if di_sum == 0:
            continue
        dx = abs(plus_di - minus_di) / di_sum * 100
        dx_values.append(dx)

    if len(dx_values) < period:
        return None

    adx = sum(dx_values[:period]) / period
    for i in range(period, len(dx_values)):
        adx = (adx * (period - 1) + dx_values[i]) / period

    return round(adx, 2)


def _compute_atr(highs: list[float], lows: list[float],
                  closes: list[float], period: int = 14) -> float | None:
    """Average True Range."""
    if len(closes) < period + 1:
        return None

    tr_list = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        tr_list.append(tr)

    if len(tr_list) < period:
        return None

    atr = sum(tr_list[-period:]) / period
    return round(atr, 4)


# ── Strategy detection ────────────────────────────────────────────

def _detect_rsi_reversal(indicators: dict) -> dict | None:
    """Detect RSI reversal setups."""
    rsi_14 = indicators.get("rsi_14")
    rsi_21 = indicators.get("rsi_21")
    adx = indicators.get("adx")

    if rsi_14 is None:
        return None

    score = 0
    direction = None

    # Oversold reversal (LONG)
    if rsi_14 < RSI_OVERSOLD:
        score = 50 + (RSI_OVERSOLD - rsi_14) * 2  # Deeper oversold = higher score
        direction = "LONG"
        # Confirmation: RSI 21 also oversold
        if rsi_21 is not None and rsi_21 < RSI_OVERSOLD + 5:
            score += 15
        # ADX confirmation: ranging market better for reversal
        if adx is not None and adx < ADX_TREND_THRESHOLD:
            score += 10

    # Overbought reversal (SHORT)
    elif rsi_14 > RSI_OVERBOUGHT:
        score = 50 + (rsi_14 - RSI_OVERBOUGHT) * 2
        direction = "SHORT"
        if rsi_21 is not None and rsi_21 > RSI_OVERBOUGHT - 5:
            score += 15
        if adx is not None and adx < ADX_TREND_THRESHOLD:
            score += 10

    if direction is None or score < MIN_SETUP_SCORE:
        return None

    return {
        "strategy": "rsi_reversal",
        "direction": direction,
        "score": min(100, round(score, 1)),
        "signals": {
            "rsi_14": rsi_14,
            "rsi_21": rsi_21,
            "adx": adx,
        },
    }


def _detect_macd_crossover(indicators: dict) -> dict | None:
    """Detect MACD crossover setups."""
    macd = indicators.get("macd")
    adx = indicators.get("adx")

    if macd is None:
        return None

    score = 0
    direction = None

    # Bullish crossover: MACD crosses above signal
    if macd["prev_macd"] <= macd["prev_signal"] and macd["macd"] > macd["signal"]:
        direction = "LONG"
        score = 45
        # Histogram growing
        if macd["histogram"] > macd["prev_histogram"]:
            score += 15
        # Below zero line (stronger signal)
        if macd["macd"] < 0:
            score += 10
        # Trending market amplifies signal
        if adx is not None and adx > ADX_TREND_THRESHOLD:
            score += 10

    # Bearish crossover: MACD crosses below signal
    elif macd["prev_macd"] >= macd["prev_signal"] and macd["macd"] < macd["signal"]:
        direction = "SHORT"
        score = 45
        if macd["histogram"] < macd["prev_histogram"]:
            score += 15
        if macd["macd"] > 0:
            score += 10
        if adx is not None and adx > ADX_TREND_THRESHOLD:
            score += 10

    if direction is None or score < MIN_SETUP_SCORE:
        return None

    return {
        "strategy": "macd_crossover",
        "direction": direction,
        "score": min(100, round(score, 1)),
        "signals": {
            "macd_line": round(macd["macd"], 4),
            "signal_line": round(macd["signal"], 4),
            "histogram": round(macd["histogram"], 4),
            "adx": adx,
        },
    }


def _detect_bollinger_squeeze(indicators: dict) -> dict | None:
    """Detect Bollinger Band squeeze/breakout setups."""
    bb = indicators.get("bollinger")
    adx = indicators.get("adx")
    rsi_14 = indicators.get("rsi_14")
    last_close = indicators.get("last_close")

    if bb is None or last_close is None:
        return None

    score = 0
    direction = None

    # Squeeze: bandwidth is narrow (< 3% for most assets)
    # Breakout: price near or outside bands
    if bb["bandwidth"] < 3.0:
        # Breakout above upper band
        if bb["pct_b"] > 0.95:
            direction = "LONG"
            score = 50 + (bb["pct_b"] - 0.95) * 200
        # Breakout below lower band
        elif bb["pct_b"] < 0.05:
            direction = "SHORT"
            score = 50 + (0.05 - bb["pct_b"]) * 200

        # Tighter squeeze = stronger signal
        if bb["bandwidth"] < 1.5:
            score += 15
        elif bb["bandwidth"] < 2.0:
            score += 10

        # ADX rising from low = breakout confirmation
        if adx is not None and adx < ADX_TREND_THRESHOLD:
            score += 10

        # RSI confirmation
        if rsi_14 is not None and direction == "LONG" and rsi_14 > 50:
            score += 5
        elif rsi_14 is not None and direction == "SHORT" and rsi_14 < 50:
            score += 5

    if direction is None or score < MIN_SETUP_SCORE:
        return None

    return {
        "strategy": "bollinger_squeeze",
        "direction": direction,
        "score": min(100, round(score, 1)),
        "signals": {
            "bandwidth": bb["bandwidth"],
            "pct_b": bb["pct_b"],
            "adx": adx,
            "rsi_14": rsi_14,
        },
    }


def _detect_ma_trend(indicators: dict) -> dict | None:
    """Detect moving average alignment setups."""
    sma_20 = indicators.get("sma_20")
    sma_50 = indicators.get("sma_50")
    ema_20 = indicators.get("ema_20")
    adx = indicators.get("adx")
    last_close = indicators.get("last_close")

    if sma_20 is None or sma_50 is None or last_close is None:
        return None

    score = 0
    direction = None

    # Bullish alignment: price > EMA20 > SMA20 > SMA50
    if last_close > sma_20 > sma_50:
        direction = "LONG"
        score = 40
        # EMA20 confirmation
        if ema_20 is not None and last_close > ema_20:
            score += 10
        # Price well above SMA50 (strong trend)
        if sma_50 > 0:
            distance = (last_close - sma_50) / sma_50 * 100
            if distance > 2:
                score += 10
        # ADX confirms trend
        if adx is not None and adx > ADX_TREND_THRESHOLD:
            score += 15
        elif adx is not None and adx > 20:
            score += 5

    # Bearish alignment: price < EMA20 < SMA20 < SMA50
    elif last_close < sma_20 < sma_50:
        direction = "SHORT"
        score = 40
        if ema_20 is not None and last_close < ema_20:
            score += 10
        if sma_50 > 0:
            distance = (sma_50 - last_close) / sma_50 * 100
            if distance > 2:
                score += 10
        if adx is not None and adx > ADX_TREND_THRESHOLD:
            score += 15
        elif adx is not None and adx > 20:
            score += 5

    if direction is None or score < MIN_SETUP_SCORE:
        return None

    return {
        "strategy": "ma_trend",
        "direction": direction,
        "score": min(100, round(score, 1)),
        "signals": {
            "sma_20": round(sma_20, 4),
            "sma_50": round(sma_50, 4),
            "ema_20": round(ema_20, 4) if ema_20 else None,
            "adx": adx,
            "close": round(last_close, 4),
        },
    }


def _detect_momentum_divergence(indicators: dict) -> dict | None:
    """Detect price/RSI divergence setups.

    Bullish divergence: price makes lower low, RSI makes higher low.
    Bearish divergence: price makes higher high, RSI makes lower high.
    Simplified: compare current vs recent levels.
    """
    rsi_14 = indicators.get("rsi_14")
    rsi_prev = indicators.get("rsi_prev")
    last_close = indicators.get("last_close")
    prev_close = indicators.get("prev_close")
    macd = indicators.get("macd")

    if rsi_14 is None or rsi_prev is None or last_close is None or prev_close is None:
        return None

    score = 0
    direction = None

    # Bullish divergence: price lower, RSI higher
    if last_close < prev_close and rsi_14 > rsi_prev:
        direction = "LONG"
        price_drop = (prev_close - last_close) / prev_close * 100 if prev_close > 0 else 0
        rsi_rise = rsi_14 - rsi_prev
        score = 35 + price_drop * 5 + rsi_rise * 2

        # MACD histogram also diverging
        if macd and macd["histogram"] > macd["prev_histogram"]:
            score += 15

        # RSI in oversold zone amplifies
        if rsi_14 < 40:
            score += 10

    # Bearish divergence: price higher, RSI lower
    elif last_close > prev_close and rsi_14 < rsi_prev:
        direction = "SHORT"
        price_rise = (last_close - prev_close) / prev_close * 100 if prev_close > 0 else 0
        rsi_drop = rsi_prev - rsi_14
        score = 35 + price_rise * 5 + rsi_drop * 2

        if macd and macd["histogram"] < macd["prev_histogram"]:
            score += 15

        if rsi_14 > 60:
            score += 10

    if direction is None or score < MIN_SETUP_SCORE:
        return None

    return {
        "strategy": "momentum_divergence",
        "direction": direction,
        "score": min(100, round(score, 1)),
        "signals": {
            "rsi_14": rsi_14,
            "rsi_prev": rsi_prev,
            "price_change_pct": round(
                (last_close - prev_close) / prev_close * 100, 2
            ) if prev_close > 0 else 0,
            "macd_histogram": round(macd["histogram"], 4) if macd else None,
        },
    }


# ── Main scoring function ────────────────────────────────────────

def _fetch_ohlcv(ticker: str, period: str = "3mo", interval: str = "1d") -> dict | None:
    """Fetch OHLCV data for a ticker via market_data (Twelve Data + yfinance fallback).

    Returns dict with lists of open/high/low/close/volume.
    """
    try:
        from ..market_data import fetch_history

        # Map yfinance-style period to days
        period_map = {"1mo": 30, "2mo": 60, "3mo": 90, "6mo": 180}
        period_days = period_map.get(period, 90)

        # Map yfinance-style interval to Twelve Data format
        interval_map = {"1d": "1day", "1h": "1h", "4h": "4h", "15m": "15min"}
        td_interval = interval_map.get(interval, "1day")

        h = fetch_history(ticker, period_days=period_days, interval=td_interval)
        if h is None or h.empty or len(h) < 30:
            return None
        return {
            "open": list(h["Open"]),
            "high": list(h["High"]),
            "low": list(h["Low"]),
            "close": list(h["Close"]),
            "volume": list(h["Volume"]) if "Volume" in h.columns else [],
        }
    except Exception as exc:
        logger.warning("OHLCV fetch failed for %s (%s/%s): %s",
                       ticker, period, interval, exc)
        return None


def _compute_all_indicators(ohlcv: dict) -> dict:
    """Compute all technical indicators from OHLCV data."""
    closes = ohlcv["close"]
    highs = ohlcv["high"]
    lows = ohlcv["low"]

    indicators = {
        "last_close": closes[-1] if closes else None,
        "prev_close": closes[-5] if len(closes) >= 5 else (closes[-2] if len(closes) >= 2 else None),
    }

    # RSI
    indicators["rsi_14"] = _compute_rsi(closes, 14)
    indicators["rsi_21"] = _compute_rsi(closes, 21)
    # Prev RSI (5 bars ago for divergence detection)
    if len(closes) > 5:
        indicators["rsi_prev"] = _compute_rsi(closes[:-5], 14)

    # MACD
    indicators["macd"] = _compute_macd(closes)

    # Bollinger Bands
    indicators["bollinger"] = _compute_bollinger(closes)

    # Moving Averages
    sma_20 = _compute_sma(closes, SMA_FAST)
    sma_50 = _compute_sma(closes, SMA_MID)
    ema_20 = _compute_ema(closes, SMA_FAST)
    indicators["sma_20"] = sma_20[-1] if sma_20 else None
    indicators["sma_50"] = sma_50[-1] if sma_50 else None
    indicators["ema_20"] = ema_20[-1] if ema_20 else None

    # Stochastic
    indicators["stochastic"] = _compute_stochastic(highs, lows, closes)

    # ADX
    indicators["adx"] = _compute_adx(highs, lows, closes)

    # ATR
    indicators["atr"] = _compute_atr(highs, lows, closes)

    # Volume analysis
    volumes = ohlcv.get("volume", [])
    if volumes and len(volumes) >= 20:
        avg_vol = sum(volumes[-20:]) / 20
        indicators["volume_ratio"] = round(volumes[-1] / avg_vol, 2) if avg_vol > 0 else 1.0
    else:
        indicators["volume_ratio"] = 1.0

    return indicators


def score_technical_setups(tickers: dict | None = None) -> dict:
    """Compute technical scores for all tickers.

    Returns dict with:
        - setups: list[dict] — all detected setups sorted by score desc
        - by_ticker: dict[ticker, list[dict]] — grouped by ticker
        - by_strategy: dict[strategy, list[dict]] — grouped by strategy
        - stats: {total_tickers, setups_found, avg_score, top_strategy}
    """
    target_tickers = tickers or TECH_TICKERS

    result = {
        "setups": [],
        "by_ticker": {},
        "by_strategy": {},
        "stats": {
            "total_tickers": len(target_tickers),
            "setups_found": 0,
            "avg_score": 0.0,
            "top_strategy": "",
        },
    }

    all_setups = []
    strategy_detectors = [
        _detect_rsi_reversal,
        _detect_macd_crossover,
        _detect_bollinger_squeeze,
        _detect_ma_trend,
        _detect_momentum_divergence,
    ]

    for ticker, info in target_tickers.items():
        # Fetch daily data (primary timeframe)
        ohlcv = _fetch_ohlcv(ticker, period="6mo", interval="1d")
        if not ohlcv:
            continue

        indicators = _compute_all_indicators(ohlcv)
        atr = indicators.get("atr")
        last_close = indicators.get("last_close")

        if not last_close or last_close <= 0:
            continue

        # Run all strategy detectors
        for detector in strategy_detectors:
            try:
                setup = detector(indicators)
            except Exception as exc:
                logger.warning("Strategy detector failed for %s: %s", ticker, exc)
                continue

            if setup is None:
                continue

            strategy_name = setup["strategy"]
            strategy_weight = STRATEGIES.get(strategy_name, {}).get("weight", 1.0)

            # Volume boost: high volume confirms setup
            vol_ratio = indicators.get("volume_ratio", 1.0)
            volume_boost = 1.0
            if vol_ratio > 1.5:
                volume_boost = 1.15
            elif vol_ratio > 2.0:
                volume_boost = 1.25

            # Compute final score with strategy weight and volume
            final_score = min(100, setup["score"] * strategy_weight * volume_boost)

            # Compute target and stop from ATR
            atr_mult = info.get("atr_mult", 1.0)
            target_pct = 0.0
            stop_pct = 0.0
            if atr and last_close > 0:
                atr_pct = atr / last_close * 100
                target_pct = round(atr_pct * 1.5 * atr_mult, 2)
                stop_pct = round(atr_pct * 1.0 * atr_mult, 2)

            entry = {
                "ticker": ticker,
                "name": info["name"],
                "category": info["category"],
                "strategy": strategy_name,
                "strategy_name": STRATEGIES[strategy_name]["name"],
                "direction": setup["direction"],
                "score": round(final_score, 1),
                "confidence": min(100, int(final_score * 0.9)),
                "entry_price": round(last_close, 4),
                "target_pct": target_pct,
                "stop_pct": stop_pct,
                "atr": atr,
                "volume_ratio": vol_ratio,
                "adx": indicators.get("adx"),
                "signals": setup.get("signals", {}),
                "timeframe": "1d",  # Primary timeframe used
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            all_setups.append(entry)

    # Sort by score descending
    all_setups.sort(key=lambda s: s["score"], reverse=True)

    result["setups"] = all_setups

    # Group by ticker
    for setup in all_setups:
        result["by_ticker"].setdefault(setup["ticker"], []).append(setup)

    # Group by strategy
    strategy_counts: dict[str, int] = {}
    for setup in all_setups:
        strat = setup["strategy"]
        result["by_strategy"].setdefault(strat, []).append(setup)
        strategy_counts[strat] = strategy_counts.get(strat, 0) + 1

    # Stats
    result["stats"]["setups_found"] = len(all_setups)
    if all_setups:
        result["stats"]["avg_score"] = round(
            sum(s["score"] for s in all_setups) / len(all_setups), 1
        )
    if strategy_counts:
        result["stats"]["top_strategy"] = max(strategy_counts, key=strategy_counts.get)

    return result


class AgentScoring3(BaseAgent):
    """Agent Scoring 3 — Technical Indicators Scoring for Team 3.

    Computes technical indicator scores across 20 liquid assets.
    Purely computational — no Claude API calls.
    """

    name = "scoring_3"
    description = "Technical indicators scoring — multi-strategy, multi-timeframe"
    version = "1.0"

    def __init__(self):
        super().__init__()
        self._last_setups_count: int = 0
        self._last_avg_score: float = 0.0
        self._total_scorings: int = 0
        self._last_result: dict | None = None

    def run(self, **kwargs) -> dict:
        """Score all tickers for technical setups.

        Returns dict with scored setups.
        """
        self._set_status(AgentStatus.WORKING,
                         f"Scoring technical setups ({len(TECH_TICKERS)} tickers)")

        start = time.monotonic()

        try:
            # Compute technical scores
            tech_data = self.execute(
                "Computing technical indicator scores",
                score_technical_setups,
            )

            self._last_result = tech_data
            self._total_scorings += 1
            self._last_setups_count = tech_data["stats"]["setups_found"]
            self._last_avg_score = tech_data["stats"]["avg_score"]

            # Log results
            setups = tech_data["stats"]["setups_found"]
            total = tech_data["stats"]["total_tickers"]
            avg_score = tech_data["stats"]["avg_score"]

            self.log(f"Tech scoring: {setups} setups from {total} tickers", {
                "setups_found": setups,
                "avg_score": avg_score,
                "top_strategy": tech_data["stats"].get("top_strategy", ""),
                "by_strategy": {
                    k: len(v)
                    for k, v in tech_data.get("by_strategy", {}).items()
                },
            })

            # Log high-score setups
            for setup in tech_data.get("setups", [])[:5]:
                if setup["score"] >= 60:
                    self.log_decision(f"Strong setup: {setup['ticker']}", {
                        "ticker": setup["ticker"],
                        "strategy": setup["strategy_name"],
                        "direction": setup["direction"],
                        "score": setup["score"],
                        "entry_price": setup["entry_price"],
                        "target_pct": setup["target_pct"],
                        "stop_pct": setup["stop_pct"],
                    })

            # Publish on bus
            duration_ms = int((time.monotonic() - start) * 1000)
            self.publish("tech_scored", {
                "setups_found": setups,
                "avg_score": avg_score,
                "top_setups": [
                    {"ticker": s["ticker"], "strategy": s["strategy"],
                     "direction": s["direction"], "score": s["score"]}
                    for s in tech_data.get("setups", [])[:10]
                ],
                "duration_ms": duration_ms,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            self._set_status(
                AgentStatus.IDLE,
                f"{setups} setups, avg score {avg_score}"
            )

            return tech_data

        except Exception as exc:
            self.log("Tech scoring failed", {"error": str(exc)}, level="ERROR")
            self._set_status(AgentStatus.ERROR, str(exc))
            raise

    def get_last_result(self) -> dict | None:
        """Get the last scoring result (for API/frontend)."""
        return self._last_result

    def get_metrics(self) -> dict:
        return {
            "last_setups_count": self._last_setups_count,
            "last_avg_score": self._last_avg_score,
            "total_scorings": self._total_scorings,
            "tickers_tracked": len(TECH_TICKERS),
        }
