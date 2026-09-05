# Library-Only Marketplace Crawling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking. (This plan was executed inline in the session that wrote it, with the later tasks shaped by review rounds on the pull request — recorded here as the historical task log the plans tree is for.)

**Goal:** An admin setting, `crawl_library_only`, off by default, shown as a "Library only" checkbox in Settings' Marketplace Management section. On, marketplace crawlers price only the stock items somebody wants — saved, or matching a record in some user's collection or wantlist. Off is the existing crawl-everything behaviour.

**Architecture:** The setting is a stock-item predicate applied where the enabled-store gate already is: the claim, the stock-sync enqueue and the dead-row sweep, through one composed predicate. "Someone wants it" is answered by an admin-owned view over `stock_item_saves` and the library match, which the unscoped worker role can read because a view is read as its owner. Interest arriving (a save, a library sync) ensures a queue row exists. Release rows are untouched.

**Tech Stack:** Python ≥3.9, FastAPI, psycopg 3 against Postgres 16; React + TypeScript + Vitest on the frontend.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`.
- `library_only` is only ever the setting's value as read by the caller (`config.crawl_library_only()`), read before an app-pool connection is borrowed, and composed into SQL in Python from a bool — never bound as a parameter, never request-derived.
- The view carries `item_key` only. No `user_id`, no other column, `UNION` not `UNION ALL`. No policy or grant change on `library_items` or `stock_item_saves`.
- The source gate requires a `catalog`/`catalog_browser` crawler: a marketplace result is never a stock target.
- The sweep and both interest helpers take the one reconciliation advisory lock.
- Interest helpers are insert-if-absent, never a revive.
- No inventory counts in any document.
- Every commit carries the AI-attribution trailer block, via `git commit -F`.

Full grounding: [`docs/specifications/shaping/2026-09-05-library-only-marketplace-crawl-design.md`](../shaping/2026-09-05-library-only-marketplace-crawl-design.md).

---

### Task 1: The setting, the view, the gate, the UI

**Files:** `backend/db.py`, `backend/config.py`, `backend/crawl_manager.py`, `backend/routers/settings.py`, `backend/routers/queue.py`, `frontend/src/views/Settings.tsx`, `frontend/src/api/types.ts`, tests in `backend/tests/test_crawl_queue.py`, `test_settings_router.py`, `test_queue_router.py`, `test_crawl_manager.py`, `frontend/src/test/settings.test.tsx`.

- [x] **Step 1: Verify the RLS mechanism** — prove against the local Postgres that a `NOBYPASSRLS` role reads every row through a superuser-owned view over a forced-RLS table and none through the table itself.
- [x] **Step 2: `library_stock_item_keys`** — extract `_library_release_match_sql` from `_library_match_fragment`; create the view in `init_tenant_schema` after `TENANT_SCHEMA`; grant `SELECT` to `app_user`.
- [x] **Step 3: `_stock_item_crawlable`** — compose the enabled-store gate with `_library_interest_exists` when `library_only`; thread the flag through `enqueue_crawl_queue_for_stock_item`, `claim_crawl_queue_batch`, `delete_dead_stock_crawl_queue_rows`, `_queue_row_state_sql`, `_queue_totals`, `_queue_fanout`, `queue_summary`, `queue_next_for_crawler`.
- [x] **Step 4: Readers** — `config.crawl_library_only()`; `_drain_one_batch` reads it per claim, `_sync_stock` per source and for its end-of-run sweep; `routers/queue.py` passes it to both reports.
- [x] **Step 5: Settings API and UI** — the field on `GET`/`POST /api/settings`; the off-to-on sweep returning `discarded`; a `checkbox` row type and the "Library only" row.
- [x] **Step 6: Tests** — view visibility and match rule through the real `app_user` role; claim, enqueue and sweep under both settings; settings round trip and edge-only sweep; Queue tab totals, `next`, and the plan regression check with the view joined; worker and stock sync under the setting; the checkbox.
- [x] **Step 7: Spec drift** — amend the dead-stock, fan-out and admin-queue-tab designs, the main design's settings table, and `CLAUDE.md`.
- [x] **Step 8: Commit** via `git commit -F`, with trailers; open the PR ready for review.

### Task 2: Review round — interest restores a missing row

**Files:** `backend/db.py`, `backend/routers/stock.py`, `backend/crawl_manager.py`, tests.

- [x] **Step 1:** `enqueue_crawl_queue_for_saved_stock_item`, run from the save endpoint in the save's transaction.
- [x] **Step 2:** `enqueue_crawl_queue_for_library_stock_items`, run at the end of a collection sync under the user's scope.
- [x] **Step 3:** Both insert-if-absent; tests for a missing row, a `done` row, and a disabled source; the sync test; the save endpoint test.
- [x] **Step 4:** Name both sweep causes in the stock sync's log line; close the pool in the `app_user_url` fixture.

### Task 3: Review round — indexes and a literal title match

- [x] **Step 1:** `library_items_wanted_discogs_id_idx`, partial over collected-or-wanted rows, beside `stock_item_saves_item_key_idx`.
- [x] **Step 2:** `starts_with` in place of `LIKE` for the title prefix; drop the percent-escaping switch; test `100% Fun`.

### Task 4: Review round — store inventory only, serialization, failure path

- [x] **Step 1:** `_enabled_stock_source_exists` and the Queue tab's live-keys set require a `catalog`/`catalog_browser` crawler; regression test on an `upsert_stock_item_from_release` row; the reversion-sweep test inserts its row directly.
- [x] **Step 2:** `STOCK_QUEUE_RECONCILE_LOCK_KEY`, taken by the sweep and both interest helpers; a lock-side test.
- [x] **Step 3:** The sync's restoration runs on its failure path too, best-effort; a test with a failing wantlist fetch.
- [x] **Step 4:** Name the two indexes in the design doc.

### Task 5: Review round — restoration only under the setting, and this plan

- [x] **Step 1:** `restore_library_stock_rows` reads the setting when it runs and skips itself when off; the two restoration tests turn the setting on; a test that the off case never calls the helper.
- [x] **Step 2:** This plan, recorded as the task log.

---
