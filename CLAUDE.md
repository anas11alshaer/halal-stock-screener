# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Telegram bot that checks whether stocks are Shariah-compliant (Halal) by scraping compliance data from Musaffa.com. Users can send ticker symbols via text or images, and the bot returns the compliance status.

## Commands

**Run the bot:**
```bash
python src/bot.py
```

**Install dependencies:**
```bash
pip install -r requirements.txt
playwright install chromium
```

**Run scraper tests:**
```bash
python tests/test_scraper.py
```

## Architecture

The application follows a layered architecture with clear separation of concerns:

```
Telegram → bot.py → screener.py → scraper.py → Musaffa.com
                         ↓
              database.py (SQLite cache + history)
                         ↓
              image_parser.py (Gemini AI for images)
```

### Data Flow

1. **bot.py** receives Telegram messages (text, commands, or photos)
2. **screener.py** orchestrates the workflow:
   - Extracts tickers from text via regex or from images via Gemini
   - Checks SQLite cache first (24-hour TTL)
   - Calls scraper for uncached tickers
   - Records results in user history
3. **scraper.py** uses Playwright to render Musaffa.com's JavaScript-heavy pages and parse compliance status
4. **database.py** provides two tables: `cache` (ticker results) and `checks` (user history)

### Key Classes

- `StockScreener` (screener.py): Main orchestrator, coordinates caching, scraping, and history
- `MusaffaScraper` (scraper.py): Playwright-based scraper with retry logic
- `ImageParser` (image_parser.py): Gemini API integration for extracting tickers from images
- `TickerCache` / `CheckHistory` (database.py): SQLite data access layer

### Compliance Status Enum

Defined in `scraper.py` as `ComplianceStatus`: HALAL, NOT_HALAL, DOUBTFUL, NOT_COVERED, ERROR

## Configuration

Environment variables loaded from `.env` via python-dotenv:

- `TELEGRAM_BOT_TOKEN` (required): Bot token from BotFather
- `GEMINI_API_KEY` (optional): For image analysis feature
- `CACHE_TTL_HOURS`: Cache expiration (default: 24)
- `LOG_LEVEL`: Logging verbosity (default: INFO)

Path constants are defined in `config.py` and automatically create `data/` and `logs/` directories.
