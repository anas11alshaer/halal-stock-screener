# Code Review Findings — stock_screener audit

Date: 2026-08-07  
Scope: full `src/`, `tests/`, config, Docker, docs pointers  
Method: static review of every Python module + live Musaffa/Zoya ground truth (in progress)

## Critical / High

### 1. Musaffa meta SEO false-HALAL (FIXED in working tree)
Meta descriptions are now `"Is X halal?…"` with no verdict, so old `"halal" in meta` logic marked banks/ETFs HALAL. Parser now uses `status-text` chip / `classified as …`. Label normalizer must strip spaces (`NOT HALAL` → `nothalal`).

### 2. Zoya FAQ JSON-LD contradictory templates (FIXED in working tree)
Prefer H2; map `questionable` → DOUBTFUL; ignore ambiguous JSON-LD.

### 2. Cache omits `company_name` and `quote_type`
`TickerCache.set/get` only stores status/ranking/details. On cache hit, ETF detection and company names degrade until TTL expiry; display and ETF/Musaffa URL routing still re-call yfinance per ticker at end of batch, but source results lack names.
- File: `src/screener.py` ~230–247, `src/database.py` `TickerCache`

### 3. Conflict resolution drops `company_name` when Zoya wins
`resolve_compliance` sets `company_name=musaffa.company_name if winning_source == "musaffa" else None` — Zoya path loses Musaffa’s name (Zoya scraper never sets company_name).
- File: `src/resolver.py` ~126–133

### 4. Broken / stale venv home path
Old venv targeted missing `D:\Program Files\Python 3.14\python.exe`. Recreated against `D:\Programs\Python` (3.13.14). Document Python path in README/AGENTS.

## Medium

### 5. Welcome text claims Musaffa-only
`bot.py` `/start` says “using Musaffa.com data” but dual-source + ETF Musaffa path is real behavior. Misleading for users debugging mismatches.

### 6. Duplicate status emoji maps in `bot.py`
`history_command` / `stats_command` hardcode icons instead of `STATUS_ICON` from `scrapers.base` — drift risk.

### 7. Sync SQLite on async event loop
All `database.py` access is blocking `sqlite3` inside async handlers. Under load, stalls Telegram responsiveness. Prefer `asyncio.to_thread` or aiosqlite.

### 8. `os.environ` for `PORT` in `bot.py`
Violates project convention (config via `config.py` only). Minor.

### 9. Image parser always `mime_type="image/jpeg"`
Telegram PNGs/WebP still labeled JPEG for Gemini — can reduce extraction quality.

### 10. Ticker length / format gaps
`is_valid_ticker` allows `^[A-Z]{1,5}(\.[A-Z])?$` — rejects some real symbols (e.g. longer tickers, numeric class shares). `screen_text` fallback only accepts pure alpha 1–5.

### 11. `NOT_COVERED` is cached for 24h
Temporary site outages or parse failures that return NOT_COVERED (vs ERROR) poison cache. Zoya parse-fail returns NOT_COVERED, not ERROR — then gets cached.

### 12. Serial `get_quote_type` after screening
After batch resolve, each ticker awaits yfinance sequentially (`screener.py` ~324–325). Slow for large batches; should gather.

### 13. Health server binds `0.0.0.0` with no auth
Expected for Railway health checks; ensure no sensitive routes (currently only “OK”).

## Low / Dead code / Docs drift

### 14. CLAUDE.md lists BeautifulSoup; scrapers use regex; BS4 only via yfinance transitive dep
### 15. `tests/test_scraper.py` is a printable script, not pytest assertions for scrapers (resolver asserts only when run as `__main__`)
### 16. `docs/` empty / gitignored — overview/architecture/ai-context missing locally
### 17. README still says “google-genai multi-key”; code is single `GEMINI_API_KEY` + model rotation
### 18. `compliance_ranking` never populated by Musaffa parser (always None)
### 19. No rate limiting / user input cap beyond `MAX_TICKERS_PER_REQUEST` batching — unbounded message length still parsed

## Security

- `.env` gitignored — good. Do not commit.
- User IDs logged in debug paths — avoid elevating to INFO with PII.
- No auth on bot (Telegram open) — by design; consider allowlist if abused.
- Scrapers send browser UA — fine; no credentials in requests.
- `deploy.bat` gitignored (IPs) — good.

## Ground-truth snapshot (manual browser fetch, Aug 2026)

| Ticker | Musaffa (live) | Zoya (live H2) | Notes |
|--------|----------------|----------------|-------|
| AAPL | HALAL | Shariah-compliant | Zoya FAQ HTML has contradictory templates |
| JPM | NOT HALAL | not Shariah-compliant | |
| TSLA | HALAL | (pending script) | |
| SPUS ETF | HALAL | N/A | |
| SPY ETF | NOT HALAL | N/A | |

Live scraper script results: see `tmp_audit_screen.py` run output / later loop ticks.
