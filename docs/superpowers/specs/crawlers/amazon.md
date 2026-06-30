# Amazon Crawler Spec

**Site:** Amazon.com  
**URL:** https://www.amazon.com  
**File:** `backend/crawlers/amazon.py`

## Purpose

Find a vinyl/CD listing on Amazon matching a Discogs release and return the current price.

## Search URL

`https://www.amazon.com/s?k={artist}+{title}+{format}&i=popular`

Built by `search_url(cls, release)` using `clean_search_text` on artist and title, plus the raw format string. `search_url` is also used as the "View" link stored in the DB before any crawl.

## Text Cleaning

`clean_search_text(text)` (in `crawler.py`) strips Discogs disambiguation suffixes like `(2)`, colons, and URL-unsafe characters (`?#&=+%`), then collapses whitespace.

`_strip_stop_words(text)` removes common prepositions, conjunctions, and articles (`a`, `an`, `the`, `of`, `in`, `on`, `at`, `to`, `for`, `and`, `or`, `but`, `with`, `from`, `by`, `as`, `is`). If removing stop words would leave an empty string, the original is returned unchanged.

`Crawler._artist(release)` applies `clean_search_text` then `_strip_stop_words`; returns `""` if the artist is empty or `"various"` (so Various Artists releases search by title only).

## Title Variants

`_title_variants(title)` controls retry behaviour on no result:
- Title ≤ 5 words: returns `[title]` — one attempt.
- Title > 5 words: returns `[title, short]` — tries full title first, then a 3-word stop-word-stripped abbreviation.

## Result Filtering

Scans `[data-component-type="s-search-result"]` items (up to 10). For each item:

1. Locate `[data-cy="price-recipe"] a.a-text-bold` — the format link (e.g. "Vinyl", "Audio CD"). Skip if absent.
2. Check the format link text against `fmt_keywords` (derived from `_amazon_format_keywords(format)`). Skip if no keyword matches.
3. Extract `h2` heading text. Accept only if the first word of artist **or** the first word of title appears in the heading (case-insensitive). Skip if neither matches.
4. Take the `href` from the format link as the product URL.

Accept the first passing item. If none pass after all title variants, return no result.

## Format Keyword Map

```python
_FORMAT_MAP = {
    "vinyl":    ["vinyl"],
    "cd":       ["audio cd", "cd"],
    "cassette": ["cassette", "audio cassette"],
    "blu-ray":  ["blu-ray"],
    "dvd":      ["dvd"],
    "box set":  ["box set"],
}
```

`_amazon_format_keywords(discogs_format)` returns the keyword list for the format, falling back to `[discogs_format.lower()]` if no entry matches.

## Price Extraction

`extract_price(page, fmt_keywords)` is called on the product detail page. Three fallback levels, all scoped to buybox containers to avoid matching carousel/recommendation prices:

**Level 1 — scoped offscreen spans** (tried in order):
- `#corePrice_feature_div .a-offscreen`
- `#unifiedPrice_feature_div .a-offscreen`
- `#apex_offerDisplay_desktop .a-offscreen`
- `#priceblock_ourprice`
- `#priceblock_dealprice`
- `#desktop_buybox .a-offscreen`

**Level 2 — split spans** scoped to `#corePrice_feature_div`, `#unifiedPrice_feature_div`, `#desktop_buybox`: combines `.a-price-whole` + `.a-price-fraction`.

**Level 3 — aria-label buttons**: scans `a.a-button-text[id^='a-autoid']` buttons. Skips any button whose `aria-label` doesn't contain a `fmt_keyword` (prevents selecting the CD price when looking for Vinyl). Extracts `$X.XX` via regex.

Returns `float` or `None`.

After navigating to the product page, `vinyl_url = page.url` captures the post-redirect canonical URL.

## Bot Detection

If Amazon returns a CAPTCHA or bot interstitial (detected via `_BOT_SELECTORS`), raises `BotDetectedError`. The crawl engine resets the browser context and retries.

The crawl browser uses a persistent Chrome profile (`chrome_profile/`) with `channel="chrome"` (real Chrome binary) and `playwright_stealth`. Saved session cookies from `browser_state.json` are loaded on context creation.

## Known Limitations

- Amazon's catalogue skews toward new stock; rare vinyl may not appear.
- Price extraction returns the primary "new" listing price only; marketplace and used prices are not captured.
- If no item passes the format + title filter, the result is `not_found`.
