"""Tests for the internal GET /screen JSON endpoint."""

import sys
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import config
import httpx

from bot import HealthHandler
from scrapers import AssetType, ComplianceStatus, ResultState, ScreeningResult
from screener import ScreenResponse


def _screen_response(ticker="EXAMPLE"):
    verdict = ScreeningResult(
        ticker=ticker,
        status=ComplianceStatus.HALAL,
        source="combined",
        company_name="Example Security",
        asset_type=AssetType.STOCK,
        state=ResultState.SUCCESS,
        is_provisional=False,
        confirmation_count=2,
        checked_at="2026-09-03T00:00:00+00:00",
    )
    provider_result = ScreeningResult(
        ticker=ticker,
        status=ComplianceStatus.HALAL,
        source="fakeprovider",
        state=ResultState.SUCCESS,
        evidence="Holdings screened against a certified Shariah benchmark",
    )
    return ScreenResponse(
        [verdict], [False], source_results={ticker: {"fakeprovider": provider_result}}
    )


class FakeScreener:
    def __init__(self, response=None):
        self.response = response if response is not None else _screen_response()

    async def screen_tickers(self, tickers, user_id=None):
        return self.response


@contextmanager
def _screen_server(screener):
    server = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
    server.screener_factory = lambda: screener
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_healthcheck_root_still_returns_ok():
    with _screen_server(FakeScreener()) as base_url:
        response = httpx.get(f"{base_url}/")
    assert response.status_code == 200
    assert response.text == "OK"


def test_screen_returns_json_verdict_with_sources(monkeypatch):
    monkeypatch.setattr(config, "SCREEN_API_TOKEN", "")
    with _screen_server(FakeScreener()) as base_url:
        response = httpx.get(f"{base_url}/screen", params={"ticker": "EXAMPLE"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    payload = response.json()
    assert payload["ticker"] == "EXAMPLE"
    assert payload["company_name"] == "Example Security"
    assert payload["asset_type"] == "STOCK"
    assert payload["status"] == "HALAL"
    assert payload["status_text"] == "Halal"
    assert payload["is_provisional"] is False
    assert payload["confirmation_count"] == 2
    assert payload["confidence"] == "confirmed"
    assert payload["details"] is None
    assert payload["checked_at"] == "2026-09-03T00:00:00+00:00"
    source = payload["sources"]["fakeprovider"]
    assert source["status"] == "HALAL"
    assert source["state"] == "SUCCESS"
    assert source["evidence"] == "Holdings screened against a certified Shariah benchmark"
    assert source["methodology"] is None
    assert source["url"] is None
    assert source["error_message"] is None


def test_screen_requires_bearer_token_when_configured(monkeypatch):
    monkeypatch.setattr(config, "SCREEN_API_TOKEN", "s3cret")
    with _screen_server(FakeScreener()) as base_url:
        response = httpx.get(f"{base_url}/screen", params={"ticker": "EXAMPLE"})
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}


def test_screen_accepts_correct_bearer_token(monkeypatch):
    monkeypatch.setattr(config, "SCREEN_API_TOKEN", "s3cret")
    with _screen_server(FakeScreener()) as base_url:
        response = httpx.get(
            f"{base_url}/screen",
            params={"ticker": "EXAMPLE"},
            headers={"Authorization": "Bearer s3cret"},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "HALAL"


def test_screen_rejects_wrong_token(monkeypatch):
    monkeypatch.setattr(config, "SCREEN_API_TOKEN", "s3cret")
    with _screen_server(FakeScreener()) as base_url:
        response = httpx.get(
            f"{base_url}/screen",
            params={"ticker": "EXAMPLE"},
            headers={"Authorization": "Bearer nope"},
        )
    assert response.status_code == 401


def test_screen_open_when_no_token_configured(monkeypatch):
    monkeypatch.setattr(config, "SCREEN_API_TOKEN", "")
    with _screen_server(FakeScreener()) as base_url:
        response = httpx.get(f"{base_url}/screen", params={"ticker": "EXAMPLE"})
    assert response.status_code == 200
    assert response.json()["confidence"] == "confirmed"


def test_screen_missing_ticker_returns_400(monkeypatch):
    monkeypatch.setattr(config, "SCREEN_API_TOKEN", "")
    with _screen_server(FakeScreener()) as base_url:
        missing = httpx.get(f"{base_url}/screen")
        empty = httpx.get(f"{base_url}/screen", params={"ticker": "   "})
    assert missing.status_code == 400
    assert missing.json() == {"error": "missing ticker query parameter"}
    assert empty.status_code == 400
    assert empty.json() == {"error": "missing ticker query parameter"}


def test_unknown_path_returns_404():
    with _screen_server(FakeScreener()) as base_url:
        response = httpx.get(f"{base_url}/unknown")
    assert response.status_code == 404


def test_screen_resolution_error_returns_200_with_error_field(monkeypatch):
    monkeypatch.setattr(config, "SCREEN_API_TOKEN", "")
    with _screen_server(FakeScreener(ScreenResponse([], [], error="ambiguous"))) as base_url:
        response = httpx.get(f"{base_url}/screen", params={"ticker": "EXAMPLE"})
    assert response.status_code == 200
    assert response.json() == {"error": "ambiguous"}
