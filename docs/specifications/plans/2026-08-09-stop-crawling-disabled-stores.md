# Stop Crawling Disabled Stores Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Disabling a crawler in Settings stops it crawling within one batch, instead of only stopping future enqueues.

**Architecture:** `crawlers.enabled` becomes a live runtime gate consulted at three points — claim time (`db.claim_crawl_queue_batch`), enqueue time (`db.enqueue_crawl_queue` / `enqueue_crawl_queue_for_stock_item`), and per catalog source inside `_sync_stock` — instead of a filter applied once when a job list is built. Disabling also purges that crawler's `pending` queue rows. The worker pool loads plugins for *all* crawlers at boot so `enabled` no longer doubles as a plugin-loading filter, which is what made a post-boot re-enable silently drop jobs.

**Tech Stack:** Python 3.9+, FastAPI, psycopg3 + Postgres, pytest (`asyncio_mode = "auto"`), React + TypeScript + Vitest + Testing Library.

**Spec:** [`docs/specifications/shaping/2026-08-09-stop-crawling-disabled-stores-design.md`](../shaping/2026-08-09-stop-crawling-disabled-stores-design.md)

## Global Constraints

- No comments unless the WHY is non-obvious. No backwards-compat shims. Python ≥3.9 — no `str | None`, use `Optional[str]`.
- Prefer editing existing files; no new abstractions without a clear reason.
- Every commit carries the AI-attribution trailer block from `CLAUDE.md` as its last paragraph, and is created with `git commit -F`, never `git commit -m` — shell quoting drops trailers. Each task below gives only its subject line; build the message with this exact pattern, substituting that subject and a one-paragraph body, and replacing `claude-opus-5` with the model actually running the task:
  ```bash
  cat > /tmp/commit-msg.txt <<'EOF'
  <subject line from the task>

  <one paragraph on what changed and why>

  Note: This commit message was created by AI
  ai-generated: true
  ai-model: claude-opus-5
  ai-tool: claude-code
  ai-surface: claude-code-desktop
  ai-executor: local-agent
  EOF
  git commit -F /tmp/commit-msg.txt
  ```
- Backend tests need all three env vars. The full backend command, run from the repo root:
  ```bash
  cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest
  ```
  Every `Run:` step below that names `pytest` assumes that same `cd backend` and those same three env vars.
- Frontend tests run from `frontend/`: `npm test -- --run <path>`.
- A test may never assume pre-existing schema or role state — construct everything the test asserts on within the test.
- `backend/version.py`'s `VERSION` is bumped in this branch (Task 7), minor only: `3.13` → `3.14`.

---

## File Structure

| File | Responsibility in this change |
| --- | --- |
| `backend/db.py` | New `get_crawlers`; new `delete_pending_crawl_queue_for_crawler`; enabled gate inside `claim_crawl_queue_batch`; enabled guard inside both enqueue helpers. |
| `backend/crawl_manager.py` | `start_worker_pool` loads all release crawlers; `_sync_stock` re-reads enabled state per catalog source. |
| `backend/routers/settings.py` | `update_crawler` purges pending rows on disable, logs, and returns the count. |
| `frontend/src/api/client.ts` | `setCrawlerEnabled` returns the parsed response body. |
| `frontend/src/views/Settings.tsx` | Single-slot "N queued jobs discarded" notice next to the toggled row. |
| `backend/tests/test_crawler_crud.py` | `get_crawlers` coverage. |
| `backend/tests/test_crawl_queue.py` | Claim gate, enqueue guard, purge helper. |
| `backend/tests/test_settings_router.py` | `PATCH /crawlers/{id}` purge + response shape. |
| `backend/tests/test_crawl_manager.py` | `start_worker_pool` registry, `_sync_stock` per-source skip. |
| `frontend/src/test/settings.test.tsx` | Discarded-count notice. |

Task order matters in one place only: Task 3 (enqueue guard) changes the behaviour every later backend test's setup depends on — after Task 3, `db.enqueue_crawl_queue` for a disabled crawler inserts nothing, so any test that wants a queue row for a disabled crawler must enqueue *before* disabling. Tasks 1–6 are otherwise independent.

---

### Task 1: `db.get_crawlers`, and load all release plugins at boot

Today `start_worker_pool` builds its plugin registry from `get_enabled_crawlers(conn)`. A crawler enabled after boot therefore has no entry in `plugins_by_crawler_id`, and `_drain_one_batch`'s `plugin is None` branch marks its claimed rows `done` with no listing, no error and no log line. This task makes plugin loading independent of `enabled`.

**Files:**
- Modify: `backend/db.py` — add `get_crawlers` immediately after `get_enabled_crawlers` (around line 705)
- Modify: `backend/crawl_manager.py:71-75` — `start_worker_pool`
- Test: `backend/tests/test_crawler_crud.py`, `backend/tests/test_crawl_manager.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `db.get_crawlers(conn, crawler_type: str = "release") -> list[dict]` — every `crawlers` row of that type, enabled or not.

- [ ] **Step 1: Write the failing `get_crawlers` test**

Append to `backend/tests/test_crawler_crud.py`:

```python
def test_get_crawlers_includes_disabled_and_filters_by_type(admin_conn):
    db.register_crawler(admin_conn, "Amazon", "/path/amazon.py")
    db.register_crawler(admin_conn, "eBay", "/path/ebay.py")
    db.register_crawler(admin_conn, "Stock Site", "/path/stock.py", crawler_type="catalog")
    admin_conn.commit()
    ebay_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'eBay'").fetchone()["id"]
    db.set_crawler_enabled(admin_conn, ebay_id, False)
    admin_conn.commit()

    release = db.get_crawlers(admin_conn)
    assert {c["site_name"] for c in release} == {"Amazon", "eBay"}

    catalog = db.get_crawlers(admin_conn, crawler_type="catalog")
    assert {c["site_name"] for c in catalog} == {"Stock Site"}
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `pytest tests/test_crawler_crud.py::test_get_crawlers_includes_disabled_and_filters_by_type -v`
Expected: FAIL with `AttributeError: module 'db' has no attribute 'get_crawlers'`

