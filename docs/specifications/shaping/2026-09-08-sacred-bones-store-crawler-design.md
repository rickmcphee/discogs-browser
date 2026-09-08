# Sacred Bones Records crawler design

**Status:** implemented
**Date:** 2026-09-08
**Store:** https://www.sacredbonesrecords.com/collections/vinyl

## Problem

Sacred Bones Records' own webstore carries the Brooklyn label's catalog —
John Carpenter, David Lynch, Molchat Doma, Zola Jesus, Moon Duo, The Men,
Thou, Khanate, Uniform, Pharmakon, Marissa Nadler, Blanck Mass, SPELLLING,
Mort Garson, Jenny Hval, Boy Harsher, The Soft Moon, Alan Vega — alongside a
distro of kindred labels. None of that stock is covered by a bundled crawler,
so none of it reaches the Store tab and none of it is matched against a
user's library under the Store tab's Collection and Wantlist filters.

The store runs Shopify (`sacred-bones-records.myshopify.com`), so
`shopify_catalog.iter_products()` already implements the transport. The
payload is unusually tidy — `vendor` is a real artist credit on every product
and the title is the album alone — so the design work was not in reading an
identity out of it. It was in the medium, because of one structural fact:

**On this store a product is a *release*, not a pressing.** Its variants are
the formats the release came out in. So `/collections/vinyl` — a shelf of
products, not of formats — carries the CD, cassette, 8-track, Blu-Ray and
digital-download variants of every record on it. 513 of the shelf's 1,306
variants are not vinyl at all (measured live, 2026-09-08). The medium has to
be decided per variant, and the gate that does it is the whole design.

## Scope

**In:** a `catalog`-type plugin, `backend/crawlers/sacredbonesrecords.py`,
walking the store's `vinyl` collection over the public `products.json`
endpoint and yielding in-stock vinyl as stock items.

**Out:**

- CDs, cassettes, 8-tracks, Blu-Rays and digital downloads, including the
  ones shelved as variants of a vinyl release (below).
- Books, apparel, pins, posters, bags and the rest of the store, which live
  under other collections and are not walked. The handful of non-record
  strays shelved *inside* `vinyl` as extra variants — a guitar pedal, a run
  of art prints — are filtered out (below).
- The store's charity raffle, whose "variants" are $10 entries rather than
  pressings.
- Vinyl the store shelves outside the `vinyl` collection. Recovering it means
  walking `all` (1,013 published products, most of them merch, CDs and books)
  and gating on a title that says nothing about the medium — a far weaker
  signal than the shelf the request named. The store's own shelving decides.
- Any release-type (per-library-item) crawling of this store. This is a
  catalog source; the Store tab's own crawlers price its items.

## Technical grounding

Everything below was gathered live on 2026-09-08 by fully paginating the
store's `vinyl` collection at two page sizes, reading `meta.json`,
`collections.json` and `robots.txt`, and caching the payloads.

`robots.txt` allows the walk (`User-agent: * / Allow: /`); it asks agents not
to complete checkout, which this crawler never does. `meta.json` gives
`"currency":"USD"` and `"money_format":"${{amount}}"`, so `currency` is
hardcoded `"USD"`.

### Collection choice: `vinyl`

The request named `/collections/vinyl`. Paginating it returns 343 products
(two pages at `limit=250`, the second short), and the walk is stable: the
same 343 product ids come back at `limit=50` across seven pages, with no
duplicates either way. `collections.json` reports `products_count: 380` for
the shelf — it counts products not published to the online store — so the
walk's own exhaustion is the catalog, not that number. Page 101 answers HTTP
400, which is `shopify_catalog`'s documented ceiling and nowhere near
reachable here.

### The artist is `vendor`, with no fallback

`vendor` is a real artist credit on all 343 products: never blank, and only
ever the label's own name on the three products that are not one artist's
record (a mystery grab-bag, the label's own `Todo Muere SBXV` compilation,
and the raffle skipped below). 157 distinct vendors across the shelf.

