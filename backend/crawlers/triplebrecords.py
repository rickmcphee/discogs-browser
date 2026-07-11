import re
from typing import AsyncIterator
from shopify_catalog import iter_products, resolve_cover_image

_COLLECTION_SLUG = "all"
_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s*-\s*(?P<album>.+)$')
# Confirmed live product_type distribution: real vinyl releases are "Vinyl",
# "CD/Vinyl" (mixed-format, needs the variant filter below), or "" (legacy
# listings with no metadata at all). Everything else here is apparel,
# cassette-only, CD-only, digital-only, or the one confirmed non-release
# "Shipping Protection" add-on product — none of those have a vinyl variant
# worth salvaging.
_EXCLUDED_PRODUCT_TYPES = {
    "t-shirt", "shirt", "hoodie", "bottoms", "accessory", "accessories",
    "shipping protection", "cassette", "cd", "digital",
}
# Real vinyl color variants here carry NO format keyword at all ("Baby Blue /
# Black Swirl (out of 200)") — the opposite of Fat Wreck Chords/Secretly
# Store/Deathwish Inc, where a positive vinyl-regex works. Here only the CD/
# Digital siblings on an otherwise-vinyl product need excluding.
_NON_VINYL_VARIANT_RE = re.compile(r'^(cd|digital( download)?)$', re.IGNORECASE)


class Crawler:
    site_name: str = "Triple B Records"
    base_url: str = "https://triplebrecords.net"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        product_type = (product.get("product_type") or "").strip().lower()
        if product_type in _EXCLUDED_PRODUCT_TYPES:
            return []

        artist, album_title = cls._parse_artist_title(
            product.get("title", ""), product.get("vendor", "")
        )
        handle = product.get("handle", "")
        url = f"{cls.base_url}/products/{handle}"

        items = []
        for variant in product.get("variants") or []:
            if not variant.get("available"):
                continue
            variant_title = variant.get("title", "")
            if _NON_VINYL_VARIANT_RE.match(variant_title.strip()):
                continue
            try:
                price = float(variant["price"])
            except (KeyError, TypeError, ValueError):
                price = None
            items.append({
                "artist": artist,
                "title": f"{album_title} — {variant_title}",
                "format": "Vinyl",
                "price": price,
                "currency": "USD",
                "url": url,
                "cover_image_url": resolve_cover_image(product, variant),
            })
        return items

    @staticmethod
    def _parse_artist_title(title: str, vendor: str):
        # `vendor` is usually "TRIPLE B RECORDS" (with real live casing
        # variance) but not always — a distributed band ("Combust") shows up
        # as its own vendor — so vendor can't be trusted either way. The
        # title's "Artist - Album" dash split is reliable regardless of what
        # vendor says.
        m = _TITLE_RE.match(title)
        if m:
            return m.group("artist").strip(), m.group("album").strip()
        return (vendor or "").strip(), title.strip()
