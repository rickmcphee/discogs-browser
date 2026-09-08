# Bella Union crawler design

**Status:** implemented
**Date:** 2026-09-08
**Store:** https://bellaunion.com/collections/all

## Problem

Bella Union's own webstore carries the label's catalog — Beach House,
Father John Misty, John Grant, Explosions In The Sky, Ezra Furman, Marissa
Nadler, Midlake, The Flaming Lips, Spiritualized, The Fall, Susanne Sundfør,
Lanterns On The Lake, Penelope Isles — plus a shelf of Cocteau Twins and
other 4AD reissues signed by Simon Raymonde that exists nowhere else. None of
it is covered by a bundled crawler, so none of it reaches the Store tab, and
none of it is matched against a user's library under the Store tab's
Collection and Wantlist filters.

The store runs Shopify (`466daf-6.myshopify.com`), so
`shopify_catalog.iter_products()` already implements the transport. What
needed deciding was how to read a row out of the payload, and the payload
splits the work differently from the Shopify siblings: the artist is
unambiguous (the product title says `Artist - Album` on every record but two)
while *what a product is* is not (the store's own `product_type` is blank on
four of its records and on all of its merch alike). So most of the design
below is about deciding, per product and then per variant, which of the
store's things are records.

## Scope

**In:** a `catalog`-type plugin, `backend/crawlers/bellaunion.py`, walking the
store's `all` collection over the public `products.json` endpoint and
yielding in-stock vinyl as stock items.

**Out:**

- The store's non-vinyl formats. Confirmed live (2026-09-08) by fully
  paginating `all`: CDs sit beside the records as sibling variants of the
  same product, alongside two cassettes.
- The store's merch — a cap, a tote bag, a football jersey and four
  T-shirts, all of which it tags `Merch`.
- Record-and-garment bundles (`Black Vinyl + T Shirt Bundle`,
  `Vinyl / "Tie" T Shirt Bundle + Signed Insert`). A bundle's price is not
  the record's; see "A garment joined to a record is a bundle" below.
- Two records the store did not title to its own convention. See "Two
  records the parse does not read".
- Any release-type (per-library-item) crawling of this store. This is a
  catalog source; the Store tab's own crawlers price its items.

## Technical grounding

Everything below was gathered live on 2026-09-08 by fully paginating the
store's `all` collection at two page sizes, and by reading `meta.json`,
`cart.js`, `collections.json`, the `merch`, `upcoming-releases` and
`all-minus-pre-orders` collections, and `robots.txt`. The payloads were
cached and every rule below was replayed against them.

`robots.txt` is Shopify's standard storefront file and allows crawling of
public product and collection JSON; it asks only that agents not complete
checkout, which this crawler never does.

Prices are GBP: `meta.json` reports `"currency":"GBP"` and
`"money_format":"£{{amount}}"`, and `cart.js` agrees.

### Collection choice: `all`

The request named `/collections/all`, which is Shopify's built-in
all-products collection. It is also the only choice: `collections.json`
lists the store's collections and there is no vinyl, format or music shelf
among them — they are one per artist, plus `merch`, `sale`, `best-sellers`,
`frontpage`, `upcoming-releases` and two hand-curated "all" variants.

The walk is stable and complete. Paginating `all` returns 221 published
products on a single page at `limit=250`, and the same 221 product ids come
back at `limit=50` across five pages. `meta.json` independently reports
`published_products_count: 221`.

`collections.json` disagrees, reporting `products_count: 208` for this very
collection. It is wrong in the direction that matters least — the endpoint
that answers with products returns more than the one that answers with a
count — and the walk's own exhaustion is what the crawler treats as the
catalog, which is what `shopify_catalog` already documents for the sibling
stores.

Two collections were checked and rejected as narrower shelves:

- `all-minus-pre-orders` (204 products) is hand-curated, not derived. It
  excludes six of the store's 13 pre-orders and none of the other seven, and
  it also drops nine of the signed Cocteau Twins reissues and two T-shirts.
