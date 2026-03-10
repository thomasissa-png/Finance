"""Unified market data: Twelve Data (primary) + yfinance (fallback).

Twelve Data provides faster, more reliable market data for stocks, indices,
forex, and commodities. Falls back to yfinance when:
- TWELVE_DATA_API_KEY is not set or empty
- Ticker is not supported on Twelve Data (e.g., ZQ=F Fed Funds futures)
- Rate limit reached (8 req/min free tier)
- API error or timeout

Caching and rate limiting are built in to stay within free tier limits
(800 credits/day, 8 requests/minute).
"""

import logging
import os
import threading
import time
from collections import deque
from datetime import date, timedelta
from typing import Any

import pandas as pd
import requests as http_req

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────
TWELVE_DATA_API_KEY = os.environ.get("TWELVE_DATA_API_KEY", "")
TD_BASE = "https://api.twelvedata.com"
TD_TIMEOUT = 15  # seconds per request

# ── Rate limiter (8 req/min free tier, use 7 for safety) ──────────
_rate_lock = threading.Lock()
_request_times: deque = deque()
_TD_RPM = 7

# ── TTL Cache ─────────────────────────────────────────────────────
_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()
CACHE_TTL_SHORT = 120    # 2 min — quotes, 2d history
CACHE_TTL_MEDIUM = 600   # 10 min — daily history (25d)
CACHE_TTL_LONG = 1800    # 30 min — intraday bars (journal uses once/day)


_CACHE_PURGE_INTERVAL = 300  # 5 minutes
_last_purge: float = 0.0


def _maybe_purge_cache() -> None:
    """v5.0 N2: Periodically purge expired cache entries to prevent memory leak."""
    global _last_purge
    now = time.monotonic()
    if now - _last_purge < _CACHE_PURGE_INTERVAL:
        return
    _last_purge = now
    with _cache_lock:
        stale = [k for k, (expiry, _) in _cache.items() if time.monotonic() > expiry]
        for k in stale:
            del _cache[k]
        if stale:
            logger.debug("Cache purge: removed %d expired entries, %d remaining", len(stale), len(_cache))


def td_available() -> bool:
    """Check if Twelve Data API key is configured and non-empty."""
    return bool(TWELVE_DATA_API_KEY)


