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
permit. While the setting stays off, a resolve carries no permission and the
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

- An open **Copilot** thread on a pull request now carries a meaning it did not
  before: the agent is not finished with it.
- The signal is over Copilot's threads specifically, **not** over GitHub's
  unresolved-conversation badge, and the gap between the two is worth knowing
  because the badge does not distinguish authors. Two cases above deliberately
  leave a thread open after the agent is done with it: the owner's own threads,
  which are the owner's to resolve, and — if `required_review_thread_resolution`
  is ever enabled — promotion pull request threads, which are left for a human by
  design. On a pull request carrying either, a nonzero badge is not evidence of
  unfinished agent work, and the question to ask is whether any *Copilot* thread
  is open. Where neither applies, which is the ordinary case, badge and signal
  agree. That is a real cost to reading this at a glance, and it is the price of
  the two exceptions rather than an oversight in them.
- That meaning is only as good as the reply discipline above. A session that
  resolves without replying has not saved the owner a read, it has hidden one.
- Suppressed findings get a written disposition they previously only got when
  someone happened to read the review bodies by hand. Whether the older ones were
  addressed is not recoverable from the API — there is no state field to check —
  so this starts from here rather than being backfilled.
- The closing-out cost scales with pushes rather than with pull requests, because
  each push draws a review. That is a real cost and it lands hardest on the long
  rounds, which are also the ones where the signal is worth the most.
