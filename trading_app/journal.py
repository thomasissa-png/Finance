"""
Journal des actifs, rapports quotidiens/hebdomadaires, stockage analyses.
"""
import sqlite3
import json
from datetime import datetime, timedelta

import pandas as pd

from .config import client_anthropic, logger, DB_PATH, DB_TIMEOUT, to_python_type
from .constants import ACTIFS_PERMANENTS, SYMBOLES_ACTIONS_US
from .market_context import get_paris_time, get_market_context, get_regime_marche
from .market_data import recuperer_donnees_marche, get_twelvedata_batch
from .trades import get_trades_du_jour, get_performances
from .validation import extraire_json_claude

def ajouter_entree_journal(symbole, nom_actif, type_info, titre, contenu, impact_cours='', importance=2):
    """Ajoute une entrée au journal d'un actif"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        cursor = conn.cursor()
        maintenant = get_paris_time()

        cursor.execute('''
            INSERT INTO journal_actifs
            (date, symbole, nom_actif, type_info, titre, contenu, impact_cours, importance, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            maintenant.date(),
            symbole,
            nom_actif,
            type_info,
            titre,
            contenu,
            impact_cours,
            importance,
            maintenant
        ))

        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"⚠️ Erreur ajout journal: {e}")
        return False

def get_journal_actif(symbole, limite=50):
    """Récupère le journal d'un actif"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM journal_actifs
            WHERE symbole = ?
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (symbole, limite))

        entries = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return entries
    except Exception as e:
        print(f"⚠️ Erreur journal: {e}")
        return []

def get_tous_journaux():
    """Récupère tous les journaux regroupés par actif"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('''
            SELECT DISTINCT symbole, nom_actif,
                   COUNT(*) as nb_entrees,
                   MAX(timestamp) as derniere_maj
            FROM journal_actifs
            GROUP BY symbole
            ORDER BY derniere_maj DESC
        ''')

        actifs = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return actifs
    except Exception as e:
        print(f"⚠️ Erreur journaux: {e}")
        return []

# ============================================================================
# CATÉGORIES D'ACTIFS
# ============================================================================

def get_categorie_actif(symbole):
    """Détermine la catégorie d'un actif"""
    if symbole.startswith('^'):
        return 'indice'
    elif symbole.endswith('.PA') or symbole.endswith('.DE') or symbole.endswith('.MI'):
        return 'action_eu'
    elif symbole.endswith('=X'):
        return 'forex'
    elif symbole.endswith('=F'):
        return 'commodite'
    elif len(symbole) <= 5 and symbole.isalpha():
        return 'action_us'
    return 'autre'

# ============================================================================
# PRÉ-MARKET
# ============================================================================

def recuperer_donnees_premarket():
    """Récupère les données pré-market (futures, overnight gaps)
    OPTIMISÉ: Utilise BATCH API au lieu d'appels individuels"""
    premarket_symbols = {
        "ES=F": "S&P 500 Futures",
        "NQ=F": "Nasdaq Futures",
        "YM=F": "Dow Futures",
        "^N225": "Nikkei 225",
        "^HSI": "Hang Seng",
        "^STOXX50E": "Euro Stoxx 50"
    }

    donnees = {}

    # BATCH API: un seul appel pour tous les symboles
    symboles_list = list(premarket_symbols.keys())
    batch_results = get_twelvedata_batch(symboles_list, outputsize=2, interval="1day")

    for symbole, nom in premarket_symbols.items():
        try:
            info, _ = batch_results.get(symbole, (pd.DataFrame(), False))
            if not info.empty and len(info) >= 2:
                prix_hier = info['Close'].iloc[-2]
                prix_actuel = info['Close'].iloc[-1]
                if prix_hier > 0:  # Évite division par zéro
                    variation = ((prix_actuel - prix_hier) / prix_hier) * 100
                    donnees[nom] = {
                        "symbole": symbole,
                        "prix": float(round(prix_actuel, 2)),
                        "prix_hier": float(round(prix_hier, 2)),
                        "variation_overnight": float(round(variation, 2))
                    }
        except Exception as e:
            print(f"⚠️ Erreur premarket {symbole}: {e}")

    return donnees


# ============================================================================
# JOURNAL QUOTIDIEN AUTOMATIQUE
# ============================================================================

SYSTEM_PROMPT_JOURNAL = """Tu es un spéculateur expérimenté qui tient un carnet de bord quotidien.

Pour chaque actif, rédige un commentaire UNIQUEMENT si quelque chose de notable s'est passé:
- Mouvement significatif (>1.5% ou inhabituel pour l'actif)
- Cassure de niveau technique important
- News ou événement macro impactant
- Nouveau record historique ou annuel
- Changement de tendance ou de momentum
- Corrélation intéressante avec d'autres actifs

Si rien de notable, ne mets PAS de commentaire pour cet actif.

Style: Direct, factuel, langage de trader. Pas de langue de bois.
Exemples de bons commentaires:
- "Nouveau record historique à 6150. Le momentum reste puissant, pas de signe d'essoufflement."
- "Breakout des 2050$ confirmé sur fond de tensions Iran. Le prochain objectif technique est 2100$."
- "Chute de 4% suite aux résultats décevants. Support à surveiller à 145€."
- "Range serré 1.08-1.085, attente des NFP demain."

FORMAT DE RÉPONSE EN JSON:
{
  "commentaires": {
    "SYMBOLE1": "Commentaire si notable...",
    "SYMBOLE2": "Commentaire si notable..."
  },
  "faits_marquants": [
    "Fait marquant 1 de la journée (ex: S&P 500 nouveau record, tensions géopolitiques, etc.)",
    "Fait marquant 2"
  ]
}"""

