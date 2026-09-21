# Le Noise Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking. (This plan was executed inline in the session that wrote it, which had no `superpowers:*` skill available — recorded here as the historical task log the plans tree is for.)

**Goal:** Add `backend/crawlers/lenoise.py`, a `crawler_type="catalog"` Shopify plugin covering Le Noise, the Montreal record store (`lenoise.ca`).

**Architecture:** Walk the store's own `vinyl` shelf via the existing `shopify_catalog.iter_products()` helper. The shelf supplies most of the format scoping, so unlike `monorailmusic.py` there is no whole-shop collection to filter down — the crawler's two gates only repair what the store files onto the shelf by mistake. The first reads `product_type` lowercased, because the store's casing slips (`VInyl`) on real records. The second reads the title's bracket, because a seam of CDs and cassettes carries the `Vinyl` product_type; it is two-sided (a bracket may name several media, and a vinyl box with bonus discs must survive) and scoped to brackets rather than the whole title, because albums are *named* after media in both directions. The artist comes from the title's `Artist - Album` split and from nowhere else — `vendor` here is a catalogue number as often as a name, and reversed as often as not — taken lazily at the first dash with whitespace on at least one side, which is looser than the fleet rule and measured rather than assumed. The row's title keeps the pressing bracket, so two pressings of one album never collide on `item_key`. The shelf is larger than Shopify's `products.json` will paginate, so the walk is truncated at the endpoint's ceiling; what falls outside is the early alphabet, and that is accepted and documented rather than worked around. Raise on an empty walk, on a shelf with no vinyl product_type, on every product reading as another medium, on unreadable variants, on records that lost their identity, artist or stock flag, and on rows that all lack a price — so drift can never wipe the previous snapshot through `replace_stock_items()`.

**Tech Stack:** Python ≥3.9, `httpx` (via `shopify_catalog.iter_products`), `pytest`/`pytest-asyncio`, `respx` for HTTP mocking.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`; use `Optional[str]` or leave untyped.
- No new shared module — reuse `shopify_catalog.iter_products()` and `resolve_cover_image()` unchanged. `has_tag` and `strip_vendor_prefix` are not used: tags are never read, and no vendor prefix appears in a title.
- `format` is hardcoded `"Vinyl"`; `currency` is hardcoded `"CAD"` — `products.json` carries no currency to read, the storefront sets `cart_currency=CAD`, and the store is in Montreal.
- **`_COLLECTION_SLUG = "vinyl"`.** The store publishes a shelf per medium and per merchandise kind, so the shelf is the format scoping. The vinyl-adjacent shelves are subsets of it, not a partition.
- **The walk is truncated at Shopify's page ceiling and that is accepted.** `collections.json` reports 41,615 products; the endpoint serves 25,000. The ordering drifts down the alphabet, so the loss is concentrated in A–D rather than scattered. `sort_by` is disallowed by `robots.txt`, tag-scoped `products.json` answers 404, and the sitemap costs one request per product — all three rejected.
- **The product_type gate compares lowercased**, because `VInyl` is live on real records.
- **The medium gate reads brackets, never the whole title**, in both directions: `Lip Cream - Big Foot Cassette (Yellow)` is a record, `Led Zeppelin - Live EP (CD)` is a CD.
- **The medium gate is two-sided**: it disqualifies a product that names a non-vinyl medium *with no vinyl medium named alongside it*, so `(4LP+4CD)` and `(3LP/2CD)` survive while `(CD)`, `(2CD)`, `(Cassette)` and `(CD/BRD)` do not.
- **Each medium pattern carries its own `\d*`** — a leading count has no word boundary before the letters (`2CD`, `3LP`).
- **`EP` is not in the vinyl vocabulary.** It names a length, not a medium, and CD EPs exist.
- **There is no `vendor` fallback for the artist.** The field is the act, the act reversed, a bare catalogue number, the act with a catalogue number glued on, or a genre-first label, with nothing marking which.
- **The separator class is the three dashes the store uses** — ASCII hyphen-minus, U+2010, U+2013. U+2014 does not appear and is not matched.
- **Whitespace is required on at least one side of the separator**, not both. Measured: both sides leaves 10 titles artist-less, at-least-one-side leaves 4, and the six rescued are all split correctly.
- **The artist half is lazy (`.+?`).** That, not the order of the alternatives, is what splits `Iron Butterfly -In-A-Gadda-Da-Vida (Clear)` correctly.
- **The pressing bracket stays on the row's title.** It is what keeps two pressings of one album distinct under `item_key`, and `db._library_release_match_sql`'s exact-or-prefix-followed-by-a-space test still matches the catalog.
- **One row per product, at the cheapest in-stock variant.** Every variant shares `(artist, title, url)`, so a fan-out collides on `item_key`. The store publishes one variant per product today; the pick is kept as the shape that stays correct if that changes.
- **Only the literal `True` admits a variant.** The string `"false"` is truthy.
- **No pre-order handling.** Pre-orders report `available: true` and are purchasable at the listed price, so they are stock; a title marker would re-title every row on ship day and orphan its `item_key`.
- **`resolve_cover_image()` is called through a local type-guarding wrapper**, because the shared helper passes a retyped field straight to `.get()` and a raise there aborts the whole source over display-only artwork.
- **Identity is guarded before the artist, and variants before both** — a product that lost its title lost its artist source too, and one whose variants are unreadable has no readable stock flag either, so the wrong guard would otherwise answer.
- **The four "no rows AND" guards fire only on an empty outcome**; `price-source drift` fires only when no row at all carries a price.
- No comments except where the WHY is non-obvious.
- Registration is automatic via `main.py`'s bundled-crawler startup loop — no wiring changes anywhere else.
- Every commit carries the AI-attribution trailer block required by this repo's `CLAUDE.md`, created via `git commit -F <message-file>`, not `-m`.

Full grounding for every rule above: [`docs/specifications/shaping/2026-09-21-le-noise-crawler-design.md`](../shaping/2026-09-21-le-noise-crawler-design.md).

**Running the tests.** These tests mock HTTP with `respx` and never reach the store, but the `*_crawler` autouse fixture in `conftest.py` still resolves through the config layer, so run them with the three test env vars set. From `backend/`:

```bash
cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test \
  IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_lenoise_crawler.py -v
