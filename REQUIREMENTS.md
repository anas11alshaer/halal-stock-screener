# Halal Stock Screener Bot — Product Requirements

## Product Overview

A Telegram bot that checks whether stocks and ETFs are Shariah-compliant (Halal) by scraping compliance data from Musaffa.com and Zoya.finance. Users send ticker symbols via text messages, bot commands, or images containing tickers. The bot returns compliance status with conservative conflict resolution when sources disagree (most restrictive verdict wins).

## Features

### F-001: Text Ticker Screening

**Description:** Users send one or more stock ticker symbols as a plain text message and receive Shariah compliance results.

**Acceptance Criteria:**
- [ ] Sending a single uppercase ticker (e.g., `AAPL`) returns a detailed compliance result with company name, verdict, and per-source breakdown
- [ ] Sending multiple tickers separated by spaces (e.g., `AAPL MSFT GOOGL`) returns a compact multi-ticker result
- [ ] Sending a cashtag (e.g., `$AAPL`) correctly extracts and screens the ticker
- [ ] Tickers are case-insensitive (e.g., `aapl` works the same as `AAPL`)
- [ ] Common false positives (e.g., `CEO`, `IPO`, `USD`) are filtered out and not screened
- [ ] If no valid tickers are found, the bot responds with a helpful message suggesting the correct format

**How to Test:**
- Send `AAPL` to the bot — expect a detailed single-ticker result with sources
- Send `AAPL MSFT GOOGL` — expect a compact multi-ticker result
- Send `$TSLA` — expect Tesla compliance result
- Send `hello world` — expect "No tickers found" message with usage hint

### F-002: Command-Based Screening

**Description:** Users can use the `/check` command followed by ticker symbols to screen stocks.

**Acceptance Criteria:**
- [ ] `/check AAPL` returns the compliance result for AAPL
- [ ] `/check AAPL MSFT` returns results for both tickers
- [ ] `/check` with no arguments returns a usage hint

**How to Test:**
- Send `/check AAPL` — expect compliance result
- Send `/check AAPL MSFT` — expect multi-ticker result
- Send `/check` alone — expect usage message: `Usage: /check AAPL MSFT`

### F-003: Image Ticker Extraction

**Description:** Users send an image containing stock tickers (e.g., a screenshot of a watchlist), and the bot extracts tickers using Gemini AI and screens them.

**Acceptance Criteria:**
- [ ] Sending a photo with visible ticker symbols extracts and screens those tickers
- [ ] Results from image extraction are cached by image hash (SHA-256) for 24 hours
- [ ] If no tickers are found in the image, the bot responds with a clear message
- [ ] If the Gemini API key is not configured, the bot responds with a message indicating image analysis is unavailable
- [ ] If all Gemini models hit their quota, the bot responds with a quota exceeded message suggesting text input instead

**How to Test:**
- Send an image containing stock ticker symbols — expect extracted tickers to be screened
- Send an image with no tickers — expect "No stock tickers found in the image" message

### F-004: Multi-Source Compliance with Conflict Resolution

**Description:** Each ticker is screened against both Musaffa.com and Zoya.finance in parallel. When sources disagree, the most restrictive verdict is used (conservative approach).

**Acceptance Criteria:**
- [ ] Both Musaffa and Zoya are queried for each stock ticker
- [ ] When both sources agree, the agreed status is returned
- [ ] When sources conflict, the more restrictive status wins (NOT_HALAL > DOUBTFUL > HALAL)
- [ ] When one source returns NOT_COVERED or ERROR, the other source's result is used
- [ ] When both sources return NOT_COVERED, the final result is NOT_COVERED
- [ ] When both sources return ERROR, the final result is ERROR
- [ ] Single-ticker results show a per-source breakdown (Musaffa and Zoya verdicts)
- [ ] Conflicts are flagged with a warning message: "Sources disagree — using more restrictive result"

**How to Test:**
- Send a well-known halal ticker (e.g., `AAPL`) — expect both sources to agree
- Send a ticker not covered by Zoya but covered by Musaffa — expect Musaffa result used
- Check the per-source breakdown in single-ticker responses

### F-005: ETF Support

**Description:** ETFs are detected via yfinance and screened by Musaffa only, since Zoya does not cover ETFs.

**Acceptance Criteria:**
- [ ] ETFs are automatically detected (not manually configured)
- [ ] ETF tickers are screened on Musaffa using the `/etf/{TICKER}/` URL path
- [ ] Zoya immediately returns NOT_COVERED for ETFs (no HTTP request made)
- [ ] Single-ticker ETF results display an "ETF" label and a note: "Screened by Musaffa only — Zoya does not cover ETFs"
- [ ] Multi-ticker results show "ETF" label next to ETF tickers

**How to Test:**
- Send `SPY` (a well-known ETF) — expect ETF label and Musaffa-only result
- Send `QQQ` — expect ETF label and Musaffa-only note

### F-006: Result Caching

**Description:** Screening results are cached in SQLite with a configurable TTL (default 24 hours) to avoid redundant HTTP requests.

