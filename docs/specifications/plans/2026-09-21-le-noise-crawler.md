# Le Noise Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking. (This plan was executed inline in the session that wrote it, which had no `superpowers:*` skill available — recorded here as the historical task log the plans tree is for.)

**Goal:** Add `backend/crawlers/lenoise.py`, a `crawler_type="catalog"` Shopify plugin covering Le Noise, the Montreal record store (`lenoise.ca`).

**Architecture:** Walk the store's own `vinyl` shelf via the existing `shopify_catalog.iter_products()` helper. The shelf supplies most of the format scoping, so unlike `monorailmusic.py` there is no whole-shop collection to filter down — the crawler's two gates only repair what the store files onto the shelf by mistake. The first reads `product_type` lowercased, because the store's casing slips (`VInyl`) on real records. The second reads the title's bracket, because a seam of CDs and cassettes carries the `Vinyl` product_type; it is two-sided (a bracket may name several media, and a vinyl box with bonus discs must survive) and scoped to brackets rather than the whole title, because albums are *named* after media in both directions. The artist comes from the title's `Artist - Album` split and from nowhere else — `vendor` here is a catalogue number as often as a name, and reversed as often as not — taken lazily at the first dash with whitespace on at least one side, which is looser than the fleet rule and measured rather than assumed. The row's title keeps the pressing bracket, so two pressings of one album never collide on `item_key`. The shelf is larger than Shopify's `products.json` will paginate, so the walk is truncated at the endpoint's ceiling; what falls outside is the early alphabet, and that is accepted and documented rather than worked around. Raise on an empty walk, on a shelf with no vinyl product_type, on every product reading as another medium, on unreadable variants, on records that lost their identity, artist or stock flag, and on rows that all lack a price — so drift can never wipe the previous snapshot through `replace_stock_items()`.

