# Store "Recommended" Filter — UX Refinements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Four UX fixes surfaced by manual testing of the shipped Store "Recommended" filter: reliable judgment-run feedback (logging + no silent no-ops), a visible pressed state on action buttons, a less jarring first-load flash on the Store tab, and gating `Recommended` on having actually completed a judgment run (not just having a key configured), auto-falling-back if a run starts while it's selected.

**Architecture:** Backend logging/broadcast fixes live entirely in `CrawlManager._run_judgment_phase`. A new tiny `has_any_stock_judgment` DB helper + `GET /api/stock/judge/status` endpoint gives the frontend a durable (reload-safe) signal for "has judgment ever completed," combined in `App.tsx` with a new SSE-derived `judgmentRunning` flag into a single `recommendedAvailable` boolean passed down to `StockBrowser`.

**Tech Stack:** Same as the base feature — Python 3.9 / FastAPI / SQLite, React 19 / TypeScript / vitest.

**Spec:** [`docs/superpowers/specs/2026-07-06-store-recommended-filter-design.md`](../specs/2026-07-06-store-recommended-filter-design.md), Amendment 2 (2026-07-07) section.

---

## File Structure

| File | Change |
|---|---|
| `backend/crawl_manager.py` | `_run_judgment_phase` broadcasts+logs on the empty-unjudged path instead of silently returning; logs a per-batch progress line. |
| `backend/db.py` | New `has_any_stock_judgment(conn) -> bool`. |
| `backend/routers/stock.py` | New `GET /stock/judge/status` endpoint. |
| `frontend/src/api/client.ts` | New `getJudgmentStatus()`. |
| `frontend/src/views/Settings.tsx` | `active:` pressed state added to 4 buttons. |
| `frontend/src/views/StockBrowser.tsx` | Loading spinner; `hasAnthropicKey` prop renamed to `recommendedAvailable`; auto-fallback to `All` when it becomes unavailable while selected. |
| `frontend/src/App.tsx` | New `hasJudgedItems`/`judgmentRunning` state, `getJudgmentStatus()` fetch at startup, computed `recommendedAvailable` passed to `StockBrowser`. |

Tests: `backend/tests/test_crawl_manager.py`, `test_db.py`, `test_stock_router.py`; `frontend/src/test/stockBrowser.test.tsx`, `inStockTab.test.tsx`.

---

### Task 1: Reliable judgment-run logging and feedback

**Files:**
- Modify: `backend/crawl_manager.py`
- Test: `backend/tests/test_crawl_manager.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_crawl_manager.py`, in the "judgment phase" section (near the other `_run_judgment_phase`-exercising tests):

```python
async def test_run_judgment_phase_broadcasts_complete_when_nothing_unjudged(manager, tmp_config_dir, caplog):
    import config as cfg_module
    import db as db_module

    conn = sqlite3.connect(cfg_module.DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db_module.init_db(conn)

    with caplog.at_level("INFO", logger="crawl_manager"):
        await manager._run_judgment_phase(conn, "sk-ant-test")

    events = [e for e in manager.recent_events() if e["status"] == "stock_judgment_complete"]
    assert events == [{"status": "stock_judgment_complete", "judged": 0}]
    assert any("nothing to do" in r.message for r in caplog.records)
    conn.close()


async def test_run_judgment_phase_logs_per_batch_progress(manager, tmp_config_dir, monkeypatch, caplog):
    import config as cfg_module
    import db as db_module
    import recommendations
    from db import register_crawler, replace_stock_items

    conn = sqlite3.connect(cfg_module.DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db_module.init_db(conn)
    register_crawler(conn, "Nuclear Blast", "/path/nb.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Nuclear Blast'").fetchone()[0]
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "T1", "price": 1.0, "currency": "USD", "url": "https://x/1"},
        {"artist": "NAILS", "title": "T2", "price": 2.0, "currency": "USD", "url": "https://x/2"},
        {"artist": "Ghost", "title": "T3", "price": 3.0, "currency": "USD", "url": "https://x/3"},
    ])

    monkeypatch.setattr(recommendations, "BATCH_SIZE", 2)

    def _fake_judge(client, taste, batch):
        return [
            {"item_key": item["item_key"], "recommended": item["artist"] == "Rob Zombie", "reason": None}
            for item in batch
        ]

    monkeypatch.setattr(recommendations, "judge_batch", _fake_judge)

    with caplog.at_level("INFO", logger="crawl_manager"):
        await manager._run_judgment_phase(conn, "sk-ant-test")

    batch_logs = [r.message for r in caplog.records if "Judged batch" in r.message]
    assert len(batch_logs) == 2
    assert batch_logs[0].startswith("Judged batch 2/3:")
    assert batch_logs[1].startswith("Judged batch 3/3:")
    total_recommended_logged = sum(int(m.rsplit(":", 1)[1].split()[0]) for m in batch_logs)
    assert total_recommended_logged == 1
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_crawl_manager.py -k "nothing_unjudged or per_batch_progress" -v`
Expected: FAIL — the first test's `"nothing to do"` log assertion fails (current code returns before any log/broadcast at all, so `events == []` and `caplog.records` is empty); the second test's `batch_logs` assertion fails (`len(batch_logs) == 0`, no per-batch log line exists yet).

