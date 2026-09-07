import math
import re
from typing import AsyncIterator, Optional, Tuple

from shopify_catalog import iter_products, resolve_cover_image

# Shopify's built-in all-products collection, which the request named. It is
# also a strict superset of the store's own vinyl shelves: confirmed live
# that `vinyl-shop` holds only the plain `Vinyl` products, missing every
# `Distro Vinyl` and `Vinyl/CD` one, and that no product on either of the
# `vinyl-shop`/`distro-vinyl` shelves is absent from `all`. The format
# scoping is done by the product_type gate below instead. `all` is complete:
# it returns exactly the store's published_products_count from meta.json.
_COLLECTION_SLUG = "all"
# Enumerated positively rather than matched negatively, so a non-music type
# the store might add stays out by default. `Vinyl/CD` is a mixed-format
# product whose CD sibling the variant gate drops; the store's other live
# types are apparel, `Tape`, `CD`, `Accessories`, `Poster`, `Flags`, plus a
# `HIDDEN` and an `mws_fee_generated` that are not products at all. No vinyl
# hides outside these three -- confirmed live that no variant of any other
# type names a record.
_VINYL_PRODUCT_TYPES = frozenset({"vinyl", "distro vinyl", "vinyl/cd"})
# Whitespace required on at least one side of the hyphen, the repo's standard
# fix for this bug class: a plain \s*-\s* form would split a hyphenated word,
# and this catalog has one ("ANORAK! - Self-actualization and the ignorance
# and hesitation towards it"). It survives a naive split only because the
# billing hyphen happens to come first; a title whose artist carried the
# hyphen would not.
_TITLE_RE = re.compile(r'^(?P<artist>.+?)(?:\s+-\s*|\s*-\s+)(?P<album>.+)$')
# Shopify's placeholder for a product with exactly one variant. It names no
# pressing, so a row built on it carries the album title alone -- and only
# when it IS the product's sole variant: on a multi-variant product the
# placeholder is malformed data, and a blank title is never a pressing.
# Either would otherwise share the bare album title and the product URL, and
# so the item_key, with every sibling built the same way.
_PLACEHOLDER_VARIANT = "default title"
# The variant gate is negative, because a variant title here is the
# pressing's colour and usually nothing else ("Clear w Pink Cornetto /1000",
# "Scrambled Eggs /200"): on a vinyl-typed product a variant is a record
# unless its title names another medium. Check order follows iodinerecords.py
# and is load-bearing rather than stylistic -- a vinyl word decides first, so
# "AB Dark Blue / CD Light Blue 2xLP" stays a record. That is a live variant,
# and its "CD" is the second disc's two sides, not a compact disc; a gate
# that tested for another medium first would drop it.
_VINYL_WORDS = (r"\d*[x×]?lps?", r"vinyls?", r"picture\s+discs?")
_NON_VINYL_MEDIA_WORDS = (
    r"\d*[x×]?cds?", r"cassettes?", r"tapes?", r"digital", r"digipa[kc]k?s?",
    r"\d*[x×]?dvds?", r"blu-?rays?",
)
_INCH = r'(?<![a-z0-9])(?:\d+[x×])?\d{1,2}\s*-?\s*(?:"|”|″|inch(?:es)?\b)'


def _alternation(*tuples):
    return "|".join(word for group in tuples for word in group)


_VINYL_WORD_RE = re.compile(r"(?<![a-z])(?:%s)\b" % _alternation(_VINYL_WORDS), re.IGNORECASE)
_NON_VINYL_MEDIA_RE = re.compile(r"(?<![a-z])(?:%s)\b" % _alternation(_NON_VINYL_MEDIA_WORDS), re.IGNORECASE)
_INCH_RE = re.compile(_INCH, re.IGNORECASE)


