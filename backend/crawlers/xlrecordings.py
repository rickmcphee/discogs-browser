import math
import re
import unicodedata
from typing import AsyncIterator, List, Optional, Tuple

from shopify_catalog import iter_products, resolve_cover_image

# Shopify's built-in all-products collection. The store publishes a curated
# `all-releases` shelf too, and on 2026-09-21 the two agreed exactly -- same
# product ids, and both equal to `meta.json`'s published_products_count. The
# built-in one is preferred anyway because its completeness is structural
# rather than maintained: `all-releases` is hand-curated, so a release nobody
# adds to it is invisible with nothing to notice, while `all` cannot omit a
# published product. Merch sits in both, so walking `all` costs no extra
# filtering -- the gates below would have to run either way.
_COLLECTION_SLUG = "all"

# The `Crawl-delay` this store's robots.txt asks for. Passed to every request
# as a floor rather than left to `crawl_delay_seconds`, which is admin-editable
# with no lower bound: at its default of 30 the jitter gives 15-30s and honours
# this comfortably, but a setting below 20 would not, and 0 would send requests
# back-to-back. This repo's crawl-citizenship spec is normative and says
# citizenship is enforced by the design rather than asserted -- a compliance
# claim that holds only while nobody edits a setting is the asserted kind.
# The sibling Shopify stores' robots.txt names no Crawl-delay at all, which is
# why this is the first Shopify-backed crawler to need the floor.
_SITE_CRAWL_DELAY = 10.0

# The artist is NOT in the product title here -- the title is the album alone
# (`Dopamine Chamber`, `XXXXX`), so there is no `Artist - Album` split to do.
# It comes from `vendor`, except where `vendor` names the shop rather than
# anybody who made the record: `XLRecordingsProd` (the myshopify handle) and
# `XL Recordings USA` between them cover more than a third of the catalog.
# Matched on a normalised prefix rather than the two literals so that a future
# `XL Recordings UK` is caught as the label it is, instead of being published
# as an artist of that name for every record it fronts.
_LABEL_VENDOR_PREFIX = "xlrecordings"
_NON_ALNUM_RE = re.compile(r"[^0-9a-z]+")

# Where `vendor` is the label, the artist is a tag. The store tags a product
# with its artist plus, variously, a release year, a format shelf and a
# fulfilment state, so those three are subtracted and the artist is what
# remains. Years are matched by shape, not enumerated, or the crawler starts
# publishing records by an artist called "2027" the January after it ships.
_YEAR_TAG_RE = re.compile(r"^(?:19|20)\d{2}$")
# What separates two credits in a billing, as opposed to what merely sits
# inside one artist's name. Deliberately space-delimited and deliberately
# WITHOUT the comma: a comma here belongs to the name (`Tyler, The Creator`),
# which is also how Discogs writes it, so reading one as a separator would
# truncate the artist rather than complete the match. `x` needs trailing
# whitespace, so the `xx` of `Jamie xx` cannot pose as one.
_COLLABORATION_SEPARATOR_RE = re.compile(
    r"^\s*(?:&|\+|and|feat\.?|featuring|ft\.?|with|vs\.?|x)\s+", re.IGNORECASE)
_NON_ARTIST_TAGS = frozenset(('12" singles', "preorder", "xl merch"))

# The pressing descriptor is the tail of the variant title, which this store
# builds as `{album} - {descriptor}` -- the album name is baked into its
# `Format` option values (`Dopamine Chamber - Blue Vinyl LP`). Split on the
# LAST separator, not the first: the album half carries its own ` - ` on a
# bundle, and a double A-side names both sides with a slash
# (`Gi Mi Keys Back / Auto Fake - 12" Single`), so only the tail is reliably
# the format. \s+-\s+ rather than a bare dash, so a hyphen inside either half
# (`Gil Scott-Heron`, `Blu-Ray`) is not a separator.
_SPLIT_RE = re.compile(r"\s+-\s+")

