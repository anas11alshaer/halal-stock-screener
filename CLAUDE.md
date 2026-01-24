# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Telegram bot that checks whether stocks are Shariah-compliant (Halal) by scraping compliance data from multiple sources (Musaffa.com and Zoya). Users can send ticker symbols via text or images, and the bot returns the compliance status with conflict resolution when sources disagree.

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

**Run tests:**
```bash
pytest tests/
python tests/test_scraper.py
python tests/test_image_parser.py
```

## Architecture

The application follows a layered architecture with clear separation of concerns:

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
3. **scrapers/** package uses Playwright to render JavaScript-heavy pages:
   - `musaffa.py`: Scrapes Musaffa.com
   - `zoya.py`: Scrapes Zoya
   - `base.py`: Shared base class, ComplianceStatus enum, ScreeningResult dataclass
4. **database.py** provides three tables:
   - `cache`: Ticker screening results (per source)
   - `checks`: User check history
   - `image_cache`: Extracted tickers by image hash

### Key Classes

- `StockScreener` (screener.py): Main orchestrator, coordinates caching, scraping, and history
- `MusaffaScraper` / `ZoyaScraper` (scrapers/): Playwright-based scrapers with retry logic
- `ImageParser` (image_parser.py): Gemini API integration with caching and retry logic
- `TickerCache` / `CheckHistory` / `ImageCache` (database.py): SQLite data access layer
- `resolve_compliance` (resolver.py): Resolves conflicts between multiple screening sources

### Compliance Status Enum

Defined in `scrapers/base.py` as `ComplianceStatus`: HALAL, NOT_HALAL, DOUBTFUL, NOT_COVERED, ERROR

### Image Parser Features

- Uses `gemini-2.0-flash-lite` model for fast responses
- SHA-256 image hashing with 24-hour cache (avoids duplicate API calls)
- Async rate limiting with `asyncio.Lock`
- Retry logic with exponential backoff (3 retries: 1s, 2s, 4s)
- Downloads medium-resolution photos for faster processing

## Configuration

Environment variables loaded from `.env` via python-dotenv:

- `TELEGRAM_BOT_TOKEN` (required): Bot token from BotFather
- `GEMINI_API_KEY` (optional): For image analysis feature
- `CACHE_TTL_HOURS`: Cache expiration (default: 24)
- `LOG_LEVEL`: Logging verbosity (default: INFO)

Path constants are defined in `config.py` and automatically create `data/` and `logs/` directories.

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
    ├── base.py         # Base class, enums, dataclasses
    ├── musaffa.py      # Musaffa.com scraper
    └── zoya.py         # Zoya scraper
tests/
├── test_image_parser.py  # Image parser and cache tests
└── test_scraper.py       # Scraper tests
```
