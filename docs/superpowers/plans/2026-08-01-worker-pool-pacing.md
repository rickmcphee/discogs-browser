# Worker Pool Pacing and Circuit Breaker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore per-site request pacing and a per-site consecutive-failure circuit breaker in the shared crawl worker pool (lost when `crawl_releases()` was deleted), and remove the two settings (`shuffle_crawl_order`, `debug_screenshot_interval`) that no longer map onto the new architecture.

**Architecture:** See [`docs/superpowers/specs/2026-08-01-worker-pool-pacing-design.md`](../specs/2026-08-01-worker-pool-pacing-design.md). In short: `CrawlManager` gains four in-memory dicts keyed by `crawler_id` (a lock + "next allowed request time" for pacing; a consecutive-failure counter + "cooldown until" for the circuit breaker). A new `_paced_search` method wraps `plugin.search()` with the lock/delay and covers the existing bot-detection retry so the lock spans both attempts. `db.claim_crawl_queue_batch` gains an `excluded_crawler_ids` parameter so a worker never claims rows for a site currently cooling down.

**Tech Stack:** Python `asyncio`, psycopg3, FastAPI, React/TypeScript.

**Verified against the actual current code before writing this plan** — every snippet below matches the real current state of `backend/crawl_manager.py`, `backend/db.py`, `backend/routers/settings.py`, and the frontend files as of branch `crawl-queue-refactor`. Still verify against the real file yourself before editing, in case anything changed between plan-writing and execution.

## File structure

| File | Task(s) | Responsibility after this plan |
|---|---|---|
| `backend/db.py` | 1 | `claim_crawl_queue_batch` excludes cooling-down sites from what it claims |
| `backend/crawl_manager.py` | 2, 3 | Per-site pacing lock + delay (`_paced_search`); per-site consecutive-failure circuit breaker wired into `_drain_one_batch` |
| `backend/routers/settings.py` | 4 | `debug_screenshot_interval`/`shuffle_crawl_order` removed from the admin settings surface |
| `frontend/src/api/types.ts`, `frontend/src/views/Settings.tsx` | 5 | Same two fields removed from the frontend type and UI table |

---

### Task 1: `db.py` — `claim_crawl_queue_batch` excludes cooling-down sites

