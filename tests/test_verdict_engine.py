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
from data_finder import NoOpFinder
from fusion import HALAL, NOT_HALAL, fuse_conjunction, required_plugin_names
from market_data import YFinanceMarketData, _latest_fact, _sum_facts_same_period
from plugins.base import (
    DenominatorSource,
    FactState,
    Fundamentals,
    HoldingScreenMode,
    PluginVote,
    Segment,
    Vote,
)
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
        trailing_avg_market_cap=1000.0,
        denominator_source=DenominatorSource.TRAILING_AVG,
        total_debt=100.0,
        cash_and_securities=50.0,
        interest_income=10.0,
        revenue=500.0,
    )
    data.update(overrides)
    return Fundamentals(**data)


_UNSET = object()


def _engine(
    profiles: dict[str, Fundamentals],
    *,
    hw_records: dict | None = None,
    nport: NportClient | None = None,
    policy=None,
    finder=_UNSET,
) -> VerdictEngine:
    injected = NoOpFinder() if finder is _UNSET else finder
    return VerdictEngine(
        policy or load_policy(POLICY_PATH),
        market=FakeMarket(profiles),
        nport=nport or FakeNport({}),
        halalwallet_records={} if hw_records is None else hw_records,
        finder=injected,
    )


def _etf_profile(ticker: str = "SPY") -> Fundamentals:
    return Fundamentals(ticker=ticker, quote_type="ETF", sector="Financial Services")


async def _record_holding_screens(engine: VerdictEngine) -> list[tuple]:
    await engine._ensure_clients()
    plugin = engine._plugins["NportHoldings"]
    inner = plugin._screen_holding
    calls: list[tuple] = []

    async def rec(ticker: str, depth: int, mode, finder: bool):
        result = await inner(ticker, depth, mode, finder)
        calls.append((ticker, depth, mode, finder, result))
        return result

    plugin._screen_holding = rec
    return calls


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
@pytest.mark.parametrize(
    ("total_debt", "ratios_vote", "verdict"),
    [
        (329.0, Vote.PASS, HALAL),
        (330.0, Vote.FAIL, NOT_HALAL),
        (331.0, Vote.FAIL, NOT_HALAL),
    ],
)
async def test_debt_ratio_fail_at_or_above_33(
    total_debt: float, ratios_vote: Vote, verdict: str
) -> None:
    engine = _engine({"AAPL": _ok_equity(total_debt=total_debt)})
    result = await engine.screen("AAPL")
    assert result.votes["Ratios"].vote is ratios_vote
    assert result.verdict == verdict


@pytest.mark.asyncio
async def test_cdw_shaped_debt_37_pct_of_trailing_denom_fails() -> None:
    # 37% of trailing FAIL; 18.5% of spot would PASS if spot were used.
    engine = _engine(
        {
            "CDW": _ok_equity(
                ticker="CDW",
                market_cap=2000.0,
                trailing_avg_market_cap=1000.0,
                denominator_source=DenominatorSource.TRAILING_AVG,
                total_debt=370.0,
            )
        }
    )
    result = await engine.screen("CDW")
    assert result.votes["Ratios"].vote is Vote.FAIL
    assert "debt" in result.votes["Ratios"].reason
    assert result.votes["Ratios"].metrics["debt_pct"] == pytest.approx(37.0)
    assert result.verdict == NOT_HALAL


@pytest.mark.asyncio
async def test_tsco_shaped_debt_exactly_33_pct_of_trailing_denom_fails() -> None:
    # 33.0% of trailing FAIL (>=); 16.5% of spot would PASS if spot were used.
    engine = _engine(
        {
            "TSCO": _ok_equity(
                ticker="TSCO",
                market_cap=2000.0,
                trailing_avg_market_cap=1000.0,
                denominator_source=DenominatorSource.TRAILING_AVG,
                total_debt=330.0,
            )
        }
    )
    result = await engine.screen("TSCO")
    assert result.votes["Ratios"].vote is Vote.FAIL
    assert "debt" in result.votes["Ratios"].reason
    assert result.votes["Ratios"].metrics["debt_pct"] == pytest.approx(33.0)
    assert result.verdict == NOT_HALAL


@pytest.mark.asyncio
async def test_trailing_avg_denom_passes_when_spot_debt_would_fail() -> None:
    # 37% of spot FAIL; 18.5% of trailing PASS.
    engine = _engine(
        {
            "AAPL": _ok_equity(
                market_cap=1000.0,
                trailing_avg_market_cap=2000.0,
                denominator_source=DenominatorSource.TRAILING_AVG,
                total_debt=370.0,
            )
        }
    )
    result = await engine.screen("AAPL")
    assert result.votes["Ratios"].vote is Vote.PASS
    assert result.votes["Ratios"].metrics["debt_pct"] == pytest.approx(18.5)
    assert result.verdict == HALAL


@pytest.mark.asyncio
async def test_missing_use_trailing_avg_market_cap_uses_spot() -> None:
    policy = load_policy(POLICY_PATH)
    del policy.raw["ratios"]["use_trailing_avg_market_cap"]
    # Trailing 16.5% would PASS; spot 33% FAIL.
    engine = _engine(
        {
            "AAPL": _ok_equity(
                market_cap=1000.0,
                trailing_avg_market_cap=2000.0,
                denominator_source=DenominatorSource.TRAILING_AVG,
                total_debt=330.0,
            )
        },
        policy=policy,
    )
    result = await engine.screen("AAPL")
    assert result.votes["Ratios"].vote is Vote.FAIL
    assert result.votes["Ratios"].metrics["debt_pct"] == pytest.approx(33.0)
    assert result.verdict == NOT_HALAL


@pytest.mark.asyncio
async def test_spot_fallback_uses_spot_denom_when_ok() -> None:
    policy = load_policy(POLICY_PATH)
    assert policy.section("ratios").get("spot_fallback_ok") is True
    engine = _engine(
        {
            "AAPL": _ok_equity(
                market_cap=1000.0,
                trailing_avg_market_cap=2000.0,
                denominator_source=DenominatorSource.SPOT_FALLBACK,
                total_debt=330.0,
            )
        },
        policy=policy,
    )
    result = await engine.screen("AAPL")
    assert result.votes["Ratios"].vote is Vote.FAIL
    assert result.votes["Ratios"].metrics["debt_pct"] == pytest.approx(33.0)


@pytest.mark.asyncio
async def test_spot_fallback_abstains_when_not_ok() -> None:
    policy = load_policy(POLICY_PATH)
    policy.raw["ratios"]["spot_fallback_ok"] = False
    engine = _engine(
        {
            "AAPL": _ok_equity(
                market_cap=1000.0,
                trailing_avg_market_cap=None,
                denominator_source=DenominatorSource.SPOT_FALLBACK,
                total_debt=100.0,
            )
        },
        policy=policy,
    )
    result = await engine.screen("AAPL")
    assert result.votes["Ratios"].vote is Vote.ABSTAIN
    assert "market cap" in result.votes["Ratios"].reason
    assert result.verdict == NOT_HALAL


@pytest.mark.asyncio
async def test_cash_screen_off_high_cash_is_pass() -> None:
    engine = _engine({"AAPL": _ok_equity(cash_and_securities=400.0)})
    result = await engine.screen("AAPL")
    assert result.votes["Ratios"].vote is Vote.PASS
    assert "cash" not in result.votes["Ratios"].reason
    assert result.verdict == HALAL


@pytest.mark.asyncio
async def test_cash_screen_off_missing_cash_does_not_abstain() -> None:
    engine = _engine({"AAPL": _ok_equity(cash_and_securities=None)})
    result = await engine.screen("AAPL")
    assert result.votes["Ratios"].vote is Vote.PASS
    assert result.verdict == HALAL


