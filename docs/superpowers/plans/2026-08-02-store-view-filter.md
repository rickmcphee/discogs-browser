# Store View Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every user a Settings page with a personal "which stores do I want to see" filter, and split the admin's existing single Enabled/Disabled crawler toggle into independent View (personal, everyone) and Crawl (unchanged, admin-only) columns.

**Architecture:** The View preference is a `localStorage`-backed array of hidden crawler ids, lifted to `App.tsx` state and passed down as a prop. Collections/Wishlist filtering is purely client-side (an extra filter on the existing column list). The Store tab's filtering must reach the backend, since `getStock`/`getStockArtists` are paginated — the hidden ids are sent as a new `hidden_crawler_ids` query param and excluded via SQL.

**Tech Stack:** React + TypeScript (Vite), Tailwind CSS, Vitest + Testing Library, FastAPI, psycopg (Postgres), pytest.

Full design: [`docs/superpowers/specs/2026-08-02-store-view-filter-design.md`](../specs/2026-08-02-store-view-filter-design.md).

---

### Task 1: `db.py` — exclude hidden crawler ids from stock queries

**Files:**
- Modify: `backend/db.py:743-812` (`get_stock_items`, `get_distinct_stock_artists`)
- Test: `backend/tests/test_stock_crud.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_stock_crud.py`, after `test_get_stock_items_recommended_filters_to_calling_users_judgments` (after line 55):

```python
def test_get_stock_items_excludes_hidden_crawler_ids(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    db.register_crawler(admin_conn, "Nuclear Blast", "/y.py", crawler_type="catalog")
    admin_conn.commit()
    amazon_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    nb_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Nuclear Blast'").fetchone()["id"]
    db.replace_stock_items(admin_conn, amazon_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
    ])
    db.replace_stock_items(admin_conn, nb_id, [
        {"artist": "Artist B", "title": "Album B", "url": "https://x/2", "price": 20.0, "currency": "USD"},
    ])
    admin_conn.commit()

    result = db.get_stock_items(admin_conn, alice["id"], exclude_crawler_ids=[amazon_id])
    assert [i["artist"] for i in result["items"]] == ["Artist B"]
    assert result["total"] == 1


def test_get_stock_items_exclude_crawler_ids_empty_list_excludes_nothing(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    amazon_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    db.replace_stock_items(admin_conn, amazon_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
    ])
    admin_conn.commit()

    result = db.get_stock_items(admin_conn, alice["id"], exclude_crawler_ids=[])
    assert result["total"] == 1


def test_get_distinct_stock_artists_excludes_hidden_crawler_ids(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    db.register_crawler(admin_conn, "Nuclear Blast", "/y.py", crawler_type="catalog")
    admin_conn.commit()
    amazon_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    nb_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Nuclear Blast'").fetchone()["id"]
    db.replace_stock_items(admin_conn, amazon_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
    ])
    db.replace_stock_items(admin_conn, nb_id, [
        {"artist": "Artist B", "title": "Album B", "url": "https://x/2", "price": 20.0, "currency": "USD"},
    ])
    admin_conn.commit()

    artists = db.get_distinct_stock_artists(admin_conn, alice["id"], exclude_crawler_ids=[amazon_id])
    assert artists == ["Artist B"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test .venv/bin/pytest tests/test_stock_crud.py -k exclude -v`
Expected: FAIL — `get_stock_items()`/`get_distinct_stock_artists()` got an unexpected keyword argument `exclude_crawler_ids`.

- [ ] **Step 3: Add the `exclude_crawler_ids` param**

