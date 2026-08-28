# 1-2-3-4 Go! Records Store Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `backend/crawlers/onetwothreefourgo.py`, a `crawler_type="catalog"` Shopify plugin covering 1-2-3-4 Go! Records' (`1234gorecords.shop`) `all` collection.

**Architecture:** Iterate the store's `all` collection via the existing `shopify_catalog.iter_products()` helper and gate on vinyl in-process. This is a *retail store*, not a label: `vendor` is a distributor (`Alliance`, `UMG`, `WEA`) or the literal `Used Product`, and is never the artist. The store's title convention is `[MARKER:] Artist "Album" DESCRIPTOR`, so the artist comes from a quoted-album split rather than the fleet's usual dash split, after a status-marker prefix is stripped off the front and re-emitted as a title suffix.

**Tech Stack:** Python ≥3.9, `httpx` (via `shopify_catalog.iter_products`), `pytest`/`pytest-asyncio`, `respx` for HTTP mocking.

> **Sequencing note, recorded for honesty.** This plan was written alongside the implementation rather than ahead of it: the work began as an ad hoc request, and every decision below depended on paginating the live store first. Per `CLAUDE.md`, plans in this tree are historical per-feature task logs rather than living reference, so this documents what was built and why, in the fleet's standard form. Tasks are checked off because they are done.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`; use `Optional[str]` or leave untyped.
- No new shared module — reuse `shopify_catalog.iter_products()` and `resolve_cover_image()` unchanged. This crawler does **not** use `has_tag()` or `strip_vendor_prefix()`: every marker it needs is in the title, and there is no vendor prefix to strip.
- `format` is hardcoded `"Vinyl"` on every yielded item; `currency` is hardcoded `"USD"`.
- **The module is named `onetwothreefourgo.py`, not `1234gorecords.py`.** A leading digit makes `from crawlers.1234gorecords import Crawler` a syntax error, so the test file could not import it the way every sibling test does. `twentybuckspin.py` is the precedent for spelling the digits out. `site_name` carries the real name, `1-2-3-4 Go! Records`.
- **`_COLLECTION_SLUG = "all"`. Do not "narrow" this to a vinyl-looking collection.** `lp`, `new-vinyl` and `allusedvinyl` each miss part of the catalog, and the store's self-reported `products_count` cannot be used to compare them — it is stale metadata (`quick-order` claims 101,758 and paginates to 10,975). `all` paginates to 10,982 products and is a strict superset by product `id` of every other collection checked.
- **`artist` comes from the quoted-album split, never from `vendor`.** A product with no usable pair of quotes yields nothing — 28 of 9,785 live vinyl-typed products, all genuinely unparseable.
- **The quote delimiter must keep all three forms.** Straight `"`, curly `“`/`”`, and a doubled apostrophe `''` — 28 products use the last one and nothing else. Both regex groups must stay non-greedy, or a quoted phrase inside the pressing notes and the trailing inch mark on a 7" will move the split.
- **Never add a guard that rejects an album for "looking like a format token."** It fires on Adele "19", Adele "21", Blur "13", Beach House "7", Mac DeMarco "2", FKA Twigs "LP1" and Joey Badass "1999", all real albums.
- **A colon is required for the `used`, `pre-order` and bare `damaged` marker forms.** "Damaged Bug" is a real band; a rule stripping a leading `DAMAGED ` would rewrite its artist to `Bug`. `DAMAGED COVER` needs no separator because two words in that order cannot be an artist name. The one live product this costs (`DAMAGED Sultans "Ghost Ship" LP`) is pinned by a test so it stays a decision.
- **The marker moves off the front of the title and onto the back of the album, never stays at the front.** `db._library_match_fragment` matches a stock title against the catalog exact-or-prefix-with-space, so `154 LP (1979 Japanese Issue) (Used)` matches catalog `154` and `Used Vinyl: 154` matches nothing. On a multi-variant product the variant descriptor still comes last — `Sleep Talk LP (Used) — Metallic Gold`, not `… — Metallic Gold (Used)`. Only the *front* position breaks the catalog match; both back positions match identically, so the order between them is a readability call, settled this way because `— {variant}` is terminal across the fleet and because the marker describes the product while the variant names the pressing. Pinned by a test, since no live product is currently both marked and multi-variant.
- **Marker stripping loops.** Two live products are double-marked (`Used Vinyl: Used Vinyl: …`).
- **Invisible format characters must be removed before parsing.** 195 products carry a `U+200E LEFT-TO-RIGHT MARK` between artist and opening quote; `str.strip()` does not remove it, and `_library_match_fragment` compares artist with exact `LOWER()` equality.
- **The format descriptor stays in the emitted title.** Dropping it would leave 1,003 live rows reading identically to another row instead of 237 — the store stocks ten separate copies of one Clash pressing.
- **The competing-format filter needs its vinyl override on both sides.** Applied to the title descriptor it drops the 13 vinyl-typed products that are CDs or Blu-Rays, while keeping genuine hybrids (`2xLP + CD`, `LP + 7"`, `2xLP + 15xCD Box Set`). Applied to variant names on a multi-variant product it drops a cassette-only-in-stock product, while keeping `2xLP 2xCD + DVD`.
- **Both sides of that filter must allow a disc-count prefix.** A count binds to its format word with no word boundary between them, so a bare `\bcds?\b` cannot see the CD in `3xCD` — and the store writes `2xCD`, `3xCD`, `6xCD`, `14xCD`, `15xCD` and `2xDVD`. Three products were live in that gap and were being published as vinyl. `spv.py` records the identical regression; use its `\d*[x×]?` spellings. Neither side is safe while it recognises fewer spellings than the other, or fewer than the store writes.
- **"EP" is not vinyl evidence, and the inch mark is.** An EP is a release length, not a medium, so it must never override a descriptor naming a CD (`spv.py` keeps it out of its vocabularies for the same reason). The inch mark is the only vinyl evidence 1,208 live descriptors have, and the single live descriptor pairing it with a competing format is a genuine record-plus-tape bundle — so it stays. Both are pinned by tests.
- **Pre-orders get the ` (Pre-Order)` suffix but not the siblings' availability bypass.** Those crawlers bypass `available` because their stores flag purchasable pre-orders unavailable; this one does not, so a bypass emits no rows today and would publish a gone allocation later. `darksiderecords.py` made the same call. Do not "restore" the bypass without evidence the store has started flagging live pre-orders unavailable — a test pins the current behaviour so the change would be deliberate.
- **A multi-variant product must give each row a distinct title.** `db.compute_item_key()` hashes exactly `(artist, title, url)` and the `url` here is per-product. Gate the descriptor on the product's *surviving* variant count, never its available count, or a row's identity churns when a sibling sells out. A blank or `Default Title` variant name falls back to the variant id, and a variant with neither raises — `db.replace_stock_items()` INSERTs with no ON CONFLICT guard, so colliding rows fail the sync, and raising leaves the previous snapshot intact instead. Ported from `darksiderecords.py`.
- **The competing-format variant filter runs only on multi-variant products.** A single variant is usually the `Default Title` placeholder, which names no format at all.
- No comments except where the WHY is non-obvious — no comments describing WHAT the code does.
- **A total parse failure must raise, never return empty.** `db.replace_stock_items()` DELETEs this crawler's rows before inserting, and `_sync_stock` only skips it when the crawl *raised* — so a generator that completes with nothing to show wipes the store's snapshot and records the site healthy. `crawl_catalog` counts products seen, vinyl-typed products seen, and titles parsed, and raises when the collection answers with nothing at all or when no vinyl title parses. Sold-out stock, a competing-format descriptor and an unavailable variant all parse first and are dropped after, so none of them trips it. `dischordrecords.py` and `sideonedummyrecords.py` record the same reasoning on their own stores. `product_type` drift is a knowingly uncovered third mode — guarding it would mean raising on any vinyl-free catalog, which is indistinguishable from a legitimately vinyl-free page.
- **`_parse_title` returning None must mean "the title did not parse", nothing else.** The competing-format rejection lives in `_items` for that reason: conflating the two would make every mistyped-CD catalog look like drift.
- Registration is automatic via `main.py`'s `seed_bundled_crawlers()` — no wiring changes anywhere else.
- Every commit carries the AI-attribution trailer block required by this repo's `CLAUDE.md`, created via `git commit -F <message-file>`, not `-m`. **On this branch the block omits `ai-model`**, which `CLAUDE.md` otherwise requires: a session-level rule forbids writing a model identifier into any artifact pushed to a repository, and the two rules cannot both be satisfied. Recorded here rather than left implicit, so the plan does not claim a compliance the history does not have. Not defended as normal practice: `ai-model` is present on the overwhelming majority of this repo's AI commits, and `9bc39e8` is the lone prior omission — one instance, not an established exception. Resolving the conflict properly is a maintainer decision, and the trailers can be added by amending these commits from a session without the restriction.

