# Collection sync run visibility design

Date: 2026-09-13
Branch: `claude/practical-cerf-kvjwbf`

## Problem

Reported as: *"The refresh button in Collection doesn't appear to fetch new
additions to discogs collection irrespective of the option chosen in the
popup."*

Both halves of that are explained by one gap, and it is not in the sync.

The sync itself picks up a new Discogs addition. Confirmed directly against
the real code path, under the real `app_user` role with RLS enforced: a second
sync over a collection that has grown by one release inserts the new `catalog`
row and the new `library_items` row, in `mode="new"` (which skips releases
already flagged `in_collection`) exactly as in `mode="all"`. Nothing on the
write path drops it.

What fails is everything downstream of that.

**The refresh is reported over a channel the browser may not be listening
on.** `POST /api/collection/refresh` returns as soon as the task is created;
everything the user ever learns about the sync arrives as `sync_started` /
`sync_page_fetched` / `sync_progress` / `sync_complete` / `sync_error` events
on `GET /api/crawl/stream`. Those are broadcast **in-process** —
`CrawlManager._subscribers` is a list of `asyncio.Queue`s belonging to the
streams this process is serving. The deployment runs more than one Machine
(`backend/fly.toml`: `min_machines_running = 2`, `auto_stop_machines = "off"`)
behind one hostname, with no affinity between a browser's requests. The SSE
stream and the refresh request are two independent requests. When they land on
different Machines — about as often as not — the tab that asked for the sync
hears nothing at all about it.

[`2026-08-16-fly-multi-machine-design.md`](2026-08-16-fly-multi-machine-design.md)
took that gap deliberately, reasoning that "only the crawl's *eventual* effect
(the resulting `listings`/`stock_items` rows) is consistent, not the live
narration of it". That reasoning holds for the Store tab, which re-reads those
rows on its own. It does not reach the collection sync, because:

**The collection table refetches only when one of those events arrives.**
`RecordBrowser`'s load effect is keyed on `syncGeneration`, and `syncGeneration`
is bumped from the `sync_progress` / `sync_complete` / `sync_error` SSE handlers
in `App.tsx` and from nowhere else. No event, no refetch — so the eventual
effect never reaches the screen either. The library shown is the one that was
there before the sync, new additions and all, until something else happens to
re-issue the query (a filter change, or a full page reload).

**And nothing else ever syncs a collection.** `scheduler.py` schedules crawl
sweeps and stock syncs; `scheduler.configure_sync` was deleted with the
crawl-queue refactor and collection sync is manual-trigger-only. The button is
the only path, so a button that looks inert is the whole feature looking
inert.

The failure is also silent in both directions. With no events:

- the button never spins and the status bar stays empty, so the click reads as
  a no-op;
- a sync that *fails* — a Discogs error, a revoked token — says nothing
  either. `sync_error` goes the same way as the rest;
- a second click is refused with `409` from `CrawlManager.sync_running`, which
  is also per-process, so the refusal only fires on the Machine that happens to
  hold the task. On the other one the click starts a **second** concurrent sync
  for the same user.

## Scope

Makes one run of the collection sync a row both Machines can read, and has the
client follow that row.

Touches:

- `backend/db.py` — the `library_sync_runs` table, its RLS policy and grant,
  and the claim/progress/finish/read helpers.
- `backend/crawl_manager.py` — `start_sync` claims the run before creating the
  task; `_sync_collection_blocking` advances and closes it.
- `backend/routers/collection.py` — `GET /api/collection/status` carries the
  run.
- `frontend/src/App.tsx` — a poll that follows a run to its end, and a `409`
  that joins a running sync instead of reporting a failure (and says so when
  the refusal was not one).
- `frontend/src/api/client.ts`, `frontend/src/api/types.ts` — the run's shape,
  and the HTTP status on a failed request so `409` is distinguishable.
- Tests: `backend/tests/test_crawl_manager.py`,
  `backend/tests/test_collection_router.py`,
  `frontend/src/test/collectionSyncPoll.test.tsx`,
  `frontend/src/test/wantlistRefresh.test.tsx`.

Out of scope: cross-Machine SSE fan-out in general. The stock sync's and the
crawl worker's narration still only reach the Machine producing them, and the
`LISTEN`/`NOTIFY` bridge that would fix all of it at once is still the larger,
separate piece of work the multi-Machine design named. This fixes the one case
where the missing narration is also the only thing that would have refreshed
the view.

## Design

### `library_sync_runs`

One row per user, in the tenant schema beside `library_items`, RLS-scoped on
`user_id` like every other per-user table:

```sql
CREATE TABLE IF NOT EXISTS library_sync_runs (
    user_id INTEGER PRIMARY KEY REFERENCES users(id),
    status TEXT NOT NULL,              -- 'running' | 'complete' | 'error'
    mode TEXT NOT NULL,
    scope TEXT NOT NULL,
    page INTEGER,
    total_pages INTEGER,
    synced INTEGER NOT NULL DEFAULT 0,
    wishlist_synced INTEGER,
    error TEXT,
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    heartbeat_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,
    run_token TEXT                     -- which claim owns the row; see below
);
```

