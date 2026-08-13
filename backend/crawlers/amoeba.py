import random
import re
from asyncio import sleep
from typing import AsyncIterator, Optional

from config import load_config
from crawl_progress import report_page
from crawler import BotDetectedError
from logging_config import get_logger

log = get_logger("amoeba")

# LP=3, 12"=4, 7"=17, 10"=19, 78=21. CD=1 and Cassette=24 are out of scope.
# %5B/%5D are hardcoded, not urlencode()'d, so this matches byte-for-byte the
# querystring the site's own tableUpdater.init() config sends -- though
# urlencode() would in fact produce identical bytes here, since quote_plus
# doesn't treat '[' or ']' as safe.
_VINYL_FORMAT_IDS = (3, 4, 17, 19, 21)

# 200 is the largest size the site's own #show-per-page control offers. Larger
# values are honoured server-side but are outside that contract -- if one were
# ever clamped, a single show=1000 request would silently yield 200 items
# instead of 1000 rather than failing. Capping at 200 keeps a short page
# detectable: crawl_catalog() warns when a page yields fewer than _PAGE_SIZE
# rows, which only distinguishes a clamped show= from a genuinely short page
# while a full page is expected to be exactly this size.
_PAGE_SIZE = 200

# _PAGE_SIZE * _WINDOW_PAGES == 1000, the window size the design spec chose.
# Raising either constant on its own breaks that relationship -- not a cheap win.
_WINDOW_PAGES = 5

_NEW_PRICE_RE = re.compile(r"\$(\d[\d,]*(?:\.\d{1,2})?)")

# "from" means the lowest of several used copies, not an exact price -- the
# yielded price undershoots what any single copy actually costs for these
# rows (about 1 in 1,000 live).
_USED_PRICE_RE = re.compile(r"\bUsed\s+(?:for|from)\s+\$(\d[\d,]*(?:\.\d{1,2})?)")

# The format icon's alt is always "Vinyl" here (its src is even misleadingly
# CD.png), so it carries no per-format signal -- the title's trailing token
# is the only real source.
_FORMAT_SUFFIX_RE = re.compile(r'\((LP|7"|10"|12"|78)\)\s*$')

_ALBUM_ID_RE = re.compile(r"/albums/(\d+)")

# The fragment is wrapped in <table> so the HTML parser builds real <tr>/<td>
# structure -- parsing bare <tr> markup drops the cells.
_FETCH_AND_EXTRACT_JS = """
async (args) => {
  const response = await fetch(args.url, {headers: {'X-Requested-With': 'XMLHttpRequest'}});
  if (response.status !== 200) return {status: response.status, rows: []};
  const payload = await response.json();
  if (typeof payload.data !== 'string') throw new Error('unexpected cds_and_vinyl.php payload shape');
  const doc = new DOMParser().parseFromString('<table>' + payload.data + '</table>', 'text/html');
  const rows = Array.from(doc.querySelectorAll('tr')).map(tr => {
    const titleEl = tr.querySelector('.search-deets a');
    const artistEl = tr.querySelector('a[href*="/artist/"]');
    const priceEl = tr.querySelector('.price');
    const usedEl = tr.querySelector('a.red-link');
    const imgEl = tr.querySelector('.search-thumb img');
    return {
      href: titleEl ? titleEl.getAttribute('href') : null,
      title: titleEl ? titleEl.textContent.replace(/\\s+/g, ' ').trim() : null,
      artist: artistEl ? artistEl.textContent.replace(/\\s+/g, ' ').trim() : null,
      newPrice: priceEl ? priceEl.textContent.trim() : null,
      used: usedEl ? usedEl.textContent.trim() : null,
      image: imgEl ? imgEl.getAttribute('src') : null,
    };
  });
  return {status: 200, rows: rows};
}
"""


class Crawler:
    site_name: str = "Amoeba Music"
    base_url: str = "https://www.amoeba.com"
    genre_summary: str = "Large independent record store selling new and used vinyl and CDs across nearly every genre."
    crawler_type: str = "catalog_browser"

    @classmethod
    def _listing_url(cls, page_num: int) -> str:
        formats = "".join(f"&format%5B{i}%5D={i}" for i in _VINYL_FORMAT_IDS)
        return (
            f"/ajax/cds_and_vinyl.php?page={page_num}&show={_PAGE_SIZE}"
            f"&order=date&direction=desc{formats}"
        )

    @staticmethod
    def _extract_price(new_price: Optional[str], used_label: Optional[str]) -> Optional[float]:
        for pattern, text in ((_NEW_PRICE_RE, new_price), (_USED_PRICE_RE, used_label)):
            if not text:
                continue
            match = pattern.search(text)
            if match:
                return float(match.group(1).replace(",", ""))
        return None

    @staticmethod
    def _extract_format(title: str) -> str:
        match = _FORMAT_SUFFIX_RE.search(title)
        return match.group(1) if match else "Vinyl"

    @classmethod
    def _parse_row(cls, row: dict) -> Optional[dict]:
        artist = (row.get("artist") or "").strip()
        title = (row.get("title") or "").strip()
        href = row.get("href") or ""
        if not (artist and title and href):
            return None

        price = cls._extract_price(row.get("newPrice"), row.get("used"))
        if price is None:
            return None

        return {
            "artist": artist,
            "title": title,
            "format": cls._extract_format(title),
            "price": price,
            "currency": "USD",
            "url": f"{cls.base_url}{href}",
            "cover_image_url": row.get("image"),
        }

    async def crawl_catalog(self, page) -> AsyncIterator[dict]:
        delay = float(load_config().get("crawl_delay_seconds", 30))
        seen_album_ids = set()

        await page.goto(f"{self.base_url}/music/cd-and-vinyl", timeout=120_000)
        if "Attention Required" in await page.title():
            raise BotDetectedError("Cloudflare block page")

        for page_num in range(1, _WINDOW_PAGES + 1):
            # The first sleep is the load-bearing one: it's the gap that lets
            # Cloudflare's JSD challenge script finish after goto() before the
            # AJAX call, not just pacing.
            await sleep(random.uniform(delay * 0.5, delay))
            result = await page.evaluate(
                _FETCH_AND_EXTRACT_JS, {"url": self._listing_url(page_num)}
            )
            if result["status"] != 200:
                raise BotDetectedError(
                    f"cds_and_vinyl.php returned HTTP {result['status']} on page {page_num}"
                )
            rows = result["rows"]
            if len(rows) < _PAGE_SIZE and page_num < _WINDOW_PAGES:
                log.warning(
                    "[amoeba] page %d returned %d rows, expected %d -- show= may be "
                    "clamped or the format filter stopped applying; the window is short",
                    page_num, len(rows), _PAGE_SIZE,
                )
            await report_page(page_num, len(rows))
            for row in rows:
                album_id = _ALBUM_ID_RE.search(row.get("href") or "")
                if not album_id or album_id.group(1) in seen_album_ids:
                    continue
                item = self._parse_row(row)
                if item is None:
                    continue
                seen_album_ids.add(album_id.group(1))
                yield item
