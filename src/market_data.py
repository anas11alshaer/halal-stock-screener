"""Issuer fundamentals for Activity/Ratios. yfinance first, optional SEC facts."""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any

import httpx
import yfinance as yf

from plugins.base import FactState, Fundamentals, is_fund
from policy import Policy

_RATIO_FACT_FIELDS = (
    "interest_income",
    "revenue",
    "total_debt",
    "cash_and_securities",
    "accounts_receivable",
)

logger = logging.getLogger(__name__)


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _first_row(frame: Any, names: list[str]) -> float | None:
    if frame is None:
        return None
    empty = getattr(frame, "empty", True)
    if empty:
        return None
    index_map = {str(i).lower(): i for i in frame.index}
    for name in names:
        key = index_map.get(name.lower())
        if key is None:
            continue
        series = frame.loc[key]
        values = series.tolist() if hasattr(series, "tolist") else [series]
        for raw in values:
            number = _to_float(raw)
            if number is not None:
                return number
    return None


class MarketData:
    async def get(self, ticker: str) -> Fundamentals:
        raise NotImplementedError


class YFinanceMarketData(MarketData):
    """Live fundamentals. Tests should inject a fake MarketData instead."""

    def __init__(self, policy: Policy, http: httpx.AsyncClient | None = None) -> None:
        self._policy = policy
        self._http = http
        self._ticker_ciks: dict[str, str] | None = None

    async def get(self, ticker: str) -> Fundamentals:
        ticker = ticker.upper()
        info, financials, balance = await asyncio.to_thread(self._download, ticker)
        ratios = self._policy.section("ratios")
        interest = _first_row(
            financials, list(ratios.get("yfinance_interest_income_rows") or [])
        )
        if interest is None:
            interest = _to_float(info.get("interestIncome"))
        revenue = _first_row(
            financials, list(ratios.get("yfinance_revenue_rows") or [])
        )
        debt = _to_float(info.get("totalDebt"))
        if debt is None:
            debt = _first_row(balance, list(ratios.get("yfinance_debt_rows") or []))
        cash = _first_row(balance, list(ratios.get("yfinance_cash_rows") or []))
        if cash is None:
            cash = _to_float(info.get("totalCash"))
        receivable = _first_row(
            balance, list(ratios.get("yfinance_receivable_rows") or [])
        )

        fundamentals = Fundamentals(
            ticker=ticker,
            quote_type=str(info.get("quoteType") or "UNKNOWN"),
            sector=info.get("sector"),
            industry=info.get("industry"),
            company_name=info.get("longName") or info.get("shortName"),
            market_cap=_to_float(info.get("marketCap")),
            total_debt=debt,
            cash_and_securities=cash,
            accounts_receivable=receivable,
            interest_income=interest,
            revenue=revenue,
            income_statement_present=_financials_present(financials),
        )

        if (
            ratios.get("use_sec_companyfacts")
            and self._http is not None
            and not is_fund(fundamentals.quote_type, self._policy)
            and _ratio_input_missing(fundamentals)
        ):
            await self._fill_companyfacts(fundamentals)
        self._apply_fact_states(fundamentals)
        return fundamentals

    def _download(self, ticker: str) -> tuple[dict[str, Any], Any, Any]:
        stock = yf.Ticker(ticker)
        try:
            info = dict(stock.info or {})
        except Exception as exc:
            logger.warning("yfinance info failed for %s: %s", ticker, exc)
            info = {}
        try:
            financials = stock.financials
        except Exception as exc:
            logger.warning("yfinance financials failed for %s: %s", ticker, exc)
            financials = None
        try:
            balance = stock.balance_sheet
        except Exception as exc:
            logger.warning("yfinance balance sheet failed for %s: %s", ticker, exc)
            balance = None
        return info, financials, balance

    async def _fill_companyfacts(self, fundamentals: Fundamentals) -> None:
        if self._http is None:
            return
        try:
            cik = await self._cik_for(fundamentals.ticker)
            if not cik:
                return
            url = str(self._policy.section("sources")["sec_companyfacts_url"]).format(
                cik=cik
            )
            response = await self._http.get(url, headers=self._policy.edgar_headers())
            if response.status_code >= 400:
                return
            facts = (response.json().get("facts") or {}).get("us-gaap") or {}
        except Exception as exc:
            logger.warning("companyfacts failed for %s: %s", fundamentals.ticker, exc)
            return

        ratios = self._policy.section("ratios")
        interest_tags = list(ratios.get("companyfacts_interest_income_tags") or [])
        revenue_tags = list(ratios.get("companyfacts_revenue_tags") or [])
        if fundamentals.interest_income is None:
            fundamentals.interest_income = _latest_fact(
                facts,
                interest_tags,
                skip_net=True,
            )
        if fundamentals.revenue is None:
            fundamentals.revenue = _latest_fact(facts, revenue_tags)
        if fundamentals.total_debt is None:
            fundamentals.total_debt = _sum_facts_same_period(
                facts, list(ratios.get("companyfacts_debt_tags") or [])
            )
        if fundamentals.cash_and_securities is None:
            fundamentals.cash_and_securities = _sum_facts_same_period(
                facts, list(ratios.get("companyfacts_cash_tags") or [])
            )
        if fundamentals.accounts_receivable is None:
            fundamentals.accounts_receivable = _latest_fact(
                facts, list(ratios.get("companyfacts_receivable_tags") or [])
            )
        if not fundamentals.income_statement_present and (
            _has_fy_usd_fact(facts, interest_tags, skip_net=True)
            or _has_fy_usd_fact(facts, revenue_tags)
        ):
            fundamentals.income_statement_present = True

    def _apply_fact_states(self, fundamentals: Fundamentals) -> None:
        for name in _RATIO_FACT_FIELDS:
            value = getattr(fundamentals, name)
            fundamentals.fact_states[name] = (
                FactState.FOUND if value is not None else FactState.MISSING
            )
        quote = (fundamentals.quote_type or "").upper()
        if (
            quote == "EQUITY"
            and not is_fund(quote, self._policy)
            and not fundamentals.segments
        ):
            fundamentals.fact_states["segments"] = FactState.MISSING

    async def _cik_for(self, ticker: str) -> str | None:
        mapping = await self._company_tickers()
        return mapping.get(ticker.upper())

    async def _company_tickers(self) -> dict[str, str]:
        if self._ticker_ciks is not None:
            return self._ticker_ciks
        if self._http is None:
            self._ticker_ciks = {}
            return self._ticker_ciks
        url = str(self._policy.section("sources")["sec_company_tickers_url"])
        response = await self._http.get(url, headers=self._policy.edgar_headers())
        response.raise_for_status()
        payload = response.json()
        mapping: dict[str, str] = {}
        rows = payload.values() if isinstance(payload, dict) else payload
        for rec in rows:
            if not isinstance(rec, dict):
                continue
            t = str(rec.get("ticker") or "").upper()
            cik = rec.get("cik_str") or rec.get("cik")
            if t and cik is not None:
                mapping[t] = str(cik).zfill(10)
        self._ticker_ciks = mapping
        return mapping


