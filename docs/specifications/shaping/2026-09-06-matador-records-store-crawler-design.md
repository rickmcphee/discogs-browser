# Matador Records store crawler design

**Status:** implemented
**Date:** 2026-09-06
**Store:** https://matadorrecords.com/pages/store

## Problem

Matador Records is the New York independent label behind Pavement, Yo La
Tengo, Interpol, Spoon, Cat Power, Belle and Sebastian, Kurt Vile, Queens of
the Stone Age, Snail Mail, Julien Baker, Lucy Dacus and Mdou Moctar, among
others. Its official store is not covered by any bundled crawler, so none of
that stock reaches the Store tab and none of it is matched against a user's
library under the Store tab's Collection and Wantlist filters.

The store runs Shopify (`powered-by: Shopify`,
`matadorrecordsprod.myshopify.com`), so `shopify_catalog.iter_products()`
already implements the transport. What needed deciding was different from the
sibling Shopify stores: this one keeps the **format on the variant rather than
the product**, credits a handful of releases to the label instead of the artist,
and writes its variant titles as free text. Each of those is a departure the
plugin has to handle, and each is grounded below.

## Scope

**In:** a `catalog`-type plugin, `backend/crawlers/matadorrecords.py`, walking
the store's published catalog over the public `products.json` endpoint and
yielding in-stock vinyl as stock items.

**Out:**

- Bundles. The store leaves them untyped and sells them as one product whose
  variants are combinations (`Nothing - Red LP / Spiral - White Vinyl Dbl LP /
  Psychic - CD`), so there is no single release to key a row on. See *Music
  type gate*.
- Two fan-club LPs the store also leaves untyped (`Julien Baker & Torres - Send
  A Prayer My Way`, `Spotify Fans First - Darkside Nothing`). Accepted scope
  loss: the type gate is what keeps the bundles out, and these share the blank
  type with them.
- CD, cassette, digital (`DMD Album`) and DVD variants of the music products.
- Any release-type (per-library-item) crawling of this store. This is a catalog
  source; the Store tab's own crawlers price its items.

## Technical grounding

Everything below was gathered live on 2026-09-06 by fully paginating the
store's `all` collection and caching the payload.

### Collection choice: `all`

The store publishes no format or music collection at all. Its
`collections.json` is one collection per artist (`pavement-collection`,
`yo-la-tengo-collection`, …, most of them empty) plus `featured-products`,
`bundles-1` and `boygenius-in-focus`. So there is no narrower shelf to walk, and
Shopify's built-in `all` collection is the source, as it is for `rhino.py`,
`killrockstars.py`, `saddlecreek.py` and their siblings.

`all` returned 282 products, which agrees with the root `products.json` (282)
and with `meta.json`'s `published_products_count` (282). `collections.json`
reports `all` as holding 407, but that figure counts unpublished products; the
endpoint returns 282 and that is what is walked.

### The format lives on the variant, not the product

`product_type` here says what *kind of release* a product is, never what it is
pressed on:

| `product_type` | Products | What it holds |
| --- | --- | --- |
| `Album` | 213 | CD, LP, coloured LP, cassette and digital variants side by side |
| `EP` | 20 | `CDEP` beside `12" EP` |
| `Single` | 9 | `7"` singles |
| `Merch` | 27 | shirts, hats, a tote |
| *(blank)* | 13 | bundles, plus the two fan-club LPs |

A product typed `Album` is `Perfecth` with variants `Perfecth - Deluxe  LP`,
`Perfecth - Standard LP` and `Perfecth - CD`. So the type field is a **music
gate** and nothing more, and the format gate has to read each variant.

**Music type gate.** `_MUSIC_TYPES` admits `Album`, `EP` and `Single`,
case-insensitively, and nothing else. Enumerated rather than matched
negatively (`not Merch`) so that a non-music type the store might add
(`Poster`, `Book`) stays out by default. The cost is that a *music* type the
store might add is a silent scope loss the taxonomy guard cannot see; that is
the safer direction, since the alternative is publishing a poster as a record.
The blank type is what carries the bundles, and the gate is what keeps them out
— their variant titles are full of LPs, so the format gate on its own would
admit every combination.

### Format gate: the variant title's descriptor

Every music variant title names its format in a trailing descriptor. Over the
541 variants of the 242 music products, the descriptors split cleanly:

- **Records:** `LP`, `Dbl LP`, `dbl LP`, `2X LP`, `2XLP`, `2xLP`, `3LP`,
  `4xLP`, `1.5 LP`, `120 gram 1.5XLP`, `120g LP`, `Black Vinyl LP`,
  `Black Vinyl Dbl LP`, `Clear Vinyl LP`, `Picture Disc LP`, `12"`, `12" EP`,
  `Standard Black Vinyl 12"`, `7"`, `7" Single`, `Dbl 7"`, `7" Boxset`,
  `3x12" Boxset`, `Vinyl Boxset`, `Blue Vinyl`, `Opaque White Vinyl`,
  `Anniversary Red Vinyl`, `LP + 7"`, `LP + Signed Print`,
  `Black Vinyl LP + bumper sticker`, `Deluxe Black Vinyl 2X LP + Bonus CD`,
  `White Vinyl Dbl LP (VINYL RECORD)`, …
- **Not records:** `CD`, `CDEP`, `CD EP`, `2X CD`, `2XCD`, `Dbl CD`,
  `Dbl CD (COMPACT DISC)`, `4CD Boxset`, `Cassette`, `CS Album`, `DMD Album`
  (digital), `DVD`.

`_is_vinyl()` is a **positive** gate, unlike `spv.py`'s negative one, because
the source is the whole catalog rather than a vinyl shelf: an unrecognised
descriptor is a CD until it says otherwise. It admits a descriptor on a vinyl
word (`\d*[x×]?lps?`, `vinyls?`, `picture discs?`) or an inch marker
(`7"`, `12"`, `3x12"`, `10 inch`), with a merch check between the two in the
order `spv.py` established: a dimension is not a format claim, so
`12" x 12" Poster` must not read as a record, while a merch word beside a
vinyl word (`LP + Signed Print`) is a record with an extra. The vocabulary
follows `spv.py`'s, including its `s?` on every noun (`\blp\b` cannot see the
"LPs" in `2 LPs + CD`) and its disc-count prefix (`2xLP` has no word boundary
between count and noun). The lookbehind on the LP alternative is what keeps
"Help" and "Alps" out.

Replayed over the live catalog, the gate admitted every descriptor in the
first list and rejected every one in the second, with **no misfire in either
direction**. The one product-level oddity is `Psychic Live`, typed `Album` with
a single `DVD` variant; it yields nothing, correctly.

### Descriptor derivation

The variant title repeats the album name and then says the format, so the
descriptor is what follows the album name when the variant starts with it.
The prefix match is case-insensitive, reads the store's straight and
typographic apostrophes as one (`If You're Feeling Sinister` against
`If You’re Feeling Sinister - Black Vinyl LP`), decodes an HTML entity in the
variant first (`Signals, Calls &amp; Marches`), and ends at a word boundary so
an album called "Go" cannot claim "Gone Glimmering LP". A parenthesised
edition on the product title is tried three ways, because the variant writes
it in any of them:

```
Valentine (Demos)                            / Valentine Demos - 12" EP          -> 12" EP      (unwrapped)
Electric Version (20th Anniversary Edition)  / Electric Version LP               -> LP          (dropped)
Set and Setting (25th Anniversary Edition)   / Set and Setting 25th Anniversary Edition - LP -> LP (unwrapped)
Signals, Calls & Marches (The Standard Edition) / Signals, Calls &amp; Marches THE STANDARD EDITION LP -> LP
The Greatest: Slipcase Edition               / The Greatest 120 gram LP          -> 120 gram LP (colon edition dropped)
```

When the variant does not start with the album name at all — the store
re-spells it (`6 Feet Beneath The Moon` against `Six Feet Beneath The Moon -
Dbl LP`), drops a credit (`Body/Head - Coming Apart` against `Coming Apart -
CD`) or adds one (`Valentine` against `Snail Mail Valentine - Pink Glass LP`)
— the text after the last `" - "` is the descriptor. Failing that too, the
whole variant title is; on the live catalog that path reaches only CDs the gate
rejects anyway. Internal whitespace is collapsed, because the store's own
`Deluxe  LP` would otherwise carry its double space into the row's identity.

