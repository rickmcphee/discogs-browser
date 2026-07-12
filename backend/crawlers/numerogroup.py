import re
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, resolve_cover_image

_COLLECTION_SLUG = "vinyl"
_PREORDER_TAG = "Street Date"
_VINYL_RE = re.compile(r'\bvinyl\b|\b\d*x?lp\b', re.IGNORECASE)


class Crawler:
    site_name: str = "Numero Group"
    base_url: str = "https://numerogroup.com"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        # `vendor` is a label placeholder ("Numero"/"Numero Group") for most
        # of this back-catalog — confirmed live, and the album title never
        # contains the real artist either. Used directly as a known,
        # accepted gap: there is no reliable artist source for most of this
        # catalog. Upcoming releases (Street Date tagged) are the exception,
        # where vendor genuinely is the artist.
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
