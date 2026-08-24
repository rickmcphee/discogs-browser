# Darkside Records Store Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `backend/crawlers/darksiderecords.py`, a `crawler_type="catalog"` Shopify plugin covering Darkside Records' (`shop.darksiderecords.com`) `new-vinyl-in-stock` collection.

**Architecture:** Iterate the store's `new-vinyl-in-stock` collection via the existing `shopify_catalog.iter_products()` helper. This is a *retail store*, not a label, so it follows `jackpotrecords.py`'s shape rather than the label-store crawlers: `vendor` holds the distributor (`THE ORCHARD`, `UMG`, `WMX`, `AEC`) and is never used as the artist; the artist is split off the product title with the asymmetric-spacing hyphen/en-dash/em-dash regex, and a product with no separator is dropped rather than guessed at. Gate products on a `product_type` prefix, gate variants on availability, and emit one stock item per available variant.

**Tech Stack:** Python ≥3.9, `httpx` (via `shopify_catalog.iter_products`), `pytest`/`pytest-asyncio`, `respx` for HTTP mocking.

> **Sequencing note, recorded for honesty.** This plan was written alongside the implementation rather than ahead of it: the work began as an ad hoc request, and the collection-choice decision below could only be made after paginating the live site. Per `CLAUDE.md`, plans in this tree are historical per-feature task logs rather than living reference, so this documents what was built and why, in the fleet's standard form. Tasks are checked off because they are done.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`; use `Optional[str]` or leave untyped.
- No new shared module — reuse `shopify_catalog.iter_products()` and `resolve_cover_image()` unchanged. This crawler does **not** use `has_tag()` or `strip_vendor_prefix()`: there is no pre-order carve-out and no vendor prefix to strip.
- `format` is hardcoded `"Vinyl"` on every yielded item; `currency` is hardcoded `"USD"`.
- **`_COLLECTION_SLUG = "new-vinyl-in-stock"`. Do not "upgrade" this to `new-vinyl`.** That collection reports 58,416 products and *cannot be crawled at all*: Shopify's `products.json` serves at most 100 pages, so page 101 returns a hard HTTP 400 and ~33,000 products are unreachable. Because a 400 is not a 429, `iter_products()` would also spend its whole `consecutive_failure_limit` budget retrying before raising. See the design spec's "Collection choice" section.
- **`artist` comes from the title split, never from `vendor`.** `vendor` is the distributor on this store. A product whose title has no separator yields nothing — 178 of 5,141 live, all genuinely artist-less soundtracks and compilations.
- **The title-split regex must keep its asymmetric-spacing alternation.** The dominant live form is `Artist- Album` (hyphen glued to the artist, space only after). Both alternatives require whitespace adjacent to the dash, which is what leaves hyphenated artist names (`Blink-182`, `Run-Dmc`, `Jean-Luc Ponty`, `Olivia Newton-John`) intact — verified against all 45 live cases.
- **Album titles are emitted verbatim.** Do not strip the trailing `(Vinyl)` (68% of titles) — `_library_match_fragment` matches exact-or-prefix-with-space, so `Awake (Clear Vinyl)` already matches catalog `Awake`. Do not filter out `(DAMAGED)` copies (173 emitted) — they are real discounted stock and the marker in the title is what makes the low price self-explanatory.
- **A multi-variant product must give each row a distinct title.** `db.compute_item_key()` hashes exactly `(artist, title, url)` and the `url` here is per-product, so rows sharing a title collapse onto one `item_key`. Gate the descriptor on the product's *total* variant count, never its available count, or a row's identity churns when a sibling sells out.
- **No pre-order handling.** Pre-orders are tagged (`preorder_bt` 874) and deliberately unused: the store already marks them `available: true`, so bypassing the availability gate could only ever admit sold-out stock.
- No comments except where the WHY is non-obvious — no comments describing WHAT the code does.
- Registration is automatic via `main.py`'s `seed_bundled_crawlers()` — no wiring changes anywhere else.
- Every commit carries the AI-attribution trailer block required by this repo's `CLAUDE.md`, created via `git commit -F <message-file>`, not `-m`.

Full grounding for every rule above: [`docs/specifications/shaping/2026-08-24-darkside-records-crawler-design.md`](../shaping/2026-08-24-darkside-records-crawler-design.md).

**Running the tests.** These tests need no database — they mock HTTP with `respx` and never touch Postgres. Run from `backend/`:

```bash
cd backend && pytest tests/test_darksiderecords_crawler.py -v
```

---

### Task 1: Darkside Records crawler + tests

**Files:**
- Create: `backend/crawlers/darksiderecords.py`
- Test: `backend/tests/test_darksiderecords_crawler.py`

**Interfaces:**
- Consumes: `shopify_catalog.iter_products(base_url, collection_slug) -> AsyncIterator[dict]` and `shopify_catalog.resolve_cover_image(product, variant) -> Optional[str]`, both unchanged in `backend/shopify_catalog.py`.
- Produces: a `Crawler` class with `site_name`, `base_url`, `genre_summary`, `genre`, `crawler_type = "catalog"`, and `async def crawl_catalog(self) -> AsyncIterator[dict]` yielding `{"artist": str, "title": str, "format": "Vinyl", "price": Optional[float], "currency": "USD", "url": str, "cover_image_url": Optional[str]}`. Three private helpers make up the rest of the surface: `Crawler._is_vinyl(product) -> bool`, `Crawler._items(product) -> list[dict]`, and `Crawler._compose_title(album, variant, is_multi_variant) -> str`.

- [x] **Step 1: Confirm the live shape before writing anything**

Paginate `/collections/new-vinyl-in-stock/products.json?limit=250` to exhaustion and record: total products, availability rate, variant count distribution, `product_type` distribution, title-separator forms and the regex's no-match rate. Probe `new-vinyl`'s pagination depth to find the 100-page cap. Read `robots.txt` and `/agents.md` for the crawl-citizenship section of the design spec.

- [x] **Step 2: Write the test file**

`backend/tests/test_darksiderecords_crawler.py`, on `test_jackpotrecords_crawler.py`'s pattern — product literals taken from confirmed-live products, served through `respx`-mocked `products.json` responses and driven via `crawl_catalog()`. Cases covering: the dominant glued-hyphen split; en-dash separator; a second spaced hyphen inside the album; a hyphenated artist name; no separator (dropped); vendor never used as artist; `(DAMAGED)` kept with its marker; unavailable variant skipped; pre-order emitted plainly and sold-out pre-order still skipped; product with no images; non-`New Vinyl` `product_type` skipped; one row per available variant; distinct identities across multi-variant rows; identity stable when a sibling sells out; single-variant bare title; placeholder descriptors falling back to the variant id; no stable value at all falling back to the bare album; variant image preferred over product image; malformed price; pagination to an empty page; site metadata.

**Assertion style, load-bearing:** assert *sequences*, not sets, wherever a row count matters. A set collapses duplicate titles and passes even when a row is silently dropped or two rows collide on one `item_key` — this masked two real defects during review.

- [x] **Step 3: Write the crawler**

Implement to green. `_is_vinyl` is a `product_type` prefix test; `_items` runs the title regex then loops available variants; `_compose_title` appends the variant descriptor only for multi-variant products.

- [x] **Step 4: Verify against the full live pull**

Replay `_is_vinyl`/`_items` over the complete 5,141-product capture and confirm: 4,960 emitted rows, 4,960 distinct `(artist, title, url)` triples, no blank artist or title, 13 rows with no cover (matching the 13 products tagged `Missing image`).

- [x] **Step 5: Write the design spec and run the pre-PR spec-drift check**

`docs/specifications/shaping/2026-08-24-darkside-records-crawler-design.md`. Then grep **both** spec trees for what the diff touches. Found and fixed: the Real Gone spec's `marketplace` genre enumeration, which under-counted once Darkside joined.

---

### Task 2: Review-driven corrections

Recorded because the defects are instructive, not because they were planned.

- [x] **Multi-variant `item_key` collision.** The first revision emitted the bare album for every variant of a product, so two available variants collapsed onto one `item_key` — and the test asserted a set of titles, pinning the collision rather than catching it. Fixed by appending the variant descriptor, gated on total variant count.
- [x] **Placeholder fallback reinstated the same collision.** When a multi-variant product's variant titles were Shopify placeholders, `_compose_title` returned the bare album again. Fixed by falling back to the variant `id`.
- [x] **Quantitative claims in prose.** Four figures were written from memory rather than derived and were wrong: the failure-duration estimates, the fleet scale comparisons, Carpark's row count, and an `instore-available` rate generalised from a single page. All corrected; a sweep of all 31 dataset-derived figures against the live capture confirmed the rest.
