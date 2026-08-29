# Not letting Copilot approve pull requests

Status: decision recorded. The ruleset changes under "What to give Copilot
instead" are proposed, not applied — they are settings on GitHub, not files in
this repository, and nothing in this branch changes them.

## Problem

Every pull request here is reviewed by `copilot-pull-request-reviewer[bot]`, and
every one of those reviews ends in a verdict: `🟢 Approval recommended` or
`🟡 Changes recommended`. `main-branch-protection` requires one approving
review. The two facts sit next to each other and suggest an obvious wiring:
let the verdict be the approval.

The pressure behind that is real, and it is worth stating plainly rather than
dismissing. `rickmcphee` is the repository's only collaborator, and the pull
requests are opened under that account — the recent merges into `main` are all
`claude/*` branches authored by the owner. GitHub does not let anyone approve
their own pull request. So on the owner's own work the required review can never
be *satisfied*; it is only ever *waived*, by the owner's bypass entry
(`bypass_mode: pull_request`, recorded in the integration sync design's "Why an
app rather than a PAT"). A gate that is bypassed every single time looks like a
gate in want of an approver, and there is already a reviewer on every pull
request with an opinion to offer.

This document is why that wiring is wrong, and what to do with the impulse
instead.

## What Copilot can actually do today

No such setting exists. Copilot code review always submits a **Comment** review
— GitHub's documentation is explicit that it leaves "a 'Comment' review, not an
'Approve' review or a 'Request changes' review", that its reviews "do not count
toward required approvals", and that it cannot satisfy CODEOWNERS or a
required-reviewers rule.
See <https://docs.github.com/copilot/using-github-copilot/code-review/using-copilot-code-review>.

The repository's own data agrees: every Copilot review on PR #233 carries
`state: "COMMENTED"`, with the verdict living in the review *body*. The green
tick is a recommendation addressed to a human, not a review state.

So this is a decision recorded ahead of the switch, for two cases. If GitHub
ships the toggle, the answer is no. And if the same effect is reached the way it
can be reached today — a GitHub App or an Action that submits an `APPROVE` on
Copilot's behalf, which does count — the answer is also no, for the same
reasons. Nothing below depends on *who* automates the approval.

## The ruleset as it stands

Read anonymously from `GET /repos/rickmcphee/discogs-browser/rules/branches/main`
on 2026-08-29, so it is the unfiltered rule set — bypass actors are not
reflected in it:

```
ruleset 18814478 (main-branch-protection)
  deletion, non_fast_forward
  copilot_code_review:    review_on_push: true, review_draft_pull_requests: false
  pull_request:           required_approving_review_count: 1
                          dismiss_stale_reviews_on_push: false
                          require_last_push_approval: false
                          required_review_thread_resolution: false
                          require_code_owner_review: false
                          require_extra_approval_for_unattributed_changes: false
                          allowed_merge_methods: ["squash"]
  required_status_checks: Backend tests, Frontend tests, CodeQL
                          strict_required_status_checks_policy: true
  code_scanning:          CodeQL, high_or_higher / errors
  code_quality:           errors
```

Two of those lines are the whole argument, and neither is about Copilot:
`dismiss_stale_reviews_on_push: false` and `require_last_push_approval: false`.

(`allowed_merge_methods: ["squash"]` confirms the check `CLAUDE.md` asks for
under the integration-branch invariant — the repo-wide "Allow merge commits"
toggle that `integration` needs has not loosened `main`.)

## Why approval is the wrong power to hand it

### The approval would attach to the first push and never move

`review_on_push: true` means Copilot reviews the moment a branch is pushed —
the first commit, before the work is finished. `dismiss_stale_reviews_on_push:
false` means no later push clears that review, and `require_last_push_approval:
false` means the final push needs no approval of its own. An approval granted to
the first commit therefore satisfies the rule for every commit after it,
including ones written in response to Copilot's own objections.

PR #233 is the worked example, and it is not a contrived one — it is a routine
copy change that merged the same afternoon:

| Time | Commit | Copilot verdict |
| --- | --- | --- |
| 18:18:57 | `e17f096` | 🟢 Approval recommended |
| 18:23:59 | `7b98c1b` | 🟡 **Changes recommended** — "Rejected items can receive positive recommendation text that is displayed in unfiltered views" |
| 18:25:17 | `46e9bbf` | 🟢 Approval recommended |
| 18:28:43 | `89a5cc6` | 🟢 Approval recommended |
| 18:33:15 | | merged |

With approvals enabled, the 18:18 review unlocks the merge fifteen minutes
before Copilot finds the bug at 18:23 — and because stale reviews are not
dismissed, Copilot's own retraction cannot take the approval back. The gate ends
up satisfied by a judgment its author revised.

### It re-opens the hole the rule was raised to close

`required_approving_review_count` was raised from zero after PR #134 merged
unreviewed: CI went green, auto-merge fired, the PR merged in two seconds, and
Copilot's review posted twenty seconds later. `CLAUDE.md` justifies the raise as
closing that race "at the platform level so no PR can merge unattended
regardless of what triggers merge."

An approving reviewer that fires automatically on push removes exactly the term
that makes that sentence true. Green CI, an automatic approval, and auto-merge
compose back into an unattended merge — #134 again, with the approval
requirement present but self-satisfying.

### The one pull request that deploys is the most exposed

`.github/workflows/integration-promote.yml` opens the weekly promotion pull
request from `integration` into `main` and arms it with `gh pr merge integration
--auto --squash`. That pull request is opened by a bot which is deliberately
*not* a bypass actor, so `main`'s required approving review is the only thing
between a week of batched Dependabot updates and a production Fly deploy. It is
the single case in this repository where the review requirement genuinely binds
rather than being waived.

The integration sync design spends its "Why an app rather than a PAT" section on
precisely this: a personal access token owned by the bypass actor would make the
promotion pull request *theirs*, the bypass would apply, and "the review
requirement stops binding on precisely the PR that deploys." Replacing the PAT
with an app was work done to keep that one approval meaningful. An
auto-approving reviewer gives away for free what that change refused to give
away — and worse, because it arrives on schedule and needs nobody to be present.

### It would be an AI approving an AI, with no third party anywhere

The recent merges into `main` come from `claude/*` branches. The reviewer would
be a model, the author would be a model, and the sole human account would appear
nowhere in the record. Whatever the merits of model review — and this repository
has good evidence for them, below — an approval is a claim that someone other
than the author accepted the change. Two agents in a loop do not make that claim
true; they make it unfalsifiable.

### It does not know what this repository's rules are about

Copilot reviews the diff. The invariants that actually break things here are
conventions it has no visibility into: never writing an inventory count into a
document, the AI-attribution trailers required on every commit, `VERSION` in
`backend/version.py` being derived and never edited, the pre-PR spec-drift check
across both spec trees, the standing ban on auto-merge. A green tick from a
reviewer that cannot see any of those would be read — by a human skimming, later
— as though it had.

`required_review_thread_resolution: false` sharpens this: an approval today
would not even require Copilot's *own* inline comments to be resolved first. The
tick and the unaddressed finding can coexist on the same pull request.

## What Copilot is worth here, which is a lot — as a "no"

None of the above is an argument that Copilot's review is weak. This
repository's specifications are full of findings it caught that the original
implementation pass missed, and that were worth writing down: the SSE filtering
gap deferred out of PR #158, the identity churn on PR #171, the price-sort
correctness bugs on PR #172 that the original test pass had not caught, the
`getStockArtists` declaration on PR #189, the `stock_schedule` gap in the
price-drop work, the write-counter race in the refresh-click work. The
`fly-multi-machine` design carries a run of amendments attributed to Copilot's
review, one of them architectural.

PR #217 exists for no other reason: "Address the #210 review findings that
landed after it merged."

That is a reviewer whose objections are load-bearing — and objections are
exactly what the current configuration leaves non-binding, while the thing being
proposed for promotion is its assent. The value is in the "no", and the "no" is
the half that has no force today.

## What to give Copilot instead

Two changes to `main-branch-protection`, neither of which grants any bot an
approval:

1. **`required_review_thread_resolution: true`.** Copilot's inline findings then
   block the merge until each one is explicitly resolved. This is the change
   that converts its review from advisory to binding, and it is the one that
   would have caught the 18:23 finding on PR #233 regardless of what any
   approval said.

2. **`require_last_push_approval: true`.** The most recent push must be approved
   by someone who did not push it. This closes the stale-approval gap on its
   own terms, independently of Copilot: today an approval on the promotion pull
   request survives any subsequent push to it. `dismiss_stale_reviews_on_push:
   true` covers similar ground; `require_last_push_approval` is the tighter of
   the two here, because approvals on this repository are rare enough that
   dismissing them is nearly a no-op while requiring a fresh one is not.

And keep merging the owner's own pull requests by bypass. That is not a
workaround to be engineered away — it is an accurate record of what happened.
One person looked and took responsibility. An automatic approval would replace a
deliberate act by a human who read the diff with a silent one by a process that
merged it, and would be indistinguishable in the log from the former.

### The cost, stated

`review_on_push: true` means every push draws a fresh review and can open fresh
threads, so thread resolution adds a step per push rather than per pull request.
The weekly promotion pull request is reviewed too, so the Dependabot batch grows
a resolution step it does not have today — on a pull request whose whole purpose
is to be cheap. That is a real cost and it is the correct one to pay: the batch
is the change that reaches production.

## Non-goals

- Removing the owner's bypass entry, or requiring review on the owner's own
  pull requests by some other route.
- Turning off Copilot code review, or changing `review_on_push`.
- Touching `--auto` in `integration-promote.yml` or `integration-sync.yml`. The
  auto-merge ban in `CLAUDE.md` is scoped to hand-opened pull requests and
  explicitly does not reach those workflows; nothing here widens it.
- Revisiting whether `main` should require an approving review at all.
- `require_extra_approval_for_unattributed_changes` is left alone. It is a
  separate question about commit attribution and deserves its own read.

## Consequences

- The answer to "should Copilot approve pull requests" is recorded as no, with
  the mechanism rather than the sentiment as the reason, so that a future
  session meeting the toggle does not have to re-derive it.
- The two ruleset gaps found while answering it —
  `required_review_thread_resolution` and `require_last_push_approval` — are
  written down whether or not they are acted on. Both are live today and
  neither depends on Copilot ever gaining an approval.
- If the promotion pull request is ever observed merging with an approval older
  than its head commit, that is this gap, not a new one.
