import re
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, resolve_cover_image

_PREORDER_TAG = "Pre-Order"
_COLLECTION_SLUG = "vinyl"
# Plain \blp\b misses glued formats like "2xLP" (no word boundary before a digit/letter-glued
# "LP") — the same gap Fat Wreck Chords needed this wider pattern for; see the design spec.
_VINYL_RE = re.compile(r'\bvinyl\b|\b\d*x?lp\b|\d+\s*"', re.IGNORECASE)


class Crawler:
    site_name: str = "Secretly Store"
    base_url: str = "https://secretlystore.com"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        artist = (product.get("vendor") or "").strip()
        title = product.get("title", "")
        handle = product.get("handle", "")
        url = f"{cls.base_url}/products/{handle}"
        is_preorder = has_tag(product, _PREORDER_TAG)

        items = []
        for variant in product.get("variants") or []:
            if not variant.get("available") and not is_preorder:
                continue
            variant_title = variant.get("title", "")
            if not _VINYL_RE.search(variant_title):
                continue
            try:
                price = float(variant["price"])
            except (KeyError, TypeError, ValueError):
                price = None
            display_title = f"{title} — {variant_title}"
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
