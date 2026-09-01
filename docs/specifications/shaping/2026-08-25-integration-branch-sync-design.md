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
- No open PR is left stranded when the branch it targets moves. Written as
  "no PR based on `integration`" until 2026-08-29 — see the amendment on
  `refresh` below, which widened the job to `main`-based PRs for the same
  reason, not a new one.
- Every failure is loud. A silent no-op that reports success is worse than a
  failed run, because nobody investigates a green run.

## Non-goals

- Changing what `integration` is for, or how the promotion works.
- Pushing to `main` or `integration` directly. Both rulesets require a pull
  request, and a direct push is rejected however the workflow authenticates.
  The reason differs between them, and the earlier wording here ("both … name
  no bypass actors") was wrong about `main`: `integration` names no bypass
  actors at all, while `main` names the repository owner with
  `bypass_mode: pull_request` — a bypass that applies *within* a pull request,
  letting them merge one that has not met the required approving review, and
  which grants no direct-push permission to anyone. The conclusion held; the
  premise did not. This matters to `refresh`, which skips PRs whose head is
  either branch precisely because that push is unavailable to it.
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

Checked 2026-08-29, reading the branch's rules anonymously: `main`'s ruleset
pins `allowed_merge_methods: ["squash"]`. The repo-wide toggle has not loosened
it. Recorded in
[`2026-08-29-copilot-pr-approval-design.md`](2026-08-29-copilot-pr-approval-design.md),
which records those effective branch rules for a different reason. They are the
rules the endpoint returns, not the whole ruleset — an anonymous read omits
bypass actors.

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
   leaves auto-merge off; `blocked` fails loudly once a failed check is left
   standing with nothing still running that could clear it. Do not rebuild its
   branch — it may carry a hand-resolved conflict.

**Amended 2026-09-01: `blocked` is routed rather than falling through.**
`integration` requires no approving review, so a sync PR is `blocked` for one of
two unrelated reasons: its required checks have not finished — the normal first
minutes of every sync PR's life — or one of them failed. Only the second is a
problem, and it used to be an invisible one. Nothing in the job can clear it:
step 2 never rebuilds an open sync PR, so the branch keeps whatever broke it,
and re-arming auto-merge is a no-op on a PR already armed. The run then exits 0,
and every later run reports success while nothing moves.

Sync PR #259 is the case that exposed it. It failed on 2026-08-30 against a
respx/authlib break whose fix landed on `main` hours after its branch was cut,
so the branch could never carry that fix. It sat red for two days, holding
`integration` at `5adef80` and stranding promotion PR #278 behind it — with
green runs of this workflow throughout — until someone read the two PRs side by
side and closed it by hand.

Nothing is decided until the check board settles, and that one condition carries
the whole guard. `statusCheckRollup` reports every check on the PR rather than
just the branch's required ones, so a failed *optional* check sitting beside a
required one that is merely pending would fire this every run for a PR that goes
on to merge perfectly well — the same "a job that is red for a benign reason is
one nobody reads" concern that keeps `refresh` from erroring on the promotion PR.
Waiting for pending to empty also removes any need to ask which checks are
required: with nothing left running, an optional-only failure leaves the PR
`unstable` rather than `blocked`, so it never reaches this routing at all. That
is why no `isRequired` field is read.

Read naively, though, that same rule inverts into something worse than the noise
it prevents. Because the rollup does not mark required checks, waiting on the
whole board means a required check that has *conclusively failed* goes
unreported for as long as any optional check sits queued beside it — and an
optional check that hangs buries it for good, restoring the exact silence this
routing exists to end. Failing loud is recoverable; failing open is the bug.

So a pending check only shields a failure while it is plausibly still running.
Past `PENDING_CHECK_GRACE` (six hours) it is hung rather than slow, and stops
shielding anything. Checks here finish in minutes, so the bound never expires
while something is genuinely running, and the optional-failure suppression holds
for exactly as long as it should. A pending check carrying no usable timestamp
counts as waiting, which fails towards silence rather than towards a red run.

**That last fallback is unbounded, and it is the one hole left in this routing.**
A CheckRun that stays queued keeps exporting no usable `startedAt`, so it reads
as freshly pending on every run rather than ageing out — and a queue entry that
never starts would shield an already-failed required check for as long as it
sits there. "The next run re-decides" is true of the *state* reads above it; it
is not true here, and an earlier draft of this section wrongly said it was.

The hole is narrow on this repository — a sync PR's checks are Backend and
Frontend tests, which start promptly — and the parked "Approve and run" case that
would most plausibly trigger it is inactive while the sync app is configured. It
is documented rather than patched because there is no correct small fix for it:
bounding it needs either a real creation timestamp, which `gh` does not expose
for a CheckRun, or the required-only snapshot below, which removes the need for
optional-check shielding altogether and with it the grace period, this fallback,
and this hole. That upgrade is the fix; further patching around the missing
required-ness is not.

The state is then re-resolved before anything is reported. `mergeable_state` is
read before the rollup, and GitHub recomputes it asynchronously, so the two
disagree across that gap: required checks going green inside it leave the board
settled with only an optional failure standing, while the state still reads
`blocked` from a moment earlier — and reporting on the stale read would call a
PR stuck at the exact moment it turns `unstable` and merges. Anything but
`blocked` on the second read (including `unknown`, which GitHub answers while it
recomputes) means this run cannot honestly call the PR stuck, so it says
nothing. That silence lasts one run and corrects itself; a genuinely stuck PR
stays stuck and is reported next time round.

It also stops there rather than continuing. Falling through would carry every
non-`blocked` value into the generic re-arm below, which is where each of them
does the damage its own branch exists to prevent: `unknown` arms auto-merge on a
state the top of this routing refuses to guess at, `dirty` arms it on a conflict
without the label or the disable, and `behind` arms it without `update-branch`.
A read added to avoid one false report must not become a side entrance past the
fail-closed handling it sits inside.

The report names the checks the grace period stopped shielding, alongside the
failed ones. Because the rollup does not mark required checks, one visible
consequence of inferring remains, and it runs both ways: a required check hung
past the grace period with an optional one failed beside it fires on the
optional failure's name, while a required check that failed beside a hung
optional one fires on the right name but sits next to an irrelevant stalled one.
Either way the PR really is stalled, so reporting is right — but which of the two
is holding it cannot be known from here.

So both sets are reported and neither is ranked. Ranking them would assert
exactly the required-ness this routing is otherwise careful never to infer, and
would be wrong in one of the two directions every time. Naming both, and saying
plainly that the rollup cannot tell them apart, is what the gap costs once
nothing silent is left in it.

`CANCELLED` and `STALE` count as failures alongside the obvious ones — nothing
is coming for either. `ACTION_REQUIRED` counts as *waiting*: it means a person
has been asked for a click, which is the parked "Approve and run" state this
design already documents and tolerates under the token fallback, and erroring on
it would turn a run red every week for a condition deliberately accepted.

The failure is reported rather than repaired, and the reason is narrower than
"a sync PR contains nothing hand-authored". `CONFLICT_LABEL` marks one thing
only: a branch handed to a person because the merge conflicted. It says nothing
about a branch someone pushed a CI fix onto to unstick a red sync PR, which is
both a plausible response to this very error and entirely unlabelled. So an
unlabelled sync branch is *not* provably machine-only, and force-pushing over it
can destroy the only copy of that work.

That applies to the person as much as to the job, so the error does not simply
tell them to delete the branch. It tells them to re-run the failed check first,
since a transient Actions failure clears with nothing changed anywhere; then, if
it reproduces and the cause is already fixed on `main`, to check the branch for
commits this job did not create; and only then to close the PR, delete the
branch and re-run, so step 3 rebuilds from the current `main`. An earlier draft
of this section asserted the machine-only premise and the deletion advice
together, which was both wrong and, in the advice, potentially destructive.
3. Otherwise branch from `integration`, `git merge origin/main`, and open a PR
   that auto-merges **with a merge commit**.

Branch from `integration` and merge `main` in, never the reverse: the head must
contain `integration`'s tip to satisfy the same strict up-to-date policy on the
way back in.

### `refresh`

Triggers: push to `main` or `integration`, the same cron, and
`workflow_dispatch`. It runs whatever `sync` did, including when `sync` failed —
those are the moments PRs most need catching up, not least.

**Amended 2026-08-29: widened from `integration`-based PRs to both bases.**
`main-branch-protection` sets `strict_required_status_checks_policy` too, so a
PR based on `main` goes `behind` the moment any PR merges and cannot merge until
something moves its head forward — the identical failure this job was built for,
on the other branch, and it was left running by hand. The job now queries each
base separately (server-side `--base`, so the 200 limit stays a promise per base
rather than a budget shared between them) and carries each PR's base alongside
its number, because every message here names the branch a PR is behind and
naming the wrong one would send a reader to a branch the PR never touched.

The push guard went with it. It used to skip a push to `main`, correctly: while
the job only routed `integration`-based PRs, `main` moving stranded none of them.
Now `main` moving is exactly what strands a main-based PR, so skipping that event
would leave the daily cron as the only thing catching them up — reintroducing,
for `main`, the up-to-a-day lag this job exists to close.

**A PR whose *head* is a protected branch is skipped.** `update-branch` pushes
the base merge onto the head branch, and neither ruleset permits that push:
`integration` requires a pull request and names no bypass actors at all, while
`main`'s sole bypass actor is a User rather than the sync app. The call is
therefore rejected for this workflow's identity either way, the job collects the
failure, and it exits 1 on every run for as long as such a PR is open and its
base has moved.

The live case is the promotion PR. `integration-promote.yml` opens it
`--base main --head integration`, so widening `refresh` to `main` brought it
into scope — weekly, and open for days while it waits on `main`'s required
approving review. Unhandled, `refresh` would be red for that entire window every
week, and a job that is red every Tuesday for a benign reason is a job nobody
reads, which costs the third goal above rather than serving it.

`main` is named in the filter for symmetry rather than for a case that has
occurred: no PR in this repository has ever had `main` as its head, and `sync`
opens its own from `SYNC_BRANCH` precisely so that one is never needed. It is
there because the rule being encoded is "a protected head cannot be pushed to",
and a filter naming only one of the two protected branches would not be that
rule — it would be a coincidence that happened to hold.

Skipped rather than tolerated as a known failure, because nothing is wrong when
it happens: `sync` is what makes the promotion PR's head current, by merging
`main` into `integration` through a PR of its own — also the only route that
keeps `git merge-base` current, which is why `integration` is merge-only. The
filter is scoped to same-repository heads, so a fork PR that merely has a branch
named `main` or `integration` is an ordinary PR and still gets refreshed.

(Both halves came from Copilot's review on the PR that made this change, in two
rounds, rather than from the change's own testing — the promotion-PR collision
is invisible unless you hold both workflows at once, and the generalisation to
`main` is invisible unless you notice the code was narrower than the comment
justifying it.)

One further consequence: the two base queries share the job's `set -e`, so
a transient API failure listing one base now aborts before the other is listed.
That is deliberate and unchanged in spirit — the alternative is the silent no-op
the third goal above exists to forbid — but it does mean a failure on `main`'s
query leaves `integration`'s PRs for the next run.

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
- `dirty` → reported for a human, with the advice branched on who opened it.
  This job routes *every* open PR based on a branch it covers, not only
  Dependabot's,
  and the two need different instructions: a Dependabot bump can be rebuilt by a
  maintainer's `@dependabot recreate`, while a hand-written PR has to be repaired
  by hand by someone who can push to its head branch. A failed author read falls
  back to wording true either way rather than asserting an authorship it never
  established. A warning
  rather than a job failure: a conflicted PR can sit for days, and failing here
  would turn every later run red over a condition this job cannot act on.
- anything else → untouched.

### Why `dirty` is not delegated to Dependabot

This route used to post `@dependabot recreate` on Dependabot's own PRs, on the
reasoning that a conflicted bump is usually a lockfile and a lockfile resolved
by merge is a file no package manager would have written. That reasoning holds.
The mechanism never worked.

**Dependabot refuses commands from bots**, answering "Sorry, only users with
push access can use that command" — whether the comment comes from
`github-actions[bot]` or from a GitHub App, and regardless of the write
permissions either one holds ([dependabot/dependabot-core#9147][dbc9147]). The
path sat unexercised from the day it was written until 2026-08-26, when a
conflict on PR #175 fired it for the first time and Dependabot refused it.

It was removed rather than reworked because every remaining way to issue the
command needs a real user's token — precisely what the app migration had just
finished removing, and for reasons that have not changed. The supporting
machinery went with it: the `RECREATE_MARKER` stamp, the don't-ask-twice guard
that counted comments bearing it, and the paginated seven-day comment read that
guard required.

**The command still works for a person.** The refusal is specifically about
bots: "only users with push access can use that command" grants it to exactly
the human this route now asks for. So the recovery for a *Dependabot* PR is a
maintainer commenting `@dependabot recreate` — no workflow, no token, no special
access beyond what they already have. For any other conflicted PR the command is
meaningless and the warning says so instead, pointing at the *capability* rather
than an identity: the branch has to be repaired by hand by whoever can push to
it. That is not only the opener — a branch in this repository is writable by any
collaborator with push access, and a fork branch by maintainers when the PR
allows maintainer edits. Naming the author instead, as this route's wording did
from the start, risks talking someone out of a repair they were entitled to
make.

**Do not close a conflicted Dependabot PR instead.** This is the trap, and it
was walked into on #175 while writing this change. Closing does not queue a
clean replacement: Dependabot reads a close as *"skip this release"* and answers
*"I won't notify you again about this release, but will get in touch when a new
version is available"* — so the bump is suppressed until something **newer**
ships, not re-raised against the current base. Its own suggested recovery,
*"just re-open this PR and I'll resolve any conflicts on it"*, then fails on a
second mechanism: Dependabot deletes its branch on close, and a PR whose head
branch is gone cannot be reopened. `respx>=0.23.1` was lost that way and had to
be re-applied by hand.

[dbc9147]: https://github.com/dependabot/dependabot-core/issues/9147

## Conflict handling

A conflict now means one thing: both sides genuinely changed the same lines
since a base git derived from real ancestry.

This is a simplification worth noting rather than passing over. Under the
recorded marker a conflict had *two* readings — a real disagreement, or a
marker that had fallen behind what `integration` held — and the diagnostic had
to present both, because resolving a conflict that does not exist is its own
way to lose work. With the base derived rather than recorded, the second
reading is gone.

There are two conflict paths, and they are not interchangeable.

**A conflict on an open sync PR** (`mergeable_state=dirty`). Auto-merge is
disabled and `sync-conflict-needs-hand-merge` applied, both attempted
independently so a failure in one does not skip the other. The label is the
durable half: it survives to the next run and is what stops a re-arm once the
resolution makes the PR clean. If either fails, the job says which and tells
the resolver **not** to proceed, rather than printing instructions that invite
a push it cannot protect.

**A conflict while building the branch**, before any PR exists. This exit
cannot use the label at all — there is nothing to label — so the instruction
deliberately sends the resolver to *their own branch*, not `$SYNC_BRANCH`. A
PR opened from `$SYNC_BRANCH` is indistinguishable from one this job opened:
the next run adopts it, finds it clean and unlabelled, and arms auto-merge,
merging a hand-written resolution unreviewed. A branch of their own is never
adopted.

`$SYNC_BRANCH` is protected regardless of which instruction is followed. The
job force-pushes it only when the remote tip is one it could have produced —
contained in the merge just built, or identical in tree (its own earlier build,
differing only by commit timestamp, left by a failed `gh pr create`) — and the
push uses `--force-with-lease` against the tip it fetched, so anything pushed
between that fetch and the push is rejected rather than overwritten.

## Tokens

A **GitHub App** installed on this repository is a **prerequisite for the first
sync**, not a convenience, and its installation must hold Workflows write. That
sync carries `integration-sync.yml` itself plus a change to
`integration-promote.yml`, and `GITHUB_TOKEN` may not create or update anything
under `.github/workflows/`. The token is passed to `actions/checkout`, not only
exported as `GH_TOKEN`: checkout persists whichever token it used as the git
credential for every later push, and `GH_TOKEN` authenticates only the `gh` CLI.

The installation needs Contents, Pull requests, Issues **and Workflows** write.
Issues because labels are issues-scoped even on a PR, and
`sync-conflict-needs-hand-merge` is the only thing keeping auto-merge off a
hand-resolved conflict. Workflows because Contents does not cover
`.github/workflows/`: a token without it hits exactly the push rejection
described above, so a setup that lists only the first three produces the very
failure this section diagnoses. The workflow's own `permissions:` block grants
the three that apply to it (`GITHUB_TOKEN` cannot be given Workflows at all,
which is why the app is the prerequisite).

### Why an app rather than a PAT

This started as `INTEGRATION_PROMOTE_TOKEN`, a personal access token. The
replacement is not cosmetic: a PAT owned by a repository admin quietly disarms
the protection on the one PR here that reaches production.

`main-branch-protection` requires one approving review — raised from zero after
PR #134 merged unreviewed — and lists a single bypass actor, the repository
owner, with `bypass_mode: pull_request`. Today the weekly promotion PR is
opened and armed by `github-actions[bot]`, which is not a bypass actor, so that
review genuinely binds. Authenticate the same workflow with the owner's PAT and
the PR becomes theirs, armed and merged as them, and the bypass applies. The
review requirement stops binding on precisely the PR that deploys. An app is a
distinct identity and no ruleset's bypass actor, so the requirement survives.

Two secondary benefits fall out. An installation token cannot lapse the way a
PAT expires — and because the workflows fall back to `github.token` rather than
failing, an expiry would have degraded *silently* back to the weekly click and
the blind push triggers. And the app's bot is not `github-actions[bot]`, so the
runs its pushes start are not `GITHUB_TOKEN`-authored and are not parked.

The cost is that installation tokens expire after an hour and so cannot be
stored as a secret at all. Each job mints its own via
`actions/create-github-app-token`, `sync` and `refresh` included — step outputs
do not cross job boundaries, and `refresh` runs on `always()` even when `sync`
failed. Configuration is split: the Client ID is not secret and lives in the
repository variable `INTEGRATION_PROMOTE_APP_CLIENT_ID`, the private key in the
secret `INTEGRATION_PROMOTE_APP_PRIVATE_KEY`.

That split is load-bearing, not tidiness. Each mint step is guarded by `if:` so
an unconfigured repository still falls back to `github.token` as documented
below — and the `secrets` context **is not available in a step-level `if`** at
all (GitHub allows only `github`, `needs`, `strategy`, `matrix`, `job`,
`runner`, `env`, `vars`, `steps` and `inputs` there). `vars` is available, which
is what makes the Client ID half of the guard expressible directly.

The key half has to be reached indirectly, and it must be reached. The guard
tests **both** values, because a half-configured repository is not a
hypothetical: with the variable set and the secret removed,
`create-github-app-token` rejects the empty `private-key` and **fails the
step**, which fails the job before any `|| github.token` is ever evaluated.
Guarding on the variable alone turns a half-removed configuration into an
outage rather than the documented fallback — the precise opposite of the intent.
`secrets` *is* available in `jobs.<job_id>.env`, so each job reduces the key to
a boolean there (`APP_PRIVATE_KEY_SET`) and the step tests that; `env` is
available in a step-level `if` even though `secrets` is not.

A skipped step yields empty outputs, so
`steps.app-token.outputs.token || github.token` degrades exactly as before.

Separately, and more weakly, the app removes a weekly "Approve and run" click:
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

### One after the marker

Kept out of the table above, which is scoped to the recorded marker and would
stop being readable as history if later failures were filed into it. This one
belongs to the routing that replaced it, and it is here because it shares the
table's moral exactly:

**A red sync PR stalling both branches, silently.** `blocked` fell through to
the re-arm, which is a no-op on an already-armed PR, so the job exited 0 while
the branch kept a failure it could never shed. Closed by routing `blocked`, and
failing the run once a failed check stands with nothing still running that could
clear it. Its cost was a silent one too — an `integration` frozen for two days
and a promotion PR stranded behind it, with every run of this workflow green
throughout.

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
   is the mechanism behind failure (1), and dropping it would retire the
   `integration` half of `refresh`. The promotion PR re-tests the whole batch
   against `main` before anything ships, so it buys little on a staging branch.

   It no longer retires the job, as it would have before the 2026-08-29
   widening: `main` sets the same policy, deliberately, and the `main` half
   would remain. Dropping it on `main` too is *not* on this list — that policy
   is what stops a PR merging green against a base it was never tested on, which
   is worth more on the branch that deploys than the hand-updating it costs.

2. **A merge queue on `main`.** The properly native answer to that cost: per
   GitHub's docs a merge queue "provides the same benefits as the **Require
   branches to be up to date before merging** branch protection, but does not
   require a pull request author to update their pull request branch." It would
   retire the `main` half of `refresh` outright. Not taken, because it is not a
   toggle: every required check would need a `merge_group` trigger, and none of
   this repository's workflows has one, so queued groups would report nothing
   and PRs would sit forever. Worth revisiting if open-PR volume ever makes the
   `update-branch` churn expensive; at one or two PRs at a time it does not pay.

## Runtime/agent document impact

`CLAUDE.md` carries the invariant in its "Key invariants" list, and a scope note
on the auto-merge rule clarifying that it governs PRs opened by hand — the
`--auto` in these two workflows is deliberate and load-bearing.
