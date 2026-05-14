"""
handlers.py — All command and message handlers.
"""

import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from config import ADMIN_IDS, REQUIRED_CHANNELS, FILE_EXPIRY_DAYS
from database import Database

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────────

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def check_membership(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> list[str]:
    """Return list of channels the user has NOT yet joined."""
    missing = []
    for channel in REQUIRED_CHANNELS:
        try:
            member = await context.bot.get_chat_member(channel, user_id)
            if member.status in ("left", "kicked", "banned"):
                missing.append(channel)
        except BadRequest:
            missing.append(channel)  # Can't verify → treat as missing
    return missing


def get_db(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.application.bot_data["db"]


def fmt_dt(iso: str) -> str:
    dt = datetime.fromisoformat(iso)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


# ── Handler class ─────────────────────────────────────────────────────────────

class BotHandlers:

    # /start
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        db = get_db(context)
        db.register_user(user.id, user.username or "", user.first_name or "")

        if is_admin(user.id):
            await update.message.reply_text(
                f"👋 Welcome back, admin *{user.first_name}*!\n\n"
                "Commands available to you:\n"
                "📤 Send any file to upload it\n"
                "📋 /files — list all active files\n"
                "🗑 /delete <id> — remove a file immediately\n"
                "📊 /stats — bot statistics\n"
                "❓ /help — show this message",
                parse_mode="Markdown",
            )
        else:
            missing = await check_membership(user.id, context)
            if missing:
                links = "\n".join(f"• {ch}" for ch in missing)
                await update.message.reply_text(
                    "👋 Welcome! To access the file vault you need to join:\n\n"
                    f"{links}\n\n"
                    "After joining, send /start again.",
                )
            else:
                await update.message.reply_text(
                    f"👋 Hello, *{user.first_name}*! You're all set.\n\n"
                    "Use /files to browse available files.",
                    parse_mode="Markdown",
                )

    # /help
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if is_admin(user.id):
            text = (
                "🔧 *Admin Commands*\n"
                "📤 Send any file — upload to vault\n"
                "📋 /files — list active files\n"
                "🗑 /delete <id> — delete a file\n"
                "📊 /stats — usage stats\n\n"
                f"Files auto-delete after *{FILE_EXPIRY_DAYS} days*."
            )
        else:
            text = (
                "📂 *File Vault*\n"
                "/start — check your membership\n"
                "/files — browse available files"
            )
        await update.message.reply_text(text, parse_mode="Markdown")

    # /upload (just a hint for admins)
    async def upload_prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ This command is for admins only.")
            return
        await update.message.reply_text(
            "📤 Just send me any file (document, photo, video, audio) "
            "and I'll store it in the vault.\n"
            "You can add a caption to describe it."
        )

    # Handle incoming files (admin only)
    async def handle_file_upload(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not is_admin(user.id):
            await update.message.reply_text("⛔ Only admins can upload files.")
            return

        msg = update.message
        db = get_db(context)

        if msg.document:
            file_id = msg.document.file_id
            file_type = "document"
            file_name = msg.document.file_name or "unnamed"
        elif msg.photo:
            file_id = msg.photo[-1].file_id  # largest size
            file_type = "photo"
            file_name = "photo.jpg"
        elif msg.video:
            file_id = msg.video.file_id
            file_type = "video"
            file_name = msg.video.file_name or "video.mp4"
        elif msg.audio:
            file_id = msg.audio.file_id
            file_type = "audio"
            file_name = msg.audio.file_name or "audio.mp3"
        else:
            await msg.reply_text("❌ Unsupported file type.")
            return

        caption = msg.caption or ""
        db_id = db.add_file(file_id, file_type, file_name, caption, user.id)

        await msg.reply_text(
            f"✅ *File stored!*\n"
            f"🆔 ID: `{db_id}`\n"
            f"📄 Name: {file_name}\n"
            f"⏳ Expires in *{FILE_EXPIRY_DAYS} days*\n"
            f"🗑 To delete early: /delete {db_id}",
            parse_mode="Markdown",
        )

    # /files — list active files
    async def list_files(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user

        if not is_admin(user.id):
            missing = await check_membership(user.id, context)
            if missing:
                links = "\n".join(f"• {ch}" for ch in missing)
                await update.message.reply_text(
                    "🔒 You need to join these channels first:\n\n" + links
                )
                return

        db = get_db(context)
        files = db.get_active_files()

        if not files:
            await update.message.reply_text("📭 No files available right now.")
            return

        # Send each file with an inline button
        await update.message.reply_text(f"📂 *{len(files)} file(s) available:*", parse_mode="Markdown")

        for row in files:
            expiry_str = fmt_dt(row["expiry_time"])
            caption_preview = f"\n📝 {row['caption']}" if row["caption"] else ""
            type_emoji = {"document": "📄", "photo": "🖼", "video": "🎬", "audio": "🎵"}.get(row["file_type"], "📁")

            keyboard = [[InlineKeyboardButton("⬇️ Get File", callback_data=f"get_{row['id']}")]]
            if is_admin(user.id):
                keyboard[0].append(
                    InlineKeyboardButton("🗑 Delete", callback_data=f"del_{row['id']}")
                )

            await update.message.reply_text(
                f"{type_emoji} *{row['file_name']}*{caption_preview}\n"
                f"🆔 ID: `{row['id']}` | ⏳ Expires: {expiry_str}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

    # Inline button callbacks
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        user = query.from_user
        data = query.data
        db = get_db(context)

        if data.startswith("get_"):
            # Membership check for regular users
            if not is_admin(user.id):
                missing = await check_membership(user.id, context)
                if missing:
                    await query.message.reply_text(
                        "🔒 Join the required channels first:\n" +
                        "\n".join(f"• {ch}" for ch in missing)
                    )
                    return

            file_db_id = int(data.split("_", 1)[1])
            row = db.get_file(file_db_id)
            if not row:
                await query.message.reply_text("❌ File not found or has expired.")
                return

            send_map = {
                "document": context.bot.send_document,
                "photo":    context.bot.send_photo,
                "video":    context.bot.send_video,
                "audio":    context.bot.send_audio,
            }
            sender = send_map.get(row["file_type"], context.bot.send_document)
            kwarg_key = row["file_type"] if row["file_type"] != "document" else "document"

            try:
                await sender(
                    chat_id=user.id,
                    **{kwarg_key: row["file_id"]},
                    caption=row["caption"] or None,
                )
                await query.message.reply_text("✅ File sent to your DM!")
            except Exception as e:
                logger.error("Failed to send file: %s", e)
                await query.message.reply_text("❌ Couldn't send the file. Start the bot in DM first: @your_bot")

        elif data.startswith("del_") and is_admin(user.id):
            file_db_id = int(data.split("_", 1)[1])
            db.mark_deleted(file_db_id)
            await query.edit_message_text("🗑 File deleted.")

    # /delete <id>
    async def delete_file(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Admins only.")
            return
        if not context.args:
            await update.message.reply_text("Usage: /delete <file_id>")
            return
        try:
            file_db_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ Invalid ID.")
            return

        db = get_db(context)
        row = db.get_file(file_db_id)
        if not row:
            await update.message.reply_text("❌ File not found or already deleted.")
            return

        db.mark_deleted(file_db_id)
        await update.message.reply_text(f"✅ File `{file_db_id}` deleted.", parse_mode="Markdown")

    # /stats (admin only)
    async def stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Admins only.")
            return
        db = get_db(context)
        await update.message.reply_text(
            f"📊 *Bot Stats*\n"
            f"👥 Total users: {db.get_user_count()}\n"
            f"📂 Active files: {db.get_active_file_count()}\n"
            f"⏳ File expiry: {FILE_EXPIRY_DAYS} days",
            parse_mode="Markdown",
        )
