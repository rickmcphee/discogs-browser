import math
import re
from typing import AsyncIterator, Optional
from shopify_catalog import iter_products, has_tag, strip_vendor_prefix, resolve_cover_image

_PREORDER_TAG = "sfccPreOrderProduct"
# Shopify's built-in all-products collection, not the store's own `vinyl` one.
# `vinyl` is both incomplete and contaminated: confirmed live 2026-09-04 it
# omits vinyl-typed products the store publishes (its 579 `Vinyl - LP` against
# 679 store-wide) while itself carrying CDs, CD-only box sets and bundles, so
# it is neither a superset nor a clean subset. `all` is the whole published
# catalog -- it contains every product `vinyl`, `music` and `all-products`
# hold, and agrees with the product sitemap -- which leaves the format scoping
# to the type gate below, where the store's own structured format field can do
# it properly.
_COLLECTION_SLUG = "all"
# The store types products as "{family} - {variant}": `Vinyl - LP`,
# `Vinyl - 2LP`, `Vinyl - Single` against `CD - Album`, `CD - Single`,
# `Boxset - Mixed`, `Boxset - CD Only`, `Blu-Ray`, `Cassette`, `reel to reel`
# and the apparel types. Matching the `Vinyl` family rather than enumerating
# its live variants keeps a new variant (`Vinyl - 3LP`, `Vinyl - EP`) in scope
# instead of silently dropping a shelf's worth of records; the family prefix is
# safe to read that widely because accessories carry their own top-level types
# (`Slipmat`, `Poster/Print`, `Patch`) rather than a `Vinyl - ` one -- confirmed
# by sweeping every admitted title for accessory words and finding only album
# titles ("Picture Book", "Pin Ups", "Hatful Of Hollow").
#
# `Boxset - Vinyl Only` is the one vinyl type outside that family and is named
# in full. Its siblings `Boxset - Mixed` and `Boxset - CD Only` are deliberately
# excluded: `Boxset - Mixed` spans sets that are all CD/DVD ("A (The 40th
# Anniversary Edition) 3CD/3DVD") and sets that do hold a record, with nothing
# in the payload separating them, so admitting the type would label CD box sets
# as vinyl. Same accepted scope loss as the sibling stores' box sets.
_VINYL_TYPE_RE = re.compile(r"^(?:vinyl\s*-\s*\S|boxset\s*-\s*vinyl\s+only\s*$)", re.IGNORECASE)


