"""Business-activity screen: yfinance sector/industry vs policy denylists."""

from __future__ import annotations

from plugins.base import Plugin, PluginVote, ScreenContext, Vote, is_fund


class ActivityPlugin(Plugin):
    name = "Activity"

    async def vote(self, ctx: ScreenContext) -> PluginVote:
        if is_fund(ctx.quote_type, self.policy):
            return PluginVote(
                plugin=self.name,
                vote=Vote.ABSTAIN,
                reason="fund wrapper is screened via holdings, not sector",
            )

        section = self.policy.section("activity")
        denied_sectors = {s.lower() for s in section.get("denied_sectors") or []}
        denied_industries = {s.lower() for s in section.get("denied_industries") or []}

        sector = (ctx.fundamentals.sector or "").strip()
        industry = (ctx.fundamentals.industry or "").strip()
        metrics = {"sector": sector or None, "industry": industry or None}

        if not sector and not industry:
            return PluginVote(
                plugin=self.name,
                vote=Vote.ABSTAIN,
                reason="missing sector and industry",
                metrics=metrics,
            )

        if sector and sector.lower() in denied_sectors:
            return PluginVote(
                plugin=self.name,
                vote=Vote.FAIL,
                reason=f"denied sector {sector!r}",
                metrics=metrics,
            )
        if industry and industry.lower() in denied_industries:
            return PluginVote(
                plugin=self.name,
                vote=Vote.FAIL,
                reason=f"denied industry {industry!r}",
                metrics=metrics,
            )
        return PluginVote(
            plugin=self.name,
            vote=Vote.PASS,
            reason="sector/industry not on policy denylist",
            metrics=metrics,
        )
