# Store "Recommended" Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Store tab's disabled "Recommended" filter option work — a Claude-judged, batched, cached recommendation flag on `stock_items`, computed during the existing stock sync and exposed as a cheap SQL filter at read time.

**Architecture:** A new `stock_item_judgments` table (keyed by a content hash of artist/title/url, not the row id `replace_stock_items` destroys every sync) stores Claude's verdicts. A stored `item_key` column on `stock_items` makes both "find unseen items" and the `recommended` read filter plain SQL joins. `crawl_manager._sync_stock()` gains a judgment phase — capped, batched Claude calls via a new `recommendations.py` module — that runs after the existing per-crawler catalog loop. `GET /api/stock` and `GET /api/stock/artists` gain a `recommended` boolean param. The frontend gates the dropdown option on whether an Anthropic API key is configured (added to Settings) and shows each recommendation's one-line reason as a tooltip.

**Tech Stack:** Python 3.9 / FastAPI / SQLite (backend), React 19 / TypeScript / Vite / vitest (frontend), `anthropic` Python SDK (already a dependency), `respx` for mocking its underlying httpx calls in tests.

**Spec:** [`docs/superpowers/specs/2026-07-06-store-recommended-filter-design.md`](../specs/2026-07-06-store-recommended-filter-design.md)

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/db.py` | Modify: schema (new table + column), `replace_stock_items`, `get_stock_items`, `get_distinct_stock_artists`. New: `compute_item_key`, `get_unjudged_stock_items`, `get_taste_listing`, `upsert_stock_judgments`. |
| `backend/routers/stock.py` | Modify: thread `recommended` query param through both endpoints. |
| `backend/routers/settings.py` | Modify: `anthropic_api_key` field on the settings model/get/post. |
| `backend/recommendations.py` | New: prompt building + one batched Claude call (`judge_batch`), no orchestration. |
| `backend/crawl_manager.py` | Modify: `_sync_stock` gains a judgment phase (`_run_judgment_phase`), new SSE events. |
| `frontend/src/api/types.ts` | Modify: `StockItem.reason`, `Settings.anthropic_api_key`, new `CrawlEvent` status values + `judged` field. |
| `frontend/src/api/client.ts` | Modify: `getStock`/`getStockArtists` gain `recommended`. |
| `frontend/src/App.tsx` | Modify: fetch settings once, derive `hasAnthropicKey`, pass to `StockBrowser`; handle `stock_judgment_*` SSE events. |
| `frontend/src/views/StockBrowser.tsx` | Modify: `hasAnthropicKey` prop gates the dropdown option; thread `recommended` through fetch calls; render `reason` as a tooltip; fix `stockFilter` localStorage restore to recognize `'recommended'`. |
| `frontend/src/views/Settings.tsx` | Modify: add `anthropic_api_key` row to `SETTING_ROWS`. |

Tests live alongside: `backend/tests/test_db.py`, `test_stock_router.py`, `test_settings_router.py` (new), `test_recommendations.py` (new), `test_crawl_manager.py`; `frontend/src/test/stockBrowser.test.tsx`, `inStockTab.test.tsx`.

---

### Task 1: Schema — `stock_item_judgments` table, `stock_items.item_key` column, `compute_item_key`

**Files:**
- Modify: `backend/db.py`
- Test: `backend/tests/test_db.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_db.py`, in the import block at the top add `compute_item_key` to the `from db import (...)` list, then add near the stock-items test section:

```python
def test_compute_item_key_stable_for_same_inputs():
    assert compute_item_key("Rob Zombie", "The Great Satan", "https://x/1") == \
        compute_item_key("Rob Zombie", "The Great Satan", "https://x/1")


def test_compute_item_key_differs_when_any_field_differs():
    base = compute_item_key("Rob Zombie", "The Great Satan", "https://x/1")
    assert compute_item_key("NAILS", "The Great Satan", "https://x/1") != base
    assert compute_item_key("Rob Zombie", "Other Title", "https://x/1") != base
    assert compute_item_key("Rob Zombie", "The Great Satan", "https://x/2") != base


def test_stock_item_judgments_table_exists(conn):
    conn.execute(
        "INSERT INTO stock_item_judgments (item_key, recommended, reason) VALUES ('k1', 1, 'similar genre')"
    )
    row = conn.execute(
        "SELECT item_key, recommended, reason FROM stock_item_judgments WHERE item_key = 'k1'"
    ).fetchone()
    assert row["recommended"] == 1
    assert row["reason"] == "similar genre"


