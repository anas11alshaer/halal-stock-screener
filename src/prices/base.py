"""Live price provider contract."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class PriceQuote:
    """One live quote; a set `error` means this ticker failed alone."""

    ticker: str
    price: float | None = None
    currency: str | None = None
    change_pct: float | None = None
    error: str | None = None
    quote_url: str = ""


class PriceProvider(ABC):
    """Fetch live prices without caching or compliance side effects."""

    @abstractmethod
    async def get_prices(self, tickers: list[str]) -> list[PriceQuote]:
        """Return one quote per ticker in request order."""
