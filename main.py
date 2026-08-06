import logging
from telegram.ext import ApplicationBuilder, Application, PicklePersistence
from telegram import BotCommand
from config import TOKEN
from database import init_db
from handlers import register_all_handlers, job_check_deadlines_and_remind, job_post_debts_to_warns

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

def register_jobs(application: Application) -> None:
    """Register periodic background jobs."""
    # Check round deadlines & send reminders every 30 minutes
    application.job_queue.run_repeating(job_check_deadlines_and_remind, interval=1800, first=30)
    # Post/update debts summary in ПРЕДЫ thread every 12 hours
    application.job_queue.run_repeating(job_post_debts_to_warns, interval=12 * 3600, first=60)

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

    # Register periodic background jobs
    register_jobs(application)

    # Start the bot
    logger.info("Starting Telegram bot...")
    try:
        application.run_polling()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped by user.")
        
    persistence = PicklePersistence(filepath="state/bot_persistence.pickle")

    application = (
        ApplicationBuilder()
        .token(TOKEN)
        .post_init(post_init)
        .persistence(persistence) # <-- Подключаем персистентность
        .build()
    )

if __name__ == "__main__":
    main()
