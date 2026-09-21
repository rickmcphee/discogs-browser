# XL Recordings Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking. (This plan was executed inline in the session that wrote it — recorded here as the historical task log the plans tree is for.)

**Goal:** Add `backend/crawlers/xlrecordings.py`, a `crawler_type="catalog"` Shopify plugin covering XL Recordings' US storefront (`shopusa.xlrecordings.com`), and give `shopify_catalog.iter_products()` a `min_delay` passthrough so the store's `robots.txt` `Crawl-delay` is honoured by the design.

**Architecture:** Walk the store's built-in `all` collection via the existing `shopify_catalog.iter_products()` helper. The UK storefront named in the request (`shop.xlrecordings.com`) is not walkable at all — every path on it answers `202` with an empty body behind an `x-amzn-waf-action: challenge` header — so the US one is used, at the requester's direction, and prices are USD. The product title here is the album *alone*, so there is no `Artist - Album` split: the artist is `vendor`, falling back to the product's *sole* remaining tag when `vendor` names the shop rather than the act. The pressing descriptor is the tail of the variant title, split on the **last** spaced dash, because the album half carries its own separators. The format gate is per variant and its tail *admits*, since the live `Picture Disc` is a record naming no format word. Bundles are rejected on two independent signals, merch on `product_type` plus a trailing-garment-size rule. Only the literal `True` admits a variant; pre-orders are ordinary stock. Raise on an empty walk, on rows that all lack a price, and — whenever the walk yielded nothing — on a lost artist source, a lost identity, an unusably dropped variant, an unreadable availability flag, or no product claiming a format at all, so drift can never wipe the previous snapshot through `replace_stock_items()`.

**Tech Stack:** Python ≥3.9, `httpx` (via `shopify_catalog.iter_products`), `pytest`/`pytest-asyncio`, `respx` for HTTP mocking.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`; use `Optional[str]` or leave untyped.
- Reuse `shopify_catalog.iter_products()` and `resolve_cover_image()`. `has_tag` and `strip_vendor_prefix` are not used: the tags are read directly for the artist fallback, and no vendor prefix appears in a title.
- **One shared-module change:** `iter_products()` gains a keyword-only `min_delay: float = 0.0`, passed straight through to `catalog_http.get_with_retry()`, which already takes that floor. The default leaves every existing caller byte-for-byte unchanged.
- `format` is hardcoded `"Vinyl"`; `currency` is hardcoded `"USD"` (from the store's `meta.json`).
- **`_COLLECTION_SLUG = "all"`.** The store's curated `all-releases` shelf agreed exactly on the day of writing, so this is not a coverage decision but which kind of completeness to depend on: `all` cannot omit a published product, `all-releases` can be forgotten.
- **`_SITE_CRAWL_DELAY = 10.0`**, from the store's `robots.txt`. Unlike the sibling Shopify stores, this one names a `Crawl-delay`; `crawl_delay_seconds` is admin-editable with no lower bound, so the floor is passed rather than assumed.
- **The artist is `vendor` first, then the sole surviving tag.** `vendor` names the act on most of the catalogue and the shop on the rest; the label test is a normalised `xlrecordings` *prefix*, so a storefront opened later is read as the label rather than as an artist.
- **Non-artist tags are the release year, `12" singles`, `preorder` and `XL Merch`.** The year is matched by shape, not enumerated, or the crawler starts publishing an artist called "2027".
- **The tag fallback requires EXACTLY one survivor.** Shopify comma-splits tags and alphabetises them, so two survivors may equally be one split name or two collaborators — and a wrong artist is hashed into `item_key` and read by `db._library_release_match_sql`, producing a row that is permanently mis-keyed, unmatchable, and plausible enough that nobody goes looking for it.
- **`vendor` beats a tag wherever both name an artist.** Both live disagreements (`Tyler, The Creator`; `Gil Scott-Heron & Jamie xx`) have `vendor` right and the tag a fragment.
- **The descriptor is the LAST spaced-dash split of the variant title**, because a double A-side puts a `/` in the album half and a bundle puts a whole second product there. A title with no separator is the descriptor entire.
- **The per-descriptor gate's tail ADMITS.** The live `Picture Disc` is a record that names no format word; enumerating the record words would silently drop it and the next one the store invents.
- **`cs` is listed as another medium** — the store's own cassette abbreviation, which names no medium a reader would recognise. Safe only because the record word is tested first, which is what keeps the live `Deluxe 3X LP + CS + Books LP` box set in.
- **`print` is NOT a merch word**: two live records are pressings packaged with a signed print, priced as records.
- **Bundles are rejected on the title word `Bundle` OR more than one Shopify option**, never on the descriptor — a bundle's tail belongs to whichever half was written last. Both signals are kept because the record-plus-record bundle carries one option and reads as an ordinary pressing at a bundle price.
- **Merch is `product_type == "Merch"` plus a trailing-garment-size rule**, because the store leaves some merch untyped. The size pattern is anchored at the end and must reach `$`, so the `L` of an `LP` cannot satisfy it.
- **The pressing is appended to every row's title, after the album.** Every variant of a product shares the artist and URL, so the descriptor is what keeps `item_key` distinct; appending unconditionally keeps it stable when a sibling sells out; appending *after* the album keeps `db._library_release_match_sql`'s exact-or-prefix-with-space test matching.
- **Availability comes from `variant.available` and only the literal `True` admits a row.** No `(Pre-Order)` marker, though the store tags pre-orders: a marker that vanished on release would re-key every pressing and orphan the saves and judgments held against the old key.
- **Readability is judged over the admitted vinyl pressings only**, with `all()`, not `any()` — this store sells the record, the CD and the cassette as variants of one product, so a CD's unreadable flag must not condemn a record that was read perfectly.
- **A format-source guard EXISTS here**, unlike `bellaunion.py`, which documents needing none. The product-level gate here is positive — nothing but a variant descriptor claims a format — so its disappearance *can* silently empty the walk.
- **The drift guards are ordered most specific first, and the order is load-bearing.** Artist before identity (losing the artist sources also empties `_has_identity`); format last (anything that breaks titles or variants also stops the format claim).
- **All guards but `products_seen == 0` and `price-source` are gated on the walk yielding nothing**, since rows on the way out prove every source still answers. `price-source drift` fires only when no row at all carries a price; isolated nulls stay tolerated.
- No comments except where the WHY is non-obvious.
- Registration is automatic via `main.py`'s bundled-crawler startup loop — no wiring changes anywhere else.
- Every commit carries the AI-attribution trailer block required by this repo's `CLAUDE.md`, created via `git commit -F <message-file>`, not `-m`.

