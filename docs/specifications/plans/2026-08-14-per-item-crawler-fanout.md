# Per-Item Marketplace Crawler Fan-Out Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `crawl_queue` hold one row per target and resolve the eligible marketplace-crawler set at dequeue time, so every enabled crawler runs for a target before work moves to the next one and enable/disable takes effect immediately.

**Architecture:** `crawl_queue` loses `crawler_id` and becomes unique on the target alone, gaining `pending_crawler_ids INTEGER[]` (per-pass progress; NULL = all currently eligible) and `available_at TIMESTAMP` (not-before marker for cooldown deferral). Producers enqueue targets without consulting `crawlers`. `_drain_one_batch` claims target rows, resolves eligibility per row against live `crawlers` state, flattens the batch into a target-major list of `(row, crawler)` work units, drains them with no barrier between targets, and defers cooling-down crawlers instead of waiting on them. Enabling a release crawler backfills the targets it has no priced listing for.

**Tech Stack:** Python 3, psycopg3, FastAPI, asyncio, Playwright, Postgres, pytest.

**Design spec:** [`docs/specifications/shaping/2026-08-14-per-item-crawler-fanout-design.md`](../shaping/2026-08-14-per-item-crawler-fanout-design.md)

**Verified against:** `origin/main` @ `ebf38f2`. Every snippet below matches the real current state of `backend/db.py`, `backend/crawl_manager.py`, `backend/routers/crawl.py`, and `backend/routers/settings.py` as of that commit. Verify against the real file before editing in case anything has changed since.

## Global Constraints

- **No migration tooling.** `GLOBAL_SCHEMA` is one idempotent SQL string executed at boot by `init_global_schema()`. Every schema change must be safe to re-run. Rewriting an index under an unchanged name via `CREATE INDEX IF NOT EXISTS` is a silent no-op on a database that already has the old definition — use a new index name plus an explicit `DROP INDEX IF EXISTS` of the old one.
- **Release rows are claimed ahead of stock-item rows.** `ORDER BY (item_key IS NOT NULL), requested_at, id` — priority within a batch, not exclusion.
- **No NULL-price placeholder** is created for a target that has not been crawled. A missing `listings` row still means "not yet crawled". `clear_listing_price` may leave a NULL-price row behind for a target that *was* crawled and found nothing.
- **Only `matches[0]` is stored.**
- **One short-lived connection per unit of work**, committed per unit, never spanning Playwright calls.
- **One request per site in flight process-wide**, via the per-`crawler_id` lock in `_paced_search`, spanning the bot-detection retry.
- **Failure domains:** crawlers declaring the same `failure_domain` share breaker state; `_record_site_result` applies results to domain peers, keyed by `crawler_id`.
- **Empty stock-item results carry no site-health signal** — only bot detection or a match is recorded for `item_key` targets.
- **The stock source gate** (`_enabled_stock_source_exists`) stays on both the stock enqueue path and the claim.
- **No new retry semantics.** Work is one-shot per pass; cooldown deferral is the only re-schedule path. No `listings.last_checked` freshness logic.
- `batch_size` for `_drain_one_batch` is **2**, not 5.
- Tests run from `backend/`: `cd backend && pytest`. The suite provisions its own Postgres database per session via the `pg_test_db` / `pg_schema` fixtures in `backend/tests/conftest.py`.

## File structure

| File | Task(s) | Responsibility after this plan |
|---|---|---|
| `backend/db.py` | 1, 2, 3 | Item-level `crawl_queue` schema + collapse migration; target-keyed enqueue helpers; claim honouring `available_at`; `defer_crawl_queue_row`; `get_eligible_crawlers`; `backfill_crawl_queue_for_crawler`; pending count without a `crawler_id` join |
| `backend/crawl_manager.py` | 1, 2 | `_cooldown_remaining_seconds`; producers enqueue targets only; `_drain_one_batch` fans out per target and defers cooling-down crawlers |
| `backend/routers/crawl.py` | 2 | `POST /crawl/start` enqueues one row per target; `enqueued` counts targets |
| `backend/routers/settings.py` | 2, 3 | Disable no longer purges per-crawler rows; enable backfills |
| `backend/tests/test_crawl_queue.py` | 1, 2, 3 | Queue-level semantics: availability gate, deferral, collapse migration, target-level idempotency, pending count, backfill |
| `backend/tests/test_crawl_manager.py` | 1, 2 | Cooldown-remaining helper; fan-out, live enablement, deferral, target-major drain |
| `backend/tests/test_crawl_router.py`, `test_settings_router.py`, `test_crawler_crud.py`, `test_stock_crud.py` | 2 | Updated for the new `enqueue_crawl_queue` signature and target-based counts |
| `CLAUDE.md` | 2, 3 | Key invariants describe dispatch-time crawler resolution |

---

### Task 1: Additive schema, availability gate, and deferral primitives

Everything in this task is additive and leaves the existing pair-row behaviour intact, so the suite stays green. It builds the two columns and the two helpers that Task 2 needs.

**Files:**
- Modify: `backend/db.py` (`GLOBAL_SCHEMA` around line 147; `claim_crawl_queue_batch` at line 934; new function after `mark_crawl_queue_done` at line 977)
- Modify: `backend/crawl_manager.py` (new method next to `_cooling_down_crawler_ids` at line 130)
- Test: `backend/tests/test_crawl_queue.py`, `backend/tests/test_crawl_manager.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `db.defer_crawl_queue_row(conn, queue_id: int, crawler_ids: list, delay_seconds: float) -> None`
  - `CrawlManager._cooldown_remaining_seconds(self, crawler_ids: list) -> float`
  - `crawl_queue.pending_crawler_ids INTEGER[]` (nullable), `crawl_queue.available_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP`
  - `claim_crawl_queue_batch` additionally filters `available_at <= CURRENT_TIMESTAMP`

- [ ] **Step 1: Write the failing tests for the availability gate and deferral**

Add to `backend/tests/test_crawl_queue.py`. `admin_conn` and `_make_catalog_and_crawler` are the existing fixtures/helpers at the top of that file — reuse them, don't redefine them.

```python
def test_claim_crawl_queue_batch_skips_rows_not_yet_available(admin_conn):
    crawler_id = _make_catalog_and_crawler(admin_conn, "r1")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1", crawler_id)
    admin_conn.execute(
        "UPDATE crawl_queue SET available_at = CURRENT_TIMESTAMP + INTERVAL '1 hour' WHERE discogs_id = 'r1'"
    )
    admin_conn.commit()

    claimed = db.claim_crawl_queue_batch(admin_conn, "worker-1", limit=10)

    assert claimed == []


def test_claim_crawl_queue_batch_claims_rows_whose_availability_has_passed(admin_conn):
    crawler_id = _make_catalog_and_crawler(admin_conn, "r1")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1", crawler_id)
    admin_conn.execute(
        "UPDATE crawl_queue SET available_at = CURRENT_TIMESTAMP - INTERVAL '1 second' WHERE discogs_id = 'r1'"
    )
    admin_conn.commit()

    claimed = db.claim_crawl_queue_batch(admin_conn, "worker-1", limit=10)

    assert len(claimed) == 1