Over the live catalog the prefix path resolves 480 variants, the last-dash
path 52, and the whole-title path 9, and every one of the 220 yielded rows
carries a clean descriptor.

### Identity: the descriptor is always appended

`item_key` is `sha256(artist|title|url)`, and every pressing of a product
shares all three, so the descriptor has to be part of the title for the rows
to be distinct — that much is the same as every sibling. The departure is that
it is appended **on every row**, not only when the product has more than one
variant.

Nearly every product here is multi-variant, because its CD sits beside its LP.
Keyed on the variant count, a vinyl row's title would read `Adore Life` while
the CD is listed and `Adore Life — LP` the day the CD goes out of print, and
that flip re-keys the row and orphans the listings, judgments and saves hanging
off the old identity — over a change to a *sibling* that never yielded. The
descriptor is the pressing, a property of the variant itself, so the row's
identity depends on nothing but its own variant. The library match behind the
Store tab's Collection and Wantlist filters is unaffected: `_library_release_match_sql` matches a catalog title as an
exact-or-prefix-with-space, and `Adore Life — LP` starts with `Adore Life `.

### Artist: `vendor`, unless it is the label

`vendor` is the artist on all but a handful of products. On 11 it is
`MatadorRecordsProd` (the store's myshopify name) and on 8 it is
`Matador Records`, and the artist is then in `tags`, alongside the store's
housekeeping tags. Every live tag that is not an artist credit is one of
`migrated`, `preorder`, `sale`, `checkbox` and `Matador Merch`; those are
excluded, and the **first** credit that remains on a label-vendored product is
the artist. First rather than all of them joined: the catalog keeps a release's
primary artist alone (`discogs.parse_release` reads `artists[0]`) and
`_library_release_match_sql` is an exact artist equality, so a joined
`Jay Reatard / Sonic Youth` could never match a library record. Shopify
serialises tags alphabetically, which is the only order the payload offers; a
split whose Discogs primary artist sorts second will not match its library
record, but is still credited to an artist who is on it. One live product is a
split (`Hang Them All / No Garage`, tagged `Jay Reatard` and `Sonic Youth`),
and it is sold out.

**Amendment (2026-09-06, review round 1):** the first draft joined every
credit with ` / ` and called that the Discogs convention for a split. Copilot's
review pointed out that the catalog side stores only the primary artist and
the match is exact, so the joined value could never match; the rule above is
the correction.

This recovers real releases rather than edge cases: Gang of Four's `77-81`
box, Majical Cloudz's `Are You Alone?` and `Impersonator`, Tobias Jesso Jr.'s
`Goon`, Belle and Sebastian's `The Boy With The Arab Strap` and `Days of the
Bagnold Summer`, Bettie Serveert's `Palomine`, Lower's `I'm A Lazy Son…`. A
label-vendored product with no credit left after the exclusions is skipped
rather than credited to the label, because a row credited to "Matador
Records" can never match a Discogs release. The remaining label-vendored
products are merch, which the type gate drops first.

A **blank** vendor is no artist, and is not a cue to read the tags: only the
label's own name in the field says the credit is in the tags. Every product
carries tags, so reading them on a blank vendor would let a store-wide loss of
`vendor` credit rows from whatever tag sorts first and slip past the
artist-source guard; a blank-vendor product is skipped, and a catalog of them
raises. *(Amendment, 2026-09-06, review round 4: the first draft fell through
to the tags on a blank vendor as well.)*

A tag never overrides a real vendor. Real-vendor products carry other credits
in their tags (`Stephen Malkmus & The Jicks` on Pavement products, `Archy
Marshall` on King Krule's), and the vendor is the right answer there.

### Title: the product title, with the shared exact-case `" - "` strip

The store keeps the artist out of the product title everywhere but one
product, `Body/Head - Coming Apart`, where `strip_vendor_prefix` is a live
transformation (`Coming Apart — Dbl LP`). Self-titled albums (`Algiers`,
`Interpol`, `boygenius`) carry no separator and are left alone.

One product title already carries its format, dash-separated
(`I'm A Lazy Son...But I'm The Only Son - 12" EP`, whose variant title is the
same string). Composing `{album} — {descriptor}` naively would name the format
twice, so a terminal ` - {descriptor}` on the album is dropped before the
descriptor is appended, and the row reads `I'm A Lazy Son...But I'm The Only
Son — 12" EP`. Only that dash-separated terminal form is stripped: an album
that merely ends in the same words, or is nothing but them, is left alone.

**Amendment (2026-09-06, review round 2):** the first draft appended the
descriptor unconditionally and accepted the duplicated format on that one
sold-out product as rare; Copilot's second round pointed out it would surface
the day the product restocks. The strip above is the correction.

### Availability: the `available` flag; no pre-order bypass

Availability reads Shopify's `available` flag and nothing else. The store's
`preorder` tag marks pre-orders and is used for the ` (Pre-Order)` title suffix
only: every live pre-order reports `available=True`, so an unavailable one is
gone allocation whether or not it is tagged. Same call as `rhino.py`,
`udiscovermusic.py` and `hammerheart.py`.

### Variants: per-colour images

Unlike Rhino's single-variant catalog, most variants here carry a
`featured_image` of their own (490 of the 541 music variants), so
`resolve_cover_image` returns the pressing's own image where the store has
one and falls back to the product image otherwise. Every music product has at
least one product image.

### Price and currency

`meta.json` reports `"currency":"USD"` and the store ships from New York, so
`USD` is hardcoded as on every sibling. Live prices run $6.78–$148.73 across
the yielded rows, and every row carries one. `_price` rejects booleans before
`float()` and non-finite or non-positive values after it, the guard shape the
siblings converged on.

### Drift guards

`db.replace_stock_items()` DELETEs this crawler's previous snapshot before
inserting, and `_sync_stock` only skips that call when the crawl **raised** —
a completed-but-empty walk is destructive where a raise is inert. Each guard
names a distinct way the payload can stop carrying what this crawler reads:

| Guard | Fires when | Drift it names |
| --- | --- | --- |
| `products_seen == 0` | the collection returns nothing | `all` renamed or removed, or the endpoint changed shape |
| `music_seen == 0` | no product carries `Album`, `EP` or `Single` | the type taxonomy was renamed wholesale |
| `artist_ok == 0` | no music product resolves an artist from `vendor` or `tags` | the artist source moved |
| `vinyl_seen == 0` | no music product with an artist has a variant whose title reads as vinyl | the format moved out of the variant title (into an option, a metafield) |
| `not yielded and identity_missing` | the walk produced no rows *and* some product that could have yielded one has a blank or absent `title` or `handle` | an identity field vanished or was renamed store-wide |
| `not yielded and unreadable_stock` | the walk produced no rows *and* some product that could have yielded one had a vinyl variant with no boolean `available` | the availability field vanished, was renamed, or changed type |
| `yielded and not priced` | rows came through and *none* of them carries a price | the `price` field vanished or changed type store-wide |

The tallies are **nested**, not independent: a product counts toward
`artist_ok` only if it passed the music gate, toward `vinyl_seen` only if it
also has an artist, and toward `unreadable_stock` only if it also has a vinyl
variant. A row needs all of those on one product, so only such a product's
stock readability says anything about an empty result; tallied independently,
a product with an artist but no readable flag and another with a readable flag
but no artist would each satisfy one guard while neither can yield.

The field tallies are taken **before** the availability filter, so a sold-out
product still counts toward every one of them; `yielded` and `priced` are
necessarily counted after it, which is why the two guards reading them are each
conditioned on a second tally rather than on emptiness alone — a shelf that has
simply sold out is empty legitimately, and that is the one case where an empty
result is the truth.

Two things are specific to this store's shape:

- **Readability is judged over the vinyl variants only**, because they are the
  only ones that could have yielded. A sold-out LP beside a CD whose flag is
  junk is a sold-out LP, and the walk completes empty as the truth; a CD's
  clean flag, conversely, says nothing about whether the LP's emptiness can be
  trusted. Within those variants the quantifier is every(), not any(): a
  readable-False black pressing must not vouch for a coloured pressing carrying
  the string `"false"`.
- **`variants` vanishing is named as descriptor drift, not stock drift.** The
  format is read off the variant, so a catalog with no readable variants is a
  catalog with no readable format, and `vinyl_seen` is the tally that reaches
  zero. A raise either way.

The per-variant filter's strictness is independent of the guards: it admits a
variant on the literal `True` and nothing else, because `"false"` is truthy and
a falsiness test would publish a sold-out record as in stock. Losing a row is
the safe direction; offering for sale a record that is not for sale is not.

**`title` and `handle` are identity, not display.** `item_key` is
`sha256(artist|title|url)` and the URL is built from `handle`, so a product
missing either would not be emitted with degraded text — it would be emitted
under a *fresh* identity, and `replace_stock_items()` would then replace the
old row with one that nothing saved or judged against can find. Such a product
is skipped instead, and the skip is counted: it is nested with the stock tally
(a product is counted as identity-less before its availability is even read),
and the guard fires on the same empty-outcome gate as the stock guard, because
a store-wide loss of either field leaves every product skipped and the walk
looking sold out. An isolated identity-less product among rows that did come
through stays a skipped row, on the same reasoning as an isolated unreadable
one.

**Amendment (2026-09-06, review round 3):** the first draft deliberately left
`title` and `handle` unguarded as "degraded data rather than a deleted
snapshot", following `rhino.py`'s spec. Copilot's third round pointed out both
fields feed `compute_item_key`, so their loss is identity drift, not degraded
presentation. The skip and the guard above are the correction; the row in the
guard table is new.

### Fields

| Field | Source |
| --- | --- |
| `artist` | `product.vendor`, or the first non-housekeeping tag when the vendor is the label |
| `title` | `strip_vendor_prefix(product.title, artist)`, `+ " (Pre-Order)"` when tagged, `+ " — {descriptor}"` always |
| `format` | `"Vinyl"`, hardcoded |
| `price` | `variant.price`, guarded; `None` when unusable |
| `currency` | `"USD"`, hardcoded |
| `url` | `{base_url}/products/{handle}` |
| `cover_image_url` | `resolve_cover_image(product, variant)` |

## Verification

Replayed `Crawler._items()` over the fully-cached live catalog: 282 products
walked, 242 pass the music gate, all 242 resolve an artist, 236 carry at least
one vinyl variant, and **220 rows yielded** — zero `item_key` collisions, zero
blank artists or titles, zero whitespace contamination, zero malformed URLs,
zero missing covers and zero null prices. Six rows carry the pre-order suffix.
Five rows take their artist from tags (`Gang of Four`, `Majical Cloudz` ×2,
`Tobias Jesso Jr.`, `Belle and Sebastian`); the other label-vendored records
are sold out. The six music products that yield nothing are the DVD and five
CD-only titles. Every qualifying product is readable, so the stock guard
cannot fire on live data.

Unit tests are respx-mocked against captured products, following the sibling
crawler test files. Each guard and rule was confirmed to **bite** rather than
assumed, by mutating the crawler and checking that only the intended tests
fail: dropping each guard in turn, reading availability by truthiness,
weakening readability to any() or to a presence check, turning the format gate
negative, dropping the merch check, dropping the word boundary on the album
prefix, treating the label vendors as artists, reading housekeeping tags as
credits, skipping the HTML decode, dropping each edition rewrite, skipping the
whitespace collapse, keying the descriptor on the variant count, keeping junk
variants, dropping the pre-order suffix, dropping the vendor strip, admitting a
boolean price, and widening the music types. Every mutation failed at least one
test, and the tests it failed were the ones written for it.

## Crawl citizenship and `robots.txt` compliance

`matadorrecords.com/robots.txt` is Shopify's standard file: `User-agent: *`
with a `Disallow` list covering `/admin`, `/cart`, `/checkout`, `/orders`,
`/account`, `/search`, `/recommendations/products` and filtered or sorted
collection URLs (`/collections/*sort_by*`, `/collections/*+*`), and no
`Crawl-delay`. `/collections/all/products.json` matches no `Disallow` rule.

Pacing is the pipeline's, not this crawler's: `shopify_catalog.iter_products()`
routes every page through `catalog_http.get_with_retry()`, which applies the
configured `crawl_delay_seconds` between pages and never retries a 429. The
catalog fits in two pages, so a full walk is three requests.

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
`genre: "rock"` places it in the Store tab's genre filter.
