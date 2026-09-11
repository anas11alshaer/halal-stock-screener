"""Telegram delivery-channel adapter."""

import asyncio
import html
import logging
import sys
import threading
from io import BytesIO

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import MAX_TICKERS_PER_REQUEST, TELEGRAM_BOT_TOKEN
from plugins import load_price_provider
from screener import StockScreener

logger = logging.getLogger(__name__)


class _PriceResponse:
    """Single-message wrapper so prices reuse `_deliver_response`."""

    def __init__(self, message: str):
        self._message = message

    def format_messages(self) -> list[str]:
        return [self._message]


def _render_price_message(quotes, truncated: bool) -> str:
    lines = ["<b>Live Prices</b>", ""]
    for quote in quotes:
        safe_ticker = html.escape(quote.ticker)
        if quote.quote_url:
            link = f'<a href="{html.escape(quote.quote_url, quote=True)}">{safe_ticker}</a>'
        else:
            link = f"<code>{safe_ticker}</code>"
        if quote.error is not None or quote.price is None:
            lines.append(f"{link}: ⚠️ {html.escape(quote.error or 'No price available')}")
            continue
        parts = [str(quote.price)]
        if quote.currency:
            parts.append(html.escape(quote.currency))
        if quote.change_pct is not None:
            sign = "+" if quote.change_pct >= 0 else ""
            parts.append(f"({sign}{quote.change_pct:.2f}%)")
        lines.append(f"{link}: {' '.join(parts)}")
    if truncated:
        lines.append(f"<i>Showing first {MAX_TICKERS_PER_REQUEST} tickers.</i>")
    return "\n".join(lines)


class TelegramChannel:
    """Receive Telegram updates and deliver rendered screening responses."""

    def __init__(self, screening_service=None, price_provider=None):
        self.screener = screening_service or StockScreener()
        self._price_provider = price_provider

    @property
    def price_provider(self):
        if self._price_provider is None:
            self._price_provider = load_price_provider()
        return self._price_provider

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = """<b>Halal Stock Screener</b>

Check stocks and ETFs using configurable independent screening sources.
Confirmed sources vote; a tied vote is treated as Not Halal.
One confirmed source is clearly marked provisional.

<b>Usage</b>
Send tickers or a company/fund name: <code>AAPL</code>, <code>BRK.B</code>, or <code>Apple</code>
Or send an image with stock tickers.

<b>Commands</b>
/check <code>AAPL MSFT</code> - Check tickers
/check <code>Apple</code> - Resolve a company name
/price <code>AAPL MSFT</code> - Live prices
/history - Recent checks
/stats - Your statistics"""
        await update.message.reply_text(message, parse_mode="HTML")

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.start_command(update, context)

    async def check_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text(
                "Usage: <code>/check AAPL MSFT</code> or <code>/check Apple</code>",
                parse_mode="HTML",
            )
            return
        status_message = await update.message.reply_text("Checking...")
        response = await self.screener.screen_text(" ".join(context.args), update.effective_user.id)
        await self._deliver_response(status_message, update, response)

    async def price_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text(
                "Usage: <code>/price AAPL MSFT</code>",
                parse_mode="HTML",
            )
            return
        tickers = [arg.strip().upper() for arg in context.args if arg.strip()]
        truncated = len(tickers) > MAX_TICKERS_PER_REQUEST
        tickers = tickers[:MAX_TICKERS_PER_REQUEST]
        status_message = await update.message.reply_text("Fetching prices...")
        quotes = await self.price_provider.get_prices(tickers)
        await self._deliver_response(
            status_message, update, _PriceResponse(_render_price_message(quotes, truncated))
        )

    async def history_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        history = self.screener.get_user_history(update.effective_user.id, limit=15)
        if not history:
            await update.message.reply_text("No history yet. Send a ticker to get started.")
            return
        icons = {
            "HALAL": "✅",
            "NOT_HALAL": "❌",
            "DOUBTFUL": "⚠️",
            "NOT_COVERED": "❓",
            "ERROR": "⚠️",
        }
        lines = ["<b>Recent Checks</b>", ""]
        for entry in history:
            provisional = " (provisional)" if entry.get("is_provisional") else ""
            lines.append(
                f"{icons.get(entry['status'], '❓')} <code>{entry['ticker']}</code> "
                f"{entry['checked_at'][:10]}{provisional}"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        stats = self.screener.get_user_stats(update.effective_user.id)
        if stats["total_checks"] == 0:
            await update.message.reply_text("No statistics yet. Send a ticker to get started.")
            return
        lines = [
            "<b>Your Statistics</b>",
            "",
            f"Total checks: <b>{stats['total_checks']}</b>",
            f"Unique securities: <b>{stats['unique_tickers']}</b>",
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
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()
        if not text or text.startswith("/"):
            return
        status_message = await update.message.reply_text("Checking...")
        response = await self.screener.screen_text(text, update.effective_user.id)
        await self._deliver_response(status_message, update, response)

    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        status_message = await update.message.reply_text("Analyzing image...")
        photos = update.message.photo
        photo = photos[min(len(photos) - 1, max(1, len(photos) // 2))]
        telegram_file = await context.bot.get_file(photo.file_id)
        buffer = BytesIO()
        await telegram_file.download_to_memory(buffer)
        response = await self.screener.screen_image(buffer.getvalue(), update.effective_user.id)
        await self._deliver_response(status_message, update, response)

    @staticmethod
    async def _deliver_response(status_message, update, response):
        messages = response.format_messages()
        await status_message.edit_text(messages[0], parse_mode="HTML")
        for message in messages[1:]:
            await update.message.reply_text(message, parse_mode="HTML")

    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.error("Telegram update failed: %s", context.error)
        if update and update.effective_message:
            await update.effective_message.reply_text("Something went wrong. Please try again.")

    def run(self):
        if not TELEGRAM_BOT_TOKEN:
            logger.error("TELEGRAM_BOT_TOKEN is not set")
            sys.exit(1)
        application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        application.add_handler(CommandHandler("start", self.start_command))
        application.add_handler(CommandHandler("help", self.help_command))
        application.add_handler(CommandHandler("check", self.check_command))
        application.add_handler(CommandHandler("price", self.price_command))
        application.add_handler(CommandHandler("history", self.history_command))
        application.add_handler(CommandHandler("stats", self.stats_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))
        application.add_handler(MessageHandler(filters.PHOTO, self.handle_photo))
        application.add_error_handler(self.error_handler)
        self.screener.clear_expired_cache()
        logger.info("Telegram channel is running")
        # In multi-channel mode this runs in a worker thread, which has no event
        # loop by default and cannot install signal handlers.
        kwargs = {}
        if threading.current_thread() is not threading.main_thread():
            asyncio.set_event_loop(asyncio.new_event_loop())
            kwargs["stop_signals"] = None
        application.run_polling(allowed_updates=Update.ALL_TYPES, **kwargs)
