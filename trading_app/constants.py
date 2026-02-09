"""
Constantes de l'application: actifs suivis, symboles, mappings, jours fériés.
"""
from datetime import datetime
import holidays

# ============================================================================
# JOURS FÉRIÉS
# ============================================================================

HOLIDAYS_FR = holidays.France()
HOLIDAYS_US = holidays.NYSE()
HOLIDAYS_DE = holidays.Germany(prov='HE')

def est_jour_ferie(date_check=None, marche='ALL'):
    """Vérifie si une date est un jour férié où les marchés sont fermés."""
    from .market_context import get_paris_time
    if date_check is None:
        date_check = get_paris_time().date()
    if isinstance(date_check, datetime):
        date_check = date_check.date()

    raisons = []
    if marche in ['FR', 'ALL'] and date_check in HOLIDAYS_FR:
        raisons.append(f"FR: {HOLIDAYS_FR.get(date_check)}")
    if marche in ['US', 'ALL'] and date_check in HOLIDAYS_US:
        raisons.append(f"US: {HOLIDAYS_US.get(date_check)}")
    if marche in ['DE', 'ALL'] and date_check in HOLIDAYS_DE:
        raisons.append(f"DE: {HOLIDAYS_DE.get(date_check)}")
    if raisons:
        return True, " | ".join(raisons)
    return False, ""

def est_jour_trading_valide(date_check=None):
    """Vérifie si c'est un jour de trading valide (pas weekend, pas férié)."""
    from .market_context import get_paris_time
    if date_check is None:
        date_check = get_paris_time()
    if isinstance(date_check, datetime):
        date_obj = date_check.date()
        weekday = date_check.weekday()
    else:
        date_obj = date_check
        weekday = date_check.weekday()
    if weekday >= 5:
        return False, "Weekend"
    is_ferie, raison = est_jour_ferie(date_obj)
    if is_ferie:
        return False, f"Jour férié: {raison}"
    return True, ""

# ============================================================================
# ACTIFS SUIVIS
# ============================================================================

SYMBOLES_YAHOO_FALLBACK = {
    "^FCHI", "^GDAXI",
    "^GSPC", "^IXIC", "^DJI", "^VIX",
    "^N225", "^HSI", "^STOXX50E",
    "ES=F", "NQ=F", "YM=F"
}

ACTIFS_PERMANENTS = {
    "^FCHI": "CAC 40",
    "^GSPC": "S&P 500",
    "^IXIC": "Nasdaq",
    "^DJI": "Dow Jones",
    "^GDAXI": "DAX",
    "^VIX": "VIX",
    "AIR.PA": "Airbus",
    "MC.PA": "LVMH",
    "OR.PA": "L\'Oréal",
    "RMS.PA": "Hermès",
    "TTE.PA": "TotalEnergies",
    "SAN.PA": "Sanofi",
    "BNP.PA": "BNP Paribas",
    "AXA.PA": "AXA",
    "SU.PA": "Schneider Electric",
    "SAF.PA": "Safran",
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "NVDA": "NVIDIA",
    "TSLA": "Tesla",
    "AMZN": "Amazon",
    "META": "Meta",
    "GOOGL": "Google",
    "JPM": "JPMorgan",
    "XOM": "ExxonMobil",
    "V": "Visa",
    "GC=F": "Or",
    "SI=F": "Argent",
    "PL=F": "Platine",
    "BZ=F": "Pétrole Brent",
    "NG=F": "Gaz naturel",
    "KC=F": "Café",
    "CC=F": "Cacao",
    "HG=F": "Cuivre",
    "ZS=F": "Soja",
    "SB=F": "Sucre",
    "ZW=F": "Blé",
    "ZC=F": "Maïs",
    "EURUSD=X": "EUR/USD",
    "GBPUSD=X": "GBP/USD",
    "USDJPY=X": "USD/JPY"
}

SYMBOLES_ACTIONS_US = {"AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL", "JPM", "XOM", "V"}
ACTIFS_HORS_US = {k: v for k, v in ACTIFS_PERMANENTS.items() if k not in SYMBOLES_ACTIONS_US}