The product title is the album alone — no `Artist - Album` convention to
parse and no vendor prefix to strip. Exactly one title on the shelf contains
` - ` at all (`Twin Peaks: The Return - Soundtrack`), and it is part of the
album name. 19 titles begin with the vendor, and every one of them is a
self-titled record (`Khanate` / `Khanate`, `Blanck Mass` / `Blanck Mass`)
where stripping the prefix would leave nothing. `strip_vendor_prefix()` is
therefore deliberately not used.

A product with no `vendor` is skipped rather than credited from its title.
There is nothing in the title to fall back to, and inventing a credit would
re-key rows whose `item_key` hashes the artist.

#### One reduction, and one deliberately not made

A split release's billing names every act on the record, but a stock row's
artist has to be the *first-billed* one to be matchable: `discogs.parse_release`
stores `artists[0]` and nothing else, and `db._library_release_match_sql`
compares artists with exact case-folded equality (only the title gets the
exact-or-prefix treatment). So a joined billing sits permanently outside the
Store tab's Collection and Wantlist filters.

A billing joined by a whitespace-flanked slash is therefore reduced to the act
before it — one live vendor, `The Men / Woods` on their Pickathon split. The
whitespace is required on at least one side, the repo's standard fix for this
bug class, so an act whose own name contains a slash (`AC/DC`) is not clipped
to its first half. Same rule and same regex as `counterintuitiverecords.py`'s
`_BILLING_SPLIT_RE`.

**`&`, `,` and `and` are deliberately not split**, though the shelf joins acts
with all three on 20 of its 157 vendors (`Uniform & The Body`, `John Carpenter,
Cody Carpenter, and Daniel Davies`, `Dean Hurley and Gloria de Oliveira`). Each
is also an ordinary part of a single act's own name, and nothing in the payload
separates the two readings — `Mandy, Indiana` is live on this very shelf, and a
comma split would credit it to `Mandy`. Reducing would silently break the match
for such a name, which is the failure with no signal to recover from; leaving
it joined costs a match the store's own spelling was unlikely to win anyway.
This is the same call `translationloss.py`'s `_artist` makes, on the same
grounds.

### The medium is decided per variant, and the gate is negative

The variant title is where the store writes the format, uniformly. That holds
even where the option *axis* is misnamed: the shelf's axes are `Format` on
the bulk of it and then `LP`, `Title`, `Variant`, `Edition`, `Color`, `Style`,
`2xLP` and one typo (`Varaint`) — but under every one of them the variant
titles still read `LP`, `CD`, `Digital Album MP3`. The axis name is never
read; the variant title always is.

**The gate is negative**: an explicit vinyl word admits outright, and what is
left is admitted unless something in the name rejects it — a merch word, or
another medium — rather than having to prove itself a record. (The full
ordering, which decides `12" x 12" Poster` against `10 INCH + CD`, is the
four tiers below.) It has to be negative, and this is the single most
load-bearing decision in the crawler. Sacred Bones names its coloured
pressings *by colour alone* — 50 distinct variant titles on the live shelf
carry no format word whatsoever
(`Lavender Swirl`, `Clear Pink`, `Sacred Bones Exclusive Black and White
Galaxy`, `Blue & White Galaxy`, `15th Label Anniversary Limited Edition Royal
Blue`, `Art Edition Red Fire`). A positive-only regex of the kind the
sibling Shopify crawlers use would drop nearly every store exclusive on the
shelf while keeping the plain black pressing beside it.

Run over all 1,306 live variants the gate rejects 513 across 55 distinct
titles, and every one of those 55 is genuinely not a record: CD (in fourteen
spellings, from bare `CD` through `Japanese Import CD with OBI-Strip` to
`"Diehard Edition" CD`), `Cassette`/`Tape`/`CASSETTE`/`Rave Case Tape`,
`8 Track`, `Blu-Ray`, the whole digital ladder (`Digital Album MP3`,
`Digital Download: WAV`, `Digital Single AIFF`, and two variants the store
titled with their raw SKU, `SBR-333-WAV` and `SBR-333-MP3`), plus the two
non-record strays discussed below. Nothing vinyl is rejected.

