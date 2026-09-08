# The Flenser crawler design

**Status:** implemented
**Date:** 2026-09-08
**Store:** https://nowflensing.com/collections/vinyl (walked at `/collections/all`)

## Problem

The Flenser is a San Francisco label and webstore for experimental black
metal, doom, shoegaze and dark post-punk. It carries its own catalog — Chat
Pile, Have a Nice Life, Agriculture, King Woman, Giles Corey, Planning For
Burial, Bosse-de-Nage, Midwife, Uboa, Ragana, Wreck and Reference — alongside
a large heavy-underground distro pulling from Profound Lore, 20 Buck Spin,
Tartarus, Dark Descent, Relapse, Sentient Ruin, Gilead Media, Southern Lord
and Sacred Bones, plus indie shelves from Kranky, Numero Group, Constellation,
Thrill Jockey, Sub Pop and Dead Oceans. None of that stock is covered by a
bundled crawler, so none of it reaches the Store tab and none of it is matched
against a user's library under the Store tab's Collection and Wantlist filters.

The store runs Shopify (`nowflensing-com.myshopify.com`), so
`shopify_catalog.iter_products()` already implements the transport. What
needed deciding was which shelf to walk, how to read an artist out of a title
convention no sibling crawler shares, and where the format gate has to sit.

## Scope

**In:** a `catalog`-type plugin, `backend/crawlers/theflenser.py`, walking the
store's `all` collection over the public `products.json` endpoint and yielding
in-stock vinyl as stock items.

**Out:**

- CDs, cassettes, apparel, books, banners, gift cards, subscription
  memberships and shipping upgrades, all of which sit in the same collection
  and are excluded on their `product_type`.
- The store's scratch-and-dent bin and its multi-record bundles — see
  "Descriptor gate" below.
- Any release-type (per-library-item) crawling of this store. This is a
  catalog source; the Store tab's own crawlers price its items.

## Technical grounding

Everything below was gathered live on 2026-09-08 by fully paginating both the
`vinyl` shelf and the `all` collection over `products.json`, cross-checking
the shelf against its own paginated HTML, reading `meta.json`,
`collections.json` and `robots.txt`, pulling the store's other
vinyl-named shelves for comparison, tabulating every product type, tag,
title, descriptor and variant title in the catalog, and caching the payloads
so every rule below could be replayed against them.

### Collection choice: `all`, not the `vinyl` shelf the request named

The request named `/collections/vinyl`. That shelf walks cleanly — 278
products over two pages at `limit=250`, and the same 278 handles its own
paginated HTML lists across six pages, so nothing is repeated or skipped and
the ceiling in `shopify_catalog.iter_products()` is nowhere near being
reached. It is nonetheless **not** the store's records:

| | Products |
| --- | --- |
| `vinyl` shelf | 278 |
| Store-wide products carrying a `vinyl` `product_type` segment | 281 |
| Vinyl-typed products **absent from the shelf** | 3 |

The three are Bismuth "The Eternal Marshes" LP, Glitch "Towards The Gutter"
LP and Filmmaker "Multiverse Nightmare" LP — all Tartarus distro, all
published within four minutes of each other on 2025-10-17, all in stock at
$23, all tagged `Vinyl`, and none of them on the shelf. The store's curation
of that shelf has already fallen behind its own taxonomy once, and a shelf
walk would silently inherit every future instance of that.

`all` is Shopify's built-in all-products collection. It returns 754
products, exactly the `published_products_count` in `meta.json` and exactly
the same handle set as the store-wide `/products.json`, and it is a strict
superset of every vinyl-named shelf:

| Shelf | Published products | Note |
| --- | --- | --- |
| `vinyl` | 278 | misses the three above |
| `used-vinyl` | 0 | `collections.json` counts 12; none published |
| `test-pressings` | 0 | `collections.json` counts 132; none published |
| `faetooth-vinyl` | 1 | a subset of `vinyl` |

The cost of `all` over the shelf is the pages the walk fetches — four rather
than two, since the shelf is a fraction of the catalog. That is two extra
paced requests per stock sync, against three records the shelf would lose
today and an unknown number later. `collections.json` reporting `vinyl`'s
`products_count` as 1051 against 278 published is the usual gap — it counts
products not published to the online store — and is not a shortfall to chase.

