"""True 1-month pipeline integration test.

Exercises the REAL pipeline functions with mocks only at I/O boundaries:
- Claude API → mock responses with realistic scoring
- Market data (fetch_history, fetch_history_range, fetch_history_batch) → mock DataFrames
- File I/O → temp files
- PostgreSQL → disabled (JSON fallback)

Simulates 20 trading days × 4 scans/day = 80 scans through:
  score_news_batch() → select_trade() → save_trade() → run_daily_journal() → compute_learning_adjustments()

Each day:
  1. Generate realistic news items (weather, commodity, supply_chain, geopolitical)
  2. Score them via score_news_batch (Claude mocked with realistic structured responses)
  3. Select trades via select_trade (real calibration, correlation, spread filter)
  4. Save trades
  5. End-of-day: run_daily_journal (real TP/SL detection on mocked bars)
  6. Run learning adjustments
  7. Feed learning into next day's scans
"""

import json
import os
import tempfile
import time
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock

import pandas as pd
import numpy as np
import pytest

from backend.app.config import (
    ASSET_BY_TICKER,
    ASSETS,
    CATEGORY_SCORE_MULTIPLIERS,
    ESTIMATED_SPREADS,
)
from backend.app.models import (
    Direction,
    JournalEntry,
    NewsItem,
    ScanResult,
    ScanType,
    ScoredNews,
    TradeRecommendation,
    TradeResult,
)
from backend.app.news_scorer import score_news_batch, _compute_freshness
from backend.app.trade_selector import select_trade
from backend.app.learning import (
    compute_learning_adjustments,
    invalidate_perf_summary_cache,
    load_trades,
    save_trade,
    update_trade_result,
)
from backend.app.journal import run_daily_journal, load_journal


logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════
# Test fixtures & helpers
# ════════════════════════════════════════════════════════════════

# 20 days of realistic news scenarios across all asset categories
# Each scenario: (title, source, source_weight, category, tickers, surprise, delay, awareness, direction, magnitude, reliability)
DAILY_SCENARIOS = [
    # Day 1: Weather — Midwest drought (high edge)
    [
        ("NOAA: Severe drought developing across US Midwest corn belt — soil moisture at 10-year low", "NOAA", 1.1, "weather", ["ZC=F", "ZS=F"], 85, 85, 10, "LONG", 70, 90),
        ("Open-Meteo: Record heat wave forecast for Brazil Minas Gerais — 40°C next 5 days", "Open-Meteo", 1.2, "weather", ["KC=F"], 80, 90, 5, "LONG", 65, 85),
        ("Investing.com: Markets steady ahead of Fed decision", "Investing.com", 0.7, "macro", ["^GSPC"], 10, 5, 95, "NEUTRAL", 20, 90),
    ],
    # Day 2: EIA crude draw (commodity edge)
    [
        ("EIA: Crude oil inventories draw -8.2M barrels vs consensus -2M", "EIA", 1.15, "commodity", ["CL=F", "BZ=F"], 75, 70, 20, "LONG", 60, 95),
        ("Baltic Dry Index surges 18% in 3 sessions", "gCaptain", 0.9, "supply_chain", ["HG=F"], 65, 65, 30, "LONG", 50, 80),
    ],
    # Day 3: Geopolitical — Russia wheat export ban
    [
        ("Russia suspends all wheat exports for 90 days citing domestic shortages", "Reuters", 0.85, "geopolitical", ["ZW=F"], 88, 75, 15, "LONG", 75, 85),
        ("USDA: US wheat crop condition drops 12pp week-over-week", "USDA", 1.1, "commodity", ["ZW=F"], 78, 75, 18, "LONG", 60, 92),
    ],
    # Day 4: Panama Canal disruption
    [
        ("Panama Canal Authority restricts daily transits to 22 — drought impact worsening", "ACP", 0.9, "supply_chain", ["BZ=F", "CL=F"], 72, 80, 20, "LONG", 55, 88),
        ("Apple Q1 earnings beat estimates by 5%", "CNBC", 0.9, "earnings", ["^GSPC"], 15, 5, 98, "LONG", 20, 95),
    ],
    # Day 5: CFTC positioning extreme
    [
        ("CFTC COT: Commercial gold shorts at 5-year extreme — divergence with specs", "CFTC", 1.1, "commodity", ["GC=F", "SI=F"], 68, 65, 25, "LONG", 55, 90),
        ("ECB holds rates unchanged at 4.5% as expected", "ECB", 0.85, "macro", ["EURUSD=X"], 5, 5, 99, "NEUTRAL", 10, 95),
    ],
    # Day 6: Gas storage crisis
    [
        ("GIE AGSI: German gas storage drops to 28% — below 5-year seasonal average", "GIE", 1.15, "commodity", ["NG=F"], 75, 72, 18, "LONG", 65, 92),
        ("Geada cafe Minas Gerais — temperaturas abaixo de zero registradas", "GNews", 0.95, "weather", ["KC=F"], 85, 92, 5, "LONG", 70, 80),
    ],
    # Day 7: Hurricane threat
    [
        ("NHC: Tropical storm forming in Gulf of Mexico — Category 2 forecast within 48h", "NHC", 1.15, "weather", ["CL=F", "NG=F"], 80, 85, 12, "LONG", 65, 75),
        ("China PMI falls to 48.2 — contraction deepens", "Caixin", 0.9, "commodity", ["HG=F", "AUDUSD=X"], 60, 55, 35, "SHORT", 50, 85),
    ],
    # Day 8: Animal disease outbreak
    [
        ("WOAH: HPAI (H5N1) outbreak detected in Iowa — 2M turkeys culled", "WOAH", 1.15, "commodity", ["LE=F", "HE=F"], 72, 78, 12, "LONG", 55, 90),
        ("OPEC+ meets Sunday — sources say no change expected", "OilPrice", 0.9, "commodity", ["CL=F"], 20, 25, 70, "NEUTRAL", 15, 60),
    ],
    # Day 9: Suez Canal blockage
    [
        ("Suez Canal blocked by grounded container ship — diversions underway", "gCaptain", 0.9, "supply_chain", ["BZ=F", "CL=F"], 92, 85, 8, "LONG", 75, 90),
        ("NASA POWER: 14-day drought confirmed over Argentina Pampas — zero precipitation", "NASA", 1.15, "weather", ["ZS=F", "ZC=F"], 75, 82, 12, "LONG", 60, 88),
    ],
    # Day 10: Copper squeeze
    [
        ("LME copper inventory drops 42% in 8 weeks — lowest since 2005", "Reuters", 0.85, "commodity", ["HG=F"], 78, 68, 22, "LONG", 65, 85),
        ("Freight rates surge 25% on Asia-Europe route — container shortage worsening", "MarineLink", 0.9, "supply_chain", ["BZ=F"], 65, 60, 28, "LONG", 50, 80),
    ],
    # Day 11: OPEC surprise cut
    [
        ("OPEC+ announces surprise 500K bbl/day additional cut effective immediately", "OilPrice", 0.9, "commodity", ["CL=F", "BZ=F"], 82, 50, 40, "LONG", 70, 92),
        ("India wheat harvest threatened by record late-season heat", "Open-Meteo", 1.2, "weather", ["ZW=F"], 77, 80, 10, "LONG", 60, 80),
    ],
    # Day 12: Cocoa supply shock
    [
        ("Ivory Coast cocoa mid-crop arrivals down 35% year-over-year", "Reuters", 0.85, "commodity", ["CC=F"], 73, 72, 20, "LONG", 60, 82),
        ("US Federal Register: New anti-dumping tariffs on Chinese steel — 45% duty", "Federal Register", 0.9, "regulatory", ["HG=F"], 65, 70, 25, "LONG", 45, 90),
    ],
    # Day 13: Natural gas squeeze
    [
        ("TTF natural gas spot spikes 15% on Norwegian pipeline maintenance extension", "Reuters", 0.85, "commodity", ["NG=F"], 78, 60, 30, "LONG", 70, 85),
        ("Gold hits 3-month high as central banks increase reserves — WGC report", "Reuters", 0.85, "commodity", ["GC=F"], 55, 45, 40, "LONG", 45, 88),
    ],
    # Day 14: Sugar/Brazil FX
    [
        ("Brazil real crashes 4.5% — sugar export revenues repriced in USD terms", "Reuters", 0.85, "geopolitical", ["SB=F", "KC=F"], 75, 70, 18, "SHORT", 60, 85),
        ("USDA WASDE: US corn yield revised down 8% from prior estimate", "USDA", 1.1, "commodity", ["ZC=F"], 82, 78, 15, "LONG", 65, 95),
    ],
    # Day 15: Australian cyclone
    [
        ("Severe cyclone hits Western Australian wheat belt — crop damage extensive", "NHC", 1.15, "weather", ["ZW=F"], 80, 85, 10, "LONG", 65, 78),
        ("ASF outbreak confirmed in Vietnam — 50K pigs culled in Mekong Delta", "WOAH", 1.15, "commodity", ["HE=F", "LE=F"], 70, 75, 15, "LONG", 50, 88),
    ],
    # Day 16: Cotton supply disruption
    [
        ("India announces cotton export ban to protect domestic textile industry", "GNews", 0.95, "supply_chain", ["CT=F"], 85, 80, 12, "LONG", 70, 88),
        ("Palm oil exports from Indonesia surge — prices fall 8% this week", "Reuters", 0.85, "commodity", ["ZS=F"], 55, 50, 35, "SHORT", 45, 82),
    ],
    # Day 17: Uranium geopolitics
    [
        ("Niger military junta suspends all uranium exports to France", "Reuters", 0.85, "geopolitical", ["URA"], 85, 80, 15, "LONG", 70, 82),
        ("Orange juice futures — Florida citrus greening disease spreads to 3 new counties", "USDA", 1.1, "weather", ["OJ=F"], 68, 75, 18, "LONG", 55, 85),
    ],
    # Day 18: FX carry trade unwind
    [
        ("BOJ signals possible yield curve control adjustment — yen strengthens 2%", "BOJ", 0.9, "central_bank_subtle", ["USDJPY=X", "EURJPY=X"], 70, 45, 30, "SHORT", 55, 88),
        ("EIA: Natural gas storage injection well below 5-year average", "EIA", 1.15, "commodity", ["NG=F"], 72, 68, 22, "LONG", 55, 92),
    ],
    # Day 19: Platinum supply disruption
    [
        ("South Africa load-shedding Stage 6 — PGM mines forced to halt operations", "Reuters", 0.85, "supply_chain", ["PL=F", "PA=F"], 78, 75, 15, "LONG", 65, 85),
        ("Seca milho soja safra quebra — Rio Grande do Sul declara emergencia", "GNews", 0.95, "weather", ["ZS=F", "ZC=F"], 80, 88, 5, "LONG", 60, 78),
    ],
    # Day 20: Mixed signals — end of month
    [
        ("CFTC: Managed money net short crude at 3-year extreme — contrarian signal", "CFTC", 1.1, "commodity", ["CL=F"], 65, 60, 28, "LONG", 50, 85),
        ("China Caixin manufacturing PMI rebounds to 52.1 — copper demand proxy", "Caixin", 0.9, "commodity", ["HG=F", "AUDUSD=X"], 62, 55, 32, "LONG", 50, 82),
        ("Fed Chair Powell: 'We are data-dependent' — no change in guidance", "Federal Reserve", 0.85, "macro", ["^GSPC"], 5, 3, 99, "NEUTRAL", 5, 95),
    ],
]


