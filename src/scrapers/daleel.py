"""Daleel REST screening provider for US stocks and ETFs."""

import json

import httpx

from config import DALEEL_API_KEY, DALEEL_BASE_URL

from .base import (
    AssetType,
    BaseScraper,
    ComplianceStatus,
    ResultState,
    ScreeningResult,
    Security,
)


class DaleelProvider(BaseScraper):
    supported_asset_types = frozenset({AssetType.STOCK, AssetType.ETF, AssetType.UNKNOWN})

    @property
    def source_name(self) -> str:
        return "daleel"

    async def _fetch_single(self, client: httpx.AsyncClient, security: Security) -> ScreeningResult:
        url = f"{DALEEL_BASE_URL}/v1/screen/{security.symbol.upper()}"
        headers = {"X-API-Key": DALEEL_API_KEY} if DALEEL_API_KEY else None
        response = await self.request(client, "GET", url, headers=headers)
        if response.status_code == 400:
            return self.failure(
                security,
                ResultState.NOT_COVERED,
                "Daleel does not accept this ticker format",
                url=url,
                status=ComplianceStatus.NOT_COVERED,
            )
        if response.status_code >= 400:
            return self.http_failure(security, response, url)
        try:
            payload = response.json()
            data = payload["data"]
            status = {
                "compliant": ComplianceStatus.HALAL,
                "questionable": ComplianceStatus.DOUBTFUL,
                "non_compliant": ComplianceStatus.NOT_HALAL,
                "not_screenable": ComplianceStatus.NOT_COVERED,
            }[data["compliance"]]
        except (ValueError, KeyError, TypeError):
            return self.failure(
                security,
                ResultState.PARSE_ERROR,
                "Daleel returned an unknown schema",
                url=url,
            )

        security_type = str(data.get("security_type", "")).lower()
        asset_type = AssetType.ETF if security_type == "etf" else AssetType.STOCK
        if security.asset_type not in {AssetType.UNKNOWN, asset_type}:
            return self.failure(
                security,
                ResultState.IDENTITY_MISMATCH,
                "Daleel returned a different security type",
                url=url,
            )
        if str(data.get("symbol", "")).upper() != security.symbol.upper():
            return self.failure(
                security,
                ResultState.IDENTITY_MISMATCH,
                "Daleel returned a different ticker",
                url=url,
            )

        coverage = data.get("etf_look_through", {}).get("coverage_pct_of_fund")
        if coverage is not None:
            evidence = f"Holdings look-through; {coverage:g}% of fund screened"
        else:
            evidence_items = data.get("evidence") or []
            concepts = [item.get("concept") for item in evidence_items[:3] if item.get("concept")]
            evidence = (
                "SEC filing evidence: " + ", ".join(concepts)
                if concepts
                else "Daleel screening result"
            )
        details = json.dumps(
            {
                "score": data.get("score"),
                "approximations": data.get("approximations", []),
                "coverage_pct": coverage,
            },
            separators=(",", ":"),
        )
        state = ResultState.SUCCESS
        if status == ComplianceStatus.NOT_COVERED:
            state = ResultState.NOT_COVERED
        return ScreeningResult(
            ticker=security.symbol.upper(),
            status=status,
            source=self.source_name,
            company_name=data.get("company_name"),
            quote_type="ETF" if asset_type == AssetType.ETF else "EQUITY",
            asset_type=asset_type,
            state=state,
            url=url,
            evidence=evidence,
            details=details,
            methodology="AAOIFI, DJIM, MSCI, S&P, FTSE (calculation; no Sharia board)",
        )
