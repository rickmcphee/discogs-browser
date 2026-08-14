# Turntable Lab store crawler design

Date: 2026-08-13
Branch: `claude/turntable-lab-crawler-5369a9`

## Problem

Turntable Lab (`turntablelab.com`), a New York-based record store and hi-fi
retailer, is not covered by any existing crawler. Its store is a standard
Shopify storefront, so it belongs to the same family as the 33 Shopify
`catalog` plugins already in `backend/crawlers/`.

What makes it more than a copy of a label-store plugin: it sells hardware
(turntables, speakers, cables, DJ gear) alongside records, spread across 479
Shopify collections with no single canonical "everything vinyl" slug of the
kind the label stores use (`vinyl`, `vinyl-1`, `all`). Titles are also
colon-separated (`"Artist: Album"`), not the hyphen form the fleet's existing
`_TITLE_RE` conventions assume.

## Scope

Add `backend/crawlers/turntablelab.py` as a `crawler_type="catalog"` plugin
covering the `/collections/vinyl-lps-alpha` collection via the existing
`shopify_catalog.iter_products()` — no new shared code needed.

**Non-goals**

- **No browser.** Confirmed live: `products.json` is served to plain `httpx`
  with no Cloudflare gate and no bot interstitial (10 pages fetched cleanly
  at `crawl_delay_seconds`-scale pacing).
- **No UCP/MCP integration.** See "Why not the store's own agent API" below.
- **No multi-collection union.** See "Why `vinyl-lps-alpha` alone" below.
- **No condition/format filtering beyond availability.** The collection is
  already vinyl-only; no non-vinyl contamination was found (see "Collection
  shape").

## Technical grounding

All figures below were confirmed against the live site on 2026-08-13 by
paginating `/collections/vinyl-lps-alpha/products.json?limit=250` to
exhaustion (10 pages, page 11 empty).

### Why `vinyl-lps-alpha` alone

Of 479 total collections, 94 have "vinyl" in their title or handle, almost
entirely overlapping genre/format/curated cuts (`electronic-alpha`,
`classic-rock-vinyl`, `essential-hip-hop-vinyl`, `colored-vinyl-editions-alpha`,
…) of the kind `sgrecordshop.py` has to union across many category pages.
`vinyl-lps-alpha` ("Vinyl LPs") is different: its `products_count` (2,393) and
a full paginated fetch (2,385 products) both land within noise of each other
of the store's largest "Vinyl" collections, and it is the one whose title
plainly claims to be the catalog, not a curated subset. There is no evidence
of a still-larger uncovered set: `vinyl-cds-alpha` ("Bestsellers", 2,502) and
`vinyl-lps-date` ("Newest", 2,382) are same-order-of-magnitude siblings sorted
differently over what reads as the same underlying set, not larger supersets.

### Collection shape

| Measure | Count |
|---|---|
| Products in `vinyl-lps-alpha` | 2,385 |
| Variants across those products | 2,547 |
| …available | 2,492 |
| …unavailable | 55 |
| Tagged `pre-order` | 302 |
| Multi-variant products | 158 |

Every title contains the word "Vinyl" or an LP-count token except 6/2,385
(`"A Tribe Called Quest: The Low End Theory 2LP"` and similar) — all
genuinely vinyl, just not spelling out the word. No turntables, speakers, or
accessories were found in a full pass; unlike `cleorecs.py`'s `vinyl-1`
collection, no `product_type`/title filter is needed here.

### Title shape: colon-separated, not hyphen

Every title in the collection (2,385/2,385, confirmed by direct regex match)
splits cleanly on `^(?P<artist>.+?): (?P<album>.+)$`, independent of the
`vendor` field. This is a different separator from the fleet's existing
`_TITLE_RE`/`split_artist_title` hyphen conventions
(`2026-08-07-shared-title-split-helper-design.md`) — not a variant of it, so
this crawler does not (and structurally cannot) reuse that shared helper.
`vendor` itself is usually the artist but occasionally abbreviated or
differently-cased relative to the title (`vendor="Blue Note"` vs.
title-artist `"Blue Note Records"`; confirmed on 7/2,385 products) — parsing
off the title, not `vendor`, is the only source that's correct on all of
them. One confirmed case has a stray space before the colon
(`"Warren G : Regulate..."`); `.strip()` on the captured artist group handles
it without a separate rule.

### Pre-orders and availability

302/2,385 products carry a clean `pre-order` tag (`has_tag(product,
"pre-order")`, same helper `epitaph.py`/`bigscarymonstersusa.py` use) — no
title/body-text sniffing needed, unlike `polyvinylrecords.py`'s
substring-search fallback. 55/2,547 variants are unavailable and not
pre-order-tagged; those are skipped, matching the fleet's standard
`not variant["available"] and not is_preorder` rule.

### Multi-variant products carry condition grading, not colour

158 products have more than one variant — mostly the same pressing offered
at full price alongside a cosmetically-graded, cheaper copy: 102 distinct
variant-title strings were seen, dominated by `"Seam Split Vinyl LP"` (31),
`"Bent Corner Vinyl LP"` (20), `"Dented Corner Vinyl 2LP"` (19), and a long
tail of one-off, hand-typed condition notes (`"Damaged Ripped Spine Vinyl
LP"`, `"Crushed Spine Vinyl 2LP"`, `"Sign Cover - Vinyl 2LP"`, …) — not a
closed, parseable vocabulary.

This matters for the title because, unlike `polyvinylrecords.py`/
`epitaph.py` (titles are bare `"Artist - Album"`, no format info), every
Turntable Lab title already ends with its own format suffix (`"... Vinyl
LP"`, `"... Vinyl 2LP"`) — the single dominant variant title
(1,459/2,547 = 57% are literally `"Vinyl LP"`, another 788 are `"Vinyl
2LP"`) duplicates text already in the title. Appending it unconditionally,
`polyvinylrecords.py`-style, would stamp a redundant `"— Vinyl LP"` on 93%
of rows. Not appending it at all would make the standard and
condition-graded variants of the same product indistinguishable — two rows,
same artist/title, different price, no way to tell why.

**Rule adopted:** append `" — {variant_title}"` only when the product has
more than one variant (`len(variants) > 1`). This sidesteps parsing the
free-text condition tail entirely — no vocabulary to maintain, no case that
falls through unrecognized — at the cost of an occasional redundant-looking
`"... Vinyl LP — Vinyl LP"` on the standard-condition sibling of a
multi-variant product (a small, cosmetic blemish, not a correctness issue:
the two rows are still distinct and correctly priced). This is the same
kind of guard `cleorecs.py` needed against its `"Default Title"` placeholder
noise, adapted to this store's inverse shape (the common case is redundant,
not a placeholder).

