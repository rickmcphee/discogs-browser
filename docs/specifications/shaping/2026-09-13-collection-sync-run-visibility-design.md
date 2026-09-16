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

`start_sync` claims the run in Postgres, off the event loop via
`run_in_threadpool` — the same treatment `start_stock_sync` gives its advisory
lock, for the same reason — and the row, not `_sync_tasks`, is what decides.
The in-process check is *not* kept in front of it as a free early-out, and the
reason is worth stating because "it costs nothing and catches the local
duplicate" is the obvious argument for keeping it: `sync_running()` answers
"is this task object still pending", which a worker wedged in a blocking call
says for ever. Refusing on that before reading the row made this Machine the
one place a run it had itself abandoned could never be recovered — the row's
expired heartbeat, which exists precisely to say the worker is gone, was never
reached — while the other Machine read it and took the claim. The same click
then succeeded or failed according to load balancing.

Nothing is loosened by dropping it. A healthy local run is refused by the
claim just as firmly, because that run's own heartbeat is keeping its row
fresh; the check was only ever redundant with the row, except in the one case
where it was wrong. When the claim *is* granted and this Machine still has a
task for that user, the task is working a run that is no longer its own: its
fencing would stop it at the next checkpoint, but a worker that never reaches
one is exactly the case in hand, so it is cancelled outright.

`start_plex_match`'s guard stays in front, because the Plex match still has no
claim of its own — see the open item at the end of this document.

```sql
INSERT INTO library_sync_runs (user_id, status, mode, scope, run_token)
VALUES (..., 'running', ..., ..., ...)
ON CONFLICT (user_id) DO UPDATE SET status = 'running', run_token = EXCLUDED.run_token, ...
WHERE NOT (library_sync_runs.status IN ('running', 'plex_matching'))
      OR <heartbeat is stale>
RETURNING run_token
```

Both claimed statuses, not just `'running'`. `plex_matching` holds the claim
too — that is the whole point of the phase handoff described further down, and
a predicate naming only `'running'` would hand out a claim during the Plex
phase, which is the overlap the handoff exists to prevent.

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
- **A backstop reports a failure its run did not have.** The `status =
  'running'` predicate makes that `finally` a no-op *once an outcome is
  recorded* — and a close that answered `None` recorded nothing. The row is
  still `running`, so a generic "Sync ended unexpectedly" lands over a sync
  that completed and committed its records, for every client reading the row,
  while the same-Machine stream has already announced success. So the backstop
  retries the outcome the body chose, and falls back to the generic failure
  only for a run that never chose one.

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
the claim and committing — rather than only at the page boundary, and no less
often than `crawl_manager.SYNC_CHECKPOINT_MAX_SECONDS` however slow the items
are. Fifteen minutes is then far outside what either loop can go quiet for,
and far inside "a human clicked the button again".

**Amendment (2026-09-13, merging `main`):** that wall-clock ceiling is not
how this shipped. The count alone bounded the gap, on the reasoning above
that an item's worst case is one 30-second request timeout, so twenty-five of
them stay inside the window. `2026-09-13-discogs-api-429-retry-design.md`
landed on `main` in the meantime and ended that: `discogs._get_with_retry`
waits out a rate limit *on top of* those timeouts, so under a sustained 429 a
chunk of twenty-five items runs far past fifteen minutes while the sync is
alive and committing the whole way. The count was measuring the wrong thing —
items, when the window measures time — and it took a change in an unrelated
module to expose it. A sync in that state would be read as stale, told the
user it had stopped, and taken over by the next `start_sync`, at which point
the fencing above stops the worker that was making progress. Nothing is
corrupted; the sync just restarts having lied about why. Bounding the gap in
the unit the window is expressed in is what makes it robust to whatever a
request costs next.

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

