# Track Tab Wantlist Filter (+ Tab Rename) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the store/library intersection tab to match Discogs wantlist items as well as collection items, behind an All/Collection/Wantlist dropdown, and rename the four browse tabs to Collection, Wantlist, Store, Track.

**Architecture:** `_owned_match_fragment` in `backend/db.py` becomes `_library_match_fragment(user_id_param, library_scope)`, parameterized by which `library_items` membership flag to require. `get_stock_items`/`get_distinct_stock_artists` swap their `overlapping: bool` for `library_scope: Optional[str]` (`'collection'`/`'wishlist'`/`'all'`/`None`). The "what I paid" price subquery and the Recommended filter's `_not_owned_clause` stay pinned to `'collection'` scope, so a wantlist-only row gets a NULL `discogs_price` for free and Recommended keeps meaning "not already owned." On the frontend, the four tabs are renamed through frontend identifiers only — `client.ts` translates to the unchanged backend vocabulary — and `StockBrowser` grows a scope-dependent filter dropdown.

**Tech Stack:** FastAPI + psycopg 3 (raw SQL, no ORM), pytest against a real Postgres via `TEST_DATABASE_URL`, React 19 + TypeScript + Vite + Tailwind, vitest + @testing-library/react.

**Spec:** [`docs/specifications/shaping/2026-08-10-collection-wishlist-filter-design.md`](../shaping/2026-08-10-collection-wishlist-filter-design.md)

