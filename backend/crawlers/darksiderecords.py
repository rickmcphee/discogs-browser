import re
from typing import AsyncIterator
from shopify_catalog import iter_products, resolve_cover_image

# Not the store's headline `new-vinyl` collection (58,416 products by its own
# products_count), because that one cannot be crawled at all: Shopify's
# products.json serves at most 100 pages, and confirmed live, page 100 returns
# 250 products while page 101 returns a hard HTTP 400. That caps any collection
# at 25,000 products, leaving `new-vinyl` unreachable by ~33k -- and since a 400
# is not a 429, iter_products() would spend its whole consecutive_failure_limit
# budget retrying it before raising.
#
# `new-vinyl-in-stock` is both crawlable (5,141 products, 21 pages) and the
# better semantic: every one of its products carries the `instore-available`
# tag, against ~24% of `new-vinyl` (sampled over 1,500 products spanning
# pages 1-100), so it is what is physically on the shelf in Poughkeepsie
# rather than what the store can order from a distributor.
_COLLECTION_SLUG = "new-vinyl-in-stock"

# All 5,141 products are typed "New Vinyl/<genre>" across 44 distinct subtypes,
# so this gate is a no-op today. It earns its place because the store demonstrably
# sells non-vinyl -- books, CDs, board games, plush -- just not in this
# collection; the gate is what keeps a misfiled one out.
_VINYL_TYPE_PREFIX = "new vinyl"

# Hyphen/en-dash/em-dash split with asymmetric spacing, from jackpotrecords.py.
# The dominant live form is "Artist- Album", the hyphen glued to the artist with
# a space only after it; 4 products use an en-dash the same way. The non-greedy
# artist group is what keeps the 316 products with a further spaced hyphen inside
# the album ("The Specials- Live From The Cathedral - Black Vinyl [Import]") from
# splitting at the wrong one, and what makes hyphenated artist names parse
# correctly -- confirmed live on all 45 of them, including "Blink-182",
# "Run-Dmc", "Jean-Luc Ponty" and "Olivia Newton-John", since neither alternative
# matches a dash with no adjacent whitespace.
#
# No vendor fallback: vendor here is the distributor ("THE ORCHARD", "UMG",
# "WMX", "AEC"), never the artist. A product whose title has no separator at all
# is dropped rather than guessed at -- 178 of 5,141 live, all of them genuinely
# artist-less soundtracks and compilations ("Hocus Pocus (Original Motion Picture
# Soundtrack) [Blue Jay 2LP Vinyl]").
_TITLE_RE = re.compile(r'^(?P<artist>.+?)(?:\s+[-–—]\s*|\s*[-–—]\s+)(?P<album>.+)$')

# Shopify's placeholders for a product with no real options. Every live
# product uses "Default Title"; the bare "Default" spelling is carried
# because realgonemusic.py found both in the wild on one store.
_DEFAULT_VARIANT_TITLES = {"default", "default title"}


class Crawler:
    site_name: str = "Darkside Records"
    base_url: str = "https://shop.darksiderecords.com"
    genre_summary: str = "Poughkeepsie, New York independent record store — new vinyl across every genre, from rock and metal to jazz, hip hop and soundtracks."
    genre: str = "marketplace"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            if not self._is_vinyl(product):
                continue
            for item in self._items(product):
                yield item

    @classmethod
    def _is_vinyl(cls, product: dict) -> bool:
        return (product.get("product_type") or "").strip().lower().startswith(_VINYL_TYPE_PREFIX)

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        m = _TITLE_RE.match(product.get("title", ""))
        if not m:
            return []
        artist = m.group("artist").strip()
        album = m.group("album").strip()

        handle = product.get("handle", "")
        url = f"{cls.base_url}/products/{handle}"

        # No pre-order carve-out, unlike the sibling crawlers that bypass this
        # gate for pre-order-tagged products -- because there are no current
        # pre-orders here to carve out. Both tag forms (`preorder_bt` 874,
        # `pre-order vinyl` 271) are stale residue on already-released shelf
        # stock: every tagged product's release date is in the past, the two
        # never co-occur, and everything in this collection is tagged
        # `instore-available`. See the design spec's "Pre-orders" section.
        # Only 3 of 5,141 products have no available variant at all.
        variants = product.get("variants") or []
        # Gated on the total variant count, not the available one, so a row's
        # identity is stable: db.compute_item_key() hashes exactly
        # (artist, title, url), so if the descriptor appeared only while a
        # sibling happened to be in stock, a variant's item_key would change
        # the moment that sibling sold out, orphaning its listings and
        # saved-item rows.
        is_multi_variant = len(variants) > 1

        items = []
        for variant in variants:
            if not variant.get("available"):
                continue

            try:
                price = float(variant["price"])
            except (KeyError, TypeError, ValueError):
                price = None

            items.append({
                "artist": artist,
                # Kept verbatim, including the trailing "(Vinyl)" that 68% of
                # titles carry and the "(DAMAGED)" marker on the 173 emitted
                # discounted sleeve-damaged copies. Neither is stripped:
                # _library_match_fragment matches a stock title against the
                # catalog on exact-or-prefix-with-space, so "Awake (Clear
                # Vinyl)" still matches catalog "Awake", and the damage marker
                # is what makes a below-market price self-explanatory in the
                # price column.
                "title": cls._compose_title(album, variant, is_multi_variant, url),
                "format": "Vinyl",
                "price": price,
                "currency": "USD",
                "url": url,
                "cover_image_url": resolve_cover_image(product, variant),
            })
        return items

    @staticmethod
    def _compose_title(album: str, variant: dict, is_multi_variant: bool, url: str) -> str:
        # Single-variant products -- all 5,141 of them live -- keep the bare
        # album title, since their only variant is the "Default Title"
        # placeholder and carries nothing. A multi-variant product appends its
        # variant descriptor instead, because db.compute_item_key() hashes
        # (artist, title, url) and this crawler's url is per-product: without
        # the descriptor two available variants would collapse onto one
        # item_key, so the stock sync would enqueue a single marketplace
        # lookup and Store would show one row for what are two different
        # pressings at potentially different prices.
        if not is_multi_variant:
            return album
        descriptor = (variant.get("title") or "").strip()
        if not descriptor or descriptor.lower() in _DEFAULT_VARIANT_TITLES:
            # Shopify only issues "Default Title" for a product with exactly
            # one variant, so a multi-variant product reaching here is
            # malformed data. Fall back to the variant id -- immutable, and
            # unique per variant -- rather than the bare album, which would
            # put every such row back onto one item_key and reintroduce the
            # collision this helper exists to prevent. A raw id reads poorly
            # in Store, but identity correctness beats cosmetics in a shape
            # the store cannot currently produce.
            descriptor = str(variant.get("id") or "").strip()
            if not descriptor:
                # Nothing stable left to disambiguate on. Returning the bare
                # album would emit rows sharing one item_key, and
                # replace_stock_items() DELETEs this crawler's rows before
                # inserting, so a corrupt snapshot would replace a good one.
                # Raising instead leaves the previous snapshot untouched:
                # _sync_stock catches it, records the site as failed and
                # `continue`s past replace_stock_items entirely.
                raise ValueError(
                    f"{url}: multi-variant product whose variants have neither a "
                    "title nor an id -- cannot derive distinct item keys"
                )
        return f"{album} — {descriptor}"
