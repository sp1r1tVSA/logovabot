import logging
from telegram.ext import ApplicationBuilder, Application
from telegram import BotCommand
from config import TOKEN
from database import init_db
from handlers import register_all_handlers, job_check_deadlines_and_remind

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

async def post_init(application: Application) -> None:
    await application.bot.set_my_commands([
        BotCommand("start", "Открыть главное меню")
    ])

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

    # Build the Telegram Application
    application = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    # Register all handlers (modular registration)
    register_all_handlers(application)

    # Setup periodic background reminders (check every 30 mins)
    if application.job_queue:
        application.job_queue.run_repeating(job_check_deadlines_and_remind, interval=1800, first=10)

    # Start the bot
    logger.info("Starting Telegram bot...")
    try:
        application.run_polling()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped by user.")

if __name__ == "__main__":
    main()
