# Store crawler per-crawler refresh button design

Date: 2026-08-07
Branch: `store-crawler-refresh-button`

## Problem

Settings' "Store Management" section has one bulk "Refresh" button that scans
every enabled catalog crawler (`POST /stock/sync/start` →
`crawl_manager.start_stock_sync()` → `_sync_stock()`, which loops over
`db.get_enabled_crawlers(conn, crawler_type="catalog")` sequentially in a
single `asyncio` task). There's no way to re-scan just one store without
running all of them. Admins want a per-row refresh action in the store
crawler table, in the same icon-button style already used for "Sync
collection from Discogs" (`RecordBrowser.tsx:205-213`), visible only to
admins, to the right of the existing "Crawl" (enable/disable) column.

## Scope

Touches:

- `backend/routers/stock.py` — extend `POST /stock/sync/start`, add
  `require_admin`.
- `backend/crawl_manager.py` — `start_stock_sync()` / `_sync_stock()` gain an
  optional crawler-id filter; broadcast payloads gain `crawler_id`.
- `frontend/src/views/Settings.tsx` — new "Refresh" column in the catalog
  crawler table.
- `frontend/src/App.tsx` — new state for which crawler (if any) is currently
  syncing; new handler; wiring for the shared busy lock.
- `frontend/src/api/client.ts`, `frontend/src/api/types.ts` — extend
  `postStockSyncStart`, `CrawlEvent`.
- Tests: `backend/tests/test_stock_router.py`, `test_crawl_manager.py`,
  `frontend/src/test/settings.test.tsx`.

Out of scope: the release/price crawler table (top "Crawler Management"
section) is unaffected — the refresh column is catalog-only. The scheduled
stock sync (`stock_schedule` cron) is unaffected — it always runs the bulk,
all-crawlers path.

## Decisions carried from brainstorming

- **Shared lock.** A per-store refresh and the bulk refresh contend for the
  same single `_stock_task` — only one catalog crawl runs at a time, whether
  bulk or single-store. No new concurrency tracking is introduced.
- **Enabled-only.** The refresh button is disabled for a crawler whose "Crawl"
  toggle shows "Disabled" — refreshing doesn't override that state.
- **Admin gating on the endpoint, not just the button.** `POST
  /stock/sync/start` currently has no `require_admin` dependency — any
  authenticated user can call it directly today even though the UI hides its
  button from non-admins. This change adds `require_admin` to the endpoint,
  closing that gap for both the existing bulk call and the new per-crawler
  call.

## Backend design

`backend/routers/stock.py`:

```python
class StockSyncStartRequest(BaseModel):
    crawler_id: Optional[int] = None

@router.post("/stock/sync/start", dependencies=[Depends(require_admin)])
async def start_stock_sync(body: StockSyncStartRequest = StockSyncStartRequest()):
    started = await crawl_manager.start_stock_sync(body.crawler_id)
    return {"started": started, "running": crawl_manager.stock_sync_running}
```

One route for both bulk and single-store, matching the existing
`POST /crawl/start` shape (`CrawlStartRequest.release_id`) rather than adding
a second endpoint.

`backend/crawl_manager.py`:

- `start_stock_sync(self, crawler_id: Optional[int] = None) -> bool` — same
  `if self.stock_sync_running: return False` guard as today; passes
  `crawler_id` through to the task.
- `_sync_stock(self, crawler_id: Optional[int] = None)`:
  - After `enabled = get_enabled_crawlers(conn, crawler_type="catalog")`,
    when `crawler_id is not None`, filter: `enabled = [c for c in enabled if
    c["id"] == crawler_id]`. An empty result (bad id, wrong type, or the
    crawler is disabled) hits the existing `"No enabled catalog crawlers"`
    error broadcast path unchanged — no new error message needed, since a
    single missing id and zero enabled crawlers are the same "nothing to do"
    case today.
  - `await self._broadcast({"status": "stock_sync_started", "crawler_id":
    crawler_id})` — `crawler_id` is `None` for a bulk run, unchanged for the
    frontend to treat as "no specific row."
  - `stock_sync_error` has three distinct emission sites today, and all three
    get `"crawler_id": crawler_id` (the originally-requested target, echoed
    back — not necessarily the crawler that failed) added for consistency:
    the `"No enabled catalog crawlers"` empty-list case, the per-crawler
    failure inside the `for crawler in crawlers` loop (which additionally
    keeps its existing `"source": crawler._db_site_name`), and the outer
    `except Exception` catch-all at the end of `_sync_stock`. The frontend
    doesn't actually need to distinguish between these three — see below —
    but leaving any of them without `crawler_id` would be an inconsistent,
    hard-to-notice gap.
  - `stock_sync_progress` is unchanged (no `crawler_id` added) — it already
    carries `source` (site name), and nothing in this feature's frontend
    design reads a progress event's crawler identity; adding an unused field
    there would be dead weight.
  - `await self._broadcast({"status": "stock_sync_complete", "synced":
    total_synced, "crawler_id": crawler_id})` — echoes back the original
    target (or `None`), for the same consistency reason as the error sites.
  - `stock_sync_aborted` (two consecutive 429s) is unchanged — with a
    single-crawler filter the loop only ever has one crawler in it, so this
    path can't trigger for a per-store refresh; no `crawler_id` needed there.

No change to `replace_stock_items`/`update_crawler_last_run` — they already
operate per-`crawler_id` and are safe to call for a filtered one-crawler run.

## Frontend design

`frontend/src/api/types.ts`: add `crawler_id?: number` to `CrawlEvent`.

`frontend/src/api/client.ts`:

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

`frontend/src/App.tsx`:

- New state: `const [stockSyncTarget, setStockSyncTarget] = useState<number | 'all' | null>(null)`.
- In the SSE handler: `stock_sync_started` sets `setStockSyncTarget(event.crawler_id ?? 'all')` (in addition to the existing `setSyncing(true)`/`setSyncStatus(...)`); `stock_sync_complete` and `stock_sync_aborted` each add `setStockSyncTarget(null)`.

**Amendment (found during final review, corrected before merge):** `stock_sync_error` is *not* always terminal — the per-crawler failure inside `_sync_stock`'s loop (the one carrying `"source"`, described above) lets the sync continue to the next crawler, eventually reaching `stock_sync_complete`. The first implementation cleared `stockSyncTarget` unconditionally on every `stock_sync_error`, which re-enabled every refresh button mid-bulk-sync on a single crawler's failure — exactly the silent-no-op the shared lock was meant to prevent. The fix: on `stock_sync_error`, only clear `stockSyncTarget` when `!event.source` — the two terminal emission sites ("no enabled catalog crawlers" and the outer catch-all) never carry `source`; only the non-terminal per-crawler one does.
- `handleRefreshStock` (existing bulk handler) is unchanged apart from now implicitly passing no `crawler_id`.
- New `handleRefreshStoreCrawler`:

```typescript
const handleRefreshStoreCrawler = useCallback(async (crawlerId: number) => {
  try {
    await postStockSyncStart(crawlerId)
  } catch (e: any) {
    setSyncStatus(`In-stock sync failed to start: ${e.message}`)
  }
}, [setSyncStatus])
```

- Pass to `Settings`: `stockSyncBusy={stockSyncTarget !== null}`,
  `stockSyncCrawlerId={typeof stockSyncTarget === 'number' ? stockSyncTarget : null}`,
  `onRefreshStoreCrawler={handleRefreshStoreCrawler}`.

`frontend/src/views/Settings.tsx`:

- New props: `stockSyncBusy: boolean`, `stockSyncCrawlerId: number | null`,
  `onRefreshStoreCrawler: (crawlerId: number) => void`.
- `renderCrawlerTable` takes a new parameter, `showRefresh: boolean`,
  `false` by default; only the catalog-crawler call site
  (`renderCrawlerTable(shownCatalogCrawlers, ..., true)`) passes `true`. The
  release-crawler call site is unchanged.
- Header row: when `isAdmin && showRefresh`, add `<th
  className="text-left py-2 w-24">Refresh</th>` immediately after the
  existing `Crawl` header.
- Body row: when `isAdmin && showRefresh`, add a cell using the same
  `navButtonClass` helper `RecordBrowser.tsx` uses for its collection-refresh
  icon button (imported from `../styles/buttons`, not reinvented):

```tsx
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
```

- The existing bulk "Refresh" button in the Store Management section gets
  `disabled={stockSyncBusy}` added — today it has no disabled state at all,
  so clicking it mid-sync is a silent backend no-op. This is the visible half
  of the "shared lock" decision: without it, an admin could click a per-store
  refresh, then also click the bulk button, and only backend logs would show
  the second click did nothing.

## Testing

- `backend/tests/test_stock_router.py` — new cases: `POST /stock/sync/start`
  as a non-admin returns 403 (covers both the bulk and single-crawler body
  shapes, since it's one route); a `crawler_id` given in the body is
  forwarded through to `crawl_manager.start_stock_sync` unchanged (verified
  by faking that method, mirroring this file's existing judgment-start
  tests — a real asyncio.Task can't be observed across two separate
  `TestClient` requests here).
- `backend/tests/test_crawl_manager.py` — new cases: `_sync_stock(crawler_id=X)`
  only calls `crawl_catalog()` on the matching crawler (both a plain
  `"catalog"` one and a `"catalog_browser"` one, in two separate tests —
  the latter added after the final review flagged that nothing guarded the
  `catalog_browser` half of the enabled-crawler union, the exact clause a
  stale plan read had deleted earlier in this branch's history) and calls
  `replace_stock_items`/`update_crawler_last_run` only for it; the
  `stock_sync_started` and `stock_sync_complete` broadcasts carry
  `crawler_id=X`; a `crawler_id` for a disabled/nonexistent crawler produces
  the same `stock_sync_error` ("No enabled catalog crawlers") as today's
  empty-list case, now with `crawler_id=X` attached.
- `frontend/src/test/settings.test.tsx` — new cases: refresh column/button
  renders only for catalog crawlers and only when `isAdmin`; button is
  `disabled` when the crawler is disabled or `stockSyncBusy`; clicking it
  calls `onRefreshStoreCrawler` with that crawler's id; the bulk Refresh
  button is `disabled` when `stockSyncBusy`.
- `frontend/src/test/inStockTab.test.tsx` — new case (added after the final
  review's Important finding): a non-terminal, `source`-carrying
  `stock_sync_error` mid-bulk-sync does not clear `stockSyncTarget` or
  re-enable the refresh buttons; a subsequent `stock_sync_complete` does.

Playwright-driven crawl behavior is unit-tested only insofar as it already
is (fixtures, mocked `crawl_catalog()`); no new fixture is needed since this
change doesn't touch any crawler's scraping logic.

## Runtime/agent document impact

No `.agents/INPUTS.md`, `.agents/OUTPUTS.md`, or `.agents/INSTRUCTIONS.md`
exist in this repo (confirmed absent, same as the monochrome-restyle spec
noted). This change adds an admin-triggered HTTP action, not a new external
trigger, input, or output shape — `/stock/sync/start` already exists as an
input surface; this only parameterizes it. No stack, golden-command, or
CI/CD change. `README.md` has no Settings-tab UI documentation to update.
No agent-facing documentation changes are needed.
