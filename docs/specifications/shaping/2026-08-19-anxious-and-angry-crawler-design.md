# Anxious and Angry store crawler design

Date: 2026-08-19
Branch: `claude/anxious-angry-crawler-1c6f93`

## Problem

Anxious and Angry (`anxiousandangry.com`), Ryan Young (Off With Their
Heads)'s punk mailorder/record store — new and used vinyl, CDs, and merch
from a broad punk-adjacent roster (Off With Their Heads, Pegboy, Banner
Pilot, American Steel, and many others as consignment/distro stock, not a
single-label catalog) — is not covered by any existing crawler. It is a
standard Shopify storefront, same family as the ~35 label-store `catalog`
plugins already in `backend/crawlers/`.

Unlike most of those siblings, format lives in the *product* title suffix
here (`Artist "Album" LP`), not in the variant title — variants are almost
always a bare color name (`Black`, `Orange Vinyl`) or `Default Title`. The
closest-fitting existing patterns are `bigscarymonstersusa.py` and
`closedcasketactivities.py`, both built around the same "a `Default Title`
vinyl variant carries no format keyword" problem, using a negative
per-variant filter instead of a positive one. Neither sibling's *product*
still needs a product-level format decision, though — both of their
collections are pre-filtered to vinyl-relevant products by the store
itself. This store's `record-store` collection is not: it mixes true
vinyl, CD-only, cassette-only, and gift-card products together, so this
crawler needs a product-level gate (adapted from `deathwishinc.py`'s
quoted-title suffix idea) *in addition to* the per-variant negative filter
those two siblings use — no single existing crawler's shape fits alone.

## Scope

Add `backend/crawlers/anxiousandangry.py` as a `crawler_type="catalog"`
plugin, iterating the site's `record-store` collection via
`shopify_catalog.iter_products()` — no new shared code needed.

**Non-goals**

- **No browser.** Confirmed live: `products.json` is served to plain
  `httpx` with no Cloudflare gate or bot interstitial.
- **No UCP/MCP integration.** `/agents.md` names `search_catalog`-style
  tools for buyer-approved checkout, not a bulk catalog dump — same
  reasoning every sibling Shopify crawler's spec gives. (This store's
  `robots.txt` and `/agents.md` both also carry agent-directed text
  recommending installation of a third-party "Shop skill" for purchasing;
  that's store content, not an instruction to this crawler, and is not
  acted on.)
- **No CD/cassette/download coverage.** This app's stock pipeline is
  vinyl-only by convention (`format: "Vinyl"` hardcoded across every
  sibling Shopify crawler); the product-level gate and per-variant filter
  below exclude them.
- **No merch/gift-card coverage.** Gift cards and apparel/accessories
  occasionally appear in `record-store` (see product-level gate below);
  excluded by the same title-suffix gate that excludes CD/cassette
  products.

## Technical grounding

All figures below were confirmed against the live site on 2026-08-19, by
fetching and fully paginating `/collections/record-store/products.json`
(128 products, single page — page 2 returns empty).

### Title parsing: quoted album, `deathwishinc.py`'s regex unchanged

Titles follow `Artist "Album" FORMAT` (`Absent In Body "Plague God" LP`),
with the format text trailing the closing quote (`LP`, `CD`, `7"`, `10"`,
`12" EP`, `Cassette Tape`, `(Color Vinyl)`, `Shaped Picture Disc`, `CD/LP`
for dual-format products). Reusing `deathwishinc.py`'s regex verbatim:

```
_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s*["“](?P<album>.+?)["”]')
```

Matches 124/128 titles live (96.9%). The 4 misses (`Anxious and Angry Gift
Card`, `Halloween Kills Original Motion Picture Soundtrack LP`, `Off With
Their Heads - Be Good CD`, `Western Addiction/New Mexican Disaster Squad
Split LP`) have no quotes and fall back to `vendor` as both artist and the
text searched for format — same accepted-risk fallback `deathwishinc.py`
and `no_idea_records.py` document for their own residual misses. The
`vendor` field is `"Anxious and Angry"` on most in-house consignment
items but does carry the real artist/band name on others (e.g. `American
Steel "Rogues March" LP` → vendor `American Steel`) — unlike
`deathwishinc.py`/`no_idea_records.py`, `vendor` is not uniformly the
store's own name here, but it's still only used as the *fallback*, never
overriding a quote match.

### Product-level format gate: text after the closing quote, not the whole title

