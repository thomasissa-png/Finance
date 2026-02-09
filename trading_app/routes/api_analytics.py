"""API Routes: statistiques avancées, equity curve, historique, recherche."""
import sqlite3
import json
from datetime import timedelta
from flask import Blueprint, jsonify, request

from ..config import logger, DB_PATH, DB_TIMEOUT, to_python_type
from ..market_context import get_paris_time

bp = Blueprint('api_analytics', __name__)

@bp.route('/api/stats-avancees')
def api_stats_avancees():
    """Récupère les stats avancées pour A/B testing (jour, session, conviction, etc.)"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Stats des 7 derniers jours
        date_debut = (get_paris_time() - timedelta(days=7)).strftime('%Y-%m-%d')

        # Stats par jour de la semaine
        # CORRIGÉ: Ajout losses pour calcul win rate correct (wins/conclus, pas wins/total)
        cursor.execute('''
            SELECT jour_semaine,
                COUNT(*) as nb,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as losses,
                ROUND(AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END), 2) as pnl_moyen
            FROM trades_recommandes
            WHERE date >= ? AND jour_semaine IS NOT NULL
            GROUP BY jour_semaine
        ''', (date_debut,))
        par_jour = [dict(row) for row in cursor.fetchall()]

        # Stats par conviction
        cursor.execute('''
            SELECT conviction_score,
                COUNT(*) as nb,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as losses,
                ROUND(AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END), 2) as pnl_moyen
            FROM trades_recommandes
            WHERE date >= ? AND conviction_score IS NOT NULL
            GROUP BY conviction_score
            ORDER BY conviction_score
        ''', (date_debut,))
        par_conviction = [dict(row) for row in cursor.fetchall()]

        # Stats par stratégie d'entrée
        cursor.execute('''
            SELECT strategie_entree,
                COUNT(*) as nb,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as losses,
                ROUND(AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END), 2) as pnl_moyen
            FROM trades_recommandes
            WHERE date >= ? AND strategie_entree IS NOT NULL
            GROUP BY strategie_entree
        ''', (date_debut,))
        par_strategie = [dict(row) for row in cursor.fetchall()]

        # Stats par régime
        cursor.execute('''
            SELECT regime_marche,
                COUNT(*) as nb,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as losses,
                ROUND(AVG(vix_niveau), 1) as vix_moyen
            FROM trades_recommandes
            WHERE date >= ? AND regime_marche IS NOT NULL
            GROUP BY regime_marche
        ''', (date_debut,))
        par_regime = [dict(row) for row in cursor.fetchall()]

        conn.close()

        return jsonify({
            'success': True,
            'periode': f"7 derniers jours (depuis {date_debut})",
            'par_jour_semaine': par_jour,
            'par_conviction': par_conviction,
            'par_strategie_entree': par_strategie,
            'par_regime_marche': par_regime
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@bp.route('/api/stats-detaillees')
def api_stats_detaillees():
    """Récupère les statistiques détaillées par heure, actif et type"""
    try:
        periode = request.args.get('periode', 'mois')
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
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
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                   SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as stops,
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
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                   SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as stops,
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
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                   SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as stops,
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
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                   SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as stops,
                   AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as pnl_moyen
            FROM trades_recommandes
            WHERE date >= ? AND direction IS NOT NULL
            GROUP BY direction
        ''', (date_debut,))
        stats_par_direction = [dict(row) for row in cursor.fetchall()]

        # Top 5 actifs les plus performants (basé sur trades CONCLUS uniquement)
        cursor.execute('''
            SELECT actif, symbole,
                   COUNT(*) as nb_trades,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                   SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as stops,
                   AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as pnl_moyen
            FROM trades_recommandes
            WHERE date >= ? AND resultat IN ('TP1', 'TP2', 'WIN_FORCE', 'STOP', 'LOSS_FORCE')
            GROUP BY symbole
            HAVING (reussis + stops) >= 2
            ORDER BY (CAST(reussis AS FLOAT) / (reussis + stops)) DESC, pnl_moyen DESC
            LIMIT 5
        ''', (date_debut,))
        top_actifs = [dict(row) for row in cursor.fetchall()]

        # Bottom 5 actifs les moins performants (basé sur trades CONCLUS uniquement)
        cursor.execute('''
            SELECT actif, symbole,
                   COUNT(*) as nb_trades,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                   SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as stops,
                   AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as pnl_moyen
            FROM trades_recommandes
            WHERE date >= ? AND resultat IN ('TP1', 'TP2', 'WIN_FORCE', 'STOP', 'LOSS_FORCE')
            GROUP BY symbole
            HAVING (reussis + stops) >= 2
            ORDER BY (CAST(reussis AS FLOAT) / (reussis + stops)) ASC, pnl_moyen ASC
            LIMIT 5
        ''', (date_debut,))
        bottom_actifs = [dict(row) for row in cursor.fetchall()]

        # Statistiques de durée des trades
        cursor.execute('''
            SELECT
                AVG(duree_minutes) as duree_moyenne,
                MIN(duree_minutes) as duree_min,
                MAX(duree_minutes) as duree_max,
                AVG(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN duree_minutes END) as duree_moyenne_gagnants,
                AVG(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN duree_minutes END) as duree_moyenne_perdants
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
            conclus = (s.get('reussis') or 0) + (s.get('stops') or 0)
            s['taux_reussite'] = round((s.get('reussis', 0) / conclus * 100) if conclus > 0 else 0, 1)
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

@bp.route('/api/trades-historique')
def api_trades_historique():
    """Récupère l'historique des trades avec filtres et pagination"""
    try:
        # Paramètres de pagination
        page = int(request.args.get('page', 0))
        limit = int(request.args.get('limit', 100))
        limit = min(limit, 500)  # Max 500 par page pour éviter surcharge
        offset = page * limit

        # Paramètres de filtrage
        periode = request.args.get('periode', 'semaine')
        categorie = request.args.get('categorie', '')
        direction = request.args.get('direction', '')
        recherche = request.args.get('recherche', '')
        resultat = request.args.get('resultat', '')  # TP1, TP2, STOP, etc.
        conviction_min = request.args.get('conviction_min', '')
        regime = request.args.get('regime', '')

        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        maintenant = get_paris_time()
        if periode == 'jour':
            date_debut = maintenant.date()
        elif periode == 'semaine':
            date_debut = (maintenant - timedelta(days=7)).date()
        elif periode == 'mois':
            date_debut = (maintenant - timedelta(days=30)).date()
        elif periode == 'trimestre':
            date_debut = (maintenant - timedelta(days=90)).date()
        elif periode == 'annee':
            date_debut = (maintenant - timedelta(days=365)).date()
        else:
            date_debut = (maintenant - timedelta(days=365)).date()

        # Construction requête avec filtres
        where_clauses = ['date >= ?']
        params = [date_debut]

        if categorie:
            where_clauses.append('categorie_actif = ?')
            params.append(categorie)

        if direction:
            where_clauses.append('direction = ?')
            params.append(direction)

        if resultat:
            where_clauses.append('resultat = ?')
            params.append(resultat)

        if conviction_min:
            where_clauses.append('conviction_score >= ?')
            params.append(int(conviction_min))

        if regime:
            where_clauses.append('regime_marche = ?')
            params.append(regime)

        if recherche:
            where_clauses.append('(actif LIKE ? OR symbole LIKE ? OR justification LIKE ?)')
            params.extend([f'%{recherche}%', f'%{recherche}%', f'%{recherche}%'])

        where_sql = ' AND '.join(where_clauses)

        # Compter le total pour pagination
        count_query = f'SELECT COUNT(*) FROM trades_recommandes WHERE {where_sql}'
        cursor.execute(count_query, params)
        total_count = cursor.fetchone()[0]

        # Requête avec projection de colonnes (évite SELECT *)
        select_cols = '''id, date, timestamp_reco, actif, symbole, direction,
                         prix_entree, stop_loss, take_profit_1, take_profit_2,
                         resultat, pnl_pct, duree_minutes, categorie_actif,
                         conviction_score, regime_marche, strategie_entree,
                         statut_intraday, action_recommandee, justification'''

        query = f'''SELECT {select_cols} FROM trades_recommandes
                    WHERE {where_sql}
                    ORDER BY timestamp_reco DESC
                    LIMIT ? OFFSET ?'''
        params.extend([limit, offset])

        cursor.execute(query, params)
        trades = [dict(row) for row in cursor.fetchall()]
        conn.close()

        # Calcul des pages
        total_pages = (total_count + limit - 1) // limit

        return jsonify({
            'success': True,
            'trades': trades,
            'pagination': {
                'page': page,
                'limit': limit,
                'total_count': total_count,
                'total_pages': total_pages,
                'has_next': page < total_pages - 1,
                'has_prev': page > 0
            }
        })
    except Exception as e:
        logger.error(f"Erreur api_trades_historique: {e}")
        return jsonify({'success': False, 'error': str(e)})