class Crawler:
    site_name: str = "Rhino"
    base_url: str = "https://store.rhino.com"
    genre_summary: str = "Warner Music's catalog reissue label — classic rock, soul and pop, heavy on deluxe and audiophile pressings."
    genre: str = "rock"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        products_seen = 0
        vinyl_seen = 0
        vendor_ok = 0
        stock_flag_ok = 0
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            products_seen += 1
            if _VINYL_TYPE_RE.match((product.get("product_type") or "").strip()):
                vinyl_seen += 1
                if (product.get("vendor") or "").strip():
                    vendor_ok += 1
                    # Nested, not a sibling: a row needs the vinyl type, a
                    # vendor and a readable flag on *one* product, so two
                    # independent tallies can each be satisfied by a different
                    # product that cannot yield on its own -- one with a vendor
                    # but no readable flag, one with a readable flag but no
                    # vendor -- and the walk then completes empty with both
                    # passing. The conjunction is what makes a non-zero tally
                    # mean "some product would have yielded a row if it were in
                    # stock".
                    if self._has_readable_stock_flag(product):
                        stock_flag_ok += 1
            for item in self._items(product):
                yield item
        # db.replace_stock_items() DELETEs this crawler's previous snapshot
        # before inserting, and _sync_stock only skips that call when the crawl
        # raised -- so a completed-but-empty walk is destructive where a raise
        # is inert. Each guard names a distinct way the payload can stop
        # carrying what this crawler reads, and none of them can fire on a
        # merely quiet catalog: sold-out products still count toward every
        # tally, because each one is taken before the availability filter.
        if products_seen == 0:
            raise RuntimeError(f"{_COLLECTION_SLUG} collection returned no products -- renamed, removed, or markup drift")
        if vinyl_seen == 0:
            # A store whose whole reason to exist is catalog vinyl reissues does
            # not stop typing records, so zero is drift in the type taxonomy
            # rather than a sold-out shelf. udiscovermusic.py deliberately omits
            # this guard because its collection is genuinely mixed-format and an
            # all-CD run is indistinguishable from a renamed type; here the gate
            # reads the store's own format field over the entire catalog, where
            # an empty result has no innocent reading. It still cannot catch a
            # *partial* rename -- `Vinyl - LP` alone becoming `Vinyl Album`
            # would drop those rows and leave the rest to satisfy this check.
            raise RuntimeError(f"no product in the {_COLLECTION_SLUG} collection carries a vinyl product_type -- format-taxonomy drift")
        if vendor_ok == 0:
            raise RuntimeError(f"no vinyl product in the {_COLLECTION_SLUG} collection carries a vendor -- artist-source drift")
        if stock_flag_ok == 0:
            # The availability filter reads a field that can vanish, and a
            # vanished one is silently indistinguishable from "everything is
            # sold out": `variants` gone, or `available` gone from every
            # variant, makes each product yield nothing while every guard above
            # still passes, so the walk completes empty and deletes the
            # snapshot. This tally is what separates the two, and it counts
            # products rather than short-circuiting on the first, so an
            # isolated malformed product is skipped by _items without failing
            # the crawl.
            raise RuntimeError(f"no vinyl product in the {_COLLECTION_SLUG} collection carries a readable availability flag -- stock-source drift")

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        if not _VINYL_TYPE_RE.match((product.get("product_type") or "").strip()):
            return []
        artist = (product.get("vendor") or "").strip()
        if not artist:
            return []
        # The store writes the artist into `vendor` and keeps it out of `title`
        # on all but a handful of products, so the shared exact-case " - "
        # strip is a live transformation here rather than the drift guard it is
        # on the sibling stores ("Eagles - Live 2LP", "ZZ Top - From The Top:
        # 1971-1976 (Rhino High Fidelity) (5LP Boxed Set)").
        #
        # Its narrowness is doing real work: a colon after the vendor's name is
        # far more common and is almost never a separator -- "Talking Heads: 77",
        # "Nuggets: Original Artyfacts From the First Psychedelic Era",
        # "Fleetwood Mac: 1973-1974" are the album titles, and widening the
        # strip to a colon would reduce them to "77", "Original Artyfacts..."
        # and "1973-1974".
        title = strip_vendor_prefix(product.get("title", ""), artist)
        handle = product.get("handle", "")
        url = f"{cls.base_url}/products/{handle}"
        is_preorder = has_tag(product, _PREORDER_TAG)

        # Non-mapping entries are dropped *before* the count, because
        # len(variants) decides whether a per-variant descriptor is appended and
        # that descriptor is part of item_key. Leaving them in meant a single
        # -variant product acquiring one junk sibling silently re-titled its
        # healthy row from "Album" to "Album - 11", changing the identity that
        # listings, judgments and saves are keyed on and orphaning all of them
        # -- over an entry the filter then ignored anyway.
        #
        # Mapping entries stay in the count whatever their availability says,
        # including malformed and sold-out ones. The descriptor disambiguates
        # pressings, which is a structural property of the product; keying it on
        # stock state would make item_key change every time a variant sold out.
        variants = [v for v in (product.get("variants") or []) if isinstance(v, dict)]
        items = []
        for variant in variants:
            # `available` decides, not the store's own `out_of_stock` tag: the
            # two disagree on 13 of the 35 tagged products (tagged out of stock,
            # flagged available), so the tag is hand-curated and stale where the
            # flag is Shopify's live inventory state. Same reason the `exclude`
            # tag is ignored -- those products resolve 200, are published and
            # are purchasable, so it is a merchandising-feed flag rather than a
            # storefront one.
            #
            # No pre-order availability bypass, matching udiscovermusic.py and
            # hammerheart.py: every live pre-order here reports available=True,
            # so an unavailable product is gone allocation whether or not it is
            # tagged.
            # `is not True`, not falsiness. The catalog-wide guard above only
            # establishes that the field is readable *somewhere*, so on a mixed
            # payload it is satisfied by one healthy product while a sibling
            # carrying the string "false" sails through a truthiness test and
            # gets published as in stock -- a sold-out record offered for sale,
            # which is worse than losing the row. Demanding the literal True
            # here is what actually closes that, and it keeps the filter and the
            # guard reading the same field the same way. Anything else --
            # False, "false", 1, None, absent -- is skipped rather than guessed
            # at. Non-mapping entries are already gone, filtered out above.
            if variant.get("available") is not True:
                continue
            price = cls._price(variant)
            display_title = f"{title} (Pre-Order)" if is_preorder else title
            if len(variants) > 1:
                display_title = f"{display_title} — {cls._variant_descriptor(variant)}"
            items.append({
                "artist": artist,
                "title": display_title,
                "format": "Vinyl",
                "price": price,
                "currency": "USD",
                "url": url,
                "cover_image_url": resolve_cover_image(product, variant),
            })
        return items

    @staticmethod
    def _has_readable_stock_flag(product: dict) -> bool:
        # A literal bool, not merely a present key. Shopify sends JSON
        # true/false here, and the filter below reads the value's truthiness --
        # so `available` arriving as the string "false" would not just be drift,
        # it would invert the filter and publish every sold-out record as in
        # stock. Requiring the type refuses that payload instead of trusting it.
        # An int 1/0 would filter correctly and is refused anyway: the cost of
        # over-strictness is a raise, which leaves the previous snapshot intact.
        for variant in product.get("variants") or []:
            if isinstance(variant, dict) and isinstance(variant.get("available"), bool):
                return True
        return False

    @staticmethod
    def _price(variant: dict) -> Optional[float]:
        raw = variant.get("price")
        # bool before float(): bool is an int subclass, so True would price a
        # record at 1. nan is the other one a truthiness check cannot catch --
        # it is truthy, and would reach the stock row and break JSON
        # serialisation downstream.
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
    def _variant_descriptor(variant: dict) -> str:
        # Unreachable on the live catalog, which is single-variant throughout
        # (840/840 vinyl products). Without a per-variant descriptor those rows
        # would share (artist, title, url) and collapse onto one item_key --
        # the identity stock_item_identities, crawl_queue targets, listings,
        # judgments and saves all key on -- so colliding variants become
        # indistinguishable downstream. Variant id as fallback follows
        # udiscovermusic.py/hammerheart.py: immutable, unique, identity over
        # cosmetics.
        title = (variant.get("title") or "").strip()
        if title and title != "Default Title":
            return title
        variant_id = variant.get("id")
        if variant_id is None:
            raise RuntimeError("variant carries neither a usable title nor an id")
        return str(variant_id)
