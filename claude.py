from anthropic import Anthropic
from pathlib import Path
import os

PROMPT = Path("prompt.md").read_text()

def generate_comment(api_key, author, post_text):
    client = Anthropic(api_key=api_key)

    message = f"""
    AUTHOR: {author}

    POST TEXT:
    \"\"\"
    {post_text}
    \"\"\"

    ---
    {PROMPT}
    """

    resp = client.messages.create(
        model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5"),
        max_tokens=200,
        temperature=0.7,
        messages=[{"role": "user", "content": message}]
    )

    return resp.content[0].text.strip()