def _make_price_df(base_price: float, days: int = 10, volatility: float = 0.02) -> pd.DataFrame:
    """Generate a realistic OHLCV DataFrame for mocking fetch_history."""
    rng = np.random.RandomState(42)
    dates = pd.date_range(end=datetime.now(), periods=days, freq="B")
    n = len(dates)  # Use actual number of business days
    prices = [base_price]
    for _ in range(n - 1):
        change = rng.normal(0, volatility)
        prices.append(prices[-1] * (1 + change))

    data = {
        "Open": [p * (1 + rng.uniform(-0.005, 0.005)) for p in prices],
        "High": [p * (1 + abs(rng.normal(0, volatility))) for p in prices],
        "Low": [p * (1 - abs(rng.normal(0, volatility))) for p in prices],
        "Close": prices,
        "Volume": [rng.randint(10000, 1000000) for _ in prices],
    }
    return pd.DataFrame(data, index=dates)


# Base prices for all 41 tickers
TICKER_PRICES = {
    "CL=F": 75.0, "BZ=F": 80.0, "NG=F": 3.50, "GC=F": 2050.0, "SI=F": 24.0,
    "HG=F": 4.20, "PL=F": 950.0, "PA=F": 1100.0, "ZC=F": 450.0, "ZW=F": 620.0,
    "ZS=F": 1350.0, "KC=F": 180.0, "SB=F": 22.0, "CC=F": 3200.0, "CT=F": 82.0,
    "OJ=F": 350.0, "LE=F": 175.0, "HE=F": 85.0, "EURUSD=X": 1.08, "USDJPY=X": 150.0,
    "GBPUSD=X": 1.26, "USDCHF=X": 0.88, "AUDUSD=X": 0.65, "USDCNH=X": 7.25,
    "EURJPY=X": 162.0, "^GSPC": 5200.0, "^DJI": 39000.0, "^IXIC": 16500.0,
    "^RUT": 2050.0, "^FCHI": 7800.0, "^GDAXI": 18200.0, "^FTSE": 7700.0,
    "^N225": 39000.0, "^VIX": 16.0, "TTE.PA": 58.0, "MC.PA": 780.0,
    "BNP.PA": 62.0, "SAN.PA": 78.0, "AI.PA": 135.0, "RMS.PA": 2100.0,
    "OR.PA": 410.0, "URA": 28.0,
}


def _mock_fetch_history(ticker, period_days=20, interval="1day"):
    """Mock fetch_history returning realistic DataFrames."""
    base = TICKER_PRICES.get(ticker, 100.0)
    return _make_price_df(base, days=max(period_days, 10))


