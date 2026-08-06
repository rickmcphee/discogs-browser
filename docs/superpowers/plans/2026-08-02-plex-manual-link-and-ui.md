# Plex Manual Link + Collection Hyperlink/Filter Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a manual Plex-match trigger button, fix Collections/Wishlist hyperlinks so the cover icon links to Discogs (not the artist name), and restore an "Unmatched" filter on the Collection tab.

**Architecture:** The manual trigger is a new fire-and-forget endpoint mirroring the existing `postStockSyncStart`/`postJudgmentStart` pattern, backed by a new per-user task dict on `CrawlManager` (matching `_judgment_tasks`) so it can't double-run or collide with a sync-triggered match. The hyperlink fix is a pure JSX restructuring in `RecordBrowser.tsx`. The Unmatched filter adds one boolean condition to `db.get_library_releases`'s existing conditions-list pattern, threaded through as a new query param, mirroring how the Store tab's `overlapping`/`recommended` filters already work.

**Tech Stack:** React + TypeScript (Vite), Tailwind CSS, Vitest + Testing Library, FastAPI, psycopg (Postgres), pytest.

Full design: [`docs/superpowers/specs/2026-08-02-plex-manual-link-and-ui-design.md`](../specs/2026-08-02-plex-manual-link-and-ui-design.md).

---

### Task 1: `db.py` / `routers/releases.py` — Unmatched filter

**Files:**
- Modify: `backend/db.py:476-516` (`get_library_releases`)
- Modify: `backend/routers/releases.py:9-24` (`list_releases`)
- Test: `backend/tests/test_catalog_crud.py`, `backend/tests/test_releases_router.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_catalog_crud.py`, after `test_get_library_releases_search_and_scope_filters` (after line 157):

```python
def test_get_library_releases_unmatched_filter(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    for rid in ("r1", "r2"):
        db.upsert_catalog_release(admin_conn, {
            "discogs_id": rid, "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True)
    db.upsert_library_item(admin_conn, alice["id"], "r2", in_collection=True)
    db.set_plex_match(admin_conn, alice["id"], "r1", "https://plex.local/album/1")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_library_releases(conn, alice["id"], unmatched=True)
    assert [r["discogs_id"] for r in result["releases"]] == ["r2"]

    with db.user_scope(alice["id"]) as conn:
        result = db.get_library_releases(conn, alice["id"], unmatched=False)
    assert {r["discogs_id"] for r in result["releases"]} == {"r1", "r2"}
```

Add to `backend/tests/test_releases_router.py`, after `test_list_releases_scope_wishlist` (after line 45):

```python
def test_list_releases_unmatched_filter(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        for rid in ("r1", "r2"):
            db.upsert_catalog_release(conn, {
                "discogs_id": rid, "artist": "A", "title": "T", "year": None, "label": None,
                "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
                "discogs_url": None,
            })
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True)
        db.upsert_library_item(conn, alice["id"], "r2", in_collection=True)
        db.set_plex_match(conn, alice["id"], "r1", "https://plex.local/album/1")
        conn.commit()

    client = authed_client_factory(alice["id"])
    r = client.get("/api/releases?unmatched=true")
    ids = {rel["discogs_id"] for rel in r.json()["releases"]}
    assert ids == {"r2"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test .venv/bin/pytest tests/test_catalog_crud.py tests/test_releases_router.py -k unmatched -v`
Expected: FAIL — `get_library_releases()` got an unexpected keyword argument `unmatched`; the router ignores the query param entirely.

- [ ] **Step 3: Add the `unmatched` param**

In `backend/db.py`, replace the `get_library_releases` signature and its `conditions` setup (lines 476-508):

```python
def get_library_releases(
    conn,
    user_id: int,
    search: Optional[str] = None,
    artist: Optional[str] = None,
    sort: str = "artist",
    order: str = "asc",
    page: int = 1,
    per_page: int = 50,
    release_id: Optional[str] = None,
    scope: Optional[str] = None,
    unmatched: bool = False,
) -> dict:
    order_sql = "DESC" if order.lower() == "desc" else "ASC"
    null_order = "ASC" if order_sql == "ASC" else "DESC"

    conditions = ["li.user_id = %(user_id)s"]
    params: dict = {"user_id": user_id}

    if release_id:
        conditions.append("c.discogs_id = %(release_id)s")
        params["release_id"] = release_id
    if search:
        conditions.append("(c.artist ILIKE %(search)s OR c.title ILIKE %(search)s)")
        params["search"] = f"%{search}%"
    if artist:
        conditions.append("c.artist = %(artist)s")
        params["artist"] = artist
    if scope == "collection":
        conditions.append("li.in_collection = TRUE")
    elif scope == "wishlist":
        conditions.append("li.in_wishlist = TRUE")
    if unmatched:
        conditions.append("li.plex_url IS NULL")

    where = "WHERE " + " AND ".join(conditions)
```

