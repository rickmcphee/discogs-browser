# CC Music Crawler Spec

**Site:** CC Music  
**URL:** https://www.ccmusic.com  
**File:** `backend/crawlers/ccmusic.py`

## Purpose

Find a vinyl/CD listing on CC Music matching a Discogs release and return the current price.

## Search Strategy

Navigate directly to a search URL constructed from the release's artist and title. Do not start from the CC Music homepage.

Search URL pattern: `https://www.ccmusic.com/search?q={artist}+{title}&mod=AP`

A pre-populated "View" link using this URL is inserted into the database before any crawl runs (via `search_url()` classmethod + `prepopulate_listings()`).

Use `wait_until="networkidle"` when navigating to the search URL. Wait for result elements to appear before proceeding (up to 15 seconds).

## Result Filtering

Scan product/result item elements on the page. Extract title and price from each. Return the first match; apply any title similarity check if needed to avoid false positives.

## Price Extraction

Look for price elements within each result item. Strip `$` and commas, parse as float.

## Bot Detection — Cloudflare

CC Music uses Cloudflare protection. As of the current version, Playwright-based crawls are blocked by Cloudflare (HTTP 403 or a Cloudflare JS challenge page). The site works normally when accessed from the user's real browser.

**Current mitigation approach:** Users can log in to CC Music via Settings → Site Sessions. The login browser opens with a copy of the user's real Chrome cookies (including any existing `cf_clearance` cookie), and the resulting session is saved to `browser_state.json`. These cookies are loaded into the crawl browser at the start of each crawl, which may allow Cloudflare to pass the request.

**Status:** The Cloudflare bypass via session cookie transfer is the most promising approach available without a proxy or external service. If this fails, the crawler will return `not_found` for all results and the only way for the user to access CC Music listings is via the pre-populated "View" links (manual browser navigation).

If the crawler detects a Cloudflare challenge page (no meaningful result items, HTTP 403, or challenge-page content), it should raise `BotDetectedError` so the crawl engine can attempt a context reset and retry.

The crawl browser uses a persistent Chrome profile and `channel="chrome"` (real Chrome binary) with stealth applied.

## Known Limitations

- Cloudflare protection may block all automated access regardless of session cookies.
- If blocked, the "View" pre-populated links remain functional for manual lookup.
- CC Music's search may not surface all formats; results depend on their catalogue and search ranking.
