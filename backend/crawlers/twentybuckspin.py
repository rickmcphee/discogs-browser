import re
from typing import AsyncIterator
from shopify_catalog import iter_products, resolve_cover_image

_COLLECTION_SLUG = "vinyl"
_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s*-\s*(?P<album>.+)$')
# Confirmed live in this collection: a tote bag with product_type "VINYL" —
# the format field can't be trusted to exclude it, only the title keyword can.
_MERCH_TITLE_RE = re.compile(r'tote bag|t-shirt|hoodie', re.IGNORECASE)


class Crawler:
    site_name: str = "20 Buck Spin"
    base_url: str = "https://20buckspin.com"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        raw_title = product.get("title", "")
        if _MERCH_TITLE_RE.search(raw_title):
            return []

        artist, album_title = cls._parse_artist_title(raw_title, product.get("vendor", ""))
        handle = product.get("handle", "")
        url = f"{cls.base_url}/products/{handle}"

        items = []
        for variant in product.get("variants") or []:
            if not variant.get("available"):
                continue
            try:
                price = float(variant["price"])
            except (KeyError, TypeError, ValueError):
                price = None
            # Confirmed live: a "free mystery LP" promo bundle prices every
            # variant at $0.00 — not a real release, and no other signal
            # (tags are meaningless single letters, product_type is always
            # "VINYL") distinguishes it, so a zero/missing price is the filter.
            if not price:
                continue
            items.append({
                "artist": artist,
                "title": f"{album_title} — {variant.get('title', '')}",
                "format": "Vinyl",
                "price": price,
                "currency": "USD",
                "url": url,
                "cover_image_url": resolve_cover_image(product, variant),
            })
        return items

    @staticmethod
    def _parse_artist_title(title: str, vendor: str):
        m = _TITLE_RE.match(title)
        if m:
            return m.group("artist").strip(), m.group("album").strip()
        return (vendor or "").strip(), title.strip()
