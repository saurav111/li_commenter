from db import init_db, get_db

def migrate():
    init_db()

    # Ensure column exists even if table was created earlier
    with get_db() as (conn, cur):
        cur.execute("ALTER TABLE targets ADD COLUMN IF NOT EXISTS person_identifier TEXT;")
        cur.execute("ALTER TABLE targets ADD COLUMN IF NOT EXISTS public_identifier TEXT;")
        cur.execute("ALTER TABLE pending_reviews ADD COLUMN IF NOT EXISTS slack_channel TEXT;")
        cur.execute("ALTER TABLE pending_reviews ADD COLUMN IF NOT EXISTS slack_ts TEXT;")
        conn.commit()

if __name__ == "__main__":
    migrate()
    print("OK: migrations applied")
