# Stopping a recommendation run design

Date: 2026-09-16
Branch: `claude/lucid-maxwell-69049q`

**Amendment (2026-09-16, branch `claude/confident-goldberg-e0hrew`):** nothing
covered the lock itself. Deleting the `db.lock_stock_judgment_run` call out of
`_judgment_running` left the whole backend suite green, because every test
reaching those guards was satisfied by the row read alone — the half of the
mechanism that answers *is a run under way*, never the half that keeps the
answer true until the write commits. The cross-Machine half was real and
covered — a clear on a Machine running no judgment does refuse against a
claimed row — but the race the lock is there for, a claim landing in the
window between that read and the commit, went unasserted.
The Testing section below gains the cases that fail without the lock: it is
held across the write itself — the `DELETE`, and the import's upsert — it is
keyed per user, so one user's live run neither refuses another user's clear nor
is taken by it, and it is released on each of the three ways out of the
handler, committed, refused from inside the transaction, and unwound by an
exception. Behaviour is unchanged; this is the guarantee above being pinned
down rather than assumed.

## Problem

Reported as: *"In the profile page, when a user has set off a recommendation
crawl, provide an option to stop the recommendation run. Perhaps flip the
Refresh to Stop in the button when a user's recommendation is running. Then
when either the run is over or the run has been stopped, flip back from Stop to
Refresh."*

Account's **Refresh** under Recommendations posts `/api/stock/judge/start` and
that is the last say the user gets. The run then walks every unjudged Store
item in batches of `recommendations.BATCH_SIZE`, one Anthropic call each, on
the user's own API key, up to their `recommendation_item_limit` — 300 by
default, which is several minutes and a bill. Nothing can call it off. A
mistaken click, a run started against the wrong taste listing, a key the user
wants to stop spending on: all of them run to completion.

The button also cannot tell the user a run is under way. It reads Refresh
whether the run is idle, half done, or has been going for five minutes, so the
natural reaction to a UI that looks inert — click it again — is met with a
silent refusal from `CrawlManager.start_judgment_only`.

Both halves need the same missing thing, and it is not an endpoint. **Nothing
outside the one Python process knows a judgment run exists.**
`CrawlManager._judgment_tasks` is a dict of `asyncio.Task`s in the memory of
whichever Machine served the POST, and the `stock_judgment_*` events that
narrate a run are fanned out to that Machine's own SSE subscribers. The
deployment runs two Machines behind one hostname with no affinity
(`backend/fly.toml`: `min_machines_running = 2`), so:

- a browser whose `/api/crawl/stream` landed on the other Machine hears no
  `stock_judgment_started`, no progress and no completion — the button could
  not flip on those events even if it wanted to;
- a `POST` asking to stop would cancel a task on the Machine that served it,
  which about half the time is not the Machine running the job;
- a second Refresh is refused by `judgment_running()`, which is per-process, so
  on the other Machine it starts a **second** concurrent run — a duplicate
  Anthropic bill for the same items.

This is the same gap
[`2026-09-13-collection-sync-run-visibility-design.md`](2026-09-13-collection-sync-run-visibility-design.md)
closed for the collection sync, and it is closed the same way: the run becomes a
row both Machines can read.

## Scope

Makes one recommendation run a row both Machines can read, gives that row a
stop flag the run itself honours, and has Account's button follow it.

Touches:

- `backend/db.py` — the `stock_judgment_runs` table, its RLS policy and grant,
  and the claim/progress/stop/finish/read helpers.
- `backend/crawl_manager.py` — `start_judgment_only` claims the run before
  creating the task; `_run_judgment_phase` advances it, checks the stop flag at
  every batch boundary, and closes it.
- `backend/routers/stock.py` — `POST /api/stock/judge/stop`; the run carried on
  `GET /api/stock/judge/status` and on the start response; the clear and import
  guards read the row rather than this process's task map.
- `frontend/src/App.tsx` — the run's state, a poll that follows it to its end,
  and the stop handler.
- `frontend/src/views/Account.tsx` — the button's three faces.
- `frontend/src/api/client.ts`, `frontend/src/api/types.ts` — the run's shape
  and the stop call.
- Tests: `backend/tests/test_judgment_crud.py`,
  `backend/tests/test_crawl_manager.py`, `backend/tests/test_stock_router.py`,
  `frontend/src/test/account.test.tsx`,
  `frontend/src/test/recommendationRunStop.test.tsx`.

