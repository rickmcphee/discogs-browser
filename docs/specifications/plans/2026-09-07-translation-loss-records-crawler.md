# Translation Loss Records Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking. (This plan was executed inline in the session that wrote it — recorded here as the historical task log the plans tree is for.)

**Goal:** Add `backend/crawlers/translationloss.py`, a `crawler_type="catalog"` Shopify plugin covering Translation Loss Records' webstore (`translationloss.com`), at the `vinyl` collection the request named.

**Architecture:** Walk the store's `vinyl` collection via the existing `shopify_catalog.iter_products()` helper. Format scoping is a two-layer gate. The first reads `product_type`, which on this store *is* the format (`12"`, `2x12"`, `10"` against `CD`, `2xCD`, `CD/DVD`, `Cassette`, `T-Shirt`, `Kit`) — it admits the shape of a vinyl type (an inch marker or a vinyl word) rather than enumerating today's literals, so a `7"` the label presses next is admitted with no edit. The second reads the title, because the first cannot see a *mistyped* product: a title naming another medium is rejected unless a vinyl word overrides it. The artist is `vendor`, which holds the act's own name on every live product, with no fallback and no title split. The product title is kept verbatim, format token and all, and the pressing name is appended to every row that has one; the `Default Title` placeholder carries the album alone, as a product's sole variant only. Skip unavailable variants; the store has no pre-order signal to bypass or mark. Raise on an empty collection, on a catalog with no vinyl type, no vendor, or no variant naming a pressing, on a walk that yielded rows but no prices, and on a walk that yielded no rows while any single product had lost its vendor, its variants, its identity, or its readability — so drift can never wipe the previous snapshot through `replace_stock_items()`.

**Tech Stack:** Python ≥3.9, `httpx` (via `shopify_catalog.iter_products`), `pytest`/`pytest-asyncio`, `respx` for HTTP mocking.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`; use `Optional[str]` or leave untyped.
- No new shared module — reuse `shopify_catalog.iter_products()` and `resolve_cover_image()` unchanged. `has_tag` and `strip_vendor_prefix` are not used: no tag carries a signal this crawler reads, and no vendor prefix appears in a title.
- `format` is hardcoded `"Vinyl"`; `currency` is hardcoded `"USD"` (confirmed via the store's `meta.json`).
- **`_COLLECTION_SLUG = "vinyl"`.** It is what the request named and it is exactly the vinyl-typed subset of `all` — every vinyl-typed product in `all` is on it, and no product outside it names a record.
- **The `product_type` gate admits an inch marker or a vinyl word**, not a list of literals, because the store's type vocabulary is the format written as a disc count bound to a disc size. The inch marker allows a counted prefix *inside* itself, or `2x12"` is dropped; the same allowance must not admit `2xCD`.
- **Gate even though the shelf is curated as vinyl** — `hammerheart.py` found two CDs mistyped into its store's own `vinyl` collection.
- **Each gate layer has its own tally**, so a guard names the layer that actually rejected the product: a shared counter would report "no vinyl product_type" when the title layer is what emptied the walk.
- **The gate has a second layer, on the title**, because the type layer alone cannot see a *mistyped* product — the very case the hammerheart precedent is about. Same shape as hammerheart's: a title naming another medium is rejected unless a vinyl word overrides it. Vocabulary kept to hammerheart's proven set, because a title here often names no format at all.
- **Every source is tracked by its failures, not only its successes.** `artist_ok`/`pressings_seen` are success-only counters, so one healthy product hides a partial loss of either source from the total-loss guards; each has a matching `*_missing` tally whose guard is gated on an empty outcome.
- **Variants this crawler cannot interpret are counted, not silently dropped**, and the count feeds the stock guard — an available blank-titled variant beside a readable sold-out one would otherwise satisfy every guard while purchasable stock went unpublished. The tally sits *outside* `if pressings`, so a product whose variants are all unreadable cannot hide behind another product's readable pressing.
- **The artist is `vendor`, with no fallback and no title split.** Titles are album-only; the single live title with a dash separator is a split whose `vendor` carries the same billing more completely.
- **A ` & `-joined billing is left joined**, departing from `counterintuitiverecords.py`'s slash reduction: an ampersand is also an ordinary part of a single artist's own name, and nothing in the payload separates the two readings.
- **The product title is kept verbatim** (whitespace collapsed), format token and all. `db._library_release_match_sql` matches a catalog title exactly or as a prefix followed by a space, so a token after the album name never costs a match.
- **The row's title is `title + " — " + variant`**, with the pressing appended on every row that names one so `compute_item_key` stays stable when a sibling sells out.
- **The `Default Title` placeholder carries the album alone, and only as a product's sole variant.**
- **No variant-level format gate** — `product_type` already scopes the format, and no live variant on a vinyl-typed product names another medium.
- **Availability comes from `variant.available` and only the literal `True` admits a row.** No pre-order handling at all: the store's pre-order language lives only in `body_html`, with no tag and no title marker.
- **Readability is judged over the admitted variants only**, with `all()`, not `any()`.
- **Tallies are nested**, type gate → vendor → pressing → identity/readable, so a non-zero count means "some product would have yielded a row if it were in stock". The artist tally sits *inside* the type gate, because the artist source is `vendor` on a vinyl-typed product — un-nested, a CD row's vendor would vouch for a vinyl shelf that had lost its own.
- **`price-source drift` fires only when no row at all carries a price**; isolated nulls stay tolerated.
- No comments except where the WHY is non-obvious.
- Registration is automatic via `main.py`'s bundled-crawler startup loop — no wiring changes anywhere else.
- Every commit carries the AI-attribution trailer block required by this repo's `CLAUDE.md`, created via `git commit -F <message-file>`, not `-m`.