In `backend/db.py`, replace the `get_stock_items` signature and its `conditions` setup (lines 743-774):

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
    overlapping: bool = False,
    recommended: bool = False,
    exclude_crawler_ids: Optional[list[int]] = None,
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
    if exclude_crawler_ids:
        conditions.append("s.crawler_id != ALL(%(exclude_crawler_ids)s)")
        params["exclude_crawler_ids"] = exclude_crawler_ids
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
```

Replace `get_distinct_stock_artists` (lines 799-812):

```python
def get_distinct_stock_artists(
    conn, user_id: int, overlapping: bool = False, recommended: bool = False,
    exclude_crawler_ids: Optional[list[int]] = None,
) -> list[str]:
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
    if exclude_crawler_ids:
        conditions.append("s.crawler_id != ALL(%(exclude_crawler_ids)s)")
        params["exclude_crawler_ids"] = exclude_crawler_ids
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = conn.execute(f"SELECT DISTINCT s.artist FROM stock_items s {where} ORDER BY s.artist", params).fetchall()
    return [row["artist"] for row in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test .venv/bin/pytest tests/test_stock_crud.py -v`
Expected: PASS (all cases, including the 3 new ones).

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test .venv/bin/pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add backend/db.py backend/tests/test_stock_crud.py
git commit -m "feat: db.py get_stock_items/get_distinct_stock_artists accept exclude_crawler_ids"
```

---

### Task 2: `routers/stock.py` — accept `hidden_crawler_ids` query param

**Files:**
- Modify: `backend/routers/stock.py:11-34` (`list_stock`, `list_stock_artists`)
- Test: `backend/tests/test_stock_router.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_stock_router.py`, after `test_list_stock_search_and_artist_params` (after line 212):

```python
def test_list_stock_excludes_hidden_crawler_ids(pg_test_db, authed_client_factory):
    amazon_id = _make_crawler("Amazon")
    nb_id = _make_crawler("Nuclear Blast")
    with db.get_admin_pool().connection() as conn:
        db.replace_stock_items(conn, amazon_id, [
            {"artist": "Artist A", "title": "Album A", "price": 10.0, "currency": "USD", "url": "https://x/1"},
        ])
        db.replace_stock_items(conn, nb_id, [
            {"artist": "Artist B", "title": "Album B", "price": 20.0, "currency": "USD", "url": "https://x/2"},
        ])
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()

    client = authed_client_factory(user["id"])
    r = client.get(f"/api/stock?hidden_crawler_ids={amazon_id}")
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["artist"] == "Artist B"
```

Add after `test_list_stock_artists_endpoint` (after line 228):

```python
def test_list_stock_artists_excludes_hidden_crawler_ids(pg_test_db, authed_client_factory):
    amazon_id = _make_crawler("Amazon")
    nb_id = _make_crawler("Nuclear Blast")
    with db.get_admin_pool().connection() as conn:
        db.replace_stock_items(conn, amazon_id, [
            {"artist": "Artist A", "title": "Album A", "price": 10.0, "currency": "USD", "url": "https://x/1"},
        ])
        db.replace_stock_items(conn, nb_id, [
            {"artist": "Artist B", "title": "Album B", "price": 20.0, "currency": "USD", "url": "https://x/2"},
        ])
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()

    client = authed_client_factory(user["id"])
    r = client.get(f"/api/stock/artists?hidden_crawler_ids={amazon_id},{nb_id}")
    assert r.json()["artists"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test .venv/bin/pytest tests/test_stock_router.py -k hidden_crawler_ids -v`
Expected: FAIL — `total` is 2, not 1 (param is currently ignored).

- [ ] **Step 3: Accept and parse the query param**

In `backend/routers/stock.py`, replace `list_stock` and `list_stock_artists` (lines 11-34):

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
    overlapping: bool = Query(False),
    recommended: bool = Query(False),
    hidden_crawler_ids: Optional[str] = Query(None),
):
    user_id = request.state.user_id
    exclude_crawler_ids = _parse_crawler_ids(hidden_crawler_ids)
    with db.user_scope(user_id) as conn:
        return db.get_stock_items(
            conn, user_id, search=search, artist=artist, sort=sort, order=order,
            page=page, per_page=per_page, overlapping=overlapping, recommended=recommended,
            exclude_crawler_ids=exclude_crawler_ids,
        )


@router.get("/stock/artists")
def list_stock_artists(
    request: Request, overlapping: bool = Query(False), recommended: bool = Query(False),
    hidden_crawler_ids: Optional[str] = Query(None),
):
    user_id = request.state.user_id
    exclude_crawler_ids = _parse_crawler_ids(hidden_crawler_ids)
    with db.user_scope(user_id) as conn:
        return {"artists": db.get_distinct_stock_artists(
            conn, user_id, overlapping=overlapping, recommended=recommended,
            exclude_crawler_ids=exclude_crawler_ids,
        )}
```

Add the parsing helper above `list_stock` (after the `router = APIRouter()` line):

```python
def _parse_crawler_ids(raw: Optional[str]) -> Optional[list[int]]:
    if not raw:
        return None
    return [int(x) for x in raw.split(",") if x]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test .venv/bin/pytest tests/test_stock_router.py -v`
Expected: PASS (all cases, including the 2 new ones).

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test .venv/bin/pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add backend/routers/stock.py backend/tests/test_stock_router.py
git commit -m "feat: accept hidden_crawler_ids query param on /stock and /stock/artists"
```

---

### Task 3: `client.ts` — thread hidden crawler ids into stock API calls

**Files:**
- Modify: `frontend/src/api/client.ts:142-175` (`getStock`, `getStockArtists`)
- Test: `frontend/src/test/client.test.ts`

- [ ] **Step 1: Write the failing tests**

Replace the import line at the top of `frontend/src/test/client.test.ts` (line 2):

```ts
import { postCrawlStart, getUserSettings, saveUserSettings, getStock, getStockArtists } from '../api/client'
```

Add inside the existing `describe('crawl/user-settings client functions', ...)` block, after the `saveUserSettings` test (after line 29):

```ts
  it('getStock includes hidden_crawler_ids when provided', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ total: 0, page: 1, per_page: 250, items: [] }) })
    await getStock({ hiddenCrawlerIds: [3, 7] })
    expect(fetchMock.mock.calls[0][0]).toContain('hidden_crawler_ids=3%2C7')
  })

  it('getStock omits hidden_crawler_ids when the list is empty', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ total: 0, page: 1, per_page: 250, items: [] }) })
    await getStock({ hiddenCrawlerIds: [] })
    expect(fetchMock.mock.calls[0][0]).not.toContain('hidden_crawler_ids')
  })

  it('getStockArtists includes hidden_crawler_ids when provided', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ artists: [] }) })
    await getStockArtists(false, false, [3, 7])
    expect(fetchMock.mock.calls[0][0]).toContain('hidden_crawler_ids=3%2C7')
  })
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/test/client.test.ts`
Expected: FAIL — `hiddenCrawlerIds` isn't a recognized param on `getStock`'s type, and the query string never contains `hidden_crawler_ids`.

