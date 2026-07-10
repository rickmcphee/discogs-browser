# Collection/Wishlist Price Crawler Updates — Design

**Date:** 2026-07-08
**Status:** Draft
**Branch:** `collection-price-crawlers`

**Amendment (2026-07-08):** direct fetch of `discogs.com/sell/release/{id}` (plain `curl`, no browser) returns HTTP 403 with a Cloudflare "Just a moment..." interstitial — confirming the page requires a real browser (justifying the Playwright-based approach already chosen below) and that `BotDetectedError` handling is needed from the start, not added reactively as originally written. The exact post-challenge DOM structure (listing rows, price/shipping/condition selectors) could not be confirmed from outside a real browser session in this environment; the plan documents this as a manual verification step during implementation, the same posture this repo already takes for Playwright-scraped selectors generally (see CLAUDE.md: "Playwright-dependent code ... is not unit-tested; integration testing is manual").

## Problem

Post-testing feedback on the Collections tab surfaced three issues, all in the "per-release price search" pipeline (not the Store tab's catalog crawlers):

1. **Empty "Store" columns.** `RecordBrowser.tsx` renders one column per *enabled* crawler (`crawlers.filter(c => c.enabled)`), with no filter on `crawler_type`. Catalog crawlers (Century Media, Epitaph, Relapse, Secretly Store, and the other label-store sites added in the Store-tab work) populate `stock_items`, not per-release `listings` — so when one is enabled, its column renders in Collections/Wishlist but is always empty. There isn't enough overlap between a personal collection and any one store's current stock to make that column worth displaying anyway.
2. **No unrestricted eBay search.** The existing `backend/crawlers/ebay.py` (`site_name: "CC Music/eBay"`) hardcodes a `sellers:{collectorschoicemusic}` filter on the eBay Browse API call — by design, per its original spec (`docs/brainstorms/2026-06-28-ebay-crawler-requirements.md`), which explicitly scoped out "general eBay search across all sellers." That's now wanted as a second, separate crawler.
3. **No Discogs marketplace price.** Nothing searches Discogs's own marketplace. Note: the existing "Price" column (`r.discogs_price`) is *not* a live marketplace price — it's a custom note value pulled from the user's own Discogs collection entry (`backend/discogs.py:parse_release`, `price_field_id` lookup), typically what they paid or a manually-set field. A live "cheapest USA-shipping listing" price is a genuinely new data point, not a duplicate of that column.

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
- **Both new crawlers auto-enable on first registration**, `crawler_type` defaulting to `"release"` (unset, same as `amazon.py`/`ebay.py` today). This was originally scoped as "ships disabled by default," but `db.register_crawler` always inserts new rows with `enabled = 1` (confirmed in `backend/tests/test_db.py:436`, "enabled by default") — every existing crawler, including all 13 catalog crawlers, has always auto-enabled on first registration, with no precedent for a crawler-declared opt-out. Rather than add a new mechanism with no other caller, the two new crawlers follow the existing behavior: they run immediately after this ships, same as any other newly bundled crawler. The user can disable either via the existing Settings toggle if unwanted.
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
- A Settings UI change beyond the two new crawlers appearing in the existing crawler-enable table (no new section needed — they're `crawler_type: "release"`, same table as Amazon/CC Music/eBay).

## Success criteria

- Enabling any catalog (Store-type) crawler has no effect on Collections/Wishlist columns; only `release`-type crawlers ever appear there.
- The "eBay" column returns results from sellers other than CC Music, sorted by price + shipping, Buy-It-Now only.
- The "CC Music/eBay" column's behavior is unchanged after the `ebay_api.py` extraction — same seller scoping, same results, same config keys.
- The "Discogs" column shows the cheapest listing that ships from the USA for a release, or no result if none ships from the USA — independent of and never overwriting the existing "Price" column.
- Both new crawlers appear enabled in Settings after this ships (matching every other crawler's first-registration behavior) and start producing results on the next price refresh without any new config fields to fill in first; the user can disable either via the existing toggle.