#### Digit-glued counts

`cd` and `cs` carry the same digit-glued-count lookbehind the vinyl words
carry, and for the same reason the repo already documents for `2xLP`: `\b`
matches nothing between `3` and `CD`. The shelf spells two of its box sets
`Limited Edition 3xCD Box Set` and `2xCS Box Set`, and a plain `\bcds?\b`
reads neither as another medium — it would publish a three-CD box set and a
two-cassette box set as records. This was found by running the gate over the
live shelf, not by inspection.

#### Four tiers, and why merch sits between them

The gate is four tiers, and the order between the middle two is the whole
point:

1. an explicit format word (`LP`, `vinyl`, `picture disc`, `test press`,
   `flexi`) admits outright;
2. a merch word rejects;
3. an inch marker admits;
4. anything left is admitted unless it names another medium.

Tier 1 above tier 2 is what keeps a record bundled with merch — `Limited
Edition Red Glitter Vinyl LP + Poster` is a record. Tier 2 above tier 3 is
the correction: **a measurement is not a format claim**. With the inch marker
sitting in tier 1, as one alternative of the vinyl-word pattern, a bare `12"`
admitted outright and published `12" x 12" Poster` as a record. Tier 4 stays
*below* tier 3 because there the pairing reads the other way round — `10 INCH
+ CD` is a record bundled with a CD, where `12" x 12" Poster` is a poster's
size. `spv.py` orders its own gate identically, for the same reason; this
matches it rather than inventing a shape. Found by Copilot in review on
PR #332.

The inch marker also allows an optional hyphen, because `12-INCH` is
established notation in this repo (`asianmanrecords.py`'s `_VINYL_TYPES`) and
`\s*` alone missed it. That only changes an outcome in tier 3 beside a media
word — `12-INCH + CD` would otherwise fall to tier 4 and be dropped as a CD.

Reordering changed no live classification: all 538 distinct variant names on
the shelf classify identically before and after.

#### Merch words

The gate names `poster`, `print` and `pedal` in tier 2. These
are the non-record strays the shelf actually stocks beside its records, as
extra variants of a record's own product: `Limited Edition hand numbered
posters designed by Grace O'Conner, limited to 150` ($45, on the *Only Lovers
Left Alive* soundtrack) and `Sacred Bones exclusive Boris Pedal` ($150, a
guitar pedal, on Boris' *W*). Both name no medium at all, so the default
branch would admit them.

The vocabulary is deliberately limited to the words the shelf actually
forced rather than a general merch lexicon. A word listed here rejects any pressing that merely mentions it, so
the cost of guessing wrong is losing a record — and the shelf carries no
apparel to justify `shirt` or `tee`. An explicit vinyl word still wins
outright, which is what keeps the pressings that come *with* one: `Limited Edition Smoke
Vinyl LP w/ Print Set` and `Limited Edition Red Glitter Vinyl LP + Poster`
are both admitted.

#### The gate reads the pressing name only

Never the product title. An album called `Cassette Tape` must not reject its
own vinyl, and the shelf's titles include `Prince of Darkness (7" + Blu-Ray
Box Set)` — a title naming two media at once, whose vinyl variants are still
records.

### Bundles are not excluded

Earache's crawler skips bundles. This one deliberately does not, because on
this store the same shape means something different. `Black Vinyl LP + 7"` is
not a bundle of two products — it is *the* standard pressing of Thou's
*Umbilical*, and the same is true of `Sacred Bones Exclusive Marble Smoke
Vinyl + Clear 7"`, `Gatefold LP+Zine` and `Sacred Bones Exclusive Midnight
Splatter LP + 7"`. Excluding on `+` is worse still: the store uses it to join
colours (`Limited Edition Blue+White LP`, `Limited Edition Red+White LP`) and
volume numbers (`Occult Architecture Vol 1+2`).