# ── Ticker mapping: yfinance format → (td_symbol, extra_params) ──
# Verified against TD /indices, /forex_pairs, /commodities endpoints (2026-03).
_TICKER_MAP: dict[str, tuple[str, dict]] = {
    # Indices — ^FCHI/^GDAXI REMOVED: TD returns ETF prices (~46/44) not index
    # (CAC=8000+, DAX=24000+). ^FTSE/^N225 also removed (same risk).
    # All EU/JP indices use yfinance fallback (reliable).
    # Note: ^GSPC, ^DJI, ^IXIC, ^RUT, ^VIX are NOT on TD — blacklisted below
    # Forex — all verified correct via /forex_pairs endpoint
    "EURUSD=X": ("EUR/USD", {}),
    "USDJPY=X": ("USD/JPY", {}),
    "GBPUSD=X": ("GBP/USD", {}),
    "USDCHF=X": ("USD/CHF", {}),
    "EURJPY=X": ("EUR/JPY", {}),
    "AUDUSD=X": ("AUD/USD", {}),
    # v5.0 N7: Added v3.5 tickers
    "USDCNH=X": ("USD/CNH", {}),   # Yuan offshore
    # Paris stocks (Euronext) — use mic_code=XPAR
    "TTE.PA": ("TTE", {"mic_code": "XPAR"}),
    "MC.PA": ("MC", {"mic_code": "XPAR"}),
    "BNP.PA": ("BNP", {"mic_code": "XPAR"}),
    "SAN.PA": ("SAN", {"mic_code": "XPAR"}),
    "AI.PA": ("AI", {"mic_code": "XPAR"}),
    "OR.PA": ("OR", {"mic_code": "XPAR"}),
    "RMS.PA": ("RMS", {"mic_code": "XPAR"}),
    # Commodities / Futures — verified via /commodities endpoint
    # Energy
    "CL=F": ("CL1", {}),        # WTI Crude (front month)
    "BZ=F": ("CO1", {}),        # Brent Crude (front month)
    "NG=F": ("NG/USD", {}),     # Natural Gas
    # Precious metals — TD uses forex-style symbols
    "GC=F": ("XAU/USD", {}),    # Gold
    "SI=F": ("XAG/USD", {}),    # Silver
    "PL=F": ("XPT/USD", {}),    # Platinum
    "PA=F": ("XPD/USD", {}),    # Palladium
    # Base metals — HG=F REMOVED: TD "HG1" = Homag Group AG (stock), not copper futures
    # Agriculture — verified 2026-03 with API key:
    #   W_1/C_1/S_1/CT1 = real futures (correct prices, <5% vs yfinance)
    #   CC1 = Amundi MSCI China Tech ETF (287€, not cocoa 3425$)
    #   KC1 = unknown stock (0.01, not coffee 296)
    #   SB1 = Smartbroker Holding AG (12€, not sugar 14$)
    #   JO1 = John B. Sanfilippo & Son (64$, not OJ 189$)
    #   LC1 = The Marzetti Company (140$, not live cattle 232$)
    #   LH1 = Lifetime Brands Inc. (3.38$, not lean hogs 96$)
    "ZW=F": ("W_1", {}),        # Wheat Futures (verified real)
    "ZC=F": ("C_1", {}),        # Corn Futures (verified real)
    "ZS=F": ("S_1", {}),        # Soybeans Futures (verified real)
    "CT=F": ("CT1", {}),        # Cotton Futures (verified real)
    # ETFs — standard US equity symbols, work as-is on TD
    "SPY": ("SPY", {}), "QQQ": ("QQQ", {}),
    "USO": ("USO", {}), "GLD": ("GLD", {}),
    "SLV": ("SLV", {}), "CORN": ("CORN", {}),
    "WEAT": ("WEAT", {}),
    # v5.0 N7: Uranium ETF (v3.5)
    "URA": ("URA", {}),
}

# Tickers known to not work on Twelve Data — skip to yfinance directly.
# Verified 2026-03 with actual API key: TD resolves these symbols as stocks/ETFs,
# not the expected futures contracts.
_td_blacklist: set[str] = {
    # Indices — not available on TD free tier, or resolve to ETFs
    "ZQ=F", "^GSPC", "^DJI", "^IXIC", "^RUT", "^VIX",
    "^FCHI", "^GDAXI",  # 404 on TD
    "^FTSE",             # TD returns ETF (~14$) not index (~10400)
    "^N225",             # 404 on TD
    # Agriculture — TD symbol resolves to stock/ETF, not futures
    "CC=F",   # CC1 = Amundi China Tech ETF (287€), not cocoa (3425$)
    "KC=F",   # KC1 = unknown stock (0.01$), not coffee (296$)
    "SB=F",   # SB1 = Smartbroker Holding AG (12€), not sugar (14$)
    "OJ=F",   # JO1 = John B. Sanfilippo & Son (64$), not OJ (189$)
    # Base metals — TD resolves to German stock
    "HG=F",   # HG1 = Homag Group AG (25€), not copper (5.93$)
    # Livestock — TD resolves to US stocks
    "LE=F",   # LC1 = The Marzetti Company (140$), not cattle (232$)
    "HE=F",   # LH1 = Lifetime Brands Inc. (3.38$), not lean hogs (96$)
}
_blacklist_lock = threading.Lock()


def _map_ticker(yf_ticker: str) -> tuple[str, dict] | None:
    """Map yfinance ticker to Twelve Data (symbol, params). None if blacklisted."""
    with _blacklist_lock:
        if yf_ticker in _td_blacklist:
            return None
    if yf_ticker in _TICKER_MAP:
        return _TICKER_MAP[yf_ticker]
    # Dynamic mapping for unlisted Paris stocks
    if yf_ticker.endswith(".PA"):
        return (yf_ticker.replace(".PA", ""), {"mic_code": "XPAR"})
    # Futures not in _TICKER_MAP: don't guess — fall back to yfinance
    if yf_ticker.endswith("=F") or ".CBT" in yf_ticker:
        return None
    return (yf_ticker, {})


