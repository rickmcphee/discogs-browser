# Matador Records Store Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking. (This plan was executed inline in the session that wrote it — recorded here as the historical task log the plans tree is for.)

**Goal:** Add `backend/crawlers/matadorrecords.py`, a `crawler_type="catalog"` Shopify plugin covering Matador Records' official store (`matadorrecords.com`).

**Architecture:** Walk Shopify's built-in `all` collection via the existing `shopify_catalog.iter_products()` helper. Gate on `product_type` for *music* (`Album`, `EP`, `Single`) — the type never says the format here — and read the format off each **variant title's descriptor** with a positive vinyl gate in `spv.py`'s vocabulary. Derive the descriptor by stripping the album name from the variant title (with the edition rewrites and a last-`" - "` fallback), and append it to every row's title so a row's identity depends on its own variant and not on a CD sibling being listed. Set `artist` to `vendor`, except when the vendor is the label itself, where it is the product's first non-housekeeping tag. Skip unavailable variants with **no** pre-order bypass; suffix ` (Pre-Order)` on the `preorder` tag. Raise on an empty collection, on a catalog with no music type, on music with no artist, on music with no variant that reads as vinyl, on a walk that yielded no rows while a product that could have yielded one was unreadable, and on a walk that yielded rows but no prices — so drift can never wipe the previous snapshot through `replace_stock_items()`.

**Tech Stack:** Python ≥3.9, `httpx` (via `shopify_catalog.iter_products`), `pytest`/`pytest-asyncio`, `respx` for HTTP mocking.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`; use `Optional[str]` or leave untyped.
- No new shared module — reuse `shopify_catalog.iter_products()`, `has_tag()`, `strip_vendor_prefix()` and `resolve_cover_image()` unchanged.
- `format` is hardcoded `"Vinyl"`; `currency` is hardcoded `"USD"` (confirmed via the store's `meta.json`).
- **`_COLLECTION_SLUG = "all"`.** The store publishes no format or music collection; `all` agrees with the root `products.json` and `meta.json`'s published count.
- **`product_type` is a music gate, not a format gate.** `Album`/`EP`/`Single` each hold CD, LP, cassette and digital variants side by side. Enumerate the three; do not match negatively, and do not read the format from it.
- **The format gate reads the variant title's descriptor, positively.** An unrecognised descriptor is a CD until it says otherwise. Vocabulary and check order (vinyl word, then merch, then inch marker) follow `spv.py`.
- **The descriptor is appended on every row**, not only on multi-variant products. Keyed on the variant count, a CD sibling going out of print would re-title every vinyl row of the product and orphan everything keyed on the old identity.
- **`artist` is `vendor` unless the vendor is the label** (`Matador Records`, `MatadorRecordsProd`), in which case it is the first non-housekeeping tag — the catalog keeps a release's primary artist alone and the library match behind the Store tab's Collection and Wantlist filters is exact, so credits are never joined. A label-vendored product with no credit left is skipped, never credited to the label. A tag never overrides a real vendor.
- **`strip_vendor_prefix` is used unchanged.** It fires on exactly one live product.
- **Availability comes from `variant.available`; no pre-order bypass.** Every live pre-order reports `available: true`.
- **Readability is judged over the vinyl variants only**, with every(), not any(): they are the only variants that could have yielded, and one readable pressing must not vouch for a malformed sibling.
- **Tallies are nested**, music → artist → vinyl variant → readable, so that a non-zero count means "some product would have yielded a row if it were in stock".
- No comments except where the WHY is non-obvious.
- Registration is automatic via `main.py`'s bundled-crawler startup loop — no wiring changes anywhere else.
- Every commit carries the AI-attribution trailer block required by this repo's `CLAUDE.md`, created via `git commit -F <message-file>`, not `-m`.

Full grounding for every rule above: [`docs/specifications/shaping/2026-09-06-matador-records-store-crawler-design.md`](../shaping/2026-09-06-matador-records-store-crawler-design.md).

**Running the tests.** These tests mock HTTP with `respx` and never reach the store, but the `*_crawler` autouse fixture in `conftest.py` still resolves through the config layer, so run them with the three test env vars set. From `backend/`:

```bash
cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test \
  IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_matadorrecords_crawler.py -v
```

---

### Task 1: Matador Records store crawler + tests

**Files:**
- Create: `backend/crawlers/matadorrecords.py`
- Test: `backend/tests/test_matadorrecords_crawler.py`

**Interfaces:**
- Consumes: `shopify_catalog.iter_products(base_url, collection_slug)`, `has_tag(product, tag)`, `strip_vendor_prefix(title, vendor)`, `resolve_cover_image(product, variant)` — all exist unchanged in `backend/shopify_catalog.py`.
- Produces: a `Crawler` class with the standard `catalog` plugin surface (`site_name`, `base_url`, `genre_summary`, `genre`, `crawler_type`, `async def crawl_catalog()`), yielding `{"artist", "title", "format": "Vinyl", "price", "currency": "USD", "url", "cover_image_url"}`.

- [x] **Step 1: Ground the design against the live store** — identify the platform, list the collections, histogram `product_type`, tabulate every variant descriptor, check `vendor` and `tags`, availability, pre-orders, images, prices, `meta.json` and `robots.txt`.
- [x] **Step 2: Write the crawler** — `all` collection, enumerated music-type gate, positive variant-descriptor vinyl gate, descriptor derivation with the edition rewrites, always-appended descriptor, vendor-or-tags artist, exact-case vendor-prefix strip, no pre-order bypass, guarded price parse, nested drift guards.
- [x] **Step 3: Write the test file** — fixtures distinguish captured / altered / invented provenance, each marked at its definition; cases per the design spec's Verification section.
- [x] **Step 4: Replay over the fully-cached live catalog** — 282 products walked → 242 music → 236 with a vinyl variant → 220 rows, no `item_key` collisions, no blank artist or title, no whitespace contamination, no malformed URL, no missing cover, no null price.
- [x] **Step 5: Run the test file** — all tests in it pass.
- [x] **Step 6: Mutation-check that each guard and rule bites** — mutate the crawler once per guard or rule and confirm only the intended tests fail.
- [x] **Step 7: Run the wider crawler test selection for regressions** (`pytest tests/ -k crawler` with the three test env vars set — the plugin loader imports every module in `backend/crawlers/`, so a syntax error in the new file breaks unrelated tests). Compare against a baseline on `main`.
- [x] **Step 8: Commit** via `git commit -F`, with trailers.

---

## Post-implementation: pre-PR spec-drift check

- [x] **Grep both spec trees** (`docs/superpowers/specs/`, `docs/specifications/shaping/`) for the files, symbols, and names this diff touches.
- [x] **Delete any crawler/store/source/plugin/test count** found in a spec visited during the check, rather than updating it.
- [x] **Record findings in the PR description** (drift found and fixed, or none).
