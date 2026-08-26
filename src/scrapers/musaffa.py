"""Musaffa public-page screening provider."""

import html
import logging
import re

import httpx

from config import MUSAFFA_BASE_URL
from .base import (
    AssetType,
    BaseScraper,
    ComplianceStatus,
    ResultState,
    ScreeningResult,
    Security,
)

MUSAFFA_ETF_BASE_URL = "https://musaffa.com/etf"
logger = logging.getLogger(__name__)


class MusaffaScraper(BaseScraper):
    """Screen stocks and ETFs through Musaffa's public detail pages."""

    supported_asset_types = frozenset(
        {AssetType.STOCK, AssetType.ETF, AssetType.UNKNOWN}
    )

    @property
    def source_name(self) -> str:
        return "musaffa"

    async def _fetch_single(
        self, client: httpx.AsyncClient, security: Security
    ) -> ScreeningResult:
        routes = self._routes(security)
        last_result: ScreeningResult | None = None
        for route_type, url in routes:
            response = await self.request(client, "GET", url)
            if response.status_code >= 400:
                last_result = self.http_failure(security, response, url)
                if response.status_code == 404:
                    continue
                return last_result

            result = self._parse_content(security, response.text, url, route_type)
            if result.state == ResultState.SUCCESS:
                return result
            last_result = result
            # A valid but mismatched route may be recovered by the alternate route once.
            if result.state not in {
                ResultState.PARSE_ERROR,
                ResultState.IDENTITY_MISMATCH,
            }:
                return result

        return last_result or self.failure(
            security,
            ResultState.NOT_COVERED,
            "Security not found",
            status=ComplianceStatus.NOT_COVERED,
        )

    @staticmethod
    def _routes(security: Security) -> list[tuple[AssetType, str]]:
        ticker = security.symbol.upper()
        stock = (AssetType.STOCK, f"{MUSAFFA_BASE_URL}/{ticker}/")
        etf = (AssetType.ETF, f"{MUSAFFA_ETF_BASE_URL}/{ticker}/")
        if security.asset_type == AssetType.ETF:
            return [etf, stock]
        if security.asset_type == AssetType.STOCK:
            return [stock, etf]
        return [stock, etf]

    def _parse_content(
        self,
        security: Security,
        page_html: str,
        url: str,
        route_type: AssetType,
    ) -> ScreeningResult:
        ticker = security.symbol.upper()
        page_lower = page_html.lower()
        if "page not found" in page_lower or "does not exist" in page_lower:
            return self.failure(
                security,
                ResultState.NOT_COVERED,
                "Security not found on Musaffa",
                url=url,
                status=ComplianceStatus.NOT_COVERED,
            )

        visible = self._visible_text(page_html)
        if not re.search(rf"\b{re.escape(ticker)}\b", visible, re.IGNORECASE):
            return self.failure(
                security,
                ResultState.IDENTITY_MISMATCH,
                "Musaffa page does not identify the requested ticker",
                url=url,
            )

        section = re.search(
            rf">\s*{re.escape(ticker)}\s+Shariah\s+Compliance\s*</h2>(.{{0,3000}})",
            page_html,
            re.IGNORECASE | re.DOTALL,
        )
        if not section:
            failed = self.failure(
                security,
                ResultState.PARSE_ERROR,
                "Valid Musaffa page has no compliance section",
                url=url,
            )
            failed.review_text = visible[:5000]
            return failed

        verdict = re.search(
            r'class="[^"]*status-text[^"]*"[^>]*>\s*'
            r"(NOT\s+HALAL|DOUBTFUL|HALAL)\s*<",
            section.group(1),
            re.IGNORECASE,
        )
        if not verdict:
            section_text = self._visible_text(section.group(1))
            verdict = re.search(
                r"\bclassified as\s+(NOT\s+HALAL|DOUBTFUL|HALAL)\b",
                section_text,
                re.IGNORECASE,
            )
        if not verdict:
            failed = self.failure(
                security,
                ResultState.PARSE_ERROR,
                "Valid Musaffa compliance section has no recognizable verdict",
                url=url,
            )
            failed.review_text = self._visible_text(section.group(1))[:5000]
            return failed

        label = re.sub(r"\s+", " ", verdict.group(1).upper()).strip()
        status = {
            "HALAL": ComplianceStatus.HALAL,
            "NOT HALAL": ComplianceStatus.NOT_HALAL,
            "DOUBTFUL": ComplianceStatus.DOUBTFUL,
        }[label]
        evidence = f"{ticker} Shariah Compliance - {label}"
        return ScreeningResult(
            ticker=ticker,
            status=status,
            source=self.source_name,
            company_name=self._extract_company_name(ticker, page_html),
            quote_type="ETF" if route_type == AssetType.ETF else "EQUITY",
            asset_type=route_type,
            url=url,
            evidence=evidence,
            methodology="AAOIFI",
        )

    @staticmethod
    def _visible_text(page_html: str) -> str:
        without_scripts = re.sub(
            r"<(script|style)\b[^>]*>.*?</\1>", " ", page_html, flags=re.I | re.S
        )
        text = re.sub(r"<[^>]+>", " ", without_scripts)
        return re.sub(r"\s+", " ", html.unescape(text)).strip()

    @staticmethod
    def _extract_company_name(ticker: str, page_html: str) -> str | None:
        meta_match = re.search(
            r'<meta\s+name="description"\s+content="([^"]*)"',
            page_html,
            re.IGNORECASE,
        )
        if meta_match:
            meta_raw = html.unescape(meta_match.group(1))
            name_match = re.search(r"^Is\s+(.+?)\s+halal\?", meta_raw, re.I)
            if name_match:
                return name_match.group(1).strip()
        title_match = re.search(r"<title>([^<]+)</title>", page_html, re.I)
        if title_match:
            title = html.unescape(title_match.group(1))
            name_match = re.search(r"^Is\s+(.+?)\s+Halal\?", title, re.I)
            if name_match:
                return name_match.group(1).strip()
        return None