- [ ] **Step 3: Implement `get_crawlers`**

In `backend/db.py`, directly below `get_enabled_crawlers`:

```python
def get_crawlers(conn, crawler_type: str = "release") -> list[dict]:
    return conn.execute("SELECT * FROM crawlers WHERE crawler_type = %s", [crawler_type]).fetchall()
```

Deliberately not `get_all_crawlers` for the worker pool's use: that one import-executes every plugin module a second time to read a cosmetic `base_url` for the admin listing, work `load_enabled_crawlers` is about to redo.

- [ ] **Step 4: Run it to make sure it passes**

Run: `pytest tests/test_crawler_crud.py::test_get_crawlers_includes_disabled_and_filters_by_type -v`
Expected: PASS

- [ ] **Step 5: Write the failing `start_worker_pool` test**

Append to `backend/tests/test_crawl_manager.py`, after the existing `_drain_one_batch` block (near line 1450). `AsyncMock`, `MagicMock`, `patch`, `db` and `CrawlManager` are already imported at the top of that file.

```python
async def test_start_worker_pool_loads_plugins_for_disabled_crawlers(pg_schema):
    """`enabled` is a runtime gate, not a plugin-loading filter. When it was
    both, a crawler enabled after boot had no plugin, and _drain_one_batch's
    `plugin is None` branch marked its rows done with no listing and no log."""
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/amazon.py")
        db.register_crawler(conn, "eBay", "/ebay.py")
        conn.commit()
        ebay_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'eBay'").fetchone()["id"]
        db.set_crawler_enabled(conn, ebay_id, False)
        conn.commit()

    loaded = []

    def _capture(rows):
        loaded.extend(r["site_name"] for r in rows)
        return []

    browser = AsyncMock()
    playwright = AsyncMock()
    playwright.chromium.launch = AsyncMock(return_value=browser)
    launcher = MagicMock()
    launcher.start = AsyncMock(return_value=playwright)

    manager = CrawlManager()
    with patch("crawler.load_enabled_crawlers", side_effect=_capture), \
         patch("playwright.async_api.async_playwright", return_value=launcher), \
         patch("playwright_stealth.Stealth"):
        await manager.start_worker_pool(worker_count=0)
        await manager.stop_worker_pool()

    assert sorted(loaded) == ["Amazon", "eBay"]
```

- [ ] **Step 6: Run it to make sure it fails**

Run: `pytest tests/test_crawl_manager.py::test_start_worker_pool_loads_plugins_for_disabled_crawlers -v`
Expected: FAIL with `AssertionError: assert ['Amazon'] == ['Amazon', 'eBay']`

- [ ] **Step 7: Switch `start_worker_pool` to `get_crawlers`**

In `backend/crawl_manager.py`, inside `start_worker_pool`, change the import line and the two lines that build the registry:

```python
        from db import get_app_pool, get_crawlers

        with get_app_pool().connection() as conn:
            all_crawlers = get_crawlers(conn)
        plugins = load_enabled_crawlers(all_crawlers)
```

`get_enabled_crawlers` is no longer used by this method; leave the other call sites alone. `load_enabled_crawlers` keeps its name and signature — it filters nothing itself and never did.

- [ ] **Step 8: Run both tests to make sure they pass**

Run: `pytest tests/test_crawler_crud.py tests/test_crawl_manager.py -v`
Expected: PASS, no regressions in the existing `_drain_one_batch` or stock-sync tests

- [ ] **Step 9: Commit**

```bash
git add backend/db.py backend/crawl_manager.py backend/tests/test_crawler_crud.py backend/tests/test_crawl_manager.py
git commit -F /tmp/commit-msg.txt
```

Message subject: `fix: load crawler plugins regardless of enabled state`

---

### Task 2: Live enabled gate at claim time

`claim_crawl_queue_batch` is the authoritative stop. It re-evaluates on every batch, so the first claim after the toggle already skips the store. Worst-case overrun is one batch per worker (2 workers × `batch_size=5` = 10 items with current defaults).

**Files:**
- Modify: `backend/db.py:808-838` — `claim_crawl_queue_batch`
- Test: `backend/tests/test_crawl_queue.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: no signature change. `claim_crawl_queue_batch(conn, worker_id, limit, excluded_crawler_ids=None)` keeps returning `list[dict]` of `id, discogs_id, item_key, crawler_id`, now never including a row whose crawler is disabled.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_crawl_queue.py`. The `admin_conn` fixture and both `_make_*_and_crawler` helpers already exist at the top of that file.

