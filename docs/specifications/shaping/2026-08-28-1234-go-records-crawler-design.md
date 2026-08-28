# 1-2-3-4 Go! Records store crawler design

Date: 2026-08-28
Branch: `claude/1234gorecords-crawler-2de684`

## Problem

1-2-3-4 Go! Records (`1234gorecords.shop`) — an Oakland, California
independent record store trading since 2008, and a small label besides — is
not covered by any existing crawler. It is a standard Shopify storefront, the
same family as the `catalog` plugins already in `backend/crawlers/`, and like
`jackpotrecords.py`, `darksiderecords.py` and `waterloorecords.py` it is a
*retail store* rather than a label.

Two things make it unlike every sibling, and both drive the design:

1. **Its titles quote the album.** The whole fleet splits `Artist - Album` on
   a dash. This store writes `Artist "Album" FORMAT (pressing notes)`, and
   prefixes the lot with a status marker (`Used Vinyl:`, `PRE-ORDER:`,
   `DAMAGED COVER:`). `asianmanrecords.py` and `spv.py` have seen the quoted
   shape before, but both keep a dash split as a fallback behind it; this
   store needs none, and has none.
2. **Used stock is a first-class part of the catalog, not a sideline.** Of
   the vinyl-typed products live on 2026-08-28, 3,651 of 9,785 carry a used
   marker. Excluding it, as `darksiderecords.py` did, would discard 37% of
   the store.

## Scope

Add `backend/crawlers/onetwothreefourgo.py` as a `crawler_type="catalog"`
plugin, iterating the site's `all` collection via
`shopify_catalog.iter_products()` — no new shared code needed. Registration is
automatic: `main.seed_bundled_crawlers()` globs `backend/crawlers/*.py` and
reads `site_name`/`crawler_type` off the plugin class.

**Non-goals**

- **No CDs, cassettes, DVDs, books, apparel, or merch.** The store sells all
  of them; this app's stock pipeline is vinyl-only by convention
  (`format: "Vinyl"` hardcoded across every sibling).
- **No browser.** Confirmed live: `products.json` is served to plain
  `curl`/`httpx` with no Cloudflare gate or bot interstitial, despite the
  storefront sitting behind Cloudflare.
- **No condition grading.** `stock_items` has no condition column, so a used
  copy's grade cannot be carried. See "Used stock" below for what is carried
  instead.

## Module naming

`onetwothreefourgo.py`, not `1234gorecords.py`. A module whose name starts
with a digit cannot be written as `from crawlers.1234gorecords import Crawler`
— that is a syntax error, so the test file could not import it the way every
sibling test does. Spelling the digits out follows `twentybuckspin.py`, whose
store is "20 Buck Spin". The plugin's `site_name` carries the real name.

## Technical grounding

All figures below were confirmed against the live site on 2026-08-28 by fully
paginating `/collections/{slug}/products.json` at `limit=250` and caching
every page.

### Collection choice: `all`

Shopify's `products.json` serves at most 100 pages — confirmed live here:
page 101 of any collection returns HTTP 400. At `limit=250` that is a ceiling
of 25,000 products per collection, and `darksiderecords.py` records what
happens when a collection exceeds it (`iter_products()` treats a 400 as a
generic error and burns `consecutive_failure_limit` retrying it).

This store never approaches that ceiling, but its collections' self-reported
`products_count` values suggest otherwise and must not be trusted:

| collection | `products_count` says | actually paginates to |
|---|---|---|
| `quick-order` | 101,758 | 10,975 |
| `lp` | 57,582 | 8,613 |
| `allusedvinyl` | 33,720 | 10,978 |
| `new-vinyl` | 32,644 | 6,076 |
| `all` | 11,424 | 10,982 |

The four largest all converge on ~10,980 because their collection rules are
broken and match essentially the whole store; `products_count` on this store
is stale metadata, not a size. `all` ("Everything we got!") paginates to
10,982 products across 44 pages, and is a strict superset by product `id` of
every other collection checked — `lp`, `new-vinyl`, `used-lps`, `7`, `10`,
`cassette` and `cd` each contain zero products absent from it.

