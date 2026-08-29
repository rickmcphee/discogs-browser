# Stock-Sync 429 Backoff — Design Spec

_2026-08-02_

**Amendment (2026-08-28, branch `claude/store-crawler-activity-missing-1tybli`):** the non-429 retry rule described below — "Any other failure (timeout, DNS, non-429 HTTP status, etc.)" enters the `consecutive_failure_limit` budget — is **unchanged**, but `iter_products()` no longer reaches it at the one place it used to fire pointlessly. Shopify's storefront `products.json` refuses `page` past 100 with an HTTP 400 regardless of `limit`, so the endpoint reaches 25,000 products and no further, and there is no cursor alternative — it sends no `Link` header, unlike the Admin API. That ceiling used to be indistinguishable from a transient fault: the 400 was retried the full budget (10 paced requests, ~4 minutes, against a deterministic error) and then raised, and because `_sync_stock` skips `replace_stock_items()` entirely when a catalog crawl raises, **every product already fetched in that run was discarded**. A store larger than the ceiling therefore logged a full run of healthy page-fetch lines at INFO and then wrote no rows at all, with the explanatory line at ERROR — which `routers/logs.py`'s exact-level filter (`level = ANY(...)`, not level-and-above) hides from an INFO view. Waterloo Records hit this on every sync.

`iter_products()` now stops before requesting page `_MAX_PAGE + 1` at all, so the ceiling is never a request and never an error, and logs that it stopped there at INFO (not WARNING) for the same log-filter reason `_sync_stock`'s swept-rows line does. That message says only that the ceiling was reached and that anything beyond it is unreachable — a collection of exactly `_MAX_PAGE * _PAGE_LIMIT` products also ends on a full page and is complete, so it does not claim products were left behind.

A 400 *below* the ceiling deliberately keeps the existing behaviour and raises. An earlier draft of this change treated any 400 after page 1 as end-of-pagination, which GitHub Copilot's review on PR #210 correctly identified as destructive: with the proactive stop in place, such a 400 is unexplained rather than expected, and ending the walk on it would hand `_sync_stock` an arbitrary prefix, which `replace_stock_items()` would then use to DELETE the store's complete snapshot and reinsert only that prefix. Raising is the fail-safe outcome — the previous snapshot survives untouched and the site's consecutive-failure breaker sees the error.

**Amendment (2026-08-09):** the last sentence of the 2026-08-04 amendment below — "No cooldown between separate `_sync_stock` runs exists before or after this amendment ... it remains a known, accepted gap" — is now true **only of 429s**. `_sync_stock` applies the release path's per-site consecutive-failure breaker (`consecutive_failure_limit`, 30-minute cooldown, `CrawlManager._record_site_result`) to catalog crawlers: a non-429 failure counts, a success resets, and a cooling-down source is skipped with an INFO line instead of being crawled. Prompted by Amoeba Music answering every request with a Cloudflare 403, which the initial attempt *and* `_run_catalog_crawler`'s context-reset retry both hit, on every scheduled sync, with nothing remembering the previous run. A 429 is still deliberately excluded from that breaker — it keeps the handling this document describes (never retried, plus the run-level 2-consecutive-sites abort) because it is an expected, handled condition rather than evidence the site is broken, so the accepted gap stands for the rate-limit case. The breaker's state is in-process, like the release path's, so a restart clears it. See items 13-14 of [`2026-08-01-worker-pool-pacing-design.md`](2026-08-01-worker-pool-pacing-design.md).

**Amendment (2026-08-04): the "Retry-After handling" section below is reversed — a 429 is no longer retried at all.** The full debug-level header logging added alongside this amendment (see `stock-sync-429-followup` investigation notes) captured a real Relapse 429 carrying `Retry-After: 60`. Manually retrying at that exact 60s interval, repeatedly, still failed every time — the header's stated wait does not reflect when the underlying platform-edge IP throttle actually clears. Retrying a 429 at all, Retry-After-paced or not, just spends `consecutive_failure_limit`'s entire retry budget (up to ~10 minutes per crawler) before giving up anyway, which is strictly worse than giving up immediately. `iter_products()` now raises on the very first 429, uncounted against `consecutive_failure_limit` (that budget still applies to genuinely transient non-429 failures, e.g. 503s, unchanged). `_parse_retry_after()` is deleted along with the retry path it fed — the raw header value is still visible via the debug log, just no longer acted on. The "Run-level circuit breaker" section below is **not** affected by this reversal — `_sync_stock`'s 2-consecutive-429 abort logic is unchanged; it now simply triggers much faster per crawler (immediately, rather than after each crawler's own ~10-minute retry loop), so a run that's going to abort does so within seconds rather than ~20 minutes. No cooldown between separate `_sync_stock` runs exists before or after this amendment — see the "Non-goals" section below, which already scoped that out; it remains a known, accepted gap (a fresh run has no memory that the previous one aborted on a 429 and will hit the same still-active block again if triggered too soon).

## Overview

`_sync_stock` (`backend/crawl_manager.py`) iterates enabled catalog crawlers one
at a time, each pulling pages from `shopify_catalog.iter_products()`. On
2026-08-02, a single run hit HTTP 429 on the *first* request of four unrelated
Shopify merchants in a row (Run For Cover, Equal Vision, Saddle Creek,
Temporary Residence Ltd) — after the existing 22.5–45s pre-request delay added
in `dd969c1`/`98cbcfe`; robots.txt, this app's Playwright-only
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
