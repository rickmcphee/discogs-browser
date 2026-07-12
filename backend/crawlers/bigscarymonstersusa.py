import re
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, resolve_cover_image

_COLLECTION_SLUG = "vinyl"
# Confirmed live: titles use either a hyphen or an en dash ("–") as the
# artist/album separator.
_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s*[-–]\s*(?P<album>.+)$')
_PREORDER_TAG = "Pre-order"
# Same landmine as Closed Casket Activities: a confirmed-live single
# "Default Title" vinyl variant carries no format keyword, so the filter here
# is negative (exclude the real non-vinyl siblings) rather than positive.
_NON_VINYL_VARIANT_RE = re.compile(r'^(cd|cassette|digital( download)?)$', re.IGNORECASE)


class Crawler:
    site_name: str = "Big Scary Monsters USA"
    base_url: str = "https://usa.bsmrocks.com"
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
        is_preorder = has_tag(product, _PREORDER_TAG)

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
    def _parse_artist_title(title: str, vendor: str):
        # `vendor` is confirmed unreliable — the same artist ("Lakes") shows
        # up correctly as vendor on one release and as the store's own name
        # on another. Title parsing is the only consistent source.
        m = _TITLE_RE.match(title)
        if m:
            return m.group("artist").strip(), m.group("album").strip()
        return (vendor or "").strip(), title.strip()
