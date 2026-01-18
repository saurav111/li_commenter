import os
import time
import random
import requests

from db import get_db
from unipile import normalize_dsn, _items_from_unipile_response, extract_salesnav_lead_id, resolve_salesnav_lead_to_profile_id


def _sleep(min_s=1.2, max_s=3.0):
    time.sleep(random.uniform(min_s, max_s))


def sync_salesnav_list(
    dsn: str,
    account_id: str,
    api_key: str,
    salesnav_url: str,
    max_people: int = 200,
    debug: bool = False,
):
    """
    1) Uses Unipile to parse the Sales Nav search URL and return people
    2) Extracts Sales Nav lead id (ACw...) for each person
    3) Resolves Sales Nav lead id -> classic profile id (ACo.../ADo...)
    4) Upserts into Postgres 'targets' table

    targets schema expected:
      targets(profile_url TEXT PRIMARY KEY,
              linkedin_urn TEXT,           # we store salesnav_lead_id here (ACw...)
              person_identifier TEXT,      # resolved ACo.../ADo...
              name TEXT)
    """
    dsn = normalize_dsn(dsn)
    url = f"{dsn}/api/v1/linkedin/search"
    headers = {
        "X-API-KEY": api_key,
        "accept": "application/json",
        "content-type": "application/json",
    }
    params = {"account_id": account_id}
    payload = {"url": salesnav_url}

    r = requests.post(url, headers=headers, params=params, json=payload, timeout=60)
    if debug and r.status_code >= 400:
        print("[SALESNAV] status:", r.status_code, "body:", r.text[:2000])
    r.raise_for_status()
    data = r.json()

    people = _items_from_unipile_response(data)[: int(max_people)]

    inserted = 0
    resolved = 0

    with get_db() as (conn, c):
        for idx, p in enumerate(people):
            if not isinstance(p, dict):
                continue

            # Name fields vary
            name = (p.get("name") or p.get("full_name") or p.get("fullName") or "name").strip()

            # Store whatever URL we got for reference (can be sales nav lead url)
            profile_url = (p.get("profile_url") or p.get("profileUrl") or p.get("url") or "").strip()
            if not profile_url:
                # if no URL, skip; we can still work via id but URL is helpful for debugging
                profile_url = f"salesnav://{idx}"

            salesnav_lead_id = extract_salesnav_lead_id(p)
            if not salesnav_lead_id:
                if debug:
                    print("[SALESNAV] Could not extract lead id for:", name, "keys=", list(p.keys()))
                continue

            # Resolve to classic id (ACo/ADo) – cache in DB so daily job is fast
            person_identifier = None
            try:
                person_identifier = resolve_salesnav_lead_to_profile_id(
                    dsn=dsn,
                    api_key=api_key,
                    account_id=account_id,
                    salesnav_lead_id=salesnav_lead_id,
                    debug=debug,
                )
            except Exception as e:
                print(f"[WARN] resolve_salesnav_lead_to_profile_id failed for {name} ({salesnav_lead_id}): {repr(e)}")

            if person_identifier:
                resolved += 1

            c.execute(
                """
                INSERT INTO targets(profile_url, linkedin_urn, person_identifier, name)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (profile_url) DO UPDATE
                SET linkedin_urn = EXCLUDED.linkedin_urn,
                    person_identifier = COALESCE(EXCLUDED.person_identifier, targets.person_identifier),
                    name = COALESCE(EXCLUDED.name, targets.name)
                """,
                (profile_url, salesnav_lead_id, person_identifier, name),
            )
            inserted += 1

            # Gentle pacing (avoid bursts)
            if idx % 3 == 0:
                _sleep(1.5, 3.5)

        conn.commit()

    print(f"[SYNC] Inserted {inserted} targets from Sales Nav search (resolved identifiers for {resolved})")
    return inserted