import math
import re
from typing import AsyncIterator, Optional, Tuple
from shopify_catalog import iter_products, resolve_cover_image

# The store's own format shelf, at the URL the request named. It is exactly
# the store's records in both directions, confirmed 2026-09-07: every product
# in it is typed 12", 2x12" or 7", and every product in the whole store
# carrying one of those types is in it. `collections.json` reports a
# products_count larger than the published catalog -- it counts products not
# published to the online store -- so the walk's own exhaustion is the
# catalog, confirmed to return the same product ids at limit=250 and limit=50.
_COLLECTION_SLUG = "vinyl"
# Every character this crawler treats as a quote. One definition, used by the
# title regex's two exclusions, by the stray-quote check and by the
# descriptor's one-quote cap -- because each time these were spelled out
# separately they drifted apart, and every such disagreement has been a bug:
# a left curly the gate would not accept as an inch marker but the stray check
# exempted, then a double prime the stray check rejected but the album group
# still admitted. Found in review on PR #323, twice.
_QUOTE_CHARS = '"“”″'
_OPENING_QUOTES = '"“'   # the two a title can open an album with
# `Artist "Album" <format>`. Every SINGLE-ITEM product in the store leads with
# `Artist "Album"`, records and CDs and shirts alike; the store's multi-item
# bundles do not, which is why _bundle_shaped below has to recognise them
# separately. The trailing format is universal only on the vinyl shelf --
# books, pins and stickers stop at the quoted album -- so every live title IN
# THIS COLLECTION carries exactly three quotes: the album's opening one, its
# closing one, and the format's inch marker. That asymmetry is what the
# both-halves-required rule below turns into a filter.
#
# The two character classes are ASYMMETRIC, deliberately, and both are built
# from the constants above rather than spelled out so they cannot fall out of
# step with the rest of the crawler's idea of a quote:
#
#   leading group -- excludes only the two characters that can OPEN an album
#     (`"` and `“`). That is what makes the album's opening quote always the
#     title's first, so a trailing inch marker can never be read as one. It
#     deliberately still ADMITS `”` and `″`, because neither can open a
#     quotation: excluding them would only reject an artist name containing
#     one, and this group's capture is discarded anyway -- the credit comes
#     from `vendor`. Non-capturing for that reason, and allowed to be empty,
#     since a title that omits the artist (`"Album" 12"`) still carries a
#     readable album and format and the row built from `vendor` is correct.
#
#   album group -- excludes EVERY quote there is, so a fourth quote is junk
#     rather than an album and `Artist "" 12"` parses to nothing instead of
#     to an album of `" 12`.
#
# What the closing quote's lookahead adds is a rejection rather than a choice:
# the quote must be followed by whitespace, a digit or the end, so a closing
# quote glued to a letter (`"Fire"X`) fails to parse rather than being guessed
# at. It does NOT by itself reject a nested quotation -- `"The " Big"` has
# whitespace after the inner quote and satisfies it -- which is what
# _descriptor_quotes_are_clean is for. The `\d` arm keeps a descriptor glued
# onto the closing quote (`"Album"12"`) readable; this store does not write it
# but a sibling Shopify store does. Curly quotes are admitted though the store
# writes none: they are the commonest way a storefront's copy drifts.
_TITLE_RE = re.compile(
    r'^(?:[^' + re.escape(_OPENING_QUOTES) + r']*?)\s*[' + re.escape(_OPENING_QUOTES) + r']'
    r'(?P<album>[^' + re.escape(_QUOTE_CHARS) + r']+?)'
    r'["”](?=[\s\d]|$)\s*'
    r'(?P<rest>.*)$'
)
# The store sells bundles (`Bad Moves LP + Shirt`, `Bad Moves Vinyl Bundle`).
# None is shelved here and none carries a quoted album, so the title parse
# already excludes every live one -- but a bundle written to the store's usual
# convention (`Bad Moves "Untenable" Vinyl Bundle`) would parse, and its
# descriptor's own `Vinyl` would then admit it. A bundle is not a Discogs
# release and its price is not any record's price.
#
# Read against the DESCRIPTOR, like the combo rule, and not the whole title:
# scanning the title discards an album that legitimately contains the word
# (`Artist "Bundle of Joy" 12"`), silently dropping a stock row. Found in
# review on PR #323.
_BUNDLE_RE = re.compile(r"\bbundles?\b", re.IGNORECASE)
# What a quote is allowed to be in a descriptor: part of one complete inch
# marker, and nothing else. Every live descriptor is exactly that (`12"`,
# `2x12"`, `7"`).
#
# The token is shared with _VINYL_WORD_RE below rather than approximated,
# which is the whole point. A "preceded by a digit" lookbehind stood in for it
# through three review passes and was wrong in both directions: it accepted
# `Studio54" LP`, where the quote is embedded in a word and the title is the
# nested-quote shape this rejects, and it refused `12 "`, which the format
# gate reads as an inch marker perfectly well. Approximating one rule inside
# another is what produced every quote bug on this crawler. Found in review on
# PR #323.
_INCH_MARKER = (
    r'(?<![a-z0-9])(?:\d+\s*[x×]\s*)?\d{1,2}\s*'
    # The quote glyph needs a right-hand boundary of its own, which the
    # spelled-out `inch` alternative gets free from its `\b`: without one,
    # `12"CD` reads as a complete inch marker, the format gate admits it
    # before ever noticing the `CD`, and the quote check sees nothing left
    # over to object to. Found in review on PR #323.
    r'(?:["”″](?![A-Za-z0-9])|inch(?:es)?\b)'
)
_INCH_MARKER_RE = re.compile(_INCH_MARKER, re.IGNORECASE)
# Exemption-only, and narrower than _BUNDLE_RE on purpose -- see
# _bundle_shaped for why the two differ.
_TERMINAL_BUNDLE_RE = re.compile(r"\bbundles?\s*$", re.IGNORECASE)
# The shelf has already said the product is a record, so the descriptor gate
# is negative: a record word admits outright, then a word naming another
# medium or a merch item rejects, and anything else is admitted on the
# collection's own claim. Negative rather than enumerated so a format the
# store adds later (10", a box set) stays in by default.
_VINYL_WORD_RE = re.compile(
    r'(?<![a-z])(?:\d+(?:\.\d+)?\s*[x×]\s*)?lps?\b|\bvinyls?\b'
    r'|\bpicture\s+discs?\b|\btest\s+pressings?\b'
    r'|' + _INCH_MARKER,
    re.IGNORECASE,
)
# Not an invented vocabulary: these are the store's own `product_type` values
# for everything it sells that is not a record, read off the `all` collection
# -- CD, 2xCD, Cassette, T-Shirt, Girls T-shirt, Tank Top, Longsleeve,
# Crewneck Sweatshirt, Books, Paperback Book, Hardcover Book, Pins, Bag,
# Stickers & Decals -- plus `Zine`, which is not a product_type but is the
# descriptor of the one non-record in the store that names a format at all
# (`Liz Pelly "P.S. Eliot: 2007-2011" Zine`). A mis-shelved one would parse
# perfectly and publish as a record, since the store titles these exactly
# like the records (`Bad Moves "Logo" T-Shirt`).
_OTHER_MEDIA_RE = re.compile(
    r"(?<![a-z])(?:\d+\s*[x×]\s*)?(?:cds?|cassettes?|dvds?|blu-?\s?rays?)\b",
    re.IGNORECASE,
)
_MERCH_RE = re.compile(
    r"\b(?:t-?\s?)?shirts?\b|\btees?\b|\btank\s+tops?\b|\blongsleeves?\b"
    r"|\bsweatshirts?\b|\bcrewnecks?\b|\bhoodies?\b"
    r"|\bbooks?\b|\bpaperbacks?\b|\bhardcovers?\b|\bzines?\b"
    r"|\bpins?\b|\bstickers?\b|\bdecals?\b|\bbags?\b|\btotes?\b",
    re.IGNORECASE,
)
# Shopify's placeholder for a product with exactly one variant. The live
# catalog has none -- every variant names a colour -- but it names no pressing
# if one appears, so a row built on it carries the composed title alone, and
# only when it IS the product's sole variant: on a multi-variant product the
# placeholder is malformed data, and a blank title is never a pressing.
# Either would otherwise share the title and the product URL, and so the
# item_key, with every sibling built the same way.
_PLACEHOLDER_VARIANT = "default title"


