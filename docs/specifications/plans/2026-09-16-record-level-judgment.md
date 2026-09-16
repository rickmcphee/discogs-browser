# Record-Level Judgment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking. (This plan was executed inline in the session that wrote its spec, which had no superpowers skills installed, and its later tasks were shaped by review rounds on the pull request — recorded here as the historical task log the plans tree is for, in the same way as [`2026-09-07-recommendation-judgment-token-cost.md`](2026-09-07-recommendation-judgment-token-cost.md).)

**Goal:** Stop paying for the same taste question more than once. Judgments already survived a scheduled re-crawl, but the identity they hang on — `item_key`, a hash including the store URL — made one record three separate paid questions: a re-slugged product page, a second shop stocking it, and each pressing.

**Architecture:** Judgment is billed per *record* rather than per listing, without re-keying the table that stores it. `record_key` (`backend/title_key.py`) is `title_key` one step coarser, dropping the pressing-variant vocabulary a store fences off in a bracket or a trailing segment. `get_unjudged_stock_items` returns one representative per unjudged record; `db.propagate_stock_judgments` copies a record's verdict onto its other in-stock, unowned, unjudged listings. Every reader — the Recommended filter, `get_recommended_stock_items`, the CSV export — goes on matching listing-for-listing, so `stock_item_judgments` keeps its `(user_id, item_key)` primary key, its export format, and needs no migration. Verdicts reach records through `stock_item_identities`, never `stock_items`, because the snapshot table is deleted and rewritten every sync and a join through it loses exactly the re-listing case this exists to catch.

**Tech Stack:** Python ≥3.9, Postgres with RLS-scoped connections, pytest with `asyncio_mode = "auto"`; React + Vite + TypeScript with vitest for the Store tab's filter state.

**Spec:** [`docs/specifications/shaping/2026-09-16-record-level-judgment-design.md`](../shaping/2026-09-16-record-level-judgment-design.md)

**Branch:** `claude/wizardly-goldberg-mxvey2`, worktree under `.worktrees/`, based on `origin/main`. Not stacked on anything.

**Before starting:** confirm the baseline is green so any later failure is attributable to this plan.

```bash
cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test \
  IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest
```

`pytest` needs a running Postgres and all three of `TEST_DATABASE_URL`, `IDENTITY_DB_PASSWORD`, `APP_DB_PASSWORD` set — without the latter two, `init_tenant_schema()` raises `RuntimeError` and every DB test errors at setup.

## Task 1: `record_key`

- [x] Add `record_key(title, artist)` to `backend/title_key.py`: `title_key`'s fold with the pressing-variant vocabulary (`_VARIANT_WORDS`) dropped.
- [x] Drop those words **only** where a store fenced them off — inside a bracketed aside, or in a trailing segment behind a separator. A bare word list folds "Purple Rain" into "Rain" and "Black Sabbath" into "Sabbath", and a false merge here hands a record another's verdict *and* a reason written about a different album.
- [x] Keep the never-empty guarantee: fall back to `title_key(title, artist)` when stripping would leave nothing.

## Task 2: Store and sweep the key

- [x] Add `record_key` to `stock_items` **and** `stock_item_identities`. Both, because the judgment path needs the durable table: `replace_stock_items` deletes and rewrites the snapshot every sync, so a verdict reached through live stock rows goes missing for the re-slug case.
- [x] Compute the value once per item in `replace_stock_items` and `upsert_stock_item_from_release` and write that same value to both tables, so the pair can never disagree about which record an `item_key` belongs to.
- [x] Rename `backfill_title_keys` to `backfill_stock_keys` and have it fill either column across both tables. Key an identity from its live stock row where it still has one — on the release-crawler path the two tables' own titles genuinely differ (`listing_title` against the catalog target's name), and reading each table's own columns would reintroduce the disagreement the live writers avoid.

## Task 3: Bill per record

