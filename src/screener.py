"""Core screening logic that orchestrates the full check flow."""

import logging
from dataclasses import dataclass
from typing import Optional

from scraper import MusaffaScraper, ScreeningResult, ComplianceStatus
from image_parser import ImageParser, parse_text_for_tickers
from database import TickerCache, CheckHistory, init_database

logger = logging.getLogger(__name__)


@dataclass
class ScreenResponse:
    """Response from a screening request."""
    results: list[ScreeningResult]
    from_cache: list[bool]
    error: Optional[str] = None

    def format_message(self) -> str:
        """Format results as a user-friendly HTML message."""
        import html

        if self.error:
            return f"❌ Error: {html.escape(self.error)}"

        if not self.results:
            return "No tickers found to screen."

        lines = []
        for result, cached in zip(self.results, self.from_cache):
            status_emoji = {
                ComplianceStatus.HALAL: "✅",
                ComplianceStatus.NOT_HALAL: "❌",
                ComplianceStatus.DOUBTFUL: "⚠️",
                ComplianceStatus.NOT_COVERED: "❓",
                ComplianceStatus.ERROR: "⚠️"
            }.get(result.status, "❓")

            line = f"{status_emoji} <b>{html.escape(result.ticker)}</b>: {html.escape(result.status.value)}"

            if result.company_name:
                line += f"\n   {html.escape(result.company_name)}"

            if result.compliance_ranking:
                line += f"\n   Ranking: {html.escape(result.compliance_ranking)}"

            if result.error_message:
                line += f"\n   <i>{html.escape(result.error_message)}</i>"

            if cached:
                line += " (cached)"

            lines.append(line)

        return "\n\n".join(lines)


class StockScreener:
    """Main screener class that orchestrates all components."""

    def __init__(self):
        self.scraper = MusaffaScraper()
        self.image_parser: Optional[ImageParser] = None
        self._init_image_parser()

        # Ensure database is initialized
        init_database()

    def _init_image_parser(self):
        """Initialize image parser if API key is available."""
        try:
            self.image_parser = ImageParser()
        except ValueError as e:
            logger.warning(f"Image parser not available: {e}")
            self.image_parser = None

    async def screen_tickers(
        self,
        tickers: list[str],
        user_id: Optional[int] = None
    ) -> ScreenResponse:
        """Screen a list of tickers.

        Args:
            tickers: List of ticker symbols to screen
            user_id: Optional user ID for history tracking

        Returns:
            ScreenResponse with results
        """
        if not tickers:
            return ScreenResponse(results=[], from_cache=[], error="No tickers provided")

        # Normalize tickers
        tickers = [t.upper().strip() for t in tickers]
        tickers = list(dict.fromkeys(tickers))  # Remove duplicates, preserve order

        results = []
        from_cache = []

        # Check cache first
        uncached_tickers = []
        for ticker in tickers:
            cached = TickerCache.get(ticker)
            if cached:
                logger.info(f"Cache hit for {ticker}")
                results.append(ScreeningResult(
                    ticker=ticker,
                    status=ComplianceStatus(cached["status"]),
                    compliance_ranking=cached.get("compliance_ranking"),
                    details=cached.get("details")
                ))
                from_cache.append(True)
            else:
                uncached_tickers.append(ticker)

        # Fetch uncached tickers
        if uncached_tickers:
            logger.info(f"Fetching {len(uncached_tickers)} uncached tickers")
            fresh_results = await self.scraper.screen_multiple(uncached_tickers)

            for result in fresh_results:
                results.append(result)
                from_cache.append(False)

                # Cache successful results
                if result.status != ComplianceStatus.ERROR:
                    TickerCache.set(
                        ticker=result.ticker,
                        status=result.status.value,
                        compliance_ranking=result.compliance_ranking,
                        details=result.details
                    )

        # Record history for user
        if user_id:
            for result in results:
                CheckHistory.record(
                    user_id=user_id,
                    ticker=result.ticker,
                    status=result.status.value
                )

        # Reorder results to match original ticker order
        result_map = {r.ticker: (r, c) for r, c in zip(results, from_cache)}
        ordered_results = []
        ordered_cache = []
        for ticker in tickers:
            if ticker in result_map:
                r, c = result_map[ticker]
                ordered_results.append(r)
                ordered_cache.append(c)

        return ScreenResponse(results=ordered_results, from_cache=ordered_cache)

    async def screen_text(
        self,
        text: str,
        user_id: Optional[int] = None
    ) -> ScreenResponse:
        """Screen tickers from text input.

        Args:
            text: Text containing ticker symbols
            user_id: Optional user ID for history tracking

        Returns:
            ScreenResponse with results
        """
        tickers = parse_text_for_tickers(text)

        if not tickers:
            # Maybe the text is just a single ticker without special formatting
            cleaned = text.strip().upper()
            if 1 <= len(cleaned) <= 5 and cleaned.isalpha():
                tickers = [cleaned]

        return await self.screen_tickers(tickers, user_id)

    async def screen_image(
        self,
        image_data: bytes,
        user_id: Optional[int] = None
    ) -> ScreenResponse:
        """Screen tickers from an image.

        Args:
            image_data: Raw image bytes
            user_id: Optional user ID for history tracking

        Returns:
            ScreenResponse with results
        """
        if self.image_parser is None:
            return ScreenResponse(
                results=[],
                from_cache=[],
                error="Image analysis is not available. Please set GEMINI_API_KEY."
            )

        try:
            tickers = await self.image_parser.extract_tickers(image_data)
        except Exception as e:
            logger.error(f"Error extracting tickers from image: {e}")
            return ScreenResponse(
                results=[],
                from_cache=[],
                error=f"Failed to analyze image: {str(e)}"
            )

        if not tickers:
            return ScreenResponse(
                results=[],
                from_cache=[],
                error="No stock tickers found in the image."
            )

        return await self.screen_tickers(tickers, user_id)

    def get_user_history(self, user_id: int, limit: int = 20) -> list[dict]:
        """Get screening history for a user."""
        return CheckHistory.get_user_history(user_id, limit)

    def get_user_stats(self, user_id: int) -> dict:
        """Get statistics for a user."""
        return CheckHistory.get_stats(user_id)

    def clear_expired_cache(self):
        """Clear expired cache entries."""
        TickerCache.clear_expired()
