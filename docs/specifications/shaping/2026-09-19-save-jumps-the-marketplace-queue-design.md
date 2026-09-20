# Saving an item jumps it to the front of the marketplace queue

Date: 2026-09-19
Branch: `claude/kind-darwin-gpg70g`

## Problem

Saving a Store item is the one moment a user is asking a question about that
one record: what do the marketplaces want for it? Today the save answers that
question only by accident.

`PUT /api/stock/saved/{item_key}` writes `stock_item_saves` and calls
`enqueue_crawl_queue_for_saved_stock_item`, which is insert-if-absent. So the
save changes the crawl queue in exactly one case — the item had no row at all,
which under `crawl_library_only` means the switch-on sweep deleted it while
nobody wanted it. In every other case:

- The row is `done`. The save does nothing. The item's marketplace prices are
  whatever the last sync found, and nothing re-prices it until the next stock
  sync revives it with the rest of the catalog.
- The row is `pending`. The save does nothing, and the row keeps its place in
  a queue ordered `(item_key IS NOT NULL), requested_at, id` — behind every
  pending release row, and behind every stock row enqueued before it. After a
  stock sync that is the sources' whole combined inventory ahead of it.

Even the one case the save does change is only nominally an answer: the
inserted row goes to the back of that same queue. "Saved, and the prices will
be along" is true at the scale of the next full drain, not of a click.

## Scope

A save puts the item's marketplace crawl at the front of the queue, and
re-prices an item that was already priced.

`crawl_queue` grows a `priority` column, higher first, read by
`claim_crawl_queue_batch`'s sort ahead of every existing key. Two values are
in use: `0`, every routine enqueue, and `QUEUE_PRIORITY_INTERACTIVE`, set by
the save path alone. The save helper stops being insert-if-absent and becomes
insert-or-expedite.

Touches:

- `backend/db.py` — the `priority` column and the claimable index rebuilt
  around it; `QUEUE_PRIORITY_INTERACTIVE`; `enqueue_crawl_queue_for_saved_stock_item`
  rewritten; the sort in `claim_crawl_queue_batch` and `queue_next_for_crawler`;
  a `priority = 0` reset on the two routine revives; an `expedited` flag in
  `_queue_row_state_sql`, counted by `_queue_totals` and read by
  `_queue_crawler_eta`.
- Tests in `backend/tests/test_crawl_queue.py`,
  `backend/tests/test_stock_router.py` and
  `backend/tests/test_queue_router.py`.

Deliberately untouched: `enqueue_crawl_queue` (release targets), which cannot
reach a prioritised row — priority is only ever set on an `item_key` row and
the two key spaces are disjoint. The Queue tab's counts and ages, which
measure the queue rather than order it. The frontend, which already refetches
stock rows on the `listing_changed` the crawl broadcasts, so a price that
arrives seconds after a save lands on screen by itself.

The Queue tab's per-crawler **ETA** is not in that list, and the difference is
worth stating because it looks like a reporting detail and is a correctness
one. `_queue_crawler_eta` does not merely count the queue — it derives a
crawler's *position* in it from the claim's sort, on the stated guarantee that
every claimable release row precedes every claimable stock row. This change
makes that false for an expedited row, so a release-only crawler's ETA would
come out short by exactly the rows most likely to have just arrived. It takes
the new count.

That count is named `_claimable_expedited_stock_rows` and popped before the
summary is returned, following the per-crawler bucket's own
`_claimable_stock_units` — which is the same quantity one scope down.
`queue_summary` hands back its `totals` dict verbatim, so a key left in it is
`GET /api/queue/summary`'s public shape, declared by `QueueTotals` in the
frontend's `types.ts`. An ETA intermediate is not that, and the endpoint test
asserts no underscored key survives rather than naming this one, so the next
such value is caught without anyone remembering this paragraph.

## Design

### Priority leads the sort, ahead of the release/stock split

```sql
ORDER BY priority DESC, (item_key IS NOT NULL), requested_at, id
```

