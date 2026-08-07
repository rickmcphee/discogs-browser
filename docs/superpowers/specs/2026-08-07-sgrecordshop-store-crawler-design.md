# Sound Garden Record Shop Store Crawler — Design Spec

_2026-08-07_

**Status:** Draft
**Branch:** `store-crawler-sgrecordshop`

## Overview

Add `sgrecordshop.com` (The Sound Garden — independent record store,
Baltimore/Syracuse) as a new `crawler_type="catalog"` source for the Store
tab. The platform is **FieldStack Omni** (confirmed via `<meta
name="generator" content="FieldStack Omni">` and `pc*`-style asset paths),
not Shopify — the first crawler in this codebase for that platform. No
bot-blocking was found (no Cloudflare, no `robots.txt` restriction; `/robots.txt`
itself 404s) across roughly 90 live requests made while investigating this
design, so this stays a plain-`httpx` `catalog` crawler — it does not need
the `catalog_browser`/Playwright plumbing angryyoungandpoor introduced, which
exists specifically to bypass bot detection, not for markup-parsing
convenience.

## Goals / non-goals

**Goals**
- Crawl the 14 "by genre" vinyl categories (Rock/Pop/Indie, Soul/Funk/R&B,
  Beats/Hip-Hop, Jazz/Fusion, Electronic, Goth/Industrial, Metal,
  Punk/Hardcore, Folk/Country/Americana, Blues, Dub/Reggae, World,
  Soundtracks, Experimental/Modern Classical), deduplicated by product ID.
- Extract artist, title, format, price, URL, and cover image from each
  purchasable listing.

**Non-goals**
- No "featured" categories (New Arrivals, Artist Signed, Clearance,
  Pre-Orders, New Releases) — confirmed live to total ~150-200+ paginated
  requests for full "genre + featured" coverage vs 66 for genre-only; the
  featured categories are treated as near-total subsets of the genre
  buckets, not verified exhaustively but accepted given the request-volume
  cost of confirming it.
- No used-condition coverage at all. The 4 "used records" categories
  (`Used Records LPs - New Arrivals` `c2792`, `LPs - All` `c2819`, `12"
  Singles` `c2836`, `7" Singles` `c2837`) are skipped entirely, and any
  price-list entry labeled `"Used"` (should one ever appear in a
  genre-category result) is not specially handled — out of scope per an
  explicit decision, not an oversight.
- No `catalog_browser`/Playwright — no bot-blocking was observed to justify
  the overhead.
- No retry/circuit-breaker logic for transient failures — no failure mode
  (429, throttle, etc.) was observed in ~90 live requests to design around.

## Technical grounding

### Platform and fetch mechanism

