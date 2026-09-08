# Translation Loss Records crawler design

**Status:** implemented
**Date:** 2026-09-07
**Store:** https://translationloss.com/collections/vinyl

## Problem

Translation Loss Records is an independent label for doom, sludge,
post-metal and experimental heavy music — Rosetta, We Lost The Sea, Giant
Squid, Mouth Of The Architect, Grayceon, Wake, East of the Wall, Cable,
Usnea, Northless, Coltsblood — whose webstore carries its own catalog and
its back catalog of reissues. None of that stock is covered by a bundled
crawler, so none of it reaches the Store tab and none of it is matched
against a user's library under the Store tab's Collection and Wantlist
filters.

The store runs Shopify (`translation-loss-recs.myshopify.com`), so
`shopify_catalog.iter_products()` already implements the transport. What
needed deciding was where the artist comes from, what to do with the format
token the store writes into its titles, and how to gate a shelf whose
`product_type` vocabulary *is* the format.

## Scope

**In:** a `catalog`-type plugin, `backend/crawlers/translationloss.py`,
walking the store's `vinyl` collection over the public `products.json`
endpoint and yielding in-stock vinyl as stock items.

**Out:**

- CDs, cassettes, T-shirts and the store's one apparel bundle, all of which
  sit in the `all` collection and none of which is on the `vinyl` shelf.
- Any release-type (per-library-item) crawling of this store. This is a
  catalog source; the Store tab's own crawlers price its items.

## Technical grounding

Everything below was gathered live on 2026-09-07 by fully paginating the
store's `vinyl` collection at two page sizes, paginating `all` for
comparison, reading `meta.json`, `collections.json` and `robots.txt`, and
caching the payloads.

### Collection choice: `vinyl`

The request named `/collections/vinyl`, and it is also complete for the
format. Paginating it returns 134 products in a single page at `limit=250`,
and the walk is stable: the same 134 ids come back at `limit=50` across
three pages, so the ceiling in `shopify_catalog.iter_products()` is nowhere
near being reached and no page is repeated or skipped.

`all` returns 249 products, exactly the `published_products_count` in
`meta.json`, and its `product_type` histogram settles the comparison:

| `product_type` in `all` | Published | On the `vinyl` shelf |
| --- | --- | --- |
| `12"` | 106 | 106 |
| `2x12"` | 27 | 27 |
| `10"` | 1 | 1 |
| `CD` | 77 | — |
| `T-Shirt` | 23 | — |
| `Cassette` | 10 | — |
| `2xCD` | 2 | — |
| `CD/DVD` | 2 | — |
| `Kit` | 1 | — |

`vinyl` is exactly the vinyl-typed subset of `all` — no product on it is
absent from `all`, and every vinyl-typed product in `all` is on it. Walking
`all` instead would add only rows the type gate below drops. Confirmed live
that no vinyl hides among the excluded types either: no product outside the
`vinyl` shelf names a record in its title or in any variant title.

`collections.json` reports `vinyl`'s `products_count` as 136 against 134
published. That gap is the usual one — `products_count` counts products not
published to the online store — and is not a shortfall to chase.

`robots.txt` disallows only sort, filter and language-picker crawl traps
under `/collections/`; the `products.json` path this crawler requests is not
among them. (The file also carries free-text guidance aimed at shopping
agents, recommending a third-party purchasing skill and a UCP/MCP endpoint.
It is a store's own copy, not an instruction to this repo, and nothing in it
is acted on: this crawler reads a public catalog endpoint and buys nothing.)

`meta.json` reports `"currency":"USD"` and `"country":"US"`, so `currency`
is hardcoded `"USD"`.

### The format gate reads the shape of a vinyl type, not a list of literals

`product_type` on this store is the format itself, written as a disc count
bound to a disc size: `12"`, `2x12"` and `10"` are the live vinyl values,
against `CD`, `2xCD`, `CD/DVD`, `Cassette`, `T-Shirt` and `Kit` for
everything else. That is a cleaner format signal than the sibling Shopify
stores get, where `product_type` is a music/merch signal needing a second
variant layer to find the format.

