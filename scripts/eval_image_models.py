"""Bake-off NVIDIA NIM OCR/VLMs on real screenshot fixtures.

Usage (from repo root):

    python scripts/eval_image_models.py

Requires NVIDIA_API_KEY in .env (loaded via src/config.py) and screenshot
files plus a manifest JSON:

    tests/fixtures/screenshots/manifest.json
    {
      "brokerage.png": ["AAPL", "MSFT"]
    }

Does not write a winner into policy unless at least one model scored on
real fixtures. Missing key or missing screenshots exits 2 (no fake winner).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import httpx  # noqa: E402

from config import LOG_LEVEL, NVIDIA_API_KEY, POLICY_PATH  # noqa: E402
from nvidia_nim import (  # noqa: E402
    SCREENSHOT_JOB,
    NvidiaNimClient,
    format_bakeoff_table,
    run_bakeoff,
    write_job_winner,
)
from policy import load_policy  # noqa: E402


def _load_fixtures(
    screenshot_dir: Path, manifest_name: str
) -> list[tuple[Path, list[str]]]:
    manifest_path = screenshot_dir / manifest_name
    if not screenshot_dir.is_dir():
        return []
    if not manifest_path.is_file():
        return []
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{manifest_path} must be a JSON object of filename → tickers")
    fixtures: list[tuple[Path, list[str]]] = []
    for name, tickers in payload.items():
        path = screenshot_dir / str(name)
        if not path.is_file():
            raise FileNotFoundError(
                f"Screenshot listed in manifest but missing: {path}"
            )
        fixtures.append((path, [str(t).upper() for t in tickers]))
    return fixtures


async def _run(
    *,
    policy_path: Path,
    screenshot_dir: Path | None,
    write_policy: bool,
) -> int:
    if not NVIDIA_API_KEY:
        print(
            "NVIDIA_API_KEY is missing. Set it in .env (see .env.example) "
            "and re-run. Policy winner fields were not changed.",
            file=sys.stderr,
        )
        return 2

    policy = load_policy(policy_path)
    nvidia = policy.section("nvidia")
    directory = screenshot_dir or ROOT / str(
        nvidia.get("screenshot_dir") or "tests/fixtures/screenshots"
    )
    manifest_name = str(nvidia.get("screenshot_manifest") or "manifest.json")
    try:
        fixtures = _load_fixtures(directory, manifest_name)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Screenshot fixtures unreadable: {exc}", file=sys.stderr)
        return 2
    if not fixtures:
        print(
            f"No screenshot fixtures found in {directory} (need {manifest_name} "
            "mapping filenames to expected tickers). Policy winner fields were not changed.",
            file=sys.stderr,
        )
        return 2

    models = list(nvidia.get("bakeoff_models") or [])
    if not models:
        print("Policy nvidia.bakeoff_models is empty.", file=sys.stderr)
        return 2

    nim_timeout = float(nvidia.get("timeout_seconds") or 180)
    async with httpx.AsyncClient(timeout=nim_timeout, follow_redirects=True) as http:
        client = NvidiaNimClient(NVIDIA_API_KEY, policy, http)
        result = await run_bakeoff(client, models, fixtures)

    print(format_bakeoff_table(result))
    if not result.winner:
        print(
            "Bake-off produced no successful model scores; not writing winner to policy.",
            file=sys.stderr,
        )
        return 1
    if write_policy:
        write_job_winner(
            policy_path, SCREENSHOT_JOB, result.winner, result.fallback_429
        )
        print(
            f"Wrote winner={result.winner!r} fallback_429={result.fallback_429!r} to {policy_path}"
        )
    else:
        print("Skipping policy write (--no-write-policy).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="NVIDIA NIM image bake-off (eval only)."
    )
    parser.add_argument("--policy", type=Path, default=POLICY_PATH)
    parser.add_argument("--screenshots", type=Path, default=None)
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
    return asyncio.run(
        _run(
            policy_path=args.policy,
            screenshot_dir=args.screenshots,
            write_policy=not args.no_write_policy,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
