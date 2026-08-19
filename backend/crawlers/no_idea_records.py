import re
from typing import AsyncIterator, Optional
from shopify_catalog import iter_products, resolve_cover_image

_COLLECTION_SLUG = "list"
# Matches straight or curly quotes on either side independently, and doesn't
# require the closing quote to end the string -- titles like 'A WILHELM
# SCREAM "Partycrasher" + POSTER' have trailing format text after it.
_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s*[""](?P<album>.+?)[""]')
# This store uses the curly right double quotation mark (U+201D) for the inch
# mark on some 7"/8" variants alongside straight quotes on others -- both
# forms confirmed live. 4/345 kept variants are accepted noise: a genuine
# vinyl pressing whose only variant-title signal is a bare color name (no
# "LP"/"vinyl"/inch-mark token at all) is indistinguishable from real
# non-vinyl noise on this store and is dropped along with it.
_VINYL_RE = re.compile(r'\bvinyl\b|\b\d*x?lp\b|\d+\s*[”″"]', re.IGNORECASE)


class Crawler:
    site_name: str = "No Idea Records"
    base_url: str = "https://noidearecords.com"
    genre_summary: str = "Gainesville, FL punk and emo label and mailorder store."
    genre: str = "punk"
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
        # No Idea's `vendor` is the store's own name, not the artist -- the
        # real artist only exists embedded in the title as Artist "Album
        # Title". Falls back to the store name if a title doesn't match that
        # pattern (confirmed live: 352/360 titles match, 97.8%).
        m = _TITLE_RE.match(title)
        if m:
            return m.group("artist").strip(), m.group("album").strip()
        return (vendor or "").strip(), title.strip()
