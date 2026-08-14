import html
import random
import re
from asyncio import sleep
from typing import AsyncIterator

import httpx

from config import load_config
from crawl_progress import report_page

# Whitespace required on at least one side of the hyphen, matching the
# repo's standard fix for this bug class: a plain \s*-\s* form would clip a
# hyphenated word with no surrounding space (confirmed live here --
# "The Suicide Machines-On the Eve of Destruction 2xLP" -- into
# "The Suicide Machines" plus a mangled album).
_TITLE_RE = re.compile(r'^(?P<artist>.+?)(?:\s+-\s*|\s*-\s+)(?P<album>.+)$')
_VARIOUS_RE = re.compile(r'^various(?:\s+artists)?$', re.IGNORECASE)
# Bigcartel's own `categories` field is not used for inclusion -- confirmed
# live, 26% of real vinyl releases carry an empty categories array. This
# regex (same shape as angryyoungandpoor.py's, which filters an equally
# mixed single-store catalog) is the sole inclusion gate.
_FORMAT_RE = re.compile(r'\bvinyl\b|\b\d*x?lp\b|\bep\b|\d+\s*"', re.IGNORECASE)


class Crawler:
    site_name: str = "Asbestos Records"
    base_url: str = "https://asbestosrecords.bigcartel.com"
    genre_summary: str = "Ska, punk, and hardcore label and record store."
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        # Confirmed live: page= and limit= query params are silently ignored
        # on this store -- /products.json always returns the full 76-product
        # catalog in one response. One request per sync, still paced like
        # every sibling crawler.
        cfg = load_config()
        delay = float(cfg.get("crawl_delay_seconds", 30))
        await sleep(random.uniform(delay * 0.5, delay))
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{self.base_url}/products.json")
            r.raise_for_status()
        products = r.json()

        items = [item for product in products for item in self._items(product)]
        await report_page(1, len(items))
        for item in items:
            yield item

    @classmethod
    def _items(cls, product: dict) -> list:
        name = product.get("name", "")
        if not _FORMAT_RE.search(name):
            return []

        artist, album = cls._parse_artist_title(name, product.get("artists") or [])
        if artist is None:
            return []

        url = f"{cls.base_url}{product.get('url', '')}"
        images = product.get("images") or []
        cover_image_url = images[0].get("url") if images else None
        clean_name = html.unescape(name).strip()

        items = []
        for option in product.get("options") or []:
            if option.get("sold_out"):
                continue
            option_name = html.unescape(option.get("name") or "").strip()
            title = album if option_name == clean_name else f"{album} — {option_name}"
            price = option.get("price")
            items.append({
                "artist": artist,
                "title": title,
                "format": "Vinyl",
                "price": float(price) if isinstance(price, (int, float)) else None,
                "currency": "USD",
                "url": url,
                "cover_image_url": cover_image_url,
            })
        return items

    @classmethod
    def _parse_artist_title(cls, name: str, artists: list):
        # Bigcartel's `artists` field is store-curated per product (unlike a
        # Shopify `vendor`, which is one label name repeated on every row),
        # but it's only trustworthy as a fallback: some tagged artists don't
        # literally match the title's billing (e.g. a member's solo release
        # tagged under their main band), so a literal title split always
        # wins when one exists.
        clean = html.unescape(name).strip()
        m = _TITLE_RE.match(clean)
        if m:
            artist = m.group("artist").strip()
            album = m.group("album").strip()
            if _VARIOUS_RE.match(artist):
                artist = "Various"
            return artist, album
        if artists:
            return html.unescape(artists[0].get("name") or "").strip(), clean
        return None, clean
