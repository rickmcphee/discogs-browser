import math
import re
from typing import AsyncIterator, Optional, Tuple

from shopify_catalog import iter_products, resolve_cover_image

# Shopify's built-in all-products collection, not the `vinyl` shelf the
# request named. The shelf is not the store's records: confirmed live that
# three published, in-stock LPs (Bismuth "The Eternal Marshes", Glitch
# "Towards The Gutter", Filmmaker "Multiverse Nightmare" — all Tartarus
# distro, all published the same day) carry the `Vinyl` product_type and sit
# outside it, so the store's own curation of that shelf has already fallen
# behind its taxonomy once. `all` is the whole published catalog, confirmed to
# return exactly the store's published_products_count from meta.json and the
# same handle set as the store-wide /products.json, and the product_type gate
# below scopes it to records. The cost is the pages the walk fetches, since
# the shelf is a fraction of the catalog.
#
# The shelf walk was verified complete in the other direction before it was
# passed over: it returns the same handles as its own paginated HTML, and
# `used-vinyl`, `test-pressings` and `faetooth-vinyl` add nothing to it
# (the first two publish no products at all; the third is a subset).
_COLLECTION_SLUG = "all"
# Enumerated positively, so a type the store adds later stays out by default.
# A product_type here can name more than one taxonomy, comma-joined
# ("Vinyl,Distributed titles", "Flenser Releases,CDs"), so the test is
# per-segment rather than on the whole string -- an equality test would drop
# the three records typed that way, and a substring test would admit
# "Distributed titles,CDs".
#
# Read from product_type and NOT from tags, though both look like they say
# the same thing. The tag is wrong in both directions live: it is on the
# store's vinyl-edition subscription ("Flenser Membership - Series Nine -
# Vinyl Edition"), which is not a record and is typed `Membership Series`,
# and it is missing from two records that carry no tags at all (Hum "Inlet"
# DLP, Kathryn Mohr "Waiting Room" LP). product_type is right on all three,
# and on every other product in the catalog.
_VINYL_PRODUCT_TYPE = "vinyl"
# `Artist "Album" <format>`, the convention every single-item product in the
# store follows. Confirmed live against every vinyl-typed product: all but one
# carry exactly two quotes, and the one that doesn't is a bundle (see
# _BUNDLE_RE).
#
# The two character classes are asymmetric, deliberately. The artist group
# excludes only the characters that can OPEN a quotation, which is what makes
# the album's opening quote the first quote in the string -- so a descriptor's
# own inch marker (`12"`) can never be read as one. The album group excludes
# every quote there is, so a stray third quote is junk rather than an album.
# Curly quotes are admitted though the store writes none: they are the
# commonest way a storefront's copy drifts, and admitting them costs nothing.
_OPENING_QUOTES = '"“'
_CLOSING_QUOTES = '"”'
_ALL_QUOTES = '"“”'
_TITLE_RE = re.compile(
    r'^(?P<artist>[^' + re.escape(_OPENING_QUOTES) + r']*?)\s*'
    r'[' + re.escape(_OPENING_QUOTES) + r']'
    r'(?P<album>[^' + re.escape(_ALL_QUOTES) + r']+?)'
    r'[' + re.escape(_CLOSING_QUOTES) + r']\s*'
    r'(?P<descriptor>.*)$'
)
# A split's billing names every band on the record, but a stock row's artist
# has to be the first-billed one to be matchable: `discogs.parse_release`
# stores `artists[0]` and nothing else, and `db._library_release_match_sql`
# compares artists with exact case-folded equality (only the title gets the
# exact-or-prefix treatment). A joined billing can therefore never match a
# library release, so the store's two splits would sit permanently outside
# the Store tab's Collection and Wantlist filters.
#
# Whitespace required on at least one side of the slash, the repo's standard
# guard for this bug class, so an artist whose own name contains one (AC/DC)
# is not clipped to its first half.
#
# The slash only, NOT the ampersand, though the store bills eight
# collaborations with one ("Bell Witch & Aerial Ruin", "Ragana & Drowse") and
# every live ampersand is in fact a collaboration. An ampersand is also how
# plenty of single acts spell their own name -- Belle & Sebastian, Iron &
# Wine, both on labels this store already distros -- and clipping one of
# those would not merely miss a library match, it would credit the row to a
# band that did not make the record. A slash carries no such ambiguity: it is
# the split-record separator and nothing else.
_BILLING_SPLIT_RE = re.compile(r'(?:\s+/\s*|\s*/\s+)')
# The store sells bundles, and the one it shelves as vinyl ("Mamaleek Vinyl
# Bundle") carries no quoted album, so the title parse already excludes it.
# This is for the bundle written to the store's usual convention
# (`Mamaleek "Vinyl Bundle" LP`), whose descriptor would then satisfy the
# format gate on its own `Vinyl`. A bundle is not a Discogs release and its
# price is not any record's price.
#
# Read against the DESCRIPTOR rather than the whole title, so an album that
# legitimately contains the word (`Artist "Bundle of Joy" LP`) is not
# silently dropped.
_BUNDLE_RE = re.compile(r'\bbundles?\b', re.IGNORECASE)
# What the descriptor has to name for the title to be a record. The
# product_type gate is not enough by itself: the store shelves a
# scratch-and-dent bin as `Various "Scratch & Dent" Stock`, typed `Vinyl`,
# whose "variants" are whole other releases (a Succumb LP, a Loss of Self CD,
# a Planning for Burial tape set) rather than pressings of one. Its
# descriptor is the only live one that names no format, and that is what
# separates a record from an inventory bucket.
#
# Positive rather than negative, because a descriptor can name a record AND
# something else: `DLP & DVD`, `DLP & Book (pre-order)` and `DLP & Zine` are
# all real records in packaging, and a gate that rejected on the second noun
# would drop all three. Naming a vinyl format wins; naming anything else
# alongside it is irrelevant.
#
# `EP` is admitted here though nothing live uses it, and ONLY here. An EP is
# as often a CD as a record, so the word names a format without naming a
# medium -- which is all this gate needs, since product_type has already said
# the product is vinyl. The variant gate below asks the other question ("is
# this pressing a record, next to siblings that may not be") and so reads
# _VINYL_MEDIUM_RE, which EP is deliberately not part of: a "CD EP" sibling
# must not be admitted by the same word that admits a 12" EP here.
#
# The inch marker admits the quote glyph as well as the spelled-out word,
# though this store spells every one of its own out (`10inch`, `7inch`) and
# writes no quote glyph anywhere. The glyph is genuinely ambiguous in a
# variant title, where the string can be a whole `Artist "Album" Format`
# title rather than a pressing name: the album's closing quote after a digit
# (`... Vol 1 & 2" Tape Set`) reads as a 2-inch record and admits a variant
# the media gate would otherwise reject. That is tolerated rather than
# machined around, on two grounds -- the only live title shaped that way is
# inside the scratch-and-dent bin the descriptor gate already drops, and the
# variant gate's default is to admit anyway, so the ambiguity can only
# reach an outcome the gate was already willing to reach. Dropping the glyph
# instead would trade that for a silent loss of any record the store one day
# describes as a 12", which is the worse failure.
_LP = r'(?:\d+\s*[x×]?\s*)?d?lps?'
_INCH = r'(?:\d+\s*[x×]\s*)?\d{1,2}\s*-?\s*(?:inch(?:es)?|["”″])'
_VINYL_MEDIUM_RE = re.compile(
    r'(?<!\w)(?:%s|vinyls?)(?!\w)|(?<!\w)(?:%s)' % (_LP, _INCH),
    re.IGNORECASE,
)
_RECORD_FORMAT_RE = re.compile(
    r'(?<!\w)(?:%s|vinyls?|eps?)(?!\w)|(?<!\w)(?:%s)' % (_LP, _INCH),
    re.IGNORECASE,
)
# Shopify's placeholder for a product with exactly one variant. It names no
# pressing, so a row built on it carries the album title alone -- and only
# when it IS the product's sole variant: on a multi-variant product the
# placeholder is malformed data, and a blank name is never a pressing. Either
# would otherwise share the bare album title and the product URL, and so the
# item_key, with every sibling built the same way.
_PLACEHOLDER_VARIANT = "default title"
# A variant title here is the pressing's colour and usually nothing else
# ("Bone with Black Splatter Vinyl", "Koi Pond Vinyl with Book"), so the
# variant gate is negative: on a vinyl-typed product a variant is a record
# unless its own title names another medium. Check order is load-bearing
# rather than stylistic -- a vinyl word decides before another medium word,
# so a pressing named for both sides of its second disc stays a record.
_NON_VINYL_MEDIA_RE = re.compile(
    r'(?<!\w)(?:\d*[x×]?cds?|cassettes?|tapes?|digital|digipa[kc]k?s?'
    r'|\d*[x×]?dvds?|blu-?rays?)(?!\w)',
    re.IGNORECASE,
)


