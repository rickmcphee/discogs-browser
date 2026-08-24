# Auto-hide Price column Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates superpowers:subagent-driven-development for plan execution — do not offer superpowers:executing-plans as a default alternative. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hide the `Price` column (Collection tab, Wantlist tab, Track tab) for any user who has no collection price data, instead of showing a column of nothing but `—`.

**Architecture:** A new backend query (`db.has_any_price_paid`) checks whether the calling user has any `library_items` row with `in_collection = TRUE` and a non-null `price_paid`, exposed via `GET /api/collection/price-status`. The frontend fetches this once at app bootstrap (same pattern as the existing `any_judged` fetch) and passes the result down as a `hasPriceField` prop to `RecordBrowser` and the Track `StockBrowser`, which gate their existing `Price` column markup on it.

**Tech Stack:** FastAPI + psycopg (backend), React + TypeScript + Vitest/Testing Library (frontend).

## Global Constraints

- Python ≥3.9 — no `str | None` syntax; use `Optional[str]` or leave untyped.
- No comments unless the WHY is non-obvious.
- Every commit needs the AI-attribution trailer block (see repo `CLAUDE.md` — `ai-generated`, `ai-model`, `ai-tool`, `ai-surface`, `ai-executor`), added via `git commit -F <message-file>`, never `-m`.
- Backend tests require Postgres running with `TEST_DATABASE_URL`, `IDENTITY_DB_PASSWORD`, `APP_DB_PASSWORD` set (see repo `CLAUDE.md`'s "Tests" section). Run backend tests one suite at a time, not concurrently (two parallel `pytest` runs both die with exit 137 on this machine).
- Frontend tests: `npm test` (== `vitest run`) from `frontend/`.

---

### Task 1: Backend — detect whether the user has any collection price data

**Files:**
- Modify: `backend/db.py` — add `has_any_price_paid` near the existing `has_any_stock_judgment` (`db.py:1867`)
- Test: `backend/tests/test_user_crud.py`

**Interfaces:**
- Produces: `db.has_any_price_paid(conn, user_id: int) -> bool`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_user_crud.py` (uses the file's existing `admin_conn` fixture):

```python
def test_has_any_price_paid_true_when_a_collection_item_has_a_price(admin_conn):
    user = db.create_user(admin_conn, discogs_user_id=42, discogs_username="alice")
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "d1", "artist": "A", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    admin_conn.commit()
    db.upsert_library_item(admin_conn, user_id=user["id"], discogs_id="d1", in_collection=True, price_paid="25.00")
    admin_conn.commit()

    assert db.has_any_price_paid(admin_conn, user["id"]) is True


def test_has_any_price_paid_false_with_no_price_data(admin_conn):
    user = db.create_user(admin_conn, discogs_user_id=42, discogs_username="alice")
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "d1", "artist": "A", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    admin_conn.commit()
    db.upsert_library_item(admin_conn, user_id=user["id"], discogs_id="d1", in_collection=True)
    admin_conn.commit()

    assert db.has_any_price_paid(admin_conn, user["id"]) is False


def test_has_any_price_paid_ignores_other_users(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=42, discogs_username="alice")
    bob = db.create_user(admin_conn, discogs_user_id=43, discogs_username="bob")
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "d1", "artist": "A", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    admin_conn.commit()
    db.upsert_library_item(admin_conn, user_id=alice["id"], discogs_id="d1", in_collection=True, price_paid="25.00")
    admin_conn.commit()

    assert db.has_any_price_paid(admin_conn, bob["id"]) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_user_crud.py -k has_any_price_paid -v`
Expected: FAIL with `AttributeError: module 'db' has no attribute 'has_any_price_paid'`

- [ ] **Step 3: Implement `has_any_price_paid`**

In `backend/db.py`, immediately above `has_any_stock_judgment` (`db.py:1867`):

```python
def has_any_price_paid(conn, user_id: int) -> bool:
    return conn.execute(
        "SELECT EXISTS(SELECT 1 FROM library_items WHERE user_id = %s "
        "AND in_collection = TRUE AND price_paid IS NOT NULL)",
        [user_id],
    ).fetchone()["exists"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_user_crud.py -k has_any_price_paid -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add backend/db.py backend/tests/test_user_crud.py
```

Commit message body: `Add db.has_any_price_paid to detect collection price data`, with the required AI-attribution trailer block, via `git commit -F <message-file>`.

---

### Task 2: Backend — `GET /collection/price-status` endpoint

**Files:**
- Modify: `backend/routers/collection.py`
- Test: `backend/tests/test_collection_router.py`

**Interfaces:**
- Consumes: `db.has_any_price_paid(conn, user_id: int) -> bool` (Task 1)
- Produces: `GET /api/collection/price-status` → `{"any_price_paid": bool}`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_collection_router.py` (uses the file's existing `authed_client_factory` fixture):

```python
def test_collection_price_status_scoped_to_calling_user(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True, price_paid="25.00")
        conn.commit()

    client = authed_client_factory(alice["id"])
    r = client.get("/api/collection/price-status")
    assert r.json() == {"any_price_paid": True}

    client = authed_client_factory(bob["id"])
    r = client.get("/api/collection/price-status")
    assert r.json() == {"any_price_paid": False}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_collection_router.py -k price_status -v`
Expected: FAIL with 404 (route doesn't exist)

- [ ] **Step 3: Implement the endpoint**

In `backend/routers/collection.py`, immediately after `collection_status` (`:9-17`):

```python
@router.get("/collection/price-status")
def collection_price_status(request: Request):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        return {"any_price_paid": db.has_any_price_paid(conn, user_id)}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_collection_router.py -k price_status -v`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add backend/routers/collection.py backend/tests/test_collection_router.py
```

Commit message body: `Add GET /collection/price-status endpoint`, with the required AI-attribution trailer block, via `git commit -F <message-file>`.

---

### Task 3: Frontend — gate `RecordBrowser`'s Price column on a `hasPriceField` prop

**Files:**
- Modify: `frontend/src/views/RecordBrowser.tsx`
- Test: `frontend/src/test/recordBrowser.test.tsx`

**Interfaces:**
- Produces: `RecordBrowser` gains prop `hasPriceField?: boolean` (default `true`) — later consumed by Task 5's App.tsx wiring.

- [ ] **Step 1: Write the failing tests**

Add to `frontend/src/test/recordBrowser.test.tsx`, inside the `describe('RecordBrowser', ...)` block:

```tsx
  it('renders the Price column by default', async () => {
    render(<RecordBrowser scope="collection" />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    expect(screen.getByText(/Price/)).toBeTruthy()
  })

  it('hides the Price column when hasPriceField is false, and widens the empty-state row to match', async () => {
    render(<RecordBrowser scope="collection" hasPriceField={false} />)
    const emptyRow = await screen.findByText('No records found. Click the sync icon above to load your collection from Discogs.')
    expect(screen.queryByText(/Price/)).toBeNull()
    expect(emptyRow.closest('td')).toHaveAttribute('colSpan', '7')
  })
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npx vitest run src/test/recordBrowser.test.tsx -t "Price column"`
Expected: the `hasPriceField={false}` test FAILs (Price header still renders, colSpan is still 8); the "by default" test passes already (no behavior change yet).

- [ ] **Step 3: Implement the prop and gating**

In `frontend/src/views/RecordBrowser.tsx`, the `Props` interface (`:8-13`):

```tsx
interface Props {
  scope: RecordScope
  syncing?: boolean
  onRefreshCollection?: () => void
  syncGeneration?: number
  hasPriceField?: boolean
}
```

The function signature (`:20`):

```tsx
export default function RecordBrowser({ scope, syncing, onRefreshCollection, syncGeneration, hasPriceField = true }: Props) {
```

The Price `<th>` block (`:305-312`), wrapped:

```tsx
                {hasPriceField && (
                  <th
                    className="text-center"
                    aria-sort={sort === 'discogs_price' ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}
                  >
                    <button type="button" onClick={() => toggleSort('discogs_price')} className={`${sortButtonClass} text-center`}>
                      Price {sort === 'discogs_price' ? (order === 'asc' ? '↑' : '↓') : ''}
                    </button>
                  </th>
                )}
```

The empty-state `colSpan` (`:326`):

```tsx
                  <td colSpan={hasPriceField ? 8 : 7} className="text-center py-8 text-gray-500">
```

The Price `<td>` (`:361`):

```tsx
                  {hasPriceField && <td className="px-3 py-2 text-gray-400">{r.discogs_price ?? '—'}</td>}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/test/recordBrowser.test.tsx`
Expected: all tests pass (existing tests are unaffected — `hasPriceField` defaults to `true`, matching prior unconditional rendering).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/RecordBrowser.tsx frontend/src/test/recordBrowser.test.tsx
```

Commit message body: `Gate RecordBrowser's Price column on a hasPriceField prop`, with the required AI-attribution trailer block, via `git commit -F <message-file>`.

---

### Task 4: Frontend — gate `StockBrowser`'s Track-tab Price column on the same prop

**Files:**
- Modify: `frontend/src/views/StockBrowser.tsx`
- Test: `frontend/src/test/stockBrowser.test.tsx`

**Interfaces:**
- Produces: `StockBrowser` gains prop `hasPriceField?: boolean` (default `true`) — later consumed by Task 5's App.tsx wiring.

- [ ] **Step 1: Write the failing tests**

Add to `frontend/src/test/stockBrowser.test.tsx`, inside the `describe('StockBrowser', ...)` block:

```tsx
  it('renders the Price column by default in Track scope', async () => {
    render(<StockBrowser scope="track" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(screen.getByText(/Price/)).toBeTruthy()
  })

  it('hides the Price column in Track scope when hasPriceField is false', async () => {
    render(<StockBrowser scope="track" hasPriceField={false} />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(screen.queryByText(/Price/)).toBeNull()
  })

  it('does not render a Price column in Store scope even when hasPriceField is true', async () => {
    render(<StockBrowser hasPriceField />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(screen.queryByText(/Price/)).toBeNull()
  })
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npx vitest run src/test/stockBrowser.test.tsx -t "Price column"`
Expected: the `hasPriceField={false}` test FAILs (Price header still renders).

- [ ] **Step 3: Implement the prop and gating**

In `frontend/src/views/StockBrowser.tsx`, the `Props` interface (`:9-18`):

```tsx
interface Props {
  scope?: StockScope
  recommendedAvailable?: boolean
  hiddenCrawlerIds?: number[]
  crawlers?: Crawler[]
  onHiddenCrawlerIdsChange?: (hiddenCrawlerIds: number[]) => void
  hiddenCrawlerIdsLoaded?: boolean
  syncGeneration?: number
  isAdmin?: boolean
  hasPriceField?: boolean
}
```

The function signature (`:38-42`):

```tsx
function StockBrowser({
  scope = 'store', recommendedAvailable = false, hiddenCrawlerIds = NO_HIDDEN_CRAWLER_IDS,
  crawlers = NO_CRAWLERS, onHiddenCrawlerIdsChange = NOOP_HIDDEN_CRAWLER_IDS_CHANGE,
  hiddenCrawlerIdsLoaded = true, syncGeneration, isAdmin = false, hasPriceField = true,
}: Props) {
```

`colCount` (`:239`):

```tsx
  const colCount = scope === 'track' ? (hasPriceField ? 7 : 6) : 7
```

The header block (`:412-422`):

```tsx
                {scope === 'track' && hasPriceField && (
                  priceSortable ? (
                    <th className="text-center" aria-sort={sort === 'discogs_price' ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}>
                      <button type="button" onClick={() => toggleSort('discogs_price')} className={`${sortButtonClass} text-center`}>
                        Price {sort === 'discogs_price' ? (order === 'asc' ? '↑' : '↓') : ''}
                      </button>
                    </th>
                  ) : (
                    <th className="text-center px-3 py-2">Price</th>
                  )
                )}
```

The cell block (`:456-458`):

```tsx
                  {scope === 'track' && hasPriceField && (
                    <td className="px-3 py-2 text-gray-400">{item.discogs_price ?? '—'}</td>
                  )}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/test/stockBrowser.test.tsx`
Expected: all tests pass (existing tests are unaffected — `hasPriceField` defaults to `true`).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/StockBrowser.tsx frontend/src/test/stockBrowser.test.tsx
```

Commit message body: `Gate StockBrowser's Track Price column on a hasPriceField prop`, with the required AI-attribution trailer block, via `git commit -F <message-file>`.

---

### Task 5: Frontend — fetch price status at bootstrap and wire it through

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/App.tsx`
- Modify (mock updates only): `frontend/src/test/accountNav.test.tsx`, `frontend/src/test/backendDown.test.tsx`, `frontend/src/test/crawlStatusBar.test.tsx`, `frontend/src/test/staleSignupLink.test.tsx`, `frontend/src/test/viewRenderChurn.test.tsx`, `frontend/src/test/wantlistRefresh.test.tsx`, `frontend/src/test/inStockTab.test.tsx`
- Test: `frontend/src/test/inStockTab.test.tsx`

**Interfaces:**
- Consumes: `RecordBrowser`'s and `StockBrowser`'s `hasPriceField` prop (Tasks 3, 4); backend `GET /collection/price-status` (Task 2)
- Produces: `getPriceStatus(): Promise<{ any_price_paid: boolean }>` in `api/client.ts`

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/test/inStockTab.test.tsx`, immediately after the existing `'shows a Track nav button that switches to a track-scoped StockBrowser'` test (`:108-115`):

```tsx
  it('hides the Track Price column when the user has no collection price data', async () => {
    getPriceStatus.mockResolvedValue({ any_price_paid: false })
    render(<App />)
    await waitFor(() => expect(screen.getByText('Track')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Track'))
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ libraryScope: 'all' })))
    expect(screen.queryByText(/Price/)).toBeNull()
  })

  it('shows the Track Price column when the user has collection price data', async () => {
    getPriceStatus.mockResolvedValue({ any_price_paid: true })
    render(<App />)
    await waitFor(() => expect(screen.getByText('Track')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Track'))
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ libraryScope: 'all' })))
    expect(screen.getByText(/Price/)).toBeTruthy()
  })
```

This file mocks `getStock` with items that render `'The Great Satan — Ghostly Black Vinyl'` (see file top), so `getStock` having been called with the Track scope is enough to know the tab rendered.

Still in `frontend/src/test/inStockTab.test.tsx`, add the mock plumbing following the file's existing `getJudgmentStatus` pattern:

```tsx
const getJudgmentStatus = vi.fn()
const getCrawlers = vi.fn()
```
becomes
```tsx
const getJudgmentStatus = vi.fn()
const getPriceStatus = vi.fn()
const getCrawlers = vi.fn()
```

```tsx
  getJudgmentStatus: (...args: unknown[]) => getJudgmentStatus(...args),
```
becomes
```tsx
  getJudgmentStatus: (...args: unknown[]) => getJudgmentStatus(...args),
  getPriceStatus: (...args: unknown[]) => getPriceStatus(...args),
```

```tsx
  getJudgmentStatus.mockResolvedValue({ any_judged: false })
```
becomes
```tsx
  getJudgmentStatus.mockResolvedValue({ any_judged: false })
  getPriceStatus.mockResolvedValue({ any_price_paid: false })
```

In each of the other App-rendering test files (`accountNav.test.tsx`, `backendDown.test.tsx`, `crawlStatusBar.test.tsx`, `staleSignupLink.test.tsx`, `viewRenderChurn.test.tsx`, `wantlistRefresh.test.tsx`), find:

```tsx
  getJudgmentStatus: vi.fn().mockResolvedValue({ any_judged: false }),
```

and add immediately after it:

```tsx
  getPriceStatus: vi.fn().mockResolvedValue({ any_price_paid: false }),
```

(This line is required in every file, not just the two new `inStockTab.test.tsx` tests — without it, App.tsx's new bootstrap call to `getPriceStatus()` throws `getPriceStatus is not a function` before its own `.catch()` attaches, crashing every test in those files.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npx vitest run src/test/inStockTab.test.tsx -t "Price column"`
Expected: FAIL — `getPriceStatus` isn't exported from `api/client.ts` yet, so the mock registration itself is inert and `App.tsx` doesn't call or use it; the new tests fail because the Price column still isn't wired to anything (Track still shows it unconditionally per Task 4's `hasPriceField = true` default).

Then run the full frontend suite to confirm nothing is broken yet by the mock-only edits: `cd frontend && npm test`
Expected: passes (mock additions are inert until `App.tsx` calls the new function).

- [ ] **Step 3: Implement the client function and App.tsx wiring**

In `frontend/src/api/client.ts`, immediately after `getJudgmentStatus` (`:254-258`):

```ts
export async function getPriceStatus(): Promise<{ any_price_paid: boolean }> {
  const r = await apiFetch('/collection/price-status')
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}
```

In `frontend/src/App.tsx`, the import line (`:12`) — add `getPriceStatus` to the destructured import list from `'./api/client'`, next to `getJudgmentStatus`.

State declarations (`:42-43`):

```tsx
  const [hasAnthropicKey, setHasAnthropicKey] = useState(false)
  const [hasJudgedItems, setHasJudgedItems] = useState(false)
  const [hasPriceData, setHasPriceData] = useState(false)
```

Bootstrap effect (`:137-141`):

```tsx
    getUserSettings().then((s) => {
      setHasAnthropicKey(Boolean(s.anthropic_api_key))
    }).catch(() => {})
    getJudgmentStatus().then((s) => setHasJudgedItems(s.any_judged)).catch(() => {})
    getPriceStatus().then((s) => setHasPriceData(s.any_price_paid)).catch(() => {})
    hasAvatar().then((exists) => setAvatarVersion(exists ? Date.now() : 0)).catch(() => {})
```

The `RecordBrowser` and `StockBrowser` render sites (`:626-646`):

```tsx
        <div className={view === 'collection' ? 'h-full' : 'hidden'}>
          <RecordBrowser
            scope="collection"
            syncing={syncing}
            onRefreshCollection={() => handleRefresh()}
            syncGeneration={syncGeneration}
            hasPriceField={hasPriceData}
          />
        </div>
        <div className={view === 'wantlist' ? 'h-full' : 'hidden'}>
          <RecordBrowser
            scope="wantlist"
            syncing={syncing}
            onRefreshCollection={() => handleRefreshWantlist()}
            syncGeneration={syncGeneration}
            hasPriceField={hasPriceData}
          />
        </div>
        <div className={view === 'store' ? 'h-full' : 'hidden'}>
          <StockBrowser recommendedAvailable={recommendedAvailable} hiddenCrawlerIds={hiddenCrawlerIds} crawlers={crawlers} onHiddenCrawlerIdsChange={updateHiddenCrawlerIds} hiddenCrawlerIdsLoaded={hiddenCrawlerIdsLoaded} syncGeneration={stockSyncGeneration} isAdmin={showAdminNav} />
        </div>
        <div className={view === 'track' ? 'h-full' : 'hidden'}>
          <StockBrowser scope="track" hiddenCrawlerIds={hiddenCrawlerIds} crawlers={crawlers} onHiddenCrawlerIdsChange={updateHiddenCrawlerIds} hiddenCrawlerIdsLoaded={hiddenCrawlerIdsLoaded} syncGeneration={stockSyncGeneration} isAdmin={showAdminNav} hasPriceField={hasPriceData} />
        </div>
```

(The Store `StockBrowser` instance is left without the prop — it never renders a Price column regardless, per Task 4.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/test/inStockTab.test.tsx`
Expected: all pass.

Then the full frontend suite: `cd frontend && npm test`
Expected: all pass — this is the check that the other mock-updated files still render `<App />` correctly.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/App.tsx frontend/src/test/accountNav.test.tsx frontend/src/test/backendDown.test.tsx frontend/src/test/crawlStatusBar.test.tsx frontend/src/test/staleSignupLink.test.tsx frontend/src/test/viewRenderChurn.test.tsx frontend/src/test/wantlistRefresh.test.tsx frontend/src/test/inStockTab.test.tsx
```

Commit message body: `Fetch price status at bootstrap and wire hasPriceField through App`, with the required AI-attribution trailer block, via `git commit -F <message-file>`.

---

## Post-implementation

- Manually verify in a browser: an account with a Discogs `"Price"` collection field populated shows the column on Collection/Wantlist/Track; an account without one does not.
- Run the repo's required pre-PR spec-drift check (see `CLAUDE.md`) before opening the PR.
