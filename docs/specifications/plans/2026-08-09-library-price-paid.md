# Per-user collection price storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the per-user Discogs collection price off the global `catalog.discogs_price` column onto `library_items.price_paid`, closing a recurring cross-tenant data-loss bug.

**Architecture:** Add `library_items.price_paid` via an additive `TENANT_SCHEMA` migration. Write it from `upsert_library_item` at the one collection-sync call site where the per-user `price_field_id` result is in scope, using an `_UNSET` sentinel so an explicit `None` clears a price while omission inherits it. Repoint both existing read paths (`get_library_releases`, `get_stock_items`) — both already join `library_items` per-user — keeping `discogs_price` as the wire name. That cutover also retires `preserve_price`, which then guards nothing. Finally, backfill provably-unambiguous rows and drop `catalog.discogs_price` in one self-retiring guarded `DO` block.

**Tech Stack:** Python 3.9+, FastAPI, psycopg 3, Postgres 16, pytest (`asyncio_mode = "auto"`), respx for HTTP mocking.

**Design spec:** [`docs/specifications/shaping/2026-08-09-library-price-paid-design.md`](../shaping/2026-08-09-library-price-paid-design.md)

---

## Before you start

**Worktree:** All work happens in `.worktrees/library-price-paid` on branch `worktree-library-price-paid`. This branch is **stacked on `worktree-collection-wishlist-filter`**, not `main` — `_library_match_fragment`, the `get_stock_items` price subquery, and `preserve_price` exist only there. Do not rebase onto `main`.

**Test setup:** Backend tests need Postgres running and `TEST_DATABASE_URL` in `backend/.env`. Some tests also need `APP_DB_PASSWORD` and `IDENTITY_DB_PASSWORD`. Run tests from the `backend/` directory:

```bash
cd backend && pytest
```

**Commits:** Every commit needs AI-attribution trailers and must be made with `git commit -F <file>`, never `-m`. Each task's commit step gives the exact message. Write it to a scratch file first, e.g.:

```bash
git commit -F /tmp/msg.txt
```

The required trailer block, appended as the last paragraph after a blank line:

```
Note: This commit message was created by AI
ai-generated: true
ai-model: claude-opus-5
ai-tool: claude-code
ai-surface: cli
ai-executor: local-agent
```

**Known pre-existing suite flakiness — do not chase it.** The full suite fails intermittently, observed once in ten runs during Task 2 (`9 failed, 761 passed, 4 errors`), with failures scattered across unrelated files including `test_session_crud.py`. Ruled out: connection exhaustion (`pg_stat_activity` peaked at 13 against `max_connections` 100) and test-order randomization (neither `pytest-randomly` nor `pytest-xdist` is installed, so order is deterministic). No traceback has been captured; the working hypothesis is per-test connection-pool churn racing the `TRUNCATE ... CASCADE` fixture teardown, since `psycopg_pool.ConnectionPool.close()` does not wait for in-use connections.

It is not caused by this branch: a diff to `upsert_library_item`'s SQL cannot make `test_session_crud.py::create_session` fail, and the implementer reproduced comparable failures at the pre-branch baseline. **At any green gate below, if failures appear in files this branch does not touch, re-run before investigating; treat a clean run as the true result.** Do not "fix" unrelated tests to make a gate pass, and do not let this mask a real failure — a failure inside a file this branch *does* touch is yours until proven otherwise.

**Task ordering is load-bearing, and Task 3 is deliberately large.** The write path, both read paths, and the test seeding all reference the price together, so moving any one of them alone leaves the suite red — there is no smaller green step. Task 3 is therefore a single atomic cutover with one green gate at its end; its sub-steps are individually small, but do not commit partway through. Task 4 then drops the column, which is only safe once Task 3 has stopped both writing and reading it.

**One psycopg detail that dictates the above:** psycopg raises `query parameter missing` when a named placeholder in the statement is absent from the params dict (extra keys, by contrast, are ignored harmlessly). So `upsert_catalog_release` cannot reference a renamed price key while ~40 existing fixtures still pass the old one — the reference has to be deleted, not repointed.

---

## File Structure

| File | Change |
|---|---|
| `backend/db.py` | `TENANT_SCHEMA` migration + `DO` block; `GLOBAL_SCHEMA` column removal; `upsert_library_item`, `upsert_catalog_release`, `get_library_releases`, `get_stock_items`, `_RELEASE_ALLOWED_SORT` |
| `backend/discogs.py` | `parse_release` output key rename |
| `backend/crawl_manager.py` | collection-loop call site passes `price_paid`; wantlist loop drops `preserve_price` |
| `backend/tests/test_tenant_schema.py` | new column + backfill/drop migration tests |
| `backend/tests/test_global_schema.py` | `catalog` expected-column set |
| `backend/tests/test_catalog_crud.py` | `upsert_library_item` price tests; delete obsolete `preserve_price` tests |
| `backend/tests/test_discogs.py` | `parse_release` price key tests |
| `backend/tests/test_crawl_manager.py` | cross-tenant regression test; repoint existing price assertions |
| `backend/tests/test_stock_crud.py` | repoint price seeds to `price_paid` |
| `backend/tests/test_stock_router.py` | repoint price seeds to `price_paid` |
| `backend/tests/test_releases_router.py` | `get_library_releases` price test |
| `backend/version.py` | bump to `3.3` |
| `docs/superpowers/specs/2026-07-26-multi-tenant-architecture-design.md` | data dictionary correction |
| `docs/superpowers/specs/2026-06-27-discogs-browser-design.md` | data dictionary correction |

No frontend files change — `discogs_price` stays the wire name.

---

### Task 1: Add the `library_items.price_paid` column

**Files:**
- Modify: `backend/db.py:195-196` (`TENANT_SCHEMA` additive migrations)
- Test: `backend/tests/test_tenant_schema.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_tenant_schema.py`:

```python
def test_library_items_has_price_paid_column(admin_conn):
    row = admin_conn.execute(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = 'library_items' AND column_name = 'price_paid'"
    ).fetchone()
    assert row is not None
    assert row["data_type"] == "text"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_tenant_schema.py::test_library_items_has_price_paid_column -v`
Expected: FAIL — `assert None is not None`

- [ ] **Step 3: Add the migration**

In `backend/db.py`, immediately after the existing two `library_items` `ALTER TABLE` lines (`:195-196`):

```sql
ALTER TABLE library_items ADD COLUMN IF NOT EXISTS collection_date_added TIMESTAMP;
ALTER TABLE library_items ADD COLUMN IF NOT EXISTS wishlist_date_added TIMESTAMP;
ALTER TABLE library_items ADD COLUMN IF NOT EXISTS price_paid TEXT;
```

TEXT, not NUMERIC: the value is free text from a user-controlled Discogs custom field, and the existing stock sort already regex-extracts a number from it.

No `GRANT` change is needed — `app_user` already holds table-level `SELECT, INSERT, UPDATE, DELETE ON library_items`, which covers new columns, and `library_items_isolation` is row-scoped so it protects `price_paid` on creation.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_tenant_schema.py -v`
Expected: PASS (all tests in file)

- [ ] **Step 5: Commit**

```
feat: add library_items.price_paid column

