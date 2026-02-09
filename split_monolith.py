#!/usr/bin/env python3
"""
Script de découpage du monolithe app.py en modules.
Lit app.py et extrait les sections dans des fichiers séparés.
"""
import re

def read_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.readlines()

def write_file(path, content):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"  ✅ {path} ({len(content.splitlines())} lignes)")

lines = read_file('trading_app/app.py')
total = len(lines)
print(f"📖 app.py: {total} lignes")

# Helper to extract lines (1-indexed to 0-indexed)
def extract(start, end):
    """Extract lines start..end (1-indexed, inclusive)"""
    return ''.join(lines[start-1:end])

# ============================================================================
# 1. config.py - Flask app, logging, API clients, global state, locks
# ============================================================================
config_py = '''"""
Configuration globale de l'application de trading.
Flask app, clients API, variables globales, caches et locks.
"""
import os
import json
import time
import logging
import warnings
from logging.handlers import RotatingFileHandler
from threading import Lock
from datetime import datetime

import pytz
import numpy as np
from anthropic import Anthropic
from flask import Flask
from twilio.rest import Client

# ============================================================================
# FLASK APP
# ============================================================================

app = Flask(__name__, template_folder='templates', static_folder='static')
_secret_key = os.environ.get('SECRET_KEY')
if not _secret_key:
    warnings.warn("SECRET_KEY non définie! Utilisation d'une clé temporaire (non sécurisé en production)")
    _secret_key = 'dev-only-insecure-key-' + os.urandom(16).hex()
app.config['SECRET_KEY'] = _secret_key

# ============================================================================
# LOGGING
# ============================================================================

LOG_PATH = 'trading.log'
logger = logging.getLogger('trading_app')
logger.setLevel(logging.DEBUG)

file_handler = RotatingFileHandler(LOG_PATH, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
file_handler.setLevel(logging.INFO)
file_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.WARNING)
console_formatter = logging.Formatter('[%(levelname)s] %(message)s')
console_handler.setFormatter(console_formatter)
logger.addHandler(console_handler)

# ============================================================================
# CLIENTS API
# ============================================================================

client_anthropic = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
client_twilio = Client(
    os.environ.get("TWILIO_ACCOUNT_SID"),
    os.environ.get("TWILIO_AUTH_TOKEN")
) if os.environ.get("TWILIO_ACCOUNT_SID") else None

# ============================================================================
# VARIABLES GLOBALES
# ============================================================================

NEWS_ENVOYEES_AUJOURDHUI = 0
MAX_NEWS_PAR_JOUR = 3
DERNIERE_VERIFICATION_DATE = None
DB_PATH = 'trading.db'
DB_TIMEOUT = 10.0
NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY")
TWELVEDATA_API_KEY = os.environ.get("TWELVEDATA_API_KEY")

# Rate limiting Twelve Data (Plan Grow: 55 appels/min)
TWELVEDATA_LAST_CALL = None
TWELVEDATA_MIN_INTERVAL = 1.1
TWELVEDATA_CACHE = {}
TWELVEDATA_CACHE_TTL = 600
TWELVEDATA_CACHE_MAX_SIZE = 500

# Circuit breaker quota
TWELVEDATA_QUOTA_EXCEEDED = False
TWELVEDATA_QUOTA_RESET_TIME = None
TWELVEDATA_QUOTA_COOLDOWN = 60

# Cache news
NEWS_CACHE = {'data': None, 'timestamp': 0}
NEWS_CACHE_TTL = 300

# Cache VIX persistant
VIX_CACHE = {'value': 20.0, 'timestamp': 0, 'source': 'default'}
VIX_CACHE_TTL = 120
VIX_CACHE_FILE = 'vix_cache.json'

# Cache données marché (anti-stampede)
MARKET_DATA_CACHE = {'data': None, 'timestamp': 0, 'actifs_key': None}
MARKET_DATA_CACHE_TTL = 60

# Locks thread-safety
TWELVEDATA_RATE_LOCK = Lock()
TWELVEDATA_CACHE_LOCK = Lock()
TWELVEDATA_FETCH_LOCK = Lock()

# Timezone
TZ_PARIS = pytz.timezone('Europe/Paris')

# A/B Tests actifs (chargé au démarrage)
AB_TESTS_ACTIFS = {}

# ============================================================================
# HELPERS
# ============================================================================

def to_python_type(val):
    """Convertit les types numpy en types Python natifs pour la sérialisation JSON."""
    if val is None:
        return None
    if isinstance(val, (np.integer, np.int64)):
        return int(val)
    if isinstance(val, (np.floating, np.float64)):
        return float(val)
    if isinstance(val, np.ndarray):
        return val.tolist()
    return val
'''

