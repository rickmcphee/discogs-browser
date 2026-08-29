#!/usr/bin/env bash
# SessionStart hook entry point: decides whether this working tree is trusted,
# and hands off to scripts/cloud-setup.sh only when it is.
#
# The hook in .claude/settings.json cannot name scripts/cloud-setup.sh directly.
# anthropics/claude-code-action restores a fixed list of Claude configuration
# paths from a pull request's *base* branch before starting Claude -- `.claude/`
# among them -- and leaves the rest of the working tree at the PR head. The CLI
# reads and acts on `.claude/settings.json` before any tool-permission gating,
# SessionStart hooks included. A base-branch hook that reaches into `scripts/`
# therefore executes whatever a contributor put there, with the workflow's write
# token and CLAUDE_CODE_OAUTH_TOKEN in its environment. This repository is
# public, so "a contributor" means anyone.
# https://github.com/anthropics/claude-code-action/blob/main/docs/security.md
#
# `.claude/` is on that restore list, so this file is base-controlled and the
# checks below cannot be edited from a PR head. Relocating cloud-setup.sh here
# too would not have been sufficient on its own: it runs `pip install -e` over
# backend/pyproject.toml and `npm ci` over frontend/package.json, both PR-head
# files that execute code at install time. Declining to provision is the fix;
# keeping that decision in a restored path is what stops a pull request from
# reverting it.
#
# See docs/specifications/shaping/2026-08-29-session-start-hook-pr-safety-design.md
#
# Takes no arguments. To provision by hand, run scripts/cloud-setup.sh --force.
set -e

# Positive gate. Provisioning is only ever wanted in a Claude Code cloud
# session, whose tree is whatever the operator chose to open -- the same trust
# as a local checkout. Everything else, local sessions included, is a no-op,
# which is how this behaved before the check moved up here from cloud-setup.sh.
# claude-code-action sets neither this variable nor CLAUDE_PROJECT_DIR.
[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0

# Backstop against that first check becoming true by accident -- a future
# claude-code-action release deciding to set CLAUDE_CODE_REMOTE, say. GitHub
# sets GITHUB_ACTIONS for every step on every runner, and nothing in a checkout
# can influence it.
[ -z "${GITHUB_ACTIONS:-}" ] || exit 0

# Third layer: the only one keyed to the restore itself rather than to the
# environment around it. Before overwriting the sensitive paths, the action
# snapshots the PR's own copies of them into `.claude-pr/`, so that directory
# existing means a PR head is checked out.
#
# Best-effort, and deliberately last. The action snapshots only the paths the
# head still has, so a pull request that deletes every one of them -- `.claude/`,
# `CLAUDE.md`, `.mcp.json` and the rest -- leaves nothing here to find. This
# catches a case the two checks above would both have to fail for; it is not a
# substitute for either. It cannot fail the other way: a pull request that
# creates the directory itself only makes this refuse.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
[ ! -e "$REPO_ROOT/.claude-pr" ] || exit 0

exec bash "$REPO_ROOT/scripts/cloud-setup.sh"
