# Joyful Noise Recordings Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking. (This plan was executed inline in the session that wrote it — recorded here as the historical task log the plans tree is for.)

**Goal:** Add `backend/crawlers/joyfulnoiserecordings.py`, a `crawler_type="catalog"` Shopify plugin covering Joyful Noise Recordings, the Indianapolis label and store (`joyfulnoiserecordings.com`).

**Architecture:** Walk the store's built-in `all` collection via `shopify_catalog.iter_products()`. The store *does* publish a `vinyl` shelf, and it is rejected on evidence rather than taste: that shelf is exactly the `Vinyl`-tagged products, and the tag is not how this store decides what a record is — 271 products outside it have an in-stock variant the gate reads as vinyl, including ordinary catalog LPs, and the 7" singles, flexi-discs and test pressings sit outside it almost entirely. Format lives in the variant under the store's `Format` option; `product_type` names the release *kind* and is actively misleading, typing every White Label Series record `Subscription`. The format gate is therefore two layers: the head (before the first parenthesis) decides whether some other medium owns the product, and the full string then has to show vinyl — because reading the whole string convicts every record of being a download, while reading the head alone loses the lathe-cut singles, which are titled with the song. The credit is `vendor`, except on the White Label Series where it names the series and the artist appears only in the title as `Artist 'Album'`. A variant with no usable price is skipped rather than listed blank, which departs from the sibling crawlers and is the one decision that needed its own drift guard.

**Tech Stack:** Python ≥3.9, `httpx` (via `shopify_catalog.iter_products`), `pytest`/`pytest-asyncio`, `respx` for HTTP mocking.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`; use `Optional[str]` or leave untyped.
- No new shared module — `shopify_catalog.iter_products()` and `resolve_cover_image()` are reused unchanged. No shared code is touched.
- `format` is hardcoded `"Vinyl"`; `currency` is hardcoded `"USD"`.
- **`_COLLECTION_SLUG = "all"`.** Confirmed to return exactly `meta.json`'s `published_products_count` and exactly the store-wide `/products.json` handle set. That check mattered: the store *also* publishes a hand-made collection titled "All" whose reported `products_count` is lower, and reading that number alone would have argued the walk onto a shelf.
- **The format gate is positive** — a variant must show vinyl to be admitted. That is the opposite polarity from the crawlers whose shelf has already vouched for the medium, and it is why the format-source drift guard exists.
- **The head is stripped of its trailing `+ Digital` before the medium veto runs**, anchored on the joining `+`/`&`, so the download that ships *with* a record cannot convict it while a variant that *is* the download stays vetoed.
- **Only a competing *physical* medium in the blurb vetoes.** Every legitimate record's blurb names a download, so the download words would veto everything.
- **Every string field is read through `_text`**, which requires `isinstance(..., str)`, unescapes HTML entities and normalises to NFC. The `or ""` idiom it replaces handed a truthy retyped value to `.strip()`/`.split()` and aborted the whole source over one malformed product.
- **Every token boundary is Unicode-aware and the inch marker is closed on both sides.** The left boundary sits before the *whole* marker with the multiplier absorbed into it, or `2x12"` is lost.
- **Multi-release lots are excluded** — their price is not any single record's. `Box Set` alone is deliberately not a bundle word; single releases ship that way.
- **The descriptor is the variant's whole title**, as every sibling uses. `item_key` hashes it, so it has to be a function of one variant and nothing else.
- **A variant with no usable price is skipped**, and the drift tally counts only prices that cannot be *read* — a zero reads fine, and the store means it.
- No comments except where the WHY is non-obvious.
- Registration is automatic via `main.py`'s bundled-crawler startup loop — no wiring changes anywhere else.
- Every commit carries the AI-attribution trailer block required by this repo's `CLAUDE.md`, created via `git commit -F <message-file>`, not `-m`.

Full grounding for every rule above: [`docs/specifications/shaping/2026-09-09-joyful-noise-recordings-crawler-design.md`](../shaping/2026-09-09-joyful-noise-recordings-crawler-design.md).

**Running the tests.** These tests mock HTTP with `respx` and never reach the store, but the `*_crawler` autouse fixture in `conftest.py` still resolves through the config layer, so run them with the three test env vars set. From `backend/`:

```bash
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test \
  IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test \
  python3 -m pytest tests/test_joyfulnoiserecordings_crawler.py -q
```

## Steps

- [x] **Investigate the live payload.** Establish which collection is the whole catalog (`meta.json` vs `collections.json` vs the built-in `all`), where format actually lives, and what `product_type`/tags are worth. Capture the catalog for replay.
- [x] **Write the crawler.** `crawl_catalog()` over `iter_products()`, the two-layer format gate, the bundle exclusion, the `vendor`-then-`Artist 'Album'` credit, the price rule, and the drift guards.
- [x] **Write the test suite** against captured live products, marking each fixture captured / altered / invented.
- [x] **Write the design doc** recording the evidence behind every rule.
- [x] **Replay the captured catalog** and confirm the emitted rows are all priced, all distinct, and name no merch, CD, cassette or download.
- [x] **Review rounds.** Copilot reviewed after every push. Each finding was reproduced against the code before being fixed, each has a regression test, and each test was confirmed to fail against the pre-fix code. One finding was verified **false** and the code deliberately left unchanged. The design doc was amended alongside every fix. See the PR for the full account — the recurring shapes were a drift tally that could not see the failure it was meant to catch, and a reader that crashed before its guard could report.

## Verification

Replaying the catalog captured 2026-09-09 yields 462 rows, every one priced, every one carrying a cover image, and all identities distinct. That figure held across every review round — each fix changed what the guards can see, never what the walk emits.

A note worth carrying into the next crawler of this kind: replaying a captured catalog shows what the crawler does with the payload it *has*, and says nothing about what the guards do when that payload changes — which is the only thing the guards are for. Several real defects here were invisible to replay and were found only by reading the code.