(Everything after the `where = ...` line stays exactly as-is.)

In `backend/routers/releases.py`, replace `list_releases` (lines 9-24):

```python
@router.get("/releases")
def list_releases(
    request: Request,
    search: Optional[str] = Query(None),
    artist: Optional[str] = Query(None),
    sort: str = Query("artist"),
    order: str = Query("asc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    scope: Optional[str] = Query(None),
    unmatched: bool = Query(False),
):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        return db.get_library_releases(
            conn, user_id, search=search, artist=artist, sort=sort,
            order=order, page=page, per_page=per_page, scope=scope,
            unmatched=unmatched,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test .venv/bin/pytest tests/test_catalog_crud.py tests/test_releases_router.py -v`
Expected: PASS (all cases, including the 2 new ones).

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test .venv/bin/pytest -q`
Expected: PASS, or only pre-existing, unrelated `test_crawl_manager.py` flakiness (async/timing-sensitive, known issue) — rerun once to confirm if you see 1-2 failures only there.

- [ ] **Step 6: Commit**

```bash
git add backend/db.py backend/routers/releases.py backend/tests/test_catalog_crud.py backend/tests/test_releases_router.py
git commit -m "feat: add unmatched filter to get_library_releases and GET /releases"
```

---

### Task 2: `crawl_manager.py` / `routers/plex.py` — manual Plex match trigger

**Files:**
- Modify: `backend/crawl_manager.py` (add `_plex_match_tasks`, `plex_match_running`, `start_plex_match`)
- Create: `backend/routers/plex.py`
- Modify: `backend/main.py` (register the new router)
- Test: `backend/tests/test_crawl_manager.py`, `backend/tests/test_plex_router.py` (new)

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_crawl_manager.py`, near the other Plex-match tests (after `test_sync_collection_skips_plex_match_when_unconfigured`, before the `_run_plex_match` section comment):

```python
async def test_start_plex_match_runs_when_configured(pg_schema):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute(
            "UPDATE users SET plex_base_url = %s, plex_token = %s WHERE id = %s",
            ["plex.local:32400", "ptok", user["id"]],
        )
        conn.commit()

    manager = CrawlManager()
    calls = []

    async def _fake_plex_match(user_id, base_url, token, threshold):
        calls.append((user_id, base_url, token, threshold))

    manager._run_plex_match = _fake_plex_match
    started = await manager.start_plex_match(user["id"])
    assert started is True
    await asyncio.sleep(0)
    assert calls == [(user["id"], "plex.local:32400", "ptok", 90)]


async def test_start_plex_match_returns_false_when_unconfigured(pg_schema):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()

    manager = CrawlManager()
    started = await manager.start_plex_match(user["id"])
    assert started is False
    assert manager.plex_match_running(user["id"]) is False


async def test_start_plex_match_returns_false_when_already_running(pg_schema):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute(
            "UPDATE users SET plex_base_url = %s, plex_token = %s WHERE id = %s",
            ["plex.local:32400", "ptok", user["id"]],
        )
        conn.commit()

    manager = CrawlManager()

    async def _never_finishes(user_id, base_url, token, threshold):
        await asyncio.sleep(10)

    manager._run_plex_match = _never_finishes
    assert await manager.start_plex_match(user["id"]) is True
    assert await manager.start_plex_match(user["id"]) is False
    manager._plex_match_tasks[user["id"]].cancel()


async def test_start_plex_match_returns_false_while_sync_running(pg_schema):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute(
            "UPDATE users SET plex_base_url = %s, plex_token = %s WHERE id = %s",
            ["plex.local:32400", "ptok", user["id"]],
        )
        conn.commit()

    manager = CrawlManager()

    async def _never_finishes(user_id, mode):
        await asyncio.sleep(10)

    manager._sync_collection = _never_finishes
    assert await manager.start_sync(user["id"], "all") is True
    assert await manager.start_plex_match(user["id"]) is False
    manager._sync_tasks[user["id"]].cancel()
```

