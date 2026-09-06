# Music On Vinyl store crawler design

**Status:** implemented
**Date:** 2026-09-06
**Store:** https://www.musiconvinyl.com/collections/all-products

## Problem

Music On Vinyl is the Haarlem reissue label behind a large share of the
audiophile 180g represses on the shelves of European record shops: classic
rock, jazz, soul, film scores and Dutch pop, licensed from the major
catalogs and sold direct from its own webshop. None of that stock reaches the
Store tab, and none of it is matched against a user's library under the Store
tab's Collection and Wantlist filters.

The store runs Shopify (`music-on-vinyl-store.myshopify.com`), so
`shopify_catalog.iter_products()` already implements the transport. The
request named the store's `all-products` collection, and what needed
deciding was whether that collection is the right source, what the store's
type field can be trusted for, and how its collective vendor credits should
be recorded.

## Scope

**In:** a `catalog`-type plugin, `backend/crawlers/musiconvinyl.py`, walking
the store's `all-products` collection over the public `products.json` endpoint
and yielding in-stock vinyl as stock items, priced in EUR.

**Out:**

- CDs, CD+Blu-ray sets, Blu-ray/DVD sets and the artbook the store also sells.
  None of these are in `all-products` except one CD box set the store
  shelved there, and the type gate drops it. See *Type gate*.
- One record the store typed `Music` instead of `Vinyl` (Three Drives,
  `Greece 2000`). Accepted scope loss: the type gate reads a format claim and
  `Music` is not one.
- Any release-type (per-library-item) crawling of this store. This is a
  catalog source; the Store tab's own crawlers price its items.

## Technical grounding

Everything below was gathered live on 2026-09-06 by fully paginating the
store's `all-products` and `all` collections and caching the payloads.

### Collection choice: `all-products`, and why not `all`

`all-products` is the collection the store itself titles **ALL VINYL**, and
the request named it. It is not merely a curated subset of the catalog:
diffing it against Shopify's built-in `all` collection, every product `all`
adds is a CD, a CD+Blu-ray, a Blu-ray+DVD or an artbook, so walking `all`
would only enlarge the set the type gate then drops. `all-products` returned
1,095 products over five pages; `all` returned 1,111, matching the product
sitemap.

`collections.json` reports `all-products` as holding 2,990. That figure counts
unpublished products, exactly as it did for `matadorrecords.py`'s store; the
endpoint's 1,095 agrees with the storefront's own listing pages (every handle
on the first and last HTML page is in the JSON walk) and with the sitemap.
The store is served identically to a non-EU client: the walk from this
session's US egress returned the same products, and `?country=NL` changed
nothing.

### Type gate: `product_type` is a format claim here

Unlike Matador's store, the type says what the product is pressed on:

| `product_type` | Where | What it holds |
| --- | --- | --- |
| `Vinyl` | all but two products of `all-products` | records |
| `CD` | one product in `all-products`; the rest only in `all` | `Heartstopper Boxset`, a CD set shelved under ALL VINYL |
| `Music` | one product in `all-products` | `Greece 2000`, a 12" the store left generically typed |
| `CD+Bluray`, `Bluray + DVD`, `Artbook` | only in `all` | discs and a book |

So membership of the collection is not the gate; the type is. `_is_vinyl()`
admits a type that **begins with the word `Vinyl`** (`^vinyl(?![a-z])`,
case-insensitive), so that a variant the store might introduce (`Vinyl - 2LP`,
as `rhino.py`'s store writes them) stays in scope, while `Vinyls`, `Coloured
Vinyl` and `Music` stay out. Positive rather than negative because the
alternative is publishing a CD box set as a record.

### One variant per product; coloured pressings are separate products

Every product carries exactly one variant, titled `Default Title`, with no
featured image of its own. The store lists a coloured pressing as its own
product — `Elegant Gypsy` (black, `al-di-meola-elegant-gypsy-vinyl`) and
`Elegant Gypsy` (red, `al-dmeola-elegant-gypsy-red-vinyl`) — under the same
vendor and title, so several dozen (vendor, title) pairs repeat across
products. `item_key` is `sha256(artist|title|url)` and the URL is built from
the handle, so those rows are already distinct; the Store tab shows them as
two same-titled rows with different prices and links. The colour is only
recoverable from the handle slug or the free-text description, and neither is
read: a handle is not a display field, and the title as the store writes it is
the identity the row keeps.

Because the catalog is single-variant throughout, no per-variant descriptor
is appended on live data. One is kept for a product that grows a second
variant, so its pressings yield distinct rows rather than collapsing onto one
`item_key`, and it is read from the **variant's own title** and nothing else:
a variant titled `Default Title` (or nothing) yields the bare product title,
and a variant the store has named yields `{title} — {variant title}`. Not
from the sibling count, as `rhino.py` keys it: `item_key` hashes the title,
so a row whose descriptor appeared the day a sibling was listed would re-key
and orphan its listings, judgments and saves over a change to a *different*
variant — the same identity churn the pre-order label was dropped for. Under
this rule an existing variant's row changes only when the store renames that
variant, which is what Shopify does when a product gains options. Two
variants of one product that both lack a usable title would resolve to the
same row; the second is skipped rather than emitted under a colliding key.

**Amendment (2026-09-06, review round 2):** the first draft appended the
descriptor only when the product had more than one variant, falling back to
the variant id. Copilot's review pointed out the sibling-count dependence;
the rule above is the correction.

### Artist: `vendor`, with `Various Artists` rewritten to `Various`

`vendor` is the artist on every product; none is blank. Two collective
credits appear:

- **`Various Artists`** becomes **`Various`** — Discogs' own entity name, and
  the exact string two consumers compare against: `amazon.py`'s
  `Crawler._artist()` special-cases the literal `various` to search by title
  alone, and `db._library_release_match_sql` does an exact `LOWER()` equality
  against the catalog artist. Same rewrite as `angryyoungandpoor.py` and
  `cleorecs.py`.
- **`Original Soundtrack`** is left as written. Discogs credits a score to
  its composer and a song compilation to Various, and nothing in the payload
  says which a product is — the tags carry a composer's name split across
  entries (`STEVEN`, `PRICE`) on a few products and nothing on most — so a
  rewrite would be a guess in either direction. Those rows display in the
  Store tab and will not match a library record; accepted.

A blank vendor is skipped, and a catalog of them raises.

### Title: the product title, with the shared exact-case `" - "` strip

The store keeps the artist out of the title. Self-titled albums (`The Civil
Wars`, `Cheap Trick`, `Blue Öyster Cult`) are the bare name with no
separator, and no title carries a `{vendor} - ` prefix, so
`strip_vendor_prefix` is the drift guard it is on the sibling stores rather
than a live transformation. It runs against the vendor as written, before the
`Various` rewrite, since that is the spelling a prefix would carry.

Titles are otherwise stored as the store writes them, including the
store-exclusive suffix (`Elvis Now | MOV Exclusive - Ltd.500`) and the
limited-run suffix (`Black Market Gardening - Ltd. 250`). Both name the
pressing and separate it from the standard product, and
`_library_release_match_sql` is exact-or-prefix-with-space, so `Elvis Now`
still matches through its suffix.

### Availability: the `available` flag; no pre-order bypass, no pre-order label

Availability reads Shopify's `available` flag and nothing else. The store's
`Pre-order` tag is accurate — 71 of the 77 tagged products report
`available: true`, and the six that do not are limited pressings sold through
before release (`Black Market Gardening - Ltd. 250`), so an unavailable
pre-order is gone allocation — but it is not read. No availability bypass,
the same call as `rhino.py`, `udiscovermusic.py` and `hammerheart.py`; and
no ` (Pre-Order)` title suffix either, departing from those siblings:
`compute_item_key` hashes the title, so a marker that disappears when the
record ships would re-key the row and orphan the listings, judgments and
saves hanging off its old identity. `darksiderecords.py` declined the label
on its store for the same reason, and the row's identity here depends on
nothing but the product itself.

**Amendment (2026-09-06, review round 1):** the first draft appended the
suffix, as the sibling Shopify crawlers do. Copilot's review pointed out the
re-keying; the rule above is the correction, and the tests pin the absence.

### Price and currency

`meta.json` reports `"currency":"EUR"` and the store ships from Haarlem, so
`EUR` is hardcoded, as `spv.py` and `jetglowrecordings.py` do for their
stores; `formatPrice.ts` renders it with its own symbol. Live prices run
€6.99–€159.99 and every row carries one. `_price` rejects booleans before
`float()` and non-finite or non-positive values after it, the guard shape the
siblings converged on.

About fifty product descriptions carry "Due to rights restrictions, this item
is only available in the EU." That is a shipping restriction, not a stock
state — the products are published, priced and flagged available — and the
crawler records what the store lists.

### Images

Every product has at least one image and no variant has one of its own, so
`resolve_cover_image` always falls back to the product image.

### Drift guards

`db.replace_stock_items()` DELETEs this crawler's previous snapshot before
inserting, and `_sync_stock` only skips that call when the crawl **raised** —
a completed-but-empty walk is destructive where a raise is inert. Each guard
names a distinct way the payload can stop carrying what this crawler reads:

| Guard | Fires when | Drift it names |
| --- | --- | --- |
| `products_seen == 0` | the collection returns nothing | `all-products` renamed or removed, or the endpoint changed shape |
| `vinyl_seen == 0` | no product carries a type beginning with `Vinyl` | the type taxonomy was renamed wholesale |
| `vendor_ok == 0` | no vinyl product carries a vendor | the artist source moved |
| `not yielded and identity_missing` | the walk produced no rows *and* some vinyl product with a vendor has a blank or absent `title` or `handle` | an identity field vanished or was renamed store-wide |
| `not yielded and unreadable_stock` | the walk produced no rows *and* some vinyl product with a vendor and an identity has no variant, or a variant without a boolean `available` | the availability field vanished, was renamed, or changed type |
| `yielded and not priced` | rows came through and *none* of them carries a price | the `price` field vanished or changed type store-wide |

The tallies are **nested**, vinyl → vendor → identity → readable, so a
non-zero count means "some product would have yielded a row if it were in
stock". The field tallies are taken before the availability filter, so a
sold-out product still counts toward every one of them; `yielded` and
`priced` are counted after it, which is why the guards reading them are each
conditioned on a second tally rather than on emptiness alone — a shelf that
has simply sold out is empty legitimately.

The per-variant filter admits a variant on the literal `True` and nothing
else, because `"false"` is truthy and a falsiness test would publish a
sold-out record as in stock. Readability is every(), not any(), over a
product's mapping variants, and non-mapping entries are dropped before the
variant count so a junk sibling can neither raise mid-walk nor re-title the
healthy row.

### Fields

| Field | Source |
| --- | --- |
| `artist` | `product.vendor`; `Various` when the vendor is `Various Artists` |
| `title` | `strip_vendor_prefix(product.title, vendor)`, `+ " — {variant title}"` when the variant carries one; never a pre-order marker |
| `format` | `"Vinyl"`, hardcoded |
| `price` | `variant.price`, guarded; `None` when unusable |
| `currency` | `"EUR"`, hardcoded |
| `url` | `{base_url}/products/{handle}` |
| `cover_image_url` | `resolve_cover_image(product, variant)` |

## Verification

Replayed `Crawler._items()` over the fully-cached live catalog: 1,095
products walked, 1,093 pass the type gate, all of them carry a vendor, and
**1,076 rows yielded** — the 17 products that yield nothing are exactly the
ones the store flags unavailable. Zero `item_key` collisions, zero blank
artists or titles, zero whitespace contamination, zero malformed URLs, zero
missing covers, zero null prices. 14 rows are credited to `Various`.

Unit tests are respx-mocked against captured products, following the sibling
crawler test files, and cover the type gate, the collective-vendor rewrite,
the same-title pressings, the exclusive suffix, the vendor strip and its
self-titled non-case, the absence of a pre-order label and of an
availability bypass,
the descriptor on an altered multi-variant product and its independence from
the sibling count, junk variant entries,
every price shape, cover resolution, and each drift guard on both its firing
and its non-firing side.

## Crawl citizenship and `robots.txt` compliance

`musiconvinyl.com/robots.txt` is Shopify's current standard file: `User-agent:
*` with a `Disallow` list covering `/admin`, `/cart`, `/checkout`, `/orders`,
`/account`, `/search` and filtered or sorted collection URLs, and no
`Crawl-delay`. Its preamble states that public product and collection content
is crawlable and asks agents not to complete checkouts, which this crawler
never touches. `/collections/all-products/products.json` matches no `Disallow`
rule.

Pacing is the pipeline's, not this crawler's: `shopify_catalog.iter_products()`
routes every page through `catalog_http.get_with_retry()`, which applies the
configured `crawl_delay_seconds` between pages and never retries a 429. The
catalog fits in five pages, so a full walk is six requests.

## Queue fan-out

Each yielded row becomes a `stock_items` row and, via
`enqueue_crawl_queue_for_stock_item`, one `crawl_queue` target that the enabled
release crawlers then price. Nothing here selects crawlers — `crawlers.enabled`
is resolved at dispatch by `_drain_one_batch`, per this repo's per-item fan-out
invariant. One conditional applies to every catalog source alike: when the
admin's `crawl_library_only` setting is on, `_sync_stock` still calls the
helper for every row, but its shared crawlability gate
(`db._stock_item_crawlable`) enqueues only an item some user has saved or
that matches a collection or wantlist record through the
`library_stock_item_keys` view; the rest are listed in the Store tab unpriced
until that changes.

## Registration

Automatic. `seed_bundled_crawlers()` copies every file in `backend/crawlers/`
and registers it by its `site_name` on each boot; the `genre_summary`
attribute surfaces as the hover tooltip on the store link in Settings, and
`genre: "rock"` places it in the Store tab's genre filter, as it does for
`rhino.py`.
