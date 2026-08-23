import re
from typing import AsyncIterator

from shopify_catalog import iter_products, resolve_cover_image

_COLLECTION_SLUG = "vinyl"

# Product titles are `Artist "Album" FORMAT`, with the album in straight or
# typographic double quotes and a trailing format/edition blurb:
#   Sodom "1982" LP (exclusive)
#   Magnum "The Monster Roars" LP (white & black marbled vinyl)
# Same two-stage quoted-primary/dash-fallback shape as asianmanrecords.py, the
# only other store in the fleet that quotes its album titles, including its
# optional separator before the opening quote (`Artist - "Album"`). Widened
# here to accept typographic quotes, and extended with an `extra` capture the
# sibling has no use for -- this store carries its format in the title blurb,
# not in the variant, so the gate below needs the text after the album.
# Both captures are quote-free classes: the artist can never swallow the
# album's opening quote, and the album stops at the FIRST closing quote rather
# than running to the last quote in the string.
_TITLE_RE = re.compile(
    r'^(?P<artist>[^"“”]+?)\s*[-–—]?\s*["“](?P<album>[^"“”]+)["”]\s*(?P<extra>.*)$'
)
# Fallback for the occasional unquoted title. Byte-identical to
# asianmanrecords.py's own fallback: the hyphen/en-dash/em-dash class
# cleorecs.py established, with seasonofmist.py's whitespace anchoring on at
# least one side of the separator so hyphenated artist names ("Cro-Mags")
# aren't clipped at their internal hyphen.
_DASH_RE = re.compile(r'^(?P<artist>.+?)(?:\s+[-–—]\s*|\s*[-–—]\s+)(?P<album>.+)$')
_PREORDER_RE = re.compile(r'pre[\s_-]?order', re.IGNORECASE)
_VINYL_RE = re.compile(r'\b\d*lp\b|\bvinyl\b|\b(?:7|10|12)"|\bpicture disc\b', re.IGNORECASE)
_NON_VINYL_RE = re.compile(
    r'\b(cds?|digipa[kc]k?|cassette|tape|mc|dvd|blu-?ray|shirt|t-shirt|hoodie|'
    r'longsleeve|poster|patch|flag|mug|book)\b',
    re.IGNORECASE,
)


class Crawler:
    site_name: str = "SPV Entertainment"
    base_url: str = "https://store.spv.de"
    genre_summary: str = (
        "German independent label and distributor — Steamhammer and Long Branch "
        "metal, hard rock, and prog."
    )
    genre: str = "metal"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        artist, album_title, extra = cls._parse_title(
            product.get("title", ""), product.get("vendor", "")
        )
        # No artist source at all -> skip rather than publish a row that can
        # never match a Discogs release (darkdescentrecords.py's convention).
        if not artist or not album_title:
            return []
        if not cls._is_vinyl(extra):
            return []

        handle = product.get("handle", "")
        url = f"{cls.base_url}/products/{handle}"
        is_preorder = cls._is_preorder(product)

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
                "currency": "EUR",
                "url": url,
                "cover_image_url": resolve_cover_image(product, variant),
            })
        return items

    @staticmethod
    def _parse_title(title: str, vendor: str) -> tuple:
        """Split `Artist "Album" FORMAT` into its three parts.

        `vendor` is the last resort only: on this store it is expected to carry
        the label (SPV / Steamhammer / Long Branch), not the artist, so a title
        that yields no artist of its own is better dropped by the caller than
        published under the label's name.
        """
        title = (title or "").strip()
        m = _TITLE_RE.match(title)
        if m:
            return m.group("artist").strip(), m.group("album").strip(), m.group("extra").strip()
        m = _DASH_RE.match(title)
        if m:
            return m.group("artist").strip(), m.group("album").strip(), ""
        return (vendor or "").strip(), title, ""

    @staticmethod
    def _is_vinyl(extra: str) -> bool:
        """Gate on the title's trailing format blurb.

        Negative, not positive: the source collection is the store's own vinyl
        collection, so an unrecognised blurb is kept (a positive filter would
        silently drop stock whose descriptor this list doesn't anticipate) and
        only an explicit non-vinyl format is dropped. A blurb naming both
        (`LP+CD`) is vinyl.
        """
        if _VINYL_RE.search(extra):
            return True
        return not _NON_VINYL_RE.search(extra)

    @staticmethod
    def _is_preorder(product: dict) -> bool:
        tags = product.get("tags") or []
        if isinstance(tags, str):
            tags = tags.split(",")
        return any(_PREORDER_RE.search(t or "") for t in tags)
