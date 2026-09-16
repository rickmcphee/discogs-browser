# Closing out Copilot's review findings

Status: convention adopted. The rule itself lives in `CLAUDE.md`'s "Pull
requests" section; this document is why it is shaped the way it is. Nothing here
changes a setting on GitHub.

## Problem

The owner wants one thing from a review round that the pull request does not
currently give up: **when is the agent working this branch done with what Copilot
said?**

Nothing on the pull request answers that. `copilot_code_review` posts a review
per push — most carrying findings, some clean — and the record of what happened
to each finding is prose scattered down a timeline that interleaves the owner's
own reviews, CI, and pushes. Reading it is the only way to know, and reading it
is the cost the signal is supposed to remove.

GitHub already has the control that would say it — a review thread is either open
or resolved, and the unresolved count sits at the top of the Files tab. It was
simply never assigned a meaning here.

## What the practice already is

Audited on 2026-09-16, across the twelve most recently closed pull requests into
`main` (371, 366, 367, 351, 352, 353, 344, 337, 338, 339, 331, 333):

- Every inline review thread in that window was opened by
  `copilot-pull-request-reviewer` — no human opened one.
- Every one of them is resolved. There is no open Copilot thread anywhere in the
  window.
- All but one carry a reply naming the fixing commit before the resolve. The
  exception is on 331, a duplicate of a finding raised again a round later that
  did get a reply.

So the convention below is mostly a transcription of what sessions have already
been doing. Writing it down is still worth it, and the reason is the usual one:
an undocumented habit holds exactly as long as each session happens to have it.

What it guards against is worth stating precisely, because the obvious version is
wrong. A session that simply ignores the review does *not* produce a
false "everything is handled" — Copilot opens the threads, so ignoring them
leaves them open, and the signal reads correctly as unfinished work. Silence is
the safe failure here. The two ways the signal can read "handled" when it is not
are narrower: **a thread resolved with nothing said**, which is
indistinguishable from an addressed one, and **a finding that never had a thread
to leave open** — the body-only channel below. Both of those are what the rules
that follow are built around.

## The half the signal misses

Copilot puts findings in two places, and only one of them can be resolved.

Findings it is confident about arrive as **inline review comments**, which carry
a thread, a comment id, and a resolve state. Findings below that bar arrive
**inside the review body**, under a `### Suppressed comments (N)` heading, as
`**path:line**` entries — sometimes under a further `**Previously missed (N)**`
sub-heading for findings in code that has not changed since the last review.
Those have no thread, no comment id, no resolve state, and nothing to reply to.
They exist only as text in the body's `<details>` block.

In the audited window, **the body-only findings outnumbered the inline ones** —
roughly 120 suppressed against 105 inline threads, with most reviews carrying at
least one. They are not a lesser class of finding either. From PR 337's own
review bodies, suppressed:

> `resolve_cover_image()` assumes `featured_image` is a mapping [...] so optional
> artwork aborts the whole refresh and leaves all prices stale.

> This left boundary is ASCII-only, even with `IGNORECASE`. For example,
> `_is_vinyl('ÉLP CD')` matches the embedded `LP` and returns `True` before the
> explicit CD veto.

That is the trap this design exists to avoid. A convention that says "resolve
every Copilot comment" and stops there produces a pull request with zero
unresolved threads while the larger channel of findings has no state at all —
and the owner, reading the signal the convention taught them to read, would take
it to mean every finding was dealt with. The signal would be worse than no
signal, because it would be confidently wrong in the direction of "handled."

So the convention has to cover both channels or it cannot mean what it is being
adopted to mean. Since suppressed findings have nowhere to be resolved, they are
accounted for in a single running comment on the pull request, edited in place
each round rather than re-posted, listing each finding and its disposition.

## What a resolve is allowed to mean

**Reply first, then resolve.** A resolved thread with no reply is
indistinguishable from an addressed one and reads as addressed, so it is worse
than leaving it open. The reply is the evidence; the resolve is only the index
entry pointing at it.

**Resolve after the fix is pushed**, so the commit named in the reply is on the
branch when the owner goes looking for it.

**Resolved means "dealt with", not "agreed".** Declining a finding is a
legitimate disposition — the reply carries what was checked and why the finding
does not hold, and the thread still resolves. Leaving a thread open to mean "I
disagree" would collide with the one meaning the signal has.

**The owner can unresolve.** That is what makes this two-way: a resolve is the
agent's claim that it is done, and reopening the thread is the owner rejecting
the claim. It is a cheaper objection than a paragraph, which is the point.

**An outdated thread still needs resolving.** `is_outdated: true` means the code
moved under the comment, not that anyone answered it.