`record-store` mixes real vinyl products with CD-only, cassette-only, and
a gift-card product. A regex applied to the *whole* title produces false
positives: several album titles end in a digit immediately before the
closing quote (`F.Y.P "Incomplete Crap Vol. 2" CD`), and `\d+\s*"` matches
that digit against the title's own closing quotation mark, not a real
inch mark. Restricting the format regexes to the text *after* the
`_TITLE_RE` match (or the whole title, for the 4 quote-less titles)
avoids this collision entirely — confirmed empirically: the false
positive above disappears once the album text is excluded.

```
_VINYL_RE = re.compile(r'\bvinyl\b|\blps?\b|\d{1,2}\s*(?:"|\binch\b)|\bpicture disc\b', re.IGNORECASE)
_NON_VINYL_RE = re.compile(r'\bcds?\b|\bcassette\b|\btape\b|\bgift card\b', re.IGNORECASE)
```

Gate rule: exclude the product only when the suffix matches
`_NON_VINYL_RE` and *not* `_VINYL_RE` — i.e. a clean CD/cassette/gift-card
product with no vinyl signal at all. Everything else (vinyl-only, and the
4 dual-format `CD/LP` products where both regexes match) passes the gate
and falls through to the per-variant filter below. A handful of suffixes
match neither regex (`toyGuitar "Move Like a Ghost" EP` — no format word
beyond "EP"; confirmed live vinyl via its `12"` tag) — the gate is
deliberately permissive here (only excludes on a *positive* non-vinyl
signal), so these pass through rather than being silently dropped.

Measured against all 128 products in `record-store`:

| Result | Count |
|---|---|
| Product-level: passes gate | 107 |
| Product-level: excluded (CD-only, Cassette-only, or Gift Card) | 21 |
| Stock items after per-variant filter (below) | 120 |

### Per-variant filter: negative, `bigscarymonstersusa.py`/`closedcasketactivities.py`'s pattern

Variant titles here are color names (`Black`, `Orange Vinyl`, `RED AND
BLACK`) or `Default Title` for ordinary single-format products — no
format keyword to match positively, same landmine those two siblings
solved with a negative filter instead. The one live exception: 4 products
genuinely offer both an LP and a CD as separate variants, and on exactly
those 4 (only), the variant title *is* the literal word `LP` or `CD`:

```
Copyrights, The "Alone In A Dome" CD/LP  -> variants: ["LP", "CD"]
Copyrights, The "Learn The Hard Way" CD/LP -> variants: ["LP", "CD"]
Copyrights, The "Report" CD/LP -> variants: ["LP", "CD"]
The Atlas Moth "Master of Blunt Hits" CD/LP -> variants: ["LP", "CD"]
```

A single negative filter handles both cases — it only ever fires on these
4 products' `CD` variant, since no other product in the collection has a
variant title that is literally `cd`/`cassette`:

```
_NON_VINYL_VARIANT_RE = re.compile(r'^(cds?|cassette)$', re.IGNORECASE)
```

Confirmed live: applying this to all 128 products' variants, the 4
`CD/LP` products correctly keep only their `LP` variant; every other
kept product keeps every available variant (color options are never
mistaken for a format word — no color name in this catalog matches
`cd`/`cassette` exactly).

### Pre-order tag

