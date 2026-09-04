# Rhino Store Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking. (This plan was executed inline in the session that wrote it — recorded here as the historical task log the plans tree is for.)

**Goal:** Add `backend/crawlers/rhino.py`, a `crawler_type="catalog"` Shopify plugin covering Rhino's official store (`store.rhino.com`), Warner Music's catalog reissue label.

**Architecture:** Walk Shopify's built-in `all` collection via the existing `shopify_catalog.iter_products()` helper and scope to records with a `product_type` gate. The store's own `Vinyl` collection is *not* the source: it is both incomplete (it omits vinyl-typed products the store publishes) and contaminated (it carries CDs, CD-only box sets and bundles), so the format scoping moves onto the store's own structured format field, read over the whole catalog. Set `artist` to `vendor` unconditionally (non-blank on every live vinyl product); keep the title as-is apart from the shared exact-case `strip_vendor_prefix`, which is a live transformation on this store. Skip unavailable variants with **no** pre-order bypass; suffix ` (Pre-Order)` on the `sfccPreOrderProduct` tag. Raise on an empty collection, on a catalog with no vinyl type at all, and on vinyl with no vendors, so drift can never wipe the previous snapshot through `replace_stock_items()`.

**Tech Stack:** Python ≥3.9, `httpx` (via `shopify_catalog.iter_products`), `pytest`/`pytest-asyncio`, `respx` for HTTP mocking.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`; use `Optional[str]` or leave untyped.
- No new shared module — reuse `shopify_catalog.iter_products()`, `has_tag()`, `strip_vendor_prefix()` and `resolve_cover_image()` unchanged.
- `format` is hardcoded `"Vinyl"`; `currency` is hardcoded `"USD"` (confirmed via the store's `meta.json`).
- **`_COLLECTION_SLUG = "all"`.** Do not "fix" this to the store's `vinyl` collection — the design spec tables the live product counts showing it is neither a superset nor a clean subset of what this crawler needs.
- **The format gate matches the `Vinyl` type *family* prefix plus the exact `Boxset - Vinyl Only`,** not an enumeration of today's variants. Enumerating would silently drop a shelf's worth of records when the store adds a variant, and the family prefix is safe because accessories carry their own top-level types. `Boxset - Mixed` and `Boxset - CD Only` stay out.
- **No title-level format veto.** `product_type` is the store's own structured field and is right on all but a handful of products; a title veto would reject genuine hybrid records. The handful of store mistypes is a quantified, accepted false positive — see the design spec.
- **`artist` is `product["vendor"]`, always; a blank vendor skips the product.** Never split the title.
- **`strip_vendor_prefix` is used unchanged and must not be widened to a colon.** A colon after the vendor's name almost always opens the album title on this store (`Talking Heads: 77`, `Nuggets: Original Artyfacts…`), and widening would truncate those titles.
- **Availability comes from `variant.available`, not the store's tags.** Its `out_of_stock` tag is stale and contradicts the flag on a third of the products carrying it; its `exclude` tag hides nothing.
- **No pre-order availability bypass.** Every live pre-order-tagged vinyl product reports `available: true`; an unavailable product here is gone allocation. A test pins the absence; see the design spec before "fixing" this to match `napalmrecords.py`.
- Multi-variant products (none live among vinyl) append a variant descriptor to the title, falling back to the immutable variant id, and raise when a variant has neither — identity over cosmetics: `stock_items.item_key` is deliberately non-unique, so colliding rows insert fine and then share identity, crawl results, judgments and saved state downstream.
- The vendor drift guard counts **vinyl** products only, in both directions — a vendor-less CD must not fail a healthy walk, and the store's CDs must not vouch for vinyl that has lost its artist source. The stock-flag guard is scoped identically.
- **A guard must cover the availability field itself.** `variants` vanishing, or `available` vanishing from every variant, makes every product yield nothing while the other tallies stay non-zero — a successful empty walk that deletes the snapshot. The guard tests for a literal boolean, not a present key: `available` as the string `"false"` is truthy and would invert the filter rather than merely break it.
- No comments except where the WHY is non-obvious.
- Registration is automatic via `main.py`'s bundled-crawler startup loop — no wiring changes anywhere else.
- Every commit carries the AI-attribution trailer block required by this repo's `CLAUDE.md`, created via `git commit -F <message-file>`, not `-m`.

Full grounding for every rule above: [`docs/specifications/shaping/2026-09-04-rhino-store-crawler-design.md`](../shaping/2026-09-04-rhino-store-crawler-design.md).

**Running the tests.** These tests mock HTTP with `respx` and never reach the store, but the `*_crawler` autouse fixture in `conftest.py` still resolves through the config layer, so run them with the three test env vars set. From `backend/`:

```bash
cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test \
  IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_rhino_crawler.py -v
```

---

### Task 1: Rhino store crawler + tests

**Files:**
- Create: `backend/crawlers/rhino.py`
- Test: `backend/tests/test_rhino_crawler.py`

**Interfaces:**
- Consumes: `shopify_catalog.iter_products(base_url, collection_slug)`, `has_tag(product, tag)`, `strip_vendor_prefix(title, vendor)`, `resolve_cover_image(product, variant)` — all exist unchanged in `backend/shopify_catalog.py`.
- Produces: a `Crawler` class with the standard `catalog` plugin surface (`site_name`, `base_url`, `genre_summary`, `genre`, `crawler_type`, `async def crawl_catalog()`), yielding `{"artist", "title", "format": "Vinyl", "price", "currency": "USD", "url", "cover_image_url"}`.

- [x] **Step 1: Ground the design against the live store** — identify the platform, compare candidate collections by product count and membership, histogram `product_type`, and check `vendor`, `tags`, variant counts, availability, prices and `robots.txt`.
- [x] **Step 2: Write the crawler** — `all` collection, `Vinyl`-family + `Boxset - Vinyl Only` gate, vendor artist, exact-case vendor-prefix strip, no pre-order bypass, guarded price parse, three drift guards, multi-variant disambiguation chain.
- [x] **Step 3: Write the test file** — fixtures distinguish captured / altered / invented provenance, each marked at its definition; cases per the design spec's Verification section.
- [x] **Step 4: Replay over the fully-cached live catalog** — 1,522 products walked → 840 pass the gate → 663 rows, no `item_key` collisions, no blank artist or title, no whitespace contamination, no malformed URL, no missing cover, no null price.
- [x] **Step 5: Run the test file** — all tests in it pass.
- [x] **Step 6: Mutation-check that each guard bites** — mutate the crawler once per guard and confirm only the intended tests fail; fix any test that passes under its own mutation, and treat a test that fails for the wrong reason as a finding about the code rather than the test.
- [x] **Step 7: Run the wider crawler test selection for regressions** (`pytest tests/ -k crawler` with the three test env vars set — the plugin loader imports every module in `backend/crawlers/`, so a syntax error in the new file breaks unrelated tests). Compare against a stashed baseline.
- [x] **Step 8: Commit** via `git commit -F`, with trailers.

---

## Post-implementation: pre-PR spec-drift check

- [x] **Grep both spec trees** (`docs/superpowers/specs/`, `docs/specifications/shaping/`) for the files, symbols, and names this diff touches.
- [x] **Delete any crawler/store/source/plugin/test count** found in a spec visited during the check, rather than updating it.
- [x] **Record findings in the PR description** (drift found and fixed, or none).
