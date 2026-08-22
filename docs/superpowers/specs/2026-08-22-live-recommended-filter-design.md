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

Three changes, all in `frontend/src/App.tsx`, all additive to existing SSE
handlers — no new state, no new endpoints.

1. **`recommendedAvailable` drops the `judgmentRunning` gate** (`App.tsx:560`):
   ```js
   const recommendedAvailable = hasAnthropicKey && hasJudgedItems
   ```
   `hasJudgedItems` already means "at least one judgment exists"; whether a
   refresh happens to be running concurrently is no longer relevant to
   whether the filter is usable.

2. **`hasJudgedItems` flips true progressively**, in the
   `stock_judgment_progress` handler (`App.tsx:274`): call
   `setHasJudgedItems(true)` when `event.judged > 0`. Mirrors what
   `stock_judgment_complete` already does unconditionally, but fires on the
   first batch instead of waiting for the whole run — this is what makes a
   first-ever refresh's filter selectable mid-run rather than only after.

3. **`stockSyncGeneration` bumps on judgment events**, same pattern as the
   existing `stock_sync_progress`/`stock_sync_complete` handlers: add
   `setStockSyncGeneration(g => g + 1)` to both the `stock_judgment_progress`
   and `stock_judgment_complete` handlers. `StockBrowser`'s existing
   `syncGeneration`-keyed effects (`StockBrowser.tsx:113-117` for the item
   list, `:134-150` for the artist sidebar) already refetch on every tick —
   this just gives them something to react to during a judgment run.

No change needed to `StockBrowser.tsx` itself: the effect that resets the
filter away from "recommended" (`:118-122`) only fires when
`recommendedAvailable` goes false, which after change 1 no longer happens
mid-refresh.

## Testing

- Frontend: `stock_judgment_progress` with `judged > 0` sets
  `hasJudgedItems` true and bumps `stockSyncGeneration`; a subsequent
  `judged === 0` event (shouldn't occur, but keep the guard) doesn't flip it
  false. `stock_judgment_complete` still bumps `stockSyncGeneration` in
  addition to its existing behavior. `recommendedAvailable` is true whenever
  `hasAnthropicKey && hasJudgedItems`, regardless of `judgmentRunning`.
- Existing `StockBrowser` tests covering the `recommendedAvailable`
  false→true transition (filter option enabling, no forced reset away from
  "recommended") continue to pass unchanged — behavior there doesn't change,
  only what drives the prop.