Note: `asyncio` is already imported at the top of `test_crawl_manager.py` (used elsewhere in the file) — confirm this before adding, don't re-import.

Create `backend/tests/test_plex_router.py`:

```python
import pytest

import db
from crawl_manager import crawl_manager
from routers import plex as plex_router


@pytest.fixture
def authed_client_factory(authed_client_factory_builder):
    return authed_client_factory_builder([plex_router.router])


@pytest.fixture(autouse=True)
def reset_crawl_manager():
    crawl_manager._plex_match_tasks = {}
    yield
    for task in crawl_manager._plex_match_tasks.values():
        if task and not task.done():
            task.cancel()
    crawl_manager._plex_match_tasks = {}


def test_plex_match_start_uses_calling_user(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute(
            "UPDATE users SET plex_base_url = %s, plex_token = %s WHERE id = %s",
            ["plex.local:32400", "ptok", user["id"]],
        )
        conn.commit()
    client = authed_client_factory(user["id"])
    r = client.post("/api/plex/match/start", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200


def test_plex_match_start_returns_false_when_already_running_for_calling_user(
    pg_test_db, authed_client_factory, monkeypatch
):
    """Mirrors test_stock_judge_start_returns_false_when_already_running_for_calling_user
    in test_stock_router.py -- a bare TestClient(app) opens its own event loop per
    request, so a real asyncio.Task can't be observed across two separate
    client.post() calls here. Real per-user task-dict coverage lives in
    test_crawl_manager.py."""
    running_for: set = set()

    async def _fake_start_plex_match(user_id):
        if user_id in running_for:
            return False
        running_for.add(user_id)
        return True

    monkeypatch.setattr(crawl_manager, "start_plex_match", _fake_start_plex_match)
    monkeypatch.setattr(crawl_manager, "plex_match_running", lambda uid: uid in running_for)

    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(user["id"])

    r1 = client.post("/api/plex/match/start", headers={"X-Requested-With": "fetch"})
    assert r1.json()["started"] is True

    r2 = client.post("/api/plex/match/start", headers={"X-Requested-With": "fetch"})
    assert r2.json()["started"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test .venv/bin/pytest tests/test_crawl_manager.py -k plex_match_run -v tests/test_plex_router.py -v`
Expected: FAIL — `CrawlManager` has no attribute `start_plex_match`; `routers.plex` module doesn't exist.

- [ ] **Step 3: Add `_plex_match_tasks`/`plex_match_running`/`start_plex_match` to `CrawlManager`**

In `backend/crawl_manager.py`, find `self._judgment_tasks: dict[int, asyncio.Task] = {}` in `__init__` and add right after it:

```python
        self._plex_match_tasks: dict[int, asyncio.Task] = {}
```

Find `judgment_running`/`start_judgment_only` and add a sibling pair right after `start_sync` (after the existing `async def start_sync(self, user_id: int, mode: str = "all") -> bool:` method, before `async def _sync_collection`):

```python
    def plex_match_running(self, user_id: int) -> bool:
        task = self._plex_match_tasks.get(user_id)
        return task is not None and not task.done()

    async def start_plex_match(self, user_id: int) -> bool:
        if self.plex_match_running(user_id) or self.sync_running(user_id):
            return False
        from db import get_identity_pool
        with get_identity_pool().connection() as conn:
            user = conn.execute(
                "SELECT plex_base_url, plex_token, plex_match_threshold FROM users WHERE id = %s",
                [user_id],
            ).fetchone()
        if user is None or not user["plex_base_url"] or not user["plex_token"]:
            return False
        self._plex_match_tasks[user_id] = asyncio.create_task(
            self._run_plex_match(user_id, user["plex_base_url"], user["plex_token"], user["plex_match_threshold"])
        )
        return True
```

- [ ] **Step 4: Create the router**

Create `backend/routers/plex.py`:

```python
from fastapi import APIRouter, Request
from crawl_manager import crawl_manager

router = APIRouter()


@router.post("/plex/match/start")
async def start_plex_match(request: Request):
    user_id = request.state.user_id
    started = await crawl_manager.start_plex_match(user_id)
    return {"started": started, "running": crawl_manager.plex_match_running(user_id)}
```

- [ ] **Step 5: Register the router**

In `backend/main.py`, add `plex` to the import (line 11):

```python
from routers import collection, releases, settings, crawl, logs, screenshots, health, session, stock, plex
```

