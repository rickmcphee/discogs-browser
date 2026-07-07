# Store "Recommended" Filter — Refinements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Three refinements to the already-merged Store "Recommended" filter: never suggest an item the user already owns, rewrite the judgment prompt so reasons read as factual item descriptions (not "matches your collection" phrasing) and move it into its own file, and add a way to re-run judgment without a full 13-source stock re-crawl.

**Architecture:** A single reusable SQL exclusion clause (artist + title-prefix match against `in_collection = 1`) applied at both judgment time (`get_unjudged_stock_items`) and read time (`get_stock_items`/`get_distinct_stock_artists`). The judgment prompt moves from a Python string constant into `backend/recommendations_prompt.md`, loaded once at import. `CrawlManager` gains a second, independently-triggerable entry point (`start_judgment_only`) that runs the existing `_run_judgment_phase` without the catalog crawl, mutually exclusive with the full stock sync.

**Tech Stack:** Same as the base feature — Python 3.9 / FastAPI / SQLite, React 19 / TypeScript / vitest.

**Spec:** [`docs/superpowers/specs/2026-07-06-store-recommended-filter-design.md`](../specs/2026-07-06-store-recommended-filter-design.md), Amendment (2026-07-07) section.

---

## File Structure

| File | Change |
|---|---|
| `backend/db.py` | New `_NOT_OWNED_CLAUSE` constant; applied in `get_unjudged_stock_items`, `get_stock_items`, `get_distinct_stock_artists`. |
| `backend/recommendations_prompt.md` | New. The judgment system prompt, moved out of Python source. |
| `backend/recommendations.py` | `SYSTEM_PROMPT` now loads from the new file instead of an inline string. |
| `backend/crawl_manager.py` | New `judgment_running` property, `start_judgment_only()`, `_run_judgment_only()`; `start_stock_sync()` gains a mutual-exclusion check. |
| `backend/routers/stock.py` | New `POST /stock/judge/start` endpoint. |
| `frontend/src/api/client.ts` | New `postJudgmentStart()`. |
| `frontend/src/App.tsx` | New `handleRefreshRecommendations`, threaded to `Settings`. |
| `frontend/src/views/Settings.tsx` | New "Refresh Recommendations" button in Store Management, disabled without a configured key. |

Tests: `backend/tests/test_db.py`, `test_recommendations.py`, `test_crawl_manager.py`, `test_stock_router.py`; `frontend/src/test/inStockTab.test.tsx`.

---

### Task 1: Exclude already-owned items

**Files:**
- Modify: `backend/db.py`
- Test: `backend/tests/test_db.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_db.py` (near the other `get_unjudged_stock_items`/`get_stock_items`/`get_distinct_stock_artists` tests):

```python
def test_get_unjudged_stock_items_excludes_owned_item(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    upsert_release(conn, _release(discogs_id="r1", artist="Rob Zombie", title="The Great Satan"))
    mark_in_collection(conn, "r1")
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "The Great Satan — Ghostly Black Vinyl", "format": "Vinyl", "price": 1.0, "currency": "USD", "url": "https://x/1"},
        {"artist": "NAILS", "title": "Every Bridge Burning", "format": "Vinyl", "price": 2.0, "currency": "USD", "url": "https://x/2"},
    ])
    unjudged = get_unjudged_stock_items(conn, limit=10)
    assert [u["artist"] for u in unjudged] == ["Nails"]


def test_get_unjudged_stock_items_wishlist_match_not_excluded(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    upsert_release(conn, _release(discogs_id="r1", artist="Rob Zombie", title="The Great Satan"))
    mark_in_wishlist(conn, "r1")
    mark_not_in_collection(conn, "r1")
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "The Great Satan — Ghostly Black Vinyl", "format": "Vinyl", "price": 1.0, "currency": "USD", "url": "https://x/1"},
    ])
    unjudged = get_unjudged_stock_items(conn, limit=10)
    assert [u["artist"] for u in unjudged] == ["Rob Zombie"]


def test_get_unjudged_stock_items_different_title_same_artist_not_excluded(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    upsert_release(conn, _release(discogs_id="r1", artist="NAILS", title="Abandon All Life"))
    mark_in_collection(conn, "r1")
    replace_stock_items(conn, crawler_id, [
        {"artist": "NAILS", "title": "Every Bridge Burning", "format": "Vinyl", "price": 2.0, "currency": "USD", "url": "https://x/2"},
    ])
    unjudged = get_unjudged_stock_items(conn, limit=10)
    assert [u["artist"] for u in unjudged] == ["Nails"]


def test_get_stock_items_recommended_excludes_owned_item_even_if_judged_recommended(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    upsert_release(conn, _release(discogs_id="r1", artist="Rob Zombie", title="The Great Satan"))
    mark_in_collection(conn, "r1")
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "The Great Satan — Ghostly Black Vinyl", "format": "Vinyl", "price": 1.0, "currency": "USD", "url": "https://x/1"},
    ])
    key = compute_item_key("Rob Zombie", "The Great Satan — Ghostly Black Vinyl", "https://x/1")
    conn.execute("INSERT INTO stock_item_judgments (item_key, recommended, reason) VALUES (?, 1, 'similar genre')", [key])
    result = get_stock_items(conn, recommended=True)
    assert result["total"] == 0


def test_get_distinct_stock_artists_recommended_excludes_owned_artist_when_only_match_is_owned(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    upsert_release(conn, _release(discogs_id="r1", artist="Rob Zombie", title="The Great Satan"))
    mark_in_collection(conn, "r1")
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "The Great Satan — Ghostly Black Vinyl", "format": "Vinyl", "price": 1.0, "currency": "USD", "url": "https://x/1"},
    ])
    key = compute_item_key("Rob Zombie", "The Great Satan — Ghostly Black Vinyl", "https://x/1")
    conn.execute("INSERT INTO stock_item_judgments (item_key, recommended, reason) VALUES (?, 1, NULL)", [key])
    assert get_distinct_stock_artists(conn, recommended=True) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_db.py -k "excludes_owned or wishlist_match_not_excluded or different_title_same_artist_not_excluded or excludes_owned_artist" -v`