Ahead of `(item_key IS NOT NULL)`, not behind it, and that is the whole point
rather than a detail. That key exists so that a stock sync's enqueue burst
cannot delay a user's own collection crawl behind it — bulk work must never
outrank a person. A save is the same rule pointed at the same problem from the
other side: one row, one click, a user waiting on the answer. Sorting it
behind the release lane would mean a save was answered only once the
collection crawl ahead of it had drained, which is the wait this change
exists to remove.

What it costs is that one saved item delays one collection row by one row.
The lane is bounded by clicks rather than by anything automated: there is no
path by which a sync, a schedule or a crawler toggle puts a row into it.

It is **not** bounded per tenant, though, and it would be wrong to read it as
"their own queue in both directions". `crawl_queue` is global and ownerless —
a row names a target, never a user — so one person's saves sort ahead of
everyone's routine work, not just their own. What keeps that acceptable is
not a quota, which this queue has never had: release rows already outrank
every other user's stock rows today, with no fairness mechanism anywhere in
the claim, so the lane extends an existing cross-tenant property by one rank
rather than introducing one. Starving the queue means clicking save faster
than the pool drains, indefinitely, by hand.

If that ever stops being theoretical, the fix is bounded scheduling —
reserving part of each claim for routine rows — and it is deliberately not
built here. It would be the first fairness mechanism in this queue, and
`QUEUE_CLAIM_BATCH_SIZE` is 2, so any reservation is a coarse split that
halves the expedite it is protecting. Sizing that is a decision about the
whole queue, not about the save button.

That sort decides *which* rows a batch takes. A second, identical one decides
the order the caller gets them back in, and conflating the two is how the
guarantee quietly fails to hold: `UPDATE ... RETURNING` is under no obligation
to return rows in the order its subquery selected them, and
`_process_claimed_rows` walks that list sequentially. Without a final ordered
`SELECT` over the claimed rows, an expedited row could be crawled *after* a
routine one claimed beside it, and two saves could run out of FIFO order —
within one batch, so bounded by `QUEUE_CLAIM_BATCH_SIZE`, and silently worse
if that constant ever rises. What the lane promises is about when work runs,
which makes the returned order part of the guarantee rather than a
presentational detail.

`crawl_queue_claimable_idx` is replaced by `crawl_queue_priority_claimable_idx`
with `priority DESC` leading, matching the new sort. A new name, not an edited
definition: `CREATE INDEX IF NOT EXISTS` under an unchanged name is a no-op
against a database that already has the old one, which is the trap the index
it replaces already documents.

`queue_next_for_crawler` takes the same new sort. That query's contract is that
it shows the next targets *in `claim_crawl_queue_batch`'s own sort order*, so
leaving it on the old sort would make the Queue tab's list disagree with what
the worker actually takes next — and disagree exactly about the rows a user is
watching for.

### The save is insert-or-expedite, and what it does depends on the state

Five outcomes, chosen by the row's status — the `pending` one splits by
whether the row is already in the lane:

- **No row** — insert one, at `QUEUE_PRIORITY_INTERACTIVE`, behind the
  unchanged enabled-store gate.
- **`done`** — revive it: `pending`, priority set, `available_at` and
  `pending_crawler_ids` reset the way every other revive resets them. A
  re-crawl means "price this against everything eligible", not "resume some
  earlier pass's narrowed set".
- **`pending`, routine** — raise the priority, widen it back to every
  eligible crawler, and stamp `requested_at`: joining the lane is when its
  place in the lane starts.
- **`pending`, already expedited and already widened** — nothing at all. See
  `requested_at` below; this is the case that makes a repeated save a true
  no-op. (A row expedited and *then* narrowed by a backfill is widened again,
  as any `pending` row is.)
- **`in_progress`** — raise the priority and leave everything else alone,
  in a second statement.

The `pending` case is the common one and the reason a revive alone would not
have been enough. With `crawl_library_only` off, every live stock item already
has a row, so a save that only revived `done` rows would leave the ordinary
backlog case — a row pending since the last sync, tens of thousands deep —
exactly as slow as before.

