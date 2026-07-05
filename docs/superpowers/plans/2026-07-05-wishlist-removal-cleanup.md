# Wishlist Removal Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a release drops off the Discogs wantlist and was never in the collection, delete its row and listings from the local database instead of just hiding it.

**Architecture:** Add `delete_orphaned_releases(conn)` to `backend/db.py`, which deletes any release (+ listings) with both `in_collection` and `in_wishlist` false. Call it from `backend/crawl_manager.py`'s `_sync_collection`, immediately after the existing `clear_wishlist_flags_not_in` call.

**Tech Stack:** Python 3.9+, sqlite3, pytest (existing `conn` / `conn_with_crawler` fixtures in `backend/tests/conftest.py` and `backend/tests/test_db.py`).

**Spec:** `docs/superpowers/specs/2026-07-05-wishlist-removal-cleanup-design.md`

---

### Task 1: `delete_orphaned_releases` in `db.py`

**Files:**
- Modify: `backend/db.py:131-140` (add new function directly after `clear_wishlist_flags_not_in`)
- Test: `backend/tests/test_db.py` (add tests in the "collection/wishlist flags" section, after `test_wishlist_only_release_not_in_collection_scope` at line 268)

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_db.py`, right after `test_wishlist_only_release_not_in_collection_scope` (line 268) and before `test_clear_wishlist_flags_not_in_removes_stale`:

```python
def test_delete_orphaned_releases_deletes_release_and_listings(conn_with_crawler):
    conn, crawler_id = conn_with_crawler
    upsert_release(conn, _release("r1"))
    upsert_listing(conn, "r1", crawler_id, {"url": "https://a.com", "price": 9.99})
    mark_not_in_collection(conn, "r1")  # in_wishlist already defaults to 0

    deleted = delete_orphaned_releases(conn)

    assert deleted == ["r1"]
    assert conn.execute("SELECT 1 FROM releases WHERE discogs_id = 'r1'").fetchone() is None
    assert conn.execute("SELECT 1 FROM listings WHERE release_id = 'r1'").fetchone() is None


def test_delete_orphaned_releases_preserves_wishlist_only(conn):
    upsert_release(conn, _release("r1"))
    mark_not_in_collection(conn, "r1")
    mark_in_wishlist(conn, "r1")

    deleted = delete_orphaned_releases(conn)

    assert deleted == []
    assert conn.execute("SELECT 1 FROM releases WHERE discogs_id = 'r1'").fetchone() is not None


def test_delete_orphaned_releases_preserves_collection_only(conn):
    upsert_release(conn, _release("r1"))  # in_collection defaults to 1

    deleted = delete_orphaned_releases(conn)

    assert deleted == []
    assert conn.execute("SELECT 1 FROM releases WHERE discogs_id = 'r1'").fetchone() is not None
```

Add `delete_orphaned_releases` to the `from db import (...)` block at the top of `backend/tests/test_db.py:3-10`, alongside `clear_wishlist_flags_not_in`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_db.py -k delete_orphaned_releases -v`
Expected: FAIL — `ImportError: cannot import name 'delete_orphaned_releases' from 'db'`

- [ ] **Step 3: Implement `delete_orphaned_releases`**

Add to `backend/db.py`, directly after `clear_wishlist_flags_not_in` (after line 140, before the blank lines preceding `def get_releases` at line 143):

```python
def delete_orphaned_releases(conn: sqlite3.Connection) -> list[str]:
    """Delete releases with neither in_collection nor in_wishlist set, along
    with their listings. Returns the deleted discogs_ids."""
    rows = conn.execute(
        "SELECT discogs_id FROM releases WHERE in_collection = 0 AND in_wishlist = 0"
    ).fetchall()
    orphaned = [row[0] for row in rows]
    for discogs_id in orphaned:
        conn.execute("DELETE FROM listings WHERE release_id = ?", [discogs_id])
        conn.execute("DELETE FROM releases WHERE discogs_id = ?", [discogs_id])
    if orphaned:
        conn.commit()
    return orphaned
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_db.py -k delete_orphaned_releases -v`
Expected: 3 passed

- [ ] **Step 5: Run the full db test file to check for regressions**

Run: `cd backend && pytest tests/test_db.py -v`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add backend/db.py backend/tests/test_db.py
git commit -m "db: add delete_orphaned_releases for wishlist-only removals"
```

---

### Task 2: Wire deletion into the wishlist sync

**Files:**
- Modify: `backend/crawl_manager.py:129` (import), `backend/crawl_manager.py:221-222` (call site + log line)

- [ ] **Step 1: Update the import**

In `backend/crawl_manager.py:129`, change:

```python
        from db import upsert_release, mark_in_collection, mark_in_wishlist, mark_not_in_collection, clear_wishlist_flags_not_in
```

to:

```python
        from db import upsert_release, mark_in_collection, mark_in_wishlist, mark_not_in_collection, clear_wishlist_flags_not_in, delete_orphaned_releases
```

- [ ] **Step 2: Call it after the existing flag-clearing, and extend the log line**

In `backend/crawl_manager.py:221-222`, change:

```python
                cleared = clear_wishlist_flags_not_in(conn, wishlist_seen)
                log.info("Wishlist sync complete: %d items, %d stale entries cleared", wishlist_count, cleared)
```

to:

```python
                cleared = clear_wishlist_flags_not_in(conn, wishlist_seen)
                deleted = delete_orphaned_releases(conn)
                log.info(
                    "Wishlist sync complete: %d items, %d stale entries cleared, %d releases deleted",
                    wishlist_count, cleared, len(deleted),
                )
```

- [ ] **Step 3: Run the existing crawl_manager test file to check for regressions**

Run: `cd backend && pytest tests/test_crawl_manager.py -v`
Expected: all pass (this file fakes out `_sync_collection` entirely, so it won't exercise the new call — that's consistent with how this file already treats `_sync_collection`'s body as untested plumbing, matching the project's existing convention that Playwright/live-sync internals are verified manually rather than unit tested)

- [ ] **Step 4: Manual verification**

Run a real "Refresh Now" sync against a Discogs account (or a mocked token/response if no live account is convenient) where an item has been removed from the wantlist since the last sync. Confirm in the app log (`~/.discogs-browser/app.log` or console) that the log line reports a non-zero deleted count, and confirm via the Wishlist pane and `sqlite3 ~/.discogs-browser/db.sqlite "SELECT discogs_id FROM releases WHERE in_collection=0 AND in_wishlist=0"` that no orphaned rows remain.

- [ ] **Step 5: Commit**

```bash
git add backend/crawl_manager.py
git commit -m "crawl_manager: delete orphaned releases after wishlist sync"
```
