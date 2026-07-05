import ast
import importlib.util
import asyncio
import random
import re
from pathlib import Path
from typing import AsyncIterator

from config import CRAWLERS_DIR, CONFIG_DIR, load_config, PLAYWRIGHT_CHANNEL

BROWSER_STATE_FILE = CONFIG_DIR / "browser_state.json"
CHROME_PROFILE_DIR = CONFIG_DIR / "chrome_profile"
from logging_config import get_logger

log = get_logger("crawler")


class BotDetectedError(Exception):
    """Raised by a crawler when it detects an anti-bot interstitial."""


def clean_search_text(text: str) -> str:
    """Strip Discogs disambiguation suffixes and URL-unsafe characters from search strings."""
    text = re.sub(r'\s*\(\d+\)\s*$', '', text)  # remove trailing (2), (3), etc.
    text = re.sub(r'[?#&=+%:]', ' ', text)        # remove URL-special chars
    text = re.sub(r'\s+', ' ', text)              # collapse whitespace
    return text.strip()


_STOP_WORDS = frozenset({
    "a", "an", "the", "of", "in", "on", "at", "to", "for",
    "and", "or", "but", "with", "from", "by", "as", "is",
})


def strip_stop_words(text: str) -> str:
    words = text.split()
    meaningful = [w for w in words if w.lower() not in _STOP_WORDS]
    return " ".join(meaningful) if meaningful else text


def title_variants(title: str) -> list:
    """Return [title] when short; otherwise [title, shortened] for a retry."""
    words = title.split()
    if len(words) <= 5:
        return [title]
    meaningful = [w for w in words if w.lower() not in _STOP_WORDS]
    short = " ".join(meaningful[:3]) if meaningful else " ".join(words[:3])
    return [title, short]


def validate_crawler_code(code: str) -> bool:
    # Only checks for the release-crawler interface (async search()); doesn't
    # know about the catalog crawler_type (async crawl_catalog()). Fine while
    # discover.py's caller is unregistered — see the note there.
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Crawler":
            for item in node.body:
                if isinstance(item, ast.AsyncFunctionDef) and item.name == "search":
                    return True
    return False