`available_at` is therefore set conditionally rather than unconditionally,
and only it. On a `pending` row a future `available_at` is live state: some
crawler's site is in circuit-breaker cooldown, and clearing it would send a
worker straight back at a site that is failing. That column is about *when*
the row runs, which a save has no business overriding — the row is first in
line the moment it is claimable, and not a moment before.

`pending_crawler_ids` is cleared in every branch, and that uniformity is
load-bearing rather than tidy. A save means "price this against everything
eligible", and that has to hold however the row came to be narrowed. Reading
a narrowed `pending` row as a partial pass whose other crawlers just ran is
only *sometimes* true: there are two writers of that column and they mean
different things.

- `defer_crawl_queue_row` narrows to the crawlers a pass could not reach, and
  the rest did run — minutes ago, in the pass being deferred.
- `backfill_crawl_queue_for_crawler` revives a **`done`** target as `pending`
  with `ARRAY[the newly enabled crawler]`, because the rest already have
  prices. Those prices are as old as the last full pass.

Nothing on the row distinguishes the two, and the second is not a corner: its
window is every row that backfill revived, from an admin enabling a crawler
until the queue drains them, which at the sizes this queue reaches is not
minutes. Preserving the set there would answer the click by refreshing one
marketplace and leaving every other price on the comparison stale — the exact
promise the `done` branch keeps and this one would quietly break.

So the save widens unconditionally. The cost is re-running crawlers that did
finish earlier in an in-flight pass's cycle: bounded, paid only when somebody
clicks save, and precisely what they are asking for.

**One state is outside that rule, and it is a known gap rather than an
oversight.** A save on an `in_progress` row touches only `priority`, because
the worker holding it has already claimed its own snapshot of
`pending_crawler_ids` and resolves its crawler set from that. If the in-flight
pass was itself narrowed *and it completes*, `mark_crawl_queue_done` marks the
row `done` having refreshed only that narrowed set, and the save is consumed
by a partial pass. Widening the row here would not help: the worker is not
reading it any more.

Closing it needs a follow-up-request signal that the terminal write consumes —
a new column, and a `mark_crawl_queue_done` that sometimes hands the row back
instead of completing it, on the one path in this file where taking the wrong
branch loses a crawl result and which interacts with `defer_crawl_queue_row`
as the other terminal write. That is a larger change than the gap warrants
today: it needs the item to be mid-crawl *and* narrowed at the instant of the
click, against a backlog drain, where the earlier `pending` version of this
bug was open from an admin enabling a crawler until the queue emptied. Recorded
here so the next person to touch this weighs it deliberately rather than
rediscovering it.

The `in_progress` case looks safe to skip and is not. A worker holding the row
is already crawling what the user asked for — but only if that pass finishes.
`defer_crawl_queue_row`, `revert_crawl_queue_claim` and
`reclaim_stranded_crawl_queue_rows` all hand the row back as `pending` without
touching `priority`, by the rule above, so a save that recorded nothing would
leave the remaining work — on a revert or a reclaim, *all* of it — in the
ordinary backlog with nothing to show the user ever asked. Stamping the
priority costs the worker nothing: `mark_crawl_queue_done` and
`defer_crawl_queue_row` match on `id` and `claimed_by`, and neither reads this
column. `requested_at` is deliberately not stamped with it — the row's age is
what the Queue tab reports, and a claim in flight has not just been requested.

It is a second statement rather than a sixth `CASE` in the first, because what
this row needs is the *opposite* of what the others do: every column left
exactly as its worker set it, and `priority` alone moved. Spelling that as
"keep the current value" six times reads as though the writer had a choice
about each. The two statements are mutually exclusive — a row is in one state
— so at most one of them ever reports a row, and the returned count still
means "the item is queued because of this call". Both carry the enabled-store
gate; the second needs it written on it rather than inheriting one.

