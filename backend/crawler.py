import ast
import importlib.util
import asyncio
import random
import re
from pathlib import Path

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


async def _new_context(browser, stealth):
    context = await browser.new_context(
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
    page = await context.new_page()
    await stealth.apply_stealth_async(page)
    return context, page


async def _reset_context(context, browser, stealth, screenshotter):
    log.warning("Bot detected — resetting browser context")
    await context.close()
    await asyncio.sleep(random.uniform(3.0, 6.0))
    context, page = await _new_context(browser, stealth)
    if screenshotter:
        screenshotter.detach()
        screenshotter._page = page
        screenshotter.attach()
    return context, page
