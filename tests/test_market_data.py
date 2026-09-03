"""YFinanceMarketData companyfacts outer gate and fact states (offline)."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from config import POLICY_PATH
from market_data import YFinanceMarketData, _sum_facts_same_period
from plugins.base import FactState
from policy import load_policy

AAPL_TICKERS = {"0": {"ticker": "AAPL", "cik_str": 320193}}

DEBT_GAAP = {
    "LongTermDebt": {
        "units": {
            "USD": [
                {"end": "2024-12-31", "val": 80},
                {"end": "2023-12-31", "val": 1},
            ]
        }
    },
    "DebtCurrent": {"units": {"USD": [{"end": "2024-12-31", "val": 20}]}},
    "ShortTermBorrowings": {"units": {"USD": [{"end": "2023-12-31", "val": 999}]}},
}


class _Loc:
    def __init__(self, rows: dict[str, float]) -> None:
        self._rows = rows

    def __getitem__(self, key):
        return self._rows[key]


class _Frame:
    empty = False

    def __init__(self, rows: dict[str, float]) -> None:
        self.index = list(rows)
        self.loc = _Loc(rows)


class StubYahoo(YFinanceMarketData):
    def __init__(self, policy, http, info: dict, financials=None, balance=None) -> None:
        super().__init__(policy, http=http)
        self._info = info
        self._financials = financials
        self._balance = balance

    def _download(self, ticker: str):
        return self._info, self._financials, self._balance


def _equity_info(**extra) -> dict:
    info = {
        "quoteType": "EQUITY",
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "longName": "Test Co",
        "marketCap": 1000.0,
        "totalCash": 5.0,
    }
    info.update(extra)
    return info


def _companyfacts_handler(gaap: dict, requested: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requested.append(url)
        if url.endswith("company_tickers.json"):
            return httpx.Response(200, json=AAPL_TICKERS)
        if "companyfacts" in url:
            return httpx.Response(200, json={"facts": {"us-gaap": gaap}})
        return httpx.Response(404)

    return handler


@pytest.mark.asyncio
async def test_companyfacts_runs_when_interest_present_debt_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("config.SEC_CONTACT_EMAIL", "nport-tests@example.com")
    policy = load_policy(POLICY_PATH)
    requested: list[str] = []
    gaap = dict(DEBT_GAAP)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_companyfacts_handler(gaap, requested))
    ) as http:
        fundamentals = await StubYahoo(
            policy, http, _equity_info(interestIncome=12.0)
        ).get("AAPL")

    debt_tags = list(policy.section("ratios")["companyfacts_debt_tags"])
    assert any(url.endswith("company_tickers.json") for url in requested)
    assert any("companyfacts" in url for url in requested)
    assert any("CIK0000320193" in url for url in requested)
    assert fundamentals.interest_income == pytest.approx(12.0)
    assert fundamentals.total_debt == _sum_facts_same_period(gaap, debt_tags)
    assert fundamentals.total_debt == pytest.approx(100.0)
    assert fundamentals.fact_states["total_debt"] is FactState.FOUND
    assert fundamentals.fact_states["interest_income"] is FactState.FOUND


@pytest.mark.asyncio
async def test_companyfacts_ar_ignores_usd_shares(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("config.SEC_CONTACT_EMAIL", "nport-tests@example.com")
    policy = load_policy(POLICY_PATH)
    requested: list[str] = []
    gaap = {
        "AccountsReceivableNetCurrent": {
            "units": {"USD/shares": [{"end": "2024-12-31", "val": 999}]}
        },
        "AccountsReceivableNet": {
            "units": {"USD/shares": [{"end": "2024-12-31", "val": 50}]}
        },
    }

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_companyfacts_handler(gaap, requested))
    ) as http:
        fundamentals = await StubYahoo(
            policy,
            http,
            _equity_info(interestIncome=12.0, totalDebt=10.0),
        ).get("AAPL")

    assert any("companyfacts" in url for url in requested)
    assert fundamentals.accounts_receivable is None
    assert fundamentals.fact_states["accounts_receivable"] is FactState.MISSING


@pytest.mark.asyncio
async def test_companyfacts_ar_usd_fill_is_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("config.SEC_CONTACT_EMAIL", "nport-tests@example.com")
    policy = load_policy(POLICY_PATH)
    requested: list[str] = []
    gaap = {
        "AccountsReceivableNetCurrent": {
            "units": {"USD": [{"end": "2024-12-31", "val": 42}]}
        }
    }

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_companyfacts_handler(gaap, requested))
    ) as http:
        fundamentals = await StubYahoo(
            policy,
            http,
            _equity_info(interestIncome=12.0, totalDebt=10.0),
        ).get("AAPL")

    assert any("companyfacts" in url for url in requested)
    assert fundamentals.accounts_receivable == pytest.approx(42.0)
    assert fundamentals.fact_states["accounts_receivable"] is FactState.FOUND


@pytest.mark.asyncio
async def test_yfinance_zero_interest_and_ar_are_found() -> None:
    policy = load_policy(POLICY_PATH)
    fundamentals = await StubYahoo(
        policy,
        None,
        _equity_info(totalDebt=10.0),
        financials=_Frame({"Interest Income": 0.0}),
        balance=_Frame({"Net Receivables": 0.0}),
    ).get("AAPL")
    assert fundamentals.interest_income == 0.0
    assert fundamentals.accounts_receivable == 0.0
    assert fundamentals.fact_states["interest_income"] is FactState.FOUND
    assert fundamentals.fact_states["accounts_receivable"] is FactState.FOUND
    assert fundamentals.fact_states["interest_income"] is not FactState.ZERO
    assert fundamentals.fact_states["accounts_receivable"] is not FactState.ZERO


@pytest.mark.asyncio
async def test_absent_interest_after_companyfacts_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("config.SEC_CONTACT_EMAIL", "nport-tests@example.com")
    policy = load_policy(POLICY_PATH)
    requested: list[str] = []
    gaap = {
        "InterestIncomeExpenseNet": {
            "units": {"USD": [{"end": "2024-12-31", "val": 50}]}
        },
        "InterestIncomeOperating": {
            "units": {"USD/shares": [{"end": "2024-12-31", "val": 9}]}
        },
    }

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_companyfacts_handler(gaap, requested))
    ) as http:
        fundamentals = await StubYahoo(policy, http, _equity_info(totalDebt=10.0)).get(
            "AAPL"
        )

    assert any("companyfacts" in url for url in requested)
    assert fundamentals.interest_income is None
    assert fundamentals.fact_states["interest_income"] is FactState.MISSING


@pytest.mark.asyncio
async def test_equity_empty_segments_are_missing() -> None:
    policy = load_policy(POLICY_PATH)
    fundamentals = await StubYahoo(
        policy, None, _equity_info(interestIncome=12.0, totalDebt=10.0)
    ).get("AAPL")
    assert fundamentals.segments == []
    assert fundamentals.fact_states["segments"] is FactState.MISSING


@pytest.mark.asyncio
async def test_fund_does_not_mark_segments_missing() -> None:
    policy = load_policy(POLICY_PATH)
    fundamentals = await StubYahoo(
        policy,
        None,
        _equity_info(quoteType="ETF", interestIncome=12.0, totalDebt=10.0),
    ).get("SPY")
    assert "segments" not in fundamentals.fact_states
