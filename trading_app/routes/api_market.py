"""API Routes: statut, données marché, indicateurs, analyse, trades ouverts."""
import sqlite3
import json
import requests
from flask import Blueprint, jsonify, request

from ..config import TWELVEDATA_API_KEY, NEWSAPI_KEY, logger, DB_PATH, DB_TIMEOUT, to_python_type, SCHEDULER_HEALTH
from .. import config as _config
from ..constants import ACTIFS_PERMANENTS, ACTIFS_HORS_US, SYMBOLES_ACTIONS_US
from ..market_context import (
    get_paris_time, get_market_context, get_vix_level, get_regime_marche,
    get_session_marche, get_contexte_trading_complet,
    get_evenements_macro_jour, get_actifs_filtres_atr, is_weekend
)
from ..market_data import recuperer_donnees_marche, get_twelvedata_time_series, convert_symbol_to_twelvedata, rate_limit_twelvedata
from ..indicators import calculer_indicateurs_techniques, enrichir_donnees_avec_indicateurs
from ..analysis import analyser_marche_json, fetch_and_analyze_news
from ..validation import valider_opportunite, valider_setup_avant_trade
from ..trades import enregistrer_recommandation, get_trades_du_jour, get_performances, enrichir_opportunite_avec_donnees_marche
from ..journal import (
    get_derniere_analyse, get_analyses_du_jour, sauvegarder_analyse,
    get_categorie_actif
)
from ..adjustments import generer_instructions_dynamiques
from ..ab_testing import get_instructions_ab_testing

bp = Blueprint('api_market', __name__)

