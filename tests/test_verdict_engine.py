"""Eval-engine tests from Issue #7 acceptance (offline, no live network)."""

from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from config import EVAL_FIXTURES_PATH, POLICY_PATH
from fusion import HALAL, NOT_HALAL, fuse_conjunction, required_plugin_names
from market_data import YFinanceMarketData
from plugins.base import Fundamentals, PluginVote, Vote
from plugins.nport import (
    Holding,
    NportClient,
    NportReport,
    SecNportClient,
    nport_xml_document,
    parse_nport_xml,
)
from policy import load_policy
from verdict_engine import ScreenResult, VerdictEngine, format_screen_table

ROOT = Path(__file__).resolve().parent.parent


class FakeMarket:
    def __init__(self, items: dict[str, Fundamentals]) -> None:
        self.items = {key.upper(): value for key, value in items.items()}

    async def get(self, ticker: str) -> Fundamentals:
        found = self.items.get(ticker.upper())
        if found is None:
            return Fundamentals(ticker=ticker.upper(), quote_type="UNKNOWN")
        return found


class FakeNport(NportClient):
    def __init__(self, reports: dict[str, NportReport | None]) -> None:
        self.reports = {key.upper(): value for key, value in reports.items()}

    async def holdings(self, ticker: str) -> NportReport | None:
        return self.reports.get(ticker.upper())


def _ok_equity(ticker: str = "AAPL", **overrides) -> Fundamentals:
    data = dict(
        ticker=ticker,
        quote_type="EQUITY",
        sector="Technology",
        industry="Consumer Electronics",
        company_name="Test Co",
        market_cap=1000.0,
        total_debt=100.0,
        cash_and_securities=50.0,
        interest_income=10.0,
        revenue=500.0,
    )
    data.update(overrides)
    return Fundamentals(**data)


def _engine(
    profiles: dict[str, Fundamentals],
    *,
    hw_records: dict | None = None,
    nport: NportClient | None = None,
    policy=None,
) -> VerdictEngine:
    return VerdictEngine(
        policy or load_policy(POLICY_PATH),
        market=FakeMarket(profiles),
        nport=nport or FakeNport({}),
        halalwallet_records={} if hw_records is None else hw_records,
    )


def test_fusion_any_fail_is_not_halal() -> None:
    votes = {
        "Activity": PluginVote("Activity", Vote.PASS),
        "Ratios": PluginVote("Ratios", Vote.FAIL, reason="debt"),
    }
    assert fuse_conjunction(votes, ["Activity", "Ratios"]) == NOT_HALAL


def test_fusion_optional_fail_is_not_halal() -> None:
    votes = {
        "Activity": PluginVote("Activity", Vote.PASS),
        "Ratios": PluginVote("Ratios", Vote.PASS),
        "HalalWallet": PluginVote("HalalWallet", Vote.FAIL),
    }
    assert fuse_conjunction(votes, ["Activity", "Ratios"]) == NOT_HALAL


def test_fusion_empty_required_is_not_halal() -> None:
    votes = {
        "Activity": PluginVote("Activity", Vote.ABSTAIN),
        "Ratios": PluginVote("Ratios", Vote.ABSTAIN),
    }
    assert fuse_conjunction(votes, []) == NOT_HALAL


def test_fusion_required_pass_none_abstain_is_halal() -> None:
    votes = {
        "Activity": PluginVote("Activity", Vote.PASS),
        "Ratios": PluginVote("Ratios", Vote.PASS),
        "HalalWallet": PluginVote("HalalWallet", Vote.ABSTAIN),
    }
    assert fuse_conjunction(votes, ["Activity", "Ratios"]) == HALAL


def test_fusion_required_abstain_is_not_halal() -> None:
    votes = {
        "Activity": PluginVote("Activity", Vote.PASS),
        "Ratios": PluginVote("Ratios", Vote.ABSTAIN, reason="missing interest-income"),
    }
    assert fuse_conjunction(votes, ["Activity", "Ratios"]) == NOT_HALAL


@pytest.mark.asyncio
async def test_missing_interest_income_ratios_abstain_not_halal() -> None:
    engine = _engine({"AAPL": _ok_equity(interest_income=None)})
    result = await engine.screen("AAPL")
    assert result.votes["Ratios"].vote is Vote.ABSTAIN
    assert "interest-income" in result.votes["Ratios"].reason
    assert result.verdict == NOT_HALAL


