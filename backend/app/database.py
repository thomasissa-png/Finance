"""PostgreSQL persistence layer for the news trading system.

When DATABASE_URL is set (Replit provides it automatically after creating a
PostgreSQL database), all data operations use PostgreSQL instead of JSON flat
files. This solves the data loss issue on Replit autoscale deployments.

Falls back to JSON files when DATABASE_URL is not set (local development).

v4.4 DB audit — improvements:
- C1: pg_prune_journal() — prevents unbounded journal table growth
- H1: pg_prune_scan_history() — explicit scan history pruning function
- H3: SELECT FOR UPDATE in pg_update_trade_result() — prevents race conditions
- H4: statement_timeout=30s on connections — prevents query hangs
- H5: PG retry with backoff (3 retries, 1s/2s/4s) — resilience to PG restarts
- H6: pg_load_trades(since=) — date-filtered loading for learning lookback
- M1: Conditional pre-ping (idle > 300s) — eliminates ~200 useless queries/day
- M2: pg_load_pending_trades() — avoids loading full trade history for journal
- M3: Index on journal_entries(result) — faster pending trade queries
- M4: pg_run_maintenance() — periodic VACUUM ANALYZE helper
- M5: Query duration logging via timed get_conn() — detects slow queries
- M6: _migration_attempted guard — prevents repeated auto-migration attempts
- M7: pg_table_stats() — row counts and table sizes for monitoring
- M8: Pool maxconn parameterized via PG_POOL_MAX env var
- L1: pg_load_journal(limit=, offset=) — pagination support
- L2: pg_load_journal_by_date_range() — efficient date range queries
- L3: compute_pnl() — shared PnL calculation (DRY)
- L5: pg_backup_to_json() — PG dump to JSON files for backup

Tables:
- trades: All historical trades (replaces data/trades.json)
- journal_entries: Daily journal (replaces data/journal.json)
- scan_history: Audit trail (replaces data/scan_history.json)
- last_scans: Scan result cache (replaces data/last_scans.json)
"""

import json
import logging
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# Try to import psycopg2 — optional dependency (only needed when DATABASE_URL is set)
try:
    import psycopg2
    import psycopg2.extras
    import psycopg2.pool
    _HAS_PSYCOPG2 = True
except ImportError:
    _HAS_PSYCOPG2 = False

DATABASE_URL = os.environ.get("DATABASE_URL")

# Fix common postgres:// prefix (Replit/Heroku) to postgresql:// (psycopg2 requirement)
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]

_pool = None
_pool_lock = __import__("threading").Lock()  # v8.4 fix P7-E1: thread-safe pool creation

# M1: Track last connection use time for conditional pre-ping
_last_conn_use: float = 0.0
_PRE_PING_IDLE_THRESHOLD = 30.0  # 30s — aggressive pre-ping to catch SSL drops on Replit

# M6: Guard against repeated auto-migration attempts
_migration_attempted: dict[str, bool] = {}

# H4: Statement timeout (seconds) — prevents queries from blocking indefinitely
_STATEMENT_TIMEOUT_S = int(os.environ.get("PG_STATEMENT_TIMEOUT", "30"))

# M8: Pool size configurable via env var
# P1 (v7.8): Increased pool defaults — 21 agents + background jobs need headroom.
# Old: min=2, max=20 caused thundering herd at startup (13+ agents competing for 20 conns).
# New: min=4 (pre-warm more connections), max=30 (enough for 21 agents + scheduler + API).
_PG_POOL_MIN = int(os.environ.get("PG_POOL_MIN", "4"))
_PG_POOL_MAX = int(os.environ.get("PG_POOL_MAX", "30"))

# H5: Retry configuration
_PG_MAX_RETRIES = 3
_PG_RETRY_BACKOFF = [1, 2, 4]  # seconds

# C2 (v7.6): Whitelist of valid table names — prevents SQL injection via f-strings
_VALID_TABLES = frozenset({
    "trades", "journal_entries", "scan_history", "last_scans",
    "agent_messages", "agent_logs", "audit_reports",
    "trend_positions", "trend_journal_entries",
    "tech_positions", "tech_journal_entries",
    "meta_positions", "meta_journal_entries",
    "performance_data", "price_archive", "source_health",
})


def _safe_table(table: str) -> str:
    """Validate table name against whitelist. Raises ValueError if not allowed."""
    if table not in _VALID_TABLES:
        raise ValueError(f"Invalid table name: {table!r}")
    return table


def is_pg_enabled() -> bool:
    """Check if PostgreSQL persistence is available and configured."""
    return _HAS_PSYCOPG2 and DATABASE_URL is not None


def _get_pool():
    """Get or create the thread-safe connection pool (lazy initialization).

    M8: Pool size configurable via PG_POOL_MIN/PG_POOL_MAX env vars.
    H4: Statement timeout applied via DSN options.
    v8.4 fix P7-E1: Double-checked locking to prevent multiple pools at startup
    when 21 agents initialize concurrently.
    """
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is not None:  # double-check after acquiring lock
            return _pool
        if not is_pg_enabled():
            raise RuntimeError("PostgreSQL is not configured (DATABASE_URL not set)")
        # H4: Append statement_timeout to DSN options
        dsn = DATABASE_URL
        options = f"-c statement_timeout={_STATEMENT_TIMEOUT_S * 1000}"
        # TCP keepalive: detect dead connections before they cause errors
        # keepalives_idle=60: start probing after 60s idle
        # keepalives_interval=15: probe every 15s
        # keepalives_count=4: give up after 4 failed probes (total ~2min)
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=_PG_POOL_MIN,
            maxconn=_PG_POOL_MAX,
            dsn=dsn,
            options=options,
            keepalives=1,
            keepalives_idle=60,
            keepalives_interval=15,
            keepalives_count=4,
        )
        logger.info(
            "PostgreSQL connection pool created (min=%d, max=%d, statement_timeout=%ds, keepalive=60s)",
            _PG_POOL_MIN, _PG_POOL_MAX, _STATEMENT_TIMEOUT_S,
        )
    return _pool


def close_pool():
    """Close the connection pool on shutdown to release all connections.

    v8.4 fix P16-E1: Also resets _last_conn_use to prevent stale pre-ping checks
    after pool recreation.
    """
    global _pool, _last_conn_use
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.closeall()
                logger.info("PostgreSQL connection pool closed")
            except Exception as exc:
                logger.warning("Error closing PostgreSQL pool: %s", exc)
            _pool = None
            _last_conn_use = 0.0


def reset_pool():
    """Force-reset the connection pool after a transient failure (e.g. SSL drop).

    v8.5: When all PG connections die simultaneously (Replit SSL reset, network
    hiccup), the pool holds dead connections. This destroys the entire pool so
    the next get_conn() call creates a fresh one with live connections.

    Safe to call from any thread — uses the same lock as pool creation.
    """
    global _pool, _last_conn_use
    with _pool_lock:
        if _pool is not None:
            logger.warning("Force-resetting PG connection pool (all connections presumed dead)")
            try:
                _pool.closeall()
            except Exception:
                pass
            _pool = None
            _last_conn_use = 0.0


