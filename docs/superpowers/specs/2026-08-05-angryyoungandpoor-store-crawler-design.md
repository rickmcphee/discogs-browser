# Angry Young and Poor Store Crawler — Design Spec

_2026-08-05_

**Status:** Draft
**Branch:** `store-crawler-angryyoungandpoor`

**Amendment (2026-08-05, during plan-writing):** two corrections to the original Title parsing and accessory filtering section below, found while verifying details against live data before writing the implementation plan (text below is updated in place to match):

1. **The filter is category-specific, not global.** V/A Compilation LPs (`V-A-Compilation-LPs-c397`) titles carry no `"Artist- "` prefix at all — confirmed live: `"Barbarian (Soundtrack) LP (Mothers Milk & Blood Splatter Vinyl)"`, `"Carrie (Soundtrack) 2xLP (Red & Orange Smoke Vinyl)"`. Requiring a `"- "` split globally, as originally written, would have silently dropped every item in that category. The dash+format-token filter applies only to Records-c301, Sale-Records-c472, and Used-Records-c1215 (all three confirmed to use the `"Artist- Title FORMAT (variant)"` shape). V/A Compilation LPs instead requires only a format-token match, with `artist` hardcoded to `"Various Artists"` — that category isn't confirmed to mix in non-release accessories the way Records-c301 does, so the dash gate isn't needed there, and the category can't provide one anyway.
2. **A format-token check alone is not a safe accessory filter.** `12" Record Sleeve` contains a literal `12"`, which matches the same inch-size token (`\d+\s*"`) used to detect 7"/10"/12" singles — a format-token-only rule would have let it back in. The dash-split requirement is what actually excludes accessories (none of the sampled accessory names contain `"- "` at all); the format-token check is a second, additional gate on top of it, not a substitute for it.

The rest of this section (the concrete regex/split rule) is written below already reflecting both corrections.

## Overview

Add `angryyoungandpoor.com` as a 32nd catalog source for the Store tab —
but unlike every existing catalog source, this one can't reuse
`shopify_catalog.py`. Direct inspection (`curl` with a real browser UA)
confirms Cloudflare returns "Attention Required!" (403) for any non-browser
request, including `robots.txt` — this is a platform-level block, not a
per-endpoint one. The site itself is not Shopify: it's PinnacleCart
(confirmed by `pc*`-prefixed CSS classes — `pcButton`, `pcShowProductsH`,
`pcColumn` — and the `/store/pc/` URL segment), server-rendering product
listings with schema.org microdata (`itemprop="name"`,
`<meta itemprop="price">`) and no public JSON endpoint.

The existing catalog-crawler subsystem (`crawler_type="catalog"`,
`crawl_catalog()`, invoked from `CrawlManager._sync_stock`) is pure `httpx`
with zero Playwright plumbing — it was never designed to need a browser,
because every prior catalog source is an unauthenticated Shopify JSON
endpoint. Bypassing Cloudflare here requires a real, stealth-patched browser,
which this app already has — just on the *other* crawler path
(`crawler_type` release-search, e.g. `amazon.py`'s `search()` +
`BotDetectedError`) — not the catalog one.

## Goals / non-goals

**Goals**
- Introduce `crawler_type="catalog_browser"`: a catalog crawler that receives
  a Playwright `Page` and can raise `BotDetectedError`, reusing the shared
  stealth Chromium instance and bot-detection-retry convention the
  release-crawl path already has, instead of building a second one.
- Add an Angry Young and Poor crawler covering four PinnacleCart categories —
  Records (`Records-c301`), Sale Records (`Sale-Records-c472`), Used Records
  (`Used-Records-c1215`), and V/A Compilation LPs (`V-A-Compilation-LPs-c397`)
  — deduplicated by product ID.
- Filter out non-release accessories (record sleeves, cleaning supplies) that
  share Records-c301's listing markup with actual releases.

**Non-goals**
- No changes to `shopify_catalog.py` or any existing `catalog`-type crawler.
- No attempt to detect "sold out" — no reliable signal was found in sampling
  (see Technical grounding). Every scraped item is treated as in-stock; this
  is a known, accepted gap, not a guessed-at filter.
- No dedicated `condition` column/API/UI — see Data model below.

## Technical grounding

### Platform and blocking

