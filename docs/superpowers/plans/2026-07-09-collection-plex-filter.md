# Collection "No Plex" Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `No Plex` option to a new filter dropdown on the Collection tab, narrowing the table (and artist sidebar) to releases with no matched Plex album — disabled until Plex is configured in Settings.

**Architecture:** Mirrors the Store tab's existing `overlapping`/`recommended` filter exactly: a `no_plex: bool = False` parameter threaded through `db.get_releases`/`db.get_distinct_artists` → `GET /api/releases`/`GET /api/artists` → `RecordBrowser.tsx`'s new filter `<select>`, gated by a `plexAvailable` prop computed in `App.tsx` from the Settings API the same way `hasAnthropicKey` already is.

**Tech Stack:** FastAPI + SQLite (backend), React + TypeScript + Vite (frontend), pytest (backend tests), vitest + @testing-library/react (frontend tests).

**Spec:** [`docs/superpowers/specs/2026-07-09-collection-plex-filter-design.md`](../specs/2026-07-09-collection-plex-filter-design.md)

---

## Task 1: `db.get_releases` / `db.get_distinct_artists` — `no_plex` parameter

**Files:**
- Modify: `backend/db.py:216-226` (`get_releases` signature/conditions), `backend/db.py:534` (`get_distinct_artists` signature/conditions)
- Test: `backend/tests/test_db.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_db.py`, right after `test_get_releases_scope_both_flags_appears_in_both`:

```python
def test_get_releases_no_plex_filter(conn):
    upsert_release(conn, _release("r1"))
    upsert_release(conn, _release("r2"))
    set_plex_match(conn, "r1", "http://plex.local:32400/web/x")
    result = get_releases(conn, no_plex=True)
    ids = {r["discogs_id"] for r in result["releases"]}
    assert ids == {"r2"}


def test_get_releases_no_plex_false_returns_all(conn):
    upsert_release(conn, _release("r1"))
    upsert_release(conn, _release("r2"))
    set_plex_match(conn, "r1", "http://plex.local:32400/web/x")
    result = get_releases(conn, no_plex=False)
    assert result["total"] == 2


def test_get_releases_no_plex_combined_with_scope(conn):
    upsert_release(conn, _release("r1"))
    upsert_release(conn, _release("r2"))
    mark_in_wishlist(conn, "r2")
    conn.execute("UPDATE releases SET in_collection = 0 WHERE discogs_id = 'r2'")
    result = get_releases(conn, scope="collection", no_plex=True)
    ids = {r["discogs_id"] for r in result["releases"]}
    assert ids == {"r1"}
```

And right after `test_get_distinct_artists_scope_none_returns_all`:

```python
def test_get_distinct_artists_no_plex_filter(conn):
    upsert_release(conn, _release("r1", artist="Matched Artist"))
    upsert_release(conn, _release("r2", artist="Unmatched Artist"))
    set_plex_match(conn, "r1", "http://plex.local:32400/web/x")
    artists = get_distinct_artists(conn, no_plex=True)
    assert artists == ["Unmatched Artist"]
```

`set_plex_match` is already imported in this file's `from db import (...)` block (added for the Plex-match feature) — no import change needed for it. `mark_in_wishlist` is also already imported.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_db.py -v -k no_plex`
Expected: FAIL — `TypeError: get_releases() got an unexpected keyword argument 'no_plex'`

- [ ] **Step 3: Add the parameter to `get_releases`**

In `backend/db.py`, change the `get_releases` signature and add a condition, right after the existing `scope` handling:

```python
def get_releases(
    conn: sqlite3.Connection,
    search: Optional[str] = None,
    artist: Optional[str] = None,
    sort: str = "artist",
    order: str = "asc",
    page: int = 1,
    per_page: int = 50,
    release_id: Optional[str] = None,
    scope: Optional[str] = None,
    no_plex: bool = False,
) -> dict:
    order_sql = "DESC" if order.lower() == "desc" else "ASC"

    conditions = []
    params: list = []

    if release_id:
        conditions.append("r.discogs_id = ?")
        params.append(release_id)
    if search:
        conditions.append("(r.artist LIKE ? OR r.title LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    if artist:
        conditions.append("r.artist = ?")
        params.append(artist)
    if scope == "collection":
        conditions.append("r.in_collection = 1")
    elif scope == "wishlist":
        conditions.append("r.in_wishlist = 1")
    if no_plex:
        conditions.append("r.plex_url IS NULL")
```

(The rest of the function — `where = ...` onward — is unchanged; `conditions`/`params` already flow into it.)

