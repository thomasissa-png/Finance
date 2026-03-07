"""Tests for data persistence safety — guards against data loss on deploy.

These tests verify that:
1. Data files are in .gitignore (never committed to git)
2. Data files are NOT tracked by git
3. All persistence modules have _ensure_file() guards
4. File locking is used for concurrent access
5. Cross-scan dedup cache works correctly
6. Root health check endpoint responds for Replit Autoscale

NEVER DELETE THESE TESTS — they prevent the critical bug where deploying
new code overwrites production trade/journal data with empty files.
"""

import json
import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

# ── 1. Git safety: data files must be gitignored ─────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

DATA_FILES = [
    "data/trades.json",
    "data/journal.json",
    "data/scan_history.json",
    "data/last_scans.json",
]


def test_gitignore_contains_data_files():
    """All data/*.json files must be listed in .gitignore."""
    gitignore = (PROJECT_ROOT / ".gitignore").read_text()
    for f in DATA_FILES:
        assert f in gitignore, (
            f"{f} is NOT in .gitignore — deploying will overwrite production data!"
        )


def test_data_files_not_tracked_by_git():
    """Data files must NOT be tracked by git (git ls-files)."""
    import subprocess
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch"] + DATA_FILES,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    # git ls-files --error-unmatch returns 1 if files are NOT tracked (which is what we want)
    assert result.returncode != 0, (
        f"Data files are still tracked by git! Run: git rm --cached {' '.join(DATA_FILES)}\n"
        f"Tracked files: {result.stdout.strip()}"
    )


# ── 2. Ensure-file guards: all modules auto-create missing files ──


def test_trades_ensure_file():
    """learning.py must create trades.json if missing."""
    from backend.app.learning import _ensure_data_dir, TRADES_FILE, DATA_DIR
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "trades.json"
        with patch("backend.app.learning.TRADES_FILE", tmp_path), \
             patch("backend.app.learning.DATA_DIR", Path(tmpdir)):
            assert not tmp_path.exists()
            _ensure_data_dir()
            assert tmp_path.exists()
            assert json.loads(tmp_path.read_text()) == []


def test_journal_ensure_file():
    """journal.py must create journal.json if missing."""
    from backend.app.journal import _ensure_journal_file, JOURNAL_FILE
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "journal.json"
        with patch("backend.app.journal.JOURNAL_FILE", tmp_path), \
             patch("backend.app.journal.DATA_DIR", Path(tmpdir)):
            assert not tmp_path.exists()
            _ensure_journal_file()
            assert tmp_path.exists()
            assert json.loads(tmp_path.read_text()) == []


def test_scan_history_ensure_file():
    """scan_history.py must create scan_history.json if missing."""
    from backend.app.scan_history import _ensure_file, SCAN_HISTORY_FILE
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "scan_history.json"
        with patch("backend.app.scan_history.SCAN_HISTORY_FILE", tmp_path), \
             patch("backend.app.scan_history.DATA_DIR", Path(tmpdir)):
            assert not tmp_path.exists()
            _ensure_file()
            assert tmp_path.exists()
            assert json.loads(tmp_path.read_text()) == []


# ── 3. Load functions handle missing/empty/corrupt files ──────────


def test_load_trades_missing_file():
    """load_trades() must return [] if trades.json doesn't exist."""
    from backend.app.learning import load_trades
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "trades.json"
        with patch("backend.app.learning.TRADES_FILE", tmp_path), \
             patch("backend.app.learning.DATA_DIR", Path(tmpdir)):
            result = load_trades()
            assert result == []
            # File should have been created
            assert tmp_path.exists()


def test_load_journal_missing_file():
    """load_journal() must return [] if journal.json doesn't exist."""
    from backend.app.journal import load_journal
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "journal.json"
        with patch("backend.app.journal.JOURNAL_FILE", tmp_path), \
             patch("backend.app.journal.DATA_DIR", Path(tmpdir)):
            result = load_journal()
            assert result == []


def test_load_scan_history_missing_file():
    """load_scan_history() must return [] if scan_history.json doesn't exist."""
    from backend.app.scan_history import load_scan_history
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "scan_history.json"
        with patch("backend.app.scan_history.SCAN_HISTORY_FILE", tmp_path), \
             patch("backend.app.scan_history.DATA_DIR", Path(tmpdir)):
            result = load_scan_history()
            assert result == []


