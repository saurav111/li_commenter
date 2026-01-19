from fastapi import FastAPI, Request
import json
import os
from datetime import datetime, timezone

from db import get_db
from unipile import comment_on_post
from slack_modal import open_edit_modal

app = FastAPI()

def _utc_now():
    return datetime.now(timezone.utc)

def _ack_ok():
    # Always return a 200 response to Slack
    return {"ok": True}

@app.post("/slack/actions")
async def slack_actions(req: Request):
    """
    Handles:
      - button clicks: approve_comment, edit_comment, skip_comment
      - modal submit: callback_id=edit_comment_submit
    """
    try:
        form = await req.form()
        if "payload" not in form:
            print("[slack/actions] Missing payload in form")
            return _ack_ok()

        payload = json.loads(form["payload"])
    except Exception as e:
        print("[slack/actions] Failed to parse payload:", repr(e))
        return _ack_ok()

    try:
        with get_db() as (conn, cur):

            # -----------------------
            # 1) Modal submission
            # -----------------------
            if payload.get("type") == "view_submission":
                view = payload.get("view") or {}
                callback_id = view.get("callback_id")

                if callback_id != "edit_comment_submit":
                    return {"response_action": "clear"}

                social_id = view.get("private_metadata")
                if not social_id:
                    print("[slack/actions] Modal submit missing private_metadata/social_id")
                    return {"response_action": "clear"}

                # Extract edited text
                try:
                    edited_comment = view["state"]["values"]["comment_block"]["comment_input"]["value"]
                except Exception:
                    print("[slack/actions] Modal submit missing edited comment value")
                    return {"response_action": "clear"}

                # Load pending row
                cur.execute(
                    "SELECT generated_comment FROM pending_reviews WHERE social_id=%s",
                    (social_id,),
                )
                row = cur.fetchone()
                if not row:
                    print("[slack/actions] No pending review found for", social_id)
                    return {"response_action": "clear"}

                # Post edited comment (or DRY_RUN)
                if os.getenv("DRY_RUN") == "1":
                    print(f"[DRY_RUN] Would comment on {social_id}: {edited_comment[:200]}")
                else:
                    comment_on_post(
                        os.environ["UNIPILE_DSN"],
                        os.environ["UNIPILE_ACCOUNT_ID"],
                        os.environ["UNIPILE_API_KEY"],
                        social_id,
                        edited_comment,
                    )

                # Upsert into comments
                cur.execute(
                    """
                    INSERT INTO comments(social_id, comment_text, commented_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (social_id) DO UPDATE
                    SET comment_text = EXCLUDED.comment_text,
                        commented_at = EXCLUDED.commented_at
                    """,
                    (social_id, edited_comment, _utc_now()),
                )

                # Remove from pending
                cur.execute("DELETE FROM pending_reviews WHERE social_id=%s", (social_id,))
                conn.commit()

                # Clear modal
                return {"response_action": "clear"}

            # -----------------------
            # 2) Button clicks
            # -----------------------
            if payload.get("type") != "block_actions":
                return _ack_ok()

            actions = payload.get("actions") or []
            if not actions:
                return _ack_ok()

            action = actions[0]
            action_id = action.get("action_id")
            social_id = action.get("value")
            trigger_id = payload.get("trigger_id")

            if not action_id or not social_id:
                return _ack_ok()

            # Load pending review row
            cur.execute(
                "SELECT generated_comment FROM pending_reviews WHERE social_id=%s",
                (social_id,),
            )
            row = cur.fetchone()
            if not row:
                return _ack_ok()

            if action_id == "approve_comment":
                comment_text = row["generated_comment"]
                print(f"[slack/actions] approve social_id={social_id!r} dry_run={os.getenv('DRY_RUN')}")
                print(f"[slack/actions] approve comment_preview={comment_text[:200]!r}")


                if os.getenv("DRY_RUN") == "1":
                    print(f"[DRY_RUN] Would comment on {social_id}: {comment_text[:200]}")
                else:
                    comment_on_post(
                        os.environ["UNIPILE_DSN"],
                        os.environ["UNIPILE_ACCOUNT_ID"],
                        os.environ["UNIPILE_API_KEY"],
                        social_id,
                        comment_text,
                    )

                cur.execute(
                    """
                    INSERT INTO comments(social_id, comment_text, commented_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (social_id) DO UPDATE
                    SET comment_text = EXCLUDED.comment_text,
                        commented_at = EXCLUDED.commented_at
                    """,
                    (social_id, comment_text, _utc_now()),
                )

                cur.execute("DELETE FROM pending_reviews WHERE social_id=%s", (social_id,))
                conn.commit()
                return _ack_ok()

            if action_id == "skip_comment":
                cur.execute("DELETE FROM pending_reviews WHERE social_id=%s", (social_id,))
                conn.commit()
                return _ack_ok()

            if action_id == "edit_comment":
                # Open modal with prefilled comment

                ok = open_edit_modal(
                    slack_token=os.environ["SLACK_BOT_TOKEN"],
                    trigger_id=trigger_id,
                    social_id=social_id,
                    original_comment=row["generated_comment"],
                )

                print(f"[slack/actions] approve social_id={social_id!r} dry_run={os.getenv('DRY_RUN')}")
                print(f"[slack/actions] approve comment_preview={comment_text[:200]!r}")

                # Even if modal fails, ack Slack to avoid toast
                if not ok:
                    print("[slack/actions] views.open failed for social_id:", social_id)
                return _ack_ok()

            return _ack_ok()

    except Exception as e:
        # Always ACK Slack; log server-side for debugging
        print("[slack/actions] ERROR:", repr(e))
        return _ack_ok()