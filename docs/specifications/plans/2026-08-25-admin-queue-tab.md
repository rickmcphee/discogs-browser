# Admin Queue Tab Implementation Plan

**Goal:** An admin-only **Queue** tab giving a live, read-only view of the shared crawl queue — its overall shape, its per-crawler fan-out, and a drill-down for one selected crawler.

**Architecture:** Two new admin-gated endpoints in `backend/routers/queue.py`, backed by `db.queue_summary`/`db.queue_next_for_crawler`. The per-crawler fan-out is computed as two small aggregates (broad rows with `pending_crawler_ids IS NULL`, narrowed rows `unnest`ed) combined in Python, never as a pending-rows-by-crawlers cross product. A new `frontend/src/views/QueueView.tsx` polls the summary while the tab is active and visible.

**Tech Stack:** Python 3, FastAPI, psycopg, Postgres, pytest (`asyncio_mode = "auto"`); React 19, TypeScript, Tailwind 4, Vitest. Charts are hand-rolled SVG — the frontend has no charting dependency and this adds none.

**Design spec:** [`docs/specifications/shaping/2026-08-25-admin-queue-tab-design.md`](../shaping/2026-08-25-admin-queue-tab-design.md)

**Verified against:** `main` @ `1650e36`.

## Global Constraints

- **Read-only.** No endpoint in this feature writes. In particular, do not add the reclaim path for stranded `in_progress` rows that `claim_crawl_queue_batch` documents as missing — the spec's non-goals put that in its own change.
- **Eligibility must mirror `get_eligible_crawlers` and `claim_crawl_queue_batch` exactly** — `enabled`, `crawler_type = 'release'`, the `requires_discogs_release` rule, `pending_crawler_ids`, `_enabled_stock_source_exists`, and `available_at`. A divergence is a bug in this feature.
- **Never materialize pending rows × crawlers.** A stock sync enqueues rows on an order that makes that cross product large enough to compete with the worker pool on every poll.
- **Two units, never conflated:** a *row* is one target (what drains, what the ETA counts); a *work unit* is one (row, crawler) pair (what a per-crawler number counts).
- Tests run from `backend/`: `cd backend && pytest`. Postgres-backed tests need `TEST_DATABASE_URL`, `IDENTITY_DB_PASSWORD=test`, `APP_DB_PASSWORD=test` (root `CLAUDE.md`, "Tests").

## File structure

| File | Task(s) | Responsibility after this plan |
|---|---|---|
| `backend/db.py` | 1 | `listings_crawler_last_checked_idx`; `queue_summary`, `queue_next_for_crawler` |
| `backend/routers/queue.py` | 2 | New admin-gated router: `GET /api/queue/summary`, `GET /api/queue/crawlers/{id}/next` |
| `backend/main.py` | 2 | Registers the router |
| `backend/tests/test_queue_router.py` | 3 | Admin gating, fan-out arithmetic, gates, stranded/unactionable, claim order |
| `frontend/src/api/types.ts` | 4 | `QueueSummary`, `QueueCrawlerSummary`, `QueueNextItem` |
| `frontend/src/api/client.ts` | 4 | `getQueueSummary`, `getQueueNext` |
| `frontend/src/views/QueueView.tsx` | 5 | Stat tiles, state donut, crawler bar list, three detail panels |
| `frontend/src/App.tsx` | 6 | `'queue'` view, admin-gated nav button, admin-gated mount |
| `frontend/src/test/queueView.test.tsx` | 7 | Rendering, selection, empty state |
| `frontend/src/test/accountNav.test.tsx` | 7 | Queue joins the existing admin nav-gating assertions |

---

### Task 1: `db.py` — index and the two query helpers

- [ ] Add `listings_crawler_last_checked_idx ON listings (crawler_id, last_checked)` beside the other `listings` indexes, so the per-crawler recency aggregate is an index scan.
- [ ] `queue_summary(conn)`: totals over `crawl_queue`; the broad and narrowed fan-out aggregates; the `listings` activity aggregate; combine per crawler in Python. Reuse `_enabled_stock_source_exists("crawl_queue.item_key")` for the stock gate rather than restating it.
- [ ] `queue_next_for_crawler(conn, crawler_id, limit)`: claim-order slice with `catalog`/`stock_item_identities` joins.
- [ ] Only statistics that *compose* across the broad/narrowed split may be per-crawler: `MIN(requested_at)` and bucketed age counts do; a median does not.

### Task 2: `routers/queue.py` and registration

- [ ] Both routes `dependencies=[Depends(require_admin)]`, reading through `db.get_app_pool()` (global tables, no per-user owner column, so not `user_scope`).
- [ ] Clamp `limit` on the `next` route.
- [ ] Register in `backend/main.py` alongside the other routers.

### Task 3: Backend tests

- [ ] Build queue state through the code under test (`enqueue_crawl_queue`, `enqueue_crawl_queue_for_stock_item`, `register_crawler`, `claim_crawl_queue_batch`, `defer_crawl_queue_row`) — never by hand-writing `crawl_queue` rows.
- [ ] Cover: admin gating on both routes; broad and narrowed fan-out; both target kinds; the `requires_discogs_release` exclusion; held vs claimable; the stock source gate; stranded and unactionable counts; `next` in claim order.

### Task 4: Frontend types and client

- [ ] Types mirroring the documented response shapes; `getQueueSummary`/`getQueueNext` following the existing `apiFetch` conventions.

### Task 5: `QueueView.tsx`

- [ ] Stat tiles; the three-segment work-unit donut on the validated ordinal ramp (`#86b6ef`, `#3987e5`, `#184f95`); the crawler bar list using emphasis, not per-crawler hues; the three detail panels.
- [ ] Poll every 10s, only while the tab is active and `document.visibilityState === 'visible'`.
- [ ] Label the two units distinctly wherever both appear; state the `results_last_hour` floor caveat inline.

### Task 6: `App.tsx` wiring

- [ ] `'queue'` in the `View` union, nav button gated on `showAdminNav`, and the view mounted only for an admin — matching how `LogViewer` is gated so a non-admin never opens its poll.

### Task 7: Frontend tests

- [ ] `queueView.test.tsx` for rendering, selection driving the detail fetch, and the empty state; extend `accountNav.test.tsx`'s admin-gating assertions to Queue.