@bp.route('/api/status')
def api_status():
    """Statut de l'application"""
    try:
        maintenant = get_paris_time()
        marche_type, marche_info = get_market_context()

        # Compter les news du jour depuis la DB (remplace le compteur in-memory cassé)
        news_today = 0
        try:
            conn_status = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
            cursor_status = conn_status.cursor()
            cursor_status.execute("SELECT COUNT(*) FROM alertes_news WHERE date = ?", (maintenant.strftime('%Y-%m-%d'),))
            news_today = cursor_status.fetchone()[0]
            conn_status.close()
        except Exception:
            pass

        return jsonify({
            'status': 'online',
            'datetime': maintenant.strftime('%d/%m/%Y %H:%M:%S'),
            'timezone': 'Europe/Paris',
            'marche': marche_type,
            'marche_info': marche_info,
            'news_envoyees': news_today,
            'twelvedata_configured': bool(TWELVEDATA_API_KEY)
        })
    except Exception as e:
        logger.error(f"Erreur api_status: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 500

@bp.route('/api/twelvedata/test')
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

@bp.route('/api/twelvedata/symbol/<path:symbole>')
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

@bp.route('/api/donnees-marche')
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

@bp.route('/api/indicateurs/<symbole>')
def api_indicateurs(symbole):
    """Récupère les indicateurs techniques d'un actif"""
    try:
        indicateurs = calculer_indicateurs_techniques(symbole)
        if indicateurs:
            return jsonify({'success': True, 'indicateurs': indicateurs})
        return jsonify({'success': False, 'error': 'Données non disponibles'})
    except Exception as e:
        logger.error(f"Erreur api_indicateurs({symbole}): {e}")
        return jsonify({'success': False, 'error': str(e)})

@bp.route('/api/analyse')
def api_lancer_analyse():
    """Lance une analyse de marché"""
    try:
        weekend = is_weekend()

        # Weekend: pas d'analyse active, retourner synthèse de la semaine
        if weekend:
            # Récupérer le résumé de la semaine dernière
            conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
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
            symboles_traites = set()  # Déduplication

            # Récupérer le régime de marché actuel pour adapter les validations
            vix_actuel = get_vix_level()
            regime_actuel, _, _ = get_regime_marche(vix_actuel)

            # Conviction minimale adaptée au régime de marché
            conviction_min_par_regime = {
                'CALME': 2,      # Marché calme: plus souple
                'NORMAL': 3,     # Standard
                'VOLATILE': 4,   # Exiger plus de conviction
                'EXTREME': 5     # Uniquement conviction max
            }
            conviction_min = conviction_min_par_regime.get(regime_actuel, 3)

            # Liste des actifs exclus pour validation ATR
            actifs_exclus_atr = set(a['nom'] for a in get_actifs_filtres_atr(donnees_enrichies, seuil_atr_min=1.0))

            for opp in analyse.get('opportunites', []):
                symbole = opp.get('symbole', opp.get('actif', 'N/A'))
                actif_nom = opp.get('actif', '')

                # Validation 0: Déduplication - skip si déjà traité
                if symbole in symboles_traites:
                    print(f"⚠️ [{symbole}] Opportunité dupliquée ignorée")
                    continue

                # Validation 1: ATR - Claude a pu ignorer les exclusions
                if actif_nom in actifs_exclus_atr or symbole in actifs_exclus_atr:
                    print(f"⚠️ [{symbole}] Opportunité rejetée: ATR < 1% (exclus)")
                    opportunites_rejetees += 1
                    continue

                # Validation 2: Conviction adaptée au régime
                # Si conviction manquante, estimer basé sur d'autres indicateurs
                conviction = opp.get('conviction_score')
                if conviction is None:
                    # Estimer conviction basée sur confluence/alignement
                    confluence = opp.get('confluence_score', 5)
                    alignement = opp.get('alignement_tf', 1)
                    conviction = min(5, max(1, (confluence // 2) + alignement))
                    opp['conviction_score'] = conviction
                    print(f"ℹ️ [{symbole}] Conviction estimée: {conviction} (confluence={confluence}, alignement={alignement})")

                if conviction < conviction_min:
                    print(f"⚠️ [{symbole}] Opportunité rejetée: conviction {conviction} < {conviction_min} (régime {regime_actuel})")
                    opportunites_rejetees += 1
                    continue

                # Injecter le régime réel pour validation R:R adaptée
                opp['regime_marche'] = regime_actuel

                try:
                    # Validation 3: Ratio R:R, cohérence prix, heures marché
                    valide, opp_validee, warns, errs = valider_opportunite(opp)
                    if not valide:
                        print(f"⚠️ [{symbole}] Opportunité rejetée par validation: {'; '.join(errs)}")
                        opportunites_rejetees += 1
                        continue
                    if warns:
                        print(f"ℹ️ [{symbole}] Avertissements: {'; '.join(warns)}")

                    opp_enrichie = enrichir_opportunite_avec_donnees_marche(opp_validee, donnees_enrichies, indicateurs)

                    # Validation 4: Vérifier prix réel avant enregistrement
                    prix_entree_prevu = float(opp_validee.get('entree', 0))
                    setup_ok, setup_raison, quote_fraiche = valider_setup_avant_trade(
                        symbole, opp_validee.get('direction', 'LONG'), prix_entree_prevu, donnees_enrichies
                    )
                    if not setup_ok:
                        print(f"⚠️ [{symbole}] Setup rejeté: {setup_raison}")
                        opportunites_rejetees += 1
                        continue
                    # Utiliser le prix RÉEL du marché comme prix d'entrée (pas le prix suggéré par Claude)
                    if quote_fraiche:
                        opp_enrichie['prix_actuel'] = quote_fraiche['prix']
                        opp_enrichie['entree'] = quote_fraiche['prix']

                    result = enregistrer_recommandation(opp_enrichie)

                    if result:
                        symboles_traites.add(symbole)
                        opportunites_valides += 1
                    else:
                        opportunites_rejetees += 1
                except Exception as e:
                    logger.error(f"Erreur traitement opportunité {symbole}: {e}")
                    opportunites_rejetees += 1

            if opportunites_rejetees > 0 or opportunites_valides > 0:
                print(f"📊 Bilan opportunités (régime {regime_actuel}): {opportunites_valides} validées, {opportunites_rejetees} rejetées")

            return jsonify({
                'success': True,
                'datetime': get_paris_time().strftime('%d/%m/%Y %H:%M:%S'),
                'analyse': analyse,
                'weekend': False
            })
        return jsonify({'success': False, 'error': 'Analyse échouée'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@bp.route('/api/derniere-analyse')
def api_derniere_analyse():
    """Récupère la dernière analyse"""
    try:
        analyse = get_derniere_analyse()
        if analyse:
            return jsonify({'success': True, 'analyse': analyse})
        return jsonify({'success': False, 'error': 'Aucune analyse trouvée'})
    except Exception as e:
        logger.error(f"Erreur api_derniere_analyse: {e}")
        return jsonify({'success': False, 'error': str(e)})

@bp.route('/api/analyses-jour')
def api_analyses_jour():
    """Récupère toutes les analyses du jour"""
    try:
        analyses = get_analyses_du_jour()
        return jsonify({
            'success': True,
            'date': get_paris_time().strftime('%d/%m/%Y'),
            'analyses': analyses
        })
    except Exception as e:
        logger.error(f"Erreur api_analyses_jour: {e}")
        return jsonify({'success': False, 'error': str(e)})

@bp.route('/api/trades-jour')
def api_trades_jour():
    """Récupère les trades du jour"""
    try:
        trades = get_trades_du_jour()
        return jsonify({
            'success': True,
            'date': get_paris_time().strftime('%d/%m/%Y'),
            'trades': trades
        })
    except Exception as e:
        logger.error(f"Erreur api_trades_jour: {e}")
        return jsonify({'success': False, 'error': str(e)})

@bp.route('/api/trades/ouverts')
def api_trades_ouverts():
    """Récupère les trades ouverts avec leur tracking temps réel"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('''
            SELECT id, actif, symbole, direction, prix_entree, prix_stop, prix_tp1, prix_tp2,
                   timestamp_reco, prix_max_atteint, prix_min_atteint, prix_dernier_check,
                   timestamp_dernier_check, pnl_max, pnl_min, nb_checks,
                   type_setup, catalyseur, duree_estimee, ratio_rr,
                   atr_pct_reco, volume_relatif_reco, validite_minutes, heure_expiration,
                   conviction_score, trade_grade, grade_setup_score, heure_message, prix_actuel
            FROM trades_recommandes
            WHERE resultat IS NULL AND symbole IS NOT NULL AND symbole != ''
            ORDER BY timestamp_reco DESC
        ''')

        trades = []
        for row in cursor.fetchall():
            trade = dict(row)
            entree = float(trade.get('prix_entree') or 0)
            prix_actuel = float(trade.get('prix_dernier_check') or trade.get('prix_actuel') or entree)
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

@bp.route('/api/scheduler-status')
def api_scheduler_status():
    """Statut du scheduler pour diagnostic"""
    import schedule as _schedule
    health = _config.SCHEDULER_HEALTH

    # Compter les jobs par type
    jobs_summary = {}
    for job in _schedule.get_jobs():
        func_name = getattr(job.job_func, '__name__', '?')
        if hasattr(job.job_func, 'func'):
            func_name = job.job_func.func.__name__
        jobs_summary[func_name] = jobs_summary.get(func_name, 0) + 1

    # Prochains jobs à exécuter
    upcoming = sorted(
        [j for j in _schedule.get_jobs() if j.next_run],
        key=lambda j: j.next_run
    )[:10]
    next_jobs = []
    for j in upcoming:
        func_name = getattr(j.job_func, '__name__', '?')
        if hasattr(j.job_func, 'func'):
            func_name = j.job_func.func.__name__
        next_jobs.append({
            'function': func_name,
            'next_run': j.next_run.strftime('%Y-%m-%d %H:%M'),
        })

    return jsonify({
        'success': True,
        'scheduler': {
            'alive': health.get('alive', False),
            'started_at': health.get('started_at'),
            'last_heartbeat': health.get('last_heartbeat'),
            'last_analysis': health.get('last_analysis'),
            'total_cycles': health.get('total_cycles', 0),
            'total_errors': health.get('total_errors', 0),
        },
        'jobs_count': len(_schedule.get_jobs()),
        'jobs_summary': jobs_summary,
        'next_10_jobs': next_jobs,
        'datetime': get_paris_time().strftime('%d/%m/%Y %H:%M:%S'),
    })

