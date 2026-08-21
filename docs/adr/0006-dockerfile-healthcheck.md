# ADR 0006: Deployment — nixpacks → Dockerfile + health-check server

Date: 2026-01-24
Status: accepted

## Context

Railway nixpacks auto-detect failed repeatedly (`b2e0a98`, `b1d7cc`, `b1cd44c`) and app is polling bot (no HTTP) → Railway health checks failed → restarts. `src/bot.py:38-55`.

## Options

1. Nixpacks + `nixpacks.toml`/`railway.toml`/`Procfile` (`worker: python src/bot.py`) — tried `74772bdc`, abandoned.
2. Dockerfile with manual `playwright install --with-deps` — intermediate.
3. Dockerfile + threading `HTTPServer` on `$PORT` returning `OK` — chosen.

## Decision

We pick **option 3**. Shipped `Dockerfile:1` (lean `python:3.11-slim-bookworm` after `23e6669`), start daemon thread `start_health_server()` (`src/bot.py:50`) in `main()` (`src/bot.py:218`) before `run_polling`. `railway.toml`/`nixpacks.toml`/`Procfile` later removed (`50bbbef`).

## Consequences

- Good: deterministic builds, Railway stays green.
- Cost / follow-up: thread + unauthenticated `0.0.0.0:$PORT` endpoint (only `OK`), `PORT` read via `os.environ` outside `config.py` (violates project rule).
- Hardware / recovery: N/A — hosted service.
