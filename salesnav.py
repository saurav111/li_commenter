import time
import random
import requests

from unipile import (
    normalize_dsn,
    _items_from_unipile_response,
    extract_salesnav_lead_id,
    resolve_salesnav_lead_to_profile_id,
)

def _sleep(a=0.8, b=1.8):
    time.sleep(random.uniform(a, b))

def _extract_next_cursor(data: dict):
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
    resolve_identifiers: bool = True,
):
    """
    Pulls *all* people from a Sales Nav lead list URL and upserts into `targets`.

    IMPORTANT:
    - We store a SalesNav lead id (ACw...) as `salesnav_lead_id` (new column recommended).
    - We resolve that ACw... to provider internal id (usually ACo...) and store into `person_identifier`.
      This is the identifier that tends to work for GET /api/v1/users/{id}/posts.
    """
    from db import get_db  # local import to avoid cycles

    dsn = normalize_dsn(dsn)
    url = f"{dsn}/api/v1/linkedin/search"
    headers = {
        "X-API-KEY": api_key,
        "accept": "application/json",
        "content-type": "application/json",
    }
    params = {"account_id": account_id}

    upserted = 0
    cursor = None
    seen_profile_urls = set()

    while upserted < max_people:
        payload = {"url": salesnav_url, "limit": page_limit}
        if cursor:
            payload["cursor"] = cursor

        _sleep(0.8, 1.8)
        r = requests.post(url, headers=headers, params=params, json=payload, timeout=60)
        if debug:
            print("[salesnav] status:", r.status_code)
        r.raise_for_status()
        data = r.json() if r.text else {}

        items = _items_from_unipile_response(data)

        if debug:
            keys = list(data.keys()) if isinstance(data, dict) else [type(data)]
            print(f"[salesnav] keys={keys}")
            print(f"[salesnav] got items={len(items)} cursor={cursor!r}")
            if isinstance(data, dict) and "paging" in data:
                print("[salesnav] paging:", data.get("paging"))

        if not items:
            break

        with get_db() as (conn, cur):
            for it in items:
                if upserted >= max_people:
                    break
                if not isinstance(it, dict):
                    continue

                # Unipile sometimes returns these with different keys
                profile_url = it.get("profile_url") or it.get("profileUrl") or it.get("url")
                name = (it.get("name") or it.get("full_name") or it.get("fullName") or "").strip() or None
                public_identifier = it.get("public_identifier") or it.get("publicIdentifier") or None

                if not profile_url or profile_url in seen_profile_urls:
                    continue
                seen_profile_urls.add(profile_url)

                # Extract Sales Nav lead id (ACw...) from the item or URL
                salesnav_lead_id = extract_salesnav_lead_id(it) or extract_salesnav_lead_id(profile_url)

                # Resolve to provider id (often ACo...) — this is what posts endpoint tends to accept.
                person_identifier = None
                if resolve_identifiers and salesnav_lead_id:
                    try:
                        _sleep(0.6, 1.4)
                        person_identifier = resolve_salesnav_lead_to_profile_id(
                            dsn=dsn,
                            api_key=api_key,
                            account_id=account_id,
                            salesnav_lead_id=salesnav_lead_id,
                            debug=debug,
                        )
                    except Exception as e:
                        if debug:
                            print("[salesnav] resolve failed:", salesnav_lead_id, repr(e))

                # Upsert target.
                # NOTE: this assumes you add salesnav_lead_id column (recommended).
                cur.execute(
                    """
                    INSERT INTO targets(profile_url, linkedin_urn, salesnav_lead_id, person_identifier, name, public_identifier)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (profile_url) DO UPDATE SET
                        linkedin_urn=COALESCE(EXCLUDED.linkedin_urn, targets.linkedin_urn),
                        salesnav_lead_id=COALESCE(EXCLUDED.salesnav_lead_id, targets.salesnav_lead_id),
                        person_identifier=COALESCE(EXCLUDED.person_identifier, targets.person_identifier),
                        name=COALESCE(EXCLUDED.name, targets.name),
                        public_identifier=COALESCE(EXCLUDED.public_identifier, targets.public_identifier)
                    """,
                    (
                        profile_url,
                        str(it.get("urn") or it.get("linkedin_urn") or it.get("id") or "") or None,
                        salesnav_lead_id,
                        person_identifier,
                        name,
                        public_identifier,
                    ),
                )

                upserted += 1

            conn.commit()

        cursor = _extract_next_cursor(data) if isinstance(data, dict) else None
        if not cursor:
            break

    return upserted