**Branch:** `worktree-collection-wishlist-filter`, stacked on `worktree-collection-price-paid` (PR #76), itself stacked on `worktree-store-collection-split` (PR #75). Merge order is #75 → #76 → this. Rebase on each predecessor after it lands.

**Interim-state note:** between Task 1 and Task 5 the frontend still sends the removed `overlapping` query param, so the Track tab renders unfiltered at runtime during that window. Automated tests stay green throughout (frontend tests mock `api/client`). The branch is coherent as a whole; individual commits inside it are not independently deployable. Do not "fix" this by reordering — the backend must accept `library_scope` before the frontend can send it.

**Before starting:** confirm the baseline is green so any later failure is attributable to this plan.

```bash
cd backend && pytest
```

```bash
cd frontend && npm test
```

Both must pass. `pytest` needs a running Postgres and `TEST_DATABASE_URL` set — see `README.md`'s environment-variable table.

---

## File Structure

**Modified — backend:**
- `backend/db.py` — `_LIBRARY_MEMBERSHIP` dict and `_library_match_fragment` replace `_owned_match_fragment`; `get_stock_items` and `get_distinct_stock_artists` take `library_scope`; `upsert_catalog_release` takes `preserve_price`.
- `backend/routers/stock.py` — `/stock` and `/stock/artists` take `library_scope` instead of `overlapping`.
- `backend/crawl_manager.py` — the wantlist sync loop passes `preserve_price=True`.
- `backend/version.py` — `"3.1"` → `"3.2"`.

**Modified — frontend:**
- `frontend/src/api/types.ts` — `RecordScope` values renamed; new `StockScope`, `LibraryScope`.
- `frontend/src/api/client.ts` — scope-translation maps; `getStock`/`getStockArtists` take `libraryScope`.
- `frontend/src/App.tsx` — `View` union values, four nav labels, wantlist handler and status strings.
- `frontend/src/views/RecordBrowser.tsx` — `'wishlist'` → `'wantlist'` scope checks and user-facing strings.
- `frontend/src/views/StockBrowser.tsx` — `scope` prop type, widened filter state, scope-dependent dropdown, filter-aware empty states.

**Modified — tests:**
- `backend/tests/test_stock_crud.py`, `backend/tests/test_stock_router.py`, `backend/tests/test_catalog_crud.py`, `backend/tests/test_crawl_manager.py`
- `frontend/src/test/stockBrowser.test.tsx`, `inStockTab.test.tsx`, `recordBrowser.test.tsx`, `plexLink.test.tsx`, `syncRefetch.test.tsx`, `wishlistRefresh.test.tsx`, `crawlStatusBar.test.tsx`, `staleSignupLink.test.tsx`, `client.test.ts`

**Modified — docs:**
- `docs/specifications/shaping/2026-08-08-discogs-tab-rename-design.md`, `2026-08-08-store-collection-split-design.md`, `2026-08-09-collection-price-paid-design.md`, `2026-08-10-collection-wishlist-filter-design.md` (drift amendments, Task 6)

No new files. No `.agents/` directory exists in this repo, so there are no `INPUTS.md`/`OUTPUTS.md`/`INSTRUCTIONS.md` tasks. `README.md` documents authentication, deployment, and environment variables — not tab names or the `/api/stock` query shape — so it needs no change; `CLAUDE.md`'s invariants cover crawl queueing, listings population, and wishlist-removal semantics, none of which this touches.

---

## Task 1: `library_scope` on `get_stock_items` and `/stock`

Replaces `overlapping: bool` with a three-valued scope, parameterizes the match fragment, and pins the price subquery to collection scope. The router changes in the same task because `routers/stock.py` passes the kwarg through — splitting them would leave the backend raising `TypeError` on every `/api/stock` request.

**Files:**
- Modify: `backend/db.py:873-887` (fragment helpers), `backend/db.py:893-950` (`get_stock_items`)
- Modify: `backend/routers/stock.py:29-48` (`list_stock`)
- Test: `backend/tests/test_stock_crud.py`, `backend/tests/test_stock_router.py`

- [ ] **Step 1: Write the failing tests**

Add these to `backend/tests/test_stock_crud.py`, after the existing `test_get_stock_items_sort_by_source` test. They follow the file's existing idiom: `admin_conn` for setup, then `db.user_scope(...)` for the read.

```python
def _seed_collection_and_wantlist(admin_conn):
    """Alice owns Artist A / Album A (paid 20.00) and wants Artist B / Album B
    (catalog price 30.00, which she has not paid). Both are in stock."""
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
        {"artist": "Artist B", "title": "Album B", "url": "https://x/2", "price": 15.0, "currency": "USD"},
    ])
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "Artist A", "title": "Album A", "year": None, "label": None,
        "format": None, "discogs_price": "20.00", "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r2", "artist": "Artist B", "title": "Album B", "year": None, "label": None,
        "format": None, "discogs_price": "30.00", "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True)
    db.upsert_library_item(admin_conn, alice["id"], "r2", in_wishlist=True)
    admin_conn.commit()
    return alice


def test_get_stock_items_library_scope_wishlist_returns_only_wantlist_matches(admin_conn):
    alice = _seed_collection_and_wantlist(admin_conn)

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], library_scope="wishlist")
        assert result["total"] == 1
        assert result["items"][0]["artist"] == "Artist B"


def test_get_stock_items_library_scope_collection_returns_only_collection_matches(admin_conn):
    alice = _seed_collection_and_wantlist(admin_conn)

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], library_scope="collection")
        assert result["total"] == 1
        assert result["items"][0]["artist"] == "Artist A"


def test_get_stock_items_library_scope_all_returns_the_union(admin_conn):
    alice = _seed_collection_and_wantlist(admin_conn)

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], library_scope="all")
        assert result["total"] == 2
        assert [i["artist"] for i in result["items"]] == ["Artist A", "Artist B"]


def test_get_stock_items_library_scope_none_and_unrecognized_do_not_filter(admin_conn):
    alice = _seed_collection_and_wantlist(admin_conn)

    with db.user_scope(alice["id"]) as conn:
        assert db.get_stock_items(conn, alice["id"], library_scope=None)["total"] == 2
        # A hand-crafted query string must not be able to raise -- the router
        # does no validation of its own, so this normalization is the only gate.
        assert db.get_stock_items(conn, alice["id"], library_scope="bogus")["total"] == 2


def test_get_stock_items_library_scope_all_does_not_duplicate_a_release_in_both_lists(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
    ])
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "Artist A", "title": "Album A", "year": None, "label": None,
        "format": None, "discogs_price": "20.00", "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True, in_wishlist=True)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], library_scope="all")
        assert result["total"] == 1
        assert len([i for i in result["items"] if i["is_own"]]) == 1
        # Owned as well as wanted, so the paid price still applies.
        assert result["items"][0]["discogs_price"] == "20.00"


def test_get_stock_items_discogs_price_is_null_for_a_wantlist_only_match(admin_conn):
    alice = _seed_collection_and_wantlist(admin_conn)

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], library_scope="wishlist")
        # catalog.discogs_price for r2 is "30.00", but the price subquery is
        # pinned to collection scope: she has not bought this one.
        assert result["items"][0]["discogs_price"] is None

        result = db.get_stock_items(conn, alice["id"], library_scope="all")
        by_artist = {i["artist"]: i["discogs_price"] for i in result["items"]}
        assert by_artist == {"Artist A": "20.00", "Artist B": None}


def test_get_stock_items_sort_by_discogs_price_under_wishlist_scope_does_not_error(admin_conn):
    alice = _seed_collection_and_wantlist(admin_conn)

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(
            conn, alice["id"], library_scope="wishlist", sort="discogs_price", order="asc"
        )
        assert result["total"] == 1


def test_get_stock_items_recommended_excludes_owned_but_not_merely_wanted(admin_conn):
    alice = _seed_collection_and_wantlist(admin_conn)

    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [
            {"item_key": db.compute_item_key("Artist A", "Album A", "https://x/1"),
             "recommended": True, "reason": "x"},
            {"item_key": db.compute_item_key("Artist B", "Album B", "https://x/2"),
             "recommended": True, "reason": "y"},
        ])
        result = db.get_stock_items(conn, alice["id"], recommended=True)
        # Owning it disqualifies it; merely wanting it does not.
        assert [i["artist"] for i in result["items"]] == ["Artist B"]
```

Then update the existing tests that pass the removed kwarg. Rename each function as shown and swap the kwarg:

| Line | Rename `def` to | Kwarg change |
|---|---|---|
| 299 | `test_get_stock_items_recommended_excludes_items_matching_users_collection` | none — this test never passed `overlapping`, it was misnamed |
| 327 | `test_get_stock_items_library_scope_collection_returns_only_items_matching_users_collection` | `overlapping=False` → `library_scope=None`; `overlapping=True` → `library_scope="collection"` |
| 454 | `test_get_stock_items_library_scope_collection_includes_comparison_rows_for_owned_items` | `overlapping=True` → `library_scope="collection"` |
| 502 | unchanged | `overlapping=True` → `library_scope="collection"` |
| 539 | unchanged | `overlapping=True` → `library_scope="collection"` |
| 565 | unchanged | `overlapping=True` → `library_scope="collection"` |
| 594 | `test_get_stock_items_sort_by_discogs_price_falls_back_to_artist_when_no_library_scope` | `overlapping=False` → `library_scope=None` |

And in `backend/tests/test_stock_router.py`, change line 277 and add a wantlist case immediately after that test:

```python
    r = client.get("/api/stock", params={"library_scope": "collection"})
```

```python
def test_list_stock_library_scope_wishlist_matches_wantlist_items(pg_test_db, authed_client_factory):
    crawler_id = _make_crawler()
    with db.get_admin_pool().connection() as conn:
        db.replace_stock_items(conn, crawler_id, [
            {"artist": "Rob Zombie", "title": "The Great Satan", "price": 31.99, "currency": "USD", "url": "https://x/1"},
        ])
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "Rob Zombie", "title": "The Great Satan", "year": None, "label": None,
            "format": None, "discogs_price": "20.00", "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.upsert_library_item(conn, user["id"], "r1", in_wishlist=True)
        conn.commit()

    client = authed_client_factory(user["id"])
    r = client.get("/api/stock", params={"library_scope": "wishlist"})
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["discogs_price"] is None

    r = client.get("/api/stock", params={"library_scope": "collection"})
    assert r.json()["items"] == []
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && pytest tests/test_stock_crud.py tests/test_stock_router.py -v
```

Expected: the new and renamed tests FAIL with `TypeError: get_stock_items() got an unexpected keyword argument 'library_scope'`.

- [ ] **Step 3: Replace the match-fragment helpers**

In `backend/db.py`, replace `_owned_match_fragment` and `_not_owned_clause` (lines 873-887) with:

```python
_LIBRARY_MEMBERSHIP = {
    "collection": "li.in_collection = TRUE",
    "wishlist": "li.in_wishlist = TRUE",
    "all": "(li.in_collection = TRUE OR li.in_wishlist = TRUE)",
}


def _library_match_fragment(user_id_param: str, library_scope: str) -> str:
    # Exact-or-prefix-with-space title match, not exact-only: stock listings
    # often append edition/format qualifiers the catalog title doesn't have
    # (e.g. catalog "Kid A" vs. stock listing "Kid A (Deluxe Reissue)"), so a
    # strict equality would treat an already-owned release as still unowned.
    return f"""FROM library_items li
        JOIN catalog c ON c.discogs_id = li.discogs_id
        WHERE li.user_id = {user_id_param}
          AND {_LIBRARY_MEMBERSHIP[library_scope]}
          AND LOWER(c.artist) = LOWER(s.artist)
          AND (LOWER(s.title) = LOWER(c.title) OR LOWER(s.title) LIKE LOWER(c.title) || ' %%')"""


# Recommended items are defined as ones the user doesn't already own. A release
# merely on the wantlist stays recommendable, so this stays collection-scoped.
def _not_owned_clause(user_id_param: str) -> str:
    return f"NOT EXISTS (SELECT 1 {_library_match_fragment(user_id_param, 'collection')})"
```

- [ ] **Step 4: Swap `get_stock_items`' parameter**

In the signature, replace `overlapping: bool = False,` with `library_scope: Optional[str] = None,`.

Then replace the opening of the body (the `order_sql`/`sort_expr` block) with:

```python
    if library_scope not in _LIBRARY_MEMBERSHIP:
        library_scope = None
    order_sql = "DESC" if order.lower() == "desc" else "ASC"
    if sort == "discogs_price" and library_scope is not None:
        sort_expr = """(SELECT (regexp_match(c.discogs_price, '\\d+\\.?\\d*'))[1]::numeric
                        {match} LIMIT 1)""".format(
            match=_library_match_fragment("%(user_id)s", "collection")
        )
    elif sort == "source":
        sort_expr = "cr.site_name"
    else:
        sort_col = sort if sort in _STOCK_ALLOWED_SORT else "artist"
        sort_expr = f"s.{sort_col}"
```

Replace the membership condition — this drops the `.replace("NOT EXISTS", "EXISTS")` string surgery, which only existed because the fragment wasn't scope-aware:

```python
    if library_scope:
        conditions.append(f"EXISTS (SELECT 1 {_library_match_fragment('%(user_id)s', library_scope)})")
```

And in the main `SELECT`, change the `discogs_price` subquery line to pin the scope:

```python
               (SELECT c.discogs_price {_library_match_fragment('%(user_id)s', 'collection')} LIMIT 1) AS discogs_price
```

Leave `_STOCK_ALLOWED_SORT` alone — `"discogs_price"` is deliberately not a member, which is what makes a Store-tab request for that sort fall back to `artist` rather than resolving to a nonexistent `s.discogs_price` column.

- [ ] **Step 5: Swap the router parameter**

In `backend/routers/stock.py`'s `list_stock`, replace `overlapping: bool = Query(False),` with `library_scope: Optional[str] = Query(None),` and update the call:

```python
        return db.get_stock_items(
            conn, user_id, search=search, artist=artist, sort=sort, order=order,
            page=page, per_page=per_page, library_scope=library_scope, recommended=recommended,
            exclude_crawler_ids=exclude_crawler_ids,
        )
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
cd backend && pytest tests/test_stock_crud.py tests/test_stock_router.py -v
```

Expected: PASS.

- [ ] **Step 7: Run the full backend suite**

```bash
cd backend && pytest
```

Expected: PASS. `get_unjudged_stock_items` and `get_missing_releases` both call `_not_owned_clause`, whose signature and semantics are unchanged, so their tests should be unaffected — if one fails, the fragment refactor changed behavior it shouldn't have.

- [ ] **Step 8: Commit**

```bash
git add backend/db.py backend/routers/stock.py backend/tests/test_stock_crud.py backend/tests/test_stock_router.py
```

Write the message to a file and commit with the repo's required AI trailers — see `CLAUDE.md`'s "Commits — AI attribution trailers" section, and prefer the `sdlc:commit` skill's `commit-with-cleanup.sh` helper. Subject: `feat: match stock items against the wantlist via library_scope`.

---

## Task 2: `library_scope` on `get_distinct_stock_artists` and `/stock/artists`

The artist sidebar must filter to the same set as the table, or selecting an artist yields an empty list.

**Files:**
- Modify: `backend/db.py:992-1011` (`get_distinct_stock_artists`)
- Modify: `backend/routers/stock.py:51-65` (`list_stock_artists`)
- Test: `backend/tests/test_stock_crud.py`

- [ ] **Step 1: Write the failing tests**

Add after the existing `test_get_distinct_stock_artists_overlapping_filters_to_owned_artists` (line 713), and rename that existing test to `test_get_distinct_stock_artists_library_scope_collection_filters_to_owned_artists`, changing its `overlapping=True` to `library_scope="collection"`:

```python
def test_get_distinct_stock_artists_library_scope_wishlist_filters_to_wanted_artists(admin_conn):
    alice = _seed_collection_and_wantlist(admin_conn)

    with db.user_scope(alice["id"]) as conn:
        assert db.get_distinct_stock_artists(conn, alice["id"], library_scope="wishlist") == ["Artist B"]
        assert db.get_distinct_stock_artists(conn, alice["id"], library_scope="collection") == ["Artist A"]
        assert db.get_distinct_stock_artists(conn, alice["id"], library_scope="all") == ["Artist A", "Artist B"]
        assert db.get_distinct_stock_artists(conn, alice["id"], library_scope=None) == ["Artist A", "Artist B"]
        assert db.get_distinct_stock_artists(conn, alice["id"], library_scope="bogus") == ["Artist A", "Artist B"]
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && pytest tests/test_stock_crud.py -k distinct_stock_artists -v
```

Expected: FAIL with `TypeError: get_distinct_stock_artists() got an unexpected keyword argument 'library_scope'`.

- [ ] **Step 3: Swap the parameter**

In `backend/db.py`, change the signature's `overlapping: bool = False` to `library_scope: Optional[str] = None`, and replace the membership condition:

```python
def get_distinct_stock_artists(conn, user_id: int, library_scope: Optional[str] = None, recommended: bool = False,
    exclude_crawler_ids: Optional[list[int]] = None,
) -> list[str]:
    if library_scope not in _LIBRARY_MEMBERSHIP:
        library_scope = None
    conditions = []
    params: dict = {"user_id": user_id}
    if library_scope:
        conditions.append(f"EXISTS (SELECT 1 {_library_match_fragment('%(user_id)s', library_scope)})")
```

Leave the rest of the function (the `recommended` block, `exclude_crawler_ids`, the query) untouched.

- [ ] **Step 4: Swap the router parameter**

In `backend/routers/stock.py`'s `list_stock_artists`, replace `overlapping: bool = Query(False),` with `library_scope: Optional[str] = Query(None),` and update the call to pass `library_scope=library_scope`.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd backend && pytest tests/test_stock_crud.py tests/test_stock_router.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/db.py backend/routers/stock.py backend/tests/test_stock_crud.py
```

Subject: `feat: filter the stock artist sidebar by library_scope`. Same trailer requirement as Task 1.

---

## Task 3: Stop wantlist syncs from wiping `catalog.discogs_price`

`catalog` is global (keyed on `discogs_id` alone, no `user_id`), and `upsert_catalog_release`'s conflict clause does an unconditional `discogs_price = EXCLUDED.discogs_price`. The wantlist loop calls `parse_release(item, price_field_id=None)`, which always yields `discogs_price = None`, so every wantlist sync nulls the stored price for each release it touches. Because the wantlist loop runs *after* the collection loop in the same sync, a release in both lists gets its price written then nulled in one run; under `mode="new"` existing collection items skip `upsert_catalog_release` entirely, so the value never returns. Symptom: `—` in the Track tab's Price column for a record the user owns and paid for.

**Files:**
- Modify: `backend/db.py:356-371` (`upsert_catalog_release`)
- Modify: `backend/crawl_manager.py:469` (wantlist loop's call)
- Test: `backend/tests/test_catalog_crud.py`, `backend/tests/test_crawl_manager.py`

- [ ] **Step 1: Write the failing unit test**

Add to `backend/tests/test_catalog_crud.py`, after `test_upsert_catalog_release_inserts_then_updates`:

```python
def test_upsert_catalog_release_preserve_price_keeps_the_stored_price(admin_conn):
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "d1", "artist": "A", "title": "T", "year": 1999,
        "label": "L", "format": "LP", "discogs_price": "$10", "barcode": "123",
        "cover_image_url": "http://x/cover.jpg", "discogs_url": "http://x/release/d1",
    })
    admin_conn.commit()

    # The wantlist sync path can't know the price -- it never reads the custom
    # field -- so it must not overwrite what a collection sync stored.
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "d1", "artist": "A2", "title": "T2", "year": 2005,
        "label": "L2", "format": "CD", "discogs_price": None, "barcode": "456",
        "cover_image_url": "http://x/cover2.jpg", "discogs_url": "http://x/release/d1b",
    }, preserve_price=True)
    admin_conn.commit()

    row = db.get_catalog_release(admin_conn, "d1")
    assert row["discogs_price"] == "$10"
    # Every other column still updates.
    assert row["artist"] == "A2"
    assert row["title"] == "T2"
    assert row["format"] == "CD"


