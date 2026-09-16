import ast
import importlib.util
import asyncio
import random
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from logging_config import get_logger

log = get_logger("crawler")


class BotDetectedError(Exception):
    """Raised by a crawler when it detects an anti-bot interstitial."""


# Rejected before parsing rather than after, because urlparse and a browser
# disagree about these and it is the browser that ultimately fetches the URL.
# Backslash is the sharp one: WHATWG treats "\" as "/" in a special scheme, so
# "https://evil.example\@www.ebay.com/itm/1" is host evil.example to a browser
# while urlparse reads "evil.example\" as userinfo and answers www.ebay.com --
# which turns a hostname allowlist into a redirect to anywhere. C0 controls,
# DEL and space go with it: a browser strips or rejects them, and which of
# those Python does has changed across releases, so the host a URL resolves to
# would otherwise depend on the interpreter.
_PARSER_DIFFERENTIAL_RE = re.compile(r"[\\\x00-\x20\x7f]")


def https_host(url) -> Optional[str]:
    """The hostname of `url` when it is a well-formed https URL, else None.

    Two hazards in one guard, both reachable from values a crawler reads off a
    site or an API and hands to the browser as a link or an <img src>:

    urlparse *raises* on a malformed authority -- "https://[" is an
    unterminated IPv6 literal -- so a caller that parses inline aborts the
    whole crawl over one bad string. On the stock-item path that reads to the
    consecutive-failure breaker as the site being down.

    A URL can also parse *differently* here than in the browser that will
    fetch it, which for the eBay link is an allowlist bypass -- see
    `_PARSER_DIFFERENTIAL_RE` above.

    And a prefix test passes "https:///x.jpg", which has no hostname and is
    neither a link nor a picture -- as does "https://h:not-a-port/x.jpg",
    whose port urlparse does not check until asked. Neither is merely
    useless: a listing image is *preferred* over the target's own cover, so
    a value the browser cannot load wins over good art and renders a broken
    thumbnail.

    Answering None for both lets every caller fall back, which is what each of
    them already has in hand.
    """
    if not isinstance(url, str) or not url:
        return None
    if _PARSER_DIFFERENTIAL_RE.search(url):
        return None
    try:
        parsed = urlparse(url)
        # .port is a lazily-parsed property rather than something urlparse
        # checked: "https://h:not-a-port/x.jpg" splits with a clean scheme and
        # hostname and only raises when the port is read. Read it here, inside
        # the guard, so a URL is either fully well-formed or rejected --
        # otherwise a caller gets a hostname off a parse that was never
        # finished and stores a URL no browser can load.
        _port = parsed.port
    except ValueError:
        return None
    return parsed.hostname if parsed.scheme == "https" else None


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
