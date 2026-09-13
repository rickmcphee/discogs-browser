from time import sleep

from authlib.integrations.httpx_client import OAuth1Client
from logging_config import get_logger
import config
from oauth_discogs import _require_consumer_credentials

# authlib >=1.8 builds its clients on httpx2 when importable, httpx otherwise;
# raise_for_status() therefore raises that module's HTTPStatusError. Mirror the
# selection and re-export the type so callers can catch it without caring which
# transport authlib picked.
try:
    import httpx2 as _transport_httpx
except ImportError:
    import httpx as _transport_httpx

HTTPStatusError = _transport_httpx.HTTPStatusError

log = get_logger("discogs")
DISCOGS_API = "https://api.discogs.com"
_USER_AGENT = "DiscogsCollectionBrowser/1.0 +https://github.com/local/discogs-browser"

# Every caller of _client() runs this module's blocking httpx calls inside
# crawl_manager._sync_collection, an async def scheduled on the main event
# loop with no run_in_threadpool wrapper -- a request that hangs with no
# timeout freezes that loop (and therefore the worker pool and every other
# user's requests) indefinitely rather than failing after a bounded wait.
_TIMEOUT = 30.0

# Tests inject an in-memory transport here (see conftest.py's bridge fixture):
# authlib >=1.8 transports over httpx2, which respx cannot patch, so mocking
# has to enter through the client itself rather than the httpx module.
_transport = None

# Discogs allows 60 authenticated requests per rolling 60-second window, and a
# collection sync runs just under it -- the per-release barcode fetch paces at
# 1.1s all by itself, so a page fetch landing between two of them is enough to
# cross the line, as is any other client holding the same user's token. A 429
# on a page fetch used to end the whole sync and abandon every page after it,
# so the request is waited out and retried instead. See
# docs/specifications/shaping/2026-09-13-discogs-api-429-retry-design.md.
_MAX_RETRIES = 3
# One full window. The longest honest wait for a 60-second rolling limit is 60
# seconds, so a Retry-After materially past that describes something other than
# the documented limit (a longer block, or a malformed value) and sleeping it
# would be indistinguishable from a hang. Clamping means we retry once per
# window instead, and spend the budget if the block really is longer.
_MAX_RETRY_WAIT = 60.0
# 5s, 20s, 60s -- cumulatively 85s, so the last retry is certainly outside the
# window that produced the 429. A backoff topping out below 60s would spend its
# whole budget inside that window and give up for arithmetic reasons.
_BACKOFF_BASE = 5.0
_BACKOFF_FACTOR = 4


def _retry_wait(response, attempt: int):
    """Seconds to wait before retry `attempt` (1-based) of a 429, and why.

    Retry-After wins when it parses as a non-negative number; an HTTP-date
    form, a negative value, or no header at all falls through to the backoff,
    which still waits -- just on our schedule rather than the server's.
    """
    raw = response.headers.get("Retry-After")
    if raw is not None:
        try:
            seconds = float(raw)
        except (TypeError, ValueError):
            seconds = -1.0
        if seconds >= 0:
            return min(seconds, _MAX_RETRY_WAIT), "Retry-After: %s" % raw
    return (
        min(_BACKOFF_BASE * _BACKOFF_FACTOR ** (attempt - 1), _MAX_RETRY_WAIT),
        "no usable Retry-After",
    )


def _get_with_retry(client, url, *, params=None):
    """GET through the Discogs rate-limit retry budget.

    Every request site in this module goes through here, so a 429 is handled
    in one place and a request site cannot be added that quietly skips it.

    Retrying changes *when* a caller sees a 429, never *what* it sees: once
    the budget is spent the original HTTPStatusError is raised, so
    fetch_release_barcode's caller still loses one barcode and a page walk
    still propagates to sync_error. A non-429 error status raises immediately,
    unretried and unchanged -- _sync_collection_blocking branches on the type
    around fetch_collection_fields.

    No jitter, unlike catalog_http.get_with_retry(): that de-synchronises
    crawlers converging on one shared platform edge, while this limit is per
    authenticated token, so two users syncing at once cannot collide.
    """
    attempt = 0
    while True:
        r = client.get(url, params=params)
        if r.status_code != 429:
            r.raise_for_status()
            return r
        if attempt >= _MAX_RETRIES:
            # The exception below is the same generic HTTPStatusError any
            # failure raises; this line is the only thing distinguishing "rate
            # limited, we waited, it never cleared" from a single 429.
            log.error(
                "Discogs rate limit did not clear after %d retries on %s — giving up",
                _MAX_RETRIES, url,
            )
            r.raise_for_status()
        attempt += 1
        wait, reason = _retry_wait(r, attempt)
        # WARNING, not DEBUG: routers/logs.py filters by exact level, so this
        # has to sit at a level someone asking why a sync crawled will have
        # selected. Not ERROR -- the next attempt is expected to succeed.
        log.warning(
            "Discogs rate limited (HTTP 429) on %s — retry %d/%d in %.1fs (%s)",
            url, attempt, _MAX_RETRIES, wait, reason,
        )
        sleep(wait)


def _client(oauth_token: str, oauth_token_secret: str) -> OAuth1Client:
    _require_consumer_credentials()
    return OAuth1Client(
        client_id=config.DISCOGS_CONSUMER_KEY,
        client_secret=config.DISCOGS_CONSUMER_SECRET,
        token=oauth_token,
        token_secret=oauth_token_secret,
        headers={"User-Agent": _USER_AGENT},
        timeout=_TIMEOUT,
        transport=_transport,
    )


def get_identity(oauth_token: str, oauth_token_secret: str) -> dict:
    with _client(oauth_token, oauth_token_secret) as client:
        r = _get_with_retry(client, f"{DISCOGS_API}/oauth/identity")
        return r.json()


def fetch_collection_fields(oauth_token: str, oauth_token_secret: str, username: str) -> dict:
    """Return a mapping of field_id -> field_name for the user's custom collection fields."""
    with _client(oauth_token, oauth_token_secret) as client:
        r = _get_with_retry(client, f"{DISCOGS_API}/users/{username}/collection/fields")
        fields = r.json().get("fields", [])
        return {f["id"]: f["name"] for f in fields}


def iter_collection_pages(oauth_token: str, oauth_token_secret: str, username: str):
    """Yield (page, total_pages, items) for each page of the user's collection."""
    with _client(oauth_token, oauth_token_secret) as client:
        page = 1
        while True:
            log.info("Fetching collection page %d for %s", page, username)
            r = _get_with_retry(
                client,
                f"{DISCOGS_API}/users/{username}/collection/folders/0/releases",
                params={"page": page, "per_page": 100},
            )
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
            r = _get_with_retry(
                client,
                f"{DISCOGS_API}/users/{username}/wants",
                params={"page": page, "per_page": 100},
            )
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
        r = _get_with_retry(client, f"{DISCOGS_API}/releases/{release_id}")
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
    price_paid = None
    if price_field_id is not None:
        for note in item.get("notes", []):
            if note.get("field_id") == price_field_id:
                price_paid = note.get("value") or None
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
        "price_paid": price_paid,
        "barcode": None,
    }
