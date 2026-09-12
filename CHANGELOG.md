# Changelog

Keep user-facing bullets. Internal refactors stay out unless they change how someone operates the device or app.

## [Unreleased]

### Added

- `/price AAPL MSFT` Telegram command showing live Yahoo prices with day-change % and quote links (up to 25 tickers, uncached); provider is replaceable via `PRICE_PROVIDER_PLUGIN`.
- Internal `GET /screen?ticker=X` JSON endpoint on the health server for the whale_scout service on the same VM — full verdict (status, evidence, confidence, per-source results) as JSON; requires `SCREEN_API_TOKEN` bearer auth (403 when unset); unknown paths now return 404 (`e80431d`).
- Configurable screening-provider, Gemini-reviewer, and delivery-channel plugins.
- Free Daleel stock/ETF provider with SEC/holdings evidence and methodology caveats.
- Company-name resolution, ETF detection, ambiguous-name handling, and dotted tickers.
- Per-provider evidence URLs, operational failure states, and provisional results.
- `d830bbb` docs: migrate to GitHub Flow on `main` — part of PR #2 (`709d632` Merge pull request #2 from anas11alshaer/docs/gitflow-master-template)

### Changed

- Slack answers channel messages without an `@mention` in public and private channels the bot has joined. `@mentions` still work and are not double-handled.
- Confirmed providers now use majority voting; tied votes resolve to Not Halal.
- Cache/history storage is provider-neutral and retains evidence and failure metadata.
- Network requests share connections and retry only transient failures.
- Telegram is a replaceable adapter and displays each configured provider failure.
- `d830bbb` docs: migrate to GitHub Flow on `main` — fix tags (`v0.02.00` → `v0.2.0`), remove `develop`/`release` branches (PR #2 `709d632`) — 2026-08-21
- `2dfb7c2` chore: cleanup repo per new structure — remove `.claude/`, `.cursor/`, `CLAUDE.md`, `AGENTS.md`, `apps/`, `docs/` old vault, `pyproject.toml`, `LICENSE`, `README.md`, `REQUIREMENTS.md` to prepare regeneration (PR #3 `da0cf2e` Merge pull request #3 from anas11alshaer/chore/cleanup-repo) — 2026-08-21

### Fixed

- Slack `/price AAPL MSFT` command showing live Yahoo prices with day-change % and quote links (up to 25 tickers, uncached) — via slash command, `@mention /price ...`, or plain `/price ...` message; previously any `/price` text ran a full halal screen.

## [0.2.1] — 2026-08-21

### Fixed

- `39c7a57` fix: correct Musaffa/Zoya parsers after site HTML changes — Musaffa SEO meta always contains `halal` (false-positive for banks/conventional ETFs); now prefers status chips; Zoya maps `questionable` → `DOUBTFUL`; `resolver.py` preserves `company_name` on conflict resolution (merged in `f4a5fc3` Merge pull request #1 from anas11alshaer/fix/scraper-musaffa-zoya) — 2026-08-08

### Changed

- Tag normalization: `v0.02.00` → `v0.2.0` to follow SemVer (applied in `d830bbb` on `main`; retroactive for `v0.2.0` at `d5f8a60`)

## [0.2.0] — 2026-03-15

### Added

- `47223f4` Add multi-model Gemini rotation, batch screening, and HTTP error handling — 4-model rotation (`gemini-3.1-flash-lite-preview`, `gemini-2.5-flash-lite`, `gemini-3-flash-preview`, `gemini-2.5-flash`) with per-slot RPM/RPD tracking across all API keys, batch max `25` in `screener.py:36`, HTTP `400+` handling in `musaffa.py`/`zoya.py`, `pyproject.toml` + pytest deps — 2026-03-08
- `6f59966` feat: add `REQUIREMENTS.md` and QA release engineer agent — 11 features + 6 error-handling specs, `.claude/agents/qa-release-engineer.md` — 2026-03-15

### Changed

- `850c457` feat: replace multi-key Gemini rotation with daily model rotation — single `GEMINI_API_KEY` cycles across 4 models per request (resets daily); removes `GEMINI_API_KEYS` and `_ModelSlot` complexity, `GEMINI_MODELS` in `src/config.py:40` — 2026-03-15

### Fixed

- `d5f8a60` fix: patch `database.DATABASE_PATH` in test fixtures for proper isolation — patches both `config.DATABASE_PATH` and `database.DATABASE_PATH` in `tests/test_image_parser.py` (tagged `v0.2.0` `d5f8a60`) — 2026-03-15

## [0.1.0] — 2026-01-23 to 2026-03-01 (initial usable)

### Added

- `4ada1c2` Initial commit: Halal Stock Screener Telegram Bot — Telegram polling (`src/bot.py`) with `/start`, `/check`, text+image ticker extraction via Gemini, Musaffa-only screening (`src/scraper.py`), SQLite cache (`CACHE_TTL_HOURS=24` in `src/config.py`), user history (`src/database.py`) — 2026-01-23
- `19f2301` Add Zoya as second compliance source with Railway deployment — `src/scrapers/zoya.py` + `src/resolver.py` conservative resolution (`NOT_HALAL > DOUBTFUL > HALAL > NOT_COVERED`), dual-source DB schema, `google-genai` migration — 2026-01-24
- `c3bcb01` Add health check server for Railway deployment — `GET /` → `OK` on `$PORT` in `src/bot.py`; `74772bd` reorganize scrapers to `src/scrapers/base.py`, `musaffa.py`, `zoya.py`, add `LICENSE` MIT — 2026-01-24
- `d596787` Optimize image analysis for speed and rate limiting — `gemini-2.0-flash-lite`, image hash cache (`ImageCache` in `database.py`), async `asyncio.sleep` rate limit, retry `1s/2s/4s`, medium-resolution photos, `tests/test_image_parser.py` — 2026-01-24
- `5928c31` Add multi-key support for Gemini API to increase quota — `GEMINI_API_KEYS` comma-separated round-robin with per-key cooldowns (`src/config.py`, `src/image_parser.py`) — 2026-01-24
- `21eae68` Add Musaffa authentication and ETF compliance support — `MUSAFFA_EMAIL`/`PASSWORD` session (`data/musaffa_session.json` 23h TTL), `yfinance` `get_quote_type()` ETF URL routing in `base.py`, Zoya ETF `NOT_COVERED` short-circuit — 2026-02-23
- `7ab40f8` Show ETF label and Musaffa-only note in Telegram messages — `src/screener.py` formats ETF single-source indicator (Zoya has no public ETF pages) — 2026-03-01

### Changed

- `d2121df` Improve image parsing, performance, and message formatting — `gemini-2.5-flash`, `QuotaExceededError`, single-browser concurrent pages, grouped status messages, in-place status edits — 2026-01-23
- `23e6669` Replace Playwright with `httpx` for faster, lighter scraping — SSR meta description (Musaffa) + JSON-LD (Zoya) parsing, drop Chromium, lean `Dockerfile` (`python:3.11-slim` non-root), `per-source` results in `screener.py`, `resolver.py` preserves `company_name` (supersedes `21eae68` auth) — 2026-02-26
- Deployment: `Nixpacks` → `Dockerfile` via `f119033` Switch to Dockerfile for reliable Playwright deployment, `bd5054f` Use official Playwright Docker image, `f70f934` Use plain Python image with manual Playwright setup; infra churn `b2e0a98` Fix railway.toml, `b1d7cc7` Simplify Railway config, `b1cd44c` Fix nixpacks, `1c47e10` Fix logging for containerized environment, `c76d2e4` Fix Dockerfile entrypoint, `bbacad6` Lower simultaneous browser pages to prevent RAM exhaustion — collapsed (2026-01-24) — operational, health check retained
- `81f0f5b` Codebase cleanup: extract `BaseScraper` ABC, consolidate `STATUS_ICON`/`STATUS_TEXT`/`DEFAULT_HEADERS` in `base.py`, `asyncio.to_thread` fix, remove dead code — 2026-02-27

### Fixed

- `8a3ccb9` Add quota cooldown and better error logging for image analysis — 5-min cooldown after `resource exhausted`/`too many requests`, distinguish retry vs daily quota — 2026-01-24
- `81f0f5b` fix: `screen_multiple` backoff from flat `1s` to exponential `2**attempt` — 2026-02-27

### Note

- Tags before `0.2.0` used zero-padded `v0.02.00`; normalized to `v0.2.0` in `d830bbb`.
