import re
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, strip_vendor_prefix, resolve_cover_image

_PREORDER_TAG = "pre-order"
_COLLECTION_SLUG = "hard-rock-heavy-metal"
# The collection is the store's genre shelf, not a format one -- CDs are its
# largest product_type, alongside DVD/Blu-Ray, box sets, cassettes, and
# apparel -- so this gate does the vinyl scoping the sibling stores get from
# a `vinyl` collection slug. product_type is the store's own format field
# and is clean and specific here (confirmed live 2026-09-01: vinyl is
# exactly the 1LP/2LP/3LP/4LP/7in types, every one a real record, and no
# vinyl-typed title names a non-vinyl format). Box sets are typed
# "Box Set (…)" whatever media they hold, so the vinyl ones stay excluded --
# accepted scope loss, see the design spec's Non-goals.
_VINYL_TYPE_RE = re.compile(r"^(?:\d*lp|\d+in)$", re.IGNORECASE)


class Crawler:
    site_name: str = "uDiscover Music"
    base_url: str = "https://shop.udiscovermusic.com"
    genre_summary: str = "Universal Music's official store, crawled for its hard rock and heavy metal collection."
    genre: str = "rock"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        products_seen = 0
        vendor_ok = 0
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            products_seen += 1
            if (product.get("vendor") or "").strip():
                vendor_ok += 1
            for item in self._items(product):
                yield item
        # db.replace_stock_items() DELETEs this crawler's previous snapshot
        # before inserting, and _sync_stock only skips that call when the
        # crawl raised -- so a renamed/removed collection or a store that
        # stopped writing artists into `vendor` must raise here, not complete
        # empty and wipe the snapshot. Sold-out, format-gated, and
        # blank-vendor products still count toward both tallies, so a
        # legitimately quiet catalog doesn't trip either guard. A store-side
        # product_type renaming that starved the gate would slip through the
        # vendor guard -- accepted deliberately, because the collection is
        # genuinely mixed-format and an all-CD run is indistinguishable from
        # that drift.
        if products_seen == 0:
            raise RuntimeError(f"{_COLLECTION_SLUG} collection returned no products -- renamed, removed, or markup drift")
        if vendor_ok == 0:
            raise RuntimeError(f"no product in {_COLLECTION_SLUG} carries a vendor -- artist-source drift")

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        if not _VINYL_TYPE_RE.match((product.get("product_type") or "").strip()):
            return []
        artist = (product.get("vendor") or "").strip()
        if not artist:
            return []
        # Zero live titles carry a "{vendor} - " prefix, and the ones that
        # start with the vendor's name are self-titled albums the exact-case
        # separator match correctly leaves alone ("She Wants Revenge 2LP",
        # "KISS Destroys Anaheim '76 2LP") -- this is a drift guard, not a
        # live transformation. The trailing pressing descriptor ("2LP",
        # "(LP)") stays: it separates two pressings of one album, and
        # _library_match_fragment's exact-or-prefix-with-space match still
        # finds the bare album title in front of it.
        title = strip_vendor_prefix(product.get("title", ""), artist)
        handle = product.get("handle", "")
        url = f"{cls.base_url}/products/{handle}"
        is_preorder = has_tag(product, _PREORDER_TAG)

        variants = product.get("variants") or []
        items = []
        for variant in variants:
            # No pre-order availability bypass, deliberately departing from
            # napalmrecords.py/centurymedia.py: this store flags purchasable
            # pre-orders available (confirmed live: all 13 pre-order-tagged
            # vinyl products report available=True), so an unavailable
            # product -- pre-order or not -- is gone allocation. Same call
            # as hammerheart.py and darksiderecords.py on their stores.
            if not variant.get("available"):
                continue
            try:
                price = float(variant["price"])
            except (KeyError, TypeError, ValueError):
                price = None
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
    def _variant_descriptor(variant: dict) -> str:
        # Only reached on a multi-variant product, which the live vinyl
        # catalog doesn't have (303/303 single-variant; the collection's
        # multi-variant products are T-shirt sizes the format gate never
        # admits) -- without a per-variant descriptor those rows would share
        # (artist, title, url) and collapse onto one item_key. The sync
        # accepts that (stock_items.item_key is deliberately non-unique),
        # which is exactly the problem: item_key is the identity everything
        # downstream keys on -- stock_item_identities, crawl_queue targets,
        # listings, judgments, saves -- so colliding variants become
        # indistinguishable there. Variant id as fallback follows
        # hammerheart.py/darksiderecords.py: immutable, unique, identity
        # over cosmetics.
        title = (variant.get("title") or "").strip()
        if title and title != "Default Title":
            return title
        variant_id = variant.get("id")
        if variant_id is None:
            raise RuntimeError("variant carries neither a usable title nor an id")
        return str(variant_id)
