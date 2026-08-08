# Store/Collection tab split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Store tab show cross-site comparison prices for stock items (collected by slice 2 but never displayed), and add a new Collection tab that's the same inventory filtered to items also in the user's Discogs collection.

**Architecture:** `get_stock_items` gains a second query + Python merge step that flattens each stock item's own row plus its `listings` comparison rows into one list, without touching pagination math. `StockBrowser.tsx` gains a `scope: 'store' | 'collection'` prop (mirroring `RecordBrowser`'s existing pattern) that forces the owned-collection filter and hides the filter dropdown; its table body renders the new multi-row shape, its tile view filters to each item's own row.

**Tech Stack:** FastAPI + psycopg (backend), React + TypeScript + Vitest (frontend), pytest.

## Global Constraints

- No API/router signature changes — `/api/stock` and `/api/stock/artists` keep their exact current query params.
- Pagination stays item-counted, not row-counted — `total`/`page`/`per_page` are unaffected by flattening.
- No placeholder/pending rows for comparisons that haven't landed yet — a row only exists once a `listings` row exists.
- No SSE/live-update work in this slice — `StockBrowser` keeps its existing fetch-on-load/filter-change pattern.
- `backend/version.py`'s `VERSION` takes the **major** bump to `"3.0"` on this PR (repo owner's explicit instruction — see Task 5), not the usual automatic minor bump.
- Every commit needs the full AI-attribution trailer block from this repo's `CLAUDE.md` (not just `ai-generated: true`) — `ai-model`, `ai-tool`, `ai-surface`, `ai-executor` are all required, created via `commit-with-cleanup.sh`, never `git commit -m`.

---

### Task 1: Backend — flatten comparison rows into `get_stock_items`

**Files:**
- Modify: `backend/db.py:934-948` (the `SELECT`/return tail of `get_stock_items`)
- Test: `backend/tests/test_stock_crud.py` (new cases after the existing `test_get_stock_items_overlapping_true_returns_only_items_matching_users_collection` at line 327)
- Test: `backend/tests/test_stock_router.py` (one new case after `test_list_stock_returns_items` at line 222)

**Interfaces:**
- Consumes: `db.upsert_stock_item_listing(conn, item_key, crawler_id, url, price, shipping, currency, condition)` (already exists, `backend/db.py:407`) and `db.compute_item_key(artist, title, url)` (already exists) — both used only in test setup here.
- Produces: `get_stock_items(...)["items"]` — each dict now additionally carries `item_key: str` and `is_own: bool`. Later tasks (frontend) rely on both fields being present on every row.

- [ ] **Step 1: Write the failing tests in `backend/tests/test_stock_crud.py`**

Add after `test_get_stock_items_overlapping_true_returns_only_items_matching_users_collection` (line 350):