@contextmanager
def get_conn():
    """Get a connection from the pool as a context manager.

    M1: Conditional pre-ping — only pings if connection idle > 300s.
    M5: Logs query duration for slow query detection.
    H5: Retries on transient PG errors with exponential backoff.
    P1 (v7.7): Retry on PoolError with backoff — handles startup thundering herd.
    Auto-commits on success, rolls back on exception.
    """
    global _last_conn_use
    pool = _get_pool()

    # P1 (v7.7): Retry getconn() with backoff on pool exhaustion
    # At startup, 13+ agents can simultaneously request connections,
    # exhausting the pool before any connection is returned.
    conn = None
    _POOL_RETRY_BACKOFF = [0.5, 1.0, 2.0, 4.0]
    for attempt in range(len(_POOL_RETRY_BACKOFF) + 1):
        try:
            conn = pool.getconn()
            break
        except Exception as exc:
            exc_name = type(exc).__name__
            is_pool_error = "PoolError" in exc_name or "pool" in str(exc).lower()
            if is_pool_error and attempt < len(_POOL_RETRY_BACKOFF):
                wait = _POOL_RETRY_BACKOFF[attempt]
                logger.warning(
                    "PG pool exhausted (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1, len(_POOL_RETRY_BACKOFF), wait, exc,
                )
                time.sleep(wait)
                continue
            raise  # Non-pool error or all retries exhausted
    if conn is None:
        raise RuntimeError("Failed to acquire PG connection after retries")

    start_time = time.monotonic()
    try:
        # M1: Pre-ping if connection has been idle — detect dead connections
        now = time.monotonic()
        if (now - _last_conn_use) > _PRE_PING_IDLE_THRESHOLD or conn.closed:
            try:
                if conn.closed:
                    raise Exception("connection already closed")
                # v8.4 fix P12-E1: close cursor explicitly to avoid resource leak
                with conn.cursor() as _ping_cur:
                    _ping_cur.execute("SELECT 1")
                conn.rollback()  # Don't leave the pre-ping in a transaction
            except Exception:
                logger.warning("Stale PG connection detected (idle %.0fs), replacing",
                               now - _last_conn_use)
                try:
                    conn.close()
                except Exception:
                    pass
                # Return closed conn to pool (pool needs it back to track count)
                # then get a fresh one. Pool will create new conn since this one is closed.
                pool.putconn(conn, close=True)
                conn = pool.getconn()
        yield conn
        conn.commit()
        _last_conn_use = time.monotonic()
    except Exception as exc:
        # Handle "connection already closed" or SSL dropped — rollback would also fail
        is_conn_dead = conn.closed if hasattr(conn, 'closed') else False
        exc_str = str(exc).lower()
        is_ssl_error = "ssl" in exc_str or "connection" in exc_str and "closed" in exc_str
        try:
            if not is_conn_dead:
                conn.rollback()
        except Exception:
            is_conn_dead = True
            logger.warning("Rollback failed (connection dead): %s", exc)
        # If connection is dead, close it so pool creates a fresh one
        if is_conn_dead or is_ssl_error:
            try:
                conn.close()
            except Exception:
                pass
        raise
    finally:
        elapsed = time.monotonic() - start_time
        # M5: Log slow queries (>2s)
        if elapsed > 2.0:
            logger.warning("Slow PG operation: %.2fs", elapsed)
        try:
            # putconn with close=True if connection is dead — pool replaces it
            if conn.closed:
                pool.putconn(conn, close=True)
            else:
                pool.putconn(conn)
        except Exception:
            pass  # Connection already closed — pool will create a new one


def _pg_retry(func, *args, **kwargs):
    """H5: Retry a PG operation with exponential backoff on transient errors.

    Retries up to _PG_MAX_RETRIES times on OperationalError (connection issues).
    Does NOT retry on ProgrammingError, IntegrityError, etc. (logic bugs).

    v8.4 fix P6-E1: No longer destroys the entire pool on transient errors.
    Previous behavior called _pool.closeall() which killed ALL connections including
    those in use by other threads, causing a thundering herd cascade. Now just retries
    with a fresh connection from the pool (get_conn handles dead connections via
    putconn(close=True)). Only destroys pool as last resort after all retries exhausted.
    """
    last_exc = None
    for attempt in range(_PG_MAX_RETRIES + 1):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            # Only retry on operational/connection errors
            if _HAS_PSYCOPG2 and isinstance(exc, psycopg2.OperationalError):
                last_exc = exc
                if attempt < _PG_MAX_RETRIES:
                    wait = _PG_RETRY_BACKOFF[attempt] if attempt < len(_PG_RETRY_BACKOFF) else 4
                    logger.warning(
                        "PG transient error (attempt %d/%d), retrying in %ds: %s",
                        attempt + 1, _PG_MAX_RETRIES, wait, exc,
                    )
                    time.sleep(wait)
                    # v8.4: Don't destroy entire pool — let get_conn handle bad connections.
                    # Only destroy pool after ALL retries are exhausted (see below).
                    continue
            raise  # Non-transient error — raise immediately
    # All retries exhausted — destroy pool as last resort so next call creates a fresh one
    global _pool
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.closeall()
            except Exception:
                pass
            _pool = None
    raise last_exc


# ── Table Definitions ────────────────────────────────────────────────

_CREATE_TRADES = """
CREATE TABLE IF NOT EXISTS trades (
    id SERIAL PRIMARY KEY,
    schema_version INTEGER DEFAULT 3,
    scan_type VARCHAR(10) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    ticker VARCHAR(30) NOT NULL,
    asset_name VARCHAR(100) NOT NULL,
    category VARCHAR(50) NOT NULL,
    direction VARCHAR(10) NOT NULL,
    news_headline TEXT DEFAULT '',
    news_category VARCHAR(50) DEFAULT 'other',
    catalyst TEXT NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    target_price DOUBLE PRECISION NOT NULL,
    stop_price DOUBLE PRECISION NOT NULL,
    target_pct DOUBLE PRECISION NOT NULL,
    stop_pct DOUBLE PRECISION NOT NULL,
    risk_reward DOUBLE PRECISION NOT NULL,
    confidence INTEGER NOT NULL,
    time_window VARCHAR(50) NOT NULL,
    news_sources JSONB DEFAULT '[]'::jsonb,
    result VARCHAR(10) DEFAULT 'PENDING',
    exit_price DOUBLE PRECISION,
    pnl_pct DOUBLE PRECISION,
    closed_at TIMESTAMPTZ,
    pre_move_pct DOUBLE PRECISION,
    gap_buffer_applied BOOLEAN DEFAULT FALSE,
    binary_event_warning TEXT,
    volume_confirmed BOOLEAN,
    transmission_delay INTEGER,
    market_awareness INTEGER,
    edge_score DOUBLE PRECISION,
    chain_reactions JSONB,
    raw_claude_score DOUBLE PRECISION,
    learning_multiplier DOUBLE PRECISION,
    vix_at_trade DOUBLE PRECISION,
    market_regime VARCHAR(20),
    day_of_week INTEGER,
    volume_ratio DOUBLE PRECISION,
    predicted_transmission_delay INTEGER,
    actual_pricing_time_hours DOUBLE PRECISION,
    delay_accuracy DOUBLE PRECISION,
    surprise INTEGER,
    directional_clarity INTEGER,
    signal_reliability INTEGER,
    expected_magnitude INTEGER,
    position_size_pct DOUBLE PRECISION,
    convergence_count INTEGER,
    convergence_boost DOUBLE PRECISION,
    news_url TEXT DEFAULT '',
    news_description TEXT DEFAULT '',
    news_zone VARCHAR(60) DEFAULT '',
    agent_versions JSONB
)
"""

