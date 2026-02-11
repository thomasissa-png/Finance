"""
Ajustements stratégiques: propositions, validation, feedback, critères dynamiques.
"""
import sqlite3
import json
from datetime import datetime, timedelta

from .config import logger, DB_PATH, DB_TIMEOUT
from .market_context import get_paris_time

def extraire_categorie_ajustement(critere, raison, action):
    """
    Extrait une catégorie spécifique d'un ajustement stratégique.
    CORRIGÉ: Évite la catégorie générique 'trading' pour permettre un feedback précis.
    """
    texte = f"{critere} {raison} {action}".lower()

    # Catégories d'actifs
    if any(mot in texte for mot in ['indice', 'indices', 'cac', 'dax', 'sp500', 's&p', 'nasdaq', 'dow']):
        return 'indice'
    if any(mot in texte for mot in ['action_eu', 'actions européennes', 'actions eu', 'lvmh', 'total', 'airbus']):
        return 'action_eu'
    if any(mot in texte for mot in ['action_us', 'actions américaines', 'actions us', 'apple', 'tesla', 'nvidia', 'microsoft']):
        return 'action_us'
    # 'or' vérifié avec word boundary pour éviter faux positifs (forex, score, effort...)
    if any(mot in texte for mot in ['commodit', 'gold', 'pétrole', 'oil', 'silver', 'argent']):
        return 'commodite'
    if ' or ' in f' {texte} ':
        return 'commodite'
    if any(mot in texte for mot in ['forex', 'eur/usd', 'gbp', 'devise', 'currency']):
        return 'forex'

    # Catégories temporelles/contextuelles
    if any(mot in texte for mot in ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi']):
        return 'timing_jour'
    if any(mot in texte for mot in ['matin', 'après-midi', 'soir', 'ouverture', 'clôture', 'session']):
        return 'timing_session'
    if any(mot in texte for mot in ['news', 'annonce', 'économique', 'fomc', 'nfp', 'inflation']):
        return 'news_trading'

    # Catégories techniques
    if any(mot in texte for mot in ['rsi', 'macd', 'indicateur']):
        return 'indicateurs'
    if any(mot in texte for mot in ['trailing', 'stop', 'tp', 'take profit']):
        return 'gestion_position'
    if any(mot in texte for mot in ['ratio', 'r/r', 'risk', 'reward']):
        return 'risk_reward'
    if any(mot in texte for mot in ['conviction', 'confiance', 'score']):
        return 'conviction'

    # Catégorie par défaut plus spécifique que "trading"
    return 'strategie_generale'

def sauvegarder_ajustements_proposes(ajustements, scores_confiance, source='rapport_hebdo'):
    """Sauvegarde les ajustements proposés en attente de validation

    CORRIGÉ: Catégorie extraite du contenu au lieu de 'trading' générique
    CORRIGÉ: Auto-validation retardée (pas immédiate après création)
    """
    maintenant = get_paris_time()

    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        cursor = conn.cursor()

        # Sauvegarder les scores de confiance comme propositions
        for categorie, score_data in scores_confiance.items():
            if isinstance(score_data, dict):
                score = score_data.get('score', 50)
                tendance = score_data.get('tendance', 'stable')

                # Normaliser la catégorie pour cohérence avec categorie_actif
                categorie_normalisee = normaliser_categorie(categorie)

                cursor.execute('''
                    INSERT INTO ajustements_proposes
                    (type_ajustement, categorie, critere, action, valeur_proposee, raison, source, statut, date_proposition)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    'score_confiance',
                    categorie_normalisee,
                    'score_confiance',
                    f"Définir score à {score}/100",
                    str(score),
                    f"Tendance: {tendance}",
                    source,
                    'en_attente',
                    maintenant
                ))

        # Sauvegarder les ajustements stratégiques
        for ajust in ajustements:
            critere = ajust.get('critere', '')
            action = ajust.get('action', '')
            raison = ajust.get('raison', '')

            # CORRIGÉ: Extraire une catégorie spécifique du contenu
            categorie = extraire_categorie_ajustement(critere, raison, action)

            cursor.execute('''
                INSERT INTO ajustements_proposes
                (type_ajustement, categorie, critere, action, valeur_proposee, raison, source, statut, date_proposition)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                'strategie',
                categorie,  # CORRIGÉ: Catégorie spécifique au lieu de 'trading'
                critere,
                action,
                '',
                raison,
                source,
                'en_attente',
                maintenant
            ))

        conn.commit()
        conn.close()
        print(f"[{maintenant.strftime('%H:%M:%S')} CET] 📋 {len(ajustements) + len(scores_confiance)} ajustements proposés en attente de validation")

        # CORRIGÉ: Auto-validation RETARDÉE - ne pas appeler immédiatement
        # Le traitement automatique sera fait par le scheduler après un délai
        # pour laisser à l'utilisateur le temps de review manuellement
        # traiter_ajustements_automatiquement()  # Désactivé - sera appelé par scheduler

    except Exception as e:
        print(f"⚠️ Erreur sauvegarde ajustements: {e}")