- `curl -A "<real Chrome UA>" https://www.angryyoungandpoor.com/store/pc/Records-c301.htm` → Cloudflare interstitial, title `"Attention Required! | Cloudflare"`, HTTP 403. Same result for `/robots.txt`. Confirmed independently of this app's code.
- A real browser session (used for this investigation) loads the page fine; network trace shows a `POST` to `/cdn-cgi/challenge-platform/h/b/jsd/oneshot/...` — Cloudflare's non-interactive JS challenge running silently, not an interactive CAPTCHA. `playwright_stealth`, already used on the release-crawl path, is the existing tool for passing this.
- No JSON API found. Product data is server-rendered HTML with schema.org microdata:

  ```html
  <div class="pcShowProductsH pcShowProductBgHover" data-pid="372193">
    <a itemprop="url" href="100-Demons-Embrace-The-Black-Light-LP-Onyx-Marble-Vinyl-301p372193.htm">
      <img itemprop="image" src="catalog/products/lp/CCAS157X.jpg">
    </a>
    <span itemprop="name">100 Demons- Embrace The Black Light LP (Onyx Marble Vinyl)</span>
    <div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
      <meta itemprop="priceCurrency" content="USD">
      <meta itemprop="price" content="27.99">
      Price: <span class="eprice">$27.99</span>
    </div>
  </div>
  ```

### Pagination