Out of scope: cross-Machine SSE fan-out in general. The stock sync's and the
crawl worker's narration still only reach the Machine producing them, and the
`LISTEN`/`NOTIFY` bridge that would fix all of it at once remains the larger,
separate piece of work. What this change needs from that gap is narrower —
"is a run under way, and please stop it" — and a row answers both without
moving any events.

## Design

### The stop is cooperative, and that is not a compromise

The obvious implementation of Stop is `task.cancel()`. It is the wrong one
here, for two independent reasons.

**It cannot reach the batch.** `_run_judgment_phase` spends essentially all of
its time inside `await asyncio.to_thread(recommendations.judge_batch, ...)`.
Cancelling that `await` raises `CancelledError` in the coroutine but does not
touch the worker thread, which goes on to finish its `client.messages.create`
call regardless. The tokens are spent either way.

**It would throw the answer away.** Cancelling mid-batch abandons a response
the user has already paid for, leaving those items unjudged so the next run
pays for them a second time. That is the opposite of what someone clicking Stop
wants.

So a stop is a flag the run reads at every batch boundary. The batch in flight
finishes, its judgments are committed, and the run closes before spending
anything more. The cost is latency — one batch, bounded by a single
`claude-haiku-4-5` call over at most `BATCH_SIZE` items — and the UI is honest
about it: the button reads **Stopping…** from the click until the run actually
ends, rather than claiming an instant stop it cannot deliver.

The flag is also checked once before the first batch, which is free: the same
statement records the run's total.

### `stock_judgment_runs`

One row per user, in the tenant schema beside `stock_item_judgments`, RLS-scoped
on `user_id` like every other per-user table:

```sql
CREATE TABLE IF NOT EXISTS stock_judgment_runs (
    user_id INTEGER PRIMARY KEY REFERENCES users(id),
    status TEXT NOT NULL,              -- 'running' | 'complete'
                                       -- | 'stopped' | 'error'
    judged INTEGER NOT NULL DEFAULT 0,
    total INTEGER,
    error TEXT,
    stop_requested BOOLEAN NOT NULL DEFAULT FALSE,
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    heartbeat_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,
    run_token TEXT
);
```

Rewritten by each run rather than appended to, for the reasons
`library_sync_runs` is: nothing here is history — the question is "what is my
recommendation run doing *now*, and how did the last one end" — and the claim
needs a single conflicting key to be atomic.

`app_user` gets `SELECT, INSERT, UPDATE` and deliberately not `DELETE`: a row is
claimed, advanced and closed in place, and the next run overwrites it.

`stopped` is a status of its own rather than a flag on `complete`. A run that
stopped early and a run that judged everything differ in the one way the user
cares about — whether there are items left to judge — and collapsing them would
make the banner lie about which happened.

### The claim replaces the per-process guard

`start_judgment_only` claims the row in Postgres before creating the task,
through `run_in_threadpool` because psycopg's calls block, and the row decides.
The `judgment_running()` check that used to guard it is not kept in front as a
cheap early-out, for the reason `start_sync` does not keep its own: that
predicate answers "is this task object still pending", which a worker wedged in
a blocking call says for ever, so refusing on it made this Machine the one place
a run it had itself abandoned could never be recovered — while the other Machine
read no such thing and started a duplicate. A healthy local run is refused by
the claim just as firmly, because its own heartbeat keeps the row fresh.

```sql
INSERT INTO stock_judgment_runs (user_id, status, run_token)
VALUES (..., 'running', ...)
ON CONFLICT (user_id) DO UPDATE SET status = 'running', stop_requested = FALSE, ...
WHERE stock_judgment_runs.status <> 'running' OR <heartbeat is stale>
RETURNING run_token
```

`stop_requested = FALSE` on the claim matters: without it a stop flag left on a
row by the run that just honoured it would stop the *next* run at its first
checkpoint, before it judged anything.

When the claim is granted and this Machine still holds a task for that user,
that task is working a run that is no longer its own — and it is left alone to
find that out at its next checkpoint, deliberately unlike `start_sync`, which
cancels its predecessor outright.

The difference is what the two workers do next. A dispossessed sync worker goes
on to destructive wantlist cleanup driven by a stale snapshot, so stopping it
late is not good enough. A dispossessed judgment worker's only write is an
idempotent upsert of judgments already paid for. Cancelling it cannot stop the
Anthropic call it is inside, for the same reason a stop cannot (below), so the
money is spent either way; all the cancellation achieves is discarding the
answer before it can be committed, leaving those items for the replacement to
buy a second time. Letting it finish the batch and stop at the checkpoint keeps
what the user has already paid for.