@pytest.mark.asyncio
async def test_ar_parsed_with_screen_off_metrics_only() -> None:
    policy = load_policy(POLICY_PATH)
    assert policy.section("ratios").get("receivables_screen_enabled") is False
    assert policy.section("ratios").get("receivables_to_market_cap_pct") == 49.0
    engine = _engine(
        {"AAPL": _ok_equity(accounts_receivable=900.0)},
        policy=policy,
    )
    result = await engine.screen("AAPL")
    assert result.votes["Ratios"].vote is Vote.PASS
    assert result.votes["Ratios"].metrics["receivables_pct"] == pytest.approx(90.0)
    assert "receivables" not in result.votes["Ratios"].reason
    assert result.verdict == HALAL


@pytest.mark.asyncio
async def test_missing_cash_screen_enabled_key_still_applies_cash() -> None:
    policy = load_policy(POLICY_PATH)
    del policy.raw["ratios"]["cash_screen_enabled"]
    engine = _engine({"AAPL": _ok_equity(cash_and_securities=400.0)}, policy=policy)
    result = await engine.screen("AAPL")
    assert result.votes["Ratios"].vote is Vote.FAIL
    assert "cash" in result.votes["Ratios"].reason
    assert result.verdict == NOT_HALAL


@pytest.mark.asyncio
async def test_missing_receivables_screen_enabled_key_still_applies_ar() -> None:
    policy = load_policy(POLICY_PATH)
    del policy.raw["ratios"]["receivables_screen_enabled"]
    engine = _engine(
        {"AAPL": _ok_equity(accounts_receivable=900.0)},
        policy=policy,
    )
    result = await engine.screen("AAPL")
    assert result.votes["Ratios"].vote is Vote.FAIL
    assert "receivables" in result.votes["Ratios"].reason
    assert result.verdict == NOT_HALAL


def test_unquantified_denied_tag_is_doubtful_in_policy() -> None:
    policy = load_policy(POLICY_PATH)
    assert policy.section("activity").get("unquantified_denied_tag") == "doubtful"


def _found_seg(
    name: str,
    tags: list[str],
    *,
    revenue: float | None = None,
    revenue_pct: float | None = None,
) -> Segment:
    return Segment(
        name=name,
        revenue=revenue,
        revenue_pct=revenue_pct,
        tags=tags,
        state=FactState.FOUND,
    )


@pytest.mark.asyncio
async def test_interest_plus_gambling_segment_ratios_fail() -> None:
    engine = _engine(
        {
            "X": _ok_equity(
                ticker="X",
                interest_income=5.0,
                revenue=500.0,
                segments=[
                    _found_seg(
                        "Loot boxes",
                        ["gaming_gambling_mechanics"],
                        revenue_pct=6.0,
                    )
                ],
            )
        }
    )
    result = await engine.screen("X")
    ratios = result.votes["Ratios"]
    assert result.votes["Activity"].vote is Vote.PASS
    assert ratios.metrics["interest_income_pct"] == pytest.approx(1.0)
    assert ratios.metrics["interest_income_pct"] < ratios.metrics["income_limit_pct"]
    assert ratios.metrics["impermissible_income_pct"] == pytest.approx(7.0)
    assert (
        ratios.metrics["impermissible_income_pct"] > ratios.metrics["income_limit_pct"]
    )
    assert ratios.vote is Vote.FAIL
    assert "impermissible-income" in ratios.reason


@pytest.mark.asyncio
async def test_interest_plus_allowed_games_ratios_pass() -> None:
    engine = _engine(
        {
            "MSFT": _ok_equity(
                ticker="MSFT",
                sector="Technology",
                industry="Software - Infrastructure",
                interest_income=5.0,
                revenue=500.0,
                segments=[
                    _found_seg(
                        "Xbox",
                        ["gaming_interactive_entertainment"],
                        revenue_pct=8.0,
                    )
                ],
            )
        }
    )
    result = await engine.screen("MSFT")
    ratios = result.votes["Ratios"]
    assert result.votes["Activity"].vote is Vote.PASS
    assert ratios.metrics["interest_income_pct"] == pytest.approx(1.0)
    assert ratios.metrics["impermissible_income_pct"] == pytest.approx(1.0)
    assert (
        ratios.metrics["impermissible_income_pct"] < ratios.metrics["income_limit_pct"]
    )
    assert ratios.vote is Vote.PASS


@pytest.mark.asyncio
async def test_unquantified_impure_ads_video_ratios_abstain() -> None:
    engine = _engine(
        {
            "GOOGL": _ok_equity(
                ticker="GOOGL",
                sector="Communication Services",
                industry="Internet Content & Information",
                interest_income=5.0,
                revenue=500.0,
                segments=[
                    _found_seg("YouTube ads", ["ads_video"]),
                ],
            )
        }
    )
    result = await engine.screen("GOOGL")
    ratios = result.votes["Ratios"]
    assert result.votes["Activity"].vote is Vote.PASS
    assert ratios.metrics["interest_income_pct"] == pytest.approx(1.0)
    assert ratios.metrics["interest_income_pct"] < ratios.metrics["income_limit_pct"]
    assert ratios.vote is Vote.ABSTAIN
    assert "unquantified" in ratios.reason
    assert "interest-income" not in ratios.reason


@pytest.mark.asyncio
async def test_cited_weapons_under_income_cap_ratios_pass() -> None:
    engine = _engine(
        {
            "CAT": _ok_equity(
                ticker="CAT",
                sector="Industrials",
                industry="Farm & Heavy Construction Machinery",
                interest_income=5.0,
                revenue=500.0,
                segments=[_found_seg("Defense parts", ["weapons"], revenue_pct=3.0)],
            )
        }
    )
    result = await engine.screen("CAT")
    ratios = result.votes["Ratios"]
    assert result.votes["Activity"].vote is Vote.PASS
    assert ratios.metrics["interest_income_pct"] == pytest.approx(1.0)
    assert ratios.metrics["impermissible_income_pct"] == pytest.approx(4.0)
    assert (
        ratios.metrics["impermissible_income_pct"] < ratios.metrics["income_limit_pct"]
    )
    assert ratios.vote is Vote.PASS


@pytest.mark.asyncio
async def test_cited_weapons_over_income_cap_ratios_fail() -> None:
    engine = _engine(
        {
            "CAT": _ok_equity(
                ticker="CAT",
                sector="Industrials",
                industry="Farm & Heavy Construction Machinery",
                interest_income=5.0,
                revenue=500.0,
                segments=[_found_seg("Defense parts", ["weapons"], revenue_pct=6.0)],
            )
        }
    )
    result = await engine.screen("CAT")
    ratios = result.votes["Ratios"]
    assert result.votes["Activity"].vote is Vote.PASS
    assert ratios.metrics["interest_income_pct"] == pytest.approx(1.0)
    assert ratios.metrics["interest_income_pct"] < ratios.metrics["income_limit_pct"]
    assert ratios.metrics["impermissible_income_pct"] == pytest.approx(7.0)
    assert (
        ratios.metrics["impermissible_income_pct"] > ratios.metrics["income_limit_pct"]
    )
    assert ratios.vote is Vote.FAIL


@pytest.mark.asyncio
async def test_impure_segment_revenue_dollars_count_toward_mix() -> None:
    engine = _engine(
        {
            "X": _ok_equity(
                ticker="X",
                interest_income=5.0,
                revenue=500.0,
                segments=[
                    _found_seg(
                        "Loot boxes",
                        ["gaming_gambling_mechanics"],
                        revenue=30.0,
                    )
                ],
            )
        }
    )
    result = await engine.screen("X")
    ratios = result.votes["Ratios"]
    assert ratios.metrics["interest_income_pct"] == pytest.approx(1.0)
    assert ratios.metrics["impermissible_income_pct"] == pytest.approx(7.0)
    assert ratios.vote is Vote.FAIL


