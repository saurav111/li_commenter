import time
import random
import requests

from unipile import normalize_dsn

def _sleep(a=0.8, b=1.8):
    time.sleep(random.uniform(a, b))

def _extract_next_cursor(data: dict):
    # different endpoints use different shapes; handle a few common ones
    paging = data.get("paging") or {}
    for k in ("next_cursor", "cursor", "next", "nextCursor", "next_cursor_id"):
        if paging.get(k):
            return paging.get(k)
        if data.get(k):
            return data.get(k)
    return None

def sync_salesnav_list(
    dsn: str,
    account_id: str,
    api_key: str,
    salesnav_url: str,
    max_people: int = 500,
    page_limit: int = 50,
    debug: bool = False,
):
    """
    Pulls *all* people from a Sales Nav lead list URL and upserts into `targets`.
    Uses cursor-based pagination when provided by Unipile response.
    """
    from db import get_db  # local import to avoid cycles

    dsn = normalize_dsn(dsn)
    url = f"{dsn}/api/v1/linkedin/search"
    headers = {"X-API-KEY": api_key, "accept": "application/json", "content-type": "application/json"}
    params = {"account_id": account_id}

    inserted = 0
    cursor = None
    seen_profile_urls = set()

    while inserted < max_people:
        payload = {"url": salesnav_url, "limit": page_limit}
        if cursor:
            payload["cursor"] = cursor

        _sleep(0.8, 1.8)
        r = requests.post(url, headers=headers, params=params, json=payload, timeout=60)
        if debug:
            print("[salesnav] status:", r.status_code)
        r.raise_for_status()
        data = r.json() if r.text else {}

        items = data.get("items") or []
        if debug:
            print(f"[salesnav] got items={len(items)} cursor={cursor!r}")
            if "paging" in data:
                print("[salesnav] paging:", data.get("paging"))

        if not items:
            break

        with get_db() as (conn, cur):
            for it in items:
                profile_url = it.get("profile_url")
                name = it.get("name")
                urn = it.get("urn") or it.get("linkedin_urn") or it.get("id")
                # NOTE: person_identifier resolution happens later in your pipeline
                if not profile_url or profile_url in seen_profile_urls:
                    continue

                seen_profile_urls.add(profile_url)

                cur.execute(
                    """
                    INSERT INTO targets(profile_url, linkedin_urn, person_identifier, name, public_identifier)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (profile_url) DO UPDATE SET
                        linkedin_urn=EXCLUDED.linkedin_urn,
                        name=COALESCE(EXCLUDED.name, targets.name)
                    """,
                    (profile_url, str(urn) if urn else None, None, name, None),
                )
                inserted += 1
                if inserted >= max_people:
                    break
            conn.commit()

        cursor = _extract_next_cursor(data)
        if not cursor:
            break

    return inserted