SYSTEM_PROMPT_BILAN = """Tu es un spéculateur expérimenté qui fait le bilan de la journée de trading.
Analyse les données du jour et rédige un bilan honnête et constructif.

Le bilan doit inclure:
1. Un résumé des marchés du jour (3-4 phrases)
2. Les points positifs: ce qui a bien fonctionné dans les recommandations
3. Les points négatifs: ce qui n'a pas fonctionné, les erreurs
4. Les leçons apprises: ce qu'on peut retenir pour demain

Sois direct, factuel et autocritique. Pas de langue de bois.

FORMAT DE RÉPONSE EN JSON:
{
  "resume": "Paragraphe résumant la journée des marchés...",
  "points_positifs": ["Point 1", "Point 2"],
  "points_negatifs": ["Point 1", "Point 2"],
  "lecons_apprises": ["Leçon 1", "Leçon 2"]
}"""

SYSTEM_PROMPT_RAPPORT_HEBDO = """Tu es un spéculateur expérimenté qui analyse la performance hebdomadaire de ton système de trading.

Analyse les données de la semaine et génère un rapport complet incluant:
1. Un résumé exécutif de la semaine (performance globale, contexte marché)
2. Analyse des forces et faiblesses identifiées
3. Les patterns observés (heures rentables, types de setups qui marchent)
4. ANALYSE DES INDICATEURS: Quel RSI, MACD, ATR a généré les meilleurs résultats?
5. Recommandations d'ajustement pour la semaine prochaine
6. Score de confiance pour chaque catégorie d'actifs et type de setup

FOCUS SUR LES INDICATEURS (données fournies):
- stats_par_rsi: Performance par signal RSI (SURVENTE/NEUTRE/SURACHAT)
- stats_par_macd: Performance par signal MACD (BULLISH/BEARISH/CROSS)
- stats_par_ratio_rr: Performance par ratio Risk/Reward
- stats_par_atr: Performance par niveau de volatilité (FAIBLE/MOYEN/ELEVE)

Utilise ces données pour recommander des CRITÈRES PRÉCIS, ex:
- "Privilégier RSI SURVENTE pour LONG (taux 75% cette semaine)"
- "Éviter MACD BEARISH pour les indices (taux 30%)"

Sois analytique, data-driven et autocritique.

FORMAT DE RÉPONSE EN JSON:
{
  "resume_executif": "Paragraphe résumant la semaine...",
  "chiffres_cles": {
    "nb_trades": 0,
    "taux_reussite": 0,
    "pnl_total": 0,
    "meilleur_jour": "",
    "pire_jour": ""
  },
  "forces": ["Force 1", "Force 2"],
  "faiblesses": ["Faiblesse 1", "Faiblesse 2"],
  "patterns_identifies": [
    {"pattern": "Description", "recommandation": "Action à prendre"}
  ],
  "analyse_indicateurs": {
    "rsi_optimal": "RSI qui a le mieux performé + recommandation",
    "macd_optimal": "Signal MACD le plus rentable + recommandation",
    "atr_optimal": "Niveau ATR le plus rentable + recommandation",
    "ratio_rr_optimal": "Ratio R/R le plus performant"
  },
  "ajustements_recommandes": [
    {"critere": "Nom du critère", "action": "Augmenter/Réduire/Modifier", "raison": "Justification basée sur données"}
  ],
  "scores_confiance": {
    "indice": {"score": 0, "tendance": "hausse/baisse/stable"},
    "action_eu": {"score": 0, "tendance": ""},
    "action_us": {"score": 0, "tendance": ""},
    "commodite": {"score": 0, "tendance": ""},
    "forex": {"score": 0, "tendance": ""},
    "news_trading": {"score": 0, "tendance": ""},
    "technique": {"score": 0, "tendance": ""}
  },
  "focus_semaine_prochaine": ["Point 1", "Point 2"]
}"""

def generer_commentaires_journal(donnees_actifs):
    """Génère des commentaires AI pour les actifs du journal (style carnet de bord)"""
    if not donnees_actifs:
        return {}, []

    donnees_texte = json.dumps(donnees_actifs, ensure_ascii=False, indent=2)
    maintenant = get_paris_time()

    question = f"""Voici les données des actifs pour le {maintenant.strftime('%d/%m/%Y')}:

{donnees_texte}

Rédige ton carnet de bord de spéculateur:
1. Commente UNIQUEMENT les actifs avec des mouvements notables
2. Liste les 2-4 faits marquants de la journée (records, événements macro, etc.)

Sois direct et factuel, comme un trader pro."""

    try:
        message = client_anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=3000,
            system=SYSTEM_PROMPT_JOURNAL,
            messages=[{"role": "user", "content": question}]
        )

        reponse = message.content[0].text
        result, erreur = extraire_json_claude(reponse)
        if erreur:
            logger.warning(f"Extraction JSON journal échouée: {erreur}")
            return {}, []
        return result.get('commentaires', {}), result.get('faits_marquants', [])
    except Exception as e:
        logger.error(f"Erreur commentaires journal: {e}")
        return {}, []

def recuperer_recommandations_analystes(symbole):
    """Récupère les recommandations des analystes
    Note: Twelve Data ne fournit pas les recommandations analystes,
    cette fonctionnalité nécessiterait une autre source de données."""
    # Twelve Data ne propose pas cette fonctionnalité
    # Retourne une liste vide pour l'instant
    return []

