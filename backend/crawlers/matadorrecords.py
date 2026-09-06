import html
import math
import re
from typing import AsyncIterator, Optional
from shopify_catalog import iter_products, has_tag, strip_vendor_prefix, resolve_cover_image

# Shopify's built-in all-products collection. The store publishes no format
# or "music" collection at all -- its own collections are one per artist plus
# `featured-products`, `bundles-1` and `boygenius-in-focus` -- so there is no
# narrower source to walk, and `all` is confirmed (2026-09-06) to agree with
# the root products.json and with meta.json's published_products_count.
_COLLECTION_SLUG = "all"
# The store's `product_type` says what kind of release a product is, never
# what it is pressed on: `Album`, `EP` and `Single` each hold CD, vinyl,
# cassette and digital variants side by side. Its only job here is to keep
# `Merch` out and to drop the products the store leaves untyped, which are
# its bundles (`Darkside Bundle`, `Funeral For Justice Album/Shirt Bundle`)
# plus two fan-club LPs. Enumerated rather than matched negatively so that a
# new non-music type the store might add (`Poster`, `Book`) stays out by
# default; a new music type is a silent scope loss the taxonomy guard below
# cannot see, and is accepted as the safer direction.
_MUSIC_TYPES = frozenset({"album", "ep", "single"})
_PREORDER_TAG = "preorder"
# On most products `vendor` is the artist. On a handful it is the label's own
# name -- `MatadorRecordsProd` (the store's myshopify subdomain) or
# `Matador Records` -- and the artist is then in `tags`, alongside the store's
# housekeeping tags. Every live tag that is not an artist is listed here;
# anything else on a label-vendored product is read as an artist credit.
_LABEL_VENDORS = frozenset({"matador records", "matadorrecordsprod"})
_HOUSEKEEPING_TAGS = frozenset({"migrated", "preorder", "sale", "checkbox", "matador merch"})
# The format lives in the variant title, as a descriptor after the album
# name: `Black Vinyl LP`, `Dbl LP`, `2xLP`, `12" EP`, `7" Single`,
# `Picture Disc LP`, `Vinyl Boxset` against `CD`, `CDEP`, `2X CD`,
# `4CD Boxset`, `Cassette`, `DMD Album` (digital) and `DVD`. The vocabulary
# follows spv.py's: every noun carries `s?` because `\blp\b` cannot see the
# "LPs" in "2 LPs + CD", and the disc-count prefix admits `2xLP`, `1.5XLP`
# and `4xLP`, where no word boundary separates the count from the noun. The
# lookbehind is what keeps "Help" and "Alps" out.
_VINYL_WORD_RE = re.compile(r"(?<![a-z])(?:\d+(?:\.\d+)?[x×]?)?lps?\b|\bvinyls?\b|\bpicture\s+discs?\b", re.IGNORECASE)
# `7"`, `12"`, `3x12"`, and the typographic quotes the store also uses.
_INCH_RE = re.compile(r"(?<![a-z])(?:\d+[x×])?\d{1,2}\s*(?:\"|”|″|inch(?:es)?\b)", re.IGNORECASE)
# Checked between the vinyl word and the inch marker, as spv.py does: a
# dimension is not a format claim, so `12" x 12" Poster` must not read as a
# record. A merch word next to a vinyl word (`LP + Signed Print`,
# `Black Vinyl LP + bumper sticker`) is a record with an extra, and the vinyl
# word has already decided by the time this runs.
_MERCH_RE = re.compile(
    r"\b(?:posters?|prints?|t-?shirts?|shirts?|tees?|hoodies?|totes?|hats?|caps?|bags?|"
    r"stickers?|patche?s?|pins?|slipmats?|mugs?|books?)\b", re.IGNORECASE)
_TRAILING_PAREN_RE = re.compile(r"\s*\([^()]*\)\s*$")
_PAREN_WRAP_RE = re.compile(r"\(([^()]*)\)")
_TRAILING_COLON_RE = re.compile(r":\s[^:]*$")
_LEADING_SEPARATOR_RE = re.compile(r"^[\s\-–—:]+")
_QUOTE_CLASS = "['’‘]"