# Unicode-aware word boundaries, ported from bellaunion.py, which took them
# from dongiovannirecords.py over two review passes. `[a-z]` is ASCII-only
# even under IGNORECASE, so an accented letter is not a letter to it and the
# boundary opens: `éLP CD` matched `lp`, the gate admitted on the record word,
# and the trailing `CD` was never reached. `[^\W\d_]` is "a letter" to
# Python's Unicode `\w`, which closes that. This store's live descriptors are
# ASCII, but its product titles are not (`Jack Peñate`, `Låpsley`, `Sigur
# Rós`), so the encoding the store writes in is demonstrably not ASCII and a
# descriptor that follows the titles is a matter of time.
_NOT_AFTER_LETTER = r"(?<![^\W\d_])"
_NOT_AFTER_LETTER_OR_DIGIT = r"(?<![^\W_])"
_NOT_BEFORE_LETTER_OR_DIGIT = r"(?![^\W_])"
# `\w` is still not the whole story: it excludes the combining MARK
# categories, so in decomposed text the character before `LP` in `éLP CD` is
# the accent rather than a letter and the boundary opens again -- while the
# precomposed spelling of the same string is rejected. The same descriptor
# must not read two ways depending on how it was encoded.
_MARK_CATEGORIES = frozenset(("Mn", "Mc", "Me"))
# A mark stands in as a letter, because a mark IS part of the word it
# follows. Deliberately not an ASCII letter: it appears in no pattern here and
# matches none of them under IGNORECASE, so folding can only ever close a
# boundary, never spell a format or merch word into existence.
_MARK_STAND_IN = "ß"


def _fold_marks(text: str) -> str:
    """The text with every combining mark replaced by a letter, for MATCHING ONLY.

    Never for anything emitted: it is a decision-time normalisation, so that
    the boundaries above see a mark as the word-interior it is.
    """
    if text.isascii():
        return text
    return "".join(
        _MARK_STAND_IN if unicodedata.category(ch) in _MARK_CATEGORIES else ch
        for ch in text
    )