@pytest.mark.asyncio
async def test_core_fail_segment_does_not_change_ratios_mix() -> None:
    engine = _engine(
        {
            "X": _ok_equity(
                ticker="X",
                interest_income=5.0,
                revenue=500.0,
                segments=[
                    _found_seg(
                        "Sportsbook",
                        ["online_gambling_operator"],
                        revenue_pct=6.0,
                    )
                ],
            )
        }
    )
    result = await engine.screen("X")
    ratios = result.votes["Ratios"]
    assert result.votes["Activity"].vote is Vote.FAIL
    assert ratios.metrics["impermissible_income_pct"] == pytest.approx(1.0)
    assert ratios.vote is Vote.PASS


@pytest.mark.asyncio
async def test_quantified_zero_impure_does_not_abstain() -> None:
    engine = _engine(
        {
            "X": _ok_equity(
                ticker="X",
                interest_income=5.0,
                revenue=500.0,
                segments=[_found_seg("Defense parts", ["weapons"], revenue_pct=0.0)],
            )
        }
    )
    result = await engine.screen("X")
    ratios = result.votes["Ratios"]
    assert ratios.vote is Vote.PASS
    assert ratios.metrics["impermissible_income_pct"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_impure_mix_prefers_revenue_pct_over_dollars() -> None:
    engine = _engine(
        {
            "X": _ok_equity(
                ticker="X",
                interest_income=5.0,
                revenue=500.0,
                segments=[
                    _found_seg(
                        "Defense parts",
                        ["weapons"],
                        revenue=30.0,
                        revenue_pct=3.0,
                    )
                ],
            )
        }
    )
    result = await engine.screen("X")
    ratios = result.votes["Ratios"]
    assert ratios.metrics["impermissible_income_pct"] == pytest.approx(4.0)
    assert ratios.vote is Vote.PASS


@pytest.mark.asyncio
async def test_impure_segment_counted_once_not_per_tag() -> None:
    engine = _engine(
        {
            "X": _ok_equity(
                ticker="X",
                interest_income=5.0,
                revenue=500.0,
                segments=[
                    _found_seg(
                        "Dual",
                        ["weapons", "pork"],
                        revenue_pct=3.0,
                    )
                ],
            )
        }
    )
    result = await engine.screen("X")
    ratios = result.votes["Ratios"]
    assert ratios.metrics["impermissible_income_pct"] == pytest.approx(4.0)
    assert ratios.vote is Vote.PASS


@pytest.mark.asyncio
async def test_empty_activity_denylist_lets_bank_pass() -> None:
    policy = load_policy(POLICY_PATH)
    policy.raw["activity"]["denied_sectors"] = []
    policy.raw["activity"]["denied_industries"] = []
    policy.raw["activity"]["denied_industry_substrings"] = []
    assert any(
        isinstance(spec, dict) and spec.get("id") == "cannabis"
        for spec in policy.section("activity").get("segments") or []
    )
    engine = _engine(
        {
            "JPM": _ok_equity(
                ticker="JPM",
                sector="Financial Services",
                industry="Banks - Diversified",
            ),
            "CGC": _ok_equity(
                ticker="CGC",
                sector="Healthcare",
                industry="Specialty Cannabis",
            ),
        },
        policy=policy,
    )
    bank = await engine.screen("JPM")
    assert bank.votes["Ratios"].vote is Vote.PASS
    assert bank.votes["Activity"].vote is Vote.PASS
    assert bank.verdict == HALAL
    cannabis = await engine.screen("CGC")
    assert cannabis.votes["Activity"].vote is Vote.PASS
    assert cannabis.verdict == HALAL


@pytest.mark.asyncio
async def test_halalwallet_not_halal_in_dataset_still_vetoes() -> None:
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
    assert "HalalWallet" not in result.required
    assert result.verdict == NOT_HALAL


@pytest.mark.asyncio
async def test_halalwallet_absent_from_dataset_is_not_required() -> None:
    engine = _engine({"AAPL": _ok_equity()}, hw_records={})
    result = await engine.screen("AAPL")
    assert result.votes["HalalWallet"].vote is Vote.ABSTAIN
    assert "HalalWallet" not in result.required
    assert result.verdict == HALAL


@pytest.mark.asyncio
async def test_halalwallet_in_dataset_abstain_is_not_required() -> None:
    records = {
        "AAPL": {
            "ticker": "AAPL",
            "verdict": "unknown",
        }
    }
    engine = _engine({"AAPL": _ok_equity()}, hw_records=records)
    result = await engine.screen("AAPL")
    assert result.votes["HalalWallet"].vote is Vote.ABSTAIN
    assert "HalalWallet" not in result.required
    assert result.verdict == HALAL


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ticker", "industry", "company_name"),
    [
        ("JPM", "Banks - Diversified", "JPMorgan Chase"),
        ("BAC", "Banks - Diversified", "Bank of America"),
        ("DIB", "Banks - Regional", "Dubai Islamic Bank Takaful"),
    ],
)
async def test_conjunction_bank_activity_fail(
    ticker: str, industry: str, company_name: str
) -> None:
    engine = _engine(
        {
            ticker: _ok_equity(
                ticker=ticker,
                sector="Financial Services",
                industry=industry,
                company_name=company_name,
            )
        }
    )
    result = await engine.screen(ticker)
    assert result.votes["Ratios"].vote is Vote.PASS
    assert result.votes["Activity"].vote is Vote.FAIL
    assert result.verdict == NOT_HALAL


@pytest.mark.asyncio
async def test_tobacco_industry_activity_fail() -> None:
    engine = _engine(
        {
            "MO": _ok_equity(
                ticker="MO",
                sector="Consumer Defensive",
                industry="Tobacco",
            )
        }
    )
    result = await engine.screen("MO")
    assert result.votes["Ratios"].vote is Vote.PASS
    assert result.votes["Activity"].vote is Vote.FAIL
    assert result.verdict == NOT_HALAL


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "industry",
    ["Specialty Cannabis", "Medical MARIJUANA Products"],
)
async def test_cannabis_substring_activity_fail(industry: str) -> None:
    denied = [
        s.casefold()
        for s in load_policy(POLICY_PATH).section("activity").get("denied_industries")
        or []
    ]
    assert industry.casefold() not in denied
    engine = _engine(
        {
            "CGC": _ok_equity(
                ticker="CGC",
                sector="Healthcare",
                industry=industry,
            )
        }
    )
    result = await engine.screen("CGC")
    assert result.votes["Ratios"].vote is Vote.PASS
    assert result.votes["Activity"].vote is Vote.FAIL
    assert result.verdict == NOT_HALAL


@pytest.mark.asyncio
async def test_msft_software_infrastructure_activity_pass() -> None:
    engine = _engine(
        {
            "MSFT": _ok_equity(
                ticker="MSFT",
                sector="Technology",
                industry="Software - Infrastructure",
            )
        }
    )
    result = await engine.screen("MSFT")
    assert result.votes["Ratios"].vote is Vote.PASS
    assert result.votes["Activity"].vote is Vote.PASS
    assert result.verdict == HALAL


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "industry",
    ["Movies & Entertainment", "Advertising Agencies", "Advertising"],
)
async def test_movies_and_advertising_industries_activity_pass(industry: str) -> None:
    denied = [
        s.casefold()
        for s in load_policy(POLICY_PATH).section("activity").get("denied_industries")
        or []
    ]
    assert industry.casefold() not in denied
    engine = _engine(
        {
            "DIS": _ok_equity(
                ticker="DIS",
                sector="Communication Services",
                industry=industry,
            )
        }
    )
    result = await engine.screen("DIS")
    assert result.votes["Ratios"].vote is Vote.PASS
    assert result.votes["Activity"].vote is Vote.PASS
    assert result.verdict == HALAL


def test_committed_denylist_omits_media_and_gics_extras() -> None:
    denied = [
        s.casefold()
        for s in load_policy(POLICY_PATH).section("activity").get("denied_industries")
        or []
    ]
    for name in (
        "movies & entertainment",
        "advertising",
        "advertising agencies",
        "financial data & stock exchanges",
        "transaction & payment processing services",
        "luxury goods",
        "drug manufacturers - general",
    ):
        assert name not in denied


