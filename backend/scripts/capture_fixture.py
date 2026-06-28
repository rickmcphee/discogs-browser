"""
Capture a rendered page fixture for regression testing.

Usage (from backend/):
    python scripts/capture_fixture.py amazon https://www.amazon.com/dp/... "artist - title"
    python scripts/capture_fixture.py ccmusic https://... "artist - title"

The page is opened using the same Playwright context as the crawler (persistent
Chrome profile, stealth, real cookies) so the captured DOM matches what the
scraper actually sees.  Output goes to tests/fixtures/crawlers/<crawler>/<slug>.html
"""

import asyncio
import re
import sys
from pathlib import Path

# Make backend modules importable when run from backend/
sys.path.insert(0, str(Path(__file__).parent.parent))

from crawler import CHROME_PROFILE_DIR, BROWSER_STATE_FILE
from config import PLAYWRIGHT_CHANNEL
from logging_config import setup_logging

setup_logging()


def _slug(label: str) -> str:
    s = re.sub(r"[^\w\s-]", "", label.lower())
    s = re.sub(r"[\s-]+", "_", s).strip("_")
    return s[:60]


async def capture(crawler_name: str, url: str, label: str):
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth
    import json

    out_dir = Path(__file__).parent.parent / "tests" / "fixtures" / "crawlers" / crawler_name
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{_slug(label)}.html"

    stealth = Stealth()

    async with async_playwright() as pw:
        CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        context = await pw.chromium.launch_persistent_context(
            str(CHROME_PROFILE_DIR),
            headless=True,
            channel=PLAYWRIGHT_CHANNEL,
            args=["--disable-blink-features=AutomationControlled"],
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"macOS"',
            },
        )

        if BROWSER_STATE_FILE.exists():
            state = json.loads(BROWSER_STATE_FILE.read_text())
            if state.get("cookies"):
                await context.add_cookies(state["cookies"])
                print(f"Loaded {len(state['cookies'])} cookies from browser state")

        page = await context.new_page()
        await stealth.apply_stealth_async(page)

        print(f"Navigating to {url} ...")
        await page.goto(url, wait_until="domcontentloaded")

        # Wait for price elements to appear (best-effort)
        for selector in (".a-price", "#priceblock_ourprice", ".a-price-whole"):
            try:
                await page.wait_for_selector(selector, timeout=6000)
                break
            except Exception:
                continue

        html = await page.content()
        out_file.write_text(html, encoding="utf-8")
        print(f"Saved {len(html):,} chars → {out_file}")

        await context.close()


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    asyncio.run(capture(sys.argv[1], sys.argv[2], sys.argv[3]))
