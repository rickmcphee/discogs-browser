# Counter Intuitive Records crawler design

**Status:** implemented
**Date:** 2026-09-07
**Store:** https://counterintuitiverecords.com/collections/all

## Problem

Counter Intuitive Records is a Methuen, Massachusetts emo/pop-punk/indie
label whose webstore carries its own catalog — Origami Angel, Mom Jeans,
Prince Daddy & The Hyena, Oso Oso, Macseal, Bears In Trees, Retirement
Party, Charmer, Oldsoul, Skatune Network, Insignificant Other, Jail Socks —
alongside a small distro (The Hotelier, I'm Glad It's You, Born Without
Bones, Just Friends). None of that stock is covered by a bundled crawler, so
none of it reaches the Store tab and none of it is matched against a user's
library under the Store tab's Collection and Wantlist filters.

The store runs Shopify (`counterintuitiverecords.myshopify.com`), so
`shopify_catalog.iter_products()` already implements the transport. What
needed deciding was the format gate and the artist source, both of which
differ from the sibling Shopify stores in ways the payload settles.

## Scope

**In:** a `catalog`-type plugin, `backend/crawlers/counterintuitiverecords.py`,
walking the store's `all` collection over the public `products.json`
endpoint and yielding in-stock vinyl as stock items.

**Out:**

- Apparel, headwear, accessories, flags, posters, cassettes, CDs and digital
  releases, all of which sit in the same collection and are excluded on
  their `product_type`.
- The store's non-product rows — a `HIDDEN`-typed coupon and an
  `mws_fee_generated` shipping fee — excluded the same way.
- Any release-type (per-library-item) crawling of this store. This is a
  catalog source; the Store tab's own crawlers price its items.

## Technical grounding

Everything below was gathered live on 2026-09-07 by fully paginating the
store's `all` collection at two page sizes, reading `meta.json`,
`collections.json` and `robots.txt`, pulling the `vinyl-shop`,
`discography` and `distro-vinyl` collections for comparison, and caching
the payloads.

### Collection choice: `all`

The request named `/collections/all`, and it is also the only complete
shelf. Paginating it returns 177 products in a single page at `limit=250`,
and the walk is stable: the same 177 ids come back at `limit=50` across four
pages, so the ceiling in `shopify_catalog.iter_products()` is nowhere near
being reached and no page is repeated or skipped. `meta.json` reports
`published_products_count` as 177, exactly what the walk returns.

The store's own vinyl shelves are **not** substitutes, which is what settles
this rather than the request alone:

| Shelf | Published products | Vinyl-typed products in `all` it misses |
| --- | --- | --- |
| `vinyl-shop` | 96, all typed `Vinyl` | 15 — every `Distro Vinyl` and every `Vinyl/CD` |
| `discography` | 80 | 32 |
| `distro-vinyl` | 11 | 100 |

No product on any of those shelves is absent from `all`, and none of them
carries a vinyl product `all` does not. `all` is a strict superset, and the
`product_type` gate below does the format scoping the shelves would have.

`collections.json` reports `all`'s `products_count` as 400 against 177
published. That gap is the usual one — `products_count` counts products not
published to the online store — and is not a shortfall to chase.

`robots.txt` disallows only sort, filter and language-picker crawl traps
under `/collections/`; the `products.json` path this crawler requests is not
among them.

`meta.json` reports `"currency":"USD"` and `"country":"US"`, so `currency`
is hardcoded `"USD"`.

### Format gate, layer one: `product_type`

`product_type` is a clean format signal on this store, unlike the sibling
stores where it is a music/merch signal needing a second variant layer to
find the format. Three of its live values are vinyl:

- `Vinyl` — the label's own pressings
- `Distro Vinyl` — records the store stocks but did not release
- `Vinyl/CD` — a mixed-format product whose CD sibling layer two drops

The others are `Clothing`, `T-shirt`, `Hoodie`, `Bottoms`, `Headwear`,
`Accessories`, `Flags`, `Poster`, `Tape`, `CD`, plus a `HIDDEN` coupon and
an `mws_fee_generated` shipping fee. The three vinyl values are **enumerated
positively** rather than the rest being excluded, so a non-music type the
store adds later stays out by default.

