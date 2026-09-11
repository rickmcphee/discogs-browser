# Monorail Music Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking. (This plan was executed inline in the session that wrote it — recorded here as the historical task log the plans tree is for.)

**Goal:** Add `backend/crawlers/monorailmusic.py`, a `crawler_type="catalog"` Shopify plugin covering Monorail Music, the independent record shop in Glasgow (`monorailmusic.com`).

**Architecture:** Walk the store's `all` collection via the existing `shopify_catalog.iter_products()` helper — the store publishes no vinyl shelf, so the format scoping is done in the crawler rather than chosen by URL. Scoping is two gates. The first reads `product_type` and admits the named `Music` kind, which keeps out films, books, merchandise and — the case that actually matters — the in-store album launches that bundle a record with an event ticket at a price that is neither, and that carry an *empty* product_type rather than `Events`. The second reads the variant, because a product here is a release and its variants are the formats it was released in, so a record and its CD share one product. That gate is positive, shape-based rather than a list of literals, and preceded by a dimension guard that deletes `10" X 10"`-shaped runs so a CD's bonus poster is not read as a 10-inch single. The artist comes from the title's `Artist - Album` split, refused when the dash sits between two numbers, and falls back to the product's *sole* tag — `vendor` is the label here, never the act. The row's title is the album with the pressing appended, always, so two pressings of one release never collide on `item_key`. Only the literal `True` admits a variant; pre-orders are stock. Raise on an empty walk, on a catalog with no `Music` kind, on unreadable variants, on no vinyl format anywhere, on rows that all lack a price, and on a walk that yielded nothing while any record had lost its artist, its identity or its availability flag — so drift can never wipe the previous snapshot through `replace_stock_items()`.

**Tech Stack:** Python ≥3.9, `httpx` (via `shopify_catalog.iter_products`), `pytest`/`pytest-asyncio`, `respx` for HTTP mocking.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`; use `Optional[str]` or leave untyped.
- No new shared module — reuse `shopify_catalog.iter_products()` and `resolve_cover_image()` unchanged. `has_tag` and `strip_vendor_prefix` are not used: the tags are read directly for the artist fallback, and no vendor prefix appears in a title.
- `format` is hardcoded `"Vinyl"`; `currency` is hardcoded `"GBP"` (from the store's `meta.json`, confirmed against a live page rendering `£14.99`).
- **`_COLLECTION_SLUG = "all"`.** There is no vinyl shelf: every genre, staff-pick and event shelf mixes formats, and the two catalog-wide shelves are the same products renamed. The walk returns exactly the `published_products_count` from `meta.json`.
- **The kind gate admits a named type, not a rejected list**, because the album-launch ticket bundles carry an *empty* `product_type` and are the only products outside `Music` whose variants name vinyl.
- **The format gate is per variant and positive** — `all` is the whole shop, so anything not proven a record is not one. That is the opposite of `sacredbonesrecords.py`'s negative gate, and the difference is the shelf, not the taste.
- **The gate matches shapes, not literals**: `vinyl` as a substring (for `biovinyl`), the store's `vinly` misspelling, `\d*[x×]?d?lps?\d?` (for `2LP`, `2xLP`, `DLP`, `LP2`, none of which `\blps?\b` sees), sizes `7`/`10`/`12` before an inch mark with an optional count, `VL`, `picture disc`, `flexi`.
- **No non-vinyl word list.** A positive gate does not need one, and leaving it out is what admits a bundle (`LP + BONUS CD`) as the record it is.
- **The dimension guard runs before the inch marker** — the one live false positive is a CD whose bonus poster is `10" X 10"`. Same move as `byrdlandrecords.py` deleting `not vinyl` before its override reads the `vinyl` inside it.
- **Colour-only pressings are dropped, deliberately.** A colour is not a format claim, and colour names a CD edition too.
- **The artist is the title's first-dash split, then the sole tag.** `vendor` is the label. The title is primary because Shopify comma-splits tags, so a comma-containing act arrives as fragments.
- **The tag fallback requires EXACTLY one tag.** Tags are alphabetically sorted and mix the act with genre words; no single-tag product in the catalog carries a genre word, and two tags may equally be one split name or two collaborators.
- **The split is refused when the dash sits between two numbers**, and refused rather than repaired — that is what hands the three live year-range titles to the tag fallback, which is right for all three.
- **The pressing is appended to every row's title, after the album.** Every variant of a product shares the artist and URL, so the pressing is what keeps `item_key` distinct; appending unconditionally is what keeps it stable when a sibling sells out; appending *after* the album is what keeps `db._library_release_match_sql`'s exact-or-prefix-with-space test matching.
- **Availability comes from `variant.available` and only the literal `True` admits a row.** No pre-order handling: the store's pre-orders report `True` and their only signal is a separate collection this payload does not carry.
- **Readability is judged over the admitted variants only**, with `all()`, not `any()`.
- **`variant-identity` is guarded before `format-taxonomy`** — a product whose variants cannot be read has no admitted pressing either, so the format guard would otherwise answer every variants-level failure with the wrong diagnosis.
- **Tallies are nested inside `if pressings:`**, so a CD-only product cannot vouch for a catalog of artist-less records.
- **`price-source drift` fires only when no row at all carries a price**; isolated nulls stay tolerated.
- No comments except where the WHY is non-obvious.
- Registration is automatic via `main.py`'s bundled-crawler startup loop — no wiring changes anywhere else.
- Every commit carries the AI-attribution trailer block required by this repo's `CLAUDE.md`, created via `git commit -F <message-file>`, not `-m`.

Full grounding for every rule above: [`docs/specifications/shaping/2026-09-09-monorail-music-crawler-design.md`](../shaping/2026-09-09-monorail-music-crawler-design.md).

**Running the tests.** These tests mock HTTP with `respx` and never reach the store, but the `*_crawler` autouse fixture in `conftest.py` still resolves through the config layer, so run them with the three test env vars set. From `backend/`:

```bash
cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test \
  IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_monorailmusic_crawler.py -v