Per-user home for the Discogs collection price field. Nothing writes or
reads it yet.
```

---

### Task 2: `upsert_library_item` writes `price_paid`

**Files:**
- Modify: `backend/db.py:476-517` (`upsert_library_item`)
- Test: `backend/tests/test_catalog_crud.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_catalog_crud.py`:

```python
def _seed_user_and_release(admin_conn):
    user = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "d1", "artist": "A", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None,
        "cover_image_url": None, "discogs_url": None,
    })
    admin_conn.commit()
    return user


def _price_paid(admin_conn, user_id, discogs_id="d1"):
    return admin_conn.execute(
        "SELECT price_paid FROM library_items WHERE user_id = %s AND discogs_id = %s",
        [user_id, discogs_id],
    ).fetchone()["price_paid"]


def test_upsert_library_item_writes_price_paid(admin_conn):
    user = _seed_user_and_release(admin_conn)
    db.upsert_library_item(admin_conn, user["id"], "d1", in_collection=True, price_paid="$10")
    admin_conn.commit()
    assert _price_paid(admin_conn, user["id"]) == "$10"


def test_upsert_library_item_omitting_price_paid_preserves_the_stored_value(admin_conn):
    # The mode="new" sync path never calls parse_release, so it has no price to
    # pass. Omitting it must inherit, not blank the row out.
    user = _seed_user_and_release(admin_conn)
    db.upsert_library_item(admin_conn, user["id"], "d1", in_collection=True, price_paid="$10")
    admin_conn.commit()

    db.upsert_library_item(admin_conn, user["id"], "d1", in_collection=True)
    admin_conn.commit()
    assert _price_paid(admin_conn, user["id"]) == "$10"


def test_upsert_library_item_explicit_none_price_paid_clears_it(admin_conn):
    # The other half of the sentinel contract, and the reason price_paid cannot
    # use the COALESCE pattern its neighbours use: parse_release yields None for
    # a user who cleared their Discogs "Price" field, so None has to mean
    # "authoritatively empty". Under COALESCE this test fails and a price
    # becomes permanently unclearable.
    user = _seed_user_and_release(admin_conn)
    db.upsert_library_item(admin_conn, user["id"], "d1", in_collection=True, price_paid="$10")
    admin_conn.commit()

    db.upsert_library_item(admin_conn, user["id"], "d1", in_collection=True, price_paid=None)
    admin_conn.commit()
    assert _price_paid(admin_conn, user["id"]) is None


def test_upsert_library_item_clearing_does_not_disturb_other_columns(admin_conn):
    user = _seed_user_and_release(admin_conn)
    db.upsert_library_item(
        admin_conn, user["id"], "d1", in_collection=True,
        collection_date_added="2024-03-15T10:00:00Z", price_paid="$10",
    )
    admin_conn.commit()

    db.upsert_library_item(admin_conn, user["id"], "d1", in_collection=True, price_paid=None)
    admin_conn.commit()

    row = admin_conn.execute(
        "SELECT in_collection, collection_date_added, price_paid FROM library_items "
        "WHERE user_id = %s AND discogs_id = 'd1'",
        [user["id"]],
    ).fetchone()
    assert row["price_paid"] is None
    assert row["in_collection"] is True
    assert row["collection_date_added"] is not None


def test_upsert_library_item_price_paid_is_null_on_insert_when_omitted(admin_conn):
    user = _seed_user_and_release(admin_conn)
    db.upsert_library_item(admin_conn, user["id"], "d1", in_collection=True)
    admin_conn.commit()
    assert _price_paid(admin_conn, user["id"]) is None


def test_upsert_library_item_price_paid_is_per_user(admin_conn):
    # The whole point of the change: two users, one shared release, independent
    # prices. On catalog this was one row and one value.
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    bob = db.create_user(admin_conn, discogs_user_id=2, discogs_username="bob")
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "d1", "artist": "A", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None,
        "cover_image_url": None, "discogs_url": None,
    })
    db.upsert_library_item(admin_conn, alice["id"], "d1", in_collection=True, price_paid="$10")
    db.upsert_library_item(admin_conn, bob["id"], "d1", in_collection=True, price_paid=None)
    admin_conn.commit()

    assert _price_paid(admin_conn, alice["id"]) == "$10"
    assert _price_paid(admin_conn, bob["id"]) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_catalog_crud.py -k price_paid -v`
Expected: FAIL — `TypeError: upsert_library_item() got an unexpected keyword argument 'price_paid'`

- [ ] **Step 3: Add the sentinel and the parameter**

`price_paid` cannot use the `COALESCE(%(param)s, library_items.param)` pattern its neighbours use. Those parameters treat `None` as "unspecified, inherit", but `parse_release` legitimately yields `None` for a user who cleared their Discogs `"Price"` field — so under `COALESCE` a price would inherit forever and be permanently unclearable through the app. `None` has to mean "authoritatively empty", which leaves nothing to mean "unspecified". Hence a sentinel default.

Add a module-level sentinel in `backend/db.py`, near the top with the other module constants:

```python
# price_paid's "unspecified" needs to be distinct from None: None means the
# user's custom "Price" field is genuinely empty and the stored value must be
# cleared, so it can't double as "this caller never looked."
_UNSET = object()
```

Change the signature, adding the parameter last. It is left unannotated because a sentinel default has no honest `Optional[str]` annotation:

```python
def upsert_library_item(
    conn,
    user_id: int,
    discogs_id: str,
    in_collection: Optional[bool] = None,
    in_wishlist: Optional[bool] = None,
    collection_date_added: Optional[str] = None,
    wishlist_date_added: Optional[str] = None,
    price_paid=_UNSET,
):
```

Include the column in the `SET` clause only when the caller passed something:

```python
    price_set = "" if price_paid is _UNSET else "price_paid = %(price_paid)s,"
    conn.execute(
        f"""
        INSERT INTO library_items (
            user_id, discogs_id, in_collection, in_wishlist,
            collection_date_added, wishlist_date_added, price_paid, last_synced
        )
        VALUES (
            %(user_id)s, %(discogs_id)s, COALESCE(%(in_collection)s, FALSE),
            COALESCE(%(in_wishlist)s, FALSE), %(collection_date_added)s,
            %(wishlist_date_added)s, %(price_paid)s, CURRENT_TIMESTAMP
        )
        ON CONFLICT (user_id, discogs_id) DO UPDATE SET
            in_collection = COALESCE(%(in_collection)s, library_items.in_collection),
            in_wishlist = COALESCE(%(in_wishlist)s, library_items.in_wishlist),
            collection_date_added = COALESCE(%(collection_date_added)s, library_items.collection_date_added),
            wishlist_date_added = COALESCE(%(wishlist_date_added)s, library_items.wishlist_date_added),
            {price_set}
            last_synced = CURRENT_TIMESTAMP
        """,
        {
            "user_id": user_id,
            "discogs_id": discogs_id,
            "in_collection": in_collection,
            "in_wishlist": in_wishlist,
            "collection_date_added": collection_date_added,
            "wishlist_date_added": wishlist_date_added,
            "price_paid": None if price_paid is _UNSET else price_paid,
        },
    )
