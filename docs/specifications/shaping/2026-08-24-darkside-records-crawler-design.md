# Darkside Records store crawler design

Date: 2026-08-24
Branch: `claude/darkside-records-crawler-4kfeag`

## Problem

Darkside Records (`shop.darksiderecords.com`) — a Poughkeepsie, New York
independent record store, open since 2011 and the Hudson Valley's largest —
is not covered by any existing crawler. It is a standard Shopify storefront,
the same family as the ~46 `catalog` plugins already in `backend/crawlers/`,
but it is a *retail store* rather than a label, so its shape matches
`jackpotrecords.py` (distributor `vendor`, `Artist- Album` titles) rather
than the label-store crawlers.

What makes this store different from every sibling is **scale**: its
`new-vinyl` collection reports 58,416 products, two orders of magnitude past
the largest existing crawler. That turns out to be not merely a pacing
concern but a hard blocker, and resolving it is the central design decision
below.

## Scope

Add `backend/crawlers/darksiderecords.py` as a `crawler_type="catalog"`
plugin, iterating the site's `new-vinyl-in-stock` collection via
`shopify_catalog.iter_products()` — no new shared code needed.

**Non-goals**

- **No used vinyl.** The store's used inventory (`used-vinyl`,
  `usedtuesforyou`, 694 products) is deliberately out of scope: it is
  one-off VG+ stock that churns weekly, so listings go stale between syncs,
  and there is no condition field in the stock item shape to carry a grade.
- **No CDs, books, games, or merch.** The store sells all of them (`books`
  714, `7-99-cds` 644, `board-games` 88, `plush` 60); this app's stock
  pipeline is vinyl-only by convention (`format: "Vinyl"` hardcoded across
  every sibling).
- **No browser.** Confirmed live: `products.json` is served to plain
  `curl`/`httpx` with no Cloudflare gate or bot interstitial.
- **No UCP/MCP integration.** See "Crawl citizenship" below.

## Technical grounding

All figures below were confirmed against the live site on 2026-08-24 by
fully paginating `/collections/{slug}/products.json`.

### Collection choice: `new-vinyl-in-stock`, because `new-vinyl` is uncrawlable

This is the load-bearing decision, and it is forced rather than preferred.

**Shopify's `products.json` serves at most 100 pages.** Confirmed live
against `new-vinyl`: page 99 → 250 products, page 100 → 250 products,
page 101 → **HTTP 400**, and every page past it likewise. At `limit=250`
that is a hard ceiling of 25,000 products per collection. `new-vinyl`
reports 58,416, so roughly 33,000 of its products are unreachable through
this endpoint no matter how the crawl is paced.

Worse, the failure is not graceful. `iter_products()` treats a 400 as a
generic `httpx.HTTPError` (only 429 gets the fail-fast path), so it would
`continue` and retry the same page until `consecutive_failure_limit` is
exhausted — up to ~10 minutes of retries — and then raise, after having
already spent ~90 minutes paginating the first 100 pages. `new-vinyl` is
therefore not a "slow but workable" option; it is a guaranteed failed sync.

`new-vinyl-in-stock` is the crawlable alternative, and it happens to carry
the better semantic too:

| | `new-vinyl` | `new-vinyl-in-stock` |
|---|---|---|
| `products_count` | 58,416 | 5,157 |
| paginates cleanly | **no** (HTTP 400 past page 100) | yes (21 pages) |
| products actually retrieved | — | **5,141** |
| tagged `instore-available` | 52% | **100%** |

The `instore-available` tag is the store's own marker for stock physically
on the shelf in Poughkeepsie, as opposed to titles it can order from a
distributor. Since this app exists to tell a user where they can actually
buy a record now, "on the shelf" is the more useful set, not a compromise.

Pagination terminates cleanly: 20 pages of 250 plus a final page of 141
(5,141), then an empty page 22. Note the small discrepancy between the
collection's self-reported `products_count` (5,157) and the paginated total
(5,141) — the paginated figure is what this crawler sees, and every count
below derives from it. The same discrepancy is recorded in
`2026-08-23-realgonemusic-crawler-design.md`.

The genre sub-collections (`new-vinyl-rock-a` … `new-vinyl-rock-z`,
`new-vinyl-jazz`, `new-vinyl-metal`, …) were considered as a way to reach
the full `new-vinyl` set by splitting it under the 25,000 cap. Rejected:
it would mean ~50 collection slugs hardcoded in the plugin, each needing
its own pagination and de-duplication pass across overlapping membership,
to surface stock the store does not physically have — a large amount of
new machinery for the less useful half of the catalog.

