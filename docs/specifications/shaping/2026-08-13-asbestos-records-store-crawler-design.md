# Asbestos Records store crawler design

Date: 2026-08-13
Branch: `claude/realgonemusic-store-crawler-68d8ae`

## Problem

Asbestos Records (`asbestosrecords.bigcartel.com`) is a ska/punk/hardcore
label and record store not covered by any existing crawler. It's the first
Bigcartel-hosted store in this repo — every existing `catalog` crawler
targets either a Shopify storefront (via `shopify_catalog.py`) or a bespoke
HTML/JS storefront (`sgrecordshop.py`, `angryyoungandpoor.py`). Bigcartel's
public JSON feed is closer to Shopify's `products.json` in spirit but
differs in two ways that matter: it returns the **entire catalog in one
response** (no pagination), and it carries a store-curated `artists: [...]`
field on about half of products.

## Scope

Add `backend/crawlers/asbestosrecords.py` as a `crawler_type="catalog"`
plugin. No shared module — this is the first Bigcartel store, so a
`bigcartel_catalog.py` helper would be premature abstraction; `shopify_catalog.py`
itself wasn't extracted until nine Shopify crawlers had converged on identical
logic (see `docs/specifications/shaping/2026-08-07-shared-title-split-helper-design.md`).

**Non-goals**

- **No pagination.** Confirmed live: `/products.json?page=2`,
  `?page=3`, and `?limit=5` all return the same, full 76-product array —
  Bigcartel silently ignores both params on this store. One GET per sync.
- **No use of the `categories` field for inclusion filtering.** 20 of 76
  products (26%) — including genuine vinyl releases like `Random Hand -
  Random Hand` and `John Hinckley - Redemption LP` — carry an empty
  `categories` array. Filtering on it would under-cover the real catalog; see
  "Format filtering" below for what's used instead.
- **No CD/tape coverage.** This app's stock-item pipeline is vinyl-only by
  convention (`format: "Vinyl"` is hardcoded across every sibling crawler);
  this store's standalone CD products (`Treephort - ... CD`, etc.) are
  correctly excluded by the format filter without special-casing them.