Add the include-router call next to `stock`'s (after line 109):

```python
app.include_router(plex.router, prefix="/api")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test .venv/bin/pytest tests/test_crawl_manager.py tests/test_plex_router.py -v`
Expected: PASS (all cases, including the new ones).

- [ ] **Step 7: Run the full backend suite**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test .venv/bin/pytest -q`
Expected: PASS, or only pre-existing `test_crawl_manager.py` flakiness unrelated to this change — rerun once to confirm.

- [ ] **Step 8: Commit**

```bash
git add backend/crawl_manager.py backend/routers/plex.py backend/main.py backend/tests/test_crawl_manager.py backend/tests/test_plex_router.py
git commit -m "feat: add manual Plex match trigger (CrawlManager.start_plex_match, POST /plex/match/start)"
```

---

### Task 3: `client.ts` — `getReleases` unmatched param, new `postPlexMatchStart`

**Files:**
- Modify: `frontend/src/api/client.ts:44-64` (`getReleases`)
- Modify: `frontend/src/api/client.ts` (add `postPlexMatchStart` near `postStockSyncStart`)
- Test: `frontend/src/test/client.test.ts`

- [ ] **Step 1: Write the failing tests**

Update the import line at the top of `frontend/src/test/client.test.ts` to also pull in `getReleases` and `postPlexMatchStart`:

```ts
import { postCrawlStart, getUserSettings, saveUserSettings, getStock, getStockArtists, getReleases, postPlexMatchStart } from '../api/client'
```

Add inside the existing `describe('crawl/user-settings client functions', ...)` block:

```ts
  it('getReleases includes unmatched when true', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ total: 0, page: 1, per_page: 50, releases: [] }) })
    await getReleases({ unmatched: true })
    expect(fetchMock.mock.calls[0][0]).toContain('unmatched=true')
  })

  it('getReleases omits unmatched when false or omitted', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ total: 0, page: 1, per_page: 50, releases: [] }) })
    await getReleases({})
    expect(fetchMock.mock.calls[0][0]).not.toContain('unmatched')
  })

  it('postPlexMatchStart posts to /plex/match/start', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ started: true, running: true }) })
    const result = await postPlexMatchStart()
    expect(fetchMock.mock.calls[0][0]).toContain('/plex/match/start')
    expect(fetchMock.mock.calls[0][1].method).toBe('POST')
    expect(result.started).toBe(true)
  })
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/test/client.test.ts`
Expected: FAIL — `unmatched` isn't a recognized param on `getReleases`'s type; `postPlexMatchStart` doesn't exist.

- [ ] **Step 3: Add `unmatched` to `getReleases`, add `postPlexMatchStart`**

In `frontend/src/api/client.ts`, replace `getReleases` (lines 44-64):

```ts
export async function getReleases(params: {
  search?: string
  artist?: string
  sort?: SortField
  order?: SortOrder
  page?: number
  per_page?: number
  scope?: RecordScope
  unmatched?: boolean
}): Promise<ReleasesResponse> {
  const q = new URLSearchParams()
  if (params.search) q.set('search', params.search)
  if (params.artist) q.set('artist', params.artist)
  if (params.sort) q.set('sort', params.sort)
  if (params.order) q.set('order', params.order)
  if (params.page) q.set('page', String(params.page))
  if (params.per_page) q.set('per_page', String(params.per_page))
  if (params.scope) q.set('scope', params.scope)
  if (params.unmatched) q.set('unmatched', 'true')
  const r = await apiFetch(`/releases?${q}`)
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}
```

Add `postPlexMatchStart` next to `postStockSyncStart`:

```ts
export async function postPlexMatchStart(): Promise<{ started: boolean; running: boolean }> {
  const r = await apiFetch('/plex/match/start', { method: 'POST' })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/test/client.test.ts`
Expected: PASS (all cases, including the 3 new ones).

- [ ] **Step 5: Run the full frontend suite**

Run: `cd frontend && npx vitest run`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/test/client.test.ts
git commit -m "feat: getReleases accepts unmatched, add postPlexMatchStart"
```

---

### Task 4: `RecordBrowser.tsx` — hyperlink swap + Unmatched filter

**Files:**
- Modify: `frontend/src/views/RecordBrowser.tsx`
- Test: `frontend/src/test/recordBrowser.test.tsx`

- [ ] **Step 1: Write the failing tests**

Add to `frontend/src/test/recordBrowser.test.tsx`, after the existing test at lines 28-32:

```tsx
  it('links the cover icon to Discogs and leaves the artist name as plain text, in tile view', async () => {
    getReleases.mockResolvedValue({
      total: 1, page: 1, per_page: 250,
      releases: [{
        discogs_id: 'r1', artist: 'Pink Floyd', title: 'The Wall', year: 1979, label: 'Harvest',
        format: 'Vinyl', discogs_price: null, cover_image_url: 'https://x/cover.jpg',
        discogs_url: 'https://discogs.com/r1', plex_url: null, plex_matched_at: null,
        last_synced: '', listings: {},
      }],
    })
    localStorage.setItem('collectionViewMode_collection', 'tiles')
    render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} />)
    const icon = await screen.findByAltText('The Wall')
    expect(icon.closest('a')?.getAttribute('href')).toBe('https://discogs.com/r1')
    const artistText = screen.getByText('Pink Floyd')
    expect(artistText.closest('a')).toBeNull()
  })

  it('links the cover icon to Discogs and leaves the artist name as plain text, in list view', async () => {
    getReleases.mockResolvedValue({
      total: 1, page: 1, per_page: 250,
      releases: [{
        discogs_id: 'r1', artist: 'Pink Floyd', title: 'The Wall', year: 1979, label: 'Harvest',
        format: 'Vinyl', discogs_price: null, cover_image_url: 'https://x/cover.jpg',
        discogs_url: 'https://discogs.com/r1', plex_url: null, plex_matched_at: null,
        last_synced: '', listings: {},
      }],
    })
    render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} />)
    const icon = await screen.findByAltText('The Wall')
    expect(icon.closest('a')?.getAttribute('href')).toBe('https://discogs.com/r1')
    const artistText = screen.getByText('Pink Floyd')
    expect(artistText.closest('a')).toBeNull()
  })

  it('shows the Unmatched filter dropdown for the collection scope but not wishlist', async () => {
    const { rerender } = render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    expect(screen.getByRole('combobox')).toBeInTheDocument()
    rerender(<RecordBrowser scope="wishlist" onRefreshPrices={() => {}} />)
    await waitFor(() => expect(screen.queryByRole('combobox')).not.toBeInTheDocument())
  })

  it('passes unmatched to getReleases when the filter is set to Unmatched', async () => {
    render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'unmatched' } })
    await waitFor(() => expect(getReleases).toHaveBeenLastCalledWith(expect.objectContaining({ unmatched: true })))
  })
```

Update the top of the file to import `fireEvent` alongside the existing `render, screen, waitFor`:

```tsx
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/test/recordBrowser.test.tsx`
Expected: FAIL — the icon has no `href`, the artist name is still a link, there's no combobox at all.

- [ ] **Step 3: Fix the tile-view hyperlinks**

In `frontend/src/views/RecordBrowser.tsx`, replace the tile-view release card (find by the `{releases.map((r) => (` inside the tiles grid, currently lines 214-241):

```tsx
                  <div key={r.discogs_id}>
                    <a href={r.discogs_url} target="_blank" rel="noreferrer">
                      {r.cover_image_url ? (
                        <img
                          src={r.cover_image_url}
                          alt={r.title}
                          className="w-full aspect-square object-cover rounded"
                        />
                      ) : (
                        <div className="w-full aspect-square bg-gray-800 rounded" />
                      )}
                    </a>
                    <div className="mt-1.5 text-sm text-gray-200 truncate">{r.artist}</div>
                    {r.plex_url ? (
                      <a
                        href={r.plex_url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-xs text-gray-400 truncate hover:text-indigo-400 block"
                      >
                        {r.title}
                      </a>
                    ) : (
                      <div className="text-xs text-gray-400 truncate">{r.title}</div>
                    )}
                  </div>
```

The artist `<div>` moves outside the `<a>` and drops `group-hover:text-indigo-400` (no longer inside a hover-linked group). The parent `<div>`'s `className="group"` is dropped too — grepping this file confirms `group-hover:*` was only ever used by this one link, so nothing else in the card still needs it.

- [ ] **Step 4: Fix the list-view hyperlinks**

Replace the icon `<td>` and Artist `<td>` (currently lines 321-341):

```tsx
                  <td className="px-3 py-2">
                    {r.cover_image_url ? (
                      <a href={r.discogs_url} target="_blank" rel="noreferrer">
                        <img
                          src={r.cover_image_url}
                          alt={r.title}
                          className="w-10 h-10 min-w-10 object-cover rounded"
                        />
                      </a>
                    ) : (
                      <a href={r.discogs_url} target="_blank" rel="noreferrer">
                        <div className="w-10 h-10 bg-gray-800 rounded" />
                      </a>
                    )}
                  </td>
                  <td className="px-3 py-2 text-gray-200">
                    {r.artist}
                  </td>
```

- [ ] **Step 5: Fix the now-stale assertion in `plexLink.test.tsx`**

`frontend/src/test/plexLink.test.tsx`'s tile-view test currently asserts the artist name IS a Discogs link — that's exactly the old behavior this task removes. Replace the whole `describe('Plex match hyperlink — tile view', ...)` block:

```tsx
describe('Plex match hyperlink — tile view', () => {
  it('links the tile title to Plex; artist is plain text, not a Discogs link', async () => {
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => (key.startsWith('collectionViewMode') ? 'tiles' : null),
      setItem: () => {},
    })
    render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} />)
    const titleLink = await screen.findByRole('link', { name: 'Kind of Blue' })
    expect(titleLink).toHaveAttribute('href', matchedRelease.plex_url as string)
    expect(screen.queryByRole('link', { name: /Miles Davis/ })).not.toBeInTheDocument()
  })
})
```

(This fixture's `cover_image_url` is `''` for both releases, so the icon renders as a plain background-color `<div>` with no accessible name — there's no `getByRole('link', {name...})` query available to also re-assert "icon links to Discogs" against this specific fixture. That's already covered with proper fixture data by the new tests added in Step 1.)

- [ ] **Step 6: Add the Unmatched filter state and dropdown**

Add state next to `viewMode` (near the top of the component body):

```tsx
  const [unmatched, setUnmatched] = useState(false)
```

Replace the `load` callback's `getReleases` call to include `unmatched`, and add `unmatched` to its dependency array:

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
        unmatched: scope === 'collection' ? unmatched : undefined,
      })
      setReleases(result.releases)
      setTotal(result.total)
    } finally {
      setLoading(false)
    }
  }, [search, selectedArtist, sort, order, page, scope, unmatched])
