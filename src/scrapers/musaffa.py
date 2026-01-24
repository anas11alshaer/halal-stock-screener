"""Musaffa.com scraper for Halal stock screening."""

import asyncio
import logging
import re

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout, Browser

from config import MUSAFFA_BASE_URL, REQUEST_TIMEOUT, MAX_RETRIES
from .base import ComplianceStatus, ScreeningResult, get_chromium_path, MAX_CONCURRENT_PAGES

logger = logging.getLogger(__name__)


class MusaffaScraper:
    """Scraper for Musaffa.com stock screening data."""

    def __init__(self):
        self.base_url = MUSAFFA_BASE_URL

    async def _fetch_single_page(self, browser: Browser, ticker: str) -> ScreeningResult:
        """Fetch and parse a single ticker page."""
        url = f"{self.base_url}/{ticker.upper().strip()}/"

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
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
                    source="musaffa",
                    error_message="Page load timeout",
                )

            try:
                await page.wait_for_selector("text=Shariah Compliance", timeout=10000)
            except PlaywrightTimeout:
                pass

            await asyncio.sleep(0.5)

            content = await page.content()
            text_content = await page.evaluate("() => document.body.innerText")

            return self._parse_content(ticker, content, text_content)

        finally:
            await context.close()

    def _parse_content(self, ticker: str, html: str, text: str) -> ScreeningResult:
        """Parse page content to extract compliance info."""
        ticker = ticker.upper()
        text_lower = text.lower()

        status = ComplianceStatus.NOT_COVERED
        compliance_ranking = None
        company_name = None
        details = None

        has_compliance_section = "shariah compliance" in text_lower

        if not has_compliance_section:
            if "page not found" in text_lower or "does not exist" in text_lower or "404" in text_lower:
                return ScreeningResult(
                    ticker=ticker,
                    status=ComplianceStatus.NOT_COVERED,
                    source="musaffa",
                    error_message="Stock not found on Musaffa",
                )

        compliance_section = ""
        if "shariah compliance" in text_lower:
            idx = text_lower.find("shariah compliance")
            start = max(0, idx - 50)
            end = min(len(text), idx + 200)
            compliance_section = text_lower[start:end]

        if "not halal" in compliance_section or "non-halal" in compliance_section:
            status = ComplianceStatus.NOT_HALAL
            logger.info(f"{ticker}: NOT_HALAL (musaffa)")
        elif "doubtful" in compliance_section:
            status = ComplianceStatus.DOUBTFUL
            logger.info(f"{ticker}: DOUBTFUL (musaffa)")
        elif "halal" in compliance_section:
            status = ComplianceStatus.HALAL
            logger.info(f"{ticker}: HALAL (musaffa)")
        elif re.search(r'\bhalal\b', text_lower) and not re.search(r'\bnot\s+halal\b', text_lower):
            status = ComplianceStatus.HALAL
            logger.info(f"{ticker}: HALAL (musaffa, fallback)")
        elif re.search(r'\bnot\s+halal\b', text_lower):
            status = ComplianceStatus.NOT_HALAL
            logger.info(f"{ticker}: NOT_HALAL (musaffa, fallback)")

        if status == ComplianceStatus.NOT_COVERED:
            logger.warning(f"{ticker}: Could not determine status (musaffa)")

        # Extract compliance ranking
        rank_patterns = [r'rank[:\s]*(\d)', r'(\d)\s*(?:star|stars)', r'compliance[:\s]*(\d+)%']
        for pattern in rank_patterns:
            match = re.search(pattern, text_lower)
            if match:
                compliance_ranking = match.group(0).strip()
                break

        # Extract company name
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
                company_name = None

        return ScreeningResult(
            ticker=ticker,
            status=status,
            source="musaffa",
            compliance_ranking=compliance_ranking,
            company_name=company_name,
            details=details,
        )

    async def screen_ticker(self, ticker: str) -> ScreeningResult:
        """Screen a single ticker for Halal compliance."""
        ticker = ticker.upper().strip()
        logger.info(f"Screening {ticker} on Musaffa")

        for attempt in range(MAX_RETRIES):
            try:
                async with async_playwright() as p:
                    chromium_path = get_chromium_path()
                    browser = await p.chromium.launch(
                        headless=True,
                        executable_path=chromium_path,
                    )
                    try:
                        return await self._fetch_single_page(browser, ticker)
                    finally:
                        await browser.close()
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

        results = {}

        async with async_playwright() as p:
            chromium_path = get_chromium_path()
            browser = await p.chromium.launch(
                headless=True,
                executable_path=chromium_path,
            )
            try:
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
                            source="musaffa",
                            error_message="Failed to fetch data",
                        ))

                tasks = [fetch_with_semaphore(t) for t in tickers]
                completed = await asyncio.gather(*tasks)

                for ticker, result in completed:
                    results[ticker] = result

            finally:
                await browser.close()

        return [results[t] for t in tickers]