def _blacklist_ticker(yf_ticker: str):
    """Add ticker to blacklist after failure (unknown symbol on TD)."""
    with _blacklist_lock:
        _td_blacklist.add(yf_ticker)
    logger.info("TD blacklist: added %s (will use yfinance)", yf_ticker)


# ── Rate limiter ──────────────────────────────────────────────────

def _try_rate_limit() -> bool:
    """Try to acquire a rate limit slot. Returns True if allowed, False otherwise."""
    with _rate_lock:
        now = time.monotonic()
        # Purge timestamps older than 60s
        while _request_times and now - _request_times[0] > 60:
            _request_times.popleft()
        if len(_request_times) >= _TD_RPM:
            return False  # Would exceed rate limit — caller should use fallback
        _request_times.append(now)
        return True


# ── Cache ─────────────────────────────────────────────────────────

def _cache_get(key: str) -> Any | None:
    """Get from cache if not expired."""
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        expiry, val = entry
        if time.monotonic() > expiry:
            del _cache[key]
            return None
        return val


def _cache_set(key: str, value: Any, ttl: float):
    """Set cache entry with TTL."""
    with _cache_lock:
        _cache[key] = (time.monotonic() + ttl, value)


# Sentinel to distinguish "cached None/empty" from "not in cache"
_CACHE_MISS = object()


def _cache_get_or_miss(key: str) -> Any:
    """Like _cache_get but returns _CACHE_MISS sentinel instead of None."""
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return _CACHE_MISS
        expiry, val = entry
        if time.monotonic() > expiry:
            del _cache[key]
            return _CACHE_MISS
        return val


# ── Twelve Data API ───────────────────────────────────────────────

def _td_request(endpoint: str, params: dict, yf_ticker: str | None = None) -> dict | None:
    """Make a rate-limited request to Twelve Data API.

    Returns parsed JSON or None on failure (rate limit, network, API error).
    v5.0 N8: Pass yf_ticker to auto-blacklist on 'not found' errors.
    """
    if not td_available():
        return None
    if not _try_rate_limit():
        logger.debug("TD rate limited, falling back to yfinance")
        return None

    params["apikey"] = TWELVE_DATA_API_KEY
    try:
        resp = http_req.get(
            f"{TD_BASE}/{endpoint}",
            params=params,
            timeout=TD_TIMEOUT,
        )
        if resp.status_code == 429:
            logger.warning("Twelve Data 429 rate limit hit")
            return None
        if resp.status_code != 200:
            logger.debug("TD %s HTTP %d: %s", endpoint, resp.status_code, resp.text[:200])
            return None
        data = resp.json()
        if data.get("status") == "error":
            msg = data.get("message", "")
            # v5.0 N8: Blacklist unknown symbols to avoid repeated failures
            if "not found" in msg.lower() or "not available" in msg.lower():
                if yf_ticker:
                    _blacklist_ticker(yf_ticker)
                logger.info("TD symbol not found (blacklisted=%s): %s", yf_ticker or "?", msg[:120])
                return None
            logger.debug("TD %s API error: %s", endpoint, msg[:200])
            return None
        return data
    except Exception as exc:
        logger.debug("TD %s request failed: %s", endpoint, exc)
        return None


def _td_values_to_dataframe(values: list[dict], tz: str | None = None) -> pd.DataFrame:
    """Convert Twelve Data 'values' array to yfinance-compatible DataFrame.

    TD returns newest-first; we reverse to oldest-first (yfinance convention).
    Columns: Open, High, Low, Close, Volume (capitalized, matching yfinance).
    """
    if not values:
        return pd.DataFrame()

    rows = []
    for v in reversed(values):  # oldest-first
        rows.append({
            "Open": float(v["open"]),
            "High": float(v["high"]),
            "Low": float(v["low"]),
            "Close": float(v["close"]),
            "Volume": int(v.get("volume", 0) or 0),
        })

    datetimes = [v["datetime"] for v in reversed(values)]
    try:
        idx = pd.to_datetime(datetimes)
    except Exception:
        idx = pd.to_datetime(datetimes, format="mixed")

    if tz:
        try:
            idx = idx.tz_localize(tz)
        except TypeError:
            # Already tz-aware
            try:
                idx = idx.tz_convert(tz)
            except Exception:
                pass
        except Exception:
            pass

    df = pd.DataFrame(rows, index=idx)
    df.index.name = "Date"
    return df