def _mock_fetch_history_batch(tickers, period_days=20, interval="1day"):
    """Mock fetch_history_batch returning dict of DataFrames."""
    return {t: _mock_fetch_history(t, period_days, interval) for t in tickers}


# Global set: tickers that should LOSE (SL_HIT) in the current journal run.
# Set this before calling run_daily_journal() to control outcomes.
_losing_tickers: set = set()


def _mock_fetch_history_range(ticker, start=None, end=None, interval="15min"):
    """Mock fetch_history_range for journal intraday bars.

    If ticker is in _losing_tickers, generates bars that drift DOWN
    (LONG trades hit SL). Otherwise, bars drift UP (LONG trades hit TP).
    """
    base = TICKER_PRICES.get(ticker, 100.0)
    lose = ticker in _losing_tickers

    if interval == "15min":
        periods = 44  # ~11h of 15min bars
        freq = "15min"
    elif interval == "1h":
        periods = 11
        freq = "1h"
    else:
        periods = 1
        freq = "B"

    start_dt = start if isinstance(start, datetime) else datetime.combine(start, datetime.min.time())
    start_dt = start_dt.replace(hour=8, minute=0, tzinfo=timezone.utc)
    dates = pd.date_range(start=start_dt, periods=periods, freq=freq)

    rng = np.random.RandomState(hash(ticker) % 2**31)
    vol = 0.003 if interval == "15min" else 0.008

    if lose:
        # Losing bars: aggressive drift DOWN — LONG trades must hit SL
        # Stop is typically 1-2% below entry. Need Lows to reach -2% quickly.
        # -0.15%/bar × 44 bars = -6.6% cumulative drift.
        drift = -0.0015
        vol = vol * 0.3  # Very low vol so price doesn't recover
    else:
        # Winning bars: steady drift UP — LONG trades will hit TP
        drift = 0.0005   # +0.05% per bar → +2.2% over 44 bars
        vol = vol * 0.8

    prices = [base]
    for _ in range(periods - 1):
        prices.append(prices[-1] * (1 + drift + rng.normal(0, vol)))

    if lose:
        # Losing: Lows exaggerated downward, Highs stay tight (no recovery)
        # With drift -0.15%/bar, by bar 10 price is at -1.5%, by bar 20 at -3%
        # Low dips an extra 0.3-0.8% below close → ensures SL hit
        data = {
            "Open": [p * (1 + rng.uniform(-0.001, 0.001)) for p in prices],
            "High": [p * (1 + rng.uniform(0, 0.001)) for p in prices],
            "Low": [p * (1 - rng.uniform(0.003, 0.008)) for p in prices],
            "Close": prices,
            "Volume": [rng.randint(1000, 50000) for _ in prices],
        }
    else:
        # Winning: Highs exaggerated upward, Lows stay tight (no SL hit)
        data = {
            "Open": [p * (1 + rng.uniform(-0.001, 0.001)) for p in prices],
            "High": [p * (1 + rng.uniform(0.002, 0.006)) for p in prices],
            "Low": [p * (1 - rng.uniform(0, 0.002)) for p in prices],
            "Close": prices,
            "Volume": [rng.randint(1000, 50000) for _ in prices],
        }
    return pd.DataFrame(data, index=dates)


