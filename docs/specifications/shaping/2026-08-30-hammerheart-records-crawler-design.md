# Hammerheart Records store crawler design

Date: 2026-08-30
Branch: `claude/hammerheart-indiem-crawler-tzlrd0`

## Problem

Hammerheart Records (`hammerheart.indiemerch.com`) — the Dutch death, doom,
black, and Viking/folk metal label's US webstore, carrying Master, Cryptopsy,
Therion, Running Wild, Trouble, Vintersorg, Mithotyn, Pungent Stench,
Necrophobic, and the rest of its roster — is not covered by any existing
crawler. Despite the IndieMerch domain, the store is a standard Shopify
storefront (confirmed live: `products.json` and `collections.json` answer
with ordinary Shopify payloads, `Shopify.currency = {"active":"USD"}` in the
page), same family as the label-store `catalog` plugins already in
`backend/crawlers/`.

The artist lives cleanly in `vendor` on every product, but the store leads
most product titles with the artist *again*, usually in ALL CAPS
(`TROUBLE - Psalm 9 / Black Vinyl LP` against vendor `Trouble`), which
`shopify_catalog.strip_vendor_prefix`'s exact-case match cannot strip — so
this crawler carries its own case-insensitive prefix strip. The vinyl
collection also files two CDs under a vinyl `product_type`, so a negative
format filter on the title backs the collection's own typing.

## Scope

Add `backend/crawlers/hammerheart.py` as a `crawler_type="catalog"` plugin,
iterating the site's `vinyl` collection via
`shopify_catalog.iter_products()` — no new shared code needed.

**Non-goals**

- **No browser.** Confirmed live: `products.json` is served to plain
  `httpx` with no Cloudflare gate or bot interstitial.
- **No UCP/MCP integration.** The site's `robots.txt` and `/agents.md`
  ("Agent Instructions — Hammerheart Records USA") name UCP/MCP endpoints
  for buyer-approved cart/checkout, not a bulk catalog dump — same
  reasoning every sibling Shopify crawler's spec gives.
- **No CD/cassette coverage.** The store's CD stock (`CDs`, `CD`, `2xCD`,
  … product types) is out of scope; this app's stock pipeline is
  vinyl-only by convention (`format: "Vinyl"` hardcoded across every
  sibling Shopify crawler).
- **No merch coverage.** The store carries none anyway — every product in
  the full catalog is music (confirmed live: the whole store's
  `products.json` contains only vinyl, CD, and one cassette
  `product_type`).

## Technical grounding

All figures below were confirmed against the live site on 2026-08-30, by
fully paginating `/collections/vinyl/products.json` (447 products across 2
populated pages) and the whole store's `/products.json` for the superset
check.

### Collection choice: `vinyl`

`collections.json` lists per-artist collections, per-format collections
(`12`, `2x12`, `cd`, `cds`, `cassette`, …), sale/new-release collections,
and `vinyl`. Confirmed live that `vinyl` is *exactly* the store's
vinyl-typed inventory: the set of handles in `vinyl` equals the set of
products in the whole store's `products.json` whose `product_type` is
`12"` or `2x12"` — nothing missing in either direction. No union of
narrower collections is needed.

### Artist: `vendor`, always

Every product in the vinyl collection carries a non-blank `vendor`, and it
is always the artist in proper mixed case (`Trouble`, `Sig:Ar:Tyr`,
`SETYØURSAILS`), never the label or a distributor. A blank vendor has no
artist source and is skipped, the fleet's "no artist source -> skip"
convention; a catalog whose *every* vendor is blank raises instead (see
drift guards below).

### Title: case-insensitive artist-prefix strip

Three title shapes are live:

- `ARTIST - Album / Pressing-color Vinyl LP` with the artist in ALL CAPS —
  the dominant shape. `strip_vendor_prefix`'s exact-case match misses all
  of these.
- `Artist - Album / …` in the vendor's own casing — the minority prefixed
  shape.
- `Album (Pressing-color vinyl)` with no artist prefix at all.

A per-product regex `^{re.escape(vendor)}\s*[-/]\s+` (IGNORECASE) strips
the first two shapes and leaves the third alone. Confirmed live: 396/447
titles strip (only 98 of them exact-case), including the separator
oddities the pattern is written for — a tab before the dash on two
products (`Sarcasm\t- Lifeforce Omnibound / …`) and one product
separating with ` / ` instead (`ORPHANAGE / Oblivion / Blue Vinyl LP`) —
and the residual 51 are all the unprefixed parenthetical shape. Requiring
whitespace after the separator keeps a self-titled album with no separator
(`Abramelin (Black vinyl)`) intact, and keeps a hypothetical
`Artist-something` compound from splitting.

