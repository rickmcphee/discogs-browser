# Byrdland Records store crawler design

Date: 2026-09-02
Branch: `claude/byrdland-records-crawler-phb7s6`

## Problem

Byrdland Records — Washington, D.C.'s record store and label, sibling to the
Songbyrd Music House venue — is not covered by any existing crawler.

The request named `https://byrdlandrecords.com/`, but that host is a GoDaddy
Website Builder brochure site (`<meta name="generator" content="Starfield
Technologies; Go Daddy Website Builder 8.0.0000">`) with no products on it.
Its "Shop" nav points at `shop.byrdlandrecords.com`, a **Lightspeed eCom**
storefront (`(c) 2008-2026 Lightspeed Netherlands B.V.` in every page's head
comment). The crawler targets the shop host; the brochure host has nothing to
crawl.

Lightspeed is a platform this repo has not crawled before — every existing
`catalog` plugin reads some other platform's contract — Shopify
(`products.json`), WooCommerce (`/wp-json/wc/store/v1/products`), Big Cartel
(`ripplemusic.py`), a store's own private endpoint (`sgrecordshop.py`'s
`/gsrp/`), or bespoke HTML. No existing helper implements Lightspeed's, so
this crawler cannot reuse `shopify_catalog.iter_products()` and carries its
own paging loop.

## Scope

Add `backend/crawlers/byrdlandrecords.py` as a `crawler_type="catalog"`
plugin, walking the storefront's `Vinyl` category over Lightspeed's public
`?format=json` view with plain `httpx`.

**Non-goals**

- **No browser.** Confirmed live: every JSON page is served to plain `curl`
  with no bot interstitial, no Cloudflare gate, and no cookie requirement.
- **No genre-tree walk.** `/vinyl/` is a strict superset of its subcategories
  (proof below), so the tree beneath it adds requests and no rows.
- **No CD/cassette coverage.** The store's CDs and cassettes are out of
  scope; this app's stock pipeline is vinyl-only by convention. Sibling
  catalog crawlers hardcode `format: "Vinyl"` except where a store publishes
  a real per-item format — `amoeba.py` derives it from the title's trailing
  format token and `sgrecordshop.py` uses the listing's own when present —
  and even those publish only records.
- **No merch, turntables, or gift cards.** Those are separate top-level
  categories, outside the Vinyl category this crawler walks.
- **No `brand`/vendor artist source.** There isn't one — see below.

## Technical grounding

All figures below were confirmed against the live store on 2026-09-02 by
fully paginating `/vinyl/` (3,312 products across 34 pages of 100) and
caching every page for replay.

### The JSON view: `?format=json`

Appending `?format=json` to any Lightspeed storefront URL returns that page's
template data as JSON (`content-type: application/json`) instead of HTML.
`/vinyl/?format=json` yields a `collection` block — `count`, `page`, `pages`,
`limit`, and a `products` **object keyed by product id** — plus a `shop`
block carrying `id` and `currency`.

There is no `products.json`-style endpoint here: `/products.json` and
`/collections.json` both return the store's ordinary 404 HTML page, so the
Shopify shape this repo is used to does not exist on this platform.

### Pagination: path-based, and two silent traps

Two behaviours make the obvious paging loop wrong in ways that produce no
error at all. Both are pinned by tests.

**1. The `?page=` querystring is ignored.** `/vinyl/?format=json&limit=100&page=3`
answers HTTP 200 with `"page": 1`, `"items_from": 1`, and page 1's rows.
Pagination is by *path*: `/vinyl/page3.html?format=json&limit=100` answers
`"page": 3`, `"items_from": 201`. A querystring pager would re-ingest page 1
for the whole walk and look perfectly healthy doing it.

**2. Paging past the end wraps to page 1.** `/vinyl/page35.html` on a
34-page category does not 404 and does not answer empty — it returns HTTP 200
with `"page": 1` and a full 100 rows. So the usual `while True: … if not
products: break` shape never terminates here.

The loop is therefore bounded by the `pages` count the responses report —
re-read from each one rather than sampled on the first, for the reason given
two paragraphs below — and every response is checked against the page that
was requested (`_assert_page_echo`). The echo check is what makes trap 1 loud
rather than invisible: any regression to a pager the store ignores raises on
page 2.

Products are also de-duplicated by product id across pages. The listing is
sorted newest-first, so a product added mid-walk shifts every later page down
by one and re-serves a row already yielded.

Because that walk is mutable, the `pages` bound is re-read from every
response rather than sampled once: an insertion can push `count` over a page
boundary and grow `pages` after page 1 has answered. The **latest** value
wins rather than the largest, which is the opposite of what an ordinary pager
would do and is forced by the wrap above — carrying a stale-high bound
through a mid-walk *shrink* would request a page past the end, wrap to page
1, and fail an otherwise healthy crawl on the page-echo check.

