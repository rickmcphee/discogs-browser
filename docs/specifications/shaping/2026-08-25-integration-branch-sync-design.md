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
  request and name no bypass actors, so a direct push is rejected however the
  workflow authenticates.
- Deleting the `integration` branch, or otherwise resetting its history.

## The merge base

This was the load-bearing fact of the original design, and it has changed.

Both branches used to squash-merge in both directions. A squash gives neither
branch an ancestor from the other, so `git merge-base main integration` was
pinned to the last commit the two genuinely shared — `b24e568e`, 2026-08-10 —
and stayed pinned there. It did not move even after the manual sync in #184
brought `integration` to `main`'s exact tree.

Merging against a base that old is not merely inefficient, it is wrong. A
dependency pin `integration` has re-bumped looks changed on *both* sides:

```
base b24e568e   cryptography>=42
ours            cryptography>=51     (integration: synced, then re-bumped)
theirs          cryptography>=50     (main: promoted once)
-> conflict, on a file nobody disagrees about
```

Because git could not derive that base, an earlier revision of this design
recorded one by hand: a marker tag naming the `main` commit `integration`
provably held, a second tag to carry it across a sync PR's merge, a bootstrap
constant for the first run, and an invariant on each. It worked, and it was
the source of nearly every defect found while building it.

**Merge commits retire all of it.** A sync PR whose head is a merge commit with
`main`'s tip as its second parent makes that tip a genuine ancestor of
`integration` the moment it lands. `git merge-base` is then correct by
construction, for good, with nothing to record, promote or maintain.

**Ordering matters, and is enforced rather than assumed.** #186 is the one-time
merge that establishes the link, and until it lands git still derives the frozen
base — so this workflow merging first would reproduce the very conflicts it
dropped the marker protocol to avoid. The `sync` job therefore refuses to run
while `git merge-base` still returns `b24e568e`, naming the bootstrap as the
fix. The guard retires itself: once a merge commit links the branches the base
moves and the check never fires again.

Two consequences worth stating plainly, because both are load-bearing:

- **A sync PR must never be squashed.** Squashing one would land it as a single
  commit carrying no parent link to `main`, re-freezing the base at that point
  and requiring the whole recorded-marker protocol back. `arm_auto_merge` uses
  `--merge` with no squash fallback and fails loudly if merge commits are
  unavailable, rather than quietly doing the thing that breaks it.
- **A promotion produces a sync PR with no file changes.** A promotion advances
  `main` with content that came from `integration`, so merging it back applies
  nothing — but the merge commit is still the point, since it is what moves
  `main`'s tip into `integration`'s ancestry. The PR is opened anyway. One
  empty PR per promotion is the running cost of keeping the base current, and
  it is a good trade against the protocol it replaced.

Two settings, at different scopes, and the difference matters:

- The repository-level **"Allow merge commits"** toggle gates the API outright,
  returning `405 Merge commits are not allowed on this repository` regardless
  of what any ruleset says. It is repo-**wide**.
- `allowed_merge_methods` on a branch ruleset narrows what that branch accepts.
  `integration`'s should be **`["merge"]` — merge only**, not merely "including
  merge".

Merge-only rather than merge-permitted, because `arm_auto_merge` is not the
only way a sync PR can land. A conflicted sync PR is deliberately handed to a
person to resolve and merge, and there the workflow enforces nothing: if the
ruleset offers a squash button, one squash re-freezes the base for good and
brings the whole marker protocol back. Removing the choice is the only version
of this that does not rely on the resolver remembering. The cost is that
Dependabot's own PRs land on `integration` as merge commits too, which is
invisible — `integration` is squash-promoted into `main` regardless.

Where a squash-armed request already exists — the previous revision of this
workflow armed `--auto --squash` as a fallback, so this is a real state, not a
hypothetical — the `sync` job now reads `autoMergeRequest.mergeMethod`,
disables it, and re-arms with `--merge`.

Because the first is repo-wide, `main` only stays squash-only if **its own
ruleset pins `allowed_merge_methods` to squash**. Check that when enabling the
toggle rather than assuming it: nothing in this design requires `main` to
accept merge commits, and letting it silently start doing so would put merge
commits into the history this whole arrangement keeps linear.

## Is there anything to sync?

One question, answered from ancestry:

```bash
if git merge-base --is-ancestor origin/main origin/integration; then
  echo "integration already contains main; nothing to sync."
  exit 0
fi
```

This replaces a tree comparison, a commit-identity comparison, and the marker
resolution that fed both. It is exact: it cannot report "nothing to do" for a
revert (the case that forced the pending tag into the previous design), and it
cannot mistake `integration`'s own unpromoted bumps for something to merge.


## The two jobs

### `sync`

Triggers: push to `main`, push to `integration`, a daily cron, and
`workflow_dispatch`. Every trigger is cheap, because step 1 answers from refs
alone.

1. Does `integration` already contain `main`? Exit if so (above).
2. A sync PR already open? Resolve its own `mergeable_state`: `behind` gets
   `update-branch` before auto-merge is re-armed; `dirty` fails loudly and
   leaves auto-merge off. Do not rebuild its branch — it may carry a
   hand-resolved conflict.
3. Otherwise branch from `integration`, `git merge origin/main`, and open a PR
   that auto-merges **with a merge commit**.

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

A conflict now means one thing: both sides genuinely changed the same lines
since a base git derived from real ancestry. Resolve by hand, push, open a PR
into `integration`.

This is a simplification worth noting rather than passing over. Under the
recorded marker a conflict had *two* readings — a real disagreement, or a
marker that had fallen behind what `integration` held — and the diagnostic had
to present both, because resolving a conflict that does not exist is its own
way to lose work. With the base derived rather than recorded, the second
reading is gone.

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

It needs Contents, Pull requests, Issues **and Workflows** write — as a classic
PAT, `repo` + `workflow`. Issues because labels are issues-scoped even on a PR,
and `sync-conflict-needs-hand-merge` is the only thing keeping auto-merge off a
hand-resolved conflict. Workflows because Contents does not cover
`.github/workflows/`: a token without it hits exactly the push rejection
described above, so a setup that lists only the first three produces the very
failure this section diagnoses. The workflow's own `permissions:` block grants
the three that apply to it (`GITHUB_TOKEN` cannot be given Workflows at all,
which is why the PAT is the prerequisite).

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

**Read this table as history.** Every row below is a defect in the recorded
marker, and the marker no longer exists — enabling merge commits deleted the
mechanism and all of its failure modes at once. The table is kept because it is
the argument for that trade: a hand-maintained substitute for a value git can
compute went wrong in every way recorded below, all within a single sitting,
and the cost of each was a silent one — a dropped revert, a deleted bump, a
guard that failed open.

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

**Taken:** allow merge commits on `integration` — the repository's "Allow merge
commits" toggle plus `"merge"` in `allowed_merge_methods` on
`integration-branch-protection`. This is what retired the marker, the pending
tag, the bootstrap constant and their invariants. It is recorded here as a
settings prerequisite, not an optimisation: the workflow fails loudly if merge
commits become unavailable again, because the alternative is silently
re-freezing the base.

**Still available:**

1. **Drop `strict_required_status_checks_policy` on `integration` only.** That
   is the mechanism behind failure (1), and dropping it would make most of
   `refresh` unnecessary. The promotion PR re-tests the whole batch against
   `main` before anything ships, so it buys little on a staging branch.

## Runtime/agent document impact

`CLAUDE.md` carries the invariant in its "Key invariants" list, and a scope note
on the auto-merge rule clarifying that it governs PRs opened by hand — the
`--auto` in these two workflows is deliberate and load-bearing.
