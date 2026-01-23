"""Configuration module for Stock Screener."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Base directories
BASE_DIR = Path(__file__).parent.parent  # Project root
SRC_DIR = BASE_DIR / "src"
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
TESTS_DIR = BASE_DIR / "tests"

# Ensure directories exist
DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# Telegram configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Gemini API configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Cache configuration
CACHE_TTL_HOURS = int(os.getenv("CACHE_TTL_HOURS", "24"))

# Database configuration
DATABASE_PATH = DATA_DIR / "stock_screener.db"

# Scraping configuration
MUSAFFA_BASE_URL = "https://musaffa.com/stock"
REQUEST_TIMEOUT = 30  # seconds
MAX_RETRIES = 3
RETRY_DELAY = 1  # seconds (base for exponential backoff)

# Logging configuration
LOG_FILE = LOGS_DIR / "stock_screener.log"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