def test_stock_items_has_item_key_column(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    cols = {row[1] for row in conn.execute("PRAGMA table_info(stock_items)").fetchall()}
    assert "item_key" in cols
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_db.py -k "item_key or stock_item_judgments" -v`
Expected: FAIL — `ImportError: cannot import name 'compute_item_key'` (import line) and/or `no such table: stock_item_judgments`.

- [ ] **Step 3: Implement**

In `backend/db.py`, add `import hashlib` to the top-level imports (alongside the existing `import sqlite3`, `import threading`).

In the `SCHEMA` string, change the `stock_items` table definition and add the new table right after it:

```python
CREATE TABLE IF NOT EXISTS stock_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    crawler_id INTEGER NOT NULL REFERENCES crawlers(id),
    artist TEXT NOT NULL,
    title TEXT NOT NULL,
    format TEXT,
    price REAL,
    currency TEXT,
    url TEXT NOT NULL,
    cover_image_url TEXT,
    item_key TEXT,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS stock_item_judgments (
    item_key TEXT PRIMARY KEY,
    recommended INTEGER NOT NULL,
    reason TEXT,
    judged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

In `init_db`, right after the existing `crawler_cols` migration block (the `if "crawler_type" not in crawler_cols:` block), add:

```python
    stock_cols = {row[1] for row in conn.execute("PRAGMA table_info(stock_items)").fetchall()}
    if "item_key" not in stock_cols:
        conn.execute("ALTER TABLE stock_items ADD COLUMN item_key TEXT")
```

Add this new function near the other stock-items helpers (right before `replace_stock_items`):

```python
def compute_item_key(artist: str, title: str, url: str) -> str:
    return hashlib.sha256(f"{artist}|{title}|{url}".encode()).hexdigest()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_db.py -k "item_key or stock_item_judgments" -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/db.py backend/tests/test_db.py
git commit -m "store-recommended-filter: add stock_item_judgments table and item_key column"
```

---

### Task 2: `replace_stock_items` computes and stores `item_key`

**Files:**
- Modify: `backend/db.py`
- Test: `backend/tests/test_db.py`

- [ ] **Step 1: Write the failing test**

```python
def test_replace_stock_items_stores_item_key(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "T1", "format": "Vinyl", "price": 1.0, "currency": "USD", "url": "https://x/1"},
    ])
    row = conn.execute("SELECT item_key FROM stock_items WHERE crawler_id = ?", [crawler_id]).fetchone()
    assert row["item_key"] == compute_item_key("Rob Zombie", "T1", "https://x/1")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_db.py::test_replace_stock_items_stores_item_key -v`
Expected: FAIL — `row["item_key"]` is `None`.

- [ ] **Step 3: Implement**

Replace the body of `replace_stock_items` in `backend/db.py`:

```python
def replace_stock_items(conn: sqlite3.Connection, crawler_id: int, items: list[dict]):
    conn.execute("DELETE FROM stock_items WHERE crawler_id = ?", [crawler_id])
    rows = []
    for item in items:
        artist = item["artist"].title()
        rows.append((
            crawler_id, artist, item["title"], item.get("format"), item.get("price"),
            item.get("currency"), item["url"], item.get("cover_image_url"),
            compute_item_key(artist, item["title"], item["url"]),
        ))
    conn.executemany("""
        INSERT INTO stock_items (crawler_id, artist, title, format, price, currency, url, cover_image_url, item_key, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    """, rows)
    conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_db.py::test_replace_stock_items_stores_item_key -v`
Expected: PASS

- [ ] **Step 5: Run the full existing stock-items test block to check nothing broke**

Run: `cd backend && pytest tests/test_db.py -k "stock_items or stock_artists" -v`
Expected: PASS (all existing + new tests)

- [ ] **Step 6: Commit**

```bash
git add backend/db.py backend/tests/test_db.py
git commit -m "store-recommended-filter: compute and store item_key in replace_stock_items"
```

---

### Task 3: `get_stock_items` / `get_distinct_stock_artists` gain `recommended` + `reason`

**Files:**
- Modify: `backend/db.py`
- Test: `backend/tests/test_db.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_get_stock_items_recommended_filters_to_judged_recommended(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "T1", "format": "Vinyl", "price": 1.0, "currency": "USD", "url": "https://x/1"},
        {"artist": "NAILS", "title": "T2", "format": "Vinyl", "price": 2.0, "currency": "USD", "url": "https://x/2"},
    ])
    key = compute_item_key("Rob Zombie", "T1", "https://x/1")
    conn.execute(
        "INSERT INTO stock_item_judgments (item_key, recommended, reason) VALUES (?, 1, 'similar genre')", [key]
    )
    result = get_stock_items(conn, recommended=True)
    assert result["total"] == 1
    assert result["items"][0]["artist"] == "Rob Zombie"


def test_get_stock_items_excludes_judged_not_recommended(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "T1", "format": "Vinyl", "price": 1.0, "currency": "USD", "url": "https://x/1"},
    ])
    key = compute_item_key("Rob Zombie", "T1", "https://x/1")
    conn.execute("INSERT INTO stock_item_judgments (item_key, recommended, reason) VALUES (?, 0, NULL)", [key])
    result = get_stock_items(conn, recommended=True)
    assert result["total"] == 0


def test_get_stock_items_includes_reason_when_judged(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "T1", "format": "Vinyl", "price": 1.0, "currency": "USD", "url": "https://x/1"},
    ])
    key = compute_item_key("Rob Zombie", "T1", "https://x/1")
    conn.execute(
        "INSERT INTO stock_item_judgments (item_key, recommended, reason) VALUES (?, 1, 'similar genre')", [key]
    )
    result = get_stock_items(conn)
    assert result["items"][0]["reason"] == "similar genre"


def test_get_stock_items_reason_none_when_unjudged(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "T1", "format": "Vinyl", "price": 1.0, "currency": "USD", "url": "https://x/1"},
    ])
    result = get_stock_items(conn)
    assert result["items"][0]["reason"] is None


def test_get_distinct_stock_artists_recommended_filters(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "T1", "format": "Vinyl", "price": 1.0, "currency": "USD", "url": "https://x/1"},
        {"artist": "NAILS", "title": "T2", "format": "Vinyl", "price": 2.0, "currency": "USD", "url": "https://x/2"},
    ])
    key = compute_item_key("Rob Zombie", "T1", "https://x/1")
    conn.execute("INSERT INTO stock_item_judgments (item_key, recommended, reason) VALUES (?, 1, NULL)", [key])
    assert get_distinct_stock_artists(conn, recommended=True) == ["Rob Zombie"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_db.py -k "recommended" -v`
Expected: FAIL — `TypeError: get_stock_items() got an unexpected keyword argument 'recommended'` (and similarly for `get_distinct_stock_artists`). The `reason`-related tests fail with `KeyError: 'reason'`.

- [ ] **Step 3: Implement**

Replace `get_stock_items` in `backend/db.py`:

```python
def get_stock_items(
    conn: sqlite3.Connection,
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
    allowed_sort = {"artist", "title", "format", "price"}
    if sort not in allowed_sort:
        sort = "artist"

    conditions = []
    params: list = []
    if search:
        conditions.append("(s.artist LIKE ? OR s.title LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    if artist:
        conditions.append("s.artist = ?")
        params.append(artist)
    if overlapping:
        conditions.append("LOWER(s.artist) IN (SELECT LOWER(artist) FROM releases WHERE in_collection = 1)")
    if recommended:
        conditions.append("s.item_key IN (SELECT item_key FROM stock_item_judgments WHERE recommended = 1)")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    total = conn.execute(f"SELECT COUNT(*) FROM stock_items s {where}", params).fetchone()[0]

    offset = (page - 1) * per_page
    null_order = "ASC" if order_sql == "ASC" else "DESC"
    order_clause = f"CASE WHEN s.{sort} IS NULL THEN 1 ELSE 0 END {null_order}, s.{sort} {order_sql}"
    rows = conn.execute(f"""
        SELECT s.id, s.artist, s.title, s.format, s.price, s.currency, s.url, s.cover_image_url, s.last_seen,
               c.site_name AS source, j.reason AS reason
        FROM stock_items s
        JOIN crawlers c ON c.id = s.crawler_id
        LEFT JOIN stock_item_judgments j ON j.item_key = s.item_key
        {where}
        ORDER BY {order_clause}
        LIMIT ? OFFSET ?
    """, params + [per_page, offset]).fetchall()

    return {"total": total, "page": page, "per_page": per_page, "items": [dict(row) for row in rows]}
```

Replace `get_distinct_stock_artists`:

```python
def get_distinct_stock_artists(conn: sqlite3.Connection, overlapping: bool = False, recommended: bool = False) -> list[str]:
    conditions = []
    if overlapping:
        conditions.append("LOWER(artist) IN (SELECT LOWER(artist) FROM releases WHERE in_collection = 1)")
    if recommended:
        conditions.append("item_key IN (SELECT item_key FROM stock_item_judgments WHERE recommended = 1)")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = conn.execute(f"SELECT DISTINCT artist FROM stock_items {where} ORDER BY artist").fetchall()
    return [row[0] for row in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_db.py -k "stock_items or stock_artists" -v`
Expected: PASS (all existing + new tests — existing `overlapping` tests must still pass unchanged)

- [ ] **Step 5: Commit**

```bash
git add backend/db.py backend/tests/test_db.py
git commit -m "store-recommended-filter: add recommended filter and reason field to stock queries"
```

---

### Task 4: `get_unjudged_stock_items`, `get_taste_listing`, `upsert_stock_judgments`

**Files:**
- Modify: `backend/db.py`
- Test: `backend/tests/test_db.py`

- [ ] **Step 1: Write the failing tests**

Add `get_unjudged_stock_items, get_taste_listing, upsert_stock_judgments` to the `from db import (...)` block at the top of the test file, then add:

```python
def test_get_unjudged_stock_items_excludes_judged(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "T1", "format": "Vinyl", "price": 1.0, "currency": "USD", "url": "https://x/1"},
        {"artist": "NAILS", "title": "T2", "format": "Vinyl", "price": 2.0, "currency": "USD", "url": "https://x/2"},
    ])
    key = compute_item_key("Rob Zombie", "T1", "https://x/1")
    conn.execute("INSERT INTO stock_item_judgments (item_key, recommended, reason) VALUES (?, 1, NULL)", [key])
    unjudged = get_unjudged_stock_items(conn, limit=10)
    assert [u["artist"] for u in unjudged] == ["Nails"]


def test_get_unjudged_stock_items_respects_limit(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    replace_stock_items(conn, crawler_id, [
        {"artist": f"Artist {i}", "title": f"T{i}", "format": "Vinyl", "price": float(i), "currency": "USD", "url": f"https://x/{i}"}
        for i in range(5)
    ])
    unjudged = get_unjudged_stock_items(conn, limit=2)
    assert len(unjudged) == 2


def test_get_unjudged_stock_items_spillover_after_partial_judgment(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    replace_stock_items(conn, crawler_id, [
        {"artist": f"Artist {i}", "title": f"T{i}", "format": "Vinyl", "price": float(i), "currency": "USD", "url": f"https://x/{i}"}
        for i in range(5)
    ])
    first_batch = get_unjudged_stock_items(conn, limit=2)
    assert len(first_batch) == 2
    upsert_stock_judgments(conn, [
        {"item_key": item["item_key"], "recommended": True, "reason": None} for item in first_batch
    ])
    # The 2 just-judged items are gone from "unjudged"; the other 3 remain, unaffected —
    # this is what makes an over-the-cap sync's leftovers pick up automatically next run.
    remaining = get_unjudged_stock_items(conn, limit=10)
    assert len(remaining) == 3
    assert not ({item["item_key"] for item in first_batch} & {item["item_key"] for item in remaining})


def test_get_taste_listing_includes_collection_and_wishlist(conn):
    upsert_release(conn, _release(discogs_id="r1", artist="Rob Zombie", title="The Great Satan"))
    mark_in_collection(conn, "r1")
    upsert_release(conn, _release(discogs_id="r2", artist="NAILS", title="Every Bridge Burning"))
    mark_in_wishlist(conn, "r2")
    mark_not_in_collection(conn, "r2")
    assert get_taste_listing(conn) == ["NAILS - Every Bridge Burning", "Rob Zombie - The Great Satan"]


def test_get_taste_listing_excludes_neither_flag(conn):
    upsert_release(conn, _release(discogs_id="r1", artist="Rob Zombie", title="The Great Satan"))
    mark_not_in_collection(conn, "r1")
    assert get_taste_listing(conn) == []


def test_upsert_stock_judgments_inserts(conn):
    upsert_stock_judgments(conn, [{"item_key": "k1", "recommended": True, "reason": "similar genre"}])
    row = conn.execute("SELECT recommended, reason FROM stock_item_judgments WHERE item_key = 'k1'").fetchone()
    assert row["recommended"] == 1
    assert row["reason"] == "similar genre"


def test_upsert_stock_judgments_updates_existing(conn):
    upsert_stock_judgments(conn, [{"item_key": "k1", "recommended": True, "reason": "similar genre"}])
    upsert_stock_judgments(conn, [{"item_key": "k1", "recommended": False, "reason": None}])
    row = conn.execute("SELECT recommended, reason FROM stock_item_judgments WHERE item_key = 'k1'").fetchone()
    assert row["recommended"] == 0
    assert row["reason"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_db.py -k "unjudged or taste_listing or upsert_stock_judgments" -v`
Expected: FAIL — `ImportError` on the new names.

- [ ] **Step 3: Implement**

Add these three functions to `backend/db.py`, right after `get_distinct_stock_artists`:

```python
def get_unjudged_stock_items(conn: sqlite3.Connection, limit: int) -> list[dict]:
    rows = conn.execute("""
        SELECT s.item_key, s.artist, s.title
        FROM stock_items s
        LEFT JOIN stock_item_judgments j ON j.item_key = s.item_key
        WHERE j.item_key IS NULL
        GROUP BY s.item_key
        ORDER BY MIN(s.last_seen) ASC
        LIMIT ?
    """, [limit]).fetchall()
    return [dict(row) for row in rows]


def get_taste_listing(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT artist, title FROM releases WHERE in_collection = 1 OR in_wishlist = 1 ORDER BY artist, title"
    ).fetchall()
    return [f"{row[0]} - {row[1]}" for row in rows]


def upsert_stock_judgments(conn: sqlite3.Connection, judgments: list[dict]):
    conn.executemany("""
        INSERT INTO stock_item_judgments (item_key, recommended, reason, judged_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(item_key) DO UPDATE SET
            recommended=excluded.recommended, reason=excluded.reason, judged_at=CURRENT_TIMESTAMP
    """, [(j["item_key"], int(j["recommended"]), j.get("reason")) for j in judgments])
    conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_db.py -k "unjudged or taste_listing or upsert_stock_judgments" -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Run the whole file to confirm no regressions**

Run: `cd backend && pytest tests/test_db.py -v`
Expected: PASS, all tests (existing + new from Tasks 1–4)

- [ ] **Step 6: Commit**

```bash
git add backend/db.py backend/tests/test_db.py
git commit -m "store-recommended-filter: add unjudged-item, taste-listing, and judgment-upsert db helpers"
```

---

### Task 5: `routers/stock.py` — thread `recommended` through both endpoints

**Files:**
- Modify: `backend/routers/stock.py`
- Test: `backend/tests/test_stock_router.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_stock_router.py`:

```python
def test_list_stock_recommended_param(client, conn):
    register_crawler(conn, "Nuclear Blast", "/path/nb.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name='Nuclear Blast'").fetchone()[0]
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "T1", "format": "Vinyl", "price": 1.0, "currency": "USD", "url": "https://x/1"},
        {"artist": "NAILS", "title": "T2", "format": "Vinyl", "price": 2.0, "currency": "USD", "url": "https://x/2"},
    ])
    from db import compute_item_key
    key = compute_item_key("Rob Zombie", "T1", "https://x/1")
    conn.execute(
        "INSERT INTO stock_item_judgments (item_key, recommended, reason) VALUES (?, 1, 'similar genre')", [key]
    )
    r = client.get("/api/stock?recommended=true")
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["artist"] == "Rob Zombie"
    assert r.json()["items"][0]["reason"] == "similar genre"