def load_crawler_from_path(path: Path):
    spec = importlib.util.spec_from_file_location(f"crawler_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Crawler()


def load_enabled_crawlers(enabled_crawlers: list[dict]) -> list:
    loaded = []
    for row in enabled_crawlers:
        path = Path(row["module_path"])
        if not path.exists():
            log.warning("Crawler module not found: %s", path)
            continue
        try:
            crawler = load_crawler_from_path(path)
            crawler._db_id = row["id"]
            crawler._db_site_name = row["site_name"]
            loaded.append(crawler)
            log.info("Loaded crawler: %s", row["site_name"])
        except Exception as e:
            log.error("Failed to load crawler %s: %s", row["site_name"], e)
    return loaded


async def _new_context(pw, stealth):
    import json
    CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    log.debug("Launching Chrome with persistent profile: %s", CHROME_PROFILE_DIR)
    context = await pw.chromium.launch_persistent_context(
        str(CHROME_PROFILE_DIR),
        headless=True,
        channel=PLAYWRIGHT_CHANNEL,
        args=["--disable-blink-features=AutomationControlled"],
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36",
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
    # Merge in any saved login session cookies
    if BROWSER_STATE_FILE.exists():
        try:
            state = json.loads(BROWSER_STATE_FILE.read_text())
            if state.get("cookies"):
                await context.add_cookies(state["cookies"])
                log.info("Loaded %d cookies from browser state", len(state["cookies"]))
        except Exception as e:
            log.warning("Could not load browser state cookies: %s", e)
    page = await context.new_page()
    await stealth.apply_stealth_async(page)
    return context, page


async def _reset_context(context, pw, stealth, screenshotter):
    log.warning("Bot detected — resetting browser context and clearing session state")
    if BROWSER_STATE_FILE.exists():
        BROWSER_STATE_FILE.unlink()
        log.info("Cleared browser state: %s", BROWSER_STATE_FILE)
    await context.close()
    await asyncio.sleep(random.uniform(3.0, 6.0))
    context, page = await _new_context(pw, stealth)
    if screenshotter:
        screenshotter.detach()
        screenshotter._page = page
        screenshotter.attach()
    return context, page


async def crawl_releases(releases: list[dict], crawlers: list, conn, single: bool = False) -> AsyncIterator[dict]:
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth
    from db import upsert_listing, update_crawler_last_run, delete_listings_for_release
    from screenshots import CrawlScreenshotter, new_session_dir

    stealth = Stealth()
    cfg = load_config()
    raw = cfg.get("debug_screenshot_interval")
    configured_interval = int(raw) if raw is not None else 20
    screenshot_interval = 1 if single else configured_interval
    bulk_delay = 1.0 if single else float(cfg.get("crawl_delay_seconds", 30))
    shuffle_on = cfg.get("shuffle_crawl_order", True) and not single
    failure_limit = int(cfg.get("consecutive_failure_limit", 10)) if shuffle_on else 0
    consecutive_failures = 0

    session_dir = new_session_dir() if screenshot_interval > 0 else None

    log.info("Starting crawl: %d releases × %d crawlers", len(releases), len(crawlers))
    if screenshot_interval > 0:
        log.debug("Screenshots enabled: interval=%d, session=%s", screenshot_interval, session_dir.name)

    async with async_playwright() as pw:
        context, page = await _new_context(pw, stealth)

        screenshotter = None
        if screenshot_interval > 0:
            screenshotter = CrawlScreenshotter(page, session_dir, screenshot_interval)
            screenshotter.attach()

        for release in releases:
            label = f"{release['artist']} — {release['title']}"
            log.info("Searching all sites for: %s", label)
            # Clear this release's existing listings before re-searching, so a
            # crawler that no longer finds a match correctly reports "not found"
            # instead of leaving the previous crawl's stale price in place.
            # Applies uniformly to bulk crawls and single-item refreshes alike.
            delete_listings_for_release(conn, release["discogs_id"])
            for crawler in crawlers:
                await asyncio.sleep(random.uniform(bulk_delay * 0.5, bulk_delay))
                log.info("[%s] Searching: %s", crawler._db_site_name, label)

                if screenshotter:
                    screenshotter.start_search(
                        f"{release['artist']} {release['title']}",
                        crawler._db_site_name,
                    )

                crawl_release = {**release, "_screenshotter": screenshotter}
                retried = False
                while True:
                    try:
                        matches = await crawler.search(crawl_release, page)
                        screenshots = screenshotter.get_search_screenshots() if screenshotter else []
                        if matches:
                            for match in matches:
                                upsert_listing(conn, release["discogs_id"], crawler._db_id, match)
                            price = matches[0].get("price")
                            log.info("[%s] Found %d match(es) for %s — price: %s",
                                     crawler._db_site_name, len(matches), label, price)
                            consecutive_failures = 0
                            yield {
                                "discogs_id": release["discogs_id"],
                                "release": release["title"],
                                "artist": release["artist"],
                                "site": crawler._db_site_name,
                                "status": "found",
                                "price": price,
                                "screenshots": screenshots,
                            }
                        else:
                            log.info("[%s] Not found: %s", crawler._db_site_name, label)
                            consecutive_failures += 1
                            yield {
                                "discogs_id": release["discogs_id"],
                                "release": release["title"],
                                "artist": release["artist"],
                                "site": crawler._db_site_name,
                                "status": "not_found",
                                "screenshots": screenshots,
                            }
                        break
                    except BotDetectedError:
                        if retried:
                            log.error("[%s] Bot detection persists after reset, skipping %s",
                                      crawler._db_site_name, label)
                            consecutive_failures += 1
                            yield {
                                "discogs_id": release["discogs_id"],
                                "release": release["title"],
                                "artist": release["artist"],
                                "site": crawler._db_site_name,
                                "status": "error",
                                "error": "bot detection",
                                "screenshots": [],
                            }
                            break
                        context, page = await _reset_context(context, pw, stealth, screenshotter)
                        crawl_release = {**release, "_screenshotter": screenshotter}
                        retried = True
                        if screenshotter:
                            screenshotter.start_search(
                                f"{release['artist']} {release['title']}",
                                crawler._db_site_name,
                            )
                    except Exception as e:
                        log.error("[%s] Error searching %s: %s", crawler._db_site_name, label, e, exc_info=True)
                        screenshots = screenshotter.get_search_screenshots() if screenshotter else []
                        consecutive_failures += 1
                        yield {
                            "discogs_id": release["discogs_id"],
                            "release": release["title"],
                            "artist": release["artist"],
                            "site": crawler._db_site_name,
                            "status": "error",
                            "error": str(e),
                            "screenshots": screenshots,
                        }
                        break
                update_crawler_last_run(conn, crawler._db_id)

                if failure_limit and consecutive_failures >= failure_limit:
                    log.warning(
                        "%d consecutive failures — likely bot detection, stopping crawl",
                        consecutive_failures,
                    )
                    yield {
                        "status": "error",
                        "error": f"Stopped after {consecutive_failures} consecutive failures — possible bot detection",
                    }
                    return

        if screenshotter:
            screenshotter.detach()
            screenshotter.save_manifest()

        await context.storage_state(path=str(BROWSER_STATE_FILE))
        log.info("Browser state saved to %s", BROWSER_STATE_FILE)

        await context.close()
        log.info("Crawl complete")
