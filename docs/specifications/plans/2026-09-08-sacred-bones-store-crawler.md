# Sacred Bones Records Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking. (This plan was executed inline in the session that wrote it — recorded here as the historical task log the plans tree is for.)

**Goal:** Add `backend/crawlers/sacredbonesrecords.py`, a `crawler_type="catalog"` Shopify plugin covering Sacred Bones Records' webstore (`www.sacredbonesrecords.com`), at the `vinyl` collection the request named.

**Architecture:** Walk the store's `vinyl` collection via the existing `shopify_catalog.iter_products()` helper. The payload is tidy — `vendor` is the artist on every product and the title is the album alone — so identity needs no parsing. What the design turns on is that a product here is a *release* rather than a pressing: its variants are the formats it came out in, so the vinyl shelf carries the CD, cassette, 8-track, Blu-Ray and digital variants of every record on it. The medium is therefore decided per variant, on the variant title, with a **negative** gate: a vinyl word admits outright, a word naming another medium rejects, anything else is admitted on the collection's own claim — because the store names its coloured pressings by colour alone and a positive-only regex would drop nearly every store exclusive. Bundles are deliberately *not* excluded (on this store `Black Vinyl LP + 7"` is the standard pressing, and `+` also joins colours); the charity raffle is, on its tags. Rows are titled `{album} — {pressing}`. Skip unavailable variants with **no** pre-order bypass and **no** ` (Pre-Order)` marker. Raise on an empty collection, on a catalog with no vendor, on a walk that yielded no rows while a record had no identity or no readable flag, and on a walk that yielded rows but no prices — so drift can never wipe the previous snapshot through `replace_stock_items()`.

**Tech Stack:** Python ≥3.9, `httpx` (via `shopify_catalog.iter_products`), `pytest`/`pytest-asyncio`, `respx` for HTTP mocking.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`; use `Optional[str]` or leave untyped.
- No new shared module — reuse `shopify_catalog.iter_products()` and `resolve_cover_image()` unchanged. `strip_vendor_prefix` is deliberately **not** used: the store writes no vendor prefix, and every title that begins with the vendor is a self-titled record. `has_tag` is not used either — the tag check needs its own whitespace-tolerant matching over a set.
- `format` is hardcoded `"Vinyl"`; `currency` is hardcoded `"USD"` (confirmed via the store's `meta.json`).
- **`_COLLECTION_SLUG = "vinyl"`.** `collections.json` reports a `products_count` larger than the shelf's published catalog; `products.json` returns the published products and those are what is walked.
- **The artist is `vendor`, with no fallback to the title.** A product with no vendor is skipped.
- **The row's title is `album — pressing`.** The pressing stays *after* the album so `db._library_release_match_sql`'s prefix test still matches, and is appended on every row that names one.
- **A variant that names nothing** — `Default Title` or blank — carries the album alone, and only as a product's sole variant.
- **The format gate reads the variant title only**, vinyl-word first and other-medium second, defaulting to admit. `cd`/`cs` carry the digit-glued-count lookbehind, or `3xCD` and `2xCS` are not read as other media.
- **Bundles are not excluded**; the raffle is, on its `Raffle`/`Donation` tags.
- **Availability comes from `variant.available`, literal `True` only; no pre-order bypass, and no marker is written.**
- **Readability is judged over the admitted variants only**, with every(), not any().
- **The artist tally sits outside the format gate and the skip**, so an all-CD shelf does not raise `artist-source drift`.
- No comments except where the WHY is non-obvious.
- Registration is automatic via `main.py`'s bundled-crawler startup loop — no wiring changes anywhere else.
- Every commit carries the AI-attribution trailer block required by this repo's `CLAUDE.md`, created via `git commit -F <message-file>`, not `-m`.

Full grounding for every rule above: [`docs/specifications/shaping/2026-09-08-sacred-bones-store-crawler-design.md`](../shaping/2026-09-08-sacred-bones-store-crawler-design.md).

**Running the tests.** These tests mock HTTP with `respx` and never reach the store, but the `*_crawler` autouse fixture in `conftest.py` still resolves through the config layer, so run them with the three test env vars set. From `backend/`:

```bash
cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test \
  IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_sacredbonesrecords_crawler.py -v
```

---

### Task 1: Sacred Bones Records crawler + tests

**Files:**
- Create: `backend/crawlers/sacredbonesrecords.py`
- Test: `backend/tests/test_sacredbonesrecords_crawler.py`

**Interfaces:**
- Consumes: `shopify_catalog.iter_products(base_url, collection_slug)`, `resolve_cover_image(product, variant)` — both exist unchanged in `backend/shopify_catalog.py`.
- Produces: a `Crawler` class with the standard `catalog` plugin surface (`site_name`, `base_url`, `genre_summary`, `genre`, `crawler_type`, `async def crawl_catalog()`), yielding `{"artist", "title", "format": "Vinyl", "price", "currency": "USD", "url", "cover_image_url"}`.

- [x] **Step 1: Ground the design against the live store** — identify the platform, walk the collection at two page sizes to confirm pagination is stable, histogram `vendor` and `product_type`, tabulate the tags, the option axes and every variant title, check the title convention for a vendor prefix or a dash separator, find the pre-order signal and check what an unavailable pre-order means, check availability types, prices, images, `meta.json`, `collections.json` and `robots.txt`.
- [x] **Step 2: Design the format gate against the live data, not by inspection** — classify all 1,306 live variants under a candidate gate, hand-read every rejection and every default-branch admission, and fix what the pass finds (the digit-glued `3xCD`, the two merch strays).
- [x] **Step 3: Write the crawler** — the `vinyl` collection; `vendor` as the artist; `album — pressing` title composition; the raffle skip; the negative per-variant format gate; the nameless-variant rule; no pre-order marker and no pre-order bypass; guarded price parse; drift guards.
- [x] **Step 4: Write the test file** — fixtures distinguish captured / altered / invented provenance, each marked at its definition; cases per the design spec's Verification section.
- [x] **Step 5: Replay over the fully-cached live catalog** — 343 products walked → 330 rows across 94 artists, no `item_key` collisions, no blank artist or title, no malformed URL, no missing cover, no null price, no row whose pressing name mentions another medium.
- [x] **Step 6: Run the test file** — all tests in it pass.
- [x] **Step 7: Mutation-check that each guard and rule bites** — mutate the crawler once per guard, gate branch and composition rule and confirm the tests fail, then confirm the file restores byte-identical.
- [x] **Step 8: Run the wider crawler test selection for regressions** (`pytest tests/ -k crawler` with the three test env vars set — the plugin loader imports every module in `backend/crawlers/`, so a syntax error in the new file breaks unrelated tests), and baseline it without the new files to attribute anything that fails.
- [x] **Step 9: Commit** via `git commit -F`, with trailers.

---

## Post-implementation: pre-PR spec-drift check

- [x] **Grep both spec trees** (`docs/superpowers/specs/`, `docs/specifications/shaping/`) for the files, symbols, and names this diff touches.
- [x] **Delete any crawler/store/source/plugin/test count** found in a spec visited during the check, rather than updating it.
- [x] **Record findings in the PR description** (drift found and fixed, or none).
