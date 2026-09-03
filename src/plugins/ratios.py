"""Financial ratios vs policy thresholds + margin."""

from __future__ import annotations

from plugins.base import Plugin, PluginVote, ScreenContext, Vote, is_fund


def _ratio_pct(numer: float, denom: float) -> float | None:
    if denom == 0:
        return None
    return (numer / denom) * 100.0


class RatiosPlugin(Plugin):
    name = "Ratios"

    async def vote(self, ctx: ScreenContext) -> PluginVote:
        if is_fund(ctx.quote_type, self.policy):
            return PluginVote(
                plugin=self.name,
                vote=Vote.ABSTAIN,
                reason="fund wrapper is screened via holdings, not issuer ratios",
            )

        section = self.policy.section("ratios")
        debt_lim = float(section["debt_to_market_cap_pct"])
        cash_lim = float(section["cash_to_market_cap_pct"])
        inc_lim = float(section["impermissible_income_to_revenue_pct"])
        margin = float(section.get("margin_pct") or 0.0)
        # Absent flag: apply cash/AR screens.
        cash_screen_enabled = section.get("cash_screen_enabled")
        if cash_screen_enabled is None:
            cash_screen_enabled = True
        receivables_screen_enabled = section.get("receivables_screen_enabled")
        if receivables_screen_enabled is None:
            receivables_screen_enabled = True

        f = ctx.fundamentals
        metrics: dict[str, float | None] = {}

        # Interest income is required; missing → ABSTAIN (fail-closed at fusion).
        if f.interest_income is None:
            return PluginVote(
                plugin=self.name,
                vote=Vote.ABSTAIN,
                reason="missing interest-income",
                metrics=metrics,
            )
        if f.revenue is None or f.market_cap is None:
            return PluginVote(
                plugin=self.name,
                vote=Vote.ABSTAIN,
                reason="missing revenue or market cap",
                metrics=metrics,
            )
        if f.total_debt is None:
            return PluginVote(
                plugin=self.name,
                vote=Vote.ABSTAIN,
                reason="missing debt",
                metrics=metrics,
            )
        if cash_screen_enabled and f.cash_and_securities is None:
            return PluginVote(
                plugin=self.name,
                vote=Vote.ABSTAIN,
                reason="missing cash+securities",
                metrics=metrics,
            )

        debt_pct = _ratio_pct(f.total_debt, f.market_cap)
        cash_pct = (
            _ratio_pct(f.cash_and_securities, f.market_cap)
            if f.cash_and_securities is not None
            else None
        )
        inc_pct = _ratio_pct(f.interest_income, f.revenue)
        metrics = {
            "debt_pct": debt_pct,
            "cash_pct": cash_pct,
            "interest_income_pct": inc_pct,
            "debt_limit_pct": debt_lim - margin,
            "cash_limit_pct": cash_lim - margin,
            "income_limit_pct": inc_lim - margin,
        }
        if (
            debt_pct is None
            or inc_pct is None
            or (cash_screen_enabled and cash_pct is None)
        ):
            return PluginVote(
                plugin=self.name,
                vote=Vote.ABSTAIN,
                reason="zero denominator in a ratio",
                metrics=metrics,
            )

        failures: list[str] = []
        if debt_pct >= debt_lim - margin:
            failures.append("debt")
        if (
            cash_screen_enabled
            and cash_pct is not None
            and cash_pct > cash_lim - margin
        ):
            failures.append("cash")
        if inc_pct > inc_lim - margin:
            failures.append("interest-income")

        # Missing AR: skip, do not ABSTAIN.
        receivables = f.accounts_receivable
        rec_lim = section.get("receivables_to_market_cap_pct")
        if receivables is not None:
            rec_pct = _ratio_pct(float(receivables), f.market_cap)
            metrics["receivables_pct"] = rec_pct
            if (
                receivables_screen_enabled
                and rec_lim is not None
                and rec_pct is not None
                and rec_pct > float(rec_lim) - margin
            ):
                failures.append("receivables")

        if failures:
            return PluginVote(
                plugin=self.name,
                vote=Vote.FAIL,
                reason="exceeds policy limits: " + ", ".join(failures),
                metrics=metrics,
            )
        return PluginVote(
            plugin=self.name,
            vote=Vote.PASS,
            reason="ratios within policy limits",
            metrics=metrics,
        )
