import random
import re
from asyncio import sleep
from typing import AsyncIterator, Optional

from config import load_config
from crawler import BotDetectedError
from logging_config import get_logger

log = get_logger("amoeba")

# LP=3, 12"=4, 7"=17, 10"=19, 78=21. CD=1 and Cassette=24 are out of scope.
_VINYL_FORMAT_IDS = (3, 4, 17, 19, 21)

# 200 is the largest size the site's own #show-per-page control offers. Larger
# values are honoured server-side but are outside that contract -- if one were
# ever clamped, a single show=1000 request would silently yield 200 items
# instead of 1000 rather than failing.
_PAGE_SIZE = 200
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
