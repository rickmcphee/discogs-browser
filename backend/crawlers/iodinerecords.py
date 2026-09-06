import math
import re
from typing import AsyncIterator, Optional, Tuple
from shopify_catalog import iter_products, has_tag, resolve_cover_image

# Shopify's built-in all-products collection. The store's own `12` and `7`
# collections are curated shelves, not format shelves: confirmed 2026-09-06
# that between them they hold well under two-thirds of the vinyl-tagged
# records `all` returns, and `all` agrees with the root products.json and
# with meta.json's published_products_count. The format scoping is done by
# the tag gate below instead.
_COLLECTION_SLUG = "all"
# `product_type` is a music gate, never a format one: `Records` holds vinyl,
# CD and cassette products side by side, and the other live types are
# `Merch` and `Books`. Enumerated rather than matched negatively so that a
# non-music type the store might add stays out by default.
_MUSIC_TYPES = frozenset({"records"})
_PREORDER_TAG = "preorder"
_VINYL_TAG = "vinyl"
# Shopify's placeholder for a product with exactly one variant. It names no
# pressing, so a row built on it carries the album title alone.
_PLACEHOLDER_VARIANT = "default title"
# `vendor` is always the label (its own name or a distro label), so the
# artist lives only in the product title, as `Artist 'Album'` -- straight
# single quotes on nearly every product, double quotes on a couple -- with an
# optional edition after the closing quote (`Piebald 'We Are The Only
# Friends We Have' Deluxe Edition`, `Quicksand 'Slip' (Deluxe)`). The quotes
# are found structurally rather than by class: the opening one must follow
# whitespace and the closing one must precede whitespace or the end, because
# both halves carry apostrophes of their own ("Her Head's on Fire 'Am I Not
# Your Girl?'", "New Forms 'Nothing's Sacred Anymore'") that a bare quote
# class would mistake for a delimiter. Typographic quotes are accepted on
# both sides though no live title uses them.
_TITLE_RE = re.compile(r"""^(?P<artist>.+?)\s+['‘"“](?P<album>.+?)['’"”](?=\s|$)\s*(?P<extra>.*)$""")
# The format gate has two layers. The PRODUCT layer is positive and reads
# tags: the store tags every record `Vinyl` and every product with a
# `format:` tag naming its media (`format:12"`, `format:7"`, `format:lp`,
# `format:2xlp` against `format:cd`, `format:cassette`, `format:merch`,
# `format:book`). Both are read because a handful of products carry only the
# `format:` tag (the ones filed under a newer `artist:[...]`/`album:[...]`
# taxonomy). The VARIANT layer is negative, because a variant title here is
# the pressing's colour and often nothing else ("Coke Bottle", "Silver",
# "Blood and Paper Stripes"): on a vinyl-tagged product a variant is a
# record unless its title says CD, cassette, tape, DVD or digital. Vocabulary
# and check order follow spv.py: a vinyl word decides first, then a merch
# word, then an inch marker, then the non-vinyl media -- so `Black 12" Vinyl
# + DVD` and `12" + DVD` are records with an extra, while a `12" x 12"
# Poster` is a measurement rather than a format claim. Every noun carries
# `s?` (`\blp\b` cannot see the "LPs" in "2 LPs + CD") and the disc-count
# prefix admits `2xLP` and `3xLP`, where no word boundary separates the
# count from the noun.
_VINYL_WORDS = (r"\d*[x×]?lps?", r"vinyls?", r"picture\s+discs?")
_NON_VINYL_MEDIA_WORDS = (
    r"\d*[x×]?cds?", r"digital", r"digipa[kc]k?s?", r"cassettes?", r"tapes?",
    r"mcs?", r"\d*[x×]?dvds?", r"blu-?rays?",
)
_MERCH_WORDS = (
    r"t-?shirts?", r"shirts?", r"tees?", r"hoodies?", r"longsleeves?", r"posters?",
    r"prints?", r"patche?s?", r"mugs?", r"pins?", r"stickers?", r"totes?",
    r"bags?", r"hats?", r"caps?",
)
_INCH = r'(?<![a-z0-9])(?:\d+[x×])?\d{1,2}\s*-?\s*(?:"|”|″|inch(?:es)?\b)'


