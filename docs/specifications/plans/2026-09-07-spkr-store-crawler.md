# SPKR.store Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking. (This plan was executed inline in the session that wrote it — recorded here as the historical task log the plans tree is for.)

**Goal:** Add `backend/crawlers/spkr.py`, a `crawler_type="catalog"` Shopify plugin covering SPKR.store (`spkr.store`), Prophecy Productions' mailorder, at the `vinyl` collection the request named.

**Architecture:** Walk the store's `vinyl` collection via the existing `shopify_catalog.iter_products()` helper. Gate on `product_type` starting with `Vinyl` (here the type *is* the format), with a **negative variant layer** on the variant title for the day a `Format` option grows a CD. Read the artist out of the product title's `Artist - Album (descriptor)` convention, splitting on the **first** ` - ` (an album can carry one, an artist never does — checked against the store's one-collection-per-artist list), keeping the rest verbatim, and crediting `Various Artists` as `Various`. Append the variant title to every row that names a pressing, unescaping the HTML entities the store types into a few; the `Default Title` placeholder carries the title alone, as a product's sole variant only. Skip unavailable variants with **no** pre-order bypass and **no** ` (Pre-Order)` marker: the store's only pre-order signal is membership of its `pre-order` collection, and a title marker would re-key the row when the record ships. Raise on an empty collection, on a catalog with no `Vinyl` type, on records with no dashed title, on a walk that yielded no rows while a product that could have yielded one had no handle or no readable flag, and on a walk that yielded rows but no prices — so drift can never wipe the previous snapshot through `replace_stock_items()`.

**Tech Stack:** Python ≥3.9, `httpx` (via `shopify_catalog.iter_products`), `pytest`/`pytest-asyncio`, `respx` for HTTP mocking.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`; use `Optional[str]` or leave untyped.
- No new shared module — reuse `shopify_catalog.iter_products()` and `resolve_cover_image()` unchanged. `strip_vendor_prefix` and `has_tag` are not used: the vendor is the literal `details`, and the tags carry labels only.
- `format` is hardcoded `"Vinyl"`; `currency` is hardcoded `"EUR"` (confirmed via the store's `meta.json`).
- **`_COLLECTION_SLUG = "vinyl"`.** It holds exactly the products the root `products.json` types `Vinyl`.
- **The `pre-order` collection is not read and no ` (Pre-Order)` marker is written.** `compute_item_key` hashes the title; a marker that vanishes when the record ships would re-key the row (amended after review round 1).
- **`product_type` is the format gate**, matched as the word `Vinyl` at the start of the type.
- **The artist comes from the product title, split on the first ` - `.** The rest of the title is kept verbatim.
- **The variant title is appended on every row that names a pressing**; the `Default Title` placeholder is the one exception, and only as the sole variant.
- **Availability comes from `variant.available`; no pre-order bypass.** Every live pre-order reports `available: true`.
- **Readability is judged over the admitted pressings only**, with every(), not any().
- **Tallies are nested**, type → artist → identity/readable, so that a non-zero count means "some product would have yielded a row if it were in stock".
- No comments except where the WHY is non-obvious.
- Registration is automatic via `main.py`'s bundled-crawler startup loop — no wiring changes anywhere else.
- Every commit carries the AI-attribution trailer block required by this repo's `CLAUDE.md`, created via `git commit -F <message-file>`, not `-m`.

Full grounding for every rule above: [`docs/specifications/shaping/2026-09-07-spkr-store-crawler-design.md`](../shaping/2026-09-07-spkr-store-crawler-design.md).

**Running the tests.** These tests mock HTTP with `respx` and never reach the store, but the `*_crawler` autouse fixture in `conftest.py` still resolves through the config layer, so run them with the three test env vars set. From `backend/`:

```bash
cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test \
  IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_spkr_crawler.py -v
```

---

### Task 1: SPKR.store crawler + tests

**Files:**
- Create: `backend/crawlers/spkr.py`
- Test: `backend/tests/test_spkr_crawler.py`

**Interfaces:**
- Consumes: `shopify_catalog.iter_products(base_url, collection_slug)`, `resolve_cover_image(product, variant)` — both exist unchanged in `backend/shopify_catalog.py`.
- Produces: a `Crawler` class with the standard `catalog` plugin surface (`site_name`, `base_url`, `genre_summary`, `genre`, `crawler_type`, `async def crawl_catalog()`), yielding `{"artist", "title", "format": "Vinyl", "price", "currency": "EUR", "url", "cover_image_url"}`.

- [x] **Step 1: Ground the design against the live store** — identify the platform, list the collections, histogram `product_type`, tabulate the tags, the option schemes and every variant title, check `vendor` and the title convention against the per-artist collections, find the pre-order signal, check availability, images, prices, `meta.json` and `robots.txt`.
- [x] **Step 2: Write the crawler** — the `vinyl` collection; type gate; first-dash title split with the rest verbatim; `Various` rewrite; negative variant gate; always-appended, entity-unescaped variant title with the placeholder exception; no pre-order marker and no pre-order bypass; guarded price parse; nested drift guards.
- [x] **Step 3: Write the test file** — fixtures distinguish captured / altered / invented provenance, each marked at its definition; cases per the design spec's Verification section.
- [x] **Step 4: Replay over the fully-cached live catalog** — 1,058 products walked → 1,058 pass every gate → 1,452 rows, no `item_key` collisions, no blank artist or title, no whitespace contamination, no malformed URL, no missing cover, no null price.
- [x] **Step 5: Run the test file** — all tests in it pass.
- [x] **Step 6: Mutation-check that each guard and rule bites** — mutate the crawler once per guard or rule and confirm the tests fail.
- [x] **Step 7: Run the wider crawler test selection for regressions** (`pytest tests/ -k crawler` with the three test env vars set — the plugin loader imports every module in `backend/crawlers/`, so a syntax error in the new file breaks unrelated tests).
- [x] **Step 8: Commit** via `git commit -F`, with trailers.

---

## Post-implementation: pre-PR spec-drift check

- [x] **Grep both spec trees** (`docs/superpowers/specs/`, `docs/specifications/shaping/`) for the files, symbols, and names this diff touches.
- [x] **Delete any crawler/store/source/plugin/test count** found in a spec visited during the check, rather than updating it.
- [x] **Record findings in the PR description** (drift found and fixed, or none).
