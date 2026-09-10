"""Slack delivery-channel adapter (Socket Mode)."""

import asyncio
import html
import logging
import re
import sys

import httpx
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

from config import SLACK_APP_TOKEN, SLACK_BOT_TOKEN
from screener import StockScreener

logger = logging.getLogger(__name__)

_MENTION_RE = re.compile(r"<@[A-Z0-9]+>")
_BOLD_RE = re.compile(r"</?b>")
_CODE_RE = re.compile(r"</?code>")
_ITALIC_RE = re.compile(r"</?i>")
_PRE_RE = re.compile(r"</?pre>")
_LINK_RE = re.compile(r'<a href="([^"]+)">([^<]*)</a>')

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
`/history` - Recent checks
`/stats` - Your statistics"""


def to_slack_mrkdwn(message: str) -> str:
    """Render a Telegram-HTML screening message as Slack mrkdwn."""
    message = _LINK_RE.sub(
        lambda match: f"<{match.group(1)}|{match.group(2)}>"
        if match.group(2)
        else f"<{match.group(1)}>",
        message,
    )
    message = _BOLD_RE.sub("*", message)
    message = _CODE_RE.sub("`", message)
    message = _ITALIC_RE.sub("_", message)
    message = _PRE_RE.sub("```", message)
    return html.unescape(message)


class SlackChannel:
    """Receive Slack events over Socket Mode and deliver screening responses."""

    def __init__(self, screening_service=None):
        self.screener = screening_service or StockScreener()
        self.app = None

    async def handle_mention(self, event, say, client):
        text = _MENTION_RE.sub("", event.get("text", "")).strip()
        if text.lower().startswith("check "):
            text = text[6:].strip()
        if not text or text.lower() == "help":
            await say(HELP_MESSAGE)
            return
        await self._screen_text_and_reply(text, event["user"], say, client)

    async def handle_message(self, event, say, client):
        if event.get("bot_id") or event.get("channel_type") != "im":
            return
        subtype = event.get("subtype")
        if subtype == "file_share":
            await self._handle_files(event, say, client)
            return
        if subtype is not None or not event.get("user"):
            return
        text = (event.get("text") or "").strip()
        if not text:
            return
        if text.lower() in ("help", "start", "/start", "/help"):
            await say(HELP_MESSAGE)
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
        await respond(messages[0])
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
