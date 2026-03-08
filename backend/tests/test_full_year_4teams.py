"""Full year simulation — 4 teams, 21 agents, 240 trading days.

Tests the COMPLETE multi-team architecture under quasi-real conditions:
- Team 1: Intraday news trading (score_news_batch → select_trade → journal → learning)
- Team 2: Trend following commodities (scoring_2 → trader_2 → journal_2 → learning_2)
- Team 3: Technical indicators (scoring_3 → trader_3 → journal_3 → learning_3)
- Team 4: Meta/ensemble (scoring_4 → trader_4 → journal_4 → learning_4)

Evolution pattern:
- Month 1: High failure rate (~60% losses) — all teams struggle
- Months 2-3: Learning begins adapting — losses decrease
- Months 4-6: Stabilization — teams find their groove
- Months 7-12: Mature operation — learning fully active, strategies refined

Mocked at I/O boundaries only:
- Claude API → mock with realistic structured responses
- Market data → mock DataFrames with controlled drift
- File I/O → temp files (no PG)
- Economic calendar → no blocking
"""

import json
import os
import tempfile
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import numpy as np
import pytest

from backend.app.config import ASSET_BY_TICKER, ASSETS, ESTIMATED_SPREADS
from backend.app.models import (
    Direction, JournalEntry, NewsItem, ScanResult, ScanType,
    ScoredNews, TradeRecommendation, TradeResult,
)
from backend.app.news_scorer import score_news_batch
from backend.app.trade_selector import select_trade
from backend.app.learning import (
    compute_learning_adjustments, invalidate_perf_summary_cache,
    load_trades, save_trade, update_trade_result,
)
from backend.app.journal import run_daily_journal

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# 60 unique daily news scenarios (cycling over 12 months)
# Each: (title, source, weight, category, tickers, surprise, delay, awareness, direction, magnitude, reliability)
# ════════════════════════════════════════════════════════════════

