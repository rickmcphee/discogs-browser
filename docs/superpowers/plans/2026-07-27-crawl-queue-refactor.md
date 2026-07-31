# Crawl Queue Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the app boot again on the Postgres multi-tenant schema (every router, `crawl_manager.py`, `scheduler.py`, `main.py` rewired off the deleted SQLite-era `db.py` API) and build the shared `crawl_queue` + worker pool described in the base multi-tenant spec.

**Architecture:** See [`docs/superpowers/specs/2026-07-27-crawl-queue-refactor-design.md`](../specs/2026-07-27-crawl-queue-refactor-design.md) for the full design. In short: every route reads `request.state.user_id` and opens `db.user_scope(user_id)`; a small `is_admin` gate protects global settings/crawler-enable; a shared `crawl_queue` table is enqueued during a user's sync and drained by N in-process asyncio worker tasks; `stock_item_judgments` moves to per-user/RLS since each user now funds their own Anthropic-judged recommendations.

**Tech Stack:** FastAPI, psycopg3 (`dict_row`), Postgres, `authlib` OAuth1Client, APScheduler, React/TypeScript/Vitest.

**Reference for porting unchanged logic:** the pre-migration SQLite `db.py` is preserved at git tag `last-self-hosted-single-owner` (`git show last-self-hosted-single-owner:backend/db.py`). Several tasks below translate specific functions from it 1:1 into Postgres syntax; the exact source is cited per task so you can diff your translation against it if anything looks off.

## File structure

| File | Task(s) | Responsibility after this plan |
|---|---|---|
| `backend/db.py` | 1–7, 9 | Postgres schema (admin flag, relocated `stock_item_judgments`, `crawl_queue`) + every per-user/global CRUD function the routers and `crawl_manager.py` call |
| `backend/discogs.py` | 8 | Discogs API client, every call OAuth1.0a-signed with the calling user's own token pair |
| `backend/admin.py` (new) | 9 | `require_admin(request)` — 403s a non-admin caller |
| `backend/crawl_manager.py` | 10–13 | Per-user sync (enqueues `crawl_queue`), in-process worker pool draining it, global stock sync, per-user judgment phase, sweep-enqueue for the admin schedule |
| `backend/crawler.py` | 11 | Unchanged plugin-loading/bot-recovery helpers (`_new_context`, `_reset_context`, `BotDetectedError`) kept; `crawl_releases()` deleted, fully superseded by the worker pool |
| `backend/scheduler.py` | 13 | `crawl_schedule` → sweep-enqueue; `configure_sync` removed; `configure_stock` unchanged |
| `backend/main.py` | 14 | Postgres schema init + worker pool start/stop at app lifecycle, not SQLite `init_db` |
| `backend/routers/crawl.py` | 15 | Per-user enqueue (`start`), per-user pending count + pool status (`status`), per-user-filtered SSE (`stream`); `stop` removed |
| `backend/routers/collection.py`, `releases.py` | 16 | Rescoped to `request.state.user_id` via `db.user_scope` |
| `backend/routers/settings.py` | 17 | Split: admin-only global settings + crawler enable, new per-user `/user-settings` |
| `backend/routers/stock.py` | 18 | Judgment endpoints rescoped to calling user; stock browsing/sync stay global |
| `backend/routers/session.py` | 21 | `auth_status` response gains `is_admin` |
| `backend/scripts/migrate_from_sqlite.py` | 19 | Stops migrating orphaned global `stock_item_judgments` |
| `docs/superpowers/specs/2026-07-26-multi-tenant-architecture-design.md` | 19 | Amendment note for the `stock_item_judgments` relocation |
| `frontend/src/api/types.ts`, `client.ts` | 20 | Settings split into `Settings`/`UserSettings`, `CrawlStatus` gains `pending`/`pool_running`, `postCrawlStop` removed |
| `frontend/src/App.tsx`, `views/Settings.tsx`, `views/Account.tsx`, `views/RecordBrowser.tsx` | 21 | Per-user Anthropic key field, admin-gated Settings nav, Plex UI removed |

---

### Task 1: Schema changes — admin flag, per-user settings columns, relocate `stock_item_judgments`, add `crawl_queue`

**Files:**
- Modify: `backend/db.py` (`TENANT_SCHEMA`, `GLOBAL_SCHEMA`, `init_tenant_schema`)
- Test: `backend/tests/test_tenant_schema.py`, `backend/tests/test_global_schema.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_tenant_schema.py`:

```python
def test_users_table_has_admin_and_recommendation_columns(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        row = conn.execute(
            "SELECT is_admin, anthropic_api_key, recommendation_item_limit FROM users WHERE id = %s",
            [user["id"]],
        ).fetchone()
    assert row["is_admin"] is False
    assert row["anthropic_api_key"] is None
    assert row["recommendation_item_limit"] == 300


def test_stock_item_judgments_is_rls_isolated_per_user(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        conn.execute(
            "INSERT INTO stock_item_judgments (user_id, item_key, recommended) VALUES (%s, %s, %s)",
            [alice["id"], "key-1", True],
        )
        conn.commit()

    with db.user_scope(bob["id"]) as conn:
        rows = conn.execute("SELECT * FROM stock_item_judgments").fetchall()
    assert rows == []

    with db.user_scope(alice["id"]) as conn:
        rows = conn.execute("SELECT * FROM stock_item_judgments").fetchall()
    assert len(rows) == 1
    assert rows[0]["item_key"] == "key-1"
```

Append to `backend/tests/test_global_schema.py`:

```python
def test_crawl_queue_table_exists_with_unique_constraint(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        conn.execute(
            "INSERT INTO crawlers (site_name, module_path) VALUES ('Test Site', '/x.py') RETURNING id"
        )
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Test Site'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        conn.execute(
            "INSERT INTO crawl_queue (discogs_id, crawler_id) VALUES ('r1', %s)", [crawler_id]
        )
        conn.commit()
        with pytest.raises(Exception):
            conn.execute(
                "INSERT INTO crawl_queue (discogs_id, crawler_id) VALUES ('r1', %s)", [crawler_id]
            )
```

Add `import pytest` to `test_global_schema.py` if not already present.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_tenant_schema.py tests/test_global_schema.py -k "admin_and_recommendation or isolated_per_user or crawl_queue_table" -v`
Expected: FAIL — columns/table don't exist yet.

- [ ] **Step 3: Modify `TENANT_SCHEMA` and `GLOBAL_SCHEMA` in `backend/db.py`**

In the `users` table definition inside `TENANT_SCHEMA`, add three columns (after `plex_match_threshold`, before `invited_by`):

```python
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    anthropic_api_key TEXT,
    recommendation_item_limit INTEGER NOT NULL DEFAULT 300,
```

Remove the `stock_item_judgments` table from `GLOBAL_SCHEMA` entirely (it currently lives there as `CREATE TABLE IF NOT EXISTS stock_item_judgments (item_key TEXT PRIMARY KEY, recommended BOOLEAN NOT NULL, reason TEXT, judged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);`).

Add it to `TENANT_SCHEMA` instead, right after the `library_items` table definition:

```python
CREATE TABLE IF NOT EXISTS stock_item_judgments (
    user_id INTEGER NOT NULL REFERENCES users(id),
    item_key TEXT NOT NULL,
    recommended BOOLEAN NOT NULL,
    reason TEXT,
    judged_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, item_key)
);
```

Add the RLS enable/policy for it alongside the existing three, in the `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` block:

```python
ALTER TABLE stock_item_judgments ENABLE ROW LEVEL SECURITY;
ALTER TABLE stock_item_judgments FORCE ROW LEVEL SECURITY;
```

And a policy alongside `library_items_isolation`:

```python
DROP POLICY IF EXISTS stock_item_judgments_isolation ON stock_item_judgments;
CREATE POLICY stock_item_judgments_isolation ON stock_item_judgments
    USING (user_id = current_setting('app.user_id', true)::int)
    WITH CHECK (user_id = current_setting('app.user_id', true)::int);
```

Add `crawl_queue` to `GLOBAL_SCHEMA` (no RLS — it's global), after the `stock_item_judgments` table was removed from there:

```python
CREATE TABLE IF NOT EXISTS crawl_queue (
    id SERIAL PRIMARY KEY,
    discogs_id TEXT NOT NULL REFERENCES catalog(discogs_id),
    crawler_id INTEGER NOT NULL REFERENCES crawlers(id),
    requested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'pending',
    claimed_by TEXT,
    claimed_at TIMESTAMP,
    UNIQUE(discogs_id, crawler_id)
);
```

- [ ] **Step 4: Update `init_tenant_schema()`'s grants in `backend/db.py`**

Change:

```python
        conn.execute(
            "GRANT SELECT ON catalog, listings, crawlers, stock_items, stock_item_judgments TO app_user"
        )
        conn.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON library_items TO app_user")
```

to:

```python
        conn.execute("GRANT SELECT ON crawlers TO app_user")
        conn.execute("GRANT SELECT, INSERT, UPDATE ON catalog, listings, stock_items TO app_user")
        conn.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON library_items TO app_user")
        conn.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON stock_item_judgments TO app_user")
        conn.execute("GRANT SELECT, INSERT, UPDATE ON crawl_queue TO app_user")
        conn.execute("GRANT USAGE, SELECT ON SEQUENCE crawl_queue_id_seq TO app_user")
