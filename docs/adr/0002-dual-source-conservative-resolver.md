# ADR 0002: Dual-source compliance (Musaffa + Zoya) with conservative resolver

Date: 2026-01-24
Status: accepted

## Context

Single-source screening gave false positives and `NOT_COVERED` gaps. Need cross-check and offline speed via cache. Orchestration in `src/screener.py:190`, resolver in `src/resolver.py:10`, per-source TTL cache in `src/database.py:115`.

## Options

1. Single source (Musaffa only) — simple but trusts one site's SEO/meta drift.
2. Voting/average of sources — rejects conservative Shariah principle.
3. Most-restrictive wins + `NOT_COVERED`/`ERROR` fallback with per-source SQLite TTL cache — chosen, codified as `NOT_HALAL > DOUBTFUL > HALAL > NOT_COVERED > ERROR` in `src/resolver.py:11`.

## Decision

We pick **option 3**. Added `ZoyaScraper` (`https://zoya.finance/stocks`, lowercase URLs) and `resolver.py`. Expanded SQLite: `cache PK (ticker,source)` and `checks(musaffa_status,zoya_status,final_status,is_conflict)` with migration `src/database.py:30-107`. TTL `CACHE_TTL_HOURS` (`src/config.py:26`), `ImageCache` 24h. Batch `MAX_TICKERS_PER_REQUEST=25` (`src/config.py:36`). ETF routing via `yfinance.get_quote_type()` → `MUSAFFA_ETF_BASE_URL` vs Zoya `NOT_COVERED` for ETFs (`src/scrapers/zoya.py:28`).

## Consequences

- Good: higher trust, catches Musaffa SEO drift; cache halves duplicate scrapes; history auditability; parallel `asyncio.gather` batch.
- Cost / follow-up: 2× HTTP per ticker; cache stores status not `company_name`/`quote_type` (display degrades until TTL); `get_quote_type` via yfinance can misclassify.
- Hardware / recovery: N/A.
