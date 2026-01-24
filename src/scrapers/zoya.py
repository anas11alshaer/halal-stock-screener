"""Zoya.finance scraper for Halal stock screening."""

import asyncio
import logging

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout, Browser

from config import ZOYA_BASE_URL, REQUEST_TIMEOUT, MAX_RETRIES
from .base import ComplianceStatus, ScreeningResult, get_chromium_path, MAX_CONCURRENT_PAGES

logger = logging.getLogger(__name__)


class ZoyaScraper:
    """Scraper for Zoya.finance stock screening data."""

    def __init__(self):
        self.base_url = ZOYA_BASE_URL

    async def _fetch_single_page(self, browser: Browser, ticker: str) -> ScreeningResult:
        """Fetch and parse a single ticker page."""
        # Zoya uses lowercase tickers in URLs
        url = f"{self.base_url}/{ticker.lower().strip()}"

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        try:
            page = await context.new_page()

            try:
                response = await page.goto(url, timeout=REQUEST_TIMEOUT * 1000)

                if response and response.status == 404:
                    return ScreeningResult(
                        ticker=ticker,
                        status=ComplianceStatus.NOT_COVERED,
                        source="zoya",
                        error_message="Stock not found on Zoya",
                    )
            except PlaywrightTimeout:
                logger.warning(f"Timeout loading {url}")
                return ScreeningResult(
                    ticker=ticker,
                    status=ComplianceStatus.ERROR,
                    source="zoya",
                    error_message="Page load timeout",
                )

            try:
                await page.wait_for_selector("text=Shariah", timeout=10000)
            except PlaywrightTimeout:
                pass

            await asyncio.sleep(0.5)

            text_content = await page.evaluate("() => document.body.innerText")
            return self._parse_content(ticker, text_content)

        finally:
            await context.close()

    def _parse_content(self, ticker: str, text: str) -> ScreeningResult:
        """Parse page content to extract compliance info."""
        ticker = ticker.upper()
        text_lower = text.lower()

        status = ComplianceStatus.NOT_COVERED

        if "page not found" in text_lower or "404" in text_lower:
            return ScreeningResult(
                ticker=ticker,
                status=ComplianceStatus.NOT_COVERED,
                source="zoya",
                error_message="Stock not found on Zoya",
            )

        # Status detection (order matters!)
        if "is not shariah-compliant" in text_lower or "not shariah compliant" in text_lower or "not considered halal" in text_lower:
            status = ComplianceStatus.NOT_HALAL
            logger.info(f"{ticker}: NOT_HALAL (zoya)")
        elif "flagged" in text_lower or "questionable" in text_lower:
            status = ComplianceStatus.DOUBTFUL
            logger.info(f"{ticker}: DOUBTFUL (zoya)")
        elif "is shariah-compliant" in text_lower or "shariah-compliant" in text_lower or "considered halal" in text_lower:
            status = ComplianceStatus.HALAL
            logger.info(f"{ticker}: HALAL (zoya)")

        if status == ComplianceStatus.NOT_COVERED:
            logger.warning(f"{ticker}: Could not determine status (zoya)")

        return ScreeningResult(
            ticker=ticker,
            status=status,
            source="zoya",
        )

    async def screen_ticker(self, ticker: str) -> ScreeningResult:
        """Screen a single ticker for Halal compliance."""
        ticker = ticker.upper().strip()
        logger.info(f"Screening {ticker} on Zoya")

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
                            source="zoya",
                            error_message="Failed to fetch data",
                        ))

                tasks = [fetch_with_semaphore(t) for t in tickers]
                completed = await asyncio.gather(*tasks)

                for ticker, result in completed:
                    results[ticker] = result

            finally:
                await browser.close()

        return [results[t] for t in tickers]