def get_ajustements_en_attente():
    """Récupère les ajustements en attente de validation"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM ajustements_proposes
            WHERE statut = 'en_attente'
            ORDER BY date_proposition DESC
        ''')

        ajustements = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return ajustements
    except Exception as e:
        print(f"⚠️ Erreur récupération ajustements: {e}")
        return []

def valider_ajustement(id_ajustement, decision, decideur='utilisateur', commentaire=None):
    """Valide ou rejette un ajustement proposé avec audit trail et commentaire optionnel"""
    maintenant = get_paris_time()

    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row  # Pour accéder aux colonnes par nom
        cursor = conn.cursor()

        # Récupérer l'ajustement
        cursor.execute('SELECT * FROM ajustements_proposes WHERE id = ?', (id_ajustement,))
        row = cursor.fetchone()

        if not row:
            conn.close()
            return False, "Ajustement non trouvé"

        ajust_dict = dict(row)

        # Mettre à jour le statut avec commentaire optionnel
        nouveau_statut = 'valide' if decision else 'rejete'
        cursor.execute('''
            UPDATE ajustements_proposes
            SET statut = ?, date_decision = ?, decideur = ?, commentaire_utilisateur = ?
            WHERE id = ?
        ''', (nouveau_statut, maintenant, decideur, commentaire, id_ajustement))

        # Si validé, appliquer l'ajustement avec audit trail
        if decision:
            if ajust_dict.get('type_ajustement') == 'score_confiance':
                # Appliquer le score de confiance
                cursor.execute('''
                    INSERT INTO criteres_dynamiques
                    (date_maj, categorie, critere, valeur_actuelle, valeur_precedente, raison, timestamp, ajustement_source_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    maintenant.date(),
                    ajust_dict.get('categorie'),
                    'score_confiance',
                    float(ajust_dict.get('valeur_proposee', 50)),
                    50,
                    f"Validé par {decideur}: {ajust_dict.get('raison', '')}",
                    maintenant,
                    id_ajustement  # Audit trail: lien vers l'ajustement source
                ))
            else:
                # Appliquer l'ajustement stratégique
                cursor.execute('''
                    INSERT INTO criteres_dynamiques
                    (date_maj, categorie, critere, valeur_actuelle, valeur_precedente, raison, timestamp, ajustement_source_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    maintenant.date(),
                    'ajustement_valide',
                    ajust_dict.get('critere', ''),
                    1,
                    0,
                    f"Validé par {decideur}: {ajust_dict.get('action', '')} - {ajust_dict.get('raison', '')}",
                    maintenant,
                    id_ajustement  # Audit trail: lien vers l'ajustement source
                ))

            print(f"✅ Ajustement #{id_ajustement} validé par {decideur} et appliqué")

        conn.commit()
        conn.close()

        return True, f"Ajustement {'validé et appliqué' if decision else 'rejeté'}"

    except Exception as e:
        print(f"⚠️ Erreur validation ajustement: {e}")
        return False, str(e)

def valider_tous_ajustements(decision, decideur='utilisateur'):
    """Valide ou rejette tous les ajustements en attente"""
    ajustements = get_ajustements_en_attente()
    resultats = []

    for ajust in ajustements:
        succes, message = valider_ajustement(ajust['id'], decision, decideur)
        resultats.append({'id': ajust['id'], 'succes': succes, 'message': message})

    return resultats

def expirer_ajustements_anciens(jours_max=21):
    """DÉSACTIVÉ - Les ajustements sont conservés pour historique dans les rapports hebdo.
    Cette fonction n'est plus appelée par le scheduler."""
    return 0  # Ne rien faire - garder tout l'historique