Each category supports `?viewAll=yes`, returning the entire category on one
page (confirmed: Records is otherwise 56 pages of ~80 items at the default
page size). Using `viewAll=yes` means 4 requests total (one per category)
instead of dozens — Records' full page is ~15MB of HTML at that item count,
which Playwright handles fine but needs a raised `page.goto()` navigation
timeout (Playwright's default is 30s).

### Category overlap and dedup

Records-c301 and Sale-Records-c472 are **not** independent pools: the
13th Floor Elevators "Easter Everywhere" listing was confirmed present in
both, same `data-pid="360293"`, same product URL. Sale Records is a
cross-listing of a subset of Records, not separate stock. V/A Compilation
LPs (c397) has its own distinct `data-pid` range (soundtrack titles like
"Barbarian Soundtrack LP") not seen in Records, and looks like a genuinely
separate pool, though this wasn't exhaustively verified. Regardless of which
categories turn out to overlap, the crawler dedupes every item it collects
by `data-pid` before yielding — this makes the overlap question moot rather
than something to get right per-category.

### Title parsing and accessory filtering

Real releases follow `"Artist- Title FORMAT (variant)"`, e.g.:

```
100 Demons- Embrace The Black Light LP (Onyx Marble Vinyl)
13th Floor Elevators- Easter Everywhere LP (Sale price!)
AGNOSY- When Daylight Reveals The Torture LP (USED)
```

Records-c301 also lists non-release accessories using the *same* markup —
confirmed via direct inspection, no structural field (no `product_type`,
no category tag) distinguishes them:

```
12" Record Sleeve
Vinyl Styl Record Cleaning Fluid (16oz)
```

Filter, for Records-c301 / Sale-Records-c472 / Used-Records-c1215 (all three
confirmed to use this title shape): require the name to split on `"- "` into
two non-empty parts (artist, remainder) **and** the remainder to contain a
format token (`LP`, `2XLP`, `3XLP`, `7"`, `10"`, `12"`, `EP`,
case-insensitive). Both conditions are required — a format token alone isn't
enough, since `12" Record Sleeve` contains a literal `12"` and would
otherwise pass; the dash-split is what actually excludes accessories (no
sampled accessory name contains `"- "` at all). Accessory titles fail the
dash-split immediately.

V/A Compilation LPs (`V-A-Compilation-LPs-c397`) doesn't fit this shape —
titles carry no artist prefix at all (`"Barbarian (Soundtrack) LP (Mothers
Milk & Blood Splatter Vinyl)"`, `"Carrie (Soundtrack) 2xLP (Red & Orange
Smoke Vinyl)"`). For this one category, skip the dash requirement, require
only the format-token match, and hardcode `artist = "Various Artists"`.

This is a new filter shape relative to every existing Shopify crawler — none
of them lean on a title-regex to separate real releases from non-music
listings, because Shopify's `product_type`/tags already do that cleanly.
Flagging this as the weakest part of the design: a real release in one of
the three dash-shaped categories, titled without a dash, would be silently
dropped. None were found in sampling, but sampling wasn't exhaustive.

### Condition and sale-price noise

Used Records items reliably end in `"(USED)"` (confirmed across the sampled
page: `"AGNOSY- When Daylight Reveals The Torture LP (USED)"`,
`"RAMONES- Halfway To Sanity LP (USED)"`, no exceptions found). Sale items
carry `"(Sale price!)"` in the title, which is decorative — the price shown
is already the sale price, there's no separate list-price arithmetic needed
here (unlike some Shopify sites' `LIST PRICE`/`YOU SAVE` display, which this
crawler doesn't need to reproduce since `stock_items` only stores one price).

## Design

### `crawler_type="catalog_browser"`

`CrawlManager._sync_stock` currently does:

```python
enabled = get_enabled_crawlers(conn, crawler_type="catalog")
crawlers = load_enabled_crawlers(enabled)
...
async for item in crawler.crawl_catalog():
    items.append(item)
```

Extend it to also load `crawler_type="catalog_browser"` crawlers and, for
those, open a page from the shared `self._browser` (the same one
`start_worker_pool()` launches with `playwright_stealth`) before calling in:

```python
context, page = await _new_context(self._browser, self._stealth)
try:
    async for item in crawler.crawl_catalog(page):
        items.append(item)
except BotDetectedError:
    context, page = await _reset_context(context, self._browser, self._stealth, None)
    async for item in crawler.crawl_catalog(page):
        items.append(item)
finally:
    await context.close()
```

One retry on `BotDetectedError`, same convention as `_paced_search` on the
release-crawl path — if the retry also hits the interstitial, it propagates
and this crawler's run fails for this sync, same as any other exception
`_sync_stock` already handles. `crawl_delay_seconds` applies the same way it
does in `shopify_catalog.iter_products()`: `crawl_catalog()` sleeps
`random.uniform(delay * 0.5, delay)` before every `page.goto()`, including
the first — this site leans on Cloudflare to block anything that doesn't
look like normal browser traffic, so the 4 category-page loads can't fire
back-to-back. `consecutive_failure_limit` has no equivalent here:
`crawl_catalog()` has no generic-failure retry loop, only the one-shot
`BotDetectedError` retry described above.

Plain `catalog` crawlers are untouched — `crawl_catalog()` keeps its current
zero-arg signature for them; only `catalog_browser` crawlers get a `page`.

### `backend/crawlers/angryyoungandpoor.py`

```python
class Crawler:
    site_name: str = "Angry Young and Poor"
    base_url: str = "https://www.angryyoungandpoor.com/store/pc"
    crawler_type: str = "catalog_browser"

    _CATEGORIES = [
        "Records-c301.htm",
        "Sale-Records-c472.htm",
        "Used-Records-c1215.htm",
        "V-A-Compilation-LPs-c397.htm",
    ]

    async def crawl_catalog(self, page) -> AsyncIterator[dict]:
        seen_pids: set[str] = set()
        for category_path in self._CATEGORIES:
            await page.goto(f"{self.base_url}/{category_path}?viewAll=yes", timeout=120_000)
            if "Cloudflare" in await page.title():
                raise BotDetectedError("Cloudflare interstitial")
            raw_products = await page.evaluate(_EXTRACT_JS)
            for pid, item in self._parse_category(category_path, raw_products):
                if pid in seen_pids:
                    continue
                seen_pids.add(pid)
                yield item
```

No HTML-parsing library is added — this codebase has no BeautifulSoup/lxml
dependency today, and every other Playwright-driven crawler here
(`amazon.py`) queries the live DOM directly rather than parsing a raw HTML
string. `_parse_category` instead runs a single `page.evaluate()` per
category, extracting all `data-pid` blocks inside the confirmed single
`.pcShowProducts` container into a plain JSON array in one round trip
(cheaper than looping `page.locator()` calls across ~4400 items):
`itemprop="name"` for the raw title, `<meta itemprop="price" content="...">`
for price, `itemprop="url"` for the product link, `itemprop="image"` for the
cover image, `data-pid` for the dedup key. The artist/title/format split,
accessory filtering, and `(USED)` condition suffix are then plain Python
string/regex work on the returned title strings, not part of the
in-browser extraction.

## Data model

`stock_items` gains no new column. Condition is folded into the display
title as a `" (Used)"` suffix when the source title ends in `"(USED)"` —
the same pattern `is_preorder` suffixes already use elsewhere (e.g.
`secretlystore.py` appending `" (Pre-Order)"`). A dedicated `condition`
column was considered and rejected for now: every other of the 31 existing
catalog sources is new-only, so a schema column with 31 crawlers writing
`NULL` for one crawler's benefit is more invasive than the value justifies
at this scale. If a second used/vintage source shows up later, revisit.

## Error handling

- Bot detection: page title containing `"Cloudflare"` (or body text matching
  the interstitial) → raise `BotDetectedError`. One retry via
  `_reset_context`, then propagate.
- No `httpx.HTTPStatusError`/429 handling applies here — there's no raw HTTP
  status visible through a Playwright page load the way `shopify_catalog.py`
  sees one via `httpx`. If Cloudflare escalates to a hard rate-limit block
  under real usage, that surfaces as another bot-detection failure and gets
  the same retry-then-fail treatment; no separate circuit breaker is planned
  for it up front.
- Large-page navigation: `page.goto(..., timeout=120_000)` — Records-c301's
  `viewAll=yes` page is ~15MB; the default 30s Playwright timeout is not
  enough margin.

## Testing

`backend/tests/crawlers/test_amazon_price_extraction.py` already establishes
the pattern for testing Playwright-dependent extraction offline: launch a
real local headless browser, load a saved static HTML fixture via
`page.set_content()` (no navigation, no live site, no bot-detection risk),
and call the extraction code against that real page. This crawler follows
the same pattern rather than the dict-based unit test originally sketched
here: a small saved fixture per category shape
(`backend/tests/fixtures/crawlers/angryyoungandpoor/records.html`,
`va_compilation.html`) containing a handful of representative
`.pcShowProducts`/`data-pid` blocks (2-3 real releases, 1-2 accessories, one
`(USED)` item for the `records.html` fixture; 2-3 soundtrack titles for
`va_compilation.html`) — enough to exercise the real `_EXTRACT_JS`
`page.evaluate()` call plus the downstream Python parsing, not the full
~15MB live page. A new `test_angryyoungandpoor_crawler.py` asserts:
artist/title/format split for the dash-shaped categories, `"Various
Artists"` for V/A Compilation LPs, accessory exclusion (record sleeve /
cleaning fluid samples), `(USED)` → condition suffix, and cross-category pid
dedup (the same `pid` appearing in two loaded fixtures, confirming only one
item is yielded across a full `crawl_catalog()` run — this last assertion
does need `crawl_catalog()` itself, via a fake `page` stub whose `goto()`
loads the matching fixture per category and whose `evaluate()` delegates to
the real Playwright page's `evaluate()`).

## Frontend impact

`frontend/src/views/Settings.tsx` buckets crawlers into the Settings page's
two tables by literal string comparison — `releaseCrawlers = crawlers.filter(c
=> c.crawler_type !== 'catalog')` and `catalogCrawlers = crawlers.filter(c =>
c.crawler_type === 'catalog')` ([Settings.tsx:83-84](../../../frontend/src/views/Settings.tsx#L83-L84)).
A `catalog_browser` crawler would fail the strict `=== 'catalog'` check (so
it's dropped from the Store Crawlers table) while passing `!== 'catalog'`
(so it's wrongly bucketed into the release-crawler table instead). This
needs both filters tightened — `releaseCrawlers` to `c.crawler_type ===
'release'`, `catalogCrawlers` to `c.crawler_type === 'catalog' ||
c.crawler_type === 'catalog_browser'` — and `frontend/src/api/types.ts`'s
`crawler_type: 'release' | 'catalog'` union widened to include
`'catalog_browser'`. `RecordBrowser.tsx`'s own `crawler_type === 'release'`
check ([RecordBrowser.tsx:123](../../../frontend/src/views/RecordBrowser.tsx#L123))
is already strict and needs no change.

## Open items / accepted gaps

- No sold-out signal confirmed. Everything scraped is treated as in-stock.
- The `"- "` + format-token filter (Records/Sale/Used) is a title-shape
  heuristic, not a structural field — the weakest point in this design;
  revisit if a real release in one of those three categories turns out to
  lack a dash separator.
- V/A Compilation LPs is assumed not to mix in non-release accessories the
  way Records-c301 does, which is why it skips the dash-split gate — this
  wasn't exhaustively verified, only spot-checked against ~10 sampled titles,
  all soundtrack/score releases.
- V/A Compilation LPs' non-overlap with Records-c301 wasn't exhaustively
  verified, only spot-checked. The pid-based dedup makes this safe either
  way.
