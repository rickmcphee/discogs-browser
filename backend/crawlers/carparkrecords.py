import re
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, resolve_cover_image

_COLLECTION_SLUG = "music"
_PREORDER_TAG = "preorder"
_ALLOWED_PRODUCT_TYPES = {"music"}
# Matches an optional catalog-number prefix ("CAK188", "CAKD067", "CAK087X",
# and the one dual-catalog-number "WIX04/05") immediately followed by the
# artist/title text -- matches only a 2-5 letter all-caps run immediately
# followed by 1-4 digits, so a band name shaped like that (e.g. "MC5") would
# collide with it if used as a title prefix.
_CODE_RE = re.compile(r'^[A-Z]{2,5}\d{1,4}[A-Z]?(?:/\d{1,4})?\s*-?\s*')
# Splits on the first whitespace-dash-whitespace run, not a bare "-", so an
# unspaced hyphen inside an album title (e.g. "2001-2005") isn't mistaken
# for the artist/title separator. \s also matches the one confirmed-live
# title with a literal tab character in place of a space before the dash.
_SPLIT_RE = re.compile(r'\s+-\s+')
# Non-vinyl variant titles are almost always a bare format word, but two
# confirmed-live products ("Gemini I CD", "Deluxe Ibanez DE-7 Pink Edition
# Tape") carry it as a suffix instead -- the \b-bounded suffix match catches
# those without matching any live vinyl variant (no vinyl variant title in
# this catalog contains "cd", "tape", "cassette", or "digital" as a word).
_NON_VINYL_RE = re.compile(r'^(cd|cs|cassette|tape|digital|christmas ornament|playing cards|dvd)$', re.IGNORECASE)
_NON_VINYL_SUFFIX_RE = re.compile(r'\btape\b|\bcassette\b|\bdigital\b|\bcds?\b', re.IGNORECASE)
# Overrides the suffix regex above when a vinyl bundle variant (e.g.
# "LP + Cassette") happens to also carry a non-vinyl format word.
_VINYL_RE = re.compile(r'\bvinyl\b|\blp\b|\d{1,2}\s*"', re.IGNORECASE)


class Crawler:
    site_name: str = "Carpark Records"
    base_url: str = "https://store.carparkrecords.com"
    genre_summary: str = "Annandale/Baltimore indie label — Toro y Moi, Beach House, Dan Deacon, Speedy Ortiz, The Beths."
    genre: str = "rock"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        product_type = (product.get("product_type") or "").strip().lower()
        if product_type not in _ALLOWED_PRODUCT_TYPES:
            return []

        artist, album_title = cls._parse_artist_title(
            product.get("title", ""), product.get("vendor", "")
        )
        handle = product.get("handle", "")
        url = f"{cls.base_url}/products/{handle}"
        is_preorder = has_tag(product, _PREORDER_TAG)

        items = []
        for variant in product.get("variants") or []:
            if not variant.get("available") and not is_preorder:
                continue
            variant_title = variant.get("title", "")
            stripped = variant_title.strip()
            if _NON_VINYL_RE.match(stripped) or (_NON_VINYL_SUFFIX_RE.search(stripped) and not _VINYL_RE.search(stripped)):
                continue
            try:
                price = float(variant["price"])
            except (KeyError, TypeError, ValueError):
                price = None
            display_title = (
                album_title if stripped.lower() == "default title"
                else f"{album_title} — {variant_title}"
            )
            if is_preorder:
                display_title += " (Pre-Order)"
            items.append({
                "artist": artist,
                "title": display_title,
                "format": "Vinyl",
                "price": price,
                "currency": "USD",
                "url": url,
                "cover_image_url": resolve_cover_image(product, variant),
            })
        return items

    @staticmethod
    def _parse_artist_title(title: str, vendor: str):
        rest = _CODE_RE.sub("", title, count=1).strip()
        parts = _SPLIT_RE.split(rest, maxsplit=1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            return parts[0].strip(), parts[1].strip()
        return (vendor or "").strip(), rest