_CREATE_JOURNAL = """
CREATE TABLE IF NOT EXISTS journal_entries (
    id SERIAL PRIMARY KEY,
    schema_version INTEGER DEFAULT 3,
    date VARCHAR(10) NOT NULL,
    scan_type VARCHAR(10) NOT NULL,
    news_title TEXT NOT NULL,
    news_source TEXT NOT NULL,
    news_category VARCHAR(50) DEFAULT 'other',
    reasoning TEXT NOT NULL,
    score DOUBLE PRECISION NOT NULL,
    ticker VARCHAR(30) NOT NULL,
    asset_name VARCHAR(100) NOT NULL,
    asset_category VARCHAR(50) DEFAULT '',
    direction VARCHAR(10) NOT NULL,
    entry_time TIMESTAMPTZ NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    exit_time TIMESTAMPTZ,
    exit_price DOUBLE PRECISION,
    day_high DOUBLE PRECISION,
    day_low DOUBLE PRECISION,
    result VARCHAR(10) DEFAULT 'PENDING',
    pnl_pct DOUBLE PRECISION,
    review TEXT DEFAULT '',
    binary_event_warning TEXT,
    raw_claude_score DOUBLE PRECISION,
    learning_multiplier DOUBLE PRECISION,
    all_scored_news JSONB,
    rejection_log JSONB,
    decision_summary TEXT,
    learning_state JSONB,
    vix_at_trade DOUBLE PRECISION,
    market_regime VARCHAR(20),
    predicted_transmission_delay INTEGER,
    actual_pricing_time_hours DOUBLE PRECISION,
    delay_accuracy DOUBLE PRECISION,
    slippage DOUBLE PRECISION,
    mae DOUBLE PRECISION,
    mfe DOUBLE PRECISION,
    bar_coverage INTEGER,
    bar_interval VARCHAR(10),
    realized_rr DOUBLE PRECISION,
    news_url TEXT DEFAULT '',
    news_description TEXT DEFAULT '',
    target_price DOUBLE PRECISION,
    stop_price DOUBLE PRECISION,
    target_pct DOUBLE PRECISION,
    stop_pct DOUBLE PRECISION,
    risk_reward DOUBLE PRECISION,
    edge_score DOUBLE PRECISION,
    surprise INTEGER,
    directional_clarity INTEGER,
    market_awareness INTEGER,
    signal_reliability INTEGER,
    expected_magnitude INTEGER,
    volume_confirmed BOOLEAN,
    volume_ratio DOUBLE PRECISION,
    position_size_pct DOUBLE PRECISION,
    news_zone VARCHAR(60) DEFAULT '',
    agent_versions JSONB
)
"""

_CREATE_SCAN_HISTORY = """
CREATE TABLE IF NOT EXISTS scan_history (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    scan_type VARCHAR(10) NOT NULL,
    has_trade BOOLEAN NOT NULL,
    news_analyzed INTEGER DEFAULT 0,
    reason_no_trade TEXT DEFAULT '',
    recommendation JSONB,
    all_scored_news JSONB DEFAULT '[]'::jsonb,
    rejection_log JSONB DEFAULT '[]'::jsonb,
    decision_summary TEXT,
    learning_state JSONB,
    market_context JSONB
)
"""

_CREATE_LAST_SCANS = """
CREATE TABLE IF NOT EXISTS last_scans (
    scan_key VARCHAR(30) PRIMARY KEY,
    data JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
)
"""


# ── v5.1: Price Archive for backtesting ─────────────────────────────

_CREATE_PRICE_ARCHIVE = """
CREATE TABLE IF NOT EXISTS price_archive (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(30) NOT NULL,
    date DATE NOT NULL,
    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    volume DOUBLE PRECISION DEFAULT 0,
    source VARCHAR(20) DEFAULT 'twelve_data',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(ticker, date)
)
"""


def pg_save_price_archive(rows: list[dict]) -> int:
    """v5.1: Bulk insert daily OHLCV data for backtesting.

    rows: list of dicts with keys: ticker, date, open, high, low, close, volume, source.
    Returns number of rows inserted (ignores duplicates).
    """
    if not rows:
        return 0

    def _save():
        inserted = 0
        with get_conn() as conn:
            with conn.cursor() as cur:
                for row in rows:
                    cur.execute("""
                        INSERT INTO price_archive (ticker, date, open, high, low, close, volume, source)
                        VALUES (%(ticker)s, %(date)s, %(open)s, %(high)s, %(low)s, %(close)s, %(volume)s, %(source)s)
                        ON CONFLICT (ticker, date) DO NOTHING
                    """, row)
                    inserted += cur.rowcount
        if inserted > 0:
            logger.info("Price archive: inserted %d/%d rows", inserted, len(rows))
        return inserted
    return _pg_retry(_save)


def pg_load_price_archive(ticker: str, start_date: str | None = None,
                          end_date: str | None = None) -> list[dict]:
    """v5.1: Load archived OHLCV data for a ticker (for backtesting).

    Returns list of dicts with date, open, high, low, close, volume.
    """
    def _load():
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                conditions = ["ticker = %s"]
                params: list = [ticker]
                if start_date:
                    conditions.append("date >= %s")
                    params.append(start_date)
                if end_date:
                    conditions.append("date <= %s")
                    params.append(end_date)
                where = " AND ".join(conditions)
                cur.execute(
                    f"SELECT date, open, high, low, close, volume FROM price_archive WHERE {where} ORDER BY date ASC",
                    params,
                )
                return [dict(row) for row in cur.fetchall()]
    return _pg_retry(_load)


def pg_price_archive_stats() -> dict:
    """v5.1: Get price archive statistics for monitoring."""
    def _stats():
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT COUNT(*) as total_rows FROM price_archive")
                total = cur.fetchone()["total_rows"]
                cur.execute("SELECT COUNT(DISTINCT ticker) as tickers FROM price_archive")
                tickers = cur.fetchone()["tickers"]
                cur.execute("SELECT MIN(date) as oldest, MAX(date) as newest FROM price_archive")
                date_range = cur.fetchone()
                return {
                    "total_rows": total,
                    "tickers": tickers,
                    "oldest": str(date_range["oldest"]) if date_range["oldest"] else None,
                    "newest": str(date_range["newest"]) if date_range["newest"] else None,
                }
    return _pg_retry(_stats)


