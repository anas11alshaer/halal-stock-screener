# halal-stock-screener

Telegram bot that checks whether stocks/ETFs are Shariah-compliant by scraping Musaffa and Zoya and resolving conflicts conservatively.

## Status

usable — current version `v0.2.1` (main at `da0cf2e`)

## What you need

- Python 3.12, pip
- Telegram Bot Token (from @BotFather), optional Gemini API key for image parsing
- OS: Linux / WSL / Windows with Python
- Versions: `python-telegram-bot>=21.0`, `httpx>=0.27.0`, `yfinance>=0.2.0`, `google-genai>=1.0.0`, `pytest>=8.0.0`

## Clone, build, test

```text
git clone https://github.com/anas11alshaer/halal-stock-screener.git
cd halal-stock-screener
python -m venv venv
venv\Scripts\activate        # Windows; source venv/bin/activate on Linux
pip install -r requirements.txt
cp .env.example .env         # fill TELEGRAM_BOT_TOKEN, GEMINI_API_KEY
pytest
```

## Flash / run

```text
python src/bot.py
```

If this can brick a board, recovery is: N/A — hosted service, no flashing.

Health check: `curl http://localhost:8080` → `OK` (threaded server on `$PORT`).

Bot commands: `/start`, `/help`, `/check AAPL MSFT`, `/history`, `/stats`; or send tickers as text, or upload portfolio screenshot.

## Layout

```
halal-stock-screener/
├── src/
│   ├── bot.py               # Entry point — Telegram handlers, commands
│   ├── screener.py          # Scraper registry, screening orchestration
│   ├── resolver.py          # Conflict resolution logic across sources
│   ├── database.py          # All DB tables, queries, and cache logic
│   ├── image_parser.py      # Gemini image analysis
│   ├── config.py            # Constants and config (never os.environ in feature code)
│   └── scrapers/
│       ├── base.py          # BaseScraper: retry, ETF detection, ComplianceStatus enum
│       ├── musaffa.py
│       └── zoya.py
├── tests/
│   ├── test_scraper.py
│   └── test_image_parser.py
├── data/                    # SQLite DB (auto-created, gitignored)
├── logs/                    # Application logs (auto-created, gitignored)
├── docs/                    # docs/adr/ decision records
├── .github/
│   ├── workflows/ci.yml
│   ├── PULL_REQUEST_TEMPLATE.md
│   └── ISSUE_TEMPLATE/
├── requirements.txt
├── .env.example
└── AGENTS.md                # AI development guide
```

Follows `language-packs/python.md` (ruff + pytest). See `AGENTS.md` for stack.

## Work method

House playbook: `D:\projects\guide`. Work types start at `00-start-here.md`. This repo's agent file is `AGENTS.md`.

## License

MIT — see LICENSE (to be regenerated) for details.