**Nothing is closed out on an unreviewed head.** Both halves of the signal are
claims about one particular commit — the one Copilot last reviewed — and a push
inverts them silently. The threads resolved a moment ago stay resolved, the
ledger still reads complete, and neither has seen the new head; if that head's
review then comes back clean or with only body-only findings, no thread ever
opens to correct the impression. The window is small but it is exactly the
window in which someone glances at the pull request and concludes the agent is
finished.

So the signal is readable only once the current head *has* a review, which is
what the poll in `CLAUDE.md` already establishes — a review whose `commit_id`
matches the head SHA. Until that review exists and its findings are closed out,
the honest answer is "not yet", whatever the thread count says. The ledger
therefore names the review and head commit it covers, so a reader compares that
against the pull request's current head. A named head is preferred to a
pending/complete flag deliberately: the head goes stale by itself and visibly,
where a flag goes stale only if whoever pushed remembered to flip it, and the
whole failure being fixed here is a signal that reads "done" because nobody
updated it.

**The ledger is a closeout record rather than a suppressed-findings appendix,
and its absence has to mean something.** A review can carry body-only findings
and open no thread at all — PR #337's review 5180040053 did exactly that, four
suppressed findings and nothing inline. Before the agent writes anything, such a
pull request has no open thread *and* no ledger: both halves of the signal are
silent while the findings sit unread, and silence is what a reader takes for
"nothing to do."

So the record is written when the first review on a pull request is processed,
whether or not that round produced suppressed findings, and it always names the
review and head it covers. Its absence then says something definite. What a
reader checks, in order:

| State | Reading |
| --- | --- |
| No review on the current head | Not readable yet — the matching review hasn't landed |
| A Copilot review the record does not name | Not processed |
| Record names the newest Copilot review, no open Copilot thread | Done, as of that review |

The unit there is the **review**, not the head, and that correction was forced by
evidence. A head is the obvious proxy for "has the current state been looked
at", and it is wrong, because Copilot can review the same commit more than once
with no push in between. PR #337 has two of them on `b4607e5`: review
5161031901 carrying `🟢 Approved`, and review 5179396360 thirty-eight hours
later carrying `🟡 Changes recommended` and four findings. A test that asks
"is there a review on this head" is satisfied by the first and blind to the
second, so the proxy fails in the direction that matters.

Naming the review subsumes the head comparison rather than adding to it: a
record is stale if any Copilot review exists that it does not name, whether that
review sits on a newer head or on the same one. One test, and the head becomes
informational.

It also bounds what the signal can claim. "Done" means done as of the newest
review seen, never that no further review will arrive — #337's second round
landed a day and a half later. That is a real limit, and stating it is better
than implying a permanence the mechanism cannot deliver.

The cost is one comment on a pull request that would otherwise carry none, which
is the narrow case of a review raising nothing whatsoever. Worth paying: an
ambiguous silence is the expensive half of this, because it is the half a reader
resolves in the wrong direction.

**The owner's own review threads are left alone.** The owner resolving their own
comment means "I am satisfied"; the agent resolving one would mean "I believe I
have satisfied you." Those are different claims, and the distinction is worth
more than the tidiness of a zero.

## Mechanics

Two ids are involved and they are not interchangeable — this is the part that
wastes a session's time if it is not written down.

- **Replying** takes the comment's **numeric id**, the number in its
  `#discussion_r<id>` anchor. REST:
  `POST /pulls/{number}/comments/{comment_id}/replies`, or
  `mcp__github__add_reply_to_pull_request_comment`.
- **Resolving** takes the **thread's GraphQL node id** (`PRRT_…`), which is a
  different object. With `gh`, that is the `resolveReviewThread` mutation via
  `gh api graphql`. Without it — the remote and web sessions have no `gh` —
  `mcp__github__pull_request_read` with `method=get_review_comments` returns each
  thread's `id`, `is_resolved` and `is_outdated` alongside its comments, and
  `mcp__github__resolve_review_thread` takes that `id` directly. No GraphQL query
  needs writing.

Identifying which threads are Copilot's is its own trap, and one this repository
has already been bitten by once.
[`2026-08-29-claude-on-pull-requests-design.md`](2026-08-29-claude-on-pull-requests-design.md)
records that Copilot posts under two identities — the review body from
`copilot-pull-request-reviewer[bot]`, the inline findings from `Copilot` over
GraphQL — and that an allowlist naming only the first discarded every finding
while looking exactly like Copilot having nothing to say. Reading the same data
over REST adds a third spelling: `get_reviews` returns the login suffixed,
`get_review_comments` returns the *same* bot bare as
`copilot-pull-request-reviewer`. An exact-match author filter therefore fails
differently depending on which call produced the data, and it fails silently in
every direction. Match loosely.

Sweep the threads as part of the review poll `CLAUDE.md` already requires, not
before it. `review_on_push: true` means every push draws a fresh review, so the
round that most needs closing out is the one landing on the final head — and the
poll for a review whose `commit_id` matches that head is exactly where it
appears.