```python
def test_get_stock_items_own_row_is_flagged_and_carries_item_key(admin_conn):
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
    assert len(result["items"]) == 1
    assert result["items"][0]["is_own"] is True
    assert result["items"][0]["item_key"] == db.compute_item_key("Artist A", "Album A", "https://x/1")


def test_get_stock_items_includes_comparison_rows_after_own_row(admin_conn):
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
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"])

    assert len(result["items"]) == 2
    own, comparison = result["items"]
    assert own["is_own"] is True and own["source"] == "Nuclear Blast" and own["price"] == 10.0
    assert comparison["is_own"] is False and comparison["source"] == "Amazon" and comparison["price"] == 12.5
    assert comparison["item_key"] == item_key
    assert comparison["artist"] == "Artist A" and comparison["title"] == "Album A"
    assert comparison["cover_image_url"] == own["cover_image_url"]


def test_get_stock_items_item_with_no_comparisons_returns_only_its_own_row(admin_conn):
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
    assert len(result["items"]) == 1


def test_get_stock_items_exclude_crawler_ids_also_suppresses_comparison_rows(admin_conn):
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
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], exclude_crawler_ids=[amazon_id])

    assert len(result["items"]) == 1
    assert result["items"][0]["is_own"] is True
    assert result["total"] == 1  # excluding a comparison-only crawler doesn't touch item pagination


def test_get_stock_items_comparison_row_with_null_price_is_excluded(admin_conn):
    db.register_crawler(admin_conn, "Nuclear Blast", "/x.py", crawler_type="catalog")
    db.register_crawler(admin_conn, "Amazon", "/y.py", crawler_type="release")
    admin_conn.commit()
    store_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Nuclear Blast'").fetchone()["id"]
    amazon_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    item_key = db.replace_stock_items(admin_conn, store_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
    ])[0]
    db.upsert_stock_item_listing(admin_conn, item_key, amazon_id, "https://amazon/1", None, None, "USD", None)
    admin_conn.commit()
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"])
    assert len(result["items"]) == 1


def test_get_stock_items_overlapping_true_includes_comparison_rows_for_owned_items(admin_conn):
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
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], overlapping=True)
    assert result["total"] == 1
    assert len(result["items"]) == 2
    assert {r["source"] for r in result["items"]} == {"Nuclear Blast", "Amazon"}


def test_get_stock_items_pagination_total_stays_item_counted_with_comparison_rows(admin_conn):
    db.register_crawler(admin_conn, "Nuclear Blast", "/x.py", crawler_type="catalog")
    db.register_crawler(admin_conn, "Amazon", "/y.py", crawler_type="release")
    admin_conn.commit()
    store_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Nuclear Blast'").fetchone()["id"]
    amazon_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    item_keys = db.replace_stock_items(admin_conn, store_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
        {"artist": "Artist B", "title": "Album B", "url": "https://x/2", "price": 20.0, "currency": "USD"},
    ])
    for item_key in item_keys:
        db.upsert_stock_item_listing(admin_conn, item_key, amazon_id, "https://amazon/x", 5.0, None, "USD", "New")
    admin_conn.commit()
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], per_page=2)
    assert result["total"] == 2  # 2 items, not 4 rows
    assert len(result["items"]) == 4  # but 4 rows render on this page
```

- [ ] **Step 2: Write the failing router-level test in `backend/tests/test_stock_router.py`**

Add after `test_list_stock_returns_items` (line 237):

```python
def test_list_stock_includes_comparison_rows(pg_test_db, authed_client_factory):
    store_id = _make_crawler("Nuclear Blast")
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/y.py", crawler_type="release")
        conn.commit()
        amazon_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        item_key = db.replace_stock_items(conn, store_id, [
            {"artist": "Rob Zombie", "title": "The Great Satan", "price": 31.99, "currency": "USD", "url": "https://x/1"},
        ])[0]
        db.upsert_stock_item_listing(conn, item_key, amazon_id, "https://amazon/1", 29.99, None, "USD", "New")
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()

    client = authed_client_factory(user["id"])
    r = client.get("/api/stock")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert [row["source"] for row in body["items"]] == ["Nuclear Blast", "Amazon"]
    assert body["items"][1]["is_own"] is False
```

- [ ] **Step 3: Run every new test (both files) to verify they fail**

