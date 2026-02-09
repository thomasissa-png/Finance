"""API Routes: ajustements, critères dynamiques, régime marché."""
import sqlite3
import json
from datetime import timedelta
from flask import Blueprint, jsonify, request

from ..config import logger, DB_PATH, DB_TIMEOUT
from ..market_context import (
    get_paris_time, get_regime_marche, get_vix_level, get_contexte_trading_complet,
    get_evenements_macro_jour, get_actifs_filtres_atr
)
from ..adjustments import (
    get_ajustements_en_attente, valider_ajustement, valider_tous_ajustements,
    get_historique_ajustements, get_criteres_dynamiques_actifs, get_criteres_dynamiques
)

bp = Blueprint('api_adjustments', __name__)

@bp.route('/api/contexte-trading')
def api_contexte_trading():
    """Récupère le contexte de trading complet (VIX, session, jour, etc.)"""
    try:
        contexte = get_contexte_trading_complet()
        return jsonify({'success': True, 'contexte': contexte})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@bp.route('/api/regime-marche')
def api_regime_marche():
    """Récupère le régime de marché actuel basé sur le VIX"""
    try:
        vix = get_vix_level()
        regime, description, regles = get_regime_marche(vix)
        return jsonify({
            'success': True,
            'vix': round(vix, 2),
            'regime': regime,
            'description': description,
            'regles_adaptees': regles
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@bp.route('/api/criteres-dynamiques')
def api_criteres_dynamiques():
    """Récupère les critères dynamiques actuels"""
    try:
        criteres = get_criteres_dynamiques()
        return jsonify({'success': True, 'criteres': criteres})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@bp.route('/api/ajustements-proposes')
def api_ajustements_proposes():
    """Récupère les ajustements en attente de validation"""
    try:
        ajustements = get_ajustements_en_attente()
        return jsonify({
            'success': True,
            'ajustements': ajustements,
            'nb_en_attente': len(ajustements)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@bp.route('/api/ajustements-proposes/<int:id_ajustement>/valider', methods=['POST'])
def api_valider_ajustement(id_ajustement):
    """Valide un ajustement proposé avec commentaire optionnel"""
    try:
        data = request.get_json(silent=True) or {}
        commentaire = data.get('commentaire', None)
        succes, message = valider_ajustement(id_ajustement, decision=True, commentaire=commentaire)
        return jsonify({'success': succes, 'message': message})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@bp.route('/api/ajustements-proposes/<int:id_ajustement>/rejeter', methods=['POST'])
def api_rejeter_ajustement(id_ajustement):
    """Rejette un ajustement proposé avec commentaire optionnel"""
    try:
        data = request.get_json(silent=True) or {}
        commentaire = data.get('commentaire', None)
        succes, message = valider_ajustement(id_ajustement, decision=False, commentaire=commentaire)
        return jsonify({'success': succes, 'message': message})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@bp.route('/api/ajustements-proposes/valider-tous', methods=['POST'])
def api_valider_tous_ajustements():
    """Valide tous les ajustements en attente"""
    try:
        resultats = valider_tous_ajustements(decision=True)
        nb_valides = sum(1 for r in resultats if r['succes'])
        return jsonify({
            'success': True,
            'message': f"{nb_valides} ajustements validés",
            'details': resultats
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@bp.route('/api/ajustements-proposes/rejeter-tous', methods=['POST'])
def api_rejeter_tous_ajustements():
    """Rejette tous les ajustements en attente"""
    try:
        resultats = valider_tous_ajustements(decision=False)
        nb_rejetes = sum(1 for r in resultats if r['succes'])
        return jsonify({
            'success': True,
            'message': f"{nb_rejetes} ajustements rejetés",
            'details': resultats
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@bp.route('/api/ajustements-historique')
def api_ajustements_historique():
    """Récupère l'historique des ajustements avec audit trail"""
    try:
        limite = int(request.args.get('limite', 50))
        historique = get_historique_ajustements(limite)

        # Enrichir avec les critères appliqués (audit trail)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        for ajust in historique:
            if ajust.get('statut') == 'valide':
                cursor.execute('''
                    SELECT * FROM criteres_dynamiques
                    WHERE ajustement_source_id = ?
                ''', (ajust['id'],))
                critere_applique = cursor.fetchone()
                if critere_applique:
                    ajust['critere_applique'] = dict(critere_applique)

        conn.close()

        return jsonify({
            'success': True,
            'historique': historique,
            'total': len(historique)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@bp.route('/api/historique-rapports')
def api_historique_rapports():
    """Récupère l'historique des rapports hebdomadaires"""
    try:
        limite = int(request.args.get('limite', 10))
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('''
            SELECT semaine, date_debut, date_fin, resume_executif, chiffres_cles, timestamp
            FROM rapports_hebdo
            ORDER BY date_fin DESC
            LIMIT ?
        ''', (limite,))

        rapports = []
        for row in cursor.fetchall():
            r = dict(row)
            try:
                r['chiffres_cles'] = json.loads(r['chiffres_cles'] or '{}')
            except json.JSONDecodeError:
                r['chiffres_cles'] = {}
            rapports.append(r)

        conn.close()
        return jsonify({'success': True, 'rapports': rapports})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

