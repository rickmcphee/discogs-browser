# Bella Union Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking. (This plan was executed inline in the session that wrote it — recorded here as the historical task log the plans tree is for.)

**Goal:** Add `backend/crawlers/bellaunion.py`, a `crawler_type="catalog"` Shopify plugin covering Bella Union's webstore (`bellaunion.com`), at the `all` collection the request named.

**Architecture:** Walk the store's `all` collection via the existing `shopify_catalog.iter_products()` helper. The artist and the album both come from the product title's `Artist - Album` convention — `vendor` names the label and is never read, so one split of one field yields the whole credit. A product then has to claim to be a record before any of its variants can be published: `product_type == "Vinyl"`, or a variant naming a record. The per-variant gate is negative — a bundle or a garment rejects first, then a record word admits, then another medium or a bare garment size rejects, and anything else is admitted on the product's own claim. The row's title is the album followed by the variant descriptor, so it still prefix-matches a library title. Skip unavailable variants with **no** pre-order bypass and **no** ` (Pre-Order)` marker. Raise on an empty collection, on a catalog with no parseable title, on a walk that yielded rows but no prices, and on a walk that yielded no rows while a product went unclassified, lost its identity, dropped an unusable variant or carried no readable availability flag — so drift can never wipe the previous snapshot through `replace_stock_items()`.

**Tech Stack:** Python ≥3.9, `httpx` (via `shopify_catalog.iter_products`), `pytest`/`pytest-asyncio`, `respx` for HTTP mocking.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`; use `Optional[str]` or leave untyped.
- No new shared module — reuse `shopify_catalog.iter_products()`, `has_tag()` and `resolve_cover_image()` unchanged. `strip_vendor_prefix` is not used: `vendor` is the label, and the title separates artist from album with a spaced dash.
- `format` is hardcoded `"Vinyl"`; `currency` is hardcoded `"GBP"` (confirmed via the store's `meta.json` and `cart.js`).
- **`_COLLECTION_SLUG = "all"`.** The store publishes no vinyl or format collection at all, so there is no narrower shelf. `collections.json` reports a *smaller* `products_count` for `all` than `products.json` returns; the walk's own exhaustion is the catalog, and it agrees exactly with `meta.json`'s `published_products_count`.
- **The artist and the album both come from the title, and both are required.** `vendor` names the label (`Bella Union`, `4AD`, `Fontana`) on every product, so there is no second source and therefore one tally and no co-occurrence tally. The separator is a whitespace-dash-whitespace run, first occurrence, so a hyphen inside either half is not a separator.
- **Two live records are skipped** because the store did not title them to its own convention (one substitutes two spaces for the separator, the other a colon). Each salvage would be a rule firing on one product that can only misfire afterwards; the strict parse is also the second, independent rule that keeps the store's merch out.
- **A product must claim to be a record**: `product_type == "Vinyl"`, or a variant naming one. Neither alone covers the catalog — the type is blank on four records *and* on all seven merch products, and a variant naming a record misses the records whose only variant is `Default Title`, a box set or a `Gold Edition`.
- **The store's `Merch` tag rejects outright**, ahead of everything else. It agrees exactly with the store's `merch` collection in both directions.
- **The format gate reads the descriptor only**, never the product title: bundle/garment first, then record word, then other medium, then a bare garment size, defaulting to admit. Its record-word boundaries are Unicode-aware and mark-folded, ported from `dongiovannirecords.py`: `[a-z]` is ASCII-only under `IGNORECASE` so `éLP CD` admitted an embedded `LP` ahead of its `CD`, the inch marker's quote glyph needed a right-hand boundary or `12"CD` read as a complete marker, and `\w` excludes combining marks so a decomposed descriptor read differently from its precomposed twin. Folding is applied at every matching site, `_claims_vinyl` included. Its rejecting vocabulary spells cassette `casse+tte`, because the store's live catalog contains `Casseette` on a product whose other variants are records.
- **A `+`-joined extra is not a bundle.** A zine, signed print, postcard, polaroid, patch, comic or trading cards is priced at or below its product's plain pressing and is published; a garment combo is roughly double and is not.
- **The variant descriptor is appended on every row that names one**; the `Default Title` placeholder is the one exception, and only as a product's sole variant. Catalog numbers the store writes into the descriptor are kept verbatim.
- **Availability comes from `variant.available`, literal `True` only; no pre-order bypass.** No ` (Pre-Order)` marker either: the store's only pre-order signal is membership of a separate collection, and `compute_item_key` hashes the title, so a marker that disappeared on release would re-key every pressing and orphan the saves and judgments held against it.
- **Readability is judged against the raw variant set** (a sole blank-titled variant that is readably sold out is readable; no variants at all is not), reading the kept pressings' flags with `all()`, not `any()`, and paired with `_unusable_dropped_variant` running first. Those flags are read **through the same format gate `_items` publishes through**: this store mixes formats within one product's variant list, so reading every titled variant let a CD's unreadable flag condemn a record that was readably sold out — a case the vinyl-only shelves the sibling crawlers walk cannot express.
- **A title-less product counts toward `identity_missing` before the classification tests**, since the parse reads the title and such a product can never be classified. **A product read and deliberately skipped — the store's merch, or one with no record among its formats — does not count toward `unclassifiable`**; that test runs first, so a sold-out CD-only product cannot raise drift. Neither exemption reads the *product* title, so a non-record whose title did not parse is exempt too; requiring a readable album there made the branch depend on the one field it must not. But only the `Merch` tag reads no title at all — `_claims_vinyl` reads variant titles, and for an untyped record that is its whole claim — so `_unusable_dropped_variant` runs **before** the claim, or blanking a variant title turns an in-stock untyped record into a self-declared non-record and the empty walk goes unguarded. **`identity_missing` and `unreadable_stock` are otherwise nested inside the record classification**, so a non-zero count means "some product would have yielded a row if it were in stock".
- No comments except where the WHY is non-obvious.
- Registration is automatic via `main.py`'s bundled-crawler startup loop — no wiring changes anywhere else.
- Every commit carries the AI-attribution trailer block required by this repo's `CLAUDE.md`, created via `git commit -F <message-file>`, not `-m`.