Run: `cd backend && pytest tests/test_stock_crud.py tests/test_stock_router.py -k "comparison or own_row_is_flagged" -v`
Expected: FAIL — `KeyError: 'is_own'` on the `test_stock_crud.py` cases, and an `AssertionError` on `test_list_stock_includes_comparison_rows` (only one row, no Amazon row, since the field/join don't exist yet).

- [ ] **Step 4: Implement the flattening in `backend/db.py`**

Replace lines 934-948 (the final `SELECT`/`return` of `get_stock_items`):

```python
    rows = conn.execute(
        f"""
        SELECT s.id, s.artist, s.title, s.format, s.price, s.currency, s.url, s.cover_image_url, s.last_seen,
               s.item_key, cr.site_name AS source, j.reason AS reason
        FROM stock_items s
        JOIN crawlers cr ON cr.id = s.crawler_id
        LEFT JOIN stock_item_judgments j ON j.item_key = s.item_key AND j.user_id = %(user_id)s
        {where}
        ORDER BY CASE WHEN s.{sort_col} IS NULL THEN 1 ELSE 0 END {null_order}, s.{sort_col} {order_sql}
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        params,
    ).fetchall()

    item_keys = [r["item_key"] for r in rows]
    comparison_sql = """
        SELECT l.item_key, l.price, l.currency, l.url, l.condition, l.last_checked, cr.site_name AS source
        FROM listings l
        JOIN crawlers cr ON cr.id = l.crawler_id
        WHERE l.item_key = ANY(%(item_keys)s) AND l.price IS NOT NULL
    """
    comparison_params = {"item_keys": item_keys}
    if exclude_crawler_ids:
        comparison_sql += " AND cr.id != ALL(%(exclude_crawler_ids)s)"
        comparison_params["exclude_crawler_ids"] = exclude_crawler_ids
    comparisons_by_item: dict[str, list[dict]] = {}
    for c in conn.execute(comparison_sql, comparison_params).fetchall():
        comparisons_by_item.setdefault(c["item_key"], []).append(c)

    items = []
    for row in rows:
        r = dict(row)
        items.append({**r, "is_own": True})
        for c in comparisons_by_item.get(r["item_key"], []):
            items.append({
                "id": f"{r['item_key']}:{c['source']}",
                "item_key": r["item_key"], "artist": r["artist"], "title": r["title"],
                "format": r["format"], "cover_image_url": r["cover_image_url"],
                "price": c["price"], "currency": c["currency"], "url": c["url"],
                "source": c["source"], "reason": r["reason"], "last_seen": c["last_checked"],
                "is_own": False,
            })

    return {"total": total, "page": page, "per_page": per_page, "items": items}
```

- [ ] **Step 5: Run every new test (both files) to verify they pass**

Run: `cd backend && pytest tests/test_stock_crud.py tests/test_stock_router.py -v`
Expected: PASS — all cases in both files, including every pre-existing `test_get_stock_items_*`/`test_get_distinct_stock_artists_*`/`test_list_stock_*` case (they never set up `listings` rows, so flattening is a no-op for them: exactly one row per item, `is_own: True`, same field values as before plus the two new keys).

- [ ] **Step 6: Run the full backend suite**

Run: `cd backend && pytest`
Expected: PASS, no regressions.

- [ ] **Step 7: Commit**

```bash
git add backend/db.py backend/tests/test_stock_crud.py backend/tests/test_stock_router.py
```
Use `commit-with-cleanup.sh` per this repo's `CLAUDE.md` (full trailer block, not just `ai-generated: true`); subject: `feat: flatten cross-site comparison prices into get_stock_items`.

---

### Task 2: Frontend — `StockItem` type, `scope` prop, drop "Overlapping" from Store's dropdown

**Files:**
- Modify: `frontend/src/api/types.ts` (the `StockItem` interface)
- Modify: `frontend/src/views/StockBrowser.tsx:1-172` (props, filter type/state, dropdown JSX, `load()`)
- Test: `frontend/src/test/stockBrowser.test.tsx`

**Interfaces:**
- Consumes: nothing new from other tasks.
- Produces: `StockBrowser`'s new `scope?: 'store' | 'collection'` prop, used by Task 4 (`App.tsx`). `StockItem.item_key`/`is_own` fields, used by Task 3's rendering.

- [ ] **Step 1: Update the `StockItem` type in `frontend/src/api/types.ts`**

Replace the existing `StockItem` interface (`frontend/src/api/types.ts:112-124`):

```ts
export interface StockItem {
  id: number | string     // number (stock_items.id) for an own row;
                           // `${item_key}:${source}` string for a comparison
                           // row, which has no stock_items id of its own
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
}
```

- [ ] **Step 2: Write failing tests in `frontend/src/test/stockBrowser.test.tsx`**

The existing fixture `items` (lines 5-8) needs `item_key`/`is_own` added — every existing test that asserts on rendered content still passes once these fields exist, since none of them inspect `is_own`/`item_key` directly. Update the fixture:

```ts
const items = [
  { id: 1, item_key: 'k1', is_own: true, artist: 'Rob Zombie', title: 'The Great Satan — Ghostly Black Vinyl', format: 'Vinyl', price: 31.99, currency: 'USD', url: 'https://shop.nuclearblast.com/products/rob-zombie', cover_image_url: 'https://cdn.shopify.com/rz-black.png', source: 'Nuclear Blast', last_seen: '2026-07-05T00:00:00Z' },
  { id: 2, item_key: 'k2', is_own: true, artist: 'NAILS', title: 'Every Bridge Burning — Forest Green LP', format: 'Vinyl', price: 25.99, currency: 'USD', url: 'https://shop.nuclearblast.com/products/nails', cover_image_url: null, source: 'Nuclear Blast', last_seen: '2026-07-05T00:00:00Z' },
]
```

Then, **delete outright** these four tests, which exercise Store's now-removed "Overlapping" dropdown option and would otherwise fail permanently (the option no longer exists to select):
- `'filters to overlapping artists when Overlapping is selected'`
- `'turns the filter back off when All is selected after Overlapping'`
- `'combines search with the active Overlapping filter rather than replacing it'`
- `'refetches the artist sidebar scoped to overlapping when Overlapping is selected'`

Then **replace** these two existing tests (title and body both updated in place), since both currently assert against the old three-option dropdown or the now-invalid `'overlapping'` localStorage value — the first replaces `'defaults to All, lists options in lexicographic order, and disables Recommended when unavailable'` (line 113), the second keeps its exact title but changes body (`'persists the filter to localStorage under stockFilter and restores it on remount'`, line 247):

```ts
  it('defaults to All, lists only All/Recommended (no Overlapping), and disables Recommended when unavailable', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    const select = screen.getByRole('combobox') as HTMLSelectElement
    expect(select.value).toBe('all')
    expect(Array.from(select.options).map((o) => o.text)).toEqual(['All', 'Recommended'])
  })

  it('persists the filter to localStorage under stockFilter and restores it on remount', async () => {
    const { unmount } = render(<StockBrowser recommendedAvailable />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'recommended' } })
    await waitFor(() => expect(localStorage.getItem('stockFilter')).toBe('recommended'))
    unmount()
    render(<StockBrowser recommendedAvailable />)
    await waitFor(() => expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('recommended'))
  })

  it('scope="collection" forces overlapping and hides the filter dropdown', async () => {
    render(<StockBrowser scope="collection" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(screen.queryByRole('combobox')).toBeNull()
    expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ overlapping: true }))
  })

  it('scope="collection" forces overlapping on the artist sidebar fetch too', async () => {
    render(<StockBrowser scope="collection" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(getStockArtists).toHaveBeenCalledWith(true, false, [])
  })

  it('scope="store" (default) keeps the filter dropdown with All/Recommended', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(screen.getByRole('combobox')).toBeTruthy()
    expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ overlapping: false }))
  })
```

- [ ] **Step 3: Run the tests to verify the new/changed ones fail**

Run: `cd frontend && npx vitest run src/test/stockBrowser.test.tsx`
Expected: FAIL on the three new `scope`-related tests (`scope` prop doesn't exist yet) and possibly on the "All, Recommended" dropdown-options test (dropdown still has "Overlapping").

- [ ] **Step 4: Implement in `frontend/src/views/StockBrowser.tsx`**

Add `scope` to `Props` and the function signature (lines 6-13):

```tsx
interface Props {
  scope?: 'store' | 'collection'
  recommendedAvailable?: boolean
  hiddenCrawlerIds?: number[]
}

const NO_HIDDEN_CRAWLER_IDS: number[] = []

function StockBrowser({ scope = 'store', recommendedAvailable = false, hiddenCrawlerIds = NO_HIDDEN_CRAWLER_IDS }: Props) {
```

Change the filter type/state (line 22-25) — drop `'overlapping'`:

```tsx
  const [filter, setFilter] = useState<'all' | 'recommended'>(() => {
    const stored = localStorage.getItem('stockFilter')
    return stored === 'recommended' ? stored : 'all'
  })
```

Change `load()`'s `overlapping` derivation (line 46) to use `scope` instead of the (now-removed) `'overlapping'` filter value:

```tsx
        overlapping: scope === 'collection',
```

Change the `getStockArtists` effect (line 63) the same way:

```tsx
  useEffect(() => { getStockArtists(scope === 'collection', filter === 'recommended', hiddenCrawlerIds).then(setArtists) }, [scope, filter, hiddenCrawlerIds])
```

Add `scope` to `load`'s dependency array (line 55) since it's now read inside:

```tsx
  }, [search, selectedArtist, sort, order, page, filter, hiddenCrawlerIds, scope])
```

Replace the filter `<select>` block (lines 139-147) to hide entirely for `scope === 'collection'` and drop the `'overlapping'` option:

```tsx
            {scope === 'store' && (
              <select
                value={filter}
                onChange={(e) => { setFilter(e.target.value as 'all' | 'recommended'); setPage(1) }}
                className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:border-gray-400"
              >
                <option value="all">All</option>
                <option value="recommended" disabled={!recommendedAvailable}>Recommended</option>
              </select>
            )}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/test/stockBrowser.test.tsx`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/types.ts frontend/src/views/StockBrowser.tsx frontend/src/test/stockBrowser.test.tsx
```
Subject: `feat: add StockBrowser scope prop, drop Overlapping from Store's filter`.

---

### Task 3: Frontend — multi-row list rendering and tile-view `is_own` filtering

**Files:**
- Modify: `frontend/src/views/StockBrowser.tsx` (tile-view map, list-view `<tbody>`)
- Test: `frontend/src/test/stockBrowser.test.tsx`

**Interfaces:**
- Consumes: `StockItem.is_own`/`item_key` from Task 2.
- Produces: nothing new for later tasks — this is purely rendering.

- [ ] **Step 1: Write failing tests**

Add to `frontend/src/test/stockBrowser.test.tsx`:

```ts
  it('renders a row for every item, including comparison rows, in list view', async () => {
    getStock.mockResolvedValue({
      total: 1, page: 1, per_page: 250,
      items: [
        items[0],
        { id: 'k1:Amazon', item_key: 'k1', is_own: false, artist: 'Rob Zombie', title: 'The Great Satan — Ghostly Black Vinyl', format: 'Vinyl', price: 29.99, currency: 'USD', url: 'https://amazon/x', cover_image_url: 'https://cdn.shopify.com/rz-black.png', source: 'Amazon', last_seen: '2026-07-05T00:00:00Z', reason: null },
      ],
    })
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getAllByText('The Great Satan — Ghostly Black Vinyl').length).toBe(2))
    expect(screen.getByText('$29.99')).toBeTruthy()
    expect(screen.getByText('Amazon')).toBeTruthy()
  })

  it('shows only the own row per item in tile view, even when comparison rows are present', async () => {
    getStock.mockResolvedValue({
      total: 1, page: 1, per_page: 250,
      items: [
        items[0],
        { id: 'k1:Amazon', item_key: 'k1', is_own: false, artist: 'Rob Zombie', title: 'The Great Satan — Ghostly Black Vinyl', format: 'Vinyl', price: 29.99, currency: 'USD', url: 'https://amazon/x', cover_image_url: null, source: 'Amazon', last_seen: '2026-07-05T00:00:00Z', reason: null },
      ],
    })
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.click(screen.getByTitle('Tile view'))
    await waitFor(() => expect(screen.getAllByText('The Great Satan — Ghostly Black Vinyl').length).toBe(1))
  })
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd frontend && npx vitest run src/test/stockBrowser.test.tsx -t "own row per item in tile view"`
Expected: FAIL — tile view currently renders one tile per array entry, so it shows 2 tiles/2 matches for the title text, not 1.

(The list-view test likely already passes today, since the current `<tbody>` already maps every array entry unconditionally — that's expected; it's included here to lock the behavior in explicitly as part of this task's deliverable, not because it's expected to fail.)

- [ ] **Step 3: Implement in `frontend/src/views/StockBrowser.tsx`**

Tile view's `items.map(...)` (line 190) filters to owned rows first. Change:

```tsx
                {items.map((item) => (
```
to:
```tsx
                {items.filter((item) => item.is_own).map((item) => (
```

List view's `<tbody>` needs no filtering change (it already maps every row) — no edit needed there; this step exists only to confirm via the Step 1 list-view test that no regression was introduced.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/test/stockBrowser.test.tsx`
Expected: PASS, full file.

- [ ] **Step 5: Run the full frontend test suite**

Run: `cd frontend && npx vitest run`
Expected: PASS, no regressions elsewhere.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/StockBrowser.tsx frontend/src/test/stockBrowser.test.tsx
```
Subject: `feat: render comparison-price rows in Store's list view, keep tiles to one per item`.

---

### Task 4: Frontend — App.tsx Collection tab

**Files:**
- Modify: `frontend/src/App.tsx:14` (`View` union), `:451-457` (nav buttons), `:503-505` (render blocks)
- Test: `frontend/src/test/inStockTab.test.tsx`

**Interfaces:**
- Consumes: `StockBrowser`'s `scope` prop from Task 2.
- Produces: nothing further downstream — this is the last wiring task.

- [ ] **Step 1: Write the failing test in `frontend/src/test/inStockTab.test.tsx`**

First, extract `getStock` to a named mock so its call args can be asserted (it's currently inlined in the `vi.mock` factory at line 54). Change line 54 and add a top-level const near the other extracted mocks (e.g. after `const getCrawlers = vi.fn()` at line 27):

```ts
const getCrawlers = vi.fn()
const getStock = vi.fn()
```

Change the mock factory's `getStock` entry (line 54):

```ts
  getStock: (...args: unknown[]) => getStock(...args),
```

Add to `beforeEach` (after `getCrawlers.mockResolvedValue([])` at line 86):

```ts
  getStock.mockResolvedValue({ total: 0, page: 1, per_page: 250, items: [] })
```

Add a new test in the `describe('In Stock tab', ...)` block, after `'shows a Store nav button that switches views'` (line 96):

```ts
  it('shows a Collection nav button that switches to a collection-scoped StockBrowser', async () => {
    render(<App />)
    await waitFor(() => expect(screen.getByText('Collection')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Collection'))
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ overlapping: true })))
  })
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd frontend && npx vitest run src/test/inStockTab.test.tsx -t "Collection nav button"`
Expected: FAIL — no "Collection" text exists in the app yet.

- [ ] **Step 3: Implement in `frontend/src/App.tsx`**

`View` union (line 14):

```tsx
type View = 'discogs' | 'wishlist' | 'instock' | 'collection' | 'settings' | 'logs' | 'account'
```

New nav button, inserted after the Store button (`frontend/src/App.tsx:451-456`, right before the closing `</nav>` at line 457):

```tsx
          <button
            onClick={() => setView('collection')}
            className={`px-3 py-1.5 text-sm font-medium ${navButtonClass(view === 'collection')}`}
          >
            Collection
          </button>
```

New render block, inserted after the `instock` block (`frontend/src/App.tsx:503-505`):

```tsx
        <div className={view === 'collection' ? 'h-full' : 'hidden'}>
          <StockBrowser scope="collection" hiddenCrawlerIds={hiddenCrawlerIds} />
        </div>
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend && npx vitest run src/test/inStockTab.test.tsx`
Expected: PASS, full file.

- [ ] **Step 5: Run the full frontend suite**

Run: `cd frontend && npx vitest run`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/App.tsx frontend/src/test/inStockTab.test.tsx
```
Subject: `feat: add Collection nav tab, wired to StockBrowser's collection scope`.

---

### Task 5: Version bump, spec-drift check, and PR

**Files:**
- Modify: `backend/version.py`
- Read-only check: every file under `docs/superpowers/specs/` and `docs/specifications/shaping/`

**Interfaces:**
- Consumes: nothing (final task).
- Produces: nothing (final task).

- [ ] **Step 1: Bump the version**

`backend/version.py` currently reads `VERSION = "2.14"`. Per the repo owner's explicit instruction (recorded in the design spec's "Decisions carried from brainstorming"), this is a **major** bump, not the usual automatic minor one:

```python
VERSION = "3.0"
```

- [ ] **Step 2: Run the pre-PR spec-drift check required by this repo's `CLAUDE.md`**

```bash
grep -rl "StockBrowser\|get_stock_items\|Overlapping\|instock\|Store tab\|stock_items" docs/superpowers/specs/ docs/specifications/shaping/
```

For each file that matches, read it and confirm it still describes what actually shipped on this branch. In particular, check whether `docs/superpowers/specs/2026-06-27-discogs-browser-design.md` (the original architecture spec) describes the Store tab's old single-row-per-item shape or the "Overlapping" filter anywhere — if so, amend it with a short inline correction (not a rewrite) noting the new multi-row/Collection-tab shape, as its own commit on this branch.

- [ ] **Step 3: Run both full test suites one last time**

Run: `cd backend && pytest && cd ../frontend && npx vitest run`
Expected: PASS, both.

- [ ] **Step 4: Commit the version bump (and spec-drift fix commit, if Step 2 found any drift, as a separate commit before this one)**

```bash
git add backend/version.py
```
Subject: `chore: bump version to 3.0`.

- [ ] **Step 5: Push and open the PR**

Follow this repo's `sdlc:pr-review-prep` skill — ready-for-review (not draft), conventional-commits title, `Summary:`/`Actions:` body noting what spec-drift check found (or that none was found), and the `ai-assisted` label per commit trailers.
