# Collection tab "Price" column Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename `StockBrowser`'s `Price` column to `Cost` in both Store and Collection, and add a new `Price` column to the Collection tab only, showing the matched Discogs collection item's custom-field "price paid" value (`catalog.discogs_price`) already surfaced on the Discogs tab.

**Architecture:** `get_stock_items` gains a correlated scalar subquery reusing `_not_owned_clause`'s fuzzy artist/title match (extracted into a shared `_owned_match_fragment` helper so the two can't drift), selecting the matched `catalog.discogs_price` onto every own row; comparison rows copy it from their own row, same as `cover_image_url`. Sorting by it uses a best-effort numeric extraction, special-cased ahead of the existing `_STOCK_ALLOWED_SORT` fallback so it degrades to `artist`-order rather than erroring when requested outside Collection scope. `StockBrowser.tsx` renders the new column only when `scope === 'collection'`.

**Tech Stack:** FastAPI + psycopg (backend), React + TypeScript + Vitest (frontend), pytest.

## Global Constraints

- No API/router signature changes — `discogs_price` rides along as a new field on the existing `/api/stock` response; `sort` already accepts arbitrary strings, validated inside `get_stock_items` itself.
- `_not_owned_clause`'s matching semantics (fuzzy artist/title against `catalog`) are reused exactly as-is via the extracted `_owned_match_fragment` — not revisited or changed.
- `discogs_price` sorting must never error the query, regardless of what free text is stored — unparseable values sort last, never raise.
- This branch is stacked on `worktree-store-collection-split` (PR #75, not yet merged) — its version bump (`backend/version.py` → `"3.0"`) is already on this branch; this plan's own bump is a normal automatic minor bump on top of that (`"3.0"` → `"3.1"`), not a major one.
- Every commit needs the full AI-attribution trailer block from this repo's `CLAUDE.md` (`ai-generated`, `ai-model`, `ai-tool`, `ai-surface`, `ai-executor`), created via `commit-with-cleanup.sh`, never `git commit -m`.

---

### Task 1: Backend — matched `discogs_price` on `get_stock_items`, with fallback-safe sorting

**Files:**
- Modify: `backend/db.py:873-885` (extract `_owned_match_fragment` out of `_not_owned_clause`)
- Modify: `backend/db.py:891-946` (`get_stock_items`'s sort-expr computation and main `SELECT`)
- Modify: `backend/db.py:964-976` (comparison-row flatten step)
- Test: `backend/tests/test_stock_crud.py` (new cases after `test_get_stock_items_pagination_total_stays_item_counted_with_comparison_rows`)
- Test: `backend/tests/test_stock_router.py` (one new case after `test_list_stock_includes_comparison_rows`, line 259)

**Interfaces:**
- Consumes: `db.upsert_catalog_release(conn, data)` and `db.upsert_library_item(conn, user_id, discogs_id, in_collection)` (both already exist), used only in test setup here.
- Produces: `get_stock_items(...)["items"]` — each dict now additionally carries `discogs_price: str | None`. Task 2/3 (frontend) rely on this field being present on every row, own and comparison.

- [ ] **Step 1: Write the failing tests in `backend/tests/test_stock_crud.py`**

Add after `test_get_stock_items_pagination_total_stays_item_counted_with_comparison_rows`:

```python
def test_get_stock_items_returns_matched_discogs_price_for_owned_item(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
    ])
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "Artist A", "title": "Album A", "year": None, "label": None,
        "format": None, "discogs_price": "25.00", "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], overlapping=True)
    assert result["items"][0]["discogs_price"] == "25.00"


def test_get_stock_items_discogs_price_is_none_when_unmatched(admin_conn):
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
    ])
    admin_conn.commit()
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"])
    assert result["items"][0]["discogs_price"] is None


def test_get_stock_items_comparison_rows_carry_owns_discogs_price(admin_conn):
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
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "Artist A", "title": "Album A", "year": None, "label": None,
        "format": None, "discogs_price": "25.00", "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], overlapping=True)
    assert len(result["items"]) == 2
    assert result["items"][0]["discogs_price"] == "25.00"
    assert result["items"][1]["discogs_price"] == "25.00"


def test_get_stock_items_sort_by_discogs_price_orders_numerically_nulls_last(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
        {"artist": "Artist B", "title": "Album B", "url": "https://x/2", "price": 10.0, "currency": "USD"},
        {"artist": "Artist C", "title": "Album C", "url": "https://x/3", "price": 10.0, "currency": "USD"},
    ])
    for discogs_id, artist, title, price in [
        ("r1", "Artist A", "Album A", "$30.00"),
        ("r2", "Artist B", "Album B", "10"),
        ("r3", "Artist C", "Album C", "N/A"),
    ]:
        db.upsert_catalog_release(admin_conn, {
            "discogs_id": discogs_id, "artist": artist, "title": title, "year": None, "label": None,
            "format": None, "discogs_price": price, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
    for discogs_id in ("r1", "r2", "r3"):
        db.upsert_library_item(admin_conn, alice["id"], discogs_id, in_collection=True)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], overlapping=True, sort="discogs_price", order="asc")
    assert [r["artist"] for r in result["items"]] == ["Artist B", "Artist A", "Artist C"]


def test_get_stock_items_sort_by_discogs_price_falls_back_to_artist_when_not_overlapping(admin_conn):
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Bravo", "title": "Album B", "url": "https://x/2", "price": 10.0, "currency": "USD"},
        {"artist": "Alpha", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
    ])
    admin_conn.commit()
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], overlapping=False, sort="discogs_price", order="asc")
    assert [r["artist"] for r in result["items"]] == ["Alpha", "Bravo"]
```

- [ ] **Step 2: Write the failing router-level test in `backend/tests/test_stock_router.py`**

Add after `test_list_stock_includes_comparison_rows` (line 259):

```python
def test_list_stock_includes_discogs_price_for_matched_collection_item(pg_test_db, authed_client_factory):
    store_id = _make_crawler("Nuclear Blast")
    with db.get_admin_pool().connection() as conn:
        db.replace_stock_items(conn, store_id, [
            {"artist": "Rob Zombie", "title": "The Great Satan", "price": 31.99, "currency": "USD", "url": "https://x/1"},
        ])
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "Rob Zombie", "title": "The Great Satan", "year": None, "label": None,
            "format": None, "discogs_price": "20.00", "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.upsert_library_item(conn, user["id"], "r1", in_collection=True)
        conn.commit()

    client = authed_client_factory(user["id"])
    r = client.get("/api/stock", params={"overlapping": "true"})
    assert r.status_code == 200
    body = r.json()
    assert body["items"][0]["discogs_price"] == "20.00"
```

- [ ] **Step 3: Run every new test (both files) to verify they fail**

Run: `cd backend && pytest tests/test_stock_crud.py tests/test_stock_router.py -k "discogs_price" -v`
Expected: FAIL — `KeyError: 'discogs_price'` on every `test_stock_crud.py` case; `KeyError`/`AssertionError` on the router case (field doesn't exist in the response yet).

- [ ] **Step 4: Extract `_owned_match_fragment` from `_not_owned_clause` in `backend/db.py`**

Replace lines 873-885:

```python
def _owned_match_fragment(user_id_param: str) -> str:
    # Exact-or-prefix-with-space title match, not exact-only: stock listings
    # often append edition/format qualifiers the catalog title doesn't have
    # (e.g. catalog "Kid A" vs. stock listing "Kid A (Deluxe Reissue)"), so a
    # strict equality would treat an already-owned release as still unowned.
    return f"""FROM library_items li
        JOIN catalog c ON c.discogs_id = li.discogs_id
        WHERE li.user_id = {user_id_param}
          AND li.in_collection = TRUE
          AND LOWER(c.artist) = LOWER(s.artist)
          AND (LOWER(s.title) = LOWER(c.title) OR LOWER(s.title) LIKE LOWER(c.title) || ' %%')"""


def _not_owned_clause(user_id_param: str) -> str:
    return f"NOT EXISTS (SELECT 1 {_owned_match_fragment(user_id_param)})"
```

- [ ] **Step 5: Add the matched-price subquery and fallback-safe sort handling in `get_stock_items`**

Replace lines 904-905 (`order_sql`/`sort_col` computation):

```python
    order_sql = "DESC" if order.lower() == "desc" else "ASC"
    if sort == "discogs_price" and overlapping:
        sort_expr = """(SELECT (regexp_match(c.discogs_price, '\\d+\\.?\\d*'))[1]::numeric
                        {match} LIMIT 1)""".format(match=_owned_match_fragment("%(user_id)s"))
    else:
        sort_col = sort if sort in _STOCK_ALLOWED_SORT else "artist"
        sort_expr = f"s.{sort_col}"
```

Replace the main `SELECT`/`ORDER BY` block (lines 934-946):

```python
    rows = conn.execute(
        f"""
        SELECT s.id, s.artist, s.title, s.format, s.price, s.currency, s.url, s.cover_image_url, s.last_seen,
               s.item_key, cr.site_name AS source, j.reason AS reason,
               (SELECT c.discogs_price {_owned_match_fragment('%(user_id)s')} LIMIT 1) AS discogs_price
        FROM stock_items s
        JOIN crawlers cr ON cr.id = s.crawler_id
        LEFT JOIN stock_item_judgments j ON j.item_key = s.item_key AND j.user_id = %(user_id)s
        {where}
        ORDER BY CASE WHEN {sort_expr} IS NULL THEN 1 ELSE 0 END {null_order}, {sort_expr} {order_sql}
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        params,
    ).fetchall()
```

- [ ] **Step 6: Copy `discogs_price` onto comparison rows in the flatten step**

In the `items.append({...})` for comparison rows (line 969-976), add `"discogs_price": r["discogs_price"],` alongside the existing `"cover_image_url": r["cover_image_url"],`:

```python
            items.append({
                "id": f"{r['id']}:{c['source']}",
                "item_key": r["item_key"], "artist": r["artist"], "title": r["title"],
                "format": r["format"], "cover_image_url": r["cover_image_url"],
                "discogs_price": r["discogs_price"],
                "price": c["price"], "currency": c["currency"], "url": c["url"],
                "source": c["source"], "reason": r["reason"], "last_seen": c["last_checked"],
                "is_own": False,
            })
```

(The own-row branch, `items.append({**r, "is_own": True})`, already carries `discogs_price` for free — it's part of `r`, the dict-ified row.)

- [ ] **Step 7: Run every new test (both files) to verify they pass**

Run: `cd backend && pytest tests/test_stock_crud.py tests/test_stock_router.py -v`
Expected: PASS — all cases in both files, including every pre-existing `test_get_stock_items_*`/`test_list_stock_*` case (none of them inspect `discogs_price`, so its addition is a no-op for them).

- [ ] **Step 8: Run the full backend suite**

Run: `cd backend && pytest`
Expected: PASS, no regressions.

- [ ] **Step 9: Commit**

```bash
git add backend/db.py backend/tests/test_stock_crud.py backend/tests/test_stock_router.py
```
Subject: `feat: surface matched Discogs custom-field price on get_stock_items`.

---

### Task 2: Frontend — `StockItem`/`StockSortField` types

**Files:**
- Modify: `frontend/src/api/types.ts:112-135`

**Interfaces:**
- Consumes: nothing new from other tasks.
- Produces: `StockItem.discogs_price`, `StockSortField`'s new `'discogs_price'` member — both used by Task 3.

- [ ] **Step 1: Add `discogs_price` to `StockItem` (line 112-126)**

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
}
```

- [ ] **Step 2: Add `'discogs_price'` to `StockSortField` (line 135)**

```ts
export type StockSortField = 'artist' | 'title' | 'format' | 'price' | 'discogs_price'
```

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS (no consumer of `StockItem`/`StockSortField` breaks — both changes are additive).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/types.ts
```
Subject: `feat: add discogs_price to StockItem/StockSortField types`.

---

### Task 3: Frontend — rename `Price` to `Cost`, add Collection-only `Price` column

**Files:**
- Modify: `frontend/src/views/StockBrowser.tsx:240-282` (list-view header + row cells)
- Test: `frontend/src/test/stockBrowser.test.tsx`

**Interfaces:**
- Consumes: `StockItem.discogs_price`, `StockSortField` from Task 2.
- Produces: nothing further downstream — this is the last task before version bump/PR.

- [ ] **Step 1: Update the shared fixture and the sort-toggle test's label in `frontend/src/test/stockBrowser.test.tsx`**

The fixture (lines 5-8) needs `discogs_price` added — one `null` (to exercise the `—` fallback) and one real value (to exercise the rendered value):

```ts
const items = [
  { id: 1, item_key: 'k1', is_own: true, artist: 'Rob Zombie', title: 'The Great Satan — Ghostly Black Vinyl', format: 'Vinyl', price: 31.99, currency: 'USD', url: 'https://shop.nuclearblast.com/products/rob-zombie', cover_image_url: 'https://cdn.shopify.com/rz-black.png', source: 'Nuclear Blast', last_seen: '2026-07-05T00:00:00Z', discogs_price: null },
  { id: 2, item_key: 'k2', is_own: true, artist: 'NAILS', title: 'Every Bridge Burning — Forest Green LP', format: 'Vinyl', price: 25.99, currency: 'USD', url: 'https://shop.nuclearblast.com/products/nails', cover_image_url: null, source: 'Nuclear Blast', last_seen: '2026-07-05T00:00:00Z', discogs_price: '42.50' },
]
```

`'toggles sort order when a column header is clicked twice'` (lines 69-76) clicks the renamed header by its old label — update it to `/Cost/` (the `sort`/API param name it asserts on, `'price'`, is unchanged — only the visible label moves):

```ts
  it('toggles sort order when a column header is clicked twice', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.click(screen.getByText(/Cost/))
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ sort: 'price', order: 'asc' })))
    fireEvent.click(screen.getByText(/Cost/))
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ sort: 'price', order: 'desc' })))
  })
```

- [ ] **Step 2: Write the new failing tests**

Add after `'scope="store" (default) keeps the filter dropdown with All/Recommended'` (line 236-241):

```ts
  it('does not render a Price column in Store scope', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(screen.queryByText(/Price/)).toBeNull()
  })

  it('renders a Price column in Collection scope showing the matched discogs_price, or — when missing', async () => {
    render(<StockBrowser scope="collection" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(screen.getByText(/Price/)).toBeTruthy()
    expect(screen.getByText('—')).toBeTruthy()
    expect(screen.getByText('42.50')).toBeTruthy()
  })

  it('sorts by discogs_price when the Price column header is clicked in Collection scope', async () => {
    render(<StockBrowser scope="collection" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.click(screen.getByText(/Price/))
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ sort: 'discogs_price', order: 'asc' })))
  })
```

- [ ] **Step 3: Run the tests to verify the new/changed ones fail**

Run: `cd frontend && npx vitest run src/test/stockBrowser.test.tsx`
Expected: FAIL — the renamed-header test can't find `/Cost/` yet (header still says "Price"); the three new tests fail (no `Price` column exists at all yet, in either scope).

- [ ] **Step 4: Implement in `frontend/src/views/StockBrowser.tsx`**

Rename the existing header (lines 240-244):

```tsx
                <th className="text-center" aria-sort={sort === 'price' ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}>
                  <button type="button" onClick={() => toggleSort('price')} className={`${sortButtonClass} text-center`}>
                    Cost {sort === 'price' ? (order === 'asc' ? '↑' : '↓') : ''}
                  </button>
                </th>
                {scope === 'collection' && (
                  <th className="text-center" aria-sort={sort === 'discogs_price' ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}>
                    <button type="button" onClick={() => toggleSort('discogs_price')} className={`${sortButtonClass} text-center`}>
                      Price {sort === 'discogs_price' ? (order === 'asc' ? '↑' : '↓') : ''}
                    </button>
                  </th>
                )}
```

Update `colSpan` on the loading/empty rows (currently hardcoded `6` at lines 250 and 258) to account for the extra column in Collection scope:

```tsx
              {loading && (
                <tr><td colSpan={scope === 'collection' ? 7 : 6} className="py-8 text-gray-500">
                  <div className="flex items-center justify-center gap-2">
                    <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                    Loading…
                  </div>
                </td></tr>
              )}
              {!loading && items.length === 0 && (
                <tr><td colSpan={scope === 'collection' ? 7 : 6} className="text-center py-8 text-gray-500">No in-stock items yet. Click "Refresh Stock Now" in Settings.</td></tr>
              )}
```

Add the new cell after the existing `Cost` cell (the `<a href={item.url} ...>` block at lines 276-280):

```tsx
                  <td className="px-3 py-2">
                    <a href={item.url} target="_blank" rel="noreferrer" className="text-green-400 hover:text-green-300 font-medium">
                      {item.price != null ? `$${item.price.toFixed(2)}` : 'View'}
                    </a>
                  </td>
                  {scope === 'collection' && (
                    <td className="px-3 py-2 text-gray-400">{item.discogs_price ?? '—'}</td>
                  )}
                  <td className="px-3 py-2 text-gray-400">{item.source}</td>
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/test/stockBrowser.test.tsx`
Expected: PASS, full file.

- [ ] **Step 6: Run the full frontend test suite**

Run: `cd frontend && npx vitest run`
Expected: PASS, no regressions.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/views/StockBrowser.tsx frontend/src/test/stockBrowser.test.tsx
```
Subject: `feat: rename Store/Collection Price column to Cost, add Collection-only Price column`.

---

### Task 4: Version bump, spec-drift check, and PR

**Files:**
- Modify: `backend/version.py`
- Read-only check: every file under `docs/superpowers/specs/` and `docs/specifications/shaping/`

**Interfaces:**
- Consumes: nothing (final task).
- Produces: nothing (final task).

- [ ] **Step 1: Bump the version**

`backend/version.py` currently reads `VERSION = "3.0"` (inherited from the stacked `worktree-store-collection-split` branch). This is a normal automatic minor bump, not major:

```python
VERSION = "3.1"
```

- [ ] **Step 2: Run the pre-PR spec-drift check required by this repo's `CLAUDE.md`**

```bash
grep -rl "StockBrowser\|Price\|Cost\|discogs_price\|get_stock_items" docs/superpowers/specs/ docs/specifications/shaping/
```

For each file that matches, read it and confirm it still describes what actually shipped on this branch — in particular, check whether `docs/specifications/shaping/2026-08-08-store-collection-split-design.md` (this branch's parent slice) or any file under `docs/superpowers/specs/` still describes `StockBrowser`'s price column as `Price` rather than `Cost`. If so, amend with a short inline correction (not a rewrite), as its own commit on this branch.

- [ ] **Step 3: Run both full test suites one last time**

Run: `cd backend && pytest && cd ../frontend && npx vitest run`
Expected: PASS, both.

- [ ] **Step 4: Commit the version bump (and spec-drift fix commit, if Step 2 found any drift, as a separate commit before this one)**

```bash
git add backend/version.py
```
Subject: `chore: bump version to 3.1`.

- [ ] **Step 5: Push and open the PR**

Follow this repo's `sdlc:pr-review-prep` skill — ready-for-review (not draft), conventional-commits title, `Summary:`/`Actions:` body noting what spec-drift check found (or that none was found). Base the PR on `worktree-store-collection-split` (this branch's actual parent), not `main`, since PR #75 hasn't merged yet — retarget to `main` later if #75 merges first.