```

---

### Task 1: Monorail Music crawler + tests

**Files:**
- Create: `backend/crawlers/monorailmusic.py`
- Test: `backend/tests/test_monorailmusic_crawler.py`

**Interfaces:**
- Consumes: `shopify_catalog.iter_products(base_url, collection_slug)`, `resolve_cover_image(product, variant)` — both exist unchanged in `backend/shopify_catalog.py`.
- Produces: a `Crawler` class with the standard `catalog` plugin surface (`site_name`, `base_url`, `genre_summary`, `genre`, `crawler_type`, `async def crawl_catalog()`), yielding `{"artist", "title", "format": "Vinyl", "price", "currency": "GBP", "url", "cover_image_url"}`.

- [x] **Step 1: Ground the design against the live store** — identify the platform, walk the whole catalog and reconcile it against `meta.json` and `collections.json`, histogram `product_type`, `vendor`, the tags and the option axes, tabulate every variant format string, measure the title/tag agreement over a control group, look for a pre-order signal, and verify availability, prices, currency and images against live product pages.
- [x] **Step 2: Write the crawler** — the `all` collection; the named-kind gate; the positive per-variant format gate with its dimension guard; title-then-sole-tag artist with the number-range refusal; always-appended pressing; no pre-order handling; guarded price parse; nested drift guards.
- [x] **Step 3: Write the test file** — fixtures marked `captured` or `invented` at their definition; cases per the design's Verification section.
- [x] **Step 4: Replay over the fully cached live catalog** — 5,765 products walked → 2,862 rows across 1,969 artists, no `item_key` collisions, no blank artist or title, no null price, no malformed URL, and no row whose pressing names a non-vinyl medium.
- [x] **Step 5: Run the test file** — all tests in it pass.
- [x] **Step 6: Mutation-check that each guard and rule bites** — mutate the crawler once per guard or rule and confirm the tests fail. Two mutations survived the first pass (keeping non-mapping variant entries, and admitting a blank variant title); tests were added for both, after which every mutation was caught. One real bug was found this way and fixed: `format-taxonomy drift` was answering variants-level failures, so the two guards were reordered.
- [x] **Step 7: Run the wider crawler test selection for regressions** (`pytest tests/ -k crawler` with the three test env vars set — the plugin loader imports every module in `backend/crawlers/`, so a syntax error in the new file breaks unrelated tests). Confirmed the pre-existing Playwright-driven errors and the one order-dependent `test_queue_router` failure reproduce identically with the new files removed.
- [x] **Step 8: Pre-PR spec-drift check** — grep both spec trees for the files, symbols and strings the diff touches; amend anything that has drifted; strip any inventory count found along the way.
- [x] **Step 9: Commit** via `git commit -F`, with trailers.
