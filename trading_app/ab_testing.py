"""
Infrastructure de tests A/B pour les stratégies de trading.
"""
import sqlite3
import json
import random
from datetime import datetime

from .config import logger, DB_PATH, DB_TIMEOUT
from . import config
from .constants import ACTIFS_PERMANENTS
from .market_context import get_paris_time

# Raccourci pour accès fréquent - ATTENTION: ne pas réassigner, utiliser config.AB_TESTS_ACTIFS pour les mutations
def _get_ab_tests():
    return config.AB_TESTS_ACTIFS

def init_ab_test_table():
    """Initialise la table pour les tests A/B"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ab_tests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nom TEXT NOT NULL,
                description TEXT,
                variante_a TEXT,
                variante_b TEXT,
                actifs_groupe_a TEXT,
                actifs_groupe_b TEXT,
                date_debut TIMESTAMP,
                date_fin TIMESTAMP,
                statut TEXT DEFAULT 'actif',
                resultats_a TEXT,
                resultats_b TEXT,
                gagnant TEXT,
                conclusion TEXT
            )
        ''')

        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ Erreur init table A/B tests: {e}")

def creer_ab_test(nom, description, variante_a, variante_b, actifs_test=None):
    """
    Crée un nouveau test A/B pour comparer deux stratégies.
    - nom: Nom du test (ex: "RSI_threshold_test")
    - variante_a: Description de la stratégie A (ex: "RSI seuil 30/70")
    - variante_b: Description de la stratégie B (ex: "RSI seuil 25/75")
    - actifs_test: Liste d'actifs à utiliser (par défaut: répartition auto)
    """
    maintenant = get_paris_time()

    try:
        # Répartir les actifs en deux groupes
        if actifs_test is None:
            actifs_test = list(ACTIFS_PERMANENTS.keys())

        import random
        random.shuffle(actifs_test)
        milieu = len(actifs_test) // 2
        groupe_a = actifs_test[:milieu]
        groupe_b = actifs_test[milieu:]

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO ab_tests
            (nom, description, variante_a, variante_b, actifs_groupe_a, actifs_groupe_b, date_debut, statut)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            nom,
            description,
            variante_a,
            variante_b,
            json.dumps(groupe_a),
            json.dumps(groupe_b),
            maintenant,
            'actif'
        ))

        test_id = cursor.lastrowid
        conn.commit()
        conn.close()

        # Activer le test
        config.AB_TESTS_ACTIFS[test_id] = {
            'nom': nom,
            'variante_a': variante_a,
            'variante_b': variante_b,
            'groupe_a': groupe_a,
            'groupe_b': groupe_b
        }

        print(f"[{maintenant.strftime('%H:%M:%S')} CET] 🧪 Test A/B #{test_id} créé: {nom}")
        print(f"   Groupe A ({len(groupe_a)} actifs): {variante_a}")
        print(f"   Groupe B ({len(groupe_b)} actifs): {variante_b}")

        return test_id

    except Exception as e:
        print(f"⚠️ Erreur création A/B test: {e}")
        return None

def get_variante_ab_pour_actif(symbole):
    """
    Retourne la variante A/B active pour un actif donné.
    Retourne: ('A', variante_a) ou ('B', variante_b) ou (None, None)
    """
    for test_id, test in config.AB_TESTS_ACTIFS.items():
        if symbole in test.get('groupe_a', []):
            return 'A', test.get('variante_a', '')
        elif symbole in test.get('groupe_b', []):
            return 'B', test.get('variante_b', '')
    return None, None

def evaluer_ab_test(test_id, jours_minimum=7):
    """
    Évalue les résultats d'un test A/B après une période minimale.
    Compare les performances des deux groupes.

    CORRIGÉ: BREAKEVEN n'est plus compté comme réussite (c'est neutre)
    CORRIGÉ: Logique gagnant améliorée avec score pondéré taux/PnL
    """
    maintenant = get_paris_time()

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Récupérer le test
        cursor.execute('SELECT * FROM ab_tests WHERE id = ?', (test_id,))
        row = cursor.fetchone()

        if not row or row['statut'] != 'actif':
            conn.close()
            return None, "Test non trouvé ou non actif"

        test = dict(row)
        date_debut = test.get('date_debut')
        if isinstance(date_debut, str):
            date_debut = datetime.fromisoformat(date_debut.replace('Z', '+00:00'))

        # Vérifier durée minimale
        if (maintenant - date_debut).days < jours_minimum:
            conn.close()
            return None, f"Test trop récent ({(maintenant - date_debut).days}/{jours_minimum} jours)"

        groupe_a = json.loads(test.get('actifs_groupe_a', '[]'))
        groupe_b = json.loads(test.get('actifs_groupe_b', '[]'))

        # Stats groupe A - CORRIGÉ: BREAKEVEN exclu des réussites (c'est neutre, PnL ≈ 0)
        placeholders_a = ','.join(['?' for _ in groupe_a])
        cursor.execute(f'''
            SELECT
                COUNT(*) as nb_trades,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as echecs,
                SUM(CASE WHEN resultat = 'BREAKEVEN' THEN 1 ELSE 0 END) as breakeven,
                SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as pnl_total,
                AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as pnl_moyen
            FROM trades_recommandes
            WHERE symbole IN ({placeholders_a})
            AND ab_test_id = ?
            AND ab_groupe = 'A'
            AND resultat IS NOT NULL
            AND timestamp_reco >= ?
        ''', (*groupe_a, test_id, date_debut))
        stats_a = cursor.fetchone()

        # Stats groupe B - CORRIGÉ: BREAKEVEN exclu des réussites
        placeholders_b = ','.join(['?' for _ in groupe_b])
        cursor.execute(f'''
            SELECT
                COUNT(*) as nb_trades,
                SUM(CASE WHEN resultat IN ('TP1', 'TP2', 'WIN_FORCE') THEN 1 ELSE 0 END) as reussis,
                SUM(CASE WHEN resultat IN ('STOP', 'LOSS_FORCE') THEN 1 ELSE 0 END) as echecs,
                SUM(CASE WHEN resultat = 'BREAKEVEN' THEN 1 ELSE 0 END) as breakeven,
                SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as pnl_total,
                AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as pnl_moyen
            FROM trades_recommandes
            WHERE symbole IN ({placeholders_b})
            AND ab_test_id = ?
            AND ab_groupe = 'B'
            AND resultat IS NOT NULL
            AND timestamp_reco >= ?
        ''', (*groupe_b, test_id, date_debut))
        stats_b = cursor.fetchone()

        # Calculer les taux (sur trades conclus uniquement, sans breakeven)
        nb_a = stats_a['nb_trades'] or 0
        nb_b = stats_b['nb_trades'] or 0
        reussis_a = stats_a['reussis'] or 0
        reussis_b = stats_b['reussis'] or 0
        echecs_a = stats_a['echecs'] or 0
        echecs_b = stats_b['echecs'] or 0

        # Taux calculé sur trades décisifs (excluant breakeven)
        decisifs_a = reussis_a + echecs_a
        decisifs_b = reussis_b + echecs_b
        taux_a = round((reussis_a / decisifs_a * 100) if decisifs_a > 0 else 0, 1)
        taux_b = round((reussis_b / decisifs_b * 100) if decisifs_b > 0 else 0, 1)
        pnl_a = round(stats_a['pnl_total'] or 0, 2)
        pnl_b = round(stats_b['pnl_total'] or 0, 2)

        # Test de significativité statistique (Chi-squared simplifié)
        # Minimum 10 trades DÉCISIFS par groupe pour significativité
        significatif = False
        p_value_approx = None
        if decisifs_a >= 10 and decisifs_b >= 10:
            total = decisifs_a + decisifs_b
            total_reussis = reussis_a + reussis_b
            total_echecs = echecs_a + echecs_b

            if total_reussis > 0 and total_echecs > 0:
                # Expected values
                exp_reussis_a = decisifs_a * total_reussis / total
                exp_echecs_a = decisifs_a * total_echecs / total
                exp_reussis_b = decisifs_b * total_reussis / total
                exp_echecs_b = decisifs_b * total_echecs / total

                # Chi-squared (avec protection division par zéro)
                chi2 = 0
                for obs, exp in [(reussis_a, exp_reussis_a), (echecs_a, exp_echecs_a),
                                 (reussis_b, exp_reussis_b), (echecs_b, exp_echecs_b)]:
                    if exp > 0:
                        chi2 += ((obs - exp) ** 2) / exp

                # p < 0.05 correspond à chi2 > 3.84 (1 degré de liberté)
                significatif = chi2 > 3.84
                p_value_approx = "< 0.05" if chi2 > 3.84 else ">= 0.05"

        # CORRIGÉ: Logique gagnant améliorée avec score pondéré
        # Score = 60% taux de réussite + 40% PnL normalisé
        def calculer_score(taux, pnl, pnl_max):
            score_taux = taux  # 0-100
            # Normaliser PnL sur une échelle 0-100 (supposant max ±20%)
            pnl_normalise = min(100, max(0, (pnl + 20) * 2.5)) if pnl_max != 0 else 50
            return 0.6 * score_taux + 0.4 * pnl_normalise

        pnl_max = max(abs(pnl_a), abs(pnl_b), 1)  # Éviter division par 0
        score_a = calculer_score(taux_a, pnl_a, pnl_max)
        score_b = calculer_score(taux_b, pnl_b, pnl_max)

        if significatif:
            # Utiliser le score pondéré au lieu de la double condition stricte
            if score_a > score_b + 5:  # Différence significative de 5 points
                gagnant = 'A'
                conclusion = f"Variante A gagne (p {p_value_approx}, score {score_a:.1f} vs {score_b:.1f}): taux {taux_a}% vs {taux_b}%, PnL {pnl_a}% vs {pnl_b}%"
            elif score_b > score_a + 5:
                gagnant = 'B'
                conclusion = f"Variante B gagne (p {p_value_approx}, score {score_b:.1f} vs {score_a:.1f}): taux {taux_b}% vs {taux_a}%, PnL {pnl_b}% vs {pnl_a}%"
            else:
                gagnant = 'EGALITE'
                conclusion = f"Scores très proches (p {p_value_approx}): A={score_a:.1f} vs B={score_b:.1f}, différence < 5 points"
        else:
            min_decisifs = min(decisifs_a, decisifs_b)
            if min_decisifs < 10:
                gagnant = 'INSUFFISANT'
                conclusion = f"Données insuffisantes: A={decisifs_a} trades décisifs, B={decisifs_b} (min 10 requis)"
            else:
                gagnant = 'EGALITE'
                conclusion = f"Pas de différence significative (p {p_value_approx}): A={taux_a}%/{pnl_a}% vs B={taux_b}%/{pnl_b}%"

        # Sauvegarder les résultats
        cursor.execute('''
            UPDATE ab_tests SET
                resultats_a = ?,
                resultats_b = ?,
                gagnant = ?,
                conclusion = ?,
                date_fin = ?,
                statut = 'termine'
            WHERE id = ?
        ''', (
            json.dumps({'nb_trades': stats_a['nb_trades'], 'taux': taux_a, 'pnl': pnl_a}),
            json.dumps({'nb_trades': stats_b['nb_trades'], 'taux': taux_b, 'pnl': pnl_b}),
            gagnant,
            conclusion,
            maintenant,
            test_id
        ))

        conn.commit()
        conn.close()

        # Désactiver le test
        if test_id in config.AB_TESTS_ACTIFS:
            del config.AB_TESTS_ACTIFS[test_id]

        print(f"[{maintenant.strftime('%H:%M:%S')} CET] 🧪 Test A/B #{test_id} terminé: {gagnant}")
        print(f"   {conclusion}")

        return {
            'test_id': test_id,
            'nom': test.get('nom'),
            'gagnant': gagnant,
            'conclusion': conclusion,
            'stats_a': {'nb_trades': stats_a['nb_trades'], 'taux': taux_a, 'pnl': pnl_a},
            'stats_b': {'nb_trades': stats_b['nb_trades'], 'taux': taux_b, 'pnl': pnl_b}
        }, None

    except Exception as e:
        print(f"⚠️ Erreur évaluation A/B test: {e}")
        return None, str(e)

def charger_ab_tests_actifs():
    """Charge les tests A/B actifs au démarrage"""

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM ab_tests WHERE statut = 'actif'")
        tests = cursor.fetchall()
        conn.close()

        for row in tests:
            test = dict(row)
            config.AB_TESTS_ACTIFS[test['id']] = {
                'nom': test['nom'],
                'variante_a': test['variante_a'],
                'variante_b': test['variante_b'],
                'groupe_a': json.loads(test.get('actifs_groupe_a', '[]')),
                'groupe_b': json.loads(test.get('actifs_groupe_b', '[]'))
            }

        if config.AB_TESTS_ACTIFS:
            print(f"🧪 {len(config.AB_TESTS_ACTIFS)} test(s) A/B actif(s) chargé(s)")

    except Exception as e:
        print(f"⚠️ Erreur chargement A/B tests: {e}")

def get_instructions_ab_testing():
    """
    Génère les instructions A/B testing à injecter dans le prompt.
    Indique à Claude quelle variante utiliser pour chaque actif.
    """
    if not config.AB_TESTS_ACTIFS:
        return ""

    instructions = ["\n\n🧪 TESTS A/B EN COURS:"]

    for test_id, test in config.AB_TESTS_ACTIFS.items():
        instructions.append(f"\nTest '{test['nom']}':")
        instructions.append(f"  Groupe A ({test['variante_a']}): {', '.join(test['groupe_a'][:5])}...")
        instructions.append(f"  Groupe B ({test['variante_b']}): {', '.join(test['groupe_b'][:5])}...")

    return "\n".join(instructions)

def evaluer_tous_ab_tests():
    """Évalue tous les tests A/B actifs depuis plus de 7 jours"""
    resultats = []
    for test_id in list(config.AB_TESTS_ACTIFS.keys()):
        result, error = evaluer_ab_test(test_id, jours_minimum=7)
        if result:
            resultats.append(result)
    return resultats


