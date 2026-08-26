"""Optional HalalScreener free-key API provider."""

import httpx

from config import HALAL_SCREENER_API_KEY, HALAL_SCREENER_BASE_URL
from .base import (
    AssetType,
    BaseScraper,
    ComplianceStatus,
    ResultState,
    ScreeningResult,
    Security,
)


class HalalScreenerProvider(BaseScraper):
    supported_asset_types = frozenset(
        {AssetType.STOCK, AssetType.ETF, AssetType.UNKNOWN}
    )

    @property
    def source_name(self) -> str:
        return "halal_screener"

    async def _fetch_single(
        self, client: httpx.AsyncClient, security: Security
    ) -> ScreeningResult:
        if not HALAL_SCREENER_API_KEY:
            return self.failure(
                security,
                ResultState.AUTH_REQUIRED,
                "Set HALAL_SCREENER_API_KEY to enable",
            )
        url = f"{HALAL_SCREENER_BASE_URL}/screen"
        response = await self.request(
            client,
            "GET",
            url,
            params={"symbol": security.symbol},
            headers={"Authorization": f"Bearer {HALAL_SCREENER_API_KEY}"},
        )
        if response.status_code >= 400:
            return self.http_failure(security, response, str(response.url))
        try:
            data = response.json()
            label = (
                str(data.get("status") or data.get("verdict")).upper().replace(" ", "_")
            )
            status = {
                "HALAL": ComplianceStatus.HALAL,
                "COMPLIANT": ComplianceStatus.HALAL,
                "NOT_HALAL": ComplianceStatus.NOT_HALAL,
                "NON_COMPLIANT": ComplianceStatus.NOT_HALAL,
                "DOUBTFUL": ComplianceStatus.DOUBTFUL,
                "QUESTIONABLE": ComplianceStatus.DOUBTFUL,
            }[label]
        except (ValueError, KeyError, TypeError):
            return self.failure(
                security,
                ResultState.PARSE_ERROR,
                "HalalScreener returned an unknown schema",
                url=url,
            )
        return ScreeningResult(
            ticker=security.symbol,
            status=status,
            source=self.source_name,
            company_name=data.get("name") or data.get("company_name"),
            asset_type=security.asset_type,
            url=url,
            evidence=str(data.get("evidence") or data.get("reason") or label),
            methodology="AAOIFI",
        )
