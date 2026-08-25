# Stranded crawl_queue Row Reclaim — Implementation Plan

Design: [`docs/specifications/shaping/2026-08-25-stranded-crawl-queue-row-reclaim-design.md`](../shaping/2026-08-25-stranded-crawl-queue-row-reclaim-design.md)

## Global Constraints

- The reclaim threshold must be `db._queue_stranded_after_seconds`, called —
  not copied, not re-derived. If the Queue tab's Stranded tile and the reclaim
  ever disagree about what "stranded" means, the tile stops being usable as the
  instrument for judging whether the reclaim works.
- `pending_crawler_ids`, `available_at` and `requested_at` are not written by
  the reclaim.
- `routers/queue.py` gains no write path.
- No new table, no new column, no new scheduler, no new API field.
- Every test is confirmed to fail against unmodified source before the fix is
  written — by stashing the source change and re-running, not by inspection.

## File structure

```
backend/db.py                          # reclaim helper, next to revert_crawl_queue_claim
backend/crawl_manager.py               # call it from _drain_one_batch's _claim_batch
backend/routers/queue.py               # comment: the reclaim exists now, just not here
backend/tests/test_crawl_queue.py      # helper-level tests
backend/tests/test_crawl_manager.py    # end-to-end: _drain_one_batch claims a stranded row
frontend/src/views/QueueView.tsx       # units on tiles and legend
frontend/src/test/queueView.test.tsx   # rows-vs-units labelling
docs/specifications/shaping/2026-08-25-admin-queue-tab-design.md   # drift
```

### Task 1: `db.py` — `reclaim_stranded_crawl_queue_rows`

Add next to `revert_crawl_queue_claim`, whose column semantics it shares.
Cutoff from `_queue_stranded_after_seconds(conn, crawl_delay_seconds)`. Inner
`SELECT … FOR UPDATE SKIP LOCKED` so concurrent workers never wait on each
other. Returns `rowcount`.

Amend `claim_crawl_queue_batch`'s "Known gap, not an oversight" comment,
`count_pending_crawl_queue_for_user`'s, and `_queue_in_progress_units`'s: all
three assert there is no reclaim path and that a strand is permanent. After
this change a strand is bounded by the threshold, not permanent, and the
counting caveats change shape accordingly.

### Task 2: `crawl_manager.py` — call it from `_drain_one_batch`

`load_config()` before the app connection is borrowed; reclaim as the first
statement inside `_claim_batch`'s connection, before the claim, same
transaction. `_claim_batch` now returns `(rows, reclaimed)` — the
`CancelledError` branch reads the same task and must unpack it too, or the
revert path breaks on a tuple.

Log at warning level only when the count is non-zero.

Amend the comments that assert a claimed row has no reclaim path: `_shielded`'s
docstring, the cancellation-boundary comment in `_drain_one_batch`,
`_process_claimed_rows`' docstring, and the `batch_size` note. Each remains
*correct in its own scope* — cancellation still must not split a claimed row's
terminal write, and that is still why the shield exists — but each currently
justifies itself by "forever, with no reclaim path", which is no longer the
consequence.

### Task 3: Backend tests

`test_crawl_queue.py`: reclaim preserves `pending_crawler_ids` and the
subsequent claim returns it; a fresh claim is untouched; the cutoff moves with
`crawl_delay_seconds` and the enabled-crawler count; `requested_at` survives;
`pending`/`done` rows are untouched.

`test_crawl_manager.py`: `_drain_one_batch` claims a row stranded before it
ran. This is the test that must fail against unmodified source for a reason
other than a missing attribute. Alongside it, a guard that `_drain_one_batch`
leaves a freshly claimed row to its worker — that one passes before the fix,
because the merged code reclaims nothing; it is there to catch a later change
that loosens or drops the threshold.

### Task 4: `QueueView.tsx` labelling

Every row-denominated stat tile states `rows` in its hint. Every donut legend
entry states `units` beside its number. Labels only — no value or query
changes.

### Task 5: Frontend test

`queueView.test.tsx`: one snapshot with `in_progress_rows` and
`in_progress_units` deliberately different; assert the tile and the legend each
render their own value with its own unit.

### Task 6: Spec-drift check

Grep both `docs/superpowers/specs/` and `docs/specifications/shaping/` for the
symbols and UI strings this diff touches. The Queue tab design is the known
hit: its Problem section and its Non-goals both rest on the reclaim path not
existing. Amend in place as its own commit; report in the PR body.
