"""Eval-engine plugins enabled via the screening policy."""

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
from plugins.nport import Holding, NportHoldingsPlugin, NportReport
from plugins.ratios import RatiosPlugin

__all__ = [
    "ActivityPlugin",
    "Fundamentals",
    "HalalWalletPlugin",
    "Holding",
    "NportHoldingsPlugin",
    "NportReport",
    "Plugin",
    "PluginVote",
    "RatiosPlugin",
    "ScreenContext",
    "Vote",
    "is_fund",
]
