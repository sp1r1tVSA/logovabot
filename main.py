import logging
from telegram.ext import ApplicationBuilder, Application
from telegram import BotCommand
from config import TOKEN
from database import init_db
from handlers import register_all_handlers, job_check_deadlines_and_remind, job_post_debts_to_warns, job_debt_lifecycle_tracker

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

    # 🎰 Start Logovo.bet Telegram Mini App API server
    try:
        from api.server import start_api_server_background
        import config
        await start_api_server_background(host=config.API_HOST, port=config.API_PORT)
    except Exception as e:
        logger.warning(f"Failed to start Logovo.bet Mini App server: {e}")

    # 📱 Configure Telegram WebApp Menu Button (Admins only in Lab mode, or Global if public)
    try:
        from telegram import MenuButtonWebApp, MenuButtonDefault, WebAppInfo
        import config
        from database import get_feature_flag
        
        is_public = get_feature_flag("betting_market", default="admin_only") == "public"
        
        if is_public and config.WEBAPP_URL and config.WEBAPP_URL.startswith("https://"):
            await application.bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="🎰 Logovo.bet", web_app=WebAppInfo(url=config.WEBAPP_URL))
            )
        else:
            # Set default menu button globally for all users
            await application.bot.set_chat_menu_button(menu_button=MenuButtonDefault())
            # Set WebApp button exclusively for admin chats
            if config.WEBAPP_URL and config.WEBAPP_URL.startswith("https://"):
                for adm_id in config.ADMIN_IDS:
                    try:
                        await application.bot.set_chat_menu_button(
                            chat_id=adm_id,
                            menu_button=MenuButtonWebApp(text="🎰 Logovo.bet [Lab]", web_app=WebAppInfo(url=config.WEBAPP_URL))
                        )
                    except Exception:
                        pass
    except Exception as e:
        logger.warning(f"Could not set WebApp menu button: {e}")

def register_jobs(application: Application) -> None:
    """Register periodic background jobs."""
    # Check round deadlines & send reminders every 30 minutes
    application.job_queue.run_repeating(job_check_deadlines_and_remind, interval=1800, first=30)
    # Post/update debts summary in ПРЕДЫ thread every 12 hours
    application.job_queue.run_repeating(job_post_debts_to_warns, interval=12 * 3600, first=60)
    # Run automated debt lifecycle tracker (reminders + auto-warns + auto-kick) every 30 minutes
    application.job_queue.run_repeating(job_debt_lifecycle_tracker, interval=1800, first=90)

    # Phase 6: Live provider sync, intelligence cache & smart notifications
    try:
        from services.background_sync import (
            sync_live_provider_job,
            sync_intelligence_cache_job,
            process_notification_queue_job,
        )
        application.job_queue.run_repeating(sync_live_provider_job, interval=45, first=15)
        application.job_queue.run_repeating(sync_intelligence_cache_job, interval=300, first=45)
        application.job_queue.run_repeating(process_notification_queue_job, interval=15, first=20)
    except Exception as e:
        logger.warning(f"Could not register Phase 6 background jobs: {e}")

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

if __name__ == "__main__":
    main()
