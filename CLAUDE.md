# CLAUDE.md

This file provides guidance to Claude Code when working with this repository. **All development in this project is AI-written.** These rules govern how features are designed, implemented, and tested.

---

## Project Overview

A Telegram bot that checks whether stocks are Shariah-compliant (Halal) by scraping compliance data from multiple sources (Musaffa.com and Zoya). Users send ticker symbols via text or images; the bot returns compliance status with conservative conflict resolution when sources disagree.

---

## Commands

```bash
# Run the bot
python src/bot.py

# Install dependencies
pip install -r requirements.txt

# Run tests
pytest tests/
python tests/test_scraper.py
python tests/test_image_parser.py
```

---

## Architecture

```
Telegram → bot.py → screener.py → scrapers/ → Musaffa.com
                         ↓              ↘→ Zoya
                    resolver.py (conflict resolution)
                         ↓
              database.py (SQLite: cache, checks, image_cache)
                         ↓
              image_parser.py (Gemini AI for images)
```

### Data Flow

1. **bot.py** receives Telegram messages (text, commands, or photos)
2. **screener.py** orchestrates the workflow:
   - Extracts tickers from text via regex or from images via Gemini
   - Checks SQLite cache first (24-hour TTL)
   - Calls both scrapers in parallel for uncached tickers
   - Uses resolver.py to handle conflicts between sources
   - Records results in user history