### Artist attribution: title split, no vendor fallback

```python
_TITLE_RE = re.compile(r'^(?P<artist>.+?)(?:\s+[-–—]\s*|\s*[-–—]\s+)(?P<album>.+)$')
```

Lifted unchanged from `jackpotrecords.py`. The asymmetric-spacing
alternation is what this store needs: its dominant form is `Artist- Album`
with the hyphen glued to the artist and a space only after it (`Jay Reatard-
Blood Visions (Vinyl)`), not the `Artist - Album` the label stores use. Four
products use an en-dash the same way (`Talib Kweli & Madlib– Liberation 2`).

`vendor` is **not** a fallback, and must not become one: it holds the
distributor (`THE ORCHARD`, `UMG`, `WMX`, `AEC`, `REDEYE MUSIC
DISTRIBUTION`, `Alliance - BT`), never the artist.

Two properties of the non-greedy `.+?` artist group, both confirmed live and
both pinned by tests:

- **It splits on the first separator, not a later one.** 316 products carry
  a further spaced hyphen inside the album name — `The Specials- Live From
  The Cathedral - Black Vinyl [Import] (Black, United Kingdom - Import)`
  yields artist `The Specials` with the rest of the string intact as the
  album.
- **It leaves hyphenated artist names alone.** Because both alternatives
  require whitespace adjacent to the dash, a dash with none is skipped.
  Verified against all 45 live products whose artist contains one:
  `Blink-182`, `Run-Dmc`, `Jean-Luc Ponty`, `Olivia Newton-John`,
  `The-Dream`, `B-52's`, `Ne-Yo`, `GA-20`, `Ghost-Note`, `Dominique
  Fils-Aime`. Short artist names also survive (`X`, `U2`, `AZ`, `YG`).

**Products with no separator are dropped, not guessed at.** 178 of 5,141
(3.5%) have no artist to extract, and sampling confirms they are genuinely
artist-less — soundtracks and compilations such as `Hocus Pocus (Original
Motion Picture Soundtrack) [Blue Jay 2LP Vinyl]`, `Bridgerton: Season Two
(Soundtrack From The Netflix Series)`, `Disappearing Lines: Chiptune Music
of Tetris`. Attributing these to the distributor would be actively wrong;
dropping them costs 3.5% of the collection.

### Title kept verbatim

The album half is emitted exactly as the store wrote it, with **no**
suffix stripping. Two things this preserves deliberately:

- **The trailing `(Vinyl)`**, on 68% of titles (3,379 of 4,963 parsed).
  Stripping it was considered and is unnecessary:
  `db.py`'s `_library_match_fragment` matches a stock title against the
  catalog on `LOWER(s.title) = LOWER(c.title) OR LOWER(s.title) LIKE
  LOWER(c.title) || ' %'` — exact **or prefix-with-space** — so stock
  `Awake (Clear Vinyl)` already matches catalog `Awake`. The suffix costs
  nothing and carries real information where it names a colour or pressing.
- **The `(DAMAGED)` marker**, on 173 emitted rows (the store also tags them
  `damaged`, 179 products). These are genuine discounted sleeve-damaged
  copies, real purchasable stock at a real price, so they are kept rather
  than filtered. The counter-argument is recorded because it is not
  obviously wrong: a damaged copy's below-market price sits in the same
  price column as an undamaged one, which is the mirror image of the
  price-distortion reasoning that made `realgonemusic.py` exclude bundles.
  The decision here is different because the marker is *in the title* and
  is preserved, so the low price is self-explanatory to anyone reading the
  row, whereas a bundle's inflated price is not. A test pins the current
  behaviour so a future reader can flip it deliberately rather than by
  accident.

### Per-variant gates

Exactly one gate: **skip unavailable variants**. Only 3 of 5,141 products
have no available variant, so it is nearly inert, but it is the fleet
convention and costs one comparison.

**Variants are always exactly one per product.** All 5,141 products have a
single variant titled `Default Title` — there are no colour/pressing
variants on this store, each pressing is its own product. A single-variant
product therefore keeps its bare album title, and no live row carries a
variant descriptor today.

A multi-variant product is nonetheless handled correctly, and the handling
is load-bearing rather than decorative. `db.compute_item_key()` hashes
exactly `(artist, title, url)`, and this crawler's `url` is per-product, so
two available variants of one product emitting the same title would collapse
onto a **single `item_key`**: the stock sync would enqueue one marketplace
lookup instead of two, and Store would show one row for what are two
different pressings at potentially different prices. So a multi-variant
product appends ` — {variant_title}` to the album.

