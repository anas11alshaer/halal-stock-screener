"""Stock compliance scrapers."""

from .base import (
    AssetType,
    BaseScraper,
    ComplianceStatus,
    ResultState,
    ScreeningResult,
    Security,
    STATUS_ICON,
    STATUS_TEXT,
    get_quote_type,
)
from .musaffa import MusaffaScraper
from .zoya import ZoyaScraper
from .daleel import DaleelProvider

__all__ = [
    "BaseScraper",
    "AssetType",
    "ComplianceStatus",
    "ScreeningResult",
    "Security",
    "ResultState",
    "STATUS_ICON",
    "STATUS_TEXT",
    "MusaffaScraper",
    "ZoyaScraper",
    "DaleelProvider",
    "get_quote_type",
]