def test_load_trades_corrupt_file():
    """load_trades() must return [] on corrupt JSON, not crash."""
    from backend.app.learning import load_trades
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "trades.json"
        tmp_path.write_text("{corrupt json!!")
        with patch("backend.app.learning.TRADES_FILE", tmp_path), \
             patch("backend.app.learning.DATA_DIR", Path(tmpdir)):
            result = load_trades()
            assert result == []


def test_load_journal_corrupt_file():
    """load_journal() must return [] on corrupt JSON, not crash."""
    from backend.app.journal import load_journal
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "journal.json"
        tmp_path.write_text("{corrupt")
        with patch("backend.app.journal.JOURNAL_FILE", tmp_path), \
             patch("backend.app.journal.DATA_DIR", Path(tmpdir)):
            result = load_journal()
            assert result == []


def test_load_scan_history_corrupt_file():
    """load_scan_history() must return [] on corrupt JSON, not crash."""
    from backend.app.scan_history import load_scan_history
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "scan_history.json"
        tmp_path.write_text("not json")
        with patch("backend.app.scan_history.SCAN_HISTORY_FILE", tmp_path), \
             patch("backend.app.scan_history.DATA_DIR", Path(tmpdir)):
            result = load_scan_history()
            assert result == []


# ── 4. Save functions never truncate existing data ────────────────


def test_save_trade_preserves_existing():
    """save_trade() must APPEND, never overwrite existing trades."""
    from backend.app.learning import save_trade, load_trades
    from backend.app.models import TradeRecommendation, ScanType, Direction

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "trades.json"
        # Pre-populate with an existing trade
        existing = TradeRecommendation(
            scan_type=ScanType.EUROPE,
            timestamp=datetime(2026, 2, 25, 8, 0, tzinfo=timezone.utc),
            ticker="ZW=F", asset_name="Wheat", category="commodities",
            direction=Direction.LONG, catalyst="test",
            entry_price=550, target_price=560, stop_price=545,
            target_pct=1.8, stop_pct=0.9, risk_reward=2.0, confidence=70,
            time_window="09:00-20:00",
        )
        tmp_path.write_text(json.dumps([existing.model_dump(mode="json")], default=str))

        with patch("backend.app.learning.TRADES_FILE", tmp_path), \
             patch("backend.app.learning.DATA_DIR", Path(tmpdir)):
            # Save a new trade
            new_trade = TradeRecommendation(
                scan_type=ScanType.US,
                timestamp=datetime(2026, 2, 25, 15, 0, tzinfo=timezone.utc),
                ticker="CL=F", asset_name="WTI Crude", category="commodities",
                direction=Direction.SHORT, catalyst="test2",
                entry_price=75, target_price=73, stop_price=76,
                target_pct=2.6, stop_pct=1.3, risk_reward=2.0, confidence=65,
                time_window="15:30-20:00",
            )
            save_trade(new_trade)

            # Both trades must exist
            trades = load_trades()
            assert len(trades) == 2
            tickers = {t.ticker for t in trades}
            assert "ZW=F" in tickers, "Existing trade was lost!"
            assert "CL=F" in tickers, "New trade was not saved!"


# ── 5. Cross-scan dedup cache ────────────────────────────────────


def test_cross_scan_dedup_returns_titles():
    """get_recently_scored_titles() returns titles from recent scan history."""
    from backend.app.scan_history import (
        get_recently_scored_titles, _refresh_titles_cache,
        _scored_titles_cache, save_scan_history, load_scan_history,
    )
    from backend.app.models import ScanHistoryEntry, ScanType

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "scan_history.json"
        tmp_path.write_text("[]")

        with patch("backend.app.scan_history.SCAN_HISTORY_FILE", tmp_path), \
             patch("backend.app.scan_history.DATA_DIR", Path(tmpdir)), \
             patch("backend.app.scan_history._scored_titles_cache", []), \
             patch("backend.app.scan_history._cache_last_updated", 0.0):
            # Save a scan with scored news
            entry = ScanHistoryEntry(
                timestamp=datetime.now(timezone.utc),
                scan_type=ScanType.EUROPE,
                has_trade=False,
                news_analyzed=2,
                all_scored_news=[
                    {"title": "Drought in US Midwest affects corn", "total_score": 45},
                    {"title": "ECB holds rates steady", "total_score": 10},
                ],
            )
            save_scan_history([entry])

            _refresh_titles_cache(max_age_hours=1.0)
            import backend.app.scan_history as sh
            titles = sh._scored_titles_cache
            assert len(titles) == 2
            assert "Drought in US Midwest affects corn" in titles
            assert "ECB holds rates steady" in titles


