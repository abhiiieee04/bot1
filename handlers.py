"""
handlers.py — All command and message handlers.

Permission levels:
  Owner  (OWNER_ID env var) — everything: broadcast, export, backup, manage admins
  Admin  (stored in DB)     — upload files, delete files, stats, browse files
  User                      — browse & download files (after joining channels)
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

from config import OWNER_ID, REQUIRED_CHANNELS, FILE_EXPIRY_DAYS
from database import Database

logger = logging.getLogger(__name__)

# ── Categories ────────────────────────────────────────────────────────────────
CATEGORIES = {
    "config": "⚙️ Config",
    "combos": "🔀 Combos",
    "valids": "✅ Valids",
}

BROADCAST_DELAY = 0.05


# ── Permission helpers ────────────────────────────────────────────────────────
def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


def is_admin(user_id: int, db: Database) -> bool:
    """Admins + owner both count as admin for file management."""
    return user_id == OWNER_ID or db.is_admin(user_id)


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
    return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M UTC")


def category_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=f"cat_{key}")]
        for key, label in CATEGORIES.items()
    ])


def admin_upload_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Upload to {label}", callback_data=f"upload_cat_{key}")]
        for key, label in CATEGORIES.items()
    ])


# ── Handler class ─────────────────────────────────────────────────────────────
class BotHandlers:

    # /start
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        db   = get_db(context)
        db.register_user(user.id, user.username or "", user.first_name or "")

        if is_owner(user.id):
            await update.message.reply_text(
                f"👑 Welcome, *Owner*!\n\n"
                "You have full access to all commands.\n\n"
                "*Owner commands:*\n"
                "👤 /addadmin <user\\_id> — add an admin\n"
                "❌ /removeadmin <user\\_id> — remove an admin\n"
                "📋 /listadmins — list all admins\n"
                "📣 /broadcast — message every user\n"
                "📥 /export — export users as CSV\n"
                "💾 /backup — download full database\n\n"
                "*Shared admin commands:*\n"
                "📂 /files — browse files\n"
                "🗑 /delete <id> — delete a file\n"
                "📊 /stats — bot statistics\n"
                "❓ /help — help",
                parse_mode="Markdown",
            )
        elif is_admin(user.id, db):
            await update.message.reply_text(
                f"👋 Welcome back, admin *{user.first_name}*!\n\n"
                "Send any file and I'll ask which category to put it in.\n\n"
                "*Your commands:*\n"
                "📂 /files — browse files by category\n"
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
        db   = get_db(context)

        if is_owner(user.id):
            text = (
                "👑 *Owner Commands*\n"
                "👤 /addadmin <user\\_id> — grant admin access\n"
                "❌ /removeadmin <user\\_id> — revoke admin access\n"
                "📋 /listadmins — list all current admins\n"
                "📣 /broadcast — send a message to all users (with confirmation)\n"
                "📥 /export — download all users as CSV\n"
                "💾 /backup — download raw database backup\n\n"
                "*Shared with admins:*\n"
                "📤 Send any file → choose a category\n"
                "📂 /files — browse by category\n"
                "🗑 /delete <id> — delete a file\n"
                f"📊 /stats — usage stats\n\n"
                f"Files auto-delete after *{FILE_EXPIRY_DAYS} days*."
            )
        elif is_admin(user.id, db):
            text = (
                "🔧 *Admin Commands*\n"
                "📤 Send any file → choose a category\n"
                "📂 /files — browse by category\n"
                "🗑 /delete <id> — delete a file\n"
                f"📊 /stats — usage stats\n\n"
                f"Files auto-delete after *{FILE_EXPIRY_DAYS} days*."
            )
        else:
            text = (
                "📂 *File Vault*\n"
                "/start — check membership\n"
                "/files — browse files by category"
            )
        await update.message.reply_text(text, parse_mode="Markdown")

    # ── Owner: manage admins ──────────────────────────────────────────────────

    # /addadmin <user_id>
    async def add_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_owner(update.effective_user.id):
            await update.message.reply_text("⛔ Only the owner can manage admins.")
            return

        if not context.args:
            await update.message.reply_text("Usage: /addadmin <user_id>")
            return

        try:
            target_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID — must be a number.")
            return

        if target_id == OWNER_ID:
            await update.message.reply_text("👑 You're already the owner — no need to add yourself.")
            return

        db = get_db(context)

        # Try to get their name from Telegram
        try:
            chat = await context.bot.get_chat(target_id)
            username   = chat.username   or ""
            first_name = chat.first_name or str(target_id)
        except Exception:
            username   = ""
            first_name = str(target_id)

        db.add_admin(target_id, username, first_name)

        # Update their command menu so /broadcast etc don't show up
        try:
            from telegram import BotCommandScopeChat
            from bot import PUBLIC_COMMANDS, ADMIN_ONLY_COMMANDS
            await context.bot.set_my_commands(
                PUBLIC_COMMANDS + ADMIN_ONLY_COMMANDS,
                scope=BotCommandScopeChat(chat_id=target_id),
            )
        except Exception:
            pass

        await update.message.reply_text(
            f"✅ *{first_name}* (`{target_id}`) has been added as an admin.",
            parse_mode="Markdown",
        )

        # Notify the new admin
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text="🎉 You have been granted *admin access* to this bot.\n\nSend /help to see your commands.",
                parse_mode="Markdown",
            )
        except Exception:
            pass

    # /removeadmin <user_id>
    async def remove_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_owner(update.effective_user.id):
            await update.message.reply_text("⛔ Only the owner can manage admins.")
            return

        if not context.args:
            await update.message.reply_text("Usage: /removeadmin <user_id>")
            return

        try:
            target_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID — must be a number.")
            return

        db = get_db(context)
        if not db.is_admin(target_id):
            await update.message.reply_text("⚠️ That user is not an admin.")
            return

        db.remove_admin(target_id)

        # Reset their command menu back to public only
        try:
            from telegram import BotCommandScopeChat, BotCommandScopeDefault
            from bot import PUBLIC_COMMANDS
            await context.bot.set_my_commands(
                PUBLIC_COMMANDS,
                scope=BotCommandScopeChat(chat_id=target_id),
            )
        except Exception:
            pass

        await update.message.reply_text(
            f"✅ User `{target_id}` has been removed as admin.",
            parse_mode="Markdown",
        )

        try:
            await context.bot.send_message(
                chat_id=target_id,
                text="ℹ️ Your admin access to this bot has been removed.",
            )
        except Exception:
            pass

    # /listadmins
    async def list_admins(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_owner(update.effective_user.id):
            await update.message.reply_text("⛔ Only the owner can view the admin list.")
            return

        db     = get_db(context)
        admins = db.get_all_admins()

        if not admins:
            await update.message.reply_text("📭 No admins added yet.\n\nUse /addadmin <user_id> to add one.")
            return

        lines = ["👥 *Current Admins:*\n"]
        for i, row in enumerate(admins, 1):
            name = row["first_name"] or "Unknown"
            un   = f"@{row['username']}" if row["username"] else "no username"
            added = fmt_dt(row["added_at"])
            lines.append(f"{i}. *{name}* ({un})\n   🆔 `{row['user_id']}` | Added: {added}")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    # ── File upload (admin + owner) ───────────────────────────────────────────

    async def upload_prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        db = get_db(context)
        if not is_admin(update.effective_user.id, db):
            await update.message.reply_text("⛔ This command is for admins only.")
            return
        await update.message.reply_text(
            "📤 Just send me any file and I'll ask which category to place it in."
        )

    async def handle_file_upload(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        db   = get_db(context)
        if not is_admin(user.id, db):
            await update.message.reply_text("⛔ Only admins can upload files.")
            return

        msg = update.message
        if msg.document:
            context.user_data["pending_file"] = {
                "file_id":   msg.document.file_id,
                "file_type": "document",
                "file_name": msg.document.file_name or "unnamed",
                "caption":   msg.caption or "",
            }
        elif msg.photo:
            context.user_data["pending_file"] = {
                "file_id":   msg.photo[-1].file_id,
                "file_type": "photo",
                "file_name": "photo.jpg",
                "caption":   msg.caption or "",
            }
        elif msg.video:
            context.user_data["pending_file"] = {
                "file_id":   msg.video.file_id,
                "file_type": "video",
                "file_name": msg.video.file_name or "video.mp4",
                "caption":   msg.caption or "",
            }
        elif msg.audio:
            context.user_data["pending_file"] = {
                "file_id":   msg.audio.file_id,
                "file_type": "audio",
                "file_name": msg.audio.file_name or "audio.mp3",
                "caption":   msg.caption or "",
            }
        else:
            await msg.reply_text("❌ Unsupported file type.")
            return

        await msg.reply_text(
            "📁 Which category should this file go into?",
            reply_markup=admin_upload_menu(),
        )

    # /files
    async def list_files(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        db   = get_db(context)
        if not is_admin(user.id, db):
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

    # /delete <id>
    async def delete_file(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        db = get_db(context)
        if not is_admin(update.effective_user.id, db):
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

        row = db.get_file(file_db_id)
        if not row:
            await update.message.reply_text("❌ File not found or already deleted.")
            return

        db.mark_deleted(file_db_id)
        await update.message.reply_text(f"✅ File `{file_db_id}` deleted.", parse_mode="Markdown")

    # /stats
    async def stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        db = get_db(context)
        if not is_admin(update.effective_user.id, db):
            await update.message.reply_text("⛔ Admins only.")
            return

        lines = ["📊 *Bot Stats*", f"👥 Total users: {db.get_user_count()}"]
        for key, label in CATEGORIES.items():
            count = len(db.get_files_by_category(key))
            lines.append(f"{label}: {count} file(s)")
        lines.append(f"👤 Total admins: {len(db.get_admin_ids())}")
        lines.append(f"⏳ File expiry: {FILE_EXPIRY_DAYS} days")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    # ── Broadcast (owner only) ────────────────────────────────────────────────
    async def broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        db   = get_db(context)
        if not is_admin(user.id, db):
            await update.message.reply_text("⛔ Admins only.")
            return

        db       = get_db(context)
        user_ids = db.get_all_user_ids()
        if not user_ids:
            await update.message.reply_text("📭 No users to broadcast to yet.")
            return

        source_message = update.message.reply_to_message
        text_arg       = " ".join(context.args) if context.args else None

        if not source_message and not text_arg:
            await update.message.reply_text(
                "Usage:\n"
                "• `/broadcast <message>` — sends plain text to all users\n"
                "• Reply to any message with `/broadcast` — copies it as-is to all users",
                parse_mode="Markdown",
            )
            return

        context.bot_data.setdefault("pending_broadcasts", {})[user.id] = {
            "text":              text_arg,
            "source_chat_id":    source_message.chat_id    if source_message else None,
            "source_message_id": source_message.message_id if source_message else None,
        }

        confirm_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Confirm & Send", callback_data="bcast_confirm"),
            InlineKeyboardButton("❌ Cancel",         callback_data="bcast_cancel"),
        ]])

        if source_message:
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
        sent    = 0
        blocked = 0
        failed  = 0
        total   = len(user_ids)

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
                    pass

            await asyncio.sleep(BROADCAST_DELAY)

        try:
            await status_msg.edit_text(
                "✅ *Broadcast complete!*\n"
                f"📨 Sent: {sent}\n"
                f"🚫 Blocked: {blocked}\n"
                f"❌ Failed: {failed}\n"
                f"👥 Total attempted: {total}",
                parse_mode="Markdown",
            )
        except Exception:
            pass

    # ── Export / backup (owner only) ──────────────────────────────────────────

    # /export
    async def export_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        db = get_db(context)
        if not is_admin(update.effective_user.id, db):
            await update.message.reply_text("⛔ Admins only.")
            return

        db    = get_db(context)
        users = db.get_all_users()
        if not users:
            await update.message.reply_text("📭 No users stored yet.")
            return

        buf    = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["user_id", "username", "first_name", "joined_at"])
        for row in users:
            writer.writerow([row["user_id"], row["username"], row["first_name"], row["joined_at"]])

        data     = io.BytesIO(buf.getvalue().encode("utf-8"))
        filename = f"users_export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"

        await update.message.reply_document(
            document=data,
            filename=filename,
            caption=f"👥 {len(users)} user(s) exported.",
        )

    # /backup
    async def export_db(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        db = get_db(context)
        if not is_admin(update.effective_user.id, db):
            await update.message.reply_text("⛔ Admins only.")
            return

        db      = get_db(context)
        db_path = db.path
        if not os.path.exists(db_path):
            await update.message.reply_text("❌ Database file not found on disk.")
            return

        try:
            with open(db_path, "rb") as f:
                await update.message.reply_document(
                    document=f,
                    filename=f"backup_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.db",
                    caption=(
                        "💾 Full database backup.\n"
                        "To restore: place this file at your DB_PATH before starting the bot."
                    ),
                )
        except Exception as e:
            logger.error("Failed to export DB: %s", e)
            await update.message.reply_text("❌ Couldn't export the database file.")

    # ── Inline callbacks ──────────────────────────────────────────────────────
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        user = query.from_user
        data = query.data
        db   = get_db(context)

        # Broadcast cancelled
        if data == "bcast_cancel" and is_admin(user.id, db):
            context.bot_data.get("pending_broadcasts", {}).pop(user.id, None)
            await query.edit_message_text("❌ Broadcast cancelled. No messages were sent.")
            return

        # Broadcast confirmed
        if data == "bcast_confirm" and is_admin(user.id, db):
            pending = context.bot_data.get("pending_broadcasts", {}).pop(user.id, None)
            if not pending:
                await query.edit_message_text("⚠️ This broadcast request expired. Run /broadcast again.")
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
        if data.startswith("upload_cat_") and is_admin(user.id, db):
            category = data.split("upload_cat_", 1)[1]
            pending  = context.user_data.get("pending_file")
            if not pending:
                await query.edit_message_text("❌ No pending file found. Please send the file again.")
                return

            db_id     = db.add_file(
                pending["file_id"], pending["file_type"],
                pending["file_name"], pending["caption"],
                user.id, category,
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

        # Browse category
        if data.startswith("cat_"):
            category = data.split("cat_", 1)[1]
            if not is_admin(user.id, db):
                missing = await check_membership(user.id, context)
                if missing:
                    await query.message.reply_text(
                        "🔒 Join the required channels first:\n" +
                        "\n".join(f"• {ch}" for ch in missing)
                    )
                    return

            files     = db.get_files_by_category(category)
            cat_label = CATEGORIES.get(category, category)
            if not files:
                await query.edit_message_text(f"📭 No files in {cat_label} right now.")
                return

            await query.edit_message_text(
                f"{cat_label} — *{len(files)} file(s)*:", parse_mode="Markdown"
            )

            for row in files:
                expiry_str     = fmt_dt(row["expiry_time"])
                caption_preview = f"\n📝 {row['caption']}" if row["caption"] else ""
                type_emoji     = {"document": "📄", "photo": "🖼", "video": "🎬", "audio": "🎵"}.get(row["file_type"], "📁")
                keyboard       = [[InlineKeyboardButton("⬇️ Get File", callback_data=f"get_{row['id']}")]]
                if is_admin(user.id, db):
                    keyboard[0].append(InlineKeyboardButton("🗑 Delete", callback_data=f"del_{row['id']}"))
                await query.message.reply_text(
                    f"{type_emoji} *{row['file_name']}*{caption_preview}\n"
                    f"🆔 ID: `{row['id']}` | ⏳ Expires: {expiry_str}",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
            return

        # Get file
        if data.startswith("get_"):
            if not is_admin(user.id, db):
                missing = await check_membership(user.id, context)
                if missing:
                    await query.message.reply_text(
                        "🔒 Join the required channels first:\n" +
                        "\n".join(f"• {ch}" for ch in missing)
                    )
                    return

            file_db_id = int(data.split("_", 1)[1])
            row        = db.get_file(file_db_id)
            if not row:
                await query.message.reply_text("❌ File not found or has expired.")
                return

            send_map  = {
                "document": context.bot.send_document,
                "photo":    context.bot.send_photo,
                "video":    context.bot.send_video,
                "audio":    context.bot.send_audio,
            }
            sender    = send_map.get(row["file_type"], context.bot.send_document)
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

        # Delete (admin/owner)
        if data.startswith("del_") and is_admin(user.id, db):
            file_db_id = int(data.split("_", 1)[1])
            db.mark_deleted(file_db_id)
            await query.edit_message_text("🗑 File deleted.")