def _expirer_ajustements_anciens_legacy(jours_max=21):
    """[LEGACY] Expire automatiquement les ajustements non traités après X jours"""
    maintenant = get_paris_time()
    date_limite = maintenant - timedelta(days=jours_max)

    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        cursor = conn.cursor()

        # Marquer comme expirés les ajustements trop anciens
        cursor.execute('''
            UPDATE ajustements_proposes
            SET statut = 'expire',
                date_decision = ?,
                decideur = 'systeme_auto'
            WHERE statut = 'en_attente'
            AND date_proposition < ?
        ''', (maintenant, date_limite))

        nb_expires = cursor.rowcount
        conn.commit()
        conn.close()

        if nb_expires > 0:
            print(f"[{maintenant.strftime('%H:%M:%S')} CET] 🕐 {nb_expires} ajustements expirés (> {jours_max} jours)")

        return nb_expires

    except Exception as e:
        print(f"⚠️ Erreur expiration ajustements: {e}")
        return 0

def get_historique_ajustements(limite=50):
    """Récupère l'historique complet des ajustements (validés, rejetés, expirés)"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM ajustements_proposes
            WHERE statut != 'en_attente'
            ORDER BY date_decision DESC
            LIMIT ?
        ''', (limite,))

        historique = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return historique
    except Exception as e:
        print(f"⚠️ Erreur historique ajustements: {e}")
        return []

# ============================================================================
# FEEDBACK LOOP - ÉVALUATION DES AJUSTEMENTS
# ============================================================================

def construire_filtre_feedback(categorie, type_ajustement):
    """
    Construit le filtre SQL approprié selon la catégorie de l'ajustement.
    CORRIGÉ: Gère les catégories d'actifs ET les catégories contextuelles.
    """
    # Catégories qui correspondent directement à categorie_actif
    categories_actifs = ['indice', 'action_eu', 'action_us', 'commodite', 'forex']

    if categorie in categories_actifs:
        return "categorie_actif = ?", [categorie]

    # Catégories temporelles - filtrer par jour de semaine
    if categorie == 'timing_jour':
        return "1=1", []  # Pas de filtre spécifique, compare globalement

    # Catégories liées aux sessions
    if categorie == 'timing_session':
        return "session_marche IS NOT NULL", []

    # Catégories liées aux news
    if categorie == 'news_trading':
        return "type_setup = 'NEWS'", []

    # Catégories techniques (indicateurs, gestion position, etc.)
    if categorie in ['indicateurs', 'gestion_position', 'risk_reward', 'conviction']:
        return "1=1", []  # Compare globalement

    # Score confiance - filtre par catégorie d'actif si c'est une catégorie valide
    if type_ajustement == 'score_confiance' and categorie in categories_actifs:
        return "categorie_actif = ?", [categorie]

    # Défaut: pas de filtre spécifique
    return "1=1", []

def calculer_feedback_ajustement(id_ajustement, jours_evaluation=7):
    """
    Calcule l'impact d'un ajustement en comparant les performances
    avant et après son application.

    CORRIGÉ: Gère les différentes catégories d'ajustements (pas seulement categorie_actif)
    CORRIGÉ: Exclut les trades ouverts (resultat IS NOT NULL)
    """
    maintenant = get_paris_time()

    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Récupérer l'ajustement
        cursor.execute('SELECT * FROM ajustements_proposes WHERE id = ?', (id_ajustement,))
        row = cursor.fetchone()

        if not row or row['statut'] != 'valide':
            conn.close()
            return None, "Ajustement non trouvé ou non validé"

        ajust = dict(row)
        date_application = ajust.get('date_decision')

        if not date_application:
            conn.close()
            return None, "Date d'application non disponible"

        # Convertir la date si nécessaire
        if isinstance(date_application, str):
            date_application = datetime.fromisoformat(date_application.replace('Z', '+00:00'))

        date_application = date_application.date() if hasattr(date_application, 'date') else date_application

        # Période AVANT l'ajustement (7 jours avant)
        date_debut_avant = date_application - timedelta(days=jours_evaluation)

        # Période APRÈS l'ajustement (7 jours après ou jusqu'à maintenant)
        date_fin_apres = min(date_application + timedelta(days=jours_evaluation), maintenant.date())

        # CORRIGÉ: Construire le filtre approprié selon la catégorie
        categorie = ajust.get('categorie', '')
        type_ajust = ajust.get('type_ajustement', 'strategie')
        filtre_sql, filtre_params = construire_filtre_feedback(categorie, type_ajust)

        # Stats AVANT - CORRIGÉ: Exclut trades ouverts (resultat IS NOT NULL)
        query_avant = f'''
            SELECT
                COUNT(*) as nb_trades,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as stops,
                SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl_total
            FROM trades_recommandes
            WHERE date >= ? AND date < ?
            AND resultat IS NOT NULL
            AND {filtre_sql}
        '''
        cursor.execute(query_avant, (date_debut_avant, date_application, *filtre_params))
        avant = cursor.fetchone()

        # Stats APRÈS - CORRIGÉ: Exclut trades ouverts (resultat IS NOT NULL)
        query_apres = f'''
            SELECT
                COUNT(*) as nb_trades,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as stops,
                SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl_total
            FROM trades_recommandes
            WHERE date >= ? AND date <= ?
            AND resultat IS NOT NULL
            AND {filtre_sql}
        '''
        cursor.execute(query_apres, (date_application, date_fin_apres, *filtre_params))
        apres = cursor.fetchone()

        # Calculer les taux de réussite
        avant_conclus = (avant['reussis'] or 0) + (avant['stops'] or 0)
        apres_conclus = (apres['reussis'] or 0) + (apres['stops'] or 0)

        taux_avant = round((avant['reussis'] / avant_conclus * 100) if avant_conclus > 0 else 0, 1)
        taux_apres = round((apres['reussis'] / apres_conclus * 100) if apres_conclus > 0 else 0, 1)

        pnl_avant = round(avant['pnl_total'] or 0, 2)
        pnl_apres = round(apres['pnl_total'] or 0, 2)

        # Déterminer la conclusion (seuil 5 trades pour significativité)
        if apres_conclus < 5:
            conclusion = "INSUFFISANT"
            conclusion_detail = f"Seulement {apres_conclus} trades après ajustement (min 5 requis)"
        elif avant_conclus < 5:
            conclusion = "INSUFFISANT"
            conclusion_detail = f"Seulement {avant_conclus} trades avant ajustement (min 5 requis pour comparaison)"
        elif taux_apres > taux_avant + 5:
            conclusion = "POSITIF"
            conclusion_detail = f"Taux +{taux_apres - taux_avant:.1f}%, PnL: {pnl_avant:.2f}% → {pnl_apres:.2f}%"
        elif taux_apres < taux_avant - 5:
            conclusion = "NEGATIF"
            conclusion_detail = f"Taux {taux_apres - taux_avant:.1f}%, PnL: {pnl_avant:.2f}% → {pnl_apres:.2f}%"
        else:
            conclusion = "NEUTRE"
            conclusion_detail = f"Peu de changement (±5%), PnL: {pnl_avant:.2f}% → {pnl_apres:.2f}%"

        # Sauvegarder le feedback
        cursor.execute('''
            UPDATE ajustements_proposes SET
                perf_avant_nb_trades = ?,
                perf_avant_taux_reussite = ?,
                perf_avant_pnl_total = ?,
                perf_apres_nb_trades = ?,
                perf_apres_taux_reussite = ?,
                perf_apres_pnl_total = ?,
                feedback_date = ?,
                feedback_conclusion = ?
            WHERE id = ?
        ''', (
            avant['nb_trades'], taux_avant, pnl_avant,
            apres['nb_trades'], taux_apres, pnl_apres,
            maintenant, f"{conclusion}: {conclusion_detail}",
            id_ajustement
        ))

        conn.commit()
        conn.close()

        return {
            'id': id_ajustement,
            'categorie': ajust.get('categorie'),
            'avant': {'nb_trades': avant['nb_trades'], 'taux': taux_avant, 'pnl': pnl_avant},
            'apres': {'nb_trades': apres['nb_trades'], 'taux': taux_apres, 'pnl': pnl_apres},
            'conclusion': conclusion,
            'detail': conclusion_detail
        }, None

    except Exception as e:
        print(f"⚠️ Erreur feedback ajustement: {e}")
        return None, str(e)

def evaluer_tous_ajustements_valides():
    """Évalue tous les ajustements validés qui n'ont pas encore de feedback"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Récupérer les ajustements validés sans feedback et > 7 jours
        date_limite = get_paris_time() - timedelta(days=7)
        cursor.execute('''
            SELECT id FROM ajustements_proposes
            WHERE statut = 'valide'
            AND feedback_conclusion IS NULL
            AND date_decision < ?
        ''', (date_limite,))

        ajustements = [row['id'] for row in cursor.fetchall()]
        conn.close()

        resultats = []
        for id_ajust in ajustements:
            result, error = calculer_feedback_ajustement(id_ajust)
            if result:
                resultats.append(result)
                print(f"📊 Feedback #{id_ajust}: {result['conclusion']}")

        return resultats

    except Exception as e:
        print(f"⚠️ Erreur évaluation ajustements: {e}")
        return []

