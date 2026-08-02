# Stock-Sync 429 Backoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `shopify_catalog.iter_products()`'s retry loop respect a `Retry-After` header on HTTP 429, and make `crawl_manager._sync_stock` abort the rest of a run once two independent catalog crawlers in a row fail with 429 — instead of grinding through every remaining enabled crawler into what's very likely an active platform-wide Shopify rate limit.

**Architecture:** See [`docs/superpowers/specs/2026-08-02-stock-sync-429-backoff-design.md`](../specs/2026-08-02-stock-sync-429-backoff-design.md). In short: `iter_products()` gains a pure `_parse_retry_after()` helper and a `next_delay_override` local that, when set from a 429's `Retry-After` header, replaces the next jittered pre-request sleep exactly once. `_sync_stock` gains a local `consecutive_429_sites` list (site names, not just a count, so the abort broadcast can name them) that grows on a 429-caused crawler failure and resets to empty on anything else (success or non-429 failure); reaching length 2 broadcasts `stock_sync_aborted` and returns early, skipping the remaining crawlers, the judgment phase, and the final `stock_sync_complete` broadcast for that run.

**Tech Stack:** Python `asyncio`, `httpx`, SQLite (this branch is based on `main`, pre-multi-tenant-migration — no Postgres, no `crawl_queue` worker pool here).

**Verified against the actual current code before writing this plan** — every snippet below matches the real current state of `backend/shopify_catalog.py` and `backend/crawl_manager.py` on this branch (`stock-sync-429-backoff`, branched from `origin/main`). Still verify against the real file yourself before editing, in case anything changed between plan-writing and execution.

## File structure

| File | Task(s) | Responsibility after this plan |
|---|---|---|
| `backend/shopify_catalog.py` | 1 | `iter_products()`'s retry loop honors a 429's `Retry-After` header, capped at 600s, via a new `_parse_retry_after()` helper |
| `backend/crawl_manager.py` | 2 | `_sync_stock` aborts the run after 2 consecutive 429-caused crawler failures, broadcasting `stock_sync_aborted` naming the two sites |

---

### Task 1: `shopify_catalog.py` — respect `Retry-After` on 429

