"""Config boundary: secrets in .env, knobs in config/app.toml."""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import config  # noqa: E402


def test_app_toml_has_no_secret_keys() -> None:
    data = config.load_app_toml()
    keys = set(config._walk_keys(data))
    assert keys.isdisjoint(config._SECRET_TOML_KEYS)


def test_screening_policy_has_no_secret_keys() -> None:
    with config.POLICY_PATH.open("rb") as fh:
        data = tomllib.load(fh)
    keys = set(config._walk_keys(data))
    assert keys.isdisjoint(config._SECRET_TOML_KEYS)


def test_load_app_toml_rejects_secret_key_names(tmp_path: Path) -> None:
    path = tmp_path / "app.toml"
    path.write_text('nvidia_api_key = "leak"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="must not contain secrets"):
        config.load_app_toml(path)


def test_cache_ttl_comes_from_app_toml() -> None:
    assert config.CACHE_TTL_HOURS == 24
    assert "CACHE_TTL_HOURS" not in config._SECRET_NAMES
