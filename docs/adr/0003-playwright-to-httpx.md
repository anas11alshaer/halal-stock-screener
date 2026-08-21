# ADR 0003: Playwright → httpx async HTTP

Date: 2026-02-26
Status: accepted (supersedes ADR 0001 browser choice)

## Context

Playwright required 7+ Dockerfile attempts (chromium, `nixpacks.toml`, `railway.toml`) and OOM (`bbacad6 Lower number of simultaneous browser pages`). Discovery: both sources render verdicts server-side via SSR meta/chips and JSON-LD, no JS needed. Need to eliminate Chromium from Railway deployment. `src/scrapers/musaffa.py:27`, `src/scrapers/base.py:17`.

## Options

1. Stay on Playwright (official image `mcr.microsoft.com/playwright`) — tried `bd5054f`, `f70f934`.
2. Raw `httpx.AsyncClient` + regex/JSON-LD parsing — lean, fast.
3. Hybrid httpx with Playwright fallback — not pursued (complexity).

## Decision

We pick **option 2**. Replaced `playwright` with `httpx>=0.27.0` in `requirements.txt`. Dockerfile became lean `python:3.11-slim` + non-root `appuser:1000` (`Dockerfile:1`). Scrapers share `DEFAULT_HEADERS` and use single `AsyncClient` per `screen_multiple` with `asyncio.gather`.

## Consequences

- Good: image >1 GB → ~150 MB, no browser downloads, 10× faster, Railway nixpacks pain gone.
- Cost / follow-up: brittle string parsing; site HTML changes silently yield `NOT_COVERED`/wrong `HALAL` until patched (`39c7a57`).
- Hardware / recovery: N/A.
