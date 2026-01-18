import os
import time
import random
from datetime import datetime, timezone

from db import init_db, get_db
from salesnav import sync_salesnav_list
from unipile import list_recent_posts, resolve_salesnav_lead_to_profile_id
from claude import generate_comment
from slack_notify import send_for_review


def _sleep(min_s=1.0, max_s=3.0):
    time.sleep(random.uniform(min_s, max_s))


def main():
    # ---- required env ----
    dsn = os.environ["UNIPILE_DSN"]
    account_id = os.environ["UNIPILE_ACCOUNT_ID"]
    api_key = os.environ["UNIPILE_API_KEY"]
    salesnav_url = os.environ["SALESNAV_URL"]

    slack_token = os.environ["SLACK_BOT_TOKEN"]
    slack_user_id = os.environ["SLACK_USER_ID"]

    anthropic_key = os.environ["ANTHROPIC_API_KEY"]

    # ---- optional env ----
    lookback_days = int(os.getenv("POST_LOOKBACK_DAYS", "30"))
    max_people = int(os.getenv("MAX_PEOPLE", "20"))
    max_per_day = int(os.getenv("MAX_COMMENTS_PER_DAY", "20"))
    debug = os.getenv("DEBUG", "false").lower() == "true"
    dry_run = os.getenv("DRY_RUN", "0") == "1"

    # Ensure tables exist (safe even if migrate ran)
    init_db()

    # 1) Sync + resolve identifiers
    sync_salesnav_list(dsn, account_id, api_key, salesnav_url, max_people=max_people, debug=debug)

    sent = 0

    with get_db() as (conn, c):
        c.execute("SELECT profile_url, linkedin_urn, person_identifier, name FROM targets")
        targets = c.fetchall()

        for t in targets:
            if sent >= max_per_day:
                break

            name = (t.get("name") or "name").strip()
            salesnav_lead_id = t.get("linkedin_urn")  # we store ACw... here
            person_identifier = t.get("person_identifier")

            # Fallback: resolve if missing
            if not person_identifier and salesnav_lead_id:
                try:
                    person_identifier = resolve_salesnav_lead_to_profile_id(
                        dsn=dsn,
                        api_key=api_key,
                        account_id=account_id,
                        salesnav_lead_id=salesnav_lead_id,
                        debug=debug,
                    )
                    if person_identifier:
                        c.execute(
                            "UPDATE targets SET person_identifier=%s WHERE profile_url=%s",
                            (person_identifier, t["profile_url"]),
                        )
                        conn.commit()
                except Exception as e:
                    print(f"[WARN] Could not resolve profile id for {name}: {repr(e)}")
                    continue

            if not person_identifier:
                print(f"[INFO] Missing person_identifier for {name}")
                continue

            # 2) Fetch posts
            posts = list_recent_posts(
                dsn=dsn,
                account_id=account_id,
                api_key=api_key,
                profile_id=person_identifier,
                lookback_days=lookback_days,
                limit=20,
                debug=debug,
            )

            if not posts:
                print(f"[INFO] No posts in last {lookback_days}d for {name}")
                continue

            # 3) Choose newest eligible post and queue review
            # (posts returned are typically newest-first; we just iterate)
            for post in posts:
                if sent >= max_per_day:
                    break

                social_id = post.get("id") or post.get("social_id") or post.get("urn")
                post_text = post.get("text") or post.get("content") or ""

                if not social_id:
                    continue

                # Skip if already commented
                c.execute("SELECT 1 FROM comments WHERE social_id=%s", (social_id,))
                if c.fetchone():
                    continue

                # Skip if already pending review
                c.execute("SELECT 1 FROM pending_reviews WHERE social_id=%s", (social_id,))
                if c.fetchone():
                    continue

                comment = generate_comment(anthropic_key, name, post_text)

                # Store pending review
                c.execute(
                    """
                    INSERT INTO pending_reviews
                      (social_id, profile_name, post_text, generated_comment, status, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (social_id) DO NOTHING
                    """,
                    (
                        social_id,
                        name,
                        post_text,
                        comment,
                        "pending",
                        datetime.now(timezone.utc),
                    ),
                )
                conn.commit()

                # Send to Slack for approval
                send_for_review(
                    token=slack_token,
                    user_id=slack_user_id,
                    social_id=social_id,
                    author=name,
                    post_text=post_text,
                    comment=comment,
                )

                sent += 1
                print(f"[OK] Sent Slack review {sent}/{max_per_day} for {name}")

                # In dry-run mode, stop here (no posting). Actual posting happens on Slack approve.
                if dry_run and debug:
                    print("[DRY_RUN] queued review only (no auto-post).")

                # Spread out to avoid LinkedIn/unipile throttling
                _sleep(10, 25)
                break  # one post per person per run

    print(f"[DONE] Sent {sent} Slack review messages")


if __name__ == "__main__":
    main()