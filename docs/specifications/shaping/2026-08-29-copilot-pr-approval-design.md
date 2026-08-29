# Not letting Copilot approve pull requests

Status: decision recorded. The ruleset changes under "What to give Copilot
instead" are proposed, not applied — they are settings on GitHub, not files in
this repository, and nothing in this branch changes them.

## Problem

Every pull request into `main` is reviewed by
`copilot-pull-request-reviewer[bot]` — `copilot_code_review` sits in
`main-branch-protection` and nowhere else, so the Dependabot pull requests that
target `integration` draw no such review — and every one of those reviews ends
in a verdict: `🟢 Approval recommended` or `🟡 Changes recommended`. The same
ruleset requires one approving review. The two facts sit next to each other and
suggest an obvious wiring: let the verdict be the approval.

The pressure behind that is real, and it is worth stating plainly rather than
dismissing. `rickmcphee` is the repository's only collaborator, and the pull
requests are opened under that account — the recent merges into `main` are all
`claude/*` branches authored by the owner. GitHub does not let anyone approve
their own pull request. So on the owner's own work the required review can never
be *satisfied*; it is only ever *waived*, by the owner's bypass entry
(`bypass_mode: pull_request`, recorded in the integration sync design's "Why an
app rather than a PAT"). A gate that is bypassed every single time looks like a
gate in want of an approver, and there is already a reviewer on every one of
them with an opinion to offer.

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
on 2026-08-29, so it is the unfiltered rule set. Bypass actors are not in it —
an unauthenticated read of the ruleset itself returns `bypass_actors: null` — so
the owner's `bypass_mode: pull_request` entry is taken from `CLAUDE.md` and the
integration sync design, which both record it:

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

### The approval would attach to an early head and never move

The `copilot_code_review` rule reviews a pull request when it opens, and
`review_on_push: true` adds a fresh review on each push to it afterwards. The
first review therefore lands on whatever head the pull request opened with,
which is rarely the head that merges.
`dismiss_stale_reviews_on_push: false` means no later push clears that review,
and `require_last_push_approval: false` means the final push needs no approval of
its own. An approval granted to an early head therefore satisfies the rule for
every head after it, including ones pushed in answer to Copilot's own
objections.

PR #233 is the worked example, and it is not a contrived one — it is a routine
copy change that merged the same afternoon:

| Time | Commit | Copilot verdict |
| --- | --- | --- |
| 18:18:57 | `e17f096` | 🟢 Approval recommended |
| 18:23:59 | `7b98c1b` | 🟡 **Changes recommended** — "Rejected items can receive positive recommendation text that is displayed in unfiltered views" |
| 18:25:17 | `46e9bbf` | 🟢 Approval recommended |
| 18:28:43 | `89a5cc6` | 🟢 Approval recommended |
| 18:33:15 | | merged |

With approvals enabled, the 18:18 review unlocks the merge five minutes before
Copilot finds the bug at 18:23, and fourteen before the merge actually happens.
The exposure is that window rather than a permanent grant — a later
`REQUEST_CHANGES` from the same reviewer would supersede its own approval — but a
window is all it takes, and #134 went from green CI to merged in two seconds.
What `dismiss_stale_reviews_on_push: false` adds is that no *push* closes the
window either, so an approval also carries across the commits written to answer
the objection. Either way the gate can be satisfied by a judgment its author is
in the middle of revising.

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
*not* a bypass actor, so `main`'s required approving review is the only *human*
gate between a week of batched Dependabot updates and a production Fly deploy.
The status checks in the ruleset above gate it too, but passing them involves
nobody. It is the single case in this repository where the review requirement
genuinely binds rather than being waived — and the waiver is total, because a
bypass actor bypasses the `pull_request` rule entire rather than its approval
count alone.

The integration sync design spends its "Why an app rather than a PAT" section on
precisely this: a personal access token owned by the bypass actor would make the
promotion pull request *theirs*, the bypass would apply, and "the review
requirement stops binding on precisely the PR that deploys." Replacing the PAT
with an app was work done to keep that one approval meaningful. An
auto-approving reviewer gives away for free what that change refused to give
away — and worse, because it arrives on schedule and needs nobody to be present.

### It would be an AI approving an AI, with no third party anywhere

The recent merges into `main` come from `claude/*` branches. The reviewer would
be a model and so would the writer of the code. The human account would still be
on the pull request as its opener and its merger — which is what makes this hard
to see rather than what makes it harmless. What would be missing is not the name
but the judgment: no person would have read the change and said so. Whatever the
merits of model review — and this repository has good evidence for them, below —
an approval is a claim that someone other than the author accepted the change.
Two agents in a loop do not make that claim true; they make it unfalsifiable.

### It does not know what this repository's rules are about

Copilot reviews the diff, and several of the invariants that actually break
things here are not in one. The AI-attribution trailers live in commit messages;
the pre-PR spec-drift check is about documents the diff does *not* touch; whether
auto-merge is armed is pull request state rather than content. A reviewer reading
the patch cannot certify any of those three. Others are visible in principle and
simply not what it is looking for — an inventory count arrives on an added line,
and so does an edit to `VERSION` in `backend/version.py`. Either way, a green
tick would be read — by a human skimming, later — as though they had been
checked.

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

Changes to `main-branch-protection`, none of which grants any bot an approval:

1. **`required_review_thread_resolution: true`.** Copilot's *inline* findings
   then block any non-bypass merge until each is explicitly resolved. PR #233's
   18:23 finding was an inline comment on `backend/recommendations_prompt.md:9`,
   so it is the *kind* of finding this reaches — though not that merge, which
   the owner performed and the bypass would have waived along with everything
   else in the rule. Two narrowings apply here, not one, and both are worth
   stating rather than glossing. The second: a finding Copilot emits only in the
   review *body* has no thread to resolve and stays advisory. `CLAUDE.md`
   already draws that line where it notes a suppressed comment carries no
   `comment_id` to reply to. Copilot's own reviews of this design are the
   demonstration: over its rounds on this pull request most findings arrived
   suppressed into the body, and one round raised nothing inline at all. The
   body is also where its sharpest findings landed — the bypass scoping above
   among them, and the correction that this document had its own timeline
   wrong. Thread resolution would have reached none of those. So this binds part
   of a review, not a review.

2. **`dismiss_stale_reviews_on_push: true`, with `require_last_push_approval:
   true` alongside it.** Both close today's gap, in which an approval on the
   promotion pull request survives every later push to it — and neither depends
   on Copilot. They are not two names for one control, though, and the safer of
   them is the first. GitHub's own framing settles the order: dismissing stale
   reviews is what it recommends "if you are concerned about pull requests being
   'hijacked'", unapproved content added to an approved pull request, which is
   exactly the failure this section describes; requiring approval from someone
   other than the last pusher is "a compromise that avoids the need to dismiss
   all stale reviews", leaving prior approvals standing. What the compromise
   adds is a condition dismissal does not carry — that whoever approves the most
   recent push is not the person who made it. On the promotion pull request, a
   bot pushes and a person approves, so that condition is worth having. Take
   both and there is no gap between them.
   See <https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches>.

All of these are scoped by the same bypass that shapes everything else here, and
saying so is the difference between a proposal and an overclaim. The owner's
entry sits on the *ruleset*, not on any one of its rules, so what it waives is
the `pull_request` rule entire rather than the approval count alone — and every
setting named above lives in that rule. The sync design describes the same entry
as "letting them merge one that has not met the required approving review"
because the approval is the case it cared about, not because the rest of the
rule survives it. So none of them binds a merge performed through the bypass:
unresolved threads, an undismissed stale approval and an unapproved last push
would all be waivable exactly the way the missing approval already is. What they
bind is every merge that does not use it — the promotion pull request above all,
opened as it is by a bot that is not a bypass actor.

That is a narrower claim than "the review becomes binding," and it is still the
one worth having: the promotion path is the unattended one. On the owner's own
pull requests these are prompts rather than gates — the correct shape for a rule
whose whole purpose is to make a person look.

And keep merging the owner's own pull requests by bypass. That is not a
workaround to be engineered away — it is an accurate record of what happened.
One person looked and took responsibility. An automatic approval would replace a
deliberate act by a human who read the diff with a silent one by a process that
merged it, and would be indistinguishable in the log from the former.

### The cost, stated

`review_on_push: true` means every push draws a fresh review, so on a push whose
review carries an inline finding, thread resolution adds a step per push rather
than per pull request. On a push whose review carries none — or only body
feedback, which these rounds suggest is the ordinary case — it adds nothing,
because there is no thread to resolve. The weekly promotion pull request targets
`main`, so it is reviewed even though the individual Dependabot pull requests
into `integration` are not, and the batch grows a resolution step on any round
that produces an inline finding — on a pull request whose whole purpose is to be
cheap. That is a real cost where it lands, and the correct one to pay: the batch
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
- The ruleset gaps found while answering it —
  `required_review_thread_resolution`, `dismiss_stale_reviews_on_push` and
  `require_last_push_approval` — are written down whether or not they are acted
  on. All are live today, and none depends on Copilot ever gaining an approval.
- If the promotion pull request is ever observed merging with an approval older
  than its head commit, that is the stale-approval gap above, not a new one.