Rewritten by each run rather than appended to. Nothing here is history — the
question it answers is "what is my sync doing *now*, and how did the last one
end" — and the claim below needs a single conflicting key to be atomic.

`app_user` gets `SELECT, INSERT, UPDATE` and deliberately not `DELETE`: a row
is claimed, advanced and closed in place, and the next run overwrites it.

### The claim replaces the per-process guard

`start_sync` keeps its in-process check (it is free, and it catches this
Machine's own duplicate before a round trip) and then claims the run in
Postgres, off the event loop via `run_in_threadpool` — the same treatment
`start_stock_sync` gives its advisory lock, for the same reason.

```sql
INSERT INTO library_sync_runs (user_id, status, mode, scope, run_token)
VALUES (..., 'running', ..., ..., ...)
ON CONFLICT (user_id) DO UPDATE SET status = 'running', run_token = EXCLUDED.run_token, ...
WHERE library_sync_runs.status <> 'running' OR <heartbeat is stale>
RETURNING run_token
```

No row returned means a live run already holds the claim, and `start_sync`
returns `False` — which the router already turns into the `409` it always
returned. The refusal now holds across Machines, so the duplicate concurrent
sync is gone with it.

The claim and the record are the same row on purpose. A second advisory lock
(the shape the stock sync uses) would answer "is one running" and nothing else;
the client here needs to know *how far along* and *how it ended*, and a lock
cannot carry that. The stock sync's own cross-Machine rejection had to state
`running: true` by hand precisely because the holder's progress lives in
another Machine's memory, unreadable from the one answering.

### Every write names the claim it belongs to

The claim carries a `run_token`, and every later write to the row — progress,
heartbeat, close — requires it. Without it a writer can only address "whatever
is running for this user", which after a takeover or a re-claim is somebody
else's run, and two reachable sequences corrupt a newer run with an older
one's writes:

- **A taken-over worker is still alive.** Staleness (below) is judged on a
  heartbeat, not on proof of death; a worker slow enough to be taken over is
  still running, still advancing counters and still heartbeating. Its
  heartbeat is the worse half: it would hold the new claim open in the old
  worker's name.
- **A backstop outlives its own run.** `_sync_collection_blocking` records its
  outcome and then runs an unconditional `finally` (below). A refresh landing
  in that gap takes a fresh claim, which the old backstop's `status =
  'running'` predicate matches — reporting a sync that is seconds old as
  failed, and releasing its claim for a duplicate to take.

Both are silent when they happen, and both produce exactly the symptom this
change is fixing.

Fencing the row is only half of it, though, because the row is not the only
thing a dispossessed worker writes. It would go on committing library rows
alongside its replacement, and then reach `clear_wishlist_flags_not_in` /
`delete_orphaned_releases` — the sync's only destructive statements — driven
by a `wishlist_seen` snapshot older than the sync that replaced it, which can
delete a wantlist record the replacement has just written. So losing the claim
has to stop the worker, not just its bookkeeping: the progress write reports
whether the run is still this worker's, and a worker that finds it is not
raises out of the page's transaction (rolling its uncommitted writes back with
it) and stops. It is checked at every page commit, at every twenty-fifth item
— the rest of a page is another hundred requests made on behalf of a run
somebody else owns — and, decisively, in the cleanup's own transaction: the
row lock that check takes holds the claim until the cleanup commits, so a
takeover waits rather than landing halfway through a delete.

### Staleness, because a claim that cannot expire is a trap

A Machine that restarts mid-sync leaves its row saying `running` for ever.
Untreated, that row would refuse every later refresh for that user
permanently — a worse bug than the one being fixed. So the run heartbeats, and
a claim whose heartbeat has stopped can be taken over:
`SYNC_RUN_STALE_MINUTES = 15`.

The heartbeat advances at each page commit, inside that page's own
transaction — so the row a reader sees advances exactly when the data it
describes does, never ahead of it. That alone is too coarse to bound the
window on, though: a page is a hundred releases, and a release can spend a
30-second request timeout on its barcode fetch before the pacing sleep, so a
slow page can outlast fifteen minutes by itself and invite a takeover of a
claim that is being worked. So the run also heartbeats every twenty-fifth
item, on its own connection, landing while the page's transaction is still
open. Fifteen minutes is then far outside what either loop can go quiet for,
and far inside "a human clicked the button again".

It is written with `clock_timestamp()`, not `CURRENT_TIMESTAMP`. Inside the
page's transaction the latter is the time that transaction *began* — one
page's work before the row actually lands — which would silently shorten the
window by that much on exactly the slowest syncs.

A stale run is reported as `running: false` **and** `stale: true`, separately
from its `status`. Reported as running, the client would spin for ever on a
sync nothing is doing; reported as merely finished, the user would never learn
why their refresh stopped. It gets its own line in the status bar, and the
next click is no longer refused.

### Closing the run

Every exit from `_sync_collection_blocking` closes it: the three early error
returns (no user, no Discogs token, collection-fields fetch failed) now go
through one `sync_error` helper that broadcasts *and* records, the happy path
records `complete` with both counts, and the `except` records `error` with the
message. A `finally` closes anything else as an error — its `UPDATE` is
`WHERE status = 'running'`, so it can be called unconditionally and cannot
overwrite an outcome a path already recorded.

Best effort throughout, on its own connection: failing to *narrate* a sync must
never be what ends one, and the connection the page loop was using may be in a
failed transaction by the time the error path runs.

### The two start paths share a lock

`start_sync` and `start_plex_match` refuse to overlap, and used to get that for
free: each checked both task maps and registered its own with no `await` in
between, which asyncio's single-threaded scheduling makes atomic. The claim
puts an await inside `start_sync`'s half of that, so a plex match starting
during the claim would see both maps idle, register itself, and have the
collection task created on top of it. Both now hold one lazily-created
`asyncio.Lock` across guard, claim and registration — the same shape
`start_stock_sync` already uses for its own guard-acquire-assign sequence.

### `GET /api/collection/status` carries the run

Rather than a new endpoint. The client already calls this one (it drives the
"Collection already loaded" modal), so no new function joins the API surface
that every App-rendering test has to double, and "the state of my collection"
is what this endpoint is already for. `sync` is `null` when the user has never
synced, and the field is optional on the client so a reply without it reads the
same way.

### The client follows the run

`App.tsx` polls it every 3s while a run is live, from a loop that starts:

- on mount, so a tab that loads mid-sync (a reload, a second tab, a sync
  started from a phone) picks it up; and
- after a refresh request, including one refused with `409` — a sync already
  running is a thing to follow, not a failure to report. That is the case this
  whole change is about: the `409` may well be the other Machine saying it is
  busy with the sync this user just asked for.

A refused start is followed on the evidence, not on the refusal. `409` is what
the router answers for *every* reason `start_sync` declines, and a Plex match
for this user is one of them — so a 409 is not proof that a sync exists to
follow. A refusal therefore adopts only a run the poll finds actually running;
when it finds none (or only a finished run from some earlier click, which is
not this click's answer and must not be reported as one) it says the sync could
not start and why it might not have. Deciding on observed state rather than on
a reason code keeps that true for whatever else `start_sync` comes to decline.

The loop drives the same state the SSE handlers do — `syncing`, the status
message, and `syncGeneration`, which is what actually gets the new records onto
the screen. The SSE path stays as the same-Machine fast path; when both are
live they agree, and a duplicated progress tick costs one extra refetch and
nothing else.

Only a run the loop has watched *running* may write an outcome to the status
bar. Without that rule, every page load would re-announce the last sync,
however old — the row is the most recent run, not a fresh event. A refresh this
tab just requested counts as watched, since the claim is taken by the request
itself.

A failed poll retries only while there is something to wait for: a network blip
during a sync being followed must not abandon it, but a failed poll on a tab
that was merely checking has nothing to retry for.

## Testing

Backend:

- A sync started through `start_sync` records its progress (`page`,
  `total_pages`, `synced`) and its completion in the row; a sync that fails
  records the reason.
- A manager that has never heard of a run still refuses to start one over it —
  the cross-Machine case, with the claim taken directly rather than by another
  process.
- A run whose heartbeat has stopped is taken over rather than refusing for
  ever.
- Closing a run releases the claim; a second close cannot overwrite the first's
  outcome (what makes the `finally` backstop safe).
- A finished run's late backstop cannot close the claim taken after it, and a
  taken-over run's old owner can neither advance nor heartbeat the run that
  replaced it.
- A worker whose claim is taken over mid-sync stops rather than running its
  destructive cleanup: the wantlist record its stale snapshot would have
  deleted survives, it broadcasts no completion, and the replacement's claim
  is left running and intact.
- A plex match cannot register itself while a sync is mid-claim.
- `GET /api/collection/status` reports a running run, reports an abandoned one
  as stale rather than running, reports nothing before a user's first sync, and
  is scoped to the calling user.

Frontend (`collectionSyncPoll.test.tsx`), all with an `EventSource` that never
emits — the cross-Machine case reproduced directly:

- a run followed to completion refetches the collection and reports the counts;
- a failed run and an abandoned run each say so;
- a run that had already finished before the page loaded is not announced;
- a refresh refused with `409` follows the running sync instead of reporting a
  failure, and says the sync could not start when the refusal turns out not to
  be one.

Each was confirmed to fail against a build with the poll disabled.

## Amendments to other specs

- [`2026-08-16-fly-multi-machine-design.md`](2026-08-16-fly-multi-machine-design.md)
  — its "Cross-Machine SSE fan-out" non-goal now has an exception.
- [`2026-06-27-discogs-browser-design.md`](../../superpowers/specs/2026-06-27-discogs-browser-design.md)
  — the Refresh Collection flow.
- [`2026-07-04-wishlist-design.md`](../../superpowers/specs/2026-07-04-wishlist-design.md)
  — `/collection/status`'s response, and a pre-existing drift in what it counts.
