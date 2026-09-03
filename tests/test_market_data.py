"""YFinanceMarketData companyfacts outer gate and fact states (offline)."""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from config import POLICY_PATH
from market_data import (
    YFinanceMarketData,
    compute_trailing_avg_market_cap,
    _sum_facts_same_period,
)
from plugins.base import DenominatorSource, FactState
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
    def __init__(
        self,
        policy,
        http,
        info: dict,
        financials=None,
        balance=None,
        history=None,
    ) -> None:
        super().__init__(policy, http=http)
        self._info = info
        self._financials = financials
        self._balance = balance
        self._history = history

    def _download(self, ticker: str):
        return self._info, self._financials, self._balance

    def _download_history(self, ticker: str):
        return self._history


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


_TRAIL_END = date(2026, 8, 21)
_TRAIL_START_36 = date(2023, 8, 21)
_TRAIL_START_35 = date(2023, 9, 21)
_TRAIL_START_11 = date(2025, 9, 21)


def _weekday_bars(
    start: date,
    end: date,
    *,
    start_price: float = 10.0,
    step: float = 0.05,
) -> list[tuple[date, float]]:
    bars: list[tuple[date, float]] = []
    price = start_price
    day = start
    while day <= end:
        if day.weekday() < 5:
            bars.append((day, price))
            price += step
        day += timedelta(days=1)
    return bars


def _n_bars(
    start: date,
    end: date,
    count: int,
    *,
    start_price: float = 10.0,
    end_price: float = 30.0,
) -> list[tuple[date, float]]:
    if count < 2:
        return [(end, end_price)]
    span = (end - start).days
    bars: list[tuple[date, float]] = []
    for i in range(count):
        day = start + timedelta(days=round(i * span / (count - 1)))
        t = i / (count - 1)
        bars.append((day, start_price + t * (end_price - start_price)))
    return bars


def _expected_pavg_plast(bars: list[tuple[date, float]], spot: float) -> float:
    closes = [close for _, close in bars]
    return (sum(closes) / len(closes)) / closes[-1] * spot


def test_policy_trailing_avg_keys() -> None:
    ratios = load_policy(POLICY_PATH).section("ratios")
    assert ratios.get("use_trailing_avg_market_cap") is True
    assert ratios.get("trailing_avg_months") == 36
    assert ratios.get("spot_fallback_ok") is True


def test_trailing_avg_is_not_llm() -> None:
    text = (Path(__file__).parent.parent / "src" / "market_data.py").read_text(
        encoding="utf-8"
    )
    lower = text.casefold()
    assert "nvidia" not in lower
    assert "chat_text" not in text
    assert "DataFinder" not in text


def test_history_period_covers_trailing_window() -> None:
    text = (Path(__file__).parent.parent / "src" / "market_data.py").read_text(
        encoding="utf-8"
    )
    assert 'period="5y"' in text
    assert 'period="3y"' not in text


def test_trailing_avg_36mo_series_uses_pavg_plast_times_spot() -> None:
    months = int(load_policy(POLICY_PATH).section("ratios")["trailing_avg_months"])
    bars = _weekday_bars(_TRAIL_START_36, _TRAIL_END)
    spot = 5000.0
    expected = _expected_pavg_plast(bars, spot)
    cap, source = compute_trailing_avg_market_cap(bars, spot, months)
    assert source is DenominatorSource.TRAILING_AVG
    assert cap == pytest.approx(expected)


@pytest.mark.parametrize(
    ("last", "window_start"),
    [
        (date(2026, 8, 19), date(2023, 8, 19)),
        (date(2026, 8, 20), date(2023, 8, 20)),
    ],
)
def test_trailing_avg_weekend_window_start_is_trailing_avg(
    last: date, window_start: date
) -> None:
    months = int(load_policy(POLICY_PATH).section("ratios")["trailing_avg_months"])
    assert window_start.weekday() >= 5
    bars = _weekday_bars(window_start, last)
    expected_days = 252 * (months / 12.0)
    assert len(bars) >= expected_days * 0.80
    cap, source = compute_trailing_avg_market_cap(bars, 5000.0, months)
    assert source is DenominatorSource.TRAILING_AVG
    assert cap == pytest.approx(_expected_pavg_plast(bars, 5000.0))


def test_trailing_avg_11mo_series_is_spot_fallback() -> None:
    months = int(load_policy(POLICY_PATH).section("ratios")["trailing_avg_months"])
    bars = _weekday_bars(_TRAIL_START_11, _TRAIL_END)
    cap, source = compute_trailing_avg_market_cap(bars, 5000.0, months)
    assert cap is None
    assert source is DenominatorSource.SPOT_FALLBACK


def test_trailing_avg_calendar_months_short_of_window_is_spot_fallback() -> None:
    months = int(load_policy(POLICY_PATH).section("ratios")["trailing_avg_months"])
    bars = _weekday_bars(_TRAIL_START_35, _TRAIL_END)
    expected_days = 252 * (months / 12.0)
    assert len(bars) >= expected_days * 0.80
    cap, source = compute_trailing_avg_market_cap(bars, 5000.0, months)
    assert cap is None
    assert source is DenominatorSource.SPOT_FALLBACK


def test_trailing_avg_below_80pct_trading_days_is_spot_fallback() -> None:
    months = int(load_policy(POLICY_PATH).section("ratios")["trailing_avg_months"])
    expected_days = 252 * (months / 12.0)
    # 400/756 ≈ 53%: below 80%, above 20% if the floor were inverted.
    bars = _n_bars(_TRAIL_START_36, _TRAIL_END, 400)
    assert _TRAIL_END.year - _TRAIL_START_36.year == 3
    assert 0.20 * expected_days < len(bars) < 0.80 * expected_days
    cap, source = compute_trailing_avg_market_cap(bars, 5000.0, months)
    assert cap is None
    assert source is DenominatorSource.SPOT_FALLBACK