*Every* writer, and the Plex closer is the one easiest to leave out — it
closes a phase rather than a sync, so it reads as bookkeeping rather than as a
lease write. It is not: a Plex batch that crosses the window has already lost
the claim, its next heartbeat raises, and an unfenced closer would revive the
row as `complete` and answer `True` to a caller that takes that answer as
permission to restore stock rows. The fence and the answer are one mechanism,
not two.

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
through one `sync_error` helper that records, then logs *and* broadcasts — the
record first, because the close is what reveals whether there is still a run of
ours to speak for, and the log on the announcing side of that answer rather
than ahead of it. A dispossessed worker can reach an ordinary exception, and
announcing first tells every browser on this Machine that the run failed when
the run belongs to the replacement and may be going perfectly well; a terminal
sync event also sets the client's "an outcome was just published" flag, so the
false failure can swallow the replacement's real one. Only a definite refusal
silences the broadcast — an indeterminate close, or a run that never entered
the claim protocol, still speaks, because staying quiet about a real failure is
the worse error. The happy path records `complete` with both counts, and the
`except` records `error` with the message. A `finally` closes anything else as an error — its `UPDATE` is
`WHERE status = 'running'`, so it can be called unconditionally and cannot
overwrite an outcome a path already recorded.

**Amendment (2026-09-16, branch `claude/sleepy-dijkstra-dlsrbx`):** the log
line is new, and it is in the helper rather than at the call sites because
the call sites are where it was missing. Two of those three early returns end
a sync *before* it has reached Discogs at all, and neither wrote anything to
the log — so a user whose stored OAuth token had been refused saw a
Collection tab that simply never gained a record, and the entire log record of
each attempt was the "Collection sync started for X" line with nothing after
it. Nothing in the run row reaches the Logs tab, and nothing in this
document's cross-Machine story helps: the run row and the events both reported
the failure faithfully, to a banner, in a generic wording that named neither
the fault nor the fix. What the run row says is now also what the log says,
and for a Discogs error status both name the HTTP status — see
`crawl_manager._discogs_request_error`.

That placement is not cosmetic. A dispossessed worker reaching an ordinary
exception must not write "Collection sync failed for alice" to the Logs tab
either: the tab is shared rather than per-run, so the line would be the same
false report the suppressed broadcast exists to prevent, in the one place a
reader goes to check. What happened to that worker is still recorded — the
caller's own "this sync's run was taken over" warning says it.

The traceback is the one thing the two do not share, and it is withheld
exactly where the message was sanitized. `logging_config`'s queue handler
appends a formatted traceback to the stored message, and an
`HTTPStatusError`'s carries the full request URL and the response detail —
the two things `_discogs_request_error` exists to leave out. Attaching one to
a failure that path has already classified would write the original into
`app_logs` underneath the sentence written to omit it. An exception nothing
has classified keeps its traceback, because there it is the only account of
what happened.

Best effort throughout, on its own connection: failing to *narrate* a sync must
never be what ends one, and the connection the page loop was using may be in a
failed transaction by the time the error path runs.

### The two start paths share a lock

`start_sync` and `start_plex_match` refuse to overlap, and used to get that for
free: each checked both task maps and registered its own with no `await` in
between, which asyncio's single-threaded scheduling makes atomic. The claim
puts an await inside `start_sync`'s half of that, so a plex match starting
during the claim would see both maps idle, register itself, and have the
collection task created on top of it. Both now hold a lazily-created
`asyncio.Lock` across guard, claim and registration — the same shape
`start_stock_sync` already uses for its own guard-acquire-assign sequence.

Keyed by user, because that is the scope of the exclusion and because the
lock is held across a blocking database call. The claim can wait on the row
lock a checkpoint or cleanup transaction holds, and a single manager-wide
lock would spend that wait blocking every *other* account's sync and Plex
starts on this Machine — coupling users the rest of the manager keeps
independent, in a section whose own purpose is per-user. Building the entry
needs no lock of its own: there is no `await` between reading the dict and
writing it, so the event loop cannot interleave another start in between.

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
phase ends, on every exit including a cancelled one — and that release is
retried once if it cannot reach the row, because it is the only thing that
hands the claim back. Left unreleased, the row goes on saying `plex_matching`
for a phase that has already ended, which the claim predicate refuses on, so a
single connection blip would cost the user every refresh until the staleness
window expired. The Plex loop heartbeats
on its own commits for the same reason the sync's checkpoints do — a large
library takes time to match, and an unfed claim goes stale.

