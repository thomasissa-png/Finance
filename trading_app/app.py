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

        cursor.execute('''
            INSERT INTO trades_recommandes
            (date, heure_message, actif, symbole, type_setup, prix_entree, prix_stop,
             prix_tp1, prix_tp2, prix_actuel, catalyseur, duree_estimee, timestamp_reco)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            maintenant.date(),
            maintenant.strftime('%H:%M'),
            trade_data.get('actif', ''),
            trade_data.get('symbole', ''),
            'NEWS' if trade_data.get('is_news_trading') else 'TECHNIQUE',
            trade_data.get('entree', 0),
            trade_data.get('stop', 0),
            trade_data.get('tp1', 0),
            trade_data.get('tp2', 0),
            trade_data.get('prix_actuel', 0),
            trade_data.get('catalyseur', ''),
            trade_data.get('duree', ''),
            maintenant
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
    init_database()
    configurer_schedule()

    # Démarrer le scheduler dans un thread
    scheduler_thread = Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()

    maintenant = get_paris_time()
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║           AGENT TRADING - INTERFACE WEB                       ║
╠══════════════════════════════════════════════════════════════╣
║  📅 {maintenant.strftime('%d/%m/%Y %H:%M:%S')} CET                                  ║
║  🌐 URL: http://localhost:8080                                ║
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

    app.run(host='0.0.0.0', port=8080, debug=False)

if __name__ == "__main__":
    start_app()
