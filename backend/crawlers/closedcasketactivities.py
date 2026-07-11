import re
from typing import AsyncIterator
from shopify_catalog import iter_products, resolve_cover_image

_COLLECTION_SLUG = "vinyl"
_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s*-\s*(?P<album>.+)$')
# Two pre-order tag conventions confirmed live on this store: "__label:Pre-Order"
# and a bare lowercase "preorder" — a substring search covers both.
_PREORDER_RE = re.compile(r'pre-?order', re.IGNORECASE)
# A positive vinyl-regex would wrongly exclude the confirmed-live single
# "Default Title" vinyl variant (no format keyword at all) — a narrow
# negative filter for the actual non-vinyl siblings is used instead.
_NON_VINYL_VARIANT_RE = re.compile(r'^(cd|cassette|digital( download)?)$', re.IGNORECASE)


class Crawler:
    site_name: str = "Closed Casket Activities"
    base_url: str = "https://closedcasketactivities.com"
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
            if _NON_VINYL_VARIANT_RE.match(variant_title.strip()):
                continue
            try:
                price = float(variant["price"])
            except (KeyError, TypeError, ValueError):
                price = None
            display_title = (
                album_title if variant_title.strip().lower() == "default title"
                else f"{album_title} — {variant_title}"
            )
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
        # `vendor` is always the label here, never the artist — real artist
        # (sometimes two, for splits: "Artist1 / Artist2") is embedded in the
        # title as "Artist - Album".
        m = _TITLE_RE.match(title)
        if m:
            return m.group("artist").strip(), m.group("album").strip()
        return (vendor or "").strip(), title.strip()