write_file('trading_app/config.py', config_py)

# ============================================================================
# 2. constants.py - Actifs, symboles, mappings, holidays
# ============================================================================
constants_py = '''"""
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
    "OR.PA": "L\\'Oréal",
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

''' + extract(258, 345) + '''
'''

write_file('trading_app/constants.py', constants_py)

# ============================================================================
# 3. database.py
# ============================================================================
# Read init_database, create_database_indexes, backup_database, cleanup_old_backups
db_section = extract(1763, 2178)
database_py = '''"""
Gestion de la base de données SQLite: schéma, initialisation, backup.
"""
import os
import sqlite3
import shutil
from datetime import datetime, timedelta

from .config import DB_PATH, DB_TIMEOUT, logger

''' + db_section + '''
'''

write_file('trading_app/database.py', database_py)

# ============================================================================
# 4. market_context.py - Time, VIX, sessions, regime, grading, macro
# ============================================================================
# VIX cache load/save (2179-2202), time utils (2203-2265), VIX (2265-2343),
# sessions (2344-2418), contexte (2419-2445), grading (2446-2691), macro (2692-2808)
mc_section = extract(2179, 2808)
market_context_py = '''"""
Contexte de marché: horloge, VIX, sessions, régime, grading, événements macro.
"""
import os
import json
import time
from datetime import datetime, timedelta

import pytz
import yfinance as yf

from .config import (
    TZ_PARIS, VIX_CACHE, VIX_CACHE_TTL, VIX_CACHE_FILE,
    logger, DB_PATH, DB_TIMEOUT, to_python_type
)

''' + mc_section + '''
'''

write_file('trading_app/market_context.py', market_context_py)

# ============================================================================
# 5. market_data.py - TwelveData, Yahoo, batch, rate limiting, recuperer_donnees
# ============================================================================
# Functions: convert_symbol (347-364), circuit breaker (366-411),
# yahoo (413-568), twelvedata (570-843), batch (919-1115),
# recuperer_donnees_marche (2817-2946)
md_funcs = extract(347, 843) + '\n' + extract(919, 1115) + '\n' + extract(2817, 2946)
market_data_py = '''"""
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
    TWELVEDATA_FETCH_LOCK, MARKET_DATA_CACHE, MARKET_DATA_CACHE_TTL,
    logger, DB_PATH, DB_TIMEOUT
)
import .config as config
from .constants import SYMBOLES_YAHOO_FALLBACK, SYMBOL_MAPPING_TWELVEDATA, ACTIFS_PERMANENTS
from .market_context import get_paris_time

''' + md_funcs + '''
'''
# Fix the import syntax
market_data_py = market_data_py.replace('import .config as config', 'from . import config')

write_file('trading_app/market_data.py', market_data_py)

# ============================================================================
# 6. indicators.py - RSI, MACD, ATR, trends, enrichment, calculer_indicateurs_techniques
# ============================================================================
ind_section = extract(1120, 1387) + '\n' + extract(2947, 3020)
indicators_py = '''"""
Indicateurs techniques: RSI, MACD, ATR, tendances, enrichissement données.
"""
import numpy as np
import pandas as pd

from .config import logger
from .market_data import get_twelvedata_time_series, get_twelvedata_intraday

''' + ind_section + '''
'''

write_file('trading_app/indicators.py', indicators_py)

# ============================================================================
# 7. validation.py - JSON extraction, opportunity validation, intraday analysis
# ============================================================================
val_section = extract(1392, 1758) + '\n' + extract(844, 914)
validation_py = '''"""
Validation: extraction JSON Claude, validation opportunités, setup trades.
"""
import re
import json
import time

from .config import logger, TWELVEDATA_API_KEY, to_python_type
from .constants import ACTIFS_PERMANENTS, POOL_ROTATION
from .market_context import get_paris_time, get_session_marche
from .market_data import (
    convert_symbol_to_twelvedata, rate_limit_twelvedata,
    activate_quota_circuit_breaker, get_fresh_quote_for_trade
)

''' + val_section + '''
'''

write_file('trading_app/validation.py', validation_py)

# ============================================================================
# 8. prompts.py - System prompts (large text constants)
# ============================================================================
prompts_section = extract(3025, 3248)
# Also need SYSTEM_PROMPT_CLOTURE and others - find them
# Search for other system prompts in lines 3249+
prompts_py = '''"""
System prompts pour Claude AI: trading, clôture, news, journal, bilan, rapport hebdo.
"""

# ============================================================================
# SYSTEM PROMPTS
# ============================================================================

''' + prompts_section + '''
'''
# We'll add more prompts from the journal/report sections later

