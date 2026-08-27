"""Provider-neutral orchestration for security screening."""

import asyncio
import html
import logging
from dataclasses import dataclass, field

from config import MAX_TICKERS_PER_REQUEST
from database import CheckHistory, ImageCache, TickerCache, init_database
from image_extractors import ImageExtractionQuotaError, ImageExtractor
from image_parser import parse_text_for_tickers
from plugins import (
    load_evidence_reviewer,
    load_image_extractor,
    load_screening_providers,
)
from resolver import resolve_compliance
from scrapers import (
    STATUS_ICON,
    STATUS_TEXT,
    AssetType,
    BaseScraper,
    ComplianceStatus,
    ResultState,
    ScreeningResult,
    Security,
)
from security_resolver import YahooSecurityResolver

logger = logging.getLogger(__name__)

STATE_TEXT = {
    ResultState.NOT_COVERED: "Not covered",
    ResultState.UNSUPPORTED_ASSET: "Unsupported asset",
    ResultState.NETWORK_ERROR: "Network error",
    ResultState.RATE_LIMITED: "Rate limited",
    ResultState.AUTH_REQUIRED: "Authentication required",
    ResultState.PARSE_ERROR: "Page format changed",
    ResultState.SOURCE_UNAVAILABLE: "Source unavailable",
    ResultState.IDENTITY_MISMATCH: "Identity mismatch",
}


@dataclass
class ScreenResponse:
    results: list[ScreeningResult]
    from_cache: list[bool]
    error: str | None = None
    source_results: dict[str, dict[str, ScreeningResult]] = field(default_factory=dict)

    def format_message(self) -> str:
        if self.error:
            return f"❌ {html.escape(self.error)}"
        if not self.results:
            return "No securities found to screen."
        if len(self.results) == 1:
            return self._format_single(self.results[0])
        return self._format_multiple()

    def format_messages(self, max_length: int = 4000) -> list[str]:
        """Render one or more Telegram-safe messages without splitting HTML tags."""
        message = self.format_message()
        if len(message) <= max_length or len(self.results) <= 1:
            return [message[:max_length]]

        messages: list[str] = []
        for result in self.results:
            single = ScreenResponse(
                results=[result],
                from_cache=[False],
                source_results={result.ticker: self.source_results.get(result.ticker, {})},
            ).format_message()
            messages.append(single[:max_length])
        return messages

    def _format_single(self, result: ScreeningResult) -> str:
        name = html.escape(result.company_name or result.ticker)
        ticker = html.escape(result.ticker)
        asset = {
            AssetType.ETF: "ETF",
            AssetType.FUND: "Fund",
            AssetType.STOCK: "Stock",
        }.get(result.asset_type, "Security")
        header = f"<b>{name}</b> ({ticker}) · {asset}"
        icon = STATUS_ICON.get(result.status, "❓")
        verdict = STATUS_TEXT.get(result.status, "Unknown")
        prefix = "Provisional " if result.is_provisional else ""
        lines = [header, "", f"{icon} {prefix}verdict: <b>{verdict}</b>"]
        if result.confirmation_count:
            noun = "source" if result.confirmation_count == 1 else "sources"
            lines.append(f"Confirmed by {result.confirmation_count} {noun}")
        if result.details and result.details.startswith("Tie resolved"):
            lines.append("⚠️ Vote tied, so the result is Not Halal")
        elif result.details and result.details.startswith("Majority vote"):
            lines.append("ℹ️ Sources disagree; majority vote applied")

        lines.extend(["", "<b>Sources</b>"])
        for provider_id, provider_result in self.source_results.get(result.ticker, {}).items():
            if provider_result.state == ResultState.RATE_LIMITED:
                continue
            display_name = html.escape(provider_id.replace("_", " ").title())
            if provider_result.is_confirmed:
                provider_icon = STATUS_ICON.get(provider_result.status, "❓")
                provider_status = STATUS_TEXT.get(provider_result.status, "Unknown")
                lines.append(f"{provider_icon} <b>{display_name}</b>: {provider_status}")
                if provider_result.evidence:
                    evidence = provider_result.evidence[:300]
                    lines.append(f"Evidence: “{html.escape(evidence)}”")
                if provider_result.methodology:
                    lines.append(f"Method: {html.escape(provider_result.methodology[:180])}")
                if provider_result.url:
                    safe_url = html.escape(provider_result.url, quote=True)
                    lines.append(f'<a href="{safe_url}">Open source</a>')
            else:
                state = STATE_TEXT.get(provider_result.state, "Failed")
                lines.append(f"⚠️ <b>{display_name}</b>: {html.escape(state)}")
                if provider_result.error_message:
                    lines.append(html.escape(provider_result.error_message[:300]))
            lines.append("")
        return "\n".join(lines).strip()

    def _format_multiple(self) -> str:
        lines = ["<b>Screening Results</b>", ""]
        for result in self.results:
            icon = STATUS_ICON.get(result.status, "❓")
            status = STATUS_TEXT.get(result.status, "Unknown")
            provisional = "Provisional " if result.is_provisional else ""
            asset = " · ETF" if result.asset_type == AssetType.ETF else ""
            lines.append(
                f"{icon} <b>{html.escape(result.ticker)}</b>{asset} — "
                f"{provisional}{status} ({result.confirmation_count} confirmed)"
            )
            source_parts = []
            for provider_id, provider_result in self.source_results.get(result.ticker, {}).items():
                if provider_result.state == ResultState.RATE_LIMITED:
                    continue
                if provider_result.is_confirmed:
                    value = STATUS_TEXT.get(provider_result.status, "Unknown")
                else:
                    value = STATE_TEXT.get(provider_result.state, "Failed")
                source_parts.append(
                    f"{html.escape(provider_id.replace('_', ' ').title())}: {html.escape(value)}"
                )
            if source_parts:
                lines.append(" · ".join(source_parts))
            lines.append("")
        return "\n".join(lines).strip()


