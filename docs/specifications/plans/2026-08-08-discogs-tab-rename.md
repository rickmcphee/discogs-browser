# Discogs Tab Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the Collection tab to Discogs, strip its cross-site price columns (and Wishlist's), and add a Date Added column sourced from Discogs' own per-item `date_added`.

**Architecture:** `library_items` gains two per-scope timestamp columns (`collection_date_added`, `wishlist_date_added`), populated by `crawl_manager._sync_collection` from the raw Discogs API item. `get_library_releases` exposes the scope-correct one as `date_added` and drops its now-dead price-sort/listings-join code path. The frontend renames the `collection` scope/view to `discogs` everywhere, deletes `RecordBrowser`'s price-column rendering and crawl-event listener, and adds a sortable Date Added column.

**Tech Stack:** FastAPI + psycopg3 + Postgres (backend), React + TypeScript + Vite + Vitest/RTL (frontend), pytest + pytest-asyncio (backend tests).

## Global Constraints

- Every commit carries the full AI-attribution trailer block required by this repo's `CLAUDE.md` (`Note: This commit message was created by AI` / `ai-generated: true` / `ai-model: claude-sonnet-5` / `ai-tool: claude-code` / `ai-surface: cli` / `ai-executor: remote-agent`), created via a message file, not `git commit -m`.
- No comments unless the WHY is non-obvious; no backwards-compat shims — just change the code (repo style rule).
- Python ≥3.9: no `str | None` syntax — use `Optional[str]`.
- `backend/version.py`'s `VERSION` gets exactly one minor bump for this whole PR, done in the final task — not per-commit.
- Spec: `docs/specifications/shaping/2026-08-08-discogs-tab-rename-design.md`.

---

### Task 1: `library_items` schema — two new date-added columns

**Files:**
- Modify: `backend/db.py` (`TENANT_SCHEMA` string, `upsert_library_item`)
- Test: `backend/tests/test_catalog_crud.py`

