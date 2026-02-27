"""PostgreSQL persistence layer for the news trading system.

When DATABASE_URL is set (Replit provides it automatically after creating a
PostgreSQL database), all data operations use PostgreSQL instead of JSON flat
files. This solves the data loss issue on Replit autoscale deployments.

Falls back to JSON files when DATABASE_URL is not set (local development).

Tables:
- trades: All historical trades (replaces data/trades.json)
- journal_entries: Daily journal (replaces data/journal.json)
- scan_history: Audit trail (replaces data/scan_history.json)
- last_scans: Scan result cache (replaces data/last_scans.json)
"""

import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

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


def is_pg_enabled() -> bool:
    """Check if PostgreSQL persistence is available and configured."""
    return _HAS_PSYCOPG2 and DATABASE_URL is not None


def _get_pool():
    """Get or create the thread-safe connection pool (lazy initialization)."""
    global _pool
    if _pool is None:
        if not is_pg_enabled():
            raise RuntimeError("PostgreSQL is not configured (DATABASE_URL not set)")
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=10,
            dsn=DATABASE_URL,
        )
        logger.info("PostgreSQL connection pool created (min=2, max=10)")
    return _pool


@contextmanager
def get_conn():
    """Get a connection from the pool as a context manager.

    Auto-commits on success, rolls back on exception.
    """
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


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
    delay_accuracy DOUBLE PRECISION
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
    delay_accuracy DOUBLE PRECISION
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


def init_db() -> None:
    """Create tables and indexes if they don't exist. Called on app startup."""
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

            cur.execute(_CREATE_SCAN_HISTORY)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_scan_history_ts
                ON scan_history(timestamp)
            """)

            cur.execute(_CREATE_LAST_SCANS)

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
]

_TRADE_JSONB_COLS = {"news_sources", "chain_reactions"}


def _wrap_jsonb(col: str, val, jsonb_cols: set) -> object:
    """Wrap value with Json() adapter if it's a JSONB column."""
    if col in jsonb_cols and val is not None:
        return psycopg2.extras.Json(val)
    return val


def pg_load_trades() -> list[dict]:
    """Load all trades from PostgreSQL, ordered by timestamp."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM trades ORDER BY timestamp ASC")
            rows = cur.fetchall()
            return [{k: v for k, v in dict(row).items() if k != "id"} for row in rows]


def pg_save_trade(trade_dict: dict) -> None:
    """Insert a new trade into PostgreSQL."""
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

    Returns (updated: bool, pnl_pct: float|None).
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Read trade info for PnL calculation
            cur.execute("""
                SELECT direction, entry_price FROM trades
                WHERE ticker = %s AND timestamp = %s AND result = 'PENDING'
            """, (ticker, timestamp))
            row = cur.fetchone()
            if not row:
                return False, None

            # Calculate PnL (same logic as existing code)
            if row["direction"] == "LONG":
                pnl_pct = round(
                    (exit_price - row["entry_price"]) / row["entry_price"] * 100, 4
                )
            else:
                pnl_pct = round(
                    (row["entry_price"] - exit_price) / row["entry_price"] * 100, 4
                )

            # Update the trade
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
]

_JOURNAL_JSONB_COLS = {"all_scored_news", "rejection_log", "learning_state"}


def pg_load_journal() -> list[dict]:
    """Load all journal entries from PostgreSQL."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM journal_entries ORDER BY date ASC, entry_time ASC"
            )
            rows = cur.fetchall()
            return [{k: v for k, v in dict(row).items() if k != "id"} for row in rows]


def pg_save_journal_entries(entries: list[dict]) -> None:
    """Insert multiple journal entries into PostgreSQL (dedup via ON CONFLICT)."""
    if not entries:
        return
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
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM scan_history ORDER BY timestamp ASC")
            rows = cur.fetchall()
            return [{k: v for k, v in dict(row).items() if k != "id"} for row in rows]


def pg_save_scan_history_entry(entry_dict: dict) -> None:
    """Insert a single scan history entry and prune old ones (>30 days)."""
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
            # Prune entries older than 30 days
            cutoff = datetime.now(timezone.utc) - timedelta(days=30)
            cur.execute("DELETE FROM scan_history WHERE timestamp < %s", (cutoff,))
            if cur.rowcount > 0:
                logger.info("Pruned %d old scan history entries", cur.rowcount)


def pg_get_recently_scored_titles(max_age_hours: float = 8.0) -> list[str]:
    """Get titles from recently scored news (for cross-scan dedup)."""
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


# ── Last Scans Cache CRUD ────────────────────────────────────────────


def pg_load_last_scans() -> dict[str, dict]:
    """Load all cached scan results from PostgreSQL."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT scan_key, data FROM last_scans")
            return {row["scan_key"]: row["data"] for row in cur.fetchall()}


def pg_save_last_scan(scan_key: str, data: dict) -> None:
    """Upsert a single scan result into the cache."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO last_scans (scan_key, data, updated_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (scan_key) DO UPDATE SET
                    data = EXCLUDED.data,
                    updated_at = EXCLUDED.updated_at
            """, (scan_key, psycopg2.extras.Json(data), datetime.now(timezone.utc)))


def pg_save_all_last_scans(scans: dict[str, dict]) -> None:
    """Save all scan results to the cache (bulk upsert)."""
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
