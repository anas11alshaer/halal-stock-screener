"""Tests for ticker/name and asset-type resolution."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import security_resolver
from scrapers import AssetType
from security_resolver import YahooSecurityResolver


class FakeSearch:
    quotes_by_query = {}

    def __init__(self, query, **kwargs):
        self.quotes = self.quotes_by_query.get(query, [])


def test_resolves_etf_asset_type(monkeypatch):
    FakeSearch.quotes_by_query = {
        "SPUS": [
            {
                "symbol": "SPUS",
                "quoteType": "ETF",
                "longname": "SP Funds S&P 500 Sharia Industry Exclusions ETF",
                "exchange": "PCX",
            }
        ]
    }
    monkeypatch.setattr(security_resolver.yf, "Search", FakeSearch)
    resolved = asyncio.run(YahooSecurityResolver().resolve("SPUS"))
    assert resolved.security.asset_type == AssetType.ETF
    assert resolved.security.name.startswith("SP Funds")


def test_preserves_dotted_symbol_while_recording_yahoo_symbol(monkeypatch):
    FakeSearch.quotes_by_query = {
        "BRK-B": [
            {
                "symbol": "BRK-B",
                "quoteType": "EQUITY",
                "longname": "Berkshire Hathaway Inc.",
                "exchange": "NYQ",
            }
        ]
    }
    monkeypatch.setattr(security_resolver.yf, "Search", FakeSearch)
    resolved = asyncio.run(YahooSecurityResolver().resolve("BRK.B"))
    assert resolved.security.symbol == "BRK.B"
    assert resolved.security.yahoo_symbol == "BRK-B"


def test_ambiguous_company_name_returns_candidates(monkeypatch):
    FakeSearch.quotes_by_query = {
        "Berkshire Hathaway Inc.": [
            {
                "symbol": "BRK-A",
                "quoteType": "EQUITY",
                "longname": "Berkshire Hathaway Inc.",
                "exchange": "NYQ",
            },
            {
                "symbol": "BRK-B",
                "quoteType": "EQUITY",
                "longname": "Berkshire Hathaway Inc.",
                "exchange": "NYQ",
            },
        ]
    }
    monkeypatch.setattr(security_resolver.yf, "Search", FakeSearch)
    resolved = asyncio.run(YahooSecurityResolver().resolve("Berkshire Hathaway Inc."))
    assert resolved.is_ambiguous
    assert [candidate.symbol for candidate in resolved.candidates] == ["BRK.A", "BRK.B"]


def test_ticker_falls_back_without_guessing_asset_type(monkeypatch):
    class FailingSearch:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("offline")

    monkeypatch.setattr(security_resolver.yf, "Search", FailingSearch)
    resolved = asyncio.run(YahooSecurityResolver().resolve("AAPL"))
    assert resolved.security.symbol == "AAPL"
    assert resolved.security.asset_type == AssetType.UNKNOWN
