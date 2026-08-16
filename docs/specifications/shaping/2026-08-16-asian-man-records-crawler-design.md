# Asian Man Records store crawler design

Date: 2026-08-16
Branch: `claude/asian-man-records-crawler-074aa7`

## Problem

Asian Man Records (`asianmanrecords.com`), Mike Park's Bay Area punk/ska
label (Alkaline Trio, AJJ, Less Than Jake-adjacent catalog, 30 years of
releases), is not covered by any existing crawler. It is a standard
Shopify storefront — same family as the 34 label-store `catalog` plugins
already in `backend/crawlers/`, most directly comparable to
`jackpotrecords.py` (full-catalog pull + inclusion gate, not a narrower
per-format collection).

What makes it more than a copy of `jackpotrecords.py`: this store's
product titles follow a genuinely different convention —
`ARTIST "Album" FORMAT` (quoted album title), not the hyphen-only
`ARTIST - Album` form every sibling crawler parses. It also bundles
alternate purchase options (CD, cassette, promo slipmat, apparel-bundle
sizing) as Shopify *variants* of the vinyl product itself, a structure
none of the single-variant-per-product label stores (`jackpotrecords.py`,
`turntablelab.py`) need to handle.

## Scope

Add `backend/crawlers/asianmanrecords.py` as a `crawler_type="catalog"`
plugin, iterating the full catalog (`all-products` collection) via
`shopify_catalog.iter_products()` — no new shared code needed — filtered
by an in-process inclusion gate, with a title parser suited to this
store's quoted-album convention and a per-variant filter for its bundled
non-vinyl purchase options.

**Non-goals**

- **No browser.** Confirmed live: `products.json` is served to plain
  `httpx` with no Cloudflare gate and no bot interstitial.
- **No UCP/MCP integration.** Same reasoning as `jackpotrecords.py`'s
  "Why not the store's own agent API": `/agents.md` names `search_catalog`-
  style tools for buyer-approved checkout, not a bulk catalog dump.
- **No CD/cassette/apparel coverage.** This app's stock pipeline is
  vinyl-only by convention (`format: "Vinyl"` hardcoded across every
  sibling crawler); the inclusion gate and per-variant filter both exclude
  them (see below).
- **No test-press/acetate coverage.** No structural signal distinguishes
  a genuine reissue pressing from a one-off collector item on this
  store's `product_type`/tag data (see "Accepted noise" below).

## Technical grounding

All figures below were confirmed against the live site on 2026-08-16, by
fetching and fully paginating `/collections/all-products/products.json`
(251 products; `collections.json`'s reported `products_count: 381` is
stale — the same class of drift `jackpotrecords.py`'s spec found for
`all-vinyl`).

### Why the full catalog, not a per-format collection

`collections.json` (`GET /collections.json?limit=250`) lists the store's
own vinyl collections, and they're messier than Jackpot's: two different
collections are both titled "12-INCH VINYL" (handles `12-inch-vinyl-1`,
`12-inch-vinyl-2`), and a collection titled "7-INCH VINYL" has the handle
`12-inch-vinyl`. Depending on any of these by handle would be fragile and
the titles alone don't disambiguate which is current. `all-products`
(handle `all-products`, 251 products by full pagination) is the one
collection whose name unambiguously means the whole catalog, so this
crawler gates on product data instead of trusting the store's collection
taxonomy.

### Format filtering

**Gate:** `product_type in {"12-INCH VINYL", "7-INCH VINYL"}` OR either
tag is present (case-sensitive exact match, both forms confirmed
consistently uppercase on every observed product).

| Measure | Count |
|---|---|
| Products total (`all-products`) | 251 |
| Gate result (`product_type` or tag) | 149 |
| Non-vinyl (CDs, apparel, stickers, buttons, posters, patches, books, misc.) | 102 |

Checked for gaps in both directions against a broader title-word regex
(`\bvinyl\b`, `\d+\s*"`, `\d+\s*inch`) and found none that the gate above
misses among genuine vinyl products — the handful of products where
`product_type` says vinyl but the *tags* array doesn't (e.g. `GRUMPSTER -
"Honeydew" 12" VINYL + T-SHIRT`, tagged only `NEW RELEASE`) are still
caught because the gate checks `product_type` directly, not tags alone.

