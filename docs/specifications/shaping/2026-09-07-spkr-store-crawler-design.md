# SPKR.store crawler design

**Status:** implemented
**Date:** 2026-09-07
**Store:** https://spkr.store/collections/vinyl

## Problem

SPKR.store is the mailorder of ProMedia GmbH in Flußbach, Germany — the
company behind Prophecy Productions and its imprints Lupus Lounge and
Auerbach — and it carries the Prophecy catalog (Alcest, Ulver, Dornenreich,
Tenhi, Paysage d'Hiver, Sol Invictus, Dool, Lantlôs, Empyrium) alongside a
distro of Magnetic Eye Records, Dependent, House of Mythology, Darkness Shall
Rise, Nordvis, Ripple Music, Testimony Records, Blues Funeral Recordings and
Amor Fati. None of that stock is covered by a bundled crawler, so none of it
reaches the Store tab and none of it is matched against a user's library
under the Store tab's Collection and Wantlist filters.

The store runs Shopify (`powered-by: Shopify`, `06c23c-2.myshopify.com`), so
`shopify_catalog.iter_products()` already implements the transport. What
needed deciding was different from the sibling Shopify stores in three ways:
**`vendor` is the literal string `details`** on every product, so the artist
lives only in the title; **pre-orders carry no signal on the product** and
are known only by membership of a second collection; and **the variants are
a mixed bag** — a colour, a catalogue number, or a format-and-colour pair —
under six different option schemes. Each is grounded below.

## Scope

**In:** a `catalog`-type plugin, `backend/crawlers/spkr.py`, walking the
store's `vinyl` collection over the public `products.json` endpoint and
yielding in-stock vinyl as stock items, marked `(Pre-Order)` where the
store's `pre-order` collection lists them.

**Out:**

- CDs, cassettes (`MC`), artbooks, books, DVDs and merch, none of which are
  in the `vinyl` collection.
- The `Boxset / Bundle` products, some of which bundle a record (`Nocternity
  - Onyx LP Boxset (Vinyl Box)`, `Mesh - A Perfect Solution (CD Box + Vinyl
  7")`). They are typed and shelved as boxsets, outside the collection the
  request named; a boxset is a different Discogs entity from the record in
  it.
- Any release-type (per-library-item) crawling of this store. This is a
  catalog source; the Store tab's own crawlers price its items.

## Technical grounding

Everything below was gathered live on 2026-09-07 by fully paginating the
store's `vinyl` collection, its `pre-order` collection, the root
`products.json` and `collections.json`, and caching the payloads.

### Collection choice: `vinyl`

The request named `/collections/vinyl`, and it is the vinyl shelf and not a
curated subset: the collection returned 1,058 products across five pages,
every one typed `Vinyl`, and the root `products.json` (3,141 products) types
exactly those same 1,058 products `Vinyl` — the two sets are identical in
both directions. `collections.json` reports the collection as 1,060, a figure
that counts unpublished products; the endpoint returns 1,058 and that is what
is walked.

The rest of the store's collections are one per artist (`Alcest`, `Ulver`,
`Paysage d'Hiver` — twice, differently capitalised), one per label
(`Prophecy Productions`, `Lupus Lounge`, `Auerbach`, `Magnetic Eye Records`,
`Dependent`, `Darkness Shall Rise`, `Nordvis`), the other format shelves
(`cd`, `MCs`, `Artbook`, `Boxsets / Bundles`, `DVD / Blu-ray`), merch shelves,
and housekeeping shelves (`Reduced Tax`, `Name Your Price`, `Pre-Order`).
There is no `all` collection; Shopify's built-in one would return the whole
store, and the type gate would then drop two thirds of it.

### Format gate: `product_type`

Unlike `iodinerecords.py`'s store, where the type says what kind of thing a
product is and never what it is pressed on, here **the type is the format**:

| `product_type` | Products (root catalog) |
| --- | --- |
| `CD` | 1,250 |
| `Vinyl` | 1,058 |
| `T-Shirt` | 174 |
| `Artbook` | 166 |
| `MC` | 111 |
| `Boxset / Bundle` | 66 |
| *(merch, books, DVDs, blank)* | the rest |

`_VINYL_TYPE_RE` matches the word `Vinyl` at the start of the type, as
`musiconvinyl.py` does, so a subtype the store might introduce (`Vinyl -
2LP`) stays in scope while a type that does not begin with it is not a
format claim and stays out. The collection's own membership is not the gate,
because a CD drifting into it (as two did on `hammerheart.py`'s store) would
otherwise be listed as a record.

Because the type already says the product is a record, the **variant layer
is negative**, in `iodinerecords.py`'s vocabulary and check order (a vinyl
word admits, then a merch word rejects, then an inch marker admits, then a
non-vinyl medium rejects, and anything else is a pressing). No live variant
names another medium; the gate is for the day a `Format` option grows a CD
beside the LP. The vinyl-word and media-word patterns admit a hyphen as the
count separator (`2-LP`, `2-CD`) because that is how this store writes disc
counts.

### Artist and title: the dashed product title

`vendor` is the literal string `details` on all 1,058 vinyl products (and on
every product in the root catalog). The tags carry a label name on about
half of them (`Prophecy Productions` 321, `Magnetic Eye Records` 65,
`Testimony` 30, `Dependent` 27, `House of Mythology` 26, `Kunsthall` 21) and
nothing else — never the artist.

The product title is the artist source. Every vinyl product is written
`Artist - Album (descriptor)`, with a spaced hyphen between artist and album
and a trailing parenthesised descriptor that on all but one product starts
with `Vinyl` and names the format and, on single-variant products, the
colour:

```
Darvaza - We Are Him (Vinyl Gatefold LP)
Devil´s Hour - Black n´Punk Marauders (Vinyl 12" EP)
Draugveil & Selvnatt - Blades & Roses (Vinyl LP - White)
Abigor - Nachthymnen (From The Twilight Kingdom) (Vinyl LP)
Surturian - II - Hessian Spears (Vinyl LP)
Paysage d'Hiver - Schnee (Black)
```

**The first ` - ` is the split.** An album can carry a spaced hyphen of its
own (`Surturian - II - Hessian Spears`, `Nachtmystium - Addicts - Black
Meddle Pt. II`, `Various Artists - Alice In Chains - Dirt (Redux)`); an
artist never does. This was checked rather than assumed: the store keeps one
collection per artist, and reading every vinyl title up to its first ` - `
gives a string that is a collection title on 1,057 of the 1,058 products.
The one miss is `Lantlos - Nowhere In Between Forever`, where the collection
is spelt `Lantlôs`. A hyphen that is not spaced (`E-L-R - Atropa`) is part of
the name.

**The rest of the title is kept verbatim**, descriptor and all (`We Are Him
(Vinyl Gatefold LP)`), on `darksiderecords.py`'s reasoning: the descriptor is
where the store says which pressing a single-variant product is (`Vinyl LP -
Black`, `Vinyl 12" EP`), `title_key` folds its format words away for the
Store tab's Cheapest filter, and the library match behind the Collection and
Wantlist filters is exact-or-prefix-with-space, which `We Are Him (Vinyl
Gatefold LP)` still satisfies for a library "We Are Him". Stripping it would
also have to decide what `(Black)` is on `Paysage d'Hiver - Schnee (Black)`
and what `(Redux)` is on `The Wall (Redux)`, and get one of them wrong.

`Various Artists` (19 products) is credited as `Various`, the rewrite
`musiconvinyl.py` and `cleorecs.py` make: Discogs' own entity name is
`Various`, and both `amazon.py`'s title-only search and `db.py`'s exact
LOWER() library match compare against that literal.

Whitespace is collapsed before the split: three live titles carry a double
space after a colon (`Submarine:  Beneath The Desert Floor Chapter 9`).

### Variants: six option schemes, one rule

The 1,058 products use these option schemes:

| `options` | Products | Variant titles look like |
| --- | --- | --- |
| `Title` | 600 | `Default Title` (Shopify's single-variant placeholder) |
| `Colour` | 397 | `Black`, `Splatter`, `Clear/Black Marble`, `transparent cream/black marble` |
| `SKU` | 50 | `NVP236LP`, `DSR357LP-blk`, `DSR362LP-milky clear`, `CW55` |
| `Format` + `Colour` | 7 | `Vinyl LP / black`, `Vinyl Picture LP / Picture` |
| `Format \| Colour` | 2 | `Vinyl Picture LP \| Zoetrope`, `Vinyl LP (DSR199black)` |
| `Format` | 2 | `Vinyl LP`, `Vinyl 2-LP Gatefold` |

Whatever the scheme, the variant title is the store's own name for the
pressing, and it is what a buyer picks from on the product page, so it is
what the row appends: `Monark (Vinyl Gatefold LP) — NVP236LPS`, `Väre (Vinyl)
— Vinyl 2-LP Gatefold / Clear`. A catalogue number is opaque as a name but
distinct as an identity, and the identity is what matters (below).

Six products type an HTML entity into a variant title (`&hellip;`, `&hellip;
(DSR308LPgold)`); the title is unescaped so the row reads `…` rather than
the entity. No product title carries one.

`Default Title` is honoured **only as a product's sole variant**, and a blank
variant title is never a pressing, on the rule `iodinerecords.py` records:
either would otherwise build a row on the bare title and the product URL,
which is the `item_key` every sibling built the same way would share. Junk
(non-mapping) entries are dropped before the sole-variant count is taken,
so a placeholder beside a null is still the sole variant.

### Identity: the pressing is appended on every row that names one

`item_key` is `sha256(artist|title|url)`, and every pressing of a product
shares all three, so the variant title has to be part of the row's title
for the rows to be distinct. It is appended **on every row that names a
pressing**, not only when the product has more than one variant, so that a
sibling being listed or delisted never re-titles the rows that stay and
orphans the listings, judgments and saves hanging off their old identity.
The placeholder is the one exception, and a safe one: Shopify only issues it
for a single-variant product.

Variant titles are whitespace-collapsed before use, so a double space inside
one cannot carry into the row's identity.

### Pre-orders: the `pre-order` collection

The store's `Pre-Order` shelf (`/collections/pre-order`) lists 86 products,
43 of them vinyl. **Nothing on the product says so**: no pre-order tag (the
vinyl products carry label tags only), no title marker, no `published_at`
in the future, and every one of the 69 pressings on those 43 products
reports `available: true`. The vinyl-collection copy of a pre-order product
and its pre-order-collection copy differ in `updated_at` and nothing the
crawler reads.

So the crawler walks the `pre-order` collection first, keeps the set of
handles it returns, and suffixes ` (Pre-Order)` on any vinyl row whose
handle is in it, placed before the pressing: `Monark (Vinyl Gatefold LP)
(Pre-Order) — NVP236LP`. Handle rather than title, because the handle is the
identity the URL is built from; a shelf entry with no handle can match
nothing, since a handle-less vinyl product is skipped before the shelf is
consulted.

The shelf walk goes through `iter_products()` like the catalog walk, so a
renamed or removed shelf raises out of `get_with_retry()` after its retry
budget rather than completing without the suffix — completing would re-key
every pre-order row on the next sync. An **empty** shelf is not drift:
nothing being on pre-order is an ordinary state, and the suffix is display.
It costs one extra listing page and one empty page per sync.

Availability itself reads Shopify's `available` flag and nothing else, with
**no pre-order bypass**: a pre-order pressing that reports `false` is gone
allocation, not not-yet-released. Same call as `iodinerecords.py`,
`matadorrecords.py` and `hammerheart.py`.

### Availability and images

96 of the 1,548 variants report `available: false`; 39 products are sold
out entirely. 578 variants carry a `featured_image`, so
`resolve_cover_image` returns the pressing's own image where the store has
one and the product image otherwise. One product (`Black Electric - Black
Electric`, sold out) has no images at all.

### Price and currency

`meta.json` reports `"currency":"EUR"` and `"country":"DE"`, the storefront's
`money_format` is `€{{amount_with_comma_separator}}`, and every rendered
price reads `€19,99 EUR`, so `EUR` is hardcoded. Live prices run
€5.99–€130.00 across the yielded rows, every one a string with a dot
decimal in the JSON, and every row carries one. `_price` rejects booleans
before `float()` and non-finite or non-positive values after it, the guard
shape the siblings converged on.

### Drift guards

`db.replace_stock_items()` DELETEs this crawler's previous snapshot before
inserting, and `_sync_stock` only skips that call when the crawl **raised** —
a completed-but-empty walk is destructive where a raise is inert. Each guard
names a distinct way the payload can stop carrying what this crawler reads:

| Guard | Fires when | Drift it names |
| --- | --- | --- |
| `products_seen == 0` | the collection returns nothing | `vinyl` renamed or removed, or the endpoint changed shape |
| `vinyl_seen == 0` | no product carries the `Vinyl` type | the type taxonomy was renamed wholesale, or the collection retargeted |
| `artist_ok == 0` | no `Vinyl` product's title is of the form `Artist - Album` | the store renamed its records, or moved the artist into `vendor` |
| `not yielded and identity_missing` | the walk produced no rows *and* some product that could have yielded one has a blank or absent `handle` | the identity field vanished or was renamed store-wide |
| `not yielded and unreadable_stock` | the walk produced no rows *and* some product that could have yielded one had a pressing with no boolean `available` | the availability field vanished, was renamed, or changed type |
| `yielded and not priced` | rows came through and *none* of them carries a price | the `price` field vanished or changed type store-wide |

The tallies are **nested**, not independent: a product counts toward
`artist_ok` only if it passed the type gate, and toward `identity_missing`
or `unreadable_stock` only if it also has an artist. A row needs all of
those on one product, so only such a product's readability says anything
about an empty result; tallied independently, a vinyl product with no
artist and a readable sold-out shirt would each satisfy one guard while
neither can yield.

There is no separate "no admitted pressing" guard, unlike
`iodinerecords.py`: there the format was read off the tags and could vanish
independently of the type, whereas here the type *is* the format and the
variant gate is negative, so a product that passes the type gate with any
non-blank, non-media variant title has a pressing. A store-wide loss of
variant titles leaves every product with no admitted pressing and no
readable flag, which the stock guard already catches.

The field tallies are taken **before** the availability filter, so a
sold-out product still counts toward every one of them; `yielded` and
`priced` are necessarily counted after it, which is why the two guards
reading them are each conditioned on a second tally rather than on
emptiness alone — a shelf that has simply sold out is empty legitimately.

Readability is judged over the admitted pressings only, with every() rather
than any(), on `matadorrecords.py`'s reasoning. The per-variant filter admits
a variant on the literal `True` and nothing else, because `"false"` is truthy
and a falsiness test would publish a sold-out record as in stock.

### Fields

| Field | Source |
| --- | --- |
| `artist` | `product.title` before the first ` - `; `Various Artists` → `Various` |
| `title` | `product.title` after the first ` - `, verbatim; `+ " (Pre-Order)"` when the handle is on the pre-order shelf; `+ " — {variant title}"` unless the variant is the placeholder |
| `format` | `"Vinyl"`, hardcoded |
| `price` | `variant.price`, guarded; `None` when unusable |
| `currency` | `"EUR"`, hardcoded |
| `url` | `{base_url}/products/{handle}`, the handle as written (three are Cyrillic) |
| `cover_image_url` | `resolve_cover_image(product, variant)` |

## Verification

Replayed `Crawler._items()` over the fully-cached live catalog with the
cached pre-order handles: 1,058 products walked, all 1,058 pass the type
gate, resolve an artist, carry a handle, and are readable, and **1,452 rows
yielded** — zero `item_key` collisions, zero blank artists or titles, zero
whitespace contamination, zero malformed URLs, zero missing covers and zero
null prices. 69 rows carry the pre-order suffix, 571 are placeholder rows,
28 are credited to `Various`, and ten carry an unescaped `…`.

Unit tests are respx-mocked against captured products, following the
sibling crawler test files. Each guard and rule was confirmed to **bite**
rather than assumed, by mutating the crawler and checking that the tests
fail: dropping the type gate, splitting on the last ` - ` instead of the
first, reading availability by truthiness, dropping the placeholder rule,
admitting a blank variant title, honouring the placeholder on a
multi-variant product, turning the variant gate positive, dropping the
entity unescape, dropping the `Various` rewrite, matching pre-orders by
title instead of handle, dropping the pre-order suffix, admitting a boolean
price, dropping the identity skip, weakening readability to any(), tallying
the artist on every product, and dropping each guard in turn. Every mutation
failed at least one test.

## Crawl citizenship and `robots.txt` compliance

`spkr.store/robots.txt` is Shopify's current standard file: `User-agent: *`
with `Allow: /`, a `Disallow` list covering `/admin`, `/cart`, `/checkout`,
`/orders`, `/account`, `/search`, `/recommendations/products` and filtered or
sorted collection URLs, and no `Crawl-delay`. `/collections/vinyl/products.json`
and `/collections/pre-order/products.json` match no `Disallow` rule. The
file's preamble points agents at a UCP/MCP endpoint for cart and checkout
and asks that no agent complete a checkout; this crawler reads the catalog
only and never touches either.

Pacing is the pipeline's, not this crawler's: `shopify_catalog.iter_products()`
routes every page through `catalog_http.get_with_retry()`, which applies the
configured `crawl_delay_seconds` between pages and never retries a 429. A
full walk is the pre-order shelf's page plus its empty page, then the vinyl
collection's five pages plus its empty page.

## Queue fan-out

Each yielded row becomes a `stock_items` row and, via
`enqueue_crawl_queue_for_stock_item`, one `crawl_queue` target that the
enabled release crawlers then price. Nothing here selects crawlers —
`crawlers.enabled` is resolved at dispatch by `_drain_one_batch`, per this
repo's per-item fan-out invariant.

## Registration

Automatic. `seed_bundled_crawlers()` copies every file in `backend/crawlers/`
and registers it by its `site_name` on each boot; the `genre_summary`
attribute surfaces as the hover tooltip on the store link in Settings, and
`genre: "metal"` places it in the Store tab's genre filter.
