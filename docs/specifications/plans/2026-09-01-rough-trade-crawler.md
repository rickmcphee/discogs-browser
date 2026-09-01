# Rough Trade Release Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking. (This plan was executed inline in the session that wrote it — recorded here as the historical task log the plans tree is for.)

**Goal:** Add `backend/crawlers/roughtrade.py`, a `release`-type crawler for Rough Trade's `en-us` webstore that probes candidate product URLs constructed from the release's own artist/title and reads prices only from machine-readable signals.

**Architecture:** No catalog crawl and no search — `robots.txt` disallows every enumeration path for general-purpose clients (`*/collection/`, `*/genres/`, `*/search/`, `*/api/*`), and the amoeba spec's normative crawl-citizenship policy forbids working around that; product pages are allowed. `search(release, page)` slugifies artist/title into up to two candidate `/en-us/product/{artist}/{title}` URLs (`&`→`and` variant), treats 404 as a confirmed miss, waits out Cloudflare's interstitial, validates page identity from the confirmed `<title>` shape (exact artist segment, word-for-word title core, format-signal gate), and reads offers from Product JSON-LD scoped by canonical url/@id and exact node names, with an OG price-meta pair fallback. Every ambiguity — half-parsed offers, unattributable nodes, mixed currencies, unclassifiable landings — raises or misses; a wrong price is never persisted.

**Tech Stack:** Python ≥3.9, Playwright page supplied by the crawl manager, `pytest`/`pytest-asyncio` with a real local headless browser over constructed fixtures.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`.
- `requires_discogs_release = True` as an input-quality gate (the id is never read); `empty_result_is_expected = True` earned by the miss/failure separation.
- Never scrape visible `$` text; JSON-LD offers and OG meta pairs only.
- `[]` only for confirmed answers (404s, wrong-product landings with positive evidence, all-unpurchasable pages); unreadable pages raise.
- Registration is automatic via `main.py`'s `seed_bundled_crawlers()` — no wiring changes.
- Every commit carries the AI-attribution trailer block, created via `git commit -F`.

Full grounding: [`docs/specifications/shaping/2026-09-01-rough-trade-crawler-design.md`](../shaping/2026-09-01-rough-trade-crawler-design.md), including the robots.txt findings and the record of what could and could not be live-verified from the authoring sandbox (the first live run is the real verification pass).

**Running the tests.** Browser-backed but no live site; needs the Postgres test env vars from `CLAUDE.md`'s Tests section for the shared conftest:

```bash
cd backend && pytest tests/crawlers/test_roughtrade_crawler.py -v
```

---

### Task 1: Design doc

- [x] `docs/specifications/shaping/2026-09-01-rough-trade-crawler-design.md` — robots.txt findings (catalog design ruled out on policy), confirmed-vs-assumed grounding, crawler design, testing, load discipline, if-Rough-Trade-objects.

### Task 2: Crawler + tests

**Files:** `backend/crawlers/roughtrade.py`, `backend/tests/crawlers/test_roughtrade_crawler.py`, constructed fixtures under `backend/tests/fixtures/crawlers/roughtrade/` (marked as built to the schema.org/OpenGraph contract, not live captures).

- [x] Slug/candidate-URL construction, Cloudflare settle, 404/identity/format classification, JSON-LD + OG extraction, loud-drift raises.
- [x] Test file on the `discogs_marketplace`/`amoeba` `_FakePage` pattern; pure-helper tests plus end-to-end fixture cases for every outcome class.
- [x] Full backend suite run for regressions (the plugin loader imports every module in `backend/crawlers/`).

### Task 3: Review hardening

- [x] Ten Copilot review rounds on PR #277, each finding verified before fixing: identity-match strictness (artist boundary, word-for-word title core, truncation limits), Product-node scoping (canonical url/@id, exact names, unattributable-node poisoning), offer handling (half-parsed pages, boolean prices, node-reference/array availability, dropped payloads, mixed currencies), format gating (both title positions, positional extraction only, unknown-format passthrough), miss classification (positive evidence required), and the cleared-challenge signal-readiness poll.

## Post-implementation: pre-PR spec-drift check

- [x] Grep both spec trees for touched symbols; the fan-out specs' eligible-release-crawler enumerations stay accurate as written because this crawler sets `requires_discogs_release = True`. No document names Rough Trade. No drift found; none introduced.