Full grounding for every rule above: [`docs/specifications/shaping/2026-08-28-1234-go-records-crawler-design.md`](../shaping/2026-08-28-1234-go-records-crawler-design.md).

**Running the tests.** These tests need no database — they mock HTTP with `respx` and never touch Postgres. Run from `backend/`:

```bash
cd backend && pytest tests/test_onetwothreefourgo_crawler.py -v
```

---

### Task 1: 1-2-3-4 Go! Records crawler + tests

**Files:**
- Create: `backend/crawlers/onetwothreefourgo.py`
- Test: `backend/tests/test_onetwothreefourgo_crawler.py`

**Interfaces:**
- Consumes: `shopify_catalog.iter_products(base_url, collection_slug) -> AsyncIterator[dict]` and `shopify_catalog.resolve_cover_image(product, variant) -> Optional[str]`, both unchanged in `backend/shopify_catalog.py`.
- Produces: a `Crawler` class with `site_name`, `base_url`, `genre_summary`, `genre`, `crawler_type = "catalog"`, and `async def crawl_catalog(self) -> AsyncIterator[dict]` yielding `{"artist": str, "title": str, "format": "Vinyl", "price": Optional[float], "currency": "USD", "url": str, "cover_image_url": Optional[str]}`. The rest of the surface is private helpers: `Crawler._is_vinyl(product) -> bool`, `Crawler._items(product) -> list`, `Crawler._parse_title(raw_title) -> Optional[tuple]`, `Crawler._clean(text) -> str`, and `Crawler._competes_with_vinyl(text) -> bool`.

