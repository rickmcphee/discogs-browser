# Keeping `integration` and `main` in step

Status: implemented in `.github/workflows/integration-sync.yml`.

## Problem

Dependabot targets `integration` (`.github/dependabot.yml`) so a week of routine
bumps costs one deploy rather than one per PR, and
`.github/workflows/integration-promote.yml` carries that batch into `main` every
Tuesday. Nothing made either return trip, and two *separate* failures followed.
They are worth stating apart, because they have different causes, different
fixes, and only one of them is about drift — conflating them sends the next
reader after the wrong one.

**1. Open PRs turning un-mergeable.** `integration-branch-protection` sets
`strict_required_status_checks_policy`, which compares a PR's head with **its
own base**. Every open Dependabot PR therefore reports
`mergeable_state=behind` the moment *anything* lands on `integration` — another
bump merging, or the sync described here — and stays there until something moves
its head forward. Nothing did. Note `main` is not an input to that comparison:
`integration` trailing `main` does not by itself make any PR `behind`.

**2. Bumps vetted against stale code.** The promotion squash-merges, which
advances `main` and leaves `integration` exactly where it stood. With nothing
carrying `main` back, bumps were tested against an ever-staler base and then
promoted into a `main` they had never been tested against.

Both bit at once on 2026-08-25: six Dependabot PRs sat unmergeable, the oldest
for a fortnight, against an `integration` that had fallen 64 commits behind
`main`. The history shows which mechanism stranded them — #113/#114 opened on
`b24e568e` and went behind when `integration` advanced to `c5cec51`;
#110/#175/#176/#177 opened on `c5cec51` and went behind when it advanced to
`0e30274`. Every one of those is a merge *into* `integration`, not a promotion
out of it.

## Goals

- `integration` holds `main`'s files, without discarding bumps it is still
  carrying.
- No PR based on `integration` is left stranded when `integration` moves.
- Every failure is loud. A silent no-op that reports success is worse than a
  failed run, because nobody investigates a green run.

## Non-goals

- Changing what `integration` is for, or how the promotion works.
- Pushing to `main` or `integration` directly. Both rulesets require a pull
  request, allow only squash merges, and name no bypass actors, so a direct
  push is rejected however the workflow authenticates.
- Deleting the `integration` branch, or otherwise resetting its history.

## Why the merge base cannot be inferred

This is the load-bearing fact, and everything below follows from it.

Both branches only ever squash-merge, in both directions. A squash gives neither
branch an ancestor from the other, so `git merge-base main integration` is
pinned to the last commit the two genuinely shared — `b24e568e`, 2026-08-10 —
and stays pinned there. It did not move even after the manual sync in #184
brought `integration` to `main`'s exact tree.

Merging against a base that old is not merely inefficient, it is wrong. A
dependency pin `integration` has re-bumped looks changed on *both* sides:

```
base b24e568e   cryptography>=42
ours            cryptography>=51     (integration: synced, then re-bumped)
theirs          cryptography>=50     (main: promoted once)
-> conflict, on a file nobody disagrees about
```

The correct base is not something git can derive. It must be recorded.

## The marker

A **marker** is a commit on `main` whose content `integration` provably already
holds. That is its entire meaning, and every rule below is a consequence:

- **Invariant.** A marker MUST be an ancestor of `origin/main`
  (`git merge-base --is-ancestor`). No commit off `main` can assert "integration
  holds main up to here". This is checked on every marker regardless of where it
  came from, because one wrong base is as damaging as another and the check
  costs nothing.
- **Recorded only where containment is proven.** Never optimistically.
  Recording a marker before the content is actually on `integration` makes a
  later run conclude there is nothing to bring over and skip a sync that was
  genuinely needed.
- **Storage.** The tag `integration-sync-base`, force-moved. A tag rather than a
  file because it must survive on a branch the workflow does not otherwise
  write to, and force-moved because it is a moving pointer, not history.