The other columns need no branch. `claimed_by`, `claimed_at` and `completed_at`
are already NULL on a pending row (every path back to `pending` nulls them), so
writing NULL is a no-op there and a revive on a `done` row.

`requested_at` is stamped by the insert, by a `done` revive, and when a
routine `pending` row is promoted — the three cases where something actually
changes. Under the new sort it only breaks ties within the priority lane,
where FIFO across saves is what you want: the first item saved is the first
priced, and a row joining the lane starts its position there now.

It is left alone in the two cases where nothing changes, and both matter:

- **A row already pending in the lane.** A repeated save — a retry, a second
  tab, an unsave and re-save — has nothing to add. Bumping would move the
  earlier click *behind* saves made after it, reversing the FIFO above, and
  restart the age the Queue tab reports. The endpoint is a `PUT`; with this
  branch a repeat is a true no-op on every column rather than nearly one.
- **A row mid-claim.** Its age is already being reported, and a claim in
  flight has not just been requested.

Both are the rule `defer_crawl_queue_row` and `reclaim_stranded_crawl_queue_rows`
already follow, applied to a save instead of a hand-back: a row is not sent to
the back of its lane for something that is not a new request.

### Reviving on save reverses an earlier decision, deliberately

[`2026-09-05-library-only-marketplace-crawl-design.md`](2026-09-05-library-only-marketplace-crawl-design.md)
chose insert-if-absent for this helper, on the grounds that "a `done` row is
the record that the item was already priced, reviving it would make every save
a marketplace re-crawl, and the next stock sync revives it with everything
else."

Both halves of that still describe what happens; what has changed is that
neither is a reason to refuse. The re-crawl a save now costs is one target's
fan-out, requested by a click, and it is the thing the click is *for* — a
price found during a sync days ago is not the comparison the user is asking
for. And "the next stock sync revives it with everything else" is the wait
this change exists to remove, not a substitute for removing it.

The helper's other rules are unchanged. It keeps the enabled-store gate
(`_enabled_stock_source_exists`), so saving a marketplace-sourced
`stock_items` row still queues nothing, and it still takes the
`STOCK_QUEUE_RECONCILE_LOCK_KEY` advisory lock so a concurrent dead-row sweep
cannot delete under a stale snapshot the row a save has just written.

Its return value changes meaning with it: it was "1 if a row was inserted",
it is now "1 if the item is queued as a result", which the `pending` and
`done` branches also satisfy. Nothing in production reads it — the endpoint
discards it — but the tests that assert on it say something different now and
are rewritten rather than renumbered.

### Routine revives put the row back in the slow lane

A row is prioritised because a user asked for it once, not because the item is
permanently special. So `priority` resets to `0` in exactly the two statements
that already reset `requested_at` — `enqueue_crawl_queue_for_stock_item`'s
`done` revive (a stock sync) and `backfill_crawl_queue_for_crawler`'s `done`
revive (a crawler being enabled). Both are bulk, scheduled or admin work with
nobody waiting on a specific row.

Tying the reset to "wherever `requested_at` is reset" is what keeps it
correct without a second rule to remember, because that is already the line
this table draws between a fresh request and a row being handed back.
`defer_crawl_queue_row`, `reclaim_stranded_crawl_queue_rows` and
`revert_crawl_queue_claim` all leave `requested_at` alone precisely because
they are not new requests, and they leave `priority` alone for the same
reason: a saved item's row that was deferred behind a cooldown, stranded by a
dead Machine, or handed back at shutdown is still the row a user is waiting
on, and must come back to the front.

`backfill_crawl_queue_for_crawler`'s second statement — the widen, which
appends the newly enabled crawler to already-`pending` narrowed rows — does
not touch `priority`, because it does not touch `requested_at` either. Same
rule, same reason.

### Rolling deploys

An old binary goes on writing `crawl_queue` after a new one has added the
column. Its inserts omit `priority` and take the default; its `ON CONFLICT DO
UPDATE` names a column list that does not include `priority`, so a revive it
performs *preserves* the value instead of resetting it.