def get_historique_feedback_categorie(categorie, type_ajustement='strategie'):
    """
    Analyse l'historique des feedbacks pour une catégorie donnée.
    Retourne: {'nb_positifs': int, 'nb_negatifs': int, 'nb_neutres': int, 'recommandation': str}

    CORRIGÉ: Utilise AND au lieu de OR pour filtrer correctement par catégorie ET type.
    CORRIGÉ: Seuils augmentés de 2 à 5 pour significativité statistique.
    """
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Récupérer les feedbacks des 30 derniers jours pour cette catégorie
        # CORRIGÉ: AND au lieu de OR - on veut les feedbacks de CETTE catégorie
        date_limite = get_paris_time() - timedelta(days=30)
        cursor.execute('''
            SELECT feedback_conclusion
            FROM ajustements_proposes
            WHERE categorie = ?
            AND type_ajustement = ?
            AND statut = 'valide'
            AND feedback_conclusion IS NOT NULL
            AND feedback_conclusion NOT LIKE '%INSUFFISANT%'
            AND date_decision > ?
        ''', (categorie, type_ajustement, date_limite))

        resultats = [row['feedback_conclusion'] for row in cursor.fetchall()]
        conn.close()

        nb_positifs = sum(1 for r in resultats if r and 'POSITIF' in r)
        nb_negatifs = sum(1 for r in resultats if r and 'NEGATIF' in r)
        nb_neutres = sum(1 for r in resultats if r and 'NEUTRE' in r)

        # CORRIGÉ: Seuils harmonisés - minimum 5 feedbacks pour significativité
        total_conclus = nb_positifs + nb_negatifs
        if total_conclus < 5:
            # Pas assez de données - toujours validation manuelle
            recommandation = 'MANUEL'
        elif nb_positifs >= 5 and nb_negatifs == 0:
            # Pattern positif fort (5+ sans aucun négatif)
            recommandation = 'AUTO_VALIDER'
        elif nb_negatifs >= 5 and nb_positifs == 0:
            # Circuit breaker (5+ négatifs sans aucun positif)
            recommandation = 'BLOQUER'
        elif nb_positifs >= 5 and nb_positifs > nb_negatifs * 3:
            # Ratio 3:1 en faveur des positifs
            recommandation = 'AUTO_VALIDER'
        elif nb_negatifs >= 5 and nb_negatifs > nb_positifs * 3:
            # Ratio 3:1 en faveur des négatifs
            recommandation = 'BLOQUER'
        else:
            # Résultats mitigés - validation manuelle
            recommandation = 'MANUEL'

        return {
            'nb_positifs': nb_positifs,
            'nb_negatifs': nb_negatifs,
            'nb_neutres': nb_neutres,
            'total_conclus': total_conclus,
            'recommandation': recommandation
        }

    except Exception as e:
        print(f"⚠️ Erreur historique feedback: {e}")
        return {'nb_positifs': 0, 'nb_negatifs': 0, 'nb_neutres': 0, 'total_conclus': 0, 'recommandation': 'MANUEL'}