class Crawler:
    site_name: str = "Matador Records"
    base_url: str = "https://matadorrecords.com"
    genre_summary: str = "New York indie label — Pavement, Yo La Tengo, Interpol, Spoon, Cat Power and Queens of the Stone Age, back catalogue and new releases alike."
    genre: str = "rock"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        products_seen = 0
        music_seen = 0
        artist_ok = 0
        vinyl_seen = 0
        unreadable_stock = 0
        yielded = 0
        priced = 0
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            products_seen += 1
            # Nested, not sibling tallies: only a product with the music
            # type, an artist AND a variant whose descriptor reads as vinyl
            # could have yielded a row, so only that product's stock
            # readability says anything about an empty result. Tallied
            # independently, one product could satisfy each condition while
            # none of them can yield.
            if self._is_music(product):
                music_seen += 1
                if self._artist(product):
                    artist_ok += 1
                    vinyl = self._vinyl_variants(product)
                    if vinyl:
                        vinyl_seen += 1
                        if not self._has_readable_stock_flag(vinyl):
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
        if products_seen == 0:
            raise RuntimeError(f"{_COLLECTION_SLUG} collection returned no products -- renamed, removed, or markup drift")
        if music_seen == 0:
            raise RuntimeError(f"no product in the {_COLLECTION_SLUG} collection carries a music product_type -- format-taxonomy drift")
        if artist_ok == 0:
            raise RuntimeError(f"no music product in the {_COLLECTION_SLUG} collection resolves an artist from vendor or tags -- artist-source drift")
        if vinyl_seen == 0:
            # The format is read out of free-text variant titles, so this is
            # the guard that notices the store moving it somewhere else
            # (a variant option, a metafield): every product would then
            # yield nothing while the tallies above stayed non-zero. A label
            # whose catalog is albums does not stop pressing records, so
            # zero has no innocent reading.
            raise RuntimeError(f"no music product in the {_COLLECTION_SLUG} collection carries a variant whose title reads as vinyl -- format-descriptor drift")
        if yielded and not priced:
            # Rows without the emptiness: `_price` answers None for a value
            # it cannot use, so a `price` field removed or retyped
            # store-wide re-lists the whole catalog with no prices, which is
            # worse than the snapshot it would replace. Isolated nulls stay
            # tolerated. Same guard as rhino.py and mtheoryaudio.py.
            raise RuntimeError(
                f"none of the {yielded} rows from the {_COLLECTION_SLUG} collection carries a "
                "price -- price-source drift")
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
                f"{unreadable_stock} vinyl product(s) carry no readable availability flag -- stock-source drift")

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        if not cls._is_music(product):
            return []
        artist = cls._artist(product)
        if not artist:
            return []
        # A live transformation on exactly one product ("Body/Head - Coming
        # Apart"); everywhere else the store keeps the artist out of the
        # title and this is the drift guard it is on the sibling stores.
        album = strip_vendor_prefix((product.get("title") or "").strip(), artist)
        url = f"{cls.base_url}/products/{product.get('handle', '')}"
        is_preorder = has_tag(product, _PREORDER_TAG)
        items = []
        for variant, descriptor in cls._vinyl_variants(product):
            # Only the literal True admits a variant: the string "false" is
            # truthy, so a falsiness test would publish a sold-out record as
            # in stock. Anything else -- False, "false", 1, None, absent --
            # is skipped, which is also what keeps this filter and
            # _has_readable_stock_flag agreeing on what "readable" means.
            # No pre-order bypass: every live pre-order reports available
            # True, so an unavailable one is gone allocation.
            if variant.get("available") is not True:
                continue
            # A product title that already ends in the descriptor, dash-
            # separated ("I'm A Lazy Son...But I'm The Only Son - 12\" EP"),
            # would otherwise name the format twice; the copy inside the
            # title goes, and the appended one stands as the identity.
            base = cls._without_trailing_descriptor(album, descriptor)
            title = f"{base} (Pre-Order)" if is_preorder else base
            # The descriptor is appended on every row, not only when the
            # product has more than one variant. Nearly every product here
            # is multi-variant (its CD sits beside its LP), and the
            # descriptor is the pressing -- so it is part of the row's
            # identity, and that identity must not depend on a CD sibling
            # being listed or delisted. Keyed on the variant count, a CD
            # going out of print would re-title every vinyl row of that
            # product and orphan the listings, judgments and saves keyed on
            # the old title.
            items.append({
                "artist": artist,
                "title": f"{title} — {descriptor}",
                "format": "Vinyl",
                "price": cls._price(variant),
                "currency": "USD",
                "url": url,
                "cover_image_url": resolve_cover_image(product, variant),
            })
        return items

    @staticmethod
    def _is_music(product: dict) -> bool:
        return (product.get("product_type") or "").strip().lower() in _MUSIC_TYPES

    @staticmethod
    def _artist(product: dict) -> str:
        vendor = (product.get("vendor") or "").strip()
        if vendor and vendor.lower() not in _LABEL_VENDORS:
            return vendor
        # A product carrying more than one credit is a split, and the row
        # takes the first credit only. The catalog keeps a release's primary
        # artist alone (discogs.parse_release reads artists[0]) and the Track
        # tab's library match is an exact artist equality, so a joined
        # "Jay Reatard / Sonic Youth" could never match anything. The store
        # serialises tags alphabetically, which is the only order the payload
        # offers: a split whose Discogs primary artist sorts second will not
        # match its library record, but is still credited to an artist who is
        # on it.
        credits = [
            (t or "").strip() for t in product.get("tags") or []
            if (t or "").strip() and (t or "").strip().lower() not in _HOUSEKEEPING_TAGS
        ]
        return credits[0] if credits else ""

    @classmethod
    def _vinyl_variants(cls, product: dict) -> list:
        # Non-mapping entries are dropped here, before anything reads them,
        # so a junk entry is an ordinary skipped row rather than an
        # AttributeError from inside the yield loop.
        title = (product.get("title") or "").strip()
        pairs = []
        for variant in product.get("variants") or []:
            if not isinstance(variant, dict):
                continue
            descriptor = cls._descriptor(title, variant.get("title") or "")
            if cls._is_vinyl(descriptor):
                pairs.append((variant, descriptor))
        return pairs

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
    def _is_vinyl(descriptor: str) -> bool:
        # Positive, not negative, unlike spv.py's gate on its vinyl
        # collection: the source here is the whole catalog, so an
        # unrecognised descriptor is a CD until it says otherwise. A
        # descriptor naming both (`Deluxe Black Vinyl 2X LP + Bonus CD`) is a
        # record. Merch is checked before the inch marker for the reason
        # spv.py gives: a dimension is not a format claim.
        if _VINYL_WORD_RE.search(descriptor):
            return True
        if _MERCH_RE.search(descriptor):
            return False
        return bool(_INCH_RE.search(descriptor))

    @classmethod
    def _descriptor(cls, product_title: str, variant_title: str) -> str:
        """The part of a variant title that names the pressing.

        Variant titles repeat the album name and then say the format --
        "Adore Life - LP", "Antics Black Vinyl LP", "Alien Lanes CD" -- so the
        descriptor is what follows the album name when the variant starts
        with it. The album name is matched case-insensitively, with the
        store's straight and typographic apostrophes read as one and any
        HTML entity in the variant decoded first ("Signals, Calls &amp;
        Marches"). A parenthesised edition on the product title is tried
        three ways, because the variant writes it in any of them: as
        written, unwrapped ("Valentine (Demos)" against "Valentine Demos -
        12\" EP"), and dropped ("Electric Version (20th Anniversary
        Edition)" against "Electric Version LP"); a colon-led edition is
        dropped last ("The Greatest: Slipcase Edition" against "The Greatest
        120 gram LP").

        When the variant does not start with the album name -- the store
        re-spells it ("6 Feet Beneath The Moon" against "Six Feet Beneath
        The Moon - Dbl LP"), or drops a credit ("Body/Head - Coming Apart"
        against "Coming Apart - CD") -- the text after the last " - " is the
        descriptor. Failing that too, the whole variant title is, which on
        the live catalog reaches only CDs the gate then rejects anyway.
        Internal whitespace is collapsed: the store's own "Deluxe  LP" would
        otherwise carry its double space into the row's identity.
        """
        variant_title = html.unescape(variant_title or "").strip()
        product_title = html.unescape(product_title or "").strip()
        bases = (
            product_title,
            _PAREN_WRAP_RE.sub(r" \1 ", product_title),
            _TRAILING_PAREN_RE.sub("", product_title),
            _TRAILING_COLON_RE.sub("", product_title),
        )
        for base in bases:
            m = cls._album_prefix(base, variant_title)
            if m is None:
                continue
            rest = _LEADING_SEPARATOR_RE.sub("", variant_title[m.end():])
            rest = " ".join(rest.split())
            if rest:
                return rest
        if " - " in variant_title:
            rest = " ".join(variant_title.rsplit(" - ", 1)[1].split())
            if rest:
                return rest
        return " ".join(variant_title.split())

    @staticmethod
    def _without_trailing_descriptor(album: str, descriptor: str) -> str:
        # Only the dash-separated form is stripped: a title that merely ends
        # in the same words ("Black Vinyl" as an album name) is left alone,
        # and so is a title that is nothing but the descriptor.
        tokens = descriptor.split()
        if not tokens:
            return album
        parts = [re.sub(_QUOTE_CLASS, _QUOTE_CLASS, re.escape(tok)) for tok in tokens]
        pattern = r"\s+[-–—]\s+" + r"\s+".join(parts) + r"\s*$"
        m = re.search(pattern, album, re.IGNORECASE)
        if m is None or m.start() == 0:
            return album
        return album[:m.start()].rstrip()

    @staticmethod
    def _album_prefix(base: str, variant_title: str):
        tokens = base.split()
        if not tokens:
            return None
        parts = [re.sub(_QUOTE_CLASS, _QUOTE_CLASS, re.escape(tok)) for tok in tokens]
        # `(?!\w)`: the album name must end at a word boundary, so an album
        # called "Go" does not claim "Gone Glimmering LP" as its own.
        pattern = r"\s+".join(parts) + r"(?!\w)"
        return re.match(pattern, variant_title, re.IGNORECASE)

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