- [x] `get_unjudged_stock_items` returns one representative per unjudged record, `DISTINCT ON` the group with the lowest `item_key` so repeated runs batch identically; `count_unjudged_stock_items` counts the same groups.
- [x] Anti-join on the *record*, through `stock_item_identities`, with `i.item_key = s.item_key` as a floor so this exact listing having been judged reads as judged whatever the folds do.
- [x] Add `db.propagate_stock_judgments`, scoped to exactly the rows that would otherwise be billed (in stock, unjudged, not owned), newest `judged_at` winning where a record's listings disagree.
- [x] Call it from `_run_judgment_phase` before the counts and after each batch's upsert, each in its own `user_scope` — `app.user_id` is set transaction-locally, so committing inside one scope and carrying on leaves later statements with no RLS identity.
- [x] Carry the count on `stock_judgment_complete` as `inherited`.

## Task 4: Guard the start path

- [x] Restore the `stock_sync_running` check on `start_judgment_only`, in the manager rather than the router so no call site can start a run around it. Do **not** restore the mirror on `start_stock_sync`: judgment is per-user, the sync is global.
- [x] Make both guards reach across Machines. `db.advisory_lock_held` reads `pg_locks` for the sync lock; a judgment run holds `pg_try_advisory_lock(JUDGMENT_LOCK_NAMESPACE, user_id)` for its lifetime, released in `_run_judgment_phase`'s `finally`.
- [x] Serialize the whole check-and-assign under one asyncio lock, since the cross-Machine reads await and the task assignment is what makes the running check true.
- [x] Return `{started, running, stock_sync_running}` rather than a bool: both local flags read false for anything happening on another Machine, so the router forwards this answer rather than re-deriving it.
- [x] Render the rejection in `handleRefreshRecommendations`, guarded on the status-write counter so a slow response cannot talk over newer progress.

## Task 5: Tests

- [x] `record_key` folds every fenced pressing variant onto the bare record, keeps unfenced colour words ("Purple Rain"), keeps bare numbers ("Greatest Hits (2)"), and survives artist names containing separators or punctuation the SQL fold normalizes.
- [x] Two shops, a re-slug and a second pressing are each one billable item; propagation carries reason and `judged_at`, prefers the newest verdict, and leaves owned listings and other users alone.
- [x] The start guards refuse for a local run, a sync anywhere, and this user's run on another Machine; the lock is released on success, on failure, and on cancellation at the first await.
- [x] The Recommended filter unlocks on an inherited-only completion, and a rejection does not overwrite newer progress.

## Task 6: Spec drift and PR

- [x] Sweep both spec trees for the symbols this diff touches, amend what drifted, and record it in the PR description.
- [x] Open the PR ready for review, then work the review rounds to green.

## Review rounds

The pull request drew several rounds of review, each of which changed the code above. Recorded here because the shape of what shipped is not derivable from the tasks alone:

- **Round 1** — the sync guard was process-local; the backfill put different keys in each table for release-crawler rows; `_SEGMENT_SPLIT` tore apart artist names containing separators; the rejection message promised an automatic refresh that never happens.
- **Round 2** — the cross-Machine read introduced an `await` between the running check and the task assignment, so two clicks for one user could both start; a bare digit counted as a pressing variant; the two sides of the record match bottomed out at different fallbacks.
- **Round 3** — `_judgment_tasks` is a dict in one process, so two Machines could each start the same user's run. This predates the branch but is the same duplicate spend, so it was fixed here rather than deferred.
- **Round 4** — propagation made a run that writes judgments without judging any, which the Recommended filter's `judged > 0` gate could not see.
- **Round 5** — a mixed keyed/unkeyed catalog, which a rolling deploy produces, re-bills a judged record's siblings; the lock acquisition leaked a session when its query failed; the identities index was justified as "nearly free" on a false premise, and measurement showed it is a real trade worth making for different reasons.
- **Round 6** — the artist-prefix test did not fold the punctuation `_artist_punct_fold_sql` folds, so a store spelling "Hall & Oates" against a stored "Hall and Oates" kept the artist in the key.
- **Round 7** — that fold ran *after* the article forms were expanded, unlike the SQL, so "The-Beatles" never lost its article; the lock-release `finally` started after the first await; a slow rejection could overwrite newer progress in the banner.

**Verification at the end of round 7:** `4738 passed` backend, `621 passed` frontend, `tsc --noEmit` clean.
