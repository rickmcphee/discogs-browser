import re
from typing import AsyncIterator, Optional

from shopify_catalog import iter_products, resolve_cover_image

# "Everything we got!" -- a strict superset by product id of every other
# collection on this store, so one pass needs no cross-collection
# de-duplication. Do not swap it for a vinyl-looking collection: `lp`,
# `new-vinyl` and `allusedvinyl` each miss part of the catalog, and the
# store's self-reported `products_count` is stale metadata (`quick-order`
# claims 101,758 and paginates to 10,975), so it cannot be used to compare
# them. See the design spec's "Collection choice" section.
_COLLECTION_SLUG = "all"

# The store's own format classification, and the only positive vinyl gate.
# Bare "7" is not a typo of '7"' -- three used singles carry it. `Box Set` is
# deliberately absent: it names a packaging rather than a format, so admitting
# it would also admit the merch box sets this store sells.
_VINYL_TYPES = {"LP", '7"', '10"', '12"', "7"}

# 195 products carry a LEFT-TO-RIGHT MARK between the artist and the opening
# quote. It is a format character, not whitespace, so str.strip() leaves it
# on the artist -- and db._library_match_fragment compares artist with exact
# LOWER() equality, where one invisible character is the difference between a
# match and silence.
_INVISIBLE_RE = re.compile(r'[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]')

# Status marker the store prefixes onto the title, ahead of the artist. Every
# form here is live, typos included. It has to come off before the artist can
# be read, or the artist is "Used Vinyl: Nirvana", which matches nothing.
#
# A colon is required for the `used`, `pre-order` and bare `damaged` forms.
# That costs one live product (`DAMAGED Sultans "Ghost Ship" LP`) and is the
# point: "Damaged Bug" is a real band, and a rule stripping a leading
# "DAMAGED " would rewrite its artist to "Bug". "DAMAGED COVER" needs no
# separator because two words in that order cannot be an artist name.
#
# Applied repeatedly, not once: two live products are double-marked
# ("Used Vinyl: Used Vinyl: Aso-Naga / Restriction \"Split\" 7\""), and a
# single pass leaves the second copy stuck to the artist.
_MARKER_RE = re.compile(
    r'^(?:'
    r'(?P<used>used(?:\s+(?:viny\w*|lps?|cds?|cassettes?))?)\s*:+'
    r'|(?P<preorder>pre[\s-]?ord\w*)\s*:+'
    r'|(?P<damaged>damage\w*\s+cover\s*[:-]?|damage\w*\s*:+)'
    r')\s*',
    re.IGNORECASE,
)
_USED_SUFFIX = "(Used)"
_PREORDER_SUFFIX = "(Pre-Order)"
_DAMAGED_SUFFIX = "(Damaged)"

# `Artist "Album" DESCRIPTOR`, this store's own title convention -- the fleet's
# usual `Artist - Album` dash split does not apply here at all.
#
# The doubled apostrophe is a real delimiter, not a stand-in for a missing
# font: 28 products use it and nothing else, and `Superchunk ''I Hate Music''
# LP` is unparseable without it. It cannot be confused with a possessive
# ("Guns N' Roses") because it requires two adjacent apostrophes.
#
# Both groups are non-greedy, which is what lets a quoted phrase inside the
# pressing notes ("... 2xLP (... Edition \"Hyperspace\" Blue Splatter Vinyl)")
# and the trailing inch mark on a 7" sit outside the split without disturbing
# it.
_QUOTE = r'(?:\'\'|["“”])'
_TITLE_RE = re.compile(
    r'^(?P<artist>.+?)\s*' + _QUOTE + r'(?P<album>.+?)' + _QUOTE + r'\s*(?P<descriptor>.*)$'
)

# Formats that are not records, and the vinyl vocabulary that overrides them.
# Used on the title's format descriptor and on a multi-variant product's
# variant names, never on the album itself -- an album may legitimately be
# called "Cassette", and Adele's "19" would trip any regex asked to decide
# whether an album name looks like a format.
_NON_VINYL_RE = re.compile(
    r'\b(?:cds?|cassettes?|tapes?|dvds?|blu-?rays?|digital)\b', re.IGNORECASE
)
_VINYL_WORD_RE = re.compile(
    r'\bvinyl\b|\b\d*x?\s?lps?\b|\beps?\b|\bflexi\b|\d+\s*"', re.IGNORECASE
)