Confirmed live that no vinyl hides outside those three: no variant of any
other product type names a record.

The `cf-type-*` tags mirror `product_type` exactly (`cf-type-vinyl` on all
96 `Vinyl` products, and so on) and are deliberately **not** read as a
fallback. They are derived from `product_type` by the store's own app, so
they would drift with it rather than independently of it — a fallback that
fails at exactly the moment it would be needed.

### Format gate, layer two: the variant, negatively

A variant title here is the pressing's colour and usually nothing else —
`Clear w Pink Cornetto /1000`, `Scrambled Eggs /200`, `Copper Nugget`,
`Half Black/Half White /150`. A positive vinyl regex would reject nearly the
whole catalog, so on a vinyl-typed product a variant is a record unless its
title names another medium.

The order of the checks is load-bearing rather than stylistic:

1. A **vinyl word** admits outright — `Vinyl`, `LP`, `2xLP`, `Picture Disc`.
2. Failing that, an **inch marker** admits — `7"`, `10"`, `12"`.
3. Failing that, a title naming **another medium** is rejected — `CD`,
   `Cassette`, `Tape`, `Digital`, `Digipak`, `DVD`, `Blu-Ray`.
4. Anything else is admitted, on the product type's own claim.

Rule 1 must come before rule 3 because of a live variant:
`AB Dark Blue / CD Light Blue 2xLP`, on Bears In Trees' *Every Moonbeam
Every Feverdream*. Its "CD" is the second disc's C and D sides, not a
compact disc. A gate testing for another medium first drops a real record —
confirmed by replaying both orders over the cached catalog, where the
medium-first order loses exactly that row and the vinyl-first order keeps it
while dropping the same 58 genuine CD and cassette variants.

Rule 3 needs word boundaries rather than an anchored exact match, because
the store names cassettes by colour too: `Pink Tape`, `Yellow Cassette`,
`Blue Cassette`, `Pink Cassette` all sit beside vinyl on vinyl-typed
products, and an anchored `^(cd|cassette)$` would publish them as records.
The boundaries are what keep `Grape Shimmer /300` in at the same time.

Live, of the 338 variants on vinyl-typed products, layer two rejects 58 —
`CD`, `Jewel Case CD`, `Digipak CD`, `Cassette`, `Tape` and the
colour-prefixed cassettes — and admits 280, of which 146 name no format at
all and reach rule 4.

There is deliberately **no merch vocabulary** in this gate, unlike
`iodinerecords.py`'s. Merch is excluded a layer earlier by `product_type`,
and confirmed live that no vinyl-typed product carries a size or apparel
variant.

### The artist comes from the title, with `vendor` as the fallback

Product titles are `Artist - Album` on all but one live vinyl product. The
split uses the repo's standard `\s+-\s*|\s*-\s+` form rather than a plain
`\s*-\s*`, which requires whitespace on at least one side of the hyphen:
this catalog contains `ANORAK! - Self-actualization and the ignorance and
hesitation towards it`, and while that one survives a naive split by luck —
the billing hyphen happens to come first — a title whose *artist* carried a
hyphen would not.

**A split's billing is then reduced to the first-billed artist.** This was
found by Copilot's review of the PR and is a real matchability bug, verified
against the code: `discogs.parse_release()` stores `info["artists"][0]["name"]`
and nothing else, so a library release's `catalog.artist` is only ever its
*first-billed* artist; and `_library_release_match_sql()` compares artists
with **exact** case-folded equality — only the title gets the
exact-or-prefix-with-space treatment. A joined billing therefore can never
match a library release, and the store's two splits would have sat
permanently outside the Store tab's Collection and Wantlist filters:

| Title billing | Row artist | `vendor` |
| --- | --- | --- |
| `Mom Jeans / Grad Life` | `Mom Jeans` | `Mom Jeans` |
| `Mom Jeans. / Prince Daddy / Pictures of Vernon` | `Mom Jeans.` | `Mom Jeans` |

The reduction reads the **billing**, not `vendor`, though `vendor` carries
the same signal and was what the review suggested. Reducing the billing
keeps the store's own spelling of that artist, which is the likelier match
for the Discogs entity name: on the three-way split the billing says
`Mom Jeans.`, with the trailing period the band uses, where `vendor` says
`Mom Jeans`. It also needs no second source, so it still works on a product
whose `vendor` is empty.