# ── yfinance fallback helpers ─────────────────────────────────────

def _yf_history(ticker: str, period: str = "25d", interval: str = "1d") -> pd.DataFrame | None:
    """Fetch history via yfinance (fallback)."""
    try:
        import yfinance as yf
        df = yf.Ticker(ticker).history(period=period, interval=interval)
        if df is not None and not df.empty:
            return df
    except Exception as exc:
        logger.debug("yfinance fallback failed for %s: %s", ticker, exc)
    return None


def _yf_history_range(
    ticker: str, start: str, end: str, interval: str = "1d",
) -> pd.DataFrame | None:
    """Fetch date-range history via yfinance (fallback)."""
    try:
        import yfinance as yf
        df = yf.Ticker(ticker).history(start=start, end=end, interval=interval)
        if df is not None and not df.empty:
            return df
    except Exception as exc:
        logger.debug("yfinance range fallback failed for %s: %s", ticker, exc)
    return None


# ── Interval mapping ──────────────────────────────────────────────

def _td_interval_to_yf(td_interval: str) -> str:
    """Convert Twelve Data interval string to yfinance format."""
    mapping = {
        "1day": "1d", "1week": "1wk", "1month": "1mo",
        "1h": "1h", "4h": "1h",  # yf doesn't have 4h, use 1h
        "30min": "30m", "15min": "15m", "5min": "5m", "1min": "1m",
    }
    return mapping.get(td_interval, "1d")


# ══════════════════════════════════════════════════════════════════
#  PUBLIC API
# ══════════════════════════════════════════════════════════════════

def fetch_history(
    ticker: str,
    period_days: int = 25,
    interval: str = "1day",
) -> pd.DataFrame | None:
    """Fetch OHLCV history. Returns yfinance-compatible DataFrame or None.

    Primary: Twelve Data. Fallback: yfinance.

    Args:
        ticker: yfinance-format ticker (e.g., "^GSPC", "CL=F", "MC.PA")
        period_days: Number of data points to fetch
        interval: TD interval format — "1day", "1h", "4h", etc.
    """
    _maybe_purge_cache()
    cache_key = f"hist:{ticker}:{period_days}:{interval}"
    cached = _cache_get_or_miss(cache_key)
    if cached is not _CACHE_MISS:
        return cached

    df = None
    ttl = CACHE_TTL_SHORT if period_days <= 5 else CACHE_TTL_MEDIUM

    # Try Twelve Data
    mapping = _map_ticker(ticker)
    if mapping and td_available():
        td_sym, extra_params = mapping
        params = {
            "symbol": td_sym,
            "interval": interval,
            "outputsize": period_days,
            **extra_params,
        }
        data = _td_request("time_series", params, yf_ticker=ticker)
        if data and "values" in data:
            tz = data.get("meta", {}).get("exchange_timezone")
            df = _td_values_to_dataframe(data["values"], tz)
            if not df.empty:
                logger.debug("TD fetch OK: %s (%s) → %d bars", ticker, td_sym, len(df))
                _cache_set(cache_key, df, ttl)
                return df
        # If TD returned error about unknown symbol, blacklist it
        if data is not None and data.get("status") == "error":
            msg = data.get("message", "")
            if "not found" in msg.lower():
                _blacklist_ticker(ticker)

    # Fallback to yfinance
    yf_period = f"{period_days}d" if period_days <= 59 else f"{(period_days // 30) + 1}mo"
    yf_interval = _td_interval_to_yf(interval)
    df = _yf_history(ticker, period=yf_period, interval=yf_interval)
    if df is not None:
        logger.debug("yfinance fetch OK: %s → %d bars (TD unavailable)", ticker, len(df))
    _cache_set(cache_key, df, ttl)
    return df