write_file('trading_app/prompts.py', prompts_py)

# ============================================================================
# 9. analysis.py - Claude analysis, news
# ============================================================================
# analyser_news_trading (3249-3286), fetch_and_analyze_news (3288-3383),
# sauvegarder_news (3384-3424), get_news_historique (3425-3447),
# analyser_marche_json (3448-3582), generer_cloture_json (3583-3615)
analysis_section = extract(3249, 3615)
analysis_py = '''"""
Analyse de marché via Claude AI: analyse marché, news, clôture.
"""
import os
import json
import sqlite3
import time
from datetime import datetime, timedelta

import requests

from .config import (
    client_anthropic, NEWSAPI_KEY, NEWS_CACHE, NEWS_CACHE_TTL,
    logger, DB_PATH, DB_TIMEOUT
)
import .config as config
from .constants import ACTIFS_PERMANENTS
from .market_context import (
    get_paris_time, get_market_context, get_vix_level, get_regime_marche,
    get_session_marche, get_pattern_jour_semaine, get_contexte_trading_complet,
    get_evenements_macro_jour, get_actifs_filtres_atr
)
from .market_data import recuperer_donnees_marche
from .validation import extraire_json_claude, valider_structure_analyse
from .prompts import SYSTEM_PROMPT

''' + analysis_section + '''
'''
analysis_py = analysis_py.replace('import .config as config', 'from . import config')

write_file('trading_app/analysis.py', analysis_py)

# ============================================================================
# 10. trades.py - Trade recording, verification, re-evaluation, closure, performance
# ============================================================================
trades_section = extract(3620, 4549)
trades_py = '''"""
Gestion des trades: enregistrement, vérification, réévaluation, clôture, performance.
"""
import sqlite3
import json
import time
from datetime import datetime, timedelta

import numpy as np

from .config import logger, DB_PATH, DB_TIMEOUT, to_python_type
from .constants import ACTIFS_PERMANENTS
from .market_context import (
    get_paris_time, get_regime_marche, get_session_marche,
    calculer_trade_grade
)
from .market_data import (
    get_twelvedata_intraday, get_twelvedata_quote, get_fresh_quote_for_trade
)
from .validation import valider_opportunite, analyser_cloture_intraday

''' + trades_section + '''
'''

write_file('trading_app/trades.py', trades_py)

# ============================================================================
# 11. journal.py - Journal entries, daily/weekly reports, analyses storage
# ============================================================================
journal_section = (
    extract(4555, 4687) + '\n' +  # journal actif, premarket
    extract(4688, 5673) + '\n' +  # journal generation, reports
    extract(6825, 7027)           # journal complet, queries, analyses storage
)
journal_py = '''"""
Journal des actifs, rapports quotidiens/hebdomadaires, stockage analyses.
"""
import sqlite3
import json
from datetime import datetime, timedelta

from .config import client_anthropic, logger, DB_PATH, DB_TIMEOUT, to_python_type
from .constants import ACTIFS_PERMANENTS, SYMBOLES_ACTIONS_US
from .market_context import get_paris_time, get_market_context, get_regime_marche
from .market_data import recuperer_donnees_marche
from .trades import get_trades_du_jour, get_performances

''' + journal_section + '''
'''

write_file('trading_app/journal.py', journal_py)

# ============================================================================
# 12. adjustments.py - Adjustments, feedback, dynamic criteria
# ============================================================================
adj_section = extract(5704, 6311) + '\n' + extract(5635, 5703) + '\n' + extract(6668, 6824)
adjustments_py = '''"""
Ajustements stratégiques: propositions, validation, feedback, critères dynamiques.
"""
import sqlite3
import json
from datetime import datetime, timedelta

from .config import logger, DB_PATH, DB_TIMEOUT
from .market_context import get_paris_time

''' + adj_section + '''
'''

write_file('trading_app/adjustments.py', adjustments_py)

# ============================================================================
# 13. ab_testing.py
# ============================================================================
ab_section = extract(6319, 6667)
ab_testing_py = '''"""
Infrastructure de tests A/B pour les stratégies de trading.
"""
import sqlite3
import json
import random
from datetime import datetime

from .config import logger, DB_PATH, DB_TIMEOUT
import .config as config
from .market_context import get_paris_time

''' + ab_section + '''
'''
ab_testing_py = ab_testing_py.replace('import .config as config', 'from . import config')

