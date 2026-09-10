# halal-stock-screener

Private Telegram service that resolves stock/ETF names and tickers, asks configurable
Shariah-screening providers, and shows every verdict, failure, and evidence source.

## Status

usable — current version `v0.2.1` (main at `da0cf2e`)

## What you need

- Python 3.12, pip
- Telegram Bot Token (from @BotFather), optional Gemini API key for image parsing
  and exceptional evidence review
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

Bot commands: `/start`, `/help`, `/check AAPL MSFT`, `/check Apple`, `/history`,
`/stats`; or send a ticker, company/fund name, or portfolio screenshot.

## Free provider profile

The default profile makes no paid API calls:

| Provider | Stocks | ETFs | Access |
| --- | --- | --- | --- |
| Musaffa | Public pages | Public pages | Enabled |
| Zoya | Public pages | Unsupported publicly | Enabled for stocks |
| Daleel | Free REST API | Free REST API | Enabled; optional free key |
| Halal Terminal | Free-key API | Free-key API | Optional; quota limited |
| HalalScreener | Keyed API | Keyed API | Disabled; permission/plan required |

Daleel is an auditable calculation without a Sharia board; the bot displays that
caveat. Third-party access terms can change. This project is designed for private,
single-user installations; each installer is responsible for complying with provider
terms and quotas.

## Plugin configuration

The screening core does not import concrete providers, Gemini, or Telegram. Components
are selected by `module:Class` paths in `.env`:

```text
SCREENING_PROVIDER_PLUGINS=scrapers.musaffa:MusaffaScraper,scrapers.zoya:ZoyaScraper,scrapers.daleel:DaleelProvider
EVIDENCE_REVIEWER_PLUGIN=
IMAGE_EXTRACTOR_PLUGIN=
DELIVERY_CHANNEL_PLUGINS=channels.telegram:TelegramChannel
```

Reorder, remove, or replace a path without changing orchestration, voting, caching, or
rendering code. Optional keyed providers are:

```text
scrapers.halal_terminal:HalalTerminalProvider
scrapers.halal_screener:HalalScreenerProvider
```

The default installation remains free. Adding a keyed provider does not guarantee that
provider grants free production or display rights; check its current terms first.
To enable exceptional Gemini evidence review, set `GEMINI_API_KEY` and change
`EVIDENCE_REVIEWER_PLUGIN` to `reviewer:GeminiEvidenceReviewer`.
To enable image ticker extraction, set `IMAGE_EXTRACTOR_PLUGIN` to
`image_parser:GeminiImageExtractor`. A replacement extractor must extend
`image_extractors.base.ImageExtractor` and implement `extract_tickers(image_data)`.

`DELIVERY_CHANNEL_PLUGINS` accepts a comma-separated list; each channel runs in its
own thread. To serve Slack alongside Telegram, append `channels.slack:SlackChannel`
and set `SLACK_BOT_TOKEN` (`xoxb-…`) and `SLACK_APP_TOKEN` (`xapp-…`). The Slack
channel uses Socket Mode, so no public URL is required. It answers `@bot AAPL`
mentions, DM messages and image uploads, and `/check`, `/history`, `/stats` slash
commands (register those command names in the Slack app settings).

Confirmed providers vote by status. A unique majority wins, any tied highest vote is
reported as `NOT_HALAL`, and one confirmed result is visibly marked provisional.

## Layout

```
halal-stock-screener/
├── src/
│   ├── bot.py               # Entry point — Telegram handlers, commands
│   ├── screener.py          # Scraper registry, screening orchestration
│   ├── resolver.py          # Provider-neutral majority/tie policy
│   ├── security_resolver.py # Yahoo name, ticker, and asset-type resolution
│   ├── plugins.py           # Provider/reviewer/channel loading
│   ├── reviewer.py          # Bounded Gemini evidence fallback
│   ├── channels/            # Replaceable delivery transports
│   ├── database.py          # All DB tables, queries, and cache logic
│   ├── image_parser.py      # Gemini image analysis
│   ├── config.py            # Constants and config (never os.environ in feature code)
│   └── scrapers/
│       ├── base.py          # Provider contract, result model, retry transport
│       ├── musaffa.py
│       ├── zoya.py
│       ├── daleel.py
│       ├── halal_terminal.py
│       └── halal_screener.py
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
