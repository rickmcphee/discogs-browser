# Derived version design

Date: 2026-08-10
Branch: `derived-version`

## Problem

`backend/version.py` holds a hand-maintained literal (`VERSION = "3.18"`), and
CLAUDE.md requires every PR that merges to `main` to bump it. That rule only
works when pull requests merge strictly one at a time. They don't, and on
2026-08-10 the failure mode showed up three times in a single afternoon:

- **#92 and #93 both claimed 3.15.** #93 was authored at 3.16 on the assumption
  #92 would merge first; a reviewer correctly pointed out that pre-allocating a
  number risks a silent gap in the history if the merge order flips, so it was
  moved to 3.15 — which then guaranteed a conflict instead.
- **#91 merged mid-flight and took 3.14**, invalidating #92's already-written
  bump and forcing a rebase.
- **#94 merged with no effective bump at all.** Its branch changed 3.14 → 3.15,
  but #92 had already taken 3.15, so `main` read 3.15 both before and after it.
  The repo's own versioning rule was violated silently, by a PR whose entire
  subject was hardening repo configuration.

Two rebases and three manual conflict resolutions, all on one line of one file
that no code branches on.

The root cause is not carelessness. **The number is assigned before merge, but
its correctness can only be known at merge.** Any scheme with that shape
collides under concurrency.

## What `VERSION` is actually for

Confirmed by grep across the repository, and this constrains the whole design:

| Consumer | Use |
|---|---|
| `backend/main.py:85`, `:95` | Two startup log lines |
| `.github/ISSUE_TEMPLATE/bug_report.yml:32` | Asks a reporter for the string |
| `.github/pull_request_template.md:23` | Checklist item requiring the bump |

That is the complete list. `VERSION` appears in no API response, no frontend
code, no cache key, no migration gate, and nothing compares two versions
programmatically. Its sole job is **identifying which build is running**, for
log correlation and bug triage.

A hand-maintained counter is a poor instrument for that job even setting
collisions aside: `3.16` does not say which tree is deployed, cannot be
verified, and can be — and on 2026-08-10 was — duplicated or skipped. A
commit-derived identifier is unforgeable and names an exact tree.