Expected: FAIL — the first three tests fail because `get_unjudged_stock_items` still returns the owned item too (`assert [...] == ["Nails"]` gets `["Rob Zombie", "Nails"]` or similar); the last two fail because `result["total"]`/`get_distinct_stock_artists(...)` still include the owned item.

- [ ] **Step 3: Implement**

In `backend/db.py`, add this constant right before `get_stock_items` (i.e., right after `replace_stock_items`):

```python
_NOT_OWNED_CLAUSE = """NOT EXISTS (
    SELECT 1 FROM releases r
    WHERE r.in_collection = 1
      AND LOWER(r.artist) = LOWER(s.artist)
      AND (LOWER(s.title) = LOWER(r.title) OR LOWER(s.title) LIKE LOWER(r.title) || ' %')
)"""
```

Replace `get_stock_items`'s `if recommended:` block:

```python
    if recommended:
        conditions.append("s.item_key IN (SELECT item_key FROM stock_item_judgments WHERE recommended = 1)")
        conditions.append(_NOT_OWNED_CLAUSE)
```

Replace `get_distinct_stock_artists` in full:

```python
def get_distinct_stock_artists(conn: sqlite3.Connection, overlapping: bool = False, recommended: bool = False) -> list[str]:
    conditions = []
    if overlapping:
        conditions.append("LOWER(s.artist) IN (SELECT LOWER(artist) FROM releases WHERE in_collection = 1)")
    if recommended:
        conditions.append("s.item_key IN (SELECT item_key FROM stock_item_judgments WHERE recommended = 1)")
        conditions.append(_NOT_OWNED_CLAUSE)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = conn.execute(f"SELECT DISTINCT s.artist FROM stock_items s {where} ORDER BY s.artist").fetchall()
    return [row[0] for row in rows]
```

Replace `get_unjudged_stock_items` in full:

```python
def get_unjudged_stock_items(conn: sqlite3.Connection, limit: int) -> list[dict]:
    rows = conn.execute(f"""
        SELECT s.item_key, s.artist, s.title
        FROM stock_items s
        LEFT JOIN stock_item_judgments j ON j.item_key = s.item_key
        WHERE j.item_key IS NULL
          AND {_NOT_OWNED_CLAUSE}
        GROUP BY s.item_key
        ORDER BY MIN(s.last_seen) ASC
        LIMIT ?
    """, [limit]).fetchall()
    return [dict(row) for row in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_db.py -k "excludes_owned or wishlist_match_not_excluded or different_title_same_artist_not_excluded or excludes_owned_artist" -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Run the whole file to confirm no regressions**

Run: `cd backend && pytest tests/test_db.py -v`
Expected: PASS, all tests (existing + these 5 new ones — the pre-existing `overlapping` tests for `get_distinct_stock_artists` must still pass unchanged, since `_NOT_OWNED_CLAUSE` is only appended inside the `if recommended:` branch).

- [ ] **Step 6: Commit**

```bash
git add backend/db.py backend/tests/test_db.py
git commit -m "store-recommended-filter: exclude already-owned items from Recommended"
```

---

### Task 2: Reason-text rewrite + externalized prompt

**Files:**
- Create: `backend/recommendations_prompt.md`
- Modify: `backend/recommendations.py`
- Test: `backend/tests/test_recommendations.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_recommendations.py`:

```python
def test_system_prompt_loads_from_recommendations_prompt_md():
    from pathlib import Path
    import recommendations
    prompt_file = Path(recommendations.__file__).parent / "recommendations_prompt.md"
    assert prompt_file.exists()
    assert recommendations.SYSTEM_PROMPT == prompt_file.read_text().strip()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_recommendations.py::test_system_prompt_loads_from_recommendations_prompt_md -v`
Expected: FAIL — `assert prompt_file.exists()` is `False` (the file doesn't exist yet).

- [ ] **Step 3: Implement**

Create `backend/recommendations_prompt.md`:

```markdown
You are helping a vinyl record collector find new records they might like, based on their existing collection and wishlist.

You will be given the collector's full collection/wishlist as a list of "Artist - Title" lines, followed by a batch of in-stock catalog items to judge.

For each item, decide whether it's a good recommendation given the collector's taste (same genre/scene, related artists, similar labels, adjacent style — not just exact artist matches).

Write the reason as a factual, one-sentence description of the item itself — its genre, style, or notable lineage. Do not write about the collector, the user, or "the collection" as a concept (avoid phrasing like "matches your collection" or "similar to bands you own"). If a specific band, label, or genre concretely explains the fit, name it directly (e.g. "Melodic hardcore with soaring dual-guitar riffs, in the vein of Defeater" — not "similar to bands in your collection").

Respond with a JSON array only, no other text, one entry per item in the same order:

[{"item_key": "<key>", "recommended": true|false, "reason": "<one factual sentence about the item>"}]
```

In `backend/recommendations.py`, replace the top of the file (the imports and `SYSTEM_PROMPT` constant) with:

```python
import json
from pathlib import Path
from logging_config import get_logger

log = get_logger("recommendations")

MODEL = "claude-haiku-4-5"
BATCH_SIZE = 40
SYNC_CAP = 300

SYSTEM_PROMPT = (Path(__file__).parent / "recommendations_prompt.md").read_text().strip()
```

Everything below `SYSTEM_PROMPT` (`build_batch_prompt`, `judge_batch`) is unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_recommendations.py -v`
Expected: PASS (all existing tests + the new one — 8 total)

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend && pytest -q`
Expected: PASS, no regressions (`judge_batch`'s behavior is unchanged — only where `SYSTEM_PROMPT`'s text comes from changed, not its role in the request).

- [ ] **Step 6: Commit**

```bash
git add backend/recommendations.py backend/recommendations_prompt.md backend/tests/test_recommendations.py
git commit -m "store-recommended-filter: externalize and rewrite the judgment prompt for factual reasons"
```

---

### Task 3: Decoupled judgment-only trigger in `CrawlManager`

**Files:**
- Modify: `backend/crawl_manager.py`
- Test: `backend/tests/test_crawl_manager.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_crawl_manager.py`:

```python
async def test_judgment_running_false_initially(manager):
    assert manager.judgment_running is False


async def test_start_judgment_only_returns_true_when_idle(manager, tmp_config_dir):
    import config as cfg_module
    cfg_module.save_config({"anthropic_api_key": "sk-ant-test"})

    async def _fake_judgment_only():
        await asyncio.sleep(0)

    manager._run_judgment_only = _fake_judgment_only  # type: ignore
    started = await manager.start_judgment_only()
    assert started is True
    await asyncio.sleep(0.01)


async def test_start_judgment_only_returns_false_when_already_running(manager):
    event = asyncio.Event()

    async def _fake_judgment_only():
        await event.wait()

    manager._run_judgment_only = _fake_judgment_only  # type: ignore
    await manager.start_judgment_only()
    assert manager.judgment_running is True
    second = await manager.start_judgment_only()
    assert second is False
    event.set()
    await asyncio.sleep(0.01)


