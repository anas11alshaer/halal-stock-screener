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

# Slack configuration (Socket Mode: websocket connection, no public URL needed)
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN", "")

# Gemini API configuration (single key)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Bearer token for the internal GET /screen endpoint; the endpoint is disabled when empty.
SCREEN_API_TOKEN = os.getenv("SCREEN_API_TOKEN", "")

# Cache configuration
CACHE_TTL_HOURS = int(os.getenv("CACHE_TTL_HOURS", "24"))
NOT_COVERED_CACHE_TTL_HOURS = int(os.getenv("NOT_COVERED_CACHE_TTL_HOURS", "1"))
CACHE_SCHEMA_VERSION = 3

# Database configuration
DATABASE_PATH = DATA_DIR / "stock_screener.db"

# Scraping configuration
MUSAFFA_BASE_URL = "https://musaffa.com/stock"
ZOYA_BASE_URL = "https://zoya.finance/stocks"
DALEEL_BASE_URL = os.getenv("DALEEL_BASE_URL", "https://daleel.o11r.com")
HALAL_TERMINAL_BASE_URL = os.getenv("HALAL_TERMINAL_BASE_URL", "https://api.halalterminal.com")
HALAL_SCREENER_BASE_URL = os.getenv("HALAL_SCREENER_BASE_URL", "https://halalscreener.app/api/v1")
HALAL_TERMINAL_API_KEY = os.getenv("HALAL_TERMINAL_API_KEY", "")
HALAL_SCREENER_API_KEY = os.getenv("HALAL_SCREENER_API_KEY", "")
DALEEL_API_KEY = os.getenv("DALEEL_API_KEY", "")
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "30"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
MAX_TICKERS_PER_REQUEST = 25

# Import paths make providers replaceable without changing orchestration code.
DEFAULT_SCREENING_PROVIDERS = (
    "scrapers.musaffa:MusaffaScraper,scrapers.zoya:ZoyaScraper,scrapers.daleel:DaleelProvider"
)
SCREENING_PROVIDER_PLUGINS = [
    value.strip()
    for value in os.getenv("SCREENING_PROVIDER_PLUGINS", DEFAULT_SCREENING_PROVIDERS).split(",")
    if value.strip()
]
EVIDENCE_REVIEWER_PLUGIN = os.getenv("EVIDENCE_REVIEWER_PLUGIN", "reviewer:GeminiEvidenceReviewer")
DELIVERY_CHANNEL_PLUGINS = [
    value.strip()
    for value in os.getenv(
        "DELIVERY_CHANNEL_PLUGINS",
        os.getenv("DELIVERY_CHANNEL_PLUGIN", "channels.telegram:TelegramChannel"),
    ).split(",")
    if value.strip()
]
IMAGE_EXTRACTOR_PLUGIN = os.getenv("IMAGE_EXTRACTOR_PLUGIN", "image_parser:GeminiImageExtractor")
PRICE_PROVIDER_PLUGIN = os.getenv("PRICE_PROVIDER_PLUGIN", "prices.yahoo:YahooPriceProvider")
# Kill switch for the /price command; the provider stays so re-enable is config-only.
PRICE_COMMAND_ENABLED = (
    os.getenv("PRICE_COMMAND_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")
)

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