So `all` is crawled and the vinyl gate is applied in-process. The alternative
— `lp` (35 pages) + `7` (5) + `10` (1) — costs 41 pages against 44, needs
cross-collection de-duplication, and still misses the `12"`-typed products,
which have no crawlable collection of their own (`12-single` and `2lp` both
paginate to zero). One collection, one pass, no de-duplication.

At the default `crawl_delay_seconds = 30`, `iter_products()` sleeps
`random.uniform(delay * 0.5, delay)` before every request, so 44 pages is
~16.5 minutes on average — the same order as the established siblings.

### Vinyl gate: `product_type`, then a negative filter on the descriptor

`product_type` is the store's own classification and it is clean:

```
LP 8614 · 7" 1103 · CD 771 · Cassette 318 · 10" 51 · Apparel 36 · 12" 14 · 7 3
```

plus a long tail of merch types (Drinkware, Action Figure, Storage Crate,
Notebook, Tote Bag …). The gate admits `{LP, 7", 10", 12", 7}` — 9,785
products. The bare `7` is not a typo of `7"`; three used 7" singles carry it.

`product_type` alone is not sufficient, because it is *occasionally wrong in
the vinyl direction*: 13 vinyl-typed products are CDs or Blu-Rays by their own
titles (`Kehlani "S/T" CD`, `PRE-ORDER: King Crimson "2014: The Complete US
Tour" 2-Disc Blu-Ray`, and a 2xCD reissue the store filed under `7"`). So a
second, negative filter runs on the format descriptor the title carries after
the closing quote: a product is dropped when its descriptor names a competing
format and no vinyl format. The vinyl override is what keeps genuine hybrid
releases (`2xLP + CD`, `LP + 7"`, `2xLP + 15xCD Box Set`) in.

**Both sides of that filter must allow a disc-count prefix.** A count binds to
its format word with no word boundary between it, so a bare `\bcds?\b` cannot
see the CD in `3xCD` — and the store writes `2xCD`, `3xCD`, `6xCD`, `14xCD`,
`15xCD` and `2xDVD`. Three live products were published as records by that gap
before it was fixed. `spv.py` records the identical regression on its own
store, and the `\d*[x×]?` spellings here follow it. The same allowance already
existed on the vinyl side for `2xLP`; the rule is that neither side is safe
while it recognises fewer spellings than the other, or fewer than the store
writes.

Two accepted gaps, both deliberate:

- **`Box Set`-typed vinyl**, 2 products (`Phish … 5xLP Box Set`, `Queen …
  2xLP + 5xCD Box Set`). "Box set" names a packaging, not a format, so it
  cannot sit in a format gate without also admitting the merch box sets the
  store sells. LP-typed box sets — the overwhelming majority — are unaffected.
- **One CD-typed vinyl LP** (`PRE-ORDER: John Debney "Elf (OST)" LP (Picture
  Disc)`). Recovering it would mean promoting the descriptor from a negative
  filter to a positive gate, which would then have to be taught that
  `3" CD Single` and `CD (… in LP Replica Jacket)` — both live, both CDs —
  are not records. Not worth it for one product.

### Title parsing: strip the marker, split on quotes

```
[MARKER:] Artist "Album" DESCRIPTOR
```

99.7% of vinyl-typed products (9,757 of 9,785) match, and the 28 that do not
are dropped rather than guessed at, following `darksiderecords.py`. They are
genuinely unparseable: unbalanced quotes (`Frank Turner "Tape Deck Heart LP`),
no quotes at all (`Sophie S/T 2xLP`, `Alkaline Trio / Hot Water Music Split
LP`), and shirt-plus-record bundles from the `Fast Bundle` vendor.

Three details are load-bearing.

**The opening and closing delimiter may be `"`, a curly `“`/`”`, or a doubled
apostrophe `''`.** The `''` form is not decorative — 28 products use it and
nothing else, and `Superchunk ''I Hate Music'' LP` is unparseable without it.
A doubled apostrophe cannot be confused with a possessive (`Guns N' Roses`)
because the alternation requires two adjacent apostrophes.