### Obtaining a marker

In order, first hit wins:

1. **The tag**, if it exists.
2. **`BOOTSTRAP_BASE`**, a constant naming the `main` commit `integration` held
   when this workflow was written (`43217e9`; #184 synced `integration` to
   exactly that tree).

There is deliberately **no recovery from the sync branch**, though several
drafts had one: read the incorporated `main` SHA back off the landed sync
branch, prove containment by tree equality, adopt it. It cannot work here.
`delete_branch_on_merge` is on for this repository, so the sync branch is gone
the instant its PR merges — verified against the head branches of #157, #178 and
#184, none of which survive. A mechanism that can never execute is worse than
its absence: it reads as covering a case it does not.

The consequence is that the marker may **lag** — the path that opens a sync PR
proves nothing, so the marker only advances at the two exits below. It is always
*valid* (the invariant guarantees that), occasionally older than it could be,
and an older-but-valid base costs a possible real-looking conflict, never a
wrong merge.

### Where a marker is recorded

Only these two, and each has proven containment at that point:

| Point | What proves containment |
| --- | --- |
| Trees identical | `integration` and `main` hold the same files |
| Merge was a no-op | Bringing `main` in changed nothing on `integration` |

The path that *opens* a sync PR deliberately records nothing: the merge has not
landed, so containment is not yet true.

### Bootstrap retirement

`BOOTSTRAP_BASE` covers the first run and is never consulted again once a real
marker exists. It is not retired by deletion — it stays as a floor — but reaching
for it *after* a sync has run means neither the tag nor a landed sync branch
survived, which is warned about loudly. It is deliberately **not** a hard
failure: every path that records a marker lies past that point, so a stop there
cannot be recovered from by the workflow itself.

## The two jobs

### `sync`

Triggers: push to `main`, a daily cron, and `workflow_dispatch`.

1. Resolve a marker (above).
2. Trees identical? Record the marker; done.
3. Has `main` advanced past the marker? If not, the remaining difference is
   `integration`'s own unpromoted bumps and there is nothing to bring over.
   Skip **without attempting the merge** — attempting it is what produces the
   spurious conflict described above.
4. A sync PR already open? Resolve its own `mergeable_state`: `behind` gets
   `update-branch` before auto-merge is re-armed; `dirty` fails loudly and
   leaves auto-merge off. Do not rebuild its branch — it may carry a
   hand-resolved conflict.
5. Otherwise branch from `integration`, merge `main` in against the marker, and
   open a PR that auto-merges.

Branch from `integration` and merge `main` in, never the reverse: the head must
contain `integration`'s tip to satisfy the same strict up-to-date policy on the
way back in.

### `refresh`

Triggers: push to `integration`, the same cron, and `workflow_dispatch`. Held
off while a sync PR is *queued to merge*, since `integration` is about to move
again and any catch-up would immediately be stale.

"Queued" is the precise condition, not "open". A sync PR left conflicted has
auto-merge deliberately off, so nothing is about to move; holding `refresh` off
there would keep every unrelated stale PR hostage to a conflict that has nothing
to do with them. The same applies when auto-merge could not be armed at all —
those paths fail before reporting a pending sync, so `refresh` still runs.

Routes each open PR by `mergeable_state`, polled rather than read once because
GitHub computes it asynchronously and answers `unknown` right after a push:

- `behind` → `update-branch`. Deterministic, returns a status, needs nothing of
  the PR's author.
- `dirty` → `@dependabot recreate`, on Dependabot's own PRs only. A bump that
  conflicts is usually a lockfile, and a lockfile resolved by merge is a file no
  package manager would have written. A conflict on anyone else's PR is
  reported for a human.
- anything else → untouched.

The recreate request carries `RECREATE_MARKER`, and the don't-ask-twice guard
counts only comments bearing it. Matching on the command text would let anyone
who can comment suppress the real request; matching on author would break the
day `INTEGRATION_PROMOTE_TOKEN` is configured and the author changes.

