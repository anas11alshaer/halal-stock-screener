"""Business-activity screen: yfinance denylists, then [[activity.segments]] tags."""

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
        denied_industry_substrings = [
            s.lower() for s in section.get("denied_industry_substrings") or []
        ]

        sector = (ctx.fundamentals.sector or "").strip()
        industry = (ctx.fundamentals.industry or "").strip()
        metrics = {"sector": sector or None, "industry": industry or None}

        if not sector or not industry:
            return PluginVote(
                plugin=self.name,
                vote=Vote.ABSTAIN,
                reason="missing sector or industry",
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
        folded_industry = industry.lower()
        if any(part in folded_industry for part in denied_industry_substrings):
            return PluginVote(
                plugin=self.name,
                vote=Vote.FAIL,
                reason=f"denied industry {industry!r}",
                metrics=metrics,
            )

        actions = {
            str(spec["id"]): str(spec.get("action") or "").casefold()
            for spec in (section.get("segments") or [])
            if isinstance(spec, dict) and spec.get("id")
        }
        core_fail_positive = False
        core_fail_unquantified = False
        failed_tag: str | None = None
        for seg in ctx.fundamentals.segments or []:
            for tag in seg.tags or []:
                action = actions.get(tag)
                if action != "core_fail":
                    continue
                positive = (seg.revenue is not None and seg.revenue > 0) or (
                    seg.revenue_pct is not None and seg.revenue_pct > 0
                )
                if positive:
                    core_fail_positive = True
                    failed_tag = tag
                elif seg.revenue is None and seg.revenue_pct is None:
                    core_fail_unquantified = True
        if core_fail_positive:
            return PluginVote(
                plugin=self.name,
                vote=Vote.FAIL,
                reason=f"denied segment tag {failed_tag!r}",
                metrics=metrics,
            )
        if core_fail_unquantified:
            return PluginVote(
                plugin=self.name,
                vote=Vote.ABSTAIN,
                reason="unquantified denied activity tag",
                metrics=metrics,
            )
        return PluginVote(
            plugin=self.name,
            vote=Vote.PASS,
            reason="sector/industry not on policy denylist",
            metrics=metrics,
        )
