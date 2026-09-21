# Cooking Vinyl Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking. (This plan was executed inline in the session that wrote it, whose harness offered no `superpowers:*` skill — recorded here as the historical task log the plans tree is for.)

**Goal:** Add `backend/crawlers/cookingvinyl.py`, a `crawler_type="catalog"` plugin covering the Cooking Vinyl label store (`cookingvinyl.tmstor.es`), which runs on Townsend Music's TM Stores platform.

**Architecture:** One paced `GET /productfeed` per sync, through `catalog_http.get_with_retry`. That endpoint is the store's own published Google-Merchant-flavoured RSS feed, and it is the *only* path an HTTP client can read: the storefront, its product pages and its sitemap all answer 403 behind a Cloudflare managed challenge, and the `/products` grid is client-rendered anyway. The feed is unpaginated and uncapped, and hands over `artist`, `name`, `price`, `currency`, `purl` and `imgurl` per product, so there is no title-splitting, no pagination and no detail fetch. The format scoping is done on the product `name` in three tests — a shape-matched vinyl word, no word naming another medium, and no `+` — and deliberately *not* on `google_product_category`, whose value is store-configured and blanket-set on the platform's flagship store. Raise on a body that is not this feed, on a feed with no products, on a catalog naming no vinyl, on a catalog whose every record reads as a bundle, on rows that all lack a price, and on a crawl that yielded nothing while any publishable product had lost its identity or its availability flag — so drift can never wipe the previous snapshot through `replace_stock_items()`.

**Tech Stack:** Python ≥3.9, `httpx` (via `catalog_http.get_with_retry`), `xml.etree.ElementTree`, `pytest`/`pytest-asyncio`, `respx` for HTTP mocking.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`; use `Optional[str]` or leave untyped.
- No new shared module. The feed parsing stays inline in this plugin until a second TM Stores plugin exists, per this repo's rule against abstractions without a clear reason.
- **The request must set an identifying `User-Agent`.** httpx's default `python-httpx/<version>` is blocklisted by Cloudflare on this host and gets the 403 interstitial. Reuse the identifying form `discogs_marketplace.py` already sends; do not impersonate a browser — an identifying agent passes, so the impersonation would buy nothing.
- **`_FEED_PATH = "/productfeed"`.** Not the storefront, not `/products`, not `/product/<id>` — all three are behind the challenge, and the grid carries no product markup even when fetched from the archive.
- `robots.txt` names no `Crawl-delay`, so `get_with_retry` needs no `min_delay` floor.
- **`format` is hardcoded `"Vinyl"`; `currency` is read per product from `<currency>`**, not hardcoded, even though every product sampled says `GBP`.
- **The format gate reads `name`, never `google_product_category`.** The category field is store-configured: the Townsend Music flagship publishes all 22,967 of its products under a blanket `166`, LPs included, and Cooking Vinyl has only ever carried one value, so it has never been shown to discriminate here. A category gate that stopped matching would complete empty and wipe the snapshot silently.
- **The vinyl test matches shapes, not literals**: `vinyl` as a substring, `\d*[x×]?d?lps?\d?` (for `2LP`, `2xLP`, `DLP`, `LP2`, none of which `\blps?\b` sees), `picture disc`, `test pressing`, and sizes 7/10/12 before an inch mark with an optional count.
- **Both extra tests are required and neither subsumes the other.** `+` catches the bundle that names no second medium ("A Matter of Time + Liquid Gold … Vinyl Represses"); the other-medium word list catches the bundles joined with `&` ("True North 2LP Heavyweight Vinyl & CD").
- **Every word in the other-medium list must have been read off a live bundle.** The test only ever sees a name that already named vinyl, so its whole exposure is a record whose *title* contains one of those words. `digital` and `cap` are deliberately excluded on those grounds — see the design doc.
- **`&` is not a bundle marker.** The store writes colours with it — "Changed Giver RSD 2024 Half White & Half Black Vinyl" is live — and every `&`-joined bundle already names its second medium.
- **The title is `<name>` verbatim.** No em-dash fence: this payload blends title and pressing into one string, so the fence position would be a guess, and a wrong guess is the false merge `title_key.py` errs away from. The cost is one judgment per colour variant, which is the cheap direction.
- **`Various Artists` and `Various` both become the bare `Various`** — Discogs' entity name, and what `amazon.py` and `db._library_release_match_sql` compare against.
- **`availability` admits the literals `in stock` and `preorder` and nothing else.** Both are purchasable; those are the only values observed; an unknown value is not assumed buyable. No pre-order marker on the title — `compute_item_key()` hashes it, and a marker that vanishes on release would re-key the row.
- **Price rejects non-finite values**, not just unparseable ones: `float("nan")` and `float("inf")` both parse.
- **Emptiness alone may never raise *in the identity, stock and price guards*.** Each is conditioned on a second tally, as in `musiconvinyl.py`, so a store that has simply sold out is allowed to be empty. This is deliberately not an absolute rule: the **catalog** guard does raise on a feed with no products at all, accepting that a genuine total sell-out trips it, because a raise keeps the previous snapshot while a completed-but-empty crawl wipes it. Do not "fix" that guard to match this line.
- No comments except where the WHY is non-obvious.
- No inventory counts in prose — see `CLAUDE.md`, "Documentation — never write down a count of things that change". Dated live-data findings are fine and are what the design doc records.
- Registration is automatic via `main.py`'s bundled-crawler startup loop — no wiring changes anywhere else.
- Every commit carries the AI-attribution trailer block required by this repo's `CLAUDE.md`, created via `git commit -F <message-file>`, not `-m`.

Full grounding for every rule above: [`docs/specifications/shaping/2026-09-21-cooking-vinyl-crawler-design.md`](../shaping/2026-09-21-cooking-vinyl-crawler-design.md).

**Running the tests.** These tests mock HTTP with `respx` and never reach the store, but the `*_crawler` autouse fixture in `conftest.py` still resolves through the config layer, so run them with the three test env vars set. From `backend/`:

```bash
cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test \
  IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_cookingvinyl_crawler.py -v
