# Don Giovanni Records Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking. (This plan was executed inline in the session that wrote it — recorded here as the historical task log the plans tree is for.)

**Goal:** Add `backend/crawlers/dongiovannirecords.py`, a `crawler_type="catalog"` Shopify plugin covering Don Giovanni Records' webstore (`dongiovannirecords.com`), at the `vinyl` collection the request named.

**Architecture:** Walk the store's `vinyl` collection via the existing `shopify_catalog.iter_products()` helper. The artist is `vendor`, which is a real credit on every product and is never the label's own name; the album and the format descriptor come from the title's `Artist "Album" <format>` convention — the quoted-album half of which every single-item product in the store follows (the multi-item bundles do not, which is why `_bundle_shaped()` has to recognise them separately), while the trailing format is universal only within the vinyl collection, since the store's books, pins and stickers stop at the quoted album. The row's title is the album followed by the format, then the variant colour — so it still prefix-matches a library title. A record word in the descriptor admits, a word naming another medium or a merch item rejects, and anything else is admitted on the collection's own claim. Bundles and a `+` joining a record to a merch word are both rejected by the gate, on the descriptor, ahead of the record word that would otherwise admit it. Skip unavailable variants with **no** pre-order bypass and **no** ` (Pre-Order)` marker. Raise on an empty collection, on a catalog with no `vendor`, on a catalog with no parseable title, on a catalog where the two never co-occur on one product, on a walk that yielded rows but no prices, and on a walk that yielded no rows while a record had no identity or no readable availability flag — so drift can never wipe the previous snapshot through `replace_stock_items()`.

**Tech Stack:** Python ≥3.9, `httpx` (via `shopify_catalog.iter_products`), `pytest`/`pytest-asyncio`, `respx` for HTTP mocking.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`; use `Optional[str]` or leave untyped.
- No new shared module — reuse `shopify_catalog.iter_products()` and `resolve_cover_image()` unchanged. `strip_vendor_prefix` is not used (the store separates artist from album with quotes, not a dash), and neither is `has_tag`: the store's `preorder` tag is deliberately never read.
- `format` is hardcoded `"Vinyl"`; `currency` is hardcoded `"USD"` (confirmed via the store's `meta.json` and `cart.js`).
- **`_COLLECTION_SLUG = "vinyl"`.** `collections.json` reports a `products_count` larger than the published catalog; `products.json` returns the published products and those are what is walked. The shelf is exactly the store's records in both directions — nothing non-vinyl in it, no vinyl outside it.
- **The artist comes from `vendor`, and from nothing else.** No fallback to the title's artist prefix: it would defeat the artist-source guard, and the title truncates a long credit mid-word where `vendor` does not.
- **The album and the format descriptor come from the title, and both are required.** The two character classes are asymmetric on purpose: the leading group excludes only the two characters that can *open* an album (so the album's opening quote is always the title's first) while still admitting `”` and `″`, which cannot open a quotation and sit in a prefix that is discarded anyway; the album group excludes every quote there is (so a fourth quote is junk rather than an album). Between them that pins all three of the title's quotes. The leading group is non-capturing and may be empty, since the credit comes from `vendor` and a title that omits the artist still carries a readable album and format; the closing quote's lookahead refuses a closing quote glued to a letter, and the descriptor may carry at most one complete inch marker and no quote outside one — judged against the same `_INCH_MARKER` token the format gate uses, never a lookbehind approximating it, since every version that approximated it disagreed with the gate at some edge (`Studio54"` wrongly accepted, `12 "` wrongly rejected). The token needs a right-hand boundary on its quote glyph, or `12"CD` reads as a complete marker and the gate admits it before noticing the `CD`. Every boundary here is Unicode-aware on both sides, and `\w` is not enough on its own: it excludes the combining mark categories, so in decomposed text the character before `LP` in `éLP CD` is the accent rather than a letter and the boundary opens. `_fold_marks` replaces marks with a letter for matching only — never for anything emitted — so the two normal forms of a string are read the same way. A title naming no format is not a parse — that is what keeps the store's books, pins and stickers out if one is ever mis-shelved here, and a store-wide loss of the trailing format raises album-source drift rather than silently emptying the walk.
- **The row's title is `album + " " + descriptor`, whitespace-collapsed**, then the colour. The descriptor stays *after* the album so `db._library_release_match_sql`'s prefix test still matches.
- **Bundles are skipped**, matched on the descriptor inside the format gate — not on the whole title, which would discard an album legitimately containing the word.
- **The format gate reads the descriptor only**, `+`-merch combo first, then record word, then other-medium/merch, defaulting to admit. Its rejecting vocabulary is the store's own `product_type` values, plus `Zine`; the disc media carry the same optional disc-count prefix the record words do, since `2xCD` is one of those types and `\bcds?\b` cannot see the `CD` in it. `product_type` itself is not the gate — it disagrees with the title on a live product and an enumeration would silently drop a format the store adds.
- **The variant colour is appended on every row that names one**; the `Default Title` placeholder is the one exception, and only as a product's sole variant.
- **Availability comes from `variant.available`, literal `True` only; no pre-order bypass.** No ` (Pre-Order)` marker is written either: `compute_item_key` hashes the title, so a marker that disappears on release would re-key every pressing and orphan the saves and judgments held against it. The store's `preorder` tag is trustworthy — it agrees exactly with its `preorders` collection — and is nonetheless never read.
- **Readability is judged against the raw variant set** (a sole blank-titled variant that is readably sold out is readable; no variants at all is not), reading the kept pressings' flags with `all()`, not `any()`, and paired with `_unusable_dropped_variant` running first.
- **`artist_ok` and `parsed_ok` are tallied independently** — they are independent sources, and conflating them lets one going dark hide behind the other — with a third `sources_ok` tally requiring both to co-occur on one product, taken before the format gate so an all-CD shelf still satisfies it. **A title-less product counts toward `identity_missing` before the gate**, since `_record` reads the title and such a product can never reach the checks inside it, and **a variant dropped for want of a usable title counts too unless it is provably sold out** (only the literal `False` proves that), or a blank-titled in-stock variant beside a sold-out sibling makes an in-stock product look sold out. **A product whose own sources failed counts toward a separate `unclassifiable` tally**, since the catalog-wide tallies only notice a source vanishing from every product; a CD or a bundle was read and then skipped, and does not count — that test runs first, so a blank-vendored CD is exempt while a blank-vendored record is not. The unquoted-bundle exemption enforces its own no-quote precondition. **`identity_missing` and `unreadable_stock` are nested inside the format gate**, so a non-zero count means "some product would have yielded a row if it were in stock".
- No comments except where the WHY is non-obvious.
- Registration is automatic via `main.py`'s bundled-crawler startup loop — no wiring changes anywhere else.
- Every commit carries the AI-attribution trailer block required by this repo's `CLAUDE.md`, created via `git commit -F <message-file>`, not `-m`.

