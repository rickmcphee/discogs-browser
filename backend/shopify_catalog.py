import random
from asyncio import sleep
from typing import AsyncIterator, Optional
import httpx
from config import load_config
from crawl_progress import report_page
from logging_config import get_logger

log = get_logger("shopify_catalog")

_PAGE_LIMIT = 250

# Shopify's storefront products.json refuses `page` past 100 with an HTTP 400,
# whatever `limit` says, so this endpoint reaches 25,000 products and no more.
# Confirmed live against waterloorecords.com/collections/vinyl-lps (36,138
# products): pages 1-100 each return a full 250, page 101 returns 400 and every
# page above it does too. There is no cursor alternative to fall back on -- the
# storefront endpoint sends no Link header, unlike the Admin API.
_MAX_PAGE = 100


async def iter_products(base_url: str, collection_slug: str) -> AsyncIterator[dict]:
    """Paginate a Shopify collection's public products.json endpoint until exhausted.

    Reuses the crawl_delay_seconds / consecutive_failure_limit settings crawl_releases()
    applies to release search requests, extended here with retry-on-failure: unlike
    crawl_releases(), which just moves on to the next release/crawler pair, pagination
    has no next item to fall through to, so a non-429 failed page is retried instead.

    Pagination has a hard ceiling: Shopify refuses `page` past _MAX_PAGE with an
    HTTP 400, so a collection larger than _MAX_PAGE * _PAGE_LIMIT can only be walked
    in part. That 400 ends the walk rather than entering the retry budget, and the
    ceiling itself is stopped at before page _MAX_PAGE + 1 is ever requested -- both
    so the pages that did answer survive, since _sync_stock discards a catalog
    crawl's entire result when it raises.

    A 429 is never retried, regardless of consecutive_failure_limit or any Retry-After
    header value: confirmed empirically (see stock-sync-429-followup investigation notes)
    that Shopify's shared platform-edge IP throttle does not clear within a
    Retry-After-paced retry window -- retrying just spends consecutive_failure_limit's
    entire budget (up to ~10 minutes) before giving up anyway. Raising immediately lets
    the caller (_sync_stock) move on, or abort the run via its own 2-consecutive-429
    circuit breaker, much sooner.
    """
    cfg = load_config()
    delay = float(cfg.get("crawl_delay_seconds", 30))
    failure_limit = int(cfg.get("consecutive_failure_limit", 10))
    consecutive_failures = 0

    page = 1
    async with httpx.AsyncClient() as client:
        while True:
            url = f"{base_url}/collections/{collection_slug}/products.json"
            await sleep(random.uniform(delay * 0.5, delay))
            try:
                r = await client.get(url, params={"limit": _PAGE_LIMIT, "page": page})
                r.raise_for_status()
            except httpx.HTTPError as e:
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 429:
                    # Full header dump -- Shopify's shared platform-edge IP throttle is
                    # not publicly documented, so this is the only way to see what
                    # signal (if any) accompanies it. Not acted on: see docstring above.
                    log.debug("[%s] 429 response headers: %s", base_url, dict(e.response.headers))
                    raise
                # A 400 past the first page is this endpoint's pagination ceiling
                # rather than a fault, and must end the walk instead of entering the
                # retry budget below. It is deterministic, so those retries can only
                # spend consecutive_failure_limit paced requests and then raise --
                # and raising discards every product already collected, because
                # _sync_stock skips replace_stock_items entirely when a catalog
                # crawl raises. That is how a store larger than _MAX_PAGE * _PAGE_LIMIT
                # reported a full run of healthy page-fetch logs and then wrote no
                # rows at all. Kept off page 1 so a renamed or misspelled collection
                # slug, which 400s on the very first request, still raises rather
                # than quietly reporting the store as empty and wiping its snapshot.
                if (
                    isinstance(e, httpx.HTTPStatusError)
                    and e.response.status_code == 400
                    and page > 1
                ):
                    log.info(
                        "[%s] %s: pagination ended at page %d (HTTP 400) -- keeping "
                        "the %d pages already fetched",
                        base_url, collection_slug, page, page - 1,
                    )
                    break
                consecutive_failures += 1
                # A limit of 0 means "disabled" elsewhere, but disabled must mean
                # fail fast here, not unlimited retries — this loop has no next
                # item to move on to like crawl_releases() does.
                if failure_limit <= 0 or consecutive_failures >= failure_limit:
                    raise
                continue
            consecutive_failures = 0
            products = r.json().get("products", [])
            if not products:
                break
            # No log line here: _run_catalog_crawler logs every reported page
            # centrally, for every catalog crawler rather than only the
            # Shopify-backed ones, and names the store by its `site_name`
            # instead of by base_url. Keeping this one too put two
            # near-identical rows in app_logs for every Shopify listing page,
            # which is the opposite of what that centralisation was for.
            await report_page(page, len(products))
            for product in products:
                yield product
            if page >= _MAX_PAGE:
                # A short final page means the collection genuinely ran out on the
                # ceiling page, so there is nothing to warn about.
                if len(products) == _PAGE_LIMIT:
                    # INFO rather than WARNING on the same reasoning as _sync_stock's
                    # swept-rows line: routers/logs.py filters by exact level
                    # membership, so at WARNING this would be invisible to anyone
                    # watching the INFO stream that carries the rest of the crawl.
                    log.info(
                        "[%s] %s truncated at %d products: Shopify's products.json "
                        "stops paginating past page %d, so the rest of this "
                        "collection is unreachable from this endpoint",
                        base_url, collection_slug, _MAX_PAGE * _PAGE_LIMIT, _MAX_PAGE,
                    )
                break
            page += 1


def has_tag(product: dict, tag: str) -> bool:
    """Case-insensitive membership check against a Shopify product's tags array."""
    needle = tag.strip().lower()
    return any((t or "").strip().lower() == needle for t in product.get("tags") or [])


def strip_vendor_prefix(title: str, vendor: str) -> str:
    """Strip a leading "{vendor} - " from a product title, if present; otherwise return it unchanged."""
    vendor = (vendor or "").strip()
    prefix = f"{vendor} - "
    if vendor and title.startswith(prefix):
        return title[len(prefix):]
    return title


def resolve_cover_image(product: dict, variant: dict) -> Optional[str]:
    """Prefer the variant's own image (e.g. a specific vinyl color), falling back to the product's first image."""
    featured = variant.get("featured_image") or {}
    if featured.get("src"):
        return featured["src"]
    images = product.get("images") or []
    return images[0].get("src") if images else None