```

Three details that are easy to get wrong:

- `price_set` carries its own trailing comma and sits immediately before `last_synced`, so the clause stays valid whether or not it is present.
- The INSERT branch always lists `price_paid` and always binds the param — an unset caller inserts `NULL`, which is correct, and keeping the placeholder present unconditionally means the params dict never has to change shape.
- The param is bound even when unset, because psycopg raises on a named placeholder missing from the dict. `price_set` being empty is what keeps it out of the `SET` clause; the binding itself is harmless.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_catalog_crud.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```
feat: let upsert_library_item write per-user price_paid

An _UNSET sentinel default, not None: parse_release yields None for a user
who cleared their Discogs "Price" field, so None must mean "clear it" and
cannot double as "this caller never looked." No caller passes it yet.
```

---

### Task 3: Cut over from `catalog.discogs_price` to `library_items.price_paid`

One atomic task: nothing writes the global column, both read paths and all test seeding move to the per-user column, and `preserve_price` is retired. Work through every step before running the suite — intermediate states are expected to be red.

**Files:**
- Modify: `backend/db.py` — `upsert_catalog_release` (`:356-381`), `_RELEASE_ALLOWED_SORT` (`:553`), `get_library_releases` (`:604-618`), `get_stock_items` (`:946-953`, `:992`)
- Modify: `backend/discogs.py:101-124` (`parse_release`)
- Modify: `backend/crawl_manager.py` — collection loop (`:428-433`), wantlist loop (`:470`)
- Test: `backend/tests/test_discogs.py`, `test_crawl_manager.py`, `test_catalog_crud.py`, `test_releases_router.py`, `test_stock_crud.py`, `test_stock_router.py`

---

#### 3a. Stop writing the global column

- [ ] **Step 1: Remove the price from `upsert_catalog_release`**

This comes first precisely because of the psycopg rule above: once the statement stops referencing any price placeholder, both the old `discogs_price` key and the new `price_paid` key become harmless extras, and the ~40 unrelated fixtures that pass `"discogs_price": None` keep working untouched.

Replace the whole function. Delete the parameter, the `price_assignment` interpolation, and the comment block that exists only to justify the flag. It no longer needs to be an f-string:

```python
def upsert_catalog_release(conn, data: dict):
    conn.execute(
        """
        INSERT INTO catalog (discogs_id, artist, title, year, label, format,
                              barcode, cover_image_url, discogs_url, last_synced)
        VALUES (%(discogs_id)s, %(artist)s, %(title)s, %(year)s, %(label)s, %(format)s,
                %(barcode)s, %(cover_image_url)s, %(discogs_url)s, CURRENT_TIMESTAMP)
        ON CONFLICT (discogs_id) DO UPDATE SET
            artist = EXCLUDED.artist, title = EXCLUDED.title, year = EXCLUDED.year,
            label = EXCLUDED.label, format = EXCLUDED.format,
            barcode = EXCLUDED.barcode, cover_image_url = EXCLUDED.cover_image_url,
            discogs_url = EXCLUDED.discogs_url, last_synced = CURRENT_TIMESTAMP
        """,
        data,
    )
```

- [ ] **Step 2: Drop the flag at the wantlist call site**

In `backend/crawl_manager.py:470`:

```python
                        upsert_catalog_release(conn, release)
```

- [ ] **Step 3: Confirm the flag is gone**

Run: `cd backend && grep -rn "preserve_price" . ; echo "exit=$?"`
Expected: no matches, `exit=1`

---

#### 3b. Rename `parse_release`'s price key

- [ ] **Step 4: Rewrite the tests for the new key**

In `backend/tests/test_discogs.py`, replace the existing `test_parse_release` with:

```python
def test_parse_release():
    parsed = parse_release(_ITEM)
    assert parsed["discogs_id"] == "r456"
    assert parsed["artist"] == "Miles Davis"
    assert parsed["title"] == "Kind of Blue"
    assert parsed["year"] == 1959
    assert parsed["label"] == "Columbia"
    assert parsed["format"] == "Vinyl"
    assert parsed["discogs_url"] == "https://www.discogs.com/release/456"
    assert parsed["barcode"] is None
    # Named for what it is. No discogs_price key remains -- that name read as a
    # marketplace figure, and the misreading is what put it on a global column.
    assert parsed["price_paid"] is None
    assert "discogs_price" not in parsed


def _item_with_note(field_id, value):
    return {
        "basic_information": {
            "id": 456, "title": "Kind of Blue", "year": 1959,
            "artists": [{"name": "Miles Davis"}], "labels": [{"name": "Columbia"}],
            "formats": [{"name": "Vinyl"}], "cover_image": "",
        },
        "notes": [{"field_id": field_id, "value": value}],
    }


def test_parse_release_reads_the_matched_custom_field_into_price_paid():
    assert parse_release(_item_with_note(7, "42.50"), price_field_id=7)["price_paid"] == "42.50"


def test_parse_release_price_paid_is_none_when_the_user_has_no_price_field():
    # The exact condition that caused the data loss: no field named "Price", so
    # price_field_id is None and nothing is read.
    assert parse_release(_item_with_note(7, "42.50"), price_field_id=None)["price_paid"] is None


def test_parse_release_price_paid_is_none_when_the_field_is_empty():
    # An empty custom field is a cleared price, not a missing one -- Step 8's
    # call site passes this None through as an authoritative clear.
    assert parse_release(_item_with_note(7, ""), price_field_id=7)["price_paid"] is None
```

- [ ] **Step 5: Run them to verify they fail**

Run: `cd backend && pytest tests/test_discogs.py -k parse_release -v`
Expected: FAIL — `KeyError: 'price_paid'`

- [ ] **Step 6: Rename the key**

In `backend/discogs.py`:

```python
def parse_release(item: dict, price_field_id=None) -> dict:
    info = item["basic_information"]
    artist = info["artists"][0]["name"] if info.get("artists") else "Unknown"
    label = info["labels"][0]["name"] if info.get("labels") else ""
    fmt = info["formats"][0]["name"] if info.get("formats") else ""
    release_id = info["id"]
    price_paid = None
    if price_field_id is not None:
        for note in item.get("notes", []):
            if note.get("field_id") == price_field_id:
                price_paid = note.get("value") or None
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
        "price_paid": price_paid,
        "barcode": None,
    }
```

- [ ] **Step 7: Verify**

Run: `cd backend && pytest tests/test_discogs.py -v`
Expected: PASS

---

#### 3c. Write the per-user price from the collection loop

- [ ] **Step 8: Pass the price at the collection-loop call site**

In `backend/crawl_manager.py`, the full-parse path (the `upsert_library_item` call right after `upsert_catalog_release(conn, release)`, around `:429`):

```python
                            upsert_catalog_release(conn, release)
                            upsert_library_item(
                                conn, user_id, rid, in_collection=True,
                                collection_date_added=item.get("date_added"),
                                price_paid=release["price_paid"],
                            )
