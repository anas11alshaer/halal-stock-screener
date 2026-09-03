"""Eval harness: policy-driven plugin votes + binary verdict for fixture tickers.

Usage (from repo root, with src on PYTHONPATH via this script):

    python scripts/eval_screener.py
    python scripts/eval_screener.py --tickers AAPL JPM SPY

Calls live yfinance / HalalWallet / SEC when run; unit tests mock those.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import tomllib  # noqa: E402

from config import EVAL_FIXTURES_PATH, LOG_LEVEL, POLICY_PATH  # noqa: E402
from policy import load_policy  # noqa: E402
from verdict_engine import VerdictEngine, format_screen_table  # noqa: E402


def _load_fixture_tickers(path: Path) -> list[str]:
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    stocks = list(data.get("stocks") or [])
    etfs = list(data.get("etfs") or [])
    return [str(t).upper() for t in stocks + etfs]


async def _run(tickers: list[str], policy_path: Path) -> int:
    policy = load_policy(policy_path)
    engine = VerdictEngine(policy)
    try:
        results = [await engine.screen(ticker) for ticker in tickers]
    finally:
        await engine.aclose()
    print(format_screen_table(results, policy.enabled_plugins))
    print()
    print(
        "HALAL only if required plugins PASS and none of them ABSTAIN; any FAIL → NOT HALAL."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Eval fixture screener (not Telegram)."
    )
    parser.add_argument("--policy", type=Path, default=POLICY_PATH)
    parser.add_argument("--fixtures", type=Path, default=EVAL_FIXTURES_PATH)
    parser.add_argument("--tickers", nargs="*", default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.tickers:
        tickers = [t.upper() for t in args.tickers]
    else:
        tickers = _load_fixture_tickers(args.fixtures)
    if not tickers:
        print("No tickers in fixture list.", file=sys.stderr)
        return 2
    return asyncio.run(_run(tickers, args.policy))


if __name__ == "__main__":
    raise SystemExit(main())
