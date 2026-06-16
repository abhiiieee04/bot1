"""
Telegram File Vault Bot
- Owner manages admins; only owner can broadcast/export/backup
- Admins upload and manage files
- Users must join required channels to access files
- Automatic cleanup of expired files
"""

import logging
from telegram import Update, BotCommand, BotCommandScopeDefault, BotCommandScopeChat
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackQueryHandler,
)

from config import BOT_TOKEN, OWNER_ID
from database import Database
from handlers import BotHandlers
from scheduler import start_scheduler

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Commands every user sees
PUBLIC_COMMANDS = [
    BotCommand("start", "Get started / check access"),
    BotCommand("files", "Browse available files"),
    BotCommand("help",  "Show help"),
]

# Extra commands admins see
ADMIN_ONLY_COMMANDS = [
    BotCommand("upload",    "How to upload a file"),
    BotCommand("delete",    "Delete a file by ID"),
    BotCommand("stats",     "Bot statistics"),
    BotCommand("broadcast", "Message every user (with confirmation)"),
    BotCommand("export",    "Export users as CSV"),
    BotCommand("backup",    "Download full database backup"),
]

# Commands only the owner sees (admin management)
OWNER_ONLY_COMMANDS = [
    BotCommand("addadmin",    "Add a new admin"),
    BotCommand("removeadmin", "Remove an admin"),
    BotCommand("listadmins",  "List all admins"),
]


async def post_init(application: Application) -> None:
    db = Database()
    db.init_db()
    application.bot_data["db"] = db
    await start_scheduler(application)

    # Default menu for everyone
    await application.bot.set_my_commands(PUBLIC_COMMANDS, scope=BotCommandScopeDefault())

    # Owner gets everything
    try:
        await application.bot.set_my_commands(
            PUBLIC_COMMANDS + ADMIN_ONLY_COMMANDS + OWNER_ONLY_COMMANDS,
            scope=BotCommandScopeChat(chat_id=OWNER_ID),
        )
    except Exception as e:
        logger.warning("Could not set owner command menu: %s", e)

    # Existing admins from DB get the admin menu
    for admin_id in db.get_admin_ids():
        try:
            await application.bot.set_my_commands(
                PUBLIC_COMMANDS + ADMIN_ONLY_COMMANDS,
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        except Exception as e:
            logger.warning("Could not set admin command menu for %s: %s", admin_id, e)

    logger.info("Bot initialised. Owner: %s", OWNER_ID)


def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    h = BotHandlers()

    # Public
    app.add_handler(CommandHandler("start", h.start))
    app.add_handler(CommandHandler("files", h.list_files))
    app.add_handler(CommandHandler("help",  h.help_command))

    # Admin + owner
    app.add_handler(CommandHandler("upload", h.upload_prompt))
    app.add_handler(CommandHandler("delete", h.delete_file))
    app.add_handler(CommandHandler("stats",  h.stats))

    # Owner only
    app.add_handler(CommandHandler("addadmin",    h.add_admin))
    app.add_handler(CommandHandler("removeadmin", h.remove_admin))
    app.add_handler(CommandHandler("listadmins",  h.list_admins))
    app.add_handler(CommandHandler("broadcast",   h.broadcast))
    app.add_handler(CommandHandler("export",      h.export_users))
    app.add_handler(CommandHandler("backup",      h.export_db))

    # File uploads
    app.add_handler(MessageHandler(
        filters.Document.ALL | filters.PHOTO | filters.VIDEO | filters.AUDIO,
        h.handle_file_upload,
    ))

    # Inline buttons
    app.add_handler(CallbackQueryHandler(h.handle_callback))

    logger.info("Bot started.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