What remains — a $90 collector's edition, a $70 two-album box, a $150 record
plus NECA figure — is always an *extra* variant beside the plain pressings of
the same record, always dearer than them, and its row's title names exactly
what it is.

Those rows **do** survive the Store tab's Cheapest filter. Stating that
plainly because an earlier draft of this document claimed the opposite, and
the claim was wrong: `db._cheapest_clause` partitions on `(artist,
title_key, currency)`, and `title_key` deliberately keeps the words that say
*which* pressing a row is — colours, `exclusive`, `deluxe`, `collector` — so
a bundle whose name differs from the plain pressing's lands in its own
partition and is the floor of it. Measured over the live shelf, 328 of the
330 rows are alone in their Cheapest group. That follows from appending the
pressing name at all, which every sibling Shopify crawler does for the same
`item_key`-stability reason, rather than from anything about bundles.

They are kept anyway, because nothing in the payload separates a bundle from
a pressing on this store. `box set` cannot: `Limited Edition Dried Blood
Vinyl Box Set` and `Sacred Bones Exclusive Toxic Pumpkin Vinyl Box Set` *are*
the Halloween collection, and `3xLP Red Vinyl Boxset w/ 24 Page Booklet` is
the Xmal release. `bundle` catches two and misses the collector's edition and
the graphic-novel pairing. `+` is worse still, for the reasons above. What is
left is a hand-fitted list of one-off strings that goes stale the next time
the store runs a promotion, and whose failure mode is dropping a real record.
The accepted cost is one extra row per bundle, each named for exactly what it
is. Found by Copilot in review on PR #332.

### The raffle is skipped, on its tags

One live product is not a record in any variant: `Immigration Solidarity
Record Raffle`, whose four "variants" are $10 raffle entries for prizes the
store does not sell at $10 — including a test press and a signed poster
bundled with an LP, which the format gate would admit. The prize names
themselves say nothing (`Society 25`, `Society 26`).

It is skipped on its `Raffle` and `Donation` tags, which is where the store
says what the product is. Both tags are unique to it on the shelf. A skipped
product contributes no pressings, so it can never tally toward the identity
or stock guards below — a shelf that filled up with raffles is an empty
result, not drift.

### The row's title keeps the pressing, after the album

`{album} — {pressing name}`, matching the sibling Shopify crawlers. The
pressing is appended on every row that names one, not only when a product has
more than one: a sibling being listed or delisted must not re-title the
survivor and orphan the listings, judgments and saves keyed on its old
`item_key`.

It goes *after* the album because `db._library_release_match_sql` matches a
stock title against a catalog title with exact-or-prefix-with-space, which
`Belaya Polosa — Black LP` satisfies for a library `Belaya Polosa` only while
the album leads.

A variant that names nothing — Shopify's `Default Title` placeholder, or a
blank — carries the album alone, and only as a product's *sole* variant. On a
multi-variant product neither is a pressing, and a row built on either would
share its title and product URL, and so its `item_key`, with every sibling
built the same way. The live shelf has no placeholder variants at all; this
is the guard for a single-variant product the store adds later.

Those discards are *counted*, not silently dropped. A product all of whose
variants are discarded has no admitted pressings, so it reaches none of the
per-product tallies below — and Shopify dropping variant titles store-wide
would leave every multi-variant product in exactly that state, emptying the
walk with no guard firing. `_read_variants` therefore returns the count
alongside the pressings, and it feeds a guard of its own.

A variant whose `title` is a truthy non-string counts the same way. It would
otherwise reach `.split()` through `or ""` and raise, aborting the whole
source over one malformed entry — the opposite of the discard-and-keep-going
rule every other unreadable entry follows. An absent or null title stays
*nameless* rather than unreadable, which is the placeholder case above.

