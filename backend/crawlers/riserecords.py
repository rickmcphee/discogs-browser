from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, strip_vendor_prefix, resolve_cover_image

_PREORDER_TAG = "preorder"
_COLLECTION_SLUG = "all"
_MUSIC_TAG = "Music"
_VINYL_TAGS = ("Vinyl LP", "Vinyl 7")


class Crawler:
    site_name: str = "Rise Records"
    base_url: str = "https://riserecords.com"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        if not cls._is_vinyl(product):
            return []

        artist = (product.get("vendor") or "").strip()
        title = strip_vendor_prefix(product.get("title", ""), artist)
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
    def _is_vinyl(product: dict) -> bool:
        # The collection is "all" (the "vinyl" slug is empty) and product_type
        # is unreliable — a confirmed-live vinyl LP had product_type "Album".
        # Tags are the reliable signal: every real vinyl product carries "Music"
        # plus "Vinyl LP" or "Vinyl 7"; apparel never carries "Music" at all.
        return has_tag(product, _MUSIC_TAG) and any(has_tag(product, t) for t in _VINYL_TAGS)