# The quote glyph needs a right-hand boundary of its own, which the spelled-out
# `inch` alternative gets free from its `\b`: without one, `12"CD` reads as a
# complete inch marker, the gate admits it on the record word, and the `CD` is
# never reached.
_INCH_MARKER = (
    _NOT_AFTER_LETTER_OR_DIGIT + r"(?:\d+\s*[x×]\s*)?\d{1,2}\s*"
    r'(?:["”″]' + _NOT_BEFORE_LETTER_OR_DIGIT + r"|inch(?:es)?\b)"
)
# The store's live record vocabulary: `LP`, `2X LP`, `2xLP Lenticular Cover`,
# `3X LP`, `12" EP`, `7" Single`, `2X 10" Album`, and the colour pressings
# that name `Vinyl` outright. The disc-count prefix admits `2xLP`, where no
# word boundary separates the count from the noun.
_VINYL_WORD_RE = re.compile(
    _NOT_AFTER_LETTER + r"(?:\d+(?:\.\d+)?\s*[x×]\s*)?lps?\b|\bvinyls?\b"
    r"|" + _INCH_MARKER,
    re.IGNORECASE,
)
# `cs` is this store's own abbreviation for a cassette -- it sells `CS Album`
# beside `LP` and `CD` -- and it has to be listed, because unlike the sibling
# stores' vocabulary it names no medium a reader would recognise, so the
# default-admit rule below would otherwise publish a cassette at a cassette's
# price under this crawler's `Vinyl` format. It is safe to list precisely
# because the record word is tested FIRST: the live `Deluxe 3X LP + CS + Books
# LP` is a vinyl box set that happens to include a cassette, and it is
# admitted on its `3X LP` before this pattern is ever reached.
# `casse+tte` rather than `cassette` follows bellaunion.py, where a live
# variant was spelled `Casseette`; a strict spelling publishes a cassette as a
# record, and the cost of the loose one is nil.
_OTHER_MEDIA_RE = re.compile(
    _NOT_AFTER_LETTER + r"(?:\d+\s*[x×]\s*)?(?:cds?|cs|casse+ttes?|tapes?|dvds?|blu-?\s?rays?)\b"
    r"|\bdigital\b",
    re.IGNORECASE,
)
# Garments and the hard goods the store sells beside them, read off its live
# merch descriptors (`Long Sleeve Shirt Small`, `SLIPMAT`, `UMBRELLA`).
# Deliberately NOT `print`: `Deluxe 2X LP w/ signed print` and `Blue Yolk LP +
# Signed Print` are records with an extra, priced at a record's price, and
# rejecting them would drop two live pressings.
_MERCH_RE = re.compile(
    r"\b(?:t-?\s?)?shirts?\b|\btees?\b|\bhoodies?\b|\bsweatshirts?\b"
    r"|\bcrewnecks?\b|\blong\s*sleeves?\b|\btank\s+tops?\b|\bjerseys?\b"
    r"|\bjackets?\b|\bcaps?\b|\bhats?\b|\btotes?\b|\bbags?\b"
    r"|\bslipmats?\b|\bumbrellas?\b|\bposters?\b",
    re.IGNORECASE,
)
# A descriptor whose last word is a garment size. Anchored at the end rather
# than matched anywhere, because a size word is only ever a size in that
# position here: the store writes `Black Small`, `Charcoal Cotton XL`,
# `White Cotton XXL` and the bare `S`/`M`/`L`/`XL` its untyped merch uses.
# No live record descriptor ends in one -- `2X LP` and `Deluxe LP` end in the
# noun, not a size -- and the alternation must reach `$`, so the `L` of an
# `LP` cannot satisfy it with the `P` still to come.
_GARMENT_SIZE_RE = re.compile(
    r"(?:^|\s)(?:x{0,3}s|x{0,3}l|m|small|medium|large|x-?large|one\s+size)$",
    re.IGNORECASE,
)
# The store's word for a product that is more than one thing, on both live
# bundles. It has to be read from the PRODUCT title, not the descriptor: one
# of the two (`Nourished By Time Bundle`) is a record-plus-record whose
# variant tails read `Crystal Clear LP` and `LP` -- indistinguishable from an
# ordinary pressing, and priced $51.25 against the $28 the same record costs
# on its own product page.
_BUNDLE_RE = re.compile(r"\bbundles?\b", re.IGNORECASE)
# Shopify's placeholder for a product with exactly one variant. Unlike the
# sibling stores, a product carrying it here names NO format anywhere --
# `product_type` is `Album`/`Single`/`EP`, never `Vinyl` -- so it is not
# evidence of a record and is skipped rather than admitted -- and because
# it is the one thing that empties the format claim store-wide without
# touching any other field, it is what the format-source guard below fires on.
_PLACEHOLDER_VARIANT = "default title"


