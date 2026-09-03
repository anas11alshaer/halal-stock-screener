"""Named fusion of plugin votes. Conjunction is fail-closed."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from plugins.base import PluginVote, Vote

HALAL = "HALAL"
NOT_HALAL = "NOT_HALAL"


def fuse_conjunction(votes: Mapping[str, PluginVote], required: Sequence[str]) -> str:
    """any FAIL → NOT HALAL; required all PASS (none ABSTAIN) → HALAL; else NOT HALAL.

    Empty required (e.g. look-through plugin disabled) must not vacuously HALAL.
    """
    if not required:
        return NOT_HALAL
    if any(v.vote is Vote.FAIL for v in votes.values()):
        return NOT_HALAL
    for name in required:
        vote = votes.get(name)
        if vote is None or vote.vote is not Vote.PASS:
            return NOT_HALAL
    return HALAL


def fuse(
    fusion_name: str, votes: Mapping[str, PluginVote], required: Sequence[str]
) -> str:
    """Dispatch by policy fusion name. Unknown names must not silently pass."""
    if fusion_name != "conjunction":
        raise ValueError(
            f"Unsupported fusion {fusion_name!r}; policy must name a supported rule"
        )
    return fuse_conjunction(votes, required)


def required_plugin_names(
    *,
    fusion_section: dict,
    quote_type: str,
    ticker_in_halalwallet: bool,
    enabled: Sequence[str],
) -> list[str]:
    """Build the required set from policy (quote type + in-dataset plugins)."""
    by_type = fusion_section.get("required_plugins_by_quote_type") or {}
    typed = by_type.get(quote_type)
    if typed:
        required = list(typed)
    else:
        required = list(fusion_section.get("required_plugins") or [])

    if ticker_in_halalwallet:
        for name in fusion_section.get("add_required_when_in_dataset") or []:
            if name not in required:
                required.append(name)

    enabled_set = set(enabled)
    return [name for name in required if name in enabled_set]