def test_trailing_avg_meets_calendar_and_80pct_floors() -> None:
    months = int(load_policy(POLICY_PATH).section("ratios")["trailing_avg_months"])
    bars = _weekday_bars(_TRAIL_START_36, _TRAIL_END)
    expected_days = 252 * (months / 12.0)
    assert len(bars) >= expected_days * 0.80
    cap, source = compute_trailing_avg_market_cap(bars, 5000.0, months)
    assert source is DenominatorSource.TRAILING_AVG
    assert cap == pytest.approx(_expected_pavg_plast(bars, 5000.0))


def test_trailing_avg_dual_class_does_not_use_shares_times_close() -> None:
    months = int(load_policy(POLICY_PATH).section("ratios")["trailing_avg_months"])
    bars = _weekday_bars(_TRAIL_START_36, _TRAIL_END)
    mean_close = sum(c for _, c in bars) / len(bars)
    last_close = bars[-1][1]
    shares = 100.0
    last_times_shares = last_close * shares
    mean_times_shares = mean_close * shares
    spot = last_times_shares * 2.0
    expected = mean_close / last_close * spot
    assert expected != pytest.approx(mean_times_shares)
    assert expected != pytest.approx(last_times_shares)
    cap, source = compute_trailing_avg_market_cap(bars, spot, months)
    assert source is DenominatorSource.TRAILING_AVG
    assert cap == pytest.approx(expected)
    assert cap != pytest.approx(mean_times_shares)
    assert cap != pytest.approx(last_times_shares)


def test_trailing_avg_empty_history_or_missing_spot_is_fallback() -> None:
    months = int(load_policy(POLICY_PATH).section("ratios")["trailing_avg_months"])
    bars = _weekday_bars(_TRAIL_START_36, _TRAIL_END)
    cap, source = compute_trailing_avg_market_cap([], 5000.0, months)
    assert cap is None
    assert source is DenominatorSource.SPOT_FALLBACK
    cap, source = compute_trailing_avg_market_cap(bars, None, months)
    assert cap is None
    assert source is DenominatorSource.SPOT_FALLBACK


@pytest.mark.asyncio
async def test_injected_36mo_history_sets_trailing_avg_fields() -> None:
    policy = load_policy(POLICY_PATH)
    months = int(policy.section("ratios")["trailing_avg_months"])
    bars = _weekday_bars(_TRAIL_START_36, _TRAIL_END)
    spot = 5000.0
    expected = _expected_pavg_plast(bars, spot)
    fundamentals = await StubYahoo(
        policy,
        None,
        _equity_info(interestIncome=12.0, totalDebt=10.0, marketCap=spot),
        history=bars,
    ).get("AAPL")
    assert fundamentals.denominator_source is DenominatorSource.TRAILING_AVG
    assert fundamentals.trailing_avg_market_cap == pytest.approx(expected)
    assert fundamentals.trailing_avg_months == months
    assert fundamentals.market_cap == pytest.approx(spot)


@pytest.mark.asyncio
async def test_injected_11mo_history_is_spot_fallback() -> None:
    policy = load_policy(POLICY_PATH)
    bars = _weekday_bars(_TRAIL_START_11, _TRAIL_END)
    fundamentals = await StubYahoo(
        policy,
        None,
        _equity_info(interestIncome=12.0, totalDebt=10.0, marketCap=5000.0),
        history=bars,
    ).get("AAPL")
    assert fundamentals.denominator_source is DenominatorSource.SPOT_FALLBACK
    assert fundamentals.trailing_avg_market_cap is None
    assert fundamentals.market_cap == pytest.approx(5000.0)


@pytest.mark.asyncio
async def test_dual_class_history_ignores_shares_outstanding() -> None:
    policy = load_policy(POLICY_PATH)
    bars = _weekday_bars(_TRAIL_START_36, _TRAIL_END)
    mean_close = sum(c for _, c in bars) / len(bars)
    last_close = bars[-1][1]
    shares = 100.0
    last_times_shares = last_close * shares
    mean_times_shares = mean_close * shares
    spot = last_times_shares * 2.0
    expected = mean_close / last_close * spot
    assert expected != pytest.approx(mean_times_shares)
    assert expected != pytest.approx(last_times_shares)
    fundamentals = await StubYahoo(
        policy,
        None,
        _equity_info(
            interestIncome=12.0,
            totalDebt=10.0,
            marketCap=spot,
            sharesOutstanding=shares,
        ),
        history=bars,
    ).get("AAPL")
    assert fundamentals.denominator_source is DenominatorSource.TRAILING_AVG
    assert fundamentals.trailing_avg_market_cap == pytest.approx(expected)
    assert fundamentals.trailing_avg_market_cap != pytest.approx(mean_times_shares)
    assert fundamentals.trailing_avg_market_cap != pytest.approx(last_times_shares)


@pytest.mark.asyncio
async def test_missing_use_trailing_avg_key_does_not_fetch_history() -> None:
    policy = load_policy(POLICY_PATH)
    del policy.raw["ratios"]["use_trailing_avg_market_cap"]

    class _NoHistory(StubYahoo):
        def _download_history(self, ticker: str):
            raise AssertionError("history must not be fetched when trailing is off")

    fundamentals = await _NoHistory(
        policy, None, _equity_info(interestIncome=12.0, totalDebt=10.0)
    ).get("AAPL")
    assert fundamentals.denominator_source is DenominatorSource.SPOT
    assert fundamentals.trailing_avg_market_cap is None
    assert fundamentals.market_cap == pytest.approx(1000.0)
