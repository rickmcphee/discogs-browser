# XL Recordings crawler design

Status: implemented
Date: 2026-09-21
Related: `backend/crawlers/xlrecordings.py`,
`backend/tests/test_xlrecordings_crawler.py`, `backend/shopify_catalog.py`

## Problem

XL Recordings sells its catalogue direct, and none of it reaches the Store tab.
The label's shelf is a good fit for this app's users: Radiohead and The Smile,
Thom Yorke and Jonny Greenwood, Adele, The Prodigy, Dizzee Rascal, Arca,
Overmono, Four Tet and Peggy Gou, with a deep 12" singles shelf beside the
albums.

## Scope

One new `catalog`-type crawler plugin plus its tests, and a `min_delay`
parameter added to `shopify_catalog.iter_products()` so the store's
`robots.txt` `Crawl-delay` can be honoured by the design rather than asserted.
No schema change, no frontend change, no new dependency.

## Which storefront

The request named `shop.xlrecordings.com`; this crawler walks
`shopusa.xlrecordings.com` instead, at the requester's direction. The UK
storefront is unreachable to anything but a full browser: every path on it —
`robots.txt` and `meta.json` included — answers `202` with an empty body and
an `x-amzn-waf-action: challenge` header, so an httpx-based catalog walk gets
no data at all and a Playwright one would be solving a bot challenge on every
page. The US storefront serves the same label's catalogue over the ordinary
Shopify endpoints with no challenge, and prices in USD. (No claim is intended
about the rest of the fleet, which quotes whatever its stores charge in --
Bella Union and Monorail Music are both GBP.)

Recorded because it is the kind of thing a later session re-discovers the hard
way: the UK host is not "down" and not blocking this app specifically, it is
behind a WAF that answers every unauthenticated client the same way.

## Technical grounding

Live findings, `shopusa.xlrecordings.com`, 2026-09-21. It is Shopify
(`myshopify_domain` `xlrecordingsprod`), currency USD.

- `meta.json` reports 177 published products. Shopify's built-in `all`
  collection walks to exactly 177, and the store's own curated `all-releases`
  shelf to the same 177 — same product ids, all three agree.
- `collections.json` disagrees, reporting `products_count: 421` for
  `all-releases`. The endpoint that answers with products wins; the same
  disagreement is recorded on Bella Union, in the other direction.
- `product_type` is the **release kind**, not the medium: `Album`, `Single`,
  `EP`, `Merch`, and blank on a handful.
- The product title is the **album alone**. There is no `Artist - Album`
  convention to split.
- Variant titles are `{album} - {descriptor}`, the album name being baked into
  the store's `Format` option values (`Dopamine Chamber - Blue Vinyl LP`).

### Collection choice: `all`

Shopify's built-in all-products collection, rather than the store's
`all-releases`. The two agreed exactly on the day of writing, so this is not a
coverage decision — it is which kind of completeness to depend on.
`all-releases` is hand-curated, so a release nobody adds to it is invisible
with nothing to notice; `all` cannot omit a published product. Merch sits in
both, so walking `all` costs no extra filtering — the gates below would have
to run either way.

### The artist: `vendor` first, a sole tag second

Unlike Monorail Music, whose `vendor` is always the label, this store's
`vendor` names the **act** on most of the catalogue (`Fontaines D.C.`,
`Radiohead`, `Peggy Gou`). On 65 of 177 products it names the shop instead —
`XLRecordingsProd` (the myshopify handle) or `XL Recordings USA` — and on
those the act is a tag:

```
'Gi Mi Keys Back / Auto Fake'  vendor='XL Recordings USA'  tags=['Blawan', 'preorder']
'I Hear You'                   vendor='XLRecordingsProd'   tags=['2024', 'Peggy Gou']
'XL Banger'                    vendor='XL Recordings USA'  tags=['Various Artists']
```

The tags mix the act with a release year, a `12" singles` format shelf, an
`XL Merch` shelf and a `preorder` state. Subtract those four and 173 of 177
products carry exactly one tag, 3 carry two, and 1 carries none.

**Why `vendor` stays primary.** Where both sources name an artist they agree
on 110 of 112 products, and on **both** exceptions `vendor` is the correct
one. Shopify stores tags as one comma-separated string, so an act whose name
contains a comma arrives already split — and alphabetised, so the comma cannot
even be put back:

