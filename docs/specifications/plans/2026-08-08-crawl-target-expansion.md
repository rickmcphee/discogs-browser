# Crawl-Target Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let release crawlers (amazon, ebay — not discogs_marketplace) search on behalf of a store-crawler stock item, not just a Discogs release, so stock items accumulate the same cross-site `listings` data releases already do.

**Architecture:** A new `stock_item_identities` table (keyed by `item_key`) is the stable dimension `crawl_queue`/`listings` need — `stock_items` itself is wiped and reinserted every stock sync, so it can't be an FK target. `crawl_queue`/`listings` gain a nullable `item_key` column alongside the now-nullable `discogs_id`/`release_id`; exactly one is set per row, enforced by which function you call (`enqueue_crawl_queue` vs. `enqueue_crawl_queue_for_stock_item`, `upsert_listing` vs. `upsert_stock_item_listing`) rather than a DB constraint. `_sync_stock` enqueues a price crawl for every item it ingests, mirroring `_sync_collection`'s existing per-release enqueue. A new `crawlers.requires_discogs_release` flag excludes discogs_marketplace (which can only search a specific Discogs release, not a generic item) from that enqueue. `_drain_one_batch`, the crawl worker, branches on which key a claimed row carries to resolve its target and write back its result.

**Tech Stack:** FastAPI + psycopg3 + Postgres (backend), pytest + pytest-asyncio + respx (backend tests). No frontend changes in this slice.

**Spec:** `docs/specifications/shaping/2026-08-08-crawl-target-expansion-design.md`.

## Global Constraints

- Every commit carries the full AI-attribution trailer block required by this repo's `CLAUDE.md` (`Note: This commit message was created by AI` / `ai-generated: true` / `ai-model: claude-sonnet-5` / `ai-tool: claude-code` / `ai-surface: cli` / `ai-executor: remote-agent`), created via a message file, not `git commit -m`.
- No comments unless the WHY is non-obvious; no backwards-compat shims — just change the code (repo style rule).
- Python ≥3.9: no `str | None` syntax — use `Optional[str]`.
- `backend/version.py`'s `VERSION` gets exactly one minor bump for this whole PR, done in the final task — not per-commit.
- No new migration tooling: every schema change is an idempotent `CREATE TABLE/INDEX IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`, or `ALTER COLUMN ... DROP NOT NULL` — all safe to re-run, matching `GLOBAL_SCHEMA`/`TENANT_SCHEMA` already being re-run on every startup.

---

### Task 1: Schema — `stock_item_identities`, nullable `item_key` columns, `requires_discogs_release`

**Files:**
- Modify: `backend/db.py` (`GLOBAL_SCHEMA` string, `init_tenant_schema`'s grants)
- Test: `backend/tests/test_global_schema.py`

**Interfaces:**
- Produces: table `stock_item_identities(item_key PK, artist, title, format, last_seen)`; `crawl_queue.discogs_id` and `listings.release_id` are now nullable; both tables gain a nullable `item_key TEXT REFERENCES stock_item_identities(item_key)` column and a `UNIQUE(item_key, crawler_id)` index; `crawlers` gains `requires_discogs_release BOOLEAN NOT NULL DEFAULT FALSE`.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_global_schema.py`, first change the fixture's teardown TRUNCATE (line 12) from:

```python
        conn.execute("TRUNCATE catalog, listings, crawlers, stock_items, crawl_queue CASCADE")
```

to:

```python
        conn.execute("TRUNCATE catalog, listings, crawlers, stock_items, crawl_queue, stock_item_identities CASCADE")
```

Then add these tests after `test_crawl_queue_table_exists_with_unique_constraint` (the last test in the file):

```python
def test_crawlers_requires_discogs_release_defaults_to_false(admin_conn):
    admin_conn.execute(
        "INSERT INTO crawlers (site_name, module_path) VALUES ('Test Site', 'crawlers.test')"
    )
    row = admin_conn.execute(
        "SELECT requires_discogs_release FROM crawlers WHERE site_name = 'Test Site'"
    ).fetchone()
    assert row["requires_discogs_release"] is False


def test_crawlers_requires_discogs_release_can_be_set_true(admin_conn):
    admin_conn.execute(
        "INSERT INTO crawlers (site_name, module_path, requires_discogs_release) VALUES ('Test Site', 'crawlers.test', TRUE)"
    )
    row = admin_conn.execute(
        "SELECT requires_discogs_release FROM crawlers WHERE site_name = 'Test Site'"
    ).fetchone()
    assert row["requires_discogs_release"] is True


def test_stock_item_identities_table_exists_with_expected_columns(admin_conn):
    cols = {
        r["column_name"]
        for r in admin_conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'stock_item_identities'"
        ).fetchall()
    }
    assert cols == {"item_key", "artist", "title", "format", "last_seen"}


def test_listings_accepts_item_key_based_row_with_null_release_id(admin_conn):
    admin_conn.execute(
        "INSERT INTO stock_item_identities (item_key, artist, title) VALUES ('key1', 'A', 'T')"
    )
    admin_conn.execute(
        "INSERT INTO crawlers (site_name, module_path) VALUES ('Test Site', 'crawlers.test')"
    )
    crawler_id = admin_conn.execute(
        "SELECT id FROM crawlers WHERE site_name = 'Test Site'"
    ).fetchone()["id"]
    admin_conn.execute(
        "INSERT INTO listings (item_key, crawler_id, url) VALUES ('key1', %s, 'http://x')",
        [crawler_id],
    )
    row = admin_conn.execute(
        "SELECT release_id, item_key FROM listings WHERE item_key = 'key1'"
    ).fetchone()
    assert row["release_id"] is None
    assert row["item_key"] == "key1"


