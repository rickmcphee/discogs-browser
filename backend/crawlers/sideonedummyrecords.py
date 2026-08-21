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
# zero-width lookahead so the opening quote stays part of the raw remainder
# (peeled off separately below by _QUOTED_RE). Bare apostrophes inside words
# ("Swingin'", "Can't") never match: both require whitespace immediately
# before the punctuation, which mid-word apostrophes don't have.
_SEPARATOR_RE = re.compile(r"\s*-\s+|\s+(?=['‘])")

# Peels the quote delimiters off a quote-form remainder, e.g. "'All. Right.
# Now' 2xLP/CD - Orange Vinyl w Black Smoke" -> quoted="All. Right. Now",
# rest="2xLP/CD - Orange Vinyl w Black Smoke". Left as-is (with its leading
# quote) if this doesn't match -- e.g. an unpaired quote mark -- rather than
# raising, since the title is still usable, just imperfectly delimited.
_QUOTED_RE = re.compile(r"^['‘](?P<quoted>.+?)['’]\s*(?P<rest>.*)$")

# No .Pricing block at all for an out-of-stock product (an .OutOfStockMsg
# div replaces it), so filtering on listPrice here doubles as the
# in-stock check -- no separate out-of-stock marker to inspect. rawCount is
# the unfiltered <li> count, kept separate from the in-stock rows so
# crawl_catalog can tell "the site has nothing" (rawCount > 0, no rows pass
# the filter -- a real sellout) apart from "our selectors broke or an
# interstitial slipped past the title check" (rawCount == 0) -- the two
# must not be conflated, since the former is a normal, patient crawl and the
# latter must raise so replace_stock_items() (backend/db.py) never wipes
# every known-in-stock row for this site on a false "nothing to see".
_EXTRACT_JS = """
() => {
  const lis = Array.from(document.querySelectorAll('li.ProductElementsDisplay'));
  const products = lis.map(li => {
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
  }).filter(p => p.id && p.name && p.href && p.listPrice);
  return {rawCount: lis.length, products: products};
}
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

        result = await page.evaluate(_EXTRACT_JS)
        if result["rawCount"] == 0:
            raise RuntimeError("no products found in vinyl listing -- markup drift or missed interstitial")

        products = result["products"]
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

        # Falls back to listPrice on a parse failure too, not just an empty
        # salePrice string -- a malformed (rather than merely absent)
        # salePrice must not drop an otherwise-valid, in-stock product that
        # the extraction filter already confirmed has a real listPrice.
        price = cls._price(product.get("salePrice"))
        if price is None:
            price = cls._price(product.get("listPrice"))
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

        # The quote form's title still opens with the quote mark here (see
        # _SEPARATOR_RE) -- peel it off so the title starts with the album
        # name itself. Downstream stock-to-catalog matching (db.py's
        # _library_match_fragment) requires the stock title to equal the
        # catalog title or start with it followed by a space; a leading
        # quote mark would never satisfy either, silently orphaning the row
        # from a release the user already owns or wants.
        qm = _QUOTED_RE.match(title)
        if qm:
            quoted = qm.group("quoted").strip()
            rest = qm.group("rest").strip()
            title = f"{quoted} {rest}" if rest else quoted

        return artist, title

    @staticmethod
    def _price(raw: Optional[str]) -> Optional[float]:
        if not raw:
            return None
        try:
            return float(raw.replace("$", "").replace(",", "").strip())
        except ValueError:
            return None
