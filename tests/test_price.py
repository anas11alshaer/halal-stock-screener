"""Tests for the /price live-price plugin."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from channels.telegram import TelegramChannel
from config import MAX_TICKERS_PER_REQUEST
from plugins import load_price_provider
from prices.base import PriceProvider, PriceQuote
from prices.yahoo import YahooPriceProvider


class FakePriceProvider(PriceProvider):
    def __init__(self, quotes=None):
        self.seen = None
        self.quotes = quotes or []

    async def get_prices(self, tickers):
        self.seen = list(tickers)
        return self.quotes


def _quote(ticker, price=150.25, currency="USD", change_pct=1.23, error=None):
    return PriceQuote(
        ticker=ticker,
        price=price,
        currency=currency,
        change_pct=change_pct,
        error=error,
        quote_url=f"https://finance.yahoo.com/quote/{ticker}",
    )


def _fake_ticker(fast_info=None, exc=None):
    class FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        @property
        def fast_info(self):
            if exc is not None:
                raise exc
            return fast_info

    return FakeTicker


def test_quote_defaults_to_no_data():
    quote = PriceQuote(ticker="AAPL")
    assert quote.price is None
    assert quote.currency is None
    assert quote.change_pct is None
    assert quote.error is None


def test_fake_provider_conforms_to_abc():
    assert isinstance(FakePriceProvider(), PriceProvider)


@pytest.mark.asyncio
async def test_yahoo_returns_price_currency_and_change():
    fast_info = SimpleNamespace(last_price=150.25, currency="USD", previous_close=148.42)
    import prices.yahoo as yahoo_module

    with patch.object(yahoo_module.yf, "Ticker", _fake_ticker(fast_info)):
        quotes = await YahooPriceProvider().get_prices(["aapl"])
    assert len(quotes) == 1
    quote = quotes[0]
    assert quote.ticker == "AAPL"
    assert quote.price == 150.25
    assert quote.currency == "USD"
    assert quote.change_pct == pytest.approx((150.25 - 148.42) / 148.42 * 100)
    assert quote.error is None
    assert quote.quote_url == "https://finance.yahoo.com/quote/AAPL"


@pytest.mark.asyncio
async def test_yahoo_unknown_ticker_is_quote_error():
    import prices.yahoo as yahoo_module

    with patch.object(
        yahoo_module.yf,
        "Ticker",
        _fake_ticker(SimpleNamespace(last_price=None, currency=None, previous_close=None)),
    ):
        quotes = await YahooPriceProvider().get_prices(["NOPE123"])
    assert quotes[0].price is None
    assert quotes[0].error
    assert quotes[0].quote_url == "https://finance.yahoo.com/quote/NOPE123"


@pytest.mark.asyncio
async def test_yahoo_exception_is_quote_error_not_raise():
    import prices.yahoo as yahoo_module

    with patch.object(
        yahoo_module.yf, "Ticker", _fake_ticker(exc=Exception("boom"))
    ):
        quotes = await YahooPriceProvider().get_prices(["AAPL"])
    assert quotes[0].price is None
    assert "boom" in quotes[0].error


def test_loader_default_is_yahoo():
    assert isinstance(load_price_provider(), YahooPriceProvider)


def test_loader_override():
    provider = load_price_provider(plugin_path="test_price:FakePriceProvider")
    assert isinstance(provider, FakePriceProvider)


def test_loader_rejects_bad_path():
    with pytest.raises(ValueError, match="module:Class"):
        load_price_provider(plugin_path="no-colon-here")


def test_loader_rejects_wrong_type():
    with pytest.raises(TypeError, match="must extend PriceProvider"):
        load_price_provider(plugin_path="test_price:NotAProvider")


class NotAProvider:
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


class FakeScreener:
    def clear_expired_cache(self):
        pass


def _channel(provider):
    return TelegramChannel(screening_service=FakeScreener(), price_provider=provider)


@pytest.mark.asyncio
async def test_price_usage_on_empty_args():
    update = FakeUpdate()
    await _channel(FakePriceProvider()).price_command(update, FakeContext([]))
    assert len(update.message.replies) == 1
    assert "/price" in update.message.replies[0]
    assert "AAPL" in update.message.replies[0]


@pytest.mark.asyncio
async def test_price_multi_render_with_one_failure():
    provider = FakePriceProvider(
        [_quote("AAPL"), _quote("NOPE", price=None, currency=None, change_pct=None, error="Unknown ticker")]
    )
    update = FakeUpdate()
    await _channel(provider).price_command(update, FakeContext(["AAPL", "NOPE"]))
    assert provider.seen == ["AAPL", "NOPE"]
    body = update.message.edits[0]
    assert "finance.yahoo.com/quote/AAPL" in body
    assert "150.25" in body and "USD" in body
    assert "NOPE" in body and "Unknown ticker" in body


@pytest.mark.asyncio
async def test_price_caps_at_max_tickers():
    provider = FakePriceProvider([])
    update = FakeUpdate()
    args = [f"T{i}" for i in range(MAX_TICKERS_PER_REQUEST + 5)]
    await _channel(provider).price_command(update, FakeContext(args))
    assert len(provider.seen) == MAX_TICKERS_PER_REQUEST
    assert str(MAX_TICKERS_PER_REQUEST) in update.message.edits[0]


@pytest.mark.asyncio
async def test_price_escapes_html():
    provider = FakePriceProvider(
        [_quote("AAPL", error=None), _quote("<X>", price=None, currency=None, change_pct=None, error="<bad>")]
    )
    update = FakeUpdate()
    await _channel(provider).price_command(update, FakeContext(["AAPL", "<X>"]))
    body = update.message.edits[0]
    assert "<bad>" not in body and "&lt;bad&gt;" in body
    assert "&lt;X&gt;" in body