@pytest.mark.asyncio
async def test_financial_data_industry_outside_finance_sector_passes() -> None:
    engine = _engine(
        {
            "ICE": _ok_equity(
                ticker="ICE",
                sector="Technology",
                industry="Financial Data & Stock Exchanges",
            )
        }
    )
    result = await engine.screen("ICE")
    assert result.votes["Ratios"].vote is Vote.PASS
    assert result.votes["Activity"].vote is Vote.PASS
    assert result.verdict == HALAL


@pytest.mark.asyncio
async def test_pypl_credit_services_activity_fail() -> None:
    engine = _engine(
        {
            "PYPL": _ok_equity(
                ticker="PYPL",
                sector="Financial Services",
                industry="Credit Services",
            )
        }
    )
    result = await engine.screen("PYPL")
    assert result.votes["Ratios"].vote is Vote.PASS
    assert result.votes["Activity"].vote is Vote.FAIL
    assert result.verdict == NOT_HALAL


@pytest.mark.asyncio
async def test_activity_substring_matches_industry_not_sector() -> None:
    engine = _engine(
        {
            "X": _ok_equity(
                ticker="X",
                sector="Cannabis Retail",
                industry="Software - Infrastructure",
            )
        }
    )
    result = await engine.screen("X")
    assert result.votes["Ratios"].vote is Vote.PASS
    assert result.votes["Activity"].vote is Vote.PASS
    assert result.verdict == HALAL


@pytest.mark.asyncio
async def test_missing_denied_industry_substrings_key_is_empty() -> None:
    policy = load_policy(POLICY_PATH)
    policy.raw["activity"].pop("denied_industry_substrings", None)
    engine = _engine(
        {
            "CGC": _ok_equity(
                ticker="CGC",
                sector="Healthcare",
                industry="Specialty Cannabis",
            )
        },
        policy=policy,
    )
    result = await engine.screen("CGC")
    assert result.votes["Activity"].vote is Vote.PASS
    assert result.verdict == HALAL


@pytest.mark.asyncio
async def test_aerospace_and_defense_industry_activity_fail() -> None:
    engine = _engine(
        {
            "LMT": _ok_equity(
                ticker="LMT",
                sector="Industrials",
                industry="Aerospace & Defense",
            )
        }
    )
    result = await engine.screen("LMT")
    assert result.votes["Ratios"].vote is Vote.PASS
    assert result.votes["Activity"].vote is Vote.FAIL
    assert result.verdict == NOT_HALAL


@pytest.mark.asyncio
async def test_non_aerospace_industrial_is_not_activity_fail() -> None:
    engine = _engine(
        {
            "CAT": _ok_equity(
                ticker="CAT",
                sector="Industrials",
                industry="Farm & Heavy Construction Machinery",
            )
        }
    )
    result = await engine.screen("CAT")
    assert result.votes["Ratios"].vote is Vote.PASS
    assert result.votes["Activity"].vote is Vote.PASS
    assert result.verdict == HALAL


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
    assert result.votes["NportHoldings"].metrics["coverage"] == pytest.approx(
        0.90 / 0.99
    )
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
async def test_nport_scoring_keys_missing_fail_closed() -> None:
    report = NportReport(
        series_id="S1",
        holdings=[Holding(name="Apple", pct=0.98, ticker="AAPL", asset_cat="EC")],
    )
    profiles = {"SPY": _etf_profile(), "AAPL": _ok_equity()}
    present = await _engine(profiles, nport=FakeNport({"SPY": report})).screen("SPY")
    assert present.votes["NportHoldings"].vote is Vote.PASS
    assert present.verdict == HALAL

    for key in (
        "activity_fail_epsilon_weight",
        "ratio_fail_weight_cap",
        "ratio_materiality_weight",
    ):
        policy = load_policy(POLICY_PATH)
        del policy.raw["etf"][key]
        missing = await _engine(
            profiles, nport=FakeNport({"SPY": report}), policy=policy
        ).screen("SPY")
        assert missing.votes["NportHoldings"].vote is Vote.FAIL
        assert "incomplete" in missing.votes["NportHoldings"].reason
        assert missing.votes["NportHoldings"].metrics["missing_policy_key"] == (
            f"etf.{key}"
        )
        assert missing.verdict == NOT_HALAL

    bad = load_policy(POLICY_PATH)
    bad.raw["etf"]["activity_fail_epsilon_weight"] = "not-a-number"
    invalid = await _engine(
        profiles, nport=FakeNport({"SPY": report}), policy=bad
    ).screen("SPY")
    assert invalid.votes["NportHoldings"].vote is Vote.FAIL
    assert invalid.votes["NportHoldings"].metrics["missing_policy_key"] == (
        "etf.activity_fail_epsilon_weight"
    )


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
    assert {
        "CDW",
        "TSCO",
        "CSX",
        "ECL",
        "ITW",
        "KO",
        "DHI",
        "LULU",
        "AMZN",
        "WMT",
    } <= stocks
    assert {"SPUS", "HLAL"} <= etfs


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
    metrics = result.votes["NportHoldings"].metrics
    assert "JPM" in metrics["activity_failed_holdings"]
    assert "failed_holdings" not in metrics
    assert "failed_count" not in metrics
    assert result.verdict == NOT_HALAL


