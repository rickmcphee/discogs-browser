# Don Giovanni Records crawler design

**Status:** implemented
**Date:** 2026-09-07
**Store:** https://dongiovannirecords.com/collections/vinyl

## Problem

Don Giovanni Records' own webstore carries the label's catalog — Screaming
Females, Downtown Boys, Laura Stevenson, Bad Moves, Moor Mother, Irreversible
Entanglements, Alice Bag, Teenage Halloween, Swamp Dogg, Nana Grizol, RVIVR,
Priests, Big Eyes, Jeffrey Lewis, Peter Stampfel — none of it covered by a
bundled crawler. So none of it reaches the Store tab, and none of it is
matched against a user's library under the Store tab's Collection and
Wantlist filters.

The store runs Shopify (`don-giovanni-records.myshopify.com`), so
`shopify_catalog.iter_products()` already implements the transport. What
needed deciding was how to read a row out of the payload, and the payload
turns out to be the cleanest of any store crawled so far: `vendor` is the
artist on every product, the title follows one convention with no
exceptions, and the `vinyl` collection is exactly the store's records.
The design work was therefore less about salvaging a signal than about
deciding which of two agreeing signals is the source of record, and about
guarding an unusually tidy payload against becoming untidy.

## Scope

**In:** a `catalog`-type plugin, `backend/crawlers/dongiovannirecords.py`,
walking the store's `vinyl` collection over the public `products.json`
endpoint and yielding in-stock vinyl as stock items.

**Out:**

- The rest of the store. Confirmed live (2026-09-07) by fully paginating the
  `all` collection: 374 published products, of which 158 are the vinyl and
  the remainder are CDs (129 `CD` plus 3 `2xCD`), 13 cassettes, apparel
  (T-shirts, longsleeves, crewnecks, a tank top), books, pins, stickers and
  bags, plus four untyped bundles.
- Multi-item bundles (`Bad Moves LP + Shirt`, `Bad Moves Vinyl Bundle`,
  `Bad Moves Shirt + All Vinyl`). A bundle is not a Discogs release and its
  price is not any record's price. None is currently shelved in `vinyl`, and
  none carries a quoted album, so two independent rules already exclude them
  — but see "Bundles" below for why one of those rules is written down
  anyway.
- Any release-type (per-library-item) crawling of this store. This is a
  catalog source; the Store tab's own crawlers price its items.

## Technical grounding

Everything below was gathered live on 2026-09-07 by fully paginating the
store's `vinyl` collection and its `all` collection at two page sizes, and
by reading `meta.json`, `collections.json`, the `preorders` collection and
`robots.txt`. The payloads were cached and every rule below was replayed
against them.

`robots.txt` is Shopify's standard storefront file and allows crawling of
public product and collection JSON; it asks only that agents not complete
checkout, which this crawler never does.

### Collection choice: `vinyl`

The request named `/collections/vinyl`. Paginating it returns 158 published
products on a single page at `limit=250`, and the walk is stable: the same
158 product ids come back at `limit=50` across four pages. `collections.json`
reports `products_count: 169`, which counts products not published to the
online store; `products.json` returns the published ones, and the walk's own
exhaustion is the catalog. This mirrors what `shopify_catalog` already
documents for the sibling stores.

The shelf is exactly the store's records, in **both** directions, which is
rarer than it sounds and is what lets the rest of this design stay small:

- Nothing non-vinyl is in it. Every product in the collection carries a
  `product_type` of `12"` (134), `2x12"` (18) or `7"` (6).
- No vinyl is outside it. Every product in the whole store carrying one of
  those three types is in the collection; the difference between the store's
  374 published products and the collection's 158 is entirely CDs,
  cassettes, apparel, books and merch.

So there is no wider shelf worth walking and no narrower one worth
preferring.

### The artist comes from `vendor`, and only from `vendor`

`vendor` is a real artist credit on every product in the collection —
`Ailbhe Reddy`, `Downtown Boys`, `Lee Bains + The Glory Fires`,
`Irreversible Entanglements` — never blank, never the label's own name, and
never a housekeeping value. That already distinguishes this store from the
Shopify siblings whose `vendor` names the label (`earache.py`) or names it
some of the time (`matadorrecords.py`).

The product title carries the same credit a second time, ahead of a quoted
album: `Ailbhe Reddy "Kiss Big" 12"`. Across the whole collection the two
agree on all but one product. On that one they disagree, and the
disagreement is what settles which is the source:

```
title:  The London Experimental Ensemble with Richard Thompson, Wesley Stace,
        Sivert Høyem, Marissa Nadler a "Child Ballads: The Final Six" 2x12"
vendor: The London Experimental Ensemble with Richard Thompson, Wesley Stace,
        Sivert Høyem, Marissa Nadler...
```

The store truncates the credit in the title at 100 characters, mid-word, to
leave room for the album; `vendor` carries its own truncation but at least
ends at an ellipsis rather than a severed `Nadler a`. `vendor` is therefore
never worse and sometimes better, and it is a field of its own rather than a
substring recovered by regex.

**There is no fallback to the title's artist prefix when `vendor` is blank.**
This is deliberate, and it is the one place where the tidiness of the payload
argues the other way, so the reasoning is worth stating:

- A fallback would defeat the drift guard. `vendor` is the only artist
  source, so a store-wide loss of it is exactly what `artist_ok == 0` is
  there to catch. With a fallback the guard could never fire, the walk would
  complete, and `replace_stock_items()` would swap a good snapshot for one
  built from a second-choice source with nobody the wiser.
- A fallback would silently re-identify rows. `compute_item_key` hashes
  `artist|title|url`, so switching artist source rewrites the key of every
  row whose credit the two sources spell differently — starting with the
  truncated one above — orphaning the saves and stock judgments keyed on the
  old identity.

A blank `vendor` therefore skips the product, and a store-wide blank raises.

### The album and the format come from the title

`vendor` gives the artist but not the album, so the title is still parsed —
for the album and the format descriptor only.

The **quoted-album** half of the convention is uniform across the entire
store, records and non-records alike: every product leads with
`Artist "Album"`. The trailing `<format>` is universal *within the vinyl
collection* but not outside it — the store's books, pins and stickers stop at
the quoted album, which is the asymmetry the parse rule below turns into a
filter. Every one of the 158 titles in the
collection contains **exactly three** straight double quotes and no
typographic ones: the album's opening quote, its closing quote, and the inch
marker that ends the format. Apostrophes appear inside album names
(`"It's The Ones Who've Cracked That The Light Shines Through"`,
`"Don't Mess Up"`, `"What's Wrong With Me?"`) but never stand in for a
closing quote, unlike Earache's catalog.

The parse is therefore:

```
^(?:[^"“]*?)\s*["“](?P<album>[^"“”]+?)["”](?=[\s\d]|$)\s*(?P<rest>.*)$
```

- **Neither the leading group nor the album group may contain a quote**, and
  between them that is what pins all three of the title's quotes. The
  leading group excluding them makes the album's **opening** quote always
  the title's first, so the trailing inch marker can never be mistaken for
  it. The album excluding them is what makes a fourth quote junk rather than
  an album, so `Artist "" 12"` parses to nothing instead of to an album of
  `" 12`.
- **The leading group is non-capturing, and may be empty.** Whatever sits
  ahead of the album is never read — the credit comes from `vendor` — so the
  group exists only to pin the opening quote, and the parse returns the
  album and the format alone. Both parts of that matter. Emptiness is
  allowed because a title that omits the artist (`"Album" 12"`) still
  carries a readable album and format, and the row built from `vendor` is
  correct; requiring a prefix would drop it for naming something the crawler
  does not use. And not returning the prefix is what keeps the album-source
  guard honest: while the parse returned it, that guard tallied the prefix
  while row emission gated on the album, so a store that dropped its artist
  prefixes would have raised on a catalog every row of which this crawler
  reads perfectly. Found in review on PR #323.
- **The closing quote's lookahead adds a rejection, not a choice.** With the
  exclusions above the closing quote is already deterministic — it is the
  next one — so requiring whitespace, a digit or the end after it only ever
  refuses a parse. What it refuses is a closing quote glued to a letter
  (`"Fire"X`), which fails outright rather than being guessed at. The `\d`
  arm keeps a descriptor glued onto the closing quote (`"Album"12"`)
  readable — this store does not write it, but Earache's does.
- **A quote left in the descriptor is an inch marker, or it is drift.** The
  lookahead above does *not* on its own reject a nested quotation: in
  `Artist "The " Big" 12"` the inner quote is followed by whitespace, so it
  satisfies the lookahead and the parse yields an album of `The` and a
  descriptor of `Big" 12"` — which the inch marker inside that descriptor
  then admits as a record. The first draft of this document claimed the
  lookahead covered this; it does not. Every quote this store leaves after
  the album is an inch marker, and an inch marker always follows its digits
  (`12"`, `2x12"`, `7"`), so a quote in the descriptor that is not preceded
  by a digit rejects the parse. Found in review on PR #323.
