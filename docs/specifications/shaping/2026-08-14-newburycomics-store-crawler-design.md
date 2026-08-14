# Newbury Comics store crawler design

Date: 2026-08-14
Branch: `claude/newbury-comics-crawler-17392e`

## Problem

Newbury Comics (`newburycomics.com`), a New England record store chain and
pop-culture retailer, is not covered by any existing crawler. Its store is a
standard Shopify storefront, so it belongs to the same family as the 34
Shopify `catalog` plugins already in `backend/crawlers/`.

## Scope

Add `backend/crawlers/newburycomics.py` as a `crawler_type="catalog"` plugin
covering the `/collections/vinyl` collection via the existing
`shopify_catalog.iter_products()` — no new shared code needed.

**Non-goals**

- **No browser.** `products.json` is served to plain `httpx` with no
  Cloudflare gate and no bot interstitial.
- **No UCP/MCP integration.** See "Why not the store's own agent API" below.
- **No CD/Cassette coverage.** Out of scope by explicit decision — see
  "Format scope" below. Every sibling store crawler in this repo is
  vinyl-only.
- **No `/collections/all` enumeration.** See "Why `vinyl` alone, not
  `/collections/all`" below.
- **No variant-title disambiguation.** See "Variant shape" below.
- **No pre-order handling.** See "No pre-order tag convention" below.

## Technical grounding

All figures below were confirmed against the live site on 2026-08-14 by
paginating collection `products.json` endpoints to exhaustion and, for the
full-catalog figures, `/collections/all/products.json?limit=250` across all
56 pages.

### Why `vinyl` alone, not `/collections/all`

`/collections/all` returns 13,910 products total, 12,335 of them
`product_type: "Vinyl"` — but only 1,128 of those 12,335 have any available
variant (~9%); the rest is discontinued/sold-out back catalog going back
years. Filtering `/collections/all` by `product_type` at crawl time would
mean paginating ~56 pages every sync just to discard 91% of what it returns.

The curated `/collections/vinyl` smart collection turns out to be exactly
that available subset: 1,128 products, confirmed 100% available (every
variant in every product has `available: true`). This was cross-checked
against the two other vinyl-labeled collections on the store
(`collections.json` lists 18 collections with "vinyl" in title or handle;
only two others contain products not already in `vinyl`):

| Collection | Products | Available | Available items not already in `vinyl` |
|---|---|---|---|
| `vinyl` | 1,128 | 1,128 (100%) | — |
| `vinyl-singles` | 485 | 16 | 0 |
| `vinyl-box-sets` | 197 | 1 | 0 |

Every available item in `vinyl-singles` and `vinyl-box-sets` is already
inside `vinyl`; both are otherwise near-total sold-out remainder bins. So
`vinyl` alone is the complete, currently-purchasable vinyl catalog — no
union needed, unlike `sgrecordshop.py`'s multi-category-page union or
Turntable Lab's single-large-collection case.

### Variant shape

Every one of the 1,128 products in `vinyl` has exactly one variant, titled
`"Default Title"` (confirmed live, 0/1,128 multi-variant products — checked
across the full 12,335-product `Vinyl`-type catalog too, where only 1/12,335
had more than one variant, and that one is not in the available set). Unlike
`turntablelab.py`'s condition-graded multi-variant products, there is no
variant-title suffix to conditionally append here.

### Artist/title shape: `vendor` is the artist, `title` is the album alone

Spot-checked broadly across the `vinyl` collection (samples every 40th
product plus the top 15 vendors by frequency, ~1,128 products fetched in
full): `vendor` is the artist name directly — `"Big Star"`, `"John
Coltrane"`, `"Sublime"`, `"Trippie Redd"` — including legitimate
`"Various Artists"` compilations (25 products), not a placeholder value.
`title` never repeats or prefixes the artist (`"#1 Record LP (180g)"`,
vendor `"Big Star"`; `"Lush Life Exclusive LP (Maroon)"`, vendor `"John
Coltrane"`), so no `strip_vendor_prefix` call or regex split is needed —
unlike Turntable Lab's colon-split or Numero Group's "vendor is a label
placeholder for most of the catalog" caveat. This store's `vendor` field
does not carry that ambiguity.

Titles do carry format/edition detail (`"Exclusive"`, colorway parentheticals
like `"(Neon Purple)"`, disc-count suffixes like `"2LP"`/`"3LP"`) — kept
as-is, same as every other fleet plugin that doesn't attempt to parse
edition detail out of the title.

### No pre-order tag convention

Searched all tags across the full 12,335-product `Vinyl`-type catalog for
any pre-order/coming-soon pattern (`pre.?order`, `preorder`,
`coming.?soon`, case-insensitive) — zero matches. A `vinyl-pre-orders`
collection exists in `collections.json` but currently has 0 products. This
makes the fleet's standard `not variant["available"] and not is_preorder`
pattern moot here: there is no pre-order signal to check, so the crawler
just requires `variant["available"]`, matching the fact that the `vinyl`
collection is already availability-filtered upstream (kept as a defensive
guard, not because it's expected to ever trigger on this collection given
the shape above).

### Why not the store's own agent API

