import re
from typing import AsyncIterator, Optional
from shopify_catalog import iter_products, has_tag, resolve_cover_image

_COLLECTION_SLUG = "record-store"
_PREORDER_TAG = "PREORDER"
# Matches straight or curly quotes on either side independently, and doesn't
# require the closing quote to end the string -- titles like 'Absent In
# Body "Plague God" LP' have trailing format text after it.
_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s*[""](?P<album>.+?)[""]')
# Applied to the text *after* the closing quote (or the whole title, for the
# minority with no quotes at all) -- never to the whole title including the
# album name. Several album titles end in a digit right before the closing
# quote (F.Y.P "Incomplete Crap Vol. 2" CD), which a whole-title inch-mark
# regex misreads as a real inch mark.
_VINYL_RE = re.compile(r'\bvinyl\b|\blps?\b|\d{1,2}\s*(?:"|\binch\b)|\bpicture disc\b', re.IGNORECASE)
_NON_VINYL_RE = re.compile(r'\bcds?\b|\bcassette\b|\btape\b|\bgift card\b', re.IGNORECASE)
# This store's variant titles are almost always a bare color name or
# "Default Title" -- no format word to match positively. The one live
# exception is 4 products offering separate LP/CD variants, where the
# variant title literally is "LP" or "CD"; this negative filter only ever
# fires on those products' CD variant.
_NON_VINYL_VARIANT_RE = re.compile(r'^(cds?|cassette)$', re.IGNORECASE)


class Crawler:
    site_name: str = "Anxious and Angry"
    base_url: str = "https://anxiousandangry.com"
    genre_summary: str = "Ryan Young (Off With Their Heads)'s punk mailorder record store."
    genre: str = "punk"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        artist, album_title, suffix = cls._parse_artist_title(
            product.get("title", ""), product.get("vendor", "")
        )
        is_vinyl = bool(_VINYL_RE.search(suffix))
        is_non_vinyl = bool(_NON_VINYL_RE.search(suffix))
        if is_non_vinyl and not is_vinyl:
            return []

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
        # `vendor` carries the real artist on some releases (American Steel
        # "Rogues March" LP -> vendor "American Steel") but is just the
        # store's own name on others -- unlike deathwishinc.py/
        # no_idea_records.py, it's not uniformly one or the other, so it's
        # only used as the fallback, never overriding a quote match
        # (confirmed live: 124/128 titles match, 96.9%).
        m = _TITLE_RE.match(title)
        if m:
            return m.group("artist").strip(), m.group("album").strip(), title[m.end():]
        return (vendor or "").strip(), title.strip(), title
