import re
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, strip_vendor_prefix, resolve_cover_image

_VINYL_RE = re.compile(r"\bvinyl\b|\blp\b", re.IGNORECASE)
_PREORDER_TAG = "pre-order"
_COLLECTION_SLUG = "vinyl"


class Crawler:
    site_name: str = "Nuclear Blast"
    base_url: str = "https://shop.nuclearblast.com"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._vinyl_items(product):
                yield item

    @classmethod
    def _vinyl_items(cls, product: dict) -> list[dict]:
        artist = (product.get("vendor") or "").strip()
        album_title = strip_vendor_prefix(product.get("title", ""), artist)
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
            title = f"{album_title} — {variant_title}"
            if is_preorder:
                title += " (Pre-Order)"
            items.append({
                "artist": artist,
                "title": title,
                "format": "Vinyl",
                "price": price,
                "currency": "USD",
                "url": url,
                "cover_image_url": resolve_cover_image(product, variant),
            })
        return items
