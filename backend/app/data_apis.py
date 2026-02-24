"""Structured data APIs: EIA, Open-Meteo, USDA, GNews.

These provide NUMERICAL data that Claude can interpret precisely,
unlike RSS feeds which only give headlines.
All APIs are free-tier and optional (env var keys).
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any

import requests

from .models import NewsItem

logger = logging.getLogger(__name__)

# ── Timeouts for all HTTP requests ────────────────────────────────────
REQUEST_TIMEOUT = 15  # seconds


# ═══════════════════════════════════════════════════════════════════════
# 1. EIA API — US energy stocks and production
# ═══════════════════════════════════════════════════════════════════════
# Free key: https://www.eia.gov/opendata/register.php
# Env var: EIA_API_KEY

EIA_SERIES = {
    # Weekly US crude oil stocks (most market-moving)
    "PET.WCESTUS1.W": "US Crude Oil Stocks (Weekly)",
    # Weekly US gasoline stocks
    "PET.WGTSTUS1.W": "US Gasoline Stocks (Weekly)",
    # Weekly US distillate stocks
    "PET.WDISTUS1.W": "US Distillate Fuel Stocks (Weekly)",
    # Weekly natural gas storage
    "NG.NW2_EPG0_SWO_R48_BCF.W": "US Natural Gas Storage (Weekly)",
}


def fetch_eia_data() -> list[NewsItem]:
    """Fetch latest EIA energy data as structured news items.

    Returns NewsItem objects with numerical data in the title
    so Claude can interpret exact figures.
    """
    api_key = os.environ.get("EIA_API_KEY", "")
    if not api_key:
        logger.debug("EIA_API_KEY not set — skipping EIA data")
        return []

    items: list[NewsItem] = []

    for series_id, description in EIA_SERIES.items():
        try:
            # EIA API v2
            url = "https://api.eia.gov/v2/seriesid/" + series_id
            params = {"api_key": api_key, "num": 2}  # Last 2 data points
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)

            if resp.status_code != 200:
                # Try v1 fallback
                url_v1 = f"https://api.eia.gov/series/?api_key={api_key}&series_id={series_id}&num=2"
                resp = requests.get(url_v1, timeout=REQUEST_TIMEOUT)

            if resp.status_code != 200:
                logger.debug("EIA API error for %s: %d", series_id, resp.status_code)
                continue

            data = resp.json()

            # Check for API error in response body
            if data.get("error"):
                logger.debug("EIA API returned error for %s: %s", series_id, data["error"])
                continue

            # Parse v1 response format
            series_data = None
            if "series" in data and data["series"]:
                series_data = data["series"][0].get("data", [])
            elif "response" in data and "data" in data["response"]:
                series_data = data["response"]["data"]

            if not series_data or len(series_data) < 2:
                continue

            # Calculate week-over-week change
            current_val = float(series_data[0][1]) if isinstance(series_data[0], list) else float(series_data[0].get("value", 0))
            previous_val = float(series_data[1][1]) if isinstance(series_data[1], list) else float(series_data[1].get("value", 0))

            if previous_val == 0:
                continue

            change = current_val - previous_val
            change_pct = (change / abs(previous_val)) * 100

            # Determine related tickers based on series
            tickers = []
            if "Crude" in description:
                tickers = ["CL=F", "BZ=F"]
            elif "Gasoline" in description:
                tickers = ["CL=F"]
            elif "Distillate" in description:
                tickers = ["CL=F", "BZ=F"]
            elif "Natural Gas" in description:
                tickers = ["NG=F"]

            # Build a precise headline with numbers
            direction = "hausse" if change > 0 else "baisse"
            title = (
                f"[EIA DATA] {description}: {current_val:,.1f} "
                f"({direction} de {abs(change):,.1f}, {change_pct:+.1f}% vs semaine precedente)"
            )

            items.append(NewsItem(
                title=title,
                source="EIA",
                url=f"https://www.eia.gov/petroleum/supply/weekly/",
                published=datetime.now(timezone.utc),
                related_tickers=tickers,
                source_weight=1.1,
            ))

        except Exception as exc:
            logger.debug("EIA fetch error for %s: %s", series_id, exc)

    if items:
        logger.info("Fetched %d EIA data points", len(items))
    return items


# ═══════════════════════════════════════════════════════════════════════
# 2. Open-Meteo — Agricultural zone weather monitoring
# ═══════════════════════════════════════════════════════════════════════
# Completely free, no API key needed
# https://open-meteo.com/

# Critical agricultural zones with their commodity impacts
# growing_months: months when crops are vulnerable (frost/heat matter)
# drought_threshold_mm: zone-specific 7-day precipitation threshold
AGRICULTURAL_ZONES: list[dict[str, Any]] = [
    {
        "name": "US Midwest Corn Belt",
        "lat": 41.5, "lon": -89.0,
        "tickers": ["ZC=F", "ZS=F", "ZW=F"],
        "crops": "mais, soja, ble",
        "frost_threshold": -2,  # °C — late frost kills crops
        "heat_threshold": 35,   # °C — corn silking stress starts at 35°C
        "growing_months": [4, 5, 6, 7, 8, 9, 10],  # Apr-Oct
        "drought_threshold_mm": 10.0,  # 10mm/7d during growing season
        "drought_note": "secheresse critique pendant la saison de croissance",
    },
    {
        "name": "Brazil Minas Gerais (Coffee/Sugar)",
        "lat": -21.0, "lon": -44.0,
        "tickers": ["KC=F", "SB=F"],
        "crops": "cafe, sucre",
        "frost_threshold": 2,   # Coffee damage starts below 2°C
        "heat_threshold": 40,
        "growing_months": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],  # Year-round (perennial)
        "drought_threshold_mm": 5.0,
        "drought_note": "secheresse = stress hydrique cafe",
    },
    {
        "name": "Brazil Sao Paulo (Sugar/Ethanol)",
        "lat": -22.5, "lon": -47.5,
        "tickers": ["SB=F", "KC=F"],
        "crops": "sucre, ethanol, cafe",
        "frost_threshold": 2,
        "heat_threshold": 40,
        "growing_months": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
        "drought_threshold_mm": 5.0,
        "drought_note": "impacts recolte sucre",
    },
    {
        "name": "Ukraine/Black Sea (Wheat)",
        "lat": 49.0, "lon": 32.0,
        "tickers": ["ZW=F", "ZC=F"],
        "crops": "ble, mais",
        "frost_threshold": -20,  # Winter wheat hardened, extreme cold needed
        "heat_threshold": 35,
        "growing_months": [3, 4, 5, 6, 7, 8, 9, 10, 11],  # Mar-Nov
        "drought_threshold_mm": 8.0,
        "drought_note": "Mer Noire = 25% export ble mondial",
    },
    {
        "name": "India (Wheat/Rice)",
        "lat": 28.5, "lon": 77.0,
        "tickers": ["ZW=F"],
        "crops": "ble, riz",
        "frost_threshold": 0,
        "heat_threshold": 42,   # India heatwaves kill wheat
        "growing_months": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],  # Almost year-round
        "drought_threshold_mm": 5.0,
        "drought_note": "mousson faible = crise alimentaire",
    },
    {
        "name": "Gulf of Mexico (Oil/Gas)",
        "lat": 28.0, "lon": -90.0,
        "tickers": ["CL=F", "NG=F"],
        "crops": "petrole offshore, gaz",
        "frost_threshold": -999,  # Not relevant
        "heat_threshold": 999,
        "growing_months": [6, 7, 8, 9, 10, 11],  # Hurricane season Jun-Nov
        "drought_threshold_mm": -1,  # Drought not relevant for offshore
        "drought_note": "ouragans = arret production offshore",
    },
    {
        "name": "Southeast Asia (Palm Oil/Rice)",
        "lat": 2.0, "lon": 103.0,
        "tickers": ["ZS=F"],  # Soy as proxy for vegetable oils
        "crops": "huile de palme, riz",
        "frost_threshold": -999,
        "heat_threshold": 40,
        "growing_months": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
        "drought_threshold_mm": 15.0,  # Tropical — needs more rain
        "drought_note": "El Nino = secheresse palmiers",
    },
    {
        "name": "Australia (Wheat)",
        "lat": -33.5, "lon": 148.0,
        "tickers": ["ZW=F"],
        "crops": "ble",
        "frost_threshold": -5,
        "heat_threshold": 42,
        "growing_months": [4, 5, 6, 7, 8, 9, 10, 11],  # Apr-Nov (Southern hemisphere)
        "drought_threshold_mm": 5.0,
        "drought_note": "secheresse = export reduction",
    },
]


def _is_growing_season(zone: dict[str, Any]) -> bool:
    """Check if the current month is within the zone's growing season."""
    current_month = datetime.now(timezone.utc).month
    return current_month in zone.get("growing_months", range(1, 13))