```python
def test_claim_crawl_queue_batch_skips_a_disabled_crawler(admin_conn):
    crawler_id = _make_catalog_and_crawler(admin_conn)
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1", crawler_id)
    admin_conn.commit()

    db.set_crawler_enabled(admin_conn, crawler_id, False)
    admin_conn.commit()

    with db.get_app_pool().connection() as conn:
        assert db.claim_crawl_queue_batch(conn, "worker-1", limit=10) == []
        conn.commit()

    db.set_crawler_enabled(admin_conn, crawler_id, True)
    admin_conn.commit()

    with db.get_app_pool().connection() as conn:
        claimed = db.claim_crawl_queue_batch(conn, "worker-1", limit=10)
        conn.commit()
    assert [r["discogs_id"] for r in claimed] == ["r1"]


def test_claim_crawl_queue_batch_disabled_rows_do_not_consume_batch_slots(admin_conn):
    """A disabled crawler's rows must be invisible to the claim, not merely
    skipped after selection -- otherwise a large disabled backlog sorts ahead
    of enabled work and starves it batch after batch."""
    off_id = _make_catalog_and_crawler(admin_conn, discogs_id="r1", site_name="Off Site")
    on_id = _make_catalog_and_crawler(admin_conn, discogs_id="r2", site_name="On Site")
    admin_conn.commit()
    for i in range(5):
        db.upsert_catalog_release(admin_conn, {
            "discogs_id": f"off{i}", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(admin_conn, f"off{i}", off_id)
    db.enqueue_crawl_queue(admin_conn, "r2", on_id)
    admin_conn.commit()

    db.set_crawler_enabled(admin_conn, off_id, False)
    admin_conn.commit()

    with db.get_app_pool().connection() as conn:
        claimed = db.claim_crawl_queue_batch(conn, "worker-1", limit=5)
        conn.commit()
    assert [r["discogs_id"] for r in claimed] == ["r2"]
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `pytest tests/test_crawl_queue.py -k disabled -v`
Expected: both FAIL — the first with `assert [{...'r1'...}] == []`, the second with five `off*` rows claimed ahead of `r2`

- [ ] **Step 3: Add the gate**

In `backend/db.py`, in `claim_crawl_queue_batch`'s inner `SELECT`, add one line after `WHERE status = 'pending'`:

```sql
            WHERE status = 'pending'
              AND crawler_id IN (SELECT id FROM crawlers WHERE enabled)
              {exclusion_clause}
```

Unconditional, unlike `exclusion_clause` — there is no "no crawlers disabled" case worth branching on, and it is a small indexed scan of a table with one row per crawler. `FOR UPDATE` locks only `crawl_queue`; an `IN` subquery in `WHERE` is not a locked relation, so no `crawlers` row is locked by a claim. Both tables are in `GLOBAL_SCHEMA` with no RLS, so this reads identically from an app-pool connection and from a `user_scope` one.

Add a short comment above the line, since the WHY is not obvious from the SQL:

```python
            -- Live gate, re-evaluated every batch: this is what makes an
            -- admin disabling a crawler stop it mid-crawl rather than only
            -- stopping future enqueues.
```

- [ ] **Step 4: Run the tests to make sure they pass**

Run: `pytest tests/test_crawl_queue.py -v`
Expected: PASS, including the existing claim/skip-locked/priority-ordering tests

- [ ] **Step 5: Commit**

```bash
git add backend/db.py backend/tests/test_crawl_queue.py
git commit -F /tmp/commit-msg.txt
```

Message subject: `fix: stop claiming crawl jobs for disabled crawlers`

---

### Task 3: Enqueue guard

Without this the Task 4 purge does not stick. `_sync_collection` reads `enabled_crawlers` once before its page loop (`crawl_manager.py:435`) and then enqueues across every collection page for minutes afterwards; a store disabled mid-sync keeps getting rows re-created after the delete, and the pending count climbs back off zero. `sweep_enqueue` has the same shape across users.

Guarding the insert itself covers all four call sites — `routers/crawl.crawl_start`, `_sync_collection`, `sweep_enqueue`, and `_sync_stock`'s price-crawler fan-out — without touching any of them.

**Files:**
- Modify: `backend/db.py:774-795` — `enqueue_crawl_queue`, `enqueue_crawl_queue_for_stock_item`
- Test: `backend/tests/test_crawl_queue.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: no signature change. Both helpers become no-ops when the target crawler is disabled.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_crawl_queue.py`:

```python
def test_enqueue_crawl_queue_is_a_no_op_for_a_disabled_crawler(admin_conn):
    crawler_id = _make_catalog_and_crawler(admin_conn)
    admin_conn.commit()
    db.set_crawler_enabled(admin_conn, crawler_id, False)
    admin_conn.commit()

    db.enqueue_crawl_queue(admin_conn, "r1", crawler_id)
    admin_conn.commit()
    assert admin_conn.execute("SELECT COUNT(*) FROM crawl_queue").fetchone()["count"] == 0


def test_enqueue_crawl_queue_for_stock_item_is_a_no_op_for_a_disabled_crawler(admin_conn):
    crawler_id = _make_stock_identity_and_crawler(admin_conn)
    admin_conn.commit()
    db.set_crawler_enabled(admin_conn, crawler_id, False)
    admin_conn.commit()

    db.enqueue_crawl_queue_for_stock_item(admin_conn, "key1", crawler_id)
    admin_conn.commit()
    assert admin_conn.execute("SELECT COUNT(*) FROM crawl_queue").fetchone()["count"] == 0