def test_defer_crawl_queue_row_reopens_the_row_with_a_narrowed_crawler_set(admin_conn):
    crawler_id = _make_catalog_and_crawler(admin_conn, "r1")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1", crawler_id)
    admin_conn.commit()
    claimed = db.claim_crawl_queue_batch(admin_conn, "worker-1", limit=10)
    before = admin_conn.execute(
        "SELECT requested_at FROM crawl_queue WHERE id = %s", [claimed[0]["id"]]
    ).fetchone()["requested_at"]

    db.defer_crawl_queue_row(admin_conn, claimed[0]["id"], [crawler_id], 1800.0)
    admin_conn.commit()

    row = admin_conn.execute(
        "SELECT status, claimed_by, claimed_at, pending_crawler_ids, requested_at, "
        "available_at > CURRENT_TIMESTAMP + INTERVAL '25 minutes' AS deferred_far_out "
        "FROM crawl_queue WHERE id = %s",
        [claimed[0]["id"]],
    ).fetchone()
    assert row["status"] == "pending"
    assert row["claimed_by"] is None
    assert row["claimed_at"] is None
    assert row["pending_crawler_ids"] == [crawler_id]
    assert row["deferred_far_out"] is True
    # requested_at is deliberately untouched: a deferred row returns near its
    # original queue position rather than at the back of the queue.
    assert row["requested_at"] == before
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_crawl_queue.py -k "not_yet_available or availability_has_passed or defer_crawl_queue_row" -v`
Expected: FAIL — `column "available_at" does not exist` on the first two, `AttributeError: module 'db' has no attribute 'defer_crawl_queue_row'` on the third.

- [ ] **Step 3: Add the two columns to `GLOBAL_SCHEMA`**

In `backend/db.py`, immediately after the existing `CREATE UNIQUE INDEX IF NOT EXISTS crawl_queue_item_key_crawler_idx ...` line (line 149):

```sql
-- pending_crawler_ids is the per-pass progress record: NULL means "every
-- crawler currently eligible for this target", a non-NULL array narrows the
-- next pass to unfinished work from an earlier one. listings cannot serve
-- this purpose -- an empty result writes no row at all, and clear_listing_price
-- leaves a NULL-price row behind, so neither absence nor presence of a row
-- distinguishes "not attempted" from "attempted, found nothing".
ALTER TABLE crawl_queue ADD COLUMN IF NOT EXISTS pending_crawler_ids INTEGER[];

-- available_at is a not-before marker, set when a pass defers a crawler whose
-- site is in circuit-breaker cooldown. Without it the row would be re-claimed
-- on the very next batch and re-deferred in a hot loop.
ALTER TABLE crawl_queue ADD COLUMN IF NOT EXISTS available_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP;
```

- [ ] **Step 4: Add the availability filter to the claim**

In `claim_crawl_queue_batch` (line 934), add the filter directly below the existing `WHERE status = 'pending'` line, above the enabled-crawler gate:

```sql
            WHERE status = 'pending'
              AND available_at <= CURRENT_TIMESTAMP
              AND crawler_id IN (SELECT id FROM crawlers WHERE enabled)
```

Leave the rest of that query — the source gate, the exclusion clause, the `ORDER BY`, the `RETURNING` list — untouched in this task.

- [ ] **Step 5: Add `defer_crawl_queue_row`**

In `backend/db.py`, directly after `mark_crawl_queue_done` (line 977):

```python
# The inverse of mark_crawl_queue_done: hands a claimed row back as pending
# with only its unfinished crawlers, deferred until the earliest moment one of
# them is workable again. requested_at is deliberately not touched -- bumping
# it would send a row that merely waited on a cooling-down site to the back of
# the queue behind everything enqueued while it waited.
def defer_crawl_queue_row(conn, queue_id: int, crawler_ids: list, delay_seconds: float):
    conn.execute(
        """
        UPDATE crawl_queue
        SET status = 'pending', claimed_by = NULL, claimed_at = NULL,
            pending_crawler_ids = %(crawler_ids)s,
            available_at = CURRENT_TIMESTAMP + (%(delay_seconds)s * INTERVAL '1 second')
        WHERE id = %(queue_id)s
        """,
        {"queue_id": queue_id, "crawler_ids": list(crawler_ids), "delay_seconds": delay_seconds},
    )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_crawl_queue.py -v`
Expected: PASS — the three new tests plus every pre-existing test in the file, proving the columns and the extra filter are backward-compatible.

- [ ] **Step 7: Write the failing test for `_cooldown_remaining_seconds`**

Add to `backend/tests/test_crawl_manager.py`, next to the existing circuit-breaker tests:

```python
def test_cooldown_remaining_seconds_returns_the_earliest_expiry():
    import time
    manager = CrawlManager()
    now = time.monotonic()
    manager._site_cooldown_until = {1: now + 600, 2: now + 120}

    remaining = manager._cooldown_remaining_seconds([1, 2])

    assert 100 < remaining <= 120


def test_cooldown_remaining_seconds_is_zero_when_nothing_is_cooling_down():
    manager = CrawlManager()
    manager._site_cooldown_until = {}

    assert manager._cooldown_remaining_seconds([1, 2]) == 0.0


def test_cooldown_remaining_seconds_ignores_expired_cooldowns():
    import time
    manager = CrawlManager()
    now = time.monotonic()
    manager._site_cooldown_until = {1: now - 5, 2: now + 300}

    remaining = manager._cooldown_remaining_seconds([1, 2])

    assert 250 < remaining <= 300
```

- [ ] **Step 8: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_crawl_manager.py -k cooldown_remaining -v`
Expected: FAIL — `AttributeError: 'CrawlManager' object has no attribute '_cooldown_remaining_seconds'`.

- [ ] **Step 9: Implement `_cooldown_remaining_seconds`**

In `backend/crawl_manager.py`, directly after `_cooldown_remaining_seconds`'s sibling `_cooling_down_crawler_ids` (line 130):

```python
    # Earliest expiry, not latest: the row should come back as soon as any one
    # of its deferred crawlers is workable again. The rest stay narrowed into
    # pending_crawler_ids and get deferred again if they are still cooling.
    # Converts monotonic deadlines to a relative delay because the caller
    # writes a wall-clock available_at.
    def _cooldown_remaining_seconds(self, crawler_ids: list) -> float:
        import time
        now = time.monotonic()
        remaining = [
            self._site_cooldown_until[cid] - now
            for cid in crawler_ids
            if cid in self._site_cooldown_until and self._site_cooldown_until[cid] > now
        ]
        return min(remaining) if remaining else 0.0
```

- [ ] **Step 10: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_crawl_manager.py -k cooldown -v`
Expected: PASS — the three new tests plus the existing cooldown/circuit-breaker tests.

- [ ] **Step 11: Commit**

```bash
git add backend/db.py backend/crawl_manager.py backend/tests/test_crawl_queue.py backend/tests/test_crawl_manager.py
git commit -m "feat: add crawl_queue availability gate and deferral primitives"
```

---

### Task 2: Collapse to one row per target and fan out at dispatch

This task is deliberately large and atomic. The unique index on `discogs_id` alone cannot exist while pair rows do, and dropping `crawler_id` breaks every function and call site that reads it, so the schema collapse, the `db` function reshape, the producers, and the dispatch loop must land in one commit or the suite cannot be green at any intermediate point.

**Files:**
- Modify: `backend/db.py` — `GLOBAL_SCHEMA` (`CREATE TABLE crawl_queue` at line 114; index at line 136; ALTERs at lines 147-149), grant comment (lines 424-429), `enqueue_crawl_queue` (line 894), `enqueue_crawl_queue_for_stock_item` (line 908), `claim_crawl_queue_batch` (line 934), `delete_pending_crawl_queue_for_crawler` (delete it), `count_pending_crawl_queue_for_user` (line 1022), new `get_eligible_crawlers`
- Modify: `backend/crawl_manager.py` — `_drain_one_batch` (line 243), collection enqueue (line 491), wishlist enqueue (line 533), `sweep_enqueue` (line 575), `_sync_stock` fan-out (line 757)
- Modify: `backend/routers/crawl.py` — `crawl_start` (line 30)
- Modify: `backend/routers/settings.py` — `update_crawler` (line 69)
- Modify: `CLAUDE.md` — the "Key invariants" bullets on lines 41-43
- Test: `backend/tests/test_crawl_queue.py`, `backend/tests/test_crawl_manager.py`, `backend/tests/test_crawl_router.py`, `backend/tests/test_settings_router.py`, `backend/tests/test_crawler_crud.py`, `backend/tests/test_stock_crud.py`

**Interfaces:**
- Consumes: `db.defer_crawl_queue_row(conn, queue_id, crawler_ids, delay_seconds)` and `CrawlManager._cooldown_remaining_seconds(crawler_ids)` from Task 1.
- Produces:
  - `db.enqueue_crawl_queue(conn, discogs_id: str) -> None` (crawler argument removed)
  - `db.enqueue_crawl_queue_for_stock_item(conn, item_key: str) -> None` (crawler argument removed)
  - `db.claim_crawl_queue_batch(conn, worker_id: str, limit: int) -> list[dict]` returning rows with keys `id, discogs_id, item_key, pending_crawler_ids` (`excluded_crawler_ids` parameter removed)
  - `db.get_eligible_crawlers(conn, is_release: bool, pending_crawler_ids: Optional[list]) -> list[dict]`
  - `db.delete_pending_crawl_queue_for_crawler` no longer exists

- [ ] **Step 1: Write the failing test for the collapse migration**

Add to `backend/tests/test_crawl_queue.py`. The helper recreates the pre-migration table shape so the upgrade path can be exercised on a database that `init_global_schema()` has already brought up to the new shape.

```python
def _recreate_legacy_crawl_queue(conn):
    """Rebuild the pre-collapse (target, crawler) table shape so the migration
    in GLOBAL_SCHEMA has something to upgrade. A fresh test database is created
    at the new shape, where the migration's guard is false and it does nothing."""
    conn.execute("DROP TABLE IF EXISTS crawl_queue CASCADE")
    conn.execute("""
        CREATE TABLE crawl_queue (
            id SERIAL PRIMARY KEY,
            discogs_id TEXT REFERENCES catalog(discogs_id),
            crawler_id INTEGER NOT NULL REFERENCES crawlers(id),
            requested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            status TEXT NOT NULL DEFAULT 'pending',
            claimed_by TEXT,
            claimed_at TIMESTAMP,
            item_key TEXT REFERENCES stock_item_identities(item_key),
            pending_crawler_ids INTEGER[],
            available_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(discogs_id, crawler_id)
        )
    """)
    conn.commit()


