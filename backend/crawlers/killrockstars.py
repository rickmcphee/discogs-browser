import re
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, resolve_cover_image

_COLLECTION_SLUG = "all"
_PREORDER_TAG = "Preorder"
_VINYL_VARIANT_RE = re.compile(r'\b\d*x?lp\b|\d+\s*"', re.IGNORECASE)
# Confirmed live: a 38-variant bundle product mixes pure-vinyl, pure-CD,
# vinyl+CD-bundle, and T-shirt-only variants. A vinyl+CD-bundle variant title
# ("LP + CD Bundle / X-Small") contains "LP" and would false-positive on the
# regex above — this excludes any variant that's a bundle, even one whose
# LP-only sibling passes.
_BUNDLE_RE = re.compile(r'bundle|\+', re.IGNORECASE)


class Crawler:
    site_name: str = "Kill Rock Stars"
    base_url: str = "https://killrockstars.com"
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
            if not _VINYL_VARIANT_RE.search(variant_title) or _BUNDLE_RE.search(variant_title):
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
