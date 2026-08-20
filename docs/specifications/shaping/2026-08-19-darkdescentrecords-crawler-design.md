# Dark Descent Records store crawler design

Date: 2026-08-19
Branch: `claude/dark-descent-vinyl-crawler-9189a5`

## Problem

Dark Descent Records (`darkdescentrecords.com`), an underground metal label
and mailorder store based in Colorado specializing in death, black, and
doom metal, is not covered by any existing crawler. Unlike the ~35
label-store `catalog` plugins already in `backend/crawlers/` (all Shopify
or Bigcartel), this store runs WooCommerce on WordPress — no existing
crawler in this repo targets that platform.

## Scope

Add `backend/crawlers/darkdescentrecords.py` as a `crawler_type="catalog"`
plugin, paginating the store's public WooCommerce Store API v1
(`/wp-json/wc/store/v1/products?category=vinyl-lp`) directly with `httpx` —
matching the user's requested URL,
`https://www.darkdescentrecords.com/shop/product-category/music/vinyl/vinyl-lp/`,
which maps 1:1 to the `vinyl-lp` product category.

**Non-goals**

- **No browser.** Confirmed live: the Store API and product pages are
  served to plain `httpx`/`curl` with no Cloudflare gate or bot
  interstitial, and no `robots.txt` exists on this domain at all (see
  "Crawl citizenship" below).
- **No coverage beyond `vinyl-lp`.** Sibling categories (`Vinyl 7"`,
  boxsets, etc.) under the parent `Vinyl` category are out of scope — the
  user asked for this exact category URL.
- **No CD/cassette/merch coverage.** Out of scope by category selection;
  this store's other product categories are never requested.

## Technical grounding

All figures below were confirmed against the live site on 2026-08-19.

### WooCommerce Store API, not HTML scraping

WordPress is installed under `/shop`, so `base_url =
"https://www.darkdescentrecords.com/shop"`. The Store API's collection
endpoint returns structured JSON per product — `name`, `permalink`,
`prices` (integer minor units + `currency_code`/`currency_minor_unit`),
`images`, `is_purchasable`, `is_in_stock`, `type` (`simple`/`variable`) —
eliminating the HTML-regex fragility of a scrape-based crawler like
`sgrecordshop.py`. `vinyl-lp` has 738 products across 8 pages at
`per_page=100`; the loop stops on the first response shorter than
`per_page`, avoiding a dependency on the `X-WP-Total*` response headers.

### Title parsing: `Artist – Title`, entity-encoded en dash

The API's own `name` field is HTML-entity-encoded even in JSON — e.g.
`"Eldfödd &#8211; Risen from the Flames LP"` — so `html.unescape()`
must run before splitting. After unescaping, 737/738 vinyl-lp titles
(99.9%) follow `Artist – Title` with a literal en dash (U+2013) as
separator:

```python
_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s+–\s+(?P<album>.+)$')
```

Non-greedy `.+?` on the artist side means the *first* en dash is treated
as the separator — correct even for the one title with two dashes
(`Ascendency / Chaotian / Septage / Sequestrum – "Tetralogy of Death –
Vol. 2" LP`, confirmed live), since the album's own internal dash is never
before the real separator. The one holdout with no separator at all
(`Regere Sinister / Reptile Womb Split LP`) is skipped — no reliable
artist-name fallback field exists on this platform (no Shopify `vendor`,
no Bigcartel curated `artists`), matching this repo's established
"neither source → skip" convention (`asbestosrecords.py`,
`sgrecordshop.py`).

### Variable products: one extra request per item, via embedded JSON

72/738 products (~10%) are WooCommerce `variable` products (color/pressing
variants) rather than one `simple` product per pressing. The Store API's
collection endpoint does **not** include per-variation price/stock — only
`{id, attributes}` pairs — and there is no public, unauthenticated Store
API sub-resource for variation detail (`/products/{id}/variations` 404s;
that path is REST API v3, which requires auth). The only unauthenticated
source is the classic WooCommerce `data-product_variations` JSON blob
embedded in the product page's own HTML (`<form class="variations_form"
data-product_variations="...">`), HTML-entity-encoded the same way as the
listing JSON. So variable products cost one extra `GET` of their product
page, decoded the same way:

```python
_VARIATIONS_RE = re.compile(r'data-product_variations="([^"]*)"')
```

Each decoded variation carries its own `display_price` (already in whole
dollars, unlike the collection endpoint's minor-unit `prices.price`),
`is_purchasable`, `is_in_stock`, `attributes` (e.g. `{"attribute_variant":
"Black"}`), and optionally its own `image`. The 738 products are paged 100
at a time (8 collection-page requests), plus one request per variable
product (~72) — ~80 requests per full sync, paced identically to every
sibling crawler.

### Fields

- **price** — simple products: `int(prices["price"]) /
  10**currency_minor_unit` (confirmed minor unit 2, i.e. cents). Variable
  products: `float(variation["display_price"])` directly (already dollars).
- **currency** — `prices["currency_code"]`, confirmed `"USD"` on every
  sampled product.
- **url** — `permalink`, already an absolute URL.
- **cover_image_url** — `images[0]["src"]`; for a variable product's
  variation, its own `image.src` if present, else the parent's.
- **format** — `"Vinyl"` unconditionally — the category itself is the
  filter, unlike the Shopify sibling crawlers that mix formats and need a
  per-variant regex.
- **title** — simple products: the parsed album title unchanged (variant
  info like `(Clear/Red/Silver Splatter)` is already baked into the
  product's own `name` on this store, not a separate WooCommerce
  variation). Variable products: `f"{album} — {variant_title}"`, where
  `variant_title` joins the variation's `attributes` values (this store
  uses a single `variant` attribute, so effectively just that value).

### Crawler shape

```python
class Crawler:
    site_name: str = "Dark Descent Records"
    base_url: str = "https://www.darkdescentrecords.com/shop"
    genre_summary: str = (
        "Underground metal label and distro specializing in death, black, and doom metal."
    )
    genre: str = "metal"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]: ...