def test_listings_unique_on_item_key_and_crawler(admin_conn):
    admin_conn.execute(
        "INSERT INTO stock_item_identities (item_key, artist, title) VALUES ('key1', 'A', 'T')"
    )
    admin_conn.execute(
        "INSERT INTO crawlers (site_name, module_path) VALUES ('Test Site', 'crawlers.test')"
    )
    crawler_id = admin_conn.execute(
        "SELECT id FROM crawlers WHERE site_name = 'Test Site'"
    ).fetchone()["id"]
    admin_conn.execute(
        "INSERT INTO listings (item_key, crawler_id, url) VALUES ('key1', %s, 'http://x')",
        [crawler_id],
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        admin_conn.execute(
            "INSERT INTO listings (item_key, crawler_id, url) VALUES ('key1', %s, 'http://y')",
            [crawler_id],
        )
    admin_conn.rollback()


def test_listings_rejects_item_key_not_in_stock_item_identities(admin_conn):
    admin_conn.execute(
        "INSERT INTO crawlers (site_name, module_path) VALUES ('Test Site', 'crawlers.test')"
    )
    crawler_id = admin_conn.execute(
        "SELECT id FROM crawlers WHERE site_name = 'Test Site'"
    ).fetchone()["id"]
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        admin_conn.execute(
            "INSERT INTO listings (item_key, crawler_id, url) VALUES ('missing', %s, 'http://x')",
            [crawler_id],
        )
    admin_conn.rollback()


def test_crawl_queue_accepts_item_key_based_row_with_null_discogs_id(admin_conn):
    admin_conn.execute(
        "INSERT INTO stock_item_identities (item_key, artist, title) VALUES ('key1', 'A', 'T')"
    )
    crawler_id = admin_conn.execute(
        "INSERT INTO crawlers (site_name, module_path) VALUES ('Test Site', '/x.py') RETURNING id"
    ).fetchone()["id"]
    admin_conn.execute(
        "INSERT INTO crawl_queue (item_key, crawler_id) VALUES ('key1', %s)", [crawler_id]
    )
    admin_conn.commit()
    row = admin_conn.execute(
        "SELECT discogs_id, item_key FROM crawl_queue WHERE item_key = 'key1'"
    ).fetchone()
    assert row["discogs_id"] is None
    assert row["item_key"] == "key1"


def test_crawl_queue_unique_on_item_key_and_crawler(admin_conn):
    admin_conn.execute(
        "INSERT INTO stock_item_identities (item_key, artist, title) VALUES ('key1', 'A', 'T')"
    )
    crawler_id = admin_conn.execute(
        "INSERT INTO crawlers (site_name, module_path) VALUES ('Test Site', '/x.py') RETURNING id"
    ).fetchone()["id"]
    admin_conn.execute(
        "INSERT INTO crawl_queue (item_key, crawler_id) VALUES ('key1', %s)", [crawler_id]
    )
    admin_conn.commit()
    with pytest.raises(psycopg.errors.UniqueViolation):
        admin_conn.execute(
            "INSERT INTO crawl_queue (item_key, crawler_id) VALUES ('key1', %s)", [crawler_id]
        )
    admin_conn.rollback()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_global_schema.py -v`
Expected: FAIL — `requires_discogs_release`/`stock_item_identities`/`item_key` don't exist yet (`UndefinedColumn`/`UndefinedTable` errors).

- [ ] **Step 3: Implement the schema changes**

In `backend/db.py`, `GLOBAL_SCHEMA`, insert immediately after the `crawlers` table's closing `);` (right before the blank line and `CREATE TABLE IF NOT EXISTS listings`):

```sql
ALTER TABLE crawlers ADD COLUMN IF NOT EXISTS requires_discogs_release BOOLEAN NOT NULL DEFAULT FALSE;
```

Then, at the very end of the `GLOBAL_SCHEMA` string (after the existing `CREATE INDEX IF NOT EXISTS crawl_queue_pending_idx ...` statement, before the closing `"""`), add:

```sql

CREATE TABLE IF NOT EXISTS stock_item_identities (
    item_key TEXT PRIMARY KEY,
    artist TEXT NOT NULL,
    title TEXT NOT NULL,
    format TEXT,
    last_seen TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE crawl_queue ALTER COLUMN discogs_id DROP NOT NULL;
ALTER TABLE crawl_queue ADD COLUMN IF NOT EXISTS item_key TEXT REFERENCES stock_item_identities(item_key);
CREATE UNIQUE INDEX IF NOT EXISTS crawl_queue_item_key_crawler_idx ON crawl_queue (item_key, crawler_id);

ALTER TABLE listings ALTER COLUMN release_id DROP NOT NULL;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS item_key TEXT REFERENCES stock_item_identities(item_key);
CREATE UNIQUE INDEX IF NOT EXISTS listings_item_key_crawler_idx ON listings (item_key, crawler_id);
```

In `init_tenant_schema`, change:

```python
        conn.execute("GRANT SELECT, INSERT, UPDATE ON catalog, listings TO app_user")
```

to:

```python
        conn.execute("GRANT SELECT, INSERT, UPDATE ON catalog, listings, stock_item_identities TO app_user")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_global_schema.py -v`
Expected: PASS — every test in the file, including the pre-existing ones (all schema changes here are additive).

- [ ] **Step 5: Commit**

```bash
git add backend/db.py backend/tests/test_global_schema.py
```

Commit message body:

```
feat: add stock_item_identities table and item_key crawl-target columns

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: cli
ai-executor: remote-agent
```

---

### Task 2: `register_crawler` gains `requires_discogs_release`; discogs_marketplace opts in

**Files:**
- Modify: `backend/db.py` (`register_crawler`)
- Modify: `backend/main.py` (`_crawler_metadata`, `seed_bundled_crawlers`)
- Modify: `backend/crawlers/discogs_marketplace.py`
- Test: `backend/tests/test_crawler_crud.py`

**Interfaces:**
- Consumes: `crawlers.requires_discogs_release` column from Task 1.
- Produces: `db.register_crawler(conn, site_name, module_path, crawler_type="release", requires_discogs_release=False)` — one new optional kwarg.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_crawler_crud.py`, directly after `test_register_crawler_preserves_enabled_flag`:

```python
def test_register_crawler_sets_and_preserves_requires_discogs_release(admin_conn):
    db.register_crawler(admin_conn, "Discogs", "/x.py", requires_discogs_release=True)
    admin_conn.commit()
    row = admin_conn.execute(
        "SELECT requires_discogs_release FROM crawlers WHERE site_name = 'Discogs'"
    ).fetchone()
    assert row["requires_discogs_release"] is True

    # main.py's seed_bundled_crawlers() calls register_crawler unconditionally
    # on every startup, passing the plugin's current requires_discogs_release
    # value each time -- re-registering with the same value must leave it set.
    db.register_crawler(admin_conn, "Discogs", "/x.py", requires_discogs_release=True)
    admin_conn.commit()
    row = admin_conn.execute(
        "SELECT requires_discogs_release FROM crawlers WHERE site_name = 'Discogs'"
    ).fetchone()
    assert row["requires_discogs_release"] is True


def test_register_crawler_defaults_requires_discogs_release_to_false(admin_conn):
    db.register_crawler(admin_conn, "Amazon", "/x.py")
    admin_conn.commit()
    row = admin_conn.execute(
        "SELECT requires_discogs_release FROM crawlers WHERE site_name = 'Amazon'"
    ).fetchone()
    assert row["requires_discogs_release"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_crawler_crud.py -k requires_discogs_release -v`
Expected: FAIL — `register_crawler() got an unexpected keyword argument 'requires_discogs_release'`

- [ ] **Step 3: Implement**

In `backend/db.py`, replace `register_crawler` in full:

```python
def register_crawler(
    conn, site_name: str, module_path: str, crawler_type: str = "release", requires_discogs_release: bool = False,
):
    conn.execute(
        """
        INSERT INTO crawlers (site_name, module_path, crawler_type, requires_discogs_release, enabled)
        VALUES (%s, %s, %s, %s, TRUE)
        ON CONFLICT (site_name) DO UPDATE SET
            module_path = EXCLUDED.module_path, crawler_type = EXCLUDED.crawler_type,
            requires_discogs_release = EXCLUDED.requires_discogs_release
        """,
        [site_name, module_path, crawler_type, requires_discogs_release],
    )
```

In `backend/crawlers/discogs_marketplace.py`, change:

```python
class Crawler:
    site_name: str = "Discogs"
    base_url: str = "https://www.discogs.com"
```

to:

```python
class Crawler:
    site_name: str = "Discogs"
    base_url: str = "https://www.discogs.com"
    requires_discogs_release: bool = True
```

In `backend/main.py`, change `_crawler_metadata`:

```python
def _crawler_metadata(path: Path, fallback_site_name: str) -> tuple[str, str]:
    crawler = load_crawler_from_path(path)
    site_name = getattr(crawler, "site_name", fallback_site_name)
    crawler_type = getattr(crawler, "crawler_type", "release")
    return site_name, crawler_type
```

to:

```python
def _crawler_metadata(path: Path, fallback_site_name: str) -> tuple[str, str, bool]:
    crawler = load_crawler_from_path(path)
    site_name = getattr(crawler, "site_name", fallback_site_name)
    crawler_type = getattr(crawler, "crawler_type", "release")
    requires_discogs_release = getattr(crawler, "requires_discogs_release", False)
    return site_name, crawler_type, requires_discogs_release
```

And in `seed_bundled_crawlers`, change:

```python
            site_name, crawler_type = _crawler_metadata(dest, src.stem.replace("_", " ").title())
            register_crawler(conn, site_name, str(dest), crawler_type)
```

to:

```python
            site_name, crawler_type, requires_discogs_release = _crawler_metadata(dest, src.stem.replace("_", " ").title())
            register_crawler(conn, site_name, str(dest), crawler_type, requires_discogs_release)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_crawler_crud.py -v`
Expected: PASS — every test in the file.

- [ ] **Step 5: Commit**

```bash
git add backend/db.py backend/main.py backend/crawlers/discogs_marketplace.py backend/tests/test_crawler_crud.py
```

Commit message body:

```
feat: add requires_discogs_release flag, set it on discogs_marketplace

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: cli
ai-executor: remote-agent
```

---

### Task 3: `db.py` — resolve, enqueue, and record a stock-item crawl target

**Files:**
- Modify: `backend/db.py` (`claim_crawl_queue_batch`; new `get_stock_item_identity`, `enqueue_crawl_queue_for_stock_item`, `upsert_stock_item_listing`)
- Test: `backend/tests/test_crawl_queue.py`

**Interfaces:**
- Consumes: `stock_item_identities` table from Task 1.
- Produces: `db.get_stock_item_identity(conn, item_key) -> Optional[dict]`; `db.enqueue_crawl_queue_for_stock_item(conn, item_key, crawler_id)`; `db.upsert_stock_item_listing(conn, item_key, crawler_id, url, price, shipping, currency, condition)`; `claim_crawl_queue_batch`'s returned rows gain an `item_key` key (`None` for a release-based row).

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_crawl_queue.py`, first change the fixture's teardown TRUNCATE (line 12) from:

```python
        conn.execute("TRUNCATE catalog, users, crawlers CASCADE")
```

to:

```python
        conn.execute("TRUNCATE catalog, users, crawlers, stock_item_identities CASCADE")
```

Add a second helper right after `_make_catalog_and_crawler`:

```python
def _make_stock_identity_and_crawler(conn, item_key="key1", site_name="Amazon"):
    conn.execute(
        "INSERT INTO stock_item_identities (item_key, artist, title) VALUES (%s, 'A', 'T')", [item_key]
    )
    db.register_crawler(conn, site_name, "/x.py")
    return conn.execute("SELECT id FROM crawlers WHERE site_name = %s", [site_name]).fetchone()["id"]
```

Then add these tests at the end of the file:

```python
def test_get_stock_item_identity_returns_none_for_unknown_key(admin_conn):
    assert db.get_stock_item_identity(admin_conn, "missing") is None


def test_get_stock_item_identity_returns_the_row(admin_conn):
    admin_conn.execute(
        "INSERT INTO stock_item_identities (item_key, artist, title, format) VALUES ('key1', 'A', 'T', 'LP')"
    )
    row = db.get_stock_item_identity(admin_conn, "key1")
    assert row["artist"] == "A"
    assert row["title"] == "T"
    assert row["format"] == "LP"


def test_enqueue_crawl_queue_for_stock_item_is_idempotent(admin_conn):
    crawler_id = _make_stock_identity_and_crawler(admin_conn)
    admin_conn.commit()
    db.enqueue_crawl_queue_for_stock_item(admin_conn, "key1", crawler_id)
    db.enqueue_crawl_queue_for_stock_item(admin_conn, "key1", crawler_id)
    admin_conn.commit()
    rows = admin_conn.execute("SELECT * FROM crawl_queue WHERE item_key = 'key1'").fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"
    assert rows[0]["discogs_id"] is None


def test_enqueue_crawl_queue_for_stock_item_resets_done_row_to_pending(admin_conn):
    crawler_id = _make_stock_identity_and_crawler(admin_conn)
    admin_conn.commit()
    db.enqueue_crawl_queue_for_stock_item(admin_conn, "key1", crawler_id)
    admin_conn.commit()
    [row] = db.claim_crawl_queue_batch(admin_conn, "worker-1", limit=10)
    db.mark_crawl_queue_done(admin_conn, row["id"])
    admin_conn.commit()

    db.enqueue_crawl_queue_for_stock_item(admin_conn, "key1", crawler_id)
    admin_conn.commit()
    rows = admin_conn.execute("SELECT * FROM crawl_queue WHERE item_key = 'key1'").fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] == row["id"]
    assert rows[0]["status"] == "pending"


def test_claim_crawl_queue_batch_returns_item_key_for_a_stock_item_row(admin_conn):
    crawler_id = _make_stock_identity_and_crawler(admin_conn)
    admin_conn.commit()
    db.enqueue_crawl_queue_for_stock_item(admin_conn, "key1", crawler_id)
    admin_conn.commit()

    [row] = db.claim_crawl_queue_batch(admin_conn, "worker-1", limit=10)
    assert row["item_key"] == "key1"
    assert row["discogs_id"] is None


def test_claim_crawl_queue_batch_returns_null_item_key_for_a_release_row(admin_conn):
    crawler_id = _make_catalog_and_crawler(admin_conn)
    admin_conn.commit()
    db.enqueue_crawl_queue(admin_conn, "r1", crawler_id)
    admin_conn.commit()

    [row] = db.claim_crawl_queue_batch(admin_conn, "worker-1", limit=10)
    assert row["discogs_id"] == "r1"
    assert row["item_key"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_crawl_queue.py -v`
Expected: FAIL — `get_stock_item_identity`/`enqueue_crawl_queue_for_stock_item` don't exist (`AttributeError`), and `claim_crawl_queue_batch`'s rows have no `item_key` key (`KeyError`).

- [ ] **Step 3: Implement**

In `backend/db.py`, add right after `get_catalog_release`:

```python
def get_stock_item_identity(conn, item_key: str) -> Optional[dict]:
    return conn.execute(
        "SELECT * FROM stock_item_identities WHERE item_key = %s", [item_key]
    ).fetchone()
```

Add right after `upsert_listing`:

```python
def upsert_stock_item_listing(
    conn,
    item_key: str,
    crawler_id: int,
    url: str,
    price: Optional[float],
    shipping: Optional[float],
    currency: Optional[str],
    condition: Optional[str],
):
    conn.execute(
        """
        INSERT INTO listings (item_key, crawler_id, url, price, shipping, currency, condition, last_checked)
        VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (item_key, crawler_id) DO UPDATE SET
            url = EXCLUDED.url, price = EXCLUDED.price, shipping = EXCLUDED.shipping,
            currency = EXCLUDED.currency, condition = EXCLUDED.condition, last_checked = CURRENT_TIMESTAMP
        """,
        [item_key, crawler_id, url, price, shipping, currency, condition],
    )
```

Add right after `enqueue_crawl_queue`:

```python
def enqueue_crawl_queue_for_stock_item(conn, item_key: str, crawler_id: int):
    conn.execute(
        """
        INSERT INTO crawl_queue (item_key, crawler_id) VALUES (%s, %s)
        ON CONFLICT (item_key, crawler_id) DO UPDATE SET
            status = 'pending', requested_at = CURRENT_TIMESTAMP, claimed_by = NULL, claimed_at = NULL
        WHERE crawl_queue.status = 'done'
        """,
        [item_key, crawler_id],
    )
```

In `claim_crawl_queue_batch`, change the `RETURNING` clause from:

```python
        RETURNING id, discogs_id, crawler_id
```

to:

```python
        RETURNING id, discogs_id, item_key, crawler_id
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_crawl_queue.py -v`
Expected: PASS — every test in the file.

- [ ] **Step 5: Commit**

```bash
git add backend/db.py backend/tests/test_crawl_queue.py
```

Commit message body:

```
feat: add db functions to resolve, enqueue, and record a stock-item crawl target

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: cli
ai-executor: remote-agent
```

---

### Task 4: `replace_stock_items` upserts `stock_item_identities` and returns written item_keys

**Files:**
- Modify: `backend/db.py` (`replace_stock_items`)
- Test: `backend/tests/test_stock_crud.py`

**Interfaces:**
- Consumes: `stock_item_identities` table from Task 1.
- Produces: `db.replace_stock_items(conn, crawler_id, items) -> list[str]` — now returns the item_keys it wrote (previously returned `None`).

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_stock_crud.py`, first change the fixture's teardown TRUNCATE (line 12) from:

```python
        conn.execute("TRUNCATE catalog, users, crawlers CASCADE")
```

to:

```python
        conn.execute("TRUNCATE catalog, users, crawlers, stock_item_identities CASCADE")
```

Add these tests after `test_replace_stock_items_clears_and_inserts_for_crawler`:

```python
def test_replace_stock_items_returns_the_written_item_keys(admin_conn):
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]

    item_keys = db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
        {"artist": "Artist B", "title": "Album B", "url": "https://x/2", "price": 15.0, "currency": "USD"},
    ])
    assert item_keys == [
        db.compute_item_key("Artist A".title(), "Album A", "https://x/1"),
        db.compute_item_key("Artist B".title(), "Album B", "https://x/2"),
    ]


def test_replace_stock_items_upserts_stock_item_identities(admin_conn):
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]

    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "aphex twin", "title": "Selected Ambient Works", "url": "https://x/1", "price": 20.0, "currency": "USD", "format": "LP"},
    ])
    admin_conn.commit()
    item_key = db.compute_item_key("aphex twin".title(), "Selected Ambient Works", "https://x/1")
    row = admin_conn.execute(
        "SELECT artist, title, format FROM stock_item_identities WHERE item_key = %s", [item_key]
    ).fetchone()
    assert row["artist"] == "Aphex Twin"
    assert row["title"] == "Selected Ambient Works"
    assert row["format"] == "LP"


def test_replace_stock_items_identity_row_survives_the_items_next_disappearance(admin_conn):
    # "Never delete" per the design doc: once an item_key stops appearing in
    # a crawler's items, replace_stock_items still deletes/reinserts
    # stock_items as usual, but its stock_item_identities row (and, by
    # extension, any listings/crawl_queue rows keyed on it) is left alone.
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]

    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
    ])
    admin_conn.commit()
    item_key = db.compute_item_key("Artist A".title(), "Album A", "https://x/1")

    db.replace_stock_items(admin_conn, crawler_id, [])
    admin_conn.commit()

    assert admin_conn.execute("SELECT * FROM stock_items WHERE crawler_id = %s", [crawler_id]).fetchall() == []
    row = admin_conn.execute(
        "SELECT artist FROM stock_item_identities WHERE item_key = %s", [item_key]
    ).fetchone()
    assert row["artist"] == "Artist A"


def test_replace_stock_items_updates_identity_row_in_place_on_rerun(admin_conn):
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    item_key = db.compute_item_key("Artist A".title(), "Album A", "https://x/1")

    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD", "format": "LP"},
    ])
    admin_conn.commit()
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 12.0, "currency": "USD", "format": "CD"},
    ])
    admin_conn.commit()

    row = admin_conn.execute(
        "SELECT format FROM stock_item_identities WHERE item_key = %s", [item_key]
    ).fetchone()
    assert row["format"] == "CD"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_stock_crud.py -k "item_keys or stock_item_identities or disappearance or in_place" -v`
Expected: FAIL — `test_replace_stock_items_returns_the_written_item_keys` fails because `replace_stock_items` returns `None`; the other three fail with `stock_item_identities` empty/`UndefinedTable`-adjacent errors (no row written yet).

- [ ] **Step 3: Implement**

In `backend/db.py`, replace `replace_stock_items` in full:

```python
def replace_stock_items(conn, crawler_id: int, items: list[dict]) -> list[str]:
    conn.execute("DELETE FROM stock_items WHERE crawler_id = %s", [crawler_id])
    if not items:
        return []
    rows = []
    item_keys = []
    for item in items:
        artist = normalize_artist_casing(item["artist"])
        title = normalize_title_casing(item["title"])
        # item_key keeps hashing the legacy str.title() casing (not the
        # corrected `artist`/`title` above) so existing stock_item_judgments
        # rows, which join on item_key, don't orphan for items whose casing
        # changed here.
        item_key = compute_item_key(item["artist"].title(), item["title"], item["url"])
        item_keys.append(item_key)
        rows.append((
            crawler_id, artist, title, item.get("format"), item.get("price"),
            item.get("currency"), item["url"], item.get("cover_image_url"), item_key,
        ))
        conn.execute(
            """
            INSERT INTO stock_item_identities (item_key, artist, title, format, last_seen)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (item_key) DO UPDATE SET
                artist = EXCLUDED.artist, title = EXCLUDED.title, format = EXCLUDED.format,
                last_seen = CURRENT_TIMESTAMP
            """,
            [item_key, artist, title, item.get("format")],
        )
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO stock_items
                (crawler_id, artist, title, format, price, currency, url, cover_image_url, item_key, last_seen)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            """,
            rows,
        )
    return item_keys
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_stock_crud.py -v`
Expected: PASS — every test in the file (the pre-existing casing-regression tests don't inspect the return value or `stock_item_identities`, so they're unaffected).

- [ ] **Step 5: Commit**

```bash
git add backend/db.py backend/tests/test_stock_crud.py
```

Commit message body:

```
feat: replace_stock_items upserts stock_item_identities, returns item_keys

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: cli
ai-executor: remote-agent
```

---

### Task 5: `_sync_stock` enqueues eligible price crawls for every stock item

**Files:**
- Modify: `backend/crawl_manager.py` (`_sync_stock`)
- Test: `backend/tests/test_crawl_manager.py`

**Interfaces:**
- Consumes: `db.replace_stock_items(...) -> list[str]` (Task 4), `db.enqueue_crawl_queue_for_stock_item` (Task 3), `crawlers.requires_discogs_release` (Task 1/2).
- Produces: no new public interface — behavior change only (`_sync_stock` now enqueues `crawl_queue` rows).

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_crawl_manager.py`, first change the `pg_schema` fixture's teardown TRUNCATE (in the fixture body, currently reading `conn.execute("TRUNCATE catalog, users, crawlers CASCADE")`) to:

```python
        conn.execute("TRUNCATE catalog, users, crawlers, stock_item_identities CASCADE")
```

Add these tests directly after `test_sync_stock_replaces_items_for_each_enabled_catalog_crawler`:

```python
async def test_sync_stock_enqueues_crawl_queue_for_eligible_price_crawlers(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Stock Site", "/x.py", crawler_type="catalog")
        db.register_crawler(conn, "Amazon", "/amazon.py", crawler_type="release")
        db.register_crawler(conn, "eBay", "/ebay.py", crawler_type="release")
        conn.commit()
        amazon_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        ebay_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'eBay'").fetchone()["id"]

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

    item_key = db.compute_item_key("A".title(), "T", "https://x/1")
    with db.get_admin_pool().connection() as conn:
        queued = conn.execute(
            "SELECT crawler_id FROM crawl_queue WHERE item_key = %s ORDER BY crawler_id", [item_key]
        ).fetchall()
    assert sorted(q["crawler_id"] for q in queued) == sorted([amazon_id, ebay_id])


async def test_sync_stock_does_not_enqueue_for_a_crawler_requiring_discogs_release(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Stock Site", "/x.py", crawler_type="catalog")
        db.register_crawler(conn, "Discogs", "/discogs.py", crawler_type="release", requires_discogs_release=True)
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

    with db.get_admin_pool().connection() as conn:
        queued = conn.execute("SELECT * FROM crawl_queue").fetchall()
    assert queued == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_crawl_manager.py -k "enqueues_crawl_queue_for_eligible or requiring_discogs_release" -v`