**The album group is non-greedy and the artist group is too**, so a nested
quote inside the pressing notes cannot swallow the album: `Robert Ziegler
"Music From The Star Wars Saga (Soundtrack)" 2xLP (May The 4th Be With You
Edition "Hyperspace" Blue Splatter Vinyl)` parses correctly. The same
non-greediness is what makes the trailing inch mark on a 7" harmless — `Ben
Pirani & The Means of Production "I Know It Hurts / Something So Precious" 7"`
has three quote characters and still splits at the right two.

**An album that looks like a format token must not be rejected.** A guard on
"the album is just a number or an `LP`" was written and thrown away: it fires
on Adele "19", Adele "21", Blur "13", Beach House "7", Mac DeMarco "2", FKA
Twigs "LP1" and Joey Badass "1999", all real albums. One live product is
genuinely mis-split by its own unbalanced quote (`Fat Heaven / Raging Nathans
'Split" 7"` → album ` 7`); one bad row is the cheaper mistake.

### Invisible characters

195 products carry a `U+200E LEFT-TO-RIGHT MARK` between the artist and the
opening quote (`Used Vinyl: A.R.B ‎"Yellow Blood" LP`), and single products
carry a tab and an ideographic space. `str.strip()` does not remove U+200E —
it is a format character, not whitespace — so without explicit handling 195
artists would carry an invisible trailing character.

This matters more than it looks: `db._library_match_fragment` compares
`LOWER(s.artist) = LOWER(c.artist)` exactly, so an invisible character is the
difference between a match and silence. Format characters are removed and
whitespace collapsed before parsing. Titles carry no HTML entities, so no
unescaping is needed.

### Status markers: stripped from the front, re-emitted as a suffix

The store prefixes a status marker onto the title, ahead of the artist. Live
forms, verbatim:

| marker | forms seen | products |
|---|---|---|
| used | `Used Vinyl:`, `USED VINYL:`, `Used VInyl:`, `Used LP:`, `Used CD:`, `Used Viny:` | 3,651 |
| pre-order | `PRE-ORDER:`, `PRE-ORDE:`, `PRE-ORDEDR:` | 1,733 |
| damaged | `DAMAGED COVER:`, `DAMAGED COVER `, `DAMAGED COVER - `, `DAMAGED:`, `DAMAGE:` | 102 |

Every one of these must come off before the artist can be read — otherwise
the artist is `Used Vinyl: Nirvana`, which matches nothing. The marker is
then appended to the *album* instead, as ` (Used)`, ` (Pre-Order)` or
` (Damaged)`.

Front to back is not cosmetic. `db._library_match_fragment` matches stock
against catalog on `LOWER(s.title) = LOWER(c.title) OR LOWER(s.title) LIKE
LOWER(c.title) || ' %'` — exact **or prefix-with-space** — so a suffix still
matches the catalog title and a prefix never would. The same reasoning
`darksiderecords.py` used to justify keeping its `(DAMAGED)` marker verbatim
applies here, with the extra step that this store's marker has to be moved to
survive it.

Marker detection reads the title only, not tags or vendor, and the
agreement between them is close enough that nothing is lost: 3,651
title-marked used products against 3,501 tag-marked and 3,563 with vendor
`Used Product` — the title is the superset in both cases, missing exactly one
tag-marked product. Pre-orders: 1,733 title-marked against 1,717 tag-marked,
again one tag-only product. Two of the store's own `handle`s (
`pre-order-arlo-parks-…`, `pre-order-king-tuff-…`) are stale pre-order
residue on titles that no longer claim to be pre-orders, which is
`darksiderecords.py`'s lesson about trusting tags, arriving here through a
different field. The title is what the store shows a customer, and it is what
this crawler reads.

**The bare `DAMAGED ` form without a separator is deliberately not stripped**,
costing one live product (`DAMAGED Sultans "Ghost Ship" LP`). "Damaged Bug" is
a real band with several LPs — exactly the kind of record a Bay Area store
stocks — and a rule that strips a leading `DAMAGED ` would rewrite its artist
to `Bug`. `DAMAGED COVER` needs no separator because two words in that order
cannot be an artist name.

### Pre-orders are kept, and marked

