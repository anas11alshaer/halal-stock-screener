"""Tests for the Telegram delivery-channel plugin."""

import asyncio
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import channels.telegram as telegram_module
from channels.telegram import TelegramChannel


class FakeScreener:
    def clear_expired_cache(self):
        pass


class FakeApplication:
    def __init__(self):
        self.polling_kwargs = None
        self.loop_at_polling = None

    def add_handler(self, handler):
        pass

    def add_error_handler(self, handler):
        pass

    def run_polling(self, **kwargs):
        self.polling_kwargs = kwargs
        self.loop_at_polling = asyncio.get_event_loop_policy().get_event_loop()


class FakeBuilder:
    def __init__(self, application):
        self.application = application

    def token(self, token):
        return self

    def build(self):
        return self.application


def _patch_application(monkeypatch, application):
    monkeypatch.setattr(telegram_module, "TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(
        telegram_module.Application, "builder", staticmethod(lambda: FakeBuilder(application))
    )


def test_run_in_worker_thread_installs_loop_and_skips_signals(monkeypatch):
    application = FakeApplication()
    _patch_application(monkeypatch, application)
    errors = []

    def target():
        try:
            TelegramChannel(screening_service=FakeScreener()).run()
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=target)
    thread.start()
    thread.join(timeout=5)

    assert errors == []
    assert application.polling_kwargs["stop_signals"] is None
    assert application.loop_at_polling is not None
    application.loop_at_polling.close()


def test_run_on_main_thread_keeps_default_signals(monkeypatch):
    application = FakeApplication()
    _patch_application(monkeypatch, application)
    asyncio.set_event_loop(asyncio.new_event_loop())

    TelegramChannel(screening_service=FakeScreener()).run()

    assert "stop_signals" not in application.polling_kwargs
    asyncio.get_event_loop().close()
    asyncio.set_event_loop(None)
