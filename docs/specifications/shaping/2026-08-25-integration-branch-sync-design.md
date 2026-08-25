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

1. **The tag**, if it exists — possibly advanced by promotion, below.
2. **`BOOTSTRAP_BASE`**, a constant naming the `main` commit `integration` held
   when this workflow was written (`43217e9`; #184 synced `integration` to
   exactly that tree).

### Keeping it current: the pending tag

Opening a sync PR writes `integration-sync-pending`, naming the `main` commit
that PR carries — **before** auto-merge is armed, and the job fails if it cannot
be written. Ordering matters: an armed PR can land at any moment, and one that
lands with no pending tag leaves the marker behind what `integration` holds,
which is the lag that drops reverts. For the same reason the workflow refuses to
arm auto-merge on an already-open sync PR whose pending tag has gone missing. A later run resolves it:

| Sync PR state | Action |
| --- | --- |
| still open | leave pending; nothing is proven yet |
| merged | promote to `integration-sync-base`, then delete pending |
| closed unmerged | delete pending, promote nothing |

The PR itself is looked up by head branch, which survives the branch's deletion
because GitHub keeps the pull request record. That is the whole reason this is a
tag rather than something read off the branch: the branch is gone on merge.

There is deliberately **no recovery from the sync branch**, though several
drafts had one: read the incorporated `main` SHA back off the landed sync
branch, prove containment by tree equality, adopt it. It cannot work here.
`delete_branch_on_merge` is on for this repository, so the sync branch is gone
the instant its PR merges — verified against the head branches of #157, #178 and
#184, none of which survive. A mechanism that can never execute is worse than
its absence: it reads as covering a case it does not.

A lagging marker is not merely stale — it is **wrong**, and an earlier draft of
this document said otherwise. Valid and current are different properties, and
only the second makes the merge correct:

```
main   M0(off) -> M1(on) -> M2(reverts to off)
integration    holds M1's change, from a sync that landed

marker M0 (lagging)  -> base=off ours=on theirs=off -> keeps ON   revert DROPPED
marker M1 (current)  -> base=on  ours=on theirs=off -> takes OFF  revert applied
```

With the marker lagging, git sees `base == theirs` and treats `integration`'s
side as the only change, so `main`'s revert never arrives and the next promotion
carries the reverted change back into `main`. Worse, that merge is a no-op, so
an exit that recorded "containment proven" there would stamp the revert as
incorporated without ever applying it.

So the marker must be kept current, which needs the incorporated commit recorded
somewhere that outlives the sync branch.

### Where a marker is recorded

Only these two, and each has proven containment at that point:

| Point | What proves containment |
| --- | --- |
| Trees identical | `integration` and `main` hold the same files |
| A sync PR merged | The pending tag named what it carried, and it landed |

A merge that changes nothing on `integration` deliberately records **nothing**.
It only proves containment *relative to the base used*, so with a lagging marker
it proves nothing at all — and that is precisely the case where stamping the
current `main` marks a revert incorporated without applying it.

The path that *opens* a sync PR deliberately records nothing: the merge has not
landed, so containment is not yet true.

### Bootstrap retirement

`BOOTSTRAP_BASE` covers the first run and is never consulted again once a real
marker exists. It is not retired by deletion — it stays as a floor — but reaching
for it *after* a sync has run means the marker tag is missing or has never been
written, which is warned about loudly. It is deliberately **not** a hard
failure: every path that records a marker lies past that point, so a stop there
cannot be recovered from by the workflow itself.

## The two jobs

### `sync`

Triggers: push to `main`, push to `integration`, a daily cron, and
`workflow_dispatch`. The `integration` trigger is there because a sync landing
is the moment the pending tag can be promoted, and step 2 may be able to record
the marker outright.

1. Resolve a marker (above).
2. Trees identical? Record the marker; done.
3. Is `main` still *at* the marker? Compared by commit id, not tree. If it is,
   the remaining difference is `integration`'s own unpromoted bumps and there is
   nothing to bring over: skip **without attempting the merge**, since
   attempting it is what produces the spurious conflict described above.
   Tree equality is not sufficient — because the marker may lag, `integration`
   can already hold `main` changes the marker does not name, and a `main` that
   reverts to the marker's tree would read as "not advanced". The revert would
   never reach `integration`, and the next promotion would carry the reverted
   changes back into `main`.
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

Triggers: push to `integration`, the same cron, and `workflow_dispatch`. It runs
whatever `sync` did, including when `sync` failed — those are the moments PRs
most need catching up, not least.

