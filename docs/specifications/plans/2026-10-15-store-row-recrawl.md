# Re-crawl One Store Row Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `↻` button on each Store row puts that row's marketplace crawl at the front of the queue, so a user can ask for a fresh comparison on one record without saving it.

**Architecture:** No new queue logic. `POST /stock/{item_key}/recrawl` calls the existing insert-or-expedite helper the Save path uses, with the same lock-retry. The frontend adds a per-`item_key` pending button beside the bookmark; results arrive through the existing `listing_changed` refetch.

**Tech Stack:** Python ≥3.9, FastAPI, psycopg 3 against Postgres 16; React + Vite + TypeScript + Tailwind.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`.
- The endpoint writes no `stock_item_saves` row. Re-checking is not saving.
- It re-prices against marketplace crawlers only. It never starts or touches a catalog (store) sync.
- Every taker of `STOCK_QUEUE_RECONCILE_LOCK_KEY` acquires it before its first `crawl_queue` row lock — the helper already does; do not add a second path around it.
- At most one outstanding request per `item_key` in the UI.
- No inventory counts in any document.
- Every commit carries the AI-attribution trailer block, via `git commit -F`.

Full grounding: [`docs/specifications/shaping/2026-10-15-store-row-recrawl-design.md`](../shaping/2026-10-15-store-row-recrawl-design.md).

---

### Task 1: Establish what a stock `item_key` queue row prices

**Files:** none changed; findings recorded in the design's Open questions.

- [ ] **Step 1:** In `crawl_manager._drain_one_batch`, trace an `item_key` row end to end and confirm it runs the enabled release crawlers against the stock item (Amazon, eBay) and writes `listings` rows keyed by `item_key`.
- [ ] **Step 2:** Release-crawler-sourced stock rows never queue (`_enabled_stock_source_exists` counts catalog crawlers only) and get `reason: "not_listed"`. Check whether the row payload carries anything that says where it came from; if so, hide the button client-side on those rows, otherwise leave it showing its message. Record the outcome in the design.
- [ ] **Step 3:** Reproduce the motivating case: an Amazon comparison row with null `listing_title`. Confirm a fresh crawl of that `item_key` fills it, using the existing crawler test fixtures rather than live Amazon.

### Task 2: The endpoint

**Files:** `backend/routers/stock.py`, `backend/db.py` (docstring only), `backend/tests/test_stock_router.py`.

- [ ] **Step 1: Failing tests first.**
  - `POST /api/stock/{item_key}/recrawl` for an item whose store is enabled returns `{"queued": true, "reason": null}` and leaves a `crawl_queue` row at `QUEUE_PRIORITY_INTERACTIVE`.
  - For an item no enabled catalog store lists (unknown key, disabled store, release-crawler-sourced row), returns `{"queued": false, "reason": "not_listed"}` and creates no row.
  - With `crawl_library_only` on and no user interest in the item, returns `{"queued": false, "reason": "library_only"}`, creates no row, and leaves an already-existing row's priority untouched. The same item saved first queues normally. The test asserts the claim would actually take the row after a `true`, not only the response shape.
  - With `crawl_library_only` off, an item nobody owns queues.
  - A repeat call on an already-expedited row is a no-op: `requested_at` unchanged.
  - Writes no `stock_item_saves` row.
  - Unauthenticated request is rejected by `AuthMiddleware`.
- [ ] **Step 2:** Factor the save's one-retry-on-lock wrapper out of `save_stock_item` so both endpoints use it; keep its docstring's rolling-deploy reasoning on the shared helper.
- [ ] **Step 3:** In `db.py`, add the wrapper: it takes `_lock_stock_queue_reconciliation`, evaluates `_stock_item_crawlable(item_key, library_only)` with `library_only` a **required** argument, returns the reason when it declines, and otherwise calls `enqueue_crawl_queue_for_saved_stock_item`. The check and the enqueue run in one transaction under the lock the dead-row sweep also takes.
- [ ] **Step 4:** Add the route: read `config.crawl_library_only()` *before* borrowing the app connection, call the wrapper inside `db.user_scope`, commit, return `{"queued": ..., "reason": ...}`.
- [ ] **Step 5:** Update the expedite helper's docstring: it serves Save and re-crawl. Do not rename it.
- [ ] **Step 5:** Run `cd backend && pytest tests/test_stock_router.py tests/test_crawl_queue.py` with the three env vars from `CLAUDE.md`. Existing save tests must pass unchanged.
- [ ] **Step 6: Commit** via `git commit -F`, with trailers.

### Task 3: Client and button

**Files:** `frontend/src/api/client.ts`, `frontend/src/views/StockBrowser.tsx`, `frontend/src/App.tsx`, a test under `frontend/src/test/`.

- [ ] **Step 1: Failing tests first.**
  - Clicking `↻` on a row calls `POST /stock/{item_key}/recrawl` once; a second click while the first is pending sends nothing.
  - The status line is written through the `onStatus` callback `App.tsx` passes in: on `queued: true` it names the title; on `not_listed` and on `library_only` it says the matching message from the design.
  - After `queued: true`, the Store list is refetched at each fallback delay (fake timers), through the guarded refetch path, with no `listing_changed` event delivered — the cross-instance case. Nothing refetches after the last delay, and a `queued: false` schedules none.
  - A failed request clears the pending state and does not leave the button disabled.
  - The button renders in each layout a Save bookmark renders in.
- [ ] **Step 2:** `recrawlStockItem(itemKey)` beside `saveStockItem`.
- [ ] **Step 3:** `pendingRecrawls: Set<string>` state mirroring `pendingSaves`; a `recrawlItem` handler in the same shape as `toggleSaved`, without the optimistic patch (nothing on the row changes until `listing_changed`).
- [ ] **Step 3a:** Add an `onStatus?: (message: string) => void` prop to `StockBrowser` and pass `setSyncStatus` from `App.tsx` where `<StockBrowser … />` is rendered. Without it the component has no way to write to the shared status bar.
- [ ] **Step 3b:** The bounded fallback refetch: on `queued: true`, bump the existing `retryTick` at a few fixed delays (start with 15 s, 45 s and 2 min; adjust if the Queue tab shows crawls routinely take longer), clearing the timers on unmount. Idempotent and independent of which backend instance crawled.
- [ ] **Step 4:** The button, styled per the refresh-click-feedback design (spin and invert while busy, not dim), `title="Re-check marketplace prices"`, in all three row layouts beside the bookmark.
- [ ] **Step 5:** `cd frontend && npm run test` and the typecheck/lint the repo's CI runs.
- [ ] **Step 6: Commit.**

### Task 4: Spec drift sweep and PR

- [ ] **Step 1:** `grep -rl` across **both** `docs/superpowers/specs/` and `docs/specifications/shaping/` for `per-row refresh`, `refresh button`, `/stock/saved`, `enqueue_crawl_queue_for_saved_stock_item`, and the button's strings. Confirm each match still describes what shipped.
- [ ] **Step 2:** (Done on this branch in the docs PR.) Add the short note to the main design spec's 2026-08-27 amendment: the Collection and Wantlist per-row refresh stays removed; the Store tab has its own, see the new design. Delete any inventory count met along the way rather than refreshing it.
- [ ] **Step 3:** Commit amendments as their own commit and push.
- [ ] **Step 4:** Open the PR ready for review (not draft), noting the drift found and fixed. Do not enable auto-merge. Poll for Copilot's review on the final head SHA and close out its findings per `CLAUDE.md`.
- [ ] **Step 5:** Remove the worktree once the PR is open.