def test_migration_collapses_pairs_into_one_row_per_target(admin_conn):
    crawler_a = _make_catalog_and_crawler(admin_conn, "r1", site_name="Amazon")
    crawler_b = _make_catalog_and_crawler(admin_conn, "r1", site_name="eBay")
    admin_conn.commit()
    _recreate_legacy_crawl_queue(admin_conn)
    admin_conn.execute(
        "INSERT INTO crawl_queue (discogs_id, crawler_id, status) VALUES "
        "('r1', %s, 'done'), ('r1', %s, 'pending')",
        [crawler_a, crawler_b],
    )
    admin_conn.commit()

    db.init_global_schema()

    rows = admin_conn.execute(
        "SELECT status, pending_crawler_ids FROM crawl_queue WHERE discogs_id = 'r1'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"
    # Only the unfinished pair's crawler carries over -- the 'done' one already ran.
    assert rows[0]["pending_crawler_ids"] == [crawler_b]
    columns = admin_conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'crawl_queue'"
    ).fetchall()
    assert "crawler_id" not in [c["column_name"] for c in columns]


def test_migration_collapses_all_done_pairs_to_one_done_row(admin_conn):
    crawler_a = _make_catalog_and_crawler(admin_conn, "r1", site_name="Amazon")
    crawler_b = _make_catalog_and_crawler(admin_conn, "r1", site_name="eBay")
    admin_conn.commit()
    _recreate_legacy_crawl_queue(admin_conn)
    admin_conn.execute(
        "INSERT INTO crawl_queue (discogs_id, crawler_id, status) VALUES "
        "('r1', %s, 'done'), ('r1', %s, 'done')",
        [crawler_a, crawler_b],
    )
    admin_conn.commit()

    db.init_global_schema()

    row = admin_conn.execute(
        "SELECT status, pending_crawler_ids FROM crawl_queue WHERE discogs_id = 'r1'"
    ).fetchone()
    assert row["status"] == "done"
    assert row["pending_crawler_ids"] is None


def test_migration_is_a_no_op_on_an_already_collapsed_table(admin_conn):
    crawler_id = _make_catalog_and_crawler(admin_conn, "r1")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()

    db.init_global_schema()

    rows = admin_conn.execute("SELECT status FROM crawl_queue WHERE discogs_id = 'r1'").fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_crawl_queue.py -k migration -v`
Expected: FAIL — the collapse does not exist, so the first test still sees two rows and a `crawler_id` column; the third fails with `TypeError: enqueue_crawl_queue() missing 1 required positional argument: 'crawler_id'`.

- [ ] **Step 3: Reshape `crawl_queue` in `GLOBAL_SCHEMA`**

In `backend/db.py`, replace the `CREATE TABLE IF NOT EXISTS crawl_queue (...)` block (line 114) with the new shape — new installs get it directly, so the migration below never fires for them:

```sql
CREATE TABLE IF NOT EXISTS crawl_queue (
    id SERIAL PRIMARY KEY,
    discogs_id TEXT REFERENCES catalog(discogs_id),
    requested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'pending',
    claimed_by TEXT,
    claimed_at TIMESTAMP
);
```

Then replace the old index block (the comment plus `CREATE INDEX IF NOT EXISTS crawl_queue_pending_idx ...`, lines 125-137) with a composite index under a **new name**, since rewriting one under the old name would be a silent no-op on existing databases:

```sql
-- Serves claim_crawl_queue_batch's WHERE/ORDER BY directly: leading
-- (item_key IS NOT NULL) matches the release-before-stock sort, then
-- requested_at, id for FIFO within a kind. Partial so it stays small as rows
-- accumulate 'done' history. available_at is deliberately not a key column --
-- deferred rows are a small minority, so keeping the index ordered for the
-- sort beats indexing the filter. Named differently from the
-- crawl_queue_pending_idx it replaces because CREATE INDEX IF NOT EXISTS
-- under an unchanged name is a no-op against a database that already has the
-- old definition.
DROP INDEX IF EXISTS crawl_queue_pending_idx;
CREATE INDEX IF NOT EXISTS crawl_queue_claimable_idx
    ON crawl_queue ((item_key IS NOT NULL), requested_at, id)
    WHERE status = 'pending';
```

Leave the `ALTER TABLE crawl_queue ALTER COLUMN discogs_id DROP NOT NULL;` and `ADD COLUMN IF NOT EXISTS item_key ...` lines (147-148) in place — they are no-ops on the new shape and still needed by older databases. Replace line 149's `crawl_queue_item_key_crawler_idx` creation with the collapse migration and the new unique indexes.

**Statement order matters here.** The collapse writes `pending_crawler_ids`, so it must come *after* the two `ADD COLUMN IF NOT EXISTS` statements Task 1 added — on a legacy database those columns do not exist until those statements run. Place the block below in that position: after Task 1's `pending_crawler_ids`/`available_at` ALTERs, replacing the `crawl_queue_item_key_crawler_idx` line.

```sql
-- One row per target, not per (target, crawler) pair: the crawler set is a
-- runtime decision resolved by _drain_one_batch against live crawlers state,
-- not something frozen into row data at enqueue time. Guarded on crawler_id
-- still existing so re-runs are no-ops.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'crawl_queue' AND column_name = 'crawler_id'
    ) THEN
        -- The surviving row per target is its lowest id. pending_crawler_ids
        -- inherits exactly that target's unfinished crawlers, so a pass that
        -- was in flight when the upgrade ran resumes with the right set
        -- instead of re-running crawlers that already finished.
        WITH collapsed AS (
            SELECT COALESCE(discogs_id, item_key) AS target,
                   MIN(id) AS keep_id,
                   array_agg(crawler_id ORDER BY crawler_id)
                       FILTER (WHERE status <> 'done') AS unfinished
            FROM crawl_queue
            GROUP BY COALESCE(discogs_id, item_key)
        )
        UPDATE crawl_queue cq
        SET status = CASE WHEN c.unfinished IS NULL THEN 'done' ELSE 'pending' END,
            pending_crawler_ids = c.unfinished,
            claimed_by = NULL,
            claimed_at = NULL
        FROM collapsed c
        WHERE cq.id = c.keep_id;

        DELETE FROM crawl_queue cq
        USING (
            SELECT COALESCE(discogs_id, item_key) AS target, MIN(id) AS keep_id
            FROM crawl_queue
            GROUP BY COALESCE(discogs_id, item_key)
        ) k
        WHERE COALESCE(cq.discogs_id, cq.item_key) = k.target AND cq.id <> k.keep_id;

        ALTER TABLE crawl_queue DROP CONSTRAINT IF EXISTS crawl_queue_discogs_id_crawler_id_key;
        DROP INDEX IF EXISTS crawl_queue_item_key_crawler_idx;
        ALTER TABLE crawl_queue DROP COLUMN crawler_id;
    END IF;
END $$;

-- Nullable-column unique indexes: exactly one of discogs_id/item_key is set
-- per row, multiple NULLs coexist, and ON CONFLICT (discogs_id) /
-- ON CONFLICT (item_key) infer them.
CREATE UNIQUE INDEX IF NOT EXISTS crawl_queue_discogs_id_idx ON crawl_queue (discogs_id);
CREATE UNIQUE INDEX IF NOT EXISTS crawl_queue_item_key_idx ON crawl_queue (item_key);
```

- [ ] **Step 4: Run the migration tests**

Run: `cd backend && pytest tests/test_crawl_queue.py -k migration -v`
Expected: the two collapse tests PASS. The no-op test still FAILS on `enqueue_crawl_queue()`'s signature — Step 5 fixes that.

- [ ] **Step 5: Reshape the enqueue helpers**

In `backend/db.py`, replace `enqueue_crawl_queue` (line 894) and `enqueue_crawl_queue_for_stock_item` (line 908), including the comment block above them (which currently explains the now-deleted `WHERE EXISTS` enabled gate):

```python
# The WHERE on the DO UPDATE is load-bearing, not decorative: re-enqueuing a
# target whose row is already 'pending'/'in_progress' must be a no-op (the
# DO UPDATE runs but its WHERE filters the row out, so in-flight work is left
# untouched), while re-enqueuing a 'done' target must reset it to 'pending' so
# periodic re-crawling of stale listings actually happens -- a plain ON
# CONFLICT DO NOTHING would let a target be crawled exactly once, ever.
#
# Reviving a target clears pending_crawler_ids back to NULL: a re-enqueue means
# "price this target with everything eligible", not "resume whatever narrowed
# set some earlier pass deferred".
#
# There is deliberately no enabled-crawler gate here any more. A queue row
# names no crawler, so there is nothing to gate -- eligibility is resolved at
# dispatch by get_eligible_crawlers() against live crawlers state.
def enqueue_crawl_queue(conn, discogs_id: str):
    conn.execute(
        """
        INSERT INTO crawl_queue (discogs_id) VALUES (%(discogs_id)s)
        ON CONFLICT (discogs_id) DO UPDATE SET
            status = 'pending', requested_at = CURRENT_TIMESTAMP,
            available_at = CURRENT_TIMESTAMP, claimed_by = NULL, claimed_at = NULL,
            pending_crawler_ids = NULL
        WHERE crawl_queue.status = 'done'
        """,
        {"discogs_id": discogs_id},
    )


# Keeps the source gate: it asks whether any enabled *store* still stocks the
# item, which is independent of which marketplace crawler will price it.
def enqueue_crawl_queue_for_stock_item(conn, item_key: str):
    stock_source_gate = _enabled_stock_source_exists("%(item_key)s")
    conn.execute(
        f"""
        INSERT INTO crawl_queue (item_key)
        SELECT %(item_key)s WHERE {stock_source_gate}
        ON CONFLICT (item_key) DO UPDATE SET
            status = 'pending', requested_at = CURRENT_TIMESTAMP,
            available_at = CURRENT_TIMESTAMP, claimed_by = NULL, claimed_at = NULL,
            pending_crawler_ids = NULL
        WHERE crawl_queue.status = 'done'
        """,
        {"item_key": item_key},
    )
```

- [ ] **Step 6: Reshape the claim and delete the per-crawler purge**

Replace `claim_crawl_queue_batch` (line 934) with the target-level version. Keep the existing "row lock / no reclaim path" comment block above it verbatim.

```python
def claim_crawl_queue_batch(conn, worker_id: str, limit: int) -> list[dict]:
    stock_source_gate = _enabled_stock_source_exists("crawl_queue.item_key")
    return conn.execute(
        f"""
        UPDATE crawl_queue SET status = 'in_progress', claimed_by = %(worker_id)s, claimed_at = CURRENT_TIMESTAMP
        WHERE id IN (
            SELECT id FROM crawl_queue
            WHERE status = 'pending'
              -- Set when a pass deferred a crawler whose site is in
              -- circuit-breaker cooldown; keeps the row out of the claim until
              -- the earliest of those cooldowns expires.
              AND available_at <= CURRENT_TIMESTAMP
              -- Source-side gate: whether anything still stocks the item this
              -- row would price. item_key IS NULL keeps release rows out of it.
              -- There is no crawler-side gate here any more: the row names no
              -- crawler, and _drain_one_batch resolves the eligible set against
              -- live crawlers state per row instead.
              AND (item_key IS NULL OR {stock_source_gate})
            -- (item_key IS NOT NULL) leads the sort so every pending release
            -- row (FALSE) sorts ahead of every pending stock-item row (TRUE),
            -- regardless of which was enqueued first -- a large stock-sync
            -- enqueue burst must never delay a user's own collection crawl
            -- behind it. Priority within one LIMIT'd batch, not exclusion.
            ORDER BY (item_key IS NOT NULL), requested_at, id
            LIMIT %(limit)s
            FOR UPDATE SKIP LOCKED
        )
        RETURNING id, discogs_id, item_key, pending_crawler_ids
        """,
        {"worker_id": worker_id, "limit": limit},
    ).fetchall()
```

Delete `delete_pending_crawl_queue_for_crawler` entirely, along with its comment block. Update the grant comment at lines 424-425, which names it as the reason `crawl_queue` needs `DELETE`:

```python
        # crawl_queue needs DELETE for the same reason stock_items does:
        # delete_dead_stock_crawl_queue_rows(), run through get_app_pool() from
        # PATCH /api/crawlers/{id} and at the end of each stock sync.
```

- [ ] **Step 7: Add `get_eligible_crawlers` and reshape the pending count**

In `backend/db.py`, directly after `get_crawlers` (line 789):

```python
# The dispatch-time replacement for enqueue-time crawler selection: which
# marketplace crawlers should run for one claimed queue row, right now.
#
# is_release distinguishes the two target kinds. requires_discogs_release
# crawlers (Discogs Marketplace) can only search by release id, so they are
# excluded for stock-item targets -- this is the predicate _sync_stock used to
# apply at enqueue time.
#
# pending_crawler_ids narrows the set to a previous pass's unfinished work;
# NULL means no narrowing. The intersection is what makes a crawler disabled
# since that pass drop out silently.
def get_eligible_crawlers(conn, is_release: bool, pending_crawler_ids: Optional[list] = None) -> list[dict]:
    return conn.execute(
        """
        SELECT * FROM crawlers
        WHERE enabled AND crawler_type = 'release'
          AND (%(is_release)s OR NOT requires_discogs_release)
          AND (%(pending)s::int[] IS NULL OR id = ANY(%(pending)s::int[]))
        ORDER BY id
        """,
        {"is_release": is_release, "pending": pending_crawler_ids},
    ).fetchall()
```

Replace `count_pending_crawl_queue_for_user` (line 1022), keeping the existing comment's rationale but restating it for the new shape:

```python
# A row counts only if something can actually claim and act on it. routers/
# crawl._events_to_replay reads a non-zero count as "this user is mid-job", so
# a count that cannot reach zero would leave the client replaying stale event
# history on every connect. A pending row whose narrowed pending_crawler_ids
# are all disabled -- or any pending row at all when no marketplace crawler is
# enabled -- is unactionable, and is excluded here even though it is still
# 'pending' in the table. _drain_one_batch marks such rows done when it reaches
# them.
def count_pending_crawl_queue_for_user(conn, user_id: int) -> int:
    return conn.execute(
        """
        SELECT COUNT(*) FROM crawl_queue cq
        JOIN library_items li ON li.discogs_id = cq.discogs_id
        WHERE li.user_id = %(user_id)s AND cq.status IN ('pending', 'in_progress')
          AND EXISTS (
              SELECT 1 FROM crawlers c
              WHERE c.enabled AND c.crawler_type = 'release'
                AND (cq.pending_crawler_ids IS NULL OR c.id = ANY(cq.pending_crawler_ids))
          )
        """,
        {"user_id": user_id},
    ).fetchone()["count"]
```

- [ ] **Step 8: Update the four producers**

`backend/crawl_manager.py`, collection loop (line 491) — replace the two-line loop with a single call:

```python
                            enqueue_crawl_queue(conn, rid)
```

Wishlist loop (line 533) — same replacement:

```python
                        enqueue_crawl_queue(conn, rid)