It deliberately does **not** ask whether a sync is queued. An earlier design did,
reasoning that `integration` was about to move so any catch-up would be redone.
That reasoning holds for minutes and fails for days: a queued sync PR waits on
its required checks, and those can sit parked awaiting an "Approve and run"
click whenever the token fallback is in play. Every PR falling behind in the
meantime stayed stranded — the exact failure this workflow exists to prevent,
traded against nothing worse than a redundant `update-branch`, which is
harmless. Getting the condition right also proved hard in its own right: three
separate findings came from exits that reported the wrong pending state.

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

Valid does not mean current. The marker advances at the trees-identical exit
and when the pending tag is promoted, which between them should keep it
current — but a promotion that failed, or a tag moved by hand, leaves it
lagging, and merging from a lagging base reproduces the frozen-base artefact in
miniature, on files nobody actually disagrees about. So a conflict has two
possible readings, and the diagnostic says so rather than asserting the first:

- **A real disagreement.** Both sides changed the same lines since a base they
  genuinely share. Resolve by hand, push, open a PR into `integration`.
- **A stale marker.** `integration` already holds the newer side. Moving
  `integration-sync-base` forward and re-running is the fix; resolving by hand
  would be committing to a conflict that does not exist.

Check which before resolving. A conflict against a base that is not on `main`
is neither — the invariant makes that unreachable.

The job fails with the branch named either way, and the next run leaves that
branch alone rather than rebuilding it. A sync PR whose conflict needs hand
resolution also has auto-merge disabled and is labelled
`sync-conflict-needs-hand-merge`, which stops a later run re-arming auto-merge
once the resolution makes it mergeable — the resolution is merged by whoever
wrote it.

## Tokens

`INTEGRATION_PROMOTE_TOKEN` is a **prerequisite for the first sync**, not a
convenience, and must hold the `workflow` scope. That sync carries
`integration-sync.yml` itself plus a change to `integration-promote.yml`, and
`GITHUB_TOKEN` may not create or update anything under `.github/workflows/`. It
is passed to `actions/checkout`, not only exported as `GH_TOKEN`: checkout
persists whichever token it used as the git credential for every later push,
and `GH_TOKEN` authenticates only the `gh` CLI.

It needs Contents, Pull requests **and Issues** write. Issues because labels are
issues-scoped even on a PR, and `sync-conflict-needs-hand-merge` is the only
thing keeping auto-merge off a hand-resolved conflict. The workflow's own
`permissions:` block grants the same three.

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
| Unreachable recovery mechanism | Read the incorporated `main` SHA off the landed sync branch, but `delete_branch_on_merge` removes that branch on merge | Removed. The lag it left behind was *not* benign — see the revert row below — and the pending tag is what actually replaced it |
| **Pending bumps silently deleted** | `update-branch` on a behind sync PR makes the tip's `^2` an *integration* commit; recorded as the base, a bump `main` never had reads as "removed on main" and is dropped with no conflict | The on-`main` invariant, and later removing sync-branch recovery altogether so nothing reads `^2` |
| Hand-resolved conflict auto-merged anyway | The `dirty` path disabled auto-merge, but the resolution made the PR `clean` and the next run re-armed it unconditionally | `sync-conflict-needs-hand-merge` label gates the re-arm |
| That label never actually applied | Labels need `issues: write`, which the workflow did not grant; the failure was swallowed and the guard silently absent | `issues: write`, and the attach failure now reports as an error |
| Guard failed open on an API error | Both reads sat inside `if` tests, where `set -e` cannot see a command fail, so an auth error read as "no label, not armed" | Hoisted into assignments |
| A revert on `main` never reaching `integration` | "Has main advanced" compared trees, so a `main` reverting to the marker's tree read as no advance | Compare commit ids |
| Refresh suppressed for days | The pending guard held `refresh` off while a sync PR was queued, and a queued PR can wait indefinitely on parked checks | Guard removed; a redundant `update-branch` is cheaper than a stranded PR |
| **A revert on `main` silently dropped** | The marker lagged what `integration` held, so the three-way merge read `main`'s revert as no change at all and kept the old side | The pending tag keeps the marker current |
| A dropped revert recorded as incorporated | The no-op exit stamped the current `main` even though the no-op was an artefact of the lagging base | That exit records nothing |
| Resolver told auto-merge was off when it was not | `--disable-auto` failing only warned, then the instructions printed anyway | That path fails instead |
| A sync landing with no pending tag | The tag was written after auto-merge was armed, and its failure was swallowed | Written first, and failure is fatal |
| A fork discarding the pending marker | The closed-PR lookup matched on branch name alone, so a fork's closed PR read as "the sync was abandoned" | `isCrossRepository` on that lookup too |
| Sync PR open forever | `\|\| true` swallowed a total auto-merge failure, and the run reported success | Let it fail |
| Unrelated PRs blocked by someone else's conflict | A conflicted sync PR reported a pending sync, suppressing `refresh`, though auto-merge was off and nothing was about to move | Superseded: the pending guard is gone entirely |
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