Expected: FAIL — `assert sorted([]) == sorted([amazon_id, ebay_id])` (nothing enqueued yet); the second test passes vacuously today (nothing is ever enqueued), so it stays red until Task 5's next test starts asserting real enqueue behavior exists — run both together and confirm the first one fails.

- [ ] **Step 3: Implement**

In `backend/crawl_manager.py`, `_sync_stock`'s import line, change:

```python
        from db import get_app_pool, get_enabled_crawlers, replace_stock_items, update_crawler_last_run
```

to:

```python
        from db import get_app_pool, get_enabled_crawlers, replace_stock_items, update_crawler_last_run, enqueue_crawl_queue_for_stock_item
```

Immediately after that import block (before `await self._broadcast({"status": "stock_sync_started", ...})`), add:

```python
        with get_app_pool().connection() as conn:
            eligible_price_crawlers = [
                c for c in get_enabled_crawlers(conn, crawler_type="release") if not c["requires_discogs_release"]
            ]
```

Then change the per-crawler block:

```python
                consecutive_429_sites = []
                with get_app_pool().connection() as conn:
                    replace_stock_items(conn, crawler._db_id, items)
                    update_crawler_last_run(conn, crawler._db_id)
                    conn.commit()
```

to:

```python
                consecutive_429_sites = []
                with get_app_pool().connection() as conn:
                    item_keys = replace_stock_items(conn, crawler._db_id, items)
                    update_crawler_last_run(conn, crawler._db_id)
                    for item_key in item_keys:
                        for price_crawler in eligible_price_crawlers:
                            enqueue_crawl_queue_for_stock_item(conn, item_key, price_crawler["id"])
                    conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_crawl_manager.py -v`