The `variants` collection itself counts the same way when it is absent,
emptied or retyped. A published Shopify product always carries at least one
variant, so none of those states is a product with nothing for sale — each is
a payload this crawler cannot read, and reading it as the former is what would
let the collection disappear store-wide in silence. A *skipped* product is the
one exception, answering empty on both counts: its variants were never meant
to be read, so counting them would raise on a shelf working exactly as
designed.

Across the whole live shelf the composed titles produce no `item_key`
collisions: no two admitted variants of one product share a name.

### Availability, and why there is no pre-order handling

`available` is a real bool on all 1,306 live variants (757 True, 549 False).
Only the literal `True` admits a variant: the string `"false"` is truthy, so
a falsiness test would publish a sold-out record as in stock.

There is **no pre-order bypass and no ` (Pre-Order)` marker**, though the
store does tag pre-orders `Flag_Preorder`. Its live pre-orders report
`available: True` — the tag marks a release that has not shipped yet, not a
variant that cannot be bought. So an unavailable variant on a tagged
pre-order is a closed allocation, not something to admit: on the tagged
*evidence* pre-order the wax-seal edition was already gone while its two
siblings were still selling. A marker would also re-title every row when the
tag eventually dropped, orphaning what hangs off the old `item_key`.

### Prices and images

Prices are strings on every live variant (`"21.00"`), ranging $1–$200 across
the shelf, with no zeros. `_price()` rejects `bool` before `float()` — `bool`
is an `int` subclass, so `True` would price a record at 1 — and rejects
non-finite and non-positive values.

Covers come from `shopify_catalog.resolve_cover_image()` unchanged: the
variant's own `featured_image` when it has one, else the product's first
image. Every live product has at least one image, and the coloured pressings
almost all carry their own, so a row usually shows the colour it is selling.

### Replay over the live catalog

Replaying the finished crawler over the fully-cached live shelf: 343 products
walked → 330 rows across 93 artists. No `item_key` collisions, no blank
artist or title, no malformed URL, no missing cover, no null price, no row
whose pressing name mentions another medium. Exactly one row's artist differs
from its raw vendor — `The Men / Woods`, the shelf's one split billing.

### One change to the shared iterator

`shopify_catalog.iter_products()` read `r.json().get("products", [])` and
stopped on anything falsy, so a missing, null or retyped `products` field was
indistinguishable from exhaustion. That is the one drift no crawler-side guard
can see: from the crawler's side the walk simply ended, so on a later page
`_sync_stock` replaces the whole snapshot with a partial prefix, and on the
first page with an empty result no guard can tell from a sold-out store.

An empty *list* is now the only exhaustion; anything else raises, which is
inert (`_sync_stock` skips `replace_stock_items()` on a raise). Found by
Copilot in review on PR #332. It is shared code touching every Shopify
crawler, so the change was made deliberately rather than scoped to this one:
the endpoint always answers `{"products": [...]}`, no bundled crawler's tests
mock a body without that key, and the whole Shopify crawler suite passes
unchanged.

## Drift guards

`db.replace_stock_items()` DELETEs this crawler's previous snapshot before
inserting, and `_sync_stock` only skips that call when the crawl *raised* —
so a completed-but-empty walk is destructive where a raise is inert. Each
guard names a distinct way the payload can stop carrying what this crawler
reads.

