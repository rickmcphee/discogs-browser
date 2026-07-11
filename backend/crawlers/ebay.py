import urllib.parse
from config import load_config
from crawler import clean_search_text
from ebay_api import search_ebay

CCMUSIC_SELLER = "collectorschoicemusic"


class Crawler:
    site_name: str = "CC Music/eBay"
    base_url: str = f"https://www.ebay.com/str/{CCMUSIC_SELLER}"

    @classmethod
    def search_url(cls, release: dict) -> str:
        artist = clean_search_text(release.get("artist", ""))
        title = clean_search_text(release.get("title", ""))
        query = urllib.parse.quote_plus(f"{artist} {title}")
        return f"https://www.ebay.com/sch/{CCMUSIC_SELLER}/i.html?_nkw={query}"

    async def search(self, release: dict, page) -> list[dict]:
        cfg = load_config()
        return await search_ebay(
            release,
            cfg.get("ebay_app_id", ""),
            cfg.get("ebay_cert_id", ""),
            seller=CCMUSIC_SELLER,
            limit=3,
            log_prefix="CC Music/eBay",
            fallback_url=self.search_url(release),
        )
