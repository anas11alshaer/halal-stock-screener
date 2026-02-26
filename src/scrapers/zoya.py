"""Zoya.finance scraper for Halal stock screening."""

import asyncio
import json
import logging
import re

import httpx

from config import ZOYA_BASE_URL, REQUEST_TIMEOUT, MAX_RETRIES
from .base import ComplianceStatus, ScreeningResult, get_quote_type

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class ZoyaScraper:
    """Scraper for Zoya.finance stock screening data."""

    def __init__(self):
        self.base_url = ZOYA_BASE_URL

    async def _fetch_single(self, client: httpx.AsyncClient, ticker: str) -> ScreeningResult:
        """Fetch and parse a single ticker page via HTTP."""
        ticker = ticker.upper().strip()
        quote_type = await get_quote_type(ticker)
        if quote_type == "ETF":
            logger.info(f"{ticker}: ETF — Zoya has no public ETF pages, returning NOT_COVERED")
            return ScreeningResult(
                ticker=ticker,
                status=ComplianceStatus.NOT_COVERED,
                source="zoya",
                error_message="Zoya does not cover ETFs",
            )

        url = f"{self.base_url}/{ticker.lower()}"

        try:
            response = await client.get(url)
        except httpx.TimeoutException:
            logger.warning(f"Timeout loading {url}")
            return ScreeningResult(
                ticker=ticker,
                status=ComplianceStatus.ERROR,
                source="zoya",
                error_message="Page load timeout",
            )
        except httpx.HTTPError as e:
            logger.warning(f"HTTP error loading {url}: {e}")
            return ScreeningResult(
                ticker=ticker,
                status=ComplianceStatus.ERROR,
                source="zoya",
                error_message=f"HTTP error: {e}",
            )

        if response.status_code == 404:
            return ScreeningResult(
                ticker=ticker,
                status=ComplianceStatus.NOT_COVERED,
                source="zoya",
                error_message="Stock not found on Zoya",
            )

        html = response.text
        return self._parse_content(ticker, html)

    def _parse_content(self, ticker: str, html: str) -> ScreeningResult:
        """Parse page content to extract compliance info.

        Primary strategy: extract the FAQ JSON-LD structured data which
        contains the definitive compliance verdict without template noise.
        Fallback: parse the main H2 heading.
        """
        ticker = ticker.upper()

        if "page not found" in html.lower() or "<title>404" in html.lower():
            return ScreeningResult(
                ticker=ticker,
                status=ComplianceStatus.NOT_COVERED,
                source="zoya",
                error_message="Stock not found on Zoya",
            )

        # Strategy 1: Parse JSON-LD FAQ structured data
        status = self._parse_jsonld(ticker, html)
        if status is not None:
            return ScreeningResult(ticker=ticker, status=status, source="zoya")

        # Strategy 2: Parse the main H2 heading
        # e.g. <h2>AAPL stock is <a ...>Shariah-compliant</a></h2>
        # or   <h2>BAC stock is not <a ...>Shariah-compliant</a></h2>
        h2_match = re.search(
            rf'{ticker.lower()}\s+stock\s+is\s+(not\s+)?.*?shariah-compliant',
            html.lower(),
        )
        if h2_match:
            if h2_match.group(1):  # "not" was captured
                status = ComplianceStatus.NOT_HALAL
                logger.info(f"{ticker}: NOT_HALAL (zoya, h2)")
            else:
                status = ComplianceStatus.HALAL
                logger.info(f"{ticker}: HALAL (zoya, h2)")
            return ScreeningResult(ticker=ticker, status=status, source="zoya")

        logger.warning(f"{ticker}: Could not determine status (zoya)")
        return ScreeningResult(
            ticker=ticker,
            status=ComplianceStatus.NOT_COVERED,
            source="zoya",
        )

    def _parse_jsonld(self, ticker: str, html: str) -> ComplianceStatus | None:
        """Extract compliance status from JSON-LD FAQPage data."""
        # Find all JSON-LD blocks
        for match in re.finditer(
            r'<script\s+type="application/ld\+json"[^>]*>(.*?)</script>',
            html,
            re.DOTALL,
        ):
            try:
                data = json.loads(match.group(1))
            except (json.JSONDecodeError, ValueError):
                continue

            if data.get("@type") != "FAQPage":
                continue

            for entity in data.get("mainEntity", []):
                answer_text = (
                    entity.get("acceptedAnswer", {}).get("text", "").lower()
                )
                if "is not shariah-compliant" in answer_text or "not shariah-compliant" in answer_text:
                    logger.info(f"{ticker}: NOT_HALAL (zoya, json-ld)")
                    return ComplianceStatus.NOT_HALAL
                elif "is shariah-compliant" in answer_text or "shariah-compliant" in answer_text:
                    logger.info(f"{ticker}: HALAL (zoya, json-ld)")
                    return ComplianceStatus.HALAL

        return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def screen_ticker(self, ticker: str) -> ScreeningResult:
        """Screen a single ticker for Halal compliance."""
        ticker = ticker.upper().strip()
        logger.info(f"Screening {ticker} on Zoya")

        for attempt in range(MAX_RETRIES):
            try:
                async with httpx.AsyncClient(
                    headers=_HEADERS,
                    timeout=REQUEST_TIMEOUT,
                    follow_redirects=True,
                ) as client:
                    return await self._fetch_single(client, ticker)
            except Exception as e:
                logger.warning(f"Error screening {ticker} on Zoya (attempt {attempt + 1}): {e}")
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)

        return ScreeningResult(
            ticker=ticker,
            status=ComplianceStatus.ERROR,
            source="zoya",
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
                    source="zoya",
                    error_message="Failed to fetch data",
                ))

            tasks = [fetch_with_retry(t) for t in tickers]
            completed = await asyncio.gather(*tasks)

        results = {ticker: result for ticker, result in completed}
        return [results[t] for t in tickers]
