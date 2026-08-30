# Hammerheart Records Store Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking. (This plan was executed inline in the session that wrote it — recorded here as the historical task log the plans tree is for.)

**Goal:** Add `backend/crawlers/hammerheart.py`, a `crawler_type="catalog"` Shopify plugin covering Hammerheart Records' US webstore (`hammerheart.indiemerch.com`) `vinyl` collection.

**Architecture:** Iterate the store's `vinyl` collection via the existing `shopify_catalog.iter_products()` helper — confirmed live to be exactly the store's vinyl-typed inventory. Set `artist` to `vendor` unconditionally (always the artist on this store, in clean mixed case) and strip the artist back off the front of the title with a local case-insensitive regex, because the store re-writes it there in ALL CAPS and `strip_vendor_prefix`'s exact-case match misses the bulk of the titles. Drop products whose title names a non-vinyl format with no vinyl signal (two live CDs are mistyped `12"`). Skip unavailable variants with **no** pre-order bypass; suffix ` (Pre-Order)` on the `preorder` tag. Raise on an empty collection and on a collection with no vendors, so drift can never wipe the previous snapshot through `replace_stock_items()`.

**Tech Stack:** Python ≥3.9, `httpx` (via `shopify_catalog.iter_products`), `pytest`/`pytest-asyncio`, `respx` for HTTP mocking.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`; use `Optional[str]` or leave untyped.
- No new shared module — reuse `shopify_catalog.iter_products()`, `has_tag()`, and `resolve_cover_image()` unchanged. This crawler does **not** use `strip_vendor_prefix()`: its local strip is case-insensitive with a `[-/]` separator class, and the design spec (and the shared-title-split doc's ninth amendment) record why that stays local rather than widening the shared helper.
- `format` is hardcoded `"Vinyl"`; `currency` is hardcoded `"USD"` (confirmed via the page's `Shopify.currency`).
- `_COLLECTION_SLUG = "vinyl"`. Do not union narrower collections — the design spec's "Collection choice" section records that `vinyl` equals the store's vinyl-typed inventory exactly.
- **`artist` is `product["vendor"]`, always; a blank vendor skips the product.** Never split the title — the artist prefix that appears there is redundant with `vendor` and is stripped, not parsed.
- **No pre-order availability bypass.** This store flags purchasable pre-orders available (23/24 live), and its one unavailable pre-order renders "Sold Out" on its own page. A test pins the absence; see the design spec before "fixing" this to match `napalmrecords.py`.
- **The format filter's counted forms (`\d*x?`) stay on both sides.** A bare `\bcds?\b` cannot see the CD in `2xCD` — the `spv.py`/`onetwothreefourgo.py` regression.
- Multi-variant products (none live) append a variant descriptor to the title, falling back to the immutable variant id, and raise when a variant has neither — identity over cosmetics, because `replace_stock_items()` INSERTs with no ON CONFLICT guard.
- No comments except where the WHY is non-obvious.
- Registration is automatic via `main.py`'s `seed_bundled_crawlers()` — no wiring changes anywhere else.
- Every commit carries the AI-attribution trailer block required by this repo's `CLAUDE.md`, created via `git commit -F <message-file>`, not `-m`.

Full grounding for every rule above: [`docs/specifications/shaping/2026-08-30-hammerheart-records-crawler-design.md`](../shaping/2026-08-30-hammerheart-records-crawler-design.md).

**Running the tests.** These tests need no database — they mock HTTP with `respx` and never touch Postgres. Run from `backend/`:

```bash
cd backend && pytest tests/test_hammerheart_crawler.py -v
```

---

### Task 1: Hammerheart Records crawler + tests

**Files:**
- Create: `backend/crawlers/hammerheart.py`
- Test: `backend/tests/test_hammerheart_crawler.py`

**Interfaces:**
- Consumes: `shopify_catalog.iter_products(base_url, collection_slug)`, `has_tag(product, tag)`, `resolve_cover_image(product, variant)` — all exist unchanged in `backend/shopify_catalog.py`.
- Produces: a `Crawler` class with the standard `catalog` plugin surface (`site_name`, `base_url`, `genre_summary`, `genre`, `crawler_type`, `async def crawl_catalog()`), yielding `{"artist", "title", "format": "Vinyl", "price", "currency": "USD", "url", "cover_image_url"}`.

- [x] **Step 1: Write the test file** — fixtures distinguish captured / altered / invented provenance, each marked at its definition; cases per the design spec's Testing section.
- [x] **Step 2: Write the crawler** — case-insensitive prefix strip, format filter with vinyl override, no pre-order bypass, drift guards, multi-variant disambiguation chain.
- [x] **Step 3: Replay over the fully-cached live catalog** — 447 products → 307 rows, no (artist, title, url) collisions, no missing price or cover, no blank artist, no artist left leading a title, both mistyped CDs excluded, self-titled `Abramelin (Black vinyl)` intact.
- [x] **Step 4: Run the test file** — all tests in it pass.
- [x] **Step 5: Run the wider crawler test selection for regressions** (`pytest tests/ -k crawler` with the three test env vars set — the plugin loader imports every module in `backend/crawlers/`, so a syntax error in the new file breaks unrelated tests).
- [x] **Step 6: Commit** via `git commit -F`, with trailers.

---

## Post-implementation: pre-PR spec-drift check

- [x] **Grep both spec trees** (`docs/superpowers/specs/`, `docs/specifications/shaping/`) for the files, symbols, and names this diff touches.
- [x] **`2026-08-07-shared-title-split-helper-design.md`** — ninth amendment added: `hammerheart.py` is not a split exception (it never splits; vendor is trusted), but it diverges from `strip_vendor_prefix`'s exact-case contract with a local case-insensitive strip, and the amendment records why that stays local. Also deleted an inventory count from that doc's Non-goals section per `CLAUDE.md`'s count rule, rather than updating it.
- [x] **`2026-07-05-in-stock-crawler-design.md`** — no amendment: its 2026-08-23 amendment already declares its enumerative source lists a historical snapshot.
- [x] **Commit the spec amendments as their own commit; note the drift findings in the PR description.**