def test_list_stock_artists_recommended_param(client, conn):
    register_crawler(conn, "Nuclear Blast", "/path/nb.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name='Nuclear Blast'").fetchone()[0]
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "T1", "format": "Vinyl", "price": 1.0, "currency": "USD", "url": "https://x/1"},
        {"artist": "NAILS", "title": "T2", "format": "Vinyl", "price": 2.0, "currency": "USD", "url": "https://x/2"},
    ])
    from db import compute_item_key
    key = compute_item_key("Rob Zombie", "T1", "https://x/1")
    conn.execute("INSERT INTO stock_item_judgments (item_key, recommended, reason) VALUES (?, 1, NULL)", [key])
    r = client.get("/api/stock/artists?recommended=true")
    assert r.json()["artists"] == ["Rob Zombie"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_stock_router.py -k recommended -v`
Expected: FAIL — `total` includes both rows (param silently ignored by FastAPI's default `False` since it isn't threaded through yet — actually FastAPI accepts the querystring but `list_stock` doesn't pass it to `get_stock_items`, so the filter has no effect and `total == 2`).

- [ ] **Step 3: Implement**

Replace the two endpoint functions in `backend/routers/stock.py`:

```python
@router.get("/stock")
def list_stock(
    search: Optional[str] = Query(None),
    artist: Optional[str] = Query(None),
    sort: str = Query("artist"),
    order: str = Query("asc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    overlapping: bool = Query(False),
    recommended: bool = Query(False),
):
    conn = get_connection()
    return get_stock_items(
        conn, search=search, artist=artist, sort=sort, order=order, page=page, per_page=per_page,
        overlapping=overlapping, recommended=recommended,
    )


@router.get("/stock/artists")
def list_stock_artists(overlapping: bool = Query(False), recommended: bool = Query(False)):
    conn = get_connection()
    return {"artists": get_distinct_stock_artists(conn, overlapping=overlapping, recommended=recommended)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_stock_router.py -v`
Expected: PASS (all existing + 2 new tests)

- [ ] **Step 5: Commit**

```bash
git add backend/routers/stock.py backend/tests/test_stock_router.py
git commit -m "store-recommended-filter: expose recommended query param on stock endpoints"
```

---

### Task 6: Settings — `anthropic_api_key` field

**Files:**
- Modify: `backend/routers/settings.py`
- Test: Create `backend/tests/test_settings_router.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_settings_router.py`:

```python
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routers import settings as settings_router


@pytest.fixture
def client(tmp_config_dir):
    app = FastAPI()
    app.include_router(settings_router.router, prefix="/api")
    yield TestClient(app)


def test_get_settings_anthropic_api_key_defaults_empty(client):
    r = client.get("/api/settings")
    assert r.json()["anthropic_api_key"] == ""


def test_post_settings_round_trips_anthropic_api_key(client):
    r = client.post("/api/settings", json={"discogs_token": "", "anthropic_api_key": "sk-ant-test"})
    assert r.status_code == 200
    r2 = client.get("/api/settings")
    assert r2.json()["anthropic_api_key"] == "sk-ant-test"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_settings_router.py -v`
Expected: FAIL — `KeyError: 'anthropic_api_key'` on the first test.

- [ ] **Step 3: Implement**

In `backend/routers/settings.py`, add a field to `SettingsUpdate`:

```python
class SettingsUpdate(BaseModel):
    discogs_token: str
    debug_screenshot_interval: int = 20
    shuffle_crawl_order: bool = True
    crawl_delay_seconds: int = 30
    consecutive_failure_limit: int = 10
    crawl_schedule: str = ""
    crawl_schedule_mode: str = "missing"
    collection_schedule: str = ""
    collection_schedule_mode: str = "all"
    ebay_app_id: str = ""
    ebay_cert_id: str = ""
    stock_schedule: str = ""
    anthropic_api_key: str = ""
```

In `get_settings`, add a line to the returned dict:

```python
        "stock_schedule": config.get("stock_schedule", ""),
        "anthropic_api_key": config.get("anthropic_api_key", ""),
    }
```

In `update_settings`, add a line before `save_config(config)`:

```python
    config["stock_schedule"] = body.stock_schedule
    config["anthropic_api_key"] = body.anthropic_api_key
    save_config(config)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_settings_router.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/routers/settings.py backend/tests/test_settings_router.py
git commit -m "store-recommended-filter: expose anthropic_api_key in Settings"
```

---

### Task 7: `backend/recommendations.py` — prompt building + one batched Claude call

**Files:**
- Create: `backend/recommendations.py`
- Test: Create `backend/tests/test_recommendations.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_recommendations.py`:

```python
import json
import respx
import httpx
import anthropic


_MESSAGES_URL = "https://api.anthropic.com/v1/messages"


def _mock_text_response(mock, model, text):
    mock.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={
        "id": "msg_1", "type": "message", "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "model": model, "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 10},
    }))


