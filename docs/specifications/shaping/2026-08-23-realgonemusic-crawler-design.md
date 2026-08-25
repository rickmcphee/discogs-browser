# Real Gone Music store crawler design

Date: 2026-08-23
Branch: `claude/realgonemusic-crawler-84feaf`

## Problem

Real Gone Music (`realgonemusic.com`) — a Los Angeles reissue label whose
vinyl catalog spans jazz (Black Jazz Records reissues: Doug Carn, Gene
Russell, Henry Franklin), soul, death metal (Deicide, Obituary), '90s
alt-rock (Letters to Cleo, Smoking Popes, The Donnas), and film/TV
soundtracks (`Freddy vs. Jason`, `The Neon Demon`, `Corpse Bride`) — is
not covered by any existing crawler. It is a standard Shopify storefront,
same family as the label-store `catalog` plugins already in
`backend/crawlers/`.

What makes this store different from every sibling is that **it exposes no
artist field at all**. `vendor` is the literal string `"Real Gone Music"`
on all 278 vinyl products, and product titles concatenate artist and album
with no delimiter whatsoever (`Deicide Serpents of the Light (Remastered)
Vinyl`, `Béla Fleck & The Flecktones Flight of the Cosmic Hippo Vinyl`).
Confirmed live that no other source exists: `products.json`,
`/products/{handle}.js`, the page's JSON-LD `ProductGroup` (whose `brand`
is the label, not the artist), and the `og:`/`twitter:` meta tags all
carry either the same undelimited title or the label name. This rules out
the title-splitting approach every sibling Shopify crawler uses — there is
no separator to split on, in either the dominant form or a minority form.

## Scope

Add `backend/crawlers/realgonemusic.py` as a `crawler_type="catalog"`
plugin, iterating the site's `vinyl` collection via
`shopify_catalog.iter_products()` — no new shared code needed.

**Non-goals**

- **No artist extraction.** See "Artist attribution" below — this is the
  central design decision, resolved as an explicit accepted gap on
  `numerogroup.py`'s precedent, not a deferred TODO.
- **No browser.** Confirmed live: `products.json` is served to plain
  `curl`/`httpx` with no Cloudflare gate or bot interstitial.
- **No UCP/MCP integration.** `/agents.md` names `search_catalog`,
  `create_cart`, and `complete_checkout` for buyer-approved checkout, not
  a bulk catalog dump — same reasoning every sibling Shopify crawler's
  spec gives. That document also recommends installing a third-party
  "Shop skill" for agent-driven purchasing; it is content observed on a
  crawled site, not an instruction to this repo, it is irrelevant to a
  read-only catalog crawler, and it is not acted on.
