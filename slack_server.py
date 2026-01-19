from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import json
import os
from datetime import datetime, timezone
import threading
import traceback
import requests

from db import get_db
from unipile import comment_on_post
from slack_modal import open_edit_modal

app = FastAPI()

SLACK_API = "https://slack.com/api"

import threading
import traceback

def _run_in_thread(fn, *args, **kwargs):
    t = threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True)
    t.start()

def _edit_submit_worker(social_id: str, edited_comment: str):
    try:
        with get_db() as (conn, cur):
            cur.execute(
                "SELECT slack_channel, slack_ts FROM pending_reviews WHERE social_id=%s",
                (social_id,),
            )
            row = cur.fetchone()
            slack_channel = row.get("slack_channel") if row else None
            slack_ts = row.get("slack_ts") if row else None

            if os.getenv("DRY_RUN") == "1":
                print(f"[DRY_RUN] Would comment on {social_id}: {edited_comment[:200]}")
            else:
                comment_on_post(
                    os.environ["UNIPILE_DSN"],
                    os.environ["UNIPILE_ACCOUNT_ID"],
                    os.environ["UNIPILE_API_KEY"],
                    social_id,
                    edited_comment,
                    debug=True,
                )

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
            cur.execute("DELETE FROM pending_reviews WHERE social_id=%s", (social_id,))
            conn.commit()

        # ✅ Update Slack message to remove buttons
        if slack_channel and slack_ts:
            slack_update_message(slack_channel, slack_ts, "✅ Posted (edited). (removed from queue)")
        else:
            print("[edit_submit] missing slack_channel/ts for", social_id)

    except Exception as e:
        print("[edit_submit] ERROR:", repr(e))
        print(traceback.format_exc())


def _utc_now():
    return datetime.now(timezone.utc)


def _ack_ok():
    return JSONResponse({"ok": True})


def slack_headers():
    return {
        "Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}",
        "Content-Type": "application/json; charset=utf-8",
    }


def slack_update_message(channel: str, ts: str, text: str):
    payload = {
        "channel": channel,
        "ts": ts,
        "text": text,
        "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
    }
    r = requests.post(
        f"{SLACK_API}/chat.update",
        headers=slack_headers(),
        data=json.dumps(payload),
        timeout=20,
    )
    if not r.ok:
        print("[slack] chat.update HTTP error:", r.status_code, r.text[:800])
        return
    data = r.json()
    if not data.get("ok"):
        print("[slack] chat.update failed:", data)


def _run_in_thread(fn, *args, **kwargs):
    t = threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True)
    t.start()


def _get_channel_and_ts(payload: dict):
    channel_id = (payload.get("channel") or {}).get("id")
    message_ts = (payload.get("message") or {}).get("ts")
    return channel_id, message_ts


def _approve_worker(payload: dict, social_id: str):
    """Runs after Slack ACK to avoid 3-second timeout."""
    channel_id, message_ts = _get_channel_and_ts(payload)

    try:
        with get_db() as (conn, cur):
            cur.execute(
                "SELECT generated_comment FROM pending_reviews WHERE social_id=%s",
                (social_id,),
            )
            row = cur.fetchone()
            if not row:
                print("[approve] No pending review found for", social_id)
                if channel_id and message_ts:
                    slack_update_message(channel_id, message_ts, f"⚠️ Already handled (no pending row).")
                return

            comment_text = row["generated_comment"] or ""
            print(f"[approve] social_id={social_id!r} dry_run={os.getenv('DRY_RUN')} preview={comment_text[:160]!r}")

            if os.getenv("DRY_RUN") == "1":
                print(f"[DRY_RUN] Would comment on {social_id}: {comment_text[:200]}")
            else:
                comment_on_post(
                    os.environ["UNIPILE_DSN"],
                    os.environ["UNIPILE_ACCOUNT_ID"],
                    os.environ["UNIPILE_API_KEY"],
                    social_id,
                    comment_text,
                    debug=True,
                )

            # record comment + remove pending
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
            cur.execute(
                "INSERT INTO handled_posts(social_id, status) VALUES (%s, %s) "
                "ON CONFLICT (social_id) DO UPDATE SET status=EXCLUDED.status, handled_at=NOW()",
                (social_id, "posted"),
            )
            cur.execute("DELETE FROM pending_reviews WHERE social_id=%s", (social_id,))
            conn.commit()

        # UX: remove buttons / mark done
        if channel_id and message_ts:
            slack_update_message(channel_id, message_ts, "✅ Posted. (removed from queue)")

    except Exception as e:
        print("[approve] ERROR:", repr(e))
        print(traceback.format_exc())
        if channel_id and message_ts:
            slack_update_message(channel_id, message_ts, f"❌ Failed to post (server error). Try again.")