An approval from Copilot is not a terminal state and must not be read as one.
PR 337 drew a `🟢 Approved` review and then went on to collect further
`🟡 Changes recommended` rounds on later pushes; 331 drew several approvals among
its rounds. A session that stops at the first green tick stops in the middle.

## Why this must not become a merge gate

`required_review_thread_resolution` is `false` on `main-branch-protection` (read
2026-08-29, recorded in
[`2026-08-29-copilot-pr-approval-design.md`](2026-08-29-copilot-pr-approval-design.md)),
and that document proposes turning it on so Copilot's inline findings block a
non-bypass merge. The convention here and that proposal must not be allowed to
close into a loop, and the reason is the one that document already spends itself
on.

Its argument against letting Copilot hold an approval is that the gate would
become self-satisfying: the thing being gated and the thing satisfying the gate
would both be automatic, and the human the gate exists to summon would never be
called. Resolution has the same shape one step over. If threads must be resolved
to merge, and an agent resolves them as a matter of routine, then the agent
issues its own permit.

Where that actually bites is narrow, and saying so keeps the claim honest. On the
owner's own pull requests the bypass waives the `pull_request` rule entire, so
the setting binds nothing there either way. The one pull request it would bind is
the bot-opened promotion from `integration` into `main` — the one that deploys,
and the one that other document calls "the unattended one."

The carve-out therefore has to be mechanical rather than a statement of intent.
An earlier draft of this document said an agent does not resolve promotion
threads *to clear a merge gate*, and that is unenforceable: with the setting on,
the resolve clears the gate whatever the agent meant by it, and no reader of the
pull request can tell the two apart afterwards. A rule written on motive cannot
bind an action whose effect is identical either way. So it is stated by effect
instead — **if `required_review_thread_resolution` is ever enabled, threads on
the promotion pull request are left for a human to resolve.** An agent still
fixes what the finding names and still replies saying so; it just does not
perform the resolve there, because with that setting on the resolve *is* the
permit. Its completion is then reported by the running ledger comment rather
than by thread state, for the reason set out under Consequences: the open thread
is Copilot's either way, so on that pull request thread state no longer
distinguishes finished from unfinished. While the setting stays off, a resolve carries no permission and the
ordinary rule applies.

Stated generally, because it is the whole reason this stays a convention rather
than a setting: **resolution here is a report to the owner, not a permission to
merge.** The moment the two are wired together, the report becomes a self-issued
permit and stops being worth reading.

## Non-goals

- Turning on `required_review_thread_resolution`, or any other ruleset change.
  That proposal stays where it is, undecided.
- Letting Copilot — or any bot, or any agent — hold an approving review. That
  answer is recorded as no and nothing here revisits it.
- Resolving the owner's own review threads.
- Changing `review_on_push`, or how often Copilot reviews.
- Suppressing or requesting fewer Copilot reviews to reduce the closing-out cost.
  The cost is the work.

## Consequences

- On an ordinary pull request — which is every pull request in this repository
  today — an open **Copilot** thread now carries a meaning it did not before:
  the agent is not finished with it.
- That invariant is scoped, and the two limits on it are different in kind, which
  is worth spelling out because collapsing them into one leaves a hole. The
  first is about *authorship*: the signal is over Copilot's threads rather than
  GitHub's unresolved-conversation badge, because the badge does not distinguish
  authors and the owner's own threads stay open by design. A nonzero badge is
  therefore not by itself evidence of unfinished agent work.
- The second limit survives that filter, and an earlier draft of this document
  missed it for exactly that reason. Should `required_review_thread_resolution`
  ever be enabled, a promotion pull request's threads are left for a human to
  resolve — and those are *Copilot's* threads, so an open one there means the
  agent finished and declined to perform the resolve, not that it is still
  working. Asking "is any Copilot thread open" returns the wrong answer, and no
  refinement of *whose* threads to count fixes it, because the thread is
  Copilot's and open in both the finished and unfinished cases.
- So on that one pull request in that one configuration, thread state cannot
  carry the signal at all, and the running ledger comment carries it instead —
  kept there whether or not a round produced suppressed findings, and holding
  the disposition of every finding rather than only the threadless ones. It is
  already required, already edited in place each round, and already the thing a
  reader consults when resolution is unavailable. Nothing new to maintain: the
  case where the cheap signal fails is the case the expensive one already covers.
- That meaning is only as good as the reply discipline above. A session that
  resolves without replying has not saved the owner a read, it has hidden one.
- Suppressed findings get a written disposition they previously only got when
  someone happened to read the review bodies by hand. Whether the older ones were
  addressed is not recoverable from the API — there is no state field to check —
  so this starts from here rather than being backfilled.
- The closing-out cost scales with pushes rather than with pull requests, because
  each push draws a review. That is a real cost and it lands hardest on the long
  rounds, which are also the ones where the signal is worth the most.
