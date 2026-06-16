"""
database.py — SQLite persistence for files and users.
"""

import sqlite3
import logging
from datetime import datetime, timedelta

from config import DB_PATH, FILE_EXPIRY_DAYS

logger = logging.getLogger(__name__)


class Database:
    def __init__(self):
        self.path = DB_PATH

    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    # ── Schema ──────────────────────────────────────────────────────────────
    def init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id TEXT NOT NULL,
                    file_type TEXT NOT NULL,
                    file_name TEXT,
                    caption TEXT,
                    category TEXT NOT NULL DEFAULT 'config',
                    uploader_id INTEGER NOT NULL,
                    upload_time TEXT NOT NULL,
                    expiry_time TEXT NOT NULL,
                    deleted INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    joined_at TEXT NOT NULL
                );
            """)
            # migrate existing DB if category column is missing
            cols = [r[1] for r in conn.execute("PRAGMA table_info(files)").fetchall()]
            if "category" not in cols:
                conn.execute("ALTER TABLE files ADD COLUMN category TEXT NOT NULL DEFAULT 'config'")
        logger.info("Database initialised.")

    # ── File operations ─────────────────────────────────────────────────────
    def add_file(self, file_id: str, file_type: str, file_name: str,
                 caption: str, uploader_id: int, category: str = "config") -> int:
        now = datetime.utcnow()
        expiry = now + timedelta(days=FILE_EXPIRY_DAYS)
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO files
                   (file_id, file_type, file_name, caption, category,
                    uploader_id, upload_time, expiry_time)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (file_id, file_type, file_name, caption, category,
                 uploader_id, now.isoformat(), expiry.isoformat()),
            )
            return cur.lastrowid

    def get_active_files(self):
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM files WHERE deleted=0 ORDER BY upload_time DESC"
            ).fetchall()

    def get_files_by_category(self, category: str):
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM files WHERE deleted=0 AND category=? ORDER BY upload_time DESC",
                (category,)
            ).fetchall()

    def get_file(self, file_db_id: int):
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM files WHERE id=? AND deleted=0", (file_db_id,)
            ).fetchone()

    def mark_deleted(self, file_db_id: int):
        with self._conn() as conn:
            conn.execute(
                "UPDATE files SET deleted=1 WHERE id=?", (file_db_id,)
            )

    def get_expired_files(self):
        now = datetime.utcnow().isoformat()
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM files WHERE deleted=0 AND expiry_time <= ?", (now,)
            ).fetchall()

    def purge_expired(self) -> int:
        expired = self.get_expired_files()
        for row in expired:
            self.mark_deleted(row["id"])
        return len(expired)

    # ── User operations ─────────────────────────────────────────────────────
    def register_user(self, user_id: int, username: str, first_name: str):
        with self._conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO users (user_id, username, first_name, joined_at)
                   VALUES (?,?,?,?)""",
                (user_id, username, first_name, datetime.utcnow().isoformat()),
            )

    def get_user_count(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def get_all_user_ids(self) -> list:
        """All user_ids that have ever started the bot — used for broadcasts."""
        with self._conn() as conn:
            rows = conn.execute("SELECT user_id FROM users").fetchall()
        return [row["user_id"] for row in rows]

    def get_all_users(self):
        """Full user rows (user_id, username, first_name, joined_at) — used for export."""
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM users ORDER BY joined_at"
            ).fetchall()

    def get_active_file_count(self) -> int:
        with self._conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM files WHERE deleted=0"
            ).fetchone()[0]
