"""Economic calendar: blocks/downgrades trades before major macro events.

Knows about recurring events (FOMC, NFP, ECB, CPI, etc.) and checks
if a major event is imminent. Trades near these events have zero edge
because algos react in microseconds.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

PARIS_TZ = ZoneInfo("Europe/Paris")
ET_TZ = ZoneInfo("America/New_York")
JST_TZ = ZoneInfo("Asia/Tokyo")
AEST_TZ = ZoneInfo("Australia/Sydney")
CST_TZ = ZoneInfo("Asia/Shanghai")


def _to_paris_time(event_date: date, hour: int, minute: int, source_tz: ZoneInfo) -> time:
    """Convert a time in source_tz on event_date to Paris time.

    Handles DST transitions correctly: e.g. 14:00 ET is 20:00 CET in winter
    but 20:00 CEST in summer (same clock hour, but the UTC offset differs).
    More importantly, 08:30 ET = 14:30 CET (winter) vs 14:30 CEST (summer),
    which are both "14:30 Paris wall clock". However, when US and EU DST
    transitions don't align (2-3 weeks/year), the Paris wall clock shifts by 1h.
    """
    source_dt = datetime(
        event_date.year, event_date.month, event_date.day,
        hour, minute, tzinfo=source_tz,
    )
    paris_dt = source_dt.astimezone(PARIS_TZ)
    return paris_dt.time()


# ── Event priority for context sorting (lower = higher priority) ────
EVENT_PRIORITY: dict[str, int] = {
    "FOMC Rate Decision": 1,
    "BOJ Rate Decision": 2,
    "ECB Rate Decision": 2,
    "BOE Rate Decision": 2,
    "RBA Rate Decision": 2,
    "PBOC LPR Fixing": 2,
    "US Non-Farm Payrolls (NFP)": 3,
    "US CPI Release": 3,
    "USDA WASDE Report": 4,
    "USDA Quarterly Grain Stocks": 4,
    "EIA Weekly Petroleum Status": 5,
}


@dataclass(frozen=True)
class EconomicEvent:
    name: str
    date: date
    time_cet: time  # Time in CET/CEST (Paris wall clock)
    impact: str  # "high", "medium"
    currency: str  # "USD", "EUR", "GBP", "JPY", "CNY", "AUD", "ALL"
    blocks_trade: bool = True  # If True, no trade within the window
    hours_before: float = 2.0  # Danger window: hours BEFORE event
    hours_after: float = 1.0   # Danger window: hours AFTER event


# ── FOMC meeting dates 2025-2026 (published by the Fed) ──────────────
# These are the announcement dates (14:00 ET = 20:00 CET typically)
FOMC_DATES_2025 = [
    date(2025, 1, 29), date(2025, 3, 19), date(2025, 5, 7),
    date(2025, 6, 18), date(2025, 7, 30), date(2025, 9, 17),
    date(2025, 10, 29), date(2025, 12, 17),
]
FOMC_DATES_2026 = [
    date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29),
    date(2026, 6, 17), date(2026, 7, 29), date(2026, 9, 16),
    date(2026, 10, 28), date(2026, 12, 16),
]
FOMC_DATES_2027 = [
    # Official Fed tentative schedule (announced Sep 2025)
    date(2027, 1, 27), date(2027, 3, 17), date(2027, 4, 28),
    date(2027, 6, 9), date(2027, 7, 28), date(2027, 9, 15),
    date(2027, 10, 27), date(2027, 12, 8),
]
FOMC_DATES = set(FOMC_DATES_2025 + FOMC_DATES_2026 + FOMC_DATES_2027)

# ── ECB meeting dates 2025-2027 ──────────────────────────────────────
# Announcement at 14:15 CET
ECB_DATES_2025 = [
    date(2025, 1, 30), date(2025, 3, 6), date(2025, 4, 17),
    date(2025, 6, 5), date(2025, 7, 24), date(2025, 9, 11),
    date(2025, 10, 30), date(2025, 12, 18),
]
ECB_DATES_2026 = [
    date(2026, 1, 22), date(2026, 3, 5), date(2026, 4, 16),
    date(2026, 6, 4), date(2026, 7, 16), date(2026, 9, 10),
    date(2026, 10, 29), date(2026, 12, 17),
]
ECB_DATES_2027 = [
    # Estimated from 2025-2026 pattern (~6 weeks apart, Thursdays)
    # Update with official dates when ECB publishes them
    date(2027, 1, 21), date(2027, 3, 4), date(2027, 4, 15),
    date(2027, 6, 3), date(2027, 7, 22), date(2027, 9, 9),
    date(2027, 10, 28), date(2027, 12, 16),
]
ECB_DATES = set(ECB_DATES_2025 + ECB_DATES_2026 + ECB_DATES_2027)

# ── BOE meeting dates 2025-2027 ──────────────────────────────────────
BOE_DATES_2025 = [
    date(2025, 2, 6), date(2025, 3, 20), date(2025, 5, 8),
    date(2025, 6, 19), date(2025, 8, 7), date(2025, 9, 18),
    date(2025, 11, 6), date(2025, 12, 18),
]
BOE_DATES_2026 = [
    date(2026, 2, 5), date(2026, 3, 19), date(2026, 5, 7),
    date(2026, 6, 18), date(2026, 8, 6), date(2026, 9, 17),
    date(2026, 11, 5), date(2026, 12, 17),
]
BOE_DATES_2027 = [
    # Official BOE provisional dates
    date(2027, 2, 4), date(2027, 3, 18), date(2027, 4, 29),
    date(2027, 6, 17), date(2027, 7, 29), date(2027, 9, 16),
    date(2027, 11, 4), date(2027, 12, 16),
]
BOE_DATES = set(BOE_DATES_2025 + BOE_DATES_2026 + BOE_DATES_2027)

# ── BOJ meeting dates 2025-2026 (published by Bank of Japan) ──────
# Announcement ~12:00 JST — converted to Paris dynamically (DST-safe)
BOJ_DATES_2025 = [
    date(2025, 1, 24), date(2025, 3, 14), date(2025, 5, 1),
    date(2025, 6, 13), date(2025, 7, 31), date(2025, 9, 19),
    date(2025, 10, 31), date(2025, 12, 19),
]
BOJ_DATES_2026 = [
    date(2026, 1, 23), date(2026, 3, 13), date(2026, 4, 30),
    date(2026, 6, 12), date(2026, 7, 17), date(2026, 9, 18),
    date(2026, 10, 30), date(2026, 12, 18),
]
BOJ_DATES = set(BOJ_DATES_2025 + BOJ_DATES_2026)

# ── RBA meeting dates 2025-2026 (published by Reserve Bank of Australia) ──
# Announcement ~14:30 AEDT/AEST — converted to Paris dynamically (DST-safe)
RBA_DATES_2025 = [
    date(2025, 2, 18), date(2025, 4, 1), date(2025, 5, 20),
    date(2025, 7, 8), date(2025, 8, 12), date(2025, 9, 30),
    date(2025, 11, 4), date(2025, 12, 9),
]
RBA_DATES_2026 = [
    date(2026, 2, 3), date(2026, 3, 17), date(2026, 5, 5),
    date(2026, 6, 16), date(2026, 8, 4), date(2026, 9, 15),
    date(2026, 11, 3), date(2026, 12, 8),
]
RBA_DATES = set(RBA_DATES_2025 + RBA_DATES_2026)


def _generate_pboc_lpr_dates(year: int) -> list[date]:
    """PBOC LPR fixing is the 20th of each month (or next business day if weekend).

    Time: 09:15 CST (Asia/Shanghai) — converted to Paris dynamically.
    """
    dates = []
    for month in range(1, 13):
        d = date(year, month, 20)
        # If weekend, move to next Monday
        if d.weekday() == 5:  # Saturday
            d += timedelta(days=2)
        elif d.weekday() == 6:  # Sunday
            d += timedelta(days=1)
        dates.append(d)
    return dates


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """Find the nth occurrence of a weekday in a given month.

    weekday: 0=Monday, 4=Friday.
    n: 1=first, 2=second, etc.
    """
    first_day = date(year, month, 1)
    # Days until the first occurrence of weekday
    delta = (weekday - first_day.weekday()) % 7
    first_occurrence = first_day + timedelta(days=delta)
    return first_occurrence + timedelta(weeks=n - 1)


def _generate_nfp_dates(year: int) -> list[date]:
    """NFP is released the first Friday of each month at 08:30 ET."""
    dates = []
    for month in range(1, 13):
        dates.append(_nth_weekday(year, month, 4, 1))  # First Friday
    return dates


# ── US CPI release dates (from BLS schedule) ─────────────────────────
# Hardcoded because BLS publishes exact dates — algorithmic approximation is off by 5+ days
CPI_DATES_2025 = [
    date(2025, 1, 15), date(2025, 2, 12), date(2025, 3, 12),
    date(2025, 4, 10), date(2025, 5, 13), date(2025, 6, 11),
    date(2025, 7, 11), date(2025, 8, 12), date(2025, 9, 10),
    date(2025, 10, 14), date(2025, 11, 12), date(2025, 12, 10),
]
CPI_DATES_2026 = [
    date(2026, 1, 14), date(2026, 2, 11), date(2026, 3, 11),
    date(2026, 4, 14), date(2026, 5, 12), date(2026, 6, 10),
    date(2026, 7, 14), date(2026, 8, 12), date(2026, 9, 15),
    date(2026, 10, 13), date(2026, 11, 12), date(2026, 12, 10),
]
CPI_DATES_2027 = [
    # Estimated from BLS pattern (~2nd week of each month, Tue/Wed)
    # Update with official dates when BLS publishes them
    date(2027, 1, 13), date(2027, 2, 10), date(2027, 3, 10),
    date(2027, 4, 13), date(2027, 5, 12), date(2027, 6, 10),
    date(2027, 7, 13), date(2027, 8, 11), date(2027, 9, 14),
    date(2027, 10, 13), date(2027, 11, 10), date(2027, 12, 10),
]
CPI_DATES = set(CPI_DATES_2025 + CPI_DATES_2026 + CPI_DATES_2027)

# ── WASDE report dates (USDA — monthly, typically 10th-12th) ─────
# Most market-moving agricultural report
WASDE_DATES_2025 = [
    date(2025, 1, 10), date(2025, 2, 11), date(2025, 3, 11),
    date(2025, 4, 10), date(2025, 5, 12), date(2025, 6, 12),
    date(2025, 7, 11), date(2025, 8, 12), date(2025, 9, 12),
    date(2025, 10, 10), date(2025, 11, 11), date(2025, 12, 10),
]
WASDE_DATES_2026 = [
    date(2026, 1, 12), date(2026, 2, 10), date(2026, 3, 10),
    date(2026, 4, 9), date(2026, 5, 12), date(2026, 6, 11),
    date(2026, 7, 10), date(2026, 8, 12), date(2026, 9, 11),
    date(2026, 10, 9), date(2026, 11, 10), date(2026, 12, 10),
]
WASDE_DATES_2027 = [
    date(2027, 1, 12), date(2027, 2, 9), date(2027, 3, 9),
    date(2027, 4, 9), date(2027, 5, 11), date(2027, 6, 10),
    date(2027, 7, 12), date(2027, 8, 12), date(2027, 9, 10),
    date(2027, 10, 12), date(2027, 11, 9), date(2027, 12, 9),
]
WASDE_DATES = set(WASDE_DATES_2025 + WASDE_DATES_2026 + WASDE_DATES_2027)

# ── USDA Quarterly Grain Stocks / Prospective Plantings ──────────
# Released end of March, June, September, January
USDA_QUARTERLY_DATES_2025 = [
    date(2025, 1, 10), date(2025, 3, 31), date(2025, 6, 30), date(2025, 9, 30),
]
USDA_QUARTERLY_DATES_2026 = [
    date(2026, 1, 12), date(2026, 3, 31), date(2026, 6, 30), date(2026, 9, 30),
]
USDA_QUARTERLY_DATES_2027 = [
    date(2027, 1, 12), date(2027, 3, 31), date(2027, 6, 30), date(2027, 9, 30),
]
USDA_QUARTERLY_DATES = set(
    USDA_QUARTERLY_DATES_2025 + USDA_QUARTERLY_DATES_2026 + USDA_QUARTERLY_DATES_2027
)


def _generate_eia_weekly_dates(year: int) -> list[date]:
    """EIA Weekly Petroleum Status Report is released every Wednesday at 10:30 ET."""
    dates = []
    d = date(year, 1, 1)
    while d.year == year:
        if d.weekday() == 2:  # Wednesday
            dates.append(d)
        d += timedelta(days=1)
    return dates


def get_upcoming_events(target_date: date | None = None, window_days: int = 2) -> list[EconomicEvent]:
    """Get economic events within window_days of target_date.

    Returns events sorted by date/time.
    """
    if target_date is None:
        target_date = datetime.now(PARIS_TZ).date()

    events: list[EconomicEvent] = []
    window_start = target_date - timedelta(days=1)
    window_end = target_date + timedelta(days=window_days)

    # FOMC — rate decisions: widest window (2h before, 2h after)
    # 14:00 ET — converted to Paris dynamically (DST-safe, C5)
    for d in FOMC_DATES:
        if window_start <= d <= window_end:
            events.append(EconomicEvent(
                name="FOMC Rate Decision",
                date=d,
                time_cet=_to_paris_time(d, 14, 0, ET_TZ),
                impact="high",
                currency="USD",
                blocks_trade=True,
                hours_before=2.0,
                hours_after=2.0,  # Follow-through trading post-FOMC
            ))

    # ECB — rate decisions: wide window
    for d in ECB_DATES:
        if window_start <= d <= window_end:
            events.append(EconomicEvent(
                name="ECB Rate Decision",
                date=d,
                time_cet=time(14, 15),
                impact="high",
                currency="EUR",
                blocks_trade=True,
                hours_before=2.0,
                hours_after=1.5,
            ))

    # BOE — rate decisions
    for d in BOE_DATES:
        if window_start <= d <= window_end:
            events.append(EconomicEvent(
                name="BOE Rate Decision",
                date=d,
                time_cet=time(13, 0),
                impact="high",
                currency="GBP",
                blocks_trade=True,
                hours_before=2.0,
                hours_after=1.5,
            ))

    # BOJ — rate decisions at 12:00 JST
    # Converted to Paris dynamically (DST-safe, C7)
    for d in BOJ_DATES:
        if window_start <= d <= window_end:
            events.append(EconomicEvent(
                name="BOJ Rate Decision",
                date=d,
                time_cet=_to_paris_time(d, 12, 0, JST_TZ),
                impact="high",
                currency="JPY",
                blocks_trade=True,
                hours_before=2.0,
                hours_after=1.0,
            ))

    # RBA — rate decisions at 14:30 AEDT/AEST
    # Converted to Paris dynamically (DST-safe, C7)
    for d in RBA_DATES:
        if window_start <= d <= window_end:
            events.append(EconomicEvent(
                name="RBA Rate Decision",
                date=d,
                time_cet=_to_paris_time(d, 14, 30, AEST_TZ),
                impact="high",
                currency="AUD",
                blocks_trade=True,
                hours_before=2.0,
                hours_after=1.0,
            ))

    # PBOC LPR — monthly fixing at 09:15 CST (20th of each month)
    # Converted to Paris dynamically (DST-safe, C7)
    for year in (target_date.year - 1, target_date.year, target_date.year + 1):
        for d in _generate_pboc_lpr_dates(year):
            if window_start <= d <= window_end:
                events.append(EconomicEvent(
                    name="PBOC LPR Fixing",
                    date=d,
                    time_cet=_to_paris_time(d, 9, 15, CST_TZ),
                    impact="high",
                    currency="CNY",
                    blocks_trade=True,
                    hours_before=2.0,
                    hours_after=1.0,
                ))

    # NFP — data release at 08:30 ET: fast repricing, tighter window
    # Converted to Paris dynamically (DST-safe, C5)
    for year in (target_date.year - 1, target_date.year, target_date.year + 1):
        for d in _generate_nfp_dates(year):
            if window_start <= d <= window_end:
                events.append(EconomicEvent(
                    name="US Non-Farm Payrolls (NFP)",
                    date=d,
                    time_cet=_to_paris_time(d, 8, 30, ET_TZ),
                    impact="high",
                    currency="USD",
                    blocks_trade=True,
                    hours_before=1.0,
                    hours_after=1.0,
                ))

    # CPI — hardcoded BLS dates at 08:30 ET: fast repricing
    # Converted to Paris dynamically (DST-safe, C5)
    for d in CPI_DATES:
        if window_start <= d <= window_end:
            events.append(EconomicEvent(
                name="US CPI Release",
                date=d,
                time_cet=_to_paris_time(d, 8, 30, ET_TZ),
                impact="high",
                currency="USD",
                blocks_trade=True,
                hours_before=1.0,
                hours_after=1.0,
            ))

    # WASDE — monthly USDA report at 12:00 ET, blocks commodity trades
    # Converted to Paris dynamically (DST-safe, C5)
    for d in WASDE_DATES:
        if window_start <= d <= window_end:
            events.append(EconomicEvent(
                name="USDA WASDE Report",
                date=d,
                time_cet=_to_paris_time(d, 12, 0, ET_TZ),
                impact="high",
                currency="USD",
                blocks_trade=True,
                hours_before=1.0,
                hours_after=1.0,
            ))

    # USDA Quarterly (Grain Stocks / Prospective Plantings) at 12:00 ET
    # Converted to Paris dynamically (DST-safe, C5)
    for d in USDA_QUARTERLY_DATES:
        if window_start <= d <= window_end:
            events.append(EconomicEvent(
                name="USDA Quarterly Grain Stocks",
                date=d,
                time_cet=_to_paris_time(d, 12, 0, ET_TZ),
                impact="high",
                currency="USD",
                blocks_trade=True,
                hours_before=1.0,
                hours_after=1.5,
            ))

    # EIA Weekly Petroleum — Wednesdays at 10:30 ET (medium impact, doesn't block)
    # Converted to Paris dynamically (DST-safe, C5)
    for year in (target_date.year - 1, target_date.year, target_date.year + 1):
        for d in _generate_eia_weekly_dates(year):
            if window_start <= d <= window_end:
                events.append(EconomicEvent(
                    name="EIA Weekly Petroleum Status",
                    date=d,
                    time_cet=_to_paris_time(d, 10, 30, ET_TZ),
                    impact="medium",
                    currency="USD",
                    blocks_trade=False,  # Doesn't block — our post-EIA scan captures this
                    hours_before=0.5,
                    hours_after=0.5,
                ))

    events.sort(key=lambda e: (e.date, e.time_cet))
    return events


# ── Tickers sensitive to each currency's central bank / macro events ──
# Only these tickers are blocked during the corresponding event.
# Commodities driven by physical supply/demand (agri, softs, livestock)
# are NOT sensitive to rate decisions — a BOE rate change doesn't move coffee.
# "ALL" key = events that affect everything (unused currently, reserved).
CURRENCY_SENSITIVE_TICKERS: dict[str, set[str]] = {
    "USD": {
        # US indices
        "^GSPC", "^DJI", "^IXIC", "^RUT",
        # US stocks
        "AAPL", "MSFT", "TSLA", "AMZN",
        # USD forex
        "EURUSD=X", "USDJPY=X", "GBPUSD=X", "USDCHF=X", "EURJPY=X",
        "AUDUSD=X", "USDCNH=X",
        # Safe havens (rate-sensitive via USD strength)
        "GC=F", "SI=F",
        # Energy (USD-denominated, rate-sensitive)
        "CL=F", "BZ=F", "NG=F",
        # Copper (macro/China proxy)
        "HG=F",
        # EU indices/stocks (spillover from US macro)
        "^FCHI", "^GDAXI", "^FTSE", "^N225",
        "TTE.PA", "MC.PA", "BNP.PA", "SAN.PA", "AI.PA", "RMS.PA", "OR.PA",
    },
    "EUR": {
        # EU indices
        "^FCHI", "^GDAXI", "^FTSE",
        # EUR forex
        "EURUSD=X", "EURJPY=X",
        # Euronext stocks
        "TTE.PA", "MC.PA", "BNP.PA", "SAN.PA", "AI.PA", "RMS.PA", "OR.PA",
        # Safe havens (rate-sensitive)
        "GC=F", "SI=F",
    },
    "GBP": {
        # GBP forex
        "GBPUSD=X",
        # UK index
        "^FTSE",
        # Safe havens (minor spillover)
        "GC=F", "SI=F",
    },
    "JPY": {
        # JPY forex
        "USDJPY=X", "EURJPY=X",
        # Japan index
        "^N225",
        # Gold (yen carry trade unwind → gold bid)
        "GC=F",
    },
    "AUD": {
        # AUD forex
        "AUDUSD=X",
        # Copper (Australia = major exporter)
        "HG=F",
    },
    "CNY": {
        # CNY forex
        "USDCNH=X",
        # China demand proxies
        "HG=F", "AUDUSD=X",
        # Luxury (China consumer)
        "MC.PA", "RMS.PA", "OR.PA",
    },
}

# Tickers NEVER blocked by rate decisions / macro data releases.
# These are driven by physical supply/demand, not monetary policy.
# Weather, disease, crop reports → NOT affected by BOE/FOMC/ECB.
MACRO_EXEMPT_TICKERS: set[str] = {
    # Agriculture
    "ZC=F", "ZW=F", "ZS=F",
    # Tropical softs
    "KC=F", "CC=F", "SB=F", "OJ=F",
    # Livestock
    "LE=F", "HE=F",
    # PGMs (supply-driven, South African mines)
    "PL=F", "PA=F",
    # Cotton
    "CT=F",
    # Uranium ETF
    "URA",
}


def is_ticker_sensitive_to_event(ticker: str, event: "EconomicEvent") -> bool:
    """Check if a ticker is sensitive to a specific economic event.

    Agricultural commodities (coffee, wheat, cocoa, etc.) are NOT sensitive
    to central bank rate decisions — a BOE rate change doesn't move KC=F.

    USDA reports (WASDE, Quarterly) DO block agri tickers (they directly impact them).
    """
    # USDA reports affect agricultural commodities — block them
    if event.name in ("USDA WASDE Report", "USDA Quarterly Grain Stocks"):
        agri_tickers = {"ZC=F", "ZW=F", "ZS=F", "KC=F", "CC=F", "SB=F", "OJ=F",
                        "LE=F", "HE=F", "CT=F"}
        # USDA blocks agri tickers + general USD-sensitive tickers
        return ticker in agri_tickers or ticker in CURRENCY_SENSITIVE_TICKERS.get("USD", set())

    # Macro-exempt tickers are never blocked by rate decisions or data releases
    if ticker in MACRO_EXEMPT_TICKERS:
        return False

    # Check currency-specific sensitivity
    sensitive = CURRENCY_SENSITIVE_TICKERS.get(event.currency, set())
    if sensitive:
        return ticker in sensitive

    # Unknown currency → block conservatively
    return True


def check_event_conflict(
    scan_datetime: datetime | None = None,
    ticker: str | None = None,
) -> EconomicEvent | None:
    """Check if a major economic event is within the danger window.

    Returns the conflicting event if found, None otherwise.

    If ticker is provided, only returns a conflict if the ticker is
    sensitive to the event (e.g., KC=F is NOT blocked by BOE rate decision).
    If ticker is None, returns any active conflict (legacy global behavior).

    Each event type has its own window (hours_before/hours_after)
    set when the event is created. FOMC gets the widest window,
    data releases (NFP/CPI) get tighter windows.
    """
    if scan_datetime is None:
        scan_datetime = datetime.now(PARIS_TZ)

    # Ensure timezone-aware
    if scan_datetime.tzinfo is None:
        scan_datetime = scan_datetime.replace(tzinfo=PARIS_TZ)

    scan_dt_paris = scan_datetime.astimezone(PARIS_TZ)
    events = get_upcoming_events(scan_dt_paris.date(), window_days=1)

    for event in events:
        if not event.blocks_trade:
            continue

        event_dt = datetime.combine(event.date, event.time_cet, tzinfo=PARIS_TZ)

        window_start = event_dt - timedelta(hours=event.hours_before)
        window_end = event_dt + timedelta(hours=event.hours_after)

        if window_start <= scan_dt_paris <= window_end:
            # If ticker provided, check if this ticker is actually sensitive
            if ticker is not None and not is_ticker_sensitive_to_event(ticker, event):
                logger.info(
                    "Economic event %s active but ticker %s is exempt (not sensitive)",
                    event.name, ticker,
                )
                continue

            logger.warning(
                "Economic event conflict: %s at %s (scan at %s, window %s-%s%s)",
                event.name, event_dt.strftime("%Y-%m-%d %H:%M"),
                scan_dt_paris.strftime("%H:%M"),
                window_start.strftime("%H:%M"), window_end.strftime("%H:%M"),
                f", ticker={ticker}" if ticker else "",
            )
            return event

    return None


def get_events_context(target_date: date | None = None) -> str:
    """Build a context string about upcoming events for Claude prompt.

    This helps Claude calibrate its scoring — if FOMC is tomorrow,
    macro news should be scored lower.
    """
    events = get_upcoming_events(target_date, window_days=3)
    if not events:
        return ""

    # N1: Sort by impact priority before truncating to top 5
    # Lower priority number = higher impact (FOMC=1 > ECB/BOJ=2 > NFP=3 > WASDE=4 > EIA=5)
    events.sort(key=lambda e: (EVENT_PRIORITY.get(e.name, 5), e.date, e.time_cet))

    parts = []
    for e in events[:5]:
        parts.append(f"{e.name} le {e.date.isoformat()} a {e.time_cet.strftime('%H:%M')} CET ({e.impact})")

    return "Evenements a venir: " + " | ".join(parts)
