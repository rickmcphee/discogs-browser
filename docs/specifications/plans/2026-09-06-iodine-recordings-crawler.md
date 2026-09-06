# Iodine Recordings Store Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking. (This plan was executed inline in the session that wrote it — recorded here as the historical task log the plans tree is for.)

**Goal:** Add `backend/crawlers/iodinerecords.py`, a `crawler_type="catalog"` Shopify plugin covering Iodine Recordings' store (`iodinerecords.com`).

**Architecture:** Walk Shopify's built-in `all` collection via the existing `shopify_catalog.iter_products()` helper. Gate on `product_type` for `Records` — the type never says the format here — and read the format in two layers: a **positive product layer** on the tags (`Vinyl`, or a `format:` tag whose value reads as vinyl) and a **negative variant layer** on the variant title (a pressing unless the title names CD, cassette, tape, DVD, digital or merch), because most variant titles are just a colour. Read the artist and album out of the product title's `Artist 'Album' [edition]` convention, finding the quotes structurally (opening after whitespace, closing before whitespace or the end) so the apostrophes both halves carry never delimit; keep the edition on the album. Append the variant title to every row that names a pressing, so a row's identity depends on its own variant and not on a CD sibling being listed; the `Default Title` placeholder carries the album alone. Skip unavailable variants with **no** pre-order bypass; suffix ` (Pre-Order)` on the `preorder` tag. Raise on an empty collection, on a catalog with no `Records` type, on records with no quoted title, on records with no vinyl tag or no admitted variant, on a walk that yielded no rows while a product that could have yielded one had no handle or no readable flag, and on a walk that yielded rows but no prices — so drift can never wipe the previous snapshot through `replace_stock_items()`.

**Tech Stack:** Python ≥3.9, `httpx` (via `shopify_catalog.iter_products`), `pytest`/`pytest-asyncio`, `respx` for HTTP mocking.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`; use `Optional[str]` or leave untyped.
- No new shared module — reuse `shopify_catalog.iter_products()`, `has_tag()` and `resolve_cover_image()` unchanged. `strip_vendor_prefix` is not used: the vendor is never in the title.
- `format` is hardcoded `"Vinyl"`; `currency` is hardcoded `"USD"` (confirmed via the store's `meta.json`).
- **`_COLLECTION_SLUG = "all"`.** The store's `12` and `7` collections are curated shelves holding well under two-thirds of the vinyl-tagged records; `all` agrees with the root `products.json` and `meta.json`'s published count.
- **`product_type` is a music gate, not a format gate.** `Records` holds vinyl, CD and cassette products side by side. Enumerate it; do not match negatively, and do not read the format from it.
- **The artist comes from the product title, never the vendor or the tags.** `vendor` is always a label; the tags list the artist beside the album, the labels and the format tags with nothing marking which is which.
- **The quotes are found structurally.** A quote class mis-parses `Her Head's on Fire 'Am I Not Your Girl?'` and `New Forms 'Nothing's Sacred Anymore'`.
- **The product format layer reads both `Vinyl` and the `format:` tags**, either admitting; three live records carry only the `format:` tag.
- **The variant format layer is negative**, in `spv.py`'s vocabulary and check order (vinyl word, merch, inch marker, non-vinyl media, then admit).
- **The variant title is appended on every row that names a pressing**; the `Default Title` placeholder is the one exception.
- **Availability comes from `variant.available`; no pre-order bypass.** Every live pre-order reports `available: true` on the pressing for sale.
- **Readability is judged over the record variants only**, with every(), not any().
- **Tallies are nested**, type → artist → vinyl variant → readable, so that a non-zero count means "some product would have yielded a row if it were in stock".
- No comments except where the WHY is non-obvious.
- Registration is automatic via `main.py`'s bundled-crawler startup loop — no wiring changes anywhere else.
- Every commit carries the AI-attribution trailer block required by this repo's `CLAUDE.md`, created via `git commit -F <message-file>`, not `-m`.

Full grounding for every rule above: [`docs/specifications/shaping/2026-09-06-iodine-recordings-crawler-design.md`](../shaping/2026-09-06-iodine-recordings-crawler-design.md).

**Running the tests.** These tests mock HTTP with `respx` and never reach the store, but the `*_crawler` autouse fixture in `conftest.py` still resolves through the config layer, so run them with the three test env vars set. From `backend/`:

```bash
cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test \
  IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_iodinerecords_crawler.py -v
```

---

### Task 1: Iodine Recordings store crawler + tests

**Files:**
- Create: `backend/crawlers/iodinerecords.py`
- Test: `backend/tests/test_iodinerecords_crawler.py`

**Interfaces:**
- Consumes: `shopify_catalog.iter_products(base_url, collection_slug)`, `has_tag(product, tag)`, `resolve_cover_image(product, variant)` — all exist unchanged in `backend/shopify_catalog.py`.
- Produces: a `Crawler` class with the standard `catalog` plugin surface (`site_name`, `base_url`, `genre_summary`, `genre`, `crawler_type`, `async def crawl_catalog()`), yielding `{"artist", "title", "format": "Vinyl", "price", "currency": "USD", "url", "cover_image_url"}`.

- [x] **Step 1: Ground the design against the live store** — identify the platform, list the collections, histogram `product_type`, tabulate the tags and every variant title, check `vendor` and the title convention, availability, pre-orders, images, prices, `meta.json` and `robots.txt`.
- [x] **Step 2: Write the crawler** — `all` collection, enumerated type gate, structural title parse with the edition kept on the album, two-layer format gate, always-appended variant title with the placeholder exception, no pre-order bypass, guarded price parse, nested drift guards.
- [x] **Step 3: Write the test file** — fixtures distinguish captured / altered / invented provenance, each marked at its definition; cases per the design spec's Verification section.
- [x] **Step 4: Replay over the fully-cached live catalog** — 164 products walked → 118 records → 117 with an artist → 104 with a vinyl tag and an admitted variant → 129 rows, no `item_key` collisions, no blank artist or title, no whitespace contamination, no malformed URL, no missing cover, no null price.
- [x] **Step 5: Run the test file** — all tests in it pass.
- [x] **Step 6: Mutation-check that each guard and rule bites** — mutate the crawler once per guard or rule and confirm the tests fail.
- [x] **Step 7: Run the wider crawler test selection for regressions** (`pytest tests/ -k crawler` with the three test env vars set — the plugin loader imports every module in `backend/crawlers/`, so a syntax error in the new file breaks unrelated tests).
- [x] **Step 8: Commit** via `git commit -F`, with trailers.

---

## Post-implementation: pre-PR spec-drift check

- [x] **Grep both spec trees** (`docs/superpowers/specs/`, `docs/specifications/shaping/`) for the files, symbols, and names this diff touches.
- [x] **Delete any crawler/store/source/plugin/test count** found in a spec visited during the check, rather than updating it.
- [x] **Record findings in the PR description** (drift found and fixed, or none).