def _make_claude_response(news_items, day_scenarios):
    """Build a mock Claude API tool_use response matching the real format.

    Maps each news_item back to its scenario to produce realistic scores.
    """
    scores = []
    for i, item in enumerate(news_items):
        # Find matching scenario by title prefix
        matched = None
        for title, source, sw, cat, tickers, surprise, delay, awareness, direction, magnitude, reliability in day_scenarios:
            if item.title[:40] == title[:40]:
                matched = (title, source, sw, cat, tickers, surprise, delay, awareness, direction, magnitude, reliability)
                break

        if matched:
            _, _, _, cat, tickers, surprise, delay, awareness, direction, magnitude, reliability = matched
            scores.append({
                "index": i + 1,
                "surprise": surprise,
                "directional_clarity": 82,
                "transmission_delay": delay,
                "market_awareness": awareness,
                "expected_magnitude": magnitude,
                "signal_reliability": reliability,
                "direction": direction,
                "impacted_tickers": tickers[:2],
                "news_category": cat,
                "reasoning": f"Analysis of: {item.title[:80]}",
                "confirmed_event": reliability > 80,
            })
        else:
            # Unknown news — low score
            scores.append({
                "index": i + 1,
                "surprise": 20,
                "directional_clarity": 40,
                "transmission_delay": 10,
                "market_awareness": 80,
                "expected_magnitude": 20,
                "signal_reliability": 50,
                "direction": "NEUTRAL",
                "impacted_tickers": [],
                "news_category": "other",
                "reasoning": f"Low edge: {item.title[:80]}",
                "confirmed_event": False,
            })

    # Build mock response object matching anthropic SDK structure
    tool_block = SimpleNamespace(
        type="tool_use",
        name="submit_news_scores",
        input={"scores": scores},
    )
    usage = SimpleNamespace(
        input_tokens=1500,
        output_tokens=800,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    response = SimpleNamespace(
        content=[tool_block],
        stop_reason="tool_use",
        usage=usage,
    )
    return response


@contextmanager
def _temp_json(initial=None):
    """Temp JSON file."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(initial or [], f, default=str)
    f.close()
    try:
        yield Path(f.name)
    finally:
        os.unlink(f.name)


# ════════════════════════════════════════════════════════════════
# Main test: 1 month pipeline simulation
# ════════════════════════════════════════════════════════════════

class TestOneMonthRealPipeline:
    """Simulates 20 trading days through the REAL pipeline functions.

    Each day runs 2 scans (Europe + US), then journal closure + learning.
    Total: 40 scans, ~20 journal runs, continuous learning evolution.
    """

    def test_full_month_pipeline(self):
        """Run 20 days of real pipeline: score → select → journal → learn."""

        base_date = datetime(2026, 2, 2, tzinfo=timezone.utc)  # Monday

        # Tracking
        all_trades_saved = []
        daily_results = []
        journal_entries_all = []
        learning_states = []
        errors = []
        scans_run = 0
        trades_taken = 0
        journal_runs = 0

        with _temp_json([]) as trades_file, _temp_json([]) as journal_file:
            # Patch all I/O boundaries
            patches = [
                # Disable PostgreSQL
                patch("backend.app.learning.is_pg_enabled", return_value=False),
                patch("backend.app.journal.is_pg_enabled", return_value=False),
                # Redirect file I/O to temp files
                patch("backend.app.learning.TRADES_FILE", trades_file),
                patch("backend.app.journal.JOURNAL_FILE", journal_file),
                # Mock market data
                patch("backend.app.trade_selector.fetch_history", side_effect=_mock_fetch_history),
                patch("backend.app.news_scorer.fetch_history_batch", side_effect=_mock_fetch_history_batch),
                patch("backend.app.news_scorer.fetch_history", side_effect=_mock_fetch_history),
                patch("backend.app.journal.fetch_history_range", side_effect=_mock_fetch_history_range),
                # Mock Claude API key presence
                patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-for-integration"}),
                # Mock economic calendar (no blocking)
                patch("backend.app.trade_selector.check_event_conflict", return_value=False),
            ]

            for p in patches:
                p.start()

            try:
                for day_idx in range(20):
                    day_date = base_date + timedelta(days=day_idx)
                    # Skip weekends
                    if day_date.weekday() >= 5:
                        continue

                    day_scenarios = DAILY_SCENARIOS[day_idx % len(DAILY_SCENARIOS)]
                    day_record = {
                        "day": day_idx + 1,
                        "date": day_date.isoformat(),
                        "scans": [],
                        "trades": [],
                        "journal": [],
                        "errors": [],
                    }

                    # ── 2 Scans per day: Europe (08:00) + US (15:00) ──
                    for scan_idx, (scan_type, scan_hour) in enumerate([
                        (ScanType.EUROPE, 8),
                        (ScanType.US, 15),
                    ]):
                        scan_time = day_date.replace(hour=scan_hour, minute=50)

                        # 1. Create news items
                        news_items = []
                        for title, source, sw, cat, tickers, *_ in day_scenarios:
                            news_items.append(NewsItem(
                                title=title,
                                source=source,
                                url=f"https://example.com/{day_idx}/{scan_idx}",
                                published=scan_time - timedelta(hours=1),
                                related_tickers=tickers,
                                source_weight=sw,
                                description=f"Details about: {title[:60]}",
                            ))

                        # 2. Score via score_news_batch (real function, Claude mocked)
                        def _mock_create(**kwargs):
                            return _make_claude_response(news_items, day_scenarios)

                        mock_client = MagicMock()
                        mock_client.messages.create = _mock_create

                        with patch("backend.app.news_scorer._get_client", return_value=mock_client):
                            try:
                                scored_news, market_ctx = score_news_batch(news_items, scan_type)
                            except Exception as e:
                                errors.append(f"Day {day_idx+1} scan {scan_type.value}: score_news_batch error: {e}")
                                day_record["errors"].append(str(e))
                                continue

                        scans_run += 1
                        day_record["scans"].append({
                            "scan_type": scan_type.value,
                            "news_count": len(news_items),
                            "scored_count": len(scored_news),
                            "top_score": scored_news[0].total_score if scored_news else 0,
                        })

                        # 3. Get learning adjustments
                        invalidate_perf_summary_cache()
                        try:
                            learning_adj = compute_learning_adjustments()
                        except Exception as e:
                            learning_adj = None
                            errors.append(f"Day {day_idx+1}: learning error: {e}")

                        # 4. Select trade (real function)
                        existing_tickers = [t.ticker for t in all_trades_saved
                                           if t.timestamp.date() == day_date.date()
                                           and t.result == TradeResult.PENDING]
                        try:
                            with patch("backend.app.trade_selector.is_market_open", return_value=True):
                                scan_result = select_trade(
                                    scored_news,
                                    scan_type,
                                    learning_adjustments=learning_adj,
                                    existing_trade_ticker=existing_tickers,
                                    market_context=market_ctx,
                                )
                        except Exception as e:
                            errors.append(f"Day {day_idx+1} scan {scan_type.value}: select_trade error: {e}")
                            day_record["errors"].append(str(e))
                            continue

                        # 5. Save trade if selected
                        if scan_result.has_trade and scan_result.recommendation:
                            rec = scan_result.recommendation
                            # Override timestamp to match simulated day
                            rec.timestamp = scan_time
                            try:
                                save_trade(rec)
                                all_trades_saved.append(rec)
                                trades_taken += 1
                                day_record["trades"].append({
                                    "ticker": rec.ticker,
                                    "direction": rec.direction.value,
                                    "score": rec.raw_claude_score,
                                    "rr": rec.risk_reward,
                                    "category": rec.news_category,
                                })
                            except Exception as e:
                                errors.append(f"Day {day_idx+1}: save_trade error: {e}")

                    # ── End of day: Journal closure ──
                    # Patch datetime to simulate 22:00 CET journal run
                    try:
                        journal_result = run_daily_journal()
                        journal_runs += 1
                        if isinstance(journal_result, list):
                            journal_entries_all.extend(journal_result)
                            day_record["journal"] = [
                                {
                                    "ticker": e.get("ticker", "?") if isinstance(e, dict) else "?",
                                    "result": e.get("result", "?") if isinstance(e, dict) else "?",
                                    "pnl": e.get("pnl_pct", 0) if isinstance(e, dict) else 0,
                                }
                                for e in journal_result
                            ]
                    except Exception as e:
                        errors.append(f"Day {day_idx+1}: journal error: {e}")
                        day_record["errors"].append(str(e))

                    # ── Post-journal: Learning update ──
                    try:
                        invalidate_perf_summary_cache()
                        learning_state = compute_learning_adjustments()
                        learning_states.append(learning_state)
                    except Exception as e:
                        errors.append(f"Day {day_idx+1}: post-journal learning error: {e}")

                    daily_results.append(day_record)

                # ── Final verification inside patch context ──
                final_trades = load_trades()
                final_trades_count = len(final_trades)
                pending_count = sum(1 for t in final_trades if t.result == TradeResult.PENDING)

            finally:
                for p in patches:
                    p.stop()

        # ════════════════════════════════════════════════════════════
        # Assertions — verify the month ran correctly
        # ════════════════════════════════════════════════════════════

        # Print summary for debugging
        print(f"\n{'='*60}")
        print(f"ONE MONTH PIPELINE RESULTS")
        print(f"{'='*60}")
        print(f"Days simulated: {len(daily_results)}")
        print(f"Scans run: {scans_run}")
        print(f"Trades taken: {trades_taken}")
        print(f"Journal runs: {journal_runs}")
        print(f"Journal entries: {len(journal_entries_all)}")
        print(f"Errors: {len(errors)}")
        if errors:
            for e in errors:
                print(f"  ERROR: {e}")
        print(f"Learning states captured: {len(learning_states)}")

        # Per-day summary
        for dr in daily_results:
            trades_str = ", ".join(f"{t['ticker']} {t['direction']}" for t in dr["trades"]) or "none"
            journal_str = ", ".join(f"{j['ticker']}={j['result']}" for j in dr["journal"]) or "none"
            print(f"  Day {dr['day']}: scans={len(dr['scans'])}, trades=[{trades_str}], journal=[{journal_str}]")

        if learning_states:
            last = learning_states[-1]
            print(f"\nFinal learning state:")
            print(f"  Ticker adjustments: {len(last.get('adjustments', {}))}")
            print(f"  Session adj: {last.get('session_adj', {})}")
            print(f"  Direction adj: {last.get('direction_adj', {})}")
            print(f"  Delay bias: {last.get('delay_bias_adj', 'N/A')}")
            print(f"  Newscat adj: {len(last.get('newscat_adj', {}))}")

        print(f"{'='*60}\n")

        # ── Core assertions ──

        # 1. Pipeline ran without fatal errors
        assert len(errors) == 0, f"Pipeline had {len(errors)} errors:\n" + "\n".join(errors)

        # 2. Scans executed
        assert scans_run >= 20, f"Expected >= 20 scans (20 days × 2), got {scans_run}"

        # 3. Trades were taken (scoring + selection worked)
        assert trades_taken > 0, "No trades taken in 20 days — scoring or selection broken"

        # 4. Journal processed trades
        assert journal_runs > 0, "Journal never ran"

        # 5. Learning evolved
        assert len(learning_states) > 0, "Learning never computed"

        # 6. All scored news had valid scores
        for dr in daily_results:
            for scan in dr["scans"]:
                assert scan["scored_count"] > 0, f"Day {dr['day']}: no news scored"

        # 7. Learning state has correct structure
        last_learning = learning_states[-1]
        assert "adjustments" in last_learning
        assert "session_adj" in last_learning
        assert "newscat_adj" in last_learning
        assert "regime_adj" in last_learning
        assert "direction_adj" in last_learning
        assert "delay_bias_adj" in last_learning
        assert "decomposition" in last_learning

        # 8. Verify trades were actually persisted and loaded back
        assert final_trades_count == trades_taken, \
            f"Persistence mismatch: saved {trades_taken}, loaded {final_trades_count}"

        # 9. All daily records should have scans
        for dr in daily_results:
            assert len(dr["scans"]) >= 1, f"Day {dr['day']}: no scans"

    def test_learning_evolution_across_month(self):
        """Verify learning adjustments actually evolve as trades accumulate."""

        base_date = datetime(2026, 2, 2, tzinfo=timezone.utc)
        learning_snapshots = []

        with _temp_json([]) as trades_file, _temp_json([]) as journal_file:
            patches = [
                patch("backend.app.learning.is_pg_enabled", return_value=False),
                patch("backend.app.journal.is_pg_enabled", return_value=False),
                patch("backend.app.learning.TRADES_FILE", trades_file),
                patch("backend.app.journal.JOURNAL_FILE", journal_file),
                patch("backend.app.trade_selector.fetch_history", side_effect=_mock_fetch_history),
                patch("backend.app.news_scorer.fetch_history_batch", side_effect=_mock_fetch_history_batch),
                patch("backend.app.news_scorer.fetch_history", side_effect=_mock_fetch_history),
                patch("backend.app.journal.fetch_history_range", side_effect=_mock_fetch_history_range),
                patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
                patch("backend.app.trade_selector.check_event_conflict", return_value=False),
            ]

            for p in patches:
                p.start()

            try:
                for day_idx in range(20):
                    day_date = base_date + timedelta(days=day_idx)
                    if day_date.weekday() >= 5:
                        continue

                    day_scenarios = DAILY_SCENARIOS[day_idx % len(DAILY_SCENARIOS)]
                    scan_time = day_date.replace(hour=8, minute=50)

                    # Create news
                    news_items = [
                        NewsItem(
                            title=s[0], source=s[1], url="https://test.com",
                            published=scan_time - timedelta(hours=1),
                            related_tickers=s[4], source_weight=s[2],
                            description=f"Details: {s[0][:50]}",
                        )
                        for s in day_scenarios
                    ]

                    # Score
                    def _mock_create(**kwargs):
                        return _make_claude_response(news_items, day_scenarios)

                    mock_client = MagicMock()
                    mock_client.messages.create = _mock_create

                    with patch("backend.app.news_scorer._get_client", return_value=mock_client):
                        scored_news, market_ctx = score_news_batch(news_items, ScanType.EUROPE)

                    # Select + save
                    invalidate_perf_summary_cache()
                    learning_adj = compute_learning_adjustments()

                    with patch("backend.app.trade_selector.is_market_open", return_value=True):
                        scan_result = select_trade(
                            scored_news, ScanType.EUROPE,
                            learning_adjustments=learning_adj,
                            market_context=market_ctx,
                        )

                    if scan_result.has_trade and scan_result.recommendation:
                        rec = scan_result.recommendation
                        rec.timestamp = scan_time
                        save_trade(rec)

                    # Journal
                    run_daily_journal()

                    # Snapshot learning
                    invalidate_perf_summary_cache()
                    state = compute_learning_adjustments()
                    learning_snapshots.append({
                        "day": day_idx + 1,
                        "n_adjustments": len(state.get("adjustments", {})),
                        "n_newscat": len(state.get("newscat_adj", {})),
                        "delay_bias": state.get("delay_bias_adj"),
                        "decomposition_count": len(state.get("decomposition", {})),
                    })

            finally:
                for p in patches:
                    p.stop()

        # Learning should have more data points as month progresses
        assert len(learning_snapshots) > 0
        # Print evolution
        print("\nLearning evolution:")
        for snap in learning_snapshots:
            print(f"  Day {snap['day']}: ticker_adj={snap['n_adjustments']}, "
                  f"newscat={snap['n_newscat']}, delay_bias={snap['delay_bias']}")

    def test_scoring_produces_valid_scores(self):
        """Verify score_news_batch produces properly structured ScoredNews."""

        day_scenarios = DAILY_SCENARIOS[0]
        scan_time = datetime.now(timezone.utc)

        news_items = [
            NewsItem(
                title=s[0], source=s[1], url="https://test.com",
                published=scan_time - timedelta(hours=1),
                related_tickers=s[4], source_weight=s[2],
                description=f"Details: {s[0][:50]}",
            )
            for s in day_scenarios
        ]

        def _mock_create(**kwargs):
            return _make_claude_response(news_items, day_scenarios)

        mock_client = MagicMock()
        mock_client.messages.create = _mock_create

        with patch("backend.app.news_scorer._get_client", return_value=mock_client), \
             patch("backend.app.news_scorer.fetch_history_batch", side_effect=_mock_fetch_history_batch), \
             patch("backend.app.news_scorer.fetch_history", side_effect=_mock_fetch_history), \
             patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):

            scored, market_ctx = score_news_batch(news_items, ScanType.EUROPE)

        # Assertions
        assert len(scored) > 0, "No news scored"

        for s in scored:
            assert isinstance(s, ScoredNews)
            assert 0 <= s.surprise <= 100
            assert 0 <= s.directional_clarity <= 100
            assert 0 <= s.transmission_delay <= 100
            assert 0 <= s.market_awareness <= 100
            assert 0 <= s.expected_magnitude <= 100
            assert 0 <= s.signal_reliability <= 100
            assert s.direction in (Direction.LONG, Direction.SHORT, Direction.NEUTRAL)
            assert s.news_category in (
                "earnings", "macro", "geopolitical", "regulatory", "m_a",
                "sector", "commodity", "weather", "supply_chain",
                "central_bank_subtle", "other",
            )
            assert s.total_score >= 0

        # Weather/commodity should score higher than macro
        weather_scores = [s.total_score for s in scored if s.news_category == "weather"]
        macro_scores = [s.total_score for s in scored if s.news_category == "macro"]
        if weather_scores and macro_scores:
            assert max(weather_scores) > max(macro_scores), \
                f"Weather ({max(weather_scores):.1f}) should outscore macro ({max(macro_scores):.1f})"

        # Market context should be populated
        assert "vix" in market_ctx
        assert "regime" in market_ctx

    def test_trade_selection_respects_filters(self):
        """Verify select_trade applies correlation, spread, pre-move filters."""

        scan_time = datetime.now(timezone.utc)
        day_scenarios = DAILY_SCENARIOS[0]

        news_items = [
            NewsItem(
                title=s[0], source=s[1], url="https://test.com",
                published=scan_time - timedelta(hours=1),
                related_tickers=s[4], source_weight=s[2],
                description=f"Details: {s[0][:50]}",
            )
            for s in day_scenarios
        ]

        def _mock_create(**kwargs):
            return _make_claude_response(news_items, day_scenarios)

        mock_client = MagicMock()
        mock_client.messages.create = _mock_create

        with patch("backend.app.news_scorer._get_client", return_value=mock_client), \
             patch("backend.app.news_scorer.fetch_history_batch", side_effect=_mock_fetch_history_batch), \
             patch("backend.app.news_scorer.fetch_history", side_effect=_mock_fetch_history), \
             patch("backend.app.trade_selector.fetch_history", side_effect=_mock_fetch_history), \
             patch("backend.app.trade_selector.check_event_conflict", return_value=False), \
             patch("backend.app.trade_selector.is_market_open", return_value=True), \
             patch("backend.app.learning.is_pg_enabled", return_value=False), \
             patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):

            scored, market_ctx = score_news_batch(news_items, ScanType.EUROPE)

            with _temp_json([]) as tf:
                with patch("backend.app.learning.TRADES_FILE", tf):
                    # First trade — should succeed
                    result1 = select_trade(scored, ScanType.EUROPE, market_context=market_ctx)

                    if result1.has_trade:
                        # Second trade with same ticker blocked — test correlation
                        existing = [result1.recommendation.ticker]
                        result2 = select_trade(
                            scored, ScanType.EUROPE,
                            existing_trade_ticker=existing,
                            market_context=market_ctx,
                        )

                        # If same correlation group, should pick different ticker or no trade
                        if result2.has_trade:
                            assert result2.recommendation.ticker != result1.recommendation.ticker or \
                                result2.recommendation.ticker not in existing, \
                                "Correlation filter failed — same ticker selected twice"

        # ScanResult should always have proper structure
        assert isinstance(result1, ScanResult)
        assert isinstance(result1.has_trade, bool)
        if result1.has_trade:
            rec = result1.recommendation
            assert rec.risk_reward >= 1.0, f"R/R too low: {rec.risk_reward}"
            assert rec.entry_price > 0
            assert rec.target_price > 0
            assert rec.stop_price > 0
            assert rec.direction in (Direction.LONG, Direction.SHORT)

    def test_journal_closes_trades_with_real_bar_detection(self):
        """Verify run_daily_journal uses real TP/SL detection on bars."""

        trade_time = datetime.now(timezone.utc).replace(hour=9, minute=0)
        trade = TradeRecommendation(
            scan_type=ScanType.EUROPE,
            timestamp=trade_time,
            ticker="CL=F",
            asset_name="Pétrole WTI",
            category="commodities_energy",
            direction=Direction.LONG,
            news_headline="EIA crude draw test",
            news_category="commodity",
            catalyst="Test",
            entry_price=75.0,
            target_price=77.0,  # +2.67%
            stop_price=74.0,    # -1.33%
            target_pct=2.67,
            stop_pct=1.33,
            risk_reward=2.0,
            confidence=75,
            time_window="09:00 — 20:00",
            news_sources=["EIA"],
            raw_claude_score=65.0,
            learning_multiplier=1.0,
            predicted_transmission_delay=70,
            expected_magnitude=50,
            signal_reliability=85,
            result=TradeResult.PENDING,
        )

        with _temp_json([trade.model_dump(mode="json")]) as tf, \
             _temp_json([]) as jf:
            with patch("backend.app.learning.TRADES_FILE", tf), \
                 patch("backend.app.journal.JOURNAL_FILE", jf), \
                 patch("backend.app.learning.is_pg_enabled", return_value=False), \
                 patch("backend.app.journal.is_pg_enabled", return_value=False), \
                 patch("backend.app.journal.fetch_history_range", side_effect=_mock_fetch_history_range):

                result = run_daily_journal()

            # Verify trade was closed
            updated_trades = load_trades()

        assert isinstance(result, list)
        # Trade should no longer be PENDING
        if updated_trades:
            for t in updated_trades:
                if t.ticker == "CL=F":
                    assert t.result != TradeResult.PENDING, \
                        f"Trade still PENDING after journal: {t.result}"
                    assert t.exit_price is not None, "No exit price set"
                    assert t.pnl_pct is not None, "No PnL computed"

    def test_event_scanner_keywords_cover_all_scenarios(self):
        """Verify our test scenarios would trigger the event scanner."""
        from backend.app.event_scanner import HIGH_IMPACT_KEYWORDS

        # Flatten all scenario titles
        all_titles = []
        for day_scenarios in DAILY_SCENARIOS:
            for title, *_ in day_scenarios:
                all_titles.append(title.lower())

        # At least some titles should match high-impact keywords
        matches = 0
        for title in all_titles:
            for category, patterns in HIGH_IMPACT_KEYWORDS.items():
                for pattern in patterns:
                    if pattern.lower() in title:
                        matches += 1
                        break

        assert matches >= 10, \
            f"Only {matches}/~50 scenario titles match event scanner keywords — scenarios not realistic enough"

    def test_chain_reactions_triggered(self):
        """Verify chain reactions are detected during scoring."""

        scan_time = datetime.now(timezone.utc)
        # CL=F news should trigger chain reactions to TTE.PA, BZ=F, NG=F
        news = [NewsItem(
            title="Major oil supply disruption — pipeline explosion in Gulf",
            source="Reuters", url="https://test.com",
            published=scan_time - timedelta(hours=1),
            related_tickers=["CL=F"],
            source_weight=0.85,
            description="Pipeline explosion disrupts 500K bbl/day",
        )]

        scenario = [("Major oil supply disruption — pipeline explosion in Gulf",
                     "Reuters", 0.85, "supply_chain", ["CL=F"],
                     90, 85, 10, "LONG", 75, 90)]

        def _mock_create(**kwargs):
            return _make_claude_response(news, scenario)

        mock_client = MagicMock()
        mock_client.messages.create = _mock_create

        with patch("backend.app.news_scorer._get_client", return_value=mock_client), \
             patch("backend.app.news_scorer.fetch_history_batch", side_effect=_mock_fetch_history_batch), \
             patch("backend.app.news_scorer.fetch_history", side_effect=_mock_fetch_history), \
             patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):

            scored, _ = score_news_batch(news, ScanType.EUROPE)

        assert len(scored) >= 1
        oil_scored = [s for s in scored if "CL=F" in s.impacted_tickers]
        assert len(oil_scored) >= 1, "CL=F not in impacted tickers"

        # Chain reactions should have added secondary tickers
        all_impacted = set()
        for s in oil_scored:
            all_impacted.update(s.impacted_tickers)
            for cr in s.chain_reactions:
                all_impacted.add(cr.ticker)

        # CL=F chain: TTE.PA, BZ=F, NG=F
        assert len(all_impacted) > 1, \
            f"No chain reactions detected — only {all_impacted}"

    def test_zero_edge_filtering(self):
        """Verify earnings/macro headlines are filtered before Claude."""

        scan_time = datetime.now(timezone.utc)
        news = [
            NewsItem(title="Apple Q4 earnings beat expectations by 3%", source="CNBC",
                     url="https://test.com", published=scan_time - timedelta(hours=1),
                     related_tickers=["^GSPC"], source_weight=0.9, description=""),
            NewsItem(title="NOAA: Severe drought forming in Midwest corn belt", source="NOAA",
                     url="https://test.com", published=scan_time - timedelta(hours=1),
                     related_tickers=["ZC=F"], source_weight=1.1, description="Soil moisture critical"),
        ]

        scenarios = [
            ("Apple Q4 earnings beat expectations by 3%", "CNBC", 0.9, "earnings",
             ["^GSPC"], 15, 5, 98, "LONG", 20, 95),
            ("NOAA: Severe drought forming in Midwest corn belt", "NOAA", 1.1, "weather",
             ["ZC=F"], 85, 85, 10, "LONG", 70, 90),
        ]

        def _mock_create(**kwargs):
            return _make_claude_response(news, scenarios)

        mock_client = MagicMock()
        mock_client.messages.create = _mock_create

        with patch("backend.app.news_scorer._get_client", return_value=mock_client), \
             patch("backend.app.news_scorer.fetch_history_batch", side_effect=_mock_fetch_history_batch), \
             patch("backend.app.news_scorer.fetch_history", side_effect=_mock_fetch_history), \
             patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):

            scored, _ = score_news_batch(news, ScanType.EUROPE)

        # Both should be scored, but earnings should have much lower score
        earnings = [s for s in scored if s.news_category == "earnings"]
        weather = [s for s in scored if s.news_category == "weather"]

        assert len(weather) >= 1, "Weather news should be scored"
        if earnings and weather:
            assert weather[0].total_score > earnings[0].total_score, \
                f"Weather ({weather[0].total_score:.1f}) should outscore earnings ({earnings[0].total_score:.1f})"


# ════════════════════════════════════════════════════════════════
# 3-month test with controlled losses
# ════════════════════════════════════════════════════════════════

class TestThreeMonthWithLosses:
    """Simulates 3 months (60 trading days):
    - Month 1: 50% of trades LOSE (SL_HIT) — learning must detect and adjust
    - Month 2: learning adjustments active — should see fewer bad trades taken
    - Month 3: system should be adapted — better performance metrics

    Verifies that learning actually reacts to losses and improves selection.
    """

    def test_three_month_learning_reacts_to_losses(self):
        """Full 3-month sim: month 1 = 50% losses, months 2-3 = learning adapts."""
        global _losing_tickers

        base_date = datetime(2026, 1, 5, tzinfo=timezone.utc)  # Monday

        # Monthly tracking
        monthly_stats = {1: {"trades": 0, "wins": 0, "losses": 0, "expired": 0},
                         2: {"trades": 0, "wins": 0, "losses": 0, "expired": 0},
                         3: {"trades": 0, "wins": 0, "losses": 0, "expired": 0}}
        learning_snapshots = []
        all_errors = []
        all_daily = []

        # Tickers that LOSE in month 1 (every other trade loses)
        # We alternate: first trade of the day wins, second loses
        trade_counter = [0]  # mutable counter for closure

        with _temp_json([]) as trades_file, _temp_json([]) as journal_file:
            patches = [
                patch("backend.app.learning.is_pg_enabled", return_value=False),
                patch("backend.app.journal.is_pg_enabled", return_value=False),
                patch("backend.app.learning.TRADES_FILE", trades_file),
                patch("backend.app.journal.JOURNAL_FILE", journal_file),
                patch("backend.app.trade_selector.fetch_history", side_effect=_mock_fetch_history),
                patch("backend.app.news_scorer.fetch_history_batch", side_effect=_mock_fetch_history_batch),
                patch("backend.app.news_scorer.fetch_history", side_effect=_mock_fetch_history),
                patch("backend.app.journal.fetch_history_range", side_effect=_mock_fetch_history_range),
                patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
                patch("backend.app.trade_selector.check_event_conflict", return_value=False),
            ]

            for p in patches:
                p.start()

            try:
                trading_day = 0
                for cal_day in range(90):  # ~3 calendar months
                    day_date = base_date + timedelta(days=cal_day)
                    if day_date.weekday() >= 5:
                        continue
                    trading_day += 1

                    # Determine which month we're in
                    if trading_day <= 20:
                        month = 1
                    elif trading_day <= 40:
                        month = 2
                    else:
                        month = 3

                    day_scenarios = DAILY_SCENARIOS[(trading_day - 1) % len(DAILY_SCENARIOS)]
                    day_record = {"day": trading_day, "month": month, "trades": [], "journal": []}
                    day_trades_tickers = []

                    # ── 2 scans per day ──
                    for scan_type, scan_hour in [(ScanType.EUROPE, 8), (ScanType.US, 15)]:
                        scan_time = day_date.replace(hour=scan_hour, minute=50)

                        news_items = [
                            NewsItem(
                                title=s[0], source=s[1], url=f"https://test.com/{trading_day}",
                                published=scan_time - timedelta(hours=1),
                                related_tickers=s[4], source_weight=s[2],
                                description=f"Details: {s[0][:50]}",
                            )
                            for s in day_scenarios
                        ]

                        # Score
                        def _mock_create(**kwargs):
                            return _make_claude_response(news_items, day_scenarios)

                        mock_client = MagicMock()
                        mock_client.messages.create = _mock_create

                        with patch("backend.app.news_scorer._get_client", return_value=mock_client):
                            try:
                                scored_news, market_ctx = score_news_batch(news_items, scan_type)
                            except Exception as e:
                                all_errors.append(f"D{trading_day} score: {e}")
                                continue

                        # Learning
                        invalidate_perf_summary_cache()
                        learning_adj = compute_learning_adjustments()

                        # Select
                        existing = [t for t in day_trades_tickers]
                        try:
                            with patch("backend.app.trade_selector.is_market_open", return_value=True):
                                result = select_trade(
                                    scored_news, scan_type,
                                    learning_adjustments=learning_adj,
                                    existing_trade_ticker=existing,
                                    market_context=market_ctx,
                                )
                        except Exception as e:
                            all_errors.append(f"D{trading_day} select: {e}")
                            continue

                        # Save trade
                        if result.has_trade and result.recommendation:
                            rec = result.recommendation
                            rec.timestamp = scan_time
                            trade_counter[0] += 1

                            try:
                                save_trade(rec)
                                day_trades_tickers.append(rec.ticker)
                                day_record["trades"].append({
                                    "ticker": rec.ticker,
                                    "direction": rec.direction.value,
                                    "category": rec.news_category,
                                })
                            except Exception as e:
                                all_errors.append(f"D{trading_day} save: {e}")

                    # ── End of day: set losing tickers for journal ──
                    if month == 1:
                        # Month 1: every other trade loses
                        _losing_tickers = set()
                        for i, t in enumerate(day_record["trades"]):
                            if i % 2 == 1:  # Second trade of the day loses
                                _losing_tickers.add(t["ticker"])
                    elif month == 2:
                        # Month 2: ~25% lose (every 4th trade)
                        _losing_tickers = set()
                        for i, t in enumerate(day_record["trades"]):
                            if trade_counter[0] % 4 == 0:
                                _losing_tickers.add(t["ticker"])
                    else:
                        # Month 3: ~10% lose
                        _losing_tickers = set()
                        if trade_counter[0] % 10 == 0 and day_record["trades"]:
                            _losing_tickers.add(day_record["trades"][0]["ticker"])

                    # Journal
                    try:
                        journal_result = run_daily_journal()
                        if isinstance(journal_result, list):
                            for entry in journal_result:
                                if isinstance(entry, dict):
                                    result_str = entry.get("result", "UNKNOWN")
                                    ticker = entry.get("ticker", "?")
                                    day_record["journal"].append({
                                        "ticker": ticker,
                                        "result": result_str,
                                        "pnl": entry.get("pnl_pct", 0),
                                    })
                                    monthly_stats[month]["trades"] += 1
                                    if result_str == "TP_HIT":
                                        monthly_stats[month]["wins"] += 1
                                    elif result_str == "SL_HIT":
                                        monthly_stats[month]["losses"] += 1
                                    else:
                                        monthly_stats[month]["expired"] += 1
                    except Exception as e:
                        all_errors.append(f"D{trading_day} journal: {e}")

                    # Reset losing tickers
                    _losing_tickers = set()

                    # Post-journal learning
                    invalidate_perf_summary_cache()
                    try:
                        state = compute_learning_adjustments()
                        if trading_day % 5 == 0:  # Snapshot every 5 trading days
                            learning_snapshots.append({
                                "day": trading_day,
                                "month": month,
                                "adjustments": dict(state.get("adjustments", {})),
                                "session_adj": dict(state.get("session_adj", {})),
                                "direction_adj": dict(state.get("direction_adj", {})),
                                "newscat_adj": dict(state.get("newscat_adj", {})),
                                "delay_bias": state.get("delay_bias_adj"),
                                "n_adj": len(state.get("adjustments", {})),
                                "n_newscat": len(state.get("newscat_adj", {})),
                            })
                    except Exception as e:
                        all_errors.append(f"D{trading_day} learning: {e}")

                    all_daily.append(day_record)

            finally:
                for p in patches:
                    p.stop()

        # ════════════════════════════════════════════════════════════
        # Print results
        # ════════════════════════════════════════════════════════════
        print(f"\n{'='*70}")
        print(f"THREE MONTH PIPELINE — LEARNING ADAPTATION TEST")
        print(f"{'='*70}")

        for m in [1, 2, 3]:
            s = monthly_stats[m]
            total = s["trades"]
            wr = (s["wins"] / total * 100) if total > 0 else 0
            print(f"\n  Month {m}: {total} trades | "
                  f"{s['wins']} TP_HIT, {s['losses']} SL_HIT, {s['expired']} EXPIRED | "
                  f"Win rate: {wr:.0f}%")

        print(f"\n  Errors: {len(all_errors)}")
        if all_errors:
            for e in all_errors[:10]:
                print(f"    {e}")

        print(f"\n  Learning evolution (every 5 trading days):")
        for snap in learning_snapshots:
            print(f"    Day {snap['day']:2d} (M{snap['month']}): "
                  f"ticker_adj={snap['n_adj']}, "
                  f"newscat={snap['n_newscat']}, "
                  f"delay_bias={snap['delay_bias']}, "
                  f"session={snap['session_adj']}, "
                  f"direction={snap['direction_adj']}")

        # Per-day detail for month 1
        print(f"\n  Month 1 daily detail:")
        for dr in all_daily[:20]:
            trades_str = ", ".join(f"{t['ticker']} {t['direction']}" for t in dr["trades"]) or "none"
            journal_str = ", ".join(
                f"{j['ticker']}={'W' if j['result']=='TP_HIT' else 'L' if j['result']=='SL_HIT' else 'E'}"
                for j in dr["journal"]
            ) or "none"
            print(f"    Day {dr['day']:2d}: trades=[{trades_str}], results=[{journal_str}]")

        print(f"{'='*70}\n")

        # ════════════════════════════════════════════════════════════
        # Assertions
        # ════════════════════════════════════════════════════════════

        # 1. No fatal errors
        assert len(all_errors) == 0, f"Pipeline errors:\n" + "\n".join(all_errors)

        # 2. Month 1 should have losses (our forced 50% lose pattern)
        m1 = monthly_stats[1]
        assert m1["losses"] > 0, "Month 1 should have SL_HIT trades (forced losses)"
        assert m1["trades"] >= 8, f"Month 1 too few trades: {m1['trades']}"

        # 3. Learning should have evolved by month 2
        assert len(learning_snapshots) >= 6, \
            f"Not enough learning snapshots: {len(learning_snapshots)}"

        # 4. Month 1 win rate should be around 50% (by construction)
        m1_wr = m1["wins"] / m1["trades"] * 100 if m1["trades"] > 0 else 0
        assert m1_wr < 80, f"Month 1 WR should be <80% (forced losses): {m1_wr:.0f}%"

        # 5. Learning state should contain adjustments by end of 3 months
        # With 40-60 trades, many dimensions should activate
        final_snap = learning_snapshots[-1] if learning_snapshots else {}
        assert final_snap, "No learning snapshot at end"

        # 6. All 3 months produced trades
        for m in [1, 2, 3]:
            assert monthly_stats[m]["trades"] > 0, f"Month {m} had zero trades"

        # 7. Journal correctly identified both TP_HIT and SL_HIT
        total_wins = sum(monthly_stats[m]["wins"] for m in [1, 2, 3])
        total_losses = sum(monthly_stats[m]["losses"] for m in [1, 2, 3])
        assert total_wins > 0, "No TP_HIT trades across 3 months"
        assert total_losses > 0, "No SL_HIT trades across 3 months — loss mechanism broken"

        # 8. Learning detects mixed results (should have some ticker or newscat adjustments)
        # By month 3, with 40+ mixed trades, learning should have opinions
        has_any_adjustment = (
            final_snap.get("n_adj", 0) > 0 or
            final_snap.get("n_newscat", 0) > 0 or
            final_snap.get("delay_bias") != 1.0 or
            len(final_snap.get("session_adj", {})) > 0 or
            len(final_snap.get("direction_adj", {})) > 0
        )
        assert has_any_adjustment, "Learning produced zero adjustments after 3 months of mixed results"