def auto_valider_ajustement_si_positif(id_ajustement, categorie, type_ajustement):
    """
    Auto-valide un ajustement si l'historique est positif.
    Retourne: (bool auto_valide, str raison)
    """
    historique = get_historique_feedback_categorie(categorie, type_ajustement)

    if historique['recommandation'] == 'AUTO_VALIDER':
        # Auto-validation basée sur les performances passées
        succes, message = valider_ajustement(id_ajustement, decision=True, decideur='auto_feedback')
        if succes:
            print(f"✅ Auto-validation #{id_ajustement}: {historique['nb_positifs']} feedbacks positifs historiques")
            return True, f"Auto-validé (historique: {historique['nb_positifs']} positifs, {historique['nb_negatifs']} négatifs)"
        return False, message

    elif historique['recommandation'] == 'BLOQUER':
        # Circuit breaker - rejeter automatiquement
        succes, message = valider_ajustement(id_ajustement, decision=False, decideur='circuit_breaker')
        if succes:
            print(f"🛑 Circuit breaker #{id_ajustement}: {historique['nb_negatifs']} feedbacks négatifs historiques")
            return False, f"Bloqué (circuit breaker: {historique['nb_negatifs']} négatifs, {historique['nb_positifs']} positifs)"
        return False, message

    return False, "En attente validation manuelle"