The pressing-color/format tail (`/ Black Vinyl LP`, `(Gold vinyl)`) is
kept in the title, matching `centurymedia.py`/`napalmrecords.py`.
`db._library_match_fragment`'s exact-or-prefix-with-space match is
case-insensitive and anchored at the start, so `Psalm 9 / Black Vinyl LP`
matches a catalog `Psalm 9` — only a *leading* artist would break it,
which is what the strip removes.

The store's own data carries mojibake on a few titles (`Dunkelgl√∂d` for
*Dunkelglöd*) — passed through as-is; the artist comes from the clean
`vendor` field either way.

### Format filter: negative, title-level, with a vinyl override

The vinyl collection mistypes two CDs as `12"` (`ARTCH - Another Return /
CD`, `MONOLITHE - Black Hole District / Digipak CD`), so a product whose
title names a non-vinyl format with no vinyl signal is dropped:

```python
_NON_VINYL_RE = re.compile(r"\b\d*x?(?:cds?|dvds?)\b|\bdigipak\b|\bcassettes?\b|\btapes?\b", re.IGNORECASE)
_VINYL_RE = re.compile(r'\bvinyl\b|\b\d*x?lps?\b|\d+\s*(?:"|inch)|\bpicture dis[ck]\b', re.IGNORECASE)
```

The counted `\d*x?` allowance on both sides follows
`spv.py`/`onetwothreefourgo.py`: a disc count binds to its format word
with no word boundary between them, so a bare `\bcds?\b` cannot see the
CD in `2xCD`. Confirmed live: the filter drops exactly the two mistyped
CDs, no title pairs a non-vinyl word with a vinyl word (so the override
fires on nothing today, but keeps a future genuine `LP + CD` bundle), and
the three titles with no format signal at all (`Sagovindars Boning`,
`Abramelin`, `Gloom Immemorial (Gold viny)` [sic]) never reach the filter
— it only fires on a non-vinyl match.

### Pre-orders: tag suffix, **no availability bypass**

`has_tag(product, "preorder")` appends ` (Pre-Order)` to the title, as
across the fleet. Deliberately absent is the sibling label-store bypass
that keeps *unavailable* variants of a pre-order-tagged product: those
stores flag purchasable pre-orders unavailable; this store does the
opposite. Confirmed live: 23/24 pre-order-tagged vinyl products report
`available: true`, and the one exception (`Gathered Around the Oaken
Table (Gold vinyl)`) renders "Sold Out" on its own product page while its
Black sibling remains purchasable — an unavailable pre-order here means
the allocation is gone, and publishing an unbuyable record at a price is
worse than omitting a buyable one. Same call, on the same evidence shape,
as `darksiderecords.py` and `onetwothreefourgo.py`. A test pins the
absence so a later change is deliberate.

### Variants: single today, disambiguated if that changes

Every live product is single-variant (447/447), with the pressing color in
the product title and the variant title either `Default Title` or a
redundant color name — so nothing is appended on the live catalog. On a
multi-variant product the variant descriptor *is* appended
(`… — {variant title}`), because rows sharing (artist, title, url) collapse
onto one `item_key`. `stock_items.item_key` is deliberately non-unique
(the same record can be seen by several crawlers), so the sync *accepts*
the collision — which is exactly the problem: the colliding rows share one
`stock_item_identities` row, one `crawl_queue` target, and one
`(item_key, crawler_id)` listings slot, so judgments, saves, and
release-crawler results cannot tell the variants apart. Blank/placeholder
variant titles fall
back to the immutable variant id, and a variant with neither raises —
`darksiderecords.py`'s chain, for `onetwothreefourgo.py`'s reasons.

### Drift guards

`db.replace_stock_items()` DELETEs the crawler's previous snapshot before
inserting, and `_sync_stock` only skips that call when the crawl raised —
so the two states indistinguishable from a healthy empty store raise
instead of completing empty:

- zero products from the collection (renamed or removed), and
- a non-empty collection in which no product carries a vendor
  (artist-source drift).

Sold-out, format-filtered, and blank-vendor products all count toward the
tallies first, so a legitimately quiet catalog trips neither.

### Fields

- **price** — `float(variant["price"])`. Confirmed live: every variant
  carries a string price; none missing.
