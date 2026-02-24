"""Configuration: 49 assets and application settings."""

from dataclasses import dataclass

SCAN_TIMES = {
    "europe": "07:50",  # CET — 10 min avant ouverture Euronext
    "us": "14:30",       # CET — 1h avant ouverture Wall Street
}

TARGET_PERCENT = 1.0  # Objectif minimum de mouvement en %
MIN_RISK_REWARD = 1.0  # Ratio risque/rendement minimum
NEWS_MAX_AGE_HOURS = 6  # Ignorer les news de plus de 6h
NEWS_FRESHNESS_PEAK_HOURS = 2  # Score max si < 2h
MIN_SCORE_THRESHOLD = 40  # Score minimum pour recommander un trade (sur 100)


@dataclass(frozen=True)
class Asset:
    ticker: str
    name: str
    category: str
    currency: str


ASSETS: list[Asset] = [
    # ── ACTIONS EURONEXT PARIS (15) ──────────────────────────────
    Asset("MC.PA", "LVMH", "actions_europe", "EUR"),
    Asset("OR.PA", "L'Oréal", "actions_europe", "EUR"),
    Asset("AI.PA", "Air Liquide", "actions_europe", "EUR"),
    Asset("SAN.PA", "Sanofi", "actions_europe", "EUR"),
    Asset("TTE.PA", "TotalEnergies", "actions_europe", "EUR"),
    Asset("BNP.PA", "BNP Paribas", "actions_europe", "EUR"),
    Asset("SU.PA", "Schneider Electric", "actions_europe", "EUR"),
    Asset("SAF.PA", "Safran", "actions_europe", "EUR"),
    Asset("RMS.PA", "Hermès", "actions_europe", "EUR"),
    Asset("CS.PA", "AXA", "actions_europe", "EUR"),
    Asset("CAP.PA", "Capgemini", "actions_europe", "EUR"),
    Asset("VIE.PA", "Veolia", "actions_europe", "EUR"),
    Asset("DG.PA", "Vinci", "actions_europe", "EUR"),
    Asset("EN.PA", "Bouygues", "actions_europe", "EUR"),
    Asset("RI.PA", "Pernod Ricard", "actions_europe", "EUR"),
    # ── MÉTAUX PRÉCIEUX (4) ──────────────────────────────────────
    Asset("GC=F", "Or", "metaux", "USD"),
    Asset("SI=F", "Argent", "metaux", "USD"),
    Asset("PL=F", "Platine", "metaux", "USD"),
    Asset("PA=F", "Palladium", "metaux", "USD"),
    # ── FOREX (9) ────────────────────────────────────────────────
    Asset("EURUSD=X", "EUR/USD", "forex", "USD"),
    Asset("GBPUSD=X", "GBP/USD", "forex", "USD"),
    Asset("USDJPY=X", "USD/JPY", "forex", "JPY"),
    Asset("AUDUSD=X", "AUD/USD", "forex", "USD"),
    Asset("USDCHF=X", "USD/CHF", "forex", "CHF"),
    Asset("USDCAD=X", "USD/CAD", "forex", "CAD"),
    Asset("NZDUSD=X", "NZD/USD", "forex", "USD"),
    Asset("EURGBP=X", "EUR/GBP", "forex", "GBP"),
    Asset("EURJPY=X", "EUR/JPY", "forex", "JPY"),
    # ── COMMODITIES (9) ──────────────────────────────────────────
    Asset("CL=F", "Pétrole WTI", "commodities", "USD"),
    Asset("BZ=F", "Pétrole Brent", "commodities", "USD"),
    Asset("NG=F", "Gaz Naturel", "commodities", "USD"),
    Asset("ZC=F", "Maïs", "commodities", "USD"),
    Asset("ZW=F", "Blé", "commodities", "USD"),
    Asset("ZS=F", "Soja", "commodities", "USD"),
    Asset("KC=F", "Café", "commodities", "USD"),
    Asset("SB=F", "Sucre", "commodities", "USD"),
    Asset("HG=F", "Cuivre", "commodities", "USD"),
    # ── INDICES (12) ─────────────────────────────────────────────
    Asset("^FCHI", "CAC 40", "indices", "EUR"),
    Asset("^GSPC", "S&P 500", "indices", "USD"),
    Asset("^DJI", "Dow Jones", "indices", "USD"),
    Asset("^IXIC", "Nasdaq", "indices", "USD"),
    Asset("^RUT", "Russell 2000", "indices", "USD"),
    Asset("^GDAXI", "DAX", "indices", "EUR"),
    Asset("^FTSE", "FTSE 100", "indices", "GBP"),
    Asset("^IBEX", "IBEX 35", "indices", "EUR"),
    Asset("^FTSEMIB", "FTSE MIB", "indices", "EUR"),
    Asset("^N225", "Nikkei 225", "indices", "JPY"),
    Asset("^HSI", "Hang Seng", "indices", "HKD"),
    Asset("^AXJO", "ASX 200", "indices", "AUD"),
]

ASSET_BY_TICKER = {a.ticker: a for a in ASSETS}

CATEGORIES = {
    "actions_europe": "Actions Euronext Paris",
    "metaux": "Métaux Précieux",
    "forex": "Forex",
    "commodities": "Commodities",
    "indices": "Indices Boursiers",
}

RSS_FEEDS = [
    "https://feeds.reuters.com/reuters/businessNews",
    "https://feeds.reuters.com/reuters/topNews",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://www.cnbc.com/id/10000664/device/rss/rss.html",
    "https://www.investing.com/rss/news.rss",
]