def fetch_history_range(
    ticker: str,
    start: date | str,
    end: date | str,
    interval: str = "1h",
) -> pd.DataFrame | None:
    """Fetch OHLCV for a date range. Returns yfinance-compatible DataFrame.

    Primary: Twelve Data. Fallback: yfinance.
    Used by journal.py for intraday 1h bars and backtest.py for date ranges.
    """
    start_str = str(start)
    end_str = str(end)
    cache_key = f"range:{ticker}:{start_str}:{end_str}:{interval}"
    cached = _cache_get_or_miss(cache_key)
    if cached is not _CACHE_MISS:
        return cached

    df = None

    # Try Twelve Data
    mapping = _map_ticker(ticker)
    if mapping and td_available():
        td_sym, extra_params = mapping
        params = {
            "symbol": td_sym,
            "interval": interval,
            "start_date": start_str,
            "end_date": end_str,
            **extra_params,
        }
        data = _td_request("time_series", params, yf_ticker=ticker)
        if data and "values" in data:
            tz = data.get("meta", {}).get("exchange_timezone")
            df = _td_values_to_dataframe(data["values"], tz)
            if not df.empty:
                logger.debug("TD range OK: %s (%s) → %d bars", ticker, td_sym, len(df))
                _cache_set(cache_key, df, CACHE_TTL_LONG)
                return df

    # Fallback to yfinance
    yf_interval = _td_interval_to_yf(interval)
    df = _yf_history_range(ticker, start=start_str, end=end_str, interval=yf_interval)
    if df is not None:
        logger.debug("yfinance range OK: %s → %d bars", ticker, len(df))
    _cache_set(cache_key, df, CACHE_TTL_LONG)
    return df


def fetch_history_batch(
    tickers: list[str],
    period_days: int = 2,
    interval: str = "1day",
) -> dict[str, pd.DataFrame]:
    """Fetch history for multiple tickers efficiently.

    Uses Twelve Data batch capability (multiple symbols in 1 HTTP request)
    to minimize rate limit consumption. Falls back to yfinance per-ticker.

    Returns dict mapping original yfinance ticker → DataFrame.
    """
    result: dict[str, pd.DataFrame] = {}

    # Check cache first
    uncached = []
    for t in tickers:
        cache_key = f"hist:{t}:{period_days}:{interval}"
        cached = _cache_get_or_miss(cache_key)
        if cached is not _CACHE_MISS:
            if cached is not None:
                result[t] = cached
        else:
            uncached.append(t)

    if not uncached:
        return result

    # Group tickers by extra_params for batch compatibility
    # (can't mix mic_code=XPAR and no mic_code in a single batch)
    td_groups: dict[str, list[tuple[str, str]]] = {}  # param_key → [(yf_ticker, td_sym)]
    yf_only: list[str] = []

    for t in uncached:
        mapping = _map_ticker(t)
        if mapping and td_available():
            td_sym, extra = mapping
            param_key = str(sorted(extra.items()))
            td_groups.setdefault(param_key, []).append((t, td_sym))
        else:
            yf_only.append(t)

    # Batch request per param group
    for param_key, ticker_pairs in td_groups.items():
        # Reconstruct extra params from first ticker in group
        first_yf = ticker_pairs[0][0]
        mapping = _map_ticker(first_yf)
        extra = mapping[1] if mapping else {}

        td_syms = [td_sym for _, td_sym in ticker_pairs]
        td_to_yf = {td_sym: yf_t for yf_t, td_sym in ticker_pairs}

        params = {
            "symbol": ",".join(td_syms),
            "interval": interval,
            "outputsize": period_days,
            **extra,
        }
        data = _td_request("time_series", params)

        if data:
            if len(td_syms) == 1:
                # Single-symbol response (no nesting)
                td_sym = td_syms[0]
                yf_t = td_to_yf[td_sym]
                if "values" in data:
                    tz = data.get("meta", {}).get("exchange_timezone")
                    df = _td_values_to_dataframe(data["values"], tz)
                    if not df.empty:
                        result[yf_t] = df
                        _cache_set(f"hist:{yf_t}:{period_days}:{interval}", df, CACHE_TTL_SHORT)
                    else:
                        yf_only.append(yf_t)
                else:
                    yf_only.append(yf_t)
            else:
                # Multi-symbol batch response (nested per symbol)
                for td_sym in td_syms:
                    yf_t = td_to_yf[td_sym]
                    sym_data = data.get(td_sym, {})
                    if isinstance(sym_data, dict) and "values" in sym_data:
                        tz = sym_data.get("meta", {}).get("exchange_timezone")
                        df = _td_values_to_dataframe(sym_data["values"], tz)
                        if not df.empty:
                            result[yf_t] = df
                            _cache_set(f"hist:{yf_t}:{period_days}:{interval}", df, CACHE_TTL_SHORT)
                        else:
                            yf_only.append(yf_t)
                    else:
                        yf_only.append(yf_t)
        else:
            # TD batch failed — fall back all tickers
            for _, td_sym in ticker_pairs:
                yf_only.append(td_to_yf[td_sym])

    # Fallback: fetch remaining via yfinance individually
    for t in yf_only:
        yf_period = f"{period_days}d"
        yf_interval = _td_interval_to_yf(interval)
        df = _yf_history(t, period=yf_period, interval=yf_interval)
        if df is not None:
            result[t] = df
            _cache_set(f"hist:{t}:{period_days}:{interval}", df, CACHE_TTL_SHORT)

    return result


