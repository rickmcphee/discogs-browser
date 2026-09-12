# Merging Dependabot bumps into `integration`

Status: implemented in `.github/workflows/dependabot-auto-merge.yml`.

## Problem

The `integration` arrangement has three moving parts, and only two of them were
ever automated.

1. Dependabot opens bumps against `integration` (`.github/dependabot.yml`).
2. Something merges each bump into `integration`.
3. `.github/workflows/integration-promote.yml` carries the accumulated batch
   into `main` every Tuesday, and `integration-sync.yml` carries `main` back and
   moves stale PR heads forward.

Step 2 was a person clicking Merge. Nothing recorded that, nothing checked it,
and when it stopped happening the system did not report a fault — it reported
success.

That is the part worth keeping: **the failure is silent by construction.**
`sync` keeps `integration` holding `main`'s files. With no bump merged in, the
two branches therefore match, and the promotion's own guard is a tree
comparison:

```
if git diff --quiet origin/main origin/integration; then
  echo "integration and main already match; nothing to promote."
  exit 0
fi
```

So the weekly promotion ran, found the branches identical, and exited 0. Green
check, every Tuesday, promoting nothing. The guard is correct — it exists so a
promotion with genuinely nothing to carry is a no-op rather than an error — but
it cannot distinguish "a quiet week" from "the pipeline's input has been dead
for a fortnight," because both look like two matching branches.

Observed on 2026-09-12: bumps from two consecutive Monday Dependabot runs sat
open against `integration`, the oldest twelve days, nearly all of them
`mergeable_state=clean` — green, up to date, and merely waiting for a click that
was not coming. The last hand-merged bump was 2026-08-27. Every scheduled run of
both workflows in that window succeeded.

## Goals

- A bump that passes `integration`'s required checks lands on `integration`
  without a person in the loop.
- One bump merging does not strand the others.
- Nothing reaches production unattended.
- A dead input stops looking like a quiet week.

## The workflow

`pull_request_target`, filtered to `branches: [integration]`, gated on the PR's
author being `dependabot[bot]` and its head living in this repository. It mints
the same GitHub App token the other two workflows use and runs one command:

```
gh pr merge "$PR" --auto --merge --repo "$GITHUB_REPOSITORY"
```

The decisions in it are not free choices.

**`pull_request_target`, not `pull_request`.** A `pull_request` run raised by
Dependabot gets a read-only token and no access to secrets, so it can neither
arm auto-merge nor mint the app token. `pull_request_target` runs in the base
branch's context with the full token.

The cost is that the job runs with a write token in a workflow the PR head
cannot influence — *provided it never executes head code*. There is deliberately
no `actions/checkout` in this workflow and none may be added; everything it
needs comes from the event payload. This is the same class of boundary as the
one in
[`2026-08-29-session-start-hook-pr-safety-design.md`](2026-08-29-session-start-hook-pr-safety-design.md),
and it fails the same way if crossed: a fork contributor's code running with the
repository's write token.

**The app token, not `GITHUB_TOKEN`.** Auto-merge armed with `GITHUB_TOKEN`
merges as `github-actions[bot]`, and GitHub does not raise new workflow runs
from `GITHUB_TOKEN`-authored events. The merge commit landing on `integration`
would then not trigger `integration-sync.yml`'s push run — and that run's
`refresh` job is the only thing that moves the *remaining* Dependabot PRs
forward after each merge. Without it they sit `behind`
(`integration-branch-protection` sets `strict_required_status_checks_policy`)
and the chain stalls after a single bump.

This was measured rather than assumed. On 2026-09-12, merging one bump by hand
and immediately attempting the next returned:

```
405 Repository rule violations found
2 of 2 required status checks are expected.
```

Each merge genuinely invalidates every other open PR against that base until
something refreshes it. The chain only runs if each merge triggers `refresh`.

The fallback to `github.token` is kept so a half-removed configuration degrades
instead of failing, but it degrades to exactly that stall — worth knowing before
reading a stalled chain as a bug in this workflow.

**`--merge`, never `--squash`.** `integration`'s ruleset pins
`allowed_merge_methods` to merge only; a squash is refused with a 405. A merge
commit is also what keeps `main`'s tip an ancestor of `integration`, so
`git merge-base` stays current by construction — see
[`2026-08-25-integration-branch-sync-design.md`](2026-08-25-integration-branch-sync-design.md).

**Majors are included.** `.github/dependabot.yml` leaves major bumps ungrouped,
and that stays true and still earns its keep: a breaking major fails its own
PR's checks instead of taking a whole batch down with it. What it no longer does
is wait for a human read. The gate is CI — a major that breaks the build never
merges — and the batch still faces a person at the promotion PR into `main`,
which requires an approving review. Both jsdom 29 → 30 and
`@vitest/mocker` 4 → 5 were failing frontend tests on the day this was written,
which is the mechanism working rather than an argument against it.

## What this does not change

Nothing reaches production unattended. `integration` is a staging branch; the
only thing that deploys is a merge to `main`, and that still arrives as the
promotion PR, which `main-branch-protection` holds until a human approves it.
The rule in `CLAUDE.md` against enabling auto-merge on a PR *you* open is
untouched and still applies — its subject is the Copilot-review race on PRs into
`main`, and `integration` carries neither a required approving review nor a
Copilot review rule.

## Failure modes

| Symptom | Cause | Where it surfaces |
| --- | --- | --- |
| Bumps open and green, nothing merging | Workflow not firing — check the author/same-repo guard and that the PR's base is `integration` | No run on the PR |
| One bump merges, the rest sit `behind` forever | Auto-merge armed with `GITHUB_TOKEN`, so no push run triggered `refresh` | App token unconfigured; run logs show the fallback |
| A bump never merges though checks pass | Merge method — a squash attempt is a 405 against `integration` | The arm step's output |
| Promotion says "nothing to promote" week after week | The input is dead, not quiet | Compare against open Dependabot PRs |

An already-mergeable PR cannot be armed — GitHub rejects enabling auto-merge on
one whose checks are all green rather than merging it — so the workflow detects
that response and merges directly. The outcome is identical; only the mechanism
differs.
