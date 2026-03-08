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

# Gemini API configuration
# Supports multiple keys: comma-separated in GEMINI_API_KEYS or single GEMINI_API_KEY
_gemini_keys_str = os.getenv("GEMINI_API_KEYS", "")
GEMINI_API_KEYS = [k.strip() for k in _gemini_keys_str.split(",") if k.strip()]
# Fallback to single key for backwards compatibility
if not GEMINI_API_KEYS:
    _single_key = os.getenv("GEMINI_API_KEY", "")
    if _single_key:
        GEMINI_API_KEYS = [_single_key]
# Legacy single key variable (first key or empty)
GEMINI_API_KEY = GEMINI_API_KEYS[0] if GEMINI_API_KEYS else ""

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

# Gemini model configuration (ordered by preference: highest RPM first)
GEMINI_MODELS = [
    {"name": "gemini-3.1-flash-lite-preview", "rpm": 15, "rpd": 500},
    {"name": "gemini-2.5-flash-lite", "rpm": 10, "rpd": 500},
    {"name": "gemini-3-flash-preview", "rpm": 5, "rpd": 25},
    {"name": "gemini-2.5-flash", "rpm": 5, "rpd": 25},
]

# Logging configuration
LOG_FILE = LOGS_DIR / "stock_screener.log"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
