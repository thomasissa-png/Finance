#!/usr/bin/env python3
"""Migration script: JSON files -> PostgreSQL.

Run once after creating the PostgreSQL database on Replit:

    python -m backend.migrate_json_to_postgres

Reads data from data/*.json and inserts into PostgreSQL tables.
Safe to run multiple times (uses ON CONFLICT DO NOTHING for trades/journal,
and simply appends scan history entries).

Prerequisites:
    - DATABASE_URL environment variable must be set
    - PostgreSQL database must be accessible
"""

import json
import sys
from pathlib import Path

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.database import (
    init_db,
    is_pg_enabled,
    pg_save_trade,
    pg_save_journal_entries,
    pg_save_scan_history_entry,
    pg_save_all_last_scans,
)

DATA_DIR = PROJECT_ROOT / "data"


def migrate():
    if not is_pg_enabled():
        print("ERROR: DATABASE_URL not set.")
        print("Create a PostgreSQL database on Replit first, then re-run this script.")
        sys.exit(1)

    print("Initializing database tables...")
    init_db()
    print()

    # ── Migrate trades ────────────────────────────────────────────
    trades_file = DATA_DIR / "trades.json"
    if trades_file.exists():
        try:
            trades = json.loads(trades_file.read_text())
            print(f"Migrating {len(trades)} trades...")
            migrated = 0
            for trade in trades:
                try:
                    pg_save_trade(trade)
                    migrated += 1
                except Exception as exc:
                    print(f"  WARNING: Failed to migrate trade {trade.get('ticker', '?')} "
                          f"{trade.get('timestamp', '?')}: {exc}")
            print(f"  -> {migrated}/{len(trades)} trades migrated")
        except json.JSONDecodeError as exc:
            print(f"  ERROR: trades.json is corrupt: {exc}")
    else:
        print("No trades.json found (skipping)")

    print()

    # ── Migrate journal ───────────────────────────────────────────
    journal_file = DATA_DIR / "journal.json"
    if journal_file.exists():
        try:
            entries = json.loads(journal_file.read_text())
            print(f"Migrating {len(entries)} journal entries...")
            try:
                pg_save_journal_entries(entries)
                print(f"  -> {len(entries)} journal entries migrated")
            except Exception as exc:
                print(f"  ERROR: Failed to migrate journal entries: {exc}")
        except json.JSONDecodeError as exc:
            print(f"  ERROR: journal.json is corrupt: {exc}")
    else:
        print("No journal.json found (skipping)")

    print()

    # ── Migrate scan history ──────────────────────────────────────
    scan_file = DATA_DIR / "scan_history.json"
    if scan_file.exists():
        try:
            entries = json.loads(scan_file.read_text())
            print(f"Migrating {len(entries)} scan history entries...")
            migrated = 0
            for entry in entries:
                try:
                    pg_save_scan_history_entry(entry)
                    migrated += 1
                except Exception as exc:
                    print(f"  WARNING: Failed to migrate scan history entry: {exc}")
            print(f"  -> {migrated}/{len(entries)} scan history entries migrated")
        except json.JSONDecodeError as exc:
            print(f"  ERROR: scan_history.json is corrupt: {exc}")
    else:
        print("No scan_history.json found (skipping)")

    print()

    # ── Migrate last scans cache ──────────────────────────────────
    scans_file = DATA_DIR / "last_scans.json"
    if scans_file.exists():
        try:
            scans = json.loads(scans_file.read_text())
            if isinstance(scans, dict):
                print(f"Migrating {len(scans)} last scan cache entries...")
                pg_save_all_last_scans(scans)
                print(f"  -> {len(scans)} last scan cache entries migrated")
            else:
                print("  WARNING: last_scans.json has unexpected format (skipping)")
        except json.JSONDecodeError as exc:
            print(f"  ERROR: last_scans.json is corrupt: {exc}")
    else:
        print("No last_scans.json found (skipping)")

    print()
    print("Migration complete!")
    print()
    print("The JSON files in data/ are preserved as backups.")
    print("The app will now use PostgreSQL for all data operations.")
    print("You can verify by checking GET /api/health -> persistence: 'postgresql'")


if __name__ == "__main__":
    migrate()
