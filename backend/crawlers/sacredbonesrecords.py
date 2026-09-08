import math
import re
from typing import AsyncIterator, List, Optional, Tuple
from shopify_catalog import iter_products, resolve_cover_image

# The store's own format shelf, at the URL the request named. `collections.json`
# reports a products_count larger than the shelf's published catalog -- it
# counts products not published to the online store -- so the walk's own
# exhaustion is the catalog, confirmed 2026-09-08 to return the same product
# ids at limit=250 and limit=50.
_COLLECTION_SLUG = "vinyl"
# `vendor` is the artist credit on every product in the shelf -- never blank,
# and only ever the label's own name on the products that are not one artist's
# record (a mystery grab-bag, a label compilation, the raffle skipped below).
# The product title is the album alone: no vendor prefix to strip, and the
# titles that do begin with the vendor are self-titled records (`Khanate` /
# `Khanate`), where stripping one would leave nothing.
#
# The shelf is a Shopify *product* shelf, not a format shelf, and a product
# here is a release rather than a pressing: its variants are the formats it
# was released in, so the vinyl collection carries CD, cassette, 8-track,
# Blu-Ray and digital variants of records that also came out on vinyl. The
# medium is therefore decided per variant, and the variant title is where the
# store writes it -- uniformly, whether the option axis is named `Format`,
# `Title`, `Edition`, `Variant` or `LP`.
#
# The gate is NEGATIVE: a vinyl word admits outright, then a word naming
# another medium rejects, and anything else is admitted on the collection's
# own claim. It has to be. Coloured pressings here are routinely named by
# colour alone -- `Lavender Swirl`, `Clear Pink`, `Sacred Bones Exclusive
# Black and White Galaxy`, `Blue & White Galaxy` -- and a positive-only
# regex would drop every one of them.
_VINYL_WORD_RE = re.compile(
    r'(?<![a-z])(?:\d+(?:\.\d+)?\s*[x×]\s*)?lps?\b|\bvinyls?\b'
    r'|\bpicture\s+discs?\b|\btest\s+press(?:ing)?e?s?\b|\bflexi(?:\s*discs?)?\b',
    re.IGNORECASE,
)
# The inch marker is a SEPARATE tier, consulted after the merch words rather
# than beside the explicit format words, because a measurement is not a format
# claim: as one alternative of _VINYL_WORD_RE a bare `12"` admitted outright
# and published `12" x 12" Poster` as a record. spv.py orders its own gate
# this way for the same reason, and this is the shape it settled on.
#
# The hyphen is optional because `12-INCH` is established notation in this
# repo (asianmanrecords.py's _VINYL_TYPES), and `\s*` alone missed it.
_INCH_RE = re.compile(
    r'(?<![a-z0-9])(?:\d+\s*[x×]\s*)?\d{1,2}[\s-]*(?:"|”|″|inch(?:es)?\b)',
    re.IGNORECASE,
)
# Not a record in any pressing: what the shelf stocks beside its records as
# extra variants of a record's own product (`Limited Edition hand numbered
# posters ...`, `Sacred Bones exclusive Boris Pedal`). Kept apart from the
# media words below because the two sit on opposite sides of the inch marker
# -- an inch beside a poster is that poster's size, while an inch beside a CD
# is a record bundled with one.
_MERCH_RE = re.compile(r'\bposters?\b|\bprints?\b|\bpedals?\b', re.IGNORECASE)
# `cd`/`cs` carry the same digit-glued-count lookbehind as the vinyl words,
# and for the same reason: the shelf spells its box sets `Limited Edition
# 3xCD Box Set` and `2xCS Box Set`, and \b matches nothing between `3` and
# `CD`, so a plain \bcds?\b reads neither as a CD.
#
# Both this list and _MERCH_RE are only what the shelf forced rather than a
# general vocabulary: a word listed in either rejects a pressing that happens
# to mention it, so the cost of guessing wrong is losing a record. An explicit
# vinyl word still wins over both, which is what keeps the pressings that come
# *with* one -- `Limited Edition Smoke Vinyl LP w/ Print Set`.
_NON_VINYL_MEDIA_RE = re.compile(
    r'(?<![a-z])(?:\d+\s*[x×]\s*)?(?:cds?|css?)\b'
    r'|\bcassettes?\b|\btapes?\b|\b8\s*-?\s*tracks?\b'
    r'|\bdvds?\b|\bblu-?\s*rays?\b'
    r'|\bdigital\b|\bmp3s?\b|\bwavs?\b|\baiffs?\b|\bflacs?\b|\bdownloads?\b',
    re.IGNORECASE,
)
# A raffle entry is not a pressing and its price is not a record's price: the
# store's charity raffle lists one $10 "variant" per prize, several of which
# name a record the store does not sell at $10. Read from the tags rather
# than the title because that is where the store says what the product is;
# the prize names themselves say nothing (`Society 25`).
_SKIP_TAGS = frozenset({"raffle", "donation"})
# A split release's billing names every act on the record, but a stock row's
# artist has to be the FIRST-billed one to be matchable: discogs.parse_release
# stores `artists[0]` and nothing else, and db._library_release_match_sql
# compares artists with exact case-folded equality (only the title gets the
# exact-or-prefix treatment). So a joined billing sits permanently outside the
# Store tab's Collection and Wantlist filters.
#
# Whitespace is required on at least one side of the slash, the repo's standard
# fix for this bug class: an act whose own name contains a slash (AC/DC) must
# not be clipped to its first half.
#
# `&`, `,` and `and` are deliberately NOT split, though the shelf joins acts
# with all three (`Uniform & The Body`, `John Carpenter, Cody Carpenter, and
# Daniel Davies`). Each is also an ordinary part of a single act's own name --
# `Mandy, Indiana` is live on this very shelf -- and nothing in the payload
# separates the two readings. Reducing would silently break the match for such
# a name, which is the failure with no signal to recover from; leaving it
# joined costs a match the store's own spelling was unlikely to win anyway.
# Same call, on the same grounds, as translationloss.py's `_artist`.
_BILLING_SPLIT_RE = re.compile(r'(?:\s+/\s*|\s*/\s+)')
# Shopify's placeholder for a product with exactly one variant. It names no
# pressing, so a row built on it carries the product title alone -- and only
# when it IS the sole variant the store sent, which is equally why a blank
# title is treated as the same thing. On a multi-variant product neither is a
# pressing, and a row built on either would share its title and product URL,
# and so its item_key, with every sibling built the same way.
_PLACEHOLDER_VARIANT = "default title"


