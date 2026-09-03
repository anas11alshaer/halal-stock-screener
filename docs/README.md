# Docs

Light vault for halal-stock-screener. ADR drawer is the only long-lived docs artifact; other notes stay in GitHub Issues.

## Map

- `adr/` — Architecture Decision Records (expensive-to-reverse choices)
  - `0001-musaffa-scrape-via-ssr.md`
  - `0002-dual-source-conservative-resolver.md`
  - `0003-playwright-to-httpx.md`
  - `0004-basescraper-abstraction.md`
  - `0005-gemini-model-rotation.md`
  - `0006-dockerfile-healthcheck.md`
  - `0007-github-flow-on-main.md`
  - `0008-policy-driven-screener.md`
  - `0009-nport-etf-lookthrough.md`
  - `0010-nvidia-image-parser.md`
  - `0012-secrets-env-app-toml.md`
- `planning/` — Deferred large-change plans (not yet built)
  - `2026-08-21-vercel-modular-future.md` — Vercel + plugin architecture, src/tests pack shape
- `CHANGELOG.md` at repo root (not here) — user-facing history

## How to add an ADR

Copy `D:\projects\guide\templates\adr.md` to `docs/adr/NNNN-title.md`, number from `0011`, fill `Date`, `Status`, `Context`, `Options`, `Decision`, `Consequences`. ADR is not Rust-only — use for protocol, data-model, or infra choices that are costly to undo. Small decisions go in the Issue.

## Work method

House playbook: `D:\projects\guide`. This repo's agent file is `AGENTS.md` at root.
