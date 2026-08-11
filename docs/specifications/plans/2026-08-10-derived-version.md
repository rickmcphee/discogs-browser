# Derived Version Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hand-maintained `VERSION = "<number>"` literal with a string derived from the commit, so no pull request ever writes a version number and version collisions become structurally impossible.

**Architecture:** `backend/version.py` becomes a three-step resolver — `APP_VERSION` env var, then git, then `"dev"` — resolved once at import. CI bakes the real value into the Fly image via a Docker build argument declared as the Dockerfile's last instructions, so the version never invalidates the expensive Chromium layer.

**Tech Stack:** Python 3.9+ (`subprocess`, `os`), pytest with `asyncio_mode = "auto"`, Docker build args, GitHub Actions, `flyctl`.

**Spec:** [`docs/specifications/shaping/2026-08-10-derived-version-design.md`](../shaping/2026-08-10-derived-version-design.md)

## Global Constraints

- Python ≥3.9. No `str | None` union syntax — use `Optional[str]` or leave untyped.
- No comments unless the WHY is non-obvious.
- No backwards-compat shims. There is no deprecation path for the old literal — it is deleted outright.
- **`version.py` must never raise.** It is imported at module scope by `backend/main.py:7`, so an exception there breaks app startup. Every failure path resolves to `"dev"`.
- The resolved version format is exactly `YYYY.MM.DD+<short-sha>`, e.g. `2026.08.10+8fac644`.
- Run tests from `backend/`. These tests touch no database, so no Postgres environment variables are needed: `cd backend && pytest tests/test_version.py -v`
- Commit with `git commit -F <message-file>`, never `git commit -m`. Every commit's last paragraph must be the AI-attribution trailer block:
  ```
  Note: This commit message was created by AI
  ai-generated: true
  ai-model: <the actual model id of the session making the commit>
  ai-tool: claude-code
  ai-surface: claude-code-desktop
  ai-executor: local-agent
  ```
- **Do not bump any version number anywhere in this branch.** That is the entire point of the change. If you find yourself editing a version literal, stop — it means something was missed.
- **Out of scope, do not add:** a `GET /api/version` endpoint (a known gap, recorded in the spec, deliberately deferred); git tags of any kind; and any change to `backend/crawlers/amazon.py`'s `_VERSION = "v5-format-aware"`, which is a crawler-strategy marker unrelated to app versioning despite the name.

---

### Task 1: The version resolver

**Files:**
- Modify: `backend/version.py` (currently one line, a quoted literal such as `VERSION = "3.17"` — read the file for the value on your branch; another PR may have bumped it since this plan was written)
- Test: `backend/tests/test_version.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `version.VERSION` — a `str`, resolved at import. Already consumed by `backend/main.py:7` via `from version import VERSION`; that import must keep working unchanged.
  - `version._git_version()` — module-level function returning `Optional[str]`; `None` when git cannot answer. Tests call it only indirectly, through reloading the module.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_version.py`:

```python
import importlib
import subprocess
from unittest.mock import patch

import pytest

import version


@pytest.fixture(autouse=True)
def restore_version_module():
    # Every test reloads `version` under patched conditions; put the real
    # module back afterwards so later tests in the session see a sane value.
    yield
    importlib.reload(version)


def _completed(stdout):
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def test_app_version_env_wins_and_git_is_never_invoked():
    # The deployed container has no .git at all, so the env path must not
    # depend on git being present.
    with patch.dict("os.environ", {"APP_VERSION": "2026.08.10+abc1234"}), \
         patch("subprocess.run") as run:
        reloaded = importlib.reload(version)
    assert reloaded.VERSION == "2026.08.10+abc1234"
    run.assert_not_called()


def test_falls_back_to_git_when_env_is_unset():
    with patch.dict("os.environ", {}, clear=True), \
         patch("subprocess.run", side_effect=[_completed("2026.08.10\n"),
                                              _completed("8fac644\n")]):
        reloaded = importlib.reload(version)
    assert reloaded.VERSION == "2026.08.10+8fac644"


def test_empty_app_version_is_treated_as_unset():
    with patch.dict("os.environ", {"APP_VERSION": ""}), \
         patch("subprocess.run", side_effect=[_completed("2026.08.10\n"),
                                              _completed("8fac644\n")]):
        reloaded = importlib.reload(version)
    assert reloaded.VERSION == "2026.08.10+8fac644"


def test_git_failure_falls_back_to_dev():
    with patch.dict("os.environ", {}, clear=True), \
         patch("subprocess.run", side_effect=subprocess.CalledProcessError(128, "git")):
        reloaded = importlib.reload(version)
    assert reloaded.VERSION == "dev"


def test_missing_git_binary_falls_back_to_dev():
    with patch.dict("os.environ", {}, clear=True), \
         patch("subprocess.run", side_effect=FileNotFoundError):
        reloaded = importlib.reload(version)
    assert reloaded.VERSION == "dev"


def test_git_timeout_falls_back_to_dev():
    with patch.dict("os.environ", {}, clear=True), \
         patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 5)):
        reloaded = importlib.reload(version)
    assert reloaded.VERSION == "dev"


def test_blank_git_output_falls_back_to_dev():
    with patch.dict("os.environ", {}, clear=True), \
         patch("subprocess.run", side_effect=[_completed("\n"), _completed("\n")]):
        reloaded = importlib.reload(version)
    assert reloaded.VERSION == "dev"


def test_import_never_raises_and_yields_a_non_empty_string():
    # Guards the real reason every failure path returns "dev": main.py imports
    # this at module scope, so a raise here breaks app startup.
    reloaded = importlib.reload(version)
    assert isinstance(reloaded.VERSION, str)
    assert reloaded.VERSION
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_version.py -v`
Expected: FAIL. `test_app_version_env_wins_and_git_is_never_invoked` fails with an `AssertionError` comparing the current literal (e.g. `'3.17'`) against `'2026.08.10+abc1234'` — the module is still a literal and ignores the environment. Several others fail the same way. `test_import_never_raises_and_yields_a_non_empty_string` already passes, which is expected and fine.

