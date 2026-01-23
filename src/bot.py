"""Telegram bot entry point for Stock Screener."""

import logging
import sys
from io import BytesIO

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

from config import TELEGRAM_BOT_TOKEN, LOG_FILE, LOG_LEVEL
from screener import StockScreener

# Configure logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Global screener instance
screener = StockScreener()


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    welcome_message = """Welcome to the Halal Stock Screener Bot! 📊

I can help you check if stocks are Shariah-compliant using data from Musaffa.com.

<b>How to use:</b>
• Send a ticker symbol (e.g., <code>AAPL</code> or <code>$MSFT</code>)
• Send multiple tickers separated by spaces
• Send an image containing stock tickers

<b>Commands:</b>
• /check &lt;ticker&gt; - Check a specific stock
• /history - View your recent checks
• /stats - View your screening statistics
• /help - Show this help message

Let's get started! Send me a ticker symbol to check."""

    await update.message.reply_text(welcome_message, parse_mode="HTML")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    await start_command(update, context)


async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /check <ticker> command."""
    if not context.args:
        await update.message.reply_text(
            "Please provide a ticker symbol.\nUsage: <code>/check AAPL</code>",
            parse_mode="HTML"
        )
        return

    user_id = update.effective_user.id
    tickers = [arg.upper() for arg in context.args]

    await update.message.reply_text(f"Checking {', '.join(tickers)}...")

    response = await screener.screen_tickers(tickers, user_id)
    await update.message.reply_text(response.format_message(), parse_mode="HTML")


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /history command."""
    user_id = update.effective_user.id
    history = screener.get_user_history(user_id, limit=15)

    if not history:
        await update.message.reply_text("You haven't checked any stocks yet.")
        return

    lines = ["<b>Your Recent Checks:</b>\n"]
    for entry in history:
        status_emoji = {
            "HALAL": "✅",
            "NOT_HALAL": "❌",
            "DOUBTFUL": "⚠️",
            "NOT_COVERED": "❓",
            "ERROR": "⚠️"
        }.get(entry["status"], "❓")

        # Parse timestamp
        timestamp = entry["checked_at"][:16].replace("T", " ")
        lines.append(f"{status_emoji} {entry['ticker']} - {timestamp}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stats command."""
    user_id = update.effective_user.id
    stats = screener.get_user_stats(user_id)

    if stats["total_checks"] == 0:
        await update.message.reply_text("You haven't checked any stocks yet.")
        return

    lines = [
        "<b>Your Screening Statistics:</b>\n",
        f"Total checks: {stats['total_checks']}",
        f"Unique tickers: {stats['unique_tickers']}",
        "\n<b>Status Breakdown:</b>"
    ]

    status_labels = {
        "HALAL": "✅ Halal",
        "NOT_HALAL": "❌ Not Halal",
        "DOUBTFUL": "⚠️ Doubtful",
        "NOT_COVERED": "❓ Not Covered",
        "ERROR": "⚠️ Error"
    }

    for status, count in stats["status_breakdown"].items():
        label = status_labels.get(status, status)
        lines.append(f"  {label}: {count}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages containing tickers."""
    text = update.message.text.strip()
    user_id = update.effective_user.id

    if not text:
        return

    # Skip if it looks like a command
    if text.startswith("/"):
        return

    await update.message.reply_text("Analyzing...")

    response = await screener.screen_text(text, user_id)

    if response.error and "No tickers" in response.error:
        await update.message.reply_text(
            "I couldn't identify any stock tickers in your message.\n\n"
            "Try sending:\n"
            "• A ticker like <code>AAPL</code> or <code>$MSFT</code>\n"
            "• Multiple tickers like <code>AAPL GOOGL TSLA</code>",
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text(response.format_message(), parse_mode="HTML")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photo messages."""
    user_id = update.effective_user.id

    await update.message.reply_text("Analyzing image for stock tickers...")

    # Get the largest photo
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)

    # Download photo to memory
    buffer = BytesIO()
    await file.download_to_memory(buffer)
    image_data = buffer.getvalue()

    response = await screener.screen_image(image_data, user_id)
    await update.message.reply_text(response.format_message(), parse_mode="HTML")


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors."""
    logger.error(f"Error handling update {update}: {context.error}")

    if update and update.effective_message:
        await update.effective_message.reply_text(
            "Sorry, something went wrong. Please try again later."
        )


def main():
    """Start the bot."""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set!")
        sys.exit(1)

    logger.info("Starting Stock Screener Bot...")

    # Create application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Add command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("check", check_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("stats", stats_command))

    # Add message handlers
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # Add error handler
    application.add_error_handler(error_handler)

    # Clear expired cache on startup
    screener.clear_expired_cache()

    # Run the bot
    logger.info("Bot is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