def test_enqueue_crawl_queue_still_resurrects_a_done_row_for_an_enabled_crawler(admin_conn):
    """The ON CONFLICT ... DO UPDATE ... WHERE status = 'done' semantics must
    survive the rewrite to INSERT ... SELECT: without the resurrect, a pair
    would be crawled exactly once, ever."""
    crawler_id = _make_catalog_and_crawler(admin_conn)
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1", crawler_id)
    admin_conn.commit()
    queue_id = admin_conn.execute("SELECT id FROM crawl_queue").fetchone()["id"]
    db.mark_crawl_queue_done(admin_conn, queue_id)
    admin_conn.commit()

    db.enqueue_crawl_queue(admin_conn, "r1", crawler_id)
    admin_conn.commit()
    rows = admin_conn.execute("SELECT status FROM crawl_queue").fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"


def test_enqueue_crawl_queue_still_leaves_an_in_progress_row_alone(admin_conn):
    crawler_id = _make_catalog_and_crawler(admin_conn)
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1", crawler_id)
    admin_conn.execute("UPDATE crawl_queue SET status = 'in_progress'")
    admin_conn.commit()

    db.enqueue_crawl_queue(admin_conn, "r1", crawler_id)
    admin_conn.commit()
    rows = admin_conn.execute("SELECT status FROM crawl_queue").fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "in_progress"
```

- [ ] **Step 2: Run them to make sure the two new no-op tests fail**

Run: `pytest tests/test_crawl_queue.py -k "no_op or resurrects or in_progress_row_alone" -v`
Expected: the two `no_op` tests FAIL with `assert 1 == 0`; the two semantics-preservation tests PASS already (they are regression guards for Step 3, not new behaviour)

- [ ] **Step 3: Rewrite both helpers as guarded `INSERT ... SELECT`**

In `backend/db.py`. Keep the existing block comment above `enqueue_crawl_queue` explaining the load-bearing `DO UPDATE ... WHERE`; add the new WHY for the guard.

```python
# The WHERE EXISTS makes a disabled crawler's enqueue a silent no-op at the
# statement level rather than at each of the four call sites. _sync_collection
# reads its enabled-crawler list once and then enqueues across every collection
# page for minutes afterwards, so a call-site check would let a store disabled
# mid-sync keep re-creating the very rows the disable just purged.
def enqueue_crawl_queue(conn, discogs_id: str, crawler_id: int):
    conn.execute(
        """
        INSERT INTO crawl_queue (discogs_id, crawler_id)
        SELECT %(discogs_id)s, %(crawler_id)s
        WHERE EXISTS (SELECT 1 FROM crawlers WHERE id = %(crawler_id)s AND enabled)
        ON CONFLICT (discogs_id, crawler_id) DO UPDATE SET
            status = 'pending', requested_at = CURRENT_TIMESTAMP, claimed_by = NULL, claimed_at = NULL
        WHERE crawl_queue.status = 'done'
        """,
        {"discogs_id": discogs_id, "crawler_id": crawler_id},
    )


def enqueue_crawl_queue_for_stock_item(conn, item_key: str, crawler_id: int):
    conn.execute(
        """
        INSERT INTO crawl_queue (item_key, crawler_id)
        SELECT %(item_key)s, %(crawler_id)s
        WHERE EXISTS (SELECT 1 FROM crawlers WHERE id = %(crawler_id)s AND enabled)
        ON CONFLICT (item_key, crawler_id) DO UPDATE SET
            status = 'pending', requested_at = CURRENT_TIMESTAMP, claimed_by = NULL, claimed_at = NULL
        WHERE crawl_queue.status = 'done'
        """,
        {"item_key": item_key, "crawler_id": crawler_id},
    )
```

Note the switch from positional to named parameters: `crawler_id` now appears twice in each statement, and psycopg3 resolves repeated named placeholders from one dict. `INSERT ... SELECT` still supports `ON CONFLICT`; when the `WHERE EXISTS` fails, zero rows are inserted and the conflict clause is never reached.

- [ ] **Step 4: Run the whole queue suite to make sure it passes**

Run: `pytest tests/test_crawl_queue.py -v`
Expected: PASS, including the pre-existing `test_enqueue_crawl_queue_is_idempotent`

- [ ] **Step 5: Run the callers' suites for fallout**

Run: `pytest tests/test_crawl_router.py tests/test_crawl_manager.py -v`
Expected: PASS. If a test fails because it registered a crawler, disabled it, and *then* expected an enqueue, that test was asserting the old behaviour — fix it by enqueueing before disabling, and note it in the commit body.

- [ ] **Step 6: Commit**

```bash
git add backend/db.py backend/tests/test_crawl_queue.py
git commit -F /tmp/commit-msg.txt
```

Message subject: `fix: don't enqueue crawl jobs for disabled crawlers`

---

### Task 4: Purge pending rows on disable