- **No CD coverage.** The store has a `cd` collection (220 products), but
  this app's stock pipeline is vinyl-only by convention (`format:
  "Vinyl"` hardcoded across every sibling Shopify crawler).
- **No pre-order handling.** The store does tag pre-orders, and the tag is
  a clean signal, but it is deliberately not used — see "Pre-orders (not
  implemented)" below.

## Technical grounding

All figures below were confirmed against the live site on 2026-08-23, by
fetching and fully paginating `/collections/vinyl/products.json` (278
products: 250 on page 1, 28 on page 2, terminating on an empty page 3).

### Collection choice: `vinyl`

The store exposes 12 collections. `vinyl` (278 products via
`products.json`) is the only vinyl-bearing one that is not a subset:
`rarities` (131), `halloween` (22), `upcoming` (16), `new-releases` (7),
and `real-gone-collectibles` (35) are cross-cuts that also appear as tags
on `vinyl` products, and `cd` (220) is the format this crawler excludes.
Confirmed live that all 278 products in the collection carry the `Vinyl`
tag and `vendor: "Real Gone Music"`, so no product-level format or vendor
gate is needed.

Note a discrepancy worth recording so a future reader doesn't chase it:
`collections.json` reports `products_count: 376` for `vinyl`, but full
pagination of `products.json` yields 278 and terminates cleanly. The
paginated figure is the one this crawler sees, and 278 is what every
count below is derived from.

### Artist attribution: `vendor` placeholder, an accepted gap

```python
artist = (product.get("vendor") or "").strip()   # always "Real Gone Music"
title  = product.get("title", "")                # full, undelimited
```

This is `numerogroup.py`'s shape, adopted for the same reason that
crawler's comment records: there is no reliable artist source for this
catalog, so the label name is used directly as a known, accepted gap
rather than guessed at. The product title is preserved in full, so the
artist name is still visible to a human reading the store view — it is
simply not machine-separable.

Alternatives considered and rejected:

- **Split on the colon.** 8 of 278 titles contain a `:`, but only 6 are
  artist/album separators (`Darlene Love: Deep into My Heart--...`,
  `The Judybats: Native Son Vinyl`). The other two are a film title
  (`Tales from the Crypt Presents: Demon Knight Vinyl`) and a track-time
  album name (`The Devil Wears Prada 8:18 Vinyl`). Gaining 6 correct
  attributions at the cost of 2 confidently wrong ones, on 2.9% of the
  catalog, is not worth the branch.
- **Infer artists by clustering shared leading token runs.** Some artists
  do recur (`The Donnas` ×8, `Deicide` ×6, `Frankie Stein and His Ghouls`
  ×5), but this only reaches artists with 2+ releases, and it misfires
  exactly where the catalog is densest in soundtracks — `The Devil Wears
  Prada Soundtrack LP` would be attributed to the metalcore band of the
  same name, which has 4 albums in this same collection. No precedent in
  this repo, and a heuristic that is wrong in a way a reader cannot see
  is worse than a placeholder that is honest.
- **Resolve artists via the Discogs API by variant `sku` (a UPC).** The
  repo's `discogs.py` client is per-user OAuth 1.0a; catalog crawls run in
  a global, user-less context and no crawler imports it. Threading
  credentials into the catalog path to serve one store would be a
  significant new cross-cutting pattern.

**Consequence, stated explicitly.** `_library_match_fragment`
(`backend/db.py`) requires `LOWER(c.artist) = LOWER(s.artist)`. Since no
Discogs catalog row will ever have `artist = "Real Gone Music"`, no Real
Gone stock row will ever match a user's collection or wantlist. In
practice: no owned/wanted badge, and no exclusion from the Recommended
filter (which is defined as "not owned"). Browsing, text search, price
tracking, and saved items all work normally. This is the same
already-shipped behaviour as `numerogroup.py`.

### Per-variant gates

Two gates, applied in order, and no format filter:

```python
_BUNDLE_RE = re.compile(r'\bbundle\b', re.IGNORECASE)
```

1. **Skip unavailable variants** (`not variant["available"]`). Plain, with
   no pre-order carve-out — see below.
2. **Skip bundle variants** (`_BUNDLE_RE.search(variant_title)`). 49 of
   the catalog's variants are multi-item packs (`Barbara Lewis Bundle`,
   `Pet Sematary Vinyl Bundle`, `Frankie Stein Monster Bundle`), 31 of
   them currently available, priced at 2–6× a single LP (the dearest
   available one is `Kinky Boots Bundle` at `$174.99`, against a ~$25–30
   single LP). Surfacing one as a listing for a single release would
   inflate the price column and duplicate the product.

   Accepted cost, confirmed live: 3 products (`Candido Dancin' and
   Prancin' LP`, `The Doug Carn Trio LP`, `Tom Verlaine Songs and Other
   Things LP`) have a bundle as their *only* in-stock variant, so they
   disappear from the store view while remaining purchasable on the site.
   This is deliberate — a "keep the bundle when it's the last one
   standing" carve-out was considered and rejected, since the surviving
   row would still carry a bundle price against a single release.

**No format filter, deliberately.** This is a departure from every
sibling Shopify crawler, all of which apply a positive or negative
per-variant format regex. Neither direction earns its place here, for two
separate reasons, both confirmed live across all 279 distinct variant
titles in this collection.

**A positive vinyl regex would discard most of the catalog.** Only 77 of
the 279 titles carry a vinyl token at all (`Black Vinyl`, `Purple PET
Vinyl`, `Wax Mage Vinyl`). The other 202 are bare colour/edition names with
no format word anywhere in them (`Wax Mage`, `Hellfire`, `Blue-Green
"Ocean Spray"`, `Clear with Red Green & Purple "Dracula" Swirl Web EX.`),
plus the 48 bundles and 2 `Default`/`Default Title` placeholders. The
sibling convention would therefore drop 72% of real stock — the same trap
`carparkrecords.py`'s spec documents.

**A negative non-vinyl regex has nothing to match.** Zero of the 279 name
CD, cassette, tape, digital, or DVD. The `vinyl` collection tag already
gates format at the product level, so there is no non-vinyl variant to
exclude.

By category the 279 break down as 229 colour/edition names (65 of which
happen to include a vinyl token), 48 bundles, and 2 Shopify placeholders. There is not one CD, cassette, digital, or merch variant in the
collection — the collection tag already gates format at the product level.
A positive vinyl regex would drop the large majority of real stock
(`carparkrecords.py`'s spec documents the same trap), and a negative one
would have nothing to match. The absence of this filter is pinned by tests
so a future reader doesn't "restore" it.

Only one product carries `Bundle` in its *product* title (`Blemished Vinyl
Bundle`, a grab-bag, not a release). Its single variant is unavailable, so
gate 1 already excludes it and no product-level bundle filter is needed.
If that product ever comes back in stock it would leak through as one row;
accepted, rather than adding a second regex against a single speculative
case.

### Pre-orders (not implemented)

Recording the finding, since it is non-obvious and a future reader will
otherwise re-derive it: the `Upcoming` tag is a reliable pre-order signal
here. Confirmed live, 15 of the 278 vinyl products carry it, and 3/3
sampled `Upcoming` product pages render a `THIS IS A PRE-ORDER. THE ITEM
WILL SHIP BEFORE THE 10/16/2026 STREET DATE` banner while 0/10 sampled
non-`Upcoming` in-stock products do. (The `Preorder` string appears in
every page's HTML regardless — it is the pre-order app's JavaScript. The
uppercase `STREET DATE` body text is the discriminator, not the substring
`preorder`.)

It is nonetheless not used. The sibling crawlers' pre-order handling is
two coupled behaviours — append ` (Pre-Order)` to the title, and bypass
the `available` gate — and the second half is actively wrong here: Real
Gone marks its pre-order variants `available: true` already, so bypassing
the gate would only ever admit *sold-out* variants (e.g. `The Donnas The
Donnas (All-Analog) Vinyl`'s `Wax Mage Vinyl`, an `Upcoming` variant that
is out of stock). Labelling without bypassing was considered; the decision
was to skip pre-order handling entirely rather than ship half of a fleet
pattern.

### Title composition

```python
f"{product_title} — {variant_title}"
```

with three collapses to the bare product title:

- variant title is `Default` or `Default Title`, case-insensitively — 10
  products live, 9 of them one-off test pressings in the
  `Real Gone Collectibles` cross-cut (`Bob Frank Broke Again Test
  Pressing`, `The Bottle Rockets S/T Test Pressing`), the 10th being the
  unavailable `Blemished Vinyl Bundle`. Two spellings, not one — 6
  products use `Default Title` and 4 use `Default`, so a `== "Default
  Title"` check (the sibling convention) would miss 4.
