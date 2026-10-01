import math
import re
import unicodedata
from typing import AsyncIterator, Optional, Tuple

from shopify_catalog import iter_products, resolve_cover_image

# The store's own all-vinyl shelf. Small enough to walk whole -- well under
# shopify_catalog._MAX_PAGE pages -- and it already does most of the format
# scoping, so the gates below only repair what the store files onto it by
# mistake: merch bundles, a CD or cassette typed as an LP, a T-shirt.
_COLLECTION_SLUG = "all-vinyl"

# `product_type` is the first gate, and is enumerated positively so a kind the
# store adds later stays out by default. Live on this shelf: `LP` carries
# nearly every record, `7"`, `BOX SET` and `Vinyl` the rest; `Bundle`, `CD`,
# `Shirt` and `Magazine` are what it must keep out. Compared lowercased.
_VINYL_TYPES = frozenset({"lp", '7"', "box set", "vinyl"})

# Shopify's placeholder for a product with exactly one variant. It names no
# pressing, so a row built on it carries the product's own title -- and only
# when it IS the sole variant, the same rule iodinerecords.py keeps, so a
# placeholder beside real variants cannot share their title and item_key.
_PLACEHOLDER_VARIANT = "default title"

# `vendor` is the distributor (`AEC`, `Redeye`, `ORCHARD`, `WARNER`), never
# the act, so the artist lives only in the title, as
# `ARTIST 'ALBUM' LP (Pressing)`. Single quotes in three spellings, opened
# with any of them -- the store types `'`, `‘` and even `’` as an opening
# quote (`’12x5’`).
#
# The opening quote must follow whitespace, which is what keeps an
# apostrophe in the artist from being read as one (`KING'S X 'MANIC
# MOONLIGHT'`). The CLOSING quote is the last one followed by whitespace or
# the end, i.e. the album is greedy, and that is measured rather than
# assumed: on the live shelf a lazy album split every title whose album holds
# an apostrophe before a space -- `'INFEST THE RATS' NEST'`, `'ROCK 'N'
# ROLL BABY'`, `'LET GOD SORT EM' OUT'` -- and greedy got every one right.
# The only product greedy reads worse is one record bundling two albums under
# one title, which no rule over this string can split correctly.
_TITLE_RE = re.compile(r"^(?P<artist>.+?)\s+['‘’](?P<album>.+)['’‘](?=\s|$)\s*(?P<extra>.*)$")

# The store's spelling of a self-titled record. Discogs titles one with the
# act's name, which is what the library match compares against.
_SELF_TITLED_RE = re.compile(r"^s/t$", re.IGNORECASE)

# Word boundaries spelled "not a letter or digit" rather than `\b`, because a
# disc count is welded to its medium (`2LP`, `3LP`, `2CD`) and `\b` sees no
# boundary between a digit and a letter. The same lesson lenoise.py and
# joyfulnoiserecordings.py paid for.
_B = r"(?<![^\W_])"
_E = r"(?![^\W_])"
_COUNT = r"(?:\d+\s*[x×]?\s*)?"

# The inch marker takes the closing boundary too, though a quote glyph is
# already a non-word character: without it `12"x12" POSTER` reads as a record
# size and vouches for a CD beside it, and so does a glued `(12"CD)`. A record
# bundled with a disc still names a separator after the quote (`12" + CD`).
# Found by Copilot in review on PR #408.
_VINYL_RE = re.compile(
    _B + _COUNT + r"(?:LPs?|vinyls?|picture\s+discs?)" + _E
    + r"|" + _B + _COUNT + r"(?:7|10|12)\s*[\"”″]" + _E,
    re.IGNORECASE,
)
# `EP` is deliberately absent above: it names a record's length, not its
# medium, so as vinyl evidence it would keep a `CD EP` or `CASSETTE EP` typed
# as an LP. A vinyl EP here says so in its own bracket (`EP (Grey Vinyl)`).
# Found by Copilot in review on PR #408.
# A whole dimension -- `12"x12"`, `12" x 12"`, `12″×12″` -- is artwork or a
# sleeve, never a record, and neither half may vouch for a disc beside it.
# Both numbers need an inch mark, which is what leaves `2x12"` (two records)
# alone. Stripped before vinyl evidence is looked for. Found by Copilot in
# review on PR #408.
_DIMENSION_RE = re.compile(r'\d+\s*["”″]\s*[x×]\s*\d+\s*["”″]', re.IGNORECASE)


def _names_vinyl(text: str) -> bool:
    return bool(_VINYL_RE.search(_DIMENSION_RE.sub(" ", text)))


