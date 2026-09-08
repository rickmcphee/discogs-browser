import math
import re
from typing import AsyncIterator, Optional, Tuple
from shopify_catalog import iter_products, has_tag, resolve_cover_image

# Shopify's built-in all-products collection, at the URL the request named.
# The store publishes no vinyl or format collection at all -- its own
# collections are one per artist, plus `merch`, `sale`, `best-sellers`,
# `frontpage`, `upcoming-releases` and two hand-curated "all" variants -- so
# there is no narrower shelf to walk. The walk's own exhaustion is the
# catalog: it returns the same product ids at limit=250 and limit=50, and
# that count agrees exactly with `meta.json`'s published_products_count.
# `collections.json` disagrees, reporting a smaller `products_count` for this
# very collection; the endpoint that answers with products wins.
_COLLECTION_SLUG = "all"
# The store's own tag for the things it sells that are not records: a cap, a
# tote bag, a football jersey and four T-shirts. It agrees exactly with the
# `merch` collection, in both directions.
_MERCH_TAG = "Merch"
# `Artist - Album`, on every product in the store that is a record bar two.
# \s+-\s+ rather than a bare dash, so a hyphen inside either half
# (`Four-Calendar Cafe`, `Yellow/White Swirl`) is not a separator; the first
# occurrence splits, though no live title carries a second.
_SPLIT_RE = re.compile(r"\s+-\s+")
# The format lives in the variant title, which is the store's `Format` option:
# `Vinyl`, `Black Vinyl`, `140g EcoMix Vinyl`, `10" Signed Vinyl`,
# `Vinyl (BELLA1090V)` against `CD`, `CD (BELLA950CD)` and `Cassette`. The
# gate below is negative, so this vocabulary only has to admit a descriptor
# that ALSO names another medium -- a hypothetical `Double Vinyl + Bonus CD`.
# It carries `lps?` and an inch marker anyway, matching the sibling Shopify
# crawlers, because they are what the store would write if it ever named a
# record without the word `Vinyl`. The lookbehind is what keeps the `lp` in
# `Help` out, and the disc-count prefix admits `2xLP`, where no word boundary
# separates the count from the noun.
_VINYL_WORD_RE = re.compile(
    r"(?<![a-z])(?:\d+(?:\.\d+)?\s*[x×]\s*)?lps?\b|\bvinyls?\b"
    r"|(?<![a-z0-9])(?:\d+\s*[x×]\s*)?\d{1,2}\s*(?:\"|”|″|inch(?:es)?\b)",
    re.IGNORECASE,
)
# `casse+tte` rather than `cassette`: the store spells one of its live
# variants `Casseette`, on a product whose other variants are records
# (`Father John Misty - Mahashmashana`), so a strict spelling publishes a
# cassette at a cassette's price under this crawler's `Vinyl` format. The
# disc media carry the same optional disc-count prefix the record words do,
# since `\bcds?\b` cannot see the `CD` in `2xCD`.
_OTHER_MEDIA_RE = re.compile(
    r"(?<![a-z])(?:\d+\s*[x×]\s*)?(?:cds?|casse+ttes?|tapes?|dvds?|blu-?\s?rays?)\b"
    r"|\bdigital\b",
    re.IGNORECASE,
)
# Garments and bags -- the store's own merch categories, read off its `merch`
# collection. Deliberately NOT the small extras the store packages with a
# record (`+ Zine`, `+ Signed Print`, `+ Signed Postcard`, `+ Signed
# Polaroid`, `+ Embroidered Patch`, `+ Comic`, `+ Human Assholes Trading
# Cards`): each of those is priced at or below its product's plain pressing,
# so it is a record with an extra and its price is a record's. The shirt
# combos are the opposite -- £40.99 against a £19.99 plain pressing -- and
# they are what this rejects.
_MERCH_PATTERN = (
    r"\b(?:t-?\s?)?shirts?\b|\btees?\b|\bhoodies?\b|\bsweatshirts?\b"
    r"|\bcrewnecks?\b|\blongsleeves?\b|\btank\s+tops?\b|\bjerseys?\b"
    r"|\bcaps?\b|\bhats?\b|\btotes?\b|\bbags?\b"
)
_MERCH_RE = re.compile(_MERCH_PATTERN, re.IGNORECASE)
# Every live combo of a record and a garment says `Bundle` as well as naming
# the garment, so the two rules agree on the whole catalog and each is the
# other's backstop. This one also covers a plausible `Vinyl Bundle` of
# several records, whose price is no single record's.
_BUNDLE_RE = re.compile(r"\bbundles?\b", re.IGNORECASE)
# A descriptor that is nothing but a garment size. The store's `Size` option
# is the only place these appear, and only on merch -- but merch is kept out
# by its tag and by a title that does not parse, and the store types its
# products by hand (it leaves four of its records untyped), so a garment
# typed `Vinyl` is the shape this rejects.
_GARMENT_SIZE_RE = re.compile(r"^(?:\d*x*(?:s|m|l|xl)|one\s+size)$", re.IGNORECASE)
# Shopify's placeholder for a product with exactly one variant. It names no
# pressing, so a row built on it carries the album alone -- and only when it
# IS the product's sole variant: on a multi-variant product the placeholder
# is malformed data, and a blank title is never a pressing. Either would
# otherwise share the title and the product URL, and so the item_key, with
# every sibling built the same way.
_PLACEHOLDER_VARIANT = "default title"


