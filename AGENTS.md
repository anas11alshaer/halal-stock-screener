# AGENTS.md

## Project

- **Name:** halal-stock-screener
- **Purpose (one sentence):** Telegram bot that checks whether stocks/ETFs are Shariah-compliant by scraping Musaffa and Zoya and resolving conflicts conservatively.
- **Kind:** service — Python Telegram service, no firmware/hardware

## Stack

- **Languages:** Python 3.12 (service only)
- **Language pack(s) to follow:** python
- **Important versions:** Python 3.12, python-telegram-bot 21+, httpx 0.27+, yfinance 0.2+, google-genai 1.0+, pytest 8 / pytest-asyncio 0.24, ruff 0.9

## How to build / test / flash

```text
pip install -r requirements.txt          # or: pip install -e ".[dev]" if pyproject.toml present
pytest                                   # all tests
ruff check . && ruff format --check .    # lint (when ruff configured)
python src/bot.py                        # run bot locally (needs .env)
```

- **CI:** GitHub Actions `ci` runs on `pull_request` and `push` to `main`: `ruff check .` + `pytest` (see `.github/workflows/ci.yml`)
- **Manual verification:** Set `TELEGRAM_BOT_TOKEN` in `.env`, run `python src/bot.py`, then `/start` and `/check AAPL` in Telegram; also `curl http://localhost:8080` for health check (returns `OK`)

## Hardware risk

- **Applies?** no
- **If yes:** N/A — this is a hosted service, no flashing or probe.

## Do

- Follow the personal engineering playbook (work type → checklist). Full method: `D:\projects\guide`
- Write tests from the human's stated acceptance / bug / current behavior only.
- Keep diffs on-Issue. New dependencies need an explicit why, pin, and license.

## Don't

- Don't invent requirements, extra features, or extra files.
- Don't commit to `main` or create `develop`.
- Don't guess pin numbers, voltages, clocks, or fuse/lock bits.
- Don't add frameworks or services not in this file.

## Repo-specific

- Entry: `src/bot.py` (Telegram handlers, health server on `$PORT`)
- Scrapers: `src/scrapers/musaffa.py`, `src/scrapers/zoya.py` via `src/scrapers/base.py` (`BaseScraper`, `ComplianceStatus`)
- Orchestration: `src/screener.py` → `src/resolver.py` → `src/database.py` (all DB via `database.py`, cache TTL `CACHE_TTL_HOURS`)
- Config: `src/config.py` only (no `os.environ` in feature code except `bot.py:52` for `PORT`). Secrets in `.env`; non-secrets in `config/app.toml`; Shariah policy in `config/screening_policy.toml`.
- Data: `data/stock_screener.db` (gitignored, `data/*.db` + `data/oracle_cloud`), `logs/stock_screener.log` (gitignored)
- Secrets: `.env` (gitignored) — never commit `TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY`, `NVIDIA_API_KEY`, or `SEC_CONTACT_EMAIL`; template is `.env.example`
- Legacy ignored: `apps/`, `docs/` old vault, `deploy.bat` (to be removed after migration) — do not re-add
