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
# `Artist "Album" <format>` -- the convention the whole store follows, records
# and CDs and shirts alike. Every live title carries exactly three quotes: the
# album's opening one, its closing one, and the format's inch marker.
#
# Neither the leading group nor the album group may contain a quote, which is
# what pins all three. The leading group excluding them makes the album's
# OPENING quote always the title's first, so the trailing inch marker can
# never be read as one. It is non-capturing: the credit comes from `vendor`,
# so whatever sits ahead of the album is never read, and it is deliberately
# allowed to be empty -- a title that omits the artist (`"Album" 12"`) still
# carries a readable album and format, and rejecting it would drop a row the
# crawler can build correctly from `vendor`. The album excluding quotes is
# what makes a fourth quote junk rather than an album, so `Artist "" 12"`
# parses to nothing instead of to an album of `" 12`.
#
# What the closing quote's lookahead adds, on top of those exclusions, is a
# rejection rather than a choice: the quote must be followed by whitespace,
# a digit or the end, so a closing quote glued to a letter (`"Fire"X`) fails
# to parse rather than being guessed at. It does NOT by itself reject a
# nested quotation -- `"The " Big"` has whitespace after the inner quote and
# satisfies the lookahead -- which is what _STRAY_QUOTE_RE below is for. The
# `\d` arm keeps a descriptor glued onto the closing quote (`"Album"12"`)
# readable; this store does not write it but a sibling Shopify store does.
# Curly quotes are admitted though the store writes none: they are the
# commonest way a storefront's copy drifts.
_TITLE_RE = re.compile(
    r'^(?:[^"“]*?)\s*["“]'
    r'(?P<album>[^"“”]+?)'
    r'["”](?=[\s\d]|$)\s*'
    r'(?P<rest>.*)$'
)
# The store sells bundles (`Bad Moves LP + Shirt`, `Bad Moves Vinyl Bundle`).
# None is shelved here and none carries a quoted album, so the title parse
# already excludes every live one -- but a bundle written to the store's usual
# convention (`Bad Moves "Untenable" Vinyl Bundle`) would parse, and its
# descriptor's own `Vinyl` would then admit it. A bundle is not a Discogs
# release and its price is not any record's price, so the rule sits ahead of
# both the parse and the gate.
_BUNDLE_RE = re.compile(r"\bbundles?\b", re.IGNORECASE)
# In this store's titles every quote left after the album is an inch marker,
# and an inch marker always follows its digits (`12"`, `2x12"`, `7"`). A quote
# anywhere else in the descriptor is the tail of a nested quotation the album
# group stopped short of -- `Artist "The " Big" 12"` otherwise parses to an
# album of `The` and a descriptor of `Big" 12"`, which the inch marker in that
# descriptor then admits as a record. The closing lookahead alone does not
# catch it, because the inner quote there IS followed by whitespace; it only
# rejects the glued-letter spelling (`"Fire"X`). Found in review on PR #323.
_STRAY_QUOTE_RE = re.compile(r'(?<![0-9])["“”]')
# The shelf has already said the product is a record, so the descriptor gate
# is negative: a record word admits outright, then a word naming another
# medium or a merch item rejects, and anything else is admitted on the
# collection's own claim. Negative rather than enumerated so a format the
# store adds later (10", a box set) stays in by default.
_VINYL_WORD_RE = re.compile(
    r'(?<![a-z])(?:\d+(?:\.\d+)?\s*[x×]\s*)?lps?\b|\bvinyls?\b'
    r'|\bpicture\s+discs?\b|\btest\s+pressings?\b'
    r'|(?<![a-z0-9])(?:\d+\s*[x×]\s*)?\d{1,2}\s*(?:"|”|″|inch(?:es)?\b)',
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
            # [0] is the album, which is exactly what _record gates a row on
            # -- so this tally cannot raise on a catalog the crawler could in
            # fact read.
            has_album = bool(self._parse_title(product.get("title"))[0])
            if has_artist:
                artist_ok += 1
            if has_album:
                parsed_ok += 1
            if has_artist and has_album:
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
                # A title-less product cannot be classified at all -- _record
                # reads the title, so it can never reach the checks below,
                # and it might have been a record. Counted here rather than
                # inside the gate, or a partial loss of `title` would leave
                # an empty walk looking like a shelf that merely sold out.
                # Found in review on PR #323.
                identity_missing += 1
            elif not has_artist or (not has_album and not _BUNDLE_RE.search(title_text)):
                # The product has a title, but one of the two sources failed
                # on *it*, so this crawler never classified it as record-or-
                # not. That is different from a CD or a bundle, which are
                # classified and then deliberately skipped -- and different
                # from the catalog-wide tallies above, which only notice a
                # source vanishing from EVERY product. One well-formed
                # sold-out record keeps those non-zero while an unreadable
                # in-stock product beside it goes uncounted, and the walk
                # completes empty. Found in review on PR #323.
                unclassifiable += 1
            # These are nested inside the gate, because only a product that
            # reads as a record could have yielded a row: a mis-shelved
            # shirt's missing handle says nothing about whether this walk's
            # emptiness can be trusted.
            elif self._record(product) is not None:
                if not self._has_identity(product) or self._unusable_dropped_variant(product):
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
        if _BUNDLE_RE.search(collapsed):
            return "", ""
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
        if _STRAY_QUOTE_RE.search(descriptor):
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
        return any(
            v.get("available") is not False and not any(v is k for k in kept)
            for v in product.get("variants") or [] if isinstance(v, dict)
        )

    @staticmethod
    def _has_readable_stock_flag(pressings: list) -> bool:
        # all(), not any(): one readable variant does not make the product
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