The ` (Pre-Order)` suffix follows the sibling label crawlers (`relapse.py`,
`centurymedia.py`, `fatwreck.py`, `nuclearblast.py`, `carparkrecords.py`,
`asianmanrecords.py`). Unlike `darksiderecords.py`, whose pre-order tags were
stale residue on already-released stock, this store's markers are current:
they are backed by live monthly tags (`SEPTEMBER 2026 pre-order`,
`OCTOBER 2026 pre-order`, `NOVEMBER 2026 pre-order`) and by titles the store
edits when the record lands.

**The availability bypass those siblings pair with the suffix is deliberately
not implemented**, and this is the one place the suffix and the bypass come
apart. Those crawlers bypass `available` on a pre-order because their stores
flag pre-order variants `available: false` while still selling them. This
store does not: all 1,733 pre-order-marked products report `available: true`,
so a bypass would emit exactly zero extra rows today.

What it would do instead is decide the future case wrongly. On a store whose
pre-orders are all currently available, a pre-order that later reads
`available: false` most plausibly means the allocation is gone, not that the
flag is unreliable — and publishing an unbuyable record at a price is a worse
failure than omitting a buyable one. `darksiderecords.py`, the nearest
retail-store sibling, made the same call and pins it with a test; so does
this crawler. If the store ever does start flagging live pre-orders
unavailable, the bypass becomes correct and the test is the place to record
that it changed.

### Used stock is kept, and marked

`darksiderecords.py` excluded used vinyl on the grounds that it is one-off
stock that churns between syncs and carries no gradeable condition.
Both facts hold here. The conclusion does not, for three reasons:

1. **Scale.** Used is 37% of this store's vinyl, and it is the half a
   collector is most likely to be hunting — out-of-print pressings,
   Japanese issues, one-off copies. A crawler that skipped it would report
   this store as substantially poorer than it is.
2. **There is precedent for carrying it.** `angryyoungandpoor.py` crawls its
   `Used-Records` category and appends ` (Used)` to the title;
   `waterloorecords.py` and `amoeba.py` both see used stock and each record
   their own policy for pricing it. Exclusion is not a fleet convention.
3. **Staleness is the shared crawl's problem, not this crawler's.** A sold
   one-off drops out of `products.json` and `replace_stock_items()` removes
   its row on the next sync, exactly as it does for a sold-out new title.

What "used" costs in fidelity is condition: `Used Vinyl: Wire "154" LP (1979
Japanese Issue)` becomes `154 LP (1979 Japanese Issue) (Used)`, with the
store's own pressing note preserved but no VG+/NM grade, because there is
nowhere to put one. The ` (Used)` marker is what makes the below-market price
in the Store column self-explanatory — the same argument `darksiderecords.py`
made for keeping `(DAMAGED)`.

### The format descriptor is kept in the title

The album is emitted as `{album} {descriptor}`, e.g. `Nagatsuki Kannazuki LP
(1976 Japanese Press) (Used)` rather than bare `Nagatsuki Kannazuki`. Both
forms match the catalog through the prefix-with-space rule above, so this is
purely about whether two Store rows can be told apart, and the descriptor is
what tells them apart.

Measured over the emitted rows: dropping the descriptor leaves 8,743 distinct
`(artist, album)` pairs and 1,003 rows that read identically to another row;
keeping it leaves 9,509 distinct pairs and 237 such rows. The store stocks ten
separate copies of `The Clash "S/T (Pearl Harbour)" LP + 7" (1979 Japanese
Issue)`, nine at $125.00 and one at $120.00.

Those remaining 237 are not a database problem — `db.compute_item_key()`
hashes `(artist, title, url)` and each copy is its own product with its own
`handle`, so all ten Clash copies get distinct `item_key`s. They are ten
genuinely different physical records, and showing ten rows is the honest
answer.

### Per-variant handling

7 of 9,785 products have more than one variant; the rest have exactly one,
titled `Default Title`. The seven are real editions
(`Dove Gray LP` / `Black LP`, `LP` / `Cassette` / `Limited Edition Color
Vinyl`), so two rules apply to them and to nothing else:

- **A competing-format variant is dropped** when the product has more than
  one, following `asianmanrecords.py`. Two live products
  (`Roger Bekono "Roger Bekono"`, `King Tuff "Smalltown Stardust"`) have their
  vinyl variants sold out and only a cassette in stock; without this rule each
  would emit a cassette as though it were a record. The rule is not applied to
  single-variant products, whose sole variant is usually the `Default Title`
  placeholder.
- **A multi-variant product appends ` — {variant title}`** to the title.
  `db.compute_item_key()` hashes `(artist, title, url)` and the url is
  per-product, so without the descriptor two available variants would collapse
  onto one `item_key`: one marketplace lookup for two pressings, and two
  indistinguishable Store rows at different prices.

The multi-variant gate counts *surviving* variants, not *available* ones. If
it counted available ones, a two-variant product whose sibling sold out would
drop its descriptor, changing that row's `item_key` and orphaning its
`listings` and saved-item rows.

### Prices and images

Prices are plain decimal strings; every available variant on the store parses
as a float and none is zero. Currency is USD — an Oakland store pricing in
dollars. `cover_image_url` goes through `shopify_catalog.resolve_cover_image`,
which prefers the variant's own `featured_image` (12 variants live have one,
all on the multi-variant products) and falls back to the product's first
image; 68 vinyl products have no image at all and must yield `None` rather
than an `IndexError`.

### A total parse failure raises rather than returning empty

`db.replace_stock_items()` DELETEs this crawler's rows before inserting
anything, and `_sync_stock` only skips that call when the crawl *raised* — a
generator that completes with nothing to show is treated as a healthy crawl of
an empty store. So if this store stopped writing `Artist "Album"` titles,
every product would fail to parse, the crawler would yield nothing, and the
whole snapshot would be deleted with the site recorded as succeeding.

`crawl_catalog` therefore counts three things and raises on two of them: no
products returned by the collection at all (renamed or removed), and no vinyl
title parsed out of a non-empty set of vinyl-typed products (title drift).
`dischordrecords.py` and `sideonedummyrecords.py` reach for the same guard on
their own stores, for the same reason.

The counters are what keep the guard from firing on the states that
legitimately yield nothing. A sold-out catalog, a competing-format descriptor
and an unavailable variant all parse *first* and are dropped afterwards, so
they leave `title_parsed` non-zero. This is also why the descriptor's
format rejection lives in `_items` rather than `_parse_title`: `None` from
`_parse_title` has to mean "this title did not parse" and nothing else, or a
catalog of mistyped CDs would read as drift.

One mode is knowingly uncovered: if the store renamed its vinyl
`product_type` values, every product would fail `_is_vinyl`, `vinyl_seen`
would stay zero and the wipe would go through. Guarding it would mean raising
whenever a catalog contains no vinyl at all, which is indistinguishable from a
legitimately vinyl-free page. The two covered modes are the likely ones;
`product_type` is a stable Shopify field.

## Crawl citizenship

One GET per page against a public JSON endpoint, paced by the shared
`crawl_delay_seconds` setting through `iter_products()`, with the same
`consecutive_failure_limit` retry budget and the same fail-fast-on-429
behaviour every Shopify sibling gets. 45 requests per sync: 44 product pages
plus the empty page `iter_products()` needs to learn the collection is
exhausted, since it has no total to count down. No browser, no login, no
UCP/MCP integration.

## Testing

`backend/tests/test_onetwothreefourgo_crawler.py`, mocking the two
`products.json` pages with `respx` exactly as the sibling crawler tests do.
Most fixtures are verbatim live products, one per behaviour the design above
commits to; where a behaviour has no live example the fixture is a live
product with one field altered, or an invented product, and says which at its
own definition. The invented ones exist precisely because the live data cannot
reach them: a band actually named "Damaged Bug", a `CD EP` descriptor, blank
and placeholder variant names, and a store that has stopped writing quoted
titles. Behaviours covered: each quote form, each marker form, the marker-to-suffix move, the
`DAMAGED Sultans` non-strip, the U+200E artist, the numeric album, the
mistyped CD, the missing image, the cassette-only multi-variant product, and
the item-key stability of a multi-variant title when a sibling sells out.
