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
            variation_jour REAL,
            volume_relatif REAL,
            commentaire_ia TEXT,
            evenements_jour TEXT,
            opportunites_jour TEXT,
            timestamp DATETIME,
            UNIQUE(date, symbole)
        )
    ''')

    # Ajouter colonnes à trades_recommandes si manquantes
    try:
        cursor.execute("ALTER TABLE trades_recommandes ADD COLUMN direction TEXT DEFAULT 'LONG'")
    except:
        pass
    try:
        cursor.execute("ALTER TABLE trades_recommandes ADD COLUMN categorie_actif TEXT")
    except:
        pass

    conn.commit()
    conn.close()
    print("✅ Base de données initialisée")

# ============================================================================
# FONCTIONS UTILITAIRES
# ============================================================================

def get_paris_time():
    """Retourne l'heure actuelle à Paris"""
    return datetime.now(TZ_PARIS)

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

def get_market_context():
    """Détermine quels marchés sont ouverts"""
    maintenant = get_paris_time()
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
# RÉCUPÉRATION DONNÉES MARCHÉ
# ============================================================================

def recuperer_donnees_marche(actifs):
    """Récupère les données de marché pour les actifs donnés"""
    donnees = {}
    for symbole, nom in actifs.items():
        try:
            ticker = yf.Ticker(symbole)
            info = ticker.history(period="5d")
            if not info.empty:
                prix_actuel = info['Close'].iloc[-1]
                prix_ouverture = info['Open'].iloc[-1]
                prix_max = info['High'].iloc[-1]
                prix_min = info['Low'].iloc[-1]
                variation = ((prix_actuel - prix_ouverture) / prix_ouverture) * 100

                # Variation sur 5 jours
                if len(info) >= 5:
                    prix_5j = info['Close'].iloc[0]
                    var_5j = ((prix_actuel - prix_5j) / prix_5j) * 100
                else:
                    var_5j = 0

                donnees[nom] = {
                    "symbole": symbole,
                    "prix": round(prix_actuel, 2),
                    "ouverture": round(prix_ouverture, 2),
                    "haut": round(prix_max, 2),
                    "bas": round(prix_min, 2),
                    "variation": round(variation, 2),
                    "variation_5j": round(var_5j, 2)
                }
        except Exception as e:
            print(f"⚠️ Erreur {symbole}: {e}")
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

        # Prix actuel
        indicateurs['prix_actuel'] = round(df_daily['Close'].iloc[-1], 2)
        indicateurs['variation_jour'] = round(
            ((df_daily['Close'].iloc[-1] - df_daily['Open'].iloc[-1]) / df_daily['Open'].iloc[-1]) * 100, 2
        )

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
- Objectif: +1% minimum en quelques minutes à 2-3h max
- Stop serré: -0.5% à -0.8%
- Pas de positions overnight

RÈGLES:
- UNIQUEMENT des actifs dont le marché est OUVERT ou s'ouvre dans 2h
- AU MINIMUM 1 opportunité NEWS TRADING dans tes recommandations
- TOUTES les heures en CET (heure française)
- PRÉCISE TOUJOURS si c'est un LONG ou un SHORT

FORMAT DE RÉPONSE EN JSON:
{
  "contexte_marche": "Paragraphe narratif sur le contexte actuel",
  "snapshot": {
    "indices": {"CAC": {"prix": 0, "var": 0}, ...},
    "mouvements_actifs": [{"actif": "", "var": 0, "raison": ""}]
  },
  "opportunites": [
    {
      "actif": "",
      "direction": "LONG ou SHORT",
      "prix_actuel": 0,
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
  "evenements_a_venir": [
    {"heure": "", "evenement": "", "importance": 1-3}
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

def analyser_marche_json(donnees):
    """Analyse le marché et retourne un JSON structuré"""
    maintenant = get_paris_time()
    heure_str = maintenant.strftime('%H:%M')

    marche_type, marche_info = get_market_context()

    # Formater les données pour le prompt
    donnees_texte = json.dumps(donnees, ensure_ascii=False, indent=2)

    question = f"""DONNÉES MARCHÉ EN TEMPS RÉEL:
{donnees_texte}

Heure: {maintenant.strftime('%d/%m/%Y %H:%M')} CET
Contexte: {marche_type} - {marche_info}

Analyse le marché et fournis une réponse JSON structurée selon le format demandé.
Assure-toi d'inclure AU MOINS 1 opportunité NEWS TRADING.
Liste UNIQUEMENT les événements APRÈS {heure_str}."""

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
            return json.loads(match.group())
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

        # Déterminer la direction
        # 1. Utiliser la direction fournie par Claude si disponible
        # 2. Sinon déduire: SHORT si stop > entrée, LONG sinon
        entree = float(trade_data.get('entree', 0) or 0)
        stop = float(trade_data.get('stop', 0) or 0)

        if trade_data.get('direction'):
            direction = trade_data.get('direction').upper()
        elif stop > entree and entree > 0:
            direction = 'SHORT'
        else:
            direction = 'LONG'

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
             direction, categorie_actif)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            categorie
        ))

        conn.commit()
        conn.close()
        return cursor.lastrowid
    except Exception as e:
        print(f"⚠️ Erreur enregistrement: {e}")
        return None

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
        conclus = total - non_conclus

        return {
            'total': total,
            'reussis': reussis,
            'stops': stops,
            'non_conclus': non_conclus,
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

SYSTEM_PROMPT_JOURNAL = """Tu es un analyste financier qui rédige des commentaires de journal pour un trader.
Pour chaque actif, tu dois fournir un commentaire concis (2-3 phrases max) expliquant:
- Ce qui s'est passé aujourd'hui (mouvement de prix, volume)
- Les raisons probables (actualités, macro, technique)
- Le contexte pour demain

FORMAT DE RÉPONSE EN JSON:
{
  "commentaires": {
    "SYMBOLE1": "Commentaire pour cet actif...",
    "SYMBOLE2": "Commentaire pour cet actif..."
  }
}"""

def generer_commentaires_journal(donnees_actifs):
    """Génère des commentaires AI pour les actifs du journal"""
    if not donnees_actifs:
        return {}

    donnees_texte = json.dumps(donnees_actifs, ensure_ascii=False, indent=2)
    maintenant = get_paris_time()

    question = f"""Voici les données des actifs pour le {maintenant.strftime('%d/%m/%Y')}:

{donnees_texte}

Génère un commentaire de journal pour chaque actif, en expliquant brièvement ce qui s'est passé aujourd'hui."""

    try:
        message = client_anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            system=SYSTEM_PROMPT_JOURNAL,
            messages=[{"role": "user", "content": question}]
        )

        reponse = message.content[0].text
        match = re.search(r'\{[\s\S]*\}', reponse)
        if match:
            result = json.loads(match.group())
            return result.get('commentaires', {})
        return {}
    except Exception as e:
        print(f"❌ Erreur commentaires journal: {e}")
        return {}

def enregistrer_journal_quotidien():
    """Enregistre le journal quotidien pour tous les actifs suivis"""
    maintenant = get_paris_time()
    aujourdhui = maintenant.date()

    print(f"[{maintenant.strftime('%H:%M:%S')} CET] 📝 Enregistrement journal quotidien...")

    try:
        # Récupérer les données de tous les actifs
        donnees = recuperer_donnees_marche(ACTIFS_PERMANENTS)

        # Récupérer les opportunités du jour pour chaque actif
        opportunites_jour = {}
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('''
                SELECT actif, symbole, type_setup, prix_entree, prix_tp1, resultat
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
                    'resultat': row['resultat']
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
                'variation': data['variation'],
                'variation_5j': data.get('variation_5j', 0)
            }

        # Générer les commentaires AI
        commentaires = generer_commentaires_journal(donnees_pour_ia)

        # Enregistrer dans la base
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        for nom, data in donnees.items():
            symbole = data['symbole']
            categorie = get_categorie_actif(symbole)
            opps = opportunites_jour.get(symbole, [])

            cursor.execute('''
                INSERT OR REPLACE INTO journal_quotidien
                (date, symbole, nom_actif, categorie, prix_ouverture, prix_cloture,
                 variation_jour, volume_relatif, commentaire_ia, evenements_jour,
                 opportunites_jour, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                aujourdhui,
                symbole,
                nom,
                categorie,
                data.get('ouverture', 0),
                data['prix'],
                data['variation'],
                0,  # volume_relatif à calculer
                commentaires.get(symbole, ''),
                '',  # evenements_jour
                json.dumps(opps, ensure_ascii=False) if opps else '',
                maintenant
            ))

        conn.commit()
        conn.close()
        print(f"[{maintenant.strftime('%H:%M:%S')} CET] ✅ Journal quotidien enregistré")
        return True
    except Exception as e:
        print(f"❌ Erreur journal quotidien: {e}")
        return False

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

        # Parser les opportunités JSON
        for entry in entries:
            if entry.get('opportunites_jour'):
                try:
                    entry['opportunites_jour'] = json.loads(entry['opportunites_jour'])
                except:
                    entry['opportunites_jour'] = []

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
            except:
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
            except:
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
        return jsonify({
            'success': True,
            'datetime': get_paris_time().strftime('%d/%m/%Y %H:%M:%S'),
            'donnees': donnees
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
                'analyse': analyse
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
    """Récupère les actualités financières via NewsAPI"""
    if not NEWSAPI_KEY:
        return jsonify({'success': False, 'error': 'NEWSAPI_KEY non configurée'})

    try:
        # Requête NewsAPI pour les actualités business/finance
        url = "https://newsapi.org/v2/top-headlines"
        params = {
            'apiKey': NEWSAPI_KEY,
            'category': 'business',
            'language': 'fr',
            'pageSize': 10
        }

        response = requests.get(url, params=params, timeout=10)
        data = response.json()

        if data.get('status') != 'ok':
            # Essayer avec "everything" et des mots-clés finance
            url = "https://newsapi.org/v2/everything"
            params = {
                'apiKey': NEWSAPI_KEY,
                'q': 'bourse OR CAC40 OR marchés financiers OR trading',
                'language': 'fr',
                'sortBy': 'publishedAt',
                'pageSize': 10
            }
            response = requests.get(url, params=params, timeout=10)
            data = response.json()

        if data.get('status') == 'ok':
            articles = []
            for article in data.get('articles', [])[:10]:
                # Extraire l'heure de publication
                published = article.get('publishedAt', '')
                heure = '--:--'
                if published:
                    try:
                        from datetime import datetime
                        dt = datetime.fromisoformat(published.replace('Z', '+00:00'))
                        dt_paris = dt.astimezone(TZ_PARIS)
                        heure = dt_paris.strftime('%H:%M')
                    except:
                        pass

                articles.append({
                    'titre': article.get('title', '')[:100],
                    'description': article.get('description', '')[:200] if article.get('description') else '',
                    'source': article.get('source', {}).get('name', 'Inconnu'),
                    'heure': heure,
                    'url': article.get('url', ''),
                    'impact': 'medium'  # Par défaut
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

        # Séparer gainers et losers
        gainers = [m for m in movers_sorted if m['variation'] > 0][:5]
        losers = [m for m in movers_sorted if m['variation'] < 0][:5]

        return jsonify({
            'success': True,
            'datetime': get_paris_time().strftime('%d/%m/%Y %H:%M:%S'),
            'gainers': gainers,
            'losers': losers
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

def executer_journal_quotidien():
    """Exécute l'enregistrement du journal quotidien"""
    maintenant = get_paris_time()
    if maintenant.weekday() >= 5:
        return
    enregistrer_journal_quotidien()

def configurer_schedule():
    """Configure les tâches planifiées"""
    # Analyses: 8h, 14h30, 17h
    for heure in ["08:00", "14:30", "17:00"]:
        heure_utc = get_utc_time_for_paris(heure)
        for jour in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']:
            getattr(schedule.every(), jour).at(heure_utc).do(executer_analyse_planifiee)

    # Clôture: 22h
    heure_cloture_utc = get_utc_time_for_paris("22:00")
    for jour in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']:
        getattr(schedule.every(), jour).at(heure_cloture_utc).do(executer_cloture_planifiee)

    # Journal quotidien: 17h45 (après clôture FR) et 22h15 (après clôture US)
    for heure in ["17:45", "22:15"]:
        heure_utc = get_utc_time_for_paris(heure)
        for jour in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']:
            getattr(schedule.every(), jour).at(heure_utc).do(executer_journal_quotidien)

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
