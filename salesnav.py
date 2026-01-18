import requests, time, random
from db import get_db

def human_sleep(a,b):
    time.sleep(random.uniform(a,b))

def normalize_dsn(dsn: str) -> str:
    dsn = dsn.strip().rstrip("/")
    if not dsn.startswith("http://") and not dsn.startswith("https://"):
        dsn = "https://" + dsn
    return dsn

def sync_salesnav_list(dsn, account_id, api_key, salesnav_url, max_people=200):
    dsn = normalize_dsn(dsn)
    url = f"{dsn}/api/v1/linkedin/search"

    headers = {
        "X-API-KEY": api_key,
        "accept": "application/json",
        "content-type": "application/json",
    }

    # ✅ account_id goes in query params (required by your Unipile schema)
    params = {"account_id": account_id}

    # ✅ URL goes in JSON body
    payload = {"url": salesnav_url}

    r = requests.post(url, headers=headers, params=params, json=payload, timeout=60)
    if r.status_code >= 400:
        print("STATUS:", r.status_code)
        print("BODY:", r.text[:2000])
    r.raise_for_status()

    data = r.json()

    # Normalize list key
    people = None
    if isinstance(data, dict):
        for k in ["items", "data", "results"]:
            if isinstance(data.get(k), list):
                people = data[k]
                break
    if people is None:
        people = []

    people = people[:max_people]


    with get_db() as (conn, c):

        inserted = 0
        for i, p in enumerate(people):
            profile = p.get("profile_url") or p.get("url") or p.get("profileUrl")
            urn = p.get("urn") or p.get("profile_urn") or p.get("profileUrn") or p.get("id")
            name = p.get("name") or p.get("full_name") or p.get("fullName")

            if not profile:
                continue

            c.execute("""
                INSERT INTO targets(profile_url, linkedin_urn, name)
                VALUES (%s, %s, %s)
                ON CONFLICT (profile_url) DO NOTHING
                """, (profile, urn, name))


            inserted += 1

            if i % 10 == 0:
                human_sleep(3, 6)

        conn.commit()

    print(f"[SYNC] Inserted {inserted} targets from Sales Nav search")
