# Worker Pool Pacing and Circuit Breaker — Design Spec

_2026-08-01_

**Amendment (2026-08-01, whole-plan final review):** everything below shipped,
but five details differ from how they're written, and one design decision below
did not ship on the first pass and was fixed in this review.

1. **Pacing lives in its own method, not inline.** The Design section reads as
   though the lock/delay wraps `plugin.search()` inline "in `_drain_one_batch`";
   it shipped as a separate `CrawlManager._paced_search(crawler_id, plugin,
   release, pages)`, which `_drain_one_batch` calls once per claimed row. The
   circuit-breaker bookkeeping is likewise two small helpers,
   `_cooling_down_crawler_ids()` and `_record_site_result(crawler_id,
   succeeded)`, rather than open-coded in the drain loop.
2. **The backoff timestamp is recorded in a `finally`, not after a successful
   search.** Step 4 below reads as a post-success assignment; as written that
   would let a request whose *first attempt and bot-detection retry both* failed
   leave `_site_next_allowed_at` untouched, so the next request to that same
   site would fire with zero backoff — the worst possible moment to stop being
   polite. `_paced_search` therefore sets it in a `finally` covering every exit
   path, success or exception.
3. **`crawl_delay_seconds` / `consecutive_failure_limit` are read once per
   claimed row**, via `load_config()` inside `_paced_search` and
   `_record_site_result` — not "once per batch (or per worker-loop iteration)"
   as step 4 allows. Strictly more responsive to a config change than the spec
   required; no behavioral downside.
4. **A recovered bot detection now counts as a circuit-breaker failure — this
   is the one real fix from this review.** The first implementation had
   `_paced_search` swallow a `BotDetectedError` whose post-`_reset_context`
   retry succeeded, and `_drain_one_batch` then recorded
   `succeeded=bool(matches)`, so a site that walls *every* request but yields to
   *every* retry reset the counter to `0` each time and could never trip the
   breaker — directly contradicting the "Bot-detection interaction" section
   below, which is explicit that recovered instances must still count.
   `_paced_search` now returns `(matches, bot_detected)` and `_drain_one_batch`
   records `succeeded=bool(matches) and not bot_detected`. Covered by
   `test_drain_one_batch_counts_recovered_bot_detection_as_a_failure`.
5. **Cooldown exclusion is claim-time only.** A site whose breaker trips partway
   through a batch still has that batch's already-claimed rows crawled to
   completion; the cooldown only keeps its rows from being claimed on subsequent
   drain cycles. Bounded by `batch_size` (5) and not worth the complexity of
   re-checking mid-loop, but it is not the "stops immediately" reading the
   Design section invites.
6. **No cooldown-expiry test shipped.** The Testing section's "Cooldown expires"
   item is unimplemented: expiry is structural rather than behavioral —
   `_drain_one_batch` recomputes `_cooling_down_crawler_ids()` from
   `_site_cooldown_until` on every single call and nothing caches it, so a site
   becomes claimable again on the very next drain cycle after its timestamp
   passes, with no restart or eviction step to get wrong. The circuit-breaker
   test also asserts against `_site_cooldown_until`/`_site_consecutive_failures`
   directly rather than inspecting the arguments passed to
   `claim_crawl_queue_batch`; `db`-level exclusion is covered separately in
   `test_crawl_queue.py`.

---

## Overview

The `crawl-queue-refactor` branch's whole-branch final review found that deleting
`crawler.py`'s `crawl_releases()` (Task 11 of that plan) silently dropped four
behaviors that were never re-implemented in the new shared worker pool: the
inter-request delay (`crawl_delay_seconds`, previously ~30s between requests),
the consecutive-failure circuit breaker (`consecutive_failure_limit`), crawl-order
shuffling (`shuffle_crawl_order`), and debug screenshots
(`debug_screenshot_interval`). The settings themselves are still saved and
rendered in the admin Settings UI, describing behavior that no longer exists.

The two safety-relevant ones — pacing and the circuit breaker — matter because
they're the only thing standing between this app and a bot-detection block or IP
ban on the third-party sites it crawls. With the current worker pool, a
collection sync enqueues every release × every enabled crawler unconditionally,
and N workers drain that queue with zero delay between requests to the same
site. This spec restores both, adapted to the worker-pool architecture (where
"one crawl run" no longer exists as a unit to pace or abort), and removes the
two settings that don't map onto this architecture rather than leaving them as
non-functional UI.

