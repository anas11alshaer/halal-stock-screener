"""Musaffa.com scraper for Halal stock screening."""

import asyncio
import logging
import re

import httpx

from config import MUSAFFA_BASE_URL, REQUEST_TIMEOUT, MAX_RETRIES
from .base import ComplianceStatus, ScreeningResult, get_quote_type

MUSAFFA_ETF_BASE_URL = "https://musaffa.com/etf"

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class MusaffaScraper:
    """Scraper for Musaffa.com stock screening data."""

    def __init__(self):
        self.base_url = MUSAFFA_BASE_URL

    async def _fetch_single(self, client: httpx.AsyncClient, ticker: str) -> ScreeningResult:
        """Fetch and parse a single ticker page via HTTP."""
        ticker = ticker.upper().strip()
        quote_type = await get_quote_type(ticker)
        if quote_type == "ETF":
            url = f"{MUSAFFA_ETF_BASE_URL}/{ticker}/"
        else:
            url = f"{self.base_url}/{ticker}/"

        try:
            response = await client.get(url)
        except httpx.TimeoutException:
            logger.warning(f"Timeout loading {url}")
            return ScreeningResult(
                ticker=ticker,
                status=ComplianceStatus.ERROR,
                source="musaffa",
                error_message="Page load timeout",
            )
        except httpx.HTTPError as e:
            logger.warning(f"HTTP error loading {url}: {e}")
            return ScreeningResult(
                ticker=ticker,
                status=ComplianceStatus.ERROR,
                source="musaffa",
                error_message=f"HTTP error: {e}",
            )

        if response.status_code == 404:
            return ScreeningResult(
                ticker=ticker,
                status=ComplianceStatus.NOT_COVERED,
                source="musaffa",
                error_message="Stock not found on Musaffa",
            )

        html = response.text
        return self._parse_content(ticker, html)

    def _parse_content(self, ticker: str, html: str) -> ScreeningResult:
        """Parse page content to extract compliance info.

        Primary strategy: extract status from the meta description tag,
        which is server-side rendered and contains the compliance verdict
        even though the page body is client-side rendered (Angular).
        """
        ticker = ticker.upper()

        status = ComplianceStatus.NOT_COVERED
        company_name = None

        # Extract meta description — this is SSR and always contains the verdict
        meta_match = re.search(
            r'<meta\s+name="description"\s+content="([^"]*)"',
            html,
            re.IGNORECASE,
        )
        if meta_match:
            import html as html_mod
            meta_raw = html_mod.unescape(meta_match.group(1))
            meta = meta_raw.lower()

            # Extract company name from meta (e.g. "Johnson & Johnson - JNJ is considered")
            name_match = re.search(
                rf'(.+?)\s*-\s*{ticker.lower()}\s+is\s+considered',
                meta,
            )
            if name_match:
                # Clean up: remove leading date/report prefix
                raw_name = name_match.group(1).strip()
                # Remove "last updated: DD Month YYYY. " prefix
                raw_name = re.sub(
                    r'^last updated:.*?\.\s*', '', raw_name
                )
                # Remove "as of ... report, " prefix
                raw_name = re.sub(
                    r'^as of.*?,\s*', '', raw_name
                )
                company_name = raw_name.strip().title()

            if "not halal" in meta or "not shariah compliant" in meta:
                status = ComplianceStatus.NOT_HALAL
                logger.info(f"{ticker}: NOT_HALAL (musaffa)")
            elif "doubtful" in meta:
                status = ComplianceStatus.DOUBTFUL
                logger.info(f"{ticker}: DOUBTFUL (musaffa)")
            elif "halal" in meta or "shariah compliant" in meta:
                status = ComplianceStatus.HALAL
                logger.info(f"{ticker}: HALAL (musaffa)")
            else:
                logger.warning(f"{ticker}: Could not determine status from meta (musaffa)")
        else:
            # No meta description found — check for 404-like pages
            html_lower = html.lower()
            if "page not found" in html_lower or "does not exist" in html_lower:
                return ScreeningResult(
                    ticker=ticker,
                    status=ComplianceStatus.NOT_COVERED,
                    source="musaffa",
                    error_message="Stock not found on Musaffa",
                )
            logger.warning(f"{ticker}: No meta description found (musaffa)")

        return ScreeningResult(
            ticker=ticker,
            status=status,
            source="musaffa",
            company_name=company_name,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def screen_ticker(self, ticker: str) -> ScreeningResult:
        """Screen a single ticker for Halal compliance."""
        ticker = ticker.upper().strip()
        logger.info(f"Screening {ticker} on Musaffa")

        for attempt in range(MAX_RETRIES):
            try:
                async with httpx.AsyncClient(
                    headers=_HEADERS,
                    timeout=REQUEST_TIMEOUT,
                    follow_redirects=True,
                ) as client:
                    return await self._fetch_single(client, ticker)
            except Exception as e:
                logger.warning(f"Error screening {ticker} on Musaffa (attempt {attempt + 1}): {e}")
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)

        return ScreeningResult(
            ticker=ticker,
            status=ComplianceStatus.ERROR,
            source="musaffa",
            error_message="Failed to fetch data after multiple attempts",
        )

    async def screen_multiple(self, tickers: list[str]) -> list[ScreeningResult]:
        """Screen multiple tickers in parallel."""
        if not tickers:
            return []

        async with httpx.AsyncClient(
            headers=_HEADERS,
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        ) as client:

            async def fetch_with_retry(ticker: str) -> tuple[str, ScreeningResult]:
                for attempt in range(MAX_RETRIES):
                    try:
                        result = await self._fetch_single(client, ticker)
                        return (ticker, result)
                    except Exception as e:
                        logger.warning(f"Error screening {ticker} (attempt {attempt + 1}): {e}")
                        if attempt < MAX_RETRIES - 1:
                            await asyncio.sleep(1)
                return (ticker, ScreeningResult(
                    ticker=ticker,
                    status=ComplianceStatus.ERROR,
                    source="musaffa",
                    error_message="Failed to fetch data",
                ))

            tasks = [fetch_with_retry(t) for t in tickers]
            completed = await asyncio.gather(*tasks)

        results = {ticker: result for ticker, result in completed}
        return [results[t] for t in tickers]