```

Then delete the now-unused `enabled_crawlers` resolution just above the `user_scope` block (lines 446-447):

```python
            with get_app_pool().connection() as pool_conn:
                enabled_crawlers = get_enabled_crawlers(pool_conn)
```

and drop `get_enabled_crawlers` from that function's `from db import ...` line (line 412).

`sweep_enqueue` (line 575) — drop the `get_app_pool()`/`get_enabled_crawlers` block and flatten the loop:

```python
    async def sweep_enqueue(self, mode: str = "missing"):
        from db import get_identity_pool, enqueue_crawl_queue, get_missing_releases, user_scope

        # Enumerated via get_identity_pool(), not get_app_pool(): app_user has
        # no grant at all on users (db.py's init_tenant_schema — isolation for
        # that table comes from the grant boundary itself, not RLS), so a
        # get_app_pool() connection can't read it. get_identity_pool()'s
        # app_identity role is the one _sync_collection already uses to read
        # a single user row for the same reason.
        with get_identity_pool().connection() as conn:
            user_ids = [row["id"] for row in conn.execute("SELECT id FROM users").fetchall()]

        for user_id in user_ids:
            with user_scope(user_id) as conn:
                if mode == "missing":
                    target_ids = get_missing_releases(conn, user_id)
                else:
                    target_ids = [row["discogs_id"] for row in conn.execute(
                        "SELECT discogs_id FROM library_items WHERE user_id = %s", [user_id]
                    ).fetchall()]
                for discogs_id in target_ids:
                    enqueue_crawl_queue(conn, discogs_id)
                conn.commit()
        log.info("Sweep-enqueue complete (mode=%s) across %d users", mode, len(user_ids))
```

`_sync_stock` (line 757) — drop the eligible-price-crawler resolution entirely; `requires_discogs_release` is now a dispatch-time predicate:

```python
                with get_app_pool().connection() as conn:
                    item_keys = replace_stock_items(conn, crawler._db_id, items)
                    update_crawler_last_run(conn, crawler._db_id)
                    for item_key in item_keys:
                        enqueue_crawl_queue_for_stock_item(conn, item_key)
                    conn.commit()
```

`backend/routers/crawl.py`, `crawl_start` (line 30) — drop `enabled_crawlers` and the inner loop:

```python
        enqueued = 0
        for discogs_id in target_ids:
            db.enqueue_crawl_queue(conn, discogs_id)
            enqueued += 1
        conn.commit()
    # Counts targets, not (target, crawler) pairs -- one queue row per target
    # now, with the crawler set resolved at dispatch.
    return {"enqueued": enqueued}
```

`backend/routers/settings.py`, `update_crawler` (line 69) — a disable no longer purges per-crawler rows; only dead stock rows are still swept:

```python
@router.patch("/crawlers/{crawler_id}", dependencies=[Depends(require_admin)])
def update_crawler(crawler_id: int, body: CrawlerUpdate):
    with db.get_app_pool().connection() as conn:
        db.set_crawler_enabled(conn, crawler_id, body.enabled)
        discarded = 0
        if not body.enabled:
            # Disabling a marketplace crawler discards nothing: queue rows name
            # no crawler, and _drain_one_batch stops selecting it on the next
            # batch. This sweep is for the other case -- disabling a *store*
            # leaves stock-item rows nothing still stocks.
            discarded = db.delete_dead_stock_crawl_queue_rows(conn)
        conn.commit()
```

Leave the `log.info` line and the `return {"ok": True, "discarded": discarded}` below it untouched — the frontend still renders that count (`frontend/src/test/settings.test.tsx:244`).

- [ ] **Step 9: Rewrite `_drain_one_batch` to fan out per target**

Replace `_drain_one_batch` (line 243) in `backend/crawl_manager.py`:

```python
    async def _drain_one_batch(self, worker_id: str, plugins_by_crawler_id: dict, pages: dict, batch_size: int = 2) -> int:
        from crawler import _new_context
        from db import get_app_pool, claim_crawl_queue_batch, mark_crawl_queue_done, defer_crawl_queue_row, upsert_listing, get_catalog_release, get_stock_item_identity, upsert_stock_item_listing, upsert_stock_item_from_release, delete_stock_item_for_release, clear_listing_price, get_eligible_crawlers

        with get_app_pool().connection() as conn:
            rows = claim_crawl_queue_batch(conn, worker_id, limit=batch_size)
            conn.commit()
        if not rows:
            return 0

        # Two passes: resolve every claimed row's target and eligible crawler
        # set first, then drain the resulting work units in target-major order.
        # batch_size is small (2) because a batch is now batch_size x eligible
        # crawlers of sequential page loads, and a claimed row stays
        # 'in_progress' for all of it -- see the hung-worker gap noted on
        # claim_crawl_queue_batch.
        targets: dict = {}
        units: list = []
        for row in rows:
            is_release = row["discogs_id"] is not None
            with get_app_pool().connection() as conn:
                if is_release:
                    target = get_catalog_release(conn, row["discogs_id"])
                else:
                    target = get_stock_item_identity(conn, row["item_key"])
                eligible = get_eligible_crawlers(conn, is_release, row["pending_crawler_ids"])
            if target is None:
                with get_app_pool().connection() as conn:
                    mark_crawl_queue_done(conn, row["id"])
                    conn.commit()
                continue
            targets[row["id"]] = (row, target, is_release)
            for crawler in eligible:
                units.append((row["id"], crawler["id"]))

        # Crawlers skipped this pass because their site is cooling down, per
        # row. They go back into pending_crawler_ids rather than being waited
        # on -- there is deliberately no barrier between targets, so a worker
        # facing a cooling-down site moves to the next unit instead of idling.
        deferred: dict = {}
        for row_id, crawler_id in units:
            row, target, is_release = targets[row_id]
            plugin = plugins_by_crawler_id.get(crawler_id)
            if plugin is None:
                # A crawler whose module failed to load at boot. Counted as a
                # site failure but deliberately NOT deferred: a permanently
                # broken module would otherwise defer its rows forever.
                self._record_site_result(crawler_id, succeeded=False)
                continue
            if crawler_id in self._cooling_down_crawler_ids():
                deferred.setdefault(row_id, []).append(crawler_id)
                continue

            if crawler_id not in pages:
                pages[crawler_id] = await _new_context(self._browser, self._stealth)

            try:
                matches, bot_detected = await self._paced_search(crawler_id, plugin, target, pages)
            except Exception as e:
                log.error(
                    "[%s] Crawl failed for %s - %s (%s): %s",
                    plugin._db_site_name, target["artist"], target["title"], row["discogs_id"] or row["item_key"], e,
                )
                self._record_site_result(crawler_id, succeeded=False)
                continue

            if is_release:
                self._record_site_result(crawler_id, succeeded=bool(matches) and not bot_detected)
            elif bot_detected or matches:
                # A stock item's search failing to find anything carries no
                # site-health signal -- most small-label stock isn't listed on
                # Amazon/eBay at all, so an empty result there isn't evidence the
                # site is broken the way it is for a real Discogs release. Only
                # a genuine signal (bot detection, or a match that proves the
                # site currently works) is recorded; a plain empty result is
                # silently excluded from the circuit breaker rather than counted
                # as either outcome.
                self._record_site_result(crawler_id, succeeded=not bot_detected)

            with get_app_pool().connection() as conn:
                if matches:
                    best = matches[0]
                    if is_release:
                        upsert_listing(
                            conn, row["discogs_id"], crawler_id, best["url"],
                            best.get("price"), best.get("shipping"), best.get("currency"), best.get("condition"),
                        )
                        upsert_stock_item_from_release(conn, row["discogs_id"], crawler_id, target, best)
                    else:
                        upsert_stock_item_listing(
                            conn, row["item_key"], crawler_id, best["url"],
                            best.get("price"), best.get("shipping"), best.get("currency"), best.get("condition"),
                        )
                elif is_release and not bot_detected:
                    delete_stock_item_for_release(conn, row["discogs_id"], crawler_id)
                    clear_listing_price(conn, row["discogs_id"], crawler_id)
                conn.commit()

            status = "found" if matches else "not_found"
            if is_release:
                await self._broadcast_listing_changed(row["discogs_id"], crawler_id, status)
            else:
                await self._broadcast_stock_listing_changed(row["item_key"], crawler_id, status)

        # One resolution per row, after its units are drained. A row with
        # nothing deferred is done; a row with deferred crawlers goes back to
        # pending, narrowed to just those, until the earliest cooldown expires.
        for row_id in targets:
            with get_app_pool().connection() as conn:
                if row_id in deferred:
                    defer_crawl_queue_row(
                        conn, row_id, deferred[row_id],
                        self._cooldown_remaining_seconds(deferred[row_id]),
                    )
                else:
                    mark_crawl_queue_done(conn, row_id)
                conn.commit()

        return len(rows)
```

- [ ] **Step 10: Update the existing tests for the new signatures**

Every `db.enqueue_crawl_queue(conn, "rN", crawler_id)` call becomes `db.enqueue_crawl_queue(conn, "rN")`, and every `db.enqueue_crawl_queue_for_stock_item(conn, key, crawler_id)` becomes `db.enqueue_crawl_queue_for_stock_item(conn, key)`, across `tests/test_crawl_queue.py`, `tests/test_crawl_manager.py`, `tests/test_crawl_router.py`, `tests/test_settings_router.py`, `tests/test_crawler_crud.py`, and `tests/test_stock_crud.py`. Find them with:

```bash
cd backend && grep -rn "enqueue_crawl_queue" tests/
```

Also in `tests/test_crawl_queue.py`:

- Delete the tests for `delete_pending_crawl_queue_for_crawler` and for `claim_crawl_queue_batch`'s `excluded_crawler_ids` parameter — both target functionality this task removes. Find them with `grep -n "excluded_crawler_ids\|delete_pending_crawl_queue_for_crawler" tests/test_crawl_queue.py`.
- Any test asserting a per-pair row count (e.g. two rows for one release across two crawlers) becomes a single-row assertion.

In `tests/test_crawl_router.py`, `POST /crawl/start`'s `enqueued` now counts targets: a test enqueuing 3 releases with 2 enabled crawlers expects `3`, not `6`.

Then add one new test to `tests/test_crawl_queue.py` for the revival path, since the reset of `pending_crawler_ids` is new behaviour rather than an updated signature:

```python
def test_enqueue_revives_a_done_target_and_clears_its_narrowed_crawler_set(admin_conn):
    crawler_id = _make_catalog_and_crawler(admin_conn, "r1")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.execute(
        "UPDATE crawl_queue SET status = 'done', pending_crawler_ids = ARRAY[%s] WHERE discogs_id = 'r1'",
        [crawler_id],
    )
    admin_conn.commit()

    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()

    row = admin_conn.execute(
        "SELECT status, pending_crawler_ids FROM crawl_queue WHERE discogs_id = 'r1'"
    ).fetchone()
    assert row["status"] == "pending"
    # A re-enqueue means "price this target with everything eligible", not
    # "resume whatever narrowed set an earlier pass deferred".
    assert row["pending_crawler_ids"] is None


