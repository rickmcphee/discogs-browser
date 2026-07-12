import re
from typing import AsyncIterator
from shopify_catalog import iter_products, strip_vendor_prefix, resolve_cover_image

_COLLECTION_SLUG = "vinyl"
# Matches both this store's generic "pre-order" tag and its dated
# "Pre-Order MM-DD-YY" tag in one check — has_tag's exact-match wouldn't
# catch the dated form, and this store uses both forms live.
_PREORDER_RE = re.compile(r'^pre-order', re.IGNORECASE)


class Crawler:
    site_name: str = "Flatspot Records"
    base_url: str = "https://flatspotrecords.com"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        artist = (product.get("vendor") or "").strip()
        title = strip_vendor_prefix(product.get("title", ""), artist)
        handle = product.get("handle", "")
        url = f"{cls.base_url}/products/{handle}"
        is_preorder = cls._is_preorder(product)

        items = []
        for variant in product.get("variants") or []:
            if not variant.get("available") and not is_preorder:
                continue
            try:
                price = float(variant["price"])
            except (KeyError, TypeError, ValueError):
                price = None
            display_title = f"{title} (Pre-Order)" if is_preorder else title
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
    def _is_preorder(product: dict) -> bool:
        return any(_PREORDER_RE.match((t or "").strip()) for t in product.get("tags") or [])
