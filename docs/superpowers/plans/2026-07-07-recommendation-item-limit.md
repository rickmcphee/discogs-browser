# Configurable Recommendation Item Limit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hardcoded 300-item-per-judgment-run cap with a user-configurable Settings field ("Recommendation item limit," 0 = no limit), and reword judgment-run logging to show the true backlog size alongside what's actually being processed this run.

**Architecture:** A new `db.count_unjudged_stock_items` gives the true, uncapped backlog size; `get_unjudged_stock_items` learns to treat `limit <= 0` as "no `LIMIT` clause." `CrawlManager._run_judgment_phase` reads the configured limit from config (falling back to today's `SYNC_CAP` default) and uses both numbers in its log lines. The setting itself is plumbed through exactly like every other numeric Settings field already in this app.

**Tech Stack:** Same as the base feature — Python 3.9 / FastAPI / SQLite, React 19 / TypeScript / vitest.

**Spec:** [`docs/superpowers/specs/2026-07-06-store-recommended-filter-design.md`](../specs/2026-07-06-store-recommended-filter-design.md), Amendment 3 (2026-07-07) section.

---

## File Structure

| File | Change |
|---|---|
| `backend/db.py` | `get_unjudged_stock_items` treats `limit <= 0` as unlimited; new `count_unjudged_stock_items`. |
| `backend/crawl_manager.py` | `_run_judgment_phase` reads the configured limit, logs "Found X/Y items to judge for recommendation." |
| `backend/routers/settings.py` | New `recommendation_item_limit` field in `SettingsUpdate`, `get_settings`, `update_settings`. |
| `frontend/src/api/types.ts` | New `recommendation_item_limit?: number` on `Settings`. |
| `frontend/src/views/Settings.tsx` | New `SETTING_ROWS` entry, default state, reworded "Refresh Recommendations" description. |

Tests: `backend/tests/test_db.py`, `test_crawl_manager.py`, `test_settings_router.py`; `frontend/src/test/inStockTab.test.tsx`, `crawlStatusBar.test.tsx`.

---

### Task 1: `count_unjudged_stock_items` + unlimited `get_unjudged_stock_items`

**Files:**
- Modify: `backend/db.py`
- Test: `backend/tests/test_db.py`

- [ ] **Step 1: Write the failing tests**

Add `has_any_stock_judgment` is already imported — add `count_unjudged_stock_items` to the existing `from db import (...)` block at the top of `backend/tests/test_db.py`:

```python
    get_unjudged_stock_items, get_taste_listing, upsert_stock_judgments,
    has_any_stock_judgment, count_unjudged_stock_items,
```

Add these tests near the existing `get_unjudged_stock_items` tests:

```python
def test_get_unjudged_stock_items_limit_zero_returns_everything(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    items = [
        {"artist": f"Artist {i}", "title": f"T{i}", "price": 1.0, "currency": "USD", "url": f"https://x/{i}"}
        for i in range(305)
    ]
    replace_stock_items(conn, crawler_id, items)
    unjudged = get_unjudged_stock_items(conn, limit=0)
    assert len(unjudged) == 305


def test_get_unjudged_stock_items_negative_limit_returns_everything(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "T1", "price": 1.0, "currency": "USD", "url": "https://x/1"},
    ])
    unjudged = get_unjudged_stock_items(conn, limit=-1)
    assert len(unjudged) == 1


def test_get_unjudged_stock_items_positive_limit_still_caps(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    items = [
        {"artist": f"Artist {i}", "title": f"T{i}", "price": 1.0, "currency": "USD", "url": f"https://x/{i}"}
        for i in range(5)
    ]
    replace_stock_items(conn, crawler_id, items)
    unjudged = get_unjudged_stock_items(conn, limit=2)
    assert len(unjudged) == 2


def test_count_unjudged_stock_items_zero_when_empty(conn):
    assert count_unjudged_stock_items(conn) == 0


def test_count_unjudged_stock_items_counts_all_regardless_of_any_limit(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    items = [
        {"artist": f"Artist {i}", "title": f"T{i}", "price": 1.0, "currency": "USD", "url": f"https://x/{i}"}
        for i in range(5)
    ]
    replace_stock_items(conn, crawler_id, items)
    assert count_unjudged_stock_items(conn) == 5


def test_count_unjudged_stock_items_excludes_owned_and_judged(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    upsert_release(conn, _release(discogs_id="r1", artist="Rob Zombie", title="The Great Satan"))
    mark_in_collection(conn, "r1")
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "The Great Satan", "price": 1.0, "currency": "USD", "url": "https://x/1"},
        {"artist": "NAILS", "title": "T2", "price": 2.0, "currency": "USD", "url": "https://x/2"},
        {"artist": "Ghost", "title": "T3", "price": 3.0, "currency": "USD", "url": "https://x/3"},
    ])
    key = compute_item_key("Ghost", "T3", "https://x/3")
    upsert_stock_judgments(conn, [{"item_key": key, "recommended": False, "reason": None}])
    assert count_unjudged_stock_items(conn) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_db.py -k "limit_zero or negative_limit or positive_limit_still_caps or count_unjudged" -v`
Expected: FAIL — `limit=0`/`limit=-1` currently return 0 rows (`LIMIT 0`/`LIMIT -1` behave as "return nothing" or error under the current unconditional `LIMIT ?`), and `count_unjudged_stock_items` doesn't exist (`ImportError`).

- [ ] **Step 3: Implement**

In `backend/db.py`, replace `get_unjudged_stock_items` in full:

```python
def get_unjudged_stock_items(conn: sqlite3.Connection, limit: int) -> list[dict]:
    limit_clause = "LIMIT ?" if limit > 0 else ""
    params = [limit] if limit > 0 else []
    rows = conn.execute(f"""
        SELECT s.item_key, s.artist, s.title
        FROM stock_items s
        LEFT JOIN stock_item_judgments j ON j.item_key = s.item_key
        WHERE j.item_key IS NULL
          AND {_NOT_OWNED_CLAUSE}
        GROUP BY s.item_key
        ORDER BY MIN(s.last_seen) ASC
        {limit_clause}
    """, params).fetchall()
    return [dict(row) for row in rows]
```

Add right after it:

```python
def count_unjudged_stock_items(conn: sqlite3.Connection) -> int:
    return conn.execute(f"""
        SELECT COUNT(DISTINCT s.item_key)
        FROM stock_items s
        LEFT JOIN stock_item_judgments j ON j.item_key = s.item_key
        WHERE j.item_key IS NULL
          AND {_NOT_OWNED_CLAUSE}
    """).fetchone()[0]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_db.py -v`
Expected: PASS, all existing tests + the 6 new ones.

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend && pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add backend/db.py backend/tests/test_db.py
git commit -m "recommendation-item-limit: add count_unjudged_stock_items and unlimited get_unjudged_stock_items"
```

---

### Task 2: Configurable limit + backlog-visibility logging in `_run_judgment_phase`

**Files:**
- Modify: `backend/crawl_manager.py`
- Test: `backend/tests/test_crawl_manager.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_crawl_manager.py`, near the other `_run_judgment_phase` tests:

```python
async def test_run_judgment_phase_logs_true_backlog_size_when_limit_smaller(manager, tmp_config_dir, monkeypatch, caplog):
    import config as cfg_module
    import db as db_module
    import recommendations
    from db import register_crawler, replace_stock_items

    cfg_module.save_config({"recommendation_item_limit": 2})

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
        {"artist": "Poison", "title": "T4", "price": 4.0, "currency": "USD", "url": "https://x/4"},
        {"artist": "Slayer", "title": "T5", "price": 5.0, "currency": "USD", "url": "https://x/5"},
    ])

    monkeypatch.setattr(recommendations, "judge_batch", lambda client, taste, batch: [
        {"item_key": item["item_key"], "recommended": False, "reason": None} for item in batch
    ])

    with caplog.at_level("INFO", logger="crawl_manager"):
        await manager._run_judgment_phase(conn, "sk-ant-test")

    found_logs = [r.message for r in caplog.records if r.message.startswith("Found ")]
    assert found_logs == ["Found 2/5 items to judge for recommendation"]
    conn.close()