## Conflict handling

A conflict from a merge against a *valid* marker is a real disagreement — both
sides changed the same lines since a base both genuinely share. The job fails
with the branch named, for a human to resolve and push. The next run leaves that
branch alone rather than rebuilding it.

A conflict against an *invalid* base is the frozen-base artefact, and the design
above exists to make it unreachable.

## Tokens

`INTEGRATION_PROMOTE_TOKEN` is a **prerequisite for the first sync**, not a
convenience, and must hold the `workflow` scope. That sync carries
`integration-sync.yml` itself plus a change to `integration-promote.yml`, and
`GITHUB_TOKEN` may not create or update anything under `.github/workflows/`. It
is passed to `actions/checkout`, not only exported as `GH_TOKEN`: checkout
persists whichever token it used as the git credential for every later push,
and `GH_TOKEN` authenticates only the `gh` CLI.

Separately, and more weakly, the token removes a weekly "Approve and run" click:
a PR opened with `GITHUB_TOKEN` has its `pull_request` checks parked in an
approval-required state. Parked, not absent — on #182 the run was created three
seconds after the PR and started as attempt 2 five hours later with a human as
`triggering_actor`.

## Failure modes retired

Each of these was reachable in an earlier draft and is now closed. They are
recorded because every one was found by changing one part of the protocol
without a contract for the rest, which is the failure this document exists to
prevent.

| Failure | Why it happened | Closed by |
| --- | --- | --- |
| Spurious conflict on a re-bumped pin | Merged against the frozen inferred base | The marker |
| First run impossible | Inferred base predates `integration-promote.yml`; add/add conflict, and it died before recording a marker | `BOOTSTRAP_BASE` |
| Deadlock after the first success | "No marker + workflow file present ⇒ tag lost ⇒ fail" — but the PR-opening path never records a marker, so this fired the moment the first sync landed | The stale-bootstrap case warns instead of failing |
| Unreachable recovery mechanism | Read the incorporated `main` SHA off the landed sync branch, but `delete_branch_on_merge` removes that branch on merge | Removed; the marker lags instead, and lagging is always valid |
| **Pending bumps silently deleted** | `update-branch` on a behind sync PR makes the tip's `^2` an *integration* commit; recorded as the base, a bump `main` never had reads as "removed on main" and is dropped with no conflict | First-parent walk plus the on-`main` invariant |
| Sync PR open forever | `\|\| true` swallowed total auto-merge failure while `pending=true` suppressed `refresh` | Let it fail |
| Unrelated PRs blocked by someone else's conflict | A conflicted sync PR reported a pending sync, suppressing `refresh`, though auto-merge was off and nothing was about to move | `pending=false` on that path |
| Refresh silently doing nothing | `for n in $(gh pr list …)` is a word expansion, so `set -e` never saw the command fail | Assign first |
| Fork PR handed auto-merge | `--head` matches branch *name* only | `isCrossRepository == false` |

## Simplifications available

Neither is a code change; both are repository settings.

1. **Allow merge commits** (repo setting) plus `"merge"` in
   `allowed_merge_methods` on `integration-branch-protection`. This keeps git's
   own merge base current and makes the entire marker mechanism belt-and-braces
   rather than load-bearing. The sync already asks for a merge commit and falls
   back to squash, so it starts using one with no edit.
2. **Drop `strict_required_status_checks_policy` on `integration` only.** That
   is the mechanism behind failure (1), and dropping it would make most of
   `refresh` unnecessary. The promotion PR re-tests the whole batch against
   `main` before anything ships, so it buys little on a staging branch.

## Runtime/agent document impact

`CLAUDE.md` carries the invariant in its "Key invariants" list, and a scope note
on the auto-merge rule clarifying that it governs PRs opened by hand — the
`--auto` in these two workflows is deliberate and load-bearing.