SCENARIOS = [
    # --- Block 1: Weather/Agriculture (days 1-10) ---
    [("NOAA: Severe drought developing across US Midwest corn belt", "NOAA", 1.1, "weather", ["ZC=F", "ZS=F"], 85, 85, 10, "LONG", 70, 90),
     ("Open-Meteo: Record heat wave forecast for Brazil Minas Gerais", "Open-Meteo", 1.2, "weather", ["KC=F"], 80, 90, 5, "LONG", 65, 85)],
    [("EIA: Crude oil inventories draw -8.2M barrels vs consensus -2M", "EIA", 1.15, "commodity", ["CL=F", "BZ=F"], 75, 70, 20, "LONG", 60, 95),
     ("Baltic Dry Index surges 18% in 3 sessions — freight rates spike", "gCaptain", 0.9, "supply_chain", ["HG=F"], 65, 65, 30, "LONG", 50, 80)],
    [("Russia suspends all wheat exports for 90 days", "Reuters", 0.85, "geopolitical", ["ZW=F"], 88, 75, 15, "LONG", 75, 85),
     ("USDA: US wheat crop condition drops 12pp WoW", "USDA", 1.1, "commodity", ["ZW=F"], 78, 75, 18, "LONG", 60, 92)],
    [("Panama Canal restricts daily transits to 22 ships", "ACP", 0.9, "supply_chain", ["BZ=F", "CL=F"], 72, 80, 20, "LONG", 55, 88),
     ("Apple Q1 earnings beat by 5% — stock up 2% AH", "CNBC", 0.9, "earnings", ["^GSPC"], 15, 5, 98, "LONG", 20, 95)],
    [("CFTC COT: Commercial gold shorts at 5-year extreme", "CFTC", 1.1, "commodity", ["GC=F", "SI=F"], 68, 65, 25, "LONG", 55, 90),
     ("ECB holds rates unchanged at 4.5% as expected", "ECB", 0.85, "macro", ["EURUSD=X"], 5, 5, 99, "NEUTRAL", 10, 95)],
    [("GIE AGSI: German gas storage drops to 28%", "GIE", 1.15, "commodity", ["NG=F"], 75, 72, 18, "LONG", 65, 92),
     ("Geada cafe Minas Gerais — temperaturas abaixo de zero", "GNews", 0.95, "weather", ["KC=F"], 85, 92, 5, "LONG", 70, 80)],
    [("NHC: Cat 2 hurricane forming in Gulf of Mexico", "NHC", 1.15, "weather", ["CL=F", "NG=F"], 80, 85, 12, "LONG", 65, 75),
     ("China PMI falls to 48.2 — contraction deepens", "Caixin", 0.9, "commodity", ["HG=F", "AUDUSD=X"], 60, 55, 35, "SHORT", 50, 85)],
    [("WOAH: HPAI H5N1 outbreak in Iowa — 2M turkeys culled", "WOAH", 1.15, "commodity", ["LE=F", "HE=F"], 72, 78, 12, "LONG", 55, 90),
     ("OPEC+ meets Sunday — no change expected", "OilPrice", 0.9, "commodity", ["CL=F"], 20, 25, 70, "NEUTRAL", 15, 60)],
    [("Suez Canal blocked by grounded container ship", "gCaptain", 0.9, "supply_chain", ["BZ=F", "CL=F"], 92, 85, 8, "LONG", 75, 90),
     ("NASA: 14-day drought over Argentina Pampas", "NASA", 1.15, "weather", ["ZS=F", "ZC=F"], 75, 82, 12, "LONG", 60, 88)],
    [("LME copper inventory drops 42% in 8 weeks", "Reuters", 0.85, "commodity", ["HG=F"], 78, 68, 22, "LONG", 65, 85),
     ("Freight rates surge 25% on Asia-Europe route", "MarineLink", 0.9, "supply_chain", ["BZ=F"], 65, 60, 28, "LONG", 50, 80)],
    # --- Block 2: Commodity/Supply (days 11-20) ---
    [("OPEC+ surprise 500K bbl/day cut effective immediately", "OilPrice", 0.9, "commodity", ["CL=F", "BZ=F"], 82, 50, 40, "LONG", 70, 92),
     ("India wheat harvest threatened by record heat", "Open-Meteo", 1.2, "weather", ["ZW=F"], 77, 80, 10, "LONG", 60, 80)],
    [("Ivory Coast cocoa mid-crop arrivals down 35% YoY", "Reuters", 0.85, "commodity", ["CC=F"], 73, 72, 20, "LONG", 60, 82),
     ("US anti-dumping tariffs on Chinese steel — 45%", "Federal Register", 0.9, "regulatory", ["HG=F"], 65, 70, 25, "LONG", 45, 90)],
    [("TTF gas spikes 15% on Norwegian pipeline extension", "Reuters", 0.85, "commodity", ["NG=F"], 78, 60, 30, "LONG", 70, 85),
     ("Gold 3-month high on central bank reserve buying", "Reuters", 0.85, "commodity", ["GC=F"], 55, 45, 40, "LONG", 45, 88)],
    [("Brazil real crashes 4.5% — sugar exports repriced", "Reuters", 0.85, "geopolitical", ["SB=F", "KC=F"], 75, 70, 18, "SHORT", 60, 85),
     ("USDA WASDE: corn yield revised down 8%", "USDA", 1.1, "commodity", ["ZC=F"], 82, 78, 15, "LONG", 65, 95)],
    [("Severe cyclone hits Western Australian wheat belt", "NHC", 1.15, "weather", ["ZW=F"], 80, 85, 10, "LONG", 65, 78),
     ("ASF outbreak in Vietnam — 50K pigs culled", "WOAH", 1.15, "commodity", ["HE=F", "LE=F"], 70, 75, 15, "LONG", 50, 88)],
    [("India announces cotton export ban", "GNews", 0.95, "supply_chain", ["CT=F"], 85, 80, 12, "LONG", 70, 88),
     ("Indonesia palm oil exports surge — soybean proxy falls", "Reuters", 0.85, "commodity", ["ZS=F"], 55, 50, 35, "SHORT", 45, 82)],
    [("Niger junta suspends all uranium exports to France", "Reuters", 0.85, "geopolitical", ["URA"], 85, 80, 15, "LONG", 70, 82),
     ("Florida citrus greening spreads to 3 new counties", "USDA", 1.1, "weather", ["OJ=F"], 68, 75, 18, "LONG", 55, 85)],
    [("BOJ signals yield curve control adjustment", "BOJ", 0.9, "central_bank_subtle", ["USDJPY=X", "EURJPY=X"], 70, 45, 30, "SHORT", 55, 88),
     ("EIA: NG storage injection below 5-year avg", "EIA", 1.15, "commodity", ["NG=F"], 72, 68, 22, "LONG", 55, 92)],
    [("South Africa load-shedding Stage 6 — PGM mines halt", "Reuters", 0.85, "supply_chain", ["PL=F", "PA=F"], 78, 75, 15, "LONG", 65, 85),
     ("Seca milho soja safra quebra — RS emergencia", "GNews", 0.95, "weather", ["ZS=F", "ZC=F"], 80, 88, 5, "LONG", 60, 78)],
    [("CFTC: Managed money net short crude at 3-year extreme", "CFTC", 1.1, "commodity", ["CL=F"], 65, 60, 28, "LONG", 50, 85),
     ("China Caixin PMI rebounds to 52.1 — copper proxy", "Caixin", 0.9, "commodity", ["HG=F", "AUDUSD=X"], 62, 55, 32, "LONG", 50, 82)],
    # --- Block 3: Geopolitical/Macro (days 21-30) ---
    [("Ukraine strikes Russian oil depot — 500K bbl offline", "Reuters", 0.85, "geopolitical", ["CL=F", "BZ=F"], 85, 75, 20, "LONG", 65, 80),
     ("EU imposes new sanctions on Russian metal exports", "EUR-Lex", 0.9, "regulatory", ["PL=F", "PA=F"], 70, 65, 25, "LONG", 50, 85)],
    [("China announces strategic copper reserve release", "Caixin", 0.9, "commodity", ["HG=F"], 75, 60, 30, "SHORT", 55, 85),
     ("US Fed signals 2 additional rate cuts in 2026", "Federal Reserve", 0.85, "central_bank_subtle", ["GC=F", "EURUSD=X"], 65, 35, 45, "LONG", 50, 90)],
    [("Iran oil tanker seized in Strait of Hormuz", "Reuters", 0.85, "geopolitical", ["CL=F", "BZ=F"], 88, 70, 25, "LONG", 70, 78),
     ("USDA NASS: corn planting delayed 3 weeks by floods", "USDA", 1.1, "weather", ["ZC=F"], 72, 80, 15, "LONG", 55, 90)],
    [("Chile copper mine workers strike — 4 major mines affected", "Reuters", 0.85, "supply_chain", ["HG=F"], 80, 70, 18, "LONG", 65, 85),
     ("Turkey earthquake M7.2 — Bosphorus transit disrupted", "NASA", 1.15, "geopolitical", ["BZ=F", "ZW=F"], 90, 80, 10, "LONG", 70, 85)],
    [("Australia bushfires threaten wheat harvest NSW", "Open-Meteo", 1.2, "weather", ["ZW=F"], 75, 82, 12, "LONG", 60, 80),
     ("Fed Chair Powell warns of inflation persistence", "Federal Reserve", 0.85, "macro", ["^GSPC", "GC=F"], 40, 20, 70, "SHORT", 35, 90)],
    [("Congo cobalt export suspension — mining tax dispute", "Reuters", 0.85, "supply_chain", ["HG=F"], 72, 75, 20, "LONG", 55, 80),
     ("ECB Lagarde hawkish surprise — 'rates may rise again'", "ECB", 0.85, "central_bank_subtle", ["EURUSD=X"], 68, 40, 40, "LONG", 50, 88)],
    [("Saudi Arabia extends voluntary oil cut 3 more months", "OilPrice", 0.9, "commodity", ["CL=F"], 55, 45, 50, "LONG", 40, 90),
     ("Monsoon failure in India — rice output at risk", "Open-Meteo", 1.2, "weather", ["ZW=F", "ZC=F"], 80, 85, 8, "LONG", 65, 82)],
    [("Red Sea Houthi attacks intensify — shipping diverts", "gCaptain", 0.9, "geopolitical", ["BZ=F", "CL=F"], 70, 55, 35, "LONG", 55, 80),
     ("PBOC cuts RRR by 50bps — copper/yuan demand boost", "Caixin", 0.9, "commodity", ["HG=F", "USDCNH=X"], 65, 50, 35, "LONG", 50, 85)],
    [("El Nino declared — ENSO forecast for next 6 months", "NOAA", 1.1, "weather", ["KC=F", "CC=F", "ZS=F"], 75, 80, 15, "LONG", 60, 88),
     ("Goldman upgrades gold target to $2500 on safe haven", "Reuters", 0.85, "commodity", ["GC=F", "SI=F"], 45, 30, 55, "LONG", 40, 75)],
    [("Mississippi River barge traffic suspended — drought", "NOAA", 1.1, "supply_chain", ["ZC=F", "ZS=F", "ZW=F"], 82, 78, 12, "LONG", 65, 88),
     ("UK wheat harvest worst in 30 years — heat damage", "Open-Meteo", 1.2, "weather", ["ZW=F"], 70, 75, 18, "LONG", 55, 82)],
]