@pytest.mark.asyncio
async def test_policy_thresholds_not_hardcoded_in_plugin() -> None:
    policy = load_policy(POLICY_PATH)
    engine = _engine({"AAPL": _ok_equity()}, policy=policy)
    assert (await engine.screen("AAPL")).votes["Ratios"].vote is Vote.PASS

    policy.raw["ratios"]["debt_to_market_cap_pct"] = 5
    engine = _engine({"AAPL": _ok_equity()}, policy=policy)
    result = await engine.screen("AAPL")
    assert result.votes["Ratios"].vote is Vote.FAIL
    assert result.verdict == NOT_HALAL


@pytest.mark.asyncio
async def test_empty_activity_denylist_lets_bank_pass() -> None:
    policy = load_policy(POLICY_PATH)
    policy.raw["activity"]["denied_sectors"] = []
    policy.raw["activity"]["denied_industries"] = []
    engine = _engine(
        {
            "JPM": _ok_equity(
                ticker="JPM",
                sector="Financial Services",
                industry="Banks - Diversified",
            )
        },
        policy=policy,
    )
    result = await engine.screen("JPM")
    assert result.votes["Activity"].vote is Vote.PASS


@pytest.mark.asyncio
async def test_halalwallet_required_when_ticker_in_dataset() -> None:
    records = {
        "AAPL": {
            "ticker": "AAPL",
            "verdict": "not_halal",
            "businessActivityPass": False,
            "financialScreenPass": False,
        }
    }
    engine = _engine({"AAPL": _ok_equity()}, hw_records=records)
    result = await engine.screen("AAPL")
    assert result.votes["HalalWallet"].vote is Vote.FAIL
    assert "HalalWallet" in result.required
    assert result.verdict == NOT_HALAL


@pytest.mark.asyncio
async def test_halalwallet_absent_from_dataset_is_not_required() -> None:
    engine = _engine({"AAPL": _ok_equity()}, hw_records={})
    result = await engine.screen("AAPL")
    assert result.votes["HalalWallet"].vote is Vote.ABSTAIN
    assert "HalalWallet" not in result.required
    assert result.verdict == HALAL


@pytest.mark.asyncio
async def test_halalwallet_in_dataset_abstain_is_required_not_halal() -> None:
    records = {
        "AAPL": {
            "ticker": "AAPL",
            "verdict": "unknown",
        }
    }
    engine = _engine({"AAPL": _ok_equity()}, hw_records=records)
    result = await engine.screen("AAPL")
    assert result.votes["HalalWallet"].vote is Vote.ABSTAIN
    assert "HalalWallet" in result.required
    assert result.verdict == NOT_HALAL


@pytest.mark.asyncio
async def test_conjunction_bank_activity_fail() -> None:
    engine = _engine(
        {
            "JPM": _ok_equity(
                ticker="JPM",
                sector="Financial Services",
                industry="Banks - Diversified",
            )
        }
    )
    result = await engine.screen("JPM")
    assert result.votes["Activity"].vote is Vote.FAIL
    assert result.verdict == NOT_HALAL


