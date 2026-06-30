# CC Music Crawler Spec

**Site:** CC Music (via eBay)  
**eBay seller:** `collectorschoicemusic`  
**File:** `backend/crawlers/ebay.py`

## Purpose

Find a vinyl/CD listing on CC Music's eBay storefront matching a Discogs release and return the current price. Uses the eBay Browse API rather than Playwright — no browser needed.

## OAuth

Client credentials flow against `https://api.ebay.com/identity/v1/oauth2/token`. Token cached module-level with 60-second expiry buffer; auto-refreshed. Credentials (`ebay_app_id`, `ebay_cert_id`) read from `config.json` on each search call.

## Search Strategy

Endpoint: `GET /buy/browse/v1/item_summary/search`  
Filter: `sellers:{collectorschoicemusic},buyingOptions:{FIXED_PRICE}`  
Sort: `price+shippingCost` (lowest BIN price first)  
Limit: `3` (to give validation multiple candidates)

**Query selection:**
- If the release has a `barcode` (digits-only, stored during collection refresh): use the barcode as the sole query. Barcode searches are precise and do not require artist/title matching.
- If no barcode: fall back to `"{artist} {title}"` constructed via `clean_search_text`.

## Result Validation

`_pick_matching_item(items, release)` checks each of the up to 3 candidates before accepting:

1. **Artist match** — at least 50% of the artist's words must appear in the listing title (case-insensitive). Skipped if artist is empty.
2. **Title match** — at least 50% of the title's words must appear in the listing title.
3. **Format check** — the release's Discogs `format` field (e.g. `"Vinyl"`, `"CD"`, `"Cassette"`) is looked up in `_FORMAT_KEYWORDS`. If a match is found, the listing title must contain at least one of the corresponding whole-word patterns (matched via `re.search` with `\b` word boundaries). Unknown or absent formats skip this check.

```python
_FORMAT_KEYWORDS = {
    "Vinyl":    [r"\bvinyl\b", r"\blp\b", r"\brecord\b"],
    "CD":       [r"\bcd\b"],
    "Cassette": [r"\bcassette\b", r"\btape\b"],
    "DVD":      [r"\bdvd\b"],
    "Blu-ray":  [r"\bblu.?ray\b"],
}
```

Returns the first passing candidate. If none pass, returns no result for this release.

## URL Construction

`itemWebUrl` from the API response is preferred. Falls back to `https://www.ebay.com/itm/{legacyItemId}` if absent or not an `https://www.ebay.com` URL. Final fallback: `search_url(release)` (a direct eBay storefront search URL).

## No Playwright Dependency

`async def search(self, release, page)` ignores the `page` argument and manages its own `httpx.AsyncClient`. The `page` argument is `None` for this crawler.

## Known Limitations

- CC Music's eBay inventory may not include all formats or pressings.
- Barcode lookup is only as accurate as Discogs' identifier data; some releases lack barcodes.
- Validation thresholds (50% word overlap) may occasionally accept a wrong result for short or ambiguous titles, or reject a correct one for heavily abbreviated listings.
