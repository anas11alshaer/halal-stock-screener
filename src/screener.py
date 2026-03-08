"""Core screening logic that orchestrates the full check flow."""

import asyncio
import html
import logging
from dataclasses import dataclass
from typing import Optional

from scrapers import MusaffaScraper, ZoyaScraper, ScreeningResult, ComplianceStatus, STATUS_ICON, STATUS_TEXT, get_quote_type
from resolver import resolve_compliance
from image_parser import ImageParser, parse_text_for_tickers, QuotaExceededError
from database import TickerCache, CheckHistory, ImageCache, init_database
from config import MAX_TICKERS_PER_REQUEST

logger = logging.getLogger(__name__)


@dataclass
class ScreenResponse:
    """Response from a screening request."""
    results: list[ScreeningResult]
    from_cache: list[bool]
    error: Optional[str] = None
    # Per-ticker source results: {ticker: {"musaffa": ScreeningResult, "zoya": ScreeningResult}}
    source_results: dict = None

    def __post_init__(self):
        if self.source_results is None:
            self.source_results = {}

    def format_message(self) -> str:
        """Format results as a user-friendly HTML message."""
        if self.error:
            return f"❌ {html.escape(self.error)}"

        if not self.results:
            return "No tickers found to screen."

        # Single ticker: detailed view
        if len(self.results) == 1:
            r = self.results[0]
            musaffa = self.source_results.get(r.ticker, {}).get("musaffa")
            zoya = self.source_results.get(r.ticker, {}).get("zoya")
            return self._format_single_ticker(r, musaffa, zoya)

        # Multiple tickers: compact view
        return self._format_multiple_tickers()

    def _format_single_ticker(
        self,
        result: ScreeningResult,
        musaffa: Optional[ScreeningResult] = None,
        zoya: Optional[ScreeningResult] = None,
    ) -> str:
        """Format a single ticker result with details."""
        icon = STATUS_ICON.get(result.status, "❓")
        is_etf = result.quote_type == "ETF"

        lines = []

        # Header with company name
        if result.company_name:
            header = f"<b>{result.company_name}</b> ({result.ticker})"
        else:
            header = f"<b>{result.ticker}</b>"
        if is_etf:
            header += " · ETF"
        lines.append(header)

        lines.append("")

        # Overall verdict
        lines.append(f"{icon} Verdict: <b>{STATUS_TEXT.get(result.status, 'Unknown')}</b>")
        lines.append("")

        # Source breakdown
        if is_etf:
            lines.append("─── Source ───")
            if musaffa:
                m_icon = STATUS_ICON.get(musaffa.status, "❓")
                lines.append(f"{m_icon} Musaffa: <b>{STATUS_TEXT.get(musaffa.status, 'Unknown')}</b>")
            lines.append("")
            lines.append("ℹ️ Screened by Musaffa only — Zoya does not cover ETFs")
        else:
            lines.append("─── Sources ───")
            if musaffa:
                m_icon = STATUS_ICON.get(musaffa.status, "❓")
                lines.append(f"{m_icon} Musaffa: <b>{STATUS_TEXT.get(musaffa.status, 'Unknown')}</b>")
            if zoya:
                z_icon = STATUS_ICON.get(zoya.status, "❓")
                lines.append(f"{z_icon} Zoya: <b>{STATUS_TEXT.get(zoya.status, 'Unknown')}</b>")

            # Conflict warning
            if result.details and "Conflict:" in result.details:
                lines.append(f"\n⚡ Sources disagree — using more restrictive result")

        return "\n".join(lines)

    def _format_multiple_tickers(self) -> str:
        """Format multiple ticker results showing per-source breakdown."""
        lines = [f"<b>Screening Results</b>\n"]

        for result in self.results:
            icon = STATUS_ICON.get(result.status, "❓")
            status = STATUS_TEXT.get(result.status, "Unknown")
            is_etf = result.quote_type == "ETF"

            # Header line
            if result.company_name:
                header = f"{icon} <b>{result.ticker}</b>"
                if is_etf:
                    header += " · ETF"
                header += f" — {result.company_name}"
            else:
                header = f"{icon} <b>{result.ticker}</b>"
                if is_etf:
                    header += " · ETF"
                header += f" — {status}"
            lines.append(header)

            # Source details
            sources = self.source_results.get(result.ticker, {})
            musaffa = sources.get("musaffa")
            zoya = sources.get("zoya")

            if is_etf:
                parts = []
                if musaffa:
                    parts.append(f"Musaffa: {STATUS_TEXT.get(musaffa.status, '?')}")
                parts.append("Zoya: N/A for ETFs")
                lines.append(f"      {' · '.join(parts)}")
            else:
                parts = []
                if musaffa:
                    parts.append(f"Musaffa: {STATUS_TEXT.get(musaffa.status, '?')}")
                if zoya:
                    parts.append(f"Zoya: {STATUS_TEXT.get(zoya.status, '?')}")
                if parts:
                    lines.append(f"      {' · '.join(parts)}")

            lines.append("")

        return "\n".join(lines).strip()


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
            self.image_parser = ImageParser(image_cache=ImageCache)
        except ValueError as e:
            logger.warning(f"Image parser not available: {e}")
            self.image_parser = None

    async def screen_tickers(
        self,
        tickers: list[str],
        user_id: Optional[int] = None
    ) -> ScreenResponse:
        """Screen a list of tickers using both Musaffa and Zoya sources.

        Processes tickers in batches of MAX_TICKERS_PER_REQUEST to limit
        concurrent HTTP requests.

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

        # Process in batches
        all_results = []
        all_from_cache = []
        all_source_results = {}

        for i in range(0, len(tickers), MAX_TICKERS_PER_REQUEST):
            batch = tickers[i:i + MAX_TICKERS_PER_REQUEST]
            batch_resp = await self._screen_batch(batch, user_id)
            all_results.extend(batch_resp.results)
            all_from_cache.extend(batch_resp.from_cache)
            all_source_results.update(batch_resp.source_results)

        return ScreenResponse(
            results=all_results,
            from_cache=all_from_cache,
            source_results=all_source_results,
        )

    async def _screen_batch(
        self,
        tickers: list[str],
        user_id: Optional[int] = None
    ) -> ScreenResponse:
        """Screen a single batch of tickers against both sources."""
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

        # Populate quote_type on final results for display
        for result in results:
            result.quote_type = await get_quote_type(result.ticker)

        # Build per-ticker source breakdown for display
        source_results = {}
        for ticker in tickers:
            source_results[ticker] = {
                "musaffa": all_musaffa.get(ticker),
                "zoya": all_zoya.get(ticker),
            }

        return ScreenResponse(
            results=results,
            from_cache=from_cache,
            source_results=source_results,
        )

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
        try:
            TickerCache.clear_expired()
            ImageCache.clear_expired()
        except Exception as e:
            logger.warning(f"Failed to clear expired cache: {e}")
