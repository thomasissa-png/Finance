"""Configuration: 42 assets and application settings."""

from dataclasses import dataclass

SCAN_TIMES = {
    "europe": "07:50",       # CET — 10 min avant ouverture Euronext
    "mid_session": "11:15",  # CET — mid-session EU, capte PMIs + meteo matin
    "us": "14:50",           # CET — 20 min apres release macro US (ex 14:30 = conflit NFP/CPI)
    "us_session": "17:00",   # CET — US mid-session, capte EIA/ISM/WASDE + reaction open
}

# Mapping scan cache keys to scan type (asset eligibility)
# mid_session uses europe assets, us_session uses US assets
SCAN_KEY_TO_TYPE: dict[str, str] = {
    "europe": "europe",
    "mid_session": "europe",
    "us": "us",
    "us_session": "us",
}

TARGET_PERCENT = 1.0  # Objectif minimum de mouvement en %
MIN_RISK_REWARD = 1.2  # Ratio risque/rendement minimum (ex 1.3 — 1.2 plus realiste en intraday)
NEWS_MAX_AGE_HOURS = 8  # Ignorer les news de plus de 8h (ex 6h — elargi pour capter overnight US au scan Europe 07:50)
NEWS_FRESHNESS_PEAK_HOURS = 2  # Score max si < 2h
MIN_SCORE_THRESHOLD = 20  # Score minimum pour recommander un trade (ex 25 — capte les signaux mid-range)

DEFAULT_SOURCE_WEIGHT = 0.75