def _skip_worker(payload: dict, social_id: str):
    """Skip should update DB + update Slack message."""
    channel_id, message_ts = _get_channel_and_ts(payload)

    try:
        with get_db() as (conn, cur):
            cur.execute(
                "INSERT INTO handled_posts(social_id, status) VALUES (%s, %s) "
                "ON CONFLICT (social_id) DO UPDATE SET status=EXCLUDED.status, handled_at=NOW()",
                (social_id, "skipped"),
            )
            # Instead of delete, you *can* keep a status. If your schema has no status, delete is fine.
            cur.execute("DELETE FROM pending_reviews WHERE social_id=%s", (social_id,))
            conn.commit()

        if channel_id and message_ts:
            slack_update_message(channel_id, message_ts, "⏭️ Skipped. (removed from queue)")

    except Exception as e:
        print("[skip] ERROR:", repr(e))
        print(traceback.format_exc())
        if channel_id and message_ts:
            slack_update_message(channel_id, message_ts, "❌ Failed to skip. Try again.")


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
        # -----------------------
        # 1) Modal submission (must return response_action)
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

            try:
                edited_comment = view["state"]["values"]["comment_block"]["comment_input"]["value"]
            except Exception:
                print("[slack/actions] Modal submit missing edited comment value")
                return {"response_action": "clear"}

            # ✅ ACK immediately so Slack never times out
            _run_in_thread(_edit_submit_worker, social_id, edited_comment)
            return {"response_action": "clear"}

        # -----------------------
        # 2) Button clicks (ACK fast)
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

        # Approve/Skip should be async to avoid Slack timeout
        if action_id == "approve_comment":
            _run_in_thread(_approve_worker, payload, social_id)
            return _ack_ok()

        if action_id == "skip_comment":
            _run_in_thread(_skip_worker, payload, social_id)
            return _ack_ok()

        if action_id == "edit_comment":
            # Open modal FAST (avoid DB before views.open).
            original_comment = ""

            # Try extracting the proposed comment from the Slack message blocks (fast, no DB).
            try:
                blocks = (payload.get("message") or {}).get("blocks") or []
                for b in blocks:
                    if b.get("type") == "section":
                        txt = ((b.get("text") or {}).get("text")) or ""
                        # our slack_notify uses "*Proposed comment:*```...```"
                        if "Proposed comment" in txt and "```" in txt:
                            original_comment = txt.split("```", 1)[1].rsplit("```", 1)[0].strip()
                            break
            except Exception:
                pass

            # Fallback to DB only if needed (still might be slow, but rare)
            if not original_comment:
                with get_db() as (conn, cur):
                    cur.execute(
                        "SELECT generated_comment FROM pending_reviews WHERE social_id=%s",
                        (social_id,),
                    )
                    row = cur.fetchone()
                original_comment = (row["generated_comment"] if row else "") or ""

            ok = open_edit_modal(
                slack_token=os.environ["SLACK_BOT_TOKEN"],
                trigger_id=trigger_id,
                social_id=social_id,
                original_comment=original_comment,
            )

            if not ok:
                print("[slack/actions] views.open failed for social_id:", social_id)
            return _ack_ok()

        return _ack_ok()

    except Exception as e:
        print("[slack/actions] ERROR:", repr(e))
        print(traceback.format_exc())
        return _ack_ok()