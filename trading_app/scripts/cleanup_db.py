#!/usr/bin/env python3
"""
Script de nettoyage de la base de données Trading
À exécuter sur Replit pour repartir à zéro

Usage:
    python scripts/cleanup_db.py              # Mode interactif
    python scripts/cleanup_db.py --confirm    # Exécuter directement
    python scripts/cleanup_db.py --trades     # Nettoyer uniquement les trades
    python scripts/cleanup_db.py --all        # Tout nettoyer (y compris journal)
"""

import sqlite3
import os
import sys
from datetime import datetime

# Chemin de la base de données
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'trading.db')

# Liste blanche des tables autorisées (protection contre injection SQL)
TABLES_VALIDES = {
    'trades_recommandes',
    'analyses',
    'journal_quotidien',
    'bilan_quotidien',
    'alertes_news',
    'rapports_hebdo',
    'criteres_dynamiques',
    'ajustements_proposes',
    'ab_tests'
}

def _validate_table_name(table):
    """Valide qu'un nom de table est dans la liste blanche"""
    if table not in TABLES_VALIDES:
        raise ValueError(f"Table non autorisée: {table}")
    return table

def get_db_stats():
    """Affiche les statistiques actuelles de la base"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    stats = {}
    tables = [
        'trades_recommandes',
        'analyses',
        'journal_quotidien',
        'bilan_quotidien',
        'alertes_news',
        'rapports_hebdo',
        'criteres_dynamiques'
    ]

    for table in tables:
        try:
            _validate_table_name(table)
            # Requête sécurisée - table validée contre la liste blanche
            cursor.execute(f'SELECT COUNT(*) FROM {table}')
            stats[table] = cursor.fetchone()[0]
        except (sqlite3.OperationalError, ValueError):
            stats[table] = 0

    conn.close()
    return stats

def cleanup_trades():
    """Nettoie uniquement les trades recommandés"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('DELETE FROM trades_recommandes')
    deleted = cursor.rowcount

    conn.commit()
    conn.close()

    return deleted

def cleanup_analyses():
    """Nettoie les analyses"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('DELETE FROM analyses')
    deleted = cursor.rowcount

    conn.commit()
    conn.close()

    return deleted

def cleanup_all():
    """Nettoie toutes les tables principales"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    tables_to_clean = [
        'trades_recommandes',
        'analyses',
        'alertes_news',
        'bilan_quotidien',
        'journal_quotidien',
        'rapports_hebdo',
        'criteres_dynamiques'
    ]

    results = {}
    for table in tables_to_clean:
        try:
            _validate_table_name(table)
            # Requête sécurisée - table validée contre la liste blanche
            cursor.execute(f'DELETE FROM {table}')
            results[table] = cursor.rowcount
        except (sqlite3.OperationalError, ValueError):
            results[table] = 0

    # Réinitialiser les auto-increment
    cursor.execute("DELETE FROM sqlite_sequence WHERE name IN (?, ?, ?, ?, ?, ?, ?)",
                   tuple(tables_to_clean))

    conn.commit()
    conn.close()

    return results

def main():
    print("=" * 50)
    print("🧹 NETTOYAGE BASE DE DONNÉES TRADING")
    print("=" * 50)
    print()

    # Vérifier que la base existe
    if not os.path.exists(DB_PATH):
        print(f"❌ Base de données non trouvée: {DB_PATH}")
        sys.exit(1)

    # Afficher les stats actuelles
    print("📊 État actuel de la base de données:")
    print("-" * 40)
    stats = get_db_stats()
    for table, count in stats.items():
        print(f"  {table}: {count} entrées")
    print()

    # Vérifier les arguments
    if len(sys.argv) > 1:
        if '--confirm' in sys.argv and '--all' in sys.argv:
            results = cleanup_all()
            print("✅ Nettoyage complet effectué:")
            for table, count in results.items():
                print(f"  {table}: {count} entrées supprimées")
        elif '--confirm' in sys.argv and '--trades' in sys.argv:
            deleted = cleanup_trades()
            print(f"✅ Trades nettoyés: {deleted} entrées supprimées")
        elif '--confirm' in sys.argv:
            # Par défaut: nettoyer trades et analyses
            deleted_trades = cleanup_trades()
            deleted_analyses = cleanup_analyses()
            print(f"✅ Trades nettoyés: {deleted_trades} entrées supprimées")
            print(f"✅ Analyses nettoyées: {deleted_analyses} entrées supprimées")
        elif '--help' in sys.argv or '-h' in sys.argv:
            print(__doc__)
        else:
            print("Options disponibles:")
            print("  --confirm        Exécuter le nettoyage (trades + analyses)")
            print("  --confirm --trades   Nettoyer uniquement les trades")
            print("  --confirm --all      Tout nettoyer")
            print("  --help           Afficher l'aide")
    else:
        # Mode interactif
        print("Que souhaitez-vous nettoyer?")
        print("  1. Trades et analyses (recommandé pour fresh start)")
        print("  2. Uniquement les trades")
        print("  3. Tout (incluant journal, rapports)")
        print("  0. Annuler")
        print()

        choice = input("Votre choix [0-3]: ").strip()

        if choice == '1':
            confirm = input("⚠️  Confirmer la suppression des trades et analyses? [o/N]: ")
            if confirm.lower() in ['o', 'oui', 'y', 'yes']:
                deleted_trades = cleanup_trades()
                deleted_analyses = cleanup_analyses()
                print(f"✅ Trades: {deleted_trades} supprimés")
                print(f"✅ Analyses: {deleted_analyses} supprimées")
            else:
                print("❌ Annulé")
        elif choice == '2':
            confirm = input("⚠️  Confirmer la suppression des trades? [o/N]: ")
            if confirm.lower() in ['o', 'oui', 'y', 'yes']:
                deleted = cleanup_trades()
                print(f"✅ Trades: {deleted} supprimés")
            else:
                print("❌ Annulé")
        elif choice == '3':
            confirm = input("⚠️  ATTENTION: Tout sera supprimé! Confirmer? [o/N]: ")
            if confirm.lower() in ['o', 'oui', 'y', 'yes']:
                results = cleanup_all()
                print("✅ Nettoyage complet:")
                for table, count in results.items():
                    print(f"  {table}: {count} supprimés")
            else:
                print("❌ Annulé")
        else:
            print("❌ Annulé")

    print()
    print("📊 Nouvel état de la base:")
    print("-" * 40)
    stats = get_db_stats()
    for table, count in stats.items():
        print(f"  {table}: {count} entrées")

    print()
    print("✨ Prêt pour une nouvelle semaine de trading!")

if __name__ == '__main__':
    main()
