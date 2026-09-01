# Stock-Crawl Timeout Retry — Design Spec

_2026-09-01_

## Problem

On 2026-09-01 a scheduled stock sync logged:

```
[Dark Descent Records] Stock crawl failed: httpx.ConnectTimeout
```

The traceback bottoms out in `start_tls` — one TLS handshake ran past httpx's
default 5-second connect timeout, once. That single transient fault cost the
whole source's crawl: `_sync_stock` discards a catalog crawl's entire result
when it raises (deliberately — see the 2026-08-02 429-backoff spec), logged the
failure at ERROR, and counted it toward the site's consecutive-failure breaker.

The gap is uneven coverage. `shopify_catalog.iter_products()` grew a
retry-on-failure budget (`consecutive_failure_limit`, paced retries, raise on
the first 429) precisely because catalog pagination has no next item to fall
through to — but that budget only protects the Shopify-backed crawlers that
call it. The catalog crawlers that hand-roll their own httpx pagination
(Dark Descent's WooCommerce Store API, The Sound Garden's category search,
Dischord's Rails storefront, and the Big Cartel trio: Ripple Music, Asbestos
Records, Jetglow Recordings) issue bare `client.get()` calls: no retry, and
httpx's default 5s connect timeout, which one slow handshake exceeds.

## Design

Extract the fetch loop `iter_products()` already runs into a shared helper and
point every httpx-based catalog crawler at it.

### `catalog_http.get_with_retry()` — new module `backend/catalog_http.py`

```python
async def get_with_retry(client, url, *, delay, failure_limit,
                         params=None, headers=None, allow_404=False) -> httpx.Response
```

- Sleeps the jittered pacing delay (`random.uniform(delay * 0.5, delay)`)
  before **every** attempt, first and retries alike — exactly the shape
  `iter_products()` had, where the retry `continue` looped back through the
  same pre-request sleep.
- Sends the GET with an explicit `timeout=REQUEST_TIMEOUT`
  (`httpx.Timeout(30.0)`), replacing httpx's default 5s. Catalog crawls are
  paced in tens of seconds per request; failing a whole source over a 5-second
  handshake budget is the wrong trade.
- A 429 response raises immediately, uncounted, with the full response-header
  dump at DEBUG — unchanged semantics and rationale from the 2026-08-04
  amendment to the 429-backoff spec (retrying a platform-edge throttle just
  burns the budget), now living in the helper.
- Any other `httpx.HTTPError` (timeouts and DNS failures included — the class
  covers transport errors, not just status errors) counts toward
  `failure_limit`; the request is retried, paced, until the budget is spent,
  then the last error raises. `failure_limit <= 0` fails fast on the first
  error, same as `iter_products()` — "disabled" must not mean unlimited
  retries where there is no next item to move on to.
- `allow_404=True` returns a 404 response instead of raising, for Dischord's
  detail pages, where a dead release link is expected and skipped.

The budget is per call. `iter_products()`'s old counter lived across pages but
reset on every success, so consecutive failures only ever accumulated within
one page's attempts — per-call is the same semantics, without shared state.

### Call sites

- `shopify_catalog.iter_products()` delegates its sleep/GET/raise_for_status/
  retry block to the helper; its pagination, page ceiling, and reporting are
  untouched. The 429 header-dump log line moves to the `catalog_http` logger.
- `crawlers/darkdescentrecords.py`, `crawlers/sgrecordshop.py`,
  `crawlers/dischordrecords.py`, `crawlers/ripplemusic.py`,
  `crawlers/asbestosrecords.py`, `crawlers/jetglowrecordings.py` replace each
  hand-rolled sleep + `client.get()` + `raise_for_status()` with the helper,
  reading `consecutive_failure_limit` from config once per crawl next to the
  existing `crawl_delay_seconds` read.

`crawl_manager._sync_stock`'s handling is unchanged: a source that exhausts
its retry budget still raises, still discards the run's partial result (the
previous snapshot survives), still counts toward the site breaker. This change
only stops a *single* transient fault from being enough to get there.

Out of scope: `catalog_browser` crawlers (Playwright, no httpx),
`discogs_marketplace` and the release-crawl path (per-release failures fall
through to the next queue item; `crawl_releases()` already owns that
handling), and any cross-run state.

## Testing

- `catalog_http.get_with_retry` (respx): retries a `ConnectTimeout` then
  succeeds; raises after `failure_limit` consecutive failures; fails fast at
  `failure_limit <= 0`; raises a 429 on first sight without retrying and logs
  its headers at DEBUG; returns a 404 body under `allow_404` without retrying;
  paces every attempt with the jittered delay; stamps the explicit 30s
  timeout on the request.
- `iter_products`' existing suite passes unchanged in substance; the two tests
  that reached into `shopify_catalog` internals (the monkeypatched `sleep`,
  the 429 DEBUG logger name) now target `catalog_http`.
- Existing crawler suites pass: the conversion is behavior-preserving for
  everything they assert, and their transient-failure raises still raise once
  the budget is spent.