write_file('trading_app/ab_testing.py', ab_testing_py)

# ============================================================================
# 14. notifications.py
# ============================================================================
notif_section = extract(9138, 9160)
notifications_py = '''"""
Notifications WhatsApp via Twilio.
"""
import os

from .config import client_twilio, logger

''' + notif_section + '''
'''

write_file('trading_app/notifications.py', notifications_py)

# ============================================================================
# 15. scheduler.py
# ============================================================================
sched_section = extract(9166, 9390)
scheduler_py = '''"""
Tâches planifiées: analyses, vérifications, clôtures, rapports.
"""
import time
import os
from threading import Thread

import schedule

from .config import logger, client_twilio
from .constants import ACTIFS_PERMANENTS, ACTIFS_HORS_US, est_jour_trading_valide
from .market_context import get_paris_time, get_utc_time_for_paris, get_market_context
from .market_data import recuperer_donnees_marche
from .indicators import enrichir_donnees_avec_indicateurs
from .analysis import analyser_marche_json
from .trades import (
    enregistrer_recommandation, verifier_resultats_trades,
    reevaluer_trades_intraday, cloturer_trades_jour
)
from .journal import (
    enregistrer_journal_fr, enregistrer_journal_complet,
    generer_rapport_hebdo, sauvegarder_analyse
)
from .adjustments import traiter_ajustements_automatiquement
from .ab_testing import evaluer_tous_ab_tests
from .database import backup_database

''' + sched_section + '''
'''

write_file('trading_app/scheduler.py', scheduler_py)

# ============================================================================
# 16. routes/__init__.py
# ============================================================================
routes_init = '''"""
Routes Flask - Enregistrement des blueprints.
"""
from .pages import bp as pages_bp
from .api_market import bp as api_market_bp
from .api_trades import bp as api_trades_bp
from .api_journal import bp as api_journal_bp
from .api_analytics import bp as api_analytics_bp
from .api_adjustments import bp as api_adjustments_bp

def register_blueprints(app):
    """Enregistre tous les blueprints Flask."""
    app.register_blueprint(pages_bp)
    app.register_blueprint(api_market_bp)
    app.register_blueprint(api_trades_bp)
    app.register_blueprint(api_journal_bp)
    app.register_blueprint(api_analytics_bp)
    app.register_blueprint(api_adjustments_bp)
'''

# Create routes directory
import os
os.makedirs('trading_app/routes', exist_ok=True)
write_file('trading_app/routes/__init__.py', routes_init)

# ============================================================================
# 17. routes/pages.py
# ============================================================================
pages_section = extract(7031, 7083)
routes_pages = '''"""
Routes pages (templates HTML).
"""
from flask import Blueprint, render_template

bp = Blueprint('pages', __name__)

''' + pages_section + '''
'''
# Replace @app.route with @bp.route
routes_pages = routes_pages.replace('@app.route', '@bp.route')

write_file('trading_app/routes/pages.py', routes_pages)

# ============================================================================
# 18. routes/api_market.py - Status, data, indicators, analysis, contexte
# ============================================================================
api_market_section = extract(7089, 7430)
routes_api_market = '''"""
API Routes: statut, données marché, indicateurs, analyse.
"""
from flask import Blueprint, jsonify, request

from ..config import TWELVEDATA_API_KEY, NEWSAPI_KEY, logger
from ..constants import ACTIFS_PERMANENTS, ACTIFS_HORS_US, SYMBOLES_ACTIONS_US
from ..market_context import (
    get_paris_time, get_market_context, get_vix_level, get_regime_marche,
    get_session_marche, get_contexte_trading_complet,
    get_evenements_macro_jour, get_actifs_filtres_atr
)
from ..market_data import (
    recuperer_donnees_marche, get_twelvedata_time_series
)
from ..indicators import (
    calculer_indicateurs_techniques, enrichir_donnees_avec_indicateurs
)
from ..analysis import analyser_marche_json, fetch_and_analyze_news
from ..validation import valider_opportunite
from ..trades import enregistrer_recommandation
from ..journal import (
    get_derniere_analyse, get_analyses_du_jour, sauvegarder_analyse
)

bp = Blueprint('api_market', __name__)

''' + api_market_section + '''
'''
routes_api_market = routes_api_market.replace('@app.route', '@bp.route')

write_file('trading_app/routes/api_market.py', routes_api_market)

