# Earache Records Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking. (This plan was executed inline in the session that wrote it — recorded here as the historical task log the plans tree is for.)

**Goal:** Add `backend/crawlers/earache.py`, a `crawler_type="catalog"` Shopify plugin covering Earache Records' webstore (`earache.com`), at the `vinyl` collection the request named.

**Architecture:** Walk the store's `vinyl` collection via the existing `shopify_catalog.iter_products()` helper. `vendor` names nobody here (`vendor-unknown`, or the label's own name in four spellings), so the artist is read out of the product title's `Artist "Album" descriptor` convention, with the closing quote resolved against the store's three live spellings of it. The row's title is the album followed by the descriptor, quotes removed, so it still prefix-matches a library title. Bundles are skipped; a descriptor naming a record is admitted, one naming another medium is rejected, and anything else is admitted on the collection's own claim. Named variants are appended to every row that has one; the `Default Title` placeholder carries the title alone, as a product's sole variant only. Skip unavailable variants with **no** pre-order bypass and **no** ` (Pre-Order)` marker. Raise on an empty collection, on a catalog with no parseable title, on a walk that yielded no rows while a record had no identity or no readable flag, and on a walk that yielded rows but no prices — so drift can never wipe the previous snapshot through `replace_stock_items()`.

**Tech Stack:** Python ≥3.9, `httpx` (via `shopify_catalog.iter_products`), `pytest`/`pytest-asyncio`, `respx` for HTTP mocking.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`; use `Optional[str]` or leave untyped.
- No new shared module — reuse `shopify_catalog.iter_products()` and `resolve_cover_image()` unchanged. `strip_vendor_prefix` and `has_tag` are not used: the vendor names nobody, and the tags are not read at all.
- `format` is hardcoded `"Vinyl"`; `currency` is hardcoded `"GBP"` (confirmed via the store's `meta.json`).
- **`_COLLECTION_SLUG = "vinyl"`.** `collections.json` reports a `products_count` larger than the store's entire published catalog; `products.json` returns the published products and those are what is walked.
- **The artist comes from the product title's quoted-album convention, and from nothing else.** The `Artists A-Z` tags serialise alphabetically and demonstrably name a non-primary artist first on live products, so they are not a fallback.
- **The closing quote may be `"`/`”` followed by whitespace, a digit, or the end; or `'`/`’` followed by whitespace or the end.** The artist group excludes `"` and `“` so the opening quote is always the title's first.
- **The row's title is `album + " " + descriptor`, whitespace-collapsed.** The descriptor stays *after* the album so `db._library_release_match_sql`'s prefix test still matches.
- **Bundles and the Lucky Dip sale are skipped**, matched on the title.
- **The format gate reads the descriptor only**, vinyl-word first and other-medium second, defaulting to admit.
- **The variant title is appended on every row that names one**; the `Default Title` placeholder is the one exception, and only as the sole variant.
- **Availability comes from `variant.available`; no pre-order bypass, and no marker is written.** The store's own `- PRE-ORDER` suffix is part of the descriptor and is kept verbatim.
- **Readability is judged over the admitted variants only**, with every(), not any().
- **Tallies are nested**, format gate → identity/readable, so that a non-zero count means "some product would have yielded a row if it were in stock". The title tally sits *outside* the gate, so an all-CD shelf does not raise `artist-source drift`.
- No comments except where the WHY is non-obvious.
- Registration is automatic via `main.py`'s bundled-crawler startup loop — no wiring changes anywhere else.
- Every commit carries the AI-attribution trailer block required by this repo's `CLAUDE.md`, created via `git commit -F <message-file>`, not `-m`.

Full grounding for every rule above: [`docs/specifications/shaping/2026-09-07-earache-store-crawler-design.md`](../shaping/2026-09-07-earache-store-crawler-design.md).

**Running the tests.** These tests mock HTTP with `respx` and never reach the store, but the `*_crawler` autouse fixture in `conftest.py` still resolves through the config layer, so run them with the three test env vars set. From `backend/`:

```bash
cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test \
  IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_earache_crawler.py -v
```

---

### Task 1: Earache Records crawler + tests

**Files:**
- Create: `backend/crawlers/earache.py`
- Test: `backend/tests/test_earache_crawler.py`

**Interfaces:**
- Consumes: `shopify_catalog.iter_products(base_url, collection_slug)`, `resolve_cover_image(product, variant)` — both exist unchanged in `backend/shopify_catalog.py`.
- Produces: a `Crawler` class with the standard `catalog` plugin surface (`site_name`, `base_url`, `genre_summary`, `genre`, `crawler_type`, `async def crawl_catalog()`), yielding `{"artist", "title", "format": "Vinyl", "price", "currency": "GBP", "url", "cover_image_url"}`.

- [x] **Step 1: Ground the design against the live store** — identify the platform, walk the collection at two page sizes to confirm pagination is stable, histogram `vendor` and `product_type`, tabulate the tags and every variant title, test the title convention against the whole catalog, find the pre-order signal and check what an unavailable pre-order means, check availability, images, prices, `meta.json`, `collections.json` and `robots.txt`, and diff the collection against the store's `all` collection.
- [x] **Step 2: Write the crawler** — the `vinyl` collection; quoted-album title parse with the three closing-quote spellings; album-then-descriptor title composition; bundle skip; descriptor-only format gate; always-appended variant title with the placeholder exception; no pre-order marker and no pre-order bypass; guarded price parse; nested drift guards.
- [x] **Step 3: Write the test file** — fixtures distinguish captured / altered / invented provenance, each marked at its definition; cases per the design spec's Verification section.
- [x] **Step 4: Replay over the fully-cached live catalog** — 2,092 products walked → 1,998 rows across 567 artists, no `item_key` collisions, no blank artist or title, no malformed URL, no missing cover, no null price.
- [x] **Step 5: Run the test file** — all tests in it pass.
- [x] **Step 6: Mutation-check that each guard and rule bites** — mutate the crawler once per guard or rule and confirm the tests fail.
- [x] **Step 7: Run the wider crawler test selection for regressions** (`pytest tests/ -k crawler` with the three test env vars set — the plugin loader imports every module in `backend/crawlers/`, so a syntax error in the new file breaks unrelated tests).
- [x] **Step 8: Commit** via `git commit -F`, with trailers.

---

## Post-implementation: pre-PR spec-drift check

- [x] **Grep both spec trees** (`docs/superpowers/specs/`, `docs/specifications/shaping/`) for the files, symbols, and names this diff touches.
- [x] **Delete any crawler/store/source/plugin/test count** found in a spec visited during the check, rather than updating it.
- [x] **Record findings in the PR description** (drift found and fixed, or none).
