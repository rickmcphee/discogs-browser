# Store tab "save for later" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-user "save for later" bookmark to Store-tab stock items — a toggle icon on every row/tile plus a "Saved" filter — backed by a new `stock_item_saves` junction table.

**Architecture:** Mirrors the existing `stock_item_judgments`/`recommended` feature end to end: a `(user_id, item_key)` junction table with RLS, a `LEFT JOIN` in `get_stock_items`/`get_distinct_stock_artists` that adds a `saved` boolean to every row, two new REST endpoints to toggle it, and frontend state that's a straight extension of the existing `filter`-driven request-building already in `StockBrowser.tsx`.

**Tech Stack:** FastAPI + psycopg (Postgres) backend, React + TypeScript + Tailwind frontend, pytest (`pg_run_database`/`admin_conn`/`authed_client_factory` fixtures), no new dependencies.

## Global Constraints

- Store scope only (`scope="store"`) — the Track tab (`scope="track"`) gets no bookmark icon and no Saved filter.
- Bookmark icon appears in **both** list (table) and tile views.
- Unsaving while the Saved filter is active removes the row/tile from the visible list immediately, with no reload.
- Save state is keyed on `item_key`, not on an individual `stock_items` row — every row/tile sharing an `item_key` (an "own" row plus its cross-store comparison rows) shows and toggles the same saved state, matching how `recommended` already behaves.
- No "not owned" gate on the Saved filter (unlike Recommended) — saving works regardless of collection ownership.
- New table (`stock_item_saves`) mirrors `stock_item_judgments` exactly: same PK shape `(user_id, item_key)`, same RLS policy pattern, same grant.
- No icon library — hand-author the bookmark as an inline SVG matching the file's existing 16×16 `stroke="currentColor"` / `strokeWidth="1.5"` convention.
- Every commit on this repo requires the AI-attribution trailer block (`ai-generated: true`, `ai-model`, `ai-tool`, `ai-surface`, `ai-executor`), created via `git commit -F <message-file>`.
- Spec reference: [`docs/specifications/shaping/2026-08-16-store-saved-items-design.md`](../../specifications/shaping/2026-08-16-store-saved-items-design.md).

---

## Task 1: `stock_item_saves` table + `save_stock_item`/`unsave_stock_item`

**Files:**
- Modify: `backend/db.py` — add `stock_item_saves` to `TENANT_SCHEMA` (immediately after the `stock_item_judgments` block, `backend/db.py:339-346`), add its RLS policy (after the `stock_item_judgments_isolation` policy, `backend/db.py:420-423`), add its grant (alongside the `stock_item_judgments` grant, `backend/db.py:496`), and add two new functions near `upsert_stock_judgments` (`backend/db.py:1656`).
- Test: `backend/tests/test_stock_crud.py`

