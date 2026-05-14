# Telegram File Vault Bot

A Telegram bot that lets admins upload files available to community members for 3 days, after which they are automatically removed.

---

## Features

- **Admin uploads** — only listed admins can send files to the vault
- **Membership gate** — users must join required channels/groups before accessing files
- **Auto-expiry** — files are purged automatically after 3 days (configurable)
- **Inline delivery** — users click a button; the bot sends the file to their DM
- **Manual deletion** — admins can delete files early via `/delete <id>` or an inline button
- **Stats** — `/stats` shows user count and active file count

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure `config.py`

| Setting | Description |
|---|---|
| `BOT_TOKEN` | Token from [@BotFather](https://t.me/BotFather) |
| `ADMIN_IDS` | List of Telegram user IDs (integers) who can upload files |
| `REQUIRED_CHANNELS` | List of `@username` or numeric chat IDs users must join |
| `FILE_EXPIRY_DAYS` | Days before a file is auto-deleted (default: 3) |

### 3. Add bot to your channels/groups

The bot must be an **administrator** in every channel/group listed in `REQUIRED_CHANNELS` so it can verify membership via `getChatMember`.

### 4. Run

```bash
python bot.py
```

---

## File Structure

```
telegram_bot/
├── bot.py          # Entry point
├── config.py       # All configuration (edit this)
├── database.py     # SQLite layer
├── handlers.py     # All bot command and message handlers
├── scheduler.py    # Hourly job that purges expired files
└── requirements.txt
```

---

## How It Works

1. Admin sends a file → bot stores the Telegram `file_id` + metadata in SQLite with an expiry timestamp.
2. A user sends `/files` → bot checks membership → sends each active file as a message with a "Get File" button.
3. User clicks "Get File" → bot re-checks membership → forwards the file to the user's DM.
4. Every hour the scheduler runs `purge_expired()` which marks files past their expiry as deleted.

> **Note:** The bot stores Telegram `file_id` references, not the actual file bytes. Files are served directly from Telegram's servers. Telegram keeps files for at least a few months, so the 3-day expiry is enforced by the bot's own database; after deletion from the DB the files will no longer be served through the bot.
