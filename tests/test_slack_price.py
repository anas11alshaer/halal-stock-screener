"""Tests for the Slack /price live-price command."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from channels.slack import HELP_MESSAGE, SlackChannel
from config import MAX_TICKERS_PER_REQUEST
from prices.base import PriceProvider, PriceQuote


class FakeScreener:
    def __init__(self):
        self.text_calls = []

    async def screen_text(self, text, user_id=None):
        self.text_calls.append((text, user_id))
        raise AssertionError("screen_text must not run for /price input")


class FakePriceProvider(PriceProvider):
    def __init__(self, quotes=None):
        self.seen = None
        self.quotes = quotes or []

    async def get_prices(self, tickers):
        self.seen = list(tickers)
        return self.quotes


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
        self.kwargs = {}

    async def __call__(self, text, **kwargs):
        self.messages.append(text)
        self.kwargs = kwargs


def _quote(ticker, price=150.25, currency="USD", change_pct=1.23, error=None):
    return PriceQuote(
        ticker=ticker,
        price=price,
        currency=currency,
        change_pct=change_pct,
        error=error,
        quote_url=f"https://finance.yahoo.com/quote/{ticker}",
    )


def _channel(screener=None, provider=None):
    return SlackChannel(
        screening_service=screener or FakeScreener(),
        price_provider=provider or FakePriceProvider([_quote("AAPL")]),
    )


def test_price_command_returns_prices():
    screener = FakeScreener()
    channel = _channel(screener=screener)
    ack, respond = FakeAck(), FakeRespond()
    command = {"text": "AAPL", "user_id": "U3", "channel_id": "C3"}

    asyncio.run(channel.price_command(ack, respond, command, FakeClient()))

    assert ack.called
    assert screener.text_calls == []
    assert "150.25" in respond.messages[0]
    assert "USD" in respond.messages[0]
    assert "finance.yahoo.com/quote/AAPL" in respond.messages[0]
    assert respond.kwargs == {"response_type": "ephemeral"}


def test_price_command_empty_args_shows_usage():
    provider = FakePriceProvider()
    channel = _channel(provider=provider)
    ack, respond = FakeAck(), FakeRespond()
    command = {"text": "  ", "user_id": "U3", "channel_id": "C3"}

    asyncio.run(channel.price_command(ack, respond, command, FakeClient()))

    assert ack.called
    assert provider.seen is None
    assert respond.messages == ["Usage: `/price AAPL MSFT`"]


def test_price_command_caps_at_max_tickers():
    provider = FakePriceProvider([])
    channel = _channel(provider=provider)
    ack, respond = FakeAck(), FakeRespond()
    args = " ".join(f"T{i}" for i in range(MAX_TICKERS_PER_REQUEST + 5))
    command = {"text": args, "user_id": "U3", "channel_id": "C3"}

    asyncio.run(channel.price_command(ack, respond, command, FakeClient()))

    assert len(provider.seen) == MAX_TICKERS_PER_REQUEST
    assert str(MAX_TICKERS_PER_REQUEST) in respond.messages[0]


def test_price_command_renders_per_ticker_error():
    provider = FakePriceProvider(
        [
            _quote("AAPL"),
            _quote("NOPE", price=None, currency=None, change_pct=None, error="Unknown ticker"),
        ]
    )
    channel = _channel(provider=provider)
    ack, respond = FakeAck(), FakeRespond()
    command = {"text": "AAPL NOPE", "user_id": "U3", "channel_id": "C3"}

    asyncio.run(channel.price_command(ack, respond, command, FakeClient()))

    assert provider.seen == ["AAPL", "NOPE"]
    assert "150.25" in respond.messages[0]
    assert "NOPE" in respond.messages[0] and "Unknown ticker" in respond.messages[0]


def test_mention_price_routes_to_prices_not_screen():
    screener = FakeScreener()
    provider = FakePriceProvider([_quote("AAPL"), _quote("MSFT", price=300.5)])
    channel = SlackChannel(screening_service=screener, price_provider=provider)
    say, client = FakeSay(), FakeClient()
    event = {"text": "<@U0BOT> /price AAPL MSFT", "user": "U9", "channel": "C1"}

    asyncio.run(channel.handle_mention(event, say, client))

    assert provider.seen == ["AAPL", "MSFT"]
    assert screener.text_calls == []
    assert say.messages[0] == "Fetching prices..."
    assert "150.25" in client.updates[0]["text"]
    assert "<b>" not in client.updates[0]["text"]


def test_message_price_routes_to_prices_not_screen():
    screener = FakeScreener()
    provider = FakePriceProvider([_quote("AAPL")])
    channel = SlackChannel(screening_service=screener, price_provider=provider)
    say, client = FakeSay(), FakeClient()
    event = {"channel_type": "im", "text": "/price AAPL", "user": "U2"}

    asyncio.run(channel.handle_message(event, say, client))

    assert provider.seen == ["AAPL"]
    assert screener.text_calls == []
    assert "*Live Prices*" in client.updates[0]["text"]


def test_plain_ticker_still_screens():
    class ScreeningScreener:
        def __init__(self):
            self.text_calls = []

        async def screen_text(self, text, user_id=None):
            self.text_calls.append((text, user_id))

            class _Response:
                @staticmethod
                def format_messages():
                    return ["<b>done</b>"]

            return _Response()

    screener = ScreeningScreener()
    provider = FakePriceProvider()
    channel = SlackChannel(screening_service=screener, price_provider=provider)
    say, client = FakeSay(), FakeClient()
    event = {"channel_type": "im", "text": "AAPL", "user": "U2"}

    asyncio.run(channel.handle_message(event, say, client))

    assert screener.text_calls == [("AAPL", "U2")]
    assert provider.seen is None


def test_help_lists_price():
    assert "/price" in HELP_MESSAGE