- `frontpage` (204) is the store's home page, not an inventory.

### The artist and the album both come from the title

`vendor` is never consulted. It names the label rather than anybody who made
the record — `Bella Union` on the catalog, `4AD` on the signed Cocteau Twins
reissues, `Fontana` on one more — so unlike `dongiovannirecords.py` there is
no artist field to prefer, and unlike `matadorrecords.py` there is no subset
of products where it happens to carry a credit.

The product title is the whole source, and it follows one convention:

```
A.A. Williams - Arco
Lanterns On The Lake - Gracious Tide, Take Me Home
Ivor Raymonde - Odyssey: The Sound Of Ivor Raymonde
Beach House - Alien / Lose Your Smile 7"
```

The separator is a whitespace-dash-whitespace run, and the first one splits.
A bare `-` would not do: `Four-Calendar Café`, `Yellow/White Swirl` and
`Emiliana Torrini & The Colorist Orchestra` all carry hyphens that are not
separators. Requiring whitespace on both sides is enough on the live catalog
— every record but the two below carries exactly one such run — and the
`maxsplit=1` is there for a title that ever carries two.

Because one split yields both halves, there is **one** source here where the
sibling crawlers have two. That is why the drift guards below carry a single
`parsed_ok` tally and no co-occurrence tally: there is no second source that
could go dark behind this one, and nothing for a co-occurrence check to say.

### Two records the parse does not read

Two live products are records the store did not write to its own convention:

```
Cocteau Twins  Four-Calendar Café  (signed by Simon Raymonde)
Harold Budd / Elizabeth Fraser / Robin Guthrie / Simon Raymonde: The Moon and
    the Melodies  (Signed by Simon Raymonde)
```

The first substitutes two spaces for the separator; the second a colon. Both
are in stock, so this costs two rows out of the 184 the store could yield.

They are skipped rather than salvaged, and the reason is that each salvage
would be a rule that fires on exactly one product and can only misfire
afterwards. A colon fallback has to be inert on the three albums that
legitimately carry a colon (`Odyssey: The Sound Of Ivor Raymonde`,
`Paradise: The Sound Of Ivor Raymonde`, `1990: The Hidden Years Recording`)
— it would be, since it could only run where the dash is absent — but it
would then read the *next* colon-carrying title with no dash as a credit it
is not. A double-space fallback is worse: the parse collapses whitespace
before splitting, precisely so that `  Midlake  -  Antiphon  ` reads
correctly, and a rule keying on a double space has to run before that
collapse and against titles where a double space is otherwise meaningless
(`Cocteau Twins - Stars and Topsoil  (signed by Simon Raymonde)` has one too).

Skipping also does double duty. It is the second, independent rule that
keeps the store's merch out — none of the seven merch products names an
artist at all — so the merch is excluded by its tag *and* by the parse. Both
lost records are picked up with no code change if the store ever fixes
either title.

### What is a record: a product-level claim, then a per-variant gate

The store sells the CD, the cassette and the record as **variants of one
product**, under one title. So "is this a record" is asked twice: once of
the product, and once of each variant.

**The product-level claim** is `_claims_vinyl()`, and it takes either of two
signals because neither covers the catalog alone:

- `product_type == "Vinyl"` covers 210 of 221 products, but is blank on four
  records (`Conchúr White - Swirling Violets`, `Harp - Albion`,
  `John Grant - The Art Of The Lie`, `SONIKKU - Whirlwind Of Malevolence`)
  — and blank on all seven merch products too, so on its own it would both
  drop real records and read no differently from a shirt.
- A variant naming a record covers those four (each has a `Vinyl` variant),
  but not the records whose only variants are `Default Title`, `Gold
  Edition`, `Silver Edition`, `Deluxe Boxset` or `Limited Edition Boxset` —
  and every one of those is typed `Vinyl`.

Taken together they admit every live record and reject every live
non-record, and they fail in opposite directions, which is the point: a
mis-typed garment has to also name a record in a variant to get through, and
a record with an unrecognised format has to also lose its type.

