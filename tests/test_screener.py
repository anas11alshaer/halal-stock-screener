"""Tests for provider-neutral orchestration and output."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import screener
from scrapers import (
    AssetType,
    BaseScraper,
    ComplianceStatus,
    ResultState,
    ScreeningResult,
    Security,
)
from screener import ScreenResponse, StockScreener
from security_resolver import ResolutionResult


class FixedResolver:
    async def resolve(self, query):
        return ResolutionResult(
            security=Security(
                symbol=query.upper(),
                name="Example Security",
                asset_type=AssetType.STOCK,
            )
        )


class FixedProvider(BaseScraper):
    def __init__(self, provider_id, status, state=ResultState.SUCCESS):
        self.provider_id = provider_id
        self.fixed_status = status
        self.fixed_state = state

    @property
    def source_name(self):
        return self.provider_id

    async def _fetch_single(self, client, security):
        return ScreeningResult(
            ticker=security.symbol,
            status=self.fixed_status,
            source=self.source_name,
            state=self.fixed_state,
            asset_type=security.asset_type,
            evidence=f"{self.source_name} evidence"
            if self.fixed_state == ResultState.SUCCESS
            else None,
            error_message="temporary failure" if self.fixed_state != ResultState.SUCCESS else None,
        )


def test_orchestrator_uses_all_plugins_and_majority(monkeypatch):
    monkeypatch.setattr(screener, "init_database", lambda: None)
    monkeypatch.setattr(screener.TickerCache, "get", lambda *args: None)
    monkeypatch.setattr(screener.TickerCache, "set", lambda **kwargs: None)
    providers = [
        FixedProvider("one", ComplianceStatus.HALAL),
        FixedProvider("two", ComplianceStatus.HALAL),
        FixedProvider("three", ComplianceStatus.NOT_HALAL),
    ]
    service = StockScreener(providers=providers, security_resolver=FixedResolver())
    response = asyncio.run(service.screen_tickers(["TEST"]))
    assert response.results[0].status == ComplianceStatus.HALAL
    assert response.results[0].confirmation_count == 3
    assert list(response.source_results["TEST"]) == ["one", "two", "three"]


def test_output_shows_success_failure_and_provisional_state():
    final = ScreeningResult(
        ticker="SPUS",
        company_name="SP Funds ETF",
        status=ComplianceStatus.HALAL,
        source="combined",
        asset_type=AssetType.ETF,
        is_provisional=True,
        confirmation_count=1,
    )
    success = ScreeningResult(
        ticker="SPUS",
        status=ComplianceStatus.HALAL,
        source="daleel",
        asset_type=AssetType.ETF,
        evidence="Holdings look-through; 56.2% of fund screened",
        methodology="calculation; no Sharia board",
        url="https://example.test/SPUS",
    )
    failure = ScreeningResult(
        ticker="SPUS",
        status=ComplianceStatus.ERROR,
        source="zoya",
        state=ResultState.UNSUPPORTED_ASSET,
        error_message="Zoya does not support ETFs",
    )
    message = ScreenResponse(
        [final], [False], source_results={"SPUS": {"daleel": success, "zoya": failure}}
    ).format_message()
    assert "Provisional verdict" in message
    assert "Holdings look-through; 56.2%" in message
    assert "Zoya</b>: Unsupported asset" in message


def test_output_hides_sources_that_reached_their_call_limit():
    final = ScreeningResult(
        ticker="AAPL",
        status=ComplianceStatus.HALAL,
        source="combined",
        confirmation_count=1,
    )
    success = ScreeningResult(
        ticker="AAPL",
        status=ComplianceStatus.HALAL,
        source="daleel",
    )
    limited = ScreeningResult(
        ticker="AAPL",
        status=ComplianceStatus.ERROR,
        source="halal_screener",
        state=ResultState.RATE_LIMITED,
        error_message="Provider quota reached",
    )

    message = ScreenResponse(
        [final],
        [False],
        source_results={"AAPL": {"daleel": success, "halal_screener": limited}},
    ).format_message()

    assert "Daleel" in message
    assert "Halal Screener" not in message
    assert "quota" not in message.lower()


def test_large_multi_result_output_is_split_on_result_boundaries():
    results = [
        ScreeningResult(
            ticker=f"T{i}",
            status=ComplianceStatus.HALAL,
            source="combined",
            asset_type=AssetType.STOCK,
            confirmation_count=3,
        )
        for i in range(25)
    ]
    source_results = {
        result.ticker: {
            f"provider_with_a_long_name_{provider_index}": ScreeningResult(
                ticker=result.ticker,
                status=ComplianceStatus.HALAL,
                source=f"provider_{provider_index}",
                evidence="e" * 500,
            )
            for provider_index in range(5)
        }
        for result in results
    }
    messages = ScreenResponse(
        results, [False] * 25, source_results=source_results
    ).format_messages()
    assert len(messages) == 25
    assert all(len(message) <= 4000 for message in messages)
