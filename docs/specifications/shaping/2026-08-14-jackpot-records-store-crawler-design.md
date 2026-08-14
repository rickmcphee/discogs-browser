# Jackpot Records store crawler design

Date: 2026-08-14
Branch: `claude/jackpot-records-crawler-5a6b9f`

## Problem

Jackpot Records (`jackpotrecords.com`), a Portland, Oregon record store and
label ("new and used Vinyl LPs, CDs, DVDs and other collectibles", per its
own meta description), is not covered by any existing crawler. It is a
standard Shopify storefront — same family as Turntable Lab and the 33
label-store `catalog` plugins already in `backend/crawlers/`.

What makes it more than a copy of `turntablelab.py`: unlike Turntable Lab's
`vinyl-lps-alpha`, this store's own curated "vinyl" collection
(`all-vinyl`) is measurably incomplete — 7 confirmed-live vinyl products are
mistagged (`product_type: "CD"`, no `Vinyl` tag) and fall outside it
entirely. Covering the catalog requires the same union-gate technique
`asbestosrecords.py` used for its own coverage gap, applied to a different
pair of signals. Titles also split on a hyphen/en-dash/em-dash mix (not
Turntable Lab's colon form), and `vendor` is not a usable artist fallback
here — it is frequently the store's own name.

## Scope

Add `backend/crawlers/jackpotrecords.py` as a `crawler_type="catalog"`
plugin, iterating the full catalog (`online-store` collection) via
`shopify_catalog.iter_products()` — no new shared code needed — filtered by
an in-process inclusion gate.

**Non-goals**

- **No browser.** Confirmed live: `products.json` is served to plain
  `httpx` with no Cloudflare gate and no bot interstitial.
- **No UCP/MCP integration.** Same reasoning as `turntablelab.py`'s
  "Why not the store's own agent API": `/agents.md` names `search_catalog`
  as an intent-search tool with no "everything in this collection" call.
- **No used-condition variant.** No structural signal exists for it — see
  "No used inventory in the public catalog" below.
- **No CD/cassette coverage.** This app's stock pipeline is vinyl-only by
  convention (`format: "Vinyl"` hardcoded across every sibling crawler);
  the inclusion gate excludes them (see "Format filtering").

## Technical grounding

All figures below were confirmed against the live site on 2026-08-14.

### Why the full catalog, not the `all-vinyl` collection

Of 53 total collections (`GET /collections.json?limit=250`), the obvious
choice mirroring Turntable Lab's `vinyl-lps-alpha` is `all-vinyl` ("All
Vinyl In Stock", reported 3,135 products, 3,078 fetched by full
pagination). But diffing it against `online-store` ("Full Online Catalog
A-Z", reported 3,351, 3,276 fetched) turns up 7 products that are plainly
vinyl by title but missing from `all-vinyl` because the store's own
tagging is wrong for them:

| Product | `product_type` | `tags` |
|---|---|---|
| `Apple, Fiona - The Idler Wheel Is Wiser Than The Driver...(Vinyl)` | `0` | `["0", "Sony"]` |
| `Deftones - Private Music (Indie Ex) (Vinyl)` | `CD` | `["CD", "Rock", "WEA"]` |
| `Iron Lung - Adapting // Crawling (Vinyl)` | `01` | `["01", "Punk", "Revolver"]` |
| `Martin Denny - Exotica Vol. III (Sky Blue Vinyl)` | `""` | no format tag |
| `Martin Denny - Latin Village (Floral Swirl Vinyl)` | `""` | no format tag |
| `Suzanne Vega - An Evening... (2LP, Clear Vinyl) PRE-ORDER` | `Pre-Order` | no format tag |
| `Wipers - Land of the Lost (Black Vinyl)` | `Records & LPs` | no format tag |

The Deftones case was manually confirmed by fetching the individual
product: it is a real indie-exclusive vinyl pressing that the store has
simply mistagged `product_type: "CD"` — the same class of catalog-metadata
error `asbestosrecords.py`'s design found (there, empty `categories` on
26% of real releases).

### Format filtering

**Gate:** `has_tag(product, "Vinyl") OR` title contains the word `vinyl`
(case-insensitive, `\bvinyl\b`). Applied to the full 3,276-product
`online-store` catalog:

| Measure | Count |
|---|---|
| Products total (`online-store`) | 3,276 |
| Carry `Vinyl` tag | 3,073 |
| Gate result (tag OR title word) | 3,080 |
| Recovered vs. `all-vinyl` collection alone | 7 (table above) |
| `all-vinyl` products dropped by this gate | 0 |