The failure that buys is bounded and benign: a saved item's `done` row revived
by an old binary stays at `QUEUE_PRIORITY_INTERACTIVE` for one extra pass, so
one routine re-crawl runs early, and the next revive by a new binary corrects
it.

The reverse — a row that should be expedited not being crawled first — **is**
reachable, on the read side rather than the write side. An old worker's
`claim_crawl_queue_batch` does not know the column, so it claims by the old
sort and ignores the lane entirely: a save served by a new instance is
correctly recorded and then picked up in ordinary FIFO order by whichever old
worker claims it. Writing it up as unreachable was a mistake of scope —
"the save path is the only writer of a non-zero priority" answers who can
*set* it, not who has to *honour* it.

Nothing is lost when it happens, and nothing needs doing about it: the row is
already `pending` with the right priority stored, so the first new worker to
claim honours it, and until then the item is crawled no later than it would
have been without this feature. The window is one rolling deploy, and it is
the same window in which half the workers are running the old code for every
other reason too.

This is why the column is `NOT NULL DEFAULT 0` rather than nullable: there is
no state to distinguish, so there is nothing for a reader to get wrong, and
`ADD COLUMN ... NOT NULL DEFAULT` is a metadata-only operation on the versions
this deploys against.

## Risks

- **The priority lane is not itself paced.** Saves are claimed ahead of
  everything, so a burst of them is a burst of marketplace page loads at the
  front of the queue. The per-search pacing (`crawl_delay_seconds`) and the
  circuit breaker are both downstream of the claim and apply unchanged, so the
  lane changes what is crawled next, never how fast a site is hit.
- **A save cannot outrun an in-flight batch.** The worker claims
  `QUEUE_CLAIM_BATCH_SIZE` rows and runs every eligible crawler against each
  before claiming again, so a save lands at the front of the *next* claim, not
  the current one. "Immediately" means one batch, not zero.
- **Unsaving does not undo the priority.** The row stays prioritised until it
  is crawled. Deliberate: the crawl is already paid for by the time anyone
  could notice, and a de-prioritise path would need the same three-state care
  for no benefit.
- **`DO UPDATE` waits where `DO NOTHING` did not.** Updating on conflict locks
  the conflicting row, so a save can now block behind a stock sync's enqueue
  transaction — one source's `replace_stock_items` plus its whole enqueue
  loop — if that transaction has already touched this item's row. Bounded by
  that commit. It does not deadlock against the sync: the sync's enqueue never
  takes `STOCK_QUEUE_RECONCILE_LOCK_KEY`, so it is never waiting on anything
  the save holds, and the sync's end-of-run sweep, which does take that lock,
  takes it before touching a row — the same order the save does.

  **That was not true of the crawler-enable path**, and reasoning only about
  the sync is how it was missed. `routers/settings.py`'s enable runs
  `backfill_crawl_queue_for_crawler` and then
  `delete_dead_stock_crawl_queue_rows` *in one transaction*, so it held queue
  rows and then waited for the advisory lock — the exact opposite of the
  save's order, and a cycle Postgres may resolve by killing the save, taking
  the user's click and its `stock_item_saves` row with it. `register_crawler`'s
  conversion has the same shape. Reachable only since the save became an
  upsert: `ON CONFLICT DO NOTHING` took no row lock to wait on.

  Fixed by making the ordering a rule rather than a coincidence: **every taker
  of `STOCK_QUEUE_RECONCILE_LOCK_KEY` acquires it before its first
  `crawl_queue` row lock.** The backfill now takes it first, which is what
  extends the rule to both of its callers at once. It needs no serialization
  of its own — it is `FOR UPDATE SKIP LOCKED` throughout and never waits on a
  row — so the lock is there purely for the ordering. A holder can then only
  ever be waiting on rows, never on the lock, so the wait-for graph cannot
  close. Cheap, too: the lock is re-entrant, so the sweep's acquisition a
  moment later is a no-op, and it adds no unbounded wait that was not already
  on that path — the sweep waits for this lock unconditionally today, and this
  only moves the wait to before the row locks that made it dangerous.
