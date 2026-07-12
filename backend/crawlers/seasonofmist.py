import re
from typing import AsyncIterator
from shopify_catalog import iter_products, resolve_cover_image

_COLLECTION_SLUG = "vinyl"
_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s*-\s*(?P<album>.+)$')
_PREORDER_RE = re.compile(r'pre-?order', re.IGNORECASE)


class Crawler:
    site_name: str = "Season of Mist"
    base_url: str = "https://shopusa.season-of-mist.com"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        artist, album_title = cls._parse_artist_title(
            product.get("title", ""), product.get("vendor", "")
        )
        handle = product.get("handle", "")
        url = f"{cls.base_url}/products/{handle}"
        is_preorder = bool(_PREORDER_RE.search(product.get("body_html") or ""))

        items = []
        for variant in product.get("variants") or []:
            if not variant.get("available") and not is_preorder:
                continue
            try:
                price = float(variant["price"])
            except (KeyError, TypeError, ValueError):
                price = None
            display_title = f"{album_title} (Pre-Order)" if is_preorder else album_title
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
    def _parse_artist_title(title: str, vendor: str):
        # `vendor` is always the label ("Season of Mist - North America"), never
        # the artist — the real artist is embedded in the title as
        # "Artist - Album - Format" (e.g. "Windir - 1184 - DOUBLE LP GATEFOLD
        # COLORED"). Reuses Run For Cover's non-greedy dash-split: it stops at
        # the FIRST " - ", so the album capture correctly keeps any further
        # dashes (the format descriptor) intact. Falls back to vendor only for
        # the rare title with no " - " at all.
        m = _TITLE_RE.match(title)
        if m:
            return m.group("artist").strip(), m.group("album").strip()
        return (vendor or "").strip(), title.strip()
