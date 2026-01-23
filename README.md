# Halal Stock Screener Telegram Bot

A Telegram bot that checks whether stocks are Shariah-compliant (Halal) according to Islamic investment principles. The bot fetches compliance data from Musaffa.com and supports text-based ticker input as well as image analysis.

## Features

- **Stock Compliance Checking** - Verify if stocks are Halal, Not Halal, or Doubtful
- **Multiple Input Methods** - Send ticker symbols as text or upload images containing tickers
- **Image Analysis** - AI-powered extraction of ticker symbols from screenshots using Google Gemini
- **Batch Processing** - Check multiple tickers in a single message
- **Caching** - 24-hour cache to improve response times and reduce API calls
- **User History** - Track your screening history and view statistics
- **Rich Responses** - Formatted messages with compliance status, company names, and rankings

## Prerequisites

- Python 3.8+
- Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- Google Gemini API Key (optional, for image analysis)

## Installation

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd "Stock Screener"
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Install Playwright browsers**
   ```bash
   playwright install chromium
   ```

4. **Configure environment variables**

   Create a `.env` file in the project root:
   ```env
   TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
   GEMINI_API_KEY=your_gemini_api_key_here
   CACHE_TTL_HOURS=24
   LOG_LEVEL=INFO
   ```

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | - | Bot token from Telegram BotFather |
| `GEMINI_API_KEY` | No | - | Google Gemini API key for image analysis |
| `CACHE_TTL_HOURS` | No | 24 | Cache expiration time in hours |
| `LOG_LEVEL` | No | INFO | Logging level (DEBUG, INFO, WARNING, ERROR) |

## Usage

1. **Start the bot**
   ```bash
   python src/bot.py
   ```

2. **Interact via Telegram**

   | Command | Description |
   |---------|-------------|
   | `/start` | Display welcome message and instructions |
   | `/help` | Show usage help |
   | `/check AAPL` | Check a specific ticker |
   | `/history` | View your last 15 checked stocks |
   | `/stats` | View your screening statistics |

   You can also:
   - Send ticker symbols directly: `AAPL`, `$MSFT`, or `AAPL GOOGL TSLA`
   - Upload images containing ticker symbols for automatic extraction

## Project Structure

```
Stock Screener/
├── src/                    # Source code
│   ├── bot.py              # Telegram bot entry point and command handlers
│   ├── screener.py         # Core screening orchestration logic
│   ├── scraper.py          # Web scraper for Musaffa.com
│   ├── image_parser.py     # Image analysis and ticker extraction
│   ├── database.py         # SQLite database layer (caching & history)
│   └── config.py           # Configuration management
├── data/                   # Database files
│   └── stock_screener.db   # SQLite database (auto-generated)
├── logs/                   # Log files
│   └── stock_screener.log  # Application logs (auto-generated)
├── tests/                  # Test files
│   └── test_scraper.py     # Scraper tests
├── requirements.txt        # Python dependencies
├── .env.example            # Example environment variables
└── .env                    # Environment variables (create this)
```

## Compliance Status Types

| Status | Description |
|--------|-------------|
| HALAL | Stock is Shariah-compliant |
| NOT_HALAL | Stock does not meet Shariah compliance criteria |
| DOUBTFUL | Stock has uncertain compliance status |
| NOT_COVERED | Stock is not in the Musaffa database |
| ERROR | Failed to retrieve compliance data |

## Dependencies

- **python-telegram-bot** - Telegram bot framework
- **google-generativeai** - Google Gemini API for image analysis
- **playwright** - Browser automation for web scraping
- **python-dotenv** - Environment variable management

## Data Sources

Stock compliance data is sourced from [Musaffa.com](https://musaffa.com), a platform that provides Shariah compliance screening for stocks based on Islamic finance principles.

## License

This project is for educational and personal use.