- **A rolling deploy briefly leaves the old binary's sort unindexed**, between
  the new binary's schema init dropping `crawl_queue_claimable_idx` and that
  binary taking over. Not a regression in practice: the claim's gate predicate
  already forces a sort over the whole filtered set rather than an index walk
  terminated by `LIMIT`, which is the cost
  [`2026-08-10-dead-stock-crawl-jobs-design.md`](2026-08-10-dead-stock-crawl-jobs-design.md)
  documents the dead-row sweep as existing to bound.

## Testing

`backend/tests/test_crawl_queue.py`: a save inserts a missing row at the
interactive priority; revives a `done` row and prioritises it; raises a
`pending` row's priority and widens it back to every eligible crawler while
leaving its `available_at` hold intact; and stamps an `in_progress` row's
priority alone (below). The widening is pinned twice, once on a row narrowed
by hand and once on one narrowed by a real `backfill_crawl_queue_for_crawler`
run — the second asserts its own precondition, so it fails rather than passes
vacuously if the backfill ever stops narrowing.
The enabled-store gate is asserted on all three paths, because it guards the
expedite as well as the insert and neither of those is obvious. In the first
statement it lives in the `INSERT ... SELECT`, so a false gate yields no
source row, hence no conflict and no `DO UPDATE` — the revive is gated by
something that reads like an insert-only clause. In the second it is written
on the `UPDATE` itself, so it is gated only because it was remembered. A
`done` row is the one way an unstocked item still has a row to revive — the
dead-row sweep takes `pending` only — so that case is tested directly rather
than left to the no-row one.

The claim takes a prioritised stock row ahead of a pending release row, which
is the ordering guarantee stated above and the one an unchanged sort would
silently lose, and is FIFO within the lane.

Both of those assert on a **multi-row batch**, because the returned order is
the guarantee: a single-row claim would exercise the selection and never the
ordering. They are built so that no ordering by `id` satisfies both — one
expects the higher id first, the other the lower — so a final `SELECT` that
dropped the sort and fell back to insertion order fails one of them whichever
direction it fell. That is the shape a single test would have got wrong: the
FIFO case alone passes under `ORDER BY id DESC` by luck.

Routine revives — a stock sync's and a crawler enable's — put a prioritised
row back to `0`, while the enable's *widen* and a deferral both leave it
alone.

A save on an `in_progress` row stamps the priority and moves nothing else —
`claimed_at` and `requested_at` are asserted unchanged — and a deferral after
one leaves the row `pending` and still expedited, which is the whole reason
that case cannot be skipped.

`requested_at` is pinned from both directions, since one test alone is
satisfied by a statement that always does the same thing: a repeated save of
an already-expedited row leaves it untouched *and* leaves that row first in
the lane ahead of one saved in between, while a save that promotes a routine
`pending` row does advance it.

The lock ordering takes **two** tests, and the distinction is the whole
point. One proves the backfill takes the reconciliation lock at all, from the
lock side: while a save holds it, the backfill blocks. That one passes
whether the acquisition sits before or after the backfill's `UPDATE`s, since
it reaches the lock either way — so on its own it would have let the
dangerous ordering back in while reading as a deadlock regression test. The
second asserts the order directly: the acquisition is replaced with a raise,
and the row is read back on the same uncommitted transaction, where a revive
that had already run would be visible. Moving the call below the `UPDATE`s
fails it.

`backend/tests/test_queue_router.py`: `/next` puts an expedited stock row
ahead of a release row. The existing
`test_next_returns_claim_order_with_releases_before_stock` cannot cover this —
it uses routine rows only, so it passes with or without the priority key. The
release-only crawler's ETA counts the expedited stock rows ahead of it and
still excludes the routine ones behind it. And the summary endpoint returns no
underscored key, which is the guard on `totals` being public API.

`backend/tests/test_stock_router.py`: the endpoint queues an item whose row
was `done`, and repeated saves stay idempotent.
