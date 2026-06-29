import os
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)
from database import init_db

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a friendly welcome message when /start is invoked."""
    user = update.effective_user
    welcome_text = (
        f"Привет, {user.first_name if user else 'друг'}!\n\n"
        "Я новый League Bot, переписанный с нуля.\n"
        "Используйте /help, чтобы увидеть список доступных команд."
    )
    if update.message:
        await update.message.reply_text(welcome_text)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a list of available commands when /help is invoked."""
    help_text = (
        "Доступные команды:\n"
        "/start - Запуск бота и приветствие\n"
        "/help - Справка по командам"
    )
    if update.message:
        await update.message.reply_text(help_text)

def main() -> None:
    """Initialize and run the Telegram bot application."""
    if not TOKEN:
        logger.error("No TELEGRAM_BOT_TOKEN or BOT_TOKEN found in environment variables!")
        print("Error: Please set TELEGRAM_BOT_TOKEN in your .env file.")
        return

    # Initialize the database
    try:
        init_db()
    except Exception as e:
        logger.critical(f"Failed to initialize database: {e}")
        return

    # Build the Application
    application = ApplicationBuilder().token(TOKEN).build()

    # Register command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))

    # Start the bot
    logger.info("Starting Telegram bot...")
    application.run_polling()

if __name__ == "__main__":
    main()