### Page size: `limit=100`

100 is the largest size the storefront honours. Larger values are not clamped
to 100 — they are **ignored**, falling back to the default 12 (confirmed
live: `limit=250` and `limit=500` both answer `"limit": 12`), which would
quietly turn one paced pass into eight.

### Category choice: `/vinyl/`, not the genre tree

The store's sitemap exposes a genre tree beneath Vinyl — `vinyl/rock/indie-rock`,
`vinyl/jazz-blues/jazz`, `vinyl/global-vibes/peru`, and so on. None of it
needs walking: `/vinyl/` is a strict superset. Confirmed by fully paginating
the largest leaf (`vinyl/rock/indie-rock`, 1,152 products) and intersecting
its product ids with the 3,312 from `/vinyl/` — **zero** ids were missing.

One trap here: the category metadata's own `count` is not trustworthy.
`/catalog/?format=json` reports `count: 13123` for Vinyl and the listing
reports `count: 3312`; `indie-rock` claims 3,829 against a listing count of
1,152. The store's `sitemap.xml` carries 3,409 product URLs in total, which
corroborates the listing counts, not the metadata. **The listing `count`/`pages`
are authoritative; the category-tree `count` is not.**

### Artist: the title, and only the title

`brand` is `false` on all 3,312 products — there is no vendor field to fall
back on, so the title split is the sole artist source. A title with no
separator is skipped, the fleet's "no artist source -> skip" convention.

The separator class is `[-–—]` with whitespace required on at least one side,
the same widened class `cleorecs.py` and `jackpotrecords.py` use. Of the 3,125
titles that split, 3,055 separate on a plain hyphen and 126 on an en dash; no
live title separates on an em dash, which is carried only because the class is
shared with those two crawlers. The asymmetric forms
(`Watchhouse -\tThis Side of Jordan`, `Gracie\tAbrams- Daughter from Hell`) are
both live and both handled.

**Whitespace is collapsed before splitting.** The store's spreadsheet imports
leave raw tabs and multi-space runs in titles, both beside the separator
(`Polvo\t- In Prism`) and *inside* the artist (`Chuck\tProphet - Wake The
Dead`). The separator regex tolerates the first unaided — `\s` matches a tab —
but not the second, which would write a literal tab into the stock row.

**There is deliberately no unspaced-hyphen fallback.** It would parse a
genuine `Maruja-Pain To Power`, but this store also stocks `Now That's What I
Call K-Pop`, `Country Funk Vol. 3 1975-1982`, and `The Meters "Look-Ka Py Py"
LP`, none of which names an artist before the hyphen. Splitting those
produces a wrong artist *and* a wrong album — a row that can never match a
Discogs release and reads as garbage in the Store tab — where skipping
produces nothing. This fleet skips what it cannot split.

**A colon is not a separator.** Live colons belong to the album
(`Eccentric Soul: The Tammy Label`, `Adrian Younge Presents: Something About
April`), never to an artist boundary.

Net: 3,125 of 3,312 products yield a row. Of the 187 skipped, 128 carry no
usable separator — 80 with none at all, 26 whose only dash is unspaced, 22
whose only candidate is a colon — and 59 are CDs or cassettes (below).

### Format filter: negative, title-level, with a vinyl override

The Vinyl category carries a run of mis-filed CDs and cassettes. The filter
follows `onetwothreefourgo.py`'s shape — a negative format pattern with a
vinyl-vocabulary override — with two store-specific departures.

**`tapes?`, `book` and `magazine` are deliberately absent.** Sibling crawlers
test a *separate* format descriptor field; this store fuses the format into
the title, so the pattern is read against the album and artist names too.
`Felbm - Tape 1/Tape 2` is a live vinyl LP, `Book of Paul` a live album, and
`Peel Dream Magazine` a live artist. Every live cassette here says "cassette"
somewhere, so nothing is lost by dropping `tapes?`.

**The override must not let "vinyl" vote for itself.** The store marks its
mis-filed CDs by *negating* the category — `CD NOT VINYL`, `(CD not Vinyl)`,
`***CD (not vinyl)` — and that phrase contains the override's strongest
keyword. A plain override therefore reads the annotation as proof of the
opposite and republishes the CD as a record: **16 live products leaked through
before this was caught by a test.** `_NOT_VINYL_RE` deletes the negated phrase
before the override is tested.

The counted forms (`\d*[x×]?`) stay on both sides, per the
`spv.py`/`onetwothreefourgo.py` regression: a bare `\bcds?\b` cannot see the
CD in `2xCD`.

After the fix, exactly one kept row still names a competing format —
`Ata Kak - Obaa Sima (Splatter Vinyl LP+DVD)`, a genuine record-plus-disc
bundle the override is right to keep.

