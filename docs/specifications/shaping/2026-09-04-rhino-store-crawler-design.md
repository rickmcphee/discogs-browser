# Rhino store crawler design

**Status:** implemented
**Date:** 2026-09-04
**Store:** https://store.rhino.com

## Problem

Rhino is Warner Music's catalog reissue label — the one that puts classic rock,
soul, pop and jazz back on vinyl, heavy on deluxe editions, audiophile pressings
(Rhino High Fidelity, Rhino Reserve) and box sets. Its official store is not
covered by any bundled crawler, so none of that stock reaches the Store tab and
none of it is priced against a user's library on the Track tab.

The store runs Shopify (`powered-by: Shopify`, `us-rhino.myshopify.com`), so
`shopify_catalog.iter_products()` already implements the transport. What needed
deciding was which collection to walk, how to scope it to records, and where
this store's payload departs from the sibling Shopify stores.

## Scope

**In:** a `catalog`-type plugin, `backend/crawlers/rhino.py`, walking the
store's published catalog over the public `products.json` endpoint and yielding
in-stock vinyl as stock items.

**Out:**

- Mixed-media box sets (`Boxset - Mixed`) and bundles — see *Format gate*.
- Products the store leaves untyped, which include a real vinyl box set
  (`A Decade Of Dio: 1983-1993 (6LP, Splatter Vinyl)`) alongside stickers,
  keyrings and CDs. Accepted scope loss: the type field is the gate, and there
  is nothing else in the payload to separate them.
- Any release-type (per-library-item) crawling of this store. This is a catalog
  source; the Store tab's own crawlers price its items.

## Technical grounding

Everything below was gathered live on 2026-09-04 by fully paginating the
store's collections and caching the payloads.

### Collection choice: `all`, not the store's own `vinyl`

The store publishes a `Vinyl` collection, and taking it would have matched the
sibling stores. It is the wrong source here, in both directions at once:

| Collection | Products returned | `Vinyl - LP` in it |
| --- | --- | --- |
| `vinyl` | 755 | 579 |
| `music` | 1286 | 620 |
| `all-products` | 913 | — |
| `all` (Shopify built-in) | 1522 | 679 |

`vinyl` is **incomplete** — it holds 579 of the store's 679 `Vinyl - LP`
products — and simultaneously **contaminated**, carrying CDs, CD-only box sets
and bundles. It is not a superset of any other collection and not a clean
subset either: 38 of its products are absent from `music`, and 569 of `music`'s
are absent from it. The hand-maintained `all-products` ("Products - All") is
not all products.

`all` is Shopify's built-in all-products collection. It is not listed in
`collections.json` — it exists on every Shopify storefront regardless — and
bundled crawlers already walking it include
`killrockstars.py`, `onetwothreefourgo.py`, `piratespressrecords.py`,
`riserecords.py`, `saddlecreek.py` and `triplebrecords.py`. It contains every product held by
`vinyl`, `music` and `all-products` with none missing, and its size agrees with
the product sitemap. Walking it moves the format scoping onto the store's own
structured format field, which does the job properly.

### Format gate: the `Vinyl` type family, plus `Boxset - Vinyl Only`

The store types products as `{family} - {variant}`:

- Vinyl: `Vinyl - LP` (679), `Vinyl - 2LP` (69), `Vinyl - Single` (6),
  `Boxset - Vinyl Only` (86) — 840 products.
- Not vinyl: `CD - Album`, `CD - Single`, `CD - 2CD Album`, `CD + DVD/BluRay`,
  `Boxset - Mixed`, `Boxset - CD Only`, `Blu-Ray`, `DVD`, `Cassette`,
  `reel to reel`, `Bundle`, and the apparel and accessory types (`T-Shirt`,
  `Hoodie`, `Slipmat`, `Poster/Print`, `Patch`, `Koozie`, …).

`_VINYL_TYPE_RE` matches the `Vinyl` **family prefix** with any non-empty
variant after it, rather than enumerating today's three variants. The store
already types loosely within the family — 2LP titles are filed under
`Vinyl - LP`, and a `1LP & 7"` product under `Vinyl - 2LP` — so a new variant
(`Vinyl - 3LP`, `Vinyl - EP`) is plausible, and enumerating would silently drop
a shelf's worth of records the day one appeared. Reading the prefix that widely
is safe because accessories carry their own top-level types rather than a
`Vinyl - ` one: sweeping every admitted title for accessory words returned only
album titles ("Picture Book", "Pin Ups", "Hatful Of Hollow", "Purple Vinyl"),
no actual accessory. A bare `Vinyl` with no variant is rejected — the gate
requires something after the separator, so a product typed with the family name
alone is drift, not a record.

`Boxset - Vinyl Only` is named in full rather than caught by a `Boxset` prefix,
because its siblings must not come with it:

- `Boxset - Mixed` (58) spans sets that are entirely CD/DVD
  (`A (The 40th Anniversary Edition) 3CD/3DVD`) and sets that do hold a record
  (`5150 (Expanded Edition) (3CD/1LP/1BD)`), with nothing in the payload
  separating them. Admitting the type would file CD box sets as vinyl.
- `Boxset - CD Only` (57) is self-evidently out.

**Known false positives, quantified and accepted.** The store mistypes five
products of the 840 (0.6%), so its own format field is right on 99.4% of what
the gate admits. Four are typed `Vinyl - LP` — `1984 CD`,
`These Are The Good Old Days: … (CD)`, `Inflammable Material (4CD/1DVD)`, and
`The Doors - Immersed (1967-1971) (6BR)`, which is six Blu-rays — and one,
`The Studio Albums: 1992-2016 (14CD)`, is typed `Boxset - Vinyl Only`.

A title-based veto of the kind `byrdlandrecords.py` uses is deliberately **not**
added. That store fuses format into the title because it has no format field;
here `product_type` is the store's own structured field, and a veto reading
titles instead would reject genuine hybrid records. Six admitted products name
both vinyl and another medium in their titles — `Movement (1LP/2CD/1DVD)`,
`Heartbeat City (Deluxe Edition) (4CD/1LP)`,
`Everything Is Now … [6CD+BR+2LP Box]`, `Seal:Deluxe Edition (4CD/2LP)`,
`Sorry, Ma Forgot To Take Out The Trash (Deluxe Edition) (4CD/1LP)` and
`The Breathtaking Blue 1LP/DVD` — and every one of them is a real record. So the
trade is not merely unfavourable in kind, it is unfavourable in count: a veto
would have to distinguish those six from the five mistypes on title text alone,
and the naive version of it loses more good rows than it removes bad ones.

The counts above were re-derived after an initial draft of this section
reported four mistypes and then named a fifth: the sweep's regex used a `\b`
boundary before the format token, which never matches a digit-glued one like
`6BR`. The corrected sweep allows a leading digit run, and is what both figures
here come from.

### Artist: `vendor`, always

Every one of the 840 vinyl products carries a non-blank `vendor`, and it is the
artist (263 distinct values: David Bowie, Fleetwood Mac, Black Sabbath, Joni
Mitchell, …). Two departures the payload cannot fix: compilations are credited
to `Various Artists` / `Various Artist (Rhino)` (28 products), and exactly one
product has the label in the field rather than the act (`18 (LP)`, vendor
`Rhino`, which is Moby's album). Both are the store's own data; a
blank-vendor product is skipped rather than guessed at.

### Title: kept as-is, with the shared exact-case `" - "` strip

The store keeps the artist out of the title on all but a handful of products.
`strip_vendor_prefix` is therefore a **live transformation** here rather than
the pure drift guard it is on the sibling stores — five titles carry a
`{vendor} - ` prefix and are corrected:

```
Eagles - Live 2LP                                    -> Live 2LP
Joni Mitchell - Love Has Many Faces: A Quartet, …    -> Love Has Many Faces: A Quartet, …
The Doors - Immersed (1967-1971) (6BR)               -> Immersed (1967-1971) (6BR)
ZZ Top - From The Top: 1971-1976 (Rhino High Fidelity) (5LP Boxed Set)
                                                     -> From The Top: 1971-1976 …
ZZ Top - From The Top: 1979-1990 (Rhino High Fidelity) (5LP Boxed Set)
                                                     -> From The Top: 1979-1990 …
```

The helper's narrowness is load-bearing and is the reason it is used unchanged
rather than widened. Ten further titles open with the vendor's name followed by
a **colon**, and there the vendor's name is part of the album title, not a
prefix in front of it:

```
Talking Heads: 77 [2LP]
Nuggets: Original Artyfacts From the First Psychedelic Era (1965-1968) …
Fleetwood Mac: 1973-1974 4LP + 7” (Colored Vinyl)
Rod Stewart: 1975-1978 (5LP)
John Coltrane: 1960-1964 Mono (Rhino High Fidelity) (6LP Boxed Set)
```

Widening the strip to a colon would reduce those to `77 [2LP]`,
`Original Artyfacts …` and `1973-1974`. One colon case would benefit
(`Thomas Bangalter: Circonvolutions`); nine would be damaged. The exact-case
`" - "` match also correctly leaves `Duran Duran (1993) - 2LP` alone, where the
separator exists but not directly after the vendor's name.

The trailing pressing descriptor (`(2LP)`, `(Green Vinyl)`,
`(Rhino High Fidelity)`) stays: it separates two pressings of one album, and
`_library_match_fragment`'s exact-or-prefix-with-space match still finds the
bare album title in front of it.

### Availability: the `available` flag, not the store's tags

Three tags look relevant and none of them is authoritative:

- **`out_of_stock`** (35 vinyl products) **disagrees with Shopify's own
  `available` flag on 13 of them** — tagged out of stock while the variant is
  flagged available. It is hand-curated and stale; the flag is live inventory
  state, so the flag decides.
- **`exclude`** (15 products) does not hide anything. All 15 resolve HTTP 200,
  are published, and are purchasable — it is a merchandising-feed flag, not a
  storefront one. Ignored.
- **`sfccPreOrderProduct`** (8 products) is the pre-order marker and is used,
  for the ` (Pre-Order)` title suffix only.

### Pre-orders: tag suffix, no availability bypass

Every live pre-order-tagged vinyl product reports `available=True`, so an
unavailable product is gone allocation whether or not it is tagged. Same call
as `udiscovermusic.py`, `hammerheart.py` and `darksiderecords.py`, and
deliberately not `napalmrecords.py`/`centurymedia.py`'s bypass.

### Variants: single today, disambiguated if that changes

All 840 vinyl products are single-variant with a `Default Title` variant, and
no variant carries a `featured_image` (so `resolve_cover_image` always falls
through to the product image, which every vinyl product has). The multi-variant
descriptor is therefore unreachable on today's catalog and exists because
`item_key` is `sha256(artist|title|url)`: two variants of one product share all
three, so without a descriptor they would collapse onto one identity —
the identity `stock_item_identities`, `crawl_queue` targets, `listings`,
judgments and saves all key on. Variant id is the fallback when the variant
title is missing or `Default Title`, following `udiscovermusic.py` and
`hammerheart.py`: immutable, unique, identity over cosmetics.

### Price and currency

`meta.json` reports `"currency":"USD"` and the store is US-domiciled, so `USD`
is hardcoded as on every sibling. Live prices run $10.18–$849.99 across the
gate's products, and every yielded row carries one.

`_price` rejects booleans before `float()`, and rejects non-finite and
non-positive results after it — the guard shape `roughtrade.py`,
`discogs_marketplace.py`, `sideonedummyrecords.py` and `byrdlandrecords.py`
converged on. `bool` is an `int` subclass, so `True` would price a record at 1;
`nan` is truthy, so a falsiness check would let it reach the stock row and
break JSON serialisation downstream.

### Drift guards

`db.replace_stock_items()` DELETEs this crawler's previous snapshot before
inserting, and `_sync_stock` only skips that call when the crawl **raised** — a
completed-but-empty walk is destructive where a raise is inert. Three guards
each name a distinct way the payload can stop carrying what this crawler reads:

| Guard | Fires when | Drift it names |
| --- | --- | --- |
| `products_seen == 0` | the collection returns nothing | `all` renamed or removed, or the endpoint changed shape |
| `vinyl_seen == 0` | no product carries a vinyl `product_type` | the format taxonomy was renamed wholesale |
| `vendor_ok == 0` | no *vinyl* product carries a `vendor` | the artist source moved out of `vendor` |
| `stock_flag_ok == 0` | no *vinyl* product carries a variant with a boolean `available` | the availability field vanished, was renamed, or changed type |

Every tally is taken **before** the availability filter, so a shelf that has
simply sold out completes empty rather than raising — the one case where an
empty result is the truth.

The last guard covers the field the availability filter itself reads, which the
other three are blind to by construction. If `variants` disappeared, or
`available` disappeared from every variant, each product would yield nothing
while `products_seen`, `vinyl_seen` and `vendor_ok` all stayed non-zero — so the
walk would complete "successfully" with no rows and delete the snapshot. That is
the same destructive shape the other guards exist to prevent, reached through
the one field none of them touches.

It tests the value's **type**, not merely the key's presence, and the difference
is not pedantry: `available` arriving as the string `"false"` is truthy, so a
filter reading truthiness would not simply fail to read it — it would *invert*
it and offer a sold-out record for sale. An int `1`/`0` is refused on the same
terms: over-strictness costs a raise, which leaves the previous snapshot intact,
while under-strictness costs a corrupted one.

**The guard alone is not sufficient, and the per-variant filter carries the
other half.** The guard establishes only that the field is readable
*somewhere* in the catalog, so on a mixed payload one healthy product satisfies
it while a sibling's `"false"` still reaches the filter and is published as in
stock. The filter therefore admits a variant only on the literal `True` —
`False`, `"false"`, `"true"`, `1`, `0`, `None`, an absent key and a variant that
is not a mapping are all skipped. Losing a row is the safe direction here;
offering a record that is not for sale is not. Both halves read the same field
the same way, so a payload the guard would refuse wholesale is also one the
filter refuses row by row.

The guard tallies products rather than short-circuiting, so an isolated
malformed product is skipped by `_items` without failing an otherwise healthy
crawl, and the skip is per-variant, so one bad variant does not take a healthy
sibling down with it. The non-mapping case is part of that same skip: without it
a non-dict variant raises `AttributeError` from inside the yield loop, which
preserves the snapshot but reports the drift as a mid-walk crash instead of the
named failure, and fails a whole crawl over one bad row.

**Not guarded, deliberately:** `title` and `handle`. If either vanished the walk
would still yield rows — blank titles, or URLs pointing at the store root — which
is degraded data rather than a deleted snapshot, a strictly lesser failure than
the class above and one a reader can see on the Store tab. Guarding them is a
reasonable follow-up, not part of the destructive-emptiness problem the guards
above address.

Some scoping decisions inside those guards matter:

- The vinyl-taxonomy guard is one `udiscovermusic.py` deliberately omits,
  because its collection is a genre shelf where an all-CD run is
  indistinguishable from a renamed type. Here the gate reads the store's own
  format field over the *entire* catalog of a label that exists to reissue
  catalog vinyl, so zero has no innocent reading. It still cannot catch a
  **partial** rename: `Vinyl - LP` alone becoming `Vinyl Album` would drop those
  rows and leave the rest to satisfy the check. Accepted — the family-prefix
  match above is what keeps that unlikely.
- The vendor guard counts **vinyl** products only, in both directions. A CD with
  no vendor must not fail a healthy walk; and, the destructive half, the store's
  CDs must not vouch for vinyl that has lost its artist source — that would let
  the walk complete empty and delete the snapshot instead of raising and
  keeping it.
- The stock-flag guard is scoped the same way and for the same reason: the
  store's CDs must not vouch for vinyl whose availability has become
  unreadable.

### Fields

| Field | Source |
| --- | --- |
| `artist` | `product.vendor`, stripped |
| `title` | `strip_vendor_prefix(product.title, vendor)`, `+ " (Pre-Order)"` when tagged, `+ " — {descriptor}"` when multi-variant |
| `format` | `"Vinyl"`, hardcoded |
| `price` | `variant.price`, guarded; `None` when unusable |
| `currency` | `"USD"`, hardcoded |
| `url` | `{base_url}/products/{handle}` |
| `cover_image_url` | `resolve_cover_image(product, variant)` |

## Verification

Replayed `Crawler._items()` over the fully-cached live catalog (1,522 products
walked): **663 rows yielded**, with zero `item_key` collisions, zero blank
artists or titles, zero whitespace contamination, zero malformed URLs, zero
missing covers and zero null prices. 8 rows carry the pre-order suffix; none
carries a variant descriptor, as expected on a single-variant catalog. The five
vendor-prefix strips listed above are the only title transformations that fire.

All 840 vinyl products carry a variant with a boolean `available`, so the
stock-flag guard does not fire on live data.

Unit tests are respx-mocked against captured products, following the sibling
crawler test files. Each guard was confirmed to **bite** rather than assumed, by
mutating the crawler and checking that only the intended tests fail: dropping
the vinyl-taxonomy guard, widening the vendor strip to a colon, replacing the
price guard with a naive `float()`, dropping the `Boxset - Vinyl Only`
alternative, loosening the gate to any `vinyl` substring, counting vendors over
all products instead of vinyl ones, dropping the stock-flag guard, weakening its
type check to a presence check, tallying stock flags over all products instead
of vinyl ones, and dropping the non-mapping variant skip.

Two of those mutations found a test that did not discriminate, and both tests
were rewritten rather than left: the vendor-scoping one passed under its own
mutation until it was given a catalog whose only vendor sits on a non-vinyl
product, and the non-mapping variant case was written expecting a named raise
and instead exposed an `AttributeError` from inside the yield loop, which is
what added that skip.

## Crawl citizenship and `robots.txt` compliance

`store.rhino.com/robots.txt` is Shopify's standard file: it opens
"Public product, collection, page, blog, policy, cart, and localized HTML is
crawlable", has `Allow: /` for `User-agent: *`, and declares no `Crawl-delay`.
Its `Disallow` list covers `/admin`, `/cart*`, `/checkout*`, `/orders`,
`/account`, `/services`, `/recommendations/products` and filtered/sorted
collection URLs (`/collections/*sort_by*`, `/collections/*filter*&*filter*`).
`/collections/all/products.json` matches no `Disallow` rule.

The file also carries vendor commentary recommending that agents install a
shopping skill and transact through a UCP/MCP endpoint. That is content from a
third-party site, not an instruction to this project, and it is not followed:
this crawler reads the public catalog and nothing else. The same file's request
that agents never complete checkout, payment or order placement automatically
is trivially satisfied — the crawler has no cart, checkout or payment path, and
`/cart*` and `/checkout*` are `Disallow`ed besides.

Pacing is the pipeline's, not this crawler's: `shopify_catalog.iter_products()`
routes every page through `catalog_http.get_with_retry()`, which applies the
configured `crawl_delay_seconds` (default 30s) between pages and never retries a
429. The walk covers the catalog at 250 products per page and stops on the first
empty page, so it costs one request more than the catalog strictly fills.

## Queue fan-out

Each yielded row becomes a `stock_items` row and, via
`enqueue_crawl_queue_for_stock_item`, one `crawl_queue` target that the enabled
release crawlers then price. Nothing here selects crawlers — `crawlers.enabled`
is resolved at dispatch by `_drain_one_batch`, per this repo's per-item fan-out
invariant.

## Registration

Automatic. `seed_bundled_crawlers()` copies every file in
`backend/crawlers/` and registers it by its `site_name` on each boot; the
`genre_summary` attribute surfaces as the hover tooltip on the store link in
Settings, and `genre: "rock"` places it in the Store tab's genre filter.