def test_build_batch_prompt_includes_taste_listing_and_items():
    from recommendations import build_batch_prompt
    prompt = build_batch_prompt(
        ["Rob Zombie - Hellbilly Deluxe"],
        [{"item_key": "k1", "artist": "NAILS", "title": "T1"}],
    )
    assert "Rob Zombie - Hellbilly Deluxe" in prompt
    assert "k1" in prompt
    assert "NAILS" in prompt


def test_build_batch_prompt_handles_empty_taste_listing():
    from recommendations import build_batch_prompt
    prompt = build_batch_prompt([], [{"item_key": "k1", "artist": "NAILS", "title": "T1"}])
    assert "empty" in prompt.lower()


@respx.mock
def test_judge_batch_parses_wellformed_response():
    from recommendations import judge_batch, MODEL
    client = anthropic.Anthropic(api_key="test-key", max_retries=0)
    _mock_text_response(respx, MODEL, json.dumps([{"item_key": "k1", "recommended": True, "reason": "similar genre"}]))
    results = judge_batch(client, ["Foo - Bar"], [{"item_key": "k1", "artist": "NAILS", "title": "T1"}])
    assert results == [{"item_key": "k1", "recommended": True, "reason": "similar genre"}]


@respx.mock
def test_judge_batch_strips_markdown_fences():
    from recommendations import judge_batch, MODEL
    client = anthropic.Anthropic(api_key="test-key", max_retries=0)
    body = "```json\n" + json.dumps([{"item_key": "k1", "recommended": False, "reason": "no overlap"}]) + "\n```"
    _mock_text_response(respx, MODEL, body)
    results = judge_batch(client, [], [{"item_key": "k1", "artist": "NAILS", "title": "T1"}])
    assert results == [{"item_key": "k1", "recommended": False, "reason": "no overlap"}]


