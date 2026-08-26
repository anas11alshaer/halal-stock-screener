"""Deterministic contract tests for provider plugins and voting."""

import asyncio
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from plugins import load_screening_providers
from resolver import resolve_compliance
from scrapers import (
    AssetType,
    ComplianceStatus,
    ResultState,
    ScreeningResult,
    Security,
)
from scrapers.daleel import DaleelProvider
from scrapers.musaffa import MusaffaScraper
from scrapers.zoya import ZoyaScraper


def result(source: str, status: ComplianceStatus) -> ScreeningResult:
    return ScreeningResult(ticker="TEST", status=status, source=source)


def test_configured_plugins_are_replaceable_and_ordered():
    providers = load_screening_providers(
        ["scrapers.daleel:DaleelProvider", "scrapers.musaffa:MusaffaScraper"]
    )
    assert [provider.source_name for provider in providers] == ["daleel", "musaffa"]


def test_duplicate_provider_ids_are_rejected():
    try:
        load_screening_providers(
            ["scrapers.musaffa:MusaffaScraper", "scrapers.musaffa:MusaffaScraper"]
        )
    except ValueError as exc:
        assert "unique" in str(exc)
    else:
        raise AssertionError("duplicate providers must be rejected")


def test_majority_vote_wins():
    final, conflict = resolve_compliance(
        result("one", ComplianceStatus.HALAL),
        result("two", ComplianceStatus.HALAL),
        result("three", ComplianceStatus.NOT_HALAL),
    )
    assert final.status == ComplianceStatus.HALAL
    assert final.confirmation_count == 3
    assert not final.is_provisional
    assert conflict


def test_any_top_vote_tie_is_not_halal():
    final, conflict = resolve_compliance(
        result("one", ComplianceStatus.HALAL),
        result("two", ComplianceStatus.DOUBTFUL),
    )
    assert final.status == ComplianceStatus.NOT_HALAL
    assert "Tie resolved" in final.details
    assert conflict


def test_one_confirmed_source_is_provisional():
    unavailable = ScreeningResult(
        ticker="TEST",
        status=ComplianceStatus.ERROR,
        source="offline",
        state=ResultState.NETWORK_ERROR,
    )
    final, conflict = resolve_compliance(
        result("working", ComplianceStatus.HALAL), unavailable
    )
    assert final.status == ComplianceStatus.HALAL
    assert final.is_provisional
    assert final.confirmation_count == 1
    assert not conflict


def test_musaffa_parses_visible_etf_verdict_and_evidence():
    page = """
    <html><title>Is SP Funds ETF Halal?</title><body>
      <h2>SPUS Shariah Compliance</h2><div class="status-text">HALAL</div>
    </body></html>
    """
    security = Security("SPUS", asset_type=AssetType.ETF)
    parsed = MusaffaScraper()._parse_content(
        security, page, "https://musaffa.com/etf/SPUS/", AssetType.ETF
    )
    assert parsed.status == ComplianceStatus.HALAL
    assert parsed.evidence == "SPUS Shariah Compliance - HALAL"
    assert parsed.asset_type == AssetType.ETF


def test_musaffa_ignores_seo_and_navigation_halal_text():
    page = """
    <html><title>Is Berkshire Halal? BRK.B Shariah Compliance Analysis</title>
    <body>Halal stocks Halal ETFs
      <h2>BRK.B Shariah Compliance</h2>
      <div class="mb-0 status-text">NOT HALAL</div>
    </body></html>
    """
    parsed = MusaffaScraper()._parse_content(
        Security("BRK.B", asset_type=AssetType.STOCK),
        page,
        "https://musaffa.com/stock/BRK.B/",
        AssetType.STOCK,
    )
    assert parsed.status == ComplianceStatus.NOT_HALAL


def test_musaffa_valid_unknown_layout_is_parse_error_not_not_covered():
    security = Security("AAPL", asset_type=AssetType.STOCK)
    parsed = MusaffaScraper()._parse_content(
        security,
        "<html><body>Apple AAPL profile without a verdict</body></html>",
        "https://musaffa.com/stock/AAPL/",
        AssetType.STOCK,
    )
    assert parsed.state == ResultState.PARSE_ERROR
    assert parsed.status == ComplianceStatus.ERROR


def test_zoya_uses_visible_heading_and_preserves_dotted_identity():
    page = """
    <html><body><h2>BRK.B stock is&nbsp;<a>not Shariah-compliant</a></h2>
    <p>BRK.B is Shariah-compliant in an unrelated hidden template.</p></body></html>
    """
    security = Security("BRK.B", asset_type=AssetType.STOCK)
    parsed = ZoyaScraper()._parse_content(
        security, page, "https://zoya.finance/stocks/brk-b"
    )
    assert parsed.status == ComplianceStatus.NOT_HALAL
    assert parsed.evidence == "BRK.B stock is not Shariah-compliant"


def test_daleel_parses_stock_and_keeps_audit_evidence():
    payload = {
        "success": True,
        "data": {
            "symbol": "AAPL",
            "company_name": "Apple Inc.",
            "security_type": "equity",
            "compliance": "compliant",
            "score": 98.1,
            "evidence": [{"concept": "Total assets"}, {"concept": "Debt"}],
            "approximations": [],
        },
    }

    async def run():
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=payload, request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            return await DaleelProvider()._fetch_single(
                client, Security("AAPL", asset_type=AssetType.STOCK)
            )

    parsed = asyncio.run(run())
    assert parsed.status == ComplianceStatus.HALAL
    assert "Total assets" in parsed.evidence
    assert "no Sharia board" in parsed.methodology


def test_daleel_parses_etf_coverage():
    payload = {
        "success": True,
        "data": {
            "symbol": "SPUS",
            "company_name": "SP Funds ETF",
            "security_type": "etf",
            "compliance": "compliant",
            "score": 91.3,
            "etf_look_through": {"coverage_pct_of_fund": 56.2},
        },
    }

    async def run():
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=json.dumps(payload).encode(), request=request
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            return await DaleelProvider()._fetch_single(
                client, Security("SPUS", asset_type=AssetType.ETF)
            )

    parsed = asyncio.run(run())
    assert parsed.asset_type == AssetType.ETF
    assert parsed.evidence == "Holdings look-through; 56.2% of fund screened"


def test_shared_transport_retries_transient_status(monkeypatch):
    attempts = 0

    async def no_sleep(delay):
        return None

    def handler(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(503 if attempts < 3 else 200, request=request)

    monkeypatch.setattr("scrapers.base.asyncio.sleep", no_sleep)

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await DaleelProvider().request(client, "GET", "https://example.test")

    response = asyncio.run(run())
    assert response.status_code == 200
    assert attempts == 3


def test_shared_transport_does_not_retry_404(monkeypatch):
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(404, request=request)

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await DaleelProvider().request(client, "GET", "https://example.test")

    response = asyncio.run(run())
    assert response.status_code == 404
    assert attempts == 1
