# Handing a pull request back to Claude

Status: implemented in `.github/workflows/claude.yml`.

## Problem

The round trip does not close. `@claude` on an issue works — the workflow
subscribes to issue events, the action does the work and pushes a `claude/*`
branch, and a pull request is opened from it. `copilot-pull-request-reviewer[bot]`
then reviews that pull request, because `copilot_code_review` sits in
`main-branch-protection`, and its findings are load-bearing: the
[Copilot approval design](2026-08-29-copilot-pr-approval-design.md) lists a run
of them that the original implementation pass missed, and PR #217 exists for no
reason other than answering review findings that landed after #210 merged.

And there the trip ends. Asking the author of the change to answer the review is
the obvious next move, and it is the one thing the workflow cannot be asked to
do. Every gesture a reader would reach for is a no-op, each for a different
reason, and none of them reports anything:

| Gesture | Event that fires | Why nothing happens |
| --- | --- | --- |
| `@claude` in the pull request conversation | `issue_comment` | The clause carried `!github.event.issue.pull_request` |
| `@claude` replying under a Copilot inline finding | `pull_request_review_comment` | Not subscribed |
| The `claude` label on the pull request | `pull_request: labeled` | Not subscribed — and `issues` never fires for a pull request |

The third is the one that misleads, because `label_trigger: "claude"` is set on
the action and the label gesture demonstrably works on issues. It does not carry
over: GitHub's `issues` event covers issues only, and labelling a pull request
emits `pull_request: labeled`.

The fallback is to open a Claude Code session on the branch and drive the review
from there, which works and is what `CLAUDE.md` describes under **Pull
requests**. It is also a context switch away from the tab the review is in, for
work that is often a one-line answer to a one-line finding.

## What the narrowing was protecting

The pull request triggers were withheld when this workflow was added, and the
[session-start hook design](2026-08-29-session-start-hook-pr-safety-design.md)
names re-enabling them as a non-goal deserving "its own review." This is that
review.

The concern is exact and worth restating rather than paraphrasing: this
repository is public, so anyone may open a pull request from a fork; a run
triggered by a comment or review event executes with the base repository's
secrets rather than a fork's reduced permissions; and this job holds
`contents: write` together with `CLAUDE_CODE_OAUTH_TOKEN`. Claude would be
reading a stranger's diff and a stranger's comment text — both attacker-supplied
— with a write token in scope. The action's own security documentation names
prompt injection through pull request content as a first-class risk.
See <https://github.com/anthropics/claude-code-action/blob/main/docs/security.md>.

One concrete hole underneath that has since been closed independently: the
`SessionStart` hook used to invoke `scripts/cloud-setup.sh`, which stays at the
pull request head, so a maintainer's `@claude` would have run a contributor's
shell before Claude read a line of the prompt. That is fixed. But the hook was
an instance of the risk, not the whole of it, and the non-goal says so.

## The gates

There are two, because there are two ways untrusted content reaches Claude, and
the first draft of this design shipped only the first gate and claimed it covered
both. Copilot caught that on the pull request. Keeping the correction visible
here rather than quietly rewriting the claim, because the shape of the mistake is
the useful part: a gate on *code* was described as if it were a gate on
*everything a pull request carries*.

### The code gate: a same-repository head

The job's first step resolves the pull request's head repository and skips every
step after it unless that head is in this repository. A branch pushed by the
action or by the owner qualifies; a fork's head never does. Pushing to a branch
in this repository requires write access, so the diff, and the pull request body
that opens it, are the work of someone already trusted.

This costs the flow above nothing, which is what makes it the right shape rather
than a compromise. The pull requests that draw a Copilot review are opened from
`claude/*` branches that the action pushed into this repository. There is no
fork anywhere in the loop.

What is given up is a maintainer's ability to hand a fork contributor's pull
request to Claude. That is the case with the wide surface, and it is not the
case this exists to serve.

### The discussion gate: an actor allowlist

A same-repository head says nothing about who may *talk* on the pull request.
This repository is public, so anyone with a GitHub account can comment on one —
including the fork contributor whose head was just refused. The action includes
every actor's comments by default: `filterCommentsByActor` returns the array
untouched when both filter lists are empty. So a stranger's prose would be handed
to Claude the moment a maintainer typed `@claude`, with `contents: write` and the
OAuth secret in scope. That is the same prompt-injection surface the head gate
was raised against, reached by prose instead of by code.

`include_comments_by_actor` closes it. Humans are narrowed to the actor who
triggered the run — already restricted to `OWNER`, `MEMBER` or `COLLABORATOR` by
the job's `if:` — and to the repository owner, whose pull request it usually is.

The bots on the list are the ones whose output is the point, and getting that
list right is less obvious than it looks. **Copilot posts under two identities.**
The review body, carrying the verdict, comes from
`copilot-pull-request-reviewer[bot]`; the inline comments, which are the actual
findings, come from `Copilot`. An allowlist naming only the first — the name
`CLAUDE.md` uses throughout, and the one the checks tab shows — would have
silently discarded every finding this feature exists to act on, and the failure
would have looked exactly like Copilot having nothing to say. Both are listed in
bare and `[bot]`-suffixed form, because `actorMatchesPattern` compares exactly
and `resolveActorName` appends the suffix only when GraphQL returns a bot login
bare, which is not something REST or the UI shows.