@bp.route('/api/equity-curve')
def api_equity_curve():
    """Calcule la courbe d'equity (PnL cumulé) avec métriques avancées"""
    try:
        periode = request.args.get('periode', 'annee')
        granularite = request.args.get('granularite', 'jour')  # jour, semaine, mois

        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        maintenant = get_paris_time()
        if periode == 'mois':
            date_debut = (maintenant - timedelta(days=30)).date()
        elif periode == 'trimestre':
            date_debut = (maintenant - timedelta(days=90)).date()
        elif periode == 'semestre':
            date_debut = (maintenant - timedelta(days=180)).date()
        else:  # annee
            date_debut = (maintenant - timedelta(days=365)).date()

        # Requête selon granularité
        if granularite == 'jour':
            group_by = 'date'
            periode_col = 'date'
        elif granularite == 'semaine':
            group_by = "strftime('%Y-W%W', date)"
            periode_col = f"{group_by} as periode, MIN(date) as date"
        else:  # mois
            group_by = "strftime('%Y-%m', date)"
            periode_col = f"{group_by} as periode, MIN(date) as date"

        cursor.execute(f'''
            SELECT {periode_col},
                   COUNT(*) as nb_trades,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as losses,
                   SUM(CASE WHEN pnl_pct IS NOT NULL AND resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN pnl_pct ELSE 0 END) as gains_total,
                   SUM(CASE WHEN pnl_pct IS NOT NULL AND resultat IN ('STOP', 'LOSS_FORCE') THEN ABS(pnl_pct) ELSE 0 END) as pertes_total,
                   SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl_periode,
                   AVG(CASE WHEN pnl_pct IS NOT NULL AND resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN pnl_pct END) as avg_win,
                   AVG(CASE WHEN pnl_pct IS NOT NULL AND resultat IN ('STOP', 'LOSS_FORCE') THEN pnl_pct END) as avg_loss
            FROM trades_recommandes
            WHERE date >= ? AND resultat IS NOT NULL
            GROUP BY {group_by}
            ORDER BY date ASC
        ''', (date_debut,))

        # Construire la courbe avec métriques cumulées
        curve_data = []
        equity = 0
        peak = 0
        max_drawdown = 0
        max_drawdown_pct = 0
        total_wins = 0
        total_losses = 0
        total_gains = 0
        total_pertes = 0
        consecutive_wins = 0
        consecutive_losses = 0
        max_consecutive_wins = 0
        max_consecutive_losses = 0
        last_was_win = None

        for row in cursor.fetchall():
            data = dict(row)
            pnl = data['pnl_periode'] or 0
            equity += pnl
            total_wins += data['wins'] or 0
            total_losses += data['losses'] or 0
            total_gains += data['gains_total'] or 0
            total_pertes += data['pertes_total'] or 0

            # Peak et drawdown
            if equity > peak:
                peak = equity
            drawdown = peak - equity
            if drawdown > max_drawdown:
                max_drawdown = drawdown
                max_drawdown_pct = (drawdown / peak * 100) if peak > 0 else 0

            # Séries consécutives
            period_was_win = (data['wins'] or 0) > (data['losses'] or 0)
            if last_was_win is not None:
                if period_was_win and last_was_win:
                    consecutive_wins += 1
                    consecutive_losses = 0
                elif not period_was_win and not last_was_win:
                    consecutive_losses += 1
                    consecutive_wins = 0
                else:
                    if period_was_win:
                        consecutive_wins = 1
                        consecutive_losses = 0
                    else:
                        consecutive_losses = 1
                        consecutive_wins = 0
            else:
                consecutive_wins = 1 if period_was_win else 0
                consecutive_losses = 0 if period_was_win else 1

            max_consecutive_wins = max(max_consecutive_wins, consecutive_wins)
            max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)
            last_was_win = period_was_win

            curve_data.append({
                'date': data['date'],
                'periode': data.get('periode', data['date']),
                'nb_trades': data['nb_trades'],
                'wins': data['wins'],
                'losses': data['losses'],
                'pnl_periode': round(pnl, 2),
                'equity': round(equity, 2),
                'peak': round(peak, 2),
                'drawdown': round(drawdown, 2),
                'win_rate': round((data['wins'] / data['nb_trades'] * 100) if data['nb_trades'] > 0 else 0, 1),
                'avg_win': round(data['avg_win'], 2) if data['avg_win'] else 0,
                'avg_loss': round(data['avg_loss'], 2) if data['avg_loss'] else 0
            })

        conn.close()

        # Calcul métriques globales
        total_trades = total_wins + total_losses
        win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0
        profit_factor = (total_gains / total_pertes) if total_pertes > 0 else float('inf') if total_gains > 0 else 0
        avg_win_global = (total_gains / total_wins) if total_wins > 0 else 0
        avg_loss_global = (total_pertes / total_losses) if total_losses > 0 else 0
        expectancy = (win_rate/100 * avg_win_global) - ((1 - win_rate/100) * avg_loss_global)

        # Recovery factor = equity finale / max drawdown
        recovery_factor = (equity / max_drawdown) if max_drawdown > 0 else float('inf') if equity > 0 else 0

        return jsonify({
            'success': True,
            'periode': periode,
            'granularite': granularite,
            'curve': curve_data,
            'metrics': {
                'equity_finale': round(equity, 2),
                'peak': round(peak, 2),
                'max_drawdown': round(max_drawdown, 2),
                'max_drawdown_pct': round(max_drawdown_pct, 2),
                'total_trades': total_trades,
                'total_wins': total_wins,
                'total_losses': total_losses,
                'win_rate': round(win_rate, 2),
                'profit_factor': round(profit_factor, 2) if profit_factor != float('inf') else 'inf',
                'avg_win': round(avg_win_global, 2),
                'avg_loss': round(avg_loss_global, 2),
                'expectancy': round(expectancy, 2),
                'recovery_factor': round(recovery_factor, 2) if recovery_factor != float('inf') else 'inf',
                'max_consecutive_wins': max_consecutive_wins,
                'max_consecutive_losses': max_consecutive_losses
            }
        })
    except Exception as e:
        logger.error(f"Erreur api_equity_curve: {e}")
        return jsonify({'success': False, 'error': str(e)})