Full grounding for every rule above: [`docs/specifications/shaping/2026-09-08-bella-union-store-crawler-design.md`](../shaping/2026-09-08-bella-union-store-crawler-design.md).

**Running the tests.** These tests mock HTTP with `respx` and never reach the store, but the `*_crawler` autouse fixture in `conftest.py` still resolves through the config layer, so run them with the three test env vars set. From the repository root:

```bash
cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test \
  IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_bellaunion_crawler.py -v
```

---

### Task 1: Bella Union crawler + tests

**Files:**
- Create: `backend/crawlers/bellaunion.py`
- Test: `backend/tests/test_bellaunion_crawler.py`

**Interfaces:**
- Consumes: `shopify_catalog.iter_products(base_url, collection_slug)`, `has_tag(product, tag)` and `resolve_cover_image(product, variant)` — all three exist unchanged in `backend/shopify_catalog.py`.
- Produces: a `Crawler` class with the standard `catalog` plugin surface (`site_name`, `base_url`, `genre_summary`, `genre`, `crawler_type`, `async def crawl_catalog()`), yielding `{"artist", "title", "format": "Vinyl", "price", "currency": "GBP", "url", "cover_image_url"}`.

- [x] **Step 1: Ground the design against the live store** — identify the platform; walk the collection at two page sizes to confirm pagination is stable; histogram `vendor`, `product_type`, tags and every variant title; test the title convention against the whole catalog; enumerate the collections and check the two plausible narrower shelves; find the pre-order signal; compare bundle prices against their plain pressings; check availability, images, prices, `meta.json`, `cart.js`, `collections.json` and `robots.txt`.
- [x] **Step 2: Write the crawler** — the `all` collection; title-only credit with no vendor fallback; merch-tag rejection; two-signal product claim; descriptor-only negative format gate with the bundle/garment test first; always-appended descriptor with the placeholder exception; no pre-order marker and no availability bypass; guarded price parse; a single title tally with nested classification, identity, variant-identity and stock tallies.
- [x] **Step 3: Write the test file** — fixtures distinguish captured / altered / invented provenance, each marked at its definition; cases per the design doc's Verification section.
- [x] **Step 4: Replay over the fully-cached live catalog** — 221 products walked → 182 rows across 100 artists, no `item_key` collisions, no blank artist or title, no malformed URL, no missing cover, no null price; every product yielding nothing accounted for individually.
- [x] **Step 5: Run the test file** — all tests in it pass.
- [x] **Step 6: Mutation-check that each guard and rule bites** — mutate the crawler once per guard or rule and confirm the tests fail. Four mutations initially survived (the merch-tag gate, the product-level claim, `all()` vs `any()` in the stock check, and the title-less-product ordering); three tests were added to close them, since each rule had been reachable only behind another that rejected first.
- [x] **Step 7: Run the wider crawler test selection for regressions** (`pytest tests/ -k crawler` with the three test env vars set — the plugin loader imports every module in `backend/crawlers/`, so a syntax error in the new file breaks unrelated tests).
- [x] **Step 8: Commit** via `git commit -F`, with trailers.

---

## Post-implementation: pre-PR spec-drift check

- [x] **Grep both spec trees** (`docs/superpowers/specs/`, `docs/specifications/shaping/`) for the files, symbols, and names this diff touches.
- [x] **Delete any crawler/store/source/plugin/test count** found in a spec visited during the check, rather than updating it.
- [x] **Record findings in the PR description** (drift found and fixed, or none).

## Follow-up noted, not done here

`pytest tests/ -k crawler` reports one failure,
`tests/test_queue_router.py::test_broad_rows_fan_out_to_every_eligible_crawler`.
It is pre-existing and unrelated to this change: `-k crawler` selects
`test_main.py::test_startup_seeds_bundled_crawlers`, which boots the app and
registers every bundled plugin into the shared per-run database, and the
queue-router test then asserts exact unit counts over the crawlers it
registered itself. Confirmed by reproducing it with this branch's crawler
removed. Fixing it means isolating one of the two tests from the other's
`crawlers` rows, which is a change to shared test fixtures rather than to
this crawler.