Full grounding for every rule above: [`docs/specifications/shaping/2026-09-07-don-giovanni-store-crawler-design.md`](../shaping/2026-09-07-don-giovanni-store-crawler-design.md).

**Running the tests.** These tests mock HTTP with `respx` and never reach the store, but the `*_crawler` autouse fixture in `conftest.py` still resolves through the config layer, so run them with the three test env vars set. From the repository root:

```bash
cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test \
  IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_dongiovannirecords_crawler.py -v
```

---

### Task 1: Don Giovanni Records crawler + tests

**Files:**
- Create: `backend/crawlers/dongiovannirecords.py`
- Test: `backend/tests/test_dongiovannirecords_crawler.py`

**Interfaces:**
- Consumes: `shopify_catalog.iter_products(base_url, collection_slug)` and `resolve_cover_image(product, variant)` — both exist unchanged in `backend/shopify_catalog.py`.
- Produces: a `Crawler` class with the standard `catalog` plugin surface (`site_name`, `base_url`, `genre_summary`, `genre`, `crawler_type`, `async def crawl_catalog()`), yielding `{"artist", "title", "format": "Vinyl", "price", "currency": "USD", "url", "cover_image_url"}`.

- [x] **Step 1: Ground the design against the live store** — identify the platform; walk the collection at two page sizes to confirm pagination is stable; histogram `vendor`, `product_type`, tags and every variant title; test the title convention against the whole catalog; check `vendor` against the title's artist prefix product by product; diff the collection against the store's `all` collection in both directions; find the pre-order signal and cross-check it against the `preorders` collection; check availability, images, prices, `meta.json`, `collections.json` and `robots.txt`.
- [x] **Step 2: Write the crawler** — the `vinyl` collection; `vendor` artist with no fallback; quoted-album title parse; album-then-descriptor composition; bundle skip; descriptor-only format gate; always-appended colour with the placeholder exception; no pre-order marker and no availability bypass; guarded price parse; independent artist/album tallies with a combined co-occurrence tally, and nested identity/stock tallies.
- [x] **Step 3: Write the test file** — fixtures distinguish captured / altered / invented provenance, each marked at its definition; cases per the design doc's Verification section.
- [x] **Step 4: Replay over the fully-cached live catalog** — 158 products walked → 140 rows across 92 artists, no `item_key` collisions, no blank artist or title, no malformed URL, no missing cover, no null price.
- [x] **Step 5: Run the test file** — all tests in it pass.
- [x] **Step 6: Mutation-check that each guard and rule bites** — mutate the crawler once per guard or rule and confirm the tests fail.
- [x] **Step 7: Run the wider crawler test selection for regressions** (`pytest tests/ -k crawler` with the three test env vars set — the plugin loader imports every module in `backend/crawlers/`, so a syntax error in the new file breaks unrelated tests).
- [x] **Step 8: Commit** via `git commit -F`, with trailers.

---

## Post-implementation: pre-PR spec-drift check

- [x] **Grep both spec trees** (`docs/superpowers/specs/`, `docs/specifications/shaping/`) for the files, symbols, and names this diff touches.
- [x] **Delete any crawler/store/source/plugin/test count** found in a spec visited during the check, rather than updating it.
- [x] **Record findings in the PR description** (drift found and fixed, or none).

## Follow-up noted, not done here

`title_key`'s disc-size phrase rule is anchored on a word boundary, so
`2x12"` survives into the key as a literal token where `2xLP` and
`Double LP` fold away — a double LP from this store will not group with
another store's copy of the same pressing under the Cheapest filter. It is
pre-existing (`earache.py` emits the same descriptors and hits it
identically) and fixing it changes a shared module that keys every row in
the Store tab, so it belongs in its own change.
