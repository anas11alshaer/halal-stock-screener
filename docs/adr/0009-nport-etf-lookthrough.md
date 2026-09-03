# ADR 0009: ETF screening via SEC N-PORT look-through

Date: 2026-08-22
Status: accepted

## Context

Issue #7: US ETFs must be screened by holdings, not a Musaffa chip and not a hardcoded ticker list (including SPUS/HLAL). Incomplete holdings must be NOT HALAL. Free sources only (no paid N-PORT APIs).

## Options

1. Trust ETF issuer “halal” branding / Musaffa ETF chip — rejected (HTML guessing; no holdings).
2. Hardcoded allowlist of Islamic ETFs — rejected (Issue forbids ticker allowlists in product/plugin code).
3. SEC EDGAR Form NPORT-P XML, then run the same stock plugin pipeline on named holdings — chosen.

## Decision

We pick **option 3**. `NportHoldingsPlugin` maps ticker → CIK/series via SEC `company_tickers_mf.json`, downloads the latest matching NPORT-P `primary_doc.xml`, and scores coverage against `etf.coverage_floor` in policy. Holdings without tickers (except policy `skip_asset_categories`) count as uncovered. Coverage below floor or any holding that is not HALAL → FAIL. Activity/Ratios abstain on the ETF wrapper so the fund’s “Financial Services” sector cannot short-circuit look-through.

## Consequences

- Good: fail-closed on missing filings; same AAOIFI plugins on constituents; no product allowlist.
- Cost / follow-up: live eval of broad ETFs is many yfinance calls; N-PORT is quarterly-public and can lag; Telegram not wired yet.
- Hardware / recovery: N/A.
