"""API Routes: journal, news, A/B tests, rapports, clôture."""
import sqlite3
import json
import requests
from datetime import datetime
from flask import Blueprint, jsonify, request

import time

from ..config import logger, client_anthropic, DB_PATH, DB_TIMEOUT, to_python_type, TZ_PARIS, NEWSAPI_KEY, NEWS_CACHE, NEWS_CACHE_TTL
from ..constants import ACTIFS_PERMANENTS, ACTIFS_HORS_US, POOL_ROTATION
from ..market_context import (
    get_paris_time, get_market_context, get_regime_marche,
    get_evenements_macro_jour, get_actifs_filtres_atr, is_weekend,
    verifier_proximite_evenement_macro
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

@bp.route('/api/ab-tests')
def api_ab_tests():
    """Récupère les tests A/B actifs avec leurs performances"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Récupérer tous les tests A/B
        cursor.execute('''
            SELECT * FROM ab_tests
            ORDER BY date_debut DESC
        ''')
        tests = [dict(row) for row in cursor.fetchall()]

        # Pour chaque test, calculer les performances par groupe
        for test in tests:
            test_id = test['id']
            groupe_a = json.loads(test.get('actifs_groupe_a', '[]'))
            groupe_b = json.loads(test.get('actifs_groupe_b', '[]'))

            # Stats groupe A
            if groupe_a:
                placeholders = ','.join(['?' for _ in groupe_a])
                cursor.execute(f'''
                    SELECT COUNT(*) as total,
                           SUM(CASE WHEN resultat IN ('TP1', 'TP2') THEN 1 ELSE 0 END) as wins,
                           AVG(pnl_pct) as pnl_moyen,
                           SUM(pnl_pct) as pnl_total
                    FROM trades_recommandes
                    WHERE ab_test_id = ? AND ab_groupe = 'A'
                ''', (test_id,))
                stats_a = dict(cursor.fetchone())
                test['stats_groupe_a'] = {
                    'trades': stats_a['total'] or 0,
                    'win_rate': round((stats_a['wins'] or 0) / stats_a['total'] * 100, 1) if stats_a['total'] else 0,
                    'pnl_moyen': round(stats_a['pnl_moyen'] or 0, 2),
                    'pnl_total': round(stats_a['pnl_total'] or 0, 2)
                }
            else:
                test['stats_groupe_a'] = {'trades': 0, 'win_rate': 0, 'pnl_moyen': 0, 'pnl_total': 0}

            # Stats groupe B
            if groupe_b:
                cursor.execute(f'''
                    SELECT COUNT(*) as total,
                           SUM(CASE WHEN resultat IN ('TP1', 'TP2') THEN 1 ELSE 0 END) as wins,
                           AVG(pnl_pct) as pnl_moyen,
                           SUM(pnl_pct) as pnl_total
                    FROM trades_recommandes
                    WHERE ab_test_id = ? AND ab_groupe = 'B'
                ''', (test_id,))
                stats_b = dict(cursor.fetchone())
                test['stats_groupe_b'] = {
                    'trades': stats_b['total'] or 0,
                    'win_rate': round((stats_b['wins'] or 0) / stats_b['total'] * 100, 1) if stats_b['total'] else 0,
                    'pnl_moyen': round(stats_b['pnl_moyen'] or 0, 2),
                    'pnl_total': round(stats_b['pnl_total'] or 0, 2)
                }
            else:
                test['stats_groupe_b'] = {'trades': 0, 'win_rate': 0, 'pnl_moyen': 0, 'pnl_total': 0}

        conn.close()

        # Séparer tests actifs et terminés
        tests_actifs = [t for t in tests if t['statut'] == 'actif']
        tests_termines = [t for t in tests if t['statut'] != 'actif']

        return jsonify({
            'success': True,
            'tests_actifs': tests_actifs,
            'tests_termines': tests_termines[:5],  # 5 derniers terminés
            'total_actifs': len(tests_actifs)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@bp.route('/api/journal/<symbole>')
def api_journal(symbole):
    """Récupère le journal d'un actif"""
    try:
        entries = get_journal_actif(symbole)
        return jsonify({
            'success': True,
            'symbole': symbole,
            'entries': entries
        })
    except Exception as e:
        logger.error(f"Erreur api_journal({symbole}): {e}")
        return jsonify({'success': False, 'error': str(e)})

@bp.route('/api/journal', methods=['POST'])
def api_ajouter_journal():
    """Ajoute une entrée au journal"""
    try:
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
    except Exception as e:
        logger.error(f"Erreur api_ajouter_journal: {e}")
        return jsonify({'success': False, 'error': str(e)})

@bp.route('/api/journaux')
def api_tous_journaux():
    """Liste tous les actifs avec journal"""
    try:
        actifs = get_tous_journaux()
        return jsonify({
            'success': True,
            'actifs': actifs
        })
    except Exception as e:
        logger.error(f"Erreur api_tous_journaux: {e}")
        return jsonify({'success': False, 'error': str(e)})

@bp.route('/api/actifs')
def api_actifs():
    """Liste tous les actifs suivis"""
    try:
        return jsonify({
            'success': True,
            'permanents': ACTIFS_PERMANENTS,
            'rotation': POOL_ROTATION
        })
    except Exception as e:
        logger.error(f"Erreur api_actifs: {e}")
        return jsonify({'success': False, 'error': str(e)})


@bp.route('/api/cloture')
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

@bp.route('/api/news')
def api_news():
    """Récupère et analyse les actualités pour leur impact trading.
    OPTIMISÉ: Cache de 5 minutes pour éviter appels NewsAPI répétés."""
    # Vérifier le cache d'abord
    now = time.time()
    if NEWS_CACHE['data'] and (now - NEWS_CACHE['timestamp']) < NEWS_CACHE_TTL:
        return jsonify({
            'success': True,
            'datetime': get_paris_time().strftime('%d/%m/%Y %H:%M:%S'),
            'news': NEWS_CACHE['data'],
            'count': len(NEWS_CACHE['data']),
            'cached': True
        })

    if not NEWSAPI_KEY:
        # Fallback: retourner les news de la BDD si pas de clé API
        news_db = get_news_historique(jours=1)
        if news_db:
            return jsonify({
                'success': True,
                'datetime': get_paris_time().strftime('%d/%m/%Y %H:%M:%S'),
                'news': news_db,
                'count': len(news_db),
                'source': 'database'
            })
        return jsonify({'success': False, 'error': 'NEWSAPI_KEY non configurée'})

    try:
        # Récupérer et analyser les news
        news_analysees, error = fetch_and_analyze_news()

        if error:
            # Fallback: retourner les news en cache ou BDD
            if NEWS_CACHE['data']:
                return jsonify({
                    'success': True,
                    'news': NEWS_CACHE['data'],
                    'count': len(NEWS_CACHE['data']),
                    'cached': True,
                    'warning': error
                })
            return jsonify({'success': False, 'error': error})

        # Mettre en cache
        NEWS_CACHE['data'] = news_analysees
        NEWS_CACHE['timestamp'] = now

        return jsonify({
            'success': True,
            'datetime': get_paris_time().strftime('%d/%m/%Y %H:%M:%S'),
            'news': news_analysees,
            'count': len(news_analysees)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@bp.route('/api/news/raw')
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

@bp.route('/api/news/historique')
def api_news_historique():
    """Récupère l'historique des news analysées avec navigation par jour"""
    try:
        jours = request.args.get('jours', 7, type=int)
        news = get_news_historique(jours)

        # Grouper par date
        par_date = {}
        for n in news:
            date_str = str(n.get('date', ''))
            if date_str not in par_date:
                par_date[date_str] = []
            par_date[date_str].append({
                'heure': n.get('heure', '--:--'),
                'headline': n.get('titre', ''),
                'analyse': n.get('contenu', ''),
                'impact': n.get('impact', 'faible'),
                'actifs': n.get('actif', '').split(', ') if n.get('actif') else []
            })

        # Convertir en liste triée par date
        dates_triees = sorted(par_date.keys(), reverse=True)
        historique = [{'date': d, 'news': par_date[d]} for d in dates_triees]

        return jsonify({
            'success': True,
            'historique': historique,
            'nb_jours': len(historique)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@bp.route('/api/journal-quotidien')
def api_journal_quotidien():
    """Récupère le journal quotidien avec navigation par date et pagination"""
    try:
        symbole = request.args.get('symbole')
        limite = request.args.get('limite', 50, type=int)
        limite = min(limite, 200)  # Max 200 entrées
        page = request.args.get('page', 0, type=int)
        offset = page * limite
        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')

        result = get_journal_quotidien(
            symbole=symbole,
            limite=limite,
            date_from=date_from,
            date_to=date_to,
            offset=offset
        )

        total_pages = (result['total_count'] + limite - 1) // limite if result['total_count'] > 0 else 0

        return jsonify({
            'success': True,
            'entries': result['entries'],
            'pagination': {
                'page': page,
                'limit': limite,
                'total_count': result['total_count'],
                'total_pages': total_pages,
                'has_next': page < total_pages - 1,
                'has_prev': page > 0
            },
            'json_errors_count': len(result['json_errors'])
        })
    except Exception as e:
        logger.error(f"Erreur api_journal_quotidien: {e}")
        return jsonify({'success': False, 'error': str(e)})

@bp.route('/api/journal-quotidien/generer', methods=['POST'])
def api_generer_journal_quotidien():
    """Force la génération du journal quotidien"""
    try:
        success = enregistrer_journal_quotidien()
        return jsonify({'success': success})
    except Exception as e:
        logger.error(f"Erreur api_generer_journal_quotidien: {e}")
        return jsonify({'success': False, 'error': str(e)})

@bp.route('/api/historique-opportunites/<symbole>')
def api_historique_opportunites(symbole):
    """Récupère l'historique des opportunités pour un actif"""
    try:
        trades = get_historique_opportunites(symbole)
        return jsonify({
            'success': True,
            'symbole': symbole,
            'trades': trades
        })
    except Exception as e:
        logger.error(f"Erreur api_historique_opportunites({symbole}): {e}")
        return jsonify({'success': False, 'error': str(e)})

@bp.route('/api/stats-evolution')
def api_stats_evolution():
    """Récupère l'évolution des stats pour le graphique"""
    try:
        granularite = request.args.get('granularite', 'jour')  # jour, semaine, mois
        limite = request.args.get('limite', 30, type=int)

        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        if granularite == 'jour':
            cursor.execute('''
                SELECT date,
                       COUNT(*) as nb_trades,
                       SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                       SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as stops,
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
                       SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                       SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as stops,
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
                       SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                       SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as stops,
                       SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl_cumule
                FROM trades_recommandes
                GROUP BY strftime('%Y-%m', date)
                ORDER BY periode DESC
                LIMIT ?
            ''', (limite,))

        # Récupérer les données (triées DESC) et les inverser pour ordre chronologique
        raw_data = [dict(row) for row in cursor.fetchall()]
        raw_data.reverse()  # Maintenant en ordre chronologique (plus ancien en premier)

        conn.close()

        # Calculer le cumul dans l'ordre chronologique correct
        stats = []
        pnl_running = 0
        for data in raw_data:
            pnl_running += data['pnl_cumule'] or 0
            data['pnl_cumule_running'] = round(pnl_running, 2)
            conclus = (data['reussis'] or 0) + (data['stops'] or 0)
            data['taux_reussite'] = round((data['reussis'] / conclus * 100) if conclus > 0 else 0, 1)
            stats.append(data)

        return jsonify({
            'success': True,
            'granularite': granularite,
            'stats': stats
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@bp.route('/api/bilan-quotidien')
def api_bilan_quotidien():
    """Récupère le bilan quotidien"""
    try:
        date_str = request.args.get('date')
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
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

@bp.route('/api/bilan-quotidien/generer', methods=['POST'])
def api_generer_bilan():
    """Force la génération du bilan quotidien"""
    bilan = generer_bilan_quotidien()
    if bilan:
        return jsonify({'success': True, 'bilan': bilan})
    return jsonify({'success': False, 'error': 'Erreur génération'})


@bp.route('/api/evenements-macro')
def api_evenements_macro():
    """Récupère les événements macro du jour et vérifie la proximité"""
    try:
        weekend = is_weekend()
        evenements = get_evenements_macro_jour()  # Retourne [] le weekend

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
            'message_weekend': "Marchés fermés le weekend" if weekend else None
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@bp.route('/api/actifs-faible-atr')
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

@bp.route('/api/rapport-hebdo')
def api_rapport_hebdo():
    """Récupère le dernier rapport hebdomadaire"""
    try:
        semaine = request.args.get('semaine')
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
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

@bp.route('/api/rapport-hebdo/generer', methods=['POST'])
def api_generer_rapport_hebdo():
    """Force la génération du rapport hebdomadaire"""
    rapport = generer_rapport_hebdo()
    if rapport:
        return jsonify({'success': True, 'rapport': rapport})
    return jsonify({'success': False, 'error': 'Erreur génération'})

