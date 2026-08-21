# AGENTS.md — Stock Screener

Guidance for AI agents working in this repository.

## What this project is

A Telegram bot that screens stocks/ETFs for Shariah compliance by scraping **Musaffa** and **Zoya**, then resolving disagreements conservatively (most restrictive status wins). Accepts text tickers or portfolio screenshots (Gemini).

Authoritative project orientation also lives in `CLAUDE.md`. Prefer this file for agent workflow; prefer `CLAUDE.md` for stack/tiers/conventions.

## Stack (quick)

- Python 3.11+, `python-telegram-bot`, `httpx`, SQLite, `yfinance` (quote type / ETF detection), Google Gemini for images
- Entry: `src/bot.py` — run from project root as `python src/bot.py`
- Scrapers: `src/scrapers/{musaffa,zoya}.py` via `BaseScraper`
- Orchestration: `src/screener.py` → `src/resolver.py` → `src/database.py`
- Config: `src/config.py` only (no `os.environ` in feature modules except known legacy `PORT` in `bot.py`)

## Branches & testing

- Dev: `development/D0.02.00` · Release: `release/R0.02.00`
- **Do not run pytest on feature/bugfix branches** — release branch only
- After meaningful changes on release: `venv\Scripts\activate; pytest tests/ -x`

## Agent rules of engagement

1. **Minimal diffs** — this codebase is AI-written; do not drive-by refactor, rename, or add docstrings to untouched code.
2. **Async I/O** — `httpx.AsyncClient`, `asyncio.gather`, never `requests` / Playwright / Selenium.
3. **ComplianceStatus enum** — never raw status strings in logic; display via `STATUS_ICON` / `STATUS_TEXT`.
4. **DB only via `database.py`**; new config → `config.py` + `.env.example`.
5. **Never commit `.env`**, never log tokens/keys/PII.
6. **New scraper checklist**: subclass `BaseScraper` → export in `scrapers/__init__.py` → register in `screener.py` → resolver weighting if needed → tests on release.

## Known screening correctness risks (audit focus)

Treat these as first-class when debugging “wrong Halal/Haram results”:

- Scrapers parse SSR meta / JSON-LD; site HTML changes break parsing silently → `NOT_COVERED` or wrong status.
- Musaffa substring order must check **“not halal” before “halal”** (already ordered; do not regress).
- Zoya has **no ETF pages** → ETF path is Musaffa-only; resolver must ignore Zoya `NOT_COVERED` for ETFs.
- Cache (`TickerCache`) stores status but **not `company_name` / `quote_type`** — cache hits can degrade display and ETF labeling until TTL expiry.
- `get_quote_type` via yfinance can misclassify or fail → wrong Musaffa URL (`/stock/` vs `/etf/`).
- Conflict path can drop `company_name` when Zoya “wins”.
- Welcome copy in `bot.py` still says “Musaffa.com data” only — dual-source is the real behavior.

## Validation workflow for screening bugs

1. Fetch live pages (Musaffa stock/ETF + Zoya stock) without the bot; record expected status.
2. Run `StockScreener.screen_tickers([...])` (or Telegram `/check`) with a **fresh cache** for those tickers.
3. Compare per-source and final verdict; log mismatches with HTML snippet evidence.
4. Prefer a diverse set: clear Halal equity, clear Not Halal (e.g. banks), Doubtful if available, ETF Halal (e.g. SPUS/HLAL), conventional ETF (SPY), obscure/not covered, conflict-prone names, special symbols (e.g. `BRK.B`).

## Security notes for agents

- `.env` is gitignored; never print `TELEGRAM_BOT_TOKEN` / `GEMINI_API_KEY`.
- Validate/normalize tickers in bot handlers before scraping.
- No new outbound HTTP outside `scrapers/` without explicit approval.

## Docs

`docs/` is light and may be empty locally (often gitignored). Use `CLAUDE.md`, `README.md`, and this file.