`robots.txt` and `/agents.md` both direct agents to the store's UCP endpoint
at `POST /api/ucp/mcp` (same boilerplate template as
`2026-08-13-turntablelab-store-crawler-design.md`'s finding — this appears
to be a standard Shopify-generated `agents.md`). `GET /.well-known/ucp`
confirms the store only advertises `dev.ucp.shopping.catalog.search` and
`.lookup` capabilities — intent search over buyer context, not an
enumeration API, same reasoning `2026-08-09-cleorecs-store-crawler-design.md`
and the Turntable Lab spec give for their own UCP endpoints. The same
`/agents.md`, under "Read-Only Browsing (No Authentication Required)",
affirmatively names `GET /collections/{handle}/products.json` as a supported
path. That is the only path this crawler requests.

### Rate limiting

Confirmed empirically: after several rapid, undelayed full-catalog sweeps
during this investigation, the storefront returned `HTTP 429` with no
`Retry-After` header, and recovered after a ~20s pause. This matches
`shopify_catalog.py`'s existing documented behavior (fail fast on 429,
never retry it) — no new handling needed.

### Format scope

Newbury Comics also lists a small CD (197) and Cassette (132) catalog
(figures from the full `/collections/all` type breakdown above), but no
curated collection covers either the way `vinyl` covers the available vinyl
catalog — reaching them would require the same `/collections/all` +
`product_type` filter approach rejected above for vinyl. Out of scope by
explicit decision: every sibling store crawler in this repo is vinyl-only.

## Crawler design

`backend/crawlers/newburycomics.py`, following `numerogroup.py`'s shape
(vendor-as-artist, no title regex) rather than `turntablelab.py`'s
(title-parsed artist):

```python
class Crawler:
    site_name: str = "Newbury Comics"
    base_url: str = "https://www.newburycomics.com"
    genre_summary: str = "New England record store chain and pop-culture retailer with a broad new/exclusive vinyl selection."
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]: ...
```

`_COLLECTION_SLUG = "vinyl"`, iterated with `shopify_catalog.iter_products()`
unchanged — that helper already supplies `crawl_delay_seconds` pacing, the
`consecutive_failure_limit` retry policy, the fail-fast-on-429 rule, and
`report_page()` progress reporting.

### Parse rules

A pure `@classmethod`, unit-testable without HTTP:

- **artist** — `product["vendor"]`, used directly.
- **title** — `product["title"]`, used directly.
- **Availability** — skip a variant unless `available`.
- **format** — `"Vinyl"` unconditionally, matching every sibling Shopify
  crawler.
- **price** — `float(variant["price"])`, `None` on failure.
- **currency** — `"USD"`.
- **url** — `f"{base_url}/products/{handle}"`.
- **cover_image_url** — `resolve_cover_image(product, variant)`.

Registration is automatic: `main.py`'s startup loop reads `site_name` /
`crawler_type` / `requires_discogs_release` off every module in
`backend/crawlers/` and calls `register_crawler()`. No wiring changes.

## Queue fan-out

1,128 available products (each single-variant, so 1,128 stock items) × 3
eligible release crawlers (`amazon`, `ebay`, `ebay_general`;
`discogs_marketplace` is excluded by its `requires_discogs_release = True`)
= **~3,384 `crawl_queue` jobs per sync** — smaller than Turntable Lab's
~7,476. No window is applied, consistent with every other unwindowed
label-store plugin.

## Testing

`backend/tests/test_newburycomics_crawler.py`, on
`test_numerogroup_crawler.py`'s pattern — `respx`-mocked `products.json`
responses and hand-written product literals taken from confirmed-live
products, so no live site and no bot-detection risk. Cases:

- `vendor` used as artist directly, `title` used as-is (no split)
- `"Various Artists"` vendor passed through unchanged
- an unavailable variant is skipped
- a product with `variants: None` or an empty list yields nothing
- site metadata (`site_name`, `base_url`, `crawler_type`)

## Crawl citizenship and `robots.txt` compliance

Per the normative section of the amoeba spec
(`2026-08-09-amoeba-store-crawler-design.md`). This site's finding:

- `robots.txt`'s `User-agent: *` group is `Allow: /`, with disallows
  covering `/admin`, `/cart/`, `/checkout`, `/checkouts/`, `/orders`,
  `/account`, `/services`, `/sf_*`, `/cart.js`, `/recommendations/products`,
  and `sort_by`/multi-filter/`+`-encoded crawl traps. **None of these
  covers `/collections/vinyl/products.json`**, the only path this crawler
  requests.
- `/agents.md` names `GET /collections/{handle}/products.json` explicitly
  under "Read-Only Browsing (No Authentication Required)".
- Both documents require checkout/payment to never complete without
  contemporaneous human approval. This crawler satisfies that trivially: it
  links out to the product page and never transacts, holds no cart, and
  stores no payment method.
- Load: 6 GETs per sync (5 product pages plus the terminating empty page),
  paced at `random.uniform(delay * 0.5, delay)` with `crawl_delay_seconds`
  defaulting to 30s. No detail-page fan-out. No retry storms —
  `iter_products()` fails fast on 429 and gives up after
  `consecutive_failure_limit` on anything else.
- If Newbury Comics blocks this crawler, adds a `Disallow` covering this
  path, or asks us to stop, the response is to disable the plugin.

## Runtime/agent document impact

No `.agents/INPUTS.md`, `.agents/OUTPUTS.md`, or `.agents/INSTRUCTIONS.md`
exist in this repo. This change adds no new trigger — `_sync_stock` already
enumerates `catalog` plugins — and no new inbound interface. It adds one new
outbound host (`newburycomics.com`), which would belong in
`.agents/OUTPUTS.md` if that file existed.

`backend/version.py`'s `VERSION` is derived from git (per
`2026-08-10-derived-version-design.md`) and is not edited by this change.