Blanket `*[bot]` was the alternative, and is defensible — only the owner can
install an App, so only owner-installed Apps can comment. It is not taken,
because an enumerated list says which bots this flow actually depends on, and a
future reader can check it against what the action received.

### What is not claimed

Neither gate is total, and the first draft's error was claiming otherwise.

The actor filter narrows Claude's *starting context*. It does not stop Claude
fetching a filtered comment itself once running, because the action grants it
GitHub tooling and a stranger's comment is still reachable through the API. What
the filter buys is that untrusted prose is no longer placed in front of Claude
automatically, on every run, without anyone choosing to look at it — defence in
depth, not a boundary.

The same surface exists on issues, which this workflow already allowed before
this change: a public issue takes comments from anyone, and `@claude` on it
ingests them with the same token in scope. This change does not introduce that
class, and does not fix it there either; the allowlist applies to every event the
workflow subscribes to, issues included, so it happens to narrow that path too.

### Why a step and not another clause in `if:`

`if:` would be the cheaper gate — a skipped job never starts, and never enters
the concurrency group. Two of the three events could use it: the
`pull_request_review_comment` and `pull_request_review` payloads both carry
`pull_request.head.repo.full_name`.

`issue_comment` does not, and it is the most important of the three, because it
is the comment box under the conversation. Its payload describes the *issue*
side of a pull request; `issue.pull_request` carries a handful of URLs and no
head repository at all. The answer has to be asked for.

So the check is a step, and all three events go through it rather than two of
them taking a shortcut. A second mechanism reaching the same decision by a
different route is a thing to keep in step later, and the saving — a job that
starts and immediately stops — is small. The step runs *before* the checkout, so
the ordering still holds the real guarantee: on a fork's pull request nothing is
fetched, because the gate is reached before anything reads the head.

It fails closed in both directions available to it. A deleted fork leaves
`head.repo` null, which is not this repository, so the run skips; and `set -euo
pipefail` means an API error fails the step rather than falling through to the
action.

A skip is silent on the pull request itself and writes its reason to the job
summary. Silence is deliberate: on a fork pull request the alternative is a red
X or a bot comment on every `@claude` a maintainer types, and the reason belongs
where someone who went looking for it will be.

## Why Copilot does not trigger this itself

`pull_request_review: submitted` is subscribed, so the wiring to have Copilot's
review wake Claude directly is one input away — `allowed_bots` naming
`copilot-pull-request-reviewer[bot]`. It is deliberately not set. The action
defaults `allowed_bots` to empty, and the `author_association` pre-filter admits
only `OWNER`, `MEMBER` and `COLLABORATOR`, so a bot's review satisfies neither
gate.

The reason is the one the [Copilot approval design](2026-08-29-copilot-pr-approval-design.md)
gives at length in a different register. That document declines to let Copilot's
verdict *approve* a pull request, partly because "two agents in a loop" cannot
make the claim an approval makes. Letting Copilot's verdict *dispatch* Claude
builds the same loop from the other end: a model writes the change, a model
objects, a model answers the objection, and the push that results is reviewed by
the model that objected. Nobody has read anything.

The gesture is cheap — a comment — and keeping it manual keeps a person in the
position of having decided which findings are worth acting on. That is also the
honest division of labour: Copilot's review body regularly carries findings with
no inline thread behind them, and some of its findings are wrong. Choosing among
them is the part worth a human.

This is a default rather than a prohibition. If it is revisited, the thing to
weigh is that `review_on_push: true` means every push draws a fresh review, so a
bot trigger would not be one dispatch per pull request but one per round, with
Claude's own push drawing the next review.

## A concurrency bug this fixes on the way past

The group was `claude-${{ github.event.issue.number }}`. The review events carry
`pull_request`, not `issue`, so on either of them that expression yields
`claude-` — one group shared by every pull request in the repository, in which
each new request cancels the last. The group now reads whichever of the two is
present. This is not a pre-existing bug; it would have been introduced by
subscribing to the review events without noticing.

## Non-goals

- Running Claude on pull requests from forks, by this or any other route.
- Letting any bot, Copilot included, trigger a run.
- Changing what the action does once it starts — the checkout, the tools, the
  branch it pushes to are all as they were on issues.
- Revisiting `copilot_code_review` or anything in `main-branch-protection`. The
  ruleset gaps recorded in the Copilot approval design are still open and still
  independent of this.
- Auto-merge, in any form. `CLAUDE.md`'s ban is untouched: a run triggered this
  way pushes to the pull request and stops, exactly as a session driving the
  same branch would.

## Consequences

- `@claude` in a pull request conversation, or replying `@claude` under one of
  Copilot's inline findings, now reaches Claude on any pull request whose branch
  lives in this repository.
- A fork contributor's pull request is inert to every one of those gestures, and
  a maintainer who tries will find the reason in the job summary rather than on
  the pull request.
- Comments from anyone outside the allowlist no longer reach Claude's starting
  context on any event this workflow subscribes to, issues included.
- If Copilot's inline findings ever stop reaching Claude, the allowlist is the
  first place to look: it pins two identities that Copilot's own tooling
  presents under one name.
- The `claude` label remains an issues-only gesture. Nothing here gives it a
  pull request meaning, and `pull_request: labeled` stays unsubscribed.
- Claude answering a review it can be asked for does not change who is
  accountable for the merge. The required approving review on `main`, and the
  owner's bypass of it, are exactly as they were.
