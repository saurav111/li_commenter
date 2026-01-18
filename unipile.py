import requests, time, random
from datetime import datetime, timedelta, timezone
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote


def human_sleep(a,b):
    time.sleep(random.uniform(a,b))

_REL_RE = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$", re.IGNORECASE)

# unipile.py

def resolve_salesnav_lead_to_profile_id(base_url: str, api_key: str, account_id: str, salesnav_lead_id: str) -> str | None:
    """
    salesnav_lead_id: typically ACw...
    returns: classic provider internal id: ACo... / ADo... (what /users/{id}/posts expects)
    """
    url = f"{base_url}/api/v1/users/{salesnav_lead_id}"
    headers = {"X-API-KEY": api_key, "accept": "application/json"}
    params = {
        "account_id": account_id,
        "linkedin_api": "sales_navigator",  # key bit
        "notify": "false",
        # don't request linkedin_sections here; keep it light
    }

    r = requests.get(url, headers=headers, params=params, timeout=60)
    r.raise_for_status()
    data = r.json()

    # Unipile responses vary slightly; these are common keys.
    # You can print(data.keys()) once in debug to confirm.
    return data.get("provider_id") or data.get("id")


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

# unipile.py

from datetime import datetime, timedelta, timezone

def list_recent_posts(base_url: str, api_key: str, account_id: str, profile_id: str, since_days: int = 30, limit: int = 30):
    """
    profile_id must be ACo... / ADo...
    """
    url = f"{base_url}/api/v1/users/{profile_id}/posts"
    headers = {"X-API-KEY": api_key, "accept": "application/json"}
    params = {"account_id": account_id, "limit": min(max(limit, 1), 100)}

    r = requests.get(url, headers=headers, params=params, timeout=60)
    r.raise_for_status()
    data = r.json()

    items = data.get("items") or data.get("data") or data  # depending on Unipile envelope
    if not isinstance(items, list):
        items = []

    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)

    recent = []
    for p in items:
        # Best field to use (from Unipile docs/examples)
        dt_str = p.get("parsed_datetime")
        if dt_str:
            try:
                dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                if dt >= cutoff:
                    recent.append(p)
                continue
            except Exception:
                pass

        # Fallback: if only "date": "1d"/"3w" exists, don't crash—just include it and let caller decide
        # Or parse it if you want.
        recent.append(p)

    return recent

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
