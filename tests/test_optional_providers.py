"""Contract tests for optional keyed free-tier providers."""

import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import scrapers.halal_terminal as halal_terminal_module
from scrapers import AssetType, ComplianceStatus, Security
from scrapers.halal_terminal import HalalTerminalProvider


def test_halal_terminal_current_stock_schema(monkeypatch):
    monkeypatch.setattr(halal_terminal_module, "HALAL_TERMINAL_API_KEY", "free-key")

    async def run():
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "symbol": "AAPL",
                    "name": "Apple Inc.",
                    "is_compliant": True,
                    "shariah_compliance_status": "COMPLIANT",
                },
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            return await HalalTerminalProvider()._fetch_single(
                client, Security("AAPL", asset_type=AssetType.STOCK)
            )

    result = asyncio.run(run())
    assert result.status == ComplianceStatus.HALAL


def test_halal_terminal_uses_current_etf_route(monkeypatch):
    monkeypatch.setattr(halal_terminal_module, "HALAL_TERMINAL_API_KEY", "free-key")
    requested_path = None

    def handler(request):
        nonlocal requested_path
        requested_path = request.url.path
        assert request.method == "GET"
        return httpx.Response(200, json={"status": "COMPLIANT"}, request=request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await HalalTerminalProvider()._fetch_single(
                client, Security("SPUS", asset_type=AssetType.ETF)
            )

    asyncio.run(run())
    assert requested_path == "/api/etf/SPUS/screening"