```

Leave the other two call sites passing nothing, so the sentinel makes them inherit:
- the `mode == "new"` early-continue call (around `:409`) — never calls `parse_release`, so it has no price
- the wantlist loop's call (around `:471`) — wantlist items carry no collection price field

- [ ] **Step 9: Add the sync-level tests**

Append to `backend/tests/test_crawl_manager.py` the four tests given in full below — the cross-tenant regression, the write-through, the `mode="new"` inherit, and the `mode="all"` clear.

```python
@respx.mock
async def test_collection_sync_without_a_price_field_keeps_another_users_price(pg_schema, monkeypatch):
    """The live cross-tenant bug: bob has no custom field named "Price", so
    parse_release yields no price for him. That must not erase alice's price
    for the same release. Under the old global catalog.discogs_price this
    assertion failed on every one of bob's syncs, and recurred forever."""
    import config
    import crawl_manager as crawl_manager_module
    import discogs
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "k")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "s")
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", "kL8mN2pQ7rT5vX9yB3cF6hJ1kM4nP8sU2wZ5aD7eG0i=")
    monkeypatch.setattr(crawl_manager_module.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(discogs, "fetch_release_barcode", lambda *a, **k: None)

    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        for user in (alice, bob):
            conn.execute(
                "UPDATE users SET discogs_oauth_token_encrypted = %s, "
                "discogs_oauth_secret_encrypted = %s WHERE id = %s",
                [token_encryption.encrypt("tok"), token_encryption.encrypt("sec"), user["id"]],
            )
        db.register_crawler(conn, "Amazon", "/x.py")
        db.upsert_catalog_release(conn, {
            "discogs_id": "r111", "artist": "Artist", "title": "Album", "year": 2020,
            "label": None, "format": None, "price_paid": None, "barcode": None,
            "cover_image_url": None, "discogs_url": None,
        })
        # Alice already recorded what she paid.
        db.upsert_library_item(conn, alice["id"], "r111", in_collection=True, price_paid="42.50")
        conn.commit()

    _release = {
        "basic_information": {
            "id": 111, "title": "Album", "year": 2020,
            "artists": [{"name": "Artist"}], "labels": [], "formats": [],
            "cover_image": "",
        },
        "date_added": "2024-03-15T10:00:00Z",
    }
    # Bob has custom fields, but none named "Price" -- so price_field_id is None.
    respx.get("https://api.discogs.com/users/bob/collection/fields").mock(
        return_value=httpx.Response(200, json={"fields": [{"id": 9, "name": "Notes"}]})
    )
    respx.get("https://api.discogs.com/users/bob/collection/folders/0/releases").mock(
        return_value=httpx.Response(200, json={
            "pagination": {"pages": 1}, "releases": [_release],
        })
    )
    respx.get("https://api.discogs.com/users/bob/wants").mock(
        return_value=httpx.Response(200, json={"pagination": {"pages": 1}, "wants": []})
    )

    manager = CrawlManager()
    await manager._sync_collection(bob["id"], "all")
    assert "sync_error" not in [e["status"] for e in manager.recent_events()]

    with db.get_admin_pool().connection() as conn:
        # Bob's sync really did process the release -- otherwise the assertion
        # below could pass without his write path ever running.
        assert conn.execute(
            "SELECT in_collection FROM library_items WHERE user_id = %s AND discogs_id = 'r111'",
            [bob["id"]],
        ).fetchone()["in_collection"] is True
        prices = {
            r["user_id"]: r["price_paid"]
            for r in conn.execute(
                "SELECT user_id, price_paid FROM library_items WHERE discogs_id = 'r111'"
            ).fetchall()
        }
    assert prices[alice["id"]] == "42.50"
    assert prices[bob["id"]] is None


@respx.mock
async def test_collection_sync_writes_the_matched_price_field_to_price_paid(pg_schema, monkeypatch):
    import config
    import crawl_manager as crawl_manager_module
    import discogs
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "k")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "s")
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", "kL8mN2pQ7rT5vX9yB3cF6hJ1kM4nP8sU2wZ5aD7eG0i=")
    monkeypatch.setattr(crawl_manager_module.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(discogs, "fetch_release_barcode", lambda *a, **k: None)

    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute(
            "UPDATE users SET discogs_oauth_token_encrypted = %s, "
            "discogs_oauth_secret_encrypted = %s WHERE id = %s",
            [token_encryption.encrypt("tok"), token_encryption.encrypt("sec"), alice["id"]],
        )
        db.register_crawler(conn, "Amazon", "/x.py")
        conn.commit()

    respx.get("https://api.discogs.com/users/alice/collection/fields").mock(
        return_value=httpx.Response(200, json={"fields": [{"id": 3, "name": "Price"}]})
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
                "date_added": "2024-03-15T10:00:00Z",
                "notes": [{"field_id": 3, "value": "42.50"}],
            }],
        })
    )
    respx.get("https://api.discogs.com/users/alice/wants").mock(
        return_value=httpx.Response(200, json={"pagination": {"pages": 1}, "wants": []})
    )

    manager = CrawlManager()
    await manager._sync_collection(alice["id"], "all")
    assert "sync_error" not in [e["status"] for e in manager.recent_events()]

    with db.get_admin_pool().connection() as conn:
        assert conn.execute(
            "SELECT price_paid FROM library_items WHERE user_id = %s AND discogs_id = 'r111'",
            [alice["id"]],
        ).fetchone()["price_paid"] == "42.50"


@respx.mock
async def test_mode_new_sync_leaves_an_existing_price_paid_intact(pg_schema, monkeypatch):
    """mode="new" takes the early-continue path for known releases, which never
    calls parse_release and so has no price in scope. It must inherit, not blank."""
    import config
    import crawl_manager as crawl_manager_module
    import discogs
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "k")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "s")
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", "kL8mN2pQ7rT5vX9yB3cF6hJ1kM4nP8sU2wZ5aD7eG0i=")
    monkeypatch.setattr(crawl_manager_module.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(discogs, "fetch_release_barcode", lambda *a, **k: None)

    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute(
            "UPDATE users SET discogs_oauth_token_encrypted = %s, "
            "discogs_oauth_secret_encrypted = %s WHERE id = %s",
            [token_encryption.encrypt("tok"), token_encryption.encrypt("sec"), alice["id"]],
        )
        db.register_crawler(conn, "Amazon", "/x.py")
        db.upsert_catalog_release(conn, {
            "discogs_id": "r111", "artist": "Artist", "title": "Album", "year": 2020,
            "label": None, "format": None, "price_paid": None, "barcode": None,
            "cover_image_url": None, "discogs_url": None,
        })
        db.upsert_library_item(conn, alice["id"], "r111", in_collection=True, price_paid="42.50")
        conn.commit()

    # No "Price" field, so even the full-parse path would yield None here --
    # which is what makes the skip path's inheritance observable.
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
                "date_added": "2024-03-15T10:00:00Z",
            }],
        })
    )
    respx.get("https://api.discogs.com/users/alice/wants").mock(
        return_value=httpx.Response(200, json={"pagination": {"pages": 1}, "wants": []})
    )

    manager = CrawlManager()
    await manager._sync_collection(alice["id"], "new")
    assert "sync_error" not in [e["status"] for e in manager.recent_events()]

    with db.get_admin_pool().connection() as conn:
        assert conn.execute(
            "SELECT price_paid FROM library_items WHERE user_id = %s AND discogs_id = 'r111'",
            [alice["id"]],
        ).fetchone()["price_paid"] == "42.50"


