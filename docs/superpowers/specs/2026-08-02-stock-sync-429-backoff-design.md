# Stock-Sync 429 Backoff — Design Spec

_2026-08-02_

## Overview

`_sync_stock` (`backend/crawl_manager.py`) iterates enabled catalog crawlers one
at a time, each pulling pages from `shopify_catalog.iter_products()`. On
2026-08-02, a single run hit HTTP 429 on the *first* request of four unrelated
Shopify merchants in a row (Run For Cover, Equal Vision, Saddle Creek,
Temporary Residence Ltd) — after the existing 22.5–45s pre-request delay added
in `dd969c1`/`98cbcfe`. robots.txt, this app's Playwright-only
`BotDetectedError` mechanism, and any single site's own defenses were all
ruled out (see `stock-sync-429-followup` investigation notes). The
cross-merchant, first-request-fails pattern points to Shopify's shared
platform edge throttling the source IP itself, not any one store.

Two gaps make this worse than it needs to be: `iter_products()` never reads a
`Retry-After` header, so its retry loop keeps guessing at a fixed jittered
delay regardless of what the server asked for; and `_sync_stock` has no
concept of "several independent sites just failed the same way" — it logs
each failure and moves on to the next crawler, which is more requests thrown
at a source IP that's very likely already in a platform-wide cooldown.

This spec is scoped tightly to those two gaps. It does not touch the separate
`worker-pool-pacing` fix (per-site lock + circuit breaker for the release-crawl
worker pool, on its own branch) — different code path, different architecture
(a shared claimed queue across workers, vs. `_sync_stock`'s simple one-crawler-
at-a-time loop).

## Goals / non-goals

**Goals**
- A crawler's own retry loop respects a `Retry-After` header when the server
  sends one, instead of always guessing with the existing fixed jitter.
- `_sync_stock` recognizes the specific signal of multiple independent
  crawlers failing with 429 back-to-back and stops the run rather than working
  through every remaining enabled crawler into the same throttle.
- The frontend/log clearly show a deliberate backoff, not a crash.

**Non-goals**
- Any state that persists across separate `_sync_stock` runs (e.g. remembering
  today's throttle into tomorrow's). Each run is independent, matching current
  behavior.
- Making the abort threshold (2) or the `Retry-After` cap (10 minutes) admin-
  configurable — fixed constants, same scoping call the worker-pool-pacing
  spec made for its 30-minute cooldown.
- Anything about the release-crawl worker pool, `crawl_queue`, or per-site
  locks — out of scope, covered by the separate in-flight fix.

## Design

### Retry-After handling — `shopify_catalog.py`

In `iter_products()`'s except block (currently: increment
`consecutive_failures`, sleep the fixed jitter, retry, or raise once
`consecutive_failure_limit` is reached):

- If the caught error is `httpx.HTTPStatusError` and
  `e.response.status_code == 429`, read the `Retry-After` header off
  `e.response.headers`.
- If present and parseable as a non-negative number, sleep that many seconds
  (capped at 600s, to guard against a malformed or hostile value) instead of
  the existing `random.uniform(delay * 0.5, delay)` jitter, before the next
  attempt.
- If absent, not parseable, or the error isn't a 429, fall back to today's
  jittered delay unchanged.
- The `consecutive_failure_limit` give-up-and-raise behavior is untouched —
  this only changes how long a retry waits, not how many are attempted.

### Run-level circuit breaker — `crawl_manager._sync_stock`

A local counter (`consecutive_429_crawlers = 0`), scoped to one `_sync_stock`
call — no persistence across runs. For each crawler in the existing `for
crawler in crawlers:` loop:

- On success: reset the counter to 0 (existing behavior — collect items,
  persist, broadcast progress — is unchanged).
- On failure: inspect the raised exception. Neither `iter_products` nor any
  crawler's `crawl_catalog()` wraps it, so it's always the original type. If
  it's an `httpx.HTTPStatusError` with `status_code == 429`, increment the
  counter. Any other failure (timeout, DNS, non-429 HTTP status, etc.) resets
  the counter to 0 instead — it isn't evidence of the same IP-wide throttle,
  so it shouldn't count toward the streak.
- When the counter reaches **2**, stop iterating immediately (`break` out of
  the crawler loop) instead of continuing to the next one. Two is deliberately
  low: today's evidence was 4-for-4 identical failures, and every additional
  store tried after the second one is one more request landing during what's
  very likely an active platform-wide cooldown.
- Broadcast a new `stock_sync_aborted` status (distinct from
  `stock_sync_error`), naming the two crawlers whose 429s triggered the abort,
  so the frontend and log make clear this was a deliberate backoff rather than
  a crash. Log at `WARNING`, not `ERROR` — this is expected, handled behavior.
- Crawlers already completed earlier in the same run keep whatever stock data
  they synced; only the crawlers after the abort point are skipped for this
  run. The existing final `stock_sync_complete` broadcast is not sent when the
  run aborts — `stock_sync_aborted` replaces it for that run.

## Testing

- `iter_products` (fake `httpx` transport):
  - 429 with `Retry-After: 5` → sleeps ~5s, not the jittered default.
  - 429 with no `Retry-After` header → falls back to the existing jittered
    delay.
  - 429 with `Retry-After: 99999` → sleep is capped at 600s.
  - 429 with a non-numeric `Retry-After` → falls back to the jittered delay.
- `_sync_stock` (fake crawlers):
  - Two fake crawlers in a row both raise a 429-flavored error → a third,
    otherwise-enabled crawler's `crawl_catalog()` is never called, and
    `stock_sync_aborted` is broadcast naming both crawlers.
  - 429, then a success, then 429 → the run does *not* abort (the success
    resets the counter); all three crawlers are attempted.
  - 429, then a non-429 failure, then 429 → the run does *not* abort (same
    reset rule applies to any non-429 outcome).

## Out of scope

- Cross-run throttle memory.
- Applying this pattern to the release-crawl worker pool (`worker-pool-pacing`,
  separate branch).
- Configurable threshold/cap constants.
