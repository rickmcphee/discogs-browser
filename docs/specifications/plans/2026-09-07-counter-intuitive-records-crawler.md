# Counter Intuitive Records Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking. (This plan was executed inline in the session that wrote it — recorded here as the historical task log the plans tree is for.)

**Goal:** Add `backend/crawlers/counterintuitiverecords.py`, a `crawler_type="catalog"` Shopify plugin covering Counter Intuitive Records' webstore (`counterintuitiverecords.com`), at the `all` collection the request named.

**Architecture:** Walk the store's `all` collection via the existing `shopify_catalog.iter_products()` helper. Format scoping is a two-layer gate: `product_type` enumerates the three vinyl values positively, then a negative variant gate drops the CD and cassette siblings, checking a vinyl word *before* another medium word so a live `AB Dark Blue / CD Light Blue 2xLP` stays a record. The artist comes from the title's `Artist - Album` split, with `vendor` as a fallback that is good on this store but never preferred, because it holds only the primary artist on splits. The variant name is appended to every row that has one; the `Default Title` placeholder carries the album alone, as a product's sole variant only. Skip unavailable variants with **no** pre-order bypass and **no** ` (Pre-Order)` marker. Raise on an empty collection, on a catalog with no vinyl type, no parseable artist, or no record variant, on a walk that yielded no rows while a record had no identity or no readable flag, and on a walk that yielded rows but no prices — so drift can never wipe the previous snapshot through `replace_stock_items()`.

**Tech Stack:** Python ≥3.9, `httpx` (via `shopify_catalog.iter_products`), `pytest`/`pytest-asyncio`, `respx` for HTTP mocking.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`; use `Optional[str]` or leave untyped.
- No new shared module — reuse `shopify_catalog.iter_products()` and `resolve_cover_image()` unchanged. `has_tag` and `strip_vendor_prefix` are not used: no tag is read, and no vendor prefix appears in a title.
- `format` is hardcoded `"Vinyl"`; `currency` is hardcoded `"USD"` (confirmed via the store's `meta.json`).
- **`_COLLECTION_SLUG = "all"`.** It is what the request named and the only complete shelf: `vinyl-shop` misses every `Distro Vinyl` and `Vinyl/CD` product, and no product on any vinyl shelf is absent from `all`.
- **The `product_type` gate enumerates `Vinyl`, `Distro Vinyl` and `Vinyl/CD` positively**, so a type the store adds later stays out by default. The `cf-type-*` tags are not a fallback — they are derived from `product_type` and would drift with it.
- **The variant gate is negative and its check order is load-bearing:** vinyl word, then inch marker, then another-medium rejection, then admit. Medium-first drops the live `AB Dark Blue / CD Light Blue 2xLP`.
- **The medium rejection matches on word boundaries, not an anchored exact string** — the store names cassettes by colour (`Pink Tape`, `Yellow Cassette`).
- **The artist comes from the title's dash split, using `\s+-\s*|\s*-\s+`, with `vendor` only as a fallback.**
- **A split's billing is reduced to the first-billed artist**, because `discogs.parse_release` stores `artists[0]` and `db._library_release_match_sql` compares artists with exact equality — a joined billing can never match a library release. The slash requires whitespace on at least one side, so `AC/DC` is not clipped; the reduction reads the artist segment only, so a slash in the album is untouched.
- **The row's title is `album + " — " + variant`**, whitespace-collapsed, with the variant appended on every row that names one so `compute_item_key` stays stable.
- **The `Default Title` placeholder carries the album alone, and only as a product's sole variant.**
- **Availability comes from `variant.available` and only the literal `True` admits a row; no pre-order bypass, and no marker is written** — the store's pre-order tag is dated, so a marker driven off it would re-key the row when the record shipped.
- **Readability is judged over the admitted variants only**, with `all()`, not `any()`.
- **Tallies are nested**, variant gate → identity/readable, so a non-zero count means "some product would have yielded a row if it were in stock". The artist tally sits *outside* the gate, so an all-CD catalog does not raise `artist-source drift`.
- **`price-source drift` fires only when no row at all carries a price**; isolated nulls stay tolerated.
- No comments except where the WHY is non-obvious.
- Registration is automatic via `main.py`'s bundled-crawler startup loop — no wiring changes anywhere else.
- Every commit carries the AI-attribution trailer block required by this repo's `CLAUDE.md`, created via `git commit -F <message-file>`, not `-m`.

Full grounding for every rule above: [`docs/specifications/shaping/2026-09-07-counter-intuitive-records-crawler-design.md`](../shaping/2026-09-07-counter-intuitive-records-crawler-design.md).

**Running the tests.** These tests mock HTTP with `respx` and never reach the store, but the `*_crawler` autouse fixture in `conftest.py` still resolves through the config layer, so run them with the three test env vars set. From `backend/`:

```bash
cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test \
  IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_counterintuitiverecords_crawler.py -v
