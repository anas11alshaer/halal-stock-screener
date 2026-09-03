# ADR 0008: Policy-driven fail-closed plugin fusion

Date: 2026-08-22
Status: accepted

## Context

Musaffa/Zoya HTML scrapers can emit HALAL from SEO meta and JSON-LD FAQ templates (`src/scrapers/musaffa.py`, `src/scrapers/zoya.py`, `src/resolver.py`). Issue #7 requires a free-source engine whose thresholds, denylists, enabled plugins, fusion name, and ETF coverage floor are not hardcoded in plugin modules. Eval-first: do not wire Telegram until the fixture table is signed off.

## Options

1. Keep dual-source HTML + conservative resolver — status quo, still guesses HALAL from marketing copy.
2. LLM-as-judge over scraped pages — rejected (Issue: no LLM Shariah judge; still trusts HTML).
3. Policy file + plugin votes (PASS/FAIL/ABSTAIN) fused by named conjunction — chosen.

## Decision

We pick **option 3**. Committed `config/screening_policy.toml` loaded by `src/policy.py`. Eval engine `src/verdict_engine.py` + `src/fusion.py` (`conjunction`): any FAIL → NOT HALAL; required plugins all PASS and none ABSTAIN → HALAL; else NOT HALAL. Default required: Activity + Ratios; HalalWallet when the ticker is in that CC-BY dataset; ETFs require N-PORT look-through. Production Telegram still uses Musaffa/Zoya until Issue #7 slice 6.

## Consequences

- Good: thresholds live in policy; HALAL on the eval path cannot come from SEO/JSON-LD; plugins are optional via `enabled_plugins`.
- Cost / follow-up: Telegram still shows the old resolver until human sign-off; HtmlChip plugin is a later slice.
- Hardware / recovery: N/A.