def fetch_weather_alerts() -> list[NewsItem]:
    """Fetch weather data for critical agricultural zones and generate alerts.

    Checks for: frost, extreme heat, drought (low precipitation),
    and wind extremes (hurricanes for Gulf).
    Frost/heat alerts are only generated during the growing season.
    Drought thresholds are zone-specific.
    """
    items: list[NewsItem] = []

    for zone in AGRICULTURAL_ZONES:
        try:
            # Fetch 7-day forecast + last 7 days history
            url = "https://api.open-meteo.com/v1/forecast"
            params = {
                "latitude": zone["lat"],
                "longitude": zone["lon"],
                "daily": "temperature_2m_min,temperature_2m_max,precipitation_sum,wind_speed_10m_max",
                "past_days": 7,
                "forecast_days": 7,
                "timezone": "UTC",
            }
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                continue

            data = resp.json()
            daily = data.get("daily", {})
            if not daily:
                continue

            dates = daily.get("time", [])
            temp_mins = daily.get("temperature_2m_min", [])
            temp_maxs = daily.get("temperature_2m_max", [])
            precip_sums = daily.get("precipitation_sum", [])
            wind_maxs = daily.get("wind_speed_10m_max", [])

            # Split into past (first 7) and forecast (last 7)
            past_precip = [p for p in precip_sums[:7] if p is not None]
            forecast_temp_mins = [t for t in temp_mins[7:] if t is not None]
            forecast_temp_maxs = [t for t in temp_maxs[7:] if t is not None]
            forecast_wind = [w for w in wind_maxs[7:] if w is not None]

            in_season = _is_growing_season(zone)

            # ── Check 1: Frost alert (only during growing season) ──
            if in_season and forecast_temp_mins and zone["frost_threshold"] > -100:
                min_forecast = min(forecast_temp_mins)
                if min_forecast <= zone["frost_threshold"]:
                    frost_date = dates[7 + forecast_temp_mins.index(min_forecast)] if len(dates) > 7 else "prochains jours"
                    title = (
                        f"[METEO ALERTE] Gel prevu a {zone['name']}: {min_forecast:.1f}°C "
                        f"le {frost_date} (seuil critique: {zone['frost_threshold']}°C) — "
                        f"cultures: {zone['crops']}"
                    )
                    items.append(NewsItem(
                        title=title,
                        source="Open-Meteo",
                        url="https://open-meteo.com",
                        published=datetime.now(timezone.utc),
                        related_tickers=zone["tickers"],
                        source_weight=1.15,
                    ))

            # ── Check 2: Heat stress alert (only during growing season) ──
            if in_season and forecast_temp_maxs and zone["heat_threshold"] < 100:
                max_forecast = max(forecast_temp_maxs)
                if max_forecast >= zone["heat_threshold"]:
                    heat_date = dates[7 + forecast_temp_maxs.index(max_forecast)] if len(dates) > 7 else "prochains jours"
                    title = (
                        f"[METEO ALERTE] Canicule prevue a {zone['name']}: {max_forecast:.1f}°C "
                        f"le {heat_date} (seuil: {zone['heat_threshold']}°C) — "
                        f"stress thermique: {zone['crops']}"
                    )
                    items.append(NewsItem(
                        title=title,
                        source="Open-Meteo",
                        url="https://open-meteo.com",
                        published=datetime.now(timezone.utc),
                        related_tickers=zone["tickers"],
                        source_weight=1.15,
                    ))

            # ── Check 3: Drought alert (zone-specific threshold) ──
            drought_threshold = zone.get("drought_threshold_mm", 10.0)
            if in_season and past_precip and drought_threshold > 0:
                total_precip_7d = sum(past_precip)
                if total_precip_7d < drought_threshold:
                    title = (
                        f"[METEO ALERTE] Secheresse a {zone['name']}: "
                        f"seulement {total_precip_7d:.1f}mm sur 7 jours "
                        f"(seuil: {drought_threshold}mm) — "
                        f"{zone['drought_note']} — cultures: {zone['crops']}"
                    )
                    items.append(NewsItem(
                        title=title,
                        source="Open-Meteo",
                        url="https://open-meteo.com",
                        published=datetime.now(timezone.utc),
                        related_tickers=zone["tickers"],
                        source_weight=1.15,
                    ))

            # ── Check 4: Hurricane-force winds (always active for relevant zones) ──
            if forecast_wind:
                max_wind = max(forecast_wind)
                if max_wind >= 100:  # 100 km/h = tropical storm force
                    title = (
                        f"[METEO ALERTE] Vents violents a {zone['name']}: "
                        f"{max_wind:.0f} km/h prevus — "
                        f"risque perturbation: {zone['crops']}"
                    )
                    items.append(NewsItem(
                        title=title,
                        source="Open-Meteo",
                        url="https://open-meteo.com",
                        published=datetime.now(timezone.utc),
                        related_tickers=zone["tickers"],
                        source_weight=1.15,
                    ))

        except Exception as exc:
            logger.debug("Weather fetch error for %s: %s", zone["name"], exc)

    if items:
        logger.info("Generated %d weather alerts from %d zones", len(items), len(AGRICULTURAL_ZONES))
    return items