@pytest.mark.asyncio
async def test_engine_never_emits_halal_from_seo_or_jsonld() -> None:
    """New engine has no HTML/SEO/JSON-LD parser and does not call Musaffa/Zoya."""
    engine_src = (ROOT / "src" / "verdict_engine.py").read_text(encoding="utf-8")
    blob = engine_src.casefold()
    assert "musaffa" not in blob
    assert "zoya" not in blob
    assert "application/ld+json" not in blob
    assert "<meta" not in blob

    for path in (ROOT / "src" / "plugins").glob("*.py"):
        text = path.read_text(encoding="utf-8").casefold()
        assert "application/ld+json" not in text
        assert "musaffa.com" not in text
        assert "zoya.finance" not in text

    seo_html = (
        '<meta name="description" content="Is AAPL halal? AAPL is Shariah compliant">'
        '<script type="application/ld+json">{"text":"AAPL is HALAL"}</script>'
    )
    with (
        patch("scrapers.musaffa.MusaffaScraper.screen_ticker") as musaffa,
        patch("scrapers.zoya.ZoyaScraper.screen_ticker") as zoya,
    ):
        missing = _engine(
            {"AAPL": _ok_equity(interest_income=None, company_name=seo_html)}
        )
        closed = await missing.screen("AAPL")
        passing = _engine({"AAPL": _ok_equity(company_name=seo_html)})
        ok = await passing.screen("AAPL")
    musaffa.assert_not_called()
    zoya.assert_not_called()
    assert closed.verdict == NOT_HALAL
    assert closed.votes["Ratios"].vote is Vote.ABSTAIN
    assert "interest-income" in closed.votes["Ratios"].reason
    assert "HALAL" not in closed.votes["Activity"].reason
    assert "HALAL" not in closed.votes["Ratios"].reason
    assert ok.verdict == HALAL
    assert ok.votes["Activity"].vote is Vote.PASS
    assert ok.votes["Ratios"].vote is Vote.PASS


@pytest.mark.asyncio
async def test_incomplete_nport_holdings_not_halal() -> None:
    report = NportReport(
        series_id="S000001",
        holdings=[
            Holding(name="Apple", pct=0.90, ticker="AAPL", asset_cat="EC"),
            Holding(name="Unknown", pct=0.09, ticker=None, asset_cat="EC"),
        ],
    )
    engine = _engine(
        {
            "SPY": Fundamentals(
                ticker="SPY", quote_type="ETF", sector="Financial Services"
            ),
            "AAPL": _ok_equity(),
        },
        nport=FakeNport({"SPY": report}),
    )
    result = await engine.screen("SPY")
    assert result.votes["NportHoldings"].vote is Vote.FAIL
    assert "incomplete" in result.votes["NportHoldings"].reason
    assert result.votes["NportHoldings"].metrics["coverage"] == pytest.approx(0.90)
    assert result.verdict == NOT_HALAL


@pytest.mark.asyncio
async def test_missing_or_empty_nport_report_not_halal() -> None:
    profiles = {
        "SPY": Fundamentals(
            ticker="SPY", quote_type="ETF", sector="Financial Services"
        ),
    }
    missing = await _engine(profiles, nport=FakeNport({"SPY": None})).screen("SPY")
    empty = await _engine(
        profiles, nport=FakeNport({"SPY": NportReport(series_id="S1", holdings=[])})
    ).screen("SPY")
    assert missing.votes["NportHoldings"].vote is Vote.FAIL
    assert empty.votes["NportHoldings"].vote is Vote.FAIL
    assert missing.verdict == NOT_HALAL
    assert empty.verdict == NOT_HALAL


@pytest.mark.asyncio
async def test_coverage_floor_comes_from_policy() -> None:
    report = NportReport(
        series_id="S000001",
        holdings=[
            Holding(name="Apple", pct=0.10, ticker="AAPL", asset_cat="EC"),
            Holding(name="Unknown", pct=0.90, ticker=None, asset_cat="EC"),
        ],
    )
    profiles = {
        "SPY": Fundamentals(
            ticker="SPY", quote_type="ETF", sector="Financial Services"
        ),
        "AAPL": _ok_equity(),
    }
    default_engine = _engine(profiles, nport=FakeNport({"SPY": report}))
    default = await default_engine.screen("SPY")
    assert default.votes["NportHoldings"].vote is Vote.FAIL

    policy = load_policy(POLICY_PATH)
    policy.raw["etf"]["coverage_floor"] = 0.05
    low_floor = await _engine(
        profiles, nport=FakeNport({"SPY": report}), policy=policy
    ).screen("SPY")
    assert low_floor.votes["NportHoldings"].vote is Vote.PASS
    assert low_floor.verdict == HALAL

    missing_key = load_policy(POLICY_PATH)
    del missing_key.raw["etf"]["coverage_floor"]
    no_floor = await _engine(
        profiles, nport=FakeNport({"SPY": report}), policy=missing_key
    ).screen("SPY")
    assert no_floor.votes["NportHoldings"].vote is Vote.FAIL
    assert no_floor.verdict == NOT_HALAL