```

---

### Task 1: Le Noise crawler + tests

**Files:**
- Create: `backend/crawlers/lenoise.py`
- Test: `backend/tests/test_lenoise_crawler.py`

**Interfaces:**
- Consumes: `shopify_catalog.iter_products(base_url, collection_slug)`, `resolve_cover_image(product, variant)` — both exist unchanged in `backend/shopify_catalog.py`.
- Produces: a `Crawler` class with the standard `catalog` plugin surface (`site_name`, `base_url`, `genre_summary`, `genre`, `crawler_type`, `async def crawl_catalog()`), yielding `{"artist", "title", "format": "Vinyl", "price", "currency": "CAD", "url", "cover_image_url"}`.

- [x] **Step 1: Ground the design against the live store** — identify the platform, enumerate the collections, walk every page the vinyl shelf will serve and confirm the ceiling, histogram `product_type`, `vendor`, the tags and the option axes, tabulate every parenthetical descriptor, measure the separator characters and the three candidate whitespace rules against each other, check availability, prices, currency, images and `robots.txt`, and establish what the page ceiling actually excludes.
- [x] **Step 2: Write the crawler** — the `vinyl` shelf; the lowercased product_type gate; the two-sided bracket-scoped medium gate; the lazy at-least-one-side separator over the three live dashes; the pressing bracket kept on the title; one row per product at the cheapest in-stock price; a type-guarding cover wrapper; the ordered drift guards.
- [x] **Step 3: Write the test file** — fixtures marked `captured` or `invented` at their definition; cases per the design's Testing section.
- [x] **Step 4: Replay over the fully cached live catalog** — 25,000 products walked → 17,686 rows across 6,846 artists, no `item_key` collision, no blank artist or title, no null price, no URL off the store's product path, and no row naming a non-vinyl medium except the two vinyl boxes that bundle discs.
- [x] **Step 5: Run the test file** — all tests in it pass.
- [x] **Step 6: Mutation-check that each guard and rule bites** — mutate the crawler once per guard or rule and confirm the tests fail. One mutation survived the first pass (the medium gate reading the whole title rather than its brackets); the captured `Lip Cream - Big Foot Cassette (Yellow)` product was added as a test, after which every mutation was caught.
- [x] **Step 7: Run the wider crawler test selection for regressions** (`pytest tests/ -k crawler` with the three test env vars set — the plugin loader imports every module in `backend/crawlers/`, so a syntax error in the new file breaks unrelated tests). Confirmed the pre-existing Playwright-driven errors and the two order-dependent failures (`test_queue_router`, `test_crawler_crud`) reproduce identically with the new files removed.
- [x] **Step 8: Pre-PR spec-drift check** — grep both spec trees for the files, symbols and strings the diff touches; amend anything that has drifted; strip any inventory count found along the way.
- [x] **Step 9: Commit** via `git commit -F`, with trailers.
