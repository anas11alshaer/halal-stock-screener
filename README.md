# Halal Stock Screener

A Telegram bot that checks whether stocks and ETFs are Shariah-compliant (Halal) by cross-referencing two independent screening sources — [Musaffa](https://musaffa.com) and [Zoya](https://zoya.finance). When sources disagree, the bot applies a conservative conflict resolution rule so that uncertain stocks are never silently passed as compliant.

## Features

- **Dual-source verification** — Cross-references Musaffa and Zoya for reliable results
- **ETF support** — Authenticates with Musaffa to unlock ETF compliance data
- **Conservative conflict resolution** — When sources disagree, the more restrictive status wins
- **Multiple input methods** — Send ticker symbols as text or upload portfolio screenshots
- **AI image analysis** — Extracts tickers from screenshots using Google Gemini
- **Batch processing** — Check multiple tickers in a single message
- **Smart caching** — 24-hour SQLite cache per source to reduce scraping load
- **User history** — Track and review your past screening checks

## Quick Start

### Prerequisites

- Python 3.11+
- Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- Musaffa account (free — required for ETF compliance data)
- Google Gemini API key (optional, for image analysis)

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

# Configure environment
cp .env.example .env
# Edit .env with your credentials

# Run the bot
python src/bot.py
```

## Configuration

Copy `.env.example` to `.env` and fill in the values:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | — | Bot token from BotFather |
| `MUSAFFA_EMAIL` | Recommended | — | Musaffa account email (unlocks ETF data) |
| `MUSAFFA_PASSWORD` | Recommended | — | Musaffa account password |
| `GEMINI_API_KEY` | No | — | Google Gemini key for image analysis |
| `GEMINI_API_KEYS` | No | — | Comma-separated Gemini keys for higher quota |
| `CACHE_TTL_HOURS` | No | `24` | Cache expiration in hours |
| `LOG_LEVEL` | No | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

> **ETF note:** Without Musaffa credentials, ETF pages return a locked status and the bot reports `Not Covered`. A free Musaffa account is sufficient.

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
- Upload an image or screenshot containing ticker symbols

## Compliance Statuses

| Status | Meaning |
|--------|---------|
| **Halal** | Shariah-compliant per all sources checked |
| **Not Halal** | Does not meet Shariah criteria |
| **Doubtful** | Uncertain compliance — treat with caution |
| **Not Covered** | Not found in the screening database |

### Conflict Resolution

When Musaffa and Zoya return different results, the bot picks the most conservative status:

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
│   ├── config.py           # Configuration & path constants
│   └── scrapers/
│       ├── base.py         # BaseScraper ABC, ComplianceStatus enum, shared helpers
│       ├── musaffa.py      # Musaffa scraper (stocks + ETFs, session auth)
│       └── zoya.py         # Zoya scraper (stocks only)
├── tests/
│   ├── test_scraper.py         # Scraper & resolver integration tests
│   └── test_image_parser.py    # Image parser unit & cache tests
├── data/                   # SQLite DB (auto-created, git-ignored)
├── logs/                   # Application logs (auto-created, git-ignored)
├── .env.example            # Environment variable template
├── requirements.txt        # Python dependencies
├── Dockerfile              # Container image definition
└── CLAUDE.md               # AI development guide
```

## Data Sources

- [Musaffa](https://musaffa.com) — Shariah compliance screening for stocks and ETFs
- [Zoya Finance](https://zoya.finance) — Halal investing app, stocks only

## Deployment

### Docker

```bash
docker build -t halal-screener .
docker run --env-file .env halal-screener
```

### Manual

```bash
python src/bot.py
```

## License

MIT License — see [LICENSE](LICENSE) for details.
