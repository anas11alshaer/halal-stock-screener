"""SEC N-PORT look-through for US ETFs. Incomplete holdings → FAIL."""

from __future__ import annotations

import asyncio
import json
import logging
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from plugins.base import Plugin, PluginVote, ScreenContext, Vote, is_fund

logger = logging.getLogger(__name__)

ScreenHolding = Callable[[str, int], Awaitable[str]]


@dataclass
class Holding:
    name: str
    pct: float  # fraction of NAV in 0..1
    ticker: str | None = None
    asset_cat: str | None = None
    cusip: str | None = None


@dataclass
class NportReport:
    series_id: str | None
    holdings: list[Holding] = field(default_factory=list)


class NportClient:
    """Fetch a parsed N-PORT holdings report for an ETF ticker."""

    async def holdings(self, ticker: str) -> NportReport | None:
        raise NotImplementedError


class NportHoldingsPlugin(Plugin):
    name = "NportHoldings"

    def __init__(
        self,
        policy,
        nport: NportClient,
        screen_holding: ScreenHolding,
    ) -> None:
        super().__init__(policy)
        self._nport = nport
        self._screen_holding = screen_holding

    async def vote(self, ctx: ScreenContext) -> PluginVote:
        if not is_fund(ctx.quote_type, self.policy):
            return PluginVote(
                plugin=self.name,
                vote=Vote.ABSTAIN,
                reason="not a fund",
            )

        max_depth = int(self.policy.get("etf", "lookthrough_max_depth", default=1) or 1)
        if ctx.depth >= max_depth:
            return PluginVote(
                plugin=self.name,
                vote=Vote.FAIL,
                reason="nested fund exceeds look-through depth",
                metrics={"depth": ctx.depth, "max_depth": max_depth},
            )

        report = await self._nport.holdings(ctx.ticker)
        if report is None or not report.holdings:
            return PluginVote(
                plugin=self.name,
                vote=Vote.FAIL,
                reason="incomplete N-PORT holdings",
            )

        skip = {
            str(c).upper()
            for c in (self.policy.get("etf", "skip_asset_categories", default=[]) or [])
        }

        def _covered(holding: Holding) -> bool:
            if holding.ticker:
                return True
            return (holding.asset_cat or "").upper() in skip

        coverage = sum(h.pct for h in report.holdings if _covered(h))
        raw_floor = self.policy.get("etf", "coverage_floor")
        if raw_floor is None:
            return PluginVote(
                plugin=self.name,
                vote=Vote.FAIL,
                reason="incomplete N-PORT holdings",
                metrics={
                    "coverage": coverage,
                    "missing_policy_key": "etf.coverage_floor",
                },
            )
        floor = float(raw_floor)
        identified = [h for h in report.holdings if h.ticker]
        metrics: dict[str, Any] = {
            "coverage": coverage,
            "coverage_floor": floor,
            "holding_count": len(report.holdings),
            "identified_count": len(identified),
        }
        if coverage < floor:
            return PluginVote(
                plugin=self.name,
                vote=Vote.FAIL,
                reason="incomplete N-PORT holdings",
                metrics=metrics,
            )

        failed: list[str] = []
        for holding in identified:
            assert holding.ticker is not None
            child_verdict = await self._screen_holding(holding.ticker, ctx.depth + 1)
            if child_verdict != "HALAL":
                failed.append(holding.ticker)
        metrics["failed_holdings"] = failed[:20]
        metrics["failed_count"] = len(failed)
        if failed:
            return PluginVote(
                plugin=self.name,
                vote=Vote.FAIL,
                reason=f"{len(failed)} holding(s) not HALAL",
                metrics=metrics,
            )
        return PluginVote(
            plugin=self.name,
            vote=Vote.PASS,
            reason="holdings covered and all identified names HALAL",
            metrics=metrics,
        )


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _as_float(text: str | None) -> float | None:
    if text is None:
        return None
    try:
        return float(text.strip())
    except ValueError:
        return None


_US_EXCHANGES = {"US", "UN", "UW", "UA", "NYS", "NAS", "NYSE", "NASDAQ"}


def _openfigi_us_ticker(row: Any) -> str | None:
    if not isinstance(row, dict):
        return None
    data = row.get("data")
    if not isinstance(data, list):
        return None
    fallback: str | None = None
    for item in data:
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        if fallback is None:
            fallback = ticker
        if str(item.get("exchCode") or "").upper() in _US_EXCHANGES:
            return ticker
    return fallback


def nport_xml_document(document: str) -> str:
    """EDGAR lists XSL viewers; look-through needs the raw primary_doc.xml."""
    name = str(document).replace("\\", "/").strip()
    if not name:
        return "primary_doc.xml"
    if "xsl" in name.lower():
        return name.rsplit("/", 1)[-1] or "primary_doc.xml"
    return name