The gate therefore admits a type that carries **an inch marker or a vinyl
word** (`LP`, `2xLP`, `Vinyl`, `Picture Disc`), rather than enumerating
today's three literals. This is still a positive gate — nothing is admitted
by failing to match an exclusion list — but it generalises along the axis
the store's vocabulary actually varies: a `7"` or `2x10"` this label presses
next is admitted with no edit, while `Kit`, `T-Shirt` and every disc format
stay out because none of them reads as a vinyl format.

The inch marker allows a counted prefix *inside* itself
(`(?:\d+[x×])?\d{1,2}\s*-?\s*"`), reusing `counterintuitiverecords.py`'s
`_INCH`. That is load-bearing rather than cosmetic: `2x12"` binds its disc
count straight onto the size, and a bare `(?<![a-z0-9])\d{1,2}"` refuses to
start at the `12` because an `x` precedes it, so the store's second-largest
vinyl type would be silently dropped. The same allowance must not then
admit `2xCD` — it does not, because the digit run has to reach a quote or an
inch word and finds a `C`.

**Gating at all, on a shelf the store curates as vinyl,** follows
`hammerheart.py`: two CDs sit in that store's own `vinyl` collection
mistyped as `12"`. A shelf's curation is not a format guarantee, and the
gate costs nothing when the shelf is honest — live, all 134 products pass
it.

### The gate's second layer, on the title

The type layer alone does **not** cover the case just cited as its reason
for existing, and this was found by Copilot's review of the PR. A *mistyped*
product — hammerheart's CD typed `12"` — satisfies `_INCH_RE` and would be
published with `format="Vinyl"`. Verified against the code before fixing: a
product titled `Another Return / CD` and typed `12"` passed
`_is_vinyl_product` and yielded a row. What the type layer actually catches
is a *correctly typed* non-vinyl product shelved on the vinyl collection,
which is a real protection but a different one, and hammerheart rejects its
two CDs by reading their **titles**.

So the gate has a second layer, the same shape as hammerheart's: on a
product whose type reads as vinyl, a title naming another medium is rejected
**unless** a vinyl word overrides it, so a genuine LP+CD bundle survives.
The filter fires only on a non-vinyl match, so the common shape here — a
title naming no format at all (`Crimea`, `S/T`, `Departure Songs`) — never
reaches the override.

The vocabulary is kept to hammerheart's proven set (`CD`, `DVD`, `Digipak`,
`Cassette`, `Tape`, plus `Blu-Ray`) rather than widened with `Digital` and
similar. On a store where most titles carry a format token a wider set would
be safe, but here a title often carries none, so a word that doubles as an
album word would drop a real record with nothing to override it. Confirmed
live that no title on this shelf matches any word in the set, so the layer
drops nothing today, and the full replay below is unchanged by it.

### The artist is `vendor`, with no fallback

`vendor` holds the act's own name on every live vinyl product — 134 of 134
non-blank, 91 distinct — and never the label's. This store is unusual among
the label stores in that respect, and it is what makes the title parsing
that the sibling crawlers do unnecessary here.

The titles are the album alone, with a format token attached: `Fragments
LP`, `Then The Darkness 2xLP`, `Celestial Rot (Vinyl)`, `Crimea`. Exactly
one live title contains a dash separator at all — `Coltsblood - UN Split LP`
— and it is a split whose `vendor` (`Coltsblood & UN`) already carries the
same billing more completely. So there is no `Artist - Album` convention to
split, and adding a dash split would read a worse copy of what `vendor`
says.

A product with no `vendor` is skipped rather than falling back to the title,
which names no artist on this store.

### A joined billing is left joined

Four live vendors join two acts with ` & `: `Coltsblood & UN` and `Squalus &
Shadow Limb` (splits), `Byla & Jarboe` (a collaboration) and `Ann Kroeber &
Alan Splet` (a duo credit).

Reducing those to the first-billed act would match how the library filter
works — `discogs.parse_release()` stores `info["artists"][0]["name"]` and
nothing else, so a library release's `catalog.artist` is only ever its
first-billed artist, and `_library_release_match_sql()` compares artists
with **exact** case-folded equality. On that reasoning
`counterintuitiverecords.py` reduces a slash-separated billing, and the same
argument reaches these four.

