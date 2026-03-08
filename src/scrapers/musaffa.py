"""Musaffa.com scraper for Halal stock screening."""

import html
import logging
import re

import httpx

from config import MUSAFFA_BASE_URL
from .base import BaseScraper, ComplianceStatus, ScreeningResult, get_quote_type

MUSAFFA_ETF_BASE_URL = "https://musaffa.com/etf"

logger = logging.getLogger(__name__)


class MusaffaScraper(BaseScraper):
    """Scraper for Musaffa.com stock screening data."""

    @property
    def source_name(self) -> str:
        return "musaffa"

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

        if response.status_code >= 400:
            logger.warning(f"HTTP {response.status_code} for {url}")
            return ScreeningResult(
                ticker=ticker,
                status=ComplianceStatus.ERROR,
                source="musaffa",
                error_message=f"HTTP {response.status_code}",
            )

        return self._parse_content(ticker, response.text)

    def _parse_content(self, ticker: str, page_html: str) -> ScreeningResult:
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
            page_html,
            re.IGNORECASE,
        )
        if meta_match:
            meta_raw = html.unescape(meta_match.group(1))
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
                raw_name = re.sub(r'^last updated:.*?\.\s*', '', raw_name)
                # Remove "as of ... report, " prefix
                raw_name = re.sub(r'^as of.*?,\s*', '', raw_name)
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
            page_lower = page_html.lower()
            if "page not found" in page_lower or "does not exist" in page_lower:
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
