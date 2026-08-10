import re
import time
from typing import Optional
from urllib.parse import urlparse
import httpx
from logging_config import get_logger
from crawler import clean_search_text, strip_stop_words, title_variants

log = get_logger("ebay_api")

_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
_SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
_SCOPE = "https://api.ebay.com/oauth/api_scope"

FORMAT_KEYWORDS = {
    "Vinyl":    [r"\bvinyl\b", r"\blp\b", r"\brecord\b"],
    "CD":       [r"\bcd\b"],
    "Cassette": [r"\bcassette\b", r"\btape\b"],
    "DVD":      [r"\bdvd\b"],
    "Blu-ray":  [r"\bblu.?ray\b"],
}

# eBay US Music leaf-category IDs, used to constrain the Browse API search to
# the release's format so the price sort operates within-format rather than
# across all formats. Verified against ebay.com/b category URLs.
FORMAT_CATEGORY_IDS = {
    "Vinyl": "176985",
    "CD":    "176984",
}

# Module-level token cache, shared by every crawler that calls search_ebay()
_token = None  # type: ignore[assignment]
_token_expires_at: float = 0.0


async def get_token(app_id: str, cert_id: str) -> str:
    global _token, _token_expires_at
    if _token and time.time() < _token_expires_at - 60:
        return _token
    async with httpx.AsyncClient() as client:
        r = await client.post(
            _TOKEN_URL,
            auth=(app_id, cert_id),
            data={"grant_type": "client_credentials", "scope": _SCOPE},
        )
        r.raise_for_status()
        data = r.json()
    _token = data["access_token"]
    _token_expires_at = time.time() + int(data.get("expires_in", 7200))
    return _token


def _words(text: str) -> set:
    return set(text.lower().split())


def _is_ebay_item_url(url: str) -> bool:
    # A prefix test on the raw string accepts https://www.ebay.com.example.test/,
    # so compare the parsed host instead. The URL comes from eBay's own API but
    # is rendered as a link the user clicks.
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname == "www.ebay.com"


def pick_matching_item(items: list, release: dict) -> Optional[dict]:
    artist_words = _words(clean_search_text(release.get("artist", "")))
    title_words = _words(clean_search_text(release.get("title", "")))
    fmt_patterns = FORMAT_KEYWORDS.get(release.get("format", ""))

    for item in items:
        listing_title = item.get("title", "").lower()
        listing_words = set(listing_title.split())

        if artist_words:
            if len(artist_words & listing_words) / len(artist_words) < 0.5:
                continue
        if title_words:
            if len(title_words & listing_words) / len(title_words) < 0.5:
                continue

        if fmt_patterns:
            if not any(re.search(p, listing_title) for p in fmt_patterns):
                continue

        return item
    return None


async def search_ebay(
    release: dict,
    app_id: str,
    cert_id: str,
    seller: Optional[str],
    limit: int,
    log_prefix: str,
    fallback_url: str,
) -> list[dict]:
    if not app_id or not cert_id:
        log.warning("[%s] ebay_app_id or ebay_cert_id not configured", log_prefix)
        return []

    barcode = release.get("barcode") or ""
    if barcode:
        query = barcode
        log.info("[%s] Searching by barcode: %s", log_prefix, barcode)
    else:
        raw_artist = clean_search_text(release.get("artist", ""))
        artist = strip_stop_words(raw_artist) if raw_artist.lower() != "various" else ""
        raw_title = clean_search_text(release.get("title", ""))
        title = title_variants(raw_title)[-1]
        query = f"{artist} {title}".strip()
        log.info("[%s] searching by artist/title: %s", log_prefix, query)

    try:
        token = await get_token(app_id, cert_id)
    except httpx.HTTPError as e:
        log.error("[%s] token fetch failed: %s", log_prefix, e)
        raise

    filter_clauses = ["buyingOptions:{FIXED_PRICE}"]
    if seller:
        filter_clauses.insert(0, f"sellers:{{{seller}}}")
    params = {
        "q": query,
        "filter": ",".join(filter_clauses),
        "sort": "price+shippingCost",
        "limit": str(limit),
    }
    category_id = FORMAT_CATEGORY_IDS.get(release.get("format", ""))
    if category_id:
        params["category_ids"] = category_id

    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                _SEARCH_URL,
                headers={"Authorization": f"Bearer {token}"},
                params=params,
            )
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPStatusError as e:
        # Raised, not swallowed into []: an empty list means "eBay has no
        # matching listing", and the crawl manager reads it that way -- on the
        # stock-item path an empty result is deliberately excluded from the
        # consecutive-failure circuit breaker. Returning [] here therefore hid
        # every eBay API error (409s in particular) from the breaker, so the
        # site never cooled off no matter how many errors came back in a row.
        # Every status is raised, not just 409: eBay doesn't document a
        # per-status meaning for Browse search failures, and any of them is
        # equally "the API isn't answering", which is what the breaker counts.
        log.error("[%s] search HTTP error %s: %s", log_prefix, e.response.status_code, e)
        # The status line alone can't distinguish a daily quota from a
        # suspended keyset from a transient fault -- eBay says which in the
        # body's errors[] array (errorId + longMessage), and documents no
        # per-status meaning for Browse search failures at all. Debug, and
        # truncated: an error page from something other than eBay (a proxy's
        # HTML, say) would otherwise dump tens of KB into a rotating log file
        # the viewer has to render. Same reason shopify_catalog logs 429
        # headers at debug.
        #
        # Whitespace is collapsed so the record stays one line: routers/logs.py's
        # _line_visible passes through any line it can't read a level from, so a
        # body's second and subsequent lines would appear in *every* level view,
        # DEBUG filter or not -- the same path that puts tracebacks in the INFO
        # stream as OTHER.
        body = " ".join(e.response.text.split())[:2000]
        log.debug(
            "[%s] search HTTP %s response body: %s",
            log_prefix, e.response.status_code, body,
        )
        raise
    except httpx.RequestError as e:
        log.error("[%s] search request error: %s", log_prefix, e)
        raise

    items = data.get("itemSummaries")
    if not items:
        log.debug("[%s] No results for: %s", log_prefix, query)
        return []

    item = pick_matching_item(items, release)
    if item is None:
        log.debug("[%s] no validated match for: %s", log_prefix, query)
        return []

    price_val = item.get("price", {})
    shipping_options = item.get("shippingOptions", [])
    shipping = None
    if shipping_options:
        raw = shipping_options[0].get("shippingCost", {}).get("value")
        if raw is not None:
            try:
                shipping = float(raw)
            except (ValueError, TypeError):
                pass

    try:
        price = float(price_val.get("value", 0))
    except (ValueError, TypeError):
        price = None

    item_url = item.get("itemWebUrl", "")
    if not _is_ebay_item_url(item_url):
        legacy_id = item.get("legacyItemId")
        item_url = f"https://www.ebay.com/itm/{legacy_id}" if legacy_id else fallback_url

    log.debug("[%s] matched item for: %s → %s %s", log_prefix, query, price_val.get("currency"), price)

    return [{
        "url": item_url,
        "price": price,
        "shipping": shipping,
        "currency": price_val.get("currency"),
        "condition": item.get("condition"),
    }]
