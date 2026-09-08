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
    r'|\bpicture\s+discs?\b|\btest\s+press(?:ing)?e?s?\b|\bflexi(?:\s*discs?)?\b'
    r'|(?<![a-z0-9])(?:\d+\s*[x×]\s*)?\d{1,2}\s*(?:"|”|″|inch(?:es)?\b)',
    re.IGNORECASE,
)
# `cd`/`cs` carry the same digit-glued-count lookbehind as the vinyl words,
# and for the same reason: the shelf spells its box sets `Limited Edition
# 3xCD Box Set` and `2xCS Box Set`, and \b matches nothing between `3` and
# `CD`, so a plain \bcds?\b reads neither as a CD.
#
# The merch words are the three the shelf actually stocks beside its records
# (`Limited Edition hand numbered posters ...`, `Sacred Bones exclusive Boris
# Pedal`) rather than a general merch vocabulary: a word listed here rejects
# a pressing that happens to mention it, so the cost of guessing wrong is
# losing a record. A vinyl word still wins, which is what keeps the pressings
# that come *with* one -- `Limited Edition Smoke Vinyl LP w/ Print Set`.
_NON_VINYL_MEDIA_RE = re.compile(
    r'(?<![a-z])(?:\d+\s*[x×]\s*)?(?:cds?|css?)\b'
    r'|\bcassettes?\b|\btapes?\b|\b8\s*-?\s*tracks?\b'
    r'|\bdvds?\b|\bblu-?\s*rays?\b'
    r'|\bdigital\b|\bmp3s?\b|\bwavs?\b|\baiffs?\b|\bflacs?\b|\bdownloads?\b'
    r'|\bposters?\b|\bprints?\b|\bpedals?\b',
    re.IGNORECASE,
)
# A raffle entry is not a pressing and its price is not a record's price: the
# store's charity raffle lists one $10 "variant" per prize, several of which
# name a record the store does not sell at $10. Read from the tags rather
# than the title because that is where the store says what the product is;
# the prize names themselves say nothing (`Society 25`).
_SKIP_TAGS = frozenset({"raffle", "donation"})
# Shopify's placeholder for a product with exactly one variant. It names no
# pressing, so a row built on it carries the product title alone -- and only
# when it IS the sole variant, which is equally why a blank title is treated
# as the same thing. On a multi-variant product neither is a pressing, and a
# row built on either would share its title and product URL, and so its
# item_key, with every sibling built the same way.
_PLACEHOLDER_VARIANT = "default title"


class Crawler:
    site_name: str = "Sacred Bones Records"
    base_url: str = "https://www.sacredbonesrecords.com"
    genre_summary: str = "Brooklyn label for post-punk, psych and experimental records, and for the horror and arthouse soundtracks of John Carpenter and David Lynch, alongside a distro of kindred labels."
    genre: str = "rock"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        products_seen = 0
        credited = 0
        identity_missing = 0
        unreadable_stock = 0
        yielded = 0
        priced = 0
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            products_seen += 1
            # Tallied over every product walked, outside the format gate and
            # outside the raffle skip, because it answers a question about
            # the payload rather than about this shelf's contents: does
            # `vendor` still carry a credit at all. Tallied inside the gate,
            # a shelf that legitimately filled up with CDs would raise
            # "artist-source drift" while every vendor was perfectly readable.
            artist = self._artist(product)
            if artist:
                credited += 1
            pressings = self._pressings(product)
            if artist and pressings:
                if not self._has_identity(product):
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
        if credited == 0:
            # `vendor` is the artist, with no fallback to the title, so this
            # is the guard that notices the store emptying the field: every
            # product would be skipped while the walk still completed. Not
            # gated on having yielded nothing, because a store-wide blank
            # vendor yields nothing by construction.
            raise RuntimeError(
                f"no product in the {_COLLECTION_SLUG} collection carries a vendor -- artist-source drift")
        if yielded and not priced:
            # Rows without the emptiness: `_price` answers None for a value
            # it cannot use, so a `price` field removed or retyped store-wide
            # re-lists the whole catalog with no prices, which is worse than
            # the snapshot it would replace. Isolated nulls stay tolerated.
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
    def _items(cls, product: dict) -> List[dict]:
        artist = cls._artist(product)
        if not artist:
            return []
        if not cls._has_identity(product):
            return []
        title = " ".join((product.get("title") or "").split())
        url = f"{cls.base_url}/products/{(product.get('handle') or '').strip()}"
        items = []
        for variant, pressing in cls._pressings(product):
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
        return " ".join((product.get("vendor") or "").split())

    @classmethod
    def _pressings(cls, product: dict) -> List[Tuple[dict, str]]:
        """(variant, pressing name) for each variant this crawler reads as vinyl."""
        if cls._is_skipped(product):
            return []
        # Non-mapping entries are dropped here, before anything reads them,
        # so a junk entry is an ordinary skipped row rather than an
        # AttributeError from inside the yield loop.
        variants = [v for v in product.get("variants") or [] if isinstance(v, dict)]
        pairs = []
        for variant in variants:
            name = " ".join((variant.get("title") or "").split())
            if not name or name.lower() == _PLACEHOLDER_VARIANT:
                if len(variants) == 1:
                    pairs.append((variant, ""))
                continue
            if not cls._is_vinyl(name):
                continue
            pairs.append((variant, name))
        return pairs

    @staticmethod
    def _is_skipped(product: dict) -> bool:
        return any((t or "").strip().lower() in _SKIP_TAGS for t in product.get("tags") or [])

    @staticmethod
    def _is_vinyl(pressing: str) -> bool:
        if _VINYL_WORD_RE.search(pressing):
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