Expected: PASS — every test in the file, including the pre-existing `_sync_stock` tests (`test_sync_stock_replaces_items_for_each_enabled_catalog_crawler` registers no release-type crawler, so `eligible_price_crawlers` is empty and its assertions are unaffected).

- [ ] **Step 5: Commit**

```bash
git add backend/crawl_manager.py backend/tests/test_crawl_manager.py
```

Commit message body:

```
feat: enqueue eligible price crawls for every stock item on stock sync

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: cli
ai-executor: remote-agent
```

---

### Task 6: Worker dispatch resolves and records a stock-item target

**Files:**
- Modify: `backend/crawl_manager.py` (`_drain_one_batch`, `_paced_search`, new `_broadcast_stock_listing_changed`)
- Test: `backend/tests/test_crawl_manager.py`, `backend/tests/test_crawl_router.py`

**Interfaces:**
- Consumes: `db.get_stock_item_identity`, `db.upsert_stock_item_listing` (Task 3); `claim_crawl_queue_batch` rows carrying `item_key` (Task 3).
- Produces: `_broadcast_stock_listing_changed(item_key, crawler_id, status)` — emits `{"type": "listing_changed", "item_key": ..., "crawler_id": ..., "status": ...}` (no `discogs_id` key).

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_crawl_manager.py`, directly after `test_worker_claims_and_completes_one_queue_row`:

```python
async def test_worker_claims_and_completes_one_stock_item_queue_row(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        conn.execute(
            "INSERT INTO stock_item_identities (item_key, artist, title) VALUES ('key1', 'A', 'T')"
        )
        db.enqueue_crawl_queue_for_stock_item(conn, "key1", crawler_id)
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
        listing = conn.execute("SELECT price, release_id FROM listings WHERE item_key = 'key1'").fetchone()
        queue_row = conn.execute("SELECT status FROM crawl_queue WHERE item_key = 'key1'").fetchone()
    assert listing["price"] == 9.99
    assert listing["release_id"] is None
    assert queue_row["status"] == "done"


async def test_worker_broadcasts_stock_listing_changed_with_no_discogs_id(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        conn.execute(
            "INSERT INTO stock_item_identities (item_key, artist, title) VALUES ('key1', 'A', 'T')"
        )
        db.enqueue_crawl_queue_for_stock_item(conn, "key1", crawler_id)
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    fake_plugin = AsyncMock()
    fake_plugin.search = AsyncMock(return_value=[{"url": "https://x", "price": 9.99, "shipping": None, "currency": "USD", "condition": None}])
    fake_plugin._db_id = crawler_id
    fake_plugin._db_site_name = "Amazon"

    q = manager.subscribe()
    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
        await manager._drain_one_batch("worker-test", {crawler_id: fake_plugin}, pages={})

    event = q.get_nowait()
    assert event["type"] == "listing_changed"
    assert event["item_key"] == "key1"
    assert "discogs_id" not in event
```

Add to `backend/tests/test_crawl_router.py`, directly after `test_event_touches_user_is_scoped_to_the_calling_users_own_library`:

```python
def test_event_touches_user_returns_true_for_a_stock_item_event_with_no_discogs_id(pg_test_db, authed_client_factory):
    alice, bob, crawler_id = _setup_two_users_each_with_a_different_release()
    event = {"type": "listing_changed", "item_key": "key1", "crawler_id": crawler_id, "status": "found"}
    assert crawl_router._event_touches_user(event, alice["id"]) is True
    assert crawl_router._event_touches_user(event, bob["id"]) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_crawl_manager.py -k "stock_item_queue_row or stock_listing_changed" -v`
Expected: FAIL — `get_stock_item_identity` returns a row but `_drain_one_batch` still does `get_catalog_release(conn, row["discogs_id"])` unconditionally (`row["discogs_id"]` is `None`, so it looks up nothing and treats the target as missing, marking the row done with no listing written); `_broadcast_stock_listing_changed` doesn't exist yet.

Run: `cd backend && pytest tests/test_crawl_router.py -k stock_item_event -v`
Expected: PASS already — this one needs no code change (see Task's rationale below); confirm it passes as-is before moving on, so a later regression is caught by this test rather than masked by it never having run red.

- [ ] **Step 3: Implement**

In `backend/crawl_manager.py`, `_drain_one_batch`'s import line, change:

```python
        from db import get_app_pool, claim_crawl_queue_batch, mark_crawl_queue_done, upsert_listing, get_catalog_release
```

to:

```python
        from db import get_app_pool, claim_crawl_queue_batch, mark_crawl_queue_done, upsert_listing, get_catalog_release, get_stock_item_identity, upsert_stock_item_listing
```

Replace the `for row in rows:` loop body in full:

```python
        for row in rows:
            plugin = plugins_by_crawler_id.get(row["crawler_id"])
            with get_app_pool().connection() as conn:
                if row["discogs_id"] is not None:
                    target = get_catalog_release(conn, row["discogs_id"])
                else:
                    target = get_stock_item_identity(conn, row["item_key"])

            if plugin is None or target is None:
                with get_app_pool().connection() as conn:
                    mark_crawl_queue_done(conn, row["id"])
                    conn.commit()
                continue

            if row["crawler_id"] not in pages:
                pages[row["crawler_id"]] = await _new_context(self._browser, self._stealth)

            try:
                matches, bot_detected = await self._paced_search(row["crawler_id"], plugin, target, pages)
            except Exception as e:
                log.error("[%s] Crawl failed for %s: %s", plugin._db_site_name, row["discogs_id"] or row["item_key"], e)
                self._record_site_result(row["crawler_id"], succeeded=False)
                with get_app_pool().connection() as conn:
                    mark_crawl_queue_done(conn, row["id"])
                    conn.commit()
                continue

            self._record_site_result(row["crawler_id"], succeeded=bool(matches) and not bot_detected)

            with get_app_pool().connection() as conn:
                if matches:
                    best = matches[0]
                    if row["discogs_id"] is not None:
                        upsert_listing(
                            conn, row["discogs_id"], row["crawler_id"], best["url"],
                            best.get("price"), best.get("shipping"), best.get("currency"), best.get("condition"),
                        )
                    else:
                        upsert_stock_item_listing(
                            conn, row["item_key"], row["crawler_id"], best["url"],
                            best.get("price"), best.get("shipping"), best.get("currency"), best.get("condition"),
                        )
                mark_crawl_queue_done(conn, row["id"])
                conn.commit()

            status = "found" if matches else "not_found"
            if row["discogs_id"] is not None:
                await self._broadcast_listing_changed(row["discogs_id"], row["crawler_id"], status)
            else:
                await self._broadcast_stock_listing_changed(row["item_key"], row["crawler_id"], status)

        return len(rows)
```

Change `_paced_search`'s signature and body (rename `release` to `target` — both target kinds pass the same `{artist, title, format, ...}`-shaped dict):

```python
    async def _paced_search(self, crawler_id: int, plugin, target: dict, pages: dict) -> tuple:
```

and, inside its body:

```python
            context, page = pages[crawler_id]
            bot_detected = False
            try:
                try:
                    matches = await plugin.search(target, page)
                except BotDetectedError:
                    bot_detected = True
                    context, page = await _reset_context(context, self._browser, self._stealth, None)
                    pages[crawler_id] = (context, page)
                    matches = await plugin.search(target, page)
                return matches, bot_detected
```

Add `_broadcast_stock_listing_changed` right after `_broadcast_listing_changed`:

```python
    async def _broadcast_stock_listing_changed(self, item_key: str, crawler_id: int, status: str):
        self._seq += 1
        event = {"id": self._seq, "type": "listing_changed", "item_key": item_key, "crawler_id": crawler_id, "status": status}
        for q in list(self._subscribers):
            await q.put(event)
```

No change is needed in `backend/routers/crawl.py` — `_event_touches_user`'s existing `if not discogs_id: return True` already treats an event with no `discogs_id` key as touching every user, which is exactly the visibility a global stock-item event needs. The `test_event_touches_user_returns_true_for_a_stock_item_event_with_no_discogs_id` test added in Step 1 documents this rather than driving a change.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_crawl_manager.py tests/test_crawl_router.py -v`
Expected: PASS — every test in both files.

- [ ] **Step 5: Commit**

```bash
git add backend/crawl_manager.py backend/tests/test_crawl_manager.py backend/tests/test_crawl_router.py
```

Commit message body:

```
feat: dispatch worker to resolve and record a stock-item crawl target

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: cli
ai-executor: remote-agent
```

---

### Task 7: Full-repo verification and version bump

**Files:** `backend/version.py` only (plus none — this task is otherwise verification-only)

- [ ] **Step 1: Run the full backend test suite**

Run: `cd backend && pytest`
Expected: all tests PASS

- [ ] **Step 2: Run the TypeScript build and lint (frontend is untouched, but confirm no drift)**

Run: `cd frontend && npm run build && npm run lint`
Expected: both exit 0 — this slice makes no frontend changes, so this just confirms the working tree is otherwise clean.

- [ ] **Step 3: Manual verification**

Run the backend (`cd backend && pip install -e ".[dev]" && uvicorn main:app --reload --port 8000`) per `CLAUDE.md`'s "Running" section, with at least one `crawler_type="catalog"`/`"catalog_browser"` crawler and both `amazon`/`ebay` enabled, and:

- Trigger a stock sync (however Settings currently exposes it) and confirm it completes without error.
- Query Postgres directly (`psql $DATABASE_URL`) and confirm: `stock_item_identities` has one row per distinct item the stock sync found; `crawl_queue` has a `pending` row per `(item_key, eligible crawler)` pair, with no rows for the `discogs_marketplace`/`Discogs` crawler; after the worker pool has had time to drain the queue, `listings` has `item_key`-keyed rows with prices.
- Confirm the Discogs tab, Wishlist tab, and Store tab all still load and behave exactly as before this change — this slice adds no UI.

Stop the dev server (Ctrl-C) when done.

- [ ] **Step 4: Bump the version per `CLAUDE.md`'s versioning rule**

In `backend/version.py`, change:

```python
VERSION = "2.13"
```

to:

```python
VERSION = "2.14"
```

- [ ] **Step 5: Commit the version bump**

```bash
git add backend/version.py
```

Commit message body:

```
chore: bump version for crawl-target expansion

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: cli
ai-executor: remote-agent
```

---

## Self-Review Notes

- **Spec coverage:** every decision in the spec's "Decisions carried from brainstorming" section has a task — discogs_marketplace excluded structurally (Tasks 1, 2, 5), enqueue automatic on stock sync (Task 5), no orphan cleanup (Task 4's `test_replace_stock_items_identity_row_survives_the_items_next_disappearance` makes the absence of cleanup code an explicit, checked behavior rather than an accident), no queue prioritization and no new migration tooling (both are "do nothing further" decisions with no corresponding task, correctly — the schema in Task 1 is already the idempotent `IF NOT EXISTS`/`DROP NOT NULL` shape the spec calls for). The spec's "Why a new dimension table" rationale is Task 1's `stock_item_identities` table plus Task 4's identity-survives-disappearance test. Out of scope (Store tab UI, intersection Collection tab) has no task, correctly.
- **Type/name consistency:** `enqueue_crawl_queue_for_stock_item`/`upsert_stock_item_listing`/`get_stock_item_identity` (Task 3) are the exact names Task 5 (`_sync_stock`) and Task 6 (`_drain_one_batch`) import and call. `replace_stock_items`' return type (Task 4, `list[str]`) matches how Task 5 consumes it (`for item_key in item_keys`). `crawlers.requires_discogs_release` (Task 1's column, Task 2's `register_crawler` kwarg and `discogs_marketplace.py` class attribute) is the exact name Task 5's `_sync_stock` filters on (`if not c["requires_discogs_release"]`). `claim_crawl_queue_batch`'s `item_key` (Task 3) is the exact key Task 6's `_drain_one_batch` branches on.
- **Runtime/agent document impact:** per the spec's own "Runtime/agent document impact" section, no `.agents/INPUTS.md`, `.agents/OUTPUTS.md`, `.agents/INSTRUCTIONS.md`, or README exist or need updating — this is new internal fan-out from an already-existing sync path onto an already-existing worker, using already-existing plugins. No task adds one, correctly.
- **Scope:** one subsystem end to end (schema → registration → db functions → sync/enqueue → worker dispatch → verification), each task's tests passing standalone before the next task depends on it. No frontend task exists because this slice is deliberately backend-only (per the spec's "Out of scope"); no further decomposition needed.
