# Joyful Noise Recordings crawler design

**Status:** implemented
**Date:** 2026-09-09
**Store:** [joyfulnoiserecordings.com](https://www.joyfulnoiserecordings.com)

## Problem

Joyful Noise Recordings (`joyfulnoiserecordings.com`) is an Indianapolis label
and store whose catalog does not reach the Store tab at all. It carries
Deerhoof, Kishi Bashi, Joan of Arc, Tropical Fuck Storm, Swamp Dogg, Sebadoh
and Lou Barlow, alongside a body of records that exists nowhere else: the White
Label Series of one-off artist pressings, hand-made lathe-cut singles, the
flexi-disc series, and a deep run of test pressings.

The request named the store's front page, not a shelf.

## Scope

In: a `catalog`-type Shopify crawler over the store's whole published catalog,
scoped to vinyl, emitting one row per in-stock pressing.

Out: the store's CDs, cassettes, downloads, apparel, art objects and
memberships; its subscription products; and any lot that bundles more than one
release.

## Technical grounding

Every figure below was taken live on 2026-09-09.

### Collection choice: `all`, not the `vinyl` shelf

`meta.json` reports a `published_products_count` of 1044.
`/collections/all/products.json` returns exactly 1044 products, and exactly the
handle set of the store-wide `/products.json` — neither more nor less. So the
built-in all-products collection is the whole published catalog and
`iter_products()` can walk it directly.

That check was necessary rather than ceremonial, because the store **also**
publishes a hand-made collection titled "All". `collections.json` reports that
one at a `products_count` of 920, below the store's published total, so reading
that number alone would have argued that `all` was a curated subset and pushed
the walk onto a shelf. It is not: the built-in collection wins the handle, and
the walk's own result is the evidence.

The `vinyl` shelf was rejected on stronger grounds than that. It is exactly the
set of products tagged `Vinyl` — 256 published products, matching the tagged set
handle for handle — and that tag is not how this store decides what a record is.
271 products **outside** the shelf have an in-stock variant the format gate
below reads as vinyl. They are not edge cases: Surfer Blood's "1000 Palms"
(Sky Blue and Black Vinyl), Joan of Arc's "1984" (Yellow Vinyl) and Tall Tall
Trees' "A Wave of Golden Things" (Gold Vinyl) are ordinary catalog LPs tagged
only `InPress`, and the 7" singles, flexi-discs and test pressings sit outside
it almost entirely — of the products naming one anywhere in their title,
`product_type` or variant titles, 357 are off the shelf against 57 on it.

Both figures are measured over the catalog captured 2026-09-09, and each is
stated with the rule that produced it because neither survives a vague one:
the vinyl figure moves with the format gate itself, which eleven review rounds
changed, and the 7"/flexi figure moves by one or two depending on whether
variant titles are read. An earlier draft of this section carried numbers a
re-derivation did not reproduce, for exactly those reasons.
Walking the shelf would drop the larger part of the store's vinyl.

The cost is the extra paced pages a full walk fetches.

### Format lives in the variant, and nowhere else trustworthy

`product_type` names the *release kind*, not the medium: "Albums", "Singles",
"Test Pressing", "Box Sets", "Art Object", "Subscription". A single "Albums"
product carries the vinyl, the CD and the download side by side. Tags are worse
— many products carry none at all.

The medium is in the variant, under a `Format` option the store uses on the
bulk of its catalog. So the gate reads variant titles.

`product_type` is also actively misleading in the other direction, and this is
why it is not used even as a coarse exclusion: every White Label Series record
is typed `Subscription`, because that is how it was originally sold. Excluding
that type would drop a body of real records.

### The format gate is two layers, and neither works alone

The variant title is a **head** — the format's name — followed by the store's
blurb in parentheses.

Reading the **whole string** convicts every record of being a download, since
nearly every vinyl variant here says "+ Digital" or "Includes MP3 download".
Worse, it admits things that are not records: the digital edition of a box set
is titled `Digital (Includes MP3 and WAV downloads of all 5 LPs ...)`, and its
only vinyl word is in that blurb.

Reading the **head alone** loses real records. The store titles its hand-made
lathe-cut singles with the song — `Can't Let Go Juno (Hand-made lathe-cut 7"
limited to 100 copies)` — so for that whole series the head names no format and
the evidence is entirely inside the parenthesis. `Limited Edition Box Set +
Digital (3xLP on deluxe colored vinyl ...)` is the same shape.

So: **the head decides whether some other medium owns the product; the full
string then has to show vinyl.** A head that names vinyl outright is not vetoed
by a medium sitting beside it (`Hardbound Book + 7"`, `Red Vinyl + 7"`).

Two refinements to that, both found by Copilot in review on PR #337:

- **A trailing `+ Digital` is cut from the head before the head is judged.**
  It is the download that ships *with* the physical item, not the item, and
  leaving it in makes the download convict its own record: `Limited Edition
  Box Set + Digital (3xLP on deluxe colored vinyl ...)` is a live pressing
  whose head names no vinyl, so the veto fired and the blurb never got to
  answer — the exact shape this section cites as one the head alone cannot
  judge. The cut is anchored on the joining `+`/`&`, so a variant that *is*
  the download keeps its head whole and stays vetoed (`Digital (...)`,
  `MP3 Download (...)`).
- **When the head names no medium at all** — a bare container like `Box Set`,
  or the song, which is how the lathe-cut singles are titled — the blurb
  answers, but a competing *physical* medium there vetoes. Only physical ones
  can: every legitimate record's blurb names a download, so the download words
  would veto everything. This is what keeps out a box of ten cassettes whose
  lid doubles as a playable lathe-cut single.

These details of the patterns are load-bearing:

- **The inch marker is restricted to record sizes** (5, 7, 10, 12). An
  unrestricted one admits `18"x24" Poster` and a tote bag measured `15"W x
  16"H` — both live listings.
- **A pair of inch marks joined by an `x` is a measurement, and is dropped
  before anything looks for vinyl.** The size restriction above is not
  sufficient on its own, because merchandise is routinely sold *at* record
  size: `12"x12" Poster` names a size the crawler would otherwise read as
  proof of vinyl. The live listing that prompted the size restriction happened
  to fall outside it, which hid the hole until Copilot found it in review on
  PR #337. Dropping the measurement rather than rejecting the variant outright
  keeps a record that merely states its dimensions, since the strong words are
  still there to be found; and a disc count survives untouched, because
  `4x10" Vinyl Box Set` carries no inch mark on the `4` — it counts discs
  rather than measuring one.
- **The disc counts take a multiplier prefix** (`\d*\s*[x×]?\s*cds?`), because
  there is no word boundary inside `5xCD` and a plain `\bcds?\b` reads straight
  past it. `5xCD Box Set (... an elaborate 12"x12", 27 page bound-book)` is a
  live listing whose only inch marker measures the book; without the prefix
  nothing vetoes it before that `12"` admits it as a record. This was found by
  a test asserting the rejection the prose already claimed.
- **The token boundaries are Unicode-aware, and the inch marker is closed on
  both sides.** `[a-z]` is ASCII-only even under `IGNORECASE`, so a lookbehind
  spelled that way treats an accented letter as a separator: `ÉLP CD` matched
  the embedded `LP` and was admitted before the `CD` could reject it. And the
  inch alternative had no closing boundary at all, so `12"CD` read `12"` as a
  complete marker and won the same way — and then had no *opening* one either,
  so `Studio12" CD` matched the glued marker and won a third time. The left
  boundary sits before the **whole** marker with the multiplier absorbed into
  it: placing it before the size digits instead would block `2x12"`, whose
  digits follow the letter `x`, and lose every multiplier descriptor the store
  uses. `dongiovannirecords.py` composes its own marker that way for that
  reason. Both matter because the gate takes a
  vinyl head *at its word* — a spurious match there is not merely noise, it
  pre-empts the veto. Found by Copilot in review on PR #337;
  `dongiovannirecords.py` had already solved this class in PR #323, and its
  comments name the exact trap of making the left boundaries Unicode-aware
  while leaving the right one ASCII in the same commit.
- **The medium veto's boundaries are Unicode-aware too**, and they were not in
  the commit that fixed the vinyl ones — a gap worth recording, because the
  reasoning that produced it was wrong rather than merely incomplete. It ran:
  the gate tests vinyl first, so a loose veto boundary can only reject
  something that had no vinyl word anyway. That holds only when the vinyl word
  is in the head. It is false for precisely the shape this crawler exists to
  support — a lathe-cut single titled with the song, whose format lives
  entirely in the parenthesis — because the head veto runs *before* the blurb
  is read. So `CaféCD (7" vinyl)` had its embedded `CD` read as the head's
  medium and the record was dropped, while the unaccented `CafeCD (7" vinyl)`
  was kept: one accent, opposite classifications. Found by Copilot in review on
  PR #337, in the round after the vinyl-side fix.

Titles are normalised to **NFC** in `_text` before any of this runs, and that
is part of the same rule rather than tidiness. The boundaries ask whether a
letter sits beside a token, and in decomposed text the neighbour is a combining
mark rather than the letter it belongs to — so `éLP CD` classified as vinyl
spelled one way and as a CD spelled the other. One descriptor must not read two
ways depending on its encoding. NFC is canonical, so any two spellings of a
string share a form; it does not compose every mark that exists, but it makes
the reading consistent, which is the property that was missing. Every live
title is already NFC, so this re-keys nothing.

`_text` also **unescapes HTML entities**, and the format gate depends on that
too. `_COMPANION_RE` joins on `[+&]`, so a literal `&amp;` had its `&`
consumed and then failed to find the medium word after it — the companion
clause went uncut and `Limited Edition Box Set &amp; Digital (3xLP …)` was
rejected on its own `Digital` before the blurb could answer. Unescaping in the
shared reader rather than in that one pattern also keeps an entity out of a
displayed title and out of `item_key`. `asbestosrecords`, `darkdescentrecords`,
`dischordrecords` and `spkr` all do the same; `spkr` puts the reason best —
the row is read by a person, not a browser. No live variant title carries an
entity today, but `spkr` documents neighbouring stores that do.

The gate is **positive**: a variant must show vinyl to be admitted. That is the
opposite polarity from the crawlers whose shelf has already vouched for the
medium, and it has a consequence the drift guards have to carry — see below.

### Multi-release lots

A lot of more than one release is excluded. Its price is not any single
record's price, and the row would attach that price to whichever album it was
shelved under. The store's clearest case: a $270 "Triptych Box Set" of three
albums is a variant of each album it contains *and* a product of its own, so it
would otherwise appear four times, against records whose own LP sells for $35.

`Box Set` alone is deliberately **not** a bundle word — the store sells single
releases that way (`4x10" Vinyl Box Set`; WHY?'s "Moh Lhean - Expanded" as
eight 7"s in a box). The excluded wording is `bundle`, `grab bag`, `lucky dip`,
`complete box set` and `full set`, plus `club`. `Complete Box Set` always names
a whole series or discography here (Joan of Arc's first five albums, the Gray
Area cassette series, Danielson's lathe-cut club).

The bundle test runs against the **product** title as well as the variant,
because the store sometimes puts the lot in the product name and a plain format
in the variant (`Danielson Artist Enabler Club One-Time Payment` /
`15 lathe-cuts + Wooden Box + Digital`).

The Triptych box is caught by the format gate rather than by that list, though
not as directly as it was before the companion cut above: with `+ Digital` gone
from its head, the blurb *is* read, and what keeps it out is the other physical
goods the box is described as holding (booklets, a signed poster). That is a
weaker guarantee than the bundle wording, and worth stating as the limit it is:
a lot whose blurb named nothing but records, and which the store did not call a
bundle, would not be caught. None exists in the catalog today.

### The credit: `vendor`, unless it names a series

`vendor` is the artist across the bulk of the catalog — unusually reliable for
a store this size. The exception is the White Label Series, where `vendor` is
the string `White Label Series` and the artist appears only in the product
title, as `Artist 'Album'`.

So the parse fires only where `vendor` demonstrably is not the artist: the
title must match `Artist 'Album'` **and** the vendor must not appear anywhere
in the title. Where the two agree — the store also quotes albums on products it
vendors correctly — deferring to `vendor` costs nothing and risks nothing.

Measured against the live catalog: all 36 in-stock series products parse to the
right artist, and the rule fires on no other product in the catalog.

The closing quote must be followed by whitespace or the end of the string. That
is what stops an apostrophe *inside* the album closing it early: in the live
`Ambulances 'Frankie Bacon’s Blue, Blue Heart'` the inner apostrophe is followed
by `s`, so the lookahead refuses to close there and the album survives whole.
(Earlier drafts of this section wrote that title with a straight `'` where the
store uses a curly `’`. The rule turns on what *follows* the mark rather than on
the glyph, but quoting a live title inaccurately is worth not doing.) The artist
group excludes quotes outright, so the album's opening quote is always the
title's first.

The rule is a heuristic, not a guarantee, and the limit is worth stating: an
inner quote that *is* followed by whitespace — a possessive plural, say — does
close the album early, so `Ambulances 'The Beatles' Greatest'` would parse to an
album of `The Beatles Greatest'`, carrying the stray quote into it. No title in
the catalog has that shape: of the 89 products where this parse fires in the
2026-09-09 capture, none misparses. Copilot raised this in review on PR #337
against the `Bacon’s` title, where it does not apply — that apostrophe is
followed by a letter, and the parse there is correct — but the shape it points
at is real, and preferring the last eligible quote is what would close it if a
title ever needs it.

A title that opens on the quote (`'Emerald Sea' (Test Pressing)`) has no artist
ahead of it, does not match, and keeps its vendor.

### The row title

`{album} — {descriptor}`, where the descriptor is the variant's **whole title**
— the same choice every sibling crawler makes.

The album leads because the Store tab's Collection and Wantlist filters match
the library exact-or-prefix-with-space against the catalog title.

The descriptor was first the variant *head*, with the store's blurb dropped,
because this store's blurbs run to whole paragraphs: measured over the live
catalog on 2026-09-09, trimming takes the median descriptor from 69 characters
to 22 and the longest from 255 to 83. That readability was not free, and the
bill fell on identity.

`item_key` hashes the row title, so trimming can collide two of a product's
variants and silently overwrite one row with the other. The crawler guarded that
by falling back to the untrimmed titles whenever a product's heads collided —
which made a pressing's title, and so its identity, a function of its
**siblings**. Five products collide on the live catalog (an earlier version of
this document asserted that none did; that was simply wrong), and two of them
pair an in-stock variant with a sold-out one trimming to the same head. The
store deleting that dead variant — routine housekeeping — would flip the
survivor from its full title back to the trimmed one, change its `item_key`, and
orphan the saves, judgments and listings keyed on it. Copilot found this in
review on PR #337.

Using the whole title always is what the collision fallback already reached for,
so it costs only the readability and buys a descriptor that is a pure function
of one variant. Carrying a variant-derived key *separately* from a trimmed
display title would be the other way out, and it is the one this crawler cannot
take alone: the catalog contract has no field for it, `item_key` is computed
downstream from artist, title and URL, and every crawler in the repo keys its
URL on the product handle rather than the variant.

### Availability and price

`available` is a real bool on every variant in the catalog, and it agrees with
the wording: all 60 variants whose titles announce `[SOLD OUT]` or `We are SOLD
OUT` carry `available: false`. So the flag is the only stock signal read, and
only the literal `True` admits — the string `"false"` is truthy.

Prices are strings. A variant with no usable price is **skipped rather than
listed blank**, which departs from the sibling crawlers and is worth stating
plainly. Every unusable price in this catalog is an internal placeholder rather
than a record: two "VIP LATHE TEST" products, and a duplicated product (handle
`copy-of-...`) whose vendor and product_type are both the literal string
`hidden`. That last one would otherwise reach the Store tab crediting an artist
named "hidden", under a price nobody can act on.

**"Unusable" and "unreadable" are different, and the price guard counts only
the second.** A zero reads perfectly well — the store means it — so the
placeholders above are prices the walk declines to *use*, not prices it cannot
*read*. Counting them as drift evidence, as this crawler first did, left the
tally permanently at the number of placeholders; a guard gated on *nothing
yielded* then fires on every empty walk, including the honest one where the
catalog has simply sold out, and pins a stale snapshot in place on exactly the
payload it exists to let through. Absent, retyped and non-finite is the shape
a price field actually breaks in, so those alone are counted — which also puts
the price guard in step with how the stock and variant guards read their own
sources. A store-wide break that expressed itself as zeros rather than as
unreadable values would therefore record the empty snapshot instead of raising;
that is the deliberate cost of not having the guard fire on the store's own
data, and the rows it would drop are unbuyable either way.

The bare `VIP` variant — the members' slot, priced like a pressing but naming
no format — is excluded by the positive gate rather than by a rule of its own.
A negative gate would publish it as vinyl.

## Drift guards

`replace_stock_items()` DELETEs this crawler's previous snapshot before
inserting, and `_sync_stock` only skips that call when the crawl raised — so a
completed-but-empty walk is destructive where a raise is inert. Each guard
names a distinct way the payload can stop carrying what this crawler reads.

| Guard | Fires when |
| --- | --- |
| collection empty | the walk returned no products at all |
| variant-source drift | nothing yielded, while products carry no readable `variants` |
| title-source drift | nothing yielded, while variants not known to be sold out carry no readable title |
| artist-source drift | nothing yielded, while records carry no artist |
| format-source drift | no product has a variant naming a vinyl format |
| price-source drift | nothing yielded, while in-stock records carry a price that cannot be read at all |
| identity-source drift | nothing yielded, while records carry no title or handle |
| stock-source drift | nothing yielded, while records carry no readable availability flag |

The **format-source guard has no counterpart** in the crawlers whose format
gate is negative, and it is the one this design most needs. Because the gate is
positive, the store moving format out of the `Format` option — into
`product_type`, tags, or a metafield — would empty the walk in silence rather
than merely admitting too much. Nothing else notices that.

The variant guard is the one that cannot be nested inside the record branch,
and that is precisely why it is needed. `_pressings` reads `variants` through
`_raw_variants` and drops non-mappings, so a product whose collection is absent,
retyped, empty or holds no mapping yields no pressings — indistinguishable, to
every tally inside that branch, from a product that simply stocks no records.
A single readable sold-out record elsewhere then keeps `format_named` non-zero,
and the empty walk is waved through to delete the snapshot. Counted per product
before the branch, and checked *before* the format guard so that a
store-wide break names the upstream cause rather than the format gate it
starved. Copilot found this in review on PR #337 — the same shape of hole as
the artist tally, one guard later.

**Both readers of `variants` go through `_raw_variants`**, and that sharing is
load-bearing rather than tidiness. The guard tested `isinstance(..., list)`
while `_pressings` iterated `product.get("variants") or []`, and a truthy
scalar (`1`, `true`, `2.5`) takes the two apart: it survives the `or`, is not
iterable, and raises `TypeError` out of the comprehension before
`variant-source drift` can report — so the tally counted the product unreadable
while the path meant to tolerate it crashed on it. An isinstance test rather
than a try/except, because the opposite hazard is equally real: a string or a
dict *is* iterable, and iterating one invents entries out of characters or keys
instead of failing. `theflenser.py` grew the same helper for the same reason on
PR #331, and its docstring already recorded that sharing the derivation is what
stops the two readers diverging; this crawler had to learn it again.

**The title guard is the variant guard one level down**, and it exists because
`_has_readable_variants` answers a question about the *collection* rather than
about each variant in it. A mapping with a blank or retyped title is a perfectly
readable member of a readable list, so the product passes that guard — while
`_pressings` drops the variant for having no title, and drops it *before* it
ever reads `available`. Nothing counted that drop. Titles going blank across the
store's in-stock variants would therefore empty the walk while a single sold-out
sibling with an intact title held `format_named` above zero, and the
completed-but-empty walk would delete the snapshot. Copilot found this in review
on PR #337.

Only a literal `available: False` excuses an untitled variant. A sold-out
variant the store stopped maintaining is a dead row; anything else — in stock,
or a flag this crawler cannot read — is a record it failed to see, and the
sold-out sibling must not vouch for it. The guard is checked before the format
guard for the same reason the variant guard is: when titles break store-wide
both conditions hold, and "no variant names a vinyl format" names the gate that
was starved rather than what starved it.

Reading the title goes through `_variant_title`, which tests `isinstance(...,
str)` before `.split()`. A retyped title is truthy, so `or ""` passed it
straight through and `.split()` raised `AttributeError` — the same shape as the
truthy scalar `variants` one level up, aborting the source before any guard
could name it. That was found while reproducing the blank-title case rather than
reported.

**Every string field goes through `_text`**, and the product-level fields were
brought into line with the variant title a commit after it rather than with it —
which is the more useful fact about this, so it is recorded rather than tidied
away. Hardening `_variant_title` alone left `title`, `vendor` and `handle` still
read through `or ""`, so a truthy non-string still reached `.strip()` or
`.split()` and aborted the whole source over one malformed product. Answering
`""` instead routes that product into the identity and artist tallies, so it is
skipped **and** counted: a store-wide retyped `title` or `handle` now raises
`identity-source drift`, a retyped `vendor` raises `artist-source drift`, and
one malformed product among real rows stays an ordinary skipped row. The raise
it replaces was not fail-safe, merely unexplained — it did stop
`replace_stock_items()`, but by crashing rather than by any guard judging the
payload untrustworthy.

**Artwork is guarded separately, and does not cost the row.** `resolve_cover_image`
reaches into `variant["featured_image"].get(...)` and `product["images"][0].get(...)`
behind `or` guards, which catch a missing or null field but pass a retyped one
straight through. `_cover` type-checks both collections first, so a record whose
image field is a string still lists — with the product image, or with none. The
proportion is the argument: artwork is display-only, and letting it abort the
refresh would leave every price in the snapshot stale because one record's
`images` was a string. Guarded in this crawler rather than in `shopify_catalog`,
because every Shopify crawler in the fleet reads that helper and this is one
store's payload; `monorailmusic.py` draws the same boundary for the same reason.

The artist guard counts only products that **are** records, and only fires on
an empty walk. A tally taken over every product instead would be satisfied by
the merch and CD-only products that can never yield a row: they would keep
their vendor while the records lost theirs, and the guard would wave through a
completed-but-empty walk that deletes the snapshot. Copilot found that in
review on PR #337, along with the case where one credited but sold-out record
vouches for a vendorless available one.

The artist, price, identity and stock guards are gated on having yielded nothing, so an isolated unreadable
product among real rows stays an ordinary skipped row, and a store that has
simply sold out still records an honest empty snapshot. The stock guard counts
*unreadable* products rather than readable ones, so one genuinely sold-out
record cannot vouch for a catalog that has gone unreadable behind it.

## Verification

Replayed over the live catalog captured on 2026-09-09: 462 rows, every one
priced, every one carrying a cover image, and all 462 identities distinct. No
row names a poster, tote, cassette, CD or download; no row is credited to
`hidden`, `White Label Series` or the label itself.

Every review finding on this branch was confirmed against the code before being
fixed, and none of the fixes changes the live result: the replay still produces
the same 462 rows. Each has a regression test.

Two of them are worth separating from the rest, because the replay could not
have caught either and did not. The companion-download rejection was a *latent*
loss: every variant of the dropped shape was sold out at capture, so the gate
could reject a documented in-scope pressing without moving a single row. The
unreadable-`variants` hole was a guard that has never fired and, on today's
payload, never will — every live product carries a clean list. Both were found
only by reading the code. Replaying against a captured catalog demonstrates
what the crawler does with the payload it has; it says nothing about what the
guards do when that payload changes, which is the only thing the guards are
for.

Unit tests mock the products endpoint with `respx` and never reach the store.