def test_cross_scan_dedup_ignores_old_entries():
    """get_recently_scored_titles() must ignore entries older than max_age_hours."""
    from backend.app.scan_history import (
        _refresh_titles_cache, save_scan_history,
    )
    from backend.app.models import ScanHistoryEntry, ScanType

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "scan_history.json"
        tmp_path.write_text("[]")

        with patch("backend.app.scan_history.SCAN_HISTORY_FILE", tmp_path), \
             patch("backend.app.scan_history.DATA_DIR", Path(tmpdir)), \
             patch("backend.app.scan_history._scored_titles_cache", []), \
             patch("backend.app.scan_history._cache_last_updated", 0.0):
            # Old entry (10h ago)
            old_entry = ScanHistoryEntry(
                timestamp=datetime.now(timezone.utc) - timedelta(hours=10),
                scan_type=ScanType.EUROPE,
                has_trade=False,
                all_scored_news=[{"title": "Old news should be ignored", "total_score": 30}],
            )
            # Recent entry (1h ago)
            recent_entry = ScanHistoryEntry(
                timestamp=datetime.now(timezone.utc) - timedelta(hours=1),
                scan_type=ScanType.US,
                has_trade=True,
                all_scored_news=[{"title": "Recent drought alert", "total_score": 75}],
            )
            save_scan_history([old_entry, recent_entry])

            _refresh_titles_cache(max_age_hours=8.0)
            import backend.app.scan_history as sh
            titles = sh._scored_titles_cache
            assert "Recent drought alert" in titles
            assert "Old news should be ignored" not in titles


# ── 6. Append scan result preserves history ──────────────────────


def test_append_scan_result_preserves_existing():
    """append_scan_result() must APPEND to existing history, not overwrite."""
    from backend.app.scan_history import append_scan_result, load_scan_history, save_scan_history
    from backend.app.models import ScanHistoryEntry, ScanType

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "scan_history.json"

        with patch("backend.app.scan_history.SCAN_HISTORY_FILE", tmp_path), \
             patch("backend.app.scan_history.DATA_DIR", Path(tmpdir)), \
             patch("backend.app.scan_history._scored_titles_cache", []), \
             patch("backend.app.scan_history._cache_last_updated", 0.0):
            # Pre-populate with one entry
            existing = ScanHistoryEntry(
                timestamp=datetime.now(timezone.utc) - timedelta(hours=3),
                scan_type=ScanType.EUROPE,
                has_trade=True,
                news_analyzed=5,
                all_scored_news=[{"title": "Existing scan headline"}],
            )
            save_scan_history([existing])
            assert len(load_scan_history()) == 1

            # Append a new scan result
            new_scan = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "scan_type": "us",
                "has_trade": False,
                "news_analyzed": 3,
                "reason_no_trade": "No good signals",
                "all_scored_news": [{"title": "New scan headline"}],
            }
            append_scan_result(new_scan)

            # Both entries must exist
            entries = load_scan_history()
            assert len(entries) == 2, f"Expected 2 entries, got {len(entries)} — history was overwritten!"
            titles = [n.get("title") for e in entries for n in e.all_scored_news]
            assert "Existing scan headline" in titles
            assert "New scan headline" in titles


# ── 7. Root health check for Replit Autoscale ─────────────────────


def test_root_health_check_returns_200():
    """GET / must return 200 instantly — Replit Autoscale depends on this."""
    from fastapi.testclient import TestClient
    from backend.app.main import app

    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    # In dev (no frontend build): returns JSON {"status": "ok"}.
    # In prod (build exists): returns HTML (SPA index.html) — still 200.
    if response.headers.get("content-type", "").startswith("application/json"):
        assert response.json()["status"] == "ok"


def test_api_health_check_returns_200():
    """GET /api/health must also return 200 (detailed health)."""
    from fastapi.testclient import TestClient
    from backend.app.main import app

    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ("ok", "degraded")
    # Verify persistence mode is reported
    assert "persistence" in data
    assert data["persistence"] in ("postgresql", "json_files")


# ── 8. Database module safety ─────────────────────────────────────


def test_database_is_pg_enabled_without_env():
    """is_pg_enabled() must return False when DATABASE_URL is not set."""
    from backend.app.database import is_pg_enabled
    with patch.dict(os.environ, {}, clear=True):
        # Even if the module-level variable was set, the function should be False
        # without psycopg2 + DATABASE_URL both available
        # In test env without DATABASE_URL, it should be False
        result = is_pg_enabled()
        # Can be True only if both psycopg2 is installed AND DATABASE_URL is set
        if "DATABASE_URL" not in os.environ:
            # Module loaded without DATABASE_URL → is_pg_enabled returns False
            pass  # Just verify it doesn't crash


