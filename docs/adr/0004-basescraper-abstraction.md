# ADR 0004: BaseScraper abstraction + shared constants

Date: 2026-02-27
Status: accepted

## Context

After httpx migration both scrapers duplicated retry/backoff, `screen_ticker`/`screen_multiple`, headers, status maps, and a bug (`1s` vs `2**attempt` backoff). Need single place for retry and orchestration. `src/scrapers/base.py:96-174`.

## Options

1. Keep duplicated concrete classes — status quo, bug-prone.
2. Extract abstract base with template method — chosen.
3. Utility functions only — still duplicates orchestration.

## Decision

We pick **option 2**. Created `class BaseScraper(ABC)` with abstract `source_name` + `_fetch_single(client,ticker)`, concrete `screen_ticker`/`screen_multiple` with `2**attempt` backoff, shared `DEFAULT_HEADERS`, `STATUS_ICON`/`TEXT`, `get_quote_type` via `asyncio.to_thread` (`src/scrapers/base.py:49`). `MusaffaScraper`/`ZoyaScraper` implement only parsing. Removed dead `TickerCache.get_all_sources`, etc. (`81f0f5b`).

## Consequences

- Good: ~120 lines removed, single retry fix, consistent icons.
- Cost / follow-up: scrapers coupled to base lifecycle (retry policy now global).
- Hardware / recovery: N/A.
