import requests

def normalize_dsn(dsn: str) -> str:
    dsn = dsn.strip().rstrip("/")
    if not dsn.startswith("http://") and not dsn.startswith("https://"):
        dsn = "https://" + dsn
    return dsn

def resolve_profile_url_to_identifier(dsn, account_id, api_key, profile_url):
    """
    Uses Unipile's linkedin/search with a profile URL to get the right user identifier.
    Many Unipile deployments accept any LinkedIn URL as a search input.
    """
    dsn = normalize_dsn(dsn)
    url = f"{dsn}/api/v1/linkedin/search"
    headers = {"X-API-KEY": api_key, "accept": "application/json", "content-type": "application/json"}

    # account_id is REQUIRED as query param on your DSN
    params = {"account_id": account_id}

    # search URL is the profile itself
    payload = {"url": profile_url}

    r = requests.post(url, headers=headers, params=params, json=payload, timeout=60)
    r.raise_for_status()

    data = r.json()

    # try common list keys
    items = None
    for k in ["items", "data", "results"]:
        if isinstance(data, dict) and isinstance(data.get(k), list):
            items = data[k]
            break
    if items is None:
        items = []

    if not items:
        return None

    # pick the first match and extract a usable identifier
    p = items[0]

    # try common identifier fields
    for key in ["identifier", "id", "urn", "profile_urn", "profileUrn", "provider_internal_id"]:
        v = p.get(key)
        if v:
            return str(v)

    return None
