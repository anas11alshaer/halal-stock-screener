"""Base classes and utilities for stock scrapers."""

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import httpx
import yfinance as yf

from config import REQUEST_TIMEOUT, MAX_RETRIES

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# In-memory cache for quote types (ETF/EQUITY/etc.) — never changes for a ticker
_quote_type_cache: dict[str, str] = {}


async def get_quote_type(ticker: str) -> str:
    """Return the yfinance quoteType for a ticker (e.g. 'ETF', 'EQUITY').

    Results are cached in memory for the lifetime of the process.
    Falls back to 'EQUITY' if yfinance cannot determine the type.
    """
    ticker = ticker.upper()
    if ticker in _quote_type_cache:
        return _quote_type_cache[ticker]

    def _fetch() -> str:
        try:
            info = yf.Ticker(ticker).info
            return info.get("quoteType", "EQUITY")
        except Exception as e:
            logger.warning(f"yfinance quote type lookup failed for {ticker}: {e}")
            return "EQUITY"

    quote_type = await asyncio.to_thread(_fetch)
    _quote_type_cache[ticker] = quote_type
    logger.debug(f"{ticker}: quoteType={quote_type}")
    return quote_type


class ComplianceStatus(Enum):
    """Stock compliance status."""

    HALAL = "HALAL"
    NOT_HALAL = "NOT_HALAL"
    DOUBTFUL = "DOUBTFUL"
    NOT_COVERED = "NOT_COVERED"
    ERROR = "ERROR"


STATUS_ICON: dict[ComplianceStatus, str] = {
    ComplianceStatus.HALAL: "✅",
    ComplianceStatus.NOT_HALAL: "❌",
    ComplianceStatus.DOUBTFUL: "⚠️",
    ComplianceStatus.NOT_COVERED: "❓",
    ComplianceStatus.ERROR: "⚠️",
}

STATUS_TEXT: dict[ComplianceStatus, str] = {
    ComplianceStatus.HALAL: "Halal",
    ComplianceStatus.NOT_HALAL: "Not Halal",
    ComplianceStatus.DOUBTFUL: "Doubtful",
    ComplianceStatus.NOT_COVERED: "Not Covered",
    ComplianceStatus.ERROR: "Error",
}


@dataclass
class ScreeningResult:
    """Result of a stock screening."""

    ticker: str
    status: ComplianceStatus
    source: str = "unknown"
    compliance_ranking: Optional[str] = None
    company_name: Optional[str] = None
    details: Optional[str] = None
    error_message: Optional[str] = None
    quote_type: Optional[str] = None


class BaseScraper(ABC):
    """Abstract base for stock compliance scrapers with shared retry logic."""

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Short identifier used in logs and ScreeningResult.source (e.g. 'musaffa')."""

    @abstractmethod
    async def _fetch_single(self, client: httpx.AsyncClient, ticker: str) -> ScreeningResult:
        """Fetch and parse a single ticker. Subclasses implement site-specific logic."""

    # ------------------------------------------------------------------
    # Public API (shared retry / parallel logic)
    # ------------------------------------------------------------------

    async def screen_ticker(self, ticker: str) -> ScreeningResult:
        """Screen a single ticker with retry logic."""
        ticker = ticker.upper().strip()
        logger.info(f"Screening {ticker} on {self.source_name}")

        for attempt in range(MAX_RETRIES):
            try:
                async with httpx.AsyncClient(
                    headers=DEFAULT_HEADERS,
                    timeout=REQUEST_TIMEOUT,
                    follow_redirects=True,
                ) as client:
                    return await self._fetch_single(client, ticker)
            except Exception as e:
                logger.warning(
                    f"Error screening {ticker} on {self.source_name} "
                    f"(attempt {attempt + 1}): {e}"
                )
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)

        return ScreeningResult(
            ticker=ticker,
            status=ComplianceStatus.ERROR,
            source=self.source_name,
            error_message="Failed to fetch data after multiple attempts",
        )

    async def screen_multiple(self, tickers: list[str]) -> list[ScreeningResult]:
        """Screen multiple tickers in parallel with per-ticker retry."""
        if not tickers:
            return []

        source = self.source_name

        async with httpx.AsyncClient(
            headers=DEFAULT_HEADERS,
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        ) as client:

            async def fetch_with_retry(t: str) -> tuple[str, ScreeningResult]:
                for attempt in range(MAX_RETRIES):
                    try:
                        result = await self._fetch_single(client, t)
                        return (t, result)
                    except Exception as e:
                        logger.warning(f"Error screening {t} (attempt {attempt + 1}): {e}")
                        if attempt < MAX_RETRIES - 1:
                            await asyncio.sleep(2 ** attempt)
                return (t, ScreeningResult(
                    ticker=t,
                    status=ComplianceStatus.ERROR,
                    source=source,
                    error_message="Failed to fetch data",
                ))

            tasks = [fetch_with_retry(t) for t in tickers]
            completed = await asyncio.gather(*tasks)

        results = {ticker: result for ticker, result in completed}
        return [results[t] for t in tickers]

