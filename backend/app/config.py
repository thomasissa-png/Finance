"""Configuration: 49 assets and application settings."""

from dataclasses import dataclass

SCAN_TIMES = {
    "europe": "07:50",  # CET — 10 min avant ouverture Euronext
    "us": "14:30",       # CET — 1h avant ouverture Wall Street
}

TARGET_PERCENT = 1.0  # Objectif minimum de mouvement en %
MIN_RISK_REWARD = 1.3  # Ratio risque/rendement minimum (#20 — ex 1.0)
NEWS_MAX_AGE_HOURS = 6  # Ignorer les news de plus de 6h
NEWS_FRESHNESS_PEAK_HOURS = 2  # Score max si < 2h
MIN_SCORE_THRESHOLD = 55  # Score minimum pour recommander un trade (#19 — ex 40)

DEFAULT_SOURCE_WEIGHT = 0.75

# ── Edge-priority: score multipliers par categorie ──────────────
# Reflete l'edge REEL du systeme sur chaque type de news.
# Les earnings/macro sont deja pricees par les algos → on penalise fortement.
# Les signaux physiques (commodity, meteo) ont un delai de transmission → on booste.
CATEGORY_SCORE_MULTIPLIERS: dict[str, float] = {
    "earnings":      0.2,   # Quasi zero-edge — deja price en pre-market
    "macro":         0.3,   # Algos HFT dominent — on n'a aucun avantage
    "geopolitical":  1.3,   # Fort edge si signal early — delai de pricing 1-6h
    "regulatory":    0.8,   # Edge moyen — depend du timing
    "m_a":           0.5,   # Fort si rumeur, mais rarement en avance de phase
    "sector":        1.2,   # Liens indirects = edge reel — le marche connecte lentement
    "commodity":     1.5,   # Edge max — signaux physiques (meteo, shipping, stocks)
    "weather":       1.8,   # Edge maximal — le marche met 2-12h a pricer
    "supply_chain":  1.6,   # Disruptions logistiques — delai de pricing long
    "central_bank_subtle": 0.6,  # Speeches/minutes secondaires — edge faible mais non nul
    "other":         0.7,
}

# ── News category multipliers for calibration (#7) ────────────
NEWS_CATEGORY_MULTIPLIERS: dict[str, dict[str, float]] = {
    "earnings":             {"target_mult": 0.8,  "stop_mult": 1.2},  # Petit target, large stop — peu de conviction
    "macro":                {"target_mult": 0.8,  "stop_mult": 1.3},  # Idem — terrain hostile
    "geopolitical":         {"target_mult": 1.15, "stop_mult": 1.2},  # Target ambitieux, stop large (vol)
    "regulatory":           {"target_mult": 0.95, "stop_mult": 1.1},
    "m_a":                  {"target_mult": 1.2,  "stop_mult": 1.0},
    "sector":               {"target_mult": 1.1,  "stop_mult": 1.0},
    "commodity":            {"target_mult": 1.2,  "stop_mult": 1.05},  # Commodities physiques — gros moves possibles
    "weather":              {"target_mult": 1.3,  "stop_mult": 1.0},   # Meteo = moves directionnels forts
    "supply_chain":         {"target_mult": 1.2,  "stop_mult": 1.1},
    "central_bank_subtle":  {"target_mult": 0.9,  "stop_mult": 1.1},
    "other":                {"target_mult": 1.0,  "stop_mult": 1.0},
}

# ── Correlation groups (#22) ──────────────────────────────────
CORRELATION_GROUPS: dict[str, list[str]] = {
    "energy": ["TTE.PA", "CL=F", "BZ=F", "NG=F"],
    "gold_safe": ["GC=F", "SI=F", "USDCHF=X"],
    "risk_on_eu": ["^FCHI", "^GDAXI", "^FTSE", "^IBEX", "^FTSEMIB"],
    "risk_on_us": ["^GSPC", "^DJI", "^IXIC", "^RUT"],
    "jpy_carry": ["USDJPY=X", "EURJPY=X", "^N225"],
    "luxury": ["MC.PA", "RMS.PA", "OR.PA"],
    "agri": ["ZC=F", "ZW=F", "ZS=F"],
}

# ── Chain reactions: effets de second ordre ─────────────────────
# Quand une news impacte un actif, ces liens indirects sont souvent en retard.
CHAIN_REACTIONS: dict[str, list[dict[str, str]]] = {
    # Energie
    "CL=F":  [
        {"ticker": "TTE.PA", "direction": "same", "reason": "Stocks existants valent plus / producteur oil major"},
        {"ticker": "BZ=F", "direction": "same", "reason": "Brent correle au WTI"},
        {"ticker": "NG=F", "direction": "same", "reason": "Energie correle"},
    ],
    "BZ=F":  [{"ticker": "CL=F", "direction": "same", "reason": "WTI correle au Brent"}],
    # Metaux / safe haven
    "GC=F":  [
        {"ticker": "SI=F", "direction": "same", "reason": "Argent suit l'or"},
        {"ticker": "USDCHF=X", "direction": "inverse", "reason": "CHF safe haven correle a l'or"},
    ],
    # Agriculture — memes zones de production
    "ZC=F":  [
        {"ticker": "ZS=F", "direction": "same", "reason": "Soja meme zone de production (Midwest)"},
        {"ticker": "ZW=F", "direction": "same", "reason": "Rotation des cultures — memes terres"},
    ],
    "KC=F":  [
        {"ticker": "SB=F", "direction": "same", "reason": "Memes planteurs bresil — sucre et cafe"},
    ],
    # Geopolitique Moyen-Orient
    "USDJPY=X": [
        {"ticker": "GC=F", "direction": "inverse", "reason": "Risk-off: yen monte = or monte"},
    ],
    # Luxe / consommation Chine
    "MC.PA": [
        {"ticker": "RMS.PA", "direction": "same", "reason": "Meme exposition consommateur chinois"},
        {"ticker": "OR.PA", "direction": "same", "reason": "Luxe/beaute — meme clientele"},
    ],
    # Cuivre = indicateur industriel
    "HG=F":  [
        {"ticker": "^GSPC", "direction": "same", "reason": "Cuivre = proxy activite industrielle"},
        {"ticker": "^FCHI", "direction": "same", "reason": "Cuivre = proxy activite industrielle EU"},
    ],
}