```
'Goblin'         vendor='Tyler, The Creator'          tags=['The Creator', 'Tyler']
"We're New Here" vendor='Gil Scott-Heron & Jamie xx'  tags=['Gil Scott-Heron']
```

The second is not a split at all: the tag simply names one of two
collaborators. `vendor` carries both names whole.

This is the same hazard Monorail's design records, with the sources swapped —
there the title is whole and the tag is the fragment. The rule is the same
because the failure is: a fragment is indistinguishable from a complete name
once it is in the payload, so the field that is *structurally* whole leads.

**Why exactly one surviving tag.** Two survivors may equally be one
comma-split name or two collaborators, and nothing in the payload separates
the readings. The crawler names no artist rather than guessing, and the
product is skipped. Naming the wrong one is worse than naming none: the artist
is hashed into `item_key` and is what `db._library_release_match_sql` matches
a row against the user's library, so a guess produces a row that is both
permanently mis-keyed and unmatchable — and, being a plausible-looking row, it
is one nobody goes looking for.

**Why the label test is a prefix, not two literals.** `XL Recordings UK` is
not a live vendor value. Matching the normalised prefix `xlrecordings` means a
storefront the label opens later is read as the label, rather than published
as an artist of that name fronting every record on it.

**The year tag is matched by shape** (`^(19|20)\d{2}$`), not enumerated —
enumerating it starts publishing records by an artist called "2027" the
January after this ships.

### The pressing descriptor is the tail of the variant title

Split on the **last** spaced dash, not the first. The album half carries its
own separators in two live shapes, and only the tail is reliably the format:

```
'Gi Mi Keys Back / Auto Fake - 12" Single'    ->  '12" Single'   (double A-side)
'SOTC II - LP'                                ->  'LP'           (prefix is not the product title)
'Rooty - Blue & Pink 2X LP / Rooty Anniversary T-shirt - Black Small'
                                              ->  'Black Small'  (bundle)
```

A variant title with no separator is the descriptor entire — the store's
untyped merch names a bare `S`/`M`/`L`/`XL` that way.

`option1` is not a shortcut: it is byte-identical to the variant title,
because the album name is inside the option *value*.

### The format gate is per variant, and it is positive at the product level

This store publishes **no product-level format field at all** — `product_type`
is the release kind. So unlike the sibling Shopify crawlers, which can fall
back on `product_type == "Vinyl"`, the only thing that says a product is a
record is a variant descriptor naming one. A product claims a record when any
of its variants does.

Per descriptor the gate is ordered: merch words and a trailing garment size
reject, then a record word admits, then a word naming another medium rejects,
and **anything left is admitted**.

**Why the tail admits rather than rejects.** The live `Picture Disc` — a
Peggy Gou pressing at $28.03, priced with the `White Label LP` beside it and
well above the $13.58 CD — names neither `LP` nor `Vinyl`, and is the one live
descriptor that reaches that final line. Enumerating the record words instead
would silently drop it, and would drop the next descriptor the store invents
too. Over the whole catalogue, the descriptors reaching that line are that one
record and garment sizes, and the sizes are rejected a line earlier.

**Why `cs` is listed as another medium.** It is this store's own abbreviation
for a cassette (`CS Album`, `CS EP`), and it names no medium a reader would
recognise, so without it the default-admit tail publishes a cassette at a
cassette's price under this crawler's `Vinyl` format. It is safe to list
precisely *because* the record word is tested first: the live
`Deluxe 3X LP + CS + Books LP` is a vinyl box set that includes a cassette,
and it is admitted on its `3X LP` before the `cs` pattern is reached.

Two live products are records with no vinyl pressing at all —
`Gimme my gun` (`CD Maxi`) and `The Fat of the Land - Expanded Edition` (`CD`)
— and both correctly yield nothing.

### Bundles

Two live products are bundles, and each defeats the other's test:

```
'Basement Jaxx Rooty Bundle'  options=['Rooty (Format)', 'Rooty Anniversary T-shirt (Format)']
                              variant tails: 'Black Small' ... 'Black XXL'
'Nourished By Time Bundle'    options=['The Passionate Ones (Format)']
                              variant tails: 'Crystal Clear LP', 'LP'
```