# ============================================================================
# 19. routes/api_trades.py
# ============================================================================
api_trades_section = extract(7430, 7526) + '\n' + extract(7821, 7892)
routes_api_trades = '''"""
API Routes: trades, performances, top movers, premarket.
"""
from flask import Blueprint, jsonify, request

from ..config import logger, to_python_type, DB_PATH, DB_TIMEOUT
from ..constants import ACTIFS_PERMANENTS, SYMBOLES_ACTIONS_US
from ..market_context import get_paris_time, get_market_context, is_weekend
from ..market_data import recuperer_donnees_marche
from ..trades import (
    get_trades_du_jour, get_performances, verifier_resultats_trades,
    reevaluer_trades_intraday, cloturer_trades_jour
)
from ..journal import recuperer_donnees_premarket

import sqlite3

bp = Blueprint('api_trades', __name__)

''' + api_trades_section + '''
'''
routes_api_trades = routes_api_trades.replace('@app.route', '@bp.route')

write_file('trading_app/routes/api_trades.py', routes_api_trades)

# ============================================================================
# 20. routes/api_journal.py
# ============================================================================
api_journal_section = (
    extract(7527, 7649) + '\n' +  # ab-tests, journal actif, journaux
    extract(7663, 7820) + '\n' +  # cloture, news
    extract(7893, 8029)           # journal-quotidien, historique
)
routes_api_journal = '''"""
API Routes: journal, news, A/B tests, rapports.
"""
from flask import Blueprint, jsonify, request

from ..config import logger, DB_PATH, DB_TIMEOUT
from ..constants import ACTIFS_PERMANENTS
from ..market_context import get_paris_time, get_market_context
from ..analysis import (
    analyser_marche_json, generer_cloture_json,
    fetch_and_analyze_news, get_news_historique
)
from ..market_data import recuperer_donnees_marche
from ..indicators import enrichir_donnees_avec_indicateurs
from ..trades import (
    enregistrer_recommandation, cloturer_trades_jour,
    get_trades_du_jour, get_performances
)
from ..journal import (
    ajouter_entree_journal, get_journal_actif, get_tous_journaux,
    enregistrer_journal_quotidien, generer_bilan_quotidien,
    generer_rapport_hebdo, get_journal_quotidien,
    get_historique_opportunites, sauvegarder_analyse
)
from ..ab_testing import evaluer_ab_test, get_instructions_ab_testing

import sqlite3

bp = Blueprint('api_journal', __name__)

''' + api_journal_section + '''
'''
routes_api_journal = routes_api_journal.replace('@app.route', '@bp.route')

write_file('trading_app/routes/api_journal.py', routes_api_journal)

# ============================================================================
# 21. routes/api_analytics.py
# ============================================================================
api_analytics_section = extract(8030, 8133) + '\n' + extract(8228, 8305) + '\n' + extract(8440, 9133)
routes_api_analytics = '''"""
API Routes: statistiques avancées, equity curve, historique, recherche.
"""
from flask import Blueprint, jsonify, request

from ..config import logger, DB_PATH, DB_TIMEOUT, to_python_type

import sqlite3
import json

bp = Blueprint('api_analytics', __name__)

''' + api_analytics_section + '''
'''
routes_api_analytics = routes_api_analytics.replace('@app.route', '@bp.route')

write_file('trading_app/routes/api_analytics.py', routes_api_analytics)

# ============================================================================
# 22. routes/api_adjustments.py
# ============================================================================
api_adj_section = extract(8133, 8227) + '\n' + extract(8306, 8439)
routes_api_adjustments = '''"""
API Routes: ajustements, critères dynamiques, A/B tests, macro events.
"""
from flask import Blueprint, jsonify, request

from ..config import logger, DB_PATH, DB_TIMEOUT
from ..market_context import (
    get_paris_time, get_regime_marche, get_contexte_trading_complet,
    get_evenements_macro_jour, get_actifs_filtres_atr
)
from ..adjustments import (
    get_ajustements_en_attente, valider_ajustement, valider_tous_ajustements,
    get_historique_ajustements, get_criteres_dynamiques_actifs
)

import sqlite3

bp = Blueprint('api_adjustments', __name__)

''' + api_adj_section + '''
'''
routes_api_adjustments = routes_api_adjustments.replace('@app.route', '@bp.route')

write_file('trading_app/routes/api_adjustments.py', routes_api_adjustments)

# ============================================================================
# 23. __init__.py for trading_app package
# ============================================================================
write_file('trading_app/__init__.py', '"""Trading App Package"""\n')

print(f"\n✅ Split terminé! {total} lignes → modules créés")
print("⚠️  Les modules nécessitent des ajustements d'imports manuels")
