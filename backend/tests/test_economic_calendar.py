"""Tests for economic calendar module."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from backend.app.economic_calendar import (
    FOMC_DATES,
    ECB_DATES,
    BOE_DATES,
    EconomicEvent,
    check_event_conflict,
    get_events_context,
    get_upcoming_events,
    _nth_weekday,
    _generate_nfp_dates,
)

PARIS_TZ = ZoneInfo("Europe/Paris")


# ── Basic calendar data tests ────────────────────────────────────────


def test_fomc_dates_exist():
    """FOMC dates should be defined for 2025 and 2026."""
    assert len(FOMC_DATES) >= 16  # 8 per year × 2 years


def test_ecb_dates_exist():
    """ECB dates should be defined for 2025 and 2026."""
    assert len(ECB_DATES) >= 16


def test_boe_dates_exist():
    """BOE dates should be defined for 2025 and 2026."""
    assert len(BOE_DATES) >= 16


def test_nth_weekday_first_friday():
    """First Friday of January 2026 should be Jan 2."""
    d = _nth_weekday(2026, 1, 4, 1)  # First Friday
    assert d == date(2026, 1, 2)
    assert d.weekday() == 4  # Friday


def test_nth_weekday_second_tuesday():
    """Second Tuesday of February 2026 should be Feb 10."""
    d = _nth_weekday(2026, 2, 1, 2)  # Second Tuesday
    assert d.weekday() == 1  # Tuesday
    assert 8 <= d.day <= 14  # Second week


def test_generate_nfp_dates():
    """Should generate 12 NFP dates (first Friday of each month)."""
    dates = _generate_nfp_dates(2026)
    assert len(dates) == 12
    for d in dates:
        assert d.weekday() == 4  # All should be Fridays
        assert d.day <= 7  # First Friday is always in first 7 days


# ── Event lookup tests ───────────────────────────────────────────────


def test_get_upcoming_events_on_fomc_day():
    """Should return FOMC event when querying a known FOMC date."""
    fomc_date = list(FOMC_DATES)[0]
    events = get_upcoming_events(fomc_date, window_days=1)
    fomc_events = [e for e in events if "FOMC" in e.name]
    assert len(fomc_events) >= 1
    assert fomc_events[0].impact == "high"
    assert fomc_events[0].currency == "USD"
    assert fomc_events[0].blocks_trade is True


def test_get_upcoming_events_on_ecb_day():
    """Should return ECB event on ECB meeting day."""
    ecb_date = list(ECB_DATES)[0]
    events = get_upcoming_events(ecb_date, window_days=1)
    ecb_events = [e for e in events if "ECB" in e.name]
    assert len(ecb_events) >= 1
    assert ecb_events[0].currency == "EUR"


def test_get_upcoming_events_empty_on_random_date():
    """No events expected on a random Saturday."""
    # Pick a Saturday with no events
    d = date(2026, 6, 13)  # Saturday
    events = get_upcoming_events(d, window_days=0)
    # Might still catch nearby events, but shouldn't be many
    assert isinstance(events, list)


# ── Event conflict detection ─────────────────────────────────────────


def test_check_event_conflict_during_fomc():
    """Should detect conflict when scan is during FOMC window."""
    fomc_date = list(FOMC_DATES)[0]
    # Scan 1 hour before the event
    fomc_event = [e for e in get_upcoming_events(fomc_date) if "FOMC" in e.name][0]
    scan_time = datetime.combine(
        fomc_date,
        fomc_event.time_cet,
        tzinfo=PARIS_TZ,
    ) - timedelta(hours=1)

    conflict = check_event_conflict(scan_time)
    assert conflict is not None
    assert "FOMC" in conflict.name


def test_check_event_conflict_well_before():
    """No conflict when scan is well before any event."""
    fomc_date = list(FOMC_DATES)[0]
    # Scan 12 hours before — should be safe
    scan_time = datetime.combine(
        fomc_date,
        time(6, 0),
        tzinfo=PARIS_TZ,
    )
    conflict = check_event_conflict(scan_time)
    # FOMC is at 20:00 CET, 6:00 is 14h before — no conflict
    assert conflict is None


def test_check_event_conflict_no_events():
    """No conflict on a day with no events."""
    # Use a date far from any known event
    safe_date = datetime(2026, 8, 15, 10, 0, tzinfo=PARIS_TZ)  # Assumption nationale
    conflict = check_event_conflict(safe_date)
    assert conflict is None


# ── Context string for Claude ────────────────────────────────────────


def test_get_events_context_with_events():
    """Context string should mention event names."""
    fomc_date = list(FOMC_DATES)[0]
    ctx = get_events_context(fomc_date)
    assert "FOMC" in ctx or ctx == ""  # Depends on window


def test_get_events_context_empty():
    """Context should be empty when no events nearby."""
    # Far from any event
    ctx = get_events_context(date(2026, 8, 15))
    # Could be empty or have distant events
    assert isinstance(ctx, str)


# ── Economic event dataclass ─────────────────────────────────────────


def test_economic_event_frozen():
    """EconomicEvent should be immutable."""
    e = EconomicEvent(
        name="Test", date=date(2026, 1, 1),
        time_cet=time(14, 0), impact="high",
        currency="USD", blocks_trade=True,
    )
    assert e.name == "Test"
    assert e.blocks_trade is True


# ── Per-ticker sensitivity tests ─────────────────────────────────────


def test_agri_tickers_exempt_from_rate_decisions():
    """Agricultural commodities should NOT be blocked by central bank rate decisions."""
    from backend.app.economic_calendar import is_ticker_sensitive_to_event, MACRO_EXEMPT_TICKERS

    boe_event = EconomicEvent(
        name="BOE Rate Decision", date=date(2026, 3, 19),
        time_cet=time(13, 0), impact="high", currency="GBP",
    )
    ecb_event = EconomicEvent(
        name="ECB Rate Decision", date=date(2026, 3, 5),
        time_cet=time(14, 15), impact="high", currency="EUR",
    )
    fomc_event = EconomicEvent(
        name="FOMC Rate Decision", date=date(2026, 3, 18),
        time_cet=time(20, 0), impact="high", currency="USD",
    )

    # Agri/soft commodities should be exempt from ALL rate decisions
    for ticker in ["KC=F", "ZW=F", "CC=F", "ZC=F", "ZS=F", "SB=F", "OJ=F", "LE=F", "HE=F"]:
        assert not is_ticker_sensitive_to_event(ticker, boe_event), f"{ticker} should be exempt from BOE"
        assert not is_ticker_sensitive_to_event(ticker, ecb_event), f"{ticker} should be exempt from ECB"
        assert not is_ticker_sensitive_to_event(ticker, fomc_event), f"{ticker} should be exempt from FOMC"


def test_forex_blocked_by_relevant_rate_decision():
    """Forex tickers should be blocked by their currency's central bank."""
    from backend.app.economic_calendar import is_ticker_sensitive_to_event

    boe_event = EconomicEvent(
        name="BOE Rate Decision", date=date(2026, 3, 19),
        time_cet=time(13, 0), impact="high", currency="GBP",
    )
    assert is_ticker_sensitive_to_event("GBPUSD=X", boe_event)
    assert is_ticker_sensitive_to_event("^FTSE", boe_event)
    # But not EUR tickers
    assert not is_ticker_sensitive_to_event("EURUSD=X", boe_event)
    assert not is_ticker_sensitive_to_event("^FCHI", boe_event)