POOL_ROTATION = {
    "tech": {"AMD": "AMD", "INTC": "Intel", "ORCL": "Oracle", "CRM": "Salesforce", "ADBE": "Adobe", "CSCO": "Cisco", "NFLX": "Netflix", "PYPL": "PayPal", "QCOM": "Qualcomm"},
    "auto": {"GM": "General Motors", "F": "Ford", "STLA": "Stellantis", "RNO.PA": "Renault", "MBG.DE": "Mercedes", "BMW.DE": "BMW", "VOW3.DE": "Volkswagen", "RIVN": "Rivian", "LCID": "Lucid"},
    # CFR.SW (Richemont) et MONC.MI (Moncler) retirés - nécessitent plan Pro (CH/IT)
    "luxe": {"KER.PA": "Kering", "BOSS.DE": "Hugo Boss", "RMS.PA": "Hermès", "MC.PA": "LVMH"},
    # UCG.MI et SAN.MC retirés - nécessitent plan Pro (IT/ES)
    "banques_eu": {"GLE.PA": "Société Générale", "ACA.PA": "Crédit Agricole", "INGA.AS": "ING", "DBK.DE": "Deutsche Bank", "BNP.PA": "BNP Paribas"},
    "banques_us": {"BAC": "Bank of America", "C": "Citigroup", "GS": "Goldman Sachs", "WFC": "Wells Fargo", "MS": "Morgan Stanley"},
    "energie": {"CVX": "Chevron", "SHEL": "Shell", "BP": "BP", "ENGI.PA": "Engie", "CL=F": "Pétrole WTI"},
    "pharma": {"PFE": "Pfizer", "MRNA": "Moderna", "AZN": "AstraZeneca", "NVS": "Novartis"},
    "aero_defense": {"BA": "Boeing", "LMT": "Lockheed Martin", "NOC": "Northrop Grumman", "RTX": "Raytheon", "AM.PA": "Dassault Aviation", "HO.PA": "Thales"},
    "semi_conducteurs": {"ASML.AS": "ASML", "TSM": "TSMC", "AVGO": "Broadcom", "MU": "Micron"}
}

# ============================================================================
# TWELVE DATA - MAPPING SYMBOLES ET API
# ============================================================================

# Mapping Yahoo Finance -> Twelve Data
# Twelve Data exchange codes: EPA=Euronext Paris, XETRA=Frankfurt, etc.
SYMBOL_MAPPING_TWELVEDATA = {
    # Indices - format Twelve Data (vérifié API)
    "^FCHI": "FCHI",           # CAC 40
    "^GSPC": "SPX",            # S&P 500
    "^IXIC": "IXIC",           # NASDAQ Composite
    "^DJI": "DJI",             # Dow Jones
    "^GDAXI": "GDAXI",         # DAX
    "^VIX": "VIX",             # Volatility Index
    # Actions Françaises (Euronext Paris = XPAR)
    "AIR.PA": "AIR:XPAR",
    "MC.PA": "MC:XPAR",
    "OR.PA": "OR:XPAR",
    "RMS.PA": "RMS:XPAR",
    "TTE.PA": "TTE:XPAR",
    "SAN.PA": "SAN:XPAR",
    "BNP.PA": "BNP:XPAR",
    "AXA.PA": "CS:XPAR",    # AXA = symbole "CS" sur Twelve Data
    "SU.PA": "SU:XPAR",
    "SAF.PA": "SAF:XPAR",
    "GLE.PA": "GLE:XPAR",
    "ACA.PA": "ACA:XPAR",
    "KER.PA": "KER:XPAR",
    "ENGI.PA": "ENGI:XPAR",
    "AM.PA": "AM:XPAR",
    "HO.PA": "HO:XPAR",
    "RNO.PA": "RNO:XPAR",
    "MONC.MI": "MONC:XMIL", # Moncler (Milan)
    # Actions Allemandes (XETR = Frankfurt XETRA)
    "MBG.DE": "MBG:XETR",
    "BMW.DE": "BMW:XETR",
    "VOW3.DE": "VOW3:XETR",
    "BOSS.DE": "BOSS:XETR",
    "DBK.DE": "DBK:XETR",
    # Actions autres EU
    "ASML.AS": "ASML:XAMS",
    "INGA.AS": "INGA:XAMS",
    "UCG.MI": "UCG:XMIL",
    "SAN.MC": "SAN:XMAD",   # Santander = Bolsa Madrid
    "CFR.SW": "CFR:XSWX",
    # Métaux précieux (format forex sur Twelve Data)
    "GC=F": "XAU/USD",
    "SI=F": "XAG/USD",
    "PL=F": "XPT/USD",
    # Énergie (format Twelve Data officiel - vérifié API /commodities)
    "BZ=F": "XBR/USD",      # Brent Crude Spot
    "CL=F": "WTI/USD",      # Crude Oil WTI Spot (ou CL1 pour futures)
    "NG=F": "NG/USD",       # Natural Gas
    # Commodités agricoles (format Twelve Data vérifié)
    "KC=F": "KC1",          # Coffee
    "CC=F": "CC1",          # Cocoa
    "HG=F": "HG1",          # Copper
    "ZS=F": "S_1",          # Soybeans
    "SB=F": "SB1",          # Sugar
    "ZW=F": "W_1",          # Wheat
    "ZC=F": "C_1",          # Corn
    # Forex
    "EURUSD=X": "EUR/USD",
    "GBPUSD=X": "GBP/USD",
    "USDJPY=X": "USD/JPY",
    # Futures premarket (indices US)
    "ES=F": "ES",           # E-mini S&P 500
    "NQ=F": "NQ",           # E-mini Nasdaq 100
    "YM=F": "YM",           # E-mini Dow
    # Indices asiatiques/européens (vérifié API)
    "^N225": "N225",        # Nikkei 225
    "^HSI": "HSI",          # Hang Seng
    "^STOXX50E": "STOXX50E",# Euro Stoxx 50
}

