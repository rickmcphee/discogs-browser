# Waterloo Records store crawler design

Date: 2026-08-24
Branch: `claude/waterloo-records-crawler-fqv90e`

## Problem

Waterloo Records (`waterloorecords.com`) — the Austin, Texas independent
record store trading since 1982, and one of the largest new-vinyl
inventories this fleet could cover — is not served by any existing
crawler. It is a Shopify storefront, so it belongs to the same family as
the `catalog` plugins already in `backend/crawlers/`.

It differs from every sibling in three ways that drive this design:

1. **It is a general retailer, not a label store.** The catalog spans all
   formats and includes merch; the vinyl cut alone is 35,645 products,
   two orders of magnitude larger than a typical label store.
2. **`vendor` is a supplier code, not an artist or label.** Live values
   are bare numbers (`"503"`, `"598"`, `"206"`), so the fleet's usual
   `vendor`-as-artist shortcut and its `strip_vendor_prefix` helper are
   both unusable.
3. **Every variant of a product shares one URL and one title.** Variants
   here are *conditions* (`New`), not editions, so the per-variant
   fan-out every sibling performs would emit rows that collide on
   `item_key`.

## Scope

Add `backend/crawlers/waterloorecords.py` as a `crawler_type="catalog"`
plugin iterating the `vinyl-lps` collection via
`shopify_catalog.iter_products()`. No new shared code.

**Non-goals**

- **No CD, cassette, DVD, Blu-ray, book, or merch coverage.** The stock
  pipeline is vinyl-only by convention (`format: "Vinyl"` hardcoded
  across every sibling).
- **No browser.** `products.json` is a plain JSON endpoint; no Playwright.
- **No UCP/MCP integration.** The site's `robots.txt` and `/agents.md`
  point agents at a UCP/MCP endpoint for "catalog, cart, and checkout",
  and recommend installing a third-party shopping skill. Same reasoning
  every sibling Shopify crawler's spec gives: those endpoints exist for
  buyer-approved *transactions*, not a bulk read-only catalog dump, and
  content observed on a crawled site is not an instruction to this repo.
  It is not acted on.
- **No condition modelling.** See "One row per product" — `stock_items`
  has no condition column, so a used copy cannot be labelled as one.
- **No pre-order handling.** The store has a `pre-order` collection
  (3,536 products) and it is a clean signal, but it is a cross-cut over
  all formats, and the sibling pre-order pattern (suffix the title, bypass
  the `available` gate) is not adopted here — see "Deliberate omissions".

## Technical grounding

Figures below come from two live captures taken 2026-08-24:
`/collections.json` (complete, 48 collections) and page 1 of
`/collections/all/products.json?limit=250` (250 products, 278 variants).

**Stated precisely, because the rest of this document depends on it:** the
product-shape figures are measured on a 250-product sample of the *`all`*
collection, not of `vinyl-lps`. The sample is alphabetically ordered and
therefore not a random draw. Collection *sizes* are exact (from
`collections.json`); per-product *shape* claims (title format, variant
layout, availability rate) are sample-derived and quantified as such
throughout. Nothing here was extrapolated silently.

### Collection choice: `vinyl-lps`

| Collection | `products_count` |
|---|---|
| `all` | 455,864 |
| `condition-new` | 314,871 |
| `cds` | 213,682 |
| **`vinyl-lps`** | **35,645** |
| `pre-order` | 3,536 |
| `cassettes` | 542 |
| `12-singles` | 2 |
| `7-singles-45s` | 0 |

`all` is unusable: 1,824 pages at `limit=250`, roughly 11 hours per sync
at the default pacing. `vinyl-lps` is the vinyl cut and the only viable
choice. The dedicated `12-singles` (2) and `7-singles-45s` (0)
collections are effectively empty and are not worth a second pass — the
format gate below admits those product types wherever they occur inside
`vinyl-lps`.

### Format gate: `product_type`, never the title

`product_type` is the store's own format field:

```python
_VINYL_PRODUCT_TYPES = frozenset({"vinyl", "7-in vinyl", "10-in vinyl", "12-in single"})
```

Compared lowercased because the store's casing is inconsistent (`Vinyl`
vs `7-IN VINYL`). In the sample: `Vinyl` 115, `CD` 112, `7-IN VINYL` 8,
`T-SHIRT` 6, `12-IN SINGLE` 5, `CASSETTE` 3, `10-IN VINYL` 1 — so 129 of
250 are vinyl.

**The title's trailing bracket is not a format signal and must not be
used as one.** Live bracket values include `[Import]` (11), `[Limited
Edition]`, `[Reissue]`, `[Deluxe]`, `[Roar Ver.]` and
`[Magenta/Black/White Haze/Splatter]`, none of which names a format,
while CDs carry `[Digipak]`, `[Compact Disc]` and `[Standard Edition CD
Single]`. Cross-tabulating the 250 sampled products against
`product_type` shows 84 of the 115 `Vinyl` products use `[LP]` and the
other 31 spread across 15 distinct non-format brackets.

**The merch trap this closes.** T-shirt titles are *artist-reversed* —
`"1970 Circle - Miles Davis - TS"`, `"1972 World Tour - David Bowie -
TS"`, `"#1 Record Logo T-Shirt - Big Star - TS"` — design first, artist
second. A title-driven gate would file a Miles Davis shirt under the
artist "1970 Circle". That specific product also carries five in-stock
size variants sharing one URL, so admitting it would produce five
`item_key` collisions on top of the wrong artist. Pinned by
`test_merch_never_leaks_a_reversed_artist`.

### Artist attribution: split the title on the first spaced hyphen

```python
_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s+-\s+(?P<album>.+)$')
```

All 250 sampled titles match `"Artist - Album [Format]"`. Three
properties of this regex are load-bearing:

- **Non-greedy, so it splits on the first hyphen.** Album halves
  legitimately contain further `" - "` runs: `"10CC - Deceptive Bends -
  180gm Vinyl [LP]"` is *Deceptive Bends - 180gm Vinyl* by *10CC*. 10 of
  250 sampled titles contain more than one `" - "`.
- **Whitespace required on both sides**, so a hyphenated artist or album
  is never split mid-word.
- **No `vendor` fallback.** `vendor` holds 41 distinct values in the
  sample, mostly bare supplier numbers (`"503"`, `"598"`, `"206"`) with a
  few distributor names (`"1 Umbrella / Empire"`, `"American Classics"`).
  It is never the artist.

`tags[0]` frequently holds an uppercased artist (49 of 60 sampled), but
it is not reliable — it is variously a distributor code (`"AEC"`,
`"WRPR"`), a normalized alias (`"N SYNC"` for `*NSYNC`), or absent
entirely — and it loses the artist's real casing. The title split is both
more accurate and better-cased.

### Title composition: keep the bracket

The album half is emitted verbatim, bracket included. Two reasons:

- **It is the only thing distinguishing two pressings of one album.**
  `"Let It Be Blue [LP]"` and `"Let It Be Blue [Indie Exclusive Limited
  Edition Blue LP]"` are distinct products at distinct prices; stripping
  the bracket would merge them into one title.
- **It does not break library matching.** `_library_match_fragment`
  (`backend/db.py`) matches a stock title exactly *or* as a catalog title
  followed by a space — `LOWER(s.title) LIKE LOWER(c.title) || ' %'` —
  precisely so stock listings may append edition qualifiers. So
  `"Kid A [LP]"` still matches catalog `"Kid A"`, and owned/wanted badges
  and the Recommended filter all work normally.

`format` is `"Vinyl"` unconditionally, as in every sibling. The specific
cut is already carried in the title's bracket (`[7-IN VINYL]`), so
nothing is lost.

### One row per product, priced at the cheapest in-stock variant

This is the central departure from the sibling crawlers, and it is forced
by `item_key`:

```python
item_key = compute_item_key(artist.title(), title, url)   # db.py
```

`item_key` hashes `(artist, title, url)`. Every variant of a Waterloo
product shares all three — the URL is handle-derived and the variant
descriptor is never appended to the title — so a per-variant fan-out
would emit rows colliding on `item_key`, which `replace_stock_items`
INSERTs with no `ON CONFLICT` guard.

Unlike the label stores, there is no descriptor worth appending to tell
variants apart. Product-level `options` are `Condition` (207 of 250),
sometimes with `Price`, `Version` or `Size`, and in inconsistent order
(`("Condition",)`, `("Condition", "Price")`, `("Price", "Condition")`,
`("Size", "Condition")`). Variant titles are correspondingly
`"New"` (207), `"New / Default"` (28), `"New / Alternate"` (11),
`"Default / New"` (8) — conditions and Shopify placeholders, not editions.

So: **one row per product**, taking the cheapest *in-stock* variant's
price. Cheapest because `stock_items` has no condition column — a used
copy cannot be labelled as one, so the row reports the least it costs to
get the record.

This is this store's own policy, not a fleet convention, and the
distinction is worth stating because it is easy to assume otherwise.
`amoeba.py` is the only other crawler that sees used stock, and it
behaves differently: `_extract_price` tries the new-price pattern and the
used "from" pattern *in that order* and returns on the first match, so it
prefers the new price and falls back to used only when no new price
parses. It never compares the two. Its "lowest" semantics are internal to
the used label ("from" meaning the cheapest of several used copies), not
a choice across conditions. Waterloo picks the cheapest in-stock variant
outright, whatever its condition.

Order matters: availability gates *before* the cheapest-price pick, so a
cheaper sold-out variant never sets the price. Live anchor: `070 Shake -
Petrichor [LP]` has an in-stock variant at `$29.99` beside a sold-out one
at `$24.99`; the row must read `$29.99`. Pinned by
`test_out_of_stock_variant_never_sets_the_price`.

No live sampled vinyl product currently has two *in-stock* variants
(availability is sparse), so the collision is structurally reachable
rather than currently firing. It is pinned with a synthetic fixture
rather than left to chance.

### Fields

- **artist** — the title's first `" - "`-delimited segment.
- **title** — the remainder, bracket intact.
- **format** — `"Vinyl"` unconditionally.
- **price** — `float(variant["price"])` of the cheapest in-stock variant,
  guarded by the sibling `(KeyError, TypeError, ValueError) → None`. A
  product whose only in-stock variant has an unparseable price still
  emits a row with `price: None`, rather than dropping in-stock vinyl
  over a bad field. Live sampled vinyl range: `$6.99`–`$45.99`.
- **currency** — `"USD"`.
- **url** — `f"{base_url}/products/{handle}"`.
- **cover_image_url** — `resolve_cover_image(product, variant)`. Note
  that `featured_image` is `null` on all 278 sampled variants, so this
  always falls back to the product image today; it is called anyway so
  the crawler stays correct if the store populates it. 185 of 250
  sampled products have at least one image, so a `None` cover is normal
  and expected, not a parse failure.

`compare_at_price` is deliberately ignored: it is unreliable here (one
sampled variant lists `compare_at_price` `6.98` *below* its `6.99`
price), and no sibling uses it.

### Crawler shape

```python
class Crawler:
    site_name: str = "Waterloo Records"
    base_url: str = "https://waterloorecords.com"
    genre_summary: str = "Austin, Texas independent record store since 1982, ..."
    genre: str = "marketplace"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]: ...
```

`genre = "marketplace"` is this repo's genre-spanning bucket. Its other
members are Amoeba, Newbury Comics, Turntable Lab, The Sound Garden,
Jackpot Records, Real Gone Music, Darkside Records, and the reissue
labels Craft Recordings, Cleopatra Records and Numero Group — the right
fit for an all-genres general retailer. Registration
is automatic: `main.py`'s `seed_bundled_crawlers()` walks
`backend/crawlers/` at startup, so there are no wiring changes.

The plugin is pure transformation over dicts and raises nothing itself;
all error handling is inherited from `iter_products()`: a non-429 failure
is retried within the `consecutive_failure_limit` budget, and a 429 raises
immediately. Note the retry is *not* backed off — each attempt waits the
same `random.uniform(delay * 0.5, delay)` pacing the loop applies to every
request, with no increasing interval between successive failures.

## Scale, and why it is accepted

This is by a wide margin the largest catalog in the fleet, and that is a
real operational cost, recorded here rather than discovered later:

- **144 GETs per sync.** 35,645 products at `limit=250` is 143 non-empty
  pages — 142 full ones plus a 145-item remainder — and then the empty
  page `iter_products()` needs to terminate, which it only does on an
  empty page, never a short one.
- **Roughly 54 minutes of wall-clock per sync**, at the
  `random.uniform(delay * 0.5, delay)` pacing with `crawl_delay_seconds`
  defaulting to 30s (~22.5s mean).
- **Roughly 10,800 stock rows.** 30.2% of sampled vinyl products (39 of
  129) have at least one in-stock variant; applied to 35,645 that is
  ~10,800 rows — extrapolated from the sample, so treat it as an order of
  magnitude, not a count.
- **Roughly 32,000 dispatch work units** per sync, at one `crawl_queue`
  row per `item_key` expanded across the eligible release crawlers
  (`amazon`, `ebay`, `ebay_general`; `discogs_marketplace` is excluded by
  its `requires_discogs_release = True`).

That is ~55× `carparkrecords.py` (~590 units). It is accepted rather than
mitigated because the queue is a shared, rate-limited, always-draining
worker pool: a large enqueue makes the queue take longer to drain, it
does not make it fail, and the per-item crawler fan-out design already
handles deferral and circuit-breaker cooldowns. The mitigation lever, if
one is ever needed, is disabling the crawler or narrowing the collection
— both operational, neither structural.

### The lock-held serial cost, which queue drainage does *not* cover

The paragraph above is only about *downstream* price-dispatch work. The
~54 minutes has a second, separate cost on the *upstream* side, and it is
the more consequential of the two:

- `start_stock_sync` (`crawl_manager.py`) takes
  `pg_try_advisory_lock(STOCK_SYNC_LOCK_KEY)` on a dedicated
  non-pooled connection and holds it for the whole of `_sync_stock`,
  releasing it only in that function's `finally`.
- `_sync_stock` walks the catalog crawlers **sequentially** — one
  `await self._run_catalog_crawler(crawler)` per source, in a plain
  `for` loop — so each crawler's full duration is serial time added to
  the run, not overlapped with any other.
- A sync that starts while the lock is held is **skipped outright, not
  queued**. Both guards return `False` and log: the in-process
  `stock_sync_running` check ("Stock sync already running, ignoring start
  request") and the cross-instance lock ("Stock sync already running on
  another instance, ignoring start request").

So enabling this crawler has three consequences beyond queue depth:

1. Every full stock sync grows by ~54 minutes of wall clock.
2. Every store *after* Waterloo Records in the loop has its refresh
   delayed by that much.
3. Any scheduled or manual stock sync that fires inside the window is
   dropped for that cycle. It does not run late — it does not run.

**How bad this is depends entirely on the configured cadence**, which is
an admin setting with no default: `stock_schedule` is read as
`cfg.get("stock_schedule", "")` in `main.py`, and an empty value means no
scheduled sync at all. If the cadence is shorter than the full-run
duration, runs are skipped every cycle; if it is comfortably longer,
nothing is lost. That is a deployment decision, not a property of this
crawler, so this document records the cost rather than picking a number.

**No mitigation is implemented here, deliberately.** The obvious ones —
running catalog crawlers concurrently, or giving long crawlers their own
lock and cadence — change shared sync machinery that every store depends
on, which is well outside a single store crawler's blast radius and
wants its own design. The levers available today are all operational:
set `stock_schedule` longer than a full run, sync this store on its own
via `start_stock_sync(crawler_id=...)` (note the lock is still global, so
this shortens the run, it does not make it concurrent), or disable the
crawler.

This is the one part of the design a reviewer should push back on if the
deployment's sync cadence is tight.

## Deliberate omissions

Recorded so a future reader doesn't re-derive them:

- **Pre-orders.** A `pre-order` collection exists (3,536 products across
  all formats). The sibling pattern is two coupled behaviours — suffix
  the title with ` (Pre-Order)` and bypass the `available` gate — and
  neither half can be justified from the captured data: the collection is
  not vinyl-scoped, and no per-product pre-order tag was observed in the
  sample. Skipped entirely rather than shipping half a fleet pattern.
- **Used-copy labelling.** See "One row per product". The `Condition`
  option exists and `condition-new` (314,871) is a real collection, so
  used stock is genuinely present in the catalog — but `stock_items` has
  no column to carry the distinction, and no `Used` variant appeared in
  the sample to model against.

## Testing

`backend/tests/test_waterloorecords_crawler.py`, on the sibling
`respx`-mocked `products.json` pattern — hand-written product literals
copied verbatim from confirmed-live products, driven through
`crawl_catalog()`. No live site, no bot-detection risk. The cases:

**(2026-08-28: the conversion below rebuilt this file around `search()` and
`/search/suggest.json`, so the list that follows is the catalog-era one and is
kept as the record of what that crawler pinned. Each rule the conversion
introduced carries its own cases in the same file — read the amendment for
the rules, and the file itself for the current set.)**

- artist/album split with the format bracket preserved
- first-hyphen split, not the last (the 10CC case)
- `vendor` never used as the artist
- CDs and merch dropped by `product_type`
- merch never leaks a reversed artist (the Miles Davis t-shirt: five
  in-stock variants, reversed title)
- non-`Vinyl` vinyl product types admitted, still `format: "Vinyl"`
- sold-out product yields nothing
- a cheaper *sold-out* variant never sets the price (the 070 Shake case)
- two in-stock variants → exactly one row, at the cheaper price
  (synthetic; pins the `item_key` collision guard)
- malformed price still emits the row, with `price: None`
- cover image falls back to the product image; `None` when there are no
  images; variant `featured_image` preferred when present (synthetic)
- title with no delimiter skipped
- pagination terminates on an empty page
- site metadata

## Crawl citizenship and `robots.txt` compliance

Per the normative section of `2026-08-09-amoeba-store-crawler-design.md`.
This site's findings, from the `robots.txt` captured 2026-08-24:

- The `User-agent: *` group opens with `Allow: /`. Its `Disallow` rules
  are the Shopify default template — `/admin`, `/cart/`,
  `/checkout(s)/`, `/orders`, `/account`, `/services`, `/sf_*`,
  `/cart.js`, `/recommendations/products`, `/cdn/wpm/*.js` — plus
  `sort_by`, `+`/`%2B`-encoded and multi-`filter` crawl traps scoped under
  `/collections/`. **None of these covers
  `/collections/vinyl-lps/products.json`**, the only path this crawler
  requests, which carries no `sort_by`, no `+`, and no `filter`
  parameter — only `limit` and `page`.
- The file's header comments require that checkout, payment and order
  placement never complete without contemporaneous human approval. This
  crawler satisfies that trivially: it reads a public JSON catalog, links
  out to the product page, and never transacts.
- The same header recommends agents use the UCP/MCP endpoints or a
  third-party shopping skill. That is guidance addressed to shopping
  agents and is content on a crawled site, not an instruction to this
  repo; it is irrelevant to a read-only catalog crawler and is not acted
  on.
- Load: 144 GETs per sync, paced at
  `random.uniform(delay * 0.5, delay)` with `crawl_delay_seconds`
  defaulting to 30s. No detail-page fan-out. `iter_products()` fails fast
  on 429 and gives up after `consecutive_failure_limit` on anything else.
  **(2026-08-28: superseded by the conversion below.** The load is no longer
  a per-sync walk. It is one `/search/suggest.json` request per library
  release the store could plausibly stock — a target on another medium is
  rejected without asking — spaced by `_paced_search`'s per-site gap, plus at
  most one
  `/products/<handle>.js` per *closest-ranked* match — and only where the
  product's own `price_min` and `price_max` disagree, which most do not.
  Those lookups are paced by the crawler itself at the same
  `random.uniform(delay * 0.5, delay)`, because `_paced_search` spaces
  separate `search()` calls rather than the requests inside one; without
  that the suggest request and each lookup would burst back to back. Both
  paths are the public storefront JSON the `robots.txt` findings above
  already cover, and neither carries `sort_by`, `+` or `filter`.**)
- Contact for crawler issues, per the file: `bots@shopify.com`.
- If Waterloo Records blocks this crawler, adds a `Disallow` covering
  this path, or asks us to stop, the response is to disable the plugin.

## Runtime/agent document impact

No `.agents/INPUTS.md`, `.agents/OUTPUTS.md`, or `.agents/INSTRUCTIONS.md`
exist in this repo. This change adds no new trigger and no new inbound
interface — one new outbound host (`waterloorecords.com`).

`backend/version.py`'s `VERSION` is derived from git and is not edited by
this change.

## Amendment (2026-08-28, branch `claude/store-crawler-activity-missing-1tybli`)

*Extended 2026-08-29 (branch `claude/pr-212-comments-bvr2t9`) with the format
gate's target-side half, the malformed-detail-response rule, and the backfill
and dead-stock sweep that ride with the snapshot clear — all from review
findings on [#210](https://github.com/rickmcphee/discogs-browser/pull/210) that
landed after it merged. Kept in this amendment rather than split into a second
one: each belongs to a conversion rule stated here, and a reader implementing
from a half of the list would get it wrong.*

**The "Scale, and why it is accepted" section above is wrong in its central
assumption, and this crawler has never written a single row.**

That section plans a full walk — "144 GETs per sync", "143 non-empty pages",
"and then the empty page `iter_products()` needs to terminate" — and the
verification list restates it as "pagination terminates on an empty page".
Waterloo's `vinyl-lps` never reaches an empty page. Shopify's storefront
`products.json` refuses `page` past 100 with an HTTP 400 whatever `limit`
says, so the endpoint tops out at 25,000 products. Confirmed live on
2026-08-28: pages 1-100 each return a full 250 products, page 101 returns
400, and so does every page above it. The collection has since grown to
36,138 products, so a complete walk would need 145 pages — 45 of them past
the ceiling.

The consequence was total, not partial. `iter_products()` classified the 400
as a transient fault, retried page 101 for the whole `consecutive_failure_limit`
budget, and then raised; `_sync_stock` skips `replace_stock_items()` entirely
when a catalog crawl raises, so roughly 40 minutes of successfully fetched
pages were discarded on every run. The store showed continuous healthy
page-fetch activity at INFO and zero rows in the Store tab, with the
explanatory line at ERROR, which `routers/logs.py`'s exact-level filter keeps
out of an INFO view.

`iter_products()` now stops cleanly at the ceiling and keeps what it fetched
(see the 2026-08-28 amendment to
[`2026-08-02-stock-sync-429-backoff-design.md`](../../superpowers/specs/2026-08-02-stock-sync-429-backoff-design.md)),
which turns this crawler from zero rows into the first 25,000 products of the
collection. That is a floor, not a resolution: roughly 11,000 products remain
unreachable through this endpoint, and which ones fall outside the ceiling is
decided by the collection's own ordering rather than by anything meaningful.

**Decided: this store is now a `release` crawler, not a `catalog` one.**

The Problem section above already records that Waterloo "is a general
retailer, not a label store" — the trait that separates it from every sibling
— and that is what settles it. A catalog crawler answers "what is on the
shelf", which only works if the shelf can be enumerated; this one cannot be.
A release crawler answers "can I buy *this* record here, and for how much",
which the endpoint below can answer completely.

`crawl_catalog()` is therefore replaced by `search()` over
`/search/suggest.json`, which has no page ceiling and reaches the whole
catalog. It returns `available`, `price`, `url` and the same `type` field this
crawler's vinyl gate already read, so the availability rule and the store side
of the format gate carry over unchanged — though the format gate acquires a
second half it never needed as a catalog crawler, below;
`crawlers/ebay.py` is the precedent for an API-backed release crawler that
ignores the Playwright page it is handed.

What the conversion had to get right:

- **The url carries the search that produced it.** suggest.json returns
  `/products/<handle>?_pos=…&_psq=…&_psid=…`, and every one of those varies per
  search. `db.compute_item_key()` hashes the url, so they are stripped — left
  on, one product would take a fresh `item_key` on every crawl and orphan the
  saves and judgments hanging off the old one.
- **The endpoint is a search box, not a lookup.** "geese getting killed"
  returns 3D Country and a Third Man live record alongside the album asked
  for, and the fleet reads `matches[0]`. Both halves of the store's
  `Artist - Album` convention are checked, and matches are then *ranked*
  rather than filtered further: rank 0 is an exact title once the bracketed
  qualifiers come off, which only a base pressing achieves, and rank 1 is an
  exact match against the text before *any* qualifier delimiter (`:`, `[`,
  `(`, a spaced dash). Every boundary is tried rather than only the first,
  because an album whose own title contains a delimiter would otherwise be
  truncated to its opening fragment — "Live: In Concert" would become "Live"
  and never match this store's "Live: In Concert: Anniversary Edition [LP]".
  Trying each still rejects "Kid A Mnesia", since no boundary of it yields
  "Kid A". Rank 1 is deliberately *not*
  `db._library_match_fragment`'s exact-or-prefix-with-space rule, though an
  earlier draft used it. That rule answers "does this stock row correspond to
  a release the user owns", where a wrong answer mislabels ownership; this one
  decides whose price gets published. Under the prefix rule, a "Kid A" search
  returning only "Kid A Mnesia [3LP]" would publish the Mnesia box set's price
  as Kid A's — ranking only prevented that while a genuine "Kid A" was also in
  the results. Cutting at the delimiter instead rejects a title that merely
  continues in plain words ("Kid A Mnesia", "OK Computer Oknotok 1997 2017")
  while still matching a qualified edition ("Abbey Road: Anniversary Edition
  [LP]", the only Abbey Road this store stocks). A missing price beats a wrong
  one, because the fleet reads `matches[0]` and shows it as this record's price
  at this store.
- **The format gate needs a second half.** `_VINYL_PRODUCT_TYPES` above asks
  what the *store* is selling, and as a catalog crawler that was the whole
  question — nothing else chose which records it was asked about. A release
  crawler is run for every release in every library, so the *target's* format
  becomes a second question, and one this gate does not answer: a CD or
  cassette release would otherwise take the vinyl pressing of the same artist
  and album, and `db.upsert_stock_item_from_release()` writes the release's own
  format onto the row — so the Store tab would show a "CD" carrying an LP's url
  and price. `search()` therefore rejects a target on another medium before
  querying, matching `release["format"]` (Discogs' `formats[0].name`, which
  reaches `search()` on the catalog row) against `_OTHER_MEDIUM_FORMATS`.
  Rejecting before the request rather than filtering after it also spends no
  request on the store for a question with no right answer. `crawlers/amazon.py`
  is the fleet precedent for a release crawler gating on the target's format.

  The list holds every medium Discogs named definitely that a Waterloo vinyl
  product cannot be. **"A record you put a needle on" is not the test**, and
  reading it that way is what first let `Shellac` and `Acetate` through: this
  store sells *new vinyl*, so a pre-war 78 and a one-off lacquer are as wrong
  a match for it as a CD is. `Flexi-disc` and `Lathe Cut` are worse than
  either — both are usually alternate pressings of an album that also exists
  as a standard LP, which is exactly the case where the title match succeeds
  and the wrong price gets published.

  What stays *out* of the list is only what Discogs left genuinely open: a
  container format (`Box Set`, `All Media`) can perfectly well hold vinyl, and
  an absent or unrecognised value is not an answer at all. Both still search
  and lean on the product-type gate — the failure that guards against is
  rejecting the one format the crawler exists to serve. Rejecting costs no
  site-health signal either way, per `empty_result_is_expected` below.

  Matched as substrings for two reasons: Discogs qualifies these names (`CDr`,
  `DVD-Video`, `8-Track Cartridge`), and `search()` also receives **stock-item
  targets** — `get_eligible_crawlers()` admits a release crawler with
  `requires_discogs_release = False` for those too — whose format is whatever
  a sibling store crawler wrote (`LP`, `2xLP`, `7"`) rather than Discogs'
  controlled vocabulary. No vinyl format written by either side contains any
  listed term, which is what makes the substring test safe across both.

- **A malformed detail response is a failure, not an answer.** The
  `/products/<handle>.js` lookup is the only thing that decides *both*
  availability and price, so a 200 whose `variants` is absent, null or empty
  cannot be read as "a product nobody can buy" — a real Shopify product always
  carries at least one variant. Treating it as an answer would publish an
  unpriced row on the strength of a payload nothing actually read, and record
  the site as healthy while doing it. It raises instead, per CLAUDE.md's
  crawler contract. Distinct from the two states that *are* answers, and that
  the same lookup still reports: a product whose variants are all sold out
  (dropped), and a buyable variant whose price will not parse (kept, unpriced).

- **The old snapshot had to be cleared.** `db.replace_stock_items()` runs only
  for the catalog kinds, so rows written while this was a catalog crawler
  would never be refreshed and never deleted. `db.register_crawler()` now
  clears them when a crawler changes kind to `release`, scoped to
  `release_id IS NULL` — exactly the catalog-written set, since the release
  path writes through `upsert_stock_item_from_release()`, which always carries
  a release_id.

  Two further writes ride in that same transaction, both load-bearing rather
  than incidental, and an implementation from this section without them is
  incomplete. **The queue is backfilled**, via
  `backfill_crawl_queue_for_crawler()`, for the same reason enabling a release
  crawler backfills: eligibility resolves at dispatch, so every still-pending
  target picks the converted crawler up for free, but targets already marked
  `done` would not see it until the next sync or scheduled sweep. It runs after
  the upsert, since the backfill's own guard reads `crawler_type = 'release'`
  off the row, and is **gated on the crawler's retained enabled state** —
  the upsert deliberately leaves `enabled` out of its `DO UPDATE` list so an
  administrator's decision survives a redeploy, and `get_eligible_crawlers()`
  filters on `enabled`, so backfilling a disabled crawler would re-walk the
  whole queue to produce no work at all. **Dead stock rows are then swept**,
  via `delete_dead_stock_crawl_queue_rows()`, exactly as `routers/settings.py`'s
  enable path does. There are two sources of them, not one: the `DELETE` above
  orphans any `crawl_queue` row whose `item_key` came from this crawler's
  catalog-era snapshot, and the backfill's first `UPDATE` carries no
  stock-source predicate, so it can revive rows whose store is disabled or
  whose item has left stock. Either way they would otherwise sit pending and
  unclaimable in the claim index until some later stock sync happened to catch
  them.

- **A miss here is not a fault.** The release path counts an empty result
  against the per-site consecutive-failure breaker, on the reasoning that a
  real Discogs release absent from a near-universal marketplace means
  something is wrong. That does not hold for one shop: Waterloo stocks a
  fraction of any given library, so a run of releases it does not carry is
  ordinary. It therefore declares `empty_result_is_expected = True`, and an
  empty result records no site-health signal at all — bot detection and real
  matches still do. Without it, `consecutive_failure_limit` unstocked releases
  in a row would cool off a site that answered every request correctly.

**Consequences.** Waterloo keeps a Store tab presence, but a different one: a
release crawler still writes `stock_items` through
`upsert_stock_item_from_release()`, so what appears is driven by libraries
rather than by the shelf. Note that `stock_items` carries no `user_id` and is
not user-scoped -- a row written because *some* tenant's queue asked about a
release is visible to everyone, so what surfaces is the union of what Waterloo
stocks across all users' libraries, not one user's own. What cannot appear is a
release absent from every library, since nothing would ever enqueue it. Browsing
Waterloo for records you do not already have is gone, and that is the price of
completeness on a catalog this size. Waterloo also joins the eligible release
crawlers expanded per `crawl_queue` row at dispatch, alongside `amazon`,
`ebay` and `ebay_general`; the per-source dispatch estimates in the other
crawler design docs were computed against the set registered at the time and
were not re-derived for this change.
