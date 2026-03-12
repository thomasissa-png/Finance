"""Agent Scoring 3 — Technical Indicators Scoring (Team 3).

Responsibilities:
- Computes technical indicator scores for trading signals
- NOT based on news — Team 3 trades purely on technical indicators
- Multi-timeframe confluence: daily (primary) + intraday (confirmation)
- Indicators: RSI (14, 21), MACD (12,26,9), Bollinger Bands (20,2),
  SMA/EMA crossovers (20/50/200), Stochastic, ADX, Volume analysis
- For each ticker in TECH_TICKERS (20 liquid assets)
- Produces scored trade setups with direction, strategy, score, targets
- Publishes "tech_scored" on bus -> Trader 3 consumes

Differences with Scoring 1/2:
- Scoring 1: evaluates news edge (transmission_delay, market_awareness)
- Scoring 2: evaluates trend relevance for commodity news
- Scoring 3: evaluates technical setups (indicators, price action)
- NO Claude API calls — pure computational analysis
- Multi-timeframe confluence scoring (daily + 1h confirmation)
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

# ── Multi-timeframe confluence ────────────────────────────────────
# Primary = daily (strategy detection), confirmation = 1h (entry timing)
# 4h not used (Twelve Data free tier limits); 15m too noisy for daily strategies
TIMEFRAMES = ["1d", "1h"]  # Primary + confirmation
TIMEFRAME_WEIGHTS = {"1d": 0.70, "1h": 0.30}  # Daily dominates

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
    "stochastic_reversal": {
        "name": "Stochastic Reversal",
        "description": "Stochastic K/D extreme zones with reversal confirmation",
        "weight": 0.9,
        "preferred_regime": "ranging",  # T3: better in ranging markets
    },
    # ── Combo strategies (v2.1) — multi-indicator confluence ──
    "rsi_macd_combo": {
        "name": "RSI + MACD",
        "description": "RSI extreme + MACD crossover/momentum confluence",
        "weight": 1.1,
    },
    "bollinger_stoch_combo": {
        "name": "Bollinger + Stochastic",
        "description": "Bollinger Band touch + Stochastic extreme confluence",
        "weight": 1.1,
        "preferred_regime": "ranging",
    },
    "ma_rsi_macd_combo": {
        "name": "MA + RSI + MACD",
        "description": "Triple confluence: MA trend + RSI + MACD agreement",
        "weight": 1.2,
    },
    "rsi_bollinger_combo": {
        "name": "RSI + Bollinger",
        "description": "RSI extreme + Bollinger Band touch — mean reversion",
        "weight": 1.1,
        "preferred_regime": "ranging",
    },
    "macd_ma_combo": {
        "name": "MACD + MA",
        "description": "MACD crossover + MA trend alignment — trend continuation",
        "weight": 1.1,
    },
}

# ── Default indicator parameters (overridable via weekly_config) ──
DEFAULT_PARAMS = {
    "rsi_oversold": 30,
    "rsi_overbought": 70,
    "rsi_periods": [14, 21],
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "bb_period": 20,
    "bb_std": 2.0,
    "bb_squeeze_threshold": 3.0,
    "sma_fast": 20,
    "sma_mid": 50,
    "sma_slow": 200,
    "stoch_k": 14,
    "stoch_d": 3,
    "adx_period": 14,
    "adx_trend_threshold": 25,
    "min_setup_score": 30.0,
}

# Module-level defaults (used by indicator computations)
RSI_PERIODS = DEFAULT_PARAMS["rsi_periods"]
RSI_OVERSOLD = DEFAULT_PARAMS["rsi_oversold"]
RSI_OVERBOUGHT = DEFAULT_PARAMS["rsi_overbought"]
MACD_FAST = DEFAULT_PARAMS["macd_fast"]
MACD_SLOW = DEFAULT_PARAMS["macd_slow"]
MACD_SIGNAL = DEFAULT_PARAMS["macd_signal"]
BB_PERIOD = DEFAULT_PARAMS["bb_period"]
BB_STD = DEFAULT_PARAMS["bb_std"]
SMA_FAST = DEFAULT_PARAMS["sma_fast"]
SMA_MID = DEFAULT_PARAMS["sma_mid"]
SMA_SLOW = DEFAULT_PARAMS["sma_slow"]
STOCH_K = DEFAULT_PARAMS["stoch_k"]
STOCH_D = DEFAULT_PARAMS["stoch_d"]
ADX_PERIOD = DEFAULT_PARAMS["adx_period"]
ADX_TREND_THRESHOLD = DEFAULT_PARAMS["adx_trend_threshold"]

# Minimum score to emit a setup
MIN_SETUP_SCORE = DEFAULT_PARAMS["min_setup_score"]

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


def _compute_rsi_series(closes: list[float], period: int = 14) -> list[float]:
    """F3: Compute full RSI series (not just last value) for divergence detection."""
    if len(closes) < period + 1:
        return []
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    rsi_values = []
    if avg_loss == 0:
        rsi_values.append(100.0)
    else:
        rs = avg_gain / avg_loss
        rsi_values.append(round(100.0 - (100.0 / (1.0 + rs)), 2))

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi_values.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_values.append(round(100.0 - (100.0 / (1.0 + rs)), 2))

    return rsi_values


def _compute_macd(closes: list[float],
                   fast: int | None = None, slow: int | None = None,
                   signal_period: int | None = None) -> dict | None:
    """MACD line, signal line, histogram (last values).

    S3-P1: Accepts optional overrides for MACD params from weekly_config.
    """
    fast = fast or MACD_FAST
    slow = slow or MACD_SLOW
    sig = signal_period or MACD_SIGNAL

    ema_fast = _compute_ema(closes, fast)
    ema_slow = _compute_ema(closes, slow)
    if not ema_fast or not ema_slow:
        return None

    # Align lengths
    diff = len(ema_fast) - len(ema_slow)
    if diff > 0:
        ema_fast = ema_fast[diff:]

    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    if len(macd_line) < sig:
        return None

    signal = _compute_ema(macd_line, sig)
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
                         closes: list[float],
                         k_period: int | None = None,
                         d_period: int | None = None) -> dict | None:
    """Stochastic Oscillator %K and %D.

    S3-P1: Accepts optional overrides for Stochastic params from weekly_config.
    """
    k_per = k_period or STOCH_K
    d_per = d_period or STOCH_D

    if len(closes) < k_per:
        return None

    k_values = []
    for i in range(k_per - 1, len(closes)):
        window_highs = highs[i - k_per + 1:i + 1]
        window_lows = lows[i - k_per + 1:i + 1]
        highest = max(window_highs)
        lowest = min(window_lows)
        if highest == lowest:
            k_values.append(50.0)
        else:
            k_values.append((closes[i] - lowest) / (highest - lowest) * 100)

    if len(k_values) < d_per:
        return None

    d_values = _compute_sma(k_values, d_per)

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

        # F8: Use DX=0 instead of skipping bars to maintain consistent array length
        if atr == 0:
            dx_values.append(0.0)
            continue
        plus_di = (plus_di_smooth / atr) * 100
        minus_di = (minus_di_smooth / atr) * 100
        di_sum = plus_di + minus_di
        if di_sum == 0:
            dx_values.append(0.0)
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
    """Average True Range — F7: uses Wilder smoothing (standard convention)."""
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

    # F7: Wilder smoothing instead of simple average
    # First ATR = simple average of first N true ranges
    atr = sum(tr_list[:period]) / period
    # Subsequent values use exponential smoothing: ATR = (prev_ATR * (n-1) + TR) / n
    for i in range(period, len(tr_list)):
        atr = (atr * (period - 1) + tr_list[i]) / period
    return round(atr, 4)


# ── Strategy detection ────────────────────────────────────────────

def _detect_rsi_reversal(indicators: dict) -> dict | None:
    """Detect RSI reversal setups."""
    rsi_14 = indicators.get("rsi_14")
    rsi_21 = indicators.get("rsi_21")
    adx = indicators.get("adx")

    if rsi_14 is None:
        return None

    # F4: Read configurable thresholds from weekly config
    params = indicators.get("_params", {})
    rsi_oversold = params.get("rsi_oversold", RSI_OVERSOLD)
    rsi_overbought = params.get("rsi_overbought", RSI_OVERBOUGHT)

    score = 0
    direction = None

    # Oversold reversal (LONG)
    if rsi_14 < rsi_oversold:
        score = 50 + (rsi_oversold - rsi_14) * 2  # Deeper oversold = higher score
        direction = "LONG"
        # Confirmation: RSI 21 also oversold
        if rsi_21 is not None and rsi_21 < rsi_oversold + 5:
            score += 15
        # ADX confirmation: ranging market better for reversal
        if adx is not None and adx < ADX_TREND_THRESHOLD:
            score += 10

    # Overbought reversal (SHORT)
    elif rsi_14 > rsi_overbought:
        score = 50 + (rsi_14 - rsi_overbought) * 2
        direction = "SHORT"
        if rsi_21 is not None and rsi_21 > rsi_overbought - 5:
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

    # F4/F9: Read configurable squeeze threshold from weekly config
    params = indicators.get("_params", {})
    base_squeeze = params.get("bb_squeeze_threshold", 3.0)
    # F9: Per-category squeeze thresholds — forex has much tighter bandwidth
    category = indicators.get("_category", "")
    CATEGORY_SQUEEZE_DEFAULTS = {
        "forex": 1.5,       # Forex bandwidth is typically 1-2%
        "indices": 2.5,     # Indices are moderately volatile
        "equities": 3.0,    # Equities standard
        "commodities": 4.0, # Commodities have wider bands
    }
    squeeze_threshold = CATEGORY_SQUEEZE_DEFAULTS.get(category, base_squeeze)

    score = 0
    direction = None

    # Squeeze: bandwidth is narrow
    # Breakout: price near or outside bands
    if bb["bandwidth"] < squeeze_threshold:
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
    """Detect moving average alignment setups (P5: now uses SMA 200)."""
    sma_20 = indicators.get("sma_20")
    sma_50 = indicators.get("sma_50")
    sma_200 = indicators.get("sma_200")
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
        # SMA 200 confirmation: full alignment (golden cross confirmed)
        if sma_200 is not None and sma_50 > sma_200:
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
        # SMA 200 confirmation: death cross confirmed
        if sma_200 is not None and sma_50 < sma_200:
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
            "sma_200": round(sma_200, 4) if sma_200 else None,
            "ema_20": round(ema_20, 4) if ema_20 else None,
            "adx": adx,
            "close": round(last_close, 4),
        },
    }


def _detect_momentum_divergence(indicators: dict) -> dict | None:
    """Detect price/RSI divergence setups.

    F3: Proper two-swing divergence detection.
    Bullish divergence: price makes lower low (swing2 < swing1), RSI makes higher low.
    Bearish divergence: price makes higher high (swing2 > swing1), RSI makes lower high.
    Requires two distinct swing points to avoid false positives.
    """
    rsi_14 = indicators.get("rsi_14")
    last_close = indicators.get("last_close")
    macd = indicators.get("macd")
    closes = indicators.get("_closes")  # F3: Need full closes array
    rsi_series = indicators.get("_rsi_series")  # F3: Need full RSI series

    if rsi_14 is None or last_close is None:
        return None

    # F3: Need closes and RSI series for two-swing detection
    if not closes or not rsi_series or len(closes) < 30 or len(rsi_series) < 20:
        # Fallback to simple single-pivot detection if series not available
        rsi_prev = indicators.get("rsi_prev")
        prev_close = indicators.get("prev_close")
        if rsi_prev is None or prev_close is None:
            return None
        # Simple version (less reliable, reduced score)
        score = 0
        direction = None
        if last_close < prev_close and rsi_14 > rsi_prev:
            direction = "LONG"
            price_drop = (prev_close - last_close) / prev_close * 100 if prev_close > 0 else 0
            rsi_rise = rsi_14 - rsi_prev
            score = 30 + price_drop * 4 + rsi_rise * 1.5  # Reduced vs two-swing
            if macd and macd["histogram"] > macd["prev_histogram"]:
                score += 10
            if rsi_14 < 40:
                score += 10
        elif last_close > prev_close and rsi_14 < rsi_prev:
            direction = "SHORT"
            price_rise = (last_close - prev_close) / prev_close * 100 if prev_close > 0 else 0
            rsi_drop = rsi_prev - rsi_14
            score = 30 + price_rise * 4 + rsi_drop * 1.5
            if macd and macd["histogram"] < macd["prev_histogram"]:
                score += 10
            if rsi_14 > 60:
                score += 10
        if direction is None or score < MIN_SETUP_SCORE:
            return None
        return {
            "strategy": "momentum_divergence",
            "direction": direction,
            "score": min(100, round(score, 1)),
            "signals": {"rsi_14": rsi_14, "method": "single_pivot"},
        }

    # F3: Two-swing divergence detection
    # Find two recent swing lows (for bullish) or swing highs (for bearish)
    score = 0
    direction = None

    # Find swing lows in price
    swing_lows = []
    for i in range(len(closes) - 3, max(0, len(closes) - 40), -1):
        if i >= 1 and closes[i] <= closes[i - 1] and closes[i] <= closes[i + 1]:
            swing_lows.append(i)
            if len(swing_lows) >= 2:
                break

    # Find swing highs in price
    swing_highs = []
    for i in range(len(closes) - 3, max(0, len(closes) - 40), -1):
        if i >= 1 and closes[i] >= closes[i - 1] and closes[i] >= closes[i + 1]:
            swing_highs.append(i)
            if len(swing_highs) >= 2:
                break

    # Bullish divergence: price lower low + RSI higher low
    if len(swing_lows) >= 2:
        recent_idx, older_idx = swing_lows[0], swing_lows[1]
        if closes[recent_idx] < closes[older_idx]:  # Lower low in price
            # Get RSI at these swing points
            rsi_offset = len(closes) - len(rsi_series)
            recent_rsi_idx = recent_idx - rsi_offset
            older_rsi_idx = older_idx - rsi_offset
            if 0 <= recent_rsi_idx < len(rsi_series) and 0 <= older_rsi_idx < len(rsi_series):
                if rsi_series[recent_rsi_idx] > rsi_series[older_rsi_idx]:  # Higher low in RSI
                    direction = "LONG"
                    price_drop = abs(closes[older_idx] - closes[recent_idx]) / closes[older_idx] * 100
                    rsi_rise = rsi_series[recent_rsi_idx] - rsi_series[older_rsi_idx]
                    score = 40 + price_drop * 5 + rsi_rise * 2
                    if macd and macd["histogram"] > macd["prev_histogram"]:
                        score += 15
                    if rsi_14 < 40:
                        score += 10

    # Bearish divergence: price higher high + RSI lower high
    if direction is None and len(swing_highs) >= 2:
        recent_idx, older_idx = swing_highs[0], swing_highs[1]
        if closes[recent_idx] > closes[older_idx]:  # Higher high in price
            rsi_offset = len(closes) - len(rsi_series)
            recent_rsi_idx = recent_idx - rsi_offset
            older_rsi_idx = older_idx - rsi_offset
            if 0 <= recent_rsi_idx < len(rsi_series) and 0 <= older_rsi_idx < len(rsi_series):
                if rsi_series[recent_rsi_idx] < rsi_series[older_rsi_idx]:  # Lower high in RSI
                    direction = "SHORT"
                    price_rise = abs(closes[recent_idx] - closes[older_idx]) / closes[older_idx] * 100
                    rsi_drop = rsi_series[older_rsi_idx] - rsi_series[recent_rsi_idx]
                    score = 40 + price_rise * 5 + rsi_drop * 2
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
            "macd_histogram": round(macd["histogram"], 4) if macd else None,
            "method": "two_swing",
        },
    }


def _detect_stochastic_reversal(indicators: dict) -> dict | None:
    """Detect Stochastic K/D reversal setups (T3: was unused, now a strategy).

    Stochastic works best in ranging markets (ADX < 25).
    """
    stoch = indicators.get("stochastic")
    adx = indicators.get("adx")
    rsi_14 = indicators.get("rsi_14")

    if stoch is None:
        return None

    k = stoch["k"]
    d = stoch["d"]
    score = 0
    direction = None

    # Oversold zone + K crosses above D → LONG
    if k < 20 and d < 25 and k > d:
        direction = "LONG"
        score = 45 + (20 - k) * 1.5  # Deeper oversold = stronger
        # RSI confirmation
        if rsi_14 is not None and rsi_14 < 40:
            score += 10
        # Ranging market = stochastic works better
        if adx is not None and adx < 25:
            score += 10

    # Overbought zone + K crosses below D → SHORT
    elif k > 80 and d > 75 and k < d:
        direction = "SHORT"
        score = 45 + (k - 80) * 1.5
        if rsi_14 is not None and rsi_14 > 60:
            score += 10
        if adx is not None and adx < 25:
            score += 10

    if direction is None or score < MIN_SETUP_SCORE:
        return None

    return {
        "strategy": "stochastic_reversal",
        "direction": direction,
        "score": min(100, round(score, 1)),
        "signals": {
            "stoch_k": k,
            "stoch_d": d,
            "rsi_14": rsi_14,
            "adx": adx,
        },
    }


# ── Combo strategy detectors (v2.1) ─────────────────────────────
# Multi-indicator combinations — require 2-3 indicators to agree
# Higher base scores because confluence = higher conviction

def _detect_rsi_macd_combo(indicators: dict) -> dict | None:
    """RSI extreme + MACD crossover confirmation.

    LONG: RSI oversold + MACD bullish crossover (or histogram turning up)
    SHORT: RSI overbought + MACD bearish crossover (or histogram turning down)
    """
    rsi_14 = indicators.get("rsi_14")
    macd = indicators.get("macd")
    adx = indicators.get("adx")

    if rsi_14 is None or macd is None:
        return None

    score = 0
    direction = None

    # Bullish: RSI oversold + MACD turning up
    if rsi_14 < 35:
        macd_bullish = (
            (macd["prev_macd"] <= macd["prev_signal"] and macd["macd"] > macd["signal"])
            or macd["histogram"] > macd["prev_histogram"]
        )
        if macd_bullish:
            direction = "LONG"
            score = 60 + (35 - rsi_14) * 1.5
            if macd["macd"] > macd["signal"]:
                score += 10  # Full crossover vs just histogram
            if adx is not None and adx < 30:
                score += 5

    # Bearish: RSI overbought + MACD turning down
    elif rsi_14 > 65:
        macd_bearish = (
            (macd["prev_macd"] >= macd["prev_signal"] and macd["macd"] < macd["signal"])
            or macd["histogram"] < macd["prev_histogram"]
        )
        if macd_bearish:
            direction = "SHORT"
            score = 60 + (rsi_14 - 65) * 1.5
            if macd["macd"] < macd["signal"]:
                score += 10
            if adx is not None and adx < 30:
                score += 5

    if direction is None or score < MIN_SETUP_SCORE:
        return None

    return {
        "strategy": "rsi_macd_combo",
        "direction": direction,
        "score": min(100, round(score, 1)),
        "signals": {
            "rsi_14": rsi_14,
            "macd_line": round(macd["macd"], 4),
            "macd_signal": round(macd["signal"], 4),
            "histogram": round(macd["histogram"], 4),
            "adx": adx,
        },
    }


def _detect_bollinger_stoch_combo(indicators: dict) -> dict | None:
    """Bollinger Band touch/breakout + Stochastic extreme confirmation.

    LONG: Price near/below lower BB + Stochastic oversold (K<25)
    SHORT: Price near/above upper BB + Stochastic overbought (K>75)
    Both indicators must agree on direction.
    """
    bb = indicators.get("bollinger")
    stoch = indicators.get("stochastic")
    adx = indicators.get("adx")

    if bb is None or stoch is None:
        return None

    k = stoch["k"]
    d = stoch["d"]
    score = 0
    direction = None

    # Bullish: price at lower Bollinger + stochastic oversold
    if bb["pct_b"] < 0.15 and k < 25:
        direction = "LONG"
        score = 55 + (0.15 - bb["pct_b"]) * 100 + (25 - k) * 0.5
        if k > d:  # K crossing above D = momentum turning
            score += 10
        if bb["bandwidth"] < 3.0:  # Squeeze adds conviction
            score += 10
        if adx is not None and adx < 25:
            score += 5

    # Bearish: price at upper Bollinger + stochastic overbought
    elif bb["pct_b"] > 0.85 and k > 75:
        direction = "SHORT"
        score = 55 + (bb["pct_b"] - 0.85) * 100 + (k - 75) * 0.5
        if k < d:  # K crossing below D
            score += 10
        if bb["bandwidth"] < 3.0:
            score += 10
        if adx is not None and adx < 25:
            score += 5

    if direction is None or score < MIN_SETUP_SCORE:
        return None

    return {
        "strategy": "bollinger_stoch_combo",
        "direction": direction,
        "score": min(100, round(score, 1)),
        "signals": {
            "pct_b": bb["pct_b"],
            "bandwidth": bb["bandwidth"],
            "stoch_k": k,
            "stoch_d": d,
            "adx": adx,
        },
    }


def _detect_ma_rsi_macd_combo(indicators: dict) -> dict | None:
    """Triple confluence: MA trend alignment + RSI confirmation + MACD confirmation.

    LONG: price > SMA20 > SMA50 + RSI > 50 + MACD > signal
    SHORT: price < SMA20 < SMA50 + RSI < 50 + MACD < signal
    All three must agree — highest conviction setup.
    """
    sma_20 = indicators.get("sma_20")
    sma_50 = indicators.get("sma_50")
    ema_20 = indicators.get("ema_20")
    rsi_14 = indicators.get("rsi_14")
    macd = indicators.get("macd")
    adx = indicators.get("adx")
    last_close = indicators.get("last_close")

    if any(v is None for v in (sma_20, sma_50, rsi_14, macd, last_close)):
        return None

    score = 0
    direction = None

    # Bullish triple confluence
    if last_close > sma_20 > sma_50 and rsi_14 > 50 and macd["macd"] > macd["signal"]:
        direction = "LONG"
        score = 65
        # Stronger RSI
        if rsi_14 > 55 and rsi_14 < 75:  # Not overbought
            score += 5
        # MACD histogram growing
        if macd["histogram"] > macd["prev_histogram"]:
            score += 10
        # EMA confirms
        if ema_20 is not None and last_close > ema_20:
            score += 5
        # ADX confirms trend
        if adx is not None and adx > ADX_TREND_THRESHOLD:
            score += 10

    # Bearish triple confluence
    elif last_close < sma_20 < sma_50 and rsi_14 < 50 and macd["macd"] < macd["signal"]:
        direction = "SHORT"
        score = 65
        if rsi_14 < 45 and rsi_14 > 25:
            score += 5
        if macd["histogram"] < macd["prev_histogram"]:
            score += 10
        if ema_20 is not None and last_close < ema_20:
            score += 5
        if adx is not None and adx > ADX_TREND_THRESHOLD:
            score += 10

    if direction is None or score < MIN_SETUP_SCORE:
        return None

    return {
        "strategy": "ma_rsi_macd_combo",
        "direction": direction,
        "score": min(100, round(score, 1)),
        "signals": {
            "sma_20": round(sma_20, 4),
            "sma_50": round(sma_50, 4),
            "rsi_14": rsi_14,
            "macd_line": round(macd["macd"], 4),
            "macd_signal": round(macd["signal"], 4),
            "histogram": round(macd["histogram"], 4),
            "adx": adx,
            "close": round(last_close, 4),
        },
    }


def _detect_rsi_bollinger_combo(indicators: dict) -> dict | None:
    """RSI extreme + Bollinger Band touch — mean reversion setup.

    LONG: RSI < 35 + price below lower BB (pct_b < 0.10)
    SHORT: RSI > 65 + price above upper BB (pct_b > 0.90)
    Strong mean-reversion signal in ranging markets.
    """
    rsi_14 = indicators.get("rsi_14")
    bb = indicators.get("bollinger")
    adx = indicators.get("adx")

    if rsi_14 is None or bb is None:
        return None

    score = 0
    direction = None

    # Bullish: RSI oversold + price at/below lower BB
    if rsi_14 < 35 and bb["pct_b"] < 0.10:
        direction = "LONG"
        score = 60 + (35 - rsi_14) + (0.10 - bb["pct_b"]) * 100
        if adx is not None and adx < 25:  # Ranging = mean reversion works
            score += 10
        if bb["bandwidth"] > 3.0:  # Wide bands = more room to revert
            score += 5

    # Bearish: RSI overbought + price at/above upper BB
    elif rsi_14 > 65 and bb["pct_b"] > 0.90:
        direction = "SHORT"
        score = 60 + (rsi_14 - 65) + (bb["pct_b"] - 0.90) * 100
        if adx is not None and adx < 25:
            score += 10
        if bb["bandwidth"] > 3.0:
            score += 5

    if direction is None or score < MIN_SETUP_SCORE:
        return None

    return {
        "strategy": "rsi_bollinger_combo",
        "direction": direction,
        "score": min(100, round(score, 1)),
        "signals": {
            "rsi_14": rsi_14,
            "pct_b": bb["pct_b"],
            "bandwidth": bb["bandwidth"],
            "adx": adx,
        },
    }


def _detect_macd_ma_combo(indicators: dict) -> dict | None:
    """MACD crossover + MA trend alignment — trend continuation setup.

    LONG: MACD bullish cross + price above SMA20 > SMA50
    SHORT: MACD bearish cross + price below SMA20 < SMA50
    Confirms trend direction with momentum.
    """
    macd = indicators.get("macd")
    sma_20 = indicators.get("sma_20")
    sma_50 = indicators.get("sma_50")
    adx = indicators.get("adx")
    last_close = indicators.get("last_close")

    if any(v is None for v in (macd, sma_20, sma_50, last_close)):
        return None

    score = 0
    direction = None

    # Precompute crossover/momentum flags for both directions
    macd_bull_cross = (macd["prev_macd"] <= macd["prev_signal"]
                       and macd["macd"] > macd["signal"])
    macd_bull_momentum = macd["histogram"] > macd["prev_histogram"] and macd["histogram"] > 0
    macd_bear_cross = (macd["prev_macd"] >= macd["prev_signal"]
                       and macd["macd"] < macd["signal"])
    macd_bear_momentum = macd["histogram"] < macd["prev_histogram"] and macd["histogram"] < 0

    # Bullish: MACD cross up + MA alignment
    if (macd_bull_cross or macd_bull_momentum) and last_close > sma_20 > sma_50:
        direction = "LONG"
        score = 58
        if macd_bull_cross:
            score += 10  # Full cross > just momentum
        if macd["histogram"] > macd["prev_histogram"]:
            score += 5
        if adx is not None and adx > ADX_TREND_THRESHOLD:
            score += 10

    # F5: elif prevents potential double-match
    elif (macd_bear_cross or macd_bear_momentum) and last_close < sma_20 < sma_50:
        direction = "SHORT"
        score = 58
        if macd_bear_cross:
            score += 10
        if macd["histogram"] < macd["prev_histogram"]:
            score += 5
        if adx is not None and adx > ADX_TREND_THRESHOLD:
            score += 10

    if direction is None or score < MIN_SETUP_SCORE:
        return None

    return {
        "strategy": "macd_ma_combo",
        "direction": direction,
        "score": min(100, round(score, 1)),
        "signals": {
            "macd_line": round(macd["macd"], 4),
            "macd_signal": round(macd["signal"], 4),
            "histogram": round(macd["histogram"], 4),
            "sma_20": round(sma_20, 4),
            "sma_50": round(sma_50, 4),
            "adx": adx,
            "close": round(last_close, 4),
        },
    }


# ── Strategy R/R profiles (v2.4) ─────────────────────────────────
# (target_atr_mult, stop_atr_mult) — determines R/R ratio per strategy family
#
# Mean-reversion strategies: tighter target (ATR×1.2), wider stop (ATR×1.2)
#   → R/R ~1.0 but higher hit rate (price reverts to mean)
# Momentum/trend strategies: wider target (ATR×2.0), standard stop (ATR×1.0)
#   → R/R ~2.0, let profits run on strong directional moves
# Combo strategies: inherit from dominant component
STRATEGY_RR_PROFILES = {
    # Mean-reversion family — target closer, stop wider (forgiving)
    "rsi_reversal":         (1.2, 1.2),   # R/R ~1.0
    "stochastic_reversal":  (1.2, 1.2),   # R/R ~1.0
    "bollinger_squeeze":    (1.4, 1.1),   # R/R ~1.27 (breakout can run)
    # Momentum/trend family — let profits run
    "macd_crossover":       (2.0, 1.0),   # R/R ~2.0
    "ma_trend":             (2.2, 1.0),   # R/R ~2.2 (strongest trend signal)
    "momentum_divergence":  (1.8, 1.0),   # R/R ~1.8
    # Combo: mean-reversion dominant
    "rsi_bollinger_combo":  (1.3, 1.2),   # R/R ~1.08
    "bollinger_stoch_combo": (1.3, 1.2),  # R/R ~1.08
    # Combo: momentum dominant
    "rsi_macd_combo":       (1.6, 1.0),   # R/R ~1.6 (mixed)
    "macd_ma_combo":        (2.0, 1.0),   # R/R ~2.0 (trend continuation)
    "ma_rsi_macd_combo":    (2.0, 1.0),   # R/R ~2.0 (triple confluence = conviction)
}

# ── Regime-strategy compatibility (J3) ──────────────────────────
# Some strategies work better in trending markets, others in ranging
STRATEGY_REGIME_PREFERENCE = {
    "rsi_reversal": "ranging",       # Reversal = mean reversion = ranging
    "macd_crossover": "trending",    # Trend-following signal
    "bollinger_squeeze": "ranging",  # Breakout from compression
    "ma_trend": "trending",          # Trend alignment
    "momentum_divergence": None,     # Works in both
    "stochastic_reversal": "ranging",  # Oscillator = ranging
    # Combo strategies
    "rsi_macd_combo": None,          # Works in both (reversal + momentum)
    "bollinger_stoch_combo": "ranging",  # Both are ranging indicators
    "ma_rsi_macd_combo": "trending", # MA alignment = trending
    "rsi_bollinger_combo": "ranging",  # Mean reversion = ranging
    "macd_ma_combo": "trending",     # Trend continuation = trending
}

# Regime mismatch penalty
REGIME_MISMATCH_PENALTY = 0.80  # -20% score if regime doesn't match


# ── Pivot detection helper (C7) ──────────────────────────────────

def _find_recent_pivot(closes: list[float], min_lookback: int = 3,
                       max_lookback: int = 20,
                       pivot_type: str = "any") -> int | None:
    """Find the index of the most recent swing pivot in closes.

    F13: Now distinguishes swing high from swing low via pivot_type parameter.
    Args:
        pivot_type: "low" for swing lows only, "high" for swing highs only, "any" for either.

    Returns the index of the most recent pivot within [min_lookback, max_lookback]
    bars from the end. Returns None if no pivot found.
    """
    if len(closes) < min_lookback + 2:
        return None

    end = len(closes) - 1
    search_start = max(1, end - max_lookback)
    search_end = end - min_lookback

    for i in range(search_end, search_start - 1, -1):
        # F13: Filter by pivot_type
        is_low = closes[i] <= closes[i - 1] and closes[i] <= closes[i + 1]
        is_high = closes[i] >= closes[i - 1] and closes[i] >= closes[i + 1]

        if pivot_type == "low" and is_low:
            return i
        elif pivot_type == "high" and is_high:
            return i
        elif pivot_type == "any" and (is_low or is_high):
            return i

    return None


# ── Multi-timeframe confluence helper ────────────────────────────

def _compute_intraday_confirmation(ticker: str, direction: str) -> float:
    """Fetch 1h data and check if intraday trend confirms the daily signal.

    Returns a confluence boost: 1.0 (no data/neutral), 1.1 (confirming), 0.9 (opposing).
    """
    try:
        ohlcv = _fetch_ohlcv(ticker, period="1mo", interval="1h")
        if not ohlcv or len(ohlcv["close"]) < 20:
            return 1.0

        closes = ohlcv["close"]
        # Check short-term trend (last 10 hourly bars)
        sma_fast = sum(closes[-5:]) / 5
        sma_slow = sum(closes[-10:]) / 10

        if direction == "LONG":
            if sma_fast > sma_slow:
                return 1.1  # Intraday confirms bullish
            elif sma_fast < sma_slow * 0.998:
                return 0.9  # Intraday opposes
        elif direction == "SHORT":
            if sma_fast < sma_slow:
                return 1.1  # Intraday confirms bearish
            elif sma_fast > sma_slow * 1.002:
                return 0.9  # Intraday opposes

        return 1.0
    except Exception as exc:
        # S3-P4: Log instead of silently swallowing
        logger.debug("Intraday confirmation failed for %s: %s", ticker, exc)
        return 1.0


# ── Main scoring function ────────────────────────────────────────

def _fetch_ohlcv(ticker: str, period: str = "3mo", interval: str = "1d") -> dict | None:
    """Fetch OHLCV data for a ticker via market_data (Twelve Data + yfinance fallback).

    Returns dict with lists of open/high/low/close/volume.
    """
    try:
        from ..market_data import fetch_history

        # Map yfinance-style period to days
        # F2: Added 1y mapping (365 days) for SMA 200 computation
        period_map = {"1mo": 30, "2mo": 60, "3mo": 90, "6mo": 180, "1y": 365}
        period_days = period_map.get(period, 90)

        # Map yfinance-style interval to Twelve Data format
        interval_map = {"1d": "1day", "1h": "1h", "4h": "4h", "15m": "15min"}
        td_interval = interval_map.get(interval, "1day")

        h = fetch_history(ticker, period_days=period_days, interval=td_interval)
        if h is None or h.empty or len(h) < 30:
            return None

        # F1: Validate no NaN/Inf in OHLCV data — a single NaN propagates
        # through ALL indicator computations producing garbage signals
        for col in ["Open", "High", "Low", "Close"]:
            if col in h.columns:
                col_data = h[col]
                nan_count = col_data.isna().sum()
                if nan_count > 0:
                    logger.warning("F1: %s has %d NaN values in %s — dropping rows",
                                   ticker, nan_count, col)
                    h = h.dropna(subset=["Open", "High", "Low", "Close"])
                    break

        if len(h) < 30:
            return None

        # F1: Check for Inf values
        for col in ["Open", "High", "Low", "Close"]:
            if col in h.columns:
                inf_mask = h[col].apply(lambda x: not math.isfinite(x))
                if inf_mask.any():
                    logger.warning("F1: %s has Inf values in %s — dropping rows", ticker, col)
                    h = h[~inf_mask]

        if len(h) < 30:
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


def _compute_all_indicators(ohlcv: dict, params: dict | None = None) -> dict:
    """Compute all technical indicators from OHLCV data.

    S3-P1: Accepts optional params dict to pass configurable values
    (macd_fast, macd_slow, macd_signal, stoch_k, stoch_d, adx_period)
    from weekly_config. Falls back to module-level defaults.
    """
    p = params or {}
    closes = ohlcv["close"]
    highs = ohlcv["high"]
    lows = ohlcv["low"]

    indicators = {
        "last_close": closes[-1] if closes else None,
    }

    # RSI
    indicators["rsi_14"] = _compute_rsi(closes, 14)
    indicators["rsi_21"] = _compute_rsi(closes, 21)
    # Prev RSI for divergence (C7: use pivot-based lookback, not fixed 5 bars)
    # S3-P3: Use proper pivot_type — "low" for bullish, "high" for bearish
    # We need both for the divergence detector, so fetch both
    rsi_14 = indicators["rsi_14"]
    pivot_type = "any"
    if rsi_14 is not None:
        if rsi_14 < 50:
            pivot_type = "low"   # Bullish divergence: compare swing lows
        else:
            pivot_type = "high"  # Bearish divergence: compare swing highs
    pivot_idx = _find_recent_pivot(closes, pivot_type=pivot_type)
    if pivot_idx is not None and pivot_idx > 0:
        indicators["rsi_prev"] = _compute_rsi(closes[:pivot_idx + 1], 14)
        indicators["prev_close"] = closes[pivot_idx]
        indicators["pivot_lookback"] = len(closes) - 1 - pivot_idx
    elif len(closes) > 5:
        indicators["rsi_prev"] = _compute_rsi(closes[:-5], 14)
        indicators["prev_close"] = closes[-5] if len(closes) >= 5 else (
            closes[-2] if len(closes) >= 2 else None)
        indicators["pivot_lookback"] = 5

    # MACD (S3-P1: configurable params)
    indicators["macd"] = _compute_macd(
        closes,
        fast=p.get("macd_fast"),
        slow=p.get("macd_slow"),
        signal_period=p.get("macd_signal"),
    )

    # Bollinger Bands
    indicators["bollinger"] = _compute_bollinger(closes)

    # Moving Averages (P5: SMA 200 now computed)
    sma_fast = p.get("sma_fast", SMA_FAST)
    sma_mid = p.get("sma_mid", SMA_MID)
    sma_slow = p.get("sma_slow", SMA_SLOW)
    sma_20 = _compute_sma(closes, sma_fast)
    sma_50 = _compute_sma(closes, sma_mid)
    sma_200 = _compute_sma(closes, sma_slow)
    ema_20 = _compute_ema(closes, sma_fast)
    indicators["sma_20"] = sma_20[-1] if sma_20 else None
    indicators["sma_50"] = sma_50[-1] if sma_50 else None
    indicators["sma_200"] = sma_200[-1] if sma_200 else None
    indicators["ema_20"] = ema_20[-1] if ema_20 else None

    # Stochastic (S3-P1: configurable params)
    indicators["stochastic"] = _compute_stochastic(
        highs, lows, closes,
        k_period=p.get("stoch_k"),
        d_period=p.get("stoch_d"),
    )

    # ADX (S3-P1: configurable period)
    indicators["adx"] = _compute_adx(
        highs, lows, closes,
        period=p.get("adx_period", ADX_PERIOD),
    )

    # ATR
    indicators["atr"] = _compute_atr(highs, lows, closes)

    # Volume analysis
    volumes = ohlcv.get("volume", [])
    if volumes and len(volumes) >= 20:
        avg_vol = sum(volumes[-20:]) / 20
        indicators["volume_ratio"] = round(volumes[-1] / avg_vol, 2) if avg_vol > 0 else 1.0
    else:
        indicators["volume_ratio"] = 1.0

    # F3: Store raw series for two-swing divergence detection
    indicators["_closes"] = closes
    indicators["_rsi_series"] = _compute_rsi_series(closes, 14)

    return indicators


def score_technical_setups(tickers: dict | None = None,
                           weekly_config: dict | None = None) -> dict:
    """Compute technical scores for all tickers.

    Args:
        tickers: Override ticker universe (default: TECH_TICKERS)
        weekly_config: Weekly strategy config from Learning 3 with:
            - enabled_strategies: list[str] — only run these strategies
            - params: dict — override indicator parameters
            - strategy_weights: dict[str, float] — override strategy weights

    Returns dict with:
        - setups: list[dict] — all detected setups sorted by score desc
        - by_ticker: dict[ticker, list[dict]] — grouped by ticker
        - by_strategy: dict[strategy, list[dict]] — grouped by strategy
        - stats: {total_tickers, setups_found, avg_score, top_strategy}
    """
    target_tickers = tickers or TECH_TICKERS
    config = weekly_config or {}

    # Apply parameter overrides from weekly config
    params = {**DEFAULT_PARAMS, **(config.get("params", {}))}
    enabled = set(config.get("enabled_strategies", list(STRATEGIES.keys())))
    strategy_weight_overrides = config.get("strategy_weights", {})

    result = {
        "setups": [],
        "by_ticker": {},
        "by_strategy": {},
        "weekly_config_applied": bool(weekly_config),
        "stats": {
            "total_tickers": len(target_tickers),
            "setups_found": 0,
            "avg_score": 0.0,
            "top_strategy": "",
            "enabled_strategies": list(enabled),
        },
    }

    # Build detector list based on enabled strategies
    detector_map = {
        "rsi_reversal": _detect_rsi_reversal,
        "macd_crossover": _detect_macd_crossover,
        "bollinger_squeeze": _detect_bollinger_squeeze,
        "ma_trend": _detect_ma_trend,
        "momentum_divergence": _detect_momentum_divergence,
        "stochastic_reversal": _detect_stochastic_reversal,
        # Combo strategies (v2.1)
        "rsi_macd_combo": _detect_rsi_macd_combo,
        "bollinger_stoch_combo": _detect_bollinger_stoch_combo,
        "ma_rsi_macd_combo": _detect_ma_rsi_macd_combo,
        "rsi_bollinger_combo": _detect_rsi_bollinger_combo,
        "macd_ma_combo": _detect_macd_ma_combo,
    }
    strategy_detectors = [
        (name, fn) for name, fn in detector_map.items()
        if name in enabled
    ]

    # F6: Use fetch_history_batch for efficient parallel fetching (fewer API calls)
    ticker_list = list(target_tickers.keys())
    ohlcv_data: dict[str, dict | None] = {}
    try:
        from ..market_data import fetch_history_batch
        batch_data = fetch_history_batch(ticker_list, period_days=365, interval="1day")
        for ticker in ticker_list:
            df = batch_data.get(ticker)
            if df is not None and not df.empty and len(df) >= 30:
                # F1: Validate no NaN/Inf
                for col in ["Open", "High", "Low", "Close"]:
                    if col in df.columns and df[col].isna().any():
                        df = df.dropna(subset=["Open", "High", "Low", "Close"])
                        break
                for col in ["Open", "High", "Low", "Close"]:
                    if col in df.columns:
                        inf_mask = df[col].apply(lambda x: not math.isfinite(x))
                        if inf_mask.any():
                            df = df[~inf_mask]
                if len(df) >= 30:
                    ohlcv_data[ticker] = {
                        "open": list(df["Open"]),
                        "high": list(df["High"]),
                        "low": list(df["Low"]),
                        "close": list(df["Close"]),
                        "volume": list(df["Volume"]) if "Volume" in df.columns else [],
                    }
                else:
                    ohlcv_data[ticker] = None
            else:
                ohlcv_data[ticker] = None
    except Exception as exc:
        logger.warning("F6: fetch_history_batch failed, falling back to per-ticker: %s", exc)
        for t in ticker_list:
            if t not in ohlcv_data:
                ohlcv_data[t] = _fetch_ohlcv(t, "1y", "1d")

    all_setups = []

    for ticker, info in target_tickers.items():
        ohlcv = ohlcv_data.get(ticker)
        if not ohlcv:
            continue

        indicators = _compute_all_indicators(ohlcv, params=params)
        # Inject configurable thresholds into indicators for detectors
        indicators["_params"] = params
        # F9: Inject ticker category for per-category squeeze thresholds
        indicators["_category"] = info.get("category", "")
        atr = indicators.get("atr")
        last_close = indicators.get("last_close")

        if not last_close or last_close <= 0:
            continue

        # Determine market regime from ADX
        adx = indicators.get("adx")
        regime = "trending" if (adx is not None and adx >= params["adx_trend_threshold"]) else "ranging"

        # Run enabled strategy detectors
        for strategy_name, detector in strategy_detectors:
            try:
                setup = detector(indicators)
            except Exception as exc:
                logger.warning("Strategy detector failed for %s: %s", ticker, exc)
                continue

            if setup is None:
                continue

            strategy_weight = strategy_weight_overrides.get(
                strategy_name,
                STRATEGIES.get(strategy_name, {}).get("weight", 1.0)
            )

            # Volume boost: high volume confirms setup
            vol_ratio = indicators.get("volume_ratio", 1.0)
            volume_boost = 1.0
            if vol_ratio > 2.0:
                volume_boost = 1.25
            elif vol_ratio > 1.5:
                volume_boost = 1.15

            # J3: Regime filter — penalize strategies in wrong regime
            preferred_regime = STRATEGY_REGIME_PREFERENCE.get(strategy_name)
            regime_mult = 1.0
            if preferred_regime and preferred_regime != regime:
                regime_mult = REGIME_MISMATCH_PENALTY

            # Compute final score with strategy weight, volume, and regime
            final_score = min(100, setup["score"] * strategy_weight * volume_boost * regime_mult)

            # Compute target and stop from ATR — per strategy family
            atr_mult = info.get("atr_mult", 1.0)
            target_pct = 0.0
            stop_pct = 0.0
            if atr and last_close > 0:
                atr_pct = atr / last_close * 100
                # v2.4: Strategy-family R/R profiles
                # Mean-reversion: tighter target (more likely to hit), wider stop
                # Momentum/trend: let profits run, standard stop
                target_mult, stop_mult = STRATEGY_RR_PROFILES.get(
                    strategy_name, (1.5, 1.0)
                )
                target_pct = round(atr_pct * target_mult * atr_mult, 2)
                stop_pct = round(atr_pct * stop_mult * atr_mult, 2)

            # P4/F10: Confidence = signal quality, not just score * 0.9
            # F10: Normalize signal count by strategy type to avoid combo bias
            signal_count = sum(1 for v in setup.get("signals", {}).values()
                               if v is not None)
            # F10: Cap effective signal count at 4 to normalize combos vs singles
            # Combos naturally have more signals but that doesn't mean higher quality
            effective_signal_count = min(signal_count, 4)
            regime_conf = 1.0 if (not preferred_regime or preferred_regime == regime) else 0.8
            vol_conf = min(1.2, vol_ratio / 1.5) if vol_ratio > 1.0 else 0.8
            confidence = min(100, int(
                (effective_signal_count / 4 * 40) +  # Signal richness (max 40)
                (regime_conf * 30) +                   # Regime match (max 30)
                (vol_conf * 30)                        # Volume confirmation (max 30)
            ))

            entry = {
                "ticker": ticker,
                "name": info["name"],
                "category": info["category"],
                "strategy": strategy_name,
                "strategy_name": STRATEGIES[strategy_name]["name"],
                "direction": setup["direction"],
                "score": round(final_score, 1),
                "confidence": confidence,
                "entry_price": round(last_close, 4),
                "target_pct": target_pct,
                "stop_pct": stop_pct,
                "atr": atr,
                "volume_ratio": vol_ratio,
                "adx": adx,
                "regime": regime,
                "regime_match": (not preferred_regime or preferred_regime == regime),
                "signals": setup.get("signals", {}),
                "timeframe": "1d",  # Primary timeframe used
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            all_setups.append(entry)

    # Sort by score descending
    all_setups.sort(key=lambda s: s["score"], reverse=True)

    # S3-P2: Parallelize intraday confirmation for top setups
    # (was sequential — up to 150s for 10 tickers, now ~15s)
    from concurrent.futures import ThreadPoolExecutor, as_completed
    eligible_setups = [s for s in all_setups[:10]
                       if s["score"] >= params.get("min_setup_score", MIN_SETUP_SCORE)]
    if eligible_setups:
        mtf_results: dict[str, float] = {}
        try:
            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = {
                    executor.submit(_compute_intraday_confirmation,
                                    s["ticker"], s["direction"]): s["ticker"]
                    for s in eligible_setups
                }
                for future in as_completed(futures, timeout=30):
                    ticker = futures[future]
                    try:
                        mtf_results[ticker] = future.result()
                    except Exception:
                        mtf_results[ticker] = 1.0
        except Exception:
            pass
        for setup in eligible_setups:
            mtf_boost = mtf_results.get(setup["ticker"], 1.0)
            setup["score"] = round(min(100, setup["score"] * mtf_boost), 1)
            setup["mtf_confirmation"] = mtf_boost

    # Re-sort after MTF adjustment
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

    # Build "signals" aggregation for Scoring 4 consumption.
    # Scoring 4 expects: {ticker: {score, direction, details}}
    # We pick the best setup per ticker (highest score).
    signals: dict[str, dict] = {}
    for setup in all_setups:
        t = setup["ticker"]
        if t not in signals or setup["score"] > signals[t]["score"]:
            signals[t] = {
                "score": setup["score"],
                "direction": setup["direction"],
                "details": {
                    "strategy": setup["strategy"],
                    "confidence": setup.get("confidence", 0),
                    "regime": setup.get("regime", ""),
                    "volume_ratio": setup.get("volume_ratio", 1.0),
                    "target_pct": setup.get("target_pct", 0),
                    "stop_pct": setup.get("stop_pct", 0),
                },
            }
    result["signals"] = signals

    return result


class AgentScoring3(BaseAgent):
    """Agent Scoring 3 — Technical Indicators Scoring for Team 3.

    Computes technical indicator scores across 20 liquid assets.
    Purely computational — no Claude API calls.
    """

    name = "scoring_3"
    description = "Technical indicators scoring — multi-strategy, multi-timeframe"
    version = "2.4"  # v2.4: Strategy-family R/R profiles (mean-reversion vs momentum)

    def __init__(self):
        super().__init__()
        self._last_setups_count: int = 0
        self._last_avg_score: float = 0.0
        self._total_scorings: int = 0
        self._last_result: dict | None = None

    def run(self, weekly_config=None, **kwargs) -> dict:
        """Score all tickers for technical setups.

        Args:
            weekly_config: Optional weekly strategy config from Learning 3

        Returns dict with scored setups.
        """
        self._set_status(AgentStatus.WORKING,
                         f"Scoring technical setups ({len(TECH_TICKERS)} tickers)")

        start = time.monotonic()

        try:
            # Compute technical scores with optional weekly config
            tech_data = self.execute(
                "Computing technical indicator scores",
                score_technical_setups,
                None,  # tickers (use default)
                weekly_config,
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