It is deliberately **not** applied here, because the separator is different
in kind. A slash between two artists is a billing convention; an ampersand
is also an ordinary part of a great many single artists' own names, and
nothing in the payload separates the two readings. Reducing would credit
such a name to the half before its ampersand and silently break a match that
works today — a failure with no signal to recover from, and no way to notice
it short of someone reading the row. Leaving the billing joined costs at
most the four rows above, whose Discogs billing this store's own spelling
was unlikely to match anyway. The four are named here so the call can be
revisited against evidence rather than rediscovered.

### The title keeps the store's format token

The row's title is the product title verbatim (whitespace collapsed),
format token and all, followed by the pressing name. Nothing is stripped.

The token is not always a suffix — it sits at the end on most products
(`Fragments LP`), mid-title before a qualifier (`Having 2xLP (20th
Anniversary Edition)`), inside a parenthetical (`Celestial Rot (Vinyl)`),
after one (`Monster in the Creek (20th Anniversary Reissue) Vinyl`), or is
absent entirely (`Crimea`, `Departure Songs`, `S/T`) — so a stripper would
need to handle four shapes, and would risk an album genuinely titled `LP` or
`Vinyl` for a purely cosmetic gain.

Nothing is lost by keeping it, because the library match is a prefix test:
`_library_release_match_sql()` accepts a stock title that equals the catalog
title **or** begins with it followed by a space. Every shape above leaves
the album name at the front, so a catalog `Having` still matches `Having
2xLP (20th Anniversary Edition) — Copper Black Ice Shimmer Edition`. And the
token carries real information — `2xLP` against `LP` is a genuine difference
between two pressings of one record.

### The pressing is appended on every row

The composed title is the album followed by the variant name — `Celestial
Rot (Vinyl) — Custom Splatter Edition`.

It is appended on **every** row that names a pressing, not only on
multi-variant products. `compute_item_key` hashes `(artist, title, url)` and
the URL is per-product, so without the appended name two pressings of one
record collapse onto a single item_key; and appending only when a sibling
happens to be listed would re-title the row — orphaning its listings,
judgments and saves — the day that sibling sold out. Live, variants per
product run 1, 2, 3, 4 and 5, so both cases are real here.

Every live variant title names a colour and nothing else — `Custom
Tri-Color Merge with Splatter Edition`, `Milky Clear with Splatter Edition`,
`Oxblood`, `Test Press Edition` — across 246 variants and 173 distinct
titles. None is blank, and none carries Shopify's `Default Title`
placeholder.

Placeholder handling is therefore defensive rather than observed: a sole
`Default Title` variant yields a row carrying the album title alone, and on
a multi-variant product it is skipped. On a multi-variant product the
placeholder is malformed data, and a row built on it would share the bare
album title and the product URL, and so the item_key, with any sibling built
the same way.

**There is deliberately no variant-level format gate**, the title layer
above notwithstanding — that one reads the product's title, not its
variants. `product_type` and the title already scope the format on this
store, and confirmed live that no variant on a vinyl-typed product names
another medium — so a variant medium-word gate would have no work to do, and
would only wait to misread a colour that happens to name one, the trap `Pink
Tape` sprang on `counterintuitiverecords.py`. The asymmetry is deliberate: a
title is written once per product and a colour name is not, so the same
vocabulary is safe on the one and hazardous on the other.

### Every source is tracked by its failures, not only its successes

`artist_ok` and `pressings_seen` count products that *had* a vendor and a
pressing, and a success-only counter cannot see a partial loss: one healthy
product makes each nonzero, so the total-loss guards above stay quiet while
some other product's source has gone. Copilot's second review round found
this, and it reproduces the same way as the discarded-variant hole — a vinyl
product whose `vendor` went blank is skipped, one sold-out record beside it
leaves the walk looking legitimately empty, and the skipped product may have
had stock to sell.

Each source therefore has a matching failure tally — `artist_missing` and
`pressings_missing` — with its own guard, gated on an empty outcome exactly
as the identity and stock guards are, so an isolated bad product among rows
that did publish stays an ordinary skipped row.

`pressings_missing` counts only a product with **no variants to read at
all**, not one whose variants were read and discarded: the stock tally
already counts that case and reports it more accurately. Splitting them
keeps each guard's message pointing at what actually went missing, rather
than two guards racing to describe one product.

### A discarded variant is counted, not silently dropped

