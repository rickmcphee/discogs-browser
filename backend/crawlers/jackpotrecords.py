import re
from typing import AsyncIterator, Optional
from shopify_catalog import iter_products, has_tag, resolve_cover_image

_COLLECTION_SLUG = "online-store"
_PREORDER_TAG = "pre-order"
_VINYL_TAG = "vinyl"
# The store's own "all-vinyl" collection omits confirmed-live vinyl products
# it has mistagged (e.g. product_type "CD" on a real vinyl pressing) -- see
# 2026-08-14-jackpot-records-store-crawler-design.md "Why the full catalog,
# not the all-vinyl collection". Iterating the full catalog and gating
# in-process on tag-or-title-word recovers them without also matching "LP"
# as a substring of an album's own canonical name (e.g. "The Marshall
# Mathers LP"), which a broader lp|ep|"-style regex would.
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
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            if not self._is_vinyl(product):
                continue
            item = self._item(product)
            if item is not None:
                yield item

    @classmethod
    def _is_vinyl(cls, product: dict) -> bool:
        return has_tag(product, _VINYL_TAG) or bool(_VINYL_WORD_RE.search(product.get("title", "")))

    @classmethod
    def _item(cls, product: dict) -> Optional[dict]:
        m = _TITLE_RE.match(product.get("title", ""))
        if not m:
            return None
        artist = m.group("artist").strip()
        album = m.group("album").strip()

        variants = product.get("variants") or []
        if not variants:
            return None
        variant = variants[0]
        is_preorder = has_tag(product, _PREORDER_TAG)
        if not variant.get("available") and not is_preorder:
            return None

        try:
            price = float(variant["price"])
        except (KeyError, TypeError, ValueError):
            price = None

        handle = product.get("handle", "")
        return {
            "artist": artist,
            "title": album,
            "format": "Vinyl",
            "price": price,
            "currency": "USD",
            "url": f"{cls.base_url}/products/{handle}",
            "cover_image_url": resolve_cover_image(product, variant),
        }