def init_db() -> None:
    """Create tables and indexes if they don't exist. Called on app startup.

    M3: Added index on journal_entries(result) for faster pending queries.
    v5.1: Added price_archive table for OHLCV backtesting data.
    """
    if not is_pg_enabled():
        logger.info("PostgreSQL not configured - using JSON file persistence")
        return

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_CREATE_TRADES)
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_ticker_ts
                ON trades(ticker, timestamp)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_trades_result
                ON trades(result)
            """)

            cur.execute(_CREATE_JOURNAL)
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_journal_ticker_entry
                ON journal_entries(ticker, entry_time)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_journal_date
                ON journal_entries(date)
            """)
            # M3: Index on result for faster pending trade queries
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_journal_result
                ON journal_entries(result)
            """)

            cur.execute(_CREATE_SCAN_HISTORY)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_scan_history_ts
                ON scan_history(timestamp)
            """)
            # M3: Index on scan_type for filtered queries
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_scan_history_scan_type
                ON scan_history(scan_type)
            """)

            cur.execute(_CREATE_LAST_SCANS)

            # v5.1: Price archive for backtesting
            cur.execute(_CREATE_PRICE_ARCHIVE)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_price_archive_ticker_date
                ON price_archive(ticker, date)
            """)

            # v5.2: Source health monitoring table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS source_health (
                    id SERIAL PRIMARY KEY,
                    date DATE NOT NULL,
                    report JSONB NOT NULL,
                    report_type VARCHAR(10) DEFAULT 'daily',
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(date, report_type)
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_source_health_date
                ON source_health(date)
            """)

            # v6.0: Agent message bus
            cur.execute("""
                CREATE TABLE IF NOT EXISTS agent_messages (
                    id SERIAL PRIMARY KEY,
                    timestamp TIMESTAMPTZ DEFAULT NOW(),
                    from_agent VARCHAR(50) NOT NULL,
                    to_agent VARCHAR(50),
                    msg_type VARCHAR(50) NOT NULL,
                    payload JSONB NOT NULL,
                    consumed BOOLEAN DEFAULT FALSE
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_agent_messages_consume
                ON agent_messages(consumed, msg_type)
                WHERE consumed = FALSE
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_agent_messages_ts
                ON agent_messages(timestamp)
            """)

            # v6.0: Agent structured logs
            cur.execute("""
                CREATE TABLE IF NOT EXISTS agent_logs (
                    id SERIAL PRIMARY KEY,
                    timestamp TIMESTAMPTZ DEFAULT NOW(),
                    agent_name VARCHAR(50) NOT NULL,
                    level VARCHAR(10) DEFAULT 'INFO',
                    action VARCHAR(200) NOT NULL,
                    details JSONB,
                    duration_ms INTEGER
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_agent_logs_agent_ts
                ON agent_logs(agent_name, timestamp)
            """)

            # v6.0: Audit reports
            cur.execute("""
                CREATE TABLE IF NOT EXISTS audit_reports (
                    id SERIAL PRIMARY KEY,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    target_agent VARCHAR(50) NOT NULL,
                    score DOUBLE PRECISION,
                    focus VARCHAR(50),
                    report JSONB NOT NULL
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_reports_agent
                ON audit_reports(target_agent, created_at)
            """)

            # v7.1: Trend positions for Agent Trader 2
            cur.execute("""
                CREATE TABLE IF NOT EXISTS trend_positions (
                    ticker VARCHAR(30) PRIMARY KEY,
                    data JSONB NOT NULL,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # v7.2: Trend journal entries for Agent Journal 2
            cur.execute("""
                CREATE TABLE IF NOT EXISTS trend_journal_entries (
                    id SERIAL PRIMARY KEY,
                    ticker VARCHAR(30) NOT NULL,
                    entry_time VARCHAR(60) NOT NULL,
                    data JSONB NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE (ticker, entry_time)
                )
            """)
            # P1.2: Indexes for faster queries on trend journal
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_trend_journal_ticker
                ON trend_journal_entries(ticker)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_trend_journal_entry_time
                ON trend_journal_entries(entry_time)
            """)

            # v8.0: Performance snapshots & reports persistence
            cur.execute("""
                CREATE TABLE IF NOT EXISTS performance_data (
                    id SERIAL PRIMARY KEY,
                    data_type VARCHAR(20) NOT NULL,
                    data JSONB NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_performance_data_type_ts
                ON performance_data(data_type, created_at)
            """)

            # v6.3: Add missing trade columns for learning feedback (safe for existing DBs)
            for col_name, col_type in [
                ("surprise", "INTEGER"),
                ("directional_clarity", "INTEGER"),
                ("signal_reliability", "INTEGER"),
                ("expected_magnitude", "INTEGER"),
                ("position_size_pct", "DOUBLE PRECISION"),
                ("convergence_count", "INTEGER"),
                ("convergence_boost", "DOUBLE PRECISION"),
                ("news_url", "TEXT DEFAULT ''"),
                ("news_description", "TEXT DEFAULT ''"),
                ("agent_versions", "JSONB"),
                # v7.6: Geographic zone tracking
                ("news_zone", "VARCHAR(60) DEFAULT ''"),
            ]:
                cur.execute(f"""
                    DO $$ BEGIN
                        ALTER TABLE trades ADD COLUMN {col_name} {col_type};
                    EXCEPTION WHEN duplicate_column THEN NULL;
                    END $$;
                """)

            # v5.0 M7: Add v4.1 journal columns if missing (safe for existing DBs)
            for col_name, col_type in [
                ("slippage", "DOUBLE PRECISION"),
                ("mae", "DOUBLE PRECISION"),
                ("mfe", "DOUBLE PRECISION"),
                ("bar_coverage", "INTEGER"),
                ("bar_interval", "VARCHAR(10)"),
                ("realized_rr", "DOUBLE PRECISION"),
                # v5.1: Full news & trade context
                ("news_url", "TEXT DEFAULT ''"),
                ("news_description", "TEXT DEFAULT ''"),
                ("target_price", "DOUBLE PRECISION"),
                ("stop_price", "DOUBLE PRECISION"),
                ("target_pct", "DOUBLE PRECISION"),
                ("stop_pct", "DOUBLE PRECISION"),
                ("risk_reward", "DOUBLE PRECISION"),
                ("edge_score", "DOUBLE PRECISION"),
                ("surprise", "INTEGER"),
                ("directional_clarity", "INTEGER"),
                ("market_awareness", "INTEGER"),
                ("signal_reliability", "INTEGER"),
                ("expected_magnitude", "INTEGER"),
                ("volume_confirmed", "BOOLEAN"),
                ("volume_ratio", "DOUBLE PRECISION"),
                ("position_size_pct", "DOUBLE PRECISION"),
                # P2.5: Agent version tracking
                ("agent_versions", "JSONB"),
                # v7.6: Geographic zone tracking
                ("news_zone", "VARCHAR(60) DEFAULT ''"),
            ]:
                cur.execute(f"""
                    DO $$ BEGIN
                        ALTER TABLE journal_entries ADD COLUMN {col_name} {col_type};
                    EXCEPTION WHEN duplicate_column THEN NULL;
                    END $$;
                """)

            # v8.0: Tech positions for Agent Trader 3 (Équipe 3)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tech_positions (
                    id SERIAL PRIMARY KEY,
                    ticker VARCHAR(30) NOT NULL UNIQUE,
                    strategy VARCHAR(60) NOT NULL DEFAULT '',
                    data JSONB NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_tech_positions_ticker
                ON tech_positions(ticker)
            """)
            # v8.1: Add UNIQUE constraint on ticker if missing (for ON CONFLICT)
            cur.execute("""
                DO $$ BEGIN
                    ALTER TABLE tech_positions ADD CONSTRAINT tech_positions_ticker_key UNIQUE (ticker);
                EXCEPTION WHEN duplicate_table OR duplicate_object THEN NULL;
                END $$
            """)
            # v8.2: Fix NULL strategy rows (migration for tables created before DEFAULT '')
            cur.execute("""
                UPDATE tech_positions SET strategy = '' WHERE strategy IS NULL
            """)

            # v8.0: Tech journal entries for Agent Journal 3 (Équipe 3)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tech_journal_entries (
                    id SERIAL PRIMARY KEY,
                    ticker VARCHAR(30) NOT NULL,
                    strategy VARCHAR(60) NOT NULL,
                    entry_time VARCHAR(60) NOT NULL,
                    data JSONB NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE (ticker, strategy, entry_time)
                )
            """)
            # P1.2: Index for faster queries on tech journal
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_tech_journal_entry_time
                ON tech_journal_entries(entry_time)
            """)

            # v8.0: Meta positions for Agent Trader 4 (Équipe 4)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS meta_positions (
                    id SERIAL PRIMARY KEY,
                    ticker VARCHAR(30) NOT NULL UNIQUE,
                    data JSONB NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_meta_positions_ticker
                ON meta_positions(ticker)
            """)
            # v8.1: Add UNIQUE constraint on ticker if missing (for ON CONFLICT)
            cur.execute("""
                DO $$ BEGIN
                    ALTER TABLE meta_positions ADD CONSTRAINT meta_positions_ticker_key UNIQUE (ticker);
                EXCEPTION WHEN duplicate_table OR duplicate_object THEN NULL;
                END $$
            """)

            # v8.0: Meta journal entries for Agent Journal 4 (Équipe 4)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS meta_journal_entries (
                    id SERIAL PRIMARY KEY,
                    ticker VARCHAR(30) NOT NULL,
                    entry_time VARCHAR(60) NOT NULL,
                    confluence_level INTEGER DEFAULT 0,
                    close_type VARCHAR(30) DEFAULT '',
                    data JSONB NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE (ticker, entry_time)
                )
            """)
            # P1.2 + P2.8: Indexes for faster queries on meta journal
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_meta_journal_entry_time
                ON meta_journal_entries(entry_time)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_meta_journal_confluence
                ON meta_journal_entries(confluence_level)
            """)
            # P2.8: Add close_type column if missing (safe for existing DBs)
            cur.execute("""
                DO $$ BEGIN
                    ALTER TABLE meta_journal_entries ADD COLUMN close_type VARCHAR(30) DEFAULT '';
                EXCEPTION WHEN duplicate_column THEN NULL;
                END $$;
            """)

    logger.info("PostgreSQL tables initialized successfully")


def check_connection() -> bool:
    """Check if the database connection is healthy."""
    if not is_pg_enabled():
        return False
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                return True
    except Exception as exc:
        logger.error("PostgreSQL health check failed: %s", exc)
        return False


# ── L3: Shared PnL Calculation ───────────────────────────────────────


def compute_pnl(direction: str, entry_price: float, exit_price: float) -> float | None:
    """Shared PnL calculation used by both PG and JSON code paths.

    L3: Eliminates duplicated PnL logic between database.py and learning.py.
    Returns PnL percentage rounded to 4 decimals, or None if entry_price is invalid.
    """
    if not entry_price or entry_price == 0:
        return None
    if direction == "LONG":
        return round((exit_price - entry_price) / entry_price * 100, 4)
    else:
        return round((entry_price - exit_price) / entry_price * 100, 4)


# ── Trades CRUD ──────────────────────────────────────────────────────

_TRADE_COLUMNS = [
    "schema_version", "scan_type", "timestamp", "ticker", "asset_name",
    "category", "direction", "news_headline", "news_category", "catalyst",
    "entry_price", "target_price", "stop_price", "target_pct", "stop_pct",
    "risk_reward", "confidence", "time_window", "news_sources", "result",
    "exit_price", "pnl_pct", "closed_at", "pre_move_pct", "gap_buffer_applied",
    "binary_event_warning", "volume_confirmed", "transmission_delay",
    "market_awareness", "edge_score", "chain_reactions", "raw_claude_score",
    "learning_multiplier", "vix_at_trade", "market_regime", "day_of_week",
    "volume_ratio", "predicted_transmission_delay", "actual_pricing_time_hours",
    "delay_accuracy",
    # v6.3: Fields needed by Learning for signal_reliability/magnitude feedback
    "surprise", "directional_clarity", "signal_reliability", "expected_magnitude",
    "position_size_pct", "convergence_count", "convergence_boost",
    "news_url", "news_description",
    # v7.6: Geographic zone tracking
    "news_zone",
    # v8.2: Agent version tracking
    "agent_versions",
]

_TRADE_JSONB_COLS = {"news_sources", "chain_reactions", "agent_versions"}


def _wrap_jsonb(col: str, val, jsonb_cols: set) -> object:
    """Wrap value with Json() adapter if it's a JSONB column."""
    if col in jsonb_cols and val is not None:
        return psycopg2.extras.Json(val)
    return val


def _strip_id(row: dict) -> dict:
    """Remove the 'id' key from a row dict (internal PG serial, not part of model)."""
    return {k: v for k, v in row.items() if k != "id"}


def pg_load_trades(since: datetime | None = None) -> list[dict]:
    """Load trades from PostgreSQL, ordered by timestamp.

    H6: Optional `since` parameter to only load trades after a given date.
    Useful for learning lookback (only needs last 60-90 days).
    """
    def _load():
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if since:
                    cur.execute(
                        "SELECT * FROM trades WHERE timestamp >= %s ORDER BY timestamp ASC",
                        (since,),
                    )
                else:
                    cur.execute("SELECT * FROM trades ORDER BY timestamp ASC")
                return [_strip_id(dict(row)) for row in cur.fetchall()]
    return _pg_retry(_load)


def pg_load_pending_trades() -> list[dict]:
    """M2: Load only PENDING trades — avoids loading full history for journal."""
    def _load():
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM trades WHERE result = 'PENDING' ORDER BY timestamp ASC"
                )
                return [_strip_id(dict(row)) for row in cur.fetchall()]
    return _pg_retry(_load)


