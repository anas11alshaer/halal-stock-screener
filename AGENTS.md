# AGENTS.md

Filled from `D:\projects\guide\templates\AGENTS.md` for **this** repository. Instructions plus project context for an AI working in this tree.

## Project Overview

- **Name:** halal-stock-screener
- **Purpose:** Private hosted service that resolves stock/ETF names and tickers, queries configurable Shariah-screening providers, and shows every verdict, failure, and evidence URL. Default free profile is Musaffa public pages, Zoya public stock pages, and Daleel. Optional keyed providers, Gemini image/evidence plugins, Slack, and an internal `/screen` JSON API exist; they are off until configured.
- **Target audience:** The installer (single-user private bot). Telegram (and optional Slack) is the human UI. `whale_scout` may call `GET /screen` on the Docker network when `SCREEN_API_TOKEN` is set.
- **Kind:** service — Python process: health HTTP server plus replaceable delivery channels.

## Tech Stack

Anything not listed here must not be added without an explicit why, pin, and license.

- **Languages:** Python 3.12 (CI, README, ruff `target-version = "py312"`)
- **Frameworks / libraries:** `python-telegram-bot>=21.0`, `slack-bolt==1.30.0`, `aiohttp==3.14.3`, `httpx>=0.27.0`, `yfinance>=0.2.0`, `google-genai>=1.0.0`, `python-dotenv>=1.0.0`, `pytest>=8.0.0`, `pytest-asyncio>=0.24.0`
- **Databases:** SQLite via stdlib `sqlite3` → `data/stock_screener.db`
- **Package manager:** pip + `requirements.txt` (`pyproject.toml` is ruff-only; there is no `pip install -e ".[dev]"`)
- **Language pack(s) to follow:** python
- **Other important versions:** GitHub Actions `setup-python` 3.12; Docker base `python:3.11-slim-bookworm` (drift vs CI); ruff unpinned in CI (`pip install ruff`), config in `pyproject.toml` (line-length 100)

## Project Structure

```text
halal-stock-screener/
  src/bot.py                      # Entry: health + /screen HTTP, then load/run channels
  src/config.py                   # All getenv/dotenv (except PORT in bot.py)
  src/plugins.py                  # importlib loaders: module:Class
  src/screener.py                 # Orchestration, cache, user-facing render
  src/resolver.py                 # Majority / tie / provisional vote
  src/security_resolver.py        # Yahoo identity + asset type
  src/database.py                 # All SQLite, TTL, migrations
  src/reviewer.py                 # Optional Gemini parse-failure review
  src/image_parser.py             # Gemini ImageExtractor implementation
  src/scrapers/                   # BaseScraper + musaffa, zoya, daleel, optional keyed
  src/channels/                   # telegram.py, slack.py
  src/image_extractors/           # ImageExtractor ABC
  tests/                          # pytest; each file inserts ../src on sys.path
  docs/adr/                       # 0001–0008; current architecture is 0008
  docs/planning/                  # Deferred; not current architecture
  .github/ISSUE_TEMPLATE/ISSUE.md
  .github/PULL_REQUEST_TEMPLATE.md
  .github/workflows/ci.yml        # ruff check + pytest
  CHANGELOG.md                    # User-facing history
  README.md                       # Operator runbook
  requirements.txt                # Runtime + test pins
  pyproject.toml                  # Ruff only
  Dockerfile                      # python:3.11-slim; CMD python src/bot.py
  .env.example                    # Secret + plugin template
  data/                           # SQLite (gitignored); do not list secret files
  logs/                           # Application log (gitignored)
```

- **Entry:** `python src/bot.py` — daemon health server on `$PORT` (default 8080), then `load_delivery_channels()`; one channel occupies the main thread, otherwise one thread per channel
- **Do not touch:** `.env`, `data/*.db`, `data/oracle_cloud`, `data/musaffa_session.json`, `logs/`, `venv/`, `deploy.bat` / `deploy.sh` (gitignored). Do not re-add `apps/`. `docs/adr/` is live — do not treat `docs/` as an old vault to ignore

