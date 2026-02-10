"""
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
try:
    from twilio.rest import Client as TwilioClient
except ImportError:
    TwilioClient = None

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

try:
    client_anthropic = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
except Exception:
    client_anthropic = None
client_twilio = None
if TwilioClient and os.environ.get("TWILIO_ACCOUNT_SID"):
    client_twilio = TwilioClient(
        os.environ.get("TWILIO_ACCOUNT_SID"),
        os.environ.get("TWILIO_AUTH_TOKEN")
    )

# ============================================================================
# VARIABLES GLOBALES
# ============================================================================

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

# Scheduler health tracking (écrit par le thread scheduler, lu par l'API)
SCHEDULER_HEALTH = {'alive': False, 'started_at': None, 'last_heartbeat': None, 'last_analysis': None, 'total_cycles': 0, 'total_errors': 0}

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
