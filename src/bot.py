"""Telegram bot entry point for Stock Screener."""

print("Bot module loading...")

import logging
import os
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from io import BytesIO

print("Standard imports OK")

try:
    from telegram import Update
    from telegram.ext import (
        Application,
        CommandHandler,
        MessageHandler,
        ContextTypes,
        filters
    )
    print("Telegram imports OK")
except Exception as e:
    print(f"Telegram import error: {e}")
    raise

try:
    from config import TELEGRAM_BOT_TOKEN, LOG_FILE, LOG_LEVEL
    print("Config import OK")
except Exception as e:
    print(f"Config import error: {e}")
    raise

try:
    from screener import StockScreener
    print("Screener import OK")
except Exception as e:
    print(f"Screener import error: {e}")
    raise

# Configure logging - only use file handler if directory exists
log_handlers = [logging.StreamHandler(sys.stdout)]
try:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    log_handlers.append(logging.FileHandler(LOG_FILE))
except Exception:
    pass  # Skip file logging if it fails

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=log_handlers
)
logger = logging.getLogger(__name__)

# Health check server for Railway
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass  # Suppress health check logs


def start_health_server():
    """Start health check HTTP server in background thread."""
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"Health check server running on port {port}")
    server.serve_forever()


# Global screener instance
print("Initializing StockScreener...")
try:
    screener = StockScreener()
    print("StockScreener initialized OK")
except Exception as e:
    print(f"StockScreener init error: {e}")
    raise


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    welcome_message = """<b>Halal Stock Screener</b>

Check if stocks are Shariah-compliant using Musaffa.com data.

<b>Usage</b>
Send ticker symbols: <code>AAPL</code> <code>MSFT</code> <code>GOOGL</code>
Or send an image with stock tickers.

<b>Commands</b>
/check <code>AAPL</code> - Check specific stocks
/history - Recent checks
/stats - Your statistics"""

    await update.message.reply_text(welcome_message, parse_mode="HTML")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    await start_command(update, context)


async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /check <ticker> command."""
    if not context.args:
        await update.message.reply_text(
            "Usage: <code>/check AAPL MSFT</code>",
            parse_mode="HTML"
        )
        return

    user_id = update.effective_user.id
    tickers = [arg.upper() for arg in context.args]

    status_msg = await update.message.reply_text("Checking...")

    response = await screener.screen_tickers(tickers, user_id)
    await status_msg.edit_text(response.format_message(), parse_mode="HTML")


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /history command."""
    user_id = update.effective_user.id
    history = screener.get_user_history(user_id, limit=15)

    if not history:
        await update.message.reply_text("No history yet. Send a ticker to get started.")
        return

    status_emoji = {
        "HALAL": "✅",
        "NOT_HALAL": "❌",
        "DOUBTFUL": "⚠️",
        "NOT_COVERED": "❓",
        "ERROR": "⚠️"
    }

    lines = ["<b>Recent Checks</b>\n"]
    for entry in history:
        emoji = status_emoji.get(entry["status"], "❓")
        date = entry["checked_at"][:10]
        lines.append(f"{emoji} <code>{entry['ticker']}</code>  {date}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stats command."""
    user_id = update.effective_user.id
    stats = screener.get_user_stats(user_id)

    if stats["total_checks"] == 0:
        await update.message.reply_text("No statistics yet. Send a ticker to get started.")
        return

    # Build stats message
    lines = [
        "<b>Your Statistics</b>\n",
        f"Total checks: <b>{stats['total_checks']}</b>",
        f"Unique stocks: <b>{stats['unique_tickers']}</b>",
        ""
    ]

    status_config = [
        ("HALAL", "✅ Halal"),
        ("NOT_HALAL", "❌ Not Halal"),
        ("DOUBTFUL", "⚠️ Doubtful"),
        ("NOT_COVERED", "❓ Not Covered"),
    ]

    breakdown = stats["status_breakdown"]
    for key, label in status_config:
        if key in breakdown and breakdown[key] > 0:
            lines.append(f"{label}: {breakdown[key]}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages containing tickers."""
    text = update.message.text.strip()
    user_id = update.effective_user.id

    if not text or text.startswith("/"):
        return

    status_msg = await update.message.reply_text("Checking...")

    response = await screener.screen_text(text, user_id)

    if response.error and "No tickers" in response.error:
        await status_msg.edit_text(
            "No tickers found.\n\nTry: <code>AAPL</code> or <code>AAPL MSFT GOOGL</code>",
            parse_mode="HTML"
        )
    else:
        await status_msg.edit_text(response.format_message(), parse_mode="HTML")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photo messages."""
    user_id = update.effective_user.id

    status_msg = await update.message.reply_text("Analyzing image...")

    # Get the largest photo
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)

    # Download photo to memory
    buffer = BytesIO()
    await file.download_to_memory(buffer)
    image_data = buffer.getvalue()

    response = await screener.screen_image(image_data, user_id)
    await status_msg.edit_text(response.format_message(), parse_mode="HTML")


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors."""
    logger.error(f"Error handling update {update}: {context.error}")

    if update and update.effective_message:
        await update.effective_message.reply_text(
            "Something went wrong. Please try again."
        )


def main():
    """Start the bot."""
    print("=" * 50)
    print("HALAL STOCK SCREENER BOT STARTING")
    print("=" * 50)

    # Start health check server for Railway
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()

    if not TELEGRAM_BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN is not set!")
        logger.error("TELEGRAM_BOT_TOKEN is not set!")
        sys.exit(1)

    print(f"Token configured: {TELEGRAM_BOT_TOKEN[:10]}...")
    logger.info("Starting Stock Screener Bot...")

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("check", check_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("stats", stats_command))

    # Message handlers
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # Error handler
    application.add_error_handler(error_handler)

    # Clear expired cache on startup
    screener.clear_expired_cache()

    logger.info("Bot is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
