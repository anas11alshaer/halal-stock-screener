"""Text NIM job scorer (searcher / checker). Number normalization + citation.

Usage (from repo root):

    python scripts/eval_nim_jobs.py --job notes --fixtures path.json

Fixtures are canned model outputs (JSON). This script does not call NVIDIA.
Missing NVIDIA_API_KEY, missing fixtures, or a missing job table exits 2
(no fake winner). Write winner only if at least one model has errors == 0.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from config import LOG_LEVEL, NVIDIA_API_KEY, POLICY_PATH  # noqa: E402
from nvidia_nim import write_job_winner  # noqa: E402
from policy import load_policy  # noqa: E402

# Skip metadata/echo fields; they are not claimed facts.
_SKIP_NUMBER_KEYS = frozenset({"confidence", "scale", "accepted", "excerpt", "reason"})
_FORBIDDEN_RE = re.compile(r"\b(NOT_HALAL|HALAL|verdict|core_fail)\b", re.I)
_NUM_RE = re.compile(
    r"""
    (?P<dollar>\$)?
    (?P<num>
        \d{1,3}(?:,\d{3})+(?:\.\d+)?
        |
        \d+\.\d+
        |
        \d+
    )
    (?:
        \s*(?P<word>billions?|millions?|percent)
        |
        \s*(?P<sym>%)
        |
        (?P<letter>[BbMm])(?![A-Za-z])
    )?
    """,
    re.VERBOSE | re.IGNORECASE,
)


@dataclass
class TextScore:
    model_id: str = ""
    valid_json: bool = False
    has_verdict_keys: bool = False
    citation_rate: float = 0.0
    exact: bool = False
    errors: int = 0
    rate_limited: bool = False
    accepted: bool | None = None
    expected_accepted: bool | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class TextBakeoffResult:
    scores: list[TextScore]
    winner: str = ""
    fallback_429: str = ""


def payload_has_forbidden(text: str) -> bool:
    return _FORBIDDEN_RE.search(text) is not None


def extract_normalized_numbers(text: str) -> list[float]:
    """Scaled magnitudes: strip $ and commas; million×1e6; billion×1e9; % unchanged."""
    found: list[float] = []
    for match in _NUM_RE.finditer(text):
        raw = match.group("num").replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        word = (match.group("word") or "").lower()
        letter = (match.group("letter") or "").lower()
        sym = match.group("sym") or ""
        # Item 1B is an SEC heading, not $1B; scale B/M only with $ or decimal/comma.
        letter_ok = bool(match.group("dollar")) or (
            "," in match.group("num") or "." in match.group("num")
        )
        if word.startswith("billion") or (letter == "b" and letter_ok):
            value *= 1_000_000_000.0
        elif word.startswith("million") or (letter == "m" and letter_ok):
            value *= 1_000_000.0
        elif word == "percent" or sym == "%":
            pass
        found.append(value)
    return found


def numbers_close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-6, abs_tol=1e-3)


def _parse_json(text: str) -> Any | None:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", stripped, re.S)
    if fenced:
        stripped = fenced.group(1)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", stripped, re.S)
        if not match:
            return None
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None


def iter_payload_numbers(obj: Any, key: str | None = None) -> list[float]:
    if obj is None or isinstance(obj, bool):
        return []
    if isinstance(obj, (int, float)):
        if key in _SKIP_NUMBER_KEYS:
            return []
        return [float(obj)]
    if isinstance(obj, str):
        if key in _SKIP_NUMBER_KEYS:
            return []
        return extract_normalized_numbers(obj)
    if isinstance(obj, dict):
        found: list[float] = []
        for inner_key, value in obj.items():
            found.extend(iter_payload_numbers(value, key=str(inner_key)))
        return found
    if isinstance(obj, list):
        found = []
        for item in obj:
            found.extend(iter_payload_numbers(item, key=key))
        return found
    return []


def iter_payload_tags(obj: Any) -> list[str]:
    tags: list[str] = []
    if isinstance(obj, dict):
        tag = obj.get("tag")
        if isinstance(tag, str) and tag:
            tags.append(tag)
        raw_tags = obj.get("tags")
        if isinstance(raw_tags, list):
            tags.extend(
                str(item) for item in raw_tags if isinstance(item, str) and item
            )
        for value in obj.values():
            tags.extend(iter_payload_tags(value))
    elif isinstance(obj, list):
        for item in obj:
            tags.extend(iter_payload_tags(item))
    return tags


def all_cited(payload: Any, excerpt: str) -> bool:
    excerpt_nums = extract_normalized_numbers(excerpt)
    excerpt_cf = excerpt.casefold()
    numbers = iter_payload_numbers(payload)
    tags = iter_payload_tags(payload)
    if not numbers and not tags:
        return False
    for number in numbers:
        if not any(numbers_close(number, hay) for hay in excerpt_nums):
            return False
    for tag in tags:
        if tag.casefold() not in excerpt_cf:
            return False
    return True


def citation_rate(payload: Any, excerpt: str) -> float:
    excerpt_nums = extract_normalized_numbers(excerpt)
    excerpt_cf = excerpt.casefold()
    numbers = iter_payload_numbers(payload)
    tags = iter_payload_tags(payload)
    total = len(numbers) + len(tags)
    if total == 0:
        return 0.0
    cited = 0
    for number in numbers:
        if any(numbers_close(number, hay) for hay in excerpt_nums):
            cited += 1
    for tag in tags:
        if tag.casefold() in excerpt_cf:
            cited += 1
    return cited / total


def checker_should_accept(candidate: Any, excerpt: str) -> bool:
    raw = candidate if isinstance(candidate, str) else json.dumps(candidate)
    if payload_has_forbidden(raw):
        return False
    parsed = _parse_json(candidate) if isinstance(candidate, str) else candidate
    if parsed is None:
        return False
    return all_cited(parsed, excerpt)


def score_searcher(response_text: str, excerpt: str) -> TextScore:
    score = TextScore()
    if payload_has_forbidden(response_text):
        score.has_verdict_keys = True
        score.errors = 1
        score.notes.append("forbidden")
        blob = _parse_json(response_text)
        score.valid_json = isinstance(blob, dict)
        return score
    blob = _parse_json(response_text)
    if not isinstance(blob, dict):
        score.errors = 1
        score.notes.append("invalid json")
        return score
    score.valid_json = True
    score.citation_rate = citation_rate(blob, excerpt)
    score.exact = all_cited(blob, excerpt)
    return score


def score_checker(response_text: str, candidate: Any, excerpt: str) -> TextScore:
    score = TextScore()
    score.expected_accepted = checker_should_accept(candidate, excerpt)
    if payload_has_forbidden(response_text):
        score.has_verdict_keys = True
        score.errors = 1
        score.notes.append("forbidden")
        return score
    blob = _parse_json(response_text)
    if not isinstance(blob, dict) or not isinstance(blob.get("accepted"), bool):
        score.errors = 1
        score.notes.append("invalid json")
        return score
    score.valid_json = True
    score.accepted = blob["accepted"]
    score.exact = score.accepted is score.expected_accepted
    score.citation_rate = 1.0 if score.exact else 0.0
    return score


def finalize_text_bakeoff(scores: list[TextScore]) -> TextBakeoffResult:
    # Partial HTTP / parse scores must not become policy defaults.
    ranked = sorted(
        [s for s in scores if s.errors == 0],
        key=lambda s: (s.citation_rate, int(s.exact)),
        reverse=True,
    )
    winner = ranked[0].model_id if ranked else ""
    fallback_pool = [s for s in ranked[1:] if not s.rate_limited]
    fallback = fallback_pool[0].model_id if fallback_pool else ""
    return TextBakeoffResult(scores=scores, winner=winner, fallback_429=fallback)


def score_canned_fixtures(payload: dict[str, Any]) -> TextBakeoffResult:
    excerpt = str(payload.get("excerpt") or "")
    role = str(payload.get("role") or "searcher")
    candidate = payload.get("candidate")
    scores: list[TextScore] = []
    for spec in payload.get("models") or []:
        if not isinstance(spec, dict):
            continue
        model_id = str(spec.get("id") or "")
        status = spec.get("status_code")
        if status is not None:
            code = int(status)
            score = TextScore(
                model_id=model_id,
                errors=1,
                rate_limited=code == 429,
            )
            score.notes.append(f"HTTP {code}")
            scores.append(score)
            continue
        response = str(spec.get("response") or "")
        if role == "checker":
            scored = score_checker(response, candidate, excerpt)
        else:
            scored = score_searcher(response, excerpt)
        scored.model_id = model_id
        scores.append(scored)
    return finalize_text_bakeoff(scores)


def _job_table(policy, job: str) -> dict[str, Any] | None:
    jobs = policy.section("nvidia").get("jobs")
    if not isinstance(jobs, dict):
        return None
    table = jobs.get(job)
    return table if isinstance(table, dict) else None


def _run(
    *,
    policy_path: Path,
    job: str,
    fixtures_path: Path | None,
    write_policy: bool,
) -> int:
    if not NVIDIA_API_KEY:
        print(
            "NVIDIA_API_KEY is missing. Set it in .env (see .env.example) "
            "and re-run. Policy winner fields were not changed.",
            file=sys.stderr,
        )
        return 2
    if fixtures_path is None or not fixtures_path.is_file():
        print(
            "No job fixtures found (need a JSON file of canned model outputs). "
            "Policy winner fields were not changed.",
            file=sys.stderr,
        )
        return 2
    try:
        payload = json.loads(fixtures_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Job fixtures unreadable: {exc}", file=sys.stderr)
        return 2
    if not isinstance(payload, dict):
        print("Job fixtures must be a JSON object.", file=sys.stderr)
        return 2

    policy = load_policy(policy_path)
    table = _job_table(policy, job)
    if table is None or "winner" not in table or "fallback_429" not in table:
        print(
            f"Missing [nvidia.jobs.{job}] winner/fallback_429 in {policy_path}. "
            "Policy winner fields were not changed.",
            file=sys.stderr,
        )
        return 2

    result = score_canned_fixtures(payload)
    print(f"winner: {result.winner or '(none)'}")
    print(f"fallback_429: {result.fallback_429 or '(none)'}")
    if not result.winner:
        print(
            "Bake-off produced no successful model scores; not writing winner to policy.",
            file=sys.stderr,
        )
        return 1
    if write_policy:
        write_job_winner(policy_path, job, result.winner, result.fallback_429)
        print(
            f"Wrote winner={result.winner!r} fallback_429={result.fallback_429!r} "
            f"to [nvidia.jobs.{job}] in {policy_path}"
        )
    else:
        print("Skipping policy write (--no-write-policy).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="NVIDIA NIM text-job scorer (canned fixtures; no live NVIDIA)."
    )
    parser.add_argument("--job", required=True, help="nvidia.jobs.<job> table name")
    parser.add_argument("--policy", type=Path, default=POLICY_PATH)
    parser.add_argument("--fixtures", type=Path, default=None)
    parser.add_argument(
        "--no-write-policy",
        action="store_true",
        help="Print scores but do not patch winner/fallback_429",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return _run(
        policy_path=args.policy,
        job=args.job,
        fixtures_path=args.fixtures,
        write_policy=not args.no_write_policy,
    )


if __name__ == "__main__":
    raise SystemExit(main())
