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

        Musaffa meta descriptions are SEO questions ("Is X halal?") and no longer
        contain the verdict. Prefer the SSR status chip / FAQ "classified as …"
        phrasing in the page body.
        """
        ticker = ticker.upper()
        page_lower = page_html.lower()

        if "page not found" in page_lower or "does not exist" in page_lower:
            return ScreeningResult(
                ticker=ticker,
                status=ComplianceStatus.NOT_COVERED,
                source="musaffa",
                error_message="Stock not found on Musaffa",
            )

        company_name = self._extract_company_name(ticker, page_html)
        status = self._extract_status(ticker, page_html)

        return ScreeningResult(
            ticker=ticker,
            status=status,
            source="musaffa",
            company_name=company_name,
        )

    def _extract_company_name(self, ticker: str, page_html: str) -> str | None:
        """Best-effort company/ETF name from meta or title."""
        meta_match = re.search(
            r'<meta\s+name="description"\s+content="([^"]*)"',
            page_html,
            re.IGNORECASE,
        )
        if meta_match:
            meta_raw = html.unescape(meta_match.group(1))
            # Current SEO form: "Is Apple Inc halal? Check the Shariah…"
            name_match = re.search(
                rf'^Is\s+(.+?)\s+halal\?',
                meta_raw,
                re.IGNORECASE,
            )
            if name_match:
                return name_match.group(1).strip()

            # Legacy form: "Company - TICKER is considered…"
            legacy = re.search(
                rf'(.+?)\s*-\s*{ticker}\s+is\s+considered',
                meta_raw,
                re.IGNORECASE,
            )
            if legacy:
                raw_name = legacy.group(1).strip()
                raw_name = re.sub(r'^last updated:.*?\.\s*', '', raw_name, flags=re.I)
                raw_name = re.sub(r'^as of.*?,\s*', '', raw_name, flags=re.I)
                return raw_name.strip().title()

        title_match = re.search(r'<title>([^<]+)</title>', page_html, re.IGNORECASE)
        if title_match:
            title = html.unescape(title_match.group(1))
            # "Is Apple Inc Halal? AAPL Shariah Compliance Analysis"
            m = re.search(rf'^Is\s+(.+?)\s+Halal\?', title, re.IGNORECASE)
            if m:
                return m.group(1).strip()

        return None

    def _extract_status(self, ticker: str, page_html: str) -> ComplianceStatus:
        """Extract compliance verdict from page body (not SEO meta)."""
        # 1) Visible status chip: <div class="… status-text">HALAL</div>
        chip = re.search(
            r'class="[^"]*status-text[^"]*"[^>]*>\s*(NOT\s*HALAL|DOUBTFUL|HALAL)\s*<',
            page_html,
            re.IGNORECASE,
        )
        if chip:
            return self._status_from_label(ticker, chip.group(1), "status-text")

        # 2) FAQ / SSR copy: "classified as not halal" / "classified as halal"
        # Check not-halal / doubtful before bare halal.
        classified = re.search(
            r'classified as\s+(?:<[^>]+>\s*)*(not\s+halal|doubtful|halal)',
            page_html,
            re.IGNORECASE,
        )
        if classified:
            return self._status_from_label(ticker, classified.group(1), "classified-as")

        # 3) Legacy meta description (verdict embedded) — last resort
        meta_match = re.search(
            r'<meta\s+name="description"\s+content="([^"]*)"',
            page_html,
            re.IGNORECASE,
        )
        if meta_match:
            meta = html.unescape(meta_match.group(1)).lower()
            # Ignore SEO questions like "is x halal?" — they always contain "halal"
            if re.search(r'\bis\s+.+\s+halal\?', meta) and "classified as" not in meta:
                logger.warning(
                    f"{ticker}: Meta is SEO-only; no verdict found in body (musaffa)"
                )
            elif "not halal" in meta or "not shariah compliant" in meta:
                return self._status_from_label(ticker, "nothalal", "meta")
            elif "doubtful" in meta:
                return self._status_from_label(ticker, "doubtful", "meta")
            elif re.search(r'\bis considered (?:halal|shariah compliant)', meta):
                return self._status_from_label(ticker, "halal", "meta")

        logger.warning(f"{ticker}: Could not determine status (musaffa)")
        return ComplianceStatus.NOT_COVERED

    def _status_from_label(
        self, ticker: str, label: str, via: str
    ) -> ComplianceStatus:
        normalized = re.sub(r'[\s_]+', '', label.strip().lower())
        if normalized == "nothalal":
            status = ComplianceStatus.NOT_HALAL
        elif normalized == "doubtful":
            status = ComplianceStatus.DOUBTFUL
        elif normalized == "halal":
            status = ComplianceStatus.HALAL
        else:
            logger.warning(f"{ticker}: Unknown status label {label!r} (musaffa/{via})")
            return ComplianceStatus.NOT_COVERED

        logger.info(f"{ticker}: {status.value} (musaffa, {via})")
        return status
