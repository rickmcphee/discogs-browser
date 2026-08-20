import random
import re
from asyncio import sleep
from typing import AsyncIterator, Optional

from config import load_config
from crawl_progress import report_page
from crawler import BotDetectedError

# Titles are almost always "Artist - Title ...", but a handful use
# "Artist 'Title' ..." instead (e.g. "Satsang 'All. Right. Now' 2xLP/CD -
# Orange Vinyl w Black Smoke"). Matches whichever separator appears first --
# the dash form consumes the surrounding " - " entirely, the quote form is a
# zero-width lookahead so the opening quote stays part of the title. Bare
# apostrophes inside words ("Swingin'", "Can't") never match: both require
# whitespace immediately before the punctuation, which mid-word apostrophes
# don't have.
_SEPARATOR_RE = re.compile(r"\s*-\s+|\s+(?=['‘])")

# No .Pricing block at all for an out-of-stock product (an .OutOfStockMsg
# div replaces it), so filtering on listPrice here doubles as the
# in-stock check -- no separate out-of-stock marker to inspect.
_EXTRACT_JS = """
() => Array.from(document.querySelectorAll('li.ProductElementsDisplay')).map(li => {
  const nameEl = li.querySelector('.ProductName');
  const linkEl = nameEl ? nameEl.querySelector('a') : null;
  const imgEl = li.querySelector('img.ProductImg');
  const pricingEl = li.querySelector('.PricingContainer');
  return {
    id: nameEl ? nameEl.getAttribute('data-productid') : null,
    name: nameEl ? nameEl.getAttribute('data-productname') : null,
    href: linkEl ? linkEl.getAttribute('href') : null,
    image: imgEl ? imgEl.getAttribute('src') : null,
    listPrice: pricingEl ? pricingEl.getAttribute('data-listprice') : null,
    salePrice: pricingEl ? pricingEl.getAttribute('data-saleprice') : null,
  };
}).filter(p => p.id && p.name && p.href && p.listPrice)
"""


class Crawler:
    site_name: str = "SideOneDummy Records"
    base_url: str = "https://sideonedummyrecords.shop.musictoday.com"
    genre_summary: str = "Long-running punk and ska label's official store, including exclusive vinyl variants."
    genre: str = "punk"
    crawler_type: str = "catalog_browser"

    async def crawl_catalog(self, page) -> AsyncIterator[dict]:
        delay = float(load_config().get("crawl_delay_seconds", 30))
        await sleep(random.uniform(delay * 0.5, delay))
        await page.goto(f"{self.base_url}/dept/vinyl", timeout=120_000)
        if "Just a moment" in await page.title():
            raise BotDetectedError("Cloudflare interstitial")

        products = await page.evaluate(_EXTRACT_JS)
        await report_page(1, len(products))
        for product in products:
            item = self._parse_product(product)
            if item is not None:
                yield item

    @classmethod
    def _parse_product(cls, product: dict) -> Optional[dict]:
        artist, title = cls._parse_artist_title(product["name"])
        if artist is None:
            return None

        price = cls._price(product.get("salePrice") or product.get("listPrice"))
        if price is None:
            return None

        image = product.get("image")
        return {
            "artist": artist,
            "title": title,
            "format": "Vinyl",
            "price": price,
            "currency": "USD",
            "url": f"{cls.base_url}{product['href'].split('?')[0]}",
            "cover_image_url": f"https:{image}" if image else None,
        }

    @staticmethod
    def _parse_artist_title(name: str):
        m = _SEPARATOR_RE.search(name)
        if not m:
            return None, name.strip()
        artist = name[:m.start()].strip()
        title = name[m.end():].strip()
        if not artist or not title:
            return None, name.strip()
        return artist, title

    @staticmethod
    def _price(raw: Optional[str]) -> Optional[float]:
        if not raw:
            return None
        try:
            return float(raw.replace("$", "").replace(",", "").strip())
        except ValueError:
            return None
