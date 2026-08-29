# Collection/Wishlist Price Crawler Updates — Design

**Date:** 2026-07-08
**Status:** Draft
**Branch:** `collection-price-crawlers`

**Amendment (2026-08-29, branch `claude/discogs-price-tracking-matches-vs426m`):** the Discogs crawler under-reported badly in practice, and the cause is the decision this design deferred: "exact query params and DOM selectors are unverified and must be confirmed against the live site during implementation." They never were, and nothing downstream could tell that they hadn't been.

As shipped, `search()` read the page at `domcontentloaded` and never waited for anything. A listings region that had not rendered yet, or markup that no longer matched `#pjax_container table tbody tr`, produced zero rows and returned `[]` — which `CrawlManager._drain_one_batch` reads as "the site answered and has nothing", so it called `clear_listing_price()` and `delete_stock_item_for_release()`. Each pass therefore *erased* a price an earlier pass had found, and counted the miss toward the per-site breaker, so `consecutive_failure_limit` such releases in a row cooled Discogs off for thirty minutes. The visible symptom was a handful of prices where the marketplace plainly had many. The same missing wait defeated the bot check from the other side: Cloudflare's interstitial is always what renders first, so reading the title once, immediately, saw "Just a moment..." even on requests whose challenge would have cleared a few seconds later.

`[]` now means what CLAUDE.md says it means. The crawler waits for the challenge to settle, then waits for listings content (the way `amazon.py` waits for `.a-price`), and returns `[]` only on a rendered empty state or a `GET /marketplace/stats/{release_id}` confirming the release has no copies for sale anywhere. That endpoint is public, needs no auth, and — unlike the HTML page — is not behind the Cloudflare challenge. Anything else raises, naming the release, the page title and the stats count. Raising skips the write path entirely, so a page the crawler could not parse no longer erases good data.

Consequently the crawler declares `empty_result_is_expected = True`, which until now had been reserved for single-store crawlers rather than marketplaces. That exclusion exists for crawlers that cannot separate "nothing here" from "I could not read this", leaving the breaker to infer breakage from emptiness; this one separates them itself and raises on the second, so the breaker already has its signal and the remaining empties are confirmed answers. Counting them only cooled the site off over releases with no USA seller.

Two decisions above are revised rather than merely elaborated. "No fuzzy matching / scrape the first row" assumed `sort=price,asc` is honoured; since that parameter is still unconfirmed, the cheapest listing is now chosen from the parsed rows rather than taken from page order, so a silently ignored sort cannot make the first row masquerade as the cheapest. And row parsing now falls back to `data-pricevalue`, the attribute Discogs's own currency-switching JS reads, so a restyle that drops the legacy table does not take the crawler with it. There is deliberately no bare `[data-pricevalue]` sweep as a last resort — `amazon.py` already learned that scraping every price-shaped element picks up carousel prices, and a wrong price is worse here than a loud failure.

Still unverified, and now failing loudly instead of silently: the selectors and the `ships_from` parameter could not be confirmed from the sandbox this was written in, which Cloudflare challenges on every request. Fixtures under `backend/tests/fixtures/crawlers/discogs_marketplace/` encode the markup the crawler claims to support, not markup captured from the live site.

