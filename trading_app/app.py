"""
Agent Trading - Interface Web Complète
Application Flask avec interface dynamique pour le trading.

Architecture modulaire:
- config.py: Flask app, clients API, état global, locks
- constants.py: Actifs, symboles, mappings, jours fériés
- database.py: Schéma SQLite, init, backup
- market_context.py: Horloge, VIX, sessions, régime, grading, macro
- market_data.py: TwelveData, Yahoo Finance, batch, rate limiting
- indicators.py: RSI, MACD, ATR, tendances
- validation.py: Extraction JSON, validation opportunités
- prompts.py: System prompts Claude
- analysis.py: Analyse marché, news
- trades.py: Enregistrement, vérification, clôture trades
- journal.py: Journal, rapports quotidiens/hebdomadaires
- adjustments.py: Ajustements stratégiques, feedback
- ab_testing.py: Tests A/B
- notifications.py: WhatsApp
- scheduler.py: Tâches planifiées
- routes/: Blueprints Flask (pages, API)
"""
import time
from threading import Thread

from trading_app.config import app, logger
from trading_app.database import init_database, create_database_indexes
from trading_app.market_context import get_paris_time, load_vix_cache
from trading_app.market_data import recuperer_donnees_marche
from trading_app.constants import ACTIFS_PERMANENTS, est_jour_trading_valide
from trading_app.ab_testing import init_ab_test_table, charger_ab_tests_actifs
from trading_app.scheduler import configurer_schedule, run_scheduler, executer_analyse_planifiee
from trading_app.routes import register_blueprints

# ============================================================================
# SÉCURITÉ - Headers
# ============================================================================

@app.after_request
def add_security_headers(response):
    """Ajoute des headers de sécurité à toutes les réponses"""
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response

# ============================================================================
# ENREGISTREMENT DES BLUEPRINTS
# ============================================================================

register_blueprints(app)

# ============================================================================
# RATTRAPAGE ANALYSE AU DÉMARRAGE
# ============================================================================

def rattrapage_analyse_demarrage():
    """Si l'app démarre peu après une analyse planifiée, rattraper l'analyse manquée."""
    try:
        maintenant = get_paris_time()
        is_valide, _ = est_jour_trading_valide(maintenant)
        if not is_valide:
            return

        heure_decimal = maintenant.hour + maintenant.minute / 60

        analyses_planifiees = [
            (8.0, 8.25, True),
            (9.25, 9.5, True),
            (12.0, 12.25, True),
            (15.583, 15.833, False),
            (17.0, 17.25, False),
        ]

        for debut, fin, eu_only in analyses_planifiees:
            if debut <= heure_decimal <= fin:
                print(f"[{maintenant.strftime('%H:%M:%S')} CET] 🔄 Rattrapage analyse planifiée...")
                executer_analyse_planifiee(eu_only=eu_only)
                return
    except Exception as e:
        print(f"⚠️ Erreur rattrapage analyse: {e}")

# ============================================================================
# PRÉ-CHARGEMENT CACHE AU DÉMARRAGE
# ============================================================================

def precharger_donnees_marche():
    """Pré-charge les données de marché au démarrage pour un dashboard instantané."""
    try:
        print("🚀 Pré-chargement des données de marché en cours...")
        start_time = time.time()
        donnees = recuperer_donnees_marche(ACTIFS_PERMANENTS, inclure_indicateurs=True)
        elapsed = time.time() - start_time
        print(f"✅ Pré-chargement terminé: {len(donnees)} actifs en {elapsed:.1f}s")
    except Exception as e:
        print(f"⚠️ Erreur pré-chargement: {e}")

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
║  ⏰ Analyses AUTO: 8h, 9h15, 12h, 15h35, 17h CET              ║
║  🌙 Clôture: 22h CET                                          ║
║                                                               ║
║  📊 Architecture modulaire (22 modules)                       ║
╚══════════════════════════════════════════════════════════════╝
    """)
    app.run(host='0.0.0.0', port=5000, debug=False)

# ============================================================================
# INITIALISATION AU NIVEAU MODULE
# ============================================================================

init_database()
create_database_indexes()
init_ab_test_table()
charger_ab_tests_actifs()
load_vix_cache()
configurer_schedule()

scheduler_thread = Thread(target=run_scheduler, daemon=True)
scheduler_thread.start()

preload_thread = Thread(target=precharger_donnees_marche, daemon=True)
preload_thread.start()

def _rattrapage_differe():
    time.sleep(20)
    rattrapage_analyse_demarrage()

rattrapage_thread = Thread(target=_rattrapage_differe, daemon=True)
rattrapage_thread.start()

if __name__ == "__main__":
    start_app()