class StockScreener:
    """Resolve securities and orchestrate configured screening providers."""

    def __init__(
        self,
        providers: list[BaseScraper] | None = None,
        security_resolver: YahooSecurityResolver | None = None,
        evidence_reviewer=None,
        image_extractor: ImageExtractor | None = None,
    ):
        self.providers = providers if providers is not None else load_screening_providers()
        self.security_resolver = security_resolver or YahooSecurityResolver()
        self.evidence_reviewer = (
            evidence_reviewer if evidence_reviewer is not None else load_evidence_reviewer()
        )
        self.image_extractor = image_extractor
        if self.image_extractor is None:
            self._init_image_extractor()
        init_database()

    def _init_image_extractor(self):
        try:
            self.image_extractor = load_image_extractor(image_cache=ImageCache)
        except ValueError as exc:
            logger.warning("Image extractor not available: %s", exc)

    async def screen_tickers(
        self, tickers: list[str], user_id: int | None = None
    ) -> ScreenResponse:
        return await self.screen_queries(tickers, user_id)

    async def screen_queries(
        self, queries: list[str], user_id: int | None = None
    ) -> ScreenResponse:
        if not queries:
            return ScreenResponse([], [], error="No security name or ticker provided")

        resolutions = await asyncio.gather(
            *(self.security_resolver.resolve(query) for query in queries)
        )
        securities: list[Security] = []
        for query, resolution in zip(queries, resolutions):
            if resolution.is_ambiguous:
                choices = ", ".join(
                    f"{candidate.symbol} ({candidate.exchange or 'unknown exchange'})"
                    for candidate in resolution.candidates[:5]
                )
                return ScreenResponse(
                    [],
                    [],
                    error=f"{query!r} is ambiguous. Use one of: {choices}",
                )
            if not resolution.security:
                return ScreenResponse([], [], error=resolution.error or "Security not found")
            securities.append(resolution.security)

        unique = {security.symbol: security for security in securities}
        securities = list(unique.values())
        all_results: list[ScreeningResult] = []
        all_cached: list[bool] = []
        all_source_results: dict[str, dict[str, ScreeningResult]] = {}
        for offset in range(0, len(securities), MAX_TICKERS_PER_REQUEST):
            response = await self._screen_batch(
                securities[offset : offset + MAX_TICKERS_PER_REQUEST], user_id
            )
            all_results.extend(response.results)
            all_cached.extend(response.from_cache)
            all_source_results.update(response.source_results)
        return ScreenResponse(all_results, all_cached, source_results=all_source_results)

    async def _screen_batch(
        self, securities: list[Security], user_id: int | None
    ) -> ScreenResponse:
        provider_results: dict[str, dict[str, ScreeningResult]] = {
            security.symbol: {} for security in securities
        }
        cached_pairs: set[tuple[str, str]] = set()

        async def run_provider(provider: BaseScraper):
            missing: list[Security] = []
            for security in securities:
                if not provider.supports(security):
                    provider_results[security.symbol][provider.source_name] = provider.failure(
                        security,
                        ResultState.UNSUPPORTED_ASSET,
                        f"{provider.source_name} does not support {security.asset_type.value}",
                    )
                    continue
                cached = TickerCache.get(security.symbol, provider.source_name)
                if cached:
                    provider_results[security.symbol][provider.source_name] = self._from_cache(
                        cached
                    )
                    cached_pairs.add((security.symbol, provider.source_name))
                else:
                    missing.append(security)
            fresh = await provider.screen_securities(missing)
            for result in fresh:
                if self.evidence_reviewer and result.state == ResultState.PARSE_ERROR:
                    security = next(item for item in missing if item.symbol == result.ticker)
                    result = await self.evidence_reviewer.review(security, result)
                provider_results[result.ticker][provider.source_name] = result
                if result.is_confirmed or result.state == ResultState.NOT_COVERED:
                    self._cache_result(result)

        await asyncio.gather(*(run_provider(provider) for provider in self.providers))

        final_results: list[ScreeningResult] = []
        from_cache: list[bool] = []
        for security in securities:
            ordered_map = {
                provider.source_name: provider_results[security.symbol][provider.source_name]
                for provider in self.providers
            }
            provider_results[security.symbol] = ordered_map
            ordered = list(ordered_map.values())
            final, conflict = resolve_compliance(*ordered)
            final.company_name = security.name or final.company_name
            if security.asset_type != AssetType.UNKNOWN:
                final.asset_type = security.asset_type
                final.quote_type = security.quote_type
            final_results.append(final)
            applicable = [provider for provider in self.providers if provider.supports(security)]
            from_cache.append(
                bool(applicable)
                and all(
                    (security.symbol, provider.source_name) in cached_pairs
                    for provider in applicable
                )
            )
            if user_id:
                CheckHistory.record(
                    user_id=user_id,
                    ticker=security.symbol,
                    final_status=final.status.value,
                    provider_results={
                        result.source: {
                            "status": result.status.value,
                            "state": result.state.value,
                        }
                        for result in ordered
                    },
                    is_conflict=conflict,
                    is_provisional=final.is_provisional,
                    confirmation_count=final.confirmation_count,
                )
        return ScreenResponse(final_results, from_cache, source_results=provider_results)

    @staticmethod
    def _from_cache(cached: dict) -> ScreeningResult:
        return ScreeningResult(
            ticker=cached["ticker"],
            status=ComplianceStatus(cached["status"]),
            source=cached["source"],
            compliance_ranking=cached.get("compliance_ranking"),
            company_name=cached.get("company_name"),
            details=cached.get("details"),
            error_message=cached.get("error_message"),
            quote_type=cached.get("quote_type"),
            state=ResultState(cached.get("state") or "SUCCESS"),
            asset_type=AssetType(cached.get("asset_type") or "UNKNOWN"),
            url=cached.get("url"),
            evidence=cached.get("evidence"),
            methodology=cached.get("methodology"),
            checked_at=cached.get("checked_at"),
            retrieval_method=cached.get("retrieval_method") or "deterministic",
        )

    @staticmethod
    def _cache_result(result: ScreeningResult):
        TickerCache.set(
            ticker=result.ticker,
            source=result.source,
            status=result.status.value,
            compliance_ranking=result.compliance_ranking,
            details=result.details,
            company_name=result.company_name,
            error_message=result.error_message,
            quote_type=result.quote_type,
            state=result.state.value,
            asset_type=result.asset_type.value,
            url=result.url,
            evidence=result.evidence,
            methodology=result.methodology,
            retrieval_method=result.retrieval_method,
            checked_at=result.checked_at,
        )

    async def screen_text(self, text: str, user_id: int | None = None) -> ScreenResponse:
        tickers = parse_text_for_tickers(text)
        if tickers:
            return await self.screen_queries(tickers, user_id)
        return await self.screen_queries([text.strip()], user_id)

    async def screen_image(self, image_data: bytes, user_id: int | None = None) -> ScreenResponse:
        if self.image_extractor is None:
            return ScreenResponse(
                [], [], error="Image analysis is unavailable. Set GEMINI_API_KEY."
            )
        try:
            tickers = await self.image_extractor.extract_tickers(image_data)
        except ImageExtractionQuotaError:
            return ScreenResponse(
                [],
                [],
                error="Image analysis quota exceeded. Send ticker symbols as text.",
            )
        except Exception:
            logger.exception("Image ticker extraction failed")
            return ScreenResponse(
                [],
                [],
                error="Failed to analyze the image. Send ticker symbols as text.",
            )
        if not tickers:
            return ScreenResponse([], [], error="No stock or ETF tickers found in the image.")
        return await self.screen_queries(tickers, user_id)

    def get_user_history(self, user_id: int, limit: int = 20) -> list[dict]:
        return CheckHistory.get_user_history(user_id, limit)

    def get_user_stats(self, user_id: int) -> dict:
        return CheckHistory.get_stats(user_id)

    def clear_expired_cache(self):
        try:
            TickerCache.clear_expired()
            ImageCache.clear_expired()
        except Exception as exc:
            logger.warning("Failed to clear expired cache: %s", exc)
