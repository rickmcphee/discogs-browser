# Per-User SSE Event Filtering — Design

**Status:** implemented
**Date:** 2026-08-23
**Verified against:** `main` @ `6fc4684`

## Problem

`CrawlManager._broadcast` (`backend/crawl_manager.py`) fans every SSE event out to every
connected subscriber with no per-user filtering. `GET /api/crawl/stream`
(`backend/routers/crawl.py`) only gates its *replay* buffer on whether the calling user has
anything active — it does not filter the buffer's contents by ownership — and its live loop
forwards every event unconditionally. `CLAUDE.md` currently describes the stream as
"per-user-filtered," which is not true today.

Concretely: `_run_judgment_phase(user_id)`, `_sync_collection_blocking(user_id, ...)`, and
`_run_plex_match(user_id, ...)` each run per-user (tracked in `_judgment_tasks`/`_sync_tasks`/
`_plex_match_tasks`, keyed by `user_id`, so two different users' jobs run concurrently) but
broadcast bare `{"status": ...}` dicts with no `user_id` on them. One user's judgment refresh,
collection sync, or Plex match sets every *other* connected user's frontend state as if it were
their own run — `hasJudgedItems`, `syncing`, `judgmentRunning` all flip for users who did nothing.
Flagged by Copilot review on PR #158 (deliberately deferred out of that PR — see
[the review thread](https://github.com/rickmcphee/discogs-browser/pull/158#discussion_r3838527945))
for the `hasJudgedItems`/"Recommended" filter consequence specifically; the collection-sync and
Plex-match banners leaking the same way are the same root cause, found while scoping this fix.

**This isn't a new problem — it's a regression.** Per-user SSE filtering existed once
(`357d4a0`, 2026-07-31): `_events_to_replay` filtered by library ownership and the live loop
skipped `listing_changed` events for releases outside the caller's own library. `5e1890e`
(2026-08-12) correctly removed *that* filtering — `listing_changed` is deliberately global, since
Store/Track are shared tabs where any user's crawl match should repaint every connected user's
view — but nothing replaced it for the per-user job-status events, because at the time `357d4a0`
was written there was only ever one global sync/judgment/Plex-match task, not one per user. The
multi-tenant conversion to per-user task dicts landed later without restoring event-level
filtering for them. `test_crawl_router.py` still carries a docstring reference to
`test_crawl_stream_replay_only_includes_events_relevant_to_calling_user`, a test `5e1890e` deleted
along with the old (over-broad) filtering — the name is stale, but it's pointing at the same gap
this design closes, just correctly scoped this time.

## Goals

- `sync_*` (collection sync), `stock_judgment_*`, and `plex_match_*` SSE events reach only the
  user whose job produced them — both on initial replay and on the live stream.
- `stock_sync_*` (shared catalog refresh), `listing_changed` (global Store/Track repaint), and
  `ping` are unaffected — they have no per-user owner and must keep reaching everyone.
- Correct `CLAUDE.md`'s invariant claim so it's true again.

## Non-goals

- No change to `stock_sync_*` scope or semantics — confirmed global by inspection: neither
  `_sync_stock` nor `_run_catalog_crawler` takes a `user_id`.
- No frontend changes. The client already keys its UI state off event `status`/`type`, never off
  who else might be running a job; filtering at the source removes the leak without the frontend
  needing to know a `user_id` field exists.
- ~~No live-stream integration test.~~ **Reversed during PR review (2026-08-23).** The original
  reasoning was that existing coverage exercises `_events_to_replay` and its helpers directly,
  never the `EventSourceResponse` generator, so this design would follow that precedent. Review
  pointed out what that precedent costs here: deleting `crawl_stream`'s own
  `if not _visible_to(...): continue` left the entire suite green while restoring the cross-user
  leak — on the live path, which is the one the original bug report was about. `test_crawl_stream_live_loop_drops_another_users_tagged_event`
  now drives the real generator and is the only test that fails when that line goes. See Testing.
- No wire-format cleanup. The `user_id` tag rides along in the JSON reaching its owning client;
  nothing reads it there, and stripping it before yield would be extra code for no behavioral
  benefit.

## Design

### Tagging (`backend/crawl_manager.py`)

Each per-user job runner gets a local `broadcast` closure that stamps `user_id` into every event
once, at the one place events leave that function, rather than at each individual call site. A
call site that forgets the tag is impossible by construction — there's only one place per function
where tagging happens, not N places where it could be missed. This mirrors the file's own existing
justification for shielding whole methods rather than individual awaits (`_shielded`'s docstring,
`_drain_one_batch`'s comment about "four rounds" of finding the next unshielded gap one at a time).

- `_sync_collection_blocking(user_id, mode, scope, loop)`: its existing closure
  ```python
  broadcast = lambda event: self._broadcast_threadsafe(event, loop)
  ```
  becomes
  ```python
  broadcast = lambda event: self._broadcast_threadsafe({**event, "user_id": user_id}, loop)
  ```
  No other line in this function changes — every `broadcast({...})` call site already routes
  through it.

- `_run_judgment_phase(user_id)`: gains
  ```python
  async def broadcast(event: dict):
      await self._broadcast({**event, "user_id": user_id})
  ```
  and its seven `await self._broadcast({...})` calls become `await broadcast({...})` (started,
  the two early-return errors, the empty-run completion, progress, normal completion, and the
  exception handler).

- `_run_plex_match(user_id, base_url, token, threshold)`: same shape as `_run_judgment_phase`,
  applied to its six `self._broadcast(...)` calls.

`_broadcast` and `_broadcast_threadsafe` themselves are unchanged — they still just take a plain
`dict`. `stock_sync_*` broadcasts (`_sync_stock`, `_run_catalog_crawler`) and
`_broadcast_listing_changed`/`_broadcast_stock_listing_changed` are untouched.

### Filtering (`backend/routers/crawl.py`)

One predicate, shared by both paths that currently forward events unfiltered:

```python
def _visible_to(event: dict, user_id: int) -> bool:
    """A per-user event (sync/judgment/plex-match, tagged with the broadcasting
    user's id) is visible only to that user. An untagged event (stock sync,
    listing_changed, ping) has no owner and is visible to everyone."""
    owner = event.get("user_id")
    return owner is None or owner == user_id
```

`_events_to_replay`:
```python
if not any_active:
    return []
return [e for e in crawl_manager.recent_events() if _visible_to(e, user_id)]
```

`crawl_stream`'s live loop:
```python
event = await asyncio.wait_for(q.get(), timeout=15.0)
...
if not _visible_to(event, user_id):
    continue
yield {"data": json.dumps(event)}
```

`crawl_stream` currently doesn't bind `user_id` at all (it reads `request.state.user_id` only
inside `_events_to_replay`); it gains `user_id = request.state.user_id` at the top of the route
function, same as it had before `5e1890e` removed the earlier filtering.

### Documentation

- `CLAUDE.md`'s "Key invariants" section: the sentence "`GET /api/crawl/stream` is a persistent,
  per-user-filtered SSE connection" becomes true again; no wording change needed once the fix
  lands, but this fix is what makes it true — noted here so the PR description can point at it.
- `_events_to_replay`'s docstring gains a note alongside its existing `listing_changed`
  explanation: `sync_*`/`stock_judgment_*`/`plex_match_*` events are filtered by `user_id`; only
  `listing_changed` (and untagged events generally) stay global.

## Testing

- **New unit tests for `_visible_to`** (`test_crawl_router.py`): an event tagged with Alice's
  `user_id` is visible to Alice, not to Bob; an untagged event is visible to both.
- **New replay test**, in the shape of the deleted `test_crawl_stream_replay_only_includes_events_relevant_to_calling_user`
  but scoped correctly this time — `sync_started`/`stock_judgment_progress`-shaped dicts, not
  `listing_changed`: seed `_recent` with events tagged for Alice, for Bob, and untagged; assert
  Alice's replay includes her own and the untagged ones, excludes Bob's.
- **New live-stream test** (added during PR review, reversing the original non-goal above):
  `test_crawl_stream_live_loop_drops_another_users_tagged_event` awaits `crawl_stream` for Alice
  and drives the returned `EventSourceResponse.body_iterator` directly — Bob's tagged event is
  dropped, Alice's own and an untagged global one are delivered, in order. The generator is lazy,
  so the test must wait for its subscription to appear before broadcasting; broadcasting first
  reaches no queue and the reads collect 15s keepalive pings instead. Verified in both directions:
  it passes as written and is the *only* test that fails when `crawl_stream`'s
  `if not _visible_to(event, user_id): continue` is deleted.
- **Fix existing exact-dict assertion**: `test_run_judgment_phase_broadcasts_complete_when_nothing_unjudged`
  (`test_crawl_manager.py:3942`) asserts
  `events == [{"status": "stock_judgment_complete", "judged": 0, "id": 2}]`; update the expected
  dict to include `"user_id": alice["id"]`. Checked all four exact-dict-equality assertions in
  `test_crawl_manager.py` (lines 3457, 3487, 3523, 3942) — the other three are `stock_sync_*`
  (global, unaffected); this is the only one that breaks.
- Existing `status`-only projections (e.g. `[e["status"] for e in ...]`) throughout
  `test_crawl_manager.py` are unaffected by the added key.

## Risks

- **Missed call site**: a future per-user broadcast added directly via `self._broadcast(...)`
  inside one of the three tagged functions, bypassing the local `broadcast` closure, would leak
  globally with no test failure unless a new test specifically covers it. Mitigated by the
  closure-at-chokepoint structure itself (nothing to opt into, the closure is what's already
  there to call) but not eliminated — a reviewer would still need to notice the bypass.
- **New per-user event types added elsewhere**: any future job runner that gains a `user_id`
  parameter must remember to tag its broadcasts the same way; nothing enforces this project-wide,
  only within the three functions this design touches.