def pg_save_trade(trade_dict: dict) -> None:
    """Insert a new trade into PostgreSQL."""
    def _save():
        row = {}
        for col in _TRADE_COLUMNS:
            val = trade_dict.get(col)
            row[col] = _wrap_jsonb(col, val, _TRADE_JSONB_COLS)

        cols = list(row.keys())
        placeholders = [f"%({c})s" for c in cols]
        sql = f"""
            INSERT INTO trades ({', '.join(cols)})
            VALUES ({', '.join(placeholders)})
            ON CONFLICT (ticker, timestamp) DO NOTHING
        """
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, row)
    _pg_retry(_save)


def pg_update_trade_result(
    ticker: str,
    timestamp,
    result_value: str,
    exit_price: float,
    closed_at,
    actual_pricing_time_hours: float | None = None,
    delay_accuracy: float | None = None,
) -> tuple[bool, float | None]:
    """Update a pending trade with its outcome. Calculates PnL internally.

    H3: Uses SELECT ... FOR UPDATE to prevent race conditions.
    L3: Uses shared compute_pnl() function.
    Returns (updated: bool, pnl_pct: float|None).
    """
    def _update():
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # H3: FOR UPDATE locks the row to prevent concurrent updates
                cur.execute("""
                    SELECT direction, entry_price FROM trades
                    WHERE ticker = %s AND timestamp = %s AND result = 'PENDING'
                    FOR UPDATE
                """, (ticker, timestamp))
                row = cur.fetchone()
                if not row:
                    return False, None

                # L3: Shared PnL calculation
                pnl_pct = compute_pnl(row["direction"], row["entry_price"], exit_price)
                if pnl_pct is None:
                    logger.error("entry_price is 0 for %s — cannot compute PnL", ticker)
                    return False, None

                cur.execute("""
                    UPDATE trades SET
                        result = %s,
                        exit_price = %s,
                        pnl_pct = %s,
                        closed_at = %s,
                        actual_pricing_time_hours = %s,
                        delay_accuracy = %s
                    WHERE ticker = %s AND timestamp = %s AND result = 'PENDING'
                """, (
                    result_value, exit_price, pnl_pct, closed_at,
                    actual_pricing_time_hours, delay_accuracy,
                    ticker, timestamp,
                ))
                return True, pnl_pct
    return _pg_retry(_update)