```

---

### Task 1: Cooking Vinyl crawler + tests

**Files:**
- Create: `backend/crawlers/cookingvinyl.py`
- Create: `backend/tests/test_cookingvinyl_crawler.py`

**Step 1 — the plugin.**

- [x] `Crawler` with `site_name = "Cooking Vinyl"`, `base_url = "https://cookingvinyl.tmstor.es"`, `crawler_type = "catalog"`, `genre = "rock"`, and a one-sentence `genre_summary` for the Settings tooltip.
- [x] `crawl_catalog()` reads `crawl_delay_seconds` and `consecutive_failure_limit` from `load_config()`, opens an `httpx.AsyncClient` on `base_url`, and makes one `get_with_retry` call to `_FEED_PATH` with the identifying `User-Agent` header.
- [x] Parse with `xml.etree.ElementTree`. A `ParseError`, a root that is not `rss`, or a missing `merchant` element raises the transport guard.
- [x] Walk `merchant/product`, tally `products_seen`, `vinyl_named`, `publishable`, `identity_missing`, `unrecognised_availability`, `yielded`, `priced`, and yield one item dict per admitted product. `vinyl_named` is counted before the bundle rejection and `publishable` after it, so the two guards over them can say which of the two emptied the walk.
- [x] `report_page(1, yielded)` after the walk — one request, so `report_page` is the whole progress signal.
- [x] Every guard in the design doc's table, in that order, each with the message the operator needs.

**Step 2 — the tests.**

- [x] Feed parsing: a fixture feed built from the live payload yields the expected item dicts, with `format="Vinyl"`, the artist, the verbatim name, the float price, the per-product currency, `purl` and `imgurl`.
- [x] The `User-Agent` header is actually sent (assert on the recorded request).
- [x] Gate: CDs, cassettes, downloads, apparel and `+`-joined bundles are rejected; `&`-joined bundles naming a second medium are rejected; `&`-in-a-colour records are **kept**.
- [x] Gate shapes: `2LP`, `2xLP`, `DLP`, `LP2`, `12"`, `picture disc`, `test pressing` all admit.
- [x] `Various Artists` → `Various`.
- [x] `preorder` is kept; an unrecognised availability value is dropped, and counts as unreadable rather than as sold-out stock — the feed drops sold-out products rather than flagging them, so a third value is drift.
- [x] Price: `"22.99"` → `22.99`; `""`, `"free"`, `"0"`, `"-1"`, `"nan"`, `"inf"` → `None`.
- [x] Each guard raises on the payload that should trip it, and does **not** raise on the neighbouring payload that should not — in particular, neither the identity nor the stock guard may fire while any row was yielded; a catalog of nothing but CDs and merchandise must raise the *format* guard; and a catalog of nothing but bundles must raise the *bundle* one, rather than either silently emptying the snapshot.

**Step 3 — verification.**

- [x] Run the new test file and confirm it passes.
- [x] Run the whole backend suite to confirm nothing else moved. *(CI `Backend tests` green on `52b99b7`, which is this branch's code — the only commit after it changes documentation alone. Also confirmed locally: `4814 passed, 0 failed, 155 errors`, every error in a Playwright-dependent file this container cannot run, which CI does run and passes.)*
- [x] Pre-PR spec-drift check across `docs/superpowers/specs/` and `docs/specifications/shaping/`.