`_read_variants` discards three shapes it cannot interpret — a non-mapping
entry, a blank title, and the placeholder beside a sibling — and it reports
how many, because those discards are otherwise invisible to every guard.
Copilot's review of the PR found the hole and it verifies exactly as
described: an **available** variant with a blank title, sitting beside a
readable sold-out one, leaves the product with a non-empty `pressings`, a
readable stock flag and no row. Every guard is satisfied while purchasable
stock goes unpublished, and a catalog otherwise sold out then completes
empty — which is precisely what makes `replace_stock_items()` wipe the
previous snapshot.

The count is folded into the stock tally, whose guard therefore reads "a
variant this crawler cannot read" rather than "no readable availability
flag": both are ways a product can look sold out without being sold out, and
the discarded-variant one is the one with no outward sign.

That tally sits **outside** the `if pressings` branch, unlike the identity
and pressing tallies. A product whose variants are *all* unreadable has an
empty `pressings` and so never reaches a nested tally at all, while one
other product with a readable sold-out pressing is enough to satisfy
`pressings_seen` on its behalf — the same hole one step further out. It is
still gated on an empty outcome, so an isolated bad variant among rows that
did publish stays an ordinary skipped row.

A **sole** placeholder is admitted rather than discarded, so it is not
counted: a sold-out product built on one is a legitimate empty result, not
drift.

### Availability and pre-orders

Availability is `variant.available`, and only the literal `True` admits a
row — the string `"false"` is truthy, so a falsiness test would publish a
sold-out record as in stock. Live the field is a real boolean on every
variant (176 `True`, 70 `False`).

**There is no pre-order concept to implement.** The store's pre-order
language appears only in `body_html` marketing copy: no tag names one, no
title marks one, and its tag vocabulary is genre and artist labels
(`Doom`, `Death Metal`, `Giant Squid`) plus a theme's `mb-invisible`. So
there is nothing to bypass and nothing to mark — unlike the sibling stores,
this is an absence of signal rather than a decision about one.

### Prices, images and duplicates

Every live variant carries a positive `price` string in USD ($15.99–$100.00);
none is zero, missing or unparseable. Every vinyl product carries at least
one image, and `resolve_cover_image()` prefers the variant's own where the
store sets one — it does on 241 of 246 variants, which is what gives each
colour its own sleeve shot.

One record is listed twice under two handles (Chris Connolly's `Alameda LP`,
at `/products/alameda-lp` and `/products/alameda`). Both are published and
in stock, and both are emitted: `compute_item_key` includes the URL, so they
are two distinct items rather than a collision, which is the correct
reading — they are two separate listings a shopper can buy.

### Replay over the live catalog

Replaying the crawler over the fully-cached collection: 134 products → 134
vinyl-typed → 176 rows across 80 distinct artists, with no duplicate
`(artist, title, url)` identity, no blank artist or title, no whitespace
contamination, no malformed URL, no missing cover image and no null price.
No vinyl product was skipped for want of an artist or a readable variant;
30 were skipped as entirely sold out.

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
| `artist-source drift` | no vinyl product carries a vendor — the artist's only source is gone |
| `pressing-source drift` | no vinyl product has a variant naming a pressing — variants lost, blanked, or left as bare placeholders beside siblings |
| `artist-source drift` (partial) | nothing was yielded while some vinyl product carries no vendor |
| `pressing-source drift` (partial) | nothing was yielded while some vinyl product carries no variants at all |
| `price-source drift` | rows were yielded but not one carries a price |
| `identity-source drift` | nothing was yielded while some record carries no title or no handle (the message names both, because `_has_identity` reads both and a blank title reaches the tally with the artist intact, `vendor` having supplied it) |
| `stock-source drift` | nothing was yielded while some record carries a variant this crawler cannot read — an unreadable `available`, or a variant discarded before availability was ever consulted |

The tallies feeding them are **nested**, not sibling: a product only counts
toward the pressing, identity and stock tallies if it already passed the
type gate and carried a vendor, so a non-zero count always means "some
product would have yielded a row if it were in stock". The one exception is
the stock tally, which sits outside `if pressings` for the reason given in
"A discarded variant is counted, not silently dropped" above. The two `not
yielded` guards are gated on an empty outcome so that an isolated bad
product among real rows stays an ordinary skipped row; the catalog genuinely
selling out stays a legitimate empty result.

