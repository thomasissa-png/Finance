"""
Agent Trading - Interface Web Complète
Application Flask avec interface dynamique pour le trading
"""

import os
import json
import re
import sqlite3
from datetime import datetime, timedelta
from threading import Thread
import time

import numpy as np
import pandas as pd
import pytz
import schedule
import yfinance as yf
from anthropic import Anthropic
from flask import Flask, render_template, jsonify, request
from twilio.rest import Client
import requests

# ============================================================================
# CONFIGURATION
# ============================================================================

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'trading-secret-key-2024')

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
NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY")

# Rate limiting pour yfinance
YFINANCE_LAST_CALL = None
YFINANCE_MIN_INTERVAL = 0.5  # Minimum 0.5 seconde entre les appels
YFINANCE_CACHE = {}  # Cache simple {symbole: {'data': ..., 'timestamp': ...}}
YFINANCE_CACHE_TTL = 60  # Cache valide 60 secondes

# Timezone
TZ_PARIS = pytz.timezone('Europe/Paris')

# ============================================================================
# ACTIFS SUIVIS
# ============================================================================

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
    "luxe": {"KER.PA": "Kering", "CFR.SW": "Richemont", "ML.PA": "Moncler", "BOSS.DE": "Hugo Boss"},
    "banques_eu": {"GLE.PA": "Société Générale", "ACA.PA": "Crédit Agricole", "UCG.MI": "Unicredit", "SAN.MC": "Santander", "INGA.AS": "ING", "DBK.DE": "Deutsche Bank"},
    "banques_us": {"BAC": "Bank of America", "C": "Citigroup", "GS": "Goldman Sachs", "WFC": "Wells Fargo", "MS": "Morgan Stanley"},
    "energie": {"CVX": "Chevron", "SHEL": "Shell", "BP": "BP", "ENGI.PA": "Engie", "CL=F": "Pétrole WTI"},
    "pharma": {"PFE": "Pfizer", "MRNA": "Moderna", "AZN": "AstraZeneca", "NVS": "Novartis"},
    "aero_defense": {"BA": "Boeing", "LMT": "Lockheed Martin", "NOC": "Northrop Grumman", "RTX": "Raytheon", "AM.PA": "Dassault Aviation", "HO.PA": "Thales"},
    "semi_conducteurs": {"ASML.AS": "ASML", "TSM": "TSMC", "AVGO": "Broadcom", "MU": "Micron"}
}

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

    # Ajouter colonnes à trades_recommandes si manquantes (ignore si déjà existantes)
    colonnes_trades = [
        ("direction", "TEXT DEFAULT 'LONG'"),
        ("categorie_actif", "TEXT"),
        ("duree_minutes", "INTEGER"),
        ("heure_entree", "INTEGER")
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

# ============================================================================
# FONCTIONS UTILITAIRES
# ============================================================================

def get_paris_time():
    """Retourne l'heure actuelle à Paris"""
    return datetime.now(TZ_PARIS)

def rate_limit_yfinance():
    """Applique un rate limiting sur les appels yfinance"""
    global YFINANCE_LAST_CALL
    if YFINANCE_LAST_CALL is not None:
        elapsed = time.time() - YFINANCE_LAST_CALL
        if elapsed < YFINANCE_MIN_INTERVAL:
            time.sleep(YFINANCE_MIN_INTERVAL - elapsed)
    YFINANCE_LAST_CALL = time.time()

def get_cached_yfinance(symbole, period="1mo"):
    """Récupère les données yfinance avec cache et rate limiting"""
    global YFINANCE_CACHE
    cache_key = f"{symbole}_{period}"
    now = time.time()

    # Vérifier le cache
    if cache_key in YFINANCE_CACHE:
        cached = YFINANCE_CACHE[cache_key]
        if now - cached['timestamp'] < YFINANCE_CACHE_TTL:
            return cached['data'], cached['is_fresh']

    # Rate limiting
    rate_limit_yfinance()

    try:
        ticker = yf.Ticker(symbole)
        info = ticker.history(period=period)

        # Vérifier fraîcheur des données
        is_fresh = False
        if not info.empty:
            last_date = info.index[-1]
            # Convertir en datetime aware si nécessaire
            if last_date.tzinfo is None:
                last_date = last_date.tz_localize('UTC')
            last_date_paris = last_date.tz_convert(TZ_PARIS).date()
            today = get_paris_time().date()
            weekday = today.weekday()

            # Données fraîches si:
            # - Jour de semaine: données d'aujourd'hui ou hier (si marché pas encore ouvert)
            # - Weekend: données de vendredi
            if weekday < 5:  # Lundi-Vendredi
                days_diff = (today - last_date_paris).days
                is_fresh = days_diff <= 1
            else:  # Weekend
                # Vendredi = today - (weekday - 4) jours
                vendredi = today - timedelta(days=weekday - 4)
                is_fresh = last_date_paris >= vendredi

        # Mettre en cache
        YFINANCE_CACHE[cache_key] = {
            'data': info,
            'timestamp': now,
            'is_fresh': is_fresh
        }

        return info, is_fresh

    except Exception as e:
        print(f"⚠️ Erreur yfinance {symbole}: {e}")
        return pd.DataFrame(), False

def get_utc_time_for_paris(heure_paris):
    """Convertit une heure française en heure UTC"""
    maintenant = get_paris_time()
    heure_cible = maintenant.replace(
        hour=int(heure_paris.split(':')[0]),
        minute=int(heure_paris.split(':')[1]),
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

def get_evenements_macro_jour(pour_lundi=False):
    """Récupère les événements macro du jour (basé sur le calendrier économique)
    Si pour_lundi=True et weekend, simule le lundi suivant (pour affichage weekend)"""
    maintenant = get_paris_time()

    # Si weekend, on affiche les événements du lundi suivant
    if is_weekend():
        # Samedi (5) → +2 jours, Dimanche (6) → +1 jour
        jours_jusqua_lundi = (7 - maintenant.weekday()) % 7
        if jours_jusqua_lundi == 0:
            jours_jusqua_lundi = 1  # Dimanche: +1 jour vers lundi
        maintenant = maintenant + timedelta(days=jours_jusqua_lundi)

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

    # Vérifier CPI US (généralement entre le 10 et 15 du mois)
    if 10 <= jour_mois <= 15 and jour_semaine < 5:
        # CPI US souvent publié ces jours
        evenements.append({
            "nom": "CPI US (potentiel)",
            "heure": "14:30",
            "importance": 2,
            "impact": "FORT - Volatilité sur USD, Or, Indices"
        })

    # Événements fixes récurrents
    heures_fixes = [
        {"heure": "16:00", "nom": "ISM/PMI Services (si 1er jour ouvré)", "importance": 2},
        {"heure": "16:30", "nom": "Stocks pétrole EIA", "importance": 2, "jour": 2},  # Mercredi
    ]

    for evt in heures_fixes:
        if evt.get("jour") is None or evt.get("jour") == jour_semaine:
            if evt.get("importance", 1) >= 2:
                evenements.append({
                    "nom": evt["nom"],
                    "heure": evt["heure"],
                    "importance": evt.get("importance", 2),
                    "impact": "Volatilité modérée à forte"
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

def recuperer_donnees_marche(actifs, inclure_indicateurs=True):
    """Récupère les données de marché pour les actifs donnés avec ATR et Volume relatif
    Utilise le cache et rate limiting pour éviter de surcharger yfinance"""
    donnees = {}
    donnees_non_fraiches = []

    for symbole, nom in actifs.items():
        try:
            # Utiliser le cache avec rate limiting
            info, is_fresh = get_cached_yfinance(symbole, period="1mo")

            if not info.empty:
                prix_actuel = info['Close'].iloc[-1]
                prix_ouverture = info['Open'].iloc[-1]
                prix_max = info['High'].iloc[-1]
                prix_min = info['Low'].iloc[-1]

                # Variation: clôture précédente vers clôture actuelle (standard du marché)
                if len(info) >= 2:
                    cloture_precedente = info['Close'].iloc[-2]
                    variation = ((prix_actuel - cloture_precedente) / cloture_precedente) * 100
                else:
                    variation = 0

                # Variation sur 5 jours
                if len(info) >= 5:
                    prix_5j = info['Close'].iloc[-5]
                    var_5j = ((prix_actuel - prix_5j) / prix_5j) * 100
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
                    atr_pct = (atr / prix_actuel) * 100

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

                donnees[nom] = {
                    "symbole": symbole,
                    "prix": round(prix_actuel, 2),
                    "ouverture": round(prix_ouverture, 2),
                    "haut": round(prix_max, 2),
                    "bas": round(prix_min, 2),
                    "variation": round(variation, 2),
                    "variation_5j": round(var_5j, 2),
                    "atr": round(atr, 4) if atr else 0,
                    "atr_pct": round(atr_pct, 2) if atr_pct else 0,
                    "volume_relatif": round(volume_relatif, 1),
                    "data_date": last_date_str,
                    "is_fresh": is_fresh
                }

                if not is_fresh:
                    donnees_non_fraiches.append(nom)
        except Exception as e:
            print(f"⚠️ Erreur {symbole}: {e}")

    if donnees_non_fraiches:
        print(f"⚠️ Données potentiellement obsolètes pour: {', '.join(donnees_non_fraiches[:5])}")

    return donnees

def calculer_indicateurs_techniques(symbole):
    """Calcule les indicateurs techniques pour un actif"""
    try:
        ticker = yf.Ticker(symbole)
        df_intraday = ticker.history(period="1d", interval="5m")
        df_daily = ticker.history(period="30d", interval="1d")

        if df_intraday.empty or df_daily.empty:
            return None

        indicateurs = {}

        # VWAP
        if len(df_intraday) > 0 and 'Volume' in df_intraday.columns:
            df_intraday['TP'] = (df_intraday['High'] + df_intraday['Low'] + df_intraday['Close']) / 3
            df_intraday['TP_Volume'] = df_intraday['TP'] * df_intraday['Volume']
            total_volume = df_intraday['Volume'].sum()
            if total_volume > 0:
                vwap = df_intraday['TP_Volume'].sum() / total_volume
                indicateurs['vwap'] = round(vwap, 2)

        # Pivot Points
        if len(df_daily) >= 2:
            prev_high = df_daily['High'].iloc[-2]
            prev_low = df_daily['Low'].iloc[-2]
            prev_close = df_daily['Close'].iloc[-2]

            pivot = (prev_high + prev_low + prev_close) / 3
            indicateurs['pivot'] = round(pivot, 2)
            indicateurs['r1'] = round(2 * pivot - prev_low, 2)
            indicateurs['r2'] = round(pivot + (prev_high - prev_low), 2)
            indicateurs['s1'] = round(2 * pivot - prev_high, 2)
            indicateurs['s2'] = round(pivot - (prev_high - prev_low), 2)

        # ATR
        if len(df_daily) >= 15:
            high_low = df_daily['High'] - df_daily['Low']
            high_close = np.abs(df_daily['High'] - df_daily['Close'].shift())
            low_close = np.abs(df_daily['Low'] - df_daily['Close'].shift())

            ranges = pd.concat([high_low, high_close, low_close], axis=1)
            true_range = np.max(ranges, axis=1)
            atr = true_range.rolling(14).mean().iloc[-1]

            indicateurs['atr'] = round(atr, 2)
            indicateurs['atr_pct'] = round((atr / df_daily['Close'].iloc[-1]) * 100, 2)

        # Volume relatif
        if len(df_daily) >= 20:
            volume_actuel = df_daily['Volume'].iloc[-1]
            volume_moyen = df_daily['Volume'].iloc[-20:].mean()
            if volume_moyen > 0:
                indicateurs['volume_relatif_pct'] = round((volume_actuel / volume_moyen) * 100, 1)

        # Prix actuel et variation (cohérent avec recuperer_donnees_marche: close-to-close)
        indicateurs['prix_actuel'] = round(df_daily['Close'].iloc[-1], 2)
        if len(df_daily) >= 2:
            cloture_precedente = df_daily['Close'].iloc[-2]
            indicateurs['variation_jour'] = round(
                ((df_daily['Close'].iloc[-1] - cloture_precedente) / cloture_precedente) * 100, 2
            )
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
- Objectif: +0.7% à +1% en quelques minutes à 2-3h max
- Stop serré: -0.5% à -0.8%
- Pas de positions overnight

RÈGLES DE FILTRAGE:
- UNIQUEMENT des actifs dont le marché est OUVERT ou s'ouvre dans 2h
- EXCLUS les actifs avec ATR < 1% (trop peu de volatilité pour du day trading)
- NE RECOMMANDE PAS de trades 30 minutes AVANT un événement macro majeur (NFP, CPI, FOMC, BCE)
- AU MINIMUM 1 opportunité NEWS TRADING dans tes recommandations
- TOUTES les heures en CET (heure française)
- PRÉCISE TOUJOURS si c'est un LONG ou un SHORT

INDICATEURS CLÉS:
- ATR%: Average True Range en % du prix - mesure la volatilité moyenne. ATR < 1% = éviter
- Volume Relatif: Volume actuel vs moyenne 20j. > 150% = intérêt institutionnel

ÉVÉNEMENTS MACRO À SURVEILLER:
- NFP (1er vendredi du mois 14h30): ÉVITER 30min avant, forte volatilité USD
- CPI US (entre 10-15 du mois 14h30): ÉVITER 30min avant
- FOMC (décisions Fed 20h): ÉVITER positions, très forte volatilité
- BCE (décisions 14h15-14h45): ÉVITER positions sur EUR

FORMAT DE RÉPONSE EN JSON:
{
  "contexte_marche": "Paragraphe narratif sur le contexte actuel",
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
      "atr_pct": 0,
      "volume_relatif": 0,
      "catalyseur": "",
      "is_news_trading": true/false,
      "timing": "",
      "entree": 0,
      "stop": 0,
      "tp1": 0,
      "tp2": 0,
      "ratio_rr": "",
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
      "actifs": [
        {"symbole": "KC=F", "nom": "Café", "direction": "LONG", "raison": "Supply shock"}
      ],
      "timing": "immédiat/aujourd'hui/cette semaine",
      "source": "Source originale"
    }
  ]
}

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
        match = re.search(r'\{[\s\S]*\}', reponse)

        if match:
            result = json.loads(match.group())
            return result.get('news_analysees', [])
        return []
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
            # Trouver l'article original pour récupérer l'heure
            for article in articles:
                if article['titre'] in news.get('headline', ''):
                    if article.get('heure'):
                        try:
                            dt = datetime.fromisoformat(article['heure'].replace('Z', '+00:00'))
                            dt_paris = dt.astimezone(TZ_PARIS)
                            news['heure'] = dt_paris.strftime('%H:%M')
                        except ValueError:
                            news['heure'] = '--:--'
                    break
            if 'heure' not in news:
                news['heure'] = '--:--'

        return news_analysees, None

    except Exception as e:
        print(f"⚠️ Erreur fetch_and_analyze_news: {e}")
        return [], str(e)

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

    # Formater les données pour le prompt
    donnees_texte = json.dumps(donnees, ensure_ascii=False, indent=2)

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

    question = f"""DONNÉES MARCHÉ EN TEMPS RÉEL (avec ATR% et Volume Relatif):
{donnees_texte}

Heure: {maintenant.strftime('%d/%m/%Y %H:%M')} CET
Contexte: {marche_type} - {marche_info}
{contexte_macro}
{exclusions_atr}
{contexte_criteres}

Analyse le marché et fournis une réponse JSON structurée selon le format demandé.
- EXCLUS les actifs listés avec ATR < 1%
- Si événement macro imminent, mentionne-le dans alerte_macro
- PRIVILÉGIE les catégories avec score de confiance élevé (>60)
- ÉVITE les catégories avec score faible (<40)
- Assure-toi d'inclure AU MOINS 1 opportunité NEWS TRADING (si conditions favorables)
- Liste UNIQUEMENT les événements APRÈS {heure_str}
- Pour chaque opportunité, inclus le symbole, atr_pct et volume_relatif"""

    try:
        message = client_anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": question}]
        )

        reponse = message.content[0].text

        # Extraire le JSON
        match = re.search(r'\{[\s\S]*\}', reponse)
        if match:
            result = json.loads(match.group())
            # Ajouter l'alerte macro si présente
            if alerte_macro and not result.get('alerte_macro'):
                result['alerte_macro'] = alerte_macro
            return result
        return None
    except Exception as e:
        print(f"❌ Erreur analyse: {e}")
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
        match = re.search(r'\{[\s\S]*\}', reponse)
        if match:
            return json.loads(match.group())
        return None
    except Exception as e:
        print(f"❌ Erreur clôture: {e}")
        return None

# ============================================================================
# GESTION DES TRADES
# ============================================================================

def enregistrer_recommandation(trade_data):
    """Enregistre une recommandation de trade"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        maintenant = get_paris_time()

        # Déterminer la direction et valider la cohérence stop/entry
        entree = float(trade_data.get('entree', 0) or 0)
        stop = float(trade_data.get('stop', 0) or 0)
        tp1 = float(trade_data.get('tp1', 0) or 0)

        if trade_data.get('direction'):
            direction = trade_data.get('direction').upper()
        elif stop > entree and entree > 0:
            direction = 'SHORT'
        else:
            direction = 'LONG'

        # Validation cohérence direction/stop/entry
        if entree > 0 and stop > 0:
            if direction == 'LONG' and stop >= entree:
                print(f"⚠️ Trade LONG incohérent: stop ({stop}) >= entrée ({entree}), corrigé en SHORT")
                direction = 'SHORT'
            elif direction == 'SHORT' and stop <= entree:
                print(f"⚠️ Trade SHORT incohérent: stop ({stop}) <= entrée ({entree}), corrigé en LONG")
                direction = 'LONG'

        # Validation cohérence TP
        if entree > 0 and tp1 > 0:
            if direction == 'LONG' and tp1 < entree:
                print(f"⚠️ Trade LONG: TP1 ({tp1}) < entrée ({entree}), incohérent")
            elif direction == 'SHORT' and tp1 > entree:
                print(f"⚠️ Trade SHORT: TP1 ({tp1}) > entrée ({entree}), incohérent")

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

        cursor.execute('''
            INSERT INTO trades_recommandes
            (date, heure_message, actif, symbole, type_setup, prix_entree, prix_stop,
             prix_tp1, prix_tp2, prix_actuel, catalyseur, duree_estimee, timestamp_reco,
             direction, categorie_actif, heure_entree)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            maintenant.hour  # Heure d'entrée pour stats par heure
        ))

        conn.commit()
        conn.close()
        return cursor.lastrowid
    except Exception as e:
        print(f"⚠️ Erreur enregistrement: {e}")
        return None

def verifier_resultats_trades():
    """Vérifie et met à jour les résultats des trades ouverts (ordre chronologique)"""
    maintenant = get_paris_time()
    print(f"[{maintenant.strftime('%H:%M:%S')} CET] 🔍 Vérification résultats trades...")

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Récupérer les trades sans résultat
        cursor.execute('''
            SELECT * FROM trades_recommandes
            WHERE resultat IS NULL AND symbole IS NOT NULL AND symbole != ''
        ''')
        trades_ouverts = [dict(row) for row in cursor.fetchall()]

        if not trades_ouverts:
            print(f"[{maintenant.strftime('%H:%M:%S')} CET] ℹ️ Aucun trade à vérifier")
            conn.close()
            return 0

        nb_mis_a_jour = 0

        for trade in trades_ouverts:
            try:
                symbole = trade['symbole']
                direction = trade.get('direction', 'LONG')
                entree = float(trade['prix_entree'] or 0)
                stop = float(trade['prix_stop'] or 0)
                tp1 = float(trade['prix_tp1'] or 0)
                tp2 = float(trade['prix_tp2'] or 0)

                if entree == 0:
                    continue

                # Récupérer les données intraday (5min pour avoir l'ordre chronologique)
                ticker = yf.Ticker(symbole)
                hist = ticker.history(period="1d", interval="5m")

                if hist.empty:
                    continue

                resultat = None
                prix_sortie = None
                pnl_pct = None

                # Parcourir chronologiquement chaque bougie
                for idx, row in hist.iterrows():
                    high = row['High']
                    low = row['Low']

                    if direction == 'LONG':
                        # LONG: Stop si prix descend sous stop, TP si prix monte au-dessus
                        # Vérifier le stop d'abord (scénario pessimiste dans une même bougie)
                        if stop > 0 and low <= stop:
                            resultat = 'STOP'
                            prix_sortie = stop
                            pnl_pct = ((stop - entree) / entree) * 100
                            break
                        elif tp2 > 0 and high >= tp2:
                            resultat = 'TP2'
                            prix_sortie = tp2
                            pnl_pct = ((tp2 - entree) / entree) * 100
                            break
                        elif tp1 > 0 and high >= tp1:
                            resultat = 'TP1'
                            prix_sortie = tp1
                            pnl_pct = ((tp1 - entree) / entree) * 100
                            break
                    else:
                        # SHORT: Stop si prix monte au-dessus du stop, TP si prix descend
                        if stop > 0 and high >= stop:
                            resultat = 'STOP'
                            prix_sortie = stop
                            pnl_pct = ((entree - stop) / entree) * 100
                            break
                        elif tp2 > 0 and low <= tp2:
                            resultat = 'TP2'
                            prix_sortie = tp2
                            pnl_pct = ((entree - tp2) / entree) * 100
                            break
                        elif tp1 > 0 and low <= tp1:
                            resultat = 'TP1'
                            prix_sortie = tp1
                            pnl_pct = ((entree - tp1) / entree) * 100
                            break

                # Mettre à jour si résultat trouvé
                if resultat:
                    # Calculer la durée du trade en minutes
                    duree_minutes = None
                    if trade.get('timestamp_reco'):
                        try:
                            from datetime import datetime
                            ts_reco = datetime.fromisoformat(trade['timestamp_reco'].replace('Z', '+00:00'))
                            duree_minutes = int((maintenant.replace(tzinfo=None) - ts_reco.replace(tzinfo=None)).total_seconds() / 60)
                        except ValueError:
                            pass

                    cursor.execute('''
                        UPDATE trades_recommandes
                        SET resultat = ?, prix_sortie = ?, pnl_pct = ?, timestamp_sortie = ?, duree_minutes = ?
                        WHERE id = ?
                    ''', (resultat, prix_sortie, round(pnl_pct, 2), maintenant, duree_minutes, trade['id']))
                    nb_mis_a_jour += 1
                    duree_str = f" ({duree_minutes}min)" if duree_minutes else ""
                    print(f"   ✅ {trade['actif']}: {resultat} ({pnl_pct:+.2f}%){duree_str}")

            except Exception as e:
                print(f"   ⚠️ Erreur trade {trade.get('actif', 'inconnu')}: {e}")
                continue

        conn.commit()
        conn.close()

        print(f"[{maintenant.strftime('%H:%M:%S')} CET] ✅ {nb_mis_a_jour} trade(s) mis à jour")
        return nb_mis_a_jour

    except Exception as e:
        print(f"❌ Erreur vérification trades: {e}")
        return 0

def cloturer_trades_jour():
    """Clôture les trades du jour qui n'ont pas atteint leur objectif"""
    maintenant = get_paris_time()
    aujourdhui = maintenant.date()

    print(f"[{maintenant.strftime('%H:%M:%S')} CET] 🌙 Clôture trades du jour...")

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

        for trade in trades_ouverts:
            try:
                symbole = trade['symbole']
                direction = trade.get('direction', 'LONG')
                entree = float(trade['prix_entree'] or 0)

                if entree == 0:
                    continue

                # Récupérer le prix de clôture
                ticker = yf.Ticker(symbole)
                hist = ticker.history(period="1d")

                if hist.empty:
                    continue

                prix_cloture = hist['Close'].iloc[-1]

                # Calculer le PnL
                if direction == 'LONG':
                    pnl_pct = ((prix_cloture - entree) / entree) * 100
                else:
                    pnl_pct = ((entree - prix_cloture) / entree) * 100

                # Marquer comme NON_CONCLU
                cursor.execute('''
                    UPDATE trades_recommandes
                    SET resultat = 'NON_CONCLU', prix_sortie = ?, pnl_pct = ?, timestamp_sortie = ?
                    WHERE id = ?
                ''', (prix_cloture, round(pnl_pct, 2), maintenant, trade['id']))
                nb_clotures += 1
                print(f"   📊 {trade['actif']}: NON_CONCLU ({pnl_pct:+.2f}%)")

            except Exception as e:
                print(f"   ⚠️ Erreur clôture {trade.get('actif', 'inconnu')}: {e}")
                continue

        conn.commit()
        conn.close()

        print(f"[{maintenant.strftime('%H:%M:%S')} CET] ✅ {nb_clotures} trade(s) clôturé(s)")
        return nb_clotures

    except Exception as e:
        print(f"❌ Erreur clôture trades: {e}")
        return 0

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
        conn = sqlite3.connect(DB_PATH)
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
                SUM(CASE WHEN resultat IN ('TP1', 'TP2') THEN 1 ELSE 0 END) as reussis,
                SUM(CASE WHEN resultat = 'STOP' THEN 1 ELSE 0 END) as stops,
                SUM(CASE WHEN resultat = 'NON_CONCLU' THEN 1 ELSE 0 END) as non_conclus,
                AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as pnl_moyen,
                SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as pnl_total
            FROM trades_recommandes
            WHERE date >= ?
        ''', (date_debut,))

        row = cursor.fetchone()
        conn.close()

        total = row[0] or 0
        reussis = row[1] or 0
        stops = row[2] or 0
        non_conclus = row[3] or 0
        # Conclus = uniquement les trades avec résultat définitif (pas NULL, vide, ou en_cours)
        conclus = reussis + stops

        return {
            'total': total,
            'reussis': reussis,
            'stops': stops,
            'non_conclus': non_conclus,
            'en_cours': total - conclus - non_conclus,  # Trades sans résultat encore
            'taux_reussite': round((reussis / conclus * 100) if conclus > 0 else 0, 1),
            'pnl_moyen': round(row[4] or 0, 2),
            'pnl_total': round(row[5] or 0, 2)
        }
    except Exception as e:
        print(f"⚠️ Erreur performances: {e}")
        return {'total': 0, 'reussis': 0, 'stops': 0, 'non_conclus': 0, 'taux_reussite': 0, 'pnl_moyen': 0, 'pnl_total': 0}

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
    """Récupère les données pré-market (futures, overnight gaps)"""
    premarket_symbols = {
        "ES=F": "S&P 500 Futures",
        "NQ=F": "Nasdaq Futures",
        "YM=F": "Dow Futures",
        "^N225": "Nikkei 225",
        "^HSI": "Hang Seng",
        "^STOXX50E": "Euro Stoxx 50"
    }

    donnees = {}
    for symbole, nom in premarket_symbols.items():
        try:
            ticker = yf.Ticker(symbole)
            info = ticker.history(period="2d")
            if len(info) >= 2:
                prix_hier = info['Close'].iloc[-2]
                prix_actuel = info['Close'].iloc[-1]
                variation = ((prix_actuel - prix_hier) / prix_hier) * 100

                donnees[nom] = {
                    "symbole": symbole,
                    "prix": round(prix_actuel, 2),
                    "prix_hier": round(prix_hier, 2),
                    "variation_overnight": round(variation, 2)
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
4. Recommandations d'ajustement pour la semaine prochaine
5. Score de confiance pour chaque catégorie d'actifs et type de setup

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
  "ajustements_recommandes": [
    {"critere": "Nom du critère", "action": "Augmenter/Réduire/Modifier", "raison": "Justification"}
  ],
  "scores_confiance": {
    "indices": {"score": 0, "tendance": "hausse/baisse/stable"},
    "actions_eu": {"score": 0, "tendance": ""},
    "actions_us": {"score": 0, "tendance": ""},
    "commodites": {"score": 0, "tendance": ""},
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
        match = re.search(r'\{[\s\S]*\}', reponse)
        if match:
            result = json.loads(match.group())
            return result.get('commentaires', {}), result.get('faits_marquants', [])
        return {}, []
    except Exception as e:
        print(f"❌ Erreur commentaires journal: {e}")
        return {}, []

def recuperer_recommandations_analystes(symbole):
    """Récupère les recommandations des analystes via yfinance"""
    try:
        ticker = yf.Ticker(symbole)
        recos = ticker.recommendations
        if recos is not None and not recos.empty:
            # Prendre les recommandations récentes (30 derniers jours)
            recent = recos.tail(5)
            reco_list = []
            for idx, row in recent.iterrows():
                reco_list.append({
                    'date': str(idx.date()) if hasattr(idx, 'date') else str(idx),
                    'firm': row.get('Firm', 'N/A'),
                    'grade': row.get('To Grade', row.get('toGrade', 'N/A')),
                    'action': row.get('Action', 'N/A')
                })
            return reco_list
        return []
    except Exception as e:
        print(f"⚠️ Erreur recommandations {symbole}: {e}")
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
        match = re.search(r'\{[\s\S]*\}', reponse)
        if match:
            bilan = json.loads(match.group())

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
        print(f"❌ Erreur bilan quotidien: {e}")
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
                0,  # volume_relatif à calculer
                commentaires.get(symbole, ''),
                '',  # evenements_jour
                json.dumps(opps, ensure_ascii=False) if opps else '',
                json.dumps(recos, ensure_ascii=False) if recos else '',
                maintenant
            ))

        conn.commit()
        conn.close()
        print(f"[{maintenant.strftime('%H:%M:%S')} CET] ✅ Journal quotidien enregistré")
        return True
    except Exception as e:
        print(f"❌ Erreur journal quotidien: {e}")
        return False

def enregistrer_journal_fr():
    """Enregistre le journal pour les actions françaises (18h00)"""
    actifs_fr = {k: v for k, v in ACTIFS_PERMANENTS.items()
                 if k.endswith('.PA') or k.startswith('^FCHI') or k == '^GDAXI'}
    return enregistrer_journal_quotidien(actifs_fr)

def generer_rapport_hebdo():
    """Génère le rapport hebdomadaire avec analyse et ajustements"""
    maintenant = get_paris_time()
    print(f"[{maintenant.strftime('%H:%M:%S')} CET] 📊 Génération rapport hebdomadaire...")

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Calculer les dates de la semaine
        date_fin = maintenant.date()
        date_debut = date_fin - timedelta(days=7)
        semaine = f"{date_debut.year}-W{date_debut.isocalendar()[1]:02d}"

        # Récupérer les trades de la semaine
        cursor.execute('''
            SELECT * FROM trades_recommandes
            WHERE date >= ? AND date <= ?
            ORDER BY timestamp_reco
        ''', (date_debut, date_fin))
        trades_semaine = [dict(row) for row in cursor.fetchall()]

        # Récupérer les stats détaillées
        cursor.execute('''
            SELECT
                COUNT(*) as nb_trades,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2') THEN 1 ELSE 0 END) as reussis,
                SUM(CASE WHEN resultat = 'STOP' THEN 1 ELSE 0 END) as stops,
                SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl_total,
                AVG(CASE WHEN duree_minutes IS NOT NULL THEN duree_minutes END) as duree_moyenne
            FROM trades_recommandes
            WHERE date >= ? AND date <= ?
        ''', (date_debut, date_fin))
        stats_globales = dict(cursor.fetchone())

        # Stats par jour
        cursor.execute('''
            SELECT date,
                   COUNT(*) as nb,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2') THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
            FROM trades_recommandes
            WHERE date >= ? AND date <= ?
            GROUP BY date
            ORDER BY pnl DESC
        ''', (date_debut, date_fin))
        stats_par_jour = [dict(row) for row in cursor.fetchall()]

        # Stats par catégorie
        cursor.execute('''
            SELECT categorie_actif,
                   COUNT(*) as nb,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2') THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
            FROM trades_recommandes
            WHERE date >= ? AND date <= ? AND categorie_actif IS NOT NULL
            GROUP BY categorie_actif
        ''', (date_debut, date_fin))
        stats_par_categorie = [dict(row) for row in cursor.fetchall()]

        # Stats par type
        cursor.execute('''
            SELECT type_setup,
                   COUNT(*) as nb,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2') THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
            FROM trades_recommandes
            WHERE date >= ? AND date <= ? AND type_setup IS NOT NULL
            GROUP BY type_setup
        ''', (date_debut, date_fin))
        stats_par_type = [dict(row) for row in cursor.fetchall()]

        # Stats par heure
        cursor.execute('''
            SELECT heure_entree,
                   COUNT(*) as nb,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2') THEN 1 ELSE 0 END) as wins
            FROM trades_recommandes
            WHERE date >= ? AND date <= ? AND heure_entree IS NOT NULL
            GROUP BY heure_entree
            ORDER BY heure_entree
        ''', (date_debut, date_fin))
        stats_par_heure = [dict(row) for row in cursor.fetchall()]

        conn.close()

        # Préparer les données pour Claude
        donnees_rapport = {
            'periode': f"{date_debut} au {date_fin}",
            'stats_globales': stats_globales,
            'stats_par_jour': stats_par_jour,
            'stats_par_categorie': stats_par_categorie,
            'stats_par_type': stats_par_type,
            'stats_par_heure': stats_par_heure,
            'nb_trades_total': len(trades_semaine)
        }

        donnees_texte = json.dumps(donnees_rapport, ensure_ascii=False, indent=2, default=str)

        question = f"""Voici les données de trading de la semaine du {date_debut} au {date_fin}:

{donnees_texte}

Analyse ces performances et génère un rapport hebdomadaire complet.
Identifie les patterns (heures rentables, types de setup efficaces, catégories d'actifs).
Propose des ajustements concrets pour améliorer les performances."""

        message = client_anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=3000,
            system=SYSTEM_PROMPT_RAPPORT_HEBDO,
            messages=[{"role": "user", "content": question}]
        )

        reponse = message.content[0].text
        match = re.search(r'\{[\s\S]*\}', reponse)

        if match:
            rapport = json.loads(match.group())

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
        print(f"❌ Erreur rapport hebdo: {e}")
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
            AND date_maj >= date('now', '-7 days')
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

def sauvegarder_ajustements_proposes(ajustements, scores_confiance, source='rapport_hebdo'):
    """Sauvegarde les ajustements proposés en attente de validation"""
    maintenant = get_paris_time()

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Sauvegarder les scores de confiance comme propositions
        for categorie, score_data in scores_confiance.items():
            if isinstance(score_data, dict):
                score = score_data.get('score', 50)
                tendance = score_data.get('tendance', 'stable')

                cursor.execute('''
                    INSERT INTO ajustements_proposes
                    (type_ajustement, categorie, critere, action, valeur_proposee, raison, source, statut, date_proposition)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    'score_confiance',
                    categorie,
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

            cursor.execute('''
                INSERT INTO ajustements_proposes
                (type_ajustement, categorie, critere, action, valeur_proposee, raison, source, statut, date_proposition)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                'strategie',
                'trading',
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

def valider_ajustement(id_ajustement, decision, decideur='utilisateur'):
    """Valide ou rejette un ajustement proposé avec audit trail"""
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

        # Mettre à jour le statut
        nouveau_statut = 'valide' if decision else 'rejete'
        cursor.execute('''
            UPDATE ajustements_proposes
            SET statut = ?, date_decision = ?, decideur = ?
            WHERE id = ?
        ''', (nouveau_statut, maintenant, decideur, id_ajustement))

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

def expirer_ajustements_anciens(jours_max=7):
    """Expire automatiquement les ajustements non traités après X jours"""
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

def enregistrer_journal_complet():
    """Enregistre le journal complet + bilan (22h30)"""
    # D'abord enregistrer le journal de tous les actifs
    enregistrer_journal_quotidien()
    # Puis générer le bilan général
    generer_bilan_quotidien()

def get_journal_quotidien(symbole=None, limite=30):
    """Récupère le journal quotidien"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        if symbole:
            cursor.execute('''
                SELECT * FROM journal_quotidien
                WHERE symbole = ?
                ORDER BY date DESC LIMIT ?
            ''', (symbole, limite))
        else:
            cursor.execute('''
                SELECT * FROM journal_quotidien
                ORDER BY date DESC, nom_actif ASC LIMIT ?
            ''', (limite * len(ACTIFS_PERMANENTS),))

        entries = [dict(row) for row in cursor.fetchall()]
        conn.close()

        # Parser les champs JSON
        for entry in entries:
            if entry.get('opportunites_jour'):
                try:
                    entry['opportunites_jour'] = json.loads(entry['opportunites_jour'])
                except json.JSONDecodeError:
                    entry['opportunites_jour'] = []
            if entry.get('recommandations_analystes'):
                try:
                    entry['recommandations_analystes'] = json.loads(entry['recommandations_analystes'])
                except json.JSONDecodeError:
                    entry['recommandations_analystes'] = []

        return entries
    except Exception as e:
        print(f"⚠️ Erreur récup journal quotidien: {e}")
        return []

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
    return render_template('index.html')

@app.route('/trading')
def trading():
    """Page d'aide au trading"""
    return render_template('trading.html')

@app.route('/memoire')
def memoire():
    """Page mémoire - Journal et performances"""
    return render_template('memoire.html')

@app.route('/journal')
def journal():
    """Page journal automatique"""
    return render_template('journal.html')

@app.route('/journal/<symbole>')
def journal_actif(symbole):
    """Page journal d'un actif spécifique"""
    return render_template('journal_actif.html', symbole=symbole)

# ============================================================================
# ROUTES API
# ============================================================================

@app.route('/api/status')
def api_status():
    """Statut de l'application"""
    maintenant = get_paris_time()
    marche_type, marche_info = get_market_context()

    return jsonify({
        'status': 'online',
        'datetime': maintenant.strftime('%d/%m/%Y %H:%M:%S'),
        'timezone': 'Europe/Paris',
        'marche': marche_type,
        'marche_info': marche_info,
        'news_envoyees': NEWS_ENVOYEES_AUJOURDHUI,
        'max_news': MAX_NEWS_PAR_JOUR
    })

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
                       SUM(CASE WHEN resultat IN ('TP1', 'TP2') THEN 1 ELSE 0 END) as gagnants,
                       SUM(CASE WHEN resultat = 'STOP' THEN 1 ELSE 0 END) as perdants,
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
        analyse = analyser_marche_json(donnees)

        if analyse:
            # Sauvegarder l'analyse
            marche_type, _ = get_market_context()
            sauvegarder_analyse('intraday', analyse, marche_type)

            # Enregistrer les opportunités comme trades
            for opp in analyse.get('opportunites', []):
                enregistrer_recommandation(opp)

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
    """Récupère et analyse les actualités pour leur impact trading"""
    if not NEWSAPI_KEY:
        return jsonify({'success': False, 'error': 'NEWSAPI_KEY non configurée'})

    try:
        # Récupérer et analyser les news
        news_analysees, error = fetch_and_analyze_news()

        if error:
            return jsonify({'success': False, 'error': error})

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

@app.route('/api/top-movers')
def api_top_movers():
    """Récupère les plus fortes variations du jour"""
    try:
        donnees = recuperer_donnees_marche(ACTIFS_PERMANENTS)
        weekend = is_weekend()

        # Trier par variation absolue
        movers = []
        for nom, data in donnees.items():
            movers.append({
                'nom': nom,
                'symbole': data['symbole'],
                'prix': data['prix'],
                'variation': data['variation']
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
    """Récupère le journal quotidien"""
    symbole = request.args.get('symbole')
    limite = int(request.args.get('limite', 30))
    entries = get_journal_quotidien(symbole, limite)
    return jsonify({
        'success': True,
        'entries': entries
    })

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

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        if granularite == 'jour':
            cursor.execute('''
                SELECT date,
                       COUNT(*) as nb_trades,
                       SUM(CASE WHEN resultat IN ('TP1', 'TP2') THEN 1 ELSE 0 END) as reussis,
                       SUM(CASE WHEN resultat = 'STOP' THEN 1 ELSE 0 END) as stops,
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
                       SUM(CASE WHEN resultat IN ('TP1', 'TP2') THEN 1 ELSE 0 END) as reussis,
                       SUM(CASE WHEN resultat = 'STOP' THEN 1 ELSE 0 END) as stops,
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
                       SUM(CASE WHEN resultat IN ('TP1', 'TP2') THEN 1 ELSE 0 END) as reussis,
                       SUM(CASE WHEN resultat = 'STOP' THEN 1 ELSE 0 END) as stops,
                       SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl_cumule
                FROM trades_recommandes
                GROUP BY strftime('%Y-%m', date)
                ORDER BY periode DESC
                LIMIT ?
            ''', (limite,))

        stats = []
        pnl_running = 0
        for row in cursor.fetchall():
            data = dict(row)
            pnl_running += data['pnl_cumule'] or 0
            data['pnl_cumule_running'] = round(pnl_running, 2)
            conclus = data['reussis'] + data['stops']
            data['taux_reussite'] = round((data['reussis'] / conclus * 100) if conclus > 0 else 0, 1)
            stats.append(data)

        conn.close()

        # Inverser pour avoir l'ordre chronologique
        stats.reverse()

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
        evenements = get_evenements_macro_jour(pour_lundi=weekend)

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
            'message_weekend': "Agenda de lundi" if weekend else None
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
    """Valide un ajustement proposé"""
    try:
        succes, message = valider_ajustement(id_ajustement, decision=True)
        return jsonify({'success': succes, 'message': message})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/ajustements-proposes/<int:id_ajustement>/rejeter', methods=['POST'])
def api_rejeter_ajustement(id_ajustement):
    """Rejette un ajustement proposé"""
    try:
        succes, message = valider_ajustement(id_ajustement, decision=False)
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
        conn = sqlite3.connect(DB_PATH)
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
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2') THEN 1 ELSE 0 END) as reussis,
                   SUM(CASE WHEN resultat = 'STOP' THEN 1 ELSE 0 END) as stops,
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
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2') THEN 1 ELSE 0 END) as reussis,
                   SUM(CASE WHEN resultat = 'STOP' THEN 1 ELSE 0 END) as stops,
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
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2') THEN 1 ELSE 0 END) as reussis,
                   SUM(CASE WHEN resultat = 'STOP' THEN 1 ELSE 0 END) as stops,
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
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2') THEN 1 ELSE 0 END) as reussis,
                   SUM(CASE WHEN resultat = 'STOP' THEN 1 ELSE 0 END) as stops,
                   AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as pnl_moyen
            FROM trades_recommandes
            WHERE date >= ? AND direction IS NOT NULL
            GROUP BY direction
        ''', (date_debut,))
        stats_par_direction = [dict(row) for row in cursor.fetchall()]

        # Top 5 actifs les plus performants
        cursor.execute('''
            SELECT actif, symbole,
                   COUNT(*) as nb_trades,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2') THEN 1 ELSE 0 END) as reussis,
                   AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as pnl_moyen
            FROM trades_recommandes
            WHERE date >= ? AND resultat IS NOT NULL
            GROUP BY symbole
            HAVING nb_trades >= 2
            ORDER BY (CAST(reussis AS FLOAT) / nb_trades) DESC, pnl_moyen DESC
            LIMIT 5
        ''', (date_debut,))
        top_actifs = [dict(row) for row in cursor.fetchall()]

        # Bottom 5 actifs les moins performants
        cursor.execute('''
            SELECT actif, symbole,
                   COUNT(*) as nb_trades,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2') THEN 1 ELSE 0 END) as reussis,
                   AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as pnl_moyen
            FROM trades_recommandes
            WHERE date >= ? AND resultat IS NOT NULL
            GROUP BY symbole
            HAVING nb_trades >= 2
            ORDER BY (CAST(reussis AS FLOAT) / nb_trades) ASC, pnl_moyen ASC
            LIMIT 5
        ''', (date_debut,))
        bottom_actifs = [dict(row) for row in cursor.fetchall()]

        # Statistiques de durée des trades
        cursor.execute('''
            SELECT
                AVG(duree_minutes) as duree_moyenne,
                MIN(duree_minutes) as duree_min,
                MAX(duree_minutes) as duree_max,
                AVG(CASE WHEN resultat IN ('TP1', 'TP2') THEN duree_minutes END) as duree_moyenne_gagnants,
                AVG(CASE WHEN resultat = 'STOP' THEN duree_minutes END) as duree_moyenne_perdants
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
            conclus = (s.get('reussis') or 0) + (s.get('nb_trades') or 0) - (s.get('reussis') or 0)
            s['taux_reussite'] = round((s.get('reussis', 0) / s.get('nb_trades', 1) * 100) if s.get('nb_trades') else 0, 1)
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
    """Récupère l'historique des trades avec filtres"""
    try:
        periode = request.args.get('periode', 'semaine')
        categorie = request.args.get('categorie', '')
        direction = request.args.get('direction', '')
        recherche = request.args.get('recherche', '')

        conn = sqlite3.connect(DB_PATH)
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

        query = 'SELECT * FROM trades_recommandes WHERE date >= ?'
        params = [date_debut]

        if categorie:
            query += ' AND categorie_actif = ?'
            params.append(categorie)

        if direction:
            query += ' AND direction = ?'
            params.append(direction)

        if recherche:
            query += ' AND (actif LIKE ? OR symbole LIKE ?)'
            params.extend([f'%{recherche}%', f'%{recherche}%'])

        query += ' ORDER BY timestamp_reco DESC'

        cursor.execute(query, params)
        trades = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return jsonify({
            'success': True,
            'trades': trades,
            'count': len(trades)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ============================================================================
# WHATSAPP (optionnel)
# ============================================================================

def envoyer_whatsapp(message):
    """Envoie un message WhatsApp"""
    if not client_twilio:
        print("⚠️ Twilio non configuré")
        return False

    try:
        client_twilio.messages.create(
            from_=os.environ.get("TWILIO_WHATSAPP_FROM"),
            body=message[:1500],
            to=os.environ.get("TWILIO_WHATSAPP_TO")
        )
        return True
    except Exception as e:
        print(f"❌ Erreur WhatsApp: {e}")
        return False

# ============================================================================
# TÂCHES PLANIFIÉES
# ============================================================================

def executer_analyse_planifiee():
    """Exécute une analyse planifiée"""
    maintenant = get_paris_time()
    if maintenant.weekday() >= 5:
        return

    print(f"\n[{maintenant.strftime('%H:%M:%S')} CET] 🚀 Analyse planifiée...")

    try:
        donnees = recuperer_donnees_marche(ACTIFS_PERMANENTS)
        analyse = analyser_marche_json(donnees)

        if analyse:
            marche_type, _ = get_market_context()
            sauvegarder_analyse('planifiee', analyse, marche_type)

            for opp in analyse.get('opportunites', []):
                enregistrer_recommandation(opp)

            print(f"[{maintenant.strftime('%H:%M:%S')} CET] ✅ Analyse sauvegardée")
    except Exception as e:
        print(f"[{maintenant.strftime('%H:%M:%S')} CET] ❌ Erreur: {e}")

def executer_cloture_planifiee():
    """Exécute la clôture planifiée"""
    maintenant = get_paris_time()
    if maintenant.weekday() >= 5:
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
    if maintenant.weekday() >= 5:
        return
    enregistrer_journal_fr()

def executer_journal_complet():
    """Exécute l'enregistrement du journal complet + bilan à 22h30"""
    maintenant = get_paris_time()
    if maintenant.weekday() >= 5:
        return
    enregistrer_journal_complet()

def executer_verification_trades():
    """Exécute la vérification des trades"""
    maintenant = get_paris_time()
    if maintenant.weekday() >= 5:
        return
    verifier_resultats_trades()

def executer_cloture_trades():
    """Exécute la clôture des trades du jour"""
    maintenant = get_paris_time()
    if maintenant.weekday() >= 5:
        return
    # D'abord vérifier une dernière fois
    verifier_resultats_trades()
    # Puis clôturer les trades restants
    cloturer_trades_jour()

def executer_rapport_hebdo():
    """Exécute la génération du rapport hebdomadaire (dimanche soir)"""
    maintenant = get_paris_time()
    if maintenant.weekday() != 6:  # 6 = Dimanche
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

    # Clôture trades EU: 17h45 (après clôture EU)
    heure_cloture_eu_utc = get_utc_time_for_paris("17:45")
    for jour in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']:
        getattr(schedule.every(), jour).at(heure_cloture_eu_utc).do(executer_verification_trades)

    # Clôture: 22h
    heure_cloture_utc = get_utc_time_for_paris("22:00")
    for jour in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']:
        getattr(schedule.every(), jour).at(heure_cloture_utc).do(executer_cloture_planifiee)

    # Clôture trades US + finale: 22h15 (après clôture US)
    heure_cloture_trades_utc = get_utc_time_for_paris("22:15")
    for jour in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']:
        getattr(schedule.every(), jour).at(heure_cloture_trades_utc).do(executer_cloture_trades)

    # Journal FR: 18h00 (après clôture marchés EU)
    heure_journal_fr_utc = get_utc_time_for_paris("18:00")
    for jour in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']:
        getattr(schedule.every(), jour).at(heure_journal_fr_utc).do(executer_journal_fr)

    # Journal complet + Bilan: 22h30 (après clôture US)
    heure_journal_complet_utc = get_utc_time_for_paris("22:30")
    for jour in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']:
        getattr(schedule.every(), jour).at(heure_journal_complet_utc).do(executer_journal_complet)

    # Rapport hebdomadaire: Dimanche 20h00
    heure_rapport_hebdo_utc = get_utc_time_for_paris("20:00")
    schedule.every().sunday.at(heure_rapport_hebdo_utc).do(executer_rapport_hebdo)

    # Expiration des ajustements non traités: tous les jours à 23h00
    heure_expiration_utc = get_utc_time_for_paris("23:00")
    schedule.every().day.at(heure_expiration_utc).do(expirer_ajustements_anciens)

def run_scheduler():
    """Thread pour le scheduler"""
    while True:
        schedule.run_pending()
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

# Initialisation au niveau module (requis pour Replit)
init_database()
configurer_schedule()
scheduler_thread = Thread(target=run_scheduler, daemon=True)
scheduler_thread.start()

if __name__ == "__main__":
    start_app()
