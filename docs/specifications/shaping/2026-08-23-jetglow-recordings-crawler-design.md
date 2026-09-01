# Jetglow Recordings store crawler design

Date: 2026-08-23
Branch: `claude/competent-saha-636e61`

## Problem

Jetglow Recordings (`jetglowrecordings.bigcartel.com`) — an Italian hard
rock, glam, and punk label (Warrior Soul, Kory Clarke, Space Age Playboys,
We Are Impala, Mr. Woland, Cornea, Stoneville) — is not covered by any
existing crawler.

It is a Big Cartel storefront, the same platform as
`backend/crawlers/asbestosrecords.py`, and that sibling is the closest
template: one unpaginated `/products.json` fetch, `options` in place of
Shopify `variants`, no `vendor` field but a store-curated `artists` array,
and `ARTIST - ALBUM` product names. The parsing shape is reused directly.

Three things about *this* store diverge from that sibling and drive the
design below. All three were confirmed against the live feed on
2026-08-23; each is a place where copying `asbestosrecords.py` verbatim
would produce wrong rows, not merely fewer rows.

## Scope

Add `backend/crawlers/jetglowrecordings.py` as a `crawler_type="catalog"`
plugin. No new shared code, no changes to any existing crawler.

**Non-goals**

- **No browser.** Confirmed live: `/products.json` is served to plain
  `httpx` with no bot interstitial (HTTP 200, 186 KB).
- **No CD/cassette/merch coverage.** This app's stock pipeline is
  vinyl-only by convention (`format: "Vinyl"` hardcoded across every
  sibling catalog crawler). The store sells CDs, cassettes, t-shirts,
  posters, and screenprints; the gates below exclude all of them.

## Technical grounding

All figures confirmed live on 2026-08-23 against `/products.json`
(50 products, 114 options).

### The catalog is 50 products, and that is the whole catalog

`/products.json` returns 50 products. `page=` and `limit=` are silently
ignored — `?page=2` returns the identical 50 rows beginning with the same
first product — exactly as `asbestosrecords.py` documents for its own
store. So one request per sync, still paced like every sibling.

50 is a suspiciously round number for a page cap, so completeness was
verified from three independent endpoints rather than assumed:

| Source | Count |
|---|---|
| `/products.json` | 50 |
| `/products.xml` | 50 |
| `/category/vinyl-cassette-cd.json` | 47 |
| `/category/t-shirts-sweatshirts.json` | 1 |
| `/category/poster-postcard.json` | 1 |
| `/category/screenprinted-artworks.json` | 1 |

Every product carries exactly one category (0 products have an empty
`categories` array), and the four category feeds sum to 47+1+1+1 = 50,
matching both whole-catalog feeds. The catalog is fully enumerated; the
round number is a coincidence, not a truncation.

### Divergence 1: the media category is not a vinyl category

`asbestosrecords.py` ORs its name regex with a check for a category
literally named `Vinyl`. This store has no such category. Its single
media category is named **`Vinyl - Cassette - CD`** — it lumps all three
physical formats together, and 47 of 50 products sit in it.

So the category answers "is this a record release rather than a t-shirt",
not "is this vinyl". It is used here only as a **merch gate**: requiring
`Vinyl - Cassette - CD` drops the t-shirt, the poster, and the screenprint
(one product each). The vinyl decision is made separately, per option.

This distinction is load-bearing, not pedantic. The poster product
(`"Hobolo" - Raquel Burgueno Sepulvea`, category `Poster & Postcard`)
carries an option literally named `Poster + Vinyl`, which the per-option
vinyl regex would otherwise happily accept as a record.

### Divergence 2: format lives in the option, not the product name

`asbestosrecords.py` gates on the *product* name, because its store spells
the format there. This store frequently does not:

- `LOWDRIVE - RISE` → option `Vinyl`
- `WARRIOR SOUL - CLASSICS` → option `Vinyl`
- `KORY CLARKE - PAYBACK'S A BITCH` → option `Vinyl`

and just as often mixes formats *within* one product:

- `KELLY GANG - MIND THE BOLLOCKS HERE'S THE KELLY GANG` → `CD` (€8),
  `Vinyl` (€17), `Special Box` (€25)
- `KRÖWNN - BLÜEDEEP` → `Vinyl Blue`, `Vinyl Blue + CD`, `CD`, `Cassette`,
  `T-Shirt + Postcard w/download code`, and two opaque `Special Bluedeep
  Bundle` rows

A product-level gate cannot express this: it would emit that store's CD
and cassette rows as vinyl. The gate is therefore per option, positive
(`\bvinyls?\b`, the `LP` family, or an inch mark), matching
`carparkrecords.py`'s per-variant shape.

A positive filter is the right polarity *here* — unlike Carpark, whose
design doc rejects it because that store's vinyl variants are named by
colour alone (`Eco Mix Red`) with no format word. This store's vinyl
options always name the format (`Black Vinyl`, `Smoked Red Vinyl`,
`Vinyl + CD`, `Bundle Black Vinyls + Digipack CD`), so a positive match
loses nothing and, unlike a negative filter, correctly declines the
genuinely ambiguous `Special Bluedeep Bundle 1` / `Special Box` rows whose
contents are unknowable from the feed.