def generer_bilan_quotidien():
    """Génère le bilan général de la journée"""
    maintenant = get_paris_time()
    aujourdhui = maintenant.date()

    print(f"[{maintenant.strftime('%H:%M:%S')} CET] 📊 Génération bilan quotidien...")

    try:
        # Récupérer les données de la journée
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Récupérer les trades CONCLUS du jour (exclure trades ouverts pour stats fiables)
        cursor.execute('''
            SELECT * FROM trades_recommandes
            WHERE date = ? AND resultat IS NOT NULL
        ''', (aujourdhui,))
        trades_jour = [dict(row) for row in cursor.fetchall()]

        # Compter les trades ouverts séparément
        cursor.execute('''
            SELECT COUNT(*) FROM trades_recommandes
            WHERE date = ? AND resultat IS NULL
        ''', (aujourdhui,))
        nb_trades_ouverts = cursor.fetchone()[0] or 0

        # Récupérer les journaux du jour
        cursor.execute('''
            SELECT * FROM journal_quotidien WHERE date = ?
        ''', (aujourdhui,))
        journaux_jour = [dict(row) for row in cursor.fetchall()]

        conn.close()

        # Préparer les données pour le prompt
        donnees_bilan = {
            'date': str(aujourdhui),
            'nb_trades_conclus': len(trades_jour),
            'nb_trades_ouverts': nb_trades_ouverts,
            'trades': [{
                'actif': t['actif'],
                'direction': t.get('direction', 'LONG'),
                'type': t.get('type_setup', 'TECH'),
                'resultat': t.get('resultat', 'En cours'),
                'pnl': t.get('pnl_pct')
            } for t in trades_jour],
            'resume_actifs': [{
                'nom': j['nom_actif'],
                'variation': j['variation_jour']
            } for j in journaux_jour[:10]]
        }

        donnees_texte = json.dumps(donnees_bilan, ensure_ascii=False, indent=2)

        question = f"""Voici les données de trading pour le {maintenant.strftime('%d/%m/%Y')}:

{donnees_texte}

Fais un bilan honnête de cette journée de trading. Qu'est-ce qui a fonctionné ? Qu'est-ce qui n'a pas fonctionné ? Quelles leçons en tirer ?"""

        message = client_anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            system=SYSTEM_PROMPT_BILAN,
            messages=[{"role": "user", "content": question}]
        )

        reponse = message.content[0].text
        bilan, erreur = extraire_json_claude(reponse)
        if erreur:
            logger.warning(f"Extraction JSON bilan échouée: {erreur}")
            return None
        if bilan:
            # Sauvegarder le bilan
            conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO bilan_quotidien
                (date, contenu, points_positifs, points_negatifs, lecons_apprises, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                aujourdhui,
                bilan.get('resume', ''),
                json.dumps(bilan.get('points_positifs', []), ensure_ascii=False),
                json.dumps(bilan.get('points_negatifs', []), ensure_ascii=False),
                json.dumps(bilan.get('lecons_apprises', []), ensure_ascii=False),
                maintenant
            ))
            conn.commit()
            conn.close()

            print(f"[{maintenant.strftime('%H:%M:%S')} CET] ✅ Bilan quotidien enregistré")
            return bilan
        return None
    except Exception as e:
        logger.error(f"Erreur bilan quotidien: {e}")
        return None

