import re
import time
import urllib.parse
import httpx
from logging_config import get_logger
from config import load_config
from crawler import clean_search_text

_FORMAT_KEYWORDS = {
    "Vinyl":    [r"\bvinyl\b", r"\blp\b", r"\brecord\b"],
    "CD":       [r"\bcd\b"],
    "Cassette": [r"\bcassette\b", r"\btape\b"],
    "DVD":      [r"\bdvd\b"],
    "Blu-ray":  [r"\bblu.?ray\b"],
}

log = get_logger("crawlers.ebay")

CCMUSIC_SELLER = "collectorschoicemusic"
_EBAY_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
_EBAY_SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
_EBAY_SCOPE = "https://api.ebay.com/oauth/api_scope"

# Module-level token cache
_token = None  # type: ignore[assignment]
_token_expires_at: float = 0.0


async def _get_token(app_id: str, cert_id: str) -> str:
    global _token, _token_expires_at
    if _token and time.time() < _token_expires_at - 60:
        return _token
    async with httpx.AsyncClient() as client:
        r = await client.post(
            _EBAY_TOKEN_URL,
            auth=(app_id, cert_id),
            data={"grant_type": "client_credentials", "scope": _EBAY_SCOPE},
        )
        r.raise_for_status()
        data = r.json()
    _token = data["access_token"]
    _token_expires_at = time.time() + int(data.get("expires_in", 7200))
    return _token


def _words(text: str) -> set:
    return set(text.lower().split())


def _pick_matching_item(items: list, release: dict):
    artist_words = _words(clean_search_text(release.get("artist", "")))
    title_words = _words(clean_search_text(release.get("title", "")))
    fmt_patterns = _FORMAT_KEYWORDS.get(release.get("format", ""))

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


class Crawler:
    site_name: str = "CC Music"
    base_url: str = f"https://www.ebay.com/str/{CCMUSIC_SELLER}"
    login_url: str = ""

    @classmethod
    def search_url(cls, release: dict) -> str:
        artist = clean_search_text(release.get("artist", ""))
        title = clean_search_text(release.get("title", ""))
        query = urllib.parse.quote_plus(f"{artist} {title}")
        return f"https://www.ebay.com/sch/{CCMUSIC_SELLER}/i.html?_nkw={query}"

    async def search(self, release: dict, page) -> list[dict]:
        cfg = load_config()
        app_id = cfg.get("ebay_app_id", "")
        cert_id = cfg.get("ebay_cert_id", "")
        if not app_id or not cert_id:
            log.warning("[CC Music/eBay] ebay_app_id or ebay_cert_id not configured")
            return []

        barcode = release.get("barcode") or ""
        if barcode:
            query = barcode
            log.info("[CC Music/eBay] searching by barcode: %s", barcode)
        else:
            artist = clean_search_text(release.get("artist", ""))
            title = clean_search_text(release.get("title", ""))
            query = f"{artist} {title}"
            log.info("[CC Music/eBay] searching by artist/title: %s", query)

        try:
            token = await _get_token(app_id, cert_id)
        except httpx.HTTPError as e:
            log.error("[CC Music/eBay] token fetch failed: %s", e)
            raise

        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    _EBAY_SEARCH_URL,
                    headers={"Authorization": f"Bearer {token}"},
                    params={
                        "q": query,
                        "filter": f"sellers:{{{CCMUSIC_SELLER}}},buyingOptions:{{FIXED_PRICE}}",
                        "sort": "price+shippingCost",
                        "limit": "3",
                    },
                )
                r.raise_for_status()
                data = r.json()
        except httpx.HTTPStatusError as e:
            log.error("[CC Music/eBay] search HTTP error %s: %s", e.response.status_code, e)
            return []
        except httpx.RequestError as e:
            log.error("[CC Music/eBay] search request error: %s", e)
            return []

        items = data.get("itemSummaries")
        if not items:
            log.info("[CC Music/eBay] no results for: %s", query)
            return []

        item = _pick_matching_item(items, release)
        if item is None:
            log.info("[CC Music/eBay] no validated match for: %s", query)
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
        if not item_url or not item_url.startswith("https://www.ebay.com"):
            legacy_id = item.get("legacyItemId")
            item_url = (
                f"https://www.ebay.com/itm/{legacy_id}"
                if legacy_id
                else self.search_url(release)
            )

        return [{
            "url": item_url,
            "price": price,
            "shipping": shipping,
            "currency": price_val.get("currency"),
            "condition": item.get("condition"),
        }]
