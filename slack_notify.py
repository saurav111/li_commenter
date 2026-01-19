import requests
import json


def send_for_review(
    token: str,
    user_id: str,
    social_id: str,
    author: str,
    post_text: str,
    comment: str,
):
    """
    Sends a Slack DM to `user_id` with Approve / Edit / Skip buttons.
    Returns (channel_id, message_ts) for later chat.update/chat.delete.
    """
    url = "https://slack.com/api/chat.postMessage"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }

    post_preview = (post_text or "").strip()
    comment_preview = (comment or "").strip()

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Review LinkedIn comment"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Post by:* {author}\n\n*Post text:*\n{post_preview[:1500]}",
            },
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Proposed comment:*\n```{comment_preview}```"},
        },
        {
            "type": "actions",
            "block_id": f"review_{social_id[:60]}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "style": "primary",
                    "value": social_id,
                    "action_id": "approve_comment",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Edit"},
                    "value": social_id,
                    "action_id": "edit_comment",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Skip"},
                    "style": "danger",
                    "value": social_id,
                    "action_id": "skip_comment",
                },
            ],
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"`social_id`: {social_id}"}],
        },
    ]

    payload = {
        "channel": user_id,  # DM to yourself (your Slack user ID)
        "text": "Review LinkedIn comment",
        "blocks": blocks,
    }

    r = requests.post(url, headers=headers, data=json.dumps(payload), timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack chat.postMessage failed: {data}")

    # Slack returns the channel + ts of the posted message
    channel_id = data.get("channel")
    message_ts = (data.get("message") or {}).get("ts")

    return channel_id, message_ts