# ═══════════════════════════════════════════════════════════════════════
# 3. GNews API — targeted news search by keywords
# ═══════════════════════════════════════════════════════════════════════
# Free tier: 100 requests/day
# https://gnews.io/
# Env var: GNEWS_API_KEY

# Targeted queries aligned with our edge categories
# Split by specificity: frost and drought are separate (different commodities)
GNEWS_QUERIES: list[dict[str, Any]] = [
    {
        "q": "frost freeze crop damage agriculture",
        "tickers": ["KC=F", "ZC=F", "ZW=F"],
        "category": "weather",
    },
    {
        "q": "drought harvest failure crop loss",
        "tickers": ["ZC=F", "ZW=F", "ZS=F"],
        "category": "weather",
    },
    {
        "q": "oil sanctions embargo pipeline explosion",
        "tickers": ["CL=F", "BZ=F"],
        "category": "geopolitical",
    },
    {
        "q": "port congestion shipping disruption canal blocked",
        "tickers": [],
        "category": "supply_chain",
    },
    {
        "q": "OPEC production cut output quota",
        "tickers": ["CL=F", "BZ=F"],
        "category": "commodity",
    },
    {
        "q": "wheat corn soybean crop report harvest",
        "tickers": ["ZC=F", "ZW=F", "ZS=F"],
        "category": "commodity",
    },
    {
        "q": "gold reserve central bank buying",
        "tickers": ["GC=F", "SI=F"],
        "category": "commodity",
    },
    {
        "q": "military strike missile attack conflict",
        "tickers": ["GC=F", "CL=F"],
        "category": "geopolitical",
    },
    {
        "q": "copper mine strike production halt",
        "tickers": ["HG=F"],
        "category": "supply_chain",
    },
]