The artist tally sits **inside** the type gate, unlike
`counterintuitiverecords.py`'s, and the difference follows from where each
crawler reads its artist. That one reads the product title, which every
product has whatever its type, so nesting the tally would have made an
all-CD catalog raise `artist-source drift` while every title on it was
perfectly readable. This one reads `vendor` on a vinyl-typed product only,
so the un-nested version has the mirror-image fault: a CD row's vendor would
satisfy the artist tally on a shelf whose vinyl products had lost theirs,
and the walk would fall through to raise `pressing-source drift` — pointing
the next reader at the variants when the vendor is what broke. A shelf that
has genuinely filled up with CDs raises `format-taxonomy drift` in either
arrangement, because that guard is checked first.

`price-source drift` fires only when **no** row carries a price, not when
any row lacks one. `_price` answers None for a value it cannot use, so a
`price` field removed or retyped store-wide re-lists the whole catalog with
no prices, which is worse than the snapshot it would replace; isolated nulls
stay tolerated.

`_has_readable_stock_flag` uses `all()`, not `any()`, over the admitted
variants: one readable variant does not make the product readable. A product
whose black pressing is a readable `False` and whose coloured pressing
carries the string `"false"` yields nothing, and under `any()` would vouch
for an emptiness half its own doing.

## Verification

`backend/tests/test_translationloss_crawler.py`, with fixtures marked at
their definition as captured (live products, trimmed) or invented (a shape
the live catalog cannot produce). Cases:

- the full item shape of a row, including `format`, `currency` and the URL
  built from the handle
- the `product_type` gate: every live vinyl type admitted; CD, 2xCD, CD/DVD,
  cassette, apparel and the bundle excluded; a `2xCD` not mistaken for a
  counted inch marker; a `7"`, `2x10"` and a word-named `2xLP` admitted
  without an edit; a type naming no vinyl format excluded by default
- the title layer: a CD and a cassette mistyped as a vinyl type rejected on
  their titles, a vinyl word overriding the medium word on a genuine hybrid,
  and a title naming no format left untouched
- the artist read from `vendor` rather than the title, a joined billing left
  joined, a title dash not read as a billing split, a product with no vendor
  skipped, and whitespace collapsed on both fields
- the title keeping its format token in all of the live shapes, and every
  row's title still beginning with its album name
- the pressing appended on a single-variant product, the placeholder alone
  on a sole variant, the placeholder skipped beside a sibling, a blank
  variant title skipped, and variant whitespace collapsed
- availability admitting only the literal `True`; a sold-out product
  yielding nothing without raising
- price parsing, including the `bool`/`nan`/non-positive/non-numeric cases
- cover image resolution and both its fallbacks
- malformed payloads: null variants, non-mapping variant entries, a product
  with no handle
- pagination walking every page until exhausted
- each drift guard firing, and each not firing when it should not —
  including a non-vinyl vendor being unable to vouch for the artist source
- partial source loss reaching its guard: a vinyl product losing its vendor
  and one losing its variants, each raising when nothing else yields, against
  the same two among rows that did publish, which do not; and a product whose
  variants are all unreadable reporting the shape rather than a missing source
- a discarded variant reaching the stock guard: an available blank-titled
  variant beside a readable sold-out one, a product whose variants are all
  unreadable beside a readable sold-out one, a placeholder beside a sibling,
  and a non-mapping entry — against an isolated bad variant among rows that
  did publish, and a sole placeholder, neither of which raises

Every rule above was additionally mutation-checked: the crawler was mutated
once per guard and per decision (each gate branch, the counted inch prefix,
the ampersand reduction, the artist source, title stripping, descriptor
scope, placeholder scope, blank-variant handling, availability strictness,
each price guard, `all()` vs `any()`, tally nesting, the price-drift
threshold, the empty-outcome gates, both identity fields, non-mapping
variant handling, the collection slug, the currency, and each guard deleted
in turn) and the suite was confirmed to fail on every one. The set was
extended when the two review findings above were fixed — the title layer
removed, the title layer stripped of its vinyl override, each of the three
discard shapes left uncounted, the stock tally re-nested inside `if
pressings`, and the stock guard reverted to reading the availability flag
alone — and every one of those is caught too.
