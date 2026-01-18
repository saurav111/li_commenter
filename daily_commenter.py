import os
from datetime import datetime, timezone

from db import get_db
from salesnav import sync_salesnav_list, resolve_person_identifier
from unipile import list_recent_posts
from claude import generate_comment
from slack_notify import send_for_review

def main():
    dsn = os.environ["UNIPILE_DSN"]
    account_id = os.environ["UNIPILE_ACCOUNT_ID"]
    unipile_key = os.environ["UNIPILE_API_KEY"]
    salesnav_url = os.environ["SALESNAV_URL"]

    slack_token = os.environ["SLACK_BOT_TOKEN"]
    slack_user = os.environ["SLACK_USER_ID"]

    anthropic_key = os.environ["ANTHROPIC_API_KEY"]
    lookback_days = int(os.getenv("POST_LOOKBACK_DAYS", "30"))
    max_people = int(os.getenv("MAX_PEOPLE", "20"))
    max_per_day = int(os.getenv("MAX_COMMENTS_PER_DAY", "20"))

    # 1) Sync list and resolve identifiers
    sync_salesnav_list(dsn, account_id, unipile_key, salesnav_url, max_people=max_people)

    sent = 0

    with get_db() as (conn, c):
        c.execute("SELECT profile_url, person_identifier, name FROM targets")
        targets = c.fetchall()

        for t in targets:
            if sent >= max_per_day:
                break

            profile_url = t["profile_url"]
            person_identifier = t.get("person_identifier")
            name = t.get("name") or "name"

            # Fallback: resolve if missing
            if not person_identifier:
                try:
                    person_identifier = resolve_person_identifier(dsn, account_id, unipile_key, profile_url)
                    if person_identifier:
                        c.execute(
                            "UPDATE targets SET person_identifier=%s WHERE profile_url=%s",
                            (person_identifier, profile_url)
                        )
                        conn.commit()
                except Exception as e:
                    print("[WARN] Could not resolve identifier for", profile_url, repr(e))
                    continue

            if not person_identifier:
                print("[INFO] Missing person identifier for", name)
                continue

            posts = list_recent_posts(
                dsn,
                account_id,
                unipile_key,
                person_identifier,
                lookback_days=lookback_days,
                limit=20,
                debug=False,
            )

            if not posts:
                print(f"[INFO] No posts in last {lookback_days}d for {name}")
                continue

            # Pick newest eligible post (first in list is usually newest, but we’ll just use first)
            for post in posts:
                if sent >= max_per_day:
                    break

                social_id = post.get("id") or post.get("social_id") or post.get("urn")
                post_text = post.get("text") or post.get("content") or ""

                if not social_id:
                    continue

                # skip if already commented
                c.execute("SELECT 1 FROM comments WHERE social_id=%s", (social_id,))
                if c.fetchone():
                    continue

                comment = generate_comment(anthropic_key, name, post_text)

                # store pending review (Slack approval flow)
                c.execute("""
                    INSERT INTO pending_reviews
                      (social_id, profile_name, post_text, generated_comment, status, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (social_id) DO NOTHING
                """, (
                    social_id,
                    name,
                    post_text,
                    comment,
                    "pending",
                    datetime.now(timezone.utc).isoformat(),
                ))
                conn.commit()

                send_for_review(
                    slack_token,
                    slack_user,
                    social_id,
                    name,
                    post_text,
                    comment,
                )

                sent += 1
                print(f"[OK] Sent Slack review {sent}/{max_per_day} for {name} ({social_id})")

    print(f"[DONE] Sent {sent} Slack review messages")

if __name__ == "__main__":
    main()