async def test_run_judgment_phase_logs_equal_counts_when_limit_unset(manager, tmp_config_dir, monkeypatch, caplog):
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
    ])

    monkeypatch.setattr(recommendations, "judge_batch", lambda client, taste, batch: [
        {"item_key": item["item_key"], "recommended": False, "reason": None} for item in batch
    ])

    with caplog.at_level("INFO", logger="crawl_manager"):
        await manager._run_judgment_phase(conn, "sk-ant-test")

    found_logs = [r.message for r in caplog.records if r.message.startswith("Found ")]
    assert found_logs == ["Found 1/1 items to judge for recommendation"]
    conn.close()


async def test_run_judgment_phase_respects_zero_as_unlimited(manager, tmp_config_dir, monkeypatch, caplog):
    import config as cfg_module
    import db as db_module
    import recommendations
    from db import register_crawler, replace_stock_items

    cfg_module.save_config({"recommendation_item_limit": 0})

    conn = sqlite3.connect(cfg_module.DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db_module.init_db(conn)
    register_crawler(conn, "Nuclear Blast", "/path/nb.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Nuclear Blast'").fetchone()[0]
    replace_stock_items(conn, crawler_id, [
        {"artist": f"Artist {i}", "title": f"T{i}", "price": 1.0, "currency": "USD", "url": f"https://x/{i}"}
        for i in range(5)
    ])

    monkeypatch.setattr(recommendations, "judge_batch", lambda client, taste, batch: [
        {"item_key": item["item_key"], "recommended": False, "reason": None} for item in batch
    ])

    with caplog.at_level("INFO", logger="crawl_manager"):
        await manager._run_judgment_phase(conn, "sk-ant-test")

    found_logs = [r.message for r in caplog.records if r.message.startswith("Found ")]
    assert found_logs == ["Found 5/5 items to judge for recommendation"]
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_crawl_manager.py -k "true_backlog_size or equal_counts_when_limit_unset or respects_zero_as_unlimited" -v`
Expected: FAIL — no log line starting with `"Found "` exists yet (the current code logs `"Stock judgment started: %d unjudged items"`), and `recommendation_item_limit` isn't read from config yet, so the `limit=2` case doesn't actually cap anything (all 5 get processed, `found_logs` empty regardless).

- [ ] **Step 3: Implement**

In `backend/crawl_manager.py`, replace `_run_judgment_phase` in full:

```python
    async def _run_judgment_phase(self, conn, api_key: str):
        from db import get_unjudged_stock_items, count_unjudged_stock_items, get_taste_listing, upsert_stock_judgments
        from config import load_config
        import recommendations
        import anthropic

        limit = load_config().get("recommendation_item_limit", recommendations.SYNC_CAP)
        total_unjudged = count_unjudged_stock_items(conn)
        unjudged = get_unjudged_stock_items(conn, limit)
        if not unjudged:
            await self._broadcast({"status": "stock_judgment_complete", "judged": 0})
            log.info("Found 0/0 items to judge for recommendation, nothing to do")
            return

        await self._broadcast({"status": "stock_judgment_started"})
        log.info("Found %d/%d items to judge for recommendation", len(unjudged), total_unjudged)

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

Note: `total_unjudged` is deliberately computed even in the eventual empty-`unjudged` branch — when `total_unjudged` is `0`, `unjudged` is always empty too (the capped fetch can't return rows when there are none to return), so the `"Found 0/0..."` message is always internally consistent. The batch-loop body, `stock_judgment_progress`/`stock_judgment_complete`/`stock_judgment_error` broadcasts, and the final "Stock judgment complete: %d items judged" log line are unchanged from before.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_crawl_manager.py -v`
Expected: PASS, all existing tests + the 3 new ones.

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend && pytest -q`
Expected: PASS, no regressions. In particular, `test_run_judgment_phase_broadcasts_complete_when_nothing_unjudged` still passes since it asserts `"nothing to do" in r.message` (substring), which the new `"Found 0/0 items to judge for recommendation, nothing to do"` message still contains.

- [ ] **Step 6: Commit**

```bash
git add backend/crawl_manager.py backend/tests/test_crawl_manager.py
git commit -m "recommendation-item-limit: wire configurable limit and backlog-visibility logging into judgment phase"
```

---

### Task 3: `recommendation_item_limit` Settings field (backend)

**Files:**
- Modify: `backend/routers/settings.py`
- Test: `backend/tests/test_settings_router.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_settings_router.py`:

```python
def test_get_settings_recommendation_item_limit_defaults_300(client):
    r = client.get("/api/settings")
    assert r.json()["recommendation_item_limit"] == 300


def test_post_settings_round_trips_recommendation_item_limit(client):
    r = client.post("/api/settings", json={"discogs_token": "", "recommendation_item_limit": 50})
    assert r.status_code == 200
    r2 = client.get("/api/settings")
    assert r2.json()["recommendation_item_limit"] == 50


def test_post_settings_round_trips_recommendation_item_limit_zero(client):
    r = client.post("/api/settings", json={"discogs_token": "", "recommendation_item_limit": 0})
    assert r.status_code == 200
    r2 = client.get("/api/settings")
    assert r2.json()["recommendation_item_limit"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_settings_router.py -k recommendation_item_limit -v`
Expected: FAIL — `KeyError: 'recommendation_item_limit'` (the field doesn't exist in the response yet).

- [ ] **Step 3: Implement**

In `backend/routers/settings.py`, add to `SettingsUpdate` right after `anthropic_api_key`:

```python
    anthropic_api_key: str = ""
    recommendation_item_limit: int = 300
```

Add to `get_settings`'s returned dict, right after `"anthropic_api_key"`:

```python
        "anthropic_api_key": config.get("anthropic_api_key", ""),
        "recommendation_item_limit": int(config.get("recommendation_item_limit", 300)),
```

Add to `update_settings`, right after `config["anthropic_api_key"] = body.anthropic_api_key`:

```python
    config["anthropic_api_key"] = body.anthropic_api_key
    config["recommendation_item_limit"] = body.recommendation_item_limit
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_settings_router.py -v`
Expected: PASS, all existing tests + the 3 new ones.

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend && pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add backend/routers/settings.py backend/tests/test_settings_router.py
git commit -m "recommendation-item-limit: add recommendation_item_limit Settings field"
```

---

### Task 4: `recommendation_item_limit` Settings field (frontend) + terminology cleanup

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/views/Settings.tsx`
- Modify: `frontend/src/test/inStockTab.test.tsx`
- Modify: `frontend/src/test/crawlStatusBar.test.tsx`

This task has no new frontend behavior to unit-test beyond what the existing generic `SETTING_ROWS` renderer already covers (it's a data-driven list; adding an entry doesn't need a dedicated test, matching how `consecutive_failure_limit`/`crawl_delay_seconds` have none). It does need the two known test files with independently-duplicated Settings-shaped mock objects updated so they stay internally consistent — found by grepping for other files sharing the `consecutive_failure_limit` field, the same technique a prior task in this feature used to catch a similar gap.

- [ ] **Step 1: Implement — types**

In `frontend/src/api/types.ts`, add to the `Settings` interface, right after `anthropic_api_key?: string`:

```typescript
  anthropic_api_key?: string
  recommendation_item_limit?: number
```

- [ ] **Step 2: Implement — Settings.tsx**

Add to `SETTING_ROWS`, right after the `anthropic_api_key` entry:

```typescript
  {
    key: 'anthropic_api_key',
    label: 'Anthropic API key',
    description: 'Used to judge Store items against your collection for the Recommended filter. Get one at platform.claude.com.',
    type: 'password',
    placeholder: 'sk-ant-...',
  },
  {
    key: 'recommendation_item_limit',
    label: 'Recommendation item limit',
    description: 'Maximum number of unprocessed Store items evaluated by Claude for recommendation each time. Extra items are evaluated on a later run. 0 = no limit.',
    type: 'number',
  },
```

Add to the default `useState<SettingsType>({...})` object, right after `anthropic_api_key: ''`:

```typescript
    anthropic_api_key: '',
    recommendation_item_limit: 300,
```

Update the "Refresh Recommendations" description text:

```jsx
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                Evaluate unprocessed Store items for recommendation, without a full catalog re-crawl. Requires an Anthropic API key above.
              </td>
```

(replacing the current `"Judge currently unjudged Store items against your collection, without a full catalog re-crawl. Requires an Anthropic API key above."`)

- [ ] **Step 3: Update the two duplicated Settings-shaped test literals**

In `frontend/src/test/inStockTab.test.tsx`, find:

```typescript
const defaultSettings = {
  discogs_token: '', debug_screenshot_interval: 20, shuffle_crawl_order: true,
  crawl_delay_seconds: 30, consecutive_failure_limit: 10, crawl_schedule: '',
  crawl_schedule_mode: 'missing', collection_schedule: '', collection_schedule_mode: 'all',
  ebay_app_id: '', ebay_cert_id: '', stock_schedule: '', anthropic_api_key: '',
}
```

Replace with:

```typescript
const defaultSettings = {
  discogs_token: '', debug_screenshot_interval: 20, shuffle_crawl_order: true,
  crawl_delay_seconds: 30, consecutive_failure_limit: 10, crawl_schedule: '',
  crawl_schedule_mode: 'missing', collection_schedule: '', collection_schedule_mode: 'all',
  ebay_app_id: '', ebay_cert_id: '', stock_schedule: '', anthropic_api_key: '',
  recommendation_item_limit: 300,
}
```

In `frontend/src/test/crawlStatusBar.test.tsx`, find:

```typescript
  getSettings: vi.fn().mockResolvedValue({
    discogs_token: '', debug_screenshot_interval: 20, shuffle_crawl_order: true,
    crawl_delay_seconds: 30, consecutive_failure_limit: 10, crawl_schedule: '',
    crawl_schedule_mode: 'missing', collection_schedule: '', collection_schedule_mode: 'all',
    ebay_app_id: '', ebay_cert_id: '', stock_schedule: '',
  }),
```

Replace with:

```typescript
  getSettings: vi.fn().mockResolvedValue({
    discogs_token: '', debug_screenshot_interval: 20, shuffle_crawl_order: true,
    crawl_delay_seconds: 30, consecutive_failure_limit: 10, crawl_schedule: '',
    crawl_schedule_mode: 'missing', collection_schedule: '', collection_schedule_mode: 'all',
    ebay_app_id: '', ebay_cert_id: '', stock_schedule: '', recommendation_item_limit: 300,
  }),
```

- [ ] **Step 4: Run the full frontend suite and typecheck**

Run: `cd frontend && npx vitest run && npx tsc -b --noEmit`
Expected: all tests pass, zero type errors. If any OTHER test file also fails here with a Settings-shape-related error, that file has its own independent mock needing the same treatment — grep it for `consecutive_failure_limit` or `anthropic_api_key` to confirm, and add `recommendation_item_limit: 300` (or whatever that file's existing convention is) to its literal too before proceeding.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/types.ts frontend/src/views/Settings.tsx frontend/src/test/inStockTab.test.tsx frontend/src/test/crawlStatusBar.test.tsx
git commit -m "recommendation-item-limit: add Recommendation item limit field to Settings UI"
```

---

## Final Verification

- [x] Run the full backend suite: `cd backend && pytest -v` — expect all green.
- [x] Run the full frontend suite: `cd frontend && npx vitest run` — expect all green.
- [x] Type-check the frontend: `cd frontend && npx tsc -b --noEmit` — expect no errors.
- [x] Manual smoke test: set "Recommendation item limit" to a small number (e.g. 2) with a backlog bigger than that, click "Refresh Recommendations," confirm the Logs tab shows `"Found 2/N items to judge for recommendation"` with `N` being the true backlog size, not just `2` echoed back. Set it to `0`, confirm a backlog larger than the old 300 cap gets fully processed in one run. Confirm the "Refresh Recommendations" description in Settings no longer says "judge"/"unjudged."

---

## Amendment 4 tasks: performance, prompt tightening, recommendation lifecycle actions

Retrospective record — these five tasks were implemented and merged into this branch after manual testing of Tasks 1–4 surfaced the issues described in Amendment 4 of the design spec. Each is recorded here with what was actually done, not as prescriptive steps for a future agent (the work is already committed).

### Task 5: Event-loop blocking fix

**Files:**
- Modified: `backend/crawl_manager.py`
- Test: `backend/tests/test_crawl_manager.py`

- [x] Diagnosed via `superpowers:systematic-debugging`: `recommendations.judge_batch()`'s synchronous Anthropic call, invoked directly inside the async `_run_judgment_phase`, froze the entire single-threaded uvicorn event loop for the call's duration.
- [x] Wrapped the call in `await asyncio.to_thread(recommendations.judge_batch, client, taste_listing, batch)`.
- [x] Added a regression test: a monkeypatched `judge_batch` that sleeps synchronously, run alongside a concurrent heartbeat coroutine that must keep ticking throughout — fails against the un-fixed blocking call (`0` ticks), passes once offloaded.
- [x] Full backend suite: 371 passed.
- [x] Commit: `e9adc83` — "offload judge_batch to a thread so it stops freezing the whole server"

### Task 6: Prompt caching

**Files:**
- Modified: `backend/recommendations.py`
- Test: `backend/tests/test_recommendations.py`

- [x] Renamed `build_batch_prompt` (single string) to `build_batch_content` (list of two content blocks): a taste-listing block marked `cache_control: {"type": "ephemeral"}`, and a per-batch items block left uncached.
- [x] Added the same `cache_control` marker to the system prompt block; `system` changed from a plain string to `[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]`.
- [x] Added a test asserting the actual request body sent through the mocked HTTP layer carries `cache_control` on the system and taste-listing blocks, and not on the items block.
- [x] Full backend suite: 373 passed.
- [x] Commit: `7a5588b` — "cache the system prompt and taste listing across judgment batches"

### Task 7: Instant run feedback + status-bar wording

**Files:**
- Modified: `backend/crawl_manager.py`, `frontend/src/App.tsx`
- Test: `backend/tests/test_crawl_manager.py`, `frontend/src/test/inStockTab.test.tsx`

- [x] Moved the `stock_judgment_started` broadcast and a new `log.info("Judgment run started")` line to the top of `_run_judgment_phase`, before `count_unjudged_stock_items`/`get_unjudged_stock_items` — so the UI updates immediately instead of after the (possibly slow) backlog queries.
- [x] Added a test asserting `stock_judgment_started` precedes `stock_judgment_complete` in the broadcast log, and `"Judgment run started"` precedes the `"Found ..."` log line, in both the has-items and nothing-to-do branches.
- [x] Reworded all four `stock_judgment_*` status-bar messages in `App.tsx` to drop "judge"/"judgment" wording (see Amendment 4 §3 table in the spec).
- [x] Updated/added frontend tests for all four reworded messages.
- [x] Full backend suite: 374 passed. Full frontend suite: 60 passed.
- [x] Commit: `ee3329f` — "give instant feedback when a judgment run starts, drop 'judge' wording from status bar"

### Task 8: Tightened judgment prompt

**Files:**
- Modified: `backend/recommendations_prompt.md`

- [x] Replaced the "same genre/scene, related artists, similar labels, adjacent style" criterion with a requirement for a specific, nameable connection, a default-to-`false` instruction, and an explicit "when uncertain, do not recommend" line (full text in Amendment 4 §4 of the spec).
- [x] No code change — `test_system_prompt_loads_from_recommendations_prompt_md` already covers wiring; prompt-quality is spot-checked, not mechanically enforced (per the original spec's testing section).
- [x] Commit: `08890f8` — "tighten judgment prompt, add Export/Clear Recommendations actions" (bundled with Tasks 9–10 below)

### Task 9: Clear Recommendations action

**Files:**
- Modified: `backend/db.py`, `backend/routers/stock.py`, `frontend/src/App.tsx`, `frontend/src/views/Settings.tsx`, `frontend/src/api/client.ts`
- Test: `backend/tests/test_db.py`, `backend/tests/test_stock_router.py`, `frontend/src/test/inStockTab.test.tsx`

- [x] Added `db.clear_stock_judgments(conn) -> int` — `DELETE FROM stock_item_judgments`, returns rows removed.
- [x] Added `POST /api/stock/judge/clear` — refuses (`{"cleared": false, "running": true}`) while `judgment_running` or `stock_sync_running`; otherwise clears and returns `{"cleared": true, "count": N}`.
- [x] Added `clearJudgments()` to `api/client.ts`, an `onClearRecommendations` handler in `App.tsx` gated behind `window.confirm(...)`, and a "Clear Recommendations" button in `Settings.tsx` directly below "Refresh Recommendations," disabled until `hasJudgedItems`.
- [x] Tests: DB-level clear (removes both recommended and not-recommended, returns correct count), router-level (clears when idle, refuses while running), frontend (disabled state, confirm-cancelled path, confirm-then-clear path, running-refusal message).
- [x] Commit: `08890f8` (see Task 8).

### Task 10: Export Recommendations action

**Files:**
- Modified: `backend/db.py`, `backend/routers/stock.py`, `frontend/src/App.tsx`, `frontend/src/views/Settings.tsx`, `frontend/src/api/client.ts`
- Test: `backend/tests/test_db.py`, `backend/tests/test_stock_router.py`, `frontend/src/test/inStockTab.test.tsx`

- [x] Added `db.get_recommended_stock_items(conn) -> list[dict]` — recommended, not-owned items with `artist, title, format, price, source, url, reason`.
- [x] Added `GET /api/stock/export` — CSV (`artist,title,format,price,source,link,reason`) with `Content-Disposition: attachment; filename=recommendations.csv`.
- [x] Added `exportRecommendationsCsv()` to `api/client.ts` (fetches as `Blob`, since a plain `<a href>` would 403 — `AuthMiddleware` requires the `X-Requested-With` header only `apiFetch` sets), an `onExportRecommendations` handler in `App.tsx` that triggers a client-side download via a temporary `<a download>` element, and an "Export Recommendations" button in `Settings.tsx` between "Refresh Recommendations" and "Clear Recommendations," disabled until `hasJudgedItems`.
- [x] Tests: DB-level (fields, excludes not-recommended/unjudged/owned items), router-level (CSV content-type, `Content-Disposition`, exact body), frontend (disabled state, click triggers the export call).
- [x] Full backend suite: 383 passed. Full frontend suite: 62 passed. Typecheck: clean.
- [x] Commit: `08890f8` (see Task 8).