async def test_start_judgment_only_returns_false_when_stock_sync_running(manager):
    event = asyncio.Event()

    async def _fake_sync_stock():
        await event.wait()

    manager._sync_stock = _fake_sync_stock  # type: ignore
    await manager.start_stock_sync()
    assert manager.stock_sync_running is True
    started = await manager.start_judgment_only()
    assert started is False
    event.set()
    await asyncio.sleep(0.01)


async def test_start_stock_sync_returns_false_when_judgment_running(manager):
    event = asyncio.Event()

    async def _fake_judgment_only():
        await event.wait()

    manager._run_judgment_only = _fake_judgment_only  # type: ignore
    await manager.start_judgment_only()
    assert manager.judgment_running is True
    started = await manager.start_stock_sync()
    assert started is False
    event.set()
    await asyncio.sleep(0.01)


async def test_run_judgment_only_broadcasts_error_when_no_api_key(manager, tmp_config_dir):
    await manager._run_judgment_only()
    statuses = [e["status"] for e in manager.recent_events()]
    assert "stock_judgment_error" in statuses


async def test_run_judgment_only_judges_unjudged_items_when_api_key_configured(manager, tmp_config_dir, monkeypatch):
    import config as cfg_module
    import db as db_module
    import recommendations
    from db import register_crawler, replace_stock_items, compute_item_key

    cfg_module.save_config({"anthropic_api_key": "sk-ant-test"})

    conn = sqlite3.connect(cfg_module.DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db_module.init_db(conn)
    register_crawler(conn, "Nuclear Blast", "/path/nb.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Nuclear Blast'").fetchone()[0]
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "T1", "price": 1.0, "currency": "USD", "url": "https://x/1"},
    ])
    conn.close()

    key = compute_item_key("Rob Zombie", "T1", "https://x/1")
    monkeypatch.setattr(
        recommendations, "judge_batch",
        lambda client, taste, batch: [{"item_key": key, "recommended": True, "reason": "similar genre"}],
    )

    await manager._run_judgment_only()

    statuses = [e["status"] for e in manager.recent_events()]
    assert "stock_judgment_complete" in statuses

    conn2 = sqlite3.connect(cfg_module.DB_FILE)
    conn2.row_factory = sqlite3.Row
    row = conn2.execute("SELECT recommended FROM stock_item_judgments WHERE item_key = ?", [key]).fetchone()
    assert row["recommended"] == 1
    conn2.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_crawl_manager.py -k judgment_only -v`
Expected: FAIL — `AttributeError: 'CrawlManager' object has no attribute 'judgment_running'` (and similarly for `start_judgment_only`/`_run_judgment_only`).

- [ ] **Step 3: Implement**

In `backend/crawl_manager.py`, add `self._judgment_task: Optional[asyncio.Task] = None` to `CrawlManager.__init__`, right after the existing `self._stock_task: Optional[asyncio.Task] = None` line.

Replace `start_stock_sync`:

```python
    async def start_stock_sync(self) -> bool:
        if self.stock_sync_running or self.judgment_running:
            log.warning("Stock sync or judgment already running, ignoring start request")
            return False
        self._stock_task = asyncio.create_task(self._sync_stock())
        return True
```

Add these two methods and one property right after `_run_judgment_phase` (i.e., right before the `crawl_manager = CrawlManager()` singleton line at the end of the file):

```python
    @property
    def judgment_running(self) -> bool:
        return self._judgment_task is not None and not self._judgment_task.done()

    async def start_judgment_only(self) -> bool:
        if self.stock_sync_running or self.judgment_running:
            log.warning("Stock sync or judgment already running, ignoring judgment-only start request")
            return False
        self._judgment_task = asyncio.create_task(self._run_judgment_only())
        return True

    async def _run_judgment_only(self):
        import sqlite3
        import config as cfg_module
        from config import load_config

        conn = sqlite3.connect(cfg_module.DB_FILE, check_same_thread=False, timeout=60)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            api_key = load_config().get("anthropic_api_key", "")
            if not api_key:
                await self._broadcast({"status": "stock_judgment_error", "error": "Anthropic API key not configured"})
                return
            await self._run_judgment_phase(conn, api_key)
        except asyncio.CancelledError:
            log.info("Judgment-only run cancelled")
            raise
        except Exception as e:
            log.error("Judgment-only run failed: %s", e, exc_info=True)
            await self._broadcast({"status": "stock_judgment_error", "error": str(e)})
        finally:
            conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_crawl_manager.py -v`
Expected: PASS (all existing + 7 new tests)

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend && pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add backend/crawl_manager.py backend/tests/test_crawl_manager.py
git commit -m "store-recommended-filter: add judgment-only trigger decoupled from stock sync"
```

