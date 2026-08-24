# Store & Track Source Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the localStorage-only crawler "View" toggle (buried in Settings) with a per-user, server-persisted "Source" filter reachable from the Store/Track tab headers, grouped by a new coarse genre attribute (marketplace/punk/metal/rock/pop), and remove the now-empty Settings tab for non-admin users.

**Architecture:** A new `user_hidden_crawlers` join table (mirroring `library_items`/`stock_item_judgments`'s per-user RLS-isolated shape) replaces the `localStorage` hidden-crawler-id set. A new `genre` plugin class attribute (mirroring the existing `genre_summary`/`base_url` pattern — no DB column, no migration) lets the frontend group crawlers for bulk genre toggling. A new `SourceFilter` component renders a dropdown in `StockBrowser`'s header; `App.tsx` fetches/persists the hidden set via two new endpoints instead of `localStorage`.

**Tech Stack:** FastAPI + psycopg (backend), React + TypeScript + Vitest/Testing Library (frontend), Postgres RLS.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`, use `Optional[str]` or bare `list[int]` (PEP 585 generics on builtins are fine on 3.9+).
- No comments except where the WHY is non-obvious; no backwards-compat shims.
- Every commit needs the AI-attribution trailer block (see repo `CLAUDE.md`) — use `git commit -F <message-file>`, never `-m`, so trailers survive.
- `hidden` = a crawler the current user has chosen not to see. Default (never touched the filter) = nothing hidden = everything visible.
- Genre is one of exactly `marketplace | punk | metal | rock | pop`. Release-type crawlers (Amazon, eBay, eBay/CCmusic, Discogs Marketplace) are always `marketplace` and never set the attribute explicitly.

Reference: [`docs/superpowers/specs/2026-08-16-store-track-source-filter-design.md`](../specs/2026-08-16-store-track-source-filter-design.md)

---

## Task 1: `user_hidden_crawlers` table, RLS, and db.py helpers

**Files:**
- Modify: `backend/db.py:389-396` (TENANT_SCHEMA — new table), `backend/db.py:428-435` (RLS enable), `backend/db.py:465-474` (RLS policy), `backend/db.py:546` (grant), `backend/db.py:1804-1807` (new helper functions)
- Test: `backend/tests/test_rls_isolation.py`

**Interfaces:**
- Produces: `db.get_hidden_crawler_ids(conn, user_id: int) -> list[int]`, `db.set_hidden_crawler_ids(conn, user_id: int, crawler_ids: list[int]) -> None` — both called through `db.user_scope(user_id)`, same as `db.clear_stock_judgments`/`db.upsert_stock_judgments`.

- [ ] **Step 1: Write the failing RLS isolation tests**

Add to `backend/tests/test_rls_isolation.py`, after the existing `test_aggregate_query_also_respects_isolation` test:

```python
@pytest.fixture
def two_users_one_shared_crawler(pg_test_db, monkeypatch):
    db.init_global_schema()
    db.init_tenant_schema()
    monkeypatch.setattr(
        db.config,
        "APP_DATABASE_URL",
        db.config._with_userinfo(
            os.environ["TEST_DATABASE_URL"], "app_user", os.environ["APP_DB_PASSWORD"]
        ),
    )
    with db.get_admin_pool().connection() as admin:
        alice = admin.execute(
            "INSERT INTO users (discogs_user_id, discogs_username) VALUES (1, 'alice') RETURNING id",
        ).fetchone()
        bob = admin.execute(
            "INSERT INTO users (discogs_user_id, discogs_username) VALUES (2, 'bob') RETURNING id",
        ).fetchone()
        db.register_crawler(admin, "Amazon", "/x.py")
        crawler = admin.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()
        # Both users hide the same crawler in their own row, so a leak in
        # either direction has something real to leak.
        admin.execute(
            "INSERT INTO user_hidden_crawlers (user_id, crawler_id) VALUES (%s, %s)",
            [alice["id"], crawler["id"]],
        )
        admin.commit()

    yield alice["id"], bob["id"], crawler["id"]

    with db.get_admin_pool().connection() as admin:
        admin.execute("TRUNCATE users, crawlers, user_hidden_crawlers CASCADE")
        admin.commit()


def test_user_sees_only_their_own_hidden_crawlers(two_users_one_shared_crawler):
    alice_id, _bob_id, crawler_id = two_users_one_shared_crawler
    with db.user_scope(alice_id) as conn:
        assert db.get_hidden_crawler_ids(conn, alice_id) == [crawler_id]


def test_other_user_does_not_see_the_first_users_hidden_crawler(two_users_one_shared_crawler):
    _alice_id, bob_id, _crawler_id = two_users_one_shared_crawler
    with db.user_scope(bob_id) as conn:
        assert db.get_hidden_crawler_ids(conn, bob_id) == []


def test_set_hidden_crawler_ids_replaces_the_full_set(two_users_one_shared_crawler):
    alice_id, _bob_id, crawler_id = two_users_one_shared_crawler
    with db.get_admin_pool().connection() as admin:
        db.register_crawler(admin, "eBay", "/y.py")
        second = admin.execute("SELECT id FROM crawlers WHERE site_name = 'eBay'").fetchone()["id"]
        admin.commit()

    with db.user_scope(alice_id) as conn:
        db.set_hidden_crawler_ids(conn, alice_id, [second])
        conn.commit()

    with db.user_scope(alice_id) as conn:
        assert db.get_hidden_crawler_ids(conn, alice_id) == [second]
    # crawler_id (the original hidden one) must be gone -- this is a replace,
    # not a merge.
    with db.user_scope(alice_id) as conn:
        assert crawler_id not in db.get_hidden_crawler_ids(conn, alice_id)


def test_set_hidden_crawler_ids_with_mismatched_user_id_is_rejected(two_users_one_shared_crawler):
    alice_id, bob_id, crawler_id = two_users_one_shared_crawler
    with db.user_scope(alice_id) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO user_hidden_crawlers (user_id, crawler_id) VALUES (%s, %s)",
                [bob_id, crawler_id],
            )
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_rls_isolation.py -v`
Expected: FAIL — `user_hidden_crawlers` table does not exist, and `db.get_hidden_crawler_ids`/`db.set_hidden_crawler_ids` are not defined.

- [ ] **Step 3: Add the table, RLS policy, and grant**

In `backend/db.py`, insert immediately after the `stock_item_judgments` table definition (after the closing `);` currently at line 396, before `CREATE TABLE IF NOT EXISTS invites`):

```sql
CREATE TABLE IF NOT EXISTS user_hidden_crawlers (
    user_id INTEGER NOT NULL REFERENCES users(id),
    crawler_id INTEGER NOT NULL REFERENCES crawlers(id),
    PRIMARY KEY (user_id, crawler_id)
);
```

Immediately after `ALTER TABLE stock_item_judgments FORCE ROW LEVEL SECURITY;` (currently line 435):

```sql
ALTER TABLE user_hidden_crawlers ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_hidden_crawlers FORCE ROW LEVEL SECURITY;
```

Immediately after the `stock_item_judgments_isolation` policy (currently lines 470-473, right before the closing `"""` of `TENANT_SCHEMA`):

```sql
DROP POLICY IF EXISTS user_hidden_crawlers_isolation ON user_hidden_crawlers;
CREATE POLICY user_hidden_crawlers_isolation ON user_hidden_crawlers
    USING (user_id = current_setting('app.user_id', true)::int)
    WITH CHECK (user_id = current_setting('app.user_id', true)::int);
```

Immediately after `conn.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON stock_item_judgments TO app_user")` (currently line 546):

```python
        conn.execute("GRANT SELECT, INSERT, DELETE ON user_hidden_crawlers TO app_user")
```

- [ ] **Step 4: Add the db.py helper functions**

In `backend/db.py`, immediately after `clear_stock_judgments` (currently ends at line 1804 with `return cursor.rowcount`), before `get_recommended_stock_items`:

```python
def get_hidden_crawler_ids(conn, user_id: int) -> list[int]:
    rows = conn.execute(
        "SELECT crawler_id FROM user_hidden_crawlers WHERE user_id = %s", [user_id]
    ).fetchall()
    return [row["crawler_id"] for row in rows]


def set_hidden_crawler_ids(conn, user_id: int, crawler_ids: list[int]):
    conn.execute("DELETE FROM user_hidden_crawlers WHERE user_id = %s", [user_id])
    if crawler_ids:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO user_hidden_crawlers (user_id, crawler_id) VALUES (%s, %s)",
                [(user_id, cid) for cid in crawler_ids],
            )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_rls_isolation.py -v`
Expected: PASS (all tests in the file, including the pre-existing `library_items` ones).

- [ ] **Step 6: Commit**

```bash
git add backend/db.py backend/tests/test_rls_isolation.py
git commit -F - <<'EOF'
Add user_hidden_crawlers table with RLS isolation

Per-user hidden-crawler-id set, replacing the localStorage-only
mechanism. Mirrors library_items/stock_item_judgments exactly.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
EOF
```

---

## Task 2: `genre` field on `get_all_crawlers()`

**Files:**
- Modify: `backend/db.py:956-978` (`get_all_crawlers`)
- Test: `backend/tests/test_crawler_crud.py`

**Interfaces:**
- Produces: every dict returned by `db.get_all_crawlers(conn)` gains a `"genre"` key, one of `marketplace | punk | metal | rock | pop`, defaulting to `"marketplace"` when the plugin sets no `genre` attribute or fails to import.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_crawler_crud.py`, after `test_get_all_crawlers_genre_summary_defaults_to_none`:

```python
def test_get_all_crawlers_reads_genre(admin_conn, tmp_path):
    crawler_file = tmp_path / "genre_field_test_crawler.py"
    crawler_file.write_text(
        "class Crawler:\n"
        "    site_name = 'Genre Field Test Store'\n"
        "    genre = 'punk'\n"
    )
    db.register_crawler(admin_conn, "Genre Field Test Store", str(crawler_file), crawler_type="catalog")
    admin_conn.commit()

    crawlers = db.get_all_crawlers(admin_conn)
    row = next(c for c in crawlers if c["site_name"] == "Genre Field Test Store")
    assert row["genre"] == "punk"


def test_get_all_crawlers_genre_defaults_to_marketplace(admin_conn, tmp_path):
    crawler_file = tmp_path / "no_genre_field_test_crawler.py"
    crawler_file.write_text(
        "class Crawler:\n"
        "    site_name = 'No Genre Field Test Store'\n"
    )
    db.register_crawler(admin_conn, "No Genre Field Test Store", str(crawler_file))
    admin_conn.commit()

    crawlers = db.get_all_crawlers(admin_conn)
    row = next(c for c in crawlers if c["site_name"] == "No Genre Field Test Store")
    assert row["genre"] == "marketplace"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_crawler_crud.py -k genre -v`
Expected: FAIL — `KeyError: 'genre'`.

- [ ] **Step 3: Implement**

In `backend/db.py`, `get_all_crawlers` currently reads:

```python
            d["base_url"] = getattr(mod.Crawler, "base_url", None)
            d["genre_summary"] = getattr(mod.Crawler, "genre_summary", None)
        except Exception as e:
            log.warning("Could not load crawler plugin %s for base_url/genre_summary: %s", d["module_path"], e)
            d["base_url"] = None
            d["genre_summary"] = None
```

Change to:

```python
            d["base_url"] = getattr(mod.Crawler, "base_url", None)
            d["genre_summary"] = getattr(mod.Crawler, "genre_summary", None)
            d["genre"] = getattr(mod.Crawler, "genre", "marketplace")
        except Exception as e:
            log.warning("Could not load crawler plugin %s for base_url/genre_summary/genre: %s", d["module_path"], e)
            d["base_url"] = None
            d["genre_summary"] = None
            d["genre"] = "marketplace"
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_crawler_crud.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add backend/db.py backend/tests/test_crawler_crud.py
git commit -F - <<'EOF'
Read genre attribute in get_all_crawlers, default marketplace

Mirrors the existing base_url/genre_summary plugin-attribute pattern.
No DB column, no migration.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
EOF
```

---

## Task 3: `GET`/`POST /api/user-hidden-crawlers` endpoints

**Files:**
- Modify: `backend/routers/settings.py`
- Test: `backend/tests/test_settings_router.py`

**Interfaces:**
- Consumes: `db.get_hidden_crawler_ids`, `db.set_hidden_crawler_ids` (Task 1), `db.user_scope` (existing).
- Produces: `GET /api/user-hidden-crawlers` → `{"hidden_crawler_ids": number[]}`; `POST /api/user-hidden-crawlers` body `{"hidden_crawler_ids": number[]}` → `{"ok": true}`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_settings_router.py`, after `test_post_user_settings_with_empty_plex_base_url_skips_validation`:

```python
def test_get_user_hidden_crawlers_defaults_to_empty(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(user["id"])
    r = client.get("/api/user-hidden-crawlers")
    assert r.status_code == 200
    assert r.json() == {"hidden_crawler_ids": []}


def test_post_user_hidden_crawlers_round_trips(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(user["id"])

    r = client.post(
        "/api/user-hidden-crawlers",
        json={"hidden_crawler_ids": [crawler_id]},
        headers={"X-Requested-With": "fetch"},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    r = client.get("/api/user-hidden-crawlers")
    assert r.json() == {"hidden_crawler_ids": [crawler_id]}


def test_post_user_hidden_crawlers_replaces_not_merges(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        db.register_crawler(conn, "eBay", "/y.py")
        amazon_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        ebay_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'eBay'").fetchone()["id"]
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(user["id"])

    client.post("/api/user-hidden-crawlers", json={"hidden_crawler_ids": [amazon_id]}, headers={"X-Requested-With": "fetch"})
    r = client.post("/api/user-hidden-crawlers", json={"hidden_crawler_ids": [ebay_id]}, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200

    r = client.get("/api/user-hidden-crawlers")
    assert r.json() == {"hidden_crawler_ids": [ebay_id]}


def test_user_hidden_crawlers_are_isolated_between_users(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        conn.commit()

    alice_client = authed_client_factory(alice["id"])
    bob_client = authed_client_factory(bob["id"])
    alice_client.post("/api/user-hidden-crawlers", json={"hidden_crawler_ids": [crawler_id]}, headers={"X-Requested-With": "fetch"})

    r = bob_client.get("/api/user-hidden-crawlers")
    assert r.json() == {"hidden_crawler_ids": []}
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_settings_router.py -k hidden_crawlers -v`
Expected: FAIL — 404, endpoint does not exist.

- [ ] **Step 3: Implement**

In `backend/routers/settings.py`, add after `update_user_settings` (end of file):

```python
class UserHiddenCrawlersUpdate(BaseModel):
    hidden_crawler_ids: list[int] = []


@router.get("/user-hidden-crawlers")
def get_user_hidden_crawlers(request: Request):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        return {"hidden_crawler_ids": db.get_hidden_crawler_ids(conn, user_id)}


@router.post("/user-hidden-crawlers")
def update_user_hidden_crawlers(body: UserHiddenCrawlersUpdate, request: Request):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        db.set_hidden_crawler_ids(conn, user_id, body.hidden_crawler_ids)
        conn.commit()
    return {"ok": True}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_settings_router.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add backend/routers/settings.py backend/tests/test_settings_router.py
git commit -F - <<'EOF'
Add GET/POST /api/user-hidden-crawlers endpoints

Self-service (no admin gate), full-replace semantics, mirroring
update_user_settings.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
EOF
```

---

## Task 4: Seed `genre` on every catalog crawler plugin

**Files:**
- Modify: all files listed in the table below, under `backend/crawlers/`
- Test: `backend/tests/test_main.py`

**Interfaces:**
- Consumes: Task 2's `get_all_crawlers()` genre reading.

- [ ] **Step 1: Write the failing integration test**

In `backend/tests/test_main.py`, add after `test_startup_seeds_catalog_crawlers_with_genre_summary`:

```python
def test_startup_seeds_catalog_crawlers_with_genre(pg_test_db):
    with patch("main.crawl_manager.start_worker_pool", new=AsyncMock()), \
         patch("main.crawl_manager.stop_worker_pool", new=AsyncMock()):
        import main
        with TestClient(main.app):
            with db.get_admin_pool().connection() as conn:
                crawlers = db.get_all_crawlers(conn)

    catalog_crawlers = [c for c in crawlers if c["crawler_type"] in ("catalog", "catalog_browser")]
    release_crawlers = [c for c in crawlers if c["crawler_type"] == "release"]
    valid_genres = {"marketplace", "punk", "metal", "rock", "pop"}

    assert len(catalog_crawlers) >= 36
    invalid = {c["site_name"]: c["genre"] for c in catalog_crawlers if c["genre"] not in valid_genres}
    assert invalid == {}
    assert len(release_crawlers) >= 4
    assert all(c["genre"] == "marketplace" for c in release_crawlers)

    century_media = next(c for c in catalog_crawlers if c["site_name"] == "Century Media")
    assert century_media["genre"] == "metal"
    epitaph = next(c for c in catalog_crawlers if c["site_name"] == "Epitaph")
    assert epitaph["genre"] == "punk"
    amoeba = next(c for c in catalog_crawlers if c["site_name"] == "Amoeba Music")
    assert amoeba["genre"] == "marketplace"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_main.py -k genre -v`
Expected: FAIL — every catalog crawler currently defaults to `"marketplace"` (Task 2's fallback), so `century_media["genre"] == "metal"` fails.

- [ ] **Step 3: Add `genre` to each catalog crawler plugin**

In each file below, add a `genre: str = "<value>"` class attribute immediately after the existing `genre_summary: str = "..."` line:

| File | Site | genre |
|---|---|---|
| `backend/crawlers/amoeba.py` | Amoeba Music | `marketplace` |
| `backend/crawlers/angryyoungandpoor.py` | Angry Young and Poor | `punk` |
| `backend/crawlers/asbestosrecords.py` | Asbestos Records | `punk` |
| `backend/crawlers/asianmanrecords.py` | Asian Man Records | `punk` |
| `backend/crawlers/bigscarymonstersusa.py` | Big Scary Monsters USA | `punk` |
| `backend/crawlers/centurymedia.py` | Century Media | `metal` |
| `backend/crawlers/cleorecs.py` | Cleopatra Records | `marketplace` |
| `backend/crawlers/closedcasketactivities.py` | Closed Casket Activities | `punk` |
| `backend/crawlers/craftrecordings.py` | Craft Recordings | `marketplace` |
| `backend/crawlers/deathwishinc.py` | Deathwish Inc | `punk` |
| `backend/crawlers/epitaph.py` | Epitaph | `punk` |
| `backend/crawlers/equalvision.py` | Equal Vision | `punk` |
| `backend/crawlers/fatpossum.py` | Fat Possum | `rock` |
| `backend/crawlers/fatwreck.py` | Fat Wreck Chords | `punk` |
| `backend/crawlers/fatherdaughterrecords.py` | Father/Daughter Records | `rock` |
| `backend/crawlers/fearlessrecords.py` | Fearless Records | `punk` |
| `backend/crawlers/flatspotrecords.py` | Flatspot Records | `punk` |
| `backend/crawlers/jackpotrecords.py` | Jackpot Records | `marketplace` |
| `backend/crawlers/jadetree.py` | Jade Tree Records | `rock` |
| `backend/crawlers/killrockstars.py` | Kill Rock Stars | `rock` |
| `backend/crawlers/napalmrecords.py` | Napalm Records | `metal` |
| `backend/crawlers/newburycomics.py` | Newbury Comics | `marketplace` |
| `backend/crawlers/nuclearblast.py` | Nuclear Blast | `metal` |
| `backend/crawlers/numerogroup.py` | Numero Group | `marketplace` |
| `backend/crawlers/peaceville.py` | Peaceville | `metal` |
| `backend/crawlers/piratespressrecords.py` | Pirates Press Records | `punk` |
| `backend/crawlers/polyvinylrecords.py` | Polyvinyl Record Co. | `rock` |
| `backend/crawlers/prostheticrecords.py` | Prosthetic Records | `metal` |
| `backend/crawlers/relapse.py` | Relapse | `metal` |
| `backend/crawlers/revhq.py` | Rev HQ | `punk` |
| `backend/crawlers/riserecords.py` | Rise Records | `punk` |
| `backend/crawlers/runforcoverrecords.py` | Run For Cover | `punk` |
| `backend/crawlers/saddlecreek.py` | Saddle Creek | `rock` |
| `backend/crawlers/seasonofmist.py` | Season of Mist | `metal` |
| `backend/crawlers/secretlystore.py` | Secretly Store | `rock` |
| `backend/crawlers/sgrecordshop.py` | The Sound Garden | `marketplace` |
| `backend/crawlers/subpopmegamart.py` | Sub Pop Mega Mart | `rock` |
| `backend/crawlers/temporaryresidence.py` | Temporary Residence Ltd | `rock` |
| `backend/crawlers/triplebrecords.py` | Triple B Records | `punk` |
| `backend/crawlers/turntablelab.py` | Turntable Lab | `marketplace` |
| `backend/crawlers/twentybuckspin.py` | 20 Buck Spin | `metal` |

For example, `backend/crawlers/epitaph.py` currently has:

```python
    genre_summary: str = "Punk rock label."
```

Change to:

```python
    genre_summary: str = "Punk rock label."
    genre: str = "punk"
```

Repeat for every file using the table above. The four release-type crawlers (`amazon.py`, `discogs_marketplace.py`, `ebay.py`, `ebay_general.py`) are **not** touched — they keep defaulting to `"marketplace"` via Task 2's `getattr` fallback, exactly like they already do for `genre_summary`.

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_main.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest`
Expected: PASS (no regressions from the 40-file edit; run tests in the foreground, not in parallel with any other pytest run against the same Postgres cluster).

- [ ] **Step 6: Commit**

```bash
git add backend/crawlers/ backend/tests/test_main.py
git commit -F - <<'EOF'
Seed genre on every catalog crawler plugin

Derived from each crawler's existing genre_summary; anything spanning
unrelated genres or too broad to map cleanly is marketplace.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
EOF
```

---

## Task 5: Frontend types + API client functions

**Files:**
- Modify: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`, `frontend/src/test/settings.test.tsx`
- Test: `frontend/src/test/client.test.ts`

**Interfaces:**
- Produces: `CrawlerGenre` type; `Crawler.genre: CrawlerGenre`; `getUserHiddenCrawlers(): Promise<number[]>`; `postUserHiddenCrawlers(ids: number[]): Promise<void>`.

- [ ] **Step 1: Write the failing client tests**

Add to `frontend/src/test/client.test.ts`, after the `saveUserSettings posts to /user-settings` test, and add `getUserHiddenCrawlers, postUserHiddenCrawlers` to the top import line:

```ts
  it('getUserHiddenCrawlers fetches /user-hidden-crawlers', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ hidden_crawler_ids: [3, 7] }) })
    const result = await getUserHiddenCrawlers()
    expect(fetchMock.mock.calls[0][0]).toContain('/user-hidden-crawlers')
    expect(result).toEqual([3, 7])
  })

  it('postUserHiddenCrawlers posts the full id list to /user-hidden-crawlers', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ ok: true }) })
    await postUserHiddenCrawlers([3, 7])
    expect(fetchMock.mock.calls[0][0]).toContain('/user-hidden-crawlers')
    expect(fetchMock.mock.calls[0][1].method).toBe('POST')
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ hidden_crawler_ids: [3, 7] })
  })
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run src/test/client.test.ts`
Expected: FAIL — `getUserHiddenCrawlers`/`postUserHiddenCrawlers` are not exported from `../api/client`.

- [ ] **Step 3: Add the type and client functions**

In `frontend/src/api/types.ts`, add before the `Crawler` interface:

```ts
export type CrawlerGenre = 'marketplace' | 'punk' | 'metal' | 'rock' | 'pop'
```

Change the `Crawler` interface to add the new required field:

```ts
export interface Crawler {
  id: number
  site_name: string
  module_path: string
  crawler_type: 'release' | 'catalog' | 'catalog_browser'
  enabled: boolean
  last_run: string | null
  base_url: string | null
  genre_summary?: string | null
  genre: CrawlerGenre
}
```

In `frontend/src/api/client.ts`, add after `saveUserSettings`:

```ts
export async function getUserHiddenCrawlers(): Promise<number[]> {
  const r = await apiFetch('/user-hidden-crawlers')
  if (!r.ok) throw new Error(await r.text())
  const data = await r.json()
  return data.hidden_crawler_ids
}

export async function postUserHiddenCrawlers(hiddenCrawlerIds: number[]): Promise<void> {
  const r = await apiFetch('/user-hidden-crawlers', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ hidden_crawler_ids: hiddenCrawlerIds }),
  })
  if (!r.ok) throw new Error(await r.text())
}
```

- [ ] **Step 4: Fix the now-broken `Crawler[]` literals in settings.test.tsx**

`Crawler.genre` is now required, so `frontend/src/test/settings.test.tsx`'s `CRAWLERS`/`CATALOG_CRAWLERS_WITH_DISABLED` arrays (typed `Crawler[]`) will fail to compile. Update each literal by adding a `genre` field:

```ts
const CRAWLERS: Crawler[] = [
  { id: 1, site_name: 'Amazon', module_path: '', crawler_type: 'release', enabled: true, last_run: null, base_url: null, genre_summary: null, genre: 'marketplace' },
  { id: 2, site_name: 'Disabled Site', module_path: '', crawler_type: 'release', enabled: false, last_run: null, base_url: null, genre_summary: null, genre: 'marketplace' },
  { id: 3, site_name: 'Epitaph', module_path: '', crawler_type: 'catalog', enabled: true, last_run: null, base_url: 'https://www.epitaph.com', genre_summary: 'Punk rock label.', genre: 'punk' },
]

const CATALOG_CRAWLERS_WITH_DISABLED: Crawler[] = [
  ...CRAWLERS,
  { id: 4, site_name: 'Disabled Catalog', module_path: '', crawler_type: 'catalog', enabled: false, last_run: null, base_url: null, genre_summary: null, genre: 'marketplace' },
]
```

And the inline literal inside the `'buckets a catalog_browser crawler...'` test:

```ts
      { id: 4, site_name: 'Angry Young and Poor', module_path: '', crawler_type: 'catalog_browser', enabled: true, last_run: null, base_url: null, genre_summary: null, genre: 'punk' },
```

- [ ] **Step 5: Run to verify pass**

Run: `cd frontend && npx vitest run src/test/client.test.ts src/test/settings.test.tsx && npx tsc -b`
Expected: PASS, no type errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/types.ts frontend/src/api/client.ts frontend/src/test/client.test.ts frontend/src/test/settings.test.tsx
git commit -F - <<'EOF'
Add genre field and hidden-crawlers client functions

CrawlerGenre type, Crawler.genre, and GET/POST /user-hidden-crawlers
client wrappers, matching the getUserSettings/saveUserSettings pattern.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
EOF
```

---

## Task 6: `SourceFilter` component

**Files:**
- Create: `frontend/src/components/SourceFilter.tsx`
- Test: `frontend/src/test/sourceFilter.test.tsx`

**Interfaces:**
- Consumes: `Crawler`, `CrawlerGenre` from `../api/types` (Task 5); `navButtonClass` from `../styles/buttons`.
- Produces: `SourceFilter` default export, props `{ crawlers: Crawler[]; hiddenCrawlerIds: number[]; onChange: (hiddenCrawlerIds: number[]) => void }`.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/test/sourceFilter.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import SourceFilter from '../components/SourceFilter'
import type { Crawler } from '../api/types'

const CRAWLERS: Crawler[] = [
  { id: 1, site_name: 'Amazon', module_path: '', crawler_type: 'release', enabled: true, last_run: null, base_url: null, genre: 'marketplace' },
  { id: 2, site_name: 'Epitaph', module_path: '', crawler_type: 'catalog', enabled: true, last_run: null, base_url: null, genre: 'punk' },
  { id: 3, site_name: 'Deathwish Inc', module_path: '', crawler_type: 'catalog', enabled: true, last_run: null, base_url: null, genre: 'punk' },
  { id: 4, site_name: 'Century Media', module_path: '', crawler_type: 'catalog', enabled: true, last_run: null, base_url: null, genre: 'metal' },
]

function renderFilter(overrides: Partial<{ crawlers: Crawler[]; hiddenCrawlerIds: number[]; onChange: (ids: number[]) => void }> = {}) {
  const onChange = overrides.onChange ?? vi.fn()
  render(
    <SourceFilter
      crawlers={overrides.crawlers ?? CRAWLERS}
      hiddenCrawlerIds={overrides.hiddenCrawlerIds ?? []}
      onChange={onChange}
    />
  )
  return onChange
}

function openDropdown() {
  fireEvent.click(screen.getByRole('button', { name: 'Source' }))
}

describe('SourceFilter', () => {
  it('renders a Source button and no dropdown until clicked', () => {
    renderFilter()
    expect(screen.getByRole('button', { name: 'Source' })).toBeInTheDocument()
    expect(screen.queryByText('By genre')).not.toBeInTheDocument()
  })

  it('opens the dropdown showing every genre and every store grouped under its genre', () => {
    renderFilter()
    openDropdown()
    expect(screen.getByText('By genre')).toBeInTheDocument()
    expect(screen.getByText('Marketplace')).toBeInTheDocument()
    expect(screen.getByText('Punk')).toBeInTheDocument()
    expect(screen.getByText('Metal')).toBeInTheDocument()
    expect(screen.getByText('Rock')).toBeInTheDocument()
    expect(screen.getByText('Pop')).toBeInTheDocument()
    expect(screen.getByText('Amazon')).toBeInTheDocument()
    expect(screen.getByText('Epitaph')).toBeInTheDocument()
    expect(screen.getByText('Century Media')).toBeInTheDocument()
  })

  it('checks a store checkbox when it is not in hiddenCrawlerIds', () => {
    renderFilter({ hiddenCrawlerIds: [] })
    openDropdown()
    expect(screen.getByRole('checkbox', { name: 'Epitaph' })).toBeChecked()
  })

  it('unchecks a store checkbox when it is in hiddenCrawlerIds', () => {
    renderFilter({ hiddenCrawlerIds: [2] })
    openDropdown()
    expect(screen.getByRole('checkbox', { name: 'Epitaph' })).not.toBeChecked()
  })

  it('clicking a visible store checkbox calls onChange adding it to the hidden set', () => {
    const onChange = renderFilter({ hiddenCrawlerIds: [] })
    openDropdown()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Epitaph' }))
    expect(onChange).toHaveBeenCalledWith([2])
  })

  it('clicking a hidden store checkbox calls onChange removing it from the hidden set', () => {
    const onChange = renderFilter({ hiddenCrawlerIds: [2, 3] })
    openDropdown()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Epitaph' }))
    expect(onChange).toHaveBeenCalledWith([3])
  })

  it('checks the genre checkbox when every store in that genre is visible', () => {
    renderFilter({ hiddenCrawlerIds: [] })
    openDropdown()
    expect(screen.getByRole('checkbox', { name: 'Punk' })).toBeChecked()
  })

  it('unchecks the genre checkbox when every store in that genre is hidden', () => {
    renderFilter({ hiddenCrawlerIds: [2, 3] })
    openDropdown()
    expect(screen.getByRole('checkbox', { name: 'Punk' })).not.toBeChecked()
  })

  it('marks the genre checkbox indeterminate when some but not all stores in that genre are hidden', () => {
    renderFilter({ hiddenCrawlerIds: [2] })
    openDropdown()
    const checkbox = screen.getByRole('checkbox', { name: 'Punk' }) as HTMLInputElement
    expect(checkbox.indeterminate).toBe(true)
  })

  it('clicking a fully-visible genre checkbox hides every store in that genre', () => {
    const onChange = renderFilter({ hiddenCrawlerIds: [] })
    openDropdown()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Punk' }))
    expect(onChange).toHaveBeenCalledWith(expect.arrayContaining([2, 3]))
    expect((onChange.mock.calls[0][0] as number[]).length).toBe(2)
  })

  it('clicking a mixed genre checkbox shows every store in that genre', () => {
    const onChange = renderFilter({ hiddenCrawlerIds: [2, 4] })
    openDropdown()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Punk' }))
    expect(onChange).toHaveBeenCalledWith([4])
  })

  it('clicking Show all calls onChange with an empty array', () => {
    const onChange = renderFilter({ hiddenCrawlerIds: [2, 3, 4] })
    openDropdown()
    fireEvent.click(screen.getByText('Show all'))
    expect(onChange).toHaveBeenCalledWith([])
  })

  it('closes the dropdown when clicking outside it', () => {
    render(
      <div>
        <div data-testid="outside">outside</div>
        <SourceFilter crawlers={CRAWLERS} hiddenCrawlerIds={[]} onChange={vi.fn()} />
      </div>
    )
    openDropdown()
    expect(screen.getByText('By genre')).toBeInTheDocument()
    fireEvent.mouseDown(screen.getByTestId('outside'))
    expect(screen.queryByText('By genre')).not.toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run src/test/sourceFilter.test.tsx`
Expected: FAIL — `../components/SourceFilter` does not exist.

- [ ] **Step 3: Implement**

Create `frontend/src/components/SourceFilter.tsx`:

```tsx
import { useEffect, useRef, useState } from 'react'
import type { Crawler, CrawlerGenre } from '../api/types'
import { navButtonClass } from '../styles/buttons'

interface Props {
  crawlers: Crawler[]
  hiddenCrawlerIds: number[]
  onChange: (hiddenCrawlerIds: number[]) => void
}

const GENRES: { key: CrawlerGenre; label: string }[] = [
  { key: 'marketplace', label: 'Marketplace' },
  { key: 'punk', label: 'Punk' },
  { key: 'metal', label: 'Metal' },
  { key: 'rock', label: 'Rock' },
  { key: 'pop', label: 'Pop' },
]

function FilterCheckbox({ label, checked, indeterminate, onToggle }: {
  label: string
  checked: boolean
  indeterminate: boolean
  onToggle: () => void
}) {
  return (
    <label className="flex items-center gap-2 py-1 cursor-pointer text-gray-200 hover:text-white">
      <input
        type="checkbox"
        aria-label={label}
        checked={checked}
        ref={(el) => { if (el) el.indeterminate = indeterminate }}
        onChange={onToggle}
        className="accent-white"
      />
      {label}
    </label>
  )
}

function SourceFilter({ crawlers, hiddenCrawlerIds, onChange }: Props) {
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    function onMouseDown(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onMouseDown)
    return () => document.removeEventListener('mousedown', onMouseDown)
  }, [open])

  const byGenre = new Map<CrawlerGenre, Crawler[]>()
  for (const c of crawlers) {
    const list = byGenre.get(c.genre) ?? []
    list.push(c)
    byGenre.set(c.genre, list)
  }

  function genreState(genre: CrawlerGenre): 'all' | 'none' | 'mixed' {
    const list = byGenre.get(genre) ?? []
    if (list.length === 0) return 'all'
    const hiddenCount = list.filter((c) => hiddenCrawlerIds.includes(c.id)).length
    if (hiddenCount === 0) return 'all'
    if (hiddenCount === list.length) return 'none'
    return 'mixed'
  }

  function toggleGenre(genre: CrawlerGenre) {
    const ids = (byGenre.get(genre) ?? []).map((c) => c.id)
    if (genreState(genre) === 'all') {
      onChange([...new Set([...hiddenCrawlerIds, ...ids])])
    } else {
      onChange(hiddenCrawlerIds.filter((id) => !ids.includes(id)))
    }
  }

  function toggleStore(crawlerId: number) {
    onChange(
      hiddenCrawlerIds.includes(crawlerId)
        ? hiddenCrawlerIds.filter((id) => id !== crawlerId)
        : [...hiddenCrawlerIds, crawlerId]
    )
  }

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={`px-3 py-1.5 text-sm font-medium ${navButtonClass(open || hiddenCrawlerIds.length > 0)}`}
      >
        Source
      </button>
      {open && (
        <div className="absolute right-0 mt-2 w-72 max-h-[28rem] overflow-y-auto rounded-xl border border-gray-700 bg-gray-900 shadow-xl z-50 p-3 text-sm">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs uppercase tracking-wider text-gray-500">By genre</span>
            <button type="button" onClick={() => onChange([])} className="text-xs text-gray-400 hover:text-white">
              Show all
            </button>
          </div>
          {GENRES.map(({ key, label }) => (
            <FilterCheckbox
              key={key}
              label={label}
              checked={genreState(key) === 'all'}
              indeterminate={genreState(key) === 'mixed'}
              onToggle={() => toggleGenre(key)}
            />
          ))}
          <div className="border-t border-gray-800 my-3" />
          <span className="text-xs uppercase tracking-wider text-gray-500">By store</span>
          {GENRES.map(({ key, label }) => {
            const stores = byGenre.get(key) ?? []
            if (stores.length === 0) return null
            return (
              <div key={key} className="mt-2">
                <div className="text-xs text-gray-500 mb-1">{label}</div>
                {stores.map((c) => (
                  <FilterCheckbox
                    key={c.id}
                    label={c.site_name}
                    checked={!hiddenCrawlerIds.includes(c.id)}
                    indeterminate={false}
                    onToggle={() => toggleStore(c.id)}
                  />
                ))}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

export default SourceFilter
```

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && npx vitest run src/test/sourceFilter.test.tsx && npx tsc -b`
Expected: PASS, no type errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/SourceFilter.tsx frontend/src/test/sourceFilter.test.tsx
git commit -F - <<'EOF'
Add SourceFilter dropdown component

By-genre (tri-state bulk toggle) and by-store sections over a
crawlers list and a hidden-id set. No wiring yet.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
EOF
```

---

## Task 7: Wire `SourceFilter` into `StockBrowser`

**Files:**
- Modify: `frontend/src/views/StockBrowser.tsx`
- Test: `frontend/src/test/stockBrowser.test.tsx`

**Interfaces:**
- Consumes: `SourceFilter` (Task 6).
- Produces: `StockBrowser` gains optional props `crawlers?: Crawler[]` (default `[]`) and `onHiddenCrawlerIdsChange?: (ids: number[]) => void` (default no-op).

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/test/stockBrowser.test.tsx` (see existing file for the `getStock`/`getStockArtists` mock setup already in place — reuse it):

```tsx
import type { Crawler } from '../api/types'

const CRAWLERS: Crawler[] = [
  { id: 5, site_name: 'Epitaph', module_path: '', crawler_type: 'catalog', enabled: true, last_run: null, base_url: null, genre: 'punk' },
]

describe('StockBrowser Source filter', () => {
  it('renders the Source button in the header', async () => {
    render(<StockBrowser crawlers={CRAWLERS} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Source' })).toBeInTheDocument())
  })

  it('calls onHiddenCrawlerIdsChange when a store checkbox is toggled', async () => {
    const onHiddenCrawlerIdsChange = vi.fn()
    render(<StockBrowser crawlers={CRAWLERS} onHiddenCrawlerIdsChange={onHiddenCrawlerIdsChange} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Source' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Epitaph' }))
    expect(onHiddenCrawlerIdsChange).toHaveBeenCalledWith([5])
  })
})
```

(Add `import type { Crawler } from '../api/types'` to the file's existing import block, and confirm `fireEvent`/`screen`/`waitFor` are already imported — they are, per the file's existing tests.)

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run src/test/stockBrowser.test.tsx`
Expected: FAIL — no "Source" button rendered (StockBrowser doesn't accept `crawlers`/`onHiddenCrawlerIdsChange` yet).

- [ ] **Step 3: Implement**

In `frontend/src/views/StockBrowser.tsx`:

Add the import:

```ts
import SourceFilter from '../components/SourceFilter'
import type { StockItem, StockSortField, SortOrder, StockScope, LibraryScope, Crawler } from '../api/types'
```

Add to `Props` and defaults:

```ts
interface Props {
  scope?: StockScope
  recommendedAvailable?: boolean
  hiddenCrawlerIds?: number[]
  crawlers?: Crawler[]
  onHiddenCrawlerIdsChange?: (hiddenCrawlerIds: number[]) => void
  syncGeneration?: number
  isAdmin?: boolean
}

const NO_HIDDEN_CRAWLER_IDS: number[] = []
const NO_CRAWLERS: Crawler[] = []
const NOOP_HIDDEN_CRAWLER_IDS_CHANGE = () => {}
```

Update the function signature:

```ts
function StockBrowser({
  scope = 'store', recommendedAvailable = false, hiddenCrawlerIds = NO_HIDDEN_CRAWLER_IDS,
  crawlers = NO_CRAWLERS, onHiddenCrawlerIdsChange = NOOP_HIDDEN_CRAWLER_IDS_CHANGE,
  syncGeneration, isAdmin = false,
}: Props) {
```

In the header's `ml-auto flex items-center gap-2` div, add `<SourceFilter>` before the `<select>`:

```tsx
          <div className="ml-auto flex items-center gap-2">
            <SourceFilter crawlers={crawlers} hiddenCrawlerIds={hiddenCrawlerIds} onChange={onHiddenCrawlerIdsChange} />
            <select
```

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && npx vitest run src/test/stockBrowser.test.tsx && npx tsc -b`
Expected: PASS, no type errors, no regressions in the rest of the file's existing tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/StockBrowser.tsx frontend/src/test/stockBrowser.test.tsx
git commit -F - <<'EOF'
Wire SourceFilter into StockBrowser's header

Optional crawlers/onHiddenCrawlerIdsChange props, both defaulted so
existing callers are unaffected. Not yet wired from App.tsx.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
EOF
```

---

## Task 8: Remove the View column from `Settings.tsx`

**Files:**
- Modify: `frontend/src/views/Settings.tsx`
- Test: `frontend/src/test/settings.test.tsx`

**Interfaces:**
- Removes: `Settings` props `hiddenCrawlerIds`, `onToggleCrawlerView`.

- [ ] **Step 1: Delete the obsolete tests and default props**

In `frontend/src/test/settings.test.tsx`:

Remove `hiddenCrawlerIds={[]}` and `onToggleCrawlerView={() => {}}` from `renderSettings`'s default props.

Delete these three tests entirely: `'marks a crawler in hiddenCrawlerIds as Hidden in the View column'`, `'calls onToggleCrawlerView when a View button is clicked'`, `'still shows View toggles to a non-admin'`.

Change the `'shows both View and Crawl columns to an admin...'` test to:

```tsx
  it('shows the Crawl column to an admin, for every crawler regardless of enabled state', async () => {
    renderSettings({ crawlers: CRAWLERS })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    expect(screen.getByText('Amazon')).toBeInTheDocument()
    expect(screen.getByText('Disabled Site')).toBeInTheDocument()
    expect(screen.queryByText('Visible')).not.toBeInTheDocument()
    expect(screen.getAllByText('Enabled').length).toBe(2)
    expect(screen.getAllByText('Disabled').length).toBe(1)
  })
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run src/test/settings.test.tsx`
Expected: FAIL — `renderSettings` still passes props `Settings` will soon not accept, and the renamed test still finds "Visible" text (View column still renders).

Note: TypeScript will actually still compile at this point since `Props` hasn't changed yet — the test failure here is `screen.queryByText('Visible')` still finding a match, not a compile error. That's fine, this step just proves the test is exercising the removed behavior correctly before Step 3 removes it.

- [ ] **Step 3: Remove the View column and props from Settings.tsx**

In `frontend/src/views/Settings.tsx`, remove `hiddenCrawlerIds: number[]` and `onToggleCrawlerView: (crawlerId: number) => void` from the `Props` interface, and remove `hiddenCrawlerIds, onToggleCrawlerView,` from the function's destructured parameters.

Remove the View `<th>` (currently `<th className="text-left py-2 pr-4">View</th>`) and its corresponding `<td>` block:

```tsx
              <td className="py-3 pr-4 text-left">
                <button
                  onClick={() => onToggleCrawlerView(c.id)}
                  className={toggleButtonClass(!hiddenCrawlerIds.includes(c.id))}
                >
                  {hiddenCrawlerIds.includes(c.id) ? 'Hidden' : 'Visible'}
                </button>
              </td>
```

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && npx vitest run src/test/settings.test.tsx && npx tsc -b`
Expected: PASS, no type errors (this will also surface any other caller of `<Settings>` still passing the removed props — none exist yet since App.tsx isn't touched until Task 9, so `tsc -b` will report App.tsx's `Settings` usage as a type error at this point; that's expected and gets fixed in Task 9. Confirm the error is *only* in `App.tsx` and not `Settings.tsx`/`settings.test.tsx`.)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/Settings.tsx frontend/src/test/settings.test.tsx
git commit -F - <<'EOF'
Remove the crawler View column from Settings

Visibility now lives in the Store/Track Source filter, not Settings.
App.tsx still passes the removed props -- fixed in the next commit.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
EOF
```

---

## Task 9: Wire everything into `App.tsx`, remove Settings nav for non-admins

**Files:**
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/test/accountNav.test.tsx`, `frontend/src/test/crawlStatusBar.test.tsx`, `frontend/src/test/inStockTab.test.tsx`, `frontend/src/test/staleSignupLink.test.tsx`, `frontend/src/test/wantlistRefresh.test.tsx`, `frontend/src/test/viewRenderChurn.test.tsx`

**Interfaces:**
- Consumes: `getUserHiddenCrawlers`, `postUserHiddenCrawlers` (Task 5), `StockBrowser`'s new props (Task 7), `Settings`'s reduced props (Task 8).

- [ ] **Step 1: Update the App-mounting test files' client mocks**

In each of `frontend/src/test/crawlStatusBar.test.tsx`, `frontend/src/test/inStockTab.test.tsx`, `frontend/src/test/staleSignupLink.test.tsx`, `frontend/src/test/wantlistRefresh.test.tsx`, `frontend/src/test/viewRenderChurn.test.tsx`, find the `vi.mock('../api/client', () => ({` block and add these two lines immediately after `setUnauthorizedHandler: vi.fn(),`:

```ts
  getUserHiddenCrawlers: vi.fn().mockResolvedValue([]),
  postUserHiddenCrawlers: vi.fn().mockResolvedValue(undefined),
```

(`accountNav.test.tsx` is handled separately in Step 2 below, since its tests need to control these two mocks per-test rather than use one fixed default.)

- [ ] **Step 2: Replace accountNav.test.tsx's obsolete `hiddenCrawlerIds persistence` describe block**

In `frontend/src/test/accountNav.test.tsx`, add `getUserHiddenCrawlers`/`postUserHiddenCrawlers` to the file's existing `vi.hoisted` block alongside `getAuthStatus`/`getCrawlers`, so they can be controlled per-test:

```ts
const { getAuthStatus, getCrawlers, getUserHiddenCrawlers, postUserHiddenCrawlers } = vi.hoisted(() => ({
  getAuthStatus: vi.fn().mockResolvedValue({ state: 'authenticated', user: { discogs_username: 'test', is_admin: true } }),
  getCrawlers: vi.fn().mockResolvedValue([]),
  getUserHiddenCrawlers: vi.fn().mockResolvedValue([]),
  postUserHiddenCrawlers: vi.fn().mockResolvedValue(undefined),
}))
```

And reference those hoisted mocks (not the inline `vi.fn()`s from Step 1) in the `vi.mock('../api/client', ...)` factory:

```ts
  getUserHiddenCrawlers,
  postUserHiddenCrawlers,
```

Replace the entire `describe('hiddenCrawlerIds persistence', ...)` block with:

```tsx
describe('source filter persistence', () => {
  const crawler = {
    id: 7, site_name: 'TestStore', module_path: '', crawler_type: 'release' as const,
    enabled: true, last_run: null, base_url: null, genre: 'marketplace' as const,
  }

  it('fetches the hidden set on mount and toggling a store in the Source dropdown posts the updated set', async () => {
    getCrawlers.mockResolvedValueOnce([crawler])
    render(<App />)
    await waitFor(() => expect(getUserHiddenCrawlers).toHaveBeenCalled())

    fireEvent.click(await screen.findByText('Store'))
    const sourceButtons = await screen.findAllByRole('button', { name: 'Source' })
    fireEvent.click(sourceButtons[0])
    fireEvent.click(await screen.findByRole('checkbox', { name: 'TestStore' }))

    await waitFor(() => expect(postUserHiddenCrawlers).toHaveBeenCalledWith([7]))
  })

  it('reflects a server-persisted hidden crawler as unchecked after mount', async () => {
    getCrawlers.mockResolvedValueOnce([crawler])
    getUserHiddenCrawlers.mockResolvedValueOnce([7])
    render(<App />)

    fireEvent.click(await screen.findByText('Store'))
    const sourceButtons = await screen.findAllByRole('button', { name: 'Source' })
    fireEvent.click(sourceButtons[0])
    expect(await screen.findByRole('checkbox', { name: 'TestStore' })).not.toBeChecked()
  })
})
```

- [ ] **Step 3: Add a Settings-nav-visibility test**

In `frontend/src/test/accountNav.test.tsx`, add to the existing `describe('header profile navigation', ...)` block, alongside the existing `'does not show the role switch to a non-admin'` test:

```tsx
  it('does not show the Settings nav button to a non-admin', async () => {
    getAuthStatus.mockResolvedValueOnce({ state: 'authenticated', user: { discogs_username: 'test', is_admin: false } })
    render(<App />)
    await screen.findByRole('button', { name: /profile/i })
    expect(screen.queryByRole('button', { name: 'Settings' })).not.toBeInTheDocument()
  })
```

- [ ] **Step 4: Run to verify failure**

Run: `cd frontend && npx vitest run src/test/accountNav.test.tsx`
Expected: FAIL — Store's `StockBrowser` isn't yet given `crawlers`/`onHiddenCrawlerIdsChange`, so no `SourceFilter` checkbox for "TestStore" exists; `getUserHiddenCrawlers` isn't called yet; the Settings button still shows for a non-admin.

- [ ] **Step 5: Implement the App.tsx wiring**

Add to the import line at the top:

```ts
import { refreshCollection, getCollectionStatus, openCrawlStream, getCrawlStatus, postCrawlStart, postStockSyncStart, postJudgmentStart, clearJudgments, exportRecommendationsCsv, importRecommendationsCsv, getCrawlers, getUserSettings, getUserHiddenCrawlers, postUserHiddenCrawlers, getJudgmentStatus, checkHealth, getAuthStatus, setUnauthorizedHandler, hasAvatar } from './api/client'
```

Remove the `HIDDEN_CRAWLER_IDS_KEY` constant (currently `const HIDDEN_CRAWLER_IDS_KEY = 'discogs-browser.hiddenCrawlerIds'`).

Replace the `hiddenCrawlerIds` state initializer:

```ts
  const [hiddenCrawlerIds, setHiddenCrawlerIds] = useState<number[]>([])
```

Replace `toggleCrawlerView` with:

```ts
  const updateHiddenCrawlerIds = useCallback((ids: number[]) => {
    setHiddenCrawlerIds(ids)
    postUserHiddenCrawlers(ids).catch(() => {})
  }, [])
```

Remove the `localStorage.setItem(HIDDEN_CRAWLER_IDS_KEY, ...)` effect entirely (the one immediately following, currently reading `useEffect(() => { localStorage.setItem(...) }, [hiddenCrawlerIds])`).

In the `checkHealth` poll success block, currently:

```ts
            setServerReady(true)
            getCrawlers().then(setCrawlers).catch(() => {})
            getUserSettings().then((s) => {
              setHasAnthropicKey(Boolean(s.anthropic_api_key))
            }).catch(() => {})
```

add a fourth call alongside the other two:

```ts
            setServerReady(true)
            getCrawlers().then(setCrawlers).catch(() => {})
            getUserHiddenCrawlers().then(setHiddenCrawlerIds).catch(() => {})
            getUserSettings().then((s) => {
              setHasAnthropicKey(Boolean(s.anthropic_api_key))
            }).catch(() => {})
```

Wrap the Settings nav button:

```tsx
          {showAdminNav && (
            <button
              onClick={() => setView('settings')}
              className={`px-3 py-1.5 text-sm font-medium ${navButtonClass(view === 'settings')}`}
            >
              Settings
            </button>
          )}
```

Pass `crawlers`/`onHiddenCrawlerIdsChange` to both `StockBrowser` instances:

```tsx
          <StockBrowser recommendedAvailable={recommendedAvailable} hiddenCrawlerIds={hiddenCrawlerIds} crawlers={crawlers} onHiddenCrawlerIdsChange={updateHiddenCrawlerIds} syncGeneration={stockSyncGeneration} isAdmin={showAdminNav} />
```

```tsx
          <StockBrowser scope="track" hiddenCrawlerIds={hiddenCrawlerIds} crawlers={crawlers} onHiddenCrawlerIdsChange={updateHiddenCrawlerIds} syncGeneration={stockSyncGeneration} isAdmin={showAdminNav} />
```

Remove `hiddenCrawlerIds={hiddenCrawlerIds}` and `onToggleCrawlerView={toggleCrawlerView}` from the `<Settings>` render.

- [ ] **Step 6: Run to verify pass**

Run: `cd frontend && npx vitest run && npx tsc -b`
Expected: PASS — full frontend suite, no type errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/App.tsx frontend/src/test/accountNav.test.tsx frontend/src/test/crawlStatusBar.test.tsx frontend/src/test/inStockTab.test.tsx frontend/src/test/staleSignupLink.test.tsx frontend/src/test/wantlistRefresh.test.tsx frontend/src/test/viewRenderChurn.test.tsx
git commit -F - <<'EOF'
Wire Source filter persistence into App.tsx, hide Settings nav for non-admins

hiddenCrawlerIds now comes from GET/POST /api/user-hidden-crawlers
instead of localStorage. Settings nav button is admin-only -- for a
non-admin the page has nothing left in it since Task 8.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
EOF
```

---

## Final verification (after all tasks)

- [ ] Run the full backend suite: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest`
- [ ] Run the full frontend suite and typecheck: `cd frontend && npx vitest run && npx tsc -b`
- [ ] Manually verify in the browser (see repo `CLAUDE.md`'s "UI or frontend changes" guidance): start both servers, log in as a non-admin, confirm no Settings nav button; open Store, click Source, toggle a genre and an individual store, confirm the list refilters and the choice survives a page reload; log in as admin, confirm Settings still renders (Crawl column only, no View column) and the Source filter also works from Store/Track.
- [ ] Follow the repo's "Pre-PR spec-drift check" (`CLAUDE.md`) before opening a PR: grep both spec trees for anything this branch touched (`hiddenCrawlerIds`, `View` column, `Settings` nav) and confirm `2026-08-02-store-view-filter-design.md` and `2026-08-12-store-genre-summaries-design.md` don't need a drift correction — they document the mechanism this branch replaces/extends, so at minimum a short note pointing forward to the new spec is likely warranted.
