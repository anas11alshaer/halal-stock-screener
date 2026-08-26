"""Resolve ticker symbols and security names to canonical identities."""

import asyncio
import logging
import re
from dataclasses import dataclass, field

import yfinance as yf

from scrapers.base import AssetType, Security

logger = logging.getLogger(__name__)

TICKER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,14}$")


@dataclass
class ResolutionResult:
    security: Security | None = None
    candidates: list[Security] = field(default_factory=list)
    error: str | None = None

    @property
    def is_ambiguous(self) -> bool:
        return len(self.candidates) > 1 and self.security is None


class YahooSecurityResolver:
    """Resolve symbols and names through Yahoo's search result metadata."""

    async def resolve(self, query: str) -> ResolutionResult:
        query = query.strip()
        if not query:
            return ResolutionResult(error="No security name or ticker provided")

        try:
            search_query = query
            if re.fullmatch(r"[A-Za-z]{1,5}\.[A-Za-z]", query):
                search_query = query.replace(".", "-")
            quotes = await asyncio.to_thread(self._search, search_query)
        except Exception as exc:
            logger.warning("Yahoo search failed for %r: %s", query, exc)
            if TICKER_PATTERN.fullmatch(query):
                return ResolutionResult(
                    security=Security(symbol=query.upper()),
                    error="Yahoo unavailable; provider route detection will be used",
                )
            return ResolutionResult(error="Could not resolve that security name")

        candidates = [self._to_security(quote, query) for quote in quotes]
        candidates = [candidate for candidate in candidates if candidate is not None]
        if not candidates:
            if TICKER_PATTERN.fullmatch(query):
                return ResolutionResult(security=Security(symbol=query.upper()))
            return ResolutionResult(error=f"No listed stock or ETF found for {query!r}")

        if TICKER_PATTERN.fullmatch(query):
            query_key = self._symbol_key(query)
            exact = [
                candidate
                for candidate in candidates
                if self._symbol_key(candidate.yahoo_symbol or candidate.symbol)
                == query_key
            ]
            if len(exact) == 1:
                security = exact[0]
                security.symbol = self._canonical_symbol(query, security.asset_type)
                return ResolutionResult(security=security)

        normalized_name = self._name_key(query)
        exact_names = [
            candidate
            for candidate in candidates
            if candidate.name and self._name_key(candidate.name) == normalized_name
        ]
        if len(exact_names) == 1:
            return ResolutionResult(security=exact_names[0])
        if len(exact_names) > 1:
            return ResolutionResult(candidates=exact_names)

        return ResolutionResult(security=candidates[0])

    @staticmethod
    def _search(query: str) -> list[dict]:
        return yf.Search(
            query,
            max_results=8,
            news_count=0,
            include_cb=False,
            include_nav_links=False,
            include_research=False,
            raise_errors=True,
        ).quotes

    @staticmethod
    def _to_security(quote: dict, original_query: str) -> Security | None:
        quote_type = str(quote.get("quoteType", "")).upper()
        asset_type = {
            "EQUITY": AssetType.STOCK,
            "ETF": AssetType.ETF,
            "MUTUALFUND": AssetType.FUND,
        }.get(quote_type)
        symbol = quote.get("symbol")
        if asset_type not in {AssetType.STOCK, AssetType.ETF} or not symbol:
            return None
        yahoo_symbol = str(symbol).upper()
        return Security(
            symbol=YahooSecurityResolver._canonical_symbol(yahoo_symbol, asset_type),
            yahoo_symbol=yahoo_symbol,
            name=quote.get("longname") or quote.get("shortname"),
            asset_type=asset_type,
            exchange=quote.get("exchange") or quote.get("exchDisp"),
        )

    @staticmethod
    def _symbol_key(value: str) -> str:
        return re.sub(r"[.\-]", "", value).upper()

    @staticmethod
    def _canonical_symbol(value: str, asset_type: AssetType) -> str:
        symbol = value.upper()
        if asset_type == AssetType.STOCK and re.fullmatch(r"[A-Z]{1,5}-[A-Z]", symbol):
            return symbol.replace("-", ".")
        return symbol

    @staticmethod
    def _name_key(value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", value.lower())