def _alternation(*tuples):
    return "|".join(word for group in tuples for word in group)


_VINYL_WORD_RE = re.compile(r"(?<![a-z])(?:%s)\b" % _alternation(_VINYL_WORDS), re.IGNORECASE)
_MERCH_RE = re.compile(r"\b(?:%s)\b" % _alternation(_MERCH_WORDS), re.IGNORECASE)
_NON_VINYL_MEDIA_RE = re.compile(r"(?<![a-z])(?:%s)\b" % _alternation(_NON_VINYL_MEDIA_WORDS), re.IGNORECASE)
_INCH_RE = re.compile(_INCH, re.IGNORECASE)
_VINYL_FORMAT_TAG_RE = re.compile(r"^format:\s*(?:%s|%s)\s*$" % (_alternation(_VINYL_WORDS), _INCH), re.IGNORECASE)


class Crawler:
    site_name: str = "Iodine Recordings"
    base_url: str = "https://iodinerecords.com"
    genre_summary: str = "Massachusetts punk, hardcore, emo and screamo label — Piebald, Stretch Arm Strong, NØ MAN and Quicksand reissues, plus a Hydra Head Records distro."
    genre: str = "punk"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        products_seen = 0
        records_seen = 0
        artist_ok = 0
        vinyl_seen = 0
        identity_missing = 0
        unreadable_stock = 0
        yielded = 0
        priced = 0
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            products_seen += 1
            # Nested, not sibling tallies: only a product with the Records
            # type, an artist in its title, a vinyl tag AND a variant the
            # negative gate admits could have yielded a row, so only that
            # product's stock readability says anything about an empty
            # result. Tallied independently, one product could satisfy each
            # condition while none of them can yield.
            if self._is_music(product):
                records_seen += 1
                artist, _ = self._artist_album(product.get("title"))
                if artist:
                    artist_ok += 1
                    vinyl = self._vinyl_variants(product)
                    if vinyl:
                        vinyl_seen += 1
                        if not self._has_identity(product):
                            identity_missing += 1
                        elif not self._has_readable_stock_flag(vinyl):
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
        if records_seen == 0:
            raise RuntimeError(f"no product in the {_COLLECTION_SLUG} collection carries the Records product_type -- format-taxonomy drift")
        if artist_ok == 0:
            # The artist is read out of the product title's quoting
            # convention, so this is the guard that notices the store
            # renaming its records (`Artist - Album`, say): every record
            # would then be skipped while the type tally stayed non-zero.
            raise RuntimeError(f"no Records product in the {_COLLECTION_SLUG} collection has a title of the form Artist 'Album' -- artist-source drift")
        if vinyl_seen == 0:
            # The format is read off the tags, so this is the guard that
            # notices the store moving it somewhere else (a metafield, the
            # type) or losing its variants: every product would then yield
            # nothing while the tallies above stayed non-zero. A label whose
            # catalog is records does not stop pressing them, so zero has no
            # innocent reading.
            raise RuntimeError(f"no Records product in the {_COLLECTION_SLUG} collection carries a vinyl tag and a variant that reads as a record -- format-source drift")
        if yielded and not priced:
            # Rows without the emptiness: `_price` answers None for a value
            # it cannot use, so a `price` field removed or retyped
            # store-wide re-lists the whole catalog with no prices, which is
            # worse than the snapshot it would replace. Isolated nulls stay
            # tolerated.
            raise RuntimeError(
                f"none of the {yielded} rows from the {_COLLECTION_SLUG} collection carries a "
                "price -- price-source drift")
        if not yielded and identity_missing:
            # `handle` is identity, not display: item_key hashes the row's
            # URL, so a product missing it is skipped rather than emitted
            # under a fresh identity that would orphan the judgments and
            # saves keyed on its old one. Skipped rows leave the walk looking
            # sold out, which is why the same empty-outcome gate as the stock
            # guard below applies.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while "
                f"{identity_missing} vinyl product(s) carry no handle -- identity-source drift")
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
        artist, album = cls._artist_album(product.get("title"))
        if not artist:
            return []
        if not cls._has_identity(product):
            return []
        url = f"{cls.base_url}/products/{product.get('handle', '')}"
        # No pre-order bypass: every live pre-order reports available True,
        # and the sold-out colour beside it is gone allocation, tag or no
        # tag. The tag only marks the title.
        base = f"{album} (Pre-Order)" if has_tag(product, _PREORDER_TAG) else album
        items = []
        for variant, descriptor in cls._vinyl_variants(product):
            # Only the literal True admits a variant: the string "false" is
            # truthy, so a falsiness test would publish a sold-out record as
            # in stock. Anything else -- False, "false", 1, None, absent --
            # is skipped, which is also what keeps this filter and
            # _has_readable_stock_flag agreeing on what "readable" means.
            if variant.get("available") is not True:
                continue
            # The pressing is appended on every row that names one, not only
            # when the product has more than one variant: a CD or cassette
            # sibling being listed or delisted must not re-title the vinyl
            # rows and orphan the listings, judgments and saves keyed on the
            # old identity. The placeholder is the one exception, because it
            # names nothing and Shopify only issues it for a product with
            # exactly one variant.
            items.append({
                "artist": artist,
                "title": f"{base} — {descriptor}" if descriptor else base,
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
    def _artist_album(title) -> Tuple[str, str]:
        """Split `Artist 'Album' [edition]` into (artist, album) -- ("", "") when the title is not of that form.

        An edition after the closing quote stays on the album ("Slip (Deluxe)",
        "Alone With Heaven Deluxe 2xLP"): the store sells such editions as
        separate products, and the library match behind the Store tab's
        Collection and Wantlist filters is exact-or-prefix-with-space, which
        "Slip (Deluxe)" still satisfies for a library "Slip".
        """
        m = _TITLE_RE.match(" ".join((title or "").split()))
        if m is None:
            return "", ""
        artist = m.group("artist").strip()
        album = m.group("album").strip()
        extra = m.group("extra").strip()
        if not artist or not album:
            return "", ""
        return artist, f"{album} {extra}" if extra else album

    @classmethod
    def _vinyl_variants(cls, product: dict) -> list:
        if not cls._is_vinyl_product(product):
            return []
        pairs = []
        # Non-mapping entries are dropped here, before anything reads them,
        # so a junk entry is an ordinary skipped row rather than an
        # AttributeError from inside the yield loop.
        for variant in product.get("variants") or []:
            if not isinstance(variant, dict):
                continue
            title = " ".join((variant.get("title") or "").split())
            if title.lower() == _PLACEHOLDER_VARIANT:
                pairs.append((variant, ""))
            elif cls._is_vinyl_variant(title):
                pairs.append((variant, title))
        return pairs

    @staticmethod
    def _is_vinyl_product(product: dict) -> bool:
        if has_tag(product, _VINYL_TAG):
            return True
        return any(_VINYL_FORMAT_TAG_RE.match((t or "").strip()) for t in product.get("tags") or [])

    @staticmethod
    def _is_vinyl_variant(title: str) -> bool:
        # Negative, unlike matadorrecords.py's gate: the product layer has
        # already said this is a record, so a variant title that names no
        # format at all is a pressing colour, and only a title that names a
        # different medium says otherwise.
        if _VINYL_WORD_RE.search(title):
            return True
        if _MERCH_RE.search(title):
            return False
        if _INCH_RE.search(title):
            return True
        return not _NON_VINYL_MEDIA_RE.search(title)

    @staticmethod
    def _has_identity(product: dict) -> bool:
        return bool((product.get("title") or "").strip()) and bool((product.get("handle") or "").strip())

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
