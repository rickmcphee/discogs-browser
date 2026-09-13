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
    status TEXT NOT NULL,              -- 'running' | 'plex_matching'
                                       -- | 'complete' | 'error'
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
raises out of the chunk's transaction (rolling its uncommitted writes back
with it) and stops. It is checked at every checkpoint — which is what bounds
the damage: a worker carries on for at most one more chunk's worth of requests
on behalf of a run somebody else owns, rather than the rest of a page — and,
decisively, in the cleanup's own transaction, where the row lock that check
takes holds the claim until the cleanup commits, so a takeover waits rather
than landing halfway through a delete.

### Staleness, because a claim that cannot expire is a trap

A Machine that restarts mid-sync leaves its row saying `running` for ever.
Untreated, that row would refuse every later refresh for that user
permanently — a worse bug than the one being fixed. So the run heartbeats, and
a claim whose heartbeat has stopped can be taken over:
`SYNC_RUN_STALE_MINUTES = 15`.

The heartbeat advances with the data it describes, in the same transaction —
so the row a reader sees never runs ahead of what has actually been written.
A Discogs page is too coarse a unit to do that on, though: a page is a hundred
releases, and a release can spend a 30-second request timeout on its barcode
fetch before the pacing sleep, so a slow page can outlast fifteen minutes by
itself and invite a takeover of a claim that is being worked. Each loop
therefore checkpoints every twenty-fifth item — recording progress, proving
the claim and committing — rather than only at the page boundary. Fifteen
minutes is then far outside what either loop can go quiet for, and far inside
"a human clicked the button again".

That checkpoint runs on the loop's own connection, which is the point of
doing it this way rather than heartbeating from a second one. The app pool is
small (`max_size=10`) and a sync already holds one of its connections for the
sync's whole duration; a heartbeat that had to borrow another would queue
behind exactly the syncs it exists to keep alive, and its failures are
swallowed, so the effect would be a slow sync whose claim quietly goes stale.
Committing in chunks also bounds how long one write transaction stays open,
which a page of barcode fetches otherwise stretches to the length of the page.

Expiry is irreversible for the expired worker: the progress and finish
predicates reject a run whose heartbeat is already past the window, whether or
not anyone has claimed it yet. Without that, "stale" is only a reading — the
client is told the sync stopped and stops polling, and a worker that comes
back can refresh the heartbeat and finish into a silence nobody is listening
to. With it, a worker that went quiet that long is out of the protocol, its
next checkpoint fails, and it stops — which is what the client was already
told.

It is written with `clock_timestamp()`, not `CURRENT_TIMESTAMP`. Inside the
page's transaction the latter is the time that transaction *began* — one
page's work before the row actually lands — which would silently shorten the
window by that much on exactly the slowest syncs.

A stale run is reported as `running: false` **and** `stale: true`, separately
from its `status`. The client reads that as "the sync stopped" only while the
sync is the phase that went quiet: a stale `plex_matching` row is a Plex match
that died *after* the sync committed its rows and its final counts, and
reporting it as an unfinished sync would send the user back to redo work that
is done. Reported as running, the client would spin for ever on a
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

### The claim outlives the sync, by one phase

`_sync_collection` runs a Plex match straight after the sync when the user has
Plex configured, and this Machine's `_sync_tasks` entry stays occupied for its
duration — so `start_sync` and `start_plex_match` both refuse locally
throughout. Releasing the claim when the sync's own work ended would therefore
leave the *other* Machine free to start exactly the sync the local guard
refuses.

So the run moves to `plex_matching` rather than closing: the claim still
covers it, while `running` is already false, so the client reads the sync as
finished with its counts final and stops polling. It is released when the Plex
phase ends, on every exit including a cancelled one. The Plex loop heartbeats
on its own commits for the same reason the sync's checkpoints do — a large
library takes time to match, and an unfed claim goes stale.

The two phases have separate closers, deliberately. The sync's
`finally` backstop fires on the handoff path too, and a single closer
that accepted both phases would let that backstop close the phase it had
just handed off to.