Big Cartel has no Shopify-style `Default Title` placeholder: a
single-option product repeats its own name as the option name. Such an
option carries no independent format signal, so the gate falls back to the
product name for it. That fallback is load-bearing — it is the only thing
keeping `THE BROKENDOLLS - CARILLON INFERNALE (LP VERSION)`,
`THE HAPPY GAYS - Lonely Men In Love - LP version, CD included`, and
`THE WANKERSS - Blackborn Special Edition Double LP (CD included)`.

`\bep\b` and the inch mark have **zero** live matches on this store; the
inch mark is kept anyway (a 7" is plausible future stock and a digit
followed by a quote mark cannot misfire), `\bep\b` is omitted rather than
carried over from the sibling untested.

### Divergence 3: `sold_out` on the option is inert; product `status` is the signal

**All 114 options on this store report `sold_out: false`** — including
every option of the six products the storefront itself renders as
"Sold Out". Availability is carried by the *product-level* `status` field
(`active` on 44, `sold-out` on 6).

`asbestosrecords.py` filters on `option.sold_out` alone. Copied verbatim,
that would publish all six sold-out products as in-stock, among them
`WARRIOR SOUL - THE SPACE AGE PLAYBOYS (30TH ANNIVERSARY) DOUBLE VINYL
ED.`. This crawler gates on `status == "active"` and *also* honours
`option.sold_out` when set — the option flag is insufficient here, not
wrong, and a partially sold-out product could set it later.

### Trailing format segments are stripped from the album

Product names follow `ARTIST - ALBUM - FORMAT BLURB`, where the blurb is
one of `VINYL`, `CD AND CASSETTE`, `VINYL EDITION`, `VINYLS AND BUNDLES`,
`VINYL AND CD`, `LP version, CD included`, etc. Splitting on the first
separator leaves that blurb inside the album, and appending the option
name then doubles it:

> `DRUGS, GOD AND THE NEW REPUBLIC (35th ANNYVERSARY ED.) - VINYL — Vinyl`

So a trailing ` - ` segment is dropped from the album when every word in
it is a format/edition word. This is display-only cleanup;
`db._library_match_fragment` matches on exact-or-prefix-with-space, so
both forms already matched Discogs — the stripped form is simply the
correct title (`VISIONS`, `BURN THE STREETS AGAIN`, `LES EFIMERES`,
`APART`, `II`, `Lonely Men In Love`).

The vocabulary is deliberately narrow: it holds only format and edition
words, never ordinary title words. `the`, `with`, `deluxe`, `collectors`,
and `only` were trialled and **removed** — they change nothing on the live
feed (verified: 0 products differ between the wide and narrow
vocabularies) and each one risks eating a real album title. The strip runs
*after* the format gate, so it can never remove the token the gate
depended on.

`cleorecs.py`'s `_strip_trailing_parens` is the precedent for a
store-specific title-preprocessing pass; the shared-title-split-helper
design doc already records it as a legitimate documented exception rather
than a bug.

### Artist attribution

Reused unchanged from `asbestosrecords.py`: split the name on the first
whitespace-flanked hyphen, fall back to the curated `artists[0].name`, and
skip the product when neither yields anything. The whitespace requirement
on at least one side is this repo's standard fix for clipping a hyphenated
word.

The literal title split must keep winning over the curated field, and this
store re-confirms why: `WARRIOR SOUL - THE SPACE AGE PLAYBOYS (30TH
ANNIVERSARY) CD ED.` is tagged `artists: ["Space Age Playboys"]`, and all
three `KORY CLARKE` releases are tagged `Kory Clarke / Warrior Soul`,
which matches no Discogs artist.

`Various Artists` → `Various` normalisation is carried over. It has no
live match today, but it guards a documented repo-wide matching failure
(`_library_match_fragment` does exact `LOWER()` equality on artist, so
`Various Artists` would silently never match).

Prices are **EUR**, not USD — confirmed from the storefront's own
`data-currency-code="EUR"` markup and `"currency":"EUR"` in its embedded
config. Every sibling Big Cartel/Shopify crawler hardcodes `USD`;
`darkdescentrecords.py` is the precedent for a non-USD catalog crawler,
and `currency` is a pass-through string end to end
(`frontend/src/api/types.ts` types it `string | null`).

## Expected yield

59 vinyl rows from 44 active products; 40 options correctly declined
(CD/cassette/merch/opaque bundles), 6 products excluded as sold out, 3 as
non-media, and 4 for having no vinyl option and no format token in the
product name.

## Testing

`backend/tests/test_jetglowrecordings_crawler.py` — flat in `tests/`, like
every pure-HTTP catalog crawler; `tests/crawlers/` holds the
Playwright-driven ones. Fixtures are real products copied from the live
feed. `respx` mocks the one HTTP call.

`conftest.py`'s `_fast_catalog_crawl_sleep` gains
`crawlers.jetglowrecordings`: this crawler paces its own request with a
module-local `sleep` rather than going through
`shopify_catalog.iter_products()`, so without that entry its tests would
sleep `crawl_delay_seconds` (default 30s) for real.
*(2026-09-01: pacing and retries now come from
`catalog_http.get_with_retry()` — the conftest entry survives only for its
`load_config` patch; the module-local `sleep` binding is gone. See
[`2026-09-01-stock-crawl-timeout-retry-design.md`](2026-09-01-stock-crawl-timeout-retry-design.md).)*