@bp.route('/api/journal-quotidien/search')
def api_journal_search():
    """Recherche dans le journal quotidien avec filtres avancés"""
    try:
        # Paramètres de recherche
        recherche = request.args.get('q', '')
        date_from = request.args.get('date_from', '')
        date_to = request.args.get('date_to', '')
        symbole = request.args.get('symbole', '')
        categorie = request.args.get('categorie', '')
        page = int(request.args.get('page', 0))
        limit = int(request.args.get('limit', 50))
        limit = min(limit, 200)
        offset = page * limit

        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Construction de la requête
        where_clauses = ['1=1']
        params = []

        if date_from:
            where_clauses.append('date >= ?')
            params.append(date_from)

        if date_to:
            where_clauses.append('date <= ?')
            params.append(date_to)

        if symbole:
            where_clauses.append('symbole = ?')
            params.append(symbole)

        if categorie:
            where_clauses.append('categorie = ?')
            params.append(categorie)

        if recherche:
            where_clauses.append('''(
                nom_actif LIKE ? OR
                symbole LIKE ? OR
                commentaire LIKE ? OR
                opportunites_jour LIKE ? OR
                mouvements_notables LIKE ?
            )''')
            search_term = f'%{recherche}%'
            params.extend([search_term] * 5)

        where_sql = ' AND '.join(where_clauses)

        # Compter le total
        count_query = f'SELECT COUNT(*) FROM journal_quotidien WHERE {where_sql}'
        cursor.execute(count_query, params)
        total_count = cursor.fetchone()[0]

        # Récupérer les entrées paginées
        query = f'''
            SELECT * FROM journal_quotidien
            WHERE {where_sql}
            ORDER BY date DESC, nom_actif ASC
            LIMIT ? OFFSET ?
        '''
        params.extend([limit, offset])
        cursor.execute(query, params)

        entries = []
        for row in cursor.fetchall():
            entry = dict(row)
            # Parser JSON avec logging des erreurs
            for json_field in ['opportunites_jour', 'recommandations_analystes']:
                if entry.get(json_field):
                    try:
                        entry[json_field] = json.loads(entry[json_field])
                    except json.JSONDecodeError as e:
                        print(f"⚠️ JSON PARSE ERROR - journal_quotidien.{json_field} @ {entry.get('date')}/{entry.get('symbole')}: {e}")
                        entry[json_field] = []
                        entry[f'{json_field}_error'] = True
            entries.append(entry)

        conn.close()

        total_pages = (total_count + limit - 1) // limit

        return jsonify({
            'success': True,
            'entries': entries,
            'pagination': {
                'page': page,
                'limit': limit,
                'total_count': total_count,
                'total_pages': total_pages,
                'has_next': page < total_pages - 1,
                'has_prev': page > 0
            },
            'filters': {
                'recherche': recherche,
                'date_from': date_from,
                'date_to': date_to,
                'symbole': symbole,
                'categorie': categorie
            }
        })
    except Exception as e:
        logger.error(f"Erreur api_journal_search: {e}")
        return jsonify({'success': False, 'error': str(e)})

