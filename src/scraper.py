"""Musaffa.com scraper for Halal stock screening using Playwright."""

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Optional
from enum import Enum

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout, Browser

from config import MUSAFFA_BASE_URL, REQUEST_TIMEOUT, MAX_RETRIES

logger = logging.getLogger(__name__)

# Number of concurrent pages for parallel scraping
MAX_CONCURRENT_PAGES = 5


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
    source: str = "musaffa"
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
            "error_message": self.error_message
        }


class MusaffaScraper:
    """Scraper for Musaffa.com stock screening data using Playwright."""

    def __init__(self):
        self.base_url = MUSAFFA_BASE_URL

    async def _fetch_single_page(self, browser: Browser, ticker: str) -> ScreeningResult:
        """Fetch and parse a single ticker page using an existing browser."""
        url = f"{self.base_url}/{ticker.upper().strip()}/"

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        try:
            page = await context.new_page()

            try:
                await page.goto(url, timeout=REQUEST_TIMEOUT * 1000)
            except PlaywrightTimeout:
                logger.warning(f"Timeout loading {url}")
                return ScreeningResult(
                    ticker=ticker,
                    status=ComplianceStatus.ERROR,
                    error_message="Page load timeout"
                )

            # Wait for the compliance status to appear
            try:
                await page.wait_for_selector("text=Shariah Compliance", timeout=10000)
            except PlaywrightTimeout:
                pass  # Content might still be there, continue anyway

            # Small delay for any remaining dynamic content
            await asyncio.sleep(0.5)

            # Get page content
            content = await page.content()
            text_content = await page.evaluate("() => document.body.innerText")

            return self._parse_content(ticker, content, text_content)

        finally:
            await context.close()

    def _parse_content(self, ticker: str, html: str, text: str) -> ScreeningResult:
        """Parse the page content to extract compliance info."""
        ticker = ticker.upper()
        text_lower = text.lower()

        status = ComplianceStatus.NOT_COVERED
        compliance_ranking = None
        company_name = None
        details = None

        # First check if the page has the compliance section
        has_compliance_section = "shariah compliance" in text_lower

        # Check for "not found" or "no data" ONLY if there's no compliance section
        if not has_compliance_section:
            if "page not found" in text_lower or "does not exist" in text_lower or "404" in text_lower:
                return ScreeningResult(
                    ticker=ticker,
                    status=ComplianceStatus.NOT_COVERED,
                    error_message="Stock not found on Musaffa"
                )

        # Find the compliance section
        compliance_section = ""
        if "shariah compliance" in text_lower:
            idx = text_lower.find("shariah compliance")
            start = max(0, idx - 50)
            end = min(len(text), idx + 200)
            compliance_section = text_lower[start:end]

        # Check the compliance section for status (order matters!)
        if "not halal" in compliance_section or "non-halal" in compliance_section:
            status = ComplianceStatus.NOT_HALAL
            logger.info(f"{ticker}: NOT_HALAL")
        elif "doubtful" in compliance_section:
            status = ComplianceStatus.DOUBTFUL
            logger.info(f"{ticker}: DOUBTFUL")
        elif "halal" in compliance_section:
            status = ComplianceStatus.HALAL
            logger.info(f"{ticker}: HALAL")
        # Fallback: check the whole page
        elif re.search(r'\bhalal\b', text_lower) and not re.search(r'\bnot\s+halal\b', text_lower):
            status = ComplianceStatus.HALAL
            logger.info(f"{ticker}: HALAL (fallback)")
        elif re.search(r'\bnot\s+halal\b', text_lower):
            status = ComplianceStatus.NOT_HALAL
            logger.info(f"{ticker}: NOT_HALAL (fallback)")

        if status == ComplianceStatus.NOT_COVERED:
            logger.warning(f"{ticker}: Could not determine status")

        # Try to extract compliance ranking
        rank_patterns = [
            r'rank[:\s]*(\d)',
            r'(\d)\s*(?:star|stars)',
            r'compliance[:\s]*(\d+)%',
            r'score[:\s]*(\d+)',
        ]
        for pattern in rank_patterns:
            match = re.search(pattern, text_lower)
            if match:
                compliance_ranking = match.group(0).strip()
                break

        # Try to find company name
        name_patterns = [
            r'Purification of ([A-Z][A-Za-z\s&.,]+(?:Inc|Corp|Ltd|LLC|Company|Co))',
            r'Is ([A-Z][A-Za-z\s&.,]+(?:Inc|Corp|Ltd|LLC|Company|Co))\s*[-–]',
            rf'([A-Z][A-Za-z\s&.,]+(?:Inc|Corp|Ltd|LLC|Company|Co))\s*[-–]\s*{ticker}',
        ]
        for pattern in name_patterns:
            match = re.search(pattern, text)
            if match:
                company_name = match.group(1).strip()
                company_name = re.sub(r'[\s,.\-–]+$', '', company_name)
                if len(company_name) > 3:
                    break
                else:
                    company_name = None

        # Extract any additional details
        detail_patterns = [
            r'(business\s+screening[:\s]+[^\n]+)',
            r'(financial\s+screening[:\s]+[^\n]+)',
            r'(compliance\s+status[:\s]+[^\n]+)',
        ]
        details_found = []
        for pattern in detail_patterns:
            match = re.search(pattern, text_lower)
            if match:
                details_found.append(match.group(1).strip())
        if details_found:
            details = "; ".join(details_found)[:300]

        return ScreeningResult(
            ticker=ticker,
            status=status,
            compliance_ranking=compliance_ranking,
            company_name=company_name,
            details=details
        )

    async def screen_ticker(self, ticker: str) -> ScreeningResult:
        """Screen a single ticker for Halal compliance."""
        ticker = ticker.upper().strip()

        logger.info(f"Screening ticker: {ticker}")

        for attempt in range(MAX_RETRIES):
            try:
                async with async_playwright() as p:
                    browser = await p.chromium.launch(headless=True)
                    try:
                        result = await self._fetch_single_page(browser, ticker)
                        return result
                    finally:
                        await browser.close()
            except Exception as e:
                logger.warning(f"Error screening {ticker} (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)

        return ScreeningResult(
            ticker=ticker,
            status=ComplianceStatus.ERROR,
            error_message="Failed to fetch data after multiple attempts"
        )

    async def screen_multiple(self, tickers: list[str]) -> list[ScreeningResult]:
        """Screen multiple tickers in parallel batches for better performance."""
        if not tickers:
            return []

        results = {}

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                # Process tickers in parallel batches
                semaphore = asyncio.Semaphore(MAX_CONCURRENT_PAGES)

                async def fetch_with_semaphore(ticker: str) -> tuple[str, ScreeningResult]:
                    async with semaphore:
                        for attempt in range(MAX_RETRIES):
                            try:
                                result = await self._fetch_single_page(browser, ticker)
                                return (ticker, result)
                            except Exception as e:
                                logger.warning(f"Error screening {ticker} (attempt {attempt + 1}): {e}")
                                if attempt < MAX_RETRIES - 1:
                                    await asyncio.sleep(1)
                        return (ticker, ScreeningResult(
                            ticker=ticker,
                            status=ComplianceStatus.ERROR,
                            error_message="Failed to fetch data"
                        ))

                # Run all tickers concurrently (semaphore limits actual parallelism)
                tasks = [fetch_with_semaphore(t) for t in tickers]
                completed = await asyncio.gather(*tasks)

                for ticker, result in completed:
                    results[ticker] = result

            finally:
                await browser.close()

        # Return results in original order
        return [results[t] for t in tickers]
