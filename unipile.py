import requests, time, random
from datetime import datetime, timedelta, timezone
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote


def human_sleep(a,b):
    time.sleep(random.uniform(a,b))

_REL_RE = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$", re.IGNORECASE)

def parse_created_at(value):
    """
    Handles:
    - ISO strings: 2026-01-03T12:34:56Z
    - Relative strings: '1d', '3h', '15m', '2w'
    Returns an aware datetime in UTC or None.
    """
    if value is None:
        return None

    now = datetime.now(timezone.utc)

    # Relative like "1d"
    if isinstance(value, str):
        m = _REL_RE.match(value)
        if m:
            n = int(m.group(1))
            unit = m.group(2).lower()
            delta = {
                "s": timedelta(seconds=n),
                "m": timedelta(minutes=n),
                "h": timedelta(hours=n),
                "d": timedelta(days=n),
                "w": timedelta(weeks=n),
            }.get(unit)
            return now - delta if delta else None

        # ISO-like
        s = value.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dtv = datetime.fromisoformat(s)
            if dtv.tzinfo is None:
                dtv = dtv.replace(tzinfo=timezone.utc)
            return dtv.astimezone(timezone.utc)
        except ValueError:
            return None

    # Epoch seconds/ms
    if isinstance(value, (int, float)):
        v = float(value)
        if v > 1e12:  # ms
            v /= 1000.0
        return datetime.fromtimestamp(v, tz=timezone.utc)

    return None


import requests
from datetime import datetime, timedelta, timezone
import os, json

def list_recent_posts(dsn, account_id, api_key, user_identifier, lookback_days=30, limit=20, debug=False):
    url = f"{dsn}/api/v1/users/{user_identifier}/posts"
    headers = {"X-API-KEY": api_key, "accept": "application/json"}
    params = {"account_id": account_id, "limit": limit}

    r = requests.get(url, headers=headers, params=params, timeout=60)
    if debug:
        print("[POSTS] url:", r.url)
        print("[POSTS] status:", r.status_code)
        print("[POSTS] body:", r.text[:1500])

    if r.status_code != 200:
        return []

    data = r.json()
    items = None
    if isinstance(data, dict):
        for k in ["items", "data", "results"]:
            if isinstance(data.get(k), list):
                items = data[k]
                break
    if items is None and isinstance(data, list):
        items = data
    if items is None:
        items = []

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    eligible = []
    for p in items:
        created = p.get("created_at") or p.get("createdAt") or p.get("date")
        ts = parse_created_at(created)
        if not ts:
            continue
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        if ts >= cutoff:
            eligible.append(p)

    if debug and not eligible:
        print(f"[POSTS] parsed items={len(items)} eligible={len(eligible)} cutoff_days={lookback_days}")

    return eligible

def normalize_dsn(dsn: str) -> str:
    dsn = (dsn or "").strip().rstrip("/")
    if not dsn.startswith("http://") and not dsn.startswith("https://"):
        dsn = "https://" + dsn
    return dsn


def comment_on_post(dsn, account_id, api_key, social_id, text, comment_id=None, mentions=None):
    """
    Unipile expects account_id in JSON body for this endpoint.
    Docs example:
      POST /api/v1/posts/{social_id}/comments
      body: { "account_id": "...", "text": "Hey" }
    """

    # optional: encode social_id safely (keeps urn:li:activity:... working)
    safe_social_id = quote(str(social_id), safe=":")  # keep colons
    url = f"{dsn}/api/v1/posts/{safe_social_id}/comments"

    headers = {
        "X-API-KEY": api_key,
        "accept": "application/json",
        "content-type": "application/json",
    }

    payload = {
        "account_id": account_id,
        "text": text,
    }

    # optional reply/mentions per docs
    if comment_id:
        payload["comment_id"] = comment_id
    if mentions:
        payload["mentions"] = mentions

    r = requests.post(url, headers=headers, json=payload, timeout=60)
    if r.status_code >= 400:
        print("[UNIPILE] status:", r.status_code, "body:", r.text[:1200])
    r.raise_for_status()
    return r.json() if r.text else None
