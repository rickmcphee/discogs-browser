# Amazon Crawler Spec

**Site:** Amazon.com  
**URL:** https://www.amazon.com  
**File:** `backend/crawlers/amazon.py`

## Purpose

Find a vinyl/CD listing on Amazon matching a Discogs release and return the current price.

## Search Strategy

Navigate directly to an Amazon search URL constructed from the release's artist, title, and format. Do not start from the Amazon homepage.

Search URL pattern: `https://www.amazon.com/s?k={artist}+{title}+{format}&i=popular`

A pre-populated "View" link using this URL is inserted into the database before any crawl runs (via `search_url()` classmethod + `prepopulate_listings()`).

## Result Filtering

Amazon search results often include unrelated products. The crawler must validate each result before accepting it:

- Scan `[data-component-type="s-search-result"]` items.
- For each item, extract the product title from the `h2` heading.
- Accept the item only if the `h2` text contains the first word of the artist name **or** the first word of the release title (case-insensitive).
- Take the first accepted item.

This guards against false positives when Amazon fills the page with sponsored or tangentially related items.

## Price Extraction

Navigate to the product detail page of the accepted item and extract the price there.

On the product page, try in order:
1. `.a-price .a-offscreen` — the visually-hidden screen-reader price (most reliable)
2. Split-span fallback: combine `.a-price-whole` and `.a-price-fraction`

Extract the primary "new" listing price. Do not attempt to capture marketplace or used prices.

## Format Awareness

Include the format (e.g., "Vinyl", "CD") in the search query. This significantly reduces irrelevant results for albums available in multiple formats.

## Bot Detection

Users can log in to Amazon via Settings → Site Sessions. The login browser opens with a copy of the user's real Chrome cookies, and the resulting session is saved to `browser_state.json`. These cookies are loaded into the crawl browser at the start of each crawl.

If Amazon returns a CAPTCHA or other bot interstitial, raise `BotDetectedError`. The crawl engine will reset the browser context and retry once before skipping.

The crawl browser uses a persistent Chrome profile and `channel="chrome"` (real Chrome binary) with stealth applied.

## Known Limitations

- Amazon's catalogue skews toward new stock; rare vinyl may not appear.
- Prices reflect the primary "new" listing only.
- If no item passes the title-match filter, the result is `not_found`.