def test_upsert_catalog_release_default_still_overwrites_price_with_none(admin_conn):
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "d1", "artist": "A", "title": "T", "year": 1999,
        "label": "L", "format": "LP", "discogs_price": "$10", "barcode": "123",
        "cover_image_url": "http://x/cover.jpg", "discogs_url": "http://x/release/d1",
    })
    admin_conn.commit()

    # A collection sync legitimately writes None when the user cleared the
    # custom field on Discogs, or has no field named "Price" at all -- so the
    # default must stay a plain overwrite, not a COALESCE.
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "d1", "artist": "A", "title": "T", "year": 1999,
        "label": "L", "format": "LP", "discogs_price": None, "barcode": "123",
        "cover_image_url": "http://x/cover.jpg", "discogs_url": "http://x/release/d1",
    })
    admin_conn.commit()

    assert db.get_catalog_release(admin_conn, "d1")["discogs_price"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && pytest tests/test_catalog_crud.py -k preserve_price -v
```

Expected: FAIL with `TypeError: upsert_catalog_release() got an unexpected keyword argument 'preserve_price'`.

- [ ] **Step 3: Add the parameter**

Replace `upsert_catalog_release` in `backend/db.py` with:

```python
def upsert_catalog_release(conn, data: dict, preserve_price: bool = False):
    # catalog is global (no user_id), and discogs_price comes from a per-user
    # custom collection field. A caller that never read that field -- the
    # wantlist sync -- must leave the stored value alone rather than write its
    # own None over another sync's (or another user's) real price. This is a
    # per-call-site decision, not a blanket COALESCE: a collection sync writing
    # None means the user genuinely cleared the field, and must still clear it.
    price_assignment = "" if preserve_price else "discogs_price = EXCLUDED.discogs_price,"
    conn.execute(
        f"""
        INSERT INTO catalog (discogs_id, artist, title, year, label, format, discogs_price,
                              barcode, cover_image_url, discogs_url, last_synced)
        VALUES (%(discogs_id)s, %(artist)s, %(title)s, %(year)s, %(label)s, %(format)s,
                %(discogs_price)s, %(barcode)s, %(cover_image_url)s, %(discogs_url)s, CURRENT_TIMESTAMP)
        ON CONFLICT (discogs_id) DO UPDATE SET
            artist = EXCLUDED.artist, title = EXCLUDED.title, year = EXCLUDED.year,
            label = EXCLUDED.label, format = EXCLUDED.format, {price_assignment}
            barcode = EXCLUDED.barcode, cover_image_url = EXCLUDED.cover_image_url,
            discogs_url = EXCLUDED.discogs_url, last_synced = CURRENT_TIMESTAMP
        """,
        data,
    )
```

`preserve_price` only affects the `DO UPDATE` branch. On a first insert the supplied `discogs_price` (`None` from the wantlist path) is still written, which is correct — there was nothing to preserve.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && pytest tests/test_catalog_crud.py -v
```

Expected: PASS, including the pre-existing `test_upsert_catalog_release_inserts_then_updates`.

- [ ] **Step 5: Write the failing integration test**

Add to `backend/tests/test_crawl_manager.py`, after `test_sync_collection_wishlist_captures_date_added_and_does_not_enqueue`. It reuses that test's monkeypatch idiom exactly:

```python
async def test_sync_collection_wishlist_does_not_wipe_a_collection_discogs_price(pg_schema, monkeypatch):
    import config
    import crawl_manager as crawl_manager_module
    import discogs
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "k")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "s")
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", "kL8mN2pQ7rT5vX9yB3cF6hJ1kM4nP8sU2wZ5aD7eG0i=")
    monkeypatch.setattr(crawl_manager_module.time, "sleep", lambda *a, **k: None)

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
        # An earlier collection sync already recorded what she paid.
        db.upsert_catalog_release(conn, {
            "discogs_id": "r111", "artist": "Artist", "title": "Album", "year": 2020,
            "label": None, "format": None, "discogs_price": "42.50", "barcode": None,
            "cover_image_url": None, "discogs_url": None,
        })
        conn.commit()

    manager = CrawlManager()
    await manager._sync_collection(user["id"], "all", "wishlist")

    with db.get_admin_pool().connection() as conn:
        assert db.get_catalog_release(conn, "r111")["discogs_price"] == "42.50"
```

- [ ] **Step 6: Run the test to verify it fails**

```bash
cd backend && pytest tests/test_crawl_manager.py -k does_not_wipe -v
```

Expected: FAIL with `assert None == '42.50'` — this is the bug reproducing.

- [ ] **Step 7: Pass `preserve_price` from the wantlist loop**

In `backend/crawl_manager.py`, in the wantlist loop only (the one inside `for page, total_pages, items in discogs.iter_wantlist_pages(...)`, around line 469), change:

```python
                        upsert_catalog_release(conn, release)
```

to:

```python
                        upsert_catalog_release(conn, release, preserve_price=True)
```

Leave the collection loop's call (around line 428) as-is — it reads the real custom field and must keep overwriting.

- [ ] **Step 8: Run the tests to verify they pass**

```bash
cd backend && pytest tests/test_crawl_manager.py tests/test_catalog_crud.py -v
```

Expected: PASS.

- [ ] **Step 9: Run the full backend suite**

```bash
cd backend && pytest
```

Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add backend/db.py backend/crawl_manager.py backend/tests/test_catalog_crud.py backend/tests/test_crawl_manager.py
```

Subject: `fix: stop wantlist syncs from wiping catalog.discogs_price`. Same trailer requirement as Task 1.

---

## Task 4: Rename the four browse tabs on the frontend

Labels and frontend identifiers only. Backend and DB vocabulary (`in_wishlist`, `/api/releases?scope=wishlist`, the SSE `wishlist_synced` field) is deliberately untouched; `client.ts` becomes the single translation point.

| Position | Label now | Label after | `View` value | Component |
|---|---|---|---|---|
| 1 | Discogs | Collection | `'discogs'` → `'collection'` | `RecordBrowser` |
| 2 | Wishlist | Wantlist | `'wishlist'` → `'wantlist'` | `RecordBrowser` |
| 3 | Store | Store | `'instock'` → `'store'` | `StockBrowser` |
| 4 | Collection | Track | `'collection'` → `'track'` | `StockBrowser` |

**Files:**
- Modify: `frontend/src/api/types.ts:54`, `frontend/src/api/client.ts:37-40,54-73`
- Modify: `frontend/src/App.tsx:14,121,135-139,285-291,440-513`
- Modify: `frontend/src/views/RecordBrowser.tsx:178,193-194,309-310`
- Modify: `frontend/src/views/StockBrowser.tsx:7,14`
- Test: `frontend/src/test/recordBrowser.test.tsx`, `plexLink.test.tsx`, `syncRefetch.test.tsx`, `wishlistRefresh.test.tsx`, `crawlStatusBar.test.tsx`, `staleSignupLink.test.tsx`, `inStockTab.test.tsx`, `stockBrowser.test.tsx`, `client.test.ts`

- [ ] **Step 1: Write the failing tests**

Add to `frontend/src/test/client.test.ts`, inside its existing `describe` block. It stubs `fetch` into `fetchMock` in `beforeEach` and asserts on `fetchMock.mock.calls[N][0]`; each call within one test increments `N`:

```ts
  it('translates RecordScope values to the backend scope vocabulary', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ total: 0, page: 1, per_page: 50, releases: [] }) })
    await getReleases({ scope: 'collection' })
    expect(fetchMock.mock.calls[0][0]).toContain('scope=discogs')

    await getReleases({ scope: 'wantlist' })
    expect(fetchMock.mock.calls[1][0]).toContain('scope=wishlist')
  })
