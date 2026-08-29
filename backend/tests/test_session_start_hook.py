"""Guards the SessionStart hook against executing pull request head code.

`anthropics/claude-code-action` restores `.claude/` from a pull request's base
branch and leaves the rest of the working tree at the PR head, and the CLI runs
SessionStart hooks before any tool-permission gating. So the hook must decide
whether to provision from inside `.claude/`, and must never hand off to
`scripts/` until it has. See
docs/specifications/shaping/2026-08-29-session-start-hook-pr-safety-design.md
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = REPO_ROOT / ".claude" / "hooks" / "session-start.sh"
SETTINGS = REPO_ROOT / ".claude" / "settings.json"

# A cloud session: the one context the launcher is supposed to hand off in.
TRUSTED_ENV = {"CLAUDE_CODE_REMOTE": "true"}


def _run(tmp_path, env, handoff_exit_code=0):
    """Run a copy of the launcher over a stand-in cloud-setup.sh.

    The stand-in records that it ran, so a test can tell "the launcher declined"
    from "the launcher handed off" without provisioning anything.
    """
    (tmp_path / ".claude" / "hooks").mkdir(parents=True)
    shutil.copy(LAUNCHER, tmp_path / ".claude" / "hooks" / "session-start.sh")

    (tmp_path / "scripts").mkdir()
    stand_in = tmp_path / "scripts" / "cloud-setup.sh"
    marker = tmp_path / "handed-off"
    stand_in.write_text(
        "#!/usr/bin/env bash\n"
        f"touch {marker}\n"
        f"exit {handoff_exit_code}\n"
    )
    stand_in.chmod(0o755)

    result = subprocess.run(
        ["bash", str(tmp_path / ".claude" / "hooks" / "session-start.sh")],
        capture_output=True,
        text=True,
        timeout=30,
        # Cleared rather than inherited: this suite's own run may itself be
        # inside GitHub Actions, or inside a cloud session, and either would
        # decide these tests for them.
        env={"PATH": os.environ["PATH"], **env},
    )
    return result, marker.exists()


def test_hands_off_in_a_cloud_session(tmp_path):
    result, handed_off = _run(tmp_path, TRUSTED_ENV)
    assert handed_off, result.stderr
    assert result.returncode == 0


def test_propagates_the_setup_scripts_exit_code(tmp_path):
    # The handoff is an `exec`, so a provisioning failure has to surface as a
    # failed hook rather than being swallowed into a green start.
    result, handed_off = _run(tmp_path, TRUSTED_ENV, handoff_exit_code=3)
    assert handed_off
    assert result.returncode == 3


def test_declines_outside_a_cloud_session(tmp_path):
    result, handed_off = _run(tmp_path, {})
    assert not handed_off
    assert result.returncode == 0


def test_declines_under_github_actions(tmp_path):
    # The backstop: reached only if a future claude-code-action starts setting
    # CLAUDE_CODE_REMOTE, which is why the env deliberately sets both.
    result, handed_off = _run(
        tmp_path, {**TRUSTED_ENV, "GITHUB_ACTIONS": "true"}
    )
    assert not handed_off
    assert result.returncode == 0


def test_declines_when_the_action_left_a_pr_snapshot(tmp_path):
    (tmp_path / ".claude-pr").mkdir()
    result, handed_off = _run(tmp_path, TRUSTED_ENV)
    assert not handed_off
    assert result.returncode == 0


def test_the_hook_command_stays_inside_the_restored_tree():
    # The regression this whole file exists for: pointing the hook back at
    # scripts/ would re-open the hole, and would do it silently.
    hooks = json.loads(SETTINGS.read_text())["hooks"]["SessionStart"]
    commands = [h["command"] for entry in hooks for h in entry["hooks"]]
    assert commands
    for command in commands:
        assert ".claude/hooks/" in command, command
        assert "scripts/" not in command, command


def test_the_launcher_is_tracked_by_git():
    # `.claude/*` is gitignored. The action restores base content with
    # `git checkout origin/<base> -- .claude`, which only reaches tracked files,
    # so an untracked launcher would be deleted on a PR and never restored --
    # leaving the hook pointing at nothing.
    try:
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", ".claude/hooks/session-start.sh"],
            cwd=REPO_ROOT,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        pytest.skip("no git repository available")
    assert tracked.returncode == 0, tracked.stderr.decode()