@respx.mock
def test_judge_batch_returns_empty_on_malformed_json():
    from recommendations import judge_batch, MODEL
    client = anthropic.Anthropic(api_key="test-key", max_retries=0)
    _mock_text_response(respx, MODEL, "not json")
    results = judge_batch(client, [], [{"item_key": "k1", "artist": "NAILS", "title": "T1"}])
    assert results == []


@respx.mock
def test_judge_batch_returns_empty_on_api_error():
    from recommendations import judge_batch
    client = anthropic.Anthropic(api_key="test-key", max_retries=0)
    respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(500, json={
        "type": "error", "error": {"type": "api_error", "message": "boom"},
    }))
    results = judge_batch(client, [], [{"item_key": "k1", "artist": "NAILS", "title": "T1"}])
    assert results == []


@respx.mock
def test_judge_batch_skips_entries_missing_required_fields():
    from recommendations import judge_batch, MODEL
    client = anthropic.Anthropic(api_key="test-key", max_retries=0)
    _mock_text_response(respx, MODEL, json.dumps([
        {"item_key": "k1"},
        {"item_key": "k2", "recommended": True, "reason": "ok"},
    ]))
    results = judge_batch(client, [], [
        {"item_key": "k1", "artist": "A", "title": "T1"},
        {"item_key": "k2", "artist": "B", "title": "T2"},
    ])
    assert results == [{"item_key": "k2", "recommended": True, "reason": "ok"}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_recommendations.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'recommendations'`

- [ ] **Step 3: Implement**

Create `backend/recommendations.py`:

```python
import json
from logging_config import get_logger

log = get_logger("recommendations")

MODEL = "claude-haiku-4-5"
BATCH_SIZE = 40
SYNC_CAP = 300

SYSTEM_PROMPT = (
    "You are helping a vinyl record collector find new records they might like, "
    "based on their existing collection and wishlist.\n\n"
    "You will be given the collector's full collection/wishlist as a list of "
    "\"Artist - Title\" lines, followed by a batch of in-stock catalog items to judge.\n\n"
    "For each item, decide whether it's a good recommendation given the collector's "
    "taste (same genre/scene, related artists, similar labels, adjacent style — not "
    "just exact artist matches). Respond with a JSON array only, no other text, one "
    "entry per item in the same order:\n\n"
    "[{\"item_key\": \"<key>\", \"recommended\": true|false, \"reason\": \"<one short sentence>\"}]"
)


def build_batch_prompt(taste_listing: list[str], batch: list[dict]) -> str:
    taste_text = "\n".join(taste_listing) if taste_listing else "(empty — no collection or wishlist yet)"
    items_text = "\n".join(
        f'{{"item_key": "{item["item_key"]}", "artist": "{item["artist"]}", "title": "{item["title"]}"}}'
        for item in batch
    )
    return f"Collector's collection and wishlist:\n{taste_text}\n\nItems to judge:\n{items_text}"


def judge_batch(client, taste_listing: list[str], batch: list[dict]) -> list[dict]:
    """One Claude call judging a batch of items. Returns [] on any failure —
    caller leaves those items unjudged for retry on the next sync."""
    prompt = build_batch_prompt(taste_listing, batch)
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = "\n".join(line for line in text.splitlines() if not line.startswith("```")).strip()
        parsed = json.loads(text)
        return [
            {"item_key": entry["item_key"], "recommended": bool(entry["recommended"]), "reason": entry.get("reason")}
            for entry in parsed
            if "item_key" in entry and "recommended" in entry
        ]
    except Exception as e:
        log.error("Judgment batch failed: %s", e, exc_info=True)
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_recommendations.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/recommendations.py backend/tests/test_recommendations.py
git commit -m "store-recommended-filter: add recommendations module for batched Claude judgment"
```

---

### Task 8: `crawl_manager.py` — judgment phase wired into `_sync_stock`

**Files:**
- Modify: `backend/crawl_manager.py`
- Test: `backend/tests/test_crawl_manager.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_crawl_manager.py`:

```python
async def test_sync_stock_skips_judgment_when_no_api_key(manager, tmp_config_dir, monkeypatch):
    import config as cfg_module
    import db as db_module
    import crawler as crawler_module
    from db import register_crawler

    conn = sqlite3.connect(cfg_module.DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db_module.init_db(conn)
    register_crawler(conn, "Nuclear Blast", "/path/nb.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Nuclear Blast'").fetchone()[0]

    class _FakeCrawler:
        _db_id = crawler_id
        _db_site_name = "Nuclear Blast"

        async def crawl_catalog(self):
            yield {"artist": "Rob Zombie", "title": "T1", "price": 1.0, "currency": "USD", "url": "https://x/1"}

    monkeypatch.setattr(crawler_module, "load_enabled_crawlers", lambda enabled: [_FakeCrawler()])

    await manager._sync_stock()

    statuses = [e["status"] for e in manager.recent_events()]
    assert not any(s.startswith("stock_judgment") for s in statuses)
    conn.close()


async def test_sync_stock_runs_judgment_phase_when_api_key_configured(manager, tmp_config_dir, monkeypatch):
    import config as cfg_module
    import db as db_module
    import crawler as crawler_module
    import recommendations
    from db import register_crawler, compute_item_key

    cfg_module.save_config({"anthropic_api_key": "sk-ant-test"})

    conn = sqlite3.connect(cfg_module.DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db_module.init_db(conn)
    register_crawler(conn, "Nuclear Blast", "/path/nb.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Nuclear Blast'").fetchone()[0]

    class _FakeCrawler:
        _db_id = crawler_id
        _db_site_name = "Nuclear Blast"

        async def crawl_catalog(self):
            yield {"artist": "Rob Zombie", "title": "T1", "price": 1.0, "currency": "USD", "url": "https://x/1"}

    monkeypatch.setattr(crawler_module, "load_enabled_crawlers", lambda enabled: [_FakeCrawler()])

    key = compute_item_key("Rob Zombie", "T1", "https://x/1")
    monkeypatch.setattr(
        recommendations, "judge_batch",
        lambda client, taste, batch: [{"item_key": key, "recommended": True, "reason": "similar genre"}],
    )

    await manager._sync_stock()

    statuses = [e["status"] for e in manager.recent_events()]
    assert "stock_judgment_started" in statuses
    assert "stock_judgment_complete" in statuses
    row = conn.execute("SELECT recommended, reason FROM stock_item_judgments WHERE item_key = ?", [key]).fetchone()
    assert row["recommended"] == 1
    assert row["reason"] == "similar genre"
    conn.close()


async def test_sync_stock_judgment_phase_failure_broadcasts_error(manager, tmp_config_dir, monkeypatch):
    import config as cfg_module
    import db as db_module
    import crawler as crawler_module
    import recommendations
    from db import register_crawler

    cfg_module.save_config({"anthropic_api_key": "sk-ant-test"})

    conn = sqlite3.connect(cfg_module.DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db_module.init_db(conn)
    register_crawler(conn, "Nuclear Blast", "/path/nb.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Nuclear Blast'").fetchone()[0]

    class _FakeCrawler:
        _db_id = crawler_id
        _db_site_name = "Nuclear Blast"

        async def crawl_catalog(self):
            yield {"artist": "Rob Zombie", "title": "T1", "price": 1.0, "currency": "USD", "url": "https://x/1"}

    monkeypatch.setattr(crawler_module, "load_enabled_crawlers", lambda enabled: [_FakeCrawler()])

    def _boom(client, taste, batch):
        raise RuntimeError("boom")

    monkeypatch.setattr(recommendations, "judge_batch", _boom)

    await manager._sync_stock()

    statuses = [e["status"] for e in manager.recent_events()]
    assert "stock_judgment_error" in statuses
    assert "stock_sync_complete" in statuses  # phase failure doesn't abort the sync
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_crawl_manager.py -k judgment -v`
Expected: FAIL — no `stock_judgment_*` events are ever broadcast (the phase doesn't exist yet), so the "skips" test's `not any(...)` assertion trivially passes but the other two fail on the `assert "stock_judgment_started" in statuses` / `"stock_judgment_error" in statuses` lines.

- [ ] **Step 3: Implement**

In `backend/crawl_manager.py`, inside `_sync_stock`, add `from config import load_config` to the local imports at the top of the method (alongside the existing `from db import ...` and `from crawler import ...` lines).

Replace the tail of `_sync_stock` — from `await self._broadcast({"status": "stock_sync_complete", ...})` through the end of the `try` block — with:

```python
            api_key = load_config().get("anthropic_api_key", "")
            if api_key:
                await self._run_judgment_phase(conn, api_key)

            await self._broadcast({"status": "stock_sync_complete", "synced": total_synced})
            log.info("Stock sync complete: %d items", total_synced)
```

(everything after that — the `except asyncio.CancelledError:` / `except Exception as e:` / `finally:` block — stays unchanged.)

Add this new method to `CrawlManager`, right after `_sync_stock`:

```python
    async def _run_judgment_phase(self, conn, api_key: str):
        from db import get_unjudged_stock_items, get_taste_listing, upsert_stock_judgments
        import recommendations
        import anthropic

        unjudged = get_unjudged_stock_items(conn, recommendations.SYNC_CAP)
        if not unjudged:
            return

        await self._broadcast({"status": "stock_judgment_started"})
        log.info("Stock judgment started: %d unjudged items", len(unjudged))

        client = anthropic.Anthropic(api_key=api_key)
        taste_listing = get_taste_listing(conn)

        try:
            judged = 0
            for i in range(0, len(unjudged), recommendations.BATCH_SIZE):
                batch = unjudged[i:i + recommendations.BATCH_SIZE]
                results = recommendations.judge_batch(client, taste_listing, batch)
                if results:
                    upsert_stock_judgments(conn, results)
                    judged += len(results)
                await self._broadcast({"status": "stock_judgment_progress", "judged": judged, "total": len(unjudged)})

            await self._broadcast({"status": "stock_judgment_complete", "judged": judged})
            log.info("Stock judgment complete: %d items judged", judged)
        except Exception as e:
            log.error("Stock judgment phase failed: %s", e, exc_info=True)
            await self._broadcast({"status": "stock_judgment_error", "error": str(e)})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_crawl_manager.py -v`
Expected: PASS (all existing + 3 new tests)

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend && pytest -v`
Expected: PASS, all tests across the whole backend suite.

- [ ] **Step 6: Commit**

```bash
git add backend/crawl_manager.py backend/tests/test_crawl_manager.py
git commit -m "store-recommended-filter: wire judgment phase into stock sync"
```

---

### Task 9: Frontend types and API client

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/api/client.ts`

- [ ] **Step 1: Edit `types.ts`**

In `frontend/src/api/types.ts`, update the `Settings` interface:

```typescript
export interface Settings {
  discogs_token: string
  debug_screenshot_interval: number
  shuffle_crawl_order: boolean
  crawl_delay_seconds: number
  consecutive_failure_limit: number
  crawl_schedule?: string
  crawl_schedule_mode?: 'missing' | 'all'
  collection_schedule?: string
  collection_schedule_mode?: 'all' | 'new'
  ebay_app_id?: string
  ebay_cert_id?: string
  stock_schedule?: string
  anthropic_api_key?: string
}
```

Update `CrawlEvent`:

```typescript
export interface CrawlEvent {
  status?: 'found' | 'not_found' | 'error' | 'complete' | 'started' | 'stopped' | 'ping'
    | 'sync_started' | 'sync_progress' | 'sync_complete' | 'sync_error'
    | 'stock_sync_started' | 'stock_sync_progress' | 'stock_sync_complete' | 'stock_sync_error'
    | 'stock_judgment_started' | 'stock_judgment_progress' | 'stock_judgment_complete' | 'stock_judgment_error'
  discogs_id?: string
  release?: string
  artist?: string
  site?: string
  price?: number
  error?: string
  total?: number
  total_pages?: number
  page?: number
  synced?: number
  wishlist_synced?: number
  username?: string
  screenshots?: string[]
  source?: string
  judged?: number
}
```

Update `StockItem`:

```typescript
export interface StockItem {
  id: number
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
}
```

- [ ] **Step 2: Edit `client.ts`**

Update `getStock` in `frontend/src/api/client.ts`:

```typescript
export async function getStock(params: {
  search?: string
  artist?: string
  sort?: StockSortField
  order?: SortOrder
  page?: number
  per_page?: number
  overlapping?: boolean
  recommended?: boolean
}): Promise<StockResponse> {
  const q = new URLSearchParams()
  if (params.search) q.set('search', params.search)
  if (params.artist) q.set('artist', params.artist)
  if (params.sort) q.set('sort', params.sort)
  if (params.order) q.set('order', params.order)
  if (params.page) q.set('page', String(params.page))
  if (params.per_page) q.set('per_page', String(params.per_page))
  if (params.overlapping) q.set('overlapping', 'true')
  if (params.recommended) q.set('recommended', 'true')
  const r = await apiFetch(`/stock?${q}`)
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}
```

Update `getStockArtists`:

```typescript
export async function getStockArtists(overlapping?: boolean, recommended?: boolean): Promise<string[]> {
  const q = new URLSearchParams()
  if (overlapping) q.set('overlapping', 'true')
  if (recommended) q.set('recommended', 'true')
  const qs = q.toString() ? `?${q}` : ''
  const r = await apiFetch(`/stock/artists${qs}`)
  if (!r.ok) throw new Error(await r.text())
  const data = await r.json()
  return data.artists
}
```

- [ ] **Step 3: Verify types compile**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: no errors. (Existing call sites in `StockBrowser.tsx` still compile because the new params/fields are optional — they'll be filled in during Tasks 10–11.)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/types.ts frontend/src/api/client.ts
git commit -m "store-recommended-filter: add recommended/reason to stock types and API client"
```

---

### Task 10: `App.tsx` — settings-derived key gate + judgment SSE events

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/test/inStockTab.test.tsx`

- [ ] **Step 1: Write the failing tests**

In `frontend/src/test/inStockTab.test.tsx`, add `anthropic_api_key: ''` to the existing `getSettings` mock's resolved value (so the mock stays valid against the updated `Settings` type), then add two new tests inside the `describe('In Stock tab', ...)` block:

```typescript
  it('surfaces stock_judgment_progress events in the bottom status bar', async () => {
    render(<App />)
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    const source = getLastCrawlSource()
    source.emit({ status: 'stock_judgment_progress', judged: 5, total: 40 })
    await waitFor(() => expect(screen.getByText(/Judging in-stock catalog… 5\/40/)).toBeInTheDocument())
  })

  it('surfaces stock_judgment_complete events in the bottom status bar', async () => {
    render(<App />)
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    const source = getLastCrawlSource()
    source.emit({ status: 'stock_judgment_complete', judged: 12 })
    await waitFor(() => expect(screen.getByText(/Judged 12 new items for Recommended/)).toBeInTheDocument())
  })
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/test/inStockTab.test.tsx`
Expected: FAIL — the two new tests time out waiting for text that's never rendered (the SSE handler doesn't recognize these event types yet).

- [ ] **Step 3: Implement**

In `frontend/src/App.tsx`, add `getSettings` to the import from `./api/client`:

```typescript
import { refreshCollection, getCollectionStatus, openCrawlStream, getCrawlStatus, postCrawlStart, postStockSyncStart, getCrawlers, getSettings, checkHealth, getAuthState, setUnauthorizedHandler } from './api/client'
```

Add a new state variable near `crawlers`:

```typescript
  const [crawlers, setCrawlers] = useState<Crawler[]>([])
  const [hasAnthropicKey, setHasAnthropicKey] = useState(false)
```

In the health-poll effect, after `getCrawlers().then(setCrawlers).catch(() => {})`, add:

```typescript
            getSettings().then((s) => setHasAnthropicKey(Boolean(s.anthropic_api_key))).catch(() => {})
```

In the SSE `handleEvent` function, add four new branches right after the existing `stock_sync_error` branch:

```typescript
      if (event.status === 'stock_judgment_started') {
        setSyncing(true)
        setSyncMessage('Judging in-stock catalog against your collection…')
        return
      }
      if (event.status === 'stock_judgment_progress') {
        setSyncMessage(`Judging in-stock catalog… ${event.judged}/${event.total}`)
        return
      }
      if (event.status === 'stock_judgment_complete') {
        setSyncing(false)
        setSyncMessage(`Judged ${event.judged} new items for Recommended`)
        return
      }
      if (event.status === 'stock_judgment_error') {
        setSyncing(false)
        setSyncMessage(`Judgment failed: ${event.error}`)
        return
      }
```

Update the `StockBrowser` render call to pass the new prop:

```typescript
        <div className={view === 'instock' ? 'h-full' : 'hidden'}>
          <StockBrowser hasAnthropicKey={hasAnthropicKey} />
        </div>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/test/inStockTab.test.tsx`
Expected: PASS (all existing + 2 new tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.tsx frontend/src/test/inStockTab.test.tsx
git commit -m "store-recommended-filter: fetch anthropic key status and handle judgment SSE events"
```

---

### Task 11: `StockBrowser.tsx` — enable/gate Recommended, thread the param, show reason

**Files:**
- Modify: `frontend/src/views/StockBrowser.tsx`
- Modify: `frontend/src/test/stockBrowser.test.tsx`

- [ ] **Step 1: Write the failing tests**

In `frontend/src/test/stockBrowser.test.tsx`, replace the existing "defaults to All... keeps Recommended disabled" test with two tests (one per `hasAnthropicKey` value), and add filter-behavior and reason-rendering tests. Insert these in place of the old test:

```typescript
  it('defaults to All, lists options in lexicographic order, and disables Recommended without an Anthropic key', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    const select = screen.getByRole('combobox') as HTMLSelectElement
    expect(select.value).toBe('all')
    expect(Array.from(select.options).map((o) => o.text)).toEqual(['All', 'Overlapping', 'Recommended'])
    expect((screen.getByRole('option', { name: 'All' }) as HTMLOptionElement).disabled).toBe(false)
    expect((screen.getByRole('option', { name: 'Overlapping' }) as HTMLOptionElement).disabled).toBe(false)
    expect((screen.getByRole('option', { name: 'Recommended' }) as HTMLOptionElement).disabled).toBe(true)
  })

  it('enables Recommended when an Anthropic key is configured', async () => {
    render(<StockBrowser hasAnthropicKey />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect((screen.getByRole('option', { name: 'Recommended' }) as HTMLOptionElement).disabled).toBe(false)
  })

  it('filters to recommended items when Recommended is selected', async () => {
    render(<StockBrowser hasAnthropicKey />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'recommended' } })
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ recommended: true })))
  })

  it('refetches the artist sidebar scoped to recommended when Recommended is selected', async () => {
    render(<StockBrowser hasAnthropicKey />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(getStockArtists).toHaveBeenLastCalledWith(false, false)
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'recommended' } })
    await waitFor(() => expect(getStockArtists).toHaveBeenLastCalledWith(false, true))
  })

  it('restores a previously-selected Recommended filter from localStorage', async () => {
    localStorage.setItem('stockFilter', 'recommended')
    render(<StockBrowser hasAnthropicKey />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('recommended')
  })

  it('shows a recommendation reason as a tooltip on the artist and title cells', async () => {
    getStock.mockResolvedValue({
      total: 1, page: 1, per_page: 250,
      items: [{ ...items[0], reason: 'Similar to your hardcore collection' }],
    })
    render(<StockBrowser hasAnthropicKey />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(screen.getByText('Rob Zombie').closest('td')?.getAttribute('title')).toBe('Similar to your hardcore collection')
    expect(screen.getByText('The Great Satan — Ghostly Black Vinyl').getAttribute('title')).toBe('Similar to your hardcore collection')
  })
```

Also update the two existing tests that already call `getStockArtists` on overlapping to expect the new second argument, since the assertions must match the new two-argument call shape:

```typescript
  it('refetches the artist sidebar scoped to overlapping when Overlapping is selected', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(getStockArtists).toHaveBeenLastCalledWith(false, false)
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'overlapping' } })
    await waitFor(() => expect(getStockArtists).toHaveBeenLastCalledWith(true, false))
  })
```

(This replaces the existing test of the same name — the only change is the second argument in both `toHaveBeenLastCalledWith` calls.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/test/stockBrowser.test.tsx`
Expected: FAIL — `hasAnthropicKey` prop doesn't exist on `StockBrowser` yet (TS error surfaces as a runtime failure since the option stays hardcoded `disabled`); `recommended` is never passed to `getStock`/`getStockArtists`; no `title` attribute is rendered; `getStockArtists` is still called with one argument.

- [ ] **Step 3: Implement**

In `frontend/src/views/StockBrowser.tsx`, add a props interface and update the function signature:

```typescript
interface Props {
  hasAnthropicKey?: boolean
}

export default function StockBrowser({ hasAnthropicKey = false }: Props) {
```

Fix the `filter` state initializer to recognize a stored `'recommended'` value:

```typescript
  const [filter, setFilter] = useState<'all' | 'overlapping' | 'recommended'>(() => {
    const stored = localStorage.getItem('stockFilter')
    return stored === 'overlapping' || stored === 'recommended' ? stored : 'all'
  })
```

Update the `load` callback's `getStock` call to add `recommended`:

```typescript
      const result = await getStock({
        search: search || undefined,
        artist: selectedArtist || undefined,
        sort, order, page, per_page: PER_PAGE,
        overlapping: filter === 'overlapping',
        recommended: filter === 'recommended',
      })
```

Update the artist-sidebar effect:

```typescript
  useEffect(() => { getStockArtists(filter === 'overlapping', filter === 'recommended').then(setArtists) }, [filter])
```

Update the `<option>` for Recommended:

```typescript
              <option value="recommended" disabled={!hasAnthropicKey}>Recommended</option>
```

Add a `title` attribute to the tile-view artist/title text and to the two list-view table cells that render artist and title. In the table body row (list view), change:

```typescript
                  <td className="px-3 py-2 text-gray-200">{item.artist}</td>
                  <td className="px-3 py-2 text-gray-300">{item.title}</td>
```

to:

```typescript
                  <td className="px-3 py-2 text-gray-200" title={item.reason ?? undefined}>{item.artist}</td>
                  <td className="px-3 py-2 text-gray-300" title={item.reason ?? undefined}>{item.title}</td>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/test/stockBrowser.test.tsx`
Expected: PASS (all existing + new tests)

- [ ] **Step 5: Run the full frontend suite**

Run: `cd frontend && npx vitest run`
Expected: PASS, all test files (confirms `inStockTab.test.tsx` from Task 10 and `stockBrowser.test.tsx` both still pass together).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/StockBrowser.tsx frontend/src/test/stockBrowser.test.tsx
git commit -m "store-recommended-filter: gate and wire the Recommended filter in StockBrowser"
```

---

### Task 12: `Settings.tsx` — Anthropic API key field

**Files:**
- Modify: `frontend/src/views/Settings.tsx`

- [ ] **Step 1: Implement**

In `frontend/src/views/Settings.tsx`, add a row to `SETTING_ROWS`, right after the `ebay_cert_id` entry:

```typescript
  {
    key: 'anthropic_api_key',
    label: 'Anthropic API key',
    description: 'Used to judge Store items against your collection for the Recommended filter. Get one at platform.claude.com.',
    type: 'password',
    placeholder: 'sk-ant-...',
  },
```

Add the field to the default settings state:

```typescript
    ebay_app_id: '',
    ebay_cert_id: '',
    stock_schedule: '',
    anthropic_api_key: '',
  })
```

- [ ] **Step 2: Verify types compile and existing tests still pass**

Run: `cd frontend && npx tsc -b --noEmit && npx vitest run`
Expected: no type errors; full frontend suite still passes (no existing test asserts on the exact row count/order of `SETTING_ROWS`, so this addition is additive only).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/views/Settings.tsx
git commit -m "store-recommended-filter: add Anthropic API key field to Settings"
```

---

## Final Verification

- [ ] Run the full backend suite: `cd backend && pytest -v` — expect all green.
- [ ] Run the full frontend suite: `cd frontend && npx vitest run` — expect all green.
- [ ] Type-check the frontend: `cd frontend && npx tsc -b --noEmit` — expect no errors.
- [ ] Manual smoke test (Playwright-dependent / live-API paths aren't unit tested, per project convention): with a real Discogs collection synced and a real Anthropic API key entered in Settings, click "Refresh Stock Now", confirm the bottom status bar shows "Judging in-stock catalog…" then "Judged N new items for Recommended", then confirm the Store tab's "Recommended" option is selectable and returns a non-empty, plausible subset with hoverable reasons.
