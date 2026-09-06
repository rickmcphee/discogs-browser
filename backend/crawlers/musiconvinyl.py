import math
import re
from typing import AsyncIterator, Optional
from shopify_catalog import iter_products, strip_vendor_prefix, resolve_cover_image

# The store's own "ALL VINYL" collection, at the URL the request named. It is
# the vinyl shelf and not merely a curated subset: confirmed live (2026-09-06)
# that every product Shopify's built-in `all` collection adds to it is a CD,
# a CD+Blu-ray, a Blu-ray+DVD or an artbook, so walking `all` instead would
# buy nothing the type gate below does not then drop. `collections.json`
# reports the collection as far larger than the endpoint returns; that figure
# counts unpublished products, and the endpoint agrees with the product
# sitemap and with the storefront's own listing pages.
_COLLECTION_SLUG = "all-products"
# The store types its records `Vinyl` and its discs `CD`, `CD+Bluray`,
# `Bluray + DVD`; the vinyl shelf itself carries one CD box set mis-shelved
# under `CD` and one record typed `Music`, so the collection's membership is
# not the gate -- the type is. Matched as the word `Vinyl` at the start of
# the type rather than by equality so a variant the store might introduce
# (`Vinyl - 2LP`, as rhino.py's store writes them) stays in scope; a type
# that does not begin with it (`Music`) is not a format claim and stays out,
# which is the safer direction for a single product.
_VINYL_TYPE_RE = re.compile(r"^vinyl(?![a-z])", re.IGNORECASE)
# Bare "Various", not "Various Artists" -- Discogs' own entity name is
# "Various", and two consumers compare against that exact string:
# amazon.py's Crawler._artist() only special-cases the literal "various"
# (case-insensitively) to search by title alone, and db.py's
# _library_release_match_sql does an exact LOWER() equality against the
# catalog artist. "Various Artists" satisfies neither. Same rewrite as
# angryyoungandpoor.py and cleorecs.py. `Original Soundtrack`, the store's
# other collective credit, is left as written: Discogs credits a score to
# its composer and a song compilation to Various, and nothing in the payload
# says which a given product is, so a rewrite would be a guess either way.
_VARIOUS_VENDORS = frozenset({"various artists", "various"})


