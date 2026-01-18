import sqlite3
from pathlib import Path

DB_PATH = Path("state.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS targets (
        profile_url TEXT PRIMARY KEY,
        linkedin_urn TEXT,
        name TEXT
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS comments (
        social_id TEXT PRIMARY KEY,
        comment_text TEXT,
        commented_at DATETIME
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS pending_reviews (
        social_id TEXT PRIMARY KEY,
        profile_name TEXT,
        post_text TEXT,
        generated_comment TEXT,
        status TEXT,
        created_at DATETIME
    )
    """)

    conn.commit()
    conn.close()
