"""Service entry point: health server plus configured delivery channel."""

import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from config import LOG_FILE, LOG_LEVEL
from plugins import load_delivery_channel

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
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass


def start_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    logger.info("Health check server running on port %d", port)
    server.serve_forever()


def main():
    threading.Thread(target=start_health_server, daemon=True).start()
    load_delivery_channel().run()


if __name__ == "__main__":
    main()
