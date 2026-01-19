import os
import time
import random
from datetime import datetime, timezone, timedelta

import requests

from db import get_db
from salesnav import sync_salesnav_list
from unipile import list_recent_posts
from claude import generate_comment
from slack_notify import send_for_review


def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def utc_now_iso() -> str:
    return utc_now().isoformat()

def jitter_sleep(min_s: float, max_s: float) -> None:
    time.sleep(random.uniform(min_s, max_s))

def _get_post_text(p: dict) -> str:
    return (p.get("text") or p.get("content") or p.get("caption") or "").strip()

def _get_social_id(p: dict) -> str | None:
    return p.get("social_id") or p.get("socialId") or p.get("urn") or p.get("entity_urn")

def _parse_post_created_at(p: dict) -> str | None:
    # store as timestamptz if Unipile gives an ISO date; otherwise None
    # (don’t crash the whole run because some posts have "1d" style fields)
    for k in ("created_at", "createdAt", "created_time", "createdTime"):
        v = p.get(k)
        if not v:
            continue
        if isinstance(v, str) and ("T" in v):
            return v.replace("Z", "+00:00")
    return None

def refresh_post_pool_for_all_targets(
    dsn: str,
    account_id: str,
    api_key: str,
    lookback_days: int,
    limit_posts: int,
    debug: bool,
):
    """
    For each target, fetch posts and upsert into post_pool.
    This lets you random-sample from the entire Sales Nav list later.
    """
    with get_db() as (conn, cur):
        cur.execute("SELECT profile_url, person_identifier, name FROM targets")
        targets = cur.fetchall()

    upserted = 0
    for t in targets:
        person_identifier = t.get("person_identifier")
        profile_url = t.get("profile_url")
        name = (t.get("name") or "name").strip() or "name"

        if not person_identifier:
            # if your pipeline has identifier resolution elsewhere, keep skipping here
            if debug:
                print(f"[pool] missing person_identifier for {name} ({profile_url})")
            continue

        try:
            posts = list_recent_posts(
                dsn=dsn,
                account_id=account_id,
                api_key=api_key,
                user_identifier=person_identifier,
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
            continue

        with get_db() as (conn, cur):
            for p in posts:
                social_id = _get_social_id(p)
                post_text = _get_post_text(p)
                if not social_id or not post_text:
                    continue

                created_at = _parse_post_created_at(p)

                cur.execute(
                    """
                    INSERT INTO post_pool(social_id, person_identifier, profile_url, profile_name, post_text, post_created_at, last_seen_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (social_id) DO UPDATE SET
                        person_identifier=EXCLUDED.person_identifier,
                        profile_url=EXCLUDED.profile_url,
                        profile_name=EXCLUDED.profile_name,
                        post_text=EXCLUDED.post_text,
                        post_created_at=COALESCE(EXCLUDED.post_created_at, post_pool.post_created_at),
                        last_seen_at=EXCLUDED.last_seen_at
                    """,
                    (social_id, person_identifier, profile_url, name, post_text, created_at, utc_now()),
                )
                upserted += 1
            conn.commit()

        # pacing between profiles (important)
        jitter_sleep(0.8, 2.0)

    return upserted

def pick_random_eligible_posts(limit: int) -> list[dict]:
    """
    Pick random posts that are not already:
      - commented
      - pending review
      - handled (skipped/posted)
    Prefer 1 per person (distinct person_identifier) so it spreads across the list.
    """
    with get_db() as (conn, cur):
        cur.execute(
            """
            WITH eligible AS (
                SELECT p.*
                FROM post_pool p
                WHERE NOT EXISTS (SELECT 1 FROM comments c WHERE c.social_id = p.social_id)
                  AND NOT EXISTS (SELECT 1 FROM pending_reviews pr WHERE pr.social_id = p.social_id)
                  AND NOT EXISTS (SELECT 1 FROM handled_posts h WHERE h.social_id = p.social_id)
            ),
            one_per_person AS (
                SELECT DISTINCT ON (person_identifier)
                    social_id, person_identifier, profile_url, profile_name, post_text, post_created_at
                FROM eligible
                ORDER BY person_identifier, post_created_at DESC NULLS LAST, last_seen_at DESC
            )
            SELECT *
            FROM one_per_person
            ORDER BY RANDOM()
            LIMIT %s
            """,
            (limit,),
        )
        return cur.fetchall()

def main():
    dsn = os.environ["UNIPILE_DSN"]
    account_id = os.environ["UNIPILE_ACCOUNT_ID"]
    api_key = os.environ["UNIPILE_API_KEY"]
    salesnav_url = os.environ["SALESNAV_URL"]

    slack_token = os.environ["SLACK_BOT_TOKEN"]
    slack_user_id = os.environ["SLACK_USER_ID"]

    anthropic_key = os.environ["ANTHROPIC_API_KEY"]

    lookback_days = int(os.getenv("POST_LOOKBACK_DAYS", "30"))
    max_people = int(os.getenv("MAX_PEOPLE", "500"))          # make sure we can pull all 125
    max_per_day = int(os.getenv("MAX_COMMENTS_PER_DAY", "20"))
    limit_posts = int(os.getenv("POSTS_LIMIT", "10"))         # per person
    debug = os.getenv("DEBUG", "false").lower() in ("1", "true", "yes")

    # 1) Sync ALL targets (Sales Nav)
    inserted = sync_salesnav_list(
        dsn=dsn,
        account_id=account_id,
        api_key=api_key,
        salesnav_url=salesnav_url,
        max_people=max_people,
        page_limit=50,
        debug=debug,
    )
    print(f"[SYNC] Upserted {inserted} targets from Sales Nav search")

    # 2) Refresh post_pool across ALL targets
    upserted_posts = refresh_post_pool_for_all_targets(
        dsn=dsn,
        account_id=account_id,
        api_key=api_key,
        lookback_days=lookback_days,
        limit_posts=limit_posts,
        debug=debug,
    )
    print(f"[POOL] Upserted {upserted_posts} posts into post_pool")

    # 3) Pick random eligible posts (spread across people)
    picks = pick_random_eligible_posts(limit=max_per_day)
    print(f"[PICK] Selected {len(picks)} random posts for review")

    sent = 0
    with get_db() as (conn, cur):
        for row in picks:
            social_id = row["social_id"]
            name = row.get("profile_name") or "name"
            post_text = row.get("post_text") or ""

            # generate comment
            try:
                comment = generate_comment(anthropic_key, name, post_text)
            except Exception as e:
                print(f"[WARN] comment generation failed for {name} ({social_id}): {repr(e)}")
                continue

            # insert pending first
            cur.execute(
                """
                INSERT INTO pending_reviews
                  (social_id, profile_name, post_text, generated_comment, status, created_at, slack_channel, slack_ts)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (social_id) DO NOTHING
                """,
                (social_id, name, post_text, comment, "pending", utc_now(), None, None),
            )
            conn.commit()

            # send Slack + store ts/channel for UX updates later
            try:
                channel_id, message_ts = send_for_review(
                    token=slack_token,
                    user_id=slack_user_id,
                    social_id=social_id,
                    author=name,
                    post_text=post_text,
                    comment=comment,
                )
            except Exception as e:
                print(f"[WARN] Slack send failed ({social_id}): {repr(e)}")
                continue

            cur.execute(
                "UPDATE pending_reviews SET slack_channel=%s, slack_ts=%s WHERE social_id=%s",
                (channel_id, message_ts, social_id),
            )
            conn.commit()

            sent += 1
            print(f"[OK] Sent Slack review {sent}/{max_per_day} for {name} ({social_id})")

            jitter_sleep(4, 10)

    print(f"[DONE] Sent {sent} Slack review messages.")

if __name__ == "__main__":
    main()