# Release-Crawler Stock Items Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a release crawler (Amazon, eBay/CCmusic, Discogs Marketplace, ...) finds a match for a release in a user's collection or wishlist, write it into `stock_items` too — not just `listings` — so it shows up in the Store and Track tabs like any catalog-crawler find, live.

**Architecture:** `_drain_one_batch`'s release branch already writes one `listings` row per successful search. It gains a second write, into `stock_items`, keyed by a new `(crawler_id, release_id)` unique pair — insert/update on a match, delete on a clean "not found." Everything downstream (Store tab, Track tab, sorting, pagination, tile view, recommended, artist facets) already reads `stock_items` and needs no changes. The frontend gains one more SSE case so these live-repaint the same way catalog-crawl finds already do.

**Tech Stack:** Python 3.9+, FastAPI, psycopg3 + Postgres (backend); React + TypeScript + Vite + Vitest (frontend).

**Spec:** [`docs/specifications/shaping/2026-08-11-release-crawler-stock-items-design.md`](../shaping/2026-08-11-release-crawler-stock-items-design.md)

## Global Constraints

- Python ≥3.9. No `str | None` syntax — use `Optional[str]` or leave untyped.
- No comments unless the WHY is non-obvious. No backwards-compat shims.
- Never hand-edit `backend/version.py` — `VERSION` is derived from git at import time (see `2026-08-10-derived-version-design.md`). No task in this plan touches it.
- Run backend tests from `backend/` with the three Postgres env vars:
  `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest`
  Every backend `Run:` step below assumes that prefix; abbreviated as `pytest ...` for readability. Run the full suite in the foreground, not backgrounded alongside another pytest run — two concurrent runs against the same Postgres cluster both die.
- Run frontend tests from `frontend/`: `cd frontend && npx vitest run <path>`.
- A test may never assume pre-existing schema or role state. Build every row the test asserts on inside the test.
- Every commit message is the subject line given in that task's commit step, a blank line, and then the AI-attribution trailer block as its last paragraph. Write it to a file and use `git commit -F <file>`, never `git commit -m` — shell quoting drops trailers:
  ```
  Note: This commit message was created by AI
  ai-generated: true
  ai-model: claude-sonnet-5
  ai-tool: claude-code
  ai-surface: claude-code-desktop
  ai-executor: local-agent
  ```
- Branch is `claude/collection-wishlist-crawler-queue-f39ab5`, already created, spec already committed on it.

## File Structure

- `backend/db.py` — `GLOBAL_SCHEMA` gains `stock_items.release_id` + a unique index (Task 1); new `upsert_stock_item_from_release()` and `delete_stock_item_for_release()` (Task 1).
- `backend/crawl_manager.py` — `_drain_one_batch`'s `is_release` branch calls the two new functions (Task 2).
- `frontend/src/api/types.ts` — `CrawlEvent` gains `type?: 'listing_changed'` and `item_key?: string` (Task 3).
- `frontend/src/App.tsx` — SSE handler gains a `listing_changed` case (Task 3).
- `backend/tests/test_stock_crud.py` — new function tests (Task 1).
- `backend/tests/test_crawl_manager.py` — `_drain_one_batch` release-branch tests (Task 2).
- `frontend/src/test/inStockTab.test.tsx` — new SSE-to-refetch test (Task 3).

---

### Task 1: Schema + `stock_items` write/delete helpers

**Files:**
- Modify: `backend/db.py` — `GLOBAL_SCHEMA` string (add after line 159, before the closing `"""` at line 160); two new functions, placed directly after `upsert_stock_item_listing` (currently ends at line 495).
- Test: `backend/tests/test_stock_crud.py`