`robots.txt` disallows only sort, filter and language-picker crawl traps
under `/collections/`; the `products.json` path this crawler requests is not
among them.

`meta.json` reports `"currency":"USD"` and `"country":"US"`, so `currency` is
hardcoded `"USD"`.

### Format gate, layer one: `product_type`

Store-wide, `product_type` is a clean taxonomy: `Vinyl`, `CD`, `cd`,
`Apparel`, `Tapes`, `Book`, `Banner`, `Membership Series`, `Upgrade`, `The
Flenser Gift Card`, and a handful of comma-joined values that name a shelf
alongside the format (`Vinyl,Distributed titles`, `Vinyl,Flenser Releases`,
`Flenser Releases,CDs`, `Distributed titles,CDs`,
`Clearance,Flenser Releases,CDs`).

The gate therefore tests **per comma-separated segment**, not the whole
string:

- An equality test drops the three records typed `Vinyl,Distributed titles`
  and `Vinyl,Flenser Releases`.
- A substring test admits anything whose type merely contains the word —
  `Vinyl Accessories`, say, if the store ever adds one.

It also enumerates positively, so a type the store adds later stays out by
default.

**It reads `product_type` and not `tags`, and the payload is unambiguous
about which is right.** The tag is wrong in *both* directions live:

| Product | `product_type` | `tags` | Is it a record? |
| --- | --- | --- | --- |
| Flenser Membership - Series Nine - Vinyl Edition | `Membership Series` | `["vinyl"]` | no |
| Hum "Inlet" DLP | `Vinyl` | `[]` | yes |
| Kathryn Mohr "Waiting Room" LP | `Vinyl` | `[]` | yes |

The membership is also the only product in the whole store whose *title*
names a record format while its type does not, so it is the one case a
title-driven gate would get wrong too. `product_type` is right on all three
and on every other product in the catalog.

### Title convention: `Artist "Album" <format>`

Every single-item product in the store follows it. Of the 281 vinyl-typed
products, 280 carry exactly two quotes and parse; the one that does not is
`Mamaleek Vinyl Bundle`, which carries none. Every quote in the catalog is a
straight one; the store writes no curly ones.

**There is no fallback source for either half.** `vendor` on this store is
the *releasing label* — dozens of distinct values across the vinyl-typed set,
from `The Flenser` itself down to one-off distro labels — and never the artist,
unlike the sibling Shopify label stores (`counterintuitiverecords.py`,
`deathwishinc.py`) where a vendor fallback is reasonable. A vendor fallback
here would credit every unparseable title to a record label, so a title that
does not parse yields no row at all. That is also what excludes the store's
one vinyl-shelved bundle without any bundle-specific rule firing.

The regex's two character classes are asymmetric on purpose. The artist group
excludes only the characters that can *open* a quotation, which makes the
album's opening quote the first quote in the string, so a descriptor's own
inch marker can never be read as one; the album group excludes every quote
there is, so a fourth quote lands in the descriptor rather than in the album.
Curly quotes are admitted though the store writes none — the commonest way a
storefront's copy drifts, and admitting them costs nothing. Every quote
character the crawler knows about is named once and the two subsets derived
from it, because spelled out separately they drift and every such
disagreement has been a bug (`dongiovannirecords.py` documents the same trap).

**A nested quotation is rejected rather than guessed at**, which the character
classes alone do not achieve. The album group stopping at an inner quote is
only half the answer: `Artist "The " Big" LP` parses to an album of `The` and
a descriptor of `Big" LP`, and that descriptor still names a format, so the
gate downstream waves it through and the row is keyed on a truncated title.
**The descriptor carries no quote glyph at all.** That is the rule now, and
it arrived by elimination — three review rounds tried to say what a quote in a
descriptor is *allowed* to be, and each closed one instance of the same shape
while leaving the shape open:

- **The inch marker needs a right-hand boundary.** Without one the fragment
  matched the leading part of `12"CD`, `7"Cassette` and `12inchesPoster`,
  reading a compact disc as a record and accounting for that quote — so
  `Artist "The " 12"CD` passed and was emitted under the truncated album
  `The`.
