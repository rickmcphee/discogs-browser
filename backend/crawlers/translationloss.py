import math
import re
from typing import AsyncIterator, Optional, Tuple

from shopify_catalog import iter_products, resolve_cover_image

# The shelf the request named, and a complete one: it holds exactly the
# vinyl-typed products of the store's `all` collection (every 12", 2x12" and
# 10" in it, and nothing else), and confirmed live that no product outside it
# names a record in its title or any variant. Walking `all` instead would add
# only the CD, cassette, T-shirt and bundle rows the type gate below drops.
_COLLECTION_SLUG = "vinyl"
# product_type on this store is the format itself, written as a disc count
# bound to a disc size -- 12", 2x12", 10" live, with 7" and 2x10" the obvious
# neighbours a label like this presses next. So the gate reads the *shape* of a
# vinyl format rather than enumerating today's three literals: a 7" the store
# adds tomorrow is admitted with no edit, while CD, 2xCD, CD/DVD, Cassette,
# T-Shirt and Kit -- every other live type -- stay out because none of them
# carries an inch marker or a vinyl word.
#
# Gating at all, on a shelf the store curates as vinyl, follows hammerheart.py:
# two CDs sit in that store's own vinyl collection mistyped as 12", so a
# shelf's curation is not a format guarantee.
_VINYL_TYPE_WORDS = (r"\d*[x×]?lps?", r"vinyls?", r"picture\s+discs?")
# Counted prefix allowed *inside* the marker rather than left to the lookbehind:
# "2x12"" binds its disc count straight onto the size, and a bare
# (?<![a-z0-9])\d{1,2}" would refuse to start at the 12 because an x precedes it.
_INCH = r'(?<![a-z0-9])(?:\d+[x×])?\d{1,2}\s*-?\s*(?:"|”|″|inch(?:es)?\b)'
_VINYL_TYPE_WORD_RE = re.compile(
    r"(?<![a-z])(?:%s)\b" % "|".join(_VINYL_TYPE_WORDS), re.IGNORECASE)
_INCH_RE = re.compile(_INCH, re.IGNORECASE)
# Layer two, read off the title. `product_type` cannot see a *mistyped*
# product, and that is exactly what the hammerheart.py precedent above is
# about: the two CDs on that store's vinyl shelf are typed 12", and it rejects
# them by reading their titles. Without this layer such a product satisfies
# _INCH_RE and publishes with format="Vinyl" -- so the gate would not cover the
# case cited as its own reason for existing.
#
# Same shape as hammerheart's, and the same vocabulary: the filter fires only
# when a non-vinyl word is present, and a vinyl word overrides it so a genuine
# LP+CD bundle survives. The set is kept to that proven one rather than widened
# with "digital" and the like, because a title here often names no format at
# all ("Crimea", "S/T"), so a word that doubles as an album word would drop a
# real record with nothing to override it. Live, no title on this shelf carries
# any of these words, so nothing is dropped today.
_NON_VINYL_TITLE_RE = re.compile(
    r"\b\d*[x×]?(?:cds?|dvds?)\b|\bdigipa[kc]k?s?\b|\bcassettes?\b|\btapes?\b|\bblu-?rays?\b",
    re.IGNORECASE)
# Shopify's placeholder for a product created with no options. It names no
# pressing, so a row built on it carries the album title alone -- and only when
# it IS the product's sole variant: on a multi-variant product the placeholder
# is malformed data, and two rows built that way would share the bare title and
# the product URL, and so the item_key. No live variant carries it (every one
# of them names a colour), so this is defensive rather than observed.
_PLACEHOLDER_VARIANT = "default title"


