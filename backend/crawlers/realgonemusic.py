import re
from typing import AsyncIterator
from shopify_catalog import iter_products, resolve_cover_image

_COLLECTION_SLUG = "vinyl"
# 49 of this collection's variants are multi-item packs priced at 2-6x a
# single LP (the dearest available one is "Kinky Boots Bundle" at $174.99
# against a ~$25-30 single LP). Surfacing one as a listing for a single
# release would inflate the price column and duplicate the product.
# Confirmed live: 3 products have a bundle as their only in-stock variant
# and so drop out of the catalog entirely -- accepted, since keeping them
# would mean carrying a bundle price against a single release.
_BUNDLE_RE = re.compile(r'\bbundle\b', re.IGNORECASE)
# Shopify's placeholder for a product with no real options. Two spellings
# are live here, not one -- 6 products use "Default Title" and 4 use bare
# "Default" -- so the sibling crawlers' `== "Default Title"` check would
# miss 4 of them.
_DEFAULT_VARIANT_TITLES = {"default", "default title"}


class Crawler:
    site_name: str = "Real Gone Music"
    base_url: str = "https://realgonemusic.com"
    genre_summary: str = "Los Angeles reissue label — Black Jazz jazz reissues, '90s alt-rock, death metal, and film soundtracks."
    genre: str = "marketplace"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        # There is no artist source on this site. `vendor` is the literal
        # "Real Gone Music" on all 278 vinyl products, and titles
        # concatenate artist and album with no delimiter at all ("Deicide
        # Serpents of the Light (Remastered) Vinyl"). Confirmed live that
        # products.json, /products/{handle}.js, the page's JSON-LD, and
        # its meta tags all carry either the label or the same undelimited
        # title. Used directly as a known, accepted gap -- the same call
        # numerogroup.py makes, for the same reason. Consequence:
        # db.py's _library_match_fragment requires exact artist equality,
        # so no row from this store will ever match a user's collection or
        # wantlist. Splitting the title instead was considered and
        # rejected three ways; see the design spec before changing this.
        artist = (product.get("vendor") or "").strip()
        product_title = (product.get("title") or "").strip()
        handle = product.get("handle", "")
        url = f"{cls.base_url}/products/{handle}"

        # No format filter, deliberately -- neither direction earns its
        # place here. Confirmed live across all 279 distinct variant
        # titles: a positive vinyl regex (the sibling convention) matches
        # only 77 of them ("Black Vinyl", "Wax Mage Vinyl") and would
        # discard the other 202, which are bare colour/edition names
        # carrying no format token at all ("Wax Mage", "Hellfire",
        # "Blue-Green 'Ocean Spray'") -- 72% of real stock. A negative
        # non-vinyl regex has nothing to match: zero titles name CD,
        # cassette, tape, digital, or DVD, because the `vinyl` collection
        # tag already gates format at the product level.
        items = []
        for variant in product.get("variants") or []:
            if not variant.get("available"):
                continue
            variant_title = (variant.get("title") or "").strip()
            if _BUNDLE_RE.search(variant_title):
                continue
            try:
                price = float(variant["price"])
            except (KeyError, TypeError, ValueError):
                price = None
            items.append({
                "artist": artist,
                "title": cls._compose_title(product_title, variant_title),
                "format": "Vinyl",
                "price": price,
                "currency": "USD",
                "url": url,
                "cover_image_url": resolve_cover_image(product, variant),
            })
        return items

    @staticmethod
    def _compose_title(product_title: str, variant_title: str) -> str:
        # The equality arm is defensive, not live: "Buckcherry 15 (2-LP
        # Set)" is the one product whose variant title repeats its product
        # title, and that variant is currently sold out, so this branch
        # emits nothing today. Kept because it costs one comparison and
        # the product can restock at any time.
        if (
            not variant_title
            or variant_title.lower() in _DEFAULT_VARIANT_TITLES
            or variant_title == product_title
        ):
            return product_title
        return f"{product_title} — {variant_title}"
