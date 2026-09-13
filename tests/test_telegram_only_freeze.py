"""Telegram-only freeze: Slack delivery and /price are gone."""

import ast
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

ROOT = Path(__file__).parent.parent


class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, parse_mode=None):
        self.replies.append(text)
        return self


class FakeUpdate:
    def __init__(self):
        self.message = FakeMessage()
        self.effective_user = SimpleNamespace(id=7)


class FakeScreener:
    pass


def test_prices_module_removed():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("prices")


def test_slack_channel_removed():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("channels.slack")


def test_price_provider_loader_removed():
    import plugins

    assert not hasattr(plugins, "load_price_provider")


def test_price_and_slack_config_removed():
    import config

    for name in (
        "PRICE_PROVIDER_PLUGIN",
        "PRICE_COMMAND_ENABLED",
        "SLACK_BOT_TOKEN",
        "SLACK_APP_TOKEN",
    ):
        assert not hasattr(config, name), name


def test_telegram_has_no_price_command():
    from channels.telegram import TelegramChannel

    assert not hasattr(TelegramChannel, "price_command")


@pytest.mark.asyncio
async def test_telegram_help_omits_price():
    from channels.telegram import TelegramChannel

    update = FakeUpdate()
    await TelegramChannel(screening_service=FakeScreener()).start_command(update, None)
    assert "/price" not in update.message.replies[0]


def test_telegram_source_has_no_prices_import():
    tree = ast.parse((ROOT / "src" / "channels" / "telegram.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(not alias.name.startswith("prices") for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("prices")
            assert node.module != "plugins" or all(
                alias.name != "load_price_provider" for alias in node.names
            )


def test_slack_deps_removed_from_requirements():
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "slack-bolt" not in text
    assert "aiohttp" not in text


def test_retired_slack_plugin_path_is_ignored(monkeypatch):
    created = []

    class FakeChannel:
        def __init__(self):
            created.append(self)

    import channels.telegram as telegram_module
    from plugins import load_delivery_channels

    monkeypatch.setattr(telegram_module, "TelegramChannel", FakeChannel)
    channels = load_delivery_channels(
        ["channels.telegram:TelegramChannel", "channels.slack:SlackChannel"]
    )
    assert channels == created
    assert len(channels) == 1


def test_slack_only_plugin_list_raises():
    from plugins import load_delivery_channels

    with pytest.raises(ValueError, match="No delivery channels configured"):
        load_delivery_channels(["channels.slack:SlackChannel"])
