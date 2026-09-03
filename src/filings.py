"""10-K Item 1 / segment excerpts via stdlib html.parser (no third-party HTML libs)."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from html.parser import HTMLParser
from typing import Any

import httpx

from market_data import cik_for, paced_sec_get
from policy import Policy

logger = logging.getLogger(__name__)


class FilingFetchError(Exception):
    """EDGAR lookup or HTTP failed — not the same as a 10-K with no Item 1."""


_ITEM1_START = re.compile(
    r"(?im)(?:^|\n)\s*item\s*1(?![a-z0-9])(?:\s*\.?\s*business\b)?"
)
_ITEM1_END = re.compile(r"(?im)(?:^|\n)\s*item\s*(?:1[a-z]\b|[2-9]\b|1[0-9]\b)")
_SEGMENT_START = re.compile(
    r"(?im)(?:^|\n)\s*(?:"
    r"note\s*18\b"
    r"|note\s+\d+[^\n]{0,80}\bsegments?\b"
    r"|[^\n]{0,80}\bsegment(?:s)?(?:\s+information|\s+reporting)?\b"
    r")"
)
_SEGMENT_END = re.compile(r"(?im)(?:^|\n)\s*(?:note\s+(?!18\b)\d+\b|item\s*[1-9])")
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "br",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "table",
        "thead",
        "tbody",
        "section",
        "article",
        "header",
        "title",
        "blockquote",
        "pre",
        "hr",
        "ul",
        "ol",
        "td",
        "th",
    }
)
_SKIP_TAGS = frozenset({"script", "style", "noscript"})


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        if tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        if tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = " ".join(data.split())
        if text:
            self._chunks.append(text)

    def text(self) -> str:
        parts: list[str] = []
        buf: list[str] = []
        for chunk in self._chunks:
            if chunk == "\n":
                if buf:
                    parts.append(" ".join(buf))
                    buf = []
                parts.append("\n")
            else:
                buf.append(chunk)
        if buf:
            parts.append(" ".join(buf))
        text = "".join(parts)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def html_to_text(html: str) -> str:
    parser = _HTMLText()
    parser.feed(html)
    parser.close()
    return parser.text()


def _best_section(
    text: str, start_re: re.Pattern[str], end_re: re.Pattern[str]
) -> str | None:
    starts = list(start_re.finditer(text))
    if not starts:
        return None
    best = ""
    for match in starts:
        rest = text[match.start() :]
        end = end_re.search(rest, pos=len(match.group(0)))
        chunk = rest[: end.start()] if end else rest
        if len(chunk) > len(best):
            best = chunk
    return best or None


class FilingsClient:
    def __init__(
        self,
        policy: Policy,
        http: httpx.AsyncClient,
        *,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._policy = policy
        self._http = http
        self._sleep = sleep or asyncio.sleep

    async def tenk_excerpts(
        self, *, ticker: str, cik: str | None = None
    ) -> dict[str, str]:
        """Keys subset of {"item1", "segments"} — absent key means missing, not zero.

        If cik is None, resolve via the shared cik_for() helper.
        """
        if not cik:
            try:
                cik = await cik_for(ticker, http=self._http, policy=self._policy)
            except Exception as exc:
                raise FilingFetchError("CIK lookup failed") from exc
        if not cik:
            raise FilingFetchError(f"no CIK for {ticker}")
        url = await self._latest_tenk_url(cik)
        if not url:
            return {}
        response = await self._edgar_get(url)
        if response.status_code >= 400:
            raise FilingFetchError(f"archives HTTP {response.status_code} for {ticker}")
        text = html_to_text(response.text)
        if not text:
            return {}
        max_chars = _max_excerpt_chars(self._policy)
        out: dict[str, str] = {}
        item1 = _best_section(text, _ITEM1_START, _ITEM1_END)
        if item1:
            out["item1"] = item1[:max_chars]
        segments = _best_section(text, _SEGMENT_START, _SEGMENT_END)
        if segments:
            out["segments"] = segments[:max_chars]
        return out

    async def _latest_tenk_url(self, cik: str) -> str | None:
        padded = "".join(ch for ch in str(cik) if ch.isdigit()).zfill(10)
        submissions = str(
            self._policy.section("sources")["sec_submissions_url"]
        ).format(cik=padded)
        response = await self._edgar_get(submissions)
        if response.status_code >= 400:
            raise FilingFetchError(f"submissions HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise FilingFetchError("submissions JSON") from exc
        forms, accessions, documents = _recent_filings(data)
        archives = str(self._policy.section("sources")["sec_archives_url"])
        cik_int = str(int(padded))
        for form, accession, document in zip(
            forms, accessions, documents, strict=False
        ):
            if str(form).upper() not in {"10-K", "10-K/A"}:
                continue
            return archives.format(
                cik=cik_int,
                accession=str(accession).replace("-", ""),
                document=str(document),
            )
        return None

    async def _edgar_get(self, url: str) -> httpx.Response:
        return await paced_sec_get(self._http, url, self._policy, sleep=self._sleep)


def _max_excerpt_chars(policy: Policy) -> int:
    raw = policy.get("finder", "max_excerpt_chars", default=12000)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 12000
    return value if value > 0 else 12000


def _recent_filings(data: Any) -> tuple[list[Any], list[Any], list[Any]]:
    if not isinstance(data, dict):
        return [], [], []
    recent = (data.get("filings") or {}).get("recent") or {}
    if not isinstance(recent, dict):
        return [], [], []
    return (
        list(recent.get("form") or []),
        list(recent.get("accessionNumber") or []),
        list(recent.get("primaryDocument") or []),
    )
