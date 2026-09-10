"""Database module for caching and historical tracking."""

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

from config import (
    CACHE_SCHEMA_VERSION,
    CACHE_TTL_HOURS,
    DATABASE_PATH,
    NOT_COVERED_CACHE_TTL_HOURS,
)

logger = logging.getLogger(__name__)


@contextmanager
def get_connection():
    """Context manager for database connections."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrate_database(conn):
    """Run database migrations for schema updates."""
    cursor = conn.cursor()

    # Check if cache table needs migration (add source column)
    cursor.execute("PRAGMA table_info(cache)")
    cache_columns = {row[1] for row in cursor.fetchall()}

    if "source" not in cache_columns and "ticker" in cache_columns:
        logger.info("Migrating cache table to add source column...")

        # Create new table with updated schema
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cache_new (
                ticker TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'musaffa',
                status TEXT NOT NULL,
                compliance_ranking TEXT,
                details TEXT,
                cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (ticker, source)
            )
        """)

        # Migrate existing data (set source to 'musaffa' for existing entries)
        cursor.execute("""
            INSERT OR IGNORE INTO cache_new (ticker, source, status, compliance_ranking, details, cached_at)
            SELECT ticker, 'musaffa', status, compliance_ranking, details, cached_at FROM cache
        """)

        # Drop old table and rename new one
        cursor.execute("DROP TABLE cache")
        cursor.execute("ALTER TABLE cache_new RENAME TO cache")

        logger.info("Cache table migration completed")

    cursor.execute("PRAGMA table_info(cache)")
    cache_columns = {row[1] for row in cursor.fetchall()}
    cache_additions = {
        "company_name": "TEXT",
        "error_message": "TEXT",
        "quote_type": "TEXT",
        "state": "TEXT NOT NULL DEFAULT 'SUCCESS'",
        "asset_type": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
        "url": "TEXT",
        "evidence": "TEXT",
        "methodology": "TEXT",
        "retrieval_method": "TEXT NOT NULL DEFAULT 'deterministic'",
        "checked_at": "TEXT",
        "schema_version": "INTEGER NOT NULL DEFAULT 1",
    }
    for column, definition in cache_additions.items():
        if column not in cache_columns:
            cursor.execute(f"ALTER TABLE cache ADD COLUMN {column} {definition}")

    # Check if checks table needs migration
    cursor.execute("PRAGMA table_info(checks)")
    checks_columns = {row[1] for row in cursor.fetchall()}

    if "musaffa_status" not in checks_columns and "status" in checks_columns:
        logger.info("Migrating checks table to add multi-source columns...")

        # Create new table with updated schema
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS checks_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                musaffa_status TEXT,
                zoya_status TEXT,
                final_status TEXT NOT NULL,
                is_conflict BOOLEAN DEFAULT 0,
                checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Migrate existing data
        cursor.execute("""
            INSERT INTO checks_new (user_id, ticker, musaffa_status, final_status, is_conflict, checked_at)
            SELECT user_id, ticker, status, status, 0, checked_at FROM checks
        """)

        # Drop old table and rename new one
        cursor.execute("DROP TABLE checks")
        cursor.execute("ALTER TABLE checks_new RENAME TO checks")

        # Recreate indexes
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_checks_user_id
            ON checks(user_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_checks_ticker
            ON checks(ticker)
        """)

        logger.info("Checks table migration completed")

    cursor.execute("PRAGMA table_info(checks)")
    checks_columns = {row[1] for row in cursor.fetchall()}
    checks_additions = {
        "provider_results": "TEXT",
        "is_provisional": "BOOLEAN NOT NULL DEFAULT 0",
        "confirmation_count": "INTEGER NOT NULL DEFAULT 0",
    }
    for column, definition in checks_additions.items():
        if column not in checks_columns:
            cursor.execute(f"ALTER TABLE checks ADD COLUMN {column} {definition}")


def init_database():
    """Initialize database tables."""
    with get_connection() as conn:
        cursor = conn.cursor()

        # Cache table for ticker results (with source as part of primary key)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cache (
                ticker TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'musaffa',
                status TEXT NOT NULL,
                compliance_ranking TEXT,
                details TEXT,
                company_name TEXT,
                error_message TEXT,
                quote_type TEXT,
                state TEXT NOT NULL DEFAULT 'SUCCESS',
                asset_type TEXT NOT NULL DEFAULT 'UNKNOWN',
                url TEXT,
                evidence TEXT,
                methodology TEXT,
                retrieval_method TEXT NOT NULL DEFAULT 'deterministic',
                checked_at TEXT,
                schema_version INTEGER NOT NULL DEFAULT 2,
                cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (ticker, source)
            )
        """)

        # Image cache table for extracted tickers (24-hour TTL)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS image_cache (
                image_hash TEXT PRIMARY KEY,
                tickers TEXT NOT NULL,
                cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # History table for user checks (with multi-source support)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                musaffa_status TEXT,
                zoya_status TEXT,
                final_status TEXT NOT NULL,
                is_conflict BOOLEAN DEFAULT 0,
                provider_results TEXT,
                is_provisional BOOLEAN NOT NULL DEFAULT 0,
                confirmation_count INTEGER NOT NULL DEFAULT 0,
                checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Index for faster queries
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_checks_user_id
            ON checks(user_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_checks_ticker
            ON checks(ticker)
        """)

        # Run migrations for existing databases
        _migrate_database(conn)

        logger.info("Database initialized successfully")


class TickerCache:
    """Cache layer for ticker screening results."""

    @staticmethod
    def get(ticker: str, source: str = "musaffa") -> dict | None:
        """Get cached result for a ticker from a specific source if not expired."""
        ticker = ticker.upper()
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM cache WHERE ticker = ? AND source = ?", (ticker, source))
            row = cursor.fetchone()

            if row is None:
                return None

            if row["schema_version"] != CACHE_SCHEMA_VERSION:
                cursor.execute(
                    "DELETE FROM cache WHERE ticker = ? AND source = ?",
                    (ticker, source),
                )
                return None

            # Check if cache has expired
            cached_at = datetime.fromisoformat(row["cached_at"])
            ttl_hours = (
                NOT_COVERED_CACHE_TTL_HOURS if row["status"] == "NOT_COVERED" else CACHE_TTL_HOURS
            )
            if datetime.now() - cached_at > timedelta(hours=ttl_hours):
                # Cache expired, delete it
                cursor.execute(
                    "DELETE FROM cache WHERE ticker = ? AND source = ?",
                    (ticker, source),
                )
                return None

            return {
                "ticker": row["ticker"],
                "source": row["source"],
                "status": row["status"],
                "compliance_ranking": row["compliance_ranking"],
                "details": row["details"],
                "company_name": row["company_name"],
                "error_message": row["error_message"],
                "quote_type": row["quote_type"],
                "state": row["state"],
                "asset_type": row["asset_type"],
                "url": row["url"],
                "evidence": row["evidence"],
                "methodology": row["methodology"],
                "retrieval_method": row["retrieval_method"],
                "checked_at": row["checked_at"],
                "schema_version": row["schema_version"],
                "cached_at": row["cached_at"],
                "from_cache": True,
            }

    @staticmethod
    def set(
        ticker: str,
        status: str,
        source: str,
        compliance_ranking: str | None = None,
        details: str | None = None,
        company_name: str | None = None,
        error_message: str | None = None,
        quote_type: str | None = None,
        state: str = "SUCCESS",
        asset_type: str = "UNKNOWN",
        url: str | None = None,
        evidence: str | None = None,
        methodology: str | None = None,
        retrieval_method: str = "deterministic",
        checked_at: str | None = None,
    ):
        """Cache a ticker result for a specific source."""
        ticker = ticker.upper()
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO cache (
                    ticker, source, status, compliance_ranking, details,
                    company_name, error_message, quote_type, state, asset_type,
                    url, evidence, methodology, retrieval_method, checked_at,
                    cached_at, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    ticker,
                    source,
                    status,
                    compliance_ranking,
                    details,
                    company_name,
                    error_message,
                    quote_type,
                    state,
                    asset_type,
                    url,
                    evidence,
                    methodology,
                    retrieval_method,
                    checked_at,
                    datetime.now().isoformat(),
                    CACHE_SCHEMA_VERSION,
                ),
            )
            logger.debug(f"Cached result for {ticker} from {source}")

    @staticmethod
    def invalidate(ticker: str, source: str | None = None):
        """Remove a ticker from cache. If source is None, removes from all sources."""
        ticker = ticker.upper()
        with get_connection() as conn:
            cursor = conn.cursor()
            if source:
                cursor.execute(
                    "DELETE FROM cache WHERE ticker = ? AND source = ?",
                    (ticker, source),
                )
            else:
                cursor.execute("DELETE FROM cache WHERE ticker = ?", (ticker,))

    @staticmethod
    def clear_expired():
        """Remove all expired cache entries."""
        with get_connection() as conn:
            cursor = conn.cursor()
            expiry_time = (datetime.now() - timedelta(hours=CACHE_TTL_HOURS)).isoformat()
            cursor.execute("DELETE FROM cache WHERE cached_at < ?", (expiry_time,))
            deleted = cursor.rowcount
            if deleted > 0:
                logger.info(f"Cleared {deleted} expired cache entries")


class CheckHistory:
    """Historical tracking for user checks."""

    @staticmethod
    def record(
        user_id: int | str,
        ticker: str,
        final_status: str,
        provider_results: dict | None = None,
        is_conflict: bool = False,
        is_provisional: bool = False,
        confirmation_count: int = 0,
        musaffa_status: str | None = None,
        zoya_status: str | None = None,
    ):
        """Record a check in history with multi-source support."""
        ticker = ticker.upper()
        with get_connection() as conn:
            cursor = conn.cursor()
            serialized = json.dumps(provider_results or {}, separators=(",", ":"))
            cursor.execute(
                """
                INSERT INTO checks (
                    user_id, ticker, musaffa_status, zoya_status, final_status,
                    is_conflict, provider_results, is_provisional, confirmation_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    user_id,
                    ticker,
                    musaffa_status,
                    zoya_status,
                    final_status,
                    1 if is_conflict else 0,
                    serialized,
                    1 if is_provisional else 0,
                    confirmation_count,
                ),
            )
            logger.debug(f"Recorded check: user={user_id}, ticker={ticker}, conflict={is_conflict}")

    @staticmethod
    def get_user_history(user_id: int | str, limit: int = 20) -> list:
        """Get recent checks for a user."""
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT ticker, final_status as status, musaffa_status, zoya_status,
                       is_conflict, is_provisional, confirmation_count,
                       provider_results, checked_at
                FROM checks
                WHERE user_id = ?
                ORDER BY checked_at DESC
                LIMIT ?
            """,
                (user_id, limit),
            )
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def get_stats(user_id: int | str) -> dict:
        """Get statistics for a user."""
        with get_connection() as conn:
            cursor = conn.cursor()

            # Total checks
            cursor.execute("SELECT COUNT(*) FROM checks WHERE user_id = ?", (user_id,))
            total = cursor.fetchone()[0]

            # Unique tickers
            cursor.execute(
                "SELECT COUNT(DISTINCT ticker) FROM checks WHERE user_id = ?",
                (user_id,),
            )
            unique_tickers = cursor.fetchone()[0]

            # Status breakdown (using final_status)
            cursor.execute(
                """
                SELECT final_status as status, COUNT(*) as count
                FROM checks
                WHERE user_id = ?
                GROUP BY final_status
            """,
                (user_id,),
            )
            status_counts = {row["status"]: row["count"] for row in cursor.fetchall()}

            # Conflict count
            cursor.execute(
                "SELECT COUNT(*) FROM checks WHERE user_id = ? AND is_conflict = 1",
                (user_id,),
            )
            conflict_count = cursor.fetchone()[0]

            return {
                "total_checks": total,
                "unique_tickers": unique_tickers,
                "status_breakdown": status_counts,
                "conflict_count": conflict_count,
            }


class ImageCache:
    """Cache layer for image-to-tickers extraction results."""

    @staticmethod
    def get(image_hash: str) -> list[str] | None:
        """Get cached tickers for an image hash if not expired."""
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT tickers, cached_at FROM image_cache WHERE image_hash = ?",
                (image_hash,),
            )
            row = cursor.fetchone()

            if row is None:
                return None

            # Check if cache has expired (same TTL as ticker cache)
            cached_at = datetime.fromisoformat(row["cached_at"])
            if datetime.now() - cached_at > timedelta(hours=CACHE_TTL_HOURS):
                cursor.execute("DELETE FROM image_cache WHERE image_hash = ?", (image_hash,))
                return None

            # Parse JSON list of tickers
            try:
                return json.loads(row["tickers"])
            except json.JSONDecodeError:
                return None

    @staticmethod
    def set(image_hash: str, tickers: list[str]):
        """Cache extracted tickers for an image hash."""
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO image_cache (image_hash, tickers, cached_at)
                VALUES (?, ?, ?)
            """,
                (image_hash, json.dumps(tickers), datetime.now().isoformat()),
            )
            logger.debug(f"Cached {len(tickers)} tickers for image hash {image_hash[:8]}...")

    @staticmethod
    def clear_expired():
        """Remove all expired image cache entries."""
        with get_connection() as conn:
            cursor = conn.cursor()
            expiry_time = (datetime.now() - timedelta(hours=CACHE_TTL_HOURS)).isoformat()
            cursor.execute("DELETE FROM image_cache WHERE cached_at < ?", (expiry_time,))
            deleted = cursor.rowcount
            if deleted > 0:
                logger.info(f"Cleared {deleted} expired image cache entries")
