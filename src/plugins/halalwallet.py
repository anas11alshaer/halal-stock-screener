"""HalalWallet CC-BY JSON dataset. Required only when the ticker is present."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from plugins.base import Plugin, PluginVote, ScreenContext, Vote

logger = logging.getLogger(__name__)


class HalalWalletPlugin(Plugin):
    name = "HalalWallet"

    def __init__(
        self,
        policy,
        records: dict[str, dict[str, Any]] | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(policy)
        self._records = records
        self._http = http
        self._loaded = records is not None
        self._unavailable = False

    def contains(self, ticker: str) -> bool:
        # Unknown universe (unloaded or last fetch failed): required, fail-closed.
        if self._unavailable or not self._loaded or self._records is None:
            return True
        return ticker.upper() in self._records

    async def ensure_loaded(self) -> None:
        if self._loaded:
            return
        url = str(
            self.policy.get("halalwallet", "dataset_url")
            or self.policy.section("halalwallet").get("dataset_url")
        )
        if not url:
            raise RuntimeError("HalalWallet dataset_url missing from policy")
        if self._http is None:
            raise RuntimeError("HalalWallet HTTP client missing")
        logger.info("Fetching HalalWallet dataset")
        response = await self._http.get(url)
        response.raise_for_status()
        self._records = index_halalwallet(response.json())
        self._loaded = True
        self._unavailable = False

    async def vote(self, ctx: ScreenContext) -> PluginVote:
        if not self._loaded:
            try:
                await self.ensure_loaded()
            except Exception as exc:
                self._unavailable = True
                logger.warning("HalalWallet dataset unavailable: %s", exc)
                return PluginVote(
                    plugin=self.name,
                    vote=Vote.ABSTAIN,
                    reason="HalalWallet dataset unavailable",
                    metrics={"unavailable": True},
                )
        records = self._records or {}
        rec = records.get(ctx.ticker.upper())
        if rec is None:
            return PluginVote(
                plugin=self.name,
                vote=Vote.ABSTAIN,
                reason="ticker not in HalalWallet dataset",
                metrics={"in_dataset": False},
            )

        verdict = str(rec.get("verdict") or "").strip().lower()
        section = self.policy.section("halalwallet")
        pass_verdicts = {v.lower() for v in section.get("pass_verdicts") or ["halal"]}
        fail_verdicts = {
            v.lower() for v in section.get("fail_verdicts") or ["not_halal"]
        }
        metrics = {
            "in_dataset": True,
            "dataset_verdict": rec.get("verdict"),
            "businessActivityPass": rec.get("businessActivityPass"),
            "financialScreenPass": rec.get("financialScreenPass"),
        }
        if (
            rec.get("businessActivityPass") is False
            or rec.get("financialScreenPass") is False
        ):
            return PluginVote(
                plugin=self.name,
                vote=Vote.FAIL,
                reason="HalalWallet activity or financial screen failed",
                metrics=metrics,
            )
        if verdict in pass_verdicts:
            return PluginVote(
                plugin=self.name,
                vote=Vote.PASS,
                reason="HalalWallet dataset verdict is pass",
                metrics=metrics,
            )
        if verdict in fail_verdicts:
            return PluginVote(
                plugin=self.name,
                vote=Vote.FAIL,
                reason="HalalWallet dataset verdict is fail",
                metrics=metrics,
            )
        return PluginVote(
            plugin=self.name,
            vote=Vote.ABSTAIN,
            reason=f"HalalWallet verdict {verdict!r} is neither pass nor fail",
            metrics=metrics,
        )


def index_halalwallet(payload: Any) -> dict[str, dict[str, Any]]:
    """Index CC-BY records by ticker. Skip entities with no ticker."""
    if isinstance(payload, dict):
        rows = payload.get("records", [])
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    indexed: dict[str, dict[str, Any]] = {}
    for rec in rows:
        if not isinstance(rec, dict):
            continue
        ticker = rec.get("ticker")
        if not ticker:
            continue
        indexed[str(ticker).upper()] = rec
    return indexed