The slash needs whitespace on at least one side, for the same reason the
hyphen does and guarded the same way: an artist whose own name contains a
slash (`AC/DC`) must not be clipped to its first half. The reduction reads
the artist segment only, so the three live albums whose own titles carry a
slash (`Crushed / Gloomy Tunes`, `re: turn / DEPART`, `Play Around the Crit
/ Compound Eyes 7" Picture Disc`) are untouched.

`vendor` is the fallback, and unusually for a label store it is a good one:
it holds the artist's own name, not the label's, on all but one vinyl
product — where it names the label that released the compilation. Across the
catalog the two sources agree exactly (108 of 110 parseable titles, the two
exceptions being the splits above), so the fallback is well grounded where
it is used.

It is used on exactly one live product: `Counter Intuitive Presents: Cosmic
Debris, Vol 2`, the store's own tenth-anniversary label compilation, whose
title names no artist and whose `vendor` is `Counter Intuitive Records` —
the label that released it. It is credited to the label rather than rewritten
to `Various`, because the fallback has to generalise: a future single-artist
product that lost its billing dash would be credited correctly by `vendor`
and mis-credited as `Various` by a no-dash rule. The compilation's library
match fails on its title either way — `_library_release_match_sql` tests
exact-or-prefix-with-space and `Counter Intuitive Presents: Cosmic Debris,
Vol 2` does not begin with a catalog `Cosmic Debris, Vol 2` — so nothing is
lost by preferring the rule that generalises.

A product with neither a parseable title nor a `vendor` is skipped.

### The row's title keeps the pressing, and keeps it after the album

The composed title is the album followed by the variant name —
`Home, Like Noplace Is There (CIRecs Exclusive) — Blue w/ Black Splatter
(CIR Exclusive)`. Both halves of that are load-bearing.

The pressing is appended on **every** row that names one, not only on
multi-variant products. `compute_item_key` hashes `(artist, title, url)` and
the URL is per-product, so without the appended name two pressings of one
record collapse onto a single item_key; and appending only when a sibling
happens to be listed would re-title the row — orphaning its listings,
judgments and saves — the day that sibling sold out. A `Vinyl/CD` product
whose CD variant is delisted is exactly that case.

Keeping it *after* the album is what preserves the library match, on the
same `_library_release_match_sql` prefix rule quoted above.

Shopify's `Default Title` placeholder is the one exception: it names no
pressing, so a row built on it carries the album title alone — and only when
it **is** the product's sole variant. Live, all six placeholder products are
sole-variant. On a multi-variant product the placeholder is malformed data,
and a row built on it would share the bare album title and the product URL,
and so the item_key, with a sibling built the same way.

Whitespace is collapsed on the way through, on both the product title and
the variant name.

### Availability and pre-orders

Availability is `variant.available`, and only the literal `True` admits a
row — the string `"false"` is truthy, so a falsiness test would publish a
sold-out record as in stock. Live the field is a real boolean on every
variant (213 `True`, 125 `False`).

**There is no pre-order bypass and no ` (Pre-Order)` marker.** The store
marks a pre-order with a *dated* tag (`Pre-Order 10-02-26`) and reports its
pressings available. `compute_item_key` hashes the title, so a marker driven
off that tag would re-key the row the day the record shipped and the tag
went away, orphaning everything keyed on it. Same call as `earache.py`,
`spkr.py` and `musiconvinyl.py`; `iodinerecords.py` writes the marker, and
the difference is that its pre-order tag is undated.

### Prices and images

Every live variant carries a positive `price` string in USD ($3.00–$45.00);
none is zero, missing or unparseable. Every vinyl product carries at least
one image, and `resolve_cover_image()` prefers the variant's own where the
store sets one — it does on 125 variants, which is what gives each colour
its own sleeve shot.

### Replay over the live catalog

Replaying the crawler over the fully-cached collection: 177 products → 111
vinyl-typed → 167 rows across 57 distinct artists, with no duplicate
`(artist, title, url)` identity, no blank artist or title, no whitespace
contamination, no malformed URL, no missing cover image and no null price.
No vinyl-typed product was skipped for want of an artist or a readable
variant; 16 were skipped as entirely sold out.