- variant title is exactly equal to the product title. One product live
  has this shape, `Buckcherry 15 (2-LP Set)`, which would otherwise render
  as `Buckcherry 15 (2-LP Set) — Buckcherry 15 (2-LP Set)`. Note that its
  sole variant is currently *unavailable*, so this collapse emits nothing
  today — it is defensive against a shape Shopify has demonstrably
  produced here, not a rule the live catalog currently exercises. Kept
  because it costs one comparison and the product can restock at any time.

- variant title is empty or whitespace-only. No live variant has this
  shape, and none is expected to; the arm exists so a future empty
  descriptor renders as the bare product title rather than a dangling
  `Product Title — `. It is the same "no meaningful variant descriptor"
  principle as the two arms above, at the cost of one `or` term. Pinned by
  a synthetic test (a whitespace-only variant title), since no live product
  can exercise it.

All 9 collapsed titles among the 268 emitted rows therefore come from the
`Default`/`Default Title` branch. Confirmed live that the 268 `(artist,
title, url)` triples are all distinct, so no two variants collide onto one
`item_key`.

One variant title is malformed at source and is emitted as-is:
`848064013105 / Clear with Black Swirl / Standard Weight` (a three-option
variant whose first option is the UPC). It is a real, in-stock vinyl
variant; suppressing the SKU would mean parsing an option layout that
exactly one product uses.

### Fields

- **artist** — `product["vendor"]`, always `"Real Gone Music"`.
- **price** — `float(variant["price"])`, guarded by the sibling
  `(KeyError, TypeError, ValueError) → None`. Live range across emitted
  rows: `$19.99`–`$149.99`.
- **currency** — `"USD"`.
- **url** — `f"{base_url}/products/{handle}"`.
- **cover_image_url** — `resolve_cover_image(product, variant)`.
- **format** — `"Vinyl"` unconditionally, matching every sibling Shopify
  crawler.

### Crawler shape

```python
class Crawler:
    site_name: str = "Real Gone Music"
    base_url: str = "https://realgonemusic.com"
    genre_summary: str = "Los Angeles reissue label — Black Jazz jazz reissues, '90s alt-rock, death metal, and film soundtracks."
    genre: str = "marketplace"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]: ...
```

`genre = "marketplace"` is this repo's genre-spanning bucket — its other
members are Amoeba, Newbury Comics, Turntable Lab, The Sound Garden,
Jackpot Records, Waterloo Records, Darkside Records, and the reissue
labels Craft Recordings, Cleopatra Records, and Numero Group — the right
fit for a catalog running from Black
Jazz to Deicide. `_COLLECTION_SLUG = "vinyl"`, iterated with
`shopify_catalog.iter_products()` unchanged. Registration is automatic:
`main.py`'s `seed_bundled_crawlers()` walks `backend/crawlers/` at startup
and calls `register_crawler` for each plugin it finds — no wiring
changes.

