# Dead Stock Crawl Jobs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop Amazon/eBay price-crawling stock items that no enabled crawler still lists — items from a disabled store, and items that have left every store's stock.

**Architecture:** One SQL predicate — "some enabled crawler still lists this `item_key` in `stock_items`" — built by a fragment helper in `db.py` and used by three statements: a gate inside `claim_crawl_queue_batch`, a guard inside `enqueue_crawl_queue_for_stock_item`, and a new global sweep `delete_dead_stock_crawl_queue_rows()` called on crawler disable and at the end of every stock sync. Nothing with a `discogs_id` changes behaviour.

**Tech Stack:** Python 3.9+, FastAPI, psycopg3 + Postgres, pytest (`asyncio_mode = "auto"`).

**Spec:** [`docs/specifications/shaping/2026-08-10-dead-stock-crawl-jobs-design.md`](../shaping/2026-08-10-dead-stock-crawl-jobs-design.md)

## Global Constraints

- Python ≥3.9. No `str | None` syntax — use `Optional[str]` or leave untyped.
- No comments unless the WHY is non-obvious. No backwards-compat shims.
- Run all tests from `backend/` with the three Postgres env vars:
  `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest`
  Every `Run:` step below assumes that prefix; abbreviated as `pytest ...` for readability.
- A test may never assume pre-existing schema or role state. Build every row the test asserts on inside the test.
- Every commit message is the subject line given in that task's commit step, a blank line, and then the AI-attribution trailer block as its last paragraph. Write it to a file and use `git commit -F <file>`, never `git commit -m` — shell quoting drops trailers:
  ```
  Note: This commit message was created by AI
  ai-generated: true
  ai-model: claude-opus-5
  ai-tool: claude-code
  ai-surface: claude-code-desktop
  ai-executor: local-agent
  ```
- Branch is `claude/dead-stock-crawl-jobs`, already created, spec already committed on it.

## File Structure

- `backend/db.py` — all three statements plus the fragment helper. Tasks 1–3.
- `backend/routers/settings.py` — sweep call site on disable. Task 4.
- `backend/crawl_manager.py` — sweep call site at end of stock sync. Task 5.
- `backend/version.py`, `CLAUDE.md`, `docs/specifications/shaping/2026-08-09-stop-crawling-disabled-stores-design.md` — Task 6.
- `backend/tests/test_crawl_queue.py` — gate and guard tests. Tasks 1–2.
- `backend/tests/test_stock_crud.py` — sweep unit tests. Task 3.
- `backend/tests/test_settings_router.py` — disable-path test. Task 4.
- `backend/tests/test_crawl_manager.py` — existing stock-item fixtures need a source (Task 1); stock-sync sweep test (Task 5).

`backend/tests/test_global_schema.py` inserts `crawl_queue` rows directly to
assert schema constraints. It touches none of the three changed statements and
needs no edit — verified before writing this plan, so don't go looking.

---

### Task 1: Predicate helper and claim-time gate

**Files:**
- Modify: `backend/db.py` (add `_enabled_stock_source_exists` above `enqueue_crawl_queue` at line 788; edit `claim_crawl_queue_batch` at line 826)
- Test: `backend/tests/test_crawl_queue.py` (rewrite `_make_stock_identity_and_crawler` at line 26; add new tests)
- Test: `backend/tests/test_crawl_manager.py` (give the eight existing stock-item fixtures a source)

