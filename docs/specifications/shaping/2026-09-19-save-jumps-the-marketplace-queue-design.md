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

Four outcomes, chosen by the row's status:

- **No row** — insert one, at `QUEUE_PRIORITY_INTERACTIVE`, behind the
  unchanged enabled-store gate.
- **`done`** — revive it: `pending`, priority set, `available_at` and
  `pending_crawler_ids` reset the way every other revive resets them. A
  re-crawl means "price this against everything eligible", not "resume some
  earlier pass's narrowed set".
- **`pending`** — raise the priority and leave everything else alone.
- **`in_progress`** — raise the priority and leave everything else alone,
  in a second statement.

The `pending` case is the common one and the reason a revive alone would not
have been enough. With `crawl_library_only` off, every live stock item already
has a row, so a save that only revived `done` rows would leave the ordinary
backlog case — a row pending since the last sync, tens of thousands deep —
exactly as slow as before.

Two columns are therefore set conditionally rather than unconditionally.
`available_at` and `pending_crawler_ids` are reset only on the `done` branch.
On a `pending` row both are live state: `available_at` in the future means
some crawler's site is in circuit-breaker cooldown and `pending_crawler_ids`
names the work that pass deferred. Clearing them would send a worker straight
back at a site that is failing, and re-run crawlers that had already finished
for that target. Priority alone is the right lever there — the row is first in
line the moment it is claimable, and not a moment before.

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

`requested_at` is bumped in both branches *of this statement* — the insert and
both arms of its `DO UPDATE`. Under the new sort it only breaks ties within
the priority lane, where FIFO across saves is what you want: the first item
saved is the first priced. The `in_progress` statement above is the one place
it is deliberately left alone, for a reason that does not apply here: that row
is mid-claim, and its age is something the Queue tab is already reporting.

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

The failure that buys is bounded and benign in the one direction that matters:
a saved item's `done` row revived by an old binary stays at
`QUEUE_PRIORITY_INTERACTIVE` for one extra pass, so one routine re-crawl runs
early. The reverse — a row that should be expedited being left behind — is
unreachable, because the save path is the only writer of a non-zero priority
and a save served by an old binary simply does what it did before. The next
revive by a new binary corrects it.

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
  that commit, and it cannot deadlock: the sync's enqueue never takes
  `STOCK_QUEUE_RECONCILE_LOCK_KEY`, so it is never waiting on something the
  save holds, and the sync's end-of-run sweep, which does take that lock,
  takes it before touching a row — the same order the save does.
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
`pending` row's priority while leaving its `available_at` hold and its
narrowed `pending_crawler_ids` intact; and stamps an `in_progress` row's
priority alone (below).
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
silently lose, and is FIFO within the lane. That FIFO test claims one row at a
time: the sort decides which rows a claim takes, and `RETURNING` is under no
obligation to hand even those back in the sort's order — asserting on the
order of a two-row batch passes or fails by luck.

Routine revives — a stock sync's and a crawler enable's — put a prioritised
row back to `0`, while the enable's *widen* and a deferral both leave it
alone.

A save on an `in_progress` row stamps the priority and moves nothing else —
`claimed_at` and `requested_at` are asserted unchanged — and a deferral after
one leaves the row `pending` and still expedited, which is the whole reason
that case cannot be skipped.

`backend/tests/test_queue_router.py`: `/next` puts an expedited stock row
ahead of a release row. The existing
`test_next_returns_claim_order_with_releases_before_stock` cannot cover this —
it uses routine rows only, so it passes with or without the priority key. The
release-only crawler's ETA counts the expedited stock rows ahead of it and
still excludes the routine ones behind it.

`backend/tests/test_stock_router.py`: the endpoint queues an item whose row
was `done`, and repeated saves stay idempotent.
