"""Single config boundary.

Secrets come from .env (never committed). Non-secrets come from
config/app.toml. Shariah signals come from config/screening_policy.toml
via policy.py. Feature code must not call os.environ (except bot.py PORT).
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
APP_TOML_PATH = BASE_DIR / "config" / "app.toml"
POLICY_PATH = BASE_DIR / "config" / "screening_policy.toml"
EVAL_FIXTURES_PATH = BASE_DIR / "config" / "eval_fixtures.toml"

load_dotenv(BASE_DIR / ".env")

DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

_SECRET_NAMES = frozenset(
    {
        "TELEGRAM_BOT_TOKEN",
        "GEMINI_API_KEY",
        "NVIDIA_API_KEY",
        "SEC_CONTACT_EMAIL",
        "OPENFIGI_API_KEY",
    }
)
_SECRET_TOML_KEYS = frozenset(name.lower() for name in _SECRET_NAMES)


def _secret(name: str) -> str:
    if name not in _SECRET_NAMES:
        raise KeyError(f"{name} is not a declared secret")
    return (os.getenv(name) or "").strip()


def _env_or(name: str, default: str) -> str:
    """Optional .env override for non-secret knobs; empty means use app.toml."""
    value = (os.getenv(name) or "").strip()
    return value if value else default


def _walk_keys(value: Any) -> list[str]:
    keys: list[str] = []
    if isinstance(value, dict):
        for key, inner in value.items():
            keys.append(str(key).lower())
            keys.extend(_walk_keys(inner))
    elif isinstance(value, list):
        for item in value:
            keys.extend(_walk_keys(item))
    return keys


def load_app_toml(path: Path = APP_TOML_PATH) -> dict[str, Any]:
    """Load committed non-secret settings. Refuse tables that look like secrets."""
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a TOML table")
    found = set(_walk_keys(data)) & _SECRET_TOML_KEYS
    if found:
        raise ValueError(
            f"{path} must not contain secrets {sorted(found)}; put them in .env"
        )
    return data


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    return value if isinstance(value, dict) else {}


_APP = load_app_toml()
_CACHE = _section(_APP, "cache")
_LOGGING = _section(_APP, "logging")
_HTTP = _section(_APP, "http")
_SCRAPERS = _section(_APP, "scrapers")
_GEMINI = _section(_APP, "gemini")

# Secrets — .env only
TELEGRAM_BOT_TOKEN = _secret("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = _secret("GEMINI_API_KEY")
NVIDIA_API_KEY = _secret("NVIDIA_API_KEY")
SEC_CONTACT_EMAIL = _secret("SEC_CONTACT_EMAIL")
OPENFIGI_API_KEY = _secret("OPENFIGI_API_KEY")

# Non-secrets — config/app.toml, overridable from .env
CACHE_TTL_HOURS = int(_env_or("CACHE_TTL_HOURS", str(_CACHE.get("ttl_hours", 24))))
LOG_LEVEL = _env_or("LOG_LEVEL", str(_LOGGING.get("level") or "INFO"))
REQUEST_TIMEOUT = int(_HTTP.get("request_timeout", 30))
MAX_RETRIES = int(_HTTP.get("max_retries", 3))
MAX_TICKERS_PER_REQUEST = int(_HTTP.get("max_tickers_per_request", 25))
MUSAFFA_BASE_URL = str(_SCRAPERS.get("musaffa_base_url") or "https://musaffa.com/stock")
ZOYA_BASE_URL = str(_SCRAPERS.get("zoya_base_url") or "https://zoya.finance/stocks")
GEMINI_MODELS = [str(m) for m in (_GEMINI.get("models") or [])]

DATABASE_PATH = DATA_DIR / "stock_screener.db"
LOG_FILE = LOGS_DIR / "stock_screener.log"