def fetch_quote(ticker: str) -> dict | None:
    """Fetch real-time quote for spread/liquidity analysis.

    Returns dict with 'price' and 'intraday_range_pct' or None.
    Uses Twelve Data /quote when available, falls back to history-based estimate.
    """
    cache_key = f"quote:{ticker}"
    cached = _cache_get_or_miss(cache_key)
    if cached is not _CACHE_MISS:
        return cached

    result = None

    # Try Twelve Data /quote
    mapping = _map_ticker(ticker)
    if mapping and td_available():
        td_sym, extra_params = mapping
        params = {"symbol": td_sym, **extra_params}
        data = _td_request("quote", params, yf_ticker=ticker)
        if data and "close" in data:
            try:
                price = float(data.get("close", 0))
                high = float(data.get("high", price))
                low = float(data.get("low", price))
                if price > 0 and high > low:
                    intraday_range_pct = (high - low) / price * 100
                    result = {
                        "price": price,
                        "high": high,
                        "low": low,
                        "intraday_range_pct": round(intraday_range_pct, 4),
                    }
            except (ValueError, KeyError):
                pass

    if result is None:
        # Fallback: estimate from recent daily bars
        try:
            df = fetch_history(ticker, period_days=5, interval="1day")
            if df is not None and not df.empty:
                price = float(df["Close"].iloc[-1])
                if price > 0:
                    ranges = []
                    for _, row in df.iterrows():
                        h, l = float(row["High"]), float(row["Low"])
                        if h > 0 and l > 0:
                            ranges.append((h - l) / price * 100)
                    avg_range = sum(ranges) / len(ranges) if ranges else 0
                    result = {
                        "price": price,
                        "intraday_range_pct": round(avg_range, 4),
                    }
        except Exception:
            pass

    _cache_set(cache_key, result, CACHE_TTL_SHORT)
    return result


def fetch_price(ticker: str) -> float | None:
    """Fetch current price for a ticker.

    Wrapper around fetch_quote() — returns just the price float or None.
    Used by agents (Trader 2/3/4, Journal 2/3) for position monitoring.
    """
    quote = fetch_quote(ticker)
    if quote and "price" in quote:
        price = quote["price"]
        if price is not None and price > 0:
            return price
    return None


# ── Price validation ─────────────────────────────────────────────
# Reference prices from last known-good fetch, used to detect anomalies.
_price_reference: dict[str, float] = {}
_price_ref_lock = threading.Lock()

# Max allowed deviation from reference price (50% = 1.5x or 0.5x).
# Legitimate single-day moves rarely exceed 20%, even for volatile commodities.
_MAX_PRICE_DEVIATION = 0.50

