# No Idea Records store crawler design

Date: 2026-08-19
Branch: `claude/noidea-records-crawler-044d5b`

## Problem

No Idea Records (`noidearecords.com`), the Gainesville, FL punk/emo label
and mailorder store (Hot Water Music, Against Me!, Chuck Ragan, Latterman,
Defiance Ohio, plus a Leatherface-adjacent distro), is not covered by any
existing crawler. It is a standard Shopify storefront — same family as the
~35 label-store `catalog` plugins already in `backend/crawlers/`, most
directly comparable to `deathwishinc.py`: a single browsable collection
that mixes vinyl in with CD/cassette/download variants of the same
products, needing a per-variant format filter rather than a clean
vinyl-only collection.

## Scope

Add `backend/crawlers/no_idea_records.py` as a `crawler_type="catalog"`
plugin, iterating the site's `list` collection ("Music") via
`shopify_catalog.iter_products()` — no new shared code needed — with
`deathwishinc.py`'s quoted-title parser and a per-variant vinyl filter.

**Non-goals**

- **No browser.** Confirmed live: `products.json` is served to plain
  `httpx` with no Cloudflare gate or bot interstitial.
- **No UCP/MCP integration.** `/agents.md` names `search_catalog`-style
  tools for buyer-approved checkout, not a bulk catalog dump — same
  reasoning every sibling Shopify crawler's spec gives.
- **No CD/cassette/download coverage.** This app's stock pipeline is
  vinyl-only by convention (`format: "Vinyl"` hardcoded across every
  sibling Shopify crawler); a per-variant filter excludes them (see
  below). Deliberate scope decision, confirmed with the user given this
  store's catalog is substantially CD/cassette too — not a case where
  vinyl is obviously the only real option.
- **No merch coverage.** Books, buttons, hoodies, posters, stickers,
  t-shirts, zines live in separate collections outside `list`, whose
  every product is `product_type: "Media"`; merch is not linked from
  `list` at all.

## Technical grounding

All figures below were confirmed against the live site on 2026-08-19, by
fetching and fully paginating `/collections/list/products.json`.

### Collection choice: `list` ("Music"), not per-format collections

`collections.json` lists format-specific collections (`lp-s` 205 products,
`7-inches` 67, `10-inches` 3, `cd` 111) alongside a `list` collection
("Music", handle `list`) that is their union — 360 products confirmed by
full pagination (250 + 110 + 0-on-page-3), 100% `product_type: "Media"`.
Using `list` means one collection pass instead of three-plus, with no
cross-collection dedup needed for products that carry both an LP and a CD
variant (many do).

### Title parsing: quoted album, `deathwishinc.py`'s regex unchanged

Titles follow `ARTIST "Album"` (`A WILHELM SCREAM "Partycrasher" +
POSTER`), sometimes with descriptive text before or after the quotes
(`+ POSTER`, `(BLUE-GREEN VARIANT)`, `TEST PRESSING`). Reusing
`deathwishinc.py`'s regex verbatim:

```
_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s*["“](?P<album>.+?)["”]')
```

Matches 352/360 titles live (97.8%). The 8 misses have no quotes at all
(`CLEVELAND BOUND DEATH SENTENCE`, `V/A - STATE OF THE UNION`, two
`KAE (KATE) TEMPEST` hyphen-form titles, a fanzine bundle, others) and
fall back to `vendor`, uniformly `"No Idea Records"` on every sampled
product regardless of the actual artist's real label — same accepted-risk
fallback `deathwishinc.py` documents for its own 0.6% miss rate.

### Vinyl filtering: `deathwishinc.py`'s regex, extended for curly quotes