@respx.mock
async def test_mode_all_sync_clears_a_price_the_user_removed(pg_schema, monkeypatch):
    """The counterpart to the mode="new" test above, and the reason price_paid
    uses a sentinel rather than COALESCE. Same starting state and the same
    absent "Price" field -- only the mode differs, so a COALESCE implementation
    passes the mode="new" test and fails this one."""
    import config
    import crawl_manager as crawl_manager_module
    import discogs
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "k")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "s")
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", "kL8mN2pQ7rT5vX9yB3cF6hJ1kM4nP8sU2wZ5aD7eG0i=")
    monkeypatch.setattr(crawl_manager_module.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(discogs, "fetch_release_barcode", lambda *a, **k: None)

    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute(
            "UPDATE users SET discogs_oauth_token_encrypted = %s, "
            "discogs_oauth_secret_encrypted = %s WHERE id = %s",
            [token_encryption.encrypt("tok"), token_encryption.encrypt("sec"), alice["id"]],
        )
        db.register_crawler(conn, "Amazon", "/x.py")
        db.upsert_catalog_release(conn, {
            "discogs_id": "r111", "artist": "Artist", "title": "Album", "year": 2020,
            "label": None, "format": None, "price_paid": None, "barcode": None,
            "cover_image_url": None, "discogs_url": None,
        })
        db.upsert_library_item(conn, alice["id"], "r111", in_collection=True, price_paid="42.50")
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
                "date_added": "2024-03-15T10:00:00Z",
            }],
        })
    )
    respx.get("https://api.discogs.com/users/alice/wants").mock(
        return_value=httpx.Response(200, json={"pagination": {"pages": 1}, "wants": []})
    )

    manager = CrawlManager()
    await manager._sync_collection(alice["id"], "all")
    assert "sync_error" not in [e["status"] for e in manager.recent_events()]

    with db.get_admin_pool().connection() as conn:
        assert conn.execute(
            "SELECT price_paid FROM library_items WHERE user_id = %s AND discogs_id = 'r111'",
            [alice["id"]],
        ).fetchone()["price_paid"] is None
```


- [ ] **Step 10: Repoint the two existing wantlist price assertions**

`test_crawl_manager.py:456` and `:542` assert on `catalog.discogs_price` to pin which call site got `preserve_price`. That concern is gone with the flag. Change each test's seeding from `upsert_catalog_release(... "discogs_price": "42.50" ...)` to a `"price_paid": None` catalog dict plus `db.upsert_library_item(conn, user["id"], "r111", in_collection=True, price_paid="42.50")`, and each assertion to:

```python
    with db.get_admin_pool().connection() as conn:
        assert conn.execute(
            "SELECT price_paid FROM library_items WHERE user_id = %s AND discogs_id = 'r111'",
            [user["id"]],
        ).fetchone()["price_paid"] == "42.50"
```

Keep the surrounding `in_collection` / `in_wishlist` assertions in the full-sync test — they still prove both loops ran.

---

#### 3d. Repoint the Discogs tab read

- [ ] **Step 11: Write the failing tests**

Append to `backend/tests/test_releases_router.py`. Read the top of that file first and reuse its existing fixtures (`pg_test_db`, `authed_client_factory`) and crawler/user seeding helpers rather than inventing new ones — match the surrounding style.

```python
def test_list_releases_price_is_the_calling_users_own_price_paid(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "Artist", "title": "Album", "year": None,
            "label": None, "format": None, "price_paid": None, "barcode": None,
            "cover_image_url": None, "discogs_url": None,
        })
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True, price_paid="42.50")
        db.upsert_library_item(conn, bob["id"], "r1", in_collection=True, price_paid="9.99")
        conn.commit()

    body = authed_client_factory(alice["id"]).get(
        "/api/releases", params={"scope": "discogs"}
    ).json()
    assert body["releases"][0]["discogs_price"] == "42.50"

    body = authed_client_factory(bob["id"]).get(
        "/api/releases", params={"scope": "discogs"}
    ).json()
    assert body["releases"][0]["discogs_price"] == "9.99"


