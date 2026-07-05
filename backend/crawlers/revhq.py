import re
from typing import AsyncIterator
from shopify_catalog import iter_products, resolve_cover_image

_VINYL_RE = re.compile(r'\bvinyl\b|\blp\b|\d+\s*"', re.IGNORECASE)
_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s*"(?P<album>.+)"\s*$')
_COLLECTION_SLUG = "vinyl"


class Crawler:
    site_name: str = "Rev HQ"
    base_url: str = "https://revhq.com"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._vinyl_items(product):
                yield item

    @classmethod
    def _vinyl_items(cls, product: dict) -> list[dict]:
        artist, album_title = cls._parse_artist_title(
            product.get("title", ""), product.get("vendor", "")
        )
        handle = product.get("handle", "")
        url = f"{cls.base_url}/products/{handle}"

        items = []
        for variant in product.get("variants") or []:
            if not variant.get("available"):
                continue
            variant_title = variant.get("title", "")
            if not _VINYL_RE.search(variant_title):
                continue
            try:
                price = float(variant["price"])
            except (KeyError, TypeError, ValueError):
                price = None
            items.append({
                "artist": artist,
                "title": f"{album_title} — {variant_title}",
                "format": "Vinyl",
                "price": price,
                "currency": "USD",
                "url": url,
                "cover_image_url": resolve_cover_image(product, variant),
            })
        return items

    @staticmethod
    def _parse_artist_title(title: str, vendor: str):
        # Rev HQ's `vendor` is the record label, not the artist — the real artist only
        # exists embedded in the title as Artist "Album Title". Falls back to the label
        # if a title doesn't match that pattern, rather than leaving the artist blank.
        m = _TITLE_RE.match(title)
        if m:
            return m.group("artist").strip(), m.group("album").strip()
        return (vendor or "").strip(), title.strip()
