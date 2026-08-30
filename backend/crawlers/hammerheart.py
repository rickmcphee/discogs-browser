import re
from typing import AsyncIterator, Optional
from shopify_catalog import iter_products, has_tag, resolve_cover_image

_PREORDER_TAG = "preorder"
_COLLECTION_SLUG = "vinyl"
# Two CDs sit in the vinyl collection mistyped as 12" (confirmed live:
# "ARTCH - Another Return / CD", "MONOLITHE - Black Hole District / Digipak
# CD"), so the collection's own typing can't be trusted alone. The counted
# \d*x? allowance follows spv.py/onetwothreefourgo.py: a disc count binds to
# its format word with no word boundary between them, so a bare \bcds?\b
# cannot see the CD in "2xCD".
_NON_VINYL_RE = re.compile(r"\b\d*x?(?:cds?|dvds?)\b|\bdigipak\b|\bcassettes?\b|\btapes?\b", re.IGNORECASE)
# The override that keeps a genuine hybrid (e.g. an LP + CD bundle) when a
# non-vinyl word is present. No live title carries both signals today, and
# titles with neither (e.g. "Sagovindars Boning", "Gloom Immemorial (Gold
# viny)" [sic]) never reach the override -- the filter only fires on a
# non-vinyl match.
_VINYL_RE = re.compile(r'\bvinyl\b|\b\d*x?lps?\b|\d+\s*(?:"|inch)|\bpicture dis[ck]\b', re.IGNORECASE)


class Crawler:
    site_name: str = "Hammerheart Records"
    base_url: str = "https://hammerheart.indiemerch.com"
    genre_summary: str = "Dutch label for death, doom, black, and Viking/folk metal."
    genre: str = "metal"
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
        # empty and wipe the snapshot. Sold-out and format-filtered products
        # still count toward both tallies, so a legitimately quiet catalog
        # doesn't trip either guard.
        if products_seen == 0:
            raise RuntimeError(f"{_COLLECTION_SLUG} collection returned no products -- renamed, removed, or markup drift")
        if vendor_ok == 0:
            raise RuntimeError(f"no product in {_COLLECTION_SLUG} carries a vendor -- artist-source drift")

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        artist = (product.get("vendor") or "").strip()
        if not artist:
            return []
        raw_title = product.get("title", "")
        if _NON_VINYL_RE.search(raw_title) and not _VINYL_RE.search(raw_title):
            return []
        title = cls._strip_artist_prefix(raw_title, artist)
        handle = product.get("handle", "")
        url = f"{cls.base_url}/products/{handle}"
        is_preorder = has_tag(product, _PREORDER_TAG)

        variants = product.get("variants") or []
        items = []
        for variant in variants:
            # No pre-order availability bypass, deliberately departing from
            # napalmrecords.py/centurymedia.py: this store flags purchasable
            # pre-orders available (confirmed live: 23/24 pre-order-tagged
            # products report available=True), and the one unavailable
            # pre-order renders "Sold Out" on its own page -- allocation
            # gone, not not-yet-released. Same call as darksiderecords.py
            # and onetwothreefourgo.py on their stores.
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
    def _strip_artist_prefix(title: str, artist: str) -> str:
        # The store leads most titles with the artist, but rarely in the
        # vendor's own casing -- "TROUBLE - Psalm 9 / Black Vinyl LP" against
        # vendor "Trouble" -- so shopify_catalog.strip_vendor_prefix's
        # exact-case match misses the bulk of them (confirmed live: 393/447
        # titles strip case-insensitively, 98 exactly). The separator varies
        # too: " - " usually, a tab before the dash on two products, " / " on
        # one. Requiring whitespace after the separator keeps a self-titled
        # album with no separator ("Abramelin (Black vinyl)") intact.
        m = re.match(rf"^{re.escape(artist)}\s*[-/]\s+", title, re.IGNORECASE)
        if m:
            return title[m.end():]
        return title

    @staticmethod
    def _variant_descriptor(variant: dict) -> str:
        # Only reached on a multi-variant product, which the live catalog
        # doesn't have (447/447 single-variant) -- without a per-variant
        # descriptor those rows would share (artist, title, url), collapse
        # onto one item_key, and fail the sync in replace_stock_items(),
        # which INSERTs with no ON CONFLICT guard. Variant id as fallback
        # follows darksiderecords.py: immutable, unique, identity over
        # cosmetics.
        title = (variant.get("title") or "").strip()
        if title and title != "Default Title":
            return title
        variant_id = variant.get("id")
        if variant_id is None:
            raise RuntimeError("variant carries neither a usable title nor an id")
        return str(variant_id)