class Crawler:
    site_name: str = "1-2-3-4 Go! Records"
    base_url: str = "https://1234gorecords.shop"
    genre_summary: str = "Oakland, California independent record store trading since 2008 — new and used vinyl across every genre, deepest in punk, indie and Bay Area labels."
    genre: str = "marketplace"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            if not self._is_vinyl(product):
                continue
            for item in self._items(product):
                yield item

    @classmethod
    def _is_vinyl(cls, product: dict) -> bool:
        return (product.get("product_type") or "").strip() in _VINYL_TYPES

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        parsed = cls._parse_title(product.get("title", ""))
        if parsed is None:
            return []
        artist, album = parsed

        variants = product.get("variants") or []
        # Only on a multi-variant product: a single variant is usually the
        # "Default Title" placeholder, which names no format at all, and two
        # live products (Roger Bekono, King Tuff) have their vinyl variants
        # sold out with only a cassette left in stock.
        if len(variants) > 1:
            survivors = [
                v for v in variants
                if not cls._competes_with_vinyl(v.get("title") or "")
            ]
        else:
            survivors = list(variants)
        # Counted over survivors rather than available survivors so a row's
        # identity holds still: db.compute_item_key() hashes (artist, title,
        # url) and this url is per-product, so a title that gained or lost its
        # variant descriptor as a sibling sold out would orphan that row's
        # listings and saved-item rows.
        is_multi_variant = len(survivors) > 1

        url = f"{cls.base_url}/products/{product.get('handle', '')}"
        items = []
        for variant in survivors:
            if not variant.get("available"):
                continue
            title = album
            if is_multi_variant:
                title = f"{title} — {(variant.get('title') or '').strip()}"
            items.append({
                "artist": artist,
                "title": title,
                "format": "Vinyl",
                "price": cls._price(variant),
                "currency": "USD",
                "url": url,
                "cover_image_url": resolve_cover_image(product, variant),
            })
        return items

    @classmethod
    def _parse_title(cls, raw_title: str):
        """Split `[MARKER:] Artist "Album" DESCRIPTOR` into (artist, title).

        Returns None for the 0.3% of live products the store wrote without a
        usable pair of quotes -- unbalanced ones ('Frank Turner "Tape Deck
        Heart LP'), none at all ('Sophie S/T 2xLP'), and the shirt-plus-record
        bundles. Dropping them beats attributing them to `vendor`, which holds
        a distributor ("Alliance", "UMG", "WEA") or the literal "Used Product".
        """
        cleaned = cls._clean(raw_title)

        suffixes = []
        while True:
            marker = _MARKER_RE.match(cleaned)
            if not marker:
                break
            if marker.group("used"):
                suffix = _USED_SUFFIX
            elif marker.group("preorder"):
                suffix = _PREORDER_SUFFIX
            else:
                suffix = _DAMAGED_SUFFIX
            if suffix not in suffixes:
                suffixes.append(suffix)
            cleaned = cleaned[marker.end():]

        m = _TITLE_RE.match(cleaned)
        if not m:
            return None
        artist = m.group("artist").strip()
        album = m.group("album").strip()
        if not artist or not album:
            return None

        descriptor = m.group("descriptor").strip()
        if cls._competes_with_vinyl(descriptor):
            return None

        # db._library_match_fragment matches a stock title against the catalog
        # exact-or-prefix-with-space, which decides both halves of this line.
        # The descriptor is kept rather than trimmed away because either form
        # matches, so the only question left is whether two Store rows can be
        # told apart, and the pressing note is what tells them apart: dropping
        # it would leave 1,003 rows reading identically to another row instead
        # of 237. The marker moves to the back for the opposite reason -- a
        # prefix is the one position that match cannot survive.
        title = " ".join([f"{album} {descriptor}".strip()] + suffixes)
        return artist, title

    @staticmethod
    def _clean(text: str) -> str:
        return re.sub(r'\s+', ' ', _INVISIBLE_RE.sub('', text or '')).strip()

    @staticmethod
    def _competes_with_vinyl(text: str) -> bool:
        """Whether a format descriptor or variant name says "this is not a record".

        The vinyl override is what keeps the store's genuine hybrid releases
        ("2xLP + CD", "LP + 7\"", "2xLP 2xCD + DVD") in, while dropping the
        LP-typed products that are CDs or Blu-Rays by their own titles.
        """
        return bool(_NON_VINYL_RE.search(text) and not _VINYL_WORD_RE.search(text))

    @staticmethod
    def _price(variant: dict) -> Optional[float]:
        try:
            return float(variant["price"])
        except (KeyError, TypeError, ValueError):
            return None
