"""Base classes and utilities for stock scrapers."""

import asyncio
import logging
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

import httpx

from config import MAX_RETRIES, REQUEST_TIMEOUT

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

TRANSIENT_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


class ComplianceStatus(Enum):
    """Stock compliance status."""

    HALAL = "HALAL"
    NOT_HALAL = "NOT_HALAL"
    DOUBTFUL = "DOUBTFUL"
    NOT_COVERED = "NOT_COVERED"
    ERROR = "ERROR"


class AssetType(Enum):
    """Canonical security types used by every provider."""

    STOCK = "STOCK"
    ETF = "ETF"
    FUND = "FUND"
    UNKNOWN = "UNKNOWN"


class ResultState(Enum):
    """Operational result independent from the compliance verdict."""

    SUCCESS = "SUCCESS"
    NOT_COVERED = "NOT_COVERED"
    UNSUPPORTED_ASSET = "UNSUPPORTED_ASSET"
    NETWORK_ERROR = "NETWORK_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    PARSE_ERROR = "PARSE_ERROR"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"


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
    compliance_ranking: str | None = None
    company_name: str | None = None
    details: str | None = None
    error_message: str | None = None
    quote_type: str | None = None
    state: ResultState = ResultState.SUCCESS
    asset_type: AssetType = AssetType.UNKNOWN
    url: str | None = None
    evidence: str | None = None
    methodology: str | None = None
    checked_at: str | None = None
    retrieval_method: str = "deterministic"
    is_provisional: bool = False
    confirmation_count: int = 0
    review_text: str | None = None

    @property
    def is_confirmed(self) -> bool:
        return self.state == ResultState.SUCCESS and self.status in {
            ComplianceStatus.HALAL,
            ComplianceStatus.NOT_HALAL,
            ComplianceStatus.DOUBTFUL,
        }


@dataclass
class Security:
    """Provider-neutral identity resolved before screening."""

    symbol: str
    name: str | None = None
    asset_type: AssetType = AssetType.UNKNOWN
    exchange: str | None = None
    yahoo_symbol: str | None = None

    @property
    def quote_type(self) -> str:
        return {
            AssetType.STOCK: "EQUITY",
            AssetType.ETF: "ETF",
            AssetType.FUND: "MUTUALFUND",
        }.get(self.asset_type, "UNKNOWN")


async def get_quote_type(ticker: str) -> str:
    """Compatibility wrapper around the reliable Yahoo search resolver."""
    from security_resolver import YahooSecurityResolver

    resolved = await YahooSecurityResolver().resolve(ticker)
    if resolved.security:
        return resolved.security.quote_type
    return "UNKNOWN"