- **No preorder detection.** No structural signal for it here (unlike
  `seasonofmist.py`/`flatspotrecords.py`'s tag/`body_html` sniffing) —
  Bigcartel preorders here just show up as ordinary in-stock options
  (`**PREORDER**` appears as free text in one title but nowhere structured).
  Not acted on.

## Technical grounding

All figures confirmed against the live site on 2026-08-13 via
`GET /products.json` (single request, 76 products, 141KB).

### No pagination

```
page=1 -> 76 products
page=2 -> 76 products
page=3 -> 76 products
?limit=5 -> 76 products
```

Params are accepted (no error) but ignored. `shopify_catalog.iter_products()`
does not apply here; this crawler makes exactly one HTTP request per sync.

### Format filtering

Reusing `angryyoungandpoor.py`'s `_FORMAT_RE`
(`\bvinyl\b|\b\d*x?lp\b|\bep\b|\d+\s*"`) against the product `name`, applied
uniformly regardless of `categories`:

| Measure | Count |
|---|---|
| Products total | 76 |
| Match `_FORMAT_RE` on `name` | 42 |
| ...of which carry `categories: []` | 10 (would be lost by a category-only filter) |
| ...of which carry `Vinyl` category | 29 |

One false positive is accepted: `2025 Ska Vinyl Supscription - 10 LPs` (a
subscription bundle, not a release) matches `_FORMAT_RE` on the word `Vinyl`
in its own name. This is the same class of noise `cleorecs.py` and
`angryyoungandpoor.py` already accept rather than special-case a single SKU
— its hyphen-split artist (`2025 Ska Vinyl Supscription`) will never match a
real Discogs artist, so it's inert noise in the Store tab, not a false match.

One real release is known-lost by the format gate: `Various Artists - No
Worries: east coast love for a west coast friend` has no format token in its
title at all. Accepted rather than widening the regex for one item.

### Artist/title split

Reusing the whitespace-anchored split already standard across the Shopify
crawlers — `^(?P<artist>.+?)(?:\s+-\s*|\s*-\s+)(?P<album>.+)$` — so a hyphen
inside a name with no surrounding space (e.g. `Suicide Machines-On the Eve
of Destruction`) isn't mistaken for the separator.

Of the 42 kept products, 40 split cleanly on this regex. The remaining 2 have
no whitespace-anchored hyphen at all:

- `The Least Worst of the Suicide Machines 2xLP` — no hyphen.
- `The Suicide Machines-On the Eve of Destruction 2xLP` — hyphen present but
  glued to `Machines`, correctly rejected by the whitespace anchor.

Both carry a populated `artists` array (`[{"name": "Suicide Machines", ...}]`)
— Bigcartel's own curated artist tag, present on 38/76 products store-wide.
Unlike `vendor` on the Shopify stores, this field is store-curated per
product rather than a single label name repeated everywhere, so it's a
legitimate fallback — but only a fallback, not the primary source: some
tagged artists don't literally match the billing in the title (e.g. `David
McWane - The Gypsy Mile` is tagged under `big d & the kids table`, the
member's main band, not the solo billing), so trusting it over an actual
title split would silently overwrite a correct, literal artist name with an
editorial one. Priority order:

1. Whitespace-anchored hyphen split on `name` → use both halves.
2. No split found → `artists[0]["name"]` if present, full `name` as title.
3. Neither → skip (`None`, following `angryyoungandpoor.py`'s no-split
   precedent). Not exercised in the current 42, but kept as a guard.

No live product's split-out artist is `"various"` / `"various artists"`
(the one V/A compilation on the store is excluded by the format gate above),
so the `angryyoungandpoor.py`/`cleorecs.py` convention of normalizing to the
literal Discogs entity string `"Various"` is included for correctness but
untested against live data — it's a one-line, already-proven guard, not new
logic.

`html.unescape()` is required on `name` before splitting/display —
confirmed live: `River City Extension - Don&#x27;t Let the Sun Go Down on
Your Anger 2xLP` carries a literal HTML entity in the JSON field.

### Variants (`options`)

60 options across the 42 kept products; 17 `sold_out: true`, 43 available; 7
of the 42 products have zero available options (yield nothing for those,
same as any sibling crawler with an all-unavailable product).

Bigcartel has no Shopify-style `"Default Title"` placeholder — a
single-option product just repeats the product's own `name` as the option's
`name` (e.g. `No Fun At All - Master Celebrations 2xLP (import)
**PREORDER**` has one option, itself named identically). So the
default-variant guard compares against the product name directly instead of
a fixed placeholder string:

```python
display_title = album if option["name"].strip() == product["name"].strip() \
    else f"{album} — {option['name']}"
```

matching the shape (not the literal string) of `bigscarymonstersusa.py` /
`cleorecs.py`'s `"Default Title"` guard.

### Fields

- **price** — `option["price"]`, already a JSON float (unlike Shopify's
  stringified price) — no `float()` parsing needed, just a type check.
- **currency** — `"USD"`.
- **url** — `f"{base_url}{product['url']}"` (`product['url']` is already an
  absolute path, e.g. `/product/black-guy-fawkes-birthday-bash`).
- **cover_image_url** — `images[0]["url"]` if present, else `None`. No
  per-variant image field exists in this API (unlike Shopify's
  `featured_image`), so there's no `resolve_cover_image()` analogue here.
- **format** — `"Vinyl"` unconditionally, including 7"/10"/12" singles —
  same consumer requirement `cleorecs.py` documents (`ebay_api.FORMAT_KEYWORDS`
  keys on `Vinyl`/`CD`/etc., not raw inch sizes).

Registration is automatic via `main.py`'s startup loop — no wiring changes.

### Crawler shape

```python
class Crawler:
    site_name: str = "Asbestos Records"
    base_url: str = "https://asbestosrecords.bigcartel.com"
    genre_summary: str = "Ska, punk, and hardcore label and record store."
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]: ...
```

A single `httpx.AsyncClient().get(f"{base_url}/products.json")` (no
per-request headers needed — confirmed live with a plain `python-httpx`
user agent), `r.raise_for_status()`, then filter/parse/yield from the parsed
JSON array in-process. `report_page(1, len(items))` once, since there is
exactly one page.

## Crawl citizenship and `robots.txt` compliance

Per the normative section of
`docs/specifications/shaping/2026-08-09-amoeba-store-crawler-design.md`.
This site's finding:

- `robots.txt`:
  ```
  User-Agent: *
  Disallow: /admin
  Disallow: /cart
  Disallow: /checkout
  Disallow: /receipt
  ```
  `/products.json`, the only path this crawler requests, is not covered by
  any `Disallow`.
- No `/agents.md` exists (404) — no explicit agent guidance one way or the
  other; the `robots.txt` allowance stands on its own.
- Load: **one GET per sync**, the lightest of any crawler in this repo — no
  pagination, no per-product detail-page fan-out. Still paced with
  `random.uniform(delay * 0.5, delay)` before the request, matching every
  sibling crawler's convention, even though there's only one request to
  pace.
- If Asbestos Records blocks this crawler, adds a `Disallow` covering
  `/products.json`, or asks us to stop, the response is to disable the
  plugin.

## Testing

`backend/tests/test_asbestosrecords_crawler.py`, on
`test_cleorecs_crawler.py`'s pattern — `respx`-mocked `/products.json`
response built from hand-written product literals taken from confirmed-live
data, no live site, no bot-detection risk.

Cases:

- plain `Artist - Album LP` → correct split
- hyphen glued to artist with no surrounding space
  (`Suicide Machines-On the Eve of Destruction 2xLP`) → not clipped, falls
  through to the `artists[]` fallback
- no hyphen at all, `artists[]` present → `artists[0]["name"]` used
- no hyphen, no `artists[]` → skipped
- HTML entity in title (`Don&#x27;t`) → unescaped in both artist and title
- single-option product whose option `name` equals the product `name` → no
  ` — ` suffix
- multi-option product → one row per available option, suffix applied
- `sold_out: true` option → skipped
- product with zero available options → yields nothing
- title with no format token (`Roots & Basses vol 2 : a latin ska
  compilation`) → dropped
- title with format token but no real content (`2025 Ska Vinyl Supscription
  - 10 LPs`) → kept (documented, accepted noise), not specially filtered
- standalone CD product (`Treephort - ... CD`, no LP/EP/vinyl/inch token) →
  dropped