def enregistrer_journal_quotidien(actifs_a_traiter=None):
    """Enregistre le journal quotidien pour les actifs spécifiés ou tous"""
    maintenant = get_paris_time()
    aujourdhui = maintenant.date()

    # Si pas d'actifs spécifiés, traiter tous les actifs permanents
    if actifs_a_traiter is None:
        actifs_a_traiter = ACTIFS_PERMANENTS

    print(f"[{maintenant.strftime('%H:%M:%S')} CET] 📝 Enregistrement journal quotidien ({len(actifs_a_traiter)} actifs)...")

    try:
        # Récupérer les données des actifs
        donnees = recuperer_donnees_marche(actifs_a_traiter)

        if not donnees:
            # Retry une fois après 5 secondes (cache peut avoir expiré)
            print(f"[{maintenant.strftime('%H:%M:%S')} CET] ⚠️ Aucune donnée - retry dans 5s...")
            import time as _time
            _time.sleep(5)
            donnees = recuperer_donnees_marche(actifs_a_traiter)

        if not donnees:
            print(f"[{maintenant.strftime('%H:%M:%S')} CET] ❌ Aucune donnée marché après retry - journal vide")
            logger.error("enregistrer_journal_quotidien: aucune donnée marché (API down ou marchés fermés)")
            return False

        print(f"[{maintenant.strftime('%H:%M:%S')} CET] 📊 {len(donnees)}/{len(actifs_a_traiter)} actifs récupérés pour le journal")

        # Récupérer les opportunités du jour pour chaque actif
        opportunites_jour = {}
        try:
            conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('''
                SELECT actif, symbole, type_setup, prix_entree, prix_tp1, resultat, direction
                FROM trades_recommandes WHERE date = ?
            ''', (aujourdhui,))
            for row in cursor.fetchall():
                symbole = row['symbole']
                if symbole not in opportunites_jour:
                    opportunites_jour[symbole] = []
                opportunites_jour[symbole].append({
                    'type': row['type_setup'],
                    'entree': row['prix_entree'],
                    'tp1': row['prix_tp1'],
                    'resultat': row['resultat'],
                    'direction': row['direction']
                })
            conn.close()
        except Exception as e:
            print(f"⚠️ Erreur récup opportunités: {e}")

        # Préparer les données pour les commentaires AI
        donnees_pour_ia = {}
        for nom, data in donnees.items():
            donnees_pour_ia[data['symbole']] = {
                'nom': nom,
                'prix': data['prix'],
                'haut': data.get('haut', 0),
                'bas': data.get('bas', 0),
                'variation': data['variation'],
                'variation_5j': data.get('variation_5j', 0)
            }

        # Générer les commentaires AI
        commentaires, faits_marquants = generer_commentaires_journal(donnees_pour_ia)

        # Sauvegarder les faits marquants dans la table bilan
        if faits_marquants:
            try:
                conn_bilan = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
                cursor_bilan = conn_bilan.cursor()
                cursor_bilan.execute('''
                    INSERT OR REPLACE INTO bilan_quotidien
                    (date, faits_marquants, timestamp)
                    VALUES (?, ?, ?)
                    ON CONFLICT(date) DO UPDATE SET faits_marquants = excluded.faits_marquants
                ''', (
                    aujourdhui,
                    json.dumps(faits_marquants, ensure_ascii=False),
                    maintenant
                ))
                conn_bilan.commit()
                conn_bilan.close()
            except Exception as e:
                print(f"⚠️ Erreur sauvegarde faits marquants: {e}")

        # Enregistrer dans la base
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        cursor = conn.cursor()

        for nom, data in donnees.items():
            symbole = data['symbole']
            categorie = get_categorie_actif(symbole)
            opps = opportunites_jour.get(symbole, [])

            # Note: Les recommandations analystes ne sont pas disponibles via Twelve Data
            # La fonction recuperer_recommandations_analystes retourne toujours []
            # On garde le champ vide pour compatibilité future
            recos = []

            # Récupérer volume_relatif depuis les données (si disponible)
            volume_rel = data.get('volume_relatif', data.get('volume_relatif_pct', 0))
            if not volume_rel and 'indicateurs' in data:
                volume_rel = data['indicateurs'].get('volume_relatif_pct', 0)

            cursor.execute('''
                INSERT OR REPLACE INTO journal_quotidien
                (date, symbole, nom_actif, categorie, prix_ouverture, prix_cloture,
                 prix_max, prix_min, variation_jour, volume_relatif, commentaire_ia,
                 evenements_jour, opportunites_jour, recommandations_analystes, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                aujourdhui,
                symbole,
                nom,
                categorie,
                data.get('ouverture', 0),
                data['prix'],
                data.get('haut', 0),
                data.get('bas', 0),
                data['variation'],
                volume_rel or 0,  # Volume relatif (% vs moyenne 20j)
                commentaires.get(symbole, ''),
                '',  # evenements_jour - TODO: intégrer calendrier économique
                json.dumps(opps, ensure_ascii=False) if opps else '',
                json.dumps(recos, ensure_ascii=False) if recos else '',
                maintenant
            ))

        conn.commit()
        conn.close()
        print(f"[{maintenant.strftime('%H:%M:%S')} CET] ✅ Journal quotidien enregistré")
        return True
    except Exception as e:
        logger.error(f"Erreur journal quotidien: {e}")
        return False

def enregistrer_journal_fr():
    """Enregistre le journal pour les actions françaises (18h00)"""
    actifs_fr = {k: v for k, v in ACTIFS_PERMANENTS.items()
                 if k.endswith('.PA') or k.startswith('^FCHI') or k == '^GDAXI'}
    return enregistrer_journal_quotidien(actifs_fr)

def generer_rapport_hebdo():
    """Génère le rapport hebdomadaire avec analyse et ajustements

    CORRIGÉ: Période basée sur semaine ISO calendaire (lundi-dimanche)
    CORRIGÉ: Exclut les trades ouverts (sans résultat) des statistiques
    """
    maintenant = get_paris_time()
    print(f"[{maintenant.strftime('%H:%M:%S')} CET] 📊 Génération rapport hebdomadaire...")

    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # CORRIGÉ: Calculer la semaine ISO précédente (lundi-dimanche)
        # Si on est samedi, on prend la semaine qui vient de se terminer
        jour_actuel = maintenant.date()
        # Trouver le lundi de la semaine précédente
        jours_depuis_lundi = jour_actuel.weekday()  # 0=lundi, 6=dimanche
        lundi_semaine_courante = jour_actuel - timedelta(days=jours_depuis_lundi)
        # Semaine précédente = lundi - 7 jours
        date_debut = lundi_semaine_courante - timedelta(days=7)
        date_fin = date_debut + timedelta(days=6)  # Dimanche de cette semaine
        semaine = f"{date_debut.year}-W{date_debut.isocalendar()[1]:02d}"

        # Récupérer les trades de la semaine (tous, pour contexte)
        cursor.execute('''
            SELECT * FROM trades_recommandes
            WHERE date >= ? AND date <= ?
            ORDER BY timestamp_reco
        ''', (date_debut, date_fin))
        trades_semaine = [dict(row) for row in cursor.fetchall()]

        # CORRIGÉ: Stats détaillées - EXCLURE trades ouverts (resultat IS NOT NULL)
        cursor.execute('''
            SELECT
                COUNT(*) as nb_trades,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as stops,
                SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl_total,
                AVG(CASE WHEN duree_minutes IS NOT NULL THEN duree_minutes END) as duree_moyenne
            FROM trades_recommandes
            WHERE date >= ? AND date <= ?
            AND resultat IS NOT NULL
        ''', (date_debut, date_fin))
        stats_globales = dict(cursor.fetchone())

        # Compter aussi les trades encore ouverts pour info
        cursor.execute('''
            SELECT COUNT(*) as nb_ouverts
            FROM trades_recommandes
            WHERE date >= ? AND date <= ? AND resultat IS NULL
        ''', (date_debut, date_fin))
        nb_ouverts = cursor.fetchone()['nb_ouverts'] or 0
        stats_globales['nb_ouverts'] = nb_ouverts

        # CORRIGÉ: Stats par jour - EXCLURE trades ouverts
        cursor.execute('''
            SELECT date,
                   COUNT(*) as nb,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
            FROM trades_recommandes
            WHERE date >= ? AND date <= ?
            AND resultat IS NOT NULL
            GROUP BY date
            ORDER BY pnl DESC
        ''', (date_debut, date_fin))
        stats_par_jour = [dict(row) for row in cursor.fetchall()]

        # Stats par catégorie (CORRIGÉ: exclure trades ouverts)
        cursor.execute('''
            SELECT categorie_actif,
                   COUNT(*) as nb,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
            FROM trades_recommandes
            WHERE date >= ? AND date <= ? AND categorie_actif IS NOT NULL
            AND resultat IS NOT NULL
            GROUP BY categorie_actif
        ''', (date_debut, date_fin))
        stats_par_categorie = [dict(row) for row in cursor.fetchall()]

        # Stats par type (CORRIGÉ: exclure trades ouverts)
        cursor.execute('''
            SELECT type_setup,
                   COUNT(*) as nb,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
            FROM trades_recommandes
            WHERE date >= ? AND date <= ? AND type_setup IS NOT NULL
            AND resultat IS NOT NULL
            GROUP BY type_setup
        ''', (date_debut, date_fin))
        stats_par_type = [dict(row) for row in cursor.fetchall()]

        # Stats par heure (CORRIGÉ: exclure trades ouverts)
        cursor.execute('''
            SELECT heure_entree,
                   COUNT(*) as nb,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins
            FROM trades_recommandes
            WHERE date >= ? AND date <= ? AND heure_entree IS NOT NULL
            AND resultat IS NOT NULL
            GROUP BY heure_entree
            ORDER BY heure_entree
        ''', (date_debut, date_fin))
        stats_par_heure = [dict(row) for row in cursor.fetchall()]

        # Stats par signal RSI (CORRIGÉ: exclure trades ouverts)
        cursor.execute('''
            SELECT rsi_signal_reco,
                   COUNT(*) as nb,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
            FROM trades_recommandes
            WHERE date >= ? AND date <= ? AND rsi_signal_reco IS NOT NULL
            AND resultat IS NOT NULL
            GROUP BY rsi_signal_reco
        ''', (date_debut, date_fin))
        stats_par_rsi = [dict(row) for row in cursor.fetchall()]

        # Stats par signal MACD (CORRIGÉ: exclure trades ouverts)
        cursor.execute('''
            SELECT macd_signal_reco,
                   COUNT(*) as nb,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
            FROM trades_recommandes
            WHERE date >= ? AND date <= ? AND macd_signal_reco IS NOT NULL
            AND resultat IS NOT NULL
            GROUP BY macd_signal_reco
        ''', (date_debut, date_fin))
        stats_par_macd = [dict(row) for row in cursor.fetchall()]

        # Stats par ratio R/R (CORRIGÉ: exclure trades ouverts)
        cursor.execute('''
            SELECT ratio_rr,
                   COUNT(*) as nb,
                   SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
            FROM trades_recommandes
            WHERE date >= ? AND date <= ? AND ratio_rr IS NOT NULL AND ratio_rr != ''
            AND resultat IS NOT NULL
            GROUP BY ratio_rr
        ''', (date_debut, date_fin))
        stats_par_ratio_rr = [dict(row) for row in cursor.fetchall()]

        # Stats par niveau ATR (CORRIGÉ: exclure trades ouverts)
        cursor.execute('''
            SELECT
                CASE
                    WHEN atr_pct_reco < 1.5 THEN 'ATR_FAIBLE (<1.5%)'
                    WHEN atr_pct_reco < 2.5 THEN 'ATR_MOYEN (1.5-2.5%)'
                    ELSE 'ATR_ELEVE (>2.5%)'
                END as niveau_atr,
                COUNT(*) as nb,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
            FROM trades_recommandes
            WHERE date >= ? AND date <= ? AND atr_pct_reco IS NOT NULL
            AND resultat IS NOT NULL
            GROUP BY niveau_atr
        ''', (date_debut, date_fin))
        stats_par_atr = [dict(row) for row in cursor.fetchall()]

        # === STATS A/B TESTING AVANCÉ (CORRIGÉ: exclure trades ouverts) ===

        # Stats par jour de la semaine (saisonnalité)
        cursor.execute('''
            SELECT jour_semaine,
                COUNT(*) as nb,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
            FROM trades_recommandes
            WHERE date >= ? AND date <= ? AND jour_semaine IS NOT NULL
            AND resultat IS NOT NULL
            GROUP BY jour_semaine
            ORDER BY CASE jour_semaine
                WHEN 'LUNDI' THEN 1
                WHEN 'MARDI' THEN 2
                WHEN 'MERCREDI' THEN 3
                WHEN 'JEUDI' THEN 4
                WHEN 'VENDREDI' THEN 5
                ELSE 6 END
        ''', (date_debut, date_fin))
        stats_par_jour_semaine = [dict(row) for row in cursor.fetchall()]

        # Stats par session de marché
        cursor.execute('''
            SELECT session_marche,
                COUNT(*) as nb,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
            FROM trades_recommandes
            WHERE date >= ? AND date <= ? AND session_marche IS NOT NULL
            AND resultat IS NOT NULL
            GROUP BY session_marche
        ''', (date_debut, date_fin))
        stats_par_session = [dict(row) for row in cursor.fetchall()]

        # Stats par score de conviction
        cursor.execute('''
            SELECT conviction_score,
                COUNT(*) as nb,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
            FROM trades_recommandes
            WHERE date >= ? AND date <= ? AND conviction_score IS NOT NULL
            AND resultat IS NOT NULL
            GROUP BY conviction_score
            ORDER BY conviction_score
        ''', (date_debut, date_fin))
        stats_par_conviction = [dict(row) for row in cursor.fetchall()]

        # Stats par stratégie d'entrée
        cursor.execute('''
            SELECT strategie_entree,
                COUNT(*) as nb,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
            FROM trades_recommandes
            WHERE date >= ? AND date <= ? AND strategie_entree IS NOT NULL
            AND resultat IS NOT NULL
            GROUP BY strategie_entree
        ''', (date_debut, date_fin))
        stats_par_strategie_entree = [dict(row) for row in cursor.fetchall()]

        # Stats par régime de marché (VIX)
        cursor.execute('''
            SELECT regime_marche,
                COUNT(*) as nb,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl,
                AVG(vix_niveau) as vix_moyen
            FROM trades_recommandes
            WHERE date >= ? AND date <= ? AND regime_marche IS NOT NULL
            AND resultat IS NOT NULL
            GROUP BY regime_marche
        ''', (date_debut, date_fin))
        stats_par_regime = [dict(row) for row in cursor.fetchall()]

        # Stats trailing stop vs stop fixe
        cursor.execute('''
            SELECT
                CASE WHEN trailing_stop = 1 THEN 'TRAILING' ELSE 'FIXE' END as type_stop,
                COUNT(*) as nb,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
            FROM trades_recommandes
            WHERE date >= ? AND date <= ?
            AND resultat IS NOT NULL
            GROUP BY type_stop
        ''', (date_debut, date_fin))
        stats_par_type_stop = [dict(row) for row in cursor.fetchall()]

        # Stats par alignement multi-timeframe
        cursor.execute('''
            SELECT alignement_tf,
                COUNT(*) as nb,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
            FROM trades_recommandes
            WHERE date >= ? AND date <= ? AND alignement_tf IS NOT NULL
            AND resultat IS NOT NULL
            GROUP BY alignement_tf
            ORDER BY alignement_tf
        ''', (date_debut, date_fin))
        stats_par_alignement = [dict(row) for row in cursor.fetchall()]

        # Stats par sentiment (CORRIGÉ: exclure trades ouverts)
        cursor.execute('''
            SELECT
                CASE
                    WHEN sentiment_score < -2 THEN 'TRES_BEARISH'
                    WHEN sentiment_score < 0 THEN 'BEARISH'
                    WHEN sentiment_score = 0 THEN 'NEUTRE'
                    WHEN sentiment_score <= 2 THEN 'BULLISH'
                    ELSE 'TRES_BULLISH'
                END as sentiment_bucket,
                COUNT(*) as nb,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
            FROM trades_recommandes
            WHERE date >= ? AND date <= ? AND sentiment_score IS NOT NULL
            AND resultat IS NOT NULL
            GROUP BY sentiment_bucket
        ''', (date_debut, date_fin))
        stats_par_sentiment = [dict(row) for row in cursor.fetchall()]

        # Stats A/B Testing (CORRIGÉ: exclure trades ouverts + inclure WIN_FORCE)
        stats_ab_tests = []
        cursor.execute("SELECT * FROM ab_tests WHERE statut = 'actif'")
        tests_actifs = [dict(row) for row in cursor.fetchall()]

        for test in tests_actifs:
            test_id = test['id']
            # Stats groupe A
            cursor.execute('''
                SELECT COUNT(*) as nb,
                       SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                       SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
                FROM trades_recommandes
                WHERE ab_test_id = ? AND ab_groupe = 'A' AND date >= ? AND date <= ?
                AND resultat IS NOT NULL
            ''', (test_id, date_debut, date_fin))
            stats_a = dict(cursor.fetchone())

            # Stats groupe B
            cursor.execute('''
                SELECT COUNT(*) as nb,
                       SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as wins,
                       SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as pnl
                FROM trades_recommandes
                WHERE ab_test_id = ? AND ab_groupe = 'B' AND date >= ? AND date <= ?
                AND resultat IS NOT NULL
            ''', (test_id, date_debut, date_fin))
            stats_b = dict(cursor.fetchone())

            stats_ab_tests.append({
                'nom': test['nom'],
                'variante_a': test['variante_a'],
                'variante_b': test['variante_b'],
                'groupe_a': {
                    'trades': stats_a['nb'] or 0,
                    'wins': stats_a['wins'] or 0,
                    'pnl': round(stats_a['pnl'] or 0, 2)
                },
                'groupe_b': {
                    'trades': stats_b['nb'] or 0,
                    'wins': stats_b['wins'] or 0,
                    'pnl': round(stats_b['pnl'] or 0, 2)
                }
            })

        # Récupérer les leçons apprises des bilans quotidiens de la semaine
        cursor.execute('''
            SELECT date, lecons_apprises, points_negatifs
            FROM bilan_quotidien
            WHERE date >= ? AND date <= ?
            ORDER BY date
        ''', (date_debut, date_fin))
        bilans_semaine = []
        for row in cursor.fetchall():
            try:
                lecons = json.loads(row['lecons_apprises'] or '[]')
                points_negatifs = json.loads(row['points_negatifs'] or '[]')
                if lecons or points_negatifs:
                    bilans_semaine.append({
                        'date': str(row['date']),
                        'lecons': lecons[:2],  # Max 2 leçons par jour
                        'erreurs': points_negatifs[:2]  # Max 2 erreurs par jour
                    })
            except json.JSONDecodeError:
                pass

        conn.close()

        # Préparer les données pour Claude (incluant les analyses d'indicateurs)
        donnees_rapport = {
            'periode': f"{date_debut} au {date_fin}",
            'stats_globales': stats_globales,
            'stats_par_jour': stats_par_jour,
            'stats_par_categorie': stats_par_categorie,
            'stats_par_type': stats_par_type,
            'stats_par_heure': stats_par_heure,
            # Stats indicateurs
            'stats_par_rsi': stats_par_rsi,
            'stats_par_macd': stats_par_macd,
            'stats_par_ratio_rr': stats_par_ratio_rr,
            'stats_par_atr': stats_par_atr,
            # === NOUVELLES STATS A/B TESTING AVANCÉ ===
            'stats_par_jour_semaine': stats_par_jour_semaine,  # Saisonnalité
            'stats_par_session': stats_par_session,            # Sessions marché
            'stats_par_conviction': stats_par_conviction,      # Score conviction
            'stats_par_strategie_entree': stats_par_strategie_entree,  # IMMEDIATE/PULLBACK/etc
            'stats_par_regime': stats_par_regime,              # VIX regime
            'stats_par_type_stop': stats_par_type_stop,        # Trailing vs Fixe
            'stats_par_alignement': stats_par_alignement,      # Multi-timeframe
            'stats_par_sentiment': stats_par_sentiment,        # Sentiment score
            # Tests A/B en cours
            'tests_ab': stats_ab_tests,
            'nb_trades_total': len(trades_semaine),
            # Bilans quotidiens de la semaine (leçons apprises)
            'bilans_quotidiens': bilans_semaine
        }

        donnees_texte = json.dumps(donnees_rapport, ensure_ascii=False, indent=2, default=str)

        question = f"""Voici les données de trading de la semaine du {date_debut} au {date_fin}:

{donnees_texte}

Analyse ces performances et génère un rapport hebdomadaire complet.

ANALYSE EN PRIORITÉ:
1. INDICATEURS: RSI, MACD, ratio R/R, niveau ATR - lesquels performent le mieux?
2. SAISONNALITÉ: Quel jour de la semaine a le meilleur win rate? (stats_par_jour_semaine)
3. SESSIONS: EU_OPEN, US_OPEN, etc. - quelle session est la plus rentable? (stats_par_session)
4. CONVICTION: Les trades haute conviction (4-5) performent-ils mieux? (stats_par_conviction)
5. STRATÉGIE ENTRÉE: IMMEDIATE vs PULLBACK vs BREAKOUT - laquelle gagne? (stats_par_strategie_entree)
6. RÉGIME VIX: Performance en marché CALME vs VOLATILE vs EXTREME (stats_par_regime)
7. TRAILING STOP: Les trailing stops améliorent-ils les résultats? (stats_par_type_stop)
8. MULTI-TF: L'alignement des timeframes prédit-il le succès? (stats_par_alignement)
9. SENTIMENT: Le sentiment score prédit-il correctement la direction? (stats_par_sentiment)
10. TESTS A/B: Compare les performances des groupes A vs B
11. BILANS QUOTIDIENS: Les leçons apprises chaque jour (bilans_quotidiens) - éviter les erreurs répétées

RECOMMANDATIONS ATTENDUES:
- Quels jours/sessions privilégier ou éviter?
- Quel score de conviction minimum exiger?
- Quelle stratégie d'entrée favoriser?
- Dans quel régime de marché ajuster les règles?
- Le trailing stop doit-il être systématique?
- Quel alignement multi-TF minimum?

Propose des AJUSTEMENTS PRÉCIS et TESTABLES pour la semaine prochaine."""

        message = client_anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=3000,
            system=SYSTEM_PROMPT_RAPPORT_HEBDO,
            messages=[{"role": "user", "content": question}]
        )

        reponse = message.content[0].text
        rapport, erreur = extraire_json_claude(reponse)

        if erreur:
            logger.warning(f"Extraction JSON rapport hebdo échouée: {erreur}")
            return None

        if rapport:
            # Validation et normalisation des champs du rapport
            # Assurer que tous les champs requis existent avec des valeurs par défaut
            rapport.setdefault('resume_executif', 'Rapport généré automatiquement')
            rapport.setdefault('chiffres_cles', {})
            rapport.setdefault('forces', [])
            rapport.setdefault('faiblesses', [])
            rapport.setdefault('patterns_identifies', [])
            rapport.setdefault('ajustements_recommandes', [])
            rapport.setdefault('scores_confiance', {})
            rapport.setdefault('focus_semaine_prochaine', [])

            # Normaliser les scores de confiance (borner 0-100)
            for cat, data in rapport.get('scores_confiance', {}).items():
                if isinstance(data, dict) and 'score' in data:
                    try:
                        data['score'] = max(0, min(100, int(float(data['score']))))
                    except (ValueError, TypeError):
                        data['score'] = 50
            # Sauvegarder le rapport
            conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO rapports_hebdo
                (semaine, date_debut, date_fin, resume_executif, chiffres_cles,
                 forces, faiblesses, patterns, ajustements, scores_confiance, focus_semaine, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                semaine,
                date_debut,
                date_fin,
                rapport.get('resume_executif', ''),
                json.dumps(rapport.get('chiffres_cles', {}), ensure_ascii=False),
                json.dumps(rapport.get('forces', []), ensure_ascii=False),
                json.dumps(rapport.get('faiblesses', []), ensure_ascii=False),
                json.dumps(rapport.get('patterns_identifies', []), ensure_ascii=False),
                json.dumps(rapport.get('ajustements_recommandes', []), ensure_ascii=False),
                json.dumps(rapport.get('scores_confiance', {}), ensure_ascii=False),
                json.dumps(rapport.get('focus_semaine_prochaine', []), ensure_ascii=False),
                maintenant
            ))
            conn.commit()

            # Sauvegarder les ajustements comme propositions (attente validation)
            # Lazy import to avoid circular dependency
            from .adjustments import sauvegarder_ajustements_proposes
            sauvegarder_ajustements_proposes(
                rapport.get('ajustements_recommandes', []),
                rapport.get('scores_confiance', {}),
                source='rapport_hebdo'
            )

            conn.close()
            print(f"[{maintenant.strftime('%H:%M:%S')} CET] ✅ Rapport hebdomadaire généré (ajustements en attente de validation)")
            return rapport

        return None
    except Exception as e:
        logger.error(f"Erreur rapport hebdo: {e}")
        return None

def appliquer_ajustements_dynamiques(ajustements, scores_confiance):
    """Applique les ajustements dynamiques basés sur le rapport hebdo"""
    maintenant = get_paris_time()

    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        cursor = conn.cursor()

        # Sauvegarder les scores de confiance comme critères
        for categorie, score_data in scores_confiance.items():
            if isinstance(score_data, dict):
                # CORRIGÉ: Borner le score entre 0 et 100
                raw_score = score_data.get('score', 50)
                try:
                    score = max(0, min(100, int(float(raw_score))))
                except (ValueError, TypeError):
                    score = 50  # Valeur par défaut si conversion échoue
                tendance = score_data.get('tendance', 'stable')

                # Récupérer la valeur précédente
                cursor.execute('''
                    SELECT valeur_actuelle FROM criteres_dynamiques
                    WHERE categorie = ? AND critere = 'score_confiance'
                    ORDER BY date_maj DESC LIMIT 1
                ''', (categorie,))
                row = cursor.fetchone()
                valeur_precedente = row[0] if row else 50

                cursor.execute('''
                    INSERT INTO criteres_dynamiques
                    (date_maj, categorie, critere, valeur_actuelle, valeur_precedente, raison, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    maintenant.date(),
                    categorie,
                    'score_confiance',
                    score,
                    valeur_precedente,
                    f"Tendance: {tendance}",
                    maintenant
                ))

        # Sauvegarder les ajustements recommandés
        for ajust in ajustements:
            critere = ajust.get('critere', '')
            action = ajust.get('action', '')
            raison = ajust.get('raison', '')

            cursor.execute('''
                INSERT INTO criteres_dynamiques
                (date_maj, categorie, critere, valeur_actuelle, valeur_precedente, raison, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                maintenant.date(),
                'ajustement',
                critere,
                1 if 'augmenter' in action.lower() else -1 if 'reduire' in action.lower() else 0,
                0,
                f"{action}: {raison}",
                maintenant
            ))

        conn.commit()
        conn.close()
        print(f"[{maintenant.strftime('%H:%M:%S')} CET] ✅ Critères dynamiques mis à jour")

    except Exception as e:
        print(f"⚠️ Erreur ajustements dynamiques: {e}")

def get_criteres_dynamiques():
    """Récupère les critères dynamiques - délègue à adjustments.py (source unique)"""
    from .adjustments import get_criteres_dynamiques as _get
    return _get()

def normaliser_categorie(categorie):
    """Normalise une catégorie - délègue à adjustments.py (source unique)"""
    from .adjustments import normaliser_categorie as _norm
    return _norm(categorie)

def enregistrer_journal_complet():
    """Enregistre le journal complet + bilan (22h30).
    Les deux étapes sont indépendantes: si le journal échoue, le bilan s'exécute quand même."""
    # D'abord enregistrer le journal de tous les actifs
    try:
        enregistrer_journal_quotidien()
    except Exception as e:
        logger.error(f"Erreur enregistrement journal quotidien dans journal_complet: {e}")
        print(f"❌ Journal quotidien échoué: {e}")

    # Puis générer le bilan général (indépendant du journal)
    try:
        generer_bilan_quotidien()
    except Exception as e:
        logger.error(f"Erreur bilan quotidien dans journal_complet: {e}")
        print(f"❌ Bilan quotidien échoué: {e}")

def get_journal_quotidien(symbole=None, limite=30, date_from=None, date_to=None, offset=0):
    """Récupère le journal quotidien avec navigation par date et pagination"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Construction de la requête avec filtres
        where_clauses = []
        params = []

        if symbole:
            where_clauses.append('symbole = ?')
            params.append(symbole)

        if date_from:
            where_clauses.append('date >= ?')
            params.append(date_from)

        if date_to:
            where_clauses.append('date <= ?')
            params.append(date_to)

        where_sql = ' AND '.join(where_clauses) if where_clauses else '1=1'

        # Compter le total
        count_query = f'SELECT COUNT(*) FROM journal_quotidien WHERE {where_sql}'
        cursor.execute(count_query, params)
        total_count = cursor.fetchone()[0]

        # Récupérer les entrées
        query = f'''
            SELECT * FROM journal_quotidien
            WHERE {where_sql}
            ORDER BY date DESC, nom_actif ASC
            LIMIT ? OFFSET ?
        '''
        params.extend([limite, offset])
        cursor.execute(query, params)

        entries = []
        json_errors = []

        for row in cursor.fetchall():
            entry = dict(row)

            # Parser les champs JSON avec logging des erreurs
            for json_field in ['opportunites_jour', 'recommandations_analystes']:
                if entry.get(json_field):
                    try:
                        entry[json_field] = json.loads(entry[json_field])
                    except json.JSONDecodeError as e:
                        error_msg = f"JSON PARSE ERROR - journal_quotidien.{json_field} @ {entry.get('date')}/{entry.get('symbole')}: {str(e)[:100]}"
                        print(f"⚠️ {error_msg}")
                        json_errors.append({
                            'date': entry.get('date'),
                            'symbole': entry.get('symbole'),
                            'field': json_field,
                            'error': str(e)[:100]
                        })
                        entry[json_field] = []
                        entry[f'{json_field}_corrupted'] = True

            entries.append(entry)

        conn.close()

        # Log groupé si plusieurs erreurs
        if json_errors:
            print(f"🔴 JOURNAL JSON CORRUPTION: {len(json_errors)} erreurs détectées")

        return {
            'entries': entries,
            'total_count': total_count,
            'json_errors': json_errors
        }
    except Exception as e:
        print(f"⚠️ Erreur récup journal quotidien: {e}")
        return {'entries': [], 'total_count': 0, 'json_errors': []}

def get_historique_opportunites(symbole):
    """Récupère l'historique des opportunités pour un actif"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM trades_recommandes
            WHERE symbole = ?
            ORDER BY timestamp_reco DESC
            LIMIT 50
        ''', (symbole,))

        trades = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return trades
    except Exception as e:
        print(f"⚠️ Erreur historique opportunités: {e}")
        return []