_NON_VINYL_MEDIUM_RE = re.compile(
    _B + _COUNT + r"(?:CDs?|cassettes?|tapes?|DVDs?|blu-?\s*rays?)" + _E,
    re.IGNORECASE,
)
# Merchandise sold as part of the product. A record bundled with a magazine,
# shirt, book or print is a different product at a different price -- the
# store's own `Bundle` type holds most of them, but a seam is typed `LP`
# (`... LP + REVOLVER WINTER ISSUE`, `... WHITE LP & EXCLUSIVE T-SHIRT
# BUNDLE`, `... LP w/ SIGNED 12"x12" PAUL ROMANO PRINT`). `poster` is
# deliberately absent: `4LP WITH POSTER` is a record with an insert, not a
# bundle. `zine` covers a variant sold beside the pressings on one product.
_MERCH_RE = re.compile(
    _B + r"(?:t-?\s*shirts?|shirts?|tees?|hoodies?|bundles?|magazines?|issues?|zines?"
    r"|books?|graphic\s+novels?|comics?|prints?|slipmats?|bobbleheads?|pins?)" + _E,
    re.IGNORECASE,
)

_BRACKET_RE = re.compile(r"\([^()]*\)")

# How a bundle joins its merch to the record in the title: `... Magazine w/
# 'Album' 2LP`, `... BOOK + 'Album' LP`.
_BUNDLE_JOIN_RE = re.compile(_B + r"w/|\+")


def _text(value) -> str:
    """A whitespace-collapsed NFC string, or "" for anything that is not one.

    Every product-level field goes through here, so a retyped field is skipped
    rather than raising up through the walk and aborting the whole source over
    one product.
    """
    if not isinstance(value, str):
        return ""
    return unicodedata.normalize("NFC", " ".join(value.split()))


