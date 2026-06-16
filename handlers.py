"""
handlers.py — All command and message handlers with category folders.
"""

import csv
import io
import os
import asyncio
import logging
from datetime import datetime
from types import SimpleNamespace

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, Forbidden
from telegram.ext import ContextTypes

from config import ADMIN_IDS, REQUIRED_CHANNELS, FILE_EXPIRY_DAYS
from database import Database

logger = logging.getLogger(__name__)

# ── Categories ────────────────────────────────────────────────────────────────
CATEGORIES = {
    "config": "⚙️ Config",
    "combos": "🔀 Combos",
    "valids": "✅ Valids",
}

# How long to pause between broadcast sends (seconds).
# Telegram allows roughly 30 msgs/sec to *different* chats — 0.05s keeps us well under that.
BROADCAST_DELAY = 0.05

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
                "📣 /broadcast — message every user\n"
                "📥 /export — download users as CSV\n"
                "💾 /backup — download the full database file\n"
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
                "📊 /stats — usage stats\n"
                "📣 /broadcast <text> — preview + confirm before messaging all users\n"
                "📣 (reply to any message) /broadcast — same, but copies that message\n"
                "📥 /export — download all users as a CSV file\n"
                "💾 /backup — download the raw database file (full backup)\n\n"
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

        # Broadcast cancelled
        if data == "bcast_cancel" and is_admin(user.id):
            context.bot_data.get("pending_broadcasts", {}).pop(user.id, None)
            await query.edit_message_text("❌ Broadcast cancelled. No messages were sent.")
            return

        # Broadcast confirmed — fire it for real
        if data == "bcast_confirm" and is_admin(user.id):
            pending = context.bot_data.get("pending_broadcasts", {}).pop(user.id, None)
            if not pending:
                await query.edit_message_text(
                    "⚠️ This broadcast request has expired. Run /broadcast again."
                )
                return

            user_ids = db.get_all_user_ids()
            if not user_ids:
                await query.edit_message_text("📭 No users to broadcast to.")
                return

            await query.edit_message_text(f"📣 Starting broadcast to {len(user_ids)} user(s)...")

            source_message = None
            if pending["source_chat_id"] and pending["source_message_id"]:
                source_message = SimpleNamespace(
                    chat_id=pending["source_chat_id"],
                    message_id=pending["source_message_id"],
                )

            context.application.create_task(
                self._run_broadcast(context, query.message, user_ids, source_message, pending["text"])
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
            if not files:
                await query.edit_message_text(f"📭 No files in {cat_label} right now.")
                return

            await query.edit_message_text(
                f"{cat_label} — *{len(files)} file(s)*:",
                parse_mode="Markdown",
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
                "photo": context.bot.send_photo,
                "video": context.bot.send_video,
                "audio": context.bot.send_audio,
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

    # ── Broadcast ────────────────────────────────────────────────────────────
    # /broadcast <text>               -> previews plain text, waits for confirmation
    # (reply to a message) /broadcast -> previews that message (any type), waits for confirmation
    async def broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not is_admin(user.id):
            await update.message.reply_text("⛔ Admins only.")
            return

        db = get_db(context)
        user_ids = db.get_all_user_ids()
        if not user_ids:
            await update.message.reply_text("📭 No users to broadcast to yet.")
            return

        source_message = update.message.reply_to_message
        text_arg = " ".join(context.args) if context.args else None

        if not source_message and not text_arg:
            await update.message.reply_text(
                "Usage:\n"
                "• `/broadcast <message>` — sends plain text\n"
                "• Reply to any message (text, photo, file, etc.) with `/broadcast` "
                "— copies it as-is to every user",
                parse_mode="Markdown",
            )
            return

        # Stash what we're about to send, keyed by this admin, until they confirm or cancel.
        context.bot_data.setdefault("pending_broadcasts", {})[user.id] = {
            "text": text_arg,
            "source_chat_id": source_message.chat_id if source_message else None,
            "source_message_id": source_message.message_id if source_message else None,
        }

        confirm_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Confirm & Send", callback_data="bcast_confirm"),
            InlineKeyboardButton("❌ Cancel", callback_data="bcast_cancel"),
        ]])

        if source_message:
            # Show exactly what will go out by copying it back to the admin first.
            await context.bot.copy_message(
                chat_id=update.effective_chat.id,
                from_chat_id=source_message.chat_id,
                message_id=source_message.message_id,
            )
            await update.message.reply_text(
                f"👆 That's the preview. Send it to *{len(user_ids)} user(s)*?",
                parse_mode="Markdown",
                reply_markup=confirm_kb,
            )
        else:
            await update.message.reply_text(
                f"📣 *Preview:*\n\n{text_arg}\n\n"
                f"Send this to *{len(user_ids)} user(s)*?",
                parse_mode="Markdown",
                reply_markup=confirm_kb,
            )

    async def _run_broadcast(self, context, status_msg, user_ids, source_message, text_arg):
        sent = 0
        blocked = 0
        failed = 0
        total = len(user_ids)

        for i, uid in enumerate(user_ids, start=1):
            try:
                if source_message:
                    await context.bot.copy_message(
                        chat_id=uid,
                        from_chat_id=source_message.chat_id,
                        message_id=source_message.message_id,
                    )
                else:
                    await context.bot.send_message(chat_id=uid, text=text_arg)
                sent += 1
            except Forbidden:
                # User blocked the bot or deleted their account — expected over time, not an error.
                blocked += 1
            except BadRequest as e:
                logger.warning("Broadcast BadRequest for %s: %s", uid, e)
                failed += 1
            except Exception as e:
                logger.error("Broadcast error for %s: %s", uid, e)
                failed += 1

            if i % 25 == 0 or i == total:
                try:
                    await status_msg.edit_text(
                        f"📣 Broadcasting... {i}/{total}\n"
                        f"✅ Sent: {sent}  🚫 Blocked: {blocked}  ❌ Failed: {failed}"
                    )
                except Exception:
                    pass  # message may be unchanged or rate-limited; ignore

            await asyncio.sleep(BROADCAST_DELAY)

        try:
            await status_msg.edit_text(
                "✅ *Broadcast complete!*\n"
                f"📨 Sent: {sent}\n"
                f"🚫 Blocked (left/blocked bot): {blocked}\n"
                f"❌ Failed: {failed}\n"
                f"👥 Total attempted: {total}",
                parse_mode="Markdown",
            )
        except Exception:
            pass
        logger.info("Broadcast finished — sent=%s blocked=%s failed=%s", sent, blocked, failed)

    # ── Export / backup ─────────────────────────────────────────────────────
    # /export — CSV of user_id, username, first_name, joined_at
    async def export_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Admins only.")
            return

        db = get_db(context)
        users = db.get_all_users()
        if not users:
            await update.message.reply_text("📭 No users stored yet.")
            return

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["user_id", "username", "first_name", "joined_at"])
        for row in users:
            writer.writerow([row["user_id"], row["username"], row["first_name"], row["joined_at"]])

        data = io.BytesIO(buf.getvalue().encode("utf-8"))
        filename = f"users_export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"

        await update.message.reply_document(
            document=data,
            filename=filename,
            caption=f"👥 {len(users)} user(s) exported.",
        )

    # /backup — raw SQLite DB file (users + files, everything)
    async def export_db(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Admins only.")
            return

        db = get_db(context)
        db_path = db.path
        if not os.path.exists(db_path):
            await update.message.reply_text("❌ Database file not found on disk.")
            return

        try:
            with open(db_path, "rb") as f:
                await update.message.reply_document(
                    document=f,
                    filename=f"file_vault_backup_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.db",
                    caption=(
                        "💾 Full database backup (users + file metadata).\n"
                        "To restore on a new host: place this file at your DB_PATH "
                        "(e.g. `/data/file_vault.db`) before starting the bot, and it "
                        "will pick up right where it left off — no users lost."
                    ),
                )
        except Exception as e:
            logger.error("Failed to export DB file: %s", e)
            await update.message.reply_text("❌ Couldn't export the database file.")