**Accepted noise, not specially filtered:** 2 `TEST PRESSES`-typed
products (`JONAH RAY "You Can't Call Me Al" 12" etching`, `HAPPY WAGS
"S/T" Acetate`) are excluded — neither carries a `12-INCH VINYL`/`7-INCH
VINYL` tag or product type, and both are one-off collector formats (an
etched B-side, a lacquer test pressing) rather than a standard commercial
pressing. Not worth a special-case rule for 2/151 products, same class of
call `jackpotrecords.py` makes for its own 4/3,080 CD-priced noise.

### `vendor` is not a usable artist fallback

`vendor` is `"Asian Man Records"` on 250/251 products (one outlier is
`"ASIAN MAN RECORDS"`, same value differently cased) — the label's own
name, not an artist, on every product regardless of who actually recorded
it. Same finding and same non-fallback decision as `jackpotrecords.py`.

### Title parsing: quoted album, not hyphen-only

This store's title convention is `ARTIST "Album" FORMAT`
(`KOREA GIRL "Korea Girl" 12" VINYL`), not the hyphen form
`cleorecs.py`/`jackpotrecords.py` parse. Some titles also carry a
`PRE ORDER:` and/or `AMR DISTRO:` prefix ahead of the artist — the latter
marks a title as another label's release resold through Asian Man's own
distro service (Jeff Rosenstock, Elvis Costello, Soccer Mommy, Skankin'
Pickle, and others — 11 products), included in scope: it's real
purchasable stock on this storefront, same as any Asian Man release, and
a user's collection can just as easily include one of those records.

**Parsing, in order:**

1. Strip a leading `PRE ORDER:` and/or `AMR DISTRO:` (one or more
   colons — the live site has a `PRE ORDER: AMR DISTRO:: AJJ - ...`
   typo with a doubled colon) prefix, case-insensitive. A `PRE ORDER:`
   prefix also flags the product for the availability rule below.
2. Primary: `^(?P<artist>.+?)\s*[-–—]?\s*"(?P<album>[^"]+)"` — matches
   both the plain quoted form and the optional-hyphen variant
   (`GRUMPSTER - "Honeydew"`, `SMOKING POPES- "Stay Down"` with the
   hyphen glued to the word). Covers 133/149 gated products.
3. Fallback: `cleorecs.py`'s hyphen-split regex
   (`^(?P<artist>.+?)(?:\s+[-–—]\s*|\s*[-–—]\s+)(?P<album>.+)$`) for
   titles with no quotes at all (`MU330 - S/T 12" VINYL`,
   `THE CHINKEES - Are Coming 12" VINYL`), followed by stripping a
   trailing format suffix from the album half:
   `\s+(?:DOUBLE\s+)?\d{1,2}\s*(?:"|INCH\b).*$` — covers only the
   digit+quote/digit+INCH forms actually confirmed live on this store's
   no-quote titles (every one of the 8 matched products uses one of
   these two forms; none uses a bare `LP`/`EP` suffix with no preceding
   digit). A bare `LP`/`EP` branch was considered and dropped: checked
   against the full 251-product catalog snapshot and no gated,
   quote-free, hyphen-split title needs it, so adding it would be
   untested speculative surface with nothing live to verify it against.
   Covers 8 more products, including 3 self-titled releases where the
   "album" is the literal `S/T` shorthand (`MU330 - S/T` → artist
   `MU330`, album `S/T`) — kept as-is, same informal convention
   `jackpotrecords.py` passes through unresolved when a title lacks a
   real album name.
4. Neither pattern matches → skip. 8/149 products (5.4%): bare
   artist-plus-format titles with no album at all (`MAGUMA TAISHI 7"`,
   `SMALL CRUSH 7 INCH`), unresolvable multi-artist splits with no album
   name (`CATBITE / MIKE PARK SPLIT 12" VINYL`,
   `HARD GIRLS / SUMMER VACATION SPLIT 7"`), a compilation with no
   artist/album structure (`V/A GILMAN STREET RIPOFFS(A Tribute To
   DOOKIE)`), and 3 self-titled releases with no separator at all
   (`JOYCE MANOR S/T 12" VINYL/CD`). Same "neither → skip" precedent as
   `asbestosrecords.py`/`jackpotrecords.py` — no field on this store
   reliably supplies what's missing for these.

