from db import get_db
from salesnav import sync_salesnav_list
from unipile import list_recent_posts
from slack_notify import send_for_review
from claude import generate_comment
import os

salesnav_url = os.environ["SALESNAV_URL"]

def main():
    dsn = os.environ["UNIPILE_DSN"]
    account_id = os.environ["UNIPILE_ACCOUNT_ID"]
    unipile_key = os.environ["UNIPILE_API_KEY"]
    slack_token = os.environ["SLACK_BOT_TOKEN"]
    slack_user = os.environ["SLACK_USER_ID"]
    anthropic_key = os.environ["ANTHROPIC_API_KEY"]

    # 1) Sync Sales Nav list
    inserted = sync_salesnav_list(
        dsn,
        account_id,
        unipile_key,
        salesnav_url,
        max_people=20
    )
    print(f"[SYNC] Inserted {inserted} targets from Sales Nav search")

    with get_db() as (conn, c):

        # 2) Iterate targets
        c.execute("SELECT profile_url, linkedin_urn, name FROM targets")
        targets = c.fetchall()

        for profile_url, urn, name in targets:

            posts = list_recent_posts(
                dsn,
                account_id,
                unipile_key,
                urn,
                lookback_days=30
            )

            if not posts:
                print(f"[INFO] No posts in last 30d for {name}")
                continue

            for post in posts:
                social_id = post["id"]
                post_text = post.get("text", "")

                # Skip if already commented
                c.execute(
                    "SELECT 1 FROM comments WHERE social_id=%s",
                    (social_id,)
                )
                if c.fetchone():
                    continue

                comment = generate_comment(anthropic_key, name, post_text)

                # Insert pending review
                c.execute("""
                    INSERT INTO pending_reviews
                      (social_id, profile_name, post_text, generated_comment, status, created_at)
                    VALUES (%s, %s, %s, %s, %s, now())
                    ON CONFLICT (social_id) DO NOTHING
                """, (social_id, name, post_text, comment, "pending"))

                conn.commit()

                send_for_review(
                    slack_token,
                    slack_user,
                    social_id,
                    name,
                    post_text,
                    comment
                )

if __name__ == "__main__":
    main()