@pytest.mark.asyncio
async def test_nport_lookthrough_pass_when_holdings_halal() -> None:
    report = NportReport(
        series_id="S000001",
        holdings=[
            Holding(name="Apple", pct=0.98, ticker="AAPL", asset_cat="EC"),
            Holding(name="Cash", pct=0.02, ticker=None, asset_cat="STIV"),
        ],
    )
    engine = _engine(
        {
            "SPY": Fundamentals(
                ticker="SPY", quote_type="ETF", sector="Financial Services"
            ),
            "AAPL": _ok_equity(),
        },
        nport=FakeNport({"SPY": report}),
    )
    result = await engine.screen("SPY")
    assert result.votes["Activity"].vote is Vote.ABSTAIN
    assert result.votes["Ratios"].vote is Vote.ABSTAIN
    assert result.votes["NportHoldings"].vote is Vote.PASS
    assert result.verdict == HALAL


def test_required_plugins_etf_uses_nport() -> None:
    policy = load_policy(POLICY_PATH)
    required = required_plugin_names(
        fusion_section=policy.section("fusion"),
        quote_type="ETF",
        ticker_in_halalwallet=False,
        enabled=policy.enabled_plugins,
    )
    assert required == ["NportHoldings"]
    votes = {
        "Activity": PluginVote("Activity", Vote.ABSTAIN),
        "Ratios": PluginVote("Ratios", Vote.ABSTAIN),
        "NportHoldings": PluginVote("NportHoldings", Vote.PASS),
        "HalalWallet": PluginVote("HalalWallet", Vote.ABSTAIN),
    }
    assert fuse_conjunction(votes, required) == HALAL


def test_eval_fixture_list_has_stocks_and_etfs_and_a_bank() -> None:
    import tomllib

    payload = tomllib.loads(EVAL_FIXTURES_PATH.read_text(encoding="utf-8"))
    stocks = {item.upper() for item in payload["stocks"]}
    etfs = {item.upper() for item in payload["etfs"]}
    assert stocks
    assert etfs
    assert {"JPM", "BAC"} & stocks


def test_parse_nport_xml_percent_scale() -> None:
    xml_text = """<?xml version="1.0"?>
    <edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
      <formData>
        <genInfo><seriesId>S1</seriesId></genInfo>
        <invstOrSecs>
          <invstOrSec>
            <name>APPLE INC</name>
            <identifiers><ticker><value>AAPL</value></ticker></identifiers>
            <pctVal>60</pctVal>
            <assetCat>EC</assetCat>
          </invstOrSec>
          <invstOrSec>
            <name>MICROSOFT</name>
            <identifiers><ticker>MSFT</ticker></identifiers>
            <pctVal>40</pctVal>
            <assetCat>EC</assetCat>
          </invstOrSec>
        </invstOrSecs>
      </formData>
    </edgarSubmission>
    """
    report = parse_nport_xml(xml_text)
    assert report.series_id == "S1"
    by_ticker = {h.ticker: h.pct for h in report.holdings}
    assert by_ticker["AAPL"] == pytest.approx(0.60)
    assert by_ticker["MSFT"] == pytest.approx(0.40)


def test_nport_xml_document_strips_xsl_viewer() -> None:
    assert nport_xml_document("xslFormNPORT-P_X01/primary_doc.xml") == "primary_doc.xml"
    assert nport_xml_document("primary_doc.xml") == "primary_doc.xml"


def test_parse_nport_xml_reads_cusip_when_ticker_missing() -> None:
    xml_text = """<?xml version="1.0"?>
    <edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
      <formData>
        <genInfo><seriesId>S1</seriesId></genInfo>
        <invstOrSecs>
          <invstOrSec>
            <name>APPLE INC</name>
            <cusip>037833100</cusip>
            <pctVal>100</pctVal>
            <assetCat>EC</assetCat>
          </invstOrSec>
        </invstOrSecs>
      </formData>
    </edgarSubmission>
    """
    report = parse_nport_xml(xml_text)
    assert report.holdings[0].ticker is None
    assert report.holdings[0].cusip == "037833100"


