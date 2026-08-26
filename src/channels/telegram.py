"""Telegram delivery-channel adapter."""

import logging
import sys
from io import BytesIO

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import TELEGRAM_BOT_TOKEN
from screener import StockScreener

logger = logging.getLogger(__name__)


class TelegramChannel:
    """Receive Telegram updates and deliver rendered screening responses."""

    def __init__(self, screening_service=None):
        self.screener = screening_service or StockScreener()

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
        response = await self.screener.screen_text(
            " ".join(context.args), update.effective_user.id
        )
        await self._deliver_response(status_message, update, response)

    async def history_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        history = self.screener.get_user_history(update.effective_user.id, limit=15)
        if not history:
            await update.message.reply_text(
                "No history yet. Send a ticker to get started."
            )
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
            await update.message.reply_text(
                "No statistics yet. Send a ticker to get started."
            )
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
        response = await self.screener.screen_image(
            buffer.getvalue(), update.effective_user.id
        )
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
            await update.effective_message.reply_text(
                "Something went wrong. Please try again."
            )

    def run(self):
        if not TELEGRAM_BOT_TOKEN:
            logger.error("TELEGRAM_BOT_TOKEN is not set")
            sys.exit(1)
        application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        application.add_handler(CommandHandler("start", self.start_command))
        application.add_handler(CommandHandler("help", self.help_command))
        application.add_handler(CommandHandler("check", self.check_command))
        application.add_handler(CommandHandler("history", self.history_command))
        application.add_handler(CommandHandler("stats", self.stats_command))
        application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text)
        )
        application.add_handler(MessageHandler(filters.PHOTO, self.handle_photo))
        application.add_error_handler(self.error_handler)
        self.screener.clear_expired_cache()
        logger.info("Telegram channel is running")
        application.run_polling(allowed_updates=Update.ALL_TYPES)