**Interfaces:**
- Produces: `db.upsert_library_item(conn, user_id, discogs_id, in_collection=None, in_wishlist=None, collection_date_added=None, wishlist_date_added=None)` — two new optional kwargs, same COALESCE-on-conflict semantics as the existing two.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_catalog_crud.py`, right after `test_get_library_releases_returns_only_calling_users_rows` (so it sits with the other `library_items`-focused tests):

```python
def test_upsert_library_item_collection_and_wishlist_date_added_are_independent(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_library_item(
        admin_conn, alice["id"], "r1", in_collection=True,
        collection_date_added="2024-01-15T00:00:00",
    )
    admin_conn.commit()
    row = admin_conn.execute(
        "SELECT collection_date_added, wishlist_date_added FROM library_items WHERE user_id = %s AND discogs_id = 'r1'",
        [alice["id"]],
    ).fetchone()
    assert str(row["collection_date_added"]) == "2024-01-15 00:00:00"
    assert row["wishlist_date_added"] is None

    # A later wishlist-scoped write sets wishlist_date_added without
    # clobbering the collection_date_added set above.
    db.upsert_library_item(
        admin_conn, alice["id"], "r1", in_wishlist=True,
        wishlist_date_added="2024-02-20T00:00:00",
    )
    admin_conn.commit()
    row = admin_conn.execute(
        "SELECT collection_date_added, wishlist_date_added FROM library_items WHERE user_id = %s AND discogs_id = 'r1'",
        [alice["id"]],
    ).fetchone()
    assert str(row["collection_date_added"]) == "2024-01-15 00:00:00"
    assert str(row["wishlist_date_added"]) == "2024-02-20 00:00:00"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_catalog_crud.py::test_upsert_library_item_collection_and_wishlist_date_added_are_independent -v`
Expected: FAIL — `upsert_library_item() got an unexpected keyword argument 'collection_date_added'`

- [ ] **Step 3: Implement the schema and function change**

In `backend/db.py`, immediately after the `library_items` table's closing `);` in `TENANT_SCHEMA` (right before the `stock_item_judgments` table definition), add:

```sql
ALTER TABLE library_items ADD COLUMN IF NOT EXISTS collection_date_added TIMESTAMP;
ALTER TABLE library_items ADD COLUMN IF NOT EXISTS wishlist_date_added TIMESTAMP;
```

Replace `upsert_library_item` in full:

```python
def upsert_library_item(
    conn,
    user_id: int,
    discogs_id: str,
    in_collection: Optional[bool] = None,
    in_wishlist: Optional[bool] = None,
    collection_date_added: Optional[str] = None,
    wishlist_date_added: Optional[str] = None,
):
    # COALESCE resolves "unspecified" (None) to the existing row's own column
    # on update, or FALSE/NULL on first insert — in one atomic statement, so
    # two concurrent partial updates (e.g. collection-sync setting
    # in_collection/collection_date_added, wishlist-sync setting
    # in_wishlist/wishlist_date_added) can't race on a separate read.
    conn.execute(
        """
        INSERT INTO library_items (
            user_id, discogs_id, in_collection, in_wishlist,
            collection_date_added, wishlist_date_added, last_synced
        )
        VALUES (
            %(user_id)s, %(discogs_id)s, COALESCE(%(in_collection)s, FALSE),
            COALESCE(%(in_wishlist)s, FALSE), %(collection_date_added)s,
            %(wishlist_date_added)s, CURRENT_TIMESTAMP
        )
        ON CONFLICT (user_id, discogs_id) DO UPDATE SET
            in_collection = COALESCE(%(in_collection)s, library_items.in_collection),
            in_wishlist = COALESCE(%(in_wishlist)s, library_items.in_wishlist),
            collection_date_added = COALESCE(%(collection_date_added)s, library_items.collection_date_added),
            wishlist_date_added = COALESCE(%(wishlist_date_added)s, library_items.wishlist_date_added),
            last_synced = CURRENT_TIMESTAMP
        """,
        {
            "user_id": user_id,
            "discogs_id": discogs_id,
            "in_collection": in_collection,
            "in_wishlist": in_wishlist,
            "collection_date_added": collection_date_added,
            "wishlist_date_added": wishlist_date_added,
        },
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_catalog_crud.py -v`
Expected: PASS (the new test, and every pre-existing test in this file — `TENANT_SCHEMA` is idempotent `CREATE`/`ALTER ... IF NOT EXISTS`, so existing tests are unaffected)

- [ ] **Step 5: Commit**

```bash
git add backend/db.py backend/tests/test_catalog_crud.py
```

Commit message body:

```
feat: add collection/wishlist date_added columns to library_items

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: cli
ai-executor: remote-agent
```

---

### Task 2: Capture `date_added` during sync; stop enqueueing wishlist price crawls

**Files:**
- Modify: `backend/crawl_manager.py`
- Test: `backend/tests/test_crawl_manager.py`

**Interfaces:**
- Consumes: `db.upsert_library_item(..., collection_date_added=..., wishlist_date_added=...)` from Task 1.
- Produces: no new public interface — behavior change only (wishlist sync no longer calls `enqueue_crawl_queue`).

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_crawl_manager.py`, directly after `test_sync_collection_enqueues_crawl_queue_for_missing_listings`:

```python
@respx.mock
async def test_sync_collection_captures_date_added(pg_schema, monkeypatch):
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
                "date_added": "2024-03-15T10:00:00-08:00",
            }],
        })
    )
    respx.get(url__regex=r"https://api\.discogs\.com/releases/\d+").mock(
        return_value=httpx.Response(200, json={"identifiers": []})
    )
    respx.get("https://api.discogs.com/users/alice/wants").mock(
        return_value=httpx.Response(200, json={"pagination": {"pages": 1}, "wants": []})
    )

    manager = CrawlManager()
    await manager._sync_collection(user["id"], "all")

    with db.user_scope(user["id"]) as conn:
        row = conn.execute(
            "SELECT collection_date_added FROM library_items WHERE user_id = %s AND discogs_id = 'r111'",
            [user["id"]],
        ).fetchone()
    # collection_date_added is TIMESTAMP (no time zone) — Postgres discards
    # the "-08:00" offset on input rather than converting by it, so the
    # stored wall-clock value is the literal "10:00:00" from the API item.
    assert str(row["collection_date_added"]) == "2024-03-15 10:00:00"


async def test_sync_collection_wishlist_captures_date_added_and_does_not_enqueue(pg_schema, monkeypatch):
    import config
    import crawl_manager as crawl_manager_module
    import discogs
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "k")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "s")
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", "kL8mN2pQ7rT5vX9yB3cF6hJ1kM4nP8sU2wZ5aD7eG0i=")
    monkeypatch.setattr(crawl_manager_module.time, "sleep", lambda *a, **k: None)

    def _must_not_be_called(*a, **k):
        raise AssertionError("collection loop must not run for scope=wishlist")

    monkeypatch.setattr(discogs, "fetch_collection_fields", _must_not_be_called)
    monkeypatch.setattr(discogs, "iter_collection_pages", _must_not_be_called)

    def _wants_pages(*a, **k):
        yield 1, 1, [{
            "basic_information": {
                "id": 111, "title": "Album", "year": 2020,
                "artists": [{"name": "Artist"}], "labels": [], "formats": [],
                "cover_image": "",
            },
            "date_added": "2024-05-01T00:00:00Z",
        }]

    monkeypatch.setattr(discogs, "iter_wantlist_pages", _wants_pages)
    monkeypatch.setattr(discogs, "fetch_release_barcode", lambda *a, **k: None)

    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute(
            "UPDATE users SET discogs_oauth_token_encrypted = %s, discogs_oauth_secret_encrypted = %s WHERE id = %s",
            [token_encryption.encrypt("tok"), token_encryption.encrypt("sec"), user["id"]],
        )
        db.register_crawler(conn, "Amazon", "/x.py")
        conn.commit()

    manager = CrawlManager()
    await manager._sync_collection(user["id"], "all", "wishlist")

    statuses = [e["status"] for e in manager.recent_events()]
    assert "sync_error" not in statuses

    with db.user_scope(user["id"]) as conn:
        row = conn.execute(
            "SELECT wishlist_date_added FROM library_items WHERE user_id = %s AND discogs_id = 'r111'",
            [user["id"]],
        ).fetchone()
    # Same TIMESTAMP-without-time-zone reasoning as the collection test above
    # — the "Z" (UTC) designator on the input is discarded, not converted by.
    assert str(row["wishlist_date_added"]) == "2024-05-01 00:00:00"

    with db.get_admin_pool().connection() as conn:
        queued = conn.execute("SELECT discogs_id FROM crawl_queue").fetchall()
    assert queued == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_crawl_manager.py -k "captures_date_added" -v`
Expected: FAIL — `collection_date_added`/`wishlist_date_added` are `None` (not yet captured from the API item), and the wishlist test's final `assert queued == []` fails because the wishlist loop still enqueues today.

- [ ] **Step 3: Implement**

In `backend/crawl_manager.py`, in the collection loop inside `_sync_collection` (the `if scope != "wishlist":` block), change:

```python
                            upsert_catalog_release(conn, release)
                            upsert_library_item(conn, user_id, rid, in_collection=True)
                            for crawler in enabled_crawlers:
                                enqueue_crawl_queue(conn, rid, crawler["id"])
```

to:

```python
                            upsert_catalog_release(conn, release)
                            upsert_library_item(
                                conn, user_id, rid, in_collection=True,
                                collection_date_added=item.get("date_added"),
                            )
                            for crawler in enabled_crawlers:
                                enqueue_crawl_queue(conn, rid, crawler["id"])
```

In the wishlist loop, change:

```python
                        upsert_catalog_release(conn, release)
                        upsert_library_item(
                            conn, user_id, rid, in_wishlist=True,
                            in_collection=False if is_new_release else None,
                        )
                        for crawler in enabled_crawlers:
                            enqueue_crawl_queue(conn, rid, crawler["id"])
                        wishlist_count += 1
```

to:

```python
                        upsert_catalog_release(conn, release)
                        upsert_library_item(
                            conn, user_id, rid, in_wishlist=True,
                            in_collection=False if is_new_release else None,
                            wishlist_date_added=item.get("date_added"),
                        )
                        wishlist_count += 1
```

(The `for crawler in enabled_crawlers: enqueue_crawl_queue(...)` loop is deleted outright — wishlist items no longer get release-crawler price crawls.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_crawl_manager.py -v`
Expected: PASS — all tests in the file, including
`test_sync_collection_wishlist_scope_skips_collection_loop` (unaffected: that test registers no crawler, so it never exercised the enqueue loop either way) and the two new tests above.

- [ ] **Step 5: Commit**

```bash
git add backend/crawl_manager.py backend/tests/test_crawl_manager.py
```

Commit message body:

```
feat: capture date_added on sync, stop wishlist price-crawl enqueue

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: cli
ai-executor: remote-agent
```

---

### Task 3: Expose `date_added`; rename the backend's `collection` scope value to `discogs`; delete the dead price-sort path

**Files:**
- Modify: `backend/db.py` (`get_library_releases`, `get_distinct_artists`, delete `get_listings_for_release`)
- Test: `backend/tests/test_catalog_crud.py`, `backend/tests/test_library_maintenance.py`

**Interfaces:**
- Consumes: `library_items.collection_date_added`/`wishlist_date_added` columns from Task 1.
- Produces: `get_library_releases(...)` return dict's `releases[i]["date_added"]` — `str | None`, sourced from `collection_date_added` when `scope="discogs"`, `wishlist_date_added` when `scope="wishlist"`, else `None`. `sort="date_added"` is now a valid value for `sort`.

This task also renames the wire value the display-scope filter accepts from `"collection"` to `"discogs"`, in both `get_library_releases` and `get_distinct_artists` — Task 4 makes the frontend send `scope=discogs` (not `scope=collection`) for the Discogs tab, on both `/api/releases` and `/api/artists`, so the backend must accept that value on both endpoints or the Discogs tab's collection filter and artist sidebar silently stop filtering. Doing this rename now, in the same task that already rewrites `get_library_releases`'s scope-conditional code, avoids writing tests against a value (`"collection"`) that Task 4 would otherwise have to immediately rename anyway.

- [ ] **Step 1: Delete dead tests/helper, write new failing tests**

In `backend/tests/test_catalog_crud.py`, delete these four tests and the helper they all call (the `price_<site>` sort feature they cover is being removed): `test_get_library_releases_includes_plex_url_when_sorting_by_known_site_price`, `test_get_library_releases_includes_plex_url_when_sort_falls_back_to_artist`, `test_get_library_releases_sorts_by_price_for_named_site`, `test_get_library_releases_price_sort_for_unknown_site_falls_back_to_artist_asc`, and `_seed_three_releases_for_price_sort`. Also delete `test_get_listings_for_release_joins_crawler_site_name` (the function it tests is deleted in Step 3).

Add these in their place:

```python
def test_get_library_releases_returns_date_added_for_the_matching_scope(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_library_item(
        admin_conn, alice["id"], "r1", in_collection=True, in_wishlist=True,
        collection_date_added="2024-01-15T00:00:00", wishlist_date_added="2024-02-20T00:00:00",
    )
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        collection_result = db.get_library_releases(conn, alice["id"], scope="discogs")
        wishlist_result = db.get_library_releases(conn, alice["id"], scope="wishlist")
        unscoped_result = db.get_library_releases(conn, alice["id"])
    # dict_row returns a datetime object for a TIMESTAMP column, same as the
    # raw-cursor reads in Task 1/2's tests — isoformat() gives back the exact
    # "T"-separated string the naive datetime was written with above.
    assert collection_result["releases"][0]["date_added"].isoformat() == "2024-01-15T00:00:00"
    assert wishlist_result["releases"][0]["date_added"].isoformat() == "2024-02-20T00:00:00"
    assert unscoped_result["releases"][0]["date_added"] is None


def test_get_library_releases_sorts_by_date_added_nulls_last(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    for rid, artist, date_added in [
        ("r1", "Bbb", "2024-02-01T00:00:00"),
        ("r2", "Aaa", "2024-01-01T00:00:00"),
        ("r3", "Ccc", None),
    ]:
        db.upsert_catalog_release(admin_conn, {
            "discogs_id": rid, "artist": artist, "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.upsert_library_item(admin_conn, alice["id"], rid, in_collection=True, collection_date_added=date_added)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_library_releases(conn, alice["id"], scope="discogs", sort="date_added", order="asc")
    assert [r["discogs_id"] for r in result["releases"]] == ["r2", "r1", "r3"]

    with db.user_scope(alice["id"]) as conn:
        result = db.get_library_releases(conn, alice["id"], scope="discogs", sort="date_added", order="desc")
    assert [r["discogs_id"] for r in result["releases"]] == ["r1", "r2", "r3"]


def test_get_distinct_artists_filters_by_discogs_scope(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "Zzz", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r2", "artist": "Aaa", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True)
    db.upsert_library_item(admin_conn, alice["id"], "r2", in_wishlist=True)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        artists = db.get_distinct_artists(conn, alice["id"], scope="discogs")
    assert artists == ["Zzz"]
```

- [ ] **Step 2: Fix `test_library_maintenance.py`'s scope literal**

In `backend/tests/test_library_maintenance.py`, change `db.get_distinct_artists(conn, alice["id"], scope="collection")` to `db.get_distinct_artists(conn, alice["id"], scope="discogs")` — same rename, this test already exercises `get_distinct_artists`' collection-scope filter and would otherwise silently stop testing it (it wouldn't fail today since its dataset has only one item either way, but it would be asserting on a code path that's no longer reachable with the old string).

- [ ] **Step 3: Run tests to verify the new ones fail**

Run: `cd backend && pytest tests/test_catalog_crud.py -k "date_added or discogs_scope" -v`
Expected: FAIL — `test_get_library_releases_returns_date_added_for_the_matching_scope` and `test_get_library_releases_sorts_by_date_added_nulls_last` fail with `KeyError: 'date_added'` (the column isn't selected/computed yet); `test_get_distinct_artists_filters_by_discogs_scope` fails because `get_distinct_artists` still checks `scope == "collection"`, so `scope="discogs"` applies no filter and both artists come back.

- [ ] **Step 4: Implement**

In `backend/db.py`, replace `_RELEASE_ALLOWED_SORT` and the body of `get_library_releases` from the `if sort.startswith("price_"):` line through the end of the function (i.e. delete the whole `price_`-sort branch and the `get_listings_for_release` per-row call), with:

```python
_RELEASE_ALLOWED_SORT = {"artist", "title", "year", "label", "format", "discogs_price", "date_added"}


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
    unmatched: bool = False,
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
    if scope == "discogs":
        conditions.append("li.in_collection = TRUE")
    elif scope == "wishlist":
        conditions.append("li.in_wishlist = TRUE")
    if unmatched:
        conditions.append("li.plex_url IS NULL")

    where = "WHERE " + " AND ".join(conditions)
    base_from = "FROM library_items li JOIN catalog c ON c.discogs_id = li.discogs_id"

    total = conn.execute(f"SELECT COUNT(*) {base_from} {where}", params).fetchone()["count"]

    offset = (page - 1) * per_page
    params["limit"] = per_page
    params["offset"] = offset

    if sort == "date_added":
        sort_expr = "li." + ("wishlist_date_added" if scope == "wishlist" else "collection_date_added")
    else:
        sort_col = sort if sort in _RELEASE_ALLOWED_SORT else "artist"
        sort_expr = f"c.{sort_col}"

    rows = conn.execute(
        f"""
        SELECT c.*, li.plex_url, li.plex_matched_at,
               li.collection_date_added, li.wishlist_date_added
        {base_from} {where}
        ORDER BY CASE WHEN {sort_expr} IS NULL THEN 1 ELSE 0 END {null_order}, {sort_expr} {order_sql}
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        params,
    ).fetchall()

    releases = []
    for row in rows:
        r = dict(row)
        if scope == "wishlist":
            r["date_added"] = r.pop("wishlist_date_added")
            del r["collection_date_added"]
        elif scope == "discogs":
            r["date_added"] = r.pop("collection_date_added")
            del r["wishlist_date_added"]
        else:
            r["date_added"] = None
            del r["collection_date_added"]
            del r["wishlist_date_added"]
        releases.append(r)

    return {"total": total, "page": page, "per_page": per_page, "releases": releases}
```

Then delete `get_listings_for_release` entirely (no remaining caller).

In `get_distinct_artists`, change `if scope == "collection":` to `if scope == "discogs":` (same rename, same reason — this function backs `/api/artists`, which Task 4's frontend change also calls with `scope=discogs`).

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_catalog_crud.py tests/test_library_maintenance.py -v`
Expected: PASS for both files.

- [ ] **Step 6: Commit**

```bash
git add backend/db.py backend/tests/test_catalog_crud.py backend/tests/test_library_maintenance.py
```

Commit message body:

```
feat: expose date_added, rename collection scope value to discogs, drop dead price-sort path

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: cli
ai-executor: remote-agent
```

---

### Task 4: Frontend — rename Discogs tab, drop price columns, add Date Added

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/views/RecordBrowser.tsx`
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/test/recordBrowser.test.tsx`

**Interfaces:**
- Consumes: `Release.date_added: string | null` from Task 3's API response shape.
- Produces: `RecordScope = 'discogs' | 'wishlist'`. `RecordBrowser`'s `Props` shrinks to `{ scope: RecordScope, syncing?: boolean, onRefreshCollection?: () => void, syncGeneration?: number }` — every other prop (`onRefreshPrices`, `crawling`, `crawlingReleaseId`, `crawlEvents`, `crawlers`, `hiddenCrawlerIds`) is removed.

This task changes three files that must land together — `App.tsx` calls `RecordBrowser` with the old prop shape and `scope="collection"`, so the type/component changes below don't compile-check clean until `App.tsx` is updated in the same commit.

- [ ] **Step 1: Write the failing test — rewrite `recordBrowser.test.tsx`**

Replace the file in full:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react'
import RecordBrowser from '../views/RecordBrowser'

const getReleases = vi.fn()
const getArtists = vi.fn()

vi.mock('../api/client', () => ({
  getReleases: (...args: unknown[]) => getReleases(...args),
  getArtists: (...args: unknown[]) => getArtists(...args),
}))

beforeEach(() => {
  getReleases.mockReset()
  getArtists.mockReset()
  getReleases.mockResolvedValue({ total: 0, page: 1, per_page: 250, releases: [] })
  getArtists.mockResolvedValue([])
  localStorage.clear()
})

describe('RecordBrowser', () => {
  it('links the cover icon to Discogs and leaves the artist name as plain text, in tile view', async () => {
    getReleases.mockResolvedValue({
      total: 1, page: 1, per_page: 250,
      releases: [{
        discogs_id: 'r1', artist: 'Pink Floyd', title: 'The Wall', year: 1979, label: 'Harvest',
        format: 'Vinyl', discogs_price: null, cover_image_url: 'https://x/cover.jpg',
        discogs_url: 'https://discogs.com/r1', plex_url: null, plex_matched_at: null,
        last_synced: '', date_added: null,
      }],
    })
    localStorage.setItem('collectionViewMode_discogs', 'tiles')
    render(<RecordBrowser scope="discogs" />)
    const icon = await screen.findByAltText('The Wall')
    expect(icon.closest('a')?.getAttribute('href')).toBe('https://discogs.com/r1')
    const artistText = screen.getByText('Pink Floyd')
    expect(artistText.closest('a')).toBeNull()
  })

  it('links the cover icon to Discogs and leaves the artist name as plain text, in list view', async () => {
    getReleases.mockResolvedValue({
      total: 1, page: 1, per_page: 250,
      releases: [{
        discogs_id: 'r1', artist: 'Pink Floyd', title: 'The Wall', year: 1979, label: 'Harvest',
        format: 'Vinyl', discogs_price: null, cover_image_url: 'https://x/cover.jpg',
        discogs_url: 'https://discogs.com/r1', plex_url: null, plex_matched_at: null,
        last_synced: '', date_added: null,
      }],
    })
    render(<RecordBrowser scope="discogs" />)
    const icon = await screen.findByAltText('The Wall')
    expect(icon.closest('a')?.getAttribute('href')).toBe('https://discogs.com/r1')
    const artistText = screen.getByText('Pink Floyd')
    expect(artistText.closest('a')).toBeNull()
  })

  it('gives the placeholder icon link an accessible name when there is no cover art, in tile view', async () => {
    getReleases.mockResolvedValue({
      total: 1, page: 1, per_page: 250,
      releases: [{
        discogs_id: 'r1', artist: 'Pink Floyd', title: 'The Wall', year: 1979, label: 'Harvest',
        format: 'Vinyl', discogs_price: null, cover_image_url: '',
        discogs_url: 'https://discogs.com/r1', plex_url: null, plex_matched_at: null,
        last_synced: '', date_added: null,
      }],
    })
    localStorage.setItem('collectionViewMode_discogs', 'tiles')
    render(<RecordBrowser scope="discogs" />)
    const link = await screen.findByRole('link', { name: 'View Pink Floyd – The Wall on Discogs' })
    expect(link).toHaveAttribute('href', 'https://discogs.com/r1')
  })

  it('gives the placeholder icon link an accessible name when there is no cover art, in list view', async () => {
    getReleases.mockResolvedValue({
      total: 1, page: 1, per_page: 250,
      releases: [{
        discogs_id: 'r1', artist: 'Pink Floyd', title: 'The Wall', year: 1979, label: 'Harvest',
        format: 'Vinyl', discogs_price: null, cover_image_url: '',
        discogs_url: 'https://discogs.com/r1', plex_url: null, plex_matched_at: null,
        last_synced: '', date_added: null,
      }],
    })
    render(<RecordBrowser scope="discogs" />)
    const link = await screen.findByRole('link', { name: 'View Pink Floyd – The Wall on Discogs' })
    expect(link).toHaveAttribute('href', 'https://discogs.com/r1')
  })

  it('shows the Unmatched filter dropdown for the discogs scope but not wishlist', async () => {
    const { rerender } = render(<RecordBrowser scope="discogs" />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    expect(screen.getByRole('combobox')).toBeInTheDocument()
    rerender(<RecordBrowser scope="wishlist" />)
    await waitFor(() => expect(screen.queryByRole('combobox')).not.toBeInTheDocument())
  })

  it('passes unmatched to getReleases when the filter is set to Unmatched', async () => {
    render(<RecordBrowser scope="discogs" />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'unmatched' } })
    await waitFor(() => expect(getReleases).toHaveBeenLastCalledWith(expect.objectContaining({ unmatched: true })))
  })

  it('does not render a sync button when onRefreshCollection is not provided', async () => {
    render(<RecordBrowser scope="discogs" />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    expect(screen.queryByTitle('Sync collection from Discogs')).toBeNull()
  })

  it('calls onRefreshCollection when the sync button is clicked', async () => {
    const onRefreshCollection = vi.fn()
    render(<RecordBrowser scope="discogs" onRefreshCollection={onRefreshCollection} />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    screen.getByTitle('Sync collection from Discogs').click()
    expect(onRefreshCollection).toHaveBeenCalledTimes(1)
  })

  it('disables the sync button while syncing', async () => {
    render(<RecordBrowser scope="discogs" onRefreshCollection={() => {}} syncing />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    expect(screen.getByTitle('Sync collection from Discogs')).toBeDisabled()
  })

  it('labels the wishlist sync button distinctly from the collection one', async () => {
    const onRefreshCollection = vi.fn()
    render(<RecordBrowser scope="wishlist" onRefreshCollection={onRefreshCollection} />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    screen.getByTitle('Sync wishlist from Discogs').click()
    expect(onRefreshCollection).toHaveBeenCalledTimes(1)
  })

  it('refetches the artist nav list on every syncGeneration tick, not just on scope change', async () => {
    const { rerender } = render(<RecordBrowser scope="discogs" syncGeneration={0} />)
    await waitFor(() => expect(getArtists).toHaveBeenCalledTimes(1))
    rerender(<RecordBrowser scope="discogs" syncGeneration={1} />)
    await waitFor(() => expect(getArtists).toHaveBeenCalledTimes(2))
    rerender(<RecordBrowser scope="discogs" syncGeneration={2} />)
    await waitFor(() => expect(getArtists).toHaveBeenCalledTimes(3))
  })

  it('renders the Date Added column with a formatted date, or a dash when null', async () => {
    getReleases.mockResolvedValue({
      total: 2, page: 1, per_page: 250,
      releases: [
        {
          discogs_id: 'r1', artist: 'Pink Floyd', title: 'The Wall', year: 1979, label: 'Harvest',
          format: 'Vinyl', discogs_price: '$20', cover_image_url: '', discogs_url: '',
          plex_url: null, plex_matched_at: null, last_synced: '', date_added: '2024-03-15T10:00:00Z',
        },
        {
          discogs_id: 'r2', artist: 'Radiohead', title: 'Kid A', year: 2000, label: 'Parlophone',
          format: 'Vinyl', discogs_price: '$20', cover_image_url: '', discogs_url: '',
          plex_url: null, plex_matched_at: null, last_synced: '', date_added: null,
        },
      ],
    })
    render(<RecordBrowser scope="discogs" />)
    expect(await screen.findByText(new Date('2024-03-15T10:00:00Z').toLocaleDateString())).toBeInTheDocument()
    const row = (await screen.findByText('Radiohead')).closest('tr')!
    expect(within(row).getByText('—')).toBeInTheDocument()
  })

  it('sorts by Date Added when its header is clicked', async () => {
    render(<RecordBrowser scope="discogs" />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    fireEvent.click(screen.getByText(/Date Added/))
    await waitFor(() => expect(getReleases).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'date_added', order: 'asc' })))
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/test/recordBrowser.test.tsx`
Expected: FAIL — `scope="discogs"` isn't a valid `RecordScope` yet, `RecordBrowser` still requires `onRefreshPrices`, no "Date Added" text exists.

- [ ] **Step 3: Implement — `types.ts`**

In `frontend/src/api/types.ts`:

- Delete the `Listing` interface.
- In `Release`, delete `listings: Record<string, Listing | null>` and add `date_added: string | null`.
- Change `export type RecordScope = 'collection' | 'wishlist'` to `export type RecordScope = 'discogs' | 'wishlist'`.

- [ ] **Step 4: Implement — `RecordBrowser.tsx`**

Change the `Props` interface and function signature from:

```tsx
interface Props {
  scope: RecordScope
  onRefreshPrices: (releaseId: string) => void
  crawling?: boolean
  crawlingReleaseId?: string
  crawlEvents?: CrawlEvent[]
  crawlers?: Crawler[]
  hiddenCrawlerIds?: number[]
  syncing?: boolean
  onRefreshCollection?: () => void
  syncGeneration?: number
}

export default function RecordBrowser({ scope, onRefreshPrices, crawling, crawlingReleaseId, crawlEvents, crawlers = [], hiddenCrawlerIds = [], syncing, onRefreshCollection, syncGeneration }: Props) {
```

to:

```tsx
interface Props {
  scope: RecordScope
  syncing?: boolean
  onRefreshCollection?: () => void
  syncGeneration?: number
}

export default function RecordBrowser({ scope, syncing, onRefreshCollection, syncGeneration }: Props) {
```

Update the `Crawler`/`CrawlEvent` import line accordingly (drop both from the `import type` line at the top — neither is referenced anymore).

Delete the `crawlEvents`-consuming `useEffect` (the block starting `useEffect(() => {\n    if (!crawlEvents) return` and ending at its closing `}, [crawlEvents])`), and delete the `processedCount` ref declaration it uses.

Delete `const enabledCrawlers = crawlers.filter(...)`.

Change the two `scope === 'collection'` comparisons (the `unmatched` param in `load()`'s `getReleases` call, and the Unmatched `<select>`'s guard condition) to `scope === 'discogs'`.

In the table header, delete the `{enabledCrawlers.map((c) => ( ... ))}` block (the per-crawler `<th>`s) and add, in its place, a Date Added header matching the existing Year/Label/Format pattern:

```tsx
                <th
                  className="text-center"
                  aria-sort={sort === 'date_added' ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}
                >
                  <button type="button" onClick={() => toggleSort('date_added')} className={`${sortButtonClass} text-center`}>
                    Date Added {sort === 'date_added' ? (order === 'asc' ? '↑' : '↓') : ''}
                  </button>
                </th>
```

Also delete the trailing `<th className="px-3 py-2"></th>` that follows that block (it existed only to sit above the refresh-icon `<td>` column being removed from the body below — leaving it in place would make the header row have one more cell than each data row).

Change every `colSpan={8 + enabledCrawlers.length}` to `colSpan={8}` (two loading/empty rows) — the header now has a fixed 8 columns (cover, artist, title, year, label, format, price, date added) with no trailing empty column.

In the table body, delete the `{enabledCrawlers.map((c) => { ... })}` block (the per-crawler `<td>`s) and the trailing refresh-icon `<td>` (the one containing the `onClick={() => onRefreshPrices(r.discogs_id)}` button). Add, where the crawler `<td>`s were:

```tsx
                  <td className="px-3 py-2 text-gray-400">
                    {r.date_added ? new Date(r.date_added).toLocaleDateString() : '—'}
                  </td>
```

- [ ] **Step 5: Implement — `App.tsx`**

Change `type View = 'collection' | 'wishlist' | 'instock' | 'settings' | 'logs' | 'account'` to `type View = 'discogs' | 'wishlist' | 'instock' | 'settings' | 'logs' | 'account'`.

Change `const [view, setView] = useState<View>('collection')` to `useState<View>('discogs')`.

Change the nav button:

```tsx
          <button
            onClick={() => setView('collection')}
            className={`px-3 py-1.5 text-sm font-medium ${navButtonClass(view === 'collection')}`}
          >
            Collection
          </button>
```

to:

```tsx
          <button
            onClick={() => setView('discogs')}
            className={`px-3 py-1.5 text-sm font-medium ${navButtonClass(view === 'discogs')}`}
          >
            Discogs
          </button>
```

Change `<div className={view === 'collection' ? 'h-full' : 'hidden'}>` to `<div className={view === 'discogs' ? 'h-full' : 'hidden'}>`.

Change the two `<RecordBrowser>` call sites from:

```tsx
          <RecordBrowser
            scope="collection"
            onRefreshPrices={(id) => handleFindPrices(id)}
            crawling={crawling}
            crawlingReleaseId={crawlingReleaseId}
            crawlEvents={crawlEvents}
            crawlers={crawlers}
            hiddenCrawlerIds={hiddenCrawlerIds}
            syncing={syncing}
            onRefreshCollection={() => handleRefresh()}
            syncGeneration={syncGeneration}
          />
        </div>
        <div className={view === 'wishlist' ? 'h-full' : 'hidden'}>
          <RecordBrowser
            scope="wishlist"
            onRefreshPrices={(id) => handleFindPrices(id)}
            crawling={crawling}
            crawlingReleaseId={crawlingReleaseId}
            crawlEvents={crawlEvents}
            crawlers={crawlers}
            hiddenCrawlerIds={hiddenCrawlerIds}
            syncing={syncing}
            onRefreshCollection={() => handleRefreshWishlist()}
            syncGeneration={syncGeneration}
          />
```

to:

```tsx
          <RecordBrowser
            scope="discogs"
            syncing={syncing}
            onRefreshCollection={() => handleRefresh()}
            syncGeneration={syncGeneration}
          />
        </div>
        <div className={view === 'wishlist' ? 'h-full' : 'hidden'}>
          <RecordBrowser
            scope="wishlist"
            syncing={syncing}
            onRefreshCollection={() => handleRefreshWishlist()}
            syncGeneration={syncGeneration}
          />
```

Delete `const [crawlingReleaseId, setCrawlingReleaseId] = useState<string | undefined>(undefined)`.

In the SSE handler, delete `setCrawlingReleaseId(undefined)` from the `'complete' || 'stopped'` branch.

In `startCrawl`, delete `setCrawlingReleaseId(releaseId)` (keep the rest of the function — `releaseId` is still forwarded to `postCrawlStart`).

- [ ] **Step 6: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/test/recordBrowser.test.tsx`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/types.ts frontend/src/views/RecordBrowser.tsx frontend/src/App.tsx frontend/src/test/recordBrowser.test.tsx
```

Commit message body:

```
feat: rename Collection tab to Discogs, drop price columns, add Date Added

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: cli
ai-executor: remote-agent
```

---

### Task 5: Fix the remaining frontend tests broken by Task 4

**Files:**
- Delete: `frontend/src/test/staleListingClear.test.tsx`
- Modify: `frontend/src/test/plexLink.test.tsx`
- Modify: `frontend/src/test/syncRefetch.test.tsx`
- Modify: `frontend/src/test/wishlistRefresh.test.tsx`
- Modify: `frontend/src/test/crawlStatusBar.test.tsx`
- Modify: `frontend/src/test/viewRenderChurn.test.tsx`

**Interfaces:** none new — this task only migrates existing tests to Task 4's contract.

- [ ] **Step 1: Delete `staleListingClear.test.tsx`**

```bash
rm frontend/src/test/staleListingClear.test.tsx
```

Every test in this file drove `RecordBrowser`'s crawl-event listener effect, which Task 4 deleted — there's no replacement behavior to test.

- [ ] **Step 2: Fix `plexLink.test.tsx`**

In each of the two `Release` fixtures (`matchedRelease`, `unmatchedRelease`), replace `listings: {},` with `date_added: null,`.

Replace every `<RecordBrowser scope="collection" onRefreshPrices={() => {}} />` with `<RecordBrowser scope="discogs" />` (3 occurrences).

- [ ] **Step 3: Fix `syncRefetch.test.tsx`**

Replace every `<RecordBrowser scope="collection" onRefreshPrices={() => {}} syncGeneration={N} />` with `<RecordBrowser scope="discogs" syncGeneration={N} />` (6 occurrences, `N` varying: 0, 1, 2, 0, 1, 1).

- [ ] **Step 4: Fix `wishlistRefresh.test.tsx`**

In the `release` fixture, replace `listings: {},` with `date_added: null,`.

No `scope="collection"` literal exists in this file (it only renders `<App />` and clicks into the Wishlist tab), so no scope rename is needed here.

- [ ] **Step 5: Run these three to verify they pass**

Run: `cd frontend && npx vitest run src/test/plexLink.test.tsx src/test/syncRefetch.test.tsx src/test/wishlistRefresh.test.tsx`
Expected: PASS

- [ ] **Step 6: Fix `crawlStatusBar.test.tsx`**

In the `release` fixture, replace `listings: {},` with `date_added: null,`.

Replace the `clickRefreshAndGetSource` helper:

```tsx
async function clickRefreshAndGetSource() {
  const [button] = await screen.findAllByTitle('Refresh prices for this record')
  fireEvent.click(button)
  await waitFor(() => expect(getLastCrawlSource()).toBeDefined())
  return getLastCrawlSource()
}
```

with:

```tsx
async function getCrawlSourceOnMount() {
  await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
  return getLastCrawlSource()
}
```

(`RecordBrowser`'s per-row "Refresh prices for this record" button no longer exists; the SSE connection this helper needs already opens on component mount regardless, as this file's last two tests — `'shows page/count as soon as a page is fetched...'` and `'still shows the processed running total...'` — already demonstrate by not clicking anything.)

Update every call site (6 of them) from `await clickRefreshAndGetSource()` to `await getCrawlSourceOnMount()`. Also update `fireEvent` import if it becomes unused elsewhere in the file — check remaining usages (the `Dismiss` button clicks still use `fireEvent.click`, so the import stays).

In the file's last test, change `expect(screen.getByText('Collection')).toBeInTheDocument()` to `expect(screen.getByText('Discogs')).toBeInTheDocument()` (the nav label Task 4 renamed).

- [ ] **Step 7: Run it to verify it passes**

Run: `cd frontend && npx vitest run src/test/crawlStatusBar.test.tsx`
Expected: PASS

- [ ] **Step 8: Fix `viewRenderChurn.test.tsx`**

In the `release` fixture, replace `listings: {},` with `date_added: null,`.

Change the import line `import { render, screen, fireEvent, waitFor } from '@testing-library/react'` to `import { render, screen, waitFor } from '@testing-library/react'` — `fireEvent`'s only use in this file is the button click being deleted below, and `tsconfig.app.json` has `noUnusedLocals: true`, so an unused import fails `npm run build`.

Replace the `clickRefreshAndGetSource` helper the same way as Step 6:

```tsx
async function getCrawlSourceOnMount() {
  await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
  return getLastCrawlSource()
}
```

In the test body, replace:

```tsx
    // Both the Collection and Wishlist RecordBrowser instances render this
    // column header simultaneously, since App keeps every view mounted.
    await screen.findAllByText('Amazon')
    await waitFor(() => expect(stockSpy).toHaveBeenCalled())
```

with:

```tsx
    // Settle the post-login poll's one-time crawler fetch (it swaps in a
    // fresh `crawlers` array, which legitimately re-renders Settings once)
    // before snapshotting, so that unrelated startup settling isn't mistaken
    // for churn caused by the crawl event stream this test actually targets.
    await waitFor(() => expect(settingsSpy).toHaveBeenCalled())
    await waitFor(() => expect(stockSpy).toHaveBeenCalled())
```

Replace:

```tsx
    const src = await clickRefreshAndGetSource()
    src.emit({ status: 'started', total: 2, id: 1 })
    src.emit({ status: 'found', discogs_id: 'r1', release: 'The Wall', artist: 'Pink Floyd', site: 'Amazon', price: 24.99 })
    src.emit({ status: 'found', discogs_id: 'r1', release: 'The Wall', artist: 'Pink Floyd', site: 'eBay', price: 19.99 })
    await waitFor(() => expect(screen.getByText('eBay')).toBeInTheDocument())
```

with:

```tsx
    const src = await getCrawlSourceOnMount()
    src.emit({ status: 'started', total: 2, id: 1 })
    src.emit({ status: 'found', discogs_id: 'r1', release: 'The Wall', artist: 'Pink Floyd', site: 'Amazon', price: 24.99 })
    src.emit({ status: 'found', discogs_id: 'r1', release: 'The Wall', artist: 'Pink Floyd', site: 'eBay', price: 19.99 })
    await waitFor(() => expect(screen.getByText(/2\/2/)).toBeInTheDocument())
```

(The App-level crawl status banner's `X/total` progress count — the same signal `crawlStatusBar.test.tsx`'s `'shows X/total progress count'` test already asserts on — replaces the removed price-column cell as this test's proof that the second crawl event actually landed.)

- [ ] **Step 9: Run it to verify it passes**

Run: `cd frontend && npx vitest run src/test/viewRenderChurn.test.tsx`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add -A frontend/src/test/
```

Commit message body:

```
test: migrate remaining frontend tests to the Discogs tab rename

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: cli
ai-executor: remote-agent
```

---

### Task 6: Full-repo verification and version bump

**Files:** `backend/version.py` only (plus none — this task is otherwise verification-only)

- [ ] **Step 1: Run the full backend test suite**

Run: `cd backend && pytest`
Expected: all tests PASS

- [ ] **Step 2: Run the full frontend test suite**

Run: `cd frontend && npm run test`
Expected: all test files PASS

- [ ] **Step 3: Run the TypeScript build**

Run: `cd frontend && npm run build`
Expected: exits 0, no type errors

- [ ] **Step 4: Run the linter**

Run: `cd frontend && npm run lint`
Expected: exits 0, no lint errors

- [ ] **Step 5: Manual verification**

Run the backend (`cd backend && pip install -e ".[dev]" && uvicorn main:app --reload --port 8000`) and frontend (`cd frontend && npm install && npm run dev`) per `CLAUDE.md`'s "Running" section, log in, and:

- Confirm the first nav tab reads "Discogs" (not "Collection"), and clicking it shows your Discogs collection with a "Date Added" column instead of any per-site price columns.
- Click "Date Added" to sort by it; confirm the sort arrow appears and the order changes on a second click.
- Switch to the Wishlist tab; confirm it also has no price columns and no per-row refresh icon, matching the Discogs tab's column set.
- Click "Sync collection from Discogs" (Discogs tab) and confirm it still syncs and populates Date Added for items that have one.
- Click "Sync wishlist from Discogs" (Wishlist tab); confirm it completes without error.
- Confirm the Store tab is unaffected — same items, filters, and columns as before this change.

Stop both dev servers (Ctrl-C) when done.

- [ ] **Step 6: Bump the version per `CLAUDE.md`'s versioning rule**

Read `backend/version.py`, increment the minor version (`2.10` → `2.11`), following the existing pattern in that file.

- [ ] **Step 7: Commit the version bump**

```bash
git add backend/version.py
```

Commit message body:

```
chore: bump version for Discogs tab rename

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: cli
ai-executor: remote-agent
```

---

## Self-Review Notes

- **Spec coverage:** every "Decisions carried from brainstorming" item in the spec has a task: Wishlist loses price columns (Task 4), wishlist sync stops enqueueing (Task 2), separate `collection_date_added`/`wishlist_date_added` columns (Task 1), internal `collection`→`discogs` rename (Task 4), no new migration tooling (Task 1's `ADD COLUMN IF NOT EXISTS`). The spec's "Out of scope" list (Store tab reorganization, the new intersection Collection tab, crawl-target expansion) has no corresponding task, correctly.
- **Type consistency:** `upsert_library_item`'s two new kwarg names (`collection_date_added`, `wishlist_date_added`, Task 1) are the exact names `crawl_manager._sync_collection` passes in Task 2 and the exact column names `get_library_releases` reads in Task 3. `Release.date_added` (Task 4's `types.ts` change) matches the field name `get_library_releases` (Task 3) actually returns. `RecordScope`'s `'discogs'` value (Task 4) matches every `scope="discogs"` render call added across Tasks 4 and 5.
- **Caught during self-review:** the spec's "internal rename" decision talked about `RecordScope` and the `/api/releases?scope=` param, but grepping the backend for the literal `"collection"` turned up a second, easy-to-miss call site — `get_distinct_artists` (backs `/api/artists`, the artist sidebar) has the exact same `scope == "collection"` check as `get_library_releases`. Both are now renamed together in Task 3, along with the one existing test (`test_library_maintenance.py`) that exercised the old value — otherwise the Discogs tab's artist sidebar would have silently stopped filtering the moment Task 4's frontend started sending `scope=discogs`. Also caught: the table header's trailing empty `<th>` (originally sitting above the refresh-button column) needed explicit removal in Task 4, or the header row would have one more cell than each data row once the per-crawler `<th>`s and that trailing one were gone but only a single Date Added `<th>` was added back.
- **Scope:** one subsystem end to end (schema → sync → query → API → UI → tests), ordered so each task's tests pass standalone before the next task depends on it — Task 4 is the one exception, where three files must land in the same commit because `RecordBrowser`'s prop-shape change and `App.tsx`'s call sites are not independently compilable; this is called out explicitly at the top of that task. No further decomposition needed.