**Tech Stack:** Python ≥3.9, `httpx` (via `shopify_catalog.iter_products`), `pytest`/`pytest-asyncio`, `respx` for HTTP mocking.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`; use `Optional[str]` or leave untyped.
- No new shared module — reuse `shopify_catalog.iter_products()` and `resolve_cover_image()` unchanged. `has_tag` and `strip_vendor_prefix` are not used: tags are never read, and no vendor prefix appears in a title.
- `format` is hardcoded `"Vinyl"`; `currency` is hardcoded `"CAD"` — `products.json` carries no currency to read, the storefront sets `cart_currency=CAD`, and the store is in Montreal.
- **`_COLLECTION_SLUG = "vinyl"`.** The store publishes a shelf per medium and per merchandise kind, so the shelf is the format scoping. The vinyl-adjacent shelves are subsets of it, not a partition.
- **The walk is truncated at Shopify's page ceiling and that is accepted.** `collections.json` reports 41,615 products; the endpoint serves 25,000. The ordering drifts down the alphabet, so the loss is concentrated in A–D rather than scattered. `sort_by` is disallowed by `robots.txt`, tag-scoped `products.json` answers 404, and the sitemap costs one request per product — all three rejected.
- **The product_type gate compares lowercased**, because `VInyl` is live on real records.
- **The medium gate reads brackets, never the whole title**, in both directions: `Lip Cream - Big Foot Cassette (Yellow)` is a record, `Led Zeppelin - Live EP (CD)` is a CD.
- **The medium gate is two-sided**: it disqualifies a product that names a non-vinyl medium *with no vinyl medium named alongside it*, so `(4LP+4CD)` and `(3LP/2CD)` survive while `(CD)`, `(2CD)`, `(Cassette)` and `(CD/BRD)` do not.
- **Both medium patterns share ONE counted prefix**, `_COUNTED`, and the sharing is the rule rather than a tidiness: a leading count has no word boundary before the letters (`2CD`, `3LP`), and spelling the prefix separately per pattern is how the two sides came to disagree — `\b\d*\s*` could not cross an ASCII `x`, so `(5xCD)` was published as a record while `(5×CD)` was not. Fixing only the non-vinyl side then drops `(3xLP/2xCD)`, a vinyl box, because its LP override is missed the same way.
- **That prefix puts its boundary before the WHOLE prefix, and its count is `\d+`.** Before the word instead, a match restarts partway through a glued digit run (`Studio12LP`); with `\d*`, a bare letter reads as a multiplier (`XLP`). The `x`/`×` stays optional, because a store writes both `2CD` and `5xCD`.
- **The inch marker needs a closing boundary the word alternatives get from `\b`.** A quote glyph is already a non-word character, so `\b` has nothing left to assert after it, and `(12"CD)` otherwise reads `12"` as a complete vinyl marker and keeps the CD. It also composes its own counted prefix requiring the `x`, because there the digits belong to the size: `2x12"` counts discs, `212"` is noise.
- **Both boundaries are spelled "not a letter or digit", never `[a-z0-9]`**, which is ASCII-only even under `IGNORECASE` and would read an accented letter as a separator.
- **`_price` catches `OverflowError` as well as `TypeError` and `ValueError`.** An oversized JSON *integer* raises it rather than answering `inf`, and it is not a `ValueError`, so one malformed price aborts the whole source. An oversized *string* becomes `inf` and is caught by the finiteness test instead.
- **An unreadable `product_type` is counted, a valid non-vinyl one is not.** Absent, empty or retyped is `kind-source drift`; `CD` is an ordinary skip. Reading them alike is how a retyped in-stock record vanished with no tally while a sold-out sibling kept the guards quiet.
- **The kind comparison lives in one place, `_is_vinyl_type`.** Duplicated between the walk and `_item`, the copy in `_item` goes dead once the walk filters first, and a gate nothing can observe is a gate nothing tests.
- **Non-product entries are skipped and counted before any helper touches them.** `iter_products()` type-checks the products *container* and yields entries unchanged, so a null or scalar raises on `.get()` and aborts the whole source.
- **Everything read through `_text()` is normalised to NFC.** A combining mark is not a letter to the medium boundaries, so in NFD `(CaféLP CD)` matches `LP` and keeps a CD that the same string in NFC drops.
- **`EP` is not in the vinyl vocabulary.** It names a length, not a medium, and CD EPs exist.
- **There is no `vendor` fallback for the artist.** The field is the act, the act reversed, a bare catalogue number, the act with a catalogue number glued on, or a genre-first label, with nothing marking which.
- **The separator class is the three dashes the store uses** — ASCII hyphen-minus, U+2010, U+2013. U+2014 does not appear and is not matched.
- **Whitespace is required on at least one side of the separator**, not both. Measured: both sides leaves 10 titles artist-less, at-least-one-side leaves 4, and the six rescued are all split correctly.
- **The artist half is lazy (`.+?`).** That, not the order of the alternatives, is what splits `Iron Butterfly -In-A-Gadda-Da-Vida (Clear)` correctly.
- **The pressing bracket stays on the row's title.** It is what keeps two pressings of one album distinct under `item_key`, and `db._library_release_match_sql`'s exact-or-prefix-followed-by-a-space test still matches the catalog.
- **One row per product, at the cheapest in-stock variant.** Every variant shares `(artist, title, url)`, so a fan-out collides on `item_key`. The store publishes one variant per product today; the pick is kept as the shape that stays correct if that changes.
- **Only the literal `True` admits a variant.** The string `"false"` is truthy.
- **No pre-order handling.** Pre-orders report `available: true` and are purchasable at the listed price, so they are stock; a title marker would re-title every row on ship day and orphan its `item_key`.
- **`resolve_cover_image()` is called through a local type-guarding wrapper**, because the shared helper passes a retyped field straight to `.get()` and a raise there aborts the whole source over display-only artwork.
- **Identity is guarded before the artist, and variants before both** — a product that lost its title lost its artist source too, and one whose variants are unreadable has no readable stock flag either, so the wrong guard would otherwise answer.
- **The four "no rows AND" guards fire only on an empty outcome**; `price-source drift` fires only when no row at all carries a price.
- No comments except where the WHY is non-obvious.
- Registration is automatic via `main.py`'s bundled-crawler startup loop — no wiring changes anywhere else.
- Every commit carries the AI-attribution trailer block required by this repo's `CLAUDE.md`, created via `git commit -F <message-file>`, not `-m`.

Full grounding for every rule above: [`docs/specifications/shaping/2026-09-21-le-noise-crawler-design.md`](../shaping/2026-09-21-le-noise-crawler-design.md).

**Running the tests.** These tests mock HTTP with `respx` and never reach the store, but the `*_crawler` autouse fixture in `conftest.py` still resolves through the config layer, so run them with the three test env vars set. From `backend/`:

```bash
cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test \
  IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_lenoise_crawler.py -v
```

---

### Task 1: Le Noise crawler + tests

**Files:**
- Create: `backend/crawlers/lenoise.py`
- Test: `backend/tests/test_lenoise_crawler.py`

**Interfaces:**
- Consumes: `shopify_catalog.iter_products(base_url, collection_slug)`, `resolve_cover_image(product, variant)` — both exist unchanged in `backend/shopify_catalog.py`.
- Produces: a `Crawler` class with the standard `catalog` plugin surface (`site_name`, `base_url`, `genre_summary`, `genre`, `crawler_type`, `async def crawl_catalog()`), yielding `{"artist", "title", "format": "Vinyl", "price", "currency": "CAD", "url", "cover_image_url"}`.

- [x] **Step 1: Ground the design against the live store** — identify the platform, enumerate the collections, walk every page the vinyl shelf will serve and confirm the ceiling, histogram `product_type`, `vendor`, the tags and the option axes, tabulate every parenthetical descriptor, measure the separator characters and the three candidate whitespace rules against each other, check availability, prices, currency, images and `robots.txt`, and establish what the page ceiling actually excludes.
- [x] **Step 2: Write the crawler** — the `vinyl` shelf; the lowercased product_type gate; the two-sided bracket-scoped medium gate; the lazy at-least-one-side separator over the three live dashes; the pressing bracket kept on the title; one row per product at the cheapest in-stock price; a type-guarding cover wrapper; the ordered drift guards.
- [x] **Step 3: Write the test file** — fixtures marked `captured` or `invented` at their definition; cases per the design's Testing section.
- [x] **Step 4: Replay over the fully cached live catalog** — 25,000 products walked → 17,686 rows across 6,846 artists, no `item_key` collision, no blank artist or title, no null price, no URL off the store's product path, and no row naming a non-vinyl medium except the two vinyl boxes that bundle discs.
- [x] **Step 5: Run the test file** — all tests in it pass.
- [x] **Step 6: Mutation-check that each guard and rule bites** — mutate the crawler once per guard or rule and confirm the tests fail. Three mutations survived a first pass across the rounds below, and each was closed by adding a test rather than by weakening the mutation:
  - the medium gate reading the whole title rather than its brackets → closed by the captured `Lip Cream - Big Foot Cassette (Yellow)` product, a record whose *album* is named after a cassette;
  - the `images` container type guard → closed by giving a product a **non-iterable** `images`, since a retyped-but-iterable value such as a string survives the unguarded comprehension unharmed and so could not tell the two apart;
  - the `variant-identity-source drift` wording → closed by asserting the message text, since every test matched on the drift *name* alone and was blind to the thing that finding was about.

  Final run: no survivors.
- [x] **Step 7: Run the wider crawler test selection for regressions** (`pytest tests/ -k crawler` with the three test env vars set — the plugin loader imports every module in `backend/crawlers/`, so a syntax error in the new file breaks unrelated tests). The two order-dependent failures (`test_queue_router`, `test_crawler_crud`) reproduce identically with the new files removed, and each passes in isolation.

  **Correction, recorded rather than quietly dropped:** the first run of this step reported 155 errors in the Playwright-driven files and called them pre-existing. They were not repo state — they were this session's own environment missing Chromium. Once the `SessionStart` hook provisioned it, the same selection ran with zero errors. The "reproduces with the new files removed" check was sound and did establish the errors were not caused by this change; what it could not establish, and what was asserted anyway, is *why* they were failing.
- [x] **Step 8: Pre-PR spec-drift check** — grep both spec trees for the files, symbols and strings the diff touches; amend anything that has drifted; strip any inventory count found along the way.
- [x] **Step 9: Commit** via `git commit -F`, with trailers.
- [x] **Step 10: Work the review rounds on the PR.** Three rounds of Copilot findings, all verified against the code before acting and all real:
  - *Round 1* — partial variant corruption reaching no drift counter; `float()` accepting bools, non-finite values and zero as prices; the cover wrapper validating its containers but not the nested `src`. Each was this crawler copying an older sibling's shape instead of the hardened one the fleet had converged on, two of them hardened in response to the same reviewer on an earlier PR. The lesson worth carrying: matching *an* existing crawler is not the same as matching the current convention.
  - *Round 3* — the inch marker having no closing boundary, so `(12"CD)` read `12"` as a complete vinyl marker and the two-sided rule kept the CD; and the `variant-identity-source drift` message claiming "no readable variants" for the partial-corruption case it exists for.
  - *Round 4* — one suppressed (thread-less) finding, that this plan's Step 6 record had gone stale. Fixed above, along with the Step 7 claim it did not name.

  One finding is deliberately left open rather than closed: whether the store should ship enabled given its ~37 minutes of lock-held serial sync time. That depends on the deployment's `stock_schedule`, which is not readable from this repo, and the alternative remedy changes `_sync_stock` machinery every store depends on — out of a single crawler's blast radius, per the Waterloo design. Shipped enabled like every sibling, costed in the design doc, and flagged for the owner.
