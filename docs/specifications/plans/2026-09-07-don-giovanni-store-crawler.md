# Don Giovanni Records Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking. (This plan was executed inline in the session that wrote it — recorded here as the historical task log the plans tree is for.)

**Goal:** Add `backend/crawlers/dongiovannirecords.py`, a `crawler_type="catalog"` Shopify plugin covering Don Giovanni Records' webstore (`dongiovannirecords.com`), at the `vinyl` collection the request named.

**Architecture:** Walk the store's `vinyl` collection via the existing `shopify_catalog.iter_products()` helper. The artist is `vendor`, which is a real credit on every product and is never the label's own name; the album and the format descriptor come from the title's `Artist "Album" <format>` convention, which the whole store follows without exception. The row's title is the album followed by the format, then ` (Pre-Order)` when the product carries the store's `preorder` tag, then the variant colour — so it still prefix-matches a library title. A record word in the descriptor admits, a word naming another medium or a merch item rejects, and anything else is admitted on the collection's own claim. Bundles are skipped on the title. Skip unavailable variants with **no** pre-order bypass. Raise on an empty collection, on a catalog with no `vendor`, on a catalog with no parseable title, on a walk that yielded rows but no prices, and on a walk that yielded no rows while a record had no identity or no readable availability flag — so drift can never wipe the previous snapshot through `replace_stock_items()`.

**Tech Stack:** Python ≥3.9, `httpx` (via `shopify_catalog.iter_products`), `pytest`/`pytest-asyncio`, `respx` for HTTP mocking.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`; use `Optional[str]` or leave untyped.
- No new shared module — reuse `shopify_catalog.iter_products()`, `has_tag()` and `resolve_cover_image()` unchanged. `strip_vendor_prefix` is not used: the store separates artist from album with quotes, not a dash.
- `format` is hardcoded `"Vinyl"`; `currency` is hardcoded `"USD"` (confirmed via the store's `meta.json` and `cart.js`).
- **`_COLLECTION_SLUG = "vinyl"`.** `collections.json` reports a `products_count` larger than the published catalog; `products.json` returns the published products and those are what is walked. The shelf is exactly the store's records in both directions — nothing non-vinyl in it, no vinyl outside it.
- **The artist comes from `vendor`, and from nothing else.** No fallback to the title's artist prefix: it would defeat the artist-source guard, and the title truncates a long credit mid-word where `vendor` does not.
- **The album and the format descriptor come from the title, and both are required.** Neither the leading group nor the album group may contain a quote, which pins all three of the title's quotes; the leading group is non-capturing and may be empty, since the credit comes from `vendor` and a title that omits the artist still carries a readable album and format; the closing quote's lookahead then refuses a nested quotation rather than yielding a severed album. A title naming no format is not a parse — that is what keeps the store's books, pins and stickers out if one is ever mis-shelved here, and a store-wide loss of the trailing format raises album-source drift rather than silently emptying the walk.
- **The row's title is `album + " " + descriptor`, whitespace-collapsed**, then the pre-order marker, then the colour. The descriptor stays *after* the album so `db._library_release_match_sql`'s prefix test still matches.
- **Bundles are skipped**, matched on the whole title, ahead of both the parse and the format gate.
- **The format gate reads the descriptor only**, record word first and other-medium/merch second, defaulting to admit. Its rejecting vocabulary is the store's own `product_type` values, plus `Zine`; the disc media carry the same optional disc-count prefix the record words do, since `2xCD` is one of those types and `\bcds?\b` cannot see the `CD` in it. `product_type` itself is not the gate — it disagrees with the title on a live product and an enumeration would silently drop a format the store adds.
- **The variant colour is appended on every row that names one**; the `Default Title` placeholder is the one exception, and only as a product's sole variant.
- **Availability comes from `variant.available`, literal `True` only; no pre-order bypass.** A ` (Pre-Order)` marker *is* written, from the store's `preorder` tag, which agrees exactly with its `preorders` collection.
- **Readability is judged over the admitted variants only**, with `all()`, not `any()`.
- **`artist_ok` and `parsed_ok` are tallied independently** — they are independent sources, and conflating them lets one going dark hide behind the other. **`identity_missing` and `unreadable_stock` are nested inside the format gate**, so a non-zero count means "some product would have yielded a row if it were in stock".
- No comments except where the WHY is non-obvious.
- Registration is automatic via `main.py`'s bundled-crawler startup loop — no wiring changes anywhere else.
- Every commit carries the AI-attribution trailer block required by this repo's `CLAUDE.md`, created via `git commit -F <message-file>`, not `-m`.

Full grounding for every rule above: [`docs/specifications/shaping/2026-09-07-don-giovanni-store-crawler-design.md`](../shaping/2026-09-07-don-giovanni-store-crawler-design.md).

**Running the tests.** These tests mock HTTP with `respx` and never reach the store, but the `*_crawler` autouse fixture in `conftest.py` still resolves through the config layer, so run them with the three test env vars set. From `backend/`:

```bash
cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test \
  IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_dongiovannirecords_crawler.py -v
