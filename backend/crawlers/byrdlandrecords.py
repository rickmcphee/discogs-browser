import math
import re
from typing import AsyncIterator, Optional

import httpx

from catalog_http import get_with_retry
from config import load_config
from crawl_progress import report_page

# The whole vinyl catalog hangs off this one category. Confirmed live: every
# id in the `vinyl/rock/indie-rock` subcategory (1,152 products, the largest
# leaf) is also in `/vinyl/`, so the genre tree below it needs no separate
# walk -- the parent listing is a strict superset.
_CATEGORY_PATH = "vinyl"

# The largest `limit` the storefront honours. Anything above it is not
# clamped to 100 but silently ignored, falling back to the default 12
# (confirmed live: limit=250 and limit=500 both answer `"limit": 12`), which
# would quietly turn one paced pass into eight.
_PER_PAGE = 100

# Pagination is path-based, and the `?page=` querystring is *silently
# ignored* -- `?limit=100&page=3` answers page 1 with `"page": 1`, HTTP 200,
# no error. A querystring pager would therefore re-yield page 1 for the whole
# walk and look perfectly healthy doing it. `_page_path` is the only correct
# form; `_assert_page_echo` is what stops a regression back to the broken one
# from being invisible.
def _page_path(page: int) -> str:
    return f"/{_CATEGORY_PATH}/page{page}.html"


# Titles carry raw tabs and multi-space runs from the store's spreadsheet
# imports, both around the artist/album separator (`Polvo\t- In Prism`) and
# inside the artist itself (`Chuck\tProphet - Wake The Dead`). The separator
# regex tolerates the first on its own -- `\s` already matches a tab -- but
# not the second, which would embed a literal tab in the artist. Collapsing
# whitespace first fixes both.
_WS_RE = re.compile(r"\s+")

# Hyphen/en-dash/em-dash with whitespace on at least one side, the wider
# `[-–—]` class cleorecs.py and jackpotrecords.py use. Requiring whitespace on
# one side is what keeps a hyphen *inside* a name from being read as the
# separator, and this store needs that guard more than most: it stocks
# `Now That's What I Call K-Pop`, `Country Funk Vol. 3 1975-1982` and
# `The Meters "Look-Ka Py Py" LP`, none of which name an artist before the
# hyphen. There is deliberately no unspaced-hyphen fallback -- it would parse
# a genuine `Maruja-Pain To Power` but mangle those three, and this fleet
# skips what it cannot split rather than guessing.
_TITLE_RE = re.compile(r"^(?P<artist>.+?)(?:\s+[-–—]\s*|\s*[-–—]\s+)(?P<album>.+)$")

# The store files a small run of CDs and cassettes under the Vinyl category,
# most of them flagged in the title (`... CD NOT VINYL`, `(Cassette)`).
# The counted forms (`\d*[x×]?`) are spv.py/onetwothreefourgo.py's regression:
# a bare `\bcds?\b` cannot see the CD in `2xCD`.
#
# `tapes?` is deliberately absent where those crawlers include it. They test a
# separate format descriptor; this store fuses the format into the title, so
# the pattern is read against the album name too, and `Felbm - Tape 1/Tape 2`
# is a live vinyl LP that a `tapes?` alternative would drop. Every live
# cassette here says "cassette" somewhere, so nothing is lost. For the same
# reason there is no `magazine`/`book` alternative: `Peel Dream Magazine` is
# an artist and `Book of Paul` an album.
_NON_VINYL_RE = re.compile(
    r"\b(?:\d*[x×]?\s?cds?|\d*[x×]?\s?dvds?|blu-?rays?|cassettes?)\b", re.IGNORECASE
)
# Vinyl vocabulary that overrides the filter above, for a record bundled with
# a disc (`(Splatter Vinyl LP+DVD)`).
_VINYL_WORD_RE = re.compile(
    r'\bvinyl\b|\b\d*[x×]?\s?lps?\b|\bflexi\b|\d+\s*"', re.IGNORECASE
)
# The store marks its mis-filed CDs by *negating* the category -- `CD NOT
# VINYL`, `(CD not Vinyl)`, `***CD (not vinyl)`. That phrase contains the
# override's strongest keyword, so a plain override reads the annotation as
# proof of the opposite and republishes the CD as a record; 16 live products
# leak through without this. Deleting the negated phrase before the override
# is tested is what keeps "vinyl" from voting for itself.
_NOT_VINYL_RE = re.compile(r"\bnot\s+vinyl\b", re.IGNORECASE)