def fetch_gnews_targeted() -> list[NewsItem]:
    """Fetch targeted news from GNews API using commodity/geopolitical queries.

    This gives us focused, recent news that RSS feeds might miss.
    Limited to 100 req/day on free tier — we use ~8 queries per scan.
    """
    api_key = os.environ.get("GNEWS_API_KEY", "")
    if not api_key:
        logger.debug("GNEWS_API_KEY not set — skipping GNews")
        return []

    items: list[NewsItem] = []
    seen_titles: set[str] = set()

    for query_cfg in GNEWS_QUERIES:
        try:
            url = "https://gnews.io/api/v4/search"
            params = {
                "q": query_cfg["q"],
                "token": api_key,
                "lang": "en",
                "max": 5,  # 5 results per query
                "sortby": "publishedAt",
            }
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                logger.debug("GNews API error for '%s': %d", query_cfg["q"], resp.status_code)
                continue

            data = resp.json()
            for article in data.get("articles", []):
                title = article.get("title", "")
                if not title or title in seen_titles:
                    continue
                seen_titles.add(title)

                published = None
                pub_str = article.get("publishedAt")
                if pub_str:
                    try:
                        published = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                    except ValueError:
                        pass

                source_name = article.get("source", {}).get("name", "GNews")

                items.append(NewsItem(
                    title=title,
                    source=source_name,
                    url=article.get("url", ""),
                    published=published,
                    related_tickers=query_cfg["tickers"],
                    source_weight=0.85,  # Slightly above default, below premium
                ))

        except Exception as exc:
            logger.debug("GNews fetch error for '%s': %s", query_cfg["q"], exc)

    if items:
        logger.info("Fetched %d targeted news from GNews (%d queries)", len(items), len(GNEWS_QUERIES))
    return items


