import sqlite3
from config import DB_PATH

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()

    # USERS table (harus sesuai kebutuhan main.py)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            gender TEXT,
            age INTEGER,
            location TEXT,
            latitude REAL,
            longitude REAL,
            status TEXT,            -- idle / searching / chatting
            partner_id INTEGER,
            pref_gender TEXT,
            pref_age_min INTEGER,
            pref_age_max INTEGER,
            radius INTEGER          -- W A J I B untuk smart matching
        );
    """)

    # QUEUE table (matching pakai ini)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS queue (
            user_id INTEGER PRIMARY KEY,
            gender TEXT,
            age INTEGER
        );
    """)

    # PAIRING table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pairing (
            user_id INTEGER PRIMARY KEY,
            partner_id INTEGER
        );
    """)

    conn.commit()
    conn.close()


def save_user(user_id, field, value):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(f"UPDATE users SET {field} = ? WHERE user_id = ?", (value, user_id))
    conn.commit()
    conn.close()


def get_user(user_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row


def create_user_if_not_exists(user_id):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
    cek = cursor.fetchone()

    if not cek:
        cursor.execute("""
            INSERT INTO users (user_id, status, pref_gender, pref_age_min, pref_age_max, radius)
            VALUES (?, 'idle', 'any', 18, 50, 0)
        """, (user_id,))

    conn.commit()
    conn.close()
