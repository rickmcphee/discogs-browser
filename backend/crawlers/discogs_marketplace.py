import re
import urllib.parse
from typing import Optional
from crawler import BotDetectedError
from logging_config import get_logger

log = get_logger("crawlers.discogs_marketplace")

_AMOUNT_RE = re.compile(r"[\d,]+\.\d{2}")


def _parse_amount(text: str) -> Optional[float]:
    if not text:
        return None
    match = _AMOUNT_RE.search(text.replace(",", ""))
    return float(match.group()) if match else None


class Crawler:
    site_name: str = "Discogs"
    base_url: str = "https://www.discogs.com"
    login_url: str = ""

    @classmethod
    def search_url(cls, release: dict) -> str:
        release_id = release["discogs_id"][1:]
        query = urllib.parse.urlencode({"ships_from": "United States", "sort": "price,asc"})
        return f"https://www.discogs.com/sell/release/{release_id}?{query}"

    async def search(self, release: dict, page) -> list[dict]:
        url = self.search_url(release)
        await page.goto(url, wait_until="domcontentloaded")

        title = await page.title()
        if "just a moment" in title.lower():
            log.warning("[Discogs] bot interstitial detected for release %s", release.get("discogs_id"))
            raise BotDetectedError()

        rows = page.locator("#pjax_container table tbody tr")
        if await rows.count() == 0:
            log.info("[Discogs] no USA-shipping listings for release %s", release.get("discogs_id"))
            return []

        row = rows.first
        price_el = row.locator("td.item_price .price")
        shipping_el = row.locator("td.item_price .item_shipping")
        condition_el = row.locator("td.item_description .item_condition")

        price = None
        currency = None
        if await price_el.count():
            currency = await price_el.get_attribute("data-currency")
            price_attr = await price_el.get_attribute("data-pricevalue")
            price = float(price_attr) if price_attr else _parse_amount(await price_el.inner_text())

        shipping = _parse_amount(await shipping_el.inner_text()) if await shipping_el.count() else None
        condition = (await condition_el.inner_text()).strip() if await condition_el.count() else None

        return [{
            "url": url,
            "price": price,
            "shipping": shipping,
            "currency": currency,
            "condition": condition,
        }]
