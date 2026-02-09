"""
Récupération des données de marché: TwelveData, Yahoo Finance, batch API.
"""
import time
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import requests
import yfinance as yf

from .config import (
    TWELVEDATA_API_KEY, TWELVEDATA_CACHE, TWELVEDATA_CACHE_TTL,
    TWELVEDATA_CACHE_MAX_SIZE, TWELVEDATA_CACHE_LOCK, TWELVEDATA_RATE_LOCK,
    TWELVEDATA_FETCH_LOCK, TWELVEDATA_MIN_INTERVAL, TWELVEDATA_QUOTA_COOLDOWN,
    MARKET_DATA_CACHE, MARKET_DATA_CACHE_TTL,
    logger, DB_PATH, DB_TIMEOUT
)
from . import config
from .constants import SYMBOLES_YAHOO_FALLBACK, SYMBOL_MAPPING_TWELVEDATA, ACTIFS_PERMANENTS
from .market_context import get_paris_time

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
    if not config.TWELVEDATA_QUOTA_EXCEEDED:
        return False

    # Vérifier si le cooldown est passé
    if config.TWELVEDATA_QUOTA_RESET_TIME and time.time() > config.TWELVEDATA_QUOTA_RESET_TIME:
        config.TWELVEDATA_QUOTA_EXCEEDED = False
        config.TWELVEDATA_QUOTA_RESET_TIME = None
        print("🔄 Circuit breaker quota réinitialisé")
        return False

    return True

def activate_quota_circuit_breaker():
    """Active le circuit breaker après un quota exceeded"""
    config.TWELVEDATA_QUOTA_EXCEEDED = True
    config.TWELVEDATA_QUOTA_RESET_TIME = time.time() + TWELVEDATA_QUOTA_COOLDOWN
    print(f"🛑 Circuit breaker QUOTA activé - pause {TWELVEDATA_QUOTA_COOLDOWN}s")

def rate_limit_twelvedata():
    """Applique un rate limiting thread-safe sur les appels Twelve Data"""
    with TWELVEDATA_RATE_LOCK:
        if config.TWELVEDATA_LAST_CALL is not None:
            elapsed = time.time() - config.TWELVEDATA_LAST_CALL
            if elapsed < TWELVEDATA_MIN_INTERVAL:
                time.sleep(TWELVEDATA_MIN_INTERVAL - elapsed)
        config.TWELVEDATA_LAST_CALL = time.time()

def cleanup_twelvedata_cache():
    """Nettoie le cache si trop volumineux (bug #8)"""

    with TWELVEDATA_CACHE_LOCK:
        if len(TWELVEDATA_CACHE) > TWELVEDATA_CACHE_MAX_SIZE:
            # Garder les 400 entrées les plus récentes
            sorted_items = sorted(
                TWELVEDATA_CACHE.items(),
                key=lambda x: x[1]['timestamp'],
                reverse=True
            )
            kept = dict(sorted_items[:400])
            TWELVEDATA_CACHE.clear()
            TWELVEDATA_CACHE.update(kept)
            print(f"🧹 Cache nettoyé: {len(sorted_items)} → 400 entrées")

def get_yahoo_finance_data(symbole, outputsize=30):
    """Fallback Yahoo Finance pour les symboles non supportés par Twelve Data (indices, futures)"""

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
        if not response.ok:
            if response.status_code == 429:
                activate_quota_circuit_breaker()
            logger.warning(f"Twelve Data {symbole}: HTTP {response.status_code}")
            return pd.DataFrame(), False
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
        if not response.ok:
            if response.status_code == 429:
                activate_quota_circuit_breaker()
            return None
        data = response.json()

        if "close" not in data:
            error_msg = data.get("message", "")
            if "quota" in error_msg.lower() or "limit" in error_msg.lower():
                activate_quota_circuit_breaker()
            return None

        result = {
            "price": float(data.get("close") or 0),
            "open": float(data.get("open") or 0),
            "high": float(data.get("high") or 0),
            "low": float(data.get("low") or 0),
            "previous_close": float(data.get("previous_close") or 0),
            "change": float(data.get("change") or 0),
            "percent_change": float(data.get("percent_change") or 0),
            "volume": int(data.get("volume") or 0)
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
        if not response.ok:
            if response.status_code == 429:
                activate_quota_circuit_breaker()
            print(f"⚠️ Fresh quote {symbole}: HTTP {response.status_code}")
            return None
        data = response.json()

        if "close" not in data:
            error_msg = data.get("message", "")
            if "quota" in error_msg.lower():
                activate_quota_circuit_breaker()
            print(f"⚠️ Fresh quote {symbole}: {error_msg}")
            return None

        price = float(data.get("close") or 0)
        high = float(data.get("high") or 0)
        low = float(data.get("low") or 0)
        open_price = float(data.get("open") or 0)
        prev_close = float(data.get("previous_close") or 0)
        volume = int(data.get("volume") or 0)

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
            if not response.ok:
                if response.status_code == 429:
                    activate_quota_circuit_breaker()
                logger.warning(f"Twelve Data batch: HTTP {response.status_code}")
                continue
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


def recuperer_donnees_marche(actifs, inclure_indicateurs=True):
    """Récupère les données de marché pour les actifs donnés avec ATR et Volume relatif.

    OPTIMISÉ: Utilise BATCH API pour charger tous les symboles en quelques appels
    au lieu d'un appel par symbole. Réduit le temps de chargement de ~45s à ~7s.

    Cache haut-niveau pour éviter le stampede quand plusieurs endpoints appellent simultanément."""


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

    # Log des actifs manquants (pulse debug)
    actifs_manquants = [nom for nom in actifs.values() if nom not in donnees]
    if actifs_manquants:
        print(f"⚠️ Actifs sans données: {', '.join(actifs_manquants)}")

    # Sauvegarder dans le cache haut-niveau
    with TWELVEDATA_FETCH_LOCK:
        MARKET_DATA_CACHE['data'] = donnees
        MARKET_DATA_CACHE['timestamp'] = time.time()
        MARKET_DATA_CACHE['actifs_key'] = hash(frozenset(actifs.keys()))

    return donnees