```

---

### Task 1: Don Giovanni Records crawler + tests

**Files:**
- Create: `backend/crawlers/dongiovannirecords.py`
- Test: `backend/tests/test_dongiovannirecords_crawler.py`

**Interfaces:**
- Consumes: `shopify_catalog.iter_products(base_url, collection_slug)`, `has_tag(product, tag)`, `resolve_cover_image(product, variant)` — all exist unchanged in `backend/shopify_catalog.py`.
- Produces: a `Crawler` class with the standard `catalog` plugin surface (`site_name`, `base_url`, `genre_summary`, `genre`, `crawler_type`, `async def crawl_catalog()`), yielding `{"artist", "title", "format": "Vinyl", "price", "currency": "USD", "url", "cover_image_url"}`.

- [x] **Step 1: Ground the design against the live store** — identify the platform; walk the collection at two page sizes to confirm pagination is stable; histogram `vendor`, `product_type`, tags and every variant title; test the title convention against the whole catalog; check `vendor` against the title's artist prefix product by product; diff the collection against the store's `all` collection in both directions; find the pre-order signal and cross-check it against the `preorders` collection; check availability, images, prices, `meta.json`, `collections.json` and `robots.txt`.
- [x] **Step 2: Write the crawler** — the `vinyl` collection; `vendor` artist with no fallback; quoted-album title parse; album-then-descriptor composition; bundle skip; descriptor-only format gate; always-appended colour with the placeholder exception; pre-order marker with no availability bypass; guarded price parse; independent artist/album tallies and nested identity/stock tallies.
- [x] **Step 3: Write the test file** — fixtures distinguish captured / altered / invented provenance, each marked at its definition; cases per the design doc's Verification section.
- [x] **Step 4: Replay over the fully-cached live catalog** — 158 products walked → 140 rows across 92 artists, no `item_key` collisions, no blank artist or title, no malformed URL, no missing cover, no null price.
- [x] **Step 5: Run the test file** — all tests in it pass.
- [x] **Step 6: Mutation-check that each guard and rule bites** — mutate the crawler once per guard or rule and confirm the tests fail.
- [x] **Step 7: Run the wider crawler test selection for regressions** (`pytest tests/ -k crawler` with the three test env vars set — the plugin loader imports every module in `backend/crawlers/`, so a syntax error in the new file breaks unrelated tests).
- [x] **Step 8: Commit** via `git commit -F`, with trailers.

---

## Post-implementation: pre-PR spec-drift check

- [x] **Grep both spec trees** (`docs/superpowers/specs/`, `docs/specifications/shaping/`) for the files, symbols, and names this diff touches.
- [x] **Delete any crawler/store/source/plugin/test count** found in a spec visited during the check, rather than updating it.
- [x] **Record findings in the PR description** (drift found and fixed, or none).

## Follow-up noted, not done here

`title_key`'s disc-size phrase rule is anchored on a word boundary, so
`2x12"` survives into the key as a literal token where `2xLP` and
`Double LP` fold away — a double LP from this store will not group with
another store's copy of the same pressing under the Cheapest filter. It is
pre-existing (`earache.py` emits the same descriptors and hits it
identically) and fixing it changes a shared module that keys every row in
the Store tab, so it belongs in its own change.