_IMAGE_CDN = "https://cdn.shoplightspeed.com/shops/{shop_id}/files/{image_id}/{slug}.jpg"


class Crawler:
    site_name: str = "Byrdland Records"
    base_url: str = "https://shop.byrdlandrecords.com"
    genre_summary: str = (
        "Washington, D.C. record store and label — new and pre-owned vinyl "
        "across genres, with deep indie rock, jazz, soul and local D.C. sections."
    )
    genre: str = "marketplace"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        """Walk the store's Vinyl category, yielding one stock item per product.

        db.replace_stock_items() DELETEs this source's rows before inserting
        and _sync_stock only skips it when the crawl *raised*, so a generator
        that completes with nothing to show wipes the store's whole snapshot
        and records the site as healthy. Both drift guards below therefore
        raise rather than return empty.
        """
        cfg = load_config()
        delay = float(cfg.get("crawl_delay_seconds", 30))
        failure_limit = int(cfg.get("consecutive_failure_limit", 10))

        seen_ids = set()
        total_yielded = 0

        async with httpx.AsyncClient(base_url=self.base_url, follow_redirects=True) as client:
            page = 1
            total_pages = 1
            first_count = None
            while page <= total_pages:
                r = await get_with_retry(
                    client, _page_path(page),
                    params={"format": "json", "limit": _PER_PAGE},
                    delay=delay, failure_limit=failure_limit,
                )
                payload = r.json()
                collection = payload.get("collection") or {}
                products = collection.get("products") or {}

                self._assert_page_echo(collection, page)

                # Every page inside the reported range, not just the first.
                # The store derives `pages` from `count`, so a page within it
                # always has rows; one that comes back empty is drift, and
                # letting it pass would complete the walk successfully having
                # silently dropped that page's stock.
                if not products:
                    raise RuntimeError(
                        f"no products on {_page_path(page)} -- the Vinyl category is empty "
                        "or the JSON shape drifted"
                    )
                # Offset pagination over a collection the store keeps
                # selling from. A product removed from a page this walk has
                # ALREADY passed shifts every later product back one offset,
                # so the first row of the next page slides onto the previous
                # one and is never fetched -- and because the crawl then
                # succeeds, replace_stock_items() deletes that still-in-stock
                # row. Dedupe cannot see it: an insertion shows up as a
                # duplicate, but a deletion shows up as nothing at all.
                #
                # Only a *shrink* does this, and the asymmetry is what makes a
                # cheap guard sufficient. An insertion shifts rows forward, so
                # the next page re-serves one already yielded (deduped) and
                # skips nothing; the only row it can cost is the new arrival
                # itself, which no snapshot held yet and the next run picks up.
                # A removal ahead of the cursor is likewise harmless. So a
                # falling `count` is the one signal worth aborting on.
                count = self._item_count(collection, page)
                if first_count is None:
                    first_count = count
                elif count < first_count:
                    raise RuntimeError(
                        f"catalog shrank from {first_count} to {count} items during the walk "
                        f"(at {_page_path(page)}) -- offset pagination would silently skip rows, "
                        "so the previous snapshot is kept instead"
                    )

                # Safe as a maximum precisely because a shrink aborts above:
                # `pages` is ceil(count / limit), so it cannot fall on any walk
                # that survives, and the two readings agree. Taking the maximum
                # additionally makes a transient low `pages` fail loudly on the
                # page-echo check rather than truncating the walk in silence.
                total_pages = max(total_pages, self._page_count(collection, page))

                shop_id, currency = self._shop_identity(payload, page)

                # Sorted newest-first, so a product added mid-walk shifts every
                # later page down by one and re-serves a row already yielded.
                page_items = []
                for product_id, product in products.items():
                    if product_id in seen_ids:
                        continue
                    seen_ids.add(product_id)
                    item = self._parse_product(product, shop_id, currency)
                    if item is not None:
                        page_items.append(item)

                await report_page(page, len(page_items))
                total_yielded += len(page_items)
                for item in page_items:
                    yield item
                page += 1

        if total_yielded == 0:
            raise RuntimeError(
                "parsed 0 vinyl items across the entire Byrdland Records catalog -- title format drift"
            )

    @staticmethod
    def _page_count(collection: dict, page: int) -> int:
        """Read the page total the response reports, or raise.

        Treating it as optional and defaulting to 1 would turn a whole
        multi-page catalog into a successful single-page snapshot -- the walk
        would report success having fetched a fraction of the stock, and
        replace_stock_items() would delete the rest.
        """
        pages = collection.get("pages")
        if not isinstance(pages, int) or isinstance(pages, bool) or pages < 1:
            raise RuntimeError(
                f"{_page_path(page)} reports no usable page count ({pages!r}) -- payload shape drift"
            )
        return pages

    @staticmethod
    def _item_count(collection: dict, page: int) -> int:
        """Read the collection-wide item total the response reports, or raise."""
        count = collection.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise RuntimeError(
                f"{_page_path(page)} reports no usable item count ({count!r}) -- payload shape drift"
            )
        return count

    @staticmethod
    def _shop_identity(payload: dict, page: int):
        """Read the shop id and currency the response echoes, or raise.

        Both are present on every page, so a missing one is shape drift. There
        is deliberately no default: falling back to USD would record a crawl
        of a re-denominated store as healthy, and a missing shop id would
        quietly strip every cover URL -- each replacing good rows with
        degraded ones rather than leaving the previous snapshot alone.
        """
        shop = payload.get("shop") or {}
        shop_id, currency = shop.get("id"), shop.get("currency")
        if not shop_id or not currency:
            raise RuntimeError(
                f"{_page_path(page)} carries no shop id/currency "
                f"(id={shop_id!r}, currency={currency!r}) -- payload shape drift"
            )
        return shop_id, currency.upper()

    @staticmethod
    def _assert_page_echo(collection: dict, requested: int) -> None:
        """Raise unless the response is the page that was asked for.

        Paging past the last page does not 404 or answer empty -- it silently
        serves page 1 again with HTTP 200 (confirmed live: page35 of a
        34-page category returns `"page": 1` and a full 100 rows). Nothing
        else in this loop can tell that from real data, so an off-by-one in
        the `pages` bound would otherwise re-ingest page 1 forever. The same
        check catches a regression to the ignored `?page=` querystring.
        """
        echoed = collection.get("page")
        if echoed != requested:
            raise RuntimeError(
                f"requested {_page_path(requested)} but the store answered page {echoed!r} -- "
                "pagination contract drift"
            )

    @classmethod
    def _parse_product(cls, product: dict, shop_id, currency: str) -> Optional[dict]:
        title = _WS_RE.sub(" ", product.get("title") or "").strip()
        if not title:
            return None
        if _NON_VINYL_RE.search(title) and not _VINYL_WORD_RE.search(_NOT_VINYL_RE.sub(" ", title)):
            return None

        m = _TITLE_RE.match(title)
        if not m:
            # No artist source of any kind: `brand` is false on every product
            # in this category, so the title is all there is. Skipping matches
            # the fleet's "no artist source -> skip" convention.
            return None

        handle_url = product.get("url") or ""
        if not handle_url:
            # The url *is* the row's identity. Emitting the store root instead
            # would publish a bogus one and, because the crawl still counts as
            # healthy, replace the real row with it.
            raise RuntimeError(
                f"product {product.get('id')!r} carries no url -- payload shape drift"
            )
        handle = handle_url.rsplit(".html", 1)[0]
        return {
            "artist": m.group("artist").strip(),
            "title": m.group("album").strip(),
            "format": "Vinyl",
            "price": cls._price(product),
            "currency": currency,
            "url": f"{cls.base_url}/{handle_url}",
            "cover_image_url": cls._cover_image(product, shop_id, handle),
        }

    @staticmethod
    def _price(product: dict) -> Optional[float]:
        raw = (product.get("price") or {}).get("price")
        # bool is an int subclass, so True would otherwise price a record at 1.
        if isinstance(raw, bool):
            return None
        try:
            price = float(raw)
        except (TypeError, ValueError):
            return None
        # One live product is listed at 0. That is "call us", not free. nan and
        # inf are payload corruption; nan is the one a bare falsiness check
        # cannot catch, since it is truthy and would reach the stock row and
        # break JSON serialisation downstream. Same guard shape as
        # discogs_marketplace.py, roughtrade.py and sideonedummyrecords.py.
        if not math.isfinite(price) or price <= 0:
            return None
        return price

    @staticmethod
    def _cover_image(product: dict, shop_id, handle: str) -> Optional[str]:
        image_id = product.get("image")
        if not image_id:
            return None
        # The CDN ignores the trailing filename segment entirely -- it keys on
        # the image id alone, and serves the same bytes for any slug
        # (confirmed live). The handle is used only so the URL is readable.
        return _IMAGE_CDN.format(shop_id=shop_id, image_id=image_id, slug=handle or "cover")