| Guard | Raises when | Why it is shaped that way |
| --- | --- | --- |
| collection | the walk saw no products at all | the shelf was renamed or removed |
| price-source | rows were yielded but not one carries a price | a `price` field removed or retyped store-wide would re-list the whole catalog priced at nothing, which is worse than the snapshot it replaces. Isolated nulls stay tolerated |
| artist-source | nothing was yielded *and* some product that would otherwise have produced a row has no `vendor` | `vendor` is the artist with no fallback, so such a product is skipped rather than credited from something else — and skipping leaves the walk looking sold out |
| variant-identity-source | nothing was yielded *and* some variant, or some product's whole `variants` collection, could not be interpreted | a product whose variants are all discarded has no admitted pressings, so it reaches none of the per-product tallies; without this guard, Shopify dropping variant titles store-wide empties the walk in silence. The message names both shapes because the count mixes them and they point at different sources |
| identity-source | nothing was yielded *and* some product that would otherwise have produced a row has no `title` or `handle` | `item_key` hashes the title and URL, so such a product is skipped rather than re-identified — and skipping leaves the walk looking sold out |
| stock-source | nothing was yielded *and* some such product has no readable `available` flag | an empty result is only trustworthy when every product that could have yielded a row was readable and simply out of stock |

All four drift tallies are taken *before* the availability filter, so a
sold-out product still counts toward them; `yielded` and `priced` are
necessarily counted after it, which is why the guards reading them are each
conditioned on a second tally rather than on emptiness alone — a shelf that
has simply sold out is empty legitimately.

Three of the four count *products*: artist, identity and stock each add one
per product, and their messages read "record(s)". `unreadable_variants`
counts *variants* — every entry discarded within a product, plus one for a
`variants` collection that could not be read at all — and its message reads
"variant(s)" to match.

Artist, identity and stock share **one bracket**, gated on the product having
admitted pressings, and each product counts once against the first reason that
applies. The gate is what keeps a CD-only product — or a skipped one, which
`_read_variants` answers empty for — from tallying toward anything: it would
never have yielded a row whatever its vendor said, so it can neither raise a
false alarm on a shelf that legitimately filled up with CDs nor vouch for one
that broke.

That last half is why the artist question is asked inside the bracket rather
than over every product walked, and it is the fix for a hole Copilot found in
review on PR #332. A tally taken outside the gate is satisfied by the raffle's
own vendor — or by any CD-only product's — while every record on the shelf has
lost its. Each record then yields nothing, reaches neither the identity nor the
stock tally, and the walk completes empty having passed every guard, at which
point `replace_stock_items()` deletes the snapshot. The bracketed form has no
such hole: a record that lost its vendor still has admitted pressings, so it
tallies, and an empty walk raises.

`_has_readable_stock_flag` uses every, not any: a product whose black
pressing is a readable `False` and whose coloured pressing carries the string
`"false"` yields nothing, and under any() the readable one would vouch for an
emptiness half its own doing.

**There is deliberately no format-gate guard.** The gate is negative, so no
positive signal's disappearance can silently empty the walk.

## Verification

`backend/tests/test_sacredbonesrecords_crawler.py`, `respx`-mocked, never
reaching the store. Fixtures are captured live products trimmed to the fields
the crawler reads; each says at its definition whether it is captured,
altered or invented.

Covered: the emitted field shape and plugin identity; artist from `vendor`
and title from the product title, including a self-titled record; the row
title leading with the album so it prefix-matches a library title; the format
gate over named records, colour-only pressings and every other medium the
shelf stocks; digit-glued `3xCD` and `2xCS`; a vinyl word beating a merch
word; the gate reading the pressing name and never the product title; the
raffle skip and its tag matching; pressings appended to every row; the
nameless-variant rule in both directions; whitespace collapsing; per-variant
identity; availability including the literal-`True` rule and the sold-out
pre-order; junk variant entries; a missing `vendor`, `title` or `handle`;
price parsing over unusable and usable values; cover-image resolution and
fallback; URL construction; pagination; the split-billing reduction and the
credits deliberately left whole (`AC/DC`, `Mandy, Indiana`, the `&`/`,`/`and`
forms); and every guard in the table above, in both the raising and the
non-raising direction — including the two holes Copilot found in review, a
vendor surviving only on a skipped or non-record product, and a shelf whose
variants all name no pressing.

Each guard and rule was additionally mutation-checked: mutating the crawler
once per row of that table, per gate branch and per composition rule fails
tests every time, and the file restores byte-identical afterwards.