- **A cap of one quote is not redundant with a position check.** Two
  separately valid markers account for both their quotes, so
  `Artist "The " 54" 12"` passed on the strength of a `54"` that is really the
  album's closing quote followed by junk.
- **And one quote inside one valid marker still isn't proof.**
  `Artist "The " 12" LP` and `Artist "Album" 12"` are the *same shape* —
  three quotes, the last inside a legitimate inch marker. No rule reading only
  the descriptor can separate them, because the string genuinely does not
  determine which reading was meant.

So the glyph is refused outright, and the ambiguity with it. A title this
crawler cannot read unambiguously yields no row, which is the answer the
nested-quote rule already gave — now applied to the case that kept slipping
past it. The cost is a descriptor written `12"`, which this store does not
write: it spells every inch size out (`10inch`, `7inch`), and those still
parse, because a spelled-out unit cannot be mistaken for a closing quote.

That trade was weighed the other way in an earlier round — keep the glyph
rather than lose a hypothetical `12"` — and that was wrong. It bought a format
the store never uses at the price of a hole three rounds could not close.

Variant titles are unaffected by this rule: they are never split into album
and descriptor, so no quote in one is refused, and one still reads as an inch
marker. That is a different decision, not a claim that the glyph is
unambiguous there — it is exactly as ambiguous, and the Variant gate section
below records the false positive that follows and why it is accepted.

**The format and variant gates read mark-folded text.** `\w` excludes the
combining MARK categories, so a decomposed accent opens a boundary its
precomposed equivalent closes: in NFD, `Artist "The " É54" LP` let `54"` read
as an inch marker while the canonically equivalent NFC spelling was rejected —
and on the variant gate, `éCD` classified as a record in NFC and as another
medium in NFD. A string must not read two ways depending on how it was
encoded. The fold replaces each mark with a letter, so it can only ever
*close* a boundary, never open one, and it is decision-time only — nothing
emitted is ever folded. `dongiovannirecords.py` and `title_key._words`
document the same trap.

The quote rule above does **not** fold, and the difference is the point:
folding exists to stop a mark opening a `\w` boundary, and "does this string
contain a quote" has no boundary to open. It did fold while the rule was still
marker-aware and compared offsets; keeping the call afterwards would have been
a no-op dressed as a precaution, which is what a surviving mutation revealed
it to be.

The **emitted** identity is normalised too, and for a different reason that is
easy to conflate with the fold: `compute_item_key` hashes the artist and title
raw, and `db._library_release_match_sql` compares them with a plain
`LOWER(...)`, neither of which normalises. An NFD album would therefore fail to
match an otherwise identical NFC catalog title under the Store tab's Collection
and Wantlist filters, and would lose its `item_key` if the storefront ever
changed which spelling it served. Rows are composed to NFC on the way out —
never folded; the stand-in must not reach a row. Every live row is already NFC,
so this changes nothing today and exists to keep it that way.

Found in review on PR #331 over successive rounds; no live title is shaped any
of these ways.

### Billing reduction: the slash only, never the ampersand

A stock row's artist has to be the *first-billed* one to be matchable:
`discogs.parse_release` stores `info["artists"][0]["name"]` and nothing else,
and `db._library_release_match_sql` compares artists with exact case-folded
equality (only the title gets the exact-or-prefix-with-space treatment). A
joined billing can therefore never match a library release.

The store bills these records to more than one artist:

| Separator | Live billings |
| --- | --- |
| `/` | Botanist / Oskoreien, Deathgrave / Black Ganion |
| `&` | Bell Witch & Aerial Ruin (×2), Chat Pile & Hayden Pedigo, Efrim Manuel Menuck & Kevin Doria, Matt Jencik & Midwife, Midwife & Vyva Melinkolya, Ragana & Drowse, Throwing Bricks & Ontaard, Uboa & Whitehorse |

Every live ampersand is in fact a collaboration, so reducing on it would be
right on every one of them today — and that is not enough. An ampersand is also how
plenty of *single* acts spell their own name (Belle & Sebastian, Iron & Wine,
both on labels this store already distros), and clipping one of those would
not merely miss a library match, it would credit the row to a band that did
not make the record. A wrong artist is worse than a missed filter match. A
slash carries no such ambiguity: it is the split-record separator and nothing
else, so the reduction is slash-only.

Two details of the reduction, both load-bearing and both confirmed against
the live catalog:

- **Whitespace is required on at least one side of the slash**, the repo's
  standard guard for this bug class, so an artist whose own name contains one
  (AC/DC) is not clipped to its first half.
- **It reads the artist segment only.** These live albums carry a slash in
  their own title — Agriculture "Living is Easy / The Circle Chant", Chat
  Pile "This Dungeon Earth / Remove Your Skin Please", Nivhek "After its own
  death / Walking in a spiral towards the house", Botanist "Botanist / Thief
  Split", The Jesus Lizard "Goat (Remaster / Reissue)" — and a reduction
  applied to the album would truncate every one of them.

### Format gate, layer two: the descriptor

`product_type` alone is not enough, and one live product proves it. The store
shelves a scratch-and-dent bin as `Various "Scratch & Dent" Stock`, typed
`Vinyl`, whose "variants" are eleven *whole other releases* — a Succumb LP, a
Loss of Self CD, a Planning for Burial tape set — rather than pressings of one
record. Admitted, it would emit rows credited to "Various" whose titles are
other records' titles, at damaged-copy prices.

What separates it from a record is that its descriptor names no format.
Every other live descriptor does:

| Descriptor | Products | Admitted |
| --- | --- | --- |
| `LP` | 216 | yes |
| `DLP` | 53 | yes |
| `Deluxe DLP`, `DLP (Deluxe Edition)`, `3LP` | 1 each | yes |
| `7inch`, `10inch`, `LP + 10inch` | 1 each | yes |
| `DLP & DVD`, `DLP & Zine`, `DLP & Book (pre-order)` | 1 each | yes |
| `LP (pre-order)` | 1 | yes |
| `Stock` | 1 | **no** |

The gate is **positive**, and that is the whole reason it works: a descriptor
can name a record *and* something else. `DLP & DVD`, `DLP & Zine` and
`DLP & Book (pre-order)` are all real records in packaging, and a gate that
rejected on the second noun would drop all three. Naming a vinyl format wins;
naming anything else alongside it is irrelevant.

Two refinements sit on top of it:

- **A bundle-shaped descriptor is rejected first.** The store's one
  vinyl-shelved bundle carries no quoted album, so the title parse already
  excludes it — but a bundle written to the store's usual convention
  (`Mamaleek "Everything Else" Vinyl Bundle`) would satisfy the format gate on
  its own `Vinyl`. The shape matters: in `Mamaleek "Vinyl Bundle" LP` the
  bundle word is in the *album* and the descriptor is `LP`, so this check
  never sees it and the product is admitted — the deliberate outcome of
  reading the descriptor rather than the whole title, but not what this rule
  guards. A bundle is not a Discogs release and its price is not any
  record's. The check reads the **descriptor**, not the whole title, so an
  album that legitimately contains the word (`Artist "Bundle of Joy" LP`) is
  not silently dropped.
- **`EP` is admitted, and only here.** An EP is as often a CD as a record, so
  the word names a format without naming a medium — which is all this gate
  needs, since `product_type` has already said the product is vinyl. Nothing
  live uses it.

### Variant gate

A variant title on this store is the pressing's colour and usually nothing
else — 185 distinct values, from `Black Vinyl` to
`Olive Green and Gold Merge with Baby Blue Splatter Vinyl` — so the gate is
**negative**: on a vinyl-typed product with a record descriptor, a variant is
a record unless its own title names another medium.

Its check order is load-bearing rather than stylistic: a vinyl word decides
*before* another medium word, so a pressing named for both sides of its
second disc stays a record. That ordering follows `iodinerecords.py` and
`counterintuitiverecords.py`, where it is a live case rather than a
hypothetical.

`EP` is deliberately **not** part of the vinyl-medium vocabulary this gate
reads, though the descriptor gate admits it: a `CD EP` sibling must not be
admitted by the same word that admits a 12" EP as a product descriptor. The
two gates ask different questions and read different patterns.

The inch marker admits the quote glyph as well as the spelled-out word,
though this store spells every inch size of its own out (`10inch`, `7inch`)
and uses the glyph as an inch unit nowhere — where it does write one, as
below, it is an album's closing quote. The glyph is genuinely ambiguous in a
variant
title, where the string can be a whole `Artist "Album" Format` title rather
than a pressing name: the album's closing quote after a digit
(`Planning for Burial "Matawan Vol 1 & 2" Tape Set`) reads as a 2-inch record.