`html.unescape()` is not required — no HTML entities were found in any
sampled title.

### Variants: not always one per product, unlike Jackpot/Turntable Lab

47/149 gated products have more than one variant (max 6), unlike
`jackpotrecords.py`'s catalog where every product has exactly one. Here,
variants are used for two different purposes that both need handling:

1. **Genuine alternate pressings** (color/edition options) — e.g.
   `LP - Random Color`, `CLEAR GREEN VINYL`, `TURQUOISE` — each a real,
   independently purchasable vinyl variant. Handled like
   `secretlystore.py`: each surviving variant yields its own stock item,
   title suffixed with the variant name when more than one survives.
2. **Non-vinyl alternate purchases bundled onto the same product** — a
   CD (`CD`, `GET FIRED CD`), a cassette (`Cassette`, `CS`), or a promo
   slipmat (`A3 SLIPMAT`) offered from the same product page as an
   alternative to the vinyl. **Excluded** via
   `\bCD\b|\bCS\b|\bCASSETTE\b|\bSLIPMAT\b` (case-insensitive) matched
   against the variant's own title, applied only when a product has more
   than one variant — a single-variant product's variant title is often
   just `Default Title` or a bare color word (`BLACK`, `YELLOW`) with no
   format marker at all, so requiring a vinyl-signal match there would
   wrongly drop 55/102 single-variant products.

**Apparel-bundle sizing — collapsed, not enumerated.** 3 products
(`GRUMPSTER - "Honeydew" 12" VINYL + T-SHIRT`, the pre-order Albert
Square and Smoking Popes listings) sell the vinyl bundled with a T-shirt,
varying only by **shirt size** (`SMALL BUNDLE DEAL` … `XXXL BUNDLE DEAL`,
6 variants each). None of these variant titles carry a CD/cassette/
slipmat marker, so the exclusion rule above doesn't touch them — but
enumerating all 6 as separate stock items would produce 6 near-duplicate
rows differing only in apparel size, not in the vinyl edition. When every
surviving variant on a product matches `BUNDLE DEAL` (case-insensitive),
this crawler keeps only the cheapest one.

### Pre-orders and availability

