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
        unreadable_stock = 0
        yielded = False
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            products_seen += 1
            if _VINYL_TYPE_RE.match((product.get("product_type") or "").strip()):
                vinyl_seen += 1
                # Nested, not a sibling: only a product with both the vinyl type
                # and a vendor is one that could have yielded a row at all, so
                # its readability is the only readability that means anything.
                # Tallied independently, a product with a vendor but no readable
                # flag and another with a readable flag but no vendor each
                # satisfy half the condition while neither can yield.
                if (product.get("vendor") or "").strip():
                    vendor_ok += 1
                    if not self._has_readable_stock_flag(product):
                        unreadable_stock += 1
            for item in self._items(product):
                yielded = True
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
        if not yielded and unreadable_stock:
            # This guard reads the walk's *outcome* rather than a field, and
            # states the invariant the field checks cannot express: an
            # empty result is only trustworthy when every product that could
            # have yielded a row was readable and simply out of stock.
            #
            # Availability is the field this crawler reads that has no innocent
            # empty reading -- `variants` gone, or `available` gone, renamed or
            # retyped, makes a product yield nothing in exactly the way a
            # sold-out one does, and the field-presence tallies above stay
            # non-zero throughout. Counting *unreadable* products instead of
            # readable ones is what catches the partial case that defeats the
            # obvious formulation: one genuinely sold-out product with a
            # readable False satisfies an "at least one readable" test on
            # behalf of a whole catalog that has gone unreadable behind it.
            #
            # Gated on having yielded nothing, deliberately. An isolated
            # unreadable product among rows that did come through is an
            # ordinary skipped row, and failing the whole crawl over it would
            # freeze the snapshot for a single bad product. It is only when the
            # result is empty -- the outcome that deletes the snapshot -- that
            # an unreadable product means the emptiness cannot be trusted.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while "
                f"{unreadable_stock} vinyl product(s) carry no readable availability flag -- stock-source drift")

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
        # A literal bool, not merely a present key, and matching the filter's
        # own `is True` exactly. "Readable" has to mean "this product's stock
        # state is something we can actually determine": a variant carrying the
        # string "false" is skipped by the filter, so counting it as readable
        # would let it vouch for an emptiness it is itself the cause of. An int
        # 1/0 is refused on the same terms -- the filter would skip it too, so
        # calling it readable would be a lie the guard then relies on.
        #
        # every(), not any(): one readable variant does not make the product
        # readable. A product whose first variant is a readable False and whose
        # second is malformed yields nothing, and under an any() test is counted
        # readable while doing so -- so it vouches for an emptiness that is half
        # its own doing, and the second variant's real stock state was never
        # determinable. The same quantifier mistake as counting readable
        # products instead of unreadable ones, one level down.
        #
        # Non-mapping entries are excluded rather than failing the check, to
        # stay consistent with _items(), which drops them before it counts or
        # iterates. A product left with no mapping variants at all is
        # unreadable, not vacuously readable: it yields nothing and there is
        # nothing in it to say why.
        variants = [v for v in (product.get("variants") or []) if isinstance(v, dict)]
        return bool(variants) and all(
            isinstance(v.get("available"), bool) for v in variants)

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