**Files:**
- Modify: `backend/db.py`
- Test: `backend/tests/test_crawl_queue.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_crawl_queue.py` (match this file's existing `admin_conn` fixture convention):

```python
def test_claim_crawl_queue_batch_excludes_specified_crawler_ids(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        crawler_a = _make_catalog_and_crawler(conn, "r1", site_name="Amazon")
        crawler_b = _make_catalog_and_crawler(conn, "r2", site_name="Discogs Marketplace")
        conn.commit()
        db.enqueue_crawl_queue(conn, "r1", crawler_a)
        db.enqueue_crawl_queue(conn, "r2", crawler_b)
        conn.commit()

        claimed = db.claim_crawl_queue_batch(conn, "worker-1", limit=10, excluded_crawler_ids=[crawler_a])
    assert len(claimed) == 1
    assert claimed[0]["crawler_id"] == crawler_b


def test_claim_crawl_queue_batch_with_no_exclusions_behaves_as_before(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        crawler_id = _make_catalog_and_crawler(conn, "r1")
        conn.commit()
        db.enqueue_crawl_queue(conn, "r1", crawler_id)
        conn.commit()

        claimed = db.claim_crawl_queue_batch(conn, "worker-1", limit=10, excluded_crawler_ids=[])
    assert len(claimed) == 1
```

`_make_catalog_and_crawler` is the existing helper already defined near the top of this test file (used by the other `crawl_queue` tests) — reuse it, don't redefine it.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_crawl_queue.py -k excludes_specified -v`
Expected: FAIL — `claim_crawl_queue_batch()` doesn't accept `excluded_crawler_ids` yet (`TypeError: unexpected keyword argument`).

- [ ] **Step 3: Implement**

Replace `claim_crawl_queue_batch` in `backend/db.py` (currently at line 616):

```python
def claim_crawl_queue_batch(
    conn, worker_id: str, limit: int, excluded_crawler_ids: Optional[list] = None
) -> list[dict]:
    exclusion_clause = ""
    params: dict = {"worker_id": worker_id, "limit": limit}
    if excluded_crawler_ids:
        exclusion_clause = "AND crawler_id != ALL(%(excluded)s)"
        params["excluded"] = list(excluded_crawler_ids)
    return conn.execute(
        f"""
        UPDATE crawl_queue SET status = 'in_progress', claimed_by = %(worker_id)s, claimed_at = CURRENT_TIMESTAMP
        WHERE id IN (
            SELECT id FROM crawl_queue
            WHERE status = 'pending' {exclusion_clause}
            ORDER BY requested_at
            LIMIT %(limit)s
            FOR UPDATE SKIP LOCKED
        )
        RETURNING id, discogs_id, crawler_id
        """,
        params,
    ).fetchall()
```

`excluded_crawler_ids` defaults to `None` (equivalent to an empty list, no exclusion) so every existing caller that doesn't pass it — including all of Task 4/11's existing `test_crawl_queue.py` tests — keeps working unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_crawl_queue.py -v`
Expected: PASS — all tests in the file, including the pre-existing ones (proving the new parameter is backward-compatible).

- [ ] **Step 5: Commit**

```bash
git add backend/db.py backend/tests/test_crawl_queue.py
git commit -m "feat: add excluded_crawler_ids to claim_crawl_queue_batch for per-site cooldown"
```

---

### Task 2: `crawl_manager.py` — per-site pacing lock and delay

**Files:**
- Modify: `backend/crawl_manager.py`
- Test: `backend/tests/test_crawl_manager.py`

Adds `_paced_search`, a new method wrapping `plugin.search()` with a per-`crawler_id` lock and randomized-jitter delay, and covering the existing bot-detection retry so the lock spans both attempts (per the design spec's explicit requirement — releasing it between attempts would let a second worker's request to the same site land mid-recovery).

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_crawl_manager.py` (match this file's existing `pg_schema`/mock conventions for worker-pool tests — see `test_worker_claims_and_completes_one_queue_row` for the pattern):

```python
import time


async def test_paced_search_serializes_same_site_calls_across_concurrent_invocations():
    manager = CrawlManager()
    call_log: list[tuple[str, float]] = []

    async def fake_search(release, page):
        call_log.append(("start", time.monotonic()))
        await asyncio.sleep(0.05)
        call_log.append(("end", time.monotonic()))
        return []

    plugin = AsyncMock()
    plugin.search = fake_search
    pages = {1: (MagicMock(), MagicMock())}

    # Two "concurrent" calls for the SAME crawler_id (1) must not overlap.
    await asyncio.gather(
        manager._paced_search(1, plugin, {}, pages),
        manager._paced_search(1, plugin, {}, pages),
    )
    # call_log should read start,end,start,end (serialized), never start,start,end,end.
    assert [entry[0] for entry in call_log] == ["start", "end", "start", "end"]


async def test_paced_search_does_not_serialize_different_sites():
    manager = CrawlManager()
    call_log: list[str] = []

    async def make_fake_search(tag):
        async def fake_search(release, page):
            call_log.append(f"{tag}-start")
            await asyncio.sleep(0.05)
            call_log.append(f"{tag}-end")
            return []
        return fake_search

    plugin_a = AsyncMock()
    plugin_a.search = await make_fake_search("a")
    plugin_b = AsyncMock()
    plugin_b.search = await make_fake_search("b")
    pages = {1: (MagicMock(), MagicMock()), 2: (MagicMock(), MagicMock())}

    await asyncio.gather(
        manager._paced_search(1, plugin_a, {}, pages),
        manager._paced_search(2, plugin_b, {}, pages),
    )
    # Different crawler_ids run concurrently — both "start"s happen before either "end".
    assert call_log[0].endswith("start") and call_log[1].endswith("start")


async def test_paced_search_sets_next_allowed_at_within_jitter_bounds():
    from unittest.mock import patch
    manager = CrawlManager()
    plugin = AsyncMock()
    plugin.search = AsyncMock(return_value=[])
    pages = {1: (MagicMock(), MagicMock())}

    with patch("config.load_config", return_value={"crawl_delay_seconds": 10}):
        before = time.monotonic()
        await manager._paced_search(1, plugin, {}, pages)
        after = time.monotonic()

    next_allowed = manager._site_next_allowed_at[1]
    assert before + 5.0 <= next_allowed <= after + 10.0


async def test_paced_search_covers_bot_detection_retry_under_one_lock_acquisition():
    from crawler import BotDetectedError
    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    plugin = AsyncMock()
    plugin.search = AsyncMock(side_effect=[BotDetectedError(), []])
    pages = {1: (MagicMock(), MagicMock())}

    with patch("crawler._reset_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
        # Must not raise -- the retry succeeds under the same _paced_search call.
        result = await manager._paced_search(1, plugin, {}, pages)
    assert result == []
    assert plugin.search.call_count == 2
```

Fix the third test's slightly-garbled `monkeypatch.setattr` line before using it — it was drafted sloppily; the actual working version just needs `from unittest.mock import patch` and the `with patch("config.load_config", return_value={"crawl_delay_seconds": 10}):` block, delete the stray `monkeypatch.setattr(...)` line entirely, it does nothing useful.

Check this test file's current imports (`AsyncMock`, `MagicMock`, `patch`) are already present at the top — Task 11 already uses them for the worker-pool tests, so they should be, but verify.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_crawl_manager.py -k paced_search -v`
Expected: FAIL — `_paced_search` doesn't exist.

- [ ] **Step 3: Implement**

In `backend/crawl_manager.py`, add two new dicts to `__init__` (currently lines 8-19), right after `self._recent`/`self._seq`:

```python
        self._site_locks: dict[int, asyncio.Lock] = {}
        self._site_next_allowed_at: dict[int, float] = {}
```

Add a new method, placed right before `_drain_one_batch` (currently at line 112):

```python
    async def _paced_search(self, crawler_id: int, plugin, release: dict, pages: dict) -> list:
        """Runs plugin.search() for one crawler_id under that site's lock,
        enforcing the minimum inter-request delay and covering the existing
        bot-detection retry -- the lock spans both attempts so a second
        worker can never send a request to this same site in the middle of
        this site's own bot-detection recovery."""
        import random
        import time
        from crawler import _reset_context, BotDetectedError
        from config import load_config

        if crawler_id not in self._site_locks:
            self._site_locks[crawler_id] = asyncio.Lock()

        async with self._site_locks[crawler_id]:
            next_allowed = self._site_next_allowed_at.get(crawler_id, 0.0)
            now = time.monotonic()
            if now < next_allowed:
                await asyncio.sleep(next_allowed - now)

            context, page = pages[crawler_id]
            try:
                matches = await plugin.search(release, page)
            except BotDetectedError:
                context, page = await _reset_context(context, self._browser, self._stealth, None)
                pages[crawler_id] = (context, page)
                matches = await plugin.search(release, page)

            delay = float(load_config().get("crawl_delay_seconds", 30))
            self._site_next_allowed_at[crawler_id] = time.monotonic() + random.uniform(0.5, 1.0) * delay
            return matches
```

Note this method does NOT catch the second attempt's exception (unlike the current inline code in `_drain_one_batch`, which does) — any exception from either attempt propagates to the caller. `_drain_one_batch` (Task 3 of this plan) is what wraps the call and handles both the "first attempt failed with something other than bot detection" and "retry also failed" cases with one shared `except Exception` block, simplifying the duplication that exists in the current inline version.

Do NOT wire this into `_drain_one_batch` yet in this task — that's Task 3, done together with the circuit breaker since both touch the same lines. This task only adds `_paced_search` itself and proves it works in isolation via the tests above, which call it directly.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_crawl_manager.py -k paced_search -v`
Expected: PASS.

Also run the full file to confirm nothing existing broke: `cd backend && .venv/bin/pytest tests/test_crawl_manager.py -v`.

- [ ] **Step 5: Commit**

```bash
git add backend/crawl_manager.py backend/tests/test_crawl_manager.py
git commit -m "feat: add per-site pacing lock covering the bot-detection retry"
```

---

### Task 3: `crawl_manager.py` — per-site circuit breaker, wire both into `_drain_one_batch`

**Files:**
- Modify: `backend/crawl_manager.py`
- Test: `backend/tests/test_crawl_manager.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_crawl_manager.py`:

```python
async def test_drain_one_batch_records_failure_and_cools_down_after_limit(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1", crawler_id)
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    fake_plugin = AsyncMock()
    fake_plugin.search = AsyncMock(return_value=[])  # not_found every time
    fake_plugin._db_id = crawler_id
    fake_plugin._db_site_name = "Amazon"

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))), \
         patch("config.load_config", return_value={"crawl_delay_seconds": 0, "consecutive_failure_limit": 1}):
        await manager._drain_one_batch("worker-test", {crawler_id: fake_plugin}, pages={})

    assert manager._site_cooldown_until.get(crawler_id, 0) > time.monotonic()
    assert manager._site_consecutive_failures.get(crawler_id, 0) == 0  # reset after tripping


async def test_drain_one_batch_excludes_cooling_down_crawler_from_claim(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1", crawler_id)
        conn.commit()

    manager = CrawlManager()
    manager._site_cooldown_until[crawler_id] = time.monotonic() + 1800  # already cooling down

    fake_plugin = AsyncMock()
    claimed = await manager._drain_one_batch("worker-test", {crawler_id: fake_plugin}, pages={})

    assert claimed == 0  # nothing claimed -- the only pending row belongs to the cooling-down site
    fake_plugin.search.assert_not_called()


async def test_drain_one_batch_resets_failure_count_on_success(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.enqueue_crawl_queue(conn, "r1", crawler_id)
        conn.commit()

    manager = CrawlManager()
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    manager._site_consecutive_failures[crawler_id] = 5  # pretend it already had failures
    fake_plugin = AsyncMock()
    fake_plugin.search = AsyncMock(return_value=[{"url": "https://x", "price": 9.99, "shipping": None, "currency": "USD", "condition": None}])
    fake_plugin._db_id = crawler_id
    fake_plugin._db_site_name = "Amazon"

    with patch("crawler._new_context", new=AsyncMock(return_value=(MagicMock(), MagicMock()))), \
         patch("config.load_config", return_value={"crawl_delay_seconds": 0, "consecutive_failure_limit": 10}):
        await manager._drain_one_batch("worker-test", {crawler_id: fake_plugin}, pages={})

    assert manager._site_consecutive_failures[crawler_id] == 0
```

Add `import time` at the top of the test file if not already present from Task 2.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_crawl_manager.py -k "cools_down or excludes_cooling_down or resets_failure_count" -v`
Expected: FAIL — `_site_cooldown_until`/`_site_consecutive_failures` don't exist, `_drain_one_batch` doesn't exclude anything from its claim yet.

- [ ] **Step 3: Implement**

Add two more dicts to `__init__`, alongside the two Task 2 added:

```python
        self._site_consecutive_failures: dict[int, int] = {}
        self._site_cooldown_until: dict[int, float] = {}
```

Add a small helper method near `_paced_search`:

```python
    def _cooling_down_crawler_ids(self) -> list[int]:
        import time
        now = time.monotonic()
        return [cid for cid, until in self._site_cooldown_until.items() if now < until]

    def _record_site_result(self, crawler_id: int, succeeded: bool):
        import time
        from config import load_config
        if succeeded:
            self._site_consecutive_failures[crawler_id] = 0
            return
        count = self._site_consecutive_failures.get(crawler_id, 0) + 1
        self._site_consecutive_failures[crawler_id] = count
        limit = int(load_config().get("consecutive_failure_limit", 10))
        if limit and count >= limit:
            self._site_cooldown_until[crawler_id] = time.monotonic() + 1800
            self._site_consecutive_failures[crawler_id] = 0
            log.warning(
                "Crawler %d hit %d consecutive failures, cooling down for 30 minutes",
                crawler_id, count,
            )
```

Now replace `_drain_one_batch` in full (currently lines 112-181) — this both wires in the exclusion list at claim time and replaces the inline try/except/retry block with a single call to `_paced_search`, plus records the result for the circuit breaker:

```python
    async def _drain_one_batch(self, worker_id: str, plugins_by_crawler_id: dict, pages: dict, batch_size: int = 5) -> int:
        from db import get_app_pool, claim_crawl_queue_batch, mark_crawl_queue_done, upsert_listing, get_catalog_release

        excluded = self._cooling_down_crawler_ids()
        with get_app_pool().connection() as conn:
            rows = claim_crawl_queue_batch(conn, worker_id, limit=batch_size, excluded_crawler_ids=excluded)
            conn.commit()
        if not rows:
            return 0

        for row in rows:
            plugin = plugins_by_crawler_id.get(row["crawler_id"])
            with get_app_pool().connection() as conn:
                release = get_catalog_release(conn, row["discogs_id"])

            if plugin is None or release is None:
                with get_app_pool().connection() as conn:
                    mark_crawl_queue_done(conn, row["id"])
                    conn.commit()
                continue

            if row["crawler_id"] not in pages:
                from crawler import _new_context
                pages[row["crawler_id"]] = await _new_context(self._browser, self._stealth)

            try:
                matches = await self._paced_search(row["crawler_id"], plugin, release, pages)
            except Exception as e:
                log.error("[%s] Crawl failed for %s: %s", plugin._db_site_name, row["discogs_id"], e)
                self._record_site_result(row["crawler_id"], succeeded=False)
                with get_app_pool().connection() as conn:
                    mark_crawl_queue_done(conn, row["id"])
                    conn.commit()
                continue

            self._record_site_result(row["crawler_id"], succeeded=bool(matches))

            with get_app_pool().connection() as conn:
                if matches:
                    best = matches[0]
                    upsert_listing(
                        conn, row["discogs_id"], row["crawler_id"], best["url"],
                        best.get("price"), best.get("shipping"), best.get("currency"), best.get("condition"),
                    )
                mark_crawl_queue_done(conn, row["id"])
                conn.commit()

            if matches:
                await self._broadcast_listing_changed(row["discogs_id"], row["crawler_id"], "found")
            else:
                await self._broadcast_listing_changed(row["discogs_id"], row["crawler_id"], "not_found")

        return len(rows)
```

Note `_record_site_result(succeeded=bool(matches))` is called for BOTH the "found" and "not_found" cases (matching the old `crawl_releases()` semantics of incrementing on `not_found` too, not just on an exception) — only the exception path and the `bool(matches)`-is-`False` path count as failures; `bool(matches)`-is-`True` resets the counter.

The old inline comment about per-row connection scoping (lines 122-130 in the current file) should be preserved/moved to stay attached to the claim-then-per-row-connections structure — don't just delete it, it documents a real, previously-hard-won invariant (Task 11's fix history). Keep it above the `for row in rows:` loop.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_crawl_manager.py -v`
Expected: PASS — the full file, including every pre-existing worker-pool test from Tasks 11/12 of the original plan (they should still pass unchanged, since `_drain_one_batch`'s externally-observable behavior for the non-cooldown, non-repeated-failure case is unchanged).

Also run the broader backend baseline: `cd backend && .venv/bin/pytest tests/ -q --ignore=tests/crawlers -k "not crawler"`.

- [ ] **Step 5: Commit**

```bash
git add backend/crawl_manager.py backend/tests/test_crawl_manager.py
git commit -m "feat: add per-site consecutive-failure circuit breaker with 30-minute cooldown"
```

---

### Task 4: `routers/settings.py` — remove `debug_screenshot_interval`/`shuffle_crawl_order`

**Files:**
- Modify: `backend/routers/settings.py`
- Test: `backend/tests/test_settings_router.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_settings_router.py` (extend the existing `test_get_and_post_settings_as_admin` test rather than duplicating its setup — check it currently posts a body that still includes `debug_screenshot_interval: 30, shuffle_crawl_order: False`; if so, update that test body to drop those two keys as part of this task rather than adding a whole new test):

```python
def test_get_settings_no_longer_includes_dead_fields(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute("UPDATE users SET is_admin = TRUE WHERE id = %s", [user["id"]])
        conn.commit()
    client = authed_client_factory(user["id"])
    r = client.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    assert "debug_screenshot_interval" not in body
    assert "shuffle_crawl_order" not in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_settings_router.py -k dead_fields -v`
Expected: FAIL — both fields are still in the response today.

- [ ] **Step 3: Implement**

In `backend/routers/settings.py`, remove `debug_screenshot_interval`/`shuffle_crawl_order` from `SettingsUpdate` (lines 11-20), `get_settings` (lines 33-45), and `update_settings` (lines 49-59):

```python
class SettingsUpdate(BaseModel):
    crawl_delay_seconds: int = 30
    consecutive_failure_limit: int = 10
    crawl_schedule: str = ""
    crawl_schedule_mode: str = "missing"
    ebay_app_id: str = ""
    ebay_cert_id: str = ""
    stock_schedule: str = ""


@router.get("/settings", dependencies=[Depends(require_admin)])
def get_settings():
    config = load_config()
    return {
        "crawl_delay_seconds": int(config.get("crawl_delay_seconds", 30)),
        "consecutive_failure_limit": int(config.get("consecutive_failure_limit", 10)),
        "crawl_schedule": config.get("crawl_schedule", ""),
        "crawl_schedule_mode": config.get("crawl_schedule_mode", "missing"),
        "ebay_app_id": config.get("ebay_app_id", ""),
        "ebay_cert_id": config.get("ebay_cert_id", ""),
        "stock_schedule": config.get("stock_schedule", ""),
    }


@router.post("/settings", dependencies=[Depends(require_admin)])
def update_settings(body: SettingsUpdate):
    config = load_config()
    config["crawl_delay_seconds"] = body.crawl_delay_seconds
    config["consecutive_failure_limit"] = body.consecutive_failure_limit
    config["crawl_schedule"] = body.crawl_schedule
    config["crawl_schedule_mode"] = body.crawl_schedule_mode
    config["ebay_app_id"] = body.ebay_app_id
    config["ebay_cert_id"] = body.ebay_cert_id
    config["stock_schedule"] = body.stock_schedule
    save_config(config)
    try:
        scheduler.configure(body.crawl_schedule, body.crawl_schedule_mode)
        scheduler.configure_stock(body.stock_schedule)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}
```

`CrawlerUpdate`, `UserSettingsUpdate`, `update_crawler`, `get_user_settings`, `update_user_settings` are untouched.

Also update the existing `test_get_and_post_settings_as_admin` test's POST body (drop the two removed keys from the JSON it sends) so it doesn't send fields the model no longer accepts (Pydantic ignores unknown fields by default, so this wouldn't actually fail, but the test should reflect the real request shape a client would send going forward).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_settings_router.py -v`
Expected: PASS.

Also run the broader backend baseline: `cd backend && .venv/bin/pytest tests/ -q --ignore=tests/crawlers -k "not crawler"`.

- [ ] **Step 5: Commit**

```bash
git add backend/routers/settings.py backend/tests/test_settings_router.py
git commit -m "fix: remove shuffle_crawl_order/debug_screenshot_interval from admin settings"
```

---

### Task 5: Frontend — remove the same two fields from `types.ts`/`Settings.tsx`

**Files:**
- Modify: `frontend/src/api/types.ts`, `frontend/src/views/Settings.tsx`
- Test: existing Settings-related test file(s) under `frontend/src/test/`

- [ ] **Step 1: Write the failing test**

Check `frontend/src/test/` for an existing test file covering `Settings.tsx` (search for one rendering `<Settings />` or testing `saveSettings`/`getSettings` calls) — read it first. Add or extend a test asserting the rendered settings table no longer has a "Screenshot interval" or "Shuffle" row:

```tsx
it('does not render the removed screenshot-interval or shuffle rows', () => {
  // render <Settings /> with whatever props/mocks this file's existing tests use
  expect(screen.queryByText('Screenshot interval')).not.toBeInTheDocument()
  expect(screen.queryByText('Shuffle')).not.toBeInTheDocument()
})
```

Adapt to match the actual existing test file's rendering/mocking setup exactly — the snippet above is illustrative of the assertion, not a literal drop-in (you need the real render call and whatever mock props `Settings` requires, from Task 21 of the earlier plan's `Settings.tsx`-related test work if any exists, or write a minimal new render call if none exists yet).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- --run`
Expected: FAIL (or the rows are found, if the test framework renders them) — both rows are still present today.

- [ ] **Step 3: Implement**

In `frontend/src/api/types.ts`, remove from `Settings` (currently lines 42-43):

```ts
export interface Settings {
  crawl_delay_seconds: number
  consecutive_failure_limit: number
  crawl_schedule?: string
  crawl_schedule_mode?: 'missing' | 'all'
  ebay_app_id?: string
  ebay_cert_id?: string
  stock_schedule?: string
}
```

(i.e., delete the `debug_screenshot_interval: number` and `shuffle_crawl_order: boolean` lines; leave every other field exactly as-is.)

In `frontend/src/views/Settings.tsx`:
- Remove the `debug_screenshot_interval` row object (currently lines 28-33) and the `shuffle_crawl_order` row object (currently lines 46-51) from `CRAWLER_SETTING_ROWS`.
- Remove `debug_screenshot_interval: 20,` and `shuffle_crawl_order: true,` from the `useState<SettingsType>({...})` default object (currently lines 68-69).
- Update the `consecutive_failure_limit` row's `description` text (currently line 43) to drop the now-inapplicable shuffle caveat — change:
  ```
  'Stop bulk crawl after this many consecutive failures (not_found or error). Only active when shuffle is on. 0 = disabled.'
  ```
  to:
  ```
  'Pause crawling a site for 30 minutes after this many consecutive failures (not_found or error) in a row. 0 = disabled.'
  ```
  (This also corrects the description to describe the new per-site-cooldown behavior from Task 3 of this plan, rather than the old "stop the bulk crawl" behavior that no longer exists.)

Leave the `crawl_delay_seconds` row's description exactly as-is — it already says "Actual wait is 50–100% of this value," which matches the jitter behavior Task 2 of this plan implements, and doesn't mention shuffle.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npm test -- --run` — should pass.
Run: `cd frontend && npx tsc -b` — should be clean (removing fields from an interface only breaks callers that reference them; confirm no other file references `debug_screenshot_interval`/`shuffle_crawl_order` — grep first: `grep -rn "debug_screenshot_interval\|shuffle_crawl_order" frontend/src/`. If anything else references them, report it rather than guessing at a fix — this plan's design only accounted for `types.ts`/`Settings.tsx`).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/types.ts frontend/src/views/Settings.tsx frontend/src/test/
git commit -m "fix: remove shuffle_crawl_order/debug_screenshot_interval from frontend settings UI"
```

---

### Task 6: Final review

Not a code task — dispatch a final reviewer over this plan's whole diff (5 commits), checking:
- `_paced_search`'s lock genuinely spans the bot-detection retry (re-verify by reading the final code, not just trusting Task 2's tests).
- `_drain_one_batch`'s per-row connection-scoping discipline (established the hard way across Tasks 10/11 of the original 22-task plan) wasn't accidentally reintroduced as a held-connection-across-Playwright-calls bug while rewiring this method.
- `claim_crawl_queue_batch`'s exclusion list is actually recomputed fresh on every `_drain_one_batch` call (not stale/cached), so a site's cooldown expiring is picked up on the very next drain cycle without requiring a worker restart.
- Grep the whole repo for `debug_screenshot_interval`/`shuffle_crawl_order` to confirm zero remaining references anywhere (backend or frontend), including `config.py` defaults if any exist there.
- Full backend + frontend test suites both green.
- Pre-PR spec-drift check per `CLAUDE.md`: does `docs/superpowers/specs/2026-08-01-worker-pool-pacing-design.md` itself need any amendment given what actually shipped, and does the earlier `2026-06-27-discogs-browser-design.md` amendment (from the 22-task plan's own final review) that already flagged these four settings as "unintended consequence, open work" need updating now that two of them are fixed and two are removed?

Once approved, this branch is ready to fold back into the `crawl-queue-refactor` branch's history (it's being implemented directly on that branch, not a separate one, so "finishing" this plan is just confirming the branch is still in the state the earlier `finishing-a-development-branch` conversation will apply to).
