# Byrdland Records Store Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking. (This plan was executed inline in the session that wrote it — that session had no `superpowers` skill available — and is recorded here as the historical task log the plans tree is for.)

**Goal:** Add `backend/crawlers/byrdlandrecords.py`, a `crawler_type="catalog"` plugin covering Byrdland Records' Lightspeed eCom storefront (`shop.byrdlandrecords.com`) `Vinyl` category.

**Architecture:** Walk `/vinyl/pageN.html?format=json&limit=100` with plain `httpx` through `catalog_http.get_with_retry()`. Lightspeed is a new platform for this repo — no `shopify_catalog` helper applies, so the plugin carries its own paging loop. Pagination is path-based and the `?page=` querystring is silently ignored; paging past the last page silently wraps to page 1. Bound the loop by the `pages` the first response reports and assert every response echoes the page requested. Derive `shop.id` and `shop.currency` from the payload. Split artist from album on the title's first spaced `[-–—]` after collapsing whitespace, skipping any title without one — `brand` is `false` on every product, so there is no fallback artist source. Drop CDs and cassettes with a negative format filter whose vinyl override cannot be satisfied by the store's own `CD NOT VINYL` annotation. Raise on an empty first page, on a page-echo mismatch, and on a zero-row walk, so drift can never wipe the snapshot through `replace_stock_items()`.

**Tech Stack:** Python ≥3.9, `httpx` (via `catalog_http.get_with_retry`), `pytest`/`pytest-asyncio`, `respx` for HTTP mocking.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`; use `Optional[str]` or leave untyped.
- No new shared module. Lightspeed's JSON view has nothing in common with `shopify_catalog.iter_products()`'s Shopify contract; a second platform is not yet a pattern to extract.
- `format` is hardcoded `"Vinyl"`. `currency` and the CDN shop id are read from each response's `shop` block — **do not hardcode either**, both are echoed on every page.
- **Target `shop.byrdlandrecords.com`, not `byrdlandrecords.com`.** The apex host is a GoDaddy brochure site with no products; the design spec's Problem section records why.
- **Pagination is path-based (`/vinyl/pageN.html`).** The `?page=` querystring is silently ignored — it answers page 1 with HTTP 200. Never "simplify" this into a querystring pager; `_assert_page_echo` exists to make that regression raise.
- **Never loop until a page comes back empty.** Paging past the end wraps to page 1 with a full payload, so an emptiness-terminated loop never ends. Bound by the reported `pages`.
- **`limit=100` is the ceiling.** Larger values are ignored, not clamped, and fall back to a page of 12.
- **Walk `/vinyl/` only.** It is a strict superset of the genre tree beneath it (proved live by intersecting the largest leaf's ids). Do not union subcategories, and do not trust the category tree's own `count` field — it disagrees with the listing and the sitemap.
- **The vinyl override must not be satisfiable by `CD NOT VINYL`.** The store negates the category to mark its mis-filed CDs; a plain override reads that as proof of vinyl and republishes 16 live CDs as records. A test pins this.
- **No `tapes?`/`book`/`magazine` in the format filter.** This store fuses format into the title, so the pattern reads against album and artist names; `Tape 1/Tape 2`, `Book of Paul` and `Peel Dream Magazine` are all live and all records.
- **No unspaced-hyphen fallback and no colon separator.** Both mangle live titles; skipping beats guessing. See the design spec.
- No comments except where the WHY is non-obvious.
- Registration is automatic via `main.py`'s `seed_bundled_crawlers()` — no wiring changes anywhere else, no schema change.
- Every commit carries the AI-attribution trailer block required by this repo's `CLAUDE.md`, created via `git commit -F <message-file>`, not `-m`.

Full grounding for every rule above: [`docs/specifications/shaping/2026-09-02-byrdland-records-crawler-design.md`](../shaping/2026-09-02-byrdland-records-crawler-design.md).

**Running the tests.** These tests mock HTTP with `respx` and need no browser, but `tmp_config_dir` depends on `pg_test_db`, so the three database env vars must be set. Run from `backend/`:

```bash
cd backend && TEST_DATABASE_URL=... IDENTITY_DB_PASSWORD=... APP_DB_PASSWORD=... \
  pytest tests/test_byrdlandrecords_crawler.py -v
```

---

### Task 1: Byrdland Records crawler + tests

**Files:**
- Create: `backend/crawlers/byrdlandrecords.py`
- Test: `backend/tests/test_byrdlandrecords_crawler.py`

**Interfaces:**
- Consumes: `catalog_http.get_with_retry(client, url, *, delay, failure_limit, params)`, `config.load_config()`, `crawl_progress.report_page(page, count)` — all exist unchanged.
- Produces: a `Crawler` class with the standard `catalog` plugin surface (`site_name`, `base_url`, `genre_summary`, `genre`, `crawler_type`, `async def crawl_catalog()`), yielding `{"artist", "title", "format": "Vinyl", "price", "currency", "url", "cover_image_url"}`.

- [x] **Step 1: Establish the platform and the paging contract live** — confirm the shop host, the `?format=json` view, the path-based pager, the past-the-end wrap, the `limit` ceiling, and that `/vinyl/` is a superset of the genre tree.
- [x] **Step 2: Write the crawler** — bounded paging loop with page-echo assertion and cross-page de-duplication, whitespace-collapsing title split, format filter with the negated-annotation guard, payload-derived shop id/currency, three drift guards.
- [x] **Step 3: Write the test file** — fixtures marked CAPTURED / ALTERED at their definition; cases per the design spec's Testing section.
- [x] **Step 4: Replay over the fully-cached live catalog** — 3,312 products → 3,125 rows, no `(artist, title, url)` collisions, no blank artist or title, no whitespace contamination, no malformed URL, one intentional vinyl-plus-DVD row still naming a competing format.
- [x] **Step 5: Run the test file** — all tests in it pass.
- [x] **Step 6: Run the wider crawler test selection for regressions** (`pytest tests/ -k crawler` with the three env vars set — the plugin loader imports every module in `backend/crawlers/`, so a syntax error in the new file breaks unrelated tests). Compare against a stashed baseline; this repo's `-k crawler` selection has pre-existing Playwright-fixture errors and one pre-existing order-dependent failure in `test_queue_router.py`.
- [x] **Step 7: Commit** via `git commit -F`, with trailers.

---

## Post-implementation: pre-PR spec-drift check

- [x] **Grep both spec trees** (`docs/superpowers/specs/`, `docs/specifications/shaping/`) for the files, symbols, and names this diff touches.
- [x] **`2026-08-07-shared-title-split-helper-design.md`** — amendment added: `byrdlandrecords.py` is a further exception to the converging-contract premise, and the first from outside Shopify.
- [x] **Commit the spec amendments as their own commit; note the drift findings in the PR description.**
