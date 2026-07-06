import re
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, resolve_cover_image

_PREORDER_TAG = "Pre-Order"
_COLLECTION_SLUG = "vinyl"
# Matches straight or curly quotes on either side independently (titles mix both, and
# some mismatch open/close style), and doesn't require the closing quote to end the
# string (titles like 'All Leather "..." Double LP' have trailing format text after it).
_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s*["“](?P<album>.+?)["”]')
# Deathwish's "vinyl" collection actually mixes in thousands of Cassette/CD-only
# variants (confirmed live: 1035/6096 variants), unlike the smaller single-format
# label stores — needs the same per-variant filter Fat Wreck Chords/Secretly Store
# use. One confirmed false positive out of 6096 variants: a CD novelty item titled
# 'CD - 3" \'Mini Vinyl\'' matches the inch-mark pattern; accepted as noise.
_VINYL_RE = re.compile(r'\bvinyl\b|\b\d*x?lp\b|\d+\s*"', re.IGNORECASE)


class Crawler:
    site_name: str = "Deathwish Inc"
    base_url: str = "https://deathwishinc.com"
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
        # Deathwish's `vendor` is the distro label, not the artist — the real artist
        # only exists embedded in the title as Artist "Album Title". Falls back to the
        # label if a title doesn't match that pattern. Verified against 500 live titles:
        # this regex matches 497 (99.4%); the 3 residual misses are quote-less titles
        # (a subscription product and two feat./collab credits) that fall back to the
        # label — the same accepted-risk tradeoff as Rev HQ's title parsing.
        m = _TITLE_RE.match(title)
        if m:
            return m.group("artist").strip(), m.group("album").strip()
        return (vendor or "").strip(), title.strip()