**Files:**
- Modify: `backend/shopify_catalog.py`
- Test: `backend/tests/test_shopify_catalog.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_shopify_catalog.py` (matches this file's existing `respx`/`tmp_config_dir`/`monkeypatch` conventions — see `test_iter_products_uses_configured_crawl_delay_seconds` and `test_iter_products_retries_after_transient_failure` for the pattern this reuses):

```python
@respx.mock
async def test_iter_products_respects_retry_after_header_on_429(tmp_config_dir, monkeypatch):
    save_config({"crawl_delay_seconds": 30, "consecutive_failure_limit": 3})
    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr("shopify_catalog.asyncio.sleep", fake_sleep)
    route = respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"})
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "5"}),
        _page_response([{"id": 1}]),
    ]
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    products = [p async for p in iter_products("https://example.myshopify.test", "vinyl")]
    assert [p["id"] for p in products] == [1]
    # sleep_calls[0] is the pre-request delay before the first (failing) attempt;
    # sleep_calls[1] is the delay before the retry, which must be exactly Retry-After.
    assert sleep_calls[1] == 5.0


@respx.mock
async def test_iter_products_falls_back_to_jitter_when_429_has_no_retry_after(tmp_config_dir, monkeypatch):
    save_config({"crawl_delay_seconds": 40, "consecutive_failure_limit": 3})
    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr("shopify_catalog.asyncio.sleep", fake_sleep)
    route = respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"})
    route.side_effect = [httpx.Response(429), _page_response([{"id": 1}])]
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    products = [p async for p in iter_products("https://example.myshopify.test", "vinyl")]
    assert [p["id"] for p in products] == [1]
    assert 20 <= sleep_calls[1] <= 40


@respx.mock
async def test_iter_products_caps_retry_after_at_600_seconds(tmp_config_dir, monkeypatch):
    save_config({"crawl_delay_seconds": 30, "consecutive_failure_limit": 3})
    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr("shopify_catalog.asyncio.sleep", fake_sleep)
    route = respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"})
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "99999"}),
        _page_response([]),
    ]
    [p async for p in iter_products("https://example.myshopify.test", "vinyl")]
    assert sleep_calls[1] == 600.0


def test_parse_retry_after_returns_none_for_missing_invalid_or_negative():
    from shopify_catalog import _parse_retry_after
    assert _parse_retry_after(None) is None
    assert _parse_retry_after("not-a-number") is None
    assert _parse_retry_after("-5") is None


def test_parse_retry_after_passes_through_a_valid_value():
    from shopify_catalog import _parse_retry_after
    assert _parse_retry_after("5") == 5.0


def test_parse_retry_after_caps_at_max():
    from shopify_catalog import _parse_retry_after
    assert _parse_retry_after("99999") == 600.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_shopify_catalog.py -k "retry_after or parse_retry_after" -v`
Expected: FAIL — `_parse_retry_after` doesn't exist yet, and the 429 tests currently sleep the flat jittered delay instead of `Retry-After`.

- [ ] **Step 3: Implement**

In `backend/shopify_catalog.py`, add a module constant next to `_PAGE_LIMIT` (currently line 7):

```python
_PAGE_LIMIT = 250
_MAX_RETRY_AFTER_SECONDS = 600.0
```

Replace `iter_products()` in full (currently lines 10-45):

```python
async def iter_products(base_url: str, collection_slug: str) -> AsyncIterator[dict]:
    """Paginate a Shopify collection's public products.json endpoint until exhausted.

    Reuses the crawl_delay_seconds / consecutive_failure_limit settings crawl_releases()
    applies to release search requests, extended here with retry-on-failure: unlike
    crawl_releases(), which just moves on to the next release/crawler pair, pagination
    has no next item to fall through to, so a failed page is retried instead.
    """
    cfg = load_config()
    delay = float(cfg.get("crawl_delay_seconds", 30))
    failure_limit = int(cfg.get("consecutive_failure_limit", 10))
    consecutive_failures = 0
    next_delay_override: Optional[float] = None

    page = 1
    async with httpx.AsyncClient() as client:
        while True:
            url = f"{base_url}/collections/{collection_slug}/products.json"
            if next_delay_override is not None:
                await asyncio.sleep(next_delay_override)
                next_delay_override = None
            else:
                await asyncio.sleep(random.uniform(delay * 0.5, delay))
            try:
                r = await client.get(url, params={"limit": _PAGE_LIMIT, "page": page})
                r.raise_for_status()
            except httpx.HTTPError as e:
                consecutive_failures += 1
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 429:
                    next_delay_override = _parse_retry_after(e.response.headers.get("Retry-After"))
                # A limit of 0 means "disabled" elsewhere, but disabled must mean
                # fail fast here, not unlimited retries — this loop has no next
                # item to move on to like crawl_releases() does.
                if failure_limit <= 0 or consecutive_failures >= failure_limit:
                    raise
                continue
            consecutive_failures = 0
            products = r.json().get("products", [])
            if not products:
                break
            for product in products:
                yield product
            page += 1


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    """Parses a 429 response's Retry-After header (numeric-seconds form only).
    Returns None for a missing, non-numeric, or negative value, so the caller
    falls back to the jittered delay instead.
    """
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    if seconds < 0:
        return None
    return min(seconds, _MAX_RETRY_AFTER_SECONDS)
```

Everything below `_parse_retry_after` in the file (`has_tag`, `strip_vendor_prefix`, `resolve_cover_image`) is unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_shopify_catalog.py -v`
Expected: PASS — the full file, including every pre-existing test (proving the change is backward-compatible for the non-429 and 429-without-header cases).

- [ ] **Step 5: Commit**

```bash
git add backend/shopify_catalog.py backend/tests/test_shopify_catalog.py
git commit -m "fix: respect Retry-After header on Shopify catalog 429s"
```

---

### Task 2: `crawl_manager.py` — abort `_sync_stock` after 2 consecutive 429 crawlers

**Files:**
- Modify: `backend/crawl_manager.py`
- Test: `backend/tests/test_crawl_manager.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_crawl_manager.py` (matches the existing `_FakeCrawler`/`register_crawler`/`monkeypatch.setattr(crawler_module, "load_enabled_crawlers", ...)` pattern used by `test_sync_stock_updates_crawler_last_run` and the judgment-phase tests just above/below it):

```python
async def test_sync_stock_aborts_after_two_consecutive_429_crawlers(manager, tmp_config_dir, monkeypatch):
    import config as cfg_module
    import db as db_module
    import crawler as crawler_module
    import httpx
    from db import register_crawler

    conn = sqlite3.connect(cfg_module.DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db_module.init_db(conn)
    for name in ["Run For Cover", "Equal Vision", "Never Attempted"]:
        register_crawler(conn, name, f"/path/{name}.py", crawler_type="catalog")
    ids = {row["site_name"]: row["id"] for row in conn.execute("SELECT id, site_name FROM crawlers")}

    def _http_429():
        request = httpx.Request("GET", "https://example.test/products.json")
        return httpx.HTTPStatusError("429", request=request, response=httpx.Response(429))

    class _FailingCrawler:
        def __init__(self, name):
            self._db_id = ids[name]
            self._db_site_name = name

        async def crawl_catalog(self):
            raise _http_429()
            yield  # pragma: no cover -- keeps this an async generator function

    class _SucceedingCrawler:
        def __init__(self, name):
            self._db_id = ids[name]
            self._db_site_name = name

        async def crawl_catalog(self):
            yield {"artist": "A", "title": "T", "price": 1.0, "currency": "USD", "url": "https://x/1"}

    monkeypatch.setattr(crawler_module, "load_enabled_crawlers", lambda enabled: [
        _FailingCrawler("Run For Cover"),
        _FailingCrawler("Equal Vision"),
        _SucceedingCrawler("Never Attempted"),
    ])

    await manager._sync_stock()

    events = manager.recent_events()
    statuses = [e["status"] for e in events]
    assert "stock_sync_aborted" in statuses
    assert "stock_sync_complete" not in statuses
    aborted = next(e for e in events if e["status"] == "stock_sync_aborted")
    assert aborted["sources"] == ["Run For Cover", "Equal Vision"]
    assert not any(e.get("source") == "Never Attempted" for e in events)
    conn.close()


async def test_sync_stock_resets_429_streak_after_a_success(manager, tmp_config_dir, monkeypatch):
    import config as cfg_module
    import db as db_module
    import crawler as crawler_module
    import httpx
    from db import register_crawler

    conn = sqlite3.connect(cfg_module.DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db_module.init_db(conn)
    for name in ["Run For Cover", "Middle Site", "Equal Vision"]:
        register_crawler(conn, name, f"/path/{name}.py", crawler_type="catalog")
    ids = {row["site_name"]: row["id"] for row in conn.execute("SELECT id, site_name FROM crawlers")}

    def _http_429():
        request = httpx.Request("GET", "https://example.test/products.json")
        return httpx.HTTPStatusError("429", request=request, response=httpx.Response(429))

    class _FailingCrawler:
        def __init__(self, name):
            self._db_id = ids[name]
            self._db_site_name = name

        async def crawl_catalog(self):
            raise _http_429()
            yield  # pragma: no cover

    class _SucceedingCrawler:
        def __init__(self, name):
            self._db_id = ids[name]
            self._db_site_name = name

        async def crawl_catalog(self):
            yield {"artist": "A", "title": "T", "price": 1.0, "currency": "USD", "url": "https://x/1"}

    monkeypatch.setattr(crawler_module, "load_enabled_crawlers", lambda enabled: [
        _FailingCrawler("Run For Cover"),
        _SucceedingCrawler("Middle Site"),
        _FailingCrawler("Equal Vision"),
    ])

    await manager._sync_stock()

    statuses = [e["status"] for e in manager.recent_events()]
    assert "stock_sync_aborted" not in statuses
    assert "stock_sync_complete" in statuses
    conn.close()


async def test_sync_stock_resets_429_streak_after_a_non_429_failure(manager, tmp_config_dir, monkeypatch):
    import config as cfg_module
    import db as db_module
    import crawler as crawler_module
    import httpx
    from db import register_crawler

    conn = sqlite3.connect(cfg_module.DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db_module.init_db(conn)
    for name in ["Run For Cover", "Middle Site", "Equal Vision"]:
        register_crawler(conn, name, f"/path/{name}.py", crawler_type="catalog")
    ids = {row["site_name"]: row["id"] for row in conn.execute("SELECT id, site_name FROM crawlers")}

    def _http_429():
        request = httpx.Request("GET", "https://example.test/products.json")
        return httpx.HTTPStatusError("429", request=request, response=httpx.Response(429))

    class _FailingCrawler:
        def __init__(self, name):
            self._db_id = ids[name]
            self._db_site_name = name

        async def crawl_catalog(self):
            raise _http_429()
            yield  # pragma: no cover

    class _OtherFailureCrawler:
        def __init__(self, name):
            self._db_id = ids[name]
            self._db_site_name = name

        async def crawl_catalog(self):
            raise RuntimeError("boom")
            yield  # pragma: no cover

    monkeypatch.setattr(crawler_module, "load_enabled_crawlers", lambda enabled: [
        _FailingCrawler("Run For Cover"),
        _OtherFailureCrawler("Middle Site"),
        _FailingCrawler("Equal Vision"),
    ])

    await manager._sync_stock()

    statuses = [e["status"] for e in manager.recent_events()]
    assert "stock_sync_aborted" not in statuses
    assert "stock_sync_complete" in statuses
    conn.close()
```

Each new test does `import httpx` locally inside the function body — matching this file's existing convention (`import config as cfg_module`, `import db as db_module`, `import crawler as crawler_module` are all local-to-the-test-function too, not top-of-file). Confirmed `test_crawl_manager.py`'s actual top-of-file imports today are only `asyncio`, `sqlite3`, `pytest`, `CrawlManager` — don't add `httpx` there.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_crawl_manager.py -k "429" -v`
Expected: FAIL — `_sync_stock` has no concept of a 429 streak yet; it currently continues through every crawler regardless, so `stock_sync_aborted` is never broadcast and `Never Attempted`/all three crawlers always run.

- [ ] **Step 3: Implement**

Replace `_sync_stock` in `backend/crawl_manager.py` in full (currently lines 273-333):

```python
    async def _sync_stock(self):
        import sqlite3
        import httpx
        import config as cfg_module
        from config import load_config
        from db import get_enabled_crawlers, replace_stock_items, update_crawler_last_run
        from crawler import load_enabled_crawlers

        await self._broadcast({"status": "stock_sync_started"})
        log.info("Stock sync started")

        conn = sqlite3.connect(cfg_module.DB_FILE, check_same_thread=False, timeout=60)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            enabled = get_enabled_crawlers(conn, crawler_type="catalog")
            crawlers = load_enabled_crawlers(enabled)
            if not crawlers:
                await self._broadcast({"status": "stock_sync_error", "error": "No enabled catalog crawlers"})
                return

            total_synced = 0
            consecutive_429_sites: list[str] = []
            for crawler in crawlers:
                items = []
                try:
                    async for item in crawler.crawl_catalog():
                        items.append(item)
                except Exception as e:
                    log.error("[%s] Stock crawl failed: %s", crawler._db_site_name, e, exc_info=True)
                    await self._broadcast({
                        "status": "stock_sync_error",
                        "error": str(e),
                        "source": crawler._db_site_name,
                    })
                    if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 429:
                        consecutive_429_sites.append(crawler._db_site_name)
                    else:
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
                replace_stock_items(conn, crawler._db_id, items)
                total_synced += len(items)
                update_crawler_last_run(conn, crawler._db_id)
                log.info("[%s] Stock sync found %d items", crawler._db_site_name, len(items))
                await self._broadcast({
                    "status": "stock_sync_progress",
                    "synced": total_synced,
                    "source": crawler._db_site_name,
                })

            api_key = load_config().get("anthropic_api_key", "")
            if api_key:
                await self._run_judgment_phase(conn, api_key)

            await self._broadcast({"status": "stock_sync_complete", "synced": total_synced})
            log.info("Stock sync complete: %d items", total_synced)

        except asyncio.CancelledError:
            log.info("Stock sync cancelled")
            raise
        except Exception as e:
            log.error("Stock sync failed: %s", e, exc_info=True)
            await self._broadcast({"status": "stock_sync_error", "error": str(e)})
        finally:
            conn.close()
```

The only changes from the current method: the `import httpx` line, the `consecutive_429_sites` list and its two update points (append-or-reset in the except block, reset on success), the `len(...) >= 2` check with its `log.warning` + `stock_sync_aborted` broadcast + early `return`, and the return sitting inside the existing `try` block so the existing `finally: conn.close()` still runs on that path exactly as it does for every other exit.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_crawl_manager.py -v`
Expected: PASS — the full file, including every pre-existing `_sync_stock`/judgment-phase test (proving the non-429, non-streak case is unchanged).

Also run the broader backend baseline: `cd backend && .venv/bin/pytest tests/ -q`.

- [ ] **Step 5: Commit**

```bash
git add backend/crawl_manager.py backend/tests/test_crawl_manager.py
git commit -m "fix: abort stock sync after 2 consecutive 429 catalog crawlers"
```

---

### Task 3: Frontend — handle `stock_sync_aborted`

**Added after Task 2's code-quality review** (not in the original plan as approved): the design spec's Goal 3 ("The frontend/log clearly show a deliberate backoff, not a crash") was only half-implemented by Task 2 — the backend logs and broadcasts `stock_sync_aborted`, but nothing in the frontend handles that event. Neither `stock_sync_complete` nor `stock_sync_error` fires on the abort path, so without this task the sync spinner and "Syncing in-stock catalog…" status text would stay stuck indefinitely after an abort. This closes that gap.

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/test/inStockTab.test.tsx`

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/test/inStockTab.test.tsx`, in the `describe('In Stock tab', ...)` block, near the existing `stock_sync_complete`/`stock_sync_progress` tests (matches their exact `render`/`getLastCrawlSource`/`source.emit`/`waitFor` pattern):

```tsx
it('surfaces stock_sync_aborted events in the bottom status bar and stops syncing', async () => {
  render(<App />)
  await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
  const source = getLastCrawlSource()
  source.emit({ status: 'stock_sync_started', id: 1 })
  await waitFor(() => expect(screen.getByText(/Syncing in-stock catalog…/)).toBeInTheDocument())
  source.emit({
    status: 'stock_sync_aborted',
    error: 'Too many consecutive rate-limited catalog sites',
    sources: ['Run For Cover', 'Equal Vision'],
    id: 2,
  })
  await waitFor(() =>
    expect(
      screen.getByText(/In-stock sync stopped: Too many consecutive rate-limited catalog sites \(Run For Cover, Equal Vision\)/)
    ).toBeInTheDocument()
  )
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- --run inStockTab`
Expected: FAIL — `stock_sync_aborted` isn't in the `CrawlEvent` status union yet (TypeScript) and `App.tsx` has no handler for it, so the status bar never shows the expected text (it stays stuck on "Syncing in-stock catalog…" from the `stock_sync_started` event just before it).

- [ ] **Step 3: Implement**

In `frontend/src/api/types.ts`, in the `CrawlEvent` interface (currently lines 66-89), add `stock_sync_aborted` to the status union (line 70) and a new `sources` field:

```ts
export interface CrawlEvent {
  id?: number
  status?: 'found' | 'not_found' | 'error' | 'complete' | 'started' | 'stopped' | 'ping'
    | 'sync_started' | 'sync_progress' | 'sync_complete' | 'sync_error'
    | 'stock_sync_started' | 'stock_sync_progress' | 'stock_sync_complete' | 'stock_sync_error' | 'stock_sync_aborted'
    | 'stock_judgment_started' | 'stock_judgment_progress' | 'stock_judgment_complete' | 'stock_judgment_error'
    | 'plex_match_started' | 'plex_match_progress' | 'plex_match_complete' | 'plex_match_error'
  discogs_id?: string
  release?: string
  artist?: string
  site?: string
  price?: number
  error?: string
  total?: number
  total_pages?: number
  page?: number
  synced?: number
  wishlist_synced?: number
  username?: string
  screenshots?: string[]
  source?: string
  sources?: string[]
  judged?: number
  matched?: number
}
```

(Only change: `stock_sync_aborted` added to the status union on the existing `stock_sync_*` line, and a new `sources?: string[]` field added after the existing `source?: string` field. Everything else in the interface is unchanged.)

In `frontend/src/App.tsx`, immediately after the existing `stock_sync_error` block (currently lines 143-147):

```tsx
      if (event.status === 'stock_sync_error') {
        setSyncing(false)
        setSyncStatus(`In-stock sync failed: ${event.error}`, event.id ?? null)
        return
      }
      if (event.status === 'stock_sync_aborted') {
        setSyncing(false)
        setSyncStatus(`In-stock sync stopped: ${event.error} (${event.sources?.join(', ')})`, event.id ?? null)
        return
      }
```

(i.e., add the new `stock_sync_aborted` block right after the existing `stock_sync_error` block — same shape, `setSyncing(false)` + `setSyncStatus(...)` + `return`, matching every other terminal status handler in this function.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npm test -- --run inStockTab` — should pass.
Run: `cd frontend && npm test -- --run` — full suite, should pass (confirms no regression elsewhere).
Run: `cd frontend && npx tsc -b` — should be clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/types.ts frontend/src/App.tsx frontend/src/test/inStockTab.test.tsx
git commit -m "fix: show stock_sync_aborted status in the frontend sync banner"
```

---

### Task 4: Final review

Not a code task — dispatch a final reviewer over this plan's whole diff (4 code/doc commits plus the earlier spec-only commit), checking:
- `_parse_retry_after` is only ever reached from the 429 branch (confirm `isinstance(e, httpx.HTTPStatusError)` correctly guards `e.response` — a plain `httpx.RequestError`, e.g. a timeout or connection error, has no `.response` attribute and must never reach that line), and correctly rejects non-finite values (`math.isfinite`) as well as negative/non-numeric ones (this was a fix-up found during Task 1's code-quality review, added in a follow-up commit — confirm it actually landed).
- `_sync_stock`'s early `return` on abort genuinely skips the judgment phase and the `stock_sync_complete` broadcast for that run, and still closes the SQLite connection (via the untouched `finally`).
- `consecutive_429_sites` is reset to `[]` on *every* non-429 outcome, not just success — re-read the final code to confirm the non-429-failure branch's `else: consecutive_429_sites = []` actually landed (this is the detail `test_sync_stock_resets_429_streak_after_a_non_429_failure` exists to catch).
- Task 3's frontend handler actually stops the UI's "syncing" state on `stock_sync_aborted` (not just that the status text renders) — re-read `App.tsx`'s handler to confirm `setSyncing(false)` is really there, not just assumed from the test passing.
- Full backend test suite green: `cd backend && .venv/bin/pytest tests/ -q`. Full frontend suite green: `cd frontend && npm test -- --run` and `cd frontend && npx tsc -b`.
- Pre-PR spec-drift check per `CLAUDE.md`: `grep -rl "iter_products\|_sync_stock\|stock_sync" docs/superpowers/specs/`. This is expected to surface at least two real hits found during code-quality review and not yet fixed — amend both in their own commit(s) on this branch:
  - `docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md`: its description of `iter_products()`'s retry delay (a flat random delay in `[crawl_delay_seconds/2, crawl_delay_seconds]`) is now incomplete since a 429 with `Retry-After` overrides that delay once; and its enumerated SSE event list for stock sync (`stock_sync_started/progress/complete/error`) is missing `stock_sync_aborted`.
  - `docs/superpowers/specs/2026-06-27-discogs-browser-design.md` (the master spec): confirm whether it describes `_sync_stock`'s per-crawler failure handling or the stock-sync SSE event list in a way this plan's changes now contradict; amend if so.
- Confirm `docs/superpowers/specs/2026-08-02-stock-sync-429-backoff-design.md` itself needs no amendment beyond what's already tracked above (i.e., what shipped matches what it describes for the backend pieces it covers — verify rather than assume).

Once approved, this branch is ready for the `finishing-a-development-branch` conversation (merge/PR/cleanup).
