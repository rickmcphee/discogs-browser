# Per-User SSE Event Filtering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. This repo mandates it for every written plan (root `CLAUDE.md`, "Plan execution mode") — inline execution only if the user explicitly asks for it. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `sync_*`/`stock_judgment_*`/`plex_match_*` SSE events from one user's background job reaching every other connected user.

**Architecture:** `CrawlManager`'s three per-user job runners (`_sync_collection_blocking`, `_run_judgment_phase`, `_run_plex_match`) each gain a local `broadcast` closure that stamps `user_id` into every event dict before it reaches `self._broadcast`/`self._broadcast_threadsafe`. `routers/crawl.py` gains a shared `_visible_to(event, user_id)` predicate — an untagged event (`stock_sync_*`, `listing_changed`, `ping`) is visible to everyone, a tagged one only to its owner — applied to both the replay buffer and the live SSE loop.

**Tech Stack:** Python 3, FastAPI, asyncio, `sse-starlette`, pytest (`pytest-asyncio`, `asyncio_mode = "auto"`).

**Design spec:** [`docs/specifications/shaping/2026-08-23-per-user-sse-event-filtering-design.md`](../shaping/2026-08-23-per-user-sse-event-filtering-design.md)

**Verified against:** `main` @ `6fc4684` (this branch's parent). Every snippet below matches the real current state of `backend/crawl_manager.py`, `backend/routers/crawl.py`, `backend/tests/test_crawl_manager.py`, and `backend/tests/test_crawl_router.py` as of that commit. Verify against the real file before editing in case anything has changed since.

## Global Constraints

- **`stock_sync_*`, `listing_changed`, and `ping` stay untagged and global.** Neither `_sync_stock` nor `_run_catalog_crawler` takes a `user_id` — do not add tagging to those paths. `listing_changed` was deliberately made global by commit `5e1890e` (Store/Track are shared tabs) — do not reintroduce filtering there.
- **No frontend changes.** The client keys its UI state off event `status`/`type` only; it never needs to read the new `user_id` field.
- **No change to `_broadcast`/`_broadcast_threadsafe` signatures.** Tagging happens by merging `{**event, "user_id": user_id}` at the call site inside each per-user job runner's local `broadcast` closure, not by adding a parameter to the shared broadcast methods.
- Tests run from `backend/`: `cd backend && pytest`. Postgres-backed tests need `TEST_DATABASE_URL`, `IDENTITY_DB_PASSWORD=test`, `APP_DB_PASSWORD=test` set (see root `CLAUDE.md` "Tests").

## File structure

| File | Task(s) | Responsibility after this plan |
|---|---|---|
| `backend/crawl_manager.py` | 1 | `_sync_collection_blocking`, `_run_judgment_phase`, `_run_plex_match` tag every broadcast with the owning `user_id` |
| `backend/routers/crawl.py` | 2 | `_visible_to` filters both `_events_to_replay` and `crawl_stream`'s live loop by `user_id` |
| `backend/tests/test_crawl_manager.py` | 1 | Existing per-job-type tests assert broadcasts carry the right `user_id`; the one exact-dict assertion is updated |
| `backend/tests/test_crawl_router.py` | 2 | New `_visible_to` unit tests; new replay test covering per-user event filtering |
| `CLAUDE.md` | 2 | "Key invariants" — the per-user-filtered SSE claim becomes true |

---

### Task 1: Tag per-user broadcasts with `user_id`

**Files:**
- Modify: `backend/crawl_manager.py` (`_sync_collection_blocking` at line 619, `_run_judgment_phase` at line 1057, `_run_plex_match` at line 1127)
- Test: `backend/tests/test_crawl_manager.py` (existing tests at lines 625, 973, 3721, 3929 — see steps below for exact line ranges)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: every event broadcast by `_sync_collection_blocking`, `_run_judgment_phase`, and `_run_plex_match` now carries `"user_id": <int>` equal to that function's `user_id` argument. Task 2's `_visible_to` depends on this contract: an event with no `"user_id"` key is global, one with the key is scoped to that id.

- [ ] **Step 1: Update the four existing tests to assert the new `user_id` tag (they will fail against current code)**

In `backend/tests/test_crawl_manager.py`, update `test_run_judgment_phase_broadcasts_error_when_no_api_key` (currently ends at line 3730):

```python
async def test_run_judgment_phase_broadcasts_error_when_no_api_key(pg_schema):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=2, discogs_username="alice2")
        conn.commit()

    manager = CrawlManager()
    await manager._run_judgment_phase(alice["id"])

    statuses = [e["status"] for e in manager.recent_events()]
    assert statuses == ["stock_judgment_started", "stock_judgment_error"]
    assert all(e.get("user_id") == alice["id"] for e in manager.recent_events())
```

Update `test_sync_collection_broadcasts_page_fetched_before_that_pages_progress` (currently ends at line 667) — add one line after the existing final assertion:

```python
    # Each page's sync_page_fetched must be broadcast before that page's sync_progress
    # (i.e. before barcode-fetch processing for that page even starts) -- that's the
    # whole point: page/total_pages info shows up immediately, not after the delay.
    statuses = [e["status"] for e in events]
    first_page_fetched = statuses.index("sync_page_fetched")
    first_progress = statuses.index("sync_progress")
    assert first_page_fetched < first_progress
    assert all(e.get("user_id") == user["id"] for e in events)
```

Update `test_run_plex_match_broadcasts_error_when_no_music_section_found` (currently ends at line 998) — add one line after the existing final assertion:

```python
    statuses = [e["status"] for e in manager.recent_events()]
    assert statuses == ["plex_match_started", "plex_match_error"]
    assert all(e.get("user_id") == user["id"] for e in manager.recent_events())
```

Update `test_run_judgment_phase_broadcasts_complete_when_nothing_unjudged` (currently lines 3929-3943) — this one currently asserts an exact dict, so the key must be added rather than appended:

```python
async def test_run_judgment_phase_broadcasts_complete_when_nothing_unjudged(pg_schema, caplog):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=3, discogs_username="alice3")
        conn.execute("UPDATE users SET anthropic_api_key = 'sk-alice' WHERE id = %s", [alice["id"]])
        conn.commit()

    manager = CrawlManager()
    with caplog.at_level("INFO", logger="crawl_manager"):
        await manager._run_judgment_phase(alice["id"])

    statuses = [e["status"] for e in manager.recent_events()]
    assert statuses == ["stock_judgment_started", "stock_judgment_complete"]
    events = [e for e in manager.recent_events() if e["status"] == "stock_judgment_complete"]
    assert events == [{"status": "stock_judgment_complete", "judged": 0, "id": 2, "user_id": alice["id"]}]
    assert any("nothing to do" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run the four tests to verify they fail**

Run: `cd backend && pytest tests/test_crawl_manager.py::test_run_judgment_phase_broadcasts_error_when_no_api_key tests/test_crawl_manager.py::test_sync_collection_broadcasts_page_fetched_before_that_pages_progress tests/test_crawl_manager.py::test_run_plex_match_broadcasts_error_when_no_music_section_found tests/test_crawl_manager.py::test_run_judgment_phase_broadcasts_complete_when_nothing_unjudged -v`

Expected: all 4 FAIL — the three `assert all(...)` additions fail because no event has a `"user_id"` key yet (`e.get("user_id")` is `None`, not the expected id), and the exact-dict assertion fails on the missing key.

- [ ] **Step 3: Tag `_sync_collection_blocking`'s broadcasts**

In `backend/crawl_manager.py`, change line 628 from:

```python
        broadcast = lambda event: self._broadcast_threadsafe(event, loop)
```

to:

```python
        broadcast = lambda event: self._broadcast_threadsafe({**event, "user_id": user_id}, loop)
```

No other line in `_sync_collection_blocking` changes — every broadcast in that function already goes through this one closure.

- [ ] **Step 4: Tag `_run_judgment_phase`'s broadcasts**

In `backend/crawl_manager.py`, replace the whole `_run_judgment_phase` method (currently lines 1057-1125) with:

```python
    async def _run_judgment_phase(self, user_id: int):
        from db import (
            get_identity_pool, user_scope, get_unjudged_stock_items, count_unjudged_stock_items,
            get_taste_listing, upsert_stock_judgments,
        )
        import recommendations
        import anthropic

        # Placeholder until the query below confirms the user still exists --
        # keeps the except block's own log line safe even if that query itself
        # (or anything after it) is what raises.
        username = f"user {user_id}"

        async def broadcast(event: dict):
            await self._broadcast({**event, "user_id": user_id})

        await broadcast({"status": "stock_judgment_started"})
        try:
            with get_identity_pool().connection() as conn:
                user = conn.execute(
                    "SELECT discogs_username, anthropic_api_key, recommendation_item_limit FROM users WHERE id = %s",
                    [user_id],
                ).fetchone()
            if user is None:
                log.info("Judgment run started for %s", username)
                await broadcast({"status": "stock_judgment_error", "error": "User not found"})
                return
            username = user["discogs_username"]
            log.info("Judgment run started for %s", username)
            api_key = user["anthropic_api_key"]
            if not api_key:
                await broadcast({"status": "stock_judgment_error", "error": "Anthropic API key not configured"})
                return
            # recommendation_item_limit is NOT NULL DEFAULT 300, and 0 is a
            # deliberate "unlimited" sentinel consumed by get_unjudged_stock_items's
            # `limit > 0` check -- `or recommendations.SYNC_CAP` here would silently
            # turn a real 0 into 300 (0 is falsy), breaking that contract.
            limit = user["recommendation_item_limit"]

            with user_scope(user_id) as conn:
                total_unjudged = count_unjudged_stock_items(conn, user_id)
                unjudged = get_unjudged_stock_items(conn, user_id, limit)
                taste_listing = get_taste_listing(conn, user_id)

            if not unjudged:
                await broadcast({"status": "stock_judgment_complete", "judged": 0})
                log.info("Found 0/0 items to judge for %s, nothing to do", username)
                return
            log.info("Found %d/%d items to judge for %s", len(unjudged), total_unjudged, username)

            client = anthropic.Anthropic(api_key=api_key)
            judged = 0
            for i in range(0, len(unjudged), recommendations.BATCH_SIZE):
                batch = unjudged[i:i + recommendations.BATCH_SIZE]
                results = await asyncio.to_thread(recommendations.judge_batch, client, taste_listing, batch)
                recommended_in_batch = 0
                if results:
                    with user_scope(user_id) as conn:
                        upsert_stock_judgments(conn, user_id, results)
                        conn.commit()
                    judged += len(results)
                    recommended_in_batch = sum(1 for r in results if r["recommended"])
                log.info("Judged batch %d/%d for %s: %d recommended", judged, len(unjudged), username, recommended_in_batch)
                await broadcast({"status": "stock_judgment_progress", "judged": judged, "total": len(unjudged)})

            await broadcast({"status": "stock_judgment_complete", "judged": judged})
            log.info("Stock judgment complete for %s: %d items judged", username, judged)
        except asyncio.CancelledError:
            log.info("Judgment run cancelled")
            raise
        except Exception as e:
            log.error("Judgment phase failed for %s: %s", username, e, exc_info=True)
            await broadcast({"status": "stock_judgment_error", "error": str(e)})
```

- [ ] **Step 5: Tag `_run_plex_match`'s broadcasts**

In `backend/crawl_manager.py`, replace the whole `_run_plex_match` method (currently lines 1127-1180) with:

```python
    async def _run_plex_match(self, user_id: int, base_url: str, token: str, threshold: int):
        import plex
        import plex_security
        from db import (
            user_scope, get_library_items_for_plex_match, set_plex_match, clear_plex_match,
        )

        username = self._username_for_log(user_id)

        async def broadcast(event: dict):
            await self._broadcast({**event, "user_id": user_id})

        await broadcast({"status": "plex_match_started"})
        log.info("Plex match started for %s", username)
        try:
            section_key = await asyncio.to_thread(plex.get_music_section_key, base_url, token)
            if section_key is None:
                log.warning("Plex match skipped for %s: no music library section found on %s", username, base_url)
                await broadcast({"status": "plex_match_error", "error": "No music library found on Plex server"})
                return

            albums = await asyncio.to_thread(plex.fetch_albums, base_url, token, section_key)
            machine_id = await asyncio.to_thread(plex.get_machine_identifier, base_url, token)

            with user_scope(user_id) as conn:
                items = get_library_items_for_plex_match(conn, user_id)
                matched = 0
                for i, item in enumerate(items, start=1):
                    # Fuzzy-matching one release against the full album list is CPU-bound
                    # and, at real collection/library sizes, expensive enough per item to
                    # stall the shared event loop for other users' requests if run inline
                    # -- to_thread here yields control back between every item, same
                    # rationale as the three plex.py calls above.
                    best = await asyncio.to_thread(plex.find_best_match, item["artist"], item["title"], albums, threshold)
                    if best:
                        url = plex.build_album_url(base_url, machine_id, best["rating_key"])
                        set_plex_match(conn, user_id, item["discogs_id"], url)
                        matched += 1
                    else:
                        clear_plex_match(conn, user_id, item["discogs_id"])
                    if i % 25 == 0 or i == len(items):
                        conn.commit()
                        # user_scope()'s set_config(..., true) is transaction-local and
                        # was just reverted by the commit above -- re-issue it so the
                        # remaining items in this same connection are still RLS-scoped
                        # to this user (same hazard _sync_collection's page loop hits).
                        conn.execute("SELECT set_config('app.user_id', %s, true)", [str(user_id)])
                        await broadcast({"status": "plex_match_progress", "matched": matched, "total": len(items)})

            await broadcast({"status": "plex_match_complete", "matched": matched})
            log.info("Plex match complete for %s: %d/%d matched", username, matched, len(items))
        except Exception as e:
            if isinstance(e, plex_security.PlexUnsafeAddressError):
                log.warning("Plex match rejected for %s: %s", username, e)
                await broadcast({"status": "plex_match_error", "error": "Plex address not reachable"})
            else:
                log.warning("Plex match phase failed for %s, skipping: %s", username, e)
                await broadcast({"status": "plex_match_error", "error": "an unexpected error occurred"})
```

- [ ] **Step 6: Run the full crawl_manager test file to verify it passes**

Run: `cd backend && pytest tests/test_crawl_manager.py -v`

Expected: PASS (all tests, including the 4 from Step 1). If any other test in this file breaks, it means another exact-dict assertion on a per-user event exists that wasn't caught during design review — read the failure, and if it's a legitimate exact-dict comparison on a `sync_*`/`stock_judgment_*`/`plex_match_*` event, add `"user_id": <the test's user id>` to its expected dict the same way Step 1 did.

- [ ] **Step 7: Commit**

```bash
git add backend/crawl_manager.py backend/tests/test_crawl_manager.py
# `-F`, never `-m`: this repo requires AI-attribution trailers on every
# commit and they are easy to drop through shell quoting (root CLAUDE.md,
# "Commits — AI attribution trailers").
cat > /tmp/msg.txt <<'EOF'
Tag sync/judgment/plex-match SSE broadcasts with the owning user_id

Note: This commit message was created by AI
ai-generated: true
ai-model: <this session's model id>
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
EOF
git commit -F /tmp/msg.txt
```

---

### Task 2: Filter SSE replay and live stream by `user_id`

**Files:**
- Modify: `backend/routers/crawl.py` (`_events_to_replay` at line 61, `crawl_stream` at line 87)
- Modify: `CLAUDE.md` (the `GET /api/crawl/stream` sentence in "Key invariants")
- Test: `backend/tests/test_crawl_router.py`

**Interfaces:**
- Consumes: Task 1's contract — an event dict may carry `"user_id": <int>`; absence means global.
- Produces: `crawl_router._visible_to(event: dict, user_id: int) -> bool`, used by both `_events_to_replay` and `crawl_stream`.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_crawl_router.py`, add after `test_crawl_stream_replay_includes_listing_changed_events_for_every_release` (which currently ends at line 222, right before the `_pending_future` helper):

```python
def test_visible_to_owned_event_is_visible_only_to_its_owner():
    event = {"status": "sync_started", "user_id": 42}
    assert crawl_router._visible_to(event, 42) is True
    assert crawl_router._visible_to(event, 99) is False


def test_visible_to_untagged_event_is_visible_to_everyone():
    event = {"status": "stock_sync_progress", "synced": 3}
    assert crawl_router._visible_to(event, 42) is True
    assert crawl_router._visible_to(event, 99) is True


def test_crawl_stream_replay_only_includes_per_user_events_relevant_to_calling_user(pg_test_db, authed_client_factory):
    # sync_*/stock_judgment_*/plex_match_* events are tagged with the
    # broadcasting user's id (crawl_manager.py's per-function `broadcast`
    # closures) and must not leak another user's job status -- unlike
    # listing_changed (tested above), which is deliberately global.
    alice, bob, _crawler_id = _setup_two_users_each_with_a_different_release()

    crawl_manager._recent = [
        {"id": 1, "status": "sync_started", "user_id": alice["id"]},
        {"id": 2, "status": "sync_started", "user_id": bob["id"]},
        {"id": 3, "status": "stock_sync_progress", "synced": 5},
    ]
    with db.user_scope(alice["id"]) as conn:
        db.enqueue_crawl_queue(conn, "r1")
        conn.commit()

    events = crawl_router._events_to_replay(_FakeRequest(alice["id"]))

    ids = [e["id"] for e in events]
    assert 1 in ids
    assert 2 not in ids
    assert 3 in ids
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_crawl_router.py -k "visible_to or per_user_events" -v`

Expected: FAIL — `crawl_router._visible_to` doesn't exist yet (`AttributeError`), and the replay test currently returns all three events (including id 2, Bob's) since nothing filters by `user_id`.

- [ ] **Step 3: Add `_visible_to` and use it in both the replay buffer and the live loop**

In `backend/routers/crawl.py`, add this function after `_events_to_replay` (before `@router.get("/crawl/stream")`):

```python
def _visible_to(event: dict, user_id: int) -> bool:
    """A per-user event (sync/judgment/plex-match, tagged with the broadcasting
    user's id) is visible only to that user. An untagged event (stock sync,
    listing_changed, ping) has no owner and is visible to everyone."""
    owner = event.get("user_id")
    return owner is None or owner == user_id
```

Change `_events_to_replay`'s final two lines from:

```python
    if not any_active:
        return []
    return crawl_manager.recent_events()
```

to:

```python
    if not any_active:
        return []
    return [e for e in crawl_manager.recent_events() if _visible_to(e, user_id)]
```

Also extend `_events_to_replay`'s docstring — add this paragraph after the existing `listing_changed` paragraph:

```python
    sync_*/stock_judgment_*/plex_match_* events, in contrast, are tagged with
    the broadcasting user's id (see crawl_manager.py's per-job `broadcast`
    closures) and ARE filtered here by that id -- one user's collection sync
    or judgment run must not appear as another user's job status.
    """
```

Update `crawl_stream` to bind `user_id` and filter the live loop:

```python
@router.get("/crawl/stream")
async def crawl_stream(request: Request):
    user_id = request.state.user_id

    async def generate():
        q = crawl_manager.subscribe()
        try:
            for event in _events_to_replay(request):
                yield {"data": json.dumps(event)}
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield {"data": json.dumps({"status": "ping"})}
                    continue
                if not _visible_to(event, user_id):
                    continue
                yield {"data": json.dumps(event)}
        finally:
            crawl_manager.unsubscribe(q)
    return EventSourceResponse(generate())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_crawl_router.py -v`

Expected: PASS, including the two new `_visible_to` tests and the new replay test. `test_crawl_stream_replay_includes_listing_changed_events_for_every_release` and `test_events_to_replay_gate_opens_for_a_running_job_even_with_no_pending_queue_rows` must still pass unchanged — their seeded events carry no `user_id` key, so `_visible_to` treats them as global.

- [ ] **Step 5: Correct the CLAUDE.md invariant**

Read the "Key invariants" bullet in `CLAUDE.md` that currently reads (in part): `` `GET /api/crawl/stream` is a persistent, per-user-filtered SSE connection — it never starts a crawl, only observes.`` This sentence was inaccurate before this plan (no live filtering existed) and is accurate now — no wording change is needed, but confirm the sentence still reads correctly end-to-end after Task 1 and 2 landed, and fix it if it doesn't (e.g. if `_visible_to`'s name changed during implementation).

- [ ] **Step 6: Run the full backend test suite**

Run: `cd backend && pytest`

Expected: PASS. This catches any other test in the suite that happened to assert on `crawl_manager.recent_events()` contents for a per-user job that Steps 1-6 of Task 1 didn't already surface.

- [ ] **Step 7: Commit**

```bash
git add backend/routers/crawl.py backend/tests/test_crawl_router.py CLAUDE.md
# `-F`, never `-m`: this repo requires AI-attribution trailers on every
# commit and they are easy to drop through shell quoting (root CLAUDE.md,
# "Commits — AI attribution trailers").
cat > /tmp/msg.txt <<'EOF'
Filter SSE replay and live stream by the broadcasting event's user_id

Note: This commit message was created by AI
ai-generated: true
ai-model: <this session's model id>
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
EOF
git commit -F /tmp/msg.txt
```

---

## Pre-PR spec-drift check

Before opening the PR, follow root `CLAUDE.md`'s "Pre-PR spec-drift check": `grep -rl` across both `docs/superpowers/specs/` and `docs/specifications/shaping/` for `_events_to_replay`, `crawl_stream`, `_broadcast`, `hasJudgedItems`, `stock_judgment`, and confirm no other spec's description of SSE event delivery has drifted out of date now that `sync_*`/`stock_judgment_*`/`plex_match_*` are filtered. In particular check
`docs/specifications/shaping/2026-08-22-live-recommended-filter-design.md` on the PR #158 branch once these two branches are both mergeable — that spec's "Non-goals: No backend changes" was true when it was written and stays true (this plan doesn't touch judgment cadence or batch size), but note in the PR description that this fix closes the gap its own Non-goals section left open.