@bp.route('/api/journal-stats')
def api_journal_stats():
    """Statistiques hebdomadaires et mensuelles du journal"""
    try:
        granularite = request.args.get('granularite', 'semaine')  # semaine, mois
        limite = int(request.args.get('limite', 12))

        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        if granularite == 'semaine':
            periode_sql = "strftime('%Y-W%W', date)"
        else:  # mois
            periode_sql = "strftime('%Y-%m', date)"

        # Stats des trades par période
        cursor.execute(f'''
            SELECT {periode_sql} as periode,
                   MIN(date) as date_debut,
                   MAX(date) as date_fin,
                   COUNT(*) as nb_trades,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as losses,
                   SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl_total,
                   AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as pnl_moyen,
                   AVG(duree_minutes) as duree_moyenne,
                   COUNT(DISTINCT symbole) as nb_actifs_trades,
                   SUM(CASE WHEN conviction_score >= 4 THEN 1 ELSE 0 END) as trades_haute_conviction,
                   AVG(conviction_score) as conviction_moyenne
            FROM trades_recommandes
            WHERE resultat IS NOT NULL
            GROUP BY {periode_sql}
            ORDER BY periode DESC
            LIMIT ?
        ''', (limite,))

        stats_periodes = []
        for row in cursor.fetchall():
            data = dict(row)
            conclus = (data['wins'] or 0) + (data['losses'] or 0)
            data['win_rate'] = round((data['wins'] / conclus * 100) if conclus > 0 else 0, 1)
            data['pnl_total'] = round(data['pnl_total'] or 0, 2)
            data['pnl_moyen'] = round(data['pnl_moyen'] or 0, 2)
            data['duree_moyenne'] = round(data['duree_moyenne'] or 0, 0)
            data['conviction_moyenne'] = round(data['conviction_moyenne'] or 0, 1)
            stats_periodes.append(data)

        # Stats par catégorie d'actif par période
        cursor.execute(f'''
            SELECT {periode_sql} as periode,
                   categorie_actif,
                   COUNT(*) as nb_trades,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl_total
            FROM trades_recommandes
            WHERE resultat IS NOT NULL AND categorie_actif IS NOT NULL
            GROUP BY {periode_sql}, categorie_actif
            ORDER BY periode DESC, nb_trades DESC
            LIMIT ?
        ''', (limite * 10,))

        stats_categories = {}
        for row in cursor.fetchall():
            data = dict(row)
            periode = data['periode']
            if periode not in stats_categories:
                stats_categories[periode] = []
            stats_categories[periode].append({
                'categorie': data['categorie_actif'],
                'nb_trades': data['nb_trades'],
                'wins': data['wins'],
                'pnl_total': round(data['pnl_total'] or 0, 2)
            })

        # Meilleurs et pires actifs par période
        cursor.execute(f'''
            SELECT {periode_sql} as periode,
                   symbole,
                   actif,
                   COUNT(*) as nb_trades,
                   SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl_total
            FROM trades_recommandes
            WHERE resultat IS NOT NULL
            GROUP BY {periode_sql}, symbole
            HAVING nb_trades >= 2
            ORDER BY periode DESC, pnl_total DESC
        ''')

        top_bottom_actifs = {}
        for row in cursor.fetchall():
            data = dict(row)
            periode = data['periode']
            if periode not in top_bottom_actifs:
                top_bottom_actifs[periode] = {'top': [], 'bottom': []}

            item = {
                'symbole': data['symbole'],
                'actif': data['actif'],
                'nb_trades': data['nb_trades'],
                'pnl_total': round(data['pnl_total'] or 0, 2)
            }

            # Garder top 3 et bottom 3
            if len(top_bottom_actifs[periode]['top']) < 3:
                top_bottom_actifs[periode]['top'].append(item)
            elif item['pnl_total'] < 0 and len(top_bottom_actifs[periode]['bottom']) < 3:
                top_bottom_actifs[periode]['bottom'].insert(0, item)

        # Réorganiser bottom (les pires en premier)
        for periode in top_bottom_actifs:
            top_bottom_actifs[periode]['bottom'] = sorted(
                top_bottom_actifs[periode]['bottom'],
                key=lambda x: x['pnl_total']
            )[:3]

        # Stats par Trade Grade (A/B/C/D)
        cursor.execute('''
            SELECT trade_grade,
                   COUNT(*) as nb_trades,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE', 'BREAKEVEN') THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as losses,
                   SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl_total,
                   AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as pnl_moyen
            FROM trades_recommandes
            WHERE resultat IS NOT NULL AND trade_grade IS NOT NULL
            GROUP BY trade_grade
            ORDER BY trade_grade
        ''')

        stats_par_grade = {}
        for row in cursor.fetchall():
            data = dict(row)
            grade = data['trade_grade']
            conclus = (data['wins'] or 0) + (data['losses'] or 0)
            stats_par_grade[grade] = {
                'nb_trades': data['nb_trades'],
                'wins': data['wins'] or 0,
                'losses': data['losses'] or 0,
                'win_rate': round((data['wins'] / conclus * 100) if conclus > 0 else 0, 1),
                'pnl_total': round(data['pnl_total'] or 0, 2),
                'pnl_moyen': round(data['pnl_moyen'] or 0, 2)
            }

        conn.close()

        # Inverser pour ordre chronologique
        stats_periodes.reverse()

        return jsonify({
            'success': True,
            'granularite': granularite,
            'stats_periodes': stats_periodes,
            'stats_categories': stats_categories,
            'top_bottom_actifs': top_bottom_actifs,
            'stats_par_grade': stats_par_grade
        })
    except Exception as e:
        logger.error(f"Erreur api_journal_stats: {e}")
        return jsonify({'success': False, 'error': str(e)})