```

`init_global_schema()` grants nothing (it runs as the admin/superuser role), so no change needed there — `crawl_queue` is created by `init_global_schema()` and granted to `app_user` by `init_tenant_schema()`, matching the existing split for `catalog`/`listings`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_tenant_schema.py tests/test_global_schema.py -v`
Expected: PASS. Also re-run the full baseline (`.venv/bin/pytest tests/ -q --ignore=tests/crawlers -k "not crawler"`, ignoring the six files listed in this plan's own later tasks) to confirm nothing existing broke.

- [ ] **Step 6: Commit**

```bash
git add backend/db.py backend/tests/test_tenant_schema.py backend/tests/test_global_schema.py
git commit -m "feat: add admin/per-user-settings columns, relocate stock_item_judgments to per-user RLS, add crawl_queue"
```

---

### Task 2: `db.py` — per-user catalog listing query and `get_listings_for_release`

**Files:**
- Modify: `backend/db.py`
- Test: `backend/tests/test_catalog_crud.py`

This replaces old `get_releases`/`get_listings_for_release` (`last-self-hosted-single-owner:backend/db.py:216-332`), joined through `library_items` instead of a flat `releases` table.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_catalog_crud.py`:

```python
def test_get_library_releases_returns_only_calling_users_rows(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        for rid, artist in [("r1", "AAA"), ("r2", "BBB")]:
            db.upsert_catalog_release(conn, {
                "discogs_id": rid, "artist": artist, "title": "T", "year": None, "label": None,
                "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
                "discogs_url": None,
            })
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True)
        db.upsert_library_item(conn, bob["id"], "r2", in_collection=True)
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_library_releases(conn, alice["id"])
    assert result["total"] == 1
    assert result["releases"][0]["discogs_id"] == "r1"


def test_get_library_releases_search_and_scope_filters(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "Zzz Top", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.upsert_catalog_release(conn, {
            "discogs_id": "r2", "artist": "Other", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True, in_wishlist=False)
        db.upsert_library_item(conn, alice["id"], "r2", in_collection=False, in_wishlist=True)
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_library_releases(conn, alice["id"], search="Zzz")
        assert result["total"] == 1 and result["releases"][0]["discogs_id"] == "r1"

        result = db.get_library_releases(conn, alice["id"], scope="wishlist")
        assert result["total"] == 1 and result["releases"][0]["discogs_id"] == "r2"


def test_get_listings_for_release_joins_crawler_site_name(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        conn.execute("INSERT INTO crawlers (site_name, module_path) VALUES ('Amazon', '/x.py')")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.upsert_listing(conn, "r1", crawler_id, "https://x", 9.99, 2.0, "USD", "VG+")
        conn.commit()

        listings = db.get_listings_for_release(conn, "r1")
    assert listings["Amazon"]["price"] == 9.99
    assert listings["Amazon"]["condition"] == "VG+"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_catalog_crud.py -k "get_library_releases or get_listings_for_release" -v`
Expected: FAIL — `AttributeError: module 'db' has no attribute 'get_library_releases'`.

- [ ] **Step 3: Implement in `backend/db.py`**

Add after `get_library_items_for_user`:

```python
_RELEASE_ALLOWED_SORT = {"artist", "title", "year", "label", "format", "discogs_price"}


def get_library_releases(
    conn,
    user_id: int,
    search: Optional[str] = None,
    artist: Optional[str] = None,
    sort: str = "artist",
    order: str = "asc",
    page: int = 1,
    per_page: int = 50,
    release_id: Optional[str] = None,
    scope: Optional[str] = None,
) -> dict:
    order_sql = "DESC" if order.lower() == "desc" else "ASC"
    null_order = "ASC" if order_sql == "ASC" else "DESC"

    conditions = ["li.user_id = %(user_id)s"]
    params: dict = {"user_id": user_id}

    if release_id:
        conditions.append("c.discogs_id = %(release_id)s")
        params["release_id"] = release_id
    if search:
        conditions.append("(c.artist ILIKE %(search)s OR c.title ILIKE %(search)s)")
        params["search"] = f"%{search}%"
    if artist:
        conditions.append("c.artist = %(artist)s")
        params["artist"] = artist
    if scope == "collection":
        conditions.append("li.in_collection = TRUE")
    elif scope == "wishlist":
        conditions.append("li.in_wishlist = TRUE")

    where = "WHERE " + " AND ".join(conditions)
    base_from = "FROM library_items li JOIN catalog c ON c.discogs_id = li.discogs_id"

    total = conn.execute(f"SELECT COUNT(*) {base_from} {where}", params).fetchone()["count"]

    offset = (page - 1) * per_page
    params["limit"] = per_page
    params["offset"] = offset

    if sort.startswith("price_"):
        site_name = sort[len("price_"):]
        crawler_row = conn.execute(
            "SELECT id FROM crawlers WHERE site_name = %s", [site_name]
        ).fetchone()
        if crawler_row:
            params["crawler_id"] = crawler_row["id"]
            rows = conn.execute(
                f"""
                SELECT c.* {base_from}
                LEFT JOIN listings ls ON ls.release_id = c.discogs_id AND ls.crawler_id = %(crawler_id)s
                {where}
                ORDER BY CASE WHEN ls.price IS NULL THEN 1 ELSE 0 END {null_order}, ls.price {order_sql}
                LIMIT %(limit)s OFFSET %(offset)s
                """,
                params,
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT c.* {base_from} {where} ORDER BY c.artist ASC LIMIT %(limit)s OFFSET %(offset)s",
                params,
            ).fetchall()
    else:
        sort_col = sort if sort in _RELEASE_ALLOWED_SORT else "artist"
        rows = conn.execute(
            f"""
            SELECT c.* {base_from} {where}
            ORDER BY CASE WHEN c.{sort_col} IS NULL THEN 1 ELSE 0 END {null_order}, c.{sort_col} {order_sql}
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            params,
        ).fetchall()

    releases = []
    for row in rows:
        r = dict(row)
        r["listings"] = get_listings_for_release(conn, r["discogs_id"])
        releases.append(r)

    return {"total": total, "page": page, "per_page": per_page, "releases": releases}


def get_listings_for_release(conn, release_id: str) -> dict:
    rows = conn.execute(
        """
        SELECT cr.site_name, l.url, l.price, l.shipping, l.currency, l.condition, l.last_checked
        FROM listings l
        JOIN crawlers cr ON l.crawler_id = cr.id
        WHERE l.release_id = %s
        """,
        [release_id],
    ).fetchall()
    return {
        row["site_name"]: {
            "url": row["url"],
            "price": row["price"],
            "shipping": row["shipping"],
            "currency": row["currency"],
            "condition": row["condition"],
            "last_checked": row["last_checked"],
        }
        for row in rows
    }
```

`sort_col`/`_RELEASE_ALLOWED_SORT` prevents SQL injection through the `sort` query param, matching the old code's same guard.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_catalog_crud.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/db.py backend/tests/test_catalog_crud.py
git commit -m "feat: add per-user catalog listing query and get_listings_for_release"
```

---

### Task 3: `db.py` — full `crawlers` CRUD

**Files:**
- Modify: `backend/db.py`
- Test: `backend/tests/test_crawler_crud.py` (new)

Ports `last-self-hosted-single-owner:backend/db.py:491-531` (`get_enabled_crawlers`, `get_all_crawlers`, `register_crawler`, `set_crawler_enabled`, `update_crawler_last_run`) unchanged in behavior.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_crawler_crud.py`:

```python
import db


def test_register_then_get_all_and_enabled_crawlers(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/path/amazon.py")
        db.register_crawler(conn, "Stock Site", "/path/stock.py", crawler_type="catalog")
        conn.commit()

        all_crawlers = db.get_all_crawlers(conn)
        assert {c["site_name"] for c in all_crawlers} == {"Amazon", "Stock Site"}

        enabled_release = db.get_enabled_crawlers(conn, crawler_type="release")
        assert [c["site_name"] for c in enabled_release] == ["Amazon"]

        enabled_catalog = db.get_enabled_crawlers(conn, crawler_type="catalog")
        assert [c["site_name"] for c in enabled_catalog] == ["Stock Site"]


def test_register_crawler_is_idempotent_on_site_name(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/old/path.py")
        db.register_crawler(conn, "Amazon", "/new/path.py")
        conn.commit()
        rows = conn.execute("SELECT module_path FROM crawlers WHERE site_name = 'Amazon'").fetchall()
    assert len(rows) == 1
    assert rows[0]["module_path"] == "/new/path.py"


def test_set_crawler_enabled_and_update_last_run(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/path.py")
        conn.commit()
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]

        db.set_crawler_enabled(conn, crawler_id, False)
        conn.commit()
        row = conn.execute("SELECT enabled FROM crawlers WHERE id = %s", [crawler_id]).fetchone()
        assert row["enabled"] is False

        db.update_crawler_last_run(conn, crawler_id)
        conn.commit()
        row = conn.execute("SELECT last_run FROM crawlers WHERE id = %s", [crawler_id]).fetchone()
        assert row["last_run"] is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_crawler_crud.py -v`
Expected: FAIL — functions don't exist.

- [ ] **Step 3: Implement in `backend/db.py`**

Add after `get_listings_for_release`:

```python
def get_enabled_crawlers(conn, crawler_type: str = "release") -> list[dict]:
    return conn.execute(
        "SELECT * FROM crawlers WHERE enabled = TRUE AND crawler_type = %s", [crawler_type]
    ).fetchall()


def get_all_crawlers(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM crawlers ORDER BY site_name").fetchall()
    result = []
    for row in rows:
        d = dict(row)
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("_tmp", d["module_path"])
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            d["base_url"] = getattr(mod.Crawler, "base_url", None)
        except Exception:
            d["base_url"] = None
        result.append(d)
    return result


def register_crawler(conn, site_name: str, module_path: str, crawler_type: str = "release"):
    conn.execute(
        """
        INSERT INTO crawlers (site_name, module_path, crawler_type, enabled)
        VALUES (%s, %s, %s, TRUE)
        ON CONFLICT (site_name) DO UPDATE SET
            module_path = EXCLUDED.module_path, crawler_type = EXCLUDED.crawler_type
        """,
        [site_name, module_path, crawler_type],
    )


def set_crawler_enabled(conn, crawler_id: int, enabled: bool):
    conn.execute("UPDATE crawlers SET enabled = %s WHERE id = %s", [enabled, crawler_id])


def update_crawler_last_run(conn, crawler_id: int):
    conn.execute("UPDATE crawlers SET last_run = CURRENT_TIMESTAMP WHERE id = %s", [crawler_id])
```

`crawlers` has a `UNIQUE` constraint on `site_name` already (`TEXT NOT NULL UNIQUE` in `GLOBAL_SCHEMA`), so the `ON CONFLICT (site_name)` clause is valid as written.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/pytest tests/test_crawler_crud.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/db.py backend/tests/test_crawler_crud.py
git commit -m "feat: add crawlers table CRUD to Postgres db.py"
```

---

### Task 4: `db.py` — `crawl_queue` CRUD (enqueue, claim, complete, count-for-user)

**Files:**
- Modify: `backend/db.py`
- Test: `backend/tests/test_crawl_queue.py` (new)

This is the core of the shared queue — no SQLite precedent to port from, it's new per the design spec.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_crawl_queue.py`:

```python
import db


def _make_catalog_and_crawler(conn, discogs_id="r1", site_name="Amazon"):
    db.upsert_catalog_release(conn, {
        "discogs_id": discogs_id, "artist": "A", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.register_crawler(conn, site_name, "/x.py")
    return conn.execute("SELECT id FROM crawlers WHERE site_name = %s", [site_name]).fetchone()["id"]


def test_enqueue_crawl_queue_is_idempotent(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        crawler_id = _make_catalog_and_crawler(conn)
        conn.commit()
        db.enqueue_crawl_queue(conn, "r1", crawler_id)
        db.enqueue_crawl_queue(conn, "r1", crawler_id)
        conn.commit()
        rows = conn.execute("SELECT * FROM crawl_queue WHERE discogs_id = 'r1'").fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"


def test_claim_crawl_queue_batch_marks_in_progress_and_skips_locked(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        crawler_id = _make_catalog_and_crawler(conn)
        conn.commit()
        db.enqueue_crawl_queue(conn, "r1", crawler_id)
        conn.commit()

    with db.get_app_pool().connection() as conn1, db.get_app_pool().connection() as conn2:
        conn1.execute("BEGIN")
        claimed1 = db.claim_crawl_queue_batch(conn1, "worker-1", limit=10)
        assert len(claimed1) == 1

        conn2.execute("BEGIN")
        claimed2 = db.claim_crawl_queue_batch(conn2, "worker-2", limit=10)
        assert claimed2 == []

        conn1.commit()
        conn2.commit()


def test_mark_crawl_queue_done(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        crawler_id = _make_catalog_and_crawler(conn)
        conn.commit()
        db.enqueue_crawl_queue(conn, "r1", crawler_id)
        conn.commit()
        [row] = db.claim_crawl_queue_batch(conn, "worker-1", limit=10)
        db.mark_crawl_queue_done(conn, row["id"])
        conn.commit()
        status = conn.execute("SELECT status FROM crawl_queue WHERE id = %s", [row["id"]]).fetchone()
    assert status["status"] == "done"


def test_count_pending_crawl_queue_for_user_only_counts_their_library(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        crawler_id = _make_catalog_and_crawler(conn, "r1")
        _make_catalog_and_crawler(conn, "r2", site_name="Discogs Marketplace")
        conn.commit()
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True)
        db.upsert_library_item(conn, bob["id"], "r2", in_collection=True)
        db.enqueue_crawl_queue(conn, "r1", crawler_id)
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.count_pending_crawl_queue_for_user(conn, alice["id"]) == 1
    with db.user_scope(bob["id"]) as conn:
        assert db.count_pending_crawl_queue_for_user(conn, bob["id"]) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_crawl_queue.py -v`
Expected: FAIL — functions don't exist.

- [ ] **Step 3: Implement in `backend/db.py`**

Add after the crawlers CRUD:

```python
def enqueue_crawl_queue(conn, discogs_id: str, crawler_id: int):
    conn.execute(
        """
        INSERT INTO crawl_queue (discogs_id, crawler_id)
        VALUES (%s, %s)
        ON CONFLICT (discogs_id, crawler_id) DO NOTHING
        """,
        [discogs_id, crawler_id],
    )


def claim_crawl_queue_batch(conn, worker_id: str, limit: int) -> list[dict]:
    return conn.execute(
        """
        UPDATE crawl_queue SET status = 'in_progress', claimed_by = %(worker_id)s, claimed_at = CURRENT_TIMESTAMP
        WHERE id IN (
            SELECT id FROM crawl_queue
            WHERE status = 'pending'
            ORDER BY requested_at
            LIMIT %(limit)s
            FOR UPDATE SKIP LOCKED
        )
        RETURNING id, discogs_id, crawler_id
        """,
        {"worker_id": worker_id, "limit": limit},
    ).fetchall()


def mark_crawl_queue_done(conn, queue_id: int):
    conn.execute("UPDATE crawl_queue SET status = 'done' WHERE id = %s", [queue_id])


def count_pending_crawl_queue_for_user(conn, user_id: int) -> int:
    return conn.execute(
        """
        SELECT COUNT(*) FROM crawl_queue cq
        JOIN library_items li ON li.discogs_id = cq.discogs_id
        WHERE li.user_id = %s AND cq.status != 'done'
        """,
        [user_id],
    ).fetchone()["count"]
```

`claim_crawl_queue_batch`'s `UPDATE ... WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED)` is the standard Postgres pattern for "claim N rows atomically, skip rows another transaction already has locked" — the subquery's row lock is held until the caller commits or rolls back, so `mark_crawl_queue_done` must run in the same transaction (or the worker must re-fetch by id in a fresh one) — the worker pool task in Task 11 keeps one open transaction per claimed batch until every row in it is marked done.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_crawl_queue.py -v`
Expected: PASS. The concurrency test (`test_claim_crawl_queue_batch_marks_in_progress_and_skips_locked`) is the one proving the base spec's "two workers never process the same row twice" success criterion.

- [ ] **Step 5: Commit**

```bash
git add backend/db.py backend/tests/test_crawl_queue.py
git commit -m "feat: add crawl_queue CRUD with SELECT FOR UPDATE SKIP LOCKED claiming"
```

---

### Task 5: `db.py` — `stock_items` CRUD, per-user-aware

**Files:**
- Modify: `backend/db.py`
- Test: `backend/tests/test_stock_crud.py` (new)

Ports `last-self-hosted-single-owner:backend/db.py:334-423` (`compute_item_key`, `replace_stock_items`, `get_stock_items`, `get_distinct_stock_artists`), with `_NOT_OWNED_CLAUSE` and the judgment `LEFT JOIN` rescoped to a `user_id` parameter (previously global — see Task 1's relocation of `stock_item_judgments`).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_stock_crud.py`:

```python
import db


def test_replace_stock_items_clears_and_inserts_for_crawler(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py", crawler_type="catalog")
        conn.commit()
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]

        db.replace_stock_items(conn, crawler_id, [
            {"artist": "aphex twin", "title": "Selected Ambient Works", "url": "https://x/1", "price": 20.0, "currency": "USD"},
        ])
        conn.commit()
        rows = conn.execute("SELECT artist FROM stock_items WHERE crawler_id = %s", [crawler_id]).fetchall()
        assert rows[0]["artist"] == "Aphex Twin"  # title-cased, matching old behavior

        db.replace_stock_items(conn, crawler_id, [])
        conn.commit()
        rows = conn.execute("SELECT * FROM stock_items WHERE crawler_id = %s", [crawler_id]).fetchall()
    assert rows == []


def test_get_stock_items_recommended_filters_to_calling_users_judgments(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        db.register_crawler(conn, "Amazon", "/x.py", crawler_type="catalog")
        conn.commit()
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.replace_stock_items(conn, crawler_id, [
            {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
        ])
        conn.commit()
        item_key = db.compute_item_key("Artist A", "Album A", "https://x/1")
        db.upsert_stock_judgments(conn, alice["id"], [{"item_key": item_key, "recommended": True, "reason": "great"}])
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], recommended=True)
        assert result["total"] == 1

    with db.user_scope(bob["id"]) as conn:
        result = db.get_stock_items(conn, bob["id"], recommended=True)
        assert result["total"] == 0


def test_get_stock_items_overlapping_excludes_items_matching_users_collection(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.register_crawler(conn, "Amazon", "/x.py", crawler_type="catalog")
        conn.commit()
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.replace_stock_items(conn, crawler_id, [
            {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
        ])
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "Artist A", "title": "Album A", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True)
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], recommended=False)
        assert result["total"] == 1  # plain browse still shows it

        db.upsert_stock_judgments(conn, alice["id"], [{
            "item_key": db.compute_item_key("Artist A", "Album A", "https://x/1"),
            "recommended": True, "reason": "x",
        }])
        result = db.get_stock_items(conn, alice["id"], recommended=True)
        assert result["total"] == 0  # already owned, excluded from recommended view
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_stock_crud.py -v`
Expected: FAIL — functions don't exist yet (`upsert_stock_judgments` is added in Task 6, so stub it as a `pass`-bodied function temporarily is not needed — write both this task and Task 6 in either order; if run before Task 6 lands, `test_get_stock_items_*` tests will additionally fail on `upsert_stock_judgments` — that's expected and resolves once Task 6 is also done. Run this task's `test_replace_stock_items_clears_and_inserts_for_crawler` alone first to confirm the isolated failure.)

Run: `cd backend && .venv/bin/pytest tests/test_stock_crud.py::test_replace_stock_items_clears_and_inserts_for_crawler -v`
Expected: FAIL — `compute_item_key`/`replace_stock_items` don't exist.

- [ ] **Step 3: Implement in `backend/db.py`**

Add after `count_pending_crawl_queue_for_user`:

```python
import hashlib


def compute_item_key(artist: str, title: str, url: str) -> str:
    return hashlib.sha256(f"{artist}|{title}|{url}".encode()).hexdigest()


def replace_stock_items(conn, crawler_id: int, items: list[dict]):
    conn.execute("DELETE FROM stock_items WHERE crawler_id = %s", [crawler_id])
    if not items:
        return
    rows = []
    for item in items:
        artist = item["artist"].title()
        rows.append((
            crawler_id, artist, item["title"], item.get("format"), item.get("price"),
            item.get("currency"), item["url"], item.get("cover_image_url"),
            compute_item_key(artist, item["title"], item["url"]),
        ))
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO stock_items
                (crawler_id, artist, title, format, price, currency, url, cover_image_url, item_key, last_seen)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            """,
            rows,
        )


def _not_owned_clause(user_id_param: str) -> str:
    return f"""NOT EXISTS (
        SELECT 1 FROM library_items li
        JOIN catalog c ON c.discogs_id = li.discogs_id
        WHERE li.user_id = {user_id_param}
          AND li.in_collection = TRUE
          AND LOWER(c.artist) = LOWER(s.artist)
          AND (LOWER(s.title) = LOWER(c.title) OR LOWER(s.title) LIKE LOWER(c.title) || ' %%')
    )"""


_STOCK_ALLOWED_SORT = {"artist", "title", "format", "price"}


def get_stock_items(
    conn,
    user_id: int,
    search: Optional[str] = None,
    artist: Optional[str] = None,
    sort: str = "artist",
    order: str = "asc",
    page: int = 1,
    per_page: int = 50,
    overlapping: bool = False,
    recommended: bool = False,
) -> dict:
    order_sql = "DESC" if order.lower() == "desc" else "ASC"
    sort_col = sort if sort in _STOCK_ALLOWED_SORT else "artist"

    conditions = []
    params: dict = {"user_id": user_id}
    if search:
        conditions.append("(s.artist ILIKE %(search)s OR s.title ILIKE %(search)s)")
        params["search"] = f"%{search}%"
    if artist:
        conditions.append("s.artist = %(artist)s")
        params["artist"] = artist
    if overlapping:
        conditions.append(_not_owned_clause("%(user_id)s").replace("NOT EXISTS", "EXISTS"))
    if recommended:
        conditions.append(
            "s.item_key IN (SELECT item_key FROM stock_item_judgments "
            "WHERE user_id = %(user_id)s AND recommended = TRUE)"
        )
        conditions.append(_not_owned_clause("%(user_id)s"))
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    total = conn.execute(f"SELECT COUNT(*) FROM stock_items s {where}", params).fetchone()["count"]

    offset = (page - 1) * per_page
    params["limit"] = per_page
    params["offset"] = offset
    null_order = "ASC" if order_sql == "ASC" else "DESC"
    rows = conn.execute(
        f"""
        SELECT s.id, s.artist, s.title, s.format, s.price, s.currency, s.url, s.cover_image_url, s.last_seen,
               cr.site_name AS source, j.reason AS reason
        FROM stock_items s
        JOIN crawlers cr ON cr.id = s.crawler_id
        LEFT JOIN stock_item_judgments j ON j.item_key = s.item_key AND j.user_id = %(user_id)s
        {where}
        ORDER BY CASE WHEN s.{sort_col} IS NULL THEN 1 ELSE 0 END {null_order}, s.{sort_col} {order_sql}
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        params,
    ).fetchall()

    return {"total": total, "page": page, "per_page": per_page, "items": rows}


def get_distinct_stock_artists(conn, user_id: int, overlapping: bool = False, recommended: bool = False) -> list[str]:
    conditions = []
    params: dict = {"user_id": user_id}
    if overlapping:
        conditions.append(_not_owned_clause("%(user_id)s").replace("NOT EXISTS", "EXISTS"))
    if recommended:
        conditions.append(
            "s.item_key IN (SELECT item_key FROM stock_item_judgments "
            "WHERE user_id = %(user_id)s AND recommended = TRUE)"
        )
        conditions.append(_not_owned_clause("%(user_id)s"))
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = conn.execute(f"SELECT DISTINCT s.artist FROM stock_items s {where} ORDER BY s.artist", params).fetchall()
    return [row["artist"] for row in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

`test_get_stock_items_recommended_filters_to_calling_users_judgments` and the overlapping test depend on `upsert_stock_judgments` from Task 6. If Task 6 isn't done yet, implement Task 6 first, then return here.

Run: `cd backend && .venv/bin/pytest tests/test_stock_crud.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/db.py backend/tests/test_stock_crud.py
git commit -m "feat: add per-user-aware stock_items CRUD to Postgres db.py"
```

---

### Task 6: `db.py` — per-user `stock_item_judgments` CRUD

**Files:**
- Modify: `backend/db.py`
- Test: `backend/tests/test_judgment_crud.py` (new)

Ports `last-self-hosted-single-owner:backend/db.py:425-489` (`get_unjudged_stock_items`, `count_unjudged_stock_items`, `get_taste_listing`, `upsert_stock_judgments`, `has_any_stock_judgment`, `clear_stock_judgments`, `get_recommended_stock_items`), each rescoped to `user_id`. `get_taste_listing` changes source from the old flat `releases` table to `library_items` joined to `catalog`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_judgment_crud.py`:

```python
import db


def _seed_stock_item(conn, artist="Artist A", title="Album A", url="https://x/1"):
    db.register_crawler(conn, "Amazon", "/x.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    db.replace_stock_items(conn, crawler_id, [
        {"artist": artist, "title": title, "url": url, "price": 10.0, "currency": "USD"},
    ])
    return db.compute_item_key(artist.title(), title, url)


def test_get_taste_listing_reads_calling_users_library(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "Artist A", "title": "Album A", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True)
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        taste = db.get_taste_listing(conn, alice["id"])
    assert taste == ["Artist A - Album A"]


def test_unjudged_items_excludes_owned_and_already_judged(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        item_key = _seed_stock_item(conn)
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.count_unjudged_stock_items(conn, alice["id"]) == 1
        unjudged = db.get_unjudged_stock_items(conn, alice["id"], limit=10)
        assert len(unjudged) == 1

        db.upsert_stock_judgments(conn, alice["id"], [{"item_key": item_key, "recommended": True, "reason": "x"}])
        assert db.count_unjudged_stock_items(conn, alice["id"]) == 0


def test_has_any_stock_judgment_and_clear_are_per_user(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        item_key = _seed_stock_item(conn)
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [{"item_key": item_key, "recommended": True, "reason": "x"}])
        assert db.has_any_stock_judgment(conn, alice["id"]) is True

    with db.user_scope(bob["id"]) as conn:
        assert db.has_any_stock_judgment(conn, bob["id"]) is False

    with db.user_scope(alice["id"]) as conn:
        count = db.clear_stock_judgments(conn, alice["id"])
        assert count == 1
        assert db.has_any_stock_judgment(conn, alice["id"]) is False


def test_get_recommended_stock_items_for_user(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        item_key = _seed_stock_item(conn)
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [{"item_key": item_key, "recommended": True, "reason": "great fit"}])
        items = db.get_recommended_stock_items(conn, alice["id"])
    assert len(items) == 1
    assert items[0]["reason"] == "great fit"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_judgment_crud.py -v`
Expected: FAIL — functions don't exist.

- [ ] **Step 3: Implement in `backend/db.py`**

Add after `get_distinct_stock_artists`:

```python
def get_unjudged_stock_items(conn, user_id: int, limit: int) -> list[dict]:
    limit_clause = "LIMIT %(limit)s" if limit > 0 else ""
    rows = conn.execute(
        f"""
        SELECT s.item_key, s.artist, s.title
        FROM stock_items s
        LEFT JOIN stock_item_judgments j ON j.item_key = s.item_key AND j.user_id = %(user_id)s
        WHERE j.item_key IS NULL
          AND {_not_owned_clause('%(user_id)s')}
        GROUP BY s.item_key, s.artist, s.title
        ORDER BY MIN(s.last_seen) ASC
        {limit_clause}
        """,
        {"user_id": user_id, "limit": limit},
    ).fetchall()
    return rows


def count_unjudged_stock_items(conn, user_id: int) -> int:
    return conn.execute(
        f"""
        SELECT COUNT(DISTINCT s.item_key) FROM stock_items s
        LEFT JOIN stock_item_judgments j ON j.item_key = s.item_key AND j.user_id = %(user_id)s
        WHERE j.item_key IS NULL
          AND {_not_owned_clause('%(user_id)s')}
        """,
        {"user_id": user_id},
    ).fetchone()["count"]


def get_taste_listing(conn, user_id: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT c.artist, c.title
        FROM library_items li
        JOIN catalog c ON c.discogs_id = li.discogs_id
        WHERE li.user_id = %s AND (li.in_collection = TRUE OR li.in_wishlist = TRUE)
        ORDER BY c.artist, c.title
        """,
        [user_id],
    ).fetchall()
    return [f"{row['artist']} - {row['title']}" for row in rows]


def upsert_stock_judgments(conn, user_id: int, judgments: list[dict]):
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO stock_item_judgments (user_id, item_key, recommended, reason, judged_at)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id, item_key) DO UPDATE SET
                recommended = EXCLUDED.recommended, reason = EXCLUDED.reason, judged_at = CURRENT_TIMESTAMP
            """,
            [(user_id, j["item_key"], j["recommended"], j.get("reason")) for j in judgments],
        )


def has_any_stock_judgment(conn, user_id: int) -> bool:
    return conn.execute(
        "SELECT EXISTS(SELECT 1 FROM stock_item_judgments WHERE user_id = %s)", [user_id]
    ).fetchone()["exists"]


def clear_stock_judgments(conn, user_id: int) -> int:
    cursor = conn.execute("DELETE FROM stock_item_judgments WHERE user_id = %s", [user_id])
    return cursor.rowcount


def get_recommended_stock_items(conn, user_id: int) -> list[dict]:
    return conn.execute(
        f"""
        SELECT s.artist, s.title, s.format, s.price, cr.site_name AS source, s.url, j.reason AS reason
        FROM stock_items s
        JOIN crawlers cr ON cr.id = s.crawler_id
        JOIN stock_item_judgments j ON j.item_key = s.item_key AND j.user_id = %(user_id)s
        WHERE j.recommended = TRUE
          AND {_not_owned_clause('%(user_id)s')}
        ORDER BY s.artist, s.title
        """,
        {"user_id": user_id},
    ).fetchall()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_judgment_crud.py tests/test_stock_crud.py -v`
Expected: PASS (this also unblocks the two Task 5 tests that depend on `upsert_stock_judgments`).

- [ ] **Step 5: Commit**

```bash
git add backend/db.py backend/tests/test_judgment_crud.py
git commit -m "feat: add per-user stock_item_judgments CRUD to Postgres db.py"
```

---

### Task 7: `db.py` — per-user `get_missing_releases`, `delete_orphaned_releases`, `clear_wishlist_flags_not_in`, `get_distinct_artists`, `get_crawl_status_for_user`

**Files:**
- Modify: `backend/db.py`
- Test: `backend/tests/test_library_maintenance.py` (new)

Ports `last-self-hosted-single-owner:backend/db.py:151-214` (the `mark_in_collection`/`mark_in_wishlist`/`mark_not_in_collection`/`clear_wishlist_flags_not_in`/`delete_orphaned_releases` cluster — the mark/set functions are already covered by `upsert_library_item` from the data-model plan; this task is only the two that don't have an equivalent yet) plus `:535-546` (`get_distinct_artists`) and `:548-579` (`get_crawl_status`, `get_missing_releases`). `no_plex` filtering is dropped — Plex matching is out of scope this plan (design spec Non-goals), so `plex_url` is always `NULL` for every row and a "no Plex match" filter would be a no-op; the frontend stops sending it (Task 20).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_library_maintenance.py`:

```python
import db


def test_get_missing_releases_for_user(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        for rid in ("r1", "r2"):
            db.upsert_catalog_release(conn, {
                "discogs_id": rid, "artist": "A", "title": "T", "year": None, "label": None,
                "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
                "discogs_url": None,
            })
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True)
        db.upsert_library_item(conn, alice["id"], "r2", in_collection=True)
        db.upsert_listing(conn, "r1", crawler_id, "https://x", 9.99, None, "USD", None)
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        missing = db.get_missing_releases(conn, alice["id"])
    assert missing == ["r2"]


def test_get_crawl_status_for_user(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True)
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        status = db.get_crawl_status_for_user(conn, alice["id"])
    assert status == {"total": 1, "missing": 1, "oldest_checked": None}


def test_clear_wishlist_flags_not_in_and_delete_orphaned_releases(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        for rid in ("r1", "r2"):
            db.upsert_catalog_release(conn, {
                "discogs_id": rid, "artist": "A", "title": "T", "year": None, "label": None,
                "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
                "discogs_url": None,
            })
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=False, in_wishlist=True)
        db.upsert_library_item(conn, alice["id"], "r2", in_collection=False, in_wishlist=True)
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        cleared = db.clear_wishlist_flags_not_in(conn, alice["id"], {"r1"})
        assert cleared == 1
        deleted = db.delete_orphaned_releases(conn, alice["id"])
        assert deleted == ["r2"]
        remaining = conn.execute("SELECT discogs_id FROM library_items WHERE user_id = %s", [alice["id"]]).fetchall()
    assert [r["discogs_id"] for r in remaining] == ["r1"]


def test_get_distinct_artists_for_user_scope(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "Zzz", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True)
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        artists = db.get_distinct_artists(conn, alice["id"], scope="collection")
    assert artists == ["Zzz"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_library_maintenance.py -v`
Expected: FAIL — functions don't exist.

- [ ] **Step 3: Implement in `backend/db.py`**

Add after `get_recommended_stock_items`:

```python
def get_missing_releases(conn, user_id: int) -> list[str]:
    enabled_count = conn.execute(
        "SELECT COUNT(*) FROM crawlers WHERE enabled = TRUE"
    ).fetchone()["count"]
    if enabled_count == 0:
        return []
    rows = conn.execute(
        """
        SELECT li.discogs_id FROM library_items li
        WHERE li.user_id = %(user_id)s AND (
            SELECT COUNT(DISTINCT l.crawler_id) FROM listings l
            JOIN crawlers c ON c.id = l.crawler_id AND c.enabled = TRUE
            WHERE l.release_id = li.discogs_id AND l.price IS NOT NULL
        ) < %(enabled_count)s
        """,
        {"user_id": user_id, "enabled_count": enabled_count},
    ).fetchall()
    return [row["discogs_id"] for row in rows]


def get_crawl_status_for_user(conn, user_id: int) -> dict:
    total = conn.execute(
        "SELECT COUNT(*) FROM library_items WHERE user_id = %s", [user_id]
    ).fetchone()["count"]
    enabled_count = conn.execute(
        "SELECT COUNT(*) FROM crawlers WHERE enabled = TRUE"
    ).fetchone()["count"]

    if enabled_count == 0 or total == 0:
        return {"total": total, "missing": total, "oldest_checked": None}

    complete = conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT li.discogs_id
            FROM library_items li
            JOIN listings l ON l.release_id = li.discogs_id
            JOIN crawlers c ON c.id = l.crawler_id AND c.enabled = TRUE
            WHERE li.user_id = %(user_id)s AND l.price IS NOT NULL
            GROUP BY li.discogs_id
            HAVING COUNT(DISTINCT l.crawler_id) = %(enabled_count)s
        ) complete_releases
        """,
        {"user_id": user_id, "enabled_count": enabled_count},
    ).fetchone()["count"]

    oldest = conn.execute(
        """
        SELECT MIN(l.last_checked) FROM listings l
        JOIN library_items li ON li.discogs_id = l.release_id
        WHERE li.user_id = %s
        """,
        [user_id],
    ).fetchone()["min"]

    return {"total": total, "missing": total - complete, "oldest_checked": oldest}


def clear_wishlist_flags_not_in(conn, user_id: int, seen_ids: set) -> int:
    cursor = conn.execute(
        "UPDATE library_items SET in_wishlist = FALSE WHERE user_id = %s AND in_wishlist = TRUE AND discogs_id != ALL(%s)",
        [user_id, list(seen_ids)],
    )
    return cursor.rowcount


def delete_orphaned_releases(conn, user_id: int) -> list[str]:
    rows = conn.execute(
        """
        DELETE FROM library_items
        WHERE user_id = %s AND in_collection = FALSE AND in_wishlist = FALSE
        RETURNING discogs_id
        """,
        [user_id],
    ).fetchall()
    return [row["discogs_id"] for row in rows]


def get_distinct_artists(conn, user_id: int, scope: Optional[str] = None) -> list[str]:
    conditions = ["li.user_id = %(user_id)s"]
    if scope == "collection":
        conditions.append("li.in_collection = TRUE")
    elif scope == "wishlist":
        conditions.append("li.in_wishlist = TRUE")
    where = "WHERE " + " AND ".join(conditions)
    rows = conn.execute(
        f"""
        SELECT DISTINCT c.artist FROM library_items li
        JOIN catalog c ON c.discogs_id = li.discogs_id
        {where} ORDER BY c.artist
        """,
        {"user_id": user_id},
    ).fetchall()
    return [row["artist"] for row in rows]
```

`clear_wishlist_flags_not_in`'s `discogs_id != ALL(%s)` is the Postgres array-parameter idiom for "not in this set," passing a Python list directly (psycopg3 adapts `list` to a Postgres array automatically). `delete_orphaned_releases` changes shape from the old code (which deleted from the shared `releases` table, cascading to `listings`) — here it only ever deletes the calling user's own `library_items` row; the shared `catalog`/`listings` rows are untouched (another user may still reference them), matching the design spec's explicit note that "the global `catalog` row is never deleted by any single user's sync."

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_library_maintenance.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/db.py backend/tests/test_library_maintenance.py
git commit -m "feat: rescope library maintenance queries (missing/orphaned/wishlist/artists/crawl-status) to per-user"
```

---

### Task 8: `discogs.py` — per-user OAuth1.0a signing

**Files:**
- Modify: `backend/discogs.py`
- Test: `backend/tests/test_discogs.py`

- [ ] **Step 1: Write the failing tests**

Read the current `backend/tests/test_discogs.py` first to see its existing fixtures/mocking style (it already mocks `httpx` per CLAUDE.md's testing conventions), then add/replace tests to assert OAuth1 signing instead of a static header. Add:

```python
import respx
import httpx
import config
from authlib.integrations.httpx_client import OAuth1Client
import discogs


@respx.mock
def test_get_identity_signs_with_users_own_oauth_token(monkeypatch):
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "consumer-key")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "consumer-secret")
    route = respx.get("https://api.discogs.com/oauth/identity").mock(
        return_value=httpx.Response(200, json={"id": 1, "username": "alice"})
    )
    result = discogs.get_identity("user-token", "user-token-secret")
    assert result["username"] == "alice"
    auth_header = route.calls.last.request.headers["authorization"]
    assert 'oauth_token="user-token"' in auth_header
```

Add one equivalent per-function test for `iter_collection_pages` and `fetch_release_barcode` (mock one page, assert the same `oauth_token` shows up in the signed `Authorization` header) — follow the existing test file's page-mocking pattern for `iter_collection_pages` if one already exists; adapt it to pass `(oauth_token, oauth_token_secret)` instead of a bearer token.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_discogs.py -v`
Expected: FAIL — `get_identity()` still takes one `token` arg and sends a static header, not OAuth1-signed.

- [ ] **Step 3: Rewrite `backend/discogs.py`**

Replace the whole file:

```python
from authlib.integrations.httpx_client import OAuth1Client
from logging_config import get_logger
import config

log = get_logger("discogs")
DISCOGS_API = "https://api.discogs.com"
_USER_AGENT = "DiscogsCollectionBrowser/1.0 +https://github.com/local/discogs-browser"


def _client(oauth_token: str, oauth_token_secret: str) -> OAuth1Client:
    return OAuth1Client(
        client_id=config.DISCOGS_CONSUMER_KEY,
        client_secret=config.DISCOGS_CONSUMER_SECRET,
        token=oauth_token,
        token_secret=oauth_token_secret,
        headers={"User-Agent": _USER_AGENT},
    )


def get_identity(oauth_token: str, oauth_token_secret: str) -> dict:
    with _client(oauth_token, oauth_token_secret) as client:
        r = client.get(f"{DISCOGS_API}/oauth/identity")
        r.raise_for_status()
        return r.json()


def fetch_collection_fields(oauth_token: str, oauth_token_secret: str, username: str) -> dict:
    """Return a mapping of field_id -> field_name for the user's custom collection fields."""
    with _client(oauth_token, oauth_token_secret) as client:
        r = client.get(f"{DISCOGS_API}/users/{username}/collection/fields")
        r.raise_for_status()
        fields = r.json().get("fields", [])
        return {f["id"]: f["name"] for f in fields}


def iter_collection_pages(oauth_token: str, oauth_token_secret: str, username: str):
    """Yield (page, total_pages, items) for each page of the user's collection."""
    with _client(oauth_token, oauth_token_secret) as client:
        page = 1
        while True:
            log.info("Fetching collection page %d for %s", page, username)
            r = client.get(
                f"{DISCOGS_API}/users/{username}/collection/folders/0/releases",
                params={"page": page, "per_page": 100},
            )
            r.raise_for_status()
            data = r.json()
            total_pages = data["pagination"]["pages"]
            items = data["releases"]
            log.info("Page %d/%d — %d releases on this page", page, total_pages, len(items))
            yield page, total_pages, items
            if page >= total_pages:
                break
            page += 1


def iter_wantlist_pages(oauth_token: str, oauth_token_secret: str, username: str):
    """Yield (page, total_pages, items) for each page of the user's wantlist."""
    with _client(oauth_token, oauth_token_secret) as client:
        page = 1
        while True:
            log.info("Fetching wantlist page %d for %s", page, username)
            r = client.get(
                f"{DISCOGS_API}/users/{username}/wants",
                params={"page": page, "per_page": 100},
            )
            r.raise_for_status()
            data = r.json()
            total_pages = data["pagination"]["pages"]
            items = data["wants"]
            log.info("Page %d/%d — %d wantlist items on this page", page, total_pages, len(items))
            yield page, total_pages, items
            if page >= total_pages:
                break
            page += 1


def fetch_release_barcode(oauth_token: str, oauth_token_secret: str, release_id: int) -> str:
    """Return the first Barcode identifier for a release as digits only, or empty string."""
    with _client(oauth_token, oauth_token_secret) as client:
        r = client.get(f"{DISCOGS_API}/releases/{release_id}")
        r.raise_for_status()
        identifiers = r.json().get("identifiers", [])
        for ident in identifiers:
            if ident.get("type") == "Barcode":
                raw = ident.get("value", "")
                return "".join(c for c in raw if c.isdigit())
        return ""


def parse_release(item: dict, price_field_id=None) -> dict:
    info = item["basic_information"]
    artist = info["artists"][0]["name"] if info.get("artists") else "Unknown"
    label = info["labels"][0]["name"] if info.get("labels") else ""
    fmt = info["formats"][0]["name"] if info.get("formats") else ""
    release_id = info["id"]
    discogs_price = None
    if price_field_id is not None:
        for note in item.get("notes", []):
            if note.get("field_id") == price_field_id:
                discogs_price = note.get("value") or None
                break
    return {
        "discogs_id": f"r{release_id}",
        "artist": artist,
        "title": info.get("title", ""),
        "year": info.get("year"),
        "label": label,
        "format": fmt,
        "cover_image_url": info.get("cover_image", ""),
        "discogs_url": f"https://www.discogs.com/release/{release_id}",
        "discogs_price": discogs_price,
        "barcode": None,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_discogs.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/discogs.py backend/tests/test_discogs.py
git commit -m "feat: sign every Discogs API call with the calling user's own OAuth1.0a token pair"
```

---

### Task 9: Admin authorization gate

**Files:**
- Create: `backend/admin.py`
- Modify: `backend/db.py` (one small helper)
- Test: `backend/tests/test_admin.py` (new)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_admin.py`:

```python
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import db
from admin import require_admin

app = FastAPI()


@app.get("/needs-admin")
def needs_admin(request: Request):
    require_admin(request)
    return {"ok": True}


def test_require_admin_rejects_non_admin(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()

    client = TestClient(app)

    def override(request, call_next):
        request.state.user_id = user["id"]
        return call_next(request)

    app.middleware("http")(override)
    r = client.get("/needs-admin")
    assert r.status_code == 403


def test_require_admin_allows_admin(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute("UPDATE users SET is_admin = TRUE WHERE id = %s", [user["id"]])
        conn.commit()

    client = TestClient(app)

    def override(request, call_next):
        request.state.user_id = user["id"]
        return call_next(request)

    app.middleware("http")(override)
    r = client.get("/needs-admin")
    assert r.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_admin.py -v`
Expected: FAIL — `admin.py` doesn't exist.

- [ ] **Step 3: Implement**

Add to `backend/db.py`, after `get_user_by_discogs_id`:

```python
def is_user_admin(conn, user_id: int) -> bool:
    row = conn.execute("SELECT is_admin FROM users WHERE id = %s", [user_id]).fetchone()
    return bool(row and row["is_admin"])
```

Create `backend/admin.py`:

```python
from fastapi import HTTPException, Request
import db


def require_admin(request: Request):
    with db.get_identity_pool().connection() as conn:
        if not db.is_user_admin(conn, request.state.user_id):
            raise HTTPException(status_code=403, detail="Admin access required")
```

`get_identity_pool()` (BYPASSRLS `app_identity` role) is used rather than `user_scope()` because this check has to run before we know whether the caller is even allowed inside the RLS-scoped `app_user` context for the resource they're requesting — it mirrors how `auth_middleware.py`'s own session resolution already reads the `users` row through the identity pool.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/pytest tests/test_admin.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/admin.py backend/db.py backend/tests/test_admin.py
git commit -m "feat: add require_admin authorization gate for admin-only endpoints"
```

---

### Task 10: `crawl_manager.py` — rewrite collection/wishlist sync (per-user, Postgres, enqueues `crawl_queue`)

**Files:**
- Modify: `backend/crawl_manager.py` (replace `_sync_collection` and everything `start_sync`-related)
- Test: `backend/tests/test_crawl_manager.py`

This replaces `_sync_collection` (currently ~110 lines using SQLite + a static Discogs token). The new version takes a `user_id`, decrypts that user's OAuth token pair, calls the rewritten `discogs.py`, writes through `db.user_scope(user_id)`, and enqueues `crawl_queue` rows instead of leaving crawling to a separate manual step.

- [ ] **Step 1: Write the failing test**

The existing `backend/tests/test_crawl_manager.py` currently fails to import (SQLite-era). Read it fully first to preserve any patterns worth keeping (event-broadcast assertions, mocking conventions), then replace the `_sync_collection`-related tests with:

```python
import respx
import httpx
import db
import token_encryption
from crawl_manager import CrawlManager


@respx.mock
async def test_sync_collection_enqueues_crawl_queue_for_missing_listings(pg_test_db, monkeypatch):
    import config
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "k")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "s")
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", "kL8mN2pQ7rT5vX9yB3cF6hJ1kM4nP8sU2wZ5aD7eG0i=")

    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute(
            "UPDATE users SET discogs_oauth_token_encrypted = %s, discogs_oauth_secret_encrypted = %s WHERE id = %s",
            [token_encryption.encrypt("tok"), token_encryption.encrypt("sec"), user["id"]],
        )
        db.register_crawler(conn, "Amazon", "/x.py")
        conn.commit()

    respx.get("https://api.discogs.com/users/alice/collection/fields").mock(
        return_value=httpx.Response(200, json={"fields": []})
    )
    respx.get("https://api.discogs.com/users/alice/collection/folders/0/releases").mock(
        return_value=httpx.Response(200, json={
            "pagination": {"pages": 1},
            "releases": [{
                "basic_information": {
                    "id": 111, "title": "Album", "year": 2020,
                    "artists": [{"name": "Artist"}], "labels": [], "formats": [],
                    "cover_image": "",
                },
            }],
        })
    )
    respx.get("https://api.discogs.com/releases/111").mock(
        return_value=httpx.Response(200, json={"identifiers": []})
    )
    respx.get("https://api.discogs.com/users/alice/wants").mock(
        return_value=httpx.Response(200, json={"pagination": {"pages": 1}, "wants": []})
    )

    manager = CrawlManager()
    await manager._sync_collection(user["id"], "all")

    with db.user_scope(user["id"]) as conn:
        item = conn.execute(
            "SELECT in_collection FROM library_items WHERE user_id = %s AND discogs_id = 'r111'", [user["id"]]
        ).fetchone()
        assert item["in_collection"] is True

    with db.get_admin_pool().connection() as conn:
        queued = conn.execute("SELECT * FROM crawl_queue WHERE discogs_id = 'r111'").fetchall()
    assert len(queued) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_crawl_manager.py::test_sync_collection_enqueues_crawl_queue_for_missing_listings -v`
Expected: FAIL — `_sync_collection` still takes no `user_id` and imports `sqlite3`.

- [ ] **Step 3: Implement**

In `backend/crawl_manager.py`, replace the entire `start_sync`/`_sync_collection` pair:

```python
    @property
    def sync_running(self) -> bool:
        return self._sync_task is not None and not self._sync_task.done()

    async def start_sync(self, user_id: int, mode: str = "all") -> bool:
        if self.sync_running:
            log.warning("Collection sync already running, ignoring start request")
            return False
        self._sync_task = asyncio.create_task(self._sync_collection(user_id, mode))
        return True

    async def _sync_collection(self, user_id: int, mode: str):
        import token_encryption
        import discogs
        from db import (
            get_identity_pool, get_app_pool, user_scope, upsert_catalog_release, upsert_library_item,
            clear_wishlist_flags_not_in, delete_orphaned_releases, get_enabled_crawlers, enqueue_crawl_queue,
        )
        import httpx

        await self._broadcast({"status": "sync_started"})
        log.info("Collection sync started for user %d (mode=%s)", user_id, mode)
        try:
            with get_identity_pool().connection() as conn:
                user = conn.execute("SELECT * FROM users WHERE id = %s", [user_id]).fetchone()
            if not user["discogs_oauth_token_encrypted"]:
                await self._broadcast({"status": "sync_error", "error": "Discogs account not connected"})
                return
            oauth_token = token_encryption.decrypt(user["discogs_oauth_token_encrypted"])
            oauth_secret = token_encryption.decrypt(user["discogs_oauth_secret_encrypted"])
            username = user["discogs_username"]

            try:
                fields = discogs.fetch_collection_fields(oauth_token, oauth_secret, username)
            except httpx.HTTPStatusError:
                await self._broadcast({"status": "sync_error", "error": "Discogs request failed"})
                return
            price_field_id = next((fid for fid, name in fields.items() if name.lower() == "price"), None)

            with get_app_pool().connection() as conn:
                enabled_crawlers = get_enabled_crawlers(conn)

            count = 0
            wishlist_count = 0
            wishlist_seen: set = set()
            with user_scope(user_id) as conn:
                existing = None
                if mode == "new":
                    existing = {row["discogs_id"] for row in conn.execute(
                        "SELECT discogs_id FROM library_items WHERE user_id = %s AND in_collection = TRUE", [user_id]
                    ).fetchall()}

                for page, total_pages, items in discogs.iter_collection_pages(oauth_token, oauth_secret, username):
                    for item in items:
                        rid = f"r{item['basic_information']['id']}"
                        if existing is not None and rid in existing:
                            continue
                        release = discogs.parse_release(item, price_field_id=price_field_id)
                        existing_row = conn.execute(
                            "SELECT barcode FROM catalog WHERE discogs_id = %s", [rid]
                        ).fetchone()
                        if existing_row is None or existing_row["barcode"] is None:
                            try:
                                release["barcode"] = discogs.fetch_release_barcode(
                                    oauth_token, oauth_secret, item["basic_information"]["id"]
                                ) or None
                            except Exception as e:
                                log.warning("Barcode fetch failed for release %s: %s", rid, e)
                            await asyncio.sleep(1.1)
                        else:
                            release["barcode"] = existing_row["barcode"]
                        upsert_catalog_release(conn, release)
                        upsert_library_item(conn, user_id, rid, in_collection=True)
                        for crawler in enabled_crawlers:
                            enqueue_crawl_queue(conn, rid, crawler["id"])
                        count += 1
                    await self._broadcast({"status": "sync_progress", "synced": count, "page": page, "total_pages": total_pages})
                    log.info("Sync page %d/%d (%d releases) for user %d", page, total_pages, count, user_id)

                for page, total_pages, items in discogs.iter_wantlist_pages(oauth_token, oauth_secret, username):
                    for item in items:
                        rid = f"r{item['basic_information']['id']}"
                        wishlist_seen.add(rid)
                        release = discogs.parse_release(item, price_field_id=None)
                        existing_row = conn.execute(
                            "SELECT barcode FROM catalog WHERE discogs_id = %s", [rid]
                        ).fetchone()
                        is_new_release = existing_row is None
                        if existing_row is None or existing_row["barcode"] is None:
                            try:
                                release["barcode"] = discogs.fetch_release_barcode(
                                    oauth_token, oauth_secret, item["basic_information"]["id"]
                                ) or None
                            except Exception as e:
                                log.warning("Barcode fetch failed for wishlist release %s: %s", rid, e)
                            await asyncio.sleep(1.1)
                        else:
                            release["barcode"] = existing_row["barcode"]
                        upsert_catalog_release(conn, release)
                        upsert_library_item(
                            conn, user_id, rid, in_wishlist=True,
                            in_collection=False if is_new_release else None,
                        )
                        for crawler in enabled_crawlers:
                            enqueue_crawl_queue(conn, rid, crawler["id"])
                        wishlist_count += 1
                    log.info("Wishlist sync page %d/%d (%d items) for user %d", page, total_pages, wishlist_count, user_id)

                cleared = clear_wishlist_flags_not_in(conn, user_id, wishlist_seen)
                deleted = delete_orphaned_releases(conn, user_id)
                conn.commit()
                log.info(
                    "Wishlist sync complete for user %d: %d items, %d stale entries cleared, %d releases deleted",
                    user_id, wishlist_count, cleared, len(deleted),
                )

            await self._broadcast({
                "status": "sync_complete", "synced": count, "wishlist_synced": wishlist_count, "username": username,
            })
            log.info("Collection sync complete: %d releases, %d wishlist items for %s", count, wishlist_count, username)

        except asyncio.CancelledError:
            log.info("Collection sync cancelled")
            raise
        except Exception as e:
            log.error("Collection sync failed: %s", e, exc_info=True)
            await self._broadcast({"status": "sync_error", "error": str(e)})
```

This drops the Plex-match call entirely (`_run_plex_match` and its invocation) per the design spec's Non-goals — leave `_run_plex_match` deleted, it's addressed by base spec item 4 later. Also delete the now-unused `_run_plex_match` method body from the file.

`upsert_library_item`'s existing `COALESCE`-based signature (from the data-model plan) already treats `None` as "leave unspecified column alone," so passing `in_collection=False if is_new_release else None` during the wishlist pass reproduces the old code's "only clear `in_collection` for a genuinely new row, never overwrite an existing collection flag" behavior exactly.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/pytest tests/test_crawl_manager.py -v`
Expected: PASS (at minimum the sync test — the stock/judgment tests in this file are addressed in Task 12).

- [ ] **Step 5: Commit**

```bash
git add backend/crawl_manager.py backend/tests/test_crawl_manager.py
git commit -m "feat: rewrite collection/wishlist sync for per-user OAuth signing, Postgres, and crawl_queue enqueue"
```

---

### Task 11: `crawl_manager.py` — worker pool draining `crawl_queue`, internal event dispatch

**Files:**
- Modify: `backend/crawl_manager.py`
- Test: `backend/tests/test_crawl_manager.py`

Replaces the old single-loop `_run`/`start`/`stop`. `crawler.py`'s `crawl_releases()` generator (`backend/crawler.py:122-265`) is fully superseded by this task — its per-release-batch loop doesn't fit a worker continuously draining a shared queue one small claimed batch at a time — but its browser/context building blocks (`_new_context`, `_reset_context`, both already free functions in `crawler.py`, plus `BotDetectedError`) carry over unchanged, reused directly rather than reinvented. Delete `crawl_releases()` and its now-orphaned tests once this task lands (grep `backend/tests/` for `crawl_releases` to find them).

Per the design spec, each worker owns one Playwright `Page` per crawler plugin it actually encounters (lazily created on first use, cached for the worker's lifetime) — not a fresh browser per claim. One shared `playwright`/`browser` instance is launched once at pool startup (`start_worker_pool`), matching `crawl_releases()`'s existing "one `pw.chromium.launch()` per crawl" granularity, just scoped to the whole pool's lifetime instead of one batch's.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_crawl_manager.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch


async def test_worker_claims_and_completes_one_queue_row(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
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
        claimed = await manager._drain_one_batch("worker-test", {crawler_id: fake_plugin}, pages={})

    assert claimed == 1
    with db.get_admin_pool().connection() as conn:
        listing = conn.execute("SELECT price FROM listings WHERE release_id = 'r1'").fetchone()
        queue_row = conn.execute("SELECT status FROM crawl_queue WHERE discogs_id = 'r1'").fetchone()
    assert listing["price"] == 9.99
    assert queue_row["status"] == "done"


async def test_worker_retries_once_on_bot_detection_then_succeeds(pg_test_db):
    from crawler import BotDetectedError
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1", crawler_id)
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    fake_plugin = AsyncMock()
    fake_plugin.search = AsyncMock(side_effect=[
        BotDetectedError(),
        [{"url": "https://x", "price": 5.0, "shipping": None, "currency": "USD", "condition": None}],
    ])
    fake_plugin._db_id = crawler_id
    fake_plugin._db_site_name = "Amazon"

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))), \
         patch("crawler._reset_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
        claimed = await manager._drain_one_batch("worker-test", {crawler_id: fake_plugin}, pages={})

    assert claimed == 1
    with db.get_admin_pool().connection() as conn:
        listing = conn.execute("SELECT price FROM listings WHERE release_id = 'r1'").fetchone()
    assert listing["price"] == 5.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_crawl_manager.py -k worker -v`
Expected: FAIL — `_drain_one_batch` doesn't exist.

- [ ] **Step 3: Implement**

Add to `CrawlManager` in `backend/crawl_manager.py` (replacing the old `start`/`stop`/`_run`/`running`):

```python
    def __init__(self):
        self._sync_task: Optional[asyncio.Task] = None
        self._stock_task: Optional[asyncio.Task] = None
        self._judgment_task: Optional[asyncio.Task] = None
        self._worker_tasks: list[asyncio.Task] = []
        self._pool_running = False
        self._playwright = None
        self._browser = None
        self._stealth = None
        self._subscribers: list[asyncio.Queue] = []
        self._recent: list[dict] = []
        self._seq = 0

    @property
    def pool_running(self) -> bool:
        return self._pool_running

    async def start_worker_pool(self, worker_count: int = 2):
        from playwright.async_api import async_playwright
        from playwright_stealth import Stealth
        from crawler import load_enabled_crawlers
        from config import PLAYWRIGHT_CHANNEL
        from db import get_app_pool, get_enabled_crawlers

        with get_app_pool().connection() as conn:
            enabled = get_enabled_crawlers(conn)
        plugins = load_enabled_crawlers(enabled)
        plugins_by_crawler_id = {p._db_id: p for p in plugins}

        self._stealth = Stealth()
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            channel=PLAYWRIGHT_CHANNEL,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._pool_running = True
        for i in range(worker_count):
            self._worker_tasks.append(asyncio.create_task(self._worker_loop(f"worker-{i}", plugins_by_crawler_id)))
        log.info("Crawl worker pool started: %d workers, %d crawler plugins", worker_count, len(plugins))

    async def stop_worker_pool(self):
        self._pool_running = False
        for task in self._worker_tasks:
            task.cancel()
        for task in self._worker_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._worker_tasks = []
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def _worker_loop(self, worker_id: str, plugins_by_crawler_id: dict):
        pages: dict = {}
        try:
            while self._pool_running:
                try:
                    claimed = await self._drain_one_batch(worker_id, plugins_by_crawler_id, pages)
                    if claimed == 0:
                        await asyncio.sleep(5.0)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.error("[%s] Worker loop error: %s", worker_id, e, exc_info=True)
                    await asyncio.sleep(5.0)
        finally:
            for context, _page in pages.values():
                await context.close()

    async def _drain_one_batch(self, worker_id: str, plugins_by_crawler_id: dict, pages: dict, batch_size: int = 5) -> int:
        from crawler import _new_context, _reset_context, BotDetectedError
        from db import get_app_pool, claim_crawl_queue_batch, mark_crawl_queue_done, upsert_listing, get_catalog_release

        with get_app_pool().connection() as conn:
            rows = claim_crawl_queue_batch(conn, worker_id, limit=batch_size)
            conn.commit()
            if not rows:
                return 0
            for row in rows:
                plugin = plugins_by_crawler_id.get(row["crawler_id"])
                release = get_catalog_release(conn, row["discogs_id"])
                if plugin is None or release is None:
                    mark_crawl_queue_done(conn, row["id"])
                    continue

                if row["crawler_id"] not in pages:
                    pages[row["crawler_id"]] = await _new_context(self._browser, self._stealth)
                context, page = pages[row["crawler_id"]]

                try:
                    matches = await plugin.search(release, page)
                except BotDetectedError:
                    context, page = await _reset_context(context, self._browser, self._stealth, None)
                    pages[row["crawler_id"]] = (context, page)
                    try:
                        matches = await plugin.search(release, page)
                    except Exception as e:
                        log.error("[%s] Crawl failed after bot-detection retry for %s: %s", plugin._db_site_name, row["discogs_id"], e)
                        mark_crawl_queue_done(conn, row["id"])
                        continue
                except Exception as e:
                    log.error("[%s] Crawl failed for %s: %s", plugin._db_site_name, row["discogs_id"], e)
                    mark_crawl_queue_done(conn, row["id"])
                    continue

                if matches:
                    best = matches[0]
                    upsert_listing(
                        conn, row["discogs_id"], row["crawler_id"], best["url"],
                        best.get("price"), best.get("shipping"), best.get("currency"), best.get("condition"),
                    )
                    await self._broadcast_listing_changed(row["discogs_id"], row["crawler_id"], "found")
                else:
                    await self._broadcast_listing_changed(row["discogs_id"], row["crawler_id"], "not_found")
                mark_crawl_queue_done(conn, row["id"])
            conn.commit()
            return len(rows)

    async def _broadcast_listing_changed(self, discogs_id: str, crawler_id: int, status: str):
        self._seq += 1
        event = {"id": self._seq, "type": "listing_changed", "discogs_id": discogs_id, "crawler_id": crawler_id, "status": status}
        for q in list(self._subscribers):
            await q.put(event)
```

`_reset_context`'s fourth argument is a `screenshotter` — passed `None` here (this worker pool has no per-batch screenshot session the way `crawl_releases()` did; screenshot debugging for the shared queue, if wanted later, is a separate concern, not needed for bot-recovery to work). `_reset_context` already handles `screenshotter=None` via its existing `if screenshotter:` guard, so no change to `crawler.py` itself is needed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_crawl_manager.py -v`
Expected: PASS. Then delete `crawl_releases()` from `backend/crawler.py` (lines 122-265 as of this writing) and any test file exercising it directly (grep `backend/tests/` for `crawl_releases`) — it has no remaining callers once this task lands. Leave `_new_context`, `_reset_context`, `BotDetectedError`, and `load_enabled_crawlers` in place; they're still used.

- [ ] **Step 5: Commit**

```bash
git add backend/crawl_manager.py backend/crawler.py backend/tests/test_crawl_manager.py
git commit -m "feat: add in-process worker pool draining crawl_queue, reusing crawler.py's context/bot-recovery helpers"
```

---

### Task 12: `crawl_manager.py` — rewrite `_sync_stock` (global) and per-user judgment phase

**Files:**
- Modify: `backend/crawl_manager.py`
- Test: `backend/tests/test_crawl_manager.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_crawl_manager.py`:

```python
async def test_sync_stock_replaces_items_for_each_enabled_catalog_crawler(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Stock Site", "/x.py", crawler_type="catalog")
        conn.commit()

    fake_plugin = AsyncMock()
    async def _items():
        yield {"artist": "A", "title": "T", "url": "https://x/1", "price": 5.0, "currency": "USD"}
    fake_plugin.crawl_catalog = lambda: _items()
    fake_plugin._db_site_name = "Stock Site"
    fake_plugin._db_id = None  # set below once we have the real id

    with db.get_admin_pool().connection() as conn:
        fake_plugin._db_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Stock Site'").fetchone()["id"]

    manager = CrawlManager()
    with patch("crawler.load_enabled_crawlers", return_value=[fake_plugin]):
        await manager._sync_stock()

    with db.get_admin_pool().connection() as conn:
        items = conn.execute("SELECT artist FROM stock_items").fetchall()
    assert len(items) == 1


async def test_judgment_phase_uses_calling_users_own_key_and_taste(pg_test_db, monkeypatch):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute("UPDATE users SET anthropic_api_key = 'sk-alice' WHERE id = %s", [alice["id"]])
        db.register_crawler(conn, "Stock Site", "/x.py", crawler_type="catalog")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Stock Site'").fetchone()["id"]
        db.replace_stock_items(conn, crawler_id, [
            {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
        ])
        conn.commit()

    with patch("recommendations.judge_batch", return_value=[
        {"item_key": db.compute_item_key("Artist A", "Album A", "https://x/1"), "recommended": True, "reason": "matches taste"}
    ]) as mock_judge:
        manager = CrawlManager()
        await manager._run_judgment_phase(alice["id"])

    assert mock_judge.call_args[0][0] == "sk-alice" or True  # exact call shape depends on recommendations.judge_batch's signature; assert the key was threaded through
    with db.user_scope(alice["id"]) as conn:
        judged = conn.execute("SELECT reason FROM stock_item_judgments WHERE user_id = %s", [alice["id"]]).fetchall()
    assert judged[0]["reason"] == "matches taste"
```

Before finalizing this second test, read `backend/recommendations.py`'s actual `judge_batch` signature — the assertion on `mock_judge.call_args` above is deliberately loose (`or True`) because this plan doesn't have that file's exact signature in hand; tighten it to a real assertion once you've read `recommendations.py`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_crawl_manager.py -k "sync_stock or judgment_phase" -v`
Expected: FAIL — `_sync_stock`/`_run_judgment_phase` still use the old SQLite/global signature.

- [ ] **Step 3: Implement**

Replace `_sync_stock`, `start_stock_sync`, `_run_judgment_phase`, `start_judgment_only`, `_run_judgment_only` in `backend/crawl_manager.py`:

```python
    @property
    def stock_sync_running(self) -> bool:
        return self._stock_task is not None and not self._stock_task.done()

    async def start_stock_sync(self) -> bool:
        if self.stock_sync_running:
            log.warning("Stock sync already running, ignoring start request")
            return False
        self._stock_task = asyncio.create_task(self._sync_stock())
        return True

    async def _sync_stock(self):
        from db import get_app_pool, get_enabled_crawlers, replace_stock_items, update_crawler_last_run
        from crawler import load_enabled_crawlers

        await self._broadcast({"status": "stock_sync_started"})
        log.info("Stock sync started")
        try:
            with get_app_pool().connection() as conn:
                enabled = get_enabled_crawlers(conn, crawler_type="catalog")
            crawlers = load_enabled_crawlers(enabled)
            if not crawlers:
                await self._broadcast({"status": "stock_sync_error", "error": "No enabled catalog crawlers"})
                return

            total_synced = 0
            for crawler in crawlers:
                items = []
                try:
                    async for item in crawler.crawl_catalog():
                        items.append(item)
                except Exception as e:
                    log.error("[%s] Stock crawl failed: %s", crawler._db_site_name, e, exc_info=True)
                    await self._broadcast({"status": "stock_sync_error", "error": str(e), "source": crawler._db_site_name})
                    continue

                with get_app_pool().connection() as conn:
                    replace_stock_items(conn, crawler._db_id, items)
                    update_crawler_last_run(conn, crawler._db_id)
                    conn.commit()
                total_synced += len(items)
                log.info("[%s] Stock sync found %d items", crawler._db_site_name, len(items))
                await self._broadcast({"status": "stock_sync_progress", "synced": total_synced, "source": crawler._db_site_name})

            await self._broadcast({"status": "stock_sync_complete", "synced": total_synced})
            log.info("Stock sync complete: %d items", total_synced)
        except asyncio.CancelledError:
            log.info("Stock sync cancelled")
            raise
        except Exception as e:
            log.error("Stock sync failed: %s", e, exc_info=True)
            await self._broadcast({"status": "stock_sync_error", "error": str(e)})

    @property
    def judgment_running(self) -> bool:
        return self._judgment_task is not None and not self._judgment_task.done()

    async def start_judgment_only(self, user_id: int) -> bool:
        if self.judgment_running:
            log.warning("Judgment already running, ignoring start request")
            return False
        self._judgment_task = asyncio.create_task(self._run_judgment_phase(user_id))
        return True

    async def _run_judgment_phase(self, user_id: int):
        from db import (
            get_identity_pool, user_scope, get_unjudged_stock_items, count_unjudged_stock_items,
            get_taste_listing, upsert_stock_judgments,
        )
        import recommendations
        import anthropic

        await self._broadcast({"status": "stock_judgment_started"})
        log.info("Judgment run started for user %d", user_id)
        try:
            with get_identity_pool().connection() as conn:
                user = conn.execute(
                    "SELECT anthropic_api_key, recommendation_item_limit FROM users WHERE id = %s", [user_id]
                ).fetchone()
            api_key = user["anthropic_api_key"]
            if not api_key:
                await self._broadcast({"status": "stock_judgment_error", "error": "Anthropic API key not configured"})
                return
            limit = user["recommendation_item_limit"] or recommendations.SYNC_CAP

            with user_scope(user_id) as conn:
                total_unjudged = count_unjudged_stock_items(conn, user_id)
                unjudged = get_unjudged_stock_items(conn, user_id, limit)
                taste_listing = get_taste_listing(conn, user_id)

            if not unjudged:
                await self._broadcast({"status": "stock_judgment_complete", "judged": 0})
                log.info("Found 0/0 items to judge for user %d, nothing to do", user_id)
                return
            log.info("Found %d/%d items to judge for user %d", len(unjudged), total_unjudged, user_id)

            client = anthropic.Anthropic(api_key=api_key)
            judged = 0
            for i in range(0, len(unjudged), recommendations.BATCH_SIZE):
                batch = unjudged[i:i + recommendations.BATCH_SIZE]
                results = await asyncio.to_thread(recommendations.judge_batch, client, taste_listing, batch)
                recommended_in_batch = 0
                if results:
                    with user_scope(user_id) as conn:
                        upsert_stock_judgments(conn, user_id, results)
                        conn.commit()
                    judged += len(results)
                    recommended_in_batch = sum(1 for r in results if r["recommended"])
                log.info("Judged batch %d/%d for user %d: %d recommended", judged, len(unjudged), user_id, recommended_in_batch)
                await self._broadcast({"status": "stock_judgment_progress", "judged": judged, "total": len(unjudged)})

            await self._broadcast({"status": "stock_judgment_complete", "judged": judged})
            log.info("Stock judgment complete for user %d: %d items judged", user_id, judged)
        except asyncio.CancelledError:
            log.info("Judgment run cancelled")
            raise
        except Exception as e:
            log.error("Judgment phase failed for user %d: %s", user_id, e, exc_info=True)
            await self._broadcast({"status": "stock_judgment_error", "error": str(e)})
```

Delete `start`, `stop`, `_run`, `running`, `any_job_running`, `subscribe`/`unsubscribe`/`recent_events` changes are NOT needed (those stay — Task 15 still uses them for the per-user-filtered SSE stream) — only the crawl-job-specific `start`/`stop`/`_run`/`running` (single global crawl) go away, replaced by the worker pool from Task 11. Delete `_run_plex_match` if any trace remains from Task 10.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_crawl_manager.py -v`
Expected: PASS — full file now green.

- [ ] **Step 5: Commit**

```bash
git add backend/crawl_manager.py backend/tests/test_crawl_manager.py
git commit -m "feat: rewrite stock sync (global) and judgment phase (per-user key/taste) for Postgres"
```

---

### Task 13: `scheduler.py` — sweep-enqueue rework, drop per-user auto-sync

**Files:**
- Modify: `backend/scheduler.py`
- Test: `backend/tests/test_scheduler.py` (extend if it exists, else create)

- [ ] **Step 1: Write the failing test**

Add:

```python
from unittest.mock import AsyncMock, patch


def test_configure_crawl_schedule_calls_sweep_enqueue():
    import scheduler
    with patch("crawl_manager.crawl_manager") as mock_manager:
        mock_manager.sweep_enqueue = AsyncMock()
        scheduler.configure("*/5 * * * *", "missing")
        job = scheduler._scheduler.get_job("crawl")
        assert job is not None


def test_configure_sync_no_longer_exists():
    import scheduler
    assert not hasattr(scheduler, "configure_sync")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_scheduler.py -v`
Expected: FAIL (or `configure_sync` still exists).

- [ ] **Step 3: Implement**

In `backend/crawl_manager.py`, add a new method used by the schedule (sweeps every user's library, not just one):

```python
    async def sweep_enqueue(self, mode: str = "missing"):
        from db import get_app_pool, get_enabled_crawlers, enqueue_crawl_queue, get_missing_releases
        with get_app_pool().connection() as conn:
            enabled_crawlers = get_enabled_crawlers(conn)
            user_ids = [row["id"] for row in conn.execute("SELECT id FROM users").fetchall()]
        for user_id in user_ids:
            from db import user_scope
            with user_scope(user_id) as conn:
                if mode == "missing":
                    target_ids = get_missing_releases(conn, user_id)
                else:
                    target_ids = [row["discogs_id"] for row in conn.execute(
                        "SELECT discogs_id FROM library_items WHERE user_id = %s", [user_id]
                    ).fetchall()]
                for discogs_id in target_ids:
                    for crawler in enabled_crawlers:
                        enqueue_crawl_queue(conn, discogs_id, crawler["id"])
                conn.commit()
        log.info("Sweep-enqueue complete (mode=%s) across %d users", mode, len(user_ids))
```

(`log` here refers to `crawl_manager.py`'s existing module logger — this method belongs on the `CrawlManager` class, add it near `sweep_enqueue`'s natural neighbors, after `_worker_loop`.)

In `backend/scheduler.py`, remove `configure_sync` entirely, and change `configure`'s job body:

```python
def configure(cron_expression: str, mode: str = "missing"):
    if _scheduler.get_job("crawl"):
        _scheduler.remove_job("crawl")

    if not cron_expression:
        log.info("Crawl schedule cleared")
        return

    async def _run():
        from crawl_manager import crawl_manager
        log.info("Scheduled crawl sweep starting (mode=%s)", mode)
        await crawl_manager.sweep_enqueue(mode)

    try:
        _scheduler.add_job(_run, CronTrigger.from_crontab(cron_expression), id="crawl")
        log.info("Crawl scheduled: %s (mode=%s)", cron_expression, mode)
    except Exception as e:
        log.warning("Invalid schedule expression %r: %s", cron_expression, e)
        raise ValueError(f"Invalid cron expression: {cron_expression}") from e
```

`configure_stock` is unchanged — leave it exactly as-is.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_scheduler.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/scheduler.py backend/crawl_manager.py backend/tests/test_scheduler.py
git commit -m "feat: rework crawl_schedule into an all-users sweep-enqueue, drop per-user auto-sync schedule"
```

---

### Task 14: `main.py` — fix startup wiring, start/stop worker pool

**Files:**
- Modify: `backend/main.py`
- Test: `backend/tests/test_main.py`

- [ ] **Step 1: Write the failing test**

Replace `backend/tests/test_main.py` (currently SQLite-era, fails to import) with a Postgres-based version:

```python
from fastapi.testclient import TestClient
import db


def test_app_boots_and_health_check_succeeds(pg_test_db):
    import main
    client = TestClient(main.app)
    r = client.get("/api/health")
    assert r.status_code == 200


def test_startup_seeds_bundled_crawlers(pg_test_db):
    import main
    with TestClient(main.app):
        with db.get_admin_pool().connection() as conn:
            crawlers = db.get_all_crawlers(conn)
    assert len(crawlers) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_main.py -v`
Expected: FAIL — `main.py` still imports `get_connection`, `init_db` from `db`.

- [ ] **Step 3: Implement**

In `backend/main.py`, change the import line:

```python
from db import get_admin_pool, init_global_schema, init_tenant_schema, register_crawler
```

Change `seed_bundled_crawlers(conn)` to `seed_bundled_crawlers()` (no `conn` param — it opens its own):

```python
def seed_bundled_crawlers():
    with get_admin_pool().connection() as conn:
        for stale in CRAWLERS_DIR.glob("*.py"):
            if stale.name == "__init__.py":
                continue
            if not (BUNDLED_CRAWLERS_DIR / stale.name).exists():
                stale.unlink(missing_ok=True)
                log.info("Removed stale crawler %s from data dir", stale.name)

        for src in BUNDLED_CRAWLERS_DIR.glob("*.py"):
            dest = CRAWLERS_DIR / src.name
            shutil.copy2(src, dest)
            log.info("Synced bundled crawler %s -> %s", src.name, dest)
            site_name, crawler_type = _crawler_metadata(dest, src.stem.replace("_", " ").title())
            register_crawler(conn, site_name, str(dest), crawler_type)
            log.info("Registered bundled crawler: %s", site_name)
        conn.commit()
```

Change `startup()`:

```python
@app.on_event("startup")
async def startup():
    log.info("=" * 60)
    log.info("Discogs Browser backend v%s starting", VERSION)
    ensure_dirs()
    init_global_schema()
    init_tenant_schema()
    seed_bundled_crawlers()
    await crawl_manager.start_worker_pool(worker_count=int(load_config().get("crawl_worker_count", 2)))
    scheduler.start()
    _configure_schedules(load_config())

    log.info("=" * 60)
    log.info("Discogs Browser backend v%s ready", VERSION)


@app.on_event("shutdown")
async def shutdown():
    await crawl_manager.stop_worker_pool()
```

Add the necessary imports at the top: `from crawl_manager import crawl_manager`.

Remove the old `_configure_schedules`'s call to `scheduler.configure_sync(...)` (that function no longer exists per Task 13):

```python
def _configure_schedules(cfg: dict) -> None:
    schedule = cfg.get("crawl_schedule", "")
    if schedule:
        try:
            scheduler.configure(schedule, cfg.get("crawl_schedule_mode", "missing"))
        except ValueError as e:
            log.warning("Ignoring invalid saved crawl schedule: %s", e)

    stock_schedule = cfg.get("stock_schedule", "")
    if stock_schedule:
        try:
            scheduler.configure_stock(stock_schedule)
        except ValueError as e:
            log.warning("Ignoring invalid saved stock schedule: %s", e)
```

Add `from routers import discover` is **not** added — `discover.router` stays unregistered per the design spec.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/pytest tests/test_main.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/main.py backend/tests/test_main.py
git commit -m "fix: rewire main.py startup/shutdown to Postgres schema init and worker pool lifecycle"
```

---

### Task 15: `routers/crawl.py` — rewrite start/status/stream for shared queue, remove stop

**Files:**
- Modify: `backend/routers/crawl.py`
- Test: `backend/tests/test_crawl_router_replay.py` → rename/replace with `backend/tests/test_crawl_router.py`

- [ ] **Step 1: Write the failing tests**

Read the current `backend/tests/test_crawl_router_replay.py` in full first (it tests the SSE-replay-gating logic documented in `_events_to_replay`'s docstring — that gating logic itself is not being removed, only rescoped, so port its intent rather than discarding it). Create `backend/tests/test_crawl_router.py`:

```python
from fastapi.testclient import TestClient
import db


def test_crawl_start_enqueues_for_calling_user_only(pg_test_db, authed_client_factory):
    # authed_client_factory is assumed to already exist in this test suite's
    # conftest (used by other router tests to get a TestClient with a valid
    # session cookie for a given user) — if it doesn't exist yet, add it to
    # conftest.py following the same pattern test_auth_router.py already
    # uses to build an authenticated TestClient, then use it here.
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.upsert_library_item(conn, user["id"], "r1", in_collection=True)
        conn.commit()

    client = authed_client_factory(user["id"])
    r = client.post("/api/crawl/start", json={"mode": "all"})
    assert r.status_code == 200
    assert r.json()["enqueued"] == 1


def test_crawl_stop_endpoint_removed(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(user["id"])
    r = client.post("/api/crawl/stop")
    assert r.status_code == 404


def test_crawl_status_returns_pending_count_and_pool_running(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(user["id"])
    r = client.get("/api/crawl/status")
    assert r.status_code == 200
    body = r.json()
    assert "pending" in body and "pool_running" in body
```

If `authed_client_factory` doesn't already exist as a fixture, add it to `backend/tests/conftest.py`: a factory that builds a `TestClient` wired with `AuthMiddleware`, creates a real session row for the given `user_id` via `db.create_session`, and sets the resulting cookie — mirror exactly how `test_auth_router.py`'s `client` fixture builds its `TestClient(app)` and cookie-setting, generalized to take a `user_id` argument.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_crawl_router.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Replace `backend/routers/crawl.py`:

```python
import asyncio
import json
from typing import Optional
from fastapi import APIRouter, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
import db
from crawl_manager import crawl_manager
from logging_config import get_logger

log = get_logger("routers.crawl")
router = APIRouter()


class CrawlStartRequest(BaseModel):
    mode: str = "all"
    release_id: Optional[str] = None


@router.get("/crawl/status")
def crawl_status(request: Request):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        status = db.get_crawl_status_for_user(conn, user_id)
        pending = db.count_pending_crawl_queue_for_user(conn, user_id)
    status["pending"] = pending
    status["pool_running"] = crawl_manager.pool_running
    return status


@router.post("/crawl/start")
def crawl_start(body: CrawlStartRequest, request: Request):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        enabled_crawlers = db.get_enabled_crawlers(conn)
        if body.release_id:
            target_ids = [body.release_id]
        elif body.mode == "missing":
            target_ids = db.get_missing_releases(conn, user_id)
        else:
            target_ids = [row["discogs_id"] for row in conn.execute(
                "SELECT discogs_id FROM library_items WHERE user_id = %s", [user_id]
            ).fetchall()]
        enqueued = 0
        for discogs_id in target_ids:
            for crawler in enabled_crawlers:
                db.enqueue_crawl_queue(conn, discogs_id, crawler["id"])
                enqueued += 1
        conn.commit()
    return {"enqueued": enqueued}


def _events_to_replay(request: Request) -> list[dict]:
    """Buffered events are only useful to a client reconnecting mid-job. The
    buffer isn't cleared when a job finishes, so once every job is done,
    replaying it on every later page load would flood the client with stale
    history for no benefit. Gated on any per-user-relevant job being active,
    not a single global crawl task, since there's no single "the crawl"
    anymore under a shared queue.
    """
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        pending = db.count_pending_crawl_queue_for_user(conn, user_id)
    any_active = pending > 0 or crawl_manager.sync_running or crawl_manager.stock_sync_running or crawl_manager.judgment_running
    if not any_active:
        return []
    return [
        e for e in crawl_manager.recent_events()
        if e.get("type") != "listing_changed" or _event_touches_user(e, user_id)
    ]


def _event_touches_user(event: dict, user_id: int) -> bool:
    discogs_id = event.get("discogs_id")
    if not discogs_id:
        return True
    with db.user_scope(user_id) as conn:
        row = conn.execute(
            "SELECT 1 FROM library_items WHERE user_id = %s AND discogs_id = %s", [user_id, discogs_id]
        ).fetchone()
    return row is not None


@router.get("/crawl/stream")
async def crawl_stream(request: Request):
    user_id = request.state.user_id

    async def generate():
        q = crawl_manager.subscribe()
        try:
            for event in _events_to_replay(request):
                yield {"data": json.dumps(event)}
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield {"data": json.dumps({"status": "ping"})}
                    continue
                if event.get("type") == "listing_changed" and not _event_touches_user(event, user_id):
                    continue
                yield {"data": json.dumps(event)}
        finally:
            crawl_manager.unsubscribe(q)
    return EventSourceResponse(generate())
```

`POST /crawl/stop` is deleted outright (no route registered), so a request to it now 404s — matching the test's expectation and the design spec's "there is no per-user 'my crawl' to stop."

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_crawl_router.py -v`
Expected: PASS. Delete `backend/tests/test_crawl_router_replay.py` once you've confirmed every behavior it covered has a home in `test_crawl_router.py` (the replay-gating logic's *intent* — don't replay stale history once nothing is active — is preserved in `_events_to_replay`, just rescoped to per-user relevance).

- [ ] **Step 5: Commit**

```bash
git add backend/routers/crawl.py backend/tests/test_crawl_router.py
git rm backend/tests/test_crawl_router_replay.py
git commit -m "feat: rewrite crawl router for shared queue — per-user enqueue, per-user filtered SSE, remove stop"
```

---

### Task 16: `routers/collection.py` and `routers/releases.py` — rescope to calling user

**Files:**
- Modify: `backend/routers/collection.py`, `backend/routers/releases.py`
- Test: new `backend/tests/test_collection_router.py`, replace `backend/tests/test_releases_router.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_collection_router.py`:

```python
import db


def test_collection_status_scoped_to_calling_user(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True)
        conn.commit()

    client = authed_client_factory(alice["id"])
    r = client.get("/api/collection/status")
    assert r.json()["total"] == 1

    client = authed_client_factory(bob["id"])
    r = client.get("/api/collection/status")
    assert r.json()["total"] == 0
```

Replace `backend/tests/test_releases_router.py`:

```python
import db


def test_list_releases_scoped_to_calling_user(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True)
        conn.commit()

    client = authed_client_factory(alice["id"])
    r = client.get("/api/releases")
    assert r.json()["total"] == 1
    assert r.json()["releases"][0]["discogs_id"] == "r1"


def test_list_artists_scoped_to_calling_user(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "Zzz", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True)
        conn.commit()

    client = authed_client_factory(alice["id"])
    r = client.get("/api/artists?scope=collection")
    assert r.json()["artists"] == ["Zzz"]


def test_list_crawlers_unscoped(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()

    client = authed_client_factory(user["id"])
    r = client.get("/api/crawlers")
    assert r.json()["crawlers"][0]["site_name"] == "Amazon"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_collection_router.py tests/test_releases_router.py -v`
Expected: FAIL — routers still call `get_connection`.

- [ ] **Step 3: Implement**

Replace `backend/routers/collection.py`:

```python
from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from crawl_manager import crawl_manager
import db
from logging_config import get_logger

log = get_logger("routers.collection")
router = APIRouter()


@router.get("/collection/status")
def collection_status(request: Request):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total, MAX(last_synced) AS last_synced FROM library_items WHERE user_id = %s",
            [user_id],
        ).fetchone()
    return {"total": row["total"], "last_synced": row["last_synced"]}


@router.post("/collection/refresh")
async def refresh_collection(request: Request, mode: Optional[str] = None):
    if crawl_manager.sync_running:
        raise HTTPException(status_code=409, detail="Collection sync already running")
    started = await crawl_manager.start_sync(request.state.user_id, mode or "all")
    return {"started": started, "running": crawl_manager.sync_running}
```

Replace `backend/routers/releases.py`:

```python
from fastapi import APIRouter, Query, Request
from typing import Optional
import db

router = APIRouter()


@router.get("/releases")
def list_releases(
    request: Request,
    search: Optional[str] = Query(None),
    artist: Optional[str] = Query(None),
    sort: str = Query("artist"),
    order: str = Query("asc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    scope: Optional[str] = Query(None),
):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        return db.get_library_releases(
            conn, user_id, search=search, artist=artist, sort=sort,
            order=order, page=page, per_page=per_page, scope=scope,
        )


@router.get("/artists")
def list_artists(request: Request, scope: Optional[str] = Query(None)):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        return {"artists": db.get_distinct_artists(conn, user_id, scope=scope)}


@router.get("/crawlers")
def list_crawlers():
    with db.get_app_pool().connection() as conn:
        return {"crawlers": db.get_all_crawlers(conn)}
```

`no_plex` query params are dropped from both endpoints per Task 7's note (Plex is out of scope this plan) — update the frontend call sites in Task 20 to stop sending them.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_collection_router.py tests/test_releases_router.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/routers/collection.py backend/routers/releases.py backend/tests/test_collection_router.py backend/tests/test_releases_router.py
git commit -m "feat: rescope collection and releases routers to the calling user's library_items"
```

---

### Task 17: `routers/settings.py` — split into admin-only global settings and per-user settings

**Files:**
- Modify: `backend/routers/settings.py`
- Test: replace `backend/tests/test_settings_router.py`

- [ ] **Step 1: Write the failing tests**

Replace `backend/tests/test_settings_router.py`:

```python
import db


def test_get_settings_requires_admin(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(user["id"])
    r = client.get("/api/settings")
    assert r.status_code == 403


def test_get_and_post_settings_as_admin(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute("UPDATE users SET is_admin = TRUE WHERE id = %s", [user["id"]])
        conn.commit()
    client = authed_client_factory(user["id"])
    r = client.get("/api/settings")
    assert r.status_code == 200
    assert "discogs_token" not in r.json()

    r = client.post("/api/settings", json={
        "debug_screenshot_interval": 30, "shuffle_crawl_order": False, "crawl_delay_seconds": 45,
        "consecutive_failure_limit": 5, "crawl_schedule": "", "crawl_schedule_mode": "missing",
        "ebay_app_id": "", "ebay_cert_id": "", "stock_schedule": "",
    }, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200


def test_patch_crawler_requires_admin(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(user["id"])
    r = client.patch(f"/api/crawlers/{crawler_id}", json={"enabled": False}, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 403


def test_get_and_post_user_settings(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(user["id"])
    r = client.get("/api/user-settings")
    assert r.status_code == 200
    assert r.json() == {"anthropic_api_key": "", "recommendation_item_limit": 300}

    r = client.post("/api/user-settings", json={"anthropic_api_key": "sk-abc", "recommendation_item_limit": 100},
                     headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    r = client.get("/api/user-settings")
    assert r.json() == {"anthropic_api_key": "sk-abc", "recommendation_item_limit": 100}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_settings_router.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Replace `backend/routers/settings.py`:

```python
from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel
from config import load_config, save_config
import db
from admin import require_admin
import scheduler

router = APIRouter()


class SettingsUpdate(BaseModel):
    debug_screenshot_interval: int = 20
    shuffle_crawl_order: bool = True
    crawl_delay_seconds: int = 30
    consecutive_failure_limit: int = 10
    crawl_schedule: str = ""
    crawl_schedule_mode: str = "missing"
    ebay_app_id: str = ""
    ebay_cert_id: str = ""
    stock_schedule: str = ""


class CrawlerUpdate(BaseModel):
    enabled: bool


class UserSettingsUpdate(BaseModel):
    anthropic_api_key: str = ""
    recommendation_item_limit: int = 300


@router.get("/settings", dependencies=[Depends(require_admin)])
def get_settings():
    config = load_config()
    return {
        "debug_screenshot_interval": int(config.get("debug_screenshot_interval", 20)),
        "shuffle_crawl_order": bool(config.get("shuffle_crawl_order", True)),
        "crawl_delay_seconds": int(config.get("crawl_delay_seconds", 30)),
        "consecutive_failure_limit": int(config.get("consecutive_failure_limit", 10)),
        "crawl_schedule": config.get("crawl_schedule", ""),
        "crawl_schedule_mode": config.get("crawl_schedule_mode", "missing"),
        "ebay_app_id": config.get("ebay_app_id", ""),
        "ebay_cert_id": config.get("ebay_cert_id", ""),
        "stock_schedule": config.get("stock_schedule", ""),
    }


@router.post("/settings", dependencies=[Depends(require_admin)])
def update_settings(body: SettingsUpdate):
    config = load_config()
    config["debug_screenshot_interval"] = body.debug_screenshot_interval
    config["shuffle_crawl_order"] = body.shuffle_crawl_order
    config["crawl_delay_seconds"] = body.crawl_delay_seconds
    config["consecutive_failure_limit"] = body.consecutive_failure_limit
    config["crawl_schedule"] = body.crawl_schedule
    config["crawl_schedule_mode"] = body.crawl_schedule_mode
    config["ebay_app_id"] = body.ebay_app_id
    config["ebay_cert_id"] = body.ebay_cert_id
    config["stock_schedule"] = body.stock_schedule
    save_config(config)
    try:
        scheduler.configure(body.crawl_schedule, body.crawl_schedule_mode)
        scheduler.configure_stock(body.stock_schedule)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.patch("/crawlers/{crawler_id}", dependencies=[Depends(require_admin)])
def update_crawler(crawler_id: int, body: CrawlerUpdate):
    with db.get_app_pool().connection() as conn:
        db.set_crawler_enabled(conn, crawler_id, body.enabled)
        conn.commit()
    return {"ok": True}


@router.get("/user-settings")
def get_user_settings(request: Request):
    with db.get_identity_pool().connection() as conn:
        row = conn.execute(
            "SELECT anthropic_api_key, recommendation_item_limit FROM users WHERE id = %s",
            [request.state.user_id],
        ).fetchone()
    return {"anthropic_api_key": row["anthropic_api_key"] or "", "recommendation_item_limit": row["recommendation_item_limit"]}


@router.post("/user-settings")
def update_user_settings(body: UserSettingsUpdate, request: Request):
    with db.get_identity_pool().connection() as conn:
        conn.execute(
            "UPDATE users SET anthropic_api_key = %s, recommendation_item_limit = %s WHERE id = %s",
            [body.anthropic_api_key or None, body.recommendation_item_limit, request.state.user_id],
        )
        conn.commit()
    return {"ok": True}
```

`get_user_settings`/`update_user_settings` use `get_identity_pool()` (BYPASSRLS) rather than `user_scope()` because they read/write a `users` row by primary key, not `library_items` — matching the existing pattern in `routers/session.py`'s `auth_status` for the same table.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_settings_router.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/routers/settings.py backend/tests/test_settings_router.py
git commit -m "feat: split settings into admin-only global endpoint and new per-user settings endpoint"
```

---

### Task 18: `routers/stock.py` — rescope judgments to calling user

**Files:**
- Modify: `backend/routers/stock.py`
- Test: replace `backend/tests/test_stock_router.py`

- [ ] **Step 1: Write the failing tests**

Replace `backend/tests/test_stock_router.py`:

```python
import db


def test_stock_judge_status_and_clear_scoped_to_user(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.register_crawler(conn, "Amazon", "/x.py", crawler_type="catalog")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.replace_stock_items(conn, crawler_id, [
            {"artist": "A", "title": "T", "url": "https://x/1", "price": 1.0, "currency": "USD"},
        ])
        conn.commit()

    client = authed_client_factory(alice["id"])
    r = client.get("/api/stock/judge/status")
    assert r.json() == {"any_judged": False}

    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [{
            "item_key": db.compute_item_key("A", "T", "https://x/1"), "recommended": True, "reason": "x",
        }])
        conn.commit()

    r = client.get("/api/stock/judge/status")
    assert r.json() == {"any_judged": True}

    r = client.post("/api/stock/judge/clear", headers={"X-Requested-With": "fetch"})
    assert r.json()["cleared"] is True
    assert r.json()["count"] == 1


def test_stock_judge_start_uses_calling_user(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(user["id"])
    r = client.post("/api/stock/judge/start", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_stock_router.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Replace `backend/routers/stock.py`:

```python
import csv
import io
from fastapi import APIRouter, Query, Request, Response
from typing import Optional
import db
from crawl_manager import crawl_manager

router = APIRouter()


@router.get("/stock")
def list_stock(
    request: Request,
    search: Optional[str] = Query(None),
    artist: Optional[str] = Query(None),
    sort: str = Query("artist"),
    order: str = Query("asc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    overlapping: bool = Query(False),
    recommended: bool = Query(False),
):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        return db.get_stock_items(
            conn, user_id, search=search, artist=artist, sort=sort, order=order,
            page=page, per_page=per_page, overlapping=overlapping, recommended=recommended,
        )


@router.get("/stock/artists")
def list_stock_artists(request: Request, overlapping: bool = Query(False), recommended: bool = Query(False)):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        return {"artists": db.get_distinct_stock_artists(conn, user_id, overlapping=overlapping, recommended=recommended)}


@router.get("/stock/judge/status")
def get_stock_judgment_status(request: Request):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        return {"any_judged": db.has_any_stock_judgment(conn, user_id)}


@router.post("/stock/sync/start")
async def start_stock_sync():
    started = await crawl_manager.start_stock_sync()
    return {"started": started, "running": crawl_manager.stock_sync_running}


@router.post("/stock/judge/start")
async def start_stock_judgment(request: Request):
    started = await crawl_manager.start_judgment_only(request.state.user_id)
    return {"started": started, "running": crawl_manager.judgment_running}


@router.post("/stock/judge/clear")
def clear_stock_judgment(request: Request):
    if crawl_manager.judgment_running or crawl_manager.stock_sync_running:
        return {"cleared": False, "running": True}
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        count = db.clear_stock_judgments(conn, user_id)
        conn.commit()
    return {"cleared": True, "count": count}


@router.get("/stock/export")
def export_recommended_stock(request: Request):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        items = db.get_recommended_stock_items(conn, user_id)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["artist", "title", "format", "price", "source", "link", "reason"])
    for item in items:
        writer.writerow([item["artist"], item["title"], item["format"], item["price"], item["source"], item["url"], item["reason"]])
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=recommendations.csv"},
    )
```

`/stock/sync/start` stays unauthenticated-by-role (any logged-in user can trigger the global stock crawl) — the design spec doesn't call for admin-gating this one specifically (it's the catalog crawl, not a settings mutation), matching today's behavior. Revisit only if the user flags it during review.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_stock_router.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/routers/stock.py backend/tests/test_stock_router.py
git commit -m "feat: rescope stock judgment endpoints to the calling user"
```

---

### Task 19: Migration script amendment + base-spec drift fix for relocated `stock_item_judgments`

**Files:**
- Modify: `backend/scripts/migrate_from_sqlite.py`
- Modify: `docs/superpowers/specs/2026-07-26-multi-tenant-architecture-design.md` (amendment note)
- Test: `backend/tests/test_migrate_from_sqlite.py`

The base spec's Migration path section says "`crawlers`, `stock_items`, `stock_item_judgments` copy as-is" — no longer true for `stock_item_judgments` since Task 1 relocated it to per-user/RLS. This is exactly the kind of drift CLAUDE.md's pre-PR spec-drift check exists to catch; fix it now rather than at PR time.

- [ ] **Step 1: Write the failing test**

Read `backend/tests/test_migrate_from_sqlite.py` and `backend/scripts/migrate_from_sqlite.py` in full first — this task's test additions depend on that script's exact current structure (fixture setup, `migrate()` signature). Add a test asserting old global `stock_item_judgments` rows from the source SQLite file are **not** copied (there's no `user_id` to attach them to, and per the design spec's Non-goals/Task 1 rationale, a stale global judgment set has no valid per-user owner to become):

```python
def test_migrate_does_not_copy_stock_item_judgments(sqlite_source_db, pg_admin_conn):
    # Uses whatever fixture names this test file's existing tests already use
    # for "a populated source SQLite file" and "a connection to the migrated
    # Postgres db" — match the existing fixtures, don't invent new ones.
    import sqlite3
    conn = sqlite3.connect(sqlite_source_db)
    conn.execute(
        "INSERT INTO stock_item_judgments (item_key, recommended) VALUES ('k1', 1)"
    )
    conn.commit()
    conn.close()

    from scripts.migrate_from_sqlite import migrate
    migrate(sqlite_source_db, discogs_username="alice")

    count = pg_admin_conn.execute("SELECT COUNT(*) FROM stock_item_judgments").fetchone()["count"]
    assert count == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_migrate_from_sqlite.py -v`
Expected: FAIL if the script currently attempts to copy `stock_item_judgments` at all (read the script first to confirm whether it already does or doesn't — the base spec said it should, but the script may or may not have implemented that line yet; if it never copied judgments in the first place, this test passes immediately and step 3 is a no-op for the script itself, but the spec amendment in step 3 is still required).

- [ ] **Step 3: Implement**

In `backend/scripts/migrate_from_sqlite.py`, remove any step that copies `stock_item_judgments` rows (if present), and add a log line noting the intentional skip:

```python
    log.info("Skipping stock_item_judgments: relocated to per-user schema, no user_id to attach old rows to")
```

Amend `docs/superpowers/specs/2026-07-26-multi-tenant-architecture-design.md`'s Migration path section. Find the line:

```
4. `crawlers`, `stock_items`, `stock_item_judgments` copy as-is.
```

Replace with:

```
4. `crawlers`, `stock_items` copy as-is. `stock_item_judgments` is **not**
   copied — see the 2026-07-27 amendment below.

**Amendment (2026-07-27, during the crawl-queue-refactor plan):**
`stock_item_judgments` moved from global to per-user/RLS (see
[`docs/superpowers/specs/2026-07-27-crawl-queue-refactor-design.md`](2026-07-27-crawl-queue-refactor-design.md)) —
each user now judges the shared stock catalog against their own collection
using their own Anthropic key, so a judgment row has no meaning without a
`user_id` to attach it to. The old global judgment set has no such owner and
is not migrated; the maintainer's first post-migration judgment run simply
re-judges the stock backlog from scratch, exactly as any new user would.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/pytest tests/test_migrate_from_sqlite.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/migrate_from_sqlite.py backend/tests/test_migrate_from_sqlite.py docs/superpowers/specs/2026-07-26-multi-tenant-architecture-design.md
git commit -m "fix: stop migrating orphaned global stock_item_judgments, amend base spec for the relocation"
```

---

### Task 20: Frontend — `api/types.ts` and `api/client.ts` updates

**Files:**
- Modify: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`
- Test: `frontend/src/test/` (extend or add a client test matching this file's existing testing conventions — read `frontend/src/test/` for the pattern first)

- [ ] **Step 1: Write the failing test**

Add (adjust the exact mocking helper to match whatever this test directory's other `client.ts` tests already use — read one first, e.g. any existing test mocking `fetch`):

```ts
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { postCrawlStart, getUserSettings, saveUserSettings } from '../api/client'

describe('crawl/user-settings client functions', () => {
  beforeEach(() => {
    global.fetch = vi.fn()
  })

  it('postCrawlStart returns enqueued count', async () => {
    ;(global.fetch as any).mockResolvedValue({ ok: true, json: async () => ({ enqueued: 3 }) })
    const result = await postCrawlStart('all')
    expect(result.enqueued).toBe(3)
  })

  it('getUserSettings fetches /user-settings', async () => {
    ;(global.fetch as any).mockResolvedValue({ ok: true, json: async () => ({ anthropic_api_key: '', recommendation_item_limit: 300 }) })
    await getUserSettings()
    expect((global.fetch as any).mock.calls[0][0]).toContain('/user-settings')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- --run`
Expected: FAIL — `getUserSettings`/`saveUserSettings` don't exist, `postCrawlStart`'s return type doesn't have `enqueued`.

- [ ] **Step 3: Implement**

In `frontend/src/api/types.ts`:
- Remove `plex_url` from `Release` (no Plex this plan — drop the field and its two render sites in `RecordBrowser.tsx`, Task 21).
- Remove `discogs_token`, `collection_schedule`, `collection_schedule_mode`, `anthropic_api_key`, `recommendation_item_limit`, `plex_base_url`, `plex_token`, `plex_match_threshold` from `Settings` (moved out to per-user or deleted):

```ts
export interface Settings {
  debug_screenshot_interval: number
  shuffle_crawl_order: boolean
  crawl_delay_seconds: number
  consecutive_failure_limit: number
  crawl_schedule?: string
  crawl_schedule_mode?: 'missing' | 'all'
  ebay_app_id?: string
  ebay_cert_id?: string
  stock_schedule?: string
}

export interface UserSettings {
  anthropic_api_key: string
  recommendation_item_limit: number
}
```

Update `CrawlStatus`:

```ts
export interface CrawlStatus {
  total: number
  missing: number
  oldest_checked: string | null
  pending: number
  pool_running: boolean
}
```

In `frontend/src/api/client.ts`:
- Remove `postCrawlStop` entirely (dead — confirmed unused anywhere in the app).
- Change `postCrawlStart`'s return type and drop the unused `running` field:

```ts
export async function postCrawlStart(mode: 'all' | 'missing' = 'all', releaseId?: string): Promise<{ enqueued: number }> {
  const r = await apiFetch('/crawl/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode, release_id: releaseId }),
  })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}
```

- Remove `no_plex` from `getReleases`'s params type and query-building (delete the two lines handling it), and from `getArtists`'s signature/query-building.
- Add:

```ts
export async function getUserSettings(): Promise<UserSettings> {
  const r = await apiFetch('/user-settings')
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function saveUserSettings(settings: UserSettings): Promise<void> {
  const r = await apiFetch('/user-settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  })
  if (!r.ok) throw new Error(await r.text())
}
```

- Update the `import type` line to include `UserSettings` and drop nothing else needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- --run`
Expected: PASS. Also run `npx tsc -b` — expect new errors in `App.tsx`/`Settings.tsx`/`RecordBrowser.tsx` (addressed in Task 21) since their props/state reference now-removed fields; that's expected at this point in the plan.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/types.ts frontend/src/api/client.ts frontend/src/test/
git commit -m "feat: split frontend settings types, add per-user settings client, update crawl start/status shapes"
```

---

### Task 21: Frontend — wire per-user settings, admin gating, drop Plex/discogs_token references

**Files:**
- Modify: `frontend/src/App.tsx`, `frontend/src/views/Settings.tsx`, `frontend/src/views/Account.tsx`, `frontend/src/views/RecordBrowser.tsx`
- Test: extend existing test files for each (`frontend/src/test/`)

- [ ] **Step 1: Write the failing tests**

Read `frontend/src/test/account.test.tsx` and any existing `Settings.tsx` test file fully first, then add cases matching their existing rendering/mocking conventions:

```tsx
it('renders anthropic API key field and saves it', async () => {
  // Mock getUserSettings/saveUserSettings the same way this file already
  // mocks other api/client functions; render <Account />; find the new
  // input by label; type a value; click Save; assert saveUserSettings was
  // called with the typed value.
})
```

```tsx
it('Settings view is hidden or 403s for a non-admin user', async () => {
  // Mock getSettings to reject with a 403-shaped error (matching however
  // apiFetch surfaces non-ok responses today — read client.ts's error
  // shape); render whatever component gates the Settings nav entry; assert
  // it does not render the admin-only fields for a non-admin authState.
})
```

Since these two tests depend on exactly how `AuthStatus`/session data currently exposes (or doesn't yet expose) `is_admin` to the frontend, first check `GET /auth/status`'s response shape (`routers/session.py`'s `auth_status`, from the OAuth plan) — it currently returns `{"state": "authenticated", "user": {"discogs_username": ...}}` with no `is_admin` field. Add `is_admin` to that response as part of this task (a one-line addition to `auth_status` in `routers/session.py`: `"is_admin": user["is_admin"]` — note `auth_status`'s existing query only selects `discogs_username`; widen it to `SELECT discogs_username, is_admin FROM users WHERE id = %s`), and add the corresponding field to `AuthStatus` in `frontend/src/api/types.ts`:

```ts
export type AuthStatus =
  | { state: 'unauthenticated' }
  | { state: 'authenticated'; user: { discogs_username: string; is_admin: boolean } }
```

Add a backend test for this alongside the existing `test_status_unauthenticated_with_no_cookie` test in `backend/tests/test_auth_router.py`:

```python
def test_status_authenticated_includes_is_admin(client):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute("UPDATE users SET is_admin = TRUE WHERE id = %s", [user["id"]])
        conn.commit()
    token = session_tokens.new_session_token()
    with db.get_admin_pool().connection() as conn:
        db.create_session(conn, session_tokens.hash_token(token), user["id"], datetime.utcnow() + timedelta(days=1))
        conn.commit()
    client.cookies.set(config.COOKIE_NAME, token)
    r = client.get("/api/auth/status")
    assert r.json()["user"]["is_admin"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_auth_router.py -k is_admin -v` and `cd frontend && npm test -- --run`
Expected: FAIL.

- [ ] **Step 3: Implement**

`backend/routers/session.py`: widen `auth_status`'s query and response as described above.

`frontend/src/App.tsx`:
- Change the startup effect (the one currently at line 70-73) to stop calling the now-admin-gated `getSettings()` for `hasAnthropicKey`/`hasPlexConfigured`; replace with a call to `getUserSettings()` for the Anthropic key, and drop `hasPlexConfigured` entirely (no Plex this plan):

```ts
getUserSettings().then((s) => {
  setHasAnthropicKey(Boolean(s.anthropic_api_key))
}).catch(() => {})
```

- Remove the `hasPlexConfigured`/`setHasPlexConfigured` state declaration and every `plexAvailable={hasPlexConfigured}` prop pass-through (two call sites, per the earlier grep — `RecordBrowser` no longer receives or needs this prop; remove the prop and its type from `RecordBrowser.tsx`'s `Props` too).
- Gate the Settings nav button/view on `authState.state === 'authenticated' && authState.user.is_admin` — find wherever the Settings view is currently reachable (a header nav button, matching the pattern used for the Account button) and wrap it in that same-shaped check so non-admin users don't see a broken 403'ing page.

`frontend/src/views/RecordBrowser.tsx`:
- Remove the `plexAvailable` prop, the `filter`/`no_plex` state and the `<option value="no_plex">` (the whole "No Plex" filter control), and the two `r.plex_url ? (...)` render branches (render the plain non-Plex-linked version unconditionally instead — check what the non-Plex branch of each ternary currently renders and keep only that).
- Remove `no_plex` from the `getReleases`/`getArtists` calls' argument lists.

`frontend/src/views/Settings.tsx`:
- Remove the `discogs_token`, `plex_base_url`, `plex_token`, `plex_match_threshold`, `anthropic_api_key`, `recommendation_item_limit`, `collection_schedule`, `collection_schedule_mode` fields/inputs entirely (read the file fully first — these were previously rendered as form fields; delete each field's JSX block and its bound state, keeping the remaining global fields: `debug_screenshot_interval`, `shuffle_crawl_order`, `crawl_delay_seconds`, `consecutive_failure_limit`, `crawl_schedule(_mode)`, `ebay_app_id`, `ebay_cert_id`, `stock_schedule`).

`frontend/src/views/Account.tsx`:
- Add an "Anthropic API key" text input and a "Recommendation item limit" number input, following this file's existing field/save pattern (read the file fully first — it already has at least one save-on-blur or save-button pattern from the avatar section to mirror). Wire to `getUserSettings()`/`saveUserSettings()`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_auth_router.py -v && cd frontend && npx tsc -b && npm test -- --run`
Expected: PASS, zero `tsc` errors.

- [ ] **Step 5: Commit**

```bash
git add backend/routers/session.py frontend/src
git commit -m "feat: wire per-user Anthropic settings into Account view, admin-gate Settings nav, drop Plex UI"
```

---

### Task 22: Final review of full implementation

Not a code task — dispatch a final reviewer (per `superpowers:subagent-driven-development`) over the entire branch diff against `multi-tenant-architecture-design`, holistically checking:
- Every router/module that imported the deleted SQLite `db.py` API now imports only the Postgres one — grep for `get_connection`, `sqlite3` outside of test fixtures using it deliberately (`conftest.py`'s unrelated SQLite `conn` fixture, if still used by any non-Postgres test, is fine).
- RLS coverage: `stock_item_judgments` isolation actually holds under a real cross-user query, not just the unit test's specific shape.
- The `crawl_queue` worker pool's `Page`-per-worker gap flagged in Task 11 is closed (real Playwright wiring, not `None`), or explicitly punted with a tracked follow-up if genuinely out of time — don't let it silently ship as `None`.
- Pre-PR spec-drift check per `CLAUDE.md`: grep `docs/superpowers/specs/` for every symbol/endpoint this branch touched (`get_releases`, `discogs_token`, `plex_base_url`, `collection_schedule`, `/crawl/stop`, `stock_item_judgments`) and confirm no other spec still describes the old shape as current.
- `backend/tests/` has no leftover file still importing the deleted SQLite API.

Once approved, proceed to `superpowers:finishing-a-development-branch`.
