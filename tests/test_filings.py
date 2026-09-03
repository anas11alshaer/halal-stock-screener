"""10-K excerpts: canned HTML, no live EDGAR."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from config import POLICY_PATH
from filings import FilingFetchError, FilingsClient
from policy import load_policy

MSFT_CIK = "0000789019"
ITEM1_HTML = """<html><body>
<div>UNITED STATES SECURITIES AND EXCHANGE COMMISSION</div>
<div>ITEM 1. BUSINESS</div>
<p>We develop Xbox consoles and Game Pass subscription services.</p>
<p>The company also sells cloud software to enterprises worldwide.</p>
<div>ITEM 1A. RISK FACTORS</div>
<p>We face competition in software markets.</p>
<div>ITEM 1B. UNRESOLVED STAFF COMMENTS</div>
<p>None.</p>
</body></html>"""
SEGMENTS_HTML = """<html><body>
<div>ITEM 1. BUSINESS</div>
<p>We sell productivity software to enterprises worldwide.</p>
<div>ITEM 1A. RISK FACTORS</div>
<p>We face competition.</p>
<div>NOTE 18. SEGMENT INFORMATION</div>
<p>Gaming revenue was $23.455 billion of total $281.724 billion.</p>
<div>NOTE 19. COMMITMENTS</div>
<p>Lease obligations are disclosed here.</p>
</body></html>"""


async def _no_sleep(_: float) -> None:
    return None


def _submissions() -> dict:
    return {
        "filings": {
            "recent": {
                "form": ["10-Q", "10-K", "8-K"],
                "accessionNumber": [
                    "0000789019-25-000001",
                    "0000789019-25-000008",
                    "0000789019-25-000009",
                ],
                "primaryDocument": ["q.htm", "msft-10k.htm", "8k.htm"],
            }
        }
    }


def _handler(html: str, requested: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requested.append(url)
        if url.endswith("company_tickers.json"):
            return httpx.Response(
                200, json={"0": {"ticker": "MSFT", "cik_str": 789019}}
            )
        if "submissions" in url:
            return httpx.Response(200, json=_submissions())
        if "Archives" in url:
            return httpx.Response(200, text=html)
        return httpx.Response(404)

    return handler


@pytest.mark.asyncio
async def test_item1_extracted_from_canned_html(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("config.SEC_CONTACT_EMAIL", "filings-tests@example.com")
    requested: list[str] = []
    policy = load_policy(POLICY_PATH)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_handler(ITEM1_HTML, requested))
    ) as http:
        excerpts = await FilingsClient(policy, http, sleep=_no_sleep).tenk_excerpts(
            ticker="MSFT", cik=MSFT_CIK
        )
    assert "item1" in excerpts
    assert "Xbox" in excerpts["item1"]
    assert "Game Pass" in excerpts["item1"]
    assert "RISK FACTORS" not in excerpts["item1"]
    assert any("msft-10k.htm" in url for url in requested)
    assert not any(url.endswith("q.htm") for url in requested)


@pytest.mark.asyncio
async def test_missing_segment_heading_omits_segments_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("config.SEC_CONTACT_EMAIL", "filings-tests@example.com")
    policy = load_policy(POLICY_PATH)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_handler(ITEM1_HTML, []))
    ) as http:
        excerpts = await FilingsClient(policy, http, sleep=_no_sleep).tenk_excerpts(
            ticker="MSFT", cik=MSFT_CIK
        )
    assert "item1" in excerpts
    assert "segments" not in excerpts


@pytest.mark.asyncio
async def test_note18_heading_extracts_segments_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("config.SEC_CONTACT_EMAIL", "filings-tests@example.com")
    policy = load_policy(POLICY_PATH)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_handler(SEGMENTS_HTML, []))
    ) as http:
        excerpts = await FilingsClient(policy, http, sleep=_no_sleep).tenk_excerpts(
            ticker="MSFT", cik=MSFT_CIK
        )
    assert "segments" in excerpts
    assert "23.455" in excerpts["segments"]
    assert "COMMITMENTS" not in excerpts["segments"]


@pytest.mark.asyncio
async def test_excerpt_length_capped_by_finder_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("config.SEC_CONTACT_EMAIL", "filings-tests@example.com")
    policy = load_policy(POLICY_PATH)
    policy.raw.setdefault("finder", {})["max_excerpt_chars"] = 40
    long_html = (
        "<html><body><div>ITEM 1. BUSINESS</div><p>"
        + ("cloud software " * 80)
        + "</p><div>ITEM 1A. RISK FACTORS</div><p>risk</p></body></html>"
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_handler(long_html, []))
    ) as http:
        excerpts = await FilingsClient(policy, http, sleep=_no_sleep).tenk_excerpts(
            ticker="MSFT", cik=MSFT_CIK
        )
    assert "item1" in excerpts
    assert len(excerpts["item1"]) <= 40
    committed = int(load_policy(POLICY_PATH).section("finder")["max_excerpt_chars"])
    assert committed >= 40
    assert len(excerpts["item1"]) < committed


def test_filings_uses_stdlib_html_parser_only() -> None:
    src = (Path(__file__).parent.parent / "src" / "filings.py").read_text(
        encoding="utf-8"
    )
    assert "html.parser" in src
    folded = src.casefold()
    assert "bs4" not in folded
    assert "beautifulsoup" not in folded
    assert "lxml" not in folded
    assert "selectolax" not in folded
    assert "html5lib" not in folded


@pytest.mark.asyncio
async def test_tenk_excerpts_resolves_cik_via_shared_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("config.SEC_CONTACT_EMAIL", "filings-tests@example.com")
    requested: list[str] = []
    policy = load_policy(POLICY_PATH)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_handler(ITEM1_HTML, requested))
    ) as http:
        excerpts = await FilingsClient(policy, http, sleep=_no_sleep).tenk_excerpts(
            ticker="MSFT"
        )
    assert "item1" in excerpts
    assert any(url.endswith("company_tickers.json") for url in requested)
    assert any("CIK0000789019" in url for url in requested)


@pytest.mark.asyncio
async def test_archives_http_403_is_fetch_error_not_empty_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("config.SEC_CONTACT_EMAIL", "filings-tests@example.com")
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requested.append(url)
        if "submissions" in url:
            return httpx.Response(200, json=_submissions())
        if "Archives" in url:
            return httpx.Response(403, text="forbidden")
        return httpx.Response(404)

    policy = load_policy(POLICY_PATH)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(FilingFetchError, match="archives HTTP 403"):
            await FilingsClient(policy, http, sleep=_no_sleep).tenk_excerpts(
                ticker="MSFT", cik=MSFT_CIK
            )
    assert any("Archives" in url for url in requested)