Category pages server-render an empty listing container
(`<div class="product-list" id="product-list"></div>`) and populate it via
AJAX (`bundles/scripts/all`'s `searchFilterable` module). Confirmed this
works in plain `httpx` with a persistent cookie jar, no browser:

1. `GET` the category URL using its **exact querystring**, e.g.
   `/c/2724/record-shop-rock-pop-indie?&so=9&page=1&af=-3011|-3010|-3008|-10|-2`.
   The `af=` tokens are opaque per-category filter IDs sourced from the
   site's own nav links, not derivable — e.g. `-3` marks "used condition"
   in the used-records categories' links, but this isn't a documented
   filter scheme, only an observed pattern; the categories in scope for this
   crawler are used exactly as linked, not reconstructed from filter IDs.
2. Scrape a per-request `SearchId` GUID out of an inline `<script>` block:
   `searchFilterable.init({..., SearchId: '6f2b4160-...' })`.
3. `GET /gsrp/{page}?{same querystring}&page={page}` with header
   `X-Search-Guid: <guid>`. Confirmed live: one `SearchId` from the
   category page's initial load is valid for **all** of that category's
   pages (tested pages 1-4 of a 4-page category with a single `SearchId`) —
   only one category-page GET is needed per category, not one per page.
4. Response: `{"data": {"data": "<html fragment>", "itemcount": "...",
   "pageNumber": N, "totalPages": N}}`. Loop `page` 1..`totalPages`.

**No bulk-fetch shortcut exists.** Tried `ps=`, `pagesize=`, `pageSize=`,
`itemsPerPage=`, `limit=`, `rpp=` as extra query params on `/gsrp/` —
all ignored, fixed at 20 items/page server-side. Unlike angryyoungandpoor's
`viewAll=yes`, there's no way to collapse a category to one request.

**Confirmed request volume for the 14 in-scope categories** (sampled live,
`itemcount`/`totalPages` from the `/gsrp/` response):

| Category | Items | Pages |
|---|---|---|
| Rock/Pop/Indie (2724) | 564 | 29 |
| Electronic (2738) | 90 | 5 |
| Jazz/Fusion (2756) | 84 | 5 |
| Folk/Country/Americana (2759) | 81 | 5 |
| Soul/Funk/R&B (2726) | 82 | 5 |
| Metal (2728) | 70 | 4 |
| Beats/Hip-Hop (2725) | 46 | 3 |
| Soundtracks (2765) | 38 | 2 |
| Punk/Hardcore (2758) | 38 | 2 |
| World (2762) | 26 | 2 |
| Blues (2767) | 11 | 1 |
| Experimental/Modern Classical (2753) | 13 | 1 |
| Dub/Reggae (2760) | 9 | 1 |
| Goth/Industrial (2773) | 7 | 1 |

Total: 66 `/gsrp/` requests + 14 category-page requests = **80 requests per
full crawl**.

### Listing markup and parsing

No HTML-parsing library exists in this codebase (angryyoungandpoor avoided
needing one by querying a live DOM via Playwright's `page.evaluate()`,
which isn't a fit here without adding `catalog_browser` overhead for no
bot-bypass reason). This crawler uses **targeted regex** against the
confirmed, stable repeating block:

```html
<div class="producttitlelink product-grid-variant" ...>
    <a href="/p/26467060/kylie-minogue-aphrodite" title="Kylie Minogue/Aphrodite">
        <img ... data-src="https://cache.fieldstackintelligence.com/images/2644/13220690-T.JPG" .../>
        <span class="product-title">Kylie Minogue</span>
        <span class="product-artist"><br />Aphrodite</span>
        <span class="see-more-format">Vinyl LP</span>
        ...
        <span itemprop="price">24.99</span>   <!-- or: -->
        <span class="product-variant-unavailable">Not available</span>  <!-- when unpurchasable -->
```

Confirmed gotchas, in order of how much they change the design:

1. **`product-title`/`product-artist` class names are semantically
   swapped.** `product-title` holds the *artist*; `product-artist` holds
   the *release title*. Confirmed on every sampled item (e.g. `product-title
   = "Kylie Minogue"`, `product-artist = "Aphrodite"`, matching the URL slug
   `kylie-minogue-aphrodite`).
2. **The grid truncates long text with `"..."`.** `product-artist` (the
   title field) is truncated often; `product-title` (the artist field) was
   never observed truncated in sampling, likely because artist names are
   shorter on average, not because it's structurally exempt. The reliable,
   untruncated source for the full text is the anchor's `title="Artist/Title
   @variant@variant"` attribute, confirmed to carry HTML entities (`&#39;`,
   `&amp;`, `&quot;`) requiring `html.unescape()` (stdlib, no new
   dependency).