**Amendment (2026-08-09, second):** `search_ebay()` also logs the failing response's body at DEBUG, truncated to 2000 characters, alongside the existing ERROR line. Prompted by eBay 409s that survived the circuit breaker's full 30-minute cooldown and resumed immediately on expiry: the status line can't distinguish a daily quota from a suspended keyset from a transient fault, and eBay documents no per-status meaning for Browse `item_summary/search` failures, so the body's `errors[]` array (`errorId` + `longMessage`) is the only thing that says which. Follows `shopify_catalog.iter_products()`, which dumps 429 response headers at DEBUG for the same reason. Truncated because a non-eBay error page (a proxy's HTML) would otherwise put tens of KB into ~~a rotating log file the viewer has to render~~ the log store the viewer has to render. Whitespace in the body is collapsed so the record stays a single line: ~~`routers/logs.py`'s `_line_visible` passes through any line it can't read a level from, so a multi-line body's continuation lines would appear in *every* level view regardless of the DEBUG filter — the same path that puts tracebacks in the INFO stream as `OTHER`~~ (see 2026-08-17 amendment below — this reasoning is no longer why the collapsing happens). DEBUG is **not** in the log viewer's default level set (`{INFO, WARNING, ERROR}`), and that viewer filters by exact level membership — the DEBUG toggle has to be switched on to see these.

**Amendment (2026-08-17, branch `flyio-log-files-machines`):** the `_line_visible`/rotating-file mechanics the amendment above cites no longer exist — `routers/logs.py` reads a Postgres `app_logs` table, and every row carries a real `level` column instead of one parsed by regex from a tailed text line, so there's no "unparseable continuation line leaks into every level view" failure mode to guard against any more. The truncation and single-line whitespace-collapsing in `ebay_api.py` are unaffected by this and still happen (a 2000-char single-line DEBUG message is still more useful than an untruncated multi-line one), just for a smaller reason now: keeping one log row readable at a glance, not working around level-detection regex. See [`2026-08-17-unified-log-store-design.md`](../../specifications/shaping/2026-08-17-unified-log-store-design.md).

**Amendment (2026-08-09):** `search_ebay()` no longer returns `[]` on an HTTP or transport error — it logs and re-raises. The "returns `[]` on no match" comment in the module sketch below is still accurate for a genuine no-match, but it was implemented as "returns `[]` on no match *or any API failure*", which made an eBay error indistinguishable from "this release isn't listed on eBay." That mattered once release crawlers began searching for store-crawler stock items too (`docs/specifications/shaping/2026-08-08-crawl-target-expansion-design.md`): a stock item's empty result is deliberately excluded from the per-site consecutive-failure circuit breaker, so successive eBay errors — 409s in particular — never incremented the failure counter and the site never cooled off, however many came back in a row. Every status is raised rather than a 409-specific case: eBay documents no per-status meaning for Browse `item_summary/search` failures, and any of them is equally "the API isn't answering," which is what the breaker counts. The caller (`CrawlManager._drain_one_batch`) already treats an exception from `plugin.search()` as a site failure for both target kinds, so no crawl-manager change was needed. See item 9 of [`2026-08-01-worker-pool-pacing-design.md`](2026-08-01-worker-pool-pacing-design.md).

**Amendment (2026-07-08):** direct fetch of `discogs.com/sell/release/{id}` (plain `curl`, no browser) returns HTTP 403 with a Cloudflare "Just a moment..." interstitial — confirming the page requires a real browser (justifying the Playwright-based approach already chosen below) and that `BotDetectedError` handling is needed from the start, not added reactively as originally written. The exact post-challenge DOM structure (listing rows, price/shipping/condition selectors) could not be confirmed from outside a real browser session in this environment; the plan documents this as a manual verification step during implementation, the same posture this repo already takes for Playwright-scraped selectors generally (see CLAUDE.md: "Playwright-dependent code ... is not unit-tested; integration testing is manual").

## Problem

Post-testing feedback on the Collections tab surfaced three issues, all in the "per-release price search" pipeline (not the Store tab's catalog crawlers):

1. **Empty "Store" columns.** `RecordBrowser.tsx` renders one column per *enabled* crawler (`crawlers.filter(c => c.enabled)`), with no filter on `crawler_type`. Catalog crawlers (Century Media, Epitaph, Relapse, Secretly Store, and the other label-store sites added in the Store-tab work) populate `stock_items`, not per-release `listings` — so when one is enabled, its column renders in Collections/Wishlist but is always empty. There isn't enough overlap between a personal collection and any one store's current stock to make that column worth displaying anyway.
2. **No unrestricted eBay search.** The existing `backend/crawlers/ebay.py` (`site_name: "eBay/CCmusic"`) hardcodes a `sellers:{collectorschoicemusic}` filter on the eBay Browse API call — by design, per its original spec (`docs/brainstorms/2026-06-28-ebay-crawler-requirements.md`), which explicitly scoped out "general eBay search across all sellers." That's now wanted as a second, separate crawler.
3. **No Discogs marketplace price.** Nothing searches Discogs's own marketplace. Note: the existing "Price" column (`r.discogs_price`) is *not* a live marketplace price — it's a custom note value pulled from the user's own Discogs collection entry (`backend/discogs.py:parse_release`, `price_field_id` lookup), typically what they paid or a manually-set field. A live "cheapest USA-shipping listing" price is a genuinely new data point, not a duplicate of that column. **(2026-08-09: this description was right, and the storage has caught up — the value now lives on `library_items.price_paid` rather than the global `catalog.discogs_price`. `discogs_price` remains the wire/JSON field name, so `r.discogs_price` still reads correctly in the API response.)**

## Goal

Three independent fixes, scoped so each can ship on its own:

1. Filter Collections/Wishlist columns to `crawler_type: "release"` crawlers only.
2. Add a general eBay crawler (all sellers, not just CC Music).
3. Add a Discogs marketplace crawler (cheapest listing shipping from the USA).

## Decisions

- **Column fix is a single-line change with no Wishlist-specific work.** `RecordBrowser.tsx` is shared by both the Collections and Wishlist tabs (differentiated only by a `scope` prop) — fixing the filter in one place fixes both tabs, since they render the exact same component.
- **Shared eBay logic moves to a top-level module, not a second copy.** Crawler plugin files are loaded via `importlib.util.spec_from_file_location` from an arbitrary path and are never real members of the `backend.crawlers` package — a shared file placed *inside* `backend/crawlers/` would get mis-registered as a bogus crawler by the startup bootstrap's `glob("*.py")` scan (same constraint documented in `docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md` for `shopify_catalog.py`). Following that precedent, the OAuth token fetch, item-matching, and format-mapping logic in `ebay.py` moves to a new top-level `backend/ebay_api.py`, and both `ebay.py` and the new general-eBay crawler import from it. This is a refactor of existing code, not new abstraction for its own sake — without it, the token-fetch/matching logic (~100 lines) would need to be duplicated verbatim into the new crawler file.
- **General eBay crawler is `ebay.py`'s logic minus the seller filter.** New file, `site_name: "eBay"`, `base_url: "https://www.ebay.com"`. Reuses the existing `ebay_app_id`/`ebay_cert_id` Settings fields — same eBay developer app credentials, no new config field needed. Search behavior is otherwise identical: Buy-It-Now only, sorted by price + shipping, same artist/title/format validation via `pick_matching_item`. `limit` raised from 3 to 5 candidates server-side (unrestricted seller search returns more noise before a validated match is found; `pick_matching_item` still only returns the first one that passes).
- **Discogs marketplace crawler needs no fuzzy matching.** Discogs marketplace listings are scoped to an exact `release_id` — `search_url()` builds directly off `release["discogs_id"]` (strip the leading `"r"` — see `backend/discogs.py:parse_release`, which stores `discogs_id` as `f"r{release_id}"` but `discogs_url` as the bare numeric form), so there's no artist/title/format ambiguity to resolve the way `ebay.py`/`amazon.py` need.
- **Discogs marketplace crawler is Playwright-based, not API-based**, following `amazon.py`'s pattern rather than `ebay.py`'s — Discogs's public API has no endpoint returning per-listing ship-from country for a release's marketplace listings; that data only exists on the rendered `discogs.com/sell/release/{id}` page.
- **Exact query params and DOM selectors are unverified and must be confirmed against the live site during implementation**, the same way `amazon.py`'s selectors were confirmed via `capture_fixture.py` and the Shopify crawlers' shapes were confirmed via direct fetch (see the extensive "Technical grounding" sections in `2026-07-05-in-stock-crawler-design.md` for the standard this repo holds crawler specs to). This design assumes `ships_from=United States` and a price-ascending sort are expressible as URL query params on that page (mirroring how `sell/release` pages are known to support filtering/sorting in the browser), but the literal param names/values are a first guess, not a confirmed fact.
- **No fallback if no USA-shipping listing exists.** The crawler returns a miss (`[]`), the same as any other crawler with zero matching results — it does not fall back to the cheapest listing regardless of ship-from country.
- **Both new crawlers auto-enable on first registration**, `crawler_type` defaulting to `"release"` (unset, same as `amazon.py`/`ebay.py` today). This was originally scoped as "ships disabled by default," but `db.register_crawler` always inserts new rows with `enabled = 1` (confirmed in `backend/tests/test_db.py:436`, "enabled by default") — every existing crawler, including all catalog crawlers, has always auto-enabled on first registration, with no precedent for a crawler-declared opt-out. Rather than add a new mechanism with no other caller, the two new crawlers follow the existing behavior: they run immediately after this ships, same as any other newly bundled crawler. The user can disable either via the existing Settings toggle if unwanted.
- **No data model changes.** `crawler_type` already exists (added in the Store-tab work); no new tables or columns needed for any of the three fixes.

## Frontend

- `RecordBrowser.tsx`: `enabledCrawlers` becomes `crawlers.filter(c => c.enabled && c.crawler_type === 'release')`. No other change — column rendering, sorting (`price_${site_name}`), and the listings lookup are all unaffected since they already key off whatever's in `enabledCrawlers`.

## Backend

### `backend/ebay_api.py` (new, shared)

Extracted from the current `backend/crawlers/ebay.py`, unchanged in behavior:

```python
async def get_token(app_id: str, cert_id: str) -> str: ...   # module-level cache, same as today
def pick_matching_item(items: list, release: dict) -> dict | None: ...
FORMAT_KEYWORDS: dict[str, list[str]]
FORMAT_CATEGORY_IDS: dict[str, str]

async def search_ebay(
    release: dict, app_id: str, cert_id: str,
    seller: str | None, limit: int,
) -> list[dict]:
    ...  # builds query (barcode-first, falls back to artist/title), calls the Browse API
         # with an optional `sellers:{seller}` filter clause, applies pick_matching_item,
         # returns [] on no match — same response shape as today's ebay.py.search()
```

### `backend/crawlers/ebay.py` (modified)

Unchanged `site_name`/`base_url`/`search_url` (still CC Music-scoped for the pre-populated "View" link). `search()` becomes a thin call: `search_ebay(release, app_id, cert_id, seller=CCMUSIC_SELLER, limit=3)`.

### `backend/crawlers/ebay_general.py` (new)

```python
class Crawler:
    site_name: str = "eBay"
    base_url: str = "https://www.ebay.com"

    @classmethod
    def search_url(cls, release: dict) -> str:
        # https://www.ebay.com/sch/i.html?_nkw={artist}+{title} — no seller path segment
        ...

    async def search(self, release: dict, page) -> list[dict]:
        cfg = load_config()
        return await search_ebay(release, cfg.get("ebay_app_id", ""), cfg.get("ebay_cert_id", ""), seller=None, limit=5)
```

### `backend/crawlers/discogs_marketplace.py` (new)

```python
class Crawler:
    site_name: str = "Discogs"
    base_url: str = "https://www.discogs.com"
    login_url: str = ""

    @classmethod
    def search_url(cls, release: dict) -> str:
        release_id = release["discogs_id"].lstrip("r")
        return f"https://www.discogs.com/sell/release/{release_id}?ships_from=United+States&sort=price%2Casc"

    async def search(self, release: dict, page) -> list[dict]:
        # navigate to search_url(release); if the page's empty-results state renders, return []
        # otherwise scrape the first (cheapest) listing row: price, shipping, currency, condition
        # exact selectors TBD — confirm against the live page during implementation
        ...
```

`BotDetectedError` handling is included from the start (see Amendment above) — a Cloudflare challenge page reliably shows a `title` containing `"Just a moment"`; the crawler checks for that before attempting to scrape listing rows, same shape as `amazon.py`'s `_bot_interstitial`.

## Out of scope

- A Settings field for a Discogs-specific credential — this crawler scrapes a public page, no auth needed, same as `amazon.py`.
- Any condition/grading threshold beyond "cheapest USA-shipping listing" (e.g. excluding poor-grade copies).
- Any change to `backend/discogs.py` or the authenticated Discogs API client — the new crawler is fully independent of it.
- Deduplicating or reconciling the new "Discogs" column against the existing "Price" (`discogs_price`) column — they display side by side as distinct data.
- A Settings UI change beyond the two new crawlers appearing in the existing crawler-enable table (no new section needed — they're `crawler_type: "release"`, same table as Amazon/eBay/CCmusic).

## Success criteria

- Enabling any catalog (Store-type) crawler has no effect on Collections/Wishlist columns; only `release`-type crawlers ever appear there.
- The "eBay" column returns results from sellers other than CC Music, sorted by price + shipping, Buy-It-Now only.
- The "eBay/CCmusic" column's behavior is unchanged after the `ebay_api.py` extraction — same seller scoping, same results, same config keys.
- The "Discogs" column shows the cheapest listing that ships from the USA for a release, or no result if none ships from the USA — independent of and never overwriting the existing "Price" column.
- Both new crawlers appear enabled in Settings after this ships (matching every other crawler's first-registration behavior) and start producing results on the next price refresh without any new config fields to fill in first; the user can disable either via the existing toggle.
