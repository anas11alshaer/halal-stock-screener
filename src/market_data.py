"""Issuer fundamentals for Activity/Ratios. yfinance first, optional SEC facts."""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any

import httpx
import yfinance as yf

from plugins.base import Fundamentals, is_fund
from policy import Policy

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

        fundamentals = Fundamentals(
            ticker=ticker,
            quote_type=str(info.get("quoteType") or "UNKNOWN"),
            sector=info.get("sector"),
            industry=info.get("industry"),
            company_name=info.get("longName") or info.get("shortName"),
            market_cap=_to_float(info.get("marketCap")),
            total_debt=debt,
            cash_and_securities=cash,
            interest_income=interest,
            revenue=revenue,
        )

        if (
            fundamentals.interest_income is None
            and ratios.get("use_sec_companyfacts")
            and not is_fund(fundamentals.quote_type, self._policy)
        ):
            await self._fill_companyfacts(fundamentals)
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
        if fundamentals.interest_income is None:
            fundamentals.interest_income = _latest_fact(
                facts, list(ratios.get("companyfacts_interest_income_tags") or [])
            )
        if fundamentals.revenue is None:
            fundamentals.revenue = _latest_fact(
                facts, list(ratios.get("companyfacts_revenue_tags") or [])
            )
        if fundamentals.total_debt is None:
            fundamentals.total_debt = _latest_fact(
                facts, list(ratios.get("companyfacts_debt_tags") or [])
            )
        if fundamentals.cash_and_securities is None:
            fundamentals.cash_and_securities = _latest_fact(
                facts, list(ratios.get("companyfacts_cash_tags") or [])
            )

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


def _latest_fact(gaap: dict[str, Any], tags: list[str]) -> float | None:
    for tag in tags:
        units = (gaap.get(tag) or {}).get("units") or {}
        points = units.get("USD") or units.get("USD/shares") or []
        dated = [p for p in points if isinstance(p, dict) and p.get("val") is not None]
        if not dated:
            continue
        dated.sort(key=lambda p: str(p.get("end") or p.get("filed") or ""))
        return _to_float(dated[-1].get("val"))
    return None
