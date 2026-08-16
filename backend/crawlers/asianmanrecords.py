import re
from typing import AsyncIterator, Optional
from shopify_catalog import iter_products, has_tag, resolve_cover_image

_COLLECTION_SLUG = "all-products"
_VINYL_TYPES = {"12-INCH VINYL", "7-INCH VINYL"}

# Prefix noise seen live: "PRE ORDER: AJJ ..." and "AMR DISTRO: SKANKIN' PICKLE
# ...", sometimes both together with a doubled-colon typo
# ("PRE ORDER: AMR DISTRO:: AJJ ..."). Distro items -- another label's release
# resold through Asian Man's own store -- stay in scope; only this prefix noise
# is stripped before parsing.
_PREFIX_RE = re.compile(r'^(?:PRE ORDER:\s*)?(?:AMR DISTRO:+\s*)?', re.IGNORECASE)
# Primary title convention on this store: ARTIST "Album" FORMAT, with or
# without a hyphen (glued or spaced) before the opening quote.
_QUOTE_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s*[-–—]?\s*"(?P<album>[^"]+)"')
# Fallback for the minority of titles with no quoted album at all -- reuses
# cleorecs.py's hyphen/en-dash/em-dash split.
_HYPHEN_TITLE_RE = re.compile(r'^(?P<artist>.+?)(?:\s+[-–—]\s*|\s*[-–—]\s+)(?P<album>.+)$')
# Strips a trailing format marker off the hyphen-fallback album half -- only
# the digit+quote/digit+INCH forms confirmed live on this store's no-quote
# titles (e.g. "CHUMPS ON PARADE 12\" VINYL" -> "CHUMPS ON PARADE").
_FORMAT_SUFFIX_RE = re.compile(r'\s+(?:DOUBLE\s+)?\d{1,2}\s*(?:"|INCH\b).*$', re.IGNORECASE)
# Non-vinyl alternate purchases this store bundles as sibling Shopify variants
# of the vinyl product itself (CD, cassette, promo slipmat) -- excluded only
# when a product has more than one variant. A single-variant product's sole
# variant is often just "Default Title" or a bare color word with no such
# word to match, so applying this unconditionally would wrongly drop it.
_EXCLUDED_VARIANT_RE = re.compile(r'\bCD\b|\bCS\b|\bCASSETTE\b|\bSLIPMAT\b', re.IGNORECASE)
# Apparel-bundle sizing (vinyl + T-shirt, varying only by shirt size) --
# collapsed to the cheapest variant instead of one near-duplicate stock item
# per size.
_BUNDLE_DEAL_RE = re.compile(r'BUNDLE DEAL', re.IGNORECASE)


class Crawler:
    site_name: str = "Asian Man Records"
    base_url: str = "https://asianmanrecords.com"
    genre_summary: str = "Mike Park's Bay Area punk/ska label store, selling its own catalog plus a small distro of other labels' releases."
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            if not self._is_vinyl(product):
                continue
            for item in self._items(product):
                yield item

    @classmethod
    def _is_vinyl(cls, product: dict) -> bool:
        if product.get("product_type") in _VINYL_TYPES:
            return True
        return any(has_tag(product, t) for t in _VINYL_TYPES)

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        artist, album, is_preorder = cls._parse_title(product.get("title", ""))
        if artist is None:
            return []

        variants = product.get("variants") or []
        if len(variants) > 1:
            survivors = [v for v in variants if not _EXCLUDED_VARIANT_RE.search(v.get("title", ""))]
        else:
            survivors = list(variants)
        # Bundle-ness and the multi-edition suffix decision are both derived from
        # `survivors`, not from post-availability `kept` -- a stock item's title
        # (and therefore its item_key, backend/db.py's replace_stock_items) must
        # stay stable as individual variants sell in and out, or a durable
        # stock_item_judgments row silently orphans every time availability shifts.
        is_bundle = bool(survivors) and all(_BUNDLE_DEAL_RE.search(v.get("title", "")) for v in survivors)
        multi_edition = len(survivors) > 1 and not is_bundle

        kept = [v for v in survivors if v.get("available") or is_preorder]
        if is_bundle and kept:
            kept = [min(kept, key=cls._variant_price_sort_key)]

        handle = product.get("handle", "")
        items = []
        for variant in kept:
            title = f"{album} — {variant.get('title', '')}" if multi_edition else album
            items.append({
                "artist": artist,
                "title": title,
                "format": "Vinyl",
                "price": cls._price(variant),
                "currency": "USD",
                "url": f"{cls.base_url}/products/{handle}",
                "cover_image_url": resolve_cover_image(product, variant),
            })
        return items

    @staticmethod
    def _price(variant: dict) -> Optional[float]:
        try:
            return float(variant["price"])
        except (KeyError, TypeError, ValueError):
            return None

    @classmethod
    def _variant_price_sort_key(cls, variant: dict) -> float:
        price = cls._price(variant)
        return price if price is not None else float("inf")

    @classmethod
    def _parse_title(cls, raw_title: str):
        is_preorder = raw_title.strip().upper().startswith("PRE ORDER")
        stripped = _PREFIX_RE.sub('', raw_title).strip()

        m = _QUOTE_TITLE_RE.match(stripped)
        if m:
            return m.group("artist").strip(), m.group("album").strip(), is_preorder

        m = _HYPHEN_TITLE_RE.match(stripped)
        if m:
            album = _FORMAT_SUFFIX_RE.sub('', m.group("album")).strip()
            if album:
                return m.group("artist").strip(), album, is_preorder

        return None, None, is_preorder
