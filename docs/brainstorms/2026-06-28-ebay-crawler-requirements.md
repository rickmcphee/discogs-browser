# eBay Crawler — Requirements

**Date:** 2026-06-28
**Status:** Ready for planning
**Branch:** `dev-ebay-crawler`

---

## Problem

The existing CC Music crawler (`backend/crawlers/ccmusic.py`) uses Playwright to scrape ccmusic.com directly. The site is Cloudflare-protected, resulting in frequent bot detection failures and unreliable price data. CC Music operates an eBay storefront that is accessible via the eBay Browse API — structured, authenticated, and not subject to bot detection.

## Goal

Replace the Playwright-based CC Music crawler with an API-based crawler that queries CC Music's eBay store, returning the best available price for each release.

---

## Requirements

### Crawler behaviour

- Implemented as a standard crawler plugin at `backend/crawlers/ebay.py`
- Queries the eBay Browse API (`/buy/browse/v1/item_summary/search`) with artist + title as the search term
- Filters results to the CC Music eBay seller only (seller ID configurable, not hardcoded)
- Returns the single lowest-priced Buy It Now listing for each release
- Auctions are excluded (Buy It Now only)
- Returns price, shipping cost (if available), currency, and condition from the API response
- `search_url()` returns the CC Music eBay store search URL for the release (used for pre-populated listing links in the UI) — seller `collectorschoicemusic`
- The `search()` method signature matches the existing crawler interface — the `Page` argument is accepted but unused

### API key

- eBay API key (OAuth app token) added as a new field in the Settings UI
- Stored in `config.json` alongside `discogs_token`, using the same load/save pattern
- If the key is not configured, the crawler logs a warning and returns `[]` (same pattern as Discogs token check)
- Key label in UI: "eBay API key"

### Replacing CC Music

- `backend/crawlers/ccmusic.py` is removed once the eBay crawler is verified working
- The site name remains `"CC Music"` in `ebay.py` so the column name in the collection view is unchanged and existing listing records are preserved
- The CC Music seller ID should be verified against the live eBay store before implementation and stored as a constant in the crawler file

### No Playwright dependency

- The eBay crawler is a pure `httpx` call — no browser, no `Page` interaction
- This makes it faster, more reliable, and suitable for running without Playwright installed

---

## Out of scope

- General eBay search across all sellers
- Auction / best-offer listing types
- Multiple condition tiers (new vs used split)
- Adding other eBay sellers

---

## Open questions / assumptions

- **CC Music eBay seller ID**: confirmed as `collectorschoicemusic`.
- **eBay OAuth token type**: the Browse API uses an Application token (client credentials flow), not a user token. Token refresh should be handled automatically or cached with expiry.
- **Rate limits**: eBay Browse API allows 5,000 calls/day on a standard developer key. With a collection of ~600 releases this is comfortable for full crawls.

---

## Success criteria

- Refresh Prices for a known release returns a CC Music price sourced from eBay
- Bot detection errors from the old crawler are eliminated
- No change to the collection view UI — CC Music column continues to work as before
- Settings UI shows an eBay API key field that saves and loads correctly
