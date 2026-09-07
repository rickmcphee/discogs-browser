import math
import re
from typing import AsyncIterator, Optional, Tuple
from shopify_catalog import iter_products, resolve_cover_image

# The store's own format shelf, at the URL the request named. `collections.json`
# reports a products_count larger than the whole store's published catalog --
# it counts products that are not published to the online store -- so the
# walk's own exhaustion is the catalog, confirmed 2026-09-07 to return the
# same product ids at limit=250 and limit=50.
_COLLECTION_SLUG = "vinyl"
# `vendor` names nobody on this store: `vendor-unknown` on the bulk of the
# catalog, the label's own name (`Earache Records Ltd`, `Earache Records`,
# `Earache Webstore`, `EARACHE`) on the rest. The artist is instead the part
# of the title before the quoted album -- `Abbath "Dread Reaver" Gatefold
# Silver Vinyl`.
#
# The artist group excludes the double quotes outright, so the album's
# OPENING quote is always the title's first and a descriptor's inch marker
# (`3x12"`) can never be read as one. Closing it takes three spellings, each
# forced by live titles: `"`/`”` before whitespace or the end is the norm;
# `'`/`’` before whitespace or the end covers the ones the store closed with
# an apostrophe (`"America's Volume Dealer'`), and requiring whitespace after
# it is what keeps an apostrophe INSIDE an album from closing it early
# (`"'74 Jailbreak"`, `"Live In Santa Monica '72"`, `"We're Here Because
# We're Here"`); `"` before a digit covers the one the store glued onto its
# descriptor (`"Death Of A Dead Day"2x12"  Vinyl`). A digit and not any
# character, because an inch marker always follows its digits rather than
# preceding one.
_TITLE_RE = re.compile(
    r'^(?P<artist>[^"“]+?)\s*["“]'
    r'(?P<album>.+?)'
    r'(?:["”](?=[\s\d]|$)|[\'’](?=\s|$))\s*'
    r'(?P<rest>.*)$'
)
# Multi-record bundles are shelved beside the records. A bundle is not a
# Discogs release and its price is not any record's price; the single-album
# ones (`"Entangled In Chaos" Triple Vinyl Bundle`) are several copies of one
# record at one price, which is not a listing for it either. Several also
# defeat the title parse outright -- `Prong Vinyl Bundle - "Prove You Wrong",
# "Cleansing", ...` would credit an artist of `Prong Vinyl Bundle -`.
_BUNDLE_RE = re.compile(r"\bbundles?\b|\blucky\s+dip\b", re.IGNORECASE)
# The shelf has already said the product is a record, so the descriptor gate
# is negative: a vinyl word admits outright (keeping in the box sets that
# bundle a record, and the test pressings that name no format at all), then a
# word naming another medium rejects, and anything else is admitted on the
# collection's own claim. Read against the descriptor only, never the whole
# title, so an album name cannot trip it (`Morbid Angel "ABCD" Cassette Tape
# Collector's Box` is rejected on its descriptor).
_VINYL_WORD_RE = re.compile(
    r'(?<![a-z])(?:\d+(?:\.\d+)?\s*[x×]\s*)?lps?\b|\bvinyls?\b'
    r'|\bpicture\s+discs?\b|\btest\s+pressings?\b'
    r'|(?<![a-z0-9])(?:\d+\s*[x×]\s*)?\d{1,2}\s*(?:"|”|″|inch(?:es)?\b)',
    re.IGNORECASE,
)
_NON_VINYL_MEDIA_RE = re.compile(
    r"\bcassettes?\b|\bcds?\b|\bdvds?\b|\bblu-?\s?rays?\b", re.IGNORECASE)
# Shopify's placeholder for a product with exactly one variant. It names no
# pressing, so a row built on it carries the composed title alone -- and only
# when it IS the product's sole variant: on a multi-variant product the
# placeholder is malformed data, and a blank title is never a pressing.
# Either would otherwise share the title and the product URL, and so the
# item_key, with every sibling built the same way.
_PLACEHOLDER_VARIANT = "default title"


