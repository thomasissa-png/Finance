"""Tests for structured data APIs module."""

from unittest.mock import MagicMock, patch

from backend.app.data_apis import (
    AGRICULTURAL_ZONES,
    COT_CODES,
    EIA_SERIES,
    GNEWS_QUERIES,
    collect_structured_data,
    fetch_cot_data,
    fetch_eia_data,
    fetch_gnews_targeted,
    fetch_options_unusual_activity,
    fetch_usda_crop_data,
    fetch_weather_alerts,
)


# ── Configuration tests ──────────────────────────────────────────────


def test_agricultural_zones_defined():
    """Should have at least 5 agricultural monitoring zones."""
    assert len(AGRICULTURAL_ZONES) >= 5
    for zone in AGRICULTURAL_ZONES:
        assert "name" in zone
        assert "lat" in zone
        assert "lon" in zone
        assert "tickers" in zone
        assert "crops" in zone


def test_agricultural_zones_cover_key_regions():
    """Should monitor Midwest, Brazil, Black Sea, etc."""
    names = [z["name"] for z in AGRICULTURAL_ZONES]
    names_joined = " ".join(names).lower()
    assert "midwest" in names_joined or "corn belt" in names_joined
    assert "brazil" in names_joined
    assert "ukraine" in names_joined or "black sea" in names_joined


def test_agricultural_zones_have_tickers():
    """Each zone should be linked to commodity tickers."""
    for zone in AGRICULTURAL_ZONES:
        assert len(zone["tickers"]) >= 1
        for t in zone["tickers"]:
            assert "=F" in t or ".PA" in t or "^" in t


def test_eia_series_defined():
    """Should have EIA series for crude, gasoline, distillate, natgas."""
    assert len(EIA_SERIES) >= 4
    series_names = " ".join(EIA_SERIES.values()).lower()
    assert "crude" in series_names
    assert "natural gas" in series_names or "gasoline" in series_names


def test_gnews_queries_defined():
    """Should have targeted queries for our edge categories."""
    assert len(GNEWS_QUERIES) >= 5
    categories = [q["category"] for q in GNEWS_QUERIES]
    assert "weather" in categories
    assert "geopolitical" in categories
    assert "commodity" in categories
    assert "supply_chain" in categories


def test_cot_codes_defined():
    """Should have COT codes for key commodities."""
    assert len(COT_CODES) >= 5
    tickers = [info["ticker"] for info in COT_CODES.values()]
    assert "CL=F" in tickers  # Oil
    assert "GC=F" in tickers  # Gold
    assert "ZC=F" in tickers  # Corn


# ── API functions without keys ───────────────────────────────────────


def test_fetch_eia_no_key():
    """EIA fetch should return empty list when no API key."""
    with patch.dict("os.environ", {}, clear=True):
        result = fetch_eia_data()
    assert result == []


def test_fetch_gnews_no_key():
    """GNews fetch should return empty list when no API key."""
    with patch.dict("os.environ", {}, clear=True):
        result = fetch_gnews_targeted()
    assert result == []


def test_fetch_usda_no_key():
    """USDA fetch should return empty list when no API key."""
    with patch.dict("os.environ", {}, clear=True):
        result = fetch_usda_crop_data()
    assert result == []


# ── Weather alerts (no key needed) ───────────────────────────────────


def test_fetch_weather_alerts_structure():
    """Weather alerts should return NewsItem objects with proper fields."""
    # Mock the API response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "daily": {
            "time": [
                # 7 past days
                "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",
                "2026-02-21", "2026-02-22", "2026-02-23",
                # 7 forecast days
                "2026-02-24", "2026-02-25", "2026-02-26", "2026-02-27",
                "2026-02-28", "2026-03-01", "2026-03-02",
            ],
            "temperature_2m_min": [
                5, 4, 3, 2, 1, 0, -1,   # past
                -5, -3, -2, 0, 1, 2, 3,  # forecast — frost!
            ],
            "temperature_2m_max": [
                15, 14, 13, 12, 11, 10, 9,  # past
                8, 9, 10, 11, 12, 13, 14,   # forecast
            ],
            "precipitation_sum": [
                0, 0, 0.1, 0, 0, 0, 0.2,  # past — drought! (0.3mm total)
                0, 0, 1, 0, 0, 0, 0,       # forecast
            ],
            "wind_speed_10m_max": [
                20, 25, 30, 15, 20, 18, 22,  # past
                25, 30, 35, 20, 15, 10, 12,  # forecast
            ],
        },
    }

    with patch("backend.app.data_apis.requests.get", return_value=mock_response):
        alerts = fetch_weather_alerts()

    # Should detect frost and drought for at least one zone
    assert len(alerts) >= 1
    for alert in alerts:
        assert alert.title.startswith("[METEO ALERTE]")
        assert alert.source == "Open-Meteo"
        assert alert.source_weight == 1.15
        assert len(alert.related_tickers) >= 1


def test_fetch_weather_alerts_no_extreme():
    """No alerts should be generated for normal weather."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "daily": {
            "time": [f"2026-02-{d:02d}" for d in range(10, 24)],
            "temperature_2m_min": [10] * 14,   # Mild
            "temperature_2m_max": [25] * 14,   # Mild
            "precipitation_sum": [5] * 14,     # Good rain
            "wind_speed_10m_max": [20] * 14,   # Light wind
        },
    }

    with patch("backend.app.data_apis.requests.get", return_value=mock_response):
        alerts = fetch_weather_alerts()

    # Normal weather = no alerts (or very few from Gulf/zones with different thresholds)
    # The Gulf zone has frost_threshold=-999, so it won't trigger frost
    frost_alerts = [a for a in alerts if "Gel" in a.title]
    heat_alerts = [a for a in alerts if "Canicule" in a.title]
    drought_alerts = [a for a in alerts if "Secheresse" in a.title]
    assert len(frost_alerts) == 0
    assert len(heat_alerts) == 0
    assert len(drought_alerts) == 0


# ── COT data ────────────────────────────────────────────────────────


def test_fetch_cot_data_network_error():
    """COT fetch should handle network errors gracefully."""
    mock_response = MagicMock()
    mock_response.status_code = 404

    with patch("backend.app.data_apis.requests.get", return_value=mock_response):
        result = fetch_cot_data()
    assert result == []


# ── Options unusual activity ─────────────────────────────────────────


def test_fetch_options_handles_errors():
    """Options fetch should handle errors gracefully."""
    with patch("yfinance.Ticker") as mock_ticker_cls:
        mock_ticker = MagicMock()
        mock_ticker.options = []
        mock_ticker_cls.return_value = mock_ticker
        result = fetch_options_unusual_activity()
    assert result == []


# ── Aggregate function ───────────────────────────────────────────────


def test_collect_structured_data_returns_list():
    """collect_structured_data should always return a list."""
    with patch("backend.app.data_apis.fetch_weather_alerts", return_value=[]), \
         patch("backend.app.data_apis.fetch_eia_data", return_value=[]), \
         patch("backend.app.data_apis.fetch_gnews_targeted", return_value=[]), \
         patch("backend.app.data_apis.fetch_usda_crop_data", return_value=[]), \
         patch("backend.app.data_apis.fetch_cot_data", return_value=[]), \
         patch("backend.app.data_apis.fetch_options_unusual_activity", return_value=[]), \
         patch("backend.app.data_apis.fetch_nasa_eonet_events", return_value=[]), \
         patch("backend.app.data_apis.fetch_gie_agsi_data", return_value=[]):
        result = collect_structured_data()
    assert isinstance(result, list)
    assert len(result) == 0
