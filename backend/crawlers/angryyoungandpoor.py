import re
from typing import AsyncIterator, Optional

from crawler import BotDetectedError

# Same wider vinyl/format regex secretlystore.py uses -- plain \blp\b misses
# glued formats like "2xLP" -- plus revhq.py's \d+\s*" for bare inch-size
# singles (7"/10"/12") that carry no "lp"/"vinyl" wording at all.
_FORMAT_RE = re.compile(r'\bvinyl\b|\b\d*x?lp\b|\bep\b|\d+\s*"', re.IGNORECASE)
_USED_RE = re.compile(r'\s*\(USED\)\s*$')

# Confirmed live: these three share the "Artist- Title FORMAT (variant)"
# title shape (and Records/Sale-Records even share literal product IDs --
# Sale Records is a cross-listing, not separate stock). V/A Compilation LPs
# titles carry no artist prefix at all ("Barbarian (Soundtrack) LP ...") and
# use a different rule below.
_DASH_CATEGORIES = {"Records-c301.htm", "Sale-Records-c472.htm", "Used-Records-c1215.htm"}

# Single page.evaluate() round trip per category instead of looping
# page.locator() calls across ~4400 items. Scoped to the confirmed single
# .pcShowProducts container so an unrelated widget elsewhere on the page
# can't leak in a stray [data-pid].
_EXTRACT_JS = """
() => Array.from(document.querySelectorAll('.pcShowProducts [data-pid]')).map(el => {
  const nameEl = el.querySelector('[itemprop="name"]');
  const urlEl = el.querySelector('[itemprop="url"]');
  const imgEl = el.querySelector('[itemprop="image"]');
  const priceEl = el.querySelector('meta[itemprop="price"]');
  return {
    pid: el.getAttribute('data-pid'),
    name: nameEl ? nameEl.textContent.trim() : null,
    url: urlEl ? urlEl.getAttribute('href') : null,
    image: imgEl ? imgEl.getAttribute('src') : null,
    price: priceEl ? priceEl.getAttribute('content') : null,
  };
}).filter(p => p.pid && p.name && p.url && p.price)
"""


class Crawler:
    site_name: str = "Angry Young and Poor"
    base_url: str = "https://www.angryyoungandpoor.com/store/pc"
    crawler_type: str = "catalog_browser"

    _CATEGORIES = [
        "Records-c301.htm",
        "Sale-Records-c472.htm",
        "Used-Records-c1215.htm",
        "V-A-Compilation-LPs-c397.htm",
    ]

    async def crawl_catalog(self, page) -> AsyncIterator[dict]:
        seen_pids: set[str] = set()
        for category_path in self._CATEGORIES:
            await page.goto(f"{self.base_url}/{category_path}?viewAll=yes", timeout=120_000)
            if "Cloudflare" in await page.title():
                raise BotDetectedError("Cloudflare interstitial")
            raw_products = await page.evaluate(_EXTRACT_JS)
            for product in raw_products:
                pid = product["pid"]
                if pid in seen_pids:
                    continue
                item = self._parse_product(category_path, product)
                if item is None:
                    continue
                seen_pids.add(pid)
                yield item

    @classmethod
    def _parse_product(cls, category_path: str, product: dict) -> Optional[dict]:
        name = product["name"]
        if category_path in _DASH_CATEGORIES:
            if "- " not in name:
                return None
            artist, remainder = name.split("- ", 1)
            artist = artist.strip()
            remainder = remainder.strip()
            if not artist or not remainder or not _FORMAT_RE.search(remainder):
                return None
        else:
            if not _FORMAT_RE.search(name):
                return None
            artist = "Various Artists"
            remainder = name.strip()

        if _USED_RE.search(remainder):
            remainder = _USED_RE.sub('', remainder).strip()
            title = f"{remainder} (Used)"
        else:
            title = remainder

        try:
            price = float(product["price"])
        except (TypeError, ValueError):
            price = None

        return {
            "artist": artist,
            "title": title,
            "format": "Vinyl",
            "price": price,
            "currency": "USD",
            "url": f"{cls.base_url}/{product['url']}",
            "cover_image_url": f"{cls.base_url}/{product['image']}" if product.get("image") else None,
        }