def pg_update_trade_stop(ticker: str, timestamp, new_stop: float) -> bool:
    """Update the stop_price of a PENDING trade (trailing stop persistence).

    Called by position_monitor when trailing stop is tightened.
    Returns True if the trade was found and updated.

    v8.4 fix P11-E1: Added SELECT FOR UPDATE row lock (matching pg_update_trade_result
    pattern) to prevent concurrent modification by journal and position monitor.
    """
    def _update():
        with get_conn() as conn:
            with conn.cursor() as cur:
                # Lock the row first (matches pg_update_trade_result pattern)
                cur.execute("""
                    SELECT id FROM trades
                    WHERE ticker = %s AND timestamp = %s AND result = 'PENDING'
                    FOR UPDATE
                """, (ticker, timestamp))
                row = cur.fetchone()
                if not row:
                    return False
                cur.execute("""
                    UPDATE trades SET stop_price = %s
                    WHERE id = %s
                """, (new_stop, row[0]))
                return True
    return _pg_retry(_update)


# ── Journal CRUD ─────────────────────────────────────────────────────

_JOURNAL_COLUMNS = [
    "schema_version", "date", "scan_type", "news_title", "news_source",
    "news_category", "reasoning", "score", "ticker", "asset_name",
    "asset_category", "direction", "entry_time", "entry_price", "exit_time",
    "exit_price", "day_high", "day_low", "result", "pnl_pct", "review",
    "binary_event_warning", "raw_claude_score", "learning_multiplier",
    "all_scored_news", "rejection_log", "decision_summary", "learning_state",
    "vix_at_trade", "market_regime", "predicted_transmission_delay",
    "actual_pricing_time_hours", "delay_accuracy",
    # v5.0 M7: v4.1 journal enrichment fields
    "slippage", "mae", "mfe", "bar_coverage", "bar_interval", "realized_rr",
    # v5.1: Full news & trade context
    "news_url", "news_description", "target_price", "stop_price",
    "target_pct", "stop_pct", "risk_reward", "edge_score",
    "surprise", "directional_clarity", "market_awareness",
    "signal_reliability", "expected_magnitude",
    "volume_confirmed", "volume_ratio", "position_size_pct",
    # v7.6: Geographic zone tracking
    "news_zone",
]

_JOURNAL_JSONB_COLS = {"all_scored_news", "rejection_log", "learning_state"}


def pg_load_journal(limit: int | None = None, offset: int | None = None) -> list[dict]:
    """Load journal entries from PostgreSQL.

    L1: Supports pagination via limit/offset parameters.
    """
    def _load():
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                sql = "SELECT * FROM journal_entries ORDER BY date ASC, entry_time ASC"
                params: list = []
                if limit is not None:
                    sql += " LIMIT %s"
                    params.append(limit)
                if offset is not None:
                    sql += " OFFSET %s"
                    params.append(offset)
                cur.execute(sql, params if params else None)
                return [_strip_id(dict(row)) for row in cur.fetchall()]
    return _pg_retry(_load)


def pg_load_journal_by_date_range(
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict]:
    """L2: Load journal entries within a date range (YYYY-MM-DD strings).

    More efficient than loading all entries and filtering in Python.
    """
    def _load():
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                conditions = []
                params: list = []
                if start_date:
                    conditions.append("date >= %s")
                    params.append(start_date)
                if end_date:
                    conditions.append("date <= %s")
                    params.append(end_date)
                where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
                cur.execute(
                    f"SELECT * FROM journal_entries{where} ORDER BY date ASC, entry_time ASC",
                    params if params else None,
                )
                return [_strip_id(dict(row)) for row in cur.fetchall()]
    return _pg_retry(_load)


def pg_save_journal_entries(entries: list[dict]) -> None:
    """Insert multiple journal entries into PostgreSQL (dedup via ON CONFLICT)."""
    if not entries:
        return

    def _save():
        with get_conn() as conn:
            with conn.cursor() as cur:
                for entry_dict in entries:
                    row = {}
                    for col in _JOURNAL_COLUMNS:
                        val = entry_dict.get(col)
                        row[col] = _wrap_jsonb(col, val, _JOURNAL_JSONB_COLS)

                    cols = list(row.keys())
                    placeholders = [f"%({c})s" for c in cols]
                    sql = f"""
                        INSERT INTO journal_entries ({', '.join(cols)})
                        VALUES ({', '.join(placeholders)})
                        ON CONFLICT (ticker, entry_time) DO NOTHING
                    """
                    cur.execute(sql, row)
    _pg_retry(_save)


def pg_prune_journal(max_age_days: int = 365) -> int:
    """C1: Delete journal entries older than max_age_days.

    Returns the number of entries pruned.
    """
    def _prune():
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).strftime("%Y-%m-%d")
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM journal_entries WHERE date < %s", (cutoff,))
                pruned = cur.rowcount
                if pruned > 0:
                    logger.info("C1: Pruned %d old journal entries from PG (>%d days)", pruned, max_age_days)
                return pruned
    return _pg_retry(_prune)


# ── Scan History CRUD ────────────────────────────────────────────────

_SCAN_HISTORY_COLUMNS = [
    "timestamp", "scan_type", "has_trade", "news_analyzed", "reason_no_trade",
    "recommendation", "all_scored_news", "rejection_log", "decision_summary",
    "learning_state", "market_context",
]

_SCAN_HISTORY_JSONB_COLS = {
    "recommendation", "all_scored_news", "rejection_log",
    "learning_state", "market_context",
}


def pg_load_scan_history() -> list[dict]:
    """Load all scan history entries from PostgreSQL."""
    def _load():
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM scan_history ORDER BY timestamp ASC")
                return [_strip_id(dict(row)) for row in cur.fetchall()]
    return _pg_retry(_load)


