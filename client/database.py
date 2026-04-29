import os
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional

from werkzeug.security import generate_password_hash


# Local SQLite database used by the detector node. It stores the user's local
# session records, registered cameras, and logs waiting to sync to the server.
def utc_now_text() -> str:
    """Return a compact UTC timestamp for local database records."""
    return datetime.utcnow().isoformat(sep=" ", timespec="seconds")


def ensure_column(cur, table: str, column: str, column_def: str):
    """Add a column during lightweight schema migration if it is missing."""
    cur.execute(f"PRAGMA table_info({table})")
    cols = [row[1] for row in cur.fetchall()]
    if column not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_def}")


# ---- Database setup and schema migration ----

def init_db(db_path: str) -> sqlite3.Connection:
    """Open the local client database and create required tables when missing."""
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time TEXT,
            event TEXT,
            source TEXT,
            clip TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        """
    )

    cur.execute(
        """
        CREATE TRIGGER IF NOT EXISTS logs_updated_at
        AFTER UPDATE ON logs
        FOR EACH ROW
        BEGIN
          UPDATE logs SET updated_at = datetime('now') WHERE id = OLD.id;
        END;
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_login TEXT,
            role TEXT DEFAULT 'user'
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS cameras (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            camera_name TEXT NOT NULL,
            camera_id TEXT NOT NULL,
            is_active BOOLEAN DEFAULT 1,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE(user_id, camera_id)
        )
        """
    )

    conn.commit()
    ensure_logs_sync_columns(conn)
    create_default_admin(conn)
    return conn


def ensure_logs_sync_columns(conn: sqlite3.Connection):
    """Ensure older client databases have the sync-tracking columns."""
    cur = conn.cursor()
    ensure_column(cur, "logs", "sync_status", "TEXT DEFAULT 'pending'")
    ensure_column(cur, "logs", "synced_at", "TEXT")
    ensure_column(cur, "logs", "central_log_id", "INTEGER")
    conn.commit()


# ---- Local users ----

def create_default_admin(conn):
    """Create a local admin record for offline/local admin checks."""
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    row = cur.fetchone()
    existing_users = int(row[0] if row else 0)
    if existing_users > 0:
        return

    cur.execute(
        "INSERT INTO users (username, password_hash, created_at, role) VALUES (?, ?, ?, ?)",
        ("admin", generate_password_hash("admin"), utc_now_text(), "admin"),
    )
    conn.commit()


def insert_user(conn, username, password_hash, role="user"):
    """Insert a local user and return the new id."""
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (username, password_hash, created_at, role) VALUES (?, ?, ?, ?)",
        (username, password_hash, utc_now_text(), role),
    )
    conn.commit()
    return cur.lastrowid


def get_user_by_username(conn, username):
    """Find a local user by username."""
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username = ?", (username,))
    row = cur.fetchone()
    return dict(row) if row else None


def get_user_by_id(conn, user_id):
    """Find a local user by id."""
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def update_user(conn, user_id: int, username: str, role: str):
    """Update a local user's display data and role."""
    cur = conn.cursor()
    cur.execute("UPDATE users SET username = ?, role = ? WHERE id = ?", (username, role, int(user_id)))
    conn.commit()


def set_user_password_hash(conn, user_id: int, password_hash: str):
    """Replace the stored local password hash for a user."""
    cur = conn.cursor()
    cur.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, int(user_id)))
    conn.commit()


# ---- Local event logs and sync state ----

# Create a local detection log, usually marked pending for sync.
def create_log(
    conn: sqlite3.Connection,
    time_text: str,
    event: str,
    source: str,
    clip: str,
    sync_status: str = "pending",
) -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO logs (time, event, source, clip, sync_status) VALUES (?, ?, ?, ?, ?)",
        (time_text, event, source, clip, sync_status),
    )
    conn.commit()
    return cur.lastrowid


def list_logs(conn: sqlite3.Connection, limit: int = 500) -> List[Dict]:
    """Return local logs, preferring central ids after sync."""
    cur = conn.cursor()
    cur.execute("SELECT id, time, event, source, clip, central_log_id FROM logs ORDER BY id DESC LIMIT ?", (int(limit),))
    rows = cur.fetchall()
    return [
        {
            "id": r[5] or r[0],
            "local_id": r[0],
            "central_log_id": r[5],
            "time": r[1],
            "event": r[2],
            "source": r[3],
            "clip": r[4],
        }
        for r in rows
    ]


def update_log(conn: sqlite3.Connection, log_id: int, time_text: str, event: str, source: str, clip: str) -> None:
    """Update a local log and mark it pending for resync."""
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE logs
        SET time = ?,
            event = ?,
            source = ?,
            clip = ?,
            sync_status = 'pending',
            synced_at = NULL
        WHERE id = ?
        """,
        (time_text, event, source, clip, int(log_id)),
    )
    conn.commit()


def delete_log(conn: sqlite3.Connection, log_id: int) -> None:
    """Delete a local log row."""
    cur = conn.cursor()
    cur.execute("DELETE FROM logs WHERE id = ?", (int(log_id),))
    conn.commit()


def list_unsynced_logs(conn: sqlite3.Connection, limit: int = 100) -> List[Dict]:
    """Return local logs that still need to be uploaded to the central server."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, time, event, source, clip, sync_status, synced_at, central_log_id
        FROM logs
        WHERE COALESCE(sync_status, 'pending') != 'synced'
        ORDER BY id ASC
        LIMIT ?
        """,
        (int(limit),),
    )
    rows = cur.fetchall()
    return [
        {
            "id": r[0],
            "time": r[1],
            "event": r[2],
            "source": r[3],
            "clip": r[4],
            "sync_status": r[5],
            "synced_at": r[6],
            "central_log_id": r[7],
        }
        for r in rows
    ]


def mark_log_synced(conn: sqlite3.Connection, log_id: int, central_log_id: Optional[int] = None) -> None:
    """Mark a local log as synced and remember its central log id."""
    cur = conn.cursor()
    cur.execute(
        "UPDATE logs SET sync_status = ?, synced_at = ?, central_log_id = ? WHERE id = ?",
        ("synced", utc_now_text(), central_log_id, int(log_id)),
    )
    conn.commit()


# ---- Local camera registry ----

def register_camera(conn: sqlite3.Connection, user_id: int, camera_name: str, camera_id: str) -> int:
    """Insert a local camera record for one user."""
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO cameras (user_id, camera_name, camera_id, created_at) VALUES (?, ?, ?, ?)",
        (user_id, camera_name, camera_id, utc_now_text()),
    )
    conn.commit()
    return cur.lastrowid


def get_user_cameras(conn: sqlite3.Connection, user_id: int) -> List[Dict]:
    """Return active cameras registered by one local user."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, user_id, camera_name, camera_id, is_active, created_at
        FROM cameras
        WHERE user_id = ? AND is_active = 1
        """,
        (user_id,),
    )
    rows = cur.fetchall()
    return [
        {
            "id": r[0],
            "user_id": r[1],
            "camera_name": r[2],
            "camera_id": r[3],
            "is_active": r[4],
            "created_at": r[5],
        }
        for r in rows
    ]
