# Monorail Music crawler design

**Status:** implemented
**Date:** 2026-09-09
**Store:** [monorailmusic.com](https://monorailmusic.com)

## Problem

Add a `crawler_type="catalog"` plugin for Monorail Music, the independent
record shop in Glasgow, so its stock appears in the Store tab and is matched
against the Collection and Wantlist filters like every other catalog source.

The store runs on Shopify, so `shopify_catalog.iter_products()` already covers
the transport. What this store needs designing is the *scoping*: unlike the
label stores this fleet mostly crawls, and unlike the Shopify record shops it
already crawls, Monorail publishes no vinyl shelf. Its records, CDs,
cassettes, films, books and merchandise are one catalog, and the format is
written on the variant rather than the product. Every decision below follows
from that.

## Scope

In: a new `backend/crawlers/monorailmusic.py` and its test file. Out:
everything else — the plugin is discovered by `main.py`'s bundled-crawler
startup loop, so there is no wiring to change anywhere.

## Technical grounding

Everything in this section was confirmed against the live store on
2026-09-09 by walking its whole published catalog: 5,765 products over 24
pages of `products.json`, matching the `published_products_count` in
`meta.json` exactly.

### Collection choice: `all`

There is no vinyl shelf to walk instead. `collections.json` publishes genre
shelves (`indie-pop`, `jazz`, `metal`, `scottish`), staff-pick shelves, per-
member shelves and event shelves — every one of them mixes formats — plus two
catalog-wide shelves (`music`, `new`) that are the same products under another
name. So the format scoping cannot be done by choosing a URL, and has to be
done in the crawler. That is what the two gates below are.

`collections.json` reports a smaller `products_count` for `all` (5,707) than
the walk returns (5,765). It counts products not published to the online
store, so the walk's own exhaustion is the catalog, not that number. The same
mismatch is recorded on the Sacred Bones shelf and resolved the same way.

The walk is nowhere near Shopify's `page`-100 ceiling, so nothing is truncated.

### The kind gate: `product_type == "Music"`

The store's own kind field, and the first gate. Its vocabulary is coarse —
`Music` (5,382), `Film & TV` (265), `Books` (49), `Merchandise`, `Accessories`,
`Events`, `Other`, and 51 products with an empty string — so it does **not**
name a format: the CDs and cassettes are `Music` too. The variant gate below
is what reads the format.

What this gate does is keep out the one shape that would otherwise pass every
other test on the page. The store sells in-store album launches as a single
product bundling a record with an event ticket:

```
'The Bad Fire' Album Launch
  2LP (Monorail Exclusive Lemon Opaque Vinyl SIGNED) + Ticket   £47.99
  Ticket Only (1 per customer)                                  £22.00
```

Those bundles are the **only** products outside the `Music` type whose
variants name vinyl at all — confirmed across the whole catalog — and their
price is a record's price plus a ticket's, so publishing one misprices the
record. They carry an **empty** `product_type` rather than `Events`, which is
why the gate admits a named type rather than rejecting a list of them.

### The format gate is per variant, and it is positive

A product here is a *release* and its variants are the formats it was released
in: the option axis is literally named `Format` on every product that has one
(5,712 of them; the rest are `Title`, `Ticket type`, `Denominations`). So a
record and its CD sit on one product, sharing a title and a URL:

```
Adrianne Lenker - Bright Future     LP £23.99  ·  CD £10.99
Alabaster DePlume - Come With ...   2CD £16.99  ·  Black LP £25.99
```

The medium is therefore decided **per variant**, off the variant title.

The gate is **positive**: a variant has to name a record to be admitted.
This is the opposite of the negative gate `sacredbonesrecords.py` uses, and
the difference is the shelf rather than the taste — a negative gate is safe
there because the shelf is already curated as vinyl, and here it would publish
every CD in the shop. Of 6,464 music variants, 4,323 name a record and 2,141
do not.

The vocabulary is free text with a long tail (1,902 distinct strings), so the
gate matches shapes rather than literals:

| Pattern | Why it is not the obvious one |
| --- | --- |
| `vinyl` as a substring | The store writes `biovinyl`; `\bvinyl\b` drops it. |
| `vinly` | The store's own misspelling, live on a record it sells. |
| `\d*[x×]?d?lps?\d?` | `\b` matches nothing between a digit and a letter, so a plain `\blps?\b` reads none of `2LP`, `2xLP`, `DLP`, `LP2`. |
| sizes `7`/`10`/`12` before an inch mark | Any number would admit the `1/4"` in `180g LP Cut Directly From The Original 1/4" Master Tapes`. An optional count precedes the size, or `2x12"` is dropped; it cannot admit `2xCD`, since a size and an inch mark still have to follow. |
| `VL` | The store's bare abbreviation for a vinyl LP. Confirmed against the records carrying it rather than guessed — every one is an LP at an LP's price. |
| `picture disc`, `flexi` | Records that name no LP and no size. |

There is deliberately **no** list of non-vinyl words. A positive gate does not
need one: `CD`, `2CD`, `Cassette`, `SACD`, `Digipak` and the rest fail by
naming no record, and a *bundle* — `LP + BONUS CD`, `2LP Orange Vinyl +
Blu-Ray`, `Ice blue vinyl LP w/ tape + tote bundle` — is correctly admitted as
the record it is. All 15 live variants naming both a record and another medium
are bundles of exactly that kind.

#### The dimension guard

One live product defeats the inch marker on its own:

```
CD With "The Manuscript" Bonus Track + Collectible 20pp Booklet + 10" X 10" Poster
```

The poster's size is read as a 10-inch single and a CD is published as a
record. `10" X 10"`-shaped runs are therefore deleted before the marker is
tested — the same move `byrdlandrecords.py` makes when it deletes `not vinyl`
before its own override reads the `vinyl` inside it. It is the only false
positive in the catalog, and the guard removes it without costing a single
genuine record.

#### What the positive gate costs

Accepted and documented rather than worked around: the store names a small run
of pressings by colour or edition alone — `Eco Mix Random Colour`, `Apricot
Color Wax`, `2026 Repress`, `Gamma Cloud Green`, `15th Anniversary Edition` —
which name no format and so are dropped. Live that is 22 products with no
readable variant at all, against roughly 1,200 CD-only products the gate keeps
out.

Admitting a bare colour instead would mean reading a colour as proof of a
record, and colour names a CD edition too: `Animal Collective - Merriweather
Post Pavilion` carries `15th Anniversary Edition` beside a plain `CD` on one
product, with nothing to say which medium the former is.

### The artist: the title first, a sole tag second

`vendor` is the **label** here (`DOMINO RECORDS`, `4AD`, `Dischord`, `BFI`),
never the act, so — unlike most of this fleet's Shopify crawlers — it is no
use as the artist. The store writes the billing into the product title
instead, as `Artist - Album`, split on the first spaced dash. 5,072 of 5,382
music titles carry one.

**The tag fallback.** The remaining 310 are filed under the album name alone —
compilations, self-titled records, and reissues whose act is on the sleeve but
not in the field — and the title alone cannot credit them to anybody. 308 of
them carry tags, and the tag is the act:

```
'Action Time Vision'          tags=['Alternative TV']
'Ask Forgiveness'             tags=['Bonnie Prince Billy']
"Don't Smile At Me"           tags=['Billie Eilish']
'Eccentric Soul: The Saadia Label'  tags=['Various Artists']
```

That is 144 in-stock records recovered rather than dropped.

**Why the title stays primary.** Shopify stores tags as one comma-separated
string, so an act whose own name contains a comma arrives already split and
nothing in the payload marks the join:

```
'Black Country, New Road - For The First Time'  tags=['Black Country', 'New Road']
'Barnett, Courtney - Creature Of Habit'         tags=['Barnett', 'Courtney']
```

The title carries those names whole; the first tag is a fragment. Measured
over every product with a separator and exactly one tag, the tag agrees with
the title's artist half 4,774 times out of 4,799.

**Why exactly one tag.** The store's tags are alphabetically sorted and mix
the act with genre words, so on a multi-tag product the first tag is whichever
sorts first — not the artist:

```
'Lucy Dacus - No Burden (2026 Reissue)'  tags=['Alt', 'Lucy Dacus']
'Anxious - Little Green House'           tags=['alternative', 'Anxious', 'emo', ...]
```

And two tags may equally be one comma-split name or two collaborators, with
nothing separating the readings. The one-tag test turns a measured property
into a rule: **no single-tag product in the catalog carries a genre word.**
The genre tags only ever appear *alongside* the act's own tag, never alone —
checked exhaustively against the vocabulary that co-occurs with a confirmed
artist tag. Three products are skipped by this rule, all genuine
collaborations that a first-tag read would have mangled.

#### The number-range guard

Three live titles put the first spaced dash between two numbers:

```
Far East New Rock Invention 1969 - 1975         tags=['Various Artists']
Jon Savage's 1986 - 1990 Rollin' Under the Melody  tags=['Various Artists']
Ocean View Unit, 2012 - 2015                    tags=['Yacht Club']
```

Splitting there credits the first to an artist called `Far East New Rock
Invention 1969`. The split is refused when the artist half ends in a digit and
the album half starts with one — and refusing it rather than repairing it
afterwards is what hands the title to the tag fallback, which carries the
right answer for all three.

### The row's title keeps the pressing

`item_key` hashes `(artist, title, url)` and every variant of a product shares
the artist and the URL, so the pressing name is what keeps two pressings of
one release apart. 46 live products list more than one in-stock record —

```
Chappell Roan - The Giver
  Limited Neon Orange 7" with "The Construction Worker" Alternate Cover  £13.99
  Limited Swirl 7" with "The Plumber" Alternate Cover                    £13.99
  Limited Silver 7" with "The Private Investigator" Alternate Cover      £13.99
```

— and without the pressing each would emit several rows under **one
identity**.

Nothing would raise, and that is the point. `stock_items.item_key` is
deliberately not unique — two stores stocking the same record share one, and
`db.py` says so where the index is declared — and `stock_item_identities`
upserts on it. So the failure is silent rather than loud: the pressings would
share the saves, judgments and crawl-queue state keyed on that one identity,
each would overwrite the last's `stock_item_identities` row, and the Store tab
would list them as duplicates.

The pressing is appended on **every** row, not only on multi-pressing
products: a sibling selling out must not re-title the surviving rows and
orphan the saves and judgments keyed on the old `item_key`. It goes **after**
the album, so `db._library_release_match_sql`'s
`LOWER(s.title) = LOWER(c.title) OR starts_with(LOWER(s.title), LOWER(c.title) || ' ')`
still matches — a catalog `The Giver` matches this row's
`The Giver — Limited Swirl 7"…`.

`format` stays `"Vinyl"` unconditionally, as every sibling catalog crawler
does: the specific cut is already in the appended pressing.

### Availability, and why there is no pre-order handling

`variant.available` is a real boolean on all 6,464 music variants, and only
the literal `True` admits a row — the string `"false"` is truthy, so a
falsiness test would publish sold-out records. Verified end to end: a product
this crawler reads as sold out renders `Sold out` and `"available":false` on
its own live page.

There is **no** pre-order handling and no `(Pre-Order)` marker. The store's
pre-orders report `available: True` and carry no tag, no title marker and no
distinguishing product field — their only signal is membership of a separate
`pre-order` collection, which this payload does not carry. They are
purchasable at the listed price, so they are stock. A marker would also
re-title every row the day the record ships, orphaning everything keyed on the
old `item_key`.

### Prices, currency and images

Prices arrive as strings on every variant and are parsed with the fleet's
guarded `_price`: `bool` before `float()` (a `bool` is an `int`, so `True`
would price a record at £1), and non-finite or non-positive values rejected.

Currency is `GBP`, from the store's `meta.json`, and confirmed against a live
product page rendering `£14.99` for the value the payload carries as
`"14.99"`.

Covers go through `resolve_cover_image`, which prefers the variant's own image
over the product's. That matters here rather than being defensive: 1,462
variants carry their own `featured_image`, typically the specific colourway.
635 music products carry no image at all, and a row without a cover is still
worth publishing.

### Replay over the live catalog

The crawler was replayed over the fully cached 5,765-product walk: **2,862
rows across 1,969 artists**, no `item_key` collisions, no blank artist or
title, no null price, no malformed URL, and no row whose pressing names a
non-vinyl medium. 143 of those rows take their artist from the sole-tag
fallback. Prices span £4.99 to £295.99.

## Drift guards

`db.replace_stock_items()` DELETEs this crawler's previous snapshot before
inserting, and `_sync_stock` only skips that call when the crawl **raised** —
so a completed-but-empty walk is destructive where a raise is inert. Each
guard names a distinct way the payload can stop carrying what this crawler
reads.

Both gates here are positive, unlike the negative gate `sacredbonesrecords.py`
can leave unguarded, so **each needs its own guard**: a renamed `product_type`
or a dropped `Format` axis empties this walk while every product still parses.

| Guard | Fires when | Why it cannot be left out |
| --- | --- | --- |
| `payload drift` | the walk returned no products | the collection was renamed or removed |
| `kind-taxonomy drift` | no product carries the `Music` type | the kind gate is positive; a renamed vocabulary empties the walk silently |
| `variant-identity-source drift` | nothing was yielded and some `variants` collection or entry could not be interpreted | those discards are otherwise invisible, and a product whose variants are all discarded reaches no other tally |
| `format-taxonomy drift` | no music product names a record on any variant | the format gate is positive; availability is not read until after this tally, so a sold-out shop cannot trip it |
| `price-source drift` | rows were yielded and *none* carries a price | a store-wide `price` change re-lists the catalog with no prices, which is worse than the snapshot it replaces; isolated nulls stay tolerated |
| `artist-source drift` | nothing was yielded and some record carried no readable artist | the artist has no third source to fall back to, and skipping leaves the walk looking sold out |
| `identity-source drift` | nothing was yielded and some record lost its title or handle | `item_key` hashes both, so a re-identified row orphans the judgments and saves keyed on the old one |
| `stock-source drift` | nothing was yielded and some record carried no readable availability flag | an empty result is only trustworthy when everything that could have yielded a row was readable and simply out of stock |

Two orderings inside that table are load-bearing:

- **`variant-identity` is consulted before `format-taxonomy`.** A product
  whose variants cannot be read has no admitted pressing either, so the format
  guard would otherwise answer every variants-level failure with the one
  diagnosis that is not the cause. Nothing the format guard is for can hide
  behind that ordering: a renamed format vocabulary leaves the variants
  perfectly readable.
- **The per-product tallies are nested inside `if pressings:`.** A CD-only
  product would never have yielded a row whatever its title said, so it must
  not satisfy the artist guard on behalf of a record that has lost its own.
  Un-nested, the CDs' titles vouch for a catalog of artist-less records and
  the walk completes empty having passed every guard.

The four empty-outcome guards are gated on `not yielded` so that one bad
product among real rows stays an ordinary skipped row, and
`_has_readable_stock_flag` uses `all()` rather than `any()` so one readable
variant cannot vouch for an unreadable sibling.

## Verification

- Unit tests in `backend/tests/test_monorailmusic_crawler.py`, fixtures
  captured live and marked at each definition with their provenance
  (`captured` against `invented`).
- Every rule and every guard above was mutation-checked: the crawler was
  mutated once per rule and the suite confirmed to fail each time. Two
  mutations survived the first pass — keeping non-mapping variant entries, and
  admitting a blank variant title — and tests were added for both until every
  mutation was caught.
- Replayed over the cached live catalog, with the results in *Replay over the
  live catalog* above.
