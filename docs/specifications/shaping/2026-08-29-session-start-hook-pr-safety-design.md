# Keeping the SessionStart hook off pull request heads

Status: implemented in `.claude/hooks/session-start.sh`, `.claude/settings.json`,
`.gitignore`, and `scripts/cloud-setup.sh`.

## Problem

`.claude/settings.json` registers a `SessionStart` hook that ran
`scripts/cloud-setup.sh`. That is correct everywhere Claude Code has run so far,
and wrong the moment Claude Code runs against a pull request head.

`anthropics/claude-code-action` restores a fixed list of Claude configuration
paths from a pull request's **base** branch before starting Claude —
`.claude/`, `.mcp.json`, `.claude.json`, `.gitmodules`, `.ripgreprc`,
`CLAUDE.md`, `CLAUDE.local.md`, `.husky/` — and leaves the rest of the working
tree at the PR head. It does this because the CLI's non-interactive mode trusts
its working directory: it reads `.claude/settings.json` and acts on it *before*
any tool-permission gating, executing hooks, `SessionStart` included.

So the hook's *configuration* was trusted and its *target* was not. This
repository is public, so anyone can open a pull request from a fork. A
contributor rewrites `scripts/cloud-setup.sh`, a maintainer types `@claude` on
the pull request, and the contributor's shell runs with the workflow's write
token and `CLAUDE_CODE_OAUTH_TOKEN` in its environment, before Claude has read a
line of the prompt.

Nothing was exploitable when this was written: `.github/workflows/claude.yml`
subscribes to issue events only, and its `issue_comment` clause excludes
anything carrying `pull_request`. That narrowing was the mitigation, adopted
during review of the pull request that added the workflow, and the hook is the
reason it could not be lifted. This design removes that reason.

The action's own security documentation names this exact case: a base-branch
hook that runs "a repo-relative script" resolves through files the pull request
supplies, and its advice is to keep such commands self-contained.
See <https://github.com/anthropics/claude-code-action/blob/main/docs/security.md>.

## Goals

- No code from a pull request head can execute via the `SessionStart` hook.
- The decision that stops it lives somewhere a pull request cannot edit.
- `scripts/cloud-setup.sh` keeps provisioning Claude Code cloud sessions
  unchanged — Postgres, the test database, `backend/.env`, and the backend,
  Playwright and frontend dependencies.
- Local sessions keep no-opping, exactly as before.

## Non-goals

- Re-enabling the pull-request triggers in `.github/workflows/claude.yml`.
  Closing this hole is a precondition for that, not the same decision: running
  Claude with `contents: write` against a fork head has a wider surface than one
  hook, and it deserves its own review.
- Hardening a Claude Code cloud session opened directly on an untrusted branch.
  There the operator chose the branch, no workflow secret is injected, and
  running the suite at all executes that branch's `conftest.py`. It is the same
  trust as a local checkout, and outside what a hook can fix.

## Why relocating the script is not the fix

`.claude/` is on the restore list, so the obvious move — put the provisioning
script under `.claude/` and let it be restored from base too — is worth stating
and rejecting, because it looks sufficient and is not.

`scripts/cloud-setup.sh` runs `pip install -e ".[dev]"` against
`backend/pyproject.toml` and `npm ci` against `frontend/package.json`. Both files
stay at the PR head, and both execute arbitrary code at install time — a build
backend for the first, lifecycle scripts for the second. A base-branch copy of
the script reaches the same code by a slightly longer route.

Provisioning a pull request head is therefore not something to do carefully; it
is something not to do. **Declining to provision is the fix. Keeping that
decision in a restored path is what stops a pull request from reverting it.**

## The gate

`.claude/settings.json` now names `.claude/hooks/session-start.sh`, a launcher
that lives inside the restored tree and hands off to `scripts/cloud-setup.sh`
only when it can establish the working tree is trusted. Layered, outermost
first:

1. **`CLAUDE_CODE_REMOTE = "true"`** — the positive gate, and the one doing the
   real work. Provisioning is only ever wanted in a Claude Code cloud session.
   `claude-code-action` sets neither this variable nor `CLAUDE_PROJECT_DIR`
   (confirmed against the action's source), so this fails closed there. Every
   other context, local sessions included, is a no-op — which is how the hook
   already behaved, via the identical check inside `cloud-setup.sh`.
2. **`GITHUB_ACTIONS` unset** — a backstop against the first check becoming true
   by accident, say a future action release deciding to set
   `CLAUDE_CODE_REMOTE`. GitHub sets `GITHUB_ACTIONS` for every step on every
   runner and nothing in a checkout can influence it.
3. **No `.claude-pr/` directory** — the only check keyed to the restore itself
   rather than to the environment around it. Before overwriting the sensitive
   paths, the action snapshots the pull request's own copies of them into
   `.claude-pr/`; the directory existing means a PR head is checked out.
   Best-effort, and deliberately last: the action snapshots only the paths the
   head still has, so a pull request that deletes every one of them leaves
   nothing to find. It cannot fail the other way — a pull request that creates
   the directory only makes the launcher refuse — but it is not a substitute for
   either check above.

The first two are each sufficient on their own against `claude-code-action` as
it stands. The third is not, and is there for the case where both of those have
failed. They are layered because they fail in different directions: the first is
an absence (a variable not being set), the second an invariant of the runner,
the third an artefact of the action's implementation that a pull request can
suppress and a future release could rename. None of them can be reached from a
PR head, because all of them are inside `.claude/`.

`cloud-setup.sh` keeps its own `CLAUDE_CODE_REMOTE` check and `--force` flag.
That check is now a convenience for hand invocation, not a security boundary —
`scripts/` is not restored, so a pull request can delete it. The launcher's
header says so, to stop a later reader treating the two as one mechanism and
deleting the "duplicate."

## Tracking the launcher

`.gitignore` ignores `.claude/*` with a single exception for `settings.json`.
The launcher has to be tracked, because the action restores base content with
`git checkout origin/<base> -- .claude`, which only reaches tracked files. Git
does not descend into an ignored directory, so un-ignoring the file alone would
not have worked; the directory is un-ignored first, then its contents re-ignored,
then the one file excepted. Anything else dropped into `.claude/hooks/` stays
ignored.

## Consequences

- The hook now executes only base-controlled code. `.github/workflows/claude.yml`
  can subscribe to pull request events without that particular hole, once
  someone decides the rest of the surface is acceptable. Its comment above `on:`
  is rewritten to say so: it no longer names a precondition to wait for, because
  what is left is a standing judgement about `contents: write` and a fork's
  prompt-injectable code, not a defect.
- `.claude/settings.json` is no longer the only tracked file under `.claude/`.
- A cloud session is one process hop longer to start. Nothing else changes: the
  provisioning script, its output, and its `--force` behaviour are untouched.
