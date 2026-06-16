"""
Telegram File Vault Bot
- Admins upload files; files expire after 3 days
- Users must join required channels/groups to access files
- Automatic cleanup of expired files
"""

import asyncio
import logging
from datetime import datetime

from telegram import Update, BotCommand, BotCommandScopeDefault, BotCommandScopeChat
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

# Commands every user sees in the "/" menu.
PUBLIC_COMMANDS = [
    BotCommand("start", "Get started / check access"),
    BotCommand("files", "Browse available files"),
    BotCommand("help", "Show help"),
]

# Extra commands only admins see in their own "/" menu.
ADMIN_ONLY_COMMANDS = [
    BotCommand("upload", "How to upload a file"),
    BotCommand("delete", "Delete a file by ID"),
    BotCommand("stats", "Bot statistics"),
    BotCommand("broadcast", "Message every user (with confirmation)"),
    BotCommand("export", "Export users as a CSV file"),
    BotCommand("backup", "Download full database backup"),
]


async def post_init(application: Application) -> None:
    """Initialize database, scheduler, and per-role command menus after app starts."""
    db = Database()
    db.init_db()
    application.bot_data["db"] = db
    await start_scheduler(application)

    # Everyone gets the plain public menu by default.
    await application.bot.set_my_commands(PUBLIC_COMMANDS, scope=BotCommandScopeDefault())

    # Admins additionally get the admin commands, but only inside their own
    # private chat with the bot — this only changes what's *listed* in the
    # "/" menu; is_admin() checks inside each handler are the real gate.
    for admin_id in ADMIN_IDS:
        try:
            await application.bot.set_my_commands(
                PUBLIC_COMMANDS + ADMIN_ONLY_COMMANDS,
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        except Exception as e:
            # Fails if the admin hasn't opened a chat with the bot yet — harmless,
            # it'll succeed next restart after they /start it once.
            logger.warning("Could not set admin command menu for %s: %s", admin_id, e)

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
    app.add_handler(CommandHandler("broadcast", handlers_obj.broadcast))
    app.add_handler(CommandHandler("export", handlers_obj.export_users))
    app.add_handler(CommandHandler("backup", handlers_obj.export_db))

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
