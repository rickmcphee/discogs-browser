# uDiscover Music Hard Rock & Heavy Metal Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking. (This plan was executed inline in the session that wrote it — recorded here as the historical task log the plans tree is for.)

**Goal:** Add `backend/crawlers/udiscovermusic.py`, a `crawler_type="catalog"` Shopify plugin covering uDiscover Music's (`shop.udiscovermusic.com`) `hard-rock-heavy-metal` collection.

**Architecture:** Iterate the store's `hard-rock-heavy-metal` collection via the existing `shopify_catalog.iter_products()` helper. The collection is mixed-format (CDs are its largest type), so a `product_type` gate — accept `^\d*LP$` and `^\d+in$`, case-insensitive — does the vinyl scoping the sibling stores get from a `vinyl` collection slug. Set `artist` to `vendor` unconditionally (always the artist on this store's vinyl, in clean mixed case), keep the title as-is with the shared exact-case `strip_vendor_prefix` as a drift guard (zero live titles carry the prefix; self-titled shapes must not be stripped). Skip unavailable variants with **no** pre-order bypass; suffix ` (Pre-Order)` on the `pre-order` tag. Raise on an empty collection and on a collection with no vendors, so drift can never wipe the previous snapshot through `replace_stock_items()`.

**Tech Stack:** Python ≥3.9, `httpx` (via `shopify_catalog.iter_products`), `pytest`/`pytest-asyncio`, `respx` for HTTP mocking.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`; use `Optional[str]` or leave untyped.
- No new shared module — reuse `shopify_catalog.iter_products()`, `has_tag()`, `strip_vendor_prefix()`, and `resolve_cover_image()` unchanged.
- `format` is hardcoded `"Vinyl"`; `currency` is hardcoded `"USD"` (confirmed via the page's `Shopify.currency`).
- `_COLLECTION_SLUG = "hard-rock-heavy-metal"`. The vinyl scoping is the `product_type` gate, not a collection slug — do not swap to the store's all-vinyl collection, which spans the whole Universal roster.
- **`artist` is `product["vendor"]`, always; a blank vendor skips the product.** Never split the title.
- **No pre-order availability bypass.** All 13 live pre-order-tagged vinyl products report `available: true`; an unavailable product here is gone allocation. A test pins the absence; see the design spec before "fixing" this to match `napalmrecords.py`.
- **No title-level format filter.** `product_type` is clean and specific on this store (confirmed live); the gate is the whole filter. Box sets are typed `Box Set (…)` regardless of media and stay excluded — accepted scope loss, see the design spec's Non-goals.
- Multi-variant products (none live among vinyl) append a variant descriptor to the title, falling back to the immutable variant id, and raise when a variant has neither — identity over cosmetics: `stock_items.item_key` is deliberately non-unique, so colliding rows insert fine and then share identity, crawl results, judgments, and saved state downstream.
- No comments except where the WHY is non-obvious.
- Registration is automatic via `main.py`'s bundled-crawler startup loop — no wiring changes anywhere else.
- Every commit carries the AI-attribution trailer block required by this repo's `CLAUDE.md`, created via `git commit -F <message-file>`, not `-m`.

Full grounding for every rule above: [`docs/specifications/shaping/2026-09-01-udiscover-hard-rock-crawler-design.md`](../shaping/2026-09-01-udiscover-hard-rock-crawler-design.md).

**Running the tests.** These tests need no database — they mock HTTP with `respx` and never touch Postgres. Run from `backend/`:

```bash
cd backend && pytest tests/test_udiscovermusic_crawler.py -v
```

---

### Task 1: uDiscover Music crawler + tests

**Files:**
- Create: `backend/crawlers/udiscovermusic.py`
- Test: `backend/tests/test_udiscovermusic_crawler.py`

**Interfaces:**
- Consumes: `shopify_catalog.iter_products(base_url, collection_slug)`, `has_tag(product, tag)`, `strip_vendor_prefix(title, vendor)`, `resolve_cover_image(product, variant)` — all exist unchanged in `backend/shopify_catalog.py`.
- Produces: a `Crawler` class with the standard `catalog` plugin surface (`site_name`, `base_url`, `genre_summary`, `genre`, `crawler_type`, `async def crawl_catalog()`), yielding `{"artist", "title", "format": "Vinyl", "price", "currency": "USD", "url", "cover_image_url"}`.

- [x] **Step 1: Write the test file** — fixtures distinguish captured / altered / invented provenance, each marked at its definition; cases per the design spec's Testing section.
- [x] **Step 2: Write the crawler** — `product_type` vinyl gate, vendor-prefix guard, no pre-order bypass, drift guards, multi-variant disambiguation chain.
- [x] **Step 3: Replay over the fully-cached live catalog** — 710 products → 303 pass the gate → 296 rows, no (artist, title, url) collisions, no missing price or cover, no blank artist, self-titled titles intact.
- [x] **Step 4: Run the test file** — all tests in it pass.
- [x] **Step 5: Run the wider crawler test selection for regressions** (`pytest tests/ -k crawler` with the three test env vars set — the plugin loader imports every module in `backend/crawlers/`, so a syntax error in the new file breaks unrelated tests).
- [x] **Step 6: Commit** via `git commit -F`, with trailers.

---

## Post-implementation: pre-PR spec-drift check

- [x] **Grep both spec trees** (`docs/superpowers/specs/`, `docs/specifications/shaping/`) for the files, symbols, and names this diff touches.
- [x] **Record findings in the PR description** (drift found and fixed, or none).
