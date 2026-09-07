import html
import math
import re
from typing import AsyncIterator, Optional, Tuple
from shopify_catalog import iter_products, resolve_cover_image

# The store's own format shelf, at the URL the request named. It is the
# whole vinyl catalog and not a curated subset: confirmed 2026-09-07 that it
# holds exactly the products the root products.json types `Vinyl`, no more
# and no fewer. The boxsets that bundle a record sit under `Boxset / Bundle`
# outside it, and stay out. Its pre-orders are not marked: they carry no
# tag, no title marker and no distinct availability, and are known only by
# membership of the store's `pre-order` collection -- which is not read,
# because the only place a marker could go is the title, and
# compute_item_key hashes the title, so a marker that disappears when the
# record ships would re-key the row and orphan its listings, judgments and
# saves. Same call as musiconvinyl.py and darksiderecords.py.
_COLLECTION_SLUG = "vinyl"
# `product_type` here IS the format: `Vinyl` against `CD`, `MC`, `Boxset /
# Bundle`, `Artbook`, `T-Shirt` and the rest. Matched as the word at the
# start of the type rather than by equality so a subtype the store might
# introduce (`Vinyl - 2LP`) stays in scope, while a type that does not begin
# with it is not a format claim and stays out.
_VINYL_TYPE_RE = re.compile(r"^vinyl(?![a-z])", re.IGNORECASE)
# `vendor` is the literal string `details` on every product, so the artist
# lives only in the product title, as `Artist - Album (descriptor)`. The
# first ` - ` is the split: an album can carry one of its own (`Surturian -
# II - Hessian Spears`, `Nachtmystium - Addicts - Black Meddle Pt. II`), an
# artist never does -- checked against the store's one-collection-per-artist
# list, where every artist read this way is a collection title save one
# spelt with a diacritic the title drops.
_ARTIST_SEP_RE = re.compile(r"^(?P<artist>.+?)\s+-\s+(?P<rest>.+)$")
# Bare "Various", not "Various Artists" -- Discogs' own entity name is
# "Various", and two consumers compare against that exact string:
# amazon.py's Crawler._artist() only special-cases the literal "various"
# (case-insensitively) to search by title alone, and db.py's
# _library_release_match_sql does an exact LOWER() equality against the
# catalog artist. Same rewrite as musiconvinyl.py and cleorecs.py.
_VARIOUS_ARTISTS = frozenset({"various artists", "various"})
# Shopify's placeholder for a product with exactly one variant. It names no
# pressing, so a row built on it carries the product title alone -- and only
# when it IS the product's sole variant: on a multi-variant product the
# placeholder is malformed data, and a blank title is never a pressing.
# Either would otherwise share the bare title and the product URL, and so
# the item_key, with every sibling built the same way.
_PLACEHOLDER_VARIANT = "default title"
# The type has already said the product is a record, so the variant gate is
# negative: a variant title here is a colour ("Splatter", "Clear/Black
# Marble"), a catalogue number ("DSR357LP-blk", "NVP236LPS") or a format-and-
# colour pair ("Vinyl Picture LP / Picture"), and it is a pressing unless it
# names a different medium. No live variant does; the gate is for the day a
# `Format` option grows a CD beside the LP. Vocabulary and check order follow
# iodinerecords.py and spv.py: a vinyl word decides first, then a merch word,
# then an inch marker, then the non-vinyl media.
_VINYL_WORDS = (r"\d*[x×-]?lps?", r"vinyls?", r"picture\s+discs?")
_NON_VINYL_MEDIA_WORDS = (
    r"\d*[x×-]?cds?", r"digital", r"digipa[kc]k?s?", r"cassettes?", r"tapes?",
    r"mcs?", r"\d*[x×-]?dvds?", r"blu-?rays?",
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


class Crawler:
    site_name: str = "SPKR.store"
    base_url: str = "https://spkr.store"
    genre_summary: str = "Prophecy Productions' own mailorder in Flußbach, Germany — black metal, doom, dark folk and post-metal from Prophecy, Lupus Lounge, Auerbach, Magnetic Eye, Dependent, House of Mythology, Darkness Shall Rise and Nordvis."
    genre: str = "metal"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        products_seen = 0
        vinyl_seen = 0
        artist_ok = 0
        identity_missing = 0
        unreadable_stock = 0
        yielded = 0
        priced = 0
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            products_seen += 1
            # Nested, not sibling tallies: only a product with the Vinyl
            # type, an artist in its title, an identity AND a variant the
            # gate admits could have yielded a row, so only that product's
            # stock readability says anything about an empty result.
            # Tallied independently, one product could satisfy each
            # condition while none of them can yield.
            if self._is_vinyl(product):
                vinyl_seen += 1
                artist, _ = self._artist_title(product.get("title"))
                if artist:
                    artist_ok += 1
                    if not self._has_identity(product):
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
        if products_seen == 0:
            raise RuntimeError(f"{_COLLECTION_SLUG} collection returned no products -- renamed, removed, or markup drift")
        if vinyl_seen == 0:
            raise RuntimeError(f"no product in the {_COLLECTION_SLUG} collection carries the Vinyl product_type -- format-taxonomy drift")
        if artist_ok == 0:
            # The artist is read out of the product title's ` - ` convention,
            # so this is the guard that notices the store renaming its
            # records or moving the artist into `vendor`: every record would
            # then be skipped while the type tally stayed non-zero.
            raise RuntimeError(f"no Vinyl product in the {_COLLECTION_SLUG} collection has a title of the form Artist - Album -- artist-source drift")
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
        if not cls._is_vinyl(product):
            return []
        artist, title = cls._artist_title(product.get("title"))
        if not artist:
            return []
        if not cls._has_identity(product):
            return []
        url = f"{cls.base_url}/products/{(product.get('handle') or '').strip()}"
        items = []
        for variant, descriptor in cls._pressings(product):
            # Only the literal True admits a variant: the string "false" is
            # truthy, so a falsiness test would publish a sold-out record as
            # in stock. Anything else -- False, "false", 1, None, absent --
            # is skipped, which is also what keeps this filter and
            # _has_readable_stock_flag agreeing on what "readable" means.
            # No pre-order bypass: every live pre-order reports True on
            # every pressing, so a False beside one is gone allocation.
            if variant.get("available") is not True:
                continue
            # The pressing is appended on every row that names one, not only
            # when the product has more than one variant: a sibling being
            # listed or delisted must not re-title the rows and orphan the
            # listings, judgments and saves keyed on the old identity. The
            # placeholder is the one exception, because it names nothing,
            # and _pressings admits it only as a product's sole variant, so
            # no sibling can share the bare title.
            items.append({
                "artist": artist,
                "title": f"{title} — {descriptor}" if descriptor else title,
                "format": "Vinyl",
                "price": cls._price(variant),
                "currency": "EUR",
                "url": url,
                "cover_image_url": resolve_cover_image(product, variant),
            })
        return items

    @staticmethod
    def _is_vinyl(product: dict) -> bool:
        return bool(_VINYL_TYPE_RE.match((product.get("product_type") or "").strip()))

    @staticmethod
    def _artist_title(title) -> Tuple[str, str]:
        """Split `Artist - Album (descriptor)` into (artist, rest) -- ("", "") when the title is not of that form.

        The rest is kept verbatim, format descriptor and all ("Castle Curtains
        (Vinyl LP)", "Monark (Vinyl Gatefold LP)"): the descriptor is where the
        store says which pressing a single-variant product is ("Vinyl LP -
        Black"), title_key folds its format words away for the Cheapest
        filter, and the library match behind the Store tab's Collection and
        Wantlist filters is exact-or-prefix-with-space, which "Castle Curtains
        (Vinyl LP)" still satisfies for a library "Castle Curtains".
        """
        m = _ARTIST_SEP_RE.match(" ".join((title or "").split()))
        if m is None:
            return "", ""
        artist = m.group("artist").strip()
        rest = m.group("rest").strip()
        if not artist or not rest:
            return "", ""
        if artist.lower() in _VARIOUS_ARTISTS:
            artist = "Various"
        return artist, rest

    @classmethod
    def _pressings(cls, product: dict) -> list:
        pairs = []
        # Non-mapping entries are dropped here, before anything reads them,
        # so a junk entry is an ordinary skipped row rather than an
        # AttributeError from inside the yield loop.
        variants = [v for v in product.get("variants") or [] if isinstance(v, dict)]
        for variant in variants:
            # Unescaped because the store types entities into a handful of
            # variant titles (`&hellip; (DSR308LPgold)`), and the row is read
            # by a person, not a browser.
            title = " ".join(html.unescape(variant.get("title") or "").split())
            if not title:
                continue
            if title.lower() == _PLACEHOLDER_VARIANT:
                if len(variants) == 1:
                    pairs.append((variant, ""))
                continue
            if cls._is_pressing(title):
                pairs.append((variant, title))
        return pairs

    @staticmethod
    def _is_pressing(title: str) -> bool:
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
    def _has_readable_stock_flag(pressings: list) -> bool:
        # every(), not any(): one readable variant does not make the product
        # readable. A product whose black pressing is a readable False and
        # whose coloured pressing carries the string "false" yields nothing,
        # and under any() would vouch for an emptiness half its own doing.
        # Scoped to the admitted pressings, because they are the only ones
        # that could have yielded.
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
