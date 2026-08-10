# Cleopatra Records store crawler design

Date: 2026-08-09
Branch: `claude/cleorecs-vinyl-crawler-265d08`

## Problem

Cleopatra Records (`cleorecs.com`) is a Los Angeles reissue and catalog label
whose vinyl output — across its own imprint plus Purple Pyramid, Deadline
Music, Goldenlane, Kung Fu, New Red Archives, Magna Carta, X-Ray and others —
is not covered by any existing crawler. Its store is a standard Shopify
storefront, so it belongs to the same family as the 33 Shopify `catalog`
plugins already in `backend/crawlers/`.

Two things make it more than a copy of one of those: `vendor` is the imprint
on **every** product rather than the artist, so artist attribution rests
entirely on title parsing; and the `vinyl-1` collection carries a large
population of one-off **vinyl test pressings** alongside ordinary retail
stock, plus a tail of posters, books, apparel bundles and discs.

## Scope

Add `backend/crawlers/cleorecs.py` as a `crawler_type="catalog"` plugin
covering the full `/collections/vinyl-1` collection — every available vinyl
variant, fetched in 16 GETs per sync (15 product pages plus the terminating
empty page) via the existing `shopify_catalog.iter_products()`.

The terms this crawl operates under are set out in
[Crawl citizenship and `robots.txt` compliance](#crawl-citizenship-and-robotstxt-compliance)
below. That section of
`docs/specifications/shaping/2026-08-09-amoeba-store-crawler-design.md` is
normative for every store crawler in this repository; what follows here is
this site's specific finding under it, not a restatement of the policy.

**Non-goals**

- **No browser.** The collection's `products.json` is served to plain `httpx`
  with no Cloudflare gate and no bot interstitial. `catalog_browser` would be
  strictly more expensive for no gain.
- **No UCP/MCP integration.** See "Why not the store's own agent API" below.
- **No per-artist collection crawls.** See "Why not per-artist collections".
- **No non-vinyl formats.** CD, cassette, DVD/Blu-ray, books, posters,
  apparel and bundles are filtered out, even where they appear inside the
  vinyl collection.
- **No pre-order detection.** Unlike `seasonofmist.py` and
  `flatspotrecords.py`, this store's pre-orders already report
  `available: true` (30 such products live), so they flow through the normal
  path with no `body_html` or tag sniffing.
- **No window on the item count.** See "Queue fan-out" below, which states the
  cost this accepts.

## Technical grounding

All figures below were confirmed against the live site on 2026-08-09 by
paginating `/collections/vinyl-1/products.json?limit=250` to exhaustion
(15 pages, page 16 empty).

### Collection shape

| Measure | Count |
|---|---|
| Products in `vinyl-1` | 3,615 |
| …with at least one available variant | 2,919 |
| Available variants across those | 3,251 |
| After the non-vinyl filter (below) | 2,870 products / 3,151 variants |

`product_type` distribution over the whole collection: `LP` 3,324, `SP` 240,
`BND` 30, `CD` 9, `BK` 4, `PS` 2, and one each of `PO`, `DVD`, `BR`, `TB`,
plus 2 empty. Prices on available variants run \$5.98–\$750.00, median
\$26.98; none are zero, so 20 Buck Spin's zero-price promo filter has no
analogue here.

### `vendor` is the imprint, never the artist

Top vendors: `Cleopatra Records` (2,269), `Purple Pyramid Records` (344),
`Deadline Music` (333), `Goldenlane Records` (134), `Kung Fu Records` (118).
There is no product on which `vendor` names the artist. The repo's usual
`return (vendor or "").strip(), title.strip()` fallback is therefore not a
safe default here — it would emit `Cleopatra Records` as an artist, which can
never match a Discogs library entry.

Tags are not a usable substitute. They mix artist names, genres, track
titles and marketing labels without distinction — one compilation carries 20
tags spanning 15 artist names, a holiday label and a format label.

### Title shape

The dominant form is `Artist - Album (Colour/Format)`, e.g.
`UFO - A Conspiracy Of Stars (Colored Double Vinyl LP)`. Measured over the
2,870 kept products:

| Split behaviour | Count |
|---|---|
| Splits on `" - "` (the repo's current `_TITLE_RE`) | 2,680 |
| Recovered by widening the class to `[-–—]` | +29 |
| No separator at all after both | 161 |

Three specific hazards, each confirmed live:

- **Hyphenated artist names.** 18 kept artists contain an internal hyphen
  with no surrounding space: `Anti-Flag`, `Blink-182`, `Buck-O-Nine`,
  `Ann-Margret`, `Eek-A-Mouse`, `B-Movie`, `Love-Hate`, `Huw Lloyd-Langton`
  and others. The whitespace-anchored form the repo already standardised on
  (`(?:\s+-\s*|\s*-\s+)`) handles these; the older plain `\s*-\s*` form does
  not.
- **En-dash separators.** 34 titles use `–` rather than `-`, including
  `U.K. Subs – Endangered Species`, `Third World – Under The Magic Sun`,
  `Pink Fairies – Screwed Up` and `Prog Collective – Dark Encounters`. Only
  `bigscarymonstersusa.py` currently allows this character.
- **Separators inside the trailing parenthetical.** 11 titles have no artist
  prefix but *do* contain a `" - "` within their trailing bracket, so a naive
  split captures nonsense:
  `Danzig Sings Elvis (Gatefold Green Vinyl LP - Signed by Glenn Danzig)`
  yields the artist `Danzig Sings Elvis (Gatefold Green Vinyl LP`. Others in
  this group: three more `Danzig Sings Elvis` colour variants, two
  `Mourning Noise (… - Signed by Steve Zing)`, `Mammoth WVH (… - Imported)`,
  `UK Subs: Work In Progress (… - Imported)`.

The 161 residual no-separator products are overwhelmingly the label's own
compilations, tribute records and soundtracks — `Punk Rock Christmas`,
`A Very Metal Christmas`, `Gothic Noir`, `A Tribute to Styx`,
`Psychobilly Goes Pop`, `Symphonic Music of Depeche Mode`,
`A Goth-Industrial Tribute To The Smashing Pumpkins` — with roughly a dozen
self-titled or colon-form exceptions (`The Brains`, `The Devil's Carnival`,
`Danzig: 777 I Luciferi`).

### Variants

3,151 available variants across 2,870 products. **2,650 of those variants are
Shopify's `"Default Title"` placeholder**; the 196 genuinely multi-variant
products carry pressing colours (`Red Vinyl` ×84, `Blue Vinyl` ×72,
`Blue Marble`, `Black (Box Version)`, …). 139 of the 196 price every colour
identically. 332 of the 477 variants on multi-variant products carry their own
`featured_image`, so `resolve_cover_image()`'s variant-first preference earns
its keep here.

This is why the row-per-variant rule needs a placeholder guard that
`subpopmegamart.py` and `twentybuckspin.py` lack: those two append
`variant["title"]` unconditionally, which on this store would stamp
`— Default Title` onto 2,650 of 3,151 rows.

### Test pressings

533 kept products carry a `Test Pressing` / `Vinyl Test Pressing` tag; 542
say "Test Pressing" in the title. **The tagged set is a strict subset of the
titled set — zero tagged products omit it from the title**, and 9 more
declare it in the title without the tag. They are kept, and keeping the title
verbatim is what marks them; no tag inspection and no title decoration is
required. Their prices (median \$125, max \$750) sit an order of magnitude
above ordinary stock, which is the point of marking them.

### Why not the store's own agent API

`robots.txt` and `/agents.md` both direct agents to the store's UCP endpoint
at `POST /api/ucp/mcp`. A `tools/list` call against it returns:
`search_catalog`, `lookup_catalog`, `get_product`, plus the cart/checkout/order
tools. `search_catalog` takes a free-text `query` with buyer context — it is an
*intent search*, not an enumeration API, and `lookup_catalog`/`get_product`
both require identifiers we would have to already possess. There is no call
that returns "everything in this collection," so covering 2,870 items would
mean synthesising queries and never knowing what was missed. It would also add
an MCP client dependency the backend has no precedent for.

The same `/agents.md`, under "Read-Only Browsing (No Authentication
Required)", affirmatively names `GET /collections/{handle}/products.json` as a
supported path for read-only agents. That is the path this crawler uses. UCP
is the right channel for *buying*, which this app never does.

### Why not per-artist collections

The store publishes roughly 400 per-artist collections (`/collections/danzig`,
`/collections/the-brains`, …). Crawling them would give authoritative artist
attribution and resolve all 161 fallback cases exactly. It would also cost
400+ requests per sync — about three hours at `crawl_delay_seconds = 30` —
i.e. 27× the request load to correct 5.6% of rows. Rejected.

## Crawler design

`backend/crawlers/cleorecs.py`, following `twentybuckspin.py`'s shape:

```python
class Crawler:
    site_name: str = "Cleopatra Records"
    base_url: str = "https://cleorecs.com"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]: ...
```

`_COLLECTION_SLUG = "vinyl-1"`, iterated with
`shopify_catalog.iter_products()` unchanged — that helper already supplies the
`crawl_delay_seconds` pacing, the `consecutive_failure_limit` retry policy,
the fail-fast-on-429 rule and `report_page()` progress reporting.

### Filtering

Applied per product, before parsing:

1. Drop `product_type` in `{BND, BK, PS, PO, DVD, BR, TB, CD}`.
2. Drop titles matching `poster|hardback book|tote bag|shirt|hoodie|sweater|bundle`
   (case-insensitive). `product_type` alone is correct on today's data, but
   20 Buck Spin's live experience — a tote bag typed `VINYL` — is why the
   title check is there too.

Applied per variant: skip anything with `available: false`.

### Parse rules

A pure `@classmethod` so it is unit-testable without any HTTP:

- **Split point** is found on the title with *trailing parentheticals
  stripped* — repeatedly, right to left, stopping at the first trailing text
  that is not bracketed — using
  `^(?P<artist>.+?)(?:\s+[-–—]\s*|\s*[-–—]\s+)(?P<album>.+)$`. The album text
  returned is the **original** title minus the matched artist prefix — the
  parentheticals are kept.

  Keeping them is deliberate, not incidental: `db.py`'s
  `_library_match_fragment` matches `LOWER(s.title) = LOWER(c.title) OR
  LOWER(s.title) LIKE LOWER(c.title) || ' %'`, so
  `A Conspiracy Of Stars (Colored Double Vinyl LP)` still matches a catalog
  title of `A Conspiracy Of Stars`, while the colour/format text stays visible
  to the collector. This matches `amoeba.py` keeping its `(LP)` and
  `[Coke Bottle Clear]` tokens.

  This rationale is one-sided, though: it only weighs the `_library_match_fragment`
  prefix match, not `ebay_api.pick_matching_item`, which this crawler's own
  queue fan-out feeds (see "Queue fan-out" below). That function splits both
  the release title and the listing title on whitespace and requires at
  least half the words to intersect — it never strips parentheses. A row like
  `A Conspiracy Of Stars (Colored Double Vinyl LP) — Red Marble` intersects a
  clean eBay listing (`UFO A Conspiracy Of Stars Vinyl LP`) at roughly 5 of
  11 words, under the 50% gate, so the eBay match is dropped. This is a
  fleet-wide convention — every sibling crawler that keeps parentheticals has
  the same exposure — not a regression introduced here. Cleopatra is unusual
  only in degree: nearly every title carries such a suffix, and multi-variant
  products compound it further with the appended `— {variant_title}`. Accepted
  as a known tradeoff, consistent with the rest of the fleet, not fixed here.

- **No separator** after stripping → `artist = "Various"`, album = the full
  original title. `angryyoungandpoor.py` sets the precedent for its V/A
  category, though it emitted `"Various Artists"` there (a pre-existing
  issue on that crawler, out of scope here and fixed separately in PR #93). `"Various"` — not `"Various Artists"`
  — is the literal string Discogs' own entity uses, and three consumers
  depend on that exact spelling: `amazon.py`'s `Crawler._artist()` only
  special-cases the literal `"various"` case-insensitively; `db.py`'s
  `_library_match_fragment` does an exact `LOWER()` equality against the
  catalog artist; and `ebay_api.pick_matching_item` requires at least half of
  the artist's words to appear in the listing title, which `"various"` and
  `"artists"` essentially never do. `"Various Artists"` would satisfy none
  of the three, silently breaking library matching and eBay search for every
  row that falls into this branch. This is right for the large majority of
  the 161 (they are compilations and tributes). It mis-attributes the ~12
  self-titled cases; that is accepted rather than papered over.

- **`vendor` is never consulted.** Stated explicitly because every sibling
  crawler falls back to it and this one must not.

- **Title** is `album` when the variant title is `"Default Title"`, else
  `f"{album} — {variant_title}"`.

- **`format` is `"Vinyl"` unconditionally**, including for the 187 `SP`
  7" products. This is a consumer requirement, not a convention:
  `ebay_api.FORMAT_KEYWORDS` and `FORMAT_CATEGORY_IDS` are keyed on
  `Vinyl`/`CD`/`Cassette`/`DVD`/`Blu-ray`, so emitting `7"` would resolve both
  lookups to `None` and silently drop the keyword filter *and* the eBay
  category constraint for those rows; `amazon.py` would splice `7"` into its
  literal search string.

- **price** — `float(variant["price"])`, `None` on failure, matching the
  sibling crawlers.
- **currency** — `"USD"`.
- **url** — `f"{base_url}/products/{handle}"` (product-level; Shopify variant
  URLs are not needed and the product page shows all colours).
- **cover_image_url** — `resolve_cover_image(product, variant)`.

Registration is automatic: `main.py`'s startup loop reads `site_name` /
`crawler_type` / `requires_discogs_release` off every module in
`backend/crawlers/` and calls `register_crawler()`. No wiring changes.

## Queue fan-out

3,151 stock items × 3 eligible release crawlers (amazon, ebay, ebay_general;
`discogs_marketplace` is excluded by its `requires_discogs_release = True`)
= **~9,450 `crawl_queue` jobs per sync**, drained at roughly 4 jobs/minute —
about 39 hours.

**No window is applied**, unlike the amoeba crawler's 1,000-item cap. Two
reasons:

- This is a label store, like the other 33 Shopify plugins, none of which are
  windowed; `angryyoungandpoor.py` ships ~4,400 items unwindowed.
- The starvation risk that motivated amoeba's window was fixed structurally
  afterwards. `claim_crawl_queue_batch`'s `ORDER BY (item_key IS NOT NULL)`
  puts every pending release row ahead of every pending stock-item row, so a
  user's own collection crawl cannot be delayed by this store's burst.

**Consequence to accept:** cross-site price comparison for Cleopatra's own
rows lags by a day or more after each sync. The store's own price is written
immediately by `replace_stock_items()` and is unaffected.

## Testing

`backend/tests/test_cleorecs_crawler.py`, on `test_twentybuckspin_crawler.py`'s
pattern — `respx`-mocked `products.json` responses and hand-written product
literals, so no live site and no bot-detection risk. Every literal is taken
from a confirmed-live product.

Cases:

- plain `Artist - Album (Colour Vinyl LP)` → correct split, parenthetical kept
- en-dash `U.K. Subs – Endangered Species` → correct split
- `Anti-Flag - Die For The Government (…)` → artist not clipped to `Anti`
- `Danzig Sings Elvis (Gatefold Green Vinyl LP - Signed by Glenn Danzig)` →
  no split inside the parenthetical
- `Punk Rock Christmas (Black Vinyl LP Test Pressing)` → `Various`,
  title kept verbatim so the test-pressing marking survives
- `"Default Title"` variant → no ` — ` suffix
- two-colour product → two rows, distinct titles, distinct
  `cover_image_url` from each variant's `featured_image`
- unavailable variant → skipped
- `PS` poster, `BND` shirt bundle, `BK` book → all dropped
- a `VINYL`-typed tote bag → dropped by the title filter
- an `SP` 7" product → `format == "Vinyl"`
- pagination: two pages then an empty page terminates the crawl

## Crawl citizenship and `robots.txt` compliance

Per the normative section of the amoeba spec. This site's finding:

- `robots.txt`'s `User-agent: *` group is `Allow: /`. Its disallows cover
  `/admin`, `/cart*`, `/checkout*`, `/checkouts/`, `/orders`, `/account`,
  `/services`, `/sf_*`, `/cart.js`, `/recommendations/products`, and
  `sort_by`/multi-filter crawl traps. **None of these covers
  `/collections/vinyl-1/products.json`**, the only path this crawler requests.
- `/agents.md` names `GET /collections/{handle}/products.json` explicitly
  under "Read-Only Browsing (No Authentication Required)".
- Both documents require that checkout and payment never be completed without
  contemporaneous human approval. Track Tempest satisfies this trivially: it
  links out to the product page and never transacts, holds no cart, and stores
  no payment method.
- Load: 16 GETs per sync (15 product pages plus the terminating empty page),
  paced at `random.uniform(delay * 0.5, delay)` with `crawl_delay_seconds`
  defaulting to 30s. No detail-page fan-out. No retry storms —
  `iter_products()` fails fast on 429 and gives up after
  `consecutive_failure_limit` on anything else.
- If Cleopatra blocks this crawler, adds a `Disallow` covering this path, or
  asks us to stop, the response is to disable the plugin.

## Runtime/agent document impact

No `.agents/INPUTS.md`, `.agents/OUTPUTS.md` or `.agents/INSTRUCTIONS.md`
exist in this repo. This change adds no new trigger — `_sync_stock` already
enumerates `catalog` plugins — and no new inbound interface. It adds one new
outbound host (`cleorecs.com`), which would belong in `.agents/OUTPUTS.md` if
that file existed.

`backend/version.py`'s `VERSION` goes `3.13` → `3.15` in the implementing PR
(not `3.14`: `origin/main` took `3.14` via a different, separately-merged PR
#91 after this branch's merge-base, so `3.14` was already taken by the time
this shipped — see commit `bfc37d6`).
