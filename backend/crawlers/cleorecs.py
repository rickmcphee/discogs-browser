import re
from typing import AsyncIterator

from shopify_catalog import iter_products, resolve_cover_image

_COLLECTION_SLUG = "vinyl-1"

# En-dash and em-dash are in the class because 34 live titles separate with
# "–" rather than "-" ("U.K. Subs – Endangered Species"). Whitespace is
# required on at least one side so hyphenated artist names survive: 18 live
# artists carry an internal hyphen with no surrounding space (Anti-Flag,
# Blink-182, Buck-O-Nine, Ann-Margret, Eek-A-Mouse).
_TITLE_RE = re.compile(r'^(?P<artist>.+?)(?:\s+[-–—]\s*|\s*[-–—]\s+)(?P<album>.+)$')
_TRAILING_PARENS_RE = re.compile(r'\s*\([^()]*\)\s*$')

# Confirmed live in this collection: BND shirt bundles, BK books, PS/PO
# posters, CD/DVD/BR discs, TB tote bags.
_NON_VINYL_TYPES = {"BND", "BK", "PS", "PO", "DVD", "BR", "TB", "CD"}
# product_type is correct on today's data, but 20 Buck Spin hit a tote bag
# typed "VINYL" live, so the title keyword check backs it up.
_MERCH_TITLE_RE = re.compile(
    r'poster|hardback book|tote bag|\bshirt\b|hoodie|sweater|bundle', re.IGNORECASE)
_DEFAULT_VARIANT_TITLE = "Default Title"


class Crawler:
    site_name: str = "Cleopatra Records"
    base_url: str = "https://cleorecs.com"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        raw_title = product.get("title", "")
        if product.get("product_type") in _NON_VINYL_TYPES:
            return []
        if _MERCH_TITLE_RE.search(raw_title):
            return []

        artist, album = cls._parse_artist_title(raw_title)
        url = f"{cls.base_url}/products/{product.get('handle', '')}"

        items = []
        for variant in product.get("variants") or []:
            if not variant.get("available"):
                continue
            try:
                price = float(variant["price"])
            except (KeyError, TypeError, ValueError):
                price = None
            variant_title = (variant.get("title") or "").strip()
            title = album if variant_title in ("", _DEFAULT_VARIANT_TITLE) \
                else f"{album} — {variant_title}"
            items.append({
                "artist": artist,
                "title": title,
                "format": "Vinyl",
                "price": price,
                "currency": "USD",
                "url": url,
                "cover_image_url": resolve_cover_image(product, variant),
            })
        return items

    @classmethod
    def _parse_artist_title(cls, title: str):
        # `vendor` is the imprint on every live product (Cleopatra Records,
        # Purple Pyramid Records, Deadline Music, ...) and never the artist, so
        # the sibling crawlers' vendor fallback is deliberately not used here.
        #
        # The split point is found on the title with trailing parentheticals
        # removed, because 11 live titles carry no artist prefix but do contain
        # " - " inside their trailing bracket. The album text keeps the
        # parentheticals: db.py's _library_match_fragment matches
        # exact-or-prefix-with-space, so a colour/format suffix doesn't block a
        # catalog match.
        base = title.strip()
        stripped = cls._strip_trailing_parens(base)
        m = _TITLE_RE.match(stripped)
        if not m:
            return "Various Artists", base
        return m.group("artist").strip(), base[m.start("album"):].strip()

    @staticmethod
    def _strip_trailing_parens(title: str) -> str:
        stripped = title.strip()
        while True:
            shorter = _TRAILING_PARENS_RE.sub('', stripped)
            if shorter == stripped:
                return stripped
            stripped = shorter