`judgment_running()` itself stays, and stays per-process: `_events_to_replay`
uses it to decide whether *this* Machine's replay buffer has anything worth
sending, which is a question about this process and nothing else.

### Every write names the claim it belongs to

Like the sync's, each later write to the row carries the `run_token` the claim
returned, and is refused without it. Staleness is judged on a heartbeat, not on
proof of death, so a worker slow enough to be taken over is still alive, still
committing judgments and still able to write — and its heartbeat is the worse
half, because it would hold the new claim open in the old worker's name. The
`finally` backstop is the other hazard the token closes: a fresh claim landing
between a run's own close and its backstop would otherwise be marked as failed
by its predecessor, seconds after starting.

`record_stock_judgment_progress` returns `None` when the row is no longer this
caller's, and otherwise the row's `stop_requested`. Both answers end the run,
and they are one statement because they are asked at the same instant, at every
batch boundary — the ownership question for the same reason the sync asks it
(a dispossessed worker must stop spending the user's money alongside its
replacement, not just stop writing bookkeeping), the stop flag because that is
the whole feature.

Judgments already committed are never rolled back on either answer. The batch
still *in hand* when the checkpoint reports the claim gone is a harder call,
and it is dropped — which reverses an earlier version of this design, for a
reason worth writing down.

The argument for keeping it is that a judgment write is an idempotent upsert of
something already paid for, so discarding it only means paying again. The
argument that beats it is that clearing and importing judgments guard on this
run, and the lock below orders a batch against a clear but cannot order it
against one that already finished: a clear runs to completion while the worker
sits inside `judge_batch`, long before that batch's transaction opens. A batch
written anyway would quietly repopulate rows the user asked to clear, or
overwrite verdicts they had just imported — so those guards would be promising
an exclusion they do not deliver.

What dropping it costs is bounded and mostly notional. A run that was taken
over has a replacement which already selected these same still-unjudged items,
so it is paying for them either way and nothing extra is lost. Only a run that
went stale with *no* replacement loses anything real, and then only one batch,
and only if a single Anthropic call outlasted `JUDGMENT_RUN_STALE_MINUTES`.

The checkpoint goes **first** within that transaction, though, ahead of the
upsert, and takes a per-user advisory lock (`pg_advisory_xact_lock`) as its own
first statement. Holding that lock until the batch commits is what serialises
the judgment write against the two things that decide whether they may touch
`stock_item_judgments` — clearing and importing, which take the same lock
before their own write. Read without it, "is a run under way" is a check the
answer outlives: a start can claim between reading *idle* and deleting, and a
live run's next checkpoint can commit between reading *idle* and importing, in
both cases letting exactly the interleaving those guards exist to prevent
happen anyway, a moment later.

An advisory lock rather than `SELECT … FOR UPDATE` on the run row, because the
row is the one thing that need not exist. Before a user's first run there is
nothing to lock, so a row lock degrades silently to no lock at all — in exactly
the case where a first Refresh races a first import. Every holder takes the
lock before touching the row, so the lock order is the same everywhere and
there is nothing to deadlock against.

What the lock does *not* do is order a batch against a clear that has already
finished — it excludes concurrent transactions, and a clear that ran while the
worker was inside `judge_batch` was never concurrent with the batch's
transaction at all. That gap is closed by dropping a batch whose claim is gone,
above, not by the lock.

A run that finds it has been taken over stops **silently**: no terminal event,
and no progress line either. The row and the narration both belong to the
replacement now, so a progress broadcast for the batch this worker happened to
finish would show every browser on this Machine a count from a run that is no
longer the one running.

### Staleness, because a claim that cannot expire is a trap

A Machine that restarts mid-run leaves its row saying `running` for ever.
Untreated, that row would refuse every later Refresh for that user and pin the
button on **Stop** with nothing behind it — a worse bug than the one being
fixed. So the run heartbeats at every batch boundary, and a claim whose
heartbeat has lapsed can be taken over: `JUDGMENT_RUN_STALE_MINUTES = 15`.

Fifteen minutes is far outside what a judgment run can go quiet for — a
checkpoint lands after every batch, and a batch is one `claude-haiku-4-5` call
capped at `MAX_TOKENS` output — and far inside "a human clicked the button
again". The same window as the sync's, for the same reason, and the two have no
interaction: they are different rows and different claims.

Staleness is fenced into every writer, not just the claim, which is what makes
expiry irreversible. A run that went quiet past the window is out of the
protocol whether or not anyone has taken it over yet, so it cannot come back
and revive its row after the client has been told it ended.