`amazon.py`'s `_VERSION = "v5-format-aware"` was a crawler-strategy marker,
unrelated to this despite the name, and was not touched by this design. It was
later removed as unused cleanup (PR #132), independent of this change.

## Scope

Replace the literal with a value derived from the commit, in the form:

```
2026.08.10+8fac644
```

Date of the commit, `+`, its short SHA. No PR ever writes this value, so no two
PRs can disagree about it — collisions become structurally impossible rather
than managed by convention.

**Non-goals**

- **No git tags.** Tagging was considered and rejected: a tag is an *assigned*
  identifier, and assignment under concurrency is the thing that collides. It
  would either be assigned pre-merge (same bug) or by CI post-merge (a new
  moving part, and a bot writing to `main`).
- **No `GET /api/version` endpoint.** See "Known gap" below.
- **No semantic-version components.** Nothing consumes major/minor; see
  "Consequences" for what this gives up.
- **No change to the deploy trigger, the Fly configuration, or the release
  process** beyond the build argument, threaded through both the Fly deploy
  workflow and the self-hosted `docker-compose`/`bootstrap.sh` path (see
  "Injection").

## Design

### Resolution order

`backend/version.py` stops being a constant and resolves `VERSION` at import,
in this order:

1. **`APP_VERSION` environment variable**, if set and non-empty. This is the
   deployed path — the value is baked into the image (see "Injection").
2. **Git**, otherwise: the commit date via
   `git log -1 --format=%cd --date=format:%Y.%m.%d` and the short SHA via
   `git rev-parse --short=7 HEAD`, joined with `+`. This is the local-development
   path; `backend/` sits inside the working tree, so git resolves the
   repository root on its own.
3. **`"dev"`**, if neither is available.

Any failure in step 2 — git not installed, not a repository, non-zero exit,
timeout — falls through to step 3 rather than propagating. `version.py` is
imported by `main.py` at module scope, so an exception here takes down app
startup; a wrong-but-honest `"dev"` is strictly better than a crash. The
subprocess calls get an explicit short timeout so a pathological git
invocation cannot hang boot.

Resolution happens once at import, matching the existing
`from version import VERSION` usage in `main.py`. In a deployed container step 1
always wins, so git is never invoked there — which is just as well, since the
image contains no `.git`.

### Injection

`backend/Dockerfile` gains, **as its last two instructions**:

```dockerfile
ARG APP_VERSION=dev
ENV APP_VERSION=$APP_VERSION
```

Placement at the end is load-bearing, not stylistic. An `ARG` invalidates the
build cache for every layer below it, and the layers above include
`pip install` and `playwright install chromium` — the expensive ones. Declaring
`APP_VERSION` above the `playwright install` layer would rebuild Chromium on
every deploy, since the value changes every time. At the end, a new version
dirties one trivial layer.

`.github/workflows/fly-deploy.yml`'s deploy job computes the string and passes
it through:

```yaml
- name: Deploy
  working-directory: backend
  run: |
    flyctl deploy --remote-only \
      --build-arg APP_VERSION="$(git log -1 --format=%cd --date=format:%Y.%m.%d)+$(git rev-parse --short=7 HEAD)"
```

Derived from the checked-out commit rather than from `github.sha` directly, so
one expression produces both halves and the local and CI paths compute the
value identically. The deploy job already runs `actions/checkout`, so the git
metadata is present.

A manual `flyctl deploy` from a developer's checkout, without the build
argument, produces `dev` via the Dockerfile's `ARG` default — visibly
unofficial, which is the right outcome for a hand-rolled deploy.

The self-hosted `docker-compose` path needs the same argument, since it
builds from the same Dockerfile: `docker-compose.yml`'s `backend` service
build stanza passes `APP_VERSION: ${APP_VERSION:-dev}`, and `bootstrap.sh` —
the routine update/redeploy script, run after `git pull` and before
`docker-compose build` — exports `APP_VERSION` computed the same way as the
Fly workflow. Without this, that build context has no `.git` and the image
has no git binary, so the self-hosted deployment would resolve to `"dev"` on
every build, silently defeating the feature there.

`bootstrap.sh` additionally appends `.dirty` when `git status --porcelain` is
non-empty, giving `2026.08.11+644b80b.dirty`. `git pull` succeeds with
non-conflicting local changes, there is no `.dockerignore`, and the
Dockerfile's `COPY . .` takes the whole build context — so without this a
modified or untracked file is baked into the image under a version string
naming a clean commit that does not describe it. `--porcelain` covers
untracked files and honours `.gitignore`, so `workspace/`, `postgres-data/`
and `.env` never trip it. A dirty tree is marked rather than rejected:
`bootstrap.sh` is the routine redeploy path, and an operator with a local edit
should still be able to deploy — just not mislabel the result. The Fly path
needs no equivalent, since `actions/checkout` is always clean.

The short SHA is taken with `--short=7`, not bare `--short`. The bare form
abbreviates to whatever is unique in the local clone (default: `core.abbrev`),
so the same commit can yield different lengths in a shallow CI checkout, a
full self-hosted clone, and a developer's checkout with `core.abbrev` set. An
explicit minimum makes every build path produce the same string for the same
commit; git still lengthens it beyond 7 if 7 is ambiguous.

## Consequences

**Accepted:** the `3.x` line ends at 3.18 and there is no successor numbering.
Nothing can be said to be "version 4.0". The repo owner explicitly accepted this
when choosing a commit-traceable identifier over an assigned release number.
Existing `3.x` references in older specs are historical records of what was true
when written and are not backfilled — consistent with how this repo already
treats superseded numbers in amended specs.

**Gained:** the version becomes verifiable. Given `2026.08.10+8fac644` from a log
line or a bug report, `git show 8fac644` names the exact tree, and the date
gives staleness at a glance without a lookup.

## Documentation changes

Four places assert the current scheme; all four change in this branch.

| File | Change |
|---|---|
| `CLAUDE.md` `## Versioning` | Replaced: `VERSION` is derived and never hand-edited, so a PR that modifies it is wrong by definition. The minor-bump rule and the "major bump only on the repo owner's explicit instruction" clause are both removed — a derived version has no components to bump. |
| `.github/pull_request_template.md:23` | Delete the `` `backend/version.py` `VERSION` minor-bumped `` checklist item. Added in #94 on 2026-08-10; left in place it would instruct every future PR to do what this change forbids. |
| `.github/ISSUE_TEMPLATE/bug_report.yml:32` | Rewrite the description: the string now looks like `2026.08.10+8fac644` and appears in the backend's startup log line, not in a source file. |
| `backend/version.py` | Becomes the resolver. |

## Known gap (not fixed here)

The version exists to serve bug reports, but a reporter cannot see it — it
reaches only a startup log line, with no API or UI surface. This is why
`bug_report.yml` already hedges with "or the commit SHA".

This predates the change and is not made worse by it, so it is recorded rather
than fixed: adding `GET /api/version` means a new API surface and a decision
about whether it sits inside or outside `AuthMiddleware`, which guards every
`/api` request. That is its own small piece of work.

## Testing

`backend/tests/test_version.py`. The module resolves at import, so each case
reloads it via `importlib.reload` under patched conditions:

- `APP_VERSION` set → that exact value is used, and git is never invoked
  (asserted by patching `subprocess` and checking it was not called — the
  deployed path must not depend on git being present).
- `APP_VERSION` unset, git available → value matches `YYYY.MM.DD+<sha>`.
- `APP_VERSION` empty string → treated as unset, falls through to git.
- `APP_VERSION` unset, git command returns non-zero → `"dev"`.
- `APP_VERSION` unset, git binary missing (`FileNotFoundError`) → `"dev"`.
- Neither available → `"dev"`, no exception raised. This case is explicitly
  tested because a raise here would break app startup, not just versioning.

No test asserts a specific version number, which is the point: there is no
longer a number for a test to encode and drift from.