def parse_nport_xml(xml_text: str) -> NportReport:
    """Parse Form NPORT-P XML into holdings with NAV fractions."""
    root = ET.fromstring(xml_text)
    series_id = None
    raw: list[tuple[str, float, str | None, str | None, str | None]] = []
    for el in root.iter():
        if _local(el.tag) == "seriesId" and el.text and series_id is None:
            series_id = el.text.strip()
        if _local(el.tag) != "invstOrSec":
            continue
        name = ""
        ticker: str | None = None
        pct_val: float | None = None
        asset_cat: str | None = None
        cusip: str | None = None
        for child in el.iter():
            ln = _local(child.tag)
            if ln == "name" and child.text and not name:
                name = child.text.strip()
            elif ln == "pctVal" and child.text:
                pct_val = _as_float(child.text)
            elif ln == "assetCat" and child.text:
                asset_cat = child.text.strip()
            elif ln == "cusip" and child.text:
                cusip = child.text.strip() or None
            elif ln == "ticker":
                if child.text and child.text.strip():
                    ticker = child.text.strip().upper()
                for nested in child:
                    if _local(nested.tag) == "value" and nested.text:
                        ticker = nested.text.strip().upper()
        if pct_val is None:
            continue
        raw.append((name, pct_val, ticker, asset_cat, cusip))

    total = sum(p for _, p, _, _, _ in raw)
    # Filers use either percent (sum ≈ 100) or fraction (sum ≈ 1).
    scale = 100.0 if total > 1.5 else 1.0
    holdings = [
        Holding(name=n, pct=p / scale, ticker=t, asset_cat=a, cusip=c)
        for n, p, t, a, c in raw
    ]
    return NportReport(series_id=series_id, holdings=holdings)


def _load_cusip_cache(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(k): str(v).upper() for k, v in payload.items() if v}


def _save_cusip_cache(path: Path, mapping: dict[str, str]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(mapping, indent=0, sort_keys=True), encoding="utf-8")
    except OSError:
        logger.warning("Could not write CUSIP cache %s", path)


def _cik10(cik: Any) -> str:
    digits = "".join(ch for ch in str(cik) if ch.isdigit())
    return digits.zfill(10)


def _accession_nodash(accession: str) -> str:
    return accession.replace("-", "")


