"""Load the committed screening policy from disk."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any


class Policy:
    """Dict wrapper so plugins read thresholds from the file, not literals."""

    def __init__(self, data: dict[str, Any], path: Path | None = None) -> None:
        self.raw = data
        self.path = path

    def section(self, name: str) -> dict[str, Any]:
        value = self.raw.get(name, {})
        if not isinstance(value, dict):
            return {}
        return value

    def get(self, *keys: str, default: Any = None) -> Any:
        cur: Any = self.raw
        for key in keys:
            if not isinstance(cur, dict) or key not in cur:
                return default
            cur = cur[key]
        return cur

    @property
    def enabled_plugins(self) -> list[str]:
        return list(self.raw.get("enabled_plugins") or [])

    @property
    def fusion_name(self) -> str:
        return str(self.get("fusion", "name", default="conjunction"))

    @property
    def required_plugins(self) -> list[str]:
        return list(self.get("fusion", "required_plugins", default=[]) or [])

    @property
    def add_required_when_in_dataset(self) -> list[str]:
        return list(
            self.get("fusion", "add_required_when_in_dataset", default=[]) or []
        )

    @property
    def required_plugins_by_quote_type(self) -> dict[str, list[str]]:
        raw = self.get("fusion", "required_plugins_by_quote_type", default={}) or {}
        return {str(k): list(v) for k, v in raw.items()}

    @property
    def fund_quote_types(self) -> set[str]:
        values = self.get("etf", "fund_quote_types", default=["ETF"]) or ["ETF"]
        return {str(v).upper() for v in values}

    def edgar_user_agent(self) -> str:
        """EDGAR User-Agent; fails closed without SEC_CONTACT_EMAIL."""
        import config as app_config

        name = str(
            self.section("sources").get("sec_user_agent_name") or "halal-stock-screener"
        ).strip()
        email = (app_config.SEC_CONTACT_EMAIL or "").strip()
        if not email or "@" not in email:
            raise RuntimeError(
                "Set SEC_CONTACT_EMAIL in .env to a real contact address. "
                "EDGAR rejects User-Agents without contact."
            )
        # SEC fair-access example: "Sample Company Name AdminContact@domain.com"
        return f"{name} {email}"

    def edgar_headers(self) -> dict[str, str]:
        """Headers EDGAR expects on www.sec.gov and data.sec.gov."""
        return {
            "User-Agent": self.edgar_user_agent(),
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate",
        }


def load_policy(path: Path | None = None) -> Policy:
    """Parse a TOML policy file."""
    if path is None:
        from config import POLICY_PATH

        path = POLICY_PATH
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Policy at {path} is not a TOML table")
    return Policy(data, path=path)