class Crawler:
    site_name: str = "Bella Union"
    base_url: str = "https://bellaunion.com"
    genre_summary: str = "Simon Raymonde's London label, born out of Cocteau Twins — Beach House, Father John Misty, John Grant, Explosions In The Sky, Ezra Furman and Marissa Nadler — alongside a signed Cocteau Twins and 4AD reissue shelf."
    genre: str = "rock"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        products_seen = 0
        parsed_ok = 0
        unclassifiable = 0
        identity_missing = 0
        variant_identity_missing = 0
        unreadable_stock = 0
        yielded = 0
        priced = 0
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            products_seen += 1
            # One tally for both halves of the credit, unlike the sibling
            # stores whose artist and album come from separate fields: here
            # a single split of a single field yields both, so there is no
            # second source that could go dark behind this one, and nothing
            # for a co-occurrence tally to say. Taken before the record
            # classification below, so a catalog that legitimately filled up
            # with CDs still satisfies it.
            _, album = self._parse_title(product.get("title"))
            if album:
                parsed_ok += 1

            if not (product.get("title") or "").strip():
                # A title-less product cannot be classified at all -- the
                # parse reads the title, and nothing below can say anything
                # about it, so it might have been a record. Counted here
                # rather than inside the gate, or a partial loss of `title`
                # would leave an empty walk looking like a shelf that had
                # merely sold out, and the snapshot would be deleted.
                identity_missing += 1
            elif has_tag(product, _MERCH_TAG) or (album and not self._claims_vinyl(product)):
                # Read and deliberately skipped: the store's merch, or a
                # product it publishes with no record among its formats.
                # Neither could yield a row however its title reads, so a
                # failed parse on one is evidence of nothing. This test comes
                # first for that reason.
                pass
            elif not album:
                # Never read at all: the one source failed on this product,
                # so the crawler cannot say whether it was a record. The
                # catalog-wide tally above only notices the convention
                # vanishing from EVERY product; one well-formed sold-out
                # record keeps it non-zero while this one goes uncounted.
                unclassifiable += 1
            else:
                # A record: the title parsed and the product claims a record
                # among its formats. These tallies are nested here because
                # only such a product could have yielded a row -- a
                # mis-shelved T-shirt's missing handle says nothing about
                # whether this walk's emptiness can be trusted.
                if not self._has_identity(product):
                    identity_missing += 1
                elif self._unusable_dropped_variant(product):
                    # Kept apart from identity_missing so the guard can name
                    # which identity failed: the product's, or a variant's.
                    variant_identity_missing += 1
                elif not self._has_readable_stock_flag(product):
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
        # stop carrying what this crawler reads. The tallies above are taken
        # before the availability filter, so a sold-out product still counts
        # toward every one of them; `yielded` and `priced` are necessarily
        # counted after it, which is why the guards reading them are each
        # conditioned on a second tally rather than on emptiness alone -- a
        # store that has simply sold out is empty legitimately.
        #
        # There is deliberately no format-gate guard: the gate is negative,
        # so no positive signal's disappearance can silently empty the walk.
        if products_seen == 0:
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection returned no products -- renamed, removed, or markup drift")
        if parsed_ok == 0:
            # The title is the only source of both the artist and the album,
            # so this is the guard that notices the store restyling its
            # product names: every record would then be skipped while the
            # walk still completed.
            raise RuntimeError(
                f"no product in the {_COLLECTION_SLUG} collection has a title of the form "
                "Artist - Album -- title-source drift")
        if yielded and not priced:
            # Rows without the emptiness: `_price` answers None for a value
            # it cannot use, so a `price` field removed or retyped store-wide
            # re-lists the whole catalog with no prices, which is worse than
            # the snapshot it would replace. Isolated nulls stay tolerated.
            raise RuntimeError(
                f"none of the {yielded} rows from the {_COLLECTION_SLUG} collection carries a "
                "price -- price-source drift")
        if not yielded and unclassifiable:
            # Same empty-outcome gate as the three below, and for the same
            # reason: among real rows an unreadable product is an ordinary
            # skipped row, but it must never be what an empty result rests on.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while "
                f"{unclassifiable} product(s) were neither the store's merch nor readable as a "
                "record -- classification drift")
        if not yielded and identity_missing:
            # `title` and `handle` are identity, not display: item_key hashes
            # the row's artist, title and URL, so a product missing either is
            # skipped rather than emitted under a fresh identity that would
            # orphan the judgments and saves keyed on its old one. Skipped
            # rows leave the walk looking sold out, which is why the same
            # empty-outcome gate as the guards below applies.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while "
                f"{identity_missing} product(s) carry no title, or are records carrying no handle "
                "-- identity-source drift")
        if not yielded and variant_identity_missing:
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while "
                f"{variant_identity_missing} record(s) dropped a variant that carries no usable "
                "title and is not provably sold out -- variant-identity drift")
        if not yielded and unreadable_stock:
            # An empty result is only trustworthy when every product that
            # could have yielded a row was readable and simply out of stock.
            # Counting unreadable products rather than readable ones is what
            # catches the partial case: one genuinely sold-out product must
            # not vouch for a catalog that has gone unreadable behind it.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while "
                f"{unreadable_stock} record(s) carry no readable availability flag -- "
                "stock-source drift")

    @classmethod
    def _items(cls, product: dict) -> list:
        record = cls._record(product)
        if record is None:
            return []
        artist, album = record
        if not cls._has_identity(product):
            return []
        url = f"{cls.base_url}/products/{(product.get('handle') or '').strip()}"
        # No ` (Pre-Order)` marker. The store's only pre-order signal is
        # membership of its `upcoming-releases` collection -- no tag, no
        # product field -- so writing one would cost a second walk, and
        # compute_item_key hashes artist, title and URL, so a marker that
        # disappeared when the record shipped would re-key every one of its
        # pressings at exactly the moment a waiting user cares most,
        # orphaning the saves and judgments held against the old key. That is
        # the same churn the descriptor rule below refuses. A pre-order is
        # purchasable at a real price and is listed as an ordinary row.
        items = []
        for variant, descriptor in cls._pressings(product):
            if not cls._is_vinyl(descriptor):
                continue
            # Only the literal True admits a variant: the string "false" is
            # truthy, so a falsiness test would publish a sold-out record as
            # in stock. Anything else -- False, "false", 1, None, absent --
            # is skipped, which is also what keeps this filter and
            # _has_readable_stock_flag agreeing on what "readable" means.
            # No pre-order bypass: an unavailable pre-order is a closed
            # allocation, not a purchasable row.
            if variant.get("available") is not True:
                continue
            # The descriptor is appended on every row that names one, not
            # only when the product has more than one variant: a sibling
            # being listed or delisted must not re-title the rows and orphan
            # the listings, judgments and saves keyed on the old identity.
            # The placeholder is the one exception, because it names nothing,
            # and _pressings admits it only as a product's sole variant, so
            # no sibling can share the bare album.
            items.append({
                "artist": artist,
                "title": f"{album} — {descriptor}" if descriptor else album,
                "format": "Vinyl",
                "price": cls._price(variant),
                "currency": "GBP",
                "url": url,
                "cover_image_url": resolve_cover_image(product, variant),
            })
        return items

    @classmethod
    def _record(cls, product: dict) -> Optional[Tuple[str, str]]:
        """(artist, album) for a product this crawler reads as a record, else None."""
        if has_tag(product, _MERCH_TAG):
            return None
        artist, album = cls._parse_title(product.get("title"))
        if not album:
            return None
        if not cls._claims_vinyl(product):
            return None
        return artist, album

    @staticmethod
    def _parse_title(title) -> Tuple[str, str]:
        """Split `Artist - Album` -- ("", "") when the title is not of that form.

        The album leads the row's title, and the pressing descriptor follows
        it, because db._library_release_match_sql matches a stock row against
        a library release on an exact-or-prefix-with-space title test: a row
        titled "Arco — Galaxy Teal Vinyl" satisfies a library "Arco" only
        while the album comes first.

        `vendor` is never consulted. It names the label rather than the
        artist on every product in the store -- `Bella Union` on the
        catalog, `4AD` and `Fontana` on the licensed reissues -- so there is
        nothing in it to fall back to, and no second source for the guard
        above to have to reason about.
        """
        collapsed = " ".join((title or "").split())
        parts = _SPLIT_RE.split(collapsed, maxsplit=1)
        if len(parts) != 2:
            return "", ""
        artist, album = parts[0].strip(), parts[1].strip()
        if not artist or not album:
            return "", ""
        return artist, album

    @classmethod
    def _claims_vinyl(cls, product: dict) -> bool:
        """Does the product itself say a record is one of the things it is?

        Two claims, either of which is enough, because neither alone covers
        the catalog. `product_type` is `Vinyl` on the bulk of the store but
        blank on four of its records, and the store's merch is blank too, so
        it cannot be the only claim; a variant naming a record covers those
        four, but not the records whose only variant is `Default Title`, a
        box set or a `Gold Edition`, all of which are typed `Vinyl`.

        This is the product-level gate; _is_vinyl below then decides which of
        the product's variants are the record.
        """
        if (product.get("product_type") or "").strip().lower() == "vinyl":
            return True
        return any(
            _VINYL_WORD_RE.search(descriptor)
            for _, descriptor in cls._pressings(product)
        )

    @staticmethod
    def _is_vinyl(descriptor: str) -> bool:
        """Is this variant the record, rather than the CD beside it?

        The product has already claimed a record among its formats, so the
        gate is negative: a bundle or a garment rejects first, then a record
        word admits outright, then a word naming another medium or a bare
        garment size rejects, and anything else is admitted on the product's
        own claim. Negative rather than enumerated so a format the store adds
        later -- another box set, a `Deluxe Edition` -- stays in by default,
        which is how every live `Gold Edition`, `Deluxe Boxset` and
        `Limited Edition Boxset` is kept.

        The bundle and garment tests come FIRST, ahead of the record word
        that would otherwise admit `Black Vinyl + T Shirt Bundle` outright:
        its price is a bundle's, not the record's. Read against the
        descriptor alone and never the product title, so an album that
        legitimately names one of these words keeps its rows -- the live
        `A.A. Williams - As The Moon Rests (Signed Print)` is a record, and
        `The Fall - Singles Live Vol.1` sells both a plain pressing and a
        shirt bundle under one title.
        """
        if _BUNDLE_RE.search(descriptor) or _MERCH_RE.search(descriptor):
            return False
        if _VINYL_WORD_RE.search(descriptor):
            return True
        if _OTHER_MEDIA_RE.search(descriptor):
            return False
        return not _GARMENT_SIZE_RE.match(descriptor.strip())

    @classmethod
    def _pressings(cls, product: dict) -> list:
        pairs = []
        # Non-mapping entries are dropped here, before anything reads them,
        # so a junk entry is an ordinary skipped row rather than an
        # AttributeError from inside the yield loop.
        variants = [v for v in product.get("variants") or [] if isinstance(v, dict)]
        for variant in variants:
            descriptor = " ".join((variant.get("title") or "").split())
            if not descriptor:
                continue
            if descriptor.lower() == _PLACEHOLDER_VARIANT:
                if len(variants) == 1:
                    pairs.append((variant, ""))
                continue
            pairs.append((variant, descriptor))
        return pairs

    @staticmethod
    def _has_identity(product: dict) -> bool:
        return bool((product.get("title") or "").strip()) and bool((product.get("handle") or "").strip())

    @classmethod
    def _unusable_dropped_variant(cls, product: dict) -> bool:
        """Did a variant get dropped for want of a usable title without being
        provably sold out?

        Only the literal False proves it. Without this, a blank-titled
        in-stock variant beside a sold-out sibling makes an in-stock product
        look sold out, and the walk's emptiness would be trusted. Runs before
        _has_readable_stock_flag, which reads only the KEPT pressings and is
        complete precisely because this has already established that every
        dropped entry is a readable False: the two are a pair and must stay
        in that order.
        """
        kept = [variant for variant, _ in cls._pressings(product)]
        for variant in product.get("variants") or []:
            if not isinstance(variant, dict):
                # A junk entry carries no availability at all, so it can never
                # be proven sold out. _pressings drops it before anything
                # reads it, which is right for building rows and wrong for
                # trusting an empty one.
                return True
            if variant.get("available") is not False and not any(variant is k for k in kept):
                return True
        return False

    @classmethod
    def _has_readable_stock_flag(cls, product: dict) -> bool:
        # Judged against the raw variant set, not the pressings kept from it:
        # a product whose sole variant has a blank title and a readable False
        # is genuinely sold out but keeps no pressings at all, and keying on
        # those would raise stock drift over a product read perfectly. A
        # product with no variants whatsoever still says nothing.
        #
        # all(), not any(): one readable variant does not make a product
        # readable, or a product whose black pressing is a readable False and
        # whose coloured pressing carries the string "false" would vouch for
        # an emptiness half its own doing.
        if not (product.get("variants") or []):
            return False
        return all(
            isinstance(variant.get("available"), bool)
            for variant, _ in cls._pressings(product)
        )

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