class Crawler:
    site_name: str = "Music On Vinyl"
    base_url: str = "https://www.musiconvinyl.com"
    genre_summary: str = "Dutch audiophile reissue label — 180g pressings of classic rock, jazz, soul, soundtracks and Dutch pop, licensed from the major catalogs."
    genre: str = "rock"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        products_seen = 0
        vinyl_seen = 0
        vendor_ok = 0
        identity_missing = 0
        unreadable_stock = 0
        yielded = 0
        priced = 0
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            products_seen += 1
            # Nested, not sibling tallies: only a product with the vinyl
            # type, a vendor, an identity AND a readable flag could have
            # yielded a row, so only that product's readability says
            # anything about an empty result. Tallied independently, one
            # product could satisfy each condition while none can yield.
            if self._is_vinyl(product):
                vinyl_seen += 1
                if self._artist(product):
                    vendor_ok += 1
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
            # A label whose whole catalog is vinyl reissues does not stop
            # typing records, so zero is drift in the type taxonomy rather
            # than a sold-out shelf.
            raise RuntimeError(f"no product in the {_COLLECTION_SLUG} collection carries a vinyl product_type -- format-taxonomy drift")
        if vendor_ok == 0:
            raise RuntimeError(f"no vinyl product in the {_COLLECTION_SLUG} collection carries a vendor -- artist-source drift")
        if yielded and not priced:
            # Rows without the emptiness: `_price` answers None for a value
            # it cannot use, so a `price` field removed or retyped
            # store-wide re-lists the whole catalog with no prices, which is
            # worse than the snapshot it would replace. Isolated nulls stay
            # tolerated. Same guard as rhino.py and matadorrecords.py.
            raise RuntimeError(
                f"none of the {yielded} rows from the {_COLLECTION_SLUG} collection carries a "
                "price -- price-source drift")
        if not yielded and identity_missing:
            # `title` and `handle` are identity, not display: item_key hashes
            # the row's title and URL, so a product missing either is skipped
            # rather than emitted under a fresh identity that would orphan
            # the judgments and saves keyed on its old one. Skipped rows
            # leave the walk looking sold out, which is why the same
            # empty-outcome gate as the stock guard below applies.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while "
                f"{identity_missing} vinyl product(s) carry no title or handle -- identity-source drift")
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
        artist = cls._artist(product)
        if not artist:
            return []
        if not cls._has_identity(product):
            return []
        # The store keeps the artist out of `title` -- a self-titled album is
        # the bare name, with no separator -- so the shared exact-case " - "
        # strip is the drift guard it is on the sibling stores rather than a
        # live transformation. Stripped against the vendor as written, since
        # that is the spelling a prefix would carry.
        title = strip_vendor_prefix((product.get("title") or "").strip(), (product.get("vendor") or "").strip())
        url = f"{cls.base_url}/products/{product.get('handle', '')}"

        # Non-mapping entries are dropped before anything reads them, so a
        # junk entry is an ordinary skipped row rather than an AttributeError
        # from inside the loop.
        variants = [v for v in (product.get("variants") or []) if isinstance(v, dict)]
        items = []
        seen_titles = set()
        for variant in variants:
            # Only the literal True admits a variant: the string "false" is
            # truthy, so a falsiness test would publish a sold-out record as
            # in stock. Anything else -- False, "false", 1, None, absent --
            # is skipped, which is also what keeps this filter and
            # _has_readable_stock_flag agreeing on what "readable" means.
            # No pre-order bypass: the store flags a purchasable pre-order
            # available, and the few unavailable ones are gone allocation
            # (a `Ltd. 250` pressing sold through before release). And no
            # pre-order label either: compute_item_key hashes the title, so
            # a marker that disappears when the record ships would re-key
            # the row and orphan its listings, judgments and saves -- the
            # same identity churn darksiderecords.py declined for its store.
            if variant.get("available") is not True:
                continue
            # The descriptor is the variant's own title and nothing else --
            # never the sibling count. compute_item_key hashes the title, so
            # a descriptor that appeared the day a sibling was listed would
            # re-key this row over a change to a *different* variant. Under
            # this rule the row changes only when the store renames the
            # variant itself, which Shopify does when a product gains
            # options. Two variants without a usable title would resolve to
            # the same row; the second is skipped rather than emitted under
            # a colliding key.
            descriptor = cls._variant_descriptor(variant)
            display_title = f"{title} — {descriptor}" if descriptor else title
            if display_title in seen_titles:
                continue
            seen_titles.add(display_title)
            items.append({
                "artist": artist,
                "title": display_title,
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
    def _artist(product: dict) -> str:
        vendor = (product.get("vendor") or "").strip()
        if vendor.lower() in _VARIOUS_VENDORS:
            return "Various"
        return vendor

    @staticmethod
    def _has_identity(product: dict) -> bool:
        return bool((product.get("title") or "").strip()) and bool((product.get("handle") or "").strip())

    @staticmethod
    def _has_readable_stock_flag(product: dict) -> bool:
        # every(), not any(): one readable variant does not make the product
        # readable. A product whose first variant is a readable False and
        # whose second carries the string "false" yields nothing, and under
        # any() would vouch for an emptiness half its own doing. Non-mapping
        # entries are excluded to stay consistent with _items(); a product
        # left with none is unreadable, not vacuously readable.
        variants = [v for v in (product.get("variants") or []) if isinstance(v, dict)]
        return bool(variants) and all(
            isinstance(v.get("available"), bool) for v in variants)

    @staticmethod
    def _variant_descriptor(variant: dict) -> str:
        # Empty on the live catalog, which is single-variant throughout:
        # every product carries one `Default Title` variant, and the store
        # lists a coloured pressing as its own product rather than as a
        # variant. Shopify's placeholder is not a pressing name, so a
        # single-variant product's row is the bare title; a variant the
        # store has named gives that name.
        title = " ".join((variant.get("title") or "").split())
        return "" if title == "Default Title" else title

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