def pg_save_scan_history_entry(entry_dict: dict) -> None:
    """Insert a single scan history entry and prune old ones (>365 days).

    v5.1: Extended retention from 30 to 365 days for backtesting capability.
    """
    def _save():
        row = {}
        for col in _SCAN_HISTORY_COLUMNS:
            val = entry_dict.get(col)
            row[col] = _wrap_jsonb(col, val, _SCAN_HISTORY_JSONB_COLS)

        cols = list(row.keys())
        placeholders = [f"%({c})s" for c in cols]
        sql = f"""
            INSERT INTO scan_history ({', '.join(cols)})
            VALUES ({', '.join(placeholders)})
        """
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, row)
                # Prune entries older than 365 days (backtest retention)
                cutoff = datetime.now(timezone.utc) - timedelta(days=365)
                cur.execute("DELETE FROM scan_history WHERE timestamp < %s", (cutoff,))
                if cur.rowcount > 0:
                    logger.info("Pruned %d old scan history entries (>365 days)", cur.rowcount)
    _pg_retry(_save)


def pg_prune_scan_history(max_age_days: int = 365) -> int:
    """H1: Explicit scan history pruning. v5.1: default 365 days for backtest retention."""
    def _prune():
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM scan_history WHERE timestamp < %s", (cutoff,))
                pruned = cur.rowcount
                if pruned > 0:
                    logger.info("H1: Pruned %d old scan history entries (>%d days)", pruned, max_age_days)
                return pruned
    return _pg_retry(_prune)


def pg_get_recently_scored_titles(max_age_hours: float = 8.0) -> list[str]:
    """Get titles from recently scored news (for cross-scan dedup)."""
    def _get():
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT all_scored_news FROM scan_history
                    WHERE timestamp > %s
                """, (cutoff,))
                titles = []
                for (scored_news,) in cur.fetchall():
                    if scored_news:
                        for news in scored_news:
                            title = news.get("title", "")
                            if title:
                                titles.append(title)
                return titles
    return _pg_retry(_get)


# ── Last Scans Cache CRUD ────────────────────────────────────────────


def pg_load_last_scans() -> dict[str, dict]:
    """Load all cached scan results from PostgreSQL."""
    def _load():
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT scan_key, data FROM last_scans")
                return {row["scan_key"]: row["data"] for row in cur.fetchall()}
    return _pg_retry(_load)


def pg_save_last_scan(scan_key: str, data: dict) -> None:
    """Upsert a single scan result into the cache."""
    def _save():
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO last_scans (scan_key, data, updated_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (scan_key) DO UPDATE SET
                        data = EXCLUDED.data,
                        updated_at = EXCLUDED.updated_at
                """, (scan_key, psycopg2.extras.Json(data), datetime.now(timezone.utc)))
    _pg_retry(_save)


def pg_save_all_last_scans(scans: dict[str, dict]) -> None:
    """Save all scan results to the cache (bulk upsert)."""
    def _save():
        with get_conn() as conn:
            with conn.cursor() as cur:
                now = datetime.now(timezone.utc)
                for scan_key, data in scans.items():
                    cur.execute("""
                        INSERT INTO last_scans (scan_key, data, updated_at)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (scan_key) DO UPDATE SET
                            data = EXCLUDED.data,
                            updated_at = EXCLUDED.updated_at
                    """, (scan_key, psycopg2.extras.Json(data), now))
    _pg_retry(_save)


# ── Agent table pruning ──────────────────────────────────────────────


def pg_prune_agent_messages(max_age_days: int = 30) -> int:
    """Prune old consumed agent messages. Keep unconsumed indefinitely."""
    def _prune():
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM agent_messages WHERE consumed = TRUE AND timestamp < %s",
                    (cutoff,))
                pruned = cur.rowcount
                if pruned > 0:
                    logger.info("Pruned %d old agent messages (>%d days)", pruned, max_age_days)
                return pruned
    if not is_pg_enabled():
        return 0
    return _pg_retry(_prune)


def pg_prune_agent_logs(max_age_days: int = 90) -> int:
    """Prune old agent logs. Keep 90 days for analysis."""
    def _prune():
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM agent_logs WHERE timestamp < %s",
                    (cutoff,))
                pruned = cur.rowcount
                if pruned > 0:
                    logger.info("Pruned %d old agent logs (>%d days)", pruned, max_age_days)
                return pruned
    if not is_pg_enabled():
        return 0
    return _pg_retry(_prune)


def pg_prune_audit_reports(max_reports: int = 100) -> int:
    """Keep only the N most recent audit reports."""
    def _prune():
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM audit_reports")
                count = cur.fetchone()[0]
                if count <= max_reports:
                    return 0
                to_delete = count - max_reports
                cur.execute("""
                    DELETE FROM audit_reports
                    WHERE id IN (
                        SELECT id FROM audit_reports
                        ORDER BY created_at ASC
                        LIMIT %s
                    )
                """, (to_delete,))
                pruned = cur.rowcount
                if pruned > 0:
                    logger.info("Pruned %d old audit reports (keeping %d)", pruned, max_reports)
                return pruned
    if not is_pg_enabled():
        return 0
    return _pg_retry(_prune)


# ── v8.0: Performance data persistence ────────────────────────────────


def pg_save_performance_data(data_type: str, data: dict) -> bool:
    """Save a performance snapshot or report to PG."""
    if not is_pg_enabled():
        return False

    def _save():
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO performance_data (data_type, data) VALUES (%s, %s)",
                    (data_type, json.dumps(data, default=str)),
                )
        return True
    return _pg_retry(_save)


def pg_load_performance_data(data_type: str, limit: int = 168) -> list[dict]:
    """Load recent performance data of given type from PG."""
    if not is_pg_enabled():
        return []

    def _load():
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT data FROM performance_data WHERE data_type = %s ORDER BY created_at DESC LIMIT %s",
                    (data_type, limit),
                )
                rows = cur.fetchall()
                return [r["data"] for r in reversed(rows)]
    return _pg_retry(_load)


def pg_prune_performance_data(max_snapshots: int = 168, max_reports: int = 30) -> int:
    """Prune old performance snapshots and reports."""
    if not is_pg_enabled():
        return 0

    def _prune():
        total = 0
        with get_conn() as conn:
            with conn.cursor() as cur:
                for dtype, max_count in [("snapshot", max_snapshots), ("daily_report", max_reports)]:
                    cur.execute("SELECT COUNT(*) FROM performance_data WHERE data_type = %s", (dtype,))
                    count = cur.fetchone()[0]
                    if count > max_count:
                        to_delete = count - max_count
                        cur.execute("""
                            DELETE FROM performance_data
                            WHERE id IN (
                                SELECT id FROM performance_data
                                WHERE data_type = %s
                                ORDER BY created_at ASC
                                LIMIT %s
                            )
                        """, (dtype, to_delete))
                        total += cur.rowcount
        if total > 0:
            logger.info("Pruned %d old performance records", total)
        return total
    return _pg_retry(_prune)