class Crawler:
    site_name: str = "XL Recordings"
    base_url: str = "https://shopusa.xlrecordings.com"
    genre_summary: str = "Richard Russell's London label, run out of its US storefront — Radiohead and The Smile, Thom Yorke and Jonny Greenwood, Adele, The Prodigy, Dizzee Rascal, Arca, Overmono, Four Tet and Peggy Gou — with a deep 12\" singles shelf beside the albums."
    genre: str = "rock"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        products_seen = 0
        artist_resolved = 0
        claims_vinyl = 0
        identity_missing = 0
        variant_identity_missing = 0
        unreadable_stock = 0
        yielded = 0
        priced = 0
        async for product in iter_products(
            self.base_url, _COLLECTION_SLUG, min_delay=_SITE_CRAWL_DELAY
        ):
            products_seen += 1
            # Taken across every product, merch included, and before the
            # record classification below: `vendor` and the tags are the only
            # two artist sources, and this is the tally that notices BOTH
            # going dark at once. Counting it only over records would let a
            # catalog that legitimately filled up with CDs satisfy it.
            if self._artist(product):
                artist_resolved += 1

            if not (product.get("title") or "").strip():
                # A title-less product cannot be classified at all -- it is
                # the album name AND half the identity, and nothing below can
                # say anything about it, so it might have been a record.
                # Counted here rather than inside the gate, or a partial loss
                # of `title` would leave an empty walk looking like a shelf
                # that had merely sold out, and the snapshot would be deleted.
                identity_missing += 1
            elif self._is_merch(product) or self._is_bundle(product):
                # Read and deliberately skipped: the store's own claim that
                # this is not a single record. Either could not yield a row
                # however its variants read, so a failed classification on one
                # is evidence of nothing, and this test comes first for that
                # reason.
                pass
            elif self._unusable_variants(product):
                # BEFORE the vinyl claim below, because that claim rests
                # ENTIRELY on the variant descriptors -- this store publishes
                # no product-level format field at all. Blank the descriptors
                # and an in-stock record classifies itself as a non-record, is
                # exempted, and an empty walk goes unguarded, at which point
                # replace_stock_items() deletes a snapshot that was correct.
                variant_identity_missing += 1
            elif not self._claims_vinyl(product):
                # No variant of this product names a record, so it is the CD
                # single or the slipmat beside them. Safe to exempt now that a
                # dropped variant cannot be what silenced the claim.
                pass
            else:
                claims_vinyl += 1
                # A record. These tallies are nested here because only such a
                # product could have yielded a row -- a mis-shelved T-shirt's
                # missing handle says nothing about whether this walk's
                # emptiness can be trusted.
                if not self._has_identity(product):
                    identity_missing += 1
                elif not self._has_readable_stock_flag(product):
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
        # carrying what this crawler reads. The tallies above are taken before
        # the availability filter, so a sold-out product still counts toward
        # every one of them; `yielded` and `priced` are necessarily counted
        # after it, which is why the guards reading them are each conditioned
        # on a second tally rather than on emptiness alone -- a store that has
        # simply sold out is empty legitimately.
        if products_seen == 0:
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection returned no products -- renamed, removed, or markup drift")
        if yielded and not priced:
            # Rows without the emptiness: `_price` answers None for a value it
            # cannot use, so a `price` field removed or retyped store-wide
            # re-lists the whole catalog with no prices, which is worse than
            # the snapshot it would replace. Isolated nulls stay tolerated.
            raise RuntimeError(
                f"none of the {yielded} rows from the {_COLLECTION_SLUG} collection carries a "
                "price -- price-source drift")
        # Everything below is gated on the walk having produced nothing,
        # because that is the only outcome that destroys anything: rows on the
        # way out are proof that every source this crawler reads still
        # answers. They are ordered most specific first, since several fire
        # together on a single cause and the first one's message is the
        # diagnosis -- losing the artist sources also empties `_has_identity`,
        # so the narrower guard has to be asked first or its cause is
        # reported as the broader one.
        if not yielded and artist_resolved == 0:
            # `vendor` and the artist tag are the only two sources of an
            # artist, and a row without one is never emitted, so this is the
            # guard that notices the store restyling either into something
            # unreadable: every record would then be skipped while the walk
            # still completed.
            raise RuntimeError(
                f"no product in the {_COLLECTION_SLUG} collection names an artist in `vendor` or "
                "its tags -- artist-source drift")
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
                "or no readable artist -- identity-source drift")
        if not yielded and variant_identity_missing:
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while "
                f"{variant_identity_missing} product(s) carry no variants at all, or dropped one "
                "that carries no usable title without being provably sold out -- "
                "variant-identity drift")
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
        if not yielded and claims_vinyl == 0:
            # Unlike the sibling Shopify crawlers, the format gate here is
            # POSITIVE at the product level: nothing but a variant descriptor
            # says a product is a record, since `product_type` is the release
            # kind (`Album`/`Single`/`EP`) rather than the medium. A positive
            # signal CAN silently empty the walk when it disappears, which is
            # exactly what those crawlers' comments say a negative gate cannot
            # -- so the guard they deliberately omit is required here. Asked
            # LAST because it is the broadest: a catalog whose titles or
            # variants went unreadable also stops claiming a format, and those
            # causes name themselves above.
            raise RuntimeError(
                f"no product in the {_COLLECTION_SLUG} collection has a variant naming a record "
                "-- format-source drift")

    @classmethod
    def _items(cls, product: dict) -> List[dict]:
        if not cls._is_record(product):
            return []
        artist = cls._artist(product)
        album = " ".join((product.get("title") or "").split())
        if not cls._has_identity(product):
            return []
        url = f"{cls.base_url}/products/{(product.get('handle') or '').strip()}"
        # No ` (Pre-Order)` marker, though the store does tag one. compute_item_key
        # hashes artist, title and URL, so a marker that disappeared when the
        # record shipped would re-key every one of its pressings at exactly the
        # moment a waiting user cares most, orphaning the saves and judgments
        # held against the old key. A pre-order is purchasable at a real price
        # and is listed as an ordinary row.
        items = []
        for variant, descriptor in cls._pressings(product):
            if not cls._is_vinyl(descriptor):
                continue
            # Only the literal True admits a variant: the string "false" is
            # truthy, so a falsiness test would publish a sold-out record as
            # in stock. Anything else -- False, "false", 1, None, absent -- is
            # skipped, which is also what keeps this filter and
            # _has_readable_stock_flag agreeing on what "readable" means.
            if variant.get("available") is not True:
                continue
            # The descriptor is appended on every row that names one, not only
            # when the product has more than one variant: a sibling being
            # listed or delisted must not re-title the rows and orphan the
            # listings, judgments and saves keyed on the old identity.
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
    def _is_record(cls, product: dict) -> bool:
        """Is this product a single record this crawler should publish?"""
        if not (product.get("title") or "").strip():
            return False
        if cls._is_merch(product) or cls._is_bundle(product):
            return False
        if not cls._artist(product):
            return False
        return cls._claims_vinyl(product)

    @staticmethod
    def _is_merch(product: dict) -> bool:
        """The store's own claim that a product is not a record.

        `product_type` carries the release kind here (`Album`, `Single`, `EP`)
        rather than the medium, so `Merch` is the one value that classifies
        anything -- and it is the only test in this crawler that reads no
        title at all, neither the product's nor a variant's, which is what
        keeps it working on a product whose every other field has drifted. It
        is not sufficient on its own: the store leaves some of its merch
        untyped (`Celeste T-Shirt`, `Temporary MA1 Flight Jacket`), and those
        are caught by their descriptors instead.
        """
        return (product.get("product_type") or "").strip().lower() == "merch"

    @classmethod
    def _is_bundle(cls, product: dict) -> bool:
        """A product that is more than one thing, so its price is no single record's.

        Two independent signals, because each covers a case the other misses
        and the store types its bundles by hand -- it leaves both of the live
        ones' `product_type` blank.

        The word in the product title catches both. The option count catches a
        bundle that does not say so: Shopify gives a product one option per
        thing being chosen, and every record in this store has exactly one
        (`Format`), while the record-plus-T-shirt bundle has two. Neither is
        read from the descriptor, because the descriptor is the tail of the
        variant title and a bundle's tail belongs to whichever half was
        written last -- `Rooty - Blue & Pink 2X LP / Rooty Anniversary
        T-shirt - Black Small` tails as `Black Small`, naming the shirt and
        hiding the record, and its sibling would tail as an ordinary `LP`.
        """
        if _BUNDLE_RE.search(_fold_marks(product.get("title") or "")):
            return True
        options = [o for o in product.get("options") or [] if isinstance(o, dict)]
        return len(options) > 1

    @classmethod
    def _artist(cls, product: dict) -> str:
        """Who made the record -- "" when nothing in the payload says.

        `vendor` first, and only the store's own name falls through to the
        tags. That precedence is not a preference for the richer field: where
        both name an artist they agree on all but two live products, and on
        both of those `vendor` is the correct one. The store's tags are a flat
        list with no escaping, so `Tyler, The Creator` is stored as the two
        tags `Tyler` and `The Creator` -- which arrive alphabetised, so the
        comma cannot even be put back -- and `Gil Scott-Heron & Jamie xx` is
        tagged with the first name alone.

        A tag is only consulted when exactly one survives the subtraction. Two
        survivors mean the crawler cannot say which is the artist, and naming
        the wrong one is worse than naming none: the artist is hashed into
        item_key and is what db._library_release_match_sql matches a row
        against the user's library, so a guess produces a row that is both
        permanently mis-keyed and unmatchable.
        """
        vendor = " ".join((product.get("vendor") or "").split())
        if not vendor:
            # Unreadable, NOT a claim that the label owns the record -- and so
            # not the case the tag fallback exists for. Falling through here
            # would silently re-credit exactly the products vendor leads for:
            # `We're New Here` would drop from the full billing to the single
            # collaborator its tag names, re-keying the row. Nothing would
            # notice, because the rows still yield and so `artist_resolved`
            # stays non-zero. Answering "" instead skips the product, and if
            # `vendor` went blank store-wide that guard fires and the snapshot
            # survives -- the loud failure rather than the quiet one.
            return ""
        candidates = cls._artist_tags(product)
        if not cls._is_label_vendor(vendor):
            return cls._leading_credit(vendor, candidates)
        return candidates[0] if len(candidates) == 1 else ""

    @staticmethod
    def _leading_credit(vendor: str, candidates: List[str]) -> str:
        """`vendor`, reduced to its first credit where the tags name one.

        The library match is what decides this, not completeness.
        discogs.parse_release() keeps `artists[0]` alone, and
        db._library_release_match_sql() compares the artist for EQUALITY --
        only the title is matched by prefix. So a row billed
        `Gil Scott-Heron & Jamie xx` cannot match a catalog row holding
        `Gil Scott-Heron`, and the record silently drops out of the
        Collection/Wantlist filters and out of the crawl_library_only gate.
        The full billing is the better NAME and the worse KEY, and this field
        is a key.

        Narrow on purpose, because the failure it must not cause is the
        opposite one -- truncating a single artist whose name merely begins
        with the tag. Three conditions have to hold together: exactly one tag
        survives, it is a leading prefix of the vendor, and what follows it is
        a separator between credits rather than part of a name. `Tyler, The
        Creator` fails all three ways that matter -- it carries two tags,
        because Shopify split its comma, and a comma is not a separator here.
        """
        if len(candidates) != 1:
            return vendor
        tag = candidates[0]
        if not tag or not vendor.lower().startswith(tag.lower()):
            return vendor
        if not _COLLABORATION_SEPARATOR_RE.match(vendor[len(tag):]):
            return vendor
        return tag

    @staticmethod
    def _is_label_vendor(vendor: str) -> bool:
        return _NON_ALNUM_RE.sub("", vendor.lower()).startswith(_LABEL_VENDOR_PREFIX)

    @staticmethod
    def _artist_tags(product: dict) -> List[str]:
        """The product's tags with the store's non-artist shelves subtracted."""
        out = []
        for tag in product.get("tags") or []:
            if not isinstance(tag, str):
                continue
            tag = " ".join(tag.split())
            if not tag or _YEAR_TAG_RE.match(tag) or tag.lower() in _NON_ARTIST_TAGS:
                continue
            out.append(tag)
        return out

    @classmethod
    def _claims_vinyl(cls, product: dict) -> bool:
        """Does any of the product's variants name a record?

        The only format claim this store makes. The sibling Shopify crawlers
        have a second one in `product_type`, and lean on it for the products
        whose variants name no format; here `product_type` is the release kind
        and never the medium, so there is nothing to fall back to and a
        product naming no format on any variant is not published at all.
        """
        return any(cls._is_vinyl(descriptor) for _, descriptor in cls._pressings(product))

    @staticmethod
    def _is_vinyl(descriptor: str) -> bool:
        """Is this variant the record, rather than the CD or the T-shirt beside it?

        Merch rejects first, then a record word admits outright, then a word
        naming another medium rejects, and anything else is admitted. Negative
        at the tail so a pressing the store describes in words no list
        anticipated stays in by default -- which is not hypothetical: the live
        `Picture Disc` is a record, names neither `LP` nor `Vinyl`, and is the
        one live descriptor that reaches that final line. Enumerating the
        record words instead would silently drop it.

        The merch tests come FIRST, ahead of the record word, so that a
        garment cannot be admitted by a format named alongside it. They are
        read against the descriptor alone and never the product title, so an
        album that legitimately names one of these words keeps its rows.

        An empty descriptor is NOT a record. It is what Shopify's
        `Default Title` placeholder folds to, and on this store that means no
        format was named anywhere -- there is no `product_type` claim to fall
        back on, so admitting it would publish a CD, a cassette or a tote bag
        as a record on no evidence at all.
        """
        # Every pattern below is matched against the mark-folded descriptor,
        # never the raw one, so that a decomposed `é` and its precomposed twin
        # decide the same way. Folding is for matching only; nothing emitted
        # goes through it.
        folded = _fold_marks(descriptor).strip()
        if not folded:
            return False
        if _MERCH_RE.search(folded) or _GARMENT_SIZE_RE.search(folded):
            return False
        if _VINYL_WORD_RE.search(folded):
            return True
        return not _OTHER_MEDIA_RE.search(folded)

    @classmethod
    def _pressings(cls, product: dict) -> List[Tuple[dict, str]]:
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
                # Kept as a pressing with an empty descriptor, and only as a
                # product's sole variant: on a multi-variant product the
                # placeholder is malformed data. _is_vinyl rejects the empty
                # descriptor, so it never yields a row -- it is kept so that
                # _has_readable_stock_flag and the drift tallies can still see
                # the variant rather than mistaking the product for one that
                # has no variants at all.
                if len(variants) == 1:
                    pairs.append((variant, ""))
                continue
            pairs.append((variant, cls._descriptor(title)))
        return pairs

    @staticmethod
    def _descriptor(variant_title: str) -> str:
        parts = _SPLIT_RE.split(variant_title)
        # A variant title with no separator is the descriptor entire: the
        # store's untyped merch names a bare `S`/`M`/`L`/`XL` that way.
        return parts[-1].strip() if len(parts) > 1 else variant_title.strip()

    @classmethod
    def _has_identity(cls, product: dict) -> bool:
        return (
            bool((product.get("title") or "").strip())
            and bool((product.get("handle") or "").strip())
            and bool(cls._artist(product))
        )

    @classmethod
    def _unusable_variants(cls, product: dict) -> bool:
        """Did this product fail to supply variant data that can be trusted?

        Two ways. It carries no variants at all, or one was dropped for want
        of a usable title without being provably sold out.

        Only the literal False proves it. Without this, a blank-titled
        in-stock variant beside a sold-out sibling makes an in-stock product
        look sold out, and the walk's emptiness would be trusted. Runs before
        _has_readable_stock_flag, which reads only the KEPT pressings and is
        complete precisely because this has already established that every
        dropped entry is a readable False: the two are a pair and must stay in
        that order.
        """
        variants = product.get("variants") or []
        if not variants:
            # No variants is not "nothing to sell". It is the availability,
            # the format and the price all absent at once, so the product
            # cannot be proven sold out and cannot be shown not to have been a
            # record. Left out, it reaches none of the tallies: it claims no
            # format, so the branch below exempts it, and
            # _has_readable_stock_flag -- which does answer False for it --
            # is only ever asked about a product that claims one. One
            # readably sold-out record elsewhere then keeps `claims_vinyl`
            # non-zero, every other tally stays 0, and the empty walk deletes
            # the snapshot with nothing raised.
            return True
        kept = [variant for variant, _ in cls._pressings(product)]
        for variant in variants:
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
        # The flags themselves are read through the SAME format gate _items
        # publishes through, because the question this answers is whether a
        # VINYL row could have been missed. This store sells the record, the
        # CD and the cassette as variants of one product, so reading every
        # titled variant lets a CD's unreadable flag condemn a record that was
        # readably sold out: the walk raises, and the previous snapshot -- the
        # stale in-stock rows -- survives instead of being cleared.
        #
        # all(), not any(): one readable variant does not make a product
        # readable, or a product whose black pressing is a readable False and
        # whose coloured pressing carries the string "false" would vouch for
        # an emptiness half its own doing.
        if not (product.get("variants") or []):
            return False
        return all(
            isinstance(variant.get("available"), bool)
            for variant, descriptor in cls._pressings(product)
            if cls._is_vinyl(descriptor)
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