## Drift guards

`db.replace_stock_items()` DELETEs this crawler's previous snapshot before
inserting, and `_sync_stock` skips that call only when the crawl **raised**
— so a completed-but-empty walk is destructive where a raise is inert. Each
guard names a distinct way the payload can stop carrying what this crawler
reads:

| Guard | Raises when |
| --- | --- |
| `no products` | the collection returned nothing — renamed, removed, or markup drift |
| `format-taxonomy drift` | no product carries a vinyl `product_type` — the store moved the format signal |
| `artist-source drift` | no vinyl product yields an artist from its title *or* its vendor — both sources gone at once |
| `format-source drift` | no vinyl product has a variant that reads as a record — variants lost, or all re-titled as another medium |
| `price-source drift` | rows were yielded but not one carries a price |
| `identity-source drift` | nothing was yielded while some record carries no title or no handle (the message names both, because `_has_identity` reads both and a blank title reaches the tally whenever `vendor` supplied the artist) |
| `stock-source drift` | nothing was yielded while some record carries no readable availability flag |

The tallies feeding them are **nested**, not sibling: a product only counts
toward the identity and stock tallies if it already parsed an artist and
survived the variant gate, so a non-zero count always means "some product
would have yielded a row if it were in stock". The two `not yielded` guards
are gated on an empty outcome so that an isolated bad product among real
rows stays an ordinary skipped row; the catalog genuinely selling out stays
a legitimate empty result.

`price-source drift` fires only when **no** row carries a price, not when
any row lacks one. `_price` answers None for a value it cannot use, so a
`price` field removed or retyped store-wide re-lists the whole catalog with
no prices, which is worse than the snapshot it would replace; isolated nulls
stay tolerated.

The artist tally sits **outside** the variant gate, because it answers a
different question — whether the store still writes its titles the way this
crawler reads them. Nested inside the gate as well, a catalog that
legitimately filled up with CDs would raise `artist-source drift` while
every title on it was perfectly readable, pointing the next reader at the
wrong source.

`_has_readable_stock_flag` uses `all()`, not `any()`, over the admitted
variants: one readable variant does not make the product readable. A product
whose black pressing is a readable `False` and whose coloured pressing
carries the string `"false"` yields nothing, and under `any()` would vouch
for an emptiness half its own doing.

## Verification

`backend/tests/test_counterintuitiverecords_crawler.py`, with fixtures
marked at their definition as captured (live products, trimmed), altered (a
captured product with one field changed) or invented (a shape the live
catalog cannot produce). Cases:

- the full item shape of a row, including `format`, `currency` and the URL
  built from the handle
- the `product_type` gate: all three vinyl types admitted; apparel, tapes,
  CDs and the non-product rows excluded; an unknown type excluded by default
- the title split, including a hyphenated word in the album and in the
  artist, whitespace collapsed, the `vendor` fallback, and a product with
  neither source skipped
- the primary-artist reduction: a split credited to its first-billed artist,
  the store's own spelling of that artist kept on the three-way split, a
  slash inside an artist name (`AC/DC`) not treated as a separator, and a
  slash in the album left untouched
- the variant gate: a vinyl word beating another medium word on the live
  `AB Dark Blue / CD Light Blue 2xLP`, inch markers, unformatted colours
  admitted by default, colour-prefixed cassettes rejected, and a colour name
  embedding a medium substring kept
- the placeholder alone on a sole variant, skipped on a multi-variant
  product, and a named variant appended even on a single-variant product
- availability admitting only the literal `True`; a pre-order neither
  bypassed nor marked; a sold-out product yielding nothing without raising
- price parsing, including the `bool`/`nan`/non-positive/non-numeric cases
- cover image resolution and its fallbacks
- malformed payloads: null variants, non-mapping variant entries, a blank
  variant title, a product with no handle
- pagination walking every page until exhausted
- each drift guard firing, and each not firing when it should not

Every rule above was additionally mutation-checked: the crawler was mutated
once per guard and per decision (gate order, hyphen regex, placeholder
scope, availability strictness, vendor precedence, tally nesting,
`all()` vs `any()`, the price-drift threshold, and each guard deleted in
turn) and the suite was confirmed to fail on every one.