**The per-variant gate** is `_is_vinyl()`, and it is negative — a bundle or
a garment rejects first, then a record word admits outright, then a word
naming another medium or a bare garment size rejects, and anything else is
admitted on the product's own claim. Negative rather than enumerated so a
format the store adds later stays in by default, which is exactly how every
live `Gold Edition`, `Deluxe Boxset` and `Limited Edition Boxset` is kept:
each was checked and each is a vinyl box (`The Perfect Vision`'s is a 7LP
set; `Once Twice Melody`'s Gold and Silver editions are the box pressings
beside the £11.99 CD).

The gate reads the **descriptor** and never the product title. The live
`A.A. Williams - As The Moon Rests (Signed Print)` is a record whose album
name carries a merch word, and `The Fall - Singles Live Vol.1` sells a plain
pressing and a shirt bundle under one title; scanning the title would drop
the first outright and could only ever answer once for the second.

### The rejecting vocabulary

`_OTHER_MEDIA_RE` covers `cd`, `cassette`, `tape`, `dvd` and `blu-ray`, each
with the optional disc-count prefix the record words carry, since `\bcds?\b`
cannot see the `CD` in `2xCD` — plus `digital`.

It spells cassette `casse+tte`, which is not decoration. The store's live
catalog contains `Casseette`, on `Father John Misty - Mahashmashana`, whose
other variants are records. Under a strict spelling the product claims vinyl,
the typo names no medium the gate knows, the default admits it, and a £6.99
cassette is published as this crawler's `Vinyl` beside the £28.99 pressings
— wrong on the Store tab and wrong under the Cheapest filter.

#### The record-word boundaries have to be Unicode-aware

The gate admits on a record word *before* it reads the rejecting vocabulary,
so a false record-word match publishes a CD at a CD's price under `Vinyl`.
Two ways that happened, both ported from `dongiovannirecords.py`, which
reached them over two review passes of its own:

- **`[a-z]` is ASCII-only, even under `IGNORECASE`.** An accented letter is
  therefore not a letter to it, the left boundary opens, and `éLP CD` matched
  the embedded `LP` and was admitted ahead of its `CD`. `[^\W\d_]` — "a
  letter" to Python's Unicode `\w` — closes it.
- **The inch marker's quote glyph had no right-hand boundary**, which the
  spelled-out `inch` alternative gets free from its `\b`. So `12"CD` read as
  a complete inch marker and was admitted before the `CD` was reached.

`\w` alone is still not enough: it excludes the combining mark categories, so
in decomposed text the character before `LP` in `éLP CD` is the accent rather
than a letter and the boundary opens again — while the precomposed spelling
of the same string is rejected. `_fold_marks` replaces marks with a letter
**for matching only**, so both normal forms of one descriptor decide
identically. It is applied at every matching site, `_claims_vinyl` included:
that runs the same record-word pattern over variant titles, and left unfolded
it would let an embedded `LP` make a non-record claim to be one.

The boundary is applied to the rejecting vocabulary too, and the mirror rule
is deliberate: an embedded medium word must not *reject* either, so
`CaféCD Gatefold` stays a record. A glued inch marker is simply not one, and
the gate falls through to the medium word it was masking. Found in review on
PR #333.

`_MERCH_RE` covers garments and bags: shirts, tees, hoodies, sweatshirts,
crewnecks, longsleeves, tank tops, jerseys, caps, hats, totes and bags. That
is the store's own merch vocabulary, read off its `merch` collection.

It deliberately does **not** cover the small extras the store packages with a
record, and the live prices are the reason:

| Descriptor | Price | Plain pressing |
| --- | --- | --- |
| `Yellow Vinyl + Embroidered Patch` | £23.99 | £23.99 |
| `Black Vinyl + Signed Print` | £19.99 | £22.99 |
| `White Bio Vinyl + Signed Polaroid` | £23.99 | £18.99 |
| `140g Smoky Grey Marble Vinyl + Signed Postcard` | £20.99 | — |
| `Clear Vinyl + Comic (BELLA1292VX)` | £22.99 | £20.99 |
| `Black Vinyl + T Shirt Bundle` | £40.99 | £19.99 |
| `Yellow/White Swirl Vinyl + T Shirt Bundle` | £39.99 | £23.99 |
| `Vinyl / "Tie" T Shirt Bundle + Signed Insert` | £41.99 | £22.99 |

