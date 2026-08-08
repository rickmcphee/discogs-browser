# Store Crawler Refresh Button Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an admin-only, per-row "Refresh" icon button to the store (catalog) crawler table in Settings, to the right of the existing "Crawl" column, that re-scans just that one store's catalog instead of every enabled catalog crawler.

**Architecture:** Extend the existing single-task stock-sync mechanism (`crawl_manager._sync_stock`) to accept an optional `crawler_id` filter instead of adding new concurrency machinery — a per-store refresh and the existing bulk refresh contend for the same `_stock_task` lock. Thread that filter through one `POST /stock/sync/start` endpoint (now admin-gated) via an optional JSON body field, and echo it back on the SSE broadcasts so the frontend knows which row (if any) is mid-refresh.

**Tech Stack:** FastAPI + Pydantic + asyncio (backend), React 19 + TypeScript + Vitest + Testing Library (frontend), pytest + pytest-asyncio (backend tests).

## Global Constraints

- Spec: `docs/specifications/shaping/2026-08-07-store-crawler-refresh-button-design.md`.
- Shared lock: a per-store refresh and the bulk refresh use the same `_stock_task`/`stock_sync_running` — only one catalog crawl (bulk or single) runs at a time.
- Enabled-only: the per-row refresh button is disabled when that crawler's "Crawl" toggle shows "Disabled."
- `POST /stock/sync/start` gains `dependencies=[Depends(require_admin)]` — this closes a pre-existing gap (the route had no admin check at all before this plan) for both the bulk and new single-crawler call.
- No `.agents/INPUTS.md`, `.agents/OUTPUTS.md`, or `.agents/INSTRUCTIONS.md` exist in this repo, and this change parameterizes an existing HTTP action rather than adding a new trigger/input/output/external call — confirmed in the spec's "Runtime/agent document impact" section. No documentation tasks are included in this plan for that reason.
- Run backend commands from `backend/`: `pytest`.
- Run frontend commands from `frontend/`: `npm run test` (vitest run), `npm run build` (tsc -b && vite build), `npm run lint` (oxlint).
- Follow this repo's commit trailer rule (`CLAUDE.md`): every commit needs the AI-attribution trailer block via `commit-with-cleanup.sh`, not `git commit -m`.

---

### Task 1: `crawl_manager` — per-crawler stock sync filter

**Files:**
- Modify: `backend/crawl_manager.py:510-582`
- Test: `backend/tests/test_crawl_manager.py`

**Interfaces:**
- Produces: `CrawlManager.start_stock_sync(self, crawler_id: Optional[int] = None) -> bool`; `CrawlManager._sync_stock(self, crawler_id: Optional[int] = None)`. Broadcasts `stock_sync_started`/`stock_sync_error`/`stock_sync_complete` now each carry a `"crawler_id"` key (the value passed in, or `None` for a bulk run). `stock_sync_progress` is unchanged (no `crawler_id`).