class Crawler:
    site_name: str = "The Flenser"
    base_url: str = "https://nowflensing.com"
    genre_summary: str = "San Francisco label and store for experimental black metal, doom and dark post-punk — Chat Pile, Have a Nice Life, Agriculture, Bell Witch and King Woman — plus a heavy underground distro."
    genre: str = "metal"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        products_seen = 0
        vinyl_typed = 0
        parsed = 0
        format_ok = 0
        record_variants_seen = 0
        identity_missing = 0
        unreadable_stock = 0
        yielded = 0
        priced = 0
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            products_seen += 1
            # Nested, not sibling tallies: only a product that is vinyl-typed
            # AND parses AND names a format AND has a variant the gate admits
            # could have yielded a row, so only such a product's identity and
            # stock readability say anything about an empty result. Tallied
            # independently, one product could satisfy each condition while
            # none of them can yield.
            if self._is_vinyl_product(product):
                vinyl_typed += 1
                artist, album, descriptor = self._parse_title(product)
                if artist and album:
                    parsed += 1
                    if self._names_a_record(descriptor):
                        format_ok += 1
                        variants = self._record_variants(product)
                        if variants:
                            record_variants_seen += 1
                            if not self._has_identity(product):
                                identity_missing += 1
                            elif not self._has_readable_stock_flag(variants):
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
        # carrying what this crawler reads. The tallies above are all taken
        # before the availability filter, so a sold-out product still counts
        # toward every one of them; `yielded` and `priced` are necessarily
        # counted after it, which is why the guards reading them are each
        # conditioned on a second tally rather than on emptiness alone -- a
        # store that has simply sold out is empty legitimately.
        if products_seen == 0:
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection returned no products -- renamed, removed, or "
                "markup drift")
        if vinyl_typed == 0:
            raise RuntimeError(
                f"no product in the {_COLLECTION_SLUG} collection carries a "
                f"{_VINYL_PRODUCT_TYPE!r} product_type segment -- format-taxonomy drift")
        if parsed == 0:
            # The artist and the album both come out of the title's quoted
            # form, with no second source -- `vendor` is the releasing label
            # here, not the artist, so there is nothing to fall back to. This
            # is the guard that notices the store restyling its titles.
            raise RuntimeError(
                f"no vinyl product in the {_COLLECTION_SLUG} collection yields an artist and an "
                'album from its `Artist "Album"` title -- title-convention drift')
        if format_ok == 0:
            # A store whose catalog is records does not stop naming their
            # format, so zero has no innocent reading: either the convention
            # moved the format out of the title, or it is being written in
            # words this gate does not know.
            raise RuntimeError(
                f"no vinyl product in the {_COLLECTION_SLUG} collection carries a descriptor that "
                "names a record format -- format-vocabulary drift")
        if record_variants_seen == 0:
            # The pressing is read off the variants, so this is the guard that
            # notices the store losing them or re-titling every one of them as
            # another medium.
            raise RuntimeError(
                f"no vinyl product in the {_COLLECTION_SLUG} collection has a variant that reads "
                "as a record -- pressing-source drift")
        if yielded and not priced:
            # Rows without the emptiness: `_price` answers None for a value it
            # cannot use, so a `price` field removed or retyped store-wide
            # re-lists the whole catalog with no prices, which is worse than
            # the snapshot it would replace. Isolated nulls stay tolerated.
            raise RuntimeError(
                f"none of the {yielded} rows from the {_COLLECTION_SLUG} collection carries a "
                "price -- price-source drift")
        if not yielded and identity_missing:
            # Title and handle are identity, not display: item_key hashes the
            # row's title and URL, so a product missing either is skipped
            # rather than emitted under a fresh identity that would orphan the
            # judgments and saves keyed on its old one. Skipped rows leave the
            # walk looking sold out, which is why the same empty-outcome gate
            # as the stock guard below applies.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while {identity_missing} vinyl "
                "product(s) carry no title or no handle -- identity-source drift")
        if not yielded and unreadable_stock:
            # An empty result is only trustworthy when every product that
            # could have yielded a row was readable and simply out of stock.
            # Counting unreadable products rather than readable ones is what
            # catches the partial case: one genuinely sold-out record must not
            # vouch for a catalog that has gone unreadable behind it.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while {unreadable_stock} vinyl "
                "product(s) carry no readable availability flag -- stock-source drift")

    @classmethod
    def _items(cls, product: dict) -> list:
        if not cls._is_vinyl_product(product):
            return []
        artist, album, descriptor = cls._parse_title(product)
        if not artist or not album:
            return []
        if not cls._names_a_record(descriptor):
            return []
        if not cls._has_identity(product):
            return []
        url = f"{cls.base_url}/products/{product.get('handle', '')}"
        items = []
        for variant, pressing in cls._record_variants(product):
            # Only the literal True admits a variant: the string "false" is
            # truthy, so a falsiness test would publish a sold-out record as
            # in stock. Anything else -- False, "false", 1, None, absent -- is
            # skipped, which is also what keeps this filter and
            # _has_readable_stock_flag agreeing on what "readable" means.
            #
            # No pre-order bypass and no " (Pre-Order)" marker. The store's
            # pre-orders already report available True, and it announces them
            # in the product title ("... LP (pre-order)") -- which the parse
            # leaves in the descriptor, outside the album, so the row is
            # titled the same before and after the record ships. item_key
            # hashes the title; a marker would re-key the row on the day it
            # stopped being a pre-order.
            if variant.get("available") is not True:
                continue
            # The pressing is appended on every row that names one, not only
            # when the product has more than one variant: a sibling pressing
            # being listed or delisted must not re-title the surviving rows
            # and orphan the listings, judgments and saves keyed on the old
            # identity. The placeholder is the one exception, because it names
            # nothing, and _record_variants admits it only as a product's sole
            # variant, so no sibling can share the bare title.
            items.append({
                "artist": artist,
                "title": f"{album} — {pressing}" if pressing else album,
                "format": "Vinyl",
                "price": cls._price(variant),
                "currency": "USD",
                "url": url,
                "cover_image_url": resolve_cover_image(product, variant),
            })
        return items

    @staticmethod
    def _is_vinyl_product(product: dict) -> bool:
        segments = (product.get("product_type") or "").split(",")
        return any(s.strip().lower() == _VINYL_PRODUCT_TYPE for s in segments)

    @classmethod
    def _parse_title(cls, product: dict) -> Tuple[str, str, str]:
        """Split `Artist "Album" <format>` out of the product title.

        There is no fallback source for either half. `vendor` names the label
        that released the record (this store distros dozens of them) and never
        the artist, unlike the sibling Shopify stores where it is the artist's
        own name -- so a vendor fallback would credit every unparseable title
        to a record label. A title that doesn't parse yields no row at all.
        """
        title = " ".join((product.get("title") or "").split())
        m = _TITLE_RE.match(title)
        if not m:
            return "", "", ""
        artist = m.group("artist").strip()
        album = m.group("album").strip()
        return cls._primary_artist(artist), album, m.group("descriptor").strip()

    @staticmethod
    def _primary_artist(billing: str) -> str:
        """Reduce a multi-artist split billing to the artist billed first."""
        return _BILLING_SPLIT_RE.split(billing, 1)[0].strip() or billing

    @staticmethod
    def _names_a_record(descriptor: str) -> bool:
        if _BUNDLE_RE.search(descriptor):
            return False
        return bool(_RECORD_FORMAT_RE.search(descriptor))

    @classmethod
    def _record_variants(cls, product: dict) -> list:
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
            if cls._is_record_variant(title):
                pairs.append((variant, title))
        return pairs

    @staticmethod
    def _is_record_variant(title: str) -> bool:
        if _VINYL_MEDIUM_RE.search(title):
            return True
        return not _NON_VINYL_MEDIA_RE.search(title)

    @staticmethod
    def _has_identity(product: dict) -> bool:
        return bool((product.get("title") or "").strip()) and bool((product.get("handle") or "").strip())

    @staticmethod
    def _has_readable_stock_flag(record_variants: list) -> bool:
        # every(), not any(): one readable variant does not make the product
        # readable. A product whose black pressing is a readable False and
        # whose coloured pressing carries the string "false" yields nothing,
        # and under any() would vouch for an emptiness half its own doing.
        # Scoped to the variants that read as records, because they are the
        # only ones that could have yielded.
        return bool(record_variants) and all(
            isinstance(v.get("available"), bool) for v, _ in record_variants)

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
