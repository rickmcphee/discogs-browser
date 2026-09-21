# Le Noise crawler design

**Status:** implemented
**Date:** 2026-09-21
**Store:** [lenoise.ca](https://lenoise.ca)

## Problem

Add a `crawler_type="catalog"` plugin for Le Noise, the Montreal record store,
so its stock appears in the Store tab and is matched against the Collection and
Wantlist filters like every other catalog source.

The store runs on Shopify and publishes a vinyl shelf of its own, so
`shopify_catalog.iter_products()` covers the transport and
`/collections/vinyl/` covers most of the scoping. Two things still need
designing, and both are properties of this shelf rather than of Shopify:

1. **The shelf is bigger than the endpoint can enumerate**, and what falls off
   the end is not a random slice.
2. **The shelf is not purely vinyl**, and the store's own format field says it
   is.

## Scope

In: a new `backend/crawlers/lenoise.py` and its test file. Out: everything
else — the plugin is discovered by `main.py`'s bundled-crawler startup loop, so
there is no wiring to change anywhere.

## Technical grounding

Everything below was confirmed against the live store on 2026-09-21 by walking
every page `/collections/vinyl/products.json` will serve: 100 pages of 250,
25,000 products, no short page and no duplicate product id.

### Collection choice: `vinyl`

The store publishes a shelf per medium (`vinyl`, `cd`, `cassettes`, `video`)
and per merchandise kind (`t-shirts`, `buttons`, `figurines`, `turntables`,
`bags`, `drinkware`, …), so unlike `monorailmusic.py` there is no whole-shop
collection that has to be filtered down in the crawler. The shelf already *is*
the format scoping; the gates below only repair what the store files onto it
by mistake.

No narrower shelf would serve better. The vinyl-adjacent ones (`music-on-vinyl`,
`preorder`, `rsd-2026`, `new-this-week`) are subsets of this one, not a
partition of it, so walking them instead would lose more than it gained.

### The page ceiling, and what specifically is lost

`collections.json` reports 41,615 products on this shelf. Shopify's storefront
`products.json` refuses `page` past `shopify_catalog._MAX_PAGE` with an HTTP
400 — confirmed here as well as on `waterloorecords.com` — so the walk reaches
25,000 of them and stops. Page 100 comes back full, page 101 is a 400.

`iter_products()` handles the stop correctly already: it breaks *before*
requesting the page that would 400, logs the ceiling, and returns what it has.
Nothing raises, and nothing partial is silently treated as complete.

What is new here is **which** products fall outside, because this shelf's
ordering is not arbitrary. Sampling the first letter of each reachable title
shows the window is not a uniform slice of the catalog:

```
A   67   B  112   C   85   D   38   E  287   F 1153   G 1308
H  860   I  577   J 1765   K 1112   L 1559   M 2689   N 1061
O 2025   P 1531   Q  124   R 1398   S 3235   T 1568   U  208
V  893   W  866   X   58   Y  256   Z  144
```

Past a short unordered block at the front, the walk drifts steadily down the
alphabet — page 5 opens on `Zola Jesus`, page 20 on `Tame Impala`, page 50 on
`OST`, page 80 on `John Legend`, page 100 on `Expectorated Sequence`, and the
last product it serves is `Eric Church - Caldwell County EP`. So the
unreachable ~16,600 products are overwhelmingly what the store files under A
through D, not a scattering across the catalog: those four letters account for
302 of the 24,988 reachable vinyl-typed titles, against 1,153 for F alone.

**This is accepted rather than mitigated**, on the same terms
`2026-08-24-waterloo-records-crawler-design.md` accepted it: a browsable, if
truncated, shelf in the Store tab is worth more than no shelf. It is recorded
here rather than left to be discovered because the shape of the loss matters —
"this store stocks no Bob Dylan" will be wrong in a specific, systematic way,
and a reader debugging that should find the answer here.

The alternatives were considered and rejected:

- **`sort_by`** would give a second ordering to union with the first, but the
  store's `robots.txt` disallows `/collections/*sort_by*` explicitly.
- **Tag-scoped shelves** (`/collections/vinyl/metal/products.json`) would
  partition the catalog into reachable pieces, but the endpoint answers 404 —
  tag filtering is HTML-only here. The genre tags also do not cover every
  product, so they were never a true partition.
- **The product sitemap** lists every product, but reaching their data means
  one request per product, which is three orders of magnitude more load than
  the walk.

### The format gates

#### Gate one: `product_type`, compared lowercased

`product_type` is the store's own format field and it is overwhelmingly
`Vinyl`. It is compared **lowercased** because the store's casing is not
consistent: `VInyl` is live on real records, and an exact match drops them.

#### Gate two: the title's bracket, because gate one leaks

The shelf carries CDs and cassettes that are typed `Vinyl`. Over the reachable
window: 28 `(CD)`, 9 `(2CD)`, 2 `(Cassette)`, and one each of `(3CD)`, `(5CD)`,
`(6CD)` and `(CD/BRD)`. Thirty-nine of the forty-three are in stock, at a CD's price.
Without a second gate they are published as records, and matched against the
library's vinyl.

The medium is read **only inside a bracket**, never from the title as a whole.
This is not caution, it is a live requirement in both directions:

- `Lip Cream - Big Foot Cassette (Yellow)` is a record whose *album* is called
  "Big Foot Cassette", pressed on yellow vinyl. Its bracket names a colour, so
  a whole-title scan finds the cassette, finds no vinyl to override it, and
  throws away a real in-stock record.
- `Led Zeppelin - Live EP (CD)` is a CD whose *album* is an EP. The bracket is
  what distinguishes the two cases, and it is the store's own convention for
  saying what it is selling.

The gate needs **both** halves of the bracket vocabulary, because a bracket can
name several media at once:

| Title | Bracket names | Verdict |
| --- | --- | --- |
| `... (CD)` / `(2CD)` / `(Cassette)` | non-vinyl only | dropped |
| `... (CD/BRD)` | non-vinyl only | dropped |
| `Lee Perry - King Scratch (4LP+4CD)` | both | kept |
| `George Michael - The Faith Tour (3LP/2CD)` | both | kept |

So the rule is *names a non-vinyl medium and names no vinyl one*, not *names a
non-vinyl medium*. The two boxes above are the whole live population of the
"both" case, and a bare "names a CD" gate discards them.

A leading count is part of the medium token and has no word boundary before the
letters (`2CD`, `3LP`), which is why each pattern carries its own `\d*` rather
than relying on `\b` to find the start.

`EP` is deliberately **not** in the vinyl vocabulary: it names a record's
length, not its medium, and CD EPs exist. Nothing is lost by leaving it out — a
bare `(EP)` product has no non-vinyl bracket for it to override — while
including it would rescue a hypothetical `(EP/CD)` that is not a record at all.

### The artist: the title, and nothing else

Titles are `Artist - Album (Pressing)`, split on the first qualifying dash.
Album halves legitimately carry further dashes, so the split is the *first*
one, taken lazily.

**There is no `vendor` fallback**, unlike most of the Shopify crawlers in this
fleet, because the field here is not one kind of thing:

| `title` | `vendor` |
| --- | --- |
| `Talking Heads - True Stories` | `Talking Heads` |
| `Stef Chura - Dancing Alone On The Concrete (Blue)` | `Chura Stef` |
| `Tame Impala - Innerspeaker (2LP)` | `MODVL128` |
| `Talos - Dear Chaos (Coloured)` | `Talos - 5053880405` |
| `John Carpenter - Cathedral` | `Soundtrack - John Carpenter` |

The act, the act reversed, a bare catalogue number, the act with its catalogue
number glued on, and a genre-first label — with nothing in the payload marking
which. Crediting a record to any of those is worse than skipping it: a wrong
artist is indistinguishable from a right one everywhere downstream.

#### The separator characters

Three dashes separate titles on this shelf: ASCII hyphen-minus, U+2010 HYPHEN
(27 vinyl-typed titles) and U+2013 EN DASH (6). The typographic two are not decoration —
`Police ‐ Greatest Hits (2LP)` and `Molecule – Nazare` are ordinary records —
and an ASCII-only class files every one of them under no artist at all. U+2014
EM DASH does not appear and is not matched.

#### Whitespace on at least one side, which is looser than the fleet rule

The sibling Shopify crawlers require whitespace on **both** sides of the
separator, to stop a hyphenated name being split mid-word. That rule costs real
records here, because this store types the separator closed up on one side
often enough to matter. Measured over the reachable window:

| Rule | Titles left with no artist |
| --- | --- |
| whitespace both sides | 10 |
| whitespace before only | 7 |
| whitespace on at least one side | 4 |

and the six titles the loosest rule rescues are all split correctly, with no
regression anywhere else in the 24,988 vinyl-typed titles:

```
Tokyo Blade -Tokyo Blade (Coloured)
Me First & The Gimme Gimmes -Most People I Know Think That I'm Crazy
Iron Butterfly -In-A-Gadda-Da-Vida (Clear)
Little Big Town- Mr. Sun (2LP)(Blue)
I The Mighty- Where The Mind Wants To Go
Ghost Inside- Searching For Solace
```

One side is enough for the protection both sides was bought for, because the
only shape *neither* alternative admits is a dash with a letter hard against it
on both sides — which is exactly a dash inside a word. `Anti-Flag - The Terror
State` and `Bryan Ferry - Bitter-Sweet (Red)` both split at the right dash.

**The laziness, not the order of the alternatives, is what makes that safe.**
On `Iron Butterfly -In-A-Gadda-Da-Vida (Clear)` the engine tries the shortest
artist first, and the earliest position where either alternative fits is after
`Iron Butterfly`; every dash inside the album is flanked by letters. Reordering
the alternatives changes nothing, and a greedy artist half breaks it
completely.

The four titles still left unsplit are genuinely unparseable — two with no dash
at all (`Various Artists Antones: 50 Years Of The Blues (4LP)(Coloured)`,
`Florence & The Machine  Everybody Scream (2LP)`) and two whose separator is
something else entirely (`Microstoria / init ding + _snd (2LP)(Coloured)`,
`Matt Jencik & Midwife (Clear)`). They are skipped rather than credited to a
guess.

### The row's title keeps the pressing bracket

The bracket stays on the title the row is published under. It is the only thing
separating two pressings of one album — `Tallest Man On Earth - Henry St.` and
`Tallest Man On Earth - Henry St. (Red)` are distinct products at distinct
URLs — and it still matches the catalog: `db._library_release_match_sql`
matches a stock title exactly **or** as a prefix followed by a space, so
`The Wall (2LP)` matches a catalog `The Wall`.

### One row per product

`db.compute_item_key` hashes `(artist, title, url)`, and every variant of a
product shares all three, so a per-variant fan-out would emit rows colliding on
`item_key` — which `replace_stock_items` INSERTs without an `ON CONFLICT`
guard.

Live, this store publishes exactly **one** variant per product, named
`Default Title`: the pressing is a separate product here, not an option axis.
The cheapest-in-stock pick is therefore inert today. It is kept anyway as the
shape that stays correct if the store ever splits a record across variants,
which is the same reason `waterloorecords.py` picks that way.

Availability admits only the literal `True`. The string `"false"` is truthy in
Python, so a falsiness test would publish a sold-out record as in stock.

No pre-order handling: pre-orders here report `available: true`, carry a
`Preorder` tag and are purchasable at the listed price, so they are stock. A
`(Pre-Order)` marker on the title would also re-title every row the day the
record ships, orphaning the saves and judgments keyed on the old `item_key`.

### Fields

| Field | Source |
| --- | --- |
| `artist` | title's artist half |
| `title` | title's album half, pressing bracket intact |
| `format` | `"Vinyl"`, unconditionally, as every sibling catalog crawler does |
| `price` | cheapest in-stock variant's `price`, `None` when not a usable one |
| `currency` | `"CAD"` |
| `url` | `https://lenoise.ca/products/{handle}` |
| `cover_image_url` | `resolve_cover_image()`, variant image first |

`currency` is hardcoded because `products.json` carries none to read: the
storefront sets `cart_currency=CAD`, and the store is in Montreal.

`price` is read through the parser the recent sibling catalog crawlers share
rather than a bare `float()`. `float()` accepts three things that are not
prices: a bool (`bool` is an `int` subclass, so `True` prices a record at 1),
a non-finite string (`"NaN"`, `"Infinity"`), and zero or a negative. The
non-finite case is the one that matters most, because `nan` is not `None` — it
counts toward `priced` as readily as a real price, so a store-wide retyping to
`"NaN"` would satisfy `price-source drift` while publishing a catalog of prices
no reader can use.

`resolve_cover_image()` is called through a local wrapper that validates both
the containers **and** the nested `src`. The shared helper reads
`variant["featured_image"].get()` and `product["images"][0].get()` behind `or`
guards, which catch a missing or null field but pass a *retyped* one straight to
`.get()` — and a raise there aborts the whole source over one product's artwork,
which is display-only. Checking the two containers is not enough on its own:
the helper returns whatever sits at `src` without looking at it, so a nested
`{"src": 123}` reaches the row's `cover_image_url` in breach of the
`Optional[str]` contract, and `replace_stock_items()` then hands an int to a
Postgres TEXT column — the same refresh-killing failure, one level deeper. So an
image whose `src` is not a non-empty string is passed over rather than allowed
to answer. Guarded in this crawler rather than in `shopify_catalog` because
every Shopify crawler in the fleet reads that helper, and this is one store's
payload rather than a fleet-wide change to make from inside a crawler.

### Replay over the live catalog

The crawler was replayed over the fully cached 25,000-product walk:

```
25,000 products  ->  17,686 rows across 6,846 artists
  skipped: out of stock                 7,255
  skipped: non-vinyl medium bracket        43
  skipped: product_type not vinyl          12
  skipped: no artist split                  4
  item_key collisions                       0
  blank artist or title                     0
  null price                                0
  rows not under https://lenoise.ca/products/   0
```

One row carries no cover (`Grave - Necropsy (3LP)` publishes no images at all),
which is emitted rather than dropped — artwork is display-only.

## Drift guards

`replace_stock_items()` DELETEs this crawler's previous snapshot before
inserting, and `_sync_stock` only skips that call when the crawl **raised** —
so a completed-but-empty walk is destructive where a raise is inert. Each guard
names a distinct way the payload can stop carrying what this crawler reads:

| Guard | Fires when |
| --- | --- |
| collection empty | the walk yielded no products at all |
| `format-taxonomy drift` | no product carries the `vinyl` product_type |
| `medium-bracket drift` | every vinyl-typed product reads as another medium |
| `variant-identity-source drift` | no rows, and some product's `variants` collection was absent, empty, retyped, or held an entry that was not a mapping |
| `identity-source drift` | no rows, and some record lost its `title` or `handle` |
| `artist-source drift` | no rows, and some record's title stopped carrying an artist |
| `stock-source drift` | no rows, and **any** kept variant's `available` was not a literal bool |
| `price-source drift` | rows were emitted and **none** carries a price |

Two ordering rules inside that set, each of which was wrong first:

- **Identity is asked before the artist.** `title` is identity *and* the
  artist's own source, so a product that has lost it has lost both, and
  reporting that as artist-source drift names the wrong field.
- **Variants are asked before everything else.** A product whose `variants`
  could not be read has no readable stock flag either, so the stock guard
  would otherwise answer every variants-level failure with the wrong
  diagnosis.

The four "no rows **and**" guards are gated on an empty outcome deliberately:
one broken product among real rows is an ordinary skipped row, not drift. The
price guard is the mirror image — it fires only when *no* row at all carries a
price, so isolated nulls stay tolerated.

The stock guard counts **unreadable** products rather than readable ones,
because that is what catches the partial case: one genuinely sold-out record
must not vouch for a catalog that has gone unreadable behind it. The same
reasoning runs one level down, inside a single product, and getting it wrong
there was a live hole Copilot found on PR #394. Readability is judged over
**every** kept variant with `all()`, not `any()` — under `any()`, the sold-out
variant in `[{"available": False}, {"available": "maybe"}]` certified the
corrupt one beside it. And a variant entry that is not a mapping at all is
**counted** as it is dropped rather than silently discarded: with
`[{"available": False}, None]` the collection is neither empty nor unreadable,
so before the count existed such a product yielded no row while incrementing
nothing, and a store-wide retyping of part of every variants array would have
emptied the walk in exactly that silence.

## Scale

Recorded rather than discovered later:

- **100 GETs per sync.** The walk stops at the ceiling, so it never requests
  the terminating page that a smaller collection needs.
- **Roughly 37 minutes of wall-clock per sync**, at the
  `random.uniform(delay * 0.5, delay)` pacing with `crawl_delay_seconds`
  defaulting to 30s (~22.5s mean).
- **Roughly 17,700 stock rows**, measured rather than extrapolated — the
  replay above walked the whole reachable window.
- **Roughly 53,000 dispatch work units** per sync, at one `crawl_queue` row
  per `item_key` expanded across the release crawlers eligible for a stock
  item (`amazon`, `ebay`, `ebay_general`; `discogs_marketplace` and
  `roughtrade` are excluded by their `requires_discogs_release = True`).
  Fewer when the admin's `crawl_library_only` setting is on, which narrows
  the queue to items some user has saved or holds a matching
  collection/wantlist record for.

The upstream cost is the consequential one, and
`2026-08-24-waterloo-records-crawler-design.md` analyses it in full: the stock
sync holds `STOCK_SYNC_LOCK_KEY` for its whole duration and walks catalog
crawlers **sequentially**, so this crawler adds its ~37 minutes to every full
run, delays every store after it in the loop by that much, and any scheduled
sync firing inside the window is dropped rather than queued. Whether that
matters is a deployment question about `stock_schedule`, not a property of this
crawler; the levers are all operational (lengthen the cadence, sync this store
alone via `start_stock_sync(crawler_id=...)`, or disable the plugin).

## Deliberate omissions

Recorded so a future reader doesn't re-derive them:

- **No used/new distinction.** The store sells both ("Vinyles neufs et
  usagés"), but `stock_items` has no condition column and the payload carries
  no condition field — the variant is `Default Title` on every product.
- **No `(Damaged)` special-casing.** It is a pressing descriptor like any
  other, and it stays in the title where a reader can see it.
- **No francophone title normalisation.** The store carries French pressing
  descriptors (`Couleur`, `Rouge`, `Blanc`, `Autographié`) alongside the
  English ones. They are display text in the pressing bracket; folding them
  would change `item_key` for no gain.
- **No second walk of `preorder` or `music-on-vinyl`.** Both are subsets of
  the shelf already walked, and neither reaches past the ceiling.

## Testing

`backend/tests/test_lenoise_crawler.py`, mocking `products.json` with `respx`.
Fixtures are marked `captured` (a real product from the 2026-09-21 walk) or
`invented` (a shape the live shelf does not publish) at their definition.

Cases: artist/album parsing with the pressing bracket kept; first-dash rather
than last-dash split; both typographic dashes; a separator closed up on either
side; a hyphenated name never split mid-word; `vendor` never used as the
artist; the `VInyl` casing slip admitted; CDs dropped under both product_types;
a cassette dropped; `(CD/BRD)` dropped; the two vinyl-plus-disc boxes kept; the
medium gate reading the bracket rather than the album name, in both directions;
sold-out products yielding nothing; a string `"false"` not counting as stock;
an out-of-stock variant never setting the price; one row per product at the
cheapest in-stock price; a malformed price not dropping in-stock vinyl; the
variant image preferred over the product image; a product with no images still
yielding a row; a title with no separator yielding nothing; multi-page walking;
every drift guard above, in both directions where it has one; and retyped
payload fields skipping the product rather than aborting the source.

Each guard and rule was mutation-checked — mutated once in the crawler, with
the suite re-run to confirm a test fails. Two mutations survived a first pass
and both were closed by adding a test: reading the whole title instead of its
brackets in the medium gate (closed by the captured `Lip Cream - Big Foot
Cassette (Yellow)` product), and dropping the type check on the `images`
container (closed by giving one product a *non-iterable* `images`, since a
retyped-but-iterable one such as a string happens to survive the unguarded
comprehension).

## Crawl citizenship and `robots.txt` compliance

Per the normative section of `2026-08-09-amoeba-store-crawler-design.md`. This
site's findings, from the `robots.txt` captured 2026-09-21:

- The `User-agent: *` group's `Disallow` rules are the Shopify default template
  — `/admin`, `/cart`, `/carts`, `/checkout`, `/checkouts/`, `/orders`,
  `/account`, `/a/downloads/-/*` and the shop-id-scoped checkout and order
  paths — plus `sort_by` and `+`/`%2B`-encoded crawl traps scoped under
  `/collections/`. **None of these covers
  `/collections/vinyl/products.json`**, the only path this crawler requests,
  which carries no `sort_by` and no `+` — only `limit` and `page`.
- There is no `Crawl-delay` directive, so no `min_delay` floor is set on
  `get_with_retry`.
- This crawler reads a public JSON catalog, links out to the product page, and
  never transacts.
- Load: 100 GETs per sync, paced at `random.uniform(delay * 0.5, delay)` with
  `crawl_delay_seconds` defaulting to 30s. No detail-page fan-out.
  `iter_products()` fails fast on 429 and gives up after
  `consecutive_failure_limit` on anything else.
- The shelf served transient HTTP 500s during the grounding walk — pages that
  answered 200 on an immediate retry. That is what `get_with_retry`'s retry
  budget is for, and no crawler-side handling was added for it.
- Contact for crawler issues, per the file: `bots@shopify.com`.
- If Le Noise blocks this crawler, adds a `Disallow` covering this path, or
  asks us to stop, the response is to disable the plugin.

## Runtime/agent document impact

No `.agents/INPUTS.md`, `.agents/OUTPUTS.md`, or `.agents/INSTRUCTIONS.md`
exist in this repo. This change adds no new trigger and no new inbound
interface — one new outbound host (`lenoise.ca`).

`backend/version.py`'s `VERSION` is derived from git and is not edited by this
change.
