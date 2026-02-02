import sqlite3
import os
from typing import Optional, List, Dict
import cv2
from datetime import datetime


def init_db(db_path: str = "detections.db") -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Existing detections table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS detections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        frame_no INTEGER,
        x1 INTEGER,
        y1 INTEGER,
        x2 INTEGER,
        y2 INTEGER,
        width INTEGER,
        height INTEGER,
        confidence REAL,
        class_id INTEGER,
        label TEXT,
        crop_path TEXT,
        model TEXT,
        video_path TEXT
    );
    """)

    # NEW: logs table for saved clips + audit
    cur.execute("""
    CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        time TEXT,
        event TEXT,
        source TEXT,
        clip TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );
    """)

    # Trigger to update updated_at
    cur.execute("""
    CREATE TRIGGER IF NOT EXISTS logs_updated_at
    AFTER UPDATE ON logs
    FOR EACH ROW
    BEGIN
      UPDATE logs SET updated_at = datetime('now') WHERE id = OLD.id;
    END;
    """)

    #users table 
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TEXT NOT NULL,
        last_login TEXT,
        role TEXT DEFAULT 'user'
    )
    """)

    conn.commit()
    return conn

#log in
def insert_user(conn, username, password_hash, role='user'):
    cur = conn.cursor()
    now = datetime.utcnow().isoformat(sep=' ', timespec='seconds')
    try:
        cur.execute(
            "INSERT INTO users (username, password_hash, created_at, role) VALUES (?, ?, ?, ?)",
            (username, password_hash, now, role)
        )
        conn.commit()
        return cur.lastrowid
    except Exception as e:
        # unique constraint or other error
        raise

def get_user_by_username(conn, username):
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username = ?", (username,))
    row = cur.fetchone()
    return dict(row) if row else None

def get_user_by_id(conn, user_id):
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    return dict(row) if row else None

def update_last_login(conn, user_id):
    cur = conn.cursor()
    now = datetime.utcnow().isoformat(sep=' ', timespec='seconds')
    cur.execute("UPDATE users SET last_login = ? WHERE id = ?", (now, user_id))
    conn.commit()

# User Management
def list_users(conn):
    cur = conn.cursor()
    cur.execute("SELECT id, username, created_at, last_login, role FROM users ORDER BY id;")
    rows = cur.fetchall()
    users = []
    for r in rows:
        users.append({
            "id": r[0],
            "username": r[1],
            "created_at": r[2],
            "last_login": r[3],
            "role": r[4]
        })
    return users

#  username / role
def update_user(conn, user_id: int, username: str, role: str):
    cur = conn.cursor()
    cur.execute("UPDATE users SET username = ?, role = ? WHERE id = ?", (username, role, int(user_id)))
    conn.commit()

def delete_user(conn, user_id: int):
    cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE id = ?", (int(user_id),))
    conn.commit()

def set_user_password_hash(conn, user_id: int, password_hash: str):
    cur = conn.cursor()
    cur.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, int(user_id)))
    conn.commit()


# -------------------------
# Existing detection helpers
# -------------------------
def insert_detection(conn: sqlite3.Connection,
                     timestamp: str,
                     frame_no: int,
                     x1: int, y1: int, x2: int, y2: int,
                     width: int, height: int,
                     confidence: float,
                     class_id: Optional[int],
                     label: Optional[str],
                     crop_path: Optional[str],
                     model: Optional[str],
                     video_path: Optional[str]) -> int:
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO detections
    (timestamp, frame_no, x1, y1, x2, y2, width, height, confidence, class_id, label, crop_path, model, video_path)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (timestamp, frame_no, x1, y1, x2, y2, width, height, confidence, class_id, label, crop_path, model, video_path))
    conn.commit()
    return cur.lastrowid


def save_crop(image, save_dir: str, base_name: str) -> str:
    os.makedirs(save_dir, exist_ok=True)
    filename = f"{base_name}.jpg"
    save_path = os.path.join(save_dir, filename)
    cv2.imwrite(save_path, image, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    return save_path


def fetch_all(conn: sqlite3.Connection):
    cur = conn.cursor()
    cur.execute("SELECT * FROM detections ORDER BY id;")
    return cur.fetchall()


def export_to_csv(conn: sqlite3.Connection, csv_path: str):
    import csv
    rows = fetch_all(conn)
    os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id","timestamp","frame_no","x1","y1","x2","y2","width","height","confidence","class_id","label","crop_path","model","video_path"])
        writer.writerows(rows)
    return csv_path


# -------------------------
# NEW: Logs CRUD helpers
# -------------------------
def create_log(conn: sqlite3.Connection, time_text: str, event: str, source: str, clip: str) -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO logs (time, event, source, clip) VALUES (?, ?, ?, ?)",
        (time_text, event, source, clip)
    )
    conn.commit()
    return cur.lastrowid


def list_logs(conn: sqlite3.Connection, limit: int = 500) -> List[Dict]:
    cur = conn.cursor()
    cur.execute(
        "SELECT id, time, event, source, clip FROM logs ORDER BY id DESC LIMIT ?",
        (int(limit),)
    )
    rows = cur.fetchall()
    return [
        {"id": r[0], "time": r[1], "event": r[2], "source": r[3], "clip": r[4]}
        for r in rows
    ]


def update_log(conn: sqlite3.Connection, log_id: int,
               time_text: str, event: str, source: str, clip: str) -> None:
    cur = conn.cursor()
    cur.execute(
        "UPDATE logs SET time = ?, event = ?, source = ?, clip = ? WHERE id = ?",
        (time_text, event, source, clip, int(log_id))
    )
    conn.commit()


def delete_log(conn: sqlite3.Connection, log_id: int) -> None:
    cur = conn.cursor()
    cur.execute("DELETE FROM logs WHERE id = ?", (int(log_id),))
    conn.commit()
