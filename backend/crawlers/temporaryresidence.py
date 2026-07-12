import re
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, resolve_cover_image

_COLLECTION_SLUG = "shop"
_PREORDER_TAG = "Flag_Pre-Order"
_ALBUM_PRODUCT_TYPE = "Albums"
_VINYL_RE = re.compile(r'\bvinyl\b|\b\d*x?lps?\b', re.IGNORECASE)


class Crawler:
    site_name: str = "Temporary Residence Ltd"
    base_url: str = "https://temporaryresidence.com"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        # "shop" mixes in T-Shirts and Gift Cards alongside real releases —
        # confirmed live. A mistyped non-music "Book" product also carries
        # product_type "Albums", but its variant title doesn't match the
        # vinyl/LP regex below, so no special case is needed for it.
        if (product.get("product_type") or "").strip() != _ALBUM_PRODUCT_TYPE:
            return []

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
