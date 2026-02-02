import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "detections.db")

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()
cur.execute("UPDATE users SET role = 'admin' WHERE username = 'admin'")
conn.commit()
print("Updated rows:", cur.rowcount)
conn.close()