The first five are a record at a record's price with something in the sleeve;
publishing them is right. The last three are roughly double, and publishing
one would put a shirt's price on a record under the Cheapest filter.

### A garment joined to a record is a bundle

Two rules reject those, and both are written down because each is the
other's backstop on a catalog where they happen to agree everywhere:

- `_MERCH_RE` on the descriptor — the garment is named.
- `_BUNDLE_RE`, a bare `bundles?` — every live combo says so as well.

`_BUNDLE_RE` also covers a shape the store does not currently sell but its
siblings do: a `Vinyl Bundle` of several records, whose price is no single
record's.

Both are checked **first**, ahead of the record word that would otherwise
admit `Black Vinyl + T Shirt Bundle` outright on its own `Vinyl`. That
ordering is the whole rule; with it reversed every live shirt bundle is
published.

### A bare garment size is not a pressing

`_GARMENT_SIZE_RE` rejects a descriptor that is nothing but `S`, `M`, `L`,
`XL`, `2XL` or `One Size`. Anchored at both ends, so it can only ever match a
descriptor that names nothing else.

This is a third rule for merch that two others already exclude, and it is
worth its two lines because the two others are the store's own claims about
itself and this one is not. The `Merch` tag and the `Artist - Album` title
both depend on the store keeping its housekeeping straight, and it visibly
does not — four of its records are untyped, one of its cassettes is
misspelled, two of its records are mis-titled. A garment typed `Vinyl` is the
shape this rejects, and nothing else in the payload would then stop it.

### Variants, availability and pre-orders

The store's option is `Format` on the records (`Vinyl`, `Black Vinyl`,
`140g EcoMix Vinyl`, `Vinyl (BELLA1090V)`, `10" Signed Vinyl`, against `CD`
and `Cassette`), `Size` on the garments, and `Title` on the products carrying
Shopify's `Default Title` placeholder.

**The descriptor is appended to every row that names one**, not only on
multi-variant products: a sibling being listed or delisted must not re-title
the surviving rows and orphan the listings, judgments and saves keyed on the
old `item_key`. The placeholder is the one exception, because it names
nothing, and it is admitted only as a product's *sole* variant — on a
multi-variant product it is malformed data, and a row built on it would share
the album, the URL and so the `item_key` with the sole-variant row it would
otherwise be. Fourteen live products carry it, every one of them as its only
variant; nine are the signed Cocteau Twins reissues.

Catalog numbers are kept verbatim where the store writes them into the
descriptor (`Vinyl (BELLA1090V)`, `CD (BELLA950CD)`). Stripping them would be
a transformation applied to some rows and not others, for no gain: the
descriptor is what the store shows the buyer, and the number identifies the
pressing.

**Availability is `variant.available`, the literal `True` only.** Every one
of the 442 live variants carries a real boolean, but the string `"false"` is
truthy, so a falsiness test would publish a sold-out record as in stock.

**There is no pre-order marker and no pre-order bypass.** The store's only
pre-order signal is membership of its `upcoming-releases` collection — no
tag, no product field — so writing a ` (Pre-Order)` marker would cost a
second walk to find out. It would be wrong even then: `compute_item_key`
hashes artist, title and URL, so a marker that disappeared when the record
shipped would re-key every one of its pressings at exactly the moment a
waiting user cares most, orphaning the saves and judgments held against the
old key. That is the same churn the always-append descriptor rule refuses.
A pre-order is `available: true` at a real price and is listed as an ordinary
row, which is what it is: purchasable now, shipped later.

### Prices and images

`_price()` answers `None` for anything it cannot use: a bool (bool is an int
subclass, so `True` would price a record at 1), a non-numeric string, a
non-finite float, and anything at or below zero. Every live variant carries a
usable positive price.

