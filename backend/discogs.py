import httpx
from logging_config import get_logger

log = get_logger("discogs")
DISCOGS_API = "https://api.discogs.com"


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Discogs token={token}",
        "User-Agent": "DiscogsCollectionBrowser/1.0 +https://github.com/local/discogs-browser",
    }


def get_identity(token: str) -> dict:
    r = httpx.get(f"{DISCOGS_API}/oauth/identity", headers=_headers(token))
    r.raise_for_status()
    return r.json()


def fetch_collection_fields(token: str, username: str) -> dict:
    """Return a mapping of field_id -> field_name for the user's custom collection fields."""
    r = httpx.get(
        f"{DISCOGS_API}/users/{username}/collection/fields",
        headers=_headers(token),
    )
    r.raise_for_status()
    fields = r.json().get("fields", [])
    return {f["id"]: f["name"] for f in fields}


def fetch_collection(token: str, username: str) -> list[dict]:
    items = []
    page = 1
    while True:
        log.info("Fetching collection page %d for %s", page, username)
        r = httpx.get(
            f"{DISCOGS_API}/users/{username}/collection/folders/0/releases",
            headers=_headers(token),
            params={"page": page, "per_page": 100},
        )
        r.raise_for_status()
        data = r.json()
        items.extend(data["releases"])
        total_pages = data["pagination"]["pages"]
        log.info("Page %d/%d — %d releases so far", page, total_pages, len(items))
        if page >= total_pages:
            break
        page += 1
    log.info("Collection fetch complete: %d releases", len(items))
    return items


def parse_release(item: dict, price_field_id=None) -> dict:
    info = item["basic_information"]
    artist = info["artists"][0]["name"] if info.get("artists") else "Unknown"
    label = info["labels"][0]["name"] if info.get("labels") else ""
    fmt = info["formats"][0]["name"] if info.get("formats") else ""
    release_id = info["id"]
    discogs_price = None
    if price_field_id is not None:
        for note in item.get("notes", []):
            if note.get("field_id") == price_field_id:
                discogs_price = note.get("value") or None
                break
    return {
        "discogs_id": f"r{release_id}",
        "artist": artist,
        "title": info.get("title", ""),
        "year": info.get("year"),
        "label": label,
        "format": fmt,
        "cover_image_url": info.get("cover_image", ""),
        "discogs_url": f"https://www.discogs.com/release/{release_id}",
        "discogs_price": discogs_price,
    }
