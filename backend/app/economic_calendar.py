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


@dataclass(frozen=True)
class EconomicEvent:
    name: str
    date: date
    time_cet: time  # Time in CET/CEST
    impact: str  # "high", "medium"
    currency: str  # "USD", "EUR", "GBP", "JPY", "ALL"
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
    """NFP is released the first Friday of each month at 08:30 ET (14:30 CET)."""
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
    for d in FOMC_DATES:
        if window_start <= d <= window_end:
            events.append(EconomicEvent(
                name="FOMC Rate Decision",
                date=d,
                time_cet=time(20, 0),  # 14:00 ET = 20:00 CET (approx)
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

    # NFP — data release: fast repricing, tighter window
    for year in (target_date.year - 1, target_date.year, target_date.year + 1):
        for d in _generate_nfp_dates(year):
            if window_start <= d <= window_end:
                events.append(EconomicEvent(
                    name="US Non-Farm Payrolls (NFP)",
                    date=d,
                    time_cet=time(14, 30),
                    impact="high",
                    currency="USD",
                    blocks_trade=True,
                    hours_before=1.0,
                    hours_after=1.0,
                ))

    # CPI — hardcoded BLS dates, data release: fast repricing
    for d in CPI_DATES:
        if window_start <= d <= window_end:
            events.append(EconomicEvent(
                name="US CPI Release",
                date=d,
                time_cet=time(14, 30),
                impact="high",
                currency="USD",
                blocks_trade=True,
                hours_before=1.0,
                hours_after=1.0,
            ))

    events.sort(key=lambda e: (e.date, e.time_cet))
    return events


def check_event_conflict(
    scan_datetime: datetime | None = None,
) -> EconomicEvent | None:
    """Check if a major economic event is within the danger window.

    Returns the conflicting event if found, None otherwise.

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
            logger.warning(
                "Economic event conflict: %s at %s (scan at %s, window %s-%s)",
                event.name, event_dt.strftime("%Y-%m-%d %H:%M"),
                scan_dt_paris.strftime("%H:%M"),
                window_start.strftime("%H:%M"), window_end.strftime("%H:%M"),
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

    parts = []
    for e in events[:5]:
        parts.append(f"{e.name} le {e.date.isoformat()} a {e.time_cet.strftime('%H:%M')} CET ({e.impact})")

    return "Evenements a venir: " + " | ".join(parts)
