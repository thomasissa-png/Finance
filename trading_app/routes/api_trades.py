"""API Routes: trades, performances, top movers, premarket, vérification."""
import sqlite3
import json
from datetime import timedelta
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

bp = Blueprint('api_trades', __name__)

@bp.route('/api/trades/stats-detaillees')
def api_trades_stats_detaillees():
    """Statistiques détaillées des trades avec breakdown par catégorie"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        periode = request.args.get('periode', 'semaine')
        maintenant = get_paris_time()

        if periode == 'jour':
            date_debut = maintenant.date()
        elif periode == 'semaine':
            date_debut = (maintenant - timedelta(days=7)).date()
        elif periode == 'mois':
            date_debut = (maintenant - timedelta(days=30)).date()
        else:
            date_debut = (maintenant - timedelta(days=365)).date()

        # Stats globales
        cursor.execute('''
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as stops,
                AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as pnl_moyen,
                SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as pnl_total,
                AVG(CASE WHEN pnl_max IS NOT NULL THEN pnl_max END) as pnl_max_moyen,
                MIN(CASE WHEN pnl_min IS NOT NULL THEN pnl_min END) as pnl_min_extreme,
                AVG(duree_minutes) as duree_moyenne
            FROM trades_recommandes WHERE date >= ?
        ''', (date_debut,))
        global_stats = dict(cursor.fetchone())

        # Stats par direction
        cursor.execute('''
            SELECT direction,
                COUNT(*) as total,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                AVG(pnl_pct) as pnl_moyen
            FROM trades_recommandes WHERE date >= ? AND direction IS NOT NULL
            GROUP BY direction
        ''', (date_debut,))
        stats_direction = {row['direction']: dict(row) for row in cursor.fetchall()}

        # Stats par heure d'entrée
        cursor.execute('''
            SELECT heure_entree,
                COUNT(*) as total,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                AVG(pnl_pct) as pnl_moyen
            FROM trades_recommandes WHERE date >= ? AND heure_entree IS NOT NULL
            GROUP BY heure_entree ORDER BY heure_entree
        ''', (date_debut,))
        stats_heure = [dict(row) for row in cursor.fetchall()]

        # Stats par catégorie d'actif
        cursor.execute('''
            SELECT categorie_actif,
                COUNT(*) as total,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                AVG(pnl_pct) as pnl_moyen
            FROM trades_recommandes WHERE date >= ? AND categorie_actif IS NOT NULL
            GROUP BY categorie_actif
        ''', (date_debut,))
        stats_categorie = {row['categorie_actif']: dict(row) for row in cursor.fetchall()}

        conn.close()

        return jsonify({
            'success': True,
            'periode': periode,
            'date_debut': str(date_debut),
            'global': global_stats,
            'par_direction': stats_direction,
            'par_heure': stats_heure,
            'par_categorie': stats_categorie
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@bp.route('/api/performances')
def api_performances():
    """Récupère les performances"""
    try:
        periode = request.args.get('periode', 'semaine')
        performances = get_performances(periode)
        return jsonify({
            'success': True,
            'periode': periode,
            'performances': performances
        })
    except Exception as e:
        logger.error(f"Erreur api_performances: {e}")
        return jsonify({'success': False, 'error': str(e)})


@bp.route('/api/top-movers')
def api_top_movers():
    """Récupère les plus fortes variations du jour.
    Filtre les actions US quand le marché US est fermé."""
    try:
        donnees = recuperer_donnees_marche(ACTIFS_PERMANENTS)
        weekend = is_weekend()

        # Déterminer si le marché US est ouvert
        marche_type, _ = get_market_context()
        us_ouvert = marche_type in ('US_OPEN', 'US_PREMARKET')

        # Trier par variation absolue - FILTRE les données invalides
        movers = []
        for nom, data in donnees.items():
            variation = data.get('variation')
            prix = data.get('prix')
            symbole = data.get('symbole', '')
            # Skip si données manquantes ou invalides
            if variation is None or prix is None:
                continue
            # Filtrer actions US individuelles si marché US fermé
            if not us_ouvert and symbole in SYMBOLES_ACTIONS_US:
                continue
            movers.append({
                'nom': nom,
                'symbole': symbole,
                'prix': prix,
                'variation': variation
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
            'us_ouvert': us_ouvert,
            'message_weekend': "Clôture de vendredi" if weekend else None
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@bp.route('/api/premarket')
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


@bp.route('/api/trades/verifier', methods=['POST'])
def api_verifier_trades():
    """Force la vérification des résultats des trades"""
    try:
        nb = verifier_resultats_trades()
        return jsonify({'success': True, 'trades_mis_a_jour': nb})
    except Exception as e:
        logger.error(f"Erreur api_verifier_trades: {e}")
        return jsonify({'success': False, 'error': str(e)})

@bp.route('/api/trades/cloturer', methods=['POST'])
def api_cloturer_trades():
    """Force la clôture des trades du jour"""
    try:
        # D'abord vérifier
        verifier_resultats_trades()
        # Puis clôturer
        nb = cloturer_trades_jour()
        return jsonify({'success': True, 'trades_clotures': nb})
    except Exception as e:
        logger.error(f"Erreur api_cloturer_trades: {e}")
        return jsonify({'success': False, 'error': str(e)})


@bp.route('/api/reevaluation-intraday')
def api_reevaluation_intraday():
    """Lance une réévaluation intraday des trades en cours"""
    try:
        reevaluations = reevaluer_trades_intraday()
        return jsonify({
            'success': True,
            'datetime': get_paris_time().strftime('%d/%m/%Y %H:%M:%S'),
            'reevaluations': reevaluations,
            'nb_trades': len(reevaluations)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