3. **Splitting that `title` attribute on `/` is not safe in either
   direction, and this was caught by testing, not by inspection.** A first
   draft split on the *last* `/` before the first `@` (to handle multi-artist
   collabs like `"21 Savage / Metro Boomin/Savage Mode Ii"` →
   `artist="21 Savage / Metro Boomin"`, `title="Savage Mode Ii"`, correct).
   But live-testing that same rule against a second real item broke it:
   `"Elephant's Memory/Take It to the Streets (CLEAR W/ BLACK SWIRL
   VINYL)@Remastered"` — `"W/"` here is an abbreviation for "with" inside a
   color-variant description, not a delimiter, and a last-`/` split
   wrongly cuts `artist="Elephant's Memory/Take It to the Streets (CLEAR W"`,
   `title="BLACK SWIRL VINYL)"`. No positional `/`-split rule is correct for
   both cases simultaneously.

   **Fix:** use the `product-title` span (gotcha 2's artist source, already
   reliably untruncated) as a known, literal prefix to strip from the
   normalized `title` attribute string, rather than searching for a
   delimiter position at all:
   - `artist` = `product-title` span text, whitespace-normalized, unescaped.
   - `full` = anchor `title` attribute, same normalization.
   - If `full` starts with `artist + "/"`, strip that exact prefix; the
     remainder up to the first `@` is `title`.
   - Verified against both failing cases: `"21 Savage / Metro
     Boomin/Savage Mode Ii"` → strips `"21 Savage / Metro Boomin/"` →
     `title="Savage Mode Ii"`. `"Elephant's Memory/Take It to the Streets
     (CLEAR W/ BLACK SWIRL VINYL)@Remastered"` → strips `"Elephant's
     Memory/"` → `title="Take It to the Streets (CLEAR W/ BLACK SWIRL
     VINYL)"`. Both correct.
   - Fallback (prefix doesn't match verbatim — e.g. the artist span itself
     is truncated in some case not yet observed): revert to the
     weaker last-`/`-before-`@` split. Flagged as an accepted gap, same
     shape as angryyoungandpoor's own fallback gaps — not observed to
     trigger in ~90 live requests sampled, but not exhaustively ruled out.
4. **Format** comes from the `see-more-format` span. Confirmed values
   across three sampled genre categories: `"Vinyl LP"`, `"Vinyl 12&quot;"`
   (unescapes to `Vinyl 12"`) — no CDs/cassettes leak into these categories'
   results.
5. **Stock/availability gate: item has a price, or it doesn't.** An
   unpurchasable item replaces the price block with `<span
   class="product-variant-unavailable">Not available</span>` and has no
   `itemprop="price"` at all — confirmed live, and used as the sole
   availability signal. Finer-grained shipping-status text also exists on
   available items (`"In Stock"`, `"Out of Stock"`, `"Special Order. We
   will try to get it for you."`, `"Limited availability"`) but is **not**
   used as a gate — an item with a price is treated as purchasable
   regardless of which shipping-status string accompanies it, consistent
   with how existing Shopify crawlers gate on `variant.available` rather
   than parsing free-text status.

### Category overlap and dedup

Not exhaustively verified whether the 14 genre categories are disjoint
(same caveat angryyoungandpoor made about its own categories) — a record
could plausibly be miscategorized into two genres. Every yielded item is
deduplicated by product ID (parsed from `/p/{pid}/{slug}` in the anchor
`href`) before yielding, making the overlap question moot regardless of
which categories turn out to overlap.

## Design

