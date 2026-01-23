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


def init_database():
    """Initialize database tables."""
    with get_connection() as conn:
        cursor = conn.cursor()

        # Cache table for ticker results
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cache (
                ticker TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                compliance_ranking TEXT,
                details TEXT,
                cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # History table for user checks
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                status TEXT NOT NULL,
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

        logger.info("Database initialized successfully")


class TickerCache:
    """Cache layer for ticker screening results."""

    @staticmethod
    def get(ticker: str) -> Optional[dict]:
        """Get cached result for a ticker if not expired."""
        ticker = ticker.upper()
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM cache WHERE ticker = ?",
                (ticker,)
            )
            row = cursor.fetchone()

            if row is None:
                return None

            # Check if cache has expired
            cached_at = datetime.fromisoformat(row["cached_at"])
            if datetime.now() - cached_at > timedelta(hours=CACHE_TTL_HOURS):
                # Cache expired, delete it
                cursor.execute("DELETE FROM cache WHERE ticker = ?", (ticker,))
                return None

            return {
                "ticker": row["ticker"],
                "status": row["status"],
                "compliance_ranking": row["compliance_ranking"],
                "details": row["details"],
                "cached_at": row["cached_at"],
                "from_cache": True
            }

    @staticmethod
    def set(ticker: str, status: str, compliance_ranking: str = None,
            details: str = None):
        """Cache a ticker result."""
        ticker = ticker.upper()
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO cache (ticker, status, compliance_ranking, details, cached_at)
                VALUES (?, ?, ?, ?, ?)
            """, (ticker, status, compliance_ranking, details, datetime.now().isoformat()))
            logger.debug(f"Cached result for {ticker}")

    @staticmethod
    def invalidate(ticker: str):
        """Remove a ticker from cache."""
        ticker = ticker.upper()
        with get_connection() as conn:
            cursor = conn.cursor()
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
    def record(user_id: int, ticker: str, status: str):
        """Record a check in history."""
        ticker = ticker.upper()
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO checks (user_id, ticker, status)
                VALUES (?, ?, ?)
            """, (user_id, ticker, status))
            logger.debug(f"Recorded check: user={user_id}, ticker={ticker}")

    @staticmethod
    def get_user_history(user_id: int, limit: int = 20) -> list:
        """Get recent checks for a user."""
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT ticker, status, checked_at
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
                SELECT user_id, status, checked_at
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

            # Status breakdown
            cursor.execute("""
                SELECT status, COUNT(*) as count
                FROM checks
                WHERE user_id = ?
                GROUP BY status
            """, (user_id,))
            status_counts = {row["status"]: row["count"] for row in cursor.fetchall()}

            return {
                "total_checks": total,
                "unique_tickers": unique_tickers,
                "status_breakdown": status_counts
            }
