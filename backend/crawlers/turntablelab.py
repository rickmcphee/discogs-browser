import re
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, resolve_cover_image

_COLLECTION_SLUG = "vinyl-lps-alpha"
_PREORDER_TAG = "pre-order"
# Titles are always "{Artist}: {Album (edition/format details)}" on this
# store -- confirmed live against the full ~2400-item collection, 100% match,
# more reliable than the vendor field (which is occasionally abbreviated or
# differently-cased, e.g. vendor "Blue Note" vs title "Blue Note Records:").
_TITLE_RE = re.compile(r'^(?P<artist>.+?): (?P<album>.+)$')


class Crawler:
    site_name: str = "Turntable Lab"
    base_url: str = "https://www.turntablelab.com"
    genre_summary: str = "Record store and hi-fi retailer with a broad new vinyl selection across genres."
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        artist, title = cls._parse_artist_title(product.get("title", ""), product.get("vendor", ""))
        handle = product.get("handle", "")
        url = f"{cls.base_url}/products/{handle}"
        is_preorder = has_tag(product, _PREORDER_TAG)

        variants = product.get("variants") or []
        # Titles already carry the format ("... Vinyl LP") for the dominant
        # single-variant case, so only append the variant title when a
        # product genuinely has more than one -- mostly condition-graded
        # copies ("Seam Split Vinyl LP", "Bent Corner Vinyl 2LP") priced
        # apart from the standard copy, confirmed live on ~7% of variants.
        # Appending unconditionally would stamp a redundant "-- Vinyl LP" on
        # the other 93%.
        disambiguate = len(variants) > 1

        items = []
        for variant in variants:
            if not variant.get("available") and not is_preorder:
                continue
            try:
                price = float(variant["price"])
            except (KeyError, TypeError, ValueError):
                price = None
            variant_title = variant.get("title")
            display_title = f"{title} — {variant_title}" if disambiguate and variant_title else title
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
        m = _TITLE_RE.match(title)
        if m:
            return m.group("artist").strip(), m.group("album").strip()
        return (vendor or "").strip(), title.strip()
