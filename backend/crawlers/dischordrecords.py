import re
from typing import AsyncIterator

_PAGE_LINK_RE = re.compile(r'/label/dischord\?page=(\d+)')
_RELEASE_LINK_RE = re.compile(r'href="(/release/[^"]+)"')


class Crawler:
    site_name: str = "Dischord Records"
    base_url: str = "https://dischord.com"
    genre_summary: str = (
        "Ian MacKaye and Jeff Nelson's DC hardcore/punk label -- Minor Threat, "
        "Fugazi, and the rest of the Dischord catalog, sold direct."
    )
    genre: str = "punk"
    crawler_type: str = "catalog"

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
