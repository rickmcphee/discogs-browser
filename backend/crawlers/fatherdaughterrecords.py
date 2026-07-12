import re
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, resolve_cover_image

_COLLECTION_SLUG = "vinyl"
_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s*-\s*(?P<album>.+)$')
_PREORDER_TAG = "Pre-order"
# "\b...lp\b" alone misses the plural "LPs" ("2 Mystery LPs") since there's
# no word boundary between "p" and a trailing "s" — the optional "s?" covers it.
_VINYL_RE = re.compile(r'\bvinyl\b|\b\d*x?lps?\b', re.IGNORECASE)


class Crawler:
    site_name: str = "Father/Daughter Records"
    base_url: str = "https://fatherdaughterrecords.com"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        # Bundle/grab-bag products collapse to a single non-descriptive
        # "Default Title" variant and are confirmed live to always have an
        # empty product_type — no variant-title signal can tell vinyl from
        # non-vinyl for these, so they're excluded entirely.
        if not (product.get("product_type") or "").strip():
            return []

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
    def _parse_artist_title(title: str, vendor: str):
        # `vendor` is a label placeholder, spelled two different ways live
        # ("Father/Daughter Records" and "Father/Daughter") — never the
        # artist. Real artist is embedded in the title as "Artist - Album"
        # for ordinary releases; grab-bag titles like "Mystery LP" have no
        # dash and fall back to vendor.
        m = _TITLE_RE.match(title)
        if m:
            return m.group("artist").strip(), m.group("album").strip()
        return (vendor or "").strip(), title.strip()