- [ ] **Step 3: Add `hiddenCrawlerIds` to both functions**

In `frontend/src/api/client.ts`, replace `getStock` (lines 142-164):

```ts
export async function getStock(params: {
  search?: string
  artist?: string
  sort?: StockSortField
  order?: SortOrder
  page?: number
  per_page?: number
  overlapping?: boolean
  recommended?: boolean
  hiddenCrawlerIds?: number[]
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
  if (params.hiddenCrawlerIds?.length) q.set('hidden_crawler_ids', params.hiddenCrawlerIds.join(','))
  const r = await apiFetch(`/stock?${q}`)
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}
```

Replace `getStockArtists` (lines 166-175):

```ts
export async function getStockArtists(overlapping?: boolean, recommended?: boolean, hiddenCrawlerIds?: number[]): Promise<string[]> {
  const q = new URLSearchParams()
  if (overlapping) q.set('overlapping', 'true')
  if (recommended) q.set('recommended', 'true')
  if (hiddenCrawlerIds?.length) q.set('hidden_crawler_ids', hiddenCrawlerIds.join(','))
  const qs = q.toString() ? `?${q}` : ''
  const r = await apiFetch(`/stock/artists${qs}`)
  if (!r.ok) throw new Error(await r.text())
  const data = await r.json()
  return data.artists
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/test/client.test.ts`
Expected: PASS (all cases, including the 3 new ones).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/test/client.test.ts
git commit -m "feat: getStock/getStockArtists accept hiddenCrawlerIds"
```

---

### Task 4: `App.tsx` — lifted `hiddenCrawlerIds` state and un-gated Settings nav

**Files:**
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/test/accountNav.test.tsx:74-80`

- [ ] **Step 1: Write the failing test**

In `frontend/src/test/accountNav.test.tsx`, replace the test at lines 74-80:

```tsx
  it('hides the Logs nav button but shows Settings for a non-admin user', async () => {
    getAuthStatus.mockResolvedValueOnce({ state: 'authenticated', user: { discogs_username: 'test', is_admin: false } })
    render(<App />)
    await screen.findByRole('button', { name: 'Store' })
    expect(screen.getByRole('button', { name: 'Settings' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Logs' })).not.toBeInTheDocument()
  })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/test/accountNav.test.tsx`
Expected: FAIL — the Settings button is still hidden for a non-admin user.

- [ ] **Step 3: Add the `hiddenCrawlerIds` key, state, and toggle handler**

In `frontend/src/App.tsx`, add a new key constant next to the existing ones (after line 20):

```tsx
const HIDDEN_CRAWLER_IDS_KEY = 'discogs-browser.hiddenCrawlerIds'
```

Add state next to the `crawlers` declaration (after line 35):

```tsx
  const [hiddenCrawlerIds, setHiddenCrawlerIds] = useState<number[]>(() => {
    try {
      return JSON.parse(localStorage.getItem(HIDDEN_CRAWLER_IDS_KEY) ?? '[]')
    } catch {
      return []
    }
  })
```

Add the toggle handler next to `setSyncStatus` (after line 57):

```tsx
  const toggleCrawlerView = useCallback((crawlerId: number) => {
    setHiddenCrawlerIds((current) => {
      const next = current.includes(crawlerId)
        ? current.filter((id) => id !== crawlerId)
        : [...current, crawlerId]
      localStorage.setItem(HIDDEN_CRAWLER_IDS_KEY, JSON.stringify(next))
      return next
    })
  }, [])
```

- [ ] **Step 4: Un-gate the Settings nav button**

Replace the Settings button block (lines 404-415):

```tsx
          <button
            onClick={() => setView('settings')}
            className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
              view === 'settings'
                ? 'bg-indigo-600 text-white'
                : 'text-gray-400 hover:text-white'
            }`}
          >
            Settings
          </button>
```

(This removes the `{authState.state === 'authenticated' && authState.user.is_admin && ( ... )}` wrapper. Leave the Logs button's wrapper at lines 416-427 untouched — Logs stays admin-only.)

- [ ] **Step 5: Pass `hiddenCrawlerIds` to RecordBrowser/StockBrowser and the new props to Settings**

Replace the three view-rendering blocks (lines 442-471):

```tsx
        <div className={view === 'collection' ? 'h-full' : 'hidden'}>
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
            onRefreshCollection={() => handleRefresh()}
            syncGeneration={syncGeneration}
          />
        </div>
        <div className={view === 'instock' ? 'h-full' : 'hidden'}>
          <StockBrowser recommendedAvailable={recommendedAvailable} hiddenCrawlerIds={hiddenCrawlerIds} />
        </div>
        <div className={view === 'settings' ? 'h-full overflow-y-auto' : 'hidden'}>
          <Settings
            crawlers={crawlers}
            onCrawlersChange={setCrawlers}
            onRefreshPrices={handleRefreshPricesFromSettings}
            onRefreshStock={handleRefreshStock}
            onRefreshRecommendations={handleRefreshRecommendations}
            onExportRecommendations={handleExportRecommendations}
            onClearRecommendations={handleClearRecommendations}
            hasJudgedItems={hasJudgedItems}
            isAdmin={authState.user.is_admin}
            hiddenCrawlerIds={hiddenCrawlerIds}
            onToggleCrawlerView={toggleCrawlerView}
          />
        </div>
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/test/accountNav.test.tsx`
Expected: PASS.

- [ ] **Step 7: Run the full frontend suite**

Run: `cd frontend && npx vitest run`
Expected: Some failures in `settings.test.tsx`/`recordBrowser.test.tsx`/`stockBrowser.test.tsx` are expected here — `Settings`/`RecordBrowser`/`StockBrowser` don't accept these new props yet (Tasks 5-7 add that). Confirm no *other* file regresses.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/App.tsx frontend/src/test/accountNav.test.tsx
git commit -m "feat: un-gate Settings nav, add hiddenCrawlerIds lifted state"
```