## Architecture & design

- **Flow:** channel adapter → `StockScreener` → `YahooSecurityResolver` (identity once) → each capable provider (`BaseScraper._fetch_single`) → optional Gemini reviewer on `PARSE_ERROR` → `resolve_compliance` (vote) → cache/history → `ScreenResponse.format_message`
- **Where it lives:** config `src/config.py`; HTTP I/O `src/scrapers/` (retry only on `BaseScraper`); SQLite `src/database.py`; domain `src/screener.py`, `src/resolver.py`, `src/security_resolver.py`; UI `src/channels/` plus `ScreenResponse.format_message`
- **Forbidden imports / boundaries:** no `os.environ` / `os.getenv` outside `config.py` except `src/bot.py` `$PORT`; screening core must not import concrete providers, Gemini, or Telegram; no SQL outside `database.py`; do not reimplement scraper retry
- **Current design:** `docs/adr/0008-modular-multi-provider-screening.md` — `module:Class` plugins; vote only confirmed verdicts; unique majority wins; top-count tie → `NOT_HALAL`; one confirmed result is provisional. Executable vote: `src/resolver.py`. Contract: `src/scrapers/base.py`. Loader: `src/plugins.py`
- **Superseded / not now:** `docs/adr/0002-dual-source-conservative-resolver.md` — do not resurrect two-source ranking. `docs/planning/2026-08-21-vercel-modular-future.md` — deferred, not current architecture. ADR 0003 — do not reintroduce Playwright

## Coding conventions

- **Language pack:** `D:\projects\guide\language-packs\python.md` plus `D:\projects\guide\style\00-core.md` and `D:\projects\guide\style\python-style.md`
- **Naming:** Flat `src/` on `PYTHONPATH` (`from config import …`, not `src.…`). Providers subclass `BaseScraper`; `source_name` is unique lowercase snake (`musaffa`, `halal_terminal`). Tests: `tests/test_<area>.py`
- **Exports / public API:** Not an installed package. Plugin contract is `module:Class`. Providers implement `source_name` + `_fetch_single`. Image extractors extend `ImageExtractor.extract_tickers`. Channels expose `run()`
- **Lint / format:** ruff — `pyproject.toml`. CI runs `ruff check .` only (not `ruff format --check`). Line length 100. Existing `ignore` list is intentional (including `BLE001` for provider/Gemini resilience)
- **Comments:** why, not what. Module docstring is one-sentence purpose. Logger is `logging.getLogger(__name__)`
- **Patterns:** dataclasses for results; enums for `ComplianceStatus` / `ResultState`; ABC + `@abstractmethod` for plugins. Fakes in tests subclass `BaseScraper`. User-facing HTML is escaped (`html.escape`); Slack re-escapes via `to_slack_mrkdwn`

## Tests

- Write tests from the human’s stated acceptance / bug / current behavior **only**, **before** implementation.
- Do not rewrite tests to match the implementation. Expected values come from the **Issue**, not from running the current code.
- Do not invent extra product behavior “for coverage.”
- **Runner:** `pytest` (CI: `pytest -o asyncio_mode=auto`)
- **Layout:** `tests/`; each file does `sys.path.insert` of `../src`. No `conftest.py`. Not an installed package
- **This repo:** isolate DB with `monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "test.db")` (image-parser tests patch **both** `config.DATABASE_PATH` and `database.DATABASE_PATH`). No live HTTP for provider tests — mock transport / fakes. Dummy `GEMINI_API_KEY` / `TELEGRAM_BOT_TOKEN` in CI

## Git (branches & commits)

