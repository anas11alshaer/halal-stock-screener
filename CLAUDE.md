# stock_screener

A Telegram bot that checks whether stocks are Shariah-compliant by scraping multiple sources (Musaffa, Zoya) and resolving conflicts conservatively. Accepts text or image input.

## Project Profile

- `project_tier`: production
- `docs_tier`: light
- `git_tier`: disciplined
- `testing_tier`: release-only
- `logging_tier`: app-logs
- `code_doc_tier`: full

## Stack & Versions

- Python 3.x
- python-telegram-bot
- httpx (async)
- BeautifulSoup
- SQLite
- Google Gemini (`gemini-2.5-flash-lite` for image parsing, daily 4-model rotation)
- pytest with `asyncio_mode = "auto"` (configured in `pyproject.toml`)
- Containerized via `Dockerfile`

## Build / Run / Test

**Install dependencies:**
```
source venv/Scripts/activate && pip install -r requirements.txt
```

**Run (development):**
```
source venv/Scripts/activate && python src/bot.py
```

**Run (production):**
```
source venv/Scripts/activate && python src/bot.py
```

**Test:**
```
source venv/Scripts/activate && pytest tests/
```
Run on release branch only.

## Repository Layout

```
stock_screener/
├── src/
│   ├── bot.py               # Entry point — Telegram handlers, commands
│   ├── screener.py          # Scraper registry, screening orchestration
│   ├── resolver.py          # Conflict resolution logic across sources
│   ├── database.py          # All DB tables, queries, and cache logic
│   ├── config.py            # Constants and config (never os.environ in feature code)
│   └── scrapers/
│       ├── __init__.py      # Exports all scrapers
│       ├── base.py          # BaseScraper: retry, ETF detection, ComplianceStatus enum, STATUS_ICON/STATUS_TEXT
│       ├── musaffa.py
│       └── zoya.py
├── tests/
│   └── test_scraper.py
├── docs/                    # Light docs vault
├── logs/
│   └── stock_screener.log   # Dual stdout + file logging
├── pyproject.toml           # Pytest config (asyncio_mode = auto)
├── Dockerfile
└── .claude/agents/          # Agent definitions
```

## Conventions

- All I/O is `async`. Use `httpx.AsyncClient` — never `requests`, `aiohttp`, or browser automation (Playwright, Selenium). Use `asyncio.gather()` for parallel ops; `await asyncio.sleep()` not `time.sleep()`.
- Database access via `database.py` classes only — no raw SQL elsewhere in the codebase.
- Config read from `config.py` constants — never `os.environ` directly in feature code.
- Logging via `logging.getLogger(__name__)` — never `print()`.
- Compliance status: use the `ComplianceStatus` enum (HALAL, NOT_HALAL, DOUBTFUL, NOT_COVERED, ERROR) — never raw strings. Display via `STATUS_ICON` / `STATUS_TEXT` from `scrapers/base.py`.
- File placement: new scraper → `src/scrapers/<site>.py` subclassing `BaseScraper`; new bot command → `bot.py`; new DB table/query → `database.py`; new config var → `config.py` + `.env.example`.
- Cache aggressively in `database.py`. Parallel scraping is already implemented — do not serialize. Never block the event loop.
- Adding a new scraper: subclass `BaseScraper`, implement `source_name` property and `_fetch_single(ticker)`, export from `scrapers/__init__.py`, register in `screener.py`, update `resolver.py` if special weighting needed, add tests in `tests/test_scraper.py`. `BaseScraper` handles retry, ETF detection, and result wrapping.
- Image parser: `gemini-2.5-flash-lite` with daily 4-model rotation, SHA-256 image hashing, 24h SQLite cache, 3 retries with exponential backoff (1s, 2s, 4s).
- **This project is entirely AI-written.** Make minimal, requested-only changes — do not refactor unrelated code, do not add docstrings to unchanged code, do not rename or reformat. Three similar lines is better than a premature abstraction; helpers/base classes only when used in 2+ places.

## Coding Rules

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan with verifiable checks per step.

Strong success criteria let Claude loop independently. Weak criteria ("make it work") require constant clarification.

### 5. Virtual Environments

**Always operate inside the project's virtual environment / isolated dev environment.**

- Python: activate `.venv` / `venv` before any command. Do not install packages or run scripts outside it.
- Node/JS: use the project's `node_modules` (run via `npm` / `yarn` / `pnpm`); do not install globally.
- Other languages: use the project's declared isolation (Cargo workspace, Go modules, etc.).
- If no isolated environment exists for the language, ask before creating one.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

## Gotchas

- Security: never log credentials, API keys, session tokens, or PII. Validate user input in `bot.py` before passing to screener. Never commit `.env`. No new external HTTP calls outside the scrapers package without approval.
- Tests run on `release/R0.02.00` only — do not run pytest on feature/bugfix branches.
- `pyproject.toml` sets `asyncio_mode = "auto"` — all async test functions are automatically treated as async tests without the `@pytest.mark.asyncio` decorator.

## Entry Points

- Application entry: `src/bot.py` (Telegram handlers, all `/command` logic)
- Screener registry: `src/screener.py` (scraper orchestration, conflict resolution entry)
- Database: `src/database.py` (all tables, cache, queries — single source of DB truth)
- Config: `src/config.py` (constants — read this before any feature code)

---

## Current Branch *(git_tier: disciplined)*

- Development branch: `development/D0.02.00`
- Release branch: `release/R0.02.00`

## Logs Location *(logging_tier: app-logs)*

- Log directory: `logs/`
- Log file: `logs/stock_screener.log`
- Rotation: *(none captured yet — fill in)*

## Docs Vault Pointers *(docs_tier: light)*

- [[docs/overview]] — what the project does, plain English
- [[docs/architecture]] — how modules connect
- [[docs/ai-context]] — Claude's project orientation (loaded at session start)
