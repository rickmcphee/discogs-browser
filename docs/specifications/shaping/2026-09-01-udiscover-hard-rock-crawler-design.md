# uDiscover Music hard rock & heavy metal crawler design

Date: 2026-09-01
Branch: `claude/udiscover-hard-rock-crawler-miv5f8`

## Problem

uDiscover Music (`shop.udiscovermusic.com`) — Universal Music's official
direct-to-consumer store, carrying exclusive pressings and reissues from the
catalog majors license (KISS, Aerosmith, Rush, Def Leppard, Danzig,
Queensrÿche, Godsmack, The Sex Pistols, blink-182, and the rest of the
Universal roster) — is not covered by any existing crawler. The store is a
standard Shopify storefront (confirmed live: `products.json` answers with
ordinary Shopify payloads to plain `httpx`, `Shopify.currency =
{"active":"USD","rate":"1.0"}` in the page), same family as the label-store
`catalog` plugins already in `backend/crawlers/`.

The whole store is far broader than this app's shelf — merch, pop, jazz,
soundtracks — so this crawler scopes to the store's own
`hard-rock-heavy-metal` collection, the one the request named. That
collection is itself mixed-format (CDs outnumber LPs in it), so a
`product_type` gate does the vinyl scoping the sibling stores get from a
`vinyl` collection slug.

## Scope

Add `backend/crawlers/udiscovermusic.py` as a `crawler_type="catalog"`
plugin, iterating the site's `hard-rock-heavy-metal` collection via
`shopify_catalog.iter_products()` — no new shared code needed.

**Non-goals**

- **No browser.** Confirmed live: `products.json` is served to plain
  `httpx` with no Cloudflare gate or bot interstitial.
- **No UCP/MCP integration.** The site's `robots.txt` names UCP/MCP
  endpoints (and `/agents.md`) for buyer-approved cart/checkout, not a bulk
  catalog dump — same reasoning every sibling Shopify crawler's spec gives.
- **No CD/DVD/cassette/merch coverage.** The collection's CD majority and
  its T-shirts are out of scope; this app's stock pipeline is vinyl-only by
  convention (`format: "Vinyl"` hardcoded across every sibling Shopify
  crawler).
- **No box-set coverage.** The store types box sets as `Box Set (Music
  Only)` / `Box Set (Music + Merch)` whatever media they hold, so the
  `product_type` gate cannot tell a 5LP box from a 7CD one. The live
  collection's box sets are mostly CD/Blu-Ray; the vinyl ones it excludes
  (confirmed live 2026-09-01: two 5LP Rush boxes and one LP+Blu-Ray box)
  are accepted scope loss rather than grounds for a title-parsing gate.
- **No other uDiscover collections.** The store's other genre shelves
  (and the all-vinyl collection, which spans the whole roster) are out of
  scope for this crawler; a later crawler or a slug change can widen it.

## Technical grounding

All figures below were confirmed against the live site on 2026-09-01, by
fully paginating `/collections/hard-rock-heavy-metal/products.json`
(710 products across 3 populated pages) with the walk cached and replayed
locally.

### Collection choice: `hard-rock-heavy-metal`, vinyl-gated by `product_type`

The collection is the store's own hard rock & heavy metal shelf and is
mixed-format: CDs are its largest type, then `1LP`/`2LP`, then DVD/Blu-Ray,
box sets, cassettes, and apparel. `product_type` is the store's own format
field and is clean and specific here — vinyl is exactly the `1LP`, `2LP`,
`3LP`, `4LP`, and `7in` types (303 of the 710 live products), every one of
them a real record. The gate accepts `^\d*LP$` and `^\d+in$`
(case-insensitive, stripped), so a bare `LP` or a future `10in`/`12in`
lands on the right side without a code change. No vinyl-typed title names a
non-vinyl format (confirmed live), so no title-level backstop is needed —
unlike `hammerheart.py`, whose collection mistypes CDs as vinyl.

### Artist: `vendor`, always

Every product in the collection carries a non-blank `vendor`, and on the
vinyl-typed products it is always the artist in proper mixed case (`KISS`,
`Queensrÿche`, `Amyl and The Sniffers`), never a label or supplier code —
117 distinct artists across the 303 vinyl products. A blank vendor has no
artist source and is skipped, the fleet's "no artist source → skip"
convention; a catalog whose *every* vendor is blank raises instead (see
drift guards below).