# ═══════════════════════════════════════════════════════════════════════
# 4. USDA NASS API — Crop progress and conditions
# ═══════════════════════════════════════════════════════════════════════
# Free key: https://quickstats.nass.usda.gov/api
# Env var: USDA_API_KEY


def fetch_usda_crop_data() -> list[NewsItem]:
    """Fetch USDA crop progress/condition data.

    The USDA publishes weekly crop progress reports — these are
    among the most market-moving agricultural data points.
    """
    api_key = os.environ.get("USDA_API_KEY", "")
    if not api_key:
        logger.debug("USDA_API_KEY not set — skipping USDA data")
        return []

    items: list[NewsItem] = []
    current_year = str(datetime.now(timezone.utc).year)

    # Key crop progress queries
    queries = [
        {
            "commodity_desc": "CORN",
            "statisticcat_desc": "PROGRESS, MEASURED IN PCT HARVESTED",
            "tickers": ["ZC=F"],
            "label": "Mais",
        },
        {
            "commodity_desc": "SOYBEANS",
            "statisticcat_desc": "PROGRESS, MEASURED IN PCT HARVESTED",
            "tickers": ["ZS=F"],
            "label": "Soja",
        },
        {
            "commodity_desc": "WHEAT",
            "statisticcat_desc": "CONDITION, MEASURED IN PCT GOOD",
            "tickers": ["ZW=F"],
            "label": "Ble",
        },
    ]

    for q in queries:
        try:
            url = "https://quickstats.nass.usda.gov/api/api_GET/"
            params = {
                "key": api_key,
                "commodity_desc": q["commodity_desc"],
                "statisticcat_desc": q["statisticcat_desc"],
                "year": current_year,
                "agg_level_desc": "NATIONAL",
                "format": "JSON",
            }
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                continue

            data = resp.json()
            records = data.get("data", [])
            if not records:
                continue

            # Get the most recent record
            records.sort(key=lambda r: r.get("week_ending", ""), reverse=True)
            latest = records[0]
            value = latest.get("Value", "")
            week = latest.get("week_ending", "")
            stat = latest.get("short_desc", q["statisticcat_desc"])

            if value:
                title = (
                    f"[USDA DATA] {q['label']} — {stat}: {value}% "
                    f"(semaine du {week})"
                )
                items.append(NewsItem(
                    title=title,
                    source="USDA",
                    url="https://quickstats.nass.usda.gov/",
                    published=datetime.now(timezone.utc),
                    related_tickers=q["tickers"],
                    source_weight=1.1,
                ))

        except Exception as exc:
            logger.debug("USDA fetch error for %s: %s", q["commodity_desc"], exc)

    if items:
        logger.info("Fetched %d USDA data points", len(items))
    return items