Covers come from `shopify_catalog.resolve_cover_image()` unchanged — the
variant's own `featured_image` where it has one, else the product's first
image. Every live product has at least one image.

### Replay over the live catalog

The crawler was replayed over the fully-cached catalog: 221 products → 182
rows across 100 artists, no `item_key` collisions, no blank artist or title,
no malformed URL, no missing cover, no null price. Every product yielding no
row was accounted for: 37 whose records are all sold out, seven merch, two
whose only variant is the CD, and the two mis-titled records above.

## Drift guards

`db.replace_stock_items()` DELETEs a crawler's previous snapshot before
inserting, and `_sync_stock` only skips that call when the crawl **raised**.
A completed-but-empty walk is therefore destructive where a raise is inert.
Each guard names a distinct way the payload can stop carrying what this
crawler reads.

The tallies are taken *before* the availability filter, so a sold-out product
still counts toward every one of them. `yielded` and `priced` are necessarily
counted after it, which is why the guards reading them are each conditioned
on a second tally rather than on emptiness alone — a store that has simply
sold out is empty legitimately.

| Guard | Raises when | Catches |
| --- | --- | --- |
| collection | `products_seen == 0` | the collection renamed, removed, or the endpoint changed shape |
| title-source | `parsed_ok == 0` | the store restyling its product names — the sole source of both the artist and the album |
| price-source | `yielded and not priced` | `price` removed or retyped store-wide |
| classification | nothing yielded, `unclassifiable > 0` | the title convention failing on *one* product, which the catalog-wide row above cannot see |
| identity-source | nothing yielded, `identity_missing > 0` | a product lost its `title`, or a record its `handle` |
| variant-identity | nothing yielded, `variant_identity_missing > 0` | a record dropped a variant carrying no usable title without it being provably sold out |
| stock-source | nothing yielded, `unreadable_stock > 0` | a record's `available` became unreadable |

Structural notes on the tallies:

- **One source, one tally.** `parsed_ok` counts the products whose title
  yields both halves of the credit. The sibling crawlers carry two tallies
  and a third co-occurrence tally because their artist and album come from
  different fields and can fail independently; here a single split of a
  single field yields both, so there is nothing a second tally would say.
- **A title-less product is counted toward `identity_missing` before the
  classification tests**, not inside them. The parse reads the title, so a
  product without one can never be classified at all — it might have been a
  record. Counted only later, a partial loss of `title` would leave an empty
  walk looking like a store that had merely sold out, and the snapshot would
  be deleted.
- **A product read and deliberately skipped does not count toward
  `unclassifiable`.** The store's merch, and a product with no record among
  its formats, could not have yielded a row however their titles read, so a
  failed parse on one is evidence of nothing. That test runs *first*, which
  is what keeps a sold-out CD-only product from raising drift.

  Neither exemption reads the *product* title, and that is what makes them
  safe on a product whose title did not parse. The first version of this
  branch required a readable album before the claim could exempt anything,
  which made it depend on the one field it must not: an untagged poster or
  beanie the store named its own way counted as `unclassifiable` and raised
  drift on a walk that had legitimately sold out. A record that lost *both*
  its title convention and its claim is exempted under the corrected rule,
  and that is the accepted cost: at that point every signal the crawler has
  says non-record, so counting it would not identify it either. Found in
  review on PR #333.

  **The two exemptions are not equally title-independent, and the order of
  the branch turns on that.** Only the `Merch` tag reads no title at all.
  `_claims_vinyl` does — for the records this store leaves untyped, its
  *whole* claim is a variant title naming a record, so blanking that title
  collapses the claim. An in-stock untyped record then classifies itself as
  a non-record, takes the exemption, and the empty walk goes unguarded while
  `replace_stock_items()` deletes a snapshot that was correct.
  `_unusable_dropped_variant` already knew that variant had been dropped
  without being provably sold out; it was simply nested *after* the claim
  and never ran. So the order is: the tag, then the dropped-variant test,
  then the claim, then the parse. Stating in the previous revision that
  "neither test reads the title" was the overstatement that left the hole —
  it was true of the product title and false of the variant titles the claim
  reads through `_pressings`. Found in review on PR #333.
  `unclassifiable` is 2 on every live walk — the two mis-titled records — and
  the guard is conditioned on an empty result, so that costs nothing until it
  matters.
