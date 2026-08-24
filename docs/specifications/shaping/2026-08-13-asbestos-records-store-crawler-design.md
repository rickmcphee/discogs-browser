# Asbestos Records store crawler design

Date: 2026-08-13
Branch: `claude/realgonemusic-store-crawler-68d8ae`

**Amendment (2026-08-23, branch `claude/competent-saha-636e61`):**
`backend/crawlers/jetglowrecordings.py` is a second Big Cartel store, so
this doc is no longer describing the only one. Two clarifications, neither
retracting anything below:

1. The Scope section's "this is the first Bigcartel store, so a
   `bigcartel_catalog.py` helper would be premature abstraction" still
   holds, but no longer for the reason given. With two stores the count
   argument is spent; what keeps the abstraction premature now is that the
   two crawlers agree only on the *fetch* (one unpaginated `/products.json`
   GET) and diverge on every parsing decision after it — see
   [`2026-08-23-jetglow-recordings-crawler-design.md`](2026-08-23-jetglow-recordings-crawler-design.md).
   A shared helper would cover the one line that is already trivial. The
   nine-Shopify-crawler bar cited below is still the right one.
2. The observations here are store-specific, not Big Cartel platform
   behaviour, and two in particular do not generalise: the `Vinyl`
   *category* (Jetglow's single media category is `Vinyl - Cassette - CD`,
   which lumps all three formats and so cannot gate vinyl at all), and
   option-level `sold_out` (populated here — 19 true — but inert on
   Jetglow, where all 114 options report `false` and product-level
   `status` carries availability instead). The "entire catalog in one
   response, `page=`/`limit=` ignored" behaviour *did* reproduce on the
   second store.

**Second amendment (2026-08-24, branch `claude/ripple-music-crawler-ttceiq`):**
`backend/crawlers/ripplemusic.py` is a third Big Cartel store, and it spends
the reasoning the first amendment replaced the count argument with.

That amendment said the `bigcartel_catalog.py` helper stays premature because
the two crawlers "agree only on the *fetch* (one unpaginated `/products.json`
GET) and diverge on every parsing decision after it." The third crawler does
not share the fetch either: it pages `/products.json` and stops when a page
returns nothing new. Not because `page=` was found to be honoured there — it
could not be checked at all (see that crawler's design doc, "Verification
status") — but because that store is far larger than either of these two (its
storefront paginates to at least `/products?page=3` and `/category/cds?page=4`,
against 76 and 50 products total here and on Jetglow), so a single GET risks
silently truncating the catalog if the platform caps the response.

The conclusion is unchanged and now rests on something stronger than a count:
the three crawlers share no logic at all, only Big Cartel's response *schema*,
which is the platform's and not ours. A shared helper would have nothing left
to hold. The nine-Shopify-crawler bar cited below is still the right one.

The first amendment's list of observations that are store-specific rather than
platform behaviour gains a third data point, and one correction of emphasis:

- The `Vinyl` *category* — already noted as not generalising to Jetglow's
  lumped `Vinyl - Cassette - CD`. Ripple Music is a third shape again: its
  media categories are named by format *and size* (`12" Vinyl`, `10" Vinyl`,
  `7" Vinyl`, `Double LP`, `Test Presses`). Three stores, three category
  vocabularies — an exact category string is store-local by default, and
  `ripplemusic.py` matches category names with a token regex for that reason.
- Option-level `sold_out` — populated here (19 true), inert on Jetglow,
  **unverified** on Ripple Music, which therefore honours both it and
  product-level `status`, and treats an absent `status` as "no signal" rather
  than as sold out.
- The unpaginated-feed behaviour reproduced on the second store, and the first
  amendment recorded that as reproduction. Two stores of 76 and 50 products
  are weak evidence for a platform-wide rule about response caps; it should be
  read as "not yet contradicted," not as established. It remains unverified on
  the third store.

Amendment only — nothing below is retracted.

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
- **No use of `categories` as the sole inclusion signal.** 20 of 76
  products (26%) — including genuine vinyl releases like `Random Hand -
  Random Hand` and `John Hinckley - Redemption LP` — carry an empty
  `categories` array. Filtering on `categories` alone would under-cover the
  real catalog. This reasoning still holds and is unchanged by the
  "Format filtering" correction below — but note `Random Hand - Random
  Hand` is itself an example of the *other* half of the problem: it also
  carries no format token in its `name`, so the name-only gate this design
  originally shipped with dropped it too. Neither signal alone is
  sufficient, which is why the shipped gate (see "Format filtering") is a
  union of the format regex and the `categories` check, not categories
  alone and not the format regex alone. Even the union will still miss a
  title with neither a format token in its name nor a `Vinyl` category —
  e.g. a genuinely empty-categories vinyl release with an atypical name.
  That residual gap can't be sized exactly without checking each of the 76
  products' actual on-page listing by hand; it's a known, accepted
  limitation, not a solved problem.
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

Figures below were confirmed against the live site on 2026-08-13 via
`GET /products.json` (single request, 76 products, 141KB), except where
marked "Corrected 2026-08-14" — those were re-derived against a fresh fetch
of the same endpoint after the inclusion gate changed post-merge.

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