```

Add the dropdown as the first child inside the `ml-auto flex items-center gap-1` div (right before the List view button):

```tsx
            {scope === 'collection' && (
              <select
                value={unmatched ? 'unmatched' : 'all'}
                onChange={(e) => { setUnmatched(e.target.value === 'unmatched'); setPage(1) }}
                className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:border-indigo-500"
              >
                <option value="all">All</option>
                <option value="unmatched">Unmatched</option>
              </select>
            )}
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/test/recordBrowser.test.tsx src/test/plexLink.test.tsx`
Expected: PASS (all cases, including the 4 new ones in `recordBrowser.test.tsx` and the fixed tile-view test in `plexLink.test.tsx`).

- [ ] **Step 8: Run the full frontend suite**

Run: `cd frontend && npx vitest run`
Expected: PASS, no regressions.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/views/RecordBrowser.tsx frontend/src/test/recordBrowser.test.tsx frontend/src/test/plexLink.test.tsx
git commit -m "feat: RecordBrowser links the cover icon to Discogs, adds Unmatched filter"
```

---

### Task 5: `Account.tsx` — manual "Link Now" button

**Files:**
- Modify: `frontend/src/views/Account.tsx`
- Test: `frontend/src/test/account.test.tsx`

- [ ] **Step 1: Write the failing tests**

