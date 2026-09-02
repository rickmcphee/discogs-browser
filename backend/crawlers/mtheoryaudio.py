import html
import re
from typing import AsyncIterator, Optional

import httpx

from catalog_http import get_with_retry
from config import load_config
from crawl_progress import report_page

# The whole store is one Bandzoogle "store feature" on this page. Its first
# batch of items is rendered into the page itself; the rest arrive from the
# AJAX endpoint below, which is what the page's own store-features controller
# calls as the reader scrolls.
_STORE_PATH = "/store"
_ITEMS_PATH = "/go/stores/{store_id}/store_items"

# The walk is already bounded by the pager's own `data-load-more` flag and by
# a strictly-increasing offset, so this only bounds a storefront that answers
# `true` forever. Generous against a catalog of a few hundred items: the cap
# is reached only after tens of thousands.
_MAX_PAGES = 200

_WRAPPER_RE = re.compile(r'<div\b[^>]*\bclass="[^"]*\bstore-wrapper\b[^"]*"[^>]*>')
# Anchored on the attribute rather than on `class="store store-item …"`: the
# class list is theme-driven (`single-image`, `multiple-images`,
# `has-upsell-products` all appear live) while the id attribute is what makes
# it an item. It also excludes the `<select>` and `<div>` elements that carry
# the same attribute inside bundle forms and upsell blocks.
_ARTICLE_RE = re.compile(r'<article\b[^>]*\bdata-store-item-id="(\d+)"')

# "Frequently purchased together" renders whole sibling products -- title,
# price and product link -- *inside* the article. Everything below is read
# from the block before this marker, or the first `item-price` on a
# two-product article would price the record at its upsell's price.
_UPSELL_MARKER = '<div class="upsell-products"'

_STORE_ID_RE = re.compile(r'\bdata-store-id="([^"]*)"')
_OFFSET_RE = re.compile(r'\bdata-offset="([^"]*)"')
_LOAD_MORE_RE = re.compile(r'\bdata-load-more="([^"]*)"')

_H1_RE = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.DOTALL)
_PRICE_RE = re.compile(
    r'<div\b[^>]*\bclass="[^"]*\bitem-price\b[^"]*"[^>]*>(.*?)</div>', re.DOTALL
)
# Comma groups are exactly three digits, or there are none at all. A bare
# `[\d,]*` accepts a comma anywhere, so `$1,2,3.00` matched and was stored as
# 123.0 -- a wrong figure published by a crawl that reported success, which is
# the one outcome the raise below exists to prevent.
#
# The group spans the cents as well as the dollars. Tightening the grouping
# first left the decimals *outside* it, which `_price` then dropped: every
# price with non-zero cents was truncated to whole dollars. No live vinyl row
# carries cents today, which is exactly why a replay over the live catalog did
# not catch it -- so the tests pin the cents directly.
_PRICE_TEXT_RE = re.compile(r"^\$((?:\d{1,3}(?:,\d{3})*|\d+)(?:\.\d{1,2})?)$")
_SHARE_URL_RE = re.compile(r'\bdata-share-dialog-url-value="([^"]*)"')
_MAIN_IMAGE_RE = re.compile(r'<a\b[^>]*\bclass="[^"]*\bmain-image\b[^"]*"[^>]*>')
_HREF_RE = re.compile(r'\bhref="([^"]*)"')
_CLASS_RE = re.compile(r'\bclass="([^"]*)"')
_ITEM_TYPE_RE = re.compile(r'\bdata-cart--salable-item-type="([^"]*)"')
_DESCRIPTION_RE = re.compile(
    r'<div\b[^>]*\bclass="[^"]*\bdescription\b[^"]*"[^>]*>(.*?)'
    r'<div\b[^>]*\bclass="[^"]*\bproduct-action\b',
    re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# Only `StoreItem` is one purchasable release. `Bundle` is a multi-product
# package -- live: two LPs sold together, and two LP/CD-plus-shirt packages --
# whose title names the package rather than an album, so it can never match a
# library release. Excluded like fatherdaughterrecords.py's grab-bags and
# killrockstars.py's bundle variants.
_STORE_ITEM_TYPE = "StoreItem"

# Titles are `ARTIST - Album …` throughout. Whitespace on at least one side of
# the dash is the fleet's rule and this store needs it: the label's own merch
# is credited to `M-Theory Audio`, which an unspaced-hyphen split would clip to
# `M`. Live titles only ever use a spaced ASCII hyphen; the en/em dashes come
# from the shared class cleorecs.py and byrdlandrecords.py use.
_TITLE_RE = re.compile(r"^(?P<artist>.+?)(?:\s+[-–—]\s*|\s*[-–—]\s+)(?P<album>.+)$")
# `HATCHET - - Awaiting Evil` doubles the separator, leaving the second dash on
# the front of the album.
_LEADING_SEP_RE = re.compile(r"^[\s\-–—]+")

# Formats the store files alongside its records. `\d*\s?[x×]?\s?` follows
# spv.py/onetwothreefourgo.py: a disc count binds to its format word with no
# boundary between them, so a bare `\bcds?\b` cannot see the CD in `2CD`.
#
# `tapes?` and `book` are deliberately absent, both for byrdlandrecords.py's
# reason -- this store fuses the format into the title, so the pattern reads
# against album and artist names too. `booklet` appears in a dozen live titles
# and every live cassette says "cassette", so neither would earn its risk.
_NON_VINYL_RE = re.compile(
    r"\b\d*\s?[x×]?\s?cds?\b|\b\d*\s?[x×]?\s?dvds?\b|\bblu-?rays?\b|"
    r"\bdigipa[ck]\b|\bjewel\s?case\b|\bcassettes?\b|\bwallet\b",
    re.IGNORECASE,
)
# Products that are not a single release at all. Deliberately narrow:
# `sticker`, `patch`, `poster` and `slipmat` all appear live as things included
# *with* a record, so a wider merch vocabulary would drop real vinyl.
_MERCH_RE = re.compile(r"\bt-?shirts?\b|\bhoodies?\b|\bbundle\b", re.IGNORECASE)

# Unambiguous vinyl format vocabulary -- the only vocabulary trusted in a
# description. `flexi` and `picture disc` fire on nothing live and are carried
# from the sibling crawlers' shared pattern.
_VINYL_FORMAT_RE = re.compile(
    r'\bvinyl\b|\b\d*\s?[x×]?\s?lps?\b|\b(?:7|10|12)\s*"|\bgatefold\b|'
    r"\bflexi\b|\bpicture dis[ck]\b",
    re.IGNORECASE,
)
# Pressing vocabulary: vinyl-specific in a *title*, which names one edition
# ("Orange Repress", "EU pressing (250)", "black/red splatter"), and the only
# thing that identifies 16 of the 109 rows this crawler yields. Not trusted in a
# description, which talks about the release rather than the edition on sale --
# live proof: a $12 CD whose blurb says "from the 2023 repressing by Via
# Nocturna". Bare "press" is excluded so a label name cannot vote.
_VINYL_PRESSING_RE = re.compile(
    r"\brepress(?:ed|ing|es)?\b|\bpressings?\b|\bpressed\b|"
    r"\bsplatter\b|\bhaze\b|\bmarble\b|\bRSD\b",
    re.IGNORECASE,
)


class Crawler:
    site_name: str = "M-Theory Audio"
    base_url: str = "https://m-theoryaudio.com"
    genre_summary: str = (
        "Las Vegas metal and hard rock label founded by Marco Barbieri "
        "(ex-Century Media, Nuclear Blast US), selling its own catalog direct."
    )
    genre: str = "metal"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        """Walk the store feature's paged item list, yielding one row per vinyl product.

        db.replace_stock_items() DELETEs this crawler's rows before inserting
        and _sync_stock only skips that when the crawl *raised*, so a walk that
        completes short wipes stock it simply failed to re-find. Every guard
        below therefore raises rather than returning what it has.
        """
        cfg = load_config()
        delay = float(cfg.get("crawl_delay_seconds", 30))
        failure_limit = int(cfg.get("consecutive_failure_limit", 10))

        seen_ids = set()
        total_yielded = 0
        priced = 0

        async with httpx.AsyncClient(base_url=self.base_url, follow_redirects=True) as client:
            r = await get_with_retry(
                client, _STORE_PATH, delay=delay, failure_limit=failure_limit
            )
            doc = r.text
            store_id = None
            offset = 0
            page = 1
            while True:
                wrapper = self._wrapper(doc, page)
                page_store_id = self._store_id(wrapper, page)
                if store_id is None:
                    store_id = page_store_id
                elif page_store_id != store_id:
                    raise RuntimeError(
                        f"page {page} of the M-Theory Audio store belongs to store "
                        f"{page_store_id!r}, not the {store_id!r} the first page named -- "
                        "the rows already yielded and the rows still to come are not one snapshot"
                    )
                next_offset, load_more = self._pager(wrapper, page, offset)

                blocks = self._articles(doc)
                if not blocks:
                    raise RuntimeError(
                        f"no store items on page {page} of the M-Theory Audio store "
                        f"(offset {offset}) -- the store is empty or the markup drifted"
                    )
                # The pager advances by a fixed stride whatever a page returns
                # (confirmed live: the short final page still reports
                # offset + 20), so a page that is short of the stride while
                # more pages are promised means the walk is stepping over rows
                # it never fetched -- and a successful walk of short pages is
                # what hands replace_stock_items() a fraction of the catalog to
                # replace the whole of it with. Only the final page may be short.
                if len(blocks) > next_offset - offset or (
                    load_more and len(blocks) != next_offset - offset
                ):
                    raise RuntimeError(
                        f"page {page} of the M-Theory Audio store returned {len(blocks)} items "
                        f"for a pager stride of {next_offset - offset} "
                        f"(offset {offset}, more pages: {load_more}) -- the walk would skip rows"
                    )

                items = []
                for item_id, block in blocks:
                    # Offset pagination over a store that keeps selling: a
                    # product added mid-walk shifts later pages along and
                    # re-serves a row already yielded.
                    if item_id in seen_ids:
                        continue
                    seen_ids.add(item_id)
                    item = self._parse_item(item_id, block)
                    if item is not None:
                        items.append(item)

                await report_page(page, len(items))
                total_yielded += len(items)
                priced += sum(1 for item in items if item["price"] is not None)
                for item in items:
                    yield item

                if not load_more:
                    break
                page += 1
                if page > _MAX_PAGES:
                    raise RuntimeError(
                        f"the M-Theory Audio store still reports more pages after {_MAX_PAGES} "
                        "of them -- pagination is not terminating"
                    )
                offset = next_offset
                r = await get_with_retry(
                    client, _ITEMS_PATH.format(store_id=store_id),
                    params={"offset": offset},
                    delay=delay, failure_limit=failure_limit,
                )
                doc = r.text

        if total_yielded == 0:
            raise RuntimeError(
                "parsed 0 vinyl items across the entire M-Theory Audio store -- "
                "title format drift"
            )
        if priced == 0:
            raise RuntimeError(
                f"none of the {total_yielded} M-Theory Audio rows carries a price -- "
                "price markup drift, and a whole catalog re-listed without prices is "
                "worse than the snapshot it would replace"
            )

    @staticmethod
    def _wrapper(doc: str, page: int) -> str:
        """Return the store-feature wrapper's opening tag, which carries the pager state."""
        m = _WRAPPER_RE.search(doc)
        if not m:
            raise RuntimeError(
                f"no store wrapper on page {page} of the M-Theory Audio store -- "
                "the store feature was removed or the markup drifted"
            )
        return m.group(0)

    @staticmethod
    def _store_id(wrapper: str, page: int) -> str:
        m = _STORE_ID_RE.search(wrapper)
        if not m or not m.group(1):
            raise RuntimeError(
                f"page {page} of the M-Theory Audio store names no store id -- "
                "there is no endpoint to page against"
            )
        return m.group(1)

    @staticmethod
    def _pager(wrapper: str, page: int, offset: int):
        """Read the next offset and the more-pages flag, or raise.

        Both are required rather than defaulted. A missing `data-load-more`
        read as "false" would collapse the whole catalog into a successful
        one-page snapshot, and a missing `data-offset` would re-request the
        page just read.
        """
        raw_offset = _OFFSET_RE.search(wrapper)
        raw_more = _LOAD_MORE_RE.search(wrapper)
        if raw_more is None or raw_more.group(1) not in ("true", "false"):
            raise RuntimeError(
                f"page {page} of the M-Theory Audio store reports no usable more-pages flag "
                f"({raw_more and raw_more.group(1)!r}) -- the walk cannot tell the end of the "
                "catalog from the middle of it"
            )
        if raw_offset is None or not raw_offset.group(1).isdigit():
            raise RuntimeError(
                f"page {page} of the M-Theory Audio store reports no usable next offset "
                f"({raw_offset and raw_offset.group(1)!r}) -- pagination contract drift"
            )
        next_offset = int(raw_offset.group(1))
        if next_offset <= offset:
            raise RuntimeError(
                f"page {page} of the M-Theory Audio store points back at offset {next_offset} "
                f"from {offset} -- the walk would repeat itself"
            )
        return next_offset, raw_more.group(1) == "true"

    @staticmethod
    def _articles(doc: str):
        """Split the document into (item id, markup) for each store item, in page order."""
        matches = list(_ARTICLE_RE.finditer(doc))
        return [
            (
                m.group(1),
                doc[m.start(): matches[i + 1].start() if i + 1 < len(matches) else len(doc)],
            )
            for i, m in enumerate(matches)
        ]

    @classmethod
    def _parse_item(cls, item_id: str, block: str) -> Optional[dict]:
        head = block.split(_UPSELL_MARKER, 1)[0]
        form = cls._form(head, item_id)

        if _ITEM_TYPE_RE.search(form).group(1) != _STORE_ITEM_TYPE:
            return None
        if not cls._available(form, item_id):
            return None

        heading = _H1_RE.search(head)
        if not heading:
            raise RuntimeError(
                f"M-Theory Audio store item {item_id} has no heading -- markup drift, and "
                "the heading is this store's only artist and format source"
            )
        title = _WS_RE.sub(" ", html.unescape(heading.group(1))).strip()
        if not title:
            return None
        if not cls._is_vinyl(title, cls._description(head, item_id)):
            return None

        m = _TITLE_RE.match(title)
        if not m:
            # No artist source but the title: this store's items carry no
            # vendor/brand field at all. Live, the titles that do not split are
            # the label's own merch and a magazine, never a release.
            return None
        album = _LEADING_SEP_RE.sub("", m.group("album")).strip()
        artist = m.group("artist").strip()
        if not (artist and album):
            return None

        price, currency = cls._price(head, item_id)
        return {
            "artist": artist,
            "title": album,
            "format": "Vinyl",
            "price": price,
            "currency": currency,
            "url": cls._url(head, item_id),
            "cover_image_url": cls._cover_image(head),
        }

    @staticmethod
    def _form(head: str, item_id: str) -> str:
        """Return the item's own cart form tag, which carries its kind and availability."""
        m = re.search(
            r'<form\b[^>]*\bdata-cart--salable-item-id="%s"[^>]*>' % re.escape(item_id), head
        )
        if not m or not _ITEM_TYPE_RE.search(m.group(0)):
            raise RuntimeError(
                f"M-Theory Audio store item {item_id} carries no typed cart form -- "
                "markup drift, and without it nothing distinguishes a sold-out item "
                "from a purchasable one"
            )
        return m.group(0)

    @staticmethod
    def _available(form: str, item_id: str) -> bool:
        """Read the server-rendered availability flag off the cart form.

        `available`/`not-available` is the only one of these classes the server
        decides. `in-stock` is rendered on every item and flipped to
        `out-of-stock` client-side from variant inventory, so reading it here
        would call every sold-out record in stock -- confirmed live on two
        sold-out items rendered `not-available … in-stock`.

        Pre-orders are purchasable and render `available`, so they are kept,
        as on every sibling store crawler.
        """
        declared = _CLASS_RE.search(form)
        classes = set((declared.group(1) if declared else "").split())
        if "not-available" in classes:
            return False
        if "available" in classes:
            return True
        raise RuntimeError(
            f"M-Theory Audio store item {item_id} declares neither available nor not-available "
            f"({sorted(classes)}) -- publishing an unbuyable record at a price is worse than "
            "failing the crawl"
        )

    @staticmethod
    def _description(head: str, item_id: str) -> str:
        """The item's blurb, cut to its own block and reduced to plain text.

        Both halves of that guard the same failure from two directions: markup
        that is not prose voting on the format. The block is cut out rather
        than the whole article scanned, because the article's other markup
        carries vinyl vocabulary in attribute values -- the cover of a live
        pre-order is `7mtp-lp-mockup.png`, and `\\bLP\\b` matches inside that
        filename. Tags are then stripped because the blurb itself is
        author-written HTML, so a link pasted into it can carry the same
        vocabulary inside the block.

        An *absent* block raises rather than reading as an empty blurb. Every
        live item has one, so its absence is a theme change rather than a
        quiet listing -- and reading it as silence would drop every record
        whose title names no format while the crawl still reported success,
        which is the shape that deletes stock instead of merely under-reporting
        it. An empty but present block still returns an empty string.
        """
        m = _DESCRIPTION_RE.search(head)
        if not m:
            raise RuntimeError(
                f"M-Theory Audio store item {item_id} has no description block -- markup "
                "drift, and treating that as an empty blurb would silently drop every "
                "record whose title names no format and delete its stock"
            )
        return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", m.group(1)))).strip()

    @staticmethod
    def _is_vinyl(title: str, description: str) -> bool:
        """Decide whether a listing is a record, from its title and then its blurb.

        The store sells vinyl, CD, cassette and merch from one undifferentiated
        list and publishes no format field anywhere -- not in the listing, not
        on the product page, not in any JSON view -- so the text is the only
        source there is. An explicit signal is therefore required rather than
        assumed: a title with no format word at all is far more often a CD than
        a record, so silence is read as "not a record" and the resulting scope
        loss is accepted, in the fleet's usual direction.
        """
        if _MERCH_RE.search(title) or _NON_VINYL_RE.search(title):
            return False
        if _VINYL_FORMAT_RE.search(title) or _VINYL_PRESSING_RE.search(title):
            return True
        if _NON_VINYL_RE.search(description):
            return False
        return bool(_VINYL_FORMAT_RE.search(description))

    @staticmethod
    def _price(head: str, item_id: str):
        prices = _PRICE_RE.findall(head)
        if len(prices) > 1:
            raise RuntimeError(
                f"M-Theory Audio store item {item_id} shows {len(prices)} prices -- "
                "ambiguous, and picking one would silently publish the wrong figure"
            )
        if not prices:
            return None, None
        text = _WS_RE.sub(" ", html.unescape(prices[0])).strip()
        m = _PRICE_TEXT_RE.match(text)
        if not m:
            # Includes a re-denominated store: recording a EUR price as USD is
            # worse than failing, and this storefront publishes no currency
            # code anywhere -- the symbol is the whole signal.
            raise RuntimeError(
                f"M-Theory Audio store item {item_id} prices at {text!r}, which is not a "
                "plain US dollar amount -- currency or price-markup drift"
            )
        return float(m.group(1).replace(",", "")), "USD"

    @classmethod
    def _url(cls, head: str, item_id: str) -> str:
        m = _SHARE_URL_RE.search(head)
        prefix = f"{cls.base_url}/product/{item_id}"
        if not m or not (m.group(1) == prefix or m.group(1).startswith(prefix + "-")):
            raise RuntimeError(
                f"M-Theory Audio store item {item_id} carries no product URL of its own "
                f"({m and m.group(1)!r}) -- the URL is half of the row's identity, so a "
                "guessed one would publish a bogus link and orphan the row's history"
            )
        return m.group(1)

    @staticmethod
    def _cover_image(head: str) -> Optional[str]:
        m = _MAIN_IMAGE_RE.search(head)
        if not m:
            return None
        href = _HREF_RE.search(m.group(0))
        if not href or not href.group(1):
            return None
        # The CDN is linked protocol-relative throughout.
        return f"https:{href.group(1)}" if href.group(1).startswith("//") else href.group(1)