```

`getArtists` isn't imported by this file today; add it to the import on line 2 alongside the existing names, then:

```ts
  it('getArtists translates the wantlist scope to the backend value', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ artists: [] }) })
    await getArtists('wantlist')
    expect(fetchMock.mock.calls[0][0]).toContain('scope=wishlist')
  })
```

Also update the two existing `refreshCollection` cases (lines 100-112), which pass the old value: `refreshCollection('all', 'wishlist')` → `refreshCollection('all', 'wantlist')` and `refreshCollection('new', 'wishlist')` → `refreshCollection('new', 'wantlist')`. Their `toContain('scope=wishlist')` assertions stay exactly as they are — that's the wire value, which is the point of the translation. Rename the test titles to say "scope is wantlist" while keeping "includes scope=wishlist".

Then update the existing test expectations for the renamed labels and scope values:

- `crawlStatusBar.test.tsx:179` — `screen.getByText('Discogs')` → `screen.getByText('Collection')`
- `staleSignupLink.test.tsx:55` — `{ name: 'Discogs' }` → `{ name: 'Collection' }`
- `inStockTab.test.tsx:102-103` — `screen.getByText('Collection')` → `screen.getByText('Track')`, and the local variable `collectionButton` → `trackButton`
- `recordBrowser.test.tsx`, `plexLink.test.tsx`, `syncRefetch.test.tsx` — every `scope="discogs"` → `scope="collection"`, every `scope="wishlist"` → `scope="wantlist"`
- `recordBrowser.test.tsx:125` and any sibling assertions on wishlist copy — the empty-state text becomes "No wantlist items yet. Add records to your wantlist on Discogs, then sync." and the sync-button title becomes "Sync wantlist from Discogs"
- `wishlistRefresh.test.tsx:85` — `toHaveBeenCalledWith('all', 'wishlist')` → `toHaveBeenCalledWith('all', 'wantlist')`
- `stockBrowser.test.tsx` — every `scope="collection"` → `scope="track"`, and the three test titles mentioning `scope="collection"` reworded to `scope="track"`. Leave their `overlapping` assertions alone for now; Task 5 replaces them.

`accountNav.test.tsx` needs no change — verified: it asserts only the `Logs`, `Settings`, and `Hidden` buttons, none of which move. (The spec's testing list names it; Task 6 corrects that.)

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd frontend && npm test
```