class Crawler:
    site_name: str = "Sacred Bones Records"
    base_url: str = "https://www.sacredbonesrecords.com"
    genre_summary: str = "Brooklyn label for post-punk, psych and experimental records, and for the horror and arthouse soundtracks of John Carpenter and David Lynch, alongside a distro of kindred labels."
    genre: str = "rock"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        products_seen = 0
        artist_missing = 0
        identity_missing = 0
        unreadable_stock = 0
        unreadable_variants = 0
        yielded = 0
        priced = 0
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            products_seen += 1
            pressings, unreadable = self._read_variants(product)
            unreadable_variants += unreadable
            # One bracket for every way a product that WOULD have yielded a
            # row failed to, counted once per product against the first
            # reason that applies. Gating the whole bracket on `pressings`
            # is what keeps a CD-only product -- or a skipped one, which
            # _read_variants answers empty for -- from tallying toward
            # anything: it would never have yielded a row whatever its
            # vendor said, so it can neither raise a false alarm nor vouch
            # for the shelf. The artist question in particular has to be
            # asked here rather than over every product walked: a tally
            # taken outside the gate is satisfied by the raffle's own
            # vendor while every record on the shelf has lost its, and the
            # walk then completes empty having passed every guard.
            if pressings:
                if not self._artist(product):
                    artist_missing += 1
                elif not self._has_identity(product):
                    identity_missing += 1
                elif not self._has_readable_stock_flag(pressings):
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
        # stop carrying what this crawler reads. The identity and stock
        # tallies are taken before the availability filter, so a sold-out
        # product still counts toward both; `yielded` and `priced` are
        # necessarily counted after it, which is why the guards reading them
        # are each conditioned on a second tally rather than on emptiness
        # alone -- a shelf that has simply sold out is empty legitimately.
        #
        # There is deliberately no format-gate guard: the gate is negative,
        # so no positive signal's disappearance can silently empty the walk.
        if products_seen == 0:
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection returned no products -- renamed, removed, or markup drift")
        if yielded and not priced:
            # Rows without the emptiness: `_price` answers None for a value
            # it cannot use, so a `price` field removed or retyped store-wide
            # re-lists the whole catalog with no prices, which is worse than
            # the snapshot it would replace. Isolated nulls stay tolerated.
            raise RuntimeError(
                f"none of the {yielded} rows from the {_COLLECTION_SLUG} collection carries a "
                "price -- price-source drift")
        if not yielded and artist_missing:
            # `vendor` is the artist, with no fallback to the title, so a
            # product that loses it is skipped rather than credited from
            # something else -- and skipping leaves the walk looking sold
            # out. Same empty-outcome gate as the two guards below, and for
            # the same reason: an isolated vendor-less product among real
            # rows is an ordinary skipped row.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while "
                f"{artist_missing} record(s) carry no vendor -- artist-source drift")
        if not yielded and unreadable_variants:
            # Everything about a product's variants this crawler could not
            # interpret: a non-mapping entry, a non-string title, one naming
            # no pressing beside a sibling, or a `variants` collection that
            # is absent, empty or retyped. The message names both shapes
            # because the count mixes them, and they point at different
            # sources. Those discards are otherwise invisible, and invisible
            # is destructive -- Shopify dropping variant titles store-wide
            # leaves every multi-variant product with nothing to build a row
            # from, and the bracket above never fires because such a product
            # has no admitted pressings to gate on.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while "
                f"{unreadable_variants} variant(s) or variant collection(s) could not be "
                "interpreted -- variant-identity-source drift")
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
    def _items(cls, product: dict) -> List[dict]:
        artist = cls._artist(product)
        if not artist:
            return []
        if not cls._has_identity(product):
            return []
        title = " ".join((product.get("title") or "").split())
        url = f"{cls.base_url}/products/{(product.get('handle') or '').strip()}"
        items = []
        for variant, pressing in cls._read_variants(product)[0]:
            # Only the literal True admits a variant: the string "false" is
            # truthy, so a falsiness test would publish a sold-out record as
            # in stock. Anything else -- False, "false", 1, None, absent --
            # is skipped, which is also what keeps this filter and
            # _has_readable_stock_flag agreeing on what "readable" means.
            #
            # No pre-order bypass and no " (Pre-Order)" marker: the store's
            # live pre-orders report available True, so an unavailable
            # variant on one is a closed allocation rather than a pre-order
            # to admit -- confirmed live on a tagged pre-order whose wax-seal
            # edition was already gone while its siblings were still selling.
            # A marker would also re-title every row when the tag dropped,
            # orphaning the saves and judgments keyed on the old item_key.
            if variant.get("available") is not True:
                continue
            # The pressing is appended on every row that names one, not only
            # when the product has more than one variant: a sibling being
            # listed or delisted must not re-title the rows and orphan what
            # hangs off the old identity. It goes after the album so
            # db._library_release_match_sql's exact-or-prefix-with-space test
            # still matches a library title -- "Belaya Polosa — Black LP"
            # prefix-matches a catalog "Belaya Polosa".
            items.append({
                "artist": artist,
                "title": f"{title} — {pressing}" if pressing else title,
                "format": "Vinyl",
                "price": cls._price(variant),
                "currency": "USD",
                "url": url,
                "cover_image_url": resolve_cover_image(product, variant),
            })
        return items

    @staticmethod
    def _artist(product: dict) -> str:
        vendor = " ".join((product.get("vendor") or "").split())
        return _BILLING_SPLIT_RE.split(vendor, 1)[0].strip() or vendor

    @classmethod
    def _read_variants(cls, product: dict) -> Tuple[List[Tuple[dict, str]], int]:
        """(pressings, unreadable) for one product.

        `pressings` is the (variant, pressing name) pairs a row can be built
        from. `unreadable` counts the entries discarded because this crawler
        could not interpret them at all -- a non-mapping entry, or one naming
        no pressing beside a sibling. That count exists because those discards
        are otherwise invisible, and invisible is destructive: a product all
        of whose variants are discarded has no admitted pressings, so it
        reaches none of the other tallies and an empty walk looks legitimate.

        A skipped product answers empty on both counts. Its variants are not
        pressings the crawler failed to read, they are entries it was never
        meant to read, so counting them would raise on a shelf that is
        working exactly as designed.
        """
        if cls._is_skipped(product):
            return [], 0
        raw = product.get("variants")
        if not isinstance(raw, (list, tuple)) or not raw:
            # Absent, emptied or retyped. A published Shopify product always
            # carries at least one variant, so none of those is a product
            # with nothing for sale -- it is a payload this crawler cannot
            # read, and reading it as the former is what would let the
            # collection disappear store-wide in silence.
            return [], 1
        # Non-mapping entries are dropped here, before anything reads them,
        # so a junk entry is an ordinary skipped row rather than an
        # AttributeError from inside the yield loop.
        variants = [v for v in raw if isinstance(v, dict)]
        unreadable = len(raw) - len(variants)
        pairs = []
        for variant in variants:
            title = variant.get("title")
            # A truthy non-string would reach .split() through `or ""` and
            # raise, aborting the whole source over one malformed variant --
            # the opposite of the discard-and-keep-going rule every other
            # unreadable entry follows. Absent and None stay nameless rather
            # than unreadable, which is the documented placeholder case.
            if title is not None and not isinstance(title, str):
                unreadable += 1
                continue
            name = " ".join((title or "").split())
            if not name or name.lower() == _PLACEHOLDER_VARIANT:
                # Sole-variant status comes from the payload as sent, not
                # from what survived the mapping filter: a sibling mangled
                # into a non-mapping entry is still a sibling, and reading
                # `variants` here would let the placeholder emit a bare-title
                # row sharing its title and URL -- and so its item_key --
                # with whatever that entry was.
                if len(raw) == 1:
                    pairs.append((variant, ""))
                else:
                    unreadable += 1
                continue
            if not cls._is_vinyl(name):
                continue
            pairs.append((variant, name))
        return pairs, unreadable

    @staticmethod
    def _is_skipped(product: dict) -> bool:
        return any((t or "").strip().lower() in _SKIP_TAGS for t in product.get("tags") or [])

    @staticmethod
    def _is_vinyl(pressing: str) -> bool:
        """Four tiers, and the order between the middle two is the whole point.

        An explicit format word admits outright, so a record bundled with
        merch is still a record. Merch is then checked *before* the inch
        marker, because a measurement is not a format claim: consulted first,
        a bare `12"` published `12" x 12" Poster` as vinyl. Media stays
        *after* the marker, because there the pairing reads the other way --
        `10 INCH + CD` is a record bundled with a CD. Anything left is
        admitted on the collection's own claim.
        """
        if _VINYL_WORD_RE.search(pressing):
            return True
        if _MERCH_RE.search(pressing):
            return False
        if _INCH_RE.search(pressing):
            return True
        return not _NON_VINYL_MEDIA_RE.search(pressing)

    @staticmethod
    def _has_identity(product: dict) -> bool:
        return bool((product.get("title") or "").strip()) and bool((product.get("handle") or "").strip())

    @staticmethod
    def _has_readable_stock_flag(pressings: List[Tuple[dict, str]]) -> bool:
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
