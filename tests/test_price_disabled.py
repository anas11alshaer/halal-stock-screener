"""Tests for the disabled-by-default /price command kill switch."""

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import channels.slack as slack_module
import channels.telegram as telegram_module
from channels.slack import SlackChannel
from channels.telegram import TelegramChannel
from prices.base import PriceProvider


@pytest.fixture(autouse=True)
def _disable_price_command(monkeypatch):
    monkeypatch.setattr(telegram_module, "PRICE_COMMAND_ENABLED", False)
    monkeypatch.setattr(slack_module, "PRICE_COMMAND_ENABLED", False)


class StrictPriceProvider(PriceProvider):
    async def get_prices(self, tickers):
        raise AssertionError("price provider must not run while /price is disabled")


class StrictScreener:
    async def screen_text(self, text, user_id=None):
        raise AssertionError("screener must not run for /price input")


class FakeScreener:
    pass


class FakeMessage:
    def __init__(self):
        self.replies = []
        self.edits = []

    async def reply_text(self, text, parse_mode=None):
        self.replies.append(text)
        return self

    async def edit_text(self, text, parse_mode=None):
        self.edits.append(text)


class FakeUpdate:
    def __init__(self):
        self.message = FakeMessage()
        self.effective_user = SimpleNamespace(id=7)


class FakeContext:
    def __init__(self, args):
        self.args = args


class FakeSay:
    def __init__(self):
        self.messages = []

    async def __call__(self, text):
        self.messages.append(text)
        return {"channel": "D123", "ts": "1700.1"}


class FakeClient:
    def __init__(self):
        self.updates = []

    async def chat_update(self, **kwargs):
        self.updates.append(kwargs)


class FakeAck:
    def __init__(self):
        self.called = False

    async def __call__(self):
        self.called = True


class FakeRespond:
    def __init__(self):
        self.messages = []

    async def __call__(self, text, **kwargs):
        self.messages.append(text)


def _telegram_channel():
    return TelegramChannel(
        screening_service=FakeScreener(), price_provider=StrictPriceProvider()
    )


def _slack_channel():
    return SlackChannel(
        screening_service=StrictScreener(), price_provider=StrictPriceProvider()
    )


@pytest.mark.asyncio
async def test_telegram_price_replies_disabled_without_calling_provider():
    update = FakeUpdate()
    await _telegram_channel().price_command(update, FakeContext(["AAPL"]))
    assert len(update.message.replies) == 1
    assert "disabled" in update.message.replies[0]
    assert update.message.edits == []


@pytest.mark.asyncio
async def test_telegram_price_disabled_without_args_too():
    update = FakeUpdate()
    await _telegram_channel().price_command(update, FakeContext([]))
    assert len(update.message.replies) == 1
    assert "disabled" in update.message.replies[0]


@pytest.mark.asyncio
async def test_telegram_help_omits_price_when_disabled():
    update = FakeUpdate()
    await _telegram_channel().start_command(update, FakeContext([]))
    assert "/price" not in update.message.replies[0]
    assert "/check" in update.message.replies[0]


@pytest.mark.asyncio
async def test_telegram_help_lists_price_when_enabled(monkeypatch):
    monkeypatch.setattr(telegram_module, "PRICE_COMMAND_ENABLED", True)
    update = FakeUpdate()
    await _telegram_channel().start_command(update, FakeContext([]))
    assert "/price" in update.message.replies[0]


@pytest.mark.asyncio
async def test_slack_price_command_responds_disabled():
    channel = _slack_channel()
    ack, respond = FakeAck(), FakeRespond()
    command = {"text": "AAPL", "user_id": "U3", "channel_id": "C3"}
    await channel.price_command(ack, respond, command, FakeClient())
    assert ack.called
    assert len(respond.messages) == 1
    assert "disabled" in respond.messages[0]


@pytest.mark.asyncio
async def test_slack_mention_price_replies_disabled():
    channel = _slack_channel()
    say, client = FakeSay(), FakeClient()
    event = {"text": "<@U0BOT> /price AAPL", "user": "U9", "channel": "C1"}
    await channel.handle_mention(event, say, client)
    assert len(say.messages) == 1
    assert "disabled" in say.messages[0]
    assert client.updates == []


@pytest.mark.asyncio
async def test_slack_message_price_replies_disabled():
    channel = _slack_channel()
    say, client = FakeSay(), FakeClient()
    event = {"channel_type": "im", "text": "/price AAPL", "user": "U2"}
    await channel.handle_message(event, say, client)
    assert len(say.messages) == 1
    assert "disabled" in say.messages[0]
    assert client.updates == []


@pytest.mark.asyncio
async def test_slack_help_omits_price_when_disabled():
    channel = _slack_channel()
    say, client = FakeSay(), FakeClient()
    event = {"channel_type": "im", "text": "help", "user": "U2"}
    await channel.handle_message(event, say, client)
    assert "/price" not in say.messages[0]
    assert "/check" in say.messages[0]


@pytest.mark.asyncio
async def test_slack_help_lists_price_when_enabled(monkeypatch):
    monkeypatch.setattr(slack_module, "PRICE_COMMAND_ENABLED", True)
    channel = _slack_channel()
    say, client = FakeSay(), FakeClient()
    event = {"channel_type": "im", "text": "help", "user": "U2"}
    await channel.handle_message(event, say, client)
    assert "/price" in say.messages[0]


def test_price_implementation_retained():
    assert callable(TelegramChannel.price_command)
    assert callable(SlackChannel.price_command)
    assert callable(telegram_module._render_price_message)


def _price_flag_in_fresh_interpreter(tmp_path, value):
    env = {k: v for k, v in os.environ.items() if k != "PRICE_COMMAND_ENABLED"}
    if value is not None:
        env["PRICE_COMMAND_ENABLED"] = value
    env["PYTHONPATH"] = str(Path(__file__).parent.parent / "src")
    proc = subprocess.run(
        [sys.executable, "-c", "import config; print(config.PRICE_COMMAND_ENABLED)"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_price_command_disabled_by_default(tmp_path):
    assert _price_flag_in_fresh_interpreter(tmp_path, None) == "False"


def test_price_command_reenabled_via_env(tmp_path):
    assert _price_flag_in_fresh_interpreter(tmp_path, "true") == "True"