- [ ] **Step 3: Write the implementation**

Replace the entire contents of `backend/version.py`:

```python
import os
import subprocess

_DATE_CMD = ["git", "log", "-1", "--format=%cd", "--date=format:%Y.%m.%d"]
_SHA_CMD = ["git", "rev-parse", "--short", "HEAD"]


def _git_version():
    """Commit date + short SHA, or None when git can't answer.

    Only reached in local development: the deployed image carries no .git and
    always has APP_VERSION baked in. The timeout is a boot-safety measure, not
    a performance one -- this runs at import, so a hung git would hang startup.
    """
    try:
        date = subprocess.run(_DATE_CMD, capture_output=True, text=True,
                              timeout=5, check=True).stdout.strip()
        sha = subprocess.run(_SHA_CMD, capture_output=True, text=True,
                             timeout=5, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if not date or not sha:
        return None
    return f"{date}+{sha}"


# main.py imports this at module scope, so a raise here would break app
# startup -- every failure path lands on "dev" instead.
VERSION = os.environ.get("APP_VERSION") or _git_version() or "dev"
```

`(OSError, subprocess.SubprocessError)` is the complete failure set: `FileNotFoundError` (no git binary) is an `OSError`, and both `CalledProcessError` (non-zero exit) and `TimeoutExpired` are `SubprocessError` subclasses.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_version.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 5: Verify the real value resolves in this checkout**

Run: `cd backend && python -c "from version import VERSION; print(VERSION)"`
Expected: a string like `2026.08.10+640e189` — today's-or-earlier date, `+`, the short SHA of `HEAD`. Not `dev`, and not the old numeric literal. Paste the actual output into your report.

- [ ] **Step 6: Confirm the app still starts**

Run: `cd backend && python -c "import main" 2>&1 | tail -5`
Expected: no traceback. This exercises `main.py:7`'s `from version import VERSION` against the rewritten module.

- [ ] **Step 7: Commit**

```bash
cat > /tmp/dv-task1-msg.txt <<'EOF'
feat: derive VERSION from the commit instead of a literal

Resolves APP_VERSION, then git, then "dev". main.py imports this at
module scope, so every failure path returns "dev" rather than raising --
a version lookup must not be able to break app startup.

Note: This commit message was created by AI
ai-generated: true
ai-model: <your actual model id>
ai-tool: claude-code
ai-surface: claude-code-desktop
ai-executor: local-agent
EOF
git add backend/version.py backend/tests/test_version.py && git commit -F /tmp/dv-task1-msg.txt
```

---

### Task 2: Bake the version into the deployed image

Task 1 makes the deployed container resolve `"dev"`, because the image has no `.git` and nothing sets `APP_VERSION` yet. This task supplies the real value.

**Files:**
- Modify: `backend/Dockerfile` (append two instructions at the end)
- Modify: `.github/workflows/fly-deploy.yml` (the `Deploy` step in the `deploy` job)

**Interfaces:**
- Consumes: `version.VERSION`'s `APP_VERSION` environment lookup from Task 1.
- Produces: no Python interface. The contract is that a container built by CI has `APP_VERSION` set to `YYYY.MM.DD+<short-sha>` in its environment.