Add `postPlexMatchStart` to `frontend/src/test/account.test.tsx`'s hoisted mocks and `vi.mock` block:

```tsx
const { uploadAvatar, deleteAvatar, logout, getUserSettings, saveUserSettings, postPlexMatchStart } = vi.hoisted(() => ({
  uploadAvatar: vi.fn().mockResolvedValue(undefined),
  deleteAvatar: vi.fn().mockResolvedValue(undefined),
  logout: vi.fn().mockResolvedValue(undefined),
  getUserSettings: vi.fn().mockResolvedValue({ anthropic_api_key: '', recommendation_item_limit: 300, plex_base_url: '', plex_token: '', plex_match_threshold: 90 }),
  saveUserSettings: vi.fn().mockResolvedValue(undefined),
  postPlexMatchStart: vi.fn().mockResolvedValue({ started: true, running: true }),
}))

vi.mock('../api/client', () => ({
  uploadAvatar,
  deleteAvatar,
  logout,
  getUserSettings,
  saveUserSettings,
  postPlexMatchStart,
  avatarUrl: (v: number) => `/api/auth/avatar?v=${v}`,
}))
```

Add to the `describe('Account', ...)` block:

```tsx
  it('disables Link Now when Plex is not configured', async () => {
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    await waitFor(() => expect(getUserSettings).toHaveBeenCalled())
    expect(screen.getByRole('button', { name: 'Link Now' })).toBeDisabled()
  })

  it('enables Link Now once Plex is configured and calls postPlexMatchStart when clicked', async () => {
    getUserSettings.mockResolvedValueOnce({
      anthropic_api_key: '', recommendation_item_limit: 300,
      plex_base_url: 'https://plex.local:32400', plex_token: 'tok', plex_match_threshold: 90,
    })
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    const button = await screen.findByRole('button', { name: 'Link Now' })
    expect(button).not.toBeDisabled()
    fireEvent.click(button)
    await waitFor(() => expect(postPlexMatchStart).toHaveBeenCalledTimes(1))
  })
```