**That changes the outcome, and the risk is accepted rather than
neutralised.** An earlier version of this section claimed the gate's default
is to admit anyway, so the ambiguity could only reach an outcome the gate was
already willing to reach. That reasoning does not survive its own example.
The default to admit applies only when `_NON_VINYL_MEDIA_RE` finds nothing,
and this title contains `Tape` — so without the false `2"` match the variant
is *rejected*. The vinyl-medium hit is checked first and wins, which is the
gate's deliberate "vinyl word before medium word" rule doing exactly what it
is for, on a quote that is not a vinyl word. Found in review on PR #331.

What the risk is worth: the only live title shaped that way is the one above,
and it is inside the `Various "Scratch & Dent" Stock` bin that the descriptor
gate drops before any variant of it is read, so it cannot reach a row today.
When one does, the cost is a Store row for something that is not a record —
visible, and correctable by the store or by a rule written against a real
example. Dropping the glyph from the marker instead would trade that for the
silent loss of any record the store one day describes as a 12", which is the
worse failure and the harder one to notice.

### Row title, availability and the placeholder

The row's title is the album, with the pressing appended as ` — {variant}`.
The pressing is appended on **every** row that names one, not only when the
product has more than one variant: `compute_item_key` hashes the title, so a
sibling pressing being listed or delisted must not re-title the surviving
rows and orphan the listings, judgments and saves keyed on the old identity.

Shopify's `Default Title` placeholder names no pressing, so a row built on it
carries the album alone — and only as a product's **sole** variant. On a
multi-variant product the placeholder is malformed data, and a blank name is
never a pressing; either would otherwise share the bare album title and the
product URL, and so the `item_key`, with every sibling built the same way. No
live product carries it alongside other variants; 206 carry it alone.

Availability comes from `variant.available`, which is a `bool` on all 506
live variants, and **only the literal `True` admits a row**: the string
`"false"` is truthy, so a falsiness test would publish a sold-out record as in
stock.

There is **no pre-order bypass and no ` (Pre-Order)` marker**. The store's two
live pre-orders already report `available: true`, and it announces them in the
product title (`... LP (pre-order)`) — which the parse leaves in the
descriptor, outside the album, so the row is titled the same before and after
the record ships. A marker would re-key the row on the day it stopped being a
pre-order.

### Prices

Every live price is a decimal string. `_price` answers `None` for anything it
cannot use, rejecting `bool` *before* `float()` (bool is an `int` subclass, so
`True` would price a record at 1), and non-finite or non-positive values after
it.

## Reading the payload

Every string field this crawler reads goes through one helper that returns
`""` for anything that is not a string. The `or ""` idiom it replaces covers a
null or absent field only, so a truthy non-string was handed straight to
`.split()`/`.strip()` and raised an `AttributeError` from inside the walk,
aborting the whole source — one malformed variant title would stop the catalog
refreshing for as long as the store served it.

That raise is not fail-safe so much as unexplained. It does protect the
previous snapshot, since `_sync_stock` skips `replace_stock_items()` on a
raise, but no drift message names it and it contradicts the
discard-and-continue behaviour `_classify_variants` documents for a junk
entry. Reading an unreadable field as *absent* instead routes every case into
the guard that already covers it:

| Field | Non-string reading | Guard it reaches |
| --- | --- | --- |
| `product_type` | not vinyl | `format-taxonomy drift` if catalog-wide |
| product `title` | fails the parse | `identity-source drift` (the blank-title tally) |
| `handle` | no identity | `identity-source drift` |
| variant `title` | unnamed pressing | `pressing-name drift` |

Found in review on PR #331, on the variant title; the other three had the same
defect and were fixed with it.

## Drift guards

`db.replace_stock_items()` DELETEs this crawler's previous snapshot before
inserting, and `_sync_stock` only skips that call when the crawl raised — so a
completed-but-empty walk is destructive where a raise is inert. Each guard
names a distinct way the payload can stop carrying what this crawler reads:

| Guard | Fires when | What it catches |
| --- | --- | --- |
| `returned no products` | the walk yielded nothing | collection renamed or removed |
| `format-taxonomy drift` | no product carries a `vinyl` type segment | the store restyling `product_type` |
| `title-convention drift` | no vinyl product yields an artist and an album | the store dropping the quoted-title convention |
| `format-vocabulary drift` | no vinyl product's descriptor names a format | the format moving out of the title, or being written in unknown words |
| `pressing-source drift` | no vinyl product has a variant reading as a record, and none was dropped unread while readably sold out | variants lost, or re-titled as another medium |
| `price-source drift` | rows were yielded but none carries a price | `price` removed or retyped store-wide |
| `identity-source drift` | nothing yielded while a vinyl product has no title or handle | identity fields going away |
| `pressing-name drift` | nothing yielded while a variant's name is unreadable | a variant retyped, blanked, or given Shopify's placeholder beside siblings |
| `variant-source drift` | nothing yielded while a record carries no variants at all | the `variants` array emptied or renamed |
| `stock-source drift` | nothing yielded while a vinyl product's flag is unreadable | `available` retyped |

These properties of the tallies matter as much as the guards themselves:

- **The chain is nested, not sibling.** Only a product that is vinyl-typed
  *and* parses *and* names a format *and* has a variant the gate admits could
  have yielded a row, so only such a product's stock readability says anything
  about an empty result. Tallied independently, one product could satisfy each
  condition while none of them can yield.
- **The tallies that count what the crawler *dropped* are the deliberate
  exceptions to that** — `identity_missing`, `unnamed_pressings`,
  `variantless_records`, and `sold_out_pressings` — the last of which is read
  in the opposite direction, vouching for an empty result rather than arming a
  guard. They are siblings for the same reason the chain is nested. A product with no identity, a pressing whose name is unreadable,
  or a record carrying no variants at all could never have yielded a row by
  definition, so nesting them behind "would have yielded" makes them
  unreachable, which is exactly what it did: `identity_missing` could only
  ever fire for a missing handle, never for the missing title its own message
  names, because a blank title fails the parse two branches earlier. Found in
  review on PR #331.

  What `unnamed_pressings` counts is narrow, and both halves of the narrowing
  are load-bearing. A variant naming another medium is **not** unreadable — a
  CD sibling being in stock says nothing about whether the record is, and
  counting it would raise on an ordinary store. A variant readably out of
  stock is not counted either, whatever its name: it could not have yielded a
  row anyway, so it neither caused an empty result nor casts doubt on one.
  What is left is the case the guard exists for — an in-stock pressing the
  crawler could not name, which leaves the walk looking sold out when it is
  not.

  That exemption has a second half, and leaving it out took the exemption
  straight back one guard down. A variant dropped unread is not a *record*
  variant either, so a record whose only pressing was unreadably named and
  readably sold out left `record_variants_seen` at zero and reached
  `pressing-source drift` — a store whose one record was out of stock was told
  its pressings had stopped being pressings. So the unread drops are split on
  the availability flag rather than one half being discarded: the in-stock
  half arms `pressing-name drift`, and the sold-out half vouches for an empty
  result at `pressing-source drift`, because it is still evidence that the
  product *has* pressings. The split is scoped to the drops — a variant naming
  another medium was read correctly and vouches for nothing, so a catalog
  whose readable variants all name other media still raises, sold out or not.
  Found in review on PR #331.

  `variantless_records` is narrowed the same way, to a raw `variants`
  array that is empty (which Shopify does not produce) rather than to an empty
  *result*: a record whose only variant names another medium is odd store data
  the gate read correctly, not a broken payload.

- **Where each of them sits is not symmetric, and that asymmetry is the
  point.** A blank title has to be seen *before* the parse — it fails the
  parse, so that is the only place it can be seen at all. The rest wait
  until the title and descriptor have established the product is a record, so a product this crawler excludes **on purpose** — the
  scratch-and-dent bin, a bundle — cannot arm a guard with a defect of its own
  and make a genuinely sold-out crawl raise, which would preserve a stale
  in-stock snapshot. Found in review on PR #331.

  That placement also draws the line this guard family works to. A product
  that fails to parse *could not* have yielded a row — deliberately, for the
  bin and the bundle — so on its own it is not evidence of drift, and only the
  catalog-wide version is, which `title-convention drift` and
  `format-vocabulary drift` already cover. A missing handle or an unreadable
  variant name is different in kind: there is no legitimate reason for a
  Shopify product to lack either, so one of those *is* evidence. Counting
  unclassifiable products instead would permanently arm a guard on the two
  known-good exclusions, which either makes every empty walk raise — defeating
  the "genuinely sold out is legitimate" property this design states outright
  — or requires hardcoding the bin and bundle shapes as exemptions, the
  brittle special-casing the positive descriptor gate exists to avoid.