# ═══════════════════════════════════════════════════════════════════════
# 5. CFTC COT Report — Commitments of Traders positioning
# ═══════════════════════════════════════════════════════════════════════


# CFTC commodity codes for our universe
COT_CODES: dict[str, dict[str, str]] = {
    "13874A": {"name": "Crude Oil WTI", "ticker": "CL=F"},
    "088691": {"name": "Gold", "ticker": "GC=F"},
    "084691": {"name": "Silver", "ticker": "SI=F"},
    "002602": {"name": "Corn", "ticker": "ZC=F"},
    "001602": {"name": "Wheat", "ticker": "ZW=F"},
    "005602": {"name": "Soybeans", "ticker": "ZS=F"},
    "083731": {"name": "Coffee", "ticker": "KC=F"},
    "080732": {"name": "Sugar", "ticker": "SB=F"},
    "023651": {"name": "Natural Gas", "ticker": "NG=F"},
    "085692": {"name": "Copper", "ticker": "HG=F"},
}


def fetch_cot_data() -> list[NewsItem]:
    """Fetch latest COT positioning data from CFTC.

    The COT report shows commercial vs speculative positioning.
    Extreme positions signal potential reversals — especially
    when combined with a physical catalyst (weather, supply disruption).
    """
    items: list[NewsItem] = []

    try:
        # CFTC publishes COT data as CSV — we use the short format
        # The Quandl/Nasdaq Data Link provides free access
        current_year = datetime.now(timezone.utc).year
        url = (
            f"https://www.cftc.gov/dea/newcot/deacom{current_year}.txt"
        )
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            # Try previous year's file format
            url = f"https://www.cftc.gov/dea/newcot/deacom.txt"
            resp = requests.get(url, timeout=REQUEST_TIMEOUT)

        if resp.status_code != 200:
            logger.debug("COT data not available: %d", resp.status_code)
            return []

        # Parse CSV — format is: Market_and_Exchange_Names, As_of_Date_In_Form_YYMMDD, ...
        lines = resp.text.strip().split("\n")
        if len(lines) < 2:
            return []

        # Parse header
        header = lines[0].split(",")
        # Find column indices
        col_map: dict[str, int] = {}
        for i, h in enumerate(header):
            h_clean = h.strip().strip('"')
            col_map[h_clean] = i

        # Key columns
        market_col = col_map.get("Market_and_Exchange_Names", 0)
        date_col = col_map.get("As_of_Date_In_Form_YYMMDD", 2)
        oi_col = col_map.get("Open_Interest_All", None)
        comm_long_col = col_map.get("Comm_Positions_Long_All", None)
        comm_short_col = col_map.get("Comm_Positions_Short_All", None)
        spec_long_col = col_map.get("NonComm_Positions_Long_All", None)
        spec_short_col = col_map.get("NonComm_Positions_Short_All", None)

        if None in (oi_col, comm_long_col, comm_short_col, spec_long_col, spec_short_col):
            logger.debug("COT CSV format not recognized")
            return []

        # Find most recent date
        latest_date = ""
        for line in lines[1:]:
            cols = line.split(",")
            if len(cols) > date_col:
                d = cols[date_col].strip().strip('"')
                if d > latest_date:
                    latest_date = d

        if not latest_date:
            return []

        # Parse data for latest date
        for line in lines[1:]:
            cols = line.split(",")
            if len(cols) <= max(oi_col, comm_long_col, comm_short_col, spec_long_col, spec_short_col):
                continue

            line_date = cols[date_col].strip().strip('"')
            if line_date != latest_date:
                continue

            market = cols[market_col].strip().strip('"')

            # Check if this market matches any of our tracked commodities
            matched_code = None
            for code, info in COT_CODES.items():
                if info["name"].lower() in market.lower() or code in market:
                    matched_code = code
                    break

            if not matched_code:
                continue

            info = COT_CODES[matched_code]

            try:
                oi = int(cols[oi_col].strip().strip('"'))
                comm_long = int(cols[comm_long_col].strip().strip('"'))
                comm_short = int(cols[comm_short_col].strip().strip('"'))
                spec_long = int(cols[spec_long_col].strip().strip('"'))
                spec_short = int(cols[spec_short_col].strip().strip('"'))
            except (ValueError, IndexError):
                continue

            if oi == 0:
                continue

            # Calculate net positioning
            comm_net = comm_long - comm_short
            spec_net = spec_long - spec_short
            comm_net_pct = (comm_net / oi) * 100
            spec_net_pct = (spec_net / oi) * 100

            # Generate alert for extreme positioning
            # Commercials (hedgers) are contrarian — if they're massively long,
            # it means they're buying physical at these prices = bullish signal
            alert_level = ""
            if abs(comm_net_pct) > 20:
                alert_level = "EXTREME"
            elif abs(comm_net_pct) > 10:
                alert_level = "NOTABLE"
            else:
                continue  # Not interesting enough

            comm_direction = "LONG" if comm_net > 0 else "SHORT"
            spec_direction = "LONG" if spec_net > 0 else "SHORT"

            title = (
                f"[COT DATA] {info['name']} — Positionnement {alert_level}: "
                f"Commerciaux net {comm_direction} {abs(comm_net_pct):.1f}% OI, "
                f"Speculateurs net {spec_direction} {abs(spec_net_pct):.1f}% OI "
                f"(date: {latest_date})"
            )

            items.append(NewsItem(
                title=title,
                source="CFTC",
                url="https://www.cftc.gov/dea/futures/deacmelf.htm",
                published=datetime.now(timezone.utc),
                related_tickers=[info["ticker"]],
                source_weight=1.05,
            ))

    except Exception as exc:
        logger.debug("COT data fetch error: %s", exc)

    if items:
        logger.info("Fetched %d COT positioning alerts", len(items))
    return items


