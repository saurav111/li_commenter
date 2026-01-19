import os
import time
import random
from datetime import datetime, timezone

import requests  # needed for requests.HTTPError

from db import get_db
from salesnav import sync_salesnav_list
from unipile import list_recent_posts
from claude import generate_comment
from slack_notify import send_for_review


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def jitter_sleep(min_s: float, max_s: float) -> None:
    time.sleep(random.uniform(min_s, max_s))


def main():
    # --------- Env ----------
    dsn = os.environ["UNIPILE_DSN"]
    account_id = os.environ["UNIPILE_ACCOUNT_ID"]
    unipile_key = os.environ["UNIPILE_API_KEY"]

    salesnav_url = os.environ["SALESNAV_URL"]

    slack_token = os.environ["SLACK_BOT_TOKEN"]
    slack_user_id = os.environ["SLACK_USER_ID"]

    anthropic_key = os.environ["ANTHROPIC_API_KEY"]

    # knobs
    lookback_days = int(os.getenv("POST_LOOKBACK_DAYS", "30"))
    max_people = int(os.getenv("MAX_PEOPLE", "20"))
    max_per_day = int(os.getenv("MAX_COMMENTS_PER_DAY", "20"))
    limit_posts = int(os.getenv("POSTS_LIMIT", "20"))
    debug = os.getenv("DEBUG", "false").lower() in ("1", "true", "yes")

    # --------- Step 1: Sync targets ----------
    inserted = sync_salesnav_list(
        dsn=dsn,
        account_id=account_id,
        api_key=unipile_key,
        salesnav_url=salesnav_url,
        max_people=max_people,
    )
    print(f"[SYNC] Inserted {inserted} targets from Sales Nav search")

    sent = 0

    # --------- Step 2: Read targets + fetch posts ----------
    with get_db() as (conn, cur):
        # Expect targets table to contain person_identifier (best) + profile_url + name
        cur.execute("""
            SELECT profile_url, person_identifier, name
            FROM targets
            ORDER BY name NULLS LAST
        """)
        targets = cur.fetchall()

        for t in targets:
            if sent >= max_per_day:
                break

            profile_url = t.get("profile_url")
            person_identifier = t.get("person_identifier")
            name = t.get("name") or "name"

            if not person_identifier:
                print(f"[WARN] Missing person_identifier for {name} ({profile_url}). Skipping.")
                continue

            # --------- Fetch posts (robust error handling) ----------
            try:
                posts = list_recent_posts(
                    dsn=dsn,
                    account_id=account_id,
                    api_key=unipile_key,
                    user_identifier=person_identifier,  # IMPORTANT: this must match your unipile.py signature
                    lookback_days=lookback_days,
                    limit=limit_posts,
                    debug=debug,
                )
            except requests.HTTPError as e:
                status = getattr(e.response, "status_code", None)
                body = getattr(e.response, "text", "") if e.response is not None else ""
                print(f"[WARN] posts fetch failed for {name} id={person_identifier} status={status} body={body[:400]}")
                continue
            except Exception as e:
                print(f"[WARN] posts fetch crashed for {name} id={person_identifier}: {repr(e)}")
                continue

            if not posts:
                print(f"[INFO] No posts in last {lookback_days}d for {name}.")
                continue

            # --------- Pick the first eligible post we haven't commented on ----------
            for p in posts:
                if sent >= max_per_day:
                    break

                post = p
                social_id = post.get("social_id") or post.get("socialId") or post.get("urn") or post.get("entity_urn")
                post_text = (p.get("text") or p.get("content") or "").strip()

                if not social_id:
                    continue

                # skip if already commented
                cur.execute("SELECT 1 FROM comments WHERE social_id=%s", (social_id,))
                if cur.fetchone():
                    continue

                # Generate comment
                try:
                    comment = generate_comment(anthropic_key, name, post_text)
                except Exception as e:
                    print(f"[WARN] comment generation failed for {name}: {repr(e)}")
                    continue

                # Insert pending review (Slack approval flow)
                cur.execute("""
                    INSERT INTO pending_reviews
                      (social_id, profile_name, post_text, generated_comment, status, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (social_id) DO NOTHING
                """, (social_id, name, post_text, comment, "pending", utc_now_iso()))
                conn.commit()

                # Send Slack DM
                try:
                    send_for_review(
                        token=slack_token,
                        user_id=slack_user_id,
                        social_id=social_id,
                        author=name,
                        post_text=post_text,
                        comment=comment,
                    )
                except Exception as e:
                    print(f"[WARN] Slack send failed for {name} ({social_id}): {repr(e)}")
                    # keep pending_review row; you can retry later
                    continue

                sent += 1
                print(f"[OK] Sent Slack review {sent}/{max_per_day} for {name} ({social_id})")

                # Human-ish pacing
                jitter_sleep(10, 25)

                # only do one post per person per run (optional)
                break

            # extra spacing between people
            jitter_sleep(2, 6)

    print(f"[DONE] Sent {sent} Slack review messages.")


if __name__ == "__main__":
    main()