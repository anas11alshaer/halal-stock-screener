"""Zoya.finance scraper for Halal stock screening using Playwright."""

import asyncio
import logging
from typing import Optional

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout, Browser

from config import ZOYA_BASE_URL, REQUEST_TIMEOUT, MAX_RETRIES
from scraper import ScreeningResult, ComplianceStatus

logger = logging.getLogger(__name__)

# Number of concurrent pages for parallel scraping
MAX_CONCURRENT_PAGES = 5


class ZoyaScraper:
    """Scraper for Zoya.finance stock screening data using Playwright."""

    def __init__(self):
        self.base_url = ZOYA_BASE_URL

    async def _fetch_single_page(self, browser: Browser, ticker: str) -> ScreeningResult:
        """Fetch and parse a single ticker page using an existing browser."""
        # Zoya uses lowercase tickers in URLs
        url = f"{self.base_url}/{ticker.lower().strip()}"

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        try:
            page = await context.new_page()

            try:
                response = await page.goto(url, timeout=REQUEST_TIMEOUT * 1000)

                # Check for 404
                if response and response.status == 404:
                    return ScreeningResult(
                        ticker=ticker,
                        status=ComplianceStatus.NOT_COVERED,
                        source="zoya",
                        error_message="Stock not found on Zoya"
                    )
            except PlaywrightTimeout:
                logger.warning(f"Timeout loading {url}")
                return ScreeningResult(
                    ticker=ticker,
                    status=ComplianceStatus.ERROR,
                    source="zoya",
                    error_message="Page load timeout"
                )

            # Wait for the compliance status to appear
            try:
                await page.wait_for_selector("text=Shariah", timeout=10000)
            except PlaywrightTimeout:
                pass  # Content might still be there, continue anyway

            # Small delay for any remaining dynamic content
            await asyncio.sleep(0.5)

            # Get page content
            text_content = await page.evaluate("() => document.body.innerText")

            return self._parse_content(ticker, text_content)

        finally:
            await context.close()

    def _parse_content(self, ticker: str, text: str) -> ScreeningResult:
        """Parse the page content to extract compliance info."""
        ticker = ticker.upper()
        text_lower = text.lower()

        status = ComplianceStatus.NOT_COVERED
        details = None

        # Check for "not found" or "no data"
        if "page not found" in text_lower or "404" in text_lower:
            return ScreeningResult(
                ticker=ticker,
                status=ComplianceStatus.NOT_COVERED,
                source="zoya",
                error_message="Stock not found on Zoya"
            )

        # Zoya status detection patterns (order matters!)
        # Pattern: "{ticker} stock is not Shariah-compliant" -> NOT_HALAL
        # Also check: "not considered halal"
        if "is not shariah-compliant" in text_lower or "not shariah compliant" in text_lower or "not considered halal" in text_lower:
            status = ComplianceStatus.NOT_HALAL
            logger.info(f"{ticker}: NOT_HALAL (zoya)")
        # Pattern: "flagged" or "questionable" -> DOUBTFUL
        elif "flagged" in text_lower or "questionable" in text_lower:
            status = ComplianceStatus.DOUBTFUL
            logger.info(f"{ticker}: DOUBTFUL (zoya)")
        # Pattern: "{ticker} stock is Shariah-compliant" -> HALAL
        # Also check: "considered halal"
        elif "is shariah-compliant" in text_lower or "shariah-compliant" in text_lower or "considered halal" in text_lower:
            status = ComplianceStatus.HALAL
            logger.info(f"{ticker}: HALAL (zoya)")

        if status == ComplianceStatus.NOT_COVERED:
            logger.warning(f"{ticker}: Could not determine status from Zoya")

        return ScreeningResult(
            ticker=ticker,
            status=status,
            source="zoya",
            details=details
        )

    async def screen_ticker(self, ticker: str) -> ScreeningResult:
        """Screen a single ticker for Halal compliance."""
        ticker = ticker.upper().strip()

        logger.info(f"Screening ticker on Zoya: {ticker}")

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
                logger.warning(f"Error screening {ticker} on Zoya (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)

        return ScreeningResult(
            ticker=ticker,
            status=ComplianceStatus.ERROR,
            source="zoya",
            error_message="Failed to fetch data from Zoya after multiple attempts"
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
                                logger.warning(f"Error screening {ticker} on Zoya (attempt {attempt + 1}): {e}")
                                if attempt < MAX_RETRIES - 1:
                                    await asyncio.sleep(1)
                        return (ticker, ScreeningResult(
                            ticker=ticker,
                            status=ComplianceStatus.ERROR,
                            source="zoya",
                            error_message="Failed to fetch data from Zoya"
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
