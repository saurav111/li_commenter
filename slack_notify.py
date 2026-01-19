import requests
import json

def send_for_review(
    token: str,
    user_id: str,
    social_id: str,
    author: str,
    post_text: str,
    comment: str
) -> tuple[str | None, str | None]:
    """
    Sends a Slack DM with Approve / Edit / Skip buttons.
    Returns (channel_id, message_ts) if successful.
    """
    url = "https://slack.com/api/chat.postMessage"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Post by:* {author}\n\n*Post text:*\n{(post_text or '').strip()[:1500]}"
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Proposed comment:*\n```{(comment or '').strip()}```"
            }
        },
        {
            "type": "actions",
            "block_id": f"review_{social_id}",
            "elements": [
                {"type": "button","text": {"type": "plain_text","text": "Approve"},"style": "primary","value": social_id,"action_id": "approve_comment"},
                {"type": "button","text": {"type": "plain_text","text": "Edit"},"value": social_id,"action_id": "edit_comment"},
                {"type": "button","text": {"type": "plain_text","text": "Skip"},"style": "danger","value": social_id,"action_id": "skip_comment"},
            ],
        },
    ]

    payload = {"channel": user_id, "text": "Review LinkedIn comment", "blocks": blocks}

    r = requests.post(url, headers=headers, data=json.dumps(payload), timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack chat.postMessage failed: {data}")

    return data.get("channel"), data.get("ts")