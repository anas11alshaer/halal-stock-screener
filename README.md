# Halal Stock Screener

A Telegram bot that checks whether stocks and ETFs are Shariah-compliant (Halal)
according to Islamic investment principles. The bot cross-references two independent
sources (Musaffa and Zoya) and applies conservative conflict resolution when they disagree.

## Features

- **Dual-Source Verification** — Cross-references Musaffa and Zoya for reliable compliance data
- **ETF Support** — Authenticates with Musaffa to unlock ETF compliance status
- **Conservative Conflict Resolution** — When sources disagree the more restrictive status wins
- **Multiple Input Methods** — Send ticker symbols as text or upload portfolio screenshots
- **AI Image Analysis** — Extracts tickers from images using Google Gemini
- **Batch Processing** — Check multiple tickers in a single message
- **Smart Caching** — 24-hour SQLite cache per source to reduce scraping load
- **User History** — Track screening history and view statistics per user

## Quick Start

### Prerequisites

- Python 3.11+
- Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- Musaffa account (free — required for ETF compliance data)
- Google Gemini API Key (optional, for image analysis)

### Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/halal-stock-screener.git
cd halal-stock-screener

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Configure environment
cp .env.example .env
# Edit .env with your credentials (see Configuration below)

# Run the bot
python src/bot.py
```

## Configuration

Copy `.env.example` to `.env` and fill in the values:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | — | Bot token from BotFather |
| `MUSAFFA_EMAIL` | Recommended | — | Musaffa account email — unlocks ETF compliance data |
| `MUSAFFA_PASSWORD` | Recommended | — | Musaffa account password |
| `GEMINI_API_KEY` | No | — | Google Gemini key for image analysis |
| `GEMINI_API_KEYS` | No | — | Comma-separated Gemini keys for higher quota |
| `CACHE_TTL_HOURS` | No | `24` | Cache expiration in hours |
| `LOG_LEVEL` | No | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

> **ETF note:** Without Musaffa credentials, ETF pages return a locked status and the
> bot will report `Not Covered`. A free Musaffa account is sufficient.

## Usage

### Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message and instructions |
| `/help` | Show usage help |
| `/check AAPL` | Check a specific ticker |
| `/history` | View your last 15 checks |
| `/stats` | View your screening statistics |

### Direct Input

- Single ticker: `AAPL`
- With cashtag: `$MSFT`
- Multiple tickers: `AAPL GOOGL TSLA`
- Upload an image containing ticker symbols

## Compliance Statuses

| Status | Meaning |
|--------|---------|
| **Halal** | Shariah-compliant per all sources checked |
| **Not Halal** | Does not meet Shariah criteria |
| **Doubtful** | Uncertain compliance — treat with caution |
| **Not Covered** | Not found in the screening database |

### Conflict Resolution

When Musaffa and Zoya return different results the bot picks the most conservative:

```
Not Halal > Doubtful > Halal > Not Covered
```

## Project Structure

```
halal-stock-screener/
├── src/
│   ├── bot.py              # Telegram bot entry point & handlers
│   ├── screener.py         # Orchestration: caching, scraping, history
│   ├── resolver.py         # Multi-source conflict resolution
│   ├── database.py         # SQLite cache, check history, image cache
│   ├── image_parser.py     # Gemini AI image analysis (multi-key)
│   ├── config.py           # All configuration & path constants
│   └── scrapers/
│       ├── base.py         # ComplianceStatus enum, ScreeningResult, helpers
│       ├── musaffa.py      # Musaffa scraper with login + session reuse
│       └── zoya.py         # Zoya scraper
├── tests/
│   ├── test_scraper.py         # Scraper + resolver integration tests
│   ├── test_etf_extraction.py  # ETF authenticated extraction diagnostic
│   └── test_image_parser.py    # Image parser & cache tests
├── data/                   # SQLite DB (auto-created, git-ignored)
├── logs/                   # Application logs (auto-created, git-ignored)
├── .env.example            # Environment variable template
├── requirements.txt
└── Dockerfile
```

## Data Sources

- [Musaffa](https://musaffa.com) — Shariah compliance screening, supports stocks and ETFs
- [Zoya Finance](https://zoya.finance) — Halal investing app, stocks only

## Deployment

### Docker

```bash
docker build -t halal-screener .
docker run --env-file .env halal-screener
```

### Railway

1. Push to GitHub
2. Connect the repository to [Railway](https://railway.app)
3. Add environment variables in the Railway dashboard
4. Deploy automatically on push

### Manual

```bash
python src/bot.py
```

## License

MIT License — see [LICENSE](LICENSE) for details.
