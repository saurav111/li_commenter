import re
import time
import random
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests


def normalize_dsn(dsn: str) -> str:
    dsn = (dsn or "").strip().rstrip("/")
    if not dsn.startswith("http://") and not dsn.startswith("https://"):
        dsn = "https://" + dsn
    return dsn


def _items_from_unipile_response(data):
    """
    Unipile sometimes returns:
      - {"items":[...]}
      - {"data":[...]}
      - or a raw list
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("items", "data", "results"):
            v = data.get(k)
            if isinstance(v, list):
                return v
    return []


def _sleep(min_s=0.8, max_s=2.2):
    time.sleep(random.uniform(min_s, max_s))


def extract_salesnav_lead_id(obj) -> str | None:
    """
    Try to extract the Sales Navigator lead id (often starts with ACw...) from:
      - a dict result object from /linkedin/search
      - or a string (url/urn)
    """
    if obj is None:
        return None

    if isinstance(obj, dict):
        # Try common keys first
        for k in ("salesnav_id", "lead_id", "leadId", "id", "urn", "profile_urn", "profileUrn"):
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                cand = extract_salesnav_lead_id(v.strip())
                if cand:
                    return cand

        # Try URL fields
        for k in ("profile_url", "profileUrl", "url", "lead_url", "leadUrl"):
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                cand = extract_salesnav_lead_id(v.strip())
                if cand:
                    return cand

        return None

    s = str(obj).strip()

    # Sales Nav lead URL: /sales/lead/<ID>,
    m = re.search(r"/sales/lead/([^,/?#]+)", s)
    if m:
        return m.group(1)

    # Sometimes it's directly an id like ACwAAB...
    if re.match(r"^[A-Za-z0-9_-]{10,}$", s) and s.startswith("ACw"):
        return s

    # Could be urn-ish; just attempt to capture ACw token
    m2 = re.search(r"(ACw[A-Za-z0-9_-]{6,})", s)
    if m2:
        return m2.group(1)

    return None


def resolve_salesnav_lead_to_profile_id(dsn: str, api_key: str, account_id: str, salesnav_lead_id: str, debug: bool = False) -> str | None:
    """
    Converts Sales Navigator lead id (ACw...) to classic LinkedIn profile identifier (ACo.../ADo...),
    using:
      GET /api/v1/users/{identifier}?account_id=...&linkedin_api=sales_navigator
    """
    dsn = normalize_dsn(dsn)
    url = f"{dsn}/api/v1/users/{quote(str(salesnav_lead_id), safe='')}"
    headers = {"X-API-KEY": api_key, "accept": "application/json"}
    params = {
        "account_id": account_id,
        "linkedin_api": "sales_navigator",
        "notify": "false",
    }

    r = requests.get(url, headers=headers, params=params, timeout=60)
    if debug and r.status_code >= 400:
        print("[RESOLVE] status:", r.status_code, "body:", r.text[:1500])
    r.raise_for_status()
    data = r.json()

    # Unipile shapes vary; these are common:
    # - data["provider_internal_id"] (often ACo...)
    # - data["provider_id"]
    # - data["id"]
    for k in ("provider_internal_id", "provider_id", "id", "identifier"):
        v = data.get(k) if isinstance(data, dict) else None
        if isinstance(v, str) and v.strip():
            return v.strip()

    return None


def _parse_unipile_datetime(post: dict) -> datetime | None:
    """
    Best field: parsed_datetime (ISO string)
    fallback: date like "1d", "2w"
    """
    dt_str = post.get("parsed_datetime")
    if isinstance(dt_str, str) and dt_str.strip():
        try:
            return datetime.fromisoformat(dt_str.replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            pass

    rel = post.get("date")
    if isinstance(rel, str):
        rel = rel.strip().lower()
        m = re.match(r"^(\d+)\s*(d|day|days|w|wk|week|weeks|mo|mon|month|months|y|yr|year|years)$", rel)
        if m:
            n = int(m.group(1))
            unit = m.group(2)
            now = datetime.now(timezone.utc)
            if unit.startswith("d") or unit in ("day", "days"):
                return now - timedelta(days=n)
            if unit.startswith("w") or unit in ("wk", "week", "weeks"):
                return now - timedelta(weeks=n)
            if unit.startswith("mo") or unit in ("mon", "month", "months"):
                return now - timedelta(days=30 * n)
            if unit.startswith("y") or unit in ("yr", "year", "years"):
                return now - timedelta(days=365 * n)

    return None

from urllib.parse import quote

def list_recent_posts(dsn, account_id, api_key, user_identifier, lookback_days=30, limit=20, debug=False):
    

def list_recent_posts(
    dsn: str,
    account_id: str,
    api_key: str,
    profile_id: str,
    lookback_days: int = 30,
    limit: int = 20,
    debug: bool = False,
):
    """
    Fetch posts for classic LinkedIn profile identifier (ACo.../ADo...).
    Endpoint:
      GET /api/v1/users/{identifier}/posts?account_id=...
    """
    dsn = normalize_dsn(dsn)

    safe_id = quote(str(user_identifier), safe="")  # encode everything
    url = f"{dsn}/api/v1/users/{safe_id}/posts"

    headers = {"X-API-KEY": api_key, "accept": "application/json"}
    params = {"account_id": account_id, "limit": limit}

    if debug:
        print("[DEBUG] posts url:", url)
        print("[DEBUG] identifier:", user_identifier)

    r = requests.get(url, headers=headers, params=params, timeout=60)
    if debug and r.status_code >= 400:
        print("[DEBUG] status:", r.status_code, "body:", r.text[:1500])
    r.raise_for_status()

    data = r.json()

    items = _items_from_unipile_response(data)

    cutoff = datetime.now(timezone.utc) - timedelta(days=int(lookback_days))
    eligible = []

    for p in items:
        if not isinstance(p, dict):
            continue
        dt = _parse_unipile_datetime(p)
        if dt is None:
            # If we cannot parse, keep it only in debug; otherwise skip.
            if debug:
                eligible.append(p)
            continue
        if dt >= cutoff:
            eligible.append(p)

    if debug:
        print(f"[POSTS] profile_id={profile_id} total={len(items)} eligible={len(eligible)} lookback_days={lookback_days}")

    return eligible


def comment_on_post(
    dsn: str,
    account_id: str,
    api_key: str,
    social_id: str,
    text: str,
    comment_id: str | None = None,
    mentions: list | None = None,
    debug: bool = False,
):
    """
    Comment on a post (activity URN is common):
      POST /api/v1/posts/{social_id}/comments
    Unipile expects account_id IN JSON body for this endpoint.
    """
    dsn = normalize_dsn(dsn)
    safe_social_id = quote(str(social_id), safe=":")  # keep urn colons
    url = f"{dsn}/api/v1/posts/{safe_social_id}/comments"
    headers = {
        "X-API-KEY": api_key,
        "accept": "application/json",
        "content-type": "application/json",
    }

    payload = {"account_id": account_id, "text": text}
    if comment_id:
        payload["comment_id"] = comment_id
    if mentions:
        payload["mentions"] = mentions

    _sleep(0.8, 2.0)
    r = requests.post(url, headers=headers, json=payload, timeout=60)
    if debug and r.status_code >= 400:
        print("[COMMENT] status:", r.status_code, "body:", r.text[:1500])
    r.raise_for_status()
    return r.json() if r.text else None