def test_list_releases_sorts_by_the_users_own_price(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        for rid, artist, price in (("r1", "Zed", "5.00"), ("r2", "Abe", "50.00")):
            db.upsert_catalog_release(conn, {
                "discogs_id": rid, "artist": artist, "title": "Album", "year": None,
                "label": None, "format": None, "price_paid": None, "barcode": None,
                "cover_image_url": None, "discogs_url": None,
            })
            db.upsert_library_item(conn, alice["id"], rid, in_collection=True, price_paid=price)
        conn.commit()

    body = authed_client_factory(alice["id"]).get(
        "/api/releases", params={"scope": "discogs", "sort": "discogs_price", "order": "asc"}
    ).json()
    # Seeded so price order and artist order disagree -- otherwise a silent
    # fallback to the default artist sort would pass this.
    assert [r["discogs_price"] for r in body["releases"]] == ["5.00", "50.00"]
```

- [ ] **Step 12: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_releases_router.py -k "price" -v`
Expected: FAIL — `discogs_price` comes back `None` (it is still read from `catalog`, which nothing writes as of Task 3).

- [ ] **Step 13: Repoint the read and the sort**

In `backend/db.py`, remove `discogs_price` from the allowed-sort set, since it is no longer a `catalog` column and must not reach the `f"c.{sort_col}"` path:

```python
_RELEASE_ALLOWED_SORT = {"artist", "title", "year", "label", "format"}
```

Add a `sort_expr` case ahead of the existing `date_added` one, and select the new column:

```python
    if sort == "discogs_price":
        sort_expr = "li.price_paid"
    elif sort == "date_added" and scope in ("discogs", "wishlist"):
        sort_expr = "li." + ("wishlist_date_added" if scope == "wishlist" else "collection_date_added")
    else:
        sort_col = sort if sort in _RELEASE_ALLOWED_SORT else "artist"
        sort_expr = f"c.{sort_col}"

    rows = conn.execute(
        f"""
        SELECT c.*, li.price_paid AS discogs_price, li.plex_url, li.plex_matched_at,
               li.collection_date_added, li.wishlist_date_added
        {base_from} {where}
        ORDER BY CASE WHEN {sort_expr} IS NULL THEN 1 ELSE 0 END {null_order}, {sort_expr} {order_sql}, c.discogs_id
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        params,
    ).fetchall()
```

**The trailing `, c.discogs_id` is required, and is not cosmetic.** Without a unique final term, a sort column that is NULL for *every* row ties all rows, the `ORDER BY` is unspecified, and Postgres' bounded top-N sort returns a different arbitrary order per page — so `LIMIT`/`OFFSET` pages repeat and drop rows. `c.discogs_id` is `catalog`'s primary key and `library_items` is filtered to a single `user_id`, so it is unique across this result set. `get_stock_items` already carries the equivalent as `, s.id` (`db.py:997`).

This change makes that hazard *more* likely, not less: immediately after deploy `price_paid` is NULL for every row until each user re-syncs, and the backfill deliberately leaves contested rows NULL — so a Price sort ties every row for nearly every user, not just those who never set the field.

**Provenance and conflict warning.** This term is the fix in PR #79 (`worktree-library-releases-pagination-tiebreak`, based on `main`), which measured 20/20 overlapping pages without it and 0/20 with it. It is reproduced here because this task rewrites the same line, so the two branches *will* conflict on it. **Resolving that conflict by taking this branch's side is only safe because the term is present here — do not drop it.** The regression test that would catch its loss (`test_get_library_releases_paginates_without_overlap_when_every_sort_key_is_null`, in `backend/tests/test_catalog_crud.py`) exists only on #79, so nothing on this branch fails if the term silently disappears. After merging, confirm the term survived and that #79's test passes against the merged result.

**Transient duplicate column, resolved in Task 4.** Until `catalog.discogs_price` is dropped, `c.*` also yields a `discogs_price` key. psycopg's `dict_row` is last-wins, and the alias is listed after `c.*`, so the per-user value wins — which is why the first test above seeds two different users and asserts each sees their own. Task 4 removes the ambiguity entirely. (Nothing writes the catalog column as of step 1 of this task, so in practice the stale side is always NULL — but do not rely on that; the alias ordering is what makes it correct.)

- [ ] **Step 14: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_releases_router.py -v`
Expected: PASS



---

#### 3e. Repoint the Store tab read

- [ ] **Step 15: Repoint both SQL sites**

There are already thorough tests for this projection; this task repoints the query and then repairs the tests' seeding. Change the sort expression:

```python
    if sort == "discogs_price" and "in_collection" in _LIBRARY_MEMBERSHIP.get(library_scope, ()):
        sort_expr = """(SELECT (regexp_match(li.price_paid, '\\d+\\.?\\d*'))[1]::numeric
                        {match} LIMIT 1)""".format(
            match=_library_match_fragment("%(user_id)s", "collection")
        )
```

And the projection:

```python
               (SELECT li.price_paid {_library_match_fragment('%(user_id)s', 'collection')} LIMIT 1) AS discogs_price
```

`_library_match_fragment` already binds `li`, so no structural change is needed. Leave the collection-pinning guard and its comment as they are — both remain correct.

- [ ] **Step 16: Repoint the test seeding in `test_stock_crud.py`**

In each case below the price moves from the `upsert_catalog_release` dict to the matching `upsert_library_item` call. Change `"discogs_price": "<value>"` to `"price_paid": None` in the catalog dict, and add `price_paid="<value>"` to the `upsert_library_item` call for that release.

- `:512` / `:518` — `test_get_stock_items_returns_matched_discogs_price_for_owned_item`, `"25.00"` → `db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True, price_paid="25.00")`
- `:552` / `:558` — `test_get_stock_items_comparison_rows_carry_owns_discogs_price`, `"25.00"` → same form
- `:648`, `:653` / `:656-657` — `_seed_collection_and_wantlist`, see Step 3 below
- `:756`, `:761` / `:764` — the `library_scope="all"` test, `"20.00"` → `db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True, in_wishlist=True, price_paid="20.00")`. The `r9` / `"99.00"` catalog row is never in alice's library, so it just becomes `"price_paid": None` with no `upsert_library_item` — that is the point of that row.

- [ ] **Step 17: Repair `_seed_collection_and_wantlist`'s premise**

This helper needs care: its `r2` price exists specifically so that `test_get_stock_items_discogs_price_is_null_for_a_wantlist_only_match` proves the subquery is *pinned to collection scope*, rather than merely finding no price. If `r2` ends up with no price anywhere, that test passes vacuously.

Keep the price present, on the wishlist row:

```python
def _seed_collection_and_wantlist(admin_conn):
    """Alice owns Artist A / Album A (paid 20.00) and wants Artist B / Album B
    (which carries a price on her wantlist row that must never be shown, since
    the price subquery is pinned to collection scope). Both are in stock."""
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
        {"artist": "Artist B", "title": "Album B", "url": "https://x/2", "price": 15.0, "currency": "USD"},
    ])
    for rid, artist, title in (("r1", "Artist A", "Album A"), ("r2", "Artist B", "Album B")):
        db.upsert_catalog_release(admin_conn, {
            "discogs_id": rid, "artist": artist, "title": title, "year": None, "label": None,
            "format": None, "price_paid": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True, price_paid="20.00")
    db.upsert_library_item(admin_conn, alice["id"], "r2", in_wishlist=True, price_paid="30.00")
    admin_conn.commit()
    return alice
```

Then update the comment in `test_get_stock_items_discogs_price_is_null_for_a_wantlist_only_match` (`:779-781`) to match the new mechanism:

```python
        # r2's library_items row carries price_paid "30.00", but the price
        # subquery is pinned to collection scope: she has not bought this one.
        assert result["items"][0]["discogs_price"] is None
```

- [ ] **Step 18: Repoint the test seeding in `test_stock_router.py`**

- `:269` / `:272` — `"20.00"` moves to `db.upsert_library_item(conn, user["id"], "r1", in_collection=True, price_paid="20.00")`
- `:291` / `:294` — this test asserts `discogs_price is None` under wishlist scope. Keep the price present so the assertion stays meaningful: `db.upsert_library_item(conn, user["id"], "r1", in_wishlist=True, price_paid="20.00")`, with `"price_paid": None` in the catalog dict.

- [ ] **Step 19: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_stock_crud.py tests/test_stock_router.py -v`
Expected: PASS



---

#### 3f. Clean up the obsolete catalog-price tests

- [ ] **Step 20: Delete tests of the retired flag and column**

From `backend/tests/test_catalog_crud.py`, delete:

- `test_upsert_catalog_release_preserve_price_keeps_the_stored_price` (`:55`)
- `test_upsert_catalog_release_preserve_price_still_writes_price_on_insert` (`:89`)
- `test_upsert_catalog_release_default_still_overwrites_price_with_none` (`:100`)

In `test_upsert_catalog_release_inserts_then_updates` (`:18`), drop the two `assert row["discogs_price"] == ...` lines (`:32`, `:49`) and change the `"discogs_price"` dict keys to `"price_paid": None`. Every other column assertion stays — that test still earns its keep.

---

#### 3g. Green gate

- [ ] **Step 21: Run the full backend suite**

Run: `cd backend && pytest`
Expected: PASS. If anything still fails, it is almost certainly a fixture seeding a price via `upsert_catalog_release` that needs the value moved to `upsert_library_item(..., price_paid=...)`. Fix it here; do not commit red.

`catalog.discogs_price` still exists at this point but is inert — nothing writes it and nothing reads it. Task 4 drops it.

- [ ] **Step 22: Commit**

```
fix: move the collection price to per-user library_items.price_paid

catalog.discogs_price was global but held a per-user value, so a sync from a
user with no custom "Price" field wrote None over every shared release's
price, recurring on every sync. Both read paths already joined library_items
per-user; only the write was global.

Adds the cross-tenant regression test whose absence let this ship, plus the
mode="new" inherit and mode="all" clear pair pinning the sentinel's branches.
Retires preserve_price, which had nothing left to guard. discogs_price stays
the wire name, so no frontend change.
```

---

### Task 4: Backfill and drop `catalog.discogs_price`

**Files:**
- Modify: `backend/db.py:65` (`GLOBAL_SCHEMA` catalog DDL), `TENANT_SCHEMA` (after the Task 1 migration)
- Test: `backend/tests/test_global_schema.py:17`, `backend/tests/test_tenant_schema.py`

- [ ] **Step 1: Write the failing tests**

Update the expected-column set in `backend/tests/test_global_schema.py:17`, dropping `discogs_price`:

```python
    assert cols == {
        "discogs_id", "artist", "title", "year", "label", "format",
        "barcode", "cover_image_url", "discogs_url", "last_synced",
    }
```

Append to `backend/tests/test_tenant_schema.py`. These tests simulate a pre-migration database by re-adding the column and seeding it, then re-running `init_tenant_schema()`:

```python
def _readd_legacy_catalog_price(admin_conn):
    """Recreate the pre-migration shape: init_tenant_schema() has already
    dropped the column by the time the fixture yields, so a migration test
    has to put it back before it has anything to migrate."""
    admin_conn.execute("ALTER TABLE catalog ADD COLUMN IF NOT EXISTS discogs_price TEXT")
    admin_conn.commit()


def test_backfill_copies_the_global_price_to_a_sole_collection_owner(admin_conn):
    _readd_legacy_catalog_price(admin_conn)
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.execute(
        "INSERT INTO catalog (discogs_id, artist, title, discogs_price) "
        "VALUES ('r1', 'A', 'T', '42.50')"
    )
    admin_conn.execute(
        "INSERT INTO library_items (user_id, discogs_id, in_collection) VALUES (%s, 'r1', TRUE)",
        [alice["id"]],
    )
    admin_conn.commit()

    db.init_tenant_schema()

    assert admin_conn.execute(
        "SELECT price_paid FROM library_items WHERE user_id = %s AND discogs_id = 'r1'",
        [alice["id"]],
    ).fetchone()["price_paid"] == "42.50"


def test_backfill_leaves_a_contested_release_null_for_everyone(admin_conn):
    # The global value is whichever user synced last, so with two owners it
    # cannot be attributed. Copying it to both would be the same cross-tenant
    # leak in reverse.
    _readd_legacy_catalog_price(admin_conn)
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    bob = db.create_user(admin_conn, discogs_user_id=2, discogs_username="bob")
    admin_conn.execute(
        "INSERT INTO catalog (discogs_id, artist, title, discogs_price) "
        "VALUES ('r1', 'A', 'T', '42.50')"
    )
    for user in (alice, bob):
        admin_conn.execute(
            "INSERT INTO library_items (user_id, discogs_id, in_collection) VALUES (%s, 'r1', TRUE)",
            [user["id"]],
        )
    admin_conn.commit()

    db.init_tenant_schema()

    prices = [
        r["price_paid"] for r in admin_conn.execute(
            "SELECT price_paid FROM library_items WHERE discogs_id = 'r1'"
        ).fetchall()
    ]
    assert prices == [None, None]


def test_backfill_skips_a_wantlist_only_holder(admin_conn):
    # A sole holder who only wants the release never paid this price.
    _readd_legacy_catalog_price(admin_conn)
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.execute(
        "INSERT INTO catalog (discogs_id, artist, title, discogs_price) "
        "VALUES ('r1', 'A', 'T', '42.50')"
    )
    admin_conn.execute(
        "INSERT INTO library_items (user_id, discogs_id, in_wishlist) VALUES (%s, 'r1', TRUE)",
        [alice["id"]],
    )
    admin_conn.commit()

    db.init_tenant_schema()

    assert admin_conn.execute(
        "SELECT price_paid FROM library_items WHERE user_id = %s AND discogs_id = 'r1'",
        [alice["id"]],
    ).fetchone()["price_paid"] is None


def test_migration_drops_the_global_catalog_price_column(admin_conn):
    _readd_legacy_catalog_price(admin_conn)
    db.init_tenant_schema()
    assert admin_conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'catalog' AND column_name = 'discogs_price'"
    ).fetchone() is None


def test_migration_is_idempotent_and_does_not_refill_a_cleared_price(admin_conn):
    """TENANT_SCHEMA re-runs on every boot. Once the source column is gone the
    guard must make the whole block a no-op -- otherwise a second boot either
    errors or resurrects a value the user deliberately cleared."""
    _readd_legacy_catalog_price(admin_conn)
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.execute(
        "INSERT INTO catalog (discogs_id, artist, title, discogs_price) "
        "VALUES ('r1', 'A', 'T', '42.50')"
    )
    admin_conn.execute(
        "INSERT INTO library_items (user_id, discogs_id, in_collection) VALUES (%s, 'r1', TRUE)",
        [alice["id"]],
    )
    admin_conn.commit()

    db.init_tenant_schema()
    admin_conn.execute(
        "UPDATE library_items SET price_paid = NULL WHERE user_id = %s AND discogs_id = 'r1'",
        [alice["id"]],
    )
    admin_conn.commit()

    db.init_tenant_schema()

    assert admin_conn.execute(
        "SELECT price_paid FROM library_items WHERE user_id = %s AND discogs_id = 'r1'",
        [alice["id"]],
    ).fetchone()["price_paid"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_tenant_schema.py tests/test_global_schema.py -k "backfill or migration or catalog_table" -v`
Expected: FAIL — the backfill tests get `None`, the drop test still finds the column, and the `catalog` column-set assertion fails on the extra `discogs_price`.

- [ ] **Step 3: Remove the column from `GLOBAL_SCHEMA`**

In `backend/db.py:65`, delete the `discogs_price TEXT,` line from `CREATE TABLE IF NOT EXISTS catalog`:

```sql
CREATE TABLE IF NOT EXISTS catalog (
    discogs_id TEXT PRIMARY KEY,
    artist TEXT NOT NULL,
    title TEXT NOT NULL,
    year INTEGER,
    label TEXT,
    format TEXT,
    barcode TEXT,
    cover_image_url TEXT,
    discogs_url TEXT,
    last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

A fresh database now never creates the column. On an existing database `CREATE TABLE IF NOT EXISTS` is a no-op, so the column survives there until the `DO` block below drops it — which is exactly the behaviour the backfill needs.

- [ ] **Step 4: Add the guarded backfill-and-drop block**

In `TENANT_SCHEMA`, **after** the `ALTER TABLE library_items ADD COLUMN IF NOT EXISTS price_paid TEXT` line from Task 1:

```sql
-- One-shot, self-retiring migration off the global catalog.discogs_price.
-- The guard is what makes it safe to leave in a schema string that re-runs on
-- every boot: once the source column is gone this whole block is a no-op, so
-- it can neither error nor resurrect a price a user deliberately cleared.
--
-- Only a release with exactly one collection owner is backfilled. The stored
-- global value is whichever user synced last, so with two owners it cannot be
-- attributed to either; copying it to both would be the same cross-tenant leak
-- in reverse. Contested rows stay NULL and self-heal on the owner's next
-- mode="all" sync.
--
-- Must live in TENANT_SCHEMA, not GLOBAL_SCHEMA: GLOBAL_SCHEMA runs first
-- (main.py), before library_items.price_paid exists.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name = 'catalog' AND column_name = 'discogs_price') THEN
    UPDATE library_items li SET price_paid = c.discogs_price
    FROM catalog c
    WHERE c.discogs_id = li.discogs_id
      AND li.in_collection = TRUE
      AND c.discogs_price IS NOT NULL
      AND (SELECT COUNT(*) FROM library_items x
           WHERE x.discogs_id = li.discogs_id AND x.in_collection = TRUE) = 1;

    ALTER TABLE catalog DROP COLUMN discogs_price;
  END IF;