### Availability, variants, price

- `available` is `true` on all 3,312 products: the listing shows only in-stock
  items, so there is no availability filter and no pre-order concept to model.
- `variant` is `"Default"` on all 3,312 — single-variant products throughout,
  so no variant disambiguation is needed.
- Price is `price.price`. `price_old` is set on no product, so there is no
  sale-price handling. One live product is listed at 0, which is "call us",
  not free — it is reported as an unknown price rather than a free record.

### Cover images

`image` is an image **id**, not a URL. The CDN URL is
`https://cdn.shoplightspeed.com/shops/{shop_id}/files/{image_id}/{slug}.jpg`.
The trailing filename segment is ignored entirely — confirmed live that
`.../77301533/x.jpg` serves the same bytes as the real slug — so it is
populated from the product handle only to keep the URL readable. 49 live
products carry `"image": 0` and get a null cover.

`shop_id` and `currency` are read from the `shop` block of every response
rather than hardcoded, so neither is a magic number in the plugin.

### Drift guards

`db.replace_stock_items()` DELETEs this source's rows before inserting, and
`_sync_stock` only skips it when the crawl *raised* — so a generator that
completes with nothing to show wipes the store's whole snapshot and records
the site as healthy. These conditions therefore raise:

- an empty `products` object on **any** page inside the reported range, not
  just the first (the category is gone, or the JSON shape drifted). The store
  derives `pages` from `count`, so every page in range has rows;
- a response whose `page` is not the page requested (pagination contract
  drift, including a regression to the ignored `?page=` form);
- a response carrying no usable `pages` count, which would otherwise default
  a whole multi-page catalog down to a one-page snapshot;
- a response carrying no `shop` id or currency, which would otherwise invent
  a currency and strip every cover URL;
- a product with no `url`, whose row would otherwise carry the store root as
  its identity;
- zero parsed rows across the whole walk (title format drift).

A mid-walk HTTP failure raises through `catalog_http.get_with_retry()` after
its retry budget, which likewise leaves the previous snapshot intact.

### Fields

| Field | Source |
| --- | --- |
| `artist` | title, before the first spaced `[-–—]` |
| `title` | title, after that separator |
| `format` | `"Vinyl"`, hardcoded |
| `price` | `price.price`, `0` reported as unknown |
| `currency` | `shop.currency`, upper-cased |
| `url` | `{base_url}/{product.url}` |
| `cover_image_url` | Lightspeed CDN URL from `shop.id` + `image` |

## Queue fan-out

Nothing new. A `catalog` source's rows enter `crawl_queue` as stock-item
targets exactly as every sibling catalog crawler's do, and
`crawlers.enabled` is resolved at dispatch, so enabling or disabling this
source needs no restart, purge, or re-sync.

## Testing

`backend/tests/test_byrdlandrecords_crawler.py`, `respx`-mocked, no browser
and no live network. Fixtures are marked at their definition as CAPTURED
(verbatim from the live response) or ALTERED (that captured product with
fields swapped).

Covered: each separator form and the asymmetric-whitespace variants; the
hyphen-inside-a-name guard (`Jay-Z`, `K-Pop`, `1975-1982`); tab collapse
inside the artist; no-separator and colon skips; the CD/cassette filter, its
counted forms, the `NOT VINYL` negation, and the three false-positive guards
(`Tape`, `Book`, `Magazine`); the vinyl-plus-DVD override; field mapping,
zero-price, and missing-cover handling; path-based pagination and the
`format`/`limit` params; stopping at the last page without requesting past it;
the page-echo raise; cross-page de-duplication; all three drift guards;
payload-derived shop id and currency; and per-page progress reporting.

The crawler was additionally replayed over the fully-cached live catalog:
3,312 products → 3,125 rows, no `(artist, title, url)` collisions, no blank
artist or title, no whitespace contamination, no malformed URL, and no
remaining `NOT VINYL` leak.

## Crawl citizenship and `robots.txt` compliance

`shop.byrdlandrecords.com/robots.txt` disallows only `/admin`, `/account/`,
`/cart/`, `/compare/`, and `/checkout/` — the category listings this crawler
reads are allowed — and asks for `Crawl-delay: 2`. Every request goes through
`catalog_http.get_with_retry()`, whose pacing is driven by
`crawl_delay_seconds` (default 30), so the crawl runs an order of magnitude
slower than the store asks for. There is no `/agents.md`.

A full pass is 34 requests.

## Runtime/agent document impact

None. Registration is automatic via `main.py`'s `seed_bundled_crawlers()`,
which reads `site_name`, `crawler_type`, and `requires_discogs_release` off
the plugin — no wiring changes anywhere else, and no schema change.
