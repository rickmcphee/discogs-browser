# Live "Recommended" Filter During Refresh — Design Spec

_2026-08-22_

**Amendment (2026-09-06, branch `claude/store-cheapest-filter-x4tdwl`, second PR):** "the Store tab's filter dropdown" below is now a `Filter` popover with a radio per option (`frontend/src/components/StockFilter.tsx`); `Recommended` stays selectable during a refresh exactly as described, it is just a radio rather than an `<option>`. See [`docs/specifications/shaping/2026-09-06-store-filter-popover-design.md`](../../specifications/shaping/2026-09-06-store-filter-popover-design.md).

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

   **(Amended 2026-09-16, branch `claude/wizardly-goldberg-mxvey2`:** the
   `stock_judgment_complete` half is now `judged > 0 || inherited > 0`. A run
   can write judgments without judging anything, which was not possible when
   this was written: `propagate_stock_judgments` gives a record's verdict to
   its other listings, and a run that only does that reports `judged: 0`
   alongside a non-zero `inherited`. Gating on `judged` alone left
   `Recommended` and Export disabled in a client whose bootstrap
   `any_judged` was still false. The reasoning above is otherwise intact —
   both counts being zero still means nothing was judged, so a fully-failed
   run and an empty catalog stay guarded. `stock_judgment_progress` is
   unchanged: progress events carry no `inherited`. `stock_judgment_stopped`,
   which arrived on `main` after this was first written, *does* carry it and
   is handled in the same branch — a stopped run has still fanned out
   everything it judged before the stop, so the same "wrote rows without
   judging" case reaches that ending too. See
   [`2026-09-16-record-level-judgment-design.md`](../../specifications/shaping/2026-09-16-record-level-judgment-design.md).**)**

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

   **Amendment (2026-09-16, PR #368):** a handler that does *both* — writes
   optimistically and issues a read — has to do them in that order, because
   "issued last wins" cuts both ways. The terminal judgment handler gained a
   `refreshJudgmentStatus()` call so the run row could correct flags cleared
   by a late ending, and it sat *above* the optimistic `setHasJudgedItems`,
   which then bumped the counter past it and threw its answer away every
   time. Harmless while the event and the database agree; wrong when they do
   not, which is precisely when a terminal event is replayed after a Clear
   has emptied the table — its counts describe a run that really happened,
   the read is the only thing that knows the rows are gone, and discarding it
   left `Recommended` and `Export` enabled over nothing. The optimistic write
   covers the window until the read returns and beats any *older* fetch; the
   read is issued after it and gets the last word.

   And it has to be the *retried* read (`discoverJudgmentRun`), not a single
   fire-and-forget one. `refreshJudgmentStatus` swallows a failed request into
   `null`, and a terminal handler has just stopped the run poll, so on the
   Machine that did not run the job nothing else will ask again — one dropped
   request leaves the optimistic write standing for good, which is the same
   empty-table state the ordering above exists to prevent. The bounded retry
   already carried that reasoning for the discovery read ("A single attempt is
   not enough anywhere this is called"); it applies here for the same reason.

   **Amendment (2026-09-17, PR #368):** the sequence protocol governs the run
   *flags*, and the spinner and the banner were left outside it — reconciled
   by nothing, since no read writes them. A terminal judgment event writes all
   three, so an ending replayed onto a live run had its flags deferred (to a
   start in flight) or corrected (by the read above) while its `setSyncing
   (false)` and its "Finished…" banner stood, and on the Machine not running
   the job no later event replaces them: a run still spending the user's key
   reads as finished for its whole length, Stop button beside a completion
   message. The rule is now that whoever restores the flags restores these
   with them — `showJudgmentRunning()`, called from the start reply when it
   reports a run under way, and from `refreshJudgmentStatus` itself whenever a
   read finds one. That covers the *error* ending, which reconciles the flags
   exactly as the two terminal ones do (an error names no run either, so a
   replayed one can belong to a run since replaced, and "Finding
   recommendations failed" left up with the spinner off then describes the
   wrong run for the length of the right one) — and, more to the point, it
   covers the **mount-time** read, which is where the gap was worst: a page
   loaded while a run is going on the other Machine takes its Stop button from
   that read and from nothing else, since no event is coming. Putting the
   restore at each ending's call site, as the first two passes at this did,
   left that one out; it belongs in the read. Fenced there on
   `latestJudgmentActionSeq`, since a newer action is newer truth, and on
   `judgmentStopPending`, because a Stop in flight owns the presentation
   exactly as it owns the flags — `refreshJudgmentStatus` already applies that
   condition to the flags it writes beside it.

   **In the innermost read, not the retry loop around it**, which is what
   makes the fence below survivable. `discoverJudgmentRun` runs its loop once,
   at mount or on a click; the run *poll* calls `refreshJudgmentStatus`
   directly. With the turn-on at the outer level, a read that declined — for
   the good reason in the next paragraph — had no second chance, and a
   cross-Machine run whose mount-time read lost that race sat with no banner
   and no spinner for the rest of its length once the competing writer went
   quiet, its row-only ending unreported for want of a claim. One level down,
   every reader of the row gets it and every tick is a fresh baseline. It
   fires only while **no claim is held**: while one is, the run's own progress
   lines own the banner, and re-announcing the generic message over "…40/120"
   every few seconds is the clobber the fence exists to prevent.

   **And on `statusWrites`, because neither of those fences can see the
   banner.** `latestJudgmentActionSeq` moves on user actions only; no SSE
   handler advances it. A *judgment* event is covered anyway, since every one
   of them bumps `latestJudgmentRunSeq` and `refreshJudgmentStatus` discards a
   read that lost that race — but a **stock sync** writes the same shared
   banner and touches no judgment counter at all, so a discovery read landing
   mid-sync replaced its progress line with a generic judgment one it had no
   newer knowledge than. The read therefore records `statusWrites` at entry
   and restores only if nothing has written the banner since, the same measure
   the price-refresh claim uses. The ordering that makes this work is the one
   that measure already documents: **a caller writes the message it wants the
   read to be allowed to replace, and issues the read after it**, so its own
   write is part of the baseline rather than news. Both endings, the error
   ending and the start handler's post-refusal read were reordered to match.

   **And the read takes the banner down as well as putting it up**, which only
   became necessary once it started putting it up. A run this client heard
   about *only* over HTTP has no terminal event coming on this Machine, so
   nothing else would ever end the presentation: "Finding recommendations for
   Store items…" spun past a run that had completed, stopped or failed, for as
   long as the page stayed open. `refreshJudgmentStatus` already reconciled the
   flags from the row and now reconciles these with them, using the same claim
   in both directions — the claim records `statusWrites` after a running
   banner is written, and the ending is written only if that value still
   stands, so a terminal event that did arrive (and wrote the same ending
   already) is not echoed. The claim is dropped either way, since the run is
   over.

   **The banner and the spinner are two different kinds of thing, and only
   one of them is claimed.** A banner holds one message and the newest writer
   wins, so the claim records `statusWrites` and the take-down writes an
   ending only if that value still stands — a write can take the message
   without touching the spinner (a source-filter load failure does exactly
   that), and answering both with one number preserved a *finished* run's
   spinner every time an unrelated message arrived.

   "Busy", though, is true while **any** operation is going, so the spinner is
   derived from `spinnerOwners`, the set of operations that currently want it:
   `beginSyncing(owner)` adds, `endSyncing(owner)` removes, and `syncing` is
   whether the set is non-empty. The collection sync, the stock sync and the
   judgment run each hold their own key. This was a raise *count* for several
   rounds, and a count cannot express it: it answers "has anyone raised since
   I did", which gets one of the two cases wrong whichever way it is read. A
   judgment run renewing its claim while a stock sync is going bumps the count
   itself, so at its ending the number matches and it takes the sync's spinner
   away with it; and a collection poll re-raising every tick makes the
   judgment's own claim unmatchable for good, stranding its spinner instead.
   The set also ends the older version of the same fault, where each feature
   lowered the shared spinner outright at its own ending and could hide
   another's still-running work.

   **Writing a live-run banner, raising the spinner and claiming both are one
   act**, and `showJudgmentRunning(message?, eventId?)` is the only way to
   perform any of them. Every message that says a run is live goes through it:
   the generic line the reads write, `stock_judgment_started`,
   `stock_judgment_progress`, the start's "already under way — use Stop", its
   failure recovery's "the run did start — use Stop", and the Stop reply's
   "Stopping — finishing the batch already paid for". A run finishing a batch
   it has paid for is still a live run; the two *endings* beside that one are
   not, and claiming one would put a spinner over a run that has stopped.

   They were three separate steps a writer had to remember, and three
   consecutive rounds found one that forgot — first a claim without a raise,
   then three writes without a claim. Both failures are the same underneath.
   A claim that names an older write is a claim the poll will not honour, so
   when it sees the run end it lowers the spinner and then declines to write
   the ending, leaving a live-run message beside a Refresh button for as long
   as the page stays open; and a claim taken once and never renewed is broken
   by the run's *own* next progress line, after which nothing can close the
   run out at all. The raise cannot be left to the reads either, because the
   fence protecting a claimed banner also blocks the read that would otherwise
   have raised the spinner — a progress line arriving first left an active run
   turning nothing. A caller cannot forget a step it has no way to take
   separately.

   Renewing on our own writes is what keeps an *unrelated* writer — a stock
   sync on the same shared banner — protected, which is the reason for
   measuring writes rather than simply flagging ownership.

   **Ending a run gives up its share rather than clearing the state**, and
   `releaseJudgmentPresentation()` is the one way to do it: the HTTP take-down
   and both terminal SSE handlers call it, and it removes the judgment owner —
   which lowers the spinner only if no other owner remains, whenever that owner
   was added. The events used to lower it outright, so a stock sync that had
   the spinner up when a judgment run ended lost its busy indicator on the
   spot, and at the time nothing put it back.

   **And every sync event takes its owner, not only `*_started`.** A progress
   line proves the work is live whether or not this client saw it begin: a
   stream reconnecting after the start event has left the replay buffer
   receives one first. `spinnerOwnerOf` derives the owner from the status, in
   one place rather than in a dozen handlers, so a handler added later cannot
   forget it — and a sync that has only ever sent progress still holds a
   share, which is what keeps a judgment ending beside it from removing the
   last one. It does not exclude the endings, so that what ends a sync is
   decided in one place: the handler for that ending, which gives the share
   back through `endSyncing` in the same pass. A list of terminal statuses
   beside it is a second answer to the same question, free to disagree — and
   it did. `stock_sync_error` ends the sync only when it names no `source`;
   with one it reports a single catalog site failing inside a run that goes on
   to the next, and a client whose first event was that one held no share for
   the sync it went on narrating.

   The *message* is not released the same way: a terminal event is
   authoritative about its own run's ending and writes it regardless, which is
   what puts it inside the baseline the read that follows measures against.
   Only the spinner is arbitrated, because "something is busy" is a claim about
   the whole page and lowering it on someone else's behalf is never right.

   **The start and stop replies read the run row too**, which is what the
   list above is really recording: for ten rounds the poll was the only reader
   being audited, and every message those two write about a live run had the
   same omission. An *accepted* run can also be over before the router reads
   its row: the start returns once the task exists, and a run with nothing to
   bill —
   every record already judged, so the whole of it is propagation — finishes
   inside that gap. The reply then carries `started: true`, `running: false`
   and a terminal row, no claim was ever taken because nothing was running to
   claim for, and the read that follows has nothing to reconcile against. That
   ending is reported from the row the reply handed over, before the read is
   issued so the read may still overrule it.

   A single `judgmentEndingMessage` builds that message for both the event
   path and the row path, so the two cannot drift into describing one ending
   two ways — and `judgmentRowEndingMessage` wraps it for the two readers
   handed a *row* rather than an event, the poll and that start reply, so the
   stale distinction below is drawn once rather than at each of them. A stale
   row still reads `status: 'running'` while `running` is false, since that is
   what
   staleness is: a claim whose heartbeat stopped. Reporting it as a completion
   would invent a finish for a worker that died, so the poll says it stopped
   responding, in the same words the Stop reply already uses
   (`STALE_JUDGMENT_RUN_MESSAGE`).

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
