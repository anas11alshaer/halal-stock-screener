"""Tests for the Slack delivery-channel plugin."""

import asyncio
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from channels.slack import SlackChannel, to_slack_mrkdwn
from plugins import load_delivery_channels
from screener import ScreenResponse
from scrapers import AssetType, ComplianceStatus, ScreeningResult


class FakeScreener:
    def __init__(self, response):
        self.response = response
        self.text_calls = []
        self.image_calls = []
        self.history = []
        self.stats = {"total_checks": 0, "unique_tickers": 0, "status_breakdown": {}}

    async def screen_text(self, text, user_id=None):
        self.text_calls.append((text, user_id))
        return self.response

    async def screen_image(self, image_data, user_id=None):
        self.image_calls.append((image_data, user_id))
        return self.response

    def get_user_history(self, user_id, limit=20):
        return self.history

    def get_user_stats(self, user_id):
        return self.stats

    def clear_expired_cache(self):
        pass


class FakeClient:
    def __init__(self):
        self.updates = []
        self.ephemerals = []
        self.files = {}

    async def chat_update(self, **kwargs):
        self.updates.append(kwargs)

    async def chat_postEphemeral(self, **kwargs):
        self.ephemerals.append(kwargs)

    async def files_info(self, file):
        return {"file": self.files[file]}


class FakeSay:
    def __init__(self):
        self.messages = []

    async def __call__(self, text):
        self.messages.append(text)
        return {"channel": "D123", "ts": "1700.1"}


class FakeAck:
    def __init__(self):
        self.called = False

    async def __call__(self):
        self.called = True


class FakeRespond:
    def __init__(self):
        self.messages = []

    async def __call__(self, text):
        self.messages.append(text)


def halal_response() -> ScreenResponse:
    result = ScreeningResult(
        ticker="AAPL",
        status=ComplianceStatus.HALAL,
        source="musaffa",
        company_name="Apple Inc.",
        asset_type=AssetType.STOCK,
        confirmation_count=1,
        is_provisional=True,
    )
    return ScreenResponse(
        results=[result],
        from_cache=[False],
        source_results={"AAPL": {"musaffa": result}},
    )


def test_to_slack_mrkdwn_converts_telegram_html():
    html = (
        "<b>Apple Inc.</b> (<code>AAPL</code>) · Stock\n"
        "✅ verdict: <b>Halal</b>\n"
        '<a href="https://musaffa.com/stock/AAPL">Open source</a> &amp; more'
    )
    assert to_slack_mrkdwn(html) == (
        "*Apple Inc.* (`AAPL`) · Stock\n"
        "✅ verdict: *Halal*\n"
        "<https://musaffa.com/stock/AAPL|Open source> & more"
    )


def test_to_slack_mrkdwn_bare_link_without_text():
    assert to_slack_mrkdwn('<a href="https://x.test"></a>') == "<https://x.test>"


def test_mention_strips_bot_tag_and_screens():
    screener = FakeScreener(halal_response())
    channel = SlackChannel(screening_service=screener)
    say, client = FakeSay(), FakeClient()
    event = {"text": "<@U0BOT> AAPL", "user": "U9USER", "channel": "C1"}

    asyncio.run(channel.handle_mention(event, say, client))

    assert screener.text_calls == [("AAPL", "U9USER")]
    assert say.messages[0] == "Checking..."
    assert client.updates[0]["channel"] == "D123"
    assert "*Apple Inc.*" in client.updates[0]["text"]
    assert "<b>" not in client.updates[0]["text"]


def test_mention_without_text_shows_help():
    channel = SlackChannel(screening_service=FakeScreener(halal_response()))
    say, client = FakeSay(), FakeClient()

    asyncio.run(channel.handle_mention({"text": "<@U0BOT>", "user": "U9"}, say, client))

    assert "Halal Stock Screener" in say.messages[0]
    assert client.updates == []


def test_message_ignores_non_dm_and_bot_messages():
    screener = FakeScreener(halal_response())
    channel = SlackChannel(screening_service=screener)
    say, client = FakeSay(), FakeClient()

    for event in (
        {"channel_type": "channel", "text": "AAPL", "user": "U1"},
        {"channel_type": "im", "text": "AAPL", "bot_id": "B1"},
        {"channel_type": "im", "text": "AAPL", "user": "U1", "subtype": "message_changed"},
    ):
        asyncio.run(channel.handle_message(event, say, client))

    assert screener.text_calls == []
    assert say.messages == []


def test_dm_message_screens_text():
    screener = FakeScreener(halal_response())
    channel = SlackChannel(screening_service=screener)
    say, client = FakeSay(), FakeClient()
    event = {"channel_type": "im", "text": "MSFT", "user": "U2"}

    asyncio.run(channel.handle_message(event, say, client))

    assert screener.text_calls == [("MSFT", "U2")]


def test_check_command_empty_args_shows_usage():
    channel = SlackChannel(screening_service=FakeScreener(halal_response()))
    ack, respond = FakeAck(), FakeRespond()
    command = {"text": "  ", "user_id": "U3", "channel_id": "C3"}

    asyncio.run(channel.check_command(ack, respond, command, FakeClient()))

    assert ack.called
    assert respond.messages == ["Usage: `/check AAPL MSFT` or `/check Apple`"]


def test_check_command_responds_with_mrkdwn():
    channel = SlackChannel(screening_service=FakeScreener(halal_response()))
    ack, respond, client = FakeAck(), FakeRespond(), FakeClient()
    command = {"text": "AAPL", "user_id": "U3", "channel_id": "C3"}

    asyncio.run(channel.check_command(ack, respond, command, client))

    assert ack.called
    assert "*Apple Inc.*" in respond.messages[0]


def test_history_command_empty():
    channel = SlackChannel(screening_service=FakeScreener(halal_response()))
    ack, respond = FakeAck(), FakeRespond()
    command = {"user_id": "U4", "channel_id": "C4"}

    asyncio.run(channel.history_command(ack, respond, command, FakeClient()))

    assert respond.messages == ["No history yet. Send a ticker to get started."]


def test_load_delivery_channels_instantiates_each_path(monkeypatch):
    module = types.ModuleType("fake_channel_mod")

    class FakeChannel:
        pass

    module.FakeChannel = FakeChannel
    monkeypatch.setitem(sys.modules, "fake_channel_mod", module)

    channels = load_delivery_channels(["fake_channel_mod:FakeChannel"])

    assert len(channels) == 1
    assert isinstance(channels[0], FakeChannel)
