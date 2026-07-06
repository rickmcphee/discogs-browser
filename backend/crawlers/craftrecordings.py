import re
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, strip_vendor_prefix, resolve_cover_image

_PREORDER_TAG = "_preorder"
_COLLECTION_SLUG = "vinyl"
# Almost every product here has exactly one variant, and multi-variant products are
# vinyl+shirt bundles where the variant is a shirt size ("Small"/"Medium"), not a
# format — a positive vinyl-regex filter would wrongly exclude those. Only one product
# out of 572 needed excluding: "Pleasure (LP / CD)" has a standalone "CD" variant
# alongside its "Vinyl" variant, so this is a narrow negative filter, not the usual
# positive one.
_NON_VINYL_VARIANT_RE = re.compile(r"^(cd|cassette)$", re.IGNORECASE)


class Crawler:
    site_name: str = "Craft Recordings"
    base_url: str = "https://craftrecordings.com"
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
        is_preorder = has_tag(product, _PREORDER_TAG)

        items = []
        for variant in product.get("variants") or []:
            if not variant.get("available") and not is_preorder:
                continue
            if _NON_VINYL_VARIANT_RE.match(variant.get("title", "")):
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