class Crawler:
    site_name: str = "Translation Loss Records"
    base_url: str = "https://translationloss.com"
    genre_summary: str = "Independent label for doom, sludge, post-metal and experimental heavy music — Rosetta, We Lost The Sea, Giant Squid, Mouth Of The Architect, Grayceon and Wake."
    genre: str = "metal"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        products_seen = 0
        vinyl_typed = 0
        artist_ok = 0
        pressings_seen = 0
        identity_missing = 0
        unreadable_stock = 0
        yielded = 0
        priced = 0
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            products_seen += 1
            # Nested, not sibling tallies: only a product with a vinyl type and
            # an artist could have yielded a row, so only that product's
            # identity and readability say anything about an empty result.
            # Tallied independently, one product could satisfy each condition
            # while none of them can yield.
            #
            # The readability tally sits OUTSIDE `if pressings`, unlike the
            # rest: a product whose variants are *all* unreadable has an empty
            # `pressings` and would slip past every guard, because one other
            # product with a readable sold-out pressing is enough to satisfy
            # pressings_seen on its behalf.
            if self._is_vinyl_product(product):
                vinyl_typed += 1
                if self._artist(product):
                    artist_ok += 1
                    pressings, unreadable_variants = self._read_variants(product)
                    if pressings:
                        pressings_seen += 1
                    if not self._has_identity(product):
                        identity_missing += 1
                    elif unreadable_variants or (
                            pressings and not self._has_readable_stock_flag(pressings)):
                        unreadable_stock += 1
            for item in self._items(product):
                yielded += 1
                if item["price"] is not None:
                    priced += 1
                yield item
        # db.replace_stock_items() DELETEs this crawler's previous snapshot
        # before inserting, and _sync_stock skips that call only when the crawl
        # raised -- so a completed-but-empty walk is destructive where a raise
        # is inert. Each guard names a distinct way the payload can stop
        # carrying what this crawler reads. The tallies above are all taken
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
            # The artist is `vendor` and nothing else on this store: titles are
            # album-only, so there is no second source to fall back to and no
            # second source whose survival could mask this one going away.
            raise RuntimeError(
                f"no vinyl product in the {_COLLECTION_SLUG} collection carries a vendor "
                "-- artist-source drift")
        if pressings_seen == 0:
            # The pressing is read off the variants, so this is the guard that
            # notices the store losing them, blanking their titles, or leaving
            # every product with a bare placeholder beside a sibling. A label
            # whose catalog is records does not stop pressing them, so zero has
            # no innocent reading.
            raise RuntimeError(
                f"no vinyl product in the {_COLLECTION_SLUG} collection has a variant naming a "
                "pressing -- pressing-source drift")
        if yielded and not priced:
            # Rows without the emptiness: `_price` answers None for a value it
            # cannot use, so a `price` field removed or retyped store-wide
            # re-lists the whole catalog with no prices, which is worse than the
            # snapshot it would replace. Isolated nulls stay tolerated.
            raise RuntimeError(
                f"none of the {yielded} rows from the {_COLLECTION_SLUG} collection carries a price "
                "-- price-source drift")
        if not yielded and identity_missing:
            # Title and handle are identity, not display: item_key hashes the
            # row's title and URL, so a product missing either is skipped rather
            # than emitted under a fresh identity that would orphan the
            # judgments and saves keyed on its old one. Both are named because
            # _has_identity reads both -- the artist comes from `vendor`, so a
            # blank title reaches this tally with the artist intact, and naming
            # only the handle would point at the wrong field. Skipped rows leave
            # the walk looking sold out, which is why the same empty-outcome
            # gate as the stock guard below applies.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while {identity_missing} vinyl "
                "product(s) carry no title or no handle -- identity-source drift")
        if not yielded and unreadable_stock:
            # An empty result is only trustworthy when every product that could
            # have yielded a row was readable and simply out of stock. Counting
            # unreadable products rather than readable ones is what catches the
            # partial case: one genuinely sold-out product must not vouch for a
            # catalog that has gone unreadable behind it.
            #
            # "Cannot read" covers both ways a product can look sold out
            # without being sold out: a variant whose `available` is not a
            # boolean, and a variant discarded before availability was ever
            # consulted. The second is the one with no outward sign -- a
            # discarded variant leaves a product that looks perfectly readable.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while {unreadable_stock} vinyl "
                "product(s) carry a variant this crawler cannot read -- stock-source drift")

    @classmethod
    def _items(cls, product: dict) -> list:
        if not cls._is_vinyl_product(product):
            return []
        artist = cls._artist(product)
        if not artist:
            return []
        if not cls._has_identity(product):
            return []
        # Kept exactly as the store writes it, format token and all. The tokens
        # sit after the album name -- "Fragments LP", "Having 2xLP (20th
        # Anniversary Edition)", "Celestial Rot (Vinyl)" -- and
        # db._library_release_match_sql matches a catalog title exactly or as a
        # prefix followed by a space, so trailing text never costs a library
        # match. Stripping would buy nothing and risk an album genuinely named
        # "LP" or "Vinyl", while the token itself is real information: 2xLP and
        # LP are different pressings of the same record.
        album = " ".join((product.get("title") or "").split())
        url = f"{cls.base_url}/products/{product.get('handle', '')}"
        items = []
        for variant, descriptor in cls._pressing_variants(product):
            # Only the literal True admits a variant: the string "false" is
            # truthy, so a falsiness test would publish a sold-out record as in
            # stock. Anything else -- False, "false", 1, None, absent -- is
            # skipped, which is also what keeps this filter and
            # _has_readable_stock_flag agreeing on what "readable" means.
            #
            # No pre-order bypass and no " (Pre-Order)" marker, because this
            # store gives neither anything to read: its pre-order language lives
            # only in body_html, with no tag and no title marker, and no live
            # product is flagged as one.
            if variant.get("available") is not True:
                continue
            # The pressing is appended on every row that names one, not only
            # when the product has more than one variant: a sibling colour being
            # listed or delisted must not re-title the rows that remain and
            # orphan the listings, judgments and saves keyed on the old
            # identity. The placeholder is the one exception, because it names
            # nothing, and _pressing_variants admits it only as a product's sole
            # variant, so no sibling can share the bare title.
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

    @classmethod
    def _is_vinyl_product(cls, product: dict) -> bool:
        if not cls._names_vinyl(product.get("product_type") or ""):
            return False
        # The title layer only ever removes: a product whose type reads as
        # vinyl but whose title names another medium with no vinyl word to
        # override it. A title naming no format at all -- the common case here
        # -- never reaches the override, because the filter fires only on a
        # non-vinyl match.
        title = product.get("title") or ""
        return not (_NON_VINYL_TITLE_RE.search(title) and not cls._names_vinyl(title))

    @staticmethod
    def _names_vinyl(text: str) -> bool:
        return bool(_INCH_RE.search(text) or _VINYL_TYPE_WORD_RE.search(text))

    @staticmethod
    def _artist(product: dict) -> str:
        """The artist is `vendor`, with no fallback to the title.

        Unlike the sibling label stores, `vendor` here holds the act's own name
        on every live vinyl product rather than the label's, and the title is
        the album alone -- so there is no `Artist - Album` billing to split. The
        one live title that does contain a dash separator is a split release
        ("Coltsblood - UN Split LP") whose vendor already carries the same
        billing, so splitting it would read a worse copy of what vendor says.

        A multi-act vendor is left joined. Four live ones join two acts with
        " & " (a split, two collaborations and a duo credit), and reducing them
        to the first-billed act would match db._library_release_match_sql's
        exact artist equality against Discogs' `artists[0]` -- but "&" is also
        an ordinary part of a single artist's own name, and nothing in the
        payload separates the two readings. Reducing would silently break the
        match for such a name, which is the failure with no signal to recover
        from; leaving it joined costs a match the store's own spelling was
        unlikely to win anyway.
        """
        return " ".join((product.get("vendor") or "").split())

    @classmethod
    def _read_variants(cls, product: dict) -> Tuple[list, int]:
        """(pressings, unreadable) for one product.

        `pressings` is the (variant, descriptor) pairs a row can be built from.
        `unreadable` counts the entries discarded because this crawler could
        not interpret them at all -- a non-mapping entry, a blank title, or the
        placeholder beside a sibling.

        The count exists because those discards are otherwise invisible, and
        invisible is destructive here: an *available* variant with a blank
        title sits beside a readable sold-out one, and the product then has a
        non-empty `pressings`, a readable stock flag, and no row. Every guard
        is satisfied while purchasable stock goes unpublished, and a catalog
        otherwise sold out completes empty -- which is what makes
        replace_stock_items() wipe the previous snapshot. Reported rather than
        raised on the spot, so an isolated bad variant among real rows stays an
        ordinary skipped row.

        No format filtering here, deliberately: `product_type` and the title
        layer already scope the format on this store, and every live variant on
        a vinyl-typed product names a pressing colour and nothing else. A
        medium-word gate would have no work to do and would misread a colour
        that happens to name one -- the trap "Pink Tape" sprang on a sibling
        store.
        """
        raw = product.get("variants") or []
        # Non-mapping entries are dropped here, before anything reads them, so a
        # junk entry is an ordinary skipped row rather than an AttributeError
        # from inside the yield loop.
        variants = [v for v in raw if isinstance(v, dict)]
        unreadable = len(raw) - len(variants)
        pairs = []
        for variant in variants:
            title = " ".join((variant.get("title") or "").split())
            if not title:
                unreadable += 1
                continue
            if title.lower() == _PLACEHOLDER_VARIANT:
                if len(variants) == 1:
                    pairs.append((variant, ""))
                else:
                    unreadable += 1
                continue
            pairs.append((variant, title))
        return pairs, unreadable

    @classmethod
    def _pressing_variants(cls, product: dict) -> list:
        return cls._read_variants(product)[0]

    @staticmethod
    def _has_identity(product: dict) -> bool:
        return bool((product.get("title") or "").strip()) and bool((product.get("handle") or "").strip())

    @staticmethod
    def _has_readable_stock_flag(pressings: list) -> bool:
        # all(), not any(): one readable variant does not make the product
        # readable. A product whose black pressing is a readable False and whose
        # coloured pressing carries the string "false" yields nothing, and under
        # any() would vouch for an emptiness half its own doing.
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
