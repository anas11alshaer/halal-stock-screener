"""Optional Halal Terminal free-key API provider."""

import httpx

from config import HALAL_TERMINAL_API_KEY, HALAL_TERMINAL_BASE_URL
from .base import (
    AssetType,
    BaseScraper,
    ComplianceStatus,
    ResultState,
    ScreeningResult,
    Security,
)


class HalalTerminalProvider(BaseScraper):
    supported_asset_types = frozenset(
        {AssetType.STOCK, AssetType.ETF, AssetType.UNKNOWN}
    )

    @property
    def source_name(self) -> str:
        return "halal_terminal"

    async def _fetch_single(
        self, client: httpx.AsyncClient, security: Security
    ) -> ScreeningResult:
        if not HALAL_TERMINAL_API_KEY:
            return self.failure(
                security,
                ResultState.AUTH_REQUIRED,
                "Set HALAL_TERMINAL_API_KEY to enable",
            )
        if security.asset_type == AssetType.ETF:
            url = f"{HALAL_TERMINAL_BASE_URL}/api/etf/{security.symbol}/screening"
            method = "GET"
        else:
            url = f"{HALAL_TERMINAL_BASE_URL}/api/screen/{security.symbol}"
            method = "GET"
        response = await self.request(
            client, method, url, headers={"X-API-Key": HALAL_TERMINAL_API_KEY}
        )
        if response.status_code >= 400:
            return self.http_failure(security, response, url)
        try:
            data = response.json()
            label = str(
                data.get("overall_status")
                or data.get("compliance_status")
                or data.get("shariah_compliance_status")
                or data.get("status")
            ).upper()
            if label in {"NONE", ""} and "is_compliant" in data:
                label = "COMPLIANT" if data["is_compliant"] else "NON_COMPLIANT"
            status = {
                "COMPLIANT": ComplianceStatus.HALAL,
                "NON_COMPLIANT": ComplianceStatus.NOT_HALAL,
                "QUESTIONABLE": ComplianceStatus.DOUBTFUL,
            }[label]
        except (ValueError, KeyError, TypeError):
            return self.failure(
                security,
                ResultState.PARSE_ERROR,
                "Halal Terminal returned an unknown schema",
                url=url,
            )
        return ScreeningResult(
            ticker=security.symbol,
            status=status,
            source=self.source_name,
            company_name=data.get("name"),
            asset_type=security.asset_type,
            url=url,
            evidence=str(data.get("compliance_explanation") or label),
            methodology="AAOIFI, DJIM, FTSE, MSCI, S&P",
        )
