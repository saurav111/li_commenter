import requests
import json

def open_edit_modal(
    slack_token: str,
    trigger_id: str,
    social_id: str,
    original_comment: str
) -> None:
    url = "https://slack.com/api/views.open"
    headers = {
        "Authorization": f"Bearer {slack_token}",
        "Content-Type": "application/json; charset=utf-8",
    }

    payload = {
        "trigger_id": trigger_id,
        "view": {
            "type": "modal",
            "callback_id": "edit_comment_submit",
            "private_metadata": social_id,  # we’ll use this to look up pending_reviews
            "title": {"type": "plain_text", "text": "Edit LinkedIn Comment"},
            "submit": {"type": "plain_text", "text": "Post"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": [
                {
                    "type": "input",
                    "block_id": "comment_block",
                    "label": {"type": "plain_text", "text": "Comment"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "comment_input",
                        "multiline": True,
                        "initial_value": original_comment or "",
                    },
                }
            ],
        },
    }

    r = requests.post(url, headers=headers, data=json.dumps(payload), timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack views.open failed: {data}")
