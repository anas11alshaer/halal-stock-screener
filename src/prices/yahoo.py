"""Yahoo Finance live price provider (no API key)."""

import asyncio
import logging
import re

import yfinance as yf

from prices.base import PriceProvider, PriceQuote

logger = logging.getLogger(__name__)


class YahooPriceProvider(PriceProvider):
    """Fetch live quotes via yfinance fast_info, one failure per ticker.

    Dotted share classes (``BRK.B``) fall back to Yahoo's hyphen form (``BRK-B``) only
    when the symbol as typed yields no price, so exchange suffixes like ``VOD.L`` still work.
    """

    async def get_prices(self, tickers: list[str]) -> list[PriceQuote]:
        return list(await asyncio.gather(*(asyncio.to_thread(self._fetch, t) for t in tickers)))

    @classmethod
    def _fetch(cls, ticker: str) -> PriceQuote:
        symbol = ticker.strip().upper()
        quote = cls._lookup(symbol, symbol)
        if quote.price is None and re.fullmatch(r"[A-Z]{1,5}\.[A-Z]", symbol):
            alt_symbol = symbol.replace(".", "-")
            logger.debug("Retrying %s as share-class symbol %s", symbol, alt_symbol)
            alt = cls._lookup(symbol, alt_symbol)
            if alt.price is not None:
                return alt
        return quote

    @staticmethod
    def _lookup(symbol: str, yahoo_symbol: str) -> PriceQuote:
        quote_url = f"https://finance.yahoo.com/quote/{yahoo_symbol}"
        try:
            info = yf.Ticker(yahoo_symbol).fast_info
            price = info.last_price
            if price is None:
                return PriceQuote(
                    ticker=symbol,
                    quote_url=quote_url,
                    error=f"No price available for {symbol}",
                )
            previous = info.previous_close
            change_pct = (price - previous) / previous * 100 if previous else None
            return PriceQuote(
                ticker=symbol,
                price=price,
                currency=info.currency,
                change_pct=change_pct,
                quote_url=quote_url,
            )
        except Exception as exc:
            logger.warning("Yahoo price fetch failed for %s: %s", symbol, exc)
            return PriceQuote(ticker=symbol, quote_url=quote_url, error=str(exc))