```python
import html
import random
import re
from asyncio import sleep
from typing import AsyncIterator
import httpx
from config import load_config
from logging_config import get_logger

log = get_logger("sgrecordshop")

_SEARCH_ID_RE = re.compile(r"SearchId:\s*'([0-9a-f-]+)'")
_BLOCK_RE = re.compile(
    r'<div class="producttitlelink product-grid-variant".*?'
    r'(?=<div class="producttitlelink product-grid-variant"|\Z)', re.S)
_PID_RE = re.compile(r'/p/(\d+)/')
_TITLE_ATTR_RE = re.compile(r'<a href="[^"]+" title="([^"]+)"')
_PRODUCT_TITLE_RE = re.compile(r'product-title">\s*([^<]+)')
_FORMAT_RE = re.compile(r'see-more-format">\s*([^<]+?)\s*<span')
_PRICE_RE = re.compile(r'itemprop="price">([\d.]+)</span>')
_IMG_RE = re.compile(r'data-src="([^"]+)"')
_UNAVAILABLE_RE = re.compile(r'product-variant-unavailable')


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


class Crawler:
    site_name: str = "The Sound Garden"
    base_url: str = "https://www.sgrecordshop.com"
    crawler_type: str = "catalog"

    # path + querystring exactly as sourced from the site's own nav --
    # af= tokens are opaque per-category filter ids, not derivable.
    _CATEGORIES = [
        "/c/2724/record-shop-rock-pop-indie?&so=9&af=-3011|-3010|-3008|-10|-2",
        "/c/2726/record-shop-soul-funk-rnb?&so=9&af=-10|-2003|-2",
        "/c/2725/record-shop-beats-hip-hop?&so=9&af=-10|-2003|-2",
        "/c/2756/record-shop-jazz-fusion?&so=9&af=-3008|-10|-2",
        "/c/2738/record-shop-electronic?&so=9&af=-10|-2003|-2013|-2",
        "/c/2773/record-shop-goth-industrial?&so=9&af=-10|-2003|-2",
        "/c/2728/record-shop-metal?&so=9&af=-10|-2003|-2",
        "/c/2758/record-shop-punk-hardcore?&so=9&af=-10|-2036|-2003|-2",
        "/c/2759/record-shop-folk-country-americana?&so=9&af=-10|-2003|-2",
        "/c/2767/record-shop-blues?&so=9&af=-10|-2003|-2",
        "/c/2760/record-shop-dub-reggae?&so=9&af=-10|-2003|-2013|-2",
        "/c/2762/record-shop-world?&so=9&af=-10|-2003|-2",
        "/c/2765/record-shop-soundtracks?&so=9&af=-10|-2003|-2",
        "/c/2753/record-shop-experimental-modern-classical?&so=9&af=-10|-2003",
    ]

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        cfg = load_config()
        delay = float(cfg.get("crawl_delay_seconds", 30))
        seen_pids: set[str] = set()

        async with httpx.AsyncClient(base_url=self.base_url, follow_redirects=True) as client:
            for category_qs in self._CATEGORIES:
                category_path, qs = category_qs.split("?", 1)
                await sleep(random.uniform(delay * 0.5, delay))
                r = await client.get(f"{category_path}?{qs}&page=1")
                r.raise_for_status()
                m = _SEARCH_ID_RE.search(r.text)
                if not m:
                    log.warning("[sgrecordshop] no SearchId on %s, skipping category", category_path)
                    continue
                search_id = m.group(1)

                page, total_pages = 1, 1
                while page <= total_pages:
                    if page > 1:
                        await sleep(random.uniform(delay * 0.5, delay))
                    r = await client.get(
                        f"/gsrp/{page}?{qs}&page={page}",
                        headers={"X-Search-Guid": search_id},
                    )
                    r.raise_for_status()
                    payload = r.json()["data"]
                    total_pages = int(payload["totalPages"])
                    for item in self._parse_items(payload["data"]):
                        if item["pid"] in seen_pids:
                            continue
                        seen_pids.add(item["pid"])
                        yield item
                    page += 1

    @classmethod
    def _parse_items(cls, fragment_html: str) -> list[dict]:
        items = []
        for block in _BLOCK_RE.findall(fragment_html):
            if _UNAVAILABLE_RE.search(block):
                continue  # "Not available" -- no price, not purchasable
            pid_m, price_m = _PID_RE.search(block), _PRICE_RE.search(block)
            if not (pid_m and price_m):
                continue

            artist_m = _PRODUCT_TITLE_RE.search(block)
            artist = _norm(artist_m.group(1)) if artist_m else ""
            title_attr_m = _TITLE_ATTR_RE.search(block)
            full = _norm(title_attr_m.group(1)) if title_attr_m else ""
            prefix = artist + "/"
            if full.startswith(prefix):
                remainder = full[len(prefix):]
            else:
                head = full.split("@", 1)[0]
                _, _, remainder = head.rpartition("/")
            title = remainder.split("@", 1)[0].strip()

            fmt_m = _FORMAT_RE.search(block)
            img_m = _IMG_RE.search(block)
            items.append({
                "pid": pid_m.group(1),
                "artist": artist,
                "title": title,
                "format": _norm(fmt_m.group(1)) if fmt_m else "Vinyl",
                "price": float(price_m.group(1)),
                "currency": "USD",
                "url": f"{cls.base_url}/p/{pid_m.group(1)}/",
                "cover_image_url": img_m.group(1) if img_m else None,
            })
        return items
```