# ── Edge-priority: score multipliers par categorie ──────────────
# Reflete l'edge REEL du systeme sur chaque type de news.
# Les earnings/macro sont deja pricees par les algos → on penalise fortement.
# Les signaux physiques (commodity, meteo) ont un delai de transmission → on booste.
CATEGORY_SCORE_MULTIPLIERS: dict[str, float] = {
    "earnings":      0.2,   # Quasi zero-edge — deja price en pre-market
    "macro":         0.3,   # Algos HFT dominent — on n'a aucun avantage
    "geopolitical":  1.3,   # Fort edge si signal early — delai de pricing 1-6h
    "regulatory":    0.9,   # Peut avoir de l'edge si signal early (ex 0.6)
    "m_a":           0.7,   # Fort si rumeur, rarement en avance de phase (ex 0.5)
    "sector":        1.2,   # Liens indirects = edge reel — le marche connecte lentement
    "commodity":     1.5,   # Edge max — signaux physiques (meteo, shipping, stocks)
    "weather":       1.6,   # Fort edge mais faux-positifs possibles sur previsions
    "supply_chain":  1.6,   # Disruptions logistiques — delai de pricing long
    "central_bank_subtle": 0.8,  # Speeches/minutes secondaires — edge faible mais non nul (ex 0.6)
    "other":         0.8,   # Defaut moins punitif (ex 0.6)
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
    "risk_on_eu": ["^FCHI", "^GDAXI", "^FTSE"],
    "risk_on_us": ["^GSPC", "^DJI", "^IXIC", "^RUT"],
    "jpy_carry": ["USDJPY=X", "EURJPY=X", "^N225"],
    "luxury": ["MC.PA", "RMS.PA", "OR.PA"],
    "agri": ["ZC=F", "ZW=F", "ZS=F"],
    "tropical_soft": ["KC=F", "SB=F", "CC=F", "OJ=F"],  # Same tropical zones (Brazil, West Africa)
    "livestock": ["LE=F", "HE=F"],  # Same disease/feed cost drivers
    "pgm": ["PL=F", "PA=F"],  # Platinum Group Metals — same South African mines
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
    "NG=F":  [
        {"ticker": "CL=F", "direction": "same", "reason": "Energie correle — substitution gaz/petrole"},
        {"ticker": "TTE.PA", "direction": "same", "reason": "Producteur gaz majeur"},
    ],
    # Metaux / safe haven
    "GC=F":  [
        {"ticker": "SI=F", "direction": "same", "reason": "Argent suit l'or"},
        {"ticker": "USDCHF=X", "direction": "inverse", "reason": "CHF safe haven correle a l'or"},
        {"ticker": "EURUSD=X", "direction": "same", "reason": "Or monte = dollar faiblit = EUR/USD monte"},
    ],
    # Agriculture — memes zones de production
    "ZC=F":  [
        {"ticker": "ZS=F", "direction": "same", "reason": "Soja meme zone de production (Midwest)"},
        {"ticker": "ZW=F", "direction": "same", "reason": "Rotation des cultures — memes terres"},
    ],
    "ZW=F":  [
        {"ticker": "ZC=F", "direction": "same", "reason": "Rotation cultures — prix ble tire mais"},
    ],
    "KC=F":  [
        {"ticker": "SB=F", "direction": "same", "reason": "Memes planteurs bresil — sucre et cafe"},
        {"ticker": "CC=F", "direction": "same", "reason": "Cafe et cacao = soft tropicaux, memes pressions climatiques"},
    ],
    "SB=F":  [
        {"ticker": "KC=F", "direction": "same", "reason": "Memes zones bresiliennes — cafe et sucre"},
    ],
    "CC=F":  [
        {"ticker": "KC=F", "direction": "same", "reason": "Cacao et cafe = soft tropicaux, memes zones Afrique/Bresil"},
        {"ticker": "SB=F", "direction": "same", "reason": "Soft commodities tropicales correles"},
    ],
    "OJ=F":  [
        {"ticker": "SB=F", "direction": "same", "reason": "Jus d'orange et sucre — Bresil/Floride, meteo tropicale"},
    ],
    "CT=F":  [
        {"ticker": "ZC=F", "direction": "same", "reason": "Coton et mais — competition terres US South"},
    ],
    # Betail — feed demand + disease contagion
    "LE=F":  [
        {"ticker": "ZC=F", "direction": "same", "reason": "Betail = demande de mais (feed) — hausse betail tire le mais"},
        {"ticker": "HE=F", "direction": "same", "reason": "Meme filiere elevage, memes risques sanitaires"},
    ],
    "HE=F":  [
        {"ticker": "ZC=F", "direction": "same", "reason": "Porc = demande de mais/soja (feed)"},
        {"ticker": "LE=F", "direction": "same", "reason": "Meme filiere elevage, risques sanitaires communs"},
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
    # PGM (Platinum Group Metals) — same mines in South Africa
    "PL=F":  [
        {"ticker": "PA=F", "direction": "same", "reason": "Memes mines sud-africaines — disruption PGM impacte les deux"},
    ],
    "PA=F":  [
        {"ticker": "PL=F", "direction": "same", "reason": "Memes mines sud-africaines — disruption PGM impacte les deux"},
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
SCHEMA_VERSION = 3  # v3: ML learning improvements


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
    # Removed: CS.PA (AXA), CAP.PA (Capgemini), VIE.PA (Veolia), DG.PA (Vinci),
    # EN.PA (Bouygues) — zero edge: no dedicated source, no chain reaction,
    # no commodity/weather link. Only reachable via generic RSS/yfinance.
    Asset("RI.PA", "Pernod Ricard", "actions_europe", "EUR"),
    # ── MÉTAUX PRÉCIEUX (4) ──────────────────────────────────────
    Asset("GC=F", "Or", "metaux", "USD"),
    Asset("SI=F", "Argent", "metaux", "USD"),
    Asset("PL=F", "Platine", "metaux", "USD"),   # Covered by GNews "mine strike South Africa" query
    Asset("PA=F", "Palladium", "metaux", "USD"),  # Covered by GNews "mine strike South Africa" + Russia sanctions
    # ── FOREX (9) ────────────────────────────────────────────────
    Asset("EURUSD=X", "EUR/USD", "forex", "USD"),
    Asset("GBPUSD=X", "GBP/USD", "forex", "USD"),
    Asset("USDJPY=X", "USD/JPY", "forex", "JPY"),
    Asset("AUDUSD=X", "AUD/USD", "forex", "USD"),
    Asset("USDCHF=X", "USD/CHF", "forex", "CHF"),
    # Removed: USDCAD=X, NZDUSD=X, EURGBP=X — no dedicated source, no chain reaction
    Asset("EURJPY=X", "EUR/JPY", "forex", "JPY"),
    # ── COMMODITIES (14) ──────────────────────────────────────────
    Asset("CL=F", "Pétrole WTI", "commodities", "USD"),
    Asset("BZ=F", "Pétrole Brent", "commodities", "USD"),
    Asset("NG=F", "Gaz Naturel", "commodities", "USD"),
    Asset("ZC=F", "Maïs", "commodities", "USD"),
    Asset("ZW=F", "Blé", "commodities", "USD"),
    Asset("ZS=F", "Soja", "commodities", "USD"),
    Asset("KC=F", "Café", "commodities", "USD"),
    Asset("SB=F", "Sucre", "commodities", "USD"),
    Asset("CC=F", "Cacao", "commodities", "USD"),
    Asset("CT=F", "Coton", "commodities", "USD"),
    Asset("OJ=F", "Jus d'Orange", "commodities", "USD"),
    Asset("HG=F", "Cuivre", "commodities", "USD"),
    Asset("LE=F", "Bétail Vivant", "commodities", "USD"),
    Asset("HE=F", "Porc Maigre", "commodities", "USD"),
    # ── INDICES (12) ─────────────────────────────────────────────
    Asset("^FCHI", "CAC 40", "indices", "EUR"),
    Asset("^GSPC", "S&P 500", "indices", "USD"),
    Asset("^DJI", "Dow Jones", "indices", "USD"),
    Asset("^IXIC", "Nasdaq", "indices", "USD"),
    Asset("^RUT", "Russell 2000", "indices", "USD"),
    Asset("^GDAXI", "DAX", "indices", "EUR"),
    Asset("^FTSE", "FTSE 100", "indices", "GBP"),
    Asset("^N225", "Nikkei 225", "indices", "JPY"),  # Kept: jpy_carry correlation group
    # Removed: ^IBEX, ^FTSEMIB — in risk_on_eu but no dedicated source (^FCHI + ^GDAXI suffisent)
    # Removed: ^HSI (Hang Seng), ^AXJO (ASX 200) — no dedicated source, no chain reaction
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
    # Reuters: feeds.reuters.com deprecated (DNS dead). BBC Business as replacement.
    "https://feeds.bbci.co.uk/news/business/rss.xml",
    "https://feeds.bbci.co.uk/news/rss.xml",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://www.cnbc.com/id/10000664/device/rss/rss.html",
    "https://www.investing.com/rss/news.rss",
]

# ── Phase 1 feeds: early-signal sources (data brute, avant interpretation) ──
# Verified 2026-02-27 — 23 feeds (was 18, added drought.gov, climate.gov, war.gov, Suez/Panama canals; fixed ECB .html→.xml)
EARLY_SIGNAL_FEEDS = [
    # Meteo / Agri — signaux physiques pour commodities
    "https://www.drought.gov/rss/rss.xml",                          # Drought.gov: US drought monitor + outlooks (replaces dead NCEI news.xml)
    "https://www.spc.noaa.gov/products/spcrss.xml",                # SPC: severe weather outlooks, tornado/storm watches
    "https://www.weather.gov/rss_page.php?site_name=nws",          # NWS national weather summary
    "https://api.weather.gov/alerts/active.atom",                   # NWS active alerts (CAP v1.2 ATOM)
    "https://www.nhc.noaa.gov/index-at.xml",                       # NHC: Atlantic hurricane advisories (Jun-Nov critical for oil/sugar)
    "https://www.climate.gov/feeds/all.xml",                        # NOAA Climate.gov: ENSO, seasonal outlooks, climate events
    # USDA / FAO — rapports sur les recoltes et stocks
    "https://www.nass.usda.gov/rss/reports.xml",                    # NASS: crop reports, cold storage, production
    "https://www.fao.org/feeds/fao-newsroom-rss",                   # FAO: food security, agriculture, crop reports
    # Geopolitique — OSINT, conflits, sanctions, defense
    # defense.gov redirects to war.gov since 2025. Single entry (was 2 — redundant fallback wasted a thread).
    "https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx?max=10&ContentType=1&Site=945",  # war.gov (ex Defense.gov): military ops, geopolitics
    "https://www.iaea.org/feeds/pressalerts",                       # IAEA: nuclear, sanctions, inspections
    # Energie
    "https://www.eia.gov/rss/todayinenergy.xml",                   # EIA: energy analysis, stocks commentary
    "https://oilprice.com/rss/main",                               # OilPrice: crude, gas, OPEC
    # Maritime / Shipping — perturbations supply chain
    "https://gcaptain.com/feed/",                                   # gCaptain: maritime (intermittent 403)
    "https://www.marinelink.com/news/rss",                          # MarineLink: shipping, maritime, offshore
    "https://www.maritime-executive.com/articles.rss",              # Maritime Executive: shipping disruptions
    "https://splash247.com/feed/",                                  # Splash247: global shipping, ports, containers, BDI commentary
    # Canal chokepoints — Panama transit disruptions = supply chain + oil
    # Removed: suezcanal.gov.eg — HTML page, NOT an RSS feed (always fails to parse)
    "https://pancanal.com/en/feed/",                               # ACP: Panama Canal Authority (draft restrictions, transit delays)
    # Central banks — speeches et minutes (signaux dovish/hawkish subtils)
    "https://www.ecb.europa.eu/rss/press.xml",                     # ECB: press releases RSS (was .html — returned HTML not XML)
    "https://www.federalreserve.gov/feeds/press_all.xml",
    "https://www.bankofengland.co.uk/rss/speeches",                 # BoE: verified working (200)
]

# ── Source weights: early-signal sources get premium weight ──────
SOURCE_WEIGHTS: dict[str, float] = {
    # Phase 0: structured data APIs (premium — donnees chiffrees, pas du texte)
    "Open-Meteo": 1.2,
    "open-meteo": 1.2,
    "CFTC": 1.1,      # Raised from 1.05: positioning extremes (>8pp weekly swing) are genuinely market-moving
    "cftc": 1.1,
    "Options Flow": 1.0,  # Raised from 0.95: put/call extremes + IV skew = smart money hedging
    # Phase 1: early-signal (premium — info pas encore pricee)
    "USDA": 1.1,
    "usda": 1.1,
    "NASS": 1.1,
    "nass": 1.1,
    "FAO": 1.05,
    "fao.org": 1.05,
    "NOAA": 1.1,
    "NCEI": 1.1,
    "ncei": 1.1,
    "drought.gov": 1.1,
    "climate.gov": 1.1,
    "Storm Prediction Center": 1.1,
    "spc.noaa.gov": 1.1,
    "National Weather Service": 1.1,
    "weather.gov": 1.1,
    "api.weather.gov": 1.1,
    "EIA": 1.15,
    "eia.gov": 1.15,
    "IAEA": 1.05,
    "iaea.org": 1.05,
    "Defense.gov": 1.05,
    "defense.gov": 1.05,
    "war.gov": 1.05,  # Defense.gov redirects to war.gov
    "ECB": 1.0,
    "ecb.europa.eu": 1.0,
    "Federal Reserve": 1.0,
    "federalreserve.gov": 1.0,
    "Bank of England": 1.0,
    "bankofengland": 1.0,
    "gCaptain": 1.05,
    "gcaptain": 1.05,
    "MarineLink": 1.05,
    "marinelink": 1.05,
    "Maritime Executive": 1.05,
    "maritime-executive": 1.05,
    "OilPrice": 0.9,   # Lowered from 1.0: news aggregator, not primary source (often lags EIA/OPEC)
    "oilprice": 0.9,
    "Splash247": 1.05,
    "splash247": 1.05,
    # Removed: Suez Canal source weight — HTML page, not RSS (dead source)
    "Panama Canal": 1.1,
    "pancanal": 1.1,
    "SHFE": 1.1,               # Shanghai Futures Exchange inventories
    "shfe": 1.1,
    "FedWatch": 1.0,           # CME FedWatch implied rates
    "NHC": 1.15,                # Hurricane advisories — critical for oil/sugar
    "nhc.noaa.gov": 1.15,
    "National Hurricane Center": 1.15,
    "NASA EONET": 1.1,         # Natural events tracker — wildfires, storms, volcanoes
    "GIE AGSI": 1.15,          # Raised from 1.1: European gas storage — critical for NG/energy, especially winter
    # Phase 3: mainstream (info deja traitee — poids reduits)
    "BBC": 0.85,
    "bbc": 0.85,
    "reuters": 0.85,
    "Reuters": 0.85,
    "CNBC": 0.9,
    "cnbc": 0.9,
    "Investing.com": 0.7,
    "investing": 0.7,
    "Yahoo Finance": 0.8,
}