**Acceptance Criteria:**
- [ ] First request for a ticker fetches from both sources via HTTP
- [ ] Subsequent requests within the TTL return cached results without HTTP calls
- [ ] Cache is per-ticker per-source (Musaffa and Zoya cached independently)
- [ ] ERROR results are not cached (only successful results are stored)
- [ ] Expired cache entries are automatically cleaned up on bot startup
- [ ] Cache TTL is configurable via the `CACHE_TTL_HOURS` environment variable

**How to Test:**
- Send a ticker, then send the same ticker again — second response should be faster (cached)
- Check logs for "Cache hit" messages on the second request

### F-007: User History

**Description:** Users can view their recent screening checks via the `/history` command.

**Acceptance Criteria:**
- [ ] `/history` shows the 15 most recent checks for the requesting user
- [ ] Each entry shows the status emoji, ticker symbol, and date
- [ ] If the user has no history, a helpful message is shown

**How to Test:**
- Screen a few tickers, then send `/history` — expect recent checks listed
- From a new user with no checks, send `/history` — expect "No history yet" message

### F-008: User Statistics

**Description:** Users can view their screening statistics via the `/stats` command.

**Acceptance Criteria:**
- [ ] `/stats` shows total checks, unique tickers, and status breakdown
- [ ] Status breakdown only shows categories with count > 0
- [ ] If the user has no history, a helpful message is shown

**How to Test:**
- Screen several tickers, then send `/stats` — expect statistics summary
- From a new user, send `/stats` — expect "No statistics yet" message

### F-009: Start and Help Commands

**Description:** `/start` and `/help` commands display a welcome message with usage instructions.

**Acceptance Criteria:**
- [ ] `/start` shows a welcome message with bot name, usage examples, and available commands
- [ ] `/help` shows the same message as `/start`

**How to Test:**
- Send `/start` — expect welcome message with usage instructions
- Send `/help` — expect same welcome message

### F-010: Daily Gemini Model Rotation

**Description:** Image analysis cycles through multiple Gemini models round-robin per request, resetting the counter each day. If a model hits its quota, it is marked exhausted and the next available model is used.

**Acceptance Criteria:**
- [ ] Requests rotate across 4 configured Gemini models
- [ ] Model rotation counter resets at the start of each new day
- [ ] If a model returns a quota/rate-limit error (429), it is marked exhausted for the day
- [ ] Exhausted models are skipped on subsequent requests
- [ ] If all models are exhausted, a QuotaExceededError is raised with a clear message

**How to Test:**
- Send multiple images throughout the day — logs should show different models being used
- If quota is hit, subsequent image requests should use the next available model

### F-011: Batch Ticker Processing

**Description:** When screening many tickers at once, they are processed in batches of 25 to limit concurrent HTTP requests.

**Acceptance Criteria:**
- [ ] Tickers are processed in batches of `MAX_TICKERS_PER_REQUEST` (25)
- [ ] All batches are combined into a single response
- [ ] Duplicate tickers in a single request are deduplicated

**How to Test:**
- Send a message with many tickers — expect all to be screened and results combined
- Send `AAPL AAPL MSFT` — expect only AAPL and MSFT in results (no duplicate)

## Error Handling

### E-001: Scraper Timeout

**Trigger:** A source website (Musaffa or Zoya) does not respond within 30 seconds.
**Expected Behavior:** The ticker returns an ERROR status for that source. If the other source succeeds, its result is used. Retry logic attempts up to 3 times with exponential backoff (1s, 2s, 4s).

### E-002: Scraper HTTP Error

**Trigger:** A source website returns an HTTP error (4xx/5xx).
**Expected Behavior:** HTTP 404 returns NOT_COVERED. Other 4xx/5xx errors return ERROR for that source. Retry logic applies for transient errors.

### E-003: Both Sources Fail

**Trigger:** Both Musaffa and Zoya return ERROR for a ticker.
**Expected Behavior:** The final result is ERROR with the message "Both sources returned errors."

### E-004: Gemini Quota Exhausted

**Trigger:** All configured Gemini models have hit their daily rate limits.
**Expected Behavior:** The bot responds: "Image analysis quota exceeded. Please try again later or send ticker symbols as text."

### E-005: Gemini Not Configured

**Trigger:** No `GEMINI_API_KEY` is set in the environment.
**Expected Behavior:** The bot responds: "Image analysis is not available. Please set GEMINI_API_KEY." Text-based screening continues to work normally.

### E-006: General Bot Error

**Trigger:** An unhandled exception occurs during message processing.
**Expected Behavior:** The bot responds: "Something went wrong. Please try again." The error is logged.

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | — | Bot token from @BotFather |
| `GEMINI_API_KEY` | No | — | Google Gemini API key for image analysis |
| `CACHE_TTL_HOURS` | No | `24` | Cache expiration in hours |
| `LOG_LEVEL` | No | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |

## Constraints

- All I/O is async — the bot must never block the event loop
- HTTP scraping uses `httpx` only — no browser automation (Playwright, Selenium)
- Both sources are scraped in parallel using `asyncio.gather()`
- Maximum 25 tickers per batch to limit concurrent HTTP connections
- Image cache uses SHA-256 hashing with 24-hour TTL
- Gemini model rotation: 4 models, counter resets daily, exhausted models skipped
- SQLite database stored at `data/stock_screener.db`
- Logs written to `logs/stock_screener.log` and stdout