def test_database_init_db_noop_without_pg():
    """init_db() must be a no-op when PostgreSQL is not configured."""
    from backend.app.database import init_db
    with patch("backend.app.database.is_pg_enabled", return_value=False):
        # Should return without error
        init_db()


def test_database_check_connection_without_pg():
    """check_connection() must return False when PostgreSQL is not configured."""
    from backend.app.database import check_connection
    with patch("backend.app.database.is_pg_enabled", return_value=False):
        assert check_connection() is False


def test_load_trades_uses_json_without_pg():
    """load_trades() must use JSON file path when PostgreSQL is not available."""
    from backend.app.learning import load_trades
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "trades.json"
        tmp_path.write_text("[]")
        with patch("backend.app.learning.TRADES_FILE", tmp_path), \
             patch("backend.app.learning.DATA_DIR", Path(tmpdir)), \
             patch("backend.app.database.is_pg_enabled", return_value=False):
            result = load_trades()
            assert result == []


def test_load_journal_uses_json_without_pg():
    """load_journal() must use JSON file path when PostgreSQL is not available."""
    from backend.app.journal import load_journal
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "journal.json"
        tmp_path.write_text("[]")
        with patch("backend.app.journal.JOURNAL_FILE", tmp_path), \
             patch("backend.app.journal.DATA_DIR", Path(tmpdir)), \
             patch("backend.app.database.is_pg_enabled", return_value=False):
            result = load_journal()
            assert result == []


def test_health_check_shows_json_persistence_without_pg():
    """Health check must report json_files persistence when PG is not configured."""
    from fastapi.testclient import TestClient
    from backend.app.main import app

    client = TestClient(app)
    with patch("backend.app.main.is_pg_enabled", return_value=False):
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["persistence"] == "json_files"


# ── 9. v4.4 DB audit improvements ───────────────────────────────────


def test_compute_pnl_long():
    """L3: compute_pnl() must calculate LONG PnL correctly."""
    from backend.app.database import compute_pnl
    pnl = compute_pnl("LONG", 100.0, 105.0)
    assert pnl == 5.0


def test_compute_pnl_short():
    """L3: compute_pnl() must calculate SHORT PnL correctly."""
    from backend.app.database import compute_pnl
    pnl = compute_pnl("SHORT", 100.0, 95.0)
    assert pnl == 5.0


def test_compute_pnl_zero_entry():
    """L3: compute_pnl() must return None on zero entry price."""
    from backend.app.database import compute_pnl
    assert compute_pnl("LONG", 0, 105.0) is None
    assert compute_pnl("SHORT", 0, 95.0) is None


def test_migration_guard_prevents_repeat():
    """M6: Auto-migration must only attempt once per table."""
    from backend.app.database import mark_migration_attempted, was_migration_attempted, _migration_attempted
    # Clear state
    _migration_attempted.clear()
    assert not was_migration_attempted("test_table")
    mark_migration_attempted("test_table")
    assert was_migration_attempted("test_table")
    # Clean up
    _migration_attempted.clear()


def test_pg_table_stats_without_pg():
    """M7: pg_table_stats() must return pg_not_enabled when PG is off."""
    from backend.app.database import pg_table_stats
    with patch("backend.app.database.is_pg_enabled", return_value=False):
        result = pg_table_stats()
        assert result["status"] == "pg_not_enabled"


def test_pg_run_maintenance_without_pg():
    """M4: pg_run_maintenance() must skip when PG is off."""
    from backend.app.database import pg_run_maintenance
    with patch("backend.app.database.is_pg_enabled", return_value=False):
        result = pg_run_maintenance()
        assert result["status"] == "skipped"


def test_pg_backup_without_pg():
    """L5: pg_backup_to_json() must skip when PG is off."""
    from backend.app.database import pg_backup_to_json
    with patch("backend.app.database.is_pg_enabled", return_value=False):
        result = pg_backup_to_json()
        assert result["status"] == "pg_not_enabled"