This deviates from `asbestosrecords.py`'s own format gate
(`\bvinyl\b|\b\d*x?lp\b|\bep\b|\d+\s*"`), which also matches bare `LP`/`EP`
tokens. That broader regex was tested here and produces two confirmed false
positives it would be wrong to include: `Eminem - The Marshall Mathers LP
(CD)` and `Eminem - The Slim Shady LP (CD)` — `LP` is part of each album's
actual, canonical title, not a format marker, and both products are
genuinely `product_type: "CD"` with no vinyl signal anywhere else. Every
title recovered by the tag-vs-title-word gap above spells out the literal
word "vinyl"; restricting the fallback arm to that word (dropping the
`lp|ep|"` alternatives) recovers all 7 real cases and neither Eminem CD.

**Accepted noise, not specially filtered:** 4 Jackpot Records label
products (`Colossal Yes - Loosen the Lead...`, `Crock - Grok...`, `Dave
Depper - The Ram Project...`, `The Skabbs - Idle Threat - Compact Disc`)
carry a `Vinyl` tag or "Vinyl" in the title (vinyl/CD bundle SKUs, e.g.
`(Vinyl LP/CD)`) but their sole variant is priced $5.99–$9.99 and titled
literally `"CD"` (or, for the Skabbs, the product title itself is `-
Compact Disc`). Whether these represent an actual vinyl LP undersold at CD
pricing, or a same-listing CD add-on, can't be resolved from the JSON
feed alone. Same class of noise `asbestosrecords.py` accepts for its own
subscription-bundle SKU — not worth a special-case rule for 4/3,080
(0.13%) products, and since this crawler never appends the variant's own
title to the display title (see "Variants" below), the misleading `"CD"`
string never surfaces anywhere a user sees it; only the hardcoded `Vinyl`
format label does.

### No used inventory in the public catalog

Despite Jackpot's reputation (and its own meta description) as a used-vinyl
store, essentially no used stock appears in `products.json`: exactly one
product store-wide carries a `used` tag or the word "used" in its title
(`Slayer - The Vinyl Conflict (11LP Box Set) **USED**`), and no variant
carries a condition-grading option value anywhere in the 3,080-product
gated set (contrast `turntablelab.py`'s 158 condition-graded multi-variant
products). This crawler covers new stock only, which is apparently already
everything the online storefront lists.

### Variants: always exactly one per product

