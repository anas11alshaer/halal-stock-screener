"""Yahoo Finance live price provider (no API key)."""

import asyncio
import logging
import re

import yfinance as yf

from prices.base import PriceProvider, PriceQuote

logger = logging.getLogger(__name__)


class YahooPriceProvider(PriceProvider):
    """Fetch live quotes via yfinance fast_info, one failure per ticker."""

    async def get_prices(self, tickers: list[str]) -> list[PriceQuote]:
        return list(await asyncio.gather(*(asyncio.to_thread(self._fetch, t) for t in tickers)))

    @staticmethod
    def _fetch(ticker: str) -> PriceQuote:
        symbol = ticker.strip().upper()
        yahoo_symbol = symbol.replace(".", "-") if re.fullmatch(r"[A-Z]{1,5}\.[A-Z]", symbol) else symbol
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
