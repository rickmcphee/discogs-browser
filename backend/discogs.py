from authlib.integrations.httpx_client import OAuth1Client
from logging_config import get_logger
import config
from oauth_discogs import _require_consumer_credentials

log = get_logger("discogs")
DISCOGS_API = "https://api.discogs.com"
_USER_AGENT = "DiscogsCollectionBrowser/1.0 +https://github.com/local/discogs-browser"

# Every caller of _client() runs this module's blocking httpx calls inside
# crawl_manager._sync_collection_blocking, which runs in a worker thread via
# run_in_threadpool (see crawl_manager._sync_collection) rather than on the
# main event loop. A request that hangs with no timeout still ties up that
# worker thread indefinitely -- starving the shared threadpool other
# run_in_threadpool callers (e.g. auth_middleware._resolve_session) depend
# on -- and never resolves the future _sync_collection is awaiting, so the
# sync never completes for that user.
_TIMEOUT = 30.0


def _client(oauth_token: str, oauth_token_secret: str) -> OAuth1Client:
    _require_consumer_credentials()
    return OAuth1Client(
        client_id=config.DISCOGS_CONSUMER_KEY,
        client_secret=config.DISCOGS_CONSUMER_SECRET,
        token=oauth_token,
        token_secret=oauth_token_secret,
        headers={"User-Agent": _USER_AGENT},
        timeout=_TIMEOUT,
    )


def get_identity(oauth_token: str, oauth_token_secret: str) -> dict:
    with _client(oauth_token, oauth_token_secret) as client:
        r = client.get(f"{DISCOGS_API}/oauth/identity")
        r.raise_for_status()
        return r.json()


def fetch_collection_fields(oauth_token: str, oauth_token_secret: str, username: str) -> dict:
    """Return a mapping of field_id -> field_name for the user's custom collection fields."""
    with _client(oauth_token, oauth_token_secret) as client:
        r = client.get(f"{DISCOGS_API}/users/{username}/collection/fields")
        r.raise_for_status()
        fields = r.json().get("fields", [])
        return {f["id"]: f["name"] for f in fields}


def iter_collection_pages(oauth_token: str, oauth_token_secret: str, username: str):
    """Yield (page, total_pages, items) for each page of the user's collection."""
    with _client(oauth_token, oauth_token_secret) as client:
        page = 1
        while True:
            log.info("Fetching collection page %d for %s", page, username)
            r = client.get(
                f"{DISCOGS_API}/users/{username}/collection/folders/0/releases",
                params={"page": page, "per_page": 100},
            )
            r.raise_for_status()
            data = r.json()
            total_pages = data["pagination"]["pages"]
            items = data["releases"]
            log.info("Page %d/%d — %d releases on this page", page, total_pages, len(items))
            yield page, total_pages, items
            if page >= total_pages:
                break
            page += 1


def iter_wantlist_pages(oauth_token: str, oauth_token_secret: str, username: str):
    """Yield (page, total_pages, items) for each page of the user's wantlist."""
    with _client(oauth_token, oauth_token_secret) as client:
        page = 1
        while True:
            log.info("Fetching wantlist page %d for %s", page, username)
            r = client.get(
                f"{DISCOGS_API}/users/{username}/wants",
                params={"page": page, "per_page": 100},
            )
            r.raise_for_status()
            data = r.json()
            total_pages = data["pagination"]["pages"]
            items = data["wants"]
            log.info("Page %d/%d — %d wantlist items on this page", page, total_pages, len(items))
            yield page, total_pages, items
            if page >= total_pages:
                break
            page += 1


def fetch_release_barcode(oauth_token: str, oauth_token_secret: str, release_id: int) -> str:
    """Return the first Barcode identifier for a release as digits only, or empty string."""
    with _client(oauth_token, oauth_token_secret) as client:
        r = client.get(f"{DISCOGS_API}/releases/{release_id}")
        r.raise_for_status()
        identifiers = r.json().get("identifiers", [])
        for ident in identifiers:
            if ident.get("type") == "Barcode":
                raw = ident.get("value", "")
                return "".join(c for c in raw if c.isdigit())
        return ""


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
        "barcode": None,
    }