- [ ] **Step 4: Add the parameter to `get_distinct_artists`**

```python
def get_distinct_artists(conn: sqlite3.Connection, scope: Optional[str] = None, no_plex: bool = False) -> list[str]:
    conditions = []
    if scope == "collection":
        conditions.append("in_collection = 1")
    elif scope == "wishlist":
        conditions.append("in_wishlist = 1")
    if no_plex:
        conditions.append("plex_url IS NULL")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = conn.execute(f"SELECT DISTINCT artist FROM releases {where} ORDER BY artist").fetchall()
    return [row[0] for row in rows]
```

This replaces the current `if scope == "collection": where = "WHERE in_collection = 1"` / `elif scope == "wishlist": ...` two-branch `where`-string approach with a `conditions` list (matching `get_releases`'s style) so a second, independent condition (`no_plex`) can be ANDed in cleanly — the old string-branching approach had no way to combine two conditions.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_db.py -v -k "no_plex or get_distinct_artists or get_releases"`
Expected: PASS (all — confirms the new tests pass and the `get_distinct_artists` rewrite didn't break its existing `scope` tests)

Run the full file too: `cd backend && pytest tests/test_db.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add backend/db.py backend/tests/test_db.py
git commit -m "worktree-plex-integration: add no_plex filter to get_releases/get_distinct_artists"
```

---

## Task 2: `GET /api/releases` / `GET /api/artists` — `no_plex` passthrough

**Files:**
- Modify: `backend/routers/releases.py`
- Test: `backend/tests/test_releases_router.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_releases_router.py`:

```python
def test_releases_no_plex_filter(client, conn):
    upsert_release(conn, _release("r1"))
    upsert_release(conn, _release("r2"))
    conn.execute("UPDATE releases SET plex_url = 'http://plex.local:32400/web/x' WHERE discogs_id = 'r1'")

    r = client.get("/api/releases?no_plex=true")
    ids = {rel["discogs_id"] for rel in r.json()["releases"]}
    assert ids == {"r2"}


def test_artists_no_plex_filter(client, conn):
    upsert_release(conn, _release("r1", artist="Matched Artist"))
    upsert_release(conn, _release("r2", artist="Unmatched Artist"))
    conn.execute("UPDATE releases SET plex_url = 'http://plex.local:32400/web/x' WHERE discogs_id = 'r1'")

    r = client.get("/api/artists?no_plex=true")
    assert r.json()["artists"] == ["Unmatched Artist"]
```

(Setting `plex_url` via a raw `UPDATE` rather than `set_plex_match` here, since this file's fixtures don't import it and there's no reason to add the import just for a one-line column set — matches this file's existing style of raw `conn.execute("UPDATE releases SET ...")` calls for test setup, e.g. `test_releases_scope_wishlist`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_releases_router.py -v -k no_plex`
Expected: FAIL — both requests succeed (200) but return all releases/artists instead of the filtered subset, since the router doesn't read `no_plex` yet.

- [ ] **Step 3: Add the parameter**

In `backend/routers/releases.py`:

```python
from fastapi import APIRouter, Query
from typing import Optional
from db import get_connection, get_releases, get_all_crawlers, get_distinct_artists

router = APIRouter()


@router.get("/releases")
def list_releases(
    search: Optional[str] = Query(None),
    artist: Optional[str] = Query(None),
    sort: str = Query("artist"),
    order: str = Query("asc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    scope: Optional[str] = Query(None),
    no_plex: bool = Query(False),
):
    conn = get_connection()
    return get_releases(conn, search=search, artist=artist, sort=sort,
                        order=order, page=page, per_page=per_page, scope=scope, no_plex=no_plex)


@router.get("/artists")
def list_artists(scope: Optional[str] = Query(None), no_plex: bool = Query(False)):
    conn = get_connection()
    return {"artists": get_distinct_artists(conn, scope=scope, no_plex=no_plex)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_releases_router.py -v`
Expected: PASS (all)

Run the full backend suite: `cd backend && pytest`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add backend/routers/releases.py backend/tests/test_releases_router.py
git commit -m "worktree-plex-integration: expose no_plex filter on releases/artists endpoints"
```

---

## Task 3: `RecordBrowser.tsx` — filter dropdown

> **Post-implementation correction (2026-07-09):** every `collectionFilter` localStorage key below (in the tests, `client.ts`, and `RecordBrowser.tsx` snippets) was shipped, then found buggy, then fixed to `` `collectionFilter_${scope}` `` before merge — see the spec's amendment for why. The steps below are left as originally written, as a record of what was actually executed step-by-step; they do not reflect the final, correct code.

**Files:**
- Modify: `frontend/src/api/client.ts:44-70` (`getReleases`, `getArtists`), `frontend/src/views/RecordBrowser.tsx`
- Test: `frontend/src/test/collectionPlexFilter.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/test/collectionPlexFilter.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import RecordBrowser from '../views/RecordBrowser'
import type { Release } from '../api/types'

const { getReleases, getArtists } = vi.hoisted(() => ({
  getReleases: vi.fn().mockResolvedValue({ total: 0, page: 1, per_page: 50, releases: [] as Release[] }),
  getArtists: vi.fn().mockResolvedValue([]),
}))

vi.mock('../api/client', () => ({
  getReleases,
  getArtists,
}))

beforeEach(() => {
  vi.clearAllMocks()
  getReleases.mockResolvedValue({ total: 0, page: 1, per_page: 50, releases: [] })
  getArtists.mockResolvedValue([])
  localStorage.clear()
})

describe('Collection "No Plex" filter', () => {
  it('renders the filter dropdown on the Collection tab, defaulting to All', async () => {
    render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    const select = screen.getByRole('combobox') as HTMLSelectElement
    expect(select.value).toBe('all')
    expect(Array.from(select.options).map((o) => o.text)).toEqual(['All', 'No Plex'])
  })

  it('does not render the filter dropdown on the Wishlist tab', async () => {
    render(<RecordBrowser scope="wishlist" onRefreshPrices={() => {}} />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    expect(screen.queryByRole('combobox')).toBeNull()
  })

  it('disables No Plex when plexAvailable is not set', async () => {
    render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    expect((screen.getByRole('option', { name: 'No Plex' }) as HTMLOptionElement).disabled).toBe(true)
  })

  it('enables No Plex when plexAvailable is true', async () => {
    render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} plexAvailable />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    expect((screen.getByRole('option', { name: 'No Plex' }) as HTMLOptionElement).disabled).toBe(false)
  })

  it('filters to unmatched releases when No Plex is selected', async () => {
    render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} plexAvailable />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'no_plex' } })
    await waitFor(() => expect(getReleases).toHaveBeenCalledWith(expect.objectContaining({ no_plex: true })))
  })

  it('turns the filter back off when All is selected after No Plex', async () => {
    render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} plexAvailable />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'no_plex' } })
    await waitFor(() => expect(getReleases).toHaveBeenCalledWith(expect.objectContaining({ no_plex: true })))
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'all' } })
    await waitFor(() => expect(getReleases).toHaveBeenCalledWith(expect.objectContaining({ no_plex: false })))
  })

  it('refetches the artist sidebar scoped to no_plex when No Plex is selected', async () => {
    render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} plexAvailable />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    expect(getArtists).toHaveBeenLastCalledWith('collection', false)
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'no_plex' } })
    await waitFor(() => expect(getArtists).toHaveBeenLastCalledWith('collection', true))
  })

  it('resets to All when plexAvailable becomes false while No Plex is selected', async () => {
    localStorage.setItem('collectionFilter', 'no_plex')
    const { rerender } = render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} plexAvailable />)
    await waitFor(() => expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('no_plex'))
    rerender(<RecordBrowser scope="collection" onRefreshPrices={() => {}} plexAvailable={false} />)
    await waitFor(() => expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('all'))
  })

  it('persists the filter to localStorage under collectionFilter and restores it on remount', async () => {
    const { unmount } = render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} plexAvailable />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'no_plex' } })
    await waitFor(() => expect(localStorage.getItem('collectionFilter')).toBe('no_plex'))
    unmount()
    render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} plexAvailable />)
    await waitFor(() => expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('no_plex'))
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/test/collectionPlexFilter.test.tsx`
Expected: FAIL — no `combobox` role exists yet in `RecordBrowser`.

- [ ] **Step 3: Add `no_plex` to `api/client.ts`**

In `frontend/src/api/client.ts`, update `getReleases`:

```typescript
export async function getReleases(params: {
  search?: string
  artist?: string
  sort?: SortField
  order?: SortOrder
  page?: number
  per_page?: number
  scope?: RecordScope
  no_plex?: boolean
}): Promise<ReleasesResponse> {
  const q = new URLSearchParams()
  if (params.search) q.set('search', params.search)
  if (params.artist) q.set('artist', params.artist)
  if (params.sort) q.set('sort', params.sort)
  if (params.order) q.set('order', params.order)
  if (params.page) q.set('page', String(params.page))
  if (params.per_page) q.set('per_page', String(params.per_page))
  if (params.scope) q.set('scope', params.scope)
  if (params.no_plex) q.set('no_plex', 'true')
  const r = await apiFetch(`/releases?${q}`)
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}
```

And `getArtists`:

```typescript
export async function getArtists(scope?: RecordScope, noPlex?: boolean): Promise<string[]> {
  const q = new URLSearchParams()
  if (scope) q.set('scope', scope)
  if (noPlex) q.set('no_plex', 'true')
  const qs = q.toString() ? `?${q}` : ''
  const r = await apiFetch(`/artists${qs}`)
  if (!r.ok) throw new Error(await r.text())
```

(The rest of `getArtists` — the final `.json()`-returning line — is unchanged. This replaces the old single-`scope`-param template-string URL with the same `URLSearchParams` pattern `getReleases`/`getStockArtists` already use, since there are now two independent optional params to combine.)

- [ ] **Step 4: Add the filter to `RecordBrowser.tsx`**

In `frontend/src/views/RecordBrowser.tsx`:

Add to the `Props` interface and destructured params:

```tsx
interface Props {
  scope: RecordScope
  onRefreshPrices: (releaseId: string) => void
  crawling?: boolean
  crawlingReleaseId?: string
  crawlEvents?: CrawlEvent[]
  crawlers?: Crawler[]
  syncing?: boolean
  plexAvailable?: boolean
}

export default function RecordBrowser({ scope, onRefreshPrices, crawling, crawlingReleaseId, crawlEvents, crawlers = [], syncing, plexAvailable }: Props) {
```

Add filter state, right after the existing `viewMode` state:

```tsx
  const [viewMode, setViewMode] = useState<'list' | 'tiles'>(
    () => (localStorage.getItem(`collectionViewMode_${scope}`) === 'tiles' ? 'tiles' : 'list')
  )
  const [filter, setFilter] = useState<'all' | 'no_plex'>(
    () => (localStorage.getItem('collectionFilter') === 'no_plex' ? 'no_plex' : 'all')
  )
```

Add the reset-when-unavailable guard and the persistence effect, alongside the other `useEffect`s near the bottom of the hooks block (after the existing `localStorage.setItem` viewMode effect):

```tsx
  useEffect(() => {
    if (!plexAvailable && filter === 'no_plex') setFilter('all')
  }, [plexAvailable, filter])
  useEffect(() => { localStorage.setItem('collectionFilter', filter) }, [filter])
```

Update `load` to send `no_plex`, and its dependency array:

```tsx
  const load = useCallback(async () => {
    setLoading(true)
    try {
      const result = await getReleases({
        search: search || undefined,
        artist: selectedArtist || undefined,
        sort,
        order,
        page,
        per_page: PER_PAGE,
        scope,
        no_plex: filter === 'no_plex',
      })
      setReleases(result.releases)
      setTotal(result.total)
    } finally {
      setLoading(false)
    }
  }, [search, selectedArtist, sort, order, page, scope, filter])
```

Update the artist-list effect:

```tsx
  useEffect(() => { getArtists(scope, filter === 'no_plex').then(setArtists) }, [scope, filter])
```

Add `setPage(1)` on filter change and add the dropdown itself. In the toolbar, find:

```tsx
          <span className="ml-3 text-xs text-gray-500">{total} records</span>
          <div className="ml-auto flex items-center gap-1">
            <button
              onClick={() => setViewMode('list')}
```

Replace with:

```tsx
          <span className="ml-3 text-xs text-gray-500">{total} records</span>
          <div className="ml-auto flex items-center gap-1">
            {scope === 'collection' && (
              <select
                value={filter}
                onChange={(e) => { setFilter(e.target.value as 'all' | 'no_plex'); setPage(1) }}
                className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:border-indigo-500 mr-1"
              >
                <option value="all">All</option>
                <option value="no_plex" disabled={!plexAvailable}>No Plex</option>
              </select>
            )}
            <button
              onClick={() => setViewMode('list')}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/test/collectionPlexFilter.test.tsx`
Expected: PASS (9 tests)

- [ ] **Step 6: Run the full frontend test suite and typecheck**

Run: `cd frontend && npm run test`
Expected: PASS (all) — pay particular attention to `staleListingClear.test.tsx`, `plexLink.test.tsx`, and `syncRefetch.test.tsx`, since they all render `RecordBrowser` too and none of them pass a `plexAvailable` prop (so `No Plex` will render disabled for them, which is fine — none of those tests interact with the dropdown).

Run: `cd frontend && npx tsc -b --noEmit`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/views/RecordBrowser.tsx frontend/src/test/collectionPlexFilter.test.tsx
git commit -m "worktree-plex-integration: add No Plex filter dropdown to Collection tab"
```

---

## Task 4: `App.tsx` — wire up `plexAvailable`

**Files:**
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Compute `hasPlexConfigured`**

In `frontend/src/App.tsx`, find the existing `hasAnthropicKey` state declaration (`const [hasAnthropicKey, setHasAnthropicKey] = useState(false)`) and add a sibling right after it:

```tsx
  const [hasAnthropicKey, setHasAnthropicKey] = useState(false)
  const [hasPlexConfigured, setHasPlexConfigured] = useState(false)
```

Find the settings-fetch line inside the health-poll effect:

```tsx
            getSettings().then((s) => setHasAnthropicKey(Boolean(s.anthropic_api_key))).catch(() => {})
```

Replace with:

```tsx
            getSettings().then((s) => {
              setHasAnthropicKey(Boolean(s.anthropic_api_key))
              setHasPlexConfigured(Boolean(s.plex_base_url && s.plex_token))
            }).catch(() => {})
```

- [ ] **Step 2: Pass the prop to both `RecordBrowser` instances**

Find both `<RecordBrowser ... syncing={syncing} />` blocks (one with `scope="collection"`, one with `scope="wishlist"`) and add `plexAvailable={hasPlexConfigured}` to each, right after `syncing={syncing}`:

```tsx
          <RecordBrowser
            scope="collection"
            onRefreshPrices={(id) => handleFindPrices(id)}
            crawling={crawling}
            crawlingReleaseId={crawlingReleaseId}
            crawlEvents={crawlEvents}
            crawlers={crawlers}
            syncing={syncing}
            plexAvailable={hasPlexConfigured}
          />
```

```tsx
          <RecordBrowser
            scope="wishlist"
            onRefreshPrices={(id) => handleFindPrices(id)}
            crawling={crawling}
            crawlingReleaseId={crawlingReleaseId}
            crawlEvents={crawlEvents}
            crawlers={crawlers}
            syncing={syncing}
            plexAvailable={hasPlexConfigured}
          />
```

(Passing it to the Wishlist instance too is harmless — its dropdown never renders regardless, per Task 3 Step 4's `scope === 'collection'` guard — same reasoning already applied to `syncing`.)

- [ ] **Step 3: Verify typecheck and existing tests**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: no errors.

Run: `cd frontend && npm run test`
Expected: PASS (all) — this file has no dedicated test suite today (consistent with the rest of `App.tsx`), so there's nothing new to run here beyond confirming nothing else broke.

- [ ] **Step 4: Manually verify in the browser**

Run: `cd frontend && npm run dev` (backend running too, with Plex already configured in Settings from the real end-to-end test done earlier).
On the Collection tab: confirm the dropdown now shows `All`/`No Plex`, `No Plex` is selectable (not grayed out), and selecting it narrows the table to releases without a Plex link (and narrows the artist sidebar to match). Switch to the Wishlist tab and confirm no dropdown appears there. Temporarily clear the Plex token in Settings, save, and confirm `No Plex` becomes disabled again and any active `No Plex` selection reverts to `All`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "worktree-plex-integration: wire plexAvailable into the Collection filter"
```

---

## Self-review notes

- **Spec coverage:** every spec requirement has a task — backend `no_plex` param (Task 1), API passthrough (Task 2), dropdown/disabled-state/reset/persistence (Task 3), `plexAvailable` derivation and wiring (Task 4). The spec's Non-goals (Wishlist dropdown, a "Matched" option, per-scope persistence) have no corresponding task, deliberately.
- **Type consistency:** `no_plex` (snake_case) is used consistently end-to-end in every backend signature, query param, and the `getReleases`/`getArtists` client calls, matching this codebase's existing `overlapping`/`recommended` naming exactly (snake_case even in TypeScript call sites, since these are wire-format param names, not JS identifiers — `getArtists`'s second parameter is named `noPlex` in camelCase since that one *is* a plain positional JS argument, matching `getStockArtists(overlapping?, recommended?)`'s precedent).
- **`get_distinct_artists` behavior change flagged, not hidden:** Task 1 Step 4 rewrites its two-branch `where`-string logic into a `conditions` list. This is called out explicitly in that step rather than left implicit, since it's a refactor of existing working code, not a pure addition — the existing `scope` tests in Task 1 Step 5 catch any regression.
- **No placeholders:** every step has runnable code or an exact command with expected output.