House GitHub Flow. Recipes: `D:\projects\guide\git\` (start at `git/00-where-am-i.md` if lost). This repo’s history: `docs/adr/0007-github-flow-on-main.md`.

- `main` is protected and stays releasable. Never commit product work on `main`.
- There is **no** `develop`. Do not create it. Do not resurrect old `development/` or `release/R*` GitFlow branches.
- One Issue per branch. Every product change is a short-lived branch + PR into `main`. Delete the branch after merge.
- Branch names: `feature/<issue>-<slug>`, `fix/<issue>-<slug>`, `refactor/<issue>-<slug>`, `docs/<slug>`, `chore/deps-<name>`, `hotfix/<version>-<slug>`, `spike/<slug>`
- Conventional Commits: `feat` / `fix` / `refactor` / `docs` / `test` / `chore`. Put `Fixes #N` / `Closes #N` on the **PR**, not every commit
- Do not `git add .` until you have read `git status`. Do not commit `.env`, tokens, `data/musaffa_session.json`, or `data/oracle_cloud`
- Do not mix a feature with a repo-wide reformat in one PR
- Tags are `vX.Y.Z` SemVer **when you mean to ship**. Merging to `main` is not a tag. Current shipped tag is `v0.2.1`

## GitHub Issues & PRs

The **Issue is the spec. The chat is not.**

**Issues**

- Open from `.github/ISSUE_TEMPLATE/ISSUE.md`. Title: work type + slice (`feat: …`, `fix: …`).
- Body: summary (problem, done-this-week), work type, **acceptance** (paste the matching file from `D:\projects\guide\templates\requirements-*.md` or `spike-notes.md` into the Issue), out of scope.
- Do **not** keep a separate `REQUIREMENTS.md` in the repo. AI must not add acceptance lines the human did not approve.
- One Issue ≠ a whole product. Split. Tiny docs-only typos may skip an Issue; prefer one for history.

**PRs**

- Every product change lands via a GitHub PR into `main`. Base is not `develop`.
- Body from `.github/PULL_REQUEST_TEMPLATE.md`: summary, `Closes #N` or `Refs #N`, work type, test plan (automated: `pytest` / `ruff check .`; manual: Telegram `/check` and/or `curl http://localhost:8080`).
- CI must be green unless CI is broken **and** a separate Issue exists. Do not claim CI passed.
- ALWAYS merge with merge commits (`--no-ff`), NEVER squash. PR title = conventional subject (`feat: …`). Delete the branch after merge.
- CI does not replace manual verification.

## Changelog

- File: `CHANGELOG.md` (house shape: `D:\projects\guide\templates\changelog.md`).
- User-facing bullets only. Internal refactors stay out unless they change how someone **operates** the service (run command, env vars, bot commands, `/screen`).
- Keep `[Unreleased]`; move bullets under `[X.Y.Z] — YYYY-MM-DD` when you tag.
- Do not document features that are not in the code.

## Deployment

- **Environments:** local, and whatever host already runs the `Dockerfile`. Do not invent staging, Kubernetes, Nixpacks, or Railway config files (`railway.toml` / `Procfile` were removed; ADR 0006).
- **How:** `Dockerfile` (`python:3.11-slim-bookworm`, non-root UID 1000, `CMD ["python", "src/bot.py"]`). Deploy a **tag**, not a dirty tree. `deploy.bat` / `deploy.sh` are personal (gitignored) — do not commit or rewrite them with IPs/keys.
- **Smoke after land:** `curl http://localhost:8080` (or the host `$PORT`) → `OK`; Telegram `/start` and `/check AAPL` if that channel is enabled.
- **Rollback:** previous image/tag. Health `GET /` is unauthenticated `OK`.
- **Do not:** reintroduce Playwright into the image; do not open `/screen` without `SCREEN_API_TOKEN`.

## Hard Rules

### House

**Do**

- Follow the personal engineering playbook (work type → checklist). Full method: `D:\projects\guide` (`00-start-here.md`).
- Write tests from the human's stated acceptance / bug / current behavior only, before implementation.
- Do not rewrite tests to match the implementation. Expected values come from the Issue, not from running the current code.
- Keep diffs on-Issue. New dependencies need an explicit why, pin, and license.

**Don't**

- Don't invent requirements, extra features, or extra files.
- Don't commit to `main` or create `develop`.
- Don't add frameworks or services not listed in Tech Stack.

