import re
from typing import AsyncIterator
from shopify_catalog import iter_products, resolve_cover_image

_COLLECTION_SLUG = "vinyl"
_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s*-\s*(?P<album>.+)$')
_VINYL_RE = re.compile(r'\bvinyl\b|\b\d*x?lp\b|\d+\s*"', re.IGNORECASE)
_PREORDER_RE = re.compile(r'pre-?order', re.IGNORECASE)


class Crawler:
    site_name: str = "Polyvinyl Record Co."
    base_url: str = "https://polyvinylrecords.com"
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
        is_preorder = cls._is_preorder(product)

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
            display_title = f"{album_title} — {variant_title}"
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

    @staticmethod
    def _is_preorder(product: dict) -> bool:
        return any(_PREORDER_RE.search(t or "") for t in product.get("tags") or [])

    @staticmethod
    def _parse_artist_title(title: str, vendor: str):
        # `vendor` is always a label/distributor here, never the artist — even
        # Polyvinyl's own releases show "Polyvinyl Records" as vendor while
        # the title is "Artist - Album". Confirmed live: 73% of a 250-product
        # sample carries a "Non-Polyvinyl" tag (third-party labels distributed
        # through this storefront) — those are included the same as house
        # releases, since they're genuinely purchasable vinyl on this
        # collection; only the artist-attribution source differs.
        m = _TITLE_RE.match(title)
        if m:
            return m.group("artist").strip(), m.group("album").strip()
        return (vendor or "").strip(), title.strip()