class Crawler:
    site_name: str = "Revolver"
    base_url: str = "https://shop.revolvermag.com"
    genre_summary: str = (
        "Revolver magazine's shop: exclusive colour pressings and reissues "
        "across metal, hardcore, emo, punk and alternative rock, alongside "
        "magazine and merch bundles."
    )
    genre: str = "metal"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        products_seen = 0
        vinyl_typed = 0
        artist_ok = 0
        records = 0
        identity_missing = 0
        unreadable_stock = 0
        unreadable_variants = 0
        unreadable_products = 0
        yielded = 0
        priced = 0
        # The store's edge 429s a page-2 request that carries page 1's session
        # cookies; see iter_products' `refuse_cookies`.
        async for product in iter_products(self.base_url, _COLLECTION_SLUG, refuse_cookies=True):
            products_seen += 1
            # A product this crawler cannot even classify -- not a mapping, a
            # `product_type` that is not a string, or a vinyl-typed product
            # whose title is not one -- reaches none of the tallies below, so
            # one readable sold-out record beside it would let the walk
            # complete empty. Counted here, before any gate filters. An empty
            # string type is live and legitimate (an untyped bundle), so only
            # a non-string counts. Found by Copilot in review on PR #408.
            if self._is_unreadable_product(product):
                unreadable_products += 1
                continue
            # Nested rather than sibling tallies: only a product that passes
            # every gate before it could have yielded a row, so only that
            # product's stock readability says anything about an empty result.
            if self._is_vinyl_type(product):
                vinyl_typed += 1
                parsed = self._parse_title(_text(product.get("title")))
                if parsed is not None:
                    artist_ok += 1
                    artist, _album, extra = parsed
                    if not self._is_off_shelf(artist, extra):
                        # Counted before the variant gate filters anything:
                        # `_vinyl_variants` drops a malformed collection or
                        # entry silently, so a product made unreadable that
                        # way would otherwise reach no tally at all, and one
                        # sold-out record beside it would let the walk
                        # complete empty. Found by Copilot in review on
                        # PR #408.
                        if self._has_unreadable_variants(product):
                            unreadable_variants += 1
                        variants = self._vinyl_variants(product)
                        if variants:
                            records += 1
                            if not _text(product.get("handle")):
                                identity_missing += 1
                            elif not all(isinstance(v.get("available"), bool) for v, _ in variants):
                                unreadable_stock += 1
            for item in self._items(product):
                yielded += 1
                if item["price"] is not None:
                    priced += 1
                yield item
        # db.replace_stock_items() DELETEs this crawler's previous snapshot
        # before inserting, and _sync_stock only skips that call when the crawl
        # raised -- so a completed-but-empty walk is destructive where a raise
        # is inert. Each guard names a distinct way the payload can stop
        # carrying what this crawler reads; the tallies are taken before the
        # availability filter, so a shelf that simply sold out is allowed to
        # be empty.
        if products_seen == 0:
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection returned no products -- renamed, "
                "removed, or payload drift")
        if not yielded and unreadable_products:
            # Ahead of the vocabulary guards below, which a payload-shape
            # failure would otherwise satisfy with the wrong diagnosis.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while "
                f"{unreadable_products} product(s) could not be read at all -- "
                "product-source drift")
        if vinyl_typed == 0:
            raise RuntimeError(
                f"none of the {products_seen} products in the {_COLLECTION_SLUG} "
                "collection carries a vinyl product_type -- format-taxonomy drift")
        if artist_ok == 0:
            # The artist is read out of the title's quoting convention, so this
            # notices the store renaming its records (`Artist - Album`, say):
            # every record would be skipped while the type tally stayed non-zero.
            raise RuntimeError(
                f"no vinyl product in the {_COLLECTION_SLUG} collection has a title "
                "of the form ARTIST 'ALBUM' -- artist-source drift")
        if not yielded and unreadable_variants:
            # Asked ahead of the format guard below: a walk whose variant data
            # has gone unreadable store-wide also has no record variant, and
            # the format guard would name the wrong cause.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while "
                f"{unreadable_variants} record(s) carry unreadable variant data "
                "-- variant-source drift")
        if records == 0:
            raise RuntimeError(
                f"every vinyl product in the {_COLLECTION_SLUG} collection reads as "
                "merch, another medium, or has no record variant -- format-source drift")
        if not yielded and identity_missing:
            # `handle` is identity: item_key hashes the row's URL, so a product
            # missing it is skipped rather than emitted under a fresh identity
            # that would orphan the judgments and saves keyed on its old one.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while "
                f"{identity_missing} record(s) carry no handle -- identity-source drift")
        if not yielded and unreadable_stock:
            # One genuinely sold-out record must not vouch for a catalog that
            # has gone unreadable behind it.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while "
                f"{unreadable_stock} record(s) carry no readable availability "
                "flag -- stock-source drift")
        if yielded and not priced:
            raise RuntimeError(
                f"none of the {yielded} rows from the {_COLLECTION_SLUG} "
                "collection carries a price -- price-source drift")

    @classmethod
    def _items(cls, product) -> list:
        if not isinstance(product, dict) or not cls._is_vinyl_type(product):
            return []
        parsed = cls._parse_title(_text(product.get("title")))
        if parsed is None:
            return []
        artist, album, extra = parsed
        if cls._is_off_shelf(artist, extra):
            return []
        handle = _text(product.get("handle"))
        if not handle:
            return []
        # The format and pressing stay on the title (`EVERYTHING UNDER THE SUN
        # LP (Smoke Vinyl)`): they are the only thing separating two pressings
        # of one album, which are distinct products at distinct URLs here, and
        # the library match is exact-or-prefix-with-space, which the bare album
        # still satisfies.
        base = f"{album} {extra}" if extra else album
        items = []
        for variant, descriptor in cls._vinyl_variants(product):
            # Only the literal True admits a variant: the string "false" is
            # truthy, so a falsiness test would publish a sold-out record.
            if variant.get("available") is not True:
                continue
            items.append({
                "artist": artist,
                "title": f"{base} — {descriptor}" if descriptor else base,
                "format": "Vinyl",
                "price": cls._price(variant),
                # The storefront sets `cart_currency=USD`, and products.json
                # carries no currency of its own.
                "currency": "USD",
                "url": f"{cls.base_url}/products/{handle}",
                "cover_image_url": cls._cover(product, variant),
            })
        return items

    @staticmethod
    def _is_vinyl_type(product: dict) -> bool:
        return _text(product.get("product_type")).lower() in _VINYL_TYPES

    @staticmethod
    def _parse_title(title: str) -> Optional[Tuple[str, str, str]]:
        """(artist, album, extra) from `ARTIST 'ALBUM' extra`, or None.

        A title with no quoted album -- the store's soundtracks (`STAND BY ME
        SOUNDTRACK LP`) and its few unclosed quotes (`PEARL JAM 'VITALOGY
        2LP`) -- names no artist this crawler can read, and is skipped: a wrong
        artist is indistinguishable from a right one everywhere downstream.
        """
        m = _TITLE_RE.match(title)
        if m is None:
            return None
        artist = m.group("artist").strip()
        album = m.group("album").strip()
        extra = m.group("extra").strip()
        if not artist or not album:
            return None
        if _SELF_TITLED_RE.match(album):
            album = artist
        return artist, album, extra

    @staticmethod
    def _is_off_shelf(artist: str, extra: str) -> bool:
        """True for a product on the vinyl shelf that is not a record for sale on its own.

        Reads the artist and the text after the album, never the album itself:
        an album is free to be named `EVERY TRICK IN THE BOOK` or `TEARS ON
        TAPE`. The artist is read because a bundle can put its quoted album
        late in the title, leaving the merch in what parses as the artist
        (`PUSCIFER x Revolver Special Collector's Edition Magazine w/ 'Global
        Probing...' 2LP`). There a merch word counts only beside the bundle's
        own joiner (`w/`, `+`), because an act can be named with one: PEEL
        DREAM MAGAZINE is a band. Found by Copilot in review on PR #408.

        Merch is looked for outside the extra's brackets only, because inside
        them the store describes the pressing, and a pressing can be named
        with a merch word (`(Leopard Print Vinyl)`, `(... w/B-Side Screen
        Print)`). A bundle names its merch after the bracket closes.

        A non-vinyl medium disqualifies only when no vinyl one is named beside
        it: `LP + CD` and `2LP + DVD` are records with an extra, `CD` and
        `CASSETTE` typed as LPs are not.
        """
        if _MERCH_RE.search(artist) and _BUNDLE_JOIN_RE.search(artist):
            return True
        if _MERCH_RE.search(_BRACKET_RE.sub(" ", extra)):
            return True
        return bool(_NON_VINYL_MEDIUM_RE.search(extra)) and not _names_vinyl(extra)

    @classmethod
    def _is_unreadable_product(cls, product) -> bool:
        if not isinstance(product, dict) or not isinstance(product.get("product_type"), str):
            return True
        return cls._is_vinyl_type(product) and not isinstance(product.get("title"), str)

    @staticmethod
    def _has_unreadable_variants(product: dict) -> bool:
        """True when `variants` is not a non-empty list of titled mappings.

        Any one bad entry counts, not only a wholly bad collection: a readable
        sold-out pressing must not vouch for a malformed sibling beside it.
        """
        raw = product.get("variants")
        if not isinstance(raw, list) or not raw:
            return True
        return any(not (isinstance(v, dict) and _text(v.get("title"))) for v in raw)

    @classmethod
    def _vinyl_variants(cls, product: dict) -> list:
        """[(variant, descriptor)] for each variant that reads as a pressing.

        A variant title here is a colour (`Custard Tart`, `Orange LP`) or a
        signing (`Unsigned`, `Signed`), so the gate is negative: only a title
        naming merch or another medium is dropped -- a zine sold beside the
        pressings on one product, say. Non-mapping entries are dropped before
        anything reads them.
        """
        raw = product.get("variants")
        variants = [v for v in raw if isinstance(v, dict)] if isinstance(raw, list) else []
        pairs = []
        for variant in variants:
            title = _text(variant.get("title"))
            if not title:
                continue
            if title.lower() == _PLACEHOLDER_VARIANT:
                if len(variants) == 1:
                    pairs.append((variant, ""))
                continue
            if _MERCH_RE.search(title):
                continue
            if _NON_VINYL_MEDIUM_RE.search(title) and not _names_vinyl(title):
                continue
            pairs.append((variant, title))
        return pairs

    @staticmethod
    def _price(variant: dict) -> Optional[float]:
        raw = variant.get("price")
        # bool before float(): bool is an int subclass, so True would price a
        # record at 1. nan and inf are the others a truthiness check misses.
        if isinstance(raw, bool):
            return None
        try:
            price = float(raw)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(price) or price <= 0:
            return None
        return price

    @staticmethod
    def _cover(product: dict, variant: dict) -> Optional[str]:
        """resolve_cover_image() with its inputs type-checked first.

        The shared helper calls .get() on whatever sits in `featured_image` and
        `images[0]`, and returns `src` unexamined, so a retyped field would
        either raise -- aborting the source over display-only artwork -- or
        hand a non-string to a TEXT column. Same guard as lenoise.py.
        """
        raw = product.get("images")
        raw = raw if isinstance(raw, list) else []
        images = [i for i in raw if isinstance(i, dict) and _text(i.get("src"))]
        featured = variant.get("featured_image")
        if not (isinstance(featured, dict) and _text(featured.get("src"))):
            variant = {**variant, "featured_image": None}
        return resolve_cover_image({**product, "images": images}, variant)