### Why not the store's own agent API

`robots.txt` and `/agents.md` both direct agents to the store's UCP endpoint
at `POST /api/ucp/mcp`. Its `search_catalog` tool is an intent search over
buyer context, not an enumeration API, and there is no call that returns
"everything in this collection" — covering the catalog would mean
synthesizing queries and never knowing what was missed, the same reasoning
`2026-08-09-cleorecs-store-crawler-design.md` gives for its own UCP endpoint.
The same `/agents.md`, under "Read-Only Browsing (No Authentication
Required)", affirmatively names `GET /collections/{handle}/products.json` as
a supported path. That is the only path this crawler requests.

## Crawler design

`backend/crawlers/turntablelab.py`, following `polyvinylrecords.py`'s shape:

```python
class Crawler:
    site_name: str = "Turntable Lab"
    base_url: str = "https://www.turntablelab.com"
    genre_summary: str = "Record store and hi-fi retailer with a broad new vinyl selection across genres."
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]: ...
```

`_COLLECTION_SLUG = "vinyl-lps-alpha"`, iterated with
`shopify_catalog.iter_products()` unchanged — that helper already supplies
`crawl_delay_seconds` pacing, the `consecutive_failure_limit` retry policy,
the fail-fast-on-429 rule, and `report_page()` progress reporting.

### Parse rules

A pure `@classmethod`, unit-testable without HTTP:

