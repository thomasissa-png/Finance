#!/usr/bin/env python3
"""Reconstruit les fichiers routes/ à partir du monolithe original."""
import re
import os

with open('/tmp/original_app.py', 'r') as f:
    lines = f.read().split('\n')

def extract_routes(start_line, end_line):
    """Extract lines from original (1-indexed) and convert @app.route to @bp.route"""
    code = '\n'.join(lines[start_line-1:end_line])
    code = code.replace('@app.route', '@bp.route')
    return code

def write_module(path, content):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    # Verify syntax
    import py_compile
    try:
        py_compile.compile(path, doraise=True)
        print(f"  ✅ {path} ({len(content.splitlines())} lignes)")
    except py_compile.PyCompileError as e:
        print(f"  ❌ {path}: SYNTAX ERROR - {e}")

os.makedirs('trading_app/routes', exist_ok=True)

# ============================================================================
# pages.py - Template routes (lines 6999-7052)
# ============================================================================
pages_code = extract_routes(6999, 7052)
write_module('trading_app/routes/pages.py', f'''"""Routes pages (templates HTML)."""
from flask import Blueprint, render_template

bp = Blueprint('pages', __name__)

{pages_code}
''')

# ============================================================================
# api_market.py - Status, data, indicators, analysis (lines 7057-7397)
# ============================================================================
api_market_code = extract_routes(7057, 7397)
write_module('trading_app/routes/api_market.py', f'''"""API Routes: statut, données marché, indicateurs, analyse, trades ouverts."""
import sqlite3
import json
from flask import Blueprint, jsonify, request

from ..config import TWELVEDATA_API_KEY, NEWSAPI_KEY, logger, DB_PATH, DB_TIMEOUT, to_python_type
from ..constants import ACTIFS_PERMANENTS, ACTIFS_HORS_US, SYMBOLES_ACTIONS_US
from ..market_context import (
    get_paris_time, get_market_context, get_vix_level, get_regime_marche,
    get_session_marche, get_contexte_trading_complet,
    get_evenements_macro_jour, get_actifs_filtres_atr, is_weekend
)
from ..market_data import recuperer_donnees_marche, get_twelvedata_time_series
from ..indicators import calculer_indicateurs_techniques, enrichir_donnees_avec_indicateurs
from ..analysis import analyser_marche_json, fetch_and_analyze_news
from ..validation import valider_opportunite
from ..trades import enregistrer_recommandation, get_trades_du_jour, get_performances
from ..journal import (
    get_derniere_analyse, get_analyses_du_jour, sauvegarder_analyse,
    get_categorie_actif
)
from ..adjustments import generer_instructions_dynamiques
from ..ab_testing import get_instructions_ab_testing

bp = Blueprint('api_market', __name__)

{api_market_code}
''')

# ============================================================================
# api_trades.py - Trade stats, performances, top movers, premarket, verification
# Lines 7398-7494 + 7789-7841 + 8033-8055 + 8138-8176
# ============================================================================
api_trades_code = (
    extract_routes(7398, 7494) + '\n\n' +
    extract_routes(7789, 7841) + '\n\n' +
    extract_routes(8033, 8055) + '\n\n' +
    extract_routes(8138, 8176)
)
write_module('trading_app/routes/api_trades.py', f'''"""API Routes: trades, performances, top movers, premarket, vérification."""
import sqlite3
import json
from flask import Blueprint, jsonify, request

from ..config import logger, to_python_type, DB_PATH, DB_TIMEOUT
from ..constants import ACTIFS_PERMANENTS, SYMBOLES_ACTIONS_US
from ..market_context import (
    get_paris_time, get_market_context, is_weekend,
    get_regime_marche, get_contexte_trading_complet
)
from ..market_data import recuperer_donnees_marche
from ..trades import (
    get_trades_du_jour, get_performances, verifier_resultats_trades,
    reevaluer_trades_intraday, cloturer_trades_jour
)
from ..journal import recuperer_donnees_premarket

bp = Blueprint('api_trades', __name__)

{api_trades_code}
''')

# ============================================================================
# api_journal.py - Journal, news, A/B tests, reports, cloture
# Lines 7495-7630 + 7631-7978 + 7979-8032 + 8056-8137
# ============================================================================
api_journal_code = (
    extract_routes(7495, 7630) + '\n\n' +
    extract_routes(7631, 7978) + '\n\n' +
    extract_routes(7979, 8032) + '\n\n' +
    extract_routes(8056, 8137)
)
write_module('trading_app/routes/api_journal.py', f'''"""API Routes: journal, news, A/B tests, rapports, clôture."""
import sqlite3
import json
from flask import Blueprint, jsonify, request

from ..config import logger, client_anthropic, DB_PATH, DB_TIMEOUT, to_python_type
from ..constants import ACTIFS_PERMANENTS, ACTIFS_HORS_US
from ..market_context import (
    get_paris_time, get_market_context, get_regime_marche,
    get_evenements_macro_jour, get_actifs_filtres_atr
)
from ..market_data import recuperer_donnees_marche
from ..indicators import enrichir_donnees_avec_indicateurs
from ..analysis import (
    analyser_marche_json, generer_cloture_json,
    fetch_and_analyze_news, get_news_historique
)
from ..trades import (
    enregistrer_recommandation, cloturer_trades_jour,
    get_trades_du_jour, get_performances
)
from ..journal import (
    ajouter_entree_journal, get_journal_actif, get_tous_journaux,
    enregistrer_journal_quotidien, generer_bilan_quotidien,
    generer_rapport_hebdo, get_journal_quotidien,
    get_historique_opportunites, sauvegarder_analyse,
    recuperer_donnees_premarket, get_analyses_du_jour
)
from ..ab_testing import evaluer_ab_test, get_instructions_ab_testing

bp = Blueprint('api_journal', __name__)

{api_journal_code}
''')

# ============================================================================
# api_adjustments.py - Adjustments, criteria, regime, contexte
# Lines 8152-8388
# ============================================================================
api_adj_code = extract_routes(8152, 8388)
write_module('trading_app/routes/api_adjustments.py', f'''"""API Routes: ajustements, critères dynamiques, régime marché."""
import sqlite3
import json
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

bp = Blueprint('api_adjustments', __name__)

{api_adj_code}
''')

# ============================================================================
# api_analytics.py - Stats détaillées, equity curve, historique, journal search
# Lines 8177-8254 + 8389-9082
# ============================================================================
api_analytics_code = (
    extract_routes(8177, 8254) + '\n\n' +
    extract_routes(8389, 9082)
)
write_module('trading_app/routes/api_analytics.py', f'''"""API Routes: statistiques avancées, equity curve, historique, recherche."""
import sqlite3
import json
from flask import Blueprint, jsonify, request

from ..config import logger, DB_PATH, DB_TIMEOUT, to_python_type

bp = Blueprint('api_analytics', __name__)

{api_analytics_code}
''')

print("\n✅ Toutes les routes recréées!")