**Files:**
- Modify: `backend/db.py` — add `delete_pending_crawl_queue_for_crawler` next to `mark_crawl_queue_done` (around line 842)
- Modify: `backend/routers/settings.py:1-9, 67-72`
- Test: `backend/tests/test_crawl_queue.py`, `backend/tests/test_settings_router.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `db.delete_pending_crawl_queue_for_crawler(conn, crawler_id: int) -> int` — count of rows deleted. `PATCH /api/crawlers/{id}` response becomes `{"ok": true, "discarded": <int>}`, consumed by Task 6.

- [ ] **Step 1: Write the failing db-helper test**

Append to `backend/tests/test_crawl_queue.py`:

```python
def test_delete_pending_crawl_queue_for_crawler_only_deletes_that_crawlers_pending_rows(admin_conn):
    target_id = _make_catalog_and_crawler(admin_conn, discogs_id="r1", site_name="Target Site")
    other_id = _make_catalog_and_crawler(admin_conn, discogs_id="r2", site_name="Other Site")
    admin_conn.commit()
    for discogs_id in ("r1", "r2"):
        db.enqueue_crawl_queue(admin_conn, discogs_id, target_id)
        db.enqueue_crawl_queue(admin_conn, discogs_id, other_id)
    admin_conn.commit()
    admin_conn.execute(
        "UPDATE crawl_queue SET status = 'in_progress' WHERE crawler_id = %s AND discogs_id = 'r1'",
        [target_id],
    )
    admin_conn.execute(
        "UPDATE crawl_queue SET status = 'done' WHERE crawler_id = %s AND discogs_id = 'r2'",
        [other_id],
    )
    admin_conn.commit()

    deleted = db.delete_pending_crawl_queue_for_crawler(admin_conn, target_id)
    admin_conn.commit()

    assert deleted == 1
    remaining = admin_conn.execute(
        "SELECT crawler_id, discogs_id, status FROM crawl_queue ORDER BY crawler_id, discogs_id"
    ).fetchall()
    assert [(r["discogs_id"], r["status"]) for r in remaining if r["crawler_id"] == target_id] == [("r1", "in_progress")]
    assert sorted((r["discogs_id"], r["status"]) for r in remaining if r["crawler_id"] == other_id) == [
        ("r1", "pending"), ("r2", "done"),
    ]
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `pytest tests/test_crawl_queue.py::test_delete_pending_crawl_queue_for_crawler_only_deletes_that_crawlers_pending_rows -v`
Expected: FAIL with `AttributeError: module 'db' has no attribute 'delete_pending_crawl_queue_for_crawler'`

- [ ] **Step 3: Implement the helper**

In `backend/db.py`, below `mark_crawl_queue_done`:

```python
# 'pending' only: an 'in_progress' row is held by a worker's open transaction
# -- it is the current item, which finishes by design, and deleting it would
# block on that worker's row lock until it committed.
def delete_pending_crawl_queue_for_crawler(conn, crawler_id: int) -> int:
    return conn.execute(
        "DELETE FROM crawl_queue WHERE crawler_id = %s AND status = 'pending'",
        [crawler_id],
    ).rowcount
```

- [ ] **Step 4: Run it to make sure it passes**

Run: `pytest tests/test_crawl_queue.py::test_delete_pending_crawl_queue_for_crawler_only_deletes_that_crawlers_pending_rows -v`
Expected: PASS

- [ ] **Step 5: Write the failing endpoint tests**

Append to `backend/tests/test_settings_router.py`. Note the ordering: enqueue *before* disabling, because after Task 3 `enqueue_crawl_queue` is a no-op for a disabled crawler.

```python
def test_patch_crawler_disable_discards_pending_jobs(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1", crawler_id)
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute("UPDATE users SET is_admin = TRUE WHERE id = %s", [user["id"]])
        conn.commit()

    client = authed_client_factory(user["id"])
    r = client.patch(f"/api/crawlers/{crawler_id}", json={"enabled": False}, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "discarded": 1}

    with db.get_admin_pool().connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM crawl_queue").fetchone()["count"] == 0


def test_patch_crawler_enable_discards_nothing(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1", crawler_id)
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute("UPDATE users SET is_admin = TRUE WHERE id = %s", [user["id"]])
        conn.commit()

    client = authed_client_factory(user["id"])
    r = client.patch(f"/api/crawlers/{crawler_id}", json={"enabled": True}, headers={"X-Requested-With": "fetch"})
    assert r.json() == {"ok": True, "discarded": 0}

    with db.get_admin_pool().connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM crawl_queue").fetchone()["count"] == 1
```

- [ ] **Step 6: Run them to make sure they fail**

Run: `pytest tests/test_settings_router.py -k discards -v`
Expected: both FAIL with `assert {'ok': True} == {'ok': True, 'discarded': ...}`

- [ ] **Step 7: Wire the purge into the endpoint**

In `backend/routers/settings.py`, add the logger import alongside the existing imports (the module has none today; `get_logger` is the convention used by the other routers):

```python
from logging_config import get_logger

log = get_logger("routers.settings")
```

Then replace `update_crawler`:

```python
@router.patch("/crawlers/{crawler_id}", dependencies=[Depends(require_admin)])
def update_crawler(crawler_id: int, body: CrawlerUpdate):
    with db.get_app_pool().connection() as conn:
        db.set_crawler_enabled(conn, crawler_id, body.enabled)
        discarded = 0
        if not body.enabled:
            discarded = db.delete_pending_crawl_queue_for_crawler(conn, crawler_id)
        conn.commit()
    if discarded:
        # INFO, not WARNING: routers/logs.py's _line_visible filters by exact
        # level membership, so at WARNING this is invisible to anyone watching
        # the INFO stream that carries the rest of the crawl narrative.
        log.info("Crawler %d disabled: %d pending crawl jobs discarded", crawler_id, discarded)
    return {"ok": True, "discarded": discarded}
```

Both statements share one transaction, so the flag flip and the purge commit together — a worker can never observe a window where the crawler is still enabled but its queue is already empty, or the reverse.

- [ ] **Step 8: Run the settings suite to make sure it passes**

