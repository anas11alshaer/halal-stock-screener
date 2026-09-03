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
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from config import LOG_LEVEL, NVIDIA_API_KEY, POLICY_PATH  # noqa: E402
from nvidia_nim import (  # noqa: E402
    all_cited,
    checker_should_accept,
    citation_rate,
    extract_normalized_numbers,
    numbers_close,
    payload_has_forbidden,
    write_job_winner,
)
from policy import load_policy  # noqa: E402

__all__ = [
    "TextBakeoffResult",
    "TextScore",
    "all_cited",
    "checker_should_accept",
    "citation_rate",
    "extract_normalized_numbers",
    "finalize_text_bakeoff",
    "main",
    "numbers_close",
    "payload_has_forbidden",
    "score_canned_fixtures",
    "score_checker",
    "score_searcher",
]


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
