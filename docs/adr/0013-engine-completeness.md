# ADR 0013: Engine completeness — HW overlay, weighted N-PORT, per-job NIM

Date: 2026-08-22
Status: accepted (PR 1 lands HW overlay + named 33% standard + cash/AR gates; weighted N-PORT and per-job NIM land in later PRs)

## Context

The eval engine (ADR 0008 conjunction fusion, ADR 0009 N-PORT look-through, ADR 0010 screenshot NIM bake-off) is fail-closed and policy-driven, but it is not yet a Shariah screener. Live comparison against Musaffa/Zoya showed false NOT_HALAL from HalalWallet `conditional`/unknown (CSX, ECL, ITW, KO, AMZN, WMT), AAOIFI 30% spot debt (CDW, TSCO), a cash screen that always runs, and ETF FAIL on Yahoo holes because any identified child that is not HALAL fails the fund. ADR 0008–0010 stay on disk; this ADR supersedes only the clauses below.

## Options

1. Keep AAOIFI 30%, HalalWallet required when the ticker is in the CC-BY dataset, any-child-not-HALAL for ETFs, and a screenshot-only NIM catalog — status quo; disagrees with post-2023 S&P Shariah and the Issue #7 disagreement set.
2. Rewrite ADR 0008 / 0009 / 0010 in place — rejected (conjunction fusion, N-PORT as source, and bake-off pattern still stand).
3. Add ADR 0013 that supersedes only HW-as-required, any-child-not-HALAL, and screenshot-only NIM; name `sp_shariah` at 33% with cash/AR gates — chosen.

## Decision

We pick **option 3**. Named standard in `config/screening_policy.toml` is `standard.name = "sp_shariah"` (label only). Plugins read `[ratios]` numbers, not `standard.name`. Debt FAIL if `debt_pct >= 33.0` of market cap (spot until a later PR lands 36-month trailing average). Cash and receivables screens are gated (`cash_screen_enabled` / `receivables_screen_enabled`); committed TOML sets both false. A missing `cash_screen_enabled` key keeps today's cash screen on. No 2% hysteresis. No ticker allowlists.

**Supersedes (clauses only):**

- ADR 0008 “HalalWallet when the ticker is in that CC-BY dataset” as **required**. Conjunction fusion **stands**. `fusion.add_required_when_in_dataset` hook **stands**; committed list is `[]`. Explicit HW `not_halal` still vetoes as an optional FAIL. `conditional` / unknown / fetch error → ABSTAIN and not required.
- ADR 0009 “any identified holding that is not HALAL → FAIL”. N-PORT **source**, series-id, STIV skip, OpenFIGI-off-hot-path, and coverage floor **stand**. Scoring becomes weighted on child `ScreenResult.votes` (later PR; decided here).
- ADR 0010 screenshot-only catalog. Bake-off **pattern** (winner + 429 fallback, mock NIM in tests) **stands**; tables become per-job **and two roles** (searcher + checker). Searcher/checker **implementation** is a later session; this ADR records the policy supersession.

0011 remains reserved for Oracle scripts-no-docker. Do not edit 0008 / 0009 / 0010 in place.

PR 1 of this decision: HW fail-only overlay, named 33% standard, cash/AR gates in `ratios.py`. Telegram (`src/bot.py`) stays on Musaffa/Zoya. Musaffa/Zoya are not VerdictEngine plugins.

## Consequences

- Good: CSX-class HW maybes no longer force NOT_HALAL; debt 32.9 PASS / 33.0 FAIL; cash 40% PASS while the screen is off; operators can restore HW-required via TOML without a code change.
- Cost / follow-up: trailing 36-month cap, companyfacts-on-any-hole, Yahoo denylist growth, DataFinder + searcher/checker, weighted N-PORT, and job-scoped NIM writer are later PRs. Telegram is not wired.
- Hardware / recovery: N/A.