3. **scrapers/** uses `httpx` (async) — no browser needed:
   - `base.py`: `BaseScraper` ABC with shared retry logic, `ComplianceStatus` enum, `ScreeningResult` dataclass, `STATUS_ICON`/`STATUS_TEXT` dicts, `DEFAULT_HEADERS`
   - `musaffa.py`: scrapes meta description tag (SSR)
   - `zoya.py`: scrapes JSON-LD FAQPage structured data
4. **database.py** provides three SQLite tables:
   - `cache`: ticker results per source (24-hour TTL)
   - `checks`: user check history
   - `image_cache`: extracted tickers by image hash

### Key Classes

| Class | File | Role |
|-------|------|------|
| `StockScreener` | screener.py | Main orchestrator |
| `BaseScraper` | scrapers/base.py | Abstract base with retry logic |
| `MusaffaScraper` | scrapers/musaffa.py | Musaffa scraper |
| `ZoyaScraper` | scrapers/zoya.py | Zoya scraper |
| `ImageParser` | image_parser.py | Gemini AI integration |
| `TickerCache` / `CheckHistory` / `ImageCache` | database.py | SQLite data access |
| `resolve_compliance` | resolver.py | Conflict resolution |

### Compliance Status

Defined in `scrapers/base.py` as `ComplianceStatus`: `HALAL`, `NOT_HALAL`, `DOUBTFUL`, `NOT_COVERED`, `ERROR`

Display helpers in the same file:
- `STATUS_ICON`: `ComplianceStatus` → emoji
- `STATUS_TEXT`: `ComplianceStatus` → human-readable string

---

## Configuration

Environment variables loaded from `.env` via python-dotenv:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | — | Bot token from BotFather |
| `GEMINI_API_KEY` | No | — | Gemini key for image analysis (rotates across 4 models daily) |
| `CACHE_TTL_HOURS` | No | `24` | Cache expiration in hours |
| `LOG_LEVEL` | No | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

Path constants are defined in `config.py` and auto-create `data/` and `logs/` directories.

---

## Project Structure

```
src/
├── bot.py              # Telegram bot entry point
├── config.py           # Configuration and path constants
├── database.py         # SQLite cache and history
├── image_parser.py     # Gemini AI image analysis
├── resolver.py         # Multi-source conflict resolution
├── screener.py         # Main orchestrator
└── scrapers/
    ├── __init__.py     # Package exports
    ├── base.py         # BaseScraper ABC, enums, dataclasses, shared constants
    ├── musaffa.py      # Musaffa.com scraper
    └── zoya.py         # Zoya scraper
tests/
├── test_image_parser.py  # Image parser and cache tests
└── test_scraper.py       # Scraper and resolver tests
```

---

## Git Workflow

This project uses a simple feature-branch workflow. Work directly on `master` for small fixes. Use a branch for anything non-trivial.

### When to create a branch

Create a branch when:
- Adding a new feature (new scraper, new bot command, new caching strategy)
- Making a change that could break existing behavior
- Doing exploratory or experimental work
- The task spans multiple files or sessions

Work directly on `master` for:
- Small bug fixes (1–3 lines)
- Documentation updates
- Config tweaks

### Branch workflow

```bash
# 1. Create and switch to a new branch
git checkout -b feature/add-zakat-screener

# 2. Work normally — make changes, run tests
pytest tests/

# 3. Stage and commit your changes
git add src/scrapers/zakat.py tests/test_scraper.py
git commit -m "Add Zakat compliance scraper"

# 4. Merge back into master
git checkout master
git merge feature/add-zakat-screener

# 5. Delete the branch (it's been merged, no longer needed)
git branch -d feature/add-zakat-screener
```

### Branch naming convention

Use a short prefix followed by a descriptive slug:

| Prefix | When to use | Example |
|--------|-------------|---------|
| `feature/` | New functionality | `feature/add-islamicly-scraper` |
| `fix/` | Bug fix | `fix/zoya-etf-parsing` |
| `refactor/` | Code restructuring | `refactor/database-layer` |
| `chore/` | Cleanup, deps, config | `chore/update-requirements` |

### Checking status

```bash
git status              # What changed
git log --oneline -10   # Recent commits
git branch              # List all branches (* = current)
git diff                # See unstaged changes
```

### Rules for AI-written commits

- Stage specific files by name — never `git add .` blindly
- Write commit messages that explain *why*, not just *what*
- One logical change per commit
- Never force-push to `master`
- Never amend commits that have already been pushed

---

## AI Development Rules

This project is entirely AI-written. The following rules must be followed precisely in every change, large or small.

### 1. Read Before Writing

**Always read every file you will modify before touching it.** Never guess at existing structure, function signatures, or variable names. Use the Read tool, then edit.

### 2. Minimal Changes Only

Make only the changes that are directly requested. Do not:
- Refactor surrounding code that wasn't asked about
- Add docstrings, comments, or type annotations to unchanged code
- Rename variables, reorganize imports, or reformat files you're editing
- Add logging, error handling, or validation beyond what's clearly necessary

### 3. No Premature Abstractions

- Do not create helpers, utilities, or base classes unless they're used in at least two places
- Three similar lines of code is better than a premature abstraction
- If a feature can be added inline, add it inline

### 4. Preserve the Async Pattern

All I/O in this project is `async`. When writing new code:
- Use `async def` and `await` for all network calls, database queries, and file I/O
- Use `asyncio.gather()` for parallel operations (see screener.py for the pattern)
- Never use `time.sleep()` — use `await asyncio.sleep()`

### 5. Follow Existing Conventions

| Convention | Rule |
|------------|------|
| HTTP | Use `httpx.AsyncClient` — never `requests`, `aiohttp`, or Playwright |
| Database | Use `database.py` classes — never raw SQL in other files |
| Config | Read from `config.py` constants — never `os.environ` directly in feature code |
| Logging | Use `logging.getLogger(__name__)` — never `print()` |
| Status | Use `ComplianceStatus` enum — never raw strings for compliance values |
| Display | Use `STATUS_ICON` / `STATUS_TEXT` from `scrapers/base.py` |

### 6. Adding a New Scraper

To add a new compliance data source:

1. Create `src/scrapers/newsite.py` — subclass `BaseScraper` from `base.py`
2. Implement the `source_name` property and `_fetch_single(ticker)` method
3. Export it from `scrapers/__init__.py`
4. Register it in `screener.py` alongside the existing scrapers
5. Update `resolver.py` if the new source needs special weighting
6. Add integration tests to `tests/test_scraper.py`

The `BaseScraper` handles retry logic, ETF detection, and result wrapping — your scraper only needs to implement `_fetch_single()`.

### 7. Adding a New Bot Command

1. Add the handler function in `bot.py` following the existing pattern (`async def command_handler(update, context)`)
2. Register it with `application.add_handler(CommandHandler(...))` in the `main()` function
3. Add it to the `/help` text and the `BotCommand` list in `main()`
4. If the command needs new data, add the database method to `database.py` first

### 8. File Placement Rules

| What | Where |
|------|-------|
| New scraper | `src/scrapers/newsite.py` |
| New bot command | `src/bot.py` |
| New database table or query | `src/database.py` |
| New config variable | `src/config.py` + `.env.example` |
| New shared enum or dataclass | `src/scrapers/base.py` (if scraper-related) or `src/screener.py` |
| Tests | `tests/test_<module>.py` |

Do not create new top-level files unless a completely new module is warranted.

### 9. Testing Requirements

- Every new scraper gets integration tests in `tests/test_scraper.py`
- Every new utility or parser gets unit tests in the appropriate test file
- Tests that make real HTTP requests must be clearly labeled as integration tests
- Run `pytest tests/` before declaring a feature complete
- Do not mock internal functions — mock only external I/O (HTTP, Gemini API)

### 10. Security Rules

- Never log credentials, API keys, session tokens, or user PII
- Never commit `.env` or any file containing real credentials
- Validate all user input in `bot.py` before passing it to the screener
- Do not add new external HTTP calls outside the scrapers package without explicit approval
- All secrets go in `.env` and are read through `config.py` — never hardcoded

### 11. Performance Rules

- The httpx approach is fast — do not add latency
- Cache aggressively: if an external call can be cached, cache it in `database.py`
- Parallel scraping is already implemented — do not serialize it
- Never block the event loop: all I/O must be awaited

### 12. What to Avoid

- **Playwright, Selenium, or any browser automation** — httpx is sufficient
- **New dependencies** without adding to `requirements.txt`
- **Hardcoded URLs or credentials** — use `config.py`
- **Global mutable state** outside of the class instances in `bot.py`
- **Backwards-compatibility shims** — just change the code
- **Speculative features** — only implement what was asked for

---

## Scraper Implementation Notes

### Musaffa (`musaffa.py`)
- Stocks: `GET /stock/{TICKER}/` — parse `<meta name="description">` for verdict
- ETFs: `GET /etf/{TICKER}/` — same parsing, no authentication required
- ETF detection: `get_quote_type(ticker)` from `base.py` (uses yfinance, cached in-memory)

### Zoya (`zoya.py`)
- Stocks: `GET /stocks/{ticker_lowercase}` — parse JSON-LD FAQPage or H2 heading fallback
- ETFs: immediately return `NOT_COVERED` (Zoya doesn't screen ETFs)

### Image Parser (`image_parser.py`)
- Model: `gemini-2.5-flash-lite`
- Daily model rotation: cycles through 4 models per request, resets each day
- SHA-256 image hashing with 24-hour SQLite cache
- Exponential backoff: 3 retries (1s, 2s, 4s delays)
