import re
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, resolve_cover_image

# The store's own "all-vinyl" collection omits confirmed-live vinyl products
# it has mistagged (e.g. product_type "CD" on a real vinyl pressing) -- see
# 2026-08-14-jackpot-records-store-crawler-design.md "Why the full catalog,
# not the all-vinyl collection". Iterating the full catalog and gating
# in-process on tag-or-title-word recovers them instead.
_COLLECTION_SLUG = "online-store"
_PREORDER_TAG = "pre-order"
_VINYL_TAG = "vinyl"
# Matches only the literal word "vinyl", not a broader lp|ep|"-style regex,
# so it doesn't also match "LP" as a substring of an album's own canonical
# name (e.g. "The Marshall Mathers LP").
_VINYL_WORD_RE = re.compile(r'\bvinyl\b', re.IGNORECASE)
# Hyphen/en-dash/em-dash split with asymmetric spacing, from cleorecs.py --
# confirmed live: this store also uses "Artist – Album" and "Artist- Album"
# forms, not just "Artist - Album". No vendor fallback: unlike the label
# stores this fleet otherwise covers, vendor here is the store's own name
# or a reissue label, never the artist.
_TITLE_RE = re.compile(r'^(?P<artist>.+?)(?:\s+[-–—]\s*|\s*[-–—]\s+)(?P<album>.+)$')


class Crawler:
    site_name: str = "Jackpot Records"
    base_url: str = "https://jackpotrecords.com"
    genre_summary: str = "Portland, Oregon record store and label with a broad new-vinyl selection across genres."
    genre: str = "marketplace"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            if not self._is_vinyl(product):
                continue
            for item in self._items(product):
                yield item

    @classmethod
    def _is_vinyl(cls, product: dict) -> bool:
        return has_tag(product, _VINYL_TAG) or bool(_VINYL_WORD_RE.search(product.get("title", "")))

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        m = _TITLE_RE.match(product.get("title", ""))
        if not m:
            return []
        artist = m.group("artist").strip()
        album = m.group("album").strip()

        handle = product.get("handle", "")
        url = f"{cls.base_url}/products/{handle}"
        is_preorder = has_tag(product, _PREORDER_TAG)

        items = []
        for variant in product.get("variants") or []:
            if not variant.get("available") and not is_preorder:
                continue

            try:
                price = float(variant["price"])
            except (KeyError, TypeError, ValueError):
                price = None

            items.append({
                "artist": artist,
                "title": album,
                "format": "Vinyl",
                "price": price,
                "currency": "USD",
                "url": url,
                "cover_image_url": resolve_cover_image(product, variant),
            })
        return items