### `running` is computed, never read off `status`

`get_stock_judgment_run` returns `status` and a computed `running`
(`status = 'running' AND NOT stale`), plus `stale` itself so a UI can say what
happened rather than silently going idle. An abandoned run says `running` in
its status column for ever; a client that believed it would show **Stop** on a
run nothing is doing, and clicking it would flag a row no worker will ever
read.

### Reading a run is not allowed to follow a commit

`user_scope` establishes the RLS scope with `set_config('app.user_id', …,
true)` — **transaction-local**. A commit inside the `with` block therefore
drops it, and the next query on that connection evaluates a policy that casts
an empty string to `int`, which raises. So the stop endpoint reads its run
inside the same transaction as the write, before the commit, where it also
sees that write.

This deserves its own heading because the *ordinary* tests cannot catch it.
Every router test reaches Postgres as the superuser, which has `BYPASSRLS`, so
the policy expression is never evaluated and the broken order passes every
assertion while answering `500` to every successful stop in production.
`test_stock_router.py` therefore grows an `rls_enforced` fixture that repoints
the app pool at `app_user` after the schema is built, and the stop endpoint is
exercised through it — so the gap is closed, but only by a fixture that has to
be asked for deliberately. Confirmed by reverting the order: that test fails,
and nothing else does.

### The API

- `GET /api/stock/judge/status` keeps `any_judged` and gains `run` — the same
  field set the collection status exposes, minus the sync's own vocabulary.
  This is what the button reads at mount, so a reload during a run comes back
  showing **Stop**.
- `POST /api/stock/judge/start` answers `{started, running, run}`. `running` is
  now read from the row, so a refused start is refused because a run is
  genuinely under way somewhere, and the reply carries that run — the button
  flips to **Stop** on the response alone, without waiting for an SSE event
  that may be going to another Machine.
- `POST /api/stock/judge/stop` sets `stop_requested` on a live running row and
  answers `{stopping, run}`. `stopping` is false when there was no live run to
  flag, which the client reports as the run having already ended rather than as
  a failure. It is deliberately not a 409: there is nothing wrong with asking a
  finished run to stop, and the answer the user needs is the state, not an
  error.
- `POST /api/stock/judge/clear` and the CSV import guard on the row rather than
  on `crawl_manager.judgment_running`. Both are racing the judgment run's
  writes to `stock_item_judgments`, and that race is with whichever Machine is
  running it — the per-process predicate was answering a different question and
  getting it right by luck. The row also expires, so a wedged worker cannot
  block a clear for ever.

### The button's three faces

`Refresh` when nothing is running, `Stop` while a run is under way, `Stopping…`
between the stop click and the run's actual end. The third is not decoration:
the batch in flight has to finish first (see above), and a button that snapped
straight back to Refresh would invite a second start against a run that is
still going, which the claim would then refuse — the silent-refusal failure
wearing a different hat.

The button keeps `min-w-20` rather than the `w-20` the neighbouring
Export/Import/Clear buttons carry, so it still lines up with them at rest and
grows for **Stopping…** instead of clipping it.

### Following the run without SSE

`stock_judgment_started` / `progress` / `complete` / `error` are unchanged, and
a new `stock_judgment_stopped` carries the count a stopped run reached. On the
Machine serving both the run and the browser's stream, those still drive the
banner exactly as before.

They cannot drive the *button*, because half the time they do not arrive. So
App polls `GET /api/stock/judge/status` while it believes a run is in flight,
seeded by the mount-time read and by the start response, and stops polling the
moment a read says otherwise. Every transition the button makes comes from that
poll or from a response to a click; the SSE handlers update the same state when
they do arrive, which on the co-located Machine simply makes the flip
immediate.

Three things follow from an event carrying no run identity, all of them cases
where the event is right about *a* run and wrong about the current one:

- **A `started` event never clears an optimistic stop.** It can be queued
  before a Stop click and delivered after it, and clearing the flag there turns
  the disabled **Stopping…** back into **Stop** over a row whose flag is set,
  inviting a second click that does nothing. Only the row clears it.
- **A terminal event is confirmed against the row.** `complete`/`stopped`/
  `error` clear the flags — right nearly always, and wrong exactly when a newer
  run has started since, where it would take Stop away from a run still
  spending *and* tear down the poll that would have noticed. So one read
  follows, which restores the flags if a run is in fact still going.
- **A superseded status read is not an answer.** The poll decides whether to
  stop from the read it just made, so a reply about an older run arriving after
  a newer start would end the follow for a run that is still going. A read
  whose sequence has been overtaken returns nothing at all rather than a
  stale verdict.

