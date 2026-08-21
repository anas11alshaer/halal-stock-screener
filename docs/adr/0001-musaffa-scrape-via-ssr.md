# ADR 0001: Scrape Musaffa.com via SSR HTTP parsing

Date: 2026-01-23
Status: accepted

## Context

Need Shariah verdicts for screening. Musaffa has no public API. Initial bot must fetch verdicts reliably from minimal deps. Must decide between official API, headless browser rendering, or raw HTTP parsing. `src/scrapers/musaffa.py:1`, `src/config.py:32`.

## Options

1. Official Musaffa API / partner feed — requires auth/partnership, unavailable at ship time.
2. Headless browser (Playwright) rendering Angular JS — robust to JS but heavy (Chromium ~400 MB, RAM/cold-start painful on Railway).
3. Raw `httpx` HTTP + regex/JSON-LD parsing of SSR meta/chips — lean, fast, but brittle to HTML changes.

## Decision

We pick **option 2 initially, then migrated to option 3** (see ADR 0003). Ship Playwright parsing `Shariah Compliance` DOM/text window, later replace with `httpx` after SSR discovery that verdict is in `<meta name="description">` and status chips. `src/scrapers/musaffa.py:74-94`.

## Consequences

- Good: shipped day-1, robust to JS; later lean image ~150 MB, 10× faster after httpx.
- Cost / follow-up: Chromium bloat drove 7 Dockerfile attempts and OOM (`bbacad6`); HTTP parsing is brittle — HTML changes now silently yield `NOT_COVERED`/wrong `HALAL` until fixed (fixed in `39c7a57`).
- Hardware / recovery: N/A — hosted service.