---

### Task 5: `RecordBrowser.tsx` — filter columns by hidden crawler ids

**Files:**
- Modify: `frontend/src/views/RecordBrowser.tsx:5-17,117`
- Test: `frontend/src/test/recordBrowser.test.tsx`

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/test/recordBrowser.test.tsx`, after the existing test at lines 28-32:

```tsx
  it('does not render a column for a crawler in hiddenCrawlerIds even if enabled', async () => {
    render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} crawlers={CRAWLERS} hiddenCrawlerIds={[1]} />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    expect(screen.queryByText('Amazon')).toBeNull()
  })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/test/recordBrowser.test.tsx`
Expected: FAIL (TypeScript prop error — `hiddenCrawlerIds` doesn't exist on `Props`) — the "Amazon" column still renders.

- [ ] **Step 3: Add the prop and extend the filter**

In `frontend/src/views/RecordBrowser.tsx`, add to `Props` (after line 11):

```tsx
  hiddenCrawlerIds?: number[]
```

Add to the destructured params (line 17) and give it a default, matching the `crawlers = []` pattern:

```tsx
export default function RecordBrowser({ scope, onRefreshPrices, crawling, crawlingReleaseId, crawlEvents, crawlers = [], hiddenCrawlerIds = [], syncing, onRefreshCollection, syncGeneration }: Props) {
```

Replace the `enabledCrawlers` line (line 117):

```tsx
  const enabledCrawlers = crawlers.filter((c) => c.enabled && c.crawler_type === 'release' && !hiddenCrawlerIds.includes(c.id))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/test/recordBrowser.test.tsx`
Expected: PASS (all cases, including the new one).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/RecordBrowser.tsx frontend/src/test/recordBrowser.test.tsx
git commit -m "feat: RecordBrowser hides columns for crawlers in hiddenCrawlerIds"
```

---

### Task 6: `StockBrowser.tsx` — pass hidden ids through and refetch on change

**Files:**
- Modify: `frontend/src/views/StockBrowser.tsx:1-52`
- Test: `frontend/src/test/stockBrowser.test.tsx`

- [ ] **Step 1: Write the failing tests**

Add to `frontend/src/test/stockBrowser.test.tsx`, after the existing test at lines 172-177 (`'filters to overlapping artists...'`):

```tsx
  it('passes hiddenCrawlerIds through to getStock', async () => {
    render(<StockBrowser hiddenCrawlerIds={[3, 7]} />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ hiddenCrawlerIds: [3, 7] }))
  })

  it('refetches items and the artist sidebar when hiddenCrawlerIds changes', async () => {
    const { rerender } = render(<StockBrowser hiddenCrawlerIds={[]} />)
    await waitFor(() => expect(getStock).toHaveBeenCalledTimes(1))
    rerender(<StockBrowser hiddenCrawlerIds={[3]} />)
    await waitFor(() => expect(getStock).toHaveBeenCalledTimes(2))
    expect(getStockArtists).toHaveBeenLastCalledWith(false, false, [3])
  })
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/test/stockBrowser.test.tsx`
Expected: FAIL (TypeScript prop error — `hiddenCrawlerIds` doesn't exist on `Props`) — `getStock`/`getStockArtists` are never called with the hidden ids.

- [ ] **Step 3: Add the prop and thread it through**

In `frontend/src/views/StockBrowser.tsx`, replace `Props` and the function signature (lines 5-9):

```tsx
interface Props {
  recommendedAvailable?: boolean
  hiddenCrawlerIds?: number[]
}

function StockBrowser({ recommendedAvailable = false, hiddenCrawlerIds = [] }: Props) {
```

Replace the `load` callback (lines 29-44):

```tsx
  const load = useCallback(async () => {
    setLoading(true)
    try {
      const result = await getStock({
        search: search || undefined,
        artist: selectedArtist || undefined,
        sort, order, page, per_page: PER_PAGE,
        overlapping: filter === 'overlapping',
        recommended: filter === 'recommended',
        hiddenCrawlerIds,
      })
      setItems(result.items)
      setTotal(result.total)
    } finally {
      setLoading(false)
    }
  }, [search, selectedArtist, sort, order, page, filter, hiddenCrawlerIds])
```

Replace the artists-fetch effect (line 52):

```tsx
  useEffect(() => { getStockArtists(filter === 'overlapping', filter === 'recommended', hiddenCrawlerIds).then(setArtists) }, [filter, hiddenCrawlerIds])
```

- [ ] **Step 4: Run tests to verify they pass, and see the 4 known pre-existing failures**

Run: `cd frontend && npx vitest run src/test/stockBrowser.test.tsx`
Expected: the 2 new tests PASS. Four pre-existing assertions now FAIL, because `getStockArtists` always receives a 3rd argument (`hiddenCrawlerIds`, defaulting to `[]`) and Vitest's `toHaveBeenLastCalledWith` requires an exact arity match against only 2 expected args:
- `'refetches the artist sidebar scoped to recommended when Recommended is selected'`: the two `toHaveBeenLastCalledWith(false, false)` / `toHaveBeenLastCalledWith(false, true)` assertions
- `'refetches the artist sidebar scoped to overlapping when Overlapping is selected'`: the two `toHaveBeenLastCalledWith(false, false)` / `toHaveBeenLastCalledWith(true, false)` assertions

- [ ] **Step 5: Update the 4 failing assertions to expect the 3rd argument**

In `frontend/src/test/stockBrowser.test.tsx`, in `'refetches the artist sidebar scoped to recommended when Recommended is selected'`, replace:

```tsx
    expect(getStockArtists).toHaveBeenLastCalledWith(false, false)
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'recommended' } })
    await waitFor(() => expect(getStockArtists).toHaveBeenLastCalledWith(false, true))
```

with:

```tsx
    expect(getStockArtists).toHaveBeenLastCalledWith(false, false, [])
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'recommended' } })
    await waitFor(() => expect(getStockArtists).toHaveBeenLastCalledWith(false, true, []))
```

In `'refetches the artist sidebar scoped to overlapping when Overlapping is selected'`, replace:

```tsx
    expect(getStockArtists).toHaveBeenLastCalledWith(false, false)
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'overlapping' } })
    await waitFor(() => expect(getStockArtists).toHaveBeenLastCalledWith(true, false))
```

with:

```tsx
    expect(getStockArtists).toHaveBeenLastCalledWith(false, false, [])
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'overlapping' } })
    await waitFor(() => expect(getStockArtists).toHaveBeenLastCalledWith(true, false, []))
```

- [ ] **Step 6: Run the full frontend suite**

Run: `cd frontend && npx vitest run`
Expected: PASS, no regressions.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/views/StockBrowser.tsx frontend/src/test/stockBrowser.test.tsx
git commit -m "feat: StockBrowser threads hiddenCrawlerIds into getStock/getStockArtists"
```

---

### Task 7: `Settings.tsx` — View/Crawl columns and the non-admin stripped page

**Files:**
- Modify: `frontend/src/views/Settings.tsx`
- Test: `frontend/src/test/settings.test.tsx`

- [ ] **Step 1: Write the failing tests**

Replace the whole of `frontend/src/test/settings.test.tsx`:

```tsx
import type { ComponentProps } from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import Settings from '../views/Settings'
import type { Crawler } from '../api/types'

const { getSettings, saveSettings, setCrawlerEnabled } = vi.hoisted(() => ({
  getSettings: vi.fn().mockResolvedValue({
    crawl_delay_seconds: 30,
    consecutive_failure_limit: 10,
    crawl_schedule: '',
    crawl_schedule_mode: 'missing',
    ebay_app_id: '',
    ebay_cert_id: '',
    stock_schedule: '',
  }),
  saveSettings: vi.fn().mockResolvedValue(undefined),
  setCrawlerEnabled: vi.fn().mockResolvedValue(undefined),
}))

vi.mock('../api/client', () => ({
  getSettings,
  saveSettings,
  setCrawlerEnabled,
}))

const CRAWLERS: Crawler[] = [
  { id: 1, site_name: 'Amazon', module_path: '', crawler_type: 'release', enabled: true, last_run: null, base_url: null },
  { id: 2, site_name: 'Disabled Site', module_path: '', crawler_type: 'release', enabled: false, last_run: null, base_url: null },
  { id: 3, site_name: 'Epitaph', module_path: '', crawler_type: 'catalog', enabled: true, last_run: null, base_url: null },
]

beforeEach(() => {
  vi.clearAllMocks()
})

function renderSettings(overrides: Partial<ComponentProps<typeof Settings>> = {}) {
  return render(
    <Settings
      crawlers={[]}
      onCrawlersChange={() => {}}
      onRefreshPrices={() => {}}
      onRefreshStock={() => {}}
      onRefreshRecommendations={() => {}}
      onExportRecommendations={() => {}}
      onClearRecommendations={() => {}}
      hasJudgedItems={false}
      isAdmin
      hiddenCrawlerIds={[]}
      onToggleCrawlerView={() => {}}
      {...overrides}
    />
  )
}

describe('Settings', () => {
  it('does not render the removed screenshot-interval or shuffle rows', async () => {
    renderSettings()
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    expect(screen.queryByText('Screenshot interval')).not.toBeInTheDocument()
    expect(screen.queryByText('Shuffle')).not.toBeInTheDocument()
  })

  it('shows both View and Crawl columns to an admin, for every crawler regardless of enabled state', async () => {
    renderSettings({ crawlers: CRAWLERS })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    expect(screen.getByText('Amazon')).toBeInTheDocument()
    expect(screen.getByText('Disabled Site')).toBeInTheDocument()
    expect(screen.getAllByText('Visible').length).toBe(3)
    expect(screen.getAllByText('Enabled').length).toBe(2)
    expect(screen.getAllByText('Disabled').length).toBe(1)
  })

  it('marks a crawler in hiddenCrawlerIds as Hidden in the View column', async () => {
    renderSettings({ crawlers: CRAWLERS, hiddenCrawlerIds: [1] })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    const amazonRow = screen.getByText('Amazon').closest('tr') as HTMLElement
    expect(amazonRow.textContent).toContain('Hidden')
  })

  it('calls onToggleCrawlerView when a View button is clicked', async () => {
    const onToggleCrawlerView = vi.fn()
    renderSettings({ crawlers: CRAWLERS, onToggleCrawlerView })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    const amazonRow = screen.getByText('Amazon').closest('tr') as HTMLElement
    fireEvent.click(screen.getAllByText('Visible').find((el) => amazonRow.contains(el))!)
    expect(onToggleCrawlerView).toHaveBeenCalledWith(1)
  })

  it('hides admin-only controls and the Crawl column for a non-admin, and only lists enabled crawlers', async () => {
    renderSettings({ crawlers: CRAWLERS, isAdmin: false })
    expect(getSettings).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument()
    expect(screen.getByText('Amazon')).toBeInTheDocument()
    expect(screen.queryByText('Disabled Site')).not.toBeInTheDocument()
    expect(screen.queryByText('Enabled')).not.toBeInTheDocument()
    expect(screen.queryByText('Recommendations Management')).not.toBeInTheDocument()
    expect(screen.getByText('Collection & Wishlist Price Sources')).toBeInTheDocument()
    expect(screen.getByText('Store Catalog Sources')).toBeInTheDocument()
  })

  it('still shows View toggles to a non-admin', async () => {
    const onToggleCrawlerView = vi.fn()
    renderSettings({ crawlers: CRAWLERS, isAdmin: false, onToggleCrawlerView })
    const amazonRow = screen.getByText('Amazon').closest('tr') as HTMLElement
    fireEvent.click(screen.getAllByText('Visible').find((el) => amazonRow.contains(el))!)
    expect(onToggleCrawlerView).toHaveBeenCalledWith(1)
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/test/settings.test.tsx`
Expected: FAIL — `isAdmin`/`hiddenCrawlerIds`/`onToggleCrawlerView` don't exist on `Settings`'s props yet; "Visible"/"Hidden"/relabeled section text doesn't exist.

- [ ] **Step 3: Add the new props and a shared toggle-button style helper**

In `frontend/src/views/Settings.tsx`, replace the `Props` interface and function signature (lines 42-53):

```tsx
interface Props {
  crawlers: Crawler[]
  onCrawlersChange: (crawlers: Crawler[]) => void
  onRefreshPrices: (mode: 'missing' | 'all') => void
  onRefreshStock: () => void
  onRefreshRecommendations: () => void
  onExportRecommendations: () => void
  onClearRecommendations: () => void
  hasJudgedItems: boolean
  isAdmin: boolean
  hiddenCrawlerIds: number[]
  onToggleCrawlerView: (crawlerId: number) => void
}

function toggleButtonClass(on: boolean): string {
  return `px-3 py-1 rounded text-xs font-medium transition-colors ${
    on ? 'bg-green-700 hover:bg-green-600 text-white' : 'bg-gray-700 hover:bg-gray-600 text-gray-400'
  }`
}

function Settings({
  crawlers, onCrawlersChange, onRefreshPrices, onRefreshStock, onRefreshRecommendations,
  onExportRecommendations, onClearRecommendations, hasJudgedItems, isAdmin, hiddenCrawlerIds, onToggleCrawlerView,
}: Props) {
```

- [ ] **Step 4: Skip the admin-only settings fetch for non-admins**

Replace the `useEffect` at lines 105-107:

```tsx
  useEffect(() => {
    if (isAdmin) getSettings().then(setSettings).catch(() => {})
  }, [isAdmin])
```

- [ ] **Step 5: Gate the top Save button**

Replace the header block (lines 128-137):

```tsx
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-white">Settings</h1>
        {isAdmin && (
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-4 py-1.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 rounded text-sm font-medium transition-colors"
          >
            {saved ? '✓ Saved' : saving ? 'Saving…' : 'Save'}
          </button>
        )}
      </div>
```

- [ ] **Step 6: Rewrite the "Crawler Management" section**

Replace the whole section (lines 139-240):

```tsx
      {/* Crawler Management */}
      <section>
        <h2 className="text-lg font-semibold text-white mb-1 text-left">
          {isAdmin ? 'Crawler Management' : 'Collection & Wishlist Price Sources'}
        </h2>
        <p className="text-sm text-gray-500 mb-4 text-left">
          {isAdmin
            ? <>Run price crawlers on a schedule. Leave blank to disable. Example: <code className="text-gray-400 font-mono">0 2 * * *</code> = 2 am daily.</>
            : 'Choose which stores\' prices you want to see in your Collection and Wishlist.'}
        </p>
        {isAdmin && (
          <>
            <table className="w-full text-sm border-collapse mb-4">
              <tbody>
                {CRAWLER_SETTING_ROWS.map((row, i) => renderSettingRow(row, i === 0))}
              </tbody>
            </table>
            <table className="w-full text-sm border-collapse">
              <tbody>
                <tr className="border-b border-gray-800/50">
                  <td className="py-3 pr-4 text-left text-gray-300 font-medium align-top whitespace-nowrap w-40">Schedule</td>
                  <td className="py-3 pr-4 text-left align-top w-64">
                    <input
                      type="text"
                      value={settings.crawl_schedule ?? ''}
                      placeholder="0 2 * * *"
                      onChange={(e) => setSettings({ ...settings, crawl_schedule: e.target.value })}
                      className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500 font-mono text-xs"
                    />
                  </td>
                  <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                    Cron expression (5 fields: min hour day month weekday). Empty = disabled.
                  </td>
                </tr>
                <tr className="border-b border-gray-800/50">
                  <td className="py-3 pr-4 text-left text-gray-300 font-medium align-top whitespace-nowrap">Mode</td>
                  <td className="py-3 pr-4 text-left align-top">
                    <select
                      value={settings.crawl_schedule_mode ?? 'missing'}
                      onChange={(e) => setSettings({ ...settings, crawl_schedule_mode: e.target.value as 'missing' | 'all' })}
                      className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white focus:outline-none focus:border-indigo-500"
                    >
                      <option value="missing">Missing only</option>
                      <option value="all">All records</option>
                    </select>
                  </td>
                  <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                    What to crawl on each scheduled run.
                  </td>
                </tr>
                <tr className="border-b border-gray-800/50">
                  <td className="py-3 pr-4 text-left align-top whitespace-nowrap w-40"></td>
                  <td className="py-3 pr-4 text-left align-top">
                    <button
                      onClick={() => onRefreshPrices(settings.crawl_schedule_mode as 'missing' | 'all' ?? 'missing')}
                      className="px-3 py-1 bg-indigo-700 hover:bg-indigo-600 active:bg-indigo-800 rounded text-xs font-medium transition-colors"
                    >
                      Refresh
                    </button>
                  </td>
                  <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                    Run price crawlers immediately.
                  </td>
                </tr>
              </tbody>
            </table>
          </>
        )}
        {(() => {
          const shown = isAdmin ? releaseCrawlers : releaseCrawlers.filter((c) => c.enabled)
          return shown.length === 0 ? (
            <p className="text-gray-500 text-sm text-left mt-4">No crawlers configured.</p>
          ) : (
            <table className="w-full text-sm border-collapse mt-4">
              <thead>
                <tr className="text-xs text-gray-500 uppercase tracking-wider border-b border-gray-800">
                  <th className="text-left py-2 pr-4 w-40">Site</th>
                  {isAdmin && <th className="text-left py-2 pr-4 w-48">Last run</th>}
                  <th className="text-left py-2 pr-4">View</th>
                  {isAdmin && <th className="text-left py-2">Crawl</th>}
                </tr>
              </thead>
              <tbody>
                {shown.map((c) => (
                  <tr key={c.id} className="border-b border-gray-800/50">
                    <td className="py-3 pr-4 text-left text-gray-200 font-medium">
                      {c.base_url
                        ? <a href={c.base_url} target="_blank" rel="noreferrer"
                             className="text-indigo-400 hover:text-indigo-300 underline">{c.site_name}</a>
                        : c.site_name}
                    </td>
                    {isAdmin && (
                      <td className="py-3 pr-4 text-left text-gray-500 text-xs">
                        {c.last_run ? new Date(c.last_run).toLocaleString() : '—'}
                      </td>
                    )}
                    <td className="py-3 pr-4 text-left">
                      <button
                        onClick={() => onToggleCrawlerView(c.id)}
                        className={toggleButtonClass(!hiddenCrawlerIds.includes(c.id))}
                      >
                        {hiddenCrawlerIds.includes(c.id) ? 'Hidden' : 'Visible'}
                      </button>
                    </td>
                    {isAdmin && (
                      <td className="py-3 text-left">
                        <button
                          onClick={() => handleToggleCrawler(c)}
                          className={toggleButtonClass(c.enabled)}
                        >
                          {c.enabled ? 'Enabled' : 'Disabled'}
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          )
        })()}
      </section>
```

- [ ] **Step 7: Rewrite the "Store Management" section**

Replace the whole section (originally lines 242-322, shifted by Step 6's edit — locate by the `{/* Store Management */}` comment):

```tsx
      {/* Store Management */}
      <section>
        <h2 className="text-lg font-semibold text-white mb-1 text-left">
          {isAdmin ? 'Store Management' : 'Store Catalog Sources'}
        </h2>
        <p className="text-sm text-gray-500 mb-4 text-left">
          {isAdmin
            ? 'Scan an entire site\'s in-stock catalog, independent of your collection. Results appear in the Store tab. Leave schedule blank to disable.'
            : 'Choose which stores\' items you want to see in the Store tab.'}
        </p>
        {isAdmin && (
          <table className="w-full text-sm border-collapse">
            <tbody>
              <tr className="border-b border-gray-800/50">
                <td className="py-3 pr-4 text-left text-gray-300 font-medium align-top whitespace-nowrap w-40">Schedule</td>
                <td className="py-3 pr-4 text-left align-top w-64">
                  <input
                    type="text"
                    value={settings.stock_schedule ?? ''}
                    placeholder="0 3 * * *"
                    onChange={(e) => setSettings({ ...settings, stock_schedule: e.target.value })}
                    className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500 font-mono text-xs"
                  />
                </td>
                <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                  Cron expression (5 fields: min hour day month weekday). Empty = disabled.
                </td>
              </tr>
              <tr className="border-b border-gray-800/50">
                <td className="py-3 pr-4 text-left align-top whitespace-nowrap w-40"></td>
                <td className="py-3 pr-4 text-left align-top">
                  <button
                    onClick={onRefreshStock}
                    className="px-3 py-1 bg-indigo-700 hover:bg-indigo-600 active:bg-indigo-800 rounded text-xs font-medium transition-colors"
                  >
                    Refresh
                  </button>
                </td>
                <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                  Scan all enabled catalog crawlers immediately.
                </td>
              </tr>
            </tbody>
          </table>
        )}
        {(() => {
          const shown = isAdmin ? catalogCrawlers : catalogCrawlers.filter((c) => c.enabled)
          return shown.length === 0 ? (
            <p className="text-gray-500 text-sm text-left mt-4">No catalog crawlers configured.</p>
          ) : (
            <table className="w-full text-sm border-collapse mt-4">
              <thead>
                <tr className="text-xs text-gray-500 uppercase tracking-wider border-b border-gray-800">
                  <th className="text-left py-2 pr-4 w-40">Site</th>
                  {isAdmin && <th className="text-left py-2 pr-4 w-48">Last run</th>}
                  <th className="text-left py-2 pr-4">View</th>
                  {isAdmin && <th className="text-left py-2">Crawl</th>}
                </tr>
              </thead>
              <tbody>
                {shown.map((c) => (
                  <tr key={c.id} className="border-b border-gray-800/50">
                    <td className="py-3 pr-4 text-left text-gray-200 font-medium">
                      {c.base_url
                        ? <a href={c.base_url} target="_blank" rel="noreferrer"
                             className="text-indigo-400 hover:text-indigo-300 underline">{c.site_name}</a>
                        : c.site_name}
                    </td>
                    {isAdmin && (
                      <td className="py-3 pr-4 text-left text-gray-500 text-xs">
                        {c.last_run ? new Date(c.last_run).toLocaleString() : '—'}
                      </td>
                    )}
                    <td className="py-3 pr-4 text-left">
                      <button
                        onClick={() => onToggleCrawlerView(c.id)}
                        className={toggleButtonClass(!hiddenCrawlerIds.includes(c.id))}
                      >
                        {hiddenCrawlerIds.includes(c.id) ? 'Hidden' : 'Visible'}
                      </button>
                    </td>
                    {isAdmin && (
                      <td className="py-3 text-left">
                        <button
                          onClick={() => handleToggleCrawler(c)}
                          className={toggleButtonClass(c.enabled)}
                        >
                          {c.enabled ? 'Enabled' : 'Disabled'}
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          )
        })()}
      </section>
```

- [ ] **Step 8: Gate the "Recommendations Management" section**

Wrap the whole section (locate by the `{/* Recommendations Management */}` comment through its closing `</section>`) in `{isAdmin && ( ... )}`.

- [ ] **Step 9: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/test/settings.test.tsx`
Expected: PASS (all 6 cases).

- [ ] **Step 10: Run the full frontend suite**

Run: `cd frontend && npx vitest run`
Expected: PASS, no regressions — Tasks 4-6 already updated every other call site (`App.tsx`, `RecordBrowser.tsx`, `StockBrowser.tsx`) and their tests, so this is a final confirmation, not expected to surface new failures.

- [ ] **Step 11: Type-check and build**

Run: `cd frontend && npm run build`
Expected: succeeds — confirms no leftover type errors from the prop changes across `App.tsx`/`Settings.tsx`/`RecordBrowser.tsx`/`StockBrowser.tsx`.

- [ ] **Step 12: Commit**

```bash
git add frontend/src/views/Settings.tsx frontend/src/test/settings.test.tsx
git commit -m "feat: Settings gains View/Crawl columns and a non-admin stripped view"
```

---

### Task 8: Manual verification

- [ ] **Step 1: Start the app**

```bash
cd backend && source .venv/bin/activate && TEST_DATABASE_URL= uvicorn main:app --reload --port 8000 &
cd frontend && npm run dev
```

- [ ] **Step 2: Verify the admin golden path**

Log in as an admin. Open Settings. Confirm:
- Both "Crawler Management" and "Store Management" show a Site / Last run / View / Crawl table, listing every registered crawler regardless of enabled state.
- Toggling View for a crawler (e.g. Amazon) to "Hidden" removes its column from the Collection tab immediately (no reload needed) and its items from the Store tab.
- Toggling Crawl still calls the existing enable/disable endpoint and is unaffected by View.

- [ ] **Step 3: Verify persistence**

Reload the page. Confirm the View toggle you set in Step 2 is still "Hidden" and the Collection/Store tab still reflect it (localStorage survived the reload).

- [ ] **Step 4: Verify the non-admin view**

Log in as a non-admin account. Confirm:
- Settings nav button is now visible (previously hidden).
- Settings page shows only "Collection & Wishlist Price Sources" and "Store Catalog Sources", each listing only crawl-enabled crawlers, each with just a View toggle — no Save button, no schedule/refresh controls, no Crawl column, no Recommendations Management section.
- Toggling View here also hides/shows the corresponding Collection/Store tab content for this account's browser.

- [ ] **Step 5: Verify Store tab pagination stays correct with items hidden**

With at least two catalog crawlers populated with stock items, hide one via View. Confirm the Store tab's total count and pagination reflect only the visible crawler's items (not a client-side-truncated page).