def traiter_ajustements_automatiquement():
    """
    Traite automatiquement les ajustements en attente basé sur l'historique des feedbacks.
    Appelé régulièrement pour auto-valider ou bloquer selon le pattern.
    """
    maintenant = get_paris_time()
    ajustements = get_ajustements_en_attente()

    resultats = {'auto_valides': 0, 'bloques': 0, 'manuels': 0}

    for ajust in ajustements:
        categorie = ajust.get('categorie', '')
        type_ajust = ajust.get('type_ajustement', 'strategie')
        id_ajust = ajust.get('id')

        auto_valide, raison = auto_valider_ajustement_si_positif(id_ajust, categorie, type_ajust)

        if 'Auto-validé' in raison:
            resultats['auto_valides'] += 1
        elif 'Bloqué' in raison:
            resultats['bloques'] += 1
        else:
            resultats['manuels'] += 1

    if resultats['auto_valides'] > 0 or resultats['bloques'] > 0:
        print(f"[{maintenant.strftime('%H:%M:%S')} CET] 🤖 Traitement auto: {resultats['auto_valides']} validés, {resultats['bloques']} bloqués, {resultats['manuels']} manuels")

    return resultats


def get_criteres_dynamiques():
    """Récupère les critères dynamiques actuels pour le SYSTEM_PROMPT"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Récupérer les derniers scores de confiance
        cursor.execute('''
            SELECT categorie, valeur_actuelle as score, raison as tendance
            FROM criteres_dynamiques
            WHERE critere = 'score_confiance'
            AND date_maj = (SELECT MAX(date_maj) FROM criteres_dynamiques WHERE critere = 'score_confiance')
        ''')
        scores = {row['categorie']: {'score': row['score'], 'tendance': row['tendance']}
                  for row in cursor.fetchall()}

        # Récupérer les derniers ajustements
        cursor.execute('''
            SELECT critere, raison
            FROM criteres_dynamiques
            WHERE categorie IN ('ajustement', 'ajustement_valide')
            AND date_maj >= date('now', '-30 days')
            ORDER BY date_maj DESC
        ''')
        ajustements = [{'critere': row['critere'], 'raison': row['raison']}
                      for row in cursor.fetchall()]

        conn.close()

        return {
            'scores_confiance': scores,
            'ajustements_recents': ajustements
        }
    except Exception as e:
        print(f"⚠️ Erreur critères dynamiques: {e}")
        return {'scores_confiance': {}, 'ajustements_recents': []}

def normaliser_categorie(categorie):
    """
    Normalise les noms de catégories pour assurer la cohérence.
    Mappe les variations vers les noms standardisés utilisés dans categorie_actif.
    """
    if not categorie:
        return categorie

    categorie_lower = categorie.lower().strip()

    # Mapping des variations vers les noms standardisés
    mapping = {
        # Indices
        'indices': 'indice',
        'index': 'indice',
        # Actions EU
        'actions_eu': 'action_eu',
        'actions européennes': 'action_eu',
        'actions eu': 'action_eu',
        # Actions US
        'actions_us': 'action_us',
        'actions américaines': 'action_us',
        'actions us': 'action_us',
        # Commodités
        'commodites': 'commodite',
        'commodités': 'commodite',
        'matières premières': 'commodite',
    }

    return mapping.get(categorie_lower, categorie_lower)


# ============================================================================
# CRITÈRES DYNAMIQUES ACTIFS
# ============================================================================

def get_criteres_dynamiques_actifs():
    """
    Récupère les critères dynamiques actifs (scores de confiance, exclusions).

    CORRIGÉ: Ajout d'expiration dynamique basée sur l'âge des données
    CORRIGÉ: Typage explicite des scores en int
    """
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        maintenant = get_paris_time()

        # Récupérer les derniers scores de confiance par catégorie
        # CORRIGÉ: Exclure les scores de plus de 14 jours (obsolètes)
        cursor.execute('''
            SELECT categorie, critere, valeur_actuelle, raison, date_maj
            FROM criteres_dynamiques
            WHERE critere = 'score_confiance'
            AND date_maj > date('now', '-14 days')
            AND id IN (
                SELECT MAX(id) FROM criteres_dynamiques
                WHERE critere = 'score_confiance'
                GROUP BY categorie
            )
        ''')

        scores = {}
        for row in cursor.fetchall():
            # CORRIGÉ: Typage explicite en int
            try:
                score_val = int(float(row['valeur_actuelle']))
            except (ValueError, TypeError):
                score_val = 50  # Valeur par défaut si conversion échoue

            # Calculer l'âge en jours
            date_maj = row['date_maj']
            if isinstance(date_maj, str):
                try:
                    date_maj = datetime.fromisoformat(date_maj.replace('Z', '+00:00'))
                except:
                    date_maj = maintenant
            age_jours = (maintenant - date_maj).days if hasattr(date_maj, 'days') else 0

            scores[row['categorie']] = {
                'score': score_val,
                'raison': row['raison'],
                'date_maj': row['date_maj'],
                'age_jours': age_jours
            }

        # CORRIGÉ: Expiration dynamique des ajustements selon le type
        # - Actions urgentes (ÉVITER): 10 jours (permet feedback 7j + marge)
        # - Actions prudentes (RÉDUIRE/FAVORISER): 21 jours (2 semaines de feedback)
        # CORRIGÉ: Filtre explicite type_ajustement = 'strategie' pour éviter duplication avec scores
        cursor.execute('''
            SELECT categorie, critere, action, raison, date_decision
            FROM ajustements_proposes
            WHERE statut = 'valide'
            AND type_ajustement = 'strategie'
            AND (
                (action = 'ÉVITER' AND date_decision > date('now', '-10 days'))
                OR (action IN ('RÉDUIRE', 'FAVORISER') AND date_decision > date('now', '-21 days'))
            )
        ''')

        ajustements_actifs = []
        for row in cursor.fetchall():
            ajust = dict(row)
            # Calculer l'âge en jours pour pondération
            date_decision = row['date_decision']
            if isinstance(date_decision, str):
                try:
                    date_decision = datetime.fromisoformat(date_decision.replace('Z', '+00:00'))
                    ajust['age_jours'] = (maintenant - date_decision).days
                except:
                    ajust['age_jours'] = 0
            ajustements_actifs.append(ajust)

        conn.close()

        return {
            'scores_confiance': scores,
            'ajustements_actifs': ajustements_actifs
        }

    except Exception as e:
        print(f"⚠️ Erreur critères dynamiques: {e}")
        return {'scores_confiance': {}, 'ajustements_actifs': []}

def generer_instructions_dynamiques():
    """
    Génère les instructions dynamiques à injecter dans le SYSTEM_PROMPT.

    CORRIGÉ: Trou des seuils 50-80 comblé (ajout 'NORMAL')
    CORRIGÉ: Pondération temporelle (ajustements récents prioritaires)
    """
    criteres = get_criteres_dynamiques_actifs()
    instructions = []

    # Analyser les scores de confiance
    # CORRIGÉ: Couverture complète des seuils (0-30, 30-50, 50-65, 65-80, 80+)
    for categorie, data in criteres.get('scores_confiance', {}).items():
        score = data.get('score', 50)
        age = data.get('age_jours', 0)
        age_info = f" (mis à jour il y a {age}j)" if age > 3 else ""

        if score < 30:
            instructions.append(f"⛔ ÉVITER {categorie.upper()}: Score {score}/100{age_info} - {data.get('raison', '')}")
        elif score < 50:
            instructions.append(f"⚠️ PRUDENCE {categorie.upper()}: Score {score}/100{age_info} - Limiter les positions")
        elif score < 65:
            # CORRIGÉ: Zone neutre explicite
            instructions.append(f"➖ NORMAL {categorie.upper()}: Score {score}/100{age_info} - Pas de biais particulier")
        elif score < 80:
            instructions.append(f"👍 BON {categorie.upper()}: Score {score}/100{age_info} - Conditions favorables")
        else:
            instructions.append(f"✅ EXCELLENT {categorie.upper()}: Score {score}/100{age_info} - Opportunités prioritaires")

    # CORRIGÉ: Ajustements triés par priorité (récents et urgents d'abord)
    ajustements = criteres.get('ajustements_actifs', [])

    # Priorité: ÉVITER > RÉDUIRE > FAVORISER, puis par âge croissant
    def priorite_ajustement(a):
        action_prio = {'ÉVITER': 0, 'RÉDUIRE': 1, 'FAVORISER': 2}.get(a.get('action', ''), 3)
        age = a.get('age_jours', 0)
        return (action_prio, age)

    ajustements_tries = sorted(ajustements, key=priorite_ajustement)

    for ajust in ajustements_tries:
        age = ajust.get('age_jours', 0)
        # CORRIGÉ: Pondération temporelle dans le message
        if age == 0:
            recence = "🆕 NOUVEAU"
        elif age <= 2:
            recence = "📍 RÉCENT"
        else:
            recence = f"📅 {age}j"

        if ajust.get('action') == 'ÉVITER':
            instructions.append(f"⛔ [{recence}] {ajust.get('categorie', '').upper()}: {ajust.get('raison', '')}")
        elif ajust.get('action') == 'RÉDUIRE':
            instructions.append(f"⚠️ [{recence}] RÉDUIRE {ajust.get('categorie', '').upper()}: {ajust.get('raison', '')}")
        elif ajust.get('action') == 'FAVORISER':
            instructions.append(f"✅ [{recence}] FAVORISER {ajust.get('categorie', '').upper()}: {ajust.get('raison', '')}")

    if not instructions:
        return ""

    return "\n\nCRITÈRES DYNAMIQUES ACTIFS (basés sur les performances récentes):\n" + "\n".join(instructions)


