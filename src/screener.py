"""Core screening logic that orchestrates the full check flow."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from scraper import MusaffaScraper, ScreeningResult, ComplianceStatus
from zoya_scraper import ZoyaScraper
from resolver import resolve_compliance
from image_parser import ImageParser, parse_text_for_tickers, QuotaExceededError
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
            return f"❌ {html.escape(self.error)}"

        if not self.results:
            return "No tickers found to screen."

        # Single ticker: detailed view
        if len(self.results) == 1:
            return self._format_single_ticker(self.results[0])

        # Multiple tickers: grouped view
        return self._format_multiple_tickers()

    def _format_single_ticker(self, result: ScreeningResult) -> str:
        """Format a single ticker result with details."""
        status_display = {
            ComplianceStatus.HALAL: ("✅", "Halal"),
            ComplianceStatus.NOT_HALAL: ("❌", "Not Halal"),
            ComplianceStatus.DOUBTFUL: ("⚠️", "Doubtful"),
            ComplianceStatus.NOT_COVERED: ("❓", "Not Covered"),
            ComplianceStatus.ERROR: ("⚠️", "Error"),
        }

        icon, status_text = status_display.get(result.status, ("❓", "Unknown"))

        lines = [f"<b>{result.ticker}</b>"]

        # Company name if available
        if result.company_name:
            lines[0] = f"<b>{result.ticker}</b>  •  {result.company_name}"

        # Status line
        lines.append(f"{icon} <b>{status_text}</b>")

        # Compliance ranking if available
        if result.compliance_ranking:
            lines.append(f"📊 Ranking: {result.compliance_ranking}")

        # Show conflict info if present in details
        if result.details and "Conflict:" in result.details:
            lines.append(f"⚡ {result.details}")

        return "\n".join(lines)

    def _format_multiple_tickers(self) -> str:
        """Format multiple ticker results in a clean grouped view."""
        # Group results by status
        groups = {
            ComplianceStatus.HALAL: [],
            ComplianceStatus.NOT_HALAL: [],
            ComplianceStatus.DOUBTFUL: [],
        }
        other = []

        for result in self.results:
            if result.status in groups:
                groups[result.status].append(result.ticker)
            else:
                other.append(result.ticker)

        lines = []

        # Halal stocks
        if groups[ComplianceStatus.HALAL]:
            tickers = groups[ComplianceStatus.HALAL]
            lines.append(f"✅ <b>Halal</b>  ·  {', '.join(tickers)}")

        # Not Halal stocks
        if groups[ComplianceStatus.NOT_HALAL]:
            tickers = groups[ComplianceStatus.NOT_HALAL]
            lines.append(f"❌ <b>Not Halal</b>  ·  {', '.join(tickers)}")

        # Doubtful stocks
        if groups[ComplianceStatus.DOUBTFUL]:
            tickers = groups[ComplianceStatus.DOUBTFUL]
            lines.append(f"⚠️ <b>Doubtful</b>  ·  {', '.join(tickers)}")

        # Other (not covered, errors)
        if other:
            lines.append(f"❓ <b>Not Covered</b>  ·  {', '.join(other)}")

        return "\n".join(lines)


class StockScreener:
    """Main screener class that orchestrates all components."""

    def __init__(self):
        self.musaffa_scraper = MusaffaScraper()
        self.zoya_scraper = ZoyaScraper()
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
        """Screen a list of tickers using both Musaffa and Zoya sources.

        Args:
            tickers: List of ticker symbols to screen
            user_id: Optional user ID for history tracking

        Returns:
            ScreenResponse with resolved results
        """
        if not tickers:
            return ScreenResponse(results=[], from_cache=[], error="No tickers provided")

        # Normalize tickers
        tickers = [t.upper().strip() for t in tickers]
        tickers = list(dict.fromkeys(tickers))  # Remove duplicates, preserve order

        results = []
        from_cache = []
        conflicts = []

        # Check cache for both sources
        tickers_to_fetch_musaffa = []
        tickers_to_fetch_zoya = []
        cached_musaffa = {}
        cached_zoya = {}

        for ticker in tickers:
            musaffa_cached = TickerCache.get(ticker, "musaffa")
            zoya_cached = TickerCache.get(ticker, "zoya")

            if musaffa_cached:
                logger.info(f"Cache hit for {ticker} (musaffa)")
                cached_musaffa[ticker] = ScreeningResult(
                    ticker=ticker,
                    status=ComplianceStatus(musaffa_cached["status"]),
                    source="musaffa",
                    compliance_ranking=musaffa_cached.get("compliance_ranking"),
                    details=musaffa_cached.get("details")
                )
            else:
                tickers_to_fetch_musaffa.append(ticker)

            if zoya_cached:
                logger.info(f"Cache hit for {ticker} (zoya)")
                cached_zoya[ticker] = ScreeningResult(
                    ticker=ticker,
                    status=ComplianceStatus(zoya_cached["status"]),
                    source="zoya",
                    details=zoya_cached.get("details")
                )
            else:
                tickers_to_fetch_zoya.append(ticker)

        # Fetch from both sources in parallel
        musaffa_results = {}
        zoya_results = {}

        async def fetch_musaffa():
            if tickers_to_fetch_musaffa:
                logger.info(f"Fetching {len(tickers_to_fetch_musaffa)} tickers from Musaffa")
                fresh = await self.musaffa_scraper.screen_multiple(tickers_to_fetch_musaffa)
                for result in fresh:
                    musaffa_results[result.ticker] = result
                    # Cache successful results
                    if result.status != ComplianceStatus.ERROR:
                        TickerCache.set(
                            ticker=result.ticker,
                            status=result.status.value,
                            source="musaffa",
                            compliance_ranking=result.compliance_ranking,
                            details=result.details
                        )

        async def fetch_zoya():
            if tickers_to_fetch_zoya:
                logger.info(f"Fetching {len(tickers_to_fetch_zoya)} tickers from Zoya")
                fresh = await self.zoya_scraper.screen_multiple(tickers_to_fetch_zoya)
                for result in fresh:
                    zoya_results[result.ticker] = result
                    # Cache successful results
                    if result.status != ComplianceStatus.ERROR:
                        TickerCache.set(
                            ticker=result.ticker,
                            status=result.status.value,
                            source="zoya",
                            details=result.details
                        )

        # Run both fetches in parallel
        await asyncio.gather(fetch_musaffa(), fetch_zoya())

        # Merge cached and fresh results
        all_musaffa = {**cached_musaffa, **musaffa_results}
        all_zoya = {**cached_zoya, **zoya_results}

        # Resolve conflicts and build final results
        for ticker in tickers:
            musaffa_result = all_musaffa.get(ticker)
            zoya_result = all_zoya.get(ticker)

            # Determine if result came from cache
            is_cached = ticker in cached_musaffa and ticker in cached_zoya

            # Resolve compliance using both sources
            final_result, is_conflict = resolve_compliance(musaffa_result, zoya_result)
            results.append(final_result)
            from_cache.append(is_cached)

            if is_conflict:
                conflicts.append(ticker)

            # Record history for user
            if user_id:
                CheckHistory.record(
                    user_id=user_id,
                    ticker=ticker,
                    final_status=final_result.status.value,
                    musaffa_status=musaffa_result.status.value if musaffa_result else None,
                    zoya_status=zoya_result.status.value if zoya_result else None,
                    is_conflict=is_conflict
                )

        if conflicts:
            logger.warning(f"Conflicts detected for tickers: {', '.join(conflicts)}")

        return ScreenResponse(results=results, from_cache=from_cache)

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
        except QuotaExceededError:
            return ScreenResponse(
                results=[],
                from_cache=[],
                error="Image analysis quota exceeded. Please try again later or send ticker symbols as text."
            )
        except Exception as e:
            logger.error(f"Error extracting tickers from image: {e}")
            return ScreenResponse(
                results=[],
                from_cache=[],
                error="Failed to analyze image. Please try again or send ticker symbols as text."
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
