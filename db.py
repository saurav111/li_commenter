import os
from contextlib import contextmanager
import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.environ["DATABASE_URL"]

@contextmanager
def get_db():
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            yield conn, cur

def init_db():
    with get_db() as (conn, cur):
        cur.execute("""
        CREATE TABLE IF NOT EXISTS targets (
            profile_url TEXT PRIMARY KEY,
            linkedin_urn TEXT,
            person_identifier TEXT,
            name TEXT,
            public_identifier TEXT
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            social_id TEXT PRIMARY KEY,
            comment_text TEXT,
            commented_at TIMESTAMPTZ
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS pending_reviews (
            social_id TEXT PRIMARY KEY,
            profile_name TEXT,
            post_text TEXT,
            generated_comment TEXT,
            status TEXT,
            created_at TIMESTAMPTZ,
            slack_channel TEXT,
            slack_ts TEXT
        );
        """)

        conn.commit()