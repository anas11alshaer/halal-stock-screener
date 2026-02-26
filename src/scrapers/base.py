"""Base classes and utilities for stock scrapers."""

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import yfinance as yf

logger = logging.getLogger(__name__)

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

    loop = asyncio.get_event_loop()
    quote_type = await loop.run_in_executor(None, _fetch)
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

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "status": self.status.value,
            "source": self.source,
            "compliance_ranking": self.compliance_ranking,
            "company_name": self.company_name,
            "details": self.details,
            "error_message": self.error_message,
        }
