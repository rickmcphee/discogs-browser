import asyncio
import random
from playwright.async_api import Page
from logging_config import get_logger
from crawler import clean_search_text

log = get_logger("crawlers.ccmusic")

_VERSION = "v4-playwright-cf"

_RESULT_SELECTORS = [
    ".product-item",
    "li.item",
    ".search-result-item",
    "[class*='product-card']",
]


class Crawler:
    site_name: str = "CC Music"
    base_url: str = "https://www.ccmusic.com"
    login_url: str = "https://www.ccmusic.com/customer/account/login"

    @classmethod
    def search_url(cls, release: dict) -> str:
        artist = clean_search_text(release.get("artist", ""))
        title = clean_search_text(release.get("title", ""))
        query = f"{artist} {title}".replace(" ", "+")
        return f"https://www.ccmusic.com/search?q={query}&mod=AP"

    async def search(self, release: dict, page: Page) -> list[dict]:
        artist = clean_search_text(release.get("artist", ""))
        title = clean_search_text(release.get("title", ""))
        fmt = (release.get("format", "") or "").lower()
        log.info("[CC Music] %s — searching for: %s %s", _VERSION, artist, title)

        url = self.search_url(release)
        await page.goto(url, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(random.uniform(1, 2))
        log.info("[CC Music] landed: %s (title: %r)", page.url, await page.title())

        # Wait for Cloudflare challenge to resolve and product results to appear
        result_selector = ", ".join(_RESULT_SELECTORS)
        try:
            await page.wait_for_selector(result_selector, timeout=15000)
        except Exception:
            log.warning("[CC Music] timed out waiting for result items — may be CF challenge or no results")

        results = []
        for sel in _RESULT_SELECTORS:
            items = await page.locator(sel).all()
            if items:
                log.info("[CC Music] found %d items via selector %r", len(items), sel)
                for i, item in enumerate(items[:10]):
                    try:
                        link_el = item.locator("a").first
                        title_el = item.locator("a, .product-name, h2, h3").first
                        price_el = item.locator("[class*='price'], .price").first

                        item_text = await title_el.inner_text() if await title_el.count() else ""
                        href = await link_el.get_attribute("href") if await link_el.count() else None
                        log.info("[CC Music] item %d: text=%r href=%r", i, item_text[:60], href)

                        if not href:
                            continue

                        is_vinyl = fmt == "vinyl" or not fmt
                        lower = item_text.lower()
                        if is_vinyl and not any(kw in lower for kw in ("vinyl", " lp", "record", "180g", "33rpm", '12"')):
                            log.info("[CC Music] item %d: skipped (no vinyl keyword)", i)
                            continue

                        full_url = f"https://www.ccmusic.com{href}" if href.startswith("/") else href

                        price = None
                        if await price_el.count():
                            raw = await price_el.inner_text()
                            cleaned = raw.replace("$", "").replace(",", "").strip()
                            try:
                                price = float(cleaned.split()[0])
                            except (ValueError, IndexError):
                                pass

                        log.info("[CC Music] item %d: match — url=%s price=%s", i, full_url, price)
                        results.append({
                            "url": full_url,
                            "price": price,
                            "shipping": None,
                            "currency": "USD",
                            "condition": None,
                        })
                    except Exception as e:
                        log.warning("[CC Music] item %d error: %s", i, e)
                break

        if not results:
            log.info("[CC Music] no matching results for %s %s", artist, title)

        await page.goto("about:blank")
        return results