The second is the dangerous one. `product_type` is blank, it carries one
option like any record, and both tails read as perfectly ordinary pressings —
at $51.25 and $49.80 against the $28.03 the same record costs on its own
product page. Nothing but the word in the product title marks it. It is
currently also unresolvable for an artist (label vendor, no tags), which would
skip it anyway — but that is a coincidence of today's data, not a rule: one
added tag would publish a bundle price as a record's.

So both signals are kept, and each is the other's backstop:

- **The word `Bundle` in the product title.** Catches both live bundles.
- **More than one Shopify option.** Catches a bundle that does not say so:
  Shopify gives a product one option per thing being chosen, and every record
  in this store has exactly one (`Format`).

Neither is read from the descriptor, because a bundle's tail belongs to
whichever half was written last — the Rooty one tails as the shirt, hiding the
`2X LP`, and its sibling would tail as an ordinary `LP`.

### Merch

`product_type == "Merch"` is the store's own claim, and it is the one test in
this crawler that reads no title at all — neither the product's nor a
variant's — which is what keeps it working on a product whose every other
field has drifted. It is not sufficient alone: the store leaves some merch
untyped (`Celeste T-Shirt`, `Temporary MA1 Flight Jacket`,
`Solstice Equinox White T-Shirt`), and those are caught by their bare-size
descriptors instead.

The garment-size pattern is anchored at the **end** of the descriptor, because
a size word is only ever a size in that position here (`Black Small`,
`Charcoal Cotton XL`, `White Cotton XXL`). No live record descriptor ends in
one, and the alternation must reach `$`, so the `L` of an `LP` cannot satisfy
it with the `P` still to come.

`print` is deliberately **not** a merch word: `Deluxe 2X LP w/ signed print`
and `Blue Yolk LP + Signed Print` are records with an extra, priced at a
record's price.

### The row's title keeps the pressing

`item_key` hashes `(artist, title, url)`, and every variant of a product
shares the artist and the URL, so the descriptor is what keeps two pressings
of one release apart. The album leads and the descriptor follows, because
`db._library_release_match_sql` matches a stock row against a library release
on an exact-or-prefix-with-space test — a row titled
`I Hear You — Blue Vinyl LP` satisfies a library `I Hear You` only while the
album comes first.

The descriptor is appended on every row that names one, not only when the
product has more than one variant: a sibling being listed or delisted must not
re-title the rows and orphan the listings, judgments and saves keyed on the
old identity.

Over the live catalogue this yields 158 rows whose `(artist, title, url)`
triples are all distinct.

### Availability, and why there is no pre-order marker

Only the literal `True` admits a variant. The string `"false"` is truthy, so a
falsiness test would publish a sold-out record as in stock; anything else —
`False`, `"false"`, `1`, `None`, absent — is skipped, which is also what keeps
this filter and `_has_readable_stock_flag` agreeing on what "readable" means.

The store does tag pre-orders, and no ` (Pre-Order)` marker is written anyway.
`item_key` hashes the title, so a marker that disappeared when the record
shipped would re-key every one of its pressings at exactly the moment a
waiting user cares most, orphaning the saves and judgments held against the
old key. A pre-order is purchasable at a real price and is listed as an
ordinary row — the same call Bella Union and Byrdland make.

### Prices, currency and images

USD. `bool` is checked before `float()`, since `bool` is an `int` subclass and
`True` would otherwise price a record at 1; non-finite and non-positive values
answer `None`. Every one of the 410 live variant prices parses as a positive
number.

Images use the shared `resolve_cover_image()`: the variant's own picture when
it has one — a specific colour pressing — falling back to the product's first
image. All 158 live rows carry one.

## Crawl citizenship and `robots.txt` compliance

Per the normative section of `2026-08-09-amoeba-store-crawler-design.md`.
This site's findings:

- `robots.txt`'s `User-agent: *` group disallows `/admin`, `/cart`,
  `/checkout(s)`, `/orders`, `/account`, `/search`, `/policies/`,
  `/recommendations/products` and the `sort_by`/`filter`/`+`-encoded crawl
  traps. **None of these covers `/collections/all/products.json`**, the only
  path this crawler requests.
