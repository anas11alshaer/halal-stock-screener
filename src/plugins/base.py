"""Plugin vote types for the fail-closed eval engine."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from policy import Policy


class Vote(Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    ABSTAIN = "ABSTAIN"


class FactState(str, Enum):
    FOUND = "found"
    ZERO = "zero"
    MISSING = "missing"
    DOUBTFUL = "doubtful"


@dataclass
class PluginVote:
    plugin: str
    vote: Vote
    reason: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class Fundamentals:
    ticker: str
    quote_type: str = "UNKNOWN"
    sector: str | None = None
    industry: str | None = None
    company_name: str | None = None
    market_cap: float | None = None
    total_debt: float | None = None
    cash_and_securities: float | None = None
    accounts_receivable: float | None = None
    interest_income: float | None = None
    revenue: float | None = None
    income_statement_present: bool = False
    segments: list = field(default_factory=list)
    fact_states: dict[str, FactState] = field(default_factory=dict)


@dataclass
class ScreenContext:
    ticker: str
    quote_type: str
    fundamentals: Fundamentals
    depth: int = 0


class Plugin(ABC):
    """One optional vote in the conjunction."""

    name: str

    def __init__(self, policy: Policy) -> None:
        self.policy = policy

    @abstractmethod
    async def vote(self, ctx: ScreenContext) -> PluginVote:
        """Return PASS / FAIL / ABSTAIN for ctx.ticker."""


def is_fund(quote_type: str, policy: Policy) -> bool:
    return quote_type.upper() in policy.fund_quote_types