class Crawler:
    site_name: str = "Earache Records"
    base_url: str = "https://earache.com"
    genre_summary: str = "The Nottingham label that built grindcore and death metal — Napalm Death, Carcass, Morbid Angel, Bolt Thrower, Entombed, Godflesh and Cathedral — alongside a wide rock and metal distro."
    genre: str = "metal"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        products_seen = 0
        parsed_ok = 0
        identity_missing = 0
        unreadable_stock = 0
        yielded = 0
        priced = 0
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            products_seen += 1
            # The title tally is taken before the format gate, because the
            # two guards ask different questions: whether the store still
            # writes its titles the way this crawler reads them, and -- for
            # the identity and stock tallies below, which are nested inside
            # the gate -- whether some product would have yielded a row if it
            # were in stock. Tallied together, a shelf that legitimately
            # filled up with CDs would raise "artist-source drift" while the
            # titles were perfectly readable.
            if self._parse_title(product.get("title"))[0]:
                parsed_ok += 1
                if self._record(product) is not None:
                    if not self._has_identity(product):
                        identity_missing += 1
                    elif not self._has_readable_stock_flag(self._pressings(product)):
                        unreadable_stock += 1
            for item in self._items(product):
                yielded += 1
                if item["price"] is not None:
                    priced += 1
                yield item
        # db.replace_stock_items() DELETEs this crawler's previous snapshot
        # before inserting, and _sync_stock only skips that call when the
        # crawl raised -- so a completed-but-empty walk is destructive where
        # a raise is inert. Each guard names a distinct way the payload can
        # stop carrying what this crawler reads. The field tallies above are
        # taken before the availability filter, so a sold-out product still
        # counts toward every one of them; `yielded` and `priced` are
        # necessarily counted after it, which is why the guards reading them
        # are each conditioned on a second tally rather than on emptiness
        # alone -- a shelf that has simply sold out is empty legitimately.
        #
        # There is deliberately no format-gate guard: the gate is negative,
        # so no positive signal's disappearance can silently empty the walk.
        if products_seen == 0:
            raise RuntimeError(f"{_COLLECTION_SLUG} collection returned no products -- renamed, removed, or markup drift")
        if parsed_ok == 0:
            # The artist is read out of the product title's quoted-album
            # convention, and from nothing else, so this is the guard that
            # notices the store renaming its records or dropping the quotes:
            # every record would then be skipped while the walk still
            # completed.
            raise RuntimeError(
                f'no product in the {_COLLECTION_SLUG} collection has a title of the form '
                'Artist "Album" -- artist-source drift')
        if yielded and not priced:
            # Rows without the emptiness: `_price` answers None for a value
            # it cannot use, so a `price` field removed or retyped
            # store-wide re-lists the whole catalog with no prices, which is
            # worse than the snapshot it would replace. Isolated nulls stay
            # tolerated.
            raise RuntimeError(
                f"none of the {yielded} rows from the {_COLLECTION_SLUG} collection carries a "
                "price -- price-source drift")
        if not yielded and identity_missing:
            # `title` and `handle` are identity, not display: item_key hashes
            # the row's title and URL, so a product missing either is skipped
            # rather than emitted under a fresh identity that would orphan
            # the judgments and saves keyed on its old one. Skipped rows
            # leave the walk looking sold out, which is why the same
            # empty-outcome gate as the stock guard below applies.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while "
                f"{identity_missing} record(s) carry no title or handle -- identity-source drift")
        if not yielded and unreadable_stock:
            # An empty result is only trustworthy when every product that
            # could have yielded a row was readable and simply out of stock.
            # Counting unreadable products rather than readable ones is what
            # catches the partial case: one genuinely sold-out product must
            # not vouch for a catalog that has gone unreadable behind it.
            # Gated on having yielded nothing, so an isolated unreadable
            # product among real rows stays an ordinary skipped row.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while "
                f"{unreadable_stock} record(s) carry no readable availability flag -- stock-source drift")

    @classmethod
    def _items(cls, product: dict) -> list:
        record = cls._record(product)
        if record is None:
            return []
        artist, title = record
        if not cls._has_identity(product):
            return []
        url = f"{cls.base_url}/products/{(product.get('handle') or '').strip()}"
        items = []
        for variant, descriptor in cls._pressings(product):
            # Only the literal True admits a variant: the string "false" is
            # truthy, so a falsiness test would publish a sold-out record as
            # in stock. Anything else -- False, "false", 1, None, absent --
            # is skipped, which is also what keeps this filter and
            # _has_readable_stock_flag agreeing on what "readable" means.
            # No pre-order bypass: an unavailable pre-order here is a closed
            # early-bird allocation, confirmed live to render "Sold Out".
            if variant.get("available") is not True:
                continue
            # The pressing is appended on every row that names one, not only
            # when the product has more than one variant: a sibling being
            # listed or delisted must not re-title the rows and orphan the
            # listings, judgments and saves keyed on the old identity. The
            # placeholder is the one exception, because it names nothing,
            # and _pressings admits it only as a product's sole variant, so
            # no sibling can share the bare title.
            items.append({
                "artist": artist,
                "title": f"{title} — {descriptor}" if descriptor else title,
                "format": "Vinyl",
                "price": cls._price(variant),
                "currency": "GBP",
                "url": url,
                "cover_image_url": resolve_cover_image(product, variant),
            })
        return items

    @classmethod
    def _record(cls, product: dict) -> Optional[Tuple[str, str]]:
        """(artist, title) for a product this crawler reads as a record, else None."""
        artist, album, descriptor = cls._parse_title(product.get("title"))
        if not artist:
            return None
        if not cls._is_vinyl(descriptor):
            return None
        return artist, " ".join(f"{album} {descriptor}".split())

    @staticmethod
    def _parse_title(title) -> Tuple[str, str, str]:
        """Split `Artist "Album" descriptor` -- ("", "", "") when the title is not of that form.

        The descriptor is kept, and kept after the album, on both counts
        deliberately. It is where the store says which pressing a product is
        ("Gatefold Silver Vinyl", "3x12\" Yellow Vinyl", "- PRE-ORDER"), and
        title_key folds its format words away for the Cheapest filter; and
        the library match behind the Store tab's Collection and Wantlist
        filters is exact-or-prefix-with-space against the catalog title, which
        "Dread Reaver Gatefold Silver Vinyl" satisfies for a library "Dread
        Reaver" only while the album leads.
        """
        collapsed = " ".join((title or "").split())
        if _BUNDLE_RE.search(collapsed):
            return "", "", ""
        m = _TITLE_RE.match(collapsed)
        if m is None:
            return "", "", ""
        artist = m.group("artist").strip()
        album = m.group("album").strip()
        if not artist or not album:
            return "", "", ""
        return artist, album, m.group("rest").strip()

    @staticmethod
    def _is_vinyl(descriptor: str) -> bool:
        if _VINYL_WORD_RE.search(descriptor):
            return True
        return not _NON_VINYL_MEDIA_RE.search(descriptor)

    @classmethod
    def _pressings(cls, product: dict) -> list:
        pairs = []
        # Non-mapping entries are dropped here, before anything reads them,
        # so a junk entry is an ordinary skipped row rather than an
        # AttributeError from inside the yield loop.
        variants = [v for v in product.get("variants") or [] if isinstance(v, dict)]
        for variant in variants:
            title = " ".join((variant.get("title") or "").split())
            if not title:
                continue
            if title.lower() == _PLACEHOLDER_VARIANT:
                if len(variants) == 1:
                    pairs.append((variant, ""))
                continue
            pairs.append((variant, title))
        return pairs

    @staticmethod
    def _has_identity(product: dict) -> bool:
        return bool((product.get("title") or "").strip()) and bool((product.get("handle") or "").strip())

    @staticmethod
    def _has_readable_stock_flag(pressings: list) -> bool:
        # every(), not any(): one readable variant does not make the product
        # readable. A product whose black pressing is a readable False and
        # whose coloured pressing carries the string "false" yields nothing,
        # and under any() would vouch for an emptiness half its own doing.
        return bool(pressings) and all(
            isinstance(v.get("available"), bool) for v, _ in pressings)

    @staticmethod
    def _price(variant: dict) -> Optional[float]:
        raw = variant.get("price")
        # bool before float(): bool is an int subclass, so True would price a
        # record at 1. nan is the other one a truthiness check cannot catch.
        if isinstance(raw, bool):
            return None
        try:
            price = float(raw)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(price) or price <= 0:
            return None
        return price