- [ ] **Step 3: Implement**

In `backend/crawl_manager.py`, replace `_run_judgment_phase` in full:

```python
    async def _run_judgment_phase(self, conn, api_key: str):
        from db import get_unjudged_stock_items, get_taste_listing, upsert_stock_judgments
        import recommendations
        import anthropic

        unjudged = get_unjudged_stock_items(conn, recommendations.SYNC_CAP)
        if not unjudged:
            await self._broadcast({"status": "stock_judgment_complete", "judged": 0})
            log.info("Stock judgment complete: 0 unjudged items, nothing to do")
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
                recommended_in_batch = 0
                if results:
                    upsert_stock_judgments(conn, results)
                    judged += len(results)
                    recommended_in_batch = sum(1 for r in results if r["recommended"])
                log.info("Judged batch %d/%d: %d recommended", judged, len(unjudged), recommended_in_batch)
                await self._broadcast({"status": "stock_judgment_progress", "judged": judged, "total": len(unjudged)})

            await self._broadcast({"status": "stock_judgment_complete", "judged": judged})
            log.info("Stock judgment complete: %d items judged", judged)
        except Exception as e:
            log.error("Stock judgment phase failed: %s", e, exc_info=True)
            await self._broadcast({"status": "stock_judgment_error", "error": str(e)})
```

The only changes from the current version: the empty-`unjudged` branch now broadcasts and logs before returning, and each batch iteration now computes `recommended_in_batch` and logs it. `BATCH_SIZE`, `SYNC_CAP`, and the overall control flow are unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_crawl_manager.py -k "nothing_unjudged or per_batch_progress" -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend && pytest -q`
Expected: PASS, no regressions. In particular, `test_sync_stock_updates_crawler_last_run` and `test_sync_stock_skips_judgment_when_no_api_key` don't configure an API key, so `_sync_stock`'s `if api_key:` guard means `_run_judgment_phase` is never called in those tests — unaffected by this change.

- [ ] **Step 6: Commit**

```bash
git add backend/crawl_manager.py backend/tests/test_crawl_manager.py
git commit -m "store-recommended-filter: log per-batch judgment progress and never silently no-op"
```

---

### Task 2: `has_any_stock_judgment` + `GET /api/stock/judge/status`

**Files:**
- Modify: `backend/db.py`
- Modify: `backend/routers/stock.py`
- Test: `backend/tests/test_db.py`
- Test: `backend/tests/test_stock_router.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_db.py` (near the other judgment-related tests, e.g. next to `upsert_stock_judgments` tests):

```python
def test_has_any_stock_judgment_false_when_empty(conn):
    assert has_any_stock_judgment(conn) is False


def test_has_any_stock_judgment_true_once_a_row_exists(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "T1", "price": 1.0, "currency": "USD", "url": "https://x/1"},
    ])
    key = compute_item_key("Rob Zombie", "T1", "https://x/1")
    conn.execute("INSERT INTO stock_item_judgments (item_key, recommended, reason) VALUES (?, 1, NULL)", [key])
    assert has_any_stock_judgment(conn) is True