No dedicated Shopify tag marks a pre-order on this store (unlike
`jackpotrecords.py`'s clean `Pre-Order` tag) — the only signal is the
`PRE ORDER:` title prefix, stripped and captured in parsing step 1 above.
Standard rule: skip a variant unless `available` or the product was
pre-order-flagged. On this snapshot every pre-order variant is already
`available`, so — same caveat `jackpotrecords.py`'s spec notes for its
own pre-order tag — this has no live effect right now, but is kept for
when a pre-order later sells out before release.

24/149 gated products have every variant unavailable (sold-out back
catalog); these yield zero stock items, same as any sold-out product on
any sibling crawler.

### Fields

- **price** — `float(variant["price"])`, or the cheapest surviving
  variant's price when apparel-bundle sizing is collapsed (see above).
- **currency** — `"USD"`.
- **url** — `f"{base_url}/products/{handle}"`.
- **cover_image_url** — `resolve_cover_image(product, variant)`, same
  helper every Shopify sibling crawler uses.
- **format** — `"Vinyl"` unconditionally, matching every sibling Shopify
  crawler; a bundled T-shirt or the vinyl's own color variant name is not
  separately modeled.

### Crawler shape

```python
class Crawler:
    site_name: str = "Asian Man Records"
    base_url: str = "https://asianmanrecords.com"
    genre_summary: str = "Mike Park's Bay Area punk/ska label store, selling its own catalog plus a small distro of other labels' releases."
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]: ...
```

`_COLLECTION_SLUG = "all-products"`, iterated with
`shopify_catalog.iter_products()` unchanged, filtered per-product by the
inclusion gate, parsed by the title logic above, and fanned out per
surviving variant. Registration is automatic via `main.py`'s startup
loop — no wiring changes.

## Queue fan-out

149 gated products → 8 skipped (no artist/album split) → 141 parsed →
variant filtering (CD/cassette/slipmat exclusion, apparel-bundle
collapse, unavailable-variant skip) → **118 stock items**, × 3 eligible
release crawlers (`amazon`, `ebay`, `ebay_general`;
`discogs_marketplace` excluded by its `requires_discogs_release = True`)
= **~354 `crawl_queue` jobs per sync** — two orders of magnitude smaller
than `jackpotrecords.py`'s ~8,868, consistent with this being a single
small label's own catalog rather than a multi-thousand-product record
store.

## Testing

`backend/tests/test_asianmanrecords_crawler.py`, on
`test_jackpotrecords_crawler.py`'s pattern — `respx`-mocked
`products.json` responses and hand-written product literals taken from
confirmed-live products, no live site, no bot-detection risk. Cases:

- quoted-album title, no hyphen (`KOREA GIRL "Korea Girl" 12" VINYL`)
- quoted-album title with a leading hyphen
  (`GRUMPSTER - "Honeydew" 12" VINYL + T-SHIRT`)
- quoted-album title with hyphen glued to the artist word
  (`SMOKING POPES- "Stay Down" ...`)
- `PRE ORDER:` prefix stripped and flagged for the availability rule
- `AMR DISTRO:` prefix stripped, distro item still included
- doubled-colon `PRE ORDER: AMR DISTRO:: ...` prefix, both stripped
- hyphen-only title, no quotes, format suffix stripped from the album
  (`MU330 - CHUMPS ON PARADE 12" VINYL` → album `CHUMPS ON PARADE`)
- hyphen-only self-titled (`MU330 - S/T 12" VINYL` → album `S/T`)
- no separator at all → skipped (`MAGUMA TAISHI 7"`,
  `V/A GILMAN STREET RIPOFFS(A Tribute To DOOKIE)`)
- `product_type` says vinyl but tags don't (or vice versa) → still
  included by the gate
- a `TEST PRESSES`-typed product → excluded
- multi-variant product with a CD sibling variant → CD variant dropped,
  vinyl variant kept
- multi-variant product with a cassette (`CS`) sibling variant → dropped
- multi-variant product with a slipmat (`A3 SLIPMAT`) sibling variant →
  dropped
- single-variant product whose sole variant is titled `Default Title` →
  kept (no vinyl-word requirement on single-variant products)
- apparel-bundle product with 6 size variants, none priced identically
  (`XXL`/`XXXL` upcharge) → collapsed to the cheapest
- multi-variant product with 3 genuine color-pressing variants, no
  CD/cassette/slipmat/bundle-deal signal → all 3 kept, each title suffixed
  with its own variant name
- an unavailable, non-pre-order variant → skipped
- an unavailable, pre-order-flagged variant → kept
- site metadata (`site_name`, `base_url`, `crawler_type`)

## Crawl citizenship and `robots.txt` compliance

Per the normative section of `2026-08-09-amoeba-store-crawler-design.md`.
This site's finding:

- `robots.txt`'s `User-agent: *` group is `Allow: /`, with disallows
  covering `/admin`, `/cart/`, `/checkout`, `/checkouts/`, `/orders`,
  `/account`, `/services`, `/sf_*`, `/cart.js`, `/recommendations/
  products`, and `sort_by`/multi-filter/`+`-encoded crawl traps — the
  same template `jackpotrecords.py` found. **None of these covers
  `/collections/all-products/products.json`**, the only path this
  crawler requests.
- `/agents.md` names `GET /collections/{handle}/products.json` explicitly
  under "Read-Only Browsing (No Authentication Required)".
- Both documents require checkout/payment to never complete without
  contemporaneous human approval. This crawler satisfies that trivially:
  it links out to the product page and never transacts.
- Load: 2 GETs per sync (1 page of data at `limit=250` plus the
  terminating short/empty page). Paced at
  `random.uniform(delay * 0.5, delay)` with `crawl_delay_seconds`
  defaulting to 30s. No detail-page fan-out. `iter_products()` fails
  fast on 429 and gives up after `consecutive_failure_limit` on anything
  else.
- If Asian Man Records blocks this crawler, adds a `Disallow` covering
  this path, or asks us to stop, the response is to disable the plugin.

## Runtime/agent document impact

No `.agents/INPUTS.md`, `.agents/OUTPUTS.md`, or `.agents/INSTRUCTIONS.md`
exist in this repo. This change adds no new trigger and no new inbound
interface — one new outbound host (`asianmanrecords.com`).

`backend/version.py`'s `VERSION` is derived from git and is not edited by
this change.
