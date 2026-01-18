import os, time, random
from datetime import datetime, timezone
from db import init_db, get_db
from salesnav import sync_salesnav_list
from unipile import list_recent_posts, comment_on_post
from claude import generate_comment

def human_sleep(min_s, max_s):
    time.sleep(random.uniform(min_s, max_s))

# ENV
DSN = os.environ["UNIPILE_DSN"]
ACCOUNT_ID = os.environ["UNIPILE_ACCOUNT_ID"]
UNIPILE_KEY = os.environ["UNIPILE_API_KEY"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]

MAX_COMMENTS = int(os.getenv("MAX_COMMENTS_PER_DAY", "20"))

SALESNAV_URL = "https://www.linkedin.com/sales/search/people?query=(filters:List((type:LEAD_LIST,values:List((id:7373374312965111808,text:Podcast%20guests)))))"

def main():
    init_db()
    sync_salesnav_list(DSN, ACCOUNT_ID, UNIPILE_KEY, SALESNAV_URL)

    conn = get_db()
    c = conn.cursor()

    c.execute("SELECT * FROM targets")
    targets = c.fetchall()

    comments_today = 0

    for t in targets:
        if comments_today >= MAX_COMMENTS:
            break

        posts = list_recent_posts(
            DSN, ACCOUNT_ID, UNIPILE_KEY, t["linkedin_urn"]
        )

        for p in posts:
            social_id = p.get("social_id")
            if not social_id:
                continue

            c.execute("SELECT 1 FROM comments WHERE social_id=?", (social_id,))
            if c.fetchone():
                continue

            post_text = p.get("text", "")
            comment = generate_comment(
                ANTHROPIC_KEY,
                t["name"] or "the author",
                post_text
            )

            comment_on_post(
                DSN, ACCOUNT_ID, UNIPILE_KEY, social_id, comment
            )

            human_sleep(8, 15)  # simulate reading profile


            c.execute("""
            INSERT INTO comments(social_id, comment_text, commented_at)
            VALUES (%s, %s, %s)
            """, (social_id, comment, datetime.now(timezone.utc)))

            conn.commit()

            comments_today += 1
            time.sleep(random.randint(30, 120))
            break

    conn.close()

if __name__ == "__main__":
    main()