```

Add `has_any_stock_judgment` to the existing `from db import (...)` block at the top of `backend/tests/test_db.py`.

Add to `backend/tests/test_stock_router.py`:

```python
def test_get_stock_judgment_status_false_when_empty(client):
    r = client.get("/api/stock/judge/status")
    assert r.status_code == 200
    assert r.json() == {"any_judged": False}


def test_get_stock_judgment_status_true_once_judged(client, conn):
    register_crawler(conn, "Nuclear Blast", "/path/nb.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name='Nuclear Blast'").fetchone()[0]
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "T1", "price": 1.0, "currency": "USD", "url": "https://x/1"},
    ])
    from db import compute_item_key
    key = compute_item_key("Rob Zombie", "T1", "https://x/1")
    conn.execute("INSERT INTO stock_item_judgments (item_key, recommended, reason) VALUES (?, 1, NULL)", [key])
    r = client.get("/api/stock/judge/status")
    assert r.json() == {"any_judged": True}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_db.py::test_has_any_stock_judgment_false_when_empty tests/test_db.py::test_has_any_stock_judgment_true_once_a_row_exists tests/test_stock_router.py::test_get_stock_judgment_status_false_when_empty tests/test_stock_router.py::test_get_stock_judgment_status_true_once_judged -v`
Expected: FAIL — `has_any_stock_judgment` doesn't exist (`ImportError`/`NameError`), and `/api/stock/judge/status` doesn't exist (404).

- [ ] **Step 3: Implement**

In `backend/db.py`, add right after `upsert_stock_judgments`:

```python
def has_any_stock_judgment(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT EXISTS(SELECT 1 FROM stock_item_judgments)").fetchone()[0] == 1
```

In `backend/routers/stock.py`, add `has_any_stock_judgment` to the existing `from db import ...` line:

```python
from db import get_connection, get_stock_items, get_distinct_stock_artists, has_any_stock_judgment
```

Add this endpoint right after `list_stock_artists`:

```python
@router.get("/stock/judge/status")
def get_stock_judgment_status():
    conn = get_connection()
    return {"any_judged": has_any_stock_judgment(conn)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_db.py tests/test_stock_router.py -v`
Expected: PASS (all existing + 4 new tests)

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend && pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add backend/db.py backend/routers/stock.py backend/tests/test_db.py backend/tests/test_stock_router.py
git commit -m "store-recommended-filter: add has_any_stock_judgment and GET /stock/judge/status"
```

---

### Task 3: Button pressed-state feedback

**Files:**
- Modify: `frontend/src/views/Settings.tsx`

- [ ] **Step 1: Implement**

In `frontend/src/views/Settings.tsx`, add `active:bg-indigo-800` to the className of these 4 buttons (each identified below by its surrounding `onClick` for disambiguation, since 3 share an identical className string):

1. Collection "Refresh Now":

```jsx
                <button
                  onClick={() => onRefreshCollection(settings.collection_schedule_mode as 'all' | 'new' ?? 'all')}
                  className="px-3 py-1 bg-indigo-700 hover:bg-indigo-600 active:bg-indigo-800 rounded text-xs font-medium transition-colors"
                >
                  Refresh Now
                </button>
```

2. Prices "Refresh Now":

```jsx
                <button
                  onClick={() => onRefreshPrices(settings.crawl_schedule_mode as 'missing' | 'all' ?? 'missing')}
                  className="px-3 py-1 bg-indigo-700 hover:bg-indigo-600 active:bg-indigo-800 rounded text-xs font-medium transition-colors"
                >
                  Refresh Now
                </button>
```

3. Stock "Refresh Now":

```jsx
                <button
                  onClick={onRefreshStock}
                  className="px-3 py-1 bg-indigo-700 hover:bg-indigo-600 active:bg-indigo-800 rounded text-xs font-medium transition-colors"
                >
                  Refresh Now
                </button>
```

4. "Refresh Recommendations" (already has `disabled:opacity-50`):

```jsx
                <button
                  onClick={onRefreshRecommendations}
                  disabled={!settings.anthropic_api_key}
                  className="px-3 py-1 bg-indigo-700 hover:bg-indigo-600 active:bg-indigo-800 disabled:opacity-50 rounded text-xs font-medium transition-colors"
                >
                  Refresh Recommendations
                </button>
```

Do not touch any other button in this file (e.g. "Change password," crawler login buttons) — out of scope per the spec.

- [ ] **Step 2: Verify types and existing tests are unaffected**

Run: `cd frontend && npx tsc -b --noEmit && npx vitest run`
Expected: no type errors; all existing tests pass unchanged (this is a pure CSS className addition, no behavior change).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/views/Settings.tsx
git commit -m "store-recommended-filter: add pressed-state feedback to refresh buttons"
```

---

### Task 4: Store tab loading spinner

**Files:**
- Modify: `frontend/src/views/StockBrowser.tsx`
- Test: `frontend/src/test/stockBrowser.test.tsx`

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/test/stockBrowser.test.tsx`, inside the `describe('StockBrowser', ...)` block:

```typescript
  it('shows a spinner alongside Loading… during the initial fetch', async () => {
    let resolveFetch: (v: any) => void = () => {}
    getStock.mockReturnValue(new Promise((resolve) => { resolveFetch = resolve }))
    render(<StockBrowser />)
    expect(screen.getByText('Loading…')).toBeTruthy()
    expect(document.querySelector('.animate-spin')).toBeTruthy()
    resolveFetch({ total: 0, page: 1, per_page: 250, items: [] })
    await waitFor(() => expect(screen.queryByText('Loading…')).toBeNull())
  })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/test/stockBrowser.test.tsx -t "shows a spinner"`
Expected: FAIL — `document.querySelector('.animate-spin')` is `null` (no spinner element exists in the loading branch yet).

- [ ] **Step 3: Implement**

In `frontend/src/views/StockBrowser.tsx`, replace the tile-view loading line:

```jsx
            {loading && <div className="text-center py-8 text-gray-500">Loading…</div>}
```

with:

```jsx
            {loading && (
              <div className="flex items-center justify-center gap-2 py-8 text-gray-500">
                <div className="w-4 h-4 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
                Loading…
              </div>
            )}
```

And replace the list-view loading line:

```jsx
              {loading && (
                <tr><td colSpan={6} className="text-center py-8 text-gray-500">Loading…</td></tr>
              )}
```

with:

```jsx
              {loading && (
                <tr><td colSpan={6} className="py-8 text-gray-500">
                  <div className="flex items-center justify-center gap-2">
                    <div className="w-4 h-4 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
                    Loading…
                  </div>
                </td></tr>
              )}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/test/stockBrowser.test.tsx`
Expected: PASS (all existing + the new test)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/StockBrowser.tsx frontend/src/test/stockBrowser.test.tsx
git commit -m "store-recommended-filter: show a spinner during the Store tab's initial load"
```

---

### Task 5: Frontend API client — `getJudgmentStatus`

**Files:**
- Modify: `frontend/src/api/client.ts`

- [ ] **Step 1: Implement**

In `frontend/src/api/client.ts`, add this function right after `postJudgmentStart` (before `openLogsStream`):

```typescript
export async function getJudgmentStatus(): Promise<{ any_judged: boolean }> {
  const r = await apiFetch('/stock/judge/status')
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}
```

- [ ] **Step 2: Verify types compile**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: no errors (unused-until-Task-6 export, same as `postJudgmentStart` was in the prior plan).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/client.ts
git commit -m "store-recommended-filter: add getJudgmentStatus API client function"
```

---

### Task 6: Gate `Recommended` on completed judgment + no active run

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/views/StockBrowser.tsx`
- Modify: `frontend/src/test/stockBrowser.test.tsx`
- Modify: `frontend/src/test/inStockTab.test.tsx`

- [ ] **Step 1: Write the failing tests**

In `frontend/src/test/stockBrowser.test.tsx`:

Rename every `<StockBrowser hasAnthropicKey ... />` / `<StockBrowser hasAnthropicKey={...} />` occurrence to use `recommendedAvailable` instead of `hasAnthropicKey` (8 occurrences: the bare `render(<StockBrowser hasAnthropicKey />)` calls at what are currently lines 109, 115, 122, 131, 141, and 153). Also rename the two test descriptions that reference "Anthropic key" directly:

- `'defaults to All, lists options in lexicographic order, and disables Recommended without an Anthropic key'` → `'defaults to All, lists options in lexicographic order, and disables Recommended when unavailable'`
- `'enables Recommended when an Anthropic key is configured'` → `'enables Recommended when recommendedAvailable is true'`

Add a new test inside `describe('StockBrowser', ...)`:

```typescript
  it('resets filter to All when recommendedAvailable becomes false while Recommended is selected', async () => {
    localStorage.setItem('stockFilter', 'recommended')
    const { rerender } = render(<StockBrowser recommendedAvailable />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('recommended')
    rerender(<StockBrowser recommendedAvailable={false} />)
    await waitFor(() => expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('all'))
  })
```

In `frontend/src/test/inStockTab.test.tsx`:

Add `getJudgmentStatus` to the mock setup. Find:

```typescript
const postStockSyncStart = vi.fn().mockResolvedValue({ started: true, running: true })
const postJudgmentStart = vi.fn().mockResolvedValue({ started: true, running: true })
const getSettings = vi.fn()
```

Replace with:

```typescript
const postStockSyncStart = vi.fn().mockResolvedValue({ started: true, running: true })
const postJudgmentStart = vi.fn().mockResolvedValue({ started: true, running: true })
const getSettings = vi.fn()
const getJudgmentStatus = vi.fn()
```

Find, inside the `vi.mock('../api/client', ...)` factory:

```typescript
  postStockSyncStart: (...args: unknown[]) => postStockSyncStart(...args),
  postJudgmentStart: (...args: unknown[]) => postJudgmentStart(...args),
```

Replace with:

```typescript
  postStockSyncStart: (...args: unknown[]) => postStockSyncStart(...args),
  postJudgmentStart: (...args: unknown[]) => postJudgmentStart(...args),
  getJudgmentStatus: (...args: unknown[]) => getJudgmentStatus(...args),
```

Find the `beforeEach` block:

```typescript
beforeEach(() => {
  MockEventSource.instances = []
  vi.clearAllMocks()
  postStockSyncStart.mockResolvedValue({ started: true, running: true })
  postJudgmentStart.mockResolvedValue({ started: true, running: true })
  getSettings.mockResolvedValue(defaultSettings)
})
```

Replace with:

```typescript
beforeEach(() => {
  MockEventSource.instances = []
  vi.clearAllMocks()
  postStockSyncStart.mockResolvedValue({ started: true, running: true })
  postJudgmentStart.mockResolvedValue({ started: true, running: true })
  getSettings.mockResolvedValue(defaultSettings)
  getJudgmentStatus.mockResolvedValue({ any_judged: false })
})
```

Add two new tests inside `describe('In Stock tab', ...)`:

```typescript
  it('enables Recommended in Store only once a key is configured and a judgment has completed', async () => {
    getSettings.mockResolvedValue({ ...defaultSettings, anthropic_api_key: 'sk-ant-test' })
    getJudgmentStatus.mockResolvedValue({ any_judged: true })
    render(<App />)
    await waitFor(() => expect(screen.getByText('Store')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Store'))
    await waitFor(() => {
      const option = screen.getByRole('option', { name: 'Recommended' }) as HTMLOptionElement
      expect(option.disabled).toBe(false)
    })
  })

  it('disables Recommended in Store again while a judgment run is in progress', async () => {
    getSettings.mockResolvedValue({ ...defaultSettings, anthropic_api_key: 'sk-ant-test' })
    getJudgmentStatus.mockResolvedValue({ any_judged: true })
    render(<App />)
    await waitFor(() => expect(screen.getByText('Store')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Store'))
    await waitFor(() => expect((screen.getByRole('option', { name: 'Recommended' }) as HTMLOptionElement).disabled).toBe(false))
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    const source = getLastCrawlSource()
    source.emit({ status: 'stock_judgment_started' })
    await waitFor(() => expect((screen.getByRole('option', { name: 'Recommended' }) as HTMLOptionElement).disabled).toBe(true))
  })
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/test/stockBrowser.test.tsx src/test/inStockTab.test.tsx`
Expected: FAIL — `StockBrowser` still takes `hasAnthropicKey`, not `recommendedAvailable` (so the renamed tests fail to find the expected disabled/enabled state, and the new reset-to-All test fails since there's no such effect yet). `getJudgmentStatus` already exists as a real export from Task 5, but `App.tsx` doesn't call it or compute `recommendedAvailable` yet, so the two new App-level tests fail on their assertions (Recommended stays disabled/enabled incorrectly), not on a missing-import error.

- [ ] **Step 3: Implement**

In `frontend/src/App.tsx`, add `getJudgmentStatus` to the existing `./api/client` import list.

Add two new state variables right after the existing `hasAnthropicKey` state:

```typescript
  const [hasJudgedItems, setHasJudgedItems] = useState(false)
  const [judgmentRunning, setJudgmentRunning] = useState(false)
```

In the startup poll effect, right after the existing `getSettings().then((s) => setHasAnthropicKey(Boolean(s.anthropic_api_key))).catch(() => {})` line, add:

```typescript
            getJudgmentStatus().then((s) => setHasJudgedItems(s.any_judged)).catch(() => {})
```

Update the SSE handler block for the four `stock_judgment_*` events:

```typescript
      if (event.status === 'stock_judgment_started') {
        setSyncing(true)
        setJudgmentRunning(true)
        setSyncMessage('Judging in-stock catalog against your collection…')
        return
      }
      if (event.status === 'stock_judgment_progress') {
        setSyncMessage(`Judging in-stock catalog… ${event.judged}/${event.total}`)
        return
      }
      if (event.status === 'stock_judgment_complete') {
        setSyncing(false)
        setJudgmentRunning(false)
        setHasJudgedItems(true)
        setSyncMessage(`Judged ${event.judged} new items for Recommended`)
        return
      }
      if (event.status === 'stock_judgment_error') {
        setSyncing(false)
        setJudgmentRunning(false)
        setSyncMessage(`Judgment failed: ${event.error}`)
        return
      }
```

Right before the `return (` that starts the JSX, compute:

```typescript
  const recommendedAvailable = hasAnthropicKey && hasJudgedItems && !judgmentRunning
```

Update the `<StockBrowser ... />` render call:

```jsx
        <div className={view === 'instock' ? 'h-full' : 'hidden'}>
          <StockBrowser recommendedAvailable={recommendedAvailable} />
        </div>
```

In `frontend/src/views/StockBrowser.tsx`, update the `Props` interface and function signature:

```typescript
interface Props {
  recommendedAvailable?: boolean
}

export default function StockBrowser({ recommendedAvailable = false }: Props) {
```

Update the `<option>`:

```jsx
            <option value="recommended" disabled={!recommendedAvailable}>Recommended</option>
```

Add a new `useEffect` right after the existing `useEffect(() => { load() }, [load])` line:

```typescript
  useEffect(() => {
    if (!recommendedAvailable && filter === 'recommended') {
      setFilter('all')
    }
  }, [recommendedAvailable, filter])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/test/stockBrowser.test.tsx src/test/inStockTab.test.tsx`
Expected: PASS (all existing + all new tests in both files)

- [ ] **Step 5: Run the full frontend suite and typecheck**

Run: `cd frontend && npx vitest run && npx tsc -b --noEmit`
Expected: all tests pass, zero type errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/App.tsx frontend/src/views/StockBrowser.tsx frontend/src/test/stockBrowser.test.tsx frontend/src/test/inStockTab.test.tsx
git commit -m "store-recommended-filter: gate Recommended on a completed judgment run, not just a configured key"
```

---

## Final Verification

- [ ] Run the full backend suite: `cd backend && pytest -v` — expect all green.
- [ ] Run the full frontend suite: `cd frontend && npx vitest run` — expect all green.
- [ ] Type-check the frontend: `cd frontend && npx tsc -b --noEmit` — expect no errors.
- [ ] Manual smoke test: click "Refresh Recommendations" in Settings when there's nothing unjudged — confirm the status bar and Logs tab both show something (not silence). Click it with a real backlog — confirm the Logs tab shows multiple "Judged batch N/M: K recommended" lines as it runs, not just start/complete. Confirm "Refresh Now"/"Refresh Recommendations" buttons visibly darken on click. Load the Store tab fresh — confirm the loading state shows a spinner, not bare text. Before ever running judgment, confirm `Recommended` is disabled in the Store filter dropdown even with a key configured; after a run completes, confirm it becomes selectable; start another run and confirm it's disabled again (and if it was selected, the filter visibly resets to `All`).