The close is fenced like every other write, and its answer is read: the
cleanup transaction releases the run row's lock when it commits, and a Machine
that was waiting on that lock can take the claim in the moment after. A worker
that finds its close refused stops there rather than restoring crawl rows and
announcing a completed sync on the run that replaced it — on both paths, the
handoff to the Plex phase and the plain completion alike. A close that could
not be attempted at all is not read as a takeover. The Plex loop's
own heartbeat is read the same way, and for the same reason its chunk must not
commit beside that sync.

The stock-row restoration runs outside the claim on both paths — after the
sync closes on one, after the Plex phase releases on the other. It is
follow-on work for the crawl queue rather than part of the sync, and it is the
one step here with no bound worth leasing against: inside the claim, a long
enough run of it lets the lease lapse under a worker that is still working.

Still open, and pre-existing: `start_plex_match`'s own guard remains
in-process only, so a Plex match started on another Machine can still overlap
a sync. Closing that means giving the Plex match a claim of its own, which is
its own piece of work; this change covers the direction it created.

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

It speaks only when the run has actually advanced, though, which asking again
is not. The banner is shared with the stock sync, the judgment run and the
price refresh, any of which can be running alongside a collection sync and may
have written to it since the last poll; repeating an unchanged line every
three seconds would talk over all of them. The SSE path has that restraint for
free, since it only fires when something happened.

The follow itself lives outside the poll's effect, in a ref. The effect
restarts — `authState` is replaced after every backend down/up transition, and
a nonce bump restarts it deliberately — and a follow that reset with it would
lose a sync that finished during the outage: the restarted loop would find a
terminal row, take it for an old run, and return without refetching. That is
this change's own failure reached by another road. The effect is also keyed on
whether the user is signed in rather than on the `authState` object, so a
revalidation that changes nothing restarts nothing.

The follow is released as soon as the run is accounted for — by the poll's own
terminal tick, or by the SSE handlers when this is the Machine running the
sync and the stream got there first. Left held in that second case, the next
poll would republish the outcome over whatever had spoken since, which on this
path is immediate: the Plex phase that follows a sync announces itself a beat
later.

Only a run the loop has watched *running* may write an outcome to the status
bar. Without that rule, every page load would re-announce the last sync,
however old — the row is the most recent run, not a fresh event. A refresh this
tab just requested counts as watched, since the claim is taken by the request
itself.

A failed poll retries only while there is something to wait for: a network blip
must not abandon a sync being followed, nor a refused start that has not yet
been resolved — that click has said nothing yet, and going quiet on it puts it
back to looking like a no-op. A refused start gives up after a few failed
reads and reports the refusal on its own; a sync being followed keeps waiting,
because it is running and its outcome is worth having. A failed poll on a tab
that was merely checking has nothing to retry for and stops.

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
- A worker whose close is refused stops before restoring crawl rows or
  announcing a completed sync, on the plain path as on the Plex one.
- A worker whose claim is taken over mid-sync stops rather than running its
  destructive cleanup: the wantlist record its stale snapshot would have
  deleted survives, it broadcasts no completion, and the replacement's claim
  is left running and intact.
- A run past the window cannot be advanced or closed by the worker that owns
  it, even with nobody else having claimed it.
- Progress lands a quarter of the way through a page, against an app pool of
  one connection — so the checkpoint demonstrably needs no second one.
- The claim is refused for another Machine throughout the Plex phase, and
  released once it ends.
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
  be one;
- a message another job put on the banner survives a poll that finds the run
  unchanged;
- a sync followed across a backend outage still reports its outcome and
  refetches, with the run having finished while nobody could see it;
- a Plex phase that goes stale reports the sync's outcome rather than claiming
  the sync stopped;
- an outcome the stream delivered is not republished by the poll over the Plex
  phase's own line.

Each was confirmed to fail against a build with the poll disabled.

## Amendments to other specs

- [`2026-08-16-fly-multi-machine-design.md`](2026-08-16-fly-multi-machine-design.md)
  — its "Cross-Machine SSE fan-out" non-goal now has an exception.
- [`2026-06-27-discogs-browser-design.md`](../../superpowers/specs/2026-06-27-discogs-browser-design.md)
  — the Refresh Collection flow.
- [`2026-07-04-wishlist-design.md`](../../superpowers/specs/2026-07-04-wishlist-design.md)
  — `/collection/status`'s response, and a pre-existing drift in what it counts.