```

---

### Task 1: Counter Intuitive Records crawler + tests

**Files:**
- Create: `backend/crawlers/counterintuitiverecords.py`
- Test: `backend/tests/test_counterintuitiverecords_crawler.py`

**Interfaces:**
- Consumes: `shopify_catalog.iter_products(base_url, collection_slug)`, `resolve_cover_image(product, variant)` — both exist unchanged in `backend/shopify_catalog.py`.
- Produces: a `Crawler` class with the standard `catalog` plugin surface (`site_name`, `base_url`, `genre_summary`, `genre`, `crawler_type`, `async def crawl_catalog()`), yielding `{"artist", "title", "format": "Vinyl", "price", "currency": "USD", "url", "cover_image_url"}`.

- [x] **Step 1: Ground the design against the live store** — identify the platform, walk the collection at two page sizes to confirm pagination is stable, histogram `vendor`, `product_type` and the tags, tabulate every variant title, test the title convention against the whole catalog, compare `all` against the store's three vinyl shelves, find the pre-order signal and check what an unavailable pre-order means, check availability, images, prices, `meta.json`, `collections.json` and `robots.txt`.
- [x] **Step 2: Write the crawler** — the `all` collection; positive `product_type` gate; negative variant gate with vinyl-word-first ordering; dash-split artist with vendor fallback; album-then-variant title composition; always-appended variant name with the placeholder exception; no pre-order marker and no pre-order bypass; guarded price parse; nested drift guards.
- [x] **Step 3: Write the test file** — fixtures distinguish captured / altered / invented provenance, each marked at its definition; cases per the design spec's Verification section.
- [x] **Step 4: Replay over the fully-cached live catalog** — 177 products walked → 111 vinyl-typed → 167 rows across 57 artists, no `item_key` collisions, no blank artist or title, no malformed URL, no missing cover, no null price.
- [x] **Step 5: Run the test file** — all tests in it pass.
- [x] **Step 6: Mutation-check that each guard and rule bites** — mutate the crawler once per guard or rule and confirm the tests fail. Every mutation was caught.
- [x] **Step 7: Run the wider crawler test selection for regressions** (`pytest tests/ -k crawler` with the three test env vars set — the plugin loader imports every module in `backend/crawlers/`, so a syntax error in the new file breaks unrelated tests). Confirmed the one failure and the Playwright-binary errors in that selection are pre-existing, by re-running it with the two new files removed.
- [x] **Step 8: Commit** via `git commit -F`, with trailers.
- [x] **Step 9: Address Copilot's PR review** — reduce a split's billing to the first-billed artist (a real matchability bug, verified against `discogs.parse_release` and `db._library_release_match_sql`), and name the title as well as the handle in the identity guard's message. Both re-tested, re-replayed over the cached catalog, and mutation-checked.

---

## Post-implementation: pre-PR spec-drift check

- [x] **Grep both spec trees** (`docs/superpowers/specs/`, `docs/specifications/shaping/`) for the files, symbols, and names this diff touches.
- [x] **Delete any crawler/store/source/plugin/test count** found in a spec visited during the check, rather than updating it.
- [x] **Record findings in the PR description** (drift found and fixed, or none).