def test_plugins_have_no_ticker_allowlist() -> None:
    for path in (ROOT / "src" / "plugins").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "SPUS" not in text
        assert "HLAL" not in text
        assert "CDW" not in text
        assert "TSCO" not in text


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
async def test_halalwallet_fetch_error_abstain_not_required() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="nope")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        engine = VerdictEngine(
            load_policy(POLICY_PATH),
            market=FakeMarket({"AAPL": _ok_equity()}),
            nport=FakeNport({}),
            http=http,
            finder=NoOpFinder(),
        )
        result = await engine.screen("AAPL")
    assert result.votes["HalalWallet"].vote is Vote.ABSTAIN
    assert "unavailable" in result.votes["HalalWallet"].reason
    assert "HalalWallet" not in result.required
    assert result.verdict == HALAL


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

        def _download_history(self, ticker: str):
            return None

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
    cache_path = tmp_path / "cusip.json"
    cache_path.write_text('{"037833100": "AAPL"}', encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requested.append(url)
        if url.endswith("company_tickers_mf.json"):
            return httpx.Response(200, json=mf)
        if "submissions" in url:
            return httpx.Response(200, json=submissions)
        if "openfigi.com" in url:
            return httpx.Response(500, text="OpenFIGI must not run on holdings()")
        if url.endswith("/primary_doc.xml") and "xslForm" not in url:
            return httpx.Response(200, text=xml_text)
        if "xslForm" in url:
            return httpx.Response(200, text="<html><div>S000002 mismatched")
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        report = await SecNportClient(policy, http, cache_path=cache_path).holdings(
            "QQQ"
        )
    assert report is not None
    assert report.holdings[0].ticker == "AAPL"
    assert any("xslForm" in url for url in requested) is False
    assert any(url.endswith("/primary_doc.xml") for url in requested)
    assert all("openfigi.com" not in url for url in requested)


@pytest.mark.asyncio
async def test_nport_openfigi_warm_retries_after_429(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("config.SEC_CONTACT_EMAIL", "nport-tests@example.com")
    monkeypatch.setattr("config.OPENFIGI_API_KEY", "")
    policy = load_policy(POLICY_PATH)
    policy.raw.setdefault("sources", {})["openfigi_retry_seconds"] = 1
    hits = {"openfigi": 0}
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        if "openfigi.com" in str(request.url):
            hits["openfigi"] += 1
            if hits["openfigi"] == 1:
                return httpx.Response(429, text="rate limited")
            return httpx.Response(
                200,
                json=[{"data": [{"ticker": "AAPL", "exchCode": "US"}]}],
            )
        return httpx.Response(404)

    cache_path = tmp_path / "cusip.json"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        mapped = await SecNportClient(
            policy,
            http,
            cache_path=cache_path,
            sleep=fake_sleep,
        ).warm_cusip_cache(["037833100"])
    assert waits == [1]
    assert hits["openfigi"] == 2
    assert mapped["037833100"] == "AAPL"
    assert '"037833100"' in cache_path.read_text(encoding="utf-8")


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


@pytest.mark.asyncio
async def test_nport_all_skip_category_is_not_halal() -> None:
    report = NportReport(
        series_id="S000001",
        holdings=[Holding(name="T-bills", pct=1.0, ticker=None, asset_cat="STIV")],
    )
    engine = _engine(
        {
            "SPY": Fundamentals(
                ticker="SPY", quote_type="ETF", sector="Financial Services"
            ),
        },
        nport=FakeNport({"SPY": report}),
    )
    result = await engine.screen("SPY")
    assert result.votes["NportHoldings"].vote is Vote.FAIL
    assert "incomplete" in result.votes["NportHoldings"].reason
    assert result.verdict == NOT_HALAL


@pytest.mark.asyncio
async def test_nport_skip_cannot_pad_coverage_floor() -> None:
    report = NportReport(
        series_id="S000001",
        holdings=[
            Holding(name="T-bills", pct=0.90, ticker=None, asset_cat="STIV"),
            Holding(name="Apple", pct=0.05, ticker="AAPL", asset_cat="EC"),
            Holding(name="Unknown", pct=0.05, ticker=None, asset_cat="EC"),
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
    assert result.verdict == NOT_HALAL


@pytest.mark.asyncio
async def test_nport_skip_weight_above_slack_is_not_halal() -> None:
    report = NportReport(
        series_id="S000001",
        holdings=[
            Holding(name="T-bills", pct=0.10, ticker=None, asset_cat="STIV"),
            Holding(name="Apple", pct=0.90, ticker="AAPL", asset_cat="EC"),
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
    assert result.verdict == NOT_HALAL


@pytest.mark.asyncio
async def test_activity_missing_industry_is_not_halal() -> None:
    engine = _engine(
        {
            "MO": _ok_equity(
                ticker="MO",
                sector="Consumer Defensive",
                industry=None,
            )
        }
    )
    result = await engine.screen("MO")
    assert result.votes["Activity"].vote is Vote.ABSTAIN
    assert "missing" in result.votes["Activity"].reason
    assert result.verdict == NOT_HALAL


def test_latest_fact_ignores_usd_shares_and_net_interest() -> None:
    gaap = {
        "InterestIncomeExpenseNet": {
            "units": {"USD": [{"end": "2024-12-31", "val": 50}]}
        },
        "InterestIncomeOperating": {
            "units": {"USD/shares": [{"end": "2024-12-31", "val": 9}]}
        },
    }
    tags = ["InterestIncomeOperating", "InterestIncomeExpenseNet"]
    assert _latest_fact(gaap, tags, skip_net=True) is None


def test_sum_facts_same_period_adds_debt_components() -> None:
    gaap = {
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
    tags = ["LongTermDebt", "DebtCurrent", "ShortTermBorrowings"]
    assert _sum_facts_same_period(gaap, tags) == pytest.approx(100)


@pytest.mark.asyncio
async def test_nport_issuer_cik_without_series_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("config.SEC_CONTACT_EMAIL", "nport-tests@example.com")
    policy = load_policy(POLICY_PATH)
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requested.append(url)
        if url.endswith("company_tickers_mf.json"):
            return httpx.Response(200, json={})
        if url.endswith("company_tickers.json"):
            return httpx.Response(
                200, json={"0": {"ticker": "SPY", "cik_str": 1234567}}
            )
        return httpx.Response(500, text="must not fetch filings without a series id")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        report = await SecNportClient(policy, http).holdings("SPY")
    assert report is None
    assert any(url.endswith("company_tickers.json") for url in requested) is False
    assert any("submissions" in url for url in requested) is False


@pytest.mark.asyncio
async def test_nport_skips_filing_with_missing_series_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("config.SEC_CONTACT_EMAIL", "nport-tests@example.com")
    policy = load_policy(POLICY_PATH)
    mf = {"0": {"cik_str": 1, "ticker": "SPY", "seriesId": "S000002"}}
    submissions = {
        "filings": {
            "recent": {
                "form": ["NPORT-P", "NPORT-P"],
                "accessionNumber": ["000-0", "000-1"],
                "primaryDocument": ["doc0.xml", "doc1.xml"],
            }
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("company_tickers_mf.json"):
            return httpx.Response(200, json=mf)
        if "submissions" in url:
            return httpx.Response(200, json=submissions)
        if "doc0.xml" in url:
            return httpx.Response(
                200,
                text=(
                    '<?xml version="1.0"?><edgarSubmission>'
                    "<invstOrSec><name>WRONG</name>"
                    "<identifiers><ticker>BAD</ticker></identifiers>"
                    "<pctVal>100</pctVal></invstOrSec></edgarSubmission>"
                ),
            )
        if "doc1.xml" in url:
            return httpx.Response(
                200,
                text=(
                    '<?xml version="1.0"?><edgarSubmission>'
                    "<seriesId>S000002</seriesId>"
                    "<invstOrSec><name>APPLE</name>"
                    "<identifiers><ticker>AAPL</ticker></identifiers>"
                    "<pctVal>100</pctVal></invstOrSec></edgarSubmission>"
                ),
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        report = await SecNportClient(policy, http).holdings("SPY")
    assert report is not None
    assert report.series_id == "S000002"
    assert report.holdings[0].ticker == "AAPL"


@pytest.mark.asyncio
async def test_nport_holdings_does_not_call_openfigi_on_cache_miss(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("config.SEC_CONTACT_EMAIL", "nport-tests@example.com")
    policy = load_policy(POLICY_PATH)
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
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requested.append(url)
        if url.endswith("company_tickers_mf.json"):
            return httpx.Response(200, json=mf)
        if "submissions" in url:
            return httpx.Response(200, json=submissions)
        if "openfigi.com" in url:
            return httpx.Response(500, text="OpenFIGI must not run on holdings()")
        if url.endswith("primary_doc.xml"):
            return httpx.Response(200, text=xml_text)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        report = await SecNportClient(
            policy, http, cache_path=tmp_path / "cusip.json"
        ).holdings("QQQ")
    assert report is not None
    assert report.holdings[0].ticker is None
    assert all("openfigi.com" not in url for url in requested)


@pytest.mark.asyncio
async def test_nport_openfigi_exhausted_429_does_not_sleep_on_last(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("config.SEC_CONTACT_EMAIL", "nport-tests@example.com")
    monkeypatch.setattr("config.OPENFIGI_API_KEY", "")
    policy = load_policy(POLICY_PATH)
    policy.raw.setdefault("sources", {})["openfigi_retry_seconds"] = 1
    hits = {"openfigi": 0}
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        if "openfigi.com" in str(request.url):
            hits["openfigi"] += 1
            return httpx.Response(429, text="rate limited")
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        mapped = await SecNportClient(
            policy,
            http,
            cache_path=tmp_path / "cusip.json",
            sleep=fake_sleep,
        ).warm_cusip_cache(["037833100"])
    assert mapped == {}
    assert hits["openfigi"] == 4
    assert waits == [1, 1, 1]


def test_eval_screener_fixture_list_concatenates_stocks_and_etfs() -> None:
    path = ROOT / "scripts" / "eval_screener.py"
    spec = importlib.util.spec_from_file_location("eval_screener_cli", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tickers = module._load_fixture_tickers(EVAL_FIXTURES_PATH)
    assert {"JPM", "BAC"} & set(tickers)
    assert {"SPY", "SPUS", "HLAL"} & set(tickers)


def test_compare_mz_bar_reads_new_nport_fail_metrics() -> None:
    path = ROOT / "scripts" / "compare_mz_bar.py"
    spec = importlib.util.spec_from_file_location("compare_mz_bar_helpers", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = ScreenResult(
        ticker="SPY",
        quote_type="ETF",
        verdict=NOT_HALAL,
        votes={
            "NportHoldings": PluginVote(
                "NportHoldings",
                Vote.FAIL,
                metrics={
                    "coverage": 0.98,
                    "activity_failed_holdings": ["JPM"],
                    "nested_fund_failed": ["QQQ"],
                    "failed_holdings": ["SHOULD_NOT_USE"],
                    "failed_count": 99,
                },
            )
        },
        required=["NportHoldings"],
    )
    assert module._failed_holdings(result) == ["JPM", "QQQ"]
    cell = module._vote_cell(result, ["NportHoldings"])
    assert "JPM" in cell
    assert "QQQ" in cell
    assert "SHOULD_NOT_USE" not in cell
    assert "failed_count=99" not in cell


class _SpyFinder:
    def __init__(self) -> None:
        self.calls = 0
        self.tickers: list[str] = []

    async def enrich(self, ctx):
        self.calls += 1
        self.tickers.append(ctx.ticker)
        return ctx.fundamentals


@pytest.mark.asyncio
async def test_engine_enrich_called_once_per_screen() -> None:
    spy = _SpyFinder()
    engine = _engine({"AAPL": _ok_equity()}, finder=spy)
    await engine.screen("AAPL")
    assert spy.calls == 1
    await engine.screen("AAPL")
    assert spy.calls == 1


@pytest.mark.asyncio
async def test_engine_cache_key_separates_plugins_and_finder() -> None:
    spy = _SpyFinder()
    engine = _engine(
        {
            "MSFT": _ok_equity(
                ticker="MSFT",
                sector="Technology",
                industry="Software - Infrastructure",
            )
        },
        finder=spy,
    )
    full = await engine.screen("MSFT")
    assert spy.calls == 1
    only = await engine.screen(
        "MSFT", plugins=["Activity", "HalalWallet"], finder=False
    )
    assert spy.calls == 1
    assert "Ratios" in full.votes
    assert full.votes["Ratios"].vote is Vote.PASS
    assert "Ratios" not in only.votes
    assert "Activity" in only.votes
    assert only.votes["Activity"].vote is Vote.PASS
    assert "Ratios" in full.votes
    assert full.votes["Ratios"].vote is Vote.PASS


@pytest.mark.asyncio
async def test_engine_empty_plugin_intersection_is_not_halal() -> None:
    engine = _engine({"AAPL": _ok_equity()})
    full = await engine.screen("AAPL")
    assert full.verdict == HALAL
    narrowed = await engine.screen("AAPL", plugins=["HalalWallet"], finder=False)
    assert narrowed.required == []
    assert narrowed.verdict == NOT_HALAL
    assert full.verdict == HALAL


@pytest.mark.asyncio
async def test_missing_nvidia_key_without_finder_is_enrich_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("config.NVIDIA_API_KEY", "")

    def boom(*_args, **_kwargs):
        raise AssertionError("DataFinder must not be built without NVIDIA_API_KEY")

    monkeypatch.setattr("data_finder.DataFinder", boom)
    engine = _engine(
        {"AAPL": _ok_equity(interest_income=None)},
        finder=None,
    )
    result = await engine.screen("AAPL")
    assert result.votes["Ratios"].vote is Vote.ABSTAIN
    assert result.verdict == NOT_HALAL


@pytest.mark.asyncio
async def test_engine_enriches_top_level_unknown_not_lookthrough_junk() -> None:
    spy = _SpyFinder()
    await _engine(
        {"ZZZZ": Fundamentals(ticker="ZZZZ", quote_type="UNKNOWN")},
        finder=spy,
    ).screen("ZZZZ")
    assert "ZZZZ" in spy.tickers

    lookthrough = _SpyFinder()
    report = NportReport(
        series_id="S1",
        holdings=[
            Holding(name="Apple", pct=0.97, ticker="AAPL", asset_cat="EC"),
            Holding(name="Lennar B", pct=0.01, ticker="LEN/B", asset_cat="EC"),
            Holding(name="Cash", pct=0.02, ticker=None, asset_cat="STIV"),
        ],
    )
    await _engine(
        {"SPY": _etf_profile(), "AAPL": _ok_equity()},
        nport=FakeNport({"SPY": report}),
        finder=lookthrough,
    ).screen("SPY")
    assert "AAPL" in lookthrough.tickers
    assert "LEN/B" not in lookthrough.tickers


@pytest.mark.asyncio
async def test_nport_activity_only_ignores_child_verdict() -> None:
    report = NportReport(
        series_id="S000001",
        holdings=[
            Holding(name="Microsoft", pct=0.996, ticker="MSFT", asset_cat="EC"),
            Holding(name="Apple", pct=0.004, ticker="AAPL", asset_cat="EC"),
        ],
    )
    engine = _engine(
        {
            "SPY": _etf_profile(),
            "MSFT": _ok_equity(ticker="MSFT"),
            "AAPL": _ok_equity(),
        },
        nport=FakeNport({"SPY": report}),
    )
    await engine._ensure_clients()
    plugin = engine._plugins["NportHoldings"]
    inner = plugin._screen_holding
    calls: list[tuple] = []

    async def rec(ticker: str, depth: int, mode, finder: bool):
        if ticker == "AAPL" and mode is HoldingScreenMode.ACTIVITY_ONLY:
            forced = ScreenResult(
                ticker="AAPL",
                quote_type="EQUITY",
                verdict=NOT_HALAL,
                votes={
                    "Activity": PluginVote("Activity", Vote.PASS),
                    "HalalWallet": PluginVote("HalalWallet", Vote.ABSTAIN),
                },
                required=["Activity"],
            )
            calls.append((ticker, mode, finder, forced))
            return forced
        child = await inner(ticker, depth, mode, finder)
        calls.append((ticker, mode, finder, child))
        return child

    plugin._screen_holding = rec
    result = await engine.screen("SPY")
    aapl = [c for c in calls if c[0] == "AAPL"]
    assert aapl
    assert aapl[0][1] is HoldingScreenMode.ACTIVITY_ONLY
    assert aapl[0][3].verdict == NOT_HALAL
    assert "Ratios" not in aapl[0][3].votes
    assert aapl[0][3].votes["Activity"].vote is Vote.PASS
    nport = result.votes["NportHoldings"]
    assert nport.vote is Vote.PASS
    assert result.verdict == HALAL
    assert "AAPL" not in nport.metrics["activity_failed_holdings"]
    assert nport.metrics["coverage"] >= 0.95
    assert nport.metrics["unresolved_ratio_weight"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_nport_activity_only_holes_are_not_uncovered() -> None:
    small = [
        Holding(name=f"W{i:02d}", pct=0.004, ticker=f"W{i:02d}", asset_cat="EC")
        for i in range(15)
    ]
    report = NportReport(
        series_id="S1",
        holdings=[
            Holding(name="Apple", pct=0.94, ticker="AAPL", asset_cat="EC"),
            *small,
        ],
    )
    profiles = {
        "SPY": _etf_profile(),
        "AAPL": _ok_equity(),
        **{
            f"W{i:02d}": _ok_equity(ticker=f"W{i:02d}", interest_income=None)
            for i in range(15)
        },
    }
    engine = _engine(profiles, nport=FakeNport({"SPY": report}))
    calls = await _record_holding_screens(engine)
    result = await engine.screen("SPY")
    modes = {ticker: mode for ticker, _depth, mode, _finder, child in calls}
    for i in range(15):
        ticker = f"W{i:02d}"
        assert modes[ticker] is HoldingScreenMode.ACTIVITY_ONLY
        child = next(c[4] for c in calls if c[0] == ticker)
        assert "Ratios" not in child.votes
        assert child.votes["Activity"].vote is Vote.PASS
    nport = result.votes["NportHoldings"]
    assert nport.vote is Vote.PASS
    assert result.verdict == HALAL
    assert nport.metrics["unresolved_ratio_weight"] == pytest.approx(0.0)
    assert nport.metrics["coverage"] == pytest.approx(1.0)
    assert nport.metrics["activity_failed_holdings"] == []


@pytest.mark.asyncio
async def test_nport_material_ratio_holes_are_uncovered_not_haram() -> None:
    holes_ok = [
        Holding(name="Apple", pct=0.97, ticker="AAPL", asset_cat="EC"),
        Holding(name="H1", pct=0.01, ticker="H1", asset_cat="EC"),
        Holding(name="H2", pct=0.01, ticker="H2", asset_cat="EC"),
        Holding(name="H3", pct=0.01, ticker="H3", asset_cat="EC"),
    ]
    profiles_ok = {
        "SPY": _etf_profile(),
        "AAPL": _ok_equity(),
        "H1": _ok_equity(ticker="H1", interest_income=None),
        "H2": _ok_equity(ticker="H2", interest_income=None),
        "H3": _ok_equity(ticker="H3", interest_income=None),
    }
    three = await _engine(
        profiles_ok, nport=FakeNport({"SPY": NportReport("S1", holes_ok)})
    ).screen("SPY")
    m3 = three.votes["NportHoldings"].metrics
    assert three.votes["NportHoldings"].vote is Vote.PASS
    assert three.verdict == HALAL
    assert m3["coverage"] == pytest.approx(0.97)
    assert m3["unresolved_ratio_weight"] == pytest.approx(0.03)
    assert m3["activity_failed_holdings"] == []

    holes_fail = [
        Holding(name="Apple", pct=0.94, ticker="AAPL", asset_cat="EC"),
        *[
            Holding(name=f"H{i}", pct=0.01, ticker=f"H{i}", asset_cat="EC")
            for i in range(1, 7)
        ],
    ]
    profiles_fail = {
        "SPY": _etf_profile(),
        "AAPL": _ok_equity(),
        **{
            f"H{i}": _ok_equity(ticker=f"H{i}", interest_income=None)
            for i in range(1, 7)
        },
    }
    six = await _engine(
        profiles_fail, nport=FakeNport({"SPY": NportReport("S1", holes_fail)})
    ).screen("SPY")
    m6 = six.votes["NportHoldings"].metrics
    assert six.votes["NportHoldings"].vote is Vote.FAIL
    assert "incomplete" in six.votes["NportHoldings"].reason
    assert six.verdict == NOT_HALAL
    assert m6["coverage"] == pytest.approx(0.94)
    assert m6["unresolved_ratio_weight"] == pytest.approx(0.06)
    assert m6["activity_failed_holdings"] == []


@pytest.mark.asyncio
async def test_nport_ratio_fail_weight_cap() -> None:
    over = NportReport(
        series_id="S1",
        holdings=[
            Holding(name="Apple", pct=0.94, ticker="AAPL", asset_cat="EC"),
            Holding(name="DebtCo", pct=0.06, ticker="DEBT", asset_cat="EC"),
        ],
    )
    under = NportReport(
        series_id="S1",
        holdings=[
            Holding(name="Apple", pct=0.96, ticker="AAPL", asset_cat="EC"),
            Holding(name="DebtCo", pct=0.04, ticker="DEBT", asset_cat="EC"),
        ],
    )
    at_cap = NportReport(
        series_id="S1",
        holdings=[
            Holding(name="Apple", pct=0.95, ticker="AAPL", asset_cat="EC"),
            Holding(name="DebtCo", pct=0.05, ticker="DEBT", asset_cat="EC"),
        ],
    )
    profiles = {
        "SPY": _etf_profile(),
        "AAPL": _ok_equity(),
        "DEBT": _ok_equity(ticker="DEBT", total_debt=400.0),
    }
    fail = await _engine(profiles, nport=FakeNport({"SPY": over})).screen("SPY")
    fm = fail.votes["NportHoldings"].metrics
    assert fail.votes["NportHoldings"].vote is Vote.FAIL
    assert fail.verdict == NOT_HALAL
    assert fm["ratio_fail_weight"] == pytest.approx(0.06)
    assert fm["activity_failed_holdings"] == []
    assert fm["coverage"] >= 0.95

    ok = await _engine(profiles, nport=FakeNport({"SPY": under})).screen("SPY")
    om = ok.votes["NportHoldings"].metrics
    assert ok.votes["NportHoldings"].vote is Vote.PASS
    assert ok.verdict == HALAL
    assert om["ratio_fail_weight"] == pytest.approx(0.04)
    assert om["activity_failed_holdings"] == []

    edge = await _engine(profiles, nport=FakeNport({"SPY": at_cap})).screen("SPY")
    em = edge.votes["NportHoldings"].metrics
    assert edge.votes["NportHoldings"].vote is Vote.FAIL
    assert edge.verdict == NOT_HALAL
    assert em["ratio_fail_weight"] == pytest.approx(0.05)
    assert em["activity_failed_holdings"] == []
    assert em["coverage"] >= 0.95


@pytest.mark.asyncio
async def test_nport_activity_fail_any_weight() -> None:
    report = NportReport(
        series_id="S1",
        holdings=[
            Holding(name="Apple", pct=0.996, ticker="AAPL", asset_cat="EC"),
            Holding(name="JPMorgan", pct=0.004, ticker="JPM", asset_cat="EC"),
        ],
    )
    engine = _engine(
        {
            "SPY": _etf_profile(),
            "AAPL": _ok_equity(),
            "JPM": _ok_equity(
                ticker="JPM",
                sector="Financial Services",
                industry="Banks - Diversified",
            ),
        },
        nport=FakeNport({"SPY": report}),
    )
    calls = await _record_holding_screens(engine)
    result = await engine.screen("SPY")
    jpm = [c for c in calls if c[0] == "JPM"]
    assert jpm
    assert jpm[0][2] is HoldingScreenMode.ACTIVITY_ONLY
    assert jpm[0][4].votes["Activity"].vote is Vote.FAIL
    nport = result.votes["NportHoldings"]
    assert nport.vote is Vote.FAIL
    assert result.verdict == NOT_HALAL
    assert "JPM" in nport.metrics["activity_failed_holdings"]
    assert nport.metrics["coverage"] >= 0.95


@pytest.mark.asyncio
async def test_nport_nested_fund_is_activity_fail_not_uncovered() -> None:
    report = NportReport(
        series_id="S1",
        holdings=[
            Holding(name="Apple", pct=0.94, ticker="AAPL", asset_cat="EC"),
            Holding(name="Qqq", pct=0.06, ticker="QQQ", asset_cat="EC"),
        ],
    )
    engine = _engine(
        {
            "SPY": _etf_profile(),
            "QQQ": _etf_profile("QQQ"),
            "AAPL": _ok_equity(),
        },
        nport=FakeNport({"SPY": report}),
    )
    result = await engine.screen("SPY")
    nport = result.votes["NportHoldings"]
    metrics = nport.metrics
    assert nport.vote is Vote.FAIL
    assert result.verdict == NOT_HALAL
    assert "QQQ" in metrics["nested_fund_failed"]
    assert "QQQ" not in metrics["activity_failed_holdings"]
    assert metrics["unresolved_ratio_weight"] == pytest.approx(0.0)
    assert metrics["coverage"] >= 0.95


@pytest.mark.asyncio
async def test_nport_finder_cap_and_reset() -> None:
    spy = _SpyFinder()
    holdings_a = [
        Holding(name=f"T{i:02d}", pct=0.024, ticker=f"T{i:02d}", asset_cat="EC")
        for i in range(40)
    ]
    holdings_a.append(Holding(name="T40", pct=0.006, ticker="T40", asset_cat="EC"))
    holdings_a.append(Holding(name="Cash", pct=0.034, ticker=None, asset_cat="STIV"))
    holdings_b = [
        Holding(name=f"U{i:02d}", pct=0.024, ticker=f"U{i:02d}", asset_cat="EC")
        for i in range(40)
    ]
    holdings_b.append(Holding(name="U40", pct=0.006, ticker="U40", asset_cat="EC"))
    holdings_b.append(Holding(name="Cash", pct=0.034, ticker=None, asset_cat="STIV"))
    profiles = {
        "SPY": _etf_profile(),
        "QQQ": _etf_profile("QQQ"),
        **{f"T{i:02d}": _ok_equity(ticker=f"T{i:02d}") for i in range(40)},
        "T40": _ok_equity(ticker="T40", interest_income=None),
        **{f"U{i:02d}": _ok_equity(ticker=f"U{i:02d}") for i in range(40)},
        "U40": _ok_equity(ticker="U40", interest_income=None),
    }
    engine = _engine(
        profiles,
        nport=FakeNport(
            {
                "SPY": NportReport("S1", holdings_a),
                "QQQ": NportReport("S2", holdings_b),
            }
        ),
        finder=spy,
    )
    calls = await _record_holding_screens(engine)
    first = await engine.screen("SPY")
    first_finder = {
        ticker: finder
        for ticker, _depth, _mode, finder, _result in calls
        if ticker.startswith("T")
    }
    assert [first_finder[f"T{i:02d}"] for i in range(40)] == [True] * 40
    assert first_finder["T40"] is False
    assert "T40" not in spy.tickers
    for i in range(40):
        assert f"T{i:02d}" in spy.tickers
    fm = first.votes["NportHoldings"].metrics
    assert first.votes["NportHoldings"].vote is Vote.PASS
    assert first.verdict == HALAL
    assert "T40" not in fm["activity_failed_holdings"]
    assert fm["unresolved_ratio_weight"] == pytest.approx(0.006)
    assert fm["coverage"] >= 0.95

    spy.tickers.clear()
    del calls[:]
    second = await engine.screen("QQQ")
    second_finder = {
        ticker: finder
        for ticker, _depth, _mode, finder, _result in calls
        if ticker.startswith("U")
    }
    assert [second_finder[f"U{i:02d}"] for i in range(40)] == [True] * 40
    assert second_finder["U40"] is False
    assert "U40" not in spy.tickers
    for i in range(40):
        assert f"U{i:02d}" in spy.tickers
    assert second.votes["NportHoldings"].vote is Vote.PASS
    assert second.verdict == HALAL


@pytest.mark.asyncio
async def test_nport_slash_ticker_is_junk_not_activity_fail() -> None:
    spy = _SpyFinder()
    report = NportReport(
        series_id="S1",
        holdings=[
            Holding(name="Apple", pct=0.97, ticker="AAPL", asset_cat="EC"),
            Holding(name="Lennar B", pct=0.01, ticker="LEN/B", asset_cat="EC"),
            Holding(name="Cash", pct=0.02, ticker=None, asset_cat="STIV"),
        ],
    )
    engine = _engine(
        {"SPY": _etf_profile(), "AAPL": _ok_equity()},
        nport=FakeNport({"SPY": report}),
        finder=spy,
    )
    result = await engine.screen("SPY")
    metrics = result.votes["NportHoldings"].metrics
    assert "LEN/B" in metrics["junk_ids"]
    assert "LEN/B" not in metrics["activity_failed_holdings"]
    assert metrics["uncovered_weight"] == pytest.approx(0.01)
    assert "LEN/B" not in spy.tickers
    assert result.votes["NportHoldings"].vote is Vote.PASS
    assert result.verdict == HALAL

    six_pct = NportReport(
        series_id="S1",
        holdings=[
            Holding(name="Apple", pct=0.94, ticker="AAPL", asset_cat="EC"),
            Holding(name="Lennar B", pct=0.06, ticker="LEN/B", asset_cat="EC"),
        ],
    )
    hole = await _engine(
        {"SPY": _etf_profile(), "AAPL": _ok_equity()},
        nport=FakeNport({"SPY": six_pct}),
        finder=_SpyFinder(),
    ).screen("SPY")
    hm = hole.votes["NportHoldings"].metrics
    assert hole.votes["NportHoldings"].vote is Vote.FAIL
    assert "incomplete" in hole.votes["NportHoldings"].reason
    assert hole.verdict == NOT_HALAL
    assert hm["coverage"] == pytest.approx(0.94)
    assert "LEN/B" in hm["junk_ids"]
    assert hm["activity_failed_holdings"] == []


@pytest.mark.asyncio
async def test_nport_hw_conditional_ignored_not_halal_fails() -> None:
    cond_report = NportReport(
        series_id="S1",
        holdings=[
            Holding(name="Csx", pct=0.97, ticker="CSX", asset_cat="EC"),
            Holding(name="Cash", pct=0.03, ticker=None, asset_cat="STIV"),
        ],
    )
    fail_report = NportReport(
        series_id="S1",
        holdings=[
            Holding(name="Apple", pct=0.99, ticker="AAPL", asset_cat="EC"),
            Holding(name="Overlay", pct=0.01, ticker="KO", asset_cat="EC"),
        ],
    )
    cond = await _engine(
        {"SPY": _etf_profile(), "CSX": _ok_equity(ticker="CSX")},
        nport=FakeNport({"SPY": cond_report}),
        hw_records={"CSX": {"verdict": "conditional"}},
    ).screen("SPY")
    assert cond.votes["NportHoldings"].vote is Vote.PASS
    assert cond.verdict == HALAL
    assert "CSX" not in cond.votes["NportHoldings"].metrics["activity_failed_holdings"]

    failed = await _engine(
        {
            "SPY": _etf_profile(),
            "AAPL": _ok_equity(),
            "KO": _ok_equity(ticker="KO"),
        },
        nport=FakeNport({"SPY": fail_report}),
        hw_records={"KO": {"verdict": "not_halal"}},
    ).screen("SPY")
    assert failed.votes["NportHoldings"].vote is Vote.FAIL
    assert failed.verdict == NOT_HALAL
    assert "KO" in failed.votes["NportHoldings"].metrics["activity_failed_holdings"]


@pytest.mark.asyncio
async def test_nport_full_then_activity_only_cache_has_no_ratios() -> None:
    spy = _SpyFinder()
    full_report = NportReport(
        series_id="S1",
        holdings=[
            Holding(name="Microsoft", pct=0.97, ticker="MSFT", asset_cat="EC"),
            Holding(name="Cash", pct=0.03, ticker=None, asset_cat="STIV"),
        ],
    )
    small_report = NportReport(
        series_id="S2",
        holdings=[
            Holding(name="Apple", pct=0.996, ticker="AAPL", asset_cat="EC"),
            Holding(name="Microsoft", pct=0.004, ticker="MSFT", asset_cat="EC"),
        ],
    )
    engine = _engine(
        {
            "SPY": _etf_profile(),
            "QQQ": _etf_profile("QQQ"),
            "MSFT": _ok_equity(
                ticker="MSFT",
                sector="Technology",
                industry="Software - Infrastructure",
            ),
            "AAPL": _ok_equity(),
        },
        nport=FakeNport({"SPY": full_report, "QQQ": small_report}),
        finder=spy,
    )
    calls = await _record_holding_screens(engine)
    await engine.screen("SPY")
    full_msft = [c for c in calls if c[0] == "MSFT"]
    assert full_msft
    assert full_msft[0][2] is HoldingScreenMode.FULL
    assert "Ratios" in full_msft[0][4].votes
    del calls[:]
    await engine.screen("QQQ")
    small_msft = [c for c in calls if c[0] == "MSFT"]
    assert small_msft
    assert small_msft[0][2] is HoldingScreenMode.ACTIVITY_ONLY
    assert small_msft[0][3] is False
    assert "Ratios" not in small_msft[0][4].votes
    assert small_msft[0][4].votes["Activity"].vote is Vote.PASS
