"""Zoya public stock-page screening provider."""

import html
import re

import httpx

from config import ZOYA_BASE_URL

from .base import (
    AssetType,
    BaseScraper,
    ComplianceStatus,
    ResultState,
    ScreeningResult,
    Security,
)


class ZoyaScraper(BaseScraper):
    """Parse the authoritative visible verdict heading on public pages."""

    supported_asset_types = frozenset({AssetType.STOCK, AssetType.ETF, AssetType.UNKNOWN})

    @property
    def source_name(self) -> str:
        return "zoya"

    async def _fetch_single(self, client: httpx.AsyncClient, security: Security) -> ScreeningResult:
        slug = security.symbol.lower().replace(".", "-")
        url = f"{ZOYA_BASE_URL}/{slug}"
        response = await self.request(client, "GET", url)
        if response.status_code >= 400:
            return self.http_failure(security, response, url)
        return self._parse_content(security, response.text, url)

    def _parse_content(self, security: Security, page_html: str, url: str) -> ScreeningResult:
        if "page not found" in page_html.lower() or "<title>404" in page_html.lower():
            return self.failure(
                security,
                ResultState.NOT_COVERED,
                "Security not found on Zoya",
                url=url,
                status=ComplianceStatus.NOT_COVERED,
            )

        ticker = security.symbol.upper()
        heading_match = re.search(r"<h2\b[^>]*>(.*?)</h2>", page_html, re.IGNORECASE | re.DOTALL)
        if not heading_match:
            failed = self.failure(
                security,
                ResultState.PARSE_ERROR,
                "Zoya verdict heading not found",
                url=url,
            )
            failed.review_text = self._visible_text(page_html)[:5000]
            return failed
        heading = re.sub(r"<[^>]+>", " ", heading_match.group(1))
        heading = re.sub(r"\s+", " ", html.unescape(heading)).strip()
        verdict = re.search(
            rf"\b{re.escape(ticker)}\s+(?:stock|etf|fund)\s+is\s+"
            r"(not\s+Shariah-compliant|Shariah-compliant|questionable|doubtful)\b",
            heading,
            re.IGNORECASE,
        )
        if not verdict:
            # Fallback: JSON-LD FAQPage or visible text may repeat the verdict.
            faq_match = re.search(
                r'"FAQPage".*?"text"\s*:\s*"([^"]*?' + re.escape(ticker) + r'[^"]*)"',
                page_html,
                re.IGNORECASE | re.DOTALL,
            )
            if faq_match:
                faq_text = re.sub(r"\s+", " ", html.unescape(faq_match.group(1))).strip()
                verdict = re.search(
                    rf"\b{re.escape(ticker)}\s+(?:stock|etf|fund)\s+is\s+"
                    r"(not\s+Shariah-compliant|Shariah-compliant|questionable|doubtful)\b",
                    faq_text,
                    re.IGNORECASE,
                )
                if verdict:
                    heading = faq_text
            if not verdict:
                visible = self._visible_text(page_html)
                verdict = re.search(
                    rf"\b{re.escape(ticker)}\s+(?:stock|etf|fund)\s+is\s+"
                    r"(not\s+Shariah-compliant|Shariah-compliant|questionable|doubtful)\b",
                    visible,
                    re.IGNORECASE,
                )
                if verdict:
                    heading = verdict.group(0)
        if not verdict:
            state = (
                ResultState.IDENTITY_MISMATCH
                if ticker.lower() not in heading.lower()
                else ResultState.PARSE_ERROR
            )
            failed = self.failure(
                security,
                state,
                "Zoya heading does not contain a recognized verdict",
                url=url,
            )
            if state == ResultState.PARSE_ERROR:
                failed.review_text = heading
            return failed

        label = verdict.group(1).lower()
        if label.startswith("not"):
            status = ComplianceStatus.NOT_HALAL
        elif label in {"questionable", "doubtful"}:
            status = ComplianceStatus.DOUBTFUL
        else:
            status = ComplianceStatus.HALAL
        asset_type = (
            security.asset_type
            if security.asset_type in {AssetType.STOCK, AssetType.ETF}
            else AssetType.STOCK
        )
        quote_type = "ETF" if asset_type == AssetType.ETF else "EQUITY"
        return ScreeningResult(
            ticker=ticker,
            status=status,
            source=self.source_name,
            quote_type=quote_type,
            asset_type=asset_type,
            url=url,
            evidence=heading,
            methodology="AAOIFI",
        )

    @staticmethod
    def _visible_text(page_html: str) -> str:
        text = re.sub(
            r"<(script|style)\b[^>]*>.*?</\1>", " ", page_html, flags=re.IGNORECASE | re.DOTALL
        )
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", html.unescape(text)).strip()