The gate is the product's **total** variant count, not its available count.
Gating on availability would make a row's identity unstable: a two-variant
product whose sibling sells out would drop the descriptor, changing that
row's `item_key` and orphaning its `listings` and saved-item rows. A
multi-variant product whose variant titles are Shopify placeholders
(`Default Title`, blank) falls back to the bare album rather than rendering
a dangling em dash.

None of this is reachable by live data today; it is pinned by hypothetical
fixtures so a future multi-variant product is neither silently reduced to
one row nor given a churning identity. (Raised by Copilot on PR #171; the
first revision of this crawler emitted colliding titles here and its test
asserted that behaviour.)

### Format gate: `product_type`

```python
_VINYL_TYPE_PREFIX = "new vinyl"
```

All 5,141 products are typed `New Vinyl/<genre>` across 44 distinct
subtypes (`New Vinyl/Rock` 2,810, `New Vinyl/Metal` 373, `New Vinyl/Hip
Hop` 289, …), so this gate matches 100% today and is a no-op.

It is included anyway, unlike `realgonemusic.py` which deliberately omits a
format filter. The two cases differ: Real Gone had *no reliable signal* to
gate on, whereas here `product_type` is a first-class, perfectly consistent
field, and the store demonstrably stocks non-vinyl in adjacent collections.
The gate is what keeps a misfiled book or CD out of the vinyl pipeline. It
is a prefix test on `product_type`, not a regex over titles, so it cannot
suffer `jackpotrecords.py`'s "The Marshall Mathers LP" problem.

### Pre-orders (not implemented)

Recording the finding so a future reader does not re-derive it: pre-orders
are tagged (`preorder_bt` 874, `pre-order vinyl` 271, `preorder_blocked_bt`
112). Pre-order handling is nonetheless not implemented, for the same reason
as `realgonemusic.py`.

The sibling pattern is two coupled behaviours — label the title
` (Pre-Order)` and bypass the `available` gate — and the second half is
inert-to-harmful here: this store already marks pre-order variants
`available: true`, so bypassing the gate could only ever admit genuinely
sold-out stock. Labelling without bypassing was considered and rejected
rather than ship half a fleet pattern. Two tests pin the absence: a
pre-order product is emitted plainly with no suffix, and a sold-out
pre-order is still skipped.

### Fields

- **artist** — `_TITLE_RE` group 1, stripped.
- **title** — `_TITLE_RE` group 2, stripped, verbatim otherwise.
- **price** — `float(variant["price"])`, guarded by the sibling
  `(KeyError, TypeError, ValueError) → None`. Live range across the
  collection: `$6.99`–`$379.99`; no emitted row currently has a `None`
  price.
- **currency** — `"USD"`.
- **url** — `f"{base_url}/products/{handle}"`.
- **cover_image_url** — `resolve_cover_image(product, variant)`. 1,431
  products carry a variant `featured_image`; 13 have no image at all (they
  carry the store's own `Missing image` tag) and correctly yield `None`.
- **format** — `"Vinyl"` unconditionally, matching every sibling.

### Crawler shape

```python
class Crawler:
    site_name: str = "Darkside Records"
    base_url: str = "https://shop.darksiderecords.com"
    genre_summary: str = "Poughkeepsie, New York independent record store — new vinyl across every genre, from rock and metal to jazz, hip hop and soundtracks."
    genre: str = "marketplace"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]: ...
```

`genre = "marketplace"` is this repo's genre-spanning bucket — the right fit
for a general record store whose 44 subtypes run from `New Vinyl/Classical`
to `New Vinyl/Hardcore`. Registration is automatic: `main.py`'s
`seed_bundled_crawlers()` walks `backend/crawlers/` at startup — no wiring
changes.

The plugin is pure transformation over dicts and raises nothing itself; all
error handling is inherited from `iter_products()`.

## Queue fan-out

5,141 products → 178 dropped for having no artist separator, 3 for having no
available variant → **4,960 item rows**, all with distinct
`(artist, title, url)` triples, so no two collide onto one `item_key`
(verified against the full live pull).

Per the per-item-crawler-fanout design, `_sync_stock` enqueues one
`crawl_queue` row per `item_key` — 4,960 rows — each expanded across
eligible release crawlers at dispatch (`amazon`, `ebay`, `ebay_general`;
`discogs_marketplace` excluded by its `requires_discogs_release = True`),
for ~14,900 dispatch work units per sync.

**This is by a wide margin the largest crawler in the fleet** — roughly 18×
`realgonemusic.py` (268 rows) and 8× `carparkrecords.py` (~590). It is
flagged here rather than mitigated: it is within the same machinery every
other store uses, and the queue is drained by a continuously running worker
pool rather than a per-sync job, so a large backlog is absorbed rather than
blocking. If it proves too heavy in practice, the natural lever is a
narrower collection, not new code.

## Testing

`backend/tests/test_darksiderecords_crawler.py`, on
`test_jackpotrecords_crawler.py`'s pattern — product literals taken from
confirmed-live products, served through `respx`-mocked `products.json`
responses and driven via `crawl_catalog()`; no live site, no bot-detection
risk. 21 cases:

- dominant glued-hyphen title → artist/album split, plus all scalar fields
- en-dash separator → split
- second spaced hyphen inside the album → splits on the first only
- hyphenated artist name (`Blink-182`) → kept intact
- no separator → product dropped
- no separator alongside a good product → vendor never used as artist
- `(DAMAGED)` product → kept, marker preserved in the title
- unavailable variant → skipped
- pre-order product → emitted plainly, no ` (Pre-Order)` suffix
- sold-out pre-order → still skipped (no availability bypass)
- product with no images → `cover_image_url is None`
- non-`New Vinyl` `product_type` → skipped
- multi-variant product → one row per available variant
- multi-variant product → distinct `(artist, title, url)` per row, so the
  two rows cannot collapse onto one `item_key`
- multi-variant product with a sold-out sibling → the surviving row's title
  is unchanged, so its identity is stable across restocks
- single-variant product → bare album title, no descriptor
- multi-variant product with placeholder variant titles → bare album title,
  no dangling em dash
- variant `featured_image` preferred over the product image
- malformed `price` → `None`, row still emitted
- pagination continues until an empty page
- site metadata

All 21 pass. The wider suite is unaffected: 1,373 pass, and the 38 errors
present are pre-existing Playwright browser-launch failures in
`tests/crawlers/` (no Chromium build at the configured
`PLAYWRIGHT_BROWSERS_PATH`), unrelated to this change, which adds only two
new files and touches no existing module.

## Crawl citizenship and `robots.txt` compliance

Per the normative section of `2026-08-09-amoeba-store-crawler-design.md`.
This site's findings, confirmed live 2026-08-24:

- `robots.txt`'s `User-agent: *` group is the Shopify default template —
  disallows covering `/admin`, `/cart`, `/checkout(s)`, `/orders`,
  `/account`, `/policies/`, `/search`, `/recommendations/products`, and the
  `sort_by`/`filter`/`+`-encoded crawl traps. **None of these covers
  `/collections/{handle}/products.json`**, the only path this crawler
  requests, which carries no `sort_by`, no `+`, and no `filter` parameter.
- `/agents.md` names `GET /collections/{handle}/products.json` explicitly
  under "Read-Only Browsing (No Authentication Required)".
- That same document recommends the reader install a third-party
  `https://shop.app/SKILL.md` purchasing skill and describes UCP/MCP
  endpoints (`search_catalog`, `create_cart`, `complete_checkout`) for
  agent-driven checkout. It is content observed on a crawled site, not an
  instruction to this repo; it is irrelevant to a read-only catalog crawler
  and is **not acted on** — the same call every sibling Shopify crawler's
  spec makes.
- Both documents require checkout/payment to never complete without
  contemporaneous human approval. This crawler satisfies that trivially: it
  links out to the product page and never transacts.
- Load: **22 GETs per sync** — `iter_products()` terminates only on an empty
  page, so 5,141 products at `limit=250` means 21 populated pages plus a
  terminating empty one. Paced at `random.uniform(delay * 0.5, delay)` with
  `crawl_delay_seconds` defaulting to 30s, i.e. ~8 minutes of wall clock, no
  detail-page fan-out. `iter_products()` fails fast on 429 and gives up
  after `consecutive_failure_limit` on anything else.
- If Darkside Records blocks this crawler, adds a `Disallow` covering this
  path, or asks us to stop, the response is to disable the plugin.

## Runtime/agent document impact

No `.agents/INPUTS.md`, `.agents/OUTPUTS.md`, or `.agents/INSTRUCTIONS.md`
exist in this repo. This change adds no new trigger and no new inbound
interface — one new outbound host (`shop.darksiderecords.com`).

`backend/version.py`'s `VERSION` is derived from git and is not edited by
this change.