- Curly quotes are admitted on both sides even though the store writes none.
  They cost one character each and are the commonest way a Shopify store's
  copy drifts.

**Both halves are required.** A title parses only if it carries an album *and*
a format; `Artist "Album"` with nothing after it does not. Every one of the
158 live records names its format, and the products that stop at the quoted
title are exactly the store's non-records — `Larry Livermore "Spy Rock
Memories"` (a book), `Teenage Halloween "Cloud"` (a pin), `Keith Secola
"NDN KAR"` (a sticker). Running every non-vinyl product in the store through
the parse and the format gate as though it had been mis-shelved into `vinyl`,
this rule is what excludes nine of the ten that would otherwise have been
published as records.

Rejecting silence rather than admitting it costs no live row and needs no new
guard: a store-wide loss of the trailing format empties `parsed_ok` and raises
album-source drift, which is what that guard's message already claims to
cover. It is also narrow — it rejects *silence*, not novelty, so a format the
store adds later (`10"`, a box set) is still admitted by the gate below.

All 158 live titles parse. A title that does not parse yields no row, and a
collection in which none parses raises.

### The row's title keeps the format, and keeps it after the album

The row's title is `album + " " + descriptor` — `Kiss Big 12"`,
`Open The Gates 2x12"` — with the variant colour appended after it (below).

The descriptor stays, and stays *after* the album, for the same two reasons
the sibling stores give:

- **After**, because the library match behind the Store tab's Collection and
  Wantlist filters is `db._library_release_match_sql`'s exact-or-prefix-with-
  space test against the catalog title. `Kiss Big 12"` matches a library
  `Kiss Big`; `12" Kiss Big` would match nothing.
