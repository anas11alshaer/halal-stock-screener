"""Base classes and utilities for stock scrapers."""

import shutil
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ComplianceStatus(Enum):
    """Stock compliance status."""

    HALAL = "HALAL"
    NOT_HALAL = "NOT_HALAL"
    DOUBTFUL = "DOUBTFUL"
    NOT_COVERED = "NOT_COVERED"
    ERROR = "ERROR"


@dataclass
class ScreeningResult:
    """Result of a stock screening."""

    ticker: str
    status: ComplianceStatus
    source: str = "unknown"
    compliance_ranking: Optional[str] = None
    company_name: Optional[str] = None
    details: Optional[str] = None
    error_message: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "status": self.status.value,
            "source": self.source,
            "compliance_ranking": self.compliance_ranking,
            "company_name": self.company_name,
            "details": self.details,
            "error_message": self.error_message,
        }


def get_chromium_path() -> str | None:
    """Get system Chromium path if available."""
    paths = [
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        shutil.which("chromium"),
    ]
    for path in paths:
        if path and shutil.os.path.exists(path):
            return path
    return None


# Scraper constants
MAX_CONCURRENT_PAGES = 2
