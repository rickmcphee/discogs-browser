import random
import re
from asyncio import sleep
from typing import AsyncIterator, Optional

from config import load_config
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
# detectable: Task 7's len(rows) < _PAGE_SIZE warning relies on that gap to
# fail loudly instead of silently under-collecting.
_PAGE_SIZE = 200

# _PAGE_SIZE * _WINDOW_PAGES == 1000, the window size the design spec chose.
# Raising either constant on its own breaks that relationship -- not a cheap win.
_WINDOW_PAGES = 5


class Crawler:
    site_name: str = "Amoeba Music"
    base_url: str = "https://www.amoeba.com"
    crawler_type: str = "catalog_browser"

    @classmethod
    def _listing_url(cls, page_num: int) -> str:
        formats = "".join(f"&format%5B{i}%5D={i}" for i in _VINYL_FORMAT_IDS)
        return (
            f"/ajax/cds_and_vinyl.php?page={page_num}&show={_PAGE_SIZE}"
            f"&order=date&direction=desc{formats}"
        )