# Hardcoded plausible price ranges (min, max) per ticker.
# Catches wrong-unit/wrong-instrument errors even on first fetch (no reference).
# Ranges are generous (5x-10x from current levels) to allow for long-term moves.
# Updated 2026-03 from yfinance ground truth.
_PRICE_RANGES: dict[str, tuple[float, float]] = {
    # Commodities — futures
    "CC=F": (500, 15000),       # Cocoa $/ton (yf ~3425)
    "KC=F": (50, 1000),         # Coffee cents/lb (yf ~296)
    "ZW=F": (200, 2000),        # Wheat cents/bu (yf ~590)
    "ZC=F": (150, 1500),        # Corn cents/bu (yf ~450)
    "ZS=F": (400, 3000),        # Soybeans cents/bu (yf ~1204)
    "HG=F": (1.0, 20.0),        # Copper $/lb as cwt (yf ~5.93)
    "CL=F": (20, 250),          # WTI Crude (yf ~86)
    "BZ=F": (20, 250),          # Brent Crude (yf ~86)
    "NG=F": (0.5, 20.0),        # Natural Gas (yf ~3.05)
    "GC=F": (1000, 15000),      # Gold (yf ~5236)
    "SI=F": (10, 250),          # Silver (yf ~90)
    "PL=F": (400, 5000),        # Platinum (yf ~2215)
    "PA=F": (300, 5000),        # Palladium (yf ~1688)
    "SB=F": (3, 50),            # Sugar cents/lb (yf ~14)
    "CT=F": (20, 200),          # Cotton cents/lb (yf ~65)
    "OJ=F": (50, 800),          # Orange Juice (yf ~189)
    "LE=F": (80, 500),          # Live Cattle (yf ~233)
    "HE=F": (30, 250),          # Lean Hogs (yf ~96)
    # Indices
    "^GSPC": (2000, 15000),     # S&P 500 (yf ~6823)
    "^DJI": (15000, 80000),     # Dow Jones (yf ~47987)
    "^IXIC": (5000, 40000),     # Nasdaq (yf ~22832)
    "^RUT": (800, 5000),        # Russell 2000 (yf ~2575)
    "^FCHI": (3000, 15000),     # CAC 40 (yf ~8054)
    "^GDAXI": (8000, 40000),    # DAX (yf ~23936)
    "^FTSE": (4000, 15000),     # FTSE 100 (yf ~10404)
    "^N225": (15000, 80000),    # Nikkei 225 (yf ~54248)
    # Forex — ranges are tight since they're currency pairs
    "EURUSD=X": (0.7, 1.6),     # (yf ~1.08)
    "USDJPY=X": (80, 200),      # (yf ~148)
    "GBPUSD=X": (0.9, 1.8),     # (yf ~1.29)
    "USDCHF=X": (0.6, 1.4),     # (yf ~0.88)
    "AUDUSD=X": (0.4, 1.1),     # (yf ~0.63)
    "USDCNH=X": (5.0, 10.0),    # (yf ~7.24)
}


def validate_price(ticker: str, price: float | None) -> tuple[bool, str]:
    """Validate a price against the reference (last known-good price).

    Returns (is_valid, reason).
    - First checks hardcoded price ranges (catches wrong-unit errors).
    - If no reference exists, the price is accepted and becomes the reference.
    - If deviation exceeds _MAX_PRICE_DEVIATION (50%), price is rejected.
    """
    if price is None or price <= 0:
        return False, f"Invalid price: {price}"

    # Check hardcoded range first (catches wrong-unit/instrument on first fetch)
    price_range = _PRICE_RANGES.get(ticker)
    if price_range:
        low, high = price_range
        if price < low or price > high:
            return False, (
                f"Price out of range: {ticker} price={price} "
                f"expected [{low}, {high}]"
            )

    with _price_ref_lock:
        ref = _price_reference.get(ticker)

    if ref is None:
        # No reference yet — accept and store
        _update_price_reference(ticker, price)
        return True, "first_price"

    deviation = abs(price - ref) / ref
    if deviation > _MAX_PRICE_DEVIATION:
        return False, (
            f"Price anomaly: {ticker} price={price} vs reference={ref} "
            f"(deviation={deviation:.1%} > {_MAX_PRICE_DEVIATION:.0%})"
        )

    # Valid — update reference
    _update_price_reference(ticker, price)
    return True, "ok"


