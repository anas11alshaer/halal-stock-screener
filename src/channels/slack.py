"""Slack delivery-channel adapter (Socket Mode)."""

import asyncio
import html
import logging
import re
import sys

import httpx
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

from channels.telegram import _render_price_message
from config import MAX_TICKERS_PER_REQUEST, SLACK_APP_TOKEN, SLACK_BOT_TOKEN
from plugins import load_price_provider
from screener import StockScreener

logger = logging.getLogger(__name__)

_MENTION_RE = re.compile(r"<@[A-Z0-9]+>")
_PRICE_RE = re.compile(r"^/price(\s|$)", re.IGNORECASE)
_TOKEN_RE = re.compile(r'<a href="([^"]+)">([^<]*)</a>|</?(b|code|i|pre)>')
_TAG_MARKUP = {"b": "*", "code": "`", "i": "_", "pre": "```"}

HELP_MESSAGE = """*Halal Stock Screener*

Check stocks and ETFs using configurable independent screening sources.
Confirmed sources vote; a tied vote is treated as Not Halal.
One confirmed source is clearly marked provisional.

*Usage*
Send tickers or a company/fund name: `AAPL`, `BRK.B`, or `Apple`
Or send an image with stock tickers in a DM.

*Commands*
`/check AAPL MSFT` - Check tickers
`/check Apple` - Resolve a company name
`/price AAPL MSFT` - Live prices
`/history` - Recent checks
`/stats` - Your statistics"""


def _slack_escape(text: str) -> str:
    """Re-encode HTML-escaped text so Slack never treats it as markup."""
    text = html.unescape(text)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_slack_mrkdwn(message: str) -> str:
    """Render a Telegram-HTML screening message as Slack mrkdwn.

    Only the tags emitted by the screener are treated as markup; all other text
    is kept escaped so provider data cannot inject Slack mentions or links.
    """
    parts = []
    pos = 0
    for match in _TOKEN_RE.finditer(message):
        parts.append(_slack_escape(message[pos : match.start()]))
        href, label, tag = match.groups()
        if tag:
            parts.append(_TAG_MARKUP[tag])
        else:
            href = _slack_escape(href).replace("|", "%7C")
            label = _slack_escape(label).replace("|", " ")
            parts.append(f"<{href}|{label}>" if label else f"<{href}>")
        pos = match.end()
    parts.append(_slack_escape(message[pos:]))
    return "".join(parts)


