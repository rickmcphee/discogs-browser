import math
import re
from typing import AsyncIterator, List, Optional, Tuple

from shopify_catalog import iter_products, resolve_cover_image

# Shopify's built-in all-products collection. The store publishes no vinyl
# shelf to walk instead: `collections.json` lists genre shelves (`indie-pop`,
# `jazz`, `metal`), staff-pick shelves and event shelves, every one of which
# mixes formats, and the two catalog-wide shelves (`music`, `new`) are the
# same products under another name. So the format scoping has to be done here
# rather than chosen by URL, which is what the two gates below are.
#
# `all` is the whole published catalog: the walk returns exactly the
# `published_products_count` from meta.json, confirmed live 2026-09-09.
# `collections.json` reports a smaller products_count for the same shelf --
# it counts products not published to the online store -- so the walk's own
# exhaustion is the catalog, not that number.
_COLLECTION_SLUG = "all"

# The store's own kind field, and the first gate. Its vocabulary is coarse
# (`Music` against `Film & TV`, `Books`, `Merchandise`, `Accessories`,
# `Events`, `Other`) so it does not name a format -- the CDs and cassettes
# are `Music` too, which is what the variant gate below is for. What it does
# do is keep out the one shape that would otherwise pass every other test on
# this page: the store's in-store album launches, which sell a record and an
# event ticket as one product ("2LP (Monorail Exclusive Lemon Opaque Vinyl
# SIGNED) + Ticket", £47.99). Those are the ONLY products outside the `Music`
# type whose variants name vinyl at all, confirmed live across the whole
# catalog, and their price is a record's price plus a ticket's.
#
# They carry an EMPTY product_type rather than `Events`, so this gate has to
# admit a named type rather than reject a list of them.
_MUSIC_PRODUCT_TYPE = "music"

# A product here is a release and its variants are the formats it was
# released in -- the option axis is literally named `Format` on every product
# that has one -- so a record and its CD sit on one product, sharing a title
# and a URL. The medium is therefore decided per variant, off the variant
# title, and the gate is POSITIVE: `all` is the whole shop, so anything not
# proven to be a record is not one. That is the opposite of the negative gate
# sacredbonesrecords.py uses, and the difference is the shelf, not the
# taste -- a negative gate there is safe because the shelf is already curated
# as vinyl, and here it would publish every CD in the shop.
#
# The cost is real and accepted: the store names a small run of pressings by
# colour or edition alone ("Eco Mix Random Colour", "Apricot Color Wax",
# "2026 Repress"), which name no format and so are dropped. Live, that is 22
# products whose every variant is unreadable this way, against the ~1,200
# CD-only products the gate keeps out. Admitting them instead would mean
# reading a bare colour as a record, and colour names a CD edition too --
# "15th Anniversary Edition" is live here beside a CD on the same product.

# "10\" X 10\" Poster" is a size, not a record. Deleted before the inch
# marker below can read either half as a 10-inch single: without this, the
# one live product it appears on -- a CD, whose bonus poster is measured in
# inches -- is published as vinyl. Same shape as byrdlandrecords.py deleting
# "not vinyl" before its own override reads the "vinyl" inside it.
_DIMENSION_RE = re.compile(r'\d+\s*["”″]\s*[xX×]\s*\d+\s*["”″]')

_VINYL_RE = re.compile(
    # Substring, not \bvinyl\b: the store writes "biovinyl" for a plant-based
    # pressing, and a word-bounded match drops it.
    r'vinyl'
    # The store's own misspelling, live on a record it sells.
    r'|\bvinly\b'
    # LP, and the forms the store glues to it: a count before (2LP, 2xLP),
    # a D for a double (DLP), a disc number after (LP2). \b matches nothing
    # between a digit and a letter, so a plain \blps?\b reads none of them.
    # The lookbehind is what stops the leading \d* from letting a letter run
    # into it -- without it, `FLP`-shaped words would match.
    r'|(?<![a-z])\d*\s*[x×]?\s*d?lps?\d?\b'
    r'|\bflexi\b'
    # Sizes only -- 7, 10 and 12 -- rather than any number before an inch
    # mark, because the number in "1/4\" Master Tapes" is not a record. A
    # count may precede the size (2x12"), which is why sacredbonesrecords.py's
    # optional multiplier is here too; it cannot admit 2xCD, since what
    # follows still has to be a size and an inch mark.
    r'|(?<![a-z0-9])(?:\d+\s*[x×]\s*)?(?:7|10|12)\s*-?\s*(?:["”″]|inch(?:es)?\b|in\b)'
    # The store's own abbreviation for a vinyl LP, used bare as a whole
    # variant name. Confirmed against the records it appears on rather than
    # guessed -- every one is an LP at an LP's price.
    r'|\bvl\b'
    r'|\bpicture\s+discs?\b',
    re.IGNORECASE,
)

# "Artist - Album", split on the FIRST spaced dash: album halves legitimately
# carry further runs of one. Whitespace is required on both sides so a
# hyphenated name is never split mid-word, and the en dash is included
# because the store uses it on a handful of titles.
_SEPARATOR_RE = re.compile(r'^(?P<artist>.+?)\s+[-–—]\s+(?P<album>.+)$')
# A dash between two numbers is a range, not a separator: "Far East New Rock
# Invention 1969 - 1975" is one compilation's name, and splitting it credits
# the record to an artist called "Far East New Rock Invention 1969". Three
# live titles do this. Rejecting the split here rather than repairing it
# afterwards is what hands those titles to the tag fallback below, which
# carries the right answer for all three.
_NUMBER_RANGE_RE = re.compile(r'\d\s*$')
_LEADING_NUMBER_RE = re.compile(r'^\s*\d')


def _text(value) -> str:
    """A whitespace-collapsed string, or "" for anything that is not one.

    Every product-level field this crawler reads goes through here. A truthy
    non-string would otherwise reach .strip() or .split() and raise, taking
    the whole source down over one malformed product -- the opposite of the
    discard-and-keep-going rule _read_variants already applies to a variant
    title, and the reason the product-level fields are brought into line with
    it here. Answering "" instead routes the product into the identity and
    artist tallies below, so it is skipped and *counted*, and the drift guards
    still see it.

    That the abort is otherwise inert -- _sync_stock skips
    replace_stock_items() on a raise, leaving the previous snapshot intact --
    is not a defence. It leaves the store frozen at that snapshot for as long
    as the one bad product is published, with every other record's price
    silently stale.
    """
    return " ".join(value.split()) if isinstance(value, str) else ""


class Crawler:
    site_name: str = "Monorail Music"
    base_url: str = "https://monorailmusic.com"
    genre_summary: str = (
        "Glasgow independent record shop inside Mono, strong on indie pop, "
        "experimental and reissued and Scottish music, with its own label and "
        "shop-exclusive pressings."
    )
    genre: str = "marketplace"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        products_seen = 0
        music_seen = 0
        vinyl_products = 0
        artist_missing = 0
        identity_missing = 0
        unreadable_stock = 0
        unreadable_variants = 0
        yielded = 0
        priced = 0
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            products_seen += 1
            if not self._is_music(product):
                continue
            music_seen += 1
            pressings, unreadable = self._read_variants(product)
            unreadable_variants += unreadable
            # One bracket per product for every way a product that WOULD have
            # yielded a row failed to, counted once against the first reason
            # that applies. Gating it on `pressings` is what keeps a CD-only
            # product from tallying toward anything: it would never have
            # yielded a row whatever its title said, so it can neither raise a
            # false alarm nor vouch for the catalog. In particular the artist
            # question has to be asked here rather than over every `Music`
            # product walked -- a tally taken outside the gate is satisfied by
            # the CDs' own titles while every record has lost its.
            if pressings:
                vinyl_products += 1
                # Identity before artist: `title` is identity AND the artist's
                # own source, so a product that has lost it has lost both, and
                # reporting that as artist-source drift names the wrong field.
                # `handle` is asked here for the same reason it is asked at
                # all -- item_key hashes the URL built from it.
                if not self._has_identity(product):
                    identity_missing += 1
                elif not self._artist(product):
                    artist_missing += 1
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
        # stop carrying what this crawler reads.
        #
        # Both gates here are positive, unlike the negative gate
        # sacredbonesrecords.py can leave unguarded, so each needs its own
        # guard: a renamed product_type or a dropped `Format` axis empties
        # this walk while every product still parses.
        if products_seen == 0:
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection returned no products -- renamed, removed, "
                "or payload drift")
        if not music_seen:
            raise RuntimeError(
                f"none of the {products_seen} products in the {_COLLECTION_SLUG} collection "
                f"carries the {_MUSIC_PRODUCT_TYPE!r} product_type -- kind-taxonomy drift")
        if not yielded and unreadable_variants:
            # Everything about a product's variants this crawler could not
            # interpret: a non-mapping entry, a non-string title, or a
            # `variants` collection that is absent, empty or retyped. Those
            # discards are otherwise invisible, and invisible is destructive --
            # Shopify dropping variant titles store-wide leaves every product
            # with nothing to gate on, and the bracket above never fires
            # because such a product has no admitted pressings.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while {unreadable_variants} "
                "variant(s) or variant collection(s) could not be interpreted -- "
                "variant-identity-source drift")
        if not vinyl_products:
            # A record shop with no record on any of its music products is
            # the store's format vocabulary having moved, not a sold-out
            # catalog: availability is not read until after this tally.
            #
            # Consulted AFTER the variant guard above, because a product whose
            # variants could not be read has no admitted pressing either --
            # this guard would otherwise answer every variants-level failure
            # with the one diagnosis that is not the cause. A renamed format
            # vocabulary leaves the variants perfectly readable, so nothing
            # this guard is for can hide behind that ordering.
            raise RuntimeError(
                f"none of the {music_seen} music products in the {_COLLECTION_SLUG} collection "
                "names a vinyl format on any variant -- format-taxonomy drift")
        if yielded and not priced:
            # Rows without the emptiness: `_price` answers None for a value it
            # cannot use, so a `price` field removed or retyped store-wide
            # re-lists the whole catalog with no prices, which is worse than
            # the snapshot it would replace. Isolated nulls stay tolerated.
            raise RuntimeError(
                f"none of the {yielded} rows from the {_COLLECTION_SLUG} collection carries a "
                "price -- price-source drift")
        if not yielded and artist_missing:
            # The artist is read from the title, then from a sole tag, and a
            # product carrying neither is skipped rather than credited from
            # something else -- `vendor` is the label here ("Domino Records",
            # "4AD"), never the act. Skipping leaves the walk looking sold
            # out, which is why this and the three guards below are gated on
            # an empty outcome: one artist-less product among real rows is an
            # ordinary skipped row.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while {artist_missing} "
                "record(s) carry no readable artist -- artist-source drift")
        if not yielded and identity_missing:
            # `title` and `handle` are identity, not display: item_key hashes
            # the row's title and URL, so a product missing either is skipped
            # rather than emitted under a fresh identity that would orphan the
            # judgments and saves keyed on its old one.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while {identity_missing} "
                "record(s) carry no title or handle -- identity-source drift")
        if not yielded and unreadable_stock:
            # An empty result is only trustworthy when every product that
            # could have yielded a row was readable and simply out of stock.
            # Counting unreadable products rather than readable ones is what
            # catches the partial case: one genuinely sold-out record must not
            # vouch for a catalog that has gone unreadable behind it.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while {unreadable_stock} "
                "record(s) carry no readable availability flag -- stock-source drift")

    @classmethod
    def _items(cls, product: dict) -> List[dict]:
        if not cls._has_identity(product):
            return []
        artist = cls._artist(product)
        if not artist:
            return []
        album = cls._album(product)
        url = f"{cls.base_url}/products/{_text(product.get('handle'))}"
        items = []
        for variant, pressing in cls._read_variants(product)[0]:
            # Only the literal True admits a variant: the string "false" is
            # truthy, so a falsiness test would publish a sold-out record as
            # in stock. Anything else -- False, "false", 1, None, absent -- is
            # skipped, which is also what keeps this filter and
            # _has_readable_stock_flag agreeing on what "readable" means.
            #
            # No pre-order handling and no " (Pre-Order)" marker: the store's
            # pre-orders report available True and carry no tag, no title
            # marker and no distinguishing product field -- their only signal
            # is membership of a separate `pre-order` collection, which this
            # payload does not carry. They are purchasable at the listed
            # price, so they are stock. A marker would also re-title every row
            # the day the record ships, orphaning the saves and judgments
            # keyed on the old item_key.
            if variant.get("available") is not True:
                continue
            # The pressing is appended on every row that names one, not only
            # when the product has more than one: a sibling selling out must
            # not re-title the surviving rows and orphan what hangs off the
            # old identity. It goes AFTER the album so
            # db._library_release_match_sql's exact-or-prefix-followed-by-a-
            # space test still matches a library title -- a catalog "The
            # Giver" matches this row's "The Giver — Limited Swirl 7"...".
            #
            # It is also what keeps the pressings of one release distinct:
            # item_key hashes (artist, title, url) and every variant of a
            # product shares the artist and the URL, so without the pressing
            # the store's live multi-pressing records would each emit several
            # rows under ONE identity.
            #
            # Nothing would raise. stock_items.item_key is deliberately not
            # unique -- two stores stocking the same record share one -- and
            # stock_item_identities upserts on it. That is exactly why this is
            # worth spelling out: the pressings would silently share the saves,
            # judgments and crawl-queue state keyed on that identity, each
            # overwriting the last's identity row, and the Store tab would
            # list them as duplicates.
            items.append({
                "artist": artist,
                "title": f"{album} — {pressing}" if pressing else album,
                # "Vinyl" unconditionally, as every sibling catalog crawler
                # does. The specific cut (7", 10", LP) is already carried in
                # the pressing appended above.
                "format": "Vinyl",
                "price": cls._price(variant),
                # From the store's meta.json, which reports GBP, and confirmed
                # against a live product page rendering £14.99 for the value
                # this payload carries as "14.99".
                "currency": "GBP",
                "url": url,
                "cover_image_url": cls._cover(product, variant),
            })
        return items

    @staticmethod
    def _cover(product: dict, variant: dict) -> Optional[str]:
        """resolve_cover_image() with its two collections type-checked first.

        The shared helper reads `variant["featured_image"].get(...)` and
        `product["images"][0].get(...)` behind `or` guards, which catch a
        missing or null field but pass a *retyped* one straight through to
        .get() -- and a raise there aborts the whole source over one product's
        artwork, which is display-only. Guarded here rather than in
        `shopify_catalog`, because every Shopify crawler in the fleet reads
        that helper and this is one store's payload, not a fleet-wide change
        to make from inside this crawler.
        """
        images = product.get("images")
        images = [i for i in images if isinstance(i, dict)] if isinstance(images, (list, tuple)) else []
        if not isinstance(variant.get("featured_image"), dict):
            variant = {**variant, "featured_image": None}
        return resolve_cover_image({**product, "images": images}, variant)

    @staticmethod
    def _is_music(product: dict) -> bool:
        return _text(product.get("product_type")).lower() == _MUSIC_PRODUCT_TYPE

    @classmethod
    def _split_title(cls, title: str):
        match = _SEPARATOR_RE.match(title)
        if match is None:
            return None
        if (_NUMBER_RANGE_RE.search(match.group("artist"))
                and _LEADING_NUMBER_RE.match(match.group("album"))):
            return None
        return match

    @classmethod
    def _artist(cls, product: dict) -> str:
        """The title's artist half, else the product's sole tag.

        The title is primary because it is the store's own billing, written
        out in full. The tag is a fallback and not the other way round for one
        specific reason: Shopify stores tags as a comma-separated string, so
        an act whose own name contains a comma arrives already split across
        several tags -- "Black Country, New Road" as ["Black Country", "New
        Road"] -- and nothing in the payload marks the join. The title carries
        that name whole.

        The fallback is worth having because the store files a real run of
        records under the album name alone (compilations, self-titled records,
        and reissues where the act's name is on the sleeve but not in the
        field): 144 in-stock records live, which the title alone cannot credit
        to anybody. 141 of them carry exactly one tag, and it is the act every
        time.

        Which is why it is taken only when there is EXACTLY one tag. The
        store's tags are alphabetically sorted and mix the act with genre
        words ("ambient", "alt-rock", "Alt"), so on a multi-tag product the
        first tag is whichever sorts first, not the artist -- and two tags may
        equally be one comma-split name or two collaborators. Measured over
        the catalog, no single-tag product carries a genre word: the genre
        tags only ever appear ALONGSIDE the act's own tag, never alone. The
        one-tag test is what turns that into a rule.
        """
        match = cls._split_title(_text(product.get("title")))
        if match is not None:
            return match.group("artist").strip()
        # The collection itself is type-checked before it is iterated, not
        # only its entries: `product.get("tags") or []` leaves a retyped
        # `tags` intact, and iterating a non-collection raises TypeError from
        # inside the artist read -- the same whole-source abort `_text` exists
        # to prevent, one level up.
        raw_tags = product.get("tags")
        if not isinstance(raw_tags, (list, tuple)):
            raw_tags = []
        tags = [_text(t) for t in raw_tags if _text(t)]
        return tags[0] if len(tags) == 1 else ""

    @classmethod
    def _album(cls, product: dict) -> str:
        title = _text(product.get("title"))
        match = cls._split_title(title)
        # Whole when the title named no artist -- there is nothing to strip,
        # and the tag that supplied the artist took nothing out of the title.
        return match.group("album").strip() if match is not None else title

    @classmethod
    def _read_variants(cls, product: dict) -> Tuple[List[Tuple[dict, str]], int]:
        """(pressings, unreadable) for one product.

        `pressings` is the (variant, pressing name) pairs a row can be built
        from -- the variants naming a vinyl format. `unreadable` counts the
        entries discarded because this crawler could not interpret them at
        all. That count exists because those discards are otherwise invisible,
        and invisible is destructive: a product all of whose variants are
        discarded has no admitted pressings, so it reaches none of the other
        tallies and an empty walk looks legitimate.

        A variant naming a format that is simply not vinyl is NOT unreadable.
        It is the gate working, on roughly a third of the catalog.
        """
        raw = product.get("variants")
        if not isinstance(raw, (list, tuple)) or not raw:
            # Absent, emptied or retyped. A published Shopify product always
            # carries at least one variant, so none of those is a product with
            # nothing for sale -- it is a payload this crawler cannot read, and
            # reading it as the former is what would let the catalog disappear
            # store-wide in silence.
            return [], 1
        # Non-mapping entries are dropped here, before anything reads them, so
        # a junk entry is an ordinary skipped row rather than an AttributeError
        # from inside the yield loop.
        variants = [v for v in raw if isinstance(v, dict)]
        unreadable = len(raw) - len(variants)
        pairs = []
        for variant in variants:
            title = variant.get("title")
            # A truthy non-string would reach .split() through `or ""` and
            # raise, aborting the whole source over one malformed variant --
            # the opposite of the discard-and-keep-going rule every other
            # unreadable entry follows.
            if title is not None and not isinstance(title, str):
                unreadable += 1
                continue
            name = " ".join((title or "").split())
            if not name:
                # Every live product names its format, so a nameless variant
                # is drift rather than the sole-variant placeholder other
                # stores in this fleet send. It cannot be admitted either way:
                # a bare-album row would share its title and URL -- and so its
                # item_key -- with any sibling built the same way.
                unreadable += 1
                continue
            if not cls._is_vinyl(name):
                continue
            pairs.append((variant, name))
        return pairs, unreadable

    @staticmethod
    def _is_vinyl(pressing: str) -> bool:
        return bool(_VINYL_RE.search(_DIMENSION_RE.sub(" ", pressing)))

    @staticmethod
    def _has_identity(product: dict) -> bool:
        # Both must be readable STRINGS, not merely truthy: a non-string
        # title or handle is a product this crawler cannot identify, and
        # `_text` has already flattened it to "" so it is skipped and counted
        # here rather than raising from inside the row build.
        return bool(_text(product.get("title"))) and bool(_text(product.get("handle")))

    @staticmethod
    def _has_readable_stock_flag(pressings: List[Tuple[dict, str]]) -> bool:
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