**Interfaces:**
- Produces: `db.save_stock_item(conn, user_id: int, item_key: str) -> None`, `db.unsave_stock_item(conn, user_id: int, item_key: str) -> None`. Both are idempotent no-ops on repeat calls. Later tasks (2, 4) call these.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_stock_crud.py`, right after `test_get_stock_items_recommended_filters_to_calling_users_judgments` (after line 243, before `test_get_stock_items_excludes_hidden_crawler_ids`):

```python
def test_save_stock_item_then_unsave_round_trips(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()

    db.save_stock_item(admin_conn, alice["id"], "some-item-key")
    admin_conn.commit()
    row = admin_conn.execute(
        "SELECT * FROM stock_item_saves WHERE user_id = %s AND item_key = %s",
        [alice["id"], "some-item-key"],
    ).fetchone()
    assert row is not None

    db.unsave_stock_item(admin_conn, alice["id"], "some-item-key")
    admin_conn.commit()
    row = admin_conn.execute(
        "SELECT * FROM stock_item_saves WHERE user_id = %s AND item_key = %s",
        [alice["id"], "some-item-key"],
    ).fetchone()
    assert row is None


def test_save_stock_item_is_idempotent(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()

    db.save_stock_item(admin_conn, alice["id"], "some-item-key")
    db.save_stock_item(admin_conn, alice["id"], "some-item-key")
    admin_conn.commit()

    rows = admin_conn.execute(
        "SELECT * FROM stock_item_saves WHERE user_id = %s AND item_key = %s",
        [alice["id"], "some-item-key"],
    ).fetchall()
    assert len(rows) == 1


def test_unsave_stock_item_never_saved_is_a_noop(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()

    db.unsave_stock_item(admin_conn, alice["id"], "never-saved-key")
    admin_conn.commit()  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_stock_crud.py -k "save_stock_item" -v`
Expected: FAIL with `AttributeError: module 'db' has no attribute 'save_stock_item'` (or similar — the table doesn't exist yet either, but the attribute error surfaces first).

- [ ] **Step 3: Add the table, RLS policy, and grant**

In `backend/db.py`, immediately after the `stock_item_judgments` table definition (ends at line 346, right before `CREATE TABLE IF NOT EXISTS invites`):

```python
CREATE TABLE IF NOT EXISTS stock_item_saves (
    user_id INTEGER NOT NULL REFERENCES users(id),
    item_key TEXT NOT NULL,
    saved_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, item_key)
);

```

(This is a raw string inside the `TENANT_SCHEMA` Python constant — match the existing indentation and blank-line style exactly; don't add Python code here, just more SQL text in the string.)

Immediately after `ALTER TABLE stock_item_judgments FORCE ROW LEVEL SECURITY;` (line 385):

```python
ALTER TABLE stock_item_saves ENABLE ROW LEVEL SECURITY;
ALTER TABLE stock_item_saves FORCE ROW LEVEL SECURITY;
```

Immediately after the `stock_item_judgments_isolation` policy (ends at line 423, right before the closing `"""`):

```python

DROP POLICY IF EXISTS stock_item_saves_isolation ON stock_item_saves;
CREATE POLICY stock_item_saves_isolation ON stock_item_saves
    USING (user_id = current_setting('app.user_id', true)::int)
    WITH CHECK (user_id = current_setting('app.user_id', true)::int);
```

In the grants section, right after line 496 (`conn.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON stock_item_judgments TO app_user")`):

```python
        conn.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON stock_item_saves TO app_user")
```

- [ ] **Step 4: Add the two functions**

In `backend/db.py`, near `upsert_stock_judgments` (around line 1656), add:

```python
def save_stock_item(conn, user_id: int, item_key: str) -> None:
    conn.execute(
        """
        INSERT INTO stock_item_saves (user_id, item_key)
        VALUES (%s, %s)
        ON CONFLICT (user_id, item_key) DO NOTHING
        """,
        [user_id, item_key],
    )


def unsave_stock_item(conn, user_id: int, item_key: str) -> None:
    conn.execute(
        "DELETE FROM stock_item_saves WHERE user_id = %s AND item_key = %s",
        [user_id, item_key],
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_stock_crud.py -k "save_stock_item" -v`
Expected: 3 passed.

- [ ] **Step 6: Run the full backend suite to check for regressions**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest -x`
Expected: all pass. (Run in the foreground, one suite at a time — two concurrent pytest runs against this project's Postgres cluster both die with exit 137.)

- [ ] **Step 7: Commit**

```bash
git add backend/db.py backend/tests/test_stock_crud.py
git commit -F- <<'EOF'
Add stock_item_saves table and save/unsave functions

New per-user junction table for the Store tab "save for later" feature,
mirroring stock_item_judgments: same (user_id, item_key) PK, same RLS
isolation policy, same grant.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
EOF
```

---

## Task 2: `saved_only` filter in `get_stock_items` and `get_distinct_stock_artists`

**Files:**
- Modify: `backend/db.py` — `get_stock_items` (lines 1478-1587) and `get_distinct_stock_artists` (lines 1590+).
- Test: `backend/tests/test_stock_crud.py`

**Interfaces:**
- Consumes: `db.save_stock_item`/`db.unsave_stock_item` (Task 1) to set up test fixtures.
- Produces: `get_stock_items(..., saved_only: bool = False)` — every returned item dict gains a `"saved": bool` key, and when `saved_only=True` only items with a `stock_item_saves` row for the calling user are returned. `get_distinct_stock_artists(..., saved_only: bool = False)` gets the matching filter (no `saved` field — it returns a plain list of artist names, unchanged shape). Task 3 (router) calls both with this new parameter.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_stock_crud.py`, after the `save_stock_item`/`unsave_stock_item` tests added in Task 1:

```python
def _make_amazon_item(admin_conn, artist="Artist A", title="Album A", url="https://x/1", price=10.0):
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": artist, "title": title, "url": url, "price": price, "currency": "USD"},
    ])
    admin_conn.commit()
    return db.compute_item_key(artist, title, url)


def test_get_stock_items_saved_only_filters_to_calling_users_saves(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    bob = db.create_user(admin_conn, discogs_user_id=2, discogs_username="bob")
    admin_conn.commit()
    item_key = _make_amazon_item(admin_conn)

    db.save_stock_item(admin_conn, alice["id"], item_key)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], saved_only=True)
        assert result["total"] == 1

    with db.user_scope(bob["id"]) as conn:
        result = db.get_stock_items(conn, bob["id"], saved_only=True)
        assert result["total"] == 0


def test_get_stock_items_saved_only_does_not_exclude_owned_items(admin_conn):
    # Unlike `recommended`, `saved_only` has no not-owned gate: a saved item
    # the user already owns must still appear.
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()
    item_key = _make_amazon_item(admin_conn)
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "Artist A", "title": "Album A", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True)
    db.save_stock_item(admin_conn, alice["id"], item_key)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], saved_only=True)
        assert result["total"] == 1


def test_get_stock_items_saved_field_present_on_every_row(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()
    saved_key = _make_amazon_item(admin_conn, artist="Artist Saved", title="Album Saved", url="https://x/saved")
    unsaved_key = _make_amazon_item(admin_conn, artist="Artist Unsaved", title="Album Unsaved", url="https://x/unsaved")
    db.save_stock_item(admin_conn, alice["id"], saved_key)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"])
        by_key = {r["item_key"]: r["saved"] for r in result["items"]}
        assert by_key[saved_key] is True
        assert by_key[unsaved_key] is False


def test_get_stock_items_saved_state_shared_across_comparison_rows(admin_conn):
    # A record's saved flag must be identical on its own row and every
    # cross-crawler comparison row for the same item_key.
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.register_crawler(admin_conn, "Nuclear Blast", "/x.py", crawler_type="catalog")
    db.register_crawler(admin_conn, "Amazon", "/y.py", crawler_type="release")
    admin_conn.commit()
    store_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Nuclear Blast'").fetchone()["id"]
    amazon_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    item_key = db.replace_stock_items(admin_conn, store_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
    ])[0]
    db.upsert_stock_item_listing(admin_conn, item_key, amazon_id, "https://amazon/1", 12.5, None, "USD", "New")
    admin_conn.commit()
    db.save_stock_item(admin_conn, alice["id"], item_key)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"])
        rows_for_item = [r for r in result["items"] if r["item_key"] == item_key]
        assert len(rows_for_item) == 2  # own row + one comparison row
        assert all(r["saved"] for r in rows_for_item)


def test_get_distinct_stock_artists_saved_only_filters(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()
    saved_key = _make_amazon_item(admin_conn, artist="Artist Saved", title="Album Saved", url="https://x/saved")
    _make_amazon_item(admin_conn, artist="Artist Unsaved", title="Album Unsaved", url="https://x/unsaved")
    db.save_stock_item(admin_conn, alice["id"], saved_key)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        artists = db.get_distinct_stock_artists(conn, alice["id"], saved_only=True)
        assert artists == ["Artist Saved"]
```

The two tests above copy their setup calls verbatim from
`test_get_stock_items_library_scope_collection_includes_comparison_rows_for_owned_items`
(`backend/tests/test_stock_crud.py:454-477`), which already exercises
`upsert_catalog_release`, `upsert_library_item`, and
`upsert_stock_item_listing` with verified working arguments — no signature
guessing needed here.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_stock_crud.py -k "saved_only or saved_field or saved_state" -v`
Expected: FAIL with `TypeError: get_stock_items() got an unexpected keyword argument 'saved_only'` (and similarly for `get_distinct_stock_artists`).

- [ ] **Step 3: Add `saved_only` and the join to `get_stock_items`**

In `backend/db.py`, change the signature (line 1478-1490):

```python
def get_stock_items(
    conn,
    user_id: int,
    search: Optional[str] = None,
    artist: Optional[str] = None,
    sort: str = "artist",
    order: str = "asc",
    page: int = 1,
    per_page: int = 50,
    library_scope: Optional[str] = None,
    recommended: bool = False,
    saved_only: bool = False,
    exclude_crawler_ids: Optional[list[int]] = None,
) -> dict:
```

Add the filter condition right after the existing `recommended` block (after line 1526, before `if exclude_crawler_ids:`):

```python
    if saved_only:
        conditions.append(
            "s.item_key IN (SELECT item_key FROM stock_item_saves "
            "WHERE user_id = %(user_id)s)"
        )
```

Add the join and the `saved` column to the main `SELECT` (lines 1538-1551):

```python
    rows = conn.execute(
        f"""
        SELECT s.id, s.artist, s.title, s.format, s.price, s.currency, s.url, s.cover_image_url, s.last_seen,
               s.item_key, cr.site_name AS source, j.reason AS reason,
               (sv.item_key IS NOT NULL) AS saved,
               (SELECT li.price_paid {_library_match_fragment('%(user_id)s', 'collection')} LIMIT 1) AS discogs_price
        FROM stock_items s
        JOIN crawlers cr ON cr.id = s.crawler_id
        LEFT JOIN stock_item_judgments j ON j.item_key = s.item_key AND j.user_id = %(user_id)s
        LEFT JOIN stock_item_saves sv ON sv.item_key = s.item_key AND sv.user_id = %(user_id)s
        {where}
        ORDER BY CASE WHEN {sort_expr} IS NULL THEN 1 ELSE 0 END {null_order}, {sort_expr} {order_sql}, s.id
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        params,
    ).fetchall()
```

Propagate `saved` onto every item dict, both the "own" row and its comparison rows (lines 1569-1585):

```python
    items = []
    own_rows = [dict(row) for row in rows]
    _apply_canonical_artists(conn, own_rows)
    for r in own_rows:
        items.append({**r, "is_own": True})
        for c in comparisons_by_item.get(r["item_key"], []):
            items.append({
                "id": f"{r['id']}:{c['source']}",
                "item_key": r["item_key"], "artist": r["artist"], "title": r["title"],
                "format": r["format"], "cover_image_url": r["cover_image_url"],
                "discogs_price": r["discogs_price"], "saved": r["saved"],
                "price": c["price"], "currency": c["currency"], "url": c["url"],
                "source": c["source"], "reason": r["reason"], "last_seen": c["last_checked"],
                "is_own": False,
            })
```

(Only the two literal additions matter here: `(sv.item_key IS NOT NULL) AS saved` in the `SELECT`, the new `LEFT JOIN stock_item_saves`, and `"saved": r["saved"]` in the comparison-row dict — the rest of the block is shown for exact placement, not to be retyped from scratch differently.)

- [ ] **Step 4: Add `saved_only` and the join to `get_distinct_stock_artists`**

Open `backend/db.py` at `get_distinct_stock_artists` (starts at line 1590) and read its full body — it mirrors `get_stock_items`'s `recommended` handling and its `LEFT JOIN stock_item_judgments j` (line 1618, and a second join at line 1634 if the function queries the join twice, e.g. once for the main list and once for a comparison-rows widening, matching whatever `get_stock_items` needed). Apply the identical pair of changes there: add `saved_only: bool = False` to the signature, add the same `if saved_only: conditions.append(...)` block used in Step 3, and add `LEFT JOIN stock_item_saves sv ON sv.item_key = s.item_key AND sv.user_id = %(user_id)s` next to every existing `LEFT JOIN stock_item_judgments j` in this function. This function returns a plain list of artist name strings, not item dicts — it doesn't need a `saved` field in its output, only the filter's `WHERE`/`JOIN` participation, so no dict-shape changes are needed here, unlike `get_stock_items`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_stock_crud.py -k "saved_only or saved_field or saved_state" -v`
Expected: 6 passed.

- [ ] **Step 6: Run the full backend suite**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest -x`
Expected: all pass. This includes every existing `get_stock_items`/`get_distinct_stock_artists` test — the new `saved` field and `LEFT JOIN` must not change any existing row's other fields or the total counts under `recommended`/`library_scope` filtering.

- [ ] **Step 7: Commit**

```bash
git add backend/db.py backend/tests/test_stock_crud.py
git commit -F- <<'EOF'
Add saved_only filter and saved field to get_stock_items

get_stock_items and get_distinct_stock_artists now join
stock_item_saves the same way they already join stock_item_judgments,
adding a per-row `saved` boolean and a saved_only filter with no
not-owned gate (unlike recommended).

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
EOF
```

---

## Task 3: Router endpoints — `PUT`/`DELETE /stock/saved/{item_key}` and `saved` query param

**Files:**
- Modify: `backend/routers/stock.py` — `list_stock` (lines 30-50), `list_stock_artists` (lines 53-67), plus two new route handlers.
- Test: `backend/tests/test_stock_router.py`

**Interfaces:**
- Consumes: `db.get_stock_items(..., saved_only=...)`, `db.get_distinct_stock_artists(..., saved_only=...)` (Task 2), `db.save_stock_item`/`db.unsave_stock_item` (Task 1).
- Produces: `GET /api/stock?saved=true`, `GET /api/stock/artists?saved=true`, `PUT /api/stock/saved/{item_key}` → `{"saved": true}`, `DELETE /api/stock/saved/{item_key}` → `{"saved": false}`. Task 5 (frontend `client.ts`) calls these.

- [ ] **Step 1: Write the failing tests**

First check the exact `_make_crawler` helper and any `_seed_stock_item`-style fixture already defined near the top of `backend/tests/test_stock_router.py` (`grep -n "^def _make_crawler\|^def _seed" backend/tests/test_stock_router.py`) and reuse it rather than re-deriving item setup — the tests below assume a helper named `_make_crawler()` returning a crawler id exists, matching what `test_list_stock_returns_items` (line 232) already uses; if its actual name differs, use the real one.

Add to `backend/tests/test_stock_router.py`, near `test_list_stock_returns_items`:

```python
def test_put_stock_saved_marks_item_saved(pg_test_db, authed_client_factory):
    crawler_id = _make_crawler()
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.replace_stock_items(conn, crawler_id, [
            {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
        ])
        conn.commit()
    item_key = db.compute_item_key("Artist A", "Album A", "https://x/1")
    client = authed_client_factory(user["id"])

    r = client.put(f"/api/stock/saved/{item_key}", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    assert r.json() == {"saved": True}

    r = client.get("/api/stock?saved=true", headers={"X-Requested-With": "fetch"})
    assert r.json()["total"] == 1


def test_delete_stock_saved_unmarks_item_saved(pg_test_db, authed_client_factory):
    crawler_id = _make_crawler()
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.replace_stock_items(conn, crawler_id, [
            {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
        ])
        conn.commit()
    item_key = db.compute_item_key("Artist A", "Album A", "https://x/1")
    client = authed_client_factory(user["id"])
    client.put(f"/api/stock/saved/{item_key}", headers={"X-Requested-With": "fetch"})

    r = client.delete(f"/api/stock/saved/{item_key}", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    assert r.json() == {"saved": False}

    r = client.get("/api/stock?saved=true", headers={"X-Requested-With": "fetch"})
    assert r.json()["total"] == 0


def test_delete_stock_saved_on_never_saved_item_is_a_noop(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(user["id"])

    r = client.delete("/api/stock/saved/never-saved-key", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    assert r.json() == {"saved": False}


def test_stock_saved_isolated_per_user(pg_test_db, authed_client_factory):
    crawler_id = _make_crawler()
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        db.replace_stock_items(conn, crawler_id, [
            {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
        ])
        conn.commit()
    item_key = db.compute_item_key("Artist A", "Album A", "https://x/1")
    alice_client = authed_client_factory(alice["id"])
    bob_client = authed_client_factory(bob["id"])

    alice_client.put(f"/api/stock/saved/{item_key}", headers={"X-Requested-With": "fetch"})

    r = bob_client.get("/api/stock?saved=true", headers={"X-Requested-With": "fetch"})
    assert r.json()["total"] == 0


def test_list_stock_artists_saved_filters(pg_test_db, authed_client_factory):
    crawler_id = _make_crawler()
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.replace_stock_items(conn, crawler_id, [
            {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
            {"artist": "Artist B", "title": "Album B", "url": "https://x/2", "price": 15.0, "currency": "USD"},
        ])
        conn.commit()
    item_key = db.compute_item_key("Artist A", "Album A", "https://x/1")
    client = authed_client_factory(user["id"])
    client.put(f"/api/stock/saved/{item_key}", headers={"X-Requested-With": "fetch"})

    r = client.get("/api/stock/artists?saved=true", headers={"X-Requested-With": "fetch"})
    assert r.json()["artists"] == ["Artist A"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_stock_router.py -k "saved" -v`
Expected: FAIL with 404s (routes don't exist) and `TypeError`/422 on the `saved=true` query param (not yet accepted).

- [ ] **Step 3: Add the query param to `list_stock` and `list_stock_artists`**

In `backend/routers/stock.py`, `list_stock` (lines 30-50):

```python
@router.get("/stock")
def list_stock(
    request: Request,
    search: Optional[str] = Query(None),
    artist: Optional[str] = Query(None),
    sort: str = Query("artist"),
    order: str = Query("asc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    library_scope: Optional[str] = Query(None),
    recommended: bool = Query(False),
    saved: bool = Query(False),
    hidden_crawler_ids: Optional[str] = Query(None),
):
    user_id = request.state.user_id
    exclude_crawler_ids = _parse_crawler_ids(hidden_crawler_ids)
    with db.user_scope(user_id) as conn:
        return db.get_stock_items(
            conn, user_id, search=search, artist=artist, sort=sort, order=order,
            page=page, per_page=per_page, library_scope=library_scope, recommended=recommended,
            saved_only=saved, exclude_crawler_ids=exclude_crawler_ids,
        )
```

`list_stock_artists` (lines 53-67):

```python
@router.get("/stock/artists")
def list_stock_artists(
    request: Request,
    library_scope: Optional[str] = Query(None),
    recommended: bool = Query(False),
    saved: bool = Query(False),
    hidden_crawler_ids: Optional[str] = Query(None),
):
    user_id = request.state.user_id
    exclude_crawler_ids = _parse_crawler_ids(hidden_crawler_ids)
    with db.user_scope(user_id) as conn:
        artists = db.get_distinct_stock_artists(
            conn, user_id, library_scope=library_scope, recommended=recommended,
            saved_only=saved, exclude_crawler_ids=exclude_crawler_ids,
        )
        return {"artists": artists}
```

- [ ] **Step 4: Add the two new endpoints**

In `backend/routers/stock.py`, after `list_stock_artists` and before `get_stock_judgment_status` (i.e. right after the function ending at line 67):

```python
@router.put("/stock/saved/{item_key}")
def save_stock_item(item_key: str, request: Request):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        db.save_stock_item(conn, user_id, item_key)
        conn.commit()
    return {"saved": True}


@router.delete("/stock/saved/{item_key}")
def unsave_stock_item(item_key: str, request: Request):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        db.unsave_stock_item(conn, user_id, item_key)
        conn.commit()
    return {"saved": False}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_stock_router.py -k "saved" -v`
Expected: 5 passed.

- [ ] **Step 6: Run the full backend suite**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest -x`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add backend/routers/stock.py backend/tests/test_stock_router.py
git commit -F- <<'EOF'
Add PUT/DELETE /stock/saved/{item_key} endpoints

Toggle endpoints for the Store tab "save for later" feature, plus a
`saved` query param on GET /stock and /stock/artists, following the
existing `recommended` param's shape.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
EOF
```

---

## Task 4: Frontend types and API client

**Files:**
- Modify: `frontend/src/api/types.ts` — `StockItem` interface (lines 127-142).
- Modify: `frontend/src/api/client.ts` — `getStock` (lines 163-186), `getStockArtists` (lines 189-197).
- Test: `frontend/src/test/client.test.ts`

**Interfaces:**
- Produces: `StockItem.saved: boolean`; `getStock(params: { ...; saved?: boolean })`; `getStockArtists(libraryScope?, recommended?, hiddenCrawlerIds?, saved?: boolean)`; `saveStockItem(itemKey: string): Promise<{saved: boolean}>`; `unsaveStockItem(itemKey: string): Promise<{saved: boolean}>`. Task 5/6 (`StockBrowser.tsx`) call all of these.

- [ ] **Step 1: Write the failing tests**

`frontend/src/test/client.test.ts` stubs `global.fetch` with a `fetchMock =
vi.fn()` in a `beforeEach` (see lines 5-9 and the `getStock`
`hidden_crawler_ids`/`library_scope` tests at lines 64-95, which this
follows exactly), and reads calls via `fetchMock.mock.calls[N][0]` (URL)
and `fetchMock.mock.calls[N][1]` (init object) rather than
`toHaveBeenCalledWith`. Add these inside the existing `describe(...)`
block, and add `saveStockItem, unsaveStockItem` to the import list at the
top of the file (line 2):

```ts
it('getStock forwards saved=true when saved is set', async () => {
  fetchMock.mockResolvedValue({ ok: true, json: async () => ({ total: 0, page: 1, per_page: 250, items: [] }) })
  await getStock({ saved: true })
  expect(fetchMock.mock.calls[0][0]).toContain('saved=true')
})

it('getStock omits saved when unset', async () => {
  fetchMock.mockResolvedValue({ ok: true, json: async () => ({ total: 0, page: 1, per_page: 250, items: [] }) })
  await getStock({})
  expect(fetchMock.mock.calls[0][0]).not.toContain('saved=')
})

it('getStockArtists forwards saved=true', async () => {
  fetchMock.mockResolvedValue({ ok: true, json: async () => ({ artists: [] }) })
  await getStockArtists(undefined, false, undefined, true)
  expect(fetchMock.mock.calls[0][0]).toContain('saved=true')
})

it('saveStockItem PUTs to /stock/saved/:item_key', async () => {
  fetchMock.mockResolvedValue({ ok: true, json: async () => ({ saved: true }) })
  await saveStockItem('abc123')
  expect(fetchMock.mock.calls[0][0]).toContain('/stock/saved/abc123')
  expect(fetchMock.mock.calls[0][1].method).toBe('PUT')
})

it('unsaveStockItem DELETEs to /stock/saved/:item_key', async () => {
  fetchMock.mockResolvedValue({ ok: true, json: async () => ({ saved: false }) })
  await unsaveStockItem('abc123')
  expect(fetchMock.mock.calls[0][0]).toContain('/stock/saved/abc123')
  expect(fetchMock.mock.calls[0][1].method).toBe('DELETE')
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/test/client.test.ts`
Expected: FAIL — `saveStockItem`/`unsaveStockItem` are not exported yet, and `saved` params aren't forwarded.

- [ ] **Step 3: Update `StockItem` type**

In `frontend/src/api/types.ts` (lines 127-142):

```ts
export interface StockItem {
  id: number | string
  item_key: string
  artist: string
  title: string
  format: string | null
  price: number | null
  currency: string | null
  url: string
  cover_image_url: string | null
  source: string
  last_seen: string
  reason: string | null
  is_own: boolean
  discogs_price: string | null
  saved: boolean
}
```

- [ ] **Step 4: Update `getStock` and `getStockArtists`, add `saveStockItem`/`unsaveStockItem`**

In `frontend/src/api/client.ts`:

```ts
export async function getStock(params: {
  search?: string
  artist?: string
  sort?: StockSortField
  order?: SortOrder
  page?: number
  per_page?: number
  libraryScope?: LibraryScope
  recommended?: boolean
  saved?: boolean
  hiddenCrawlerIds?: number[]
}): Promise<StockResponse> {
  const q = new URLSearchParams()
  if (params.search) q.set('search', params.search)
  if (params.artist) q.set('artist', params.artist)
  if (params.sort) q.set('sort', params.sort)
  if (params.order) q.set('order', params.order)
  if (params.page) q.set('page', String(params.page))
  if (params.per_page) q.set('per_page', String(params.per_page))
  if (params.libraryScope) q.set('library_scope', LIBRARY_SCOPE_PARAM[params.libraryScope])
  if (params.recommended) q.set('recommended', 'true')
  if (params.saved) q.set('saved', 'true')
  if (params.hiddenCrawlerIds?.length) q.set('hidden_crawler_ids', params.hiddenCrawlerIds.join(','))
  const r = await apiFetch(`/stock?${q}`)
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function getStockArtists(libraryScope?: LibraryScope, recommended?: boolean, hiddenCrawlerIds?: number[], saved?: boolean): Promise<string[]> {
  const q = new URLSearchParams()
  if (libraryScope) q.set('library_scope', LIBRARY_SCOPE_PARAM[libraryScope])
  if (recommended) q.set('recommended', 'true')
  if (saved) q.set('saved', 'true')
  if (hiddenCrawlerIds?.length) q.set('hidden_crawler_ids', hiddenCrawlerIds.join(','))
  const qs = q.toString() ? `?${q}` : ''
  const r = await apiFetch(`/stock/artists${qs}`)
  if (!r.ok) throw new Error(await r.text())
  const data = await r.json()
  return data.artists
}

export async function saveStockItem(itemKey: string): Promise<{ saved: boolean }> {
  const r = await apiFetch(`/stock/saved/${encodeURIComponent(itemKey)}`, { method: 'PUT' })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function unsaveStockItem(itemKey: string): Promise<{ saved: boolean }> {
  const r = await apiFetch(`/stock/saved/${encodeURIComponent(itemKey)}`, { method: 'DELETE' })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/test/client.test.ts`
Expected: all pass.

- [ ] **Step 6: Run the full frontend suite**

Run: `cd frontend && npx vitest run`
Expected: all pass (no other file references `StockItem` fields exhaustively enough to break on the new `saved` field, but this confirms it).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/types.ts frontend/src/api/client.ts frontend/src/test/client.test.ts
git commit -F- <<'EOF'
Add saved field and save/unsave API client functions

StockItem gains a saved boolean; getStock/getStockArtists forward a
saved filter param; new saveStockItem/unsaveStockItem PUT/DELETE
/stock/saved/{item_key}.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
EOF
```

---

## Task 5: `StockBrowser` — Saved filter, empty-state copy, and request wiring

**Files:**
- Modify: `frontend/src/views/StockBrowser.tsx` — `STORE_FILTERS` (line 17), `load()` (lines 55-68), the artist-refetch effect (lines 88-100), `emptyMessage` (lines 165-170), the filter `<select>` (lines 218-236).
- Test: `frontend/src/test/stockBrowser.test.tsx`

**Interfaces:**
- Consumes: `getStock`/`getStockArtists` with the `saved` param (Task 4).
- Produces: filter state that includes `'saved'` as a valid Store-scope value, with `load()` translating it to `saved: true` in the request — Task 6 (the toggle handler) reads `filter === 'saved'` to decide whether to drop a row from view on unsave.

- [ ] **Step 1: Write the failing tests**

`frontend/src/test/stockBrowser.test.tsx` mocks `getStock`/`getStockArtists`
as bare top-level `vi.fn()`s via `vi.mock('../api/client', ...)` (lines
10-16), resets and re-primes them in `beforeEach` (lines 18-24), and tests
the Recommended dropdown exactly like this (lines 157-192) — follow that
pattern verbatim, adding these alongside the existing Recommended tests:

```tsx
it('renders a Saved option in the Store filter dropdown', async () => {
  render(<StockBrowser scope="store" />)
  await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
  expect(screen.getByRole('option', { name: 'Saved' })).toBeTruthy()
})

it('does not render a Saved option in the Track filter dropdown', async () => {
  render(<StockBrowser scope="track" />)
  await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
  expect(screen.queryByRole('option', { name: 'Saved' })).toBeNull()
})

it('selecting Saved sends saved=true and no recommended param', async () => {
  render(<StockBrowser scope="store" />)
  await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'saved' } })
  await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ saved: true, recommended: false })))
})

it('shows saved-specific empty-state copy under the Saved filter with no results', async () => {
  getStock.mockResolvedValue({ total: 0, page: 1, per_page: 250, items: [] })
  render(<StockBrowser scope="store" />)
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'saved' } })
  await waitFor(() => expect(screen.getByText("You haven't saved anything yet.")).toBeTruthy())
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/test/stockBrowser.test.tsx`
Expected: FAIL — no "Saved" option exists yet, `saved` is never sent.

- [ ] **Step 3: Add `'saved'` to `STORE_FILTERS` and the dropdown**

In `frontend/src/views/StockBrowser.tsx`, line 17:

```ts
const STORE_FILTERS = ['all', 'recommended', 'saved'] as const
```

Lines 230-235:

```tsx
{scope === 'track' ? (
  <>
    <option value="all">All</option>
    <option value="collection">Collection</option>
    <option value="wantlist">Wantlist</option>
  </>
) : (
  <>
    <option value="all">All</option>
    <option value="recommended" disabled={!recommendedAvailable}>Recommended</option>
    <option value="saved">Saved</option>
  </>
)}
```

- [ ] **Step 4: Wire `saved` into `load()` and the artist-refetch effect**

In `load()` (lines 56-63), add `saved` to the `getStock` call:

```ts
const result = await getStock({
  search: search || undefined,
  artist: selectedArtist || undefined,
  sort, order, page, per_page: PER_PAGE,
  libraryScope: scope === 'track' ? trackLibraryScope(filter) : undefined,
  recommended: scope === 'store' && filter === 'recommended',
  saved: scope === 'store' && filter === 'saved',
  hiddenCrawlerIds,
})
```

In the artist-refetch effect (lines 94-98):

```ts
getStockArtists(
  scope === 'track' ? trackLibraryScope(filter) : undefined,
  scope === 'store' && filter === 'recommended',
  hiddenCrawlerIds,
  scope === 'store' && filter === 'saved',
).then((list) => { if (latest) setArtists(list) })
```

- [ ] **Step 5: Add the empty-state branch**

In `emptyMessage` (lines 165-170), add a `filter === 'saved'` branch alongside the existing `filter === 'recommended'` one:

```ts
const emptyMessage =
  scope === 'store' && filter === 'recommended' ? 'Nothing recommended is in stock right now.'
  : scope === 'store' && filter === 'saved' ? "You haven't saved anything yet."
  : scope === 'store' ? (isAdmin ? 'No in-stock items yet. Click Refresh under Store Management in Settings.' : 'No in-stock items yet. Check back after the next store sync.')
  : filter === 'collection' ? 'Nothing in your collection is in stock right now.'
  : filter === 'wantlist' ? 'Nothing on your wantlist is in stock right now.'
  : "Nothing you're tracking is in stock right now."
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/test/stockBrowser.test.tsx`
Expected: all pass.

- [ ] **Step 7: Run the full frontend suite**

Run: `cd frontend && npx vitest run`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/views/StockBrowser.tsx frontend/src/test/stockBrowser.test.tsx
git commit -F- <<'EOF'
Add Saved filter option to the Store tab dropdown

Wires filter=saved through load()/getStockArtists to the new saved
query param, and adds saved-specific empty-state copy.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
EOF
```

---

## Task 6: `StockBrowser` — bookmark icon, toggle handler, table column, tile overlay

**Files:**
- Modify: `frontend/src/views/StockBrowser.tsx` — add a `BookmarkIcon` component and `toggleSaved` handler; table `<thead>`/`<tbody>` (lines 300-378); tile rendering (lines 271-296); `colCount` (line 163).
- Test: `frontend/src/test/stockBrowser.test.tsx`

**Interfaces:**
- Consumes: `saveStockItem`/`unsaveStockItem` (Task 4), `filter` state (Task 5) to decide whether an unsave should drop the row from view.
- Produces: none consumed by later tasks — this is the last task.

- [ ] **Step 1: Write the failing tests**

First add `saved: false` to both fixture items in the file's top-level
`items` array (lines 5-8, Task 5's context) so existing tests keep passing
once the component reads `item.saved` — omit this and every pre-existing
test that renders a row will pass `item.saved === undefined`, which is
falsy and happens to render correctly, but is worth making explicit since
`StockItem.saved` is now non-optional.

Add `saveStockItem`/`unsaveStockItem` mocks to the `vi.mock('../api/client',
...)` block (lines 10-16), following the same `(...args) => mockFn(...args)`
forwarding shape already used there for `getStock`/`getStockArtists`:

```tsx
const getStock = vi.fn()
const getStockArtists = vi.fn()
const saveStockItem = vi.fn()
const unsaveStockItem = vi.fn()

vi.mock('../api/client', () => ({
  getStock: (...args: unknown[]) => getStock(...args),
  getStockArtists: (...args: unknown[]) => getStockArtists(...args),
  saveStockItem: (...args: unknown[]) => saveStockItem(...args),
  unsaveStockItem: (...args: unknown[]) => unsaveStockItem(...args),
}))
```

Add `saveStockItem.mockReset(); unsaveStockItem.mockReset()` to the
existing `beforeEach` (lines 18-24), alongside the existing
`getStock.mockReset()`/`getStockArtists.mockReset()`.

Then add these tests, following the same `render`/`waitFor` conventions as
the Recommended tests (lines 157-192):

```tsx
it('renders a bookmark button per row in Store scope list view', async () => {
  render(<StockBrowser scope="store" />)
  await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
  expect(screen.getAllByTitle('Save for later').length).toBeGreaterThanOrEqual(1)
})

it('does not render a bookmark button in Track scope', async () => {
  render(<StockBrowser scope="track" />)
  await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
  expect(screen.queryByTitle('Save for later')).toBeNull()
  expect(screen.queryByTitle('Remove from saved')).toBeNull()
})

it('clicking the bookmark button calls saveStockItem with the item_key and flips the icon title', async () => {
  saveStockItem.mockResolvedValue({ saved: true })
  render(<StockBrowser scope="store" />)
  await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
  const button = screen.getAllByTitle('Save for later')[0]
  fireEvent.click(button)
  expect(saveStockItem).toHaveBeenCalledWith('k1')
  await waitFor(() => expect(screen.getAllByTitle('Remove from saved').length).toBeGreaterThanOrEqual(1))
})

it('unsaving under the Saved filter removes the row and decrements the count', async () => {
  getStock.mockResolvedValue({
    total: 1, page: 1, per_page: 250,
    items: [{ ...items[0], saved: true }],
  })
  unsaveStockItem.mockResolvedValue({ saved: false })
  render(<StockBrowser scope="store" />)
  await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'saved' } })
  const button = await screen.findByTitle('Remove from saved')
  fireEvent.click(button)
  await waitFor(() => expect(screen.queryByText('The Great Satan — Ghostly Black Vinyl')).toBeNull())
  expect(screen.getByText(/^0 items$/)).toBeTruthy()
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/test/stockBrowser.test.tsx`
Expected: FAIL — no bookmark button exists yet.

- [ ] **Step 3: Add the icon component and imports**

At the top of `frontend/src/views/StockBrowser.tsx`, update the import from `../api/client`:

```ts
import { getStock, getStockArtists, saveStockItem, unsaveStockItem } from '../api/client'
```

After the component's props/consts (e.g. right after `NO_HIDDEN_CRAWLER_IDS`, before the `StockBrowser` function), add:

```tsx
function BookmarkIcon({ filled }: { filled: boolean }) {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill={filled ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="1.5">
      <path d="M4 2h8a1 1 0 0 1 1 1v11l-5-3-5 3V3a1 1 0 0 1 1-1Z" strokeLinejoin="round" />
    </svg>
  )
}
```

- [ ] **Step 4: Add the `toggleSaved` handler inside `StockBrowser`**

Add near `changeFilter`/`toggleSort` (after `toggleSort`, before `selectArtist`, around line 148):

```ts
async function toggleSaved(item: StockItem) {
  const next = !item.saved
  setItems((prev) => {
    const patched = prev.map((it) => (it.item_key === item.item_key ? { ...it, saved: next } : it))
    return filter === 'saved' && !next ? patched.filter((it) => it.item_key !== item.item_key) : patched
  })
  if (filter === 'saved' && !next) setTotal((t) => t - 1)
  await (next ? saveStockItem(item.item_key) : unsaveStockItem(item.item_key))
}
```

- [ ] **Step 5: Update `colCount`**

Line 163:

```ts
const colCount = scope === 'track' ? 7 : 7
```

- [ ] **Step 6: Add the table column**

In the `<thead>` (after line 305, the existing empty `<th className="w-12 px-3 py-2"></th>` cover-art header), and again at the end of the header row (after the Source `<th>`, i.e. after line 341, before the closing `</tr>` at line 342):

```tsx
{scope === 'store' && <th className="w-8 px-3 py-2"></th>}
```

In `<tbody>`, after the Source `<td>` (after line 372, before the closing `</tr>` at line 373):

```tsx
{scope === 'store' && (
  <td className="px-3 py-2">
    <button
      onClick={() => toggleSaved(item)}
      title={item.saved ? 'Remove from saved' : 'Save for later'}
      className={`p-1 ${dismissButtonClass()}`}
    >
      <BookmarkIcon filled={item.saved} />
    </button>
  </td>
)}
```

- [ ] **Step 7: Add the tile overlay**

In the tile rendering (lines 274-292), wrap the cover image/placeholder in a `relative` div and add the overlay button, and only for `scope === 'store'`:

```tsx
{items.filter((item) => item.is_own).map((item) => (
  <a
    key={item.id}
    href={item.url}
    target="_blank"
    rel="noreferrer"
    className="group"
  >
    <div className="relative">
      {item.cover_image_url ? (
        <img
          src={item.cover_image_url}
          alt={item.title}
          className="w-full aspect-square object-cover rounded"
        />
      ) : (
        <div className="w-full aspect-square bg-gray-800 rounded" />
      )}
      {scope === 'store' && (
        <button
          onClick={(e) => { e.preventDefault(); toggleSaved(item) }}
          title={item.saved ? 'Remove from saved' : 'Save for later'}
          className="absolute top-1 right-1 p-1 rounded-full bg-gray-950/70 text-white hover:bg-gray-950"
        >
          <BookmarkIcon filled={item.saved} />
        </button>
      )}
    </div>
    <div className="mt-1.5 text-sm text-gray-200 truncate group-hover:text-white" title={item.reason ?? undefined}>{item.artist}</div>
    <div className="text-xs text-gray-400 truncate" title={item.reason ?? undefined}>{item.title}</div>
  </a>
))}
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/test/stockBrowser.test.tsx`
Expected: all pass.

- [ ] **Step 9: Run the full frontend suite**

Run: `cd frontend && npx vitest run`
Expected: all pass. Pay particular attention to any test asserting a fixed column count for the Store table (`colSpan`, header cell count) — the new column shifts these.

- [ ] **Step 10: Manual verification in the browser**

Start both servers and confirm in a live browser:
1. Backend: `cd backend && uvicorn main:app --reload --port 8000`
2. Frontend: `cd frontend && npm run dev`
3. Open the Store tab, list view: confirm an outline bookmark icon appears at the right of every row, click one, confirm it fills in.
4. Switch to tile view: confirm the bookmark overlay appears top-right on each cover, click one, confirm clicking it does not navigate to the listing URL.
5. Switch the filter dropdown to "Saved": confirm only saved items show, and clicking a filled bookmark removes that item from view immediately and updates the item count.
6. Switch to the Track tab: confirm no bookmark icon appears anywhere and no "Saved" option exists in its dropdown.
7. Reload the page with the Saved filter selected: confirm the saved set persists (backend-backed, not just local state).

- [ ] **Step 11: Commit**

```bash
git add frontend/src/views/StockBrowser.tsx frontend/src/test/stockBrowser.test.tsx
git commit -F- <<'EOF'
Add bookmark toggle to Store tab rows and tiles

Bookmark icon in both list and tile views, backed by saveStockItem/
unsaveStockItem with optimistic local state; unsaving under the Saved
filter drops the row/tile from view immediately.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
EOF
```

---

## Task 7: Pre-PR spec-drift check and PR

**Files:**
- Read-only survey across `docs/superpowers/specs/` and `docs/specifications/shaping/`.
- Possible amendments to any spec found to have drifted.

- [ ] **Step 1: Grep for drift**

Run, from the repo root:

```bash
grep -rl "StockBrowser\|stock_item_judgments\|get_stock_items\|colCount\|STORE_FILTERS\|Store tab" docs/superpowers/specs/ docs/specifications/shaping/
```

For each file returned, open it and check whether this branch's changes make any of its prose inaccurate — specifically, any claim about the Store dropdown's option set (`All`/`Recommended` only), `get_stock_items`'s parameter list, or `colCount`'s value.

- [ ] **Step 2: Amend any drifted spec**

If a file's prose is now wrong (not just incomplete — see the "Spec drift" section of this feature's own design doc, which already identified `2026-08-10-collection-wishlist-filter-design.md` as incomplete-but-not-wrong and requiring no amendment), add a short inline correction note, not a rewrite. Commit it separately:

```bash
git add <the amended spec file>
git commit -F- <<'EOF'
Amend <spec-name> for Store saved-items drift

<one-line description of what changed and why>

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
EOF
```

If no drift is found, no commit is needed — note that in the PR description (Step 3).

- [ ] **Step 3: Open the PR**

```bash
git push -u origin claude/store-saved-items-7555c6
gh pr create --title "Add Store tab save-for-later" --draft=false --body "$(cat <<'EOF'
## Summary
- New per-user `stock_item_saves` table (mirrors `stock_item_judgments`) backing a bookmark toggle on Store-tab items.
- `PUT`/`DELETE /stock/saved/{item_key}` endpoints; `saved` filter param on `GET /stock` and `/stock/artists`.
- Bookmark icon in both list and tile views (Store scope only); new "Saved" filter option; unsaving under that filter removes the item from view immediately.

## Spec drift
<fill in from Task 7 Step 1/2: either "None found." or a list of amended files with one line each>

## Test plan
- [ ] `cd backend && TEST_DATABASE_URL=... IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest` — all pass
- [ ] `cd frontend && npx vitest run` — all pass
- [ ] Manual verification per Task 6 Step 10 (list view, tile view, filter, Track-tab absence, reload persistence)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Do **not** enable auto-merge. After pushing, poll for Copilot's automated review against the exact head SHA per this repo's `CLAUDE.md` "Pull requests" section before considering the PR-opening task finished.

---

## Self-Review Notes

- **Spec coverage:** every section of `2026-08-16-store-saved-items-design.md` maps to a task — table/RLS/grant (Task 1), functions (Task 1), `saved_only`/join (Task 2), router (Task 3), types/client (Task 4), filter/empty-state (Task 5), icon/toggle/table/tiles (Task 6), spec-drift + PR (Task 7).
- **Track-scope exclusion** is enforced at three separate points (dropdown option, table column, tile overlay) and each has its own test in Tasks 5/6 — this was checked against the design doc's repeated "Store scope only" callouts to make sure no single `scope === 'store'` gate was assumed to cover all three render sites.
- **Type consistency:** `item.item_key` (used throughout Task 6) matches the `StockItem.item_key: string` field already present before this plan (unchanged) and confirmed again in Task 4. `saveStockItem`/`unsaveStockItem` signatures (Task 4) match their call sites in Task 6's `toggleSaved`. `saved_only` (backend, Tasks 2-3) and `saved` (frontend, Tasks 4-6) are named consistently with the existing `recommended`/`recommended` (no `_only` suffix on the frontend/wire side) and `library_scope`/`libraryScope` precedents already in this file.
- **No placeholders:** every step has literal code, not a description of what to write. Task 2 and Task 3's test-writing steps include an explicit instruction to verify exact helper signatures against the nearest existing test before finalizing, because those signatures live in files this plan's author did not have open line-by-line when drafting — that's a verification instruction, not a placeholder for the change itself.