- [x] **Step 1: Confirm the live shape before writing anything**

Enumerate `/collections.json`, then paginate the candidate collections' `products.json` to exhaustion and cache every page. Record: which collections are reachable and how their real sizes compare to `products_count`; the `product_type` distribution; the title-form distribution and the parser's no-match rate; every status-marker spelling, typos included; the variant-count distribution and availability rate; every invisible character present in a title; and the duplicate-row rate with and without the format descriptor. Confirm `products.json` is served without a browser.

- [x] **Step 2: Write the test file**

`backend/tests/test_onetwothreefourgo_crawler.py`, on `test_darksiderecords_crawler.py`'s pattern — product literals taken from confirmed-live products where one exists, served through `respx`-mocked `products.json` responses and driven via `crawl_catalog()`. Where a behaviour has no live example the literal is a live product with one field altered, or an invented product, marked as such at its own definition — the live catalog contains no band named "Damaged Bug", no `CD EP` descriptor, no blank variant name and no drifted title, and those are exactly the cases worth guarding. Cases covering: the straight-quote split with every emitted field; the doubled-apostrophe and curly-quote delimiters; the format descriptor kept in the title; each marker moved from prefix to suffix; a repeated marker; the bare-`DAMAGED` non-strip and the band-named-Damaged case it buys; invisible-character removal; a numeric album; a nested quote in the pressing notes; a trailing inch mark; a product with no quoted album (dropped); vendor never used as artist; a vinyl-typed CD (dropped); a hybrid release naming both formats (kept); a non-vinyl `product_type`; an unavailable variant; one row per available variant with distinct identities; identity stable when siblings sell out; a single-variant bare title; a competing-format variant dropped; a hybrid variant kept; variant image preferred over product image; malformed price; pagination to an empty page; site metadata.

**Assertion style, load-bearing:** assert *sequences*, not sets, wherever a row count matters. A set collapses duplicate titles and passes even when a row is silently dropped or two rows collide on one `item_key`.

- [x] **Step 3: Write the crawler**

Implement to green. `_is_vinyl` is a `product_type` set membership test; `_parse_title` cleans, loop-strips markers and splits on quotes, returning None only when the title does not parse; `_items` applies the competing-format rejection to the descriptor, filters competing-format variants on multi-variant products, and loops available ones. `crawl_catalog` counts what it saw and raises on drift rather than yielding empty. (The descriptor rejection lived in `_parse_title` until the drift guard was added, which needed the two failure kinds separable.)

- [x] **Step 4: Replay the crawler over the cached live pages**

Run the finished `_is_vinyl`/`_items` against every cached page and check the aggregate, not just the unit tests: row count, zero `(artist, title, url)` collisions, no artist left carrying a marker or an invisible character, no row missing a price, and the emitted rows for all seven live multi-variant products. This is what surfaced the double-marked products, which no single fixture would have.

- [x] **Step 5: Pre-PR spec-drift check**

Per `CLAUDE.md`. `2026-08-07-shared-title-split-helper-design.md` needed an amendment — this is the first crawler in the fleet with no dash split at all, and it adds three parser divergences the doc has not seen. `2026-07-05-in-stock-crawler-design.md` did not: its 2026-08-23 amendment already declares its source lists a historical snapshot and points at the per-source shaping docs as the live record. `2026-08-12-store-genre-summaries-design.md` carried an inventory count, which was deleted rather than updated.