def test_enqueue_leaves_a_pending_target_untouched(admin_conn):
    _make_catalog_and_crawler(admin_conn, "r1")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()
    before = admin_conn.execute(
        "SELECT requested_at FROM crawl_queue WHERE discogs_id = 'r1'"
    ).fetchone()["requested_at"]

    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.commit()

    rows = admin_conn.execute(
        "SELECT requested_at FROM crawl_queue WHERE discogs_id = 'r1'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["requested_at"] == before
```

Note that the three tests added in Task 1 call `db.enqueue_crawl_queue(admin_conn, "r1", crawler_id)` with the old signature — they are part of this sweep too.

- [ ] **Step 11: Write the fan-out and live-enablement tests**

Add to `backend/tests/test_crawl_manager.py`, in the worker-pool section (after line 1165). These follow that section's existing conventions: `pg_schema`, `db.get_admin_pool()` for setup and assertions, `AsyncMock` plugins, `patch("crawler._new_context", ...)`.

```python
async def test_worker_fans_one_target_out_to_every_enabled_crawler(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/a.py")
        db.register_crawler(conn, "eBay", "/b.py")
        amazon_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        ebay_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'eBay'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    plugins = {}
    for crawler_id, name, price in ((amazon_id, "Amazon", 9.99), (ebay_id, "eBay", 12.50)):
        plugin = AsyncMock()
        plugin.search = AsyncMock(return_value=[{"url": f"https://{name}", "price": price, "shipping": None, "currency": "USD", "condition": None}])
        plugin._db_id = crawler_id
        plugin._db_site_name = name
        plugins[crawler_id] = plugin

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
        claimed = await manager._drain_one_batch("worker-test", plugins, pages={})

    # One claimed row, two crawls, two listings.
    assert claimed == 1
    with db.get_admin_pool().connection() as conn:
        prices = conn.execute(
            "SELECT crawler_id, price FROM listings WHERE release_id = 'r1' ORDER BY crawler_id"
        ).fetchall()
        queue_row = conn.execute("SELECT status FROM crawl_queue WHERE discogs_id = 'r1'").fetchone()
    assert [(p["crawler_id"], p["price"]) for p in prices] == [(amazon_id, 9.99), (ebay_id, 12.50)]
    assert queue_row["status"] == "done"


async def test_worker_skips_a_crawler_disabled_after_the_row_was_enqueued(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/a.py")
        db.register_crawler(conn, "eBay", "/b.py")
        amazon_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        ebay_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'eBay'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        db.set_crawler_enabled(conn, ebay_id, False)
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    plugins = {}
    for crawler_id, name in ((amazon_id, "Amazon"), (ebay_id, "eBay")):
        plugin = AsyncMock()
        plugin.search = AsyncMock(return_value=[{"url": f"https://{name}", "price": 5.0, "shipping": None, "currency": "USD", "condition": None}])
        plugin._db_id = crawler_id
        plugin._db_site_name = name
        plugins[crawler_id] = plugin

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
        await manager._drain_one_batch("worker-test", plugins, pages={})

    plugins[amazon_id].search.assert_awaited_once()
    plugins[ebay_id].search.assert_not_awaited()


async def test_worker_excludes_requires_discogs_release_crawlers_for_stock_items(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/a.py")
        db.register_crawler(conn, "Discogs", "/d.py", requires_discogs_release=True)
        amazon_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        discogs_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Discogs'").fetchone()["id"]
        _stock_item_with_source(conn, "key1")
        db.enqueue_crawl_queue_for_stock_item(conn, "key1")
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    plugins = {}
    for crawler_id, name in ((amazon_id, "Amazon"), (discogs_id, "Discogs")):
        plugin = AsyncMock()
        plugin.search = AsyncMock(return_value=[])
        plugin._db_id = crawler_id
        plugin._db_site_name = name
        plugins[crawler_id] = plugin

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
        await manager._drain_one_batch("worker-test", plugins, pages={})

    plugins[amazon_id].search.assert_awaited_once()
    plugins[discogs_id].search.assert_not_awaited()


async def test_worker_defers_a_cooling_down_crawler_and_crawls_the_rest(pg_schema):
    import time
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/a.py")
        db.register_crawler(conn, "eBay", "/b.py")
        amazon_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        ebay_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'eBay'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    manager._site_cooldown_until = {ebay_id: time.monotonic() + 1800}
    plugins = {}
    for crawler_id, name in ((amazon_id, "Amazon"), (ebay_id, "eBay")):
        plugin = AsyncMock()
        plugin.search = AsyncMock(return_value=[{"url": f"https://{name}", "price": 5.0, "shipping": None, "currency": "USD", "condition": None}])
        plugin._db_id = crawler_id
        plugin._db_site_name = name
        plugins[crawler_id] = plugin

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
        await manager._drain_one_batch("worker-test", plugins, pages={})

    plugins[amazon_id].search.assert_awaited_once()
    plugins[ebay_id].search.assert_not_awaited()
    with db.get_admin_pool().connection() as conn:
        row = conn.execute(
            "SELECT status, pending_crawler_ids, available_at > CURRENT_TIMESTAMP AS still_deferred "
            "FROM crawl_queue WHERE discogs_id = 'r1'"
        ).fetchone()
    # Back to pending, narrowed to the crawler that never ran, and held off
    # until its cooldown expires -- not marked done with a silent gap.
    assert row["status"] == "pending"
    assert row["pending_crawler_ids"] == [ebay_id]
    assert row["still_deferred"] is True


async def test_worker_honours_a_narrowed_pending_crawler_set(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/a.py")
        db.register_crawler(conn, "eBay", "/b.py")
        amazon_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        ebay_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'eBay'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        conn.execute(
            "UPDATE crawl_queue SET pending_crawler_ids = ARRAY[%s] WHERE discogs_id = 'r1'", [ebay_id]
        )
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    plugins = {}
    for crawler_id, name in ((amazon_id, "Amazon"), (ebay_id, "eBay")):
        plugin = AsyncMock()
        plugin.search = AsyncMock(return_value=[])
        plugin._db_id = crawler_id
        plugin._db_site_name = name
        plugins[crawler_id] = plugin

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
        await manager._drain_one_batch("worker-test", plugins, pages={})

    plugins[ebay_id].search.assert_awaited_once()
    plugins[amazon_id].search.assert_not_awaited()


async def test_worker_marks_a_target_done_when_no_crawler_is_eligible(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/a.py")
        amazon_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        db.set_crawler_enabled(conn, amazon_id, False)
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
        await manager._drain_one_batch("worker-test", {}, pages={})

    with db.get_admin_pool().connection() as conn:
        row = conn.execute("SELECT status FROM crawl_queue WHERE discogs_id = 'r1'").fetchone()
    assert row["status"] == "done"


async def test_worker_drains_units_target_major_across_a_batch(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/a.py")
        db.register_crawler(conn, "eBay", "/b.py")
        amazon_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        ebay_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'eBay'").fetchone()["id"]
        for rid in ("r1", "r2"):
            db.upsert_catalog_release(conn, {
                "discogs_id": rid, "artist": "A", "title": "T", "year": None, "label": None,
                "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
                "discogs_url": None,
            })
            db.enqueue_crawl_queue(conn, rid)
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    order = []
    plugins = {}
    for crawler_id, name in ((amazon_id, "Amazon"), (ebay_id, "eBay")):
        async def search(target, page, _name=name):
            order.append((target["discogs_id"], _name))
            return []
        plugin = AsyncMock()
        plugin.search = search
        plugin._db_id = crawler_id
        plugin._db_site_name = name
        plugins[crawler_id] = plugin

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
        await manager._drain_one_batch("worker-test", plugins, pages={})

    # Both crawlers run for r1 before either runs for r2 -- the property the
    # old (target, crawler) row layout only produced by accident of insert order.
    assert [rid for rid, _name in order] == ["r1", "r1", "r2", "r2"]
```

- [ ] **Step 12: Run the full backend suite**

Run: `cd backend && pytest`
Expected: PASS. Investigate any failure before continuing — a red suite here means a call site still passes a `crawler_id` or reads `row["crawler_id"]`. Find stragglers with `grep -rn "crawler_id" backend/crawl_manager.py backend/routers/crawl.py`.

- [ ] **Step 13: Update the `CLAUDE.md` key invariants**

Replace the bullet on line 42 (`**crawlers.enabled** is a runtime gate...`) with:

```markdown
- **`crawlers.enabled` is resolved at dispatch, not baked into queue rows.** A `crawl_queue` row names a *target* (`discogs_id` xor `item_key`), never a crawler. `_drain_one_batch` claims target rows and calls `db.get_eligible_crawlers` per row, so enabling or disabling a marketplace crawler takes effect on the next batch with no restart, no purge, and no re-sync. `start_worker_pool` loads plugins for *every* release crawler (`db.get_crawlers`), enabled or not, so a crawler enabled after boot already has its plugin. `pending_crawler_ids` on a row narrows the next pass to crawlers a previous pass deferred behind a circuit-breaker cooldown; `available_at` holds the row out of the claim until the earliest of those cooldowns expires. A stock-item row additionally requires an enabled store to still list its `item_key` in `stock_items` — that gate lives in `claim_crawl_queue_batch` and `enqueue_crawl_queue_for_stock_item`, with `db.delete_dead_stock_crawl_queue_rows` sweeping rows that fail it on disable and at the end of each stock sync. See [`docs/specifications/shaping/2026-08-14-per-item-crawler-fanout-design.md`](docs/specifications/shaping/2026-08-14-per-item-crawler-fanout-design.md), which supersedes the enqueue-time-selection parts of [`2026-08-09-stop-crawling-disabled-stores-design.md`](docs/specifications/shaping/2026-08-09-stop-crawling-disabled-stores-design.md) and [`2026-08-10-dead-stock-crawl-jobs-design.md`](docs/specifications/shaping/2026-08-10-dead-stock-crawl-jobs-design.md).
```

In the bullet on line 43, change "once a crawl_queue job actually ran for that `(discogs_id, crawler_id)` pair" to "once a crawl pass actually ran that crawler for that target".

In the bullet on line 41, change "both just enqueue rows for the calling user" to "both just enqueue one row per target for the calling user".

- [ ] **Step 14: Commit**

```bash
git add backend/db.py backend/crawl_manager.py backend/routers/crawl.py backend/routers/settings.py backend/tests/ CLAUDE.md
git commit -m "feat: collapse crawl_queue to one row per target and fan out at dispatch"
```

---

### Task 3: Backfill the queue when a marketplace crawler is enabled

**Files:**
- Modify: `backend/db.py` — new `backfill_crawl_queue_for_crawler` next to `set_crawler_enabled` (line 848)
- Modify: `backend/routers/settings.py` — `update_crawler` (line 69)
- Modify: `CLAUDE.md` — the invariant bullet rewritten in Task 2
- Test: `backend/tests/test_crawl_queue.py`, `backend/tests/test_settings_router.py`

**Interfaces:**
- Consumes: `crawl_queue.pending_crawler_ids` and `available_at` (Task 1); the collapsed one-row-per-target shape (Task 2).
- Produces: `db.backfill_crawl_queue_for_crawler(conn, crawler_id: int) -> int` returning the number of revived rows.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_crawl_queue.py`:

```python
def test_backfill_revives_done_targets_the_crawler_has_no_price_for(admin_conn):
    crawler_id = _make_catalog_and_crawler(admin_conn, "r1")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.execute("UPDATE crawl_queue SET status = 'done' WHERE discogs_id = 'r1'")
    admin_conn.commit()

    revived = db.backfill_crawl_queue_for_crawler(admin_conn, crawler_id)
    admin_conn.commit()

    assert revived == 1
    row = admin_conn.execute(
        "SELECT status, pending_crawler_ids FROM crawl_queue WHERE discogs_id = 'r1'"
    ).fetchone()
    assert row["status"] == "pending"
    # Narrowed to just this crawler: the point is to fill in the prices it is
    # missing, not to re-crawl what other crawlers already priced.
    assert row["pending_crawler_ids"] == [crawler_id]


def test_backfill_skips_targets_this_crawler_already_priced(admin_conn):
    crawler_id = _make_catalog_and_crawler(admin_conn, "r1")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.execute("UPDATE crawl_queue SET status = 'done' WHERE discogs_id = 'r1'")
    db.upsert_listing(admin_conn, "r1", crawler_id, "https://x", 9.99, None, "USD", None)
    admin_conn.commit()

    revived = db.backfill_crawl_queue_for_crawler(admin_conn, crawler_id)
    admin_conn.commit()

    assert revived == 0
    row = admin_conn.execute("SELECT status FROM crawl_queue WHERE discogs_id = 'r1'").fetchone()
    assert row["status"] == "done"


def test_backfill_revives_targets_whose_price_was_cleared(admin_conn):
    crawler_id = _make_catalog_and_crawler(admin_conn, "r1")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.execute("UPDATE crawl_queue SET status = 'done' WHERE discogs_id = 'r1'")
    db.upsert_listing(admin_conn, "r1", crawler_id, "https://x", 9.99, None, "USD", None)
    # clear_listing_price leaves the row behind with a NULL price, which must
    # not read as "already priced".
    db.clear_listing_price(admin_conn, "r1", crawler_id)
    admin_conn.commit()

    revived = db.backfill_crawl_queue_for_crawler(admin_conn, crawler_id)
    admin_conn.commit()

    assert revived == 1


def test_backfill_widens_a_narrowed_pending_row(admin_conn):
    crawler_a = _make_catalog_and_crawler(admin_conn, "r1", site_name="Amazon")
    crawler_b = _make_catalog_and_crawler(admin_conn, "r1", site_name="eBay")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1")
    admin_conn.execute(
        "UPDATE crawl_queue SET pending_crawler_ids = ARRAY[%s] WHERE discogs_id = 'r1'", [crawler_a]
    )
    admin_conn.commit()

    db.backfill_crawl_queue_for_crawler(admin_conn, crawler_b)
    admin_conn.commit()

    row = admin_conn.execute(
        "SELECT pending_crawler_ids FROM crawl_queue WHERE discogs_id = 'r1'"
    ).fetchone()
    assert sorted(row["pending_crawler_ids"]) == sorted([crawler_a, crawler_b])


def test_backfill_leaves_stock_item_rows_alone_for_a_discogs_only_crawler(admin_conn):
    crawler_id = _make_stock_identity_and_crawler(admin_conn, "key1", site_name="Discogs")
    admin_conn.execute("UPDATE crawlers SET requires_discogs_release = TRUE WHERE id = %s", [crawler_id])
    admin_conn.commit()
    db.enqueue_crawl_queue_for_stock_item(admin_conn, "key1")
    admin_conn.execute("UPDATE crawl_queue SET status = 'done' WHERE item_key = 'key1'")
    admin_conn.commit()

    revived = db.backfill_crawl_queue_for_crawler(admin_conn, crawler_id)
    admin_conn.commit()

    assert revived == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_crawl_queue.py -k backfill -v`
Expected: FAIL — `AttributeError: module 'db' has no attribute 'backfill_crawl_queue_for_crawler'`.

- [ ] **Step 3: Implement the backfill**

In `backend/db.py`, directly after `set_crawler_enabled` (line 848):

```python
# Enabling a crawler makes it apply to every target still pending for free --
# eligibility is resolved at dispatch. Targets already marked 'done', though,
# would not see it until the next sync or scheduled sweep, so enabling one
# revives exactly the targets it has no price for.
#
# price IS NOT NULL, not bare NOT EXISTS: clear_listing_price leaves a
# NULL-price row behind for a target this crawler crawled and found nothing
# for, and a bare existence check would read that as already priced. The
# trade-off is that targets where the crawler legitimately found nothing are
# revived on every enable -- bounded and idempotent, but not free.
def backfill_crawl_queue_for_crawler(conn, crawler_id: int) -> int:
    requires_release = conn.execute(
        "SELECT requires_discogs_release FROM crawlers WHERE id = %s", [crawler_id]
    ).fetchone()
    if requires_release is None:
        return 0
    release_only_clause = (
        "AND crawl_queue.discogs_id IS NOT NULL" if requires_release["requires_discogs_release"] else ""
    )
    revived = conn.execute(
        f"""
        UPDATE crawl_queue SET
            status = 'pending', requested_at = CURRENT_TIMESTAMP,
            available_at = CURRENT_TIMESTAMP, claimed_by = NULL, claimed_at = NULL,
            pending_crawler_ids = ARRAY[%(crawler_id)s]
        WHERE status = 'done'
          {release_only_clause}
          AND NOT EXISTS (
              SELECT 1 FROM listings l
              WHERE l.crawler_id = %(crawler_id)s AND l.price IS NOT NULL
                AND (l.release_id = crawl_queue.discogs_id OR l.item_key = crawl_queue.item_key)
          )
        """,
        {"crawler_id": crawler_id},
    ).rowcount
    # A row narrowed by an earlier deferral carries a set that predates this
    # crawler being enabled, so it would otherwise skip it. Rows with NULL need
    # nothing -- NULL already means "all currently eligible".
    conn.execute(
        """
        UPDATE crawl_queue SET pending_crawler_ids = pending_crawler_ids || %(crawler_id)s
        WHERE status = 'pending' AND pending_crawler_ids IS NOT NULL
          AND NOT (%(crawler_id)s = ANY(pending_crawler_ids))
        """,
        {"crawler_id": crawler_id},
    )
    return revived
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_crawl_queue.py -k backfill -v`
Expected: PASS — all five.

- [ ] **Step 5: Write the failing router test**

Add to `backend/tests/test_settings_router.py`, mirroring `test_patch_crawler_as_admin_flips_enabled` (line 66) — same `pg_test_db` + `authed_client_factory` fixtures, same admin promotion, and the same `X-Requested-With` header, which the CSRF guard requires on `PATCH`:

```python
def test_enabling_a_crawler_backfills_the_queue(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/a.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.set_crawler_enabled(conn, crawler_id, False)
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute("UPDATE users SET is_admin = TRUE WHERE id = %s", [user["id"]])
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1")
        conn.execute("UPDATE crawl_queue SET status = 'done' WHERE discogs_id = 'r1'")
        conn.commit()

    client = authed_client_factory(user["id"])
    resp = client.patch(
        f"/api/crawlers/{crawler_id}", json={"enabled": True}, headers={"X-Requested-With": "fetch"}
    )

    assert resp.status_code == 200
    assert resp.json()["backfilled"] == 1
    with db.get_admin_pool().connection() as conn:
        row = conn.execute(
            "SELECT status, pending_crawler_ids FROM crawl_queue WHERE discogs_id = 'r1'"
        ).fetchone()
    assert row["status"] == "pending"
    assert row["pending_crawler_ids"] == [crawler_id]
```

- [ ] **Step 6: Run the test to verify it fails**

Run: `cd backend && pytest tests/test_settings_router.py -k backfills -v`
Expected: FAIL — `KeyError: 'backfilled'`, since the endpoint does not return that key yet.

- [ ] **Step 7: Wire the backfill into the endpoint**

In `backend/routers/settings.py`, replace `update_crawler`'s body:

```python
@router.patch("/crawlers/{crawler_id}", dependencies=[Depends(require_admin)])
def update_crawler(crawler_id: int, body: CrawlerUpdate):
    with db.get_app_pool().connection() as conn:
        db.set_crawler_enabled(conn, crawler_id, body.enabled)
        discarded = 0
        backfilled = 0
        if body.enabled:
            backfilled = db.backfill_crawl_queue_for_crawler(conn, crawler_id)
        else:
            # Disabling a marketplace crawler discards nothing: queue rows name
            # no crawler, and _drain_one_batch stops selecting it on the next
            # batch. This sweep is for the other case -- disabling a *store*
            # leaves stock-item rows nothing still stocks.
            discarded = db.delete_dead_stock_crawl_queue_rows(conn)
        conn.commit()
    if discarded:
        # INFO, not WARNING: routers/logs.py's _line_visible filters by exact
        # level membership, so at WARNING this is invisible to anyone watching
        # the INFO stream that carries the rest of the crawl narrative.
        log.info("Crawler %d disabled: %d pending crawl jobs discarded", crawler_id, discarded)
    if backfilled:
        log.info("Crawler %d enabled: %d targets re-queued for backfill", crawler_id, backfilled)
    return {"ok": True, "discarded": discarded, "backfilled": backfilled}
```

`backfilled` is additive in the response; the frontend's `setCrawlerEnabled` reads only `discarded` (`frontend/src/api/client.ts`), so no frontend change is required.

- [ ] **Step 8: Run the full backend suite**

Run: `cd backend && pytest`
Expected: PASS.

- [ ] **Step 9: Document the backfill in `CLAUDE.md`**

Append to the invariant bullet rewritten in Task 2 (line 42), before the "See ..." sentence:

```markdown
Enabling a marketplace crawler also calls `db.backfill_crawl_queue_for_crawler`, which revives `done` targets it has no priced `listings` row for, narrowed via `pending_crawler_ids` so only the newly enabled crawler runs for them.
```

- [ ] **Step 10: Commit**

```bash
git add backend/db.py backend/routers/settings.py backend/tests/ CLAUDE.md
git commit -m "feat: backfill missing prices when a marketplace crawler is enabled"
```

---

## Verification

After Task 3, confirm the whole feature end to end:

- [ ] `cd backend && pytest` — full suite green.
- [ ] `cd frontend && npm test` — unaffected, but the Settings tests touch the `discarded` field, so confirm they still pass.
- [ ] Start the app (`make dev`), enable a second marketplace crawler in Settings, and confirm in the log viewer that both crawlers run against the same release in succession before the next release starts.