def test_format_table_includes_plugin_votes() -> None:
    plugins = ["Activity", "Ratios", "HalalWallet", "NportHoldings"]
    ok = ScreenResult(
        ticker="AAPL",
        quote_type="EQUITY",
        verdict=HALAL,
        votes={
            "Activity": PluginVote("Activity", Vote.PASS, metrics={}),
            "Ratios": PluginVote(
                "Ratios",
                Vote.PASS,
                metrics={"debt_pct": 2.0, "cash_pct": 1.5, "interest_income_pct": 0.4},
            ),
        },
        required=["Activity", "Ratios"],
    )
    bad = ScreenResult(
        ticker="JPM",
        quote_type="EQUITY",
        verdict=NOT_HALAL,
        votes={
            "Activity": PluginVote("Activity", Vote.FAIL, metrics={}),
            "Ratios": PluginVote("Ratios", Vote.FAIL, metrics={}),
        },
        required=["Activity", "Ratios"],
    )
    table = format_screen_table([ok, bad], plugins)
    header = table.splitlines()[0]
    for name in plugins:
        assert name in header
    assert "VERDICT" in header
    assert "AAPL" in table
    assert "PASS" in table
    assert "HALAL" in table
    assert "NOT_HALAL" in table


def test_verdict_engine_is_async_and_not_telegram() -> None:
    assert inspect.iscoroutinefunction(VerdictEngine.screen)
    bot = (ROOT / "src" / "bot.py").read_text(encoding="utf-8")
    assert "VerdictEngine" not in bot
    assert "verdict_engine" not in bot


@pytest.mark.asyncio
async def test_nport_fail_holding_is_not_halal() -> None:
    report = NportReport(
        series_id="S000001",
        holdings=[
            Holding(name="Apple", pct=0.50, ticker="AAPL", asset_cat="EC"),
            Holding(name="JPMorgan", pct=0.48, ticker="JPM", asset_cat="EC"),
            Holding(name="Cash", pct=0.02, ticker=None, asset_cat="STIV"),
        ],
    )
    engine = _engine(
        {
            "SPY": Fundamentals(
                ticker="SPY", quote_type="ETF", sector="Financial Services"
            ),
            "AAPL": _ok_equity(),
            "JPM": _ok_equity(
                ticker="JPM",
                sector="Financial Services",
                industry="Banks - Diversified",
            ),
        },
        nport=FakeNport({"SPY": report}),
    )
    result = await engine.screen("SPY")
    assert result.votes["NportHoldings"].vote is Vote.FAIL
    assert "JPM" in result.votes["NportHoldings"].metrics["failed_holdings"]
    assert result.verdict == NOT_HALAL


def test_plugins_have_no_ticker_allowlist() -> None:
    for path in (ROOT / "src" / "plugins").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "SPUS" not in text
        assert "HLAL" not in text


@pytest.mark.asyncio
async def test_disabled_nport_plugin_cannot_halal_etf() -> None:
    policy = load_policy(POLICY_PATH)
    policy.raw["enabled_plugins"] = ["Activity", "Ratios", "HalalWallet"]
    report = NportReport(
        series_id="S000001",
        holdings=[Holding(name="Apple", pct=0.99, ticker="AAPL", asset_cat="EC")],
    )
    engine = _engine(
        {
            "SPY": Fundamentals(
                ticker="SPY", quote_type="ETF", sector="Financial Services"
            ),
            "AAPL": _ok_equity(),
        },
        nport=FakeNport({"SPY": report}),
        policy=policy,
    )
    result = await engine.screen("SPY")
    assert "NportHoldings" not in result.votes
    assert result.verdict == NOT_HALAL


@pytest.mark.asyncio
async def test_halalwallet_fetch_error_fail_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="nope")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        engine = VerdictEngine(
            load_policy(POLICY_PATH),
            market=FakeMarket({"AAPL": _ok_equity()}),
            nport=FakeNport({}),
            http=http,
        )
        result = await engine.screen("AAPL")
    assert result.votes["HalalWallet"].vote is Vote.ABSTAIN
    assert "unavailable" in result.votes["HalalWallet"].reason
    assert "HalalWallet" in result.required
    assert result.verdict == NOT_HALAL


def test_edgar_user_agent_missing_email_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("config.SEC_CONTACT_EMAIL", "")
    policy = load_policy(POLICY_PATH)
    with pytest.raises(RuntimeError, match="SEC_CONTACT_EMAIL"):
        policy.edgar_user_agent()


