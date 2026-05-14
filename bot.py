"""
Telegram File Vault Bot
- Admins upload files; files expire after 3 days
- Users must join required channels/groups to access files
- Automatic cleanup of expired files
"""

import asyncio
import logging
from datetime import datetime

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackQueryHandler,
)

from config import BOT_TOKEN, ADMIN_IDS, REQUIRED_CHANNELS
from database import Database
from handlers import BotHandlers
from scheduler import start_scheduler

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def post_init(application: Application) -> None:
    """Initialize database and scheduler after app starts."""
    db = Database()
    db.init_db()
    application.bot_data["db"] = db
    await start_scheduler(application)
    logger.info("Bot initialized successfully.")


def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    handlers_obj = BotHandlers()

    # Commands
    app.add_handler(CommandHandler("start", handlers_obj.start))
    app.add_handler(CommandHandler("files", handlers_obj.list_files))
    app.add_handler(CommandHandler("upload", handlers_obj.upload_prompt))
    app.add_handler(CommandHandler("delete", handlers_obj.delete_file))
    app.add_handler(CommandHandler("stats", handlers_obj.stats))
    app.add_handler(CommandHandler("help", handlers_obj.help_command))

    # File uploads from admins
    app.add_handler(
        MessageHandler(
            filters.Document.ALL | filters.PHOTO | filters.VIDEO | filters.AUDIO,
            handlers_obj.handle_file_upload,
        )
    )

    # Inline button callbacks
    app.add_handler(CallbackQueryHandler(handlers_obj.handle_callback))

    logger.info("Bot started. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