`crawl_delay_seconds` jitter (`random.uniform(delay * 0.5, delay)`) applies
before every request — the category-page load and every `/gsrp/` page —
same convention `shopify_catalog.iter_products()` uses. 80 requests per
full crawl (14 category-page GETs + 66 `/gsrp/` page GETs).

## Data model

`stock_items` gains no new column. Output maps directly to the existing
crawler-plugin dict shape (`url`, `price`, `currency`); `condition` is
always `None` since used-condition is entirely out of scope, same as every
other purely-new-stock crawler in this codebase.

## Error handling

- **Missing `SearchId`** on a category page (unexpected markup change) →
  log a warning and skip that category, not fail the whole run — each
  category is independent, no reason to abort the other 13 over one.
- **Any `httpx.HTTPStatusError`** (from either the category-page GET or a
  `/gsrp/` GET) propagates immediately, on the first occurrence — unlike
  `shopify_catalog.py`'s `iter_products()`, which retries a non-429
  failure up to `consecutive_failure_limit` times before propagating
  (pagination has no next item to fall through to, so a failed page is
  retried rather than skipped). This crawler reads `crawl_delay_seconds`
  for its jitter but never reads `consecutive_failure_limit` — there is
  no retry loop to bound. This is a real reduction in resilience relative
  to the Shopify path, not an equivalent alternative: at 80 sequential
  requests per crawl (14 categories × up to several pages each) versus
  1-3 for a typical Shopify site, a single transient failure here costs
  the whole crawl's output, not just one page's worth. Accepted for now
  since no failure mode was observed to design around during ~90 live
  requests made during this investigation; revisit if one shows up in
  production.

## Testing

First `catalog` crawler whose extraction logic parses an HTML string
rather than consuming JSON directly, so `test_sgrecordshop_crawler.py`
needs saved fixtures — same fixture-based spirit as the
Amazon/angryyoungandpoor pattern, but simpler (no Playwright, no
`page.set_content()`): a saved `/gsrp/` JSON response fixture
(`backend/tests/fixtures/crawlers/sgrecordshop/rock_pop_indie_page1.json`,
trimmed to ~5 representative blocks) covering the specific edge cases found
live during this investigation:

- a normal single-artist item,
- the `"CLEAR W/ BLACK SWIRL VINYL"` case (artist `Elephant's Memory`) —
  regression coverage for the parsing bug found and fixed above,
- the multi-artist-slash case (`"21 Savage / Metro Boomin"`),
- an unavailable item (`product-variant-unavailable`, no price).

Assert `_parse_items()` on that fixture produces the correct artist/title
split for all three available items, and that the unavailable item is
excluded from the result entirely.

## Frontend impact

None. `crawler_type="catalog"` already fits the existing `catalogCrawlers =
crawlers.filter(c => c.crawler_type === 'catalog')` bucketing in
`Settings.tsx` untouched — unlike angryyoungandpoor's `catalog_browser`
addition, this crawler needed no changes to the `crawler_type` union or the
Settings/RecordBrowser filters.

## Open items / accepted gaps

- Artist/title split falls back to a weaker last-`/`-before-`@` heuristic
  if the `product-title` span doesn't appear verbatim as a prefix of the
  title attribute — not observed in ~90 live requests sampled, but not
  exhaustively ruled out.
- Genre-category overlap not exhaustively verified — pid dedup makes it
  moot regardless of the answer.
- No retry/circuit-breaker logic for transient failures — no failure mode
  was observed to design around during this investigation; revisit if one
  shows up in production.
- "Used" condition and the 4 used-record categories are entirely out of
  scope by explicit decision — a Sound Garden used copy will never appear
  in this app's stock data.
- "Featured" categories (New Arrivals, Artist Signed, Clearance,
  Pre-Orders, New Releases) are skipped on the assumption that they're
  near-total subsets of the 14 genre categories — sampled but not
  exhaustively verified against each other.
