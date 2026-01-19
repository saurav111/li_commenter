from db import init_db, get_db

def migrate():
    init_db()

    # Ensure column exists even if table was created earlier
    with get_db() as (conn, cur):
        cur.execute("ALTER TABLE targets ADD COLUMN IF NOT EXISTS person_identifier TEXT;")
        cur.execute("ALTER TABLE targets ADD COLUMN IF NOT EXISTS public_identifier TEXT;")
        cur.execute("ALTER TABLE pending_reviews ADD COLUMN IF NOT EXISTS slack_channel TEXT;")
        cur.execute("ALTER TABLE pending_reviews ADD COLUMN IF NOT EXISTS slack_ts TEXT;")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS handled_posts (
            social_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            handled_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """)
        # NEW: pool of candidate posts to comment on
        cur.execute("""
        CREATE TABLE IF NOT EXISTS post_pool (
            social_id TEXT PRIMARY KEY,
            person_identifier TEXT,
            profile_url TEXT,
            profile_name TEXT,
            post_text TEXT,
            post_created_at TIMESTAMPTZ NULL,
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """)

        # helpful indexes
        cur.execute("CREATE INDEX IF NOT EXISTS idx_post_pool_person ON post_pool(person_identifier);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_post_pool_last_seen ON post_pool(last_seen_at);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_handled_posts_status ON handled_posts(status);")
        cur.execute("ALTER TABLE targets ADD COLUMN IF NOT EXISTS salesnav_lead_id TEXT;")

        conn.commit()

if __name__ == "__main__":
    migrate()
    print("OK: migrations applied")