### Title: kept as-is, shared `strip_vendor_prefix` as a guard

Titles are the album plus a trailing pressing/format descriptor — `Sticks
And Stones 1LP`, `The Art Of Losing (LP)`, `ANThology (Smoked Lava) 2LP` —
and never lead with the artist: zero of the 303 vinyl titles start with
`"{vendor} - "`. The titles that do start with the vendor's name are
self-titled albums and title-drops (`She Wants Revenge 2LP`, `KISS Destroys
Anaheim '76 2LP`), which must not be stripped — so the shared, exact-case
`strip_vendor_prefix` is applied as a cheap guard against future drift and
correctly no-ops on all of today's catalog, and no local case-insensitive
strip (Hammerheart's) is wanted: it would still require a `[-/]` separator,
but there is no live prefixing to earn the looser match.

The trailing descriptor stays in the title, matching `waterloorecords.py`'s
reasoning: it is what separates two pressings of one album (`Move Along
(Deluxe Edition) 2LP` vs `Move Along (Deluxe Edition Clear Glitter) 2LP`),
and `db._library_match_fragment` matches a stock title exactly OR as a
prefix followed by a space, so `Sticks And Stones 1LP` still matches a
catalog `Sticks And Stones`.

### Pre-orders: tag suffix, **no availability bypass**

`has_tag(product, "pre-order")` (the store's tag, hyphenated like Nuclear
Blast's) appends ` (Pre-Order)` to the title. Deliberately absent is the
sibling label-store bypass that keeps *unavailable* variants of a
pre-order-tagged product: confirmed live, all 13 pre-order-tagged vinyl
products report `available: true`, so this store flags purchasable
pre-orders available and an unavailable product — pre-order or not — is
gone allocation, and publishing an unbuyable record at a price is worse
than omitting a buyable one. Same call, on the same evidence shape, as
`hammerheart.py`, `darksiderecords.py`, and `onetwothreefourgo.py`. A test
pins the absence so a later change is deliberate.

### Variants: single today, disambiguated if that changes

Every vinyl-typed product is single-variant (303/303), each variant titled
`Default Title` — color and edition live in the product title itself, so
nothing is appended on the live catalog. (The collection's multi-variant
products are apparel — T-shirt sizes — which the `product_type` gate never
admits.) On a multi-variant product the variant descriptor *is* appended
(`… — {variant title}`), because rows sharing (artist, title, url) collapse
onto one `item_key` — `stock_items.item_key` is deliberately non-unique, so
the sync accepts the collision, and the colliding rows then share one
`stock_item_identities` row, one `crawl_queue` target, and one
`(item_key, crawler_id)` listings slot downstream. Blank/placeholder
variant titles fall back to the immutable variant id, and a variant with
neither raises — `hammerheart.py`'s chain, for `onetwothreefourgo.py`'s
reasons.

### Drift guards

`db.replace_stock_items()` DELETEs the crawler's previous snapshot before
inserting, and `_sync_stock` only skips that call when the crawl raised —
so the two states indistinguishable from a healthy empty store raise
instead of completing empty:

- zero products from the collection (renamed or removed), and
- a non-empty collection in which no product carries a vendor
  (artist-source drift).

Sold-out, format-gated, and blank-vendor products all count toward the
tallies first, so a legitimately quiet catalog trips neither. The
format gate dropping *everything* (a store-side `product_type` renaming)
is deliberately not a raise: the collection is genuinely mixed-format, so
an all-CD page run is indistinguishable from that drift — accepted, since
the zero-products guard still catches the collection itself vanishing.

### Fields

- **price** — `float(variant["price"])`. Confirmed live: every vinyl
  variant carries a string price; none missing.
- **currency** — `"USD"` (confirmed via the page's `Shopify.currency`).
- **url** — `f"{base_url}/products/{handle}"`.
- **cover_image_url** — `resolve_cover_image(product, variant)`. Confirmed
  live: every vinyl product carries at least one image; no variant carries
  a `featured_image`, so the product image always wins today — called
  anyway to stay correct if the store ever populates it.
- **format** — `"Vinyl"` unconditionally, matching every sibling Shopify
  crawler.

### Crawler shape

```python
class Crawler:
    site_name: str = "uDiscover Music"
    base_url: str = "https://shop.udiscovermusic.com"
    genre_summary: str = "Universal Music's official store, crawled for its hard rock and heavy metal collection."
    genre: str = "rock"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]: ...
```

`_COLLECTION_SLUG = "hard-rock-heavy-metal"`, iterated with
`shopify_catalog.iter_products()` unchanged. Registration is automatic via
`main.py`'s startup loop — no wiring changes. `site_name` names the store
rather than the collection: Settings links the storefront, and the
`genre_summary` tooltip carries the collection scoping.

`genre: "rock"` rather than `"metal"`: the collection's own subgenre tags
lean hard rock (`subgenre: Hard Rock` is the largest bucket, ahead of
`Contemporary Metal` and `Classic Metal`), the roster is mainstream
hard rock (KISS, Aerosmith, Rush, Def Leppard) rather than the extreme
metal that fills the app's `metal` group (Nuclear Blast, Relapse, Dark
Descent, Season of Mist), and the collection name itself leads with Hard
Rock. Defensible either way; recorded here so a later re-shelving is a
deliberate one-line change, not a re-derivation.

## Queue fan-out

Replaying the crawler over the fully-cached live catalog: 710 products →
303 pass the format gate → 296 rows emitted (the rest are sold-out
single-variant products), with no (artist, title, url) collisions, no row
missing a price or cover image, and no blank artist. Per this repo's
per-item-crawler-fanout design, `_sync_stock` enqueues one `crawl_queue`
row per `item_key` — ~296 rows — each expanded across eligible release
crawlers at dispatch time (`discogs_marketplace` excluded by its
`requires_discogs_release = True`).

## Testing

`backend/tests/test_udiscovermusic_crawler.py`, on
`test_hammerheart_crawler.py`'s pattern — `respx`-mocked `products.json`
responses, no live site. Fixtures distinguish three provenances (captured
live product, live product with one field altered to reach a branch the
live data never takes, wholly invented shape), each marked at its
definition. Cases: the format gate across live vinyl and non-vinyl types
plus the case/`\d*LP`/`\d+in` boundaries; title kept as-is including the
self-titled shapes; the vendor-prefix guard stripping an invented
`"{vendor} - "` title; pre-order suffix and its case-insensitivity; the
pinned absence of the pre-order availability bypass; sold-out skip;
blank-vendor skip; null-variants product; cover-image fallback and
none-when-imageless; unparseable price → `None`; both drift-guard raises;
multi-variant disambiguation, its id fallback, and its no-identity raise;
pagination; HTTP-error raise; site metadata.

## Crawl citizenship and `robots.txt` compliance

Per the normative section of `2026-08-09-amoeba-store-crawler-design.md`.
This site's finding, confirmed live 2026-09-01:

- `robots.txt`'s `User-agent: *` group is `Allow: /`, with disallows
  covering `/admin`, `/cart/`, `/checkout(s)/`, `/orders`, `/account`,
  `/services`, `/sf_*`, `/cart.js`, `/recommendations/products`, and
  `sort_by`/`filter`/`+`-encoded crawl traps — the same Shopify-default
  template every sibling Shopify crawler found. **None of these covers
  `/collections/hard-rock-heavy-metal/products.json`**, the only path this
  crawler requests (its `limit`/`page` params match none of the trap
  patterns).
- The header comments name `/agents.md` and UCP/MCP endpoints for
  buyer-approved cart/checkout, and require that checkout, payment, and
  order placement never complete without contemporaneous human approval.
  This crawler satisfies that trivially: it links out to the product page
  and never transacts. (The recommendation to install a third-party "Shop
  skill" for agent-driven purchasing is irrelevant to this read-only
  catalog crawler, and not acted on.)
- Load: 4 GETs per sync — 710 products at `limit=250` is three populated
  pages, then the terminating empty page `iter_products()` needs to know
  the collection is exhausted. Paced at `random.uniform(delay * 0.5,
  delay)` with `crawl_delay_seconds` defaulting to 30s. No detail-page
  fan-out. `iter_products()` fails fast on 429 and gives up after
  `consecutive_failure_limit` on anything else.
- If uDiscover Music blocks this crawler, adds a `Disallow` covering this
  path, or asks us to stop, the response is to disable the plugin.

## Runtime/agent document impact

No `.agents/INPUTS.md`, `.agents/OUTPUTS.md`, or `.agents/INSTRUCTIONS.md`
exist in this repo. This change adds no new trigger and no new inbound
interface — one new outbound host (`shop.udiscovermusic.com`).

`backend/version.py`'s `VERSION` is derived from git and is not edited by
this change.
