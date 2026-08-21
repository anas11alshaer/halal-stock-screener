# ADR 0007: Branching — develop/release → GitHub Flow on main

Date: 2026-08-21
Status: accepted

## Context

Prior `development/D0.02.00` + `release/R0.02.00` with release-only tests (`3f664c2`) slowed fixes (parser hotfix needed patch while `main` moved). Need fast hotfixes, simpler mental model, protected releasable main. `AGENTS.md:19` before `d830bbb`, remote default was `master` then `main`.

## Options

1. GitFlow with long-lived `develop` + `release/Rx.y` — old, complex.
2. GitHub Flow: `main` protected releasable, short-lived `feature/fix/hotfix` + PR + delete — chosen.
3. Trunk-based with `release/x.y` only for old-line maintenance — documented as exception.

## Decision

We pick **option 2**. Default `main` protected CI-green, no direct commits; prefixes `feature/<issue>-<slug>`, `fix/<issue>-<slug>`, `hotfix/<version>-<slug>` (e.g., PR #1 `fix/scraper-musaffa-zoya`), `vX.Y.Z` tags SemVer on shipped merge commit, no `develop`. Tests run locally on any branch `pytest` + CI on PR (`AGENTS.md:21`). `d830bbb` migrated docs, `f4a5fc3` tagged `v0.2.1`, `da0cf2e` current HEAD.

## Consequences

- Good: fast hotfixes, simpler model, tag `v0.2.1` at merge commit `f4a5fc3`; deleted `development`/`release` branches.
- Cost / follow-up: need branch protection + CI enforcement; old `develop/release` history remains but abandoned.
- Hardware / recovery: N/A.