# ── Scan trigger cooldown in seconds (#35) ────────────────────
TRIGGER_COOLDOWN_SECONDS = 300  # 5 minutes entre deux triggers manuels

# ── Schema version (#42) ──────────────────────────────────────
SCHEMA_VERSION = 2


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

# ── Session filtering: which assets are eligible per scan ────────
# Europe scan: Euronext + EUR/GBP indices + global (metals, forex, commodities)
# US scan: USD/JPY/HKD/AUD indices + global (metals, forex, commodities)
EUROPE_INDEX_CURRENCIES = {"EUR", "GBP"}
US_INDEX_CURRENCIES = {"USD", "JPY", "HKD", "AUD"}


def assets_for_session(scan_type_value: str) -> set[str]:
    """Return the set of tickers eligible for a given scan session."""
    tickers: set[str] = set()
    for a in ASSETS:
        if a.category == "actions_europe":
            # European stocks: only in Europe scan
            if scan_type_value == "europe":
                tickers.add(a.ticker)
        elif a.category == "indices":
            # Indices: filter by currency
            if scan_type_value == "europe" and a.currency in EUROPE_INDEX_CURRENCIES:
                tickers.add(a.ticker)
            elif scan_type_value == "us" and a.currency in US_INDEX_CURRENCIES:
                tickers.add(a.ticker)
        else:
            # Metals, forex, commodities: available in both sessions
            tickers.add(a.ticker)
    return tickers


NEWS_CATEGORIES = [
    "earnings", "macro", "geopolitical", "regulatory",
    "m_a", "sector", "commodity", "weather",
    "supply_chain", "central_bank_subtle", "other",
]

RSS_FEEDS = [
    # ── Phase 3: medias mainstream (information deja traitee par les algos) ──
    "https://feeds.reuters.com/reuters/businessNews",
    "https://feeds.reuters.com/reuters/topNews",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://www.cnbc.com/id/10000664/device/rss/rss.html",
    "https://www.investing.com/rss/news.rss",
]

# ── Phase 1 feeds: early-signal sources (data brute, avant interpretation) ──
EARLY_SIGNAL_FEEDS = [
    # Meteo / Agri — signaux physiques pour commodities
    "https://www.drought.gov/rss/rss.xml",
    "https://www.cpc.ncep.noaa.gov/products/precip_app/cpc_rss.xml",
    "https://alerts.weather.gov/cap/us.php?x=0",
    # USDA / FAO — rapports sur les recoltes et stocks
    "https://www.usda.gov/rss/home.xml",
    "https://www.fao.org/publications/highlights/rss/en/",
    # Geopolitique — OSINT, conflits, sanctions
    "https://www.state.gov/rss/channels/press-releases.xml",
    "https://www.iaea.org/feeds/press-releases",
    "https://www.eia.gov/rss/todayinenergy.xml",
    # Maritime / Energie — perturbations supply chain
    "https://gcaptain.com/feed/",
    "https://oilprice.com/rss/main",
    # Central banks — speeches et minutes (signaux dovish/hawkish subtils)
    "https://www.ecb.europa.eu/rss/press.html",
    "https://www.federalreserve.gov/feeds/press_all.xml",
    "https://www.bankofengland.co.uk/rss/speeches",
]

# ── Source weights: early-signal sources get premium weight ──────
SOURCE_WEIGHTS: dict[str, float] = {
    # Phase 0: structured data APIs (premium — donnees chiffrees, pas du texte)
    "Open-Meteo": 1.15,
    "open-meteo": 1.15,
    "CFTC": 1.05,
    "cftc": 1.05,
    "Options Flow": 0.95,
    # Phase 1: early-signal (premium — info pas encore pricee)
    "USDA": 1.1,
    "usda": 1.1,
    "FAO": 1.05,
    "fao.org": 1.05,
    "NOAA": 1.1,
    "drought.gov": 1.1,
    "weather.gov": 1.1,
    "EIA": 1.1,
    "eia.gov": 1.1,
    "IAEA": 1.05,
    "iaea.org": 1.05,
    "State Department": 1.0,
    "state.gov": 1.0,
    "ECB": 1.0,
    "ecb.europa.eu": 1.0,
    "Federal Reserve": 1.0,
    "federalreserve.gov": 1.0,
    "Bank of England": 1.0,
    "bankofengland": 1.0,
    "gCaptain": 1.05,
    "gcaptain": 1.05,
    "OilPrice": 1.0,
    "oilprice": 1.0,
    # Phase 3: mainstream (info deja traitee)
    "reuters": 1.0,
    "Reuters": 1.0,
    "CNBC": 0.9,
    "cnbc": 0.9,
    "Investing.com": 0.7,
    "investing": 0.7,
    "Yahoo Finance": 0.8,
}