- **Kept**, because `title_key` (the Cheapest filter's grouping key) exists
  precisely to fold format words away, and it does: `Kiss Big 12" — Red`
  keys to `big kiss red`, identical to another store's `Kid A`-style
  `Kiss Big LP Red`. A `7"` folds the same way.

One caveat, recorded because it is a real limitation of the row this crawler
emits rather than a choice made here. `title_key`'s disc-size phrase rule is
anchored on a word boundary, and `2x12"` has none between the `x` and the
`12`, so a `2x12"` survives into the key as a literal `2x12` token where
`2xLP` and `Double LP` fold away. The 18 double LPs in this collection
therefore will not group with another store's copy of the same pressing
under the Cheapest filter. This is pre-existing — `earache.py` already emits
`2x12"` descriptors and hits it identically — and fixing it means changing a
shared module that keys every row in the Store tab, so it belongs in its own
change rather than riding along with a new crawler.

### A `+` joining a record to merch is a bundle

`_BUNDLE_RE` catches only the literal word, so the store's other combo shape
slips past it: `Bad Moves "Untenable" LP + Shirt` parses, and the format gate
would then admit it on its own `LP` before ever reaching a merch word. Its
price is a bundle's, not any record's.

So the gate checks for a `+` beside a merch word **first**, ahead of the
record word that would otherwise admit outright. `LP + Bonus CD` stays a
record — one item, one price, and no merch word in it — and because the gate
reads the descriptor alone, an album called `Pins + Needles` keeps its row.

This also forced the merch vocabulary wider than the `product_type` list it
was read off: those say `T-Shirt`, while the store's bundle titles say a bare
`Shirt`. Found in review on PR #323.

### Format gate: the shelf claims, a named medium rejects

The shelf currently holds nothing but records, so the gate rejects nothing
today. It is written anyway, because of what the rest of the store looks
like: **every** product in it — CD, cassette, T-shirt alike — uses the same
`Artist "Album" <format>` title as the records.

```
Ailbhe Reddy "Kiss Big" CD
Agua Viva "Piece Of Water" Cassette
Bad Moves "Logo" T-Shirt
```

A CD or a shirt mis-shelved into `vinyl` would parse perfectly and be
published as a record at a price the user would then compare against
records. The descriptor is the only thing that distinguishes them, so the
descriptor is what is gated.

The gate is negative, matching `earache.py`'s on its own vinyl shelf: a
record word admits outright, then a word naming another medium or a merch
item rejects, and anything else is admitted on the collection's own claim.
Negative rather than enumerated so that a format the store adds later
(`10"`, a box set) stays in by default; the shelf has already said the
product is a record.

The rejecting vocabulary is not invented — it is the store's own
`product_type` values, read off the `all` collection: `CD`, `2xCD`,
`Cassette`, `T-Shirt`, `Girls T-shirt`, `Tank Top`, `Longsleeve`,
`Crewneck Sweatshirt`, `Books`, `Paperback Book`, `Hardcover Book`, `Pins`,
`Bag`, `Stickers & Decals` — plus `Zine`, which is not a `product_type` but
is the descriptor of the tenth product in that mis-shelving sweep, and the
only non-record in the store that names a format at all (`Liz Pelly
"P.S. Eliot: 2007-2011" Zine`). The disc media carry the same optional
disc-count prefix the record words do, because `2xCD` is one of the store's
own types and `\bcds?\b` cannot see the `CD` in it.

With those two rules in place the sweep is clean: no product the store sells
outside the `vinyl` collection would be published as a record if it were
shelved inside it.

The gate reads the **descriptor** only, never the whole title, so an album
named `ABCD` or `Bag` cannot decide the format of the record it names.

`product_type` is deliberately *not* the gate, despite being a tidy
three-value field here. It is not maintained in lockstep with the title:
`Moor Mother "Circuit City" 2x12"` is typed `12"`. A gate on it would also
be an enumeration, silently dropping any format the store adds — the
direction this design avoids.

### Bundles

The store sells bundles (`Bad Moves LP + Shirt`, `Bad Moves Vinyl Bundle`,
`Bad Moves Shirt + All Vinyl`, `Bad Moves Wearing Out The Refrain Shirt + CD`).
None is in the `vinyl` collection and none carries a quoted album, so the
title parse alone excludes every live one.

A one-line title rule is written down anyway. If a bundle were ever shelved
in `vinyl` *and* written to the store's usual convention — a plausible
`Bad Moves "Untenable" Vinyl Bundle` — the title would parse and the
descriptor's own `Vinyl` would admit it through the format gate. The rule
sits ahead of both, on the whole title.

### Variants, availability and pre-orders

Every product's single option is `Color`, and every one of the 167 variants
in the collection names a real colour: `Black` (99), `Red`, `Yellow`, `Pink`,
`Blue`, and a long tail of one-offs — `Ocean Surf Blue Swirl`, `Blue Dream`,
`Coke Bottle Clear`, `Hand-numbered Blue`, `Peace Pill`, `"Needle Drop" B/W
splatter`. Shopify's `Default Title` placeholder appears nowhere.

**The colour is appended to every row that names one**, and the placeholder
is handled defensively even though the live catalog has none — admitted only
as a product's *sole* variant, carrying the composed title alone. Appending
unconditionally rather than only on multi-variant products is the sibling
stores' rule and matters for the same reason: a sibling pressing being
delisted must not re-title the surviving rows and orphan the listings,
judgments and saves keyed on the old identity.

The colour is load-bearing here in a way it is not everywhere, because this
store splits one record across two *products*:

```
Dead Best "Dead Best" 12"   handle dead-best-dead-best-12   Black,  $21.99
Dead Best "Dead Best" 12"   handle dead-best-dead-best-13   Yellow, $20.99
Rodeo Boys "Home Movies" 12" handle rodeo-boys-home-movies-12 Black, sold out
Rodeo Boys "Home Movies" 12" handle rodeo-boys-home-movies-13 Green,  $24.99
```

Distinct handles mean distinct URLs and so distinct `item_key`s regardless,
but without the colour the two rows would be indistinguishable to a reader.
Eight products are multi-variant the ordinary way (`Teenage Halloween
"Teenage Halloween" 12"` carries Black, Electric Smoke and Light Blue, two
of them sold out).

**The catalog-wide source tallies do not see a *per-product* failure.**
`artist_ok`, `parsed_ok` and `sources_ok` all count across the shelf, so they
only notice a source vanishing from every product. One well-formed record
that is merely sold out keeps all three non-zero while an unreadable product
beside it — a blank `vendor`, or a title that no longer parses — is skipped by
`_record()` before the identity and stock tallies ever run. The walk completes
empty and the snapshot is deleted, even though that product might have been a
record.

`unclassifiable` counts a product whose *own* sources failed, and the walk
raises when nothing was yielded and one exists. The distinction it turns on is
between a product that was **read and then skipped** and one that was **never
read at all**: a CD is classified by the format gate and a bundle by the
bundle rule, and neither counts, so a legitimately sold-out shelf full of CDs
still returns empty without raising. Found in review on PR #323.

**A variant dropped for want of a usable title is identity drift unless it is
provably sold out.** The colour is part of the row's identity, so a variant without
one cannot be published — but if it is in stock, its absence must not read as
a pressing that sold out. A product whose in-stock variant has a blank title
and whose named sibling is sold out otherwise yields nothing while
`_has_readable_stock_flag` still reports that sibling readable, every guard
passes, and the snapshot is deleted. The same applies to the placeholder on a
multi-variant product, which is the other route a variant gets dropped.

Only the literal `False` proves the dropped variant was safely sold out.
Every other value — `True`, the string `"true"`, `1`, `None`, absent — leaves
it unproven, and unproven is drift: a dropped variant carrying `"true"` is
exactly as invisible as one carrying `True`, and the readable sold-out sibling
beside it must not vouch for the emptiness. Found in review on PR #323, in two
passes — the first established that in-stock dropped variants count, the
second that "not in stock" is not the same as "provably sold out".

**Availability** comes from `variant.available`, which is a real boolean on
every one of the 167 live variants (140 `true`, 27 `false`). Only the
literal `True` admits: the string `"false"` is truthy, so a falsiness test
would publish a sold-out record as in stock.

**Pre-orders are not marked**, and the row a tagged product yields is
byte-identical to the row it would yield untagged. Nine products in the
collection carry a `preorder` tag, and the tag is trustworthy — it agrees
exactly, in both directions, with the store's own `preorders` collection
restricted to vinyl. The tag is nonetheless never read.

The first draft appended ` (Pre-Order)` to the title, on the grounds that it
is a common convention among the bundled catalog crawlers and tells the user
the record is not shipping yet. Review on PR #323 pointed out what that costs.
`compute_item_key` hashes artist, title and URL, so a marker that disappears
when the record ships re-keys every one of that product's pressings at
exactly the moment a waiting user cares most, orphaning the saves and stock
judgments held against the old key. That is the same churn the colour rule
below refuses in as many words, and accepting it here would have been
inconsistent within one file.

The bundled crawlers are genuinely split on this — `counterintuitiverecords.py`
writes a marker, `earache.py`, `spkr.py` and `musiconvinyl.py` do not, and the
split runs right up to the present — so there was no settled convention to
defer to and the argument had to decide it. Stable identity wins: there is no
field in the yielded row for pre-order status anyway, so the choice was
between putting it in the identity and not surfacing it.

There is **no pre-order bypass**: a pre-order whose variant reports
`available: false` (`Lee Bains "Free South 2025" 12"`) is skipped like any
other unavailable variant. An unavailable pre-order is a closed allocation,
not a purchasable row.

### Prices and images

Prices are strings on every variant, every one of them numeric, ranging
$4.99–$49.99 with no zeros or nulls. They are parsed defensively anyway —
`bool` rejected before `float()` because `bool` is an `int` subclass and
`True` would price a record at 1; non-finite and non-positive results
rejected.

`currency` is hardcoded `"USD"`, confirmed from `meta.json`
(`"country":"US"`, `"currency":"USD"`, `money_format` `${{amount}}`) and
from `cart.js`.

Every product has at least one image. Covers resolve through
`shopify_catalog.resolve_cover_image()`, which prefers the variant's own
`featured_image` — the store does supply per-colour images — and falls back
to the product's first.

### Replay over the live catalog

Replayed over the fully-cached collection: 158 products → 140 rows across
92 artists. No `item_key` collisions, no blank artist or title, no
whitespace contamination, no malformed URL, no missing cover, no null price.
The 23 products that yield nothing are the ones whose every variant is sold
out.

## Drift guards

`db.replace_stock_items()` DELETEs a crawler's previous snapshot before
inserting, and `_sync_stock` only skips that call when the crawl **raised**.
A completed-but-empty walk is therefore destructive where a raise is inert.
Each guard names a distinct way the payload can stop carrying what this
crawler reads.

The field tallies are taken *before* the availability filter, so a sold-out
product still counts toward every one of them. `yielded` and `priced` are
necessarily counted after it, which is why the guards reading them are each
conditioned on a second tally rather than on emptiness alone — a shelf that
has simply sold out is empty legitimately.

| Guard | Raises when | Catches |
| --- | --- | --- |
| collection | `products_seen == 0` | the shelf renamed, removed, or the endpoint changed shape |
| artist-source | `artist_ok == 0` | `vendor` emptied store-wide — the sole artist source |
| combined-source | `sources_ok == 0` | both sources alive but never on the same product — a case neither row below can see |
| classification | nothing yielded, `unclassifiable > 0` | a source failed on *one* product, which the catalog-wide rows above cannot see |
| album-source | `parsed_ok == 0` | the store abandoning the quoted-album title convention. Tallies the album — the same value `_record` gates a row on — so it cannot raise on a catalog the crawler could in fact read |
| price-source | `yielded and not priced` | `price` removed or retyped store-wide |
| identity-source | `not yielded and identity_missing` | `title`/`handle` lost store-wide |
| stock-source | `not yielded and unreadable_stock` | `available` retyped store-wide |

Two structural notes on the tallies:

- **`artist_ok` and `parsed_ok` are tallied independently of each other**,
  because they are independent sources — `vendor` and the title — and
  conflating them would let one going dark hide behind the other still
  working. Independence alone, though, lets them be satisfied by *different*
  products: one with a vendor and an unreadable title, another with a
  readable title and no vendor, leaves both non-zero while no product carries
  what a row needs, and the walk completes empty. `sources_ok` counts the
  products where both co-occur and is what makes that case raise. It is taken
  *before* the format gate, so a shelf that legitimately filled up with CDs
  still satisfies it — those titles carry a vendor and a readable album too.
  Found in review on PR #323.
- **A title-less product is counted toward `identity_missing` before the
  format gate**, not inside it. `_record` reads the title, so a product
  without one can never reach the checks nested in the gate, and it cannot be
  classified at all — it might have been a record. Counted only inside, a
  *partial* loss of `title` would leave an empty walk looking like a shelf
  that merely sold out, and the snapshot would be deleted. Found in review on
  PR #323.
- **`identity_missing` and `unreadable_stock` are otherwise nested inside the
  format gate**, because only a product that reads as a record could have
  yielded a row; a shirt's missing handle says nothing about whether this walk's
  emptiness can be trusted. `unreadable_stock` uses `all()`, not `any()`: one
  readable variant does not make a product readable, or a product whose black
  pressing is a readable `False` and whose coloured pressing carries the
  string `"false"` would vouch for an emptiness half its own doing.

There is deliberately **no format-gate guard**. The gate is negative, so no
positive signal's disappearance can silently empty the walk — and a shelf
that legitimately filled up with CDs must not raise.

## Verification

`backend/tests/test_dongiovannirecords_crawler.py`, driving the real
`crawl_catalog()` against `respx`-mocked `products.json` pages. Fixtures are
marked at their definition as **captured** (live products from 2026-09-07,
trimmed to the fields the crawler reads), **altered** (a captured product
with one field changed to reach a branch the live data never takes), or
**invented** (a product exercising a guard the live catalog cannot).

Covered: the yielded item's exact fields; plugin identity; the title parse
across the live shapes and the ones it must reject; the artist coming from
`vendor` with no fallback to the title, including on the truncated-credit
product; the album-then-format composition and its prefix match against a
library title; the format gate admitting records and undeclared descriptors
and rejecting each medium and merch word; a title that names no format at
all being skipped while an unrecognised one is admitted; the store's own
books, pins and zine staying out when mis-shelved here; an album name
resembling another medium or a merch item not deciding the format,
including with no record word in the descriptor to admit it early; the
bundle rule; colour appended to every
row; the placeholder admitted only as a sole variant; the two split-across-
products records keeping distinct identities; sold-out variants skipped
beside in-stock siblings; only the literal `True` admitting; a tagged
pre-order yielding a row byte-identical to the one it would yield untagged,
and the sold-out pre-order skipped; the `+`-merch combo rule and the
record-plus-bonus-disc descriptor it must not swallow; the guard holes
found in review — a title-less product counted before the format gate, the
two sources satisfied by different products, a dropped variant that is not provably
sold out beside a readable sold-out sibling, in each of its unreadable
spellings; a per-product source failure beside a sold-out record, and the
classified skips (a CD, a bundle) that must not be mistaken for one; the nested-quotation
shapes the closing lookahead does not catch; price parsing
of every unusable shape; cover resolution and its fallbacks; URL
construction; pagination; and every guard above, each in both the raising and
the non-raising direction.

Each guard and rule was mutation-checked: the crawler was mutated once per
rule and the tests confirmed to fail.
