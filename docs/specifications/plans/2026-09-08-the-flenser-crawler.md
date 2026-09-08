# The Flenser Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking. (This plan was executed inline in the session that wrote it — recorded here as the historical task log the plans tree is for.)

**Goal:** Add `backend/crawlers/theflenser.py`, a `crawler_type="catalog"` Shopify plugin covering The Flenser's webstore (`nowflensing.com`), whose `/collections/vinyl` shelf the request named.

**Architecture:** Walk the store's `all` collection via the existing `shopify_catalog.iter_products()` helper — *not* the `vinyl` shelf, which omits three published, in-stock, vinyl-typed records. Format scoping is a three-layer gate: a per-segment `product_type` test, then a positive format gate on the title's trailing descriptor (which is what separates a record from the store's scratch-and-dent bin), then a negative variant gate that drops another-medium siblings, checking a vinyl word *before* a medium word. Artist and album both come from the store's `Artist "Album" <format>` title convention, with **no** fallback source — `vendor` is the releasing label here, never the artist. A multi-artist billing is reduced to the first-billed artist on a slash and never on an ampersand. The pressing is appended to every row that names one; the `Default Title` placeholder carries the album alone, as a product's sole variant only. Skip unavailable variants with **no** pre-order bypass and **no** ` (Pre-Order)` marker. Raise on an empty collection, on a catalog with no vinyl type, no parseable title, no named format, or no record variant, on a walk that yielded no rows while a record had no identity or no readable flag, and on a walk that yielded rows but no prices — so drift can never wipe the previous snapshot through `replace_stock_items()`.

**Tech Stack:** Python ≥3.9, `httpx` (via `shopify_catalog.iter_products`), `pytest`/`pytest-asyncio`, `respx` for HTTP mocking.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`; use `Optional[str]` or leave untyped.
- No new shared module — reuse `shopify_catalog.iter_products()` and `resolve_cover_image()` unchanged. `has_tag` and `strip_vendor_prefix` are not used: no tag is read, and no vendor prefix appears in a title.
- `format` is hardcoded `"Vinyl"`; `currency` is hardcoded `"USD"` (confirmed via the store's `meta.json`).
- **`_COLLECTION_SLUG = "all"`, not the `vinyl` shelf the request named.** The shelf walks cleanly and matches its own paginated HTML, but three published, in-stock, vinyl-typed records sit outside it. `all` returns exactly `meta.json`'s `published_products_count` and the same handle set as the store-wide `/products.json`.
- **The `product_type` gate tests per comma-separated segment**, not the whole string: an equality test drops the records typed `Vinyl,Distributed titles`/`Vinyl,Flenser Releases`, and a substring test would admit a type that merely contains the word. It enumerates positively, so a type added later stays out by default.
- **The gate reads `product_type`, never `tags`.** The tag is wrong in both directions live — it is on the vinyl-edition subscription (typed `Membership Series`), and absent from two records that carry no tags at all.
- **Artist and album both come from the `Artist "Album"` title, with no fallback.** `vendor` is the releasing label on this store, so a vendor fallback would credit every unparseable title to a record label. This is also what excludes the store's vinyl-shelved bundle.
- **A multi-artist billing is reduced to the first-billed artist on a slash only, never on an ampersand.** Every live ampersand is a collaboration, but an ampersand is also how single acts spell their own names (Belle & Sebastian, Iron & Wine), and a wrong artist is worse than a missed library match. The slash requires whitespace on at least one side, so `AC/DC` is not clipped; the reduction reads the artist segment only, so a slash in an album title is untouched.
- **The descriptor gate is positive**, because a descriptor can name a record *and* something else — `DLP & DVD`, `DLP & Zine` and `DLP & Book (pre-order)` are all real records in packaging. It is what drops `Various "Scratch & Dent" Stock`, a `Vinyl`-typed bin whose variants are whole other releases.
- **A bundle-shaped descriptor is rejected before the format test**, and read against the descriptor rather than the whole title so an album named `"Bundle of Joy"` survives.
- **`EP` is admitted by the descriptor gate and *not* by the variant gate.** The two ask different questions: the descriptor only has to name a format (`product_type` already established the medium), while a variant has to be a record next to siblings that may not be — a `CD EP` must not be admitted by the word that admits a 12" EP.
- **The variant gate is negative and its check order is load-bearing:** vinyl word first, then the another-medium rejection.
- **The row's title is `album + " — " + variant`**, whitespace-collapsed, with the variant appended on every row that names one so `compute_item_key` stays stable.
- **The `Default Title` placeholder carries the album alone, and only as a product's sole variant.**
- **Availability comes from `variant.available` and only the literal `True` admits a row; no pre-order bypass, and no marker is written** — the store announces pre-orders in the descriptor, outside the album, so the row is titled the same before and after the record ships.
- **Readability is judged over the admitted variants only**, with `all()`, not `any()`.
- **The tally chain is nested** (type → parse → format → variant gate → stock readability), so a non-zero count means "some product would have yielded a row if it were in stock". **The tallies that count what the crawler *dropped* are deliberate exceptions and sit beside the chain, not inside it** — a dropped product could never have yielded, so nesting them makes them unreachable. Their placement is asymmetric and load-bearing: a blank title is seen **before** the parse, since it fails the parse and that is the only place it is visible at all, while `identity_missing`'s handle half, `unnamed_pressings` and `variantless_records` wait until the title and descriptor have established the product is a record, so a deliberately excluded bin or bundle cannot arm a guard with a defect of its own. All tallies are taken **before** the availability filter, so a sold-out store is empty legitimately.
- **`price-source drift` fires only when no row at all carries a price**; isolated nulls stay tolerated.
- No comments except where the WHY is non-obvious.
- Registration is automatic via `main.py`'s bundled-crawler startup loop — no wiring changes anywhere else.
- Every commit carries the AI-attribution trailer block required by this repo's `CLAUDE.md`, created via `git commit -F <message-file>`, not `-m`.

Full grounding for every rule above: [`docs/specifications/shaping/2026-09-08-the-flenser-crawler-design.md`](../shaping/2026-09-08-the-flenser-crawler-design.md).

**Running the tests.** These tests mock HTTP with `respx` and never reach the store, but the `*_crawler` autouse fixture in `conftest.py` still resolves through the config layer, so run them with the three test env vars set. From `backend/`:

```bash
cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test \
  IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_theflenser_crawler.py -v
```

---

### Task 1: The Flenser crawler + tests

**Files:**
- Create: `backend/crawlers/theflenser.py`
- Test: `backend/tests/test_theflenser_crawler.py`

**Interfaces:**
- Consumes: `shopify_catalog.iter_products(base_url, collection_slug)`, `resolve_cover_image(product, variant)` — both exist unchanged in `backend/shopify_catalog.py`.
- Produces: a `Crawler` class with the standard `catalog` plugin surface (`site_name`, `base_url`, `genre_summary`, `genre`, `crawler_type`, `async def crawl_catalog()`), yielding `{"artist", "title", "format": "Vinyl", "price", "currency": "USD", "url", "cover_image_url"}`.

- [x] **Step 1: Ground the design against the live store** — identify the platform; fully paginate the `vinyl` shelf and cross-check it against its own paginated HTML; compare it with the store-wide `/products.json` and with `collections/all`; pull the other vinyl-named shelves; read `meta.json`, `collections.json` and `robots.txt`; histogram `vendor`, `product_type` and the tags; test the title convention against every vinyl-typed product; tabulate every descriptor and every variant title; check availability, prices, images and multi-artist billings.
- [x] **Step 2: Write the crawler** — the `all` collection; per-segment `product_type` gate; quoted-title parse with no fallback; slash-only billing reduction; positive descriptor format gate with a bundle rejection; negative variant gate with vinyl-word-first ordering; album-then-pressing title composition; always-appended pressing with the placeholder exception; no pre-order marker and no pre-order bypass; guarded price parse; nested drift guards.
- [x] **Step 3: Write the test file** — fixtures carry captured live payloads, marked as such at their definition; cases per the design doc's Verification section.
- [x] **Step 4: Replay over the fully-cached live catalog** — 754 products walked → 281 vinyl-typed → 280 parsed → 279 naming a format → 319 rows across 163 artists, no `item_key` collisions, no blank artist or title, no malformed URL, no missing cover, no null price. Exactly the two intended drops.
- [x] **Step 5: Run the test file** — all tests in it pass.
- [x] **Step 6: Mutation-check that each guard and rule bites** — mutate the crawler once per guard or rule and confirm the tests fail. Two mutations initially survived (a substring `product_type` test and a tag-driven gate), exposing two tests that were not isolating the gate they named; both tests were rewritten and every mutation is now caught.
- [x] **Step 7: Run the wider crawler test selection for regressions** (`pytest tests/ -k crawler` with the three test env vars set — the plugin loader imports every module in `backend/crawlers/`, so a syntax error in the new file breaks unrelated tests).
- [x] **Step 8: Commit** via `git commit -F`, with trailers.
- [x] **Step 9: Address Copilot's first review round** — three defects, each reproduced against the code before being fixed and each given its own mutation: a nested quotation truncating the album (`Artist "The " Big" LP` parsed to an album of `The`, then passed the format gate on the leftover `LP`), an `identity-source` tally nested behind the parse so it could only ever fire for a missing handle and never the missing title its message names, and an in-stock pressing dropped for an unreadable name leaving the walk looking sold out. Plus two comment/doc corrections. Re-replayed over the cached catalog: byte-identical, 319 rows.
- [x] **Step 10: Address Copilot's second review round** — five more, each reproduced first: the inch fragment had no right-hand boundary (so `12"CD` read as a record *and* accounted for its quote, letting `Artist "The " 12"CD` through truncated), two separately valid inch markers vouched for each other (`Artist "The " 54" 12"`), the sole-variant test read the filtered variant list rather than the raw one, a record with an empty `variants` array left no trace, and the dropped-product tallies sat early enough that a deliberately excluded bin or bundle could arm a guard and make a genuinely sold-out crawl preserve a stale snapshot. Declined the suggestion to count unclassifiable vinyl-typed products — reasoning recorded on the thread and in the design doc.
- [x] **Step 11: Address Copilot's third review round** — a Unicode-normalisation hole (`\w` treats a combining mark as a word separator, so the NFD spelling of a title classified differently from its NFC equivalent and bypassed the nested-quote guard), plus two documentation examples that described a rule the code does not have and a plan step whose two rounds had been merged into one. Mark folding follows `dongiovannirecords.py`: length- and position-preserving, decision-time only.
- [x] **Step 12: Address Copilot's fourth review round** — the emitted side of the normalisation issue: `_fold_marks` is decision-time only, so rows still carried whatever normalisation the storefront served, while `compute_item_key` hashes them raw and `db._library_release_match_sql` compares them with a plain `LOWER()`. Rows are now composed to NFC through `_canonical()`, which is deliberately not the fold. Plus `_classify_variants` being called twice per record through its two wrappers, and two stale documentation summaries.

---

## Post-implementation: pre-PR spec-drift check

- [x] **Grep both spec trees** (`docs/superpowers/specs/`, `docs/specifications/shaping/`) for the files, symbols, and names this diff touches.
- [x] **Delete any crawler/store/source/plugin/test count** found in a spec visited during the check, rather than updating it.
- [x] **Record findings in the PR description** (drift found and fixed, or none).
