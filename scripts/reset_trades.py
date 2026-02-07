#!/usr/bin/env python3
"""
Script pour réinitialiser les trades recommandés.
Utilisé pour repartir propre en début de semaine.

Usage:
    python scripts/reset_trades.py              # Vide les trades + stats
    python scripts/reset_trades.py --all        # Vide TOUT (attention!)
    python scripts/reset_trades.py --keep-journal  # Garde le journal
"""

import sqlite3
import sys
import os
from datetime import datetime

# Chemin vers la base de données
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'trading_app', 'trading.db')

def get_table_counts(cursor):
    """Retourne le nombre d'entrées par table"""
    tables = [
        'trades_recommandes',
        'analyses',
        'journal_quotidien',
        'bilan_quotidien',
        'rapports_hebdo',
        'alertes_news',
        'ajustements_proposes',
        'ab_tests'
    ]
    counts = {}
    for table in tables:
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            counts[table] = cursor.fetchone()[0]
        except sqlite3.OperationalError:
            counts[table] = 0
    return counts

def reset_trades_only():
    """Vide uniquement les trades recommandés et stats quotidiennes"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    print("📊 État actuel de la base:")
    counts_before = get_table_counts(cursor)
    for table, count in counts_before.items():
        print(f"   - {table}: {count} entrées")

    print("\n🗑️  Suppression des trades et stats...")

    cursor.execute("DELETE FROM trades_recommandes")
    nb_trades = cursor.rowcount

    cursor.execute("DELETE FROM stats_quotidiennes")
    nb_stats = cursor.rowcount

    conn.commit()
    conn.close()

    print(f"   ✅ {nb_trades} trades supprimés")
    print(f"   ✅ {nb_stats} stats quotidiennes supprimées")
    print("\n✨ Base prête pour lundi!")

def reset_all():
    """Vide TOUTES les tables (attention!)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    print("⚠️  ATTENTION: Suppression de TOUTES les données!")
    print("📊 État actuel:")
    counts_before = get_table_counts(cursor)
    for table, count in counts_before.items():
        print(f"   - {table}: {count} entrées")

    confirm = input("\n❓ Confirmer la suppression totale? (oui/non): ")
    if confirm.lower() != 'oui':
        print("❌ Annulé.")
        return

    tables_to_clear = [
        'trades_recommandes',
        'analyses',
        'journal_quotidien',
        'bilan_quotidien',
        'rapports_hebdo',
        'alertes_news',
        'stats_quotidiennes',
        'ajustements_proposes',
        'ab_tests',
        'criteres_dynamiques'
    ]

    for table in tables_to_clear:
        try:
            cursor.execute(f"DELETE FROM {table}")
            print(f"   ✅ {table}: {cursor.rowcount} entrées supprimées")
        except sqlite3.OperationalError as e:
            print(f"   ⚠️ {table}: {e}")

    conn.commit()
    conn.close()
    print("\n✨ Base complètement réinitialisée!")

def reset_keep_journal():
    """Vide les trades mais garde le journal et les analyses"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    print("📊 État actuel de la base:")
    counts_before = get_table_counts(cursor)
    for table, count in counts_before.items():
        print(f"   - {table}: {count} entrées")

    print("\n🗑️  Suppression des trades (journal préservé)...")

    cursor.execute("DELETE FROM trades_recommandes")
    nb_trades = cursor.rowcount

    cursor.execute("DELETE FROM stats_quotidiennes")
    nb_stats = cursor.rowcount

    # Optionnel: vider les ajustements expirés
    cursor.execute("DELETE FROM ajustements_proposes WHERE statut = 'expire'")
    nb_expires = cursor.rowcount

    conn.commit()
    conn.close()

    print(f"   ✅ {nb_trades} trades supprimés")
    print(f"   ✅ {nb_stats} stats supprimées")
    print(f"   ✅ {nb_expires} ajustements expirés nettoyés")
    print("\n📓 Journal, analyses et rapports préservés!")
    print("✨ Base prête pour lundi!")

def show_stats():
    """Affiche les statistiques de la base"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    print("📊 Statistiques de la base de données:\n")

    counts = get_table_counts(cursor)
    for table, count in counts.items():
        print(f"   📁 {table}: {count} entrées")

    # Infos supplémentaires
    print("\n📅 Dates:")
    try:
        cursor.execute("SELECT MIN(date), MAX(date) FROM trades_recommandes")
        min_date, max_date = cursor.fetchone()
        print(f"   - Trades: {min_date or 'N/A'} → {max_date or 'N/A'}")
    except:
        pass

    try:
        cursor.execute("SELECT MIN(date), MAX(date) FROM journal_quotidien")
        min_date, max_date = cursor.fetchone()
        print(f"   - Journal: {min_date or 'N/A'} → {max_date or 'N/A'}")
    except:
        pass

    try:
        cursor.execute("SELECT MIN(date), MAX(date) FROM alertes_news")
        min_date, max_date = cursor.fetchone()
        print(f"   - News: {min_date or 'N/A'} → {max_date or 'N/A'}")
    except:
        pass

    conn.close()

if __name__ == "__main__":
    print("=" * 50)
    print("🔧 OUTIL DE RÉINITIALISATION BASE TRADING")
    print("=" * 50)
    print(f"📁 Base: {DB_PATH}")
    print(f"🕐 {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n")

    if not os.path.exists(DB_PATH):
        print(f"❌ Base de données non trouvée: {DB_PATH}")
        sys.exit(1)

    if len(sys.argv) > 1:
        if sys.argv[1] == '--all':
            reset_all()
        elif sys.argv[1] == '--keep-journal':
            reset_keep_journal()
        elif sys.argv[1] == '--stats':
            show_stats()
        else:
            print("Usage:")
            print("  python reset_trades.py              # Vide trades + stats")
            print("  python reset_trades.py --keep-journal  # Garde journal/analyses")
            print("  python reset_trades.py --all        # Vide TOUT")
            print("  python reset_trades.py --stats      # Affiche stats")
    else:
        reset_trades_only()
