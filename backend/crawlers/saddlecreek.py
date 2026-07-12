import re
from typing import AsyncIterator
from shopify_catalog import iter_products, resolve_cover_image

_COLLECTION_SLUG = "all"
_MUSIC_PRODUCT_TYPE = "Music"
_VINYL_VARIANT_RE = re.compile(r'\b\d*x?lp\b|\d+\s*"', re.IGNORECASE)


class Crawler:
    site_name: str = "Saddle Creek"
    base_url: str = "https://saddle-creek.com"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        if (product.get("product_type") or "").strip() != _MUSIC_PRODUCT_TYPE:
            return []

        artist = (product.get("vendor") or "").strip()
        title = product.get("title", "")
        handle = product.get("handle", "")
        url = f"{cls.base_url}/products/{handle}"

        items = []
        for variant in product.get("variants") or []:
            if not variant.get("available"):
                continue
            variant_title = variant.get("title", "")
            if not _VINYL_VARIANT_RE.search(variant_title):
                continue
            try:
                price = float(variant["price"])
            except (KeyError, TypeError, ValueError):
                price = None
            items.append({
                "artist": artist,
                "title": f"{title} — {variant_title}",
                "format": "Vinyl",
                "price": price,
                "currency": "USD",
                "url": url,
                "cover_image_url": resolve_cover_image(product, variant),
            })
        return items
