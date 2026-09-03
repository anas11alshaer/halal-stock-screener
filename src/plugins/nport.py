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

from plugins.base import (
    HoldingScreenMode,
    Plugin,
    PluginVote,
    ScreenContext,
    Vote,
    is_fund,
)

logger = logging.getLogger(__name__)

ScreenHolding = Callable[[str, int, HoldingScreenMode, bool], Awaitable[Any]]


@dataclass
class Holding:
    name: str
    pct: float  # fraction of NAV in 0..1
    ticker: str | None = None
    asset_cat: str | None = None
    cusip: str | None = None


@dataclass
class _HoldingTally:
    activity_failed: list[str] = field(default_factory=list)
    nested_fund_failed: list[str] = field(default_factory=list)
    junk_ids: list[str] = field(default_factory=list)
    ratio_fail_weight: float = 0.0
    unresolved_ratio_weight: float = 0.0
    junk_weight: float = 0.0


def score_holding(
    holding: Holding,
    result: Any,
    mode: HoldingScreenMode,
    fund_quote_types: set[str],
    tally: _HoldingTally,
) -> None:
    # Do not read result.verdict.
    ticker = holding.ticker
    act = result.votes.get("Activity")
    hw = result.votes.get("HalalWallet")
    nport_v = result.votes.get("NportHoldings")
    ratios = result.votes.get("Ratios")
    quote = (result.quote_type or "UNKNOWN").upper()
    if quote == "UNKNOWN":
        if ticker:
            tally.junk_ids.append(ticker)
        tally.junk_weight += holding.pct
        return
    if quote in fund_quote_types or (nport_v is not None and nport_v.vote is Vote.FAIL):
        if ticker:
            tally.nested_fund_failed.append(ticker)
        return
    if act is not None and act.vote is Vote.FAIL and ticker:
        if ticker not in tally.activity_failed:
            tally.activity_failed.append(ticker)
    if hw is not None and hw.vote is Vote.FAIL and ticker:
        if ticker not in tally.activity_failed:
            tally.activity_failed.append(ticker)
    if mode is HoldingScreenMode.ACTIVITY_ONLY or ratios is None:
        return
    if ratios.vote is Vote.FAIL:
        tally.ratio_fail_weight += holding.pct
    elif ratios.vote is Vote.ABSTAIN:
        tally.unresolved_ratio_weight += holding.pct


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

        def _unidentified_skip(holding: Holding) -> bool:
            if holding.ticker:
                return False
            return (holding.asset_cat or "").upper() in skip

        unidentified_skip = [h for h in report.holdings if _unidentified_skip(h)]
        screenable = [h for h in report.holdings if not _unidentified_skip(h)]
        identified = [h for h in screenable if h.ticker]
        skip_weight = sum(h.pct for h in unidentified_skip)
        screenable_weight = sum(h.pct for h in screenable)
        identified_weight = sum(h.pct for h in identified)
        # Skip (cash/T-bills) must not pad the floor; ratio is identified / non-skip.
        coverage = (
            identified_weight / screenable_weight if screenable_weight > 0 else 0.0
        )
        raw_floor = self.policy.get("etf", "coverage_floor")
        if raw_floor is None:
            return PluginVote(
                plugin=self.name,
                vote=Vote.FAIL,
                reason="incomplete N-PORT holdings",
                metrics={
                    "coverage": coverage,
                    "skip_weight": skip_weight,
                    "missing_policy_key": "etf.coverage_floor",
                },
            )
        floor = float(raw_floor)
        metrics: dict[str, Any] = {
            "coverage": coverage,
            "coverage_floor": floor,
            "skip_weight": skip_weight,
            "holding_count": len(report.holdings),
            "identified_count": len(identified),
        }
        slack = 1.0 - floor
        if (
            skip_weight > slack
            or screenable_weight <= 0
            or not identified
            or coverage < floor
        ):
            return PluginVote(
                plugin=self.name,
                vote=Vote.FAIL,
                reason="incomplete N-PORT holdings",
                metrics=metrics,
            )

        weights: dict[str, float] = {}
        for name in (
            "activity_fail_epsilon_weight",
            "ratio_fail_weight_cap",
            "ratio_materiality_weight",
        ):
            raw = self.policy.get("etf", name)
            key = f"etf.{name}"
            if raw is None:
                metrics["missing_policy_key"] = key
                return PluginVote(
                    plugin=self.name,
                    vote=Vote.FAIL,
                    reason="incomplete N-PORT holdings",
                    metrics=metrics,
                )
            try:
                weights[name] = float(raw)
            except (TypeError, ValueError):
                metrics["missing_policy_key"] = key
                return PluginVote(
                    plugin=self.name,
                    vote=Vote.FAIL,
                    reason="incomplete N-PORT holdings",
                    metrics=metrics,
                )
        epsilon = weights["activity_fail_epsilon_weight"]
        ratio_fail_cap = weights["ratio_fail_weight_cap"]
        materiality = weights["ratio_materiality_weight"]

        identified.sort(key=lambda h: h.pct, reverse=True)
        cap = int(
            self.policy.get("finder", "max_holdings_finder_per_etf", default=40) or 40
        )
        material = [h for h in identified if h.pct >= materiality]
        small = [h for h in identified if h.pct < materiality]
        fund_types = self.policy.fund_quote_types
        tally = _HoldingTally()
        for i, holding in enumerate(material):
            assert holding.ticker is not None
            child = await self._screen_holding(
                holding.ticker,
                ctx.depth + 1,
                HoldingScreenMode.FULL,
                i < cap,
            )
            score_holding(holding, child, HoldingScreenMode.FULL, fund_types, tally)
        for holding in small:
            assert holding.ticker is not None
            child = await self._screen_holding(
                holding.ticker,
                ctx.depth + 1,
                HoldingScreenMode.ACTIVITY_ONLY,
                False,
            )
            score_holding(
                holding,
                child,
                HoldingScreenMode.ACTIVITY_ONLY,
                fund_types,
                tally,
            )

        unidentified_weight = screenable_weight - identified_weight
        unidentified_is_uncovered = self.policy.get(
            "etf", "unidentified_is_uncovered", default=True
        )
        if unidentified_is_uncovered is None:
            unidentified_is_uncovered = True
        uncovered_weight = tally.unresolved_ratio_weight + tally.junk_weight
        if unidentified_is_uncovered:
            uncovered_weight += unidentified_weight
        coverage = (
            (identified_weight - tally.unresolved_ratio_weight - tally.junk_weight)
            / screenable_weight
            if screenable_weight > 0
            else 0.0
        )
        metrics["coverage"] = coverage
        metrics["activity_failed_holdings"] = tally.activity_failed[:20]
        metrics["nested_fund_failed"] = tally.nested_fund_failed[:20]
        metrics["ratio_fail_weight"] = tally.ratio_fail_weight
        metrics["unresolved_ratio_weight"] = tally.unresolved_ratio_weight
        metrics["uncovered_weight"] = uncovered_weight
        metrics["junk_ids"] = tally.junk_ids[:20]

        if coverage < floor:
            return PluginVote(
                plugin=self.name,
                vote=Vote.FAIL,
                reason="incomplete N-PORT holdings",
                metrics=metrics,
            )

        weight_by_ticker = {h.ticker: h.pct for h in identified if h.ticker}

        def _at_or_above_epsilon(names: list[str]) -> bool:
            return any(weight_by_ticker.get(name, 0.0) >= epsilon for name in names)

        if _at_or_above_epsilon(tally.nested_fund_failed):
            return PluginVote(
                plugin=self.name,
                vote=Vote.FAIL,
                reason="nested fund holding(s)",
                metrics=metrics,
            )
        if _at_or_above_epsilon(tally.activity_failed):
            return PluginVote(
                plugin=self.name,
                vote=Vote.FAIL,
                reason="activity-fail holding(s)",
                metrics=metrics,
            )
        if tally.ratio_fail_weight >= ratio_fail_cap:
            return PluginVote(
                plugin=self.name,
                vote=Vote.FAIL,
                reason="ratio-fail weight at or above cap",
                metrics=metrics,
            )
        return PluginVote(
            plugin=self.name,
            vote=Vote.PASS,
            reason="holdings covered",
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
    # CUSIP cache is CUSIP→ticker only.
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(mapping, indent=0, sort_keys=True), encoding="utf-8")
    except OSError:
        logger.warning("Could not write CUSIP cache %s", path)