Full grounding for every rule above: [`docs/specifications/shaping/2026-09-07-translation-loss-records-crawler-design.md`](../shaping/2026-09-07-translation-loss-records-crawler-design.md).

**Running the tests.** These tests mock HTTP with `respx` and never reach the store, but the `*_crawler` autouse fixture in `conftest.py` still resolves through the config layer, so run them with the three test env vars set. From `backend/`:

```bash
cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test \
  IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_translationloss_crawler.py -v
```

---

### Task 1: Translation Loss Records crawler + tests

**Files:**
- Create: `backend/crawlers/translationloss.py`
- Test: `backend/tests/test_translationloss_crawler.py`

**Interfaces:**
- Consumes: `shopify_catalog.iter_products(base_url, collection_slug)`, `resolve_cover_image(product, variant)` — both exist unchanged in `backend/shopify_catalog.py`.
- Produces: a `Crawler` class with the standard `catalog` plugin surface (`site_name`, `base_url`, `genre_summary`, `genre`, `crawler_type`, `async def crawl_catalog()`), yielding `{"artist", "title", "format": "Vinyl", "price", "currency": "USD", "url", "cover_image_url"}`.

- [x] **Step 1: Ground the design against the live store** — identify the platform, walk the collection at two page sizes to confirm pagination is stable, histogram `vendor`, `product_type` and the tags, tabulate every variant title, compare `vinyl` against `all` type by type and check whether any record hides outside the shelf, look for a pre-order signal, check availability, images, prices, `meta.json`, `collections.json` and `robots.txt`.
- [x] **Step 2: Write the crawler** — the `vinyl` collection; shape-based `product_type` gate; vendor-only artist with no billing reduction; verbatim title composition; always-appended pressing name with the placeholder exception; no pre-order handling; guarded price parse; nested drift guards.
- [x] **Step 3: Write the test file** — fixtures distinguish captured / invented provenance, each marked at its definition; cases per the design spec's Verification section.
- [x] **Step 4: Replay over the fully-cached live catalog** — 134 products walked → 134 vinyl-typed → 176 rows across 80 artists, no `item_key` collisions, no blank artist or title, no malformed URL, no missing cover, no null price, 30 products skipped as sold out.
- [x] **Step 5: Run the test file** — all tests in it pass.
- [x] **Step 6: Mutation-check that each guard and rule bites** — mutate the crawler once per guard or rule and confirm the tests fail. One mutation survived on the first pass (un-nesting the artist tally, which made the wrong guard fire); a test was added to pin it, after which every mutation was caught.
- [x] **Step 7: Run the wider crawler test selection for regressions** (`pytest tests/ -k crawler` with the three test env vars set — the plugin loader imports every module in `backend/crawlers/`, so a syntax error in the new file breaks unrelated tests).
- [x] **Step 8: Pre-PR spec-drift check** — grep both spec trees for the files, symbols and strings the diff touches; amend anything that has drifted; strip any inventory count found along the way.
- [x] **Step 9: Commit** via `git commit -F`, with trailers.
- [x] **Step 10: Address the PR review** — Copilot raised two findings on the opened PR: the format gate not covering the mistyped-CD case it cites as its reason, and discarded variants bypassing the empty-snapshot guards. Both verified against the code, both fixed, regression tests and mutations added for each, and the design doc amended.
- [x] **Step 11: Address the second review round** — Copilot raised the plan's architecture summary contradicting its own constraints, and partial vendor/variant-source loss bypassing the guards. Both verified (the second reproduced as two failing tests first), both fixed, guards and mutations added.
- [x] **Step 12: Address the third review round** — Copilot raised the `format-taxonomy drift` guard reporting the wrong field when the *title* layer is what rejected everything, and this task log listing Step 11 before Step 10. Both verified, both fixed; the gate's two layers now have separate tallies and a distinct `title-gate drift` guard.
