import re
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, resolve_cover_image

_COLLECTION_SLUGS = ("ppr-12-vinyl", "ppr-7", "ppr-10-vinyl", "all-distro-titles")
_PREORDER_TAG = "preorder"
_VINYL_PRODUCT_TYPES = {"Vinyl LP", "Picture Disc"}
# vendor is reliably the artist here, but doesn't always exact-prefix-match the
# title (case/whitespace drift confirmed on 58/500 titles live, e.g. vendor
# "Crim" vs. title "CRIM - ..."), so strip_vendor_prefix would miss those.
# Splitting the title on its own first " - " works regardless of vendor's casing.
_TITLE_RE = re.compile(r'^.+?\s*-\s*(?P<album>.+)$')


class Crawler:
    site_name: str = "Pirates Press Records"
    base_url: str = "https://shop.piratespressrecords.com"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        seen_ids = set()
        for slug in _COLLECTION_SLUGS:
            async for product in iter_products(self.base_url, slug):
                if product["id"] in seen_ids:
                    continue
                seen_ids.add(product["id"])
                for item in self._items(product):
                    yield item

    @classmethod
    def _items(cls, product: dict) -> list:
        if product.get("product_type") not in _VINYL_PRODUCT_TYPES:
            return []

        artist = (product.get("vendor") or "").strip()
        title = cls._display_title(product.get("title", ""))
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
    def _display_title(title: str) -> str:
        m = _TITLE_RE.match(title)
        return m.group("album") if m else title