Expected: FAIL — missing `Collection`/`Track` nav labels, `getReleases` emitting `scope=collection` instead of `scope=discogs`.

- [ ] **Step 3: Rename the frontend scope types**

In `frontend/src/api/types.ts`, change line 54 and add the stock-scope type:

```ts
export type RecordScope = 'collection' | 'wantlist'
export type StockScope = 'store' | 'track'
```

Leave `CrawlEvent`'s `scope?: 'all' | 'wishlist'` and `wishlist_synced?: number` (lines 74-76) alone — those describe the backend's SSE payload.

- [ ] **Step 4: Add the translation maps to `client.ts`**

Near the top of `frontend/src/api/client.ts`, after the imports:

```ts
// The frontend says "collection"/"wantlist"; the API still says
// "discogs"/"wishlist". This is the only place the two vocabularies meet.
const RECORD_SCOPE_PARAM: Record<RecordScope, string> = {
  collection: 'discogs',
  wantlist: 'wishlist',
}
```

In `getReleases` (line 64): `if (params.scope) q.set('scope', RECORD_SCOPE_PARAM[params.scope])`

In `getArtists` (line 73): `if (scope) q.set('scope', RECORD_SCOPE_PARAM[scope])`

In `refreshCollection` (lines 37-40):

```ts
export async function refreshCollection(mode?: 'all' | 'new', scope?: 'all' | 'wantlist'): Promise<{ started: boolean; running: boolean }> {
  const q = new URLSearchParams()
  if (mode === 'new') q.set('mode', 'new')
  if (scope === 'wantlist') q.set('scope', 'wishlist')
```

Only the parameter type and the comparison value change — the emitted `scope=wishlist` stays, and the `mode === 'new'` line is untouched.

- [ ] **Step 5: Rename the views and nav in `App.tsx`**

Line 14:

```ts
type View = 'collection' | 'wantlist' | 'store' | 'track' | 'settings' | 'logs' | 'account'
```

Line 26's initial state becomes `useState<View>('collection')`.

The four nav buttons (lines 440-462) become — note only the `setView`/`view ===` values and the label text change; the `className` expressions are otherwise identical:

```tsx
          <button
            onClick={() => setView('collection')}
            className={`px-3 py-1.5 text-sm font-medium ${navButtonClass(view === 'collection')}`}
          >
            Collection
          </button>
          <button
            onClick={() => setView('wantlist')}
            className={`px-3 py-1.5 text-sm font-medium ${navButtonClass(view === 'wantlist')}`}
          >
            Wantlist
          </button>
          <button
            onClick={() => setView('store')}
            className={`px-3 py-1.5 text-sm font-medium ${navButtonClass(view === 'store')}`}
          >
            Store
          </button>
          <button
            onClick={() => setView('track')}
            className={`px-3 py-1.5 text-sm font-medium ${navButtonClass(view === 'track')}`}
          >
            Track
          </button>
```

The four render blocks (lines 493-514):

```tsx
        <div className={view === 'collection' ? 'h-full' : 'hidden'}>
          <RecordBrowser
            scope="collection"
            syncing={syncing}
            onRefreshCollection={() => handleRefresh()}
            syncGeneration={syncGeneration}
          />
        </div>
        <div className={view === 'wantlist' ? 'h-full' : 'hidden'}>
          <RecordBrowser
            scope="wantlist"
            syncing={syncing}
            onRefreshCollection={() => handleRefreshWantlist()}
            syncGeneration={syncGeneration}
          />
        </div>
        <div className={view === 'store' ? 'h-full' : 'hidden'}>
          <StockBrowser recommendedAvailable={recommendedAvailable} hiddenCrawlerIds={hiddenCrawlerIds} />
        </div>
        <div className={view === 'track' ? 'h-full' : 'hidden'}>
          <StockBrowser scope="track" hiddenCrawlerIds={hiddenCrawlerIds} />
        </div>
```

Rename `handleRefreshWishlist` → `handleRefreshWantlist` (line 289) and change its call to `await refreshCollection('all', 'wantlist')` (line 291). Update the comment above it (line 285) to say "Wantlist tab's refresh…".

The SSE status strings (lines 121, 136-139) — keep the `event.scope === 'wishlist'` and `event.wishlist_synced` reads, which are backend field names, and change only the user-facing text:

```tsx
        setSyncStatus(event.scope === 'wishlist' ? 'Syncing wantlist…' : 'Syncing collection…', event.id ?? null)
```

```tsx
          setSyncStatus(`Synced ${event.wishlist_synced} wantlist items for ${event.username}`, event.id ?? null)
        } else {
          const wantlistPart = event.wishlist_synced != null ? `, ${event.wishlist_synced} wantlist items` : ''
          setSyncStatus(`Synced ${event.synced} records for ${event.username}${wantlistPart}`, event.id ?? null)
```

- [ ] **Step 6: Rename the scope checks in `RecordBrowser.tsx`**

Change `scope === 'wishlist'` to `scope === 'wantlist'` at lines 178, 193, and 309, and update the strings those branches produce:

- Line 178: `title={scope === 'wantlist' ? 'Sync wantlist from Discogs' : 'Sync collection from Discogs'}`
- Lines 193-194 and 309-310: `'No wantlist items yet. Add records to your wantlist on Discogs, then sync.'`

Also update the line 57 comment's "collection/wishlist tables" to "collection/wantlist tables".

- [ ] **Step 7: Rename `StockBrowser`'s scope prop values**

In `frontend/src/views/StockBrowser.tsx`, change the `Props` interface and the destructured default:

```tsx
import type { StockItem, StockSortField, SortOrder, StockScope } from '../api/types'

interface Props {
  scope?: StockScope
  recommendedAvailable?: boolean
  hiddenCrawlerIds?: number[]
}
```

Then replace every `scope === 'collection'` with `scope === 'track'` (lines 47, 48, 64, 92, 141, 241, 288 — note 48/64/141 are the `scope === 'store'` comparisons, which are unchanged, and `overlapping: scope === 'collection'` on line 47 becomes `overlapping: scope === 'track'` for now; Task 5 replaces that line entirely).

- [ ] **Step 8: Run the tests to verify they pass**

```bash
cd frontend && npm test
```

Expected: PASS.

- [ ] **Step 9: Typecheck**

```bash
cd frontend && npm run build
```

Expected: no TypeScript errors. `tsc -b` runs first, so a missed scope-value rename surfaces here rather than at runtime — vitest transpiles without typechecking.

- [ ] **Step 10: Commit**

```bash
git add frontend/src
```

Subject: `refactor: rename browse tabs to Collection, Wantlist, Store, Track`. Same trailer requirement as Task 1.

---

## Task 5: Track tab All/Collection/Wantlist dropdown

**Files:**
- Modify: `frontend/src/api/types.ts` (add `LibraryScope`)
- Modify: `frontend/src/api/client.ts:147-183` (`getStock`, `getStockArtists`)
- Modify: `frontend/src/views/StockBrowser.tsx:23-27,41-56,64,139-150,178-183,270-272`
- Test: `frontend/src/test/stockBrowser.test.tsx`, `inStockTab.test.tsx`, `client.test.ts`

- [ ] **Step 1: Write the failing tests**

Replace the three `overlapping`-based cases in `frontend/src/test/stockBrowser.test.tsx` (lines 230-249) with these, and add the rest:

```tsx
  it('scope="track" sends libraryScope and shows an All/Collection/Wantlist dropdown', async () => {
    render(<StockBrowser scope="track" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    const select = screen.getByRole('combobox') as HTMLSelectElement
    expect([...select.options].map((o) => o.value)).toEqual(['all', 'collection', 'wantlist'])
    expect(select.value).toBe('all')
    expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ libraryScope: 'all' }))
  })

  it('scope="track" sends libraryScope on the artist sidebar fetch too', async () => {
    render(<StockBrowser scope="track" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(getStockArtists).toHaveBeenCalledWith('all', false, [])
  })

  it('changing the Track filter refetches with the new libraryScope', async () => {
    render(<StockBrowser scope="track" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'wantlist' } })
    await waitFor(() =>
      expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ libraryScope: 'wantlist' }))
    )
  })

  it('persists the Track filter under stockFilter_track and restores it on remount', async () => {
    const { unmount } = render(<StockBrowser scope="track" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'collection' } })
    await waitFor(() => expect(localStorage.getItem('stockFilter_track')).toBe('collection'))
    unmount()
    render(<StockBrowser scope="track" />)
    await waitFor(() => expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('collection'))
  })

  it('ignores a stored filter value that is not valid for the scope', async () => {
    localStorage.setItem('stockFilter_track', 'recommended')
    render(<StockBrowser scope="track" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('all')
  })

  it('scope="store" (default) keeps the All/Recommended dropdown and sends no libraryScope', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    const select = screen.getByRole('combobox') as HTMLSelectElement
    expect([...select.options].map((o) => o.value)).toEqual(['all', 'recommended'])
    expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ libraryScope: undefined }))
  })

  it('renders the Price column under every Track filter value', async () => {
    render(<StockBrowser scope="track" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(screen.getByText(/Price/)).toBeTruthy()
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'wantlist' } })
    await waitFor(() => expect(screen.getByText(/Price/)).toBeTruthy())
  })

  it('shows a filter-specific empty state on the Track tab', async () => {
    getStock.mockResolvedValue({ total: 0, page: 1, per_page: 250, items: [] })
    render(<StockBrowser scope="track" />)
    await waitFor(() => expect(screen.getByText(/Nothing you're tracking is in stock/)).toBeTruthy())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'wantlist' } })
    await waitFor(() => expect(screen.getByText(/Nothing on your wantlist is in stock/)).toBeTruthy())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'collection' } })
    await waitFor(() => expect(screen.getByText(/Nothing in your collection is in stock/)).toBeTruthy())
  })
```

In `inStockTab.test.tsx:106`, change the assertion to the new param and default:

```tsx
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ libraryScope: 'all' })))
```

In `client.test.ts`, first fix the existing call at line 78 — `getStockArtists(false, false, [3, 7])` passes the old `overlapping` boolean, which is now a type error:

```ts
    await getStockArtists(undefined, false, [3, 7])
```

Then add:

```ts
  it('maps libraryScope to the backend library_scope value', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ total: 0, page: 1, per_page: 250, items: [] }) })
    await getStock({ libraryScope: 'wantlist' })
    expect(fetchMock.mock.calls[0][0]).toContain('library_scope=wishlist')

    await getStock({ libraryScope: 'collection' })
    expect(fetchMock.mock.calls[1][0]).toContain('library_scope=collection')

    await getStock({ libraryScope: 'all' })
    expect(fetchMock.mock.calls[2][0]).toContain('library_scope=all')

    await getStock({})
    expect(fetchMock.mock.calls[3][0]).not.toContain('library_scope')
  })

  it('getStockArtists maps libraryScope to the backend value', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ artists: [] }) })
    await getStockArtists('wantlist')
    expect(fetchMock.mock.calls[0][0]).toContain('library_scope=wishlist')
  })
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd frontend && npm test
```

Expected: FAIL — `getStock` still receives `overlapping`, the Track dropdown doesn't render, empty-state text doesn't match.

- [ ] **Step 3: Add the `LibraryScope` type**

In `frontend/src/api/types.ts`, next to `StockScope`:

```ts
export type LibraryScope = 'collection' | 'wantlist' | 'all'
```

- [ ] **Step 4: Swap `overlapping` for `libraryScope` in `client.ts`**

Add the map next to `RECORD_SCOPE_PARAM`:

```ts
const LIBRARY_SCOPE_PARAM: Record<LibraryScope, string> = {
  collection: 'collection',
  wantlist: 'wishlist',
  all: 'all',
}
```

In `getStock`, replace `overlapping?: boolean` with `libraryScope?: LibraryScope` in the params type, and line 165 with:

```ts
  if (params.libraryScope) q.set('library_scope', LIBRARY_SCOPE_PARAM[params.libraryScope])
```

In `getStockArtists`, replace the first parameter and its query line:

```ts
export async function getStockArtists(libraryScope?: LibraryScope, recommended?: boolean, hiddenCrawlerIds?: number[]): Promise<string[]> {
  const q = new URLSearchParams()
  if (libraryScope) q.set('library_scope', LIBRARY_SCOPE_PARAM[libraryScope])
```

Import `LibraryScope` alongside the existing type imports.

- [ ] **Step 5: Widen `StockBrowser`'s filter state**

Add the allow-sets above the component:

```tsx
const STORE_FILTERS = ['all', 'recommended']
const TRACK_FILTERS = ['all', 'collection', 'wantlist']
```

Replace the `filter` state initializer (lines 23-27):

```tsx
  const [filter, setFilter] = useState<string>(() => {
    const allowed = scope === 'track' ? TRACK_FILTERS : STORE_FILTERS
    const stored = localStorage.getItem(`stockFilter_${scope}`)
    return stored && allowed.includes(stored) ? stored : 'all'
  })
```

In `load()`, replace the `overlapping` line with:

```tsx
        libraryScope: scope === 'track' ? (filter as LibraryScope) : undefined,
        recommended: scope === 'store' && filter === 'recommended',
```

And update the artist-sidebar effect (line 64):

```tsx
  useEffect(() => {
    getStockArtists(
      scope === 'track' ? (filter as LibraryScope) : undefined,
      scope === 'store' && filter === 'recommended',
      hiddenCrawlerIds,
    ).then(setArtists)
  }, [scope, filter, hiddenCrawlerIds])
```

Import `LibraryScope` from `../api/types`.

The `recommendedAvailable` reset effect (lines 58-62) needs no change: `'recommended'` isn't reachable under `TRACK_FILTERS`, so its `filter === 'recommended'` condition can never fire on the Track tab and can't clobber a Track filter value.

- [ ] **Step 6: Render the scope-dependent dropdown**

Replace the `{scope === 'store' && (<select …>)}` block (lines 141-150) with an unconditional `<select>` whose options depend on scope:

```tsx
            <select
              value={filter}
              onChange={(e) => { setFilter(e.target.value); setPage(1) }}
              className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:border-gray-400"
            >
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
                </>
              )}
            </select>
```

- [ ] **Step 7: Make the empty state filter-aware**

Add next to `colCount` (line 92):

```tsx
  const emptyMessage =
    scope === 'store' ? 'No in-stock items yet. Click "Refresh Stock Now" in Settings.'
    : filter === 'collection' ? 'Nothing in your collection is in stock right now.'
    : filter === 'wantlist' ? 'Nothing on your wantlist is in stock right now.'
    : "Nothing you're tracking is in stock right now."
```

Use it in both empty states — the tile-view block (currently the hardcoded string around line 180) and the list-view row (around line 271):

```tsx
              <div className="text-center py-8 text-gray-500">
                {emptyMessage}
              </div>
```

```tsx
                <tr><td colSpan={colCount} className="text-center py-8 text-gray-500">{emptyMessage}</td></tr>
```

- [ ] **Step 8: Run the tests to verify they pass**

```bash
cd frontend && npm test
```

Expected: PASS. The pre-existing `shows an empty state when there are no items` test (line ~56) renders the default Store scope, so it still matches `/No in-stock items yet/`.