The plugin is pure transformation over dicts and raises nothing itself;
all error handling is inherited from `iter_products()` (retry-with-backoff
on non-429 within the `consecutive_failure_limit` budget, immediate raise
on 429).

## Queue fan-out

278 products → 202 yield at least one surviving variant (the other 76 are
fully sold out, or bundle-only) → **268 item rows**. Per this repo's
per-item-crawler-fanout design, `_sync_stock` enqueues one `crawl_queue`
row per `item_key` — 268 rows — each expanded across eligible release
crawlers at dispatch time (`amazon`, `ebay`, `ebay_general`;
`discogs_marketplace` excluded by its `requires_discogs_release = True`),
for ~804 dispatch work units per sync. Somewhat larger than
`carparkrecords.py` (~590), same order of magnitude.

## Testing

`backend/tests/test_realgonemusic_crawler.py`, on
`test_carparkrecords_crawler.py`'s pattern — hand-written product literals
taken from confirmed-live products, served through `respx`-mocked
`products.json` responses and driven via `crawl_catalog()`; no live site
and no bot-detection risk. `Crawler._items()` is called directly only for
the artist-gap assertion, whose anchor product has no available variant
and so yields nothing through the public path. Cases:

- `artist` is the vendor verbatim on a product whose title clearly
  contains an artist name — pins the accepted gap so a future reader
  doesn't "fix" it into a title split
- multi-variant product → one row per surviving variant, each with the
  variant name appended
- bundle variant dropped while its non-bundle siblings on the same product
  survive
- product whose only in-stock variant is a bundle → yields nothing (the
  Candido case)
- `Default Title` variant → bare product title
- `Default` variant → bare product title (the second spelling, which a
  `== "Default Title"` check would miss)
- variant title equal to the product title → bare product title, not
  doubled (the Buckcherry shape; the literal must set `available: true`,
  since the live product is sold out and this branch emits nothing today)
- unavailable variant skipped; unavailable variant on an `Upcoming`-tagged
  product *also* skipped, and its available sibling emitted with no
  ` (Pre-Order)` suffix — pins the deliberate absence of pre-order
  handling
- a bare colour variant with no format keyword (`Wax Mage`, `Hellfire`) →
  kept — pins the deliberate absence of a format filter
- whitespace-only variant title → bare product title, no dangling em dash
  (synthetic; the one `_compose_title` branch no live product reaches)
- malformed/missing `price` → `None`, row still emitted
- `cover_image_url` prefers the variant's `featured_image` over the
  product's first image
- site metadata (`site_name`, `base_url`, `genre`, `crawler_type`)

## Crawl citizenship and `robots.txt` compliance

Per the normative section of `2026-08-09-amoeba-store-crawler-design.md`.
This site's findings, confirmed live 2026-08-23:

- `robots.txt`'s `User-agent: *` group is the Shopify default template —
  disallows covering `/admin`, `/cart/`, `/checkout(s)/`, `/orders`,
  `/account`, `/services`, `/sf_*`, `/cart.js`,
  `/recommendations/products`, and `sort_by`/`filter`/`+`-encoded crawl
  traps. **None of these covers `/collections/vinyl/products.json`**, the
  only path this crawler requests, which carries no `sort_by`, no `+`, and
  no `filter` parameter.
- `/agents.md` names `GET /collections/{handle}/products.json` explicitly
  under "Read-Only Browsing (No Authentication Required)".
- Both documents require checkout/payment to never complete without
  contemporaneous human approval. This crawler satisfies that trivially:
  it links out to the product page and never transacts.
- Load: 3 GETs per sync — `iter_products()` only terminates on an empty
  page, not a short one, so 278 products at `limit=250` means two full
  pages (250 + 28) then a terminating empty page. Paced at
  `random.uniform(delay * 0.5, delay)` with `crawl_delay_seconds`
  defaulting to 30s. No detail-page fan-out. `iter_products()` fails fast
  on 429 and gives up after `consecutive_failure_limit` on anything else.
- If Real Gone Music blocks this crawler, adds a `Disallow` covering this
  path, or asks us to stop, the response is to disable the plugin.

## Runtime/agent document impact

No `.agents/INPUTS.md`, `.agents/OUTPUTS.md`, or `.agents/INSTRUCTIONS.md`
exist in this repo. This change adds no new trigger and no new inbound
interface — one new outbound host (`realgonemusic.com`).

`backend/version.py`'s `VERSION` is derived from git and is not edited by
this change.