```

Registration is automatic via `main.py`'s startup loop — no wiring
changes.

## Queue fan-out

738 products in `vinyl-lp`: 666 simple + 72 variable. The one title with
no separator (`Regere Sinister / Reptile Womb Split LP`, confirmed live
`type: "simple"`) is skipped, leaving 665 simple products that each yield
one row, plus 72 variable products that each yield one row per in-stock
variation (confirmed live: 0 out-of-stock/unpurchasable products or
variations at scan time, so no rows are dropped on that gate today; the
check stays in as a correctness guard against future stock changes).
Comparable in scale to `no_idea_records.py`'s ~345 rows.

## Testing

`backend/tests/test_darkdescentrecords_crawler.py`, on the sibling
crawlers' pattern — `respx`-mocked HTTP responses and hand-written
JSON literals taken from confirmed-live API/HTML responses, no live site,
no bot-detection risk. Cases:

- en-dash title split, including the entity-encoded (`&#8211;`) form
- first-en-dash split when the album title has its own internal en dash
- no-separator title → skipped
- simple product → single row, correct minor-unit price conversion
- simple product not purchasable/in stock → skipped
- variable product → fetches its product page, decodes
  `data-product_variations`, emits one row per in-stock variation with the
  parsed `display_price`
- a variation that is unpurchasable/out of stock → excluded
- a variation with no `image` → falls back to the parent product's image
- markup drift (no `data-product_variations` found) → raises, rather than
  silently dropping the product as if the site had nothing to offer
- catalog pagination: short page stops the loop without an extra request;
  a full page continues to the next
- HTTP failure on the listing endpoint → raises (circuit-breaker
  compatibility — "the site answered and has nothing" must not be
  conflated with "the request failed")
- site metadata (`site_name`, `base_url`, `genre`, `crawler_type`)

## Crawl citizenship and `robots.txt` compliance

Per the normative section of
`2026-08-09-amoeba-store-crawler-design.md`, which requires checking
`robots.txt` for the specific paths a new crawler will request, and
recording the finding here, plus the load-discipline standard every
sibling crawler in this repo follows. This site's finding:

- **No `robots.txt` exists on this domain.** `GET
  https://www.darkdescentrecords.com/robots.txt` (following the
  `darkdescentrecords.com` → `www.darkdescentrecords.com` redirect)
  returns the site's own WordPress 404 page (`Page not found — Dark
  Descent Records`, `<meta name="robots" content="noindex, follow">` on
  the 404 page itself, not a robots.txt directive), confirmed via `curl`.
  With no file present, there is no `Disallow` to violate.
- **No `/agents.md` exists either** (`404` at both
  `darkdescentrecords.com/agents.md` and the `/shop`-prefixed path) —
  checked for completeness, not because the Amoeba precedent requires it;
  no policy document of either kind constrains this crawler.
- This crawler never transacts on its own: it only links out to each
  product's own page, matching every sibling crawler in this repo.
- Load: 8 GETs to page the `vinyl-lp` collection (738 products,
  `per_page=100`) plus ~72 GETs for variable-product variation detail —
  ~80 requests per full sync. Paced at `random.uniform(delay * 0.5,
  delay)` between every request, `crawl_delay_seconds` defaulting to 30s,
  matching every sibling crawler. Any HTTP failure raises rather than
  yielding an empty result, preserving the repo's circuit-breaker
  contract.
- If Dark Descent Records blocks this crawler, adds a `robots.txt`
  covering these paths, or asks us to stop, the response is to disable
  the plugin.

## Runtime/agent document impact

No `.agents/INPUTS.md`, `.agents/OUTPUTS.md`, or `.agents/INSTRUCTIONS.md`
exist in this repo. This change adds no new trigger and no new inbound
interface — one new outbound host (`darkdescentrecords.com`).

`backend/version.py`'s `VERSION` is derived from git and is not edited by
this change.
