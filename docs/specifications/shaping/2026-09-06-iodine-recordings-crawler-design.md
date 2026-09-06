# Iodine Recordings store crawler design

**Status:** implemented
**Date:** 2026-09-06
**Store:** https://iodinerecords.com/collections/

## Problem

Iodine Recordings is the Massachusetts punk, hardcore, emo and screamo label
behind Piebald, Stretch Arm Strong, NØ MAN, There Were Wires, Love Letter,
Onelinedrawing, Jeromes Dream and The Casket Lottery, and the current home of
the Quicksand, Only Living Witness and Further Seems Forever reissues, with a
Hydra Head Records distro alongside. Its store is not covered by any bundled
crawler, so none of that stock reaches the Store tab and none of it is matched
against a user's library under the Store tab's Collection and Wantlist
filters.

The store runs Shopify (`powered-by: Shopify`,
`iodine-recordings.myshopify.com`), so `shopify_catalog.iter_products()`
already implements the transport. What needed deciding was different from the
sibling Shopify stores in three ways: **the artist is nowhere but the product
title**, quoted as `Artist 'Album'`; **the format lives in the tags**, not the
type and not reliably the variant; and **a variant title is usually just a
colour**, so the per-variant gate has to be negative. Each is grounded below.

## Scope

**In:** a `catalog`-type plugin, `backend/crawlers/iodinerecords.py`, walking
the store's published catalog over the public `products.json` endpoint and
yielding in-stock vinyl as stock items.

**Out:**

- CD and cassette products, and the CD and cassette variants of the vinyl
  products.
- The label's annual subscription (`Iodine Noise Cult Vol. 5 (Annual)`),
  typed `Records` but quoting no album; see *Artist and title*.
- Merch and books, whatever their titles quote.
- Any release-type (per-library-item) crawling of this store. This is a
  catalog source; the Store tab's own crawlers price its items.

## Technical grounding

Everything below was gathered live on 2026-09-06 by fully paginating the
store's `all` collection and caching the payload.

### Collection choice: `all`

The store's `collections.json` is one collection per artist, plus curated
shelves (`new-releases`, `staff-picks`, `exclusives-distro-titles`,
`reissues-discographies`, `black-friday-sale-2025`), merch shelves, and two
format shelves, `12` ("12\" Records") and `7` ("7\" Records"). The format
shelves are curated rather than derived: between them they returned 61
products, while the tag-derived gate below admits 104 records from `all`, so
walking them would drop well over a third of the store's vinyl. There is no
`vinyl` collection.

`all` returned 164 products, which agrees with the root `products.json` (164)
and with `meta.json`'s `published_products_count` (164). `collections.json`
reports `shop-all` as holding 260, but that figure counts unpublished
products; the endpoint returns 164 and that is what is walked. It fits in one
page.

### Music type gate: `Records`

`product_type` says what kind of thing a product is, never what it is pressed
on:

| `product_type` | Products | What it holds |
| --- | --- | --- |
| `Records` | 118 | vinyl, CD and cassette products; the vinyl ones carry CD and cassette variants beside the pressings |
| `Merch` | 43 | shirts, hoodies, pins, stickers |
| `Books` | 3 | books |

`_MUSIC_TYPES` admits `Records`, case-insensitively, and nothing else.
Enumerated rather than matched negatively so that a non-music type the store
might add stays out by default. A music type it might add is a silent scope
loss the taxonomy guard cannot see; that is the safer direction.

### Artist and title: the quoted product title

`vendor` is always a label: `Iodine Recordings` on 153 products, `Hydra Head
Records` on 7 and `Man Alive Creative` on 4. It never names the artist. The
tags carry the artist, but beside the album's own name, the label's, the
distro label's, the format tags and the housekeeping tags, in Shopify's
alphabetical order, with nothing marking which is which (`['Botch', 'Distro',
'format:cd', 'Hydra Head Records', 'Unifying Themes Redux']`). So the tags
are no artist source either.

The product title is. Every record but one is written `Artist 'Album'`:
straight single quotes on nearly all of them, double quotes on two
(`Gameface "All My Friends"`, `Love Letter "Everyone Wants Something
Beautiful"`), no typographic quotes anywhere. Some carry an edition after the
closing quote:

```
Piebald 'If It Weren't For Venetian Blinds It Would Be Curtains For Us All' Deluxe Edition
Piebald 'We Are The Only Friends We Have' Standard Edition
Quicksand 'Slip' (Deluxe)
Quicksand 'Manic Compression' Deluxe Book
The Saddest Landscape 'Alone With Heaven' Deluxe 2xLP
Candy Hearts 'You Could Be Anyone' (Rarities 2010–2016)
```

The quotes are found **structurally, not by character class**: the opening
quote must follow whitespace and the closing one must precede whitespace or
the end of the title. This is what a quote class cannot do here, because
both halves carry apostrophes of their own — `Her Head's on Fire 'Am I Not
Your Girl?'` on the artist side, `New Forms 'Nothing's Sacred Anymore'`,
`Piebald 'If It Weren't For …'`, `Stephen Brodsky 'Stephen Brodsky's Octave
Museum'` and `Shai Hulud 'Just Can't Hate Enough'` on the album side. A
class-based regex (`deathwishinc.py`'s shape) reads the `'s` in "Head's" as
the opening quote and credits "Her Head" with an album called "s on Fire".
Typographic quotes are accepted on both sides though no live title uses them.

The edition after the closing quote **stays on the album** (`Slip (Deluxe)`,
`Alone With Heaven Deluxe 2xLP`): the store sells each edition as a separate
product with its own pressings, so the rows need to read differently, and the
library match behind the Store tab's Collection and Wantlist filters is
exact-or-prefix-with-space, which `Slip (Deluxe)` still satisfies for a
library "Slip". The vendor is never in the title, so `strip_vendor_prefix` is
not used.

A `Records` product whose title quotes nothing is skipped rather than
credited to the label, because a row credited to "Iodine Recordings" can
never match a Discogs release. The one live case is the subscription. A
split (`Garrison & Orange Island 'Songs From A Central Massachusetts Mill
Town'`) is credited exactly as the title writes it.

### Format gate: two layers

**The product layer is positive and reads tags.** The store tags every record
`Vinyl` and every product with a `format:` tag naming its media:

| Tag | Products | Reads as |
| --- | --- | --- |
| `Vinyl` | 101 | vinyl |
| `format:12"` | 84 | vinyl |
| `format:lp` | 40 | vinyl |
| `format:2xlp` | 11 | vinyl |
| `format:7"` | 12 | vinyl |
| `format:3xlp` | 1 | vinyl |
| `format:cd` | 25 | not vinyl |
| `format:cassette` | 17 | not vinyl |
| `format:merch`, `format:book` | 43, 5 | not vinyl |

Both `Vinyl` and a vinyl-valued `format:` tag are read, either one admitting
the product, because three records carry only the `format:` tag: the two
filed under the store's newer `artist:[…]`/`album:[…]`/`channel:iodine`
taxonomy (`Hundreds of AU 'Life In Parallel'`, `Stretch Arm Strong 'Free At
Last'`) and `Quicksand 'Slip' (Deluxe)`. No live record carries `Vinyl`
without a `format:` tag, but the union costs nothing and survives either
taxonomy going away. The bare `LP` tag is not read: on the live catalog it
never appears without `Vinyl`, and it names a format rather than a medium.
The `format:` value is matched with the vinyl vocabulary the siblings use
(`\d*[x×]?lps?`, `vinyls?`, `picture discs?`, an inch marker), so
`format:10"` or `format:4xlp` would be admitted the day the store presses
one.

Replayed over the live catalog, the product layer admits every record with a
pressing in its variants and rejects every CD-only and cassette-only one,
with no misfire in either direction. No non-`Records` product carries a
vinyl tag.

**The variant layer is negative.** Unlike `matadorrecords.py`'s positive gate
on a whole-catalog walk, this one can only be negative, because a variant
title here is the pressing's colour and often nothing else: `Coke Bottle`,
`Silver`, `Blood and Paper Stripes`, `Noise Cult Splatter`, `Olive Green`,
`Heart + Fire Smash` — 23 of the live pressings carry no format word at all.
The product layer has already said the product is a record, so a variant is
a pressing unless its title names a different medium. The check order
follows `spv.py`: a vinyl word decides first, then a merch word, then an inch
marker, then the non-vinyl media, and a title matching none of them is
admitted. So `Black 12" Vinyl + DVD` and `12" Vinyl + DVD` are records with
an extra (the vinyl word decides), `12" + DVD` would be too (the inch marker
decides before the media word), and a `12" x 12" Poster` is a measurement
rather than a format claim (the merch word decides before the inch marker).
Over the live vinyl-tagged records the layer rejects exactly `CD`, `Cassette
Tape`, `Cassette` and `CD / Standard CD`, and admits every pressing.

A record whose product layer passes but whose variants all name another
medium yields nothing; on the live catalog there is none.

### Shopify's placeholder variant

Two products carry `Default Title`, Shopify's placeholder for a product with
exactly one variant (`Quicksand 'Slip' (Deluxe)` and `Quicksand 'Slip' Deluxe
Book`). It names no pressing, so a row built on it carries the album title
alone, as `hammerheart.py` and `carparkrecords.py` do. Every other row appends
its variant title.

### Identity: the pressing is appended on every row that names one

`item_key` is `sha256(artist|title|url)`, and every pressing of a product
shares all three, so the variant title has to be part of the row's title for
the rows to be distinct. It is appended **on every row**, not only when the
product has more than one variant, for the reason `matadorrecords.py`
records: nearly every product here is multi-variant, because its CD or
cassette sits beside its pressings, and keyed on the variant count a vinyl
row's title would flip the day the CD went out of print, re-keying the row
and orphaning the listings, judgments and saves hanging off the old identity
over a change to a sibling that never yielded. The placeholder is the one
exception, and a safe one: Shopify only issues it for a single-variant
product, so a sibling appearing would replace the placeholder with a real
variant title and re-key the row either way.

Variant titles are whitespace-collapsed before use, so a double space inside
one cannot carry into the row's identity.

### Availability: the `available` flag; no pre-order bypass

Availability reads Shopify's `available` flag and nothing else. The store's
`preorder` tag marks pre-orders and is used for the ` (Pre-Order)` title
suffix only. Every live pre-order reports `available=True` on the pressing
that is for sale, and the sold-out colour beside it (`Her Head's on Fire 'Am
I Not Your Girl?'`'s Noise Cult Splatter, `There Were Wires 'Vessel'`'s two
gone colours) is gone allocation whether or not the product is tagged. Same
call as `matadorrecords.py` and `rhino.py`, and the opposite of
`deathwishinc.py`'s bypass.

### Variants: per-colour images

240 of the 246 variants on the `Records` products carry a `featured_image` of
their own, so `resolve_cover_image` returns the pressing's own image where
the store has one and falls back to the product image otherwise. Every record
has at least one product image.

### Price and currency

`meta.json` reports `"currency":"USD"` and the store ships from Haverhill,
Massachusetts, so `USD` is hardcoded as on every sibling. Live prices run
$3.00–$99.99 across the yielded rows, and every row carries one. `_price`
rejects booleans before `float()` and non-finite or non-positive values after
it, the guard shape the siblings converged on.

### Drift guards

`db.replace_stock_items()` DELETEs this crawler's previous snapshot before
inserting, and `_sync_stock` only skips that call when the crawl **raised** —
a completed-but-empty walk is destructive where a raise is inert. Each guard
names a distinct way the payload can stop carrying what this crawler reads:

| Guard | Fires when | Drift it names |
| --- | --- | --- |
| `products_seen == 0` | the collection returns nothing | `all` removed, or the endpoint changed shape |
| `records_seen == 0` | no product carries the `Records` type | the type taxonomy was renamed wholesale |
| `artist_ok == 0` | no `Records` product's title is of the form `Artist 'Album'` | the store renamed its records (`Artist - Album`, say) |
| `vinyl_seen == 0` | no `Records` product with an artist carries a vinyl tag *and* a variant the gate admits | the format moved out of the tags, or the variants vanished |
| `not yielded and identity_missing` | the walk produced no rows *and* some product that could have yielded one has a blank or absent `handle` | the identity field vanished or was renamed store-wide |
| `not yielded and unreadable_stock` | the walk produced no rows *and* some product that could have yielded one had a record variant with no boolean `available` | the availability field vanished, was renamed, or changed type |
| `yielded and not priced` | rows came through and *none* of them carries a price | the `price` field vanished or changed type store-wide |

The tallies are **nested**, not independent: a product counts toward
`artist_ok` only if it passed the type gate, toward `vinyl_seen` only if it
also has an artist, and toward `unreadable_stock` only if it also has a
record variant. A row needs all of those on one product, so only such a
product's stock readability says anything about an empty result; tallied
independently, a subscription tagged `Vinyl` and a record whose tags were
lost would each satisfy one guard while neither can yield.

The field tallies are taken **before** the availability filter, so a sold-out
product still counts toward every one of them; `yielded` and `priced` are
necessarily counted after it, which is why the two guards reading them are
each conditioned on a second tally rather than on emptiness alone — a shelf
that has simply sold out is empty legitimately, and that is the one case
where an empty result is the truth.

Readability is judged over the record variants only, with every() rather
than any(), on `matadorrecords.py`'s reasoning: a sold-out LP beside a CD
whose flag is junk is a sold-out LP, and a readable-False black pressing must
not vouch for a coloured pressing carrying the string `"false"`. The
per-variant filter admits a variant on the literal `True` and nothing else,
because `"false"` is truthy and a falsiness test would publish a sold-out
record as in stock.

`handle` is identity, not display: the URL is built from it and `item_key`
hashes the URL, so a product missing it is skipped rather than emitted under
a fresh identity, and the skip is counted on the same empty-outcome gate as
the stock guard. The title needs no separate identity guard here: a blank
title has no artist and is already counted by the artist guard.

### Fields

| Field | Source |
| --- | --- |
| `artist` | the part of `product.title` before the quoted album |
| `title` | the quoted album plus any edition after the closing quote, `+ " (Pre-Order)"` when tagged, `+ " — {variant title}"` unless the variant is the placeholder |
| `format` | `"Vinyl"`, hardcoded |
| `price` | `variant.price`, guarded; `None` when unusable |
| `currency` | `"USD"`, hardcoded |
| `url` | `{base_url}/products/{handle}` |
| `cover_image_url` | `resolve_cover_image(product, variant)` |

## Verification

Replayed `Crawler._items()` over the fully-cached live catalog: 164 products
walked, 118 pass the type gate, 117 resolve an artist (the subscription does
not), 104 carry a vinyl tag and a variant the gate admits, all 104 are
readable, and **129 rows yielded** — zero `item_key` collisions, zero blank
artists or titles, zero whitespace contamination, zero malformed URLs, zero
missing covers and zero null prices. Five rows carry the pre-order suffix.
One row is a placeholder row (`Slip (Deluxe)`). The 13 records with an
artist that yield nothing are the CD-only and cassette-only products, every
one tagged accordingly.

Unit tests are respx-mocked against captured products, following the sibling
crawler test files. Each guard and rule was confirmed to **bite** rather than
assumed, by mutating the crawler and checking that the tests fail: dropping
the type gate, reading the vendor as the artist, reading availability by
truthiness, dropping the placeholder rule, dropping the tag gate, reading
only the `Vinyl` tag, turning the variant gate positive, dropping the merch
check, dropping the inch check, dropping the edition from the title, matching
the quotes by character class instead of structurally, keying the pressing on
the variant count, dropping the pre-order suffix, admitting a boolean price,
dropping the identity skip, weakening readability to any(), tallying the
artist on every product, tallying stock on any variant, and dropping each
guard in turn. Every mutation failed at least one test.

## Crawl citizenship and `robots.txt` compliance

`iodinerecords.com/robots.txt` is Shopify's current standard file: `User-agent:
*` with `Allow: /`, a `Disallow` list covering `/admin`, `/cart`, `/checkout`,
`/orders`, `/account`, `/search`, `/recommendations/products` and filtered or
sorted collection URLs (`/collections/*sort_by*`, `/collections/*+*`,
`/collections/*filter*&*filter*`), and no `Crawl-delay`.
`/collections/all/products.json` matches no `Disallow` rule. The file's
preamble points agents at a UCP/MCP endpoint for cart and checkout; this
crawler reads the catalog only and never touches either.

Pacing is the pipeline's, not this crawler's: `shopify_catalog.iter_products()`
routes every page through `catalog_http.get_with_retry()`, which applies the
configured `crawl_delay_seconds` between pages and never retries a 429. The
catalog fits in one page, so a full walk is two requests.

## Queue fan-out

Each yielded row becomes a `stock_items` row and, via
`enqueue_crawl_queue_for_stock_item`, one `crawl_queue` target that the enabled
release crawlers then price. Nothing here selects crawlers — `crawlers.enabled`
is resolved at dispatch by `_drain_one_batch`, per this repo's per-item fan-out
invariant.

## Registration

Automatic. `seed_bundled_crawlers()` copies every file in `backend/crawlers/`
and registers it by its `site_name` on each boot; the `genre_summary`
attribute surfaces as the hover tooltip on the store link in Settings, and
`genre: "punk"` places it in the Store tab's genre filter.