Full grounding for every rule above: [`docs/specifications/shaping/2026-09-21-xl-recordings-crawler-design.md`](../shaping/2026-09-21-xl-recordings-crawler-design.md).

**Running the tests.** These tests mock HTTP with `respx` and never reach the store, but the config layer still resolves through Postgres, so run them with the three test env vars set. From `backend/`:

```bash
cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test \
  IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_xlrecordings_crawler.py -v
```

---

### Task 1: `min_delay` passthrough on the shared Shopify walker

**Files:**
- Modify: `backend/shopify_catalog.py`

- [x] Add a keyword-only `min_delay: float = 0.0` to `iter_products()` and pass it to `get_with_retry()`.
- [x] Document in the docstring why the floor exists and that the default is a no-op for existing callers.
- [x] Confirm no sibling crawler's behaviour changes (every other caller omits the argument).

### Task 2: XL Recordings crawler + tests

**Files:**
- Create: `backend/crawlers/xlrecordings.py`
- Test: `backend/tests/test_xlrecordings_crawler.py`

- [x] Establish the storefront: confirm the UK host is WAF-challenged on every path and the US host serves the ordinary Shopify endpoints.
- [x] Ground the shape against the live catalogue: collection agreement, `product_type` vocabulary, vendor/tag split, variant-title convention, descriptor vocabulary, price and availability typing.
- [x] Implement `Crawler` with `site_name`, `base_url`, `genre`, `genre_summary`, `crawler_type = "catalog"`.
- [x] Implement artist resolution (`vendor` → sole tag), the last-dash descriptor split, the per-variant format gate, the bundle and merch gates, price and image reading.
- [x] Implement the drift guards in specificity order.
- [x] Write the test suite over captured live fixtures, covering row shape, artist resolution, descriptor splitting, the format gate's three outcomes, bundles, merch, availability, prices, every drift guard, pagination and the crawl-delay floor.
- [x] Replay the crawler over the live catalogue and audit every rejected variant and every row-less product by hand.

### Task 3: Pre-PR spec-drift check

- [x] `grep -rl` both spec trees for the files, symbols and strings this diff touches (`shopify_catalog`, `iter_products`, `min_delay`, `get_with_retry`).
- [x] Confirm each match still describes what shipped; amend anything that drifted.
- [x] Delete any crawler/store/source/plugin/test **count** encountered along the way rather than updating it.