- [ ] **Step 1: Append the build argument to the Dockerfile**

`backend/Dockerfile` currently ends with:

```dockerfile
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Add these two lines **after** the `CMD` line, as the final instructions in the file:

```dockerfile

ARG APP_VERSION=dev
ENV APP_VERSION=$APP_VERSION
```

**Placement is load-bearing, not stylistic.** An `ARG` invalidates the build cache for every layer below it. The layers above include `pip install` and `playwright install chromium` — several minutes each. Declaring `APP_VERSION` any earlier would rebuild Chromium on every single deploy, because the value changes every deploy. At the very end, a new version dirties one trivial layer.

The `=dev` default means a hand-run `flyctl deploy` without the build argument produces a visibly unofficial version rather than a misleading one.

- [ ] **Step 2: Pass the value from the deploy workflow**

In `.github/workflows/fly-deploy.yml`, the `deploy` job's `Deploy` step currently reads:

```yaml
      - name: Deploy
        working-directory: backend
        run: flyctl deploy --remote-only
        env:
          FLY_API_TOKEN: ${{ secrets.FLY_API_TOKEN }}
```

Replace it with:

```yaml
      - name: Deploy
        working-directory: backend
        run: |
          flyctl deploy --remote-only \
            --build-arg APP_VERSION="$(git log -1 --format=%cd --date=format:%Y.%m.%d)+$(git rev-parse --short HEAD)"
        env:
          FLY_API_TOKEN: ${{ secrets.FLY_API_TOKEN }}
```

Derived from the checked-out commit rather than from `${{ github.sha }}`, so one expression yields both halves and CI computes the value exactly the way `version.py`'s local fallback does. The job already runs `actions/checkout`, and its default shallow depth of 1 is sufficient — both commands read only `HEAD`.

Do not change the `uses:` lines. Those actions are pinned to full commit SHAs because the repository's Actions policy rejects tag references; a tag ref there fails the whole workflow at startup.

- [ ] **Step 3: Verify the shell expression produces the right format**

Run: `echo "$(git log -1 --format=%cd --date=format:%Y.%m.%d)+$(git rev-parse --short HEAD)"`
Expected: a string matching `YYYY.MM.DD+<short-sha>`, e.g. `2026.08.10+640e189`. Paste the actual output into your report.

- [ ] **Step 4: Verify the Dockerfile instruction order**

Run: `tail -4 backend/Dockerfile`
Expected: the `CMD` line, then `ARG APP_VERSION=dev`, then `ENV APP_VERSION=$APP_VERSION`. Confirm no `ARG` appears anywhere above the `playwright install` line:

Run: `grep -n "^ARG\|playwright install" backend/Dockerfile`
Expected: the `playwright install` line number is lower than the `ARG` line number.

- [ ] **Step 5: Verify the workflow YAML still parses**

Run: `cd backend && python -c "import yaml,sys; d=yaml.safe_load(open('../.github/workflows/fly-deploy.yml')); print(d['jobs']['deploy']['steps'][-1]['run'])"`
Expected: prints the multi-line `flyctl deploy` command including `--build-arg APP_VERSION=`. If `yaml` is not installed, run `pip install pyyaml` first — it is a dev-only check, so do not add it to `pyproject.toml`.

Note: a full `docker build` is deliberately not part of this plan. It rebuilds Chromium and takes several minutes, and this repo's convention is that the deploy path is manually integration-tested. The real verification is the startup log line on the next deploy to `main`.

- [ ] **Step 6: Commit**

```bash
cat > /tmp/dv-task2-msg.txt <<'EOF'
build: bake the derived version into the deployed image

ARG/ENV are the Dockerfile's last instructions on purpose: an ARG
invalidates every layer below it, and the layers above include
playwright install chromium. Declared earlier, a value that changes
every deploy would rebuild Chromium every deploy.

Note: This commit message was created by AI
ai-generated: true
ai-model: <your actual model id>
ai-tool: claude-code
ai-surface: claude-code-desktop
ai-executor: local-agent
EOF
git add backend/Dockerfile .github/workflows/fly-deploy.yml && git commit -F /tmp/dv-task2-msg.txt
```

---

### Task 3: Retire the manual-bump convention

Four documents still instruct humans and agents to hand-edit a version number. Left in place they would keep producing exactly the collisions this branch removes.

**Files:**
- Modify: `CLAUDE.md` (the `## Versioning` section)
- Modify: `.github/pull_request_template.md:23`
- Modify: `.github/ISSUE_TEMPLATE/bug_report.yml:32`

**Interfaces:**
- Consumes: the version format from Task 1.
- Produces: no code interface.

- [ ] **Step 1: Replace CLAUDE.md's Versioning section**

