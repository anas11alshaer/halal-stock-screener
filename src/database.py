"""Database module for caching and historical tracking."""

import sqlite3
from datetime import datetime, timedelta
from typing import Optional
from contextlib import contextmanager
import logging

from config import DATABASE_PATH, CACHE_TTL_HOURS

logger = logging.getLogger(__name__)


@contextmanager
def get_connection():
    """Context manager for database connections."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
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
                cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (ticker, source)
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
    def get(ticker: str, source: str = "musaffa") -> Optional[dict]:
        """Get cached result for a ticker from a specific source if not expired."""
        ticker = ticker.upper()
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM cache WHERE ticker = ? AND source = ?",
                (ticker, source)
            )
            row = cursor.fetchone()

            if row is None:
                return None

            # Check if cache has expired
            cached_at = datetime.fromisoformat(row["cached_at"])
            if datetime.now() - cached_at > timedelta(hours=CACHE_TTL_HOURS):
                # Cache expired, delete it
                cursor.execute("DELETE FROM cache WHERE ticker = ? AND source = ?", (ticker, source))
                return None

            return {
                "ticker": row["ticker"],
                "source": row["source"],
                "status": row["status"],
                "compliance_ranking": row["compliance_ranking"],
                "details": row["details"],
                "cached_at": row["cached_at"],
                "from_cache": True
            }

    @staticmethod
    def get_all_sources(ticker: str) -> dict[str, dict]:
        """Get cached results for a ticker from all sources."""
        ticker = ticker.upper()
        results = {}
        for source in ["musaffa", "zoya"]:
            cached = TickerCache.get(ticker, source)
            if cached:
                results[source] = cached
        return results

    @staticmethod
    def set(ticker: str, status: str, source: str = "musaffa",
            compliance_ranking: str = None, details: str = None):
        """Cache a ticker result for a specific source."""
        ticker = ticker.upper()
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO cache (ticker, source, status, compliance_ranking, details, cached_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (ticker, source, status, compliance_ranking, details, datetime.now().isoformat()))
            logger.debug(f"Cached result for {ticker} from {source}")

    @staticmethod
    def invalidate(ticker: str, source: str = None):
        """Remove a ticker from cache. If source is None, removes from all sources."""
        ticker = ticker.upper()
        with get_connection() as conn:
            cursor = conn.cursor()
            if source:
                cursor.execute("DELETE FROM cache WHERE ticker = ? AND source = ?", (ticker, source))
            else:
                cursor.execute("DELETE FROM cache WHERE ticker = ?", (ticker,))

    @staticmethod
    def clear_expired():
        """Remove all expired cache entries."""
        with get_connection() as conn:
            cursor = conn.cursor()
            expiry_time = (datetime.now() - timedelta(hours=CACHE_TTL_HOURS)).isoformat()
            cursor.execute(
                "DELETE FROM cache WHERE cached_at < ?",
                (expiry_time,)
            )
            deleted = cursor.rowcount
            if deleted > 0:
                logger.info(f"Cleared {deleted} expired cache entries")


class CheckHistory:
    """Historical tracking for user checks."""

    @staticmethod
    def record(user_id: int, ticker: str, final_status: str,
               musaffa_status: str = None, zoya_status: str = None,
               is_conflict: bool = False):
        """Record a check in history with multi-source support."""
        ticker = ticker.upper()
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO checks (user_id, ticker, musaffa_status, zoya_status, final_status, is_conflict)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, ticker, musaffa_status, zoya_status, final_status, 1 if is_conflict else 0))
            logger.debug(f"Recorded check: user={user_id}, ticker={ticker}, conflict={is_conflict}")

    @staticmethod
    def get_user_history(user_id: int, limit: int = 20) -> list:
        """Get recent checks for a user."""
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT ticker, final_status as status, musaffa_status, zoya_status, is_conflict, checked_at
                FROM checks
                WHERE user_id = ?
                ORDER BY checked_at DESC
                LIMIT ?
            """, (user_id, limit))
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def get_ticker_history(ticker: str, limit: int = 20) -> list:
        """Get recent checks for a ticker."""
        ticker = ticker.upper()
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT user_id, final_status as status, musaffa_status, zoya_status, is_conflict, checked_at
                FROM checks
                WHERE ticker = ?
                ORDER BY checked_at DESC
                LIMIT ?
            """, (ticker, limit))
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def get_stats(user_id: int) -> dict:
        """Get statistics for a user."""
        with get_connection() as conn:
            cursor = conn.cursor()

            # Total checks
            cursor.execute(
                "SELECT COUNT(*) FROM checks WHERE user_id = ?",
                (user_id,)
            )
            total = cursor.fetchone()[0]

            # Unique tickers
            cursor.execute(
                "SELECT COUNT(DISTINCT ticker) FROM checks WHERE user_id = ?",
                (user_id,)
            )
            unique_tickers = cursor.fetchone()[0]

            # Status breakdown (using final_status)
            cursor.execute("""
                SELECT final_status as status, COUNT(*) as count
                FROM checks
                WHERE user_id = ?
                GROUP BY final_status
            """, (user_id,))
            status_counts = {row["status"]: row["count"] for row in cursor.fetchall()}

            # Conflict count
            cursor.execute(
                "SELECT COUNT(*) FROM checks WHERE user_id = ? AND is_conflict = 1",
                (user_id,)
            )
            conflict_count = cursor.fetchone()[0]

            return {
                "total_checks": total,
                "unique_tickers": unique_tickers,
                "status_breakdown": status_counts,
                "conflict_count": conflict_count
            }