**Amendment (found during Task 1's task review, corrected before merge):** this task's original text was written from a read of `crawl_manager.py` that predates a separately-merged PR (`store-crawler-angryyoungandpoor`, commit `15ee68b`), which added a `_run_catalog_crawler()` helper (Playwright page handling + one-retry-on-bot-detection for a new `catalog_browser` crawler type) and fetches both `"catalog"` and `"catalog_browser"` enabled crawlers in `_sync_stock`. The original Step 3 code block below deleted both — corrected below to preserve them, filtering the *combined* catalog + catalog_browser list by `crawler_id` rather than replacing `_run_catalog_crawler`'s call with a bare `crawler.crawl_catalog()` loop. Two more corrections folded in below: a fourth pre-existing test (`test_start_stock_sync_and_start_judgment_only_run_independently`, in the "judgment-only task" section of the test file, not the "stock sync task" section) also fakes `_sync_stock` with a zero-arg function and needs the same one-line fix as the three already listed in Step 1; and `test_sync_stock_with_unmatched_crawler_id_filters_out_all_crawlers`'s exact-equality assertion forgot that `_broadcast` attaches an auto-incrementing `"id"` to every event, so it never matched even correct code.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_crawl_manager.py`, immediately after `test_sync_stock_broadcasts_error_and_continues_when_a_crawler_fails` (currently ending at line 1128, right before the `sweep_enqueue` section comment):

```python
async def test_start_stock_sync_forwards_crawler_id_to_sync_stock(manager):
    calls = []

    async def _fake_sync(crawler_id=None):
        calls.append(crawler_id)

    manager._sync_stock = _fake_sync  # type: ignore
    await manager.start_stock_sync(42)
    await asyncio.sleep(0.01)
    assert calls == [42]


async def test_sync_stock_with_crawler_id_filters_to_that_crawler_only(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Site A", "/a.py", crawler_type="catalog")
        db.register_crawler(conn, "Site B", "/b.py", crawler_type="catalog")
        conn.commit()
        site_a_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Site A'").fetchone()["id"]

    loaded_rows = []

    def _fake_load(enabled_crawlers):
        loaded_rows.extend(enabled_crawlers)
        plugin = AsyncMock()

        async def _items():
            yield {"artist": "A", "title": "T", "url": "https://x/1", "price": 5.0, "currency": "USD"}

        plugin.crawl_catalog = lambda: _items()
        plugin._db_id = enabled_crawlers[0]["id"]
        plugin._db_site_name = enabled_crawlers[0]["site_name"]
        return [plugin]

    manager = CrawlManager()
    with patch("crawler.load_enabled_crawlers", side_effect=_fake_load):
        await manager._sync_stock(crawler_id=site_a_id)

    # Only Site A's row was ever handed to the loader -- Site B, though
    # equally enabled, must never be touched by a single-crawler refresh.
    assert [row["id"] for row in loaded_rows] == [site_a_id]

    events = [(e["status"], e.get("crawler_id")) for e in manager.recent_events()]
    assert events == [
        ("stock_sync_started", site_a_id),
        ("stock_sync_progress", None),
        ("stock_sync_complete", site_a_id),
    ]


async def test_sync_stock_with_unmatched_crawler_id_filters_out_all_crawlers(pg_schema):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Site A", "/a.py", crawler_type="catalog")
        conn.commit()
        site_a_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Site A'").fetchone()["id"]

    loaded_rows = []

    def _fake_load(enabled_crawlers):
        loaded_rows.extend(enabled_crawlers)
        return []

    manager = CrawlManager()
    with patch("crawler.load_enabled_crawlers", side_effect=_fake_load):
        # Site A is enabled, but this id doesn't match it -- the filter must
        # exclude Site A rather than falling back to "sync everything."
        await manager._sync_stock(crawler_id=site_a_id + 1)

    assert loaded_rows == []
    # recent_events() entries also carry an auto-incrementing "id" from
    # _broadcast -- project it away rather than asserting exact dicts.
    events = [{k: v for k, v in e.items() if k != "id"} for e in manager.recent_events()]
    assert events == [
        {"status": "stock_sync_started", "crawler_id": site_a_id + 1},
        {"status": "stock_sync_error", "error": "No enabled catalog crawlers", "crawler_id": site_a_id + 1},
    ]
```

Also update `test_start_stock_sync_and_start_judgment_only_run_independently`, an existing test in the "judgment-only task" section further down the same file (not one of the three already covered above), whose `_fake_sync_stock` is zero-arg. Change:

```python
    async def _fake_sync_stock():
        await stock_event.wait()
```

to:

```python
    async def _fake_sync_stock(crawler_id=None):
        await stock_event.wait()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_crawl_manager.py -k "forwards_crawler_id or filters_to_that_crawler_only or filters_out_all_crawlers" -v`
Expected: FAIL — `TypeError: CrawlManager._sync_stock() got an unexpected keyword argument 'crawler_id'` (and the same for `start_stock_sync`).

- [ ] **Step 3: Implement the crawler_id filter**

In `backend/crawl_manager.py`, replace the block from `async def start_stock_sync` through the end of `_sync_stock` — this now spans `start_stock_sync`, `_run_catalog_crawler` (added by `store-crawler-angryyoungandpoor`, kept as-is below), and `_sync_stock` — with:

```python
    async def start_stock_sync(self, crawler_id: Optional[int] = None) -> bool:
        if self.stock_sync_running:
            log.warning("Stock sync already running, ignoring start request")
            return False
        self._stock_task = asyncio.create_task(self._sync_stock(crawler_id))
        return True

    async def _run_catalog_crawler(self, crawler) -> list[dict]:
        """Runs crawler.crawl_catalog(), handling the catalog_browser kind's
        Playwright page + one-retry-on-BotDetectedError convention (same as
        the release-crawl path's _paced_search). Plain catalog crawlers keep
        calling crawl_catalog() zero-arg, unchanged."""
        from crawler import _new_context, _reset_context, BotDetectedError

        if crawler.crawler_type != "catalog_browser":
            return [item async for item in crawler.crawl_catalog()]

        context, page = await _new_context(self._browser, self._stealth)
        try:
            try:
                return [item async for item in crawler.crawl_catalog(page)]
            except BotDetectedError:
                context, page = await _reset_context(context, self._browser, self._stealth, None)
                return [item async for item in crawler.crawl_catalog(page)]
        finally:
            await context.close()

    async def _sync_stock(self, crawler_id: Optional[int] = None):
        import httpx
        from db import get_app_pool, get_enabled_crawlers, replace_stock_items, update_crawler_last_run
        from crawler import load_enabled_crawlers

        await self._broadcast({"status": "stock_sync_started", "crawler_id": crawler_id})
        log.info("Stock sync started")
        try:
            with get_app_pool().connection() as conn:
                enabled = (
                    get_enabled_crawlers(conn, crawler_type="catalog")
                    + get_enabled_crawlers(conn, crawler_type="catalog_browser")
                )
            if crawler_id is not None:
                enabled = [c for c in enabled if c["id"] == crawler_id]
            crawlers = load_enabled_crawlers(enabled)
            if not crawlers:
                await self._broadcast({
                    "status": "stock_sync_error",
                    "error": "No enabled catalog crawlers",
                    "crawler_id": crawler_id,
                })
                return

            total_synced = 0
            consecutive_429_sites: list[str] = []
            for crawler in crawlers:
                try:
                    items = await self._run_catalog_crawler(crawler)
                except Exception as e:
                    is_rate_limited = isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 429
                    if is_rate_limited:
                        log.warning("[%s] Stock crawl rate-limited (HTTP 429): %s", crawler._db_site_name, e)
                        consecutive_429_sites.append(crawler._db_site_name)
                    else:
                        log.error("[%s] Stock crawl failed: %s", crawler._db_site_name, e, exc_info=True)
                        await self._broadcast({
                            "status": "stock_sync_error",
                            "error": str(e),
                            "source": crawler._db_site_name,
                            "crawler_id": crawler_id,
                        })
                        consecutive_429_sites = []
                    if len(consecutive_429_sites) >= 2:
                        log.warning(
                            "Stock sync aborted: %d catalog sites in a row hit HTTP 429 (%s) -- "
                            "likely a platform-wide rate limit, not grinding the rest of the run into it",
                            len(consecutive_429_sites), ", ".join(consecutive_429_sites),
                        )
                        await self._broadcast({
                            "status": "stock_sync_aborted",
                            "error": "Too many consecutive rate-limited catalog sites",
                            "sources": list(consecutive_429_sites),
                        })
                        return
                    continue

                consecutive_429_sites = []
                with get_app_pool().connection() as conn:
                    replace_stock_items(conn, crawler._db_id, items)
                    update_crawler_last_run(conn, crawler._db_id)
                    conn.commit()
                total_synced += len(items)
                log.info("[%s] Stock sync found %d items", crawler._db_site_name, len(items))
                await self._broadcast({"status": "stock_sync_progress", "synced": total_synced, "source": crawler._db_site_name})

            await self._broadcast({"status": "stock_sync_complete", "synced": total_synced, "crawler_id": crawler_id})
            log.info("Stock sync complete: %d items", total_synced)
        except asyncio.CancelledError:
            log.info("Stock sync cancelled")
            raise
        except Exception as e:
            log.error("Stock sync failed: %s", e, exc_info=True)
            await self._broadcast({"status": "stock_sync_error", "error": str(e), "crawler_id": crawler_id})
```

(`Optional` is already imported at the top of `crawl_manager.py` — no new import needed. `_run_catalog_crawler`'s body is unchanged from `store-crawler-angryyoungandpoor` — it's reproduced here only because it sits between the two methods that do change, not because anything about it is new to this task.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_crawl_manager.py -v`
Expected: PASS (the whole file — the three new tests, the pre-existing stock-sync tests that call `_sync_stock()` with no arguments, the four `_run_catalog_crawler` tests, and `test_start_stock_sync_and_start_judgment_only_run_independently`'s now-updated fake)

- [ ] **Step 5: Commit**

```bash
git add backend/crawl_manager.py backend/tests/test_crawl_manager.py
```

Commit message body (via `commit-with-cleanup.sh`):

```
feat: filter stock sync to a single crawler when crawler_id is given

Summary:
=======
First step of the per-store refresh button (see
docs/specifications/plans/2026-08-07-store-crawler-refresh-button.md).
Lets a caller target one catalog crawler instead of always syncing
every enabled one, while keeping the existing single _stock_task lock
shared between bulk and single-crawler runs.

Actions:
=======
- Add an optional crawler_id parameter to start_stock_sync/_sync_stock
  that filters the enabled catalog crawlers down to one row
- Echo crawler_id back on stock_sync_started/error/complete broadcasts
- Add three tests covering the forward-through, the filter itself, and
  the disabled/nonexistent-id case

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: cli
ai-executor: remote-agent
```

---

### Task 2: `POST /stock/sync/start` — accept `crawler_id`, require admin

**Files:**
- Modify: `backend/routers/stock.py:1-8,74-77`
- Test: `backend/tests/test_stock_router.py`

**Interfaces:**
- Consumes: `crawl_manager.start_stock_sync(crawler_id: Optional[int] = None) -> bool`, `crawl_manager.stock_sync_running: bool` (Task 1).
- Produces: `POST /stock/sync/start` — admin-only, accepts an optional JSON body `{"crawler_id": int}` (omitted or `null` means bulk).

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_stock_router.py`, after the imports at the top of the file (the `crawl_manager` singleton is already imported there — see line 4 — so no new import is needed for these tests):

```python
def test_post_stock_sync_start_requires_admin(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(user["id"])

    r = client.post("/api/stock/sync/start", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 403

    r = client.post("/api/stock/sync/start", json={"crawler_id": 1}, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 403


def test_post_stock_sync_start_forwards_crawler_id_as_admin(pg_test_db, authed_client_factory, monkeypatch):
    # start_stock_sync is faked rather than driven for real, mirroring
    # test_stock_judge_start_returns_false_when_already_running_for_calling_user's
    # rationale: a bare TestClient(app) opens its own event loop per
    # request, so a real asyncio.Task can't be observed across two
    # separate client.post() calls here.
    calls = []

    async def _fake_start_stock_sync(crawler_id=None):
        calls.append(crawler_id)
        return True

    monkeypatch.setattr(crawl_manager, "start_stock_sync", _fake_start_stock_sync)

    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute("UPDATE users SET is_admin = TRUE WHERE id = %s", [user["id"]])
        conn.commit()
    client = authed_client_factory(user["id"])

    r = client.post("/api/stock/sync/start", json={"crawler_id": 42}, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    assert r.json() == {"started": True, "running": False}

    r = client.post("/api/stock/sync/start", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200

    assert calls == [42, None]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_stock_router.py -k "requires_admin or forwards_crawler_id" -v`
Expected: FAIL — the first test fails with `assert 200 == 403` (no admin gate yet); the second fails because `start_stock_sync()` is called with no arguments today, so `_fake_start_stock_sync`'s `crawler_id` param stays at its default and `calls == [42, None]` never gets the `42` — actually it'll raise nothing but the assertion `calls == [42, None]` fails since the route never passes the body's `crawler_id` through (`calls == [None, None]`).

- [ ] **Step 3: Implement the endpoint change**

In `backend/routers/stock.py`, change the imports (lines 1-6) from:

```python
import csv
import io
from fastapi import APIRouter, Query, Request, Response
from typing import Optional
import db
from crawl_manager import crawl_manager
```

to:

```python
import csv
import io
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel
from typing import Optional
import db
from admin import require_admin
from crawl_manager import crawl_manager
```

Then replace the existing route (currently lines 74-77):

```python
@router.post("/stock/sync/start")
async def start_stock_sync():
    started = await crawl_manager.start_stock_sync()
    return {"started": started, "running": crawl_manager.stock_sync_running}
```

with:

```python
class StockSyncStartRequest(BaseModel):
    crawler_id: Optional[int] = None


@router.post("/stock/sync/start", dependencies=[Depends(require_admin)])
async def start_stock_sync(body: StockSyncStartRequest = StockSyncStartRequest()):
    started = await crawl_manager.start_stock_sync(body.crawler_id)
    return {"started": started, "running": crawl_manager.stock_sync_running}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_stock_router.py -v`
Expected: PASS (all tests in the file, including `test_clear_stock_judgment_refuses_while_stock_sync_running`, which only reads `crawl_manager.stock_sync_running` and isn't affected by this route change)

- [ ] **Step 5: Commit**

```bash
git add backend/routers/stock.py backend/tests/test_stock_router.py
```

Commit message body:

```
feat: admin-gate POST /stock/sync/start and accept a crawler_id body

Summary:
=======
Second step of the per-store refresh button (see
docs/specifications/plans/2026-08-07-store-crawler-refresh-button.md).
This endpoint previously had no admin check at all -- any authenticated
user could trigger a full catalog re-crawl even though the UI only
shows its button to admins. Adding require_admin here closes that for
both the existing bulk call and the new single-crawler call, which
share this one route via an optional body field (mirrors
POST /crawl/start's release_id shape).

Actions:
=======
- Add StockSyncStartRequest(crawler_id: Optional[int] = None) body model
- Add dependencies=[Depends(require_admin)] to the route
- Add two tests: non-admin gets 403 (bulk and single-crawler body), and
  the crawler_id is forwarded through to crawl_manager.start_stock_sync

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: cli
ai-executor: remote-agent
```

---

### Task 3: Frontend API layer — `CrawlEvent.crawler_id` and `postStockSyncStart(crawlerId?)`

**Files:**
- Modify: `frontend/src/api/types.ts:65-90`
- Modify: `frontend/src/api/client.ts:182-186`
- Test: `frontend/src/test/client.test.ts`

**Interfaces:**
- Produces: `CrawlEvent.crawler_id?: number`; `postStockSyncStart(crawlerId?: number): Promise<{ started: boolean; running: boolean }>` — POSTs `{"crawler_id": crawlerId}` (omitted when `crawlerId` is `undefined`, since `JSON.stringify` drops `undefined`-valued keys).

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/test/client.test.ts`, after the existing `import` line (which already imports several client functions — add `postStockSyncStart` to that same import list):

```ts
import { postCrawlStart, postStockSyncStart, getUserSettings, saveUserSettings, logout, getStock, getStockArtists, getReleases, postPlexMatchStart } from '../api/client'
```

Then add, inside the existing `describe('crawl/user-settings client functions', ...)` block, right after the `postCrawlStart returns enqueued count` test:

```ts
  it('postStockSyncStart posts an empty crawler_id for a bulk call', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ started: true, running: true }) })
    await postStockSyncStart()
    expect(fetchMock.mock.calls[0][0]).toContain('/stock/sync/start')
    expect(fetchMock.mock.calls[0][1].method).toBe('POST')
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({})
  })

  it('postStockSyncStart posts the given crawler_id for a single-store call', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ started: true, running: true }) })
    await postStockSyncStart(7)
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ crawler_id: 7 })
  })
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/test/client.test.ts`
Expected: FAIL — `TypeError: postStockSyncStart is not a function` (it's not exported with a parameter yet — the current signature takes none, so calling `postStockSyncStart(7)` doesn't fail, but `JSON.parse(fetchMock.mock.calls[0][1].body)` throws because the current implementation sends no `body` at all)

- [ ] **Step 3: Implement**

In `frontend/src/api/types.ts`, in the `CrawlEvent` interface (currently ending with `matched?: number` on line 89), add a new field:

```ts
  matched?: number
  crawler_id?: number
```

In `frontend/src/api/client.ts`, replace the current `postStockSyncStart`:

```typescript
export async function postStockSyncStart(): Promise<{ started: boolean; running: boolean }> {
  const r = await apiFetch('/stock/sync/start', { method: 'POST' })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}
```

with:

```typescript
export async function postStockSyncStart(crawlerId?: number): Promise<{ started: boolean; running: boolean }> {
  const r = await apiFetch('/stock/sync/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ crawler_id: crawlerId }),
  })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend && npx vitest run src/test/client.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/types.ts frontend/src/api/client.ts frontend/src/test/client.test.ts
```

Commit message body:

```
feat: let postStockSyncStart target a single crawler

Summary:
=======
Third step of the per-store refresh button (see
docs/specifications/plans/2026-08-07-store-crawler-refresh-button.md).
Frontend counterpart of Task 2's backend body field.

Actions:
=======
- Add crawler_id?: number to CrawlEvent
- postStockSyncStart(crawlerId?) now posts a JSON body carrying it

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: cli
ai-executor: remote-agent
```

---

### Task 4: `Settings.tsx` — per-row Refresh button for store crawlers

**Files:**
- Modify: `frontend/src/views/Settings.tsx`
- Test: `frontend/src/test/settings.test.tsx`

**Interfaces:**
- Consumes: `navButtonClass(isActive: boolean): string` from `../styles/buttons` (already used elsewhere in the frontend, e.g. `RecordBrowser.tsx`).
- Produces: three new `Settings` props — `stockSyncBusy: boolean`, `stockSyncCrawlerId: number | null`, `onRefreshStoreCrawler: (crawlerId: number) => void` — consumed by `App.tsx` in Task 5.

- [ ] **Step 1: Write the failing tests**

In `frontend/src/test/settings.test.tsx`, add a second crawler fixture array after the existing `CRAWLERS` constant (do not modify `CRAWLERS` itself — several existing tests assert exact counts against it):

```ts
const CATALOG_CRAWLERS_WITH_DISABLED: Crawler[] = [
  ...CRAWLERS,
  { id: 4, site_name: 'Disabled Catalog', module_path: '', crawler_type: 'catalog', enabled: false, last_run: null, base_url: null },
]
```

Update the `renderSettings` helper's default props to include the three new ones:

```tsx
function renderSettings(overrides: Partial<ComponentProps<typeof Settings>> = {}) {
  return render(
    <Settings
      crawlers={[]}
      onCrawlersChange={() => {}}
      onRefreshPrices={() => {}}
      onRefreshStock={() => {}}
      onRefreshRecommendations={() => {}}
      onClearRecommendations={() => {}}
      hasJudgedItems={false}
      isAdmin
      hiddenCrawlerIds={[]}
      onToggleCrawlerView={() => {}}
      stockSyncBusy={false}
      stockSyncCrawlerId={null}
      onRefreshStoreCrawler={() => {}}
      {...overrides}
    />
  )
}
```

Then add these tests inside the `describe('Settings', ...)` block, after the existing `'still shows View toggles to a non-admin'` test:

```tsx
  it('shows a per-row Refresh button only for catalog crawlers, and only to an admin', async () => {
    renderSettings({ crawlers: CRAWLERS })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    const amazonRow = screen.getByText('Amazon').closest('tr') as HTMLElement // release crawler
    const epitaphRow = screen.getByText('Epitaph').closest('tr') as HTMLElement // catalog crawler
    expect(within(amazonRow).queryByTitle(/Refresh .* catalog now/)).not.toBeInTheDocument()
    expect(within(epitaphRow).getByTitle('Refresh Epitaph catalog now')).toBeInTheDocument()
  })

  it('does not show the per-row Refresh button to a non-admin', async () => {
    renderSettings({ crawlers: CRAWLERS, isAdmin: false })
    const epitaphRow = screen.getByText('Epitaph').closest('tr') as HTMLElement
    expect(within(epitaphRow).queryByTitle('Refresh Epitaph catalog now')).not.toBeInTheDocument()
  })

  it('disables the per-row Refresh button for a disabled catalog crawler', async () => {
    renderSettings({ crawlers: CATALOG_CRAWLERS_WITH_DISABLED })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    const disabledRow = screen.getByText('Disabled Catalog').closest('tr') as HTMLElement
    expect(within(disabledRow).getByTitle('Refresh Disabled Catalog catalog now')).toBeDisabled()
  })

  it('disables every per-row Refresh button while a stock sync is running', async () => {
    renderSettings({ crawlers: CATALOG_CRAWLERS_WITH_DISABLED, stockSyncBusy: true })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    expect(screen.getByTitle('Refresh Epitaph catalog now')).toBeDisabled()
  })

  it('calls onRefreshStoreCrawler with that crawler\'s id when its Refresh button is clicked', async () => {
    const onRefreshStoreCrawler = vi.fn()
    renderSettings({ crawlers: CRAWLERS, onRefreshStoreCrawler })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    fireEvent.click(screen.getByTitle('Refresh Epitaph catalog now'))
    expect(onRefreshStoreCrawler).toHaveBeenCalledWith(3)
  })

  it('disables the bulk Store Management Refresh button while a stock sync is running', async () => {
    renderSettings({ crawlers: CRAWLERS, stockSyncBusy: true })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    const description = await screen.findByText('Scan all enabled catalog crawlers immediately.')
    const row = description.closest('tr') as HTMLElement
    expect(within(row).getByText('Refresh')).toBeDisabled()
  })
```

`within` is already imported at the top of this file (a separately-merged PR added a `within`-using test here first) — no import change needed.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npx vitest run src/test/settings.test.tsx`
Expected: FAIL — the new tests fail (`getByTitle(...)` finds nothing; `Settings` doesn't accept `stockSyncBusy`/`stockSyncCrawlerId`/`onRefreshStoreCrawler` yet, so TypeScript will also flag unknown props if you run the build, but vitest alone will fail at the `getByTitle`/`toHaveBeenCalledWith` assertions)

- [ ] **Step 3: Implement the refresh column**

In `frontend/src/views/Settings.tsx`, change the import on line 4:

```tsx
import { primaryButtonClass } from '../styles/buttons'
```

to:

```tsx
import { navButtonClass, primaryButtonClass } from '../styles/buttons'
```

Add the three new props to the `Props` interface (currently lines 43-54):

```tsx
interface Props {
  crawlers: Crawler[]
  onCrawlersChange: (crawlers: Crawler[]) => void
  onRefreshPrices: (mode: 'missing' | 'all') => void
  onRefreshStock: () => void
  onRefreshRecommendations: () => void
  onClearRecommendations: () => void
  hasJudgedItems: boolean
  isAdmin: boolean
  hiddenCrawlerIds: number[]
  onToggleCrawlerView: (crawlerId: number) => void
  stockSyncBusy: boolean
  stockSyncCrawlerId: number | null
  onRefreshStoreCrawler: (crawlerId: number) => void
}
```

Add the same three names to the function's destructured parameters (currently lines 62-65):

```tsx
function Settings({
  crawlers, onCrawlersChange, onRefreshPrices, onRefreshStock, onRefreshRecommendations,
  onClearRecommendations, hasJudgedItems, isAdmin, hiddenCrawlerIds, onToggleCrawlerView,
  stockSyncBusy, stockSyncCrawlerId, onRefreshStoreCrawler,
}: Props) {
```

Replace `renderCrawlerTable` (currently lines 127-178):

```tsx
  function renderCrawlerTable(crawlerList: Crawler[], emptyMessage: string) {
    if (crawlerList.length === 0) {
      return <p className="text-gray-500 text-sm text-left mt-4">{emptyMessage}</p>
    }
    return (
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
          {crawlerList.map((c) => (
            <tr key={c.id} className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left text-gray-200 font-medium">
                {c.base_url
                  ? <a href={c.base_url} target="_blank" rel="noreferrer"
                       className="text-gray-400 hover:text-white underline">{c.site_name}</a>
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
  }
```

with:

```tsx
  function renderCrawlerTable(crawlerList: Crawler[], emptyMessage: string, showRefresh = false) {
    if (crawlerList.length === 0) {
      return <p className="text-gray-500 text-sm text-left mt-4">{emptyMessage}</p>
    }
    return (
      <table className="w-full text-sm border-collapse mt-4">
        <thead>
          <tr className="text-xs text-gray-500 uppercase tracking-wider border-b border-gray-800">
            <th className="text-left py-2 pr-4 w-40">Site</th>
            {isAdmin && <th className="text-left py-2 pr-4 w-48">Last run</th>}
            <th className="text-left py-2 pr-4">View</th>
            {isAdmin && <th className="text-left py-2 pr-4">Crawl</th>}
            {isAdmin && showRefresh && <th className="text-left py-2 w-24">Refresh</th>}
          </tr>
        </thead>
        <tbody>
          {crawlerList.map((c) => (
            <tr key={c.id} className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left text-gray-200 font-medium">
                {c.base_url
                  ? <a href={c.base_url} target="_blank" rel="noreferrer"
                       className="text-gray-400 hover:text-white underline">{c.site_name}</a>
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
                <td className="py-3 pr-4 text-left">
                  <button
                    onClick={() => handleToggleCrawler(c)}
                    className={toggleButtonClass(c.enabled)}
                  >
                    {c.enabled ? 'Enabled' : 'Disabled'}
                  </button>
                </td>
              )}
              {isAdmin && showRefresh && (
                <td className="py-3 text-left">
                  <button
                    onClick={() => onRefreshStoreCrawler(c.id)}
                    disabled={!c.enabled || stockSyncBusy}
                    title={`Refresh ${c.site_name} catalog now`}
                    className={`p-1.5 disabled:opacity-30 disabled:cursor-not-allowed ${navButtonClass(false)}`}
                  >
                    <span className="block text-base leading-none">
                      {stockSyncCrawlerId === c.id ? '⟳' : '↻'}
                    </span>
                  </button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    )
  }
```

(Note: the `Crawl` header cell's className gains a `pr-4` it didn't have before, matching every other header cell in the row now that it's no longer the last column when `showRefresh` is true — this is a cosmetic no-op when `showRefresh` is false, since there's no visible gap to the right of the last column either way.)

Update the two call sites. The release-crawler call (currently line 298):

```tsx
        {renderCrawlerTable(shownReleaseCrawlers, 'No crawlers configured.')}
```

is unchanged (still 2-arg, `showRefresh` defaults to `false`). The catalog-crawler call (currently line 346):

```tsx
        {renderCrawlerTable(shownCatalogCrawlers, 'No catalog crawlers configured.')}
```

becomes:

```tsx
        {renderCrawlerTable(shownCatalogCrawlers, 'No catalog crawlers configured.', true)}
```

Finally, add `disabled={stockSyncBusy}` to the bulk Store Management "Refresh" button (currently lines 332-337):

```tsx
                  <button
                    onClick={onRefreshStock}
                    className={`px-3 py-1 text-xs ${primaryButtonClass()}`}
                  >
                    Refresh
                  </button>
```

becomes:

```tsx
                  <button
                    onClick={onRefreshStock}
                    disabled={stockSyncBusy}
                    className={`px-3 py-1 text-xs disabled:opacity-50 ${primaryButtonClass()}`}
                  >
                    Refresh
                  </button>
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/test/settings.test.tsx`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/Settings.tsx frontend/src/test/settings.test.tsx
```

Commit message body:

```
feat: add per-store Refresh button to the catalog crawler table

Summary:
=======
Fourth step of the per-store refresh button (see
docs/specifications/plans/2026-08-07-store-crawler-refresh-button.md).
Admin-only icon button to the right of the Crawl column, styled like
the existing collection-refresh icon button, disabled for a crawler
that's off or while any stock sync is already running.

Actions:
=======
- Add stockSyncBusy/stockSyncCrawlerId/onRefreshStoreCrawler props
- renderCrawlerTable takes a showRefresh flag, true only for the
  catalog-crawler table
- Disable the existing bulk Store Management Refresh button while
  stockSyncBusy, making the shared-lock decision visible rather than a
  silent backend no-op

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: cli
ai-executor: remote-agent
```

---

### Task 5: `App.tsx` — track the running crawler and wire the handler

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/test/inStockTab.test.tsx`

**Interfaces:**
- Consumes: `postStockSyncStart(crawlerId?: number)` (Task 3); `Settings`'s `stockSyncBusy`, `stockSyncCrawlerId`, `onRefreshStoreCrawler` props (Task 4).

- [ ] **Step 1: Write the failing tests**

In `frontend/src/test/inStockTab.test.tsx`, the mock factory currently hard-codes `getCrawlers: vi.fn().mockResolvedValue([])`, which can't be overridden per test. Change it to a hoisted, per-test-controllable mock like `getSettings`/`getUserSettings` already are. Replace this line near the top of the file:

```tsx
const getSettings = vi.fn()
const getUserSettings = vi.fn()
const getJudgmentStatus = vi.fn()
```

with:

```tsx
const getSettings = vi.fn()
const getUserSettings = vi.fn()
const getJudgmentStatus = vi.fn()
const getCrawlers = vi.fn()
```

and inside the `vi.mock('../api/client', () => ({ ... }))` factory, replace:

```tsx
  getCrawlers: vi.fn().mockResolvedValue([]),
```

with:

```tsx
  getCrawlers: (...args: unknown[]) => getCrawlers(...args),
```

and in `beforeEach`, alongside the existing `getSettings.mockResolvedValue(...)` line, add a default:

```tsx
  getCrawlers.mockResolvedValue([])
```

Then add these tests inside `describe('In Stock tab', ...)`, after the existing `'calls postStockSyncStart when Refresh is clicked in Settings'` test:

```tsx
  const CATALOG_CRAWLER = { id: 9, site_name: 'Epitaph', module_path: '', crawler_type: 'catalog', enabled: true, last_run: null, base_url: null }

  it('calls postStockSyncStart with that crawler\'s id when its per-row Refresh button is clicked', async () => {
    getCrawlers.mockResolvedValue([CATALOG_CRAWLER])
    render(<App />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Settings' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }))
    const button = await screen.findByTitle('Refresh Epitaph catalog now')
    fireEvent.click(button)
    await waitFor(() => expect(postStockSyncStart).toHaveBeenCalledWith(9))
  })

  it('disables the bulk Refresh and every per-row Refresh button once a stock sync starts', async () => {
    getCrawlers.mockResolvedValue([CATALOG_CRAWLER])
    render(<App />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Settings' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }))
    const rowButton = await screen.findByTitle('Refresh Epitaph catalog now')
    const description = await screen.findByText('Scan all enabled catalog crawlers immediately.')
    const bulkButton = within(description.closest('tr') as HTMLElement).getByText('Refresh')

    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    getLastCrawlSource().emit({ status: 'stock_sync_started', crawler_id: 9, id: 1 })

    await waitFor(() => expect(rowButton).toBeDisabled())
    expect(bulkButton).toBeDisabled()
  })

  it('re-enables the buttons and stops spinning the row once the single-crawler sync completes', async () => {
    getCrawlers.mockResolvedValue([CATALOG_CRAWLER])
    render(<App />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Settings' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }))
    const rowButton = await screen.findByTitle('Refresh Epitaph catalog now')

    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    const source = getLastCrawlSource()
    source.emit({ status: 'stock_sync_started', crawler_id: 9, id: 1 })
    await waitFor(() => expect(rowButton).toBeDisabled())

    source.emit({ status: 'stock_sync_complete', synced: 5, crawler_id: 9, id: 2 })
    await waitFor(() => expect(rowButton).not.toBeDisabled())
  })
```

`within` is already imported at the top of this file (`import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'`) — no import change needed here.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npx vitest run src/test/inStockTab.test.tsx`
Expected: FAIL — `screen.findByTitle('Refresh Epitaph catalog now')` times out (no such button exists yet; `Settings` isn't wired with the new props from `App.tsx`)

- [ ] **Step 3: Implement the App.tsx wiring**

Add a new state declaration in `frontend/src/App.tsx`, right after `const [syncGeneration, setSyncGeneration] = useState(0)` (currently line 56):

```tsx
  const [syncGeneration, setSyncGeneration] = useState(0)
  const [stockSyncTarget, setStockSyncTarget] = useState<number | 'all' | null>(null)
```

In the SSE event handler, update the four `stock_sync_*` branches (currently lines 162-186):

```tsx
      if (event.status === 'stock_sync_started') {
        setSyncing(true)
        setSyncStatus('Syncing in-stock catalog…', event.id ?? null)
        return
      }
      if (event.status === 'stock_sync_progress') {
        setSyncStatus(`Syncing in-stock catalog… ${event.synced} items (${event.source})`, event.id ?? null)
        return
      }
      if (event.status === 'stock_sync_complete') {
        setSyncing(false)
        setSyncStatus(`In-stock sync complete: ${event.synced} items`, event.id ?? null)
        return
      }
      if (event.status === 'stock_sync_error') {
        setSyncing(false)
        setSyncStatus(`In-stock sync failed: ${event.error}`, event.id ?? null)
        return
      }
      if (event.status === 'stock_sync_aborted') {
        setSyncing(false)
        const sources = event.sources?.length ? ` (${event.sources.join(', ')})` : ''
        setSyncStatus(`In-stock sync stopped: ${event.error}${sources}`, event.id ?? null)
        return
      }
```

to:

```tsx
      if (event.status === 'stock_sync_started') {
        setSyncing(true)
        setStockSyncTarget(event.crawler_id ?? 'all')
        setSyncStatus('Syncing in-stock catalog…', event.id ?? null)
        return
      }
      if (event.status === 'stock_sync_progress') {
        setSyncStatus(`Syncing in-stock catalog… ${event.synced} items (${event.source})`, event.id ?? null)
        return
      }
      if (event.status === 'stock_sync_complete') {
        setSyncing(false)
        setStockSyncTarget(null)
        setSyncStatus(`In-stock sync complete: ${event.synced} items`, event.id ?? null)
        return
      }
      if (event.status === 'stock_sync_error') {
        setSyncing(false)
        setStockSyncTarget(null)
        setSyncStatus(`In-stock sync failed: ${event.error}`, event.id ?? null)
        return
      }
      if (event.status === 'stock_sync_aborted') {
        setSyncing(false)
        setStockSyncTarget(null)
        const sources = event.sources?.length ? ` (${event.sources.join(', ')})` : ''
        setSyncStatus(`In-stock sync stopped: ${event.error}${sources}`, event.id ?? null)
        return
      }
```

Add a new handler right after `handleRefreshStock` (currently lines 312-318):

```tsx
  const handleRefreshStock = useCallback(async () => {
    try {
      await postStockSyncStart()
    } catch (e: any) {
      setSyncStatus(`In-stock sync failed to start: ${e.message}`)
    }
  }, [setSyncStatus])

  const handleRefreshStoreCrawler = useCallback(async (crawlerId: number) => {
    try {
      await postStockSyncStart(crawlerId)
    } catch (e: any) {
      setSyncStatus(`In-stock sync failed to start: ${e.message}`)
    }
  }, [setSyncStatus])
```

Finally, pass the three new props to `<Settings>` (currently lines 495-503):

```tsx
          <Settings
            crawlers={crawlers}
            onCrawlersChange={setCrawlers}
            onRefreshPrices={handleRefreshPricesFromSettings}
            onRefreshStock={handleRefreshStock}
            onRefreshRecommendations={handleRefreshRecommendations}
            onClearRecommendations={handleClearRecommendations}
            hasJudgedItems={hasJudgedItems}
            isAdmin={showAdminNav}
            hiddenCrawlerIds={hiddenCrawlerIds}
            onToggleCrawlerView={toggleCrawlerView}
          />
```

becomes:

```tsx
          <Settings
            crawlers={crawlers}
            onCrawlersChange={setCrawlers}
            onRefreshPrices={handleRefreshPricesFromSettings}
            onRefreshStock={handleRefreshStock}
            onRefreshRecommendations={handleRefreshRecommendations}
            onClearRecommendations={handleClearRecommendations}
            hasJudgedItems={hasJudgedItems}
            isAdmin={showAdminNav}
            hiddenCrawlerIds={hiddenCrawlerIds}
            onToggleCrawlerView={toggleCrawlerView}
            stockSyncBusy={stockSyncTarget !== null}
            stockSyncCrawlerId={typeof stockSyncTarget === 'number' ? stockSyncTarget : null}
            onRefreshStoreCrawler={handleRefreshStoreCrawler}
          />
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/test/inStockTab.test.tsx`
Expected: PASS (all tests in the file, including every pre-existing one — none of them assert on `getCrawlers`' return value, so defaulting it to `[]` in `beforeEach` preserves their current behavior)

- [ ] **Step 5: Run the full frontend test suite**

Run: `cd frontend && npm run test`
Expected: PASS (all test files)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/App.tsx frontend/src/test/inStockTab.test.tsx
```

Commit message body:

```
feat: wire the per-store refresh button through App.tsx

Summary:
=======
Fifth and final frontend step of the per-store refresh button (see
docs/specifications/plans/2026-08-07-store-crawler-refresh-button.md).
Tracks which crawler (if any) is mid-sync from the started/complete/
error/aborted SSE events, so Settings can spin exactly one row and
disable every refresh control -- bulk and per-store -- while it runs.

Actions:
=======
- Add stockSyncTarget state (number | 'all' | null), set from
  stock_sync_started's crawler_id, cleared on every terminal event
- Add handleRefreshStoreCrawler, mirroring handleRefreshStock
- Pass stockSyncBusy/stockSyncCrawlerId/onRefreshStoreCrawler to Settings
- Make getCrawlers overridable per-test in inStockTab.test.tsx and add
  three tests for the new button/disabled/spinner behavior

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: cli
ai-executor: remote-agent
```

---

### Task 6: Full-repo verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend test suite**

Run: `cd backend && pytest`
Expected: all tests PASS

- [ ] **Step 2: Run the full frontend test suite**

Run: `cd frontend && npm run test`
Expected: all test files PASS

- [ ] **Step 3: Run the TypeScript build**

Run: `cd frontend && npm run build`
Expected: exits 0, no type errors

- [ ] **Step 4: Run the linter**

Run: `cd frontend && npm run lint`
Expected: exits 0, no lint errors

- [ ] **Step 5: Manual verification**

Run the backend (`cd backend && pip install -e ".[dev]" && uvicorn main:app --reload --port 8000`) and frontend (`cd frontend && npm install && npm run dev`) per `CLAUDE.md`'s "Running" section, log in as an admin user, and in Settings → Store Management:

- Confirm a "Refresh" column now appears to the right of "Crawl" for every catalog crawler row, with an `↻` icon button.
- Click one store's `↻`. Confirm it turns into `⟳`, the bulk "Refresh" button and every other store's `↻` button become disabled, and the bottom status bar shows "Syncing in-stock catalog…".
- Wait for it to finish. Confirm the icon reverts to `↻`, all buttons re-enable, and the status bar shows a completion message.
- Toggle a store's "Crawl" to "Disabled". Confirm its `↻` button is now disabled too.
- Log in as (or switch to) a non-admin user. Confirm no "Refresh" column appears anywhere in Settings.

Stop both dev servers (Ctrl-C) when done.

- [ ] **Step 6: Bump the version per `CLAUDE.md`'s versioning rule**

Read `backend/version.py`, increment the minor version, following the existing pattern in that file.

- [ ] **Step 7: Commit the version bump**

```bash
git add backend/version.py
```

Commit message body:

```
chore: bump version for store crawler refresh button

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: cli
ai-executor: remote-agent
```

---

## Self-Review Notes

- **Spec coverage:** every decision in the spec's "Decisions carried from brainstorming" section has a corresponding implementation point — shared lock (Task 1's single `_stock_task`, Task 4's `stockSyncBusy` disabling both bulk and per-row buttons), enabled-only (Task 4's `disabled={!c.enabled || stockSyncBusy}`), and admin-gating on the endpoint (Task 2's `require_admin`, plus Task 4/5's admin-only column). The spec's exact broadcast-payload plan (which of `stock_sync_started`/`error`/`complete`/`progress` gain `crawler_id`) is followed exactly in Task 1, including leaving `stock_sync_progress` untouched.
- **Type consistency:** `stockSyncBusy`, `stockSyncCrawlerId`, and `onRefreshStoreCrawler` are defined with identical names and types in Task 4's `Settings` `Props` interface and consumed with the same names in Task 5's `App.tsx` JSX. `postStockSyncStart(crawlerId?: number)` (Task 3) is called identically in Task 5's `handleRefreshStoreCrawler`. `showRefresh` (Task 4) is a plain boolean parameter, not exposed outside `Settings.tsx`.
- **Scope:** one subsystem end to end (backend sync filter → endpoint → frontend API layer → UI → wiring), ordered so each task's tests pass standalone before the next task depends on it. No decomposition needed.