def test_edgar_user_agent_uses_env_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("config.SEC_CONTACT_EMAIL", "you@example.com")
    ua = load_policy(POLICY_PATH).edgar_user_agent()
    assert ua == "halal-stock-screener you@example.com"
    assert "(" not in ua
    headers = load_policy(POLICY_PATH).edgar_headers()
    assert headers["User-Agent"] == ua
    assert "Accept" in headers


@pytest.mark.asyncio
async def test_companyfacts_http_error_keeps_yfinance_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("config.SEC_CONTACT_EMAIL", "nport-tests@example.com")
    policy = load_policy(POLICY_PATH)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    class Stub(YFinanceMarketData):
        def _download(self, ticker: str):
            return (
                {
                    "quoteType": "EQUITY",
                    "sector": "Technology",
                    "industry": "Consumer Electronics",
                    "marketCap": 1000.0,
                    "totalDebt": 10.0,
                    "totalCash": 5.0,
                },
                None,
                None,
            )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        fundamentals = await Stub(policy, http=http).get("AAPL")
    assert fundamentals.quote_type == "EQUITY"
    assert fundamentals.sector == "Technology"
    assert fundamentals.interest_income is None


@pytest.mark.asyncio
async def test_nport_skips_unmatched_series_filings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("config.SEC_CONTACT_EMAIL", "nport-tests@example.com")
    policy = load_policy(POLICY_PATH)
    mf = {
        "0": {
            "cik_str": 1234567,
            "ticker": "SPY",
            "seriesId": "S000002",
        }
    }
    submissions = {
        "filings": {
            "recent": {
                "form": ["NPORT-P"] * 10,
                "accessionNumber": [f"000-000-00{i:02d}" for i in range(10)],
                "primaryDocument": [f"doc{i}.xml" for i in range(10)],
            }
        }
    }

    def xml_for(series: str, ticker: str) -> str:
        return (
            '<?xml version="1.0"?><edgarSubmission xmlns="http://www.sec.gov/edgar/nport">'
            f"<formData><genInfo><seriesId>{series}</seriesId></genInfo>"
            "<invstOrSecs><invstOrSec><name>X</name>"
            f"<identifiers><ticker>{ticker}</ticker></identifiers>"
            "<pctVal>100</pctVal><assetCat>EC</assetCat></invstOrSec>"
            "</invstOrSecs></formData></edgarSubmission>"
        )

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("company_tickers_mf.json"):
            return httpx.Response(200, json=mf)
        if "submissions" in url:
            return httpx.Response(200, json=submissions)
        if "doc9.xml" in url:
            return httpx.Response(200, text=xml_for("S000002", "AAPL"))
        if "doc" in url and url.endswith(".xml"):
            return httpx.Response(200, text=xml_for("S000001", "WRONG"))
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = SecNportClient(policy, http)
        report = await client.holdings("SPY")
    assert report is not None
    assert report.series_id == "S000002"
    assert report.holdings[0].ticker == "AAPL"


@pytest.mark.asyncio
async def test_nport_uses_raw_xml_not_xsl_and_maps_cusip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("config.SEC_CONTACT_EMAIL", "nport-tests@example.com")
    policy = load_policy(POLICY_PATH)
    mf = {
        "fields": ["cik", "seriesId", "classId", "symbol"],
        "data": [[99, "S000002", "C1", "QQQ"]],
    }
    submissions = {
        "filings": {
            "recent": {
                "form": ["NPORT-P"],
                "accessionNumber": ["000-000-0001"],
                "primaryDocument": ["xslFormNPORT-P_X01/primary_doc.xml"],
            }
        }
    }
    xml_text = """<?xml version="1.0"?>
    <edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
      <formData>
        <genInfo><seriesId>S000002</seriesId></genInfo>
        <invstOrSecs>
          <invstOrSec>
            <name>APPLE INC</name>
            <cusip>037833100</cusip>
            <pctVal>100</pctVal>
            <assetCat>EC</assetCat>
          </invstOrSec>
        </invstOrSecs>
      </formData>
    </edgarSubmission>
    """
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requested.append(url)
        if url.endswith("company_tickers_mf.json"):
            return httpx.Response(200, json=mf)
        if "submissions" in url:
            return httpx.Response(200, json=submissions)
        if "openfigi.com" in url:
            return httpx.Response(
                200,
                json=[{"data": [{"ticker": "AAPL", "exchCode": "US"}]}],
            )
        if url.endswith("/primary_doc.xml") and "xslForm" not in url:
            return httpx.Response(200, text=xml_text)
        if "xslForm" in url:
            return httpx.Response(200, text="<html><div>S000002 mismatched")
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        report = await SecNportClient(
            policy, http, cache_path=tmp_path / "cusip.json"
        ).holdings("QQQ")
    assert report is not None
    assert report.holdings[0].ticker == "AAPL"
    assert any("xslForm" in url for url in requested) is False
    assert any(url.endswith("/primary_doc.xml") for url in requested)


