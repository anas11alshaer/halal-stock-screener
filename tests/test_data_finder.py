"""DataFinder searcher+checker and Activity segment tags (canned excerpts, no live NIM)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from config import POLICY_PATH
from data_finder import DataFinder
from nvidia_nim import claims_cited, numbers_cited, payload_has_forbidden
from plugins.activity import ActivityPlugin
from plugins.base import (
    DenominatorSource,
    FactState,
    Fundamentals,
    ScreenContext,
    Segment,
    Vote,
)
from policy import load_policy
from test_verdict_engine import FakeMarket, FakeNport, _ok_equity
from verdict_engine import VerdictEngine

XBOX_ITEM1 = "We develop Xbox consoles, Activision titles, and Game Pass interactive entertainment."
INTEREST_ITEM1 = (
    "Interest income was $10. Revenue was $500. Total debt was $100. Cash was $50."
)
GAMING_NOTE = "Gaming revenue was $23.455 billion of total $281.724 billion (8.3%)."
SPORTSBOOK_ITEM1 = "The company operates a sportsbook and igaming platform."
WEAPONS_ITEM1 = (
    "The industrial unit supplies missiles and munitions to allied governments."
)
ADS_TEXT_ITEM1 = "We earn most of our revenue from search advertising and text ads."


class FakeFilings:
    def __init__(self, excerpts: dict[str, dict[str, str]]) -> None:
        self.excerpts = excerpts
        self.calls = 0

    async def tenk_excerpts(
        self, *, ticker: str, cik: str | None = None
    ) -> dict[str, str]:
        self.calls += 1
        found = self.excerpts.get(ticker.upper())
        if found is None:
            found = self.excerpts.get("*", {})
        return dict(found)


class FakeNim:
    def __init__(self, responses: dict[str, str | list[str]]) -> None:
        self.responses = responses
        self.calls: list[str] = []
        self.prompts: list[str] = []
        self._idx: dict[str, int] = {}

    async def chat_text(
        self,
        *,
        model_id: str,
        prompt: str,
        max_tokens: int = 2048,
        url: str | None = None,
    ) -> str:
        self.prompts.append(prompt)
        match = re.search(r"\[job ([^\]]+)\]", prompt)
        job = match.group(1) if match else "unknown"
        self.calls.append(job)
        spec = self.responses.get(job, self.responses.get("*", "{}"))
        if isinstance(spec, list):
            i = self._idx.get(job, 0)
            self._idx[job] = i + 1
            return spec[min(i, len(spec) - 1)]
        return spec


class FakeChecker:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, candidate: dict, excerpt: str) -> dict:
        self.calls.append(candidate)
        raw = json.dumps(candidate)
        if payload_has_forbidden(raw):
            return {"accepted": False, "reason": "forbidden"}
        ok = claims_cited(candidate, excerpt)
        return {"accepted": ok, "reason": "cited" if ok else "uncited"}


async def _fixed_cik(_ticker: str) -> str:
    return "0000789019"


def _finder(
    *,
    excerpts: dict[str, dict[str, str]],
    responses: dict[str, str | list[str]] | None = None,
    checker=None,
    cik_lookup=None,
) -> tuple[DataFinder, FakeNim, FakeChecker, FakeFilings]:
    nim = FakeNim(responses or {"6": '{"segments":[{"name":"Cloud"}]}'})
    check = checker or FakeChecker()
    filings = FakeFilings(excerpts)
    finder = DataFinder(
        load_policy(POLICY_PATH),
        nim,
        filings,
        checker=check,
        cik_lookup=cik_lookup or _fixed_cik,
    )
    return finder, nim, check, filings


def _ctx(fundamentals: Fundamentals) -> ScreenContext:
    return ScreenContext(
        ticker=fundamentals.ticker,
        quote_type=fundamentals.quote_type,
        fundamentals=fundamentals,
    )


def _msft_found() -> Fundamentals:
    return _ok_equity(
        ticker="MSFT",
        sector="Technology",
        industry="Software - Infrastructure",
    )


async def _activity_for(fundamentals: Fundamentals):
    plugin = ActivityPlugin(load_policy(POLICY_PATH))
    return await plugin.vote(_ctx(fundamentals))


@pytest.mark.asyncio
async def test_msft_found_ratios_still_runs_jobs_6_7_12() -> None:
    finder, nim, _checker, _filings = _finder(
        excerpts={"MSFT": {"item1": XBOX_ITEM1}},
        responses={"6": '{"segments":[{"name":"Gaming"}]}'},
    )
    fundamentals = await finder.enrich(_ctx(_msft_found()))
    assert "6" in finder.jobs_run
    assert "7" in finder.jobs_run
    assert "12" in finder.jobs_run
    assert nim.calls.count("6") >= 1
    tags = [tag for seg in fundamentals.segments for tag in seg.tags]
    assert "gaming_interactive_entertainment" in tags


@pytest.mark.asyncio
async def test_item1_xbox_allow_without_pct_activity_pass() -> None:
    vote = await _activity_for(
        _ok_equity(
            ticker="MSFT",
            sector="Technology",
            industry="Software - Infrastructure",
            segments=[
                Segment(
                    name="Item 1",
                    revenue=None,
                    revenue_pct=None,
                    tags=["gaming_interactive_entertainment"],
                    state=FactState.FOUND,
                )
            ],
        )
    )
    assert vote.vote is Vote.PASS


@pytest.mark.asyncio
async def test_unquantified_online_gambling_activity_abstain() -> None:
    vote = await _activity_for(
        _ok_equity(
            segments=[
                Segment(
                    name="Betting",
                    revenue=None,
                    revenue_pct=None,
                    tags=["online_gambling_operator"],
                    state=FactState.FOUND,
                )
            ]
        )
    )
    assert vote.vote is Vote.ABSTAIN
    assert "unquantified" in vote.reason


@pytest.mark.asyncio
async def test_unquantified_weapons_is_not_activity_fail() -> None:
    vote = await _activity_for(
        _ok_equity(
            ticker="CAT",
            sector="Industrials",
            industry="Farm & Heavy Construction Machinery",
            segments=[
                Segment(
                    name="Defense parts",
                    revenue=None,
                    revenue_pct=None,
                    tags=["weapons"],
                    state=FactState.FOUND,
                )
            ],
        )
    )
    assert vote.vote is not Vote.FAIL
    assert vote.vote is Vote.PASS


@pytest.mark.asyncio
async def test_googl_ads_text_allow_does_not_core_fail() -> None:
    vote = await _activity_for(
        _ok_equity(
            ticker="GOOGL",
            sector="Communication Services",
            industry="Internet Content & Information",
            segments=[
                Segment(
                    name="Search",
                    revenue=None,
                    revenue_pct=None,
                    tags=["ads_text"],
                    state=FactState.FOUND,
                )
            ],
        )
    )
    assert vote.vote is Vote.PASS


@pytest.mark.asyncio
async def test_job12_keywords_from_policy_table() -> None:
    finder, _nim, _checker, _filings = _finder(
        excerpts={"X": {"item1": SPORTSBOOK_ITEM1}},
        responses={"6": '{"segments":[]}'},
    )
    fundamentals = await finder.enrich(_ctx(_ok_equity(ticker="X")))
    tags = [tag for seg in fundamentals.segments for tag in seg.tags]
    assert "online_gambling_operator" in tags
    vote = await _activity_for(fundamentals)
    assert vote.vote is Vote.ABSTAIN


@pytest.mark.asyncio
async def test_invented_number_checker_reject_stays_doubtful() -> None:
    equity = _ok_equity(interest_income=None, accounts_receivable=1.0)
    equity.segments = [
        Segment("Cloud", 1.0, 1.0, [], FactState.FOUND),
    ]
    equity.fact_states["segments"] = FactState.FOUND
    equity.fact_states["interest_income"] = FactState.MISSING
    finder, _nim, checker, _filings = _finder(
        excerpts={"AAPL": {"item1": INTEREST_ITEM1}},
        responses={"3": '{"field":"interest_income","value":999000000000}'},
    )
    fundamentals = await finder.enrich(_ctx(equity))
    assert checker.calls, "checker must run; invented numbers must not skip proof"
    assert fundamentals.interest_income is None
    assert fundamentals.fact_states["interest_income"] is FactState.DOUBTFUL
    assert fundamentals.fact_states["interest_income"] is not FactState.FOUND
    for candidate in checker.calls:
        assert numbers_cited(candidate, INTEREST_ITEM1) is False


@pytest.mark.asyncio
async def test_cited_23455b_checker_accept_is_found() -> None:
    finder, _nim, checker, _filings = _finder(
        excerpts={"MSFT": {"item1": XBOX_ITEM1, "segments": GAMING_NOTE}},
        responses={
            "6": '{"segments":[{"name":"Gaming","revenue":23455000000,"pct":8.3}]}'
        },
    )
    fundamentals = await finder.enrich(_ctx(_msft_found()))
    assert checker.calls
    assert any(
        seg.revenue is not None and abs(seg.revenue - 23_455_000_000) < 1
        for seg in fundamentals.segments
    )
    assert fundamentals.fact_states.get("segments") is FactState.FOUND


@pytest.mark.asyncio
async def test_halal_and_core_fail_rejected_from_both_roles() -> None:
    finder, _nim, checker, _filings = _finder(
        excerpts={"AAPL": {"item1": "We sell office software to enterprises."}},
        responses={
            "6": '{"segments":[{"name":"Office"}],"verdict":"HALAL"}',
        },
    )
    fundamentals = await finder.enrich(_ctx(_ok_equity(accounts_receivable=1.0)))
    assert all(seg.name != "Office" for seg in fundamentals.segments)
    assert fundamentals.fact_states.get("segments") is FactState.DOUBTFUL
    assert checker.calls == []

    async def blessing_checker(candidate: dict, excerpt: str) -> dict:
        return {"accepted": True, "reason": "core_fail ignored, HALAL"}

    finder2, _nim2, _c2, _f2 = _finder(
        excerpts={"AAPL": {"item1": "We sell office software to enterprises."}},
        responses={"6": '{"segments":[{"name":"Office"}]}'},
    )
    finder2._checker = blessing_checker
    out = await finder2.enrich(_ctx(_ok_equity(accounts_receivable=1.0)))
    assert all(seg.name != "Office" for seg in out.segments)


@pytest.mark.asyncio
async def test_searcher_cap_never_drops_jobs_6_and_12() -> None:
    equity = Fundamentals(
        ticker="HOLE",
        quote_type="EQUITY",
        sector=None,
        industry=None,
        market_cap=1000.0,
        trailing_avg_market_cap=1000.0,
        denominator_source=DenominatorSource.TRAILING_AVG,
        total_debt=None,
        cash_and_securities=None,
        accounts_receivable=None,
        interest_income=None,
        revenue=None,
        fact_states={
            "interest_income": FactState.MISSING,
            "revenue": FactState.MISSING,
            "total_debt": FactState.MISSING,
            "cash_and_securities": FactState.MISSING,
            "accounts_receivable": FactState.MISSING,
            "segments": FactState.MISSING,
        },
    )
    excerpt = (
        INTEREST_ITEM1 + " Sector Technology. Industry Consumer Electronics. Cloud."
    )
    finder, nim, _checker, _filings = _finder(
        excerpts={"HOLE": {"item1": excerpt}},
        responses={
            "6": '{"segments":[{"name":"Cloud"}]}',
            "3": (
                '{"interest_income":10,"revenue":500,"total_debt":100,'
                '"cash_and_securities":50}'
            ),
            "5": '{"sector":"Technology","industry":"Consumer Electronics"}',
            "4": '{"accounts_receivable":1}',
        },
    )
    await finder.enrich(_ctx(equity))
    assert "6" in finder.jobs_run
    assert "12" in finder.jobs_run
    assert "4" not in finder.jobs_run
    assert nim.calls.count("4") == 0
    assert finder.searcher_calls <= 4
    assert nim.calls.count("6") >= 1


@pytest.mark.asyncio
async def test_cik_cache_second_ticker_skips_jobs_6_and_12_nim() -> None:
    excerpts = {"MSFT": {"item1": XBOX_ITEM1}, "MSFTW": {"item1": XBOX_ITEM1}}
    finder, nim, _checker, filings = _finder(
        excerpts=excerpts,
        responses={"6": '{"segments":[{"name":"Gaming"}]}'},
        cik_lookup=_fixed_cik,
    )
    await finder.enrich(_ctx(_msft_found()))
    first_six = nim.calls.count("6")
    assert first_six >= 1
    second = _ok_equity(
        ticker="MSFTW",
        sector="Technology",
        industry="Software - Infrastructure",
    )
    await finder.enrich(_ctx(second))
    assert nim.calls.count("6") == first_six
    assert "6" not in finder.jobs_run
    assert "12" not in finder.jobs_run


@pytest.mark.asyncio
async def test_yahoo_and_companyfacts_miss_10k_interest_fills_ratios() -> None:
    equity = _ok_equity(ticker="DHI", interest_income=None)
    equity.fact_states["interest_income"] = FactState.MISSING
    finder, _nim, checker, _filings = _finder(
        excerpts={"DHI": {"item1": INTEREST_ITEM1}},
        responses={
            "6": '{"segments":[{"name":"Homebuilding"}]}',
            "3": '{"interest_income":10}',
        },
    )
    engine = VerdictEngine(
        load_policy(POLICY_PATH),
        market=FakeMarket({"DHI": equity}),
        nport=FakeNport({}),
        halalwallet_records={},
        finder=finder,
    )
    result = await engine.screen("DHI")
    assert checker.calls
    assert result.votes["Ratios"].vote is not Vote.ABSTAIN
    assert "interest-income" not in result.votes["Ratios"].reason
    assert result.votes["Ratios"].vote in {Vote.PASS, Vote.FAIL}


@pytest.mark.asyncio
async def test_job12_weapons_and_ads_text_via_item1() -> None:
    weapons_finder, *_ = _finder(
        excerpts={"CAT": {"item1": WEAPONS_ITEM1}},
        responses={"6": '{"segments":[{"name":"Machinery"}]}'},
    )
    weapons = await weapons_finder.enrich(
        _ctx(
            _ok_equity(
                ticker="CAT",
                sector="Industrials",
                industry="Farm & Heavy Construction Machinery",
            )
        )
    )
    assert "weapons" in [tag for seg in weapons.segments for tag in seg.tags]
    assert (await _activity_for(weapons)).vote is Vote.PASS

    ads_finder, *_ = _finder(
        excerpts={"GOOGL": {"item1": ADS_TEXT_ITEM1}},
        responses={"6": '{"segments":[{"name":"Search"}]}'},
    )
    ads = await ads_finder.enrich(
        _ctx(
            _ok_equity(
                ticker="GOOGL",
                sector="Communication Services",
                industry="Internet Content & Information",
            )
        )
    )
    assert "ads_text" in [tag for seg in ads.segments for tag in seg.tags]
    assert (await _activity_for(ads)).vote is Vote.PASS


def test_committed_finder_and_segment_tables() -> None:
    policy = load_policy(POLICY_PATH)
    finder = policy.section("finder")
    assert finder["max_excerpt_chars"] == 12000
    assert finder["max_searcher_calls_per_equity"] == 4
    assert finder["max_checker_calls_per_equity"] == 4
    assert finder["searcher_attempts_per_field"] == 3
    assert finder["max_holdings_finder_per_etf"] == 40
    ids = {
        spec["id"]
        for spec in policy.section("activity").get("segments") or []
        if isinstance(spec, dict) and spec.get("id")
    }
    for needed in (
        "gaming_interactive_entertainment",
        "online_gambling_operator",
        "weapons",
        "ads_text",
        "ads_video",
        "cannabis",
        "music_film",
    ):
        assert needed in ids
    actions = {
        spec["id"]: spec["action"]
        for spec in policy.section("activity").get("segments") or []
        if isinstance(spec, dict)
    }
    assert actions["gaming_interactive_entertainment"] == "allow"
    assert actions["online_gambling_operator"] == "core_fail"
    assert actions["weapons"] == "impure"
    assert actions["ads_text"] == "allow"
    src = (Path(__file__).parent.parent / "src" / "plugins" / "activity.py").read_text(
        encoding="utf-8"
    )
    assert "SPUS" not in src
    assert "HLAL" not in src


@pytest.mark.asyncio
async def test_empty_item1_segments_does_not_skip_note18_mix() -> None:
    finder, nim, checker, _filings = _finder(
        excerpts={"MSFT": {"item1": XBOX_ITEM1, "segments": GAMING_NOTE}},
        responses={
            "6": [
                '{"segments":[]}',
                '{"segments":[{"name":"Gaming","revenue":23455000000,"pct":8.3}]}',
            ]
        },
    )
    fundamentals = await finder.enrich(_ctx(_msft_found()))
    assert nim.calls.count("6") >= 2
    assert len(checker.calls) >= 2
    assert any(
        seg.revenue is not None and abs(seg.revenue - 23_455_000_000) < 1
        for seg in fundamentals.segments
    )
    assert fundamentals.fact_states.get("segments") is FactState.FOUND


@pytest.mark.asyncio
async def test_empty_filings_do_not_stamp_from_empty_excerpt() -> None:
    equity = _ok_equity(sector=None, industry=None)
    finder, nim, checker, _filings = _finder(
        excerpts={"AAPL": {}},
        responses={
            "5": '{"sector":"Technology","industry":"Consumer Electronics"}',
            "6": '{"segments":[{"name":"Cloud"}]}',
        },
    )
    out = await finder.enrich(_ctx(equity))
    assert "6" not in nim.calls
    assert "5" not in nim.calls
    assert checker.calls == []
    assert out.sector is None
    assert out.industry is None
    assert all(seg.name != "Cloud" for seg in out.segments)


@pytest.mark.asyncio
async def test_no_body_non_financial_ratio_holes_are_zero() -> None:
    equity = _ok_equity(interest_income=None)
    equity.fact_states["interest_income"] = FactState.MISSING
    finder, nim, checker, _filings = _finder(
        excerpts={"DHI": {}},
        responses={"3": '{"interest_income":999}'},
    )
    engine = VerdictEngine(
        load_policy(POLICY_PATH),
        market=FakeMarket({"DHI": equity}),
        nport=FakeNport({}),
        halalwallet_records={},
        finder=finder,
    )
    result = await engine.screen("DHI")
    assert nim.calls.count("3") == 0
    assert checker.calls == []
    assert result.votes["Ratios"].vote is not Vote.ABSTAIN
    assert "interest-income" not in result.votes["Ratios"].reason
    filled = await finder.enrich(_ctx(_ok_equity(ticker="DHI", interest_income=None)))
    assert filled.interest_income == 0.0
    assert filled.fact_states["interest_income"] is FactState.ZERO


@pytest.mark.asyncio
async def test_no_body_financial_issuer_does_not_zero_interest() -> None:
    equity = _ok_equity(
        ticker="JPM",
        sector="Financial Services",
        industry="Banks - Diversified",
        interest_income=None,
    )
    equity.fact_states["interest_income"] = FactState.MISSING
    finder, nim, _checker, _filings = _finder(
        excerpts={"JPM": {}},
        responses={"3": '{"interest_income":0}'},
    )
    out = await finder.enrich(_ctx(equity))
    assert nim.calls.count("3") == 0
    assert out.interest_income is None
    assert out.fact_states.get("interest_income") is not FactState.ZERO
    assert out.fact_states.get("interest_income") is FactState.MISSING


class _RaisingFilings:
    def __init__(self) -> None:
        self.calls = 0

    async def tenk_excerpts(
        self, *, ticker: str, cik: str | None = None
    ) -> dict[str, str]:
        self.calls += 1
        raise RuntimeError("HTTP 403 forbidden")


@pytest.mark.asyncio
async def test_edgar_http_failure_keeps_interest_missing() -> None:
    equity = _ok_equity(ticker="DHI", interest_income=None)
    equity.fact_states["interest_income"] = FactState.MISSING
    filings = _RaisingFilings()
    finder = DataFinder(
        load_policy(POLICY_PATH),
        FakeNim({"3": '{"interest_income":0}'}),
        filings,
        checker=FakeChecker(),
        cik_lookup=_fixed_cik,
    )
    engine = VerdictEngine(
        load_policy(POLICY_PATH),
        market=FakeMarket({"DHI": equity}),
        nport=FakeNport({}),
        halalwallet_records={},
        finder=finder,
    )
    result = await engine.screen("DHI")
    assert filings.calls >= 1
    assert result.votes["Ratios"].vote is Vote.ABSTAIN
    assert "interest-income" in result.votes["Ratios"].reason
    out = await finder.enrich(_ctx(_ok_equity(ticker="LULU", interest_income=None)))
    assert out.interest_income is None
    assert out.fact_states.get("interest_income") is not FactState.ZERO
    assert out.fact_states.get("interest_income") is FactState.MISSING
    assert filings.calls >= 2


@pytest.mark.asyncio
async def test_checker_accepted_zero_on_present_body_is_zero() -> None:
    equity = _ok_equity(interest_income=None, accounts_receivable=1.0)
    equity.segments = [Segment("Cloud", 1.0, 1.0, [], FactState.FOUND)]
    equity.fact_states["segments"] = FactState.FOUND
    equity.fact_states["interest_income"] = FactState.MISSING
    excerpt = "Interest income was $0."
    finder, _nim, checker, _filings = _finder(
        excerpts={"AAPL": {"item1": excerpt}},
        responses={"3": '{"field":"interest_income","value":0}'},
    )
    out = await finder.enrich(_ctx(equity))
    assert checker.calls
    assert out.interest_income == 0.0
    assert out.fact_states["interest_income"] is FactState.ZERO


@pytest.mark.asyncio
async def test_null_interest_on_first_excerpt_does_not_zero_before_notes() -> None:
    equity = _ok_equity(interest_income=None, accounts_receivable=1.0)
    equity.segments = [Segment("Cloud", 1.0, 1.0, [], FactState.FOUND)]
    equity.fact_states["segments"] = FactState.FOUND
    equity.fact_states["interest_income"] = FactState.MISSING
    finder, nim, _checker, _filings = _finder(
        excerpts={
            "AAPL": {
                "item1": "We sell office software to enterprises.",
                "segments": INTEREST_ITEM1,
            }
        },
        responses={
            "3": [
                '{"interest_income": null}',
                '{"interest_income": 10}',
            ]
        },
    )
    out = await finder.enrich(_ctx(equity))
    assert nim.calls.count("3") >= 2
    assert out.interest_income == 10.0
    assert out.fact_states["interest_income"] is FactState.FOUND


@pytest.mark.asyncio
async def test_job5_fills_missing_sector_industry_after_checker() -> None:
    equity = _ok_equity(sector=None, industry=None)
    equity.segments = [Segment("Cloud", 1.0, 1.0, [], FactState.FOUND)]
    equity.fact_states["segments"] = FactState.FOUND
    excerpt = "The issuer operates in Technology. Industry is Consumer Electronics."
    finder, nim, checker, _filings = _finder(
        excerpts={"AAPL": {"item1": excerpt}},
        responses={"5": '{"sector":"Technology","industry":"Consumer Electronics"}'},
    )
    out = await finder.enrich(_ctx(equity))
    assert nim.calls.count("5") >= 1
    assert checker.calls
    assert out.sector == "Technology"
    assert out.industry == "Consumer Electronics"

    policy = load_policy(POLICY_PATH)
    jobs = policy.raw.setdefault("nvidia", {}).setdefault("jobs", {})
    jobs.pop("5", None)
    jobs.pop(5, None)
    nim2 = FakeNim({"5": '{"sector":"Technology","industry":"Consumer Electronics"}'})
    check2 = FakeChecker()
    finder2 = DataFinder(
        policy,
        nim2,
        FakeFilings({"AAPL": {"item1": excerpt}}),
        checker=check2,
        cik_lookup=_fixed_cik,
    )
    missing = _ok_equity(sector=None, industry=None)
    missing.segments = [Segment("Cloud", 1.0, 1.0, [], FactState.FOUND)]
    missing.fact_states["segments"] = FactState.FOUND
    out2 = await finder2.enrich(_ctx(missing))
    assert nim2.calls.count("5") == 0
    assert out2.sector is None
    assert out2.industry is None


@pytest.mark.asyncio
async def test_rubber_stamp_checker_cannot_bless_uncited_number() -> None:
    class Stamp:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def __call__(self, candidate: dict, excerpt: str) -> dict:
            self.calls.append(candidate)
            return {"accepted": True, "reason": "ok"}

    equity = _ok_equity(interest_income=None, accounts_receivable=1.0)
    equity.segments = [Segment("Cloud", 1.0, 1.0, [], FactState.FOUND)]
    equity.fact_states["segments"] = FactState.FOUND
    equity.fact_states["interest_income"] = FactState.MISSING
    stamp = Stamp()
    finder, _nim, _ignored, _filings = _finder(
        excerpts={"AAPL": {"item1": INTEREST_ITEM1}},
        responses={"3": '{"field":"interest_income","value":999000000000}'},
        checker=stamp,
    )
    out = await finder.enrich(_ctx(equity))
    assert stamp.calls
    assert out.interest_income is None
    assert out.fact_states["interest_income"] is FactState.DOUBTFUL
    assert out.fact_states["interest_income"] is not FactState.FOUND


@pytest.mark.asyncio
async def test_quantified_online_gambling_activity_fail() -> None:
    vote = await _activity_for(
        _ok_equity(
            segments=[
                Segment(
                    name="Betting",
                    revenue=None,
                    revenue_pct=12.0,
                    tags=["online_gambling_operator"],
                    state=FactState.FOUND,
                )
            ]
        )
    )
    assert vote.vote is Vote.FAIL
    unquant = await _activity_for(
        _ok_equity(
            segments=[
                Segment(
                    name="Betting",
                    revenue=None,
                    revenue_pct=None,
                    tags=["online_gambling_operator"],
                    state=FactState.FOUND,
                )
            ]
        )
    )
    assert unquant.vote is Vote.ABSTAIN


@pytest.mark.asyncio
async def test_invented_segment_name_is_not_tagged() -> None:
    finder, _nim, checker, _filings = _finder(
        excerpts={"X": {"item1": "We sell office software to enterprises."}},
        responses={"6": '{"segments":[{"name":"Sportsbook"}]}'},
    )
    fundamentals = await finder.enrich(
        _ctx(_ok_equity(ticker="X", accounts_receivable=1.0))
    )
    assert checker.calls
    tags = [tag for seg in fundamentals.segments for tag in seg.tags]
    assert "online_gambling_operator" not in tags
    assert all(seg.name != "Sportsbook" for seg in fundamentals.segments)


@pytest.mark.asyncio
async def test_ignore_unquantified_tag_still_abstains() -> None:
    policy = load_policy(POLICY_PATH)
    policy.raw.setdefault("activity", {})["unquantified_denied_tag"] = "ignore"
    plugin = ActivityPlugin(policy)
    vote = await plugin.vote(
        _ctx(
            _ok_equity(
                segments=[
                    Segment(
                        name="Betting",
                        revenue=None,
                        revenue_pct=None,
                        tags=["online_gambling_operator"],
                        state=FactState.FOUND,
                    )
                ]
            )
        )
    )
    assert vote.vote is Vote.ABSTAIN


@pytest.mark.asyncio
async def test_engine_keeps_yahoo_when_enrich_raises() -> None:
    class BoomFinder:
        async def enrich(self, ctx):
            raise RuntimeError("sec 403")

    engine = VerdictEngine(
        load_policy(POLICY_PATH),
        market=FakeMarket({"AAPL": _ok_equity()}),
        nport=FakeNport({}),
        halalwallet_records={},
        finder=BoomFinder(),
    )
    result = await engine.screen("AAPL")
    assert result.votes["Activity"].vote is Vote.PASS
    assert result.votes["Ratios"].vote is Vote.PASS


def test_empty_required_after_plugin_filter_stays_not_halal_in_fusion() -> None:
    from fusion import NOT_HALAL, fuse_conjunction
    from plugins.base import PluginVote

    votes = {
        "HalalWallet": PluginVote("HalalWallet", Vote.PASS),
    }
    assert fuse_conjunction(votes, []) == NOT_HALAL