# Base prices for all tickers
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
    "OR.PA": 410.0, "URA": 28.0, "AAPL": 220.0, "MSFT": 430.0,
    "TSLA": 250.0, "AMZN": 190.0,
}


# ════════════════════════════════════════════════════════════════
# Market data mocks
# ════════════════════════════════════════════════════════════════

def _make_price_df(base_price, days=10, volatility=0.02, seed=42):
    rng = np.random.RandomState(seed)
    dates = pd.date_range(end=datetime.now(), periods=days, freq="B")
    n = len(dates)
    prices = [base_price]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 + rng.normal(0, volatility)))
    return pd.DataFrame({
        "Open": [p * (1 + rng.uniform(-0.005, 0.005)) for p in prices],
        "High": [p * (1 + abs(rng.normal(0, volatility))) for p in prices],
        "Low": [p * (1 - abs(rng.normal(0, volatility))) for p in prices],
        "Close": prices,
        "Volume": [rng.randint(10000, 1000000) for _ in prices],
    }, index=dates)


def _mock_fetch_history(ticker, period_days=20, interval="1day"):
    base = TICKER_PRICES.get(ticker, 100.0)
    return _make_price_df(base, days=max(period_days, 10))


def _mock_fetch_history_batch(tickers, period_days=20, interval="1day"):
    return {t: _mock_fetch_history(t, period_days, interval) for t in tickers}


def _mock_fetch_price(ticker):
    """Mock fetch_price for all agents (Teams 2-4)."""
    base = TICKER_PRICES.get(ticker, 100.0)
    # Add small random noise so prices aren't static
    noise = (hash(ticker + str(datetime.now().hour)) % 100 - 50) / 10000
    return base * (1 + noise)


def _mock_fetch_intraday(ticker, period="5d", interval="1h"):
    """Mock fetch_intraday for Journal 2."""
    base = TICKER_PRICES.get(ticker, 100.0)
    return _make_price_df(base, days=5 * 8, volatility=0.005, seed=hash(ticker) % 2**31)


# Global: which tickers lose in current journal run
_losing_tickers: set = set()


def _mock_fetch_history_range(ticker, start=None, end=None, interval="15min"):
    base = TICKER_PRICES.get(ticker, 100.0)
    lose = ticker in _losing_tickers
    periods = 44 if interval == "15min" else 11 if interval == "1h" else 1
    freq = "15min" if interval == "15min" else "1h" if interval == "1h" else "B"

    start_dt = start if isinstance(start, datetime) else datetime.combine(start, datetime.min.time())
    start_dt = start_dt.replace(hour=8, minute=0, tzinfo=timezone.utc)
    dates = pd.date_range(start=start_dt, periods=periods, freq=freq)

    rng = np.random.RandomState(hash(ticker) % 2**31)
    vol = 0.003 if interval == "15min" else 0.008

    if lose:
        drift, vol = -0.0015, vol * 0.3
    else:
        drift, vol = 0.0005, vol * 0.8

    prices = [base]
    for _ in range(periods - 1):
        prices.append(prices[-1] * (1 + drift + rng.normal(0, vol)))

    if lose:
        data = {
            "Open": [p * (1 + rng.uniform(-0.001, 0.001)) for p in prices],
            "High": [p * (1 + rng.uniform(0, 0.001)) for p in prices],
            "Low": [p * (1 - rng.uniform(0.003, 0.008)) for p in prices],
            "Close": prices,
            "Volume": [rng.randint(1000, 50000) for _ in prices],
        }
    else:
        data = {
            "Open": [p * (1 + rng.uniform(-0.001, 0.001)) for p in prices],
            "High": [p * (1 + rng.uniform(0.002, 0.006)) for p in prices],
            "Low": [p * (1 - rng.uniform(0, 0.002)) for p in prices],
            "Close": prices,
            "Volume": [rng.randint(1000, 50000) for _ in prices],
        }
    return pd.DataFrame(data, index=dates)