class SecNportClient(NportClient):
    """Free EDGAR NPORT-P client (no paid API)."""

    def __init__(
        self,
        policy,
        http: httpx.AsyncClient,
        *,
        cache_path: Path | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._policy = policy
        self._http = http
        self._mf_index: dict[str, dict[str, Any]] | None = None
        self._issuer_ciks: dict[str, str] | None = None
        self._sleep = sleep or asyncio.sleep
        if cache_path is None:
            from config import DATA_DIR

            cache_path = DATA_DIR / "openfigi_cusip_tickers.json"
        self._cache_path = cache_path
        self._cusip_tickers = _load_cusip_cache(cache_path)

    def _sources(self) -> dict[str, Any]:
        return self._policy.section("sources")

    async def holdings(self, ticker: str) -> NportReport | None:
        ticker = ticker.upper()
        try:
            meta = await self._lookup_fund(ticker)
            if meta is None:
                logger.warning("N-PORT: no EDGAR mapping for %s", ticker)
                return None
            report = await self._latest_nport_report(meta)
            if report is None or not report.holdings:
                return None
            await self._fill_tickers_from_cusip(report)
            return report
        except Exception as exc:
            logger.warning("N-PORT fetch failed for %s: %s", ticker, exc)
            return None

    async def _lookup_fund(self, ticker: str) -> dict[str, Any] | None:
        index = await self._mf_tickers()
        row = index.get(ticker)
        if row:
            return row
        cik = await self._issuer_cik(ticker)
        if cik:
            logger.info(
                "N-PORT: %s missing from MF ticker map; using issuer CIK %s",
                ticker,
                cik,
            )
            return {"cik": cik, "seriesId": ""}
        return None

    async def _mf_tickers(self) -> dict[str, dict[str, Any]]:
        if self._mf_index is not None:
            return self._mf_index
        url = str(self._sources()["sec_company_tickers_mf_url"])
        response = await self._http.get(url, headers=self._sec_headers())
        response.raise_for_status()
        payload = response.json()
        indexed: dict[str, dict[str, Any]] = {}
        rows: list[Any]
        if isinstance(payload, dict) and "data" in payload:
            rows = payload["data"]
            fields = payload.get("fields") or []
            for row in rows:
                if isinstance(row, dict):
                    rec = row
                elif isinstance(row, list) and fields:
                    rec = {fields[i]: row[i] for i in range(min(len(fields), len(row)))}
                else:
                    continue
                for key in ("ticker", "symbol", "classTicker"):
                    t = str(rec.get(key) or "").upper().strip()
                    if t:
                        indexed[t] = rec
        elif isinstance(payload, dict):
            for rec in payload.values():
                if not isinstance(rec, dict):
                    continue
                for key in ("ticker", "symbol", "classTicker"):
                    t = str(rec.get(key) or "").upper().strip()
                    if t:
                        indexed[t] = rec
        self._mf_index = indexed
        return indexed

    async def _issuer_cik(self, ticker: str) -> str | None:
        if self._issuer_ciks is None:
            url = str(self._sources()["sec_company_tickers_url"])
            response = await self._http.get(url, headers=self._sec_headers())
            response.raise_for_status()
            payload = response.json()
            mapping: dict[str, str] = {}
            rows = payload.values() if isinstance(payload, dict) else payload
            for rec in rows:
                if not isinstance(rec, dict):
                    continue
                t = str(rec.get("ticker") or "").upper().strip()
                cik = rec.get("cik_str") or rec.get("cik")
                if t and cik is not None:
                    mapping[t] = str(cik)
            self._issuer_ciks = mapping
        return self._issuer_ciks.get(ticker.upper())

    def _sec_headers(self) -> dict[str, str]:
        return self._policy.edgar_headers()

    async def _latest_nport_report(self, meta: dict[str, Any]) -> NportReport | None:
        series_id = str(meta.get("seriesId") or meta.get("series_id") or "").strip()
        cik = _cik10(meta.get("cik_str") or meta.get("cik") or meta.get("cikStr"))
        cik_int = str(int(cik))
        archives_tmpl = str(self._sources()["sec_archives_url"])

        for forms, accessions, documents in await self._nport_filing_lists(cik):
            for form, accession, document in zip(
                forms, accessions, documents, strict=False
            ):
                if str(form).upper() not in {"NPORT-P", "NPORT-P/A"}:
                    continue
                file_url = archives_tmpl.format(
                    cik=cik_int,
                    accession=_accession_nodash(str(accession)),
                    document=nport_xml_document(str(document)),
                )
                doc = await self._http.get(file_url, headers=self._sec_headers())
                if doc.status_code >= 400:
                    continue
                try:
                    report = parse_nport_xml(doc.text)
                except ET.ParseError:
                    continue
                if series_id and report.series_id and report.series_id != series_id:
                    continue
                if report.holdings:
                    return report
        return None

    async def _fill_tickers_from_cusip(self, report: NportReport) -> None:
        """NPORT-P often has CUSIP and empty ticker; map via free OpenFIGI."""
        needed: list[str] = []
        for holding in report.holdings:
            if holding.ticker or not holding.cusip:
                continue
            cached = self._cusip_tickers.get(holding.cusip)
            if cached:
                holding.ticker = cached
            elif holding.cusip not in needed:
                needed.append(holding.cusip)
        if not needed:
            return
        mapped = await self._openfigi_cusips(needed)
        self._cusip_tickers.update(mapped)
        _save_cusip_cache(self._cache_path, self._cusip_tickers)
        for holding in report.holdings:
            if holding.ticker or not holding.cusip:
                continue
            ticker = mapped.get(holding.cusip) or self._cusip_tickers.get(holding.cusip)
            if ticker:
                holding.ticker = ticker

    async def _openfigi_cusips(self, cusips: list[str]) -> dict[str, str]:
        url = str(self._sources().get("openfigi_mapping_url") or "")
        if not url:
            return {}
        mapped: dict[str, str] = {}
        batch_size = 10
        wait = float(self._sources().get("openfigi_retry_seconds") or 65)
        for i in range(0, len(cusips), batch_size):
            chunk = cusips[i : i + batch_size]
            payload = [{"idType": "ID_CUSIP", "idValue": c} for c in chunk]
            rows: Any = None
            for attempt in range(4):
                try:
                    response = await self._http.post(
                        url,
                        headers={"Content-Type": "application/json"},
                        json=payload,
                    )
                except Exception as exc:
                    logger.warning("OpenFIGI mapping failed: %s", exc)
                    break
                if response.status_code == 429:
                    logger.warning(
                        "OpenFIGI rate-limited; retrying in %.0fs (attempt %s)",
                        wait,
                        attempt + 1,
                    )
                    await self._sleep(wait)
                    continue
                if response.status_code >= 400:
                    logger.warning("OpenFIGI HTTP %s", response.status_code)
                    break
                try:
                    rows = response.json()
                except ValueError:
                    rows = None
                break
            if not isinstance(rows, list):
                continue
            for cusip, row in zip(chunk, rows, strict=False):
                ticker = _openfigi_us_ticker(row)
                if ticker:
                    mapped[cusip] = ticker
        return mapped

    async def _nport_filing_lists(
        self, cik: str
    ) -> list[tuple[list[Any], list[Any], list[Any]]]:
        url = str(self._sources()["sec_submissions_url"]).format(cik=cik)
        response = await self._http.get(url, headers=self._sec_headers())
        response.raise_for_status()
        data = response.json()
        payloads: list[Any] = [data]
        files = (data.get("filings") or {}).get("files") or []
        base = url.rsplit("/", 1)[0]
        for item in files:
            name = item.get("name") if isinstance(item, dict) else None
            if not name:
                continue
            extra = await self._http.get(f"{base}/{name}", headers=self._sec_headers())
            if extra.status_code >= 400:
                continue
            try:
                payloads.append(extra.json())
            except ValueError:
                continue
        lists: list[tuple[list[Any], list[Any], list[Any]]] = []
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            recent = (payload.get("filings") or {}).get("recent")
            src = recent if isinstance(recent, dict) and recent.get("form") else payload
            lists.append(
                (
                    list(src.get("form") or []),
                    list(src.get("accessionNumber") or []),
                    list(src.get("primaryDocument") or []),
                )
            )
        return lists