Run: `pytest tests/test_settings_router.py -v`
Expected: PASS, including the pre-existing `test_patch_crawler_as_admin_flips_enabled` and `test_patch_crawler_requires_admin`

- [ ] **Step 9: Commit**

```bash
git add backend/db.py backend/routers/settings.py backend/tests/test_crawl_queue.py backend/tests/test_settings_router.py
git commit -F /tmp/commit-msg.txt
```

Message subject: `feat: discard a disabled crawler's pending crawl jobs`

---

### Task 5: `_sync_stock` re-checks enabled state per catalog source

The loop already re-checks `self._cooling_down_crawler_ids()` per crawler, precisely so a site tripping its limit mid-run takes effect immediately. The enabled check goes in the same place, before the `stock_sync_source_started` broadcast so a skipped source never announces itself.

A store disabled while its own catalog crawl is already in flight finishes and its items are written: the fetch has already happened, the snapshot is complete and valid, and `replace_stock_items` with a partially-paged catalog would wipe good rows for a truncated set.

**Files:**
- Modify: `backend/crawl_manager.py:634-747` — `_sync_stock`
- Test: `backend/tests/test_crawl_manager.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: no signature change.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_crawl_manager.py`, next to the existing `test_sync_stock_skips_a_cooling_down_catalog_crawler` (around line 2140). `logging` and `caplog` usage mirrors `test_sync_stock_completion_log_names_failed_and_skipped_sources` directly above it.

```python
async def test_sync_stock_skips_a_source_disabled_during_the_run(pg_schema):
    """The enabled list is read once at the top of a run. Without a per-source
    re-check, an admin disabling a store mid-sync still gets it crawled when
    the loop reaches it."""
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "First Site", "/a.py", crawler_type="catalog")
        db.register_crawler(conn, "Second Site", "/b.py", crawler_type="catalog")
        conn.commit()
        first_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'First Site'").fetchone()["id"]
        second_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Second Site'").fetchone()["id"]

    second_called = []

    async def _first_items():
        with db.get_admin_pool().connection() as conn:
            db.set_crawler_enabled(conn, second_id, False)
            conn.commit()
        yield {"artist": "A", "title": "T", "url": "https://x/1", "price": 5.0, "currency": "USD"}

    async def _second_items():
        second_called.append(1)
        yield {"artist": "B", "title": "U", "url": "https://x/2", "price": 6.0, "currency": "USD"}

    first = AsyncMock()
    first.crawl_catalog = lambda: _first_items()
    first._db_id = first_id
    first._db_site_name = "First Site"
    second = AsyncMock()
    second.crawl_catalog = lambda: _second_items()
    second._db_id = second_id
    second._db_site_name = "Second Site"

    manager = CrawlManager()
    with patch("crawler.load_enabled_crawlers", return_value=[first, second]):
        await manager._sync_stock()

    assert second_called == []
    with db.get_admin_pool().connection() as conn:
        artists = [r["artist"] for r in conn.execute("SELECT artist FROM stock_items").fetchall()]
    assert artists == ["A"]

    sources = [e.get("source") for e in manager.recent_events() if e["status"] == "stock_sync_source_started"]
    assert sources == ["First Site"]
    statuses = [e["status"] for e in manager.recent_events()]
    assert "stock_sync_complete" in statuses


async def test_sync_stock_completion_log_names_disabled_sources(pg_schema, caplog):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Off Site", "/a.py", crawler_type="catalog")
        conn.commit()
        off_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Off Site'").fetchone()["id"]
        db.set_crawler_enabled(conn, off_id, False)
        conn.commit()

    plugin = AsyncMock()
    called = []
    plugin.crawl_catalog = lambda: called.append(1)
    plugin._db_id = off_id
    plugin._db_site_name = "Off Site"

    manager = CrawlManager()
    with patch("crawler.load_enabled_crawlers", return_value=[plugin]), \
         caplog.at_level(logging.INFO, logger="crawl_manager"):
        await manager._sync_stock()

    assert called == []
    complete = [r.getMessage() for r in caplog.records if "Stock sync complete" in r.getMessage()]
    assert len(complete) == 1
    assert "Off Site" in complete[0]
    assert "disabled" in complete[0]
```

The second test relies on `load_enabled_crawlers` being patched to return the plugin even though the DB row is disabled — that is how every existing test in this file supplies plugins, and it is exactly the state the per-source check has to catch.

- [ ] **Step 2: Run them to make sure they fail**

Run: `pytest tests/test_crawl_manager.py -k "disabled_during_the_run or names_disabled_sources" -v`
Expected: the first FAILS with `assert [1] == []`, the second FAILS on `assert "disabled" in complete[0]`

- [ ] **Step 3: Add the per-source check**

In `backend/crawl_manager.py`'s `_sync_stock`, add `disabled_sources` alongside the existing accumulator lists:

```python
            total_synced = 0
            consecutive_429_sites: list[str] = []
            failed_sources: list[str] = []
            skipped_sources: list[str] = []
            disabled_sources: list[str] = []
```

Then, inside `for crawler in crawlers:`, immediately after the existing cooling-down `continue` block and before the `stock_sync_source_started` broadcast:

```python
                # Re-read per source, not once per run: the enabled list is a
                # snapshot taken before the first crawl, and an admin disabling
                # a store mid-run must stop it being visited when the loop
                # reaches it. One small query per catalog source, single digits
                # per run.
                with get_app_pool().connection() as conn:
                    live_enabled = {
                        c["id"] for c in (
                            get_enabled_crawlers(conn, crawler_type="catalog")
                            + get_enabled_crawlers(conn, crawler_type="catalog_browser")
                        )
                    }
                if crawler._db_id not in live_enabled:
                    disabled_sources.append(crawler._db_site_name)
                    log.info(
                        "[%s] Stock crawl skipped: crawler was disabled during this run",
                        crawler._db_site_name,
                    )
                    continue
```

Finally, add the note to the completion log's tail, after the existing `skipped_sources` note:

```python
            if disabled_sources:
                notes.append(f"{len(disabled_sources)} disabled ({', '.join(disabled_sources)})")
```

- [ ] **Step 4: Run them to make sure they pass**

Run: `pytest tests/test_crawl_manager.py -v`
Expected: PASS, including every existing `_sync_stock` test — the per-crawler-id filter, 429 abort/streak, cooldown, and source-name broadcast tests

- [ ] **Step 5: Commit**

```bash
git add backend/crawl_manager.py backend/tests/test_crawl_manager.py
git commit -F /tmp/commit-msg.txt
```

Message subject: `fix: skip catalog sources disabled during an in-stock sync`

---

### Task 6: Show the discarded count in Settings

**Files:**
- Modify: `frontend/src/api/client.ts:133-140` — `setCrawlerEnabled`
- Modify: `frontend/src/views/Settings.tsx` — `handleToggleCrawler` (around line 237) and the admin "Crawl" cell inside `renderCrawlerTable` (around line 164)
- Test: `frontend/src/test/settings.test.tsx`

**Interfaces:**
- Consumes: the `{"ok": true, "discarded": <int>}` response body from Task 4.
- Produces: `setCrawlerEnabled(id: number, enabled: boolean): Promise<{ ok: boolean; discarded: number }>`.

- [ ] **Step 1: Write the failing tests**

In `frontend/src/test/settings.test.tsx`, first change the hoisted mock's default — `handleToggleCrawler` will destructure the resolved value, and `mockResolvedValue(undefined)` would throw for every existing toggle test:

```typescript
  setCrawlerEnabled: vi.fn().mockResolvedValue({ ok: true, discarded: 0 }),
```

Then append the new cases inside the same `describe` block that holds the existing crawler-table tests:

```typescript
  it('shows the discarded job count on the row that was just disabled', async () => {
    setCrawlerEnabled.mockResolvedValueOnce({ ok: true, discarded: 42 })
    renderSettings({ crawlers: CRAWLERS })
    await settle()

    const row = screen.getByText('Amazon').closest('tr') as HTMLElement
    fireEvent.click(within(row).getByText('Enabled'))
    await waitFor(() => expect(within(row).getByText('42 queued jobs discarded')).toBeInTheDocument())

    const otherRow = screen.getByText('Disabled Site').closest('tr') as HTMLElement
    expect(within(otherRow).queryByText(/queued jobs? discarded/)).not.toBeInTheDocument()
  })

  it('moves the notice when a second crawler is toggled', async () => {
    setCrawlerEnabled
      .mockResolvedValueOnce({ ok: true, discarded: 42 })
      .mockResolvedValueOnce({ ok: true, discarded: 7 })
    renderSettings({ crawlers: CRAWLERS })
    await settle()

    fireEvent.click(within(screen.getByText('Amazon').closest('tr') as HTMLElement).getByText('Enabled'))
    await waitFor(() => expect(screen.getByText('42 queued jobs discarded')).toBeInTheDocument())

    fireEvent.click(within(screen.getByText('Epitaph').closest('tr') as HTMLElement).getByText('Enabled'))
    await waitFor(() => expect(screen.getByText('7 queued jobs discarded')).toBeInTheDocument())
    expect(screen.queryByText('42 queued jobs discarded')).not.toBeInTheDocument()
  })

  it('shows no notice when nothing was discarded', async () => {
    setCrawlerEnabled.mockResolvedValueOnce({ ok: true, discarded: 0 })
    renderSettings({ crawlers: CRAWLERS })
    await settle()

    fireEvent.click(within(screen.getByText('Amazon').closest('tr') as HTMLElement).getByText('Enabled'))
    await settle()
    expect(screen.queryByText(/queued jobs? discarded/)).not.toBeInTheDocument()
  })
```

- [ ] **Step 2: Run them to make sure they fail**

Run (from `frontend/`): `npm test -- --run src/test/settings.test.tsx`
Expected: the three new tests FAIL with "Unable to find an element with the text: 42 queued jobs discarded"; the pre-existing tests PASS

- [ ] **Step 3: Return the response body from the client**

In `frontend/src/api/client.ts`:

```typescript
export async function setCrawlerEnabled(id: number, enabled: boolean): Promise<{ ok: boolean; discarded: number }> {
  const r = await apiFetch(`/crawlers/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}
```

- [ ] **Step 4: Keep and render the count in Settings**

In `frontend/src/views/Settings.tsx`, add the state next to the component's other `useState` calls:

```typescript
  const [discardedNotice, setDiscardedNotice] = useState<{ crawlerId: number; count: number } | null>(null)
```

Update `handleToggleCrawler`:

```typescript
  async function handleToggleCrawler(crawler: Crawler) {
    const { discarded } = await setCrawlerEnabled(crawler.id, !crawler.enabled)
    onCrawlersChange(crawlers.map((c) => c.id === crawler.id ? { ...c, enabled: !c.enabled } : c))
    setDiscardedNotice(discarded ? { crawlerId: crawler.id, count: discarded } : null)
  }