# ============================================================================
# ANALYSES SAUVEGARDÉES
# ============================================================================

def sauvegarder_analyse(type_analyse, contenu, contexte=''):
    """Sauvegarde une analyse"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        cursor = conn.cursor()
        maintenant = get_paris_time()

        cursor.execute('''
            INSERT INTO analyses
            (date, type_analyse, heure, contenu, timestamp, contexte_marche)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            maintenant.date(),
            type_analyse,
            maintenant.strftime('%H:%M'),
            json.dumps(contenu, ensure_ascii=False) if isinstance(contenu, dict) else contenu,
            maintenant,
            contexte
        ))

        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"⚠️ Erreur sauvegarde analyse: {e}")
        return False

def get_derniere_analyse(type_analyse=None):
    """Récupère la dernière analyse"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        if type_analyse:
            cursor.execute('''
                SELECT * FROM analyses
                WHERE type_analyse = ?
                ORDER BY timestamp DESC LIMIT 1
            ''', (type_analyse,))
        else:
            cursor.execute('''
                SELECT * FROM analyses
                ORDER BY timestamp DESC LIMIT 1
            ''')

        row = cursor.fetchone()
        conn.close()

        if row:
            result = dict(row)
            try:
                result['contenu'] = json.loads(result['contenu'])
            except json.JSONDecodeError:
                pass
            return result
        return None
    except Exception as e:
        print(f"⚠️ Erreur récupération analyse: {e}")
        return None

def get_analyses_du_jour():
    """Récupère toutes les analyses du jour"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        aujourdhui = get_paris_time().date()
        cursor.execute('''
            SELECT * FROM analyses
            WHERE date = ?
            ORDER BY timestamp DESC
        ''', (aujourdhui,))

        analyses = []
        for row in cursor.fetchall():
            a = dict(row)
            try:
                a['contenu'] = json.loads(a['contenu'])
            except json.JSONDecodeError:
                pass
            analyses.append(a)

        conn.close()
        return analyses
    except Exception as e:
        print(f"⚠️ Erreur analyses jour: {e}")
        return []

# ============================================================================

