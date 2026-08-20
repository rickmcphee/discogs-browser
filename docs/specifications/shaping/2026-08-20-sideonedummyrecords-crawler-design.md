# SideOneDummy Records store crawler design

Date: 2026-08-20
Branch: `claude/side-one-dummy-crawler-da0711`

## Problem

SideOneDummy Records' official store
(`sideonedummyrecords.shop.musictoday.com`) is not covered by any existing
crawler. It runs on the Musictoday commerce platform — a platform no
existing crawler in this repo targets (the ~40 label-store `catalog`/
`catalog_browser` plugins are Shopify, Bigcartel, WooCommerce, or bespoke
storefronts).

## Scope

Add `backend/crawlers/sideonedummyrecords.py` as a
`crawler_type="catalog_browser"` plugin (a Playwright `Page`, not `httpx`)
that loads the store's dedicated Vinyl category page and extracts every
in-stock product from the rendered DOM in one `page.evaluate()` call.

**Non-goals**

- **No coverage beyond the Vinyl category.** `ACCESSORIES`/`APPAREL`/`CDS`/
  `FROM THE VAULT`/`NEW RELEASES`/`ALL PRODUCTS` are out of scope — the
  Vinyl nav link already scopes to exactly the vinyl inventory, so no
  per-item format filtering is needed (`format` is hardcoded `"Vinyl"`).

## Technical grounding

All figures below were confirmed against the live site on 2026-08-20.

### Cloudflare-gated: browser required, not `httpx`