```

Render it in the existing admin "Crawl" cell, after the toggle button:

```tsx
              {isAdmin && (
                <td className="py-3 pr-4 text-left">
                  <button
                    onClick={() => handleToggleCrawler(c)}
                    className={toggleButtonClass(c.enabled)}
                  >
                    {c.enabled ? 'Enabled' : 'Disabled'}
                  </button>
                  {discardedNotice?.crawlerId === c.id && (
                    <span className="ml-2 text-xs text-gray-500">
                      {discardedNotice.count} queued {discardedNotice.count === 1 ? 'job' : 'jobs'} discarded
                    </span>
                  )}
                </td>
              )}
```

Single-slot state, so toggling any other crawler replaces the notice rather than accumulating a column of stale counts, and re-enabling the same crawler clears it (`discarded` is 0 on enable). No timer — the note records what the click did and disappears on the next crawler action or on leaving Settings.

- [ ] **Step 5: Run the frontend suite to make sure it passes**

Run (from `frontend/`): `npm test -- --run src/test/settings.test.tsx`
Expected: PASS

- [ ] **Step 6: Typecheck and run the whole frontend suite**

Run (from `frontend/`): `npm run build && npm test -- --run`
Expected: build succeeds, all suites PASS

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/views/Settings.tsx frontend/src/test/settings.test.tsx
git commit -F /tmp/commit-msg.txt
```

Message subject: `feat: show discarded job count when disabling a crawler`

---

### Task 7: Version bump and pre-PR spec-drift check

Required on every branch by `CLAUDE.md`, and this branch touches four symbols that several existing specs describe.

**Files:**
- Modify: `backend/version.py`
- Modify: whichever specs under `docs/superpowers/specs/` and `docs/specifications/shaping/` have drifted

**Interfaces:**
- Consumes: the finished implementation from Tasks 1–6.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Run the full backend and frontend suites**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest`
Then (from `frontend/`): `npm test -- --run`
Expected: both green. Do not proceed on a failure — fix it in the task that owns it.

- [ ] **Step 2: Bump the version**

In `backend/version.py`: `VERSION = "3.13"` → `VERSION = "3.14"`. Minor bump is the automatic default; a major bump only happens on the repo owner's explicit instruction.

- [ ] **Step 3: Grep both spec trees for the touched symbols**

```bash
grep -rln "claim_crawl_queue_batch\|enqueue_crawl_queue\|start_worker_pool\|_sync_stock\|set_crawler_enabled\|load_enabled_crawlers" docs/superpowers/specs/ docs/specifications/shaping/
```

- [ ] **Step 4: Check each match against what actually shipped**

Read each hit and confirm the spec text still describes the branch's behaviour. Known candidates, from the design doc's drift section:

- `docs/specifications/shaping/2026-08-07-store-crawler-refresh-button-design.md` — its "Enabled-only" decision and its claim that a disabled `crawler_id` hits the "No enabled catalog crawlers" path. There is now a *second* point at which enabled state is consulted (per catalog source, mid-run), which that spec does not mention.
- `docs/superpowers/specs/2026-07-27-crawl-queue-refactor-design.md` — the claim-query description.
- `docs/superpowers/specs/2026-08-01-worker-pool-pacing-design.md` — the boot-time plugin registry.
- `docs/superpowers/specs/2026-08-02-stock-sync-429-backoff-design.md` and `docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md` — the `_sync_stock` loop's skip conditions.
- `CLAUDE.md`'s "Key invariants" — the crawl-is-a-shared-queue bullet does not say that `enabled` gates claiming.

- [ ] **Step 5: Amend any drifted spec**

Short note or inline correction, not a rewrite of history. Follow the amendment style already used in `2026-08-07-store-crawler-refresh-button-design.md` ("**Amendment (found during …)**").

- [ ] **Step 6: Commit**

```bash
git add backend/version.py docs/ CLAUDE.md
git commit -F /tmp/commit-msg.txt
```

Message subject: `chore: bump version to 3.14 and fix spec drift`

If the grep turns up no drift, commit only the version bump with subject `chore: bump version to 3.14`, and record "no drift found" for the PR description.

- [ ] **Step 7: Open the PR**

Ready for review, never a draft — pass `--draft=false`. The description must state what drift was found and fixed (or that none was found), and must name the two accepted consequences from the design doc: re-enabling a store does not resume its queued work (the admin runs "Refresh prices"), and an open in-stock sync progress panel lags by one source because there is no SSE event on disable.

---

## Manual verification

Playwright-driven behaviour is not unit-tested in this repo, so confirm by hand once, against a local stack:

1. Start the backend and frontend. In Settings, enable two release crawlers.
2. Click "Refresh prices" to enqueue a large backlog, and watch the log viewer until items are being crawled for both.
3. Toggle one crawler to "Disabled". Expect: the row shows "N queued jobs discarded", the log shows `Crawler <id> disabled: N pending crawl jobs discarded`, and within one batch no further lines appear for that site while the other keeps going.
4. Re-enable it. Expect: no crawling resumes on its own (the rows were discarded), and no `plugin is None` silent-drop — click "Refresh prices" again and confirm that crawler starts producing listings *without* an app restart. That last point is the Task 1 fix.
5. Start an in-stock sync across several catalog sources, and disable one that has not been reached yet. Expect: it is never announced via `stock_sync_source_started`, and the completion log's tail names it as disabled.