# ═══════════════════════════════════════════════════════════════════════
# 6. Options Unusual Activity via yfinance
# ═══════════════════════════════════════════════════════════════════════


def fetch_options_unusual_activity() -> list[NewsItem]:
    """Detect unusual options activity on key assets.

    Large call/put volume spikes relative to open interest
    can signal smart money positioning before a move.
    """
    import yfinance as yf

    # Only check equity tickers and major ETFs with liquid options
    options_tickers = {
        "TTE.PA": "TotalEnergies",
        "MC.PA": "LVMH",
        "BNP.PA": "BNP Paribas",
        "SAN.PA": "Sanofi",
        "AI.PA": "Air Liquide",
    }

    items: list[NewsItem] = []

    for ticker, name in options_tickers.items():
        try:
            stock = yf.Ticker(ticker)
            expirations = stock.options
            if not expirations:
                continue

            # Check nearest expiration
            chain = stock.option_chain(expirations[0])
            calls = chain.calls
            puts = chain.puts

            if calls.empty and puts.empty:
                continue

            # Calculate total call/put volume and OI
            total_call_vol = int(calls["volume"].sum()) if "volume" in calls.columns else 0
            total_put_vol = int(puts["volume"].sum()) if "volume" in puts.columns else 0
            total_call_oi = int(calls["openInterest"].sum()) if "openInterest" in calls.columns else 0
            total_put_oi = int(puts["openInterest"].sum()) if "openInterest" in puts.columns else 0

            if total_call_oi == 0 and total_put_oi == 0:
                continue

            # Put/Call ratio
            total_vol = total_call_vol + total_put_vol
            if total_vol == 0:
                continue

            pc_ratio = total_put_vol / total_call_vol if total_call_vol > 0 else 999

            # Alert on extreme put/call ratios
            # Threshold 3.0 for EU equities (P/C > 2.0 is common on LVMH etc.)
            if pc_ratio > 3.0:
                title = (
                    f"[OPTIONS] {name} ({ticker}) — Put/Call ratio extreme: {pc_ratio:.1f} "
                    f"(puts: {total_put_vol}, calls: {total_call_vol}) — "
                    f"signal bearish, smart money positionne a la baisse?"
                )
                items.append(NewsItem(
                    title=title,
                    source="Options Flow",
                    url="",
                    published=datetime.now(timezone.utc),
                    related_tickers=[ticker],
                    source_weight=0.95,
                ))
            elif pc_ratio < 0.25 and total_call_vol > 2000:
                title = (
                    f"[OPTIONS] {name} ({ticker}) — Activite calls inhabituelle: P/C ratio {pc_ratio:.2f} "
                    f"(calls: {total_call_vol}, puts: {total_put_vol}) — "
                    f"signal bullish, accumulation de calls"
                )
                items.append(NewsItem(
                    title=title,
                    source="Options Flow",
                    url="",
                    published=datetime.now(timezone.utc),
                    related_tickers=[ticker],
                    source_weight=0.95,
                ))

            # Alert on volume spike vs OI (>50% of OI traded in a day)
            if total_call_oi > 0 and total_call_vol > total_call_oi * 0.5:
                title = (
                    f"[OPTIONS] {name} ({ticker}) — Volume calls = {total_call_vol / total_call_oi:.0%} "
                    f"de l'open interest ({total_call_vol} vs {total_call_oi} OI) — "
                    f"activite inhabituelle"
                )
                items.append(NewsItem(
                    title=title,
                    source="Options Flow",
                    url="",
                    published=datetime.now(timezone.utc),
                    related_tickers=[ticker],
                    source_weight=0.95,
                ))

        except Exception as exc:
            logger.debug("Options data error for %s: %s", ticker, exc)

    if items:
        logger.info("Detected %d unusual options activities", len(items))
    return items


# ═══════════════════════════════════════════════════════════════════════
# Aggregate all structured data
# ═══════════════════════════════════════════════════════════════════════


def collect_structured_data() -> list[NewsItem]:
    """Collect all structured data from APIs.

    Each source is best-effort — failures don't block the scan.
    """
    all_items: list[NewsItem] = []

    # Weather alerts (free, no key needed — always run)
    all_items.extend(fetch_weather_alerts())

    # EIA energy data (needs EIA_API_KEY)
    all_items.extend(fetch_eia_data())

    # GNews targeted search (needs GNEWS_API_KEY)
    all_items.extend(fetch_gnews_targeted())

    # USDA crop data (needs USDA_API_KEY)
    all_items.extend(fetch_usda_crop_data())

    # COT positioning (free, no key)
    all_items.extend(fetch_cot_data())

    # Options unusual activity (free via yfinance)
    all_items.extend(fetch_options_unusual_activity())

    if all_items:
        logger.info("Total structured data collected: %d items", len(all_items))

    return all_items