The section currently reads:

```markdown
## Versioning

`backend/version.py`'s `VERSION` string is bumped as part of every PR that merges to `main` — not a separate follow-up commit, and not something that needs to be asked for each time:

- **Minor bump is the default, automatic action.** Increment the second number (`1.48` → `1.49`) on every PR merge, regardless of how small the change is.
- **Major bump (reset to `X.0`) only happens on the repo owner's explicit instruction.** Never take a major bump on your own judgment, no matter how large the change looks.
```

Replace those lines — the `## Versioning` heading through the second bullet — with:

```markdown
## Versioning

`backend/version.py`'s `VERSION` is **derived, never edited**. A PR that changes a version number is wrong by definition; there is nothing to bump.

The value is `YYYY.MM.DD+<short-sha>` (e.g. `2026.08.10+8fac644`), resolved at import from, in order: the `APP_VERSION` environment variable, then git, then `"dev"`. CI bakes the real value into the Fly image as a Docker build argument.

The old scheme required each PR to bump a shared literal before merge, but whether the number was right could only be known at merge — so concurrent PRs collided routinely (see `docs/specifications/shaping/2026-08-10-derived-version-design.md`). There are no major/minor components any more, and no `3.x` successor.
```

- [ ] **Step 2: Delete the PR-template checklist item**

`.github/pull_request_template.md` currently has:

```markdown
## Checklist

- [ ] `backend/version.py` `VERSION` minor-bumped
- [ ] Spec drift checked against both spec trees
- [ ] Commits carry the AI-attribution trailers
```

Delete only the `VERSION` line, leaving:

```markdown
## Checklist

- [ ] Spec drift checked against both spec trees
- [ ] Commits carry the AI-attribution trailers
```

- [ ] **Step 3: Update the bug-report template**

`.github/ISSUE_TEMPLATE/bug_report.yml` currently has:

```yaml
      description: The `VERSION` string from `backend/version.py`, or the commit SHA.
```

Replace that line with:

```yaml
      description: The version string from the backend's startup log, e.g. `2026.08.10+8fac644`. A bare commit SHA works too.
```

Keep the surrounding `- type: input` / `id: version` / `label: Version` / `validations` lines exactly as they are.

- [ ] **Step 4: Verify no instruction to bump a version survives**

Run: `grep -rn "minor-bump\|minor bump\|VERSION.*bump\|bump.*VERSION" CLAUDE.md .github/ docs/specifications/shaping/2026-08-10-derived-version-design.md`
Expected: the only matches are inside the new `## Versioning` text and the design spec, both of which describe the retired scheme in the past tense. No line instructs anyone to perform a bump.

Run: `grep -rn "3\.16" CLAUDE.md .github/ backend/`
Expected: no matches. The old literal is gone from every instruction and from the code.

- [ ] **Step 5: Run the full backend suite**

```bash
cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest
```

Expected: PASS. Report the real numbers; do not claim success without the output. If Postgres is unreachable, say so plainly and report the no-database run separately rather than faking it.

- [ ] **Step 6: Run the pre-PR spec-drift check**

Required on every branch. Grep both spec trees for what this diff touched:

```bash
grep -rln "version\.py\|VERSION\|APP_VERSION" docs/superpowers/specs/ docs/specifications/shaping/
```

For each match, confirm the text still describes what ships. Specs that record a historical version number (e.g. "VERSION goes 3.13 → 3.15") are historical statements about a past change and stay as written; a spec that states the *current rule* for versioning has drifted and must be amended. Report which files you checked and your verdict for each.

- [ ] **Step 7: Commit**

```bash
cat > /tmp/dv-task3-msg.txt <<'EOF'
docs: retire the manual version-bump convention

CLAUDE.md, the PR template and the bug-report template all still told
humans and agents to hand-edit a version number, which is the practice
this branch removes.

Note: This commit message was created by AI
ai-generated: true
ai-model: <your actual model id>
ai-tool: claude-code
ai-surface: claude-code-desktop
ai-executor: local-agent
EOF
git add CLAUDE.md .github/pull_request_template.md .github/ISSUE_TEMPLATE/bug_report.yml && git commit -F /tmp/dv-task3-msg.txt
```

---

## Manual verification after merge

The deployed value cannot be proven before merge, because it is produced by the deploy job that only runs on push to `main`. After this merges, check the backend's startup log for:

```
Discogs Browser backend v2026.08.10+<sha> starting
```

A bare numeric version there (e.g. `v3.17`) means the old image is still running; a `vdev` means the build argument did not reach the image, and the first thing to check is whether the `--build-arg` survived the `flyctl deploy` invocation.