Across all 3,080 gated products, **every product has exactly one
variant** — 0 multi-variant products found (contrast Turntable Lab's 158).
Variant option values: `"New"` (3,002), Shopify's `"Default Title"`
placeholder (61), a one-off literal colour string on the rest (`"Orange
Vinyl LP"`, `"Clear Vinyl LP"`, etc., each appearing once), and the 4
`"CD"`-titled accepted-noise cases above.

Because no product ever has more than one variant, there is no
disambiguation case to handle (unlike Turntable Lab's
`len(variants) > 1` suffix rule, or Asbestos's default-title guard) — the
display title is always the parsed album title alone; the variant's own
`title` field is read only for `price`/`available`, never displayed.

### Pre-orders and availability

4 products carry a clean `"Pre-Order"` tag (`has_tag`, same helper every
sibling crawler uses). Only 1 variant store-wide is unavailable, and it is
not one of the 4 pre-orders — on this snapshot the pre-order check has no
live effect (the one unavailable variant is skipped either way), but it's
kept for the same reason Turntable Lab keeps its defensive fallback: a
future sold-out pre-order needs it to still surface. Standard rule: skip a
variant unless `available` or pre-order-tagged.

### Title split: hyphen/en-dash/em-dash, no vendor fallback

Reusing `cleorecs.py`'s regex verbatim:
`^(?P<artist>.+?)(?:\s+[-–—]\s*|\s*[-–—]\s+)(?P<album>.+)$` — it matches
both a plain space-hyphen-space split and the asymmetric-spacing/en-dash
cases live on this store (`Electric Wizard- Black Magic Rituals...` and
`Carn, Doug – The Best Of Doug Carn (2LP)`), which the narrower
`turntablelab.py`/`polyvinylrecords.py` hyphen-only pattern would miss.

123/3,080 gated products (4.0%) have no matching separator at all —
they're bare album titles with no artist prefix (`"Astral Weeks"`, `"Back
To Black"`, `"Dummy"`). Unlike `turntablelab.py` (colon-titles, `vendor`
usually the artist) or `asbestosrecords.py` (a curated `artists[]` field),
**`vendor` is not a usable fallback on this store**: 69/123 are
`vendor: "Jackpot Records"` — the store's own name, not an artist — and
the remaining 54 have a real label name in `vendor` that is still not the
artist (e.g. `"Best of the Doobie Brothers"` has `vendor: "Rhino"`, a
reissue label, not the band). There is no field on this store that
reliably supplies the missing artist. Per `asbestosrecords.py`'s "neither →
skip" precedent, these 123 products are skipped, not partially covered
with a wrong artist.

`html.unescape()` is not required — no HTML entities were found in any
sampled title (contrast Asbestos's `&#x27;`).

### Fields

- **price** — `float(variant["price"])` (a stringified decimal, like every
  other Shopify crawler; contrast Bigcartel's already-numeric field).
- **currency** — `"USD"` (confirmed: `cart_currency=USD` cookie, no
  alternate-currency variants observed).
- **url** — `f"{base_url}/products/{handle}"`.
- **cover_image_url** — `resolve_cover_image(product, variant)` — only
  15/3,080 variants carry their own `featured_image`, so this is almost
  always the product's first image.
- **format** — `"Vinyl"` unconditionally, matching every sibling Shopify
  crawler.

### Crawler shape

```python
class Crawler:
    site_name: str = "Jackpot Records"
    base_url: str = "https://jackpotrecords.com"
    genre_summary: str = "Portland, Oregon record store and label with a broad new-vinyl selection across genres."
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]: ...
```

`_COLLECTION_SLUG = "online-store"`, iterated with
`shopify_catalog.iter_products()` unchanged, filtered per-product by the
inclusion gate before parsing. Registration is automatic via `main.py`'s
startup loop — no wiring changes.

## Queue fan-out

3,080 gated products → 123 skipped (no title split) → 2,957 remaining →
1 skipped (unavailable, non-preorder) = **2,956 stock items**, × 3 eligible
release crawlers (`amazon`, `ebay`, `ebay_general`; `discogs_marketplace`
excluded by its `requires_discogs_release = True`) = **~8,868
`crawl_queue` jobs per sync**, the same order of magnitude as
`turntablelab.py`'s ~7,476. No window applied, consistent with every other
unwindowed label-store plugin.

## Testing

`backend/tests/test_jackpotrecords_crawler.py`, on
`test_turntablelab_crawler.py`'s pattern — `respx`-mocked `products.json`
responses and hand-written product literals taken from confirmed-live
products, no live site, no bot-detection risk. Cases:

- hyphen-split artist/album (space on both sides)
- en-dash split (`Carn, Doug – The Best Of Doug Carn`)
- asymmetric-spacing hyphen split (`Electric Wizard- Black Magic
  Rituals...`)
- no separator, `vendor` present but not used as fallback → skipped
  (distinguishing this from every prior crawler's vendor-fallback
  behavior)
- product with no `Vinyl` tag but "vinyl" in the title → included
- product with `Vinyl` tag but no "vinyl" word in title → included
- product with neither signal → excluded
- a title containing `LP` as part of its literal album name and no vinyl
  signal (`Eminem - The Marshall Mathers LP (CD)`) → excluded, not a false
  positive
- an unavailable, `Pre-Order`-tagged variant → kept
- an unavailable, non-pre-order variant → skipped
- a `"Default Title"` variant → title is the album alone, no suffix
- site metadata (`site_name`, `base_url`, `crawler_type`)

## Crawl citizenship and `robots.txt` compliance

Per the normative section of `2026-08-09-amoeba-store-crawler-design.md`.
This site's finding:

- `robots.txt`'s `User-agent: *` group is `Allow: /`, with disallows
  covering `/admin`, `/cart/`, `/checkout`, `/checkouts/`, `/orders`,
  `/account`, `/services`, `/sf_*`, `/cart.js`, `/recommendations/products`,
  and `sort_by`/multi-filter crawl traps — the same template
  `turntablelab.py` found. **None of these covers
  `/collections/online-store/products.json`**, the only path this crawler
  requests.
- `/agents.md` names `GET /collections/{handle}/products.json` explicitly
  under "Read-Only Browsing (No Authentication Required)".
- Both documents require checkout/payment to never complete without
  contemporaneous human approval. This crawler satisfies that trivially: it
  links out to the product page and never transacts.
- Load: 15 GETs per sync (14 pages of data at `limit=250` plus the
  terminating empty page), paced at `random.uniform(delay * 0.5, delay)`
  with `crawl_delay_seconds` defaulting to 30s. No detail-page fan-out.
  `iter_products()` fails fast on 429 and gives up after
  `consecutive_failure_limit` on anything else.
- If Jackpot Records blocks this crawler, adds a `Disallow` covering this
  path, or asks us to stop, the response is to disable the plugin.

## Runtime/agent document impact

No `.agents/INPUTS.md`, `.agents/OUTPUTS.md`, or `.agents/INSTRUCTIONS.md`
exist in this repo. This change adds no new trigger and no new inbound
interface — one new outbound host (`jackpotrecords.com`).

`backend/version.py`'s `VERSION` is derived from git and is not edited by
this change.