---

## Goals / non-goals

**Goals**
- Requests to the same external site are paced with a minimum, randomized-jitter
  gap, regardless of which worker in the pool sends them — restoring
  `crawl_delay_seconds`'s original intent without serializing the whole pool
  across unrelated sites.
- A site that fails too many times in a row gets a temporary, self-healing
  cooldown — restoring `consecutive_failure_limit`'s intent without an
  "abort the crawl" concept that no longer exists.
- Settings that don't do anything are removed, not left as UI that lies about
  what the app does.

**Non-goals**
- Reviving `shuffle_crawl_order` or debug screenshots — per the scoping decision
  for this fix, both are removed as dead settings rather than rebuilt. Shuffling
  doesn't map cleanly onto a claimed-queue model (enqueue order across many
  users' syncs already provides more entropy than a single shuffled batch did);
  screenshots were a manual debugging aid tied to a per-batch session concept
  Task 11 deliberately dropped, and reviving it cleanly would need its own
  design (a session-per-worker or per-claim-batch concept).
- Making the cooldown duration admin-configurable — fixed at 30 minutes per the
  scoping decision, to keep this fix tight.
- Any change to which sites get crawled, how often collection syncs run, or the
  admin/per-user authorization model — this is purely restoring lost pacing
  safety, not changing scheduling policy.

---

## Design

### Per-site pacing

`CrawlManager` gains two new in-memory attributes, alongside its existing
`_worker_tasks`/`_sync_tasks`-style state:

```python
self._site_locks: dict[int, asyncio.Lock] = {}
self._site_next_allowed_at: dict[int, float] = {}
```

Both are keyed by `crawler_id`. In `_drain_one_batch`, immediately before
calling `plugin.search(release, page)` for a claimed row:

1. Get-or-create the lock for that row's `crawler_id` and acquire it.
2. If `time.monotonic()` is before `_site_next_allowed_at.get(crawler_id, 0)`,
   `await asyncio.sleep()` for the remaining time.
3. Call `plugin.search(...)` as today (still inside the lock).
4. Set `_site_next_allowed_at[crawler_id] = time.monotonic() + random.uniform(0.5, 1.0) * crawl_delay_seconds`,
   where `crawl_delay_seconds` is read from `load_config()` once per batch (or
   per worker-loop iteration — a config change mid-run taking a few seconds to
   propagate is fine, matching how other settings are already read via
   `load_config()` calls scattered through `crawl_manager.py`).
5. Release the lock.

This reuses `crawl_delay_seconds` unchanged as an admin setting, but its meaning
shifts from "delay between every request the single crawl loop makes" to
"minimum randomized gap between requests to this one site" — the number means
the same thing to an admin (how polite to be to one target site), just applied
correctly under a pool instead of a single loop.

The lock is held across the `plugin.search()` call itself, not just the delay —
this guarantees only one in-flight request per site at a time pool-wide, which
is the actual property needed (two workers must never send concurrent requests
to the same site, not just requests spaced close in time). Different sites are
entirely unaffected by each other's locks, so pool-wide parallelism across
sites is preserved.

This must include `_drain_one_batch`'s existing bot-detection retry (on
`BotDetectedError`, `_reset_context` runs and `plugin.search()` is called a
second time before giving up) — the lock is acquired once before the first
attempt and released once after the retry resolves (success or final
failure), not released and reacquired between the two attempts. Releasing it
in between would let a second worker's request to the same site land in the
middle of this site's own bot-detection recovery, which is exactly the
scenario per-site serialization exists to prevent.

`time.monotonic()` (not wall-clock) avoids any issue with system clock
adjustments during a long-running process.

### Per-site circuit breaker

Two more in-memory dicts, same lifecycle as the pacing state:

```python
self._site_consecutive_failures: dict[int, int] = {}
self._site_cooldown_until: dict[int, float] = {}
```