END $$;
```

The block runs on the admin pool (`init_tenant_schema` uses `get_admin_pool()`, whose role is `BYPASSRLS`), so the cross-user `COUNT(*)` is not filtered by RLS.

- [ ] **Step 5: Verify the `DO` block survives the multi-statement execute**

`TENANT_SCHEMA` is applied as one string via `conn.execute(TENANT_SCHEMA)` with no parameters, which psycopg 3 sends using the simple query protocol — multiple statements and `$$` dollar-quoting are both fine, and there is no `%` in the block to be mistaken for a placeholder. Confirm rather than assume:

Run: `cd backend && pytest tests/test_tenant_schema.py -v`
Expected: PASS.

If it instead raises a syntax error around `$$`, do not fight the schema string — move just this `DO` block out into its own `conn.execute(...)` call in `init_tenant_schema()`, placed immediately after `conn.execute(TENANT_SCHEMA)`. Ordering still holds, since `price_paid` is created by the line above.

- [ ] **Step 6: Run the full suite**

Run: `cd backend && pytest`
Expected: PASS

- [ ] **Step 7: Commit**

```
feat: backfill and drop the global catalog.discogs_price

Self-retiring guarded DO block: backfills only releases with exactly one
collection owner, where the global value provably belongs to that user, then
drops the column. Later boots no-op because the guard fails.
```

---

### Task 5: Correct the specs that made the global write look right

**Files:**
- Modify: `docs/superpowers/specs/2026-07-26-multi-tenant-architecture-design.md:191`, `:396`
- Modify: `docs/superpowers/specs/2026-06-27-discogs-browser-design.md:87`

- [ ] **Step 1: Fix the multi-tenant data dictionary**

`:191` currently reads:

```
| `discogs_price`    | TEXT      | Discogs' own marketplace figure — global  |
```

That is factually wrong and causally implicated in the bug: it makes an unconditional global overwrite look correct. Delete the row from the `catalog` dictionary and add one to the `library_items` dictionary in the same document, matching that table's existing row format:

```
| `price_paid`       | TEXT      | Contents of the user's own custom Discogs collection field named "Price" — per-user |
```

Read the surrounding table before editing so the column widths and phrasing match the neighbouring rows.

- [ ] **Step 2: Fix the column list at `:396`**

That line enumerates the catalog columns a sync writes (`... discogs_price, barcode, cover_image_url, discogs_url`). Remove `discogs_price` from it, and note that the per-user price goes to `library_items` alongside `in_collection`.

- [ ] **Step 3: Fix the original design spec**

`docs/superpowers/specs/2026-06-27-discogs-browser-design.md:87` reads:

```
| `discogs_price` | TEXT | User's purchase price from Discogs collection field |
```

The description is correct but sits in the `catalog`/`releases` table — the exact mismatch this change repairs. Move it to the `library_items` table in that document as `price_paid`, keeping the wording.

- [ ] **Step 4: Add a correction note**

Both documents describe shipped architecture, so record the change rather than silently rewriting history. Add a short dated note in the style each document already uses for as-implemented deviations (grep for existing "as-implemented" or "UPDATE" notes and match the local convention):

```
**Correction (2026-08-09):** `discogs_price` was documented here as a global
marketplace figure. It never was — it holds the contents of a per-user custom
Discogs collection field, and storing it on the global `catalog` row caused
recurring cross-tenant data loss. Moved to `library_items.price_paid`; see
`docs/specifications/shaping/2026-08-09-library-price-paid-design.md`.
```

- [ ] **Step 5: Run the required pre-PR spec-drift check**

Per `CLAUDE.md`, check every spec, not just these two:

```bash
grep -rln "discogs_price\|preserve_price\|upsert_catalog_release\|upsert_library_item" docs/superpowers/specs/ docs/specifications/shaping/
```

For each match, confirm the text still describes what shipped. Two known cases to judge rather than blanket-edit:
- `2026-07-08-collection-price-crawlers-design.md:15` and `:110` already describe `discogs_price` correctly as a per-user custom field, not a marketplace price. Update the column reference, keep the reasoning.
- `2026-07-08-plex-integration-design.md:48` cites `discogs_price` as a cardinality precedent for `plex_url`. That argument still holds — `plex_url` is per-user on `library_items` too, which is now a *better* analogy. Update the reference.

Plans (`docs/superpowers/plans/`, `docs/specifications/plans/`) are historical task logs and are explicitly out of scope for this check.

**Expect a second conflict with PR #79 here.** `docs/specifications/shaping/2026-08-08-discogs-tab-rename-design.md` contains a `discogs_price` reference (`:15`) so it will surface in the sweep above, and #79 also amends that file — recording its new `ORDER BY` term and correcting a pre-existing drift in the spec's `_RELEASE_ALLOWED_SORT` bullet (the allow-list never gained `"date_added"`; that became a scope-gated branch ahead of the lookup, which builds an `li.`-prefixed `sort_expr`). That correction is independent of this change and is also *compounded* by it, since Task 3 adds a second such branch for `discogs_price`. When resolving, keep both edits: #79's `date_added` correction and this branch's `discogs_price` one. Do not rewrite the bullet from scratch from either side alone.

Note in the eventual PR description what drift was found and fixed, and that the `ORDER BY` tiebreaker from #79 was preserved.

- [ ] **Step 6: Commit**

```
docs: correct the catalog data dictionary's discogs_price entry