`list` mixes vinyl variants with CD/CDep/cassette/download/DVD variants of
the same products (exactly `deathwishinc.py`'s situation, not
`asianmanrecords.py`'s clean product-level gate), so filtering is
per-variant against the variant title:

```
_VINYL_RE = re.compile(r'\bvinyl\b|\b\d*x?lp\b|\d+\s*[”″"]', re.IGNORECASE)
```

This store uses the curly right double quotation mark (`”`, U+201D) for
inch marks on some 7"/8" variants (`DARK GREEN 7”`, `BLUE 8"` uses a
straight quote — both forms are live), so the character class extends
`deathwishinc.py`'s bare `"` to `[”″"]`. Without it, 6 genuine 7" variants
are wrongly dropped.

Measured against all ~570 variants in `list`:

| Result | Count |
|---|---|
| Kept (vinyl) | 345 |
| Dropped — CD / CDep | 116 |
| Dropped — Download / Download (lossless) | 94 |
| Dropped — Cassette | 4 |
| Dropped — DVD, CD EP | 2 |
| Dropped — color-only vinyl variant, no format token (accepted miss) | 4 |
| Dropped — `Default Title` on a single-variant CD product | 1 |

**Accepted noise:** 4 variants across 3 products (Defiance Ohio's
`TRANSLUCENT BLUE`/`TRANSLUCENT GOLD`, Good Luck's `DARK GRAPE`, Chuck
Ragan's `BLUE / CLEAR SPLIT-COLOR`) are genuine vinyl pressings whose only
variant-title signal is a bare color name — no "LP", "vinyl", or inch
mark anywhere in the variant title, and no format hint in the product
title either. No structural signal on this store distinguishes these from
a genuinely non-vinyl bare-color variant, so they're dropped along with
real non-vinyl noise. 4/345 kept (1.2%), the same class of accepted
false-negative rate `deathwishinc.py` (0.6%) and `asianmanrecords.py`
(comparable) already carry.

### No pre-order tag on this store

Checked tags across all 360 `list` products for anything resembling
`pre-order`/`preorder`: none found. Unlike `deathwishinc.py`/
`asianmanrecords.py`, this crawler has no pre-order carve-out — an
unavailable variant is simply skipped
(`if not variant.get("available"): continue`), matching 141 confirmed-live
unavailable variants that should not surface as purchasable stock.

### `vendor` is not a usable artist fallback

`vendor` is `"No Idea Records"` on every sampled product, including
releases by artists never on the No Idea label roster (Against Me!,
Armalite) — the store's own name, not the artist, on every product
regardless of who actually recorded it. Same finding and same
non-fallback-for-primary-parsing decision as `deathwishinc.py`/
`asianmanrecords.py`; it's still used as the fallback *artist* value for
the 8 quote-less titles above, since nothing better exists.

### Fields

- **price** — `float(variant["price"])`.
- **currency** — `"USD"`.
- **url** — `f"{base_url}/products/{handle}"`.
- **cover_image_url** — `resolve_cover_image(product, variant)`, the
  shared helper every Shopify sibling crawler uses.
- **format** — `"Vinyl"` unconditionally, matching every sibling Shopify
  crawler (confirmed scope decision above).
- **title** — `f"{album_title} — {variant_title}"` unconditionally,
  matching `deathwishinc.py`'s own rule exactly (it suffixes every
  variant, not just when a product has more than one survivor). Confirmed
  live: no vinyl variant in `list` has an uninformative title like
  `Default Title` (0/345), so the suffix is always a real descriptor
  (e.g. `Partycrasher — RED VINYL + POSTER LP`).

### Crawler shape

```python
class Crawler:
    site_name: str = "No Idea Records"
    base_url: str = "https://noidearecords.com"
    genre_summary: str = "Gainesville, FL punk and emo label and mailorder store."
    genre: str = "punk"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]: ...
```

`_COLLECTION_SLUG = "list"`, iterated with `shopify_catalog.iter_products()`
unchanged, parsed by the title logic above, filtered per-variant by the
vinyl regex, fanned out per surviving variant. Registration is automatic
via `main.py`'s startup loop — no wiring changes.

## Queue fan-out

360 products → 345 vinyl variants survive filtering → title parsing
applies per-product (352/360 quoted, 8/360 vendor-fallback), all yield a
stock item (no additional drop stage). Per this repo's
per-item-crawler-fanout design, `_sync_stock` enqueues one `crawl_queue`
row per `item_key` — ~345 rows — each expanded across eligible release
crawlers at dispatch time (`amazon`, `ebay`, `ebay_general`;
`discogs_marketplace` excluded by its `requires_discogs_release = True`),
for ~1,035 dispatch work units per sync. Comparable in scale to
`asianmanrecords.py`'s ~354.

## Testing

`backend/tests/test_no_idea_records_crawler.py`, on
`test_asianmanrecords_crawler.py`'s pattern — `respx`-mocked
`products.json` responses and hand-written product literals taken from
confirmed-live products, no live site, no bot-detection risk. Cases:

- quoted-title product, single variant → parsed, kept
- quoted-title product with trailing descriptive text outside the quotes
  (`+ POSTER`, `(BLUE-GREEN VARIANT)`) → album title stops at the closing
  quote
- quote-less title → falls back to `vendor` as artist
- multi-variant product with a genuine vinyl color/edition split (no
  CD/cassette sibling) → all vinyl variants kept, each title suffixed
  with its own variant name
- multi-variant product with a CD sibling variant → CD variant dropped,
  vinyl variant(s) kept
- multi-variant product with a cassette sibling variant → dropped
- multi-variant product with a download-only sibling variant → dropped
- variant title using the curly right double quote for an inch mark
  (`DARK GREEN 7”`) → kept
- variant title using a straight quote for an inch mark (`BLUE 8"`) →
  kept
- bare-color vinyl variant with no format token at all (e.g.
  `TRANSLUCENT BLUE`) → dropped (documented accepted miss, not a bug)
- an unavailable variant → skipped
- site metadata (`site_name`, `base_url`, `genre`, `crawler_type`)

## Crawl citizenship and `robots.txt` compliance

Per the normative section of `2026-08-09-amoeba-store-crawler-design.md`.
This site's finding:

- `robots.txt`'s `User-agent: *` group is `Allow: /`, with disallows
  covering `/admin`, `/cart/`, `/checkout(s)/`, `/orders`, `/account`,
  `/services`, `/sf_*`, `/cart.js`, `/recommendations/products`, and
  `sort_by`/`filter`/`+`-encoded crawl traps — the same Shopify-default
  template `asianmanrecords.py`/`jackpotrecords.py` found. **None of
  these covers `/collections/list/products.json`**, the only path this
  crawler requests.
- `/agents.md` names `GET /collections/{handle}/products.json` explicitly
  under "Read-Only Browsing (No Authentication Required)".
- Both documents require checkout/payment to never complete without
  contemporaneous human approval. This crawler satisfies that trivially:
  it links out to the product page and never transacts.
- Load: 3 GETs per sync — `iter_products()` only terminates on an empty
  page, not a short one, so 360 products at `limit=250` means a full
  250-item page, a 110-item page, then a terminating empty page (3 GETs
  total). Paced at `random.uniform(delay * 0.5, delay)` with
  `crawl_delay_seconds` defaulting to 30s. No detail-page fan-out.
  `iter_products()` fails fast on 429 and gives up after
  `consecutive_failure_limit` on anything else.
- If No Idea Records blocks this crawler, adds a `Disallow` covering this
  path, or asks us to stop, the response is to disable the plugin.

## Runtime/agent document impact

No `.agents/INPUTS.md`, `.agents/OUTPUTS.md`, or `.agents/INSTRUCTIONS.md`
exist in this repo. This change adds no new trigger and no new inbound
interface — one new outbound host (`noidearecords.com`).

`backend/version.py`'s `VERSION` is derived from git and is not edited by
this change.