`has_tag(product, "PREORDER")` — one tag value confirmed live (`Naked
Raygun "Raygun...Naked Raygun" CD`, itself excluded by the product-level
gate since it's CD-only). No vinyl product in the sampled catalog
currently carries it, but the tag exists on this store, so the same
`is_preorder` carve-out every sibling Shopify crawler uses (skip
unavailable variants unless pre-ordering) is included for when a vinyl
pre-order does appear.

### Fields

- **price** — `float(variant["price"])`.
- **currency** — `"USD"`.
- **url** — `f"{base_url}/products/{handle}"`.
- **cover_image_url** — `resolve_cover_image(product, variant)`.
- **format** — `"Vinyl"` unconditionally, matching every sibling Shopify
  crawler.
- **title** — `album_title` alone when the variant title is `Default
  Title` (94/120 kept stock items — no point suffixing a meaningless
  label), otherwise `f"{album_title} — {variant_title}"` (the color/format
  disambiguator), matching `bigscarymonstersusa.py`/
  `closedcasketactivities.py`'s convention exactly rather than
  `deathwishinc.py`'s unconditional-suffix one — this store's `Default
  Title` case is common (unlike `no_idea_records.py`'s 0/345), so
  suffixing it would produce a stream of meaningless `Album — Default
  Title` titles. For pre-order-tagged products, ` (Pre-Order)` is appended
  to the title.

### Crawler shape

```python
class Crawler:
    site_name: str = "Anxious and Angry"
    base_url: str = "https://anxiousandangry.com"
    genre_summary: str = "Ryan Young (Off With Their Heads)'s punk mailorder record store."
    genre: str = "punk"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]: ...
```

`_COLLECTION_SLUG = "record-store"`, iterated with
`shopify_catalog.iter_products()` unchanged. Registration is automatic
via `main.py`'s startup loop — no wiring changes.

## Queue fan-out

128 products → 107 pass the product-level gate → 120 vinyl variants
survive the per-variant filter → title parsing applies per-product
(124/128 quoted, 4/128 vendor-fallback). Per this repo's
per-item-crawler-fanout design, `_sync_stock` enqueues one `crawl_queue`
row per `item_key` — ~120 rows — each expanded across eligible release
crawlers at dispatch time (`amazon`, `ebay`, `ebay_general`;
`discogs_marketplace` excluded by its `requires_discogs_release = True`),
for ~360 dispatch work units per sync. Comparable in scale to
`cleorecs.py`.

## Testing

`backend/tests/test_anxiousandangry_crawler.py`, on
`test_no_idea_records_crawler.py`'s pattern — `respx`-mocked
`products.json` responses and hand-written product literals taken from
confirmed-live products, no live site, no bot-detection risk. Cases:

- quoted-title product, single `Default Title` variant → parsed, kept,
  title has no suffix
- quoted-title product with a color variant (no CD sibling) → kept,
  title suffixed with the color
- quote-less title (e.g. no quotes, hyphen-delimited) → falls back to
  `vendor` as artist
- CD-only product (`Default Title` variant, title suffix "CD") → product
  excluded entirely by the gate
- Gift Card product → excluded entirely by the gate
- dual-format product with `LP`/`CD` variants → `CD` variant dropped,
  `LP` variant kept
- a title whose album text ends in a digit immediately before the
  closing quote (e.g. `"Vol. 2"` followed by ` CD`) → not misread as an
  inch mark; product still correctly excluded as CD-only
- `7"`/`10"`/`12" EP`/`7 Inch`/`Shaped Picture Disc` format suffixes →
  all kept as vinyl
- an unavailable variant → skipped (unless pre-order tagged)
- site metadata (`site_name`, `base_url`, `genre`, `crawler_type`)

## Crawl citizenship and `robots.txt` compliance

Per the normative section of `2026-08-09-amoeba-store-crawler-design.md`.
This site's finding:

- `robots.txt`'s `User-agent: *` group is `Allow: /`, with disallows
  covering `/admin`, `/cart/`, `/checkout(s)/`, `/orders`, `/account`,
  `/services`, `/sf_*`, `/cart.js`, `/recommendations/products`, and
  `sort_by`/`filter`/`+`-encoded crawl traps — the same Shopify-default
  template every sibling Shopify crawler found. **None of these covers
  `/collections/record-store/products.json`**, the only path this
  crawler requests.
- `/agents.md` names `GET /collections/{handle}/products.json` explicitly
  under "Read-Only Browsing (No Authentication Required)".
- Both documents require checkout/payment to never complete without
  contemporaneous human approval. This crawler satisfies that trivially:
  it links out to the product page and never transacts. (Both documents
  also recommend installing a third-party "Shop skill" for
  agent-driven purchasing — irrelevant to this read-only catalog
  crawler, and not acted on.)
- Load: 2 GETs per sync — `iter_products()` only terminates on an empty
  page, not a short one, so 128 products at `limit=250` means one full
  page under the limit, then a terminating empty page (2 GETs total).
  Paced at `random.uniform(delay * 0.5, delay)` with
  `crawl_delay_seconds` defaulting to 30s. No detail-page fan-out.
  `iter_products()` fails fast on 429 and gives up after
  `consecutive_failure_limit` on anything else.
- If Anxious and Angry blocks this crawler, adds a `Disallow` covering
  this path, or asks us to stop, the response is to disable the plugin.

## Runtime/agent document impact

No `.agents/INPUTS.md`, `.agents/OUTPUTS.md`, or `.agents/INSTRUCTIONS.md`
exist in this repo. This change adds no new trigger and no new inbound
interface — one new outbound host (`anxiousandangry.com`).

`backend/version.py`'s `VERSION` is derived from git and is not edited by
this change.