- [ ] **Step 9: Typecheck and lint**

```bash
cd frontend && npm run build && npm run lint
```

Expected: no errors.

- [ ] **Step 10: Commit**

```bash
git add frontend/src
```

Subject: `feat: add an All/Collection/Wantlist filter to the Track tab`. Same trailer requirement as Task 1.

---

## Task 6: Version bump, spec drift, and manual verification

**Files:**
- Modify: `backend/version.py`
- Modify: `docs/specifications/shaping/2026-08-08-discogs-tab-rename-design.md`, `2026-08-08-store-collection-split-design.md`, `2026-08-09-collection-price-paid-design.md`, `2026-08-10-collection-wishlist-filter-design.md`

- [ ] **Step 1: Bump the version**

`backend/version.py`: `VERSION = "3.2"`. Minor bump, per `CLAUDE.md`'s versioning rule. This assumes `3.1` is on `main` by merge time — if PR #76 hasn't landed yet when this branch rebases, re-check that `3.2` is still the next value.

- [ ] **Step 2: Run the pre-PR spec-drift check**

Per `CLAUDE.md`, grep every spec for the symbols and UI strings this branch touched, not just this feature's own spec:

```bash
grep -rln "overlapping\|_owned_match_fragment\|Wishlist tab\|Collection tab\|scope=\"discogs\"\|scope=\"collection\"" docs/specifications/shaping docs/superpowers/specs
```

Plans (`docs/specifications/plans/`, `docs/superpowers/plans/`) are historical task logs and are not backported.

- [ ] **Step 3: Amend `2026-08-08-discogs-tab-rename-design.md`**

Its premise — Collection → Discogs, freeing "Collection" for the future intersection tab — is now reversed. Add an amendment note under its `## Problem` heading:

```markdown
**Amendment (2026-08-10):** the rename this spec describes was reversed by
`2026-08-10-collection-wishlist-filter-design.md`. The intersection tab it
anticipated is now called **Track**, and this tab went back to
**Collection**; the wantlist tab is now **Wantlist**. The frontend scope
values changed with the labels (`'discogs'` → `'collection'`), though the
backend `scope="discogs"` value this spec introduced is unchanged.
```

- [ ] **Step 4: Amend `2026-08-08-store-collection-split-design.md`**

Three statements have drifted. Add to its Decisions section, immediately after the "Store's 'Overlapping' filter is removed, not duplicated" bullet:

```markdown
  **Amendment (2026-08-10):** "Collection has no filter dropdown at all —
  the tab itself is the filter" no longer holds. That tab is now **Track**
  and carries an All/Collection/Wantlist dropdown; see
  `2026-08-10-collection-wishlist-filter-design.md` for why the reversal
  was judged cheaper than a fifth tab. The `overlapping` parameter this
  spec documents throughout was also replaced by `library_scope`
  (`'collection'`/`'wishlist'`/`'all'`/`None`), and the `View` union values
  were renamed with the tabs.
```

- [ ] **Step 5: Amend `2026-08-09-collection-price-paid-design.md`**

Add to its Decisions section, after the "`Price` column is Collection-only" bullet:

```markdown
  **Amendment (2026-08-10):** `_owned_match_fragment` is now
  `_library_match_fragment(user_id_param, library_scope)`, and the tab is
  now **Track**. The Price column renders under all three of its filter
  values, not just the collection one — the price subquery is pinned to
  `'collection'` scope, so a wantlist-only row returns NULL and renders
  `—` with no conditional rendering. "Out of scope: any change to wishlist
  items (they never carry a `discogs_price` today and this doesn't change
  that)" is superseded: wantlist items are now matched, and still never
  carry a `discogs_price`, deliberately. The `sort == "discogs_price" and
  overlapping` gate is now `library_scope is not None`.
```

- [ ] **Step 6: Correct this feature's own spec**

Two items in `2026-08-10-collection-wishlist-filter-design.md`'s Scope and Testing sections were wrong on inspection. In the `Scope` list's test enumeration, remove `frontend/src/test/accountNav.test.tsx` and add `frontend/src/test/staleSignupLink.test.tsx`; make the same swap in the `## Testing` section's frontend bullet list, replacing the `accountNav.test.tsx` mention with `staleSignupLink.test.tsx`. Reason: `accountNav.test.tsx` asserts only the `Logs`/`Settings`/`Hidden` buttons and needs no change, while `staleSignupLink.test.tsx:55` asserts a nav button named `Discogs` and does.

- [ ] **Step 7: Run the full suite**

```bash
cd backend && pytest
```

```bash
cd frontend && npm test && npm run build && npm run lint
```

Expected: all PASS, no TypeScript or lint errors.

- [ ] **Step 8: Manual verification**

Start both services:

```bash
make dev
```

Open http://localhost:5173 and confirm:

1. The nav reads `Collection  Wantlist  Store  Track`, in that order.
2. Collection and Wantlist show Discogs metadata as before; the Wantlist tab's sync button tooltip says "Sync wantlist from Discogs".
3. Track opens with the dropdown on `All` and lists both owned and wanted in-stock items.
4. Switching Track to `Collection` narrows to owned items, all with a Price value where the Discogs custom field is set; switching to `Wantlist` narrows to wanted items, every Price cell showing `—`.
5. The Price column header is present under all three filter values.
6. The artist sidebar changes with the filter — pick an artist that's only on the wantlist and confirm it disappears under `Collection`.
7. Reload the page and confirm the Track filter selection persisted.
8. Store still shows its All/Recommended dropdown and is otherwise unchanged.

- [ ] **Step 9: Commit**

```bash
git add backend/version.py docs/specifications/shaping
```

Subject: `docs: bump version to 3.2 and fix spec drift from the tab rename`. Same trailer requirement as Task 1.

- [ ] **Step 10: Open the PR**

Use the `sdlc:pr-review-prep` skill. Per `CLAUDE.md`, open it ready for review (`--draft=false`), and note in the description what spec drift was found and fixed (the three prior shaping specs plus the two test-list corrections in this feature's own spec). State the stacked base explicitly: this PR targets `worktree-collection-price-paid`, not `main`, until #76 lands.

---

## Merge sequence

Once this PR is approved, merge in stack order — each merge requires the previous one to have landed first:

1. **PR #75** (`worktree-store-collection-split`) → `main`.
2. **PR #76** (`worktree-collection-price-paid`) — rebase on updated `main`, confirm `cd backend && pytest` and `cd frontend && npm test` still pass, retarget to `main` if GitHub hasn't done so automatically, then merge.
3. **This branch** — rebase on updated `main`, re-verify both suites, confirm `backend/version.py` still reads one minor above whatever `main` now carries, then merge.

Do not squash the three into one PR; each has its own review history and its own spec.