**Corrected 2026-08-13, post-merge, following a final whole-branch code
review.** The section below as originally written proposed
`angryyoungandpoor.py`'s `_FORMAT_RE`
(`\bvinyl\b|\b\d*x?lp\b|\bep\b|\d+\s*"`) against the product `name` as the
*sole* inclusion gate, "applied uniformly regardless of `categories`." That
shipped as written, but a re-check against live data found it under-covers
the catalog: 11 products (10 real releases plus the one subscription
bundle discussed below) carry a `Vinyl` category but no format token
anywhere in their `name` — e.g. `David McWane - The Gypsy Mile`, `River
City Extension - Deliverance`, `Roots & Bases : a Latin American Ska Scene
Compilation`. That's 19 available (non-sold-out) option rows the name-only
gate silently dropped.

The gate is now a union: `_FORMAT_RE.search(name)` OR `Vinyl` in the
product's `categories`. Neither signal alone is sufficient — `categories`
alone under-covers (26% of releases carry no categories at all, per
"Non-goals" above) and `name` alone under-covers (the 11-product/19-row gap
just described) — so `_items` ORs both.

| Measure | Count |
|---|---|
| Products total | 76 |
| Match `_FORMAT_RE` on `name` (old gate) | 42 (43 available rows) |
| Carry `Vinyl` category | 45 |
| Union of the two (shipped gate) | 53 (62 available rows) |
| Dropped by the old name-only gate despite a `Vinyl` category | 11 products / 19 available rows |

One false positive is accepted, unchanged by this fix: `2025 Ska Vinyl
Supscription - 10 LPs` (a subscription bundle, not a release) matches
`_FORMAT_RE` on the word `Vinyl` in its own name — it would be included by
the format-regex arm regardless of its (empty) `categories`. This is the
same class of noise `cleorecs.py` and `angryyoungandpoor.py` already accept
rather than special-case a single SKU — its hyphen-split artist (`2025 Ska
Vinyl Supscription`) will never match a real Discogs artist, so it's inert
noise in the Store tab, not a false match.

The design's original claim that `Various Artists - No Worries: east coast
love for a west coast friend` was a real release known-lost by the format
gate was itself wrong: live data shows this product is categorized `["CDs",
"Asbestos Records"]`, not `Vinyl` — it's correctly excluded as a CD, not a
lost vinyl release. There is no longer a known specific real-release loss
called out here; see "Non-goals" for the residual gap the union gate still
has.

### Artist/title split

Reusing the whitespace-anchored split already standard across the Shopify
crawlers — `^(?P<artist>.+?)(?:\s+-\s*|\s*-\s+)(?P<album>.+)$` — so a hyphen
inside a name with no surrounding space (e.g. `Suicide Machines-On the Eve
of Destruction`) isn't mistaken for the separator.

**Corrected 2026-08-14** alongside the "Format filtering" fix above — the
cohort here is the 53-product union, not the original 42.

Of the 53 kept products, 49 split cleanly on this regex. The remaining 4 have
no whitespace-anchored hyphen at all:

- `The Least Worst of the Suicide Machines 2xLP` — no hyphen.
- `The Suicide Machines-On the Eve of Destruction 2xLP` — hyphen present but
  glued to `Machines`, correctly rejected by the whitespace anchor.
- `Roots & Bases : a Latin American Ska Scene Compilation` — no hyphen, and
  no curated `artists` entry either.
- `2024 Asbestos Records Subscription Club!` — no hyphen, and no curated
  `artists` entry either; this is the accepted-noise bundle brought in by
  the `Vinyl`-category arm of the union gate, discussed in "Format
  filtering."

The first two carry a populated `artists` array (`[{"name": "Suicide
Machines", ...}]`) — Bigcartel's own curated artist tag, present on 38/76
products store-wide — and fall back to it correctly. Unlike `vendor` on the
Shopify stores, this field is store-curated per product rather than a single
label name repeated everywhere, so it's a legitimate fallback — but only a
fallback, not the primary source: some tagged artists don't literally match
the billing in the title (e.g. `David McWane - The Gypsy Mile` is tagged
under `big d & the kids table`, the member's main band, not the solo
billing), so trusting it over an actual title split would silently overwrite
a correct, literal artist name with an editorial one. Priority order:

1. Whitespace-anchored hyphen split on `name` → use both halves.
2. No split found → `artists[0]["name"]` if present, full `name` as title.
3. Neither → skip (`None`, following `angryyoungandpoor.py`'s no-split
   precedent). Exercised live by the last two products above, not just a
   guard against a case that hadn't occurred.

One live product's split-out artist is `"various artists"`: `Various Artists
- Black Sand Relief benefit Compilation`, brought in by the `Vinyl`-category
arm of the union gate (its name carries no format token, so the pre-fix
name-only gate never saw it). The `angryyoungandpoor.py`/`cleorecs.py`
convention of normalizing to the literal Discogs entity string `"Various"`
is now exercised by live data, not just included for correctness against a
case that hadn't occurred — confirmed `_VARIOUS_RE` normalizes it correctly.

`html.unescape()` is required on `name` before splitting/display —
confirmed live: `River City Extension - Don&#x27;t Let the Sun Go Down on
Your Anger 2xLP` carries a literal HTML entity in the JSON field.

### Variants (`options`)

**Corrected 2026-08-14**, same reason as above: 81 options across the 53
kept products; 19 `sold_out: true`, 62 available; 7 of the 53 products have
zero available options (yield nothing for those, same as any sibling
crawler with an all-unavailable product).

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