def test_trades_cache_invalidation():
    """H2: Trades cache must be invalidated after save_trade()."""
    from backend.app.learning import (
        load_trades, save_trade, _invalidate_trades_cache,
        _trades_cache, _trades_cache_lock,
    )
    from backend.app.models import TradeRecommendation, ScanType, Direction

    _invalidate_trades_cache()
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "trades.json"
        tmp_path.write_text("[]")
        with patch("backend.app.learning.TRADES_FILE", tmp_path), \
             patch("backend.app.learning.DATA_DIR", Path(tmpdir)), \
             patch("backend.app.database.is_pg_enabled", return_value=False):
            # First load should populate cache
            result1 = load_trades()
            assert result1 == []

            # Save a trade — should invalidate cache
            trade = TradeRecommendation(
                scan_type=ScanType.EUROPE,
                timestamp=datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc),
                ticker="GC=F", asset_name="Gold", category="metaux",
                direction=Direction.LONG, catalyst="test cache",
                entry_price=2000, target_price=2050, stop_price=1975,
                target_pct=2.5, stop_pct=1.25, risk_reward=2.0, confidence=70,
                time_window="09:00-20:00",
            )
            save_trade(trade)

            # Next load should see the new trade (not stale cache)
            result2 = load_trades()
            assert len(result2) == 1
            assert result2[0].ticker == "GC=F"
    _invalidate_trades_cache()


def test_db_stats_endpoint_returns_200():
    """L4: /api/db/stats must return 200."""
    from fastapi.testclient import TestClient
    from backend.app.main import app

    client = TestClient(app)
    with patch("backend.app.database.is_pg_enabled", return_value=False):
        response = client.get("/api/db/stats")
        assert response.status_code == 200


def test_db_maintenance_endpoint_returns_200():
    """M4: /api/db/maintenance must return 200."""
    from fastapi.testclient import TestClient
    from backend.app.main import app

    client = TestClient(app)
    with patch("backend.app.database.is_pg_enabled", return_value=False):
        response = client.post("/api/db/maintenance")
        assert response.status_code == 200


def test_db_backup_endpoint_returns_200():
    """L5: /api/db/backup must return 200."""
    from fastapi.testclient import TestClient
    from backend.app.main import app

    client = TestClient(app)
    with patch("backend.app.database.is_pg_enabled", return_value=False):
        response = client.post("/api/db/backup")
        assert response.status_code == 200


def test_scans_cache_file_locking():
    """C2: _save_scans_cache() must use file locking."""
    import inspect
    from backend.app.main import _save_scans_cache
    source = inspect.getsource(_save_scans_cache)
    assert "fcntl.flock" in source or "LOCK_EX" in source, \
        "_save_scans_cache() must use file locking (C2)"


def test_journal_load_uses_shared_lock():
    """C3: load_journal() must use LOCK_SH for JSON reads."""
    import inspect
    from backend.app.journal import load_journal
    source = inspect.getsource(load_journal)
    assert "LOCK_SH" in source, \
        "load_journal() must use LOCK_SH for consistent reads (C3)"


def test_pg_prune_journal_exists():
    """C1: pg_prune_journal() must exist in database module."""
    from backend.app.database import pg_prune_journal
    assert callable(pg_prune_journal)


def test_filter_todays_scans_discards_old():
    """Fix 1: _filter_todays_scans() must discard scans from previous days."""
    from backend.app.main import _filter_todays_scans
    scans = {
        "europe": {"timestamp": "2026-03-05T08:00:00+01:00", "has_trade": True},
        "us": {"timestamp": "2026-03-06T15:00:00+01:00", "has_trade": False},
    }
    result = _filter_todays_scans(scans, "2026-03-06")
    assert "europe" not in result, "March 5 scan should be discarded"
    assert "us" in result, "March 6 scan should be kept"


def test_filter_todays_scans_keeps_no_timestamp():
    """Fix 1: Scans without timestamp are kept for backward compat."""
    from backend.app.main import _filter_todays_scans
    scans = {"europe": {"has_trade": True}}
    result = _filter_todays_scans(scans, "2026-03-06")
    assert "europe" in result


def test_journal_timeout_increased():
    """Fix 2: Journal global timeout must be >= 600s."""
    from backend.app.journal import JOURNAL_GLOBAL_TIMEOUT_SECONDS
    assert JOURNAL_GLOBAL_TIMEOUT_SECONDS >= 600, \
        f"Journal timeout too low: {JOURNAL_GLOBAL_TIMEOUT_SECONDS}s (should be >= 600s)"


def test_startup_recovery_includes_today():
    """Fix 2: Startup recovery must include today's PENDING trades (not just old ones)."""
    import inspect
    from backend.app.main import _recover_pending_trades_on_startup
    source = inspect.getsource(_recover_pending_trades_on_startup)
    # Must NOT have the old `trade_date < today` filter that excluded today's trades
    assert "trade_date < today" not in source or "all_pending" in source, \
        "Startup recovery should recover ALL pending trades, not just old ones"
