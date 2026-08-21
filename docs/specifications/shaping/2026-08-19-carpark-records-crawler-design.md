# Carpark Records store crawler design

Date: 2026-08-19
Branch: `claude/carpark-records-crawler-0adc27`

## Problem

Carpark Records (`store.carparkrecords.com`) — an Annandale/Baltimore
indie label whose roster includes Toro y Moi, Beach House (early
releases), Animal Collective side-projects, Dan Deacon, Speedy Ortiz, and
The Beths, running its own sub-imprints (Company, Wax Nine, Paw Tracks
listed among the store's own vendors) — is not covered by any existing
crawler. It is a standard Shopify storefront, same family as the ~35
label-store `catalog` plugins already in `backend/crawlers/`.

Format lives entirely in the *variant* title here (`LP`, `LP (Black
vinyl)`, `CD`, `Digital`, `Tape`), never in the product title — closest to
`no_idea_records.py`/`anxiousandangry.py`'s per-variant shape, except this
store's variant vocabulary is far noisier: many genuine vinyl variants
carry no `vinyl`/`LP`/inch-mark keyword at all (`Eco Mix Red`, `Limited
Edition Neon Violet with Tangerine Splatter`, `Carpark Exclusive Mandarin
Tree Translucent Orange`), so a positive-match filter (No Idea's approach)
would silently drop a large fraction of real stock. `anxiousandangry.py`'s
negative-per-variant-filter shape fits better, extended to a small
non-vinyl keyword set (`cd`, `cassette`/`cs`, `tape`, `digital`, plus a
`\bcds?\b` suffix match for compound variants like `Gemini I CD`) rather
than that sibling's exact-match-only regex, since this store has no
product-level format gate to lean on — every product-level candidate
(`product_type`, catalog-code prefix) carries no format signal at all, so
the per-variant filter is the *only* format decision this crawler makes.
Title parsing (strip a leading catalog code, split the remainder on the
artist/title dash) is a new pattern not shared by any sibling — no
existing crawler's titles carry a catalog-number prefix.

## Scope

Add `backend/crawlers/carparkrecords.py` as a `crawler_type="catalog"`
plugin, iterating the site's `music` collection via
`shopify_catalog.iter_products()` — no new shared code needed.

**Non-goals**

- **No browser.** Confirmed live: `products.json` is served to plain
  `httpx` with no Cloudflare gate or bot interstitial.
- **No UCP/MCP integration.** `/agents.md` names `search_catalog`-style
  tools for buyer-approved checkout, not a bulk catalog dump — same
  reasoning every sibling Shopify crawler's spec gives.
- **No CD/cassette/download coverage.** This app's stock pipeline is
  vinyl-only by convention (`format: "Vinyl"` hardcoded across every
  sibling Shopify crawler); the per-variant filter below excludes them.
- **No merch coverage.** The store's apparel/accessory collections
  (`hoodie`, `shirt`, `hat`, `sweatpants`, `poster`, `playing-cards`) are
  separate Shopify collections, never surfaced by `music`; the one `Merch`
  `product_type` row that does appear in `music` is excluded by the
  `product_type` gate below.

## Technical grounding

All figures below were confirmed against the live site on 2026-08-19, by
fetching and fully paginating `/collections/music/products.json` (333
products across 2 populated pages, terminating on an empty 3rd page).

### Collection choice: `music`, not `lp`

The store also exposes format-specific collections (`lp`: 128 products,
`double-lp`: 12, `7`: 7, `cd`, `cassette`, `digital`, ...). Confirmed live
that `lp` is **not** a superset of vinyl stock — `double-lp` and `7`
between them include 19 handles absent from `lp` entirely (e.g.
`cak077-toro-y-moi-anything-in-return`, a 2xLP; `paw38-dent-may-fun-7`, a
7"). `music` (333 products) is confirmed to be a superset of `lp` ∪
`double-lp` ∪ `7` combined, so it's used as the single crawl source,
matching `no_idea_records.py`/`anxiousandangry.py`'s pattern of crawling one
broad collection and filtering at the product/variant level rather than
trusting a store's own format-specific curation.

### Product-level gate: `product_type` allowlist

Confirmed live: 332/333 products in `music` carry `product_type: "Music"`;
the one exception carries `"Merch"`. A single allowlist excludes it before
any parsing:

```python
_ALLOWED_PRODUCT_TYPES = {"music"}
```

### Title parsing: strip catalog-code prefix, split on the artist/title dash

Titles follow an optional catalog-code prefix (`CAK188`, `CAKD067`,
`CAK087X`, `CHZ19`, `WIXD05`, `PAW38` — 2-5 uppercase letters, 1-4 digits,
optional trailing letter; one live product, `WIX04/05 Johanna Warren -
Gemini I & II`, carries a dual catalog number with a `/`-separated second
number) followed by `Artist - Title`, with inconsistent spacing/dashing
around the code (`CAK188 Dent May - The Big One`, `CAK189 - casi - CASI`,
`CAKD97 Ducks Ltd. - When You're Outside`) and, on one product, a literal
tab character in place of a space before the dash (`CAK187 - Tanukichan x
Space Ghost\t- Circles - Space Ghost Remix`). Newer catalog-less releases
drop the prefix entirely (`Tanukichan - Make Believe`, `Phoebe Rings -
Astronaut (Beach House Cover)`).

```python
_CODE_RE = re.compile(r'^[A-Z]{2,5}\d{1,4}[A-Z]?(?:/\d{1,4})?\s*-?\s*')
_SPLIT_RE = re.compile(r'\s+-\s+')
```

Strip `_CODE_RE` (a no-op when the title has no catalog code — the class
only matches an all-caps run immediately followed by digits, which no
band name in this catalog's live title set does), then split the
remainder on `_SPLIT_RE`'s first match (whitespace, including the one tab
case, hyphen, whitespace) — chosen over a plain `" - "` split so the tab
variant isn't missed, and chosen over splitting on every `-` so album
titles with an internal unspaced hyphen (`Hidden Tracks and Rarities
2001-2005`) aren't broken apart. Matches 331/332 titles live (99.7%). The
1 miss (`CAK104 Carpark Sweet Sixteen Basketball Picture Disc LP`, a
various-artists/label-branded picture disc with no dash at all) falls back
to `vendor` (`"Carpark"`, the label's own name) as artist and the
code-stripped string as title — the same accepted-risk fallback
`no_idea_records.py`/`anxiousandangry.py` document for their own residual
misses.

### Per-variant filter: negative, extending `anxiousandangry.py`'s shape

Confirmed live across all 712 variants in `music`-collection `Music`-type
products (108 distinct variant titles): real vinyl variants are almost
always a `LP`/`Vinyl`/inch-mark-bearing string (`LP (Black vinyl)`,
`Limited Edition 12"`, `Standard Vinyl`), but a substantial minority carry
no format keyword at all — bare color/edition names (`Eco Mix Red`,
`Limited Edition Teal`, `Carpark Exclusive Mandarin Tree Translucent
Orange`), `EP` (used as this store's format label in place of `LP` on at
least one release), and `Limited Edition of 30 Signed Test Pressing!`. A
positive-match filter (No Idea's pattern) would drop all of these; a
negative filter matching only the non-vinyl formats is used instead:

```python
_NON_VINYL_RE = re.compile(r'^(cd|cs|cassette|tape|digital|christmas ornament|playing cards|dvd)$', re.IGNORECASE)
_NON_VINYL_SUFFIX_RE = re.compile(r'\btape\b|\bcassette\b|\bdigital\b|\bcds?\b', re.IGNORECASE)
```

Two regexes rather than one exact-match set: most non-vinyl variants are
bare format words (`CD`, `Digital`, `Tape`, `Cassette`, `CS` — a cassette
single, confirmed live only ever alongside a `Cassette`/`Tape` sibling
variant on the same product), but two products carry a compound variant
title with the format word as a suffix (`Gemini I CD`, `Gemini II CD`;
`Deluxe Ibanez DE-7 Pink Edition Tape`) that an exact-match regex misses
— `_NON_VINYL_SUFFIX_RE`'s `\b`-bounded word search catches those without
also matching a genuine vinyl variant (no live vinyl variant title
contains `tape`, `cassette`, `digital`, or `cd` as a substring).
`Christmas Ornament`, `Playing Cards`, and standalone `DVD` are excluded
as non-format bonus items bundled into a release's variant list, not audio
formats at all. A variant is dropped only when it matches either regex —
everything else, including every no-keyword color/edition name above, is
kept as vinyl. Confirmed live: this correctly excludes all 10 non-vinyl
variant titles present in the collection and keeps every one of the
no-keyword vinyl-color variants sampled above.

`_NON_VINYL_SUFFIX_RE`'s suffix match has no vinyl-signal check of its own,
so a bundle variant that pairs a vinyl format with a non-vinyl one (e.g. a
hypothetical `LP + Cassette`) would otherwise be wrongly dropped, the same
shape of collision `anxiousandangry.py` guards against at its product-level
gate. A third regex, `_VINYL_RE` (`vinyl`/`lp`/an inch mark), overrides the
suffix match when present, mirroring that guard at the variant level.

### Pre-order tag

`has_tag(product, "preorder")` (lowercase, unlike `anxiousandangry.py`'s
`"PREORDER"` — `has_tag()` is already case-insensitive, so no behavior
difference, just matching this store's actual tag casing for
readability). Confirmed live on 3 current products (`CAK188`, `CAK189`,
`CAK187`), each with at least one unavailable variant that should still be
surfaced. Same carve-out every sibling Shopify crawler uses: skip
unavailable variants unless the product is pre-order tagged, and append
` (Pre-Order)` to the title when it is.

### Fields

- **price** — `float(variant["price"])`.
- **currency** — `"USD"`.
- **url** — `f"{base_url}/products/{handle}"`.
- **cover_image_url** — `resolve_cover_image(product, variant)`.
- **format** — `"Vinyl"` unconditionally, matching every sibling Shopify
  crawler.
- **title** — `album_title` alone when the variant title is `Default
  Title`, otherwise `f"{album_title} — {variant_title}"` (the
  color/edition disambiguator), matching
  `anxiousandangry.py`/`bigscarymonstersusa.py`'s convention. For
  pre-order-tagged products, ` (Pre-Order)` is appended to the title.

### Crawler shape

```python
class Crawler:
    site_name: str = "Carpark Records"
    base_url: str = "https://store.carparkrecords.com"
    genre_summary: str = "Annandale/Baltimore indie label — Toro y Moi, Beach House, Dan Deacon, Speedy Ortiz, The Beths."
    genre: str = "rock"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]: ...
```

`_COLLECTION_SLUG = "music"`, iterated with `shopify_catalog.iter_products()`
unchanged. Registration is automatic via `main.py`'s startup loop — no
wiring changes.

## Queue fan-out

333 products → 332 pass the `product_type` gate → 155 products yield at
least one vinyl variant after the per-variant filter (the other 177 are
CD-only/digital-only releases with no vinyl option at all) → 196 vinyl
item rows total. Per this repo's per-item-crawler-fanout design,
`_sync_stock` enqueues one `crawl_queue` row per `item_key` — ~196 rows —
each expanded across eligible release crawlers at dispatch time (`amazon`,
`ebay`, `ebay_general`; `discogs_marketplace` excluded by its
`requires_discogs_release = True`), for ~590 dispatch work units per sync.
Comparable in scale to `no_idea_records.py`.

## Testing

`backend/tests/test_carparkrecords_crawler.py`, on
`test_anxiousandangry_crawler.py`'s pattern — `respx`-mocked
`products.json` responses and hand-written product literals taken from
confirmed-live products, no live site, no bot-detection risk. Cases:

- catalog-code-prefixed title (`CAK188 Dent May - The Big One`) → parsed
  into artist/title correctly
- catalog-code-prefixed title with a dash-then-space-dash separator
  (`CAK189 - casi - CASI`) → parsed correctly (code strip, then first
  `_SPLIT_RE` match)
- title with a tab character before the dash (`CAK187 - Tanukichan x
  Space Ghost\t- Circles - Space Ghost Remix`) → parsed correctly, tab
  treated as the whitespace side of the separator
- title with no catalog code at all (`Tanukichan - Make Believe`) →
  parsed correctly, no-op code strip
- title with no dash separator at all → falls back to `vendor` as artist,
  code-stripped string as title
- a bare `LP`/`Vinyl`/inch-mark-bearing variant → kept as vinyl
- a no-keyword color/edition variant (e.g. `Eco Mix Red`) → kept as vinyl
  (the permissive/negative-filter behavior this design is built around)
- a bare non-vinyl variant (`CD`, `Digital`, `Tape`, `Cassette`) → dropped
- a compound non-vinyl variant (`Gemini I CD`) → dropped via
  `_NON_VINYL_SUFFIX_RE`, while a sibling `LP` variant on the same product
  is kept
- `Christmas Ornament`/`Playing Cards`/`DVD` bonus-item variants → dropped
- `product_type: "Merch"` product → excluded entirely by the
  `product_type` allowlist
- `Default Title` variant → title has no suffix; a named variant → title
  suffixed with the variant name
- pre-order-tagged product with an unavailable variant → kept, title
  suffixed ` (Pre-Order)`
- non-pre-order product with an unavailable variant → skipped
- site metadata (`site_name`, `base_url`, `genre`, `crawler_type`)

## Crawl citizenship and `robots.txt` compliance

Per the normative section of `2026-08-09-amoeba-store-crawler-design.md`.
This site's finding, confirmed live 2026-08-19:

- `robots.txt`'s `User-agent: *` group is `Allow: /`, with disallows
  covering `/admin`, `/cart/`, `/checkout(s)/`, `/orders`, `/account`,
  `/services`, `/sf_*`, `/cart.js`, `/recommendations/products`, and
  `sort_by`/`filter`/`+`-encoded crawl traps — the same Shopify-default
  template every sibling Shopify crawler found. **None of these covers
  `/collections/music/products.json`**, the only path this crawler
  requests.
- `/agents.md` names `GET /collections/{handle}/products.json` explicitly
  under "Read-Only Browsing (No Authentication Required)".
- Both documents require checkout/payment to never complete without
  contemporaneous human approval. This crawler satisfies that trivially:
  it links out to the product page and never transacts. (Both documents
  also recommend installing a third-party "Shop skill" for
  agent-driven purchasing — irrelevant to this read-only catalog
  crawler, and not acted on.)
- Load: 3 GETs per sync — `iter_products()` only terminates on an empty
  page, not a short one, so 333 products at `limit=250` means two full
  pages (250 + 83), then a terminating empty page. Paced at
  `random.uniform(delay * 0.5, delay)` with `crawl_delay_seconds`
  defaulting to 30s. No detail-page fan-out. `iter_products()` fails fast
  on 429 and gives up after `consecutive_failure_limit` on anything else.
- If Carpark Records blocks this crawler, adds a `Disallow` covering this
  path, or asks us to stop, the response is to disable the plugin.

## Runtime/agent document impact

No `.agents/INPUTS.md`, `.agents/OUTPUTS.md`, or `.agents/INSTRUCTIONS.md`
exist in this repo. This change adds no new trigger and no new inbound
interface — one new outbound host (`store.carparkrecords.com`).

`backend/version.py`'s `VERSION` is derived from git and is not edited by
this change.
