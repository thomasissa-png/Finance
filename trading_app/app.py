"""
Agent Trading - Interface Web Complète
Application Flask avec interface dynamique pour le trading
"""

import os
import json
import re
import sqlite3
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta
from threading import Thread, Lock
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

import numpy as np
import pandas as pd
import pytz
import schedule
from anthropic import Anthropic
from flask import Flask, render_template, jsonify, request
from twilio.rest import Client
import requests
import holidays
import yfinance as yf

# ============================================================================
# CONFIGURATION
# ============================================================================

app = Flask(__name__)
# SECRET_KEY requis - utilise une valeur par défaut uniquement en dev
_secret_key = os.environ.get('SECRET_KEY')
if not _secret_key:
    import warnings
    warnings.warn("SECRET_KEY non définie! Utilisation d'une clé temporaire (non sécurisé en production)")
    _secret_key = 'dev-only-insecure-key-' + os.urandom(16).hex()
app.config['SECRET_KEY'] = _secret_key

# Configuration Logging
# - Fichier rotatif pour persistance (10MB max, 5 fichiers conservés)
# - Console pour développement
LOG_PATH = 'trading.log'
logger = logging.getLogger('trading_app')
logger.setLevel(logging.DEBUG)

# Handler fichier avec rotation
file_handler = RotatingFileHandler(LOG_PATH, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
file_handler.setLevel(logging.INFO)
file_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)

# Handler console pour développement
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.WARNING)  # Console uniquement WARNING+
console_formatter = logging.Formatter('[%(levelname)s] %(message)s')
console_handler.setFormatter(console_formatter)
logger.addHandler(console_handler)

# Clients API
client_anthropic = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
client_twilio = Client(
    os.environ.get("TWILIO_ACCOUNT_SID"),
    os.environ.get("TWILIO_AUTH_TOKEN")
) if os.environ.get("TWILIO_ACCOUNT_SID") else None

# Variables globales
NEWS_ENVOYEES_AUJOURDHUI = 0
MAX_NEWS_PAR_JOUR = 3
DERNIERE_VERIFICATION_DATE = None
DB_PATH = 'trading.db'
DB_TIMEOUT = 10.0  # Timeout SQLite standardisé (secondes)
NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY")
TWELVEDATA_API_KEY = os.environ.get("TWELVEDATA_API_KEY")

# Rate limiting pour Twelve Data (Plan Grow: 55 appels/min)
TWELVEDATA_LAST_CALL = None
TWELVEDATA_MIN_INTERVAL = 1.1  # 60s/55 = ~1.09s entre chaque appel
TWELVEDATA_CACHE = {}  # Cache simple {symbole: {'data': ..., 'timestamp': ...}}
TWELVEDATA_CACHE_TTL = 600  # Cache valide 10 minutes (réduit consommation quota de 50%)
TWELVEDATA_CACHE_MAX_SIZE = 500  # Limite du cache pour éviter fuite mémoire

# Circuit breaker pour quota dépassé
TWELVEDATA_QUOTA_EXCEEDED = False  # Flag pour stopper tous les appels
TWELVEDATA_QUOTA_RESET_TIME = None  # Timestamp de reset du quota
TWELVEDATA_QUOTA_COOLDOWN = 60  # Cooldown en secondes après quota exceeded

# Cache pour les news (évite appels NewsAPI répétés)
NEWS_CACHE = {'data': None, 'timestamp': 0}
NEWS_CACHE_TTL = 300  # 5 minutes

# Cache VIX persistant avec fallback
VIX_CACHE = {'value': 20.0, 'timestamp': 0, 'source': 'default'}
VIX_CACHE_TTL = 300  # 5 minutes - VIX change lentement
VIX_CACHE_FILE = 'vix_cache.json'  # Persistance entre redémarrages

# Locks pour thread-safety (bugs critiques #1 et #2)
TWELVEDATA_RATE_LOCK = Lock()  # Protège TWELVEDATA_LAST_CALL
TWELVEDATA_CACHE_LOCK = Lock()  # Protège TWELVEDATA_CACHE
TWELVEDATA_FETCH_LOCK = Lock()  # Protège contre le stampede (multiples appels simultanés)

# Timezone
TZ_PARIS = pytz.timezone('Europe/Paris')

# ============================================================================
# SÉCURITÉ - Headers et protection API
# ============================================================================

@app.after_request
def add_security_headers(response):
    """Ajoute des headers de sécurité à toutes les réponses"""
    # Protection contre le clickjacking
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    # Protection XSS (navigateurs modernes)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    # Referrer policy
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response

def to_python_type(val):
    """Convertit les types numpy en types Python natifs pour la sérialisation JSON.
    numpy.int64/float64 → int/float Python natif"""
    if val is None:
        return None
    if isinstance(val, (np.integer, np.int64)):
        return int(val)
    if isinstance(val, (np.floating, np.float64)):
        return float(val)
    if isinstance(val, np.ndarray):
        return val.tolist()
    return val

# Calendriers des jours fériés (marchés fermés)
HOLIDAYS_FR = holidays.France()  # Euronext Paris
HOLIDAYS_US = holidays.NYSE()     # NYSE et NASDAQ
HOLIDAYS_DE = holidays.Germany(prov='HE')  # Francfort (Hesse)

def est_jour_ferie(date_check=None, marche='ALL'):
    """
    Vérifie si une date est un jour férié où les marchés sont fermés.
    marche: 'FR' (Euronext), 'US' (NYSE/NASDAQ), 'DE' (Francfort), 'ALL' (tous)
    Retourne: (bool is_ferie, str raison)
    """
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
    """
    Vérifie si c'est un jour de trading valide (pas weekend, pas férié).
    Retourne: (bool is_valide, str raison_si_invalide)
    """
    if date_check is None:
        date_check = get_paris_time()

    if isinstance(date_check, datetime):
        date_obj = date_check.date()
        weekday = date_check.weekday()
    else:
        date_obj = date_check
        weekday = date_check.weekday()

    # Weekend
    if weekday >= 5:
        return False, "Weekend"

    # Jours fériés
    is_ferie, raison = est_jour_ferie(date_obj)
    if is_ferie:
        return False, f"Jour férié: {raison}"

    return True, ""

# ============================================================================
# ACTIFS SUIVIS
# ============================================================================

# Symboles qui utilisent Yahoo Finance (plus fiable que Twelve Data pour les indices)
# Raison: Circuit breaker Twelve Data se déclenche souvent sur les indices
SYMBOLES_YAHOO_FALLBACK = {
    "^FCHI", "^GDAXI",  # Indices EU - souvent en échec sur Twelve Data
    "^GSPC", "^IXIC", "^DJI", "^VIX",  # Indices US
    "^N225", "^HSI", "^STOXX50E",  # Indices internationaux
    "ES=F", "NQ=F", "YM=F"  # E-mini futures
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
    "OR.PA": "L'Oréal",
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

def convert_symbol_to_twelvedata(yahoo_symbol):
    """Convertit un symbole Yahoo Finance vers Twelve Data.
    Retourne (symbol, mic_code) tuple pour utiliser les paramètres séparés de l'API.
    """
    # Si déjà mappé
    if yahoo_symbol in SYMBOL_MAPPING_TWELVEDATA:
        mapped = SYMBOL_MAPPING_TWELVEDATA[yahoo_symbol]
        # Format "SYMBOL:MIC" -> (symbol, mic_code)
        if ":" in mapped:
            parts = mapped.split(":", 1)
            return (parts[0], parts[1])
        # Format sans exchange (forex, commodities, US stocks)
        return (mapped, None)
    # Actions US restent identiques (pas de MIC nécessaire)
    if not any(x in yahoo_symbol for x in ['.', '=', '^']):
        return (yahoo_symbol, None)
    # Par défaut, retourner tel quel
    return (yahoo_symbol, None)

def check_quota_circuit_breaker():
    """Vérifie si le circuit breaker quota est actif. Retourne True si bloqué."""
    global TWELVEDATA_QUOTA_EXCEEDED, TWELVEDATA_QUOTA_RESET_TIME

    if not TWELVEDATA_QUOTA_EXCEEDED:
        return False

    # Vérifier si le cooldown est passé
    if TWELVEDATA_QUOTA_RESET_TIME and time.time() > TWELVEDATA_QUOTA_RESET_TIME:
        TWELVEDATA_QUOTA_EXCEEDED = False
        TWELVEDATA_QUOTA_RESET_TIME = None
        print("🔄 Circuit breaker quota réinitialisé")
        return False

    return True

def activate_quota_circuit_breaker():
    """Active le circuit breaker après un quota exceeded"""
    global TWELVEDATA_QUOTA_EXCEEDED, TWELVEDATA_QUOTA_RESET_TIME
    TWELVEDATA_QUOTA_EXCEEDED = True
    TWELVEDATA_QUOTA_RESET_TIME = time.time() + TWELVEDATA_QUOTA_COOLDOWN
    print(f"🛑 Circuit breaker QUOTA activé - pause {TWELVEDATA_QUOTA_COOLDOWN}s")

def rate_limit_twelvedata():
    """Applique un rate limiting thread-safe sur les appels Twelve Data"""
    global TWELVEDATA_LAST_CALL
    with TWELVEDATA_RATE_LOCK:
        if TWELVEDATA_LAST_CALL is not None:
            elapsed = time.time() - TWELVEDATA_LAST_CALL
            if elapsed < TWELVEDATA_MIN_INTERVAL:
                time.sleep(TWELVEDATA_MIN_INTERVAL - elapsed)
        TWELVEDATA_LAST_CALL = time.time()

def cleanup_twelvedata_cache():
    """Nettoie le cache si trop volumineux (bug #8)"""
    global TWELVEDATA_CACHE
    with TWELVEDATA_CACHE_LOCK:
        if len(TWELVEDATA_CACHE) > TWELVEDATA_CACHE_MAX_SIZE:
            # Garder les 400 entrées les plus récentes
            sorted_items = sorted(
                TWELVEDATA_CACHE.items(),
                key=lambda x: x[1]['timestamp'],
                reverse=True
            )
            TWELVEDATA_CACHE = dict(sorted_items[:400])
            print(f"🧹 Cache nettoyé: {len(sorted_items)} → 400 entrées")

def get_yahoo_finance_data(symbole, outputsize=30):
    """Fallback Yahoo Finance pour les symboles non supportés par Twelve Data (indices, futures)"""
    global TWELVEDATA_CACHE

    cache_key = f"yf_{symbole}_{outputsize}"
    now = time.time()

    # Vérifier le cache
    with TWELVEDATA_CACHE_LOCK:
        if cache_key in TWELVEDATA_CACHE:
            cached = TWELVEDATA_CACHE[cache_key]
            if now - cached['timestamp'] < TWELVEDATA_CACHE_TTL:
                return cached['data'], cached['is_fresh']

    try:
        ticker = yf.Ticker(symbole)
        # Calculer la période nécessaire (outputsize jours + marge)
        period = f"{min(outputsize + 5, 60)}d"
        hist = ticker.history(period=period)

        if hist.empty:
            return pd.DataFrame(), False

        # Renommer les colonnes pour cohérence avec Twelve Data
        hist = hist.rename(columns={
            'Open': 'Open', 'High': 'High', 'Low': 'Low',
            'Close': 'Close', 'Volume': 'Volume'
        })

        # Garder seulement les colonnes OHLCV
        hist = hist[['Open', 'High', 'Low', 'Close', 'Volume']].tail(outputsize)

        # Déterminer si données fraîches
        is_fresh = True
        if len(hist) > 0:
            last_date = hist.index[-1]
            if hasattr(last_date, 'date'):
                last_date = last_date.date()
            today = get_paris_time().date()
            is_fresh = last_date >= today - timedelta(days=3)

        # Mettre en cache
        with TWELVEDATA_CACHE_LOCK:
            TWELVEDATA_CACHE[cache_key] = {
                'data': hist,
                'timestamp': now,
                'is_fresh': is_fresh
            }

        return hist, is_fresh

    except Exception as e:
        logger.warning(f"Yahoo Finance {symbole}: {e}")
        return pd.DataFrame(), False

def get_yahoo_finance_batch(symboles_list, outputsize=30):
    """
    BATCH Yahoo Finance: Télécharge TOUS les symboles en UN SEUL appel.
    yfinance.download() supporte les listes de symboles nativement.

    Args:
        symboles_list: Liste de symboles Yahoo Finance (^GSPC, ^DJI, ES=F, etc.)
        outputsize: Nombre de barres

    Returns:
        Dict {symbole: (DataFrame, is_fresh)}
    """
    global TWELVEDATA_CACHE

    if not symboles_list:
        return {}

    results = {}
    now = time.time()
    symboles_to_fetch = []

    # Vérifier le cache d'abord
    for symbole in symboles_list:
        cache_key = f"yf_{symbole}_{outputsize}"
        with TWELVEDATA_CACHE_LOCK:
            if cache_key in TWELVEDATA_CACHE:
                cached = TWELVEDATA_CACHE[cache_key]
                if now - cached['timestamp'] < TWELVEDATA_CACHE_TTL:
                    results[symbole] = (cached['data'], cached['is_fresh'])
                    continue
        symboles_to_fetch.append(symbole)

    if not symboles_to_fetch:
        return results

    try:
        # BATCH DOWNLOAD: Un seul appel pour tous les symboles!
        period = f"{min(outputsize + 5, 60)}d"
        data = yf.download(
            symboles_to_fetch,
            period=period,
            group_by='ticker',
            progress=False,
            threads=True  # Téléchargement parallèle interne
        )

        if data.empty:
            for symbole in symboles_to_fetch:
                results[symbole] = (pd.DataFrame(), False)
            return results

        # Parser les résultats pour chaque symbole
        for symbole in symboles_to_fetch:
            try:
                # Format différent si 1 seul symbole vs plusieurs
                if len(symboles_to_fetch) == 1:
                    hist = data.copy()
                else:
                    if symbole not in data.columns.get_level_values(0):
                        results[symbole] = (pd.DataFrame(), False)
                        continue
                    hist = data[symbole].copy()

                if hist.empty or hist.dropna(how='all').empty:
                    results[symbole] = (pd.DataFrame(), False)
                    continue

                # Nettoyer et formater
                hist = hist.dropna(how='all')
                hist = hist[['Open', 'High', 'Low', 'Close', 'Volume']].tail(outputsize)

                # Déterminer fraîcheur
                is_fresh = True
                if len(hist) > 0:
                    last_date = hist.index[-1]
                    if hasattr(last_date, 'date'):
                        last_date = last_date.date()
                    today = get_paris_time().date()
                    is_fresh = last_date >= today - timedelta(days=3)

                # Mettre en cache
                cache_key = f"yf_{symbole}_{outputsize}"
                with TWELVEDATA_CACHE_LOCK:
                    TWELVEDATA_CACHE[cache_key] = {
                        'data': hist,
                        'timestamp': now,
                        'is_fresh': is_fresh
                    }

                results[symbole] = (hist, is_fresh)

            except Exception as e:
                logger.warning(f"Yahoo batch parse {symbole}: {e}")
                results[symbole] = (pd.DataFrame(), False)

    except Exception as e:
        logger.warning(f"Yahoo Finance batch download: {e}")
        for symbole in symboles_to_fetch:
            results[symbole] = (pd.DataFrame(), False)

    return results

def get_twelvedata_time_series(symbole, outputsize=30, interval="1day"):
    """Récupère les données historiques via Twelve Data API (thread-safe)
    Utilise Yahoo Finance en fallback pour les indices et futures non supportés."""
    global TWELVEDATA_CACHE

    # Fallback Yahoo Finance pour symboles non supportés par Twelve Data
    if symbole in SYMBOLES_YAHOO_FALLBACK:
        return get_yahoo_finance_data(symbole, outputsize)

    if not TWELVEDATA_API_KEY:
        print("⚠️ TWELVEDATA_API_KEY non configurée")
        return pd.DataFrame(), False

    cache_key = f"{symbole}_{interval}_{outputsize}"
    now = time.time()

    # Vérifier le cache (thread-safe)
    with TWELVEDATA_CACHE_LOCK:
        if cache_key in TWELVEDATA_CACHE:
            cached = TWELVEDATA_CACHE[cache_key]
            if now - cached['timestamp'] < TWELVEDATA_CACHE_TTL:
                return cached['data'], cached['is_fresh']

    # Circuit breaker quota - retourner cache périmé si disponible
    if check_quota_circuit_breaker():
        with TWELVEDATA_CACHE_LOCK:
            if cache_key in TWELVEDATA_CACHE:
                return TWELVEDATA_CACHE[cache_key]['data'], False
        return pd.DataFrame(), False

    # Rate limiting
    rate_limit_twelvedata()

    try:
        td_symbol, mic_code = convert_symbol_to_twelvedata(symbole)

        url = "https://api.twelvedata.com/time_series"
        params = {
            "symbol": td_symbol,
            "interval": interval,
            "outputsize": outputsize,
            "apikey": TWELVEDATA_API_KEY,
            "timezone": "Europe/Paris"
        }
        # Ajouter mic_code si spécifié (actions européennes)
        if mic_code:
            params["mic_code"] = mic_code

        response = requests.get(url, params=params, timeout=10)
        data = response.json()

        if "values" not in data:
            error_msg = data.get("message", "Erreur inconnue")
            error_code = data.get("code", 0)

            # Détecter quota exceeded et activer circuit breaker
            if "quota" in error_msg.lower() or "limit" in error_msg.lower() or error_code == 429:
                activate_quota_circuit_breaker()

            logger.warning(f"Twelve Data {symbole}: {error_msg}")
            return pd.DataFrame(), False

        # Convertir en DataFrame similaire à yfinance
        values = data["values"]
        df = pd.DataFrame(values)

        # Renommer et convertir les colonnes
        df = df.rename(columns={
            "datetime": "Date",
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume"
        })

        # Convertir les types
        for col in ["Open", "High", "Low", "Close"]:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        if "Volume" in df.columns:
            df["Volume"] = pd.to_numeric(df["Volume"], errors='coerce').fillna(0)

        # Parser les dates et mettre en index
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date")
        df = df.sort_index()  # Ordre chronologique

        # Vérifier fraîcheur des données
        is_fresh = False
        if not df.empty:
            last_date = df.index[-1].date()
            today = get_paris_time().date()
            weekday = today.weekday()

            if weekday < 5:  # Lundi-Vendredi
                days_diff = (today - last_date).days
                is_fresh = days_diff <= 1
            else:  # Weekend
                vendredi = today - timedelta(days=weekday - 4)
                is_fresh = last_date >= vendredi

        # Mettre en cache (thread-safe)
        with TWELVEDATA_CACHE_LOCK:
            TWELVEDATA_CACHE[cache_key] = {
                'data': df,
                'timestamp': now,
                'is_fresh': is_fresh
            }

        # Nettoyage périodique du cache
        cleanup_twelvedata_cache()

        return df, is_fresh

    except requests.exceptions.Timeout:
        print(f"⚠️ Timeout Twelve Data {symbole}")
        # Fallback sur cache périmé si disponible
        with TWELVEDATA_CACHE_LOCK:
            if cache_key in TWELVEDATA_CACHE:
                return TWELVEDATA_CACHE[cache_key]['data'], False
        return pd.DataFrame(), False
    except Exception as e:
        print(f"⚠️ Erreur Twelve Data {symbole}: {e}")
        return pd.DataFrame(), False

def get_twelvedata_quote(symbole):
    """Récupère le prix en temps réel via Twelve Data (avec cache)"""
    global TWELVEDATA_CACHE

    if not TWELVEDATA_API_KEY:
        return None

    cache_key = f"quote_{symbole}"
    now = time.time()

    # Vérifier le cache
    with TWELVEDATA_CACHE_LOCK:
        if cache_key in TWELVEDATA_CACHE:
            cached = TWELVEDATA_CACHE[cache_key]
            if now - cached['timestamp'] < TWELVEDATA_CACHE_TTL:
                return cached['data']

    # Circuit breaker quota
    if check_quota_circuit_breaker():
        with TWELVEDATA_CACHE_LOCK:
            if cache_key in TWELVEDATA_CACHE:
                return TWELVEDATA_CACHE[cache_key]['data']
        return None

    rate_limit_twelvedata()

    try:
        td_symbol, mic_code = convert_symbol_to_twelvedata(symbole)

        url = "https://api.twelvedata.com/quote"
        params = {
            "symbol": td_symbol,
            "apikey": TWELVEDATA_API_KEY
        }
        # Ajouter mic_code si spécifié (actions européennes)
        if mic_code:
            params["mic_code"] = mic_code

        response = requests.get(url, params=params, timeout=10)
        data = response.json()

        if "close" not in data:
            error_msg = data.get("message", "")
            if "quota" in error_msg.lower() or "limit" in error_msg.lower():
                activate_quota_circuit_breaker()
            return None

        result = {
            "price": float(data.get("close", 0)),
            "open": float(data.get("open", 0)),
            "high": float(data.get("high", 0)),
            "low": float(data.get("low", 0)),
            "previous_close": float(data.get("previous_close", 0)),
            "change": float(data.get("change", 0)),
            "percent_change": float(data.get("percent_change", 0)),
            "volume": int(data.get("volume", 0)) if data.get("volume") else 0
        }

        # Mettre en cache
        with TWELVEDATA_CACHE_LOCK:
            TWELVEDATA_CACHE[cache_key] = {
                'data': result,
                'timestamp': now
            }

        return result

    except Exception as e:
        print(f"⚠️ Erreur quote {symbole}: {e}")
        return None

def get_twelvedata_intraday(symbole, interval="5min", outputsize=288):
    """Récupère les données intraday (pour vérification trades).

    288 bougies de 5min = 24h de trading, permet de couvrir les trades overnight.
    """
    return get_twelvedata_time_series(symbole, outputsize=outputsize, interval=interval)

def get_fresh_quote_for_trade(symbole):
    """
    Récupère un prix FRAIS (sans cache) pour l'exécution d'un trade.

    IMPORTANT pour le trading:
    - Bypasse le cache pour avoir le prix le plus récent possible
    - Calcule le spread implicite (high-low du jour)
    - Vérifie la liquidité via le volume
    - Retourne des données formatées pour la prise de décision

    Returns:
        Dict avec prix frais et métriques, ou None si erreur
    """
    if not TWELVEDATA_API_KEY:
        return None

    # Bypass le cache - on veut des données fraîches
    rate_limit_twelvedata()

    try:
        td_symbol, mic_code = convert_symbol_to_twelvedata(symbole)

        url = "https://api.twelvedata.com/quote"
        params = {
            "symbol": td_symbol,
            "apikey": TWELVEDATA_API_KEY
        }
        if mic_code:
            params["mic_code"] = mic_code

        response = requests.get(url, params=params, timeout=10)
        data = response.json()

        if "close" not in data:
            error_msg = data.get("message", "")
            if "quota" in error_msg.lower():
                activate_quota_circuit_breaker()
            print(f"⚠️ Fresh quote {symbole}: {error_msg}")
            return None

        price = float(data.get("close", 0))
        high = float(data.get("high", 0))
        low = float(data.get("low", 0))
        open_price = float(data.get("open", 0))
        prev_close = float(data.get("previous_close", 0))
        volume = int(data.get("volume", 0)) if data.get("volume") else 0

        # Calculs pour le trading
        spread_pct = ((high - low) / price * 100) if price > 0 else 0
        variation_jour = ((price - prev_close) / prev_close * 100) if prev_close > 0 else 0
        variation_open = ((price - open_price) / open_price * 100) if open_price > 0 else 0

        return {
            "symbole": symbole,
            "prix": float(round(price, 4)),
            "open": float(round(open_price, 4)),
            "high": float(round(high, 4)),
            "low": float(round(low, 4)),
            "previous_close": float(round(prev_close, 4)),
            "volume": int(volume),
            "spread_pct": float(round(spread_pct, 2)),  # Volatilité intraday
            "variation_jour": float(round(variation_jour, 2)),  # % depuis clôture veille
            "variation_open": float(round(variation_open, 2)),  # % depuis open
            "timestamp": get_paris_time().isoformat(),
            "is_fresh": True
        }

    except Exception as e:
        print(f"⚠️ Fresh quote error {symbole}: {e}")
        return None

def valider_setup_avant_trade(symbole, direction, prix_entree_prevu, donnees_marche=None):
    """
    Valide qu'un setup de trade est toujours pertinent avant exécution.

    Vérifie:
    - Le prix actuel vs prix prévu (pas trop d'écart, dynamique selon actif)
    - La direction du mouvement intraday
    - Le volume (liquidité suffisante, sauf indices)
    - La volatilité (spread raisonnable)

    Args:
        symbole: Symbole de l'actif
        direction: 'LONG' ou 'SHORT'
        prix_entree_prevu: Prix auquel Claude a recommandé d'entrer
        donnees_marche: Données du cache (optionnel, pour ATR)

    Returns:
        (bool valide, str raison, dict quote_fraiche)
    """
    quote = get_fresh_quote_for_trade(symbole)

    if not quote:
        return False, "Impossible d'obtenir un prix frais", None

    prix_actuel = quote['prix']

    # Protection division par zéro
    if prix_entree_prevu <= 0:
        return False, f"Prix d'entrée prévu invalide ({prix_entree_prevu})", None

    ecart_pct = abs((prix_actuel - prix_entree_prevu) / prix_entree_prevu * 100)

    # Écart max DYNAMIQUE selon le type d'actif
    # Commodités très volatiles: tolérance plus élevée
    COMMODITES_VOLATILES = {"NG=F", "CC=F", "KC=F", "SI=F", "PL=F", "ZW=F", "ZC=F", "ZS=F", "SB=F"}
    COMMODITES_STANDARD = {"GC=F", "BZ=F", "CL=F", "HG=F"}

    if symbole in COMMODITES_VOLATILES:
        MAX_ECART_PCT = 2.5  # Gaz naturel, cacao, café très volatils
        MAX_SPREAD_PCT = 5.0
    elif symbole in COMMODITES_STANDARD:
        MAX_ECART_PCT = 1.5  # Or, pétrole moyennement volatils
        MAX_SPREAD_PCT = 4.0
    elif symbole.endswith("=X"):  # Forex
        MAX_ECART_PCT = 0.5  # Forex très stable
        MAX_SPREAD_PCT = 1.5
    else:
        MAX_ECART_PCT = 1.0  # Actions et indices: standard
        MAX_SPREAD_PCT = 3.0

    # 1. Vérifier l'écart de prix
    if ecart_pct > MAX_ECART_PCT:
        return False, f"Prix a bougé de {ecart_pct:.1f}% (max {MAX_ECART_PCT}%)", quote

    # 2. Vérifier le spread (volatilité excessive)
    if quote['spread_pct'] > MAX_SPREAD_PCT:
        return False, f"Spread trop large: {quote['spread_pct']:.1f}% (max {MAX_SPREAD_PCT}%)", quote

    # 3. Vérifier la cohérence direction/mouvement
    if direction == 'LONG' and quote['variation_open'] < -2.0:
        return False, f"Momentum baissier depuis open ({quote['variation_open']:.1f}%)", quote
    if direction == 'SHORT' and quote['variation_open'] > 2.0:
        return False, f"Momentum haussier depuis open ({quote['variation_open']:.1f}%)", quote

    # 4. Vérifier le volume (si disponible)
    # Exception: indices via Yahoo Finance peuvent avoir volume=0 (données agrégées)
    if quote['volume'] == 0 and symbole not in SYMBOLES_YAHOO_FALLBACK:
        return False, "Volume nul - possible problème de données", quote

    return True, "Setup validé", quote

# ============================================================================
# BATCH API - OPTIMISATION CHARGEMENT
# ============================================================================

def _fetch_twelvedata_group(mic_code, symbol_pairs, interval, outputsize):
    """Helper interne pour fetch un groupe de symboles Twelve Data (appelé en parallèle)"""
    results = {}
    now = time.time()
    BATCH_SIZE = 8

    for i in range(0, len(symbol_pairs), BATCH_SIZE):
        chunk = symbol_pairs[i:i + BATCH_SIZE]
        yahoo_symbols = [p[0] for p in chunk]
        td_symbols = [p[1] for p in chunk]

        rate_limit_twelvedata()

        try:
            url = "https://api.twelvedata.com/time_series"
            params = {
                "symbol": ",".join(td_symbols),
                "interval": interval,
                "outputsize": outputsize,
                "apikey": TWELVEDATA_API_KEY,
                "timezone": "Europe/Paris"
            }

            if mic_code != "NO_MIC":
                params["mic_code"] = mic_code

            response = requests.get(url, params=params, timeout=15)
            data = response.json()

            if "values" in data:
                batch_data = {td_symbols[0]: data}
            else:
                batch_data = data

            for yahoo_sym, td_sym in chunk:
                sym_data = batch_data.get(td_sym, {})

                if "values" not in sym_data:
                    error_msg = sym_data.get("message", "Pas de données")
                    if "quota" in str(error_msg).lower():
                        activate_quota_circuit_breaker()
                    results[yahoo_sym] = (pd.DataFrame(), False)
                    continue

                values = sym_data["values"]
                df = pd.DataFrame(values)
                df = df.rename(columns={
                    "datetime": "Date", "open": "Open", "high": "High",
                    "low": "Low", "close": "Close", "volume": "Volume"
                })

                for col in ["Open", "High", "Low", "Close"]:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                if "Volume" in df.columns:
                    df["Volume"] = pd.to_numeric(df["Volume"], errors='coerce').fillna(0)

                df["Date"] = pd.to_datetime(df["Date"])
                df = df.set_index("Date")
                df = df.sort_index()

                is_fresh = False
                if len(df) > 0:
                    last_date = df.index[-1]
                    if hasattr(last_date, 'date'):
                        last_date = last_date.date()
                    today = get_paris_time().date()
                    weekday = today.weekday()
                    if weekday == 0:
                        vendredi = today - timedelta(days=3)
                    elif weekday == 6:
                        vendredi = today - timedelta(days=2)
                    else:
                        vendredi = today - timedelta(days=1) if weekday > 0 else today
                    is_fresh = last_date >= vendredi

                cache_key = f"{yahoo_sym}_{interval}_{outputsize}"
                with TWELVEDATA_CACHE_LOCK:
                    TWELVEDATA_CACHE[cache_key] = {
                        'data': df, 'timestamp': now, 'is_fresh': is_fresh
                    }

                results[yahoo_sym] = (df, is_fresh)

        except Exception as e:
            for yahoo_sym, _ in chunk:
                results[yahoo_sym] = (pd.DataFrame(), False)

    return results

def get_twelvedata_batch(symboles_list, outputsize=30, interval="1day"):
    """
    Récupère les données historiques pour PLUSIEURS symboles via BATCH API.

    OPTIMISATIONS:
    - Twelve Data: 8 symboles par appel API (au lieu de 1)
    - Yahoo Finance: Tous les symboles en 1 appel
    - PARALLÈLE: Yahoo et Twelve Data chargés simultanément

    Args:
        symboles_list: Liste de symboles Yahoo Finance format
        outputsize: Nombre de barres (défaut 30 pour indicateurs)
        interval: Intervalle (1day, 1h, etc.)

    Returns:
        Dict {symbole: (DataFrame, is_fresh)} pour chaque symbole
    """
    global TWELVEDATA_CACHE

    if not TWELVEDATA_API_KEY:
        print("⚠️ TWELVEDATA_API_KEY non configurée")
        return {}

    results = {}
    now = time.time()

    # Séparer les symboles: cache, Yahoo Finance, Twelve Data
    symboles_to_fetch_td = []
    symboles_yahoo_fallback = []

    for symbole in symboles_list:
        # Yahoo Finance fallback
        if symbole in SYMBOLES_YAHOO_FALLBACK:
            # Vérifier cache Yahoo aussi
            cache_key = f"yf_{symbole}_{outputsize}"
            with TWELVEDATA_CACHE_LOCK:
                if cache_key in TWELVEDATA_CACHE:
                    cached = TWELVEDATA_CACHE[cache_key]
                    if now - cached['timestamp'] < TWELVEDATA_CACHE_TTL:
                        results[symbole] = (cached['data'], cached['is_fresh'])
                        continue
            symboles_yahoo_fallback.append(symbole)
            continue

        cache_key = f"{symbole}_{interval}_{outputsize}"
        with TWELVEDATA_CACHE_LOCK:
            if cache_key in TWELVEDATA_CACHE:
                cached = TWELVEDATA_CACHE[cache_key]
                if now - cached['timestamp'] < TWELVEDATA_CACHE_TTL:
                    results[symbole] = (cached['data'], cached['is_fresh'])
                    continue

        symboles_to_fetch_td.append(symbole)

    # Si rien à fetcher, retourner
    if not symboles_to_fetch_td and not symboles_yahoo_fallback:
        return results

    # Circuit breaker check pour Twelve Data
    td_circuit_break = check_quota_circuit_breaker()
    if td_circuit_break and symboles_to_fetch_td:
        for symbole in symboles_to_fetch_td:
            cache_key = f"{symbole}_{interval}_{outputsize}"
            with TWELVEDATA_CACHE_LOCK:
                if cache_key in TWELVEDATA_CACHE:
                    results[symbole] = (TWELVEDATA_CACHE[cache_key]['data'], False)
                else:
                    results[symbole] = (pd.DataFrame(), False)
        symboles_to_fetch_td = []  # Ne pas fetcher si circuit breaker

    # Grouper les symboles Twelve Data par MIC code
    groups = {}
    for symbole in symboles_to_fetch_td:
        td_symbol, mic_code = convert_symbol_to_twelvedata(symbole)
        key = mic_code or "NO_MIC"
        if key not in groups:
            groups[key] = []
        groups[key].append((symbole, td_symbol))

    # ========================================================================
    # CHARGEMENT PARALLÈLE: Yahoo Finance + Twelve Data simultanément
    # ========================================================================
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = []

        # 1. Lancer Yahoo Finance batch (1 seul appel pour tous les symboles)
        if symboles_yahoo_fallback:
            futures.append(executor.submit(
                get_yahoo_finance_batch, symboles_yahoo_fallback, outputsize
            ))

        # 2. Lancer Twelve Data par groupe MIC (en parallèle)
        for mic_code, symbol_pairs in groups.items():
            futures.append(executor.submit(
                _fetch_twelvedata_group, mic_code, symbol_pairs, interval, outputsize
            ))

        # 3. Collecter tous les résultats
        for future in as_completed(futures):
            try:
                batch_results = future.result()
                results.update(batch_results)
            except Exception as e:
                print(f"⚠️ Erreur future batch: {e}")

    return results

# ============================================================================
# INDICATEURS TECHNIQUES (RSI, MACD, ATR)
# ============================================================================

def calculer_rsi(df, periode=14):
    """
    Calcule le RSI (Relative Strength Index) sur un DataFrame
    RSI < 30 = survente (potentiel achat)
    RSI > 70 = surachat (potentiel vente)
    """
    if df.empty or len(df) < periode + 1:
        return None

    # Calcul des variations
    delta = df['Close'].diff()

    # Gains et pertes
    gains = delta.where(delta > 0, 0)
    losses = (-delta).where(delta < 0, 0)

    # Moyenne mobile exponentielle des gains/pertes
    avg_gain = gains.ewm(span=periode, adjust=False).mean()
    avg_loss = losses.ewm(span=periode, adjust=False).mean()

    # Éviter division par zéro
    avg_loss = avg_loss.replace(0, 0.0001)

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    return float(round(rsi.iloc[-1], 1))

def calculer_macd(df, fast=12, slow=26, signal=9):
    """
    Calcule le MACD (Moving Average Convergence Divergence)
    Retourne: (macd_line, signal_line, histogram, signal_type)
    signal_type: 'BULLISH' si MACD > Signal, 'BEARISH' sinon
    """
    if df.empty or len(df) < slow + signal:
        return None, None, None, None

    # EMA rapide et lente
    ema_fast = df['Close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['Close'].ewm(span=slow, adjust=False).mean()

    # Ligne MACD
    macd_line = ema_fast - ema_slow

    # Ligne de signal
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()

    # Histogramme
    histogram = macd_line - signal_line

    # Valeurs actuelles - conversion en float Python natif
    macd_val = float(round(macd_line.iloc[-1], 4))
    signal_val = float(round(signal_line.iloc[-1], 4))
    hist_val = float(round(histogram.iloc[-1], 4))

    # Signal
    signal_type = 'BULLISH' if macd_val > signal_val else 'BEARISH'

    # Croisement récent?
    if len(histogram) >= 2:
        prev_hist = histogram.iloc[-2]
        if prev_hist < 0 and hist_val > 0:
            signal_type = 'BULLISH_CROSS'  # Croisement haussier
        elif prev_hist > 0 and hist_val < 0:
            signal_type = 'BEARISH_CROSS'  # Croisement baissier

    return macd_val, signal_val, hist_val, signal_type

def calculer_atr(df, periode=14):
    """
    Calcule l'ATR (Average True Range) et l'ATR en pourcentage
    Retourne: (atr_valeur, atr_pct)
    """
    if df.empty or len(df) < periode + 1:
        return None, None

    # True Range
    high_low = df['High'] - df['Low']
    high_close = abs(df['High'] - df['Close'].shift())
    low_close = abs(df['Low'] - df['Close'].shift())

    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)

    # ATR = moyenne du True Range
    atr = tr.rolling(window=periode).mean().iloc[-1]

    # ATR en pourcentage du prix actuel
    prix_actuel = df['Close'].iloc[-1]
    atr_pct = (atr / prix_actuel) * 100 if prix_actuel > 0 else 0

    return float(round(atr, 4)), float(round(atr_pct, 2))

def calculer_trend(df):
    """
    Calcule la tendance basée sur EMA20 et structure des prix.
    Retourne: 'UP', 'DOWN', ou 'RANGE'
    """
    if df is None or df.empty or len(df) < 20:
        return 'RANGE'

    try:
        # EMA20
        ema20 = df['Close'].ewm(span=20, adjust=False).mean()
        prix_actuel = df['Close'].iloc[-1]
        ema_actuelle = ema20.iloc[-1]

        # Higher highs / Lower lows sur les 5 dernières bougies
        recent = df.tail(5)
        highs = recent['High'].values
        lows = recent['Low'].values

        higher_highs = all(highs[i] >= highs[i-1] for i in range(1, len(highs)))
        lower_lows = all(lows[i] <= lows[i-1] for i in range(1, len(lows)))

        # Tendance
        if prix_actuel > ema_actuelle and higher_highs:
            return 'UP'
        elif prix_actuel < ema_actuelle and lower_lows:
            return 'DOWN'
        else:
            return 'RANGE'
    except Exception:
        return 'RANGE'

def calculer_tendances_multi_tf(symbole):
    """
    Calcule les tendances sur plusieurs timeframes (Daily, H4, H1).
    Retourne: dict avec trend_daily, trend_h4, trend_h1, alignement_tf
    """
    trends = {
        'trend_daily': 'RANGE',
        'trend_h4': 'RANGE',
        'trend_h1': 'RANGE',
        'alignement_tf': 0
    }

    try:
        # Daily (30 bougies)
        df_daily, _ = get_twelvedata_time_series(symbole, outputsize=30, interval="1day")
        if not df_daily.empty:
            trends['trend_daily'] = calculer_trend(df_daily)

        # H4 (30 bougies = 5 jours)
        df_h4, _ = get_twelvedata_time_series(symbole, outputsize=30, interval="4h")
        if not df_h4.empty:
            trends['trend_h4'] = calculer_trend(df_h4)

        # H1 (24 bougies = 1 jour)
        df_h1, _ = get_twelvedata_time_series(symbole, outputsize=24, interval="1h")
        if not df_h1.empty:
            trends['trend_h1'] = calculer_trend(df_h1)

        # Calculer alignement (combien de TF sont dans la même direction)
        trend_values = [trends['trend_daily'], trends['trend_h4'], trends['trend_h1']]
        up_count = trend_values.count('UP')
        down_count = trend_values.count('DOWN')
        trends['alignement_tf'] = max(up_count, down_count)

    except Exception as e:
        print(f"⚠️ Erreur calcul tendances multi-TF {symbole}: {e}")

    return trends

def calculer_indicateurs_complets(symbole):
    """
    Calcule tous les indicateurs techniques pour un symbole
    Retourne un dictionnaire avec RSI, MACD, ATR
    """
    # Récupérer les données daily pour les indicateurs (30 jours = même cache que recuperer_donnees_marche)
    df, is_fresh = get_twelvedata_time_series(symbole, outputsize=30, interval="1day")

    if df.empty:
        return {
            'rsi': None,
            'macd': None,
            'macd_signal': None,
            'macd_histogram': None,
            'macd_interpretation': None,
            'atr': None,
            'atr_pct': None,
            'is_fresh': False
        }

    rsi = calculer_rsi(df)
    macd_val, signal_val, hist_val, macd_type = calculer_macd(df)
    atr_val, atr_pct = calculer_atr(df)

    # Interprétations
    rsi_interpretation = None
    if rsi is not None:
        if rsi < 30:
            rsi_interpretation = 'SURVENTE'
        elif rsi > 70:
            rsi_interpretation = 'SURACHAT'
        else:
            rsi_interpretation = 'NEUTRE'

    # Calcul des tendances multi-timeframe (H1, H4, Daily)
    tendances = calculer_tendances_multi_tf(symbole)

    return {
        'rsi': rsi,
        'rsi_interpretation': rsi_interpretation,
        'macd': macd_val,
        'macd_signal': signal_val,
        'macd_histogram': hist_val,
        'macd_interpretation': macd_type,
        'atr': atr_val,
        'atr_pct': atr_pct,
        'is_fresh': is_fresh,
        'trend_daily': tendances['trend_daily'],
        'trend_h4': tendances['trend_h4'],
        'trend_h1': tendances['trend_h1'],
        'alignement_tf': tendances['alignement_tf']
    }

def enrichir_donnees_avec_indicateurs(donnees_marche):
    """
    Enrichit les données de marché avec les indicateurs techniques calculés.
    Calcule RSI/MACD pour TOUS les actifs présents dans donnees_marche.
    Structure attendue: {"CAC 40": {"symbole": "^FCHI", ...}, "Apple": {...}}
    """
    donnees_enrichies = donnees_marche.copy() if isinstance(donnees_marche, dict) else {}

    # Ajouter une section indicateurs
    donnees_enrichies['indicateurs_calcules'] = {}

    # Calculer les indicateurs pour TOUS les actifs (structure plate)
    for nom_actif, data in donnees_marche.items():
        # Ignorer les entrées qui ne sont pas des données d'actif
        if not isinstance(data, dict) or 'symbole' not in data:
            continue

        symbole = data.get('symbole')
        if not symbole:
            continue

        try:
            # Calculer les indicateurs (utilise le cache si disponible)
            indicateurs = calculer_indicateurs_complets(symbole)

            donnees_enrichies['indicateurs_calcules'][nom_actif] = {
                'symbole': symbole,
                'RSI': indicateurs.get('rsi'),
                'RSI_signal': indicateurs.get('rsi_interpretation'),
                'MACD': indicateurs.get('macd_interpretation'),
                'ATR_pct': indicateurs.get('atr_pct'),
                'trend_daily': indicateurs.get('trend_daily'),
                'trend_h4': indicateurs.get('trend_h4'),
                'trend_h1': indicateurs.get('trend_h1'),
                'alignement_tf': indicateurs.get('alignement_tf')
            }

            # Enrichir aussi directement les données de l'actif
            if nom_actif in donnees_enrichies and isinstance(donnees_enrichies[nom_actif], dict):
                donnees_enrichies[nom_actif]['rsi'] = indicateurs.get('rsi')
                donnees_enrichies[nom_actif]['rsi_signal'] = indicateurs.get('rsi_interpretation')
                donnees_enrichies[nom_actif]['macd_signal'] = indicateurs.get('macd_interpretation')
                donnees_enrichies[nom_actif]['trend_daily'] = indicateurs.get('trend_daily')
                donnees_enrichies[nom_actif]['trend_h4'] = indicateurs.get('trend_h4')
                donnees_enrichies[nom_actif]['trend_h1'] = indicateurs.get('trend_h1')
                donnees_enrichies[nom_actif]['alignement_tf'] = indicateurs.get('alignement_tf')

        except Exception as e:
            print(f"⚠️ Erreur indicateurs {symbole}: {e}")

    return donnees_enrichies

# ============================================================================
# VALIDATION JSON ET OPPORTUNITÉS
# ============================================================================

def extraire_json_claude(reponse):
    """
    Extrait et valide le JSON de la réponse Claude de manière robuste.

    Problème résolu: la regex \{[\s\S]*\} capture tout entre le premier
    et dernier {}, ce qui peut inclure du texte invalide.

    Retourne: (dict, erreur) - dict si succès, None + message si échec
    """
    if not reponse:
        return None, "Réponse vide"

    # Nettoyer la réponse (enlever markdown code blocks si présents)
    reponse_clean = reponse.strip()
    if reponse_clean.startswith('```json'):
        reponse_clean = reponse_clean[7:]
    if reponse_clean.startswith('```'):
        reponse_clean = reponse_clean[3:]
    if reponse_clean.endswith('```'):
        reponse_clean = reponse_clean[:-3]
    reponse_clean = reponse_clean.strip()

    # Essai 1: Parser directement si c'est du JSON pur
    if reponse_clean.startswith('{'):
        try:
            # Trouver la fin du JSON en comptant les accolades
            depth = 0
            end_idx = 0
            for i, char in enumerate(reponse_clean):
                if char == '{':
                    depth += 1
                elif char == '}':
                    depth -= 1
                    if depth == 0:
                        end_idx = i + 1
                        break

            if end_idx > 0:
                json_str = reponse_clean[:end_idx]
                result = json.loads(json_str)
                return result, None
        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse error (essai 1): {e}")

    # Essai 2: Chercher un bloc JSON dans le texte
    try:
        # Trouver le premier { et son } correspondant
        start_idx = reponse_clean.find('{')
        if start_idx == -1:
            return None, "Aucun JSON trouvé dans la réponse"

        depth = 0
        end_idx = 0
        for i in range(start_idx, len(reponse_clean)):
            char = reponse_clean[i]
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    end_idx = i + 1
                    break

        if end_idx > start_idx:
            json_str = reponse_clean[start_idx:end_idx]
            result = json.loads(json_str)
            return result, None
    except json.JSONDecodeError as e:
        return None, f"JSON invalide: {e}"

    return None, "Impossible d'extraire le JSON"


def valider_structure_analyse(result):
    """
    Valide que le JSON d'analyse contient tous les champs requis.

    Retourne: (bool, list_erreurs)
    """
    erreurs = []

    # Champs obligatoires niveau racine
    champs_requis = ['contexte_marche', 'opportunites']
    for champ in champs_requis:
        if champ not in result:
            erreurs.append(f"Champ manquant: {champ}")

    # Vérifier que opportunites est une liste
    if 'opportunites' in result:
        if not isinstance(result['opportunites'], list):
            erreurs.append("'opportunites' doit être une liste")

    return len(erreurs) == 0, erreurs


def valider_opportunite(opp):
    """
    Valide une opportunité individuelle de manière stricte.

    Retourne: (bool valide, dict opp_corrigee, list avertissements, list erreurs_bloquantes)
    """
    avertissements = []
    erreurs = []

    # Champs obligatoires
    champs_requis = ['entree', 'stop', 'tp1', 'direction']
    for champ in champs_requis:
        if champ not in opp or opp[champ] is None:
            erreurs.append(f"Champ obligatoire manquant: {champ}")

    if erreurs:
        return False, None, avertissements, erreurs

    # Conversion et validation des prix
    try:
        entree = float(opp.get('entree', 0) or 0)
        stop = float(opp.get('stop', 0) or 0)
        tp1 = float(opp.get('tp1', 0) or 0)
        tp2 = float(opp.get('tp2', 0) or 0)
    except (ValueError, TypeError) as e:
        erreurs.append(f"Conversion prix impossible: {e}")
        return False, None, avertissements, erreurs

    # Validation prix > 0
    if entree <= 0:
        erreurs.append(f"Prix entrée invalide: {entree}")
    if stop <= 0:
        erreurs.append(f"Prix stop invalide: {stop}")
    if tp1 <= 0:
        erreurs.append(f"Prix TP1 invalide: {tp1}")

    if erreurs:
        return False, None, avertissements, erreurs

    # Validation direction
    direction = str(opp.get('direction', '')).upper().strip()
    if direction not in ['LONG', 'SHORT']:
        erreurs.append(f"Direction invalide: '{opp.get('direction')}' (attendu: LONG ou SHORT)")
        return False, None, avertissements, erreurs

    # Validation cohérence STOP/ENTREE/TP (REJET au lieu d'auto-correction)
    if direction == 'LONG':
        if stop >= entree:
            erreurs.append(f"LONG incohérent: stop ({stop}) >= entrée ({entree})")
        if tp1 <= entree:
            erreurs.append(f"LONG incohérent: TP1 ({tp1}) <= entrée ({entree})")
        if tp2 > 0 and tp2 <= tp1:
            avertissements.append(f"TP2 ({tp2}) <= TP1 ({tp1}), TP2 ignoré")
    else:  # SHORT
        if stop <= entree:
            erreurs.append(f"SHORT incohérent: stop ({stop}) <= entrée ({entree})")
        if tp1 >= entree:
            erreurs.append(f"SHORT incohérent: TP1 ({tp1}) >= entrée ({entree})")
        if tp2 > 0 and tp2 >= tp1:
            avertissements.append(f"TP2 ({tp2}) >= TP1 ({tp1}), TP2 ignoré")

    if erreurs:
        return False, None, avertissements, erreurs

    # Calcul et validation du ratio R:R
    if direction == 'LONG':
        risque = entree - stop
        reward = tp1 - entree
    else:
        risque = stop - entree
        reward = entree - tp1

    if risque <= 0:
        erreurs.append(f"Risque invalide: {risque}")
        return False, None, avertissements, erreurs

    ratio_rr = reward / risque

    # R:R minimum adapté au régime de marché (VIX)
    regime_marche = opp.get('regime_marche', 'NORMAL')
    rr_min_par_regime = {
        'CALME': 1.3,      # VIX < 15: marché calme, R:R plus souple
        'NORMAL': 1.5,     # VIX 15-20: conditions standard
        'VOLATILE': 1.8,   # VIX 20-30: exiger plus de reward pour le risque
        'EXTREME': 2.0     # VIX > 30: très sélectif
    }
    rr_minimum = rr_min_par_regime.get(regime_marche, 1.5)

    if ratio_rr < rr_minimum:
        erreurs.append(f"Ratio R:R insuffisant: {ratio_rr:.2f} (minimum {rr_minimum} en régime {regime_marche})")
        return False, None, avertissements, erreurs

    # Opportunité valide - construire la version corrigée
    opp_valide = opp.copy()
    opp_valide['direction'] = direction
    opp_valide['ratio_rr_calcule'] = round(ratio_rr, 2)
    opp_valide['rr_minimum_applique'] = rr_minimum

    return True, opp_valide, avertissements, []


def analyser_cloture_intraday(hist, direction, entree, stop, tp1, tp2):
    """
    Analyse la clôture d'un trade en tenant compte:
    - De l'ordre chronologique des bougies
    - Du slippage potentiel (prix réel vs niveau théorique)
    - De la priorité temporelle intra-bougie

    Retourne: (resultat, prix_sortie, pnl_pct, bougie_idx) ou (None, None, None, None)
    """
    if hist is None or hist.empty:
        return None, None, None, None

    # Protection division par zéro
    if entree <= 0:
        return None, None, None, None

    # S'assurer que les bougies sont triées chronologiquement
    hist = hist.sort_index()

    for idx, row in hist.iterrows():
        high = row['High']
        low = row['Low']
        open_p = row['Open']
        close_p = row['Close']

        if direction == 'LONG':
            # Pour un LONG: Stop si Low <= stop, TP si High >= tp
            stop_touche = stop > 0 and low <= stop
            tp2_touche = tp2 > 0 and high >= tp2
            tp1_touche = tp1 > 0 and high >= tp1

            # Déterminer l'ordre probable intra-bougie
            # Si les deux sont touchés dans la même bougie, utiliser Open->Close
            if stop_touche and (tp1_touche or tp2_touche):
                # Heuristique: si Close > Open, le mouvement principal était haussier
                # donc TP probablement touché avant le dump final
                if close_p > open_p:
                    # Mouvement haussier: TP probablement d'abord
                    if tp2_touche:
                        return 'TP2', tp2, ((tp2 - entree) / entree) * 100, idx
                    return 'TP1', tp1, ((tp1 - entree) / entree) * 100, idx
                else:
                    # Mouvement baissier: Stop probablement d'abord
                    # Slippage: utiliser le Low réel si < stop
                    prix_sortie_reel = min(low, stop)
                    return 'STOP', prix_sortie_reel, ((prix_sortie_reel - entree) / entree) * 100, idx

            # Un seul niveau touché
            if stop_touche:
                prix_sortie_reel = min(low, stop)  # Slippage potentiel
                return 'STOP', prix_sortie_reel, ((prix_sortie_reel - entree) / entree) * 100, idx
            if tp2_touche:
                return 'TP2', tp2, ((tp2 - entree) / entree) * 100, idx
            if tp1_touche:
                return 'TP1', tp1, ((tp1 - entree) / entree) * 100, idx

        else:  # SHORT
            stop_touche = stop > 0 and high >= stop
            tp2_touche = tp2 > 0 and low <= tp2
            tp1_touche = tp1 > 0 and low <= tp1

            if stop_touche and (tp1_touche or tp2_touche):
                if close_p < open_p:
                    # Mouvement baissier: TP probablement d'abord
                    if tp2_touche:
                        return 'TP2', tp2, ((entree - tp2) / entree) * 100, idx
                    return 'TP1', tp1, ((entree - tp1) / entree) * 100, idx
                else:
                    # Mouvement haussier: Stop probablement d'abord
                    prix_sortie_reel = max(high, stop)
                    return 'STOP', prix_sortie_reel, ((entree - prix_sortie_reel) / entree) * 100, idx

            if stop_touche:
                prix_sortie_reel = max(high, stop)  # Slippage potentiel
                return 'STOP', prix_sortie_reel, ((entree - prix_sortie_reel) / entree) * 100, idx
            if tp2_touche:
                return 'TP2', tp2, ((entree - tp2) / entree) * 100, idx
            if tp1_touche:
                return 'TP1', tp1, ((entree - tp1) / entree) * 100, idx

    return None, None, None, None


# ============================================================================
# BASE DE DONNÉES
# ============================================================================

def init_database():
    """Initialise la base de données SQLite complète"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Table des trades recommandés
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trades_recommandes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date DATE,
            heure_message TEXT,
            actif TEXT,
            symbole TEXT,
            type_setup TEXT,
            prix_entree REAL,
            prix_stop REAL,
            prix_tp1 REAL,
            prix_tp2 REAL,
            prix_actuel REAL,
            catalyseur TEXT,
            duree_estimee TEXT,
            timestamp_reco DATETIME,
            resultat TEXT,
            prix_sortie REAL,
            pnl_pct REAL,
            timestamp_sortie DATETIME,
            notes TEXT
        )
    ''')

    # Table des analyses (pré-market, intraday, clôture)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date DATE,
            type_analyse TEXT,
            heure TEXT,
            contenu TEXT,
            timestamp DATETIME,
            contexte_marche TEXT
        )
    ''')

    # Table du journal des actifs (mémoire)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS journal_actifs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date DATE,
            symbole TEXT,
            nom_actif TEXT,
            type_info TEXT,
            titre TEXT,
            contenu TEXT,
            impact_cours TEXT,
            importance INTEGER,
            timestamp DATETIME
        )
    ''')

    # Table des alertes news
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS alertes_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date DATE,
            heure TEXT,
            titre TEXT,
            contenu TEXT,
            actif TEXT,
            symbole TEXT,
            impact TEXT,
            timestamp DATETIME
        )
    ''')

    # Table des actifs suivis personnalisés
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS actifs_suivis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbole TEXT UNIQUE,
            nom TEXT,
            categorie TEXT,
            notes TEXT,
            date_ajout DATETIME,
            actif BOOLEAN DEFAULT 1
        )
    ''')

    # Table des statistiques quotidiennes
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stats_quotidiennes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date DATE UNIQUE,
            nb_trades INTEGER,
            nb_reussis INTEGER,
            nb_stops INTEGER,
            nb_non_conclus INTEGER,
            pnl_total REAL,
            meilleur_trade TEXT,
            pire_trade TEXT,
            notes TEXT
        )
    ''')

    # Table du journal quotidien automatique
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS journal_quotidien (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date DATE,
            symbole TEXT,
            nom_actif TEXT,
            categorie TEXT,
            prix_ouverture REAL,
            prix_cloture REAL,
            prix_max REAL,
            prix_min REAL,
            variation_jour REAL,
            volume_relatif REAL,
            commentaire_ia TEXT,
            evenements_jour TEXT,
            opportunites_jour TEXT,
            recommandations_analystes TEXT,
            timestamp DATETIME,
            UNIQUE(date, symbole)
        )
    ''')

    # Table pour le bilan général quotidien
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bilan_quotidien (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date DATE UNIQUE,
            contenu TEXT,
            points_positifs TEXT,
            points_negatifs TEXT,
            lecons_apprises TEXT,
            faits_marquants TEXT,
            timestamp DATETIME
        )
    ''')

    # Table pour les rapports hebdomadaires
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS rapports_hebdo (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            semaine TEXT UNIQUE,
            date_debut DATE,
            date_fin DATE,
            resume_executif TEXT,
            chiffres_cles TEXT,
            forces TEXT,
            faiblesses TEXT,
            patterns TEXT,
            ajustements TEXT,
            scores_confiance TEXT,
            focus_semaine TEXT,
            timestamp DATETIME
        )
    ''')

    # Table pour les critères dynamiques
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS criteres_dynamiques (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_maj DATE,
            categorie TEXT,
            critere TEXT,
            valeur_actuelle REAL,
            valeur_precedente REAL,
            raison TEXT,
            timestamp DATETIME
        )
    ''')

    # Table pour les ajustements en attente de validation
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ajustements_proposes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type_ajustement TEXT,
            categorie TEXT,
            critere TEXT,
            action TEXT,
            valeur_proposee TEXT,
            raison TEXT,
            source TEXT,
            statut TEXT DEFAULT 'en_attente',
            date_proposition DATETIME,
            date_decision DATETIME,
            decideur TEXT
        )
    ''')

    # Colonnes feedback loop pour ajustements_proposes
    colonnes_feedback = [
        ("perf_avant_nb_trades", "INTEGER"),
        ("perf_avant_taux_reussite", "REAL"),
        ("perf_avant_pnl_total", "REAL"),
        ("perf_apres_nb_trades", "INTEGER"),
        ("perf_apres_taux_reussite", "REAL"),
        ("perf_apres_pnl_total", "REAL"),
        ("feedback_date", "DATETIME"),
        ("feedback_conclusion", "TEXT"),
        ("commentaire_utilisateur", "TEXT")  # Commentaire libre de l'utilisateur
    ]
    for col_nom, col_type in colonnes_feedback:
        try:
            cursor.execute(f"ALTER TABLE ajustements_proposes ADD COLUMN {col_nom} {col_type}")
        except sqlite3.OperationalError:
            pass

    # Ajouter colonnes à trades_recommandes si manquantes (ignore si déjà existantes)
    colonnes_trades = [
        ("direction", "TEXT DEFAULT 'LONG'"),
        ("categorie_actif", "TEXT"),
        ("duree_minutes", "INTEGER"),
        ("heure_entree", "INTEGER"),
        # Colonnes pour suivi temps réel
        ("prix_max_atteint", "REAL"),       # Plus haut atteint pendant le trade
        ("prix_min_atteint", "REAL"),       # Plus bas atteint pendant le trade
        ("prix_dernier_check", "REAL"),     # Dernier prix vérifié
        ("timestamp_dernier_check", "DATETIME"),  # Quand
        ("pnl_max", "REAL"),                # PnL max atteint (%)
        ("pnl_min", "REAL"),                # PnL min atteint (drawdown %)
        ("nb_checks", "INTEGER DEFAULT 0"), # Nombre de vérifications
        # Indicateurs techniques au moment de la recommandation (pour analyse performance)
        ("rsi_reco", "REAL"),               # RSI au moment de la reco (0-100)
        ("rsi_signal_reco", "TEXT"),        # SURVENTE/NEUTRE/SURACHAT
        ("macd_signal_reco", "TEXT"),       # BULLISH/BEARISH/BULLISH_CROSS/BEARISH_CROSS
        ("atr_pct_reco", "REAL"),           # ATR% au moment de la reco
        ("volume_relatif_reco", "REAL"),    # Volume relatif au moment de la reco
        # Niveaux techniques au moment de la recommandation
        ("pivot_reco", "REAL"),             # Pivot Point
        ("support1_reco", "REAL"),          # Support 1
        ("resistance1_reco", "REAL"),       # Resistance 1
        # Ratio Risk/Reward
        ("ratio_rr", "TEXT"),               # Ex: "1:1.5", "1:2", "1:3"
        ("ratio_rr_justification", "TEXT"), # Justification du ratio choisi
        # A/B Testing - traçabilité des tests
        ("ab_test_id", "INTEGER"),          # ID du test A/B (FK vers ab_tests)
        ("ab_groupe", "TEXT"),              # 'A' ou 'B'
        ("ab_variante", "TEXT"),            # Description de la variante testée
        # === NOUVELLES COLONNES A/B TESTING AVANCÉ ===
        # 1. Timing Entry/Exit
        ("strategie_entree", "TEXT"),       # IMMEDIATE / PULLBACK / BREAKOUT / LIMIT
        ("trailing_stop", "INTEGER DEFAULT 0"),  # 0=non, 1=oui
        ("trailing_stop_pct", "REAL"),      # % de trailing (ex: 0.3 = 0.3%)
        ("prix_limite_entree", "REAL"),     # Prix limite si strategie=LIMIT
        # 2. Contexte Marché Dynamique
        ("vix_niveau", "REAL"),             # VIX au moment de la reco
        ("regime_marche", "TEXT"),          # CALME (<15) / NORMAL (15-20) / VOLATILE (20-30) / EXTREME (>30)
        ("regles_adaptees", "TEXT"),        # Description des règles adaptées au régime
        # 3. Time Decay / Urgence
        ("validite_minutes", "INTEGER"),    # Fenêtre de validité en minutes
        ("heure_expiration", "TEXT"),       # Heure d'expiration CET (ex: "10:30")
        ("urgence", "TEXT"),                # HAUTE / MOYENNE / BASSE
        ("est_expire", "INTEGER DEFAULT 0"), # 0=valide, 1=expiré sans entrée
        # 4. Multi Timeframe
        ("trend_daily", "TEXT"),            # UP / DOWN / RANGE
        ("trend_h4", "TEXT"),               # UP / DOWN / RANGE
        ("trend_h1", "TEXT"),               # UP / DOWN / RANGE (timeframe principal)
        ("alignement_tf", "INTEGER"),       # 0-3 (nb de TF alignés)
        ("confluence_score", "INTEGER"),    # 0-10 score de confluence technique
        # 5. Analyse Sentiment
        ("sentiment_score", "REAL"),        # -5 (très bearish) à +5 (très bullish)
        ("sentiment_source", "TEXT"),       # NEWS / TECHNIQUE / FLOW / MIXTE
        ("sentiment_detail", "TEXT"),       # Explication du sentiment
        # 6. Suivi Intraday Actif
        ("statut_intraday", "TEXT DEFAULT 'EN_COURS'"),  # EN_COURS / TP50_ATTEINT / STOP_AJUSTE / CLOTURE
        ("stop_ajuste", "REAL"),            # Nouveau stop après ajustement (breakeven, trailing)
        ("reevaluations", "TEXT"),          # JSON des réévaluations intraday
        ("nb_reevaluations", "INTEGER DEFAULT 0"),  # Nombre de réévaluations
        ("action_recommandee", "TEXT"),     # HOLD / RENFORCER / REDUIRE / SORTIR
        # 7. Saisonnalité
        ("jour_semaine", "TEXT"),           # LUNDI / MARDI / MERCREDI / JEUDI / VENDREDI
        ("session_marche", "TEXT"),         # EU_OPEN / US_PREMARKET / US_OPEN / EU_CLOSE / US_CLOSE
        ("pattern_jour", "TEXT"),           # Pattern historique du jour (ex: "Lundi souvent haussier")
        # Score de conviction global
        ("conviction_score", "INTEGER"),    # 1-5 (5 = très haute conviction)
        # 8. Trade Quality Grading
        ("trade_grade", "TEXT"),            # A/B/C/D - Note qualité du setup
        ("grade_details", "TEXT"),          # JSON détail des critères de notation
        ("grade_entry_score", "INTEGER"),   # Score qualité entrée (0-100)
        ("grade_exit_score", "INTEGER"),    # Score qualité sortie (0-100, calculé après clôture)
        ("grade_setup_score", "INTEGER")    # Score qualité setup (0-100)
    ]
    for col_nom, col_type in colonnes_trades:
        try:
            cursor.execute(f"ALTER TABLE trades_recommandes ADD COLUMN {col_nom} {col_type}")
        except sqlite3.OperationalError:
            pass  # Colonne existe déjà

    # Ajouter colonnes au journal_quotidien si manquantes
    colonnes_journal = [
        ("prix_max", "REAL"),
        ("prix_min", "REAL"),
        ("recommandations_analystes", "TEXT")
    ]
    for col_nom, col_type in colonnes_journal:
        try:
            cursor.execute(f"ALTER TABLE journal_quotidien ADD COLUMN {col_nom} {col_type}")
        except sqlite3.OperationalError:
            pass  # Colonne existe déjà

    try:
        cursor.execute("ALTER TABLE bilan_quotidien ADD COLUMN faits_marquants TEXT")
    except sqlite3.OperationalError:
        pass  # Colonne existe déjà

    # Ajouter colonne audit trail pour lier critères à leur source
    try:
        cursor.execute("ALTER TABLE criteres_dynamiques ADD COLUMN ajustement_source_id INTEGER")
    except sqlite3.OperationalError:
        pass  # Colonne existe déjà

    conn.commit()
    conn.close()
    print("✅ Base de données initialisée")

    # Créer les index pour optimiser les requêtes fréquentes
    create_database_indexes()

def create_database_indexes():
    """Crée les index SQL pour optimiser les performances des requêtes fréquentes"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    indexes = [
        # Index trades_recommandes - colonnes les plus utilisées
        ("idx_trades_date", "trades_recommandes", "date"),
        ("idx_trades_symbole", "trades_recommandes", "symbole"),
        ("idx_trades_resultat", "trades_recommandes", "resultat"),
        ("idx_trades_date_resultat", "trades_recommandes", "date, resultat"),
        ("idx_trades_date_symbole", "trades_recommandes", "date, symbole"),
        ("idx_trades_conviction", "trades_recommandes", "conviction_score"),
        ("idx_trades_regime", "trades_recommandes", "regime_marche"),
        ("idx_trades_strategie", "trades_recommandes", "strategie_entree"),
        ("idx_trades_statut", "trades_recommandes", "statut_intraday"),
        ("idx_trades_ab_test", "trades_recommandes", "ab_test_id"),
        # Index analyses
        ("idx_analyses_date", "analyses", "date"),
        ("idx_analyses_type", "analyses", "type"),
        # Index journal_quotidien
        ("idx_journal_date", "journal_quotidien", "date"),
        ("idx_journal_symbole", "journal_quotidien", "symbole"),
        # Index bilan_quotidien
        ("idx_bilan_date", "bilan_quotidien", "date"),
        # Index alertes_news
        ("idx_news_date", "alertes_news", "date"),
        # Index criteres_dynamiques
        ("idx_criteres_date", "criteres_dynamiques", "date_maj"),
        # Index ajustements_proposes
        ("idx_ajustements_statut", "ajustements_proposes", "statut"),
        ("idx_ajustements_date", "ajustements_proposes", "date_proposition"),
        # Index ab_tests
        ("idx_ab_tests_statut", "ab_tests", "statut"),
    ]

    for idx_name, table, columns in indexes:
        try:
            cursor.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({columns})")
        except sqlite3.OperationalError as e:
            pass  # Index existe déjà ou table n'existe pas encore

    conn.commit()
    conn.close()
    print("✅ Index SQL créés/vérifiés")

def backup_database():
    """Crée une sauvegarde horodatée de la base de données"""
    import shutil

    if not os.path.exists(DB_PATH):
        print("⚠️ Base de données non trouvée, backup ignoré")
        return None

    # Créer le dossier backups si nécessaire
    backup_dir = os.path.join(os.path.dirname(DB_PATH) or '.', 'backups')
    os.makedirs(backup_dir, exist_ok=True)

    # Nom du backup avec date
    maintenant = datetime.now()
    backup_name = f"trading_backup_{maintenant.strftime('%Y%m%d_%H%M%S')}.db"
    backup_path = os.path.join(backup_dir, backup_name)

    try:
        # Copie sécurisée (avec flush SQLite)
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # Flush WAL si utilisé
        conn.close()

        shutil.copy2(DB_PATH, backup_path)

        # Nettoyer les anciens backups (garder les 7 derniers)
        cleanup_old_backups(backup_dir, keep=7)

        print(f"✅ Backup créé: {backup_name}")
        return backup_path
    except Exception as e:
        logger.error(f"Erreur backup: {e}")
        return None

def cleanup_old_backups(backup_dir, keep=7):
    """Supprime les backups anciens, garde les N plus récents"""
    try:
        backups = sorted([
            os.path.join(backup_dir, f)
            for f in os.listdir(backup_dir)
            if f.startswith('trading_backup_') and f.endswith('.db')
        ], key=os.path.getmtime, reverse=True)

        # Supprimer les plus vieux
        for old_backup in backups[keep:]:
            os.remove(old_backup)
            print(f"🗑️ Ancien backup supprimé: {os.path.basename(old_backup)}")
    except Exception as e:
        print(f"⚠️ Erreur nettoyage backups: {e}")

def load_vix_cache():
    """Charge le cache VIX depuis le fichier persistant"""
    global VIX_CACHE
    try:
        cache_path = os.path.join(os.path.dirname(DB_PATH) or '.', VIX_CACHE_FILE)
        if os.path.exists(cache_path):
            with open(cache_path, 'r') as f:
                cached = json.load(f)
                # Vérifier si le cache n'est pas trop vieux (max 1h)
                if time.time() - cached.get('timestamp', 0) < 3600:
                    VIX_CACHE = cached
                    print(f"✅ Cache VIX chargé: {VIX_CACHE['value']:.1f} ({VIX_CACHE['source']})")
    except Exception as e:
        print(f"⚠️ Erreur chargement cache VIX: {e}")

def save_vix_cache():
    """Sauvegarde le cache VIX dans un fichier persistant"""
    try:
        cache_path = os.path.join(os.path.dirname(DB_PATH) or '.', VIX_CACHE_FILE)
        with open(cache_path, 'w') as f:
            json.dump(VIX_CACHE, f)
    except Exception as e:
        print(f"⚠️ Erreur sauvegarde cache VIX: {e}")

# ============================================================================
# FONCTIONS UTILITAIRES
# ============================================================================

def get_paris_time():
    """Retourne l'heure actuelle à Paris"""
    return datetime.now(TZ_PARIS)

def get_utc_time_for_paris(heure_paris):
    """Convertit une heure française en heure UTC"""
    maintenant = get_paris_time()
    # Validation du format HH:MM
    try:
        parts = heure_paris.split(':')
        if len(parts) < 2:
            raise ValueError(f"Format heure invalide: {heure_paris}")
        hour = int(parts[0])
        minute = int(parts[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"Heure hors limites: {heure_paris}")
    except (ValueError, AttributeError) as e:
        logger.warning(f"Format heure invalide '{heure_paris}': {e}, utilisation 09:00 par défaut")
        hour, minute = 9, 0

    heure_cible = maintenant.replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0
    )
    heure_utc = heure_cible.astimezone(pytz.UTC)
    return heure_utc.strftime('%H:%M')

def is_weekend():
    """Vérifie si c'est le weekend (samedi ou dimanche)"""
    maintenant = get_paris_time()
    return maintenant.weekday() >= 5  # 5=samedi, 6=dimanche

def get_jour_semaine_nom():
    """Retourne le nom du jour en français"""
    jours = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche']
    return jours[get_paris_time().weekday()]

def get_market_context():
    """Détermine quels marchés sont ouverts"""
    maintenant = get_paris_time()

    # Weekend = marchés fermés
    if is_weekend():
        return "WEEKEND", "Marchés fermés (weekend). Forex limité."

    heure_decimal = maintenant.hour + maintenant.minute / 60

    if 9 <= heure_decimal < 15.5:
        return "EUROPE", "Marchés européens ouverts. US fermés."
    elif 15.5 <= heure_decimal < 17.5:
        return "EUROPE_US", "Marchés EU et US ouverts (chevauchement)."
    elif 17.5 <= heure_decimal < 22:
        return "US", "Marchés EU fermés. US ouverts."
    else:
        return "FERME", "Marchés principaux fermés. Forex/Commodités 24h."

def get_vix_level():
    """Récupère le niveau actuel du VIX avec cache persistant et fallback robuste"""
    global VIX_CACHE

    # Vérifier si le cache est encore valide
    if time.time() - VIX_CACHE.get('timestamp', 0) < VIX_CACHE_TTL:
        return VIX_CACHE['value']

    # Essayer Yahoo Finance d'abord (plus fiable pour VIX)
    try:
        vix = yf.Ticker("^VIX")
        hist = vix.history(period="1d")
        if not hist.empty:
            vix_value = float(hist['Close'].iloc[-1])
            VIX_CACHE = {'value': vix_value, 'timestamp': time.time(), 'source': 'yahoo'}
            save_vix_cache()
            return vix_value
    except Exception as e:
        logger.warning(f"Erreur récup VIX Yahoo: {e}")

    # Fallback: utiliser Twelve Data si disponible
    try:
        donnees = recuperer_donnees_marche({"^VIX": "VIX"})
        if donnees and 'VIX' in donnees:
            vix_value = donnees['VIX'].get('prix', None)
            if vix_value:
                VIX_CACHE = {'value': vix_value, 'timestamp': time.time(), 'source': 'twelvedata'}
                save_vix_cache()
                return vix_value
    except Exception as e:
        logger.warning(f"Erreur récup VIX Twelve Data: {e}")

    # Fallback ultime: utiliser le cache même périmé (mieux que rien)
    if VIX_CACHE.get('value'):
        print(f"⚠️ Utilisation cache VIX périmé: {VIX_CACHE['value']:.1f}")
        return VIX_CACHE['value']

    return 20.0  # Valeur par défaut (normal)

def get_regime_marche(vix_niveau=None):
    """Détermine le régime de marché basé sur le VIX
    Returns: (regime, description, regles_adaptees)
    """
    if vix_niveau is None:
        vix_niveau = get_vix_level()

    if vix_niveau < 15:
        return "CALME", f"VIX à {vix_niveau:.1f} - Marché calme", \
            "Stops serrés (-0.4%), TP ambitieux (+1.2%), ratio 1:3 possible"
    elif vix_niveau < 20:
        return "NORMAL", f"VIX à {vix_niveau:.1f} - Conditions normales", \
            "Paramètres standard: stops -0.5% à -0.8%, TP +0.8% à +1.5%"
    elif vix_niveau < 30:
        return "VOLATILE", f"VIX à {vix_niveau:.1f} - Volatilité élevée", \
            "Stops élargis (-1%), TP conservateur (+1%), moins de trades"
    else:
        return "EXTREME", f"VIX à {vix_niveau:.1f} - Volatilité extrême", \
            "TRÈS SÉLECTIF: uniquement conviction 5, stops -1.5%, éviter les indices"

def get_session_marche():
    """Détermine la session de marché actuelle"""
    maintenant = get_paris_time()
    heure = maintenant.hour
    minute = maintenant.minute
    heure_decimal = heure + minute / 60

    # Détection du décalage DST US/EU (différent ~2 semaines par an)
    # US DST: 2nd dimanche mars → 1er dimanche novembre
    # EU DST: dernier dimanche mars → dernier dimanche octobre
    # Pendant ces périodes, US ouvre 1h plus tôt en heure Paris
    us_offset = 0
    mois = maintenant.month
    jour_mois = maintenant.day

    # Période mars: après 2nd dimanche US, avant dernier dimanche EU
    if mois == 3 and jour_mois >= 8 and jour_mois <= 31:
        # Simplification: US DST actif, vérifier si EU DST pas encore actif
        # Dernier dimanche de mars = jour >= 25 et weekday == 6 (dimanche)
        dernier_dimanche = 31 - ((datetime(maintenant.year, 3, 31).weekday() + 1) % 7)
        if jour_mois < dernier_dimanche:
            us_offset = -1  # US ouvre 1h plus tôt en heure Paris

    # Période octobre/novembre: EU DST terminé, US DST encore actif
    if mois == 10 and jour_mois >= 25:
        dernier_dimanche_oct = 31 - ((datetime(maintenant.year, 10, 31).weekday() + 1) % 7)
        if jour_mois > dernier_dimanche_oct:
            us_offset = -1
    elif mois == 11 and jour_mois <= 7:
        # Avant 1er dimanche novembre, US encore en DST
        premier_dimanche_nov = (7 - datetime(maintenant.year, 11, 1).weekday()) % 7 + 1
        if jour_mois < premier_dimanche_nov:
            us_offset = -1

    # Heures de session ajustées
    us_open = 15.5 + us_offset
    us_close = 22 + us_offset
    eu_us_overlap_end = 17.5 + us_offset

    if 9 <= heure_decimal < 11:
        return "EU_OPEN", "Ouverture européenne - Forte activité"
    elif 11 <= heure_decimal < 13:
        return "EU_MID", "Milieu de session EU - Consolidation"
    elif 13 <= heure_decimal < us_open:
        return "US_PREMARKET", "Pré-marché US - Attente données"
    elif us_open <= heure_decimal < 17:
        return "US_OPEN", f"Ouverture US ({us_open:.1f}h Paris) - Chevauchement EU/US"
    elif 17 <= heure_decimal < 17.5:
        return "EU_CLOSE", "Clôture EU - Prise de profits"
    elif 17.5 <= heure_decimal < (us_close - 1):
        return "US_ONLY", "Session US seule - Momentum US"
    elif (us_close - 1) <= heure_decimal < us_close:
        return "US_CLOSE", "Clôture US - Volatilité de fin"
    else:
        return "HORS_SESSION", "Hors heures principales"

def get_pattern_jour_semaine(jour=None):
    """Retourne le pattern historique typique pour un jour de la semaine"""
    if jour is None:
        jour = get_paris_time().weekday()

    patterns = {
        0: {  # Lundi
            "nom": "LUNDI",
            "pattern": "Gaps fréquents à l'open, volatilité matinale, consolidation PM",
            "conseil": "Attendre 30min après l'open, éviter les gaps extrêmes"
        },
        1: {  # Mardi
            "nom": "MARDI",
            "pattern": "Jour le plus actif de la semaine, bon volume",
            "conseil": "Journée idéale pour le day trading, momentum fiable"
        },
        2: {  # Mercredi
            "nom": "MERCREDI",
            "pattern": "Continuation de tendance, attention FOMC (si prévu)",
            "conseil": "Suivre la tendance du mardi, prudence si FOMC"
        },
        3: {  # Jeudi
            "nom": "JEUDI",
            "pattern": "Données emploi US, décisions BCE potentielles",
            "conseil": "Attention aux annonces 14h30, éviter positions avant"
        },
        4: {  # Vendredi
            "nom": "VENDREDI",
            "pattern": "Prises de profits PM, volume décroissant après 16h",
            "conseil": "Clôturer positions avant 16h, éviter overnight"
        }
    }

    return patterns.get(jour, {"nom": "WEEKEND", "pattern": "Marchés fermés", "conseil": "Pas de trading"})

def get_contexte_trading_complet():
    """Retourne le contexte complet pour Claude (VIX, session, jour, etc.)"""
    maintenant = get_paris_time()

    # VIX et régime
    vix_niveau = get_vix_level()
    regime, regime_desc, regles_adaptees = get_regime_marche(vix_niveau)

    # Session
    session, session_desc = get_session_marche()

    # Jour
    pattern_jour = get_pattern_jour_semaine()

    return {
        "datetime": maintenant.strftime("%Y-%m-%d %H:%M:%S CET"),
        "vix_niveau": vix_niveau,
        "regime_marche": regime,
        "regime_description": regime_desc,
        "regles_adaptees": regles_adaptees,
        "session_marche": session,
        "session_description": session_desc,
        "jour_semaine": pattern_jour["nom"],
        "pattern_jour": pattern_jour["pattern"],
        "conseil_jour": pattern_jour["conseil"]
    }

def calculer_trade_grade(trade_data):
    """
    Calcule la note qualité d'un trade (A/B/C/D) basée sur plusieurs critères.

    A = Setup parfait (score >= 85): Tous les feux au vert
    B = Bon setup (score 70-84): La plupart des critères OK
    C = Setup acceptable (score 50-69): Trade risqué mais justifiable
    D = Setup faible (score < 50): Trade de basse qualité

    Critères:
    - Conviction (20 points max)
    - R:R ratio (20 points max)
    - Alignement multi-timeframe (15 points max)
    - Confluence technique (15 points max)
    - Régime marché adapté (10 points max)
    - Session appropriée (10 points max)
    - Sentiment aligné (10 points max)
    """
    score = 0
    details = {}

    # 1. CONVICTION (20 points)
    conviction = trade_data.get('conviction_score', 3)
    conviction_score = conviction * 4  # 1→4, 2→8, 3→12, 4→16, 5→20
    score += conviction_score
    details['conviction'] = {'score': conviction_score, 'max': 20, 'valeur': conviction}

    # 2. RATIO R:R (20 points)
    ratio_rr_str = trade_data.get('ratio_rr', '1:1')
    try:
        parts = ratio_rr_str.replace(' ', '').split(':')
        if len(parts) == 2:
            rr_ratio = float(parts[1]) / float(parts[0])
        else:
            rr_ratio = 1.0
    except (ValueError, ZeroDivisionError, TypeError) as e:
        logger.warning(f"Erreur parsing ratio R:R '{ratio_rr_str}': {e}")
        rr_ratio = 1.0

    if rr_ratio >= 3.0:
        rr_score = 20
    elif rr_ratio >= 2.5:
        rr_score = 18
    elif rr_ratio >= 2.0:
        rr_score = 15
    elif rr_ratio >= 1.5:
        rr_score = 12
    elif rr_ratio >= 1.0:
        rr_score = 8
    else:
        rr_score = 4

    score += rr_score
    details['ratio_rr'] = {'score': rr_score, 'max': 20, 'valeur': ratio_rr_str}

    # 3. ALIGNEMENT MULTI-TIMEFRAME (15 points)
    alignement = trade_data.get('alignement_tf', 0)
    align_score = alignement * 5  # 0→0, 1→5, 2→10, 3→15
    score += align_score
    details['alignement_tf'] = {'score': align_score, 'max': 15, 'valeur': alignement}

    # 4. CONFLUENCE TECHNIQUE (15 points)
    confluence = trade_data.get('confluence_score', 5)
    conf_score = int(confluence * 1.5)  # 0→0, 5→7.5, 10→15
    score += conf_score
    details['confluence'] = {'score': conf_score, 'max': 15, 'valeur': confluence}

    # 5. RÉGIME MARCHÉ ADAPTÉ (10 points)
    regime = trade_data.get('regime_marche', 'NORMAL')
    direction = trade_data.get('direction', 'LONG')

    # En régime EXTREME, seules les positions SHORT ou conviction 5 sont OK
    if regime == 'EXTREME':
        if direction == 'SHORT' or conviction == 5:
            regime_score = 10
        else:
            regime_score = 2
    elif regime == 'VOLATILE':
        if conviction >= 4:
            regime_score = 8
        else:
            regime_score = 5
    elif regime == 'CALME':
        regime_score = 10  # Conditions idéales
    else:  # NORMAL
        regime_score = 8

    score += regime_score
    details['regime'] = {'score': regime_score, 'max': 10, 'valeur': regime}

    # 6. SESSION APPROPRIÉE (10 points)
    session = trade_data.get('session_marche', 'HORS_SESSION')

    session_scores = {
        'EU_OPEN': 10,      # Meilleur moment
        'US_OPEN': 10,      # Très bon
        'EU_MID': 7,        # Acceptable
        'US_ONLY': 7,       # OK
        'US_PREMARKET': 5,  # Risqué (avant données)
        'EU_CLOSE': 4,      # Prises de profits
        'US_CLOSE': 3,      # Volatile fin de journée
        'HORS_SESSION': 2   # Mauvais timing
    }
    session_score = session_scores.get(session, 5)
    score += session_score
    details['session'] = {'score': session_score, 'max': 10, 'valeur': session}

    # 7. SENTIMENT ALIGNÉ (10 points)
    sentiment = trade_data.get('sentiment_score', 0)

    # Sentiment doit être aligné avec la direction
    if direction == 'LONG':
        if sentiment >= 2:
            sent_score = 10
        elif sentiment >= 0:
            sent_score = 6
        elif sentiment >= -2:
            sent_score = 3
        else:
            sent_score = 0
    else:  # SHORT
        if sentiment <= -2:
            sent_score = 10
        elif sentiment <= 0:
            sent_score = 6
        elif sentiment <= 2:
            sent_score = 3
        else:
            sent_score = 0

    score += sent_score
    details['sentiment'] = {'score': sent_score, 'max': 10, 'valeur': sentiment}

    # Calcul du grade final
    if score >= 85:
        grade = 'A'
        grade_label = 'Excellent'
    elif score >= 70:
        grade = 'B'
        grade_label = 'Bon'
    elif score >= 50:
        grade = 'C'
        grade_label = 'Acceptable'
    else:
        grade = 'D'
        grade_label = 'Faible'

    # Score setup (avant exécution)
    setup_score = score

    return {
        'grade': grade,
        'grade_label': grade_label,
        'setup_score': setup_score,
        'details': details,
        'resume': f"{grade} ({setup_score}/100) - {grade_label}"
    }

def calculer_exit_grade(trade):
    """
    Calcule la note de sortie d'un trade après clôture.

    Critères:
    - Résultat vs objectif (40 points)
    - Gestion du drawdown (30 points)
    - Timing de sortie (30 points)
    """
    score = 0
    details = {}

    resultat = trade.get('resultat', 'NON_CONCLU')
    pnl_pct = trade.get('pnl_pct', 0) or 0
    pnl_max = trade.get('pnl_max', 0) or 0
    pnl_min = trade.get('pnl_min', 0) or 0

    # 1. RÉSULTAT VS OBJECTIF (40 points)
    if resultat == 'TP2':
        result_score = 40  # Objectif dépassé
    elif resultat == 'TP1':
        result_score = 35  # Objectif atteint
    elif resultat == 'WIN_FORCE':
        if pnl_pct >= 0.5:
            result_score = 28
        else:
            result_score = 22
    elif resultat == 'BREAKEVEN':
        result_score = 15  # Capital préservé
    elif resultat == 'STOP':
        result_score = 10  # Stop respecté
    elif resultat == 'LOSS_FORCE':
        if pnl_pct >= -0.5:
            result_score = 8
        else:
            result_score = 5
    else:
        result_score = 0

    score += result_score
    details['resultat'] = {'score': result_score, 'max': 40, 'valeur': resultat}

    # 2. GESTION DU DRAWDOWN (30 points)
    # Mesure: combien de % du gain max a été capturé
    if pnl_max > 0:
        if pnl_pct >= pnl_max * 0.8:
            dd_score = 30  # Excellent - gardé 80%+ du max
        elif pnl_pct >= pnl_max * 0.5:
            dd_score = 22  # Bon - gardé 50%+
        elif pnl_pct >= pnl_max * 0.2:
            dd_score = 15  # Passable
        elif pnl_pct >= 0:
            dd_score = 10  # Breakeven après profit
        else:
            dd_score = 5   # Perte après profit
    else:
        # Jamais été en profit
        if pnl_pct >= 0:
            dd_score = 15
        elif pnl_pct >= pnl_min * 0.5:
            dd_score = 12  # Remonté du min
        else:
            dd_score = 8   # Près du pire

    score += dd_score
    details['drawdown'] = {'score': dd_score, 'max': 30, 'pnl_max': pnl_max, 'pnl_pct': pnl_pct}

    # 3. TIMING DE SORTIE (30 points)
    duree = trade.get('duree_minutes', 0) or 0

    # Pour le day trading, durée idéale entre 30 min et 3h
    if 30 <= duree <= 180:
        timing_score = 30  # Durée idéale
    elif 15 <= duree <= 240:
        timing_score = 22  # Acceptable
    elif 5 <= duree <= 360:
        timing_score = 15  # Long/court mais OK
    else:
        timing_score = 8   # Très court ou overnight

    score += timing_score
    details['timing'] = {'score': timing_score, 'max': 30, 'duree_min': duree}

    return {
        'exit_score': score,
        'details': details
    }

# ============================================================================
# CALENDRIER MACRO & ÉVÉNEMENTS
# ============================================================================

# Événements macro récurrents majeurs (heure CET)
EVENEMENTS_MACRO_RECURRENTS = {
    # Chaque mois, jour approximatif
    "NFP": {"jour_semaine": 4, "semaine": 1, "heure": "14:30", "importance": 3},  # 1er vendredi
    "CPI_US": {"jour_mois": [10, 11, 12, 13], "heure": "14:30", "importance": 3},
    "FOMC": {"heures": ["20:00"], "importance": 3},  # Vérifié via API
    "BCE": {"heures": ["14:15", "14:45"], "importance": 3},
}

def get_evenements_macro_jour():
    """Récupère les événements macro du jour (basé sur le calendrier économique)
    IMPORTANT: Retourne une liste VIDE le weekend (pas de marchés ouverts)"""
    maintenant = get_paris_time()

    # Weekend: pas d'événements économiques (marchés fermés)
    if is_weekend():
        return []

    aujourdhui = maintenant.date()
    jour_semaine = maintenant.weekday()  # 0=Lundi, 4=Vendredi
    jour_mois = maintenant.day

    evenements = []

    # Vérifier NFP (1er vendredi du mois)
    if jour_semaine == 4:  # Vendredi
        premier_jour = maintenant.replace(day=1)
        premier_vendredi = 1 + (4 - premier_jour.weekday()) % 7
        if jour_mois == premier_vendredi:
            evenements.append({
                "nom": "NFP (Non-Farm Payrolls)",
                "heure": "14:30",
                "importance": 3,
                "impact": "MAJEUR - Très forte volatilité USD et indices"
            })

    # Vérifier CPI US (généralement entre le 10 et 15 du mois, jours ouvrés)
    if 10 <= jour_mois <= 15:
        evenements.append({
            "nom": "CPI US (potentiel)",
            "heure": "14:30",
            "importance": 2,
            "impact": "FORT - Volatilité sur USD, Or, Indices"
        })

    # ISM/PMI Services: 1er jour ouvré du mois uniquement
    if jour_mois <= 3:  # Dans les 3 premiers jours du mois
        # Calculer le 1er jour ouvré du mois
        premier_jour = maintenant.replace(day=1)
        premier_jour_ouvre = premier_jour
        while premier_jour_ouvre.weekday() >= 5:  # Skip weekend
            premier_jour_ouvre += timedelta(days=1)
        if maintenant.date() == premier_jour_ouvre.date():
            evenements.append({
                "nom": "ISM/PMI Services",
                "heure": "16:00",
                "importance": 2,
                "impact": "Volatilité modérée - indicateur économique clé"
            })

    # Stocks pétrole EIA: Mercredi uniquement
    if jour_semaine == 2:  # Mercredi
        evenements.append({
            "nom": "Stocks pétrole EIA",
            "heure": "16:30",
            "importance": 2,
            "impact": "Volatilité sur WTI/Brent"
        })

    return evenements

def verifier_proximite_evenement_macro(minutes_avant=30):
    """
    Vérifie si un événement macro majeur est imminent.
    Retourne (True, evenement) si on est à moins de X minutes d'un événement important.
    """
    maintenant = get_paris_time()
    evenements = get_evenements_macro_jour()

    for evt in evenements:
        if evt.get("importance", 1) >= 2:  # Événement important
            try:
                heure_evt = evt["heure"]
                h, m = map(int, heure_evt.split(":"))
                heure_evenement = maintenant.replace(hour=h, minute=m, second=0, microsecond=0)

                # Calculer la différence en minutes
                diff = (heure_evenement - maintenant).total_seconds() / 60

                # Si l'événement est dans les X prochaines minutes
                if 0 <= diff <= minutes_avant:
                    return True, evt
            except (ValueError, KeyError):
                continue

    return False, None

def get_actifs_filtres_atr(donnees_marche, seuil_atr_min=1.0):
    """
    Filtre les actifs avec un ATR trop faible pour le day trading.
    Retourne la liste des actifs à éviter.
    """
    actifs_faible_atr = []
    for nom, data in donnees_marche.items():
        atr_pct = data.get("atr_pct", 0)
        if atr_pct > 0 and atr_pct < seuil_atr_min:
            actifs_faible_atr.append({
                "nom": nom,
                "symbole": data.get("symbole"),
                "atr_pct": atr_pct
            })
    return actifs_faible_atr

# ============================================================================
# RÉCUPÉRATION DONNÉES MARCHÉ
# ============================================================================

# Cache haut niveau pour éviter le stampede (multiples appels simultanés)
MARKET_DATA_CACHE = {'data': None, 'timestamp': 0, 'actifs_key': None}
MARKET_DATA_CACHE_TTL = 600  # 10 minutes (cohérent avec TWELVEDATA_CACHE_TTL)

def recuperer_donnees_marche(actifs, inclure_indicateurs=True):
    """Récupère les données de marché pour les actifs donnés avec ATR et Volume relatif.

    OPTIMISÉ: Utilise BATCH API pour charger tous les symboles en quelques appels
    au lieu d'un appel par symbole. Réduit le temps de chargement de ~45s à ~7s.

    Cache haut-niveau pour éviter le stampede quand plusieurs endpoints appellent simultanément."""
    global MARKET_DATA_CACHE

    # Clé unique pour ce set d'actifs
    actifs_key = hash(frozenset(actifs.keys()))
    now = time.time()

    # Vérifier cache haut-niveau (évite le stampede)
    with TWELVEDATA_FETCH_LOCK:
        if (MARKET_DATA_CACHE['data'] is not None and
            MARKET_DATA_CACHE['actifs_key'] == actifs_key and
            now - MARKET_DATA_CACHE['timestamp'] < MARKET_DATA_CACHE_TTL):
            return MARKET_DATA_CACHE['data']

    donnees = {}
    donnees_non_fraiches = []

    # BATCH API: Récupérer toutes les données en quelques appels groupés
    symboles_list = list(actifs.keys())
    batch_results = get_twelvedata_batch(symboles_list, outputsize=30, interval="1day")

    for symbole, nom in actifs.items():
        try:
            # Récupérer depuis les résultats batch
            info, is_fresh = batch_results.get(symbole, (pd.DataFrame(), False))

            if not info.empty:
                prix_actuel = info['Close'].iloc[-1]
                prix_ouverture = info['Open'].iloc[-1]
                prix_max = info['High'].iloc[-1]
                prix_min = info['Low'].iloc[-1]

                # Variation: clôture précédente vers clôture actuelle (standard du marché)
                if len(info) >= 2:
                    cloture_precedente = info['Close'].iloc[-2]
                    variation = ((prix_actuel - cloture_precedente) / cloture_precedente) * 100 if cloture_precedente > 0 else 0
                else:
                    variation = 0

                # Variation sur 5 jours
                if len(info) >= 5:
                    prix_5j = info['Close'].iloc[-5]
                    var_5j = ((prix_actuel - prix_5j) / prix_5j) * 100 if prix_5j > 0 else 0
                else:
                    var_5j = 0

                # ATR (Average True Range) sur 14 périodes
                atr = 0
                atr_pct = 0
                if len(info) >= 15 and inclure_indicateurs:
                    high_low = info['High'] - info['Low']
                    high_close = np.abs(info['High'] - info['Close'].shift())
                    low_close = np.abs(info['Low'] - info['Close'].shift())
                    ranges = pd.concat([high_low, high_close, low_close], axis=1)
                    true_range = np.max(ranges, axis=1)
                    atr = true_range.rolling(14).mean().iloc[-1]
                    atr_pct = (atr / prix_actuel) * 100 if prix_actuel > 0 else 0

                # Volume relatif (vs moyenne 20 jours)
                volume_relatif = 100
                if len(info) >= 20 and inclure_indicateurs and 'Volume' in info.columns:
                    volume_actuel = info['Volume'].iloc[-1]
                    volume_moyen = info['Volume'].iloc[-20:].mean()
                    if volume_moyen > 0:
                        volume_relatif = (volume_actuel / volume_moyen) * 100

                # Date des dernières données
                last_date = info.index[-1]
                if hasattr(last_date, 'strftime'):
                    last_date_str = last_date.strftime('%Y-%m-%d')
                else:
                    last_date_str = str(last_date)[:10]

                # Pivot Points (Support/Résistance) - basés sur la veille
                pivot = support1 = resistance1 = None
                if len(info) >= 2:
                    prev_high = info['High'].iloc[-2]
                    prev_low = info['Low'].iloc[-2]
                    prev_close = info['Close'].iloc[-2]

                    pivot = (prev_high + prev_low + prev_close) / 3
                    resistance1 = 2 * pivot - prev_low  # R1
                    support1 = 2 * pivot - prev_high    # S1

                # Convertir en types Python natifs pour éviter erreur JSON "int64 not serializable"
                donnees[nom] = {
                    "symbole": symbole,
                    "prix": float(round(prix_actuel, 2)),
                    "ouverture": float(round(prix_ouverture, 2)),
                    "haut": float(round(prix_max, 2)),
                    "bas": float(round(prix_min, 2)),
                    "variation": float(round(variation, 2)),
                    "variation_5j": float(round(var_5j, 2)),
                    "atr": float(round(atr, 4)) if atr else 0,
                    "atr_pct": float(round(atr_pct, 2)) if atr_pct else 0,
                    "volume_relatif": float(round(volume_relatif, 1)),
                    "pivot": float(round(pivot, 2)) if pivot else None,
                    "support1": float(round(support1, 2)) if support1 else None,
                    "resistance1": float(round(resistance1, 2)) if resistance1 else None,
                    "data_date": last_date_str,
                    "is_fresh": is_fresh
                }

                if not is_fresh:
                    donnees_non_fraiches.append(nom)
        except Exception as e:
            print(f"⚠️ Erreur {symbole}: {e}")

    if donnees_non_fraiches:
        print(f"⚠️ Données potentiellement obsolètes pour: {', '.join(donnees_non_fraiches[:5])}")

    # Sauvegarder dans le cache haut-niveau
    with TWELVEDATA_FETCH_LOCK:
        MARKET_DATA_CACHE['data'] = donnees
        MARKET_DATA_CACHE['timestamp'] = time.time()
        MARKET_DATA_CACHE['actifs_key'] = hash(frozenset(actifs.keys()))

    return donnees

def calculer_indicateurs_techniques(symbole):
    """Calcule les indicateurs techniques pour un actif via Twelve Data"""
    try:
        # Données intraday 5min (288 bougies = 24h pour couvrir trades overnight)
        df_intraday, _ = get_twelvedata_intraday(symbole, interval="5min", outputsize=288)
        # Données daily pour 30 jours
        df_daily, _ = get_twelvedata_time_series(symbole, outputsize=30, interval="1day")

        if df_daily.empty:
            return None

        indicateurs = {}

        # VWAP (si données intraday disponibles)
        if not df_intraday.empty and len(df_intraday) > 0 and 'Volume' in df_intraday.columns:
            df_intraday['TP'] = (df_intraday['High'] + df_intraday['Low'] + df_intraday['Close']) / 3
            df_intraday['TP_Volume'] = df_intraday['TP'] * df_intraday['Volume']
            total_volume = df_intraday['Volume'].sum()
            if total_volume > 0:
                vwap = df_intraday['TP_Volume'].sum() / total_volume
                indicateurs['vwap'] = float(round(vwap, 2))

        # Pivot Points
        if len(df_daily) >= 2:
            prev_high = df_daily['High'].iloc[-2]
            prev_low = df_daily['Low'].iloc[-2]
            prev_close = df_daily['Close'].iloc[-2]

            pivot = (prev_high + prev_low + prev_close) / 3
            indicateurs['pivot'] = float(round(pivot, 2))
            indicateurs['r1'] = float(round(2 * pivot - prev_low, 2))
            indicateurs['r2'] = float(round(pivot + (prev_high - prev_low), 2))
            indicateurs['s1'] = float(round(2 * pivot - prev_high, 2))
            indicateurs['s2'] = float(round(pivot - (prev_high - prev_low), 2))

        # ATR
        if len(df_daily) >= 15:
            high_low = df_daily['High'] - df_daily['Low']
            high_close = np.abs(df_daily['High'] - df_daily['Close'].shift())
            low_close = np.abs(df_daily['Low'] - df_daily['Close'].shift())

            ranges = pd.concat([high_low, high_close, low_close], axis=1)
            true_range = np.max(ranges, axis=1)
            atr = true_range.rolling(14).mean().iloc[-1]

            indicateurs['atr'] = float(round(atr, 2))
            prix_close = df_daily['Close'].iloc[-1]
            indicateurs['atr_pct'] = float(round((atr / prix_close) * 100, 2)) if prix_close > 0 else 0

        # Volume relatif
        if len(df_daily) >= 20 and 'Volume' in df_daily.columns:
            volume_actuel = df_daily['Volume'].iloc[-1]
            volume_moyen = df_daily['Volume'].iloc[-20:].mean()
            if volume_moyen > 0:
                indicateurs['volume_relatif_pct'] = float(round((volume_actuel / volume_moyen) * 100, 1))

        # Prix actuel et variation (cohérent avec recuperer_donnees_marche: close-to-close)
        indicateurs['prix_actuel'] = float(round(df_daily['Close'].iloc[-1], 2))
        if len(df_daily) >= 2:
            cloture_precedente = df_daily['Close'].iloc[-2]
            if cloture_precedente > 0:
                indicateurs['variation_jour'] = float(round(
                    ((df_daily['Close'].iloc[-1] - cloture_precedente) / cloture_precedente) * 100, 2
                ))
            else:
                indicateurs['variation_jour'] = 0
        else:
            indicateurs['variation_jour'] = 0

        return indicateurs
    except Exception as e:
        print(f"⚠️ Erreur indicateurs {symbole}: {e}")
        return None

# ============================================================================
# ANALYSE CLAUDE
# ============================================================================

SYSTEM_PROMPT = """Tu es un agent de trading intraday TRÈS COURT TERME pour Thomas.

CONTRAINTES CRITIQUES:
- Thomas trade avec LEVIER - positions le MOINS LONGTEMPS possible
- Objectif: +0.8% à +1.5% en quelques minutes à 2-3h max
- Stop serré: -0.5% à -0.8%
- Pas de positions overnight
- RATIO RISK/REWARD MINIMUM: 1:1.5 (risquer 1 pour espérer 1.5)

RÈGLES DE FILTRAGE:
- UNIQUEMENT des actifs dont le marché est OUVERT ou s'ouvre dans 2h
- EXCLUS les actifs avec ATR < 1% (trop peu de volatilité pour du day trading)
- NE RECOMMANDE PAS de trades 30 minutes AVANT un événement macro majeur (NFP, CPI, FOMC, BCE)
- AU MINIMUM 1 opportunité NEWS TRADING dans tes recommandations
- TOUTES les heures en CET (heure française)
- PRÉCISE TOUJOURS si c'est un LONG ou un SHORT
- UTILISE les indicateurs techniques fournis (RSI, MACD) pour confirmer tes trades

INDICATEURS TECHNIQUES (fournis dans les données):
- ATR%: Average True Range en % du prix - mesure la volatilité. ATR < 1% = ÉVITER
- Volume Relatif: Volume actuel vs moyenne 20j. > 150% = intérêt institutionnel
- RSI (0-100): < 30 = survente (potentiel LONG), > 70 = surachat (potentiel SHORT)
- MACD: BULLISH_CROSS = signal d'achat, BEARISH_CROSS = signal de vente
- Pivot/Support1/Resistance1: Niveaux techniques clés calculés sur la veille
  - UTILISE support1/resistance1 pour placer tes stops et TP intelligemment
  - Entrée proche du pivot = setup neutre, entrée proche support1 = meilleur R/R

=== RÈGLES AVANCÉES A/B TESTING ===

1. TIMING D'ENTRÉE (strategie_entree):
- IMMEDIATE: Entrer maintenant au marché (signal fort, momentum clair)
- PULLBACK: Attendre un repli de 0.2-0.3% avant d'entrer (meilleur R/R)
- BREAKOUT: Attendre cassure d'un niveau (résistance/support) avec volume
- LIMIT: Placer un ordre limite à un prix précis (prix_limite_entree)
RÈGLE: PULLBACK si RSI neutre, IMMEDIATE si RSI extrême, BREAKOUT sur range

2. TRAILING STOP (trailing_stop, trailing_stop_pct):
- Active le trailing stop (trailing_stop=1) si conviction >= 4
- trailing_stop_pct: % de trailing (0.3 = stop suit le prix à 0.3%)
- RÈGLE: Trailing si trend_daily aligné avec la position

3. RÉGIME DE MARCHÉ (basé sur VIX fourni):
- CALME (VIX < 15): Stops serrés (-0.4%), TP ambitieux (+1.2%), ratio 1:3
- NORMAL (15-20): Paramètres standard (-0.5% à -0.8%, +0.8% à +1.5%)
- VOLATILE (20-30): Stops élargis (-1%), TP réduit (+1%), moins de trades
- EXTREME (VIX > 30): TRÈS SÉLECTIF, uniquement conviction 5, stops -1.5%
Indique dans "regles_adaptees" comment tu adaptes au régime actuel.

4. TIME DECAY / URGENCE:
- validite_minutes: Fenêtre de validité de l'opportunité (30, 60, 120 min)
- heure_expiration: Heure CET après laquelle le setup n'est plus valide
- urgence: HAUTE (entrer dans 15min), MOYENNE (30min), BASSE (flexible)
RÈGLE: News trading = urgence HAUTE, technique pure = BASSE

5. MULTI-TIMEFRAME (trend_daily, trend_h4, trend_h1):
- UP: Tendance haussière (prix > EMA20, higher highs)
- DOWN: Tendance baissière (prix < EMA20, lower lows)
- RANGE: Pas de tendance claire
- alignement_tf: Compte combien de TF sont alignés (0-3)
- confluence_score: Score global 0-10 (TF alignés + indicateurs + volume)
RÈGLE: Privilégier trades avec alignement_tf >= 2

6. SENTIMENT (sentiment_score, sentiment_source):
- sentiment_score: -5 (très bearish) à +5 (très bullish)
- sentiment_source: NEWS (actualités), TECHNIQUE (graphique), FLOW (volumes), MIXTE
- sentiment_detail: Explication du sentiment
RÈGLE: Ne pas aller contre un sentiment extrême (< -3 ou > +3)

7. SAISONNALITÉ (jour_semaine, session_marche):
- jour_semaine: LUNDI/MARDI/MERCREDI/JEUDI/VENDREDI
- session_marche: EU_OPEN (9h-11h), US_PREMARKET (13h-15h30), US_OPEN (15h30-17h), EU_CLOSE (17h-17h30), US_CLOSE (21h-22h)
- pattern_jour: Observation historique (ex: "Lundi souvent gap puis consolidation")
PATTERNS CONNUS:
- LUNDI: Gaps fréquents, volatilité matinale, consolidation PM
- MARDI/MERCREDI: Jours les plus actifs, bon pour day trading
- JEUDI: Attention aux annonces BCE
- VENDREDI: Prises de profits PM, éviter après 16h

8. SCORE DE CONVICTION (conviction_score 1-5):
- 1: Setup faible, seulement si rien d'autre
- 2: Setup acceptable, petit sizing
- 3: Setup standard, sizing normal
- 4: Bonne opportunité, sizing +50%, trailing stop activé
- 5: Setup exceptionnel (multi-TF aligné + indicateurs + news), sizing max
RÈGLE: Ne recommande QUE des trades avec conviction >= 3

ÉVÉNEMENTS MACRO À SURVEILLER:
- NFP (1er vendredi du mois 14h30): ÉVITER 30min avant, forte volatilité USD
- CPI US (entre 10-15 du mois 14h30): ÉVITER 30min avant
- FOMC (décisions Fed 20h): ÉVITER positions, très forte volatilité
- BCE (décisions 14h15-14h45): ÉVITER positions sur EUR

FORMAT DE RÉPONSE EN JSON:
{
  "contexte_marche": "Paragraphe narratif sur le contexte actuel",
  "regime_marche_global": "CALME/NORMAL/VOLATILE/EXTREME",
  "vix_actuel": 0,
  "jour_semaine": "LUNDI/MARDI/MERCREDI/JEUDI/VENDREDI",
  "session_actuelle": "EU_OPEN/US_PREMARKET/US_OPEN/EU_CLOSE/US_CLOSE",
  "alerte_macro": "Message d'alerte si événement imminent, sinon null",
  "snapshot": {
    "indices": {"CAC": {"prix": 0, "var": 0}, ...},
    "mouvements_actifs": [{"actif": "", "var": 0, "raison": ""}]
  },
  "opportunites": [
    {
      "actif": "",
      "symbole": "",
      "direction": "LONG ou SHORT",
      "prix_actuel": 0,
      "conviction_score": 1-5,
      "atr_pct": 0,
      "volume_relatif": 0,
      "rsi": 0,
      "macd_signal": "BULLISH/BEARISH/BULLISH_CROSS/BEARISH_CROSS",
      "catalyseur": "",
      "is_news_trading": true/false,
      "strategie_entree": "IMMEDIATE/PULLBACK/BREAKOUT/LIMIT",
      "prix_limite_entree": null,
      "timing": "",
      "entree": 0,
      "stop": 0,
      "trailing_stop": 0,
      "trailing_stop_pct": 0,
      "tp1": 0,
      "tp2": 0,
      "ratio_rr": "1:1.5 ou 1:2 ou 1:3",
      "ratio_rr_justification": "Pourquoi ce ratio",
      "trend_daily": "UP/DOWN/RANGE",
      "trend_h4": "UP/DOWN/RANGE",
      "trend_h1": "UP/DOWN/RANGE",
      "alignement_tf": 0-3,
      "confluence_score": 0-10,
      "sentiment_score": -5 à +5,
      "sentiment_source": "NEWS/TECHNIQUE/FLOW/MIXTE",
      "sentiment_detail": "",
      "validite_minutes": 30/60/120,
      "heure_expiration": "HH:MM",
      "urgence": "HAUTE/MOYENNE/BASSE",
      "regime_marche": "CALME/NORMAL/VOLATILE/EXTREME",
      "regles_adaptees": "Comment les règles sont adaptées au régime",
      "session_marche": "EU_OPEN/US_PREMARKET/US_OPEN/EU_CLOSE/US_CLOSE",
      "pattern_jour": "Observation saisonnière",
      "duree": "",
      "invalidation": ""
    }
  ],
  "actifs_exclus_atr": ["Liste des actifs exclus car ATR trop faible"],
  "evenements_a_venir": [
    {"heure": "", "evenement": "", "importance": 1-3, "impact_recommande": "ÉVITER/PRUDENCE/OK"}
  ],
  "zones_dangereuses": [""],
  "tactical_tip": ""
}"""

SYSTEM_PROMPT_CLOTURE = """Tu es un agent de trading qui prépare Thomas pour le lendemain.

FORMAT DE RÉPONSE EN JSON:
{
  "resume_journee": "Paragraphe narratif résumant la journée",
  "chiffres_cles": {
    "indices": {"CAC": {"prix": 0, "var": 0}, ...},
    "mouvements_majeurs": [{"actif": "", "var": 0, "raison": ""}],
    "commodites_forex": {"Or": {"prix": 0, "var": 0}, ...}
  },
  "niveaux_techniques_demain": [
    {"actif": "", "support": 0, "resistance": 0, "contexte": ""}
  ],
  "agenda_demain": [
    {"heure": "", "evenement": "", "importance": 1-3, "attendu": "", "impact": ""}
  ],
  "setups_demain": [
    {"actif": "", "direction": "LONG/SHORT", "condition": "", "entree": 0, "tp": 0, "stop": 0}
  ],
  "conseil_demain": ""
}"""

SYSTEM_PROMPT_NEWS_ANALYSIS = """Tu es un analyste trading senior spécialisé dans l'identification des impacts marché des actualités.

OBJECTIF: Analyser des headlines d'actualités et identifier celles qui ont un RÉEL impact trading.

ACTIFS TRADABLES (avec symboles):
- Indices: CAC 40 (^FCHI), S&P 500 (^GSPC), Nasdaq (^IXIC), DAX (^GDAXI)
- Actions FR: LVMH (MC.PA), Airbus (AIR.PA), TotalEnergies (TTE.PA), BNP (BNP.PA)
- Actions US: Apple (AAPL), Tesla (TSLA), NVIDIA (NVDA), Amazon (AMZN)
- Commodités: Or (GC=F), Pétrole Brent (BZ=F), Café (KC=F), Cacao (CC=F), Cuivre (HG=F), Blé (ZW=F)
- Forex: EUR/USD (EURUSD=X), GBP/USD (GBPUSD=X), USD/JPY (USDJPY=X)

CRITÈRES D'IMPACT:
- HIGH: Événement majeur, mouvement attendu > 1%, action immédiate recommandée
  (catastrophe naturelle affectant production, décision banque centrale surprise, guerre/conflit, données macro très éloignées des attentes)
- MEDIUM: Impact notable, mouvement 0.3-1%, à surveiller
  (earnings surprise, changement politique, données macro légèrement hors attentes)
- LOW: Impact limité, < 0.3%, information de contexte
  (rumeurs, analyses, prévisions)

RÈGLES:
- Ne retourne QUE les news avec un réel impact trading (ignore les news corporate mineures, people, etc.)
- Maximum 6 news les plus impactantes
- Sois PRÉCIS sur les actifs concernés avec leurs SYMBOLES
- Indique la DIRECTION probable (LONG/SHORT)
- Évalue le TIMING (immédiat, aujourd'hui, cette semaine)

FORMAT JSON:
{
  "news_analysees": [
    {
      "headline": "Titre original de la news",
      "impact": "HIGH/MEDIUM/LOW",
      "analyse": "Explication courte de l'impact trading (1 phrase)",
      "impact_cours": "+1.5% à +3% attendu" ou "-0.5% à -1% attendu",
      "actifs": [
        {"symbole": "KC=F", "nom": "Café", "direction": "LONG", "raison": "Supply shock", "impact_estime": "+2%"}
      ],
      "timing": "immédiat/aujourd'hui/cette semaine",
      "source": "Source originale"
    }
  ]
}

IMPORTANT: Pour chaque news, estime l'IMPACT SUR LES COURS en pourcentage (impact_cours et impact_estime par actif).

Si aucune news n'a d'impact trading significatif, retourne un tableau vide."""

def analyser_news_trading(headlines):
    """Analyse les headlines d'actualités pour identifier les impacts trading"""
    if not headlines:
        return []

    maintenant = get_paris_time()

    # Formater les headlines pour Claude
    headlines_text = "\n".join([
        f"- [{h.get('source', 'Unknown')}] {h.get('titre', h.get('title', ''))}"
        for h in headlines[:15]  # Max 15 headlines à analyser
    ])

    question = f"""Analyse ces actualités du {maintenant.strftime('%d/%m/%Y')} et identifie celles avec un impact trading:

{headlines_text}

Retourne UNIQUEMENT les news pertinentes pour le trading en JSON."""

    try:
        message = client_anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            system=SYSTEM_PROMPT_NEWS_ANALYSIS,
            messages=[{"role": "user", "content": question}]
        )

        reponse = message.content[0].text
        result, erreur = extraire_json_claude(reponse)

        if erreur:
            logger.warning(f"Extraction JSON news échouée: {erreur}")
            return []

        return result.get('news_analysees', [])
    except Exception as e:
        print(f"⚠️ Erreur analyse news: {e}")
        return []

def fetch_and_analyze_news():
    """Récupère les news via NewsAPI et les analyse pour l'impact trading"""
    if not NEWSAPI_KEY:
        return [], "NEWSAPI_KEY non configurée"

    try:
        # 1. Récupérer les news brutes
        articles = []

        # News business générales
        url = "https://newsapi.org/v2/top-headlines"
        params = {
            'apiKey': NEWSAPI_KEY,
            'category': 'business',
            'language': 'fr',
            'pageSize': 15
        }
        response = requests.get(url, params=params, timeout=10)
        data = response.json()

        if data.get('status') == 'ok':
            for article in data.get('articles', []):
                articles.append({
                    'titre': article.get('title', ''),
                    'source': article.get('source', {}).get('name', 'Inconnu'),
                    'heure': article.get('publishedAt', ''),
                    'url': article.get('url', '')
                })

        # News internationales (pour commodités, géopolitique)
        url_world = "https://newsapi.org/v2/top-headlines"
        params_world = {
            'apiKey': NEWSAPI_KEY,
            'category': 'general',
            'language': 'en',
            'pageSize': 10
        }
        response_world = requests.get(url_world, params=params_world, timeout=10)
        data_world = response_world.json()

        if data_world.get('status') == 'ok':
            for article in data_world.get('articles', []):
                articles.append({
                    'titre': article.get('title', ''),
                    'source': article.get('source', {}).get('name', 'Inconnu'),
                    'heure': article.get('publishedAt', ''),
                    'url': article.get('url', '')
                })

        if not articles:
            return [], "Aucune news récupérée"

        # 2. Analyser les news avec Claude
        news_analysees = analyser_news_trading(articles)

        # 3. Enrichir avec l'heure formatée
        for news in news_analysees:
            headline = news.get('headline', '').lower()
            best_match = None
            best_score = 0

            # Trouver l'article original avec correspondance flexible
            for article in articles:
                titre = article.get('titre', '').lower()
                # Vérifier plusieurs types de correspondance
                if titre in headline or headline in titre:
                    best_match = article
                    break
                # Correspondance par mots-clés significatifs (>5 caractères)
                titre_mots = set(m for m in titre.split() if len(m) > 5)
                headline_mots = set(m for m in headline.split() if len(m) > 5)
                score = len(titre_mots & headline_mots)
                if score > best_score:
                    best_score = score
                    best_match = article

            if best_match and best_match.get('heure'):
                try:
                    dt = datetime.fromisoformat(best_match['heure'].replace('Z', '+00:00'))
                    dt_paris = dt.astimezone(TZ_PARIS)
                    news['heure'] = dt_paris.strftime('%H:%M')
                except ValueError:
                    news['heure'] = get_paris_time().strftime('%H:%M')
            else:
                # Utiliser l'heure courante si pas de correspondance
                news['heure'] = get_paris_time().strftime('%H:%M')

        # 4. Sauvegarder les news analysées en base
        sauvegarder_news_analysees(news_analysees)

        return news_analysees, None

    except Exception as e:
        print(f"⚠️ Erreur fetch_and_analyze_news: {e}")
        return [], str(e)

def sauvegarder_news_analysees(news_list):
    """Sauvegarde les news analysées dans la table alertes_news"""
    if not news_list:
        return

    maintenant = get_paris_time()
    aujourdhui = maintenant.date()

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        for news in news_list:
            # Vérifier si la news existe déjà (par titre)
            cursor.execute('''
                SELECT id FROM alertes_news WHERE titre = ? AND date = ?
            ''', (news.get('headline', '')[:200], aujourdhui))

            if cursor.fetchone() is None:
                # Insérer la nouvelle news
                # Note: Le JSON de Claude utilise 'actifs', pas 'actifs_concernes'
                actifs_list = news.get('actifs', news.get('actifs_concernes', []))
                actifs_concernes = ', '.join([a.get('symbole', '') for a in actifs_list])
                cursor.execute('''
                    INSERT INTO alertes_news (date, heure, titre, contenu, actif, impact, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    aujourdhui,
                    news.get('heure', '--:--'),
                    news.get('headline', '')[:200],
                    news.get('analyse', ''),
                    actifs_concernes,
                    news.get('impact', 'faible'),
                    maintenant
                ))

        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ Erreur sauvegarde news: {e}")

def get_news_historique(jours=7):
    """Récupère l'historique des news analysées"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        date_limite = get_paris_time().date() - timedelta(days=jours)

        cursor.execute('''
            SELECT * FROM alertes_news
            WHERE date >= ?
            ORDER BY date DESC, timestamp DESC
        ''', (date_limite,))

        news = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return news
    except Exception as e:
        print(f"⚠️ Erreur historique news: {e}")
        return []

def analyser_marche_json(donnees):
    """Analyse le marché et retourne un JSON structuré"""
    maintenant = get_paris_time()
    heure_str = maintenant.strftime('%H:%M')

    marche_type, marche_info = get_market_context()

    # Vérifier les événements macro imminents
    evt_imminent, evt_details = verifier_proximite_evenement_macro(minutes_avant=30)
    alerte_macro = None
    if evt_imminent:
        alerte_macro = f"⚠️ ATTENTION: {evt_details['nom']} dans moins de 30 min ({evt_details['heure']}). {evt_details.get('impact', '')}"

    # Récupérer les événements macro du jour
    evenements_macro = get_evenements_macro_jour()

    # Identifier les actifs à faible ATR
    actifs_faible_atr = get_actifs_filtres_atr(donnees, seuil_atr_min=1.0)
    liste_exclus = [a['nom'] for a in actifs_faible_atr]

    # Enrichir les données avec les indicateurs RSI/MACD calculés (si pas déjà fait)
    if 'indicateurs_calcules' in donnees:
        donnees_enrichies = donnees  # Déjà enrichies
    else:
        donnees_enrichies = enrichir_donnees_avec_indicateurs(donnees)

    # Formater les données pour le prompt
    donnees_texte = json.dumps(donnees_enrichies, ensure_ascii=False, indent=2)

    # Construire le contexte enrichi
    contexte_macro = ""
    if alerte_macro:
        contexte_macro = f"\n\n🚨 ALERTE MACRO: {alerte_macro}"
    if evenements_macro:
        contexte_macro += f"\n\nÉVÉNEMENTS MACRO DU JOUR:\n"
        for evt in evenements_macro:
            contexte_macro += f"- {evt['heure']}: {evt['nom']} (importance {evt['importance']}/3)\n"

    exclusions_atr = ""
    if liste_exclus:
        exclusions_atr = f"\n\nACTIFS À EXCLURE (ATR < 1%):\n{', '.join(liste_exclus)}"

    # Récupérer les critères dynamiques
    criteres = get_criteres_dynamiques()
    contexte_criteres = ""
    if criteres.get('scores_confiance'):
        contexte_criteres = "\n\nSCORES DE CONFIANCE (basés sur performances passées):\n"
        for cat, data in criteres['scores_confiance'].items():
            if isinstance(data, dict):
                score = data.get('score', 50)
                emoji = "🟢" if score >= 60 else "🟡" if score >= 40 else "🔴"
                contexte_criteres += f"- {cat}: {emoji} {score}/100\n"

    if criteres.get('ajustements_recents'):
        contexte_criteres += "\nAJUSTEMENTS RÉCENTS:\n"
        for aj in criteres['ajustements_recents'][:3]:
            contexte_criteres += f"- {aj.get('critere', '')}: {aj.get('raison', '')}\n"

    # === NOUVEAU: Contexte trading avancé (VIX, session, saisonnalité) ===
    contexte_avance = get_contexte_trading_complet()
    contexte_regime = f"""
=== CONTEXTE TRADING AVANCÉ ===
📊 VIX: {contexte_avance['vix_niveau']:.1f} → Régime: {contexte_avance['regime_marche']}
   {contexte_avance['regime_description']}
   Règles adaptées: {contexte_avance['regles_adaptees']}

⏰ Session: {contexte_avance['session_marche']} - {contexte_avance['session_description']}

📅 Jour: {contexte_avance['jour_semaine']}
   Pattern: {contexte_avance['pattern_jour']}
   Conseil: {contexte_avance['conseil_jour']}
"""

    question = f"""DONNÉES MARCHÉ EN TEMPS RÉEL (avec ATR%, Volume Relatif, RSI, MACD, Pivot/Support/Résistance):
{donnees_texte}

Heure: {maintenant.strftime('%d/%m/%Y %H:%M')} CET
Contexte: {marche_type} - {marche_info}
{contexte_regime}
{contexte_macro}
{exclusions_atr}
{contexte_criteres}

Analyse le marché et fournis une réponse JSON structurée selon le format demandé.
RÈGLES IMPÉRATIVES:
- EXCLUS les actifs listés avec ATR < 1%
- Si événement macro imminent, mentionne-le dans alerte_macro
- PRIVILÉGIE les catégories avec score de confiance élevé (>60)
- ÉVITE les catégories avec score faible (<40)
- AU MOINS 1 opportunité NEWS TRADING (si conditions favorables)
- Liste UNIQUEMENT les événements APRÈS {heure_str}
- UTILISE les indicateurs RSI et MACD fournis pour confirmer tes trades
- UTILISE les niveaux support1/resistance1 pour placer stop/TP intelligemment
- RATIO R/R MINIMUM 1:1.5 - justifie ton choix dans ratio_rr_justification
- ADAPTE tes règles au RÉGIME DE MARCHÉ indiqué (VIX)
- INDIQUE pour chaque opportunité: conviction_score (1-5), strategie_entree, validite_minutes, sentiment_score
- REMPLIS tous les champs multi-timeframe: trend_daily, trend_h4, trend_h1, alignement_tf
- Pour chaque opportunité: symbole, atr_pct, volume_relatif, rsi, macd_signal, ratio_rr, ratio_rr_justification"""

    try:
        # Générer le prompt système avec les instructions dynamiques et A/B testing
        instructions_dynamiques = generer_instructions_dynamiques()
        instructions_ab = get_instructions_ab_testing()
        system_prompt_complet = SYSTEM_PROMPT + instructions_dynamiques + instructions_ab

        message = client_anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            system=system_prompt_complet,
            messages=[{"role": "user", "content": question}]
        )

        reponse = message.content[0].text

        # Extraire le JSON avec la nouvelle fonction robuste
        result, erreur = extraire_json_claude(reponse)
        if erreur:
            logger.error(f"Extraction JSON échouée: {erreur}")
            return None

        # Valider la structure
        valide, erreurs_structure = valider_structure_analyse(result)
        if not valide:
            logger.error(f"Structure JSON invalide: {erreurs_structure}")
            return None

        # Ajouter l'alerte macro si présente
        if alerte_macro and not result.get('alerte_macro'):
            result['alerte_macro'] = alerte_macro

        return result
    except Exception as e:
        logger.error(f"Erreur analyse: {e}")
        return None

def generer_cloture_json(donnees):
    """Génère l'analyse de clôture en JSON"""
    maintenant = get_paris_time()
    demain = maintenant + timedelta(days=1)

    donnees_texte = json.dumps(donnees, ensure_ascii=False, indent=2)

    question = f"""DONNÉES MARCHÉ FIN DE JOURNÉE:
{donnees_texte}

Date: {maintenant.strftime('%d/%m/%Y')}
Demain: {demain.strftime('%d/%m/%Y')}

Génère un résumé de clôture complet en JSON selon le format demandé."""

    try:
        message = client_anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            system=SYSTEM_PROMPT_CLOTURE,
            messages=[{"role": "user", "content": question}]
        )

        reponse = message.content[0].text
        result, erreur = extraire_json_claude(reponse)
        if erreur:
            logger.error(f"Extraction JSON clôture échouée: {erreur}")
            return None
        return result
    except Exception as e:
        logger.error(f"Erreur clôture: {e}")
        return None

# ============================================================================
# GESTION DES TRADES
# ============================================================================

def enrichir_opportunite_avec_donnees_marche(opportunite, donnees_marche, indicateurs_calcules=None):
    """
    Enrichit une opportunité Claude avec les données de marché réelles.
    Garantit que les indicateurs sont enregistrés même si Claude les oublie.
    """
    opp = opportunite.copy()
    actif_nom = opp.get('actif', '')
    symbole = opp.get('symbole', '')

    # Chercher les données de marché pour cet actif
    donnees_actif = None
    for nom, data in donnees_marche.items():
        if isinstance(data, dict):
            if nom == actif_nom or data.get('symbole') == symbole:
                donnees_actif = data
                break

    if donnees_actif:
        # Compléter avec les données de marché (si pas déjà présent)
        if not opp.get('atr_pct'):
            opp['atr_pct'] = donnees_actif.get('atr_pct')
        if not opp.get('volume_relatif'):
            opp['volume_relatif'] = donnees_actif.get('volume_relatif')
        if not opp.get('pivot'):
            opp['pivot'] = donnees_actif.get('pivot')
        if not opp.get('support1'):
            opp['support1'] = donnees_actif.get('support1')
        if not opp.get('resistance1'):
            opp['resistance1'] = donnees_actif.get('resistance1')

    # Chercher les indicateurs calculés pour cet actif
    if indicateurs_calcules:
        indic = indicateurs_calcules.get(actif_nom)
        if indic:
            if not opp.get('rsi'):
                opp['rsi'] = indic.get('RSI')
            if not opp.get('rsi_signal') and not opp.get('RSI_signal'):
                opp['rsi_signal'] = indic.get('RSI_signal')
            if not opp.get('macd_signal') and not opp.get('MACD'):
                opp['macd_signal'] = indic.get('MACD')

    return opp

def enregistrer_recommandation(trade_data):
    """Enregistre une recommandation de trade (avec fermeture DB garantie)

    CHANGEMENT v2: Validation stricte au lieu d'auto-correction.
    Les trades incohérents sont REJETÉS et loggés, pas corrigés silencieusement.
    """
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        maintenant = get_paris_time()

        # Injecter le régime de marché actuel si non présent (pour validation R:R adaptatif)
        if 'regime_marche' not in trade_data or not trade_data.get('regime_marche'):
            regime, _, _ = get_regime_marche()
            trade_data['regime_marche'] = regime

        # VALIDATION STRICTE avec la nouvelle fonction
        valide, opp_validee, avertissements, erreurs = valider_opportunite(trade_data)

        if not valide:
            # Trade rejeté - logger les erreurs et retourner None
            symbole = trade_data.get('symbole', trade_data.get('actif', 'N/A'))
            logger.error(f"❌ Trade REJETÉ [{symbole}]: {'; '.join(erreurs)}")
            print(f"❌ Trade REJETÉ [{symbole}]: {'; '.join(erreurs)}")
            if conn:
                conn.close()
            return None

        # Logger les avertissements (non bloquants)
        if avertissements:
            symbole = trade_data.get('symbole', trade_data.get('actif', 'N/A'))
            logger.warning(f"⚠️ Trade [{symbole}] avertissements: {'; '.join(avertissements)}")

        # Utiliser les données validées
        entree = float(opp_validee.get('entree', 0))
        stop = float(opp_validee.get('stop', 0))
        tp1 = float(opp_validee.get('tp1', 0))
        tp2 = float(opp_validee.get('tp2', 0) or 0)
        direction = opp_validee.get('direction', 'LONG')

        # Trouver le symbole et la catégorie
        symbole = trade_data.get('symbole', '')
        if not symbole:
            # Essayer de trouver le symbole à partir du nom
            actif_nom = trade_data.get('actif', '')
            for sym, nom in ACTIFS_PERMANENTS.items():
                if nom == actif_nom:
                    symbole = sym
                    break
            # Chercher aussi dans le pool de rotation
            if not symbole:
                for pool in POOL_ROTATION.values():
                    for sym, nom in pool.items():
                        if nom == actif_nom:
                            symbole = sym
                            break

        categorie = get_categorie_actif(symbole) if symbole else 'autre'

        # Extraire les indicateurs techniques de la recommandation
        rsi_reco = trade_data.get('rsi')
        rsi_signal_reco = trade_data.get('rsi_signal') or trade_data.get('RSI_signal')
        macd_signal_reco = trade_data.get('macd_signal') or trade_data.get('MACD')
        atr_pct_reco = trade_data.get('atr_pct')
        volume_relatif_reco = trade_data.get('volume_relatif')
        pivot_reco = trade_data.get('pivot')
        support1_reco = trade_data.get('support1')
        resistance1_reco = trade_data.get('resistance1')
        ratio_rr = trade_data.get('ratio_rr', '')
        ratio_rr_justification = trade_data.get('ratio_rr_justification', '')

        # A/B Testing - récupérer le groupe et la variante
        ab_groupe, ab_variante = get_variante_ab_pour_actif(symbole)
        ab_test_id = None
        if ab_groupe:
            # Trouver le test_id actif pour ce symbole
            for tid, test in AB_TESTS_ACTIFS.items():
                groupe_key = 'groupe_a' if ab_groupe == 'A' else 'groupe_b'
                if symbole in test.get(groupe_key, []):
                    ab_test_id = tid
                    break

        # === NOUVEAUX CHAMPS A/B TESTING AVANCÉ ===
        # 1. Timing Entry/Exit
        strategie_entree = trade_data.get('strategie_entree', 'IMMEDIATE')
        trailing_stop = 1 if trade_data.get('trailing_stop') else 0
        trailing_stop_pct = trade_data.get('trailing_stop_pct')
        prix_limite_entree = trade_data.get('prix_limite_entree')

        # 2. Contexte Marché Dynamique
        vix_niveau = trade_data.get('vix_niveau')
        regime_marche = trade_data.get('regime_marche', 'NORMAL')
        regles_adaptees = trade_data.get('regles_adaptees', '')

        # 3. Time Decay / Urgence
        validite_minutes = trade_data.get('validite_minutes', 60)
        heure_expiration = trade_data.get('heure_expiration', '')
        urgence = trade_data.get('urgence', 'MOYENNE')

        # 4. Multi Timeframe
        trend_daily = trade_data.get('trend_daily')
        trend_h4 = trade_data.get('trend_h4')
        trend_h1 = trade_data.get('trend_h1')
        alignement_tf = trade_data.get('alignement_tf', 0)
        confluence_score = trade_data.get('confluence_score', 0)

        # 5. Analyse Sentiment
        sentiment_score = trade_data.get('sentiment_score', 0)
        sentiment_source = trade_data.get('sentiment_source', 'TECHNIQUE')
        sentiment_detail = trade_data.get('sentiment_detail', '')

        # 6. Saisonnalité
        jours_fr = ['LUNDI', 'MARDI', 'MERCREDI', 'JEUDI', 'VENDREDI', 'SAMEDI', 'DIMANCHE']
        jour_semaine = trade_data.get('jour_semaine') or jours_fr[maintenant.weekday()]
        session_marche = trade_data.get('session_marche', '')
        pattern_jour = trade_data.get('pattern_jour', '')

        # Déterminer la session si non fournie
        if not session_marche:
            heure = maintenant.hour
            if 9 <= heure < 11:
                session_marche = 'EU_OPEN'
            elif 13 <= heure < 15:
                session_marche = 'US_PREMARKET'
            elif 15 <= heure < 17:
                session_marche = 'US_OPEN'
            elif 17 <= heure < 18:
                session_marche = 'EU_CLOSE'
            elif 21 <= heure < 22:
                session_marche = 'US_CLOSE'
            else:
                session_marche = 'HORS_SESSION'

        # Score de conviction global
        conviction_score = trade_data.get('conviction_score', 3)

        # === TRADE QUALITY GRADING ===
        # Préparer les données pour le calcul du grade
        grade_data = {
            'conviction_score': conviction_score,
            'ratio_rr': ratio_rr,
            'alignement_tf': alignement_tf,
            'confluence_score': confluence_score,
            'regime_marche': regime_marche,
            'session_marche': session_marche,
            'sentiment_score': sentiment_score,
            'direction': direction
        }
        grade_result = calculer_trade_grade(grade_data)
        trade_grade = grade_result['grade']
        grade_details = json.dumps(grade_result['details'], ensure_ascii=False)
        grade_setup_score = grade_result['setup_score']

        logger.info(f"Trade Grade: {symbole} - {grade_result['resume']}")

        cursor.execute('''
            INSERT INTO trades_recommandes
            (date, heure_message, actif, symbole, type_setup, prix_entree, prix_stop,
             prix_tp1, prix_tp2, prix_actuel, catalyseur, duree_estimee, timestamp_reco,
             direction, categorie_actif, heure_entree,
             rsi_reco, rsi_signal_reco, macd_signal_reco, atr_pct_reco, volume_relatif_reco,
             pivot_reco, support1_reco, resistance1_reco, ratio_rr, ratio_rr_justification,
             ab_test_id, ab_groupe, ab_variante,
             strategie_entree, trailing_stop, trailing_stop_pct, prix_limite_entree,
             vix_niveau, regime_marche, regles_adaptees,
             validite_minutes, heure_expiration, urgence,
             trend_daily, trend_h4, trend_h1, alignement_tf, confluence_score,
             sentiment_score, sentiment_source, sentiment_detail,
             jour_semaine, session_marche, pattern_jour, conviction_score,
             trade_grade, grade_details, grade_setup_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            maintenant.date(),
            maintenant.strftime('%H:%M'),
            trade_data.get('actif', ''),
            symbole,
            'NEWS' if trade_data.get('is_news_trading') else 'TECHNIQUE',
            entree,
            stop,
            trade_data.get('tp1', 0),
            trade_data.get('tp2', 0),
            trade_data.get('prix_actuel', 0),
            trade_data.get('catalyseur', ''),
            trade_data.get('duree', ''),
            maintenant,
            direction,
            categorie,
            maintenant.hour,
            rsi_reco,
            rsi_signal_reco,
            macd_signal_reco,
            atr_pct_reco,
            volume_relatif_reco,
            pivot_reco,
            support1_reco,
            resistance1_reco,
            ratio_rr,
            ratio_rr_justification,
            ab_test_id,
            ab_groupe,
            ab_variante,
            # Nouveaux champs
            strategie_entree,
            trailing_stop,
            trailing_stop_pct,
            prix_limite_entree,
            vix_niveau,
            regime_marche,
            regles_adaptees,
            validite_minutes,
            heure_expiration,
            urgence,
            trend_daily,
            trend_h4,
            trend_h1,
            alignement_tf,
            confluence_score,
            sentiment_score,
            sentiment_source,
            sentiment_detail,
            jour_semaine,
            session_marche,
            pattern_jour,
            conviction_score,
            # Trade Quality Grading
            trade_grade,
            grade_details,
            grade_setup_score
        ))

        conn.commit()
        trade_id = cursor.lastrowid
        logger.info(f"Trade enregistré: #{trade_id} {symbole} {direction} @ {entree}")
        return trade_id
    except Exception as e:
        logger.error(f"Erreur enregistrement trade {symbole}: {e}")
        return None
    finally:
        if conn:
            conn.close()

def verifier_resultats_trades():
    """Vérifie et met à jour les résultats des trades ouverts (ordre chronologique)
    Avec suivi temps réel: prix max/min atteints, PnL max/min
    Utilise une transaction atomique pour éviter les états incohérents."""
    maintenant = get_paris_time()
    print(f"[{maintenant.strftime('%H:%M:%S')} CET] 🔍 Vérification résultats trades...")

    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        conn.isolation_level = 'DEFERRED'  # Transaction explicite
        cursor = conn.cursor()
        cursor.execute('BEGIN TRANSACTION')

        # Récupérer les trades sans résultat DU JOUR uniquement
        # (évite de vérifier des trades anciens avec des données intraday d'aujourd'hui)
        aujourdhui = maintenant.strftime('%Y-%m-%d')
        cursor.execute('''
            SELECT * FROM trades_recommandes
            WHERE resultat IS NULL
            AND symbole IS NOT NULL AND symbole != ''
            AND date = ?
        ''', (aujourdhui,))
        trades_ouverts = [dict(row) for row in cursor.fetchall()]

        if not trades_ouverts:
            print(f"[{maintenant.strftime('%H:%M:%S')} CET] ℹ️ Aucun trade à vérifier")
            return 0

        nb_mis_a_jour = 0
        nb_tracking = 0

        for trade in trades_ouverts:
            try:
                symbole = trade['symbole']
                direction = trade.get('direction', 'LONG')
                entree = float(trade['prix_entree'] or 0)
                stop = float(trade['prix_stop'] or 0)
                tp1 = float(trade['prix_tp1'] or 0)
                tp2 = float(trade['prix_tp2'] or 0)

                if entree <= 0:
                    print(f"   ⚠️ Trade {trade.get('actif', 'inconnu')}: prix entrée invalide ({entree})")
                    continue

                # Récupérer les données intraday via Twelve Data (5min, 288 bougies = 24h pour trades overnight)
                hist, _ = get_twelvedata_intraday(symbole, interval="5min", outputsize=288)

                if hist.empty:
                    continue

                resultat = None
                prix_sortie = None
                pnl_pct = None

                # Tracking temps réel: calculer max/min sur toutes les bougies
                prix_max_session = hist['High'].max()
                prix_min_session = hist['Low'].min()
                prix_actuel = hist['Close'].iloc[-1]

                # Récupérer les valeurs précédentes de tracking
                prix_max_atteint = trade.get('prix_max_atteint') or prix_max_session
                prix_min_atteint = trade.get('prix_min_atteint') or prix_min_session

                # Mettre à jour les extremes
                prix_max_atteint = max(prix_max_atteint, prix_max_session)
                prix_min_atteint = min(prix_min_atteint, prix_min_session)

                # Calculer PnL actuel et extremes
                if direction == 'LONG':
                    pnl_actuel = ((prix_actuel - entree) / entree) * 100
                    pnl_max = ((prix_max_atteint - entree) / entree) * 100
                    pnl_min = ((prix_min_atteint - entree) / entree) * 100
                else:
                    pnl_actuel = ((entree - prix_actuel) / entree) * 100
                    pnl_max = ((entree - prix_min_atteint) / entree) * 100  # Inversé pour SHORT
                    pnl_min = ((entree - prix_max_atteint) / entree) * 100

                # Comparer avec les PnL extrêmes précédents
                prev_pnl_max = trade.get('pnl_max') or pnl_max
                prev_pnl_min = trade.get('pnl_min') or pnl_min
                pnl_max = max(pnl_max, prev_pnl_max)
                pnl_min = min(pnl_min, prev_pnl_min)

                nb_checks = (trade.get('nb_checks') or 0) + 1

                # NOUVELLE LOGIQUE: Utiliser analyser_cloture_intraday
                # Prend en compte: ordre chronologique, slippage, priorité intra-bougie
                resultat, prix_sortie, pnl_pct, bougie_cloture = analyser_cloture_intraday(
                    hist, direction, entree, stop, tp1, tp2
                )

                if resultat:
                    # Trade terminé - calculer durée
                    duree_minutes = None
                    if trade.get('timestamp_reco'):
                        try:
                            ts_reco = datetime.fromisoformat(trade['timestamp_reco'].replace('Z', '+00:00'))
                            duree_minutes = int((maintenant.replace(tzinfo=None) - ts_reco.replace(tzinfo=None)).total_seconds() / 60)
                        except ValueError:
                            pass

                    cursor.execute('''
                        UPDATE trades_recommandes
                        SET resultat = ?, prix_sortie = ?, pnl_pct = ?, timestamp_sortie = ?,
                            duree_minutes = ?, prix_max_atteint = ?, prix_min_atteint = ?,
                            prix_dernier_check = ?, timestamp_dernier_check = ?,
                            pnl_max = ?, pnl_min = ?, nb_checks = ?
                        WHERE id = ?
                    ''', (resultat, prix_sortie, round(pnl_pct, 2), maintenant,
                          duree_minutes, round(prix_max_atteint, 4), round(prix_min_atteint, 4),
                          round(prix_actuel, 4), maintenant,
                          round(pnl_max, 2), round(pnl_min, 2), nb_checks,
                          trade['id']))
                    nb_mis_a_jour += 1
                    duree_str = f" ({duree_minutes}min)" if duree_minutes else ""
                    logger.info(f"Trade clos: {trade['actif']} {resultat} PnL={pnl_pct:+.2f}% Max={pnl_max:+.2f}%{duree_str}")
                    print(f"   ✅ {trade['actif']}: {resultat} ({pnl_pct:+.2f}%) | Max: {pnl_max:+.2f}%{duree_str}")
                else:
                    # Trade en cours - mettre à jour le tracking temps réel
                    cursor.execute('''
                        UPDATE trades_recommandes
                        SET prix_max_atteint = ?, prix_min_atteint = ?,
                            prix_dernier_check = ?, timestamp_dernier_check = ?,
                            pnl_max = ?, pnl_min = ?, nb_checks = ?
                        WHERE id = ?
                    ''', (round(prix_max_atteint, 4), round(prix_min_atteint, 4),
                          round(prix_actuel, 4), maintenant,
                          round(pnl_max, 2), round(pnl_min, 2), nb_checks,
                          trade['id']))
                    nb_tracking += 1
                    print(f"   📊 {trade['actif']}: En cours {pnl_actuel:+.2f}% | Max: {pnl_max:+.2f}% | Min: {pnl_min:+.2f}%")

            except Exception as e:
                logger.warning(f"Erreur trade {trade.get('actif', 'inconnu')}: {e}")
                continue

        conn.commit()  # Commit de la transaction atomique

        print(f"[{maintenant.strftime('%H:%M:%S')} CET] ✅ {nb_mis_a_jour} terminé(s), {nb_tracking} en suivi")
        return nb_mis_a_jour

    except Exception as e:
        logger.error(f"Erreur vérification trades: {e}")
        if conn:
            try:
                conn.rollback()  # Annuler toutes les modifications en cas d'erreur
                print("🔄 Transaction annulée (rollback)")
            except:
                pass
        return 0
    finally:
        if conn:
            conn.close()

def analyser_historique_intraday_pour_tp_stop(symbole, direction, entree, stop, tp1, tp2):
    """
    Analyse l'historique intraday complet pour détecter si TP ou Stop a été touché.
    Retourne: (resultat, prix_sortie, note) ou (None, None, None) si rien touché.
    """
    # Récupérer l'historique intraday complet (5min, 288 bougies = 24h pour trades overnight)
    hist, _ = get_twelvedata_intraday(symbole, interval="5min", outputsize=288)

    if hist.empty:
        return None, None, None

    # Parcourir chronologiquement pour trouver le premier événement
    for idx, row in hist.iterrows():
        high = row['High']
        low = row['Low']

        if direction == 'LONG':
            # Vérifier STOP d'abord (priorité au stop)
            if stop > 0 and low <= stop:
                return 'STOP', stop, f"Stop touché à {idx}"
            # Puis TP2
            if tp2 and tp2 > 0 and high >= tp2:
                return 'TP2', tp2, f"TP2 touché à {idx}"
            # Puis TP1
            if tp1 and tp1 > 0 and high >= tp1:
                return 'TP1', tp1, f"TP1 touché à {idx}"
        else:  # SHORT
            # Vérifier STOP d'abord
            if stop > 0 and high >= stop:
                return 'STOP', stop, f"Stop touché à {idx}"
            # Puis TP2
            if tp2 and tp2 > 0 and low <= tp2:
                return 'TP2', tp2, f"TP2 touché à {idx}"
            # Puis TP1
            if tp1 and tp1 > 0 and low <= tp1:
                return 'TP1', tp1, f"TP1 touché à {idx}"

    return None, None, None

def reevaluer_trades_intraday():
    """
    Réévaluation active des trades en cours.
    Analyse chaque trade et propose des ajustements:
    - Move stop to breakeven si TP50% atteint
    - Trailing stop activation si conviction >= 4
    - Alerte si le trade approche du stop
    - Recommandation: HOLD / RENFORCER / REDUIRE / SORTIR
    """
    maintenant = get_paris_time()
    print(f"[{maintenant.strftime('%H:%M:%S')} CET] 🔄 Réévaluation intraday des trades...")

    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        aujourdhui = maintenant.strftime('%Y-%m-%d')
        cursor.execute('''
            SELECT * FROM trades_recommandes
            WHERE resultat IS NULL
            AND symbole IS NOT NULL AND symbole != ''
            AND date = ?
        ''', (aujourdhui,))
        trades_ouverts = [dict(row) for row in cursor.fetchall()]

        if not trades_ouverts:
            print(f"   Aucun trade ouvert à réévaluer")
            return []

        print(f"   {len(trades_ouverts)} trade(s) à réévaluer")

        reevaluations = []

        for trade in trades_ouverts:
            try:
                symbole = trade.get('symbole')
                if not symbole:
                    continue

                # Récupérer le prix actuel
                quote, _ = get_fresh_quote_for_trade(symbole)
                if not quote:
                    continue

                prix_actuel = quote.get('prix', 0)
                if prix_actuel <= 0:
                    continue

                entree = trade.get('prix_entree', 0)
                stop = trade.get('prix_stop', 0)
                tp1 = trade.get('prix_tp1', 0)
                direction = trade.get('direction', 'LONG')
                conviction = trade.get('conviction_score', 3)
                trailing_stop_actif = trade.get('trailing_stop', 0)
                trailing_stop_pct = trade.get('trailing_stop_pct', 0.3)

                if entree <= 0:
                    continue

                # Calcul PnL actuel
                if direction == 'LONG':
                    pnl_pct = ((prix_actuel - entree) / entree) * 100
                else:
                    pnl_pct = ((entree - prix_actuel) / entree) * 100

                # Calcul distance au TP et au Stop
                if direction == 'LONG':
                    distance_tp_pct = ((tp1 - prix_actuel) / prix_actuel) * 100 if tp1 > 0 else 0
                    distance_stop_pct = ((prix_actuel - stop) / prix_actuel) * 100 if stop > 0 else 0
                else:
                    distance_tp_pct = ((prix_actuel - tp1) / prix_actuel) * 100 if tp1 > 0 else 0
                    distance_stop_pct = ((stop - prix_actuel) / prix_actuel) * 100 if stop > 0 else 0

                # Déterminer le statut et l'action recommandée avec PRIORITÉ
                # Priorité: SORTIR > REDUIRE > RENFORCER > HOLD
                # On accumule les signaux et on garde le plus critique
                statut_intraday = trade.get('statut_intraday', 'EN_COURS')
                stop_ajuste = None
                detail_reevaluation = []
                signaux = []  # Liste de (priorité, action, raison)

                # 1. Vérifier si TP50% atteint → Breakeven
                tp_pct = ((tp1 - entree) / entree) * 100 if direction == 'LONG' and tp1 > 0 else \
                         ((entree - tp1) / entree) * 100 if direction == 'SHORT' and tp1 > 0 else 0

                if tp_pct > 0 and pnl_pct >= tp_pct * 0.5 and statut_intraday == 'EN_COURS':
                    statut_intraday = 'TP50_ATTEINT'
                    stop_ajuste = entree  # Breakeven
                    detail_reevaluation.append(f"✅ TP50% atteint ({pnl_pct:+.2f}%), stop → breakeven")
                    signaux.append((0, 'HOLD', 'TP50% protégé'))

                # 2. Trailing stop si activé et conviction élevée
                if trailing_stop_actif and conviction >= 4:
                    if direction == 'LONG':
                        nouveau_stop = prix_actuel * (1 - trailing_stop_pct / 100)
                        if nouveau_stop > (stop_ajuste or stop):
                            stop_ajuste = nouveau_stop
                            detail_reevaluation.append(f"📈 Trailing stop → {nouveau_stop:.2f}")
                    else:
                        nouveau_stop = prix_actuel * (1 + trailing_stop_pct / 100)
                        if nouveau_stop < (stop_ajuste or stop):
                            stop_ajuste = nouveau_stop
                            detail_reevaluation.append(f"📉 Trailing stop → {nouveau_stop:.2f}")

                # 3. Alerte si proche du stop (< 30% du chemin original restant)
                if direction == 'LONG' and stop > 0:
                    distance_originale_pct = ((entree - stop) / entree) * 100
                elif direction == 'SHORT' and stop > 0:
                    distance_originale_pct = ((stop - entree) / entree) * 100
                else:
                    distance_originale_pct = 0

                seuil_alerte = distance_originale_pct * 0.30
                if distance_stop_pct > 0 and distance_stop_pct < seuil_alerte:
                    pct_restant = (distance_stop_pct / distance_originale_pct * 100) if distance_originale_pct > 0 else 0
                    detail_reevaluation.append(f"⚠️ PROCHE DU STOP ({pct_restant:.0f}% restant)")
                    if pnl_pct < -0.3:
                        signaux.append((2, 'REDUIRE', f'Proche stop avec perte {pnl_pct:.2f}%'))

                # 4. Renforcer si très positif et haute conviction
                if pnl_pct > 0.5 and conviction >= 4 and statut_intraday != 'TP50_ATTEINT':
                    detail_reevaluation.append(f"💪 En profit ({pnl_pct:+.2f}%), renforcement possible")
                    signaux.append((1, 'RENFORCER', f'Profit {pnl_pct:.2f}% + conviction {conviction}'))

                # 5. Sortir si momentum inversé
                pnl_max = trade.get('pnl_max', 0) or 0
                if pnl_max > 0.5 and pnl_pct < pnl_max * 0.3:
                    detail_reevaluation.append(f"🔻 Momentum perdu: max {pnl_max:+.2f}% → actuel {pnl_pct:+.2f}%")
                    signaux.append((3, 'SORTIR', f'Perte momentum ({pnl_max:.2f}% → {pnl_pct:.2f}%)'))

                # 6. Sortir si perte importante (> 50% du stop)
                if pnl_pct < 0 and distance_originale_pct > 0:
                    perte_vs_stop = abs(pnl_pct) / distance_originale_pct
                    if perte_vs_stop > 0.7:  # > 70% du chemin vers le stop
                        signaux.append((3, 'SORTIR', f'Perte critique {pnl_pct:.2f}% (70%+ vers stop)'))

                # Déterminer l'action finale par priorité (le plus haut gagne)
                if signaux:
                    signaux.sort(key=lambda x: x[0], reverse=True)
                    action_recommandee = signaux[0][1]
                    # Ajouter toutes les raisons au détail
                    if len(signaux) > 1:
                        autres_signaux = [f"{s[1]}:{s[2]}" for s in signaux[1:]]
                        detail_reevaluation.append(f"[Autres signaux: {', '.join(autres_signaux)}]")
                else:
                    action_recommandee = 'HOLD'

                # Construire la réévaluation
                reevaluation = {
                    'trade_id': trade['id'],
                    'actif': trade.get('actif'),
                    'symbole': symbole,
                    'direction': direction,
                    'pnl_actuel': round(pnl_pct, 2),
                    'distance_tp_pct': round(distance_tp_pct, 2),
                    'distance_stop_pct': round(distance_stop_pct, 2),
                    'statut_intraday': statut_intraday,
                    'action_recommandee': action_recommandee,
                    'stop_ajuste': round(stop_ajuste, 4) if stop_ajuste else None,
                    'detail': ' | '.join(detail_reevaluation) if detail_reevaluation else 'RAS',
                    'timestamp': maintenant.isoformat()
                }
                reevaluations.append(reevaluation)

                # Mettre à jour le trade dans la base
                nb_reevaluations = (trade.get('nb_reevaluations') or 0) + 1
                anciennes_reeval = trade.get('reevaluations')
                if anciennes_reeval:
                    try:
                        liste_reeval = json.loads(anciennes_reeval)
                    except:
                        liste_reeval = []
                else:
                    liste_reeval = []
                liste_reeval.append(reevaluation)
                # Garder les 10 dernières
                liste_reeval = liste_reeval[-10:]

                cursor.execute('''
                    UPDATE trades_recommandes
                    SET statut_intraday = ?,
                        action_recommandee = ?,
                        stop_ajuste = ?,
                        nb_reevaluations = ?,
                        reevaluations = ?
                    WHERE id = ?
                ''', (
                    statut_intraday,
                    action_recommandee,
                    stop_ajuste,
                    nb_reevaluations,
                    json.dumps(liste_reeval, ensure_ascii=False),
                    trade['id']
                ))

                emoji = {'HOLD': '✋', 'RENFORCER': '💪', 'REDUIRE': '📉', 'SORTIR': '🚪'}.get(action_recommandee, '❓')
                print(f"   {emoji} {trade.get('actif')}: {pnl_pct:+.2f}% → {action_recommandee}")

            except Exception as e:
                print(f"   ⚠️ Erreur rééval {trade.get('actif', 'inconnu')}: {e}")
                continue

        conn.commit()
        print(f"[{maintenant.strftime('%H:%M:%S')} CET] ✅ {len(reevaluations)} trade(s) réévalué(s)")
        return reevaluations

    except Exception as e:
        logger.error(f"Erreur réévaluation intraday: {e}")
        return []
    finally:
        if conn:
            conn.close()

def cloturer_trades_jour():
    """
    Clôture FORCÉE des trades 15 minutes avant fermeture du marché.
    AMÉLIORATION: Analyse l'historique intraday pour détecter si TP/Stop a été touché
    plus tôt dans la journée (évite les faux WIN_FORCE/LOSS_FORCE).
    - Si TP/Stop touché: utilise le vrai résultat (TP1, TP2, STOP)
    - Sinon: PnL > 0 = WIN_FORCE, PnL <= 0 = LOSS_FORCE
    """
    maintenant = get_paris_time()
    aujourdhui = maintenant.date()

    print(f"[{maintenant.strftime('%H:%M:%S')} CET] 🔔 Clôture forcée trades (15min avant fermeture)...")

    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Récupérer les trades du jour sans résultat
        cursor.execute('''
            SELECT * FROM trades_recommandes
            WHERE date = ? AND resultat IS NULL AND symbole IS NOT NULL AND symbole != ''
        ''', (aujourdhui,))
        trades_ouverts = [dict(row) for row in cursor.fetchall()]

        nb_clotures = 0
        total_pnl = 0

        for trade in trades_ouverts:
            try:
                symbole = trade['symbole']
                direction = trade.get('direction', 'LONG')
                entree = float(trade['prix_entree'] or 0)
                stop = float(trade['prix_stop'] or 0)
                tp1 = float(trade['prix_tp1'] or 0)
                tp2 = float(trade['prix_tp2'] or 0)

                if entree <= 0:
                    print(f"   ⚠️ Trade {trade.get('actif', 'inconnu')}: prix entrée invalide ({entree})")
                    continue

                # AMÉLIORATION: Vérifier si TP/Stop a été touché plus tôt dans la journée
                resultat_historique, prix_historique, note_historique = analyser_historique_intraday_pour_tp_stop(
                    symbole, direction, entree, stop, tp1, tp2
                )

                if resultat_historique and prix_historique is not None:
                    # TP ou Stop a été touché plus tôt - utiliser ce résultat
                    resultat = resultat_historique
                    prix_sortie = float(prix_historique)

                    if direction == 'LONG':
                        pnl_pct = ((prix_sortie - entree) / entree) * 100
                    else:
                        pnl_pct = ((entree - prix_sortie) / entree) * 100

                    emoji = '🎯' if resultat in ['TP1', 'TP2'] else '🛑'
                    note_cloture = f" | {note_historique} (détecté à clôture)"
                else:
                    # Aucun TP/Stop touché - utiliser le prix actuel
                    quote = get_twelvedata_quote(symbole)

                    if not quote:
                        hist, _ = get_twelvedata_time_series(symbole, outputsize=1, interval="1day")
                        if hist.empty:
                            continue
                        prix_sortie = hist['Close'].iloc[-1]
                    else:
                        prix_sortie = quote.get('price', 0)

                    if prix_sortie == 0:
                        continue

                    if direction == 'LONG':
                        pnl_pct = ((prix_sortie - entree) / entree) * 100
                    else:
                        pnl_pct = ((entree - prix_sortie) / entree) * 100

                    # Classifier le résultat avec tolérance pour BREAKEVEN
                    if pnl_pct > 0.05:  # Win si > 0.05%
                        resultat = 'WIN_FORCE'
                        emoji = '✅'
                    elif pnl_pct >= -0.05:  # Breakeven si entre -0.05% et +0.05%
                        resultat = 'BREAKEVEN'
                        emoji = '⚖️'
                    else:  # Perte si < -0.05%
                        resultat = 'LOSS_FORCE'
                        emoji = '❌'

                    note_cloture = " | Clôture forcée 15min avant fermeture"

                # Calculer la durée
                duree_minutes = None
                if trade.get('timestamp_reco'):
                    try:
                        ts_reco = datetime.fromisoformat(trade['timestamp_reco'].replace('Z', '+00:00'))
                        duree_minutes = int((maintenant.replace(tzinfo=None) - ts_reco.replace(tzinfo=None)).total_seconds() / 60)
                    except ValueError:
                        pass

                # Récupérer les valeurs de tracking si disponibles
                prix_max = trade.get('prix_max_atteint') or prix_sortie
                prix_min = trade.get('prix_min_atteint') or prix_sortie
                pnl_max = trade.get('pnl_max') or pnl_pct
                pnl_min = trade.get('pnl_min') or pnl_pct

                # Mettre à jour le trade
                cursor.execute('''
                    UPDATE trades_recommandes
                    SET resultat = ?, prix_sortie = ?, pnl_pct = ?, timestamp_sortie = ?,
                        duree_minutes = ?, prix_dernier_check = ?, timestamp_dernier_check = ?,
                        notes = COALESCE(notes, '') || ?
                    WHERE id = ?
                ''', (
                    resultat, round(prix_sortie, 4), round(pnl_pct, 2), maintenant,
                    duree_minutes, round(prix_sortie, 4), maintenant,
                    note_cloture,
                    trade['id']
                ))

                nb_clotures += 1
                total_pnl += pnl_pct
                duree_str = f" ({duree_minutes}min)" if duree_minutes else ""

                # Afficher avec les infos de tracking
                max_info = f" | Max atteint: {pnl_max:+.2f}%" if pnl_max != pnl_pct else ""
                print(f"   {emoji} {trade['actif']}: {resultat} ({pnl_pct:+.2f}%){max_info}{duree_str}")

            except Exception as e:
                print(f"   ⚠️ Erreur clôture {trade.get('actif', 'inconnu')}: {e}")
                continue

        conn.commit()

        if nb_clotures > 0:
            print(f"[{maintenant.strftime('%H:%M:%S')} CET] 📊 Bilan clôture: {nb_clotures} trade(s), PnL total: {total_pnl:+.2f}%")
        else:
            print(f"[{maintenant.strftime('%H:%M:%S')} CET] ✅ Aucun trade à clôturer")

        return nb_clotures

    except Exception as e:
        logger.error(f"Erreur clôture trades: {e}")
        return 0
    finally:
        if conn:
            conn.close()

def get_trades_du_jour():
    """Récupère les trades du jour"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        aujourdhui = get_paris_time().date()
        cursor.execute('''
            SELECT * FROM trades_recommandes
            WHERE date = ?
            ORDER BY timestamp_reco DESC
        ''', (aujourdhui,))

        trades = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return trades
    except Exception as e:
        print(f"⚠️ Erreur récupération trades: {e}")
        return []

def get_performances(periode='semaine'):
    """Récupère les performances sur une période"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        cursor = conn.cursor()

        maintenant = get_paris_time()
        if periode == 'jour':
            date_debut = maintenant.date()
        elif periode == 'semaine':
            date_debut = (maintenant - timedelta(days=7)).date()
        elif periode == 'mois':
            date_debut = (maintenant - timedelta(days=30)).date()
        else:
            date_debut = (maintenant - timedelta(days=365)).date()

        cursor.execute('''
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2') THEN 1 ELSE 0 END) as reussis_naturel,
                SUM(CASE WHEN resultat = 'WIN_FORCE' THEN 1 ELSE 0 END) as reussis_force,
                SUM(CASE WHEN resultat = 'STOP' THEN 1 ELSE 0 END) as stops_naturel,
                SUM(CASE WHEN resultat = 'LOSS_FORCE' THEN 1 ELSE 0 END) as stops_force,
                SUM(CASE WHEN resultat = 'NON_CONCLU' THEN 1 ELSE 0 END) as non_conclus,
                AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as pnl_moyen,
                SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as pnl_total
            FROM trades_recommandes
            WHERE date >= ?
        ''', (date_debut,))

        row = cursor.fetchone()
        conn.close()

        total = row[0] or 0
        reussis_naturel = row[1] or 0
        reussis_force = row[2] or 0
        stops_naturel = row[3] or 0
        stops_force = row[4] or 0
        non_conclus = row[5] or 0

        # Totaux combinés (naturels + forcés)
        reussis = reussis_naturel + reussis_force
        stops = stops_naturel + stops_force

        # Conclus = uniquement les trades avec résultat définitif
        conclus = reussis + stops

        return {
            'total': total,
            'reussis': reussis,
            'reussis_naturel': reussis_naturel,  # TP1, TP2
            'reussis_force': reussis_force,       # WIN_FORCE (clôture forcée positive)
            'stops': stops,
            'stops_naturel': stops_naturel,       # STOP
            'stops_force': stops_force,           # LOSS_FORCE (clôture forcée négative)
            'non_conclus': non_conclus,           # Historique (ne devrait plus arriver)
            'en_cours': total - conclus - non_conclus,  # Trades en cours
            'taux_reussite': round((reussis / conclus * 100) if conclus > 0 else 0, 1),
            'pnl_moyen': round(row[6] or 0, 2),
            'pnl_total': round(row[7] or 0, 2)
        }
    except Exception as e:
        print(f"⚠️ Erreur performances: {e}")
        return {'total': 0, 'reussis': 0, 'reussis_naturel': 0, 'reussis_force': 0, 'stops': 0, 'stops_naturel': 0, 'stops_force': 0, 'non_conclus': 0, 'en_cours': 0, 'taux_reussite': 0, 'pnl_moyen': 0, 'pnl_total': 0}

# ============================================================================
# JOURNAL DES ACTIFS (MÉMOIRE)
# ============================================================================

def ajouter_entree_journal(symbole, nom_actif, type_info, titre, contenu, impact_cours='', importance=2):
    """Ajoute une entrée au journal d'un actif"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        maintenant = get_paris_time()

        cursor.execute('''
            INSERT INTO journal_actifs
            (date, symbole, nom_actif, type_info, titre, contenu, impact_cours, importance, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            maintenant.date(),
            symbole,
            nom_actif,
            type_info,
            titre,
            contenu,
            impact_cours,
            importance,
            maintenant
        ))

        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"⚠️ Erreur ajout journal: {e}")
        return False

def get_journal_actif(symbole, limite=50):
    """Récupère le journal d'un actif"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM journal_actifs
            WHERE symbole = ?
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (symbole, limite))

        entries = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return entries
    except Exception as e:
        print(f"⚠️ Erreur journal: {e}")
        return []

def get_tous_journaux():
    """Récupère tous les journaux regroupés par actif"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('''
            SELECT DISTINCT symbole, nom_actif,
                   COUNT(*) as nb_entrees,
                   MAX(timestamp) as derniere_maj
            FROM journal_actifs
            GROUP BY symbole
            ORDER BY derniere_maj DESC
        ''')

        actifs = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return actifs
    except Exception as e:
        print(f"⚠️ Erreur journaux: {e}")
        return []

# ============================================================================
# CATÉGORIES D'ACTIFS
# ============================================================================

def get_categorie_actif(symbole):
    """Détermine la catégorie d'un actif"""
    if symbole.startswith('^'):
        return 'indice'
    elif symbole.endswith('.PA') or symbole.endswith('.DE') or symbole.endswith('.MI'):
        return 'action_eu'
    elif symbole.endswith('=X'):
        return 'forex'
    elif symbole.endswith('=F'):
        return 'commodite'
    elif len(symbole) <= 5 and symbole.isalpha():
        return 'action_us'
    return 'autre'

# ============================================================================
# PRÉ-MARKET
# ============================================================================

def recuperer_donnees_premarket():
    """Récupère les données pré-market (futures, overnight gaps)
    OPTIMISÉ: Utilise BATCH API au lieu d'appels individuels"""
    premarket_symbols = {
        "ES=F": "S&P 500 Futures",
        "NQ=F": "Nasdaq Futures",
        "YM=F": "Dow Futures",
        "^N225": "Nikkei 225",
        "^HSI": "Hang Seng",
        "^STOXX50E": "Euro Stoxx 50"
    }

    donnees = {}

    # BATCH API: un seul appel pour tous les symboles
    symboles_list = list(premarket_symbols.keys())
    batch_results = get_twelvedata_batch(symboles_list, outputsize=2, interval="1day")

    for symbole, nom in premarket_symbols.items():
        try:
            info, _ = batch_results.get(symbole, (pd.DataFrame(), False))
            if not info.empty and len(info) >= 2:
                prix_hier = info['Close'].iloc[-2]
                prix_actuel = info['Close'].iloc[-1]
                if prix_hier > 0:  # Évite division par zéro
                    variation = ((prix_actuel - prix_hier) / prix_hier) * 100
                    donnees[nom] = {
                        "symbole": symbole,
                        "prix": float(round(prix_actuel, 2)),
                        "prix_hier": float(round(prix_hier, 2)),
                        "variation_overnight": float(round(variation, 2))
                    }
        except Exception as e:
            print(f"⚠️ Erreur premarket {symbole}: {e}")

    return donnees

# ============================================================================
# JOURNAL QUOTIDIEN AUTOMATIQUE
# ============================================================================

SYSTEM_PROMPT_JOURNAL = """Tu es un spéculateur expérimenté qui tient un carnet de bord quotidien.

Pour chaque actif, rédige un commentaire UNIQUEMENT si quelque chose de notable s'est passé:
- Mouvement significatif (>1.5% ou inhabituel pour l'actif)
- Cassure de niveau technique important
- News ou événement macro impactant
- Nouveau record historique ou annuel
- Changement de tendance ou de momentum
- Corrélation intéressante avec d'autres actifs

Si rien de notable, ne mets PAS de commentaire pour cet actif.

Style: Direct, factuel, langage de trader. Pas de langue de bois.
Exemples de bons commentaires:
- "Nouveau record historique à 6150. Le momentum reste puissant, pas de signe d'essoufflement."
- "Breakout des 2050$ confirmé sur fond de tensions Iran. Le prochain objectif technique est 2100$."
- "Chute de 4% suite aux résultats décevants. Support à surveiller à 145€."
- "Range serré 1.08-1.085, attente des NFP demain."

FORMAT DE RÉPONSE EN JSON:
{
  "commentaires": {
    "SYMBOLE1": "Commentaire si notable...",
    "SYMBOLE2": "Commentaire si notable..."
  },
  "faits_marquants": [
    "Fait marquant 1 de la journée (ex: S&P 500 nouveau record, tensions géopolitiques, etc.)",
    "Fait marquant 2"
  ]
}"""

SYSTEM_PROMPT_BILAN = """Tu es un spéculateur expérimenté qui fait le bilan de la journée de trading.
Analyse les données du jour et rédige un bilan honnête et constructif.

Le bilan doit inclure:
1. Un résumé des marchés du jour (3-4 phrases)
2. Les points positifs: ce qui a bien fonctionné dans les recommandations
3. Les points négatifs: ce qui n'a pas fonctionné, les erreurs
4. Les leçons apprises: ce qu'on peut retenir pour demain

Sois direct, factuel et autocritique. Pas de langue de bois.

FORMAT DE RÉPONSE EN JSON:
{
  "resume": "Paragraphe résumant la journée des marchés...",
  "points_positifs": ["Point 1", "Point 2"],
  "points_negatifs": ["Point 1", "Point 2"],
  "lecons_apprises": ["Leçon 1", "Leçon 2"]
}"""

SYSTEM_PROMPT_RAPPORT_HEBDO = """Tu es un spéculateur expérimenté qui analyse la performance hebdomadaire de ton système de trading.

Analyse les données de la semaine et génère un rapport complet incluant:
1. Un résumé exécutif de la semaine (performance globale, contexte marché)
2. Analyse des forces et faiblesses identifiées
3. Les patterns observés (heures rentables, types de setups qui marchent)
4. ANALYSE DES INDICATEURS: Quel RSI, MACD, ATR a généré les meilleurs résultats?
5. Recommandations d'ajustement pour la semaine prochaine
6. Score de confiance pour chaque catégorie d'actifs et type de setup

FOCUS SUR LES INDICATEURS (données fournies):
- stats_par_rsi: Performance par signal RSI (SURVENTE/NEUTRE/SURACHAT)
- stats_par_macd: Performance par signal MACD (BULLISH/BEARISH/CROSS)
- stats_par_ratio_rr: Performance par ratio Risk/Reward
- stats_par_atr: Performance par niveau de volatilité (FAIBLE/MOYEN/ELEVE)

Utilise ces données pour recommander des CRITÈRES PRÉCIS, ex:
- "Privilégier RSI SURVENTE pour LONG (taux 75% cette semaine)"
- "Éviter MACD BEARISH pour les indices (taux 30%)"

Sois analytique, data-driven et autocritique.

FORMAT DE RÉPONSE EN JSON:
{
  "resume_executif": "Paragraphe résumant la semaine...",
  "chiffres_cles": {
    "nb_trades": 0,
    "taux_reussite": 0,
    "pnl_total": 0,
    "meilleur_jour": "",
    "pire_jour": ""
  },
  "forces": ["Force 1", "Force 2"],
  "faiblesses": ["Faiblesse 1", "Faiblesse 2"],
  "patterns_identifies": [
    {"pattern": "Description", "recommandation": "Action à prendre"}
  ],
  "analyse_indicateurs": {
    "rsi_optimal": "RSI qui a le mieux performé + recommandation",
    "macd_optimal": "Signal MACD le plus rentable + recommandation",
    "atr_optimal": "Niveau ATR le plus rentable + recommandation",
    "ratio_rr_optimal": "Ratio R/R le plus performant"
  },
  "ajustements_recommandes": [
    {"critere": "Nom du critère", "action": "Augmenter/Réduire/Modifier", "raison": "Justification basée sur données"}
  ],
  "scores_confiance": {
    "indice": {"score": 0, "tendance": "hausse/baisse/stable"},
    "action_eu": {"score": 0, "tendance": ""},
    "action_us": {"score": 0, "tendance": ""},
    "commodite": {"score": 0, "tendance": ""},
    "forex": {"score": 0, "tendance": ""},
    "news_trading": {"score": 0, "tendance": ""},
    "technique": {"score": 0, "tendance": ""}
  },
  "focus_semaine_prochaine": ["Point 1", "Point 2"]
}"""

def generer_commentaires_journal(donnees_actifs):
    """Génère des commentaires AI pour les actifs du journal (style carnet de bord)"""
    if not donnees_actifs:
        return {}, []

    donnees_texte = json.dumps(donnees_actifs, ensure_ascii=False, indent=2)
    maintenant = get_paris_time()

    question = f"""Voici les données des actifs pour le {maintenant.strftime('%d/%m/%Y')}:

{donnees_texte}

Rédige ton carnet de bord de spéculateur:
1. Commente UNIQUEMENT les actifs avec des mouvements notables
2. Liste les 2-4 faits marquants de la journée (records, événements macro, etc.)

Sois direct et factuel, comme un trader pro."""

    try:
        message = client_anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=3000,
            system=SYSTEM_PROMPT_JOURNAL,
            messages=[{"role": "user", "content": question}]
        )

        reponse = message.content[0].text
        result, erreur = extraire_json_claude(reponse)
        if erreur:
            logger.warning(f"Extraction JSON journal échouée: {erreur}")
            return {}, []
        return result.get('commentaires', {}), result.get('faits_marquants', [])
    except Exception as e:
        logger.error(f"Erreur commentaires journal: {e}")
        return {}, []

def recuperer_recommandations_analystes(symbole):
    """Récupère les recommandations des analystes
    Note: Twelve Data ne fournit pas les recommandations analystes,
    cette fonctionnalité nécessiterait une autre source de données."""
    # Twelve Data ne propose pas cette fonctionnalité
    # Retourne une liste vide pour l'instant
    return []

def generer_bilan_quotidien():
    """Génère le bilan général de la journée"""
    maintenant = get_paris_time()
    aujourdhui = maintenant.date()

    print(f"[{maintenant.strftime('%H:%M:%S')} CET] 📊 Génération bilan quotidien...")

    try:
        # Récupérer les données de la journée
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Récupérer les trades du jour
        cursor.execute('''
            SELECT * FROM trades_recommandes WHERE date = ?
        ''', (aujourdhui,))
        trades_jour = [dict(row) for row in cursor.fetchall()]

        # Récupérer les journaux du jour
        cursor.execute('''
            SELECT * FROM journal_quotidien WHERE date = ?
        ''', (aujourdhui,))
        journaux_jour = [dict(row) for row in cursor.fetchall()]

        conn.close()

        # Préparer les données pour le prompt
        donnees_bilan = {
            'date': str(aujourdhui),
            'nb_recommandations': len(trades_jour),
            'trades': [{
                'actif': t['actif'],
                'direction': t.get('direction', 'LONG'),
                'type': t.get('type_setup', 'TECH'),
                'resultat': t.get('resultat', 'En cours'),
                'pnl': t.get('pnl_pct')
            } for t in trades_jour],
            'resume_actifs': [{
                'nom': j['nom_actif'],
                'variation': j['variation_jour']
            } for j in journaux_jour[:10]]
        }

        donnees_texte = json.dumps(donnees_bilan, ensure_ascii=False, indent=2)

        question = f"""Voici les données de trading pour le {maintenant.strftime('%d/%m/%Y')}:

{donnees_texte}

Fais un bilan honnête de cette journée de trading. Qu'est-ce qui a fonctionné ? Qu'est-ce qui n'a pas fonctionné ? Quelles leçons en tirer ?"""

        message = client_anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            system=SYSTEM_PROMPT_BILAN,
            messages=[{"role": "user", "content": question}]
        )

        reponse = message.content[0].text
        bilan, erreur = extraire_json_claude(reponse)
        if erreur:
            logger.warning(f"Extraction JSON bilan échouée: {erreur}")
            return None
        if bilan:
            # Sauvegarder le bilan
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO bilan_quotidien
                (date, contenu, points_positifs, points_negatifs, lecons_apprises, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                aujourdhui,
                bilan.get('resume', ''),
                json.dumps(bilan.get('points_positifs', []), ensure_ascii=False),
                json.dumps(bilan.get('points_negatifs', []), ensure_ascii=False),
                json.dumps(bilan.get('lecons_apprises', []), ensure_ascii=False),
                maintenant
            ))
            conn.commit()
            conn.close()

            print(f"[{maintenant.strftime('%H:%M:%S')} CET] ✅ Bilan quotidien enregistré")
            return bilan
        return None
    except Exception as e:
        logger.error(f"Erreur bilan quotidien: {e}")
        return None

def enregistrer_journal_quotidien(actifs_a_traiter=None):
    """Enregistre le journal quotidien pour les actifs spécifiés ou tous"""
    maintenant = get_paris_time()
    aujourdhui = maintenant.date()

    # Si pas d'actifs spécifiés, traiter tous les actifs permanents
    if actifs_a_traiter is None:
        actifs_a_traiter = ACTIFS_PERMANENTS

    print(f"[{maintenant.strftime('%H:%M:%S')} CET] 📝 Enregistrement journal quotidien ({len(actifs_a_traiter)} actifs)...")

    try:
        # Récupérer les données des actifs
        donnees = recuperer_donnees_marche(actifs_a_traiter)

        # Récupérer les opportunités du jour pour chaque actif
        opportunites_jour = {}
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('''
                SELECT actif, symbole, type_setup, prix_entree, prix_tp1, resultat, direction
                FROM trades_recommandes WHERE date = ?
            ''', (aujourdhui,))
            for row in cursor.fetchall():
                symbole = row['symbole']
                if symbole not in opportunites_jour:
                    opportunites_jour[symbole] = []
                opportunites_jour[symbole].append({
                    'type': row['type_setup'],
                    'entree': row['prix_entree'],
                    'tp1': row['prix_tp1'],
                    'resultat': row['resultat'],
                    'direction': row['direction']
                })
            conn.close()
        except Exception as e:
            print(f"⚠️ Erreur récup opportunités: {e}")

        # Préparer les données pour les commentaires AI
        donnees_pour_ia = {}
        for nom, data in donnees.items():
            donnees_pour_ia[data['symbole']] = {
                'nom': nom,
                'prix': data['prix'],
                'haut': data.get('haut', 0),
                'bas': data.get('bas', 0),
                'variation': data['variation'],
                'variation_5j': data.get('variation_5j', 0)
            }

        # Générer les commentaires AI
        commentaires, faits_marquants = generer_commentaires_journal(donnees_pour_ia)

        # Sauvegarder les faits marquants dans la table bilan
        if faits_marquants:
            try:
                conn_bilan = sqlite3.connect(DB_PATH)
                cursor_bilan = conn_bilan.cursor()
                cursor_bilan.execute('''
                    INSERT OR REPLACE INTO bilan_quotidien
                    (date, faits_marquants, timestamp)
                    VALUES (?, ?, ?)
                    ON CONFLICT(date) DO UPDATE SET faits_marquants = excluded.faits_marquants
                ''', (
                    aujourdhui,
                    json.dumps(faits_marquants, ensure_ascii=False),
                    maintenant
                ))
                conn_bilan.commit()
                conn_bilan.close()
            except Exception as e:
                print(f"⚠️ Erreur sauvegarde faits marquants: {e}")

        # Enregistrer dans la base
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        for nom, data in donnees.items():
            symbole = data['symbole']
            categorie = get_categorie_actif(symbole)
            opps = opportunites_jour.get(symbole, [])

            # Récupérer les recommandations analystes (seulement pour les actions)
            recos = []
            if categorie in ['action_eu', 'action_us']:
                recos = recuperer_recommandations_analystes(symbole)

            # Récupérer volume_relatif depuis les données (si disponible)
            volume_rel = data.get('volume_relatif', data.get('volume_relatif_pct', 0))
            if not volume_rel and 'indicateurs' in data:
                volume_rel = data['indicateurs'].get('volume_relatif_pct', 0)

            cursor.execute('''
                INSERT OR REPLACE INTO journal_quotidien
                (date, symbole, nom_actif, categorie, prix_ouverture, prix_cloture,
                 prix_max, prix_min, variation_jour, volume_relatif, commentaire_ia,
                 evenements_jour, opportunites_jour, recommandations_analystes, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                aujourdhui,
                symbole,
                nom,
                categorie,
                data.get('ouverture', 0),
                data['prix'],
                data.get('haut', 0),
                data.get('bas', 0),
                data['variation'],
                volume_rel or 0,  # Volume relatif (% vs moyenne 20j)
                commentaires.get(symbole, ''),
                '',  # evenements_jour - TODO: intégrer calendrier économique
                json.dumps(opps, ensure_ascii=False) if opps else '',
                json.dumps(recos, ensure_ascii=False) if recos else '',
                maintenant
            ))

        conn.commit()
        conn.close()
        print(f"[{maintenant.strftime('%H:%M:%S')} CET] ✅ Journal quotidien enregistré")
        return True
    except Exception as e:
        logger.error(f"Erreur journal quotidien: {e}")
        return False

def enregistrer_journal_fr():
    """Enregistre le journal pour les actions françaises (18h00)"""
    actifs_fr = {k: v for k, v in ACTIFS_PERMANENTS.items()
                 if k.endswith('.PA') or k.startswith('^FCHI') or k == '^GDAXI'}
    return enregistrer_journal_quotidien(actifs_fr)

def generer_rapport_hebdo():
    """Génère le rapport hebdomadaire avec analyse et ajustements

    CORRIGÉ: Période basée sur semaine ISO calendaire (lundi-dimanche)
    CORRIGÉ: Exclut les trades ouverts (sans résultat) des statistiques
    """
    maintenant = get_paris_time()
    print(f"[{maintenant.strftime('%H:%M:%S')} CET] 📊 Génération rapport hebdomadaire...")

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # CORRIGÉ: Calculer la semaine ISO précédente (lundi-dimanche)
        # Si on est samedi, on prend la semaine qui vient de se terminer
        jour_actuel = maintenant.date()
        # Trouver le lundi de la semaine précédente
        jours_depuis_lundi = jour_actuel.weekday()  # 0=lundi, 6=dimanche
        lundi_semaine_courante = jour_actuel - timedelta(days=jours_depuis_lundi)
        # Semaine précédente = lundi - 7 jours
        date_debut = lundi_semaine_courante - timedelta(days=7)
        date_fin = date_debut + timedelta(days=6)  # Dimanche de cette semaine
        semaine = f"{date_debut.year}-W{date_debut.isocalendar()[1]:02d}"

        # Récupérer les trades de la semaine (tous, pour contexte)
        cursor.execute('''
            SELECT * FROM trades_recommandes
            WHERE date >= ? AND date <= ?
            ORDER BY timestamp_reco
        ''', (date_debut, date_fin))
        trades_semaine = [dict(row) for row in cursor.fetchall()]

        # CORRIGÉ: Stats détaillées - EXCLURE trades ouverts (resultat IS NOT NULL)
        cursor.execute('''
            SELECT
                COUNT(*) as nb_trades,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as stops,
                SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl_total,
                AVG(CASE WHEN duree_minutes IS NOT NULL THEN duree_minutes END) as duree_moyenne
            FROM trades_recommandes
            WHERE date >= ? AND date <= ?
            AND resultat IS NOT NULL
        ''', (date_debut, date_fin))
        stats_globales = dict(cursor.fetchone())

        # Compter aussi les trades encore ouverts pour info
        cursor.execute('''
            SELECT COUNT(*) as nb_ouverts
            FROM trades_recommandes
            WHERE date >= ? AND date <= ? AND resultat IS NULL
        ''', (date_debut, date_fin))
        nb_ouverts = cursor.fetchone()['nb_ouverts'] or 0
        stats_globales['nb_ouverts'] = nb_ouverts

        # CORRIGÉ: Stats par jour - EXCLURE trades ouverts
        cursor.execute('''
            SELECT date,
                   COUNT(*) as nb,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
            FROM trades_recommandes
            WHERE date >= ? AND date <= ?
            AND resultat IS NOT NULL
            GROUP BY date
            ORDER BY pnl DESC
        ''', (date_debut, date_fin))
        stats_par_jour = [dict(row) for row in cursor.fetchall()]

        # Stats par catégorie (CORRIGÉ: exclure trades ouverts)
        cursor.execute('''
            SELECT categorie_actif,
                   COUNT(*) as nb,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
            FROM trades_recommandes
            WHERE date >= ? AND date <= ? AND categorie_actif IS NOT NULL
            AND resultat IS NOT NULL
            GROUP BY categorie_actif
        ''', (date_debut, date_fin))
        stats_par_categorie = [dict(row) for row in cursor.fetchall()]

        # Stats par type (CORRIGÉ: exclure trades ouverts)
        cursor.execute('''
            SELECT type_setup,
                   COUNT(*) as nb,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
            FROM trades_recommandes
            WHERE date >= ? AND date <= ? AND type_setup IS NOT NULL
            AND resultat IS NOT NULL
            GROUP BY type_setup
        ''', (date_debut, date_fin))
        stats_par_type = [dict(row) for row in cursor.fetchall()]

        # Stats par heure (CORRIGÉ: exclure trades ouverts)
        cursor.execute('''
            SELECT heure_entree,
                   COUNT(*) as nb,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins
            FROM trades_recommandes
            WHERE date >= ? AND date <= ? AND heure_entree IS NOT NULL
            AND resultat IS NOT NULL
            GROUP BY heure_entree
            ORDER BY heure_entree
        ''', (date_debut, date_fin))
        stats_par_heure = [dict(row) for row in cursor.fetchall()]

        # Stats par signal RSI (CORRIGÉ: exclure trades ouverts)
        cursor.execute('''
            SELECT rsi_signal_reco,
                   COUNT(*) as nb,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
            FROM trades_recommandes
            WHERE date >= ? AND date <= ? AND rsi_signal_reco IS NOT NULL
            AND resultat IS NOT NULL
            GROUP BY rsi_signal_reco
        ''', (date_debut, date_fin))
        stats_par_rsi = [dict(row) for row in cursor.fetchall()]

        # Stats par signal MACD (CORRIGÉ: exclure trades ouverts)
        cursor.execute('''
            SELECT macd_signal_reco,
                   COUNT(*) as nb,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
            FROM trades_recommandes
            WHERE date >= ? AND date <= ? AND macd_signal_reco IS NOT NULL
            AND resultat IS NOT NULL
            GROUP BY macd_signal_reco
        ''', (date_debut, date_fin))
        stats_par_macd = [dict(row) for row in cursor.fetchall()]

        # Stats par ratio R/R (CORRIGÉ: exclure trades ouverts)
        cursor.execute('''
            SELECT ratio_rr,
                   COUNT(*) as nb,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
            FROM trades_recommandes
            WHERE date >= ? AND date <= ? AND ratio_rr IS NOT NULL AND ratio_rr != ''
            AND resultat IS NOT NULL
            GROUP BY ratio_rr
        ''', (date_debut, date_fin))
        stats_par_ratio_rr = [dict(row) for row in cursor.fetchall()]

        # Stats par niveau ATR (CORRIGÉ: exclure trades ouverts)
        cursor.execute('''
            SELECT
                CASE
                    WHEN atr_pct_reco < 1.5 THEN 'ATR_FAIBLE (<1.5%)'
                    WHEN atr_pct_reco < 2.5 THEN 'ATR_MOYEN (1.5-2.5%)'
                    ELSE 'ATR_ELEVE (>2.5%)'
                END as niveau_atr,
                COUNT(*) as nb,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
            FROM trades_recommandes
            WHERE date >= ? AND date <= ? AND atr_pct_reco IS NOT NULL
            AND resultat IS NOT NULL
            GROUP BY niveau_atr
        ''', (date_debut, date_fin))
        stats_par_atr = [dict(row) for row in cursor.fetchall()]

        # === STATS A/B TESTING AVANCÉ (CORRIGÉ: exclure trades ouverts) ===

        # Stats par jour de la semaine (saisonnalité)
        cursor.execute('''
            SELECT jour_semaine,
                COUNT(*) as nb,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
            FROM trades_recommandes
            WHERE date >= ? AND date <= ? AND jour_semaine IS NOT NULL
            AND resultat IS NOT NULL
            GROUP BY jour_semaine
            ORDER BY CASE jour_semaine
                WHEN 'LUNDI' THEN 1
                WHEN 'MARDI' THEN 2
                WHEN 'MERCREDI' THEN 3
                WHEN 'JEUDI' THEN 4
                WHEN 'VENDREDI' THEN 5
                ELSE 6 END
        ''', (date_debut, date_fin))
        stats_par_jour_semaine = [dict(row) for row in cursor.fetchall()]

        # Stats par session de marché
        cursor.execute('''
            SELECT session_marche,
                COUNT(*) as nb,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
            FROM trades_recommandes
            WHERE date >= ? AND date <= ? AND session_marche IS NOT NULL
            AND resultat IS NOT NULL
            GROUP BY session_marche
        ''', (date_debut, date_fin))
        stats_par_session = [dict(row) for row in cursor.fetchall()]

        # Stats par score de conviction
        cursor.execute('''
            SELECT conviction_score,
                COUNT(*) as nb,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
            FROM trades_recommandes
            WHERE date >= ? AND date <= ? AND conviction_score IS NOT NULL
            AND resultat IS NOT NULL
            GROUP BY conviction_score
            ORDER BY conviction_score
        ''', (date_debut, date_fin))
        stats_par_conviction = [dict(row) for row in cursor.fetchall()]

        # Stats par stratégie d'entrée
        cursor.execute('''
            SELECT strategie_entree,
                COUNT(*) as nb,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
            FROM trades_recommandes
            WHERE date >= ? AND date <= ? AND strategie_entree IS NOT NULL
            AND resultat IS NOT NULL
            GROUP BY strategie_entree
        ''', (date_debut, date_fin))
        stats_par_strategie_entree = [dict(row) for row in cursor.fetchall()]

        # Stats par régime de marché (VIX)
        cursor.execute('''
            SELECT regime_marche,
                COUNT(*) as nb,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl,
                AVG(vix_niveau) as vix_moyen
            FROM trades_recommandes
            WHERE date >= ? AND date <= ? AND regime_marche IS NOT NULL
            AND resultat IS NOT NULL
            GROUP BY regime_marche
        ''', (date_debut, date_fin))
        stats_par_regime = [dict(row) for row in cursor.fetchall()]

        # Stats trailing stop vs stop fixe
        cursor.execute('''
            SELECT
                CASE WHEN trailing_stop = 1 THEN 'TRAILING' ELSE 'FIXE' END as type_stop,
                COUNT(*) as nb,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
            FROM trades_recommandes
            WHERE date >= ? AND date <= ?
            AND resultat IS NOT NULL
            GROUP BY type_stop
        ''', (date_debut, date_fin))
        stats_par_type_stop = [dict(row) for row in cursor.fetchall()]

        # Stats par alignement multi-timeframe
        cursor.execute('''
            SELECT alignement_tf,
                COUNT(*) as nb,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
            FROM trades_recommandes
            WHERE date >= ? AND date <= ? AND alignement_tf IS NOT NULL
            AND resultat IS NOT NULL
            GROUP BY alignement_tf
            ORDER BY alignement_tf
        ''', (date_debut, date_fin))
        stats_par_alignement = [dict(row) for row in cursor.fetchall()]

        # Stats par sentiment (CORRIGÉ: exclure trades ouverts)
        cursor.execute('''
            SELECT
                CASE
                    WHEN sentiment_score < -2 THEN 'TRES_BEARISH'
                    WHEN sentiment_score < 0 THEN 'BEARISH'
                    WHEN sentiment_score = 0 THEN 'NEUTRE'
                    WHEN sentiment_score <= 2 THEN 'BULLISH'
                    ELSE 'TRES_BULLISH'
                END as sentiment_bucket,
                COUNT(*) as nb,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
            FROM trades_recommandes
            WHERE date >= ? AND date <= ? AND sentiment_score IS NOT NULL
            AND resultat IS NOT NULL
            GROUP BY sentiment_bucket
        ''', (date_debut, date_fin))
        stats_par_sentiment = [dict(row) for row in cursor.fetchall()]

        # Stats A/B Testing (CORRIGÉ: exclure trades ouverts + inclure WIN_FORCE)
        stats_ab_tests = []
        cursor.execute("SELECT * FROM ab_tests WHERE statut = 'actif'")
        tests_actifs = [dict(row) for row in cursor.fetchall()]

        for test in tests_actifs:
            test_id = test['id']
            # Stats groupe A
            cursor.execute('''
                SELECT COUNT(*) as nb,
                       SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                       SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
                FROM trades_recommandes
                WHERE ab_test_id = ? AND ab_groupe = 'A' AND date >= ? AND date <= ?
                AND resultat IS NOT NULL
            ''', (test_id, date_debut, date_fin))
            stats_a = dict(cursor.fetchone())

            # Stats groupe B
            cursor.execute('''
                SELECT COUNT(*) as nb,
                       SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                       SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
                FROM trades_recommandes
                WHERE ab_test_id = ? AND ab_groupe = 'B' AND date >= ? AND date <= ?
                AND resultat IS NOT NULL
            ''', (test_id, date_debut, date_fin))
            stats_b = dict(cursor.fetchone())

            stats_ab_tests.append({
                'nom': test['nom'],
                'variante_a': test['variante_a'],
                'variante_b': test['variante_b'],
                'groupe_a': {
                    'trades': stats_a['nb'] or 0,
                    'wins': stats_a['wins'] or 0,
                    'pnl': round(stats_a['pnl'] or 0, 2)
                },
                'groupe_b': {
                    'trades': stats_b['nb'] or 0,
                    'wins': stats_b['wins'] or 0,
                    'pnl': round(stats_b['pnl'] or 0, 2)
                }
            })

        conn.close()

        # Préparer les données pour Claude (incluant les analyses d'indicateurs)
        donnees_rapport = {
            'periode': f"{date_debut} au {date_fin}",
            'stats_globales': stats_globales,
            'stats_par_jour': stats_par_jour,
            'stats_par_categorie': stats_par_categorie,
            'stats_par_type': stats_par_type,
            'stats_par_heure': stats_par_heure,
            # Stats indicateurs
            'stats_par_rsi': stats_par_rsi,
            'stats_par_macd': stats_par_macd,
            'stats_par_ratio_rr': stats_par_ratio_rr,
            'stats_par_atr': stats_par_atr,
            # === NOUVELLES STATS A/B TESTING AVANCÉ ===
            'stats_par_jour_semaine': stats_par_jour_semaine,  # Saisonnalité
            'stats_par_session': stats_par_session,            # Sessions marché
            'stats_par_conviction': stats_par_conviction,      # Score conviction
            'stats_par_strategie_entree': stats_par_strategie_entree,  # IMMEDIATE/PULLBACK/etc
            'stats_par_regime': stats_par_regime,              # VIX regime
            'stats_par_type_stop': stats_par_type_stop,        # Trailing vs Fixe
            'stats_par_alignement': stats_par_alignement,      # Multi-timeframe
            'stats_par_sentiment': stats_par_sentiment,        # Sentiment score
            # Tests A/B en cours
            'tests_ab': stats_ab_tests,
            'nb_trades_total': len(trades_semaine)
        }

        donnees_texte = json.dumps(donnees_rapport, ensure_ascii=False, indent=2, default=str)

        question = f"""Voici les données de trading de la semaine du {date_debut} au {date_fin}:

{donnees_texte}

Analyse ces performances et génère un rapport hebdomadaire complet.

ANALYSE EN PRIORITÉ:
1. INDICATEURS: RSI, MACD, ratio R/R, niveau ATR - lesquels performent le mieux?
2. SAISONNALITÉ: Quel jour de la semaine a le meilleur win rate? (stats_par_jour_semaine)
3. SESSIONS: EU_OPEN, US_OPEN, etc. - quelle session est la plus rentable? (stats_par_session)
4. CONVICTION: Les trades haute conviction (4-5) performent-ils mieux? (stats_par_conviction)
5. STRATÉGIE ENTRÉE: IMMEDIATE vs PULLBACK vs BREAKOUT - laquelle gagne? (stats_par_strategie_entree)
6. RÉGIME VIX: Performance en marché CALME vs VOLATILE vs EXTREME (stats_par_regime)
7. TRAILING STOP: Les trailing stops améliorent-ils les résultats? (stats_par_type_stop)
8. MULTI-TF: L'alignement des timeframes prédit-il le succès? (stats_par_alignement)
9. SENTIMENT: Le sentiment score prédit-il correctement la direction? (stats_par_sentiment)
10. TESTS A/B: Compare les performances des groupes A vs B

RECOMMANDATIONS ATTENDUES:
- Quels jours/sessions privilégier ou éviter?
- Quel score de conviction minimum exiger?
- Quelle stratégie d'entrée favoriser?
- Dans quel régime de marché ajuster les règles?
- Le trailing stop doit-il être systématique?
- Quel alignement multi-TF minimum?

Propose des AJUSTEMENTS PRÉCIS et TESTABLES pour la semaine prochaine."""

        message = client_anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=3000,
            system=SYSTEM_PROMPT_RAPPORT_HEBDO,
            messages=[{"role": "user", "content": question}]
        )

        reponse = message.content[0].text
        rapport, erreur = extraire_json_claude(reponse)

        if erreur:
            logger.warning(f"Extraction JSON rapport hebdo échouée: {erreur}")
            return None

        if rapport:
            # Sauvegarder le rapport
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO rapports_hebdo
                (semaine, date_debut, date_fin, resume_executif, chiffres_cles,
                 forces, faiblesses, patterns, ajustements, scores_confiance, focus_semaine, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                semaine,
                date_debut,
                date_fin,
                rapport.get('resume_executif', ''),
                json.dumps(rapport.get('chiffres_cles', {}), ensure_ascii=False),
                json.dumps(rapport.get('forces', []), ensure_ascii=False),
                json.dumps(rapport.get('faiblesses', []), ensure_ascii=False),
                json.dumps(rapport.get('patterns_identifies', []), ensure_ascii=False),
                json.dumps(rapport.get('ajustements_recommandes', []), ensure_ascii=False),
                json.dumps(rapport.get('scores_confiance', {}), ensure_ascii=False),
                json.dumps(rapport.get('focus_semaine_prochaine', []), ensure_ascii=False),
                maintenant
            ))
            conn.commit()

            # Sauvegarder les ajustements comme propositions (attente validation)
            sauvegarder_ajustements_proposes(
                rapport.get('ajustements_recommandes', []),
                rapport.get('scores_confiance', {}),
                source='rapport_hebdo'
            )

            conn.close()
            print(f"[{maintenant.strftime('%H:%M:%S')} CET] ✅ Rapport hebdomadaire généré (ajustements en attente de validation)")
            return rapport

        return None
    except Exception as e:
        logger.error(f"Erreur rapport hebdo: {e}")
        return None

def appliquer_ajustements_dynamiques(ajustements, scores_confiance):
    """Applique les ajustements dynamiques basés sur le rapport hebdo"""
    maintenant = get_paris_time()

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Sauvegarder les scores de confiance comme critères
        for categorie, score_data in scores_confiance.items():
            if isinstance(score_data, dict):
                score = score_data.get('score', 50)
                tendance = score_data.get('tendance', 'stable')

                # Récupérer la valeur précédente
                cursor.execute('''
                    SELECT valeur_actuelle FROM criteres_dynamiques
                    WHERE categorie = ? AND critere = 'score_confiance'
                    ORDER BY date_maj DESC LIMIT 1
                ''', (categorie,))
                row = cursor.fetchone()
                valeur_precedente = row[0] if row else 50

                cursor.execute('''
                    INSERT INTO criteres_dynamiques
                    (date_maj, categorie, critere, valeur_actuelle, valeur_precedente, raison, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    maintenant.date(),
                    categorie,
                    'score_confiance',
                    score,
                    valeur_precedente,
                    f"Tendance: {tendance}",
                    maintenant
                ))

        # Sauvegarder les ajustements recommandés
        for ajust in ajustements:
            critere = ajust.get('critere', '')
            action = ajust.get('action', '')
            raison = ajust.get('raison', '')

            cursor.execute('''
                INSERT INTO criteres_dynamiques
                (date_maj, categorie, critere, valeur_actuelle, valeur_precedente, raison, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                maintenant.date(),
                'ajustement',
                critere,
                1 if 'augmenter' in action.lower() else -1 if 'reduire' in action.lower() else 0,
                0,
                f"{action}: {raison}",
                maintenant
            ))

        conn.commit()
        conn.close()
        print(f"[{maintenant.strftime('%H:%M:%S')} CET] ✅ Critères dynamiques mis à jour")

    except Exception as e:
        print(f"⚠️ Erreur ajustements dynamiques: {e}")

def get_criteres_dynamiques():
    """Récupère les critères dynamiques actuels pour le SYSTEM_PROMPT"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Récupérer les derniers scores de confiance
        cursor.execute('''
            SELECT categorie, valeur_actuelle as score, raison as tendance
            FROM criteres_dynamiques
            WHERE critere = 'score_confiance'
            AND date_maj = (SELECT MAX(date_maj) FROM criteres_dynamiques WHERE critere = 'score_confiance')
        ''')
        scores = {row['categorie']: {'score': row['score'], 'tendance': row['tendance']}
                  for row in cursor.fetchall()}

        # Récupérer les derniers ajustements
        cursor.execute('''
            SELECT critere, raison
            FROM criteres_dynamiques
            WHERE categorie = 'ajustement'
            AND date_maj >= date('now', '-30 days')
            ORDER BY date_maj DESC
        ''')
        ajustements = [{'critere': row['critere'], 'raison': row['raison']}
                      for row in cursor.fetchall()]

        conn.close()

        return {
            'scores_confiance': scores,
            'ajustements_recents': ajustements
        }
    except Exception as e:
        print(f"⚠️ Erreur critères dynamiques: {e}")
        return {'scores_confiance': {}, 'ajustements_recents': []}

def normaliser_categorie(categorie):
    """
    Normalise les noms de catégories pour assurer la cohérence.
    Mappe les variations vers les noms standardisés utilisés dans categorie_actif.
    """
    if not categorie:
        return categorie

    categorie_lower = categorie.lower().strip()

    # Mapping des variations vers les noms standardisés
    mapping = {
        # Indices
        'indices': 'indice',
        'index': 'indice',
        # Actions EU
        'actions_eu': 'action_eu',
        'actions européennes': 'action_eu',
        'actions eu': 'action_eu',
        # Actions US
        'actions_us': 'action_us',
        'actions américaines': 'action_us',
        'actions us': 'action_us',
        # Commodités
        'commodites': 'commodite',
        'commodités': 'commodite',
        'matières premières': 'commodite',
    }

    return mapping.get(categorie_lower, categorie_lower)

def extraire_categorie_ajustement(critere, raison, action):
    """
    Extrait une catégorie spécifique d'un ajustement stratégique.
    CORRIGÉ: Évite la catégorie générique 'trading' pour permettre un feedback précis.
    """
    texte = f"{critere} {raison} {action}".lower()

    # Catégories d'actifs
    if any(mot in texte for mot in ['indice', 'indices', 'cac', 'dax', 'sp500', 's&p', 'nasdaq', 'dow']):
        return 'indice'
    if any(mot in texte for mot in ['action_eu', 'actions européennes', 'actions eu', 'lvmh', 'total', 'airbus']):
        return 'action_eu'
    if any(mot in texte for mot in ['action_us', 'actions américaines', 'actions us', 'apple', 'tesla', 'nvidia', 'microsoft']):
        return 'action_us'
    if any(mot in texte for mot in ['commodit', 'or', 'gold', 'pétrole', 'oil', 'silver', 'argent']):
        return 'commodite'
    if any(mot in texte for mot in ['forex', 'eur/usd', 'gbp', 'devise', 'currency']):
        return 'forex'

    # Catégories temporelles/contextuelles
    if any(mot in texte for mot in ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi']):
        return 'timing_jour'
    if any(mot in texte for mot in ['matin', 'après-midi', 'soir', 'ouverture', 'clôture', 'session']):
        return 'timing_session'
    if any(mot in texte for mot in ['news', 'annonce', 'économique', 'fomc', 'nfp', 'inflation']):
        return 'news_trading'

    # Catégories techniques
    if any(mot in texte for mot in ['rsi', 'macd', 'indicateur']):
        return 'indicateurs'
    if any(mot in texte for mot in ['trailing', 'stop', 'tp', 'take profit']):
        return 'gestion_position'
    if any(mot in texte for mot in ['ratio', 'r/r', 'risk', 'reward']):
        return 'risk_reward'
    if any(mot in texte for mot in ['conviction', 'confiance', 'score']):
        return 'conviction'

    # Catégorie par défaut plus spécifique que "trading"
    return 'strategie_generale'

def sauvegarder_ajustements_proposes(ajustements, scores_confiance, source='rapport_hebdo'):
    """Sauvegarde les ajustements proposés en attente de validation

    CORRIGÉ: Catégorie extraite du contenu au lieu de 'trading' générique
    CORRIGÉ: Auto-validation retardée (pas immédiate après création)
    """
    maintenant = get_paris_time()

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Sauvegarder les scores de confiance comme propositions
        for categorie, score_data in scores_confiance.items():
            if isinstance(score_data, dict):
                score = score_data.get('score', 50)
                tendance = score_data.get('tendance', 'stable')

                # CORRIGÉ: Normaliser la catégorie pour cohérence avec categorie_actif
                categorie_normalisee = normaliser_categorie(categorie)

                cursor.execute('''
                    INSERT INTO ajustements_proposes
                    (type_ajustement, categorie, critere, action, valeur_proposee, raison, source, statut, date_proposition)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    'score_confiance',
                    categorie_normalisee,
                    'score_confiance',
                    f"Définir score à {score}/100",
                    str(score),
                    f"Tendance: {tendance}",
                    source,
                    'en_attente',
                    maintenant
                ))

        # Sauvegarder les ajustements stratégiques
        for ajust in ajustements:
            critere = ajust.get('critere', '')
            action = ajust.get('action', '')
            raison = ajust.get('raison', '')

            # CORRIGÉ: Extraire une catégorie spécifique du contenu
            categorie = extraire_categorie_ajustement(critere, raison, action)

            cursor.execute('''
                INSERT INTO ajustements_proposes
                (type_ajustement, categorie, critere, action, valeur_proposee, raison, source, statut, date_proposition)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                'strategie',
                categorie,  # CORRIGÉ: Catégorie spécifique au lieu de 'trading'
                critere,
                action,
                '',
                raison,
                source,
                'en_attente',
                maintenant
            ))

        conn.commit()
        conn.close()
        print(f"[{maintenant.strftime('%H:%M:%S')} CET] 📋 {len(ajustements) + len(scores_confiance)} ajustements proposés en attente de validation")

        # CORRIGÉ: Auto-validation RETARDÉE - ne pas appeler immédiatement
        # Le traitement automatique sera fait par le scheduler après un délai
        # pour laisser à l'utilisateur le temps de review manuellement
        # traiter_ajustements_automatiquement()  # Désactivé - sera appelé par scheduler

    except Exception as e:
        print(f"⚠️ Erreur sauvegarde ajustements: {e}")

def get_ajustements_en_attente():
    """Récupère les ajustements en attente de validation"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM ajustements_proposes
            WHERE statut = 'en_attente'
            ORDER BY date_proposition DESC
        ''')

        ajustements = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return ajustements
    except Exception as e:
        print(f"⚠️ Erreur récupération ajustements: {e}")
        return []

def valider_ajustement(id_ajustement, decision, decideur='utilisateur', commentaire=None):
    """Valide ou rejette un ajustement proposé avec audit trail et commentaire optionnel"""
    maintenant = get_paris_time()

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row  # Pour accéder aux colonnes par nom
        cursor = conn.cursor()

        # Récupérer l'ajustement
        cursor.execute('SELECT * FROM ajustements_proposes WHERE id = ?', (id_ajustement,))
        row = cursor.fetchone()

        if not row:
            conn.close()
            return False, "Ajustement non trouvé"

        ajust_dict = dict(row)

        # Mettre à jour le statut avec commentaire optionnel
        nouveau_statut = 'valide' if decision else 'rejete'
        cursor.execute('''
            UPDATE ajustements_proposes
            SET statut = ?, date_decision = ?, decideur = ?, commentaire_utilisateur = ?
            WHERE id = ?
        ''', (nouveau_statut, maintenant, decideur, commentaire, id_ajustement))

        # Si validé, appliquer l'ajustement avec audit trail
        if decision:
            if ajust_dict.get('type_ajustement') == 'score_confiance':
                # Appliquer le score de confiance
                cursor.execute('''
                    INSERT INTO criteres_dynamiques
                    (date_maj, categorie, critere, valeur_actuelle, valeur_precedente, raison, timestamp, ajustement_source_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    maintenant.date(),
                    ajust_dict.get('categorie'),
                    'score_confiance',
                    float(ajust_dict.get('valeur_proposee', 50)),
                    50,
                    f"Validé par {decideur}: {ajust_dict.get('raison', '')}",
                    maintenant,
                    id_ajustement  # Audit trail: lien vers l'ajustement source
                ))
            else:
                # Appliquer l'ajustement stratégique
                cursor.execute('''
                    INSERT INTO criteres_dynamiques
                    (date_maj, categorie, critere, valeur_actuelle, valeur_precedente, raison, timestamp, ajustement_source_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    maintenant.date(),
                    'ajustement_valide',
                    ajust_dict.get('critere', ''),
                    1,
                    0,
                    f"Validé par {decideur}: {ajust_dict.get('action', '')} - {ajust_dict.get('raison', '')}",
                    maintenant,
                    id_ajustement  # Audit trail: lien vers l'ajustement source
                ))

            print(f"✅ Ajustement #{id_ajustement} validé par {decideur} et appliqué")

        conn.commit()
        conn.close()

        return True, f"Ajustement {'validé et appliqué' if decision else 'rejeté'}"

    except Exception as e:
        print(f"⚠️ Erreur validation ajustement: {e}")
        return False, str(e)

def valider_tous_ajustements(decision, decideur='utilisateur'):
    """Valide ou rejette tous les ajustements en attente"""
    ajustements = get_ajustements_en_attente()
    resultats = []

    for ajust in ajustements:
        succes, message = valider_ajustement(ajust['id'], decision, decideur)
        resultats.append({'id': ajust['id'], 'succes': succes, 'message': message})

    return resultats

def expirer_ajustements_anciens(jours_max=21):
    """DÉSACTIVÉ - Les ajustements sont conservés pour historique dans les rapports hebdo.
    Cette fonction n'est plus appelée par le scheduler."""
    return 0  # Ne rien faire - garder tout l'historique

def _expirer_ajustements_anciens_legacy(jours_max=21):
    """[LEGACY] Expire automatiquement les ajustements non traités après X jours"""
    maintenant = get_paris_time()
    date_limite = maintenant - timedelta(days=jours_max)

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Marquer comme expirés les ajustements trop anciens
        cursor.execute('''
            UPDATE ajustements_proposes
            SET statut = 'expire',
                date_decision = ?,
                decideur = 'systeme_auto'
            WHERE statut = 'en_attente'
            AND date_proposition < ?
        ''', (maintenant, date_limite))

        nb_expires = cursor.rowcount
        conn.commit()
        conn.close()

        if nb_expires > 0:
            print(f"[{maintenant.strftime('%H:%M:%S')} CET] 🕐 {nb_expires} ajustements expirés (> {jours_max} jours)")

        return nb_expires

    except Exception as e:
        print(f"⚠️ Erreur expiration ajustements: {e}")
        return 0

def get_historique_ajustements(limite=50):
    """Récupère l'historique complet des ajustements (validés, rejetés, expirés)"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM ajustements_proposes
            WHERE statut != 'en_attente'
            ORDER BY date_decision DESC
            LIMIT ?
        ''', (limite,))

        historique = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return historique
    except Exception as e:
        print(f"⚠️ Erreur historique ajustements: {e}")
        return []

# ============================================================================
# FEEDBACK LOOP - ÉVALUATION DES AJUSTEMENTS
# ============================================================================

def construire_filtre_feedback(categorie, type_ajustement):
    """
    Construit le filtre SQL approprié selon la catégorie de l'ajustement.
    CORRIGÉ: Gère les catégories d'actifs ET les catégories contextuelles.
    """
    # Catégories qui correspondent directement à categorie_actif
    categories_actifs = ['indice', 'action_eu', 'action_us', 'commodite', 'forex']

    if categorie in categories_actifs:
        return "categorie_actif = ?", [categorie]

    # Catégories temporelles - filtrer par jour de semaine
    if categorie == 'timing_jour':
        return "1=1", []  # Pas de filtre spécifique, compare globalement

    # Catégories liées aux sessions
    if categorie == 'timing_session':
        return "session_marche IS NOT NULL", []

    # Catégories liées aux news
    if categorie == 'news_trading':
        return "type_setup = 'NEWS'", []

    # Catégories techniques (indicateurs, gestion position, etc.)
    if categorie in ['indicateurs', 'gestion_position', 'risk_reward', 'conviction']:
        return "1=1", []  # Compare globalement

    # Score confiance - filtre par catégorie d'actif si c'est une catégorie valide
    if type_ajustement == 'score_confiance' and categorie in categories_actifs:
        return "categorie_actif = ?", [categorie]

    # Défaut: pas de filtre spécifique
    return "1=1", []

def calculer_feedback_ajustement(id_ajustement, jours_evaluation=7):
    """
    Calcule l'impact d'un ajustement en comparant les performances
    avant et après son application.

    CORRIGÉ: Gère les différentes catégories d'ajustements (pas seulement categorie_actif)
    CORRIGÉ: Exclut les trades ouverts (resultat IS NOT NULL)
    """
    maintenant = get_paris_time()

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Récupérer l'ajustement
        cursor.execute('SELECT * FROM ajustements_proposes WHERE id = ?', (id_ajustement,))
        row = cursor.fetchone()

        if not row or row['statut'] != 'valide':
            conn.close()
            return None, "Ajustement non trouvé ou non validé"

        ajust = dict(row)
        date_application = ajust.get('date_decision')

        if not date_application:
            conn.close()
            return None, "Date d'application non disponible"

        # Convertir la date si nécessaire
        if isinstance(date_application, str):
            date_application = datetime.fromisoformat(date_application.replace('Z', '+00:00'))

        date_application = date_application.date() if hasattr(date_application, 'date') else date_application

        # Période AVANT l'ajustement (7 jours avant)
        date_debut_avant = date_application - timedelta(days=jours_evaluation)

        # Période APRÈS l'ajustement (7 jours après ou jusqu'à maintenant)
        date_fin_apres = min(date_application + timedelta(days=jours_evaluation), maintenant.date())

        # CORRIGÉ: Construire le filtre approprié selon la catégorie
        categorie = ajust.get('categorie', '')
        type_ajust = ajust.get('type_ajustement', 'strategie')
        filtre_sql, filtre_params = construire_filtre_feedback(categorie, type_ajust)

        # Stats AVANT - CORRIGÉ: Exclut trades ouverts (resultat IS NOT NULL)
        query_avant = f'''
            SELECT
                COUNT(*) as nb_trades,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as stops,
                SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl_total
            FROM trades_recommandes
            WHERE date >= ? AND date < ?
            AND resultat IS NOT NULL
            AND {filtre_sql}
        '''
        cursor.execute(query_avant, (date_debut_avant, date_application, *filtre_params))
        avant = cursor.fetchone()

        # Stats APRÈS - CORRIGÉ: Exclut trades ouverts (resultat IS NOT NULL)
        query_apres = f'''
            SELECT
                COUNT(*) as nb_trades,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as stops,
                SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl_total
            FROM trades_recommandes
            WHERE date >= ? AND date <= ?
            AND resultat IS NOT NULL
            AND {filtre_sql}
        '''
        cursor.execute(query_apres, (date_application, date_fin_apres, *filtre_params))
        apres = cursor.fetchone()

        # Calculer les taux de réussite
        avant_conclus = (avant['reussis'] or 0) + (avant['stops'] or 0)
        apres_conclus = (apres['reussis'] or 0) + (apres['stops'] or 0)

        taux_avant = round((avant['reussis'] / avant_conclus * 100) if avant_conclus > 0 else 0, 1)
        taux_apres = round((apres['reussis'] / apres_conclus * 100) if apres_conclus > 0 else 0, 1)

        pnl_avant = round(avant['pnl_total'] or 0, 2)
        pnl_apres = round(apres['pnl_total'] or 0, 2)

        # Déterminer la conclusion (seuil 5 trades pour significativité)
        if apres_conclus < 5:
            conclusion = "INSUFFISANT"
            conclusion_detail = f"Seulement {apres_conclus} trades après ajustement (min 5 requis)"
        elif avant_conclus < 5:
            conclusion = "INSUFFISANT"
            conclusion_detail = f"Seulement {avant_conclus} trades avant ajustement (min 5 requis pour comparaison)"
        elif taux_apres > taux_avant + 5:
            conclusion = "POSITIF"
            conclusion_detail = f"Taux +{taux_apres - taux_avant:.1f}%, PnL: {pnl_avant:.2f}% → {pnl_apres:.2f}%"
        elif taux_apres < taux_avant - 5:
            conclusion = "NEGATIF"
            conclusion_detail = f"Taux {taux_apres - taux_avant:.1f}%, PnL: {pnl_avant:.2f}% → {pnl_apres:.2f}%"
        else:
            conclusion = "NEUTRE"
            conclusion_detail = f"Peu de changement (±5%), PnL: {pnl_avant:.2f}% → {pnl_apres:.2f}%"

        # Sauvegarder le feedback
        cursor.execute('''
            UPDATE ajustements_proposes SET
                perf_avant_nb_trades = ?,
                perf_avant_taux_reussite = ?,
                perf_avant_pnl_total = ?,
                perf_apres_nb_trades = ?,
                perf_apres_taux_reussite = ?,
                perf_apres_pnl_total = ?,
                feedback_date = ?,
                feedback_conclusion = ?
            WHERE id = ?
        ''', (
            avant['nb_trades'], taux_avant, pnl_avant,
            apres['nb_trades'], taux_apres, pnl_apres,
            maintenant, f"{conclusion}: {conclusion_detail}",
            id_ajustement
        ))

        conn.commit()
        conn.close()

        return {
            'id': id_ajustement,
            'categorie': ajust.get('categorie'),
            'avant': {'nb_trades': avant['nb_trades'], 'taux': taux_avant, 'pnl': pnl_avant},
            'apres': {'nb_trades': apres['nb_trades'], 'taux': taux_apres, 'pnl': pnl_apres},
            'conclusion': conclusion,
            'detail': conclusion_detail
        }, None

    except Exception as e:
        print(f"⚠️ Erreur feedback ajustement: {e}")
        return None, str(e)

def evaluer_tous_ajustements_valides():
    """Évalue tous les ajustements validés qui n'ont pas encore de feedback"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Récupérer les ajustements validés sans feedback et > 7 jours
        date_limite = get_paris_time() - timedelta(days=7)
        cursor.execute('''
            SELECT id FROM ajustements_proposes
            WHERE statut = 'valide'
            AND feedback_conclusion IS NULL
            AND date_decision < ?
        ''', (date_limite,))

        ajustements = [row['id'] for row in cursor.fetchall()]
        conn.close()

        resultats = []
        for id_ajust in ajustements:
            result, error = calculer_feedback_ajustement(id_ajust)
            if result:
                resultats.append(result)
                print(f"📊 Feedback #{id_ajust}: {result['conclusion']}")

        return resultats

    except Exception as e:
        print(f"⚠️ Erreur évaluation ajustements: {e}")
        return []

def get_historique_feedback_categorie(categorie, type_ajustement='strategie'):
    """
    Analyse l'historique des feedbacks pour une catégorie donnée.
    Retourne: {'nb_positifs': int, 'nb_negatifs': int, 'nb_neutres': int, 'recommandation': str}

    CORRIGÉ: Utilise AND au lieu de OR pour filtrer correctement par catégorie ET type.
    CORRIGÉ: Seuils augmentés de 2 à 5 pour significativité statistique.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Récupérer les feedbacks des 30 derniers jours pour cette catégorie
        # CORRIGÉ: AND au lieu de OR - on veut les feedbacks de CETTE catégorie
        date_limite = get_paris_time() - timedelta(days=30)
        cursor.execute('''
            SELECT feedback_conclusion
            FROM ajustements_proposes
            WHERE categorie = ?
            AND type_ajustement = ?
            AND statut = 'valide'
            AND feedback_conclusion IS NOT NULL
            AND feedback_conclusion NOT LIKE '%INSUFFISANT%'
            AND date_decision > ?
        ''', (categorie, type_ajustement, date_limite))

        resultats = [row['feedback_conclusion'] for row in cursor.fetchall()]
        conn.close()

        nb_positifs = sum(1 for r in resultats if r and 'POSITIF' in r)
        nb_negatifs = sum(1 for r in resultats if r and 'NEGATIF' in r)
        nb_neutres = sum(1 for r in resultats if r and 'NEUTRE' in r)

        # CORRIGÉ: Seuils harmonisés - minimum 5 feedbacks pour significativité
        total_conclus = nb_positifs + nb_negatifs
        if total_conclus < 5:
            # Pas assez de données - toujours validation manuelle
            recommandation = 'MANUEL'
        elif nb_positifs >= 5 and nb_negatifs == 0:
            # Pattern positif fort (5+ sans aucun négatif)
            recommandation = 'AUTO_VALIDER'
        elif nb_negatifs >= 5 and nb_positifs == 0:
            # Circuit breaker (5+ négatifs sans aucun positif)
            recommandation = 'BLOQUER'
        elif nb_positifs >= 5 and nb_positifs > nb_negatifs * 3:
            # Ratio 3:1 en faveur des positifs
            recommandation = 'AUTO_VALIDER'
        elif nb_negatifs >= 5 and nb_negatifs > nb_positifs * 3:
            # Ratio 3:1 en faveur des négatifs
            recommandation = 'BLOQUER'
        else:
            # Résultats mitigés - validation manuelle
            recommandation = 'MANUEL'

        return {
            'nb_positifs': nb_positifs,
            'nb_negatifs': nb_negatifs,
            'nb_neutres': nb_neutres,
            'total_conclus': total_conclus,
            'recommandation': recommandation
        }

    except Exception as e:
        print(f"⚠️ Erreur historique feedback: {e}")
        return {'nb_positifs': 0, 'nb_negatifs': 0, 'nb_neutres': 0, 'total_conclus': 0, 'recommandation': 'MANUEL'}

def auto_valider_ajustement_si_positif(id_ajustement, categorie, type_ajustement):
    """
    Auto-valide un ajustement si l'historique est positif.
    Retourne: (bool auto_valide, str raison)
    """
    historique = get_historique_feedback_categorie(categorie, type_ajustement)

    if historique['recommandation'] == 'AUTO_VALIDER':
        # Auto-validation basée sur les performances passées
        succes, message = valider_ajustement(id_ajustement, decision=True, decideur='auto_feedback')
        if succes:
            print(f"✅ Auto-validation #{id_ajustement}: {historique['nb_positifs']} feedbacks positifs historiques")
            return True, f"Auto-validé (historique: {historique['nb_positifs']} positifs, {historique['nb_negatifs']} négatifs)"
        return False, message

    elif historique['recommandation'] == 'BLOQUER':
        # Circuit breaker - rejeter automatiquement
        succes, message = valider_ajustement(id_ajustement, decision=False, decideur='circuit_breaker')
        if succes:
            print(f"🛑 Circuit breaker #{id_ajustement}: {historique['nb_negatifs']} feedbacks négatifs historiques")
            return False, f"Bloqué (circuit breaker: {historique['nb_negatifs']} négatifs, {historique['nb_positifs']} positifs)"
        return False, message

    return False, "En attente validation manuelle"

def traiter_ajustements_automatiquement():
    """
    Traite automatiquement les ajustements en attente basé sur l'historique des feedbacks.
    Appelé régulièrement pour auto-valider ou bloquer selon le pattern.
    """
    maintenant = get_paris_time()
    ajustements = get_ajustements_en_attente()

    resultats = {'auto_valides': 0, 'bloques': 0, 'manuels': 0}

    for ajust in ajustements:
        categorie = ajust.get('categorie', '')
        type_ajust = ajust.get('type_ajustement', 'strategie')
        id_ajust = ajust.get('id')

        auto_valide, raison = auto_valider_ajustement_si_positif(id_ajust, categorie, type_ajust)

        if 'Auto-validé' in raison:
            resultats['auto_valides'] += 1
        elif 'Bloqué' in raison:
            resultats['bloques'] += 1
        else:
            resultats['manuels'] += 1

    if resultats['auto_valides'] > 0 or resultats['bloques'] > 0:
        print(f"[{maintenant.strftime('%H:%M:%S')} CET] 🤖 Traitement auto: {resultats['auto_valides']} validés, {resultats['bloques']} bloqués, {resultats['manuels']} manuels")

    return resultats

# ============================================================================
# A/B TESTING - EXPÉRIMENTATION STRATÉGIQUE
# ============================================================================

# Configuration A/B Testing actif
AB_TESTS_ACTIFS = {}

def init_ab_test_table():
    """Initialise la table pour les tests A/B"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ab_tests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nom TEXT NOT NULL,
                description TEXT,
                variante_a TEXT,
                variante_b TEXT,
                actifs_groupe_a TEXT,
                actifs_groupe_b TEXT,
                date_debut TIMESTAMP,
                date_fin TIMESTAMP,
                statut TEXT DEFAULT 'actif',
                resultats_a TEXT,
                resultats_b TEXT,
                gagnant TEXT,
                conclusion TEXT
            )
        ''')

        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ Erreur init table A/B tests: {e}")

def creer_ab_test(nom, description, variante_a, variante_b, actifs_test=None):
    """
    Crée un nouveau test A/B pour comparer deux stratégies.
    - nom: Nom du test (ex: "RSI_threshold_test")
    - variante_a: Description de la stratégie A (ex: "RSI seuil 30/70")
    - variante_b: Description de la stratégie B (ex: "RSI seuil 25/75")
    - actifs_test: Liste d'actifs à utiliser (par défaut: répartition auto)
    """
    maintenant = get_paris_time()

    try:
        # Répartir les actifs en deux groupes
        if actifs_test is None:
            actifs_test = list(ACTIFS_PERMANENTS.keys())

        import random
        random.shuffle(actifs_test)
        milieu = len(actifs_test) // 2
        groupe_a = actifs_test[:milieu]
        groupe_b = actifs_test[milieu:]

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO ab_tests
            (nom, description, variante_a, variante_b, actifs_groupe_a, actifs_groupe_b, date_debut, statut)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            nom,
            description,
            variante_a,
            variante_b,
            json.dumps(groupe_a),
            json.dumps(groupe_b),
            maintenant,
            'actif'
        ))

        test_id = cursor.lastrowid
        conn.commit()
        conn.close()

        # Activer le test
        AB_TESTS_ACTIFS[test_id] = {
            'nom': nom,
            'variante_a': variante_a,
            'variante_b': variante_b,
            'groupe_a': groupe_a,
            'groupe_b': groupe_b
        }

        print(f"[{maintenant.strftime('%H:%M:%S')} CET] 🧪 Test A/B #{test_id} créé: {nom}")
        print(f"   Groupe A ({len(groupe_a)} actifs): {variante_a}")
        print(f"   Groupe B ({len(groupe_b)} actifs): {variante_b}")

        return test_id

    except Exception as e:
        print(f"⚠️ Erreur création A/B test: {e}")
        return None

def get_variante_ab_pour_actif(symbole):
    """
    Retourne la variante A/B active pour un actif donné.
    Retourne: ('A', variante_a) ou ('B', variante_b) ou (None, None)
    """
    for test_id, test in AB_TESTS_ACTIFS.items():
        if symbole in test.get('groupe_a', []):
            return 'A', test.get('variante_a', '')
        elif symbole in test.get('groupe_b', []):
            return 'B', test.get('variante_b', '')
    return None, None

def evaluer_ab_test(test_id, jours_minimum=7):
    """
    Évalue les résultats d'un test A/B après une période minimale.
    Compare les performances des deux groupes.

    CORRIGÉ: BREAKEVEN n'est plus compté comme réussite (c'est neutre)
    CORRIGÉ: Logique gagnant améliorée avec score pondéré taux/PnL
    """
    maintenant = get_paris_time()

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Récupérer le test
        cursor.execute('SELECT * FROM ab_tests WHERE id = ?', (test_id,))
        row = cursor.fetchone()

        if not row or row['statut'] != 'actif':
            conn.close()
            return None, "Test non trouvé ou non actif"

        test = dict(row)
        date_debut = test.get('date_debut')
        if isinstance(date_debut, str):
            date_debut = datetime.fromisoformat(date_debut.replace('Z', '+00:00'))

        # Vérifier durée minimale
        if (maintenant - date_debut).days < jours_minimum:
            conn.close()
            return None, f"Test trop récent ({(maintenant - date_debut).days}/{jours_minimum} jours)"

        groupe_a = json.loads(test.get('actifs_groupe_a', '[]'))
        groupe_b = json.loads(test.get('actifs_groupe_b', '[]'))

        # Stats groupe A - CORRIGÉ: BREAKEVEN exclu des réussites (c'est neutre, PnL ≈ 0)
        placeholders_a = ','.join(['?' for _ in groupe_a])
        cursor.execute(f'''
            SELECT
                COUNT(*) as nb_trades,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as echecs,
                SUM(CASE WHEN resultat = 'BREAKEVEN' THEN 1 ELSE 0 END) as breakeven,
                SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as pnl_total,
                AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as pnl_moyen
            FROM trades_recommandes
            WHERE symbole IN ({placeholders_a})
            AND ab_test_id = ?
            AND ab_groupe = 'A'
            AND resultat IS NOT NULL
            AND timestamp_reco >= ?
        ''', (*groupe_a, test_id, date_debut))
        stats_a = cursor.fetchone()

        # Stats groupe B - CORRIGÉ: BREAKEVEN exclu des réussites
        placeholders_b = ','.join(['?' for _ in groupe_b])
        cursor.execute(f'''
            SELECT
                COUNT(*) as nb_trades,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as echecs,
                SUM(CASE WHEN resultat = 'BREAKEVEN' THEN 1 ELSE 0 END) as breakeven,
                SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as pnl_total,
                AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as pnl_moyen
            FROM trades_recommandes
            WHERE symbole IN ({placeholders_b})
            AND ab_test_id = ?
            AND ab_groupe = 'B'
            AND resultat IS NOT NULL
            AND timestamp_reco >= ?
        ''', (*groupe_b, test_id, date_debut))
        stats_b = cursor.fetchone()

        # Calculer les taux (sur trades conclus uniquement, sans breakeven)
        nb_a = stats_a['nb_trades'] or 0
        nb_b = stats_b['nb_trades'] or 0
        reussis_a = stats_a['reussis'] or 0
        reussis_b = stats_b['reussis'] or 0
        echecs_a = stats_a['echecs'] or 0
        echecs_b = stats_b['echecs'] or 0

        # Taux calculé sur trades décisifs (excluant breakeven)
        decisifs_a = reussis_a + echecs_a
        decisifs_b = reussis_b + echecs_b
        taux_a = round((reussis_a / decisifs_a * 100) if decisifs_a > 0 else 0, 1)
        taux_b = round((reussis_b / decisifs_b * 100) if decisifs_b > 0 else 0, 1)
        pnl_a = round(stats_a['pnl_total'] or 0, 2)
        pnl_b = round(stats_b['pnl_total'] or 0, 2)

        # Test de significativité statistique (Chi-squared simplifié)
        # Minimum 10 trades DÉCISIFS par groupe pour significativité
        significatif = False
        p_value_approx = None
        if decisifs_a >= 10 and decisifs_b >= 10:
            total = decisifs_a + decisifs_b
            total_reussis = reussis_a + reussis_b
            total_echecs = echecs_a + echecs_b

            if total_reussis > 0 and total_echecs > 0:
                # Expected values
                exp_reussis_a = decisifs_a * total_reussis / total
                exp_echecs_a = decisifs_a * total_echecs / total
                exp_reussis_b = decisifs_b * total_reussis / total
                exp_echecs_b = decisifs_b * total_echecs / total

                # Chi-squared (avec protection division par zéro)
                chi2 = 0
                for obs, exp in [(reussis_a, exp_reussis_a), (echecs_a, exp_echecs_a),
                                 (reussis_b, exp_reussis_b), (echecs_b, exp_echecs_b)]:
                    if exp > 0:
                        chi2 += ((obs - exp) ** 2) / exp

                # p < 0.05 correspond à chi2 > 3.84 (1 degré de liberté)
                significatif = chi2 > 3.84
                p_value_approx = "< 0.05" if chi2 > 3.84 else ">= 0.05"

        # CORRIGÉ: Logique gagnant améliorée avec score pondéré
        # Score = 60% taux de réussite + 40% PnL normalisé
        def calculer_score(taux, pnl, pnl_max):
            score_taux = taux  # 0-100
            # Normaliser PnL sur une échelle 0-100 (supposant max ±20%)
            pnl_normalise = min(100, max(0, (pnl + 20) * 2.5)) if pnl_max != 0 else 50
            return 0.6 * score_taux + 0.4 * pnl_normalise

        pnl_max = max(abs(pnl_a), abs(pnl_b), 1)  # Éviter division par 0
        score_a = calculer_score(taux_a, pnl_a, pnl_max)
        score_b = calculer_score(taux_b, pnl_b, pnl_max)

        if significatif:
            # Utiliser le score pondéré au lieu de la double condition stricte
            if score_a > score_b + 5:  # Différence significative de 5 points
                gagnant = 'A'
                conclusion = f"Variante A gagne (p {p_value_approx}, score {score_a:.1f} vs {score_b:.1f}): taux {taux_a}% vs {taux_b}%, PnL {pnl_a}% vs {pnl_b}%"
            elif score_b > score_a + 5:
                gagnant = 'B'
                conclusion = f"Variante B gagne (p {p_value_approx}, score {score_b:.1f} vs {score_a:.1f}): taux {taux_b}% vs {taux_a}%, PnL {pnl_b}% vs {pnl_a}%"
            else:
                gagnant = 'EGALITE'
                conclusion = f"Scores très proches (p {p_value_approx}): A={score_a:.1f} vs B={score_b:.1f}, différence < 5 points"
        else:
            min_decisifs = min(decisifs_a, decisifs_b)
            if min_decisifs < 10:
                gagnant = 'INSUFFISANT'
                conclusion = f"Données insuffisantes: A={decisifs_a} trades décisifs, B={decisifs_b} (min 10 requis)"
            else:
                gagnant = 'EGALITE'
                conclusion = f"Pas de différence significative (p {p_value_approx}): A={taux_a}%/{pnl_a}% vs B={taux_b}%/{pnl_b}%"

        # Sauvegarder les résultats
        cursor.execute('''
            UPDATE ab_tests SET
                resultats_a = ?,
                resultats_b = ?,
                gagnant = ?,
                conclusion = ?,
                date_fin = ?,
                statut = 'termine'
            WHERE id = ?
        ''', (
            json.dumps({'nb_trades': stats_a['nb_trades'], 'taux': taux_a, 'pnl': pnl_a}),
            json.dumps({'nb_trades': stats_b['nb_trades'], 'taux': taux_b, 'pnl': pnl_b}),
            gagnant,
            conclusion,
            maintenant,
            test_id
        ))

        conn.commit()
        conn.close()

        # Désactiver le test
        if test_id in AB_TESTS_ACTIFS:
            del AB_TESTS_ACTIFS[test_id]

        print(f"[{maintenant.strftime('%H:%M:%S')} CET] 🧪 Test A/B #{test_id} terminé: {gagnant}")
        print(f"   {conclusion}")

        return {
            'test_id': test_id,
            'nom': test.get('nom'),
            'gagnant': gagnant,
            'conclusion': conclusion,
            'stats_a': {'nb_trades': stats_a['nb_trades'], 'taux': taux_a, 'pnl': pnl_a},
            'stats_b': {'nb_trades': stats_b['nb_trades'], 'taux': taux_b, 'pnl': pnl_b}
        }, None

    except Exception as e:
        print(f"⚠️ Erreur évaluation A/B test: {e}")
        return None, str(e)

def charger_ab_tests_actifs():
    """Charge les tests A/B actifs au démarrage"""
    global AB_TESTS_ACTIFS
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM ab_tests WHERE statut = 'actif'")
        tests = cursor.fetchall()
        conn.close()

        for row in tests:
            test = dict(row)
            AB_TESTS_ACTIFS[test['id']] = {
                'nom': test['nom'],
                'variante_a': test['variante_a'],
                'variante_b': test['variante_b'],
                'groupe_a': json.loads(test.get('actifs_groupe_a', '[]')),
                'groupe_b': json.loads(test.get('actifs_groupe_b', '[]'))
            }

        if AB_TESTS_ACTIFS:
            print(f"🧪 {len(AB_TESTS_ACTIFS)} test(s) A/B actif(s) chargé(s)")

    except Exception as e:
        print(f"⚠️ Erreur chargement A/B tests: {e}")

def get_instructions_ab_testing():
    """
    Génère les instructions A/B testing à injecter dans le prompt.
    Indique à Claude quelle variante utiliser pour chaque actif.
    """
    if not AB_TESTS_ACTIFS:
        return ""

    instructions = ["\n\n🧪 TESTS A/B EN COURS:"]

    for test_id, test in AB_TESTS_ACTIFS.items():
        instructions.append(f"\nTest '{test['nom']}':")
        instructions.append(f"  Groupe A ({test['variante_a']}): {', '.join(test['groupe_a'][:5])}...")
        instructions.append(f"  Groupe B ({test['variante_b']}): {', '.join(test['groupe_b'][:5])}...")

    return "\n".join(instructions)

def evaluer_tous_ab_tests():
    """Évalue tous les tests A/B actifs depuis plus de 7 jours"""
    resultats = []
    for test_id in list(AB_TESTS_ACTIFS.keys()):
        result, error = evaluer_ab_test(test_id, jours_minimum=7)
        if result:
            resultats.append(result)
    return resultats

# ============================================================================
# CRITÈRES DYNAMIQUES ACTIFS
# ============================================================================

def get_criteres_dynamiques_actifs():
    """
    Récupère les critères dynamiques actifs (scores de confiance, exclusions).

    CORRIGÉ: Ajout d'expiration dynamique basée sur l'âge des données
    CORRIGÉ: Typage explicite des scores en int
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        maintenant = get_paris_time()

        # Récupérer les derniers scores de confiance par catégorie
        # CORRIGÉ: Exclure les scores de plus de 14 jours (obsolètes)
        cursor.execute('''
            SELECT categorie, critere, valeur_actuelle, raison, date_maj
            FROM criteres_dynamiques
            WHERE critere = 'score_confiance'
            AND date_maj > date('now', '-14 days')
            AND id IN (
                SELECT MAX(id) FROM criteres_dynamiques
                WHERE critere = 'score_confiance'
                GROUP BY categorie
            )
        ''')

        scores = {}
        for row in cursor.fetchall():
            # CORRIGÉ: Typage explicite en int
            try:
                score_val = int(float(row['valeur_actuelle']))
            except (ValueError, TypeError):
                score_val = 50  # Valeur par défaut si conversion échoue

            # Calculer l'âge en jours
            date_maj = row['date_maj']
            if isinstance(date_maj, str):
                try:
                    date_maj = datetime.fromisoformat(date_maj.replace('Z', '+00:00'))
                except:
                    date_maj = maintenant
            age_jours = (maintenant - date_maj).days if hasattr(date_maj, 'days') else 0

            scores[row['categorie']] = {
                'score': score_val,
                'raison': row['raison'],
                'date_maj': row['date_maj'],
                'age_jours': age_jours
            }

        # CORRIGÉ: Expiration dynamique des ajustements selon le type
        # - Actions urgentes (ÉVITER): 10 jours (permet feedback 7j + marge)
        # - Actions prudentes (RÉDUIRE/FAVORISER): 21 jours (2 semaines de feedback)
        # CORRIGÉ: Filtre explicite type_ajustement = 'strategie' pour éviter duplication avec scores
        cursor.execute('''
            SELECT categorie, critere, action, raison, date_decision
            FROM ajustements_proposes
            WHERE statut = 'valide'
            AND type_ajustement = 'strategie'
            AND (
                (action = 'ÉVITER' AND date_decision > date('now', '-10 days'))
                OR (action IN ('RÉDUIRE', 'FAVORISER') AND date_decision > date('now', '-21 days'))
            )
        ''')

        ajustements_actifs = []
        for row in cursor.fetchall():
            ajust = dict(row)
            # Calculer l'âge en jours pour pondération
            date_decision = row['date_decision']
            if isinstance(date_decision, str):
                try:
                    date_decision = datetime.fromisoformat(date_decision.replace('Z', '+00:00'))
                    ajust['age_jours'] = (maintenant - date_decision).days
                except:
                    ajust['age_jours'] = 0
            ajustements_actifs.append(ajust)

        conn.close()

        return {
            'scores_confiance': scores,
            'ajustements_actifs': ajustements_actifs
        }

    except Exception as e:
        print(f"⚠️ Erreur critères dynamiques: {e}")
        return {'scores_confiance': {}, 'ajustements_actifs': []}

def generer_instructions_dynamiques():
    """
    Génère les instructions dynamiques à injecter dans le SYSTEM_PROMPT.

    CORRIGÉ: Trou des seuils 50-80 comblé (ajout 'NORMAL')
    CORRIGÉ: Pondération temporelle (ajustements récents prioritaires)
    """
    criteres = get_criteres_dynamiques_actifs()
    instructions = []

    # Analyser les scores de confiance
    # CORRIGÉ: Couverture complète des seuils (0-30, 30-50, 50-65, 65-80, 80+)
    for categorie, data in criteres.get('scores_confiance', {}).items():
        score = data.get('score', 50)
        age = data.get('age_jours', 0)
        age_info = f" (mis à jour il y a {age}j)" if age > 3 else ""

        if score < 30:
            instructions.append(f"⛔ ÉVITER {categorie.upper()}: Score {score}/100{age_info} - {data.get('raison', '')}")
        elif score < 50:
            instructions.append(f"⚠️ PRUDENCE {categorie.upper()}: Score {score}/100{age_info} - Limiter les positions")
        elif score < 65:
            # CORRIGÉ: Zone neutre explicite
            instructions.append(f"➖ NORMAL {categorie.upper()}: Score {score}/100{age_info} - Pas de biais particulier")
        elif score < 80:
            instructions.append(f"👍 BON {categorie.upper()}: Score {score}/100{age_info} - Conditions favorables")
        else:
            instructions.append(f"✅ EXCELLENT {categorie.upper()}: Score {score}/100{age_info} - Opportunités prioritaires")

    # CORRIGÉ: Ajustements triés par priorité (récents et urgents d'abord)
    ajustements = criteres.get('ajustements_actifs', [])

    # Priorité: ÉVITER > RÉDUIRE > FAVORISER, puis par âge croissant
    def priorite_ajustement(a):
        action_prio = {'ÉVITER': 0, 'RÉDUIRE': 1, 'FAVORISER': 2}.get(a.get('action', ''), 3)
        age = a.get('age_jours', 0)
        return (action_prio, age)

    ajustements_tries = sorted(ajustements, key=priorite_ajustement)

    for ajust in ajustements_tries:
        age = ajust.get('age_jours', 0)
        # CORRIGÉ: Pondération temporelle dans le message
        if age == 0:
            recence = "🆕 NOUVEAU"
        elif age <= 2:
            recence = "📍 RÉCENT"
        else:
            recence = f"📅 {age}j"

        if ajust.get('action') == 'ÉVITER':
            instructions.append(f"⛔ [{recence}] {ajust.get('categorie', '').upper()}: {ajust.get('raison', '')}")
        elif ajust.get('action') == 'RÉDUIRE':
            instructions.append(f"⚠️ [{recence}] RÉDUIRE {ajust.get('categorie', '').upper()}: {ajust.get('raison', '')}")
        elif ajust.get('action') == 'FAVORISER':
            instructions.append(f"✅ [{recence}] FAVORISER {ajust.get('categorie', '').upper()}: {ajust.get('raison', '')}")

    if not instructions:
        return ""

    return "\n\nCRITÈRES DYNAMIQUES ACTIFS (basés sur les performances récentes):\n" + "\n".join(instructions)

def enregistrer_journal_complet():
    """Enregistre le journal complet + bilan (22h30)"""
    # D'abord enregistrer le journal de tous les actifs
    enregistrer_journal_quotidien()
    # Puis générer le bilan général
    generer_bilan_quotidien()

def get_journal_quotidien(symbole=None, limite=30, date_from=None, date_to=None, offset=0):
    """Récupère le journal quotidien avec navigation par date et pagination"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Construction de la requête avec filtres
        where_clauses = []
        params = []

        if symbole:
            where_clauses.append('symbole = ?')
            params.append(symbole)

        if date_from:
            where_clauses.append('date >= ?')
            params.append(date_from)

        if date_to:
            where_clauses.append('date <= ?')
            params.append(date_to)

        where_sql = ' AND '.join(where_clauses) if where_clauses else '1=1'

        # Compter le total
        count_query = f'SELECT COUNT(*) FROM journal_quotidien WHERE {where_sql}'
        cursor.execute(count_query, params)
        total_count = cursor.fetchone()[0]

        # Récupérer les entrées
        query = f'''
            SELECT * FROM journal_quotidien
            WHERE {where_sql}
            ORDER BY date DESC, nom_actif ASC
            LIMIT ? OFFSET ?
        '''
        params.extend([limite, offset])
        cursor.execute(query, params)

        entries = []
        json_errors = []

        for row in cursor.fetchall():
            entry = dict(row)

            # Parser les champs JSON avec logging des erreurs
            for json_field in ['opportunites_jour', 'recommandations_analystes']:
                if entry.get(json_field):
                    try:
                        entry[json_field] = json.loads(entry[json_field])
                    except json.JSONDecodeError as e:
                        error_msg = f"JSON PARSE ERROR - journal_quotidien.{json_field} @ {entry.get('date')}/{entry.get('symbole')}: {str(e)[:100]}"
                        print(f"⚠️ {error_msg}")
                        json_errors.append({
                            'date': entry.get('date'),
                            'symbole': entry.get('symbole'),
                            'field': json_field,
                            'error': str(e)[:100]
                        })
                        entry[json_field] = []
                        entry[f'{json_field}_corrupted'] = True

            entries.append(entry)

        conn.close()

        # Log groupé si plusieurs erreurs
        if json_errors:
            print(f"🔴 JOURNAL JSON CORRUPTION: {len(json_errors)} erreurs détectées")

        return {
            'entries': entries,
            'total_count': total_count,
            'json_errors': json_errors
        }
    except Exception as e:
        print(f"⚠️ Erreur récup journal quotidien: {e}")
        return {'entries': [], 'total_count': 0, 'json_errors': []}

def get_historique_opportunites(symbole):
    """Récupère l'historique des opportunités pour un actif"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM trades_recommandes
            WHERE symbole = ?
            ORDER BY timestamp_reco DESC
            LIMIT 50
        ''', (symbole,))

        trades = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return trades
    except Exception as e:
        print(f"⚠️ Erreur historique opportunités: {e}")
        return []

# ============================================================================
# ANALYSES SAUVEGARDÉES
# ============================================================================

def sauvegarder_analyse(type_analyse, contenu, contexte=''):
    """Sauvegarde une analyse"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        maintenant = get_paris_time()

        cursor.execute('''
            INSERT INTO analyses
            (date, type_analyse, heure, contenu, timestamp, contexte_marche)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            maintenant.date(),
            type_analyse,
            maintenant.strftime('%H:%M'),
            json.dumps(contenu, ensure_ascii=False) if isinstance(contenu, dict) else contenu,
            maintenant,
            contexte
        ))

        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"⚠️ Erreur sauvegarde analyse: {e}")
        return False

def get_derniere_analyse(type_analyse=None):
    """Récupère la dernière analyse"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        if type_analyse:
            cursor.execute('''
                SELECT * FROM analyses
                WHERE type_analyse = ?
                ORDER BY timestamp DESC LIMIT 1
            ''', (type_analyse,))
        else:
            cursor.execute('''
                SELECT * FROM analyses
                ORDER BY timestamp DESC LIMIT 1
            ''')

        row = cursor.fetchone()
        conn.close()

        if row:
            result = dict(row)
            try:
                result['contenu'] = json.loads(result['contenu'])
            except json.JSONDecodeError:
                pass
            return result
        return None
    except Exception as e:
        print(f"⚠️ Erreur récupération analyse: {e}")
        return None

def get_analyses_du_jour():
    """Récupère toutes les analyses du jour"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        aujourdhui = get_paris_time().date()
        cursor.execute('''
            SELECT * FROM analyses
            WHERE date = ?
            ORDER BY timestamp DESC
        ''', (aujourdhui,))

        analyses = []
        for row in cursor.fetchall():
            a = dict(row)
            try:
                a['contenu'] = json.loads(a['contenu'])
            except json.JSONDecodeError:
                pass
            analyses.append(a)

        conn.close()
        return analyses
    except Exception as e:
        print(f"⚠️ Erreur analyses jour: {e}")
        return []

# ============================================================================
# ROUTES FLASK - PAGES
# ============================================================================

@app.route('/')
def index():
    """Page d'accueil - Dashboard principal"""
    try:
        return render_template('index.html')
    except Exception as e:
        logger.error(f"Erreur page index: {e}")
        return f"Erreur de chargement: {e}", 500

@app.route('/trading')
def trading():
    """Page d'aide au trading"""
    try:
        return render_template('trading.html')
    except Exception as e:
        logger.error(f"Erreur page trading: {e}")
        return f"Erreur de chargement: {e}", 500

@app.route('/memoire')
def memoire():
    """Page mémoire - Journal et performances"""
    try:
        return render_template('memoire.html')
    except Exception as e:
        logger.error(f"Erreur page memoire: {e}")
        return f"Erreur de chargement: {e}", 500

@app.route('/journal')
def journal():
    """Page journal automatique"""
    try:
        return render_template('journal.html')
    except Exception as e:
        logger.error(f"Erreur page journal: {e}")
        return f"Erreur de chargement: {e}", 500

@app.route('/journal/<symbole>')
def journal_actif(symbole):
    """Page journal d'un actif spécifique"""
    try:
        return render_template('journal_actif.html', symbole=symbole)
    except Exception as e:
        logger.error(f"Erreur page journal_actif {symbole}: {e}")
        return f"Erreur de chargement: {e}", 500

@app.route('/ajustements')
def ajustements():
    """Page de gestion des ajustements proposés par Claude"""
    try:
        return render_template('ajustements.html')
    except Exception as e:
        logger.error(f"Erreur page ajustements: {e}")
        return f"Erreur de chargement: {e}", 500

# ============================================================================
# ROUTES API
# ============================================================================

@app.route('/api/status')
def api_status():
    """Statut de l'application"""
    try:
        maintenant = get_paris_time()
        marche_type, marche_info = get_market_context()

        return jsonify({
            'status': 'online',
            'datetime': maintenant.strftime('%d/%m/%Y %H:%M:%S'),
            'timezone': 'Europe/Paris',
            'marche': marche_type,
            'marche_info': marche_info,
            'news_envoyees': NEWS_ENVOYEES_AUJOURDHUI,
            'max_news': MAX_NEWS_PAR_JOUR,
            'twelvedata_configured': bool(TWELVEDATA_API_KEY)
        })
    except Exception as e:
        logger.error(f"Erreur api_status: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/twelvedata/test')
def api_twelvedata_test():
    """Teste la connexion Twelve Data et liste les symboles fonctionnels"""
    if not TWELVEDATA_API_KEY:
        return jsonify({'success': False, 'error': 'TWELVEDATA_API_KEY non configurée'})

    # Tester quelques symboles clés
    test_symbols = {
        "EUR/USD": "EURUSD=X",
        "SPX": "^GSPC",
        "CAC40": "^FCHI",
        "AAPL": "AAPL",
        "MC (LVMH)": "MC.PA",
        "Or (XAU/USD)": "GC=F"
    }

    results = {}
    for name, yahoo_sym in test_symbols.items():
        td_sym = convert_symbol_to_twelvedata(yahoo_sym)
        try:
            rate_limit_twelvedata()
            url = "https://api.twelvedata.com/quote"
            params = {"symbol": td_sym, "apikey": TWELVEDATA_API_KEY}
            response = requests.get(url, params=params, timeout=10)
            data = response.json()

            if "close" in data:
                results[name] = {
                    "status": "OK",
                    "symbol_td": td_sym,
                    "price": data.get("close"),
                    "change": data.get("percent_change")
                }
            else:
                results[name] = {
                    "status": "ERREUR",
                    "symbol_td": td_sym,
                    "error": data.get("message", "Symbole non trouvé")
                }
        except Exception as e:
            results[name] = {"status": "ERREUR", "symbol_td": td_sym, "error": str(e)}

    return jsonify({
        'success': True,
        'datetime': get_paris_time().strftime('%d/%m/%Y %H:%M:%S'),
        'results': results
    })

@app.route('/api/twelvedata/symbol/<path:symbole>')
def api_twelvedata_symbol(symbole):
    """Teste un symbole spécifique sur Twelve Data"""
    if not TWELVEDATA_API_KEY:
        return jsonify({'success': False, 'error': 'TWELVEDATA_API_KEY non configurée'})

    td_sym = convert_symbol_to_twelvedata(symbole)

    try:
        rate_limit_twelvedata()
        url = "https://api.twelvedata.com/quote"
        params = {"symbol": td_sym, "apikey": TWELVEDATA_API_KEY}
        response = requests.get(url, params=params, timeout=10)
        data = response.json()

        return jsonify({
            'success': "close" in data,
            'yahoo_symbol': symbole,
            'twelvedata_symbol': td_sym,
            'response': data
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/donnees-marche')
def api_donnees_marche():
    """Récupère les données de marché en temps réel"""
    try:
        donnees = recuperer_donnees_marche(ACTIFS_PERMANENTS)
        weekend = is_weekend()
        return jsonify({
            'success': True,
            'datetime': get_paris_time().strftime('%d/%m/%Y %H:%M:%S'),
            'donnees': donnees,
            'weekend': weekend,
            'message_weekend': "Marchés fermés - Données de vendredi" if weekend else None
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/indicateurs/<symbole>')
def api_indicateurs(symbole):
    """Récupère les indicateurs techniques d'un actif"""
    indicateurs = calculer_indicateurs_techniques(symbole)
    if indicateurs:
        return jsonify({'success': True, 'indicateurs': indicateurs})
    return jsonify({'success': False, 'error': 'Données non disponibles'})

@app.route('/api/analyse')
def api_lancer_analyse():
    """Lance une analyse de marché"""
    try:
        weekend = is_weekend()

        # Weekend: pas d'analyse active, retourner synthèse de la semaine
        if weekend:
            # Récupérer le résumé de la semaine dernière
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Stats de la semaine écoulée
            cursor.execute('''
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as gagnants,
                       SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as perdants,
                       ROUND(AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END), 2) as pnl_moyen
                FROM trades_recommandes
                WHERE date >= date('now', '-7 days')
            ''')
            stats = cursor.fetchone()
            conn.close()

            analyse_weekend = {
                'weekend': True,
                'contexte_marche': [
                    "📅 Weekend - Marchés fermés",
                    f"📊 Semaine écoulée: {stats['total'] or 0} trades",
                    f"✅ Gagnants: {stats['gagnants'] or 0} | ❌ Perdants: {stats['perdants'] or 0}",
                    f"📈 PnL moyen: {stats['pnl_moyen'] or 0}%"
                ],
                'news_importante': [],
                'opportunites': [],
                'zones_danger': ["Marchés fermés - Reprendre lundi à 9h"],
                'message_weekend': "Marchés fermés - Synthèse de la semaine"
            }

            return jsonify({
                'success': True,
                'datetime': get_paris_time().strftime('%d/%m/%Y %H:%M:%S'),
                'analyse': analyse_weekend,
                'weekend': True
            })

        donnees = recuperer_donnees_marche(ACTIFS_PERMANENTS)
        donnees_enrichies = enrichir_donnees_avec_indicateurs(donnees)
        # Passer les données enrichies pour éviter double calcul dans analyser_marche_json
        analyse = analyser_marche_json(donnees_enrichies)

        if analyse:
            # Sauvegarder l'analyse
            marche_type, _ = get_market_context()
            sauvegarder_analyse('intraday', analyse, marche_type)

            # Enregistrer les opportunités comme trades (enrichies avec données réelles)
            # Avec validation: conviction >= 3 et ratio R:R >= 1.5
            indicateurs = donnees_enrichies.get('indicateurs_calcules', {})
            opportunites_valides = 0
            opportunites_rejetees = 0

            for opp in analyse.get('opportunites', []):
                # Validation 1: Conviction >= 3
                conviction = opp.get('conviction_score', 0)
                if conviction < 3:
                    print(f"⚠️ [{opp.get('symbole', 'N/A')}] Opportunité rejetée: conviction {conviction} < 3")
                    opportunites_rejetees += 1
                    continue

                # Validation 2: Ratio R:R >= 1.5
                entree = float(opp.get('entree', 0) or 0)
                stop = float(opp.get('stop', 0) or 0)
                tp1 = float(opp.get('tp1', 0) or 0)
                direction = opp.get('direction', 'LONG').upper()

                if entree > 0 and stop > 0 and tp1 > 0:
                    if direction == 'LONG':
                        risque = entree - stop
                        reward = tp1 - entree
                    else:  # SHORT
                        risque = stop - entree
                        reward = entree - tp1

                    if risque > 0:
                        ratio_rr_calc = reward / risque
                        if ratio_rr_calc < 1.5:
                            print(f"⚠️ [{opp.get('symbole', 'N/A')}] Opportunité rejetée: R:R {ratio_rr_calc:.2f} < 1.5")
                            opportunites_rejetees += 1
                            continue

                opp_enrichie = enrichir_opportunite_avec_donnees_marche(opp, donnees, indicateurs)
                enregistrer_recommandation(opp_enrichie)
                opportunites_valides += 1

            if opportunites_rejetees > 0:
                print(f"📊 Bilan opportunités: {opportunites_valides} validées, {opportunites_rejetees} rejetées")

            return jsonify({
                'success': True,
                'datetime': get_paris_time().strftime('%d/%m/%Y %H:%M:%S'),
                'analyse': analyse,
                'weekend': False
            })
        return jsonify({'success': False, 'error': 'Analyse échouée'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/derniere-analyse')
def api_derniere_analyse():
    """Récupère la dernière analyse"""
    analyse = get_derniere_analyse()
    if analyse:
        return jsonify({'success': True, 'analyse': analyse})
    return jsonify({'success': False, 'error': 'Aucune analyse trouvée'})

@app.route('/api/analyses-jour')
def api_analyses_jour():
    """Récupère toutes les analyses du jour"""
    analyses = get_analyses_du_jour()
    return jsonify({
        'success': True,
        'date': get_paris_time().strftime('%d/%m/%Y'),
        'analyses': analyses
    })

@app.route('/api/trades-jour')
def api_trades_jour():
    """Récupère les trades du jour"""
    trades = get_trades_du_jour()
    return jsonify({
        'success': True,
        'date': get_paris_time().strftime('%d/%m/%Y'),
        'trades': trades
    })

@app.route('/api/trades/ouverts')
def api_trades_ouverts():
    """Récupère les trades ouverts avec leur tracking temps réel"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('''
            SELECT id, actif, symbole, direction, prix_entree, prix_stop, prix_tp1, prix_tp2,
                   timestamp_reco, prix_max_atteint, prix_min_atteint, prix_dernier_check,
                   timestamp_dernier_check, pnl_max, pnl_min, nb_checks
            FROM trades_recommandes
            WHERE resultat IS NULL AND symbole IS NOT NULL AND symbole != ''
            ORDER BY timestamp_reco DESC
        ''')

        trades = []
        for row in cursor.fetchall():
            trade = dict(row)
            entree = float(trade.get('prix_entree') or 0)
            prix_actuel = float(trade.get('prix_dernier_check') or entree)
            direction = trade.get('direction', 'LONG')

            if entree > 0:
                if direction == 'LONG':
                    pnl_actuel = ((prix_actuel - entree) / entree) * 100
                else:
                    pnl_actuel = ((entree - prix_actuel) / entree) * 100
                trade['pnl_actuel'] = round(pnl_actuel, 2)

            trades.append(trade)

        conn.close()

        return jsonify({
            'success': True,
            'datetime': get_paris_time().strftime('%d/%m/%Y %H:%M:%S'),
            'trades_ouverts': trades,
            'nb_ouverts': len(trades)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/trades/stats-detaillees')
def api_trades_stats_detaillees():
    """Statistiques détaillées des trades avec breakdown par catégorie"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        periode = request.args.get('periode', 'semaine')
        maintenant = get_paris_time()

        if periode == 'jour':
            date_debut = maintenant.date()
        elif periode == 'semaine':
            date_debut = (maintenant - timedelta(days=7)).date()
        elif periode == 'mois':
            date_debut = (maintenant - timedelta(days=30)).date()
        else:
            date_debut = (maintenant - timedelta(days=365)).date()

        # Stats globales
        cursor.execute('''
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as stops,
                AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as pnl_moyen,
                SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as pnl_total,
                AVG(CASE WHEN pnl_max IS NOT NULL THEN pnl_max END) as pnl_max_moyen,
                MIN(CASE WHEN pnl_min IS NOT NULL THEN pnl_min END) as pnl_min_extreme,
                AVG(duree_minutes) as duree_moyenne
            FROM trades_recommandes WHERE date >= ?
        ''', (date_debut,))
        global_stats = dict(cursor.fetchone())

        # Stats par direction
        cursor.execute('''
            SELECT direction,
                COUNT(*) as total,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                AVG(pnl_pct) as pnl_moyen
            FROM trades_recommandes WHERE date >= ? AND direction IS NOT NULL
            GROUP BY direction
        ''', (date_debut,))
        stats_direction = {row['direction']: dict(row) for row in cursor.fetchall()}

        # Stats par heure d'entrée
        cursor.execute('''
            SELECT heure_entree,
                COUNT(*) as total,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                AVG(pnl_pct) as pnl_moyen
            FROM trades_recommandes WHERE date >= ? AND heure_entree IS NOT NULL
            GROUP BY heure_entree ORDER BY heure_entree
        ''', (date_debut,))
        stats_heure = [dict(row) for row in cursor.fetchall()]

        # Stats par catégorie d'actif
        cursor.execute('''
            SELECT categorie_actif,
                COUNT(*) as total,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                AVG(pnl_pct) as pnl_moyen
            FROM trades_recommandes WHERE date >= ? AND categorie_actif IS NOT NULL
            GROUP BY categorie_actif
        ''', (date_debut,))
        stats_categorie = {row['categorie_actif']: dict(row) for row in cursor.fetchall()}

        conn.close()

        return jsonify({
            'success': True,
            'periode': periode,
            'date_debut': str(date_debut),
            'global': global_stats,
            'par_direction': stats_direction,
            'par_heure': stats_heure,
            'par_categorie': stats_categorie
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/performances')
def api_performances():
    """Récupère les performances"""
    periode = request.args.get('periode', 'semaine')
    performances = get_performances(periode)
    return jsonify({
        'success': True,
        'periode': periode,
        'performances': performances
    })

@app.route('/api/ab-tests')
def api_ab_tests():
    """Récupère les tests A/B actifs avec leurs performances"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Récupérer tous les tests A/B
        cursor.execute('''
            SELECT * FROM ab_tests
            ORDER BY date_debut DESC
        ''')
        tests = [dict(row) for row in cursor.fetchall()]

        # Pour chaque test, calculer les performances par groupe
        for test in tests:
            test_id = test['id']
            groupe_a = json.loads(test.get('actifs_groupe_a', '[]'))
            groupe_b = json.loads(test.get('actifs_groupe_b', '[]'))

            # Stats groupe A
            if groupe_a:
                placeholders = ','.join(['?' for _ in groupe_a])
                cursor.execute(f'''
                    SELECT COUNT(*) as total,
                           SUM(CASE WHEN resultat IN ('TP1', 'TP2') THEN 1 ELSE 0 END) as wins,
                           AVG(pnl_pct) as pnl_moyen,
                           SUM(pnl_pct) as pnl_total
                    FROM trades_recommandes
                    WHERE ab_test_id = ? AND ab_groupe = 'A'
                ''', (test_id,))
                stats_a = dict(cursor.fetchone())
                test['stats_groupe_a'] = {
                    'trades': stats_a['total'] or 0,
                    'win_rate': round((stats_a['wins'] or 0) / stats_a['total'] * 100, 1) if stats_a['total'] else 0,
                    'pnl_moyen': round(stats_a['pnl_moyen'] or 0, 2),
                    'pnl_total': round(stats_a['pnl_total'] or 0, 2)
                }
            else:
                test['stats_groupe_a'] = {'trades': 0, 'win_rate': 0, 'pnl_moyen': 0, 'pnl_total': 0}

            # Stats groupe B
            if groupe_b:
                cursor.execute(f'''
                    SELECT COUNT(*) as total,
                           SUM(CASE WHEN resultat IN ('TP1', 'TP2') THEN 1 ELSE 0 END) as wins,
                           AVG(pnl_pct) as pnl_moyen,
                           SUM(pnl_pct) as pnl_total
                    FROM trades_recommandes
                    WHERE ab_test_id = ? AND ab_groupe = 'B'
                ''', (test_id,))
                stats_b = dict(cursor.fetchone())
                test['stats_groupe_b'] = {
                    'trades': stats_b['total'] or 0,
                    'win_rate': round((stats_b['wins'] or 0) / stats_b['total'] * 100, 1) if stats_b['total'] else 0,
                    'pnl_moyen': round(stats_b['pnl_moyen'] or 0, 2),
                    'pnl_total': round(stats_b['pnl_total'] or 0, 2)
                }
            else:
                test['stats_groupe_b'] = {'trades': 0, 'win_rate': 0, 'pnl_moyen': 0, 'pnl_total': 0}

        conn.close()

        # Séparer tests actifs et terminés
        tests_actifs = [t for t in tests if t['statut'] == 'actif']
        tests_termines = [t for t in tests if t['statut'] != 'actif']

        return jsonify({
            'success': True,
            'tests_actifs': tests_actifs,
            'tests_termines': tests_termines[:5],  # 5 derniers terminés
            'total_actifs': len(tests_actifs)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/journal/<symbole>')
def api_journal(symbole):
    """Récupère le journal d'un actif"""
    entries = get_journal_actif(symbole)
    return jsonify({
        'success': True,
        'symbole': symbole,
        'entries': entries
    })

@app.route('/api/journal', methods=['POST'])
def api_ajouter_journal():
    """Ajoute une entrée au journal"""
    data = request.json
    success = ajouter_entree_journal(
        data.get('symbole'),
        data.get('nom_actif'),
        data.get('type_info'),
        data.get('titre'),
        data.get('contenu'),
        data.get('impact_cours', ''),
        data.get('importance', 2)
    )
    return jsonify({'success': success})

@app.route('/api/journaux')
def api_tous_journaux():
    """Liste tous les actifs avec journal"""
    actifs = get_tous_journaux()
    return jsonify({
        'success': True,
        'actifs': actifs
    })

@app.route('/api/actifs')
def api_actifs():
    """Liste tous les actifs suivis"""
    return jsonify({
        'success': True,
        'permanents': ACTIFS_PERMANENTS,
        'rotation': POOL_ROTATION
    })

@app.route('/api/cloture')
def api_cloture():
    """Génère l'analyse de clôture"""
    try:
        donnees = recuperer_donnees_marche(ACTIFS_PERMANENTS)
        cloture = generer_cloture_json(donnees)

        if cloture:
            sauvegarder_analyse('cloture', cloture)
            return jsonify({
                'success': True,
                'datetime': get_paris_time().strftime('%d/%m/%Y %H:%M:%S'),
                'cloture': cloture
            })
        return jsonify({'success': False, 'error': 'Génération échouée'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/news')
def api_news():
    """Récupère et analyse les actualités pour leur impact trading.
    OPTIMISÉ: Cache de 5 minutes pour éviter appels NewsAPI répétés."""
    global NEWS_CACHE

    # Vérifier le cache d'abord
    now = time.time()
    if NEWS_CACHE['data'] and (now - NEWS_CACHE['timestamp']) < NEWS_CACHE_TTL:
        return jsonify({
            'success': True,
            'datetime': get_paris_time().strftime('%d/%m/%Y %H:%M:%S'),
            'news': NEWS_CACHE['data'],
            'count': len(NEWS_CACHE['data']),
            'cached': True
        })

    if not NEWSAPI_KEY:
        # Fallback: retourner les news de la BDD si pas de clé API
        news_db = get_news_historique(jours=1)
        if news_db:
            return jsonify({
                'success': True,
                'datetime': get_paris_time().strftime('%d/%m/%Y %H:%M:%S'),
                'news': news_db,
                'count': len(news_db),
                'source': 'database'
            })
        return jsonify({'success': False, 'error': 'NEWSAPI_KEY non configurée'})

    try:
        # Récupérer et analyser les news
        news_analysees, error = fetch_and_analyze_news()

        if error:
            # Fallback: retourner les news en cache ou BDD
            if NEWS_CACHE['data']:
                return jsonify({
                    'success': True,
                    'news': NEWS_CACHE['data'],
                    'count': len(NEWS_CACHE['data']),
                    'cached': True,
                    'warning': error
                })
            return jsonify({'success': False, 'error': error})

        # Mettre en cache
        NEWS_CACHE['data'] = news_analysees
        NEWS_CACHE['timestamp'] = now

        return jsonify({
            'success': True,
            'datetime': get_paris_time().strftime('%d/%m/%Y %H:%M:%S'),
            'news': news_analysees,
            'count': len(news_analysees)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/news/raw')
def api_news_raw():
    """Récupère les actualités brutes sans analyse (fallback)"""
    if not NEWSAPI_KEY:
        return jsonify({'success': False, 'error': 'NEWSAPI_KEY non configurée'})

    try:
        url = "https://newsapi.org/v2/top-headlines"
        params = {
            'apiKey': NEWSAPI_KEY,
            'category': 'business',
            'language': 'fr',
            'pageSize': 10
        }

        response = requests.get(url, params=params, timeout=10)
        data = response.json()

        if data.get('status') == 'ok':
            articles = []
            for article in data.get('articles', [])[:10]:
                published = article.get('publishedAt', '')
                heure = '--:--'
                if published:
                    try:
                        dt = datetime.fromisoformat(published.replace('Z', '+00:00'))
                        dt_paris = dt.astimezone(TZ_PARIS)
                        heure = dt_paris.strftime('%H:%M')
                    except ValueError:
                        pass

                articles.append({
                    'titre': article.get('title', '')[:100],
                    'source': article.get('source', {}).get('name', 'Inconnu'),
                    'heure': heure,
                    'url': article.get('url', '')
                })

            return jsonify({
                'success': True,
                'datetime': get_paris_time().strftime('%d/%m/%Y %H:%M:%S'),
                'articles': articles
            })

        return jsonify({'success': False, 'error': 'Aucun article trouvé'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/news/historique')
def api_news_historique():
    """Récupère l'historique des news analysées avec navigation par jour"""
    try:
        jours = int(request.args.get('jours', 7))
        news = get_news_historique(jours)

        # Grouper par date
        par_date = {}
        for n in news:
            date_str = str(n.get('date', ''))
            if date_str not in par_date:
                par_date[date_str] = []
            par_date[date_str].append({
                'heure': n.get('heure', '--:--'),
                'headline': n.get('titre', ''),
                'analyse': n.get('contenu', ''),
                'impact': n.get('impact', 'faible'),
                'actifs': n.get('actif', '').split(', ') if n.get('actif') else []
            })

        # Convertir en liste triée par date
        dates_triees = sorted(par_date.keys(), reverse=True)
        historique = [{'date': d, 'news': par_date[d]} for d in dates_triees]

        return jsonify({
            'success': True,
            'historique': historique,
            'nb_jours': len(historique)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/top-movers')
def api_top_movers():
    """Récupère les plus fortes variations du jour"""
    try:
        donnees = recuperer_donnees_marche(ACTIFS_PERMANENTS)
        weekend = is_weekend()

        # Trier par variation absolue - FILTRE les données invalides
        movers = []
        for nom, data in donnees.items():
            variation = data.get('variation')
            prix = data.get('prix')
            # Skip si données manquantes ou invalides
            if variation is None or prix is None:
                continue
            movers.append({
                'nom': nom,
                'symbole': data.get('symbole', ''),
                'prix': prix,
                'variation': variation
            })

        # Trier par variation absolue décroissante
        movers_sorted = sorted(movers, key=lambda x: abs(x['variation']), reverse=True)

        # Séparer gainers et losers - 5 de chaque
        gainers = [m for m in movers_sorted if m['variation'] > 0][:5]
        losers = [m for m in movers_sorted if m['variation'] < 0][:5]

        return jsonify({
            'success': True,
            'datetime': get_paris_time().strftime('%d/%m/%Y %H:%M:%S'),
            'gainers': gainers,
            'losers': losers,
            'weekend': weekend,
            'message_weekend': "Clôture de vendredi" if weekend else None
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/premarket')
def api_premarket():
    """Récupère les données pré-market"""
    try:
        donnees = recuperer_donnees_premarket()
        return jsonify({
            'success': True,
            'datetime': get_paris_time().strftime('%d/%m/%Y %H:%M:%S'),
            'premarket': donnees
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/journal-quotidien')
def api_journal_quotidien():
    """Récupère le journal quotidien avec navigation par date et pagination"""
    try:
        symbole = request.args.get('symbole')
        limite = int(request.args.get('limite', 50))
        limite = min(limite, 200)  # Max 200 entrées
        page = int(request.args.get('page', 0))
        offset = page * limite
        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')

        result = get_journal_quotidien(
            symbole=symbole,
            limite=limite,
            date_from=date_from,
            date_to=date_to,
            offset=offset
        )

        total_pages = (result['total_count'] + limite - 1) // limite if result['total_count'] > 0 else 0

        return jsonify({
            'success': True,
            'entries': result['entries'],
            'pagination': {
                'page': page,
                'limit': limite,
                'total_count': result['total_count'],
                'total_pages': total_pages,
                'has_next': page < total_pages - 1,
                'has_prev': page > 0
            },
            'json_errors_count': len(result['json_errors'])
        })
    except Exception as e:
        logger.error(f"Erreur api_journal_quotidien: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/journal-quotidien/generer', methods=['POST'])
def api_generer_journal_quotidien():
    """Force la génération du journal quotidien"""
    success = enregistrer_journal_quotidien()
    return jsonify({'success': success})

@app.route('/api/historique-opportunites/<symbole>')
def api_historique_opportunites(symbole):
    """Récupère l'historique des opportunités pour un actif"""
    trades = get_historique_opportunites(symbole)
    return jsonify({
        'success': True,
        'symbole': symbole,
        'trades': trades
    })

@app.route('/api/stats-evolution')
def api_stats_evolution():
    """Récupère l'évolution des stats pour le graphique"""
    try:
        granularite = request.args.get('granularite', 'jour')  # jour, semaine, mois
        limite = int(request.args.get('limite', 30))

        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        if granularite == 'jour':
            cursor.execute('''
                SELECT date,
                       COUNT(*) as nb_trades,
                       SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                       SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as stops,
                       SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl_cumule
                FROM trades_recommandes
                GROUP BY date
                ORDER BY date DESC
                LIMIT ?
            ''', (limite,))
        elif granularite == 'semaine':
            cursor.execute('''
                SELECT strftime('%Y-W%W', date) as periode,
                       MIN(date) as date,
                       COUNT(*) as nb_trades,
                       SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                       SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as stops,
                       SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl_cumule
                FROM trades_recommandes
                GROUP BY strftime('%Y-W%W', date)
                ORDER BY periode DESC
                LIMIT ?
            ''', (limite,))
        else:  # mois
            cursor.execute('''
                SELECT strftime('%Y-%m', date) as periode,
                       MIN(date) as date,
                       COUNT(*) as nb_trades,
                       SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                       SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as stops,
                       SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl_cumule
                FROM trades_recommandes
                GROUP BY strftime('%Y-%m', date)
                ORDER BY periode DESC
                LIMIT ?
            ''', (limite,))

        # Récupérer les données (triées DESC) et les inverser pour ordre chronologique
        raw_data = [dict(row) for row in cursor.fetchall()]
        raw_data.reverse()  # Maintenant en ordre chronologique (plus ancien en premier)

        conn.close()

        # Calculer le cumul dans l'ordre chronologique correct
        stats = []
        pnl_running = 0
        for data in raw_data:
            pnl_running += data['pnl_cumule'] or 0
            data['pnl_cumule_running'] = round(pnl_running, 2)
            conclus = (data['reussis'] or 0) + (data['stops'] or 0)
            data['taux_reussite'] = round((data['reussis'] / conclus * 100) if conclus > 0 else 0, 1)
            stats.append(data)

        return jsonify({
            'success': True,
            'granularite': granularite,
            'stats': stats
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/bilan-quotidien')
def api_bilan_quotidien():
    """Récupère le bilan quotidien"""
    try:
        date_str = request.args.get('date')
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        if date_str:
            cursor.execute('''
                SELECT * FROM bilan_quotidien WHERE date = ?
            ''', (date_str,))
        else:
            cursor.execute('''
                SELECT * FROM bilan_quotidien ORDER BY date DESC LIMIT 1
            ''')

        row = cursor.fetchone()
        conn.close()

        if row:
            bilan = dict(row)
            # Parser les JSON
            try:
                bilan['points_positifs'] = json.loads(bilan['points_positifs'] or '[]')
            except json.JSONDecodeError:
                bilan['points_positifs'] = []
            try:
                bilan['points_negatifs'] = json.loads(bilan['points_negatifs'] or '[]')
            except json.JSONDecodeError:
                bilan['points_negatifs'] = []
            try:
                bilan['lecons_apprises'] = json.loads(bilan['lecons_apprises'] or '[]')
            except json.JSONDecodeError:
                bilan['lecons_apprises'] = []
            try:
                bilan['faits_marquants'] = json.loads(bilan['faits_marquants'] or '[]')
            except json.JSONDecodeError:
                bilan['faits_marquants'] = []
            return jsonify({'success': True, 'bilan': bilan})

        return jsonify({'success': False, 'error': 'Aucun bilan trouvé'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/bilan-quotidien/generer', methods=['POST'])
def api_generer_bilan():
    """Force la génération du bilan quotidien"""
    bilan = generer_bilan_quotidien()
    if bilan:
        return jsonify({'success': True, 'bilan': bilan})
    return jsonify({'success': False, 'error': 'Erreur génération'})

@app.route('/api/trades/verifier', methods=['POST'])
def api_verifier_trades():
    """Force la vérification des résultats des trades"""
    nb = verifier_resultats_trades()
    return jsonify({'success': True, 'trades_mis_a_jour': nb})

@app.route('/api/trades/cloturer', methods=['POST'])
def api_cloturer_trades():
    """Force la clôture des trades du jour"""
    # D'abord vérifier
    verifier_resultats_trades()
    # Puis clôturer
    nb = cloturer_trades_jour()
    return jsonify({'success': True, 'trades_clotures': nb})

@app.route('/api/evenements-macro')
def api_evenements_macro():
    """Récupère les événements macro du jour et vérifie la proximité"""
    try:
        weekend = is_weekend()
        evenements = get_evenements_macro_jour()  # Retourne [] le weekend

        # Pas d'alerte imminente le weekend
        if weekend:
            evt_imminent = False
            evt_details = None
        else:
            evt_imminent, evt_details = verifier_proximite_evenement_macro(minutes_avant=30)

        return jsonify({
            'success': True,
            'datetime': get_paris_time().strftime('%d/%m/%Y %H:%M:%S'),
            'evenements': evenements,
            'alerte_imminente': evt_imminent,
            'evenement_imminent': evt_details,
            'weekend': weekend,
            'message_weekend': "Marchés fermés le weekend" if weekend else None
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/actifs-faible-atr')
def api_actifs_faible_atr():
    """Récupère les actifs avec ATR trop faible pour le day trading"""
    try:
        donnees = recuperer_donnees_marche(ACTIFS_PERMANENTS)
        actifs_exclus = get_actifs_filtres_atr(donnees, seuil_atr_min=1.0)

        return jsonify({
            'success': True,
            'seuil_atr': 1.0,
            'actifs_exclus': actifs_exclus,
            'nb_exclus': len(actifs_exclus)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/rapport-hebdo')
def api_rapport_hebdo():
    """Récupère le dernier rapport hebdomadaire"""
    try:
        semaine = request.args.get('semaine')
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        if semaine:
            cursor.execute('SELECT * FROM rapports_hebdo WHERE semaine = ?', (semaine,))
        else:
            cursor.execute('SELECT * FROM rapports_hebdo ORDER BY date_fin DESC LIMIT 1')

        row = cursor.fetchone()
        conn.close()

        if row:
            rapport = dict(row)
            # Parser les champs JSON
            for field in ['chiffres_cles', 'forces', 'faiblesses', 'patterns', 'ajustements', 'scores_confiance', 'focus_semaine']:
                try:
                    rapport[field] = json.loads(rapport[field] or '[]')
                except json.JSONDecodeError:
                    rapport[field] = [] if field not in ['chiffres_cles', 'scores_confiance'] else {}

            return jsonify({'success': True, 'rapport': rapport})

        return jsonify({'success': False, 'error': 'Aucun rapport trouvé'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/rapport-hebdo/generer', methods=['POST'])
def api_generer_rapport_hebdo():
    """Force la génération du rapport hebdomadaire"""
    rapport = generer_rapport_hebdo()
    if rapport:
        return jsonify({'success': True, 'rapport': rapport})
    return jsonify({'success': False, 'error': 'Erreur génération'})

@app.route('/api/reevaluation-intraday')
def api_reevaluation_intraday():
    """Lance une réévaluation intraday des trades en cours"""
    try:
        reevaluations = reevaluer_trades_intraday()
        return jsonify({
            'success': True,
            'datetime': get_paris_time().strftime('%d/%m/%Y %H:%M:%S'),
            'reevaluations': reevaluations,
            'nb_trades': len(reevaluations)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/contexte-trading')
def api_contexte_trading():
    """Récupère le contexte de trading complet (VIX, session, jour, etc.)"""
    try:
        contexte = get_contexte_trading_complet()
        return jsonify({'success': True, 'contexte': contexte})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/regime-marche')
def api_regime_marche():
    """Récupère le régime de marché actuel basé sur le VIX"""
    try:
        vix = get_vix_level()
        regime, description, regles = get_regime_marche(vix)
        return jsonify({
            'success': True,
            'vix': round(vix, 2),
            'regime': regime,
            'description': description,
            'regles_adaptees': regles
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/stats-avancees')
def api_stats_avancees():
    """Récupère les stats avancées pour A/B testing (jour, session, conviction, etc.)"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Stats des 7 derniers jours
        date_debut = (get_paris_time() - timedelta(days=7)).strftime('%Y-%m-%d')

        # Stats par jour de la semaine
        cursor.execute('''
            SELECT jour_semaine,
                COUNT(*) as nb,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                ROUND(AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END), 2) as pnl_moyen
            FROM trades_recommandes
            WHERE date >= ? AND jour_semaine IS NOT NULL
            GROUP BY jour_semaine
        ''', (date_debut,))
        par_jour = [dict(row) for row in cursor.fetchall()]

        # Stats par conviction
        cursor.execute('''
            SELECT conviction_score,
                COUNT(*) as nb,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                ROUND(AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END), 2) as pnl_moyen
            FROM trades_recommandes
            WHERE date >= ? AND conviction_score IS NOT NULL
            GROUP BY conviction_score
            ORDER BY conviction_score
        ''', (date_debut,))
        par_conviction = [dict(row) for row in cursor.fetchall()]

        # Stats par stratégie d'entrée
        cursor.execute('''
            SELECT strategie_entree,
                COUNT(*) as nb,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                ROUND(AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END), 2) as pnl_moyen
            FROM trades_recommandes
            WHERE date >= ? AND strategie_entree IS NOT NULL
            GROUP BY strategie_entree
        ''', (date_debut,))
        par_strategie = [dict(row) for row in cursor.fetchall()]

        # Stats par régime
        cursor.execute('''
            SELECT regime_marche,
                COUNT(*) as nb,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                ROUND(AVG(vix_niveau), 1) as vix_moyen
            FROM trades_recommandes
            WHERE date >= ? AND regime_marche IS NOT NULL
            GROUP BY regime_marche
        ''', (date_debut,))
        par_regime = [dict(row) for row in cursor.fetchall()]

        conn.close()

        return jsonify({
            'success': True,
            'periode': f"7 derniers jours (depuis {date_debut})",
            'par_jour_semaine': par_jour,
            'par_conviction': par_conviction,
            'par_strategie_entree': par_strategie,
            'par_regime_marche': par_regime
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/criteres-dynamiques')
def api_criteres_dynamiques():
    """Récupère les critères dynamiques actuels"""
    try:
        criteres = get_criteres_dynamiques()
        return jsonify({'success': True, 'criteres': criteres})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/ajustements-proposes')
def api_ajustements_proposes():
    """Récupère les ajustements en attente de validation"""
    try:
        ajustements = get_ajustements_en_attente()
        return jsonify({
            'success': True,
            'ajustements': ajustements,
            'nb_en_attente': len(ajustements)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/ajustements-proposes/<int:id_ajustement>/valider', methods=['POST'])
def api_valider_ajustement(id_ajustement):
    """Valide un ajustement proposé avec commentaire optionnel"""
    try:
        data = request.get_json(silent=True) or {}
        commentaire = data.get('commentaire', None)
        succes, message = valider_ajustement(id_ajustement, decision=True, commentaire=commentaire)
        return jsonify({'success': succes, 'message': message})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/ajustements-proposes/<int:id_ajustement>/rejeter', methods=['POST'])
def api_rejeter_ajustement(id_ajustement):
    """Rejette un ajustement proposé avec commentaire optionnel"""
    try:
        data = request.get_json(silent=True) or {}
        commentaire = data.get('commentaire', None)
        succes, message = valider_ajustement(id_ajustement, decision=False, commentaire=commentaire)
        return jsonify({'success': succes, 'message': message})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/ajustements-proposes/valider-tous', methods=['POST'])
def api_valider_tous_ajustements():
    """Valide tous les ajustements en attente"""
    try:
        resultats = valider_tous_ajustements(decision=True)
        nb_valides = sum(1 for r in resultats if r['succes'])
        return jsonify({
            'success': True,
            'message': f"{nb_valides} ajustements validés",
            'details': resultats
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/ajustements-proposes/rejeter-tous', methods=['POST'])
def api_rejeter_tous_ajustements():
    """Rejette tous les ajustements en attente"""
    try:
        resultats = valider_tous_ajustements(decision=False)
        nb_rejetes = sum(1 for r in resultats if r['succes'])
        return jsonify({
            'success': True,
            'message': f"{nb_rejetes} ajustements rejetés",
            'details': resultats
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/ajustements-historique')
def api_ajustements_historique():
    """Récupère l'historique des ajustements avec audit trail"""
    try:
        limite = int(request.args.get('limite', 50))
        historique = get_historique_ajustements(limite)

        # Enrichir avec les critères appliqués (audit trail)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        for ajust in historique:
            if ajust.get('statut') == 'valide':
                cursor.execute('''
                    SELECT * FROM criteres_dynamiques
                    WHERE ajustement_source_id = ?
                ''', (ajust['id'],))
                critere_applique = cursor.fetchone()
                if critere_applique:
                    ajust['critere_applique'] = dict(critere_applique)

        conn.close()

        return jsonify({
            'success': True,
            'historique': historique,
            'total': len(historique)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/historique-rapports')
def api_historique_rapports():
    """Récupère l'historique des rapports hebdomadaires"""
    try:
        limite = int(request.args.get('limite', 10))
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('''
            SELECT semaine, date_debut, date_fin, resume_executif, chiffres_cles, timestamp
            FROM rapports_hebdo
            ORDER BY date_fin DESC
            LIMIT ?
        ''', (limite,))

        rapports = []
        for row in cursor.fetchall():
            r = dict(row)
            try:
                r['chiffres_cles'] = json.loads(r['chiffres_cles'] or '{}')
            except json.JSONDecodeError:
                r['chiffres_cles'] = {}
            rapports.append(r)

        conn.close()
        return jsonify({'success': True, 'rapports': rapports})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/stats-detaillees')
def api_stats_detaillees():
    """Récupère les statistiques détaillées par heure, actif et type"""
    try:
        periode = request.args.get('periode', 'mois')
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        maintenant = get_paris_time()
        if periode == 'jour':
            date_debut = maintenant.date()
        elif periode == 'semaine':
            date_debut = (maintenant - timedelta(days=7)).date()
        elif periode == 'mois':
            date_debut = (maintenant - timedelta(days=30)).date()
        else:
            date_debut = (maintenant - timedelta(days=365)).date()

        # Stats par heure d'entrée
        cursor.execute('''
            SELECT heure_entree as heure,
                   COUNT(*) as nb_trades,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                   SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as stops,
                   AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as pnl_moyen,
                   AVG(CASE WHEN duree_minutes IS NOT NULL THEN duree_minutes END) as duree_moyenne
            FROM trades_recommandes
            WHERE date >= ? AND heure_entree IS NOT NULL
            GROUP BY heure_entree
            ORDER BY heure_entree
        ''', (date_debut,))
        stats_par_heure = [dict(row) for row in cursor.fetchall()]

        # Stats par catégorie d'actif
        cursor.execute('''
            SELECT categorie_actif as categorie,
                   COUNT(*) as nb_trades,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                   SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as stops,
                   AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as pnl_moyen,
                   AVG(CASE WHEN duree_minutes IS NOT NULL THEN duree_minutes END) as duree_moyenne
            FROM trades_recommandes
            WHERE date >= ? AND categorie_actif IS NOT NULL AND categorie_actif != ''
            GROUP BY categorie_actif
            ORDER BY nb_trades DESC
        ''', (date_debut,))
        stats_par_categorie = [dict(row) for row in cursor.fetchall()]

        # Stats par type de setup (NEWS vs TECHNIQUE)
        cursor.execute('''
            SELECT type_setup as type,
                   COUNT(*) as nb_trades,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                   SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as stops,
                   AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as pnl_moyen,
                   AVG(CASE WHEN duree_minutes IS NOT NULL THEN duree_minutes END) as duree_moyenne
            FROM trades_recommandes
            WHERE date >= ? AND type_setup IS NOT NULL
            GROUP BY type_setup
        ''', (date_debut,))
        stats_par_type = [dict(row) for row in cursor.fetchall()]

        # Stats par direction (LONG vs SHORT)
        cursor.execute('''
            SELECT direction,
                   COUNT(*) as nb_trades,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                   SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as stops,
                   AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as pnl_moyen
            FROM trades_recommandes
            WHERE date >= ? AND direction IS NOT NULL
            GROUP BY direction
        ''', (date_debut,))
        stats_par_direction = [dict(row) for row in cursor.fetchall()]

        # Top 5 actifs les plus performants (basé sur trades CONCLUS uniquement)
        cursor.execute('''
            SELECT actif, symbole,
                   COUNT(*) as nb_trades,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                   SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as stops,
                   AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as pnl_moyen
            FROM trades_recommandes
            WHERE date >= ? AND resultat IN ('TP1', 'TP2', 'WIN_FORCE', 'STOP', 'LOSS_FORCE')
            GROUP BY symbole
            HAVING (reussis + stops) >= 2
            ORDER BY (CAST(reussis AS FLOAT) / (reussis + stops)) DESC, pnl_moyen DESC
            LIMIT 5
        ''', (date_debut,))
        top_actifs = [dict(row) for row in cursor.fetchall()]

        # Bottom 5 actifs les moins performants (basé sur trades CONCLUS uniquement)
        cursor.execute('''
            SELECT actif, symbole,
                   COUNT(*) as nb_trades,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                   SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as stops,
                   AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as pnl_moyen
            FROM trades_recommandes
            WHERE date >= ? AND resultat IN ('TP1', 'TP2', 'WIN_FORCE', 'STOP', 'LOSS_FORCE')
            GROUP BY symbole
            HAVING (reussis + stops) >= 2
            ORDER BY (CAST(reussis AS FLOAT) / (reussis + stops)) ASC, pnl_moyen ASC
            LIMIT 5
        ''', (date_debut,))
        bottom_actifs = [dict(row) for row in cursor.fetchall()]

        # Statistiques de durée des trades
        cursor.execute('''
            SELECT
                AVG(duree_minutes) as duree_moyenne,
                MIN(duree_minutes) as duree_min,
                MAX(duree_minutes) as duree_max,
                AVG(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN duree_minutes END) as duree_moyenne_gagnants,
                AVG(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN duree_minutes END) as duree_moyenne_perdants
            FROM trades_recommandes
            WHERE date >= ? AND duree_minutes IS NOT NULL
        ''', (date_debut,))
        row = cursor.fetchone()
        stats_duree = dict(row) if row else {}

        conn.close()

        # Calculer les taux de réussite
        for stat_list in [stats_par_heure, stats_par_categorie, stats_par_type, stats_par_direction]:
            for s in stat_list:
                conclus = (s.get('reussis') or 0) + (s.get('stops') or 0)
                s['taux_reussite'] = round((s.get('reussis', 0) / conclus * 100) if conclus > 0 else 0, 1)
                s['pnl_moyen'] = round(s.get('pnl_moyen') or 0, 2)
                s['duree_moyenne'] = round(s.get('duree_moyenne') or 0, 0)

        for s in top_actifs + bottom_actifs:
            conclus = (s.get('reussis') or 0) + (s.get('stops') or 0)
            s['taux_reussite'] = round((s.get('reussis', 0) / conclus * 100) if conclus > 0 else 0, 1)
            s['pnl_moyen'] = round(s.get('pnl_moyen') or 0, 2)

        return jsonify({
            'success': True,
            'periode': periode,
            'stats_par_heure': stats_par_heure,
            'stats_par_categorie': stats_par_categorie,
            'stats_par_type': stats_par_type,
            'stats_par_direction': stats_par_direction,
            'top_actifs': top_actifs,
            'bottom_actifs': bottom_actifs,
            'stats_duree': {
                'moyenne': round(stats_duree.get('duree_moyenne') or 0, 0),
                'min': stats_duree.get('duree_min') or 0,
                'max': stats_duree.get('duree_max') or 0,
                'moyenne_gagnants': round(stats_duree.get('duree_moyenne_gagnants') or 0, 0),
                'moyenne_perdants': round(stats_duree.get('duree_moyenne_perdants') or 0, 0)
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/trades-historique')
def api_trades_historique():
    """Récupère l'historique des trades avec filtres et pagination"""
    try:
        # Paramètres de pagination
        page = int(request.args.get('page', 0))
        limit = int(request.args.get('limit', 100))
        limit = min(limit, 500)  # Max 500 par page pour éviter surcharge
        offset = page * limit

        # Paramètres de filtrage
        periode = request.args.get('periode', 'semaine')
        categorie = request.args.get('categorie', '')
        direction = request.args.get('direction', '')
        recherche = request.args.get('recherche', '')
        resultat = request.args.get('resultat', '')  # TP1, TP2, STOP, etc.
        conviction_min = request.args.get('conviction_min', '')
        regime = request.args.get('regime', '')

        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        maintenant = get_paris_time()
        if periode == 'jour':
            date_debut = maintenant.date()
        elif periode == 'semaine':
            date_debut = (maintenant - timedelta(days=7)).date()
        elif periode == 'mois':
            date_debut = (maintenant - timedelta(days=30)).date()
        elif periode == 'trimestre':
            date_debut = (maintenant - timedelta(days=90)).date()
        elif periode == 'annee':
            date_debut = (maintenant - timedelta(days=365)).date()
        else:
            date_debut = (maintenant - timedelta(days=365)).date()

        # Construction requête avec filtres
        where_clauses = ['date >= ?']
        params = [date_debut]

        if categorie:
            where_clauses.append('categorie_actif = ?')
            params.append(categorie)

        if direction:
            where_clauses.append('direction = ?')
            params.append(direction)

        if resultat:
            where_clauses.append('resultat = ?')
            params.append(resultat)

        if conviction_min:
            where_clauses.append('conviction_score >= ?')
            params.append(int(conviction_min))

        if regime:
            where_clauses.append('regime_marche = ?')
            params.append(regime)

        if recherche:
            where_clauses.append('(actif LIKE ? OR symbole LIKE ? OR justification LIKE ?)')
            params.extend([f'%{recherche}%', f'%{recherche}%', f'%{recherche}%'])

        where_sql = ' AND '.join(where_clauses)

        # Compter le total pour pagination
        count_query = f'SELECT COUNT(*) FROM trades_recommandes WHERE {where_sql}'
        cursor.execute(count_query, params)
        total_count = cursor.fetchone()[0]

        # Requête avec projection de colonnes (évite SELECT *)
        select_cols = '''id, date, timestamp_reco, actif, symbole, direction,
                         prix_entree, stop_loss, take_profit_1, take_profit_2,
                         resultat, pnl_pct, duree_minutes, categorie_actif,
                         conviction_score, regime_marche, strategie_entree,
                         statut_intraday, action_recommandee, justification'''

        query = f'''SELECT {select_cols} FROM trades_recommandes
                    WHERE {where_sql}
                    ORDER BY timestamp_reco DESC
                    LIMIT ? OFFSET ?'''
        params.extend([limit, offset])

        cursor.execute(query, params)
        trades = [dict(row) for row in cursor.fetchall()]
        conn.close()

        # Calcul des pages
        total_pages = (total_count + limit - 1) // limit

        return jsonify({
            'success': True,
            'trades': trades,
            'pagination': {
                'page': page,
                'limit': limit,
                'total_count': total_count,
                'total_pages': total_pages,
                'has_next': page < total_pages - 1,
                'has_prev': page > 0
            }
        })
    except Exception as e:
        logger.error(f"Erreur api_trades_historique: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/equity-curve')
def api_equity_curve():
    """Calcule la courbe d'equity (PnL cumulé) avec métriques avancées"""
    try:
        periode = request.args.get('periode', 'annee')
        granularite = request.args.get('granularite', 'jour')  # jour, semaine, mois

        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        maintenant = get_paris_time()
        if periode == 'mois':
            date_debut = (maintenant - timedelta(days=30)).date()
        elif periode == 'trimestre':
            date_debut = (maintenant - timedelta(days=90)).date()
        elif periode == 'semestre':
            date_debut = (maintenant - timedelta(days=180)).date()
        else:  # annee
            date_debut = (maintenant - timedelta(days=365)).date()

        # Requête selon granularité
        if granularite == 'jour':
            group_by = 'date'
            periode_col = 'date'
        elif granularite == 'semaine':
            group_by = "strftime('%Y-W%W', date)"
            periode_col = f"{group_by} as periode, MIN(date) as date"
        else:  # mois
            group_by = "strftime('%Y-%m', date)"
            periode_col = f"{group_by} as periode, MIN(date) as date"

        cursor.execute(f'''
            SELECT {periode_col},
                   COUNT(*) as nb_trades,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as losses,
                   SUM(CASE WHEN pnl_pct IS NOT NULL AND resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN pnl_pct ELSE 0 END) as gains_total,
                   SUM(CASE WHEN pnl_pct IS NOT NULL AND resultat IN ('STOP', 'LOSS_FORCE') THEN ABS(pnl_pct) ELSE 0 END) as pertes_total,
                   SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl_periode,
                   AVG(CASE WHEN pnl_pct IS NOT NULL AND resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN pnl_pct END) as avg_win,
                   AVG(CASE WHEN pnl_pct IS NOT NULL AND resultat IN ('STOP', 'LOSS_FORCE') THEN pnl_pct END) as avg_loss
            FROM trades_recommandes
            WHERE date >= ? AND resultat IS NOT NULL
            GROUP BY {group_by}
            ORDER BY date ASC
        ''', (date_debut,))

        # Construire la courbe avec métriques cumulées
        curve_data = []
        equity = 0
        peak = 0
        max_drawdown = 0
        max_drawdown_pct = 0
        total_wins = 0
        total_losses = 0
        total_gains = 0
        total_pertes = 0
        consecutive_wins = 0
        consecutive_losses = 0
        max_consecutive_wins = 0
        max_consecutive_losses = 0
        last_was_win = None

        for row in cursor.fetchall():
            data = dict(row)
            pnl = data['pnl_periode'] or 0
            equity += pnl
            total_wins += data['wins'] or 0
            total_losses += data['losses'] or 0
            total_gains += data['gains_total'] or 0
            total_pertes += data['pertes_total'] or 0

            # Peak et drawdown
            if equity > peak:
                peak = equity
            drawdown = peak - equity
            if drawdown > max_drawdown:
                max_drawdown = drawdown
                max_drawdown_pct = (drawdown / peak * 100) if peak > 0 else 0

            # Séries consécutives
            period_was_win = (data['wins'] or 0) > (data['losses'] or 0)
            if last_was_win is not None:
                if period_was_win and last_was_win:
                    consecutive_wins += 1
                    consecutive_losses = 0
                elif not period_was_win and not last_was_win:
                    consecutive_losses += 1
                    consecutive_wins = 0
                else:
                    if period_was_win:
                        consecutive_wins = 1
                        consecutive_losses = 0
                    else:
                        consecutive_losses = 1
                        consecutive_wins = 0
            else:
                consecutive_wins = 1 if period_was_win else 0
                consecutive_losses = 0 if period_was_win else 1

            max_consecutive_wins = max(max_consecutive_wins, consecutive_wins)
            max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)
            last_was_win = period_was_win

            curve_data.append({
                'date': data['date'],
                'periode': data.get('periode', data['date']),
                'nb_trades': data['nb_trades'],
                'wins': data['wins'],
                'losses': data['losses'],
                'pnl_periode': round(pnl, 2),
                'equity': round(equity, 2),
                'peak': round(peak, 2),
                'drawdown': round(drawdown, 2),
                'win_rate': round((data['wins'] / data['nb_trades'] * 100) if data['nb_trades'] > 0 else 0, 1),
                'avg_win': round(data['avg_win'], 2) if data['avg_win'] else 0,
                'avg_loss': round(data['avg_loss'], 2) if data['avg_loss'] else 0
            })

        conn.close()

        # Calcul métriques globales
        total_trades = total_wins + total_losses
        win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0
        profit_factor = (total_gains / total_pertes) if total_pertes > 0 else float('inf') if total_gains > 0 else 0
        avg_win_global = (total_gains / total_wins) if total_wins > 0 else 0
        avg_loss_global = (total_pertes / total_losses) if total_losses > 0 else 0
        expectancy = (win_rate/100 * avg_win_global) - ((1 - win_rate/100) * avg_loss_global)

        # Recovery factor = equity finale / max drawdown
        recovery_factor = (equity / max_drawdown) if max_drawdown > 0 else float('inf') if equity > 0 else 0

        return jsonify({
            'success': True,
            'periode': periode,
            'granularite': granularite,
            'curve': curve_data,
            'metrics': {
                'equity_finale': round(equity, 2),
                'peak': round(peak, 2),
                'max_drawdown': round(max_drawdown, 2),
                'max_drawdown_pct': round(max_drawdown_pct, 2),
                'total_trades': total_trades,
                'total_wins': total_wins,
                'total_losses': total_losses,
                'win_rate': round(win_rate, 2),
                'profit_factor': round(profit_factor, 2) if profit_factor != float('inf') else 'inf',
                'avg_win': round(avg_win_global, 2),
                'avg_loss': round(avg_loss_global, 2),
                'expectancy': round(expectancy, 2),
                'recovery_factor': round(recovery_factor, 2) if recovery_factor != float('inf') else 'inf',
                'max_consecutive_wins': max_consecutive_wins,
                'max_consecutive_losses': max_consecutive_losses
            }
        })
    except Exception as e:
        logger.error(f"Erreur api_equity_curve: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/journal-quotidien/search')
def api_journal_search():
    """Recherche dans le journal quotidien avec filtres avancés"""
    try:
        # Paramètres de recherche
        recherche = request.args.get('q', '')
        date_from = request.args.get('date_from', '')
        date_to = request.args.get('date_to', '')
        symbole = request.args.get('symbole', '')
        categorie = request.args.get('categorie', '')
        page = int(request.args.get('page', 0))
        limit = int(request.args.get('limit', 50))
        limit = min(limit, 200)
        offset = page * limit

        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Construction de la requête
        where_clauses = ['1=1']
        params = []

        if date_from:
            where_clauses.append('date >= ?')
            params.append(date_from)

        if date_to:
            where_clauses.append('date <= ?')
            params.append(date_to)

        if symbole:
            where_clauses.append('symbole = ?')
            params.append(symbole)

        if categorie:
            where_clauses.append('categorie = ?')
            params.append(categorie)

        if recherche:
            where_clauses.append('''(
                nom_actif LIKE ? OR
                symbole LIKE ? OR
                commentaire LIKE ? OR
                opportunites_jour LIKE ? OR
                mouvements_notables LIKE ?
            )''')
            search_term = f'%{recherche}%'
            params.extend([search_term] * 5)

        where_sql = ' AND '.join(where_clauses)

        # Compter le total
        count_query = f'SELECT COUNT(*) FROM journal_quotidien WHERE {where_sql}'
        cursor.execute(count_query, params)
        total_count = cursor.fetchone()[0]

        # Récupérer les entrées paginées
        query = f'''
            SELECT * FROM journal_quotidien
            WHERE {where_sql}
            ORDER BY date DESC, nom_actif ASC
            LIMIT ? OFFSET ?
        '''
        params.extend([limit, offset])
        cursor.execute(query, params)

        entries = []
        for row in cursor.fetchall():
            entry = dict(row)
            # Parser JSON avec logging des erreurs
            for json_field in ['opportunites_jour', 'recommandations_analystes']:
                if entry.get(json_field):
                    try:
                        entry[json_field] = json.loads(entry[json_field])
                    except json.JSONDecodeError as e:
                        print(f"⚠️ JSON PARSE ERROR - journal_quotidien.{json_field} @ {entry.get('date')}/{entry.get('symbole')}: {e}")
                        entry[json_field] = []
                        entry[f'{json_field}_error'] = True
            entries.append(entry)

        conn.close()

        total_pages = (total_count + limit - 1) // limit

        return jsonify({
            'success': True,
            'entries': entries,
            'pagination': {
                'page': page,
                'limit': limit,
                'total_count': total_count,
                'total_pages': total_pages,
                'has_next': page < total_pages - 1,
                'has_prev': page > 0
            },
            'filters': {
                'recherche': recherche,
                'date_from': date_from,
                'date_to': date_to,
                'symbole': symbole,
                'categorie': categorie
            }
        })
    except Exception as e:
        logger.error(f"Erreur api_journal_search: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/journal-stats')
def api_journal_stats():
    """Statistiques hebdomadaires et mensuelles du journal"""
    try:
        granularite = request.args.get('granularite', 'semaine')  # semaine, mois
        limite = int(request.args.get('limite', 12))

        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        if granularite == 'semaine':
            periode_sql = "strftime('%Y-W%W', date)"
        else:  # mois
            periode_sql = "strftime('%Y-%m', date)"

        # Stats des trades par période
        cursor.execute(f'''
            SELECT {periode_sql} as periode,
                   MIN(date) as date_debut,
                   MAX(date) as date_fin,
                   COUNT(*) as nb_trades,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as losses,
                   SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl_total,
                   AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as pnl_moyen,
                   AVG(duree_minutes) as duree_moyenne,
                   COUNT(DISTINCT symbole) as nb_actifs_trades,
                   SUM(CASE WHEN conviction_score >= 4 THEN 1 ELSE 0 END) as trades_haute_conviction,
                   AVG(conviction_score) as conviction_moyenne
            FROM trades_recommandes
            WHERE resultat IS NOT NULL
            GROUP BY {periode_sql}
            ORDER BY periode DESC
            LIMIT ?
        ''', (limite,))

        stats_periodes = []
        for row in cursor.fetchall():
            data = dict(row)
            conclus = (data['wins'] or 0) + (data['losses'] or 0)
            data['win_rate'] = round((data['wins'] / conclus * 100) if conclus > 0 else 0, 1)
            data['pnl_total'] = round(data['pnl_total'] or 0, 2)
            data['pnl_moyen'] = round(data['pnl_moyen'] or 0, 2)
            data['duree_moyenne'] = round(data['duree_moyenne'] or 0, 0)
            data['conviction_moyenne'] = round(data['conviction_moyenne'] or 0, 1)
            stats_periodes.append(data)

        # Stats par catégorie d'actif par période
        cursor.execute(f'''
            SELECT {periode_sql} as periode,
                   categorie_actif,
                   COUNT(*) as nb_trades,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl_total
            FROM trades_recommandes
            WHERE resultat IS NOT NULL AND categorie_actif IS NOT NULL
            GROUP BY {periode_sql}, categorie_actif
            ORDER BY periode DESC, nb_trades DESC
            LIMIT ?
        ''', (limite * 10,))

        stats_categories = {}
        for row in cursor.fetchall():
            data = dict(row)
            periode = data['periode']
            if periode not in stats_categories:
                stats_categories[periode] = []
            stats_categories[periode].append({
                'categorie': data['categorie_actif'],
                'nb_trades': data['nb_trades'],
                'wins': data['wins'],
                'pnl_total': round(data['pnl_total'] or 0, 2)
            })

        # Meilleurs et pires actifs par période
        cursor.execute(f'''
            SELECT {periode_sql} as periode,
                   symbole,
                   actif,
                   COUNT(*) as nb_trades,
                   SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl_total
            FROM trades_recommandes
            WHERE resultat IS NOT NULL
            GROUP BY {periode_sql}, symbole
            HAVING nb_trades >= 2
            ORDER BY periode DESC, pnl_total DESC
        ''')

        top_bottom_actifs = {}
        for row in cursor.fetchall():
            data = dict(row)
            periode = data['periode']
            if periode not in top_bottom_actifs:
                top_bottom_actifs[periode] = {'top': [], 'bottom': []}

            item = {
                'symbole': data['symbole'],
                'actif': data['actif'],
                'nb_trades': data['nb_trades'],
                'pnl_total': round(data['pnl_total'] or 0, 2)
            }

            # Garder top 3 et bottom 3
            if len(top_bottom_actifs[periode]['top']) < 3:
                top_bottom_actifs[periode]['top'].append(item)
            elif item['pnl_total'] < 0 and len(top_bottom_actifs[periode]['bottom']) < 3:
                top_bottom_actifs[periode]['bottom'].insert(0, item)

        # Réorganiser bottom (les pires en premier)
        for periode in top_bottom_actifs:
            top_bottom_actifs[periode]['bottom'] = sorted(
                top_bottom_actifs[periode]['bottom'],
                key=lambda x: x['pnl_total']
            )[:3]

        # Stats par Trade Grade (A/B/C/D)
        cursor.execute('''
            SELECT trade_grade,
                   COUNT(*) as nb_trades,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE', 'BREAKEVEN') THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as losses,
                   SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl_total,
                   AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as pnl_moyen
            FROM trades_recommandes
            WHERE resultat IS NOT NULL AND trade_grade IS NOT NULL
            GROUP BY trade_grade
            ORDER BY trade_grade
        ''')

        stats_par_grade = {}
        for row in cursor.fetchall():
            data = dict(row)
            grade = data['trade_grade']
            conclus = (data['wins'] or 0) + (data['losses'] or 0)
            stats_par_grade[grade] = {
                'nb_trades': data['nb_trades'],
                'wins': data['wins'] or 0,
                'losses': data['losses'] or 0,
                'win_rate': round((data['wins'] / conclus * 100) if conclus > 0 else 0, 1),
                'pnl_total': round(data['pnl_total'] or 0, 2),
                'pnl_moyen': round(data['pnl_moyen'] or 0, 2)
            }

        conn.close()

        # Inverser pour ordre chronologique
        stats_periodes.reverse()

        return jsonify({
            'success': True,
            'granularite': granularite,
            'stats_periodes': stats_periodes,
            'stats_categories': stats_categories,
            'top_bottom_actifs': top_bottom_actifs,
            'stats_par_grade': stats_par_grade
        })
    except Exception as e:
        logger.error(f"Erreur api_journal_stats: {e}")
        return jsonify({'success': False, 'error': str(e)})

# ============================================================================
# WHATSAPP (optionnel)
# ============================================================================

def envoyer_whatsapp(message):
    """Envoie un message WhatsApp"""
    if not client_twilio:
        print("⚠️ Twilio non configuré")
        return False

    twilio_from = os.environ.get("TWILIO_WHATSAPP_FROM")
    twilio_to = os.environ.get("TWILIO_WHATSAPP_TO")

    if not twilio_from or not twilio_to:
        print("⚠️ TWILIO_WHATSAPP_FROM ou TWILIO_WHATSAPP_TO non configuré")
        return False

    try:
        client_twilio.messages.create(
            from_=twilio_from,
            body=message[:1500],
            to=twilio_to
        )
        return True
    except Exception as e:
        logger.error(f"Erreur WhatsApp: {e}")
        return False

# ============================================================================
# TÂCHES PLANIFIÉES
# ============================================================================

def executer_analyse_planifiee():
    """Exécute une analyse planifiée"""
    maintenant = get_paris_time()

    # Vérifier si c'est un jour de trading valide
    is_valide, raison = est_jour_trading_valide(maintenant)
    if not is_valide:
        print(f"[{maintenant.strftime('%H:%M:%S')} CET] ⏭️ Analyse ignorée: {raison}")
        return

    print(f"\n[{maintenant.strftime('%H:%M:%S')} CET] 🚀 Analyse planifiée...")

    try:
        donnees = recuperer_donnees_marche(ACTIFS_PERMANENTS)
        donnees_enrichies = enrichir_donnees_avec_indicateurs(donnees)
        analyse = analyser_marche_json(donnees)

        if analyse:
            marche_type, _ = get_market_context()
            sauvegarder_analyse('planifiee', analyse, marche_type)

            # Enregistrer les opportunités avec toutes les données (enrichies)
            indicateurs = donnees_enrichies.get('indicateurs_calcules', {})
            for opp in analyse.get('opportunites', []):
                opp_enrichie = enrichir_opportunite_avec_donnees_marche(opp, donnees, indicateurs)
                enregistrer_recommandation(opp_enrichie)

            print(f"[{maintenant.strftime('%H:%M:%S')} CET] ✅ Analyse sauvegardée")
    except Exception as e:
        print(f"[{maintenant.strftime('%H:%M:%S')} CET] ❌ Erreur: {e}")

def executer_cloture_planifiee():
    """Exécute la clôture planifiée"""
    maintenant = get_paris_time()
    is_valide, raison = est_jour_trading_valide(maintenant)
    if not is_valide:
        return

    print(f"\n[{maintenant.strftime('%H:%M:%S')} CET] 🌙 Clôture planifiée...")

    try:
        donnees = recuperer_donnees_marche(ACTIFS_PERMANENTS)
        cloture = generer_cloture_json(donnees)

        if cloture:
            sauvegarder_analyse('cloture', cloture)
            print(f"[{maintenant.strftime('%H:%M:%S')} CET] ✅ Clôture sauvegardée")
    except Exception as e:
        print(f"[{maintenant.strftime('%H:%M:%S')} CET] ❌ Erreur: {e}")

def executer_journal_fr():
    """Exécute l'enregistrement du journal FR à 18h00"""
    maintenant = get_paris_time()
    is_valide, _ = est_jour_trading_valide(maintenant)
    if not is_valide:
        return
    enregistrer_journal_fr()

def executer_journal_complet():
    """Exécute l'enregistrement du journal complet + bilan à 22h30"""
    maintenant = get_paris_time()
    is_valide, _ = est_jour_trading_valide(maintenant)
    if not is_valide:
        return
    enregistrer_journal_complet()

def executer_verification_trades():
    """Exécute la vérification des trades"""
    maintenant = get_paris_time()
    is_valide, _ = est_jour_trading_valide(maintenant)
    if not is_valide:
        return
    verifier_resultats_trades()

def executer_reevaluation_intraday():
    """Exécute la réévaluation intraday des trades en cours"""
    maintenant = get_paris_time()
    is_valide, _ = est_jour_trading_valide(maintenant)
    if not is_valide:
        return
    reevaluer_trades_intraday()

def executer_cloture_trades():
    """Exécute la clôture des trades du jour"""
    maintenant = get_paris_time()
    is_valide, _ = est_jour_trading_valide(maintenant)
    if not is_valide:
        return
    # D'abord vérifier une dernière fois
    verifier_resultats_trades()
    # Puis clôturer les trades restants
    cloturer_trades_jour()

def executer_rapport_hebdo():
    """Exécute la génération du rapport hebdomadaire (samedi matin)"""
    maintenant = get_paris_time()
    if maintenant.weekday() != 5:  # 5 = Samedi
        return
    generer_rapport_hebdo()

def configurer_schedule():
    """Configure les tâches planifiées"""
    # Analyses: 8h, 14h30, 17h
    for heure in ["08:00", "14:30", "17:00"]:
        heure_utc = get_utc_time_for_paris(heure)
        for jour in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']:
            getattr(schedule.every(), jour).at(heure_utc).do(executer_analyse_planifiee)

    # Vérification trades: toutes les 30 minutes entre 9h et 22h
    for heure in ["09:30", "10:00", "10:30", "11:00", "11:30", "12:00",
                  "14:00", "14:30", "15:00", "15:30", "16:00", "16:30",
                  "17:00", "17:30", "18:00", "19:00", "20:00", "21:00"]:
        heure_utc = get_utc_time_for_paris(heure)
        for jour in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']:
            getattr(schedule.every(), jour).at(heure_utc).do(executer_verification_trades)

    # Réévaluation intraday: toutes les heures pendant les sessions actives
    # (plus fréquent pendant US_OPEN car plus de volatilité)
    for heure in ["09:15", "10:15", "11:15", "14:15", "15:45", "16:30", "17:15"]:
        heure_utc = get_utc_time_for_paris(heure)
        for jour in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']:
            getattr(schedule.every(), jour).at(heure_utc).do(executer_reevaluation_intraday)

    # Clôture trades EU: 17h45 (après clôture EU)
    heure_cloture_eu_utc = get_utc_time_for_paris("17:45")
    for jour in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']:
        getattr(schedule.every(), jour).at(heure_cloture_eu_utc).do(executer_verification_trades)

    # Clôture: 22h
    heure_cloture_utc = get_utc_time_for_paris("22:00")
    for jour in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']:
        getattr(schedule.every(), jour).at(heure_cloture_utc).do(executer_cloture_planifiee)

    # Clôture forcée trades EU: 17:15 (15min avant fermeture EU 17:30)
    heure_cloture_eu_trades_utc = get_utc_time_for_paris("17:15")
    for jour in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']:
        getattr(schedule.every(), jour).at(heure_cloture_eu_trades_utc).do(executer_cloture_trades)

    # Clôture forcée trades US: 21:45 (15min avant fermeture US 22:00)
    heure_cloture_us_trades_utc = get_utc_time_for_paris("21:45")
    for jour in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']:
        getattr(schedule.every(), jour).at(heure_cloture_us_trades_utc).do(executer_cloture_trades)

    # Journal FR: 18h00 (après clôture marchés EU)
    heure_journal_fr_utc = get_utc_time_for_paris("18:00")
    for jour in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']:
        getattr(schedule.every(), jour).at(heure_journal_fr_utc).do(executer_journal_fr)

    # Journal complet + Bilan: 22h30 (après clôture US)
    heure_journal_complet_utc = get_utc_time_for_paris("22:30")
    for jour in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']:
        getattr(schedule.every(), jour).at(heure_journal_complet_utc).do(executer_journal_complet)

    # Rapport hebdomadaire: Samedi 09h00 (après clôture complète US vendredi soir)
    heure_rapport_hebdo_utc = get_utc_time_for_paris("09:00")
    schedule.every().saturday.at(heure_rapport_hebdo_utc).do(executer_rapport_hebdo)

    # NOTE: Expiration des ajustements DÉSACTIVÉE - on garde tout l'historique
    # Les ajustements sont conservés pour analyse dans les rapports hebdo

    # Évaluation feedback des ajustements validés: dimanche 19h00
    heure_feedback_utc = get_utc_time_for_paris("19:00")
    schedule.every().sunday.at(heure_feedback_utc).do(evaluer_tous_ajustements_valides)

    # Traitement automatique des ajustements en attente: tous les jours à 08h00
    heure_auto_ajust_utc = get_utc_time_for_paris("08:00")
    schedule.every().day.at(heure_auto_ajust_utc).do(traiter_ajustements_automatiquement)

    # Évaluation des tests A/B: dimanche 18h00
    heure_ab_eval_utc = get_utc_time_for_paris("18:00")
    schedule.every().sunday.at(heure_ab_eval_utc).do(evaluer_tous_ab_tests)

    # Backup quotidien de la base de données: tous les jours à 23h00
    heure_backup_utc = get_utc_time_for_paris("23:00")
    schedule.every().day.at(heure_backup_utc).do(backup_database)
    print(f"  ⏰ Backup DB: 23h00 CET ({heure_backup_utc} UTC)")

def run_scheduler():
    """Thread robuste pour le scheduler avec gestion d'erreurs"""
    consecutive_errors = 0
    max_consecutive_errors = 3

    while True:
        try:
            schedule.run_pending()
            consecutive_errors = 0  # Reset si succès
        except Exception as e:
            consecutive_errors += 1
            logger.error(f"Erreur scheduler (#{consecutive_errors}): {e}")

            # Alerte après plusieurs erreurs consécutives
            if consecutive_errors >= max_consecutive_errors:
                print(f"🚨 ALERTE CRITIQUE: Scheduler a échoué {consecutive_errors} fois!")
                # Envoyer notification WhatsApp si configuré
                try:
                    twilio_to = os.environ.get('TWILIO_WHATSAPP_TO')
                    twilio_from = os.environ.get('TWILIO_WHATSAPP_FROM')
                    if client_twilio and twilio_to and twilio_from:
                        client_twilio.messages.create(
                            body=f"🚨 ALERTE TRADING: Scheduler en erreur ({consecutive_errors}x): {str(e)[:100]}",
                            from_=twilio_from,
                            to=twilio_to
                        )
                except Exception as twilio_err:
                    print(f"⚠️ Impossible d'envoyer l'alerte: {twilio_err}")

                # Cooldown prolongé après alertes
                time.sleep(120)
                consecutive_errors = 0  # Reset après cooldown
                continue

            # Petit délai avant retry en cas d'erreur
            time.sleep(60)
            continue

        time.sleep(30)

# ============================================================================
# DÉMARRAGE
# ============================================================================

def start_app():
    """Démarre l'application"""
    maintenant = get_paris_time()
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║           AGENT TRADING - INTERFACE WEB                       ║
╠══════════════════════════════════════════════════════════════╣
║  📅 {maintenant.strftime('%d/%m/%Y %H:%M:%S')} CET                                  ║
║  🌐 URL: http://localhost:5000                                ║
║                                                               ║
║  ⏰ Analyses AUTO: 8h, 14h30, 17h CET                         ║
║  🌙 Clôture: 22h CET                                          ║
║                                                               ║
║  📊 Fonctionnalités:                                          ║
║     - Dashboard temps réel                                    ║
║     - Aide au trading                                         ║
║     - Journal des actifs                                      ║
║     - Suivi des performances                                  ║
╚══════════════════════════════════════════════════════════════╝
    """)

    app.run(host='0.0.0.0', port=5000, debug=False)

# ============================================================================
# PRÉ-CHARGEMENT CACHE AU DÉMARRAGE
# ============================================================================

def precharger_donnees_marche():
    """Pré-charge les données de marché au démarrage pour un dashboard instantané.
    Exécuté en background pour ne pas bloquer le démarrage du serveur."""
    try:
        print("🚀 Pré-chargement des données de marché en cours...")
        start_time = time.time()

        # Charger tous les actifs permanents via batch API
        donnees = recuperer_donnees_marche(ACTIFS_PERMANENTS, inclure_indicateurs=True)

        elapsed = time.time() - start_time
        print(f"✅ Pré-chargement terminé: {len(donnees)} actifs en {elapsed:.1f}s")

    except Exception as e:
        print(f"⚠️ Erreur pré-chargement: {e}")

# Initialisation au niveau module (requis pour Replit)
init_database()
init_ab_test_table()
charger_ab_tests_actifs()
load_vix_cache()  # Charger le cache VIX persistant
configurer_schedule()
scheduler_thread = Thread(target=run_scheduler, daemon=True)
scheduler_thread.start()

# Pré-charger les données en background (dashboard instantané)
preload_thread = Thread(target=precharger_donnees_marche, daemon=True)
preload_thread.start()

if __name__ == "__main__":
    start_app()
