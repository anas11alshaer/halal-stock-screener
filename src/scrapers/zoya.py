"""Zoya.finance scraper for Halal stock screening."""

import json
import logging
import re

import httpx

from config import ZOYA_BASE_URL
from .base import BaseScraper, ComplianceStatus, ScreeningResult, get_quote_type

logger = logging.getLogger(__name__)


class ZoyaScraper(BaseScraper):
    """Scraper for Zoya.finance stock screening data."""

    @property
    def source_name(self) -> str:
        return "zoya"

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

        if response.status_code >= 400:
            logger.warning(f"HTTP {response.status_code} for {url}")
            return ScreeningResult(
                ticker=ticker,
                status=ComplianceStatus.ERROR,
                source="zoya",
                error_message=f"HTTP {response.status_code}",
            )

        return self._parse_content(ticker, response.text)

    def _parse_content(self, ticker: str, page_html: str) -> ScreeningResult:
        """Parse page content to extract compliance info.

        Prefer the main H2 verdict. Zoya FAQ JSON-LD embeds contradictory
        A/B template answers, so it is only a fallback.
        """
        ticker = ticker.upper()

        if "page not found" in page_html.lower() or "<title>404" in page_html.lower():
            return ScreeningResult(
                ticker=ticker,
                status=ComplianceStatus.NOT_COVERED,
                source="zoya",
                error_message="Stock not found on Zoya",
            )

        # Strategy 1: main H2 heading (authoritative visible verdict)
        # e.g. "AAPL stock is Shariah-compliant"
        #      "JPM stock is not Shariah-compliant"
        #      "MSFT stock is questionable"
        page_lower = page_html.lower()
        h2_match = re.search(
            rf'{ticker.lower()}\s+stock\s+is\s+'
            rf'(not\s+)?(?:<[^>]+>\s*)*(shariah-compliant|questionable|doubtful)',
            page_lower,
        )
        if h2_match:
            if h2_match.group(2) in ("questionable", "doubtful"):
                status = ComplianceStatus.DOUBTFUL
                logger.info(f"{ticker}: DOUBTFUL (zoya, h2)")
            elif h2_match.group(1):  # "not" was captured
                status = ComplianceStatus.NOT_HALAL
                logger.info(f"{ticker}: NOT_HALAL (zoya, h2)")
            else:
                status = ComplianceStatus.HALAL
                logger.info(f"{ticker}: HALAL (zoya, h2)")
            return ScreeningResult(ticker=ticker, status=status, source="zoya")

        # Strategy 2: JSON-LD FAQ (noisy — may contain template variants)
        status = self._parse_jsonld(ticker, page_html)
        if status is not None:
            return ScreeningResult(ticker=ticker, status=status, source="zoya")

        logger.warning(f"{ticker}: Could not determine status (zoya)")
        return ScreeningResult(
            ticker=ticker,
            status=ComplianceStatus.NOT_COVERED,
            source="zoya",
        )

    def _parse_jsonld(self, ticker: str, page_html: str) -> ComplianceStatus | None:
        """Extract compliance status from JSON-LD FAQPage data.

        Only accepts an answer when it yields a single unambiguous verdict
        across entities that mention compliance.
        """
        verdicts: set[ComplianceStatus] = set()

        for match in re.finditer(
            r'<script\s+type="application/ld\+json"[^>]*>(.*?)</script>',
            page_html,
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
                if not answer_text:
                    continue
                if "questionable" in answer_text or "flagged as" in answer_text:
                    # "flagged as" templates are ambiguous; skip
                    if "questionable" in answer_text:
                        verdicts.add(ComplianceStatus.DOUBTFUL)
                    continue
                if "not shariah-compliant" in answer_text:
                    verdicts.add(ComplianceStatus.NOT_HALAL)
                elif "shariah-compliant" in answer_text:
                    verdicts.add(ComplianceStatus.HALAL)

        if len(verdicts) == 1:
            status = next(iter(verdicts))
            logger.info(f"{ticker}: {status.value} (zoya, json-ld)")
            return status

        if len(verdicts) > 1:
            logger.warning(
                f"{ticker}: Ambiguous JSON-LD verdicts {verdicts} (zoya); ignoring"
            )
        return None
