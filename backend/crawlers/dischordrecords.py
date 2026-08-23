import html
import random
import re
from asyncio import sleep
from typing import AsyncIterator, Optional

import httpx

from config import load_config
from crawl_progress import report_page

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
_BUTTON_RE = re.compile(
    r'<a rel="nofollow" data-method="post" href="/cart/add/\d+">(?P<text>[^<]+)</a>'
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

            total_yielded = 0
            for page in range(1, total_pages + 1):
                if page > 1:
                    await sleep(random.uniform(delay * 0.5, delay))
                    r = await client.get(_LABEL_PATH, params={"page": page})
                    r.raise_for_status()
                    page_html = r.text

                hrefs = self._release_hrefs(page_html)
                if not hrefs:
                    raise RuntimeError(f"no release links found on {_LABEL_PATH}?page={page} -- markup drift")

                page_items = []
                for href in hrefs:
                    await sleep(random.uniform(delay * 0.5, delay))
                    r = await client.get(href)
                    r.raise_for_status()
                    page_items.extend(self._parse_release(r.text, href))

                await report_page(page, len(page_items))
                total_yielded += len(page_items)
                for item in page_items:
                    yield item

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
        multi_edition = len(vinyl_formats) > 1
        items = []
        for fmt, price in vinyl_formats:
            items.append({
                "artist": artist,
                "title": f"{title} — {fmt}" if multi_edition else title,
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