Every path on this domain, including `/robots.txt`, returns Cloudflare's
"Just a moment..." managed-challenge interstitial (`cf-mitigated:
challenge`, HTTP 403) to a plain `curl`/`httpx` request. A real browser
(this session's Playwright-driven preview) passes the challenge
automatically with no interaction required, matching the established
`catalog_browser` crawlers (`angryyoungandpoor.py`, `amoeba.py`) rather
than the `httpx`-based ones. Bot detection: `page.title()` contains "Just
a moment" while still on the interstitial, mirroring
`angryyoungandpoor.py`'s `"Cloudflare" in title` / `amoeba.py`'s
`"Attention Required" in title` checks — raises `BotDetectedError` so the
circuit breaker handles it rather than silently yielding nothing.

### One page, no pagination

`{base_url}/dept/vinyl` (the real destination behind the `VINYL` nav link;
the `?cp=...` query string it carries is store-internal navigation state,
confirmed unnecessary — the bare path renders the identical 93-product
listing) renders all 93 vinyl products server-side in a single response,
confirmed by scrolling to the bottom and re-counting `li.ProductElementsDisplay`
(no infinite scroll, no "load more", count unchanged). So `crawl_catalog`
is a single `page.goto()` + single `page.evaluate()`, unlike the
multi-page `catalog_browser` siblings.

### DOM shape and extraction

Each product is `li.ProductElementsDisplay > div.ProductContainer`, with:

- `.ProductName` — carries `data-productid` and `data-productname`
  attributes directly (no need to read visible text), and its child `<a>`
  carries the product `href`.
- `img.ProductImg` — `src` is a protocol-relative URL
  (`//static.musictoday.com/store/bands/{bandId}/...`); prefixed with
  `https:` for `cover_image_url`.
- `.PricingContainer` — carries `data-listprice` and `data-saleprice`
  (confirmed live: `data-saleprice` is always `""` today, no active sales
  to validate against, so `salePrice or listPrice` is defensive, not
  confirmed-exercised) as `"$25.99"`-style strings.
- **Out-of-stock products have no `.PricingContainer` at all** — an
  `.OutOfStockMsg` div takes its place instead (confirmed live: 17 of 93
  products, e.g. "Walter Etc. - When The Band Breaks Up Again Teal
  Vinyl"). Filtering the extracted list on a truthy `listPrice` therefore
  doubles as the in-stock gate — no separate stock flag to inspect.

```js
() => Array.from(document.querySelectorAll('li.ProductElementsDisplay')).map(li => {
  const nameEl = li.querySelector('.ProductName');
  const linkEl = nameEl ? nameEl.querySelector('a') : null;
  const imgEl = li.querySelector('img.ProductImg');
  const pricingEl = li.querySelector('.PricingContainer');
  return {
    id: nameEl ? nameEl.getAttribute('data-productid') : null,
    name: nameEl ? nameEl.getAttribute('data-productname') : null,
    href: linkEl ? linkEl.getAttribute('href') : null,
    image: imgEl ? imgEl.getAttribute('src') : null,
    listPrice: pricingEl ? pricingEl.getAttribute('data-listprice') : null,
    salePrice: pricingEl ? pricingEl.getAttribute('data-saleprice') : null,
  };
}).filter(p => p.id && p.name && p.href && p.listPrice)
```

Confirmed live via `page.evaluate()` against the real listing: 93 total
`li`s, 76 pass the filter (in stock), matching the 17 `.OutOfStockMsg`
products by inspection.

### Title parsing: two separator shapes, resolved by leftmost position

`data-productname` has no separate artist field (unlike Shopify's
`vendor` on `no_idea_records.py`/`anxiousandangry.py`) — the artist only
exists embedded in the title string, and this store mixes two shapes
depending on product, confirmed against the full live 93-title set:

- **90/93 (97%): `Artist - Title ...`** — a dash separator, e.g.
  `"Kerosene Heights - Blame It On The Weather Limited Edition Watermelon
  Splash LP"`. Titles routinely contain a *second* dash further in (e.g.
  `"Violent Soho - Hungry Ghost 10 Year Anniversary LP - Standard Version
  1"`), so the split must take the *first* dash, not any dash.
- **3/93: `Artist 'Title' ...`** — a single-quote separator instead, with
  no dash before it, e.g. `"Satsang 'All. Right. Now' 2xLP/CD - Orange
  Vinyl w Black Smoke"`. A first-dash-only split would wrongly cut these
  at their *later*, incidental dash (artist = `"Satsang 'All. Right. Now'
  2xLP/CD"`).
- **1/93: no separator at all** — `"Flogging Molly LP Bundle"`, a
  multi-artist bundle with no reliable single-artist source. Skipped,
  matching this repo's established "no artist source → skip" convention
  (`darkdescentrecords.py`, `asbestosrecords.py`).

Resolved with one regex whose two alternatives are tried at every
position left-to-right, so whichever separator shape actually occurs
first in a given title wins — no per-title branching needed:

```python
_SEPARATOR_RE = re.compile(r"\s*-\s+|\s+(?=['‘])")
```

The dash alternative consumes the surrounding `" - "` entirely (title
starts clean, no leading dash). The quote alternative is a zero-width
lookahead on whitespace only, so the opening quote mark stays part of the
title rather than being consumed — the two runtime cases are not the
same shape and shouldn't produce a title formatted as if they were.
Requiring whitespace *immediately before* the punctuation in both
alternatives is what keeps this safe against mid-word apostrophes/dashes
that are not real separators — confirmed against every apostrophe in the
live title set: `"Swingin' Utters"`, `"Can't"`, `"That's"`, `"You're"`,
and the `7'`/`7"` inch-mark suffixes all have the punctuation glued
directly onto the adjacent letter/digit with no preceding whitespace, so
none of them false-match.

### Fields

- **price** — `salePrice or listPrice`, `"$"`/`,` stripped, `float()`.
  `None` (skip) if neither parses.
- **currency** — `"USD"` unconditionally, confirmed on every sampled
  product.
- **url** — `base_url + href`, with the `?cp=...` tracking query string
  stripped (store-internal nav-path state, not part of the product's
  identity — the `/product/{id}/{slug}` path alone is already unique).
- **cover_image_url** — `https:` + the protocol-relative `src`.
- **format** — `"Vinyl"` unconditionally (see Non-goals).

### Crawler shape

```python
class Crawler:
    site_name: str = "SideOneDummy Records"
    base_url: str = "https://sideonedummyrecords.shop.musictoday.com"
    genre_summary: str = "Long-running punk and ska label's official store, including exclusive vinyl variants."
    genre: str = "punk"
    crawler_type: str = "catalog_browser"

    async def crawl_catalog(self, page) -> AsyncIterator[dict]: ...
```

Registration is automatic via `main.py`'s startup loop — no wiring
changes.

## Testing

No unit tests, matching this repo's established convention for
`catalog_browser` crawlers (`angryyoungandpoor.py`, `amoeba.py` also have
none) — see `CLAUDE.md`: "Playwright-dependent code (live crawl, browser
launch) is not unit-tested; integration testing is manual." The title
regex and full product-shape parsing were instead exercised directly
against the live site's real `data-productname` values (all 93) and
representative product objects (in-stock and out-of-stock) during
development, not committed as a fixture-based test file.

## Crawl citizenship and `robots.txt` compliance

Per the normative section of
`2026-08-09-amoeba-store-crawler-design.md`, which requires checking
`robots.txt` for the specific paths a new crawler will request, and
recording the finding here:

- **`robots.txt` exists and does not disallow `/dept/vinyl`.** Fetched via
  a real browser (a plain HTTP client gets the same Cloudflare challenge
  on this path as every other path). Disallows are scoped to
  `/cart/`, `/checkout/`, `/account/`, `/search/`, and a set of legacy
  `.aspx` account/checkout pages — none overlap `/dept/vinyl`. (A separate
  `User-agent: GPTBot` block disallows everything, but that UA doesn't
  apply here.)
- **No `/agents.md` exists** (`Not Found`, confirmed via browser) —
  checked for completeness, not because the Amoeba precedent requires it;
  no policy document of either kind constrains this crawler.
- This crawler never transacts on its own: it only links out to each
  product's own page, matching every sibling crawler in this repo.
- Load: one `page.goto()` plus one `page.evaluate()` per full sync — the
  lightest-weight crawler in this repo by request count, since the entire
  vinyl catalog renders in a single response. Paced with the same
  `random.uniform(delay * 0.5, delay)` pre-request sleep every sibling
  crawler uses, `crawl_delay_seconds` defaulting to 30s.
- If SideOneDummy Records blocks this crawler, adds a `robots.txt` rule
  covering `/dept/vinyl`, or asks us to stop, the response is to disable
  the plugin.

## Runtime/agent document impact

No `.agents/INPUTS.md`, `.agents/OUTPUTS.md`, or `.agents/INSTRUCTIONS.md`
exist in this repo. This change adds no new trigger and no new inbound
interface — one new outbound host
(`sideonedummyrecords.shop.musictoday.com`).

`backend/version.py`'s `VERSION` is derived from git and is not edited by
this change.