@pytest.mark.asyncio
async def test_nport_openfigi_retries_after_429(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("config.SEC_CONTACT_EMAIL", "nport-tests@example.com")
    policy = load_policy(POLICY_PATH)
    policy.raw.setdefault("sources", {})["openfigi_retry_seconds"] = 1
    mf = {
        "fields": ["cik", "seriesId", "classId", "symbol"],
        "data": [[99, "S1", "C1", "QQQ"]],
    }
    submissions = {
        "filings": {
            "recent": {
                "form": ["NPORT-P"],
                "accessionNumber": ["000-1"],
                "primaryDocument": ["primary_doc.xml"],
            }
        }
    }
    xml_text = """<?xml version="1.0"?>
    <edgarSubmission>
      <genInfo><seriesId>S1</seriesId></genInfo>
      <invstOrSec>
        <name>APPLE INC</name>
        <cusip>037833100</cusip>
        <pctVal>100</pctVal>
        <assetCat>EC</assetCat>
      </invstOrSec>
    </edgarSubmission>
    """
    hits = {"openfigi": 0}
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("company_tickers_mf.json"):
            return httpx.Response(200, json=mf)
        if "submissions" in url:
            return httpx.Response(200, json=submissions)
        if "openfigi.com" in url:
            hits["openfigi"] += 1
            if hits["openfigi"] == 1:
                return httpx.Response(429, text="rate limited")
            return httpx.Response(
                200,
                json=[{"data": [{"ticker": "AAPL", "exchCode": "US"}]}],
            )
        if url.endswith("primary_doc.xml"):
            return httpx.Response(200, text=xml_text)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        report = await SecNportClient(
            policy,
            http,
            cache_path=tmp_path / "cusip.json",
            sleep=fake_sleep,
        ).holdings("QQQ")
    assert waits == [1]
    assert hits["openfigi"] == 2
    assert report is not None
    assert report.holdings[0].ticker == "AAPL"


@pytest.mark.asyncio
async def test_nport_no_matching_series_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("config.SEC_CONTACT_EMAIL", "nport-tests@example.com")
    policy = load_policy(POLICY_PATH)
    mf = {"0": {"cik_str": 1, "ticker": "SPY", "seriesId": "S999"}}
    submissions = {
        "filings": {
            "recent": {
                "form": ["NPORT-P"] * 9,
                "accessionNumber": [f"000-{i}" for i in range(9)],
                "primaryDocument": [f"d{i}.xml" for i in range(9)],
            }
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("company_tickers_mf.json"):
            return httpx.Response(200, json=mf)
        if "submissions" in url:
            return httpx.Response(200, json=submissions)
        return httpx.Response(
            200,
            text=(
                '<?xml version="1.0"?><edgarSubmission>'
                "<seriesId>S000001</seriesId></edgarSubmission>"
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await SecNportClient(policy, http).holdings("SPY") is None


def test_eval_screener_fixture_list_concatenates_stocks_and_etfs() -> None:
    path = ROOT / "scripts" / "eval_screener.py"
    spec = importlib.util.spec_from_file_location("eval_screener_cli", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tickers = module._load_fixture_tickers(EVAL_FIXTURES_PATH)
    assert {"JPM", "BAC"} & set(tickers)
    assert {"SPY", "SPUS", "HLAL"} & set(tickers)
