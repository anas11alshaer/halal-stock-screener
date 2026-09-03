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

We pick **option 3**. `NportHoldingsPlugin` maps ticker → CIK/series via SEC `company_tickers_mf.json` (series id required; issuer-CIK fallback is not used), downloads the latest matching NPORT-P `primary_doc.xml`, and scores coverage against `etf.coverage_floor` in policy. Coverage is identified weight / non-skip weight. Skip-category rows (cash/T-bills) do not pad the floor and cannot exceed `1 - coverage_floor`. A filing whose `seriesId` is missing or different is skipped. Unmapped CUSIPs stay uncovered; CUSIP→ticker is a local cache (`data/openfigi_cusip_tickers.json`). OpenFIGI runs only in `scripts/cache_openfigi.py`, not on `holdings()`. Coverage below floor or any identified holding that is not HALAL → FAIL. Activity/Ratios abstain on the ETF wrapper so the fund’s “Financial Services” sector cannot short-circuit look-through.

## Consequences

- Good: fail-closed on missing filings, missing series id, cold CUSIP cache, and oversized cash sleeves; same AAOIFI plugins on constituents; no product allowlist.
- Cost / follow-up: live eval of broad ETFs is many yfinance calls; N-PORT is quarterly-public and can lag; first eval needs `scripts/cache_openfigi.py`; Telegram not wired yet.
- Hardware / recovery: N/A.