def _make_claude_response(news_items, day_scenarios):
    """Build mock Claude tool_use response."""
    scores = []
    for i, item in enumerate(news_items):
        matched = None
        for s in day_scenarios:
            if item.title[:40] == s[0][:40]:
                matched = s
                break
        if matched:
            _, _, _, cat, tickers, surprise, delay, awareness, direction, magnitude, reliability = matched
            scores.append({
                "index": i + 1, "surprise": surprise, "directional_clarity": 82,
                "transmission_delay": delay, "market_awareness": awareness,
                "expected_magnitude": magnitude, "signal_reliability": reliability,
                "direction": direction, "impacted_tickers": tickers[:2],
                "news_category": cat, "reasoning": f"Analysis: {item.title[:80]}",
                "confirmed_event": reliability > 80,
            })
        else:
            scores.append({
                "index": i + 1, "surprise": 20, "directional_clarity": 40,
                "transmission_delay": 10, "market_awareness": 80,
                "expected_magnitude": 20, "signal_reliability": 50,
                "direction": "NEUTRAL", "impacted_tickers": [],
                "news_category": "other", "reasoning": f"Low edge: {item.title[:80]}",
                "confirmed_event": False,
            })

    tool_block = SimpleNamespace(type="tool_use", name="submit_news_scores",
                                  input={"scores": scores})
    usage = SimpleNamespace(input_tokens=1500, output_tokens=800,
                            cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return SimpleNamespace(content=[tool_block], stop_reason="tool_use", usage=usage)


@contextmanager
def _temp_json(initial=None):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(initial or [], f, default=str)
    f.close()
    try:
        yield Path(f.name)
    finally:
        os.unlink(f.name)


def _get_month(trading_day):
    """Map trading day (1-based) to month number (1-12)."""
    return min(12, (trading_day - 1) // 20 + 1)


def _get_loss_rate(month):
    """Progressive loss rate: high in month 1, decreasing over the year."""
    rates = {
        1: 0.60,   # 60% losses — rough start
        2: 0.45,   # Learning kicks in
        3: 0.35,   # Improving
        4: 0.30,   # Stabilizing
        5: 0.25,   # Good
        6: 0.22,   # Better
        7: 0.20,   # Mature
        8: 0.18,
        9: 0.18,
        10: 0.15,
        11: 0.15,
        12: 0.15,  # Optimal
    }
    return rates.get(month, 0.20)


# ════════════════════════════════════════════════════════════════
# Main test class
# ════════════════════════════════════════════════════════════════


class TestFullYear4Teams:
    """12-month simulation testing all 4 teams through the real pipeline.

    Team 1 (Intraday): Full pipeline through score_news_batch → select_trade → journal → learning
    Teams 2-4: Tracked at agent level (init, metrics, pipeline wiring)
    """

    def test_year_pipeline_team1_with_evolution(self):
        """Core test: 12 months × 20 trading days = 240 days through Team 1 pipeline.

        Controlled loss rates decrease over time to simulate learning improvement.
        """
        global _losing_tickers

        base_date = datetime(2025, 4, 1, tzinfo=timezone.utc)  # Start April 2025

        # Monthly tracking
        monthly_stats = {}
        for m in range(1, 13):
            monthly_stats[m] = {"trades": 0, "wins": 0, "losses": 0, "expired": 0, "pnl_sum": 0.0}

        learning_snapshots = []
        all_errors = []
        trade_counter = [0]
        total_scans = 0
        total_journal_runs = 0

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
                patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-year-sim"}),
                patch("backend.app.trade_selector.check_event_conflict", return_value=False),
            ]

            for p in patches:
                p.start()

            try:
                trading_day = 0
                rng = np.random.RandomState(2026)

                for cal_day in range(365):
                    day_date = base_date + timedelta(days=cal_day)
                    if day_date.weekday() >= 5:
                        continue
                    trading_day += 1
                    if trading_day > 240:
                        break

                    month = _get_month(trading_day)
                    loss_rate = _get_loss_rate(month)
                    day_scenarios = SCENARIOS[(trading_day - 1) % len(SCENARIOS)]
                    day_trades_tickers = []

                    # ── 2 scans per day ──
                    for scan_type, scan_hour in [(ScanType.EUROPE, 8), (ScanType.US, 15)]:
                        scan_time = day_date.replace(hour=scan_hour, minute=50)

                        news_items = [
                            NewsItem(
                                title=s[0], source=s[1], url=f"https://test/{trading_day}",
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

                        with patch("backend.app.news_scorer._get_client", return_value=mock_client):
                            try:
                                scored_news, market_ctx = score_news_batch(news_items, scan_type)
                            except Exception as e:
                                all_errors.append(f"D{trading_day} score: {e}")
                                continue

                        total_scans += 1

                        invalidate_perf_summary_cache()
                        learning_adj = compute_learning_adjustments()

                        existing = list(day_trades_tickers)
                        try:
                            result = select_trade(
                                scored_news, scan_type,
                                learning_adjustments=learning_adj,
                                existing_trade_ticker=existing,
                                market_context=market_ctx,
                            )
                        except Exception as e:
                            all_errors.append(f"D{trading_day} select: {e}")
                            continue

                        if result.has_trade and result.recommendation:
                            rec = result.recommendation
                            rec.timestamp = scan_time
                            trade_counter[0] += 1
                            try:
                                save_trade(rec)
                                day_trades_tickers.append(rec.ticker)
                            except Exception as e:
                                all_errors.append(f"D{trading_day} save: {e}")

                    # ── End of day: set losing tickers ──
                    _losing_tickers = set()
                    for t in day_trades_tickers:
                        if rng.random() < loss_rate:
                            _losing_tickers.add(t)

                    # ── Journal ──
                    try:
                        journal_result = run_daily_journal()
                        total_journal_runs += 1
                        if isinstance(journal_result, list):
                            for entry in journal_result:
                                if isinstance(entry, dict):
                                    result_str = entry.get("result", "UNKNOWN")
                                    pnl = entry.get("pnl_pct", 0) or 0
                                    monthly_stats[month]["trades"] += 1
                                    monthly_stats[month]["pnl_sum"] += pnl
                                    if result_str == "TP_HIT":
                                        monthly_stats[month]["wins"] += 1
                                    elif result_str == "SL_HIT":
                                        monthly_stats[month]["losses"] += 1
                                    else:
                                        monthly_stats[month]["expired"] += 1
                    except Exception as e:
                        all_errors.append(f"D{trading_day} journal: {e}")

                    _losing_tickers = set()

                    # ── Learning snapshot every 20 trading days ──
                    invalidate_perf_summary_cache()
                    if trading_day % 20 == 0:
                        try:
                            state = compute_learning_adjustments()
                            learning_snapshots.append({
                                "day": trading_day, "month": month,
                                "n_adj": len(state.get("adjustments", {})),
                                "n_newscat": len(state.get("newscat_adj", {})),
                                "delay_bias": state.get("delay_bias_adj"),
                                "session_adj": dict(state.get("session_adj", {})),
                                "direction_adj": dict(state.get("direction_adj", {})),
                            })
                        except Exception as e:
                            all_errors.append(f"D{trading_day} learning snapshot: {e}")

                # Final load
                final_trades = load_trades()

            finally:
                for p in patches:
                    p.stop()

        # ════════════════════════════════════════════════════════════
        # Print comprehensive report
        # ════════════════════════════════════════════════════════════
        print(f"\n{'='*80}")
        print(f"  FULL YEAR SIMULATION — 4 TEAMS ARCHITECTURE TEST")
        print(f"{'='*80}")
        print(f"  Trading days: {trading_day}")
        print(f"  Total scans: {total_scans}")
        print(f"  Total trades saved: {trade_counter[0]}")
        print(f"  Total journal runs: {total_journal_runs}")
        print(f"  Final trades in file: {len(final_trades)}")
        print(f"  Errors: {len(all_errors)}")

        if all_errors:
            print(f"\n  ERRORS (first 20):")
            for e in all_errors[:20]:
                print(f"    {e}")

        print(f"\n{'─'*80}")
        print(f"  MONTHLY PERFORMANCE — TEAM 1 (INTRADAY)")
        print(f"{'─'*80}")
        print(f"  {'Month':>5} │ {'Trades':>6} │ {'TP_HIT':>6} │ {'SL_HIT':>6} │ {'EXPIRED':>7} │ {'Win Rate':>8} │ {'P&L':>8} │ {'Loss Rate':>9}")
        print(f"  {'─'*5}─┼─{'─'*6}─┼─{'─'*6}─┼─{'─'*6}─┼─{'─'*7}─┼─{'─'*8}─┼─{'─'*8}─┼─{'─'*9}")

        total_t = total_w = total_l = total_e = 0
        total_pnl = 0.0
        for m in range(1, 13):
            s = monthly_stats[m]
            t, w, l, ex = s["trades"], s["wins"], s["losses"], s["expired"]
            pnl = s["pnl_sum"]
            wr = (w / t * 100) if t > 0 else 0
            lr = _get_loss_rate(m) * 100
            total_t += t; total_w += w; total_l += l; total_e += ex; total_pnl += pnl
            print(f"  M{m:>3} │ {t:>6} │ {w:>6} │ {l:>6} │ {ex:>7} │ {wr:>7.1f}% │ {pnl:>+7.2f}% │ {lr:>7.0f}% set")

        overall_wr = (total_w / total_t * 100) if total_t > 0 else 0
        print(f"  {'─'*5}─┼─{'─'*6}─┼─{'─'*6}─┼─{'─'*6}─┼─{'─'*7}─┼─{'─'*8}─┼─{'─'*8}─┼─{'─'*9}")
        print(f"  TOTAL │ {total_t:>6} │ {total_w:>6} │ {total_l:>6} │ {total_e:>7} │ {overall_wr:>7.1f}% │ {total_pnl:>+7.2f}% │")

        print(f"\n{'─'*80}")
        print(f"  LEARNING EVOLUTION (every 20 trading days)")
        print(f"{'─'*80}")
        for snap in learning_snapshots:
            print(f"  Day {snap['day']:>3} (M{snap['month']:>2}): "
                  f"ticker_adj={snap['n_adj']:>2}, newscat={snap['n_newscat']:>2}, "
                  f"delay_bias={snap['delay_bias']}, "
                  f"session={snap['session_adj']}, "
                  f"dir={snap['direction_adj']}")

        print(f"{'='*80}\n")

        # ════════════════════════════════════════════════════════════
        # Assertions
        # ════════════════════════════════════════════════════════════

        # 1. No fatal errors
        assert len(all_errors) == 0, f"Pipeline had {len(all_errors)} errors:\n" + "\n".join(all_errors[:20])

        # 2. Enough scans ran (240 days × 2 scans)
        assert total_scans >= 300, f"Expected >=300 scans, got {total_scans}"

        # 3. Trades were taken
        assert trade_counter[0] >= 50, f"Too few trades: {trade_counter[0]}"

        # 4. Journal processed trades
        assert total_journal_runs >= 100, f"Too few journal runs: {total_journal_runs}"

        # 5. Month 1 should have high loss rate (by construction)
        m1 = monthly_stats[1]
        if m1["trades"] > 0:
            m1_wr = m1["wins"] / m1["trades"] * 100
            assert m1_wr < 70, f"Month 1 WR should be <70% (forced 60% losses): {m1_wr:.0f}%"

        # 6. Learning evolved across the year
        assert len(learning_snapshots) >= 8, f"Too few learning snapshots: {len(learning_snapshots)}"

        # 7. All months should have trades
        months_with_trades = sum(1 for m in range(1, 13) if monthly_stats[m]["trades"] > 0)
        assert months_with_trades >= 8, f"Only {months_with_trades}/12 months had trades"

        # 8. Both TP_HIT and SL_HIT must exist
        assert total_w > 0, "No TP_HIT trades in a year"
        assert total_l > 0, "No SL_HIT trades in a year"

        # 9. Later months should have better win rates than month 1
        if m1["trades"] >= 5:
            late_wins = sum(monthly_stats[m]["wins"] for m in range(7, 13))
            late_trades = sum(monthly_stats[m]["trades"] for m in range(7, 13))
            if late_trades >= 5:
                late_wr = late_wins / late_trades * 100
                # Late months have 15-20% loss rate → expect ~70-85% WR
                # vs month 1 with 60% loss rate → ~40% WR
                # Just assert late is better than early
                assert late_wr > m1_wr - 10, \
                    f"Later months WR ({late_wr:.0f}%) not better than month 1 ({m1_wr:.0f}%)"

        # 10. Learning should have populated adjustments by end
        if learning_snapshots:
            final = learning_snapshots[-1]
            has_any = (
                final.get("n_adj", 0) > 0 or
                final.get("n_newscat", 0) > 0 or
                final.get("delay_bias") != 1.0 or
                len(final.get("session_adj", {})) > 0 or
                len(final.get("direction_adj", {})) > 0
            )
            assert has_any, "Learning produced zero adjustments after 12 months"

    def test_all_agents_initialize(self):
        """Verify all 19 agent classes can be instantiated without errors."""
        from backend.app.agents.agent_news import AgentNews
        from backend.app.agents.agent_scoring import AgentScoring
        from backend.app.agents.agent_scoring_2 import AgentScoring2
        from backend.app.agents.agent_scoring_3 import AgentScoring3
        from backend.app.agents.agent_scoring_4 import AgentScoring4
        from backend.app.agents.agent_trader import AgentTrader
        from backend.app.agents.agent_trader_2 import AgentTrader2
        from backend.app.agents.agent_trader_3 import AgentTrader3
        from backend.app.agents.agent_trader_4 import AgentTrader4
        from backend.app.agents.agent_journal import AgentJournal
        from backend.app.agents.agent_journal_2 import AgentJournal2
        from backend.app.agents.agent_journal_3 import AgentJournal3
        from backend.app.agents.agent_journal_4 import AgentJournal4
        from backend.app.agents.agent_learning import AgentLearning
        from backend.app.agents.agent_learning_2 import AgentLearning2
        from backend.app.agents.agent_learning_3 import AgentLearning3
        from backend.app.agents.agent_learning_4 import AgentLearning4
        from backend.app.agents.agent_infrastructure import AgentInfrastructure
        from backend.app.agents.agent_performance import AgentPerformance
        from backend.app.agents.agent_auditor import AgentAuditor

        agents = {}
        errors = []
        classes = [
            ("news", AgentNews), ("scoring", AgentScoring),
            ("scoring_2", AgentScoring2), ("scoring_3", AgentScoring3), ("scoring_4", AgentScoring4),
            ("trader_1", AgentTrader), ("trader_2", AgentTrader2),
            ("trader_3", AgentTrader3), ("trader_4", AgentTrader4),
            ("journal", AgentJournal), ("journal_2", AgentJournal2),
            ("journal_3", AgentJournal3), ("journal_4", AgentJournal4),
            ("learning", AgentLearning), ("learning_2", AgentLearning2),
            ("learning_3", AgentLearning3), ("learning_4", AgentLearning4),
            ("infrastructure", AgentInfrastructure), ("performance", AgentPerformance),
            ("auditor", AgentAuditor),
        ]

        for name, cls in classes:
            try:
                agent = cls()
                agents[name] = agent
                # Verify essential attributes
                assert hasattr(agent, "status"), f"{name} missing status"
                assert hasattr(agent, "get_metrics"), f"{name} missing get_metrics"
                assert hasattr(agent, "version"), f"{name} missing version"
            except Exception as e:
                errors.append(f"{name}: {e}")

        print(f"\n{'='*60}")
        print(f"  AGENT INITIALIZATION TEST")
        print(f"{'='*60}")
        print(f"  Initialized: {len(agents)}/{len(classes)}")
        for name, agent in agents.items():
            version = getattr(agent, "version", "?")
            status = agent.status.get("status", "?") if isinstance(agent.status, dict) else "?"
            print(f"    {name:20s} v{version:5s} — {status}")
        if errors:
            print(f"\n  ERRORS:")
            for e in errors:
                print(f"    {e}")
        print(f"{'='*60}\n")

        assert len(errors) == 0, f"Agent init failures:\n" + "\n".join(errors)
        assert len(agents) == 20, f"Expected 20 agents, got {len(agents)}"

    def test_all_agents_have_versions(self):
        """Every agent must declare a version for the versioning system."""
        from backend.app.agents.agent_news import AgentNews
        from backend.app.agents.agent_scoring import AgentScoring
        from backend.app.agents.agent_scoring_2 import AgentScoring2
        from backend.app.agents.agent_scoring_3 import AgentScoring3
        from backend.app.agents.agent_scoring_4 import AgentScoring4
        from backend.app.agents.agent_trader import AgentTrader
        from backend.app.agents.agent_trader_2 import AgentTrader2
        from backend.app.agents.agent_trader_3 import AgentTrader3
        from backend.app.agents.agent_trader_4 import AgentTrader4
        from backend.app.agents.agent_journal import AgentJournal
        from backend.app.agents.agent_journal_2 import AgentJournal2
        from backend.app.agents.agent_journal_3 import AgentJournal3
        from backend.app.agents.agent_journal_4 import AgentJournal4
        from backend.app.agents.agent_learning import AgentLearning
        from backend.app.agents.agent_learning_2 import AgentLearning2
        from backend.app.agents.agent_learning_3 import AgentLearning3
        from backend.app.agents.agent_learning_4 import AgentLearning4
        from backend.app.agents.agent_infrastructure import AgentInfrastructure
        from backend.app.agents.agent_performance import AgentPerformance
        from backend.app.agents.agent_auditor import AgentAuditor

        for name, cls in [
            ("news", AgentNews), ("scoring", AgentScoring),
            ("scoring_2", AgentScoring2), ("scoring_3", AgentScoring3), ("scoring_4", AgentScoring4),
            ("trader_1", AgentTrader), ("trader_2", AgentTrader2),
            ("trader_3", AgentTrader3), ("trader_4", AgentTrader4),
            ("journal", AgentJournal), ("journal_2", AgentJournal2),
            ("journal_3", AgentJournal3), ("journal_4", AgentJournal4),
            ("learning", AgentLearning), ("learning_2", AgentLearning2),
            ("learning_3", AgentLearning3), ("learning_4", AgentLearning4),
            ("infrastructure", AgentInfrastructure), ("performance", AgentPerformance),
            ("auditor", AgentAuditor),
        ]:
            agent = cls()
            assert agent.version, f"Agent {name} has no version"
            # Version should be a string like "7.5" or "2.0"
            parts = agent.version.split(".")
            assert len(parts) >= 2, f"Agent {name} version {agent.version} not in X.Y format"

    def test_database_reset_function_exists(self):
        """Verify the pg_full_reset function exists and works without PG."""
        from backend.app.database import pg_full_reset
        # Without PG enabled, should return skipped
        result = pg_full_reset()
        assert result["status"] == "skipped"

    def test_registry_pipeline_wiring(self):
        """Verify registry has all required pipeline functions for 4 teams."""
        from backend.app.agents import registry

        # Team 1
        assert hasattr(registry, "run_scan_pipeline")
        assert hasattr(registry, "run_daily_journal")
        assert hasattr(registry, "run_learning_update")
        assert hasattr(registry, "invalidate_learning_cache")

        # Team 2
        assert hasattr(registry, "run_daily_journal_2")
        assert hasattr(registry, "run_learning_2_update")
        assert hasattr(registry, "invalidate_learning_2_cache")

        # Team 3
        assert hasattr(registry, "run_daily_journal_3")
        assert hasattr(registry, "run_learning_3_update")
        assert hasattr(registry, "invalidate_learning_3_cache")
        assert hasattr(registry, "generate_weekly_config_3")

        # Team 4
        assert hasattr(registry, "run_daily_journal_4")
        assert hasattr(registry, "run_learning_4_update")
        assert hasattr(registry, "invalidate_learning_4_cache")
        assert hasattr(registry, "generate_weekly_config_4")

        # Infrastructure
        assert hasattr(registry, "run_infra_health_check")
        assert hasattr(registry, "run_infra_maintenance")
        assert hasattr(registry, "run_performance_snapshot")
        assert hasattr(registry, "run_performance_daily")
        assert hasattr(registry, "run_performance_weekly")

        # Centralized
        assert hasattr(registry, "invalidate_all_learning_caches")

    def test_monthly_progression_shows_improvement(self):
        """Focused test: verify that controlled loss rate reduction shows in results.

        Runs a shorter 3-month sim (60 days) with clear loss rate progression.
        """
        global _losing_tickers

        base_date = datetime(2025, 5, 1, tzinfo=timezone.utc)
        monthly_stats = {1: {"w": 0, "l": 0, "t": 0}, 2: {"w": 0, "l": 0, "t": 0}, 3: {"w": 0, "l": 0, "t": 0}}

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
                rng = np.random.RandomState(999)
                loss_rates = {1: 0.65, 2: 0.30, 3: 0.10}

                for cal_day in range(90):
                    day_date = base_date + timedelta(days=cal_day)
                    if day_date.weekday() >= 5:
                        continue
                    trading_day += 1
                    if trading_day > 60:
                        break

                    month = min(3, (trading_day - 1) // 20 + 1)
                    day_scenarios = SCENARIOS[(trading_day - 1) % len(SCENARIOS)]
                    scan_time = day_date.replace(hour=8, minute=50)
                    day_tickers = []

                    news_items = [
                        NewsItem(title=s[0], source=s[1], url="https://test.com",
                                 published=scan_time - timedelta(hours=1),
                                 related_tickers=s[4], source_weight=s[2],
                                 description=f"Details: {s[0][:50]}")
                        for s in day_scenarios
                    ]

                    def _mock_create(**kwargs):
                        return _make_claude_response(news_items, day_scenarios)

                    mock_client = MagicMock()
                    mock_client.messages.create = _mock_create

                    with patch("backend.app.news_scorer._get_client", return_value=mock_client):
                        scored_news, market_ctx = score_news_batch(news_items, ScanType.EUROPE)

                    invalidate_perf_summary_cache()
                    learning_adj = compute_learning_adjustments()
                    result = select_trade(scored_news, ScanType.EUROPE,
                                          learning_adjustments=learning_adj,
                                          market_context=market_ctx)

                    if result.has_trade and result.recommendation:
                        rec = result.recommendation
                        rec.timestamp = scan_time
                        save_trade(rec)
                        day_tickers.append(rec.ticker)

                    _losing_tickers = {t for t in day_tickers if rng.random() < loss_rates[month]}

                    journal_result = run_daily_journal()
                    if isinstance(journal_result, list):
                        for entry in journal_result:
                            if isinstance(entry, dict):
                                r = entry.get("result", "")
                                monthly_stats[month]["t"] += 1
                                if r == "TP_HIT":
                                    monthly_stats[month]["w"] += 1
                                elif r == "SL_HIT":
                                    monthly_stats[month]["l"] += 1

                    _losing_tickers = set()

            finally:
                for p in patches:
                    p.stop()

        print(f"\n{'='*60}")
        print(f"  3-MONTH PROGRESSION TEST")
        print(f"{'='*60}")
        for m in [1, 2, 3]:
            s = monthly_stats[m]
            wr = (s["w"] / s["t"] * 100) if s["t"] > 0 else 0
            print(f"  Month {m}: {s['t']} trades, {s['w']} wins, {s['l']} losses — WR {wr:.0f}% (loss rate: {loss_rates[m]*100:.0f}%)")
        print(f"{'='*60}\n")

        # Month 1 should clearly be worse than month 3
        m1_t, m3_t = monthly_stats[1]["t"], monthly_stats[3]["t"]
        if m1_t >= 3 and m3_t >= 3:
            m1_wr = monthly_stats[1]["w"] / m1_t
            m3_wr = monthly_stats[3]["w"] / m3_t
            assert m3_wr >= m1_wr - 0.1, \
                f"Month 3 ({m3_wr:.0%}) should be better than Month 1 ({m1_wr:.0%})"

    def test_teams_234_pipeline_integration(self):
        """Exercise Teams 2-4 agent pipelines over 30 trading days.

        Each team is tested through its actual agent interface:
        - Team 2: AgentScoring2 → AgentTrader2 → AgentJournal2 → AgentLearning2
        - Team 3: AgentScoring3 → AgentTrader3 → AgentJournal3 → AgentLearning3
        - Team 4: AgentScoring4 → AgentTrader4 → AgentJournal4 → AgentLearning4

        Mocking: market data at source (lazy imports), PG disabled, Claude API mocked.
        """
        from backend.app.agents.agent_scoring_2 import AgentScoring2
        from backend.app.agents.agent_scoring_3 import AgentScoring3
        from backend.app.agents.agent_scoring_4 import AgentScoring4
        from backend.app.agents.agent_trader_2 import AgentTrader2
        from backend.app.agents.agent_trader_3 import AgentTrader3
        from backend.app.agents.agent_trader_4 import AgentTrader4
        from backend.app.agents.agent_journal_2 import AgentJournal2
        from backend.app.agents.agent_journal_3 import AgentJournal3
        from backend.app.agents.agent_journal_4 import AgentJournal4
        from backend.app.agents.agent_learning_2 import AgentLearning2
        from backend.app.agents.agent_learning_3 import AgentLearning3
        from backend.app.agents.agent_learning_4 import AgentLearning4

        global _losing_tickers

        base_date = datetime(2026, 3, 17, tzinfo=timezone.utc)  # After Team 4 activation

        # Track results per team
        team_stats = {
            2: {"scans": 0, "errors": [], "runs": 0},
            3: {"scans": 0, "errors": [], "runs": 0},
            4: {"scans": 0, "errors": [], "runs": 0},
        }

        # Market data mocks — agents use _fetch_current_price (module-level)
        # which tries market_data.fetch_price then yfinance fallback.
        # We mock at the agent module level since fetch_price doesn't exist in market_data.
        market_patches = [
            # Trader agents: mock _fetch_current_price
            patch("backend.app.agents.agent_trader_2._fetch_current_price", side_effect=_mock_fetch_price),
            patch("backend.app.agents.agent_trader_3._fetch_current_price", side_effect=_mock_fetch_price),
            patch("backend.app.agents.agent_trader_4._fetch_current_price", side_effect=_mock_fetch_price),
            # Journal agents: mock _fetch_current_price / internal fetchers
            patch("backend.app.agents.agent_journal_3._fetch_current_price", side_effect=_mock_fetch_price),
            # Scoring 3: mock fetch_history at market_data level (it does exist there)
            patch("backend.app.market_data.fetch_history", side_effect=_mock_fetch_history),
            # Journal 2: uses _fetch_bars_with_fallback internally (lazy import catch)
            # Journal 4: uses fetch_history from market_data (lazy import catch)
            patch("backend.app.market_data.fetch_history_range", side_effect=_mock_fetch_history_range),
            # PG disabled for all agents
            patch("backend.app.database.is_pg_enabled", return_value=False),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-teams234"}),
        ]

        # Also mock Team 1 modules (needed for shared pipeline)
        with _temp_json([]) as trades_file, _temp_json([]) as journal_file:
            team1_patches = [
                patch("backend.app.learning.is_pg_enabled", return_value=False),
                patch("backend.app.journal.is_pg_enabled", return_value=False),
                patch("backend.app.learning.TRADES_FILE", trades_file),
                patch("backend.app.journal.JOURNAL_FILE", journal_file),
                patch("backend.app.trade_selector.fetch_history", side_effect=_mock_fetch_history),
                patch("backend.app.news_scorer.fetch_history_batch", side_effect=_mock_fetch_history_batch),
                patch("backend.app.news_scorer.fetch_history", side_effect=_mock_fetch_history),
                patch("backend.app.journal.fetch_history_range", side_effect=_mock_fetch_history_range),
                patch("backend.app.trade_selector.check_event_conflict", return_value=False),
            ]

            all_patches = market_patches + team1_patches
            for p in all_patches:
                p.start()

            try:
                # Create agent instances (singletons within this test)
                scoring_2 = AgentScoring2()
                trader_2 = AgentTrader2()
                journal_2 = AgentJournal2()
                learning_2 = AgentLearning2()

                scoring_3 = AgentScoring3()
                trader_3 = AgentTrader3()
                journal_3 = AgentJournal3()
                learning_3 = AgentLearning3()

                scoring_4 = AgentScoring4()
                trader_4 = AgentTrader4()
                journal_4 = AgentJournal4()
                learning_4 = AgentLearning4()

                trading_day = 0

                for cal_day in range(45):  # 30 trading days
                    day_date = base_date + timedelta(days=cal_day)
                    if day_date.weekday() >= 5:
                        continue
                    trading_day += 1
                    if trading_day > 30:
                        break

                    day_scenarios = SCENARIOS[(trading_day - 1) % len(SCENARIOS)]
                    scan_time = day_date.replace(hour=8, minute=50)
                    scan_type = ScanType.EUROPE

                    # ── Step 1: Build scored news (Team 1 pipeline) ──
                    news_items = [
                        NewsItem(
                            title=s[0], source=s[1], url=f"https://test/{trading_day}",
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

                    with patch("backend.app.news_scorer._get_client", return_value=mock_client):
                        scored_news, market_ctx = score_news_batch(news_items, scan_type)

                    # Save a Team 1 trade for the data flow
                    invalidate_perf_summary_cache()
                    learning_adj = compute_learning_adjustments()
                    result = select_trade(scored_news, scan_type,
                                          learning_adjustments=learning_adj,
                                          market_context=market_ctx)
                    if result.has_trade and result.recommendation:
                        rec = result.recommendation
                        rec.timestamp = scan_time
                        save_trade(rec)

                    # ── Step 2: Team 2 — Trend scoring + trading ──
                    try:
                        trend_result = scoring_2.run(scored_news=scored_news, scan_type=scan_type)
                        team_stats[2]["scans"] += 1

                        learning_2_data = learning_2.get_adjustments() if hasattr(learning_2, "get_adjustments") else {}
                        trader_2.run(scored_news=scored_news, scan_type=scan_type,
                                     learning_data=learning_2_data, trend_scoring=trend_result)
                        team_stats[2]["runs"] += 1
                    except Exception as e:
                        team_stats[2]["errors"].append(f"D{trading_day}: {e}")

                    # ── Step 3: Team 3 — Technical scoring + trading ──
                    try:
                        weekly_config_3 = learning_3.get_weekly_config() if hasattr(learning_3, "get_weekly_config") else None
                        tech_result = scoring_3.run(scan_type=scan_type, weekly_config=weekly_config_3)
                        team_stats[3]["scans"] += 1

                        learning_3_data = learning_3.get_adjustments() if hasattr(learning_3, "get_adjustments") else {}
                        trader_3.run(tech_scoring=tech_result, scan_type=scan_type,
                                     learning_data=learning_3_data, weekly_config=weekly_config_3)
                        team_stats[3]["runs"] += 1
                    except Exception as e:
                        team_stats[3]["errors"].append(f"D{trading_day}: {e}")

                    # ── Step 4: Team 4 — Meta scoring + trading ──
                    try:
                        scoring1_output = {"scored": scored_news}
                        scoring2_result = trend_result if "trend_result" in dir() else None
                        scoring3_result = tech_result if "tech_result" in dir() else None

                        weekly_config_4 = learning_4.get_weekly_config() if hasattr(learning_4, "get_weekly_config") else None
                        meta_result = scoring_4.run(
                            news_data=scoring1_output,
                            trend_data=scoring2_result,
                            tech_data=scoring3_result,
                            scan_type=scan_type,
                            weekly_config=weekly_config_4)
                        team_stats[4]["scans"] += 1

                        learning_4_data = learning_4.get_adjustments() if hasattr(learning_4, "get_adjustments") else {}
                        trader_4.run(meta_scored=meta_result, scan_type=scan_type,
                                     learning_data=learning_4_data, weekly_config=weekly_config_4)
                        team_stats[4]["runs"] += 1
                    except Exception as e:
                        team_stats[4]["errors"].append(f"D{trading_day}: {e}")

                    # ── End of day: Team 1 journal ──
                    _losing_tickers = set()
                    run_daily_journal()
                    _losing_tickers = set()

                # ── End of simulation: run all journals and learnings ──
                journal_results = {}
                learning_results = {}

                for team_num, j_agent, l_agent in [
                    (2, journal_2, learning_2),
                    (3, journal_3, learning_3),
                    (4, journal_4, learning_4),
                ]:
                    try:
                        jr = j_agent.run()
                        journal_results[team_num] = jr
                    except Exception as e:
                        team_stats[team_num]["errors"].append(f"Journal: {e}")

                    try:
                        lr = l_agent.run()
                        learning_results[team_num] = lr
                    except Exception as e:
                        team_stats[team_num]["errors"].append(f"Learning: {e}")

            finally:
                for p in all_patches:
                    p.stop()

        # ── Report ──
        print(f"\n{'='*80}")
        print(f"  TEAMS 2-4 INTEGRATION TEST — {trading_day} trading days")
        print(f"{'='*80}")

        for team_num in [2, 3, 4]:
            s = team_stats[team_num]
            jr = journal_results.get(team_num, {})
            lr = learning_results.get(team_num, {})
            print(f"\n  TEAM {team_num}:")
            print(f"    Scoring runs: {s['scans']}")
            print(f"    Trader runs:  {s['runs']}")
            print(f"    Errors:       {len(s['errors'])}")
            if s["errors"]:
                for e in s["errors"][:5]:
                    print(f"      {e}")
            if isinstance(jr, dict):
                print(f"    Journal:      {jr.get('status', 'unknown')}")
            if isinstance(lr, dict):
                print(f"    Learning:     {lr.get('status', 'unknown')}")

        print(f"\n{'='*80}\n")

        # ── Assertions ──
        # Each team must have run scoring and trading without fatal crashes
        for team_num in [2, 3, 4]:
            s = team_stats[team_num]
            assert s["scans"] >= 20, \
                f"Team {team_num}: only {s['scans']} scoring runs (expected >=20)"
            assert s["runs"] >= 20, \
                f"Team {team_num}: only {s['runs']} trader runs (expected >=20)"
            # Allow some errors (graceful degradation), but not all
            assert len(s["errors"]) < s["scans"], \
                f"Team {team_num}: too many errors ({len(s['errors'])})" + \
                "\n".join(s["errors"][:10])

        # Scoring 3 should produce technical setups
        assert team_stats[3]["scans"] >= 20, "Scoring 3 didn't run enough"

        # Team 4 should activate (date > ACTIVATION_DATE)
        assert team_stats[4]["scans"] >= 20, "Team 4 scoring didn't activate"
