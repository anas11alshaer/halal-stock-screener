"""Searcher + checker 10-K enrich. Never returns a verdict."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from filings import FilingsClient
from market_data import cik_for
from nvidia_nim import (
    NimError,
    NvidiaNimClient,
    _json_blob,
    claims_cited,
    payload_has_forbidden,
)
from plugins.base import (
    FactState,
    Fundamentals,
    ScreenContext,
    Segment,
    is_fund,
)
from policy import Policy

logger = logging.getLogger(__name__)

CheckerFn = Callable[[dict[str, Any], str], Awaitable[dict[str, Any]]]
CikLookup = Callable[[str], Awaitable[str | None]]

_SEARCHER_PRIORITY = ("6", "12", "3", "5", "7", "4")
_PROTECTED_SEARCHER = frozenset({"6", "12"})
_SLOT_JOBS = frozenset({"6", "12", "3", "5", "4"})

_RATIO_ATTRS = {
    "interest": "interest_income",
    "interest_income": "interest_income",
    "interest-income": "interest_income",
    "revenue": "revenue",
    "debt": "total_debt",
    "total_debt": "total_debt",
    "cash": "cash_and_securities",
    "cash_and_securities": "cash_and_securities",
}
_AR_ATTRS = {
    "ar": "accounts_receivable",
    "accounts_receivable": "accounts_receivable",
    "receivable": "accounts_receivable",
    "receivables": "accounts_receivable",
}
_RATIO_MISSING = (
    "interest_income",
    "revenue",
    "total_debt",
    "cash_and_securities",
)


@dataclass
class _CikCache:
    segments: list[Segment]
    business_description: str | None
    segments_state: FactState | None


class NoOpFinder:
    """Leaves fundamentals unchanged when NVIDIA_API_KEY is missing or tests inject this."""

    async def enrich(self, ctx: ScreenContext) -> Fundamentals:
        return ctx.fundamentals


class DataFinder:
    def __init__(
        self,
        policy: Policy,
        nim: NvidiaNimClient,
        filings: FilingsClient,
        *,
        checker: CheckerFn | None = None,
        cik_lookup: CikLookup | None = None,
    ) -> None:
        self._policy = policy
        self._nim = nim
        self._filings = filings
        self._checker = checker
        self._cik_lookup = cik_lookup
        self._by_cik: dict[str, _CikCache] = {}
        self._excerpts_by_cik: dict[str, dict[str, str]] = {}
        self.searcher_calls = 0
        self.checker_calls = 0
        self.jobs_run: list[str] = []

    async def enrich(self, ctx: ScreenContext) -> Fundamentals:
        """Engine calls this once. Inside: searcher then checker, bounded retries.

        Never returns a verdict.
        """
        fundamentals = replace(
            ctx.fundamentals,
            segments=list(ctx.fundamentals.segments),
            fact_states=dict(ctx.fundamentals.fact_states),
        )
        self.searcher_calls = 0
        self.checker_calls = 0
        self.jobs_run = []
        if is_fund(ctx.quote_type, self._policy):
            return fundamentals

        caps = _finder_caps(self._policy)
        cik = await self._cik(ctx.ticker)
        cached = self._by_cik.get(cik) if cik else None
        excerpts, fetch_ok = await self._excerpts(ctx.ticker, cik)
        body_present = bool(excerpts)
        excerpt_list = [excerpts[k] for k in ("item1", "segments") if excerpts.get(k)]
        if excerpts.get("item1") and not fundamentals.business_description:
            fundamentals.business_description = excerpts["item1"]

        if cached and cached.segments and not fundamentals.segments:
            fundamentals.segments = [replace(seg) for seg in cached.segments]
            if cached.segments_state is not None:
                fundamentals.fact_states["segments"] = cached.segments_state
            if cached.business_description and not fundamentals.business_description:
                fundamentals.business_description = cached.business_description

        selected = self._select_jobs(fundamentals, cached, caps)

        if "6" in selected:
            await self._job_segments(fundamentals, excerpt_list, body_present, caps)
        if "7" in selected:
            self._job7_keyword(fundamentals)
        if "12" in selected:
            self._job12_keyword(fundamentals, excerpts.get("item1") or "")
        if "3" in selected:
            await self._job_numbers(
                fundamentals,
                excerpt_list,
                body_present,
                caps,
                job="3",
                attrs=_RATIO_ATTRS,
                missing=_RATIO_MISSING,
                fetch_ok=fetch_ok,
            )
        if "5" in selected:
            await self._job_industry(fundamentals, excerpt_list, caps)
        if "4" in selected:
            await self._job_numbers(
                fundamentals,
                excerpt_list,
                body_present,
                caps,
                job="4",
                attrs=_AR_ATTRS,
                missing=("accounts_receivable",),
                fetch_ok=fetch_ok,
            )

        if cik:
            self._by_cik[cik] = _CikCache(
                segments=[replace(seg) for seg in fundamentals.segments],
                business_description=fundamentals.business_description,
                segments_state=fundamentals.fact_states.get("segments"),
            )
        return fundamentals

    async def _cik(self, ticker: str) -> str | None:
        try:
            if self._cik_lookup is not None:
                return await self._cik_lookup(ticker)
            http = getattr(self._filings, "_http", None)
            return await cik_for(ticker, http=http, policy=self._policy)
        except Exception as exc:
            logger.warning("CIK lookup failed for %s: %s", ticker, exc)
            return None

    async def _excerpts(
        self, ticker: str, cik: str | None
    ) -> tuple[dict[str, str], bool]:
        if cik and cik in self._excerpts_by_cik:
            return dict(self._excerpts_by_cik[cik]), True
        try:
            excerpts = await self._filings.tenk_excerpts(ticker=ticker, cik=cik)
        except Exception as exc:
            logger.warning("10-K excerpts failed for %s: %s", ticker, exc)
            return {}, False
        if not isinstance(excerpts, dict):
            return {}, False
        if cik:
            self._excerpts_by_cik[cik] = dict(excerpts)
        return excerpts, True

    def _select_jobs(
        self,
        fundamentals: Fundamentals,
        cached: _CikCache | None,
        caps: dict[str, int],
    ) -> list[str]:
        need: set[str] = set()
        have_cached_segments = bool(cached and cached.segments)
        segments_needed = (not fundamentals.segments) or fundamentals.fact_states.get(
            "segments"
        ) is FactState.MISSING
        if segments_needed and not have_cached_segments:
            need.update({"6", "7", "12"})
        elif fundamentals.segments:
            need.add("7")
        if any(_still_missing(fundamentals, name) for name in _RATIO_MISSING):
            need.add("3")
        if _still_missing(fundamentals, "accounts_receivable"):
            need.add("4")
        if not (fundamentals.sector and fundamentals.industry):
            need.add("5")

        selected: list[str] = []
        slots = 0
        cap = caps["max_searcher_calls_per_equity"]
        for job in _SEARCHER_PRIORITY:
            if job not in need:
                continue
            takes_slot = job in _SLOT_JOBS
            if takes_slot and job not in _PROTECTED_SEARCHER and slots >= cap:
                continue
            selected.append(job)
            if takes_slot:
                slots += 1
        return selected

    async def _job_segments(
        self,
        fundamentals: Fundamentals,
        excerpts: Sequence[str],
        body_present: bool,
        caps: dict[str, int],
    ) -> None:
        filled = await self._search_proof(
            job="6",
            excerpts=excerpts,
            caps=caps,
            prompt_extra=(
                'Schema: {"segments":[{"name":string,"revenue":number|null,'
                '"pct":number|null,"excerpt":string}]}'
            ),
            apply=lambda blob, _excerpt, _terminal: _apply_segments(fundamentals, blob),
        )
        if fundamentals.segments:
            fundamentals.fact_states["segments"] = FactState.FOUND
            return
        if filled:
            fundamentals.fact_states["segments"] = FactState.MISSING
            return
        if body_present:
            fundamentals.fact_states["segments"] = FactState.DOUBTFUL
        elif "segments" not in fundamentals.fact_states:
            fundamentals.fact_states["segments"] = FactState.MISSING

    def _job7_keyword(self, fundamentals: Fundamentals) -> None:
        self.jobs_run.append("7")
        specs = _segment_specs(self._policy)
        for seg in fundamentals.segments:
            known = set(seg.tags)
            for tag in _tags_for_text(seg.name, specs):
                if tag not in known:
                    seg.tags.append(tag)
                    known.add(tag)

    def _job12_keyword(self, fundamentals: Fundamentals, item1: str) -> None:
        self.jobs_run.append("12")
        if not item1:
            return
        tags = _tags_for_text(item1, _segment_specs(self._policy))
        if not tags:
            return
        fundamentals.segments.append(
            Segment(
                name="Item 1",
                revenue=None,
                revenue_pct=None,
                tags=tags,
                state=FactState.FOUND,
                excerpt=item1[:500],
            )
        )
        if fundamentals.fact_states.get("segments") is not FactState.FOUND:
            fundamentals.fact_states["segments"] = FactState.FOUND

    async def _job_numbers(
        self,
        fundamentals: Fundamentals,
        excerpts: Sequence[str],
        body_present: bool,
        caps: dict[str, int],
        *,
        job: str,
        attrs: dict[str, str],
        missing: Sequence[str],
        fetch_ok: bool,
    ) -> None:
        targets = [name for name in missing if _still_missing(fundamentals, name)]
        if not targets:
            return
        financial = _is_financial_issuer(fundamentals, self._policy)
        if not excerpts:
            if fetch_ok and not financial:
                for name in targets:
                    if _still_missing(fundamentals, name):
                        setattr(fundamentals, name, 0.0)
                        fundamentals.fact_states[name] = FactState.ZERO
            else:
                for name in targets:
                    if _still_missing(fundamentals, name):
                        fundamentals.fact_states[name] = FactState.MISSING
            return
        filled = await self._search_proof(
            job=job,
            excerpts=excerpts,
            caps=caps,
            prompt_extra="Schema: JSON object mapping field names to number or null.",
            apply=lambda blob, _excerpt, terminal: _apply_numbers(
                fundamentals,
                blob,
                attrs,
                financial=financial,
                terminal=terminal,
            ),
        )
        if filled:
            return
        state = FactState.DOUBTFUL if body_present else FactState.MISSING
        for name in targets:
            if _still_missing(fundamentals, name):
                fundamentals.fact_states[name] = state

    async def _job_industry(
        self,
        fundamentals: Fundamentals,
        excerpts: Sequence[str],
        caps: dict[str, int],
    ) -> None:
        await self._search_proof(
            job="5",
            excerpts=excerpts,
            caps=caps,
            prompt_extra=(
                'Schema: {"sector":string|null,"industry":string|null,"gics":string|null}'
            ),
            apply=lambda blob, _excerpt, _terminal: _apply_industry(fundamentals, blob),
        )

    async def _search_proof(
        self,
        *,
        job: str,
        excerpts: Sequence[str],
        caps: dict[str, int],
        prompt_extra: str,
        apply: Callable[[dict[str, Any], str, bool], bool],
    ) -> bool:
        sources = [text for text in excerpts if text]
        if not sources:
            return False
        attempts = 0
        max_attempts = caps["searcher_attempts_per_field"]
        filled = False
        n = len(sources)
        for i, excerpt in enumerate(sources):
            if attempts >= max_attempts:
                break
            if self.searcher_calls >= caps["max_searcher_calls_per_equity"]:
                break
            raw = await self._chat_job(
                job, _searcher_prompt(job, excerpt, prompt_extra)
            )
            self.searcher_calls += 1
            attempts += 1
            self.jobs_run.append(job)
            blob = _searcher_blob(raw)
            if blob is None:
                continue
            if self.checker_calls >= caps["max_checker_calls_per_equity"]:
                break
            accepted = await self._run_checker(blob, excerpt)
            self.checker_calls += 1
            if not accepted:
                continue
            if apply(blob, excerpt, i == n - 1):
                filled = True
        return filled

    async def _run_checker(self, candidate: dict[str, Any], excerpt: str) -> bool:
        try:
            if self._checker is not None:
                result = await self._checker(candidate, excerpt)
            else:
                raw = await self._chat_job(
                    "checker", _checker_prompt(candidate, excerpt)
                )
                if raw is None or payload_has_forbidden(raw):
                    return False
                parsed = _json_blob(raw)
                result = parsed if isinstance(parsed, dict) else {}
        except Exception as exc:
            logger.warning("checker failed: %s", exc)
            return False
        if not isinstance(result, dict):
            return False
        if payload_has_forbidden(json.dumps(result)):
            return False
        if result.get("accepted") is not True:
            return False
        return claims_cited(candidate, excerpt)

    async def _chat_job(self, job: str, prompt: str) -> str | None:
        table = _nvidia_job_table(self._policy, job)
        winner = str(table.get("winner") or "")
        fallback = str(table.get("fallback_429") or "")
        if not winner:
            return None
        raw_tokens = self._policy.get("finder", "max_tokens", default=2048)
        max_tokens = int(raw_tokens or 2048)
        try:
            return await self._nim.chat_text(
                model_id=winner, prompt=prompt, max_tokens=max_tokens
            )
        except NimError as exc:
            if exc.status_code == 429 and fallback:
                try:
                    return await self._nim.chat_text(
                        model_id=fallback, prompt=prompt, max_tokens=max_tokens
                    )
                except NimError as retry_exc:
                    logger.warning("NIM %s fallback failed: %s", job, retry_exc)
                    return None
            logger.warning("NIM %s failed: %s", job, exc)
            return None
        except Exception as exc:
            logger.warning("NIM %s failed: %s", job, exc)
            return None


def _finder_caps(policy: Policy) -> dict[str, int]:
    section = policy.section("finder")

    def _int(name: str, default: int) -> int:
        raw = section.get(name, default)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return default
        return value if value > 0 else default

    return {
        "max_searcher_calls_per_equity": _int("max_searcher_calls_per_equity", 4),
        "max_checker_calls_per_equity": _int("max_checker_calls_per_equity", 4),
        "searcher_attempts_per_field": _int("searcher_attempts_per_field", 3),
    }


def _nvidia_job_table(policy: Policy, job: str) -> dict[str, Any]:
    jobs = policy.section("nvidia").get("jobs") or {}
    if not isinstance(jobs, dict):
        return {}
    table = jobs.get(job)
    if table is None and job.isdigit():
        table = jobs.get(int(job))
    return table if isinstance(table, dict) else {}


def _still_missing(fundamentals: Fundamentals, name: str) -> bool:
    if getattr(fundamentals, name) is not None:
        return False
    state = fundamentals.fact_states.get(name)
    return state is None or state is FactState.MISSING


def _is_financial_issuer(fundamentals: Fundamentals, policy: Policy) -> bool:
    denied = {
        str(s).casefold()
        for s in (policy.section("activity").get("denied_sectors") or [])
    }
    sector = (fundamentals.sector or "").casefold()
    return bool(sector) and sector in denied


def _segment_specs(policy: Policy) -> list[dict[str, Any]]:
    raw = policy.section("activity").get("segments") or []
    return [spec for spec in raw if isinstance(spec, dict) and spec.get("id")]


def _tags_for_text(text: str, specs: Sequence[dict[str, Any]]) -> list[str]:
    folded = (text or "").casefold()
    if not folded:
        return []
    tags: list[str] = []
    for spec in specs:
        sid = str(spec.get("id") or "")
        if not sid:
            continue
        keywords = spec.get("keywords") or []
        if any(str(keyword).casefold() in folded for keyword in keywords if keyword):
            tags.append(sid)
    return tags


def _searcher_prompt(job: str, excerpt: str, extra: str) -> str:
    return (
        f"[job {job}]\n{extra}\n"
        "Return a JSON object only. Every number must appear in the excerpt.\n"
        f"Excerpt:\n{excerpt}\n"
    )


def _checker_prompt(candidate: dict[str, Any], excerpt: str) -> str:
    return (
        "[job checker]\n"
        'Return {"accepted": bool, "reason": string}. '
        "accepted is true only if every number in the candidate appears in the excerpt.\n"
        f"Candidate:\n{json.dumps(candidate)}\nExcerpt:\n{excerpt}\n"
    )


def _searcher_blob(raw: str | None) -> dict[str, Any] | None:
    if not raw or payload_has_forbidden(raw):
        return None
    blob = _json_blob(raw)
    return blob if isinstance(blob, dict) else None


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _apply_segments(fundamentals: Fundamentals, blob: dict[str, Any]) -> bool:
    rows = blob.get("segments")
    if not isinstance(rows, list) or not rows:
        return False
    existing = {seg.name.casefold() for seg in fundamentals.segments}
    added = False
    for item in rows:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name.casefold() in existing:
            continue
        excerpt = item.get("excerpt")
        fundamentals.segments.append(
            Segment(
                name=name,
                revenue=_as_float(item.get("revenue")),
                revenue_pct=_as_float(
                    item.get("pct") if "pct" in item else item.get("revenue_pct")
                ),
                tags=[],
                state=FactState.FOUND,
                excerpt=str(excerpt) if excerpt is not None else None,
            )
        )
        existing.add(name.casefold())
        added = True
    return added


def _apply_numbers(
    fundamentals: Fundamentals,
    blob: dict[str, Any],
    attrs: dict[str, str],
    *,
    financial: bool,
    terminal: bool,
) -> bool:
    updates: list[tuple[str, float | None]] = []
    field = blob.get("field")
    if isinstance(field, str) and field.casefold() in attrs:
        updates.append((attrs[field.casefold()], _as_float(blob.get("value"))))
    raw_fields = blob.get("fields")
    if isinstance(raw_fields, list):
        for item in raw_fields:
            if not isinstance(item, dict):
                continue
            name = item.get("field")
            if isinstance(name, str) and name.casefold() in attrs:
                updates.append((attrs[name.casefold()], _as_float(item.get("value"))))
    for key, attr in attrs.items():
        if key in blob and key not in {"field", "value"}:
            updates.append((attr, _as_float(blob.get(key))))
    seen: set[str] = set()
    filled = False
    for attr, value in updates:
        if attr in seen:
            continue
        seen.add(attr)
        if not _still_missing(fundamentals, attr):
            continue
        if value is None:
            if financial or not terminal:
                continue
            setattr(fundamentals, attr, 0.0)
            fundamentals.fact_states[attr] = FactState.ZERO
            filled = True
            continue
        if value == 0:
            if financial:
                continue
            setattr(fundamentals, attr, 0.0)
            fundamentals.fact_states[attr] = FactState.ZERO
            filled = True
            continue
        setattr(fundamentals, attr, value)
        fundamentals.fact_states[attr] = FactState.FOUND
        filled = True
    return filled


def _apply_industry(fundamentals: Fundamentals, blob: dict[str, Any]) -> bool:
    filled = False
    sector = blob.get("sector")
    industry = blob.get("industry")
    gics = blob.get("gics")
    if isinstance(sector, str) and sector.strip() and not fundamentals.sector:
        fundamentals.sector = sector.strip()
        filled = True
    if isinstance(industry, str) and industry.strip() and not fundamentals.industry:
        fundamentals.industry = industry.strip()
        filled = True
    if isinstance(gics, str) and gics.strip() and not fundamentals.gics:
        fundamentals.gics = gics.strip()
        filled = True
    return filled