def _load_ticker_aliases(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid N-PORT ticker alias file {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"N-PORT ticker alias file {path} must be a JSON object")
    aliases: dict[str, str] = {}
    for key, value in payload.items():
        src = str(key).strip().upper()
        if not src or not isinstance(value, str) or not value.strip():
            raise ValueError(f"invalid N-PORT ticker alias mapping {key!r}")
        aliases[src] = value.strip().upper()
    return aliases


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
        alias_path: Path | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._policy = policy
        self._http = http
        self._mf_index: dict[str, dict[str, Any]] | None = None
        self._sleep = sleep or asyncio.sleep
        if cache_path is None or alias_path is None:
            from config import DATA_DIR

            if cache_path is None:
                cache_path = DATA_DIR / "openfigi_cusip_tickers.json"
            if alias_path is None:
                alias_path = DATA_DIR / "nport_ticker_aliases.json"
        self._cache_path = cache_path
        self._alias_path = alias_path
        self._cusip_tickers = _load_cusip_cache(cache_path)
        self._ticker_aliases = _load_ticker_aliases(alias_path)

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
            self._apply_ticker_aliases(report)
            return report
        except Exception as exc:
            logger.warning("N-PORT fetch failed for %s: %s", ticker, exc)
            return None

    async def _lookup_fund(self, ticker: str) -> dict[str, Any] | None:
        index = await self._mf_tickers()
        row = index.get(ticker)
        if not row:
            return None
        series_id = str(row.get("seriesId") or row.get("series_id") or "").strip()
        if not series_id:
            logger.warning("N-PORT: %s has no series id in MF ticker map", ticker)
            return None
        return row

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
                if series_id and report.series_id != series_id:
                    continue
                if report.holdings:
                    return report
        return None

    async def _fill_tickers_from_cusip(self, report: NportReport) -> None:
        """NPORT-P often has CUSIP and empty ticker; live path is cache-only."""
        for holding in report.holdings:
            if holding.ticker or not holding.cusip:
                continue
            cached = self._cusip_tickers.get(holding.cusip)
            if cached:
                holding.ticker = cached

    def _apply_ticker_aliases(self, report: NportReport) -> None:
        aliases = self._ticker_aliases
        if not aliases:
            return
        for holding in report.holdings:
            ticker = holding.ticker
            if not ticker:
                continue
            aliased = aliases.get(ticker.upper())
            if aliased:
                holding.ticker = aliased

    async def warm_cusip_cache(self, cusips: list[str]) -> dict[str, str]:
        """Map CUSIPs via OpenFIGI offline. Not called from holdings()."""
        needed: list[str] = []
        seen: set[str] = set()
        for cusip in cusips:
            if not cusip or cusip in self._cusip_tickers or cusip in seen:
                continue
            seen.add(cusip)
            needed.append(cusip)
        if not needed:
            return {}
        mapped = await self._openfigi_cusips(needed)
        self._cusip_tickers.update(mapped)
        _save_cusip_cache(self._cache_path, self._cusip_tickers)
        return mapped

    def _openfigi_wait(self, response: httpx.Response, default: float) -> float:
        for header in ("Retry-After", "ratelimit-reset"):
            raw = response.headers.get(header)
            if not raw:
                continue
            try:
                return float(raw)
            except ValueError:
                continue
        return default

    async def _openfigi_cusips(self, cusips: list[str]) -> dict[str, str]:
        url = str(self._sources().get("openfigi_mapping_url") or "")
        if not url:
            return {}
        from config import OPENFIGI_API_KEY

        api_key = (OPENFIGI_API_KEY or "").strip()
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["X-OPENFIGI-APIKEY"] = api_key
        mapped: dict[str, str] = {}
        batch_size = 100 if api_key else 10
        wait = float(self._sources().get("openfigi_retry_seconds") or 65)
        attempts = 4
        # Anonymous mapping is 25 req/min; pace so the warmer does not 429 immediately.
        inter_batch = 0.0 if api_key else 60.0 / 25.0
        for i in range(0, len(cusips), batch_size):
            chunk = cusips[i : i + batch_size]
            payload = [{"idType": "ID_CUSIP", "idValue": c} for c in chunk]
            rows: Any = None
            response: httpx.Response | None = None
            for attempt in range(attempts):
                try:
                    response = await self._http.post(
                        url,
                        headers=headers,
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
                    if attempt + 1 < attempts:
                        await self._sleep(self._openfigi_wait(response, wait))
                    continue
                if response.status_code >= 400:
                    logger.warning("OpenFIGI HTTP %s", response.status_code)
                    break
                try:
                    rows = response.json()
                except ValueError:
                    rows = None
                break
            if isinstance(rows, list):
                for cusip, row in zip(chunk, rows, strict=False):
                    ticker = _openfigi_us_ticker(row)
                    if ticker:
                        mapped[cusip] = ticker
            more = i + batch_size < len(cusips)
            if more and response is not None and response.status_code < 400:
                remaining = response.headers.get("ratelimit-remaining")
                if remaining == "0":
                    await self._sleep(self._openfigi_wait(response, wait))
                elif inter_batch:
                    await self._sleep(inter_batch)
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