def pg_prune_price_archive(max_age_days: int = 1095) -> int:
    """P1.4: Prune old price archive entries — keep max 3 years."""
    if not is_pg_enabled():
        return 0

    def _prune():
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM price_archive
                    WHERE date < CURRENT_DATE - INTERVAL '%s days'
                """, (max_age_days,))
                deleted = cur.rowcount
        if deleted > 0:
            logger.info("Pruned %d old price_archive entries (> %d days)", deleted, max_age_days)
        return deleted
    return _pg_retry(_prune)


# ── M4: Maintenance ──────────────────────────────────────────────────


def pg_run_maintenance() -> dict:
    """M4: Run VACUUM ANALYZE on all tables. Call periodically (e.g., daily after journal).

    Returns dict with table names and whether maintenance succeeded.
    """
    if not is_pg_enabled():
        return {"status": "skipped", "reason": "PG not enabled"}

    # Prune agent tables before VACUUM
    try:
        pg_prune_agent_messages(max_age_days=30)
        pg_prune_agent_logs(max_age_days=90)
        pg_prune_audit_reports(max_reports=100)
    except Exception as exc:
        logger.warning("Agent table pruning failed: %s", exc)

    # Prune performance data
    try:
        pg_prune_performance_data(max_snapshots=168, max_reports=30)
    except Exception as exc:
        logger.warning("Performance data pruning failed: %s", exc)

    # P1.4: Prune price_archive — keep max 3 years (1095 days)
    try:
        pg_prune_price_archive(max_age_days=1095)
    except Exception as exc:
        logger.warning("Price archive pruning failed: %s", exc)

    results = {}
    # P1.3: Added price_archive and source_health to VACUUM
    tables = ["trades", "journal_entries", "scan_history", "last_scans",
              "agent_messages", "agent_logs", "audit_reports", "trend_positions",
              "trend_journal_entries", "performance_data",
              "tech_positions", "tech_journal_entries",
              "meta_positions", "meta_journal_entries",
              "price_archive", "source_health"]
    for table in tables:
        try:
            pool = _get_pool()
            conn = pool.getconn()
            try:
                # VACUUM requires autocommit mode
                conn.autocommit = True
                with conn.cursor() as cur:
                    cur.execute(f"VACUUM ANALYZE {_safe_table(table)}")
                results[table] = "ok"
            finally:
                conn.autocommit = False
                pool.putconn(conn)
        except Exception as exc:
            logger.warning("VACUUM ANALYZE %s failed: %s", table, exc)
            results[table] = f"error: {exc}"
    logger.info("M4: Maintenance complete: %s", results)
    return results


def pg_full_reset() -> dict:
    """Reset ALL trading data tables for a fresh start.

    Truncates all 16 tables (trades, journals, positions, logs, messages, etc.).
    Returns dict with per-table results.

    WARNING: This is destructive and irreversible. Only call when you want
    to start with a completely clean database.
    """
    if not is_pg_enabled():
        return {"status": "skipped", "reason": "PG not enabled"}

    tables = [
        "trades", "journal_entries", "scan_history", "last_scans",
        "agent_messages", "agent_logs", "audit_reports",
        "trend_positions", "trend_journal_entries",
        "tech_positions", "tech_journal_entries",
        "meta_positions", "meta_journal_entries",
        "performance_data", "price_archive", "source_health",
    ]
    results = {}
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                for table in tables:
                    try:
                        cur.execute(f"TRUNCATE TABLE {_safe_table(table)} RESTART IDENTITY CASCADE")
                        results[table] = "truncated"
                    except Exception as exc:
                        results[table] = f"error: {exc}"
                        logger.warning("TRUNCATE %s failed: %s", table, exc)
            conn.commit()
    except Exception as exc:
        logger.error("pg_full_reset failed: %s", exc)
        return {"status": "error", "error": str(exc)}

    logger.info("pg_full_reset complete: %s", results)
    return {"status": "ok", "tables": results}


# ── M7: Monitoring ───────────────────────────────────────────────────


def pg_table_stats() -> dict:
    """M7: Get row counts and estimated sizes for all tables.

    Returns dict with table stats for monitoring/alerting.
    """
    if not is_pg_enabled():
        return {"status": "pg_not_enabled"}

    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                stats = {}
                for table in ["trades", "journal_entries", "scan_history", "last_scans",
                              "agent_messages", "agent_logs", "audit_reports",
                              "trend_positions", "trend_journal_entries",
                              "tech_positions", "tech_journal_entries",
                              "meta_positions", "meta_journal_entries",
                              "performance_data", "price_archive", "source_health"]:
                    cur.execute(f"SELECT COUNT(*) as row_count FROM {_safe_table(table)}")
                    row_count = cur.fetchone()["row_count"]

                    cur.execute("""
                        SELECT pg_total_relation_size(%s) as total_bytes
                    """, (table,))
                    total_bytes = cur.fetchone()["total_bytes"]

                    stats[table] = {
                        "rows": row_count,
                        "size_bytes": total_bytes,
                        "size_mb": round(total_bytes / (1024 * 1024), 2),
                    }

                # Oldest and newest trade
                cur.execute("SELECT MIN(timestamp) as oldest, MAX(timestamp) as newest FROM trades")
                trade_range = cur.fetchone()
                stats["trade_range"] = {
                    "oldest": trade_range["oldest"].isoformat() if trade_range["oldest"] else None,
                    "newest": trade_range["newest"].isoformat() if trade_range["newest"] else None,
                }

                # Pending trades count
                cur.execute("SELECT COUNT(*) as cnt FROM trades WHERE result = 'PENDING'")
                stats["pending_trades"] = cur.fetchone()["cnt"]

                return stats
    except Exception as exc:
        logger.error("pg_table_stats failed: %s", exc)
        return {"error": str(exc)}


# ── M6: Migration guard ─────────────────────────────────────────────


def mark_migration_attempted(table: str) -> None:
    """M6: Mark that auto-migration was already attempted for a table."""
    _migration_attempted[table] = True


def was_migration_attempted(table: str) -> bool:
    """M6: Check if auto-migration was already attempted for a table."""
    return _migration_attempted.get(table, False)


# ── L5: Backup ───────────────────────────────────────────────────────


def pg_backup_to_json(output_dir: str | Path | None = None) -> dict:
    """L5: Dump all PG tables to JSON files for backup.

    Creates timestamped JSON files in output_dir (defaults to data/).
    Returns dict with file paths and row counts.
    """
    if not is_pg_enabled():
        return {"status": "pg_not_enabled"}

    if output_dir is None:
        output_dir = Path(__file__).resolve().parent.parent.parent / "data" / "backups"
    else:
        output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    results = {}

    tables = {
        "trades": pg_load_trades,
        "journal_entries": pg_load_journal,
        "scan_history": pg_load_scan_history,
        "last_scans": pg_load_last_scans,
    }

    for table_name, load_func in tables.items():
        try:
            data = load_func()
            filename = f"{table_name}_{timestamp}.json"
            filepath = output_dir / filename
            filepath.write_text(json.dumps(data, indent=2, default=str))
            count = len(data) if isinstance(data, list) else len(data.keys()) if isinstance(data, dict) else 0
            results[table_name] = {"file": str(filepath), "rows": count}
            logger.info("L5: Backed up %s: %d entries → %s", table_name, count, filepath)
        except Exception as exc:
            results[table_name] = {"error": str(exc)}
            logger.error("L5: Backup of %s failed: %s", table_name, exc)

    return results
