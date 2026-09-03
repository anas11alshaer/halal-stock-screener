"""Policy-driven conjunction engine used by the eval harness (not Telegram)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from config import REQUEST_TIMEOUT
from fusion import NOT_HALAL, fuse, required_plugin_names
from market_data import MarketData, YFinanceMarketData
from plugins.activity import ActivityPlugin
from plugins.base import (
    Fundamentals,
    Plugin,
    PluginVote,
    ScreenContext,
    Vote,
    is_fund,
)
from plugins.halalwallet import HalalWalletPlugin
from plugins.nport import NportClient, NportHoldingsPlugin, SecNportClient
from plugins.ratios import RatiosPlugin
from policy import Policy

logger = logging.getLogger(__name__)


@dataclass
class ScreenResult:
    ticker: str
    quote_type: str
    verdict: str
    votes: dict[str, PluginVote]
    required: list[str]
    company_name: str | None = None

    def vote_label(self, plugin: str) -> str:
        vote = self.votes.get(plugin)
        return vote.vote.value if vote else "—"


class VerdictEngine:
    """Run enabled plugins and fuse. Does not parse marketing HTML."""

    def __init__(
        self,
        policy: Policy,
        *,
        market: MarketData | None = None,
        nport: NportClient | None = None,
        halalwallet_records: dict | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.policy = policy
        self._owns_http = http is None
        self._http = http
        self._market = market
        self._nport = nport
        self._cache: dict[tuple[str, int], ScreenResult] = {}
        self._halalwallet = HalalWalletPlugin(
            policy, records=halalwallet_records, http=None
        )
        self._plugins: dict[str, Plugin] = {}

    async def aclose(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _ensure_clients(self) -> None:
        if self._http is None and (
            self._market is None or self._nport is None or not self._halalwallet._loaded
        ):
            self._http = httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT, follow_redirects=True
            )
        if self._market is None:
            self._market = YFinanceMarketData(self.policy, http=self._http)
        if self._nport is None:
            assert self._http is not None
            self._nport = SecNportClient(self.policy, self._http)
        if not self._halalwallet._loaded:
            self._halalwallet._http = self._http
        if self._plugins:
            return
        self._plugins = {
            "Activity": ActivityPlugin(self.policy),
            "Ratios": RatiosPlugin(self.policy),
            "HalalWallet": self._halalwallet,
            "NportHoldings": NportHoldingsPlugin(
                self.policy,
                nport=self._nport,
                screen_holding=self._screen_holding_verdict,
            ),
        }

    async def _screen_holding_verdict(self, ticker: str, depth: int) -> str:
        result = await self.screen(ticker, depth=depth)
        return result.verdict

    async def screen(self, ticker: str, depth: int = 0) -> ScreenResult:
        ticker = ticker.upper().strip()
        cache_key = (ticker, depth)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        await self._ensure_clients()
        assert self._market is not None
        try:
            await self._halalwallet.ensure_loaded()
        except Exception as exc:
            # Leave unloaded so the next screen retries; contains() fail-closes.
            logger.warning("HalalWallet dataset failed: %s", exc)
            self._halalwallet._unavailable = True

        try:
            fundamentals = await self._market.get(ticker)
        except Exception as exc:
            logger.warning("fundamentals failed for %s: %s", ticker, exc)
            fundamentals = Fundamentals(ticker=ticker, quote_type="UNKNOWN")

        quote_type = (fundamentals.quote_type or "UNKNOWN").upper()
        ctx = ScreenContext(
            ticker=ticker,
            quote_type=quote_type,
            fundamentals=fundamentals,
            depth=depth,
        )

        in_dataset = self._halalwallet.contains(ticker)
        required = required_plugin_names(
            fusion_section=self.policy.section("fusion"),
            quote_type=quote_type,
            ticker_in_halalwallet=in_dataset,
            enabled=self.policy.enabled_plugins,
        )

        votes: dict[str, PluginVote] = {}
        for name in self.policy.enabled_plugins:
            plugin = self._plugins.get(name)
            if plugin is None:
                votes[name] = PluginVote(
                    plugin=name,
                    vote=Vote.ABSTAIN,
                    reason="plugin not implemented",
                )
                continue
            try:
                votes[name] = await plugin.vote(ctx)
            except Exception as exc:
                logger.warning("%s failed for %s: %s", name, ticker, exc)
                votes[name] = PluginVote(
                    plugin=name,
                    vote=Vote.ABSTAIN,
                    reason=f"plugin error: {exc}",
                )

        verdict = fuse(self.policy.fusion_name, votes, required)
        if is_fund(quote_type, self.policy):
            nport_vote = votes.get("NportHoldings")
            if nport_vote is None or nport_vote.vote is not Vote.PASS:
                verdict = NOT_HALAL
        result = ScreenResult(
            ticker=ticker,
            quote_type=quote_type,
            verdict=verdict,
            votes=votes,
            required=required,
            company_name=fundamentals.company_name,
        )
        self._cache[cache_key] = result
        logger.info(
            "%s: %s (required=%s votes=%s)",
            ticker,
            verdict,
            required,
            {k: v.vote.value for k, v in votes.items()},
        )
        return result


def format_screen_table(results: list[ScreenResult], plugin_names: list[str]) -> str:
    """Plain-text table: ticker, per-plugin votes, binary verdict, ratio metrics."""
    headers = ["TICKER", "TYPE", *plugin_names, "VERDICT", "debt%", "cash%", "int%"]
    rows: list[list[str]] = []
    for result in results:
        ratios = result.votes.get("Ratios").metrics if "Ratios" in result.votes else {}
        row = [
            result.ticker,
            result.quote_type,
            *[result.vote_label(name) for name in plugin_names],
            result.verdict,
            _fmt_pct(ratios.get("debt_pct") if isinstance(ratios, dict) else None),
            _fmt_pct(ratios.get("cash_pct") if isinstance(ratios, dict) else None),
            _fmt_pct(
                ratios.get("interest_income_pct") if isinstance(ratios, dict) else None
            ),
        ]
        rows.append(row)
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt(row: list[str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row))

    lines = [fmt(headers), "  ".join("-" * w for w in widths)]
    lines.extend(fmt(row) for row in rows)
    return "\n".join(lines)


def _fmt_pct(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "—"
    return f"{value:.2f}"


__all__ = [
    "ScreenResult",
    "VerdictEngine",
    "format_screen_table",
]
