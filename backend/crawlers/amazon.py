import asyncio
import random
import re
from logging_config import get_logger
from crawler import BotDetectedError, clean_search_text

log = get_logger("crawlers.amazon")

_VERSION = "v5-format-aware"

# Map Discogs format strings to Amazon format link keywords (case-insensitive contains match)
_FORMAT_MAP = {
    "vinyl":     ["vinyl"],
    "cd":        ["audio cd", "cd"],
    "cassette":  ["cassette", "audio cassette"],
    "blu-ray":   ["blu-ray"],
    "dvd":       ["dvd"],
    "box set":   ["box set"],
}

def _amazon_format_keywords(discogs_format: str) -> list[str]:
    key = discogs_format.lower().strip()
    for k, keywords in _FORMAT_MAP.items():
        if k in key:
            return keywords
    return [key]  # fall back to the raw format string

_BOT_SELECTORS = [
    "input[value='Continue shopping']",
    "button:has-text('Continue shopping')",
    "input[value='Continue Shopping']",
    "form[action*='errors/validateCaptcha']",
    "h4:has-text('Enter the characters you see below')",
]

async def _bot_interstitial(page) -> bool:
    for sel in _BOT_SELECTORS:
        try:
            if await page.locator(sel).count():
                log.warning("[Amazon] bot interstitial detected via %r", sel)
                return True
        except Exception:
            pass
    return False


_STOP_WORDS = frozenset({
    "a", "an", "the", "of", "in", "on", "at", "to", "for",
    "and", "or", "but", "with", "from", "by", "as", "is",
})


def _strip_stop_words(text: str) -> str:
    """Remove stop words from a search token, collapsing remaining words."""
    words = text.split()
    meaningful = [w for w in words if w.lower() not in _STOP_WORDS]
    return " ".join(meaningful) if meaningful else text


def _title_variants(title: str) -> list[str]:
    """Return [title] when short; otherwise [title, shortened] for a retry."""
    words = title.split()
    if len(words) <= 5:
        return [title]
    meaningful = [w for w in words if w.lower() not in _STOP_WORDS]
    short = " ".join(meaningful[:3]) if meaningful else " ".join(words[:3])
    return [title, short]


async def extract_price(page, fmt_keywords: list[str]):
    """Extract a product price from an already-loaded Amazon product page.

    Scoped to known buybox containers to avoid picking up carousel prices.
    Returns a float or None.
    """
    price = None

    # Primary: scoped offscreen price spans (screen-reader text Amazon injects next to prices).
    # Never use bare ".a-price .a-offscreen" — it matches recommendation carousel prices too.
    for selector in (
        "#corePrice_feature_div .a-offscreen",
        "#unifiedPrice_feature_div .a-offscreen",
        "#apex_offerDisplay_desktop .a-offscreen",
        "#priceblock_ourprice",
        "#priceblock_dealprice",
        "#desktop_buybox .a-offscreen",
    ):
        el = page.locator(selector).first
        if await el.count():
            raw = await el.inner_text()
            cleaned = raw.replace("$", "").replace(",", "").strip()
            log.debug("[Amazon] price selector %r → raw=%r cleaned=%r", selector, raw, cleaned)
            try:
                price = float(cleaned.split()[0])
                log.debug("[Amazon] price parsed: %s", price)
                break
            except (ValueError, IndexError) as e:
                log.debug("[Amazon] price parse failed for %r: %s", cleaned, e)
                continue

    # Fallback: split spans scoped to buybox (avoids carousel .a-price-whole elements)
    if price is None:
        try:
            for scope in ("#corePrice_feature_div", "#unifiedPrice_feature_div", "#desktop_buybox"):
                whole_el = page.locator(f"{scope} .a-price-whole").first
                frac_el  = page.locator(f"{scope} .a-price-fraction").first
                if await whole_el.count() and await frac_el.count():
                    whole = (await whole_el.inner_text()).replace(",", "").replace(".", "").strip()
                    frac  = (await frac_el.inner_text()).strip()
                    price = float(f"{whole}.{frac}")
                    log.debug("[Amazon] price from split spans (%s): %s", scope, price)
                    break
        except Exception as e:
            log.info("[Amazon] split-span price failed: %s", e)

    # Fallback: format-selector buttons with aria-label containing format keyword + price.
    # Guards against picking up the CD button when looking for Vinyl, etc.
    if price is None:
        try:
            for btn in await page.locator("a.a-button-text[id^='a-autoid']").all():
                label = await btn.get_attribute("aria-label") or ""
                if not label:
                    span = btn.locator("span[aria-label]").first
                    if await span.count():
                        label = await span.get_attribute("aria-label") or ""
                label_lower = label.lower()
                if not any(kw in label_lower for kw in fmt_keywords):
                    continue
                m = re.search(r'\$([0-9,]+\.?\d*)', label)
                if m:
                    price = float(m.group(1).replace(",", ""))
                    log.info("[Amazon] price from aria-label button %r: %s", label.strip(), price)
                    break
        except Exception as e:
            log.info("[Amazon] aria-label button price failed: %s", e)

    return price


