from fastapi import FastAPI, Request
import json, os
from datetime import datetime, timezone

from db import get_db
from unipile import comment_on_post
from slack_modal import open_edit_modal

app = FastAPI()

@app.post("/slack/actions")
async def slack_actions(req: Request):
    form = await req.form()
    payload = json.loads(form["payload"])

    conn = get_db()
    c = conn.cursor()

    # 1) Modal submit
    if payload.get("type") == "view_submission" and payload["view"]["callback_id"] == "edit_comment_submit":
        social_id = payload["view"]["private_metadata"]
        edited_comment = payload["view"]["state"]["values"]["comment_block"]["comment_input"]["value"]

        c.execute("SELECT generated_comment FROM pending_reviews WHERE social_id=?", (social_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            return {"response_action": "clear"}

        # Post edited comment
        comment_on_post(
            os.environ["UNIPILE_DSN"],
            os.environ["UNIPILE_ACCOUNT_ID"],
            os.environ["UNIPILE_API_KEY"],
            social_id,
            edited_comment
        )

        # Persist final comment
        c.execute("""
            INSERT OR REPLACE INTO comments(social_id, comment_text, commented_at)
            VALUES (?, ?, ?)
        """, (social_id, edited_comment, datetime.now(timezone.utc).isoformat()))

        c.execute("DELETE FROM pending_reviews WHERE social_id=?", (social_id,))
        conn.commit()
        conn.close()

        return {"response_action": "clear"}

    # 2) Button clicks
    action = payload["actions"][0]
    action_id = action["action_id"]
    social_id = action["value"]
    trigger_id = payload.get("trigger_id")

    c.execute("SELECT generated_comment FROM pending_reviews WHERE social_id=?", (social_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return {"ok": True}

    if action_id == "approve_comment":
        comment_on_post(
            os.environ["UNIPILE_DSN"],
            os.environ["UNIPILE_ACCOUNT_ID"],
            os.environ["UNIPILE_API_KEY"],
            social_id,
            row["generated_comment"]
        )

        c.execute("""
            INSERT OR REPLACE INTO comments(social_id, comment_text, commented_at)
            VALUES (?, ?, ?)
        """, (social_id, row["generated_comment"], datetime.now(timezone.utc).isoformat()))

        c.execute("DELETE FROM pending_reviews WHERE social_id=?", (social_id,))
        conn.commit()
        conn.close()
        return {"ok": True}

    if action_id == "skip_comment":
        c.execute("DELETE FROM pending_reviews WHERE social_id=?", (social_id,))
        conn.commit()
        conn.close()
        return {"ok": True}

    if action_id == "edit_comment":
        # open modal with pre-filled comment
        open_edit_modal(
            slack_token=os.environ["SLACK_BOT_TOKEN"],
            trigger_id=trigger_id,
            social_id=social_id,
            original_comment=row["generated_comment"]
        )
        conn.close()
        return {"ok": True}

    conn.close()
    return {"ok": True}
