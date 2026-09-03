"""Warm CUSIP→ticker cache via OpenFIGI. Not on the screen() path.

Usage (from repo root):

    python scripts/cache_openfigi.py
    python scripts/cache_openfigi.py --tickers SPUS HLAL SPY QQQ
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

from config import (  # noqa: E402
    DATA_DIR,
    EVAL_FIXTURES_PATH,
    LOG_LEVEL,
    POLICY_PATH,
    REQUEST_TIMEOUT,
)
from policy import load_policy  # noqa: E402
from plugins.nport import SecNportClient  # noqa: E402

import httpx  # noqa: E402


def _load_etf_tickers(path: Path) -> list[str]:
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return [str(t).upper() for t in (data.get("etfs") or [])]


async def _run(tickers: list[str], policy_path: Path) -> int:
    policy = load_policy(policy_path)
    async with httpx.AsyncClient(
        timeout=max(REQUEST_TIMEOUT, 60), follow_redirects=True
    ) as http:
        client = SecNportClient(policy, http)
        for ticker in tickers:
            report = await client.holdings(ticker)
            if report is None or not report.holdings:
                print(f"{ticker}: no N-PORT report")
                continue
            needed = [h.cusip for h in report.holdings if h.cusip and not h.ticker]
            mapped = await client.warm_cusip_cache(needed)
            print(
                f"{ticker}: {len(report.holdings)} holdings, "
                f"{len(needed)} unmapped, {len(mapped)} newly mapped"
            )
        print(f"cache: {DATA_DIR / 'openfigi_cusip_tickers.json'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Warm OpenFIGI CUSIP cache (not Telegram, not screen())."
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
        tickers = _load_etf_tickers(args.fixtures)
    if not tickers:
        print("No ETF tickers to warm.", file=sys.stderr)
        return 2
    return asyncio.run(_run(tickers, args.policy))


if __name__ == "__main__":
    raise SystemExit(main())
