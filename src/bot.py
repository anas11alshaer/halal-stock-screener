"""Service entry point: health server plus configured delivery channel."""

import asyncio
import hmac
import json
import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import config
from config import LOG_FILE, LOG_LEVEL
from plugins import load_delivery_channels

log_handlers = [logging.StreamHandler(sys.stdout)]
try:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    log_handlers.append(logging.FileHandler(LOG_FILE))
except OSError:
    pass

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=log_handlers,
)
logger = logging.getLogger(__name__)

# httpx request URLs for Telegram contain the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._respond_text(200, "OK")
        elif parsed.path == "/screen":
            self._handle_screen(parse_qs(parsed.query))
        else:
            self._respond_text(404, "Not found")

    def _handle_screen(self, params):
        from scrapers import STATUS_TEXT, ResultState

        token = config.SCREEN_API_TOKEN
        if not token:
            self._respond_json(403, {"error": "screen endpoint disabled; set SCREEN_API_TOKEN"})
            return
        if not hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {token}"):
            self._respond_json(401, {"error": "unauthorized"})
            return
        values = params.get("ticker", [])
        if not values or not values[0].strip():
            self._respond_json(400, {"error": "missing ticker query parameter"})
            return
        ticker = values[0].strip()
        try:
            response = asyncio.run(self._screener().screen_tickers([ticker], user_id=None))
        except Exception:
            logger.exception("Screening request for %s failed", ticker)
            self._respond_json(502, {"error": "screening failed"})
            return
        if response.error:
            self._respond_json(200, {"error": response.error})
            return
        result = response.results[0]
        if result.is_provisional:
            confidence = "provisional"
        elif result.confirmation_count >= 2:
            confidence = "confirmed"
        else:
            confidence = "unresolved"
        provider_results = response.source_results.get(result.ticker, {})
        checked_at = min(
            (
                provider_result.checked_at
                for provider_result in provider_results.values()
                if provider_result.checked_at and provider_result.state == ResultState.SUCCESS
            ),
            default=None,
        )
        sources = {
            provider_id: {
                "status": provider_result.status.value if provider_result.status else None,
                "state": provider_result.state.value,
                "evidence": provider_result.evidence,
                "methodology": provider_result.methodology,
                "url": provider_result.url,
                "error_message": provider_result.error_message,
                "checked_at": provider_result.checked_at,
            }
            for provider_id, provider_result in provider_results.items()
            if provider_result.state != ResultState.RATE_LIMITED
        }
        self._respond_json(
            200,
            {
                "ticker": result.ticker,
                "company_name": result.company_name,
                "asset_type": result.asset_type.value,
                "status": result.status.value,
                "status_text": STATUS_TEXT.get(result.status),
                "is_provisional": result.is_provisional,
                "confirmation_count": result.confirmation_count,
                "confidence": confidence,
                "details": result.details,
                "checked_at": checked_at,
                "sources": sources,
            },
        )

    def _screener(self):
        if getattr(self.server, "_screener", None) is None:
            factory = self.server.screener_factory or self._default_screener_factory
            self.server._screener = factory()
        return self.server._screener

    @staticmethod
    def _default_screener_factory():
        from screener import StockScreener

        return StockScreener()

    def _respond_text(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def _respond_json(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload, default=str).encode("utf-8"))

    def log_message(self, format, *args):
        pass


def start_health_server(port: int | None = None, screener_factory=None) -> None:
    port = port if port is not None else int(os.environ.get("PORT", 8080))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    server.screener_factory = screener_factory
    logger.info("Health check server running on port %d", port)
    server.serve_forever()


def main():
    threading.Thread(target=start_health_server, daemon=True).start()
    channels = load_delivery_channels()
    if len(channels) == 1:
        channels[0].run()
        return
    # Each channel's run() owns its event loop, so every transport gets a thread.
    threads = [threading.Thread(target=channel.run, daemon=True) for channel in channels]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


if __name__ == "__main__":
    main()
