import html
import json
import random
import re
from asyncio import sleep
from typing import AsyncIterator, Optional

import httpx

from config import load_config
from crawl_progress import report_page

_CATEGORY_SLUG = "vinyl-lp"
_PER_PAGE = 100
# The Store API's `name` field carries the en dash as the literal HTML
# entity `&#8211;`, not the unicode character -- html.unescape() first,
# then split on the real en dash. Confirmed live: 737/738 vinyl-lp titles
# use "Artist – Title" with this exact separator; the one holdout ("Regere
# Sinister / Reptile Womb Split LP") has no separator at all and is
# skipped, matching this repo's "no artist source -> skip" convention.
_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s+–\s+(?P<album>.+)$')
_VARIATIONS_RE = re.compile(r'data-product_variations="([^"]*)"')


class Crawler:
    site_name: str = "Dark Descent Records"
    base_url: str = "https://www.darkdescentrecords.com/shop"
    genre_summary: str = (
        "Underground metal label and distro specializing in death, black, and doom metal."
    )
    genre: str = "metal"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        cfg = load_config()
        delay = float(cfg.get("crawl_delay_seconds", 30))

        async with httpx.AsyncClient(base_url=self.base_url, follow_redirects=True) as client:
            page = 1
            while True:
                await sleep(random.uniform(delay * 0.5, delay))
                r = await client.get(
                    "/wp-json/wc/store/v1/products",
                    params={"category": _CATEGORY_SLUG, "per_page": _PER_PAGE, "page": page},
                )
                r.raise_for_status()
                products = r.json()
                if not products:
                    break

                page_items = []
                for product in products:
                    page_items.extend(await self._items(product, client, delay))
                await report_page(page, len(page_items))
                for item in page_items:
                    yield item

                if len(products) < _PER_PAGE:
                    break
                page += 1

    @classmethod
    async def _items(cls, product: dict, client: httpx.AsyncClient, delay: float) -> list:
        if not (product.get("is_purchasable") and product.get("is_in_stock")):
            return []

        artist, album = cls._parse_artist_title(product.get("name", ""))
        if artist is None:
            return []

        url = product.get("permalink", "")
        currency = (product.get("prices") or {}).get("currency_code", "USD")

        if product.get("type") == "variable":
            await sleep(random.uniform(delay * 0.5, delay))
            r = await client.get(url)
            r.raise_for_status()
            return cls._variable_items(r.text, product, artist, album, url, currency)

        return [{
            "artist": artist,
            "title": album,
            "format": "Vinyl",
            "price": cls._price(product.get("prices") or {}),
            "currency": currency,
            "url": url,
            "cover_image_url": cls._cover_image(product),
        }]

    @classmethod
    def _variable_items(cls, page_html: str, product: dict, artist: str, album: str,
                         url: str, currency: str) -> list:
        m = _VARIATIONS_RE.search(page_html)
        if not m:
            # Not "the site has nothing" -- a variable product's page always
            # carries this data live, so a miss is a parser/markup failure
            # and must raise, not silently drop the product from the batch.
            raise RuntimeError(f"no data-product_variations found on {url}")
        variations = json.loads(html.unescape(m.group(1)))

        items = []
        for variation in variations:
            if not (variation.get("is_purchasable") and variation.get("is_in_stock")):
                continue
            attrs = variation.get("attributes") or {}
            variant_title = " / ".join(v for v in attrs.values() if v)
            title = f"{album} — {variant_title}" if variant_title else album
            price = variation.get("display_price")
            image_url = (variation.get("image") or {}).get("src") or cls._cover_image(product)
            items.append({
                "artist": artist,
                "title": title,
                "format": "Vinyl",
                "price": float(price) if isinstance(price, (int, float)) else None,
                "currency": currency,
                "url": url,
                "cover_image_url": image_url,
            })
        return items

    @staticmethod
    def _cover_image(product: dict) -> Optional[str]:
        images = product.get("images") or []
        return images[0].get("src") if images else None

    @staticmethod
    def _price(prices: dict) -> Optional[float]:
        raw = prices.get("price")
        if raw is None:
            return None
        try:
            minor_unit = int(prices.get("currency_minor_unit", 2))
            return int(raw) / (10 ** minor_unit)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_artist_title(name: str):
        clean = html.unescape(name or "").strip()
        m = _TITLE_RE.match(clean)
        if m:
            return m.group("artist").strip(), m.group("album").strip()
        return None, clean