### This repo

- **Secrets:** `.env` is gitignored; template is `.env.example`. Never commit `TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY`, Slack tokens, `SCREEN_API_TOKEN`, provider API keys, or `data/musaffa_session.json`. Mute httpx logs: Telegram URLs contain the bot token.
- **Security / product policy:** Private single-user install; installer owns provider ToS/quotas. Default install stays free. Daleel is calculation without a Sharia board — keep the UI caveat. `GET /screen` is 403 until `SCREEN_API_TOKEN` is set; auth is `Authorization: Bearer …` (`hmac.compare_digest`). `MAX_TICKERS_PER_REQUEST = 25`.
- **Prohibited libraries / patterns:** Do not reintroduce Playwright/Chromium. Do not hard-wire providers, Gemini, or Telegram in orchestration.
- **Behavioral:** Vote only confirmed verdicts; unique majority wins; any top-count tie → `NOT_HALAL`; one confirmed result is provisional. Operational failures never vote and are not cached as compliance except `NOT_COVERED`. Gemini reviewer may only review bounded `PARSE_ERROR` text — no browsing, no invented evidence. Quota on one provider must not block others. Duplicate `source_name` is a hard error. `CACHE_SCHEMA_VERSION = 3`. `.env.example` blanks Gemini plugins (token-free default); `config.py` still defaults those plugin strings on if the env keys are omitted — treat `.env.example` as the intended free-profile docs.

## Common Commands

```text
INSTALL: python -m venv venv && venv\Scripts\activate && pip install -r requirements.txt
BUILD:   N/A
LINT:    pip install ruff && ruff check .
TEST:    pytest
RUN:     python src/bot.py
```

Copy `.env.example` → `.env` and fill tokens before `RUN`. CI also uses `pytest -o asyncio_mode=auto`.

- **CI:** GitHub Actions `ci` on `pull_request` and `push` to `main`: Python 3.12, `pip install -r requirements.txt`, `pip install ruff pytest pytest-asyncio`, `ruff check .`, `pytest -o asyncio_mode=auto` with dummy `GEMINI_API_KEY` / `TELEGRAM_BOT_TOKEN` (`.github/workflows/ci.yml`)
- **Manual verification:** Set `TELEGRAM_BOT_TOKEN` in `.env`, run `python src/bot.py`, `/start` and `/check AAPL` in Telegram; `curl http://localhost:8080` → `OK`. Slack needs `SLACK_BOT_TOKEN` + `SLACK_APP_TOKEN` and `channels.slack:SlackChannel` appended. `/screen` needs `SCREEN_API_TOKEN`

## Context Pointers

- **Playbook:** `D:\projects\guide` — work type → checklist; start at `00-start-here.md`
- **Git recipes:** `D:\projects\guide\git\00-where-am-i.md` · `branching.md` · `commits.md` · `pull-requests.md` · `tags.md`
- **Issues / PRs (house):** `D:\projects\guide\method\tickets.md` · `method/pr-ci.md` · `templates/ISSUE.md` · `templates/PR.md` · `templates/requirements-*.md`
- **Issues / PRs (this repo):** `.github/ISSUE_TEMPLATE/ISSUE.md` · `.github/PULL_REQUEST_TEMPLATE.md`
- **Language pack / style:** `D:\projects\guide\language-packs\python.md` · `D:\projects\guide\style\python-style.md`
- **Domain / architecture:** `docs/adr/0008-modular-multi-provider-screening.md` — plugins and vote. `src/resolver.py` · `src/scrapers/base.py` · `src/plugins.py`
- **Operator docs:** `README.md` · `.env.example`
- **Other:** `docs/adr/0003-playwright-to-httpx.md` — no Playwright · `docs/adr/0006-dockerfile-healthcheck.md` — health / `$PORT` · `docs/adr/0007-github-flow-on-main.md` — why `develop` is gone · `docs/adr/0002-dual-source-conservative-resolver.md` — superseded