---

### Task 4: `POST /api/stock/judge/start` endpoint

**Files:**
- Modify: `backend/routers/stock.py`
- Test: `backend/tests/test_stock_router.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_stock_router.py`:

```python
def test_start_stock_judgment_calls_manager(client, monkeypatch):
    fake_manager = AsyncMock()
    fake_manager.start_judgment_only = AsyncMock(return_value=True)
    fake_manager.judgment_running = True
    monkeypatch.setattr(stock_router, "crawl_manager", fake_manager)
    r = client.post("/api/stock/judge/start")
    assert r.status_code == 200
    assert r.json() == {"started": True, "running": True}
    fake_manager.start_judgment_only.assert_awaited_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_stock_router.py::test_start_stock_judgment_calls_manager -v`
Expected: FAIL — `404 Not Found` (the route doesn't exist yet).

- [ ] **Step 3: Implement**

In `backend/routers/stock.py`, add this endpoint right after `start_stock_sync`:

```python
@router.post("/stock/judge/start")
async def start_stock_judgment():
    started = await crawl_manager.start_judgment_only()
    return {"started": started, "running": crawl_manager.judgment_running}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_stock_router.py -v`
Expected: PASS (all existing + this new test)

- [ ] **Step 5: Commit**

```bash
git add backend/routers/stock.py backend/tests/test_stock_router.py
git commit -m "store-recommended-filter: add POST /stock/judge/start endpoint"
```

---

### Task 5: Frontend API client — `postJudgmentStart`

**Files:**
- Modify: `frontend/src/api/client.ts`

- [ ] **Step 1: Implement**

In `frontend/src/api/client.ts`, add this function right after `postStockSyncStart`:

```typescript
export async function postJudgmentStart(): Promise<{ started: boolean; running: boolean }> {
  const r = await apiFetch('/stock/judge/start', { method: 'POST' })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}
```

- [ ] **Step 2: Verify types compile**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: no errors (this is an isolated, unused-until-Task-6 export — TypeScript doesn't flag unused exports the way it would an unused local variable).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/client.ts
git commit -m "store-recommended-filter: add postJudgmentStart API client function"
```

---

### Task 6: "Refresh Recommendations" button (`App.tsx` + `Settings.tsx`)

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/views/Settings.tsx`
- Modify: `frontend/src/test/inStockTab.test.tsx`

- [ ] **Step 1: Write the failing test**

In `frontend/src/test/inStockTab.test.tsx`, the mock setup needs `getSettings` and a new `postJudgmentStart` to be individually overridable per test (matching the pattern `stockBrowser.test.tsx` already uses for `getStock`/`getStockArtists`), since this test needs a configured API key to exercise the button while other tests in the file need the default empty key.

Replace the mock declarations at the top of the file — find:

```typescript
const postStockSyncStart = vi.fn().mockResolvedValue({ started: true, running: true })

vi.mock('../api/client', () => ({
```

Replace with:

```typescript
const postStockSyncStart = vi.fn().mockResolvedValue({ started: true, running: true })
const postJudgmentStart = vi.fn().mockResolvedValue({ started: true, running: true })
const getSettings = vi.fn()

vi.mock('../api/client', () => ({
```

Inside that same `vi.mock` factory, find the inline `getSettings: vi.fn().mockResolvedValue({...})` block and replace it with a reference to the top-level mock:

```typescript
  getSettings: (...args: unknown[]) => getSettings(...args),
```

In the same factory, find:

```typescript
  postStockSyncStart: (...args: unknown[]) => postStockSyncStart(...args),
```

and add right after it:

```typescript
  postJudgmentStart: (...args: unknown[]) => postJudgmentStart(...args),
```

Find the existing `beforeEach` block:

```typescript
beforeEach(() => {
  MockEventSource.instances = []
  vi.clearAllMocks()
  postStockSyncStart.mockResolvedValue({ started: true, running: true })
})
```

Replace with:

```typescript
const defaultSettings = {
  discogs_token: '', debug_screenshot_interval: 20, shuffle_crawl_order: true,
  crawl_delay_seconds: 30, consecutive_failure_limit: 10, crawl_schedule: '',
  crawl_schedule_mode: 'missing', collection_schedule: '', collection_schedule_mode: 'all',
  ebay_app_id: '', ebay_cert_id: '', stock_schedule: '', anthropic_api_key: '',
}

beforeEach(() => {
  MockEventSource.instances = []
  vi.clearAllMocks()
  postStockSyncStart.mockResolvedValue({ started: true, running: true })
  postJudgmentStart.mockResolvedValue({ started: true, running: true })
  getSettings.mockResolvedValue(defaultSettings)
})
```

Then add this new test inside the `describe('In Stock tab', ...)` block:

```typescript
  it('calls postJudgmentStart when Refresh Recommendations is clicked in Settings', async () => {
    getSettings.mockResolvedValue({ ...defaultSettings, anthropic_api_key: 'sk-ant-test' })
    render(<App />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Settings' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }))
    await waitFor(() => expect(screen.getByText('Refresh Recommendations')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Refresh Recommendations'))
    await waitFor(() => expect(postJudgmentStart).toHaveBeenCalled())
  })
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/test/inStockTab.test.tsx`
Expected: FAIL — the new test can't find text "Refresh Recommendations" (the button doesn't exist yet); all other tests in the file should still pass, confirming the mock refactor itself didn't break anything (verify this before moving on — if other tests fail, the refactor introduced a regression to fix before proceeding).

- [ ] **Step 3: Implement**

In `frontend/src/App.tsx`, add `postJudgmentStart` to the existing `./api/client` import (alongside `postStockSyncStart`, `getSettings`, etc.).

Add a new handler right after `handleRefreshStock`:

```typescript
  async function handleRefreshRecommendations() {
    try {
      await postJudgmentStart()
    } catch (e: any) {
      setSyncMessage(`Refresh recommendations failed to start: ${e.message}`)
    }
  }
```

Update the `Settings` render call to pass the new handler:

```typescript
<Settings crawlers={crawlers} onCrawlersChange={setCrawlers} onRefreshCollection={(mode) => handleRefresh(mode)} onRefreshPrices={(mode) => handleFindPrices(undefined, mode)} onRefreshStock={handleRefreshStock} onRefreshRecommendations={handleRefreshRecommendations} />
```

In `frontend/src/views/Settings.tsx`, update the `Props` interface:

```typescript
interface Props {
  crawlers: Crawler[]
  onCrawlersChange: (crawlers: Crawler[]) => void
  onRefreshCollection: (mode: 'all' | 'new') => void
  onRefreshPrices: (mode: 'missing' | 'all') => void
  onRefreshStock: () => void
  onRefreshRecommendations: () => void
}
```

Update the function signature:

```typescript
export default function Settings({ crawlers, onCrawlersChange, onRefreshCollection, onRefreshPrices, onRefreshStock, onRefreshRecommendations }: Props) {
```

In the Store Management section, add a new row right after the existing "Refresh Now" button row (inside the same `<table>`, right after that row's closing `</tr>`, still before `</tbody>`):

```typescript
            <tr className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left align-top whitespace-nowrap w-40"></td>
              <td className="py-3 pr-4 text-left align-top">
                <button
                  onClick={onRefreshRecommendations}
                  disabled={!settings.anthropic_api_key}
                  className="px-3 py-1 bg-indigo-700 hover:bg-indigo-600 disabled:opacity-50 rounded text-xs font-medium transition-colors"
                >
                  Refresh Recommendations
                </button>
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                Judge currently unjudged Store items against your collection, without a full catalog re-crawl. Requires an Anthropic API key above.
              </td>
            </tr>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/test/inStockTab.test.tsx`
Expected: PASS (all existing + the new test)

- [ ] **Step 5: Run the full frontend suite and typecheck**

Run: `cd frontend && npx vitest run && npx tsc -b --noEmit`
Expected: all tests pass, zero type errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/App.tsx frontend/src/views/Settings.tsx frontend/src/test/inStockTab.test.tsx
git commit -m "store-recommended-filter: add Refresh Recommendations button"
```

---

## Final Verification

- [ ] Run the full backend suite: `cd backend && pytest -v` — expect all green.
- [ ] Run the full frontend suite: `cd frontend && npx vitest run` — expect all green.
- [ ] Type-check the frontend: `cd frontend && npx tsc -b --noEmit` — expect no errors.
- [ ] Manual smoke test: with a real Anthropic API key and a non-trivial collection, confirm a store item matching an owned title never appears under Recommended even after a full sync; read a few generated reasons and confirm they read as factual item descriptions, not "matches your collection" phrasing; click "Refresh Recommendations" in Settings and confirm the status bar shows judgment progress without any of the 13 catalog crawlers running.