It was documented as "Discogs' own marketplace figure — global". It is a
per-user custom collection field, and that wrong description made an
unconditional global overwrite look correct. Moved to library_items.price_paid.
```

---

### Task 6: Version bump and final verification

**Files:**
- Modify: `backend/version.py`

- [ ] **Step 1: Bump the version**

`CLAUDE.md` requires a minor bump on every PR that merges to `main`, automatically and regardless of size. Never take a major bump without the repo owner's explicit instruction.

```python
VERSION = "3.3"
```

- [ ] **Step 2: Verify nothing references the retired column**

```bash
cd backend && grep -rn "discogs_price" . --include="*.py" | grep -v "/tests/"
```

Expected: matches only where `discogs_price` is the deliberate **wire/JSON name** — the `AS discogs_price` aliases and the `sort == "discogs_price"` comparisons in `db.py`. No `catalog.discogs_price` or `c.discogs_price` reference should remain.

- [ ] **Step 3: Run the full backend suite**

Run: `cd backend && pytest`
Expected: PASS, with no skips introduced by this branch. Paste the summary line into the PR description — do not claim green without it.

- [ ] **Step 4: Run the frontend suite**

No frontend file was changed, since `discogs_price` remains the wire name. Prove that rather than assuming it:

Run: `cd frontend && npm run test`
Expected: PASS

- [ ] **Step 5: Commit**

```
chore: bump version to 3.3
```

---

## Definition of done

- `library_items.price_paid` exists; `catalog.discogs_price` does not.
- A collection sync from a user with no `"Price"` field leaves every other user's recorded price intact — proven by `test_collection_sync_without_a_price_field_keeps_another_users_price`.
- Both read paths return the calling user's own value under the `discogs_price` wire name; no frontend change.
- `preserve_price` is gone from the codebase.
- A price clears on a `mode="all"` sync once the user removes their `"Price"` field, and inherits on `mode="new"` — both branches of the `_UNSET` sentinel have a test.
- Re-running `init_tenant_schema()` neither errors nor resurrects a cleared price.
- Backend and frontend suites pass, with output pasted into the PR.
- Both spec data dictionaries describe the column correctly, and the drift sweep is recorded in the PR description.
- `get_library_releases`' `ORDER BY` still ends in `c.discogs_id`, and PR #79's `date_added` spec correction survived the merge alongside this branch's `discogs_price` one.
