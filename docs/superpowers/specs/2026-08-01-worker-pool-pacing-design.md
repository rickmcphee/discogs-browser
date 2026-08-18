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

**Amendment (2026-08-08, crawl-target-expansion whole-branch review):** two
more details in this document are now stale, both from the branch that let
release crawlers search on behalf of store-crawler stock items as well as
Discogs releases (see `docs/specifications/shaping/2026-08-08-crawl-target-expansion-design.md`).

7. **`_paced_search`'s third parameter is now named `target`, not `release`.**
   Item 1 above already quotes the signature as
   `_paced_search(crawler_id, plugin, release, pages)`; it's now `target` —
   a cosmetic rename (both a Discogs release and a stock item pass the same
   `{artist, title, format, ...}`-shaped dict), not a behavior change.
8. **"On `not_found` ... increment" no longer holds unconditionally.** The
   bullet list above `_drain_one_batch`'s per-search bookkeeping is release-
   target-only now: a stock-item row's `not_found` result, when no bot
   detection occurred, records nothing at all (neither a reset nor an
   increment) — most small-label stock inventory simply isn't listed on
   Amazon/eBay, so an empty result there carries no site-health signal the
   way it does for a real Discogs release. A stock-item row still increments
   the counter on bot detection, and still resets it to 0 on an actual match,
   exactly as a release row does. Covered by
   `test_drain_one_batch_excludes_empty_stock_item_result_from_circuit_breaker`,
   `test_drain_one_batch_counts_bot_detected_stock_item_search_as_a_failure`,
   and `test_drain_one_batch_resets_failure_count_on_a_found_stock_item_match`.

**Amendment (2026-08-09, branch `claude/ebay-409-cooloff`):** item 8's exclusion
had a consequence nobody spotted when it landed.

9. **A crawler that answers an API failure with `[]` is invisible to the
   breaker.** `ebay_api.search_ebay()` caught every `httpx.HTTPStatusError` /
   `httpx.RequestError`, logged it, and returned `[]` — the same value it
   returns for "no matching listing." Combined with item 8, that meant
   successive eBay API errors on a stock-item row recorded nothing at all: the
   counter never moved, `consecutive_failure_limit` was never reached, and the
   site never cooled off no matter how many errors came back in a row. (Reported
   against eBay 409s.) `search_ebay()` now re-raises after logging, so
   `_drain_one_batch`'s existing `except Exception` path records the failure —
   for release and stock-item rows alike, since that path runs before the
   target-kind branch. Nothing in `CrawlManager` changed. The general rule this
   makes explicit for any future crawler: **`[]` means "the site answered and
   has nothing"; anything else must raise**, because the breaker cannot
   distinguish the two otherwise. Covered by
   `test_successive_ebay_api_errors_cool_down_the_site` (real `crawlers/ebay.py`
   plugin against a mocked 409, asserting the cooldown trips) and
   `test_search_raises_on_http_error` / `test_search_raises_on_request_error`.
10. **The breaker's unit is now a failure domain, not always a crawler.** One
    `crawlers` row was assumed to mean one upstream; the two eBay plugins
    (`eBay/CCmusic` and `eBay`) break that — separate rows, but one eBay app,
    one cached OAuth token and one API, so an error storm answering one
    answers both. With a counter each, a storm had to reach
    `consecutive_failure_limit` twice over before both stopped calling. A
    plugin may now declare `failure_domain: str` (both eBay plugins declare
    `"ebay-browse-api"`); `CrawlManager._set_failure_domains`, called from
    `start_worker_pool`, collects those declarations, and
    `_record_site_result` applies each result to every crawler in the domain.
    Undeclared — every other crawler — means the crawler is its own domain,
    unchanged. The counters and `_site_cooldown_until` stay keyed by
    `crawler_id` rather than by domain, because that is what
    `claim_crawl_queue_batch`'s `excluded_crawler_ids` consumes. **Pacing was
    deliberately left per-crawler**: `_site_locks` / `_site_next_allowed_at`
    still give each eBay crawler its own `crawl_delay_seconds` slot, so the
    two together can issue requests twice as fast as one site's configured
    rate. That is a politeness question with a throughput cost on a
    5000-calls/day API quota, separate from the health question this item
    fixes, and no observed problem to act on. Covered by
    `test_failures_pool_across_crawlers_sharing_a_failure_domain` and
    `test_a_crawler_with_no_failure_domain_keeps_its_own_counter`.

**Amendment (2026-08-09, branch `worktree-amazon-error-swallowing`):** two
follow-ons from items 9 and 10.

11. **`crawlers/amazon.py` had the same blind spot, in a worse shape.** Its
    product-page step was wrapped in a bare `except Exception` that logged a
    warning and fell through to `return [{"url": ..., "price": None, ...}]`.
    That value is *truthy*, so `_record_site_result` recorded the failed crawl
    as a success and **reset** the site's consecutive-failure count — where
    eBay's `[]` at least counted as a failure on the release path, this
    actively cleared the counter on both. `BotDetectedError` subclasses
    `Exception`, so the same handler also swallowed product-page bot walls,
    which therefore never reached `_paced_search`'s context-reset retry. The
    handler now logs and re-raises. A product page that loads but shows no
    price still returns the listing with `price: None` — `extract_price`
    returns `None` without raising, and that is a real answer, not a failure.
    Covered by `tests/crawlers/test_amazon_search_errors.py`.