- **Stock readability is judged against the raw variant set, not the
  pressings kept from it.** A product whose sole variant has a blank title
  and a readable `False` is genuinely sold out but keeps no pressings at all;
  keying on those would raise stock drift over a product read perfectly. A
  product with no variants whatsoever still says nothing and stays
  unreadable. The check reads only the kept pressings for the flags
  themselves, which is complete because `_unusable_dropped_variant` runs
  first and has already established that every *dropped* entry is a literal
  `False`: the two are a pair and must stay in that order.

  **The flags are read through the same format gate `_items` publishes
  through**, because the question the guard answers is whether a *vinyl* row
  could have been missed. This store sells the record, the CD and the
  cassette as variants of one product, so reading every titled variant let a
  CD's unreadable flag condemn a record that was readably sold out: the walk
  raised, and the stale in-stock rows survived instead of being cleared. The
  vinyl-only shelves `earache.py` and `dongiovannirecords.py` walk cannot
  express that case — every variant there is a record — which is why their
  version reads every pressing, and why copying it here imported an
  assumption this store breaks. A product with no vinyl pressing at all is
  then vacuously readable, which is right: it could never have yielded a row.
  A store-wide loss of `available` is still caught, because the vinyl
  pressings go unreadable too. Found in review on PR #333.
- **`identity_missing` and `unreadable_stock` are otherwise nested inside the
  record classification**, because only a product that reads as a record
  could have yielded a row; a mis-shelved shirt's missing handle says nothing
  about whether this walk's emptiness can be trusted. `unreadable_stock` uses
  `all()`, not `any()`: one readable variant does not make a product
  readable, or a product whose black pressing is a readable `False` and whose
  coloured pressing carries the string `"false"` would vouch for an emptiness
  half its own doing.

There is deliberately **no format-gate guard** and **no merch-tag guard**.
The variant gate is negative, so no positive signal's disappearance can
silently empty the walk; and the `Merch` tag only ever removes rows, so its
disappearance would add merch rather than empty the catalog — which the
product-level claim and the title parse then reject anyway.

## Verification

`backend/tests/test_bellaunion_crawler.py`, driving the real
`crawl_catalog()` against `respx`-mocked `products.json` pages. Fixtures are
marked at their definition as **captured** (live products from 2026-09-08,
trimmed to the fields the crawler reads), **altered** (a captured product
with one field changed to reach a branch the live data never takes), or
**invented** (a product exercising a guard the live catalog cannot).

Covered: the yielded item's exact fields; plugin identity; the title parse
across the live shapes and the ones it must reject, including both
mis-titled records and every merch title; the artist never coming from
`vendor`, including when `vendor` names another label and when it is blank;
the album-then-descriptor composition and its prefix match against a library
title; the product-level claim in both of its forms and on merch; an untyped
record published on its variant's claim and an untyped non-record rejected;
merch rejected by its tag, including a shirt titled and typed like a record;
the format gate admitting records, packaged extras and undeclared
descriptors while rejecting every medium, the store's own cassette typo,
garments, sizes and both spellings of a shirt bundle; a bundle rejected
ahead of the record word that would admit it; an album name carrying a merch
word not deciding the format; a CD-only product yielding nothing without
raising; the descriptor appended to every row; the placeholder admitted only
as a sole variant, case-insensitively; blank and junk variants; only the
literal `True` admitting a variant; no pre-order marker; price parsing of
every unusable shape; cover resolution and its fallbacks; URL construction;
pagination; and every guard above, each in both the raising and the
non-raising direction.

Each guard and rule was mutation-checked: the crawler was mutated once per
rule and the tests confirmed to fail.
