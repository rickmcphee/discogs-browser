# Live "Recommended" Filter During Refresh — Design Spec

_2026-08-22_

## Overview

The Store tab's "Recommended" filter option is disabled for the entire
duration of a recommendation refresh (`judgmentRunning`), even when prior
judgments already exist. `StockBrowser`'s `useEffect` at
`frontend/src/views/StockBrowser.tsx:118-122` then force-resets a user
already on that filter back to "all" the moment a refresh starts. Separately,
even a fetch that does hit the "Recommended" filter while a refresh runs
never repaints — `stock_judgment_progress`/`stock_judgment_complete` events
never bump `stockSyncGeneration`, so nothing re-triggers `StockBrowser`'s
load effect as new judgments land.

This spec makes the filter selectable throughout a refresh — including the
very first one a user ever runs, as soon as its first batch lands — and
makes the Store list/artist sidebar repaint each batch as recommendations
are added, mirroring the existing `stock_sync_progress` live-update pattern.

## Goals / non-goals

**Goals**
- "Recommended" stays selectable in the Store tab's filter dropdown while a
  recommendation refresh is in progress, provided at least one item has been
  judged (this run or a prior one).
- On a user's very first-ever refresh, "Recommended" becomes selectable as
  soon as the first batch (up to 40 items) completes, not only once the
  whole run finishes.
- While "Recommended" is selected, the item list and artist sidebar update
  each batch as new judgments land, without user action.

**Non-goals**
- No backend changes. `get_stock`/`get_distinct_stock_artists` already query
  `stock_item_judgments` live, and `upsert_stock_judgments` already commits
  per-batch — a mid-run fetch already sees everything judged so far.
- No change to batch size, judgment cadence, or the circuit-breaker/error
  handling around judgment runs.

## Design

Four changes, all in `frontend/src/App.tsx`, no new endpoints.

1. **`recommendedAvailable` drops the `judgmentRunning` gate** (`App.tsx:560`):
   ```js
   const recommendedAvailable = hasAnthropicKey && hasJudgedItems
   ```
   `hasJudgedItems` already means "at least one judgment exists"; whether a
   refresh happens to be running concurrently is no longer relevant to
   whether the filter is usable.

2. **`hasJudgedItems` flips true only when a batch actually judged
   something**, on both the `stock_judgment_progress` and
   `stock_judgment_complete` handlers: call `setHasJudgedItems(true)` when
   `(event.judged ?? 0) > 0`. `stock_judgment_progress` firing this on the
   first non-zero batch (rather than waiting for the whole run) is what
   makes a first-ever refresh's filter selectable mid-run. Guarding
   `stock_judgment_complete` the same way — rather than the unconditional
   flip it originally had — matters for a run where every batch fails
   (`judge_batch` returns `[]` on any failure, so `judged` never advances)
   or an empty catalog: without the guard, `stock_judgment_complete` would
   still unlock `Recommended` with zero actual judgments.

3. **`stockSyncGeneration` bumps on judgment events**, same pattern as the
   existing `stock_sync_progress`/`stock_sync_complete` handlers: add
   `setStockSyncGeneration(g => g + 1)` to both the `stock_judgment_progress`
   and `stock_judgment_complete` handlers. `StockBrowser`'s existing
   `syncGeneration`-keyed effects (`StockBrowser.tsx:113-117` for the item
   list, `:134-150` for the artist sidebar) already refetch on every tick —
   this just gives them something to react to during a judgment run.

4. **`hasJudgedItems` writes are sequence-guarded against stale responses.**
   Making the flip progressive (change 2) shrinks the previously-large
   "safe" window between page load and a run completing, into one where a
   judgment batch can plausibly land before the mount-time bootstrap
   `getJudgmentStatus()` call resolves — and `handleImportRecommendations`'s
   own post-import re-fetch has the same exposure. A new
   `latestHasJudgedItemsSeq` ref (`useRef(0)`) and a shared
   `refreshJudgmentStatus()` helper — same shape as the existing
   `fetchPriceStatus`/`latestPriceStatusSeq` pair — fix this: every writer
   of `hasJudgedItems` shares the one counter. The bootstrap effect and
   `handleImportRecommendations` both call `refreshJudgmentStatus()`, which
   captures the counter before firing `getJudgmentStatus()` and discards its
   result if the counter has moved by the time it resolves. The two judgment
   SSE handlers (change 2) and `handleClearRecommendations`'s explicit
   `setHasJudgedItems(false)` bump the counter immediately before writing —
   they already know the answer without fetching, so bumping first is enough
   to invalidate any older in-flight fetch. Net effect: whichever write was
   issued last always wins, regardless of network resolve order.

No change needed to `StockBrowser.tsx` itself: the effect that resets the
filter away from "recommended" (`:118-122`) only fires when
`recommendedAvailable` goes false, which after change 1 no longer happens
mid-refresh.

## Testing

- Frontend: `stock_judgment_progress` with `judged > 0` sets
  `hasJudgedItems` true and bumps `stockSyncGeneration`. `stock_judgment_complete`
  bumps `stockSyncGeneration` unconditionally but only sets `hasJudgedItems`
  true when `(event.judged ?? 0) > 0` — a fresh user whose entire run judges
  zero items (every batch failed, or nothing was left to judge) sees
  `stock_judgment_started` → `stock_judgment_complete` with `judged: 0` and
  the "Recommended" option stays disabled throughout. `recommendedAvailable`
  is true whenever `hasAnthropicKey && hasJudgedItems`, regardless of
  `judgmentRunning`.
- Existing `StockBrowser` tests covering the `recommendedAvailable`
  false→true transition (filter option enabling, no forced reset away from
  "recommended") continue to pass unchanged — behavior there doesn't change,
  only what drives the prop.
- Frontend: a deferred bootstrap `getJudgmentStatus()` response that
  resolves after a judgment SSE event has already set `hasJudgedItems` true
  does not flip it back false. The same holds for an explicit Clear: a
  deferred bootstrap response resolving after a successful clear (with a
  stale `any_judged: true`) does not re-enable Clear/Export/Recommended.