class SlackChannel:
    """Receive Slack events over Socket Mode and deliver screening responses."""

    def __init__(self, screening_service=None, price_provider=None):
        self.screener = screening_service or StockScreener()
        self._price_provider = price_provider
        self.app = None

    @property
    def price_provider(self):
        if self._price_provider is None:
            self._price_provider = load_price_provider()
        return self._price_provider

    async def handle_mention(self, event, say, client):
        text = _MENTION_RE.sub("", event.get("text", "")).strip()
        if text.lower().startswith("check "):
            text = text[6:].strip()
        if not text or text.lower() == "help":
            await say(HELP_MESSAGE)
            return
        if _PRICE_RE.match(text):
            await self._price_text_and_reply(text, say, client)
            return
        await self._screen_text_and_reply(text, event["user"], say, client)

    async def handle_message(self, event, say, client, context=None):
        if event.get("bot_id") or not event.get("user"):
            return
        channel_type = event.get("channel_type")
        if channel_type not in ("im", "channel", "group", "mpim"):
            return
        subtype = event.get("subtype")
        if subtype == "file_share":
            if channel_type != "im":
                return
            await self._handle_files(event, say, client)
            return
        if subtype is not None:
            return
        text = (event.get("text") or "").strip()
        if not text:
            return
        # @mentions also arrive as message events; handle_mention owns those.
        bot_user_id = None if context is None else context.get("bot_user_id")
        if bot_user_id and f"<@{bot_user_id}>" in text:
            return
        if text.lower() in ("help", "start", "/start", "/help"):
            await say(HELP_MESSAGE)
            return
        if _PRICE_RE.match(text):
            await self._price_text_and_reply(text, say, client)
            return
        await self._screen_text_and_reply(text, event["user"], say, client)

    async def check_command(self, ack, respond, command, client):
        await ack()
        text = (command.get("text") or "").strip()
        if not text:
            await respond("Usage: `/check AAPL MSFT` or `/check Apple`")
            return
        response = await self.screener.screen_text(text, command["user_id"])
        await self._respond_with_response(response, respond, client, command)

    async def price_command(self, ack, respond, command, client):
        await ack()
        args = (command.get("text") or "").split()
        if not args:
            await respond("Usage: `/price AAPL MSFT`")
            return
        message = await self._fetch_price_message(args)
        await respond(text=message, response_type="ephemeral")

    async def history_command(self, ack, respond, command, client):
        await ack()
        history = self.screener.get_user_history(command["user_id"], limit=15)
        if not history:
            await respond("No history yet. Send a ticker to get started.")
            return
        icons = {
            "HALAL": "✅",
            "NOT_HALAL": "❌",
            "DOUBTFUL": "⚠️",
            "NOT_COVERED": "❓",
            "ERROR": "⚠️",
        }
        lines = ["*Recent Checks*", ""]
        for entry in history:
            provisional = " (provisional)" if entry.get("is_provisional") else ""
            lines.append(
                f"{icons.get(entry['status'], '❓')} `{entry['ticker']}` "
                f"{entry['checked_at'][:10]}{provisional}"
            )
        await respond("\n".join(lines))

    async def stats_command(self, ack, respond, command, client):
        await ack()
        stats = self.screener.get_user_stats(command["user_id"])
        if stats["total_checks"] == 0:
            await respond("No statistics yet. Send a ticker to get started.")
            return
        lines = [
            "*Your Statistics*",
            "",
            f"Total checks: *{stats['total_checks']}*",
            f"Unique securities: *{stats['unique_tickers']}*",
        ]
        labels = {
            "HALAL": "✅ Halal",
            "NOT_HALAL": "❌ Not Halal",
            "DOUBTFUL": "⚠️ Doubtful",
            "NOT_COVERED": "❓ Not Covered",
        }
        for status, count in stats["status_breakdown"].items():
            if count and status in labels:
                lines.append(f"{labels[status]}: {count}")
        await respond("\n".join(lines))

    async def handle_error(self, error):
        logger.error("Slack event failed: %s", error)

    async def _screen_text_and_reply(self, text, user_id, say, client):
        status = await say("Checking...")
        response = await self.screener.screen_text(text, user_id)
        await self._deliver_response(response, status, say, client)

    async def _price_text_and_reply(self, text, say, client):
        args = text.split()[1:]
        if not args:
            await say("Usage: `/price AAPL MSFT`")
            return
        status = await say("Fetching prices...")
        message = await self._fetch_price_message(args)
        await client.chat_update(channel=status["channel"], ts=status["ts"], text=message)

    async def _fetch_price_message(self, args):
        tickers = [arg.strip().upper() for arg in args if arg.strip()]
        truncated = len(tickers) > MAX_TICKERS_PER_REQUEST
        quotes = await self.price_provider.get_prices(tickers[:MAX_TICKERS_PER_REQUEST])
        return to_slack_mrkdwn(_render_price_message(quotes, truncated))

    async def _handle_files(self, event, say, client):
        files = event.get("files") or []
        image = next((f for f in files if str(f.get("mimetype", "")).startswith("image/")), None)
        if image is None:
            await say("Send an image containing stock tickers.")
            return
        url = image.get("url_private_download") or image.get("url_private")
        if not url and image.get("id"):
            info = await client.files_info(file=image["id"])
            url = info["file"].get("url_private_download") or info["file"].get("url_private")
        if not url:
            await say("Could not access that file.")
            return
        status = await say("Analyzing image...")
        try:
            image_data = await self._download_file(url)
        except Exception:
            logger.exception("Slack file download failed")
            await client.chat_update(
                channel=status["channel"],
                ts=status["ts"],
                text="Failed to download the image.",
            )
            return
        response = await self.screener.screen_image(image_data, event["user"])
        await self._deliver_response(response, status, say, client)

    @staticmethod
    async def _download_file(url: str) -> bytes:
        async with httpx.AsyncClient(timeout=30) as http:
            response = await http.get(url, headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"})
            response.raise_for_status()
            return response.content

    async def _deliver_response(self, response, status, say, client):
        messages = [to_slack_mrkdwn(m) for m in response.format_messages()]
        await client.chat_update(channel=status["channel"], ts=status["ts"], text=messages[0])
        for message in messages[1:]:
            await say(message)

    @staticmethod
    async def _respond_with_response(response, respond, client, command):
        messages = [to_slack_mrkdwn(m) for m in response.format_messages()]
        await respond(text=messages[0], response_type="ephemeral")
        for message in messages[1:]:
            await client.chat_postEphemeral(
                channel=command["channel_id"], user=command["user_id"], text=message
            )

    def run(self):
        if not SLACK_BOT_TOKEN or not SLACK_APP_TOKEN:
            logger.error("SLACK_BOT_TOKEN and SLACK_APP_TOKEN must be set")
            sys.exit(1)
        self.app = AsyncApp(token=SLACK_BOT_TOKEN)
        self.app.event("app_mention")(self.handle_mention)
        self.app.event("message")(self.handle_message)
        self.app.command("/check")(self.check_command)
        self.app.command("/price")(self.price_command)
        self.app.command("/history")(self.history_command)
        self.app.command("/stats")(self.stats_command)
        self.app.error(self.handle_error)
        self.screener.clear_expired_cache()
        logger.info("Slack channel is running")
        asyncio.run(self._serve())

    async def _serve(self):
        # AsyncSocketModeHandler builds an aiohttp session at construction,
        # so it must be created inside the running event loop.
        await AsyncSocketModeHandler(self.app, SLACK_APP_TOKEN).start_async()
