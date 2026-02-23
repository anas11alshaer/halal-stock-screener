"""Musaffa.com scraper for Halal stock screening."""

import asyncio
import logging
import re
from datetime import datetime

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout, Browser, BrowserContext

from config import (
    MUSAFFA_BASE_URL, REQUEST_TIMEOUT, MAX_RETRIES,
    MUSAFFA_EMAIL, MUSAFFA_PASSWORD, MUSAFFA_LOGIN_URL,
    MUSAFFA_SESSION_FILE, SESSION_MAX_AGE_HOURS,
)
from .base import ComplianceStatus, ScreeningResult, get_chromium_path, get_quote_type, MAX_CONCURRENT_PAGES

MUSAFFA_ETF_BASE_URL = "https://musaffa.com/etf"

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class MusaffaScraper:
    """Scraper for Musaffa.com stock screening data."""

    def __init__(self):
        self.base_url = MUSAFFA_BASE_URL

    # ------------------------------------------------------------------
    # Authentication helpers
    # ------------------------------------------------------------------

    async def _login(self, context: BrowserContext) -> bool:
        """Log in to Musaffa and return True on success."""
        page = await context.new_page()
        try:
            await page.goto(MUSAFFA_LOGIN_URL, timeout=30000)
            await asyncio.sleep(2)
            await page.fill('#email', MUSAFFA_EMAIL)
            await page.fill('#password', MUSAFFA_PASSWORD)
            await page.click('button.login-btn')
            try:
                await page.wait_for_url(
                    lambda url: '/authentication/login' not in url,
                    timeout=15000,
                )
                logger.info("Musaffa login successful")
                return True
            except PlaywrightTimeout:
                logger.warning("Musaffa login timed out — proceeding unauthenticated")
                return False
        except Exception as e:
            logger.warning(f"Musaffa login error: {e}")
            return False
        finally:
            await page.close()

    async def _get_context(self, browser: Browser) -> BrowserContext:
        """Return a browser context, reusing a saved session when available."""
        # Check for a fresh saved session
        if MUSAFFA_SESSION_FILE.exists():
            age_h = (
                datetime.now() - datetime.fromtimestamp(MUSAFFA_SESSION_FILE.stat().st_mtime)
            ).total_seconds() / 3600
            if age_h < SESSION_MAX_AGE_HOURS:
                logger.debug("Musaffa: reusing saved session (%.1f h old)", age_h)
                return await browser.new_context(
                    storage_state=str(MUSAFFA_SESSION_FILE),
                    user_agent=_USER_AGENT,
                )

        # Fresh context — attempt login if credentials are configured
        context = await browser.new_context(user_agent=_USER_AGENT)
        if MUSAFFA_EMAIL and MUSAFFA_PASSWORD:
            ok = await self._login(context)
            if ok:
                await context.storage_state(path=str(MUSAFFA_SESSION_FILE))
        else:
            logger.debug("Musaffa: no credentials configured, skipping login")
        return context

    # ------------------------------------------------------------------
    # DOM-based compliance chip parser
    # ------------------------------------------------------------------

    async def _parse_chip(self, page) -> ComplianceStatus | None:
        """Query the compliance chip element directly.

        Returns the status if an unlocked chip is found, or None if the chip
        is locked / absent (caller should fall back to text parsing).
        """
        # If the current-status chip is locked, don't pick up historical chips
        locked = await page.query_selector('div.compliance-chip.locked-chip')
        if locked:
            return None

        el = await page.query_selector(
            'div.compliance-chip:not(.locked-chip) h5.status-text'
        )
        if not el:
            return None
        text = (await el.inner_text()).strip().lower()
        mapping = {
            'halal': ComplianceStatus.HALAL,
            'not halal': ComplianceStatus.NOT_HALAL,
            'doubtful': ComplianceStatus.DOUBTFUL,
            'not covered': ComplianceStatus.NOT_COVERED,
        }
        status = mapping.get(text)
        if status is not None:
            logger.info(f"Chip status '{text}' found via DOM")
        else:
            logger.debug(f"Unrecognised chip text: '{text}'")
        return status

    # ------------------------------------------------------------------
    # Page fetching
    # ------------------------------------------------------------------

    async def _fetch_single_page(
        self, context: BrowserContext, ticker: str
    ) -> ScreeningResult:
        """Fetch and parse a single ticker page using an existing context."""
        ticker = ticker.upper().strip()
        quote_type = await get_quote_type(ticker)
        if quote_type == "ETF":
            url = f"{MUSAFFA_ETF_BASE_URL}/{ticker}/"
        else:
            url = f"{self.base_url}/{ticker}/"

        page = await context.new_page()
        try:
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

            # Try DOM chip first (works when authenticated for ETFs)
            chip_status = await self._parse_chip(page)
            if chip_status is not None:
                return ScreeningResult(
                    ticker=ticker,
                    status=chip_status,
                    source="musaffa",
                )

            # Fall back to text extraction
            content = await page.content()
            text_content = await page.evaluate("() => document.body.innerText")
            return self._parse_content(ticker, content, text_content)

        finally:
            await page.close()

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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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
                        context = await self._get_context(browser)
                        try:
                            return await self._fetch_single_page(context, ticker)
                        finally:
                            await context.close()
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
        """Screen multiple tickers in parallel, sharing one authenticated context."""
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
                # One login / session for the entire batch
                context = await self._get_context(browser)
                try:
                    semaphore = asyncio.Semaphore(MAX_CONCURRENT_PAGES)

                    async def fetch_with_semaphore(ticker: str) -> tuple[str, ScreeningResult]:
                        async with semaphore:
                            for attempt in range(MAX_RETRIES):
                                try:
                                    result = await self._fetch_single_page(context, ticker)
                                    return (ticker, result)
                                except Exception as e:
                                    logger.warning(
                                        f"Error screening {ticker} (attempt {attempt + 1}): {e}"
                                    )
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
                    await context.close()

            finally:
                await browser.close()

        return [results[t] for t in tickers]
