"""
handlers.py — All command and message handlers with category folders.
"""

import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from config import ADMIN_IDS, REQUIRED_CHANNELS, FILE_EXPIRY_DAYS
from database import Database

logger = logging.getLogger(__name__)

# ── Categories ────────────────────────────────────────────────────────────────

CATEGORIES = {
    "config": "⚙️ Config",
    "combos": "📄 Combos",
    "valids": "✅ Valids",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def check_membership(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> list[str]:
    missing = []
    for channel in REQUIRED_CHANNELS:
        try:
            member = await context.bot.get_chat_member(channel, user_id)
            if member.status in ("left", "kicked", "banned"):
                missing.append(channel)
        except BadRequest:
            missing.append(channel)
    return missing


def get_db(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.application.bot_data["db"]


def fmt_dt(iso: str) -> str:
    dt = datetime.fromisoformat(iso)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def category_menu() -> InlineKeyboardMarkup:
    """Main category selection keyboard."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=f"cat_{key}")]
        for key, label in CATEGORIES.items()
    ])


def admin_upload_menu() -> InlineKeyboardMarkup:
    """Ask admin which category to upload to."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Upload to {label}", callback_data=f"upload_cat_{key}")]
        for key, label in CATEGORIES.items()
    ])


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
                "Send any file and I'll ask which category to put it in.\n\n"
                "Commands:\n"
                "📋 /files — browse files by category\n"
                "🗑 /delete <id> — remove a file\n"
                "📊 /stats — bot statistics\n"
                "❓ /help — help",
                parse_mode="Markdown",
            )
        else:
            missing = await check_membership(user.id, context)
            if missing:
                links = "\n".join(f"• {ch}" for ch in missing)
                await update.message.reply_text(
                    "👋 Welcome! To access the file vault you need to join:\n\n"
                    f"{links}\n\nAfter joining, send /start again."
                )
            else:
                await update.message.reply_text(
                    f"👋 Hello, *{user.first_name}*!\n\n"
                    "Use /files to browse available files.",
                    parse_mode="Markdown",
                )

    # /help
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if is_admin(user.id):
            text = (
                "🔧 *Admin Commands*\n"
                "📤 Send any file → choose a category\n"
                "📋 /files — browse by category\n"
                "🗑 /delete <id> — delete a file\n"
                "📊 /stats — usage stats\n\n"
                f"Files auto-delete after *{FILE_EXPIRY_DAYS} days*."
            )
        else:
            text = (
                "📂 *File Vault*\n"
                "/start — check membership\n"
                "/files — browse files by category"
            )
        await update.message.reply_text(text, parse_mode="Markdown")

    # /upload hint
    async def upload_prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ This command is for admins only.")
            return
        await update.message.reply_text(
            "📤 Just send me any file and I'll ask which category to place it in."
        )

    # Handle incoming files (admin only) — ask for category first
    async def handle_file_upload(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not is_admin(user.id):
            await update.message.reply_text("⛔ Only admins can upload files.")
            return

        msg = update.message

        if msg.document:
            context.user_data["pending_file"] = {
                "file_id": msg.document.file_id,
                "file_type": "document",
                "file_name": msg.document.file_name or "unnamed",
                "caption": msg.caption or "",
            }
        elif msg.photo:
            context.user_data["pending_file"] = {
                "file_id": msg.photo[-1].file_id,
                "file_type": "photo",
                "file_name": "photo.jpg",
                "caption": msg.caption or "",
            }
        elif msg.video:
            context.user_data["pending_file"] = {
                "file_id": msg.video.file_id,
                "file_type": "video",
                "file_name": msg.video.file_name or "video.mp4",
                "caption": msg.caption or "",
            }
        elif msg.audio:
            context.user_data["pending_file"] = {
                "file_id": msg.audio.file_id,
                "file_type": "audio",
                "file_name": msg.audio.file_name or "audio.mp3",
                "caption": msg.caption or "",
            }
        else:
            await msg.reply_text("❌ Unsupported file type.")
            return

        await msg.reply_text(
            "📁 Which category should this file go into?",
            reply_markup=admin_upload_menu(),
        )

    # /files — show category picker
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

        await update.message.reply_text(
            "📂 Choose a category to browse:",
            reply_markup=category_menu(),
        )

    # Inline button callbacks
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        user = query.from_user
        data = query.data
        db = get_db(context)

        # --- Handle Back Button ---
        if data == "back_to_categories":
            await query.edit_message_text(
                "📂 Choose a category to browse:",
                reply_markup=category_menu(),
            )
            return

        # Admin chose upload category
        if data.startswith("upload_cat_") and is_admin(user.id):
            category = data.split("upload_cat_", 1)[1]
            pending = context.user_data.get("pending_file")
            if not pending:
                await query.edit_message_text("❌ No pending file found. Please send the file again.")
                return

            db_id = db.add_file(
                pending["file_id"],
                pending["file_type"],
                pending["file_name"],
                pending["caption"],
                user.id,
                category,
            )
            context.user_data.pop("pending_file", None)
            cat_label = CATEGORIES.get(category, category)
            await query.edit_message_text(
                f"✅ *File stored in {cat_label}!*\n"
                f"🆔 ID: `{db_id}`\n"
                f"📄 Name: {pending['file_name']}\n"
                f"⏳ Expires in *{FILE_EXPIRY_DAYS} days*\n"
                f"🗑 To delete: /delete {db_id}",
                parse_mode="Markdown",
            )
            return

        # User/admin chose a category to browse
        if data.startswith("cat_"):
            category = data.split("cat_", 1)[1]

            if not is_admin(user.id):
                missing = await check_membership(user.id, context)
                if missing:
                    await query.message.reply_text(
                        "🔒 Join the required channels first:\n" +
                        "\n".join(f"• {ch}" for ch in missing)
                    )
                    return

            files = db.get_files_by_category(category)
            cat_label = CATEGORIES.get(category, category)

            # Create a reusable Back button keyboard
            back_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back to Categories", callback_data="back_to_categories")]
            ])

            if not files:
                await query.edit_message_text(
                    f"📭 No files in {cat_label} right now.",
                    reply_markup=back_markup
                )
                return

            await query.edit_message_text(
                f"{cat_label} — *{len(files)} file(s)*:",
                parse_mode="Markdown",
                reply_markup=back_markup  # Attach the back button here
            )

            for row in files:
                expiry_str = fmt_dt(row["expiry_time"])
                caption_preview = f"\n📝 {row['caption']}" if row["caption"] else ""
                type_emoji = {"document": "📄", "photo": "🖼", "video": "🎬", "audio": "🎵"}.get(row["file_type"], "📁")

                keyboard = [[InlineKeyboardButton("⬇️ Get File", callback_data=f"get_{row['id']}")]]
                if is_admin(user.id):
                    keyboard[0].append(
                        InlineKeyboardButton("🗑 Delete", callback_data=f"del_{row['id']}")
                    )
                
                # Attach back button to individual files
                keyboard.append([InlineKeyboardButton("🔙 Back to Categories", callback_data="back_to_categories")])

                await query.message.reply_text(
                    f"{type_emoji} *{row['file_name']}*{caption_preview}\n"
                    f"🆔 ID: `{row['id']}` | ⏳ Expires: {expiry_str}",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
            return

        # Get file
        if data.startswith("get_"):
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
                await query.message.reply_text(
                    "❌ Couldn't send the file. Make sure you've started the bot in DM first."
                )
            return

        # Delete (admin)
        if data.startswith("del_") and is_admin(user.id):
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

    # /stats
    async def stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Admins only.")
            return
        db = get_db(context)
        lines = ["📊 *Bot Stats*", f"👥 Total users: {db.get_user_count()}"]
        for key, label in CATEGORIES.items():
            count = len(db.get_files_by_category(key))
            lines.append(f"{label}: {count} file(s)")
        lines.append(f"⏳ File expiry: {FILE_EXPIRY_DAYS} days")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