def _descriptor_quotes_are_clean(descriptor: str) -> bool:
    """Is every quote in this descriptor part of one complete inch marker?

    Two ways a descriptor's quotes say the parse went wrong, and both are the
    tail of a nested quotation the album group stopped short of. More than one
    inch marker (`Artist "The " 54" 12"` leaves `54" 12"`), or a quote that is
    not inside one at all (`Artist "The " Big" 12"` leaves `Big" 12"`, and
    `Studio54"` embeds it in a word). Either way the album is severed and the
    surviving inch marker would admit the row through the format gate.

    Judged against _INCH_MARKER_RE, the same token the gate uses, so the two
    cannot disagree about what an inch marker is -- every quote bug on this
    crawler came from one rule approximating the other.
    """
    markers = _INCH_MARKER_RE.findall(descriptor)
    if len(markers) > 1:
        return False
    return not any(q in _INCH_MARKER_RE.sub(" ", descriptor) for q in _QUOTE_CHARS)


class Crawler:
    site_name: str = "Don Giovanni Records"
    base_url: str = "https://dongiovannirecords.com"
    genre_summary: str = "The New Brunswick, New Jersey punk label now run out of Philadelphia — Screaming Females, Downtown Boys, Laura Stevenson, Bad Moves, Alice Bag and Teenage Halloween — alongside the free-jazz and experimental side that brought in Moor Mother and Irreversible Entanglements."
    genre: str = "punk"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        products_seen = 0
        artist_ok = 0
        parsed_ok = 0
        sources_ok = 0
        unclassifiable = 0
        identity_missing = 0
        variant_identity_missing = 0
        unreadable_stock = 0
        yielded = 0
        priced = 0
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            products_seen += 1
            # The artist and album tallies are independent of each other and
            # of the format gate, because `vendor` and the title are
            # independent sources: nested, one going dark would hide behind
            # the other still working, and gated, a shelf that legitimately
            # filled up with CDs would raise source drift.
            has_artist = bool(self._artist(product))
            album, descriptor = self._parse_title(product.get("title"))
            if has_artist:
                artist_ok += 1
            if album:
                parsed_ok += 1
            if has_artist and album:
                # The two tallies above are independent on purpose, so that
                # one source going dark cannot hide behind the other still
                # working. But independence alone lets them be satisfied by
                # *different* products: one with a vendor and an unreadable
                # title, another with a readable title and no vendor, leaves
                # both non-zero while no product has what a row needs. This
                # third tally is what makes that case raise. Taken before the
                # format gate, so a shelf that legitimately filled up with
                # CDs still satisfies it. Found in review on PR #323.
                sources_ok += 1

            title_text = " ".join((product.get("title") or "").split())
            if not title_text:
                # A title-less product cannot be classified at all -- the
                # parse reads the title, so nothing below can say anything
                # about it, and it might have been a record. Counted, or a
                # partial loss of `title` would leave an empty walk looking
                # like a shelf that merely sold out.
                identity_missing += 1
            elif (album and not self._is_vinyl(descriptor)) or (
                    not album and self._bundle_shaped(title_text)):
                # Read and deliberately skipped: a CD, a shirt, a bundle.
                # None of them could yield a record whatever its `vendor`
                # says, so a source failing on one is evidence of nothing --
                # counted, a blank-vendored CD would raise on a shelf that
                # had merely sold out, keeping stale rows alive. This test
                # comes FIRST for that reason. Found in review on PR #323.
                pass
            elif not has_artist or not album:
                # Never read at all: one of the two sources failed on this
                # product, so the crawler cannot say whether it was a record.
                # The catalog-wide tallies above only notice a source
                # vanishing from EVERY product; one well-formed sold-out
                # record keeps them non-zero while this one goes uncounted.
                unclassifiable += 1
            else:
                # A record: both sources read, and the format gate admits it.
                # These tallies are nested here because only such a product
                # could have yielded a row -- a mis-shelved shirt's missing
                # handle says nothing about whether this walk's emptiness can
                # be trusted.
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
        if artist_ok == 0:
            # `vendor` is the only artist source, deliberately: the title's
            # own artist prefix is not a fallback, because a fallback would
            # keep this guard from ever firing and would re-key every row
            # whose credit the two spell differently -- starting with the one
            # the store truncates mid-word in the title.
            raise RuntimeError(
                f"no product in the {_COLLECTION_SLUG} collection carries a vendor -- artist-source drift")
        if parsed_ok == 0:
            raise RuntimeError(
                f'no product in the {_COLLECTION_SLUG} collection has a title of the form '
                'Artist "Album" format -- album-source drift')
        if sources_ok == 0:
            # Reached only when both sources are alive somewhere but never on
            # the same product, which neither guard above can see.
            raise RuntimeError(
                f"no product in the {_COLLECTION_SLUG} collection carries both a vendor and a "
                "readable album -- combined-source drift")
        if yielded and not priced:
            # Rows without the emptiness: `_price` answers None for a value
            # it cannot use, so a `price` field removed or retyped
            # store-wide re-lists the whole catalog with no prices, which is
            # worse than the snapshot it would replace. Isolated nulls stay
            # tolerated.
            raise RuntimeError(
                f"none of the {yielded} rows from the {_COLLECTION_SLUG} collection carries a "
                "price -- price-source drift")
        if not yielded and unclassifiable:
            # Same empty-outcome gate as the two below, and for the same
            # reason: among real rows an unreadable product is an ordinary
            # skipped row, but it must never be what an empty result rests on.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while "
                f"{unclassifiable} product(s) could not be read as record or not -- "
                "classification drift")
        if not yielded and identity_missing:
            # `title` and `handle` are identity, not display: item_key hashes
            # the row's artist, title and URL, so a product missing either is
            # skipped rather than emitted under a fresh identity that would
            # orphan the judgments and saves keyed on its old one. Skipped
            # rows leave the walk looking sold out, which is why the same
            # empty-outcome gate as the stock guard below applies.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while "
                f"{identity_missing} record(s) carry no title or handle -- identity-source drift")
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
        # No ` (Pre-Order)` marker, though the store tags its pre-orders and
        # the tag is trustworthy. compute_item_key hashes artist, title and
        # URL, so a marker that disappears when the record ships would re-key
        # every one of its pressings at exactly the moment a waiting user
        # cares most, orphaning the saves and judgments held against the old
        # key. That is the same churn the colour rule below refuses, and it
        # would be inconsistent to accept it here. The bundled crawlers are
        # split on this; earache.py and spkr.py omit it on these grounds.
        items = []
        for variant, colour in cls._pressings(product):
            # Only the literal True admits a variant: the string "false" is
            # truthy, so a falsiness test would publish a sold-out record as
            # in stock. Anything else -- False, "false", 1, None, absent --
            # is skipped, which is also what keeps this filter and
            # _has_readable_stock_flag agreeing on what "readable" means.
            # No pre-order bypass: an unavailable pre-order is a closed
            # allocation, not a purchasable row.
            if variant.get("available") is not True:
                continue
            # The colour is appended on every row that names one, not only
            # when the product has more than one variant: a sibling being
            # listed or delisted must not re-title the rows and orphan the
            # listings, judgments and saves keyed on the old identity. The
            # placeholder is the one exception, because it names nothing,
            # and _pressings admits it only as a product's sole variant, so
            # no sibling can share the bare title.
            items.append({
                "artist": artist,
                "title": f"{title} — {colour}" if colour else title,
                "format": "Vinyl",
                "price": cls._price(variant),
                "currency": "USD",
                "url": url,
                "cover_image_url": resolve_cover_image(product, variant),
            })
        return items

    @classmethod
    def _record(cls, product: dict) -> Optional[Tuple[str, str]]:
        """(artist, title) for a product this crawler reads as a record, else None."""
        artist = cls._artist(product)
        if not artist:
            return None
        album, descriptor = cls._parse_title(product.get("title"))
        if not album:
            return None
        if not cls._is_vinyl(descriptor):
            return None
        return artist, f"{album} {descriptor}"

    @staticmethod
    def _bundle_shaped(title: str) -> bool:
        """Is this title one of the store's bundles, written without a quoted album?

        Only for the `unclassifiable` tally, never for rejecting a row: a
        title that does not parse is skipped either way, and this decides
        whether that skip is a known shape or evidence of drift.

        Two shapes, matching the two rules the format gate applies to a
        descriptor. A TERMINAL `Bundle`/`Bundles` (`Bad Moves Vinyl Bundle`),
        and a `+` joining something to merch (`Bad Moves LP + Shirt`,
        `Bad Moves Shirt + All Vinyl`) -- the store's live combos, which carry
        no quoted album and so never reach the gate at all.

        Both shapes are deliberately narrower here than the gate's own rules,
        because the two directions of error cost differently. In the gate, a
        false positive skips one row; here, a false exemption lets an
        unreadable product pass for a known shape and the whole snapshot be
        deleted. So the word must END the title, or a real record that lost
        its album quotes (`Amy Klein Bundle of Joy 12"`) would be waved
        through; and the second shape needs BOTH halves, or a record title
        that lost its quotes but kept a `+` in the artist credit
        (`Lee Bains + The Glory Fires Youth Detention 12"`) would be too.
        Found in review on PR #323, over two passes.
        """
        # The precondition the docstring names is enforced, not assumed: a
        # title carrying any quote is not one of these shapes, and a record
        # that lost only its format (`Artist "Pins + Needles"`) would
        # otherwise satisfy the second heuristic and be waved through as a
        # known bundle. Found in review on PR #323.
        if any(q in title for q in _QUOTE_CHARS):
            return False
        return bool(_TERMINAL_BUNDLE_RE.search(title)) or (
            "+" in title and bool(_MERCH_RE.search(title)))

    @staticmethod
    def _artist(product: dict) -> str:
        # `vendor` is a real credit on every product here -- never blank,
        # never the label's own name -- and the title carries the same credit
        # truncated mid-word once it runs long, so this field is never worse
        # and sometimes better. There is no fallback to the title's prefix;
        # see the artist-source guard for why.
        return " ".join((product.get("vendor") or "").split())

    @staticmethod
    def _parse_title(title) -> Tuple[str, str]:
        """The album and format in `Artist "Album" format` -- ("", "") when the title is not of that form.

        The artist is not returned, because it is not read from here: the
        credit comes from `vendor`. Returning it once meant the album-source
        guard could tally the artist prefix while row emission gated on the
        album, so a store that dropped its artist prefixes would have raised
        on a catalog this crawler could still read perfectly.

        The format is kept, and kept after the album, on both counts
        deliberately. title_key folds a disc size away for the Cheapest
        filter, so keeping it costs nothing there; and the library match
        behind the Store tab's Collection and Wantlist filters is
        exact-or-prefix-with-space against the catalog title, which
        `Kiss Big 12"` satisfies for a library `Kiss Big` only while the
        album leads.
        """
        collapsed = " ".join((title or "").split())
        m = _TITLE_RE.match(collapsed)
        if m is None:
            return "", ""
        album = m.group("album").strip()
        descriptor = m.group("rest").strip()
        # Both halves are required, because the convention is both halves.
        # Every live record names its format; the store's books, pins and
        # stickers are precisely the products that stop at the quoted title
        # (`Larry Livermore "Spy Rock Memories"`), and mis-shelved here they
        # would otherwise reach a format gate that admits an unrecognised
        # descriptor by default. Rejecting silence rather than admitting it
        # costs no live row and needs no new guard: a store-wide loss of the
        # trailing format empties `parsed_ok` and raises album-source drift,
        # which is exactly what that guard's message already claims to cover.
        if not album or not descriptor:
            return "", ""
        if not _descriptor_quotes_are_clean(descriptor):
            return "", ""
        return album, descriptor

    @staticmethod
    def _is_vinyl(descriptor: str) -> bool:
        # Read against the descriptor only, never the whole title, so an
        # album named `ABCD` or `Bag` cannot decide the format of the record
        # it names -- and so an album named `Pins + Needles` cannot trip the
        # combo rule below.
        #
        # A `+` joining a record to a merch item is a bundle, and it is
        # checked FIRST because the record word would otherwise admit it
        # outright. The store sells exactly these (`LP + Shirt`,
        # `Shirt + All Vinyl`); their price is a bundle's, not any record's.
        # `LP + Bonus CD` stays a record: one item, one price, and no merch
        # word in it.
        if _BUNDLE_RE.search(descriptor):
            return False
        if "+" in descriptor and _MERCH_RE.search(descriptor):
            return False
        if _VINYL_WORD_RE.search(descriptor):
            return True
        return not (_OTHER_MEDIA_RE.search(descriptor) or _MERCH_RE.search(descriptor))

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

    @classmethod
    def _unusable_dropped_variant(cls, product: dict) -> bool:
        """Was a variant dropped for want of a usable title without being provably sold out?

        The variant title is part of the row's identity, so a variant without
        one cannot be published -- but its absence must not read as a pressing
        that sold out. Left uncounted, a product whose unpublishable variant
        is in stock and whose named sibling is sold out yields nothing while
        every guard passes, and the snapshot is deleted.

        Only the literal False proves the dropped variant was safely sold out.
        Every other value -- True, the string "true", 1, None, absent -- leaves
        it unproven, and unproven is drift: a dropped variant carrying "true"
        is exactly as invisible as one carrying True, and the readable
        sold-out sibling beside it must not vouch for the emptiness. Found in
        review on PR #323.
        """
        kept = [v for v, _ in cls._pressings(product)]
        for variant in product.get("variants") or []:
            if not isinstance(variant, dict):
                # A junk entry carries no availability at all, so it can never
                # be proven sold out. _pressings drops it before anything
                # reads it, which is right for building rows and wrong for
                # trusting an empty one. Found in review on PR #323.
                return True
            if variant.get("available") is not False and not any(variant is k for k in kept):
                return True
        return False

    @classmethod
    def _has_readable_stock_flag(cls, product: dict) -> bool:
        # Emptiness is judged against the RAW variant set, not the pressings
        # kept from it. A product whose sole variant has a blank title and a
        # readable False is genuinely sold out, but keeps no pressings at
        # all, and keying on those raised stock drift over a product that
        # could be read perfectly -- preserving stale in-stock rows. A
        # product with no variants whatsoever still says nothing and stays
        # unreadable. Found in review on PR #323.
        #
        # Reads only the kept pressings for the flags themselves, which is
        # complete because _unusable_dropped_variant runs first in the caller
        # and has already established that every DROPPED entry is a literal
        # False. The two are a pair and must stay in that order.
        #
        # all(), not any(): one readable variant does not make the product
        # readable. A product whose black pressing is a readable False and
        # whose coloured pressing carries the string "false" yields nothing,
        # and under any() would vouch for an emptiness half its own doing.
        if not (product.get("variants") or []):
            return False
        return all(
            isinstance(v.get("available"), bool) for v, _ in cls._pressings(product))

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