12. **The cooldown notice is logged at INFO, not WARNING.** `routers/logs.py`'s
    ~~`_line_visible` filters by exact level membership rather than
    level-and-above~~ (see 2026-08-17 amendment below), so at WARNING the one
    line explaining a 30-minute crawl pause was invisible to anyone watching
    the INFO stream that carries the rest of the crawl narrative. Covered by
    `test_tripping_the_cooldown_is_logged_at_info`.

    **Amendment (2026-08-17, branch `flyio-log-files-machines`):**
    `_line_visible` no longer exists — `routers/logs.py` reads a Postgres
    `app_logs` table with a real `level` column per row, and level filtering
    is now a SQL `WHERE level = ANY(...)` clause, not a regex parsed off a
    tailed text line. The "INFO not WARNING" reasoning above is unaffected:
    the log viewer still filters by exact level set, not level-and-above. See
    [`2026-08-17-unified-log-store-design.md`](../../specifications/shaping/2026-08-17-unified-log-store-design.md).
13. **The breaker now covers catalog crawlers too, not just the worker pool.**
    `_sync_stock` had no consecutive-failure breaker at all — only the
    2-consecutive-429-sites run abort — so a site that hard-blocks us was
    re-attempted in full (initial attempt plus `_run_catalog_crawler`'s
    context-reset retry) on every scheduled sync, forever. Found via Amoeba
    Music answering every request with a Cloudflare 403. It now calls the same
    `_record_site_result` and skips a source whose `crawler_id` is in
    `_cooling_down_crawler_ids()`, reusing one set of state and one setting
    across both paths. A 429 is deliberately still excluded — it keeps its own
    handling (never retried, plus the run-level abort) as an expected,
    handled condition rather than evidence the site is broken. Covered by
    `test_sync_stock_cools_down_a_repeatedly_failing_catalog_crawler`,
    `test_sync_stock_skips_a_cooling_down_catalog_crawler`, and
    `test_sync_stock_does_not_count_a_429_toward_the_cooloff`.
14. **The stock-sync completion line names failed and cooling-down sources.**
    "Stock sync complete: 0 items" on its own read as a clean run; the ERROR
    explaining the zero was a different level, and per item 12's filtering
    quirk an INFO-only view never saw it. It now appends, when non-empty,
    `-- N failed (names); M cooling down (names)`. Covered by
    `test_sync_stock_completion_log_names_failed_and_skipped_sources`.

**Amendment (2026-08-14, branch `per-item-crawler-fanout`):** two more details
in this document are now stale — `crawl_queue` rows are per-target, not
per-`(target, crawler)` pair, so cooldown exclusion can no longer be expressed
as a claim-time exclusion list.

15. **`claim_crawl_queue_batch` has no `excluded_crawler_ids` parameter any
    more.** Item 5's "passes them ... as a new `excluded_crawler_ids`
    parameter" and item 10's "keyed by `crawler_id` rather than by domain,
    because that is what `claim_crawl_queue_batch`'s `excluded_crawler_ids`
    consumes" both describe a mechanism that no longer exists: a `crawl_queue`
    row no longer carries a `crawler_id` to exclude by. Cooldown is now an
    in-loop skip inside `_drain_one_batch`'s per-crawler dispatch, deferred via
    `pending_crawler_ids`/`available_at` written back onto the row, rather than
    kept off the claim in the first place. The counters/`_site_cooldown_until`
    staying keyed by `crawler_id` (item 10) is unchanged.
16. **The 2026-08-10 amendment's `AND crawler_id IN (SELECT id FROM crawlers
    WHERE enabled)` clause is gone from the claim query.** There is no
    `crawler_id` column on `crawl_queue` to filter by any more; the
    enabled-crawler set is resolved per claimed row at dispatch time
    (`db.get_eligible_crawlers`), not as a claim-time predicate.

See [`2026-08-14-per-item-crawler-fanout-design.md`](../../specifications/shaping/2026-08-14-per-item-crawler-fanout-design.md).

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

**Amendment (2026-08-10):** "unchanged when empty" no longer means the bare
`status = 'pending'`. That `WHERE` clause now also carries an unconditional
`AND crawler_id IN (SELECT id FROM crawlers WHERE enabled)`, deliberately the
same shape as this exclusion for the same purpose — stop sending work to a
site without disturbing anything else about the queue — but never omitted,
since there is no "no crawlers disabled" case worth branching on. Only the
cooldown half is conditional. Relatedly, `_set_failure_domains` (item 10 above)
now sees every release crawler rather than only the enabled ones, because
`start_worker_pool` no longer filters the plugin registry by `enabled`; a
disabled crawler's domain entry is inert while its rows go unclaimed and
correct the moment it is re-enabled. See
[`2026-08-09-stop-crawling-disabled-stores-design.md`](../../specifications/shaping/2026-08-09-stop-crawling-disabled-stores-design.md).

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
