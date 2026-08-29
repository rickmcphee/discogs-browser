import re
from typing import AsyncIterator, Optional

from shopify_catalog import iter_products, resolve_cover_image

# The store's whole catalog (`all`, 455,864 products) is two orders of
# magnitude past what one sync can walk; `vinyl-lps` (35,645) is the vinyl
# cut of it. The nearly-empty `12-singles` (2) and `7-singles-45s` (0)
# collections are not worth a second pass -- the format gate below already
# admits those product types wherever they appear in this collection.
#
# This collection is larger than Shopify's storefront products.json can
# enumerate: the endpoint refuses `page` past shopify_catalog._MAX_PAGE, so
# the walk keeps the first _MAX_PAGE * _PAGE_LIMIT products and stops, and
# which products fall outside is decided by the collection's own ordering.
# Accepted deliberately -- this store was briefly a release crawler to reach
# the whole catalog, and the owner reverted it: a browsable (if truncated)
# shelf in the Store tab is worth more here than complete per-release
# coverage. See the 2026-08-29 amendment to
# docs/specifications/shaping/2026-08-24-waterloo-records-crawler-design.md.
_COLLECTION_SLUG = "vinyl-lps"

# `product_type` is the store's own format field and the only trustworthy
# format signal here. The title's trailing bracket is NOT one: live values
# include "[Import]", "[Reissue]", "[Limited Edition]", "[Deluxe]" and
# "[Magenta/Black/White Haze/Splatter]", none of which name a format, while
# CDs carry "[Digipak]" and "[Standard Edition CD]". Compared lowercased
# because the store's own casing is inconsistent ("Vinyl" vs "7-IN VINYL").
_VINYL_PRODUCT_TYPES = frozenset({
    "vinyl", "7-in vinyl", "10-in vinyl", "12-in single",
})

# "Artist - Album [Format]", split on the FIRST spaced hyphen: album halves
# legitimately contain further " - " runs ("10CC - Deceptive Bends - 180gm
# Vinyl [LP]" is Deceptive Bends, not Deceptive Bends by 10CC - Deceptive
# Bends). Requires whitespace on both sides so a hyphenated artist or album
# ("Chik-Chik") is never split mid-word. `vendor` is deliberately not used
# as a fallback: it is a numeric supplier code here ("503", "598", "206"),
# not a label or an artist.
_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s+-\s+(?P<album>.+)$')


class Crawler:
    site_name: str = "Waterloo Records"
    base_url: str = "https://waterloorecords.com"
    genre_summary: str = "Austin, Texas independent record store since 1982, with a deep new-vinyl catalog spanning every genre and a strong Texas-music selection."
    genre: str = "marketplace"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            item = self._item(product)
            if item is not None:
                yield item

    @classmethod
    def _item(cls, product: dict) -> Optional[dict]:
        if (product.get("product_type") or "").strip().lower() not in _VINYL_PRODUCT_TYPES:
            return None

        m = _TITLE_RE.match((product.get("title") or "").strip())
        if not m:
            return None
        artist = m.group("artist").strip()
        album = m.group("album").strip()
        if not artist or not album:
            return None

        variant = cls._pick_variant(product)
        if variant is None:
            return None

        return {
            "artist": artist,
            # The trailing bracket stays in the title. It is what separates
            # two pressings of one album ("Let It Be Blue [LP]" vs "Let It Be
            # Blue [Indie Exclusive Limited Edition Blue LP]"), which share a
            # handle-derived URL prefix but are distinct products, and it
            # still matches the catalog: _library_match_fragment matches a
            # stock title exactly OR as a prefix followed by a space, so
            # "Kid A [LP]" matches catalog "Kid A".
            "title": album,
            # "Vinyl" unconditionally, as every sibling catalog crawler does.
            # The specific cut (7", 10", 12") is already carried in the
            # title's own bracket, so nothing is lost by not encoding it here.
            "format": "Vinyl",
            "price": cls._price(variant),
            "currency": "USD",
            "url": f"{cls.base_url}/products/{product.get('handle', '')}",
            # featured_image is null on every variant this store publishes,
            # so this always resolves to the product image -- called anyway
            # to stay correct if the store ever populates it.
            "cover_image_url": resolve_cover_image(product, variant),
        }

    @classmethod
    def _pick_variant(cls, product: dict) -> Optional[dict]:
        """The cheapest in-stock variant, or None when nothing is in stock.

        One row per *product*, never per variant. `db.compute_item_key` hashes
        (artist, title, url), and every variant of a product shares all three
        -- so a per-variant fan-out would emit rows that collide on item_key,
        which `replace_stock_items` INSERTs without an ON CONFLICT guard.

        The variants are conditions and placeholders ("New", "New / Default",
        "Default / New"), not editions, so unlike the sibling label crawlers
        there is no descriptor worth appending to the title to tell them
        apart. Cheapest wins because `stock_items` has no condition column:
        a used copy cannot be labelled as one, so the row reports the least
        it costs to get the record. That is this store's own policy, not a
        fleet convention -- amoeba.py, the only other crawler that sees used
        stock, prefers the new price and falls back to used only when no new
        price parses, rather than comparing the two.
        """
        available = [v for v in product.get("variants") or [] if v.get("available")]
        if not available:
            return None
        priced = [v for v in available if cls._price(v) is not None]
        # No parseable price anywhere: still emit the row (price None), same
        # as every sibling, rather than dropping in-stock vinyl over a bad field.
        if not priced:
            return available[0]
        return min(priced, key=cls._price)

    @staticmethod
    def _price(variant: dict) -> Optional[float]:
        try:
            return float(variant["price"])
        except (KeyError, TypeError, ValueError):
            return None