class Crawler:
    site_name: str = "Counter Intuitive Records"
    base_url: str = "https://counterintuitiverecords.com"
    genre_summary: str = "Massachusetts emo, pop-punk and indie label — Origami Angel, Mom Jeans, Prince Daddy & The Hyena, Oso Oso, Macseal and Bears In Trees, plus a small distro."
    genre: str = "punk"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        products_seen = 0
        vinyl_typed = 0
        artist_ok = 0
        vinyl_variants_seen = 0
        identity_missing = 0
        unreadable_stock = 0
        yielded = 0
        priced = 0
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            products_seen += 1
            # Nested, not sibling tallies: only a product with a vinyl type,
            # an artist, AND a variant the gate admits could have yielded a
            # row, so only that product's identity and stock readability say
            # anything about an empty result. Tallied independently, one
            # product could satisfy each condition while none of them can
            # yield.
            if self._is_vinyl_product(product):
                vinyl_typed += 1
                if self._artist_album(product)[0]:
                    artist_ok += 1
                    vinyl = self._vinyl_variants(product)
                    if vinyl:
                        vinyl_variants_seen += 1
                        if not self._has_identity(product):
                            identity_missing += 1
                        elif not self._has_readable_stock_flag(vinyl):
                            unreadable_stock += 1
            for item in self._items(product):
                yielded += 1
                if item["price"] is not None:
                    priced += 1
                yield item
        # db.replace_stock_items() DELETEs this crawler's previous snapshot
        # before inserting, and _sync_stock only skips that call when the
        # crawl raised -- so a completed-but-empty walk is destructive where a
        # raise is inert. Each guard names a distinct way the payload can stop
        # carrying what this crawler reads. The field tallies above are taken
        # before the availability filter, so a sold-out product still counts
        # toward every one of them; `yielded` and `priced` are necessarily
        # counted after it, which is why the guards reading them are each
        # conditioned on a second tally rather than on emptiness alone -- a
        # shelf that has simply sold out is empty legitimately.
        if products_seen == 0:
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection returned no products -- renamed, removed, or markup drift")
        if vinyl_typed == 0:
            raise RuntimeError(
                f"no product in the {_COLLECTION_SLUG} collection carries a vinyl product_type "
                "-- format-taxonomy drift")
        if artist_ok == 0:
            # The artist comes from the title's dash split, with `vendor` as
            # the fallback, so this is the guard that notices both sources
            # going away at once: every record would then be skipped while
            # the type tally stayed non-zero.
            raise RuntimeError(
                f"no vinyl product in the {_COLLECTION_SLUG} collection yields an artist from its "
                "title or its vendor -- artist-source drift")
        if vinyl_variants_seen == 0:
            # The pressing is read off the variants, so this is the guard
            # that notices the store losing them or re-titling every one of
            # them as another medium. A label whose catalog is records does
            # not stop pressing them, so zero has no innocent reading.
            raise RuntimeError(
                f"no vinyl product in the {_COLLECTION_SLUG} collection has a variant that reads as "
                "a record -- format-source drift")
        if yielded and not priced:
            # Rows without the emptiness: `_price` answers None for a value
            # it cannot use, so a `price` field removed or retyped store-wide
            # re-lists the whole catalog with no prices, which is worse than
            # the snapshot it would replace. Isolated nulls stay tolerated.
            raise RuntimeError(
                f"none of the {yielded} rows from the {_COLLECTION_SLUG} collection carries a price "
                "-- price-source drift")
        if not yielded and identity_missing:
            # `handle` is identity, not display: item_key hashes the row's
            # URL, so a product missing it is skipped rather than emitted
            # under a fresh identity that would orphan the judgments and
            # saves keyed on its old one. Skipped rows leave the walk looking
            # sold out, which is why the same empty-outcome gate as the stock
            # guard below applies.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while {identity_missing} vinyl "
                "product(s) carry no handle -- identity-source drift")
        if not yielded and unreadable_stock:
            # An empty result is only trustworthy when every product that
            # could have yielded a row was readable and simply out of stock.
            # Counting unreadable products rather than readable ones is what
            # catches the partial case: one genuinely sold-out product must
            # not vouch for a catalog that has gone unreadable behind it.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while {unreadable_stock} vinyl "
                "product(s) carry no readable availability flag -- stock-source drift")

    @classmethod
    def _items(cls, product: dict) -> list:
        if not cls._is_vinyl_product(product):
            return []
        artist, album = cls._artist_album(product)
        if not artist or not album:
            return []
        if not cls._has_identity(product):
            return []
        url = f"{cls.base_url}/products/{product.get('handle', '')}"
        items = []
        for variant, descriptor in cls._vinyl_variants(product):
            # Only the literal True admits a variant: the string "false" is
            # truthy, so a falsiness test would publish a sold-out record as
            # in stock. Anything else -- False, "false", 1, None, absent -- is
            # skipped, which is also what keeps this filter and
            # _has_readable_stock_flag agreeing on what "readable" means.
            #
            # No pre-order bypass and no " (Pre-Order)" marker: the store's
            # live pre-orders report available True and are tagged with a
            # dated string ("Pre-Order 10-02-26") that changes when the date
            # does. item_key hashes the title, so a marker driven off that
            # tag would re-key the row the day it shipped.
            if variant.get("available") is not True:
                continue
            # The pressing is appended on every row that names one, not only
            # when the product has more than one variant: a CD or cassette
            # sibling being listed or delisted must not re-title the vinyl
            # rows and orphan the listings, judgments and saves keyed on the
            # old identity. The placeholder is the one exception, because it
            # names nothing, and _vinyl_variants admits it only as a product's
            # sole variant, so no sibling can share the bare title.
            items.append({
                "artist": artist,
                "title": f"{album} — {descriptor}" if descriptor else album,
                "format": "Vinyl",
                "price": cls._price(variant),
                "currency": "USD",
                "url": url,
                "cover_image_url": resolve_cover_image(product, variant),
            })
        return items

    @staticmethod
    def _is_vinyl_product(product: dict) -> bool:
        return (product.get("product_type") or "").strip().lower() in _VINYL_PRODUCT_TYPES

    @staticmethod
    def _artist_album(product: dict) -> Tuple[str, str]:
        """Split `Artist - Album` out of the product title, falling back to `vendor`.

        The title split wins wherever it parses. `vendor` is the artist's own
        name on all but one live vinyl product -- unlike the sibling Shopify
        label stores, where it is the label repeated on every row -- but it is
        only ever the *primary* artist: the store's two splits are billed
        fully in the title ("Mom Jeans / Grad Life") and carry one of the
        bands in `vendor`, so a vendor-first rule would drop the other. The
        fallback covers the store's own label compilation, whose title
        ("Counter Intuitive Presents: ...") names no artist and whose vendor
        is the label that released it.
        """
        title = " ".join((product.get("title") or "").split())
        m = _TITLE_RE.match(title)
        if m:
            artist = m.group("artist").strip()
            album = m.group("album").strip()
            if artist and album:
                return artist, album
        return " ".join((product.get("vendor") or "").split()), title

    @classmethod
    def _vinyl_variants(cls, product: dict) -> list:
        pairs = []
        # Non-mapping entries are dropped here, before anything reads them, so
        # a junk entry is an ordinary skipped row rather than an
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
            if cls._is_vinyl_variant(title):
                pairs.append((variant, title))
        return pairs

    @staticmethod
    def _is_vinyl_variant(title: str) -> bool:
        if _VINYL_WORD_RE.search(title):
            return True
        if _INCH_RE.search(title):
            return True
        return not _NON_VINYL_MEDIA_RE.search(title)

    @staticmethod
    def _has_identity(product: dict) -> bool:
        return bool((product.get("title") or "").strip()) and bool((product.get("handle") or "").strip())

    @staticmethod
    def _has_readable_stock_flag(vinyl_variants: list) -> bool:
        # every(), not any(): one readable variant does not make the product
        # readable. A product whose black pressing is a readable False and
        # whose coloured pressing carries the string "false" yields nothing,
        # and under any() would vouch for an emptiness half its own doing.
        # Scoped to the vinyl variants, because they are the only ones that
        # could have yielded: a CD variant's flag says nothing about whether
        # the record's emptiness can be trusted.
        return bool(vinyl_variants) and all(
            isinstance(v.get("available"), bool) for v, _ in vinyl_variants)

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