It carries the same wall-clock ceiling too, and the argument for it is
sharper here than in the sync. `find_best_match` scans the entire Plex album
list for every item, so what a chunk of items costs is set by the size of the
user's Plex library — not by anything this loop controls, and not by anything
visible at the point the cadence is chosen. A count is a proxy for elapsed
time only while the per-item cost is roughly known; here it is a property of
somebody else's media server. Bounding the gap in the unit the window is
actually expressed in is the only version that holds for every library size.

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

"Outside the claim" means *after the release*, not *regardless of how the
release answered*. A release refused is a takeover, and the paragraph above
applies unchanged: this worker restores nothing. The distinction is easy to
lose on the Plex path, because `_run_plex_match` handles its own `_ClaimLost`
and returns normally, so the release in the `finally` runs on the dispossessed
path exactly as on the healthy one and only its answer tells the two apart.
Restoring on a refusal would scan and enqueue against the very `library_items`
the replacement sync is rewriting — and the replacement runs its own
restoration when it ends, so nothing is lost by declining. A release that
could not be attempted at all is not evidence of a takeover, and still
restores.

Still open, and pre-existing: `start_plex_match`'s own guard remains
in-process only, so a Plex match started on another Machine can still overlap
a sync. Closing that means giving the Plex match a claim of its own, which is
its own piece of work; this change covers the direction it created.

### `GET /api/collection/status` carries the run

Rather than a new endpoint. The client already calls this one (it drives the
"Collection already loaded" modal), so no new function joins the API surface
that every App-rendering test has to double, and "the state of my collection"
is what this endpoint is already for. `sync` is `null` when the user has never
synced, and the field is **required** on the client (`CollectionSyncRun | null`)
rather than optional: the backend sends it on every reply, and "absent" must not
be allowed to read as "never synced". Those are the same shape to a client that
only checks falsiness, and different facts — the second is an answer, the first
is a reply that has lost a field. It was briefly optional here, to spare the
test doubles that predate it; that is the contract bent to fit the mocks, and
the doubles now say `sync: null` instead.

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

The follow is released by the poll's own terminal tick and by nothing else.
When this is the Machine running the sync, the stream reports the outcome
first and the poll must not repeat it over whatever has spoken since — the
Plex phase that follows a sync announces itself a beat later — so a terminal
SSE event records that *an* outcome has just been published, and the poll
skips its own line when it finds that flag set. It is deliberately not taken
as evidence that the followed run has ended: `_events_to_replay` replays the
whole retained buffer on reconnect, so the event may belong to an earlier sync
entirely, and dropping the follow on it would lose the refetch for the run
actually in flight — the original failure, by a new road. The poll clears the
flag the moment it sees the run still running, and refetches on its terminal
tick whether or not it says anything.

Only a run the loop has watched *running* may write an outcome to the status
bar. Without that rule, every page load would re-announce the last sync,
however old — the row is the most recent run, not a fresh event. A refresh this
tab just requested counts as watched, since the claim is taken by the request
itself.

A failed read is retried. A sync being followed is waited on indefinitely: it
is running, and its outcome is worth having. Everything else gets a bounded
number of tries — a refused start, which has said nothing yet and would
otherwise be back to looking like a no-op, and the mount-time read that
discovers a sync already under way, which since the effect stopped restarting
on revalidation has no second chance of its own. Out of tries, a refused start
reports the refusal; a discovery read has nothing to report and stops.

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
  announcing a completed sync, on the plain path as on the Plex one — and on
  the error path too, where failing is not the same as still owning the run.
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
  phase's own line;
- a stale terminal event replayed from another Machine's buffer does not cost
  the run in flight its refetch;
- a failed mount-time read is retried, so a tab that loads mid-sync still
  finds it.

Each was confirmed to fail against a build with the poll disabled.

## Amendments to other specs

- [`2026-08-16-fly-multi-machine-design.md`](2026-08-16-fly-multi-machine-design.md)
  — its "Cross-Machine SSE fan-out" non-goal now has an exception.
- [`2026-06-27-discogs-browser-design.md`](../../superpowers/specs/2026-06-27-discogs-browser-design.md)
  — the Refresh Collection flow.
- [`2026-07-04-wishlist-design.md`](../../superpowers/specs/2026-07-04-wishlist-design.md)
  — `/collection/status`'s response, and a pre-existing drift in what it counts.
