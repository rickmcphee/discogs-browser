# Save Jumps The Marketplace Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking. (This plan was executed inline in the session that wrote it, with the later tasks shaped by review rounds on the pull request — recorded here as the historical task log the plans tree is for, exactly as its predecessor [`2026-09-05-library-only-marketplace-crawl.md`](2026-09-05-library-only-marketplace-crawl.md) was.)

**Goal:** Clicking save on a Store item queues that item's marketplace crawl ahead of everything else, so the price comparison the click is asking for arrives on the next claim rather than at the scale of the next full drain.

**Architecture:** A `priority` column on `crawl_queue`, higher claimed sooner, leading `claim_crawl_queue_batch`'s sort *ahead* of the release/stock split — that split exists so bulk work cannot outrank a person, and an expedited row is one person waiting on one record. Only the save path writes a non-zero value. The save helper stops being insert-if-absent and becomes insert-or-expedite, branching on the row's state. Priority resets wherever `requested_at` already does, which is the line the table already draws between a fresh request and a row being handed back.

**Tech Stack:** Python ≥3.9, FastAPI, psycopg 3 against Postgres 16. No frontend change — the Store tab already refetches on the `listing_changed` the crawl broadcasts.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`.
- Only the save path writes a non-zero `priority`. Nothing scheduled, synced or admin-triggered reaches the lane.
- `priority` resets exactly where `requested_at` resets, and nowhere else.
- The enabled-store gate guards every path the save can take, including the expedite.
- Every taker of `STOCK_QUEUE_RECONCILE_LOCK_KEY` acquires it before its first `crawl_queue` row lock.
- A sort that decides which rows a batch takes is not the order they are returned in; where the order is the guarantee, sort again.
- No inventory counts in any document.
- Every commit carries the AI-attribution trailer block, via `git commit -F`.

Full grounding: [`docs/specifications/shaping/2026-09-19-save-jumps-the-marketplace-queue-design.md`](../shaping/2026-09-19-save-jumps-the-marketplace-queue-design.md).

---

### Task 1: The column, the sort, the save

**Files:** `backend/db.py`, `backend/routers/stock.py`, tests in `backend/tests/test_crawl_queue.py`, `test_stock_router.py`.

- [x] **Step 1: The column** — `priority INTEGER NOT NULL DEFAULT 0` on `crawl_queue`; `QUEUE_PRIORITY_INTERACTIVE`.
- [x] **Step 2: The index** — replace `crawl_queue_claimable_idx` with `crawl_queue_priority_claimable_idx`, `priority DESC` leading. A new name, because `CREATE INDEX IF NOT EXISTS` under an unchanged one is a no-op against a database holding the old definition.
- [x] **Step 3: The sort** — `priority DESC` leads in `claim_crawl_queue_batch` and in `queue_next_for_crawler`, whose contract is that it shows what the worker takes next.
- [x] **Step 4: Insert-or-expedite** — `enqueue_crawl_queue_for_saved_stock_item` inserts, revives a `done` row, or raises a `pending` row's priority, preserving both that row's `available_at` and its `pending_crawler_ids`; keeps the store gate and the reconciliation lock. (Task 8 later widens `pending_crawler_ids` unconditionally: preserving it was right for a deferral and wrong for a backfill revive.)
- [x] **Step 5: The resets** — `priority = 0` in the two routine revives, and nowhere else.
- [x] **Step 6: Tests** — each state of the save; the claim takes an expedited row ahead of a release row and is FIFO within the lane; the routine revives reset and a deferral does not; the gate on both save paths.
- [x] **Step 7: Spec drift** — sweep both trees; amend the six designs that state the old sort, the old index name or the old save behaviour.
- [x] **Step 8: Commit** via `git commit -F`, with trailers; open the PR ready for review.

### Task 2: Review round — a save on a claimed row, and the ETA

**Files:** `backend/db.py`, tests in `test_crawl_queue.py`, `test_queue_router.py`.

- [x] **Step 1:** A save on an `in_progress` row stamps the priority in a second statement and moves nothing else — a defer, revert or reclaim hands the row back without touching `priority`, so recording nothing would lose the click. `requested_at` deliberately not stamped with it.
- [x] **Step 2:** `_queue_crawler_eta` counts the expedited stock rows ahead of a release-only crawler; `_queue_row_state_sql` carries `expedited`, `_queue_totals` counts it.
- [x] **Step 3:** A `/next` ordering test — the existing claim-order test uses routine rows only and passes with or without the priority key.
- [x] **Step 4:** Correct the design's cross-tenant claim; the lane is not per-tenant, and saying so is not the same as building a quota.

### Task 3: Review round — the docs the last round left behind

- [x] **Step 1:** Every "leaves an `in_progress` row alone" now names the exception; the `requested_at` sentence scoped to its statement; the testing summary stops contradicting the paragraph below it.

### Task 4: Review round — idempotence and an API leak

**Files:** `backend/db.py`, tests in `test_crawl_queue.py`, `test_queue_router.py`.

- [x] **Step 1:** `requested_at` stamped only where something changes — the insert, a `done` revive, and promoting a routine `pending` row — so a repeated save is a true no-op, as a `PUT` should be. Pinned from both directions.
- [x] **Step 2:** The ETA's count renamed `_claimable_expedited_stock_rows` and popped before `queue_summary` returns `totals` verbatim; the endpoint test asserts no underscored key survives, so the next such value is caught without anyone remembering.
- [x] **Step 3:** Two comments still naming the renamed index.

### Task 5: Review round — the order a batch is returned in

**Files:** `backend/db.py`, tests in `test_crawl_queue.py`.

- [x] **Step 1:** The claim becomes a CTE with a final ordered `SELECT`: `UPDATE ... RETURNING` does not preserve its subquery's order, and `_process_claimed_rows` walks the list sequentially, so an expedited row could be crawled after a routine one claimed beside it.
- [x] **Step 2:** Both ordering tests assert on a multi-row batch, built so no ordering by `id` satisfies both — the FIFO case alone passes under `ORDER BY id DESC` by luck.
- [x] **Step 3:** Split the `pending` case in the code comment and the design's outcome list; promotion stamps `requested_at`, an already-expedited row changes nothing.

### Task 6: Review round — lock ordering

**Files:** `backend/db.py`, tests in `test_crawl_queue.py`.

- [x] **Step 1:** `backfill_crawl_queue_for_crawler` takes the reconciliation lock before its first row lock. Both its callers follow it with the sweep in the same transaction, so acquiring afterwards closed a cycle against a save holding the lock and waiting for a row — reachable only since the save became an upsert.
- [x] **Step 2:** Correct the design's rolling-deploy claim: a row failing to be expedited *is* reachable, on the read side, because an old worker claims by the old sort.

### Task 7: Review round — the test that did not test the ordering, and this plan

**Files:** `backend/tests/test_crawl_queue.py`, and this file.

- [x] **Step 1:** Task 6's lock-side test survives moving the acquisition below the `UPDATE`s, so it proved the lock is taken rather than taken first. Two tests now, named for what each proves; the new one makes the acquisition raise and reads the row back on the same uncommitted transaction.
- [x] **Step 2:** This plan, recorded as the task log.

### Task 8: Review round — a save widens, whatever narrowed the row

**Files:** `backend/db.py`, tests in `test_crawl_queue.py`.

- [x] **Step 1:** `pending_crawler_ids` is cleared unconditionally. Task 1 read a narrowed `pending` row as a deferral whose other crawlers had just run; `backfill_crawl_queue_for_crawler` is the second writer of that column and revives a *done* target narrowed to the newly enabled crawler, whose siblings' prices are as old as the last full pass. Nothing on the row tells the two apart.
- [x] **Step 2:** `available_at` keeps its `CASE` and is now the only one — it says *when* a row runs, not *which* crawlers do.
- [x] **Step 3:** Pinned twice, the second against a real backfill run whose precondition the test asserts, so it fails rather than passes vacuously if the backfill stops narrowing.

### Task 9: Review round — name the state a save cannot reach

**Files:** `backend/db.py`, design doc.

- [x] **Step 1:** Task 8's wording claimed a save means "everything eligible" in every state. It holds in every state that *statement* can reach; a claimed row's worker already holds its own snapshot, so a narrowed pass that completes consumes the save. Corrected rather than left overstated.
- [x] **Step 2:** Recorded as a known gap, the owner choosing to ship it documented rather than add a follow-up-request column and a conditional `mark_crawl_queue_done`.

### Task 10: Review round — the deploy window, and a declined finding

**Files:** `backend/routers/stock.py`, tests in `test_stock_router.py`, design doc.

- [x] **Step 1:** The lock-order rule holds within one version and a rolling deploy runs two, so the save retries once on a fresh transaction; both its statements are idempotent. The enable path already degraded on a deadlock, so the save was the only side that could lose.
- [x] **Step 2:** The design named the backfill as the writer that can leave an *expedited* pending row narrowed; it cannot, resetting `priority` as it narrows. `defer_crawl_queue_row` is the path.
- [x] **Step 3:** Declined, on the thread: clearing a stock item's listing on a confirmed miss. Pre-existing, outside this diff, and the surrounding code treats an empty stock result as uninformative for the same reason the breaker does.

### Task 11: Review round — pin the migration, not just the behaviour

**Files:** `backend/tests/test_global_schema.py`, design doc, this file.

- [x] **Step 1:** The functional ordering tests pass with the whole index migration deleted — Postgres sorts without an index. A schema test now recreates the old name, runs `init_global_schema`, and asserts it is gone and the replacement carries `priority DESC`; it fails under either half of the migration being removed.
- [x] **Step 2:** The library-only design's amendment and Task 1's Step 4 above still described the pre-Task-8 handling of `pending_crawler_ids`. Both corrected, and Tasks 8–11 added: this log had stopped at Task 7, which is what let its earliest description stand as the last word.

---