class BaseScraper(ABC):
    """Abstract base for stock compliance scrapers with shared retry logic."""

    supported_asset_types = frozenset(
        {AssetType.STOCK, AssetType.ETF, AssetType.FUND, AssetType.UNKNOWN}
    )

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Short identifier used in logs and ScreeningResult.source (e.g. 'musaffa')."""

    @abstractmethod
    async def _fetch_single(self, client: httpx.AsyncClient, security: Security) -> ScreeningResult:
        """Fetch and parse one resolved security."""

    # ------------------------------------------------------------------
    # Public API (shared retry / parallel logic)
    # ------------------------------------------------------------------

    def supports(self, security: Security) -> bool:
        return security.asset_type in self.supported_asset_types

    async def screen_security(self, security: Security) -> ScreeningResult:
        """Screen one security using a reusable HTTP client."""
        return (await self.screen_securities([security]))[0]

    async def screen_ticker(self, ticker: str) -> ScreeningResult:
        """Compatibility API for callers that have not resolved metadata."""
        ticker = ticker.upper().strip()
        return await self.screen_security(Security(symbol=ticker))

    async def screen_multiple(self, tickers: list[str]) -> list[ScreeningResult]:
        securities = [Security(symbol=ticker.upper().strip()) for ticker in tickers]
        return await self.screen_securities(securities)

    async def screen_securities(self, securities: list[Security]) -> list[ScreeningResult]:
        """Screen securities concurrently while sharing connections."""
        if not securities:
            return []

        async with httpx.AsyncClient(
            headers=DEFAULT_HEADERS,
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        ) as client:
            results = await asyncio.gather(
                *(self._screen_safe(client, security) for security in securities)
            )
        return list(results)

    async def _screen_safe(self, client: httpx.AsyncClient, security: Security) -> ScreeningResult:
        if not self.supports(security):
            return self.failure(
                security,
                ResultState.UNSUPPORTED_ASSET,
                f"{self.source_name} does not support {security.asset_type.value}",
            )
        logger.info("Screening %s on %s", security.symbol, self.source_name)
        try:
            result = await self._fetch_single(client, security)
            result.checked_at = result.checked_at or datetime.now(UTC).isoformat()
            result.asset_type = (
                security.asset_type if result.asset_type == AssetType.UNKNOWN else result.asset_type
            )
            result.quote_type = result.quote_type or security.quote_type
            result.company_name = result.company_name or security.name
            return result
        except httpx.TimeoutException:
            return self.failure(security, ResultState.NETWORK_ERROR, "Request timed out")
        except httpx.HTTPError as exc:
            logger.warning("%s request failed for %s: %s", self.source_name, security.symbol, exc)
            return self.failure(security, ResultState.NETWORK_ERROR, "Network request failed")
        except Exception:
            logger.exception("Unexpected %s failure for %s", self.source_name, security.symbol)
            return self.failure(
                security, ResultState.SOURCE_UNAVAILABLE, "Provider failed unexpectedly"
            )

    async def request(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        **kwargs,
    ) -> httpx.Response:
        """Perform one HTTP request with bounded transient retries."""
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                response = await client.request(method, url, **kwargs)
                if response.status_code not in TRANSIENT_STATUS_CODES:
                    return response
                if attempt == MAX_RETRIES - 1:
                    return response
                retry_after = response.headers.get("Retry-After")
                delay = self._retry_delay(attempt, retry_after)
                logger.warning(
                    "%s returned HTTP %d for %s; retrying in %.2fs",
                    self.source_name,
                    response.status_code,
                    url,
                    delay,
                )
                await asyncio.sleep(delay)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt == MAX_RETRIES - 1:
                    raise
                await asyncio.sleep(self._retry_delay(attempt, None))
        if last_error:
            raise last_error
        raise RuntimeError("Request retry loop ended without a response")

    @staticmethod
    def _retry_delay(attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), 30.0)
            except ValueError:
                pass
        return min(2**attempt + random.uniform(0, 0.25), 8.0)

    def failure(
        self,
        security: Security,
        state: ResultState,
        message: str,
        *,
        url: str | None = None,
        status: ComplianceStatus = ComplianceStatus.ERROR,
    ) -> ScreeningResult:
        return ScreeningResult(
            ticker=security.symbol,
            status=status,
            source=self.source_name,
            company_name=security.name,
            quote_type=security.quote_type,
            asset_type=security.asset_type,
            state=state,
            error_message=message,
            url=url,
            checked_at=datetime.now(UTC).isoformat(),
        )

    def http_failure(
        self, security: Security, response: httpx.Response, url: str
    ) -> ScreeningResult:
        if response.status_code == 404:
            return self.failure(
                security,
                ResultState.NOT_COVERED,
                "Security not found",
                url=url,
                status=ComplianceStatus.NOT_COVERED,
            )
        if response.status_code in (401, 403):
            return self.failure(
                security, ResultState.AUTH_REQUIRED, "Authentication required", url=url
            )
        if response.status_code == 402:
            return self.failure(
                security,
                ResultState.RATE_LIMITED,
                "Provider quota reached",
                url=url,
            )
        if response.status_code == 429:
            return self.failure(
                security,
                ResultState.RATE_LIMITED,
                "Provider rate limit reached",
                url=url,
            )
        return self.failure(
            security,
            ResultState.SOURCE_UNAVAILABLE,
            f"Provider returned HTTP {response.status_code}",
            url=url,
        )