After each `plugin.search()` call in `_drain_one_batch`:
- On a result with matches (`found`): `self._site_consecutive_failures[crawler_id] = 0`.
- On `not_found` or an exception (`error`): increment
  `self._site_consecutive_failures[crawler_id]`. If it reaches
  `consecutive_failure_limit` (existing admin setting, read via `load_config()`),
  set `self._site_cooldown_until[crawler_id] = time.monotonic() + 1800` (30
  minutes, fixed) and reset the counter to 0 (so it doesn't immediately
  re-trigger the instant the cooldown ends and the next single failure comes
  in).

Before claiming a batch, `_drain_one_batch` computes the current set of
`crawler_id`s still in cooldown (`time.monotonic() < cooldown_until`) and passes
them to `db.claim_crawl_queue_batch` as a new `excluded_crawler_ids` parameter,
so a cooling-down site's rows are never claimed by any worker in the first
place — they stay `pending` and get claimed normally once the cooldown expires.
This is cleaner than claiming and un-claiming: no wasted claim-batch slots, and
other pending rows for non-cooldown sites still fill that worker's batch.

`db.claim_crawl_queue_batch`'s query gains `AND crawler_id != ALL(%(excluded)s)`
(or equivalent) in its `WHERE` clause when the list is non-empty; unchanged when
empty (the common case — most sites aren't in cooldown most of the time).

### Bot-detection interaction

`BotDetectedError` (raised by a plugin, triggering `_reset_context`'s browser
context reset — unchanged from Task 11) is treated as a failure for circuit-
breaker purposes exactly like any other exception: it increments
`_site_consecutive_failures` and can trigger a cooldown. This is intentional —
repeated bot detection on one site, even if each individual instance is
recovered via context reset, is exactly the signal the circuit breaker should
act on to back off that site entirely for a while, not just reset one browser
context and immediately try again.

### Settings removal

`debug_screenshot_interval` and `shuffle_crawl_order` are removed entirely:
- `backend/routers/settings.py`: dropped from `SettingsUpdate`, `get_settings`'s
  response, and `update_settings`'s write-through to `config.json`.
- `frontend/src/api/types.ts`: dropped from the `Settings` interface.
- `frontend/src/views/Settings.tsx`: their form fields/rows removed.

`crawl_delay_seconds` and `consecutive_failure_limit` are unchanged in the
settings surface — they already exist as admin fields; only their backing
behavior is being restored.

No database migration is needed — settings live in `config.json`, not a
schema; removing fields from the Pydantic model and the frontend type is
sufficient. Any stale keys left over in an existing `config.json` on disk are
inert and harmless.

---

## Testing

- **Per-site serialization**: two workers, both claiming rows for the *same*
  `crawler_id` in overlapping batches — assert their `plugin.search()` calls
  never overlap in time (e.g. via a fake plugin that records call start/end
  timestamps), proving the lock actually serializes same-site requests across
  workers.
- **Cross-site parallelism preserved**: two workers, each claiming a row for a
  *different* `crawler_id` — assert their `plugin.search()` calls *do* overlap,
  proving the fix didn't regress into a pool-wide global lock.
- **Jitter bounds**: after a request, assert the computed
  `_site_next_allowed_at` falls within `[now + 0.5*delay, now + 1.0*delay]`.
- **Circuit breaker trips and cools down**: a fake plugin failing
  `consecutive_failure_limit` times in a row for one `crawler_id` — assert that
  site's `crawler_id` appears in the excluded set passed to
  `claim_crawl_queue_batch` on the next drain cycle, and that a *different*
  `crawler_id`'s rows are still claimed normally in the same cycle.
- **Cooldown expires**: advance the fake clock (or use a very short test-only
  cooldown constant) past 30 minutes and confirm the site is claimable again
  with a reset failure counter.
- **Settings removal**: `GET /api/settings` no longer includes
  `debug_screenshot_interval`/`shuffle_crawl_order` in its response; posting a
  body that still includes them (a stale frontend client) doesn't error
  (Pydantic ignores unknown fields by default) but doesn't persist them either.

---

## Out of scope

- Reviving crawl-order shuffling or debug screenshots (see Non-goals).
- Making the 30-minute cooldown configurable.
- Any change to `crawl_queue` enqueue logic, RLS, admin authorization, or
  anything else already shipped on the `crawl-queue-refactor` branch — this is
  additive to the worker pool's internals only.