**Interfaces:**
- Produces: `db._enabled_stock_source_exists(item_key_expr: str) -> str` — returns a SQL `EXISTS (...)` fragment. `item_key_expr` is a literal chosen by the caller: a column reference like `"crawl_queue.item_key"` or a placeholder like `"%(item_key)s"`. Tasks 2 and 3 both call it.
- Produces: `_make_stock_identity_and_crawler(conn, item_key="key1", site_name="Amazon", source_site_name=None) -> int` — unchanged return (the *price* crawler's id), now also registering an enabled catalog crawler named `f"{site_name} Source"` (unless overridden) and inserting the `stock_items` row that links it to `item_key`. Task 2 uses it.

**Context:** `claim_crawl_queue_batch`'s existing enabled subquery checks `crawl_queue.crawler_id` — the crawler about to *do* the work (Amazon/eBay). A stock row's source store is reachable only through `stock_items.crawler_id` joined on `item_key`, which nothing currently consults.

- [ ] **Step 1: Update the test helper so stock items have a source**

The current helper inserts a `stock_item_identities` row and no `stock_items` row, so every existing stock test would fail the gate this task adds. Replace `_make_stock_identity_and_crawler` in `backend/tests/test_crawl_queue.py:26-31` with:

```python
def _make_stock_identity_and_crawler(conn, item_key="key1", site_name="Amazon", source_site_name=None):
    """Mirrors production: the item's source is a catalog crawler and the queue
    row's crawler_id is a separate price crawler. Keeping them distinct is what
    lets a test disable the source without disabling the crawler under test."""
    conn.execute(
        "INSERT INTO stock_item_identities (item_key, artist, title) VALUES (%s, 'A', 'T') "
        "ON CONFLICT (item_key) DO NOTHING",
        [item_key],
    )
    source_site_name = source_site_name or f"{site_name} Source"
    db.register_crawler(conn, source_site_name, "/src.py", crawler_type="catalog")
    source_id = conn.execute(
        "SELECT id FROM crawlers WHERE site_name = %s", [source_site_name]
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO stock_items (crawler_id, artist, title, url, item_key) "
        "VALUES (%s, 'A', 'T', %s, %s)",
        [source_id, f"https://x/{item_key}/{source_site_name}", item_key],
    )
    db.register_crawler(conn, site_name, "/x.py")
    return conn.execute("SELECT id FROM crawlers WHERE site_name = %s", [site_name]).fetchone()["id"]
```

And add this helper directly below it:

```python
def _set_enabled_by_name(conn, site_name, enabled):
    crawler_id = conn.execute(
        "SELECT id FROM crawlers WHERE site_name = %s", [site_name]
    ).fetchone()["id"]
    db.set_crawler_enabled(conn, crawler_id, enabled)
```

- [ ] **Step 2: Give `test_crawl_manager.py`'s stock-item fixtures a source**

That file builds stock-item queue rows at eight sites, each inserting a
`stock_item_identities` row and no `stock_items` row. All eight would break on
this task's gate (nothing claims) and again on Task 2's guard (nothing
enqueues). Add this helper at module level, next to the file's other
module-level helpers:

```python
def _stock_item_with_source(conn, item_key="key1", source_site_name="Source Store"):
    """A stock item needs an enabled crawler stocking it, or the source gate in
    claim_crawl_queue_batch treats it as dead and never claims its jobs."""
    conn.execute(
        "INSERT INTO stock_item_identities (item_key, artist, title) VALUES (%s, 'A', 'T') "
        "ON CONFLICT (item_key) DO NOTHING",
        [item_key],
    )
    db.register_crawler(conn, source_site_name, "/src.py", crawler_type="catalog")
    source_id = conn.execute(
        "SELECT id FROM crawlers WHERE site_name = %s", [source_site_name]
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO stock_items (crawler_id, artist, title, url, item_key) "
        "VALUES (%s, 'A', 'T', %s, %s)",
        [source_id, f"https://src/{item_key}", item_key],
    )
```

Then replace the raw identity insert at each of these sites with a call to it,
leaving every surrounding line alone:

| Line | Current | Replacement |
| --- | --- | --- |
| 1181-1183 | `conn.execute("INSERT INTO stock_item_identities ... VALUES ('key1', 'A', 'T')")` | `_stock_item_with_source(conn, "key1")` |
| 1217-1219 | same | `_stock_item_with_source(conn, "key1")` |
| 1253-1255 | same | `_stock_item_with_source(conn, "key1")` |
| 1315-1317 | same | `_stock_item_with_source(conn, "key1")` |
| 1347-1349 | same | `_stock_item_with_source(conn, "key1")` |
| 1380-1382 | same | `_stock_item_with_source(conn, "key1")` |
| 1694-1697 | the parameterised insert inside `for i in range(3):` | `_stock_item_with_source(conn, f"key{i}")` |
| 1743 | the single-line insert for `'key1'` | `_stock_item_with_source(conn, "key1")` |

Line numbers are from the pre-edit file and shift as you go — match on the
`INSERT INTO stock_item_identities` text rather than trusting them. The extra
registered catalog crawler is inert in every one of these tests: they all pass
an explicit `plugins_by_crawler_id` dict to `_drain_one_batch` rather than
reading the crawler table.

- [ ] **Step 3: Run both test files to confirm they still pass**

Run: `pytest tests/test_crawl_queue.py tests/test_crawl_manager.py -v`
Expected: PASS — both helper changes are additive, and the gate does not exist yet.

- [ ] **Step 4: Write the failing tests**

Append to `backend/tests/test_crawl_queue.py`:

```python
def test_claim_crawl_queue_batch_skips_a_stock_item_whose_only_source_is_disabled(admin_conn):
    crawler_id = _make_stock_identity_and_crawler(admin_conn)
    admin_conn.commit()
    db.enqueue_crawl_queue_for_stock_item(admin_conn, "key1", crawler_id)
    _set_enabled_by_name(admin_conn, "Amazon Source", False)
    admin_conn.commit()

    with db.get_app_pool().connection() as conn:
        assert db.claim_crawl_queue_batch(conn, "worker-1", limit=10) == []
        conn.commit()


def test_claim_crawl_queue_batch_claims_a_stock_item_again_once_its_source_is_re_enabled(admin_conn):
    crawler_id = _make_stock_identity_and_crawler(admin_conn)
    admin_conn.commit()
    db.enqueue_crawl_queue_for_stock_item(admin_conn, "key1", crawler_id)
    _set_enabled_by_name(admin_conn, "Amazon Source", False)
    admin_conn.commit()

    _set_enabled_by_name(admin_conn, "Amazon Source", True)
    admin_conn.commit()

    with db.get_app_pool().connection() as conn:
        claimed = db.claim_crawl_queue_batch(conn, "worker-1", limit=10)
        conn.commit()
    assert [r["item_key"] for r in claimed] == ["key1"]


def test_claim_crawl_queue_batch_skips_a_stock_item_with_no_stock_items_row(admin_conn):
    """A sold-out item: replace_stock_items dropped its stock_items row, while
    its identity and its queue rows survived. Inserted directly rather than via
    enqueue_crawl_queue_for_stock_item, which refuses such a row -- this is the
    shape of a row that predates that guard."""
    crawler_id = _make_stock_identity_and_crawler(admin_conn, item_key="key1")
    admin_conn.execute(
        "INSERT INTO stock_item_identities (item_key, artist, title) VALUES ('gone', 'A', 'T')"
    )
    admin_conn.execute(
        "INSERT INTO crawl_queue (item_key, crawler_id) VALUES ('gone', %s)", [crawler_id]
    )
    admin_conn.commit()

    with db.get_app_pool().connection() as conn:
        assert db.claim_crawl_queue_batch(conn, "worker-1", limit=10) == []
        conn.commit()


def test_claim_crawl_queue_batch_claims_a_stock_item_with_one_enabled_source_of_two(admin_conn):
    """'No enabled source remains' -- one surviving enabled source is enough."""
    crawler_id = _make_stock_identity_and_crawler(admin_conn, item_key="key1")
    _make_stock_identity_and_crawler(
        admin_conn, item_key="key1", site_name="Amazon", source_site_name="Second Source"
    )
    admin_conn.commit()
    db.enqueue_crawl_queue_for_stock_item(admin_conn, "key1", crawler_id)
    _set_enabled_by_name(admin_conn, "Amazon Source", False)
    admin_conn.commit()

    with db.get_app_pool().connection() as conn:
        claimed = db.claim_crawl_queue_batch(conn, "worker-1", limit=10)
        conn.commit()
    assert [r["item_key"] for r in claimed] == ["key1"]


def test_claim_crawl_queue_batch_still_claims_release_rows_when_a_catalog_crawler_is_disabled(admin_conn):
    """Release rows have a NULL item_key and must be untouched by the source gate."""
    release_crawler_id = _make_catalog_and_crawler(admin_conn, discogs_id="r1", site_name="eBay")
    _make_stock_identity_and_crawler(admin_conn, item_key="key1", site_name="Amazon")
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1", release_crawler_id)
    _set_enabled_by_name(admin_conn, "Amazon Source", False)
    admin_conn.commit()

    with db.get_app_pool().connection() as conn:
        claimed = db.claim_crawl_queue_batch(conn, "worker-1", limit=10)
        conn.commit()
    assert [r["discogs_id"] for r in claimed] == ["r1"]
```

- [ ] **Step 5: Run the new tests to verify they fail**

Run: `pytest tests/test_crawl_queue.py -v`
Expected: two FAIL — `..._skips_a_stock_item_whose_only_source_is_disabled` and
`..._skips_a_stock_item_with_no_stock_items_row`, both asserting `== []` but
getting a claimed row. The other three new tests pass already; they are
regression guards for the change below, not drivers of it.

- [ ] **Step 6: Add the predicate helper**

In `backend/db.py`, directly above the `# The WHERE on the DO UPDATE is load-bearing...` comment block that precedes `enqueue_crawl_queue` (line 788):

```python
def _enabled_stock_source_exists(item_key_expr: str) -> str:
    """A stock item is worth crawling only while some enabled crawler still
    lists it. One predicate covers two populations: an item whose store an
    admin disabled, and an item that has left every store's stock --
    replace_stock_items() deletes a crawler's whole batch and reinserts only
    what is currently in stock, so a sold-out item loses its stock_items row
    while its stock_item_identities row and its queue rows survive.

    item_key_expr is always a literal chosen at the call site -- a column
    reference or a bound-parameter placeholder, never request-derived -- the
    same contract _library_match_fragment's user_id_param already carries."""
    return f"""
        EXISTS (
            SELECT 1 FROM stock_items si
            JOIN crawlers sc ON sc.id = si.crawler_id
            WHERE si.item_key = {item_key_expr} AND sc.enabled
        )
    """
```

- [ ] **Step 7: Add the gate to the claim query**

In `claim_crawl_queue_batch` (`backend/db.py:826`), add a local above the `return conn.execute(...)`, immediately after the existing `exclusion_clause` block:

```python
    stock_source_gate = _enabled_stock_source_exists("crawl_queue.item_key")
```

(A local, not an inline `{...}` expression: the surrounding f-string is triple-double-quoted and this repo targets Python ≥3.9, where nesting quotes inside an f-string expression is a trap worth sidestepping.)

Then add one line to the inner `SELECT`'s `WHERE`, directly below the existing `AND crawler_id IN (SELECT id FROM crawlers WHERE enabled)` line and above `{exclusion_clause}`:

```sql
              -- Source-side gate: the row above checks the crawler about to do
              -- the work; this checks whether anything still stocks the item it
              -- would price. item_key IS NULL keeps release rows out of it.
              AND (item_key IS NULL OR {stock_source_gate})
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `pytest tests/test_crawl_queue.py tests/test_crawl_manager.py -v`
Expected: PASS, all of them — the new tests, every pre-existing queue test, and
the eight stock-item worker tests re-sourced in Step 2.

- [ ] **Step 9: Commit**

```bash
git add backend/db.py backend/tests/test_crawl_queue.py backend/tests/test_crawl_manager.py
git commit -F <message-file>
```

Message subject: `feat: skip stock crawl jobs whose sources are all disabled`

---

### Task 2: Enqueue guard

**Files:**
- Modify: `backend/db.py:802-816` (`enqueue_crawl_queue_for_stock_item`)
- Test: `backend/tests/test_crawl_queue.py`

**Interfaces:**
- Consumes: `db._enabled_stock_source_exists` and the test helpers from Task 1.

**Context:** `_sync_stock` reads its enabled catalog set, crawls one store for minutes, then enqueues. Under READ COMMITTED an enqueue that began before a disable committed still evaluates against the older snapshot, so a purge alone cannot stop rows appearing after it. The guard is satisfiable when it should be: `replace_stock_items` and the enqueue fan-out share one transaction and one connection (`crawl_manager.py:746-755`), so the `stock_items` rows this looks for were inserted by the statement immediately above it.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_crawl_queue.py`:

```python
def test_enqueue_crawl_queue_for_stock_item_inserts_nothing_when_the_source_is_disabled(admin_conn):
    crawler_id = _make_stock_identity_and_crawler(admin_conn)
    _set_enabled_by_name(admin_conn, "Amazon Source", False)
    admin_conn.commit()

    db.enqueue_crawl_queue_for_stock_item(admin_conn, "key1", crawler_id)
    admin_conn.commit()
    rows = admin_conn.execute("SELECT * FROM crawl_queue WHERE item_key = 'key1'").fetchall()
    assert rows == []


def test_enqueue_crawl_queue_for_stock_item_inserts_nothing_when_the_item_has_no_stock_row(admin_conn):
    crawler_id = _make_stock_identity_and_crawler(admin_conn, item_key="key1")
    admin_conn.execute(
        "INSERT INTO stock_item_identities (item_key, artist, title) VALUES ('gone', 'A', 'T')"
    )
    admin_conn.commit()

    db.enqueue_crawl_queue_for_stock_item(admin_conn, "gone", crawler_id)
    admin_conn.commit()
    rows = admin_conn.execute("SELECT * FROM crawl_queue WHERE item_key = 'gone'").fetchall()
    assert rows == []
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `pytest tests/test_crawl_queue.py -k "inserts_nothing" -v`
Expected: FAIL — both assert `rows == []` but get one queued row each.

- [ ] **Step 3: Add the guard**

Replace the body of `enqueue_crawl_queue_for_stock_item` (`backend/db.py:802-816`) with:

```python
def enqueue_crawl_queue_for_stock_item(conn, item_key: str, crawler_id: int):
    stock_source_gate = _enabled_stock_source_exists("%(item_key)s")
    conn.execute(
        f"""
        INSERT INTO crawl_queue (item_key, crawler_id)
        SELECT %(item_key)s, %(crawler_id)s
        WHERE EXISTS (SELECT 1 FROM crawlers WHERE id = %(crawler_id)s AND enabled)
          AND {stock_source_gate}
        ON CONFLICT (item_key, crawler_id) DO UPDATE SET
            status = 'pending', requested_at = CURRENT_TIMESTAMP, claimed_by = NULL, claimed_at = NULL
        WHERE crawl_queue.status = 'done'
        """,
        {"item_key": item_key, "crawler_id": crawler_id},
    )
```

The two guards answer different questions and both are needed: the first is "may this crawler run work", the second is "is this item still stocked anywhere". `INSERT ... SELECT` keeps supporting `ON CONFLICT`, so the resurrect-a-`done`-row semantics are unchanged; when the `WHERE` fails, zero rows are inserted and the conflict clause is never reached.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_crawl_queue.py tests/test_crawl_manager.py -v`
Expected: PASS, all of them. `test_enqueue_crawl_queue_for_stock_item_is_idempotent`, `..._resets_done_row_to_pending`, and every stock-item test in `test_crawl_manager.py` still pass because the Task 1 helpers give their items an enabled source.

- [ ] **Step 5: Commit**

```bash
git add backend/db.py backend/tests/test_crawl_queue.py
git commit -F <message-file>
```

Message subject: `feat: refuse to enqueue stock crawl jobs with no enabled source`

---

### Task 3: The sweep

**Files:**
- Modify: `backend/db.py` (add `delete_dead_stock_crawl_queue_rows` directly below `delete_pending_crawl_queue_for_crawler` at line 871-876)
- Test: `backend/tests/test_stock_crud.py`

**Interfaces:**
- Consumes: `db._enabled_stock_source_exists` from Task 1.
- Produces: `db.delete_dead_stock_crawl_queue_rows(conn) -> int` — deletes every `pending` row with a non-NULL `item_key` failing the predicate, returns the rowcount. Tasks 4 and 5 both call it.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_stock_crud.py`:

```python
def _stock_row(conn, item_key, source_site_name, price_site_name="Amazon", source_enabled=True):
    """Builds the production shape: a catalog crawler stocking an item, a
    separate price crawler, and a pending queue row for the pair. Returns the
    price crawler's id."""
    db.register_crawler(conn, source_site_name, "/src.py", crawler_type="catalog")
    source_id = conn.execute(
        "SELECT id FROM crawlers WHERE site_name = %s", [source_site_name]
    ).fetchone()["id"]
    db.register_crawler(conn, price_site_name, "/price.py")
    price_id = conn.execute(
        "SELECT id FROM crawlers WHERE site_name = %s", [price_site_name]
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO stock_item_identities (item_key, artist, title) VALUES (%s, 'A', 'T') "
        "ON CONFLICT (item_key) DO NOTHING",
        [item_key],
    )
    conn.execute(
        "INSERT INTO stock_items (crawler_id, artist, title, url, item_key) "
        "VALUES (%s, 'A', 'T', %s, %s)",
        [source_id, f"https://x/{item_key}", item_key],
    )
    conn.execute(
        "INSERT INTO crawl_queue (item_key, crawler_id) VALUES (%s, %s)", [item_key, price_id]
    )
    if not source_enabled:
        db.set_crawler_enabled(conn, source_id, False)
    return price_id


def test_delete_dead_stock_crawl_queue_rows_deletes_a_disabled_source_row(admin_conn):
    _stock_row(admin_conn, "key1", "Dead Store", source_enabled=False)
    admin_conn.commit()

    assert db.delete_dead_stock_crawl_queue_rows(admin_conn) == 1
    admin_conn.commit()
    assert admin_conn.execute("SELECT COUNT(*) FROM crawl_queue").fetchone()["count"] == 0


def test_delete_dead_stock_crawl_queue_rows_deletes_a_row_whose_item_has_no_stock_row(admin_conn):
    price_id = _stock_row(admin_conn, "key1", "Live Store")
    admin_conn.execute("DELETE FROM stock_items WHERE item_key = 'key1'")
    admin_conn.execute(
        "INSERT INTO stock_item_identities (item_key, artist, title) VALUES ('gone', 'A', 'T')"
    )
    admin_conn.execute(
        "INSERT INTO crawl_queue (item_key, crawler_id) VALUES ('gone', %s)", [price_id]
    )
    admin_conn.commit()

    assert db.delete_dead_stock_crawl_queue_rows(admin_conn) == 2
    admin_conn.commit()
    assert admin_conn.execute("SELECT COUNT(*) FROM crawl_queue").fetchone()["count"] == 0


def test_delete_dead_stock_crawl_queue_rows_keeps_a_live_row(admin_conn):
    _stock_row(admin_conn, "key1", "Live Store")
    admin_conn.commit()

    assert db.delete_dead_stock_crawl_queue_rows(admin_conn) == 0
    admin_conn.commit()
    assert admin_conn.execute("SELECT COUNT(*) FROM crawl_queue").fetchone()["count"] == 1


def test_delete_dead_stock_crawl_queue_rows_keeps_in_progress_and_done_rows(admin_conn):
    """in_progress rows belong to a worker's open transaction and finish by
    design; done rows are the historical record and are never re-claimed."""
    _stock_row(admin_conn, "key1", "Dead Store", source_enabled=False)
    _stock_row(admin_conn, "key2", "Dead Store", source_enabled=False)
    admin_conn.execute("UPDATE crawl_queue SET status = 'in_progress' WHERE item_key = 'key1'")
    admin_conn.execute("UPDATE crawl_queue SET status = 'done' WHERE item_key = 'key2'")
    admin_conn.commit()

    assert db.delete_dead_stock_crawl_queue_rows(admin_conn) == 0
    admin_conn.commit()
    assert admin_conn.execute("SELECT COUNT(*) FROM crawl_queue").fetchone()["count"] == 2


def test_delete_dead_stock_crawl_queue_rows_keeps_release_rows(admin_conn):
    db.register_crawler(admin_conn, "Amazon", "/price.py")
    price_id = admin_conn.execute(
        "SELECT id FROM crawlers WHERE site_name = 'Amazon'"
    ).fetchone()["id"]
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.enqueue_crawl_queue(admin_conn, "r1", price_id)
    admin_conn.commit()

    assert db.delete_dead_stock_crawl_queue_rows(admin_conn) == 0
    admin_conn.commit()
    assert admin_conn.execute("SELECT COUNT(*) FROM crawl_queue").fetchone()["count"] == 1
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `pytest tests/test_stock_crud.py -k dead_stock -v`
Expected: FAIL, all five, with `AttributeError: module 'db' has no attribute 'delete_dead_stock_crawl_queue_rows'`.

- [ ] **Step 3: Implement the sweep**

In `backend/db.py`, directly below `delete_pending_crawl_queue_for_crawler` (which ends at line 876):

```python
# Global rather than scoped to one crawler: idempotent, self-correcting, and it
# also clears residue predating the source gate. The cost is that the count a
# disable reports can include rows from another store's delisted items -- it
# means "jobs that are now dead", not "jobs this store created".
#
# 'pending' only, for the same reason as delete_pending_crawl_queue_for_crawler:
# an in_progress row is held by a worker's open transaction. 'done' rows are the
# record of past crawls and are never re-claimed -- only
# enqueue_crawl_queue_for_stock_item resurrects one, and it now refuses to.
def delete_dead_stock_crawl_queue_rows(conn) -> int:
    stock_source_gate = _enabled_stock_source_exists("crawl_queue.item_key")
    return conn.execute(
        f"""
        DELETE FROM crawl_queue
        WHERE status = 'pending'
          AND item_key IS NOT NULL
          AND NOT {stock_source_gate}
        """
    ).rowcount
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_stock_crud.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/db.py backend/tests/test_stock_crud.py
git commit -F <message-file>
```

Message subject: `feat: add a sweep for stock crawl jobs with no enabled source`

---

### Task 4: Sweep on crawler disable

**Files:**
- Modify: `backend/routers/settings.py:69-82` (`update_crawler`)
- Test: `backend/tests/test_settings_router.py`

**Interfaces:**
- Consumes: `db.delete_dead_stock_crawl_queue_rows(conn) -> int` from Task 3.

**Context:** The response shape stays `{"ok": True, "discarded": N}` and the frontend is untouched — `discarded` becomes the sum of both purges. Both statements stay inside the existing single transaction with `set_crawler_enabled`, so the flag flip and the purges commit together.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_settings_router.py`:

```python
def test_patch_crawler_disable_discards_dead_stock_jobs(pg_test_db, authed_client_factory):
    """Disabling a store discards the Amazon/eBay jobs queued for its items --
    rows that carry the price crawler's id, not the store's, so the
    crawler-scoped purge alone never matched them."""
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Dead Store", "/src.py", crawler_type="catalog")
        store_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Dead Store'").fetchone()["id"]
        db.register_crawler(conn, "Amazon", "/price.py")
        amazon_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        conn.execute(
            "INSERT INTO stock_item_identities (item_key, artist, title) VALUES ('key1', 'A', 'T')"
        )
        conn.execute(
            "INSERT INTO stock_items (crawler_id, artist, title, url, item_key) "
            "VALUES (%s, 'A', 'T', 'https://x/1', 'key1')",
            [store_id],
        )
        db.enqueue_crawl_queue_for_stock_item(conn, "key1", amazon_id)
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute("UPDATE users SET is_admin = TRUE WHERE id = %s", [user["id"]])
        conn.commit()

    client = authed_client_factory(user["id"])
    r = client.patch(f"/api/crawlers/{store_id}", json={"enabled": False}, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "discarded": 1}

    with db.get_admin_pool().connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM crawl_queue").fetchone()["count"] == 0
```

- [ ] **Step 2: Run the new test to verify it fails**

Run: `pytest tests/test_settings_router.py -k dead_stock -v`
Expected: FAIL — `discarded` is `0` and the queue row survives, because `delete_pending_crawl_queue_for_crawler(store_id)` matches nothing.

- [ ] **Step 3: Call the sweep on disable**

In `backend/routers/settings.py`, replace the `if not body.enabled:` block inside `update_crawler`:

```python
        discarded = 0
        if not body.enabled:
            discarded = (
                db.delete_pending_crawl_queue_for_crawler(conn, crawler_id)
                + db.delete_dead_stock_crawl_queue_rows(conn)
            )
```

Leave the existing log line and return statement exactly as they are.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_settings_router.py -v`
Expected: PASS, including the three pre-existing `discarded` tests — a release-crawler disable still reports its own count, since no stock row is dead in those fixtures.

- [ ] **Step 5: Commit**

```bash
git add backend/routers/settings.py backend/tests/test_settings_router.py
git commit -F <message-file>
```

Message subject: `feat: discard dead stock crawl jobs when a store is disabled`

---

### Task 5: Sweep at the end of a stock sync

**Files:**
- Modify: `backend/crawl_manager.py:756-760` (end of `_sync_stock`'s source loop)
- Test: `backend/tests/test_crawl_manager.py`

**Interfaces:**
- Consumes: `db.delete_dead_stock_crawl_queue_rows(conn) -> int` from Task 3, and `_stock_item_with_source(conn, item_key, source_site_name)` added to this test file in Task 1.

**Context:** This is what catches items that left a store's stock — no disable happens, so nothing else would. Once per run, not per source: the statement is global, and running it per store would repeat identical work.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_crawl_manager.py`:

```python
async def test_sync_stock_sweeps_dead_stock_jobs_at_end_of_run(pg_schema):
    """Nothing disables a store when an item merely sells out, so the end-of-run
    sweep is the only thing that stops it being priced forever."""
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Stock Site", "/x.py", crawler_type="catalog")
        db.register_crawler(conn, "Amazon", "/amazon.py", crawler_type="release")
        _stock_item_with_source(conn, "dead", source_site_name="Dead Store")
        amazon_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        dead_store_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Dead Store'").fetchone()["id"]
        db.enqueue_crawl_queue_for_stock_item(conn, "dead", amazon_id)
        db.set_crawler_enabled(conn, dead_store_id, False)
        conn.commit()

    fake_plugin = AsyncMock()

    async def _items():
        yield {"artist": "A", "title": "T", "url": "https://x/1", "price": 5.0, "currency": "USD"}

    fake_plugin.crawl_catalog = lambda: _items()
    fake_plugin._db_site_name = "Stock Site"
    with db.get_admin_pool().connection() as conn:
        fake_plugin._db_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Stock Site'").fetchone()["id"]

    manager = CrawlManager()
    with patch("crawler.load_enabled_crawlers", return_value=[fake_plugin]):
        await manager._sync_stock()

    live_key = db.compute_item_key("A".title(), "T", "https://x/1")
    with db.get_admin_pool().connection() as conn:
        keys = [r["item_key"] for r in conn.execute("SELECT item_key FROM crawl_queue").fetchall()]
    assert keys == [live_key]
```

- [ ] **Step 2: Run the new test to verify it fails**

Run: `pytest tests/test_crawl_manager.py -k sweeps_dead -v`
Expected: FAIL — `keys` contains `'dead'` alongside the live key.

- [ ] **Step 3: Call the sweep at the end of the run**

In `backend/crawl_manager.py`, between the end of the `for crawler in crawlers:` loop (the `stock_sync_progress` broadcast at line 758) and the `stock_sync_complete` broadcast at line 760:

```python
            with get_app_pool().connection() as conn:
                swept = delete_dead_stock_crawl_queue_rows(conn)
                conn.commit()
            if swept:
                # INFO, not WARNING: routers/logs.py's _line_visible filters by
                # exact level membership, so at WARNING this would be invisible
                # to anyone watching the INFO stream carrying the rest of the
                # crawl narrative.
                log.info("Discarded %d queued price lookups with no enabled source", swept)
```

Add `delete_dead_stock_crawl_queue_rows` to the `from db import ...` line at the top of `_sync_stock` (line 643).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_crawl_manager.py -v`
Expected: PASS, including every pre-existing `_sync_stock` test — their items are stocked by an enabled crawler, so the sweep deletes nothing.

- [ ] **Step 5: Run the whole backend suite**

Run: `pytest`
Expected: PASS. This is the first point where every consumer of the three changed statements has been exercised together.

- [ ] **Step 6: Commit**

```bash
git add backend/crawl_manager.py backend/tests/test_crawl_manager.py
git commit -F <message-file>
```

Message subject: `feat: sweep dead stock crawl jobs at the end of each stock sync`

---

### Task 6: Version bump and spec drift

**Files:**
- Modify: `backend/version.py:1`
- Modify: `docs/specifications/shaping/2026-08-09-stop-crawling-disabled-stores-design.md:4`
- Modify: `CLAUDE.md:42`

**Context:** `CLAUDE.md`'s pre-PR spec-drift check is required on every branch. The 2026-08-09 spec and the `crawlers.enabled` invariant both describe the enabled gate as being about the crawling crawler alone, which this branch makes incomplete.

- [ ] **Step 1: Bump the version**

`backend/version.py` — minor bump, the default for every PR that merges to `main`:

```python
VERSION = "3.15"
```

- [ ] **Step 2: Amend the 2026-08-09 spec**

In `docs/specifications/shaping/2026-08-09-stop-crawling-disabled-stores-design.md`, insert directly below the `Branch:` line (line 4) and above the blank line preceding `## Problem`:

```markdown

> **2026-08-10 amendment.** Every gate below checks the crawler *doing* the
> crawling. On the stock-item path the crawler the work came *from* is a
> different row, and none of this reached it: disabling a store left its items'
> Amazon/eBay jobs queued and running. Extended by
> [2026-08-10-dead-stock-crawl-jobs-design.md](2026-08-10-dead-stock-crawl-jobs-design.md),
> which adds a source-side predicate to `claim_crawl_queue_batch`,
> `enqueue_crawl_queue_for_stock_item`, and a new global sweep. "Out of scope:
> existing data" below still holds for recorded `listings`/`stock_items` rows;
> it never meant queued work.
```

- [ ] **Step 3: Amend the CLAUDE.md invariant**

In `CLAUDE.md:42`, inside the `crawlers.enabled` bullet, replace this fragment:

```
and `_sync_stock` re-reads the enabled catalog set per source.
```

with:

```
and `_sync_stock` re-reads the enabled catalog set per source. A stock-item queue row carries the *price* crawler's id, never the source store's, so those gates alone never reached it: `claim_crawl_queue_batch` and `enqueue_crawl_queue_for_stock_item` additionally require an enabled crawler to still list the `item_key` in `stock_items`, and `db.delete_dead_stock_crawl_queue_rows` sweeps rows failing that on disable and at the end of each stock sync — see [`docs/specifications/shaping/2026-08-10-dead-stock-crawl-jobs-design.md`](docs/specifications/shaping/2026-08-10-dead-stock-crawl-jobs-design.md).
```

- [ ] **Step 4: Grep for any other drift**

Run:
```bash
grep -rln "claim_crawl_queue_batch\|enqueue_crawl_queue_for_stock_item\|delete_pending_crawl_queue_for_crawler" docs/superpowers/specs/ docs/specifications/shaping/
```
Read each hit and confirm its text still describes what shipped. Amend any that no longer do; record in the PR description what was found (or that nothing else was).

- [ ] **Step 5: Run the full suite one more time**

Run: `pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/version.py CLAUDE.md docs/specifications/shaping/2026-08-09-stop-crawling-disabled-stores-design.md
git commit -F <message-file>
```

Message subject: `docs: bump version and fix disabled-crawler spec drift`

---

## Done when

- `pytest` passes from `backend/` with the three Postgres env vars set.
- A pending stock job is not claimed when every crawler stocking its `item_key` is disabled, nor when nothing stocks it at all.
- Disabling a store returns a `discarded` count covering the price-crawler jobs queued for its items, and leaves none of them pending.
- A stock sync ends by discarding queued price lookups for items that left every enabled store's stock.
- Release rows (`discogs_id`) behave exactly as before, at enqueue, claim, and sweep.
