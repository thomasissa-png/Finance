"""
Gestion de la base de données SQLite: schéma, initialisation, backup.
"""
import os
import sqlite3
import shutil
from datetime import datetime, timedelta

from .config import DB_PATH, DB_TIMEOUT, logger

def init_database():
    """Initialise la base de données SQLite complète"""
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
    cursor = conn.cursor()

    # Table des trades recommandés
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trades_recommandes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date DATE,
            heure_message TEXT,
            actif TEXT,
            symbole TEXT,
            type_setup TEXT,
            prix_entree REAL,
            prix_stop REAL,
            prix_tp1 REAL,
            prix_tp2 REAL,
            prix_actuel REAL,
            catalyseur TEXT,
            duree_estimee TEXT,
            timestamp_reco DATETIME,
            resultat TEXT,
            prix_sortie REAL,
            pnl_pct REAL,
            timestamp_sortie DATETIME,
            notes TEXT
        )
    ''')

    # Table des analyses (pré-market, intraday, clôture)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date DATE,
            type_analyse TEXT,
            heure TEXT,
            contenu TEXT,
            timestamp DATETIME,
            contexte_marche TEXT
        )
    ''')

    # Table du journal des actifs (mémoire)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS journal_actifs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date DATE,
            symbole TEXT,
            nom_actif TEXT,
            type_info TEXT,
            titre TEXT,
            contenu TEXT,
            impact_cours TEXT,
            importance INTEGER,
            timestamp DATETIME
        )
    ''')

    # Table des alertes news
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS alertes_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date DATE,
            heure TEXT,
            titre TEXT,
            contenu TEXT,
            actif TEXT,
            symbole TEXT,
            impact TEXT,
            timestamp DATETIME
        )
    ''')

    # Table des actifs suivis personnalisés
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS actifs_suivis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbole TEXT UNIQUE,
            nom TEXT,
            categorie TEXT,
            notes TEXT,
            date_ajout DATETIME,
            actif BOOLEAN DEFAULT 1
        )
    ''')

    # Table des statistiques quotidiennes
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stats_quotidiennes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date DATE UNIQUE,
            nb_trades INTEGER,
            nb_reussis INTEGER,
            nb_stops INTEGER,
            nb_non_conclus INTEGER,
            pnl_total REAL,
            meilleur_trade TEXT,
            pire_trade TEXT,
            notes TEXT
        )
    ''')

    # Table du journal quotidien automatique
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS journal_quotidien (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date DATE,
            symbole TEXT,
            nom_actif TEXT,
            categorie TEXT,
            prix_ouverture REAL,
            prix_cloture REAL,
            prix_max REAL,
            prix_min REAL,
            variation_jour REAL,
            volume_relatif REAL,
            commentaire_ia TEXT,
            evenements_jour TEXT,
            opportunites_jour TEXT,
            recommandations_analystes TEXT,
            timestamp DATETIME,
            UNIQUE(date, symbole)
        )
    ''')

    # Table pour le bilan général quotidien
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bilan_quotidien (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date DATE UNIQUE,
            contenu TEXT,
            points_positifs TEXT,
            points_negatifs TEXT,
            lecons_apprises TEXT,
            faits_marquants TEXT,
            timestamp DATETIME
        )
    ''')

    # Table pour les rapports hebdomadaires
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS rapports_hebdo (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            semaine TEXT UNIQUE,
            date_debut DATE,
            date_fin DATE,
            resume_executif TEXT,
            chiffres_cles TEXT,
            forces TEXT,
            faiblesses TEXT,
            patterns TEXT,
            ajustements TEXT,
            scores_confiance TEXT,
            focus_semaine TEXT,
            timestamp DATETIME
        )
    ''')

    # Table pour les critères dynamiques
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS criteres_dynamiques (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_maj DATE,
            categorie TEXT,
            critere TEXT,
            valeur_actuelle REAL,
            valeur_precedente REAL,
            raison TEXT,
            timestamp DATETIME
        )
    ''')

    # Table pour les ajustements en attente de validation
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ajustements_proposes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type_ajustement TEXT,
            categorie TEXT,
            critere TEXT,
            action TEXT,
            valeur_proposee TEXT,
            raison TEXT,
            source TEXT,
            statut TEXT DEFAULT 'en_attente',
            date_proposition DATETIME,
            date_decision DATETIME,
            decideur TEXT
        )
    ''')

    # Colonnes feedback loop pour ajustements_proposes
    colonnes_feedback = [
        ("perf_avant_nb_trades", "INTEGER"),
        ("perf_avant_taux_reussite", "REAL"),
        ("perf_avant_pnl_total", "REAL"),
        ("perf_apres_nb_trades", "INTEGER"),
        ("perf_apres_taux_reussite", "REAL"),
        ("perf_apres_pnl_total", "REAL"),
        ("feedback_date", "DATETIME"),
        ("feedback_conclusion", "TEXT"),
        ("commentaire_utilisateur", "TEXT")  # Commentaire libre de l'utilisateur
    ]
    for col_nom, col_type in colonnes_feedback:
        try:
            cursor.execute(f"ALTER TABLE ajustements_proposes ADD COLUMN {col_nom} {col_type}")
        except sqlite3.OperationalError:
            pass

    # Ajouter colonnes à trades_recommandes si manquantes (ignore si déjà existantes)
    colonnes_trades = [
        ("direction", "TEXT DEFAULT 'LONG'"),
        ("categorie_actif", "TEXT"),
        ("duree_minutes", "INTEGER"),
        ("heure_entree", "INTEGER"),
        # Colonnes pour suivi temps réel
        ("prix_max_atteint", "REAL"),       # Plus haut atteint pendant le trade
        ("prix_min_atteint", "REAL"),       # Plus bas atteint pendant le trade
        ("prix_dernier_check", "REAL"),     # Dernier prix vérifié
        ("timestamp_dernier_check", "DATETIME"),  # Quand
        ("pnl_max", "REAL"),                # PnL max atteint (%)
        ("pnl_min", "REAL"),                # PnL min atteint (drawdown %)
        ("nb_checks", "INTEGER DEFAULT 0"), # Nombre de vérifications
        # Indicateurs techniques au moment de la recommandation (pour analyse performance)
        ("rsi_reco", "REAL"),               # RSI au moment de la reco (0-100)
        ("rsi_signal_reco", "TEXT"),        # SURVENTE/NEUTRE/SURACHAT
        ("macd_signal_reco", "TEXT"),       # BULLISH/BEARISH/BULLISH_CROSS/BEARISH_CROSS
        ("atr_pct_reco", "REAL"),           # ATR% au moment de la reco
        ("volume_relatif_reco", "REAL"),    # Volume relatif au moment de la reco
        # Niveaux techniques au moment de la recommandation
        ("pivot_reco", "REAL"),             # Pivot Point
        ("support1_reco", "REAL"),          # Support 1
        ("resistance1_reco", "REAL"),       # Resistance 1
        # Ratio Risk/Reward
        ("ratio_rr", "TEXT"),               # Ex: "1:1.5", "1:2", "1:3"
        ("ratio_rr_justification", "TEXT"), # Justification du ratio choisi
        # A/B Testing - traçabilité des tests
        ("ab_test_id", "INTEGER"),          # ID du test A/B (FK vers ab_tests)
        ("ab_groupe", "TEXT"),              # 'A' ou 'B'
        ("ab_variante", "TEXT"),            # Description de la variante testée
        # === NOUVELLES COLONNES A/B TESTING AVANCÉ ===
        # 1. Timing Entry/Exit
        ("strategie_entree", "TEXT"),       # IMMEDIATE / PULLBACK / BREAKOUT / LIMIT
        ("trailing_stop", "INTEGER DEFAULT 0"),  # 0=non, 1=oui
        ("trailing_stop_pct", "REAL"),      # % de trailing (ex: 0.3 = 0.3%)
        ("prix_limite_entree", "REAL"),     # Prix limite si strategie=LIMIT
        # 2. Contexte Marché Dynamique
        ("vix_niveau", "REAL"),             # VIX au moment de la reco
        ("regime_marche", "TEXT"),          # CALME (<15) / NORMAL (15-20) / VOLATILE (20-30) / EXTREME (>30)
        ("regles_adaptees", "TEXT"),        # Description des règles adaptées au régime
        # 3. Time Decay / Urgence
        ("validite_minutes", "INTEGER"),    # Fenêtre de validité en minutes
        ("heure_expiration", "TEXT"),       # Heure d'expiration CET (ex: "10:30")
        ("urgence", "TEXT"),                # HAUTE / MOYENNE / BASSE
        ("est_expire", "INTEGER DEFAULT 0"), # 0=valide, 1=expiré sans entrée
        # 4. Multi Timeframe
        ("trend_daily", "TEXT"),            # UP / DOWN / RANGE
        ("trend_h4", "TEXT"),               # UP / DOWN / RANGE
        ("trend_h1", "TEXT"),               # UP / DOWN / RANGE (timeframe principal)
        ("alignement_tf", "INTEGER"),       # 0-3 (nb de TF alignés)
        ("confluence_score", "INTEGER"),    # 0-10 score de confluence technique
        # 5. Analyse Sentiment
        ("sentiment_score", "REAL"),        # -5 (très bearish) à +5 (très bullish)
        ("sentiment_source", "TEXT"),       # NEWS / TECHNIQUE / FLOW / MIXTE
        ("sentiment_detail", "TEXT"),       # Explication du sentiment
        # 6. Suivi Intraday Actif
        ("statut_intraday", "TEXT DEFAULT 'EN_COURS'"),  # EN_COURS / TP50_ATTEINT / STOP_AJUSTE / CLOTURE
        ("stop_ajuste", "REAL"),            # Nouveau stop après ajustement (breakeven, trailing)
        ("reevaluations", "TEXT"),          # JSON des réévaluations intraday
        ("nb_reevaluations", "INTEGER DEFAULT 0"),  # Nombre de réévaluations
        ("action_recommandee", "TEXT"),     # HOLD / RENFORCER / REDUIRE / SORTIR
        # 7. Saisonnalité
        ("jour_semaine", "TEXT"),           # LUNDI / MARDI / MERCREDI / JEUDI / VENDREDI
        ("session_marche", "TEXT"),         # EU_OPEN / US_PREMARKET / US_OPEN / EU_CLOSE / US_CLOSE
        ("pattern_jour", "TEXT"),           # Pattern historique du jour (ex: "Lundi souvent haussier")
        # Score de conviction global
        ("conviction_score", "INTEGER"),    # 1-5 (5 = très haute conviction)
        # 8. Trade Quality Grading
        ("trade_grade", "TEXT"),            # A/B/C/D - Note qualité du setup
        ("grade_details", "TEXT"),          # JSON détail des critères de notation
        ("grade_entry_score", "INTEGER"),   # Score qualité entrée (0-100)
        ("grade_exit_score", "INTEGER"),    # Score qualité sortie (0-100, calculé après clôture)
        ("grade_setup_score", "INTEGER")    # Score qualité setup (0-100)
    ]
    for col_nom, col_type in colonnes_trades:
        try:
            cursor.execute(f"ALTER TABLE trades_recommandes ADD COLUMN {col_nom} {col_type}")
        except sqlite3.OperationalError:
            pass  # Colonne existe déjà

    # Ajouter colonnes au journal_quotidien si manquantes
    colonnes_journal = [
        ("prix_max", "REAL"),
        ("prix_min", "REAL"),
        ("recommandations_analystes", "TEXT")
    ]
    for col_nom, col_type in colonnes_journal:
        try:
            cursor.execute(f"ALTER TABLE journal_quotidien ADD COLUMN {col_nom} {col_type}")
        except sqlite3.OperationalError:
            pass  # Colonne existe déjà

    try:
        cursor.execute("ALTER TABLE bilan_quotidien ADD COLUMN faits_marquants TEXT")
    except sqlite3.OperationalError:
        pass  # Colonne existe déjà

    # Ajouter colonne audit trail pour lier critères à leur source
    try:
        cursor.execute("ALTER TABLE criteres_dynamiques ADD COLUMN ajustement_source_id INTEGER")
    except sqlite3.OperationalError:
        pass  # Colonne existe déjà

    # Activer WAL mode pour meilleures performances en concurrence
    cursor.execute("PRAGMA journal_mode = WAL")
    cursor.execute("PRAGMA synchronous = NORMAL")

    conn.commit()
    conn.close()
    print("✅ Base de données initialisée")

    # Créer les index pour optimiser les requêtes fréquentes
    create_database_indexes()

def create_database_indexes():
    """Crée les index SQL pour optimiser les performances des requêtes fréquentes"""
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
    cursor = conn.cursor()

    indexes = [
        # Index trades_recommandes - colonnes les plus utilisées
        ("idx_trades_date", "trades_recommandes", "date"),
        ("idx_trades_symbole", "trades_recommandes", "symbole"),
        ("idx_trades_resultat", "trades_recommandes", "resultat"),
        ("idx_trades_date_resultat", "trades_recommandes", "date, resultat"),
        ("idx_trades_date_symbole", "trades_recommandes", "date, symbole"),
        ("idx_trades_conviction", "trades_recommandes", "conviction_score"),
        ("idx_trades_regime", "trades_recommandes", "regime_marche"),
        ("idx_trades_strategie", "trades_recommandes", "strategie_entree"),
        ("idx_trades_statut", "trades_recommandes", "statut_intraday"),
        ("idx_trades_ab_test", "trades_recommandes", "ab_test_id"),
        # Index analyses
        ("idx_analyses_date", "analyses", "date"),
        ("idx_analyses_type_analyse", "analyses", "type_analyse"),
        # Index journal_quotidien
        ("idx_journal_date", "journal_quotidien", "date"),
        ("idx_journal_symbole", "journal_quotidien", "symbole"),
        # Index bilan_quotidien
        ("idx_bilan_date", "bilan_quotidien", "date"),
        # Index alertes_news
        ("idx_news_date", "alertes_news", "date"),
        # Index criteres_dynamiques
        ("idx_criteres_date", "criteres_dynamiques", "date_maj"),
        ("idx_criteres_source", "criteres_dynamiques", "ajustement_source_id"),
        # Index ajustements_proposes
        ("idx_ajustements_statut", "ajustements_proposes", "statut"),
        ("idx_ajustements_date", "ajustements_proposes", "date_proposition"),
        # Index ab_tests
        ("idx_ab_tests_statut", "ab_tests", "statut"),
    ]

    for idx_name, table, columns in indexes:
        try:
            cursor.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({columns})")
        except sqlite3.OperationalError as e:
            pass  # Index existe déjà ou table n'existe pas encore

    conn.commit()
    conn.close()
    print("✅ Index SQL créés/vérifiés")

def backup_database():
    """Crée une sauvegarde horodatée de la base de données"""
    import shutil

    if not os.path.exists(DB_PATH):
        print("⚠️ Base de données non trouvée, backup ignoré")
        return None

    # Créer le dossier backups si nécessaire
    backup_dir = os.path.join(os.path.dirname(DB_PATH) or '.', 'backups')
    os.makedirs(backup_dir, exist_ok=True)

    # Nom du backup avec date
    maintenant = datetime.now()
    backup_name = f"trading_backup_{maintenant.strftime('%Y%m%d_%H%M%S')}.db"
    backup_path = os.path.join(backup_dir, backup_name)

    try:
        # Copie sécurisée (avec flush SQLite)
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # Flush WAL si utilisé
        conn.close()

        shutil.copy2(DB_PATH, backup_path)

        # Nettoyer les anciens backups (garder les 7 derniers)
        cleanup_old_backups(backup_dir, keep=7)

        print(f"✅ Backup créé: {backup_name}")
        return backup_path
    except Exception as e:
        logger.error(f"Erreur backup: {e}")
        return None

def cleanup_old_backups(backup_dir, keep=7):
    """Supprime les backups anciens, garde les N plus récents"""
    try:
        backups = sorted([
            os.path.join(backup_dir, f)
            for f in os.listdir(backup_dir)
            if f.startswith('trading_backup_') and f.endswith('.db')
        ], key=os.path.getmtime, reverse=True)

        # Supprimer les plus vieux
        for old_backup in backups[keep:]:
            os.remove(old_backup)
            print(f"🗑️ Ancien backup supprimé: {os.path.basename(old_backup)}")
    except Exception as e:
        print(f"⚠️ Erreur nettoyage backups: {e}")


