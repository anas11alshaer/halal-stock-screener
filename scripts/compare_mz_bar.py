"""Compare VerdictEngine vs Musaffa/Zoya resolver bar (eval calibration).

Usage (from repo root):

    python scripts/compare_mz_bar.py
    python scripts/compare_mz_bar.py --tickers AAPL JPM SPY

Live scrapers + live engine. Sequential. Not Telegram.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import tomllib  # noqa: E402

from config import EVAL_FIXTURES_PATH, LOG_LEVEL, POLICY_PATH  # noqa: E402
from fusion import HALAL, NOT_HALAL  # noqa: E402
from policy import load_policy  # noqa: E402
from resolver import resolve_compliance  # noqa: E402
from scrapers import ComplianceStatus, MusaffaScraper, ZoyaScraper  # noqa: E402
from verdict_engine import ScreenResult, VerdictEngine  # noqa: E402

# Stop extra-holding scrapes after this many consecutive source ERRORs.
_EXTRA_CONSECUTIVE_ERROR_STOP = 5

# Activity denylist classes used only to flag SEO-wrong M/Z HALAL suspects.
_SEO_HINTS = (
    "financial",
    "bank",
    "tobacco",
    "alcohol",
    "weapon",
    "aerospace",
    "defense",
    "gambling",
    "casino",
    "brew",
    "winery",
    "distill",
    "credit",
    "insurance",
    "capital markets",
    "asset management",
    "mortgage",
)


def _load_fixture_tickers(path: Path) -> list[str]:
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    stocks = list(data.get("stocks") or [])
    etfs = list(data.get("etfs") or [])
    return [str(t).upper() for t in stocks + etfs]


def _status_label(status: ComplianceStatus | None) -> str:
    if status is None:
        return "ERROR"
    return status.value


def _vote_cell(result: ScreenResult, plugin_names: list[str]) -> str:
    parts: list[str] = []
    for name in plugin_names:
        vote = result.votes.get(name)
        if vote is None:
            parts.append(f"{name}=—")
            continue
        extra = ""
        metrics = vote.metrics or {}
        if name == "NportHoldings":
            bits: list[str] = []
            coverage = metrics.get("coverage")
            if isinstance(coverage, (int, float)):
                bits.append(f"coverage={coverage:.3f}")
            failed = list(metrics.get("activity_failed_holdings") or [])
            failed.extend(metrics.get("nested_fund_failed") or [])
            if failed:
                bits.append("failed=" + ",".join(str(x) for x in failed))
            extra = f" [{'; '.join(bits)}]" if bits else ""
        elif name == "Activity":
            sector = metrics.get("sector")
            industry = metrics.get("industry")
            extra = f" [sector={sector}; industry={industry}]"
        elif name == "Ratios":
            bits = []
            for key, label in (
                ("debt_pct", "debt%"),
                ("cash_pct", "cash%"),
                ("interest_income_pct", "int%"),
            ):
                value = metrics.get(key)
                if isinstance(value, (int, float)):
                    bits.append(f"{label}={value:.2f}")
            extra = f" [{'; '.join(bits)}]" if bits else ""
        elif name == "HalalWallet":
            ds = metrics.get("dataset_verdict")
            extra = f" [dataset={ds}]" if ds is not None else ""
        parts.append(f"{name}={vote.vote.value}:{vote.reason}{extra}")
    return " | ".join(parts)


def _agree(
    engine: str,
    bar: str,
    musaffa: str,
    zoya: str,
) -> str:
    if musaffa == "ERROR" or zoya == "ERROR" or bar == "ERROR":
        return "n/a"
    if engine == bar:
        return "agree"
    return "disagree"


def _seo_suspect(row: dict[str, Any]) -> bool:
    votes = str(row.get("engine_votes") or "").lower()
    if "activity=fail" not in votes:
        return False
    if row["musaffa"] != HALAL and row["zoya"] != HALAL:
        return False
    blob = votes
    return any(hint in blob for hint in _SEO_HINTS)


def _cached_engine(engine: VerdictEngine, ticker: str) -> ScreenResult | None:
    ticker = ticker.upper()
    for depth in (0, 1):
        hit = engine._cache.get((ticker, depth))
        if hit is not None:
            return hit
    return None


def _failed_holdings(result: ScreenResult) -> list[str]:
    vote = result.votes.get("NportHoldings")
    if vote is None or not vote.metrics:
        return []
    names = list(vote.metrics.get("activity_failed_holdings") or [])
    names.extend(vote.metrics.get("nested_fund_failed") or [])
    return [str(t).upper() for t in names]


def _pad(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    widths = [len(c) for c in rows[0]]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt(row: list[str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row))

    lines = [fmt(rows[0]), "  ".join("-" * w for w in widths)]
    lines.extend(fmt(row) for row in rows[1:])
    return "\n".join(lines)


def _row_line(row: dict[str, Any]) -> list[str]:
    return [
        row["ticker"],
        row["engine_verdict"],
        row["engine_votes"],
        row["musaffa"],
        row["zoya"],
        row["bar"],
        row["agree"],
    ]


async def _scrape_pair(
    musaffa: MusaffaScraper,
    zoya: ZoyaScraper,
    ticker: str,
) -> tuple[Any, Any, Any, bool]:
    m_res = await musaffa.screen_ticker(ticker)
    z_res = await zoya.screen_ticker(ticker)
    final, conflict = resolve_compliance(m_res, z_res)
    return m_res, z_res, final, conflict


def _build_row(
    *,
    ticker: str,
    kind: str,
    source_etfs: list[str],
    engine_result: ScreenResult,
    plugin_names: list[str],
    m_res: Any,
    z_res: Any,
    final: Any,
    conflict: bool,
) -> dict[str, Any]:
    musaffa = _status_label(m_res.status)
    zoya = _status_label(z_res.status)
    bar = _status_label(final.status)
    engine_verdict = engine_result.verdict
    return {
        "ticker": ticker,
        "kind": kind,
        "source_etfs": source_etfs,
        "quote_type": engine_result.quote_type,
        "engine_verdict": engine_verdict,
        "engine_votes": _vote_cell(engine_result, plugin_names),
        "musaffa": musaffa,
        "zoya": zoya,
        "bar": bar,
        "conflict": bool(conflict),
        "agree": _agree(engine_verdict, bar, musaffa, zoya),
        "musaffa_error": m_res.error_message,
        "zoya_error": z_res.error_message,
        "bar_details": final.details,
    }


def _dump(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _print_groups(rows: list[dict[str, Any]]) -> None:
    comparable = [r for r in rows if r["agree"] != "n/a"]
    errors = [r for r in rows if r["agree"] == "n/a"]

    false_halal = [
        r
        for r in comparable
        if r["engine_verdict"] == HALAL and r["bar"] in (NOT_HALAL, "DOUBTFUL")
    ]
    false_not_halal = [
        r for r in comparable if r["engine_verdict"] == NOT_HALAL and r["bar"] == HALAL
    ]
    mz_conflict = [r for r in comparable if r["conflict"]]
    seo = [r for r in comparable if _seo_suspect(r)]

    def names(group: list[dict[str, Any]]) -> str:
        if not group:
            return "(none)"
        return ", ".join(
            f"{r['ticker']} (engine={r['engine_verdict']}, M={r['musaffa']}, "
            f"Z={r['zoya']}, bar={r['bar']})"
            for r in group
        )

    print()
    print("GROUP: engine HALAL, bar NOT_HALAL/DOUBTFUL (false HALAL)")
    print(names(false_halal))
    print()
    print("GROUP: engine NOT_HALAL, bar HALAL (false NOT_HALAL)")
    print(names(false_not_halal))
    print()
    print("GROUP: M vs Z conflict (bar = restrictive)")
    print(names(mz_conflict))
    print()
    print(
        "GROUP: suspected SEO false HALAL on M/Z "
        "(bank/tobacco/alcohol/weapons/gambling)"
    )
    print(names(seo))
    print()
    print("SCRAPE ERROR / n/a rows (cannot agree or disagree)")
    if not errors:
        print("(none)")
    else:
        for r in errors:
            print(
                f"{r['ticker']}: M={r['musaffa']} Z={r['zoya']} bar={r['bar']} "
                f"engine={r['engine_verdict']} "
                f"m_err={r['musaffa_error']!r} z_err={r['zoya_error']!r}"
            )


def _load_resume(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


async def _run(
    tickers: list[str],
    policy_path: Path,
    json_out: Path | None,
    include_extras: bool,
    resume_from: Path | None,
) -> int:
    policy = load_policy(policy_path)
    plugin_names = list(policy.enabled_plugins)
    engine = VerdictEngine(policy)
    musaffa = MusaffaScraper()
    zoya = ZoyaScraper()
    fixture_set = {t.upper() for t in tickers}
    extra_from: dict[str, list[str]] = {}
    rows: list[dict[str, Any]] = []
    skipped_extras: list[str] = []
    prior = _load_resume(resume_from)
    if prior:
        rows.extend(prior.get("rows") or [])
        skipped_extras.extend(prior.get("skipped_extras") or [])
        for holding, etfs in (prior.get("extra_from") or {}).items():
            extra_from.setdefault(str(holding).upper(), [])
            for etf in etfs:
                if etf not in extra_from[str(holding).upper()]:
                    extra_from[str(holding).upper()].append(etf)
    done_fixtures = {r["ticker"] for r in rows if r.get("kind") == "fixture"}
    remaining = [t for t in tickers if t not in done_fixtures]
    payload: dict[str, Any] = {
        "fixtures": tickers,
        "rows": rows,
        "skipped_extras": skipped_extras,
        "extra_from": extra_from,
    }

    try:
        for ticker in remaining:
            print(f"ENGINE {ticker} ...", flush=True)
            result = await engine.screen(ticker)
            for holding in _failed_holdings(result):
                extra_from.setdefault(holding, [])
                if ticker not in extra_from[holding]:
                    extra_from[holding].append(ticker)
            print(
                f"SCRAPE {ticker} engine={result.verdict} ...",
                flush=True,
            )
            m_res, z_res, final, conflict = await _scrape_pair(musaffa, zoya, ticker)
            row = _build_row(
                ticker=ticker,
                kind="fixture",
                source_etfs=[],
                engine_result=result,
                plugin_names=plugin_names,
                m_res=m_res,
                z_res=z_res,
                final=final,
                conflict=conflict,
            )
            rows.append(row)
            print(
                f"ROW {ticker} engine={row['engine_verdict']} "
                f"M={row['musaffa']} Z={row['zoya']} bar={row['bar']} "
                f"agree={row['agree']}",
                flush=True,
            )
            _dump(json_out, payload)

        extras: list[str] = []
        already = {r["ticker"] for r in rows}
        if include_extras:
            extras = [
                t for t in extra_from if t not in fixture_set and t not in already
            ]
            extras.sort()
        consecutive_errors = 0
        for ticker in extras:
            if consecutive_errors >= _EXTRA_CONSECUTIVE_ERROR_STOP:
                skipped_extras.append(ticker)
                continue
            cached = _cached_engine(engine, ticker)
            if cached is None:
                print(f"ENGINE extra {ticker} ...", flush=True)
                cached = await engine.screen(ticker)
            print(f"SCRAPE extra {ticker} engine={cached.verdict} ...", flush=True)
            m_res, z_res, final, conflict = await _scrape_pair(musaffa, zoya, ticker)
            row = _build_row(
                ticker=ticker,
                kind="failed_holding",
                source_etfs=list(extra_from.get(ticker) or []),
                engine_result=cached,
                plugin_names=plugin_names,
                m_res=m_res,
                z_res=z_res,
                final=final,
                conflict=conflict,
            )
            rows.append(row)
            if row["musaffa"] == "ERROR" or row["zoya"] == "ERROR":
                consecutive_errors += 1
            else:
                consecutive_errors = 0
            print(
                f"ROW extra {ticker} from={row['source_etfs']} "
                f"engine={row['engine_verdict']} M={row['musaffa']} "
                f"Z={row['zoya']} bar={row['bar']} agree={row['agree']}",
                flush=True,
            )
            _dump(json_out, payload)
        if consecutive_errors >= _EXTRA_CONSECUTIVE_ERROR_STOP:
            print(
                f"Stopped extra scrapes after {_EXTRA_CONSECUTIVE_ERROR_STOP} "
                "consecutive ERRORs.",
                flush=True,
            )
    finally:
        await engine.aclose()
        _dump(json_out, payload)

    headers = [
        "ticker",
        "engine verdict",
        "engine plugin votes+reasons",
        "Musaffa",
        "Zoya",
        "resolver bar",
        "agree?",
    ]
    fixture_rows = [r for r in rows if r["kind"] == "fixture"]
    extra_rows = [r for r in rows if r["kind"] != "fixture"]

    print()
    print("FIXTURE COMPARISON")
    print(_pad([headers] + [_row_line(r) for r in fixture_rows]))
    if extra_rows:
        print()
        print("EXTRA FAILED HOLDINGS (from N-PORT votes; cheap cap = first 20/ETF)")
        print(_pad([headers] + [_row_line(r) for r in extra_rows]))
        print()
        print("Extra holding sources:")
        for r in extra_rows:
            print(f"  {r['ticker']}: {', '.join(r['source_etfs'])}")
    print()
    print(f"Covered extras: {[r['ticker'] for r in extra_rows]}")
    print(f"Skipped extras: {skipped_extras}")
    _print_groups(rows)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare eval engine vs Musaffa/Zoya bar (not Telegram)."
    )
    parser.add_argument("--policy", type=Path, default=POLICY_PATH)
    parser.add_argument("--fixtures", type=Path, default=EVAL_FIXTURES_PATH)
    parser.add_argument("--tickers", nargs="*", default=None)
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Write incremental JSON (also the final table).",
    )
    parser.add_argument(
        "--skip-extras",
        action="store_true",
        help="Do not scrape N-PORT failed_holdings beyond fixtures.",
    )
    parser.add_argument(
        "--resume-from",
        type=Path,
        default=None,
        help="Keep rows from a prior JSON run and continue remaining tickers.",
    )
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
    return asyncio.run(
        _run(
            tickers,
            args.policy,
            args.json_out,
            include_extras=not args.skip_extras,
            resume_from=args.resume_from,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