class Crawler:
    site_name: str = "Amazon"
    base_url: str = "https://www.amazon.com"
    login_url: str = "https://www.amazon.com/ap/signin?openid.pape.max_auth_age=0&openid.return_to=https%3A%2F%2Fwww.amazon.com%2F&openid.identity=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select&openid.assoc_handle=usflex&openid.mode=checkid_setup&openid.claimed_id=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select&openid.ns=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0"

    @staticmethod
    def _artist(release: dict) -> str:
        raw = clean_search_text(release.get("artist", ""))
        if not raw or raw.lower() == "various":
            return ""
        return _strip_stop_words(raw)

    @classmethod
    def search_url(cls, release: dict) -> str:
        artist = cls._artist(release)
        title = clean_search_text(release.get("title", ""))
        fmt = release.get("format", "vinyl") or "vinyl"
        query = f"{artist} {title} {fmt}".strip().replace(" ", "+")
        return f"https://www.amazon.com/s?k={query}&i=popular"

    async def search(self, release: dict, page) -> list[dict]:
        artist = self._artist(release)
        title = clean_search_text(release.get("title", ""))
        fmt = release.get("format", "vinyl") or "vinyl"
        fmt_keywords = _amazon_format_keywords(fmt)
        sc = release.get("_screenshotter")
        log.debug("[Amazon] %s — searching for: %s %s [format: %s]", _VERSION, artist, title, fmt)

        vinyl_url = None
        vinyl_price = None

        # Try the full title first; if nothing matched, retry with a shortened title.
        # Long titles (> 5 words) often return zero results on Amazon.
        title_variants = _title_variants(title)
        for attempt, title_attempt in enumerate(title_variants):
            if attempt > 0:
                log.debug("[Amazon] retrying with shortened title: %r", title_attempt)
            query = f"{artist}+{title_attempt}+{fmt}".strip("+").replace(" ", "+")
            url = f"https://www.amazon.com/s?k={query}&i=popular"
            await page.goto(url, wait_until="domcontentloaded")
            await asyncio.sleep(random.uniform(1, 2))

            if await _bot_interstitial(page):
                raise BotDetectedError("interstitial on search results page")

            try:
                items = await page.locator('[data-component-type="s-search-result"]').all()
                log.debug("[Amazon] Found %d result items", len(items))
                for i, item in enumerate(items[:10]):  # noqa
                    try:
                        # Amazon shows format as a bold link inside [data-cy="price-recipe"]:
                        #   <a class="...a-text-bold" href="/dp/...">Vinyl</a>
                        format_link = item.locator('[data-cy="price-recipe"] a.a-text-bold').first
                        count = await format_link.count()
                        if not count:
                            log.debug("[Amazon] item %d: no price-recipe format link", i)
                            continue
                        format_text = await format_link.inner_text()
                        log.debug("[Amazon] item %d: format link text=%r", i, format_text)
                        if not any(kw in format_text.lower() for kw in fmt_keywords):
                            continue

                        # Verify the result title matches artist or album title
                        try:
                            title_el = item.locator("h2").first
                            item_title = (await title_el.inner_text()).lower() if await title_el.count() else ""
                        except Exception:
                            item_title = ""
                        artist_match = artist.lower().split()[0] in item_title if artist else True
                        title_match = title.lower().split()[0] in item_title if title else True
                        if not (artist_match or title_match):
                            log.debug("[Amazon] item %d: title mismatch %r", i, item_title[:60])
                            continue
                        log.debug("[Amazon] item %d: title match %r", i, item_title[:60])

                        href = await format_link.get_attribute("href")
                        if not href:
                            continue
                        vinyl_url = f"https://www.amazon.com{href}" if href.startswith("/") else href
                        break
                    except Exception:
                        continue
            except Exception:
                pass

            if vinyl_url:
                break

        if not vinyl_url:
            await page.goto("about:blank")
            return []

        # Step 2: navigate to vinyl product page, get price there (avoids CD/Vinyl price mismatch)
        try:
            await page.goto(vinyl_url, wait_until="domcontentloaded")
            await asyncio.sleep(random.uniform(1, 2))
            vinyl_url = page.url  # use the final URL after any redirects

            if await _bot_interstitial(page):
                raise BotDetectedError("interstitial on product page")

            try:
                await page.wait_for_selector(".a-price", timeout=8000)
            except Exception:
                pass

            vinyl_price = await extract_price(page, fmt_keywords)

            if vinyl_price is None:
                log.warning("[Amazon] no price found on product page: %s", vinyl_url)

        except Exception as e:
            log.warning("[Amazon] product page error: %s", e)

        await page.goto("about:blank")
        return [{
            "url": vinyl_url,
            "price": vinyl_price,
            "shipping": None,
            "currency": "USD",
            "condition": None,
        }]