**Interfaces:**
- Produces: `db.upsert_stock_item_from_release(conn, release_id: str, crawler_id: int, catalog_release: dict, listing: dict) -> None`. `catalog_release` is a `catalog` row (as returned by `db.get_catalog_release`) — reads `["artist"]`, `["title"]`, `["format"]`, `["cover_image_url"]`. `listing` is a crawler match dict (as returned by a plugin's `search()`) — reads `["url"]`, `.get("price")`, `.get("currency")`. Task 2 calls this.
- Produces: `db.delete_stock_item_for_release(conn, release_id: str, crawler_id: int) -> None`. Task 2 calls this.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_stock_crud.py`:

```python
def _release_crawler_and_catalog_row(conn, site_name="Amazon", discogs_id="r1", cover_image_url="https://img/r1.jpg"):
    db.register_crawler(conn, site_name, "/x.py", crawler_type="release")
    conn.commit()
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = %s", [site_name]).fetchone()["id"]
    db.upsert_catalog_release(conn, {
        "discogs_id": discogs_id, "artist": "Aphex Twin", "title": "Selected Ambient Works",
        "year": None, "label": None, "format": "LP", "discogs_price": None,
        "barcode": None, "cover_image_url": cover_image_url, "discogs_url": None,
    })
    conn.commit()
    catalog_release = conn.execute("SELECT * FROM catalog WHERE discogs_id = %s", [discogs_id]).fetchone()
    return crawler_id, catalog_release


def test_upsert_stock_item_from_release_creates_a_stock_items_row(admin_conn):
    crawler_id, catalog_release = _release_crawler_and_catalog_row(admin_conn)

    db.upsert_stock_item_from_release(
        admin_conn, "r1", crawler_id, catalog_release,
        {"url": "https://amazon/x", "price": 24.99, "currency": "USD"},
    )
    admin_conn.commit()

    row = admin_conn.execute(
        "SELECT * FROM stock_items WHERE crawler_id = %s AND release_id = 'r1'", [crawler_id]
    ).fetchone()
    assert row["artist"] == "Aphex Twin"
    assert row["title"] == "Selected Ambient Works"
    assert row["format"] == "LP"
    assert row["price"] == 24.99
    assert row["currency"] == "USD"
    assert row["url"] == "https://amazon/x"
    assert row["cover_image_url"] == "https://img/r1.jpg"
    assert row["item_key"] == db.compute_item_key("Aphex Twin", "Selected Ambient Works", "https://amazon/x")


def test_upsert_stock_item_from_release_updates_in_place_on_rerun(admin_conn):
    crawler_id, catalog_release = _release_crawler_and_catalog_row(admin_conn)

    db.upsert_stock_item_from_release(
        admin_conn, "r1", crawler_id, catalog_release,
        {"url": "https://amazon/x", "price": 24.99, "currency": "USD"},
    )
    admin_conn.commit()
    db.upsert_stock_item_from_release(
        admin_conn, "r1", crawler_id, catalog_release,
        {"url": "https://amazon/x-new", "price": 19.99, "currency": "USD"},
    )
    admin_conn.commit()

    rows = admin_conn.execute(
        "SELECT * FROM stock_items WHERE crawler_id = %s AND release_id = 'r1'", [crawler_id]
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["price"] == 19.99
    assert rows[0]["url"] == "https://amazon/x-new"


def test_upsert_stock_item_from_release_upserts_stock_item_identities(admin_conn):
    crawler_id, catalog_release = _release_crawler_and_catalog_row(admin_conn)

    db.upsert_stock_item_from_release(
        admin_conn, "r1", crawler_id, catalog_release,
        {"url": "https://amazon/x", "price": 24.99, "currency": "USD"},
    )
    admin_conn.commit()

    item_key = db.compute_item_key("Aphex Twin", "Selected Ambient Works", "https://amazon/x")
    row = admin_conn.execute(
        "SELECT artist, title, format FROM stock_item_identities WHERE item_key = %s", [item_key]
    ).fetchone()
    assert row["artist"] == "Aphex Twin"
    assert row["format"] == "LP"


def test_upsert_stock_item_from_release_allows_two_crawlers_for_the_same_release(admin_conn):
    amazon_id, catalog_release = _release_crawler_and_catalog_row(admin_conn, site_name="Amazon")
    db.register_crawler(admin_conn, "eBay", "/y.py", crawler_type="release")
    admin_conn.commit()
    ebay_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'eBay'").fetchone()["id"]

    db.upsert_stock_item_from_release(
        admin_conn, "r1", amazon_id, catalog_release,
        {"url": "https://amazon/x", "price": 24.99, "currency": "USD"},
    )
    db.upsert_stock_item_from_release(
        admin_conn, "r1", ebay_id, catalog_release,
        {"url": "https://ebay/x", "price": 21.99, "currency": "USD"},
    )
    admin_conn.commit()

    rows = admin_conn.execute(
        "SELECT crawler_id FROM stock_items WHERE release_id = 'r1' ORDER BY crawler_id"
    ).fetchall()
    assert sorted(r["crawler_id"] for r in rows) == sorted([amazon_id, ebay_id])


def test_delete_stock_item_for_release_removes_only_that_crawlers_row(admin_conn):
    amazon_id, catalog_release = _release_crawler_and_catalog_row(admin_conn, site_name="Amazon")
    db.register_crawler(admin_conn, "eBay", "/y.py", crawler_type="release")
    admin_conn.commit()
    ebay_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'eBay'").fetchone()["id"]
    db.upsert_stock_item_from_release(
        admin_conn, "r1", amazon_id, catalog_release,
        {"url": "https://amazon/x", "price": 24.99, "currency": "USD"},
    )
    db.upsert_stock_item_from_release(
        admin_conn, "r1", ebay_id, catalog_release,
        {"url": "https://ebay/x", "price": 21.99, "currency": "USD"},
    )
    admin_conn.commit()

    db.delete_stock_item_for_release(admin_conn, "r1", amazon_id)
    admin_conn.commit()

    rows = admin_conn.execute("SELECT crawler_id FROM stock_items WHERE release_id = 'r1'").fetchall()
    assert [r["crawler_id"] for r in rows] == [ebay_id]


def test_delete_stock_item_for_release_leaves_the_identity_row(admin_conn):
    crawler_id, catalog_release = _release_crawler_and_catalog_row(admin_conn)
    db.upsert_stock_item_from_release(
        admin_conn, "r1", crawler_id, catalog_release,
        {"url": "https://amazon/x", "price": 24.99, "currency": "USD"},
    )
    admin_conn.commit()
    item_key = db.compute_item_key("Aphex Twin", "Selected Ambient Works", "https://amazon/x")

    db.delete_stock_item_for_release(admin_conn, "r1", crawler_id)
    admin_conn.commit()

    assert admin_conn.execute("SELECT * FROM stock_items WHERE release_id = 'r1'").fetchall() == []
    row = admin_conn.execute(
        "SELECT artist FROM stock_item_identities WHERE item_key = %s", [item_key]
    ).fetchone()
    assert row is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_stock_crud.py -k "upsert_stock_item_from_release or delete_stock_item_for_release" -v`
Expected: every new test FAILs with `AttributeError: module 'db' has no attribute 'upsert_stock_item_from_release'` (or `delete_stock_item_for_release`).

- [ ] **Step 3: Add the schema column and index**

In `backend/db.py`, insert immediately after line 159 (`CREATE INDEX IF NOT EXISTS stock_items_item_key_idx ...`) and before the closing `"""` of `TENANT_SCHEMA`:

```sql

ALTER TABLE stock_items ADD COLUMN IF NOT EXISTS release_id TEXT REFERENCES catalog(discogs_id);
CREATE UNIQUE INDEX IF NOT EXISTS stock_items_crawler_release_idx ON stock_items (crawler_id, release_id);
```

- [ ] **Step 4: Implement the two functions**

In `backend/db.py`, directly after `upsert_stock_item_listing` (ends at line 495):

```python
def upsert_stock_item_from_release(conn, release_id: str, crawler_id: int, catalog_release: dict, listing: dict):
    artist = normalize_artist_casing(catalog_release["artist"])
    title = normalize_title_casing(catalog_release["title"])
    item_key = compute_item_key(artist, title, listing["url"])
    conn.execute(
        """
        INSERT INTO stock_item_identities (item_key, artist, title, format, last_seen)
        VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (item_key) DO UPDATE SET
            artist = EXCLUDED.artist, title = EXCLUDED.title, format = EXCLUDED.format,
            last_seen = CURRENT_TIMESTAMP
        """,
        [item_key, artist, title, catalog_release["format"]],
    )
    conn.execute(
        """
        INSERT INTO stock_items
            (crawler_id, release_id, artist, title, format, price, currency, url, cover_image_url, item_key, last_seen)
        VALUES (%(crawler_id)s, %(release_id)s, %(artist)s, %(title)s, %(format)s, %(price)s, %(currency)s,
                %(url)s, %(cover_image_url)s, %(item_key)s, CURRENT_TIMESTAMP)
        ON CONFLICT (crawler_id, release_id) DO UPDATE SET
            artist = EXCLUDED.artist, title = EXCLUDED.title, format = EXCLUDED.format,
            price = EXCLUDED.price, currency = EXCLUDED.currency, url = EXCLUDED.url,
            cover_image_url = EXCLUDED.cover_image_url, item_key = EXCLUDED.item_key, last_seen = CURRENT_TIMESTAMP
        """,
        {
            "crawler_id": crawler_id, "release_id": release_id, "artist": artist, "title": title,
            "format": catalog_release["format"], "price": listing.get("price"), "currency": listing.get("currency"),
            "url": listing["url"], "cover_image_url": catalog_release["cover_image_url"], "item_key": item_key,
        },
    )


def delete_stock_item_for_release(conn, release_id: str, crawler_id: int):
    conn.execute(
        "DELETE FROM stock_items WHERE crawler_id = %s AND release_id = %s",
        [crawler_id, release_id],
    )
```

`ON CONFLICT (crawler_id, release_id)` targets the unique index added in Step 3 — Postgres allows `ON CONFLICT` to name a unique index's columns directly without a matching named constraint.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_stock_crud.py -v`
Expected: PASS — the six new tests plus every pre-existing test in the file (schema changes are additive; nothing else in this file touches `release_id`).

- [ ] **Step 6: Commit**

```bash
cd backend
git add db.py tests/test_stock_crud.py
cat > /tmp/commit-msg-1.txt << 'EOF'
feat: add stock_items.release_id and release-crawl upsert/delete helpers

New column plus upsert_stock_item_from_release/delete_stock_item_for_release,
laying the groundwork for _drain_one_batch's release branch (next task) to
write real Store/Track rows instead of only listings rows nothing displays.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-desktop
ai-executor: local-agent
EOF
git commit -F /tmp/commit-msg-1.txt
```

---

### Task 2: Wire the helpers into `_drain_one_batch`

**Files:**
- Modify: `backend/crawl_manager.py` — import line at 245; matches-writing block at 304-318.
- Test: `backend/tests/test_crawl_manager.py`

**Interfaces:**
- Consumes: `db.upsert_stock_item_from_release(conn, release_id, crawler_id, catalog_release, listing)` and `db.delete_stock_item_for_release(conn, release_id, crawler_id)` from Task 1.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_crawl_manager.py` (near `test_worker_claims_and_completes_one_queue_row`, which this pattern extends):

```python
async def test_worker_release_match_also_creates_a_stock_items_row(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": "LP", "discogs_price": None, "barcode": None,
            "cover_image_url": "https://img/r1.jpg", "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1", crawler_id)
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    fake_plugin = AsyncMock()
    fake_plugin.search = AsyncMock(return_value=[{"url": "https://x", "price": 9.99, "shipping": None, "currency": "USD", "condition": None}])
    fake_plugin._db_id = crawler_id
    fake_plugin._db_site_name = "Amazon"

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
        await manager._drain_one_batch("worker-test", {crawler_id: fake_plugin}, pages={})

    with db.get_admin_pool().connection() as conn:
        row = conn.execute(
            "SELECT artist, title, format, price, url, cover_image_url FROM stock_items WHERE crawler_id = %s AND release_id = 'r1'",
            [crawler_id],
        ).fetchone()
    assert row["artist"] == "A"
    assert row["title"] == "T"
    assert row["format"] == "LP"
    assert row["price"] == 9.99
    assert row["url"] == "https://x"
    assert row["cover_image_url"] == "https://img/r1.jpg"


async def test_worker_release_not_found_deletes_an_existing_stock_items_row(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        catalog_release = conn.execute("SELECT * FROM catalog WHERE discogs_id = 'r1'").fetchone()
        db.upsert_stock_item_from_release(
            conn, "r1", crawler_id, catalog_release, {"url": "https://x", "price": 9.99, "currency": "USD"},
        )
        db.enqueue_crawl_queue(conn, "r1", crawler_id)
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    fake_plugin = AsyncMock()
    fake_plugin.search = AsyncMock(return_value=[])
    fake_plugin._db_id = crawler_id
    fake_plugin._db_site_name = "Amazon"

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
        await manager._drain_one_batch("worker-test", {crawler_id: fake_plugin}, pages={})

    with db.get_admin_pool().connection() as conn:
        rows = conn.execute("SELECT * FROM stock_items WHERE release_id = 'r1'").fetchall()
    assert rows == []


async def test_worker_release_crawl_exception_leaves_an_existing_stock_items_row_untouched(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        catalog_release = conn.execute("SELECT * FROM catalog WHERE discogs_id = 'r1'").fetchone()
        db.upsert_stock_item_from_release(
            conn, "r1", crawler_id, catalog_release, {"url": "https://x", "price": 9.99, "currency": "USD"},
        )
        db.enqueue_crawl_queue(conn, "r1", crawler_id)
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    fake_plugin = AsyncMock()
    fake_plugin.search = AsyncMock(side_effect=RuntimeError("boom"))
    fake_plugin._db_id = crawler_id
    fake_plugin._db_site_name = "Amazon"

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
        await manager._drain_one_batch("worker-test", {crawler_id: fake_plugin}, pages={})

    with db.get_admin_pool().connection() as conn:
        row = conn.execute("SELECT price FROM stock_items WHERE crawler_id = %s AND release_id = 'r1'", [crawler_id]).fetchone()
    assert row["price"] == 9.99
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_crawl_manager.py -k "release_match_also_creates or release_not_found_deletes or release_crawl_exception" -v`
Expected: `test_worker_release_match_also_creates_a_stock_items_row` FAILs (`row` is `None`, `NoneType` subscript error) — no `stock_items` row is created yet. `test_worker_release_not_found_deletes_an_existing_stock_items_row` FAILs (`rows` is not `[]`, the pre-existing row is still there) — nothing deletes it yet. `test_worker_release_crawl_exception_leaves_an_existing_stock_items_row_untouched` PASSes already (nothing touches `stock_items` on any path yet, which happens to already satisfy "untouched") — note this in the run output but proceed; it will still exercise the real code path once Step 3 lands.

- [ ] **Step 3: Wire the helpers into `_drain_one_batch`**

In `backend/crawl_manager.py`, update the import at line 245:

```python
        from db import get_app_pool, claim_crawl_queue_batch, mark_crawl_queue_done, upsert_listing, get_catalog_release, get_stock_item_identity, upsert_stock_item_listing, upsert_stock_item_from_release, delete_stock_item_for_release
```

And replace the matches-writing block at lines 304-318:

```python
            with get_app_pool().connection() as conn:
                if matches:
                    best = matches[0]
                    if is_release:
                        upsert_listing(
                            conn, row["discogs_id"], row["crawler_id"], best["url"],
                            best.get("price"), best.get("shipping"), best.get("currency"), best.get("condition"),
                        )
                        upsert_stock_item_from_release(conn, row["discogs_id"], row["crawler_id"], target, best)
                    else:
                        upsert_stock_item_listing(
                            conn, row["item_key"], row["crawler_id"], best["url"],
                            best.get("price"), best.get("shipping"), best.get("currency"), best.get("condition"),
                        )
                elif is_release:
                    delete_stock_item_for_release(conn, row["discogs_id"], row["crawler_id"])
                mark_crawl_queue_done(conn, row["id"])
                conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_crawl_manager.py -v`
Expected: PASS — the three new tests plus every pre-existing test in the file, including `test_worker_claims_and_completes_one_queue_row` and `test_worker_claims_and_completes_one_stock_item_queue_row` (the `listings` writes are unchanged).

- [ ] **Step 5: Commit**

```bash
cd backend
git add crawl_manager.py tests/test_crawl_manager.py
cat > /tmp/commit-msg-2.txt << 'EOF'
feat: feed stock_items from release-crawler matches

_drain_one_batch's release branch now upserts a stock_items row on a match
and deletes it on a clean not-found, alongside the existing listings write.
Store and Track tabs read only stock_items, so this is the entire fix --
no query-side changes needed.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-desktop
ai-executor: local-agent
EOF
git commit -F /tmp/commit-msg-2.txt
```

---

### Task 3: Live-repaint Store/Track on `listing_changed`

**Files:**
- Modify: `frontend/src/api/types.ts` — `CrawlEvent` interface.
- Modify: `frontend/src/App.tsx` — SSE `handleEvent`.
- Test: `frontend/src/test/inStockTab.test.tsx`

**Interfaces:**
- Consumes: nothing new from Tasks 1–2 (this task only touches how the frontend reacts to an SSE event shape the backend already emits).
- Produces: no new exported function — `stockSyncGeneration` (existing state) ticks on one more event type, which `StockBrowser`'s existing `useEffect(() => { load() }, [load, syncGeneration])` already reacts to.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/test/inStockTab.test.tsx` (inside the `describe('In Stock tab', ...)` block, alongside the other SSE tests):

```tsx
  it('refetches stock items on a listing_changed SSE event', async () => {
    render(<App />)
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    await waitFor(() => expect(getStock).toHaveBeenCalled())
    const callsBefore = getStock.mock.calls.length

    getLastCrawlSource().emit({ id: 1, type: 'listing_changed', status: 'found', discogs_id: 'r1', crawler_id: 9 })

    await waitFor(() => expect(getStock.mock.calls.length).toBeGreaterThan(callsBefore))
  })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/test/inStockTab.test.tsx -t "refetches stock items on a listing_changed"`
Expected: FAIL — `getStock.mock.calls.length` never increases; the event is silently dropped by `handleEvent`.

- [ ] **Step 3: Extend `CrawlEvent` and handle the event**

In `frontend/src/api/types.ts`, add to the `CrawlEvent` interface (after the `id?: number` line):

```ts
  type?: 'listing_changed'
```

And add near the other optional fields (after `item_key` doesn't exist yet — add it after `discogs_id?: string`):

```ts
  item_key?: string
```

In `frontend/src/App.tsx`, inside `handleEvent`, add this case after the `stock_judgment_error` block (before the legacy `if (event.status === 'started') { ... }` chain):

```ts
      if (event.type === 'listing_changed') {
        setStockSyncGeneration(g => g + 1)
        return
      }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/test/inStockTab.test.tsx`
Expected: PASS — the new test plus every other test in the file, including the `'found'`/`'not_found'`-status crawl-status-bar tests in `crawlStatusBar.test.tsx` (unaffected: those events carry no `type` field, so they never match the new branch).

- [ ] **Step 5: Run the full frontend test suite**

Run: `npx vitest run`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
cd frontend
git add src/api/types.ts src/App.tsx src/test/inStockTab.test.tsx
cat > /tmp/commit-msg-3.txt << 'EOF'
feat: live-repaint Store/Track on listing_changed SSE events

The worker pool already broadcasts listing_changed on every release-crawl
and stock-item-comparison result, but the frontend dropped it -- the old
per-release UI that used to consume it was removed by the tab rename.
Wires it to the same stockSyncGeneration counter stock_sync_progress
already bumps, so Store/Track repaint live as release crawlers add items,
consistent with catalog-crawl live repaint (#118).

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-desktop
ai-executor: local-agent
EOF
git commit -F /tmp/commit-msg-3.txt
```

---

## Final Verification

- [ ] Run the full backend suite: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest -q`. Expected: all pass.
- [ ] Run the full frontend suite: `cd frontend && npx vitest run`. Expected: all pass.
- [ ] Pre-PR spec-drift check per `CLAUDE.md`: `grep -rl "release_id.*listings\|stock_items" docs/superpowers/specs/ docs/specifications/shaping/` and confirm no other spec's prose still claims release-crawler `listings` rows are never displayed.
