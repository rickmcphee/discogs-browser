# Music On Vinyl Store Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking. (This plan was executed inline in the session that wrote it — recorded here as the historical task log the plans tree is for.)

**Goal:** Add `backend/crawlers/musiconvinyl.py`, a `crawler_type="catalog"` Shopify plugin covering Music On Vinyl's webshop (`www.musiconvinyl.com`), walking the `all-products` collection the request named.

**Architecture:** Walk the store's `all-products` collection via the existing `shopify_catalog.iter_products()` helper. Gate on `product_type` beginning with the word `Vinyl` — here the type is a format claim, and the collection itself shelves one CD box set. Set `artist` to `vendor`, rewriting `Various Artists` to `Various`. Keep the product title as written, through the shared exact-case vendor-prefix strip. Skip unavailable variants with **no** pre-order bypass; suffix ` (Pre-Order)` on the `Pre-order` tag. Price in EUR. Raise on an empty collection, on a catalog with no vinyl type, on vinyl with no vendor, on a walk that yielded no rows while a product that could have yielded one lacked an identity field or a readable availability flag, and on a walk that yielded rows but no prices — so drift can never wipe the previous snapshot through `replace_stock_items()`.

**Tech Stack:** Python ≥3.9, `httpx` (via `shopify_catalog.iter_products`), `pytest`/`pytest-asyncio`, `respx` for HTTP mocking.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`; use `Optional[str]` or leave untyped.
- No new shared module — reuse `shopify_catalog.iter_products()`, `has_tag()`, `strip_vendor_prefix()` and `resolve_cover_image()` unchanged.
- `format` is hardcoded `"Vinyl"`; `currency` is hardcoded `"EUR"` (confirmed via the store's `meta.json`).
- **`_COLLECTION_SLUG = "all-products"`.** The store's own ALL VINYL collection; `all` adds only discs and a book.
- **`product_type` is the format gate.** Admit a type beginning with the word `Vinyl`; nothing else.
- **`artist` is `vendor`**, with `Various Artists` rewritten to `Various` and `Original Soundtrack` left as written.
- **`strip_vendor_prefix` is used unchanged**, against the vendor as written. No live title carries a prefix.
- **Availability comes from `variant.available`; no pre-order bypass.** Unavailable pre-orders are sold-through limited pressings.
- **The per-variant descriptor is appended only on a multi-variant product**, on `rhino.py`'s pattern; every live product is single-variant.
- **Tallies are nested**, vinyl → vendor → identity → readable, so that a non-zero count means "some product would have yielded a row if it were in stock".
- No comments except where the WHY is non-obvious.
- Registration is automatic via `main.py`'s bundled-crawler startup loop — no wiring changes anywhere else.
- Every commit carries the AI-attribution trailer block required by this repo's `CLAUDE.md`, created via `git commit -F <message-file>`, not `-m`.

Full grounding for every rule above: [`docs/specifications/shaping/2026-09-06-music-on-vinyl-crawler-design.md`](../shaping/2026-09-06-music-on-vinyl-crawler-design.md).

**Running the tests.** These tests mock HTTP with `respx` and never reach the store, but the `*_crawler` autouse fixture in `conftest.py` still resolves through the config layer, so run them with the three test env vars set. From `backend/`:

```bash
cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test \
  IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_musiconvinyl_crawler.py -v
```

---

### Task 1: Music On Vinyl store crawler + tests

**Files:**
- Create: `backend/crawlers/musiconvinyl.py`
- Test: `backend/tests/test_musiconvinyl_crawler.py`

**Interfaces:**
- Consumes: `shopify_catalog.iter_products(base_url, collection_slug)`, `has_tag(product, tag)`, `strip_vendor_prefix(title, vendor)`, `resolve_cover_image(product, variant)` — all exist unchanged in `backend/shopify_catalog.py`.
- Produces: a `Crawler` class with the standard `catalog` plugin surface (`site_name`, `base_url`, `genre_summary`, `genre`, `crawler_type`, `async def crawl_catalog()`), yielding `{"artist", "title", "format": "Vinyl", "price", "currency": "EUR", "url", "cover_image_url"}`.

- [x] **Step 1: Ground the design against the live store** — identify the platform, diff `all-products` against `all`, histogram `product_type`, check `vendor`, `tags`, variants, availability, pre-orders, images, prices, `meta.json` and `robots.txt`.
- [x] **Step 2: Write the crawler** — `all-products` collection, `Vinyl` type gate, vendor artist with the `Various` rewrite, exact-case vendor-prefix strip, no pre-order bypass, multi-variant-only descriptor, guarded price parse, nested drift guards.
- [x] **Step 3: Write the test file** — fixtures distinguish captured / altered provenance, each marked at its definition; cases per the design spec's Verification section.
- [x] **Step 4: Replay over the fully-cached live catalog** — 1,095 products walked → 1,093 vinyl → 1,076 rows, no `item_key` collisions, no blank artist or title, no whitespace contamination, no malformed URL, no missing cover, no null price.
- [x] **Step 5: Run the test file** — all tests in it pass.
- [x] **Step 6: Run the wider crawler test selection for regressions** (`pytest tests/ -k crawler` with the three test env vars set — the plugin loader imports every module in `backend/crawlers/`, so a syntax error in the new file breaks unrelated tests).
- [x] **Step 7: Commit** via `git commit -F`, with trailers.

---

## Post-implementation: pre-PR spec-drift check

- [x] **Grep both spec trees** (`docs/superpowers/specs/`, `docs/specifications/shaping/`) for the files, symbols, and names this diff touches.
- [x] **Delete any crawler/store/source/plugin/test count** found in a spec visited during the check, rather than updating it.
- [x] **Record findings in the PR description** (drift found and fixed, or none).
