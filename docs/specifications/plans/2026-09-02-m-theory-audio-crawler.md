# M-Theory Audio Store Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking. (This plan was executed inline in the session that wrote it — recorded here as the historical task log the plans tree is for.)

**Goal:** Add `backend/crawlers/mtheoryaudio.py`, a `crawler_type="catalog"` plugin covering the M-Theory Audio label store (`m-theoryaudio.com/store`) — a Bandzoogle storefront, a platform this repo has not crawled before.

**Architecture:** Fetch `/store`, read the store id and pager state off the store-feature wrapper, then follow `/go/stores/{id}/store_items?offset=N` — the endpoint the page's own lazy-load controller calls — until `data-load-more` reports `false`. Parse each `<article>` from the part before its `upsell-products` block. Take availability and item kind from the item's own cart form; skip `not-available` items and `Bundle` packages, keep pre-orders. Decide vinyl-ness from the title, falling back to the blurb, requiring an explicit positive signal because the platform publishes no format field anywhere. Split artist from album on the fleet's whitespace-bounded dash. Raise on every drift shape that would otherwise let a short or corrupt walk succeed, because `replace_stock_items()` deletes this crawler's rows before inserting.

**Tech Stack:** Python ≥3.9, `httpx` (via `catalog_http.get_with_retry`), `pytest`/`pytest-asyncio`, `respx` for HTTP mocking.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`; use `Optional[str]` or leave untyped.
- No new shared module, and exactly one scoped change to an existing one: `catalog_http.get_with_retry()` gains an opt-in `min_delay` (see the crawl-pacing constraint below), defaulting to `0` so no other caller changes. This store is not Shopify, so `shopify_catalog.py` does not apply; pacing and retries otherwise come from `get_with_retry` as-is, progress from `crawl_progress.report_page` unchanged.
- `format` is hardcoded `"Vinyl"`. `currency` is `"USD"` where there is a price and `None` where there is not — the platform publishes no currency code, so a price that is not a plain dollar amount **raises** rather than being recorded as USD.
- **Availability is `available`/`not-available` on the cart form, never `in-stock`.** The server renders `in-stock` on every item and the page's JavaScript flips it to `out-of-stock` from variant inventory, so both live sold-out items are `not-available … in-stock`. A test pins this.
- **Pass the site's `Crawl-delay` to `get_with_retry` as `min_delay`.** `crawl_delay_seconds` is admin-editable with no lower bound and the helper sleeps 50–100% of it, so robots.txt compliance cannot rest on the default. The helper floors both ends of its jitter window (flooring only the value still permits half the minimum) and defaults `min_delay` to 0, so no sibling crawler changes. This crawler's tests mock `catalog_http.sleep` rather than setting the delay to 0, because bypassing the floor is exactly what it exists to prevent.
- **Match articles as elements, then validate the id; never match articles *by* the id.** An article matched by its `data-store-item-id` is not matched at all when that id is malformed, and the stride check cannot see that on the final page, which is allowed to be short — so the crawl completes one record lighter and deletes it. A product article with no usable id raises; the platform's own `article.empty` placeholder is skipped as the non-product it is. Same reasoning for a heading that is missing *or* blank.
- **Read the blurb from its own `description` block, as stripped text.** Scanning the whole article lets a cover *filename* vote (`7mtp-lp-mockup.png` matches `\bLP\b`); keeping the tags lets a pasted link vote. Both are pinned by tests.
- **Pressing vocabulary (`repress`/`pressing`/`splatter`/`haze`/`marble`/`RSD`) is trusted in a title and not in a blurb.** A title names the edition on sale; a blurb talks about the release. Live proof: a $12 CD whose blurb says "from the 2023 repressing by Via Nocturna".
- **`tape`, `book`, `sticker`, `patch`, `poster` and `slipmat` stay out of the filters.** Each would drop live records; see the design spec before widening either pattern.
- **Every page inside the walk must carry exactly the pager stride.** The endpoint advances `data-offset` by the stride whatever it returns, so a short page means rows are being stepped over — and a successful short walk deletes stock rather than merely under-reporting it. Only the final page may be short.
- No comments except where the WHY is non-obvious.
- Registration is automatic via `main.py`'s `seed_bundled_crawlers()` — no wiring changes anywhere else.
- Every commit carries the AI-attribution trailer block required by this repo's `CLAUDE.md`, created via `git commit -F <message-file>`, not `-m`.

Full grounding for every rule above: [`docs/specifications/shaping/2026-09-02-m-theory-audio-crawler-design.md`](../shaping/2026-09-02-m-theory-audio-crawler-design.md).

**Running the tests.** These tests mock HTTP with `respx` and never reach the live site, but `tmp_config_dir` depends on `pg_test_db`, so the three database env vars must be set (`backend/.env` carries them in a cloud session). Run from `backend/`:

```bash
cd backend && pytest tests/test_mtheoryaudio_crawler.py tests/test_catalog_http.py -v
```

---

### Task 1: M-Theory Audio crawler + tests

**Files:**
- Create: `backend/crawlers/mtheoryaudio.py`
- Modify: `backend/catalog_http.py` (add `min_delay` to `get_with_retry`)
- Test: `backend/tests/test_mtheoryaudio_crawler.py`, `backend/tests/test_catalog_http.py`

**Interfaces:**
- Consumes: `catalog_http.get_with_retry(client, url, *, delay, failure_limit, params=None, min_delay=0.0)` — the `min_delay` argument is added by this task; `config.load_config()` and `crawl_progress.report_page(page, count)` exist unchanged.
- Produces: a `Crawler` class with the standard `catalog` plugin surface (`site_name`, `base_url`, `genre_summary`, `genre`, `crawler_type`, `async def crawl_catalog()`), yielding `{"artist", "title", "format": "Vinyl", "price", "currency", "url", "cover_image_url"}`.

- [x] **Step 1: Ground the design against the live site** — walk the whole store page by page and cache every response; confirm the pager contract (stride-advancing offset, `data-load-more` terminator, optional `store_feature_id`, empty past-the-end page), the per-article invariants, the absence of any format field in every JSON/structured-data form, and `robots.txt` for both requested paths.
- [x] **Step 2: Write the crawler** — pager guards, per-item parse scoped past the upsell block, availability and kind from the cart form, the two-stage format filter, the title split, and the whole-catalog raises.
- [x] **Step 3: Write the test file** — fixtures are the live markup trimmed to what is read, each case marked captured / altered / invented; cases per the design spec's Testing section.
- [x] **Step 4: Replay the shipped crawler over the fully-cached live catalog** — 252 items → 109 rows, no (artist, title, url) collisions, no duplicate URL, no blank artist or title, no row missing a price or cover, no whitespace contamination, one currency and format throughout.
- [x] **Step 5: Confirm each guard bites** — revert each in turn, re-run the suite, and check it fails only the cases written for it.
- [x] **Step 6: Add the pacing floor** — `min_delay` on `get_with_retry`, flooring both ends of the jitter window, with the site's `Crawl-delay` passed on every request; helper tests for the floor, the no-floor default and a delay already above it, and a crawler test asserting it reaches the helper on a real walk. This crawler's own tests mock `catalog_http.sleep`, since the floor is precisely what stops `crawl_delay_seconds: 0` from bypassing pacing.
- [x] **Step 7: Run the test files, then the wider `pytest tests/ -k "crawler or catalog_http"` selection for regressions** (the plugin loader imports every module in `backend/crawlers/`, so a syntax error in the new file breaks unrelated tests).
- [x] **Step 8: Commit** via `git commit -F`, with trailers.

---

## Post-implementation: pre-PR spec-drift check

- [x] **Grep both spec trees** (`docs/superpowers/specs/`, `docs/specifications/shaping/`) for the files, symbols, section names and UI strings this diff touches.
- [x] **Amend whatever drifted**, as its own commit, and note the findings in the PR description.