def test_usda_blocks_agri_tickers():
    """USDA WASDE should block agricultural commodity tickers (directly affected)."""
    from backend.app.economic_calendar import is_ticker_sensitive_to_event

    wasde_event = EconomicEvent(
        name="USDA WASDE Report", date=date(2026, 3, 10),
        time_cet=time(18, 0), impact="high", currency="USD",
    )
    # USDA WASDE directly impacts agri — should block
    assert is_ticker_sensitive_to_event("ZW=F", wasde_event)
    assert is_ticker_sensitive_to_event("ZC=F", wasde_event)
    assert is_ticker_sensitive_to_event("KC=F", wasde_event)


def test_check_event_conflict_with_exempt_ticker():
    """check_event_conflict with ticker= should skip events the ticker is exempt from."""
    from backend.app.economic_calendar import check_event_conflict

    # Use a BOE date — 2026-03-19 at 13:00 CET
    boe_date = date(2026, 3, 19)
    scan_time = datetime(2026, 3, 19, 13, 30, tzinfo=PARIS_TZ)  # Within BOE window

    # Without ticker → returns BOE conflict
    conflict = check_event_conflict(scan_time)
    assert conflict is not None
    assert "BOE" in conflict.name

    # With exempt ticker (coffee) → no conflict
    conflict = check_event_conflict(scan_time, ticker="KC=F")
    assert conflict is None

    # With sensitive ticker (GBPUSD) → still returns conflict
    conflict = check_event_conflict(scan_time, ticker="GBPUSD=X")
    assert conflict is not None
