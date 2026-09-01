import random
from asyncio import sleep

import httpx

from logging_config import get_logger

log = get_logger("catalog_http")

# httpx's default is 5s for every phase, and one slow TLS handshake past it
# cost Dark Descent an entire catalog crawl (2026-09-01). Catalog crawls are
# paced in tens of seconds per request, so a generous bound loses nothing.
REQUEST_TIMEOUT = httpx.Timeout(30.0)


async def get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    delay: float,
    failure_limit: int,
    params=None,
    headers=None,
    allow_404: bool = False,
) -> httpx.Response:
    """Paced GET with the catalog-crawl retry budget, shared by every
    httpx-based catalog crawler.

    Catalog pagination has no next item to fall through to the way
    crawl_releases() does, and _sync_stock discards a catalog crawl's entire
    partial result when it raises — so a transient failure is retried, paced,
    up to `failure_limit` consecutive attempts before the error propagates.
    A limit of 0 means "disabled" elsewhere, but disabled must mean fail fast
    here, not unlimited retries.

    A 429 raises on first sight, uncounted and never retried: empirically
    (see stock-sync-429-followup investigation notes) platform-edge IP
    throttles do not clear within any retry window this budget could pace,
    and _sync_stock's own 2-consecutive-429-sites abort wants the signal
    immediately. The response headers are logged at DEBUG — the only way to
    see what signal, if any, accompanies an undocumented throttle.

    `allow_404=True` returns the 404 response instead of raising, for sites
    where a dead detail link is expected and skipped rather than an error.
    """
    consecutive_failures = 0
    while True:
        await sleep(random.uniform(delay * 0.5, delay))
        try:
            r = await client.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
            if allow_404 and r.status_code == 404:
                return r
            r.raise_for_status()
            return r
        except httpx.HTTPError as e:
            if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 429:
                log.debug("[%s] 429 response headers: %s", url, dict(e.response.headers))
                raise
            consecutive_failures += 1
            if failure_limit <= 0 or consecutive_failures >= failure_limit:
                raise
