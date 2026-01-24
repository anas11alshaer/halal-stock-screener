# Halal Stock Screener

A Telegram bot that checks whether stocks are Shariah-compliant (Halal) according to Islamic investment principles. The bot fetches compliance data from multiple sources (Musaffa and Zoya) and uses conservative conflict resolution.

## Features

- **Dual-Source Verification** - Cross-references Musaffa and Zoya for accurate compliance data
- **Conservative Conflict Resolution** - When sources disagree, the more restrictive status is used
- **Multiple Input Methods** - Send ticker symbols as text or upload images
- **Image Analysis** - AI-powered extraction of tickers from screenshots using Google Gemini
- **Batch Processing** - Check multiple tickers in a single message
- **Smart Caching** - 24-hour cache per source to improve response times
- **User History** - Track your screening history and view statistics

## Quick Start

### Prerequisites

- Python 3.11+
- Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- Google Gemini API Key (optional, for image analysis)

### Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/halal-stock-screener.git
cd halal-stock-screener

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Run the bot
python src/bot.py
```

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | - | Bot token from BotFather |
| `GEMINI_API_KEY` | No | - | Google Gemini API key for image analysis |
| `CACHE_TTL_HOURS` | No | 24 | Cache expiration time in hours |
| `LOG_LEVEL` | No | INFO | Logging level |

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
- Upload images containing ticker symbols

## Project Structure

```
halal-stock-screener/
├── src/
│   ├── bot.py             # Telegram bot handlers
│   ├── screener.py        # Orchestration logic
│   ├── scraper.py         # Musaffa scraper
│   ├── zoya_scraper.py    # Zoya scraper
│   ├── resolver.py        # Conflict resolution
│   ├── database.py        # SQLite caching & history
│   ├── image_parser.py    # Gemini image analysis
│   └── config.py          # Configuration
├── tests/
│   └── test_scraper.py
├── data/                  # SQLite database (auto-created)
├── logs/                  # Application logs (auto-created)
├── requirements.txt
├── Procfile              # Railway deployment
├── railway.toml
└── nixpacks.toml
```

## Compliance Status

| Status | Description |
|--------|-------------|
| **Halal** | Shariah-compliant according to both sources |
| **Not Halal** | Does not meet Shariah criteria |
| **Doubtful** | Uncertain compliance status |
| **Not Covered** | Stock not in database |

### Conflict Resolution

When Musaffa and Zoya disagree, the bot uses conservative resolution:

```
Priority: Not Halal > Doubtful > Halal > Not Covered > Error
```

## Deployment

### Railway (Recommended)

1. Push to GitHub
2. Connect repository to [Railway](https://railway.app)
3. Add environment variables in Railway dashboard
4. Deploy automatically

### Manual Deployment

```bash
# Using the Procfile
python src/bot.py
```

## Data Sources

- [Musaffa](https://musaffa.com) - Shariah compliance screening platform
- [Zoya Finance](https://zoya.finance) - Halal investing app with compliance data

## License

MIT License - See [LICENSE](LICENSE) for details.
