"""Musaffa.com scraper for Halal stock screening using Playwright."""

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Optional
from enum import Enum

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from config import MUSAFFA_BASE_URL, REQUEST_TIMEOUT, MAX_RETRIES

logger = logging.getLogger(__name__)


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
    compliance_ranking: Optional[str] = None
    company_name: Optional[str] = None
    details: Optional[str] = None
    error_message: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "status": self.status.value,
            "compliance_ranking": self.compliance_ranking,
            "company_name": self.company_name,
            "details": self.details,
            "error_message": self.error_message
        }


class MusaffaScraper:
    """Scraper for Musaffa.com stock screening data using Playwright."""

    def __init__(self):
        self.base_url = MUSAFFA_BASE_URL

    async def _fetch_with_playwright(self, url: str, ticker: str) -> ScreeningResult:
        """Fetch and parse page using Playwright for JS rendering."""
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
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

                # Wait for the compliance status to appear (more reliable than networkidle)
                try:
                    await page.wait_for_selector("text=Shariah Compliance", timeout=10000)
                except PlaywrightTimeout:
                    # Content might still be there, continue anyway
                    logger.debug(f"Selector timeout for {ticker}, continuing with available content")

                # Small delay for any remaining dynamic content
                await asyncio.sleep(1)

                # Get page content
                content = await page.content()
                text_content = await page.evaluate("() => document.body.innerText")

                return self._parse_content(ticker, content, text_content)

            finally:
                await browser.close()

    def _parse_content(self, ticker: str, html: str, text: str) -> ScreeningResult:
        """Parse the page content to extract compliance info."""
        ticker = ticker.upper()
        text_lower = text.lower()

        # Debug: log first 500 chars of extracted text
        logger.debug(f"Extracted text for {ticker}: {text[:500]}")

        status = ComplianceStatus.NOT_COVERED
        compliance_ranking = None
        company_name = None
        details = None

        # First check if the page has the compliance section - this means it's a valid stock page
        has_compliance_section = "shariah compliance" in text_lower

        # Check for "not found" or "no data" ONLY if there's no compliance section
        # (The phrase "not found" can appear in other parts of valid pages)
        if not has_compliance_section:
            if "page not found" in text_lower or "does not exist" in text_lower or "404" in text_lower:
                return ScreeningResult(
                    ticker=ticker,
                    status=ComplianceStatus.NOT_COVERED,
                    error_message="Stock not found on Musaffa"
                )

        # Find the compliance section - look for text near "Shariah Compliance"
        # Musaffa shows status like: "AAPL Shariah Compliance ... HALAL" or "NOT HALAL"
        compliance_section = ""
        if "shariah compliance" in text_lower:
            # Get ~200 chars around the compliance section
            idx = text_lower.find("shariah compliance")
            start = max(0, idx - 50)
            end = min(len(text), idx + 200)
            compliance_section = text_lower[start:end]
            logger.debug(f"{ticker} compliance section: {compliance_section}")

        # Check the compliance section for status (order matters!)
        # Look for NOT HALAL first (most specific)
        if "not halal" in compliance_section or "non-halal" in compliance_section:
            status = ComplianceStatus.NOT_HALAL
            logger.info(f"{ticker}: Found NOT_HALAL in compliance section")
        # Then check for DOUBTFUL
        elif "doubtful" in compliance_section:
            status = ComplianceStatus.DOUBTFUL
            logger.info(f"{ticker}: Found DOUBTFUL in compliance section")
        # Then check for HALAL (after checking NOT HALAL)
        elif "halal" in compliance_section:
            status = ComplianceStatus.HALAL
            logger.info(f"{ticker}: Found HALAL in compliance section")
        # Fallback: check the whole page for explicit status badges
        elif re.search(r'\bhalal\b', text_lower) and not re.search(r'\bnot\s+halal\b', text_lower):
            status = ComplianceStatus.HALAL
            logger.info(f"{ticker}: Found HALAL as standalone word")
        elif re.search(r'\bnot\s+halal\b', text_lower):
            status = ComplianceStatus.NOT_HALAL
            logger.info(f"{ticker}: Found NOT HALAL in page")

        # If still NOT_COVERED, log the text for debugging
        if status == ComplianceStatus.NOT_COVERED:
            logger.warning(f"{ticker}: Could not determine status. Text sample: {text[:300]}")

        # Try to extract compliance ranking (look for patterns like "Rank 1-5" or star ratings)
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
        # Musaffa shows "Purification of Apple Inc" or "Is Apple Inc - AAPL Stock Halal"
        name_patterns = [
            r'Purification of ([A-Z][A-Za-z\s&.,]+(?:Inc|Corp|Ltd|LLC|Company|Co))',
            r'Is ([A-Z][A-Za-z\s&.,]+(?:Inc|Corp|Ltd|LLC|Company|Co))\s*[-–]',
            rf'([A-Z][A-Za-z\s&.,]+(?:Inc|Corp|Ltd|LLC|Company|Co))\s*[-–]\s*{ticker}',
        ]
        for pattern in name_patterns:
            match = re.search(pattern, text)
            if match:
                company_name = match.group(1).strip()
                # Clean up any trailing punctuation
                company_name = re.sub(r'[\s,.\-–]+$', '', company_name)
                if len(company_name) > 3:  # Avoid garbage matches
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
        url = f"{self.base_url}/{ticker}/"

        logger.info(f"Screening ticker: {ticker}")

        for attempt in range(MAX_RETRIES):
            try:
                result = await self._fetch_with_playwright(url, ticker)
                return result
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
        """Screen multiple tickers sequentially (to avoid browser resource issues)."""
        results = []
        for ticker in tickers:
            result = await self.screen_ticker(ticker)
            results.append(result)
            # Small delay between requests
            if len(tickers) > 1:
                await asyncio.sleep(1)
        return results
