# SPV Entertainment store crawler design

Date: 2026-08-23
Branch: `claude/spv-store-crawler-2mdf0z`

## Problem

SPV Entertainment (`store.spv.de`) — the official store of SPV GmbH, the
German independent label and distributor founded in 1984, home to the
Steamhammer and Long Branch Records imprints (Sodom, Magnum, Agent Fresco,
Siamese, The Wild!, Satan's Fall) — is not covered by any existing crawler.
It is a Shopify storefront, the same family as the 48 other `catalog`-kind
plugins already in `backend/crawlers/`, 41 of which are Shopify stores crawled
through `shopify_catalog.iter_products()` (counted directly: `grep -l
iter_products backend/crawlers/*.py` returns 42 including this one).

Prices are EUR, not the USD every sibling *Shopify* crawler hardcodes — SPV is
the first Shopify store in the set to price in anything else. It is not the
first EU-domiciled source, though: `jetglowrecordings.py` (Italian, Big Cartel)
already hardcodes EUR, and `darkdescentrecords.py` passes its feed's currency
through. Those two are the precedent, and `currency` is a pass-through string
end to end. (An earlier draft of this doc called SPV the first EU-domiciled
source outright; corrected in review on PR #165.)

Its title convention quotes the album — `Sodom "1982" LP (exclusive)`,
`Magnum "The Monster Roars" LP (white & black marbled vinyl)` — rather than
naming the artist in `vendor` (`centurymedia.py`, `napalmrecords.py`,
`nuclearblast.py`) or splitting the title on a dash (`seasonofmist.py`,
`carparkrecords.py`). That is **not** new to the fleet:
`asianmanrecords.py` already ships a quoted-album primary parser with a
dash-split fallback, recorded as the third exception in
[`2026-08-07-shared-title-split-helper-design.md`](2026-08-07-shared-title-split-helper-design.md).
This crawler reuses that two-stage shape rather than inventing a parallel
one — see "Title parsing" below for the two places it widens it, and that
doc's fifth amendment for the divergence record.

## Scope

Add `backend/crawlers/spv.py` as a `crawler_type="catalog"` plugin, iterating
the store's `vinyl` collection via `shopify_catalog.iter_products()` — no new
shared code needed. `main.py`'s `seed_bundled_crawlers()` picks it up from
`backend/crawlers/` at boot with no registry edit.

**Non-goals**

- **No browser.** Same as every sibling Shopify crawler: `products.json` is a
  plain JSON endpoint, fetched with `httpx` through `shopify_catalog`.
- **No CD/cassette/merch coverage.** This app's stock pipeline is vinyl-only
  by convention (`format: "Vinyl"` hardcoded across every sibling); the title
  blurb gate below excludes them.
- **No release-crawler (`search()`) mode.** Catalog-only, like every other
  label store.

## ⚠️ Unverified against the live feed

**Every sibling crawler's design doc in this directory records figures
confirmed by fetching the store's live feed. This one cannot.** The session
this crawler was written in runs behind an egress proxy that answers `403` to
`CONNECT store.spv.de:443` (and to every other record-store domain — the
policy allowlist covers GitHub, the Anthropic API, and package registries
only). Routing around an organization egress denial is prohibited, so no
figure below is live-confirmed, and none is presented as if it were.

**Confirmed** (from indexed store pages, i.e. the store's own rendered
titles and URLs, not the JSON feed):

- The storefront is Shopify: collection URLs are `/collections/<slug>`
  (`/collections/vinyl`, `/collections/sodom`) and product URLs are
  `/products/<handle>`.
- A `vinyl` collection exists, titled "LP".
- Product titles follow `Artist "Album" FORMAT`, with straight double quotes
  and a trailing format/edition blurb:
  `Sodom "1982" LP (exclusive)`,
  `Magnum "The Monster Roars" LP (white & black marbled vinyl)`,
  `Siamese "Home" LP`, `The Wild! "Wild At Heart" LP`,
  `Agent Fresco "Destrier" LP`,
  `Satan's Fall "Destination Destruction" LP (exclusive)`. All six sampled
  titles match the pattern.
- The store ships from the EU.

**Assumed, and to be checked against `/collections/vinyl/products.json`
before this crawler is enabled in production:**

| Assumption | Why | If wrong |
|---|---|---|
| `products.json` is served unauthenticated and paginates on `?limit=250&page=N` | Shopify default; true on all 41 sibling stores using `iter_products()` | `iter_products()` raises; no silent bad data |
| Prices are EUR | German store, ships from EU | Prices display under the wrong currency — one-line fix |
| `vendor` carries the label, not the artist | Matches `seasonofmist.py`, whose vendor is the label | Nothing — the crawler never reads `vendor`. This assumption is why it doesn't (see "Title parsing"); if it turned out to hold the artist, an unparseable title would be a missed row rather than a wrong one |
| Pre-order products carry a `pre-order`/`preorder` tag | Sibling convention; exact casing/spelling varies per store, so the check is a case-insensitive regex over both spellings rather than an exact `has_tag()` match | Pre-orders lose their ` (Pre-Order)` suffix and their unavailable variants are dropped |
| The `vinyl` collection is vinyl-only | It is titled "LP" | Non-vinyl bleed, caught by the negative blurb gate below |
| Typographic quotes (`“ ”`) may appear alongside straight ones | Not observed in the sample; cheap to accept both | Nothing — the parser handles both |

Most of these fail safe — but not all of them, and an earlier draft of this
section claimed otherwise ("each unknown either degrades to a documented
fallback or raises rather than publishing wrong data silently"). That was an
overclaim, flagged in review on PR #165. The honest split:

**Fails loudly.** A wrong endpoint or pagination assumption makes
`iter_products()` raise; the consecutive-failure breaker cools the source off
and no rows are written. A title the parser can't read yields no artist and is
dropped. These cost coverage, never correctness.

**Fails silently, and would need a human to notice.** Two:

- **The currency.** If prices are not EUR, every SPV row displays under the
  wrong currency. Nothing in the pipeline can detect this — `currency` is a
  pass-through string the crawler asserts rather than derives.
- **The format gate.** It is deliberately negative (see below), so an
  unrecognised descriptor is *kept*. That is the right trade against silently
  dropping stock, but it means non-vinyl bleed in the `vinyl` collection would
  publish as `format: "Vinyl"` rather than being rejected.

Both are bounded and reversible: `replace_stock_items` only ever replaces this
crawler's own rows, so disabling the source in Settings and re-syncing removes
them. Neither can corrupt another source. But neither announces itself, which
is the reason the "Verification still owed" section at the end exists and why
this crawler should be treated as unverified until those checks are run.

## Technical grounding

### Collection choice: `vinyl`

The store curates its own vinyl collection, so the crawler trusts it rather
than crawling a broad collection and filtering — the same call
`centurymedia.py`, `napalmrecords.py`, `peaceville.py`, and `seasonofmist.py`
make. `carparkrecords.py`'s opposite choice (crawl `music`, filter per
variant) was driven by that store's format-specific collections being
confirmed *not* to be supersets of its vinyl stock; there is no evidence of
that here, and the check needs the live feed.

### Title parsing: quoted album, with two fallbacks

```python
_TITLE_RE = re.compile(r'^(?P<artist>[^"“”]+?)\s*[-–—]?\s*["“](?P<album>[^"“”]+)["”]\s*(?P<extra>.*)$')
_DASH_RE  = re.compile(r'^(?P<artist>.+?)(?:\s+[-–—]\s*|\s*[-–—]\s+)(?P<album>.+)$')
```

`asianmanrecords.py`'s equivalent pair is
`^(?P<artist>.+?)\s*[-–—]?\s*"(?P<album>[^"]+)"` and a byte-identical
`_DASH_RE`. Two deliberate widenings, no other differences: typographic
quotes are accepted alongside straight ones (unobserved here, free to
support), and an `extra` group captures the text after the closing quote,
which that sibling has no use for — it gates format per variant, whereas
this store carries format in the title blurb.

Both captures are quote-free character classes, not the sibling's `.+?`. That
matters in both directions: the artist capture cannot swallow the album's opening quote,
and the album capture stops at the *first* closing quote rather than running
to the last quote in the string — so a blurb that quotes a word
(`Sodom "1982" LP (the "exclusive" pressing)`) still yields `1982`.

The optional `[-–—]?` before the opening quote is the sibling's too, for the
`Artist - "Album"` variant; without it the dash is left dangling on the
artist, which would never match a Discogs release.

`_DASH_RE` is the fallback for an unquoted title: the hyphen/en-dash/em-dash
class `cleorecs.py` established and `jackpotrecords.py`/`asianmanrecords.py`
adopted, with `seasonofmist.py`'s whitespace anchoring on at least one side of
the separator, so hyphenated artist names (`Cro-Mags`, `Vio-lence`) aren't
clipped at their internal hyphen the way a plain `\s*-\s*` split clips them.

A title neither regex can parse returns **no artist at all** and is dropped by
the `not artist or not album_title` guard. `_parse_title()` takes only the
title — there is no `vendor` parameter to fall back to, matching
`cleorecs.py`/`jackpotrecords.py`/`asianmanrecords.py`, and unlike
`carparkrecords.py`/`no_idea_records.py`'s publish-under-vendor fallback.
`darkdescentrecords.py`'s "no artist source -> skip" convention: a row
attributed to "Steamhammer" can never match a Discogs release and would just
be catalog noise.

An earlier revision of this branch did thread `vendor` in as a last resort,
which made that guard dead code for this store — a non-empty label vendor
always satisfied it, so unparseable titles published under the label's name,
the exact outcome the guard exists to prevent. Caught in review on PR #165;
`test_unparseable_title_is_skipped_even_when_vendor_is_populated` pins it, and
the test it replaced only passed because it blanked `vendor`.

### Format gate: negative, on the title blurb

```python
_VINYL_RE     = re.compile(r'\b\d*[x×]?lp\b|\bvinyl\b|\b(?:7|10|12)"|\bpicture disc\b', re.IGNORECASE)
_NON_VINYL_RE = re.compile(r'\b(\d*[x×]?cds?|digipa[kc]k?|cassette|tape|mc|\d*[x×]?dvd|blu-?ray|shirt|t-shirt|hoodie|longsleeve|poster|patch|flag|mug|book)\b', re.IGNORECASE)
```

The gate reads the trailing blurb only, never the artist or album, so a
record named *Tape* or *Book* is not mistaken for one.

The `\d*[x×]?` on the LP/CD/DVD forms is load-bearing in both directions, and
arrived in two review rounds on PR #165. A disc count binds to the format word
with no word boundary between them, so a bare `\bcds?\b` cannot match the "CD"
in `2CD` and a double-CD edition passed the gate as Vinyl. The `x`/`×` form is
the same hole one step further out: Shopify stores write the count both ways
(`2LP` and `2xLP` — this repo's own `test_temporaryresidence_crawler.py`
fixtures carry the x form), and without it `2xCD` is published as vinyl *and*
`2xLP+CD` is wrongly dropped, since the bundle's vinyl half no longer matches
the override while its `CD` half still matches the negative side. `_VINYL_RE`,
`_NON_VINYL_RE`, and `_TRAILING_FORMAT_RE` all carry the same allowance so the
two gates and the dash-path splitter cannot disagree.

Negative rather than positive, and deliberately so: the source is the store's
own vinyl collection, so an *unrecognised* blurb (`Deluxe Edition`) is kept.
A positive filter would silently drop stock whose descriptor this list didn't
anticipate — the failure mode `carparkrecords.py`'s doc records for its own
store, where a positive filter would have dropped every bare-colour variant
name. A blurb naming both formats (`LP+CD`) is vinyl: `_VINYL_RE` short-
circuits ahead of `_NON_VINYL_RE`, mirroring that sibling's collision guard.

The gate reads `extra`, which only the quoted parser produces — so on the dash
fallback it was initially inert, and `Sodom - 1982 CD` published as Vinyl with
the format left in the title while the quoted equivalent was correctly dropped.
Same class of defect as the vendor fallback above: a safety check silently
disabled on one code path. `_split_trailing_format()` now splits a trailing
format marker off the dash-path album and hands it to the gate, mirroring
`asianmanrecords.py`'s `_FORMAT_SUFFIX_RE`, which does the same for its own
no-quote titles.

Its vocabulary is deliberately narrower than `_NON_VINYL_RE` — unambiguous
markers only, no bare `MC` or `EP` — because it rewrites the stored title
rather than merely gating on it. The required leading `\s+` means a single-word
album that *is* one of these words (`Sodom - Tape`) has nothing preceding it to
match and survives untouched.

This gate is the one piece of the design that is pure insurance. If the
`vinyl` collection turns out to be strictly vinyl, it never fires.

### Multi-variant products: qualify the title, or collide

`db.compute_item_key` hashes `(artist, title, url)`, and `replace_stock_items`
INSERTs into `stock_items` with no `ON CONFLICT`. Two variants of one product
sharing a title therefore become two physically duplicated rows under a single
identity — indistinguishable in the Store tab, sharing one judgment and one
saved state between them. A multi-variant product qualifies each row with its
variant name (`1982 — Black`), the shape `nuclearblast.py` uses.

Three details, all load-bearing:

- **The count is over the full variant list, never the availability-filtered
  one.** A qualifier that appeared only while a sibling variant happened to be
  in stock would change the title — and with it `item_key` — between syncs,
  orphaning that row's judgment every time stock moved.
- **Shopify's `Default Title` placeholder never reaches a title.** It appears
  on single-variant products, identifies nothing, and would be pure noise.
- **The qualifier precedes the ` (Pre-Order)` suffix**, so the clean album
  title stays an exact or space-terminated prefix of whatever is stored — the
  invariant [`2026-07-06-store-recommended-filter-design.md`](../../superpowers/specs/2026-07-06-store-recommended-filter-design.md)
  depends on.

Found in review on PR #165. Whether this store even has multi-variant products
is one more thing the live feed would settle; the qualifier costs nothing if
every product turns out to have exactly one.

### Pre-order

`_PREORDER_RE = re.compile(r'pre[\s_-]?order', re.IGNORECASE)`, searched over
the product's tags, covering `pre-order`, `preorder`, and `Pre Order` in one
check rather than betting on one spelling — the sibling crawlers each
hardcode the single spelling their store was confirmed to use
(`"preorder"` on Century Media and Carpark, `"pre-order"` on Nuclear Blast),
and that confirmation is exactly what's unavailable here. Shopify normally
serves `tags` as an array; the string form is also accepted and split on
commas, since which one a store returns varies with the API version.

Behaviour is the sibling carve-out: skip unavailable variants unless the
product is pre-order tagged, and append ` (Pre-Order)` to the title when it
is.

### Fields

- **artist / title** — from the title parse above.
- **price** — `float(variant["price"])`, `None` when unparseable.
- **currency** — `"EUR"` (see the assumption table).
- **url** — `f"{base_url}/products/{handle}"`.
- **cover_image_url** — `resolve_cover_image(product, variant)`.
- **format** — `"Vinyl"` unconditionally, matching every sibling.

### Frontend: the price cell had to learn about currency

`currency` was already stored per row, selected by `get_stock_items`, and typed
on `StockItem` — but `StockBrowser.tsx` ignored it and hardcoded a `$`.

This is a **pre-existing live bug this branch exposed, not one it introduced**.
An earlier draft of this section claimed the gap was invisible until SPV added
the first EU-domiciled store; that was wrong, and review on PR #165 caught it.
`jetglowrecordings.py` shipped the day before this branch and hardcodes EUR, so
Jetglow's rows are being rendered as dollars in production right now. Fixing it
here corrects those rows too, not just SPV's.

`views/formatPrice.ts` maps the code to a symbol and is used at the single
price render site, which comparison rows share. A symbol map rather than
`Intl.NumberFormat`: `Intl` would also start inserting thousands separators
into USD prices, changing how every existing source renders in order to fix a
bug in one of them. `toFixed(2)` is kept exactly as it was, so USD output is
byte-for-byte unchanged. A null `currency` defaults to USD — of the 49 stock
sources, only `jetglowrecordings.py` (hardcoded EUR), `darkdescentrecords.py`
(feed pass-through) and this crawler are anything else — so defaulting avoids
regressing pre-existing rows to a bare number. An unmapped-but-real code prints as `27.99 SEK` rather than
guessing a symbol.

### Metadata

```python
site_name = "SPV Entertainment"
base_url  = "https://store.spv.de"
genre     = "metal"          # in test_main.py's valid_genres set
genre_summary = "German independent label and distributor — Steamhammer and Long Branch metal, hard rock, and prog."
crawler_type = "catalog"
```

## Tests

`backend/tests/test_spv_crawler.py`, 38 cases, `respx`-mocked — the same
shape as the sibling crawler tests. Product titles and handles in the
fixtures are real store listings; prices, tags, variants, and image URLs are
synthesized, and the file says so at the top rather than implying a live
capture.

Covered: per-variant yield and field mapping, product-image fallback,
pagination to an empty page, typographic quotes, an apostrophe in the artist
name (`Satan's Fall`), the first-closing-quote stop, all three pre-order tag
spellings including the comma-string form, unavailable-variant drop, the
non-vinyl blurb gate, the `LP+CD` override, the unrecognised-blurb keep, both
dash fallbacks including `Cro-Mags`, the no-artist-source skip, null
variants, and an unparseable price. Later review rounds added the
count-prefixed and multiplier format cases (`2CD`, `2xCD`, `2×CD`, `2xDVD`
dropped; `2LP`, `2xLP`, `2xLP+CD` kept), the four variant-qualifier cases
below, and the spaced `Pre Order` tag spelling the design claimed but nothing
pinned.

No `conftest.py` change is needed: this crawler paces itself through
`shopify_catalog.iter_products()`, whose `sleep` and `load_config` the
existing `_fast_catalog_crawl_sleep` fixture already patches for any test
module named `*_crawler`.

## Verification still owed

Before enabling this crawler on a real deployment, from a host that can reach
`store.spv.de`:

1. `GET /collections/vinyl/products.json?limit=250&page=1` — confirm it is
   served, and page until empty to get the product count.
2. Confirm `currency` on the shop, and what `vendor` actually holds.
3. Confirm the pre-order tag spelling, and whether pre-orders instead rely on
   `available: true` with a body-copy release date (`seasonofmist.py` reads
   `body_html` for exactly that reason).
4. Measure what fraction of titles the quoted-title regex matches, and read
   the residue — the dash fallback and the skip path are sized for a handful
   of stragglers, not a second convention.
5. Check whether the `vinyl` collection carries non-vinyl products, and
   whether the blurb gate catches them.
