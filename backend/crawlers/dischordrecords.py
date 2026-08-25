import html
import random
import re
from asyncio import sleep
from typing import AsyncIterator, Optional

import httpx

from config import load_config
from crawl_progress import report_detail, report_page

_PAGE_LINK_RE = re.compile(r'/label/dischord\?page=(\d+)')
_RELEASE_LINK_RE = re.compile(r'href="(/release/[^"]+)"')
_H1_RE = re.compile(
    r"<h1>\s*<span class='releaseNumber'>.*?</span>\s*"
    r'<a href="/band/[^"]*">(?P<artist>[^<]+)</a>\s*'
    r"<cite>(?P<title>[^<]+)</cite>",
    re.DOTALL,
)
_OG_IMAGE_RE = re.compile(r"<meta content='(?P<url>[^']*)' property='og:image'>")
_PRICES_DIV_RE = re.compile(
    r"<div class='productGeneral' id='productPrices'>(?P<body>.*?)</div>", re.DOTALL
)
# Matches a cart anchor by its href alone -- any attribute order, any extra
# attributes -- rather than pinning the exact attribute string Rails emits
# today. Pinning it made attribute drift *invisible*: a reordered or extra
# attribute matched nothing, so the release yielded no vinyl and was silently
# cleared by replace_stock_items instead of raising. The whole-crawl
# zero-vinyl guard can't cover that, since other releases still yield items.
# Text is deliberately [^<]* rather than [^<]+ so an empty anchor still
# matches here and fails _BUTTON_TEXT_RE below -- drift must reach a raise,
# never disappear. Safe to loosen because the search is already scoped to the
# #productPrices block, so no unrelated cart link on the page is in range.
_BUTTON_RE = re.compile(
    r'<a\b[^>]*\bhref="/cart/add/\d+"[^>]*>(?P<text>[^<]*)</a>'
)
_BUTTON_TEXT_RE = re.compile(
    r'^(?:Buy|Preorder)\s+(?P<format>.+?)\s+\$(?P<price>[\d,]+(?:\.\d+)?)$'
)
_PAREN_SUFFIX_RE = re.compile(r'\s*\([^)]*\)\s*$')
_NON_VINYL_FORMAT_RE = re.compile(
    r'^(?:CD(?:\s+(?:EP|Single))?|Digital(?:\s+Download)?|Cass(?:ette)?|DVD|VHS|'
    r'Blu-?Ray|Book|Zine|PCARD|Subscription|Maxi\s+CD|Dbl\s+CD|3-CD\s+Set|Tape)$',
    re.IGNORECASE,
)

_LABEL_PATH = "/label/dischord"