- The store publishes an `/agents.md` naming
  `GET /collections/{handle}/products.json` explicitly under "Read-Only
  Browsing (No Authentication Required)". It also asks agents to back off on
  429, which `catalog_http.get_with_retry()` does by raising on first sight
  rather than retrying.
- Both documents require checkout to never complete without contemporaneous
  buyer approval. This crawler satisfies that trivially: it links out to the
  product page and never transacts.
- Load: 2 GETs per sync. `iter_products()` terminates only on an empty page,
  so 177 products at `limit=250` means one full page then a terminating empty
  one. No detail-page fan-out.

### `min_delay` on `iter_products()`

Unlike the sibling Shopify stores, whose `robots.txt` names no `Crawl-delay`,
this one asks for `Crawl-delay: 10`. `crawl_delay_seconds` is admin-editable
with no lower bound — at its default of 30 the jitter gives 15-30s and honours
the request comfortably, but a setting below 20 would not, and 0 would send
requests back-to-back. `catalog_http.get_with_retry()` already takes a
`min_delay` floor for exactly this (added for M-Theory Audio), and floors both
ends of its jitter window with it; `iter_products()` simply had no way to pass
one through.

It is added as a keyword-only parameter defaulting to `0.0`, which leaves
every existing caller byte-for-byte unchanged.

## Drift guards

`db.replace_stock_items()` DELETEs this crawler's previous snapshot before
inserting, and `_sync_stock` only skips that call when the crawl raised — so a
completed-but-empty walk is destructive where a raise is inert. Each guard
names a distinct way the payload can stop carrying what this crawler reads.

| Guard | Fires when |
| --- | --- |
| collection empty | the walk saw no products at all |
| price-source drift | rows came out and **none** carries a price |
| artist-source drift | nothing yielded, and no product named an artist in `vendor` or its tags |
| identity-source drift | nothing yielded, and some product carries no title, or is a record carrying no handle or no readable artist |
| variant-identity drift | nothing yielded, and some product dropped a variant that carries no usable title and is not provably sold out |
| stock-source drift | nothing yielded, and some record carries no readable availability flag |
| format-source drift | nothing yielded, and no product has a variant naming a record |

Everything but the first two is gated on the walk having produced nothing,
because that is the only outcome that destroys anything: rows on the way out
are proof that every source still answers.

**They are ordered most specific first, and the order is load-bearing.**
Several fire together on one cause, and the first one's message is the
diagnosis a later session reads. Losing the artist sources also empties
`_has_identity`, so the artist guard has to be asked before the identity one
or its cause is reported as the broader one. The format guard is asked **last**
for the same reason in reverse: a catalogue whose titles or variants went
unreadable also stops claiming a format, and those causes name themselves
above.

**Why a format guard exists here at all.** Bella Union's design says
explicitly that it needs none, because its gate is negative and no positive
signal's disappearance can silently empty the walk. That reasoning does not
transfer: the product-level gate here *is* positive, since nothing but a
variant descriptor says a product is a record. The realistic shape of that
drift is Shopify's `Default Title` placeholder — a product carrying it names
no format anywhere, and it is the one thing that empties the format claim
store-wide without touching any other field.

The one empty outcome that must **not** raise is a catalogue that has simply
sold out: every product readable, every pressing a readable `False`.

## Verification

- `backend/tests/test_xlrecordings_crawler.py` — all passing.
  Fixtures are live products captured 2026-09-21 and trimmed to the fields the
  crawler reads; each is marked captured, altered or invented at its
  definition.
- Replayed over the live catalogue captured the same day: 177 products in,
  158 rows out, every row priced, artist-bearing, USD, `Vinyl`, carrying an
  image, and with a distinct `(artist, title, url)`.
- Every variant rejected on a product that *did* yield rows was audited by
  hand: all of them are CDs, cassettes or a DVD. No vinyl pressing is lost.
- Every product yielding no rows was audited the same way: merch, the two
  bundles, the two CD-only releases, and records that are genuinely sold out.

## Runtime/agent document impact

No `.agents/INPUTS.md`, `.agents/OUTPUTS.md` or `.agents/INSTRUCTIONS.md`
exist in this repo. This change adds no new trigger and no new inbound
interface — one new outbound host (`shopusa.xlrecordings.com`).

`backend/version.py`'s `VERSION` is derived from git and is not edited by this
change.