- **currency** — `"USD"` (confirmed via the page's `Shopify.currency`).
- **url** — `f"{base_url}/products/{handle}"`.
- **cover_image_url** — `resolve_cover_image(product, variant)`. Confirmed
  live: every product carries at least one image.
- **format** — `"Vinyl"` unconditionally, matching every sibling Shopify
  crawler.

### Crawler shape

```python
class Crawler:
    site_name: str = "Hammerheart Records"
    base_url: str = "https://hammerheart.indiemerch.com"
    genre_summary: str = "Dutch label for death, doom, black, and Viking/folk metal."
    genre: str = "metal"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]: ...
```

`_COLLECTION_SLUG = "vinyl"`, iterated with
`shopify_catalog.iter_products()` unchanged. Registration is automatic via
`main.py`'s startup loop — no wiring changes. `site_name` follows
`napalmrecords.py`'s precedent of naming the label rather than the
storefront's own "Hammerheart Records USA Store" banner: the store *is*
the label's official US outlet, and Settings links it by name.

## Queue fan-out

Replaying the crawler over the fully-cached live catalog: 447 products →
2 dropped by the format filter → 307 rows emitted (the rest are sold-out
single-variant products, plus the one sold-out pre-order), with no
(artist, title, url) collisions, no row missing a price or cover image,
no blank artist, and no artist left leading a title. Per this repo's
per-item-crawler-fanout design, `_sync_stock` enqueues one `crawl_queue`
row per `item_key` — ~307 rows — each expanded across eligible release
crawlers at dispatch time (`discogs_marketplace` excluded by its
`requires_discogs_release = True`).

## Testing

`backend/tests/test_hammerheart_crawler.py`, on
`test_centurymedia_crawler.py`'s pattern — `respx`-mocked `products.json`
responses, no live site. Fixtures distinguish three provenances (captured
live product, live product with one field altered to reach a branch the
live data never takes, wholly invented shape), each marked at its
definition. Cases: the three title shapes and both separator oddities;
the self-titled no-strip; pre-order suffix; the pinned absence of the
pre-order availability bypass; sold-out skip; both live mistyped CDs and
the invented counted-CD and hybrid-bundle shapes; blank-vendor skip;
null-variants product; both drift-guard raises; multi-variant
disambiguation, its id fallback, and its no-identity raise; site
metadata.

## Crawl citizenship and `robots.txt` compliance

Per the normative section of `2026-08-09-amoeba-store-crawler-design.md`.
This site's finding, confirmed live 2026-08-30:

- `robots.txt`'s `User-agent: *` group is `Allow: /`, with disallows
  covering `/admin`, `/cart/`, `/checkout(s)/`, `/orders`, `/account`,
  `/services`, `/sf_*`, `/cart.js`, `/recommendations/products`, and
  `sort_by`/`filter`/`+`-encoded crawl traps — the same Shopify-default
  template every sibling Shopify crawler found. **None of these covers
  `/collections/vinyl/products.json`**, the only path this crawler
  requests (its `limit`/`page` params match none of the trap patterns).
- `/agents.md` ("Agent Instructions — Hammerheart Records USA") describes
  read-only browsing and UCP/MCP endpoints for buyer-approved purchasing.
  Both it and `robots.txt` require checkout/payment to never complete
  without contemporaneous human approval. This crawler satisfies that
  trivially: it links out to the product page and never transacts. (Both
  documents also recommend installing a third-party "Shop skill" for
  agent-driven purchasing — irrelevant to this read-only catalog crawler,
  and not acted on.)
- Load: 3 GETs per sync — 447 products at `limit=250` is two full pages,
  then the terminating empty page `iter_products()` needs to know the
  collection is exhausted. Paced at `random.uniform(delay * 0.5, delay)`
  with `crawl_delay_seconds` defaulting to 30s. No detail-page fan-out.
  `iter_products()` fails fast on 429 and gives up after
  `consecutive_failure_limit` on anything else.
- If Hammerheart Records blocks this crawler, adds a `Disallow` covering
  this path, or asks us to stop, the response is to disable the plugin.

## Runtime/agent document impact

No `.agents/INPUTS.md`, `.agents/OUTPUTS.md`, or `.agents/INSTRUCTIONS.md`
exist in this repo. This change adds no new trigger and no new inbound
interface — one new outbound host (`hammerheart.indiemerch.com`).

`backend/version.py`'s `VERSION` is derived from git and is not edited by
this change.
