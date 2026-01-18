import requests, time, random
from db import get_db

def human_sleep(a, b):
    time.sleep(random.uniform(a, b))

def pick_public_profile_url(p: dict) -> str | None:
    # Best case (documented by Unipile)
    u = p.get("public_profile_url")
    if isinstance(u, str) and u.strip():
        return u.strip()

    # Sometimes comes in camelCase
    u = p.get("publicProfileUrl")
    if isinstance(u, str) and u.strip():
        return u.strip()

    # Fallback: build from public_identifier
    pid = p.get("public_identifier") or p.get("publicIdentifier")
    if isinstance(pid, str) and pid.strip():
        return f"https://www.linkedin.com/in/{pid.strip().strip('/')}/"

    return None

def pick_person_identifier(p: dict) -> str | None:
    # Unipile docs show member_urn/public_identifier for people results
    mu = p.get("member_urn") or p.get("memberUrn")
    if isinstance(mu, str) and mu.strip():
        return mu.strip()  # e.g. urn:li:member:76351639

    pid = p.get("public_identifier") or p.get("publicIdentifier")
    if isinstance(pid, str) and pid.strip():
        return pid.strip()  # e.g. luciano-bana-b876a021

    return None


def pick_profile_url(p):
    # Try known fields first
    for k in ["public_profile_url", "publicProfileUrl", "profile_url", "profileUrl", "url"]:
        v = p.get(k)
        if isinstance(v, str) and v.strip():
            if "/sales/lead/" not in v:
                return v.strip()

    # Try to synthesize from public identifier / vanity
    for k in ["public_identifier", "publicIdentifier", "vanityName", "vanity_name"]:
        v = p.get(k)
        if isinstance(v, str) and v.strip():
            return f"https://www.linkedin.com/in/{v.strip().strip('/')}/"

    return None

def normalize_dsn(dsn: str) -> str:
    dsn = dsn.strip().rstrip("/")
    if not dsn.startswith("http://") and not dsn.startswith("https://"):
        dsn = "https://" + dsn
    return dsn

def resolve_person_identifier(dsn, account_id, api_key, profile_url):
    """
    Resolve a LinkedIn profile URL into an identifier usable for:
      GET /api/v1/users/{identifier}/posts
    We try multiple likely keys returned by Unipile.
    """
    dsn = normalize_dsn(dsn)
    url = f"{dsn}/api/v1/linkedin/search"

    headers = {
        "X-API-KEY": api_key,
        "accept": "application/json",
        "content-type": "application/json",
    }
    params = {"account_id": account_id}
    payload = {"url": profile_url}

    r = requests.post(url, headers=headers, params=params, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()

    items = None
    if isinstance(data, dict):
        for k in ["items", "data", "results"]:
            if isinstance(data.get(k), list):
                items = data[k]
                break
    if items is None:
        items = []

    if not items:
        return None

    p = items[0]
    # Try common identifier fields
    for key in ["person_urn", "profile_urn", "urn", "identifier", "id", "provider_internal_id"]:
        v = p.get(key)
        if v:
            return str(v)

    return None

def sync_salesnav_list(dsn, account_id, api_key, salesnav_url, max_people=200):
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
    if r.status_code >= 400:
        print("STATUS:", r.status_code)
        print("BODY:", r.text[:2000])
    r.raise_for_status()

    data = r.json()

    people = None
    if isinstance(data, dict):
        for k in ["items", "data", "results"]:
            if isinstance(data.get(k), list):
                people = data[k]
                break
    if people is None:
        people = []

    people = people[:max_people]

    inserted = 0
    resolved = 0

    with get_db() as (conn, c):
        for i, p in enumerate(people):
            profile_url = pick_public_profile_url(p)
            person_identifier = pick_person_identifier(p)

            # keep salesnav lead url ONLY as a fallback reference (don’t use it for resolving posts)
            salesnav_url = p.get("profile_url") or p.get("profileUrl") or p.get("url")

            name = p.get("name") or p.get("full_name") or p.get("fullName") or "name"
        

            if not profile_url:
                print("[DEBUG] missing public profile fields keys:", list(p.keys()))
                continue

            # person_identifier = None
            # try:
            #     person_identifier = resolve_person_identifier(dsn, account_id, api_key, profile)
            #     if person_identifier:
            #         resolved += 1
            # except Exception as e:
            #     print("[WARN] resolve_person_identifier failed:", profile, repr(e))

            c.execute("""
                INSERT INTO targets(profile_url, linkedin_urn, person_identifier, name)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (profile_url) DO UPDATE
                SET linkedin_urn = EXCLUDED.linkedin_urn,
                    person_identifier = COALESCE(EXCLUDED.person_identifier, targets.person_identifier),
                    name = COALESCE(EXCLUDED.name, targets.name)
            """, (profile_url, salesnav_url, person_identifier, name))

            inserted += 1

            if i % 5 == 0:
                human_sleep(2, 5)

        conn.commit()

    print(f"[SYNC] Inserted {inserted} targets from Sales Nav search (resolved identifiers for {resolved})")
    return inserted