import math
import re
from typing import AsyncIterator, Optional

from shopify_catalog import iter_products, resolve_cover_image

# The store's own vinyl shelf, and the one the format scoping is delegated to.
# `collections.json` publishes shelves per medium (`cd`, `cassettes`, `video`)
# and per merchandise kind (`t-shirts`, `buttons`, `figurines`, `turntables`),
# so unlike monorailmusic.py there is no whole-shop collection to filter down
# -- the shelf already is the filter, and the two gates below only repair what
# the store files onto it by mistake.
#
# This collection is larger than Shopify's storefront products.json can
# enumerate: the endpoint refuses `page` past shopify_catalog._MAX_PAGE, so the
# walk keeps the first _MAX_PAGE * _PAGE_LIMIT products and stops. Which
# products fall outside is decided by the collection's own ordering, and here
# that ordering is not arbitrary: it drifts steadily down the alphabet, so what
# is lost is the early-alphabet tail rather than a random slice. Accepted on the
# same terms as waterloorecords.py -- a browsable, if truncated, shelf in the
# Store tab is worth more than no shelf -- and recorded in
# docs/specifications/shaping/2026-09-21-le-noise-crawler-design.md.
_COLLECTION_SLUG = "vinyl"

# `product_type` is the store's own format field and the first gate. Compared
# lowercased because the store's own casing is not: "VInyl" is live on real
# records, and an exact match drops them.
_VINYL_PRODUCT_TYPE = "vinyl"

# The second gate, and the reason the first is not enough: the store files a
# thin seam of CDs and cassettes onto the vinyl shelf under the `Vinyl`
# product_type, and names the medium in the title's own bracket -- "(CD)",
# "(2CD)", "(5CD)", "(Cassette)", "(CD/BRD)". Without this gate those are
# published as records, at a CD's price, against the library's vinyl.
#
# Read only inside a bracket, never as a bare word: a record legitimately
# carries either in its own name (Led Zeppelin's "Live EP (CD)" is a CD whose
# *album* is an EP), and the bracket is where this store says what it is
# selling.
_BRACKET_RE = re.compile(r'\(([^()]*)\)')

# Both sides of the bracket vocabulary, because a bracket can name several
# media at once: "(3LP/2CD)" and "(4LP+4CD)" are vinyl boxes with bonus discs
# and belong on the shelf, while "(CD/BRD)" is not vinyl in any part. So the
# non-vinyl names cannot decide on their own -- what disqualifies a product is
# naming one with no vinyl named anywhere alongside it.
#
# A leading count is part of the token and has no word boundary before the
# letters ("2CD", "3LP"), which is why each pattern carries its own `\d*`
# rather than relying on \b to find the start.
_NON_VINYL_MEDIUM_RE = re.compile(
    r'\b\d*\s*(?:CDs?|Cassettes?|K7|DVDs?|Blu-?\s*Rays?|BRD)\b',
    re.IGNORECASE,
)
# `EP` is deliberately absent: it names a record's length, not its medium, and
# CD EPs exist. Nothing is lost by leaving it out -- a bare "(EP)" product has
# no non-vinyl bracket for this to override -- while including it would rescue
# a hypothetical "(EP/CD)" that is not a record at all.
_VINYL_MEDIUM_RE = re.compile(
    r'\b\d*\s*LPs?\b'
    r'|\bvinyls?\b'
    r'|\bpicture\s+discs?\b'
    r'|\b(?:7|10|12)\s*["”″]',
    re.IGNORECASE,
)

# "Artist - Album (Pressing)", split on the FIRST qualifying dash: album halves
# legitimately carry further runs of one. The character class is the three dashes
# the store actually separates with -- ASCII hyphen-minus, U+2010 HYPHEN and
# U+2013 EN DASH. The two typographic ones are not decoration: they are how a
# small run of titles is punctuated, and an ASCII-only class files every one of
# them under no artist at all.
#
# Whitespace is required on AT LEAST ONE side of the dash, which is looser than
# the both-sides rule the sibling Shopify crawlers use, and is measured rather
# than assumed. Both sides is what keeps a hyphenated name from being split
# mid-word ("Bitter-Sweet", "In-A-Gadda-Da-Vida"), but this store types the
# separator closed up on one side often enough to matter -- "Tokyo Blade -Tokyo
# Blade", "Little Big Town- Mr. Sun" -- and one side is enough for that
# protection: the only shape both alternatives reject is a dash with a letter
# hard against it on both sides, which is exactly a dash inside a word.
#
# The laziness, not the order of the alternatives, is what keeps that safe. On
# "Iron Butterfly -In-A-Gadda-Da-Vida (Clear)" the engine tries the shortest
# artist first, and the earliest position where either alternative fits is after
# "Iron Butterfly" -- every dash inside the album is flanked by letters.
_SEPARATOR_RE = re.compile(
    r'^(?P<artist>.+?)'
    r'(?:\s+[-‐–]\s*|\s*[-‐–]\s+)'
    r'(?P<album>.+)$')


def _text(value) -> str:
    """A whitespace-collapsed string, or "" for anything that is not one.

    Every product-level field this crawler reads goes through here, so a
    retyped field is skipped and counted rather than raising .strip() up
    through the walk and aborting the whole source over one product. That the
    abort would otherwise be inert -- _sync_stock skips replace_stock_items()
    on a raise -- is not a defence: it freezes the store at its previous
    snapshot for as long as the one bad product is published.
    """
    return " ".join(value.split()) if isinstance(value, str) else ""


class Crawler:
    site_name: str = "Le Noise"
    base_url: str = "https://lenoise.ca"
    genre_summary: str = (
        "Montreal record store selling new and used vinyl across rock, metal, "
        "punk, jazz, soundtracks, hip-hop and Quebec francophone music, "
        "alongside band merchandise."
    )
    genre: str = "marketplace"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        products_seen = 0
        vinyl_typed = 0
        records = 0
        identity_missing = 0
        artist_missing = 0
        unreadable_stock = 0
        unreadable_variants = 0
        yielded = 0
        priced = 0
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            products_seen += 1
            if not self._is_vinyl_type(product):
                continue
            vinyl_typed += 1
            if self._is_other_medium(_text(product.get("title"))):
                continue
            records += 1
            variants, unreadable_entries = self._variants(product)
            if unreadable_entries or not variants:
                unreadable_variants += 1
            # One bracket per record for every way a product that WOULD have
            # yielded a row failed to, counted once against the first reason
            # that applies. Identity is asked before the artist because `title`
            # is identity AND the artist's own source, so a product that has
            # lost it has lost both, and reporting that as artist-source drift
            # names the wrong field.
            elif not self._has_identity(product):
                identity_missing += 1
            elif self._split_title(_text(product.get("title"))) is None:
                artist_missing += 1
            # all(), not any(): one readable variant must not vouch for the
            # corrupt ones beside it. A product whose variants are
            # [{"available": False}, {"available": "maybe"}] yields no row, and
            # under any() it incremented nothing -- so a store-wide retyping of
            # part of every variants array emptied the walk in silence, past
            # every guard, and the completed-but-empty walk deleted the
            # snapshot. Found by Copilot in review on PR #394.
            elif not all(isinstance(v.get("available"), bool) for v in variants):
                unreadable_stock += 1
            item = self._item(product)
            if item is not None:
                yielded += 1
                if item["price"] is not None:
                    priced += 1
                yield item
        # db.replace_stock_items() DELETEs this crawler's previous snapshot
        # before inserting, and _sync_stock only skips that call when the crawl
        # raised -- so a completed-but-empty walk is destructive where a raise is
        # inert. Each guard below names a distinct way the payload can stop
        # carrying what this crawler reads.
        if products_seen == 0:
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection returned no products -- renamed, "
                "removed, or payload drift")
        if not vinyl_typed:
            # The whole shelf losing the `Vinyl` product_type is the store's
            # format vocabulary having moved, not a sold-out catalog:
            # availability is not read until after this tally.
            raise RuntimeError(
                f"none of the {products_seen} products in the {_COLLECTION_SLUG} "
                f"collection carries the {_VINYL_PRODUCT_TYPE!r} product_type -- "
                "format-taxonomy drift")
        if not records:
            # Every product on the vinyl shelf reading as a CD or a cassette is
            # the medium bracket having changed meaning, not a shelf that really
            # has no records on it.
            raise RuntimeError(
                f"every one of the {vinyl_typed} vinyl-typed products in the "
                f"{_COLLECTION_SLUG} collection names a non-vinyl medium -- "
                "medium-bracket drift")
        if not yielded and unreadable_variants:
            # A `variants` collection that is absent, empty or retyped, or whose
            # entries are not mappings. Those discards are otherwise invisible,
            # and invisible is destructive: Shopify dropping the collection
            # store-wide leaves every product with no price and no stock flag,
            # and the guards below cannot see it because such a product reaches
            # none of their tallies.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while "
                f"{unreadable_variants} product(s) carry no readable variants -- "
                "variant-identity-source drift")
        if not yielded and identity_missing:
            # `title` and `handle` are identity, not display: item_key hashes the
            # row's title and URL, so a product missing either is skipped rather
            # than emitted under a fresh identity that would orphan the judgments
            # and saves keyed on its old one.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while "
                f"{identity_missing} record(s) carry no title or handle -- "
                "identity-source drift")
        if not yielded and artist_missing:
            # The artist is read from the title and from nowhere else -- `vendor`
            # here is a catalogue number as often as a name, and reversed
            # ("Chura Stef") as often as not -- so a title that stops carrying it
            # leaves the walk looking sold out. Gated on an empty outcome because
            # one artist-less product among real rows is an ordinary skipped row.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while "
                f"{artist_missing} record(s) carry no readable artist -- "
                "artist-source drift")
        if not yielded and unreadable_stock:
            # An empty result is only trustworthy when every product that could
            # have yielded a row was readable and simply out of stock. Counting
            # unreadable products rather than readable ones is what catches the
            # partial case: one genuinely sold-out record must not vouch for a
            # catalog that has gone unreadable behind it.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while "
                f"{unreadable_stock} record(s) carry no readable availability "
                "flag -- stock-source drift")
        if yielded and not priced:
            # Rows without the emptiness: `_price` answers None for a value it
            # cannot use, so a `price` field removed or retyped store-wide
            # re-lists the whole catalog with no prices, which is worse than the
            # snapshot it would replace. Isolated nulls stay tolerated.
            raise RuntimeError(
                f"none of the {yielded} rows from the {_COLLECTION_SLUG} "
                "collection carries a price -- price-source drift")

    @classmethod
    def _item(cls, product: dict) -> Optional[dict]:
        if not cls._is_vinyl_type(product):
            return None
        title = _text(product.get("title"))
        if cls._is_other_medium(title):
            return None
        if not cls._has_identity(product):
            return None
        split = cls._split_title(title)
        if split is None:
            return None
        artist, album = split
        variant = cls._pick_variant(product)
        if variant is None:
            return None
        return {
            "artist": artist,
            # The pressing bracket stays on the title. It is the only thing
            # separating two pressings of one album ("Henry St." vs "Henry St.
            # (Red)"), which are distinct products at distinct URLs, and it still
            # matches the catalog: db._library_release_match_sql matches a stock
            # title exactly OR as a prefix followed by a space, so "The Wall
            # (2LP)" matches catalog "The Wall".
            "title": album,
            # "Vinyl" unconditionally, as every sibling catalog crawler does. The
            # specific cut is already carried in the title's own bracket.
            "format": "Vinyl",
            "price": cls._price(variant),
            # The store prices in Canadian dollars: its storefront sets
            # `cart_currency=CAD`, and products.json carries no currency of its
            # own to read instead.
            "currency": "CAD",
            "url": f"{cls.base_url}/products/{_text(product.get('handle'))}",
            "cover_image_url": cls._cover(product, variant),
        }

    @staticmethod
    def _is_other_medium(title: str) -> bool:
        """True when the title's brackets name a non-vinyl medium and no vinyl one.

        Both halves are needed. Without the first, nothing keeps the shelf's
        miscategorised CDs and cassettes out -- `product_type` says `Vinyl` on
        every one of them. Without the second, the store's vinyl boxes that
        bundle discs ("(3LP/2CD)", "(4LP+4CD)") are thrown away with them.
        """
        brackets = _BRACKET_RE.findall(title)
        if not any(_NON_VINYL_MEDIUM_RE.search(b) for b in brackets):
            return False
        return not any(_VINYL_MEDIUM_RE.search(b) for b in brackets)

    @staticmethod
    def _is_vinyl_type(product: dict) -> bool:
        return _text(product.get("product_type")).lower() == _VINYL_PRODUCT_TYPE

    @staticmethod
    def _has_identity(product: dict) -> bool:
        return bool(_text(product.get("title")) and _text(product.get("handle")))

    @staticmethod
    def _split_title(title: str):
        """(artist, album) off the title's first spaced dash, or None.

        The title is the only artist source. There is no `vendor` fallback,
        unlike most Shopify crawlers in this fleet, because the field here is
        not one kind of thing: it holds the act ("Talking Heads"), a bare
        catalogue number ("MODVL128"), the act surname-first ("Chura Stef"), a
        genre-first label ("Soundtrack - John Carpenter") and the act with its
        catalogue number glued on ("Talos - 5053880405"), with nothing in the
        payload marking which. Crediting a record to any of those is worse than
        skipping it: a wrong artist is indistinguishable from a right one
        everywhere downstream.
        """
        match = _SEPARATOR_RE.match(title)
        if match is None:
            return None
        artist = match.group("artist").strip()
        album = match.group("album").strip()
        if not artist or not album:
            return None
        return artist, album

    @staticmethod
    def _variants(product: dict):
        """(the product's mapping variants, the number of entries that were not).

        The drop count is returned rather than discarded because a dropped
        entry is otherwise invisible to every guard in `crawl_catalog`: a
        product whose variants are `[{"available": False}, None]` keeps one
        readable variant, so the collection is neither empty nor unreadable,
        and it yields no row while incrementing nothing. Shopify retyping part
        of every variants array store-wide would empty the walk in exactly that
        silence, and a completed-but-empty walk deletes the snapshot. Found by
        Copilot in review on PR #394.
        """
        variants = product.get("variants")
        if not isinstance(variants, (list, tuple)):
            return [], 0
        kept = [v for v in variants if isinstance(v, dict)]
        return kept, len(variants) - len(kept)

    @classmethod
    def _pick_variant(cls, product: dict) -> Optional[dict]:
        """The cheapest in-stock variant, or None when nothing is in stock.

        One row per *product*, never per variant. db.compute_item_key hashes
        (artist, title, url), and every variant of a product shares all three --
        so a per-variant fan-out would emit rows that collide on item_key, which
        replace_stock_items INSERTs without an ON CONFLICT guard.

        Live, this store publishes exactly one variant per product, named
        "Default Title": the pressing is a separate product here, not an option
        axis. The cheapest-of pick is therefore inert today and kept anyway, as
        the shape that stays correct if the store ever splits a record across
        variants -- which is the same reason waterloorecords.py picks that way.

        Only the literal True admits a variant: the string "false" is truthy, so
        a falsiness test would publish a sold-out record as in stock.
        """
        available = [v for v in cls._variants(product)[0] if v.get("available") is True]
        if not available:
            return None
        priced = [v for v in available if cls._price(v) is not None]
        # No parseable price anywhere: still emit the row (price None), same as
        # every sibling, rather than dropping in-stock vinyl over a bad field.
        if not priced:
            return available[0]
        return min(priced, key=cls._price)

    @staticmethod
    def _price(variant: dict) -> Optional[float]:
        raw = variant.get("price")
        # bool before float(): bool is an int subclass, so True would price a
        # record at 1. nan is the other one a truthiness check cannot catch --
        # and it counts toward `priced` as readily as a real price, so a
        # store-wide retyping to "NaN" would satisfy `price-source drift` while
        # publishing a catalog of prices no reader can use. Found by Copilot in
        # review on PR #394; this is the parser the recent sibling catalog
        # crawlers already share.
        if isinstance(raw, bool):
            return None
        try:
            price = float(raw)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(price) or price <= 0:
            return None
        return price

    @staticmethod
    def _cover(product: dict, variant: dict) -> Optional[str]:
        """resolve_cover_image() with its two collections type-checked first.

        The shared helper reads `variant["featured_image"].get(...)` and
        `product["images"][0].get(...)` behind `or` guards, which catch a missing
        or null field but pass a *retyped* one straight through to .get() -- and
        a raise there aborts the whole source over one product's artwork, which
        is display-only. Guarded here rather than in `shopify_catalog`, because
        every Shopify crawler in the fleet reads that helper and this is one
        store's payload, not a fleet-wide change to make from inside a crawler.

        Type-checking the two CONTAINERS is not enough: the helper returns
        whatever sits at `src` without looking at it, so a nested
        `{"src": 123}` comes back as the row's `cover_image_url` in breach of
        the `Optional[str]` contract, and `replace_stock_items()` then hands an
        int to a Postgres TEXT column -- killing the whole refresh over
        display-only artwork, which is the failure this boundary exists to
        prevent, arriving one level deeper. So `src` has to be a non-empty
        string too, and an image without one is passed over rather than allowed
        to answer. Found by Copilot in review on PR #394, as it was on PR #337
        for `joyfulnoiserecordings.py`.
        """
        raw = product.get("images")
        raw = raw if isinstance(raw, (list, tuple)) else []
        images = [i for i in raw if _text(i.get("src") if isinstance(i, dict) else None)]
        featured = variant.get("featured_image")
        if not (isinstance(featured, dict) and _text(featured.get("src"))):
            variant = {**variant, "featured_image": None}
        return resolve_cover_image({**product, "images": images}, variant)