def _financials_present(financials: Any) -> bool:
    return bool(financials is not None and not getattr(financials, "empty", True))


def _ratio_input_missing(fundamentals: Fundamentals) -> bool:
    return any(getattr(fundamentals, name) is None for name in _RATIO_FACT_FIELDS)


def _has_fy_usd_fact(
    gaap: dict[str, Any], tags: list[str], *, skip_net: bool = False
) -> bool:
    for tag in tags:
        if skip_net and _is_net_interest_tag(tag):
            continue
        for point in _usd_unit_points((gaap.get(tag) or {}).get("units") or {}):
            if str(point.get("fp") or "").upper() == "FY":
                return True
    return False


def _usd_unit_points(units: dict[str, Any]) -> list[dict[str, Any]]:
    # USD/shares is a per-share figure; treating it as a dollar total understates
    # (or overstates) ratios.
    points = units.get("USD") or []
    return [p for p in points if isinstance(p, dict) and p.get("val") is not None]


def _is_net_interest_tag(tag: str) -> bool:
    return "Expense" in tag or tag.endswith("Net")


def _latest_fact(
    gaap: dict[str, Any], tags: list[str], *, skip_net: bool = False
) -> float | None:
    for tag in tags:
        if skip_net and _is_net_interest_tag(tag):
            continue
        dated = _usd_unit_points((gaap.get(tag) or {}).get("units") or {})
        if not dated:
            continue
        dated.sort(key=lambda p: str(p.get("end") or p.get("filed") or ""))
        return _to_float(dated[-1].get("val"))
    return None


def _sum_facts_same_period(gaap: dict[str, Any], tags: list[str]) -> float | None:
    """Sum component tags at the latest shared `end` date (first-hit understates)."""
    tagged: list[list[dict[str, Any]]] = []
    all_points: list[dict[str, Any]] = []
    for tag in tags:
        points = _usd_unit_points((gaap.get(tag) or {}).get("units") or {})
        if not points:
            continue
        tagged.append(points)
        all_points.extend(points)
    if not all_points:
        return None
    latest = max(str(p.get("end") or p.get("filed") or "") for p in all_points)
    if not latest:
        return None
    total = 0.0
    found = False
    for points in tagged:
        at_end = [
            p for p in points if str(p.get("end") or p.get("filed") or "") == latest
        ]
        if not at_end:
            continue
        at_end.sort(key=lambda p: str(p.get("end") or p.get("filed") or ""))
        value = _to_float(at_end[-1].get("val"))
        if value is not None:
            total += value
            found = True
    return total if found else None