- **They are all taken before the availability filter**, so a store that has
  simply sold out is empty legitimately and trips nothing. `yielded` and
  `priced` are necessarily counted after it, which is why the guards reading
  them are each conditioned on a second tally rather than on emptiness alone.

Stock readability is judged with `all()`, not `any()`, over the admitted
variants: one readable variant does not make the product readable, and a
product whose black pressing is a readable `False` and whose coloured pressing
carries the string `"false"` would otherwise vouch for an emptiness half its
own doing.

## Verification

Replayed over the fully-cached live catalog: 754 products walked → 281
vinyl-typed → 280 parsed → 279 naming a format → **319 rows across 163
artists**. No `item_key` collisions, no blank artist or title, no malformed
URL, no missing cover image, no null price, every row `USD` and `Vinyl`.

Exactly two products are dropped after the type gate, both intentionally:
`Mamaleek Vinyl Bundle` (no quoted album) and `Various "Scratch & Dent" Stock`
(descriptor names no format).

Every rule and guard above was mutation-checked — the crawler was mutated once
per rule and the test suite confirmed to fail on every one, including the two
that initially survived (a substring `product_type` test and a tag-driven gate),
which exposed two tests that were not isolating the gate they named.

Copilot reviewed PR #331 over successive rounds, and every finding was
reproduced against the code before being fixed and given its own mutation:

- the nested-quote truncation and its two follow-ons — the inch marker with no
  right-hand boundary, and two separately valid markers vouching for each
  other;
- the unreachable identity tally, and the tally placement that let a
  deliberately excluded product arm a guard;
- the unnamed in-stock pressing, the record with no variants at all, and the
  sole-variant test reading the filtered variant list;
- the Unicode-normalisation hole, where a decomposed accent opened a boundary
  its precomposed equivalent closes;
- and, on the emitted side of that same issue, an identity left in whatever
  normalisation the storefront happened to serve;
- a payload field that was not a string reaching `.split()` and aborting the
  whole source, on the variant title and three sibling fields (see "Reading
  the payload" above);
- a handle validated in its collapsed spelling but interpolated into the URL
  raw, so a padded one was persisted as a malformed link under a different
  `item_key`;
- the broadest drift guard ordered ahead of the specific ones, which made
  `variant-source drift` and `pressing-name drift` unreachable whenever a
  single product was the whole catalog — and, a round later, `parsed == 0`
  doing the same to `identity-source drift` when every product lost its title.
  The guards are now ordered most-specific-first, broadest last. Each of them
  raises, so the snapshot was safe throughout; what was lost is the only thing
  distinct guards are for;
- the same problem a third time, arriving through the tallies rather than the
  sequence: the sold-out pressing `unnamed_pressings` deliberately exempts
  reached the broad guard anyway, because an unread variant is not a record
  variant either. Split rather than discarded, as described above — and the
  test that had covered it was masking it with a named sold-out sibling;
- the mark fold in the format gate left with no test of its own once the quote
  rule stopped folding, since the NFC/NFD title case reaches `_parse_title`
  only;
- a `variants` field retyped to a truthy scalar, which `list()` turns into a
  `TypeError` that aborts the whole source. Only a real list is a variants
  collection, and one reading of that field is shared by the classifier and
  the `variantless_records` tally — the two disagreeing is what sent a
  retyped field to the broad guard instead of the one that names it;
- and the ambiguous single-quote descriptor above.

Two suggestions were declined, both recorded above: counting every dropped
variant rather than the malformed ones, and counting unclassifiable
vinyl-typed products.

None of it changes a single live row. The replay above is byte-identical
throughout, every live row is already NFC, and no live product carries an
unreadable pressing name, a missing identity, an empty variants array, or a
descriptor quote.
