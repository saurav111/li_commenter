import os
import time
import random
from datetime import datetime, timezone

from db import init_db, get_db
from salesnav import sync_salesnav_list
from unipile import list_recent_posts
from claude import generate_comment
from slack_notify import send_for_review

SALESNAV_URL = "https://www.linkedin.com/sales/search/people?query=(filters%3AList((type%3ALEAD_LIST%2Cvalues%3AList((id%3A7373374312965111808%2Ctext%3APodcast%2520guests%2CselectionType%3AINCLUDED)))))&viewAllFilters=true"

def human_sleep(a, b):
    time.sleep(random.uniform(a, b))

def main():
    # Required env vars
    dsn = os.environ["UNIPILE_DSN"]
    account_id = os.environ["UNIPILE_ACCOUNT_ID"]
    unipile_key = os.environ["UNIPILE_API_KEY"]

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")  # optional if you want stubbed comments

    slack_token = os.environ["SLACK_BOT_TOKEN"]
    slack_user_id = os.environ["SLACK_USER_ID"]

    # Test params
    max_people = int(os.environ.get("TEST_MAX_PEOPLE", "5"))
    use_claude = os.environ.get("TEST_USE_CLAUDE", "1") == "1"

    init_db()

    print(f"[TEST] Syncing Sales Nav list (max_people={max_people})...")
    sync_salesnav_list(dsn, account_id, unipile_key, SALESNAV_URL, max_people=max_people)

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT profile_url, linkedin_urn, name FROM targets LIMIT ?", (max_people,))
    targets = c.fetchall()

    if not targets:
        raise RuntimeError("No targets synced. Check Sales Nav URL + Unipile /linkedin/search response.")

    print(f"[TEST] Synced {len(targets)} targets. Fetching posts + sending Slack review DMs...")

    sent = 0
    for t in targets:
        name = t["name"] or "Unknown"
        urn = t["linkedin_urn"]
        if not urn:
            print(f"[WARN] Missing linkedin_urn for {name} ({t['profile_url']}); skipping.")
            continue

        human_sleep(2, 4)
        posts = list_recent_posts(dsn, account_id, unipile_key, urn)
        if not posts:
            print(f"[INFO] No posts in last 24h for {name}.")
            continue

        p = posts[0]
        social_id = p.get("social_id")
        post_text = (p.get("text") or "").strip()

        if not social_id:
            print(f"[WARN] No social_id for {name}'s recent post; skipping.")
            continue

        # Generate comment
        if use_claude:
            if not anthropic_key:
                raise RuntimeError("ANTHROPIC_API_KEY not set but TEST_USE_CLAUDE=1")
            comment = generate_comment(anthropic_key, name, post_text)
        else:
            comment = f"TEST comment for {name} @ {datetime.now(timezone.utc).isoformat()}"

        # Store pending review row (so Approve can find it)
        c.execute("""
            INSERT OR REPLACE INTO pending_reviews
            (social_id, profile_name, post_text, generated_comment, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (social_id, name, post_text, comment, "pending", datetime.now(timezone.utc).isoformat()))
        conn.commit()

        # Send Slack DM
        send_for_review(
            token=slack_token,
            user_id=slack_user_id,
            social_id=social_id,
            author=name,
            post_text=post_text,
            comment=comment
        )

        print(f"[OK] Sent Slack review for {name} (social_id={social_id})")
        sent += 1

        # Keep it gentle
        human_sleep(3, 6)

        # For a first test, you can stop after 2
        if sent >= int(os.environ.get("TEST_MAX_SLACK_MESSAGES", "3")):
            break

    conn.close()
    print(f"[DONE] Sent {sent} Slack review messages.")

if __name__ == "__main__":
    main()