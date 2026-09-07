# Earache Records crawler design

**Status:** implemented
**Date:** 2026-09-07
**Store:** https://earache.com/collections/vinyl

## Problem

Earache Records' own webstore carries the label's catalog — Napalm Death,
Carcass, Morbid Angel, Bolt Thrower, Entombed, Cathedral, Godflesh, Sleep,
At The Gates, Deicide, Massacre, Wormrot, 200 Stab Wounds, Blood Incantation
— alongside a large licensed distro (AC/DC, Black Sabbath, Metallica,
Alice In Chains, Amon Amarth, Abbath, Cult Of Luna, Converge). None of that
stock is covered by a bundled crawler, so none of it reaches the Store tab
and none of it is matched against a user's library under the Store tab's
Collection and Wantlist filters.

The store runs Shopify (`earache-webstore.myshopify.com`), so
`shopify_catalog.iter_products()` already implements the transport. What
needed deciding was how to read a row out of a payload that differs from
every sibling Shopify store in one decisive way: **`vendor` names nobody**.
It is `vendor-unknown` on the bulk of the catalog and the label's own name
(`Earache Records Ltd`, `Earache Records`, `Earache Webstore`, `EARACHE`) on
the rest, so the artist lives only in the product title — and the title's
convention is `Artist "Album" descriptor`, with a quoted album rather than
the dash separator the sibling stores use.

## Scope

**In:** a `catalog`-type plugin, `backend/crawlers/earache.py`, walking the
store's `vinyl` collection over the public `products.json` endpoint and
yielding in-stock vinyl as stock items.

**Out:**

- CDs, cassettes, T-shirts, hoodies, tickets and the rest of the store, none
  of which are in the `vinyl` collection. The handful of non-vinyl products
  that *are* shelved in it are filtered out (below).
- Multi-record bundles, which the store shelves in the same collection.
- Vinyl the store shelves outside the `vinyl` collection. Confirmed live
  (2026-09-07) that products whose title reads as a record sit under other
  shelves — signature-bundle listings typed by release date
  (`2026-09-04B`), a few strays typed `SHIPPING - SINGLE LP` or even
  `All CDs`. Recovering them means walking the store's `all` collection
  (6,491 published products, most of them apparel and CDs) and gating on the
  title alone, a far weaker signal than the shelf the request named. The
  store's own shelving decides.
- Any release-type (per-library-item) crawling of this store. This is a
  catalog source; the Store tab's own crawlers price its items.

## Technical grounding

Everything below was gathered live on 2026-09-07 by fully paginating the
store's `vinyl` collection and its `all` collection, reading `meta.json`,
`collections.json` and `robots.txt`, and caching the payloads.

### Collection choice: `vinyl`

The request named `/collections/vinyl`. Paginating it returns 2,092 products
(nine pages at `limit=250`, the last short), and the walk is stable: the same
2,092 ids come back at `limit=50` across 42 pages, so the ceiling in
`shopify_catalog.iter_products()` is nowhere near being reached and no page
is repeated or skipped.

`collections.json` reports the collection's `products_count` as 6,642. That
figure is not reachable and not a shortfall to chase: the store's *entire*
published catalog — its `all` collection, apparel and CDs and tickets
included — is 6,491 products, fewer than the count claimed for this one
shelf. `products_count` counts products that are not published to the online
store; `products.json` returns the published ones, and those are what a
shopper sees and what this crawler walks.

`robots.txt` allows `/collections/` and `/products/` wholesale; the only
`collections` rules are against sort, filter and language-picker crawl
traps, none of which this path touches.

`meta.json` reports `"currency":"GBP"` and `"country":"GB"` — the store is
in Nottingham, England — so `currency` is hardcoded `"GBP"`, as
`musiconvinyl.py`, `spkr.py` and `spv.py` hardcode `"EUR"`.

### The artist comes from the title, and only from the title

`vendor` is unusable: `vendor-unknown` on most products and the label's own
name on the rest, in four spellings. Every product's artist is instead the
part of the title before the quoted album:

```
1349 "Massive Cauldron Of Chaos" Special Edition White/Black Vinyl
Abbath "Dread Reaver" Gatefold Silver Vinyl
40 Watt Sun "A Perfect Light" 3x12" Yellow Vinyl
```

so the parse is `^(artist)"(album)"(descriptor)$`, and it fits: of the 2,092
products, all but five carry a quoted album, and the five that do not are
four bundles and a "Lucky Dip" mixed-lot sale plus one genuine record
(`Exhumed / Iron Reagan Split 12" EP`).

**The `Artists A-Z` tag is deliberately not used as a fallback.** Most
products carry one or more tags of the form `Artists A-Z. Artists A-Z:
<name>`, and it is tempting to read the artist from there when the title
does not parse. Shopify serialises tags alphabetically, which is the only
order the payload offers, and on this store the first one alphabetically is
demonstrably *not* the credit: `Nihilist "Carnal Leftovers"` is tagged
`Entombed` before `Nihilist`, `Ozzy Osbourne "Bark At The Moon"` is tagged
`Black Sabbath` before `Ozzy Osbourne`, `Massive Wagons "TRIGGERED!"` is
tagged `Dub War` first. A source that mis-credits a record whenever it is
the only source is worse than no source, and everything it would recover
here is a bundle. A product with no quoted album is skipped.

#### Where the quote ends

The closing quote is not reliably the same character as the opening one, and
the store types inch markers with the same `"`. Three live shapes force the
rule:

- `Corrosion Of Conformity "America's Volume Dealer' Black / White Swirl
  Vinyl` and `Motorhead "Overnight Sensation' Vinyl` close with an
  apostrophe, so `'` and `’` must be able to close the album.
- `AC/DC "'74 Jailbreak" Vinyl`, `David Bowie "Live In Santa Monica '72"
  Vinyl` and `Anathema "We're Here Because We're Here" Vinyl` carry
  apostrophes *inside* the album, so an apostrophe may only close it when
  whitespace or the end of the title follows.
- `Sikth "Death Of A Dead Day"2x12"  Vinyl` closes with `"` glued straight
  onto the descriptor, so a `"` may also close the album when a **digit**
  follows. A digit and not any character: `40 Watt Sun "A Perfect Light"
  3x12" Yellow Vinyl` proves the descriptor's own inch markers must not be
  mistaken for the album's closing quote, and an inch marker is always
  *preceded* by its digits, never followed by one.

The artist group excludes `"` and `“` outright, so the album's *opening*
quote is always the first one in the title and an inch marker can never be
read as one.

### The row's title keeps the descriptor, and keeps it after the album

The composed title is the album followed by the descriptor, with the quotes
gone: `Massive Cauldron Of Chaos Special Edition White/Black Vinyl`. Both
halves of that are load-bearing.

Keeping the descriptor is what distinguishes one pressing from another —
the store lists a black, a splatter and a picture-disc pressing of the same
album as separate products, and `title_key` folds the format words away for
the Cheapest filter regardless.

Keeping it *after* the album is what preserves the library match.
`db._library_release_match_sql` matches a stock row to a catalog release on
`LOWER(s.title) = LOWER(c.title) OR starts_with(LOWER(s.title),
LOWER(c.title) || ' ')` — an exact-or-prefix-with-space test against the
catalog title. `Massive Cauldron Of Chaos Special Edition White/Black Vinyl`
prefix-matches a library `Massive Cauldron Of Chaos`; the raw product title,
with its quotes still around the album, would not.

Whitespace is collapsed on the way through, because the store leaves double
spaces in a few titles (`Decapitated "Nihility" Merge Vinyl  (Ltd to 500
Copies)`).

### Bundles are excluded

The collection shelves multi-record bundles beside the records:
`Dream Theater 10LP Clear Vinyl Bundle`, `In Flames Anniversary Bundle -
"Siren Charms", "Soundtrack To Your Escape", "Colony" & "Lunar Strain"
180g Colour Vinyl`, `Threshold "Psychedelicatessen" & "Wounded Land" Colour
Vinyl Bundle`, `Lucky Dip Sale - 3 LPs for £25`. A bundle is not a Discogs
release, its price is not any record's price, and several of them defeat the
title parse outright — `Prong Vinyl Bundle - "Prove You Wrong",
"Cleansing", "Beg To Differ" & "Rude Awakening"` parses to an "artist" of
`Prong Vinyl Bundle -`. The single-album ones (`Morbid Angel "Entangled In
Chaos" Triple Vinyl Bundle`) are several copies of one record at one price,
which is not a listing for that record either. A title naming a bundle, or
the store's Lucky Dip sale, is skipped.

### Format gate: vinyl wins, then a named other medium rejects

The shelf is the vinyl shelf, so the descriptor is read as vinyl by default.
Two narrow rules apply on top, in this order:

1. A descriptor naming a **record** is admitted outright — `Vinyl`, `LP`,
   `2x12"`, `Picture Disc`, `Test Pressing`. This is what keeps the box sets
   that bundle a record in (`Darkthrone "Pre-Historic Metal" Deluxe Splatter
   Vinyl / CD / Cassette Tape Box Set`, `Heresy "Face Up To It..." 2x12"
   Vinyl (inc CD)`), and what keeps `Green Druid "At The Maw Of Ruin"
   REJECTED Test Pressing (Side A/B only)` in despite it naming no format
   word at all.
2. Failing that, a descriptor naming **another medium** is rejected —
   `Cassette`, `CD`, `DVD`, `Blu-Ray`. Live this drops exactly the store's
   `Cassette Tape Collector's Box` reissue series (Carcass, Godflesh, Morbid
   Angel, Napalm Death) plus `Oasis "Definitely Maybe (Deluxe Edition)" 30th
   Anniversary 2 CD` and `Thorns "Thorns" CD`.

The gate reads the **descriptor**, not the whole title, so an album name
cannot trip it. `Morbid Angel "ABCD" Cassette Tape Collector's Box` is
rejected on its descriptor; the album `ABCD` never enters the test, and
would not match `\bcds?\b` if it did.

Anything matching neither rule is admitted, on the collection's own claim.
Live that is one product, `Metallica "Metallica (The Black Album)'
Remastered Deluxe Box Set` — a vinyl box, correctly admitted.

### Variants, availability and pre-orders

Almost every product is single-variant with Shopify's `Default Title`
placeholder; the pressing is named in the product title instead. The
exceptions are a handful of "Choice of Colour" products whose colour lives
only in the variant (`Massive Wagons "TRIGGERED!" Choice of Colour Vinyl w/
12 Page Booklet` → `Orange`, `Green`, `Pink`).

So the rule is `spkr.py`'s and `darksiderecords.py`'s: a named variant is
appended to every row that has one, and the placeholder is admitted only as
a product's sole variant, carrying the title alone. `compute_item_key`
hashes the row's title and URL, and the URL is per-product, so without the
appended colour the three `TRIGGERED!` pressings would collapse onto one
item_key; and appending only when a sibling happens to be listed would
re-title the row — orphaning its listings, judgments and saves — the day
that sibling sold out.

Availability is `variant.available`, and only the literal `True` admits a
row. **There is no pre-order bypass.** Pre-orders are marked twice on this
store, by a `Pre-Orders` tag and by a `- PRE-ORDER` suffix the store types
into the title, and they are overwhelmingly available; the ones that report
`False` are closed early-bird allocations, confirmed live to render "Sold
Out" on their product pages. An unavailable pre-order here is gone
allocation, not not-yet-released.

**No ` (Pre-Order)` marker is written either**, and the tag is not read.
`compute_item_key` hashes the title, so a marker that vanishes when the
record ships would re-key the row. The store's own `- PRE-ORDER` suffix is
already part of the descriptor and is kept verbatim, exactly as every other
word the store wrote there is — the crawler neither adds nor removes it.
Same call as `spkr.py` and `musiconvinyl.py`.

### Prices and images

Every live variant carries a positive `price` string in GBP; none is zero or
missing. Every product carries at least one image, and 
`resolve_cover_image()` prefers the variant's own where the store sets one
(it does on most multi-colour products).

### Replay over the live catalog

Replaying the crawler over the fully-cached collection: 2,092 products → 15
skipped for carrying no readable `Artist "Album"` (the 14 bundles, and the
one split with no quoted album), 8 rejected on format, 77 skipped as sold
out → 1,998 rows across 567 distinct artists, with no duplicate `(artist,
title, url)` identity, no blank artist or title, no whitespace
contamination, no malformed URL, no missing cover image and no null price.

## Drift guards

`db.replace_stock_items()` DELETEs this crawler's previous snapshot before
inserting, and `_sync_stock` skips that call only when the crawl **raised**
— so a completed-but-empty walk is destructive where a raise is inert. Each
guard names a distinct way the payload can stop carrying what this crawler
reads:

| Guard | Raises when |
| --- | --- |
| `no products` | the collection returned nothing — renamed, removed, or markup drift |
| `artist-source drift` | no product in the collection has a title of the form `Artist "Album"` — the store moved the credit or dropped the quotes |
| `price-source drift` | rows were yielded but not one carries a price |
| `identity-source drift` | nothing was yielded while some record carries no title or no handle |
| `stock-source drift` | nothing was yielded while some record carries no readable availability flag |

The tallies feeding them are **nested**, not sibling: a product only counts
toward the identity and stock tallies if it already parsed an artist and
survived the format gate, so a non-zero count always means "some product
would have yielded a row if it were in stock". The two `not yielded` guards
are gated on an empty outcome so that an isolated bad product among real
rows stays an ordinary skipped row; the shelf genuinely selling out stays a
legitimate empty result.

The title tally is the one taken **outside** the format gate, because it
answers a different question — whether the store still writes its titles the
way this crawler reads them. Nested inside the gate as well, a shelf that
legitimately filled up with CDs would raise `artist-source drift` while
every title on it was perfectly readable.

There is deliberately **no** format-gate guard. The gate here is negative —
it rejects only a descriptor that names another medium — so there is no
positive signal whose disappearance would silently empty the walk, and a
collection of records that all stopped naming a format would still be
crawled correctly.

## Verification

`backend/tests/test_earache_crawler.py`, with fixtures marked at their
definition as captured (live products, trimmed), altered (a captured product
with one field changed) or invented (a shape the live catalog cannot
produce). Cases:

- the full item shape of a row, including `format`, `currency` and the URL
  built from the handle
- the title parse: quoted album, apostrophe-closed album, apostrophes inside
  the album, a `"` glued onto the descriptor, inch markers in the
  descriptor, whitespace collapsed, and the forms that must not parse
- the artist is never taken from `vendor` or from the `Artists A-Z` tags
- bundles and the Lucky Dip sale are skipped
- the format gate: records admitted, cassettes/CDs rejected, an unmatched
  descriptor admitted, an album name that resembles another medium not
  tripping it
- a named variant appended on every row that has one; the placeholder alone
  on a sole variant; the placeholder on a multi-variant product skipped
- sold-out variants and sold-out pre-orders skipped, with no marker written
- junk variant entries dropped before anything reads them
- price parsing, including the `bool`/`nan`/non-positive cases
- cover image resolution and its fallbacks
- each drift guard firing, and each not firing when it should not