The poll also drives the Store's refresh generations from the counters it can
see. Those bumps live on the `stock_judgment_*` handlers, which is precisely
what does not arrive on the other Machine — so without this an already-open
Store tab on the Recommended filter never refetches the judgments the run is
writing. Gated on the run having actually moved (`status`/`judged` changing),
or it would refetch every few seconds for the length of a run, and skipped for
the first read, which is a page load rather than movement.

The banner is left to SSE alone. A run whose events reach this browser narrates
itself as it always did; one whose events go elsewhere says nothing, as it
always did — and crucially says nothing *consistently*, so there is no stale
progress line for the poll to have to clear.

## Testing

`backend/tests/test_judgment_crud.py`
- a claim is refused while a live run holds it, and granted once the run is
  closed;
- a claim is granted over a run whose heartbeat has lapsed past the window;
- a claim clears a stop flag left behind by the previous run;
- progress writes are refused with a foreign `run_token`, and refused after
  the window has lapsed;
- `request_stock_judgment_stop` flags a live run and reports nothing to stop
  for an idle or stale one;
- `get_stock_judgment_run` computes `running` false for a stale row.

`backend/tests/test_crawl_manager.py`
- a run whose stop flag is set between batches stops at the boundary, keeps the
  judgments it had already committed, and broadcasts `stock_judgment_stopped`
  with the count it reached;
- a stop flag set before the first batch stops the run without an Anthropic
  call;
- a run that loses its claim mid-flight stops without finishing the remaining
  batches, says nothing at all — progress included — and drops the batch it had
  in hand rather than writing it over a clear or an import that may have landed
  while it was in the Anthropic call;
- `start_judgment_only` leaves a stalled local task running rather than
  cancelling it when it takes its claim over;
- a completed run closes its row `complete`; a failed one closes it `error`
  with the message;
- `start_judgment_only` refuses when a live row holds the claim, including one
  this process never started.

`backend/tests/test_stock_router.py`
- `POST /stock/judge/stop` flags the calling user's run and cannot touch
  another user's;
- the stop endpoint answers under a pool authenticated as `app_user`, with the
  tenant policies actually enforced (`rls_enforced`) — the only way in this
  file to catch a read placed after a commit, since every other test bypasses
  RLS;
- the start response and the status endpoint carry the run;
- clear and import refuse against a live row;
- clear and import are still *holding* the per-user lock when their own write
  runs — the `DELETE` and the upsert — probed from a second pooled connection
  from inside each write, since an advisory lock is re-entrant within the
  session holding it and the handler's own connection would answer "free"
  however tightly it were held;
- the lock is keyed per user: one user's claimed run neither refuses another
  user's clear nor is held by the handler serving it, which is what separates
  the two-argument form from a one-argument `pg_advisory_xact_lock(key)` that
  would pass a response-only check while putting every user behind one lock;
- clear releases the lock on all three ways out — committed, refused from
  inside the transaction, and unwound by an exception — and is probed holding
  it on each, so "released" cannot be satisfied by never having taken it.

`frontend/src/test/recommendationRunStop.test.tsx`
- the button reads Refresh, Stop and Stopping… in the three states, driven by
  the status poll rather than by SSE;
- clicking Stop posts once and does not post a start;
- a run already under way at mount shows Stop without any event arriving;
- the button returns to Refresh when the poll reports the run ended, and when
  a `stock_judgment_stopped` event arrives;
- a `started` event delivered after a stop click leaves the button on
  Stopping…, asserted on that event's own flush with no poll allowed in
  between (a test that waited would pass against the bug);
- a terminal event the row contradicts restores Stop, asserted on the
  confirming read rather than on the label;
- the poll refetches the Store when it sees the run's count advance.

## Amendments to other specs

- [`2026-07-06-store-recommended-filter-design.md`](../../superpowers/specs/2026-07-06-store-recommended-filter-design.md)
  — the start guard, the `/stock/judge/*` shapes, and the button's wording.
- [`2026-07-18-profile-account-section-design.md`](../../superpowers/specs/2026-07-18-profile-account-section-design.md)
  — the Recommendations section's `Refresh` row, now three-faced.
- [`2026-08-09-recommendations-import-design.md`](2026-08-09-recommendations-import-design.md)
  — the import endpoint's busy guard reads the row, not the task map.
- [`2026-08-16-fly-multi-machine-design.md`](2026-08-16-fly-multi-machine-design.md)
  — a second exception to its cross-Machine SSE non-goal.
