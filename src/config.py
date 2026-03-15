"""Configuration module for Stock Screener."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Base directories
BASE_DIR = Path(__file__).parent.parent  # Project root
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"

# Ensure directories exist
DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# Telegram configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Gemini API configuration (single key)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Cache configuration
CACHE_TTL_HOURS = int(os.getenv("CACHE_TTL_HOURS", "24"))

# Database configuration
DATABASE_PATH = DATA_DIR / "stock_screener.db"

# Scraping configuration
MUSAFFA_BASE_URL = "https://musaffa.com/stock"
ZOYA_BASE_URL = "https://zoya.finance/stocks"
REQUEST_TIMEOUT = 30  # seconds
MAX_RETRIES = 3
MAX_TICKERS_PER_REQUEST = 25

# Gemini model rotation (first model has highest rate limits)
# Each request cycles to the next model; counter resets daily
GEMINI_MODELS = [
    "gemini-3.1-flash-lite-preview",
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]

# Logging configuration
LOG_FILE = LOGS_DIR / "stock_screener.log"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