def _update_price_reference(ticker: str, price: float) -> None:
    """Store a validated price as the new reference."""
    with _price_ref_lock:
        _price_reference[ticker] = price


def fetch_price_validated(ticker: str) -> float | None:
    """Fetch current price with sanity-check validation.

    Use this for entry_price and any price that will be stored long-term.
    Falls back to yfinance cross-check if Twelve Data price looks anomalous.
    """
    price = fetch_price(ticker)
    if price is None:
        return None

    is_valid, reason = validate_price(ticker, price)
    if is_valid:
        return price

    # Price anomaly detected — try cross-checking with yfinance
    logger.warning("Price validation failed: %s — cross-checking with yfinance", reason)
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        h = t.history(period="5d")
        if not h.empty:
            yf_price = float(h["Close"].iloc[-1])
            if yf_price > 0:
                # Check if yfinance agrees with the anomalous price
                yf_deviation = abs(price - yf_price) / yf_price
                if yf_deviation < 0.10:  # Within 10% — original price was correct
                    logger.info("yfinance confirms price for %s: %.4f (was flagged at %.4f)",
                                ticker, yf_price, price)
                    _update_price_reference(ticker, price)
                    return price
                else:
                    # yfinance disagrees — use yfinance price
                    logger.warning("Using yfinance price for %s: %.4f (rejected: %.4f)",
                                   ticker, yf_price, price)
                    _update_price_reference(ticker, yf_price)
                    return yf_price
    except Exception as exc:
        logger.warning("yfinance cross-check failed for %s: %s", ticker, exc)

    # Both sources failed validation — reject
    logger.error("REJECTED price for %s: %s — no valid cross-check available", ticker, reason)
    return None


def seed_price_references() -> None:
    """Seed the reference price cache from recent history at startup.

    Called once at app init to establish baseline prices,
    so the first fetch_price_validated() call has something to compare against.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Get all known tickers from config
    try:
        from .config import ASSETS
        tickers = [a["ticker"] for a in ASSETS]
    except Exception:
        logger.warning("Could not load ASSETS for price seeding")
        return

    logger.info("Seeding price references for %d tickers...", len(tickers))
    seeded = 0

    def _seed_one(ticker: str) -> tuple[str, float | None]:
        try:
            p = fetch_price(ticker)
            return ticker, p
        except Exception:
            return ticker, None

    executor = ThreadPoolExecutor(max_workers=5)
    try:
        futs = {executor.submit(_seed_one, t): t for t in tickers}
        for fut in as_completed(futs, timeout=60):
            ticker, price = fut.result()
            if price is not None and price > 0:
                _update_price_reference(ticker, price)
                seeded += 1
    except Exception as exc:
        logger.warning("Price seeding timeout: %s", exc)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    logger.info("Seeded %d/%d price references", seeded, len(tickers))


def fetch_intraday(ticker: str, period: str = "5d", interval: str = "1h") -> pd.DataFrame | None:
    """Fetch intraday OHLCV data for a ticker.

    Wrapper around fetch_history() — converts period string to days.
    Used by Journal 2 for MAE/MFE enrichment with intraday bars.
    """
    period_map = {"1d": 1, "2d": 2, "5d": 5, "7d": 7, "10d": 10, "30d": 30}
    period_days = period_map.get(period, 5)
    return fetch_history(ticker, period_days=period_days, interval=interval)


def get_provider_status() -> dict:
    """Return status info about the market data provider for health checks."""
    with _blacklist_lock:
        bl_count = len(_td_blacklist)
    with _rate_lock:
        now = time.monotonic()
        recent = sum(1 for t in _request_times if now - t <= 60)
    with _cache_lock:
        cache_size = len(_cache)

    return {
        "primary": "twelve_data" if td_available() else "yfinance",
        "twelve_data_configured": td_available(),
        "td_requests_last_minute": recent,
        "td_rate_limit": _TD_RPM,
        "td_blacklisted_tickers": bl_count,
        "cache_entries": cache_size,
    }