- **Split** on `^(?P<artist>.+?): (?P<album>.+)$` against the title; falls
  back to `(vendor, title)` only if no colon separator is found (not
  observed live, but kept as the fleet's standard defensive fallback).
- **Pre-order**: `has_tag(product, "pre-order")`.
- **Availability**: skip a variant unless `available` or pre-order.
- **Title**: `album` alone when the product has exactly one variant, else
  `f"{album} — {variant_title}"`.
- **`format`**: `"Vinyl"` unconditionally, matching every sibling Shopify
  crawler (consumed by `ebay_api.FORMAT_KEYWORDS`/`FORMAT_CATEGORY_IDS`,
  keyed on the coarse `Vinyl`/`CD`/`Cassette`/`DVD`/`Blu-ray` set).
- **price** — `float(variant["price"])`, `None` on failure.
- **currency** — `"USD"`.
- **url** — `f"{base_url}/products/{handle}"`.
- **cover_image_url** — `resolve_cover_image(product, variant)`.

Registration is automatic: `main.py`'s startup loop reads `site_name` /
`crawler_type` / `requires_discogs_release` off every module in
`backend/crawlers/` and calls `register_crawler()`. No wiring changes.

## Queue fan-out

2,547 variants minus the 55 unavailable/non-pre-order ones this crawler
skips (see "Pre-orders and availability" above) = 2,492 stock items, × 3 eligible release
crawlers (`amazon`, `ebay`, `ebay_general`; `discogs_marketplace` is excluded
by its `requires_discogs_release = True`) = **~7,476 `crawl_queue` jobs per
sync**, the same order of magnitude as `cleorecs.py`'s ~9,450. No window is applied,
consistent with every other unwindowed label-store plugin — see
`2026-08-09-cleorecs-store-crawler-design.md`'s "Queue fan-out" section for
the fuller reasoning (the `claim_crawl_queue_batch` ordering that prevents a
user's own collection crawl from starving behind a store burst applies
identically here).

## Testing

`backend/tests/test_turntablelab_crawler.py`, on
`test_polyvinylrecords_crawler.py`'s pattern — `respx`-mocked
`products.json` responses and hand-written product literals taken from
confirmed-live products, so no live site and no bot-detection risk. Cases:

- colon-split artist/album from the title
- title-derived artist wins over an abbreviated/differently-cased `vendor`
- a stray space before the colon separator is stripped
- an unavailable, `pre-order`-tagged variant is kept
- an unavailable, non-pre-order variant is skipped
- a product with `variants: None` yields nothing
- a two-variant (standard + condition-graded) product gets two
  distinguishable rows, each with its own price; a one-variant product does
  not get a redundant `" — Vinyl LP"` suffix
- site metadata (`site_name`, `base_url`, `crawler_type`)

## Crawl citizenship and `robots.txt` compliance

Per the normative section of the amoeba spec
(`2026-08-09-amoeba-store-crawler-design.md`). This site's finding:

- `robots.txt`'s `User-agent: *` group is `Allow: /`, with disallows
  covering `/admin`, `/cart/`, `/checkout`, `/checkouts/`, `/orders`,
  `/account`, `/services`, `/sf_*`, `/cart.js`, `/recommendations/products`,
  and `sort_by`/multi-filter crawl traps. **None of these covers
  `/collections/vinyl-lps-alpha/products.json`**, the only path this crawler
  requests.
- `/agents.md` names `GET /collections/{handle}/products.json` explicitly
  under "Read-Only Browsing (No Authentication Required)".
- Both documents require checkout/payment to never complete without
  contemporaneous human approval. This crawler satisfies that trivially: it
  links out to the product page and never transacts, holds no cart, and
  stores no payment method.
- Load: 11 GETs per sync (10 product pages plus the terminating empty page),
  paced at `random.uniform(delay * 0.5, delay)` with `crawl_delay_seconds`
  defaulting to 30s. No detail-page fan-out. No retry storms —
  `iter_products()` fails fast on 429 and gives up after
  `consecutive_failure_limit` on anything else.
- If Turntable Lab blocks this crawler, adds a `Disallow` covering this
  path, or asks us to stop, the response is to disable the plugin.

## Runtime/agent document impact

No `.agents/INPUTS.md`, `.agents/OUTPUTS.md`, or `.agents/INSTRUCTIONS.md`
exist in this repo. This change adds no new trigger — `_sync_stock` already
enumerates `catalog` plugins — and no new inbound interface. It adds one new
outbound host (`turntablelab.com`), which would belong in
`.agents/OUTPUTS.md` if that file existed.

`backend/version.py`'s `VERSION` is derived from git (per
`2026-08-10-derived-version-design.md`) and is not edited by this change.