Confirm `fireEvent` is already imported at the top of this test file (it's used by the existing "shows a clean error message" test) — no import change needed if so.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/test/account.test.tsx`
Expected: FAIL — no button named "Link Now" exists yet.

- [ ] **Step 3: Add the button**

In `frontend/src/views/Account.tsx`, add the import:

```tsx
import { deleteAvatar, getUserSettings, logout, postPlexMatchStart, saveUserSettings, uploadAvatar } from '../api/client'
```

Add state next to `plexSaveError`:

```tsx
  const [plexMatchStarting, setPlexMatchStarting] = useState(false)
```

Add the handler next to `handleSaveUserSettings`:

```tsx
  async function handleLinkPlexNow() {
    setPlexMatchStarting(true)
    try {
      await postPlexMatchStart()
    } finally {
      setPlexMatchStarting(false)
    }
  }
```

Add a new row to the Plex section's `<table>`, right after the "Match threshold" row (before the closing `</tbody>` of that table, currently ending around line 269):

```tsx
            <tr>
              <td className="py-3 pr-4 text-left align-top whitespace-nowrap w-40"></td>
              <td className="py-3 pr-4 text-left align-top">
                <button
                  onClick={handleLinkPlexNow}
                  disabled={!plexBaseUrl || !plexToken || plexMatchStarting}
                  className="px-3 py-1 bg-indigo-700 hover:bg-indigo-600 active:bg-indigo-800 disabled:opacity-50 rounded text-xs font-medium transition-colors"
                >
                  {plexMatchStarting ? 'Starting…' : 'Link Now'}
                </button>
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                Re-run Plex matching against your current collection now, without waiting for the next sync.
              </td>
            </tr>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/test/account.test.tsx`
Expected: PASS (all cases, including the 2 new ones).

- [ ] **Step 5: Run the full frontend suite**

Run: `cd frontend && npx vitest run`
Expected: PASS, no regressions.

- [ ] **Step 6: Type-check and build**

Run: `cd frontend && npm run build`
Expected: succeeds. `rm -rf dist` afterward.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/views/Account.tsx frontend/src/test/account.test.tsx
git commit -m "feat: add Link Now button to Account's Plex section"
```

---

### Task 6: Manual verification

- [ ] **Step 1: Start the app**

```bash
cd backend && source .venv/bin/activate && uvicorn main:app --reload --port 8000 &
cd frontend && npm run dev
```

- [ ] **Step 2: Verify the hyperlink fix**

Open Collections, both list and tile view. Confirm clicking the cover icon opens the Discogs page in a new tab, and the artist name is no longer clickable. Confirm the title still links to the matched Plex album when one exists.

- [ ] **Step 3: Verify the Unmatched filter**

On the Collection tab, select "Unmatched" from the new dropdown. Confirm only releases without a Plex match appear. Switch to the Wishlist tab and confirm the dropdown isn't present there at all.

- [ ] **Step 4: Verify the manual Plex trigger**

Open Account, scroll to the Plex section. With no `plex_base_url`/`plex_token` saved, confirm "Link Now" is disabled. Save valid Plex settings, confirm the button becomes enabled. Click it, confirm the bottom status bar shows "Matching collection against Plex…" progress (same display the sync-triggered path already uses) and completes without needing to run a full collection sync first.