class Crawler:
    site_name: str = "Dischord Records"
    base_url: str = "https://dischord.com"
    genre_summary: str = (
        "Ian MacKaye and Jeff Nelson's DC hardcore/punk label -- Minor Threat, "
        "Fugazi, and the rest of the Dischord catalog, sold direct."
    )
    genre: str = "punk"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        delay = float(load_config().get("crawl_delay_seconds", 30))

        async with httpx.AsyncClient(base_url=self.base_url, follow_redirects=True) as client:
            await sleep(random.uniform(delay * 0.5, delay))
            r = await client.get(_LABEL_PATH, params={"page": 1})
            r.raise_for_status()
            page_html = r.text
            total_pages = self._max_page(page_html)

            seen_hrefs = set()
            total_yielded = 0
            page = 1
            while page <= total_pages:
                if page > 1:
                    await sleep(random.uniform(delay * 0.5, delay))
                    r = await client.get(_LABEL_PATH, params={"page": page})
                    r.raise_for_status()
                    page_html = r.text
                    total_pages = max(total_pages, self._max_page(page_html))

                hrefs = self._release_hrefs(page_html)
                if not hrefs:
                    raise RuntimeError(f"no release links found on {_LABEL_PATH}?page={page} -- markup drift")
                new_hrefs = [h for h in hrefs if h not in seen_hrefs]
                seen_hrefs.update(new_hrefs)

                # Progress is reported per detail fetch, not just per listing
                # page, because this crawler's two phases put tens of minutes
                # between one report_page() and the next -- a listing page's
                # worth of paced detail fetches. Reporting only at the end of
                # that meant a run showed nothing at all after "Stock crawl
                # started", which is indistinguishable from a hang while the
                # sync's advisory lock rejects every other Refresh. The 0/N
                # report before the loop is what puts the size of the wait on
                # the record before the first fetch has even finished.
                label = f"listing page {page}/{total_pages}"
                await report_detail(0, len(new_hrefs), label)
                page_items = []
                for done, href in enumerate(new_hrefs, start=1):
                    await sleep(random.uniform(delay * 0.5, delay))
                    r = await client.get(href)
                    if r.status_code != 404:
                        r.raise_for_status()
                        page_items.extend(self._parse_release(r.text, href))
                    await report_detail(done, len(new_hrefs), label)

                await report_page(page, len(page_items))
                total_yielded += len(page_items)
                for item in page_items:
                    yield item
                page += 1

        if total_yielded == 0:
            raise RuntimeError("parsed 0 vinyl items across the entire Dischord catalog -- format drift")

    @staticmethod
    def _max_page(html_text: str) -> int:
        pages = [int(n) for n in _PAGE_LINK_RE.findall(html_text)]
        return max(pages) if pages else 1

    @staticmethod
    def _release_hrefs(html_text: str) -> list:
        seen = set()
        hrefs = []
        for href in _RELEASE_LINK_RE.findall(html_text):
            if href not in seen:
                seen.add(href)
                hrefs.append(href)
        return hrefs

    @classmethod
    def _parse_release(cls, page_html: str, href: str) -> list:
        m = _H1_RE.search(page_html)
        if not m:
            raise RuntimeError(f"could not parse artist/title on {href} -- markup drift")
        artist = html.unescape(m.group("artist")).strip()
        title = html.unescape(m.group("title")).strip()

        img = _OG_IMAGE_RE.search(page_html)
        cover_image_url = img.group("url").strip() if img else None

        prices_div = _PRICES_DIV_RE.search(page_html)
        if not prices_div:
            raise RuntimeError(f"no productPrices block found on {href} -- markup drift")

        vinyl_formats = []
        for raw_text in _BUTTON_RE.findall(prices_div.group("body")):
            text = html.unescape(raw_text).strip()
            button_m = _BUTTON_TEXT_RE.match(text)
            if not button_m:
                raise RuntimeError(f"unparsable buy button {text!r} on {href} -- markup drift")
            fmt = button_m.group("format").strip()
            bare_fmt = _PAREN_SUFFIX_RE.sub('', fmt).strip()
            if _NON_VINYL_FORMAT_RE.match(bare_fmt):
                continue
            vinyl_formats.append((fmt, cls._price(button_m.group("price"))))

        url = f"{cls.base_url}{href}"
        # Suffixed unconditionally, never only when siblings happen to exist.
        # Every format of a release shares one URL, so the suffix is the only
        # thing separating two editions' item_keys (db.py's replace_stock_items
        # hashes the title). Deciding it from the *current* format count would
        # rewrite an edition's title -- and orphan its durable
        # stock_item_judgments row -- the moment a sibling format sold out.
        # asianmanrecords.py avoids that by deciding before its availability
        # filter, but that option doesn't exist here: this site omits an
        # unavailable format's button entirely, so the full edition set is
        # never observable.
        items = []
        for fmt, price in vinyl_formats:
            items.append({
                "artist": artist,
                "title": f"{title} — {fmt}",
                "format": "Vinyl",
                "price": price,
                "currency": "USD",
                "url": url,
                "cover_image_url": cover_image_url,
            })
        return items

    @staticmethod
    def _price(raw: str) -> Optional[float]:
        try:
            return float(raw.replace(',', ''))
        except ValueError:
            return None
