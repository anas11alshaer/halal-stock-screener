"""Provider-neutral image extraction contract."""

from abc import ABC, abstractmethod


class ImageExtractionQuotaError(Exception):
    """Raised when an image provider has exhausted its available quota."""


class ImageExtractor(ABC):
    """Extract canonical ticker symbols from image bytes."""

    @abstractmethod
    async def extract_tickers(self, image_data: bytes) -> list[str]:
        """Return validated ticker symbols found in an image."""
