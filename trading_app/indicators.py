"""
Indicateurs techniques: RSI, MACD, ATR, tendances, enrichissement données.
"""
import numpy as np
import pandas as pd

from .config import logger
from .market_data import get_twelvedata_time_series, get_twelvedata